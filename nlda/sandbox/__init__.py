"""
La sandbox del codice generato dall'LLM.

Due moduli con responsabilita' nettamente separate:

* `validator` decide se il codice e' ammissibile — ispeziona, non esegue;
* `runner` lo esegue in un sottoprocesso e ne riporta indietro l'esito.

Chi usa la sandbox dall'esterno passa da `execute_pandas_code`.
"""
from nlda.sandbox.runner import execute_pandas_code, summarize_result
from nlda.sandbox.validator import UnsafeCodeError

__all__ = ["UnsafeCodeError", "execute_pandas_code", "summarize_result"]
