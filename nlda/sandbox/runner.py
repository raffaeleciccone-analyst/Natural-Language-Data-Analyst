"""
Esecuzione del codice validato e trasporto del risultato.

Tre responsabilita' che stanno insieme perche' descrivono un unico percorso:
eseguire il codice, isolarlo in un sottoprocesso, e riportarne indietro l'esito.

Nota sull'asimmetria dei formati, che e' deliberata: verso il worker si usa
pickle (la sorgente e' il genitore, fidata, e preserva i dtype del DataFrame);
al ritorno solo JSON, perche' quel processo ha appena eseguito codice generato da
un modello e va trattato come potenzialmente compromesso.
"""
import ast
import io
import json
import pickle  # nosec B403
import subprocess  # nosec B404
from typing import cast

import pandas as pd

# px e go finiscono nel contesto di esecuzione del codice generato: il prompt
# promette al modello che Plotly Express sia gia' disponibile come 'px'.
from nlda.charts import apply_theme, go, is_plotly_figure, px, to_chart, try_chart
from nlda.config import settings
from nlda.log import get_logger
from nlda.results import (
    FAILURE_KINDS,
    ExecutionFailure,
    ExecutionResult,
    ExecutionSuccess,
    FailureKind,
)
from nlda.sandbox.pool import riserva
from nlda.sandbox.validator import (
    SAFE_BUILTINS,
    _last_assigned_name,
    _parse_and_validate,
)
from nlda.utils import clean_code

log = get_logger(__name__)

# Esecuzione isolata in un sottoprocesso con timeout (chiude i DoS da loop e
# allocazioni, e aggiunge una barriera di processo). I parametri vivono in
# nlda.config; qui restano alias comodi per leggibilita' e per i test.
SANDBOX_SUBPROCESS = settings.sandbox_subprocess
EXEC_TIMEOUT = settings.exec_timeout  # secondi

def _fig_summary(fig, max_rows: int = 30) -> str:
    lines = []
    for trace in fig.data:
        name = getattr(trace, "name", None) or "serie"
        x_attr = getattr(trace, "x", None)
        y_attr = getattr(trace, "y", None)
        xs = list(x_attr) if x_attr is not None else []
        ys = list(y_attr) if y_attr is not None else []
        pairs = ", ".join(f"{x}={y}" for x, y in list(zip(xs, ys, strict=False))[:max_rows])
        lines.append(f"{name}: {pairs}")
    return "Dati del grafico -> " + " | ".join(lines)


def _obj_summary(obj, max_rows: int = 30) -> str:
    if isinstance(obj, (pd.DataFrame, pd.Series)):
        return obj.head(max_rows).to_string()
    return str(obj)


def _make_summary(fig, value, max_rows: int = 30) -> str:
    """Riepilogo testuale del risultato per l'LLM: preferisce i dati alla figura."""
    if value is not None and not (isinstance(value, str) and value == "Codice eseguito correttamente."):
        return _obj_summary(value, max_rows)
    if fig is not None:
        return _fig_summary(fig, max_rows)
    return "Nessun risultato."


def summarize_result(result, max_rows: int = 30) -> str:
    """
    Testo di riepilogo di un risultato per l'LLM. Il flusso reale passa sempre un
    ExecutionSuccess (il riepilogo è già calcolato nel worker); resta un ripiego
    per oggetti grezzi (Series/DataFrame/scalari) e per il vecchio dict.
    """
    if isinstance(result, ExecutionSuccess):
        return result.summary or _make_summary(result.fig, result.value, max_rows)
    if isinstance(result, dict):
        return result.get("summary") or _make_summary(result.get("fig"), result.get("value"), max_rows)
    return _obj_summary(result, max_rows)
def _run_code(code: str, df: pd.DataFrame) -> ExecutionResult:
    """
    Valida ed esegue il codice. È il cuore eseguito sia in-process sia nel
    sottoprocesso: ritorna sempre un esito tipizzato, mai una stringa d'errore.
    """
    parsed = _parse_and_validate(code)
    if isinstance(parsed, ExecutionFailure):
        return parsed
    tree = parsed

    # Contesto isolato per l'esecuzione (builtin ridotti al minimo; niente 'st')
    safe_globals = {"__builtins__": SAFE_BUILTINS}
    local_context = {"df": df, "pd": pd, "px": px, "go": go,
                     "to_chart": to_chart, "try_chart": try_chart}

    try:
        fig = None
        value = None

        # Caso 1: singola espressione pura (es. df['Sales'].sum() o px.bar(...))
        if len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr):
            # È il punto in cui la sandbox esegue davvero: 'code' ha già superato
            # l'allowlist AST e gira con builtin ridotti, dentro un sottoprocesso
            # con timeout. ast.literal_eval non è un'alternativa: servono chiamate
            # Pandas, non letterali.
            v = eval(code, safe_globals, local_context)  # nosec B307
            if is_plotly_figure(v):
                fig = apply_theme(v)
            else:
                value = v
        else:
            # Caso 2: statement -> exec. Può produrre CONTEMPORANEAMENTE un grafico
            # ('fig') e dei dati ('risultato').
            # Stesse garanzie dell'eval qui sopra: allowlist AST, builtin ridotti,
            # sottoprocesso con timeout.
            exec(code, safe_globals, local_context)  # nosec B102
            f = local_context.get("fig")
            if is_plotly_figure(f):
                fig = apply_theme(f)
            # Il prompt insegna 'result'; 'risultato' resta accettato perché
            # è il nome che il modello ha imparato in passato e che continua a
            # comparire: rifiutarlo trasformerebbe una risposta valida in un errore.
            for name in ("result", "risultato"):
                if local_context.get(name) is not None:
                    value = local_context[name]
                    break
            if value is None:
                last_name = _last_assigned_name(tree)
                if last_name and last_name in local_context \
                        and not is_plotly_figure(local_context[last_name]):
                    value = local_context[last_name]

        if fig is None and value is None:
            value = "Codice eseguito correttamente."

        # Il riepilogo testuale è calcolato QUI (figura reale) e viaggia col risultato.
        return ExecutionSuccess(fig=fig, value=value, summary=_make_summary(fig, value))

    except Exception as e:
        return ExecutionFailure("runtime", f"Errore di esecuzione sul codice generato: {e}", code)
# --- Canale di ritorno dal worker: SOLO dati, mai oggetti ----------------------
# Il worker è il processo in cui gira il codice generato dall'LLM: va trattato come
# potenzialmente compromesso. Deserializzare un pickle proveniente da lì darebbe
# esecuzione di codice arbitrario NEL PROCESSO PADRE (pickle costruisce oggetti, non
# li legge soltanto), vanificando proprio la barriera di processo che la sandbox
# vuole erigere: i due livelli condividerebbero lo stesso destino.
# Per questo il canale worker -> padre trasporta esclusivamente JSON. La direzione
# opposta (padre -> worker) resta su pickle: lì la sorgente è fidata ed è l'unico
# modo di trasferire un DataFrame conservandone i dtype.

def _encode_value(value) -> dict:
    """Riduce il valore prodotto dal codice generato a una struttura JSON-compatibile."""
    if value is None:
        return {"kind": "none"}
    if isinstance(value, pd.DataFrame):
        return {"kind": "frame", "data": value.to_json(orient="split", date_format="iso")}
    if isinstance(value, pd.Series):
        name = value.name if isinstance(value.name, str) else None
        return {"kind": "series", "name": name,
                "data": value.to_json(orient="split", date_format="iso")}
    # Scalari numpy/pandas (np.int64, np.float64, ...): .item() li riporta al tipo
    # Python nativo. Va tentato PRIMA dell'isinstance: np.float64 eredita da float,
    # quindi passerebbe il controllo restando un oggetto numpy, e il payload
    # dipenderebbe dalla tolleranza di json verso le sottoclassi invece che dal
    # nostro contratto. I tipi nativi non hanno .item(), quindi non sono toccati.
    item = getattr(value, "item", None)
    if callable(item):
        try:
            value = item()
        except Exception:  # noqa: BLE001 — array non scalari e simili
            # La degradazione a testo è il comportamento voluto, non un errore.
            pass  # nosec B110
    if isinstance(value, (bool, int, float, str)):
        return {"kind": "scalar", "data": value}
    # Tutto il resto degrada a testo: meglio perdere il tipo che riaprire un canale
    # capace di istanziare oggetti arbitrari nel processo padre.
    return {"kind": "text", "data": str(value)}


def _decode_value(payload):
    """Ricostruisce il valore dal payload JSON. Non istanzia mai tipi arbitrari."""
    if not isinstance(payload, dict):
        return None
    kind = payload.get("kind")
    try:
        if kind == "frame":
            return pd.read_json(io.StringIO(payload["data"]), orient="split")
        if kind == "series":
            s = pd.read_json(io.StringIO(payload["data"]), orient="split", typ="series")
            return s.rename(payload.get("name")) if payload.get("name") else s
    except Exception as e:  # noqa: BLE001 — payload malformato: si degrada, non si rompe
        log.warning("Payload tabellare non decodificabile dal sottoprocesso: %s", e)
        return None
    if kind in ("scalar", "text"):
        return payload.get("data")
    return None


def serialize_result(result: ExecutionResult) -> dict:
    """
    Prepara l'esito per il trasferimento dal sottoprocesso, in forma JSON.
    Anche il fallimento viaggia strutturato: la CAUSA ('kind') deve sopravvivere
    al viaggio, altrimenti il padre dovrebbe reinferirla dal testo del messaggio.
    """
    if isinstance(result, ExecutionFailure):
        return {"kind": "err",
                "failure": {"kind": result.kind, "message": result.message, "code": result.code}}
    fig = result.fig
    return {"kind": "ok",
            "fig": fig.to_json() if fig is not None else None,
            "value": _encode_value(result.value),
            "summary": result.summary}


def _deserialize_failure(payload: dict) -> ExecutionFailure:
    """Ricostruisce il fallimento dal payload, senza fidarsi di ciò che contiene."""
    fail = payload.get("failure")
    if not isinstance(fail, dict):
        return ExecutionFailure("internal", "Errore sconosciuto dall'ambiente di esecuzione isolato.")
    raw = fail.get("kind")
    # Un 'kind' fuori dai valori previsti non deve diventare una causa arbitraria:
    # il worker è non fidato, quindi si degrada su 'internal'.
    kind: FailureKind = cast(FailureKind, raw) if raw in FAILURE_KINDS else "internal"
    return ExecutionFailure(kind, str(fail.get("message") or "Errore sconosciuto."),
                            str(fail.get("code") or ""))


def _deserialize_result(payload) -> ExecutionResult:
    if not isinstance(payload, dict):
        return ExecutionFailure("internal", "Errore: risultato non valido dal sottoprocesso.")
    if payload.get("kind") == "err":
        return _deserialize_failure(payload)
    fig = None
    if payload.get("fig"):
        import plotly.io as pio
        fig = apply_theme(pio.from_json(payload["fig"]))
    return ExecutionSuccess(fig=fig, value=_decode_value(payload.get("value")),
                            summary=str(payload.get("summary") or ""))


def _run_in_subprocess(code: str, df: pd.DataFrame, timeout: int) -> ExecutionResult:
    """Esegue il codice in un interprete separato con timeout. Solleva se non avviabile."""
    # Andata (padre -> worker): pickle, sorgente fidata e dtype del DataFrame preservati.
    payload = pickle.dumps((code, df))
    # La riserva calda consegna un worker che ha GIA' importato pandas e plotly:
    # ~840 ms di costo fisso pagati in background invece che nell'attesa
    # dell'utente. Resta comunque un processo fresco, che non ha mai eseguito
    # nulla prima — l'isolamento non e' barattato con la velocita'.
    returncode, stdout, stderr = riserva.esegui(payload, timeout)
    if returncode != 0 or not stdout:
        err = (stderr or b"").decode(errors="replace").strip()[-300:]
        # Il worker è morto: la causa dipende da CHI l'ha ucciso, e da questo
        # discende se ritentare abbia senso.
        #   - abbattuto DAL codice generato (segnale/OOM, MemoryError) -> 'runtime':
        #     rigenerarlo più leggero è un tentativo plausibile;
        #   - non partito affatto (import fallito, PYTHONPATH rotto, dipendenza
        #     mancante nell'ambiente del sottoprocesso) -> 'internal': è rotto il
        #     deploy, non il codice. Ritentare brucerebbe tre chiamate all'LLM per
        #     ogni domanda, senza alcuna possibilità di successo.
        # In assenza di prove si sceglie 'internal': l'errore silenzioso costoso è
        # il deploy rotto che continua a pagare token, non l'OOM che non ritenta.
        killed_by_code = returncode < 0 or "MemoryError" in err
        if killed_by_code:
            return ExecutionFailure("runtime", "Errore: esecuzione terminata in modo anomalo "
                                    "(possibile esaurimento memoria). Prova una domanda "
                                    "che aggreghi meno dati.", code)
        log.error("Worker terminato senza risultato (returncode=%s): %s", returncode, err)
        return ExecutionFailure(
            "internal", "Errore: l'ambiente di esecuzione isolato non ha prodotto un "
            "risultato. Se il problema persiste, l'installazione potrebbe essere incompleta.",
            code)
    # Ritorno (worker -> padre): JSON. Un payload malformato produce un errore
    # leggibile, MAI l'esecuzione di codice nel padre.
    try:
        data = json.loads(stdout.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as e:
        log.error("Payload non valido dal sottoprocesso: %s", e)
        return ExecutionFailure(
            "internal", "Errore: risultato non leggibile dall'ambiente di esecuzione isolato.", code)
    return _deserialize_result(data)


def execute_pandas_code(code_string: str, df: pd.DataFrame) -> ExecutionResult:
    code = clean_code(code_string)

    # Pre-controllo rapido (fail-fast, senza avviare un processo)
    parsed = _parse_and_validate(code)
    if isinstance(parsed, ExecutionFailure):
        return parsed

    # Esecuzione isolata in sottoprocesso (con timeout e cap memoria su POSIX).
    if settings.sandbox_subprocess:
        try:
            return _run_in_subprocess(code, df, settings.exec_timeout)
        except subprocess.TimeoutExpired:
            return ExecutionFailure(
                "timeout", f"Errore: esecuzione interrotta dopo {settings.exec_timeout}s "
                "(codice troppo lento o troppo pesante).", code)
        except Exception as e:
            # Il sottoprocesso non è avviabile. L'in-process NON ha timeout né
            # limite di memoria: degradare a esso indebolisce la sandbox. Per questo
            # è dietro un flag che il deploy pubblico tiene spento (fail-closed).
            if not settings.allow_inprocess_fallback:
                log.error("Sandbox subprocess non avviabile e fallback disattivato: %s", e)
                return ExecutionFailure(
                    "internal", "Errore: ambiente di esecuzione isolato non disponibile. "
                    "Per sicurezza l'esecuzione è stata bloccata.", code)
            log.warning("Sandbox subprocess non avviabile (%s): fallback in-process "
                        "SENZA timeout/limite memoria.", e)

    return _run_code(code, df)
