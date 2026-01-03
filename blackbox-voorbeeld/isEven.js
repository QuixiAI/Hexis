// Functie die controleert of een getal even of oneven is
// Gemaakt als demonstratie van Blackbox AI

function isEven(getal) {
    // Controleer eerst of het wel een getal is
    if (typeof getal !== 'number' || isNaN(getal)) {
        return "Fout: dit is geen geldig getal!";
    }

    // Gebruik de modulo operator (%)
    // Als een getal gedeeld door 2 rest 0 heeft, is het even
    if (getal % 2 === 0) {
        return `${getal} is EVEN`;
    } else {
        return `${getal} is ONEVEN`;
    }
}

// ═══════════════════════════════════════════
// VOORBEELDEN - Laten we het testen!
// ═══════════════════════════════════════════

console.log("=== EVEN OF ONEVEN CHECKER ===\n");

// Test met positieve getallen
console.log("📊 Positieve getallen:");
console.log(isEven(4));    // 4 is EVEN
console.log(isEven(7));    // 7 is ONEVEN
console.log(isEven(100));  // 100 is EVEN
console.log(isEven(99));   // 99 is ONEVEN

console.log("\n📊 Negatieve getallen:");
console.log(isEven(-2));   // -2 is EVEN
console.log(isEven(-5));   // -5 is ONEVEN

console.log("\n📊 Speciale gevallen:");
console.log(isEven(0));    // 0 is EVEN (0 / 2 = 0, geen rest)
console.log(isEven(1));    // 1 is ONEVEN

console.log("\n📊 Kommagetallen:");
console.log(isEven(4.5));  // 4.5 is ONEVEN (rest na delen door 2)
console.log(isEven(6.0));  // 6.0 is EVEN

console.log("\n❌ Foute input:");
console.log(isEven("text"));     // Fout
console.log(isEven(undefined));  // Fout

// Exporteer de functie
module.exports = isEven;
