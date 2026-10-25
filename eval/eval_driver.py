# -*- coding: utf-8 -*-
"""20-question retrieval benchmark driver (embedding-model comparison).

v0.9.113.0 — ROADMAP Qwen3-4B verification recipe companion.

Usage (process-env override — never touches .env):
    python eval/eval_driver.py file_docs 4096         # current 8B baseline
    python eval/eval_driver.py file_docs_4b 2560      # 4B side collection

Env the driver pins for itself:
    EMBEDDING_BACKEND=local  OLLAMA_BASE_URL (not used by local)
    EMBEDDING_DIM=<dim arg>  QDRANT_COLLECTION=<collection arg>

Scores: hit@5 (any expect filename in the top-5), first-hit rank, embed ms.
Questions whose expect set is NOT in the searched collection are SKIPPED
(they cannot be judged on a subset collection) and excluded from the rate.
"""
import io
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8",
                              errors="replace")

COLL = sys.argv[1] if len(sys.argv) > 1 else "file_docs"
DIM = sys.argv[2] if len(sys.argv) > 2 else "4096"
URL = ""
if "--url" in sys.argv:
    URL = sys.argv[sys.argv.index("--url") + 1]

os.environ["EMBEDDING_BACKEND"] = "local"
os.environ["EMBEDDING_DIM"] = DIM
os.environ["QDRANT_COLLECTION"] = COLL
if URL:
    os.environ["LLAMACPP_EMBED_URL"] = URL
os.environ.pop("QDRANT_HYBRID", None)

import src.config as cfg  # noqa: E402

cfg.QDRANT_COLLECTION = COLL
cfg.EMBEDDING_DIM = int(DIM)

from src.ingest.embedder import Embedder  # noqa: E402
from src.vector_store.qdrant_client import QdrantStore  # noqa: E402


def main() -> None:
    emb = Embedder()
    store = QdrantStore()
    store.collection = COLL

    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "eval_20q.json"), encoding="utf-8") as f:
        questions = json.load(f)["questions"]

    # filenames present in the searched collection → skip unjudgeable rows
    seen_files: set[str] = set()
    nxt = None
    while True:
        pts, nxt = store.client.scroll(
            store.collection, limit=500, offset=nxt,
            with_payload=["filename"], with_vectors=False)
        for p in pts:
            fn = (p.payload or {}).get("filename") or ""
            if fn:
                seen_files.add(fn)
        if not nxt:
            break

    active, skipped = [], []
    for item in questions:
        if set(item["expect"]) & seen_files:
            active.append(item)
        else:
            skipped.append(item["q"][:40])
    if skipped:
        print(f"SKIPPED (expect not in {store.collection}): {len(skipped)}")
        for s in skipped:
            print("   -", s)

    rows, hits = [], 0
    t_all = time.time()
    for i, item in enumerate(active, 1):
        expect = set(item["expect"])
        try:
            t0 = time.time()
            vec = emb.embed(item["q"])
            emb_ms = (time.time() - t0) * 1000
            res = store.search(query_vector=vec, top_k=5)
        except Exception as e:
            rows.append({"q": item["q"], "ok": False, "rank": None,
                         "emb_ms": 0, "top5": [repr(e)[:60]]})
            print(f"[{i:02d}] ERR {e!r:.80}")
            continue
        top5 = [(h["filename"], round(h["score"], 4)) for h in res[:5]]
        rank = next((j + 1 for j, (fn, _) in enumerate(top5)
                     if fn in expect), None)
        rows.append({"q": item["q"], "ok": rank is not None, "rank": rank,
                     "emb_ms": emb_ms, "top5": top5})
        hits += rank is not None
        print(f"[{i:02d}] {'OK rk' + str(rank) if rank else 'MISS'} "
              f"{emb_ms:.0f}ms {item['q'][:34]}")
    dt = time.time() - t_all
    print()
    print(f"== {store.collection}: hit@5 {hits}/{len(active)} == "
          f"avg embed {sum(r['emb_ms'] for r in rows)/max(len(rows),1):.0f} ms, "
          f"total {dt:.1f}s")
    for i, r in enumerate(rows, 1):
        mark = "OK" if r["ok"] else "MISS"
        rk = f" rk{r['rank']}" if r["rank"] else ""
        t5 = "  ".join(f"{f}@{s}" for f, s in r["top5"])[:110]
        print(f"| {i} | {r['q'][:44]} | {mark}{rk} | {t5}")


if __name__ == "__main__":
    main()