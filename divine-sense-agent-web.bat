@echo off
REM Launch the Divine Sense Agent Web UI (browser mode, v0.9.15.0+).
REM Same assets + Api as the pywebview GUI; binds 127.0.0.1:8334 by
REM default. v0.9.15.1+: any local IP works, e.g.
REM   divine-sense-agent-web.bat --host 0.0.0.0        (all IPv4 - LAN URLs
REM                                               printed at startup)
REM   divine-sense-agent-web.bat --host 192.168.50.154 (specific LAN IP)
REM   divine-sense-agent-web.bat --host ::             (IPv6 dual-stack)
REM or set QDRANT_WEB_HOST / QDRANT_WEB_PORT (.env is honoured).
chcp 65001 >nul
setlocal
cd /d "%~dp0"
call "C:\ProgramData\miniconda3\condabin\conda.bat" activate milvus-mcp >nul 2>&1
python -m gui.app --web %*
endlocal
