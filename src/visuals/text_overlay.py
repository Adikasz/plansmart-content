"""Szöveg-overlay rendszer — betűhű magyar szöveg Flux/Muapi alapképekre (Pillow).

A Flux/Muapi nem tud helyesen magyar szöveget renderelni (halandzsa betűk), ezért a képet
SZÖVEG NÉLKÜL generáljuk, majd a magyar szöveget PIL-lel komponáljuk rá — így betűhű.

A márka fontjai .woff2-ben vannak (assets/fonts), amit a FreeType/PIL nem olvas: ezeket
fonttools-szal TTF-re konvertáljuk (assets/fonts/ttf/ cache), a Bebas Neue-t letöltjük.

Önálló teszt:
    python -m src.visuals.text_overlay <base_image_url> [voice]
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFilter, ImageFont

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FONTS_DIR = PROJECT_ROOT / "assets" / "fonts"
TTF_DIR = FONTS_DIR / "ttf"
GENERATED_DIR = PROJECT_ROOT / "assets" / "generated"

# Teljes lefedettségű TTF-ek (Google Fonts) — a repo .woff2 fájljai unicode-range SUBSETek
# (a latin-ext csak a kiterjesztett jeleket tartalmazza, az alap latint NEM), így boxokat
# adnának. A teljes TTF-ek lefedik a magyar ékezeteket (á é í ó ö ő ú ü ű) + a → nyilat.
# Inter = a márka elsődleges fontja (variable, named weights: Regular/SemiBold/Bold/ExtraBold).
FONT_DOWNLOADS = {
    "inter": "https://github.com/google/fonts/raw/main/ofl/inter/Inter%5Bopsz,wght%5D.ttf",
    "fragment": "https://github.com/google/fonts/raw/main/ofl/fragmentmono/FragmentMono-Regular.ttf",
    "bebas": "https://github.com/google/fonts/raw/main/ofl/bebasneue/BebasNeue-Regular.ttf",
}

# Színek (RGBA)
WHITE = (255, 255, 255, 255)
LIGHT_GREY = (205, 213, 222, 255)
GREY = (150, 165, 180, 255)
TEAL = (45, 212, 191, 255)
AMBER = (245, 179, 66, 255)

DOWNLOAD_TIMEOUT_S = 30.0


def _valid_ttf(path: Path) -> bool:
    try:
        ImageFont.truetype(str(path), 20)
        return True
    except Exception:
        return False


def _ensure_ttf() -> dict[str, Path]:
    """Teljes lefedettségű TTF-ek letöltése (cache). Visszaad: face → ttf path."""
    TTF_DIR.mkdir(parents=True, exist_ok=True)
    paths: dict[str, Path] = {}
    for face, url in FONT_DOWNLOADS.items():
        ttf = TTF_DIR / f"{face}.ttf"
        if not ttf.exists() or not _valid_ttf(ttf):
            try:
                r = httpx.get(url, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_S)
                r.raise_for_status()
                ttf.write_bytes(r.content)
                if not _valid_ttf(ttf):
                    raise RuntimeError("a letöltött fájl nem érvényes TTF")
                logger.info("[overlay] font letöltve: %s (%d B)", face, len(r.content))
            except Exception as exc:
                logger.warning("[overlay] font letöltés hiba (%s): %s", face, str(exc)[:100])
                if ttf.exists() and not _valid_ttf(ttf):
                    ttf.unlink(missing_ok=True)
                continue
        paths[face] = ttf
    return paths


class TextOverlayComposer:
    """Magyar szöveg komponálása Flux alapképekre, voice-specifikus elrendezéssel."""

    def __init__(self) -> None:
        self.fonts_dir = FONTS_DIR
        self._ttf = _ensure_ttf()
        self._cov = self._build_coverage()
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    def _build_coverage(self) -> dict[str, set[int]]:
        """face → a font által lefedett unicode kódpontok (a glyph-fallbackhez)."""
        cov: dict[str, set[int]] = {}
        for face, path in self._ttf.items():
            try:
                from fontTools.ttLib import TTFont

                cov[face] = set(TTFont(str(path)).getBestCmap().keys())
            except Exception:
                cov[face] = set()
        return cov

    def _covers(self, face: str, text: str) -> bool:
        """Lefedi-e a font az összes (nem-szóköz) karaktert? (pl. Bebas-ban nincs → nyíl)."""
        cp = self._cov.get(face if face != "bricolage" else "inter", set())
        if not cp:
            return True
        return all(ord(ch) in cp for ch in (text or "") if ch.strip())

    def _font_for(self, face: str, text: str, size: int, weight: str | None = None) -> ImageFont.FreeTypeFont:
        """A kért face, ha lefedi a szöveget; különben Inter (teljes lefedettség)."""
        if not self._covers(face, text):
            return self._font("inter", size, weight or "ExtraBold")
        return self._font(face, size, weight)

    _MEASURE = ImageDraw.Draw(Image.new("RGB", (8, 8)))

    def _fit_font(self, face, text, size, max_w, weight=None) -> ImageFont.FreeTypeFont:
        """A legnagyobb (≤size) font, amivel a 'text' egy sorban elfér max_w-ben (pl. stat)."""
        while size > 24:
            font = self._font_for(face, text, size, weight)
            if self._MEASURE.textlength(text, font=font) <= max_w:
                return font
            size = int(size * 0.9)
        return self._font_for(face, text, size, weight)

    def _hard_wrap(self, text, font, max_w) -> list[str]:
        """Szó-tördelés, majd a max_w-nél szélesebb tokeneket karakterenként is megtöri."""
        out: list[str] = []
        for token in self._wrap(self._MEASURE, text, font, max_w):
            if self._MEASURE.textlength(token, font=font) <= max_w:
                out.append(token)
                continue
            cur = ""  # egyetlen hosszú szó (pl. ADMINISZTRÁCIÓ) karakter-szintű törése
            for ch in token:
                if self._MEASURE.textlength(cur + ch, font=font) <= max_w or not cur:
                    cur += ch
                else:
                    out.append(cur)
                    cur = ch
            if cur:
                out.append(cur)
        return out

    def _fit_lines(self, face, upper_text, target_size, max_w, weight=None,
                   min_ratio=0.55, max_lines=3) -> tuple[ImageFont.FreeTypeFont, list[str]]:
        """Shrink-to-fit a main_text-hez: csökkenti a méretet 5%-onként, amíg minden sor elfér.

        Egy hosszú összetett szó (ami szóközre nem tördelhető) így nem lóg ki. A min méret
        alatt karakter-szintű tördelés (max_lines-ig). Visszaad: (font, sorok).
        """
        min_size = max(44, int(target_size * min_ratio))
        size = target_size
        while True:
            font = self._font_for(face, upper_text, size, weight)
            lines = self._wrap(self._MEASURE, upper_text, font, max_w)
            if lines and max(self._MEASURE.textlength(ln, font=font) for ln in lines) <= max_w:
                return font, lines
            if size <= min_size:
                break
            size = max(min_size, int(size * 0.95))  # 5%-os csökkentés a min-ig
        # Min méret elérve, még mindig kilóg → karakter-szintű tördelés (max_lines-ig).
        font = self._font_for(face, upper_text, min_size, weight)
        return font, self._hard_wrap(upper_text, font, max_w)[:max_lines]

    # ── Font betöltés (méret px-ben, opcionális named weight: Inter variable) ──
    @lru_cache(maxsize=64)
    def _font(self, face: str, size: int, weight: str | None = None) -> ImageFont.FreeTypeFont:
        if face == "bricolage":  # a Bricolage variable TTF nem tölthető PIL-be → Inter (márka-elsődleges)
            face = "inter"
        path = self._ttf.get(face) or self._ttf.get("inter") or next(iter(self._ttf.values()), None)
        if path is None:
            return ImageFont.load_default()
        font = ImageFont.truetype(str(path), size)
        if weight:
            try:
                font.set_variation_by_name(weight)  # 'Regular'|'SemiBold'|'Bold'|'ExtraBold'
            except Exception:
                pass  # nem variable / nincs ilyen instance — alap marad
        return font

    # ── Szöveg-tördelés a megadott max szélességre ─────────────────────
    @staticmethod
    def _wrap(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, max_w: int) -> list[str]:
        words = (text or "").split()
        if not words:
            return []
        lines, cur = [], words[0]
        for w in words[1:]:
            trial = f"{cur} {w}"
            if draw.textlength(trial, font=font) <= max_w:
                cur = trial
            else:
                lines.append(cur)
                cur = w
        lines.append(cur)
        return lines

    # ── Gradiens overlay-ek ────────────────────────────────────────────
    @staticmethod
    def _gradient(size, *, top=0.0, bottom=0.0, full=0.0) -> Image.Image:
        """Sötét #04060a gradiens RGBA réteg — soronkénti alpha-rámpa (gyors, nem per-pixel).

        top/bottom: a kép tetejére/aljára eső sötétítés erőssége (0-1, a magasság ~42%-án
        elhalva); full: egyenletes sötétítés az egész képre.
        """
        w, h = size
        band = max(1, int(h * 0.42))
        col = bytearray(h)
        for y in range(h):
            a = 255 * full
            if top and y < band:
                a = max(a, 255 * top * (1 - y / band))
            if bottom and y > h - band:
                a = max(a, 255 * bottom * (1 - (h - y) / band))
            col[y] = int(max(0, min(255, a)))
        ramp = Image.frombytes("L", (1, h), bytes(col)).resize((w, h))
        solid = Image.new("RGBA", size, (4, 6, 10, 255))
        layer = Image.new("RGBA", size, (0, 0, 0, 0))
        return Image.composite(solid, layer, ramp)

    # ── Fő belépési pont ───────────────────────────────────────────────
    def compose(
        self,
        base_image_url: str,
        main_text: str,
        sub_text: str | None = None,
        stat: str | None = None,
        voice: str = "david",
        output_path: str | None = None,
    ) -> str:
        """Letölti az alapképet, ráírja a magyar szöveget, menti, és visszaadja a lokális utat."""
        # BUG2 fix: ne rajzoljuk ki kétszer ugyanazt a számot. Ha a stat megegyezik a
        # main_text-tel (kis/nagybetűtől függetlenül), vagy a main_text már tartalmazza,
        # akkor a stat-ot elhagyjuk — egyszer jelenik meg, a main pozícióban.
        if stat:
            s_norm = stat.strip().lower()
            m_norm = (main_text or "").strip().lower()
            if s_norm and (s_norm == m_norm or s_norm in m_norm):
                stat = None

        img = self._load_base(base_image_url).convert("RGBA")
        w, h = img.size
        s = w / 1024.0  # méretskála (a pt értékek 1024px-re vannak hangolva)

        layer_fn = {
            "david": lambda: self._gradient(img.size, bottom=0.85),
            "adam": lambda: self._gradient(img.size, top=0.7, bottom=0.8),
            "plansmart": lambda: self._gradient(img.size, full=0.6),
        }.get(voice, lambda: self._gradient(img.size, bottom=0.8))
        img = Image.alpha_composite(img, layer_fn())

        if voice == "david":
            self._layout_david(img, main_text, sub_text, stat, s)
        elif voice == "adam":
            self._layout_adam(img, main_text, sub_text, stat, s)
        else:
            self._layout_plansmart(img, main_text, sub_text, s)

        out = Path(output_path) if output_path else (GENERATED_DIR / "overlay_tmp.png")
        out.parent.mkdir(parents=True, exist_ok=True)
        img.convert("RGB").save(out, "PNG")
        return str(out)

    def _load_base(self, url_or_path: str) -> Image.Image:
        if str(url_or_path).startswith("http"):
            r = httpx.get(url_or_path, follow_redirects=True, timeout=DOWNLOAD_TIMEOUT_S)
            r.raise_for_status()
            from io import BytesIO

            return Image.open(BytesIO(r.content))
        return Image.open(url_or_path)

    # ── Lágy sötét árnyék a szöveg mögé (kontraszt/olvashatóság a változó hátteren) ──
    def _shadow(self, img, lines, font, xy, anchor, lh, blur, alpha=160):
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        x, y = xy
        for ln in lines:
            d.text((x, y), ln, font=font, fill=(0, 0, 0, alpha), anchor=anchor)
            y += lh
        img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(blur)))

    # ── Glow-os szövegréteg (külön rétegen, hogy a blur ne mossa el a fő szöveget) ──
    def _text_with_glow(self, img, lines, font, xy, fill, anchor, glow_color, blur, line_gap=1.1):
        ascent, descent = font.getmetrics()
        lh = int((ascent + descent) * line_gap)
        # 1) sötét kontraszt-árnyék, 2) szín-glow, 3) éles fő szöveg
        self._shadow(img, lines, font, xy, anchor, lh, blur=int(blur * 1.6), alpha=170)
        glow_layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow_layer)
        x, y = xy
        for ln in lines:
            gd.text((x, y), ln, font=font, fill=glow_color, anchor=anchor)
            y += lh
        img.alpha_composite(glow_layer.filter(ImageFilter.GaussianBlur(blur)))
        draw = ImageDraw.Draw(img)
        x, y = xy
        for ln in lines:
            draw.text((x, y), ln, font=font, fill=fill, anchor=anchor)
            y += lh
        return y

    # ── Voice layoutok ─────────────────────────────────────────────────
    def _layout_david(self, img, main_text, sub_text, stat, s):
        draw = ImageDraw.Draw(img)
        w, h = img.size
        margin = int(70 * s)
        main_font, main_lines = self._fit_lines("bebas", (main_text or "").upper(), int(120 * s), w - 2 * margin)
        sub_font = self._font("inter", int(32 * s))
        sub_lines = self._wrap(draw, sub_text or "", sub_font, w - 2 * margin) if sub_text else []
        # Blokk-magasság a bal-alsó pozícionáláshoz
        ma, md = main_font.getmetrics()
        sa, sd = sub_font.getmetrics()
        main_lh = int((ma + md) * 1.04)
        sub_lh = int((sa + sd) * 1.2)
        block_h = len(main_lines) * main_lh + (len(sub_lines) * sub_lh if sub_lines else 0)
        y = h - margin - block_h
        y = self._text_with_glow(img, main_lines, main_font, (margin, y), WHITE, "la", TEAL, int(14 * s), line_gap=1.04)
        if sub_lines:
            self._shadow(img, sub_lines, sub_font, (margin, y), "la", sub_lh, blur=int(8 * s))
            draw = ImageDraw.Draw(img)
            for ln in sub_lines:
                draw.text((margin, y), ln, font=sub_font, fill=LIGHT_GREY, anchor="la")
                y += sub_lh
        if stat:
            stat_font = self._fit_font("fragment", stat, int(160 * s), int(w * 0.5))
            self._shadow(img, [stat], stat_font, (w - margin, margin), "ra", int(160 * s), blur=int(12 * s))
            ImageDraw.Draw(img).text((w - margin, margin), stat, font=stat_font, fill=TEAL, anchor="ra")
        self._watermark(img, s, where="br")

    def _layout_adam(self, img, main_text, sub_text, stat, s):
        draw = ImageDraw.Draw(img)
        w, h = img.size
        margin = int(70 * s)
        main_font, main_lines = self._fit_lines("inter", (main_text or "").upper(), int(124 * s), w - 2 * margin, weight="ExtraBold")
        sub_font = self._font("inter", int(36 * s))
        sub_lines = self._wrap(draw, sub_text or "", sub_font, int(w * 0.8)) if sub_text else []

        ma, md = main_font.getmetrics()
        sa, sd = sub_font.getmetrics()
        main_lh = int((ma + md) * 1.06)
        sub_lh = int((sa + sd) * 1.2)
        block_h = len(main_lines) * main_lh + (len(sub_lines) * sub_lh + int(24 * s) if sub_lines else 0)
        y = (h - block_h) // 2 + int(h * 0.06)
        cx = w // 2
        self._shadow(img, main_lines, main_font, (cx, y), "ma", main_lh, blur=int(16 * s), alpha=170)
        for ln in main_lines:
            draw.text((cx, y), ln, font=main_font, fill=WHITE, anchor="ma")
            y += main_lh
        if sub_lines:
            y += int(24 * s)
            self._shadow(img, sub_lines, sub_font, (cx, y), "ma", sub_lh, blur=int(8 * s))
            for ln in sub_lines:
                draw.text((cx, y), ln, font=sub_font, fill=GREY, anchor="ma")
                y += sub_lh
        if stat:
            stat_font = self._fit_font("fragment", stat, int(200 * s), int(w * 0.84))
            self._shadow(img, [stat], stat_font, (cx, margin), "ma", int(200 * s), blur=int(14 * s))
            draw.text((cx, margin), stat, font=stat_font, fill=AMBER, anchor="ma")
        self._watermark(img, s, where="br")

    def _layout_plansmart(self, img, main_text, sub_text, s):
        draw = ImageDraw.Draw(img)
        w, h = img.size
        margin = int(70 * s)
        main_font, main_lines = self._fit_lines("inter", (main_text or "").upper(), int(118 * s), w - 2 * margin, weight="ExtraBold")
        sub_font = self._font("inter", int(32 * s))
        sub_lines = self._wrap(draw, sub_text or "", sub_font, int(w * 0.8)) if sub_text else []

        ma, md = main_font.getmetrics()
        sa, sd = sub_font.getmetrics()
        main_lh = int((ma + md) * 1.06)
        sub_lh = int((sa + sd) * 1.2)
        block_h = len(main_lines) * main_lh + (len(sub_lines) * sub_lh + int(24 * s) if sub_lines else 0)
        y = (h - block_h) // 2
        cx = w // 2
        self._shadow(img, main_lines, main_font, (cx, y), "ma", main_lh, blur=int(16 * s), alpha=170)
        for ln in main_lines:
            draw.text((cx, y), ln, font=main_font, fill=WHITE, anchor="ma")
            y += main_lh
        if sub_lines:
            y += int(24 * s)
            self._shadow(img, sub_lines, sub_font, (cx, y), "ma", sub_lh, blur=int(8 * s))
            for ln in sub_lines:
                draw.text((cx, y), ln, font=sub_font, fill=LIGHT_GREY, anchor="ma")
                y += sub_lh
        # PlanSmart wordmark alul középen
        self._watermark(img, s, where="bc", text="PlanSmart", size=40, opacity=204)

    def _watermark(self, img, s, where="br", text="PlanSmart", size=24, opacity=153):
        draw = ImageDraw.Draw(img)
        w, h = img.size
        font = self._font("inter", int(size * s), weight="SemiBold")
        fill = (255, 255, 255, opacity)
        margin = int(48 * s)
        if where == "bc":
            draw.text((w // 2, h - margin), text, font=font, fill=fill, anchor="md")
        else:  # bottom-right
            draw.text((w - margin, h - margin), text, font=font, fill=fill, anchor="rd")


def _demo(url: str, voice: str) -> int:
    composer = TextOverlayComposer()
    samples = {
        "david": ("MEGTANULTAD A CLAUDE-OT", "Mi jön utána?", None),
        "adam": ("73%", "heti 4+ óra ismétlődő manuális munka", "73%"),
        "plansmart": ("16 PERC → 3,5 MP", "Ezt csináljuk KKV-knak", None),
    }
    m, sub, stat = samples.get(voice, samples["david"])
    out = composer.compose(url, m, sub, stat, voice=voice, output_path=str(GENERATED_DIR / f"demo_{voice}.png"))
    print("Mentve:", out)
    return 0


if __name__ == "__main__":
    import os
    import sys

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    if len(sys.argv) < 2:
        print("Használat: python -m src.visuals.text_overlay <base_image_url> [voice]")
        raise SystemExit(2)
    raise SystemExit(_demo(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "david"))
