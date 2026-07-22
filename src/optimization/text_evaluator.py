"""Szöveg-minőség értékelő — ANGOL LinkedIn posztok pontozása Claude Sonnettel (Phase 14).

A 2026-os algoritmus + engagement kritériumok szerint pontoz: hook erő, emberi érzet,
angol nyelvi minőség (english_quality), angol natívság (english_native_quality), konkrét érték,
voice-konzisztencia, engagement potenciál — plusz anti-pattern lista és (gyenge poszt esetén)
teljes átírási javaslat. A magyar dimenziók (hungarian_*) kódja dormant maradt (SCORE_KEYS_HU,
SYSTEM_PROMPT_HU, _hunglish_flags) — jelenleg nem hívjuk.

Önálló teszt:
    python -m src.optimization.text_evaluator
"""
from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from anthropic import AsyncAnthropic
from dotenv import load_dotenv

from src.generators.base_generator import _repair_and_parse
from src.storage.cost_tracking import record_claude_usage
from src.utils.logging import setup_logging

logger = logging.getLogger(__name__)
load_dotenv(override=False)

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 1400

# Phase 14: az aktív pipeline ANGOL. A nyelvi dimenzió: english_native_quality (univerzális,
# mind a 3 voice). A hungarian_quality/hungarian_nativeness kódot NEM töröljük — dormant marad
# (lásd SCORE_KEYS_HU + SYSTEM_PROMPT_HU lentebb), ha valaha újra kellene a magyar kimenet.
SCORE_KEYS = (
    "hook_strength", "human_feel", "english_quality", "english_native_quality",
    "concrete_value", "voice_consistency", "engagement_potential", "ai_signature_risk",
)
# Dormant (Phase 13.5 magyar pipeline) — jelenleg nem hívjuk, de megőrizzük.
SCORE_KEYS_HU = (
    "hook_strength", "human_feel", "hungarian_quality", "hungarian_nativeness",
    "concrete_value", "voice_consistency", "engagement_potential",
)

# Az értékelőnek átadott voice-elvárás (Phase 14: ANGOL kimenet, magyar KKV közönség).
VOICE_EXPECTATION = {
    "david": "David — builder: direct, technical, concrete; first-hand build experience, real "
             "tooling, buildlog feel. Confident native-level English, hands-on engineer (NOT "
             "corporate). NEVER marketing buzzwords.",
    "adam": "Adam — strategist: business, argumentative, owner-to-owner; ROI/time numbers, "
            "decision-maker lens. Confident native-level English, a sharp operator (NOT a "
            "management-consultant cliché). NEVER technical jargon, NEVER top-down.",
    "plansmart": "PlanSmart — brand: 'we' voice, outcome-oriented, quantified, anonymized client "
                 "results. Polished native-level English B2B voice. NEVER personal opinion or "
                 "builder detail.",
}

# ── Phase 14: ANGOL anti-pattern listák (aktív) ────────────────────────
# AI-tell / stiff-connector kifejezések, amik "translated / ChatGPT" érzetet adnak angolul.
ENGLISH_AI_TELLS = [
    "it's important to note", "it is important to note", "in today's fast-paced world",
    "in today's digital age", "at the end of the day", "when it comes to", "in conclusion",
    "furthermore", "moreover", "delve into", "delve", "navigating the", "in the realm of",
    "testament to", "tapestry", "ever-evolving", "ever-changing landscape", "needle in a haystack",
    "let's dive in", "buckle up", "the bottom line is", "rest assured", "it goes without saying",
]
# Tiltott business-buzzword (a feladat listája + bővítve) — determinisztikus kapás.
ENGLISH_BANNED: list[tuple[str, str]] = [
    (r"\bleverage\b", "leverage → use / build on"),
    (r"\brevolutioniz(?:e|es|ing|ed)\b", "revolutionize → banned hype"),
    (r"\brevolutionary\b", "revolutionary → banned hype"),
    (r"\bgame[- ]?chang(?:er|ing)\b", "game changer → banned entirely"),
    (r"\bseamless(?:ly)?\b", "seamless → just say it works / describe it"),
    (r"\bdisrupt(?:ive|ion|ing)?\b", "disruptive/disruption → banned buzzword"),
    (r"\bcutting[- ]edge\b", "cutting-edge → banned buzzword"),
    (r"\bunlock(?:ing)?\s+your\s+\w+", "unlock your potential → banned cliché"),
    (r"\bsupercharge\b", "supercharge → banned hype"),
    (r"\bsynerg(?:y|ies|istic)\b", "synergy → banned corporate-speak"),
    (r"\bparadigm shift\b", "paradigm shift → banned cliché"),
    (r"\bharness the power\b", "harness the power → banned cliché"),
    (r"\bworld[- ]class\b", "world-class → empty superlative"),
    (r"\bbest[- ]in[- ]class\b", "best-in-class → empty superlative"),
    (r"\bmove the needle\b", "move the needle → tired idiom"),
    (r"\blow[- ]hanging fruit\b", "low-hanging fruit → tired idiom"),
]

# ── ai_signature_risk (Phase 20): determinisztikus "Signs of AI writing" kapás ─────────
# Lásd prompts/ai_writing_signals.md (29 minta, blader/humanizer MIT + Wikipedia WP:AICLEANUP).
# Csak a MEGBÍZHATÓAN reguláris/keyword-alapú mintákat kapjuk itt kódból; a szemantikus
# ítéletet igénylő minták (significance inflation, vague attributions, rule of three, …) a
# SYSTEM_PROMPT-on keresztül Sonnet-re maradnak.
AI_VOCABULARY = [
    "testament to", "tapestry", "delve into", "delve", "intricate", "underscore", "underscores",
    "crucial", "pivotal", "meticulously", "meticulous", "robust", "boasts", "elevate", "elevates",
    "unlock", "unlocks", "realm", "beacon", "nestled", "ever-evolving", "multifaceted",
]
PROMOTIONAL_LANGUAGE = [
    "breathtaking", "stunning", "renowned", "vibrant", "unparalleled", "world-renowned",
    "must-have", "best-in-class", "game-changing", "transformative",
]
SIGNPOSTING_PHRASES = [
    "let's dive in", "here's what you need to know", "buckle up", "let's break it down",
    "without further ado", "in today's rapidly evolving landscape",
]
CHATBOT_ARTIFACTS = [
    "let me know if you have any questions", "i hope this helps", "would you like me to",
    "happy to assist", "as an ai", "i don't have the ability to",
]
CUTOFF_DISCLAIMERS = [
    "as of my last update", "as of my knowledge cutoff", "i don't have access to real-time data",
    "i cannot browse the internet",
]
SYCOPHANTIC_PHRASES = [
    "great question", "you're absolutely right", "that's a fantastic point",
    "i'd be happy to help", "excellent point",
]
GENERIC_CONCLUSIONS = [
    "the future looks bright", "the possibilities are endless", "only time will tell",
    "time will tell", "exciting times ahead", "the sky's the limit",
]
FILLER_PHRASES: list[tuple[str, str]] = [
    (r"\bin order to\b", "in order to → to"),
    (r"\bdue to the fact that\b", "due to the fact that → because"),
    (r"\bat this point in time\b", "at this point in time → now"),
    (r"\bfor the purpose of\b", "for the purpose of → to"),
    (r"\bin the event that\b", "in the event that → if"),
]
HEDGE_WORDS = ["could", "potentially", "possibly", "perhaps", "arguably", "to some extent", "in some cases"]
NEGATIVE_PARALLELISM_RE = re.compile(r"\bit'?s not (?:just |only )?[^,.]+,\s*(?:it'?s|but)\b", re.I)
_EM_DASH_RE = re.compile(r"[—–]")
_CURLY_QUOTE_RE = re.compile(r"[“”‘’]")
_MD_BOLD_RE = re.compile(r"\*\*[^*]+\*\*")
_INLINE_HEADER_RE = re.compile(r"\*\*[^*]{1,40}:\*\*")
_HYPHEN_COMPOUND_RE = re.compile(r"\b[a-zA-Z]+-[a-zA-Z]+\b")  # csak betű-betű (a "25-person" ne számítson)
_GERUND_CHAIN_RE = re.compile(r"\b\w+ing\b[^.!?]*,\s*\w+ing\b[^.!?]*,\s*\w+ing\b", re.I)  # 3+ lánc kell
_EMOJI_RE = re.compile(
    "[\U0001F300-\U0001FAFF\U00002600-\U000027BF\U0001F1E6-\U0001F1FF]"
)
EMOJI_MAX_BY_VOICE = {"david": 1, "adam": 0, "plansmart": 3}


def _ai_signature_flags(text: str, voice: str) -> list[str]:
    """Determinisztikus "Signs of AI writing" kapás (Phase 20, aktív). Lásd prompts/ai_writing_signals.md."""
    t = text or ""
    low = t.lower()
    flags: list[str] = []
    for term in AI_VOCABULARY:
        if term in low:
            flags.append(f"AI-signature: AI vocabulary — “{term}”")
    for term in PROMOTIONAL_LANGUAGE:
        if term in low:
            flags.append(f"AI-signature: promotional language — “{term}”")
    for phrase in SIGNPOSTING_PHRASES:
        if phrase in low:
            flags.append(f"AI-signature: signposting announcement — “{phrase}”")
    for phrase in CHATBOT_ARTIFACTS:
        if phrase in low:
            flags.append(f"AI-signature: chatbot artifact — “{phrase}”")
    for phrase in CUTOFF_DISCLAIMERS:
        if phrase in low:
            flags.append(f"AI-signature: cutoff disclaimer — “{phrase}”")
    for phrase in SYCOPHANTIC_PHRASES:
        if phrase in low:
            flags.append(f"AI-signature: sycophantic tone — “{phrase}”")
    for phrase in GENERIC_CONCLUSIONS:
        if phrase in low:
            flags.append(f"AI-signature: generic conclusion — “{phrase}”")
    for pat, msg in FILLER_PHRASES:
        if re.search(pat, t, re.I):
            flags.append(f"AI-signature: filler phrase — {msg}")
    if NEGATIVE_PARALLELISM_RE.search(t):
        flags.append("AI-signature: negative parallelism (“it's not X, it's/but Y”)")
    n_hedge = sum(low.count(h) for h in HEDGE_WORDS)
    if n_hedge >= 2:
        flags.append(f"AI-signature: excessive hedging ({n_hedge} stacked hedge words)")
    n_dash = len(_EM_DASH_RE.findall(t))
    if n_dash >= 2:
        flags.append(f"AI-signature: em-dash overuse ({n_dash} em/en dashes)")
    if _CURLY_QUOTE_RE.search(t):
        flags.append("AI-signature: curly/smart quotes (should be straight quotes)")
    if _MD_BOLD_RE.search(t):
        flags.append("AI-signature: markdown boldface (**word**) — renders as literal asterisks on LinkedIn")
    if _INLINE_HEADER_RE.search(t):
        flags.append("AI-signature: inline-header list (\"**Label:** ...\") instead of prose")
    n_hyphen = len(_HYPHEN_COMPOUND_RE.findall(t))
    if n_hyphen >= 3:
        flags.append(f"AI-signature: hyphenated word-pair overuse ({n_hyphen} compound modifiers)")
    if _GERUND_CHAIN_RE.search(t):
        flags.append("AI-signature: superficial -ing chain (e.g. \"showcasing…, reflecting…, symbolizing…\")")
    n_emoji = len(_EMOJI_RE.findall(t))
    emoji_max = EMOJI_MAX_BY_VOICE.get(voice, 0)
    if n_emoji > emoji_max:
        flags.append(f"AI-signature: emoji overuse ({n_emoji} emoji, max {emoji_max} for this voice)")
    return flags


# ── Dormant (Phase 13.5 magyar pipeline) — megőrizve, jelenleg NEM hívjuk ──
AI_TELLS = [
    "fontos megérteni", "kihasználva", "lehetőséget biztosítva", "kulcsfontosságú", "jelentős",
    "innovatív", "a mai rohanó világban", "nem szabad elfelejteni", "összességében",
    "ezáltal", "lehetővé teszi", "számos előnnyel",
]
BUZZWORDS = ["forradalom", "forradalmi", "game changer", "diszruptív", "diszrupció", "paradigmaváltás"]

# Hunglish: lefordítatlanul hagyott angol business-jargon (prompts/hungarian_native_guide.md).
# (regex, magyar javaslat) — determinisztikus, kódból ellenőrizhető kapás; a strukturális
# tükörfordításokat a Sonnet fogja külön (a keyword-lista csak az egyértelmű eseteket).
HUNGLISH_JARGON: list[tuple[str, str]] = [
    (r"\bleverage\b", "leverage → „kihasználni” / „építeni rá”"),
    (r"\binsights?\b", "insights → „tanulságok” / „meglátások”"),
    (r"\binzájt\w*", "inzájt → „tanulság” / „meglátás” (magyarosított angol)"),
    (r"\bmindset\b", "mindset → „szemlélet” / „gondolkodásmód”"),
    (r"\bonboarding\b", "onboarding → „bevezetés” / „beillesztés”"),
    (r"\bdeep dive\b", "deep dive → „részletes elemzés”"),
    (r"\btakeaway\b", "takeaway → „tanulság”"),
    (r"\baction items?\b", "action items → „teendők” / „következő lépések”"),
    (r"\bskáláz\w*", "skálázni → „növelni” / „bővíteni” (kivéve technikai dev közönség)"),
    (r"\bscal(?:e|es|ing)\b", "scale/scaling → „növelni” / „bővíteni”"),
]
# „workflow” csak akkor Hunglish, ha NEM tool-specifikus (n8n/make/zapier/… workflow → OK).
_WORKFLOW_RE = re.compile(r"\bworkflow\b", re.I)
_WORKFLOW_TOOL_RE = re.compile(r"\b(?:n8n|make|zapier|airflow|github|ci/?cd|claude|langchain)\s+workflow\b", re.I)

# Phase 14: ANGOL értékelő. A posztok ANGOL nyelvűek (magyar KKV közönség, presztízs-pozicionálás).
SYSTEM_PROMPT = (
    "You are an elite English LinkedIn copywriting critic scoring a post STRICTLY on a 1-10 scale "
    "against 2026 algorithm + engagement criteria. The posts are written in English for a Hungarian "
    "SME-owner audience (English is used deliberately for authority/prestige). You always reply with "
    "a SINGLE JSON object and nothing around it.\n\n"
    "Scoring keys (integer 1-10):\n"
    "  hook_strength         — does it stop the scroll? Is the first ~140 chars a real contrarian/"
    "data/narrative/pain/comparison hook, or flat/generic?\n"
    "  human_feel            — sounds like a real person, or AI-generated? AI-tells and templated "
    "phrasing lower this.\n"
    "  english_quality       — grammar, word choice, natural flow of the English. Awkward or "
    "clearly-translated phrasing lowers this.\n"
    "  english_native_quality — does it read like it was WRITTEN by a confident native English "
    "business writer, not translated? Penalize: (1) banned corporate jargon/hype (leverage, "
    "revolutionize, game changer, seamless, disruptive, cutting-edge, unlock your potential, "
    "synergy, supercharge, paradigm shift); (2) stiff essay connectors (furthermore, moreover, "
    "in conclusion, it's important to note, in today's fast-paced world) and ChatGPT-isms (delve, "
    "tapestry, testament to, ever-evolving); (3) generic LinkedIn-guru rhythm (a wall of tiny "
    "'punchy' one-line sentences with no substance). Reward specific, idiomatic, confident prose "
    "that sounds like a smart operator talking, not a press release.\n"
    "  concrete_value        — concrete number/name/example, or vague ('a lot', 'many', 'several')? "
    "Empty platitudes = low.\n"
    "  voice_consistency     — does it fit the given voice?\n"
    "  engagement_potential  — would people comment/save? Real question or insight, not engagement-bait.\n"
    "  ai_signature_risk     — how strongly does this read as AI-generated, per Wikipedia's \"Signs of "
    "AI writing\" catalog (WikiProject AI Cleanup) / the blader/humanizer pattern list "
    "(prompts/ai_writing_signals.md)? SCALE IS INVERTED from the other keys: 1 = reads fully human, no "
    "AI-tells; 10 = obviously AI-generated. Penalize clusters (not a single instance) of: significance "
    "inflation (grand claims about importance with no substance), notability name-dropping (vague "
    "'recognized by experts' citations), vague attributions ('experts believe...' with no source), "
    "formulaic 'despite challenges, continues to thrive' sections, copula avoidance ('serves as' instead "
    "of 'is'), rule-of-three padding (forcing lists into exactly 3 items), synonym cycling (avoiding a "
    "repeated word via near-synonyms), false ranges ('from X to Y' for non-continuous items), passive "
    "voice/subjectless fragments ('no configuration needed'), Title Case Headings, persuasive-authority "
    "tropes ('at its core, what matters is...'), and fragmented headers (heading immediately restated as "
    "a sentence). A single em dash, one non-cliché adjective, or correct grammar is NOT an AI-tell by "
    "itself — only flag clusters of multiple distinct patterns.\n\n"
    "What to FIND and flag in anti_patterns (quote the exact problem span):\n"
    f"  • AI-tell / stiff connectors: {', '.join(ENGLISH_AI_TELLS[:12])}, …\n"
    "  • Banned buzzwords: leverage, revolutionize, game changer, seamless, disruptive, "
    "cutting-edge, unlock your potential, synergy, supercharge, paradigm shift, world-class\n"
    "  • Translated-from-Hungarian phrasing / awkward English word order\n"
    "  • Generic 'LinkedIn guru' rhythm: many tiny punchy lines in a row with no substance\n"
    "  • Vague quantity instead of a concrete number ('a lot', 'many', 'several', 'tons')\n"
    "  • Missing human signal (no 'yesterday', 'last Tuesday', 'a client of ours', 'we shipped', a concrete time)\n"
    "  • Empty business platitude / cliché\n"
    "  • Bad CTA: 'Agree?', engagement-bait, external link, 'DM me', 'book a call'\n"
    "  • Length outside the 1300-1900 character sweet spot (too short = thin, too long = loses "
    "dwell/engagement) — a post far outside this range CANNOT score 9+ overall.\n"
    "  • AI-writing-signal clusters (see ai_signature_risk above): significance inflation, formulaic "
    "'despite challenges' framing, copula avoidance, rule-of-three padding, synonym cycling, false "
    "ranges, subjectless passive fragments, persuasive-authority tropes, fragmented headers.\n\n"
    "SCORING CAP — ai_signature_risk: if ai_signature_risk is 7-8, overall_score CANNOT be 8+; if "
    "ai_signature_risk is 9-10, overall_score CANNOT be 6+ (a post that reads as clearly AI-generated "
    "cannot score as excellent, no matter how strong the other dimensions are).\n\n"
    "overall_score is holistic (NOT the average of sub-scores). If overall < 7, rewrite_suggestion "
    "must be a COMPLETE, ready English post (same voice, strong hook). If >= 7, rewrite_suggestion "
    "is an empty string.\n\n"
    "FABRICATION CHECK (hard, BINARY — set fabrication_risk true or false):\n"
    "A specific factual claim is legitimate ONLY if it is SOURCED. Valid sources: (1) a vetted case "
    "study (CONTENT TYPE == 'case_study'), (2) a manual_instruction the author wrote themselves "
    "(HAS_MANUAL_SOURCE == true), (3) real third-party facts about the subject of the news/feed_item "
    "(CONTENT TYPE == 'ai_news'). Set fabrication_risk = TRUE when the post makes an UNSOURCED, "
    "specific, FIRST-PERSON factual claim: an invented client story (\"we had a client who…\", \"one "
    "of our customers…\", \"at our client…\"), a named person/company presented as a real client, a "
    "dollar figure / headcount / timeframe tied to \"we / our client\" as a real result, or a fake "
    "specific date (\"last Tuesday\", \"three weeks ago\") on an invented event. Rules by content type:\n"
    "  • case_study → specific anonymized client claims are EXPECTED → fabrication_risk = false "
    "(only true if it invents a NAMED real person/company beyond the anonymized seed).\n"
    "  • ai_news → third-party facts about the news subject are fine; a first-person \"our client…\" "
    "result NOT backed by HAS_MANUAL_SOURCE → fabrication_risk = true.\n"
    "  • educational / consultant_builder / workshop_promo → ANY specific first-person client "
    "anecdote → fabrication_risk = true UNLESS HAS_MANUAL_SOURCE == true.\n"
    "NOT fabrication: general honest patterns (\"what we often see on small teams…\"), clearly-"
    "hypothetical framing (\"imagine a 30-person firm…\"), and conceptual explanation. When "
    "fabrication_risk is true, fabrication_reason must QUOTE the exact fabricated span and say why; "
    "when false, fabrication_reason is an empty string.\n\n"
    'Reply ONLY with this JSON: {"hook_strength":int,"human_feel":int,"english_quality":int,'
    '"english_native_quality":int,"concrete_value":int,"voice_consistency":int,'
    '"engagement_potential":int,"ai_signature_risk":int,"anti_patterns":[...],'
    '"overall_score":float,"fabrication_risk":true|false,"fabrication_reason":"...",'
    '"rewrite_suggestion":"...","feedback":"..."}'
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]


@lru_cache(maxsize=1)
def _client() -> AsyncAnthropic:
    return AsyncAnthropic()


def _clamp(value: Any, lo: int, hi: int, default: int) -> int:
    try:
        return max(lo, min(hi, int(round(float(value)))))
    except (TypeError, ValueError):
        return default


def _english_flags(text: str) -> list[str]:
    """Determinisztikus angol jargon/AI-tell kapás (Phase 14, aktív)."""
    t = text or ""
    low = t.lower()
    flags = []
    for pat, msg in ENGLISH_BANNED:
        if re.search(pat, t, re.I):
            flags.append(f"banned buzzword: {msg}")
    for tell in ENGLISH_AI_TELLS:
        if tell in low:
            flags.append(f"AI-tell / stiff connector: “{tell}”")
    for vague in (" a lot of ", " lots of ", " many ", " several ", " tons of ", " numerous "):
        if vague in f" {low} ":
            flags.append(f"vague quantity: “{vague.strip()}” (use a concrete number)")
    if "agree?" in low or "do you agree" in low:
        flags.append("engagement-bait CTA: “Agree?”")
    if "book a call" in low or "dm me" in low or "book a demo" in low:
        flags.append("pushy CTA: “book a call” / “DM me” (Calendly is in bio only)")
    if "http://" in low or "https://" in low:
        flags.append("external link in post (-60% reach)")
    return flags


def _hunglish_flags(text: str) -> list[str]:
    """DORMANT (Phase 13.5): determinisztikus Hunglish-kapás. Jelenleg nem hívjuk a pipeline-ban."""
    t = text or ""
    flags = []
    for pat, msg in HUNGLISH_JARGON:
        if re.search(pat, t, re.I):
            flags.append(f"Hunglish jargon: {msg}")
    if _WORKFLOW_RE.search(t) and _WORKFLOW_RE.search(_WORKFLOW_TOOL_RE.sub("", t)):
        flags.append("Hunglish jargon: workflow → „folyamat” (kivéve konkrét tool: „n8n workflow”)")
    return flags


# Cél hosszsáv (linkedin_optimization.md: 1300-1900 kar a sweet spot).
CHAR_MIN, CHAR_MAX = 1300, 1900


def _length_flags(text: str) -> list[str]:
    """Determinisztikus hossz-ellenőrzés (a poszt legyen 1300-1900 karakter)."""
    n = len(text or "")
    if n < CHAR_MIN:
        return [f"too short: {n} chars (target {CHAR_MIN}-{CHAR_MAX})"]
    if n > CHAR_MAX:
        return [f"too long: {n} chars (target {CHAR_MIN}-{CHAR_MAX}) — trim to the sweet spot"]
    return []


def _local_flags(text: str) -> list[str]:
    """Determinisztikus, kódból ellenőrizhető anti-pattern jelek (Phase 14: ANGOL pipeline)."""
    return _english_flags(text) + _length_flags(text)


class TextEvaluator:
    """Scores ENGLISH LinkedIn posts against 2026 engagement criteria (Sonnet). Phase 14."""

    async def evaluate_post(
        self, post_content: str, voice: str, content_type: str, *, has_manual_source: bool = False
    ) -> dict[str, Any]:
        """Egy poszt értékelése. Hiba esetén overall_score=0 + a hiba a feedbackben.

        has_manual_source: True, ha a poszt forrása egy /create manual_instruction volt (a szerző
        maga írta le a konkrétumot) — ilyenkor a specifikus állítás sourced, nem fabrikáció.
        """
        expectation = VOICE_EXPECTATION.get(voice, "")
        user = (
            f"VOICE: {voice}\nVOICE EXPECTATION: {expectation}\nCONTENT TYPE: {content_type}\n"
            f"HAS_MANUAL_SOURCE: {str(bool(has_manual_source)).lower()}\n\n"
            f"POST:\n{post_content}\n\nScore the post. Return only the JSON."
        )
        try:
            msg = await _client().messages.create(
                model=MODEL, max_tokens=MAX_TOKENS, system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user}],
            )
            record_claude_usage(msg, MODEL)
            data = _repair_and_parse(msg.content[0].text if msg.content else "")
        except Exception as exc:
            logger.warning("[text-eval] hiba (%s): %s", voice, str(exc)[:120])
            return self._error(f"eval hiba: {str(exc)[:100]}")
        if not data:
            return self._error("a modell nem adott értelmezhető JSON-t")
        return self._normalize(data, post_content, voice)

    @staticmethod
    def _normalize(data: dict[str, Any], post_content: str, voice: str = "") -> dict[str, Any]:
        scores = {k: _clamp(data.get(k), 1, 10, 5) for k in SCORE_KEYS}
        # Determinisztikus angol-jargon büntetés: a banned-lista/AI-tell találatai lehúzzák az
        # english_native_quality-t, akkor is, ha a modell elnézte (1 találat → max 6, 2+ → max 4).
        # Csak a nyelvi (jargon/AI-tell) jeleket számoljuk, a link/CTA jelet nem.
        n_jargon = sum(1 for f in _english_flags(post_content)
                       if f.startswith("banned buzzword") or f.startswith("AI-tell"))
        if n_jargon:
            cap = 4 if n_jargon >= 2 else 6
            scores["english_native_quality"] = min(scores["english_native_quality"], cap)
        flags = [str(f) for f in (data.get("anti_patterns") or []) if str(f).strip()]
        for lf in _local_flags(post_content):
            if lf not in flags:
                flags.append(lf)
        # ai_signature_risk (Phase 20): determinisztikus "Signs of AI writing" találatok emelik a
        # kockázatot akkor is, ha a modell nem vette észre (fordított skála — magasabb = rosszabb,
        # lásd _ai_signature_flags). 1 találat → min 4, 2 → min 6, 3+ → min 7.
        ai_sig_flags = _ai_signature_flags(post_content, voice)
        if ai_sig_flags:
            floor = 7 if len(ai_sig_flags) >= 3 else 6 if len(ai_sig_flags) == 2 else 4
            scores["ai_signature_risk"] = max(scores["ai_signature_risk"], floor)
        for f in ai_sig_flags:
            if f not in flags:
                flags.append(f)
        try:
            overall = float(data.get("overall_score"))
        except (TypeError, ValueError):
            # Fallback: ai_signature_risk fordított skálájú (magasabb = rosszabb), a 11-x
            # inverzióval keveredik bele a többi (magasabb = jobb) dimenzió átlagába.
            quality = {k: v for k, v in scores.items() if k != "ai_signature_risk"}
            quality["ai_signature_quality"] = 11 - scores["ai_signature_risk"]
            overall = sum(quality.values()) / len(quality)
        overall = max(1.0, min(10.0, round(overall, 2)))
        fabrication_risk = bool(data.get("fabrication_risk"))
        fabrication_reason = str(data.get("fabrication_reason") or "").strip()
        if fabrication_risk and not fabrication_reason:
            fabrication_reason = "unsourced first-person specific claim (model flagged, no span given)"
        return {
            **scores, "anti_patterns": flags, "overall_score": overall,
            "fabrication_risk": fabrication_risk, "fabrication_reason": fabrication_reason,
            "rewrite_suggestion": str(data.get("rewrite_suggestion") or "").strip(),
            "feedback": str(data.get("feedback") or "").strip(),
        }

    @staticmethod
    def _error(reason: str) -> dict[str, Any]:
        return {
            **{k: 0 for k in SCORE_KEYS}, "anti_patterns": ["eval_error"],
            "overall_score": 0.0, "fabrication_risk": False, "fabrication_reason": "",
            "rewrite_suggestion": "", "feedback": reason, "error": True,
        }


async def _demo() -> int:
    sample = ("In today's fast-paced world, it's important to note that businesses must leverage "
              "cutting-edge AI to revolutionize their workflows. This is a real game changer. Agree?")
    out = await TextEvaluator().evaluate_post(sample, "adam", "ai_news")
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import asyncio

    setup_logging()
    raise SystemExit(asyncio.run(_demo()))
