@echo off
rem Meta-Harness uninstaller (Windows).

setlocal EnableDelayedExpansion

if "%HERMES_HOME%"=="" (
    set "HERMES_HOME=%LOCALAPPDATA%\hermes"
)
if not exist "%HERMES_HOME%" (
    echo Hermes Home not found at %HERMES_HOME% - nothing to uninstall.
    exit /b 0
)

set "BACKUP_DIR=%HERMES_HOME%\backups"
if not exist "%BACKUP_DIR%" mkdir "%BACKUP_DIR%"
for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMddTHHmmssZ"') do set "TS=%%i"
set "CONFIG=%HERMES_HOME%\config.yaml"
if exist "%CONFIG%" (
    copy /Y "%CONFIG%" "%BACKUP_DIR%\config.yaml.before-meta-harness-uninstall.!TS!" >nul
)

if exist "%HERMES_HOME%\plugins\meta-harness" rmdir /S /Q "%HERMES_HOME%\plugins\meta-harness"
if exist "%HERMES_HOME%\desktop-plugins\meta-harness" rmdir /S /Q "%HERMES_HOME%\desktop-plugins\meta-harness"

if exist "%CONFIG%" (
    powershell -NoProfile -Command "$f='%CONFIG%'; $lines = Get-Content $f; $out = New-Object System.Collections.Generic.List[string]; $skip = 0; for ($i=0; $i -lt $lines.Count; $i++) { if ($skip -gt 0) { $skip--; continue }; $line = $lines[$i]; if ($line -match '^\s*meta-harness:\s*$') { $indent = $line.Length - $line.TrimStart().Length; $skip++; while ($skip -lt $lines.Count) { $n = $lines[$skip]; $t = $n.TrimStart(); if ($t -and ($n.Length -$t.Length) -le $indent) { break }; $skip++ }; $skip--; continue }; $out.Add($line) }; Set-Content -Path $f -Value $out"
)

echo [ok] Meta-Harness removed.
echo Backup of your previous config: %BACKUP_DIR%\config.yaml.before-meta-harness-uninstall.!TS!
endlocal