@echo off
REM Build the divine-sense-agent-gui PyInstaller bundle.
REM Output: dist\divine-sense-agent-gui\divine-sense-agent-gui.exe + assets/
REM
REM Prerequisites:
REM   pip install -e .[build]
REM   (tested with pyinstaller 6.x on Windows + Python 3.10+)

chcp 65001 >nul
setlocal
cd /d "%~dp0"

call "C:\ProgramData\miniconda3\condabin\conda.bat" activate milvus-mcp >nul 2>&1
if errorlevel 1 (
    echo [error] conda env milvus-mcp not found. Aborting.
    exit /b 1
)

echo === Cleaning previous build ===
if exist build rmdir /s /q build
if exist dist  rmdir /s /q dist

echo === Running PyInstaller ===
pyinstaller gui.spec
if errorlevel 1 (
    echo.
    echo [error] PyInstaller failed. See above for details.
    exit /b 1
)

echo.
echo === Build complete ===
echo Output:  %CD%\dist\divine-sense-agent-gui\divine-sense-agent-gui.exe
echo Size:    (see dir listing)
dir /B dist\divine-sense-agent-gui\divine-sense-agent-gui.exe | findstr /v "^$"
echo.
echo To run:  dist\divine-sense-agent-gui\divine-sense-agent-gui.exe
echo Logs at: dist\divine-sense-agent-gui\gui\app.log  (after first run)

endlocal
