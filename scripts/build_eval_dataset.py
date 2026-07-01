"""Eval dataset építő — 10 valós poszt a posts táblából → data/eval_dataset.json.

Hangok/típusok keverve, tartalmas (>150 kar) posztokat választva. Minden bejegyzéshez
heurisztikus 'expected_visual_elements' lista (dark bg + voice-elem + stat ha van).

    python -m scripts.build_eval_dataset [--limit 10]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.storage.db import get_client, has_service_key

logger = logging.getLogger("build_eval_dataset")
load_dotenv(override=False)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_FILE = PROJECT_ROOT / "data" / "eval_dataset.json"

VOICE_ELEMENT = {
    "david": "monospace / code-diagram",
    "adam": "metric / before-after chart",
    "plansmart": "PlanSmart wordmark",
}
_STAT_RE = re.compile(r"(\d[\d\s.,]*\s?(?:%|Ft|óra|perc|nap|hét|x|×|USD|\$|millió|ezer))", re.IGNORECASE)


def _stat(text: str) -> str | None:
    m = _STAT_RE.search(text or "")
    return m.group(1).strip() if m else None


def _expected_elements(voice: str, content: str) -> list[str]:
    el = ["dark bg #04060a", "bold display type", VOICE_ELEMENT.get(voice, "single focal element")]
    s = _stat(content)
    if s:
        el.append(f"stat: {s}")
    el.append("PlanSmart watermark")
    return el


def _content_of(row: dict) -> str:
    # Az élesen posztolt (optimalizált) szöveg, ha van; egyébként a nyers.
    return (row.get("final_content") or row.get("content") or "").strip()


def _pick_balanced(rows: list[dict], limit: int) -> list[dict]:
    """Hangok szerint körbejárva válogat, hogy kiegyensúlyozott legyen a minta."""
    by_voice: dict[str, list[dict]] = {}
    for r in rows:
        if len(_content_of(r)) < 150:
            continue
        by_voice.setdefault(r.get("voice") or "?", []).append(r)
    # voice-onként a hosszabb (tartalmasabb) posztok előre
    for v in by_voice.values():
        v.sort(key=lambda r: len(_content_of(r)), reverse=True)

    picked, voices = [], list(by_voice.keys())
    idx = {v: 0 for v in voices}
    while len(picked) < limit and any(idx[v] < len(by_voice[v]) for v in voices):
        for v in voices:
            if idx[v] < len(by_voice[v]):
                picked.append(by_voice[v][idx[v]])
                idx[v] += 1
                if len(picked) >= limit:
                    break
    return picked


def build(limit: int = 10) -> list[dict]:
    client = get_client(use_service_key=has_service_key())
    resp = client.table("posts").select(
        "id, voice, content_type, content, final_content, metadata, visual_url"
    ).execute()
    rows = resp.data or []

    picked = _pick_balanced(rows, limit)
    dataset = []
    for r in picked:
        content = _content_of(r)
        ctype = (r.get("metadata") or {}).get("strategy_type") or r.get("content_type") or "post"
        dataset.append({
            "post_id": r["id"],
            "voice": r.get("voice"),
            "content_type": ctype,
            "content": content,
            "current_visual_url": r.get("visual_url"),  # eredeti vizuál (side-by-side baseline)
            "expected_visual_elements": _expected_elements(r.get("voice") or "", content),
        })
    return dataset


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    ap = argparse.ArgumentParser(description="Eval dataset építő a posts táblából.")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    dataset = build(args.limit)
    OUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    OUT_FILE.write_text(json.dumps(dataset, ensure_ascii=False, indent=2), encoding="utf-8")

    from collections import Counter
    by_voice = Counter(d["voice"] for d in dataset)
    logger.info("Mentve: %s (%d poszt)", OUT_FILE, len(dataset))
    logger.info("Hang szerint: %s", dict(by_voice))
    logger.info("Eredeti vizuállal: %d", sum(1 for d in dataset if d.get("current_visual_url")))
    for d in dataset:
        logger.info("  %s [%s/%s] %s", d["post_id"], d["voice"], d["content_type"],
                    d["content"][:60].replace("\n", " "))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
