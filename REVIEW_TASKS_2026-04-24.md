# Kódbázis review – javasolt feladatok (2026-04-24)

## 1) Elírás javítása

**Feladat:** Javítsuk a README-ben az „arvezerelet” elírást „árvezérelt”-re (vagy az ékezetmentes konvenció szerint „arvezerelt”-re).

**Miért fontos:** A README elsődleges onboarding anyag, az elírás csökkenti a professzionális benyomást és kereshetőséget.

**Elfogadási kritérium:**
- A README szövegében az elírt szó javítva van.
- Ha ékezetmentes stílust követ a projekt, az egész mondat stílusban konzisztens marad.

---

## 2) Hiba javítása

**Feladat:** A trade filter minimum méretre clampelésének jelölése (`size_clamped_to_min`) legyen garantáltan jelen a rögzített filter payloadban, amikor clamp ténylegesen történik.

**Miért fontos:** Két integrációs teszt jelenleg `KeyError`-ral bukik, mert a kulcs hiányzik a JSON payloadból, noha a teszt a clampelt viselkedést várja.

**Technikai irány:**
- Vizsgáljuk felül a `selected_filter_values` merge / felülírás sorrendjét a clamp logika környezetében.
- A kulcsot érdemes defaulttal (`False`) inicializálni, és clamp esetén `True`-ra állítani.
- Ellenőrizzük, hogy a runtime-ba visszaírt `last_filter_values` ezt a mezőt valóban tartalmazza.

**Elfogadási kritérium:**
- A két anti-overtrading integrációs teszt stabilan zöld.
- A `last_filter_values` JSON-ban clamp esetén `size_clamped_to_min: true` szerepel.

---

## 3) Kódkommentár / dokumentációs ellentmondás javítása

**Feladat:** Egységesítsük az ULTRA profile standby-logikáját leíró tesztnevet/kommentárt és a tényleges implementációt.

**Miért fontos:** A teszt neve szerint „extreme chaos only” esetben standby várható, de a jelenlegi implementáció `defensive_mm` módot ad vissza ugyanarra a szcenárióra. Ez vagy regresszió, vagy elavult specifikáció.

**Technikai irány:**
- Döntsük el az üzleti elvárt működést (standby vs defensive_mm).
- Ehhez igazítsuk **vagy** a `select_mode` implementációt, **vagy** a teszt nevét/assertjait és a hozzá kapcsolódó dokumentációt.
- Röviden dokumentáljuk a döntést (`ARCHITECTURE.md` vagy dedikált decision log).

**Elfogadási kritérium:**
- A standby-viselkedésről szóló teszt és a kód ugyanazt az elvárt működést reprezentálja.
- Az adaptív módválasztás dokumentációja nem mond ellent a tesztnek.

---

## 4) Teszt javítása

**Feladat:** Erősítsük meg az anti-overtrading teszteket úgy, hogy ne csak egy opcionális kulcs jelenlétére támaszkodjanak, hanem a viselkedést több invariánssal validálják.

**Miért fontos:** A jelenlegi assert túl sérülékeny payload-struktúra változásra; emiatt könnyen lehet álnegatív (false negative) hiba.

**Technikai irány:**
- A `size_clamped_to_min` kulcs mellett ellenőrizzük a trade méretet és a trade count változását.
- Használjunk `.get("size_clamped_to_min", False)` mintát, ha a cél viselkedés-alapú ellenőrzés.
- Ahol szükséges, külön tesztben validáljuk a payload schema-t.

**Elfogadási kritérium:**
- A tesztek egyértelműen különválasztják a „viselkedés” és a „schema” ellenőrzést.
- A tesztek kevésbé törékenyek belső payload-átalakításokra.
