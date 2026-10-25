@echo off
REM Switch console to UTF-8 BEFORE anything else.
chcp 65001 >nul
setlocal
cd /d "%~dp0"
call "C:\ProgramData\miniconda3\condabin\conda.bat" activate milvus-mcp >nul 2>&1
python cli.py %*
endlocal
