"""Phase 17 — vizuál-változatosság rotáció (no-immediate-repeat), Supabase state.

Minden új poszt olyan (template, mood) kombót kap, ami KÜLÖNBÖZIK az adott voice előző
posztjától. Determinisztikus körbe-forgás (nem random): a 4 template fix sorrendben lép
tovább (így 4 egymást követő poszt MIND a 4 template-et használja), a mood a voice
paletáján lép. Az első posztnál (nincs előző állapot) a baseline: STAT_CARD + default mood.

Állapot: elsődlegesen a Supabase `visual_variety_state` tábla (túléli a Railway redeployt,
lásd Phase 15); ha a Supabase elérhetetlen, lokális JSON fallback. SOSEM crashelünk emiatt.

Önálló teszt (rotáció szárazon, image nélkül):
    python -m src.visuals.visual_variety
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

from src.visuals import layout_templates as lt

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STATE_FILE = PROJECT_ROOT / "data" / "visual_variety_state.json"  # csak fallback
STATE_TABLE = "visual_variety_state"


def _next_template(voice: str, last_template: str | None) -> str:
    """A voice FIX template-sorrendjében a következő (körbe). None / a voice halmazán KÍVÜLI
    (pl. korábbi, már letiltott template) → az első (STAT_CARD = baseline). A voice szűkített
    halmazát használja (david: 2, adam/plansmart: 4) — lásd lt.templates_for."""
    templates = lt.templates_for(voice)
    if last_template not in templates:
        return templates[0]
    return templates[(templates.index(last_template) + 1) % len(templates)]


def _next_mood(voice: str, last_mood: str | None) -> str:
    """A voice mood-paletáján a következő (körbe). None → az első (default = baseline)."""
    keys = lt.mood_keys(voice)
    if last_mood not in keys:
        return keys[0]
    return keys[(keys.index(last_mood) + 1) % len(keys)]


def _decide(voice: str, last: dict | None) -> tuple[str, str]:
    """(következő_template, következő_mood) az előző állapotból.

    Első hívás (last üres) → (STAT_CARD, default mood) = validált baseline. Utána körbe-forgás,
    ami garantálja: template != last_template ÉS mood != last_mood (nincs azonnali ismétlés).
    """
    last = last or {}
    lt_prev = last.get("last_template")
    lm_prev = last.get("last_mood")
    if not lt_prev:
        return lt.templates_for(voice)[0], lt.DEFAULT_MOOD.get(voice, lt.mood_keys(voice)[0])
    return _next_template(voice, lt_prev), _next_mood(voice, lm_prev)


# ── Supabase állapot (elsődleges) ──────────────────────────────────────
def _sb_client():
    from src.storage.db import get_client, has_service_key

    return get_client(use_service_key=has_service_key())


def _sb_get(voice: str) -> dict | None:
    resp = (_sb_client().table(STATE_TABLE)
            .select("last_template,last_mood").eq("voice", voice).limit(1).execute())
    rows = resp.data or []
    return rows[0] if rows else None


def _sb_upsert(voice: str, template: str, mood: str) -> None:
    _sb_client().table(STATE_TABLE).upsert({
        "voice": voice, "last_template": template, "last_mood": mood,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }).execute()


# ── Lokális JSON fallback ──────────────────────────────────────────────
def _load_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def next_variant(voice: str) -> dict:
    """A voice következő vizuál-változata: {template, mood, accent}.

    Beolvassa az előző választást (Supabase, fallback JSON), kiszámolja a KÖVETKEZŐT
    (nincs azonnali ismétlés), és VISSZAÍRJA az állapotot. Hiba esetén sosem dob — a
    legrosszabb eset a baseline (STAT_CARD + default mood).
    """
    try:
        template, mood = _decide(voice, _sb_get(voice))
        _sb_upsert(voice, template, mood)
    except Exception as exc:
        logger.warning("[visual-variety] Supabase elérhetetlen (%s) — lokális JSON fallback",
                       str(exc)[:120])
        try:
            state = _load_state()
            template, mood = _decide(voice, state.get(voice))
            state[voice] = {"last_template": template, "last_mood": mood}
            _save_state(state)
        except Exception as exc2:  # a fallback FS is hibázhat → végső eset: baseline (sosem crashelünk)
            logger.warning("[visual-variety] JSON fallback is hibázott (%s) — baseline variant", str(exc2)[:100])
            template = lt.TEMPLATES[0]
            mood = lt.DEFAULT_MOOD.get(voice, lt.mood_keys(voice)[0])
    accent = lt.accent_for(voice, mood)
    logger.info("[visual-variety] %s → template=%s mood=%s accent=%s", voice, template, mood, accent)
    return {"template": template, "mood": mood, "accent": accent}


def _demo() -> int:
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    # Szimuláció: 4 egymást követő választás voice-onként egy in-memory store-ral.
    store: dict[str, dict] = {}

    def fake_get(v):
        return dict(store[v]) if v in store else None

    def fake_upsert(v, t, m):
        store[v] = {"last_template": t, "last_mood": m}

    global _sb_get, _sb_upsert
    _sb_get, _sb_upsert = fake_get, fake_upsert  # type: ignore
    for voice in ("david", "adam", "plansmart"):
        allowed = lt.templates_for(voice)
        # 2*len körrel megmutatjuk a teljes ciklust (david: 4 = 2 kör; adam/plansmart: 8 = 2 kör).
        seq = [next_variant(voice) for _ in range(2 * len(allowed))]
        print(f"\n{voice} (engedélyezett template-ek: {len(allowed)} — {', '.join(allowed)}):")
        for i, v in enumerate(seq, 1):
            print(f"  {i}. {v['template']:20s} | {v['mood']}")
        templates_used = {v["template"] for v in seq}
        combos = [(v["template"], v["mood"]) for v in seq]
        no_repeat = all(combos[i] != combos[i - 1] for i in range(1, len(combos)))
        first_baseline = seq[0]["template"] == lt.TEMPLATES[0] and seq[0]["mood"] == lt.DEFAULT_MOOD[voice]
        print(f"  -> {len(templates_used)}/{len(allowed)} allowed templates used | "
              f"no immediate repeat: {no_repeat} | first=baseline: {first_baseline}")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level="INFO", format="%(message)s")
    raise SystemExit(_demo())
