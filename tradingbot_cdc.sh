#!/bin/bash
# ====================================================
#  tradingbot_cdc.sh — Lancement sécurisé du bot
#  Usage :   ./tradingbot_cdc.sh
# ====================================================

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR" || exit 1

echo "============================================"
echo "  🚀 TRADINGBOT CDC"
echo "  Répertoire : $PROJECT_DIR"
echo "============================================"

echo "[1/4] Nettoyage du cache Python..."
find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null
rm -f test_equity.png test_signals.png

echo "[2/4] Arrêt des anciennes instances du bot..."
ps aux | grep run_paper.py | grep -v grep | awk '{print $2}' | xargs kill 2>/dev/null
sleep 1

echo "[3/4] Vérification de la configuration..."
if [ ! -f config.yaml ]; then
    if [ -f config.example.yaml ]; then
        cp config.example.yaml config.yaml
        echo "    config.example.yaml -> config.yaml (copie)"
    else
        echo "    ❌ Aucun fichier config.yaml trouvé !"
        echo "    Répertoire actuel : $(pwd)"
        exit 1
    fi
else
    echo "    ✔ config.yaml trouvé"
fi

echo "[4/4] Démarrage du bot..."
echo ""
echo "============================================"
echo "  Bot démarré - Presse Ctrl+C pour arrêter"
echo "============================================"
echo "  Universe : 8 actifs (BTC ETH SOL DOGE MATIC XRP ADA DOT)"
echo "  Capital  : \$100  |  Max/trade : \$85  |  Concurrence : 1"
echo "  TP 2% / SL 1%  |  Night bonus : +30% (20h-4h UTC)"
echo "============================================"
echo ""
python run_paper.py

echo ""
read -p "Appuyez sur Entrée pour fermer..."