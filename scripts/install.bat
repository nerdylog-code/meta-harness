@echo off
rem Meta-Harness installer (Windows).
rem Usage: scripts\install.bat [hermes-home]
rem        default: %HERMES_HOME% or %LOCALAPPDATA%\hermes

setlocal EnableDelayedExpansion

if "%~1"=="" (
    if "%HERMES_HOME%"=="" (
        set "HERMES_HOME=%LOCALAPPDATA%\hermes"
    )
) else (
    set "HERMES_HOME=%~1"
)

if not exist "%HERMES_HOME%" (
    echo ERROR: Hermes Home not found at %HERMES_HOME% 1>&2
    exit /b 1
)

set "ROOT=%~dp0.."
pushd "%ROOT%"
set "ROOT=%CD%"
popd

set "BACKUP_DIR=%HERMES_HOME%\backups"
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"
for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMddTHHmmssZ"') do set "TS=%%i"

set "CONFIG=%HERMES_HOME%\config.yaml"
if exist "%CONFIG%" (
    copy /Y "%CONFIG%" "%BACKUP_DIR%\config.yaml.before-meta-harness.!TS!" >nul
    echo [ok] backed up config -^> %BACKUP_DIR%\config.yaml.before-meta-harness.!TS!
)

if not exist "%HERMES_HOME%\plugins\meta-harness" mkdir "%HERMES_HOME%\plugins\meta-harness"
if not exist "%HERMES_HOME%\desktop-plugins\meta-harness" mkdir "%HERMES_HOME%\desktop-plugins\meta-harness"
if not exist "%HERMES_HOME%\meta-harness" mkdir "%HERMES_HOME%\meta-harness"

rem Backend plugin
xcopy /Y /E /I /Q "%ROOT%\hermes-plugin\*" "%HERMES_HOME%\plugins\meta-harness\"
echo [ok] backend plugin -^> %HERMES_HOME%\plugins\meta-harness

rem Desktop plugin
copy /Y "%ROOT%\desktop-plugin\plugin.js" "%HERMES_HOME%\desktop-plugins\meta-harness\plugin.js" >nul
echo [ok] desktop plugin -^> %HERMES_HOME%\desktop-plugins\meta-harness

rem Patch config.yaml (idempotent — skip if 'meta-harness' already present)
if exist "%CONFIG%" (
    findstr /C:"meta-harness" "%CONFIG%" >nul
    if errorlevel 1 (
        rem Try to insert after an existing "entries:" line under plugins:.
        rem If none exists, fall back to creating a new plugins: block.
        powershell -NoProfile -Command "$f='%CONFIG%'; $lines = Get-Content $f; $out = New-Object System.Collections.Generic.List[string]; $entriesLine = -1; for ($i=0; $i -lt $lines.Count; $i++) { if ($lines[$i] -match '^\s*entries:\s*$') { $entriesLine = $i; break } }; if ($entriesLine -ge 0) { for ($i=0; $i -lt $lines.Count; $i++) { $out.Add($lines[$i]); if ($i -eq $entriesLine) { $out.Add('    meta-harness:'); $out.Add('      enabled: true'); $out.Add('      config: {}') } } } else { $out.AddRange($lines); $out.Add(''); $out.Add('plugins:'); $out.Add('  entries:'); $out.Add('    meta-harness:'); $out.Add('      enabled: true'); $out.Add('      config: {}') }; Set-Content -Path $f -Value $out"
        echo [ok] config.yaml patched (plugins.entries.meta-harness)
    ) else (
        echo [ok] config.yaml already references meta-harness
    )
)

echo.
echo [done] Meta-Harness installed.
echo   backend plugin : %HERMES_HOME%\plugins\meta-harness
echo   desktop plugin : %HERMES_HOME%\desktop-plugins\meta-harness
echo   data dir       : %HERMES_HOME%\meta-harness
echo.
echo Next:
echo   1. Restart Hermes (or open Hermes Desktop).
echo   2. Sidebar -^> Meta-Harness.
echo   3. Ctrl+K -^> "Reload desktop plugins" if the sidebar row is missing.
echo.
echo Uninstall: scripts\uninstall.bat
echo Doctor   : scripts\doctor.bat
endlocal