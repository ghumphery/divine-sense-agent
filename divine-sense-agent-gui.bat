@echo off
REM Launch the Divine Sense Agent GUI (PyWebView wrapper).
chcp 65001 >nul
setlocal
cd /d "%~dp0"
call "C:\ProgramData\miniconda3\condabin\conda.bat" activate milvus-mcp >nul 2>&1
python -m gui.app %*
endlocal
