import json
import os
import re
from typing import NamedTuple

import pandas as pd

from nlda.utils import column_kind, fmt_num

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
            parsed = pd.to_datetime(sample, errors="coerce", dayfirst=True)
        except Exception:
            continue
        frac = parsed.notna().mean()
        if (bool(_DATE_HINT.search(str(col))) and frac >= 0.5) or frac >= 0.9:
            df[col] = pd.to_datetime(s, errors="coerce", dayfirst=True)
    return df


def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Pulisce i nomi delle colonne (spazi) e rileva automaticamente le colonne data."""
    df.columns = [str(c).strip() for c in df.columns]
    return _maybe_parse_dates(df)


def _stringify_complex(df: pd.DataFrame) -> pd.DataFrame:
    """Converte in stringa le celle contenenti liste/dizionari (JSON annidati),
    così tabelle, grafici e aggregazioni non falliscono."""
    for col in df.columns:
        if df[col].map(lambda v: isinstance(v, (list, dict))).any():
            df[col] = df[col].map(
                lambda v: json.dumps(v, ensure_ascii=False) if isinstance(v, (list, dict)) else v
            )
    return df


def _read_csv_robusto(f) -> pd.DataFrame:
    """Legge un CSV rilevando il separatore e gestendo encoding non-UTF8."""
    try:
        return pd.read_csv(f, sep=None, engine="python")
    except (UnicodeDecodeError, UnicodeError):
        f.seek(0)
        return pd.read_csv(f, sep=None, engine="python", encoding="latin-1")


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
        df = _read_csv_robusto(uploaded_file)

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
            dettaglio = f"min {s.min():.2f} · media {s.mean():.2f} · max {s.max():.2f}"
        else:
            valori = s.dropna().astype(str)
            dettaglio = f"più frequente: {valori.mode().iat[0]}" if not valori.empty else "—"
        rows.append({
            "Colonna": col,
            "Tipo": kind,
            "Mancanti": f"{miss} ({miss / n * 100:.0f}%)" if n else "0",
            "Valori unici": int(s.nunique(dropna=True)),
            "Dettaglio": dettaglio,
        })
    return pd.DataFrame(rows)


def _clean_label(value) -> str:
    """
    Sanitizza un valore di cella (categoria/etichetta) prima di inserirlo nel testo
    passato all'LLM o mostrato in Markdown: i valori sono dati NON fidati (un file
    caricato può contenere prompt injection o sintassi Markdown). Rimuove newline,
    backtick e metacaratteri Markdown di link, e tronca la lunghezza.
    """
    s = str(value).replace("\n", " ").replace("\r", " ").replace("`", "'")
    s = s.translate({ord(c): None for c in "[]()"})  # neutralizza i link Markdown
    return s[:40] + "…" if len(s) > 40 else s


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
        parti = ", ".join(f"{_clean_label(row[cat])}={fmt_num(row[num])}"
                          for _, row in top.head(5).iterrows())
        lines.append(f"Classifica di {num} per {cat} (primi 5): {parti}.")
    if "trend" in res:
        dcol, num, per = res["trend"]
        if not per.empty:
            best = per.loc[per[num].idxmax()]
            worst = per.loc[per[num].idxmin()]
            lines.append(f"Andamento di {num} nel tempo: periodo migliore "
                         f"{best[dcol].date()}={fmt_num(best[num])}, peggiore "
                         f"{worst[dcol].date()}={fmt_num(worst[num])}.")
    if res.get("corr_pairs"):
        parti = ", ".join(f"{a}–{b} (r={r:.2f})" for a, b, r in res["corr_pairs"][:3])
        lines.append(f"Correlazioni più forti tra le misure: {parti}. "
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


def _measure_columns(df: pd.DataFrame, num_cols: list) -> list:
    """Colonne numeriche 'misura' (esclude gli identificatori); ripiega su tutte se vuota."""
    n = len(df)
    measures = [c for c in num_cols if not _is_identifier(c, df[c], n)]
    return measures or num_cols


def measure_columns(df: pd.DataFrame) -> list:
    """API pubblica: colonne numeriche 'misura' del dataset (identificatori esclusi)."""
    num_cols = df.select_dtypes("number").columns.tolist()
    return _measure_columns(df, num_cols) if num_cols else []


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
    coppie = [(c, df[c].nunique(dropna=True)) for c in cat_cols]
    buone = [c for c, u in coppie if 2 <= u <= 30]
    if not buone:
        return min(coppie, key=lambda t: t[1])[0] if coppie else None
    for pattern in _CAT_PRIORITY_GROUPS:
        rx = re.compile(pattern, re.I)
        for c in buone:
            if rx.search(str(c)):
                return c
    return buone[0]


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


def _correlations(df: pd.DataFrame, measures: list, soglia: float = 0.6):
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
    coppie = []
    cols = list(corr.columns)
    for i in range(len(cols)):
        for j in range(i + 1, len(cols)):
            r = corr.iloc[i, j]
            if pd.notna(r) and abs(r) >= soglia:
                coppie.append((cols[i], cols[j], round(float(r), 2)))
    coppie.sort(key=lambda t: abs(t[2]), reverse=True)
    return corr, coppie


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
            nota = " (ultimo periodo parziale)" if _last_period_partial(df, dcol, per) else ""
            if first:
                g = (last - first) / abs(first) * 100
                out.append(f"Dal primo all'ultimo periodo {num} è "
                           f"{'in crescita' if g >= 0 else 'in calo'} del {fmt_num(abs(g))}%{nota}.")
            if prev:
                g2 = (last - prev) / abs(prev) * 100
                out.append(f"Nell'ultimo periodo {num} è {'salito' if g2 >= 0 else 'sceso'} "
                           f"del {fmt_num(abs(g2))}% rispetto al precedente{nota}.")

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
    corr, coppie = _correlations(df, measures)
    if corr is not None:
        res["corr"] = corr
    if coppie:
        res["corr_pairs"] = coppie

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
