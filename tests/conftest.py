"""Zero-network pytest fixtúrák — se élő Supabase / Anthropic / Muapi / Telegram, se net.

A projekt minden külső kliense lustán (@lru_cache) épül, ezért a tiszta-logika modulok
credential nélkül importálhatók. Ahol mégis kell kliens/válasz, itt adunk in-memory
hamisítványt. Egyetlen új függőség sincs — csak a stdlib unittest.mock.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

# A repo gyökér a sys.path-on (hogy a `src` csomag akkor is importálható legyen, ha a
# pytest nem `-m`-mel indul). A pyproject testpaths=["tests"], így ez a conftest gyökér.
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Sentinel (HAMIS) credentialök. Egyetlen valódi titok sincs köztük — azért léteznek, hogy
# (a) a lazy kliensek / Settings SOHA ne bukjanak creds-hiányon import közben, és
# (b) a lenti autouse fixture ezekkel FELÜLÍRJon minden valódi kulcsot a teszt idejére.
_TEST_ENV = {
    "ANTHROPIC_API_KEY": "test-anthropic-key",
    "SUPABASE_URL": "https://test.supabase.co",
    "SUPABASE_KEY": "test-anon-key",
    "SUPABASE_SERVICE_KEY": "test-service-key",
    "TELEGRAM_BOT_TOKEN": "123456:test-telegram-token",
    "MUAPI_API_KEY": "test-muapi-key",
    "OPENROUTER_API_KEY": "test-openrouter-key",
}

# Import-idejű padló: a hiányzó kulcsokat kitöltjük, hogy a modul-importok (lazy kliensek,
# Settings) SOSE bukjanak creds-hiányon. Csak setdefault — a valódi scrubot a fixture végzi.
for _k, _v in _TEST_ENV.items():
    os.environ.setdefault(_k, _v)


@pytest.fixture(autouse=True)
def _isolate_secret_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """MINDEN tesztet (autouse) leválaszt a valódi credentialökről.

    Kényszerítve HAMIS sentinel-értékre állít minden titkot, FELÜLÍRVA a fejlesztő gépén
    esetleg .env-ből betöltött valódi kulcsot — így éles titok a suite-on belül SEM olvasható
    ki (és hálózatra sem szivároghat). A monkeypatch a teszt után visszaállítja a környezetet.
    """
    for _k, _v in _TEST_ENV.items():
        monkeypatch.setenv(_k, _v)


from src.core.storage.models import FeedItem  # noqa: E402  (a sys.path/env beállítás UTÁN)


# ── FeedItem gyár ──────────────────────────────────────────────────────
@pytest.fixture
def make_feed_item():
    """FeedItem gyár: csak a kötelező mezőket adja, minden felülírható kwarg-gal."""

    def _make(**overrides) -> FeedItem:
        base: dict = dict(
            id="testid0000000001",
            source_name="TestSource",
            source_priority=1,
            url="https://example.com/post",
            title="Test title",
            content="Test content body",
            score=None,
        )
        base.update(overrides)
        return FeedItem(**base)

    return _make


# ── Hamis Anthropic üzenet (messages.create() válasz alakja) ───────────
class FakeAnthropicMessage:
    """A .content[0].text + .stop_reason minimál mása (sync és async klienshez is jó)."""

    def __init__(self, text: str, stop_reason: str = "end_turn"):
        self.content = [type("Block", (), {"text": text})()]
        self.stop_reason = stop_reason


@pytest.fixture
def anthropic_message():
    """A FakeAnthropicMessage osztályt adja vissza (a teszt maga példányosít)."""
    return FakeAnthropicMessage


# ── Hamis Supabase kliens (fluent lánc, in-memory) ─────────────────────
class _FluentQuery:
    """table().select().eq()....execute() lánc — minden köztes hívás önmagát adja vissza,
    az execute() egy .data attribútumú válasz-objektumot ad (a teszt által megadott sorokkal)."""

    def __init__(self, data: list | None = None):
        self._data = data if data is not None else []

    def __getattr__(self, _name):
        def _method(*_a, **_k):
            return self

        return _method

    def execute(self):
        return type("Resp", (), {"data": list(self._data)})()


@pytest.fixture
def fake_supabase():
    """Gyár: fake_supabase([{...rows}]) -> a modulok `client=` paraméterébe injektálható."""

    def _make(data: list | None = None) -> _FluentQuery:
        return _FluentQuery(data)

    return _make
