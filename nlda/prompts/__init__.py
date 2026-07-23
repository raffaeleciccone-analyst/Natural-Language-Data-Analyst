"""
Prompt versionati fuori dal codice.

Ogni file .md contiene ESATTAMENTE il testo di un system prompt: è ciò che arriva
al modello, e il golden (`tests/test_prompt_contract.py`) lo confronta carattere
per carattere. Tenere i prompt qui, come testo, li rende leggibili e diffabili
senza scavare in una f-string in mezzo al codice — ma non tocca il contratto: se
un editor aggiunge un a-capo o cambia una parola, il golden diventa rosso. È la
rete che impedisce di degradarli in silenzio.

I file NON vengono normalizzati: `read_text` con universal-newline riporta comunque
CRLF a `\\n` in lettura, quindi il testo a runtime è indipendente da come git ha
scritto i fine-riga su disco.

`load` restituisce il testo grezzo; `render` sostituisce i segnaposto `$nome` con
`string.Template` — scelto al posto di `str.format` perché nel prompt di
generazione compare la graffa letterale `{perc:.1f}`, che con `format` andrebbe
raddoppiata (fonte di errori silenziosi).
"""
from pathlib import Path
from string import Template

_DIR = Path(__file__).parent


def load(name: str) -> str:
    """Testo grezzo del prompt in `prompts/<name>.md`."""
    return (_DIR / f"{name}.md").read_text(encoding="utf-8")


def render(name: str, /, **variables: str) -> str:
    """Come `load`, ma sostituisce i segnaposto `$var`. Solleva `KeyError` se ne manca uno."""
    return Template(load(name)).substitute(variables)
