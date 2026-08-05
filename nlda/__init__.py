"""
Natural Language Data Analyst.

Qui vive la versione del progetto, e vive QUI perché è l'unico posto che tutti
possono leggere: `pyproject` la prende da questo file (versione dinamica di
hatchling), l'API la dichiara nello schema OpenAPI importandola, e chi legge il
sorgente la trova dove se l'aspetta.

Prima erano due copie — una in `pyproject`, una scritta a mano in `api/app.py` —
e due copie di un numero che deve cambiare insieme sono due numeri che prima o
poi divergono. Non si legge dai metadati del pacchetto installato
(`importlib.metadata`) perché nel container l'app gira dal SORGENTE con il
pacchetto disinstallato: lì quella lettura fallirebbe.
"""
__version__ = "2.3.0"
