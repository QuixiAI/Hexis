# 🎯 START HIER - Blackbox AI voor Beginners

**Welkom!** Dit is jouw startpunt om Blackbox AI te leren gebruiken.

---

## 📚 Wat vind je in deze map?

```
blackbox-voorbeeld/
│
├── 📖 START-HIER.md              ← JIJ BENT HIER! Begin hier.
├── 📋 SAMENVATTING.md            ← Snelle referentie en commando's
├── 📘 README.md                  ← Uitgebreide uitleg en leerdoelen
│
├── 🧮 calculator.js              ← Voorbeeld: werkende rekenmachine
├── 🧪 test-calculator.js         ← Tests voor de rekenmachine
└── 🚀 gebruik-blackbox.sh        ← Handig script om Blackbox te gebruiken
```

---

## ⚡ SNELSTART (3 stappen)

### Stap 1: Test het Voorbeeld
```bash
cd /vercel/sandbox/blackbox-voorbeeld
./gebruik-blackbox.sh test
```
✅ Dit laat zien hoe de rekenmachine werkt!

### Stap 2: Bekijk de Code
```bash
cat calculator.js
```
✅ Lees de code met uitleg - elke regel is gedocumenteerd!

### Stap 3: Probeer Blackbox
```bash
./gebruik-blackbox.sh vraag "Maak een functie die controleert of een woord een palindroom is"
```
✅ Blackbox genereert code voor je!

---

## 🎓 Wat Heb Je Geleerd?

Door dit voorbeeld te bestuderen begrijp je:

1. **Functies** - Herbruikbare code blokken
   ```javascript
   function calculator(getal1, getal2, operatie) {
       // code hier
   }
   ```

2. **Parameters** - Input voor functies
   ```javascript
   calculator(10, 5, '+')  // 10 en 5 zijn parameters
   ```

3. **Return Values** - Output van functies
   ```javascript
   return getal1 + getal2;  // Dit geeft het resultaat terug
   ```

4. **Switch Statements** - Keuzes maken
   ```javascript
   switch(operatie) {
       case '+': return getal1 + getal2;
       case '-': return getal1 - getal2;
   }
   ```

5. **Error Handling** - Fouten afvangen
   ```javascript
   if (getal2 === 0) {
       return "Fout: je kunt niet delen door 0!";
   }
   ```

---

## 🔧 Hoe Gebruik je Blackbox?

### Optie A: Met het Helper Script (Makkelijkst!)

```bash
# Help tonen
./gebruik-blackbox.sh

# Interactieve modus
./gebruik-blackbox.sh interactief

# Directe vraag
./gebruik-blackbox.sh vraag "jouw vraag hier"

# Tests uitvoeren
./gebruik-blackbox.sh test
```

### Optie B: Direct (Voor Gevorderden)

```bash
# Eerst API key instellen
export BLACKBOX_API_KEY="bb_40f515a66c5a7c9408526509c53bd4893957357ad27ed94f2a2e12452a7fa61c"

# Dan Blackbox gebruiken
blackbox -p "jouw vraag"
```

---

## 💡 Voorbeeld Vragen voor Blackbox

Probeer deze vragen te stellen aan Blackbox:

### 🟢 Beginners Vragen:
```bash
./gebruik-blackbox.sh vraag "Maak een functie die twee getallen vergelijkt"
./gebruik-blackbox.sh vraag "Hoe maak ik een array in JavaScript?"
./gebruik-blackbox.sh vraag "Wat is het verschil tussen let en const?"
```

### 🟡 Gemiddeld:
```bash
./gebruik-blackbox.sh vraag "Maak een functie die een array sorteert"
./gebruik-blackbox.sh vraag "Hoe lees ik een bestand in Node.js?"
./gebruik-blackbox.sh vraag "Maak een eenvoudige HTTP server"
```

### 🔴 Gevorderd:
```bash
./gebruik-blackbox.sh vraag "Maak een async functie die data van een API haalt"
./gebruik-blackbox.sh vraag "Implementeer een binary search algoritme"
./gebruik-blackbox.sh vraag "Maak een Express REST API met error handling"
```

---

## 📖 Lees Verder

1. **Voor snelle referentie** → Lees `SAMENVATTING.md`
2. **Voor uitgebreide uitleg** → Lees `README.md`
3. **Voor code voorbeelden** → Bekijk `calculator.js` en `test-calculator.js`

---

## 🎯 Je Eerste Opdracht

Klaar om te beginnen? Probeer dit:

1. Voer de tests uit:
   ```bash
   ./gebruik-blackbox.sh test
   ```

2. Bekijk de calculator code:
   ```bash
   cat calculator.js
   ```

3. Vraag Blackbox om een nieuwe functie te maken:
   ```bash
   ./gebruik-blackbox.sh vraag "Maak een functie die controleert of een getal even of oneven is"
   ```

4. Test de nieuwe functie die Blackbox heeft gemaakt!

---

## ❓ Veelgestelde Vragen

**Q: Moet ik de API key elke keer instellen?**
A: Als je het helper script gebruikt (`gebruik-blackbox.sh`), niet! Het doet het automatisch.

**Q: Wat als ik een foutmelding krijg?**
A: Check of je in de juiste directory bent (`/vercel/sandbox/blackbox-voorbeeld`)

**Q: Kan Blackbox elke programmeertaal?**
A: Ja! JavaScript, Python, Go, Rust, Java, en veel meer.

**Q: Hoe stop ik de interactieve modus?**
A: Type `exit` of druk op `Ctrl+D`

---

## 🚀 Volgende Stappen

Nu je de basis kent:

1. ✅ Experimenteer met verschillende operaties in calculator.js
2. ✅ Vraag Blackbox om uitleg over code die je niet begrijpt
3. ✅ Laat Blackbox nieuwe functies voor je maken
4. ✅ Probeer de code aan te passen en te verbeteren
5. ✅ Maak je eigen project met hulp van Blackbox!

---

## 🎉 Succes!

Je hebt nu alles wat je nodig hebt om met Blackbox AI te beginnen.

**Onthoud:**
- Start klein en bouw stap voor stap
- Test je code regelmatig
- Vraag uitleg als je iets niet begrijpt
- Experimenteer en leer van fouten!

**Veel plezier met programmeren!** 💻✨

---

*💡 Pro Tip: Bookmark dit bestand - het is je snelle toegang tot alles wat je nodig hebt!*
