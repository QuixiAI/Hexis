#!/bin/bash

# Dit script maakt het gemakkelijk om Blackbox te gebruiken
# Uitleg: Het zorgt ervoor dat de API key altijd beschikbaar is

# Stel de API key in
export BLACKBOX_API_KEY="bb_40f515a66c5a7c9408526509c53bd4893957357ad27ed94f2a2e12452a7fa61c"

echo "🚀 Blackbox AI Helper Script"
echo "=============================="
echo ""

# Check of er een argument is meegegeven
if [ $# -eq 0 ]; then
    echo "📖 Gebruik:"
    echo ""
    echo "  ./gebruik-blackbox.sh interactief"
    echo "    → Start Blackbox in interactieve modus"
    echo ""
    echo "  ./gebruik-blackbox.sh vraag \"jouw vraag hier\""
    echo "    → Stel een directe vraag aan Blackbox"
    echo ""
    echo "  ./gebruik-blackbox.sh test"
    echo "    → Test de calculator voorbeelden"
    echo ""
    echo "Voorbeelden:"
    echo "  ./gebruik-blackbox.sh vraag \"Maak een functie die twee strings samenvoegt\""
    echo "  ./gebruik-blackbox.sh vraag \"Leg uit wat recursie is met een voorbeeld\""
    exit 0
fi

# Verwerk de verschillende opties
case "$1" in
    interactief)
        echo "✨ Start Blackbox in interactieve modus..."
        echo "   (Type 'exit' om te stoppen)"
        echo ""
        blackbox
        ;;

    vraag)
        if [ -z "$2" ]; then
            echo "❌ Fout: Geef een vraag mee!"
            echo "   Voorbeeld: ./gebruik-blackbox.sh vraag \"Hoe maak ik een array?\""
            exit 1
        fi
        echo "🤔 Vraag aan Blackbox: $2"
        echo ""
        blackbox -p "$2"
        ;;

    test)
        echo "🧪 Test de calculator voorbeelden..."
        echo ""
        echo "1️⃣  Basis voorbeelden:"
        node calculator.js
        echo ""
        echo "2️⃣  Extra tests:"
        node test-calculator.js
        ;;

    *)
        echo "❌ Onbekende optie: $1"
        echo "   Voer './gebruik-blackbox.sh' uit zonder argumenten voor hulp"
        exit 1
        ;;
esac
