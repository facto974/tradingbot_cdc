@echo off
REM =====================================================
REM  tradingbot.bat  —  Lancement unique du bot paper-trading
REM  Usage :   tradingbot           (depuis n'importe où)
REM =====================================================
setlocal enabledelayedexpansion

REM --- Répertoire racine du projet ---
set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

echo ============================================
echo  🚀 TRADINGBOT CDC
echo  Répertoire : %PROJECT_DIR%
echo ============================================

REM --- 1. Forcer le nettoyage du cache Python ---
echo [1/4] Nettoyage du cache Python...
if exist __pycache__ rmdir /s /q __pycache__ 2>nul
for /d /r . %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d" 2>nul

REM Nettoyage des PNG de test
echo Nettoyage des fichiers de test...
del /q test_equity.png 2>nul
del /q test_signals.png 2>nul

REM --- 2. Tuer les anciennes instances Python ---
echo [2/4] Arrêt des anciennes instances...
powershell -Command "Get-Process python* -ErrorAction SilentlyContinue | Stop-Process -Force"
timeout /t 3 /nobreak >nul

REM --- 3. Vérifier la configuration ---
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