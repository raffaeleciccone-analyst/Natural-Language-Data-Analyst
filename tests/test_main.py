"""
Test del plumbing della UI (lettura secrets, quota della demo, caricamento dati)
e dell'importabilità dell'entry-point.

Il plumbing vive in `nlda.ui.session`: è proprio la lettura della configurazione
(secrets, quota) a meritare un test, perché sbagliarla non produce un errore ma un
comportamento silenziosamente diverso in produzione. Streamlit è sostituito da un
finto: qui si verifica la logica, non il rendering (coperto da `test_main_history`
e dai test su `ui_components`).

`main.py` resta importabile senza disegnare nulla (`st.set_page_config` è dentro
`configure_page`, chiamata solo da `main()`): lo si fissa qui.
"""
from datetime import date, timedelta
from unittest.mock import MagicMock

import pytest

import main
from nlda.demo import DemoLimits
from nlda.ui import session


class _SessionState(dict):
    """Imita `st.session_state`: accesso sia per chiave sia per attributo."""

    def __getattr__(self, nome):
        return self.get(nome)

    def __setattr__(self, nome, valore):
        self[nome] = valore


@pytest.fixture
def fake_st(monkeypatch):
    """Sostituisce il modulo `st` dentro nlda.ui.session con un finto ispezionabile."""
    finto = MagicMock()
    finto.session_state = _SessionState()
    finto.secrets = {}          # nessun secrets.toml: come in locale
    monkeypatch.setattr(session, "st", finto)
    return finto


# --- Importabilità: la premessa di tutto il resto ------------------------------
def test_importare_main_non_disegna_nulla():
    # Se un domani qualcuno rimettesse una chiamata Streamlit a livello di modulo,
    # questo test resterebbe verde ma l'import tornerebbe ad avere effetti: quello
    # che si fissa qui è che `main()` esista e non venga eseguito all'import.
    assert callable(main.main)
    assert main.__name__ == "main"


# --- Lettura dei secret --------------------------------------------------------
def test_secret_preferisce_i_secrets_allambiente(fake_st, monkeypatch):
    fake_st.secrets = {"PROVIDER": "groq"}
    monkeypatch.setenv("PROVIDER", "openai")
    assert session._secret("PROVIDER") == "groq"


def test_secret_ripiega_sullambiente(fake_st, monkeypatch):
    monkeypatch.setenv("GROQ_API_KEY", "gsk-di-test")
    assert session._secret("GROQ_API_KEY") == "gsk-di-test"


def test_secret_senza_secrets_toml_non_solleva(fake_st, monkeypatch):
    # Fuori dal deploy `st.secrets` solleva: è la condizione normale, non un errore.
    type(fake_st).secrets = property(lambda self: (_ for _ in ()).throw(RuntimeError("no secrets")))
    monkeypatch.delenv("CHIAVE_ASSENTE", raising=False)
    assert session._secret("CHIAVE_ASSENTE", "default") == "default"


# --- Quota della demo ----------------------------------------------------------
def test_demo_disattivata_per_default(fake_st, monkeypatch):
    monkeypatch.delenv("DEMO_MODE", raising=False)
    assert session.demo_limits().enabled is False


@pytest.mark.parametrize("valore,atteso", [("true", True), ("1", True), ("on", True),
                                           ("yes", True), ("false", False), ("", False)])
def test_demo_mode_riconosce_le_forme_del_vero(fake_st, valore, atteso):
    fake_st.secrets = {"DEMO_MODE": valore}
    assert session.demo_limits().enabled is atteso


def test_demo_max_questions_malformato_usa_il_default(fake_st):
    # Un secret scritto male non deve togliere il tetto: sarebbe un tetto assente
    # su un deploy pubblico che paga le chiamate.
    fake_st.secrets = {"DEMO_MODE": "true", "DEMO_MAX_QUESTIONS": "quindici"}
    assert session.demo_limits().max_questions == 15


def test_demo_max_daily_malformato_usa_il_default(fake_st):
    fake_st.secrets = {"DEMO_MODE": "true", "DEMO_MAX_DAILY": "tante"}
    assert session.demo_limits().max_daily == 200


@pytest.fixture
def consumo(monkeypatch):
    """
    Sostituisce il contatore giornaliero condiviso con uno pulito.

    In produzione vive in `st.cache_resource`, cioè una cache di processo: senza
    questa sostituzione i test si passerebbero il conteggio a vicenda.
    """
    stato = {"giorno": date.today(), "usate": 0}
    monkeypatch.setattr(session, "_consumo_giornaliero", lambda: stato)
    return stato


def test_fuori_dalla_demo_non_si_conta_nulla(fake_st, consumo):
    limiti = DemoLimits(enabled=False)
    assert session.demo_allows(limiti, "domande") is True
    session.demo_consume(limiti)
    assert consumo["usate"] == 0
    assert "_demo_q" not in fake_st.session_state


def test_il_controllo_non_consuma_da_solo(fake_st, consumo):
    # È il punto della separazione: prima si contava al CONTROLLO, quindi una
    # richiesta mai partita — provider irraggiungibile — consumava comunque quota.
    limiti = DemoLimits(enabled=True, max_questions=2)
    assert session.demo_allows(limiti, "domande") is True
    assert session.demo_allows(limiti, "domande") is True
    assert consumo["usate"] == 0
    assert fake_st.session_state.get("_demo_q") is None


def test_la_quota_di_sessione_si_esaurisce_e_avvisa(fake_st, consumo):
    limiti = DemoLimits(enabled=True, max_questions=2)
    for _ in range(2):
        assert session.demo_allows(limiti, "domande") is True
        session.demo_consume(limiti)

    assert session.demo_allows(limiti, "domande") is False
    fake_st.warning.assert_called_once()
    assert "2" in fake_st.warning.call_args[0][0]


def test_il_tetto_giornaliero_vale_su_tutte_le_sessioni(fake_st, consumo):
    # Il limite per sessione da solo non protegge nulla: basta una scheda nuova.
    # Qui si simula proprio quello — sessione azzerata, tetto giornaliero pieno.
    limiti = DemoLimits(enabled=True, max_questions=15, max_daily=3)
    consumo["usate"] = 3
    fake_st.session_state.clear()

    assert session.demo_allows(limiti, "domande") is False
    assert "giornaliero" in fake_st.warning.call_args[0][0]


def test_il_tetto_giornaliero_si_azzera_il_giorno_dopo(fake_st, consumo):
    limiti = DemoLimits(enabled=True, max_daily=3)
    consumo.update(giorno=date.today() - timedelta(days=1), usate=3)

    assert session.demo_allows(limiti, "domande") is True
    assert consumo["usate"] == 0 and consumo["giorno"] == date.today()


def test_il_consumo_incrementa_entrambi_i_contatori(fake_st, consumo):
    limiti = DemoLimits(enabled=True)
    session.demo_consume(limiti)
    assert fake_st.session_state["_demo_q"] == 1
    assert consumo["usate"] == 1


# --- Robustezza del report -----------------------------------------------------
def test_try_fig_restituisce_la_figura(fake_st):
    assert session._try_fig(lambda x: x * 2, 21) == 42


def test_try_fig_ingoia_lerrore_e_restituisce_none(fake_st):
    # Una figura che non si costruisce non deve far cadere l'intero report.
    def esplode():
        raise ValueError("colonna assente")

    assert session._try_fig(esplode) is None


# --- Caricamento del dataset ---------------------------------------------------
def test_file_illeggibile_mostra_un_errore_e_non_solleva(fake_st, monkeypatch):
    def esplode(nome, dati):
        raise ValueError("CSV corrotto")

    monkeypatch.setattr(session, "load_uploaded_cached", esplode)
    caricato = MagicMock(name="upload")
    caricato.name = "vendite.csv"
    caricato.getvalue.return_value = b"non un csv"

    df, etichetta = session.load_dataframe(caricato)

    assert df is None and etichetta is None
    fake_st.error.assert_called_once()


def test_dataset_di_default_quando_non_ci_sono_upload(fake_st, monkeypatch):
    import pandas as pd
    monkeypatch.setattr(session, "load_default_cached", lambda: pd.DataFrame({"a": [1]}))

    df, etichetta = session.load_dataframe(None)

    assert etichetta == "Dataset di default (Superstore Sales)"
    assert len(df) == 1


def test_dataset_di_default_mancante_non_solleva(fake_st, monkeypatch):
    def assente():
        raise FileNotFoundError("data/sales.csv")

    monkeypatch.setattr(session, "load_default_cached", assente)
    assert session.load_dataframe(None) == (None, None)


# --- Filtro persistente (funzione pura) ----------------------------------------
def _df_regioni():
    import pandas as pd
    return pd.DataFrame({"Regione": ["Nord", "Sud", "Nord", "Est"], "Val": [1, 2, 3, 4]})


def test_filtro_assente_lascia_il_df_invariato():
    df = _df_regioni()
    fuori, etichetta = session.apply_filter(df, None)
    assert fuori is df and etichetta == ""


def test_filtro_su_un_valore_restringe_e_etichetta():
    df = _df_regioni()
    fuori, etichetta = session.apply_filter(df, ("Regione", ("Nord",)))
    assert list(fuori["Val"]) == [1, 3]
    assert etichetta == "Regione = Nord"


def test_filtro_su_piu_valori():
    df = _df_regioni()
    fuori, etichetta = session.apply_filter(df, ("Regione", ("Nord", "Est")))
    assert list(fuori["Val"]) == [1, 3, 4]
    assert "Nord" in etichetta and "Est" in etichetta


def test_filtro_confronta_come_stringa():
    import pandas as pd
    df = pd.DataFrame({"Anno": [2022, 2023, 2022], "Val": [1, 2, 3]})
    fuori, _ = session.apply_filter(df, ("Anno", ("2022",)))
    assert list(fuori["Val"]) == [1, 3]   # match anche se la colonna è numerica
