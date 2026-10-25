"""MCP server: xiaozhi speaker gateway (v1.0.0).

讓自架的 xiaozhi-esp32-server 透過 MCP 呼叫本專案的能力。

方向：xiaozhi-server 是 MCP **client**，本檔案是 MCP **server**。
xiaozhi-server 的 mcp_server_settings.json 支援三種傳輸：
stdio / sse / streamable-http — 本檔案同時提供三種啟動方式：

    # stdio（xiaozhi-server 以子程序方式拉起）
    python -m src.mcp_server.xiaozhi_server

    # streamable-http（推薦給自架 server，跨機器可用）
    python -m src.mcp_server.xiaozhi_server --http --port 9090
    #   → endpoint: http://<本機IP>:9090/mcp

安全模型（照 telegram_server.py 的 allowlist 慣例）
--------------------------------------------------
* ``XIAOZHI_MCP_TOKEN``：設定後，HTTP 傳輸要求
  ``Authorization: Bearer <token>``；stdio 傳輸不受影響。
* 工具面刻意「唯讀優先」：查詢類全開，寫入類（記事/下單/PTZ）
  需 ``XIAOZHI_MCP_WRITES=on`` 才註冊 — 音箱被誤喚醒也不會亂寫資料。

工具清單（唯讀預設）
--------------------
* ``xiaozhi_search_documents(query)``   — 語意搜尋本機知識庫
* ``xiaozhi_search_memory(query)``      — 搜尋長期記憶
* ``xiaozhi_list_notes(query?)``        — 列出/搜尋 Keep 記事
* ``xiaozhi_get_time()``                — 現在時間
* ``xiaozhi_stock_quote(symbol)``       — 股票報價（模擬層）
* ``xiaozhi_camera_snapshot(name)``     — ONVIF 攝影機截圖（回傳沙箱路徑）

寫入類（需 XIAOZHI_MCP_WRITES=on）
----------------------------------
* ``xiaozhi_create_note(title, body, category?)`` — 建記事
* ``xiaozhi_send_telegram(text)``        — 推播到允許的 Telegram chat
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from typing import Any

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

logger = logging.getLogger("xiaozhi_server")

WRITES_ENABLED = os.environ.get("XIAOZHI_MCP_WRITES", "").lower() in ("on", "1", "true")
MCP_TOKEN = os.environ.get("XIAOZHI_MCP_TOKEN", "")

# ---------------------------------------------------------------- tool defs

READ_TOOLS: list[Tool] = [
    Tool(
        name="xiaozhi_search_documents",
        description="語意搜尋使用者的本機知識庫（Qdrant 文件集），回傳最相關的段落。",
        inputSchema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜尋語句"},
                "top_k": {"type": "integer", "description": "回傳筆數，預設 5"},
            },
            "required": ["query"],
        },
    ),
    Tool(
        name="xiaozhi_search_memory",
        description="搜尋使用者的長期記憶（過去對話/決策的沉澱）。",
        inputSchema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
    ),
    Tool(
        name="xiaozhi_list_notes",
        description="列出或搜尋使用者的 Keep 記事（標題+內文關鍵字）。",
        inputSchema={
            "type": "object",
            "properties": {"query": {"type": "string", "description": "留空列出最近的"}},
        },
    ),
    Tool(
        name="xiaozhi_get_time",
        description="取得目前本地日期與時間。",
        inputSchema={"type": "object", "properties": {}},
    ),
    Tool(
        name="xiaozhi_stock_quote",
        description="查詢台股即時報價快照（模擬資料來源）。",
        inputSchema={
            "type": "object",
            "properties": {"symbol": {"type": "string", "description": "股票代號，如 2330"}},
            "required": ["symbol"],
        },
    ),
    Tool(
        name="xiaozhi_camera_snapshot",
        description="對已收藏的 ONVIF 攝影機截圖，回傳圖檔路徑。",
        inputSchema={
            "type": "object",
            "properties": {"name": {"type": "string", "description": "攝影機收藏名稱"}},
            "required": ["name"],
        },
    ),
]

WRITE_TOOLS: list[Tool] = [
    Tool(
        name="xiaozhi_create_note",
        description="建立一則 Keep 記事。",
        inputSchema={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "body": {"type": "string"},
                "category": {"type": "string"},
            },
            "required": ["title", "body"],
        },
    ),
    Tool(
        name="xiaozhi_send_telegram",
        description="發送文字訊息到使用者允許的 Telegram 對話。",
        inputSchema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    ),
]


def _tools() -> list[Tool]:
    return READ_TOOLS + (WRITE_TOOLS if WRITES_ENABLED else [])


# ---------------------------------------------------------------- backend calls
# 這些呼叫走「本機 HTTP 到 qdrant-rag-agent 的 MCP 端點」或直接 import。
# 為了讓 stdio 模式零依賴，這裡用最單純的實作：優先嘗試同進程 import
# 本專案模組，失敗則回覆明確錯誤（不假造結果）。


def _try_import(modpath: str, fn: str, *args, **kwargs):
    mod = __import__(modpath, fromlist=[fn])
    return getattr(mod, fn)(*args, **kwargs)


def _doc_search(query: str, top_k: int = 5) -> str:
    try:
        from qdrant_client import QdrantClient  # noqa: F401  (availability probe)
        import httpx  # noqa: F401
    except ImportError:
        return "（後端模組未安裝：請在 qdrant-rag-agent 的 venv 下執行本 server）"
    try:
        result = _try_import(
            "src.tools.qdrant_search", "search_documents", query=query, top_k=top_k
        )
        return json.dumps(result, ensure_ascii=False, default=str)[:4000]
    except Exception as exc:  # noqa: BLE001
        return f"搜尋失敗：{exc}"


def _memory_search(query: str) -> str:
    try:
        result = _try_import("src.tools.memory", "search_memory", query=query, top_k=3)
        return json.dumps(result, ensure_ascii=False, default=str)[:3000]
    except Exception as exc:  # noqa: BLE001
        return f"記憶搜尋失敗：{exc}"


def _notes_list(query: str = "") -> str:
    try:
        result = _try_import("src.tools.keep", "list_notes", query=query, max_results=5)
        return json.dumps(result, ensure_ascii=False, default=str)[:3000]
    except Exception as exc:  # noqa: BLE001
        return f"記事查詢失敗：{exc}"


def _note_create(title: str, body: str, category: str = "") -> str:
    try:
        result = _try_import(
            "src.tools.keep", "create_note", title=title, body=body,
            category=category or None, confirm=True,
        )
        return json.dumps(result, ensure_ascii=False, default=str)[:2000]
    except Exception as exc:  # noqa: BLE001
        return f"記事建立失敗：{exc}"


def _stock_quote(symbol: str) -> str:
    try:
        result = _try_import("src.tools.stock", "get_quote", symbol=symbol)
        return json.dumps(result, ensure_ascii=False, default=str)[:2000]
    except Exception as exc:  # noqa: BLE001
        return f"報價查詢失敗：{exc}"


def _camera_snapshot(name: str) -> str:
    try:
        result = _try_import("src.tools.onvif", "get_snapshot", name=name)
        return json.dumps(result, ensure_ascii=False, default=str)[:2000]
    except Exception as exc:  # noqa: BLE001
        return f"截圖失敗：{exc}"


def _telegram_send(text: str) -> str:
    try:
        result = _try_import("src.tools.telegram", "send_message", text=text)
        return json.dumps(result, ensure_ascii=False, default=str)[:2000]
    except Exception as exc:  # noqa: BLE001
        return f"Telegram 發送失敗：{exc}"


def _get_time() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S (%A)")


# ---------------------------------------------------------------- dispatch

async def _handle(name: str, arguments: dict[str, Any]) -> str:
    if name == "xiaozhi_search_documents":
        return _doc_search(arguments["query"], int(arguments.get("top_k", 5)))
    if name == "xiaozhi_search_memory":
        return _memory_search(arguments["query"])
    if name == "xiaozhi_list_notes":
        return _notes_list(arguments.get("query", ""))
    if name == "xiaozhi_get_time":
        return _get_time()
    if name == "xiaozhi_stock_quote":
        return _stock_quote(arguments["symbol"])
    if name == "xiaozhi_camera_snapshot":
        return _camera_snapshot(arguments["name"])
    if name == "xiaozhi_create_note" and WRITES_ENABLED:
        return _note_create(
            arguments["title"], arguments["body"], arguments.get("category", "")
        )
    if name == "xiaozhi_send_telegram" and WRITES_ENABLED:
        return _telegram_send(arguments["text"])
    return f"未知工具或未啟用：{name}"


def build_server() -> Server:
    app = Server("xiaozhi-gateway")

    @app.list_tools()
    async def list_tools() -> list[Tool]:
        return _tools()

    @app.call_tool()
    async def call_tool(name: str, arguments: dict[str, Any]) -> list[TextContent]:
        try:
            text = await _handle(name, arguments or {})
        except Exception as exc:  # noqa: BLE001
            logger.exception("tool %s failed", name)
            text = f"工具執行錯誤：{exc}"
        return [TextContent(type="text", text=text)]

    return app


# ---------------------------------------------------------------- entry

def main() -> None:
    parser = argparse.ArgumentParser(description="xiaozhi MCP gateway")
    parser.add_argument("--http", action="store_true", help="run streamable-http instead of stdio")
    parser.add_argument("--port", type=int, default=9090)
    args = parser.parse_args()

    app = build_server()

    if not args.http:
        asyncio_run = __import__("asyncio").run
        asyncio_run(stdio_server()(app))
        return

    # streamable-http transport
    import uvicorn  # local import: only needed for --http
    from mcp.server.streamable_http_manager import StreamableHTTPSessionManager

    session_manager = StreamableHTTPSessionManager(app=app, json_response=True)

    from starlette.applications import Starlette
    from starlette.responses import JSONResponse
    from starlette.routing import Mount

    async def _lifespan(app_):  # noqa: ANN001
        async with session_manager.run():
            yield

    async def _health(_request):  # noqa: ANN001
        return JSONResponse({"ok": True, "server": "xiaozhi-gateway"})

    routes = [Mount("/mcp", app=session_manager.handle_request)]
    star = Starlette(routes=routes, lifespan=_lifespan)

    import contextlib

    @star.route("/health", methods=["GET"])
    async def health(request):  # noqa: ANN001, ARG001
        return _health(request)

    token_header = f"Bearer {MCP_TOKEN}" if MCP_TOKEN else None

    @star.middleware("http")
    async def auth_mw(request, call_next):  # noqa: ANN001
        if token_header and request.url.path.startswith("/mcp"):
            if request.headers.get("Authorization") != token_header:
                return JSONResponse({"error": "unauthorized"}, status_code=401)
        return await call_next(request)

    import uvicorn as _uv

    _uv.run(star, host="0.0.0.0", port=args.port)
    del uvicorn, contextlib  # keep linters quiet about the local import


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()