import io
import json
import os
import re
from typing import NamedTuple

import pandas as pd

from nlda.config import settings
from nlda.log import get_logger
from nlda.sanitize import sanitize
from nlda.utils import column_kind, fmt_num, to_datetime_quiet

log = get_logger(__name__)

# Estensioni file supportate per l'upload
SUPPORTED_EXTENSIONS = ["csv", "xlsx", "xls", "json"]


class Grouped(NamedTuple):
    """
    Un aggregato del report. `key` è la colonna-chiave (categoria per la classifica,
    data per l'andamento), `measure` la colonna di valori, `data` il DataFrame
    [key, measure]. Essendo un NamedTuple resta spacchettabile come prima
    (`k, m, d = grouped`) ma è leggibile via `.key` / `.measure` / `.data`.
    """
    key: str
    measure: str
    data: pd.DataFrame


_DATE_HINT = re.compile(r'(date|data|time|giorno|mese|anno|timestamp|periodo|scadenza)', re.I)


def _maybe_parse_dates(df: pd.DataFrame) -> pd.DataFrame:
    """
    Converte in datetime le colonne testuali che sembrano date, a prescindere dal
    nome: se il nome contiene un indizio ('date/data/time/...') basta che ≥50% dei
    valori sia parsabile; altrimenti serve ≥90% (evita falsi positivi su ID/codici).
    """
    for col in df.columns:
        s = df[col]
        if not (s.dtype == object or pd.api.types.is_string_dtype(s)):
            continue
        sample = s.dropna()
        if sample.empty:
            continue
        try:
            parsed = to_datetime_quiet(sample, dayfirst=True)
        except Exception:  # noqa: BLE001
            # Colonna non interpretabile come data: si passa alla successiva.
            continue  # nosec B112
        frac = parsed.notna().mean()
        if (bool(_DATE_HINT.search(str(col))) and frac >= 0.5) or frac >= 0.9:
            df[col] = to_datetime_quiet(s, dayfirst=True)
    return df


_SIMBOLO_VALUTA = re.compile(r"[$€£¥]")
_SOLO_NON_NUMERICO = re.compile(r"[^\d,.\-]")
_MIGLIAIA_VIRGOLA = re.compile(r"^-?\d{1,3}(,\d{3})+$")   # 1,234 / 12,345,678
_MIGLIAIA_PUNTO = re.compile(r"^-?\d{1,3}(\.\d{3})+$")    # 1.234 / 1.234.567 (EU)


def _numero_da_valuta(v):
    """
    Interpreta una stringa di valuta/numero formattato (`$1,000`, `1.234,56`) come
    float, o None se non è un numero. Il separatore decimale è l'ULTIMO tra ',' e '.';
    con un separatore solo, i gruppi di 3 cifre sono migliaia, il resto è decimale.
    """
    if not isinstance(v, str):
        return v
    s = _SOLO_NON_NUMERICO.sub("", v)
    if not any(ch.isdigit() for ch in s):
        return None
    has_c, has_d = "," in s, "." in s
    if has_c and has_d:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif has_c:
        s = s.replace(",", "") if _MIGLIAIA_VIRGOLA.match(s) else s.replace(",", ".")
    elif has_d:
        s = s.replace(".", "") if _MIGLIAIA_PUNTO.match(s) else s
    try:
        return float(s)
    except ValueError:
        return None


def _numero_semplice(v):
    """Un numero 'nudo' (nessun separatore ambiguo da interpretare), o None."""
    if not isinstance(v, str):
        return v
    try:
        return float(v.strip())
    except ValueError:
        return None


def _maybe_parse_numbers(df: pd.DataFrame) -> pd.DataFrame:
    """
    Converte in numerico le colonne testuali che sono in realtà numeri: valuta
    formattata (`$1,000`) e misure con qualche cella-testo per i mancanti (`n.d.`).

    Due prudenze contro i numeri sbagliati:
    - il munging dei separatori (ambiguo tra stile US ed EU) si applica SOLO alle
      colonne con un simbolo di valuta, dove l'intento 'denaro' è chiaro; le altre
      accettano solo numeri nudi, così una stringa ambigua senza contesto resta testo;
    - si converte solo se ≥90% dei valori non-nulli diventa numero: pochi 'n.d.'/'N/A'
      passano a NaN, ma una colonna categoriale (in gran parte non numerica) resta testo.
    """
    for col in df.columns:
        s = df[col]
        # Solo colonne testuali (in pandas 3 il testo ha dtype 'str', non 'object').
        if not (s.dtype == object or pd.api.types.is_string_dtype(s)):
            continue
        campione = s.dropna().astype(str)
        if campione.empty:
            continue
        valuta = bool(campione.str.contains(_SIMBOLO_VALUTA).mean() >= 0.5)
        num = pd.to_numeric(s.map(_numero_da_valuta if valuta else _numero_semplice),
                            errors="coerce")
        non_nulli = int(s.notna().sum())
        if non_nulli and num.notna().sum() / non_nulli >= 0.9:
            df[col] = num
    return df


def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Pulisce i nomi delle colonne (spazi) e rileva automaticamente date e numeri."""
    df.columns = [str(c).strip() for c in df.columns]
    df = _maybe_parse_dates(df)
    return _maybe_parse_numbers(df)


def _stringify_complex(df: pd.DataFrame) -> pd.DataFrame:
    """Converte in stringa le celle contenenti liste/dizionari (JSON annidati),
    così tabelle, grafici e aggregazioni non falliscono."""
    for col in df.columns:
        if df[col].map(lambda v: isinstance(v, (list, dict))).any():
            df[col] = df[col].map(
                lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
            )
    return df


# Delimitatori che un CSV usa davvero. NON si lascia indovinare il separatore a
# `csv.Sniffer` (sep=None): su un file a COLONNA SINGOLA il sniffer non trova un
# delimitatore vero e ne sceglie uno qualsiasi — tipicamente una lettera
# dell'intestazione — spezzando 'Fatturato' in ['Fa','ura','o']. Qui si cerca solo
# tra delimitatori reali; se l'header non ne contiene, è una colonna sola e va
# tenuta intera.
_CANDIDATI_SEP = [",", ";", "\t", "|"]


def _detect_sep(header: str) -> str:
    presenti = [s for s in _CANDIDATI_SEP if s in header]
    # Nessun delimitatore nell'intestazione -> colonna singola. Si usa ',' proprio
    # perché ASSENTE: non spezza nulla, a differenza di sep=None.
    return max(presenti, key=header.count) if presenti else ","


def _read_csv_resilient(f) -> pd.DataFrame:
    """Legge un CSV rilevando il separatore tra delimitatori reali, togliendo il BOM
    e gestendo gli encoding non-UTF8."""
    raw = f.read()
    if isinstance(raw, str):
        text = raw
    else:
        try:
            text = raw.decode("utf-8-sig")   # '-sig' rimuove il BOM in testa, se c'è
        except UnicodeDecodeError:
            text = raw.decode("latin-1")
    header = text.split("\n", 1)[0]
    return pd.read_csv(io.StringIO(text), sep=_detect_sep(header), engine="python")


def _check_dimensioni(df: pd.DataFrame) -> None:
    """
    Rifiuta i file troppo grandi per essere analizzati in modo utilizzabile.

    Si RIFIUTA invece di campionare: questa app promette che i numeri li calcola
    Pandas sui dati caricati, e un totale calcolato su un campione sarebbe un
    numero sbagliato presentato come giusto. Meglio un errore chiaro.

    Le soglie stanno in `nlda.config` e sono di usabilità, non di memoria (vedi
    lì il perché). Il cap di upload di Streamlit bounda già i CSV, ma non i
    formati compressi: un .xlsx da 25 MB può contenere milioni di righe.
    """
    righe, colonne = df.shape
    if righe > settings.max_rows:
        raise ValueError(
            f"Il file ha {righe:,} righe, oltre il limite di {settings.max_rows:,}. "
            "Filtra o aggrega i dati prima di caricarli, oppure alza MAX_ROWS."
            .replace(",", "."))
    if colonne > settings.max_columns:
        raise ValueError(
            f"Il file ha {colonne} colonne, oltre il limite di {settings.max_columns}. "
            "Tieni solo le colonne che ti servono, oppure alza MAX_COLUMNS.")


def read_any(uploaded_file) -> pd.DataFrame:
    """
    Legge un file caricato dall'utente in un DataFrame, riconoscendo il formato
    dall'estensione: CSV, Excel (.xlsx/.xls) o JSON.
    """
    name = uploaded_file.name.lower()

    if name.endswith((".xlsx", ".xls")):
        # sheet_name=0: viene letto il primo foglio (comportamento documentato)
        df = pd.read_excel(uploaded_file, sheet_name=0)  # richiede openpyxl per .xlsx
    elif name.endswith(".json"):
        data = json.loads(uploaded_file.read())
        if isinstance(data, (list, dict)):
            df = pd.json_normalize(data)  # lista di record -> righe; dict -> riga singola/appiattito
        else:
            raise ValueError("Formato JSON non riconosciuto: attesa una lista di oggetti o un oggetto.")
        df = _stringify_complex(df)  # array/oggetti annidati -> stringa
    else:
        df = _read_csv_resilient(uploaded_file)

    # Il controllo va DOPO la lettura: la dimensione reale si conosce solo a file
    # aperto, perché i formati compressi non la lasciano dedurre dai byte.
    _check_dimensioni(df)
    return _clean_columns(df)


def profile(df: pd.DataFrame) -> pd.DataFrame:
    """Profilo deterministico del dataset: una riga per colonna con tipo, mancanti, unici, dettaglio."""
    n = len(df)
    rows = []
    for col in df.columns:
        s = df[col]
        miss = int(s.isna().sum())
        kind = column_kind(s)
        if kind == "numerica" and s.notna().any():
            detail = f"min {s.min():.2f} · media {s.mean():.2f} · max {s.max():.2f}"
        else:
            values = s.dropna().astype(str)
            detail = f"più frequente: {values.mode().iat[0]}" if not values.empty else "—"
        rows.append({
            "Colonna": col,
            "Tipo": kind,
            "Mancanti": f"{miss} ({miss / n * 100:.0f}%)" if n else "0",
            "Valori unici": int(s.nunique(dropna=True)),
            "Dettaglio": detail,
        })
    return pd.DataFrame(rows)


def _clean_label(value) -> str:
    """
    Sanitizza un'etichetta (categoria, valore di cella) prima di inserirla nel
    testo passato all'LLM o mostrato in Markdown.

    Delega a `nlda.sanitize`: la difesa è UNA per tutto il progetto. Prima ne
    esistevano due copie quasi identiche, qui e in `agent.py`, ed è così che un
    rafforzamento può raggiungerne una e dimenticare l'altra.
    """
    return sanitize(value, strip_markdown=True)


def _insights_text(df: pd.DataFrame, res: dict) -> str:
    """Costruisce un riepilogo testuale dei NUMERI calcolati, per l'LLM."""
    lines = [f"Righe totali: {fmt_num(len(df))}."]
    if "numeric_stats" in res:
        lines.append("Statistiche delle colonne numeriche:")
        for _, r in res["numeric_stats"].iterrows():
            lines.append(f"  - {r['Colonna']}: somma {fmt_num(r['Somma'])}, media {fmt_num(r['Media'])}, "
                         f"min {fmt_num(r['Minimo'])}, max {fmt_num(r['Massimo'])}")
    if "top" in res:
        cat, num, top = res["top"]
        parts = ", ".join(f"{_clean_label(row[cat])}={fmt_num(row[num])}"
                          for _, row in top.head(5).iterrows())
        lines.append(f"Classifica di {num} per {cat} (primi 5): {parts}.")
    if "trend" in res:
        dcol, num, per = res["trend"]
        if not per.empty:
            best = per.loc[per[num].idxmax()]
            worst = per.loc[per[num].idxmin()]
            lines.append(f"Andamento di {num} nel tempo: periodo migliore "
                         f"{best[dcol].date()}={fmt_num(best[num])}, peggiore "
                         f"{worst[dcol].date()}={fmt_num(worst[num])}.")
    if res.get("corr_pairs"):
        parts = ", ".join(f"{a}–{b} (r={r:.2f})" for a, b, r in res["corr_pairs"][:3])
        lines.append(f"Correlazioni più forti tra le misure: {parts}. "
                     "(Correlazione, non causa.)")
    if res.get("findings"):
        lines.append("Osservazioni automatiche (già calcolate, da NON ricalcolare):")
        for f in res["findings"]:
            lines.append(f"  - {f}")
    return "\n".join(lines)


_ID_PATTERN = re.compile(r'(^|[_\s])(id|code|zip|postal|index|codice|cap|isbn)($|[_\s])', re.I)
_YEAR_PATTERN = re.compile(r'(^|[_\s])(year|anno|yr|esercizio)($|[_\s])', re.I)


def _is_year_like(name, series: pd.Series) -> bool:
    """Riconosce colonne 'anno' (dimensione temporale, non una misura da sommare)."""
    if _YEAR_PATTERN.search(str(name)):
        return True
    s = series.dropna()
    if s.empty or not pd.api.types.is_numeric_dtype(s):
        return False
    try:
        vals = s.astype(float)
    except Exception:
        return False
    # interi nell'intervallo degli anni, poca cardinalità -> è un anno, non una misura
    return bool((vals == vals.round()).all() and vals.min() >= 1900
                and vals.max() <= 2100 and s.nunique() <= 100)


def _is_identifier(name, series: pd.Series, n: int) -> bool:
    """Riconosce colonne numeriche che NON sono misure: identificatori (ID, CAP, codici) o anni."""
    if _ID_PATTERN.search(str(name)) or _is_year_like(name, series):
        return True
    # valori quasi tutti distinti -> è una chiave (solo su dataset abbastanza grandi:
    # con poche righe l'unicità è priva di significato)
    if n >= 20 and series.nunique(dropna=True) / n > 0.9:
        return True
    return False


def _is_strong_id(name, series: pd.Series) -> bool:
    """
    Chiave CERTA: nome-ID/anno, oppure una sequenza di interi densa e tutta distinta
    (codici progressivi tipo OrderID 1000..1029). A differenza di `_is_identifier`,
    NON basta l'alta cardinalità: un intero ad alta varianza (Profit, importi) resta
    fuori. Serve al fallback delle misure, per non ripescare una chiave da sommare.
    """
    if _ID_PATTERN.search(str(name)) or _is_year_like(name, series):
        return True
    s = series.dropna()
    if s.empty or not pd.api.types.is_integer_dtype(s):
        return False
    span = int(s.max() - s.min()) + 1
    # tutti distinti E impacchettati densamente nel loro intervallo -> è una sequenza
    return bool(s.nunique() == len(s) and span > 0 and s.nunique() / span >= 0.9)


def _measure_columns(df: pd.DataFrame, num_cols: list) -> list:
    """
    Colonne numeriche 'misura' (esclude gli identificatori).

    Se l'esclusione non lascia nulla, si ripiega sulle numeriche — l'euristica di
    unicità può aver scartato una misura continua (near-unique ma non una chiave) —
    ESCLUSE però le chiavi certe (`_is_strong_id`). Prima il ripiego era su TUTTE le
    numeriche ('measures or num_cols'): un dataset 'OrderID + città' finiva per
    sommare gli ID e dichiarare 'Totale OrderID' con la quota del leader, un numero
    senza senso spacciato per un fatto. Ora un ID progressivo resta fuori e l'analisi
    passa alla modalità a conteggi.
    """
    n = len(df)
    measures = [c for c in num_cols if not _is_identifier(c, df[c], n)]
    if measures:
        return measures
    return [c for c in num_cols if not _is_strong_id(c, df[c])]


def measure_columns(df: pd.DataFrame) -> list:
    """API pubblica: colonne numeriche 'misura' del dataset (identificatori esclusi)."""
    num_cols = df.select_dtypes("number").columns.tolist()
    return _measure_columns(df, num_cols) if num_cols else []


# Misure che, se presenti, l'utente si aspetta di trovare in cima al selettore:
# sono quelle su cui si basa quasi sempre un report di vendite.
_MEASURE_PRIORITY = ("Sales", "Profit", "Revenue", "Amount", "Total")


def ordered_measures(measures: list) -> list:
    """
    Riordina le misure mettendo davanti quelle "principali", preservando l'ordine
    originale per tutte le altre. Serve a scegliere un default sensato quando il
    dataset ha molte colonne numeriche.
    """
    priority = [c for c in _MEASURE_PRIORITY if c in measures]
    return priority + [c for c in measures if c not in priority]


# Indizi di misura economica, in inglese e in italiano: se il nome della colonna
# ne contiene uno e l'utente non ha indicato un'unità, il dollaro è più
# informativo di nessuna unità.
_ECON_HINTS = ("sales", "revenue", "profit", "amount", "price", "cost", "income",
               "expense", "budget", "margin", "fatturato", "vendite", "ricavi",
               "costo", "prezzo", "importo", "spesa", "utile", "margine", "incasso")


def default_unit(measure) -> str:
    """Unità implicita per una misura economica, o stringa vuota se non deducibile."""
    if measure and any(h in str(measure).lower() for h in _ECON_HINTS):
        return "$"
    return ""


# Righe campionate in testa e in coda per la firma del contenuto. Vedi il perché
# in `dataset_signature`: hashare tutto costava quanto il dataset, a ogni click.
_RIGHE_CAMPIONE = 200


def dataset_signature(df: pd.DataFrame, source_label) -> tuple:
    """
    Firma che identifica il CONTENUTO del dataset, non solo la sua provenienza.

    Serve a capire quando ricalcolare report e profilo: due file diversi con lo
    stesso nome devono dare firme diverse, e lo stesso file ricaricato deve dare
    la stessa firma.

    Il contenuto è campionato invece che hashato per intero. Streamlit ri-esegue
    lo script a OGNI interazione — ogni click, ogni tasto in un campo di testo —
    e hashare l'intero DataFrame rendeva questa funzione l'unico costo
    proporzionale ai dati sul percorso caldo: misurati 167 ms per interazione su
    un milione di righe, contro meno di un millisecondo così.

    Il compromesso, dichiarato: due file con stesso nome, stessa forma, stessi
    tipi e prime e ultime 200 righe identiche ma centro diverso darebbero la
    stessa firma. Su un upload reale non succede, e se succedesse la conseguenza
    sarebbe un report non aggiornato — non un numero sbagliato. I dtype entrano
    nella firma proprio per stringere il campionamento: un cambio di tipo di una
    colonna la rende diversa anche a valori uguali.
    """
    if len(df) > 2 * _RIGHE_CAMPIONE:
        campione = pd.concat([df.head(_RIGHE_CAMPIONE), df.tail(_RIGHE_CAMPIONE)])
    else:
        campione = df
    try:
        content_hash: int | None = int(pd.util.hash_pandas_object(campione, index=False).sum())
    except Exception as e:  # noqa: BLE001 — colonne non hashabili: si degrada
        log.warning("Hash del contenuto non calcolabile: %s", e)
        content_hash = None
    return (source_label, df.shape, tuple(df.columns),
            tuple(str(t) for t in df.dtypes), content_hash)


def best_category(df: pd.DataFrame):
    """API pubblica: colonna categoriale con cardinalità utile per un raggruppamento."""
    return _best_category(df, category_columns(df))


def category_columns(df: pd.DataFrame) -> list:
    """API pubblica: colonne categoriali (di testo) del dataset."""
    return [c for c in df.columns if column_kind(df[c]) == "testo"]


# Gruppi di dimensioni "interessanti" per un report, in ordine di priorità
_CAT_PRIORITY_GROUPS = [
    r'(region|zona|area|country|paese|nazion|state|stato|provinc|city|citt|geo)',   # geografico
    r'(category|categoria|segment|segmento|type|tipo|reparto|brand|marca|settore)',  # business
]


def _best_category(df: pd.DataFrame, cat_cols: list):
    """
    Sceglie una colonna categoriale con cardinalità utile per un raggruppamento,
    preferendo dimensioni interessanti (geografiche, poi di business) in base al nome.
    """
    pairs = [(c, df[c].nunique(dropna=True)) for c in cat_cols]
    usable = [c for c, u in pairs if 2 <= u <= 30]
    if not usable:
        return min(pairs, key=lambda t: t[1])[0] if pairs else None
    for pattern in _CAT_PRIORITY_GROUPS:
        rx = re.compile(pattern, re.I)
        for c in usable:
            if rx.search(str(c)):
                return c
    return usable[0]


def monthly_trend(df: pd.DataFrame, date_col: str, measure=None):
    """
    Serie temporale mensile a partire da una colonna data: somma della `measure`
    oppure, se `measure` è None, conteggio dei record (colonna 'conteggio').
    Ritorna un DataFrame [date_col, valore] oppure None se non c'è nulla da mostrare.
    """
    s = df.dropna(subset=[date_col]).copy()
    if s.empty:
        return None
    s["_periodo"] = s[date_col].dt.to_period("M").dt.to_timestamp()
    if measure:
        per = s.groupby("_periodo", as_index=False)[measure].sum()
    else:
        per = s.groupby("_periodo").size().reset_index(name="conteggio")
    return per.rename(columns={"_periodo": date_col})


def _correlations(df: pd.DataFrame, measures: list, threshold: float = 0.6):
    """
    Matrice di correlazione (Pearson) tra le misure e coppie 'forti' (|r| >= soglia).
    Solo su misure reali (niente ID/anni) e dataset non minuscoli, dove la
    correlazione ha senso. Ritorna (corr_df, coppie) oppure (None, []).
    """
    if len(measures) < 2 or len(df) < 10:
        return None, []
    # min_periods: servono almeno 10 righe SOVRAPPOSTE non-NaN per una coppia,
    # altrimenti r diventa NaN (evita "correlazioni forti" spurie su pochi dati).
    corr = df[measures].corr(numeric_only=True, min_periods=10)
    # Scarta righe/colonne interamente NaN (es. misure costanti): niente celle vuote.
    corr = corr.dropna(axis=0, how="all").dropna(axis=1, how="all")
    if corr.shape[1] < 2:
        return None, []
    pairs = []
    cols = list(corr.columns)
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            r = corr.iloc[i, j]
            if pd.notna(r) and abs(r) >= threshold:
                pairs.append((cols[i], cols[j], round(float(r), 2)))
    pairs.sort(key=lambda t: abs(t[2]), reverse=True)
    return corr, pairs


def _last_period_partial(df: pd.DataFrame, dcol: str, per: pd.DataFrame) -> bool:
    """
    True se l'ultimo mese della serie non è coperto fino alla sua fine: i dati
    arrivano prima dell'ultimo giorno del mese, quindi quel periodo è parziale.
    """
    try:
        month_start = per[dcol].iloc[-1]
        month_end = month_start + pd.offsets.MonthEnd(0)
        return df[dcol].max() < month_end
    except Exception:
        return False


def _findings(df: pd.DataFrame, res: dict, main_num) -> list:
    """
    Osservazioni automatiche calcolate in Pandas (numeri, MAI dedotti dall'LLM):
    quota del leader, crescita di periodo, variazione recente, outlier. Ritorna
    una lista di frasi pronte da mostrare e da passare alla narrazione.
    """
    out: list[str] = []
    if not main_num:
        return out

    tot = df[main_num].sum()
    if "top" in res and len(res["top"].data):
        cat, num, top = res["top"]
        lead = top.iloc[0]
        # Solo con totale positivo e quota sensata (0-100%): con misure che possono
        # essere negative — es. Profit con perdite — la percentuale non avrebbe senso.
        if tot > 0 and 0 <= lead[num] <= tot:
            out.append(f"{_clean_label(lead[cat])} da solo pesa il "
                       f"{fmt_num(lead[num] / tot * 100)}% del totale di {num}.")

    if "trend" in res:
        dcol, num, per = res["trend"]
        col = per[num]
        if len(col) >= 2:
            first, last, prev = col.iloc[0], col.iloc[-1], col.iloc[-2]
            # L'ultimo mese può essere ancora in corso (dati fino a metà mese): in tal
            # caso la variazione è gonfiata/sgonfiata e va segnalata come parziale.
            note = " (ultimo periodo parziale)" if _last_period_partial(df, dcol, per) else ""
            if first:
                g = (last - first) / abs(first) * 100
                out.append(f"Dal primo all'ultimo periodo {num} è "
                           f"{'in crescita' if g >= 0 else 'in calo'} del {fmt_num(abs(g))}%{note}.")
            if prev:
                g2 = (last - prev) / abs(prev) * 100
                out.append(f"Nell'ultimo periodo {num} è {'salito' if g2 >= 0 else 'sceso'} "
                           f"del {fmt_num(abs(g2))}% rispetto al precedente{note}.")

    s = df[main_num].dropna()
    if len(s) >= 20:
        q1, q3 = s.quantile(0.25), s.quantile(0.75)
        iqr = q3 - q1
        if iqr > 0:
            lo, hi = q1 - 1.5 * iqr, q3 + 1.5 * iqr
            n_out = int(((s < lo) | (s > hi)).sum())
            if n_out:
                out.append(f"{fmt_num(n_out)} record hanno {main_num} anomalo "
                           f"(fuori dall'intervallo {fmt_num(lo)}–{fmt_num(hi)}): "
                           "possibili valori da verificare.")
    return out


def analyze(df: pd.DataFrame, measure=None, category=None) -> dict:
    """
    Calcola insight quantitativi sul CONTENUTO del dataset, adattandosi alla sua
    forma: con una MISURA numerica usa somme/medie/andamento; SENZA misura ripiega
    su conteggi e distribuzioni. `measure`/`category` permettono di forzare la scelta
    (altrimenti euristica). Ignora identificatori e anni tra le misure.
    """
    res = {}
    num_cols = df.select_dtypes("number").columns.tolist()
    cat_cols = category_columns(df)
    date_cols = [c for c in df.columns if column_kind(df[c]) == "data"]

    measures = _measure_columns(df, num_cols) if num_cols else []
    measures = [c for c in measures if df[c].notna().any()]  # scarta colonne tutte-NaN

    # Misura e categoria: usa quelle indicate se valide, altrimenti euristica
    main_num = measure if (measure in measures) else (measures[0] if measures else None)
    cat = category if (category and category in df.columns) else _best_category(df, cat_cols)
    dcol = date_cols[0] if date_cols else None

    if main_num:
        # --- MODALITÀ MISURA: statistiche, classifica per somma, andamento ---
        stats = df[measures].agg(["sum", "mean", "min", "max"]).T
        stats.columns = ["Somma", "Media", "Minimo", "Massimo"]
        res["numeric_stats"] = stats.reset_index().rename(columns={"index": "Colonna"})

        if cat:
            top = (df.groupby(cat, as_index=False)[main_num].sum()
                     .sort_values(main_num, ascending=False).head(10))
            res["top"] = Grouped(cat, main_num, top)

        if dcol:
            per = monthly_trend(df, dcol, main_num)
            if per is not None:
                res["trend"] = Grouped(dcol, main_num, per)

    elif cat:
        # --- MODALITÀ SENZA MISURA: conteggi e distribuzioni ---
        vc = df[cat].value_counts().head(10)
        res["top"] = Grouped(cat, "conteggio",
                             pd.DataFrame({cat: vc.index, "conteggio": vc.values}))

        if dcol:
            per = monthly_trend(df, dcol)
            if per is not None:
                res["trend"] = Grouped(dcol, "conteggio", per)

    # Osservazioni automatiche deterministiche (per narrazione e card UI)
    findings = _findings(df, res, main_num)
    if findings:
        res["findings"] = findings

    # Correlazioni tra misure (indipendenti dalla modalità)
    corr, pairs = _correlations(df, measures)
    if corr is not None:
        res["corr"] = corr
    if pairs:
        res["corr_pairs"] = pairs

    res["measure"] = main_num
    res["category"] = cat
    res["text"] = _insights_text(df, res)
    return res


def load_dataset(file_name: str = "sales.csv") -> pd.DataFrame:
    """
    Carica il dataset di esempio dalla cartella 'data' e lo passa per la stessa
    pipeline dei file caricati dall'utente (`_clean_columns`): pulizia dei nomi
    di colonna e rilevamento automatico delle colonne data.
    """
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    file_path = os.path.join(base_dir, "data", file_name)

    if not os.path.exists(file_path):
        raise FileNotFoundError(
            f"Errore: Il file {file_name} non è stato trovato in {os.path.dirname(file_path)}"
        )

    return _clean_columns(pd.read_csv(file_path))
