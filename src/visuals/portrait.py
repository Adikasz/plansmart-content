"""Alapító-portré kezelés — háttér-eltávolítás (rembg) + cache + gyakoriság-logika.

A Dávid/Ádám portrékat egyszer futtatjuk át rembg-en (ONNX U2Net, lassú), a kivágott
PNG-t az `assets/brand/cutouts/` alá cache-eljük — utána azonnal betölthető. A brand
(PlanSmart) hang SOHA nem kap portrét (arctalan/hivatalos marad).

A gyakoriság: minden 3-4. david/adam poszt kap portrét (3 vagy 4 véletlen váltakozva,
természetes variációért). Az állapot elsődlegesen a Supabase `portrait_counters`
tábla (túléli a Railway redeployt); ha a Supabase elérhetetlen, a régi lokális JSON
(`data/portrait_state.json`) a fallback.

Önálló futtatás (cache előmelegítés + állapot kiírás):
    python -m src.visuals.portrait
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from PIL import Image

from src.config.settings import get_settings
from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BRAND_DIR = PROJECT_ROOT / "assets" / "brand"
# Éles: PORTRAIT_CUTOUT_DIR egy perzisztens Railway volume-ra mutat, hogy a már kivágott
# PNG-k túléljék a redeployt és a rembg SOSE fusson (memória-korlát — lásd Settings docstring).
CUTOUT_DIR = Path(get_settings().portrait_cutout_dir) if get_settings().portrait_cutout_dir else BRAND_DIR / "cutouts"
STATE_FILE = PROJECT_ROOT / "data" / "portrait_state.json"  # csak fallback (Supabase az elsődleges)
STATE_TABLE = "portrait_counters"

# voice → a feltöltött forrás-portré fájlneve az assets/brand/ alatt.
PORTRAIT_SOURCES: dict[str, str] = {
    "david": "Jécsai Dávid fotó.jpeg",
    "adam": "Nagy Ádám portrékép.jpg",
}

# Csak ezek a hangok kaphatnak portrét — plansmart SOHA.
PORTRAIT_VOICES = frozenset(PORTRAIT_SOURCES)

# Gyakoriság: minden N. poszt kap portrét, N ∈ {3, 4} véletlenszerűen (természetes variáció).
FREQ_CHOICES = (3, 4)


def remove_background(image_path: str | Path) -> Image.Image:
    """Háttér-eltávolítás rembg-gel → RGBA kivágat (a személy, átlátszó háttérrel).

    Lassú (ONNX U2Net, első hívásnál modell-letöltés is) — ezért cache-eljük (get_cutout).
    """
    from rembg import remove  # lazy import (nehéz onnxruntime függőség)

    src = Image.open(image_path).convert("RGBA")
    cut = remove(src, session=_session())
    bbox = cut.getchannel("A").getbbox()  # az átlátszó margó levágása
    return cut.crop(bbox) if bbox else cut


@lru_cache(maxsize=1)
def _session():
    from rembg import new_session

    return new_session("u2net")


def get_cutout(voice: str, *, force_refresh: bool = False) -> Path | None:
    """A voice kivágott portréjának lokális útja (cache-elve). None, ha nincs forrás/hang.

    Első hívásnál lefuttatja a rembg-et és elmenti `assets/brand/cutouts/{voice}.png`-ként.
    """
    if voice not in PORTRAIT_SOURCES:
        return None
    src = BRAND_DIR / PORTRAIT_SOURCES[voice]
    if not src.exists():
        logger.warning("[portrait] hiányzó forrás-portré: %s", src)
        return None
    out = CUTOUT_DIR / f"{voice}.png"
    if out.exists() and not force_refresh:
        return out
    CUTOUT_DIR.mkdir(parents=True, exist_ok=True)
    logger.info("[portrait] háttér-eltávolítás (%s) — egyszeri, cache-elt…", voice)
    cut = remove_background(src)
    cut.save(out, "PNG")
    logger.info("[portrait] cutout mentve: %s (%dx%d)", out.name, *cut.size)
    return out


def preprocess_all(force_refresh: bool = False) -> dict[str, Path]:
    """Minden alapító-portré kivágatának előállítása/cache-elése (startupkor egyszer)."""
    result: dict[str, Path] = {}
    for voice in PORTRAIT_SOURCES:
        p = get_cutout(voice, force_refresh=force_refresh)
        if p is not None:
            result[voice] = p
    return result


# ── Gyakoriság-logika (rolling counter per voice) ──────────────────────
#
# Döntési logika (közös a Supabase és a JSON-fallback ág között):
#   count = előző_count + 1
#   ha count >= threshold → portré, majd reset (count=0, új véletlen threshold 3/4)
#   különben → nincs portré, az új count/threshold mentése.
def _decide(entry: dict | None) -> tuple[bool, int, int]:
    """A {count, threshold} állapotból: (kell_portré?, új_count, új_threshold)."""
    entry = entry or {}
    count = int(entry.get("count", 0)) + 1
    threshold = int(entry.get("threshold") or random.choice(FREQ_CHOICES))
    if count >= threshold:
        return True, 0, random.choice(FREQ_CHOICES)
    return False, count, threshold


# ── Supabase állapot (elsődleges — túléli a Railway redeployt) ─────────
def _sb_client():
    from src.storage.db import get_client, has_service_key

    return get_client(use_service_key=has_service_key())


def _sb_get(voice: str) -> dict | None:
    """A voice {count, threshold} sora Supabase-ből, vagy None ha még nincs.

    Kapcsolati / API hibát FELDOB — a hívó (should_include_portrait) fogja el és
    esik vissza a lokális JSON-ra.
    """
    resp = _sb_client().table(STATE_TABLE).select("count,threshold").eq("voice", voice).limit(1).execute()
    rows = resp.data or []
    return rows[0] if rows else None


def _sb_upsert(voice: str, count: int, threshold: int) -> None:
    _sb_client().table(STATE_TABLE).upsert({
        "voice": voice, "count": count, "threshold": threshold,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).execute()


# ── Lokális JSON fallback (ha a Supabase elérhetetlen) ─────────────────
def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def should_include_portrait(voice: str, *, force: bool = False) -> bool:
    """Kell-e portré ehhez a poszthoz? Növeli a per-voice számlálót és a küszöbnél jelez.

    force=True: mindig igaz (a /create --portrait kézi felülíráshoz) — a számlálót nem érinti.
    plansmart / ismeretlen hang: mindig hamis. Minden N. (3-4) david/adam poszt kap portrét.

    Állapot: elsődlegesen a Supabase `portrait_counters` tábla (túléli a redeployt);
    ha a Supabase elérhetetlen, a lokális JSON a fallback (redeploykor ugyan elveszik,
    de sosem crashelünk emiatt).
    """
    if voice not in PORTRAIT_VOICES:
        return False
    if force:
        return True
    try:
        include, new_count, new_threshold = _decide(_sb_get(voice))
        _sb_upsert(voice, new_count, new_threshold)
        return include
    except Exception as exc:  # Supabase elérhetetlen → lokális JSON fallback
        logger.warning("[portrait] Supabase állapot elérhetetlen (%s) — lokális JSON fallback",
                       str(exc)[:120])
        state = _load_state()
        include, new_count, new_threshold = _decide(state.get(voice))
        state[voice] = {"count": new_count, "threshold": new_threshold}
        _save_state(state)
        return include


def seed_from_local_state() -> dict[str, str]:
    """Egyszeri migráció: a lokális portrait_state.json értékeit átemeli Supabase-be.

    Csak azt a hangot seedeli, amelynek MÉG NINCS sora Supabase-ben (nem ír felül élő
    állapotot). A Supabase táblának léteznie kell (migration_15). Visszaadja, mi történt.
    """
    local = _load_state()
    result: dict[str, str] = {}
    for voice, entry in local.items():
        if voice not in PORTRAIT_VOICES:
            continue
        if _sb_get(voice) is not None:
            result[voice] = "kihagyva (már van sor Supabase-ben)"
            continue
        count = int((entry or {}).get("count", 0))
        threshold = int((entry or {}).get("threshold") or random.choice(FREQ_CHOICES))
        _sb_upsert(voice, count, threshold)
        result[voice] = f"seedelve (count={count}, threshold={threshold})"
    return result


def _demo() -> int:
    setup_logging()
    paths = preprocess_all()
    print("Cutouts:", {v: str(p) for v, p in paths.items()})
    try:
        print("Supabase state:", {v: _sb_get(v) for v in PORTRAIT_VOICES})
    except Exception as exc:
        print("Supabase elérhetetlen:", str(exc)[:120])
    print("Lokális fallback state:", _load_state())
    return 0


if __name__ == "__main__":
    raise SystemExit(_demo())
