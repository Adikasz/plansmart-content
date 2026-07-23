"""Muapi.ai képgeneráló kliens — async, kétlépéses (submit → poll) minta.

A Muapi.ai aszinkron: egy POST elindít egy predikciót (visszaad egy request_id-t),
majd egy GET végponton pollozzuk az eredményt, amíg `status=completed`. A kész kép
a Muapi R2 tárhelyén hosztolt URL-en érhető el.

Csak Muapi.ai-t használunk (Higgsfield NEM). A kulcs a `.env`-ből: MUAPI_API_KEY.

Önálló füstteszt (egy kép generálása):
    python -m src.integrations.visuals.muapi_client "dark minimalist dashboard, #04060a background"
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
from dataclasses import dataclass, field
from typing import Any

import httpx
from dotenv import load_dotenv

from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)
load_dotenv(override=False)

BASE_URL = "https://api.muapi.ai/api/v1"

# Alapértelmezett modell statikus vizuálhoz; karakter-konzisztenciához nano-banana-2.
DEFAULT_MODEL = "flux-2-pro"

# Modell -> POST endpoint slug. Ha egy modell nincs itt, a modell nevét használjuk slugként.
MODEL_ENDPOINTS: dict[str, str] = {
    "flux-2-pro": "flux-2-pro",
    "nano-banana-2": "nano-banana-2",
}

# Becsült költség USD / kép (a Muapi nem mindig ad vissza pontos cost-ot a válaszban).
# Ha az API ad cost mezőt, az felülírja ezt. Lásd docs/BRAND.md modell-választás.
MODEL_PRICING_USD: dict[str, float] = {
    "flux-2-pro": 0.032,
    "nano-banana-2": 0.06,
    "midjourney-v8": 0.10,  # editorial/film-grain alternatíva (SOUL helyett — Phase 12.6 teszt)
}

POLL_INTERVAL_S = 2.0
POLL_TIMEOUT_S = 180.0
SUBMIT_TIMEOUT_S = 30.0

DEFAULT_RESOLUTION = "1k"  # flux-2-pro / nano-banana-2 kötelező/alapértelmezett felbontás

# A Muapi a model-kártya PÉLDA képét adja vissza ($0 költséggel), ha a generálás
# nem fut le igazából (pl. account még nem aktivált / web-safety sandbox). Ezt a
# /webassets/ útvonalról ismerjük fel — sosem mentünk el ilyen "kamu" képet.
_PLACEHOLDER_MARKER = "/webassets/"

MISSING_KEY_MSG = (
    "Hiányzó MUAPI_API_KEY. Add hozzá a .env-hez:\n"
    "    MUAPI_API_KEY=sk-...\n"
    "Regisztrálj a https://muapi.ai oldalon, és a kulcsot a "
    "Settings → API Keys alatt találod."
)

PLACEHOLDER_MSG = (
    "A Muapi a model PÉLDA-képét adta vissza valódi generálás helyett ($0 költség, "
    "üres usage-log). A kód és a kérés helyes (endpoint, paraméterek, x-api-key OK) — "
    "ez account-oldali állapot. Tedd rendbe a https://muapi.ai fiókban:\n"
    "  • adj meg fizetési módot / aktiváld a fiókot (a 'balance' lehet zárolt promó),\n"
    "  • kapcsold ki a kényszerített web-safety / sandbox módot (web_safety_forced),\n"
    "  • ha marad, írj a Muapi supportnak az érintett API kulccsal.\n"
    "Ellenőrzés: GET https://api.muapi.ai/api/v1/account/balance"
)

# Befejezett / hibás státuszok (a Muapi több szinonimát is használhat).
_DONE_OK = {"completed", "succeeded", "success", "done"}
_DONE_FAIL = {"failed", "error", "canceled", "cancelled"}


class MuapiError(RuntimeError):
    """Muapi API hiba (hiányzó kulcs, generálási hiba, timeout)."""


@dataclass
class GenerationResult:
    """Egy sikeres generálás eredménye — a visual_generator és a cost tracking használja."""

    image_url: str
    model: str
    request_id: str | None = None
    cost_usd: float | None = None
    raw: dict[str, Any] = field(default_factory=dict)


def _require_key() -> str:
    key = os.environ.get("MUAPI_API_KEY")
    if not key:
        raise MuapiError(MISSING_KEY_MSG)
    return key


def _headers(key: str) -> dict[str, str]:
    # Muapi.ai a kulcsot az x-api-key fejlécben várja.
    return {"x-api-key": key, "Content-Type": "application/json"}


def _endpoint_for(model: str) -> str:
    return MODEL_ENDPOINTS.get(model, model)


def _extract_request_id(data: dict[str, Any]) -> str | None:
    for k in ("request_id", "requestId", "id", "prediction_id", "predictionId"):
        if data.get(k):
            return str(data[k])
    # néhány válasz beágyazza: {"data": {"request_id": ...}}
    inner = data.get("data")
    if isinstance(inner, dict):
        return _extract_request_id(inner)
    return None


def _extract_image_url(data: dict[str, Any]) -> str | None:
    """Robusztusan kihalássza a kész kép URL-jét a sokféle lehetséges válaszalakból."""
    # Közvetlen mezők
    for k in ("image_url", "imageUrl", "url", "output_url"):
        v = data.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v
    # Listák: outputs / images / urls / output
    for k in ("outputs", "images", "urls", "output", "result", "data"):
        v = data.get(k)
        if isinstance(v, str) and v.startswith("http"):
            return v
        if isinstance(v, list) and v:
            first = v[0]
            if isinstance(first, str) and first.startswith("http"):
                return first
            if isinstance(first, dict):
                u = _extract_image_url(first)
                if u:
                    return u
        if isinstance(v, dict):
            u = _extract_image_url(v)
            if u:
                return u
    return None


def _extract_cost(data: dict[str, Any], model: str) -> float | None:
    # A Muapi a költséget gyakran beágyazza: {"cost": {"amount_usd": 0.04, ...}}
    cost = data.get("cost")
    if isinstance(cost, dict) and isinstance(cost.get("amount_usd"), (int, float)):
        return float(cost["amount_usd"])
    for k in ("cost", "cost_usd", "price", "credits_used"):
        v = data.get(k)
        if isinstance(v, (int, float)):
            return float(v)
    inner = data.get("data")
    if isinstance(inner, dict):
        c = _extract_cost(inner, model)
        if c is not None:
            return c
    return MODEL_PRICING_USD.get(model)


def _is_placeholder(url: str | None) -> bool:
    return bool(url) and _PLACEHOLDER_MARKER in url


async def generate(
    prompt: str,
    model: str = DEFAULT_MODEL,
    aspect_ratio: str = "1:1",
    resolution: str = DEFAULT_RESOLUTION,
    *,
    extra_params: dict[str, Any] | None = None,
    client: httpx.AsyncClient | None = None,
) -> GenerationResult:
    """Generál egy képet és visszaadja a teljes eredményt (URL + költség + meta).

    Kétlépéses minta:
      a) POST {BASE}/{endpoint}  -> request_id
      b) GET  {BASE}/predictions/{request_id}/result  (poll status=completed-ig)

    A flux-2-pro / nano-banana-2 a `resolution`-t ("1k"/"2k") elvárja; ezt küldjük.
    Ha a Muapi a PÉLDA-képet adja vissza (account-oldali sandbox), MuapiError-t dobunk.
    """
    key = _require_key()
    endpoint = _endpoint_for(model)
    payload: dict[str, Any] = {"prompt": prompt, "aspect_ratio": aspect_ratio, "resolution": resolution}
    if extra_params:
        payload.update(extra_params)

    own_client = client is None
    client = client or httpx.AsyncClient()
    try:
        # a) Submit
        submit_url = f"{BASE_URL}/{endpoint}"
        logger.info("Muapi submit: model=%s endpoint=%s ar=%s", model, endpoint, aspect_ratio)
        resp = await client.post(submit_url, json=payload, headers=_headers(key), timeout=SUBMIT_TIMEOUT_S)
        if resp.status_code >= 400:
            raise MuapiError(f"Muapi submit hiba {resp.status_code}: {resp.text[:300]}")
        submit_data = resp.json()

        request_id = _extract_request_id(submit_data)
        # Néhány modell szinkron is válaszolhat: ha már jött (valódi, nem példa) URL, kész.
        immediate = _extract_image_url(submit_data)
        if immediate and not _is_placeholder(immediate):
            return GenerationResult(
                image_url=immediate, model=model, request_id=request_id,
                cost_usd=_extract_cost(submit_data, model), raw=submit_data,
            )
        if not request_id:
            raise MuapiError(f"Muapi válasz nem tartalmaz request_id-t: {str(submit_data)[:300]}")

        # b) Poll
        result_url = f"{BASE_URL}/predictions/{request_id}/result"
        waited = 0.0
        while waited < POLL_TIMEOUT_S:
            await asyncio.sleep(POLL_INTERVAL_S)
            waited += POLL_INTERVAL_S
            r = await client.get(result_url, headers=_headers(key), timeout=SUBMIT_TIMEOUT_S)
            if r.status_code >= 400:
                raise MuapiError(f"Muapi poll hiba {r.status_code}: {r.text[:300]}")
            data = r.json()
            status = str(data.get("status", "")).lower()

            if status in _DONE_FAIL:
                raise MuapiError(f"Muapi generálás sikertelen (status={status}): {str(data)[:300]}")

            url = _extract_image_url(data)
            if status in _DONE_OK or url:
                if not url:
                    raise MuapiError(f"Muapi 'completed' de nincs kép URL: {str(data)[:300]}")
                if _is_placeholder(url):
                    # A generálás nem futott le igazából — account-oldali állapot.
                    raise MuapiError(PLACEHOLDER_MSG)
                logger.info("Muapi kész: request_id=%s (%.0fs)", request_id, waited)
                return GenerationResult(
                    image_url=url, model=model, request_id=request_id,
                    cost_usd=_extract_cost(data, model), raw=data,
                )
            logger.debug("Muapi poll: status=%s (%.0fs)", status or "?", waited)

        raise MuapiError(f"Muapi timeout {POLL_TIMEOUT_S:.0f}s után (request_id={request_id})")
    finally:
        if own_client:
            await client.aclose()


async def generate_image(
    prompt: str,
    model: str = DEFAULT_MODEL,
    aspect_ratio: str = "1:1",
) -> str:
    """A specifikáció szerinti egyszerű felület: visszaadja a hosztolt kép URL-jét."""
    result = await generate(prompt, model=model, aspect_ratio=aspect_ratio)
    return result.image_url


async def _smoke(prompt: str) -> int:
    try:
        result = await generate(prompt)
    except MuapiError as exc:
        logger.error("%s", exc)
        return 1
    logger.info("URL : %s", result.image_url)
    logger.info("cost: $%s  model=%s  request_id=%s", result.cost_usd, result.model, result.request_id)
    return 0


if __name__ == "__main__":
    setup_logging()
    prompt = sys.argv[1] if len(sys.argv) > 1 else "minimalist dark dashboard, #04060a background, single bold metric centered"
    raise SystemExit(asyncio.run(_smoke(prompt)))
