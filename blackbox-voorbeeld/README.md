# 🎓 Blackbox AI - Beginners Handleiding

## Wat heb je nu?

Je hebt nu toegang tot **Blackbox AI** via de command line. Dit is een AI-assistent die je helpt met programmeren.

## 📦 Wat zit er in deze map?

### 1. `calculator.js`
Een simpele rekenmachine die ik heb gemaakt als voorbeeld. Deze laat zien:
- **Functies** - herbruikbare stukjes code
- **Switch statements** - keuzes maken in code
- **Error handling** - omgaan met fouten (zoals delen door 0)
- **Comments** - uitleg bij de code

### 2. `test-calculator.js`
Een test bestand dat laat zien hoe je de calculator kunt gebruiken met verschillende soorten getallen.

## 🚀 Wat kun je doen met Blackbox?

### Optie 1: Interactieve Modus
```bash
export BLACKBOX_API_KEY="bb_40f515a66c5a7c9408526509c53bd4893957357ad27ed94f2a2e12452a7fa61c"
blackbox
```
Dit opent een chat waar je vragen kunt stellen zoals:
- "Leg uit wat deze code doet"
- "Maak een functie die..."
- "Hoe kan ik dit beter maken?"

### Optie 2: Directe Commando's
```bash
export BLACKBOX_API_KEY="bb_40f515a66c5a7c9408526509c53bd4893957357ad27ed94f2a2e12452a7fa61c"
blackbox -p "jouw vraag hier"
```

## 📚 Uitleg van het Calculator Voorbeeld

### Wat doet de code?

1. **Functie definitie**
   ```javascript
   function calculator(getal1, getal2, operatie)
   ```
   - `getal1` en `getal2` zijn de getallen waarmee we rekenen
   - `operatie` is wat we willen doen (+, -, *, /)

2. **Switch statement**
   ```javascript
   switch(operatie) {
       case '+':
           return getal1 + getal2;
   ```
   - Dit kijkt naar de operatie en kiest de juiste actie

3. **Error handling**
   ```javascript
   if (getal2 === 0) {
       return "Fout: je kunt niet delen door 0!";
   }
   ```
   - Dit voorkomt problemen wanneer je door 0 probeert te delen

## 🎯 Probeer Zelf!

### Opdracht 1: Verander de Calculator
Voeg een nieuwe operatie toe voor het berekenen van de macht (bijvoorbeeld 2^3 = 8):
```javascript
case '^':
    return Math.pow(getal1, getal2);
```

### Opdracht 2: Maak iets nieuws met Blackbox
Vraag Blackbox om:
```bash
blackbox -p "Maak een functie die controleert of een getal een priemgetal is"
```

### Opdracht 3: Laat Blackbox code uitleggen
```bash
blackbox -p "Leg deze code regel voor regel uit: [kopieer hier code]"
```

## 🔑 Belangrijke Concepten die je Hebt Geleerd

1. **Functies** - herbruikbare code blokken
2. **Parameters** - input voor functies
3. **Return values** - output van functies
4. **Switch statements** - keuzes maken
5. **Error handling** - omgaan met problemen
6. **Module exports** - code delen tussen bestanden

## 💡 Tips

- Begin klein en bouw stap voor stap
- Test je code regelmatig
- Gebruik comments om uit te leggen wat je doet
- Vraag Blackbox om hulp als je vast zit

## 🎓 Volgende Stappen

Nu je de basis begrijpt, kun je:
1. Complexere functies maken
2. Werken met arrays en objects
3. API's aanroepen
4. Webapplicaties bouwen

**Veel succes met leren!** 🚀
