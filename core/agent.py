import re

import pandas as pd

from core.providers import LLMProvider, get_provider


def _clean_code(text: str) -> str:
    """Rimuove i fence markdown (```python ... ```) che i modelli spesso aggiungono."""
    cleaned = re.sub(r'```(?:python)?\s*|\s*```', '', text)
    return cleaned.strip()


def _column_kind(series: pd.Series) -> str:
    """Classifica il tipo di una colonna in una categoria comprensibile per l'LLM."""
    if pd.api.types.is_bool_dtype(series):
        return "booleana"
    if pd.api.types.is_datetime64_any_dtype(series):
        return "data"
    if pd.api.types.is_numeric_dtype(series):
        return "numerica"
    return "testo"


def _describe_schema(df: pd.DataFrame) -> str:
    """Costruisce la descrizione dello schema: nome, tipo ed esempi per ogni colonna."""
    lines = []
    for col in df.columns:
        kind = _column_kind(df[col])
        try:
            samples = df[col].dropna().unique()[:3]
            sample_str = ", ".join(str(s) for s in samples)
        except Exception:
            sample_str = ""
        lines.append(f"- '{col}' (tipo: {kind}) — esempi: {sample_str}")
    return "\n".join(lines)


def _example_columns(df: pd.DataFrame):
    """Sceglie una colonna categoriale e una numerica reali per un esempio calzante."""
    cat = num = None
    for col in df.columns:
        kind = _column_kind(df[col])
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
    """

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

    def _get_system_prompt(self, df: pd.DataFrame) -> str:
        schema = _describe_schema(df)
        cat, num = _example_columns(df)

        # Esempio di grafico costruito sulle colonne REALI del dataset caricato,
        # così il modello non viene spinto verso nomi di colonne inesistenti.
        if cat and num:
            esempio = (
                f"data = df.groupby('{cat}', as_index=False)['{num}'].sum(); "
                f"fig = px.bar(data, x='{cat}', y='{num}', title='{num} per {cat}')"
            )
        elif num:
            esempio = (
                f"data = df['{num}'].describe().reset_index(); "
                f"fig = px.bar(data, x='index', y='{num}')"
            )
        else:
            esempio = "fig = px.bar(df.iloc[:, :2])"

        return f"""Sei un assistente esperto di Python, Pandas e Plotly. Il tuo unico compito è tradurre la richiesta dell'utente in codice Python eseguibile.
Il DataFrame si chiama sempre e solo 'df'. Hai a disposizione Plotly Express già importato come 'px'.

SCHEMA DEL DATASET (usa ESCLUSIVAMENTE queste colonne, con i nomi esatti):
{schema}

REGOLE TASSATIVE:
1. Restituisci SOLO il codice Python puro. Nessun blocco markdown, nessuna introduzione o spiegazione.
2. Usa unicamente le colonne elencate sopra, rispettandone il nome esatto (maiuscole/minuscole comprese). Non inventare colonne.
3. Scegli le colonne in base al tipo: aggrega/somma solo colonne numeriche; raggruppa per colonne di testo o data.
4. Se la richiesta contiene parole come "mostrami", "grafico", "andamento", "visualizza", "plot", "barre", "linee", DEVI creare un grafico con Plotly Express: prepara prima i dati aggregati con groupby(..., as_index=False), poi assegna la figura alla variabile 'fig' usando 'px' con gli argomenti x e y. NON usare funzioni di Streamlit (niente st.*). Usa px.line per andamenti/serie temporali, px.bar per confronti tra categorie.
5. Se l'utente NON chiede un grafico, restituisci una singola espressione Pandas (es: df['<colonna_numerica>'].sum()).

ESEMPIO DI GRAFICO (adattato a questo dataset):
{esempio}
"""

    def ask_code(self, user_question: str, df: pd.DataFrame) -> str:
        """Invia la domanda al provider LLM e riceve la stringa di codice Pandas pulita."""
        system_prompt = self._get_system_prompt(df)
        try:
            raw = self.provider.generate(system_prompt, user_question)
        except Exception as e:
            return f"# Errore di comunicazione con il provider LLM ({self.provider.name}): {e}"

        return _clean_code(raw or "")

    def explain(self, user_question: str, result_summary: str) -> str:
        """
        Genera una risposta testuale in linguaggio naturale che interpreta il
        risultato calcolato per rispondere alla domanda dell'utente.
        """
        system_prompt = (
            "Sei un analista dati esperto. Rispondi SEMPRE in italiano, in modo "
            "chiaro e conciso (massimo 3-4 frasi). Interpreta il risultato dei dati "
            "per rispondere direttamente alla domanda dell'utente, citando i numeri "
            "chiave. NON mostrare codice e NON descrivere il procedimento tecnico: "
            "spiega solo cosa dicono i dati."
        )
        user_prompt = (
            f"Domanda dell'utente:\n{user_question}\n\n"
            f"Risultato calcolato dai dati:\n{result_summary}\n\n"
            "Scrivi la risposta discorsiva."
        )
        try:
            return self.provider.generate(system_prompt, user_prompt).strip()
        except Exception as e:
            return f"_(Impossibile generare la spiegazione: {e})_"
