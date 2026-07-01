"""Folyamatos szöveg-minőség monitor — random friss posztok pontozása + trend + alert.

A generator_worker 50 generált posztonként meghívja (a vizuál-monitorral együtt): 5 random
friss posztot pontoz a TextEvaluator-ral, a hangonkénti átlagot a text_quality_metrics
táblába írja, és ha az össz-átlag < 7.5, riaszt a Telegram reactions csatornán.
"""
from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from typing import Any

from src.optimization.text_evaluator import TextEvaluator
from src.storage.db import get_client, has_service_key

logger = logging.getLogger(__name__)

ALERT_THRESHOLD = 7.5
DEFAULT_SAMPLE = 5


def _recent_posts(limit_pool: int = 40, client=None) -> list[dict]:
    c = client or get_client(use_service_key=has_service_key())
    resp = (
        c.table("posts").select("id, voice, content, final_content, metadata, generated_at")
        .order("generated_at", desc=True).limit(limit_pool).execute()
    )
    return [r for r in (resp.data or []) if (r.get("final_content") or r.get("content"))]


def _content(row: dict) -> str:
    return (row.get("final_content") or row.get("content") or "").strip()


def _ctype(row: dict) -> str:
    return (row.get("metadata") or {}).get("strategy_type") or "post"


async def evaluate_sample(sample: int = DEFAULT_SAMPLE, client=None) -> dict[str, Any]:
    client = client or get_client(use_service_key=has_service_key())
    pool = _recent_posts(client=client)
    if not pool:
        return {"overall_avg": None, "per_voice": {}, "sample_size": 0, "evaluated": []}
    chosen = random.sample(pool, min(sample, len(pool)))
    evaluator = TextEvaluator()
    evaluated = []
    for row in chosen:
        s = await evaluator.evaluate_post(_content(row), row.get("voice") or "", _ctype(row))
        evaluated.append({"post_id": row["id"], "voice": row.get("voice"),
                          "overall": s.get("overall_score"), "error": s.get("error", False)})
    valid = [e for e in evaluated if not e["error"] and e["overall"]]
    per_voice: dict[str, list[float]] = {}
    for e in valid:
        per_voice.setdefault(e["voice"] or "?", []).append(e["overall"])
    per_voice_avg = {v: round(sum(s) / len(s), 2) for v, s in per_voice.items()}
    overall = round(sum(e["overall"] for e in valid) / len(valid), 2) if valid else None
    return {"overall_avg": overall, "per_voice": per_voice_avg,
            "sample_size": len(valid), "evaluated": evaluated}


def record_metrics(per_voice: dict[str, float], sample_by_voice: dict[str, int], client=None) -> None:
    client = client or get_client(use_service_key=has_service_key())
    now = datetime.now(timezone.utc).isoformat()
    rows = [{"date": now, "voice": v, "avg_score": avg, "sample_size": sample_by_voice.get(v, 0)}
            for v, avg in per_voice.items()]
    if rows:
        client.table("text_quality_metrics").insert(rows).execute()


async def run_continuous_text_check(sample: int = DEFAULT_SAMPLE, send_alert: bool = True) -> dict[str, Any]:
    client = get_client(use_service_key=has_service_key())
    result = await evaluate_sample(sample, client=client)
    if result["sample_size"] == 0:
        logger.info("[text-quality] nincs értékelhető poszt — kihagyva.")
        return result
    sample_by_voice: dict[str, int] = {}
    for e in result["evaluated"]:
        if not e["error"] and e["overall"]:
            sample_by_voice[e["voice"] or "?"] = sample_by_voice.get(e["voice"] or "?", 0) + 1
    try:
        record_metrics(result["per_voice"], sample_by_voice, client=client)
    except Exception as exc:
        logger.warning("[text-quality] metrika mentés kihagyva: %s", str(exc)[:100])

    overall = result["overall_avg"]
    logger.info("[text-quality] össz-átlag=%s | hangonként=%s (n=%d)",
                overall, result["per_voice"], result["sample_size"])
    if send_alert and overall is not None and overall < ALERT_THRESHOLD:
        await _send_alert(overall, result["per_voice"])
        result["alerted"] = True
    return result


async def _send_alert(overall: float, per_voice: dict[str, float]) -> None:
    try:
        from src.bots import telegram_bot as tb

        lines = "\n".join(f"  • {v}: {s}" for v, s in per_voice.items())
        text = (f"⚠️ <b>Szöveg-minőség riasztás</b>\n\n"
                f"Az átlagos szöveg-pontszám <b>{overall}</b> &lt; {ALERT_THRESHOLD} küszöb.\n"
                f"Hangonként:\n{lines}\n\n"
                f"Érdemes újra-futtatni: <code>python -m scripts.run_text_eval</code>")
        bot = tb.get_bot()
        await bot.send_message(tb.REACTIONS_CHAT_ID, text)
    except Exception as exc:
        logger.warning("[text-quality] alert küldés kihagyva: %s", str(exc)[:100])
