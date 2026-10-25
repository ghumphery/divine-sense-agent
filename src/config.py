"""Configuration for divine-sense-agent.

Reads `.env` and exposes a frozen `config` singleton with typed access.

Two access patterns coexist for backward compatibility with v0.1.x:
  1. Flat attributes: ``config.QDRANT_URL`` (still works)
  2. Namespaced groups: ``config.ingest.chunk_size`` (new in v0.2.3,
     mirrors milvus-mcp-agent's structure so the new ingest pipeline
     can share code paths)
"""
from __future__ import annotations

import os
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")

# v0.9.36.1 — CJK matplotlib font for EVERY python in the process
# tree. matplotlib honours the MATPLOTLIBRC env var (a full rc file);
# pointing it at the shipped config/matplotlibrc makes Chinese chart
# labels render correctly for run_python, run_shell/run_bash children
# AND the MCP stdio servers (windows-mcp's PowerShell) — the
# v0.9.34.0 bootstrap only covered exec_python, so charts drawn via
# write_file + PowerShell still showed tofu. setdefault keeps a
# user-provided MATPLOTLIBRC winning.
_mpl_rc = Path(__file__).resolve().parents[1] / "config" / "matplotlibrc"
if _mpl_rc.is_file():
    os.environ.setdefault("MATPLOTLIBRC", str(_mpl_rc))

# v0.9.18.0 — the shared chain parser lives in the ZERO-side-effect
# module src/provider_chain.py (importing src.config runs load_dotenv
# and would re-inject .env values a test just wiped); re-exported
# here for import-path stability.
from src.provider_chain import parse_provider_chain  # noqa: E402,F401


from pathlib import Path
from typing import Any

# Load .env before anything else (qdrant-client itself reads no env vars,
# but pydantic-base-settings and our subclass does).
from dotenv import load_dotenv
load_dotenv(Path(__file__).resolve().parents[1] / ".env")


class _IngestNamespace:
    """Namespace mirroring milvus-mcp-agent ``config.ingest``."""
    supported_extensions: tuple = (
        ".txt", ".md", ".pdf", ".docx", ".pptx", ".epub",
        ".py", ".js", ".ts", ".json", ".yaml", ".yml",
        ".csv", ".html", ".css", ".xml",
    )

    @property
    def watch_dir(self) -> Path:
        return Path(os.environ.get("WATCH_DIR", "./documents"))

    @property
    def chunk_size(self) -> int:
        return int(os.environ.get("CHUNK_SIZE", "500"))

    @property
    def chunk_overlap(self) -> int:
        return int(os.environ.get("CHUNK_OVERLAP", "50"))

    @property
    def image_cache_dir(self) -> Path:
        return Path(os.environ.get("IMAGE_CACHE_DIR", "./cache/images"))

    @property
    def describe_images(self) -> bool:
        return os.environ.get("DESCRIBE_IMAGES", "true").lower() in ("1", "true", "yes")


class _LLMNamespace:
    """Namespace mirroring milvus-mcp-agent ``config.llm``."""
    @property
    def api_key(self) -> str:
        return os.environ.get("OPENAI_API_KEY", "")

    @property
    def base_url(self) -> str:
        return os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")

    @property
    def model(self) -> str:
        return os.environ.get("LLM_MODEL", "gpt-4o-mini")


class _EmbeddingNamespace:
    @property
    def dimension(self) -> int:
        # Default 4096 to match Qwen3-Embedding-8B served by local
        # llama.cpp (v0.2.5+). Legacy hash-fallback was 384-dim;
        # OpenAI text-embedding-3-small is 1536-dim; Ollama
        # nomic-embed-text is 768-dim. All padded/truncated by
        # Embedder._normalize.
        return int(os.environ.get("EMBEDDING_DIM", "4096"))

    @property
    def backend(self) -> str:
        # ``local`` (llama.cpp), ``ollama`` (Cloud/local),
        # ``openai``, ``hash``, or ``auto`` (probe in priority order).
        return os.environ.get("EMBEDDING_BACKEND", "auto").strip().lower()

    @property
    def ollama_model(self) -> str:
        # Default ``nomic-embed-text`` (768-dim). Override with
        # ``OLLAMA_EMBED_MODEL=mxbai-embed-large`` (1024-dim), etc.
        return os.environ.get("OLLAMA_EMBED_MODEL", "nomic-embed-text")


def _normalize_loopback_url(url: str) -> str:
    """Replace the loopback NAME with the IPv4 literal (v0.9.25.1).

    Measured on the dev box (2026-09-09): ``getaddrinfo('localhost')``
    returns ``::1`` FIRST, and a TCP connect to ``::1:6333`` takes
    ~2 s to be REFUSED (loopback-IPv6 anomaly — refused, not dropped,
    but refused after a ~2 s delay). qdrant-client's REST layer
    re-opens the HTTP connection for every request, so EVERY
    search / stats / group-by call paid that ~2 s refused delay:

      qdrant-client query_points  localhost : mean 2073 ms
      qdrant-client query_points  127.0.0.1: mean   29 ms   (~71x)
      raw requests POST           127.0.0.1: mean    7 ms

    The server itself answers in 10-20 ms (actix request log). Qdrant
    binds 0.0.0.0 by default, so the IPv4 literal reaches the SAME
    server. A user who genuinely wants the IPv6 loopback can write
    ``[::1]`` explicitly in .env — only the bare ``localhost`` name is
    rewritten. Also fixes the long-standing "get_collection takes
    ~2 s" / "list_documents ~3 s" observations (same root cause).
    """
    return (url or "").replace("//localhost:", "//127.0.0.1:")


class Config:
    """Read-once configuration snapshot.

    Flat attrs for backward compat + namespaced groups for new code.
    """

    # ── Qdrant ──
    # Loopback-name normalisation (v0.9.25.1) — see
    # _normalize_loopback_url() above for the measured rationale.
    QDRANT_URL: str = _normalize_loopback_url(
        os.environ.get("QDRANT_URL", "http://localhost:6333")
    )
    QDRANT_API_KEY: str = os.environ.get("QDRANT_API_KEY", "")
    QDRANT_COLLECTION: str = os.environ.get("QDRANT_COLLECTION", "file_docs")
    # v0.9.7.0: dedicated collection for log-analyzer points (one log
    # line per point) so log payloads never mix with document chunks.
    QDRANT_LOG_COLLECTION: str = os.environ.get(
        "QDRANT_LOG_COLLECTION", "log_docs",
    )
    # Default 4096 to match Qwen3-Embedding-8B served by local
    # llama.cpp (v0.2.5+); legacy hash-fallback was 384.
    EMBEDDING_DIM: int = int(os.environ.get("EMBEDDING_DIM", "4096"))

    # ── Long-term memory (v0.9.59.0+) ──
    # The MemoryStore reads these env vars DIRECTLY (the raw-string
    # convention: monkeypatch-friendly, no reload needed); these
    # class attrs are documentation / snapshot only.
    #   QDRANT_MEMORY_COLLECTION  dedicated collection (memory_docs)
    #   QDRANT_MEMORY_ENABLED     on — recall + ingest master switch
    #   QDRANT_MEMORY_TOP_K       recall depth (5)
    #   QDRANT_MEMORY_SESSION_INGEST  on — chat Q/A pairs
    #   QDRANT_MEMORY_TOOLS       web_search,web_fetch (tool results)
    #   QDRANT_MEMORY_HIT_STATS   on — recall-hit bookkeeping (v0.9.111)
    #   QDRANT_TASK_MEMORY_SINK   on — task/plan conclusion sink,
    #                             kind=task-result, store-NOT-recall
    #                             (v0.9.111)
    #   QDRANT_TELEGRAM_VOICE_REPLY   on — voice-in → voice+text out
    QDRANT_MEMORY_COLLECTION: str = os.environ.get(
        "QDRANT_MEMORY_COLLECTION", "memory_docs",
    )

    # v0.9.124.0 — agent RULE BOOK (Memento-3 rulebook工件; the LLM
    # revises its own rules when the environment breaks expectations;
    # revise requires evidence — fail-closed). Raw strings only: the
    # resolvers in src/agent/rulebook.py read os.environ directly
    # (monkeypatch-friendly; bad values fall back to defaults there).
    #   QDRANT_RULEBOOK_ENABLED     on — master switch (prompt + tools)
    #   QDRANT_RULEBOOK_FILE        default <READ sandbox>/agent_rules.md
    #   QDRANT_RULEBOOK_MAX_CHARS   4000 — prompt-injection cap
    #   QDRANT_RULEBOOK_SUGGEST     on — tool-failure hint (once/turn)
    #   QDRANT_RULEBOOK_HISTORY_MAX 20 — snapshot-ring depth
    #   QDRANT_RULEBOOK_MEMORY      on — kind=rule-lesson event sink
    #   QDRANT_REFLECT_POSTMORTEM   off — Evo² Reflector post-mortem
    #                               turn after a failed TACTI plan
    QDRANT_RULEBOOK_ENABLED: str = os.environ.get(
        "QDRANT_RULEBOOK_ENABLED", "",
    )
    QDRANT_RULEBOOK_FILE: str = os.environ.get(
        "QDRANT_RULEBOOK_FILE", "",
    )
    QDRANT_RULEBOOK_MAX_CHARS: str = os.environ.get(
        "QDRANT_RULEBOOK_MAX_CHARS", "",
    )
    QDRANT_RULEBOOK_SUGGEST: str = os.environ.get(
        "QDRANT_RULEBOOK_SUGGEST", "",
    )
    QDRANT_RULEBOOK_HISTORY_MAX: str = os.environ.get(
        "QDRANT_RULEBOOK_HISTORY_MAX", "",
    )
    QDRANT_RULEBOOK_MEMORY: str = os.environ.get(
        "QDRANT_RULEBOOK_MEMORY", "",
    )
    QDRANT_REFLECT_POSTMORTEM: str = os.environ.get(
        "QDRANT_REFLECT_POSTMORTEM", "",
    )
    # v0.9.127.0 — self-improvement adoption (items 1-5):
    #   QDRANT_RULEBOOK_TELEMETRY      on — per-rule failure counter
    #                                  (errors/last_error per rule block;
    #                                  bypasses the snapshot ring)
    #   QDRANT_PLAN_SKILL_DRAFT        on — DONE plan (>= min-stages)
    #                                  auto-drafted as SKILL.md under
    #                                  skill_drafts/ (never auto-imported)
    #   QDRANT_PLAN_SKILL_DRAFT_MIN_STAGES 3 — minimum stage count to draft
    #   QDRANT_SKILL_DRAFTS_DIR        default $QDRANT_RAG_HOME/skill_drafts
    #   QDRANT_MEMORY_MAINTENANCE      off — opt-in weekly maintenance
    #                                  schedule (report-only; never auto-purge)
    #   QDRANT_VERIFIER_ALT_PIN        on — verification corrective turn
    #                                  pins a DIFFERENT provider (task_llm-
    #                                  pinnable: openrouter/ollama only)
    #   QDRANT_TOOLROUTE_OVERRIDES     shadow — LLM-proposed ToolRoute
    #                                  pattern rows (agent_toolroute.md):
    #                                  off|shadow(log-only)|on(union)
    #   QDRANT_TOOLROUTE_OVERRIDES_FILE default <READ sandbox>/
    #                                  agent_toolroute.md
    QDRANT_RULEBOOK_TELEMETRY: str = os.environ.get(
        "QDRANT_RULEBOOK_TELEMETRY", "",
    )
    QDRANT_PLAN_SKILL_DRAFT: str = os.environ.get(
        "QDRANT_PLAN_SKILL_DRAFT", "",
    )
    QDRANT_PLAN_SKILL_DRAFT_MIN_STAGES: str = os.environ.get(
        "QDRANT_PLAN_SKILL_DRAFT_MIN_STAGES", "",
    )
    QDRANT_SKILL_DRAFTS_DIR: str = os.environ.get(
        "QDRANT_SKILL_DRAFTS_DIR", "",
    )
    QDRANT_MEMORY_MAINTENANCE: str = os.environ.get(
        "QDRANT_MEMORY_MAINTENANCE", "",
    )
    QDRANT_VERIFIER_ALT_PIN: str = os.environ.get(
        "QDRANT_VERIFIER_ALT_PIN", "",
    )
    QDRANT_TOOLROUTE_OVERRIDES: str = os.environ.get(
        "QDRANT_TOOLROUTE_OVERRIDES", "",
    )
    QDRANT_TOOLROUTE_OVERRIDES_FILE: str = os.environ.get(
        "QDRANT_TOOLROUTE_OVERRIDES_FILE", "",
    )

    # ── LLM (flat) ──
    OPENAI_API_KEY: str = os.environ.get("OPENAI_API_KEY", "")
    OPENAI_BASE_URL: str = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1")
    LLM_MODEL: str = os.environ.get("LLM_MODEL", "gpt-4o-mini")

    # v0.9.19.0: dedicated model for the `openai` provider (which is
    # the generic OpenAI-compatible escape hatch via OPENAI_BASE_URL).
    # Unset = LLM_MODEL (legacy behaviour). Setting it lets a
    # compat-endpoint config (DeepSeek / LM Studio / vLLM / ...) avoid
    # poisoning LLM_MODEL, whose global override would otherwise
    # rewrite the ollama / openrouter / siliconflow chat models and
    # break cross-provider failover. A non-default LLM_MODEL still
    # wins (same global-override semantics as the other providers).
    OPENAI_CHAT_MODEL: str = os.environ.get("OPENAI_CHAT_MODEL", "")

    # v0.9.19.0: a FIFTH, fully independent provider slot for any
    # second OpenAI-compatible endpoint (coexists with `openai` —
    # two compat endpoints can ride the same
    # LLM_PROVIDER_CHAIN). OPENAI_COMPAT_BASE_URL is REQUIRED when
    # the key is set — a generic endpoint has no sensible default
    # (defaulting would silently hit api.openai.com; _pick_provider
    # raises and _provider_candidates skips with a warning).
    OPENAI_COMPAT_API_KEY: str = os.environ.get("OPENAI_COMPAT_API_KEY", "")
    OPENAI_COMPAT_BASE_URL: str = os.environ.get("OPENAI_COMPAT_BASE_URL", "")
    OPENAI_COMPAT_CHAT_MODEL: str = os.environ.get(
        "OPENAI_COMPAT_CHAT_MODEL", ""
    )

    # ── Ollama (v0.8.0+, preferred when OLLAMA_API_KEY is set) ──
    OLLAMA_API_KEY: str = os.environ.get("OLLAMA_API_KEY", "")
    OLLAMA_BASE_URL: str = os.environ.get("OLLAMA_BASE_URL", "https://ollama.com/v1")
    OLLAMA_CHAT_MODEL: str = os.environ.get("OLLAMA_CHAT_MODEL", "gpt-oss:120b-cloud")
    OLLAMA_VISION_MODEL: str = os.environ.get("OLLAMA_VISION_MODEL", "gemma4:31b-cloud")

    # ── OpenRouter (v0.8.9+, 400+ models through one key) ──
    # OpenRouter is OpenAI-compatible — uses the openai SDK with
    # base_url=https://openrouter.ai/api/v1. Free tier available;
    # production tier ~$5/1M tokens typical. Examples:
    #   anthropic/claude-3.5-sonnet, openai/gpt-4o,
    #   google/gemini-pro-1.5, meta-llama/llama-3.1-405b-instruct.
    OPENROUTER_API_KEY: str = os.environ.get("OPENROUTER_API_KEY", "")
    OPENROUTER_BASE_URL: str = os.environ.get(
        "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
    )
    OPENROUTER_CHAT_MODEL: str = os.environ.get(
        "OPENROUTER_CHAT_MODEL", "anthropic/claude-3.5-sonnet"
    )

    # ── OpenRouter provider routing (v0.9.9.2+) ──
    # Injected as ``extra_body={"provider": {...}}`` on every
    # chat.completions.create call — ONLY when the resolved
    # provider is openrouter (other OpenAI-compatible APIs would
    # reject or silently mis-route the unknown field).
    # All values are raw strings; Agent._apply_openrouter_routing()
    # parses them into the JSON shapes OpenRouter expects.
    # An all-empty configuration injects NOTHING (default
    # OpenRouter auto-routing, exactly the v0.8.9 behaviour).
    #   order           comma list, tried in order e.g. "DeepInfra,Together"
    #   allow_fallbacks true/false (false = never fall back to
    #                   providers outside ``order``)
    #   require_parameters true/false (only providers supporting
    #                   ALL request parameters)
    #   data_collection allow/deny (deny = ZDR-style no-training
    #                   providers only)
    #   ignore          comma list of provider slugs to skip
    #   quantizations   comma list e.g. "int4,int8" (int4/int8/fp4/
    #                   fp6/fp8/fp16/bf16/fp32/unknown)
    #   sort            price | throughput
    #   model_suffix    ":nitro" (max throughput) / ":floor" (lowest
    #                   price) / ":free" — appended to the model slug
    #                   unless the model already ends with it.
    OPENROUTER_PROVIDER_ORDER: str = os.environ.get("OPENROUTER_PROVIDER_ORDER", "")
    OPENROUTER_ALLOW_FALLBACKS: str = os.environ.get("OPENROUTER_ALLOW_FALLBACKS", "")
    OPENROUTER_REQUIRE_PARAMETERS: str = os.environ.get(
        "OPENROUTER_REQUIRE_PARAMETERS", ""
    )
    OPENROUTER_DATA_COLLECTION: str = os.environ.get(
        "OPENROUTER_DATA_COLLECTION", ""
    )
    OPENROUTER_IGNORE: str = os.environ.get("OPENROUTER_IGNORE", "")
    OPENROUTER_QUANTIZATIONS: str = os.environ.get("OPENROUTER_QUANTIZATIONS", "")
    OPENROUTER_SORT: str = os.environ.get("OPENROUTER_SORT", "")
    OPENROUTER_MODEL_SUFFIX: str = os.environ.get("OPENROUTER_MODEL_SUFFIX", "")
    # v0.9.11.1+: OpenRouter `reasoning` effort (top-level request
    # field, NOT inside `provider`). Reasoning-mandatory endpoints
    # (Together's z-ai/glm-5.3-flash, gpt-oss) reject
    # {"enabled": false} but accept an effort — "low" skips the
    # thinking pass entirely and cuts a trivial hello from ~4.4 s to
    # ~1.4 s. Empty = untouched request (default reasoning).
    OPENROUTER_REASONING_EFFORT: str = os.environ.get(
        "OPENROUTER_REASONING_EFFORT", ""
    )
    # v0.9.16.4: vision model for the ImageDescriber FALLBACK — when
    # Ollama Cloud (OLLAMA_VISION_MODEL) is unconfigured or fails, the
    # describe_image tool / ingest image pipeline retries the SAME
    # OpenAI-compatible vision call against OpenRouter with this
    # model (then against OPENAI_API_KEY + OPENAI_BASE_URL).
    OPENROUTER_VISION_MODEL: str = os.environ.get(
        "OPENROUTER_VISION_MODEL", "google/gemini-2.5-flash"
    )
    # v0.9.17.0: SiliconFlow vision candidate — third in the
    # ImageDescriber fallback chain (ollama → openrouter →
    # siliconflow → openai-compat).
    SILICONFLOW_VISION_MODEL: str = os.environ.get(
        "SILICONFLOW_VISION_MODEL", "Qwen/Qwen2.5-VL-72B-Instruct"
    )
    # v0.9.13.0+: gate which MCP servers' tools ride on every chat
    # request (comma list of server_name values, e.g.
    # "qdrant-local,web-local,filesystem-local,datetime-local,meta-local").
    # Empty = full surface. Every tool schema is attached to EVERY
    # request — the 100-tool surface measured ~32k prompt tokens
    # (v0.9.11.1) — so trimming idle servers saves prefill latency
    # and cost on every single turn. A gate matching 0 tools falls
    # back to the full surface.
    MCP_ACTIVE_SERVERS: str = os.environ.get("MCP_ACTIVE_SERVERS", "")

    # v0.9.14.1+: how many LLM tool-loop iterations one ask/chat turn
    # may burn before the loop forces a final answer. GUI desktop
    # automation (windows-mcp: launch → snapshot → click → drag → ...)
    # needs far more than the historical 6 — the default is now 20.
    # Raw string here; the agent resolves it per call via
    # _resolve_max_tool_iterations() (bad values fall back to the
    # default there and never crash the import).
    MAX_TOOL_ITERATIONS: str = os.environ.get("MAX_TOOL_ITERATIONS", "")

    # ── Concurrent turn gate (v0.9.45.0+, Phase 1 parallel multi-task)
    # ──
    # Cap on how many LLM chat turns run at once across the whole
    # process (GUI chat + scheduler fires + webhook fires + telegram
    # bot + CLI all share it). Raw strings here; the resolvers live in
    # src/agent/turn_gate.py (read os.environ directly, bad values
    # fall back to unlimited / the default wait — never crash).
    #   QDRANT_MAX_CONCURRENT_TURNS  unset = unlimited (legacy)
    #   QDRANT_TURN_WAIT_SECONDS     queue wait before TurnGateBusy
    #                                (default 120 s)
    QDRANT_TURN_WAIT_SECONDS: str = os.environ.get(
        "QDRANT_TURN_WAIT_SECONDS", "",
    )

    # ── Embedder backend failure cooldown (v0.9.48.0+) ──
    # Seconds a FAILED embeddings backend is skipped by the ``auto``
    # chain (llama.cpp Vulkan ErrorDeviceLost = alive process, green
    # probe, 500 on every POST). Raw string here; src/ingest/embedder
    # resolves it per instance (bad values fall back to the default —
    # never crash the import).
    EMBEDDER_BACKEND_COOLDOWN: str = os.environ.get(
        "EMBEDDER_BACKEND_COOLDOWN", "",
    )

    # ── Jev rerank over the cosine recall pool (v0.9.83.0+) ──
    # "Rank wide, read narrow" — Retriever.search widens the recall
    # pool (QDRANT_RERANK_CANDIDATES), the Jev Decisions API answers
    # one yes/no question per chunk in a SINGLE call (live probe
    # 2026-09-21: 0.4-0.8 s, ~$0.0002/query via OPENROUTER_API_KEY),
    # and only the top_k survive. Raw strings here;
    # src/agent/rerank.py resolves them per call (clamped, never
    # crashes the import).
    #   QDRANT_RERANK              off (default) | on
    #   QDRANT_RERANK_BACKEND      jev (default; nimble rejected at
    #                              runtime — RDNA1 prefill 40-100 s)
    #   QDRANT_RERANK_CANDIDATES   20 (recall pool, clamp 2-50)
    #   QDRANT_RERANK_TIMEOUT_S    10 (clamp 2-60)
    #   QDRANT_RERANK_CHUNK_CHARS  600 (per-chunk state truncation,
    #                              clamp 100-4000)
    #   JEV_MODEL                  typesafe/jev-1.13
    QDRANT_RERANK: str = os.environ.get("QDRANT_RERANK", "")
    QDRANT_RERANK_BACKEND: str = os.environ.get(
        "QDRANT_RERANK_BACKEND", "",
    )
    QDRANT_RERANK_CANDIDATES: str = os.environ.get(
        "QDRANT_RERANK_CANDIDATES", "",
    )
    QDRANT_RERANK_TIMEOUT_S: str = os.environ.get(
        "QDRANT_RERANK_TIMEOUT_S", "",
    )
    QDRANT_RERANK_CHUNK_CHARS: str = os.environ.get(
        "QDRANT_RERANK_CHUNK_CHARS", "",
    )
    JEV_MODEL: str = os.environ.get("JEV_MODEL", "")
    QDRANT_RERANK_MIN_PROB: str = os.environ.get(
        "QDRANT_RERANK_MIN_PROB", "",
    )

    # ── Jev watch gate (v0.9.84.0+, plan §5.3) ──
    # Before a Teams watch fire burns an LLM turn: read the chat text
    # via the UIA channel (deterministic) and ask Jev noul
    # 「需要使用者本人回覆嗎？」— p ≥ SKIP_PROB → the whole LLM turn is
    # skipped (~12k tokens + 3-5 s per false fire). EVERY failure fails
    # OPEN to the LLM turn (a missed message costs more than a wasted
    # turn). Raw strings; src/teams_assist_tools.py resolves them.
    #   QDRANT_WATCH_GATE            off (default) | on
    #   QDRANT_WATCH_GATE_SKIP_PROB  0.7 (clamp 0-1, bad→default)
    QDRANT_WATCH_GATE: str = os.environ.get("QDRANT_WATCH_GATE", "")
    QDRANT_WATCH_GATE_SKIP_PROB: str = os.environ.get(
        "QDRANT_WATCH_GATE_SKIP_PROB", "",
    )

    # ── Jev time route (v0.9.84.1+, plan §5.1 slice) ──
    # A confident date/time question is answered DETERMINISTICALLY from
    # the real get_current_time tool (skip the whole LLM tool loop —
    # <1 s). shadow logs the Jev verdict for a week before enabling;
    # failures always fall back to the LLM (fail-closed). Raw strings;
    # src/agent/agent.py resolves them.
    #   QDRANT_TIME_ROUTE      off (default) | shadow | on
    #   QDRANT_TIME_ROUTE_PROB 0.85 (clamp 0-1, bad→default)
    QDRANT_TIME_ROUTE: str = os.environ.get("QDRANT_TIME_ROUTE", "")
    QDRANT_TIME_ROUTE_PROB: str = os.environ.get(
        "QDRANT_TIME_ROUTE_PROB", "",
    )

    # ── Jev message router v2 (v0.9.84.2+, plan §5.1) ──
    # ONE Jev choice call per turn splits the message into a handler
    # class; confident classes are served WITHOUT the LLM tool loop:
    # time (get_current_time template) / greeting (locale template) /
    # status (teams_assist_status summary) / weather (PRE-FETCH:
    # web_search runs deterministically, results summarized by a
    # ONE-SHOT LLM call — no tool loop). shadow logs the verdict for a
    # week before enabling; failures fall back to the LLM path.
    # Supersedes QDRANT_TIME_ROUTE when set.
    #   QDRANT_ROUTE           off (default) | shadow | on
    #   QDRANT_ROUTE_PROB      0.85 (clamp 0-1, bad→default)
    #   QDRANT_ROUTE_CLASSES   time,greeting,status,weather (default)
    QDRANT_ROUTE: str = os.environ.get("QDRANT_ROUTE", "")
    QDRANT_ROUTE_PROB: str = os.environ.get("QDRANT_ROUTE_PROB", "")
    QDRANT_ROUTE_CLASSES: str = os.environ.get("QDRANT_ROUTE_CLASSES", "")

    # ── Jev dynamic tool-surface (v0.9.98.0, plan §5.2) ──
    # ONE Jev choice BEFORE the LLM tool loop classifies the question
    # into a tool class; the turn's surface = core servers
    # (qdrant/web/datetime/meta/filesystem) + the class's servers —
    # still filtered by MCP_ACTIVE_SERVERS + MCP_SURFACE_MAX_TOOLS and
    # hard-capped at QDRANT_TOOLROUTE_CAP (default 128 = the provider
    # tools-array limit). shadow logs the [ToolRoute] verdict for a
    # week before enabling; ANY failure / low confidence / GUI-picker
    # override keeps the legacy surface (fail-open).
    #   QDRANT_TOOLROUTE        off (default) | shadow | on
    #   QDRANT_TOOLROUTE_PROB   0.6 (clamp 0-1, bad→default)
    #   QDRANT_TOOLROUTE_CAP    128 (0 = surface-cap only)
    #   QDRANT_TOOLROUTE_CLASSES  comma list (default = all mapped)
    QDRANT_TOOLROUTE: str = os.environ.get("QDRANT_TOOLROUTE", "")
    QDRANT_TOOLROUTE_PROB: str = os.environ.get(
        "QDRANT_TOOLROUTE_PROB", "")
    QDRANT_TOOLROUTE_CAP: str = os.environ.get("QDRANT_TOOLROUTE_CAP", "")
    QDRANT_TOOLROUTE_CLASSES: str = os.environ.get(
        "QDRANT_TOOLROUTE_CLASSES", "")

    # ── Jev abandon gate (v0.9.85.0, tool-loop) ──
    # When the tool loop collects CONSECUTIVE empty/error tool results
    # (dead mirror sites; the 2026-09-22 12:49 turn burned all 20
    # iterations this way), ONE jev_noul decision decides whether the
    # model should stop trying variants and answer from what it has.
    # on + p ≥ PROB injects a stop-exploring system note (the model
    # keeps its tools). EVERY failure fail-OPEN (no note, loop runs).
    #   QDRANT_JEV_ABANDON         off (default) | shadow | on
    #   QDRANT_JEV_ABANDON_PROB    0.75 (clamp 0-1, bad→default)
    #   QDRANT_JEV_ABANDON_STREAK  3 consecutive failures (2-10)
    QDRANT_JEV_ABANDON: str = os.environ.get("QDRANT_JEV_ABANDON", "")
    QDRANT_JEV_ABANDON_PROB: str = os.environ.get(
        "QDRANT_JEV_ABANDON_PROB", "",
    )
    QDRANT_JEV_ABANDON_STREAK: str = os.environ.get(
        "QDRANT_JEV_ABANDON_STREAK", "",
    )

    # ── Web tool batching (v0.9.85.0) ──
    #   QDRANT_WEB_FETCH_BATCH_MAX  8 URLs per web_fetch_batch call
    #   QDRANT_STOCK_BATCH_MAX      10 symbols per stock_get_quotes
    # (resolved directly in src/mcp_client/host.py; raw records only.)
    QDRANT_WEB_FETCH_BATCH_MAX: str = os.environ.get(
        "QDRANT_WEB_FETCH_BATCH_MAX", "",
    )
    QDRANT_STOCK_BATCH_MAX: str = os.environ.get(
        "QDRANT_STOCK_BATCH_MAX", "",
    )

    # ── Agentic-RAG slices (v0.9.86.0+; BOTH off by default) ──
    #   ① Query-rewrite gate — QDRANT_REWRITE off|shadow|on
    #     (Retriever.search: ONE jev_noul may route the question
    #     through ONE cheap LLM rewrite before embedding; shadow logs
    #     the would-be rewrite; failures keep the original query)
    #   QDRANT_REWRITE_PROB    0.6 (needs-rewrite threshold)
    #   QDRANT_REWRITE_MODEL   openai/gpt-4o-mini (the rewrite call)
    #   ② Answer-support check — QDRANT_SUPPORT_CHECK off|shadow|on
    #     (RAG answers: ONE jev_noul asks「含未受支持的斷言?」; on +
    #     p ≥ PROB runs ONE no-tools corrective rewrite from the
    #     retrieved context; failures keep the original)
    #   QDRANT_SUPPORT_CHECK_PROB  0.7
    QDRANT_REWRITE: str = os.environ.get("QDRANT_REWRITE", "")
    QDRANT_REWRITE_PROB: str = os.environ.get("QDRANT_REWRITE_PROB", "")
    QDRANT_REWRITE_MODEL: str = os.environ.get("QDRANT_REWRITE_MODEL", "")
    QDRANT_SUPPORT_CHECK: str = os.environ.get("QDRANT_SUPPORT_CHECK", "")
    QDRANT_SUPPORT_CHECK_PROB: str = os.environ.get(
        "QDRANT_SUPPORT_CHECK_PROB", "",
    )

    # ── Jev 分類器（v0.9.93.0; mock-first） ──
    #   JEV_MODE                    mock（預設 — 確定性 hash 挑選，不連網）
    #                               | live（真實 Jev 決策 — 與 rerank 同
    #                               後端，重用 OPENROUTER_API_KEY）
    #   QDRANT_JEV_CLASSIFY_BATCH_MAX  批次 items 上限（預設 50，1-500）
    #   QDRANT_JEV_CLASSIFY_WORKERS    批次並發數（預設 4，1-16）
    #   QDRANT_JEV_CLASSIFY_TIMEOUT_S  單筆 Jev 呼叫逾時（預設 10s，2-60）
    JEV_MODE: str = os.environ.get("JEV_MODE", "")
    QDRANT_JEV_CLASSIFY_BATCH_MAX: str = os.environ.get(
        "QDRANT_JEV_CLASSIFY_BATCH_MAX", "")
    QDRANT_JEV_CLASSIFY_WORKERS: str = os.environ.get(
        "QDRANT_JEV_CLASSIFY_WORKERS", "")
    # ── Jev 分類器（v0.9.93.0; mock-first） ──
    #   JEV_MODE                    mock（預設 — 確定性 hash 挑選，不連網）
    #                               | live（真實 Jev 決策 — 與 rerank 同
    #                               後端，重用 OPENROUTER_API_KEY）
    #   QDRANT_JEV_CLASSIFY_BATCH_MAX  批次 items 上限（預設 50，1-500）
    #   QDRANT_JEV_CLASSIFY_WORKERS    批次並發數（預設 4，1-16）
    #   QDRANT_JEV_CLASSIFY_TIMEOUT_S  單筆 Jev 呼叫逾時（預設 10s，2-60）
    JEV_MODE: str = os.environ.get("JEV_MODE", "")
    QDRANT_JEV_CLASSIFY_BATCH_MAX: str = os.environ.get(
        "QDRANT_JEV_CLASSIFY_BATCH_MAX", "")
    QDRANT_JEV_CLASSIFY_WORKERS: str = os.environ.get(
        "QDRANT_JEV_CLASSIFY_WORKERS", "")
    QDRANT_JEV_CLASSIFY_TIMEOUT_S: str = os.environ.get(
        "QDRANT_JEV_CLASSIFY_TIMEOUT_S", "")

    # ── ImageGen 產圖（v0.9.104.0; mock-first; OpenRouter Image API）──
    #   IMAGEGEN_MODE              mock（預設 — Pillow placeholder，
    #                              不連網零費用）| live（真實產圖 —
    #                              重用 OPENROUTER_API_KEY，每張圖
    #                              收真實費用 usage.cost）
    #   IMAGEGEN_MODEL             預設模型（gemini-2.5-flash-image）
    #   IMAGEGEN_WRITES_ENABLED    live 總開關（預設 off — 與 hicloud/
    #                              keep 寫入閘同款；mock 只需 confirm）
    #   IMAGEGEN_MAX_IMAGES        單次 n 上限（預設 4，1-10）
    #   IMAGEGEN_TIMEOUT_S         單次產圖逾時（預設 120s，30-300）
    #   IMAGEGEN_DIR               write sandbox 內輸出子目錄（預設
    #                              images）
    IMAGEGEN_MODE: str = os.environ.get("IMAGEGEN_MODE", "")
    IMAGEGEN_MODEL: str = os.environ.get("IMAGEGEN_MODEL", "")
    IMAGEGEN_WRITES_ENABLED: str = os.environ.get(
        "IMAGEGEN_WRITES_ENABLED", "")
    IMAGEGEN_MAX_IMAGES: str = os.environ.get("IMAGEGEN_MAX_IMAGES", "")
    IMAGEGEN_TIMEOUT_S: str = os.environ.get("IMAGEGEN_TIMEOUT_S", "")
    IMAGEGEN_DIR: str = os.environ.get("IMAGEGEN_DIR", "")

    # ── 結論 grounding guard（v0.9.94.0；防 fake calling 終線） ──
    #   QDRANT_RESULT_GROUNDING     off（預設）| shadow | on — 每次成功
    #                               派發的工具結果以 uuid 存證；結論時
    #                               確定性快篩（答案含存證特徵詞即通過）
    #                               → 可疑才一次 Jev 驗證 → 未 grounding
    #                               就無工具重寫（僅依存證內容）。
    #   QDRANT_RESULT_GROUNDING_PROB   grounded 機率門檻（預設 0.6）
    QDRANT_RESULT_GROUNDING: str = os.environ.get(
        "QDRANT_RESULT_GROUNDING", "")
    QDRANT_RESULT_GROUNDING_PROB: str = os.environ.get(
        "QDRANT_RESULT_GROUNDING_PROB", "")

    # ── Hybrid search (v0.9.87.0+; off = byte-identical dense-only) ──
    # QDRANT_HYBRID=on creates the named ``bm25`` sparse vector field
    # (server-side IDF) at collection-create time, stores a keyword
    # leg at ingest, and fuses dense+sparse with RRF at search time.
    # Old corpora migrate via `cli.py backfill-sparse` after a
    # recreate. Search-time any failure degrades to dense-only.
    QDRANT_HYBRID: str = os.environ.get("QDRANT_HYBRID", "")

    # ── Embedding watchdog + hash-pollution control (v0.9.112.0+) ──
    # The embed chain's last resort is the non-semantic hash backend;
    # a degraded llama.cpp ZOMBIE (process alive, /v1/models 200,
    # every embeddings POST failing) previously passed the availability
    # probe and stayed broken for hours. The watchdog makes a real
    # embeddings POST every interval, and after N consecutive failures
    # it kills the llama-server PID bound to the embed port and
    # relaunches the start bat (v0.9.109.x auto-heal family).
    #   QDRANT_LLAMACPP_WATCHDOG           on — start at gui boot (off
    #                                      = no thread, status still
    #                                      answers)
    #   QDRANT_LLAMACPP_WATCHDOG_INTERVAL  seconds between probes
    #                                      (default 1800)
    #   QDRANT_LLAMACPP_START_BAT          launcher path override
    #                                      (default
    #                                      C:\llama.cpp-vulkan\
    #                                      start-llamacpp-8b.bat, then
    #                                      C:\llama.cpp\)
    #   QDRANT_LLAMACPP_WATCHDOG_REFUSED_HEAL on (default) — a TCP-
    #                                      REFUSED probe failure =
    #                                      the UNAMBIGUOUS dead state
    #                                      (a loading model answers
    #                                      HTTP 503, never refused)
    #                                      → heals immediately;
    #                                      off = the legacy 2-streak
    #                                      wait for every failure
    #                                      kind (v0.9.115.1).
    #   QDRANT_EMBED_FAIL_CLOSED           off — default tag-and-
    #                                      continue; ON makes ingest
    #                                      REFUSE to write batches
    #                                      that fell back to hash
    #                                      (file skipped with a clear
    #                                      error instead of silently
    #                                      polluting the collection).
    # Audit/repair: `cli.py audit-backends` / `reembed-hash` or the
    # Admin-tab Embedding card buttons.
    QDRANT_LLAMACPP_WATCHDOG: str = os.environ.get(
        "QDRANT_LLAMACPP_WATCHDOG", "")
    QDRANT_LLAMACPP_WATCHDOG_INTERVAL: str = os.environ.get(
        "QDRANT_LLAMACPP_WATCHDOG_INTERVAL", "")
    QDRANT_LLAMACPP_START_BAT: str = os.environ.get(
        "QDRANT_LLAMACPP_START_BAT", "")
    QDRANT_LLAMACPP_WATCHDOG_REFUSED_HEAL: str = os.environ.get(
        "QDRANT_LLAMACPP_WATCHDOG_REFUSED_HEAL", "")
    QDRANT_EMBED_FAIL_CLOSED: str = os.environ.get(
        "QDRANT_EMBED_FAIL_CLOSED", "")

    # ── STAIR heading metadata (v0.9.113.0, ALL opt-in) ──
    #   QDRANT_HEADING_METADATA  off (default — byte-identical legacy
    #                            ingest) | on — ingest goes heading-
    #                            aware: DOCX Heading styles become ATX
    #                            ``#`` lines, chunking splits on heading
    #                            sections and every payload carries
    #                            ``heading_path`` ("H1 > H2" stack).
    #                            ON changes chunk boundaries → the
    #                            next ingest re-embeds affected files
    #                            once. Existing points: fill with
    #                            `cli.py backfill-headings` (metadata-
    #                            only, no re-embed) or the Admin-tab
    #                            Embedding card backfill button.
    #   QDRANT_HEADING_BOOST     search-side float bonus (default 0 =
    #                            off): hits whose heading_path overlaps
    #                            query tokens get cosine + bonus and
    #                            the list re-sorts (pre-rerank; enabling
    #                            QDRANT_RERANK supersedes it with the
    #                            more precise Jev ordering).
    QDRANT_HEADING_METADATA: str = os.environ.get(
        "QDRANT_HEADING_METADATA", "")
    QDRANT_HEADING_BOOST: str = os.environ.get(
        "QDRANT_HEADING_BOOST", "")

    # ── Media multimodal side-channel (v0.9.114.0, EmbeddingGemma 2) ──
    #   Rule (user): TEXT keeps the Qwen3 primary path; MEDIA (document
    #   media + standalone media files) uses EmbeddingGemma 2's shared
    #   768-dim text+image+audio space on its own llama.cpp server
    #   (unsloth build with --mmproj, default port 8081) into a separate
    #   side collection — cross-modal retrieval = text query → ggema
    #   text tower → media collection.
    #   ⚠ FAIL-CLOSED: media embeddings are verified UPSTREAM since
    #   llama.cpp #30054 (2026-10-06, build b11457+) — but ONLY via the
    #   NESTED content-parts request shape (ggml-org#30082 was RETRACTED
    #   as a shape mis-report). Legacy shapes (internal /embedding +
    #   image_data; shallow image_url input) can still 200 with a
    #   TEXT-ONLY marker vector, cos≈1.0 between different images.
    #   MediaEmbed embeds two reference images via the nested shape and
    #   refuses to index while cos > QDRANT_MEDIA_FAKE_COS (0.999) —
    #   this also blocks pre-#30054 server builds until the binary is
    #   upgraded — UPGRADED THROUGH (2026-10-13): the gemma2 server now
    #   runs unsloth b11541-mix-2500203 (previous b11443-mix kept in
    #   C:\llama.cpp-un\_old_b11443\) and the probe returns REAL
    #   (cos=0.8795); audio rides the nested input_audio part, also
    #   live-verified; the description text path (DESCRIBE_IMAGES →
    #   Qwen3) keeps working meanwhile; verdict cached with a cooldown.
    #   QDRANT_MEDIA_EMBEDDING   off (default) | on — media side-car on
    #                            document-image ingest (pipeline hook)
    #   QDRANT_MEDIA_INGEST      also ingest STANDALONE media files
    #                            (png/jpg/webp/gif/bmp/tiff) standalone
    #   QDRANT_MEDIA_COLLECTION  side collection (media_docs_gemma2)
    #   GEMMA2_EMBED_URL         ggema server URL
    #                            (http://127.0.0.1:8081/v1/embeddings)
    QDRANT_MEDIA_EMBEDDING: str = os.environ.get(
        "QDRANT_MEDIA_EMBEDDING", "")
    QDRANT_MEDIA_INGEST: str = os.environ.get(
        "QDRANT_MEDIA_INGEST", "")
    QDRANT_MEDIA_COLLECTION: str = os.environ.get(
        "QDRANT_MEDIA_COLLECTION", "")
    GEMMA2_EMBED_URL: str = os.environ.get("GEMMA2_EMBED_URL", "")

    # ── Google Keep 記事本 (v0.9.88.0+; mock-first) ──
    #   KEEP_MODE               mock (default — simulated note book,
    #                           NO network) | live (official Keep API
    #                           v1 REST via keep.googleapis.com)
    #   KEEP_WRITES_ENABLED     live writes master switch (default
    #                           off; create/delete also need
    #                           per-call confirm=true)
    #   KEEP_CLIENT_SECRET_PATH OAuth client secret JSON (desktop
    #                           type, from Google Cloud Console)
    #   KEEP_TOKEN_PATH         token.json (default
    #                           $QDRANT_RAG_HOME/google_keep/token.json;
    #                           written by `python cli.py keep-auth`)
    KEEP_MODE: str = os.environ.get("KEEP_MODE", "")
    KEEP_WRITES_ENABLED: str = os.environ.get("KEEP_WRITES_ENABLED", "")
    KEEP_CLIENT_SECRET_PATH: str = os.environ.get(
        "KEEP_CLIENT_SECRET_PATH", "",
    )
    KEEP_TOKEN_PATH: str = os.environ.get("KEEP_TOKEN_PATH", "")

    # ── Chat context compression (v0.9.10.0+, ALL opt-in) ──
    # Off by default ("off") so chat behaviour is byte-identical to
    # v0.9.9.4 until the user opts in. Raw strings —
    # src/agent/context_compress.py::ContextCompressor parses them
    # with safe fallbacks (bad values never crash the import).
    #   CHAT_CONTEXT_COMPRESSION      off | auto | aggressive
    #     off  = no compression (v0.9.9.4 behaviour)
    #     auto = summarize old history when over the trigger +
    #            offload oversized tool results + squeeze oversized
    #            single messages (code/JSON stay verbatim)
    #     aggressive = same, but the trigger and the per-message
    #            budget are halved
    #   CHAT_COMPRESS_TRIGGER_MESSAGES    history over this folds the
    #                             older tail into a summary (24)
    #   CHAT_COMPRESS_KEEP_MESSAGES       recent messages kept
    #                             verbatim after folding (12)
    #   CHAT_COMPRESS_SUMMARY_MODE        llm | extractive (llm falls
    #                             back to extractive on any failure)
    #   CHAT_COMPRESS_SUMMARY_MAX_CHARS   summary budget (900)
    #   CHAT_COMPRESS_MSG_MAX_CHARS       per-message prose-squeeze
    #                             budget; code/JSON stay verbatim (4000)
    #   CHAT_OFFLOAD_THRESHOLD_CHARS      tool results over this are
    #                             written to a file, pointer inline
    #                             (20000)
    #   QDRANT_OFFLOAD_DIR                explicit offload dir; empty
    #                             = <filesystem read sandbox>/chat_offloads
    CHAT_CONTEXT_COMPRESSION: str = os.environ.get(
        "CHAT_CONTEXT_COMPRESSION", "off"
    )
    CHAT_COMPRESS_TRIGGER_MESSAGES: str = os.environ.get(
        "CHAT_COMPRESS_TRIGGER_MESSAGES", "24"
    )
    CHAT_COMPRESS_KEEP_MESSAGES: str = os.environ.get(
        "CHAT_COMPRESS_KEEP_MESSAGES", "12"
    )
    CHAT_COMPRESS_SUMMARY_MODE: str = os.environ.get(
        "CHAT_COMPRESS_SUMMARY_MODE", "llm"
    )
    CHAT_COMPRESS_SUMMARY_MAX_CHARS: str = os.environ.get(
        "CHAT_COMPRESS_SUMMARY_MAX_CHARS", "900"
    )
    CHAT_COMPRESS_MSG_MAX_CHARS: str = os.environ.get(
        "CHAT_COMPRESS_MSG_MAX_CHARS", "4000"
    )
    CHAT_OFFLOAD_THRESHOLD_CHARS: str = os.environ.get(
        "CHAT_OFFLOAD_THRESHOLD_CHARS", "20000"
    )
    QDRANT_OFFLOAD_DIR: str = os.environ.get("QDRANT_OFFLOAD_DIR", "")

    # ── SiliconFlow (v0.8.9+, Chinese-friendly) ──
    # OpenAI-compatible. Free tier generous; production very
    # competitive. Popular models:
    #   Qwen/Qwen2.5-7B-Instruct (fast, free)
    #   Qwen/Qwen2.5-72B-Instruct (default; strong Chinese+English)
    #   deepseek-ai/DeepSeek-V2.5 (strong reasoning)
    #   THUDM/glm-4-9b-chat (fast)
    #   Pro/Qwen/Qwen2.5-VL-72B-Instruct (vision)
    SILICONFLOW_API_KEY: str = os.environ.get("SILICONFLOW_API_KEY", "")
    SILICONFLOW_BASE_URL: str = os.environ.get(
        "SILICONFLOW_BASE_URL", "https://api.siliconflow.cn/v1"
    )
    SILICONFLOW_CHAT_MODEL: str = os.environ.get(
        "SILICONFLOW_CHAT_MODEL", "Qwen/Qwen2.5-72B-Instruct"
    )
    SILICONFLOW_VISION_MODEL: str = os.environ.get(
        "SILICONFLOW_VISION_MODEL", "Pro/Qwen/Qwen2.5-VL-72B-Instruct"
    )

    # ── LLM provider auto-detect / explicit override (v0.8.9+) ──
    # If set, the Agent uses this provider regardless of which
    # API keys are populated. If empty, the Agent falls back to
    # the auto-detect order: ollama -> openrouter -> siliconflow
    # -> openai. The first non-empty *_API_KEY wins.
    LLM_PROVIDER: str = os.environ.get("LLM_PROVIDER", "").strip().lower()

    # ── Provider chain order (v0.9.18.0+) ──
    # chat 主鏈與 vision OCR 鏈是兩個獨立的應用需求,各自可在 .env
    # 調整 provider 優先序(逗號分隔、不分大小寫;空白/未設 = 預設
    # 順序,行為不變):
    #   LLM_PROVIDER_CHAIN    → Agent chat primary 選擇 + 跨供應商
    #                           failover 順序
    #                           (預設 ollama,openrouter,siliconflow,openai)
    #   VISION_PROVIDER_CHAIN → ImageDescriber OCR 鏈順序
    #                           (預設 ollama,openrouter,siliconflow,openai;
    #                           別名 ollama-cloud / openai-compat 也可)
    # 未列出的供應商以預設相對順序接在後面(鏈定義優先序,不是剔除
    # — 要停用某家就把它的 key 留空);無效 token skip + 警告。
    # Raw strings — Agent._resolved_provider_order() /
    # ImageDescriber._chain_names() 直接讀 os.environ 解析(測試可
    # monkeypatch,不需 reload config)。
    LLM_PROVIDER_CHAIN: str = os.environ.get("LLM_PROVIDER_CHAIN", "")
    VISION_PROVIDER_CHAIN: str = os.environ.get("VISION_PROVIDER_CHAIN", "")

    # ── Ingest (flat) ──
    WATCH_DIR: str = os.environ.get("WATCH_DIR", "./documents")
    CHUNK_SIZE: int = int(os.environ.get("CHUNK_SIZE", "500"))
    CHUNK_OVERLAP: int = int(os.environ.get("CHUNK_OVERLAP", "50"))
    DESCRIBE_IMAGES: bool = os.environ.get("DESCRIBE_IMAGES", "true").lower() == "true"

    # ── Namespaced groups (new in v0.2.3) ──
    ingest: _IngestNamespace = _IngestNamespace()
    llm: _LLMNamespace = _LLMNamespace()
    embedding: _EmbeddingNamespace = _EmbeddingNamespace()

    def __repr__(self) -> str:
        # Hide API keys.
        d = self.__dict__.copy()
        for k in (
        "OPENAI_API_KEY", "QDRANT_API_KEY", "OLLAMA_API_KEY",
        "OPENROUTER_API_KEY", "SILICONFLOW_API_KEY",
        "OPENAI_COMPAT_API_KEY",
    ):
            if d.get(k):
                d[k] = "***"
        return f"Config({d})"

    # ── hot reload (v0.9.42.0+) ──────────────────────────────────────
    #
    # Class-level attrs are evaluated ONCE at import. reload() re-reads
    # .env (dotenv override=True) and re-assigns every env-backed attr
    # by its SAME-NAME env var, with typed converters for the handful
    # of attrs whose import-time evaluation does more than str().
    # Namespaced groups (ingest/llm/embedding) are live env properties
    # — always current, no reload needed.
    _RELOAD_TYPED: dict[str, Any] = {
        "QDRANT_URL": _normalize_loopback_url,
        "EMBEDDING_DIM": int,
        "CHUNK_SIZE": int,
        "CHUNK_OVERLAP": int,
        "LLM_PROVIDER": lambda v: (v or "").strip().lower(),
        "DESCRIBE_IMAGES": lambda v: (v or "").strip().lower()
        in ("1", "true", "yes", "on"),
    }
    _SKIP_ATTRS = ("ingest", "llm", "embedding")

    def reload(self, path: str | Path | None = None) -> list[str]:
        """Re-read the .env file and re-assign every env-backed attr.

        Returns the attribute names whose value CHANGED. Rules:
          * dotenv ``override=True`` — the file wins over the process
            env for keys present in it (same as a fresh boot).
          * A key REMOVED from the file keeps its stale process-env
            value (dotenv never clears) — removal requires a restart;
            document this to the user.
          * A value that fails the typed converter is SKIPPED with a
            warning (a bad edit must not take the chat down).
        ``path`` defaults to the project-root ``.env`` (the config
        watcher passes the same default; tests pass a temp file).
        """
        env_file = (Path(path) if path
                    else Path(__file__).resolve().parents[1] / ".env")
        load_dotenv(env_file, override=True)
        changed: list[str] = []
        cls = type(self)
        for name, value in list(vars(cls).items()):
            if name.startswith("_") or name in self._SKIP_ATTRS:
                continue
            if callable(value) or isinstance(
                value, (property, classmethod, staticmethod),
            ):
                continue
            if name not in os.environ:
                # env var unset — keep the import-time default
                continue
            converter = self._RELOAD_TYPED.get(name, str)
            try:
                new = converter(os.environ[name])
            except (TypeError, ValueError) as e:
                logger.warning(
                    "[config] reload: bad value for %s (%r) — kept "
                    "%r (%r)",
                    name, os.environ[name],
                    getattr(cls, name, None), e,
                )
                continue
            old = getattr(cls, name, None)
            if old != new:
                changed.append(name)
            setattr(cls, name, new)
        return changed


config = Config()
