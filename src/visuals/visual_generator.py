"""Voice-specifikus vizuál generátor — Claude Sonnet ír Muapi promptot, majd Muapi képet.

Folyamat:
  1. A post.voice alapján betölti a `prompts/visual_styles/{voice}.md` stílust.
  2. Claude Sonnet az art director: a poszt tartalmából + voice stílusból + brand DNS-ből
     EGY tömör image-generation promptot ír (flux-2-pro-nak).
  3. A muapi_client legenerálja a képet, és (best-effort) logoljuk a költséget.

Önálló teszt:
    python -m src.visuals.visual_generator
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from functools import lru_cache
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from src.generators.base_generator import _repair_and_parse
from src.visuals import muapi_client

logger = logging.getLogger(__name__)
load_dotenv(override=False)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
STYLES_DIR = PROJECT_ROOT / "prompts" / "visual_styles"

MODEL = "claude-sonnet-4-6"  # ADR-008: minőségi modell (a base_generator-ral összhangban)
HAIKU_MODEL = "claude-haiku-4-5-20251001"  # olcsó/gyors — vizuál-szöveg kinyerés
MAX_TOKENS = 600

VOICES = {"david", "adam", "plansmart"}

# A magyar overlay-szöveg kinyerése a poszt tartalmából (Haiku).
EXTRACT_SYSTEM = (
    "Te egy art director asszisztense vagy. A bemenet egy magyar LinkedIn poszt. "
    "Kinyered a vizuálra kerülő MAGYAR szöveget három mezőbe:\n"
    "• main_text: a fő üzenet 3-5 szóban, NAGYBETŰSEN (ütős display szöveg). Ha van domináns "
    "szám (pl. 73%), AZ legyen a main_text.\n"
    "• sub_text: másodlagos magyarázó szöveg, kb. 10-15 szó.\n"
    "• stat: a legfontosabb konkrét szám/metrika ha van (pl. '73%', '4 óra', '800.000 Ft'), "
    "egyébként null.\n\n"
    "Szabályok: a szöveg MINDIG magyar, a poszt SAJÁT szavaiból. Ne találj ki új adatot.\n\n"
    "Példák:\n"
    '  „Megtanultad a Claude-ot. Mi jön utána?" → '
    '{"main_text":"MEGTANULTAD A CLAUDE-OT.","sub_text":"Mi jön utána?","stat":null}\n'
    '  „73% a magyar KKV-knak heti 4+ órát ismétlődő manuális munkával tölt" → '
    '{"main_text":"73%","sub_text":"heti 4+ óra ismétlődő manuális munka","stat":"73%"}\n\n'
    "KIZÁRÓLAG ezt a JSON-t add vissza:\n"
    '{"main_text":"NAGYBETŰS FŐSZÖVEG","sub_text":"alszöveg","stat":"konkrét szám vagy null"}'
)

# Közös brand DNS — a docs/BRAND.md "Közös vizuális DNS" szakaszával összhangban.
BRAND_DNA = (
    "Brand: PlanSmart — 'Az autonóm cég operációs rendszere'. "
    "Mandatory dark background #04060a (near-black). "
    "Brand font character: geometric grotesque display + monospace metric accents. "
    "Include a subtle PlanSmart logo watermark in a corner — use the placeholder token "
    "[BRAND_LOGO] (no real logo asset is uploaded yet). "
    "If the post has a concrete number/metric, make it the visual focal point. "
    "Never render marketing buzzwords (no 'AI Revolution', no 'transform your business'). "
    "No stock-photo clichés (no handshakes, no generic glowing tech networks). "
    "Output for a text-to-image model (flux-2-pro): a single vivid English paragraph, "
    "concrete and visual, no preamble, no quotes, max ~80 words."
)

# Voice-specifikus stílus-leírók a vizuál prompt "Voice style:" sorához (kondenzált).
VOICE_STYLE_LINE = {
    "david": "technical builder — terminal/code/system-diagram accents, cool teal glow, monospace details",
    "adam": "strategist — financial-dashboard minimalism, warm gold or soft green metric accent, before/after framing",
    "plansmart": "premium SaaS brand — refined neutral accents, PlanSmart wordmark, trustworthy and official",
}

# Phase 12 eval-győztes irány hangonként (data/visual_eval_results_latest.json):
#   david → C (filmes), adam → A (produkciós), plansmart → C (filmes).
EVAL_WINNER_DIRECTION = {
    "david": "Cinematic noir (eval-győztes C): egy erős key-light, mély árnyékok, markáns 35mm "
             "film grain, atmoszférikus mélység.",
    "adam": "Letisztult produkciós keret (eval-győztes A): financial-dashboard visszafogottság, "
            "egy metrika-hős, lágy accent-glow.",
    "plansmart": "Filmes prémium (eval-győztes C): volumetrikus fény, finom köd, film grain, "
                 "movie-poster polish.",
}

# Phase 12 top-3 fix a prompt-evalból (garbled magyar szöveg, szürke háttér, HUD-zsúfoltság).
EVAL_HARDENING = (
    "Render ONLY the exact Hungarian text given above, letter-perfect — do NOT invent, translate, "
    "or add any other text, captions, code, numbers, or UI labels.\n"
    "TRUE near-black #04060a background (not grey). Ban HUD panels, pseudo-code, UI chrome, "
    "decorative charts, and any secondary text clusters.\n"
    "Exactly ONE focal element. No people, no stock-photo clichés, no marketing buzzwords."
)


@lru_cache(maxsize=4)
def _load_style(voice: str) -> str:
    path = STYLES_DIR / f"{voice}.md"
    if not path.exists():
        raise FileNotFoundError(
            f"Hiányzó vizuál stílus: {path}. Hozd létre a prompts/visual_styles/ alatt."
        )
    return path.read_text(encoding="utf-8")


@lru_cache(maxsize=1)
def _client() -> AsyncAnthropic:
    return AsyncAnthropic()  # ANTHROPIC_API_KEY a környezetből


def _post_text(post: dict[str, Any]) -> str:
    content = (post.get("edited_content") or post.get("content") or "").strip()
    tags = " ".join(post.get("hashtags") or [])
    return f"{content}\n\n{tags}".strip()


async def extract_visual_text(post_content: str) -> dict[str, Any]:
    """A poszt tartalmából kinyeri a vizuálra kerülő MAGYAR overlay-szöveget (Haiku).

    Visszaad: {"main_text": str, "sub_text": str, "stat": str | None}.
    Hiba esetén egyszerű heurisztika (első sorok), hogy a vizuál így is mehessen.
    """
    text = (post_content or "").strip()
    try:
        msg = await _client().messages.create(
            model=HAIKU_MODEL,
            max_tokens=300,
            system=EXTRACT_SYSTEM,
            messages=[{"role": "user", "content": text[:1500]}],
        )
        data = _repair_and_parse(msg.content[0].text if msg.content else "")
    except Exception as exc:
        logger.warning("[visual-text] kinyerés hiba (%s) — heurisztika.", str(exc)[:90])
        data = None

    if data and (data.get("main_text") or "").strip():
        stat = data.get("stat")
        if isinstance(stat, str) and stat.strip().lower() in {"null", "none", ""}:
            stat = None
        return {
            "main_text": str(data["main_text"]).strip(),
            "sub_text": str(data.get("sub_text") or "").strip(),
            "stat": stat,
        }

    # Fallback: a poszt első nem-üres sora főszöveg, a következő alszöveg.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    main = (lines[0] if lines else "PLANSMART")[:40].upper()
    sub = (lines[1] if len(lines) > 1 else "")[:60]
    return {"main_text": main, "sub_text": sub, "stat": None}


async def write_muapi_prompt(post: dict[str, Any], visual_text: dict[str, Any] | None = None) -> str:
    """Muapi (flux-2-pro) image prompt — magyar szöveg-overlay-jel (PART 4).

    A visual_text-et (main_text/sub_text/stat) ha nem kapja meg, kinyeri a posztból (Haiku).
    A promptot determinisztikusan, a kért formátum szerint építi fel — így a magyar szöveg
    garantáltan, szó szerint a vizuálra kerül (nem fordítjuk, nem hagyjuk modellre).
    """
    voice = post.get("voice", "")
    if visual_text is None:
        visual_text = await extract_visual_text(_post_text(post))

    main_text = visual_text.get("main_text", "")
    sub_text = visual_text.get("sub_text", "")
    stat = visual_text.get("stat")
    stat_line = f'STAT (giant, teal glow): "{stat}"\n\n' if stat else ""
    voice_style = VOICE_STYLE_LINE.get(voice, "clean, minimalist, premium")
    winner = EVAL_WINNER_DIRECTION.get(voice, "")

    return (
        "Dark #04060a background. Bold Bebas Neue / Neue Machina display typography.\n\n"
        f'MAIN TEXT (huge, centered, white, Hungarian, all caps):\n"{main_text}"\n\n'
        f"{stat_line}"
        f'SUBTEXT (smaller, grey, Hungarian):\n"{sub_text}"\n\n'
        "Bottom-right: a small, subtle, low-opacity PlanSmart wordmark watermark.\n"
        "Cinematic lighting, slight film grain, Soul Cinema aesthetic.\n"
        "High contrast, scroll-stopping, trending AI content creator style.\n"
        f"{EVAL_HARDENING}\n"
        f"Voice style: {voice_style}.\n"
        f"Eval-winner direction: {winner}"
    )


async def generate_visual(post: dict[str, Any]) -> dict[str, Any]:
    """Egy poszthoz vizuál generálása.

    Visszaad: {"image_url", "visual_prompt", "model_used", "cost_usd"}.
    A post["voice"] kötelező ("david" | "adam" | "plansmart").
    A modell felülírható a post["visual_model"]-lel (alapértelmezett: flux-2-pro);
    nano-banana-2 karakter-konzisztenciához (headshot feltöltés után).
    """
    voice = post.get("voice")
    if voice not in VOICES:
        raise ValueError(f"Ismeretlen/hiányzó voice: {voice!r}. Várt: {sorted(VOICES)}")

    model = post.get("visual_model") or muapi_client.DEFAULT_MODEL
    aspect_ratio = post.get("aspect_ratio") or "1:1"

    visual_text = await extract_visual_text(_post_text(post))
    visual_prompt = await write_muapi_prompt(post, visual_text=visual_text)
    logger.info(
        "[%s] Muapi prompt kész (%d kar) | overlay: '%s' / '%s'",
        voice, len(visual_prompt), visual_text.get("main_text", ""), visual_text.get("sub_text", ""),
    )

    result = await muapi_client.generate(visual_prompt, model=model, aspect_ratio=aspect_ratio)

    # Best-effort költség-log (csak ha van post_id és elérhető a Supabase).
    if post.get("id"):
        _record_cost_safe(post["id"], result)

    return {
        "image_url": result.image_url,
        "visual_prompt": visual_prompt,
        "visual_text": visual_text,
        "model_used": result.model,
        "cost_usd": result.cost_usd,
    }


# ── Phase 12.6: fotografikus, EGY-objektumos, szöveg-NÉLKÜLI alapkép-prompt ──────
# A Phase 12 + SOUL/Flux eval visszatérő panaszai: "generic AI-art" (62), "cluttered/
# competing" (59), "pseudo-code/UI/HUD panels" (57). A megoldás: konkrét fotó-technikai
# irányítás (filmstock, megvilágítás, mélységélesség) + EGYETLEN objektum + szigorú tiltás
# minden képernyő/UI/kód elemre.

# Minden voice-ra közös fotó-technikai DNS.
TEXTFREE_COMMON = (
    "Shot on Kodak Portra 800 film, visible organic grain structure. "
    "Single dramatic key light from upper-left, deep falloff crushing to pure black. "
    "TRUE near-black #04060a background, NOT dark grey — verify the shadows crush to black. "
    "Shallow depth of field, f/1.4, soft bokeh in the background only. "
    "Photographic, not illustrated. Not digital art. Not concept art. Not 3D render. "
    "ABSOLUTELY NO text, NO letters, NO words, NO numbers, NO typography, NO UI elements, "
    "NO HUD, NO code overlays, NO screens, NO floating panels, NO holographic interfaces, "
    "NO charts, NO logos, NO people. "
    "Reserve negative space: keep the BOTTOM-LEFT 30% and TOP-RIGHT 20% simple and dark "
    "(for text overlay added later)."
)

# Voice-specifikus EGY-objektumos jelenet (a régi absztrakt 'dashboard/terminal' helyett).
VOICE_SCENE = {
    "david": (
        "A single abstract object suggesting 'building/creating' — e.g. an out-of-focus "
        "mechanical keyboard under dramatic side light, OR a single glowing cable/wire forming "
        "a subtle curve in darkness, OR an extreme macro of a circuit-board edge with shallow "
        "focus. ONE object only. Moody, intimate, workshop-at-night feeling. Cool teal-tinged key light."
    ),
    "adam": (
        "Pure atmospheric darkness with cool, flat lighting — NO warm amber/gold tones, NO glowy "
        "bokeh. Instead: a cool blue-grey or neutral steel-toned gradient suggesting depth, like "
        "cold morning light through a window at a low angle, OR an extreme macro of dark "
        "concrete/stone/brushed-metal texture lit with a single cool white key light, OR a flat "
        "near-black gradient with subtle cool undertone and visible film grain. The feeling is "
        "'calm analytical confidence' — restrained, precise, no warmth, no romance. Desaturated "
        "toward cool/neutral, single hard key light, true near-black #04060a crushing to black in "
        "shadows. Avoid any color temperature above 5000K."
    ),
    "plansmart": (
        "Pure atmospheric darkness with a single soft light source — e.g. a subtle gradient "
        "suggesting dawn light entering a dark room, OR an out-of-focus plant leaf catching rim "
        "light, OR a pure abstract gradient with film grain and no objects at all. Premium, calm, "
        "'quiet confidence' feeling. The most minimal of the three. Refined neutral light."
    ),
}


def build_textfree_prompt(post: dict[str, Any], visual_text: dict[str, Any] | None = None) -> str:
    """Szöveg-NÉLKÜLI Muapi prompt (Phase 12.6): fotó-technikai irány + EGY objektum.

    A magyar szöveget NEM a Flux rendereli (halandzsa lenne) — azt PIL teszi rá utólag.
    A konkrét film/megvilágítás/DOF irány + a screen/UI/kód teljes tiltása a "generic AI-art" /
    "pseudo-code panel" panaszokat célozza.
    """
    voice = post.get("voice", "")
    scene = VOICE_SCENE.get(voice, "Pure abstract dark gradient with film grain, no objects. ONE focal element.")
    return f"{scene}\n{TEXTFREE_COMMON}"


@lru_cache(maxsize=1)
def _composer():
    from src.visuals.text_overlay import TextOverlayComposer

    return TextOverlayComposer()


async def compose_visual(post: dict[str, Any]) -> dict[str, Any]:
    """Phase 12.5 pipeline: szöveg-mentes alapkép (Muapi) → magyar PIL overlay → feltöltés.

    Visszaad: {"image_url" (végleges, komponált), "base_image_url" (nyers Muapi),
    "visual_prompt", "visual_text", "model_used", "cost_usd"}.
    """
    from src.visuals.uploader import upload_visual

    voice = post.get("voice")
    if voice not in VOICES:
        raise ValueError(f"Ismeretlen/hiányzó voice: {voice!r}. Várt: {sorted(VOICES)}")

    model = post.get("visual_model") or muapi_client.DEFAULT_MODEL
    aspect_ratio = post.get("aspect_ratio") or "1:1"

    visual_text = await extract_visual_text(_post_text(post))
    prompt = build_textfree_prompt(post, visual_text)
    result = await muapi_client.generate(prompt, model=model, aspect_ratio=aspect_ratio)
    base_url = result.image_url
    logger.info("[%s] szöveg-mentes alapkép kész | overlay: '%s'", voice, visual_text.get("main_text", ""))

    pid = post.get("id") or uuid.uuid4().hex[:12]
    out_path = str((Path(__file__).resolve().parents[2] / "assets" / "generated" / f"visual_{pid}.png"))
    composer = _composer()
    composed_path = await asyncio.to_thread(
        composer.compose, base_url, visual_text.get("main_text", ""),
        visual_text.get("sub_text"), visual_text.get("stat"), voice, out_path,
    )
    final_url = await asyncio.to_thread(upload_visual, composed_path, f"visual_{pid}.png")

    if post.get("id"):
        _record_cost_safe(post["id"], result)

    return {
        "image_url": final_url,
        "base_image_url": base_url,
        "local_path": composed_path,
        "visual_prompt": prompt,
        "visual_text": visual_text,
        "model_used": result.model,
        "cost_usd": result.cost_usd,
    }


def _record_cost_safe(post_id: str, result: muapi_client.GenerationResult) -> None:
    """A Muapi hívás költségének logolása a costs táblába — hiba esetén csak warn."""
    try:
        from src.storage import posts as posts_store

        posts_store.record_cost(
            post_id=post_id,
            model=result.model,
            cost_usd=result.cost_usd,
            request_id=result.request_id,
            kind="muapi_image",
        )
    except Exception as exc:
        logger.warning("Költség-log kihagyva (post=%s): %s", post_id, str(exc)[:100])


async def _demo() -> int:
    import json

    sample = {
        "id": None,
        "voice": "david",
        "platform": "linkedin",
        "content": (
            "Tegnap este átírtam a queue logikát. A poll loop 16 percről 3,5 másodpercre "
            "esett, 0 új alkalmazottal. Itt a diff lényege."
        ),
        "hashtags": ["#BuildInPublic"],
    }
    out = await generate_visual(sample)
    logging.getLogger(__name__).info("%s", json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import asyncio
    import os

    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    raise SystemExit(asyncio.run(_demo()))
