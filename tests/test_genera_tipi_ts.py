"""
Test del generatore dei tipi TypeScript.

Il file generato e' committato, quindi puo' andare fuori sincrono con l'API senza
che nessuno se ne accorga finche' qualcuno non compila il frontend. Qui si
verifica proprio quello: che il file nel repository descriva l'API di ADESSO.

Gli altri test coprono la traduzione dei costrutti che lo schema usa davvero —
sono le regole che si romperebbero aggiungendo un campo di un tipo nuovo.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))

from genera_tipi_ts import DESTINAZIONE, genera, interfaccia, tipo_ts  # noqa: E402


# --- Traduzione dei tipi ------------------------------------------------------
@pytest.mark.parametrize("schema, atteso", [
    ({"type": "string"}, "string"),
    ({"type": "integer"}, "number"),
    ({"type": "number"}, "number"),
    ({"type": "boolean"}, "boolean"),
    ({"type": "array", "items": {"type": "string"}}, "string[]"),
    ({"$ref": "#/components/schemas/Kpi"}, "Kpi"),
    ({"enum": ["scalar", "table"]}, '"scalar" | "table"'),
    ({"type": "object"}, "Record<string, unknown>"),
    ({"type": "object", "additionalProperties": {"type": "number"}}, "Record<string, number>"),
])
def test_tipo_ts(schema, atteso):
    assert tipo_ts(schema) == atteso


def test_optional_diventa_una_union_con_null():
    """`str | None` di Pydantic arriva come anyOf: deve restare esplicito in TS."""
    assert tipo_ts({"anyOf": [{"type": "string"}, {"type": "null"}]}) == "string | null"


def test_un_campo_senza_tipo_non_diventa_any():
    """`unknown` obbliga chi legge a restringere il tipo; `any` spegne il compilatore."""
    assert tipo_ts({}) == "unknown"


def test_un_array_di_union_e_parentesizzato():
    """Senza parentesi `A | B[]` significherebbe tutt'altro."""
    reso = tipo_ts({"type": "array",
                    "items": {"anyOf": [{"type": "string"}, {"type": "null"}]}})
    assert reso == "(string | null)[]"


# --- Interfacce ---------------------------------------------------------------
def test_i_campi_non_obbligatori_sono_marcati_opzionali():
    reso = interfaccia("Prova", {
        "properties": {"a": {"type": "string"}, "b": {"type": "integer"}},
        "required": ["a"],
    })
    assert "  a: string;" in reso
    assert "  b?: number;" in reso


def test_la_descrizione_diventa_un_commento():
    reso = interfaccia("Prova", {
        "properties": {"a": {"type": "string", "description": "che cos'e'"}},
        "required": ["a"],
    })
    assert "/** che cos'e' */" in reso


def test_una_descrizione_su_piu_righe_resta_jsdoc_valido():
    """Senza gli asterischi l'editor non la mostra nel tooltip."""
    reso = interfaccia("Prova", {
        "description": "prima riga\nseconda riga",
        "properties": {},
    })
    assert "/**\n * prima riga\n * seconda riga\n */" in reso


# --- Allineamento con l'API ---------------------------------------------------
def test_i_tipi_committati_sono_allineati_all_api():
    """
    Se questo test e' rosso, qualcuno ha cambiato un modello Pydantic senza
    rigenerare i tipi: `python scripts/genera_tipi_ts.py`.
    """
    assert DESTINAZIONE.exists(), "i tipi TypeScript non sono stati generati"
    assert DESTINAZIONE.read_text(encoding="utf-8") == genera(), (
        "frontend/src/api/types.ts non descrive l'API attuale — rigeneralo")


def test_le_interfacce_che_servono_al_frontend_ci_sono():
    reso = genera()
    for nome in ["DatasetResponse", "ReportResponse", "AskRequest", "AskResponse",
                 "ProjectQaResponse", "ConfigResponse", "ErrorResponse", "Kpi"]:
        assert f"export interface {nome} " in reso


def test_il_modello_di_upload_generato_da_fastapi_e_escluso():
    """Dichiarerebbe `file: string`, che dal browser e' falso: si manda un File."""
    assert "Body_" not in genera()
