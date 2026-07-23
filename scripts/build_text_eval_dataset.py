"""Szöveg-eval dataset építő — 10 reprezentatív szcenárió → data/text_eval_dataset.json.

Összetétel: 4 ai_news (valós feed_items) + 2 educational + 2 case_study (seed YAML) +
2 consultant_builder (builder-fókuszú instrukció). Minden szcenárióhoz voice + content_type.

    python -m scripts.build_text_eval_dataset
"""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from src.core.storage import feed_items as feed_store
from src.core.storage.db import get_client, has_service_key
from src.core.strategy import content_strategy

logger = logging.getLogger("build_text_eval_dataset")
load_dotenv(override=False)

OUT = Path(__file__).resolve().parents[1] / "data" / "text_eval_dataset.json"

# A híreket Dávid és Ádám viszi (PlanSmart skip-eli a hírt) — váltogatva.
NEWS_VOICES = ["adam", "david", "adam", "david"]

CONSULTANT_BUILDER = [
    {"voice": "david",
     "instruction": ("Consultant/builder poszt: oszd meg egy konkrét buildlog-tanulságot arról, "
                     "hogyan vezettél be egy automatizálást egy ügyfélnél — mi tört el, mit tanultál. "
                     "Konkrét időpont, konkrét szám, saját hang.")},
    {"voice": "david",
     "instruction": ("Consultant/builder poszt: 'így csináltuk' sztori egy n8n vagy Python pipeline-ról, "
                     "amit egy KKV-nak építettél — a döntési pont, a buktató, a végeredmény számokban.")},
]


def _news_scenarios() -> list[dict]:
    client = get_client(use_service_key=has_service_key())
    rows = feed_store.get_for_generation(limit=20, min_score=6, client=client)
    out = []
    for i, r in enumerate(rows[:4]):
        out.append({
            "id": f"news_{r['id'][:8]}", "voice": NEWS_VOICES[i % len(NEWS_VOICES)],
            "content_type": "ai_news", "kind": "ai_news", "label": "ai_news",
            "feed": {"id": r["id"], "title": r.get("title"), "summary": (r.get("content") or "")[:600],
                     "url": r.get("url"), "source": r.get("source_name"), "score": r.get("score"),
                     "tags": r.get("topics") or []},
        })
    return out


def _seed_scenarios(content_type: str, voice: str, n: int, label: str) -> list[dict]:
    out, used = [], set()
    for _ in range(n):
        seed = content_strategy.get_seed(content_type, voice, used)
        if not seed:
            break
        used.add(seed["seed_key"])
        out.append({"id": f"{label}_{seed['seed_key'][:16]}", "voice": voice,
                    "content_type": content_type, "kind": "seed", "label": label,
                    "instruction": seed["instruction"]})
    return out


def build() -> list[dict]:
    scenarios: list[dict] = []
    scenarios += _news_scenarios()
    scenarios += _seed_scenarios("educational", "david", 1, "educational")
    scenarios += _seed_scenarios("educational", "adam", 1, "educational")
    scenarios += _seed_scenarios("case_study", "plansmart", 2, "case_study")
    for i, cb in enumerate(CONSULTANT_BUILDER):
        scenarios.append({"id": f"consultant_builder_{i+1}", "voice": cb["voice"],
                          "content_type": "educational", "kind": "seed", "label": "consultant_builder",
                          "instruction": cb["instruction"]})
    return scenarios


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"), format="%(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)

    data = build()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    from collections import Counter
    logger.info("Mentve: %s (%d szcenárió)", OUT, len(data))
    logger.info("Label szerint: %s", dict(Counter(d["label"] for d in data)))
    logger.info("Voice szerint: %s", dict(Counter(d["voice"] for d in data)))
    for d in data:
        src = d.get("feed", {}).get("title") if d["kind"] == "ai_news" else d["instruction"][:60]
        logger.info("  %-22s [%s/%s] %s", d["id"], d["voice"], d["content_type"], (src or "")[:60])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
