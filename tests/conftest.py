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

# Dummy env — hogy a lazy kliensek / Settings SOSE creds-hiány miatt bukjanak import közben.
# (setdefault: ha a fejlesztő gépén van valódi .env, azt nem írjuk felül — de a tesztek
# semmilyen hálózatot nem hívnak, így a valódi kulcs sem szivárog ki.)
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("SUPABASE_URL", "https://test.supabase.co")
os.environ.setdefault("SUPABASE_KEY", "test-anon-key")
os.environ.setdefault("SUPABASE_SERVICE_KEY", "test-service-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "123456:test-telegram-token")
os.environ.setdefault("MUAPI_API_KEY", "test-muapi-key")

from src.storage.models import FeedItem  # noqa: E402  (a sys.path/env beállítás UTÁN)


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
