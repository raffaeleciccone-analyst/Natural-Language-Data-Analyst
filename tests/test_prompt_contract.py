"""
Il system prompt come CONTRATTO, verificato senza chiamare alcun modello.

Il prompt è l'unico artefatto del progetto che nessun test tocca davvero: la
suite sostituisce il provider con un finto, quindi il testo delle istruzioni non
viene mai letto da nessuno. È un punto cieco costoso — una modifica accidentale
degrada le risposte del modello senza che nulla diventi rosso, ed è già successo:
una sostituzione automatica su parola intera aveva trasformato "di cosa parlano i
dati" in "di description parlano i dati", con tutta la suite verde.

Due difese, entrambe deterministiche e a costo zero:

1. un GOLDEN: il prompt renderizzato è confrontato con una copia registrata, così
   ogni modifica — voluta o no — si vede come diff e va confermata;
2. dei CONTRATTI: ciò che il prompt promette al modello deve esistere davvero
   nell'ambiente di esecuzione, e il codice che gli insegna deve superare la
   nostra stessa sandbox. Istruire il modello a scrivere qualcosa che poi
   rifiutiamo è un ciclo di fallimento garantito, e si può escludere offline.
"""
import ast
import os
import re
from pathlib import Path

import pandas as pd
import pytest

from nlda.agent import DataAgent
from nlda.providers.base import LLMProvider
from nlda.results import ExecutionFailure
from nlda.sandbox.validator import SAFE_BUILTINS, _parse_and_validate

GOLDEN = Path(__file__).parent / "fixtures" / "system_prompt.golden.txt"

# I prompt sono QUATTRO, non uno: oltre a quello che genera codice ci sono i tre
# narrativi (panoramica, spiegazione, report esecutivo). Coprire solo il primo
# lascerebbe scoperti proprio quelli che si sono già rotti una volta: la frase
# corrotta dalla sostituzione automatica ("di cosa parlano i dati") stava nel
# prompt della panoramica. Si raccolgono tutti registrandoli da un provider finto.
_SEPARATORE = "\n\n" + "=" * 78 + "\n### PROMPT: {}\n" + "=" * 78 + "\n\n"

# Nomi che l'executor mette a disposizione del codice generato (vedi `_run_code`).
AMBIENTE_DI_ESECUZIONE = {"df", "pd", "px", "go", "to_chart", "try_chart"} | set(SAFE_BUILTINS)

# Variabili da cui l'executor raccoglie il risultato finale.
NOMI_DEL_RISULTATO = {"result", "risultato", "fig"}


class _Registratore(LLMProvider):
    """Provider finto che non risponde nulla di utile ma REGISTRA il prompt ricevuto."""

    def __init__(self):
        super().__init__(model_name="registratore")
        self.ultimo_system = ""

    def _call(self, system_prompt: str, user_prompt: str) -> str:
        self.ultimo_system = system_prompt
        return "df['Sales'].sum()"


# Schema FISSO: il golden deve dipendere solo dal codice, non dal dataset di
# esempio, che può cambiare senza preavviso.
_DF = pd.DataFrame({
    "Region": ["North", "South"],
    "Category": ["Furniture", "Technology"],
    "Sales": [100.0, 200.0],
    "Quantity": [2, 3],
    "Order Date": pd.to_datetime(["2023-01-05", "2023-02-11"]),
})


@pytest.fixture(scope="module")
def prompts() -> dict[str, str]:
    """Tutti i system prompt del progetto, raccolti registrandoli."""
    registratore = _Registratore()
    agent = DataAgent(provider=registratore)

    raccolti = {"generazione_codice": agent._get_system_prompt(_DF)}
    agent.overview("profilo del dataset")
    raccolti["panoramica"] = registratore.ultimo_system
    agent.explain("quanto vendo?", "totale 100")
    raccolti["spiegazione"] = registratore.ultimo_system
    agent.executive_report("insight calcolati")
    raccolti["report_esecutivo"] = registratore.ultimo_system
    return raccolti


@pytest.fixture(scope="module")
def prompt(prompts: dict[str, str]) -> str:
    """Il solo prompt di generazione del codice, per i contratti sul codice."""
    return prompts["generazione_codice"]


def _esempi_di_codice(prompt: str) -> list[str]:
    """
    Estrae le righe di CODICE dal prompt, escludendo la prosa.

    Criterio: righe indentate di tre spazi (la forma con cui il prompt presenta gli
    esempi) oppure la riga finale dell'esempio grafico, che contengano un
    assegnamento ' = '. La prosa delle regole non lo contiene — "NON usare
    df.groupby(...)" resta fuori — mentre ogni esempio reale lo ha.
    """
    righe = [r for r in prompt.splitlines()
             if (r.startswith("   ") or r.startswith("data = ") or r.startswith("fig = px"))
             and " = " in r]
    return [r.strip() for r in righe]


# --- 1. Golden: il prompt non cambia per sbaglio -------------------------------
def test_i_prompt_non_sono_cambiati(prompts: dict[str, str]):
    """
    Se questo test è rosso, uno dei quattro prompt è cambiato. Le due possibilità:

    * la modifica è voluta -> rigenera il golden con
      `UPDATE_PROMPT_GOLDEN=1 pytest tests/test_prompt_contract.py` e committa il
      file insieme al codice, così il diff resta leggibile nella storia;
    * la modifica NON è voluta -> hai appena degradato le istruzioni al modello,
      e nessun altro test se ne accorgerebbe.
    """
    atteso = "".join(_SEPARATORE.format(nome) + testo for nome, testo in sorted(prompts.items()))

    if os.getenv("UPDATE_PROMPT_GOLDEN"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        GOLDEN.write_text(atteso, encoding="utf8")
        pytest.skip("golden rigenerato: rilancia i test senza UPDATE_PROMPT_GOLDEN")

    assert GOLDEN.exists(), (
        f"golden mancante: generalo con UPDATE_PROMPT_GOLDEN=1 pytest {__file__}")
    assert atteso == GOLDEN.read_text(encoding="utf8"), (
        "un system prompt è cambiato rispetto al golden registrato (vedi docstring)")


def test_il_golden_copre_tutti_i_prompt(prompts: dict[str, str]):
    # Se un domani si aggiunge un prompt e non lo si registra qui, il golden
    # continuerebbe a passare lasciandolo scoperto: è così che il prompt della
    # panoramica è rimasto senza rete la prima volta.
    assert set(prompts) == {"generazione_codice", "panoramica", "spiegazione",
                            "report_esecutivo"}
    for nome, testo in prompts.items():
        assert len(testo) > 100, f"prompt '{nome}' sospettosamente corto: {testo[:60]!r}"


# --- 2. Contratti: ciò che il prompt promette deve esistere ---------------------
def test_gli_esempi_di_codice_sono_python_valido(prompt: str):
    esempi = _esempi_di_codice(prompt)
    # Se l'estrazione non trova nulla il test passerebbe verificando zero cose:
    # meglio fallire e far aggiornare il criterio.
    assert len(esempi) >= 8, f"estratti solo {len(esempi)} esempi: criterio da rivedere"
    for e in esempi:
        try:
            ast.parse(e)
        except SyntaxError as err:
            pytest.fail(f"esempio non parsificabile nel prompt: {e!r} ({err.msg})")


def test_gli_esempi_del_prompt_superano_la_nostra_sandbox(prompt: str):
    """
    Il codice che insegniamo al modello deve essere codice che accettiamo.
    Insegnargli una forma che il validatore rifiuta significa costruire un ciclo
    di fallimento: il modello obbedisce, noi blocchiamo, si ritenta, si fallisce.
    """
    for e in _esempi_di_codice(prompt):
        esito = _parse_and_validate(e)
        assert not isinstance(esito, ExecutionFailure), (
            f"il prompt insegna codice che la sandbox rifiuta: {e!r} -> {esito.message}")


def test_i_nomi_promessi_esistono_nellambiente_di_esecuzione(prompt: str):
    """
    Se il prompt cita una funzione che l'executor non espone, il modello la userà
    e otterrà un NameError — un errore che nessuna correzione può risolvere,
    perché il nome non esiste da nessuna parte.
    """
    citati = set(re.findall(r"\b(df|pd|px|go|to_chart|try_chart|st)\b", prompt))
    citati.discard("st")  # citato solo per VIETARLO ("niente st.*")
    mancanti = citati - AMBIENTE_DI_ESECUZIONE
    assert not mancanti, f"il prompt promette nomi che non esistono a runtime: {mancanti}"


def test_le_variabili_insegnate_sono_quelle_che_lexecutor_legge(prompt: str):
    """
    Il prompt insegna dove mettere il risultato finale; l'executor lo cerca lì.
    Se i due nomi divergono il codice gira, non fallisce, e l'utente non vede
    nulla: il caso peggiore, perché non produce alcun errore.
    """
    insegnate = set(re.findall(r"variabile chiamata '(\w+)'", prompt))
    assert insegnate, "il prompt non indica più dove mettere il risultato"
    assert insegnate <= NOMI_DEL_RISULTATO, (
        f"il prompt insegna {insegnate - NOMI_DEL_RISULTATO}, che l'executor non legge")


def test_il_prompt_vieta_streamlit(prompt: str):
    # Il codice generato gira nel worker, dove `st` non esiste: senza il divieto
    # esplicito i modelli tendono a produrre st.bar_chart(...).
    assert "st.*" in prompt or "niente st." in prompt.lower()
