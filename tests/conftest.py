import pytest

from llm import explain
from llm.client import ApiSettings


@pytest.fixture(autouse=True)
def disable_live_explanation_api(monkeypatch):
    monkeypatch.setattr(explain, "read_settings", lambda: ApiSettings("", ""))
