"""
Orchestrazione di un turno di analisi: domanda -> codice -> esecuzione -> spiegazione.

Questa logica viveva dentro main.py, mescolata alle chiamate Streamlit: non era
raggiungibile da un test se non avviando l'intera app. Qui non entra nulla di
Streamlit (niente spinner, niente session_state): il servizio riceve un agente,
restituisce un `Turn` e la UI si limita a mostrarlo.
"""
from dataclasses import dataclass

import pandas as pd

from core.agent import DataAgent
from core.executor import execute_pandas_code, summarize_result
from core.log import get_logger
from core.results import ExecutionFailure, ExecutionResult, ExecutionSuccess

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
               explain: bool, unit: str = "") -> Turn:
        """
        Risponde a una domanda sui dati, ritentando la generazione del codice solo
        quando ha senso farlo (vedi ExecutionFailure.retryable): un rifiuto della
        sandbox verrebbe ribloccato identico, e ritentarlo sprecherebbe chiamate
        all'LLM senza alcuna possibilità di successo.
        """
        code = self.agent.ask_code(question, df)
        result = execute_pandas_code(code, df)

        tentativo = 0
        while (isinstance(result, ExecutionFailure) and result.retryable
               and tentativo < self.max_retries):
            tentativo += 1
            log.info("Codice fallito (%s): tentativo di correzione %d/%d",
                     result.kind, tentativo, self.max_retries)
            code = self.agent.fix_code(question, df, code, result.message)
            result = execute_pandas_code(code, df)

        explanation = None
        if explain and isinstance(result, ExecutionSuccess):
            riepilogo = result.summary or summarize_result(result)
            # L'unità di misura è nota solo alla UI: senza questo aggancio il
            # modello la inventerebbe (o la ometterebbe) nella spiegazione.
            if unit:
                riepilogo = f"Unità di misura: '{unit}'.\n" + riepilogo
            explanation = self.agent.explain(question, riepilogo)

        return Turn(question=question, code=code, result=result, explanation=explanation)
