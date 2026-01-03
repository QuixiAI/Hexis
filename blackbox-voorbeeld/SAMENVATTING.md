# 🎯 SNELLE SAMENVATTING - Blackbox AI

## Wat je NU kunt doen:

### 1️⃣ Blackbox Opstarten (Interactief)
```bash
export BLACKBOX_API_KEY="bb_40f515a66c5a7c9408526509c53bd4893957357ad27ed94f2a2e12452a7fa61c"
blackbox
```
Dan kun je vragen stellen en krijg je directe antwoorden!

### 2️⃣ Eén Vraag Stellen (Non-interactief)
```bash
export BLACKBOX_API_KEY="bb_40f515a66c5a7c9408526509c53bd4893957357ad27ed94f2a2e12452a7fa61c"
blackbox -p "Maak een simpele functie die..."
```

### 3️⃣ Wat de MCP Server Doet
De MCP (Model Context Protocol) server die we hebben toegevoegd geeft Blackbox toegang tot:
- Remote code execution
- Extra tools en functionaliteit
- Uitgebreide AI capabilities

## 🎓 Wat je Hebt Geleerd:

### Het Calculator Voorbeeld toont:
✅ **Functies maken** - herbruikbare code
✅ **Parameters gebruiken** - input doorgeven
✅ **Return values** - output teruggeven
✅ **Switch statements** - keuzes maken
✅ **Error handling** - fouten afvangen
✅ **Modules** - code delen tussen bestanden

## 📁 Bestanden in deze Map:

1. **calculator.js** - De rekenmachine met uitleg
2. **test-calculator.js** - Tests om te leren hoe het werkt
3. **README.md** - Uitgebreide handleiding
4. **SAMENVATTING.md** - Dit bestand (quick reference)

## 🚀 Hoe Werkt het Calculator Voorbeeld?

```javascript
calculator(10, 5, '+')  // Geeft 15
```

**Stap voor stap:**
1. Je roept de functie aan met 2 getallen en een operatie
2. De switch statement kijkt welke operatie je wilt
3. De juiste berekening wordt uitgevoerd
4. Het resultaat wordt teruggegeven

**Speciale gevallen:**
- Delen door 0 → Geeft een foutmelding
- Verkeerde operatie → Geeft een foutmelding

## 💡 Probeer Dit Zelf:

### Eenvoudig:
```bash
node calculator.js        # Voer de voorbeelden uit
node test-calculator.js   # Voer de tests uit
```

### Geavanceerder met Blackbox:
```bash
# Vraag uitleg over een stuk code:
blackbox -p "Leg uit wat deze switch statement doet in calculator.js"

# Laat nieuwe code genereren:
blackbox -p "Maak een functie die de vierkantswortel berekent"

# Vraag om code verbetering:
blackbox -p "Hoe kan ik calculator.js uitbreiden met modulo operator?"
```

## 🎯 De 3 Belangrijkste Dingen:

1. **Blackbox is een AI programmeer-assistent**
   - Helpt met code schrijven
   - Legt code uit
   - Debugt problemen

2. **Gebruik `-p` voor directe vragen**
   - Snel antwoord krijgen
   - Geen interactieve sessie nodig

3. **MCP server geeft extra kracht**
   - Meer tools beschikbaar
   - Remote code execution
   - Geavanceerde features

## 📞 Hulp Nodig?

```bash
blackbox --help          # Alle opties zien
blackbox mcp list        # MCP servers checken
node calculator.js       # Voorbeeld uitvoeren
```

---

**Pro Tip:** Stel ALTIJD de API key in voordat je Blackbox gebruikt:
```bash
export BLACKBOX_API_KEY="bb_40f515a66c5a7c9408526509c53bd4893957357ad27ed94f2a2e12452a7fa61c"
```

Of voeg het toe aan je `~/.bashrc` of `~/.zshrc` zodat je het niet elke keer hoeft te doen!
