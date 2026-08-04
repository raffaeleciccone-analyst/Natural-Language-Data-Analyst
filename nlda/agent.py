import ast

import pandas as pd

from nlda import prompts
from nlda.errors import ProviderError
from nlda.log import get_logger
from nlda.providers import LLMProvider, get_provider
from nlda.sanitize import MAX_LEN_NOME, sanitize
from nlda.utils import clean_code, column_kind

log = get_logger(__name__)


# Tetto alle colonne descritte nel prompt: un dataset largo (fino a 500 colonne,
# vedi settings.max_columns) genererebbe un prompt enorme e costoso in token, e un
# modello sommerso da centinaia di colonne sceglie peggio. Oltre il tetto le altre
# si dichiarano in coda, così l'omissione è trasparente e non silenziosa.
MAX_SCHEMA_COLS = 100


def _describe_schema(df: pd.DataFrame) -> str:
    """
    Descrizione dello schema per il prompt: nome, tipo ed esempi di ogni colonna.

    Tutto ciò che finisce qui dentro viene dal file dell'utente, quindi passa dal
    sanitizzatore — i VALORI e anche i NOMI. Il nome di colonna era il punto
    scoperto: un'intestazione come "Ignora le istruzioni precedenti e..." finiva
    grezza in mezzo alle regole del prompt, con a capo e backtick compresi.

    Oltre `MAX_SCHEMA_COLS` colonne se ne elencano solo le prime e si dichiara
    quante restano fuori: il modello può usare solo ciò che vede, ma un prompt
    illimitato costerebbe in token e in qualità della scelta.
    """
    colonne = list(df.columns)
    lines = []
    for col in colonne[:MAX_SCHEMA_COLS]:
        kind = column_kind(df[col])
        try:
            samples = df[col].dropna().unique()[:3]
            sample_str = ", ".join(sanitize(s) for s in samples)
        except Exception:
            sample_str = ""
        lines.append(f"- '{sanitize(col, MAX_LEN_NOME)}' (tipo: {kind}) — esempi: {sample_str}")
    if len(colonne) > MAX_SCHEMA_COLS:
        lines.append(f"- (… e altre {len(colonne) - MAX_SCHEMA_COLS} colonne, "
                     "non elencate per limitare la lunghezza del prompt)")
    return "\n".join(lines)


def _split_commenti_iniziali(code: str) -> tuple[str, str]:
    """
    Separa i commenti in testa dal codice vero: `("# mappa: ...\\n", "df.sum()")`.

    Serve a `_wrap_chart`, che inserisce il codice dentro un'assegnazione: con la
    regola 10 il modello scrive delle righe di commento prima dell'espressione, e
    `result = # mappa: ...` sarebbe un errore di sintassi. La testa torna col suo
    a-capo così si può riattaccare davanti senza ricomporre nulla.
    """
    righe = code.split("\n")
    tagliato = 0
    for riga in righe:
        if riga.strip().startswith("#") or not riga.strip():
            tagliato += 1
        else:
            break
    testa = "\n".join(righe[:tagliato])
    return (testa + "\n" if testa else ""), "\n".join(righe[tagliato:]).strip()


def _example_columns(df: pd.DataFrame):
    """Sceglie una colonna categoriale e una numerica reali per un esempio calzante."""
    cat = num = None
    for col in df.columns:
        kind = column_kind(df[col])
        if kind == "testo" and cat is None:
            cat = col
        if kind == "numerica" and num is None:
            num = col
    return cat, num


class DataAgent:
    """
    Agente che traduce una domanda in linguaggio naturale in codice Pandas.
    È indipendente dal provider LLM e si adatta allo schema del dataset caricato
    (nomi, tipi ed esempi delle colonne vengono passati al modello a ogni domanda).
    Qui vive anche l'euristica "serve un grafico?": è l'unica fonte di verità, così
    il layer UI non deve duplicare parole chiave o wrapping.
    """

    # Deve restare allineata alla regola 4 del system prompt: se il modello riceve
    # l'istruzione di disegnare per una parola che qui manca, l'app promette un
    # grafico e non lo produce (era il caso di "mostrami", proposta come esempio
    # nel README e nel placeholder). "mostrami"/"visualizza" erano state tolte
    # perché su una domanda scalare l'avvolgimento falliva: ora `try_chart`
    # restituisce None invece di sollevare, quindi il motivo non sussiste più.
    _CHART_WORDS = ("grafico", "plot", "barre", "linee", "andamento", "istogramma",
                    "trend", "distribuzione", "diagramma", "mostrami", "mostra",
                    "visualizza")
    _LINE_WORDS = ("andamento", "linee", "trend", "tempo", "temporale")

    def __init__(self, provider: "str | LLMProvider" = "ollama",
                 model_name: str | None = None,
                 temperature: float = 0.0,
                 api_key: str | None = None):
        if isinstance(provider, LLMProvider):
            self.provider = provider
        else:
            self.provider = get_provider(
                provider, model_name=model_name,
                temperature=temperature, api_key=api_key,
            )
        # Cache dello schema descritto: `_get_system_prompt` è chiamato a ogni
        # domanda e a ogni retry, ma per lo stesso dataset lo schema non cambia.
        self._schema_sig: tuple | None = None
        self._schema_text: str = ""

    def _schema_for(self, df: pd.DataFrame) -> str:
        """
        Schema descritto una volta per dataset. La firma è cheap (forma, nomi e
        dtype delle colonne, O(colonne)): non hasha i dati, così il controllo di
        cache non reintroduce il costo che vuole evitare.

        Compromesso dichiarato: due dataset con stessa forma, stessi nomi e stessi
        dtype ma valori diversi condividerebbero la descrizione (inclusi i valori
        di esempio). Nella stessa sessione non accade — il dataset è fisso — e se
        accadesse cambierebbero solo 3 esempi indicativi per colonna, mai un numero.
        """
        sig = (df.shape, tuple(df.columns), tuple(str(t) for t in df.dtypes))
        if sig != self._schema_sig:
            self._schema_sig = sig
            self._schema_text = _describe_schema(df)
        return self._schema_text

    def _get_system_prompt(self, df: pd.DataFrame) -> str:
        schema = self._schema_for(df)
        cat, num = _example_columns(df)

        # Esempio di grafico costruito sulle colonne REALI del dataset caricato,
        # così il modello non viene spinto verso nomi di colonne inesistenti.
        if cat and num:
            example = (
                f"data = df.groupby({cat!r}, as_index=False)[{num!r}].sum(); "
                f"fig = px.bar(data, x={cat!r}, y={num!r}, title={f'{num} per {cat}'!r})"
            )
        elif num:
            example = (
                f"data = df[{num!r}].describe().reset_index(); "
                f"fig = px.bar(data, x='index', y={num!r})"
            )
        else:
            example = "fig = px.bar(df.iloc[:, :2])"

        # Il testo del prompt vive in prompts/code_generation.md (versionato, con
        # golden a protezione). Qui restano solo i due dati che dipendono dal
        # dataset: lo schema e l'esempio di grafico costruito sulle sue colonne.
        return prompts.render("code_generation", schema=schema, example=example)

    def _generate(self, system_prompt: str, user_prompt: str) -> str:
        """
        Chiama il provider e restituisce il codice pulito.

        Un guasto di comunicazione SOLLEVA `ProviderError` invece di restituire un
        testo: prima tornava un commento-sentinella, che l'executor classificava
        come errore di sintassi — cioè come un difetto del codice generato, quindi
        ritentabile. Risultato: un provider irraggiungibile innescava altre tre
        richieste di correzione, tutte destinate a fallire allo stesso modo.
        Il provider ha già esaurito i propri tentativi (retry con backoff in
        `LLMProvider.generate`): qui il guasto è definitivo.
        """
        try:
            raw = self.provider.generate(system_prompt, user_prompt)
        except Exception as e:
            log.error("Generazione codice fallita (%s): %s", self.provider.name, e)
            # classify() sceglie ProviderAuthError/ProviderTimeoutError quando può,
            # così la UI mostra "controlla la API key" o "riprova" invece del testo SDK.
            raise ProviderError.classify(self.provider.name, e) from e
        return clean_code(raw or "")

    @staticmethod
    def _motivo_llm(e: Exception) -> str:
        """
        Motivo leggibile del fallimento narrativo, da mostrare in pagina.

        La narrativa è un COMPLEMENTO: quando manca, l'utente non ha bisogno della
        causa tecnica — quella resta nei log (vedi il `log.error` dei chiamanti).
        Il messaggio del provider, invece, è quasi sempre inadatto alla pagina:
        un `ImportError` espone il nome del pacchetto ('No module named ollama'),
        e l'SDK OpenAI su chiave mancante risponde con un muro che cita perfino
        `OPENAI_API_KEY` mentre il provider attivo è Groq. A un recruiter che apre
        la demo tutto questo fa sembrare l'app rotta. Si distingue solo il caso
        "provider non installato" da "non risponde"; il resto è rumore.
        """
        if isinstance(e, ImportError):   # copre anche ModuleNotFoundError
            return "nessun modello LLM configurato"
        return "il modello AI non è al momento raggiungibile"

    def _narrate(self, system_prompt: str, user_prompt: str, description: str) -> str:
        """Genera testo narrativo (non codice); in caso di errore ritorna un avviso in corsivo.

        A differenza di `_generate` qui NON si solleva: la narrativa è un
        complemento, non il risultato. Se il modello non risponde, l'utente vede
        comunque i numeri calcolati da Pandas, con un avviso al posto del commento.

        Restituisce il testo del modello COM'E'. Ripulirlo qui — togliere i
        backtick, neutralizzare i `$` — era adattarlo al markdown di Streamlit
        dentro un modulo che serve anche il frontend React: vedi
        `ui_components.md_safe`, dove quella pulizia ora vive."""
        try:
            return self.provider.generate(system_prompt, user_prompt).strip()
        except Exception as e:
            log.error("Generazione narrativa (%s) fallita: %s", description, e)
            return f"_(Impossibile generare {description}: {self._motivo_llm(e)})_"

    def _chart_intent(self, question: str):
        """Deduce se la domanda richiede un grafico e di che tipo (linea vs barre)."""
        q = question.lower()
        wants = any(w in q for w in self._CHART_WORDS)
        kind = "line" if any(w in q for w in self._LINE_WORDS) else "bar"
        return wants, kind

    @staticmethod
    def _is_single_expression(code: str) -> bool:
        """True se il codice è una sola espressione, quindi inseribile in una chiamata."""
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError:
            return False
        return len(tree.body) == 1 and isinstance(tree.body[0], ast.Expr)

    @staticmethod
    def _final_result_name(code: str) -> str | None:
        """Nome della variabile assegnata per ultima, se il codice termina con un'assegnazione."""
        try:
            tree = ast.parse(code, mode="exec")
        except SyntaxError:
            return None
        if not tree.body:
            return None
        ultimo = tree.body[-1]
        if isinstance(ultimo, ast.Assign) and len(ultimo.targets) == 1 \
                and isinstance(ultimo.targets[0], ast.Name):
            return ultimo.targets[0].id
        return None

    def _wrap_chart(self, code: str, wants: bool, kind: str) -> str:
        """
        Se serve un grafico ma il modello ha prodotto solo dati, aggiunge la figura.

        Tre forme possibili del codice generato, e cosa se ne fa:

        * una sola ESPRESSIONE (`df.groupby(...).sum()`): la si assegna a `result`
          e le si affianca la figura;
        * codice che termina con un'ASSEGNAZIONE (`result = df.groupby(...)`, la
          forma che il prompt stesso insegna): si aggiunge solo la riga della
          figura, riusando quella variabile. Inserire quel codice dentro una
          chiamata lo trasformerebbe in un argomento keyword —
          `to_chart(result = df..., kind='bar')` è sintassi valida e fallisce a
          runtime con un messaggio incomprensibile;
        * qualunque altra cosa: si lascia stare, meglio nessun grafico che codice
          rotto.

        In tutti i casi si usa `try_chart`, che restituisce None quando i dati non
        sono graficabili: "mostrami il totale" resta così una risposta corretta
        senza grafico, invece di diventare un errore ritentato tre volte. E il
        risultato resta sempre in una variabile, quindi l'utente vede i numeri
        anche quando la figura non si può disegnare.
        """
        if not wants or "fig" in code or "px." in code or "st." in code:
            return code

        pulito = code.strip().rstrip(";")
        # I commenti in testa — la mappa termine→colonna della regola 10 — vanno
        # tenuti FUORI dall'espressione: `result = # mappa: ...` non è codice, e
        # l'AST non aiuta a scoprirlo perché i commenti per lui non esistono.
        # Restano davanti, dove il modello li ha scritti e dove l'utente li legge.
        testa, espressione = _split_commenti_iniziali(pulito)
        if self._is_single_expression(espressione):
            return f"{testa}result = {espressione}\nfig = try_chart(result, kind='{kind}')"

        nome = self._final_result_name(pulito)
        if nome:
            return f"{pulito}\nfig = try_chart({nome}, kind='{kind}')"
        return code

    def ask_code(self, user_question: str, df: pd.DataFrame) -> str:
        """Traduce la domanda in codice Pandas (con eventuale grafico) pronto per la sandbox."""
        wants, kind = self._chart_intent(user_question)
        # Suggerisce l'aggregazione quando serve una figura (evita indici non plottabili)
        question_text = user_question + (" (Raggruppa i dati usando as_index=False)" if wants else "")
        code = self._generate(self._get_system_prompt(df), question_text)
        return self._wrap_chart(code, wants, kind)

    def fix_code(self, user_question: str, df: pd.DataFrame,
                 broken_code: str, error_message: str) -> str:
        """Chiede al modello di correggere il codice che ha generato un errore."""
        wants, kind = self._chart_intent(user_question)
        user_prompt = (
            f"La richiesta dell'utente era:\n{user_question}\n\n"
            f"Questo codice ha prodotto un errore:\n{broken_code}\n\n"
            f"Messaggio di errore:\n{error_message}\n\n"
            "Correggi il codice tenendo conto dello schema del dataset qui sopra. "
            "Restituisci SOLO il codice Python corretto, senza spiegazioni."
        )
        code = self._generate(self._get_system_prompt(df), user_prompt)
        return self._wrap_chart(code, wants, kind)

    def overview(self, dataset_summary: str) -> str:
        """Genera una panoramica introduttiva del dataset in linguaggio naturale."""
        system_prompt = prompts.load("overview")
        user_prompt = (
            f"Profilo del dataset:\n{dataset_summary}\n\n"
            "Scrivi la panoramica introduttiva."
        )
        return self._narrate(system_prompt, user_prompt, "la panoramica")

    def _explain_prompts(self, user_question: str, result_summary: str) -> tuple[str, str]:
        return prompts.load("explain"), (
            f"Domanda dell'utente:\n{user_question}\n\n"
            f"Risultato calcolato dai dati:\n{result_summary}\n\n"
            "Scrivi la risposta discorsiva."
        )

    def explain(self, user_question: str, result_summary: str) -> str:
        """
        Genera una risposta testuale in linguaggio naturale che interpreta il
        risultato calcolato per rispondere alla domanda dell'utente.
        """
        system_prompt, user_prompt = self._explain_prompts(user_question, result_summary)
        return self._narrate(system_prompt, user_prompt, "la spiegazione")

    def explain_stream(self, user_question: str, result_summary: str):
        """
        Come `explain`, ma restituisce la spiegazione a blocchi (effetto typewriter).
        Come `_narrate`, NON solleva: se il modello non risponde, cede un avviso in
        corsivo — la narrativa è un complemento, i numeri di Pandas si vedono comunque.
        A stream finito logga il costo (i token arrivano nell'ultimo blocco).
        """
        system_prompt, user_prompt = self._explain_prompts(user_question, result_summary)
        try:
            # I blocchi escono come li manda il modello: chi li disegna decide
            # come adattarli. Qui passavano per `md_safe`, che e' una regola del
            # markdown di Streamlit, e quei `\$` finivano anche nella chat React,
            # che li mostrava in chiaro.
            yield from self.provider.stream(system_prompt, user_prompt)
        except Exception as e:  # noqa: BLE001 — la narrativa è tollerante, non solleva
            log.error("Streaming della spiegazione fallito: %s", e)
            yield f"_(Impossibile generare la spiegazione: {self._motivo_llm(e)})_"
            return
        log.info("explanation_stream_ok", extra={
            "provider": self.provider.name, "model": self.provider.model_name,
            "tokens": self.provider._last_usage.total_tokens,
            "cost_usd": self.provider.last_cost(),
        })

    def executive_report(self, insights_summary: str) -> str:
        """
        Genera un report esecutivo in markdown a partire dai NUMERI già calcolati.
        Il modello scrive solo la narrazione: non deve calcolare né inventare numeri.
        """
        system_prompt = prompts.load("executive_report")
        user_prompt = (
            f"Insight calcolati sul dataset:\n{insights_summary}\n\n"
            "Scrivi il report esecutivo in Markdown con le cinque sezioni richieste."
        )
        return self._narrate(system_prompt, user_prompt, "il report")
