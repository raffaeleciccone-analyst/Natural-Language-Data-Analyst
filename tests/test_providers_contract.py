"""
Test di CONTRATTO sui provider concreti: `_call()` senza rete e senza API key.

Perché esistono: i moduli `nlda/providers/*_provider.py` sono l'unico punto in cui
il progetto tocca gli SDK esterni, e sono anche gli unici che nessun altro test
esegue. Un aggiornamento di major (openai 2.x -> 3.x, google-genai 1.x -> 2.x) che
rinomina o rimuove un parametro passerebbe lint, type-check e suite senza che nulla
diventi rosso: l'app si romperebbe alla prima domanda reale dell'utente.

Come sono costruiti, in tre passi per ogni provider:
1. il client dell'SDK è sostituito da un finto che CATTURA gli argomenti;
2. si verifica il parsing della risposta (forma realistica, non la rete);
3. si verifica che gli argomenti catturati siano accettabili dalla firma REALE
   dell'SDK installato, via `inspect.signature(...).bind_partial(**kwargs)`.

È il passo 3 a dare valore: se un major cambia la firma, il bind solleva e il test
diventa rosso a costo zero (nessuna chiave, nessuna chiamata, nessun secondo di CI).

`pytest.importorskip` è solo una rete di sicurezza per chi lavora con un ambiente
parziale: in CI il job installa l'extra `all`, quindi qui non si salta mai nulla.
"""
import inspect
from types import SimpleNamespace

import pytest

from nlda.config import Settings

# Timeout non-default: se il provider lo prendesse da un'altra parte (o non lo
# passasse affatto) l'asserzione sul valore catturato se ne accorgerebbe.
TIMEOUT_FINTO = 7.5


def assert_firma_compatibile(func, kwargs: dict, cosa: str) -> None:
    """
    Verifica che `kwargs` sia legale per la firma REALE di `func`.

    `bind_partial` non chiama nulla: controlla solo che ogni nome esista tra i
    parametri (o sia assorbito da un **kwargs esplicito). È il rilevatore di
    rotture da major degli SDK: costa microsecondi e non richiede credenziali.
    """
    try:
        inspect.signature(func).bind_partial(**kwargs)
    except TypeError as e:
        pytest.fail(
            f"{cosa}: gli argomenti passati non sono compatibili con la firma "
            f"dell'SDK installato ({e}). Probabile aggiornamento di major da gestire."
        )


# ---------------------------------------------------------------------------
# OpenAI
# ---------------------------------------------------------------------------
def _fake_openai(catturato: dict, contenuto):
    """Fabbrica un finto `openai.OpenAI` che registra ctor e argomenti di create()."""
    def create(**kwargs):
        catturato["create"] = kwargs
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=contenuto))]
        )

    def OpenAI(**kwargs):  # noqa: N802 — imita il nome della classe dell'SDK
        catturato["ctor"] = kwargs
        return SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create))
        )

    return OpenAI


def _openai_provider(monkeypatch, catturato, contenuto="df['Sales'].sum()", cls=None,
                     api_key="chiave-di-test"):
    """Esegue `_call()` su OpenAIProvider (o una sua sottoclasse) con SDK finto."""
    openai = pytest.importorskip("openai")
    import nlda.providers.openai_provider as mod

    if cls is None:
        cls = mod.OpenAIProvider
    monkeypatch.setattr(openai, "OpenAI", _fake_openai(catturato, contenuto))
    monkeypatch.setattr(mod, "settings", Settings(request_timeout=TIMEOUT_FINTO))

    provider = cls(model_name="gpt-4o-mini", temperature=0.25, api_key=api_key)
    return provider._call("istruzioni di sistema", "domanda dell'utente")


def test_openai_costruisce_il_client_con_chiave_e_timeout(monkeypatch):
    catturato: dict = {}
    _openai_provider(monkeypatch, catturato)

    assert catturato["ctor"] == {"timeout": TIMEOUT_FINTO, "api_key": "chiave-di-test"}
    # base_url resta fuori per OpenAI: l'endpoint è quello di default dell'SDK.
    assert "base_url" not in catturato["ctor"]


def test_openai_ctor_compatibile_con_la_firma_dellsdk(monkeypatch):
    openai = pytest.importorskip("openai")
    catturato: dict = {}
    _openai_provider(monkeypatch, catturato)
    assert_firma_compatibile(openai.OpenAI.__init__, catturato["ctor"], "openai.OpenAI()")


def test_openai_invia_i_due_messaggi_e_la_temperatura(monkeypatch):
    catturato: dict = {}
    _openai_provider(monkeypatch, catturato)

    create = catturato["create"]
    assert create["model"] == "gpt-4o-mini"
    assert create["temperature"] == 0.25
    assert create["messages"] == [
        {"role": "system", "content": "istruzioni di sistema"},
        {"role": "user", "content": "domanda dell'utente"},
    ]


def test_openai_create_compatibile_con_la_firma_dellsdk(monkeypatch):
    pytest.importorskip("openai")
    from openai.resources.chat.completions import Completions

    catturato: dict = {}
    _openai_provider(monkeypatch, catturato)
    assert_firma_compatibile(Completions.create, catturato["create"],
                             "client.chat.completions.create()")


def test_openai_legge_il_contenuto_del_primo_choice(monkeypatch):
    testo = _openai_provider(monkeypatch, {}, contenuto="df['Sales'].sum()")
    assert testo == "df['Sales'].sum()"


def test_openai_contenuto_nullo_diventa_stringa_vuota(monkeypatch):
    # L'API può restituire content=None (es. risposta troncata o filtrata): il
    # chiamante si aspetta comunque una stringa, non un None da propagare.
    assert _openai_provider(monkeypatch, {}, contenuto=None) == ""


def test_openai_cattura_i_token_da_usage(monkeypatch):
    # Metrica di osservabilità: input e output dell'SDK, separati, in _last_usage.
    openai = pytest.importorskip("openai")
    import nlda.providers.openai_provider as mod

    def create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="x"))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=23),
        )

    def OpenAI(**kwargs):  # noqa: N802 — imita il nome della classe dell'SDK
        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    monkeypatch.setattr(openai, "OpenAI", OpenAI)
    monkeypatch.setattr(mod, "settings", Settings(request_timeout=TIMEOUT_FINTO))
    provider = mod.OpenAIProvider(model_name="gpt-4o-mini", api_key="k")
    provider._call("s", "u")
    assert provider._last_usage.input_tokens == 100
    assert provider._last_usage.output_tokens == 23
    assert provider._last_usage.total_tokens == 123


def test_openai_senza_chiave_non_passa_api_key(monkeypatch):
    # Senza chiave esplicita né variabile d'ambiente il client deve risolversi da
    # solo: passare api_key=None sovrascriverebbe la risoluzione dell'SDK.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    catturato: dict = {}
    _openai_provider(monkeypatch, catturato, api_key=None)
    assert "api_key" not in catturato["ctor"]


# ---------------------------------------------------------------------------
# Groq (riusa `_call` di OpenAI: l'unica ragione della classe è la base_url)
# ---------------------------------------------------------------------------
def test_groq_punta_alla_base_url_di_groq(monkeypatch):
    import nlda.providers.openai_provider as mod
    from nlda.providers.groq_provider import GroqProvider

    catturato: dict = {}
    _openai_provider(monkeypatch, catturato, cls=GroqProvider)

    assert catturato["ctor"]["base_url"] == "https://api.groq.com/openai/v1"
    # E resta una vera sottoclasse: se qualcuno duplicasse `_call` il test lo dice.
    assert GroqProvider._call is mod.OpenAIProvider._call


def test_groq_legge_la_chiave_dalla_propria_variabile(monkeypatch):
    from nlda.providers.groq_provider import GroqProvider

    monkeypatch.setenv("GROQ_API_KEY", "gsk-di-test")
    monkeypatch.setenv("OPENAI_API_KEY", "non-questa")
    assert GroqProvider(model_name="llama-3.3-70b-versatile").api_key == "gsk-di-test"


# ---------------------------------------------------------------------------
# Anthropic
# ---------------------------------------------------------------------------
def _fake_anthropic(catturato: dict, blocchi):
    def create(**kwargs):
        catturato["create"] = kwargs
        return SimpleNamespace(content=blocchi)

    def Anthropic(**kwargs):  # noqa: N802 — imita il nome della classe dell'SDK
        catturato["ctor"] = kwargs
        return SimpleNamespace(messages=SimpleNamespace(create=create))

    return Anthropic


def _blocco(tipo: str, testo: str):
    """Blocco di risposta Anthropic nella forma minima usata dal provider."""
    return SimpleNamespace(type=tipo, text=testo)


def _anthropic_call(monkeypatch, catturato, blocchi, api_key="sk-ant-di-test"):
    anthropic = pytest.importorskip("anthropic")
    import nlda.providers.anthropic_provider as mod

    monkeypatch.setattr(anthropic, "Anthropic", _fake_anthropic(catturato, blocchi))
    monkeypatch.setattr(mod, "settings", Settings(request_timeout=TIMEOUT_FINTO))

    provider = mod.AnthropicProvider(model_name="claude-opus-4-8", api_key=api_key)
    return provider._call("istruzioni di sistema", "domanda dell'utente")


def test_anthropic_passa_system_messages_e_timeout(monkeypatch):
    catturato: dict = {}
    _anthropic_call(monkeypatch, catturato, [_blocco("text", "ok")])

    create = catturato["create"]
    assert create["model"] == "claude-opus-4-8"
    assert create["system"] == "istruzioni di sistema"
    assert create["messages"] == [{"role": "user", "content": "domanda dell'utente"}]
    assert create["timeout"] == TIMEOUT_FINTO
    assert isinstance(create["max_tokens"], int) and create["max_tokens"] > 0
    # I parametri di sampling sono esclusi di proposito (400 su alcuni modelli).
    assert "temperature" not in create


def test_anthropic_create_compatibile_con_la_firma_dellsdk(monkeypatch):
    pytest.importorskip("anthropic")
    from anthropic.resources.messages import Messages

    catturato: dict = {}
    _anthropic_call(monkeypatch, catturato, [_blocco("text", "ok")])
    assert_firma_compatibile(Messages.create, catturato["create"], "client.messages.create()")


def test_anthropic_concatena_solo_i_blocchi_di_testo(monkeypatch):
    # Una risposta può contenere blocchi non testuali (thinking, tool_use): finirebbero
    # nel codice generato e lo renderebbero non eseguibile.
    blocchi = [
        _blocco("text", "df["),
        SimpleNamespace(type="tool_use", text="NON DEVE COMPARIRE"),
        _blocco("text", "'Sales'].sum()"),
    ]
    assert _anthropic_call(monkeypatch, {}, blocchi) == "df['Sales'].sum()"


def test_anthropic_senza_chiave_lascia_risolvere_lsdk(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    catturato: dict = {}
    _anthropic_call(monkeypatch, catturato, [_blocco("text", "ok")], api_key=None)
    assert catturato["ctor"] == {}  # costruttore chiamato senza argomenti


# ---------------------------------------------------------------------------
# Gemini (SDK google-genai)
# ---------------------------------------------------------------------------
def _gemini_call(monkeypatch, catturato, testo="df['Sales'].sum()", api_key="AIza-di-test"):
    pytest.importorskip("google.genai")
    from google import genai

    import nlda.providers.gemini_provider as mod

    def generate_content(**kwargs):
        catturato["generate"] = kwargs
        return SimpleNamespace(text=testo)

    def Client(**kwargs):  # noqa: N802 — imita il nome della classe dell'SDK
        catturato["ctor"] = kwargs
        return SimpleNamespace(models=SimpleNamespace(generate_content=generate_content))

    monkeypatch.setattr(genai, "Client", Client)
    monkeypatch.setattr(mod, "settings", Settings(request_timeout=TIMEOUT_FINTO))

    provider = mod.GeminiProvider(model_name="gemini-2.0-flash", temperature=0.25,
                                  api_key=api_key)
    return provider._call("istruzioni di sistema", "domanda dell'utente")


def test_gemini_timeout_convertito_in_millisecondi(monkeypatch):
    # L'SDK google-genai vuole millisecondi: passare i secondi darebbe un timeout
    # 1000 volte più corto, cioè chiamate che scadono sempre.
    catturato: dict = {}
    _gemini_call(monkeypatch, catturato)
    assert catturato["ctor"]["http_options"].timeout == int(TIMEOUT_FINTO * 1000)


def test_gemini_client_compatibile_con_la_firma_dellsdk(monkeypatch):
    pytest.importorskip("google.genai")
    from google import genai

    catturato: dict = {}
    _gemini_call(monkeypatch, catturato)
    assert set(catturato["ctor"]) == {"api_key", "http_options"}
    assert_firma_compatibile(genai.Client.__init__, catturato["ctor"], "genai.Client()")


def test_gemini_passa_prompt_di_sistema_e_temperatura_nella_config(monkeypatch):
    catturato: dict = {}
    _gemini_call(monkeypatch, catturato)

    gen = catturato["generate"]
    assert gen["model"] == "gemini-2.0-flash"
    assert gen["contents"] == "domanda dell'utente"
    # La config è costruita con i tipi VERI dell'SDK: se un campo sparisse, la
    # costruzione fallirebbe già prima di arrivare qui.
    assert gen["config"].system_instruction == "istruzioni di sistema"
    assert gen["config"].temperature == 0.25


def test_gemini_generate_content_compatibile_con_la_firma_dellsdk(monkeypatch):
    pytest.importorskip("google.genai")
    from google.genai.models import Models

    catturato: dict = {}
    _gemini_call(monkeypatch, catturato)
    assert_firma_compatibile(Models.generate_content, catturato["generate"],
                             "client.models.generate_content()")


def test_gemini_testo_nullo_diventa_stringa_vuota(monkeypatch):
    # `response.text` è None quando la risposta viene bloccata dai filtri.
    assert _gemini_call(monkeypatch, {}, testo=None) == ""


def test_gemini_senza_chiave_non_passa_api_key(monkeypatch):
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    catturato: dict = {}
    _gemini_call(monkeypatch, catturato, api_key=None)
    assert set(catturato["ctor"]) == {"http_options"}


# ---------------------------------------------------------------------------
# Ollama (locale, nessuna chiave)
# ---------------------------------------------------------------------------
def _ollama_call(monkeypatch, catturato, risposta=None):
    ollama = pytest.importorskip("ollama")
    from nlda.providers.ollama_provider import OllamaProvider

    def chat(**kwargs):
        catturato["chat"] = kwargs
        return risposta if risposta is not None else {"message": {"content": "df.sum()"}}

    monkeypatch.setattr(ollama, "chat", chat)
    provider = OllamaProvider(model_name="qwen2.5:3b", temperature=0.25)
    return provider._call("istruzioni di sistema", "domanda dell'utente")


def test_ollama_passa_modello_messaggi_e_opzioni(monkeypatch):
    catturato: dict = {}
    _ollama_call(monkeypatch, catturato)

    chat = catturato["chat"]
    assert chat["model"] == "qwen2.5:3b"
    assert chat["messages"] == [
        {"role": "system", "content": "istruzioni di sistema"},
        {"role": "user", "content": "domanda dell'utente"},
    ]
    # La temperatura va dentro `options`, non come argomento di primo livello.
    assert chat["options"] == {"temperature": 0.25}


def test_ollama_chat_compatibile_con_la_firma_dellsdk(monkeypatch):
    ollama = pytest.importorskip("ollama")
    catturato: dict = {}
    _ollama_call(monkeypatch, catturato)
    assert_firma_compatibile(ollama.chat, catturato["chat"], "ollama.chat()")


def test_ollama_legge_il_contenuto_del_messaggio(monkeypatch):
    testo = _ollama_call(monkeypatch, {}, risposta={"message": {"content": "df['a'].mean()"}})
    assert testo == "df['a'].mean()"


def test_ollama_cattura_i_token_di_prompt_e_generazione(monkeypatch):
    # Ollama riporta i due conteggi separati: input=prompt, output=generazione.
    from nlda.providers.ollama_provider import OllamaProvider

    ollama = pytest.importorskip("ollama")
    monkeypatch.setattr(ollama, "chat", lambda **kw: {
        "message": {"content": "x"}, "prompt_eval_count": 30, "eval_count": 12})
    provider = OllamaProvider(model_name="qwen2.5:3b")
    provider._call("s", "u")
    assert provider._last_usage.input_tokens == 30
    assert provider._last_usage.output_tokens == 12
    assert provider._last_usage.total_tokens == 42
