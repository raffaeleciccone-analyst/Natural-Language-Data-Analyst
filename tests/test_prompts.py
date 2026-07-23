"""
Contratto del loader dei prompt. Il testo dei prompt è già coperto byte-per-byte
dal golden (`test_prompt_contract`); qui si verificano solo i confini del loader:
sostituzione dei segnaposto e fallimenti espliciti (mai un prompt monco in silenzio).
"""
import pytest

from nlda import prompts


def test_ogni_prompt_esiste_e_non_e_vuoto():
    for nome in ("code_generation", "overview", "explain", "executive_report"):
        assert len(prompts.load(nome)) > 100


def test_render_sostituisce_i_segnaposto():
    reso = prompts.render("code_generation", schema="SCHEMA_X", example="ESEMPIO_Y")
    assert "SCHEMA_X" in reso and "ESEMPIO_Y" in reso
    assert "$schema" not in reso and "$example" not in reso


def test_render_lascia_intatta_la_graffa_letterale():
    # Il prompt insegna f"...{perc:.1f}%...": la graffa NON è un segnaposto del
    # loader (Template usa '$'), quindi deve restare tale e quale.
    reso = prompts.render("code_generation", schema="s", example="e")
    assert "{perc:.1f}" in reso


def test_render_solleva_se_manca_una_variabile():
    # Un prompt monco degraderebbe le risposte in silenzio: meglio un errore netto.
    with pytest.raises(KeyError):
        prompts.render("code_generation", schema="solo schema")  # manca example


def test_load_solleva_su_prompt_inesistente():
    with pytest.raises(FileNotFoundError):
        prompts.load("prompt_che_non_esiste")
