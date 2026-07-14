# AI Writing Signals — 29-pattern reference

Reference catalog for the `ai_signature_risk` evaluation dimension (`src/optimization/text_evaluator.py`)
and the matching targeted-fix rewrite step (`src/optimization/text_improver.py`). Not a prompt that gets
sent to Claude verbatim — the evaluator's `SYSTEM_PROMPT` summarizes this list; this file is the detailed
reference a human (or a future Claude session) reads to understand *why* a pattern is flagged and what a
fix looks like.

## Attribution

Patterns and before/after phrasing adapted from:
- **[blader/humanizer](https://github.com/blader/humanizer)** (MIT license) — a Claude Code skill that
  catalogs 33 AI-writing tells. This file uses patterns 1–29 of that catalog; patterns 30–33
  (diff-anchored writing, manufactured punchlines, aphorism formulas, conversational rhetorical openers)
  are omitted here as they target encyclopedia articles and code-changelog prose, not short first-person
  LinkedIn posts.
- **[Wikipedia:Signs of AI writing](https://en.wikipedia.org/wiki/Wikipedia:Signs_of_AI_writing)** —
  WikiProject AI Cleanup's essay, the original research this catalog is built on.

Per the humanizer README's own caveat, worth repeating here: **none of these are reliable signals in
isolation.** Perfect grammar, a single em dash, one non-cliché adjective, or an unsourced claim are not
"AI tells" by themselves — it's the *cluster* of multiple patterns in one post that indicates AI
authorship. The evaluator treats them accordingly (see "How this maps to the evaluator" below).

## How this maps to the evaluator

Each pattern below is tagged **[deterministic]** or **[LLM]**:
- **[deterministic]** — a regex/keyword/counter check in `_ai_signature_flags()` (`text_evaluator.py`).
  Reliable because the trigger is a fixed phrase, punctuation mark, or countable structure.
- **[LLM]** — requires semantic judgment (is this claim actually vague? is this really a forced triplet?)
  and is left to Sonnet's holistic read via the `ai_signature_risk` scoring instructions.

---

### 1. Significance inflation [LLM]
Inflating a plain fact into a grand claim about broader importance.
- **Before:** "This launch marks a pivotal moment in the evolution of AI-assisted marketing."
- **After:** "We shipped scheduled posting this week."

### 2. Notability name-dropping [LLM]
Stacking vague citations/mentions instead of one concrete detail.
- **Before:** "The tool has been recognized by leading publications and industry experts."
- **After:** "A 2024 case study cited a 40% drop in manual review time."

### 3. Superficial -ing analyses [deterministic + LLM]
Chaining present-participle clauses onto a claim for fake depth, with no actual analysis behind them.
- **Before:** "The new dashboard uses a blue palette, showcasing trust, reflecting stability, and
  symbolizing our commitment to clarity."
- **After:** "The dashboard uses blue because our user tests showed it read as calmer than the old orange."
- Deterministic check: 3+ consecutive gerund clauses chained by commas in one sentence
  (`showcasing…, reflecting…, symbolizing…`) is flagged directly; a natural pair ("building, shipping")
  is common voice-authentic phrasing and is NOT flagged on its own.

### 4. Promotional language [deterministic]
Sales-brochure adjectives in a context that should be plain description.
- **Before:** "A breathtaking, best-in-class solution for growing teams."
- **After:** "A scheduling tool for teams under 20 people."
- Deterministic keyword list: *breathtaking, stunning, renowned, vibrant, unparalleled, world-renowned,
  must-have, best-in-class, game-changing, transformative.*

### 5. Vague attributions [LLM]
Citing unnamed "experts" or "studies" instead of a real, checkable source.
- **Before:** "Experts believe automation plays a crucial role in SME growth."
- **After:** "A 2019 Chinese Academy of Sciences survey found automation adoption correlated with SME revenue growth."

### 6. Formulaic challenge sections [LLM]
The stock "despite challenges, X continues to thrive/evolve" shape with no real content behind it.
- **Before:** "Despite challenges, our platform continues to thrive and grow."
- **After:** "We lost two enterprise deals to pricing this quarter and cut our starter tier in response."

### 7. AI vocabulary [deterministic]
High-frequency LLM tells — words a human writer rarely reaches for in casual business prose.
- **Before:** "This is a testament to how the tapestry of modern work is evolving."
- **After:** "This shows how work is changing."
- Deterministic keyword list: *testament (to), tapestry, delve (into), intricate, landscape (metaphorical),
  underscore(s), crucial, pivotal, meticulous(ly), robust, boasts, elevate(s), unlock(s), realm, beacon,
  nestled, ever-evolving, multifaceted.*

### 8. Copula avoidance [LLM]
Reaching for "serves as / functions as / represents" instead of a plain "is."
- **Before:** "The dashboard serves as the central hub for all reporting."
- **After:** "The dashboard is where all reporting lives."

### 9. Negative parallelisms [deterministic + LLM]
The "it's not just X, it's Y" (or "not only... but also") construction, way overused by LLMs.
- **Before:** "It's not just a tool, it's a partner in your growth."
- **After:** "It's a tool that flags anomalies before they become expensive."
- Deterministic regex: `it'?s not (?:just |only )?[^,.]+,\s*(?:it'?s|but)\b` (case-insensitive).

### 10. Rule of three overuse [LLM]
Forcing every list into exactly three items regardless of whether three is the honest count.
- **Before:** "The event featured keynote sessions, panel discussions, and networking opportunities."
- **After:** "The event was two keynotes and an open Q&A."

### 11. Synonym cycling (elegant variation) [LLM]
Avoiding repeating the same (correct, clear) noun by cycling through near-synonyms.
- **Before:** "The protagonist faces challenges. The main character overcomes obstacles. The central
  figure ultimately triumphs."
- **After:** "The founder nearly ran out of runway twice before the product found its market."

### 12. False ranges [LLM]
"From X to Y" framing items that aren't actually a continuum.
- **Before:** "Our platform covers everything from onboarding to advanced analytics."
- **After:** "Our platform handles onboarding, billing, and analytics."

### 13. Passive voice / subjectless fragments [LLM]
Dropping the actor to sound authoritative, or leaving instructions ownerless.
- **Before:** "No configuration is needed. Data is synced automatically."
- **After:** "You don't need to configure anything — we sync your data automatically."

### 14. Em dash overuse [deterministic]
Not one em dash (fine, human writers use them) — a *chain* of them standing in for real punctuation.
- **Before:** "The system works fast — almost instantly — and never drops a request — even under load."
- **After:** "The system works fast, almost instantly, and never drops a request, even under load."
- Deterministic check: 2+ em/en dashes (—, –) in a single post is flagged; 0–1 is normal human usage.

### 15. Boldface overuse [deterministic]
Markdown `**bold**` mechanically slapped on acronyms/terms. On LinkedIn this is a real production bug,
not just a style tell — LinkedIn doesn't render Markdown, so `**word**` ships as literal asterisks.
- **Before:** "We track **OKRs** and **KPIs** every week."
- **After:** "We track OKRs and KPIs every week."
- Deterministic regex: any `**...**` span in the post body is flagged (LinkedIn can't render it).

### 16. Inline-header lists [deterministic]
Bolded mini-headers glued to list items instead of actual prose or a real list.
- **Before:** "**Speed:** it's fast. **Reliability:** it rarely fails. **Cost:** it's cheap."
- **After:** "It's fast, rarely fails, and costs less than the tool it replaced."
- Deterministic regex: `\*\*[^*]{1,40}:\*\*` (a bolded label ending in a colon).

### 17. Title Case Headings [LLM]
Section headers in Title Case instead of sentence case — rare in LinkedIn posts (few use headers at
all), but flagged if a post does use structured headers.
- **Before:** "Strategic Negotiations And Global Partnerships"
- **After:** "Strategic negotiations and global partnerships"

### 18. Emojis [deterministic, voice-aware threshold]
Decorative emoji as visual filler rather than genuine punctuation. Threshold is voice-specific because
the voice prompts already set different tolerances (David max 0–1, Ádám effectively 0, PlanSmart max
2–3 for structural bullets in workshop posts).
- **Before:** "🚀 We just shipped a huge update! 💡 Check it out below ✅"
- **After:** "We shipped an update this week. Details below."
- Deterministic check: counts emoji codepoints; flags if count exceeds the voice's threshold.

### 19. Curly quotes [deterministic]
Typographic “smart quotes” / ‘smart apostrophes’ instead of straight ones — a classic LLM-output tell
copy-pasted straight into a plain-text post.
- **Before:** He said “just ship it” and we did.
- **After:** He said "just ship it" and we did.
- Deterministic regex: any of `“ ” ‘ ’` in the text.

### 20. Chatbot artifacts [deterministic]
Leftover assistant-mode phrasing that has no place in a first-person LinkedIn post.
- **Before:** "I hope this helps! Let me know if you have any questions."
- **After:** *(removed entirely — a LinkedIn post doesn't address "the user")*
- Deterministic keyword list: *"let me know if you have any questions", "i hope this helps", "would you
  like me to", "happy to assist", "as an ai", "i don't have the ability to".*

### 21. Cutoff disclaimers [deterministic]
Knowledge-cutoff hedging that only makes sense inside a chat interface.
- **Before:** "As of my last update, adoption trends were still rising."
- **After:** "Adoption trends were still rising as of the 2025 report."
- Deterministic keyword list: *"as of my last update", "as of my knowledge cutoff", "i don't have access
  to real-time data", "i cannot browse the internet".*

### 22. Sycophantic tone [deterministic]
Reflexive flattery before answering — meaningless in a post with no one to flatter.
- **Before:** "Great question! You're absolutely right that automation matters."
- **After:** "Automation matters more than most owners think."
- Deterministic keyword list: *"great question", "you're absolutely right", "that's a fantastic point",
  "i'd be happy to help", "excellent point".*

### 23. Filler phrases [deterministic]
Padding that adds length without meaning; strip to the plain word.
- **Before:** "In order to reduce costs, and due to the fact that manual review was slow, we automated it."
- **After:** "To cut costs, and because manual review was slow, we automated it."
- Deterministic keyword list: *"in order to" → "to", "due to the fact that" → "because", "at this point
  in time" → "now", "for the purpose of" → "to", "in the event that" → "if".*

### 24. Excessive hedging [deterministic]
Stacking multiple hedge-words on one claim until it says nothing.
- **Before:** "This could potentially possibly help improve results to some degree."
- **After:** "This may improve results."
- Deterministic check: 2+ hedge words stacked in one clause (*could, potentially, possibly, perhaps,
  arguably, to some extent, in some cases*).

### 25. Generic conclusions [deterministic]
Closing on an empty, unfalsifiable prediction instead of an actual point or plan.
- **Before:** "The future looks bright, and the possibilities are truly endless."
- **After:** "Next month we're testing this on two more accounts."
- Deterministic keyword list: *"the future looks bright", "the possibilities are endless", "only time
  will tell", "time will tell", "exciting times ahead", "the sky's the limit".*

### 26. Hyphenated word pairs [deterministic]
Compulsive hyphenation of compound modifiers even in predicate position, or stacking several in one post.
- **Before:** "Our data-driven, customer-facing, cross-functional approach is best-in-class."
- **After:** "Our approach uses customer data across teams."
- Deterministic check: 3+ alphabetic hyphenated compound-modifier matches (`\b[a-zA-Z]+-[a-zA-Z]+\b`,
  e.g. data-driven, customer-facing, cross-functional) in one post is flagged. Numeric-led hyphenates
  ("25-person", "3-week") are excluded — those are ordinary business specifics, not an AI tell.

### 27. Persuasive authority tropes [LLM]
Faux-profound framing device instead of just stating the point.
- **Before:** "At its core, what really matters is trust."
- **After:** "Trust is what determines whether they renew."

### 28. Signposting announcements [deterministic]
Announcing that you're about to explain something instead of just explaining it.
- **Before:** "Let's dive in and break down exactly what you need to know."
- **After:** *(cut — start directly with the first point)*
- Deterministic keyword list: *"let's dive in", "here's what you need to know", "buckle up", "let's
  break it down", "without further ado", "in today's rapidly evolving landscape".*

### 29. Fragmented headers [LLM]
A heading immediately followed by a sentence that just restates the heading.
- **Before:** "**Results**\nThe results of this initiative were as follows:"
- **After:** "**Results**\nWe cut review time from 12 hours to 20 minutes."

---

## Quick keyword index (deterministic checks only)

For implementers: the exact keyword/regex lists live in `_ai_signature_flags()` in
`src/optimization/text_evaluator.py`. This table is a human-readable index, not the source of truth —
if the two disagree, the code wins.

| # | Pattern | Trigger |
|---|---------|---------|
| 3 | Superficial -ing chains | 3+ chained gerund clauses in one sentence |
| 4 | Promotional language | keyword list |
| 7 | AI vocabulary | keyword list |
| 9 | Negative parallelisms | `it's not X, it's/but Y` regex |
| 14 | Em dash overuse | 2+ em/en dashes |
| 15 | Boldface overuse | any `**bold**` span |
| 16 | Inline-header lists | `**Label:**` regex |
| 18 | Emojis | count > voice threshold |
| 19 | Curly quotes | any curly quote/apostrophe char |
| 20 | Chatbot artifacts | keyword list |
| 21 | Cutoff disclaimers | keyword list |
| 22 | Sycophantic tone | keyword list |
| 23 | Filler phrases | keyword list |
| 24 | Excessive hedging | 2+ stacked hedge words |
| 25 | Generic conclusions | keyword list |
| 26 | Hyphenated word pairs | 3+ compound-modifier matches |
| 28 | Signposting announcements | keyword list |

Patterns 1, 2, 5, 6, 8, 10, 11, 12, 13, 17, 27, 29 have no reliable regex and are scored by Sonnet's
holistic read against the descriptions above.
