"""
Stima del costo. Si testa il MECCANISMO (token → USD, separazione input/output,
gestione dei casi ignoti), non i valori del listino, che cambiano di continuo.
"""
import pytest

from nlda.pricing import PRICES_PER_1M, Usage, estimate_cost_usd


# --- Usage: il totale è la somma, ma None resta None ----------------------------
def test_total_none_se_nessun_conteggio():
    assert Usage().total_tokens is None


def test_total_somma_input_e_output():
    assert Usage(input_tokens=1000, output_tokens=250).total_tokens == 1250


def test_total_tollera_uno_solo_dei_due():
    assert Usage(input_tokens=300).total_tokens == 300
    assert Usage(output_tokens=40).total_tokens == 40


# --- estimate_cost_usd ----------------------------------------------------------
def test_costo_pesa_input_e_output_diversamente():
    # Verifica la MATEMATICA con un prezzo iniettato, non un valore reale di listino:
    # input 0.15 e output 0.60 per 1M → 2000*0.15/1e6 + 1000*0.60/1e6 = 0.0009.
    modello = next(m for m, p in PRICES_PER_1M.items() if p == (0.15, 0.60))
    costo = estimate_cost_usd(modello, Usage(input_tokens=2000, output_tokens=1000))
    assert costo == pytest.approx(0.0009)


def test_modello_non_a_listino_e_costo_sconosciuto():
    # None significa "sconosciuto", da non confondere con 0 (che è gratuito).
    assert estimate_cost_usd("modello-mai-visto-9000", Usage(100, 50)) is None


def test_senza_token_niente_costo():
    modello = next(iter(PRICES_PER_1M))
    assert estimate_cost_usd(modello, Usage()) is None


def test_ogni_modello_di_default_conosce_il_proprio_prezzo():
    # I modelli cloud proposti di default devono avere un prezzo: un default senza
    # listino mostrerebbe sempre costo None, vanificando la feature.
    from nlda.providers import DEFAULT_MODELS

    cloud = {n: m for n, m in DEFAULT_MODELS.items() if n != "ollama"}
    mancanti = [m for m in cloud.values() if m not in PRICES_PER_1M]
    assert not mancanti, f"modelli di default senza prezzo a listino: {mancanti}"
