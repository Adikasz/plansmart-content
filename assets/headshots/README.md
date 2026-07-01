# Headshots — feltöltési útmutató

Ide kerülnek a személyes fotók a Dávid és Ádám hangokhoz (a brand hangnak nincs
headshotja). Ezeket később **character-consistency** generáláshoz használjuk:
a `nano-banana-2` modellel image-to-image, hogy a generált vizuálokon
felismerhető legyen az arc.

| Fájl | Kihez | Formátum | Ajánlott |
|------|-------|----------|----------|
| `david.jpg` | Dávid (builder voice) | JPG, jó minőség | szemből, semleges háttér, ~1024×1024 |
| `adam.jpg` | Ádám (strategist voice) | JPG, jó minőség | szemből, semleges háttér, ~1024×1024 |

## Amíg nincs feltöltve

A generálás fotó nélkül, absztrakt/koncepcionális vizuálokkal megy (flux-2-pro).
Amint megvannak a fotók: Muapi `/api/v1/upload_file` → `nano-banana-2` image-to-image.
