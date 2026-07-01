Dark background #04060a. PlanSmart wordmark prominent. Case study screenshots, milestone
announcements, official tone. Clean, corporate, premium SaaS aesthetic.

Visual cues: prominent PlanSmart wordmark (geometric grotesque display character), clean
product/case-study card, milestone badge, generous negative space, premium SaaS landing
look. Refined neutral accents against the near-black #04060a background. Polished,
trustworthy, not flashy.

Always include subtle PlanSmart logo watermark in corner. Never use buzzwords visually
(no "AI Revolution", no "transform your business"). Concrete number or metric must be the
visual focal point when relevant.

## PlanSmart visual style (UPDATED 2026)
- Premium SaaS aesthetic, dark mode
- Bold display typography for headline
- PlanSmart wordmark visible but not dominant
- Case study screenshots can be incorporated
- Trustworthy, official, "company is real" feel
- Avoid: stock-photo vibes, generic business imagery

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
Vizuál A/B eval: **győztes = C (filmes prémium)** PlanSmartnál (legjobb átlag, 5.65).
- Volumetrikus fény, finom köd, film grain, movie-poster polish.
- Top fix: betűhű magyar szöveg, true #04060a háttér, nincs HUD/pseudo-code, egy fókuszelem, finom wordmark watermark.

## Phase 12.5 — SZÖVEG NÉLKÜLI alapkép (text overlay pipeline)
A magyar szöveget NEM a Flux/Muapi rendereli (halandzsa lenne), hanem PIL-lel overlay-eljük rá utólag.
Ezért az alapkép-prompt szigorúan szöveg-MENTES:
- NO TEXT, NO LETTERS, NO WORDS, NO NUMBERS, NO TYPOGRAPHY, NO UI labels, NO logók a képen.
- TRUE near-black #04060a háttér (nem szürke), egyetlen atmoszférikus fókuszelem.
- A BAL-ALSÓ és JOBB-FELSŐ sáv maradjon üres/tiszta a rárakott szövegnek.
- Voice cinematic irány az eval-győztes szerint (lásd fent). Soul Cinema: volumetrikus fény, 35mm grain, magas kontraszt.

## Phase 12.6 — fotografikus text-free alapkép (produkciós default)
A háromból a LEGMINIMÁLISABB — tiszta atmoszférikus sötétség:
- EGY lágy fényforrás: hajnali fény egy sötét szobában, VAGY rim-fényt fogó növénylevél,
  VAGY tisztán absztrakt gradiens film grain-nel, objektum nélkül. "Csendes magabiztosság."
- Kodak Portra 800 grain, egy key-light, mély feketébe esés, f/1.4 bokeh, semleges fény.
- TRUE #04060a (nem szürke). Tilos: screen/UI/HUD/kód/panel/hologram/chart/logó/ember.
- Forrás: `src/visuals/visual_generator.py` → `build_textfree_prompt` + `VOICE_SCENE`.
