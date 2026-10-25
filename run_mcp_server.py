#!/usr/bin/env python3
"""MCP server entry point for qdrant-rag-agent."""
import sys
from pathlib import Path

# Load .env
_env = Path(__file__).resolve().parent / ".env"
if _env.exists():
    import os
    for line in _env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

sys.path.insert(0, str(Path(__file__).resolve().parent))

if __name__ == "__main__":
    from src.mcp_server.server import main
    main()