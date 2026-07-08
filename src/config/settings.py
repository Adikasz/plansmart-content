"""Tipizált konfigurációs réteg — minden env változó EGY validált Pydantic objektumban.

Miért: a kód eddig ~35 helyen olvasott közvetlenül `os.environ.get(...)`-tal, típus- és
validáció nélkül. Ez a modul egy szigorúan tipizált, egyszer validált belépési pontot ad.

FONTOS (élő rendszer): NEM töröljük a meglévő közvetlen env-olvasásokat. Ez az ajánlott,
fokozatosan bevezethető KANONIKUS elérés — új kód innen olvasson, a régi migrálható.

A titkok Optional-ök: hiányukban az objektum létrejön (az import/instantiáció sosem
crashel a hiányzó kulcs miatt), a hiba a tényleges használat helyén derül ki — pontosan
ahogy a projekt lazy @lru_cache kliensei (storage.db, anthropic) is működnek.

pydantic-settings NINCS a pinned függőségek közt (csak pydantic 2.7) — ezért sima
`BaseModel` + `from_env()` classmethod, extra függőség és deploy-kockázat nélkül.
"""
from __future__ import annotations

import os
from collections.abc import Mapping
from functools import lru_cache

from pydantic import BaseModel, ConfigDict, Field

_TRUE_SET = {"1", "true", "yes", "on"}


def _as_bool(value: str | None, default: bool = False) -> bool:
    if value is None or value.strip() == "":
        return default
    return value.strip().lower() in _TRUE_SET


def _as_int(value: str | None, default: int) -> int:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _as_float(value: str | None, default: float) -> float:
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


class Settings(BaseModel):
    """A teljes futásidejű konfiguráció egy tipizált, validált objektumban."""

    model_config = ConfigDict(frozen=True, extra="ignore")

    # ── Titkok (Optional — a hiány a használat helyén derül ki) ──────────
    anthropic_api_key: str | None = None
    supabase_url: str | None = None
    supabase_key: str | None = None
    supabase_service_key: str | None = None
    telegram_bot_token: str | None = None
    muapi_api_key: str | None = None
    sentry_dsn: str | None = None
    webhook_base_url: str | None = None
    railway_environment: str | None = None

    # ── Telegram ─────────────────────────────────────────────────────────
    telegram_posts_chat_id: int = 0
    telegram_reactions_chat_id: int = 0

    # ── Outreach / reakció-asszisztens ───────────────────────────────────
    calendly_url: str = ""  # a DM first-message lead sablon foglalási linkje (üres → link nélkül)

    # ── Általános / ütemezés ─────────────────────────────────────────────
    log_level: str = "INFO"
    timezone: str = "Europe/Budapest"
    port: int = Field(default=8080, ge=0)
    collector_interval_hours: int = Field(default=2, ge=1)
    morning_post_time: str = "07:30"
    dry_run: bool = False

    # ── Generálás / minőség ──────────────────────────────────────────────
    text_ship_threshold: float = 9.0
    text_auto_improve: bool = False

    # ── Breaking / szűrés ────────────────────────────────────────────────
    breaking_news_enabled: bool = True
    max_breaking_per_day: int = Field(default=3, ge=0)
    filter_batch_limit: int = Field(default=50, ge=1)
    relevance_threshold: int = Field(default=6, ge=0, le=10)

    # ── Publikálás ───────────────────────────────────────────────────────
    linkedin_mock: bool = False

    # ── RSS collector ────────────────────────────────────────────────────
    rss_timeout_sec: float = Field(default=15.0, gt=0)
    rss_max_concurrency: int = Field(default=10, ge=1)
    rss_max_items: int = Field(default=50, ge=1)
    rss_max_retries: int = Field(default=3, ge=0)
    rss_user_agent: str | None = None  # None -> a modul BROWSER_UA defaultja

    # ── Generátor worker ─────────────────────────────────────────────────
    generation_voices: list[str] = Field(default_factory=lambda: ["david", "adam"])
    max_posts_per_run: int = Field(default=3, ge=1)
    generator_item_limit: int = Field(default=20, ge=1)
    quality_eval_every: int = Field(default=50, ge=1)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        """Env változókból (alapból os.environ) épít validált Settings-t.

        A bool/int/float mezőket a projekt történeti szemantikájával parse-oljuk
        (pl. bool: {1,true,yes,on}; interval: legalább 1), hogy a viselkedés
        megegyezzen a meglévő inline olvasásokkal.
        """
        e = env if env is not None else os.environ

        def g(name: str) -> str | None:
            v = e.get(name)
            return v if (v is None or v != "") else None

        return cls(
            anthropic_api_key=g("ANTHROPIC_API_KEY"),
            supabase_url=g("SUPABASE_URL"),
            supabase_key=g("SUPABASE_KEY"),
            supabase_service_key=g("SUPABASE_SERVICE_KEY"),
            telegram_bot_token=g("TELEGRAM_BOT_TOKEN"),
            muapi_api_key=g("MUAPI_API_KEY"),
            sentry_dsn=g("SENTRY_DSN"),
            webhook_base_url=g("WEBHOOK_BASE_URL"),
            railway_environment=g("RAILWAY_ENVIRONMENT"),
            telegram_posts_chat_id=_as_int(e.get("TELEGRAM_POSTS_CHAT_ID"), 0),
            telegram_reactions_chat_id=_as_int(e.get("TELEGRAM_REACTIONS_CHAT_ID"), 0),
            calendly_url=e.get("CALENDLY_URL", "") or "",
            log_level=e.get("LOG_LEVEL", "INFO") or "INFO",
            timezone=e.get("TIMEZONE", e.get("SCHEDULER_TZ", "Europe/Budapest")) or "Europe/Budapest",
            port=_as_int(e.get("PORT", e.get("HEALTH_PORT")), 8080),
            collector_interval_hours=max(1, _as_int(e.get("COLLECTOR_INTERVAL_HOURS"), 2)),
            morning_post_time=e.get("MORNING_POST_TIME", "07:30") or "07:30",
            dry_run=_as_bool(e.get("DRY_RUN"), False),
            text_ship_threshold=_as_float(e.get("TEXT_SHIP_THRESHOLD"), 9.0),
            text_auto_improve=_as_bool(e.get("TEXT_AUTO_IMPROVE"), False),
            breaking_news_enabled=_as_bool(e.get("BREAKING_NEWS_ENABLED"), True),
            max_breaking_per_day=_as_int(e.get("MAX_BREAKING_PER_DAY"), 3),
            filter_batch_limit=_as_int(e.get("FILTER_BATCH_LIMIT"), 50),
            relevance_threshold=_as_int(e.get("RELEVANCE_THRESHOLD"), 6),
            linkedin_mock=_as_bool(e.get("LINKEDIN_MOCK"), False),
            rss_timeout_sec=_as_float(e.get("RSS_TIMEOUT_SEC"), 15.0),
            rss_max_concurrency=_as_int(e.get("RSS_MAX_CONCURRENCY"), 10),
            rss_max_items=_as_int(e.get("RSS_MAX_ITEMS"), 50),
            rss_max_retries=_as_int(e.get("RSS_MAX_RETRIES"), 3),
            rss_user_agent=g("RSS_USER_AGENT"),
            generation_voices=[v.strip() for v in e.get("GENERATION_VOICES", "david,adam").split(",") if v.strip()],
            max_posts_per_run=_as_int(e.get("MAX_POSTS_PER_RUN"), 3),
            generator_item_limit=_as_int(e.get("GENERATOR_ITEM_LIMIT"), 20),
            quality_eval_every=_as_int(e.get("QUALITY_EVAL_EVERY"), 50),
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cache-elt, process-szintű Settings (os.environ-ből). Teszthez: Settings.from_env(dict)."""
    return Settings.from_env()
