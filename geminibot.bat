@echo off
REM =====================================================
REM  geminibot.bat  —  Lancement unique du bot paper-trading
REM  Usage :   geminibot           (depuis n'importe où)
REM =====================================================
setlocal enabledelayedexpansion

REM --- Répertoire racine du projet (là où se trouve ce script) ---
set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

echo ============================================
echo  🚀 LANCEMENT GEMINI BOT
echo  Répertoire : %PROJECT_DIR%
echo ============================================

REM --- 1. Forcer le nettoyage du cache Python ---
echo [1/4] Nettoyage du cache Python...
if exist __pycache__ rmdir /s /q __pycache__ 2>nul
for /d /r . %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d" 2>nul

REM --- 2. Tuer les anciennes instances Python qui tournent ---
echo [2/4] Arrêt des anciennes instances...
powershell -Command "Get-Process python* -ErrorAction SilentlyContinue | Stop-Process -Force"
REM Petit délai pour laisser le temps au port Telegram de se libérer
timeout /t 3 /nobreak >nul

REM --- 3. Vérifier la présence de config.yaml ---
echo [3/4] Vérification de la configuration...
if not exist "config.yaml" (
    if exist "config.example.yaml" (
        copy config.example.yaml config.yaml >nul
        echo     config.example.yaml → config.yaml (copie)
    ) else (
        echo     ❌ Aucun fichier config.yaml trouvé !
        pause
        exit /b 1
    )
) else (
    echo     ✔ config.yaml trouvé
)

REM --- 4. Lancer le bot ---
echo [4/4] Démarrage du bot...
echo.
echo ============================================
echo  Bot démarré — Presse Ctrl+C pour arrêter
echo ============================================
python run_paper.py

echo.
pause