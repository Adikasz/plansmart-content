# Brand assets — feltöltési útmutató

Ide kerülnek a PlanSmart logó fájlok. A visual generator ezeket watermarkként
és (később) image-to-image referenciaként használja.

## Mit tölts fel

| Fájl | Mit | Formátum | Ajánlott méret |
|------|-----|----------|----------------|
| `logo-wordmark.webp` | A teljes "PlanSmart" szöveges logó | WebP, átlátszó háttér | ~1200×300 px |
| `logo-mark.webp` | Csak az ikon/jel (szöveg nélkül) | WebP, átlátszó háttér | ~512×512 px |

## Fontos

- **Átlátszó háttér** (alpha csatorna) — a sötét `#04060a` háttéren így tisztán ül.
- A wordmark a **Bricolage Grotesque** fontot tükrözze (lásd `docs/BRAND.md`).
- Ne legyen körülötte fehér keret/box.

## Amíg nincs feltöltve

A visual generator a promptban `[BRAND_LOGO]` placeholder szöveget használ.
Amint a valódi fájlok megérkeznek, a Muapi `/api/v1/upload_file` végponton
feltöltjük őket, és image-to-image modellel (pl. `nano-banana-2`) tesszük rá a
generált képekre. Lásd `src/visuals/muapi_client.py`.
