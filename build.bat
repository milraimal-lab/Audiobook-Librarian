@echo off
title Build AudiobookLibrarian.exe
cd /d "%~dp0"

echo === Checking / installing PyInstaller ===
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo PyInstaller not found - installing...
    pip install pyinstaller
    if errorlevel 1 (
        echo ERROR: pip install failed. Make sure Python and pip are on your PATH.
        pause
        exit /b 1
    )
)

echo.
echo === Building AudiobookLibrarian.exe ===
pyinstaller --clean AudiobookLibrarian.spec

if errorlevel 1 (
    echo.
    echo BUILD FAILED - see errors above.
    pause
    exit /b 1
)

echo.
echo === Bundling ffmpeg (for Build M4B) ===
if exist "dist\ffmpeg.exe" goto ffmpeg_ok

rem Try copying from PATH first
for /f "delims=" %%F in ('where ffmpeg 2^>nul') do (
    copy /y "%%F" "dist\ffmpeg.exe" >nul
    goto ffmpeg_ok
)

rem Not on PATH - download the release essentials build (~90 MB)
echo ffmpeg not found on PATH - downloading...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$ProgressPreference='SilentlyContinue'; $z=Join-Path $env:TEMP 'ffmpeg_dl.zip'; Invoke-WebRequest 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip' -OutFile $z; $d=Join-Path $env:TEMP 'ffmpeg_dl'; if(Test-Path $d){Remove-Item $d -Recurse -Force}; Expand-Archive $z $d; $e=Get-ChildItem $d -Recurse -Filter ffmpeg.exe | Select-Object -First 1; Copy-Item $e.FullName 'dist\ffmpeg.exe' -Force"
if exist "dist\ffmpeg.exe" goto ffmpeg_ok

echo WARNING: Could not bundle ffmpeg.exe - Build M4B will need ffmpeg on PATH.
goto ffmpeg_end

:ffmpeg_ok
echo ffmpeg.exe is in dist\ - Build M4B works out of the box.

:ffmpeg_end
echo.
echo === Done! ===
echo The exe is at:  dist\AudiobookLibrarian.exe
echo (keep ffmpeg.exe next to it for Build M4B)
echo.
pause
