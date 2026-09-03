"""Hang-szintű értesítési kadencia — EGY igazságforrás arról, melyik hangot melyik
ütemezett worker kezelheti.

Fázis 23: Ádám értesítései konszolidálódnak. A reggeli poszt / breaking reakció / heti
videó-ötlet HÁROM külön Telegram-csatornát nyitott ugyanarra a hangra (élesben 7 nap alatt
19 poszt + 1 videó-ötlet ment ki csak Ádámnak) — ezt váltja fel a 2 naponta EGY üzenetet
küldő `adam_digest_worker`.

Ez a modul azért külön fájl (és nem a digest workerben lakik), mert a három meglévő worker
(`morning_post_worker`, `breaking_news_worker`, `video_idea_worker`) IS importálja a
kizárás-ellenőrzéshez — a digest worker viszont MINDHÁRMAT importálja a logikájuk
újrahasznosításához. Közös, függőség nélküli modul nélkül ez körkörös import lenne.

A lista config-vezérelt (`DIGEST_VOICES` env, alapból `adam`), és MINDIG hívási időben
olvasódik (nem import-időben), hogy a teszt monkeypatch-elni tudja.
"""

from __future__ import annotations

from src.core.config.settings import get_settings


def digest_voices() -> set[str]:
    """A digestbe konszolidált hangok (kisbetűsítve). Alapból: {"adam"}."""
    return {v.strip().lower() for v in get_settings().digest_voices if v.strip()}


def is_digest_voice(voice: str) -> bool:
    """True, ha ezt a hangot a digest worker kezeli — vagyis a reggeli/breaking/videó
    workernek NEM szabad rá magától tüzelnie (double-fire védelem)."""
    return (voice or "").strip().lower() in digest_voices()
