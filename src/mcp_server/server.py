"""MCP server: 9 tools backed by Qdrant.

Tools (identical API surface to milvus-mcp-agent):
  - search_documents   (query, top_k, filename_filter, min_score)
  - ingest_file        (file_path)
  - ingest_directory   (directory)
  - list_documents     ()
  - get_document_stats ()
  - add_document       (mode=path|content, ...)         v0.9.0+
  - update_document    (filename, content)              v0.9.0+
  - describe_image     (mode=path|base64, ...)          v0.9.1+
  - resize_image       (file_path, max_width, ...)      v0.9.2+

Run: `python run_mcp_server.py` (the legacy entrypoint wrapper)

v0.4.0: the CLI wrapper `divine-sense-agent.bat server` / `run_mcp_server.py`
have been removed because the *client-side* MCP integration
(`McpRegistry`, `FirefoxClient`, `/mcp`, `/firefox`) was dropped. This
module still ships as a *server* — external MCP clients (Claude Desktop,
Cline, Cursor, …) can still connect to it over stdio by running:

    python -m src.mcp_server.server

v0.9.0+: ``add_document`` / ``update_document`` are exposed so external
clients (Claude Desktop etc.) get the same LLM-driven ingest surface
that the in-process ``QdrantLocalHost`` already advertises. Sandbox
rules are identical: see ``src.mcp_client.host`` for the helpers.

v0.9.1+: ``describe_image`` exposes the local Ollama vision model
through MCP. v0.9.2+ adds ``resize_image`` so the LLM can downscale
oversize images (Pillow-backed) before re-calling ``describe_image``
under the 4 MB vision cap.
"""
from __future__ import annotations

import asyncio
import sys as _sys
from pathlib import Path

# MCP SDK
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import TextContent, Tool

from src.config import config
from src.ingest.pipeline import IngestPipeline
from src.mcp_client.host import QdrantLocalHost
from src.vector_store.qdrant_client import QdrantStore
from src.vector_store.retriever import Retriever


class QdrantMCPServer:
    """Qdrant-backed MCP server. Same tool surface as milvus-mcp-agent."""

    def __init__(self):
        self.store = QdrantStore()
        self.pipeline = IngestPipeline()
        self.retriever = Retriever()
        # v0.9.0+: defer add_document / update_document dispatch
        # to the same in-process handler the LocalHost uses, so
        # the sandbox rules are identical regardless of transport.
        self._local = QdrantLocalHost()
        self.server = Server("divine-sense-agent")
        self._register_tools()

    def _register_tools(self) -> None:
        @self.server.list_tools()
        async def _list() -> list[Tool]:
            return [
                Tool(name="search_documents",
                     description="Semantic search over ingested documents.",
                     inputSchema={
                         "type": "object",
                         "properties": {
                             "query": {"type": "string"},
                             "top_k": {"type": "integer", "default": 5},
                             "filename_filter": {"type": "string"},
                             "min_score": {"type": "number", "default": 0.1},
                         },
                         "required": ["query"],
                     }),
                Tool(name="ingest_file",
                     description="Ingest a single file into the Qdrant collection.",
                     inputSchema={"type": "object",
                                  "properties": {"file_path": {"type": "string"}},
                                  "required": ["file_path"]}),
                Tool(name="ingest_directory",
                     description="Recursively ingest a directory.",
                     inputSchema={"type": "object",
                                  "properties": {"directory": {"type": "string"}}}),
                Tool(name="list_documents",
                     description="List all ingested document filenames.",
                     inputSchema={"type": "object", "properties": {}}),
                Tool(name="get_document_stats",
                     description="Return Qdrant collection statistics.",
                     inputSchema={"type": "object", "properties": {}}),
                Tool(name="search_media",
                     description=(
                         "v0.9.114.0 — cross-modal search over MEDIA "
                         "(EmbeddingGemma 2 shared text+image+audio "
                         "768-dim space; the text query finds image/"
                         "audio vectors). Honest error when the media "
                         "path is unavailable — the media semantics "
                         "also live in the text pool via the stored "
                         "vision descriptions (search_documents)."
                     ),
                     inputSchema={
                         "type": "object",
                         "properties": {
                             "query": {"type": "string"},
                             "top_k": {"type": "integer", "default": 5},
                             "filename_filter": {"type": "string"},
                         },
                         "required": ["query"],
                     }),
                Tool(name="add_document",
                     description=(
                         "Ingest a new file. mode='path' reads from "
                         "disk (no context pollution); mode='content' "
                         "writes an inline string. Sandboxed under "
                         "QDRANT_IMPORT_ALLOWED_ROOT."
                     ),
                     inputSchema={
                         "type": "object",
                         "properties": {
                             "mode": {"type": "string",
                                      "enum": ["path", "content"]},
                             "file_path": {"type": "string"},
                             "filename": {"type": "string"},
                             "content": {"type": "string"},
                             "overwrite": {"type": "boolean",
                                           "default": False},
                         },
                         "required": ["mode"],
                     }),
                Tool(name="update_document",
                     description=(
                         "Replace an already-indexed document with new "
                         "content (delete old chunks + re-ingest)."
                     ),
                     inputSchema={
                         "type": "object",
                         "properties": {
                             "filename": {"type": "string"},
                             "content": {"type": "string"},
                         },
                         "required": ["filename", "content"],
                     }),
                Tool(name="describe_image",
                     description=(
                         "Describe an image via the local Ollama "
                         "vision model. mode='path' reads from disk; "
                         "mode='base64' decodes inline bytes. Returns "
                         "{description, vision_backend, from_cache, "
                         "sha1, ...}. v0.9.104.5: raw_image_base64 is "
                         "no longer returned — the description is "
                         "the payload; repeated identical images hit "
                         "a process-wide SHA-1 cache."
                     ),
                     inputSchema={
                         "type": "object",
                         "properties": {
                             "mode": {"type": "string",
                                      "enum": ["path", "base64"]},
                             "file_path": {"type": "string"},
                             "data_base64": {"type": "string"},
                             "filename_hint": {"type": "string"},
                             "prompt": {"type": "string"},
                         },
                         "required": ["mode"],
                     }),
                Tool(name="resize_image",
                     description=(
                         "Resize an image in the import sandbox so "
                         "it fits the 4 MB vision cap. Use after "
                         "describe_image returns ``too large``; "
                         "downscale, then re-call describe_image on "
                         "the output. Pillow-backed, preserves EXIF "
                         "(incl. Orientation). Returns "
                         "{output_path, output_dimensions, "
                         "within_cap, raw_image_base64}."
                     ),
                     inputSchema={
                         "type": "object",
                         "properties": {
                             "file_path": {"type": "string"},
                             "max_width": {"type": "integer"},
                             "max_height": {"type": "integer"},
                             "out_filename": {"type": "string"},
                             "format": {"type": "string",
                                        "enum": ["jpeg", "png", "webp"]},
                             "quality": {"type": "integer"},
                         },
                         "required": ["file_path", "max_width"],
                     }),
            ]

        @self.server.call_tool()
        async def _call(name: str, arguments: dict) -> list[TextContent]:
            try:
                if name == "search_documents":
                    return await self._handle_search(arguments)
                if name == "ingest_file":
                    return await self._handle_ingest_file(arguments)
                if name == "ingest_directory":
                    return await self._handle_ingest_directory(arguments)
                if name == "list_documents":
                    return await self._handle_list_documents()
                if name == "get_document_stats":
                    return await self._handle_get_stats()
                if name == "add_document":
                    return await self._handle_add_document(arguments)
                if name == "update_document":
                    return await self._handle_update_document(arguments)
                if name == "describe_image":
                    return await self._handle_describe_image(arguments)
                if name == "resize_image":
                    return await self._handle_resize_image(arguments)
                if name == "search_media":
                    return await asyncio.to_thread(
                        self._do_search_media, args)
            except Exception as e:
                return [TextContent(type="text", text=f"Error: {e}")]
            return [TextContent(type="text", text=f"Unknown tool: {name}")]

    async def _handle_search(self, args: dict) -> list[TextContent]:
        # Run blocking call in worker thread to keep event loop responsive.
        return await asyncio.to_thread(self._do_search, args)

    def _do_search_media(self, args: dict) -> list[TextContent]:
        """v0.9.114.0 — same in-process handler as the LocalHost tool."""
        import json as _json
        result = self._local._search_media(
            query=args.get("query", ""),
            top_k=int(args.get("top_k") or 5),
            filename_filter=args.get("filename_filter") or None,
        )
        return [TextContent(type="text",
                            text=_json.dumps(result, ensure_ascii=False))]

    def _do_search(self, args: dict) -> list[TextContent]:
        query = args["query"]
        top_k = args.get("top_k", 5)
        filename_filter = args.get("filename_filter")
        min_score = args.get("min_score", 0.1)
        results = self.retriever.search(
            query=query, top_k=top_k,
            filename_filter=filename_filter, min_score=min_score,
        )
        if not results:
            return [TextContent(type="text", text="No relevant documents found.")]
        out = [f"## Search Results for: \"{query}\"",
               f"Found {len(results)} relevant chunks:\n"]
        for i, r in enumerate(results, 1):
            text = (r.get("text") or "")[:500]
            if len(r.get("text") or "") > 500:
                text += "..."
            out.append(f"### Result {i} (score: {r['score']:.4f})")
            out.append(f"**Source:** {r.get('filename', 'unknown')}")
            out.append(f"**Content:**\n{text}\n")
        return [TextContent(type="text", text="\n".join(out))]

    async def _handle_ingest_file(self, args: dict) -> list[TextContent]:
        return await asyncio.to_thread(self._do_ingest_file, args)

    def _do_ingest_file(self, args: dict) -> list[TextContent]:
        fp = Path(args["file_path"])
        n = self.pipeline.ingest_file(str(fp))
        return [TextContent(type="text", text=f"Ingested {fp.name}: {n} chunks.")]

    async def _handle_ingest_directory(self, args: dict) -> list[TextContent]:
        return await asyncio.to_thread(self._do_ingest_directory, args)

    def _do_ingest_directory(self, args: dict) -> list[TextContent]:
        d = Path(args.get("directory") or config.WATCH_DIR)
        results = self.pipeline.ingest_directory(str(d))
        total = sum(results.values())
        return [TextContent(type="text",
                            text=f"Ingested {len(results)} files, {total} chunks total.")]

    async def _handle_list_documents(self) -> list[TextContent]:
        return await asyncio.to_thread(self._do_list_documents)

    def _do_list_documents(self) -> list[TextContent]:
        docs = self.store.list_documents()
        if not docs:
            return [TextContent(type="text", text="No documents ingested yet.")]
        return [TextContent(type="text",
                            text="Ingested documents:\n" + "\n".join(f"- {d}" for d in docs))]

    async def _handle_get_stats(self) -> list[TextContent]:
        return await asyncio.to_thread(self._do_get_stats)

    def _do_get_stats(self) -> list[TextContent]:
        import json
        return [TextContent(type="text", text=json.dumps(self.store.get_stats(), indent=2))]

    # ── v0.9.0+ add_document / update_document ────────────────
    # Delegate to QdrantLocalHost so the sandbox rules are
    # identical regardless of transport (in-process tool call vs
    # external stdio MCP client). The handlers return a dict
    # already; we JSON-encode it for the TextContent payload.
    async def _handle_add_document(self, args: dict) -> list[TextContent]:
        return await asyncio.to_thread(self._do_add_document, args)

    def _do_add_document(self, args: dict) -> list[TextContent]:
        import json
        out = self._local._add_document(args or {})
        return [TextContent(type="text", text=json.dumps(out, ensure_ascii=False, indent=2))]

    async def _handle_update_document(self, args: dict) -> list[TextContent]:
        return await asyncio.to_thread(self._do_update_document, args)

    def _do_update_document(self, args: dict) -> list[TextContent]:
        import json
        out = self._local._update_document(args or {})
        return [TextContent(type="text", text=json.dumps(out, ensure_ascii=False, indent=2))]

    async def _handle_describe_image(self, args: dict) -> list[TextContent]:
        return await asyncio.to_thread(self._do_describe_image, args)

    def _do_describe_image(self, args: dict) -> list[TextContent]:
        import json
        out = self._local._describe_image(args or {})
        # NB: ``raw_image_base64`` is large (could be 4 MB base64
        # of a 3 MB image); indent=2 quadruples it. Strip
        # indent so the TextContent payload stays compact.
        return [TextContent(
            type="text",
            text=json.dumps(out, ensure_ascii=False, separators=(",", ":")),
        )]

    # ── v0.9.2+ resize_image ────────────────────────────
    # Compact JSON — the downscaled file may still be a few MB
    # of base64; indent would 4x the payload.
    async def _handle_resize_image(self, args: dict) -> list[TextContent]:
        return await asyncio.to_thread(self._do_resize_image, args)

    def _do_resize_image(self, args: dict) -> list[TextContent]:
        import json
        out = self._local._resize_image(args or {})
        return [TextContent(
            type="text",
            text=json.dumps(out, ensure_ascii=False, separators=(",", ":")),
        )]

    async def run_stdio(self) -> None:
        """Run the MCP server over stdio."""
        async with stdio_server() as (r, w):
            await self.server.run(r, w, self.server.create_initialization_options())


def main() -> None:
    server = QdrantMCPServer()
    asyncio.run(server.run_stdio())


if __name__ == "__main__":
    main()