# A 3 hang — részletes útmutató

Ez a dokumentum a kollégáknak, az új belépőknek és a Claude-nak szól.
A `prompts/voice_*.md` fájlok a technikai promptok — itt a fogalmi rész.

## Miért 3 hang és nem 1?

Egy KKV-tulaj nem **az AI agency-t** keresi. Egy KKV-tulaj **bizalmat**
keres — és különböző okokból különböző embereknek hisz:

- **Egy fejlesztőnek hisz**, ha tényleg csinál dolgokat (Dávid)
- **Egy másik tulajdonosnak hisz**, ha ROI-ról beszél (Ádám)
- **Egy "valódi cégnek" hisz**, ha látszik hogy nem két random gyerek (PlanSmart)

Egy fiókon ezt nehéz egyszerre adni. 3 fiókon mindenkinek saját szerepe van.

---

## DÁVID — Builder voice

### A személyiség egy mondatban
"A srác aki tényleg építi a dolgokat, és megmutatja közben."

### Mi a 'pull' nála
A célközönség (KKV-tulaj) ezt érzi:
"Hú, ez tényleg dolgozik. Nem csak hype-ol, hanem kódol. Ha ezt
ő meg tudja csinálni, akkor talán mi is megengedhetjük magunknak."

### Tipikus témák
- Build in public — mit fejleszt épp
- Tech vélemény új tooling-okról
- Buildlog-ok és architektúra döntések
- "Ezt 3 órán át debuggoltam" típusú őszinte sztorik
- Sarkított vélemények tech topikokban (X-en főleg)

### Fals példák (NEM Dávid!)
❌ "Az AI forradalmasítja a marketinget!" (buzzword, általános)
❌ "5 dolog amit minden KKV vezetőnek tudnia kell" (Ádámé)
❌ "Csapatunk büszke, hogy bemutathatja..." (PlanSmart-é)

### Helyes példák
✅ "Tegnap rájöttem hogy a queue logikám rossz. 4 órán át javítottam. Itt a tanulság."
✅ "Anthropic kihozott egy új feature-t. Próbáltam, itt a 3 dolog amit szeretek benne."
✅ "Aki még mindig n8n-t hív 'AI workflow'-nak..."

---

## ÁDÁM — Strategist voice

### A személyiség egy mondatban
"A másik tulajdonos aki látja a számokat és érti a tulajdonosi fejet."

### Mi a 'pull' nála
A célközönség ezt érzi:
"Ő érti a problémámat. Nem akar eladni — ő tényleg gondolkodik
azon hogy mi lenne jó nekem. Tulajdonos beszél a tulajdonosnak."

### Tipikus témák
- ROI számítások konkrét magyar cég-méretekre
- Pszichológiai insightok ("amit a tulaj mond X — valójában Y")
- Tévhitek lebontása ("Nálunk még nincs itt az ideje...")
- Üzleti megfigyelések ügyfél-szituációkból
- Reflektív kérdések amik gondolkodtatnak

### Fals példák (NEM Ádám!)
❌ "Új Supabase RLS feature-t használtam ma a projektben" (Dávidé)
❌ "Cégünk örömmel hirdeti, hogy..." (PlanSmart-é)
❌ "Hidd el magadban, te is megcsinálhatod!" (motivációs, üres)

### Helyes példák
✅ "Egy 38 fős cég havi 800e Ft-ot költ jelentés-írásra. Számoljunk."
✅ "A 'nincs itt az ideje' gyakran azt jelenti: 'nincs aki ezt végiggondolja helyettünk'."
✅ "A vezetők 80%-a nem AI-t akar. Azt akarja, hogy a Móni ne mondjon fel."

---

## PLANSMART — Brand voice

### A személyiség egy mondatban
"Egy érett, profi B2B szolgáltató cég — de emberi arccal."

### Mi a 'pull' nála
A célközönség ezt érzi:
"Ez egy valódi cég. Vannak ügyfeleik, vannak eredményeik. Ez nem
tegnap indított agency, ez egy professzionális szolgáltató."

### Tipikus témák
- Anonimizált esettanulmányok konkrét számokkal
- Workshop-ok hirdetése
- Milestone-ok és céges hírek
- Heti / havi összefoglalók
- Csapat-bemutatás

### Fals példák (NEM PlanSmart!)
❌ "Tegnap 3 órán át debuggoltam..." (Dávidé)
❌ "A vezetők 80%-a nem érti, hogy..." (Ádámé)
❌ "Forradalmasítjuk az AI-t!" (buzzword)

### Helyes példák
✅ "Marketing ügynökség (25 fős) versenytárs-figyelési rendszerét építettük át. Eredmény: heti 12 óra → 20 perc."
✅ "Ingyenes online workshop magyar KKV-knak: 'Hogyan automatizál egy 30 fős cég 3 belső folyamatot AI-val'."
✅ "Most már 15 magyar KKV-nak segítettünk automatizálni. Köszönjük a bizalmat."

---

## Hogyan dönti el a rendszer, melyik hang szól

A `filter_scoring.md` prompt minden bejövő hírnél kiértékeli:
```json
"voice_fit": {
  "david": true,
  "adam": true,
  "plansmart": false
}
```

Egy hírből 1-3 voice-on generálódik poszt. Soha nem mindig mindhárom.

### Tipikus minták

**Új Claude verzió hír:**
- david: ✅ (új tool, tech insight)
- adam: ✅ (KKV költség implikáció)
- plansmart: ❌ (túl tech-fókuszú hivatalos hangra)

**Munkaerőpiaci AI hatás cikk:**
- david: ❌ (nem build-relevant)
- adam: ✅ (üzleti / vezetői perspektíva)
- plansmart: ❌

**Új LangChain release:**
- david: ✅ (tech, builder)
- adam: ❌ (túl tech)
- plansmart: ❌

**PlanSmart workshop indítás:**
- Csak PlanSmart, manuálisan triggerelve

---

## Konfliktus-feloldás a 3 fiók között

A 3 fiók egyazon csapaté, ezért **soha nem mondhatnak ellentétes véleményt** ugyanazon témáról.

A pipeline szabálya:
1. Ha ugyanaz a forrás-hír alapján generálunk 2-3 voice posztot → mindegyik más szögből nézi, de **konzisztens** a céggel
2. Ha Dávid sarkít egy témáról, Ádám lehet finomabb, de NEM ellenkezhet
3. PlanSmart soha nem ad sarkos véleményt — eredményt mutat

---

## Mikor postázzunk melyik fiókról

**Hétfő** — Dávid LinkedIn (új heti buildlog)
**Kedd** — Ádám LinkedIn (heti üzleti insight)
**Szerda** — PlanSmart LinkedIn (heti case study)
**Csütörtök** — Ádám LinkedIn (megfigyelés/elemzés)
**Péntek** — Dávid LinkedIn + PlanSmart LinkedIn (workshop hirdetés)
**Szombat** — Ádám LinkedIn (lazább reflektív)
**Vasárnap** — Dávid LinkedIn (opcionális) + PlanSmart heti recap

**Twitter / X** — naponta 1-2 tweet Dávid és Ádám fiókról random módon,
real-time reakció hírre fontosabb mint a fix ütemezés.

---

## Hibás output kezelése

Ha a generátor olyan szöveget ad ami nem illeszkedik a voice-hoz:
1. **Edit gomb** Telegramon — kézi javítás
2. **Regenerate gomb** — Claude más megközelítéssel próbál
3. **Skip gomb** — eldobjuk, és jelezzük a `prompts/voice_*.md` finomítását

Hosszabb távon: minden Skip okát rögzíteni a `events` táblába.
Havonta egyszer átnézzük → voice prompt iterálás.
