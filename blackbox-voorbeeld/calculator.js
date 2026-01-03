// Een simpele rekenmachine functie
// Deze functie kan optellen, aftrekken, vermenigvuldigen en delen

function calculator(getal1, getal2, operatie) {
    // We controleren welke operatie we moeten uitvoeren
    switch(operatie) {
        case '+':
            // Optellen
            return getal1 + getal2;
        case '-':
            // Aftrekken
            return getal1 - getal2;
        case '*':
            // Vermenigvuldigen
            return getal1 * getal2;
        case '/':
            // Delen - maar pas op voor delen door 0!
            if (getal2 === 0) {
                return "Fout: je kunt niet delen door 0!";
            }
            return getal1 / getal2;
        default:
            // Als de operatie niet klopt
            return "Fout: gebruik +, -, * of /";
    }
}

// Voorbeelden van gebruik:
console.log("=== REKENMACHINE VOORBEELDEN ===");
console.log("10 + 5 =", calculator(10, 5, '+'));
console.log("10 - 5 =", calculator(10, 5, '-'));
console.log("10 * 5 =", calculator(10, 5, '*'));
console.log("10 / 5 =", calculator(10, 5, '/'));
console.log("10 / 0 =", calculator(10, 0, '/'));

// Exporteer de functie zodat andere bestanden hem kunnen gebruiken
module.exports = calculator;
