"""Brand font scraper — kiolvassa a plansmart.live font-family-jét.

Megnézi a fő HTML-t (link rel=stylesheet, inline <style>), letölti a hivatkozott
CSS-eket, és összegyűjti a font-family deklarációkat + a @font-face font fájlokat.
A talált font fájlokat (woff2/woff/ttf) letölti az assets/fonts/ mappába.

A projekt elve szerint httpx-et használunk (requests helyett — az nincs telepítve),
de a logika ugyanaz: HTML + CSS letöltés, BeautifulSoup parse, regex a font-family-re.

Futtatás:
    python scripts/scrape_brand_font.py
"""
from __future__ import annotations

import json
import logging
import re
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FONTS_DIR = PROJECT_ROOT / "assets" / "fonts"

BRAND_URL = "https://plansmart.live"
TIMEOUT = 20.0
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )
}

logger = logging.getLogger("scrape_brand_font")

# font-family: ... ;   →  az értéklista
FONT_FAMILY_RE = re.compile(r"font-family\s*:\s*([^;}{]+)", re.IGNORECASE)
# @font-face { ... src: url(...) ... }
FONT_FACE_RE = re.compile(r"@font-face\s*\{([^}]*)\}", re.IGNORECASE | re.DOTALL)
FONT_SRC_URL_RE = re.compile(r"url\(\s*['\"]?([^'\")]+\.(?:woff2|woff|ttf|otf))", re.IGNORECASE)
# CSS custom property gyakran tartja a brand fontot: --font-..: "Inter", ...
FONT_VAR_RE = re.compile(r"--[\w-]*font[\w-]*\s*:\s*([^;}{]+)", re.IGNORECASE)

GENERIC = {
    "sans-serif", "serif", "monospace", "system-ui", "ui-sans-serif", "ui-serif",
    "ui-monospace", "ui-rounded", "inherit", "initial", "unset", "cursive",
    "fantasy", "-apple-system", "blinkmacsystemfont", "var",
}


def _clean_families(raw: str) -> list[str]:
    """'"Inter", system-ui, sans-serif' -> ['Inter']  (generikusok kiszűrve)."""
    out: list[str] = []
    for part in raw.split(","):
        name = part.strip().strip("'\"").strip()
        if not name or name.lower() in GENERIC or name.lower().startswith("var("):
            continue
        out.append(name)
    return out


def _fetch(client: httpx.Client, url: str) -> str:
    resp = client.get(url, timeout=TIMEOUT, headers=HEADERS, follow_redirects=True)
    resp.raise_for_status()
    return resp.text


def _download_font(client: httpx.Client, url: str) -> str | None:
    FONTS_DIR.mkdir(parents=True, exist_ok=True)
    name = url.split("?")[0].rsplit("/", 1)[-1]
    dest = FONTS_DIR / name
    try:
        resp = client.get(url, timeout=TIMEOUT, headers=HEADERS, follow_redirects=True)
        resp.raise_for_status()
        dest.write_bytes(resp.content)
        logger.info("  font letöltve: %s (%d KB)", name, len(resp.content) // 1024)
        return name
    except Exception as exc:
        logger.warning("  font letöltés sikertelen (%s): %s", url, str(exc)[:80])
        return None


def scrape(url: str = BRAND_URL) -> dict:
    families: Counter[str] = Counter()
    font_files: list[str] = []
    css_sources: list[str] = []

    with httpx.Client() as client:
        logger.info("HTML letöltése: %s", url)
        html = _fetch(client, url)
        soup = BeautifulSoup(html, "html.parser")

        # 1) Inline <style> blokkok + a HTML-be ágyazott CSS
        css_blobs: list[str] = [tag.get_text() for tag in soup.find_all("style")]

        # 2) Külső stylesheetek
        for link in soup.find_all("link", rel=lambda v: v and "stylesheet" in v):
            href = link.get("href")
            if not href:
                continue
            css_url = urljoin(url, href)
            css_sources.append(css_url)
            try:
                css_blobs.append(_fetch(client, css_url))
                logger.info("CSS letöltve: %s", css_url)
            except Exception as exc:
                logger.warning("CSS letöltés sikertelen (%s): %s", css_url, str(exc)[:80])

        # 3) Inline style attribútumok (pl. font-family a wordmarkon)
        for el in soup.find_all(style=True):
            css_blobs.append(el["style"])

        # font-family deklarációk + CSS font-változók kinyerése
        all_css = "\n".join(css_blobs)
        for m in FONT_FAMILY_RE.finditer(all_css):
            for fam in _clean_families(m.group(1)):
                families[fam] += 1
        for m in FONT_VAR_RE.finditer(all_css):
            for fam in _clean_families(m.group(1)):
                families[fam] += 2  # a brand-token explicit jel, nagyobb súly

        # @font-face → tényleges font fájlok letöltése
        seen: set[str] = set()
        for face in FONT_FACE_RE.finditer(all_css):
            block = face.group(1)
            fam_m = FONT_FAMILY_RE.search(block)
            if fam_m:
                for fam in _clean_families(fam_m.group(1)):
                    families[fam] += 3  # @font-face = saját hosztolt brand font
            for src_m in FONT_SRC_URL_RE.finditer(block):
                font_url = urljoin(url, src_m.group(1))
                if font_url in seen:
                    continue
                seen.add(font_url)
                saved = _download_font(client, font_url)
                if saved:
                    font_files.append(saved)

    return {
        "url": url,
        "families_ranked": families.most_common(),
        "primary_font": families.most_common(1)[0][0] if families else None,
        "font_files": font_files,
        "css_sources": css_sources,
    }


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level="INFO", format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    try:
        result = scrape()
    except Exception as exc:
        logger.error("Scrape sikertelen: %s", exc)
        return 1

    logger.info("\n%s", "=" * 60)
    logger.info("EREDMÉNY")
    logger.info("=" * 60)
    logger.info("Elsődleges font: %s", result["primary_font"] or "(nem található)")
    logger.info("Rangsor: %s", json.dumps(result["families_ranked"], ensure_ascii=False))
    logger.info("Letöltött font fájlok: %s", result["font_files"] or "(egy sem)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
