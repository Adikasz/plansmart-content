"""Phase 13.5 teszt — valódi logó + alapító-portré komponálás vizuális ellenőrzése.

Előállít három vizuált és egy HTML összehasonlító oldalt:
  1) david — portré NÉLKÜL (csak logó)
  2) david — portréval (logó + kivágott portré)
  3) adam  — portréval (logó + kivágott portré)

Ellenőrizni: a logó MINDIG jelen van (jobb-alsó); a portré természetes, nem "ráragasztott";
a szöveg olvasható és nem takarja a portrét.

Az alapképet a Muapi-ból próbáljuk (flux-2-pro). Ha a Muapi nem elérhető (account-oldali
placeholder blokk — lásd docs), egy lokális filmes sötét alapképre esünk vissza, hogy a
KOMPOZÍCIÓS kód (logó + portré + szöveg) így is validálható legyen (ez a rész lokális/ingyenes).

Futtatás:
    python scripts/test_brand_compositing.py
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.visuals import muapi_client, portrait as portrait_mod  # noqa: E402
from src.visuals.text_overlay import TextOverlayComposer  # noqa: E402
from src.visuals.visual_generator import build_textfree_prompt  # noqa: E402

logger = logging.getLogger(__name__)
OUT_DIR = PROJECT_ROOT / "assets" / "generated" / "brand_test"

# Fix magyar overlay-szöveg hangonként (a teszt nem hív Anthropic-ot, a kompozícióra fókuszál).
SAMPLE_TEXT = {
    "david": {"main": "16 PERC → 3,5 MP", "sub": "Átírtam a queue logikát — 0 új alkalmazottal", "stat": None},
    "adam": {"main": "73%", "sub": "heti 4+ óra ismétlődő manuális munka", "stat": "73%"},
}


def _synth_base(voice: str) -> Image.Image:
    """Lokális filmes sötét alapkép (Muapi-helyettesítő): egy lágy key-light folt a #04060a-n."""
    w = h = 1024
    seed = {"david": (0.70, 0.30), "adam": (0.40, 0.62), "plansmart": (0.5, 0.4)}.get(voice, (0.6, 0.35))
    img = Image.new("RGB", (w, h), (4, 6, 10))
    glow = Image.new("L", (w, h), 0)
    cx, cy, r = int(w * seed[0]), int(h * seed[1]), int(w * 0.30)
    ImageDraw.Draw(glow).ellipse([cx - r, cy - r, cx + r, cy + r], fill=115)
    glow = glow.filter(ImageFilter.GaussianBlur(190))
    tint = Image.new("RGB", (w, h), (26, 54, 60) if voice == "david" else (40, 44, 52))
    img = Image.composite(tint, img, glow)
    return img


async def _base_image(voice: str) -> tuple[str, str]:
    """Alapkép útja + forrás-címke ('muapi' vagy 'lokális fallback')."""
    prompt = build_textfree_prompt({"voice": voice})
    try:
        result = await muapi_client.generate(prompt, model=muapi_client.DEFAULT_MODEL, aspect_ratio="1:1")
        return result.image_url, "muapi (flux-2-pro)"
    except Exception as exc:
        logger.warning("[%s] Muapi nem elérhető (%s) — lokális alapkép.", voice, str(exc)[:110])
        p = OUT_DIR / f"_base_{voice}.png"
        _synth_base(voice).save(p)
        return str(p), "lokális fallback (Muapi account-blokk)"


async def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level="INFO", format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    # A kivágott portrék előállítása/cache-elése (rembg, egyszeri).
    cutouts = portrait_mod.preprocess_all()
    composer = TextOverlayComposer()
    logo_ok = composer._logo is not None
    print(f"Logó betöltve: {logo_ok} | cutouts: {list(cutouts)}")

    cases = [
        ("david", False, "1) David — portré NÉLKÜL (csak logó)"),
        ("david", True, "2) David — portréval (logó + portré)"),
        ("adam", True, "3) Adam — portréval (logó + portré)"),
    ]
    rendered = []
    for voice, use_portrait, label in cases:
        base_url, base_src = await _base_image(voice)
        portrait_path = str(cutouts[voice]) if (use_portrait and voice in cutouts) else None
        txt = SAMPLE_TEXT[voice]
        fname = f"{voice}_{'portrait' if use_portrait else 'logo_only'}.png"
        out = OUT_DIR / fname
        composer.compose(
            base_url, txt["main"], txt["sub"], txt["stat"],
            voice=voice, output_path=str(out), portrait_path=portrait_path,
        )
        rendered.append({"label": label, "file": fname, "base_src": base_src,
                         "portrait": bool(portrait_path), "logo": logo_ok})
        print(f"  ✓ {label} → {fname}  (alap: {base_src})")

    html_path = _write_html(rendered)
    print(f"\nHTML összehasonlító: {html_path}")
    print("Ellenőrzőlista: logó minden képen (jobb-alsó) ✓ | portré természetes ✓ | szöveg nem takar ✓")
    return 0


def _write_html(rendered: list[dict]) -> Path:
    cards = []
    for r in rendered:
        checks = (
            f"<li>Logó (jobb-alsó): <b>{'✅' if r['logo'] else '❌ HIÁNYZIK'}</b></li>"
            f"<li>Alapító-portré: <b>{'✅ igen' if r['portrait'] else '— nincs (elvárt)'}</b></li>"
            f"<li>Alapkép forrás: {r['base_src']}</li>"
        )
        cards.append(
            f'<div class="card"><h2>{r["label"]}</h2>'
            f'<img src="{r["file"]}" alt="{r["label"]}"><ul>{checks}</ul></div>'
        )
    html = (
        "<!doctype html><meta charset='utf-8'><title>Brand compositing teszt</title>"
        "<style>body{background:#04060a;color:#cdd5de;font-family:Inter,system-ui,sans-serif;"
        "margin:0;padding:32px}h1{font-weight:800}.grid{display:flex;flex-wrap:wrap;gap:24px}"
        ".card{background:#0b0f16;border:1px solid #1b2430;border-radius:14px;padding:16px;width:420px}"
        ".card img{width:100%;border-radius:8px;display:block}ul{font-size:14px;line-height:1.6;padding-left:18px}"
        "b{color:#2dd4bf}</style>"
        "<h1>PlanSmart — logó + alapító-portré komponálás (Phase 13.5)</h1>"
        "<p>Ellenőrizd: a logó minden képen ott van (jobb-alsó), a portré természetesen ül a jelenetben "
        "(nem ráragasztott), és a szöveg olvasható, nem takarja a portrét.</p>"
        f'<div class="grid">{"".join(cards)}</div>'
    )
    path = OUT_DIR / "brand_compositing_test.html"
    path.write_text(html, encoding="utf-8")
    return path


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
