Dark background #04060a. Code editor, terminal, architecture diagram aesthetic.
Monospace font accents (Fragment Mono character — clean technical monospace).
Often shows code snippets, system diagrams, or "behind the scenes" builder content.
Minimalist, technical, confidence-building for technical business owners.

Visual cues: subtle terminal window, syntax-highlighted code fragment, node-and-edge
system diagram, faint grid, thin geometric lines. Cool accent glow (teal/blue) against
the near-black #04060a background. No people unless a headshot reference is provided.

Always include subtle PlanSmart logo watermark in corner. Never use buzzwords visually
(no "AI Revolution", no "transform your business"). Concrete number or metric must be
the visual focal point when relevant.

## Dávid visual style (UPDATED 2026)
- Modern bold display typography (Bebas Neue / Neue Machina aesthetic)
- Dark #04060a background
- ONE giant focal element: number, stat, or single bold statement
- Hungarian text in display font
- Subtle PlanSmart logo bottom right
- Cinematic depth, slight film grain
- High contrast — must stand out in dark mode AND light mode feeds
- Avoid: cluttered diagrams, multiple competing elements, photo-realistic people

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
Vizuál A/B eval (10 poszt × 4 variáns): **győztes = C (filmes)** Dávidnál (átlag a leggyengébb, 5.14 → cinematic emeli).
- Noir treatment: egy erős key-light, mély árnyékok, markáns 35mm film grain, atmoszférikus mélység.
- Top fix: a magyar overlay-szöveget BETŰHŰEN kell renderelni — tilos kitalált felirat/kód.
- Tilos: szürke (nem #04060a) háttér, HUD-panelek, pseudo-code, másodlagos szövegblokkok, több fókuszpont.

## Phase 12.5 — SZÖVEG NÉLKÜLI alapkép (text overlay pipeline)
A magyar szöveget NEM a Flux/Muapi rendereli (halandzsa lenne), hanem PIL-lel overlay-eljük rá utólag.
Ezért az alapkép-prompt szigorúan szöveg-MENTES:
- NO TEXT, NO LETTERS, NO WORDS, NO NUMBERS, NO TYPOGRAPHY, NO UI labels, NO logók a képen.
- TRUE near-black #04060a háttér (nem szürke), egyetlen atmoszférikus fókuszelem.
- A BAL-ALSÓ és JOBB-FELSŐ sáv maradjon üres/tiszta a rárakott szövegnek.
- Voice cinematic irány az eval-győztes szerint (lásd fent). Soul Cinema: volumetrikus fény, 35mm grain, magas kontraszt.

## Phase 12.6 — fotografikus text-free alapkép (produkciós default)
A háttér FOTÓ, nem illusztráció (a "generic AI-art / pseudo-code panel" panaszok ellen):
- EGY objektum: out-of-focus mechanikus billentyűzet drámai oldalfényben, VAGY egy ívelő
  világító kábel a sötétben, VAGY makró egy NYÁK-él sekély fókusszal. Csak egy. Műhely-éjszaka hangulat.
- Kodak Portra 800 film grain, egy key-light bal-felülről, mély feketébe esés, f/1.4 bokeh.
- TRUE #04060a (nem szürke). Tilos: screen/UI/HUD/kód/panel/hologram/chart/logó/ember.
- Forrás: `src/visuals/visual_generator.py` → `build_textfree_prompt` + `VOICE_SCENE`.
