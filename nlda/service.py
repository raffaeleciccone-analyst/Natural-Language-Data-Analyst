"""
Orchestrazione di un turno di analisi: domanda -> codice -> esecuzione -> spiegazione.

Questa logica viveva dentro main.py, mescolata alle chiamate Streamlit: non era
raggiungibile da un test se non avviando l'intera app. Qui non entra nulla di
Streamlit (niente spinner, niente session_state): il servizio riceve un agente,
restituisce un `Turn` e la UI si limita a mostrarlo.
"""
import time
from collections.abc import Callable
from dataclasses import dataclass

import pandas as pd

from nlda.agent import DataAgent
from nlda.checks import explanation_is_redundant
from nlda.errors import ProviderError
from nlda.log import bind_context, get_logger, new_turn_id
from nlda.results import ExecutionFailure, ExecutionResult, ExecutionSuccess
from nlda.sandbox.runner import execute_pandas_code, summarize_result

log = get_logger(__name__)


@dataclass(frozen=True)
class Turn:
    """Un giro completo di conversazione: cosa è stato chiesto e cosa ne è uscito."""

    question: str
    code: str
    result: ExecutionResult
    explanation: str | None = None


class AnalysisService:
    """Orchestra domanda -> codice -> esecuzione -> spiegazione."""

    def __init__(self, agent: DataAgent, max_retries: int = 3):
        self.agent = agent
        self.max_retries = max_retries

    def answer(self, question: str, df: pd.DataFrame, *,
               explain: bool, unit: str = "",
               on_step: "Callable[[str], None] | None" = None) -> Turn:
        """
        Risponde a una domanda sui dati, ritentando la generazione del codice solo
        quando ha senso farlo (vedi ExecutionFailure.retryable): un rifiuto della
        sandbox verrebbe ribloccato identico, e ritentarlo sprecherebbe chiamate
        all'LLM senza alcuna possibilità di successo.

        Tutto il turno gira sotto un `turn_id`: i log della generazione del codice,
        dei tentativi di correzione e delle chiamate al provider lo riportano, così
        una richiesta lenta o fallita si ricostruisce filtrando su un solo campo.

        `on_step(messaggio)` viene invocato all'inizio di ogni fase (codice →
        esecuzione → eventuali correzioni → spiegazione): serve alla UI per mostrare
        a che punto è, ma è un semplice callable — il servizio resta senza Streamlit.
        """
        with bind_context(turn_id=new_turn_id()):
            t0 = time.monotonic()
            log.info("turn_start", extra={"explain": explain,
                                          "rows": len(df), "cols": df.shape[1]})
            turn = self._answer(question, df, explain=explain, unit=unit, on_step=on_step)
            outcome = "ok" if isinstance(turn.result, ExecutionSuccess) else turn.result.kind
            log.info("turn_end", extra={
                "latency_ms": round((time.monotonic() - t0) * 1000),
                "outcome": outcome, "explained": turn.explanation is not None,
            })
            return turn

    def _answer(self, question: str, df: pd.DataFrame, *,
                explain: bool, unit: str = "",
                on_step: "Callable[[str], None] | None" = None) -> Turn:
        step = on_step or (lambda _m: None)  # no-op se la UI non passa nulla
        code = ""
        try:
            step("Genero il codice…")
            code = self.agent.ask_code(question, df)
            step("Eseguo il codice…")
            result: ExecutionResult = execute_pandas_code(code, df)

            attempt = 0
            while (isinstance(result, ExecutionFailure) and result.retryable
                   and attempt < self.max_retries):
                attempt += 1
                step(f"Correggo il codice (tentativo {attempt}/{self.max_retries})…")
                log.info("code_fix_attempt", extra={
                    "kind": result.kind, "attempt": attempt, "max": self.max_retries})
                code = self.agent.fix_code(question, df, code, result.message)
                result = execute_pandas_code(code, df)
        except ProviderError as e:
            # Il modello è irraggiungibile: non c'è codice da correggere e nessun
            # tentativo può riuscire. Si chiude il turno subito, con la causa giusta.
            return Turn(question=question, code=code,
                        result=ExecutionFailure("provider", f"Errore: {e}", code))

        explanation = None
        # Se la narrazione ripeterebbe ciò che l'utente sta già leggendo non la si
        # chiede: il giudizio sta in `checks`, dove lo trovano identico anche le
        # altre strade che possono narrare (lo streaming dell'API se la genera per
        # conto suo). Se ognuna decidesse a modo proprio, la stessa domanda
        # costerebbe una chiamata al modello in un'interfaccia e non nell'altra.
        if (explain and isinstance(result, ExecutionSuccess)
                and not explanation_is_redundant(question, code, df.columns, result.value)):
            step("Scrivo la spiegazione…")
            explanation = self.agent.explain(question, self._explanation_summary(result, unit))

        return Turn(question=question, code=code, result=result, explanation=explanation)

    def _explanation_summary(self, result: ExecutionSuccess, unit: str) -> str:
        """Riepilogo del risultato dato al modello per la spiegazione. L'unità di
        misura è nota solo alla UI: senza questo aggancio il modello la inventerebbe
        (o la ometterebbe)."""
        summary_text = result.summary or summarize_result(result)
        if unit:
            summary_text = f"Unità di misura: '{unit}'.\n" + summary_text
        return summary_text

    def stream_explanation(self, question: str, result: ExecutionSuccess, unit: str = ""):
        """
        Spiegazione a blocchi (effetto typewriter) per la UI, che la consuma con
        `st.write_stream` e ne conserva il testo finale nel `Turn`. Resta un semplice
        generatore: il servizio non conosce Streamlit.
        """
        return self.agent.explain_stream(question, self._explanation_summary(result, unit))
