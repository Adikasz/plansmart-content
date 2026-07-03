"""Phase 17 — vizuál-változatosság: 4 kompozíciós template + voice-onkénti mood paletta.

Pure module (nincs I/O, könnyen tesztelhető). Szétválasztás:
  • a TEMPLATE a STRUKTÚRÁT adja (elrendezés, pozíció, hangsúly),
  • a MOOD a voice validált brand-DNS-én belüli ACCENT SZÍNT (Part B — curated, nem random),
  • a text_overlay.py ezekből RAJZOL, a visual_generator.build_textfree_prompt a template+mood
    háttér-fragmentumát fűzi a Muapi prompthoz.

A rotációt (no-immediate-repeat, Supabase state) a visual_variety.py kezeli — ez a modul
csak a definíciókat adja.
"""
from __future__ import annotations

# ── A 4 template (FIX sorrend — a rotáció ezen lép körbe) ───────────────
STAT_CARD = "STAT_CARD"
QUOTE_STYLE = "QUOTE_STYLE"
SPLIT_COMPARISON = "SPLIT_COMPARISON"
MINIMAL_TYPOGRAPHIC = "MINIMAL_TYPOGRAPHIC"
TEMPLATES: tuple[str, ...] = (STAT_CARD, QUOTE_STYLE, SPLIT_COMPARISON, MINIMAL_TYPOGRAPHIC)

# ── Phase 17.1 — voice-onkénti engedélyezett template-halmaz ────────────
# A Phase 17 eval alapján: david kiemelkedő STAT_CARD baseline-jét (8.5) a QUOTE/MINIMAL
# ~1.3 ponttal alálőtte, ezért david CSAK 2 validált template-et kap: STAT_CARD (baseline) +
# SPLIT_COMPARISON (a QUOTE-tal azonos 7.2, de STRUKTURÁLISAN a legjobban differenciált a
# STAT_CARD-tól → láthatóbb változatosság). adam/plansmart mind a 4-et használja (0 mért
# regresszió). Ami nincs a dict-ben, az mind a 4-et kapja (TEMPLATES).
VOICE_TEMPLATES: dict[str, tuple[str, ...]] = {
    "david": (STAT_CARD, SPLIT_COMPARISON),
}


def templates_for(voice: str) -> tuple[str, ...]:
    """Az adott voice által használható template-ek FIX sorrendben (a rotáció ezen lép körbe).
    Alapértelmezés: mind a 4 (adam, plansmart); david: szűkített 2-es halmaz (lásd VOICE_TEMPLATES)."""
    return VOICE_TEMPLATES.get(voice, TEMPLATES)

# ── Mood paletták voice-onként (Part B) ────────────────────────────────
# Minden mood: accent RGB + rövid háttér-szín-irány (a Muapi háttér-prompthoz).
# Az index 0 a DEFAULT — reprodukálja a validált Phase 12.6 baseline-t (accent szín).
MOODS: dict[str, list[dict]] = {
    "david": [
        {"key": "teal",            "accent": (45, 212, 191),  "bg": "cool teal-tinged key light"},
        {"key": "amber",           "accent": (245, 179, 66),  "bg": "warm amber key light, subtle tungsten warmth"},
        {"key": "cool_white_glow", "accent": (200, 226, 255), "bg": "icy cool-white key light, no color cast"},
    ],
    "adam": [  # NO warm gold — bizonyítottan rosszabb Ádámnak
        {"key": "cool_steel",     "accent": (150, 172, 198), "bg": "cool steel-grey key light, neutral, below 5000K"},
        {"key": "muted_slate",    "accent": (124, 140, 160), "bg": "muted slate-grey gradient, flat cool light"},
        {"key": "soft_cool_blue", "accent": (120, 162, 220), "bg": "soft cool-blue undertone, cold morning light"},
    ],
    "plansmart": [  # a három közül a LEGvisszafogottabb (quiet confidence)
        {"key": "near_black_minimal", "accent": (226, 232, 240), "bg": "near-black minimal, one soft neutral light source"},
        {"key": "deep_teal_accent",   "accent": (34, 138, 128),  "bg": "restrained deep muted-teal accent light"},
        {"key": "warm_off_white",     "accent": (240, 232, 220), "bg": "subtle warm off-white light accent"},
    ],
}

DEFAULT_MOOD = {v: moods[0]["key"] for v, moods in MOODS.items()}


def moods_for(voice: str) -> list[dict]:
    return MOODS.get(voice, MOODS["plansmart"])


def mood_keys(voice: str) -> list[str]:
    return [m["key"] for m in moods_for(voice)]


def accent_for(voice: str, mood_key: str) -> tuple[int, int, int]:
    for m in moods_for(voice):
        if m["key"] == mood_key:
            return tuple(m["accent"])  # type: ignore[return-value]
    return tuple(moods_for(voice)[0]["accent"])  # type: ignore[return-value]


def mood_bg(voice: str, mood_key: str) -> str:
    for m in moods_for(voice):
        if m["key"] == mood_key:
            return m["bg"]
    return moods_for(voice)[0]["bg"]


# ── Template SPEC-ek (Part A) ──────────────────────────────────────────
# layout: a text_overlay.py melyik rajzoló ágát hívja.
# bg: a template-hez illő Muapi háttér-kompozíció (negatív tér / divider stb.).
_SPECS: dict[str, dict] = {
    STAT_CARD: {
        "layout": "stat_card",
        "desc": "large stat/number focal, subtext below — the proven default",
        "bg": ("Reserve negative space: keep the BOTTOM-LEFT 30% and TOP-RIGHT 20% simple and dark "
               "(for a stat + headline overlay added later)."),
    },
    QUOTE_STYLE: {
        "layout": "quote",
        "desc": "editorial pull-quote: big left-aligned main text, quote-mark accent, small attribution sub",
        "bg": ("Asymmetric composition: keep the LEFT 60% open, simple and dark for a large editorial "
               "pull-quote; place the single faint focal element in the RIGHT third only."),
    },
    SPLIT_COMPARISON: {
        "layout": "split",
        "desc": "vertical divider, before -> after / contrast framing",
        "bg": ("Composition with a natural vertical division down the CENTER (a central seam or mirror "
               "symmetry), BOTH halves simple and dark, supporting a before/after split with a central "
               "divider line added later."),
    },
    MINIMAL_TYPOGRAPHIC: {
        "layout": "minimal",
        "desc": "one enormous word / very short phrase centered, maximum negative space, highest contrast",
        "bg": ("Maximum negative space: an almost empty frame, one faint focal hint at the very edge, "
               "the CENTER kept clean and pure near-black for one enormous word. The most restrained "
               "background of all — no texture clutter."),
    },
}


def template_spec(template: str) -> dict:
    return _SPECS.get(template, _SPECS[STAT_CARD])


def layout_of(template: str) -> str:
    return template_spec(template)["layout"]


def template_bg(template: str) -> str:
    return template_spec(template)["bg"]


def is_template(name: str) -> bool:
    return name in _SPECS
