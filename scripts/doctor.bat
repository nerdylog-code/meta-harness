@echo off
rem Meta-Harness doctor (Windows).
rem Exit codes: 0 PASS, 1 FAIL, 2 WARN.

setlocal EnableDelayedExpansion

if "%HERMES_HOME%"=="" (
    set "HERMES_HOME=%LOCALAPPDATA%\hermes"
)
if "%HERMES_HOME%"=="" (
    set "HERMES_HOME=%USERPROFILE%\.hermes"
)

set "FAIL=0"
set "WARN=0"

echo Meta-Harness doctor
echo   HERMES_HOME: %HERMES_HOME%
echo.

if exist "%HERMES_HOME%" (
    echo [PASS] Hermes Home exists
) else (
    echo [FAIL] Hermes Home missing
    set "FAIL=1"
)

where hermes >nul 2>&1
if errorlevel 0 (
    for /f "delims=" %%v in ('hermes --version 2^>^&1 ^| findstr /v Error') do (
        echo [PASS] Hermes CLI: %%v
    )
) else (
    echo [WARN] Hermes CLI not on PATH
    set "WARN=1"
)

if exist "%HERMES_HOME%\plugins\meta-harness" (
    echo [PASS] Backend plugin installed
) else (
    echo [FAIL] Backend plugin missing
    set "FAIL=1"
)

if exist "%HERMES_HOME%\plugins\meta-harness\plugin.yaml" (
    echo [PASS] plugin.yaml present
) else (
    echo [FAIL] plugin.yaml missing
    set "FAIL=1"
)

if exist "%HERMES_HOME%\plugins\meta-harness\__init__.py" (
    echo [PASS] __init__.py present
) else (
    echo [FAIL] __init__.py missing
    set "FAIL=1"
)

if exist "%HERMES_HOME%\plugins\meta-harness\dashboard\manifest.json" (
    echo [PASS] dashboard\manifest.json present
) else (
    echo [FAIL] dashboard\manifest.json missing
    set "FAIL=1"
)

if exist "%HERMES_HOME%\plugins\meta-harness\dashboard\plugin_api.py" (
    echo [PASS] dashboard\plugin_api.py present
) else (
    echo [FAIL] dashboard\plugin_api.py missing
    set "FAIL=1"
)

if exist "%HERMES_HOME%\desktop-plugins\meta-harness\plugin.js" (
    echo [PASS] Desktop plugin installed
) else (
    echo [FAIL] Desktop plugin missing
    set "FAIL=1"
)

if exist "%HERMES_HOME%\config.yaml" (
    findstr /C:"meta-harness" "%HERMES_HOME%\config.yaml" >nul
    if errorlevel 0 (
        echo [PASS] config.yaml references meta-harness
    ) else (
        echo [WARN] config.yaml does not reference meta-harness
        set "WARN=1"
    )
)

where pi >nul 2>&1
if errorlevel 0 (
    for /f "delims=" %%v in ('pi --version 2^>^&1') do echo [PASS] Pi on PATH: %%v
) else (
    echo [WARN] Pi not on PATH - engine.pi will be reported unavailable
    set "WARN=1"
)

if exist "%HERMES_HOME%\meta-harness" (
    echo [PASS] data dir exists
) else (
    echo [INFO] data dir not yet created
)

echo.
if "%FAIL%"=="1" (
    echo [RESULT] FAIL
    exit /b 1
)
if "%WARN%"=="1" (
    echo [RESULT] PASS with warnings
    exit /b 0
)
echo [RESULT] PASS
endlocal