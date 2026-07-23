# PlanSmart — Brand & Visual System

> Ez a dokumentum a vizuális generálás (Phase 7.6) egyetlen igazságforrása.
> A `src/integrations/visuals/` modulok és a `prompts/visual_styles/` fájlok erre hivatkoznak.

Forrás: <https://plansmart.live> (Astro-alapú oldal) — a fontokat
`scripts/scrape_brand_font.py` olvasta ki és töltötte le.

---

## 1. Brand alaphangzat

- **Brand voice (HU):** „Az autonóm cég operációs rendszere"
- **Pozicionálás:** üzlettulajdonosoknak szóló bizalom-építő, konkrét, nem buzzword-ös.
- **Konkrét metrika stílus:** `16′ → 3,5″`, `0 új alkalmazott` — mindig **számszerű,
  mérhető, előtte/utána**. A vizuálon a szám a fókuszpont, nem a szöveg-blokk.

---

## 2. Theme color

| Token | Hex | Használat |
|-------|-----|-----------|
| **Háttér (primary)** | `#04060a` | Minden vizuál sötét háttere — kötelező közös DNS |

Minden generált kép `#04060a` (közel-fekete, kissé kék árnyalat) háttérrel készül.
Ez a brand legazonosíthatóbb vizuális eleme a 3 hang között.

---

## 3. Font family (scraping eredmény)

A `plansmart.live` három fontot tölt be (mind self-hosted `@font-face`, woff2):

| Font | Szerep | Hol | Letöltve ide |
|------|--------|-----|--------------|
| **Inter Variable** | Body / törzsszöveg (elsődleges) | bekezdések, UI | `assets/fonts/inter-*.woff2` |
| **Bricolage Grotesque Variable** | Display / heading — a **wordmark** karaktere | címsorok, logó | `assets/fonts/bricolage-grotesque-*.woff2` |
| **Fragment Mono** | Monospace accent — számok, kód, metrikák | metrika-badge, kódrészlet | `assets/fonts/fragment-mono-*.woff2` |

Rangsor (előfordulás-súly a CSS-ben): `Inter Variable` (32) > `Bricolage Grotesque
Variable` (14) = `Fragment Mono` (14). Fallback stack a CSS-ben: `Inter` → `Segoe UI`
→ `system-ui` (body), `SFMono-Regular` / `Menlo` (mono).

**Vizuál-leképezés:**
- A **wordmark / címsor** vibe → *Bricolage Grotesque* (geometrikus grotesque, karakteres).
- A **metrikák és kódrészletek** → *Fragment Mono* (ez adja a builder/terminal érzést).
- A **törzsszöveg** → *Inter* (tiszta, semleges, megbízható).

> A képgeneráló modellek (flux-2-pro) nem renderelnek pontos fontot, ezért a promptban
> a *karaktert* írjuk le ("geometric grotesque display font", "monospace metric"),
> nem a font nevét. A valódi font a feltöltött logón (`[BRAND_LOGO]`) jelenik meg.

---

## 4. Voice → vizuál stílus leképezés

Mindhárom hang `#04060a` sötét hátteret használ, de a tartalom és az esztétika eltér.
A részletes, modellnek küldött leírás: `prompts/visual_styles/{voice}.md`.

### Dávid — BUILDER
- **Vibe:** code editor / terminal / architektúra-diagram. Monospace (*Fragment Mono*) accentek.
- **Tartalom:** kódrészlet, rendszer-diagram, „behind the scenes" build-tartalom.
- **Hangulat:** minimalista, technikai, bizalom-építő technikai tulajoknak.

### Ádám — STRATEGIST
- **Vibe:** üzleti chart / ROI vizualizáció. Nagy, középre helyezett számmetrika.
- **Tartalom:** előtte/utána összevetés, pénzügyi dashboard esztétika.
- **Hangulat:** tulaj-a-tulajnak nézőpont, nem technikai CEO-knak.

### PlanSmart — BRAND
- **Vibe:** prominens PlanSmart wordmark, tiszta, premium SaaS.
- **Tartalom:** esettanulmány-screenshot, mérföldkő-bejelentés, hivatalos tónus.
- **Hangulat:** corporate, letisztult, megbízható.

---

## 5. Közös vizuális DNS (mindhárom hangon kötelező)

1. **Háttér:** `#04060a` sötét.
2. **Brand font karakter:** geometric grotesque display + monospace metrika accent
   (Bricolage Grotesque + Fragment Mono leképezés).
3. **Logó watermark:** diszkrét PlanSmart logó a sarokban (`[BRAND_LOGO]` placeholder,
   amíg a valódi asset nincs feltöltve).
4. **Fókuszpont:** ha releváns, egy **konkrét szám / metrika** a kép vizuális központja
   (pl. `16′ → 3,5″`).

### Tiltások (soha)
- Nincs buzzword vizuálisan: ❌ „AI Revolution", ❌ „transform your business".
- Nincs stockfotó-klisé (kézfogás, felfelé mutató nyíl-grafika, generikus „tech-háló").
- Nincs világos háttér — a `#04060a` nem alku tárgya.

---

## 6. Asset státusz

| Asset | Állapot | Hol |
|-------|---------|-----|
| Fontok (Inter / Bricolage / Fragment Mono) | ✅ letöltve | `assets/fonts/` |
| `logo-wordmark.webp` | ⏳ user feltölti | `assets/brand/` |
| `logo-mark.webp` | ⏳ user feltölti | `assets/brand/` |
| `david.jpg` / `adam.jpg` headshot | ⏳ user feltölti később | `assets/headshots/` |

Amint a logó/headshot megérkezik: Muapi `/api/v1/upload_file` → image-to-image
(`nano-banana-2`) a karakter/logó konzisztenciához. Lásd `src/integrations/visuals/muapi_client.py`.

---

## 7. Modell-választás (Muapi.ai)

| Eset | Modell | Miért |
|------|--------|-------|
| Statikus koncepcionális vizuál (alapértelmezett) | `flux-2-pro` | jó minőség, gyors, legjobb A/B eredmény |
| Karakter/arc konzisztencia (headshot után) | `nano-banana-2` | image-to-image, identitás-megtartás |

> **Csak Muapi.ai-t használunk.** Higgsfield NEM.

### SOUL / base-model A/B (Phase 12.6 — 2026-06-30)

A **SOUL** (editorial/film-grain, hyper-realistic) a **Higgsfield** modellje
(higgsfield.ai/soul) — **NEM elérhető Muapin** (450 modell, egy sem `soul`/`higgsfield`).
A "Soul Cinema" kifejezés a promptjainkban csak esztétikai utalás, nem modell.

A legközelebbi editorial alternatívát, a **`midjourney-v8`-at** A/B-teszteltük a szöveg-mentes
alapkép-pipeline-hoz (ugyanaz a prompt + magyar PIL overlay + VisualEvaluator pontozás):

| Modell | Overall | HU szöveg | Költség/kép |
|--------|---------|-----------|-------------|
| **`flux-2-pro`** (alapértelmezett) | **7.6** | **8.67** | **$0.032** |
| `midjourney-v8` | 6.07 | 7.67 | $0.10 |

**Eredmény: Flux egyértelműen nyert (Δ = −1.53 az MJ kárára, ~3× költség).** Az MJ pont a
javítani kívánt hibát rontotta: zsúfoltabb, "matrix-kód-fal" hátteret adott, nem a #04060a
near-black tónust (lásd `assets/generated/cmp_mj_david.png`). **Marad a `flux-2-pro`.**
Teszt: `python -m scripts.test_soul_vs_flux`.

### Muapi API — megjegyzések (Phase 7.6 diagnosztika)

- **Auth:** `x-api-key: <MUAPI_API_KEY>` fejléc (a `Bearer` 403-at ad).
- **Endpoint:** `POST https://api.muapi.ai/api/v1/{model}` → `request_id`;
  poll: `GET .../api/v1/predictions/{request_id}/result` `status=completed`-ig.
  A kész képek a `data.images[]` / `data.outputs[]` tömbben.
- **Kötelező paraméter:** a `flux-2-pro` és `nano-banana-2` elvárja a `resolution`-t
  (`"1k"` / `"2k"`; nano-banana-2: `"4k"` is). Enélkül a generálás nem fut le rendesen.
- **Modell-lista:** `GET .../api/v1/models` (430 modell), egyenkénti séma:
  `GET .../api/v1/models/{name}`. Költségbecslés: `.../estimate-cost`.

> ⚠️ **NYITOTT BLOKKOLÓ (account-oldali):** jelenleg a Muapi a model **PÉLDA-képét**
> adja vissza valódi generálás helyett (`.../webassets/videomodels/{model}.jpg`),
> `$0` költséggel és üres usage-loggal — a `web_safety_forced: true` flag mellett
> (`/account/balance` szerint $20 egyenleg, email: adam@plansmart.live).
> A kód és a kérés helyes. Teendő a Muapi fiókban: fizetési mód/aktiválás, a
> kényszerített web-safety/sandbox kikapcsolása, vagy Muapi support. A
> `muapi_client` ezt felismeri (`/webassets/` marker) és `MuapiError`-t dob, hogy
> sose mentsünk el kamu kép-URL-t.
