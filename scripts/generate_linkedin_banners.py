"""LinkedIn banner generátor (angol, Phase 14) — 2 banner, 1584x396.

Kétlépéses minta hangonként (mint a poszt-vizuáloknál):
  1) Muapi flux-2-pro SZÖVEG NÉLKÜLI, filmes háttér (a Flux nem renderel betűhű szöveget).
  2) PIL kompozíció: valódi logó + betűhű angol szöveg (Bricolage Grotesque Bold / Inter /
     Fragment Mono), pontos pozíció/opacitás szerint.

A Muapi elérhetetlensége esetén (account-blokk) lokális, filmes sötét bannerhátterre esünk
vissza, hogy a KOMPOZÍCIÓS kód (logó + tipográfia + elrendezés) így is validálható legyen.

Futtatás:
    .venv/Scripts/python.exe scripts/generate_linkedin_banners.py
"""
from __future__ import annotations

import asyncio
import logging
import math
import sys
from io import BytesIO
from pathlib import Path

import httpx
from fontTools.ttLib import TTFont
from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from src.visuals import muapi_client  # noqa: E402

logger = logging.getLogger(__name__)

# ── Kimenet / méret ────────────────────────────────────────────────────
OUT_DIR = PROJECT_ROOT / "assets" / "generated"
FONTS_DIR = PROJECT_ROOT / "assets" / "fonts"
TTF_DIR = FONTS_DIR / "ttf"
BRAND_DIR = PROJECT_ROOT / "assets" / "brand"
BANNER_W, BANNER_H = 1584, 396

# ── Színek (RGBA) ──────────────────────────────────────────────────────
BG = (4, 6, 10)                    # #04060a
WHITE = (255, 255, 255, 255)
LIGHT_GREY = (205, 213, 222, 255)
TEAL = (45, 212, 191, 255)

# ── Fontok ─────────────────────────────────────────────────────────────
# A márka Bricolage Grotesque woff2-je fonttools-szal TTF-re konvertálható és PIL-lel
# betölthető (variable wght tengely, 'Bold' named instance) — így betűhű a headline.
BRICOLAGE_WOFF2 = FONTS_DIR / "bricolage-grotesque-latin-wght-normal.DLoelf7F.woff2"


def _ensure_bricolage_ttf() -> Path | None:
    out = TTF_DIR / "bricolage.ttf"
    if out.exists():
        try:
            ImageFont.truetype(str(out), 20)
            return out
        except Exception:
            out.unlink(missing_ok=True)
    try:
        TTF_DIR.mkdir(parents=True, exist_ok=True)
        f = TTFont(str(BRICOLAGE_WOFF2))  # fonttools auto-detektálja a woff2-t (brotli)
        f.flavor = None
        f.save(str(out))
        ImageFont.truetype(str(out), 20)
        return out
    except Exception as exc:
        logger.warning("Bricolage konverzió sikertelen (%s) — Inter fallback.", str(exc)[:120])
        return None


def _font(path: Path, size: int, weight: str | None = None) -> ImageFont.FreeTypeFont:
    fnt = ImageFont.truetype(str(path), size)
    if weight:
        try:
            fnt.set_variation_by_name(weight)
        except Exception:
            pass
    return fnt


class Fonts:
    def __init__(self) -> None:
        self.bricolage = _ensure_bricolage_ttf()
        self.inter = TTF_DIR / "inter.ttf"
        self.fragment = TTF_DIR / "fragment.ttf"
        self.headline_face = "Bricolage Grotesque Bold" if self.bricolage else "Inter Bold (fallback)"

    def headline(self, size: int) -> ImageFont.FreeTypeFont:
        if self.bricolage:
            return _font(self.bricolage, size, "Bold")
        return _font(self.inter, size, "Bold")

    def body(self, size: int) -> ImageFont.FreeTypeFont:
        return _font(self.inter, size, "Regular")

    def mono(self, size: int) -> ImageFont.FreeTypeFont:
        return _font(self.fragment, size)


# ── Muapi háttér-promptok (SZÖVEG NÉLKÜL) ──────────────────────────────
COMMON_NEG = (
    "ABSOLUTELY NO text, NO letters, NO words, NO numbers, NO typography, NO logos, NO UI, "
    "NO people. Photographic, cinematic, Kodak Portra film grain. TRUE near-black #04060a "
    "background — verify shadows crush to black, NOT dark grey. Ultra-wide banner composition."
)

BANNER1_PROMPT = (
    "Cinematic ultra-wide technology banner. Deep near-black #04060a field. On the RIGHT "
    "two-thirds: an intricate teal-glowing circuit-board / PCB trace pattern receding into "
    "shallow-focus darkness, delicate luminous nodes and connective lines in cool teal-cyan, "
    "like a macro of a glowing motherboard edge under a single dramatic key light. The LEFT "
    "third stays simple, dark and uncluttered (reserved for text). Moody, precise, "
    "workshop-at-night, high-end engineering feeling. " + COMMON_NEG
)

BANNER2_PROMPT = (
    "Restrained premium enterprise banner, ultra-wide. Pure atmospheric near-black #04060a "
    "darkness with a single soft, refined light gradient suggesting depth and quiet confidence. "
    "Very minimal: a subtle cool teal-tinged glow and faint out-of-focus circuit bokeh on the "
    "right side, most of the frame calm negative space. Corporate, sophisticated, understated, "
    "'operating system for business' feeling. The most minimal composition. " + COMMON_NEG
)

# LinkedIn banner 4:1 — a legszélesebbtől próbáljuk, majd cover-croppolunk 1584x396-ra.
_AR_CANDIDATES = ["4:1", "21:9", "16:9"]


async def _muapi_bg(prompt: str, tag: str) -> tuple[Image.Image, str]:
    """Muapi flux-2-pro háttér (2k). Sikertelenség esetén lokális filmes fallback.

    A nyers hátteret lemezre cache-eljük (assets/generated/_bg_<tag>.png), így az
    újra-komponálás NEM hív Muapit újra (ingyenes, gyors). Törléssel kényszeríthető új gen.
    """
    cache = OUT_DIR / f"_bg_{tag.split('/')[0]}.png"
    if cache.exists():
        logger.info("[%s] cache-elt háttér: %s", tag, cache.name)
        return Image.open(cache).convert("RGB"), f"cache ({cache.name})"
    async with httpx.AsyncClient() as client:
        for ar in _AR_CANDIDATES:
            try:
                res = await muapi_client.generate(
                    prompt, model="flux-2-pro", aspect_ratio=ar,
                    resolution="2k", client=client,
                )
                r = await client.get(res.image_url, follow_redirects=True, timeout=60.0)
                r.raise_for_status()
                img = Image.open(BytesIO(r.content)).convert("RGB")
                logger.info("[%s] Muapi háttér kész (ar=%s, %dx%d, $%s)",
                            tag, ar, img.width, img.height, res.cost_usd)
                img.save(cache)  # cache a jövőbeli újra-komponáláshoz
                return img, f"muapi flux-2-pro ({ar}, 2k)"
            except Exception as exc:
                logger.warning("[%s] Muapi ar=%s hiba: %s", tag, ar, str(exc)[:140])
    logger.warning("[%s] Muapi nem elérhető — lokális filmes fallback háttér.", tag)
    return _synth_bg(tag), "lokális fallback (Muapi account-blokk)"


def _synth_bg(tag: str) -> Image.Image:
    """Lokális filmes sötét banner-háttér: #04060a + teal glow + eljárásos áramköri nyomvonalak
    a jobb kétharmadon (Muapi-helyettesítő, hogy a kompozíció validálható legyen)."""
    w, h = BANNER_W, BANNER_H
    img = Image.new("RGB", (w, h), BG)
    # Lágy teal key-light folt a jobb oldalon.
    glow = Image.new("L", (w, h), 0)
    cx, cy, r = int(w * 0.72), int(h * 0.45), int(h * 1.15)
    ImageDraw.Draw(glow).ellipse([cx - r, cy - r, cx + r, cy + r], fill=90)
    glow = glow.filter(ImageFilter.GaussianBlur(120))
    tint = Image.new("RGB", (w, h), (16, 60, 66))
    img = Image.composite(tint, img, glow)
    # Eljárásos áramköri nyomvonalak a jobb kétharmadon (determinisztikus, seed nélkül).
    traces = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    td = ImageDraw.Draw(traces)
    line = (45, 212, 191, 130)
    for i in range(26):
        x0 = int(w * 0.36) + (i * 43) % int(w * 0.6)
        y0 = (i * 71) % h
        seg, x, y = 0, x0, y0
        while seg < 5:
            horiz = (i + seg) % 2 == 0
            step = 30 + (i * 13 + seg * 29) % 90
            x2 = min(w - 4, x + step) if horiz else x
            y2 = y if horiz else max(4, min(h - 4, y + (step if (i % 2) else -step)))
            td.line([x, y, x2, y2], fill=line, width=2)
            td.ellipse([x2 - 4, y2 - 4, x2 + 4, y2 + 4], outline=line, width=2)
            x, y, seg = x2, y2, seg + 1
    traces = traces.filter(ImageFilter.GaussianBlur(0.6))
    img = Image.alpha_composite(img.convert("RGBA"), traces).convert("RGB")
    # Finom film-grain.
    noise = Image.effect_noise((w, h), 16).convert("L")
    grain = Image.merge("RGBA", (noise, noise, noise, noise.point(lambda p: int(p * 0.06))))
    return Image.alpha_composite(img.convert("RGBA"), grain).convert("RGB")


def _cover(img: Image.Image, tw: int, th: int) -> Image.Image:
    """Cover-crop: kitölti a (tw,th) vásznat torzítás nélkül, középre igazítva."""
    sw, sh = img.size
    scale = max(tw / sw, th / sh)
    nw, nh = math.ceil(sw * scale), math.ceil(sh * scale)
    img = img.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - tw) // 2, (nh - th) // 2
    return img.crop((left, top, left + tw, top + th))


# ── Logó előkészítés ───────────────────────────────────────────────────
def _glow_mark() -> Image.Image:
    """A teal 'P' glow-jel (Logo main.png): a sötét glow-t alfába olvasztjuk, hogy a
    #04060a-n fényként (ne dobozként) üljön; margó levágva."""
    logo = Image.open(BRAND_DIR / "Logo main.png").convert("RGBA")
    alpha = ImageChops.multiply(logo.getchannel("A"), logo.convert("L"))
    logo.putalpha(alpha)
    bbox = alpha.getbbox()
    return logo.crop(bbox) if bbox else logo


def _wordmark_light() -> Image.Image:
    """A világos/teal 'PlanSmart' wordmark (Logo4.png) — átlátszó hátterű, sötét bannerre való."""
    wm = Image.open(BRAND_DIR / "Logo4.png").convert("RGBA")
    bbox = wm.getchannel("A").getbbox()
    return wm.crop(bbox) if bbox else wm


def _paste(img: Image.Image, layer: Image.Image, box: tuple[int, int], opacity: float) -> None:
    if opacity < 1.0:
        layer = layer.copy()
        layer.putalpha(layer.getchannel("A").point(lambda p: int(p * opacity)))
    img.alpha_composite(layer, box)


# ── Szöveg-segédek ─────────────────────────────────────────────────────
_M = ImageDraw.Draw(Image.new("RGB", (8, 8)))


def _wrap(text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
    words, lines, cur = text.split(), [], ""
    for wd in words:
        trial = f"{cur} {wd}".strip()
        if _M.textlength(trial, font=font) <= max_w or not cur:
            cur = trial
        else:
            lines.append(cur)
            cur = wd
    if cur:
        lines.append(cur)
    return lines


def _fit_headline(fonts: Fonts, text: str, start: int, max_w: int, min_size: int = 40):
    """Legnagyobb Bricolage Bold méret, amivel a headline elfér max_w-ben (≤ ~2-3 sor)."""
    size = start
    while size > min_size:
        f = fonts.headline(size)
        lines = _wrap(text, f, max_w)
        if lines and max(_M.textlength(ln, font=f) for ln in lines) <= max_w:
            return f, lines
        size = int(size * 0.94)
    f = fonts.headline(min_size)
    return f, _wrap(text, f, max_w)


def _draw_text(img: Image.Image, lines: list[str], font, xy, fill, anchor="la",
               line_gap: float = 1.14, shadow_blur: int = 10, glow=None):
    """Szöveg lágy árnyékkal (olvashatóság), opcionális szín-glow-val."""
    asc, desc = font.getmetrics()
    lh = int((asc + desc) * line_gap)
    x, y = xy
    # 1) sötét árnyék
    sh = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(sh)
    yy = y
    for ln in lines:
        sd.text((x, yy), ln, font=font, fill=(0, 0, 0, 180), anchor=anchor)
        yy += lh
    img.alpha_composite(sh.filter(ImageFilter.GaussianBlur(shadow_blur)))
    # 2) opcionális glow
    if glow:
        gl = Image.new("RGBA", img.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(gl)
        yy = y
        for ln in lines:
            gd.text((x, yy), ln, font=font, fill=glow, anchor=anchor)
            yy += lh
        img.alpha_composite(gl.filter(ImageFilter.GaussianBlur(6)))
    # 3) éles szöveg
    d = ImageDraw.Draw(img)
    yy = y
    for ln in lines:
        d.text((x, yy), ln, font=font, fill=fill, anchor=anchor)
        yy += lh
    return y + lh * len(lines)


def _block_height(lines, font, line_gap=1.14) -> int:
    asc, desc = font.getmetrics()
    return int((asc + desc) * line_gap) * len(lines)


def _left_scrim(img: Image.Image, frac: float = 0.46, strength: float = 0.92) -> None:
    """Bal oldali sötét gradiens-scrim (a bal-harmad szöveg olvashatóságáért)."""
    w, h = img.size
    band = int(w * frac)
    col = bytearray(w)
    for x in range(w):
        a = strength * (1 - x / band) if x < band else 0.0
        col[x] = int(max(0, min(255, a * 255)))
    ramp = Image.frombytes("L", (w, 1), bytes(col)).resize((w, h))
    solid = Image.new("RGBA", img.size, (*BG, 255))
    empty = Image.new("RGBA", img.size, (0, 0, 0, 0))
    img.alpha_composite(Image.composite(solid, empty, ramp))


# ── Banner 1: Dávid személyes ──────────────────────────────────────────
def compose_banner1(bg: Image.Image, fonts: Fonts, out: Path) -> None:
    img = _cover(bg, BANNER_W, BANNER_H).convert("RGBA")
    _left_scrim(img, frac=0.46, strength=0.92)

    margin = 88
    col_w = int(BANNER_W * 0.40) - margin  # bal terület a headline-tördeléshez (margóval)

    sub_font = fonts.body(27)
    sub_lines = _wrap("Building Agentic AI Systems — Python, Claude, Supabase", sub_font, col_w)
    tag_font = fonts.mono(21)
    tag = "Founder @ PlanSmart"
    gap_hs, gap_st = 22, 18
    asc, desc = tag_font.getmetrics()
    h_tag = asc + desc
    h_sub = _block_height(sub_lines, sub_font, 1.22)

    # Headline: szélességre ÉS teljes blokk-magasságra illesztünk (különben a 3. sor / tag kilóg).
    max_block = BANNER_H - 2 * 34  # 34px függőleges padding fent-lent
    size = 78
    while size > 40:
        head_font = fonts.headline(size)
        head_lines = _wrap("AI PRODUCT ENGINEER", head_font, col_w)
        h_head = _block_height(head_lines, head_font, 1.06)
        total = h_head + gap_hs + h_sub + gap_st + h_tag
        fits_w = head_lines and max(_M.textlength(ln, font=head_font) for ln in head_lines) <= col_w
        if fits_w and total <= max_block:
            break
        size = int(size * 0.94)

    y = (BANNER_H - total) // 2
    y = _draw_text(img, head_lines, head_font, (margin, y), WHITE, "la",
                   line_gap=1.06, shadow_blur=12, glow=(45, 212, 191, 85))
    y += gap_hs
    y = _draw_text(img, sub_lines, sub_font, (margin, y), LIGHT_GREY, "la",
                   line_gap=1.22, shadow_blur=7)
    y += gap_st
    _draw_text(img, [tag], tag_font, (margin, y), TEAL, "la", line_gap=1.0, shadow_blur=6)

    # Logó-jel: jobb-alsó, 8% szélesség, 65% opacitás.
    mark = _glow_mark()
    tw = int(BANNER_W * 0.08)
    th = int(mark.height * tw / mark.width)
    mark = mark.resize((tw, th), Image.LANCZOS)
    pad = 30
    _paste(img, mark, (BANNER_W - tw - pad, BANNER_H - th - pad), 0.65)

    img.convert("RGB").save(out, "PNG")
    logger.info("Banner 1 mentve: %s (headline font: %s)", out.name, fonts.headline_face)


# ── Banner 2: PlanSmartAI cégoldal ─────────────────────────────────────
def compose_banner2(bg: Image.Image, fonts: Fonts, out: Path) -> None:
    img = _cover(bg, BANNER_W, BANNER_H).convert("RGBA")
    # Enyhe, egyenletes sötétítés + bal-scrim a prémium/olvasható look-ért.
    img.alpha_composite(Image.new("RGBA", img.size, (*BG, 70)))
    _left_scrim(img, frac=0.62, strength=0.72)

    # Prominens wordmark balra, függőlegesen középen (~22% szélesség, 85% opacitás).
    wm = _wordmark_light()
    lw = int(BANNER_W * 0.22)
    lh = int(wm.height * lw / wm.width)
    wm_r = wm.resize((lw, lh), Image.LANCZOS)

    margin = 96
    gap = 56
    main_max_w = BANNER_W - (margin + lw + gap) - 80
    main_font, main_lines = _fit_headline(fonts, "THE OPERATING SYSTEM FOR AUTONOMOUS BUSINESS",
                                          58, main_max_w, min_size=34)
    sub_font = fonts.body(28)
    sub_lines = _wrap("AI Automation for SMEs", sub_font, main_max_w)

    gap_ms = 20
    h_main = _block_height(main_lines, main_font, 1.1)
    h_sub = _block_height(sub_lines, sub_font, 1.2)
    text_total = h_main + gap_ms + h_sub

    # A logó + szöveg csoportot vízszintesen kiegyensúlyozzuk (bal margó a wordmarkhoz).
    logo_x = margin
    logo_y = (BANNER_H - lh) // 2
    _paste(img, wm_r, (logo_x, logo_y), 0.85)

    text_x = logo_x + lw + gap
    y = (BANNER_H - text_total) // 2
    y = _draw_text(img, main_lines, main_font, (text_x, y), WHITE, "la",
                   line_gap=1.1, shadow_blur=11, glow=(45, 212, 191, 70))
    y += gap_ms
    _draw_text(img, sub_lines, sub_font, (text_x, y), LIGHT_GREY, "la",
               line_gap=1.2, shadow_blur=7)

    img.convert("RGB").save(out, "PNG")
    logger.info("Banner 2 mentve: %s (headline font: %s)", out.name, fonts.headline_face)


async def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level="INFO", format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    fonts = Fonts()
    print(f"Headline font: {fonts.headline_face}")

    bg1, src1 = await _muapi_bg(BANNER1_PROMPT, "banner1/david")
    bg2, src2 = await _muapi_bg(BANNER2_PROMPT, "banner2/plansmart")

    out1 = OUT_DIR / "banner_david_en.png"
    out2 = OUT_DIR / "banner_plansmart_en.png"
    compose_banner1(bg1, fonts, out1)
    compose_banner2(bg2, fonts, out2)

    print(f"\nBanner 1 (Dávid):     {out1}  [háttér: {src1}]")
    print(f"Banner 2 (PlanSmart): {out2}  [háttér: {src2}]")

    # Side-by-side HTML preview.
    html = (
        "<!doctype html><meta charset='utf-8'><title>LinkedIn banners (EN)</title>"
        "<style>body{background:#04060a;color:#cdd5de;font-family:Inter,system-ui,sans-serif;"
        "margin:0;padding:32px}h1{font-weight:800}figure{margin:0 0 28px}img{width:100%;max-width:1584px;"
        "border:1px solid #1b2430;border-radius:10px;display:block}figcaption{font-size:14px;margin-top:8px;"
        "color:#8aa}</style><h1>PlanSmart — LinkedIn banners (English, Phase 14)</h1>"
        f"<figure><img src='{out1.name}'><figcaption>Banner 1 — Dávid personal · 1584×396 · háttér: {src1}"
        f"</figcaption></figure>"
        f"<figure><img src='{out2.name}'><figcaption>Banner 2 — PlanSmartAI company · 1584×396 · háttér: {src2}"
        f"</figcaption></figure>"
    )
    html_path = OUT_DIR / "banners_en_preview.html"
    html_path.write_text(html, encoding="utf-8")
    print(f"Preview: {html_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
