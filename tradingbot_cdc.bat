@echo off
REM =====================================================
REM  tradingbot_cdc.bat — Lancement sécurisé du bot
REM  Usage :   tradingbot_cdc.bat
REM =====================================================
setlocal enabledelayedexpansion

set "PROJECT_DIR=%~dp0"
cd /d "%PROJECT_DIR%"

echo ============================================
echo  🚀 TRADINGBOT CDC
echo  Répertoire : %PROJECT_DIR%
echo ============================================

REM --- 1. Nettoyage du cache Python ---
echo [1/4] Nettoyage du cache Python...
for /d /r . %%d in (__pycache__) do @if exist "%%d" rmdir /s /q "%%d" 2>nul
del /q test_equity.png 2>nul
del /q test_signals.png 2>nul

REM --- 2. Arrêt des anciennes instances du bot uniquement ---
echo [2/4] Arrêt des anciennes instances du bot...
powershell -Command "Get-WmiObject Win32_Process -Filter \"Name='python.exe' AND CommandLine LIKE '%%run_paper.py%%'\" | ForEach-Object { $_.Terminate() }" 2>nul
powershell -Command "Get-WmiObject Win32_Process -Filter \"Name='python3.10.exe' AND CommandLine LIKE '%%run_paper.py%%'\" | ForEach-Object { $_.Terminate() }" 2>nul
REM Attente sans timeout (compatible Git Bash)
ping -n 2 127.0.0.1 >nul

REM --- 3. Vérification configuration ---
echo [3/4] Vérification de la configuration...
if not exist "config.yaml" (
    if exist "config.example.yaml" (
        copy config.example.yaml config.yaml >nul
        echo     config.example.yaml -> config.yaml (copie)
    ) else (
        echo     ❌ Aucun fichier config.yaml trouve !
        echo     Assurez-vous d'etre dans le bon repertoire.
        echo     Repertoire actuel : %CD%
        pause
        exit /b 1
    )
) else (
    echo     ✔ config.yaml trouve
)

REM --- 4. Lancement du bot ---
echo [4/4] Demarrage du bot...
echo.
echo ============================================
echo  Bot demarre - Presse Ctrl+C pour arreter
echo ============================================
echo  Universe : 8 actifs (BTC ETH SOL DOGE MATIC XRP ADA DOT)
echo  Capital  : $100  |  Max/trade : $85  |  Concurrence : 1
echo  TP 2%% / SL 1%%  |  Night bonus : +30%% (20h-4h UTC)
echo ============================================
echo.
python run_paper.py

echo.
pause