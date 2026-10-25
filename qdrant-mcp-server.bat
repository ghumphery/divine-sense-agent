@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
call "C:\ProgramData\miniconda3\condabin\conda.bat" activate milvus-mcp >nul 2>&1
python run_mcp_server.py
endlocal