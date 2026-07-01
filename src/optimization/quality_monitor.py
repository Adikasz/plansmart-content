"""Folyamatos vizuál-minőség monitor — random friss vizuálok pontozása + trend + alert.

A generator_worker 50 generált posztonként meghívja: 5 random friss, vizuállal rendelkező
posztot pontoz a VisualEvaluator-ral (nincs új Muapi kép — a meglévő visual_url-t értékeli),
a hangonkénti átlagot a visual_quality_metrics táblába írja, és ha az össz-átlag < 7.0,
riaszt a Telegram reactions csatornán.

Az /eval_visuals parancs a get_current_quality()-t használja (baseline-nal összevetve).
"""
from __future__ import annotations

import json
import logging
import random
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from src.optimization.visual_eval import VisualEvaluator
from src.storage.db import get_client, has_service_key

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BASELINE_FILE = PROJECT_ROOT / "data" / "visual_eval_results_latest.json"

ALERT_THRESHOLD = 7.0
DEFAULT_SAMPLE = 5


def _recent_visual_posts(limit_pool: int = 40, client=None) -> list[dict]:
    c = client or get_client(use_service_key=has_service_key())
    resp = (
        c.table("posts").select("id, voice, content, final_content, visual_url, generated_at")
        .not_.is_("visual_url", "null").order("generated_at", desc=True).limit(limit_pool).execute()
    )
    return [r for r in (resp.data or []) if r.get("visual_url")]


def _content(row: dict) -> str:
    return (row.get("final_content") or row.get("content") or "").strip()


async def evaluate_sample(sample: int = DEFAULT_SAMPLE, client=None) -> dict[str, Any]:
    """N random friss vizuál pontozása. Visszaad: per-voice átlag + overall + minták."""
    client = client or get_client(use_service_key=has_service_key())
    pool = _recent_visual_posts(client=client)
    if not pool:
        return {"overall_avg": None, "per_voice": {}, "sample_size": 0, "evaluated": []}

    chosen = random.sample(pool, min(sample, len(pool)))
    evaluator = VisualEvaluator()
    evaluated = []
    for row in chosen:
        scores = await evaluator.evaluate_image(row["visual_url"], _content(row), row.get("voice") or "")
        evaluated.append({"post_id": row["id"], "voice": row.get("voice"),
                          "overall": scores.get("overall_score"), "error": scores.get("error", False)})

    valid = [e for e in evaluated if not e["error"] and e["overall"]]
    per_voice: dict[str, list[float]] = {}
    for e in valid:
        per_voice.setdefault(e["voice"] or "?", []).append(e["overall"])
    per_voice_avg = {v: round(sum(s) / len(s), 2) for v, s in per_voice.items()}
    overall = round(sum(e["overall"] for e in valid) / len(valid), 2) if valid else None
    return {"overall_avg": overall, "per_voice": per_voice_avg,
            "sample_size": len(valid), "evaluated": evaluated}


def record_metrics(per_voice: dict[str, float], sample_by_voice: dict[str, int], client=None) -> None:
    """A hangonkénti átlagokat a visual_quality_metrics táblába írja (best-effort)."""
    client = client or get_client(use_service_key=has_service_key())
    now = datetime.now(timezone.utc).isoformat()
    rows = [{"date": now, "voice": v, "avg_score": avg, "sample_size": sample_by_voice.get(v, 0)}
            for v, avg in per_voice.items()]
    if rows:
        client.table("visual_quality_metrics").insert(rows).execute()


async def run_continuous_quality_check(sample: int = DEFAULT_SAMPLE, send_alert: bool = True) -> dict[str, Any]:
    """Egy folyamatos minőség-ellenőrzés: pontoz, trendet rögzít, küszöb alatt riaszt."""
    client = get_client(use_service_key=has_service_key())
    result = await evaluate_sample(sample, client=client)
    if result["sample_size"] == 0:
        logger.info("[quality] nincs értékelhető vizuál — kihagyva.")
        return result

    # sample darabszám hangonként
    sample_by_voice: dict[str, int] = {}
    for e in result["evaluated"]:
        if not e["error"] and e["overall"]:
            sample_by_voice[e["voice"] or "?"] = sample_by_voice.get(e["voice"] or "?", 0) + 1
    try:
        record_metrics(result["per_voice"], sample_by_voice, client=client)
    except Exception as exc:
        logger.warning("[quality] metrika mentés kihagyva: %s", str(exc)[:100])

    overall = result["overall_avg"]
    logger.info("[quality] össz-átlag=%s | hangonként=%s (n=%d)",
                overall, result["per_voice"], result["sample_size"])

    if send_alert and overall is not None and overall < ALERT_THRESHOLD:
        await _send_alert(overall, result["per_voice"])
        result["alerted"] = True
    return result


async def _send_alert(overall: float, per_voice: dict[str, float]) -> None:
    """Riasztás a reactions csatornára, ha a vizuál-minőség a küszöb alá esett."""
    try:
        from src.bots import telegram_bot as tb

        lines = "\n".join(f"  • {v}: {s}" for v, s in per_voice.items())
        text = (f"⚠️ <b>Vizuál minőség riasztás</b>\n\n"
                f"Az átlagos vizuál pontszám <b>{overall}</b> &lt; {ALERT_THRESHOLD} küszöb.\n"
                f"Hangonként:\n{lines}\n\n"
                f"Érdemes újra-futtatni: <code>python -m scripts.run_visual_eval</code>")
        bot = tb.get_bot()
        await bot.send_message(tb.REACTIONS_CHAT_ID, text)
    except Exception as exc:
        logger.warning("[quality] alert küldés kihagyva: %s", str(exc)[:100])


def load_baseline() -> dict[str, Any] | None:
    """A 'living baseline' (legutóbbi teljes eval) per-variant/per-voice átlagai."""
    if not BASELINE_FILE.exists():
        return None
    try:
        data = json.loads(BASELINE_FILE.read_text(encoding="utf-8"))
        return data.get("aggregate")
    except Exception:
        return None


async def get_current_quality(sample: int = DEFAULT_SAMPLE) -> dict[str, Any]:
    """Az /eval_visuals parancshoz: aktuális minta-pontszámok + baseline összevetés."""
    current = await evaluate_sample(sample)
    baseline = load_baseline()
    base_overall = None
    if baseline:
        winner = baseline.get("winner_overall")
        base_overall = (baseline.get("per_variant_avg") or {}).get(winner)
    return {"current": current, "baseline_winner_avg": base_overall,
            "baseline_winner": (baseline or {}).get("winner_overall")}
