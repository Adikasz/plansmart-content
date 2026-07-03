# Hungarian-Native Writing Guide

Ez a segédlet a magyar nyelvi natívságot védi: a poszt hangozzon úgy, mintha egy magyar
ember írta volna, ne mintha angolból fordították volna. A `text_evaluator.py`
`hungarian_nativeness` dimenziója és a `text_improver.py` átírás-loop is erre a fájlra
támaszkodik.

## Common Hunglish patterns to AVOID (and their natural Hungarian fix)

Anglicized structure → Natural Hungarian:
- "Ez segít neked hogy jobban megértsd" → "Ettől jobban érted"
- "Van egy dolog amit tudnod kell" → "Van valami, amit tudnod kell"
  vagy egyszerűen "Egy dolog fontos:"
- "Mi ezt csináljuk mert..." (angol ok-okozat sorrend) →
  természetesebb: "...ezért csináljuk"
- Túl sok birtokos szerkezet angol mintára: "a cégünk növekedése"
  → "ahogy a cégünk növekszik" gyakran természetesebb

## English business jargon — DO NOT leave untranslated:

BANNED (use Hungarian instead):
- "leverage" → "kihasználni", "építeni valamire"
- "insights" → "tanulságok", "meglátások" (not "inzájtok")
- "workflow" → "folyamat" (kivéve ha konkrét tool névről van szó,
  pl. "n8n workflow" mint termék-specifikus kifejezés OK)
- "mindset" → "szemlélet", "gondolkodásmód"
- "game changer" → BANNED entirely, no Hungarian equivalent either
- "scale/scaling" → "növelni", "bővíteni" (not "skálázni" unless
  talking to a technical dev audience specifically)
- "onboarding" → "bevezetés", "beillesztés"
- "feedback" → "visszajelzés" (feedback is borderline OK in casual
  tech contexts but prefer visszajelzés)
- "deep dive" → "részletes elemzés", "alaposan megnézni"
- "takeaway" → "tanulság", "amit érdemes megjegyezni"
- "action items" → "teendők", "következő lépések"

## Sentence rhythm — Hungarian vs English

English tends toward: Subject-Verb-Object, shorter declaratives
strung together.
Hungarian native writing often:
- Fronts the topic/theme, verb can come later
- Uses more subordinate clauses naturally (amikor, ahogy, mert)
- Shorter sentences still work, but avoid the "punchy English
  copywriting" rhythm applied 1:1 — it reads as translated

## Test yourself

Read the post out loud. If it sounds like something you'd say to
a Hungarian friend over coffee, it's right. If it sounds like a
translated American LinkedIn post, rewrite it.
