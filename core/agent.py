import pandas as pd

from core.log import get_logger
from core.providers import LLMProvider, get_provider
from core.utils import clean_code, column_kind

log = get_logger(__name__)


def _sanitize_sample(value) -> str:
    """
    Sanitizza un valore di cella prima di inserirlo nel prompt: le celle sono
    dati NON fidati (un file caricato potrebbe contenere istruzioni di prompt
    injection). Rimuove i caratteri di controllo/newline e tronca la lunghezza.
    """
    s = str(value).replace("\n", " ").replace("\r", " ").replace("`", "'")
    s = s[:40]
    return s + "…" if len(str(value)) > 40 else s


def _describe_schema(df: pd.DataFrame) -> str:
    """Costruisce la descrizione dello schema: nome, tipo ed esempi (sanitizzati) per colonna."""
    lines = []
    for col in df.columns:
        kind = column_kind(df[col])
        try:
            samples = df[col].dropna().unique()[:3]
            sample_str = ", ".join(_sanitize_sample(s) for s in samples)
        except Exception:
            sample_str = ""
        lines.append(f"- '{col}' (tipo: {kind}) — esempi: {sample_str}")
    return "\n".join(lines)


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

    # "mostrami"/"visualizza" erano troppo generiche (attivavano il grafico anche
    # su richieste scalari). Teniamo solo parole realmente grafiche.
    _CHART_WORDS = ("grafico", "plot", "barre", "linee", "andamento", "istogramma",
                    "trend", "distribuzione", "diagramma")
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

    def _get_system_prompt(self, df: pd.DataFrame) -> str:
        schema = _describe_schema(df)
        cat, num = _example_columns(df)

        # Esempio di grafico costruito sulle colonne REALI del dataset caricato,
        # così il modello non viene spinto verso nomi di colonne inesistenti.
        if cat and num:
            esempio = (
                f"data = df.groupby({cat!r}, as_index=False)[{num!r}].sum(); "
                f"fig = px.bar(data, x={cat!r}, y={num!r}, title={f'{num} per {cat}'!r})"
            )
        elif num:
            esempio = (
                f"data = df[{num!r}].describe().reset_index(); "
                f"fig = px.bar(data, x='index', y={num!r})"
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
5. Se l'utente NON chiede un grafico e la risposta è immediata, restituisci una singola espressione Pandas (es: df['<colonna_numerica>'].sum()).
6. Per calcoli in PIÙ passaggi, esegui i passaggi e metti il RISULTATO FINALE in una variabile chiamata 'risultato' (può essere un numero, una stringa formattata o un DataFrame). NON usare MAI print(). Esempio:
   top = df.groupby('<cat>', as_index=False)['<num>'].sum().sort_values('<num>', ascending=False).head(5)
   perc = top['<num>'].sum() / df['<num>'].sum() * 100
   risultato = f"I primi 5 valgono il {{perc:.1f}}% del totale"
7. Per domande di RIPARTIZIONE o CLASSIFICA (es. "per prodotto", "top N", "quanto incide ognuno"), fornisci una risposta COMPLETA: metti in 'risultato' un DataFrame di dettaglio (con una colonna 'percentuale' sul totale, arrotondata a 1 decimale) E crea un grafico con la funzione to_chart(dati, kind='bar'), che rende leggibili anche i nomi lunghi. Esempio:
   dett = df.groupby('<cat>', as_index=False)['<num>'].sum().sort_values('<num>', ascending=False)
   dett['percentuale'] = (dett['<num>'] / dett['<num>'].sum() * 100).round(1)
   risultato = dett
   fig = to_chart(dett[['<cat>', '<num>']], kind='bar')
8. Per il "top N per gruppo" (es. "top 5 prodotti per regione") usa questo idioma:
   agg = df.groupby(['<gruppo>', '<elemento>'], as_index=False)['<num>'].sum()
   risultato = agg.sort_values('<num>', ascending=False).groupby('<gruppo>', as_index=False).head(5)
   NON usare df.groupby(...).apply(...) seguito da reset_index(drop=True): perde le
   colonne di raggruppamento e causa errori.

ESEMPIO DI GRAFICO (adattato a questo dataset):
{esempio}
"""

    def _generate(self, system_prompt: str, user_prompt: str) -> str:
        """
        Chiama il provider e restituisce il codice pulito. In caso di errore di
        comunicazione restituisce un commento-sentinella: l'executor lo riconosce
        (AST senza statement eseguibili) e lo trasforma in un errore leggibile.
        """
        try:
            raw = self.provider.generate(system_prompt, user_prompt)
        except Exception as e:
            log.error("Generazione codice fallita (%s): %s", self.provider.name, e)
            return f"# Errore di comunicazione con il provider LLM ({self.provider.name}): {e}"
        return clean_code(raw or "")

    def _narrate(self, system_prompt: str, user_prompt: str, cosa: str) -> str:
        """Genera testo narrativo (non codice); in caso di errore ritorna un avviso in corsivo.
        La narrativa è testo puro: rimuove i backtick che alcuni modelli aggiungono a
        caso, perché in Markdown diventano frammenti monospace verdi senza senso."""
        try:
            return self.provider.generate(system_prompt, user_prompt).strip().replace("`", "")
        except Exception as e:
            log.error("Generazione narrativa (%s) fallita: %s", cosa, e)
            return f"_(Impossibile generare {cosa}: {e})_"

    def _chart_intent(self, question: str):
        """Deduce se la domanda richiede un grafico e di che tipo (linea vs barre)."""
        q = question.lower()
        wants = any(w in q for w in self._CHART_WORDS)
        kind = "line" if any(w in q for w in self._LINE_WORDS) else "bar"
        return wants, kind

    def _wrap_chart(self, code: str, wants: bool, kind: str) -> str:
        """Se serve un grafico ma il modello ha prodotto solo dati, li avvolge in to_chart()."""
        if wants and "fig" not in code and "px." not in code and "st." not in code:
            return f"fig = to_chart({code.strip().rstrip(';')}, kind='{kind}')"
        return code

    def ask_code(self, user_question: str, df: pd.DataFrame) -> str:
        """Traduce la domanda in codice Pandas (con eventuale grafico) pronto per la sandbox."""
        wants, kind = self._chart_intent(user_question)
        # Suggerisce l'aggregazione quando serve una figura (evita indici non plottabili)
        domanda = user_question + (" (Raggruppa i dati usando as_index=False)" if wants else "")
        code = self._generate(self._get_system_prompt(df), domanda)
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
        system_prompt = (
            "Sei un analista dati esperto. Ti viene fornito il profilo di un dataset. "
            "Scrivi in italiano una panoramica introduttiva chiara e utile (4-6 frasi): "
            "di cosa parlano i dati, quali sono le colonne principali e il loro significato, "
            "e 2-3 spunti di analisi interessanti che l'utente potrebbe esplorare. "
            "NON mostrare codice. Scrivi i numeri in modo leggibile, con separatore "
            "delle migliaia (es. 2.261.537, non 2261536.78) e al massimo due decimali. "
            "Se il testo indica ESPLICITAMENTE un'unità di misura, riportala accanto ai "
            "numeri; altrimenti NON inventarne una (non scrivere 'unità', 'euro' o simili)."
        )
        user_prompt = (
            f"Profilo del dataset:\n{dataset_summary}\n\n"
            "Scrivi la panoramica introduttiva."
        )
        return self._narrate(system_prompt, user_prompt, "la panoramica")

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
            "spiega solo cosa dicono i dati. Scrivi i numeri in modo leggibile, con "
            "separatore delle migliaia (es. 2.261.537) e al massimo due decimali. "
            "Se il testo indica ESPLICITAMENTE un'unità di misura, riportala accanto ai "
            "numeri; altrimenti NON inventarne una (non scrivere 'unità', 'euro' o simili)."
        )
        user_prompt = (
            f"Domanda dell'utente:\n{user_question}\n\n"
            f"Risultato calcolato dai dati:\n{result_summary}\n\n"
            "Scrivi la risposta discorsiva."
        )
        return self._narrate(system_prompt, user_prompt, "la spiegazione")

    def executive_report(self, insights_summary: str) -> str:
        """
        Genera un report esecutivo in markdown a partire dai NUMERI già calcolati.
        Il modello scrive solo la narrazione: non deve calcolare né inventare numeri.
        """
        system_prompt = (
            "Sei un analista dati senior. Ti vengono forniti insight GIÀ CALCOLATI su "
            "un dataset. Scrivi in italiano un report esecutivo in Markdown con "
            "ESATTAMENTE queste sezioni, nell'ordine, ciascuna come intestazione '## ':\n"
            "## Executive Summary\n## Key Insights\n## Business Recommendations\n"
            "## Possible Risks\n## Next Steps\n\n"
            "REGOLE TASSATIVE:\n"
            "- Usa SOLO i numeri presenti nell'input; non calcolarne di nuovi e non "
            "inventarne. Se un dato non c'è, non citarlo.\n"
            "- Executive Summary e Key Insights: affermativi, basati sui numeri.\n"
            "- Business Recommendations e Possible Risks: formulali come IPOTESI basate "
            "solo sui dati caricati (usa 'potrebbe', 'suggerisce'), mai come certezze; "
            "ricorda che correlazione non è causa.\n"
            "- Niente codice, niente tabelle grezze. Frasi brevi, elenchi puntati dove utile.\n"
            "- NON usare MAI il backtick (`) né blocchi di codice: scrivi i numeri come "
            "testo normale. Per enfasi usa al massimo il grassetto (**testo**). "
            "Un numero come 669.519 va scritto così, mai come `669.519`."
        )
        user_prompt = (
            f"Insight calcolati sul dataset:\n{insights_summary}\n\n"
            "Scrivi il report esecutivo in Markdown con le cinque sezioni richieste."
        )
        return self._narrate(system_prompt, user_prompt, "il report")
