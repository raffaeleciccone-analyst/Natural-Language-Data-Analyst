"""Test del loader: rilevamento date, colonne-misura vs identificatori, lettura file, analyze."""
import io
import json

import pandas as pd
import pytest

from nlda.config import Settings
from nlda.loader import (
    _e_una_colonna_europea,
    _maybe_parse_dates,
    _stima_byte_csv,
    analyze,
    dataset_signature,
    default_unit,
    duplicate_columns_warning,
    measure_columns,
    monthly_trend,
    ordered_measures,
    profile,
    read_any,
)


def test_rileva_colonne_data_per_nome():
    df = pd.DataFrame({"data": ["2023-01-01", "2023-02-01", "2023-03-01"]})
    out = _maybe_parse_dates(df.copy())
    assert pd.api.types.is_datetime64_any_dtype(out["data"])


def test_non_confonde_id_con_date():
    # colonna 'codice' numerica: non deve diventare una data
    df = pd.DataFrame({"codice": ["10010", "20020", "30030"]})
    out = _maybe_parse_dates(df.copy())
    assert not pd.api.types.is_datetime64_any_dtype(out["codice"])


def test_importo_europeo_non_diventa_una_data():
    # Regressione: '1234,56' (decimale all'europea) veniva letto come l'anno 1234,
    # trasformando il fatturato in una colonna di date. I numeri nudi vanno esclusi
    # dal rilevamento date, anche quando il nome della colonna è un indizio ('anno'
    # non c'entra: qui il nome NON è un indizio, ma la difesa deve valere comunque).
    df = pd.DataFrame({"Fatturato": ["1234,56", "2345,67", "987,65", "1500,00"]})
    out = _maybe_parse_dates(df.copy())
    assert not pd.api.types.is_datetime64_any_dtype(out["Fatturato"])


def test_importo_europeo_non_diventa_data_neanche_con_nome_suggestivo():
    # Il nome 'periodo' è un indizio di data (soglia più bassa, 50%), ma valori tutti
    # numerici restano numeri: l'indizio nel nome non deve vincere sui dati.
    df = pd.DataFrame({"periodo": ["1234,56", "2345,67", "987,65"]})
    out = _maybe_parse_dates(df.copy())
    assert not pd.api.types.is_datetime64_any_dtype(out["periodo"])


def test_date_vere_con_separatori_restano_date():
    # La guardia sui numeri non deve toccare le date con separatori.
    df = pd.DataFrame({"quando": ["2023-01-01", "2023-02-01", "2023-03-01"]})
    out = _maybe_parse_dates(df.copy())
    assert pd.api.types.is_datetime64_any_dtype(out["quando"])


def test_measure_columns_esclude_identificatori():
    # Sales con valori ripetuti (una misura reale non è quasi-tutta distinta),
    # Customer ID sequenziale (chiave), Year costante (dimensione temporale).
    df = pd.DataFrame({
        "Customer ID": range(1, 31),
        "Sales": [100.0, 200.0, 150.0] * 10,
        "Year": [2020] * 30,
    })
    measures = measure_columns(df)
    assert "Sales" in measures
    assert "Customer ID" not in measures  # ID escluso (nome + quasi-chiave)
    assert "Year" not in measures          # anno escluso


def test_solo_id_numerico_non_diventa_una_misura():
    # Regressione: con la sola colonna numerica = un ID progressivo, il fallback
    # 'measures or num_cols' la ripescava e l'app sommava gli ID ('Totale OrderID').
    # Ora una chiave densa e distinta resta fuori: niente misura -> modalità conteggi.
    df = pd.DataFrame({"OrderID": range(1000, 1030),
                       "Citta": ["Roma", "Milano"] * 15})
    assert measure_columns(df) == []
    res = analyze(df)
    assert res["measure"] is None       # non somma gli ID
    assert res["category"] == "Citta"   # conteggi per città


def test_misura_intera_ad_alta_varianza_resta_una_misura():
    # Un intero quasi-unico ma NON una sequenza densa (es. importi) è una misura,
    # non una chiave: il fallback selettivo deve ripescarlo.
    df = pd.DataFrame({"Regione": ["N", "S"] * 15,
                       "Importo": [i * 37 % 9000 for i in range(30)]})
    assert "Importo" in measure_columns(df)


def test_read_any_csv_da_bytes():
    csv = b"Region,Sales\nNorth,100\nSouth,200\n"
    buf = io.BytesIO(csv)
    buf.name = "vendite.csv"  # type: ignore[attr-defined]
    df = read_any(buf)
    assert list(df.columns) == ["Region", "Sales"]
    assert len(df) == 2


# --- File storti: dirlo, non fingere di aver capito ----------------------------
def test_un_pdf_travestito_da_csv_viene_rifiutato():
    """
    Prima non dava errore: `read_csv` leggeva i byte del PDF e produceva una
    tabella con una colonna chiamata '%PDF-1.4'. L'app fingeva di aver capito, e
    l'utente si ritrovava un report su spazzatura.
    """
    finto = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nrobaccia binaria"
    with pytest.raises(ValueError, match="sembra un PDF"):
        read_any(_upload(finto, "finto.csv"))


def test_un_binario_senza_firma_nota_viene_comunque_fermato():
    """Rete di sicurezza: un file di testo non contiene byte NUL."""
    with pytest.raises(ValueError, match="byte binari"):
        read_any(_upload(b"\x01\x02\x00\x03 dati", "strano.csv"))


def test_un_file_vuoto_lo_dice_in_italiano():
    with pytest.raises(ValueError, match="Il file è vuoto"):
        read_any(_upload(b"", "vuoto.csv"))


def test_un_file_con_la_sola_intestazione_viene_rifiutato():
    """
    Accettarlo significherebbe mostrare un report di sole caselle vuote e lasciare
    all'utente il compito di capire perche'. La causa e' nel file: si dice subito.
    """
    with pytest.raises(ValueError, match="solo l'intestazione"):
        read_any(_upload(b"Regione,Vendite\n", "solo_header.csv"))


def test_un_excel_che_non_e_un_excel_lo_dice_in_italiano():
    """Il messaggio di pandas parla di 'engine': un dettaglio della libreria."""
    with pytest.raises(ValueError, match="non è un Excel leggibile"):
        read_any(_upload(b"questo e' testo, non un excel", "finto.xlsx"))


def test_una_riga_con_troppi_campi_viene_rifiutata():
    """
    Pandas non la scarta e non solleva: usa i campi in eccesso come INDICE, e da
    li' in poi ogni colonna contiene i valori di quella accanto. Il file si legge
    senza errori e ogni numero che ne esce e' sbagliato. Prima passava con 200.
    """
    with pytest.raises(ValueError, match="più campi dell'intestazione"):
        read_any(_upload(b"A,B\n1,2\n3,4,5,6\n", "storto.csv"))


def test_il_messaggio_dice_quale_riga_correggere():
    """
    La riga 3, non la 1: l'intestazione e' l'unica sicuramente giusta, e additarla
    manderebbe l'utente a correggere l'unica riga che non ha nulla che non va.
    Vale per entrambe le strade con cui pandas tratta questo file (indice inferito
    o ParserError), perche' per chi carica e' lo stesso difetto.
    """
    with pytest.raises(ValueError, match="riga 3"):
        read_any(_upload(b"A,B\n1,2\n3,4,5\n9,9\n", "storto.csv"))

    with pytest.raises(ValueError, match="riga 3"):
        read_any(_upload(b"A,B\n1,2\n3,4,5,6\n7\n", "storto2.csv"))


def test_una_riga_con_MENO_campi_resta_valida():
    """
    E' il caso normale di un valore mancante in coda: pandas mette NaN, le colonne
    restano allineate e il dato e' onesto. Rifiutarlo sarebbe un falso positivo.
    """
    df = read_any(_upload(b"A,B\n1,2\n3\n4,5\n", "buchi.csv"))
    assert len(df) == 3 and list(df.columns) == ["A", "B"]


def test_un_csv_normale_non_e_toccato_dai_nuovi_controlli():
    """Il rischio di un controllo nuovo e' il falso positivo sul caso normale."""
    df = read_any(_upload("Città,Vendite\nRoma,10\nMilano,20\n".encode(), "ok.csv"))
    assert list(df.columns) == ["Città", "Vendite"] and len(df) == 2


def test_un_csv_non_utf8_si_legge_lo_stesso():
    """
    I CSV esportati da Excel in Europa sono spesso in latin-1. Prima la
    decodifica avveniva prima del parser e ripiegava su latin-1 in caso di
    errore; ora decodifica il parser, e il ripiego è un secondo tentativo con
    l'altro encoding. Il comportamento visibile non deve cambiare.
    """
    df = read_any(_upload("Regione,Città\nNord,Perù\n".encode("latin-1"), "eu.csv"))
    assert df["Città"].iat[0] == "Perù"


def test_csv_a_colonna_singola_non_viene_spezzato():
    # Regressione: con sep=None il csv.Sniffer sceglieva una LETTERA dell'header
    # come separatore e spezzava 'Fatturato' in ['Fa','ura','o']. Un CSV a colonna
    # sola deve restare una colonna sola, col nome intatto e il tipo giusto.
    df = read_any(_upload(b"Fatturato\n100\n200\n300\n", "una.csv"))
    assert list(df.columns) == ["Fatturato"]
    assert df["Fatturato"].sum() == 600  # numerico, quindi sommabile


def test_csv_a_colonna_singola_di_testo():
    df = read_any(_upload(b"Citta\nRoma\nMilano\nNapoli\n", "citta.csv"))
    assert list(df.columns) == ["Citta"]
    assert len(df) == 3


def test_csv_separatore_punto_e_virgola():
    df = read_any(_upload(b"Regione;Vendite\nNord;10\nSud;20\n", "pv.csv"))
    assert list(df.columns) == ["Regione", "Vendite"]
    assert df["Vendite"].sum() == 30


def test_csv_separatore_tab():
    df = read_any(_upload(b"Regione\tVendite\nNord\t10\nSud\t20\n", "tab.csv"))
    assert list(df.columns) == ["Regione", "Vendite"]


def test_valuta_formattata_diventa_numerica():
    # '$1,000' era testo -> nessuna analisi economica. Ora si riconosce come numero.
    csv = b"Regione,Fatturato\n" + b"".join(
        f'{"N" if i % 2 else "S"},"$1,{i:03d}"\n'.encode() for i in range(20))
    df = read_any(_upload(csv, "valuta.csv"))
    assert pd.api.types.is_numeric_dtype(df["Fatturato"])
    assert df["Fatturato"].sum() == sum(1000 + i for i in range(20))  # 20.190


def test_valuta_stile_europeo():
    # '1.234,00 €' (punto migliaia, virgola decimale) va letto come 1234.00.
    righe = "".join(f'A,"1.234,{i:02d} €"\n' for i in range(15))
    df = read_any(_upload(("Voce,Importo\n" + righe).encode("utf-8"), "eu.csv"))
    assert pd.api.types.is_numeric_dtype(df["Importo"])
    assert abs(df["Importo"].iloc[0] - 1234.00) < 0.001


def test_colonna_numerica_con_pochi_valori_testo():
    # Missing codificati come testo ('n.d.'): la colonna resta numerica, il testo -> NaN.
    corpo = b"".join(f"N,{i}\n".encode() for i in range(19)) + b"S,n.d.\n"
    df = read_any(_upload(b"Regione,Valore\n" + corpo, "nd.csv"))
    assert pd.api.types.is_numeric_dtype(df["Valore"])
    assert int(df["Valore"].isna().sum()) == 1


def test_colonna_categoriale_non_diventa_numerica():
    # In gran parte non numerica: NON va convertita (niente somme su codici/categorie).
    csv = b"Codice\n" + b"A1\nB2\nrosso\nverde\nblu\n" * 4
    df = read_any(_upload(csv, "cat.csv"))
    assert not pd.api.types.is_numeric_dtype(df["Codice"])


def test_stringa_numerica_ambigua_senza_valuta_resta_testo():
    # '1,234' senza simbolo di valuta è ambiguo (migliaia o decimale?): si lascia
    # testo invece di rischiare un numero sbagliato. CSV a ';' così la virgola resta
    # dentro il valore.
    csv = "Regione;Chiave\n" + "".join(f"N;{v}\n" for v in ["1,234", "5,678", "9,012"] * 8)
    df = read_any(_upload(csv.encode(), "amb.csv"))
    assert not pd.api.types.is_numeric_dtype(df["Chiave"])


def test_csv_bom_viene_rimosso_dall_intestazione():
    # Un BOM in testa lasciava la prima colonna chiamata '﻿Regione', che non
    # combaciava con filtri e domande. utf-8-sig lo toglie.
    df = read_any(_upload("﻿Regione,Vendite\nNord,10\n".encode(), "bom.csv"))
    assert list(df.columns) == ["Regione", "Vendite"]


def test_monthly_trend_aggrega_per_mese(sales_df: pd.DataFrame):
    per = monthly_trend(sales_df, "Order Date", "Sales")
    assert per is not None
    assert len(per) == 3  # gennaio, febbraio, marzo
    assert per["Sales"].sum() == sales_df["Sales"].sum()


def test_analyze_produce_insight(sales_df: pd.DataFrame):
    res = analyze(sales_df, measure="Sales", category="Region")
    assert res["measure"] == "Sales"
    assert res["category"] == "Region"
    assert "top" in res and "trend" in res
    assert isinstance(res["text"], str) and res["text"]


# --- read_any: gli altri formati accettati dall'upload -------------------------
def _upload(contenuto: bytes, nome: str) -> io.BytesIO:
    """Imita il file caricato da Streamlit: un buffer di byte con un attributo .name."""
    buf = io.BytesIO(contenuto)
    buf.name = nome  # type: ignore[attr-defined]
    return buf


def test_read_any_excel():
    # L'estensione .xlsx è offerta nella UI: senza questo test la lettura Excel
    # (e la dipendenza openpyxl che la rende possibile) non è verificata da nulla.
    origine = pd.DataFrame({"Region": ["North", "South"], "Sales": [100, 200]})
    out = io.BytesIO()
    origine.to_excel(out, index=False, engine="openpyxl")
    out.seek(0)

    df = read_any(_upload(out.getvalue(), "vendite.xlsx"))
    assert list(df.columns) == ["Region", "Sales"]
    assert df["Sales"].sum() == 300


def test_read_any_json_lista_di_record():
    payload = b'[{"Region": "North", "Sales": 100}, {"Region": "South", "Sales": 200}]'
    df = read_any(_upload(payload, "vendite.json"))
    assert list(df.columns) == ["Region", "Sales"]
    assert len(df) == 2


def test_read_any_json_annidato_diventa_stringa():
    # Liste e dizionari dentro una cella romperebbero groupby, grafici e tabelle:
    # vengono serializzati in stringa (colonne appiattite dove possibile).
    payload = b'[{"Region": "North", "Tags": ["a", "b"], "Info": {"code": 1}}]'
    df = read_any(_upload(payload, "annidato.json"))
    assert df.loc[0, "Tags"] == '["a", "b"]'
    # json_normalize appiattisce i dizionari in colonne separate.
    assert "Info.code" in df.columns
    # Nessuna cella resta di tipo lista/dict.
    assert not df.map(lambda v: isinstance(v, (list, dict))).any().any()


def test_read_any_json_scalare_e_rifiutato():
    # Un JSON valido ma che non descrive una tabella (qui un numero) deve dare un
    # errore comprensibile, non un DataFrame senza senso più avanti nel flusso.
    with pytest.raises(ValueError, match="JSON"):
        read_any(_upload(b'42', "numero.json"))


def test_read_any_json_malformato_solleva():
    with pytest.raises(ValueError):  # json.JSONDecodeError è una sottoclasse
        read_any(_upload(b'{non-json}', "rotto.json"))


# --- Selettori del report: euristiche estratte da main.py ----------------------
def test_ordered_measures_mette_davanti_le_misure_principali():
    # Con molte colonne numeriche il default proposto all'utente deve essere
    # quello che si aspetta, non la prima in ordine alfabetico o di colonna.
    out = ordered_measures(["Quantity", "Discount", "Sales", "Profit"])
    assert out[:2] == ["Sales", "Profit"]
    assert set(out) == {"Quantity", "Discount", "Sales", "Profit"}


def test_ordered_measures_conserva_lordine_delle_altre():
    assert ordered_measures(["b", "a", "c"]) == ["b", "a", "c"]


def test_ordered_measures_su_lista_vuota():
    assert ordered_measures([]) == []


@pytest.mark.parametrize("misura", ["Sales", "profit", "Importo totale", "Costo unitario"])
def test_default_unit_riconosce_le_misure_economiche(misura):
    assert default_unit(misura) == "$"


@pytest.mark.parametrize("misura", ["Quantity", "Età", "punteggio", None, ""])
def test_default_unit_non_inventa_unita(misura):
    # Su misure non economiche nessuna unità è meglio di una sbagliata: comparirebbe
    # accanto a ogni numero nei KPI e nelle risposte dell'AI.
    assert default_unit(misura) == ""


# --- Firma del dataset ---------------------------------------------------------
def test_dataset_signature_stabile_sullo_stesso_contenuto(sales_df: pd.DataFrame):
    assert dataset_signature(sales_df, "x") == dataset_signature(sales_df.copy(), "x")


def test_dataset_signature_cambia_se_cambiano_i_valori(sales_df: pd.DataFrame):
    # È il caso che conta: stesso nome file, stesse colonne, dati diversi. Senza
    # l'hash del contenuto il report resterebbe quello del file precedente.
    modificato = sales_df.copy()
    modificato.loc[0, "Sales"] = 99999
    assert dataset_signature(sales_df, "x") != dataset_signature(modificato, "x")


def test_dataset_signature_cambia_con_la_sorgente(sales_df: pd.DataFrame):
    assert dataset_signature(sales_df, "a") != dataset_signature(sales_df, "b")


def test_dataset_signature_non_solleva_su_colonne_non_hashabili():
    # Celle con liste dentro: l'hash non è calcolabile, la firma deve degradare.
    df = pd.DataFrame({"a": [[1, 2], [3]]})
    firma = dataset_signature(df, "x")
    assert firma[0] == "x" and firma[-1] is None


# --- Limiti sul file caricato --------------------------------------------------
def _csv_finto(righe: int, colonne: int = 2):
    intestazione = ",".join(f"c{i}" for i in range(colonne))
    corpo = "\n".join(",".join(str(i) for _ in range(colonne)) for i in range(righe))
    buf = io.BytesIO(f"{intestazione}\n{corpo}\n".encode())
    buf.name = "grande.csv"  # type: ignore[attr-defined]
    return buf


def test_troppe_righe_vengono_rifiutate(monkeypatch):
    # Si RIFIUTA invece di campionare: un totale calcolato su un campione sarebbe
    # un numero sbagliato presentato come giusto, e l'app promette il contrario.
    monkeypatch.setattr("nlda.loader.settings", Settings(max_rows=10))
    with pytest.raises(ValueError, match="righe"):
        read_any(_csv_finto(righe=50))


def test_troppe_colonne_vengono_rifiutate(monkeypatch):
    monkeypatch.setattr("nlda.loader.settings", Settings(max_columns=3))
    with pytest.raises(ValueError, match="colonne"):
        read_any(_csv_finto(righe=5, colonne=10))


def test_il_messaggio_dice_cosa_fare(monkeypatch):
    monkeypatch.setattr("nlda.loader.settings", Settings(max_rows=10))
    with pytest.raises(ValueError) as errore:
        read_any(_csv_finto(righe=50))
    assert "MAX_ROWS" in str(errore.value)      # come alzare il limite
    assert "aggrega" in str(errore.value)       # e l'alternativa


def test_un_file_nei_limiti_passa(monkeypatch):
    monkeypatch.setattr("nlda.loader.settings", Settings(max_rows=100, max_columns=10))
    assert len(read_any(_csv_finto(righe=50))) == 50


def test_il_messaggio_delle_troppe_righe_non_storpia_la_prosa(monkeypatch):
    """
    Regressione: le migliaia si formattavano con un `.replace(',', '.')` in coda
    al messaggio, e quella chiamata si applicava all'INTERA frase — le virgole
    della prosa diventavano punti ("50 righe. oltre il limite di 10. Filtra...").
    """
    monkeypatch.setattr("nlda.loader.settings", Settings(max_rows=10))
    with pytest.raises(ValueError) as errore:
        read_any(_csv_finto(righe=50))
    assert "righe, oltre il limite" in str(errore.value)
    assert "caricarli, oppure" in str(errore.value)


# --- Il tetto di MEMORIA: righe e colonne non dicono quanto costa un file -------
def _csv_pesante(righe: int = 60_000, colonne: int = 20, nome: str = "pesante.csv"):
    """
    Un CSV oltre il megabyte di campione, fatto di interi a una cifra.

    È la forma che ha ucciso la demo: leggerissima su disco (due byte per cella)
    e otto volte tanto in memoria, quindi ben dentro i limiti di righe e colonne.
    """
    intestazione = ",".join(f"c{i}" for i in range(colonne))
    riga = ",".join("7" for _ in range(colonne))
    buf = io.BytesIO(f"{intestazione}\n".encode() + f"{riga}\n".encode() * righe)
    buf.name = nome  # type: ignore[attr-defined]
    return buf


def test_un_file_troppo_grande_per_la_memoria_viene_rifiutato(monkeypatch):
    monkeypatch.setattr("nlda.loader.settings", Settings(max_dataset_ram_mb=1))
    with pytest.raises(ValueError) as errore:
        read_any(_csv_pesante())
    assert "memoria" in str(errore.value)
    assert "MAX_DATASET_RAM_MB" in str(errore.value)   # come alzarlo
    assert "estratto" in str(errore.value)             # e cosa fare intanto


def test_il_rifiuto_arriva_PRIMA_di_leggere_tutto_il_file(monkeypatch):
    """
    Il punto dell'intera difesa: se si rifiutasse dopo la lettura, la memoria
    sarebbe già stata presa e su un container piccolo il processo sarebbe già
    morto — portandosi via i dataset di tutti gli altri.

    La prova sta in un file che è insieme troppo grande E malformato in fondo:
    col tetto basso deve vincere il messaggio sulla memoria (la coda non è mai
    stata letta), col tetto alto quello sulla riga storta.
    """
    def grande_e_storto():
        # Troppo grande per il tetto basso, e con in coda una riga che ha PIÙ
        # campi dell'intestazione (una con meno sarebbe il caso legittimo dei
        # valori mancanti, e non verrebbe rifiutata da nessuno dei due controlli).
        coda = ",".join("9" for _ in range(25)).encode() + b"\n"
        return _upload(_csv_pesante(colonne=20).getvalue() + coda, "grande_e_storto.csv")

    monkeypatch.setattr("nlda.loader.settings", Settings(max_dataset_ram_mb=1))
    with pytest.raises(ValueError, match="memoria"):
        read_any(grande_e_storto())

    monkeypatch.setattr("nlda.loader.settings", Settings(max_dataset_ram_mb=4096))
    with pytest.raises(ValueError, match="più campi dell'intestazione"):
        read_any(grande_e_storto())


def test_la_stima_si_avvicina_alla_memoria_vera():
    """
    Una stima che sbaglia di molto è peggio di nessuna stima: sotto lascia
    passare i file che uccidono, sopra rifiuta quelli buoni. Il margine qui è
    largo (30%) perché a decidere è l'ordine di grandezza, non la precisione.
    """
    sorgente = _csv_pesante()
    raw = sorgente.getvalue()
    stimato = _stima_byte_csv(raw)
    vero = int(pd.read_csv(io.BytesIO(raw)).memory_usage(deep=True).sum())
    assert stimato is not None
    assert 0.7 * vero <= stimato <= 1.3 * vero, f"stimati {stimato}, veri {vero}"


def test_un_file_piccolo_non_si_stima():
    """Sotto il campione non c'è nulla da estrapolare: decide il controllo dopo
    la lettura, che su un file piccolo è sicuro e più preciso."""
    assert _stima_byte_csv(b"a,b\n1,2\n") is None


def test_il_tetto_vale_anche_dove_la_stima_non_arriva(monkeypatch):
    """
    Un .xlsx è compresso e un JSON va letto tutto: la memoria non si sa prima. Il
    tetto si applica lo stesso DOPO la lettura — non evita il picco, ma evita di
    tenere occupata la memoria di un dataset che questa installazione non regge.
    """
    monkeypatch.setattr("nlda.loader.settings", Settings(max_dataset_ram_mb=0))
    payload = json.dumps([{"a": i, "b": "x"} for i in range(100)]).encode()
    with pytest.raises(ValueError, match="memoria"):
        read_any(_upload(payload, "dati.json"))


# --- La firma è campionata: cosa distingue e cosa no --------------------------
def test_la_firma_distingue_un_cambio_in_testa_e_in_coda():
    a = pd.DataFrame({"x": range(1000), "y": ["v"] * 1000})
    testa = a.copy()
    testa.loc[0, "x"] = 99999
    coda = a.copy()
    coda.loc[999, "x"] = 99999
    assert dataset_signature(a, "f") != dataset_signature(testa, "f")
    assert dataset_signature(a, "f") != dataset_signature(coda, "f")


def test_la_firma_distingue_un_cambio_di_TIPO():
    # I dtype entrano nella firma proprio per stringere il campionamento: una
    # colonna che cambia tipo è un dataset diverso anche a valori identici.
    a = pd.DataFrame({"x": range(1000)})
    b = a.copy()
    b["x"] = b["x"].astype(float)
    assert dataset_signature(a, "f") != dataset_signature(b, "f")


def test_limite_noto_e_dichiarato_del_campionamento():
    """
    Il centro del dataset NON entra nella firma: è il prezzo dichiarato per
    renderla O(1) su un percorso che gira a ogni interazione.

    Il test esiste per fissare il compromesso, non per approvarlo: se un domani
    si volesse una firma esatta, questo test diventa rosso e obbliga a decidere
    consapevolmente invece di scoprirlo per caso.
    """
    a = pd.DataFrame({"x": range(5000)})
    centro = a.copy()
    centro.loc[2500, "x"] = 99999
    assert dataset_signature(a, "f") == dataset_signature(centro, "f")


def test_la_firma_e_indipendente_dalla_dimensione(sales_df: pd.DataFrame):
    # Non una misura di tempo (fragile in CI) ma della proprietà che la causa:
    # si hashano al più 400 righe, quante che ne abbia il dataset.
    import nlda.loader as mod
    grande = pd.concat([sales_df] * 2000, ignore_index=True)
    chiamate = []
    originale = pd.util.hash_pandas_object
    mod.pd.util.hash_pandas_object = lambda obj, **kw: chiamate.append(len(obj)) or originale(obj, **kw)
    try:
        dataset_signature(grande, "f")
    finally:
        mod.pd.util.hash_pandas_object = originale
    assert chiamate and chiamate[0] <= 2 * mod._RIGHE_CAMPIONE, chiamate


def test_con_una_sola_categoria_non_si_dice_che_pesa_il_100_percento():
    """
    Filtrando la pagina su una categoria l'insight diventava "East da solo pesa
    il 100% del totale di Sales": non un'osservazione ma una tautologia, e in
    cima all'elenco faceva dubitare anche degli altri.
    """
    df = pd.DataFrame({"Regione": ["East"] * 10, "Vendite": range(10, 20)})
    findings = analyze(df, "Vendite", "Regione").get("findings", [])
    assert not any("100" in f and "pesa" in f for f in findings)


def test_con_piu_categorie_la_quota_del_leader_resta():
    df = pd.DataFrame({"Regione": ["East", "West"] * 10,
                       "Vendite": [30, 10] * 10})
    findings = analyze(df, "Vendite", "Regione").get("findings", [])
    assert any("pesa" in f and "East" in f for f in findings)


# --- Numeri all'europea: la virgola decimale è la prova ------------------------
def test_un_csv_europeo_produce_una_MISURA_non_del_testo():
    """
    Il difetto: `1234,56` restava testo, quindi un CSV europeo arrivava al report
    SENZA misure — nessun KPI, nessuna classifica — mentre la chat, dove a
    leggere è il modello, il totale lo calcolava. Due verità nella stessa pagina.
    """
    dati = (b"Regione;Fatturato\nNord;1234,56\nSud;987,10\nCentro;10.500,00\n")
    df = read_any(_upload(dati, "eu.csv"))
    assert "Fatturato" in measure_columns(df)
    assert df["Fatturato"].sum() == pytest.approx(12721.66)


def test_i_punti_da_soli_NON_bastano_a_dichiarare_un_numero_europeo():
    """
    `1.234` può essere milleduecentotrentaquattro, uno-virgola-duecento… o un
    codice: senza una virgola decimale da qualche parte nella colonna non c'è
    prova, e il progetto preferisce il testo onesto al numero sbagliato. Qui la
    colonna NON deve essere trattata come europea (la converte semmai pandas,
    con la convenzione americana, che è un'altra decisione e un altro modulo).
    """
    campione = pd.Series(["1.234", "5.678", "9.012"])
    assert not _e_una_colonna_europea(campione)


def test_una_colonna_di_date_non_diventa_un_numero_europeo():
    """La guardia opposta: `01/02/2024` non ha nulla di numerico da estrarre."""
    assert not _e_una_colonna_europea(pd.Series(["01/02/2024", "15/03/2024"]))


# --- Due colonne con lo stesso nome -------------------------------------------
def test_due_colonne_omonime_vengono_dichiarate():
    """
    Pandas non fallisce e non avvisa: rinomina la seconda in `Vendite.1`. Chi ha
    caricato il file vede due misure dove ne aveva una, e "il totale delle
    vendite" ha due risposte diverse senza che nessuno lo dica.
    """
    avviso = duplicate_columns_warning(b"Vendite,Vendite,Regione\n10,20,Nord\n", "x.csv")
    assert avviso and "«Vendite»" in avviso
    assert "Vendite.1" in avviso, "va detto COME si chiama adesso la seconda"


def test_un_file_con_nomi_tutti_diversi_non_avvisa():
    """Il rischio di un avviso nuovo è il falso positivo sul caso normale."""
    assert duplicate_columns_warning(b"Regione,Vendite\nNord,10\n", "x.csv") is None


def test_l_avviso_sulle_omonime_riconosce_il_separatore():
    """Un CSV europeo usa `;`: cercare le virgole troverebbe una colonna sola."""
    avviso = duplicate_columns_warning(b"Vendite;Vendite;Regione\n10;20;Nord\n", "x.csv")
    assert avviso and "«Vendite»" in avviso


def test_un_file_oltre_il_tetto_di_upload_viene_rifiutato_dal_CARICATORE(monkeypatch):
    """
    Il tetto lo imponeva solo la rotta dell'API; l'app Streamlit si affidava a
    `maxUploadSize` del suo config.toml, e `MAX_UPLOAD_MB` non la raggiungeva.
    Con i default coincidenti non si vedeva: bastava abbassare la variabile
    perche' l'interfaccia promettesse un limite che nessuno imponeva.
    """
    monkeypatch.setattr("nlda.loader.settings", Settings(max_upload_mb=1))
    grande = b"a,b\n" + b"1,2\n" * 400_000      # ~1,6 MB
    with pytest.raises(ValueError, match="oltre il limite"):
        read_any(_upload(grande, "grande.csv"))


def test_un_file_sotto_il_tetto_passa(monkeypatch):
    monkeypatch.setattr("nlda.loader.settings", Settings(max_upload_mb=1))
    assert len(read_any(_upload(b"a,b\n1,2\n3,4\n", "piccolo.csv"))) == 2


def test_il_profilo_non_produce_righe_da_trecento_caratteri():
    """
    Con `inf` e `1e308` fra i valori, il dettaglio della colonna diventava lungo
    339 caratteri e sfondava la tabella della struttura. I numeri del profilo
    passano ora da `fmt_num`, come ovunque nell'app — cosa che sistema anche
    un'incoerenza: erano l'unico posto in cui i decimali si leggevano col punto
    americano mentre i KPI accanto usavano la virgola.
    """
    df = pd.DataFrame({"Vendite": [1e308, -1e308, float("inf"), 1.0]})
    dettaglio = profile(df)["Dettaglio"].iat[0]
    assert len(dettaglio) < 60, dettaglio
    assert "e+308" in dettaglio and "∞" in dettaglio


def test_il_profilo_scrive_i_numeri_all_italiana():
    """
    Virgola sui decimali e punto sulle migliaia, come i KPI accanto. Sopra i
    mille i decimali cadono: e' la convenzione di `fmt_num` in tutta l'app, e un
    profilo che facesse eccezione sarebbe la seconda convenzione da ricordare.
    """
    dettaglio = profile(pd.DataFrame({"Vendite": [230.77, 1500.0]}))["Dettaglio"].iat[0]
    assert "230,77" in dettaglio          # decimali con la virgola
    assert "1.500" in dettaglio           # migliaia con il punto
