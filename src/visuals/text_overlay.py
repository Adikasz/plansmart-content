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
from PIL import Image, ImageChops, ImageDraw, ImageEnhance, ImageFilter, ImageFont

from src.utils.logging import setup_logging
from src.visuals import layout_templates as lt

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
FONTS_DIR = PROJECT_ROOT / "assets" / "fonts"
TTF_DIR = FONTS_DIR / "ttf"
GENERATED_DIR = PROJECT_ROOT / "assets" / "generated"
BRAND_DIR = PROJECT_ROOT / "assets" / "brand"

# Valódi logó-jel (Phase 13.5): a teal áramköri „P" ikon, átlátszó háttérrel — ezt
# komponáljuk minden képre jobb-alsó watermarkként (a régi [BRAND_LOGO] szöveg-token
# helyett, amit a Flux betűként rajzolt ki — Phase 12.5 bug). A wordmark verziók
# (Logo2/Logo3/Logo4) NEM alkalmasak kis sarok-watermarknak, csak a mark.
LOGO_MARK_FILE = BRAND_DIR / "Logo main.png"
LOGO_WIDTH_FRAC = 0.08   # a vászon szélességének ~8%-a
LOGO_PADDING_PX = 32     # jobb/alsó széltől (1024px-re hangolva, s-sel skálázva)
LOGO_OPACITY = 0.65      # 65% átlátszatlanság

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
COOL_STEEL = (150, 172, 198, 255)

# Phase 17: voice-onkénti DEFAULT accent (ha nincs mood-accent megadva). david → teal
# (a validált 8.5 baseline), adam → cool steel (a mood-rendszer defaultja, NEM a régi amber),
# plansmart → light grey (a legvisszafogottabb).
DEFAULT_ACCENT = {"david": TEAL, "adam": COOL_STEEL, "plansmart": LIGHT_GREY}

DOWNLOAD_TIMEOUT_S = 30.0


def _rgba(color) -> tuple[int, int, int, int] | None:
    """(r,g,b) vagy (r,g,b,a) → RGBA 4-es. None → None."""
    if color is None:
        return None
    if len(color) == 4:
        return (int(color[0]), int(color[1]), int(color[2]), int(color[3]))
    return (int(color[0]), int(color[1]), int(color[2]), 255)


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
        self._logo = self._load_logo()
        GENERATED_DIR.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def _load_logo() -> Image.Image | None:
        """A valódi logó-jel betöltése egyszer (átlátszó-margó levágva). None, ha hiányzik."""
        try:
            logo = Image.open(LOGO_MARK_FILE).convert("RGBA")
        except Exception as exc:
            logger.warning("[overlay] logó betöltés hiba (%s): %s", LOGO_MARK_FILE.name, str(exc)[:100])
            return None
        # Neon-a-feketén jel: a bepékelt sötét glow félig-átlátszó, sötét pixeleket hagy (halvány
        # doboz a sarokban). Az alphát a pixel fényességével skálázzuk → a sötét részek eltűnnek,
        # csak a világító „P" marad, és a sötét #04060a háttéren fényként ül meg (nem dobozként).
        alpha = ImageChops.multiply(logo.getchannel("A"), logo.convert("L"))
        logo.putalpha(alpha)
        bbox = alpha.getbbox()  # a nagy átlátszó keret levágása
        if bbox:
            logo = logo.crop(bbox)
        return logo

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
        portrait_path: str | None = None,
        template: str = lt.STAT_CARD,
        accent=None,
    ) -> str:
        """Letölti az alapképet, ráírja a magyar szöveget, menti, és visszaadja a lokális utat.

        portrait_path (Phase 13.5): ha meg van adva, az alapító kivágott (háttér nélküli)
        portréját a bal-alsó harmadba komponáljuk a szöveg ELŐTT; ilyenkor a fő szöveg a
        kép TETEJÉRE kerül, hogy ne ütközzön a portréval. A logó mindig jobb-alsó sarok.

        template (Phase 17): STAT_CARD (baseline) | QUOTE_STYLE | SPLIT_COMPARISON |
        MINIMAL_TYPOGRAPHIC — a kompozíciós struktúra. accent: a mood accent-színe (RGB);
        None → voice default. Ha portré van, MINDIG a STAT_CARD (portré-tudatos) elrendezés fut,
        hogy ne regresszáljunk a bevált portré-layoutról.
        """
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

        acc = _rgba(accent) or DEFAULT_ACCENT.get(voice, TEAL)

        # Portré a szöveg ELŐTT (bal-alsó), ha van — plansmart sosem kap portrét.
        has_portrait = bool(portrait_path) and voice in {"david", "adam"}
        # Portré esetén a bevált STAT_CARD (portré-tudatos) elrendezésre esünk vissza.
        layout = lt.STAT_CARD if has_portrait else (template if lt.is_template(template) else lt.STAT_CARD)

        # Gradiens: STAT_CARD → a bevált per-voice; a többi template → saját, olvashatóság-barát wash.
        if layout == lt.STAT_CARD:
            layer_fn = {
                "david": lambda: self._gradient(img.size, bottom=0.85),
                "adam": lambda: self._gradient(img.size, top=0.7, bottom=0.8),
                "plansmart": lambda: self._gradient(img.size, full=0.6),
            }.get(voice, lambda: self._gradient(img.size, bottom=0.8))
        elif layout == lt.QUOTE_STYLE:
            layer_fn = lambda: self._gradient(img.size, full=0.5, bottom=0.35)
        elif layout == lt.SPLIT_COMPARISON:
            layer_fn = lambda: self._gradient(img.size, full=0.58)
        else:  # MINIMAL_TYPOGRAPHIC
            layer_fn = lambda: self._gradient(img.size, full=0.5)
        img = Image.alpha_composite(img, layer_fn())

        if has_portrait:
            self._place_portrait(img, portrait_path, s)

        if layout == lt.STAT_CARD:
            if voice == "david":
                self._layout_david(img, main_text, sub_text, stat, s, acc, portrait=has_portrait)
            elif voice == "adam":
                self._layout_adam(img, main_text, sub_text, stat, s, acc, portrait=has_portrait)
            else:
                self._layout_plansmart(img, main_text, sub_text, s, acc)
        elif layout == lt.QUOTE_STYLE:
            self._layout_quote(img, main_text, sub_text, stat, s, voice, acc)
        elif layout == lt.SPLIT_COMPARISON:
            self._layout_split(img, main_text, sub_text, stat, s, voice, acc)
        else:  # MINIMAL_TYPOGRAPHIC
            self._layout_minimal(img, main_text, sub_text, stat, s, voice, acc)

        self._paste_logo(img, s)  # valódi logó-jel minden képre, jobb-alsó sarok

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
    def _layout_david(self, img, main_text, sub_text, stat, s, accent=TEAL, portrait=False):
        draw = ImageDraw.Draw(img)
        w, h = img.size
        margin = int(70 * s)
        main_font, main_lines = self._fit_lines("bebas", (main_text or "").upper(), int(120 * s), w - 2 * margin)
        sub_font = self._font("inter", int(32 * s))
        sub_lines = self._wrap(draw, sub_text or "", sub_font, w - 2 * margin) if sub_text else []
        # Blokk-magasság a pozícionáláshoz
        ma, md = main_font.getmetrics()
        sa, sd = sub_font.getmetrics()
        main_lh = int((ma + md) * 1.04)
        sub_lh = int((sa + sd) * 1.2)
        block_h = len(main_lines) * main_lh + (len(sub_lines) * sub_lh if sub_lines else 0)
        if portrait:
            # A portré a bal-alsót foglalja → MINDEN szöveg a felső sávba, bal-oldalt, FÜGGŐLEGESEN
            # egymásra pakolva (stat → main → sub). Így a stat nem ütközik a fő szöveggel.
            y = margin
            if stat:
                stat_font = self._fit_font("fragment", stat, int(120 * s), w - 2 * margin)
                sfa, sfd = stat_font.getmetrics()
                stat_lh = int((sfa + sfd) * 1.02)
                self._shadow(img, [stat], stat_font, (margin, y), "la", stat_lh, blur=int(12 * s))
                ImageDraw.Draw(img).text((margin, y), stat, font=stat_font, fill=accent, anchor="la")
                y += stat_lh + int(10 * s)
            y = self._text_with_glow(img, main_lines, main_font, (margin, y), WHITE, "la", accent, int(14 * s), line_gap=1.04)
            if sub_lines:
                self._shadow(img, sub_lines, sub_font, (margin, y), "la", sub_lh, blur=int(8 * s))
                draw = ImageDraw.Draw(img)
                for ln in sub_lines:
                    draw.text((margin, y), ln, font=sub_font, fill=LIGHT_GREY, anchor="la")
                    y += sub_lh
            return
        # Portré NÉLKÜL: fő szöveg bal-alsó, a stat jobb-felső (külön zóna, nincs ütközés).
        y = h - margin - block_h
        y = self._text_with_glow(img, main_lines, main_font, (margin, y), WHITE, "la", accent, int(14 * s), line_gap=1.04)
        if sub_lines:
            self._shadow(img, sub_lines, sub_font, (margin, y), "la", sub_lh, blur=int(8 * s))
            draw = ImageDraw.Draw(img)
            for ln in sub_lines:
                draw.text((margin, y), ln, font=sub_font, fill=LIGHT_GREY, anchor="la")
                y += sub_lh
        if stat:
            stat_font = self._fit_font("fragment", stat, int(160 * s), int(w * 0.45))
            self._shadow(img, [stat], stat_font, (w - margin, margin), "ra", int(160 * s), blur=int(12 * s))
            ImageDraw.Draw(img).text((w - margin, margin), stat, font=stat_font, fill=accent, anchor="ra")

    def _layout_adam(self, img, main_text, sub_text, stat, s, accent=COOL_STEEL, portrait=False):
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
        # A stat (ha van) a felső sávban ül; a szöveg-blokk alapból középen, portré esetén feljebb
        # (a felső harmadba), hogy a bal-alsó portré ne takarja.
        stat_reserve = int(200 * s + margin) if stat else 0
        if portrait:
            y = stat_reserve + margin
        else:
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
            draw.text((cx, margin), stat, font=stat_font, fill=accent, anchor="ma")

    def _layout_plansmart(self, img, main_text, sub_text, s, accent=LIGHT_GREY):
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
                draw.text((cx, y), ln, font=sub_font, fill=accent, anchor="ma")
                y += sub_lh
        # A PlanSmart brandet a valódi logó-jel adja (jobb-alsó), a compose() teszi rá.

    # ── Phase 17 template-elrendezések (STAT_CARD-on túl) ──────────────
    def _layout_quote(self, img, main_text, sub_text, stat, s, voice, accent):
        """QUOTE_STYLE — editorial pull-quote: nagy idézőjel-accent, bal-igazított aszimmetrikus
        fő szöveg (pull-quote), az al-szöveg kicsi, attribúció-jellegű."""
        w, h = img.size
        margin = int(80 * s)
        # Nagy stilizált idézőjel (accent), bal-felső — az Inter fedi a „ (U+201C) glyphet.
        qfont = self._font("inter", int(300 * s), "ExtraBold")
        self._shadow(img, ["“"], qfont, (margin - int(10 * s), int(10 * s)), "la", int(300 * s), blur=int(10 * s))
        ImageDraw.Draw(img).text((margin - int(10 * s), int(10 * s)), "“", font=qfont, fill=accent, anchor="la")

        quote_text = (main_text or "").strip().strip('"“”')
        max_w = int(w * 0.82)
        main_font, main_lines = self._fit_lines("bebas", quote_text.upper(), int(112 * s), max_w,
                                                min_ratio=0.5, max_lines=4)
        ma, md = main_font.getmetrics()
        main_lh = int((ma + md) * 1.02)
        block_h = len(main_lines) * main_lh
        y = int(h * 0.32)
        if y + block_h > h - int(170 * s):  # ne fusson bele az alsó attribúció-sávba
            y = max(int(h * 0.20), h - int(170 * s) - block_h)
        y = self._text_with_glow(img, main_lines, main_font, (margin, y), WHITE, "la", accent, int(12 * s), line_gap=1.02)
        if sub_text:
            sub_font = self._font("inter", int(30 * s), "SemiBold")
            attr_lines = self._wrap(ImageDraw.Draw(img), f"— {sub_text.strip()}", sub_font, max_w)
            sa, sd = sub_font.getmetrics()
            sub_lh = int((sa + sd) * 1.2)
            y += int(18 * s)
            self._shadow(img, attr_lines, sub_font, (margin, y), "la", sub_lh, blur=int(6 * s))
            draw = ImageDraw.Draw(img)
            for ln in attr_lines:
                draw.text((margin, y), ln, font=sub_font, fill=accent, anchor="la")
                y += sub_lh

    def _split_pair(self, main_text, stat) -> tuple[str, str]:
        """(bal, jobb) szétbontás: elválasztó (→ / -> / vs) a stat-ban vagy main-ben; különben
        a stat vs. main, végső esetben a main szavai félbe."""
        for src in (stat, main_text):
            if not src:
                continue
            for sep in ("→", "->", " VS ", " vs ", " versus "):
                if sep in src:
                    a, b = src.split(sep, 1)
                    if a.strip() and b.strip():
                        return a.strip(), b.strip()
        if stat and main_text and stat.strip().lower() not in main_text.strip().lower():
            return stat.strip(), main_text.strip()
        words = (main_text or "").split()
        if len(words) >= 2:
            mid = len(words) // 2
            return " ".join(words[:mid]), " ".join(words[mid:])
        return (main_text or stat or "").strip(), ""

    def _divider(self, img, cx, y0, y1, s, accent) -> None:
        """Függőleges elválasztó vonal accent-színnel + lágy glow (SPLIT_COMPARISON)."""
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        d = ImageDraw.Draw(layer)
        lw = max(2, int(4 * s))
        d.line([(cx, y0), (cx, y1)], fill=accent, width=lw)
        img.alpha_composite(layer.filter(ImageFilter.GaussianBlur(int(6 * s))))  # glow
        img.alpha_composite(layer)  # éles vonal felül

    def _layout_split(self, img, main_text, sub_text, stat, s, voice, accent):
        """SPLIT_COMPARISON — függőleges divider, bal/jobb (before → after / kontraszt) keret."""
        w, h = img.size
        margin = int(60 * s)
        left, right = self._split_pair(main_text, stat)
        cx = w // 2
        self._divider(img, cx, int(h * 0.20), int(h * 0.80), s, accent)
        half_w = int(w * 0.5 - margin * 1.4)

        def draw_side(text, center_x):
            if not text:
                return
            font, lines = self._fit_lines("inter", text.upper(), int(100 * s), half_w,
                                          weight="ExtraBold", min_ratio=0.45, max_lines=3)
            a, dsc = font.getmetrics()
            lh = int((a + dsc) * 1.05)
            bh = len(lines) * lh
            yy = (h - bh) // 2 - int(h * 0.02)
            self._shadow(img, lines, font, (center_x, yy), "ma", lh, blur=int(12 * s), alpha=175)
            dd = ImageDraw.Draw(img)
            for ln in lines:
                dd.text((center_x, yy), ln, font=font, fill=WHITE, anchor="ma")
                yy += lh

        draw_side(left, w // 4)
        draw_side(right, 3 * w // 4)
        if sub_text:
            sub_font = self._font("inter", int(30 * s))
            sub_lines = self._wrap(ImageDraw.Draw(img), sub_text, sub_font, int(w * 0.86))
            sa, sd = sub_font.getmetrics()
            sub_lh = int((sa + sd) * 1.2)
            yy = h - margin - len(sub_lines) * sub_lh
            self._shadow(img, sub_lines, sub_font, (cx, yy), "ma", sub_lh, blur=int(6 * s))
            dd = ImageDraw.Draw(img)
            for ln in sub_lines:
                dd.text((cx, yy), ln, font=sub_font, fill=LIGHT_GREY, anchor="ma")
                yy += sub_lh

    def _layout_minimal(self, img, main_text, sub_text, stat, s, voice, accent):
        """MINIMAL_TYPOGRAPHIC — egyetlen óriási szó/rövid frázis középen, maximális negatív tér,
        legnagyobb kontraszt (erős accent-glow). Alig-alig más szöveg."""
        w, h = img.size
        margin = int(50 * s)
        # Egy RÖVID, de TELJES egység: rövid stat (pl. "73%", "16 MIN"), különben a teljes
        # headline (a main_text már 3-5 szavas display-szöveg). NEM vágunk 2 szóra (az csonka
        # frázist adott: "CHECKPOINT LONG"). Csak nagyon hosszúnál (>5 szó) az első 3 szó.
        stat_s = (stat or "").strip()
        main_s = (main_text or "").strip()
        if stat_s and len(stat_s) <= 12:
            text = stat_s
        else:
            text = main_s or stat_s
            words = text.split()
            if len(words) > 5:
                text = " ".join(words[:3])
        # Méret: illeszkedjen SZÉLESSÉGRE ÉS MAGASSÁGRA is (a hosszabb frázis kisebb lesz, de
        # teljesen látszik — nem lóg ki fent/lent). Marad a nagy negatív tér.
        avail_h = int(h * 0.60)
        size = int(280 * s)
        while True:
            main_font, main_lines = self._fit_lines("bebas", text.upper(), size, w - 2 * margin,
                                                    min_ratio=0.98, max_lines=4)
            ma, md = main_font.getmetrics()
            main_lh = int((ma + md) * 0.98)
            if not main_lines or len(main_lines) * main_lh <= avail_h or size <= int(80 * s):
                break
            size = int(size * 0.9)
        block_h = len(main_lines) * main_lh
        y = max(int(h * 0.14), (h - block_h) // 2)
        cx = w // 2
        self._text_with_glow(img, main_lines, main_font, (cx, y), WHITE, "ma", accent, int(22 * s), line_gap=0.98)
        if sub_text:  # csak egy halvány sor legalul
            sub_font = self._font("inter", int(26 * s), "SemiBold")
            sub_lines = self._wrap(ImageDraw.Draw(img), sub_text, sub_font, int(w * 0.7))[:1]
            sa, sd = sub_font.getmetrics()
            sub_lh = int((sa + sd) * 1.2)
            yy = h - margin - sub_lh
            self._shadow(img, sub_lines, sub_font, (cx, yy), "ma", sub_lh, blur=int(5 * s))
            dd = ImageDraw.Draw(img)
            for ln in sub_lines:
                dd.text((cx, yy), ln, font=sub_font, fill=GREY, anchor="ma")

    # ── Valódi logó-jel watermark (Phase 13.5) ─────────────────────────
    def _paste_logo(self, img, s) -> None:
        """A valódi teal „P" logó-jel a jobb-alsó sarokba: ~8% szélesség, 32px padding, 65% opacity."""
        if self._logo is None:
            return
        w, h = img.size
        target_w = max(1, int(w * LOGO_WIDTH_FRAC))
        ratio = target_w / self._logo.width
        target_h = max(1, int(round(self._logo.height * ratio)))
        logo = self._logo.resize((target_w, target_h), Image.LANCZOS)
        if LOGO_OPACITY < 1.0:  # globális átlátszatlanság a meglévő alpha-ra
            logo.putalpha(logo.getchannel("A").point(lambda p: int(p * LOGO_OPACITY)))
        pad = int(LOGO_PADDING_PX * s)
        img.alpha_composite(logo, (w - target_w - pad, h - target_h - pad))

    # ── Alapító-portré komponálás (Phase 13.5) ─────────────────────────
    def _place_portrait(self, img, portrait_path, s) -> None:
        """Kivágott (háttér nélküli) portré a bal-alsó harmadba, filmes illesztéssel.

        A portré a szöveg ELŐTT kerül a képre. Lépések: bbox-crop → méretezés (~40% magasság) →
        deszaturáció + kontraszt + grain (filmes illesztés) → grounding árnyék → beillesztés.
        """
        try:
            cut = Image.open(portrait_path).convert("RGBA")
        except Exception as exc:
            logger.warning("[overlay] portré betöltés hiba (%s): %s", portrait_path, str(exc)[:100])
            return
        bbox = cut.getchannel("A").getbbox()
        if bbox:
            cut = cut.crop(bbox)
        w, h = img.size
        target_h = int(h * 0.40)  # a vászon magasságának ~40%-a
        ratio = target_h / cut.height
        target_w = int(cut.width * ratio)
        max_w = int(w * 0.46)  # ne lógjon át a jobb oldalra (bal harmad + kicsit)
        if target_w > max_w:
            ratio = max_w / cut.width
            target_w, target_h = max_w, int(cut.height * ratio)
        cut = cut.resize((target_w, target_h), Image.LANCZOS)
        cut = self._match_cinematic(cut)

        px = int(40 * s)          # bal padding
        py = h - target_h          # alsó élre ültetve (a személy a keretből "emelkedik ki")
        self._portrait_backing(img, cut.getchannel("A"), (px, py), s)
        img.alpha_composite(cut, (px, py))

    @staticmethod
    def _match_cinematic(cut: Image.Image) -> Image.Image:
        """Deszaturáció ~15%, enyhe kontraszt/sötétítés + finom grain — a filmes alapképhez illesztve."""
        alpha = cut.getchannel("A")
        rgb = cut.convert("RGB")
        rgb = ImageEnhance.Color(rgb).enhance(0.85)      # -15% telítettség
        rgb = ImageEnhance.Contrast(rgb).enhance(1.05)
        rgb = ImageEnhance.Brightness(rgb).enhance(0.94)  # kicsit sötétebb, hogy beüljön a #04060a-ba
        out = rgb.convert("RGBA")
        out.putalpha(alpha)
        # Kodak Portra-szerű grain: szürke zaj, csak a portré maszkján belül, alacsony opacitással.
        noise = Image.effect_noise(out.size, 22).convert("L")
        grain = Image.merge("RGBA", (noise, noise, noise, alpha.point(lambda p: int(p * 0.10))))
        return Image.alpha_composite(out, grain)

    def _portrait_backing(self, img, alpha: Image.Image, pos, s) -> None:
        """Grounding: elmosott sötét sziluett a portré mögé (drop shadow + beolvasztás a sötét háttérbe)."""
        px, py = pos
        layer = Image.new("RGBA", img.size, (0, 0, 0, 0))
        # Kissé kinagyított, eltolt fekete sziluett → lágy dobott árnyék + elválasztás a háttértől.
        soft = alpha.point(lambda p: int(p * 0.60))
        black = Image.new("RGBA", alpha.size, (0, 0, 0, 255))
        layer.paste(black, (px + int(8 * s), py + int(12 * s)), mask=soft)
        layer = layer.filter(ImageFilter.GaussianBlur(int(20 * s)))
        img.alpha_composite(layer)


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
    import sys

    setup_logging()
    if len(sys.argv) < 2:
        print("Használat: python -m src.visuals.text_overlay <base_image_url> [voice]")
        raise SystemExit(2)
    raise SystemExit(_demo(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "david"))
