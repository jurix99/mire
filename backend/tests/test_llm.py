"""Client Gemini : bascule sur le modèle de secours en cas de surcharge (SDK simulé, aucun appel réseau)."""

from types import SimpleNamespace

import pytest
from google.genai import errors

from app.llm import GeminiClient, LLMError


class FakeModels:
    def __init__(self, failing: set[str]):
        self.failing = failing
        self.calls: list[str] = []

    def generate_content(self, model, contents, config):
        self.calls.append(model)
        if model in self.failing:
            raise errors.ServerError(503, {"error": {"code": 503, "message": "overloaded", "status": "UNAVAILABLE"}})
        return SimpleNamespace(function_calls=None, text="ok", candidates=[])


def client(failing):
    c = GeminiClient("fake-key", model="main", fallback="backup")
    c.client = SimpleNamespace(models=FakeModels(failing))
    return c


def test_fallback_when_main_model_overloaded():
    c = client({"main"})
    result = c.chat("system", [{"role": "user", "text": "salut"}], [], lambda n, a: {})
    assert result.text == "ok"
    assert c.client.models.calls == ["main", "backup"]


def test_error_when_all_models_overloaded():
    c = client({"main", "backup"})
    with pytest.raises(LLMError):
        c.chat("system", [{"role": "user", "text": "salut"}], [], lambda n, a: {})


def test_profile_schema_sent_to_gemini_is_simple():
    import json

    from app.llm import PROFILE_SCHEMA

    text = json.dumps(PROFILE_SCHEMA)
    for key in ("$ref", "$defs", "maxLength", "maxItems", "minLength", "title"):
        assert f'"{key}"' not in text
    assert PROFILE_SCHEMA["properties"]["skills"]["items"]["properties"]["kind"]["enum"] == ["fort", "a_developper"]
