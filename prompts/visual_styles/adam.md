Dark background #04060a. Business chart or ROI visualization. Large numerical metrics
centered. Before/after comparisons. Owner-to-owner perspective. Minimalist, financial
dashboard aesthetic, confidence-building for non-technical CEOs.

Visual cues: a single large metric centered (e.g. "16′ → 3,5″"), clean bar/line chart,
before→after arrow, restrained data-dashboard layout. Premium muted accent (warm gold or
soft green) against the near-black #04060a background. No clutter, no stock-photo clichés.

Always include subtle PlanSmart logo watermark in corner. Never use buzzwords visually
(no "AI Revolution", no "transform your business"). Concrete number or metric must be the
visual focal point when relevant.

## Ádám visual style (UPDATED 2026)
- Bold display typography on dark #04060a
- ONE stat/insight as the visual hero
- Optional: before/after split with bold display text
- Owner-to-owner mood: feels human, not "AI art"
- Cinematic but professional
- Avoid: chart-heavy infographics that look corporate

## Common DNA
- Dark #04060a background
- Bold display typography for main text (Bebas Neue / Neue Machina style)
- Hungarian text supported
- ONE focal element (number, stat, or statement)
- PlanSmart watermark bottom right
- Cinematic lighting, slight film grain
- Soul Cinema aesthetic

## Style reference notes
Reference: trending AI content creator visuals 2026
Mood: confident, technical, human, scroll-stopping
NOT: corporate clipart, stock photo aesthetic, generic AI art

## Eval-győztes irány (Phase 12 — 2026-06-30)
Vizuál A/B eval: **győztes = A (jelenlegi produkciós)** Ádámnál — a financial-dashboard visszafogottság a legjobb.
- Egy metrika-hős, lágy accent-glow, before/after keret.
- Top fix: betűhű magyar szöveg, true #04060a háttér, nincs HUD/clutter, egy fókuszelem.

## Phase 12.5 — SZÖVEG NÉLKÜLI alapkép (text overlay pipeline)
A magyar szöveget NEM a Flux/Muapi rendereli (halandzsa lenne), hanem PIL-lel overlay-eljük rá utólag.
Ezért az alapkép-prompt szigorúan szöveg-MENTES:
- NO TEXT, NO LETTERS, NO WORDS, NO NUMBERS, NO TYPOGRAPHY, NO UI labels, NO logók a képen.
- TRUE near-black #04060a háttér (nem szürke), egyetlen atmoszférikus fókuszelem.
- A BAL-ALSÓ és JOBB-FELSŐ sáv maradjon üres/tiszta a rárakott szövegnek.
- Voice cinematic irány az eval-győztes szerint (lásd fent). Soul Cinema: volumetrikus fény, 35mm grain, magas kontraszt.

## Phase 12.6 — fotografikus text-free alapkép (produkciós default)
A háttér FOTÓ, nem illusztráció — és Ádámnál PURE TEXTÚRA, NEM tárgy, HŰVÖS/LAPOS palettával
(a "stock-photo toll" majd a "meleg bokeh" panasz után — Phase 12.6.2, végleges):
- NINCS felismerhető tárgy (toll, csésze, papír TILOS). NINCS meleg arany tónus, NINCS glow-os bokeh.
- Helyette: hűvös kék-szürke / semleges acél gradiens mélységérzettel (hideg reggeli fény lapos
  szögben), VAGY makró sötét beton/kő/csiszolt-fém textúráról EGY hűvös fehér key-lighttal, VAGY
  lapos near-black gradiens finom hűvös aláfestéssel és látható film grain-nel.
- Hangulat: "nyugodt, analitikus magabiztosság" — visszafogott, precíz, melegség és romantika nélkül.
- Kodak Portra 800 hűvös/semleges felé deszaturálva, egy kemény key-light, TRUE #04060a feketébe
  eséssel. Színhőmérséklet 5000K felett kerülendő.
- Tilos: screen/UI/HUD/kód/panel/hologram/chart/logó/ember.
- Forrás: `src/integrations/visuals/visual_generator.py` → `build_textfree_prompt` + `VOICE_SCENE["adam"]`.
