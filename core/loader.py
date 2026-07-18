import pandas as pd
import os
import json

# Estensioni file supportate per l'upload
SUPPORTED_EXTENSIONS = ["csv", "xlsx", "xls", "json"]


def _clean_columns(df: pd.DataFrame) -> pd.DataFrame:
    """Pulisce i nomi delle colonne (spazi) e prova a convertire colonne data comuni."""
    df.columns = [str(c).strip() for c in df.columns]
    for col in ["Order Date", "Ship Date", "Date", "date"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce", dayfirst=True)
    return df


def read_any(uploaded_file) -> pd.DataFrame:
    """
    Legge un file caricato dall'utente in un DataFrame, riconoscendo il formato
    dall'estensione: CSV, Excel (.xlsx/.xls) o JSON.
    """
    name = uploaded_file.name.lower()

    if name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(uploaded_file)  # richiede openpyxl per .xlsx
    elif name.endswith(".json"):
        data = json.loads(uploaded_file.read())
        if isinstance(data, list):
            df = pd.json_normalize(data)          # lista di record -> righe
        elif isinstance(data, dict):
            df = pd.json_normalize(data)          # dict -> singola riga (o annidato appiattito)
        else:
            raise ValueError("Formato JSON non riconosciuto: attesa una lista di oggetti o un oggetto.")
    else:
        df = pd.read_csv(uploaded_file)

    return _clean_columns(df)


def load_dataset(file_name: str = "sales.csv") -> pd.DataFrame:
    """
    Carica il dataset dalla cartella 'data', pulisce le colonne e ottimizza i tipi di dati.
    """
    # Costruisce il percorso dinamico verso la cartella 'data'
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    file_path = os.path.join(base_dir, "data", file_name)
    
    if not os.path.exists(file_path):
        raise FileNotFoundError(f"Errore: Il file {file_name} non è stato trovato in {os.path.dirname(file_path)}")
        
    print(f"Caricamento dati da: {file_path}...")
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