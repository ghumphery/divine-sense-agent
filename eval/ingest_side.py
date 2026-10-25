# -*- coding: utf-8 -*-
"""Ingest a file list into a SIDE collection (per-embedding-backend recipe).

v0.9.113.0 — ROADMAP Qwen3-4B recipe companion. The per-collection backend
switch is all-process-env: point OLLAMA_BASE_URL/EMBEDDING_BACKEND at the
backend you want and this script mirrors the eval subset into a dedicated
collection — the main collection (file_docs) stays untouched and the daily
8B llama.cpp server keeps running (a second server on a second port, or an
ollama-server backend, does not disturb it).

Usage:
    python eval/ingest_side.py file_docs_4b 2560
    python eval/ingest_side.py text_docs_gemma2 768 --url http://127.0.0.1:8081/v1/embeddings
    python eval/ingest_side.py text_docs_gemma2 768 --list myfiles.txt

Env that governs the backend (set BEFORE running):
    EMBEDDING_BACKEND   local (llama.cpp) | ollama | ...
    LLAMACPP_EMBED_URL  --url wins over the .env value when given
    OLLAMA_BASE_URL / OLLAMA_EMBED_MODEL  (=embeddinggemma-2:270m etc.)
"""
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8",
                              errors="replace")

U = "C:/Users/humphery/uploads/"
DEFAULT_FILES = [
    U + "20261007-105810-ARCHITECTURE.md",
    U + "20261007-105810-README.md",
    U + "20261007-105810-SKILL_GUIDE.md",
    U + "20261007-105810-USER_MANUAL.md",
    "C:/Users/humphery/backtest_2330_report.md",
    "C:/Users/humphery/claude_code_loops_and_graphs.md",
    U + "cloudflare_audit_skill.md",
    U + "finrl_deepdive.md",
    U + "jev_tweets_digest.md",
    "C:/Users/humphery/ml4trading_3e_notes.md",
    U + "museai_deepdive.md",
    U + "museai_skills_leak.md",
    U + "nautilus_trader_deepdive.md",
    U + "openmuse_deepdive.md",
    U + "project_improvement_plan.md",
    U + "project_improvement_plan_v2.md",
    "C:/Users/humphery/project_improvement_plan_v3.md",
    U + "quant_guide_trading_randomness.md",
    U + "taiwan_stock_jev_architecture.md",
    U + "telegram/<YOUR_TELEGRAM_CHAT_ID>/ssrn_id3453295_code2224789.pdf",
    "C:/Users/humphery/xquant_book_notes.md",
    U + "telegram/<YOUR_TELEGRAM_CHAT_ID>/加密分持解決方案說明_第三方支援_20240125v4.pptx",
]


def _parse(argv: list[str]) -> tuple[str, str, list[str], str]:
    collection, dim = argv[1], argv[2].rstrip("d")
    files: list[str] = []
    url = ""
    if "--list" in argv:
        list_path = argv[argv.index("--list") + 1]
        with open(list_path, encoding="utf-8") as f:
            files = [ln.strip() for ln in f if ln.strip()]
    if "--url" in argv:
        url = argv[argv.index("--url") + 1]
    return collection, dim, files or DEFAULT_FILES, url


def main() -> None:
    argv = [a for a in sys.argv]
    collection, dim, files, url = _parse(argv)
    os.environ["QDRANT_COLLECTION"] = collection
    os.environ["EMBEDDING_DIM"] = dim
    if url:
        os.environ["LLAMACPP_EMBED_URL"] = url
        print(f"embed URL override: {url}")

    import src.config as cfg  # noqa: E402

    cfg.QDRANT_COLLECTION = collection
    cfg.EMBEDDING_DIM = int(dim)

    from src.ingest.pipeline import IngestPipeline  # noqa: E402

    p = IngestPipeline()
    binfo = p.embedder.backend_info()
    print(f"backend={binfo.get('backend')} model={binfo.get('ollama_model')}"
          f" collection={p.store.collection} dim={p.store.dim}")
    total = 0
    for f in files:
        try:
            n = p.ingest_file(f)
            total += n
            print(f"ok   {os.path.basename(f)}: {n}")
        except Exception as e:
            print(f"FAIL {os.path.basename(f)}: {e!r:.200}")
    print(f"TOTAL chunks upserted into {collection}: {total}")


if __name__ == "__main__":
    main()