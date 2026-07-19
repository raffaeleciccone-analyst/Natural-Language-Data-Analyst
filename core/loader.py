import pandas as pd
import os
import json
import re

from core.utils import column_kind, fmt_num

# Estensioni file supportate per l'upload
SUPPORTED_EXTENSIONS = ["csv", "xlsx", "xls", "json"]


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
        parti = ", ".join(f"{row[cat]}={fmt_num(row[num])}" for _, row in top.head(5).iterrows())
        lines.append(f"Classifica di {num} per {cat} (primi 5): {parti}.")
    if "trend" in res:
        dcol, num, per = res["trend"]
        if not per.empty:
            best = per.loc[per[num].idxmax()]
            worst = per.loc[per[num].idxmin()]
            lines.append(f"Andamento di {num} nel tempo: periodo migliore "
                         f"{best[dcol].date()}={fmt_num(best[num])}, peggiore "
                         f"{worst[dcol].date()}={fmt_num(worst[num])}.")
    return "\n".join(lines)


_ID_PATTERN = re.compile(r'(^|[_\s])(id|code|zip|postal|index|codice|cap|isbn)($|[_\s])', re.I)


def _is_identifier(name, series: pd.Series, n: int) -> bool:
    """Riconosce colonne numeriche che sono in realtà identificatori (ID, CAP, codici)."""
    if _ID_PATTERN.search(str(name)):
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


def _best_category(df: pd.DataFrame, cat_cols: list):
    """Sceglie una colonna categoriale con cardinalità utile per un raggruppamento."""
    coppie = [(c, df[c].nunique(dropna=True)) for c in cat_cols]
    buone = [c for c, u in coppie if 2 <= u <= 30]
    if buone:
        return buone[0]
    return min(coppie, key=lambda t: t[1])[0] if coppie else None


def analyze(df: pd.DataFrame) -> dict:
    """
    Calcola insight quantitativi sul CONTENUTO del dataset:
    statistiche numeriche, classifica principale e andamento temporale.
    Ignora le colonne-identificatore (ID, CAP, codici) per non produrre numeri privi di senso.
    """
    res = {}
    num_cols = df.select_dtypes("number").columns.tolist()
    cat_cols = [c for c in df.columns if column_kind(df[c]) == "testo"]
    date_cols = [c for c in df.columns if column_kind(df[c]) == "data"]

    measures = _measure_columns(df, num_cols) if num_cols else []
    measures = [c for c in measures if df[c].notna().any()]  # scarta colonne tutte-NaN
    main_num = measures[0] if measures else None
    cat = _best_category(df, cat_cols)

    # 1. Statistiche delle colonne-misura (somma, media, min, max)
    if measures:
        stats = df[measures].agg(["sum", "mean", "min", "max"]).T
        stats.columns = ["Somma", "Media", "Minimo", "Massimo"]
        res["numeric_stats"] = stats.reset_index().rename(columns={"index": "Colonna"})

    # 2. Classifica: categoria migliore vs misura principale (top 10 per somma)
    if cat and main_num:
        top = (df.groupby(cat, as_index=False)[main_num].sum()
                 .sort_values(main_num, ascending=False).head(10))
        res["top"] = (cat, main_num, top)

    # 3. Andamento temporale: prima colonna data vs misura principale (per mese)
    if date_cols and main_num:
        dcol = date_cols[0]
        s = df.dropna(subset=[dcol]).copy()
        if not s.empty:
            s["_periodo"] = s[dcol].dt.to_period("M").dt.to_timestamp()
            per = (s.groupby("_periodo", as_index=False)[main_num].sum()
                     .rename(columns={"_periodo": dcol}))
            res["trend"] = (dcol, main_num, per)

    res["text"] = _insights_text(df, res)
    return res


def load_dataset(file_name: str = "sales.csv") -> pd.DataFrame:
    """
    Carica il dataset dalla cartella 'data', pulisce le colonne e ottimizza i tipi di dati.
    """
    # Costruisce il percorso dinamico verso la cartella 'data'
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    file_path = os.path.join(base_dir, "data", file_name)
    
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Errore: Il file {file_name} non è stato trovato in {os.path.dirname(file_path)}")
        
    df = pd.read_csv(file_path)
    
    # 1. Pulisce eventuali spazi bianchi nei nomi delle colonne
    df.columns = df.columns.str.strip()
    
    # 2. Converte le colonne temporali in datetime (fondamentale per le analisi mensili/annuali)
    date_columns = ['Order Date', 'Ship Date']
    for col in date_columns:
        if col in df.columns:
            # dayfirst=True: le date del dataset sono in formato DD/MM/YYYY (es. 08/11/2017 = 8 novembre)
            df[col] = pd.to_datetime(df[col], errors='coerce', dayfirst=True)
            
    return df