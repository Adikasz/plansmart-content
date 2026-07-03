"""Alapító-portré kezelés — háttér-eltávolítás (rembg) + cache + gyakoriság-logika.

A Dávid/Ádám portrékat egyszer futtatjuk át rembg-en (ONNX U2Net, lassú), a kivágott
PNG-t az `assets/brand/cutouts/` alá cache-eljük — utána azonnal betölthető. A brand
(PlanSmart) hang SOHA nem kap portrét (arctalan/hivatalos marad).

A gyakoriság: minden 3-4. david/adam poszt kap portrét (3 vagy 4 véletlen váltakozva,
természetes variációért). Az állapot egy egyszerű JSON-ban (`data/portrait_state.json`).

Önálló futtatás (cache előmelegítés + állapot kiírás):
    python -m src.visuals.portrait
"""
from __future__ import annotations

import json
import logging
import random
from functools import lru_cache
from pathlib import Path

from PIL import Image

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BRAND_DIR = PROJECT_ROOT / "assets" / "brand"
CUTOUT_DIR = BRAND_DIR / "cutouts"
STATE_FILE = PROJECT_ROOT / "data" / "portrait_state.json"

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
    """
    if voice not in PORTRAIT_VOICES:
        return False
    if force:
        return True
    state = _load_state()
    entry = state.get(voice) or {}
    count = int(entry.get("count", 0)) + 1
    threshold = int(entry.get("threshold") or random.choice(FREQ_CHOICES))
    if count >= threshold:
        state[voice] = {"count": 0, "threshold": random.choice(FREQ_CHOICES)}
        _save_state(state)
        return True
    state[voice] = {"count": count, "threshold": threshold}
    _save_state(state)
    return False


def _demo() -> int:
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    paths = preprocess_all()
    print("Cutouts:", {v: str(p) for v, p in paths.items()})
    print("State:", _load_state())
    return 0


if __name__ == "__main__":
    import os

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    raise SystemExit(_demo())
