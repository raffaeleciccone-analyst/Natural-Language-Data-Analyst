import re

from core.providers import LLMProvider, get_provider


def _clean_code(text: str) -> str:
    """Rimuove i fence markdown (```python ... ```) che i modelli spesso aggiungono."""
    cleaned = re.sub(r'```(?:python)?\s*|\s*```', '', text)
    return cleaned.strip()


class DataAgent:
    """
    Agente che traduce una domanda in linguaggio naturale in codice Pandas.
    È indipendente dal provider LLM: riceve (o costruisce) un LLMProvider e
    gli delega la generazione del testo.
    """

    def __init__(self, provider: "str | LLMProvider" = "ollama",
                 model_name: str | None = None,
                 temperature: float = 0.0,
                 api_key: str | None = None):
        """
        :param provider: nome del provider ("ollama", "anthropic", "openai", ...)
                         oppure un'istanza già pronta di LLMProvider.
        :param model_name: nome del modello; se None usa il default del provider.
        :param api_key: chiave API per i provider cloud (Anthropic, OpenAI).
        """
        if isinstance(provider, LLMProvider):
            self.provider = provider
        else:
            self.provider = get_provider(
                provider, model_name=model_name,
                temperature=temperature, api_key=api_key,
            )

    def _get_system_prompt(self, df_columns: list) -> str:
        columns_str = ", ".join([f"'{col}'" for col in df_columns])

        return f"""Sei un assistente esperto di Python, Pandas e Streamlit. Il tuo unico compito è tradurre la richiesta dell'utente in codice Python eseguibile.
Il DataFrame si chiama sempre e solo 'df'.

Colonne disponibili: [{columns_str}]

Hai a disposizione la libreria Plotly Express già importata come 'px'.

REGOLE TASSATIVE:
1. Restituisci SOLO il codice Python puro. Nessun blocco markdown, nessuna introduzione o spiegazione.
2. Se la richiesta dell'utente contiene parole come "mostrami", "grafico", "andamento", "visualizza", "plot", "barre", "linee", DEVI creare un grafico con Plotly Express.
3. Per i grafici: prepara SEMPRE prima i dati aggregati con groupby(..., as_index=False), poi assegna la figura alla variabile 'fig' usando 'px', specificando gli argomenti x e y. NON usare mai funzioni di Streamlit (niente st.*). Usa px.line per andamenti/serie temporali, px.bar per confronti tra categorie.

ESEMPI RIGIDI DI CODICE PER GRAFICI:
- Domanda: "Mostrami le vendite per regione"
  Risposta: data = df.groupby('Region', as_index=False)['Sales'].sum(); fig = px.bar(data, x='Region', y='Sales', title='Vendite per Regione')
- Domanda: "Grafico dei profitti per categoria"
  Risposta: data = df.groupby('Category', as_index=False)['Profit'].sum(); fig = px.bar(data, x='Category', y='Profit', title='Profitti per Categoria')
- Domanda: "Andamento vendite"
  Risposta: data = df.groupby('Order Date', as_index=False)['Sales'].sum(); fig = px.line(data, x='Order Date', y='Sales', title='Andamento Vendite')

Se l'utente NON chiede un grafico, restituisci una singola espressione Pandas (es: df['Sales'].sum()).
"""

    def ask_code(self, user_question: str, df_columns: list) -> str:
        """Invia la domanda al provider LLM e riceve la stringa di codice Pandas pulita."""
        system_prompt = self._get_system_prompt(df_columns)
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
