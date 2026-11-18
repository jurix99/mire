"""Client Gemini : modèles de secours, erreurs lisibles, schéma (SDK simulé, aucun appel réseau)."""

import json
from types import SimpleNamespace

import pytest
from google.genai import errors

import app.llm as llm_module
from app.llm import PROFILE_SCHEMA, GeminiClient, LLMError

HELLO = [{"role": "user", "text": "salut"}]


class FakeModels:
    def __init__(self, failing: dict[str, int]):
        self.failing = failing  # modèle -> code d'erreur HTTP
        self.calls: list[str] = []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        if code := self.failing.get(model):
            cls = errors.ClientError if code < 500 else errors.ServerError
            raise cls(code, {"error": {"code": code, "message": "indisponible", "status": "X"}})
        return SimpleNamespace(function_calls=None, text="{}", candidates=[])


def client(failing):
    c = GeminiClient("fake-key", model="main", fallbacks=["backup", "backup2"])
    c.client = SimpleNamespace(models=FakeModels(failing))
    return c


def test_fallback_chain_when_models_overloaded():
    c = client({"main": 429, "backup": 503})
    assert c.chat("system", HELLO, [], lambda n, a: {}).text == "{}"
    assert c.client.models.calls == ["main", "backup", "backup2"]
    # Le modèle hors quota est ensuite ignoré directement pendant quelques minutes
    c.client.models.calls.clear()
    c.client.models.failing = {"backup": 503}
    c.chat("system", HELLO, [], lambda n, a: {})
    assert c.client.models.calls == ["backup", "backup2"]


def test_readable_errors_when_all_models_fail():
    with pytest.raises(LLMError) as e:
        client({"main": 503, "backup": 503, "backup2": 504}).chat("s", HELLO, [], lambda n, a: {})
    assert "surchargé" in e.value.detail
    with pytest.raises(LLMError) as e:
        client({"main": 429, "backup": 429, "backup2": 429}).chat("s", HELLO, [], lambda n, a: {})
    assert "Quota" in e.value.detail


def test_extract_profile_retries_a_second_round(monkeypatch):
    monkeypatch.setattr(llm_module.time, "sleep", lambda s: None)
    c = client({"main": 503, "backup": 503, "backup2": 503})
    models = c.client.models
    original = models.generate_content

    def flaky(model, contents, config):
        if len(models.calls) >= 3:  # tout se libère au deuxième passage
            models.failing = {}
        return original(model, contents, config)

    models.generate_content = flaky
    assert c.extract_profile("cv").skills == []
    assert models.calls == ["main", "backup", "backup2", "main"]


def test_profile_schema_sent_to_gemini_is_simple():
    text = json.dumps(PROFILE_SCHEMA)
    for key in ("$ref", "$defs", "maxLength", "maxItems", "minLength"):
        assert f'"{key}"' not in text
    assert "title" not in PROFILE_SCHEMA  # métadonnée retirée…
    assert "title" not in PROFILE_SCHEMA["properties"]["skills"]["items"]
    assert PROFILE_SCHEMA["properties"]["skills"]["items"]["properties"]["kind"]["enum"] == ["fort", "a_developper"]
    # Un champ qui s'appelle « title » (intitulé du poste) ne doit pas être confondu avec la métadonnée
    assert "title" in PROFILE_SCHEMA["properties"]["experiences"]["items"]["properties"]
