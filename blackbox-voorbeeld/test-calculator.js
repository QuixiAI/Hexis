// Dit bestand test onze calculator
const calculator = require('./calculator.js');

console.log("\n=== MIJN EIGEN TESTS ===");

// Test 1: Grote getallen
console.log("100 + 250 =", calculator(100, 250, '+'));

// Test 2: Negatieve getallen
console.log("-10 + 5 =", calculator(-10, 5, '+'));

// Test 3: Kommagetallen
console.log("7.5 * 2 =", calculator(7.5, 2, '*'));

// Test 4: Wat gebeurt er met een verkeerde operatie?
console.log("10 % 3 =", calculator(10, 3, '%'));
