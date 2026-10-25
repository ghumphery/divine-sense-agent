# divine-sense-agent — Technical Design Guide

**Version:** v0.9.124.0（workspace baseline v0.8.9 — 5-Provider LLM dispatch,
chain-orderable since v0.9.18.0; **v0.9.71 → v0.9.94 catch-up:**
Teams UIA/白名單/每聊天 session/動態設定（v0.9.70-79）＋ hybrid
search BM25 融合 + rerank/查詢改寫/答案支援檢查（v0.9.83-87）＋
Google Keep 筆記本 MCP → 自建本地筆記本（v0.9.88-90）＋ 行事曆
動作完整性 guard + [TurnTiming] 延遲改進（v0.9.91-92）＋ Jev
批次分類器 MCP（v0.9.93）＋ UUID 存證 grounding guard（v0.9.94）；
**v0.9.95 → v0.9.98 catch-up:** 筆記本 Web/手機 UI（v0.9.95）＋
專案更名 Divine Sense Agent（v0.9.96.0）＋ 402 免重試 failover
（v0.9.96.1）＋ 啟動器 CRLF + splash cache-bust/ETag（v0.9.96.2,
97.1）＋ Notes UI 橋接形狀（v0.9.96.3）＋ 🎨 三態主題選擇器
（v0.9.97.0）＋ stock-local 表面重掛（v0.9.97.2）＋ **Jev 動態
工具表面 QDRANT_TOOLROUTE**（v0.9.98.0，回合表面 = 核心群 +
中選類別群，恆 ≤128 硬頂）＋ live kline compact（-97% tokens，
v0.9.98.1）＋；
**v0.9.32 → v0.9.61.1 catch-up:** task_runner 平行多工（v0.9.45-47）＋
`MAX_TOOL_ITERATIONS=20` + GUI web 模式（v0.9.15）＋ provider chain /
openai-compat（v0.9.18-19）＋ chat history / webhook / stock / memory MCP
（v0.9.21-25, 59）＋ runtime hot-reload（v0.9.42）＋ ONVIF 相機（v0.9.37-44）
＋ Telegram 照片可靠性/偽造偵測（v0.9.57.0-57.1）＋ Telegram 雙向語音
（OpenRouter STT/TTS, v0.9.58.0）＋ Qdrant 長期記憶 + deterministic 引用
（v0.9.59.0-59.2）＋ Admin 全 collections 統計/刪除（v0.9.59.1）＋
GUI Search/Stats 跨 collection + 百萬級分頁資料管理（v0.9.60.0-60.4）＋
掃描式 PDF OCR 攝入歸檔（v0.9.61.0, `src/ingest/pdf_ocr.py`）＋
全域前端錯誤 toast 顯示（v0.9.61.1）＋ Telegram 檔案上傳
`send_telegram_file`（v0.9.62.0, sendDocument ≤50 MB）＋ Telegram
入站檔案接收管線（v0.9.63.0, getFile → WRITE 沙箱 → LLM）＋
**v0.9.64.0 = hicloud HWS CaaS/CVPC 雲服務 MCP 擴充**（中華電信 —
`HicloudLocalHost` server `hicloud-local` 8 工具 + 官方簽章逐字鎖定 +
mock-first 兩道閘 + stdio mirror）＋ **v0.9.65.0 = chat web 檔案下載**
（`GET /download` 沙箱串流 + read_sandbox_image 同款解析 + ⬇ chip
+ GUI 另存新檔）＋ **v0.9.66.0 = hicloud HWS 完整服務面補完**
（8 → 56 工具，+48 資料驅動：VM 補完 / CloudWatch / listLog / EBS /
Image 補完 / SLB / Subnet / NetworkAcl / VPC / IGW+IP / Intranet /
NAT+PortFwd；_RES_SPECS 資料驅動 CRUD + mock 資源簿；寫入一律
confirm 閘）＋ **v0.9.66.1 = Embedder local llama.cpp 嵌入長度護欄**
（排程長回答 1240 字 ≈ 1201 tokens 超過 n_ctx=1024 → 400 →
cooldown 根因修復；QDRANT_EMBED_LOCAL_MAX_CHARS 預設 950 字）＋
**v0.9.67.0 = Telegram MarkdownV2 + 一般 Markdown 轉換器**
（新 `src/telegram_md.py`：佔位符策略 — 圍欄/行內程式碼、連結、
表格轉 pre、標題粗體、`•` 項目、任務清單 ✅/☐、引用、水平線、
強調轉換 + 全跳脫 + 反向嵌套還原；send_telegram_markdown 與
daemon 回覆接線 + 純文字 fallback）＋
**v0.9.67.1/67.2 = 排程 fire 上下文硬化**（v0.9.67.1：fire 獨立
上下文 `QDRANT_SCHEDULE_FIRE_HISTORY` 預設 0 + Telegram 發送完整
性守衛 — 宣稱已發送但零 dispatch → 糾正回合強制呼叫工具；
v0.9.67.2：守衛 dispatch 形狀修復 — `last_tool_dispatches` 是
`list[str]` 字串優先解析（誤判曾導致重複發送）＋ fire 預設不召回
長期記憶 `QDRANT_SCHEDULE_FIRE_MEMORY`（fire 的 web_search 結果
被存成記憶後召回會注入舊股價）+ `chat_turn(use_memory=)` 開關 +
記憶 block 歷史快照硬規則）＋ **v0.9.68.0 = Web/GUI 聊天語音雙向**
（`gui/api.py` 新 `voice_stt`（🎤 MediaRecorder blob → STT，辨識
文字入輸入框）與 `tts_speak`（每個 assistant 氣泡 🔊 → TTS →
data-URL 播放，單一音訊 + LRU 快取）；SpeechClient 嗅探 webm/mp4
容器 + `to_browser_audio`；mic 需 localhost/HTTPS）＋
**v0.9.69.0 = Teams 桌面自動化代答助理**（零 Microsoft 權限 —
操作使用者自己登入的 Teams 桌面客戶端經 windows-mcp fleet；
`TeamsAssistLocalHost` server `teams-assist` 7 工具 + 截圖 hash
watch 執行緒住在 host 內 + `make_teams_assist_fire` + 反捏造
guard + fail-closed 白名單 + write gate + stdio mirror）＋
**v0.9.70.0 = UIA 零干擾通道**（spike 實證：輸入框 `new-message-`
支援 ValuePattern、傳送按鈕支援 InvokePattern、聊天 Document
TextPattern 可直讀；`scripts/uia_teams_ops.ps1` read|send 安全鏈；
`QDRANT_TEAMS_ASSIST_SEND_MODE=uia` + `teams_assist_send_uia` /
`teams_assist_read_uia`，工具面 7 → 9）。細節見
`README.md` 更新日誌）
**v0.9.99 → v0.9.104.6 catch-up:** 前端 tab 釘選/外觀字型 UI
（v0.9.99.x）＋ exec-local 工具回補 + script-library（v0.9.102.x）＋
Telegram session 上下文紀律（v0.9.102.0）＋ 排程/keep/jev/keep-server
README 對齊 ＋ session 標題重新命名 `SessionManager.rename`（v0.9.103.5）＋
Telegram 發送完整性守衛未來式形態（v0.9.103.2）＋ **IMAGEGEN 產圖
MCP**（v0.9.104.0, `src/imagegen_tools.py` OpenRouter Image API,
mock-first 兩道閘, server `imagegen-local` 4 工具）＋ **ToolRoute
上下文**（v0.9.104.1：接續型回覆「ok/執行」也 union 上輪宣告的
server；`_prev_assistant_reply` + `_toolroute_prev_reply_servers`
mining, union-only）＋ **firefox_craw MCP 上線**（v0.9.104.2：
MCP_ACTIVE_SERVERS + web-external class + browse hint, x.com /
needs-JS 頁面改走 `get_page_html`）＋ **TOOL-RESULT 爆炸防呆**
（v0.9.104.3：Gmail 全頁 HTML 1.75M-token 事故 —
`CHAT_CONTEXT_COMPRESSION=on` 靜默失效修復（on/yes/true/1 = auto
別名）+ 絕對硬閘 `QDRANT_TOOL_RESULT_MAX_CHARS` 51,200 字截斷）＋
**exec-local 升格基礎 MCP**（v0.9.104.4：`_TOOLROUTE_CORE_SERVERS`
+= exec-local，run_python 等每輪在面）＋ **describe_image token
稽核**（v0.9.104.5：`raw_image_base64` 不再回傳（58k tokens/次純
浪費）+ SHA-1 cache 升 process 層級 `_SHARED_CACHE`（host 每次
`ImageDescriber()` 重建導致永不命中）+ 截圖驗證紀律 prompt 區塊）＋
**使用者可匯入 system rules**（v0.9.104.6：`src/agent/system_rules.py`
— read sandbox `system_rules.md` 或 `QDRANT_SYSTEM_RULES_FILE` 自動
附加成 `USER SYSTEM RULES` 區塊，mtime 熱更新、8000 字截斷保護）。
**v0.9.105 → v0.9.110 catch-up:「Divine Sense」品牌與執行紀律
強化段：exec-local OS REAL-input 六工具（v0.9.105.x）＋
app.log 每日輪替 7 天（v0.9.106.1）＋ 背景 turn 三防線
（v0.9.107 read_file offload 豁免 / 工具呼叫看門狗 / _fs_walk
排除 .cache 類；v0.9.107.1 turn 硬超時）＋ runtime limits
自查 / task 併發上限 / 完成-進度 Telegram 通知（v0.9.108.0）＋
**Skill/Workflow/戰術/策略 編譯器**（v0.9.109.x：`src/agent/
skill_compiler.py` compile→wave→run→verify/notify，server
        `tacti-local` 六工具 + stdio 鏡像；SKILL_GUIDE.md；
USER_MANUAL.md 全功能說明書）＋ **技能檔案庫**（v0.9.116.0：
`src/skill_library.py` + `SkillLibraryLocalHost`（server
`skill-library-local` 六工具 skill_import/list/get/search/
remove/use — Claude SKILL.md 規範文件包匯入/查詢/刪除/引用；
skill_use 內文 inline 進回合；cli `skills`；`_skill_meta.json`
provenance；QDRANT_SKILLS_DIR / QDRANT_SKILL_LIB_* env）＋
**開機自癒**（v0.9.109.3
Qdrant readyz 探針代起 / v0.9.109.4 firefox_craw 中介
service_server.exe `/health` 探針代起；`_ensure_qdrant_server`
`_ensure_firefox_service` 掛 prewarm）＋ **task/stage 層級
LLM 模型 pin**（v0.9.110.0：`src/agent/task_llm.py` 真實目錄
驗證、限 openrouter(466)+ollama(17) 兩家、fail-closed 不跨
provider fallback；`run_task_once(llm=…)` /
`compile_plan(default_llm=…, stages[*].llm=…)` /
`list_task_models` 發現工具；+24 tests；LIVE E2E pin 三部曲
全綠）＋ **v0.9.112.0 = 嵌入韌性雙缺口修復**（llama.cpp 嵌入
**看門狗** `src/embed_watchdog.py` — 第三個 auto-heal 且唯一
「程序活著也會修」的：真實 embeddings POST 探針抓殭屍狀態
（/v1/models 200 但 POST 全 500 的 ErrorDeviceLost 態）、streak 2
→ netstat 解析埠 PID → taskkill → detached 重啟 bat（冷卻 600s；
只信 HTTP 不信延遲以免誤殺大 batch 健康伺服器；prewarm 接線 +
`get_runtime_status` 的 `embedding_watchdog` 區段）＋ **hash 向量
污染防護** `src/ingest/reembed.py` — pipeline payload 新
`embedding_backend` 標記（`Embedder.last_backends`）＋
`QDRANT_EMBED_FAIL_CLOSED=on` 讓 hash 降級改 fail-closed（預設 off
= 行為同舊版）＋ 審計（重算 hash 向量精確比對 — 無標記的舊點也
抓得到）與重嵌（`Embedder.embed_fresh` 繞兩層快取；still-hash 不
upsert）雙暴露面 GUI Admin 按鈕 + `cli.py audit-backends` /
`reembed-hash`；README 記 roadmap 評估存查）。
**v0.9.124 → v0.9.127 自我提升閉環補完**：Agent Rulebook（124.0，
Memento 3 工件 + Evo² 證據門 + snapshot ring，`agent_rules.md` 上
prompt、prediction-error hint、rule-lesson sink）於 127.0 升級為
rulebook-local **7 工具**（+ToolRoute 觸發列增補
`toolroute_override_add`/`toolroute_override_list`，
新模組 `agent/toolroute_overrides.py`——`agent_toolroute.md` evidence
門 + 快照 ring，`QDRANT_TOOLROUTE_OVERRIDES`=shadow/on/off，
shadow 預設只記 log）＋ **規則療效度控**（每次工具失敗 → 全部生效中
規則 `errors++`+`last_error`，繞快照 ring，rollback 決策量化）＋
**成功 plan→SKILL.md 固化**（done 且 ≥3 stages 自動草擬至
`skill_drafts/`，永不自動入庫）＋ **記憶維護**
（`MemoryStore.cleanup_candidates` DRY-RUN + memory-local 第 6 工具
`suggest_memory_cleanup` + opt-in 每週維護排程）＋ **驗證者異源**
（verification 糾正回合 alt-pin 到 task_llm 可 pin 的另一 provider；
`guard_agent` 貫通回執計入 `_verified_flag`）。
**Audience:** Future maintainers and contributors (including past-you, six
months from now, who has forgotten everything).
**Goal:** Explain *why* every module exists, *what* its public surface is,
and *how* to safely extend / debug it. Read this before touching code.

This document is **deliberately not a user manual** — see `README.md` for
install / CLI usage / GUI tour. This file is about *internals*.

> **TL;DR for the impatient.** Data flows in only one direction:
> `loader → (image_describer / pdf_ocr OCR) → chunker → embedder
> (3-tier cache) → Qdrant`
> (document RAG) — plus a second pipeline for log analysis
> (`log parser → embedder → log_docs collection → rule engine + outliers`).
> The Agent's tool surface is assembled in-process by `McpManager.from_config`
> (Qdrant / WebSearch / Filesystem / Scheduler / Todo / OpenWith /
> LogAnalysis / Archive / Exec / DateTime / ChatHistory / Memory /
> Task / MegaStock / Hicloud / Webhook / Onvif / Cred / NetScan /
> Runtime / Teams-assist / Meta LocalHosts — zero round-trip), and the
> same hosts are exposed as stdio servers under `src/mcp_server/` for
> external clients (Claude Desktop, Cline…). Entry points: `cli.py`
> (terminal), `gui/api.py` (PyWebView), `src/mcp_server/*` (MCP stdio).
> They all share `src/`, never import each other. Cache hits matter —
> see §4.

---

## Table of Contents

1. [System Overview](#1-system-overview)
2. [Module Map & Public Surfaces](#2-module-map--public-surfaces)
3. [Data Flow Walk-through](#3-data-flow-walk-through)
4. [Cache Topology (in-process + SQLite)](#4-cache-topology)
5. [Config, Env, and Naming Conventions](#5-config-env-and-naming-conventions)
6. [Adding a New Feature](#6-adding-a-new-feature)
7. [Adding a New Loader / Format](#7-adding-a-new-loader-format)
8. [Adding a New Embedding Backend](#8-adding-a-new-embedding-backend)
9. [The Three Entry Points (CLI / GUI / MCP)](#9-the-three-entry-points)
10. [Logging, Errors, and Graceful Degradation](#10-logging-errors-and-graceful-degradation)
11. [Debugging Checklist](#11-debugging-checklist)
12. [Platform Quirks (Windows / Vulkan / AMD)](#12-platform-quirks)
13. [Sibling Sync: `milvus-mcp-agent` Parallel](#13-sibling-sync-milvus-mcp-agent-parallel)
A. [File Inventory](#appendix-a--file-inventory)
B. [Versioning & Release Process](#appendix-b--versioning--release-process)

---

## 1. System Overview

### 1.1 What this thing does

A **RAG (retrieval-augmented generation) agent** that lets a local LLM
answer questions grounded in *your own files* (PDFs, DOCX, EPUB, PPTX,
plain text, code).

```
             +----------------------------+
  user  ----> |  CLI / GUI / MCP client   |   three entry points
             +----------------------------+
                         |
                  +-------v--------+
                  |  src/ core     |   one shared library
                  |                |
                  | ingest -> vec  |   <-- store, embed, search
                  | store          |
                  | agent          |   <-- RAG orchestration
                  | tools/web      |   <-- augmentation
                  +----------------+
                         |
              +----------+----------+
              v                     v
     +----------------+     +------------------+
     |  Qdrant HTTP   |     | LLM provider     |
     |  (vector DB)   |     | 5-provider chain |
     +----------------+     +------------------+
```

Three principles shape every design decision:

1. **One shared core, three thin entry points.** `src/` has *zero*
   awareness of CLI/GUI/MCP. That makes new entry points cheap (REST,
   Gradio, etc.) and refactors safe.
2. **Every external call can fail and that's OK.** Qdrant down, LLM
   rate-limited, web search 401 — the Agent prints and returns what
   it has. The CLI/GUI surface errors as `{"error": "..."}` dicts;
   the MCP server wraps them in `TextContent`. Nothing crashes the
   whole REPL or window.
3. **Caches are explicit, not silent.** Embedder selection has an
   `auto` probe chain, but no silent fallback when a backend is set;
   a down llama.cpp server will *fail the API call*, not silently
   produce hash-fallback vectors.

### 1.2 Historical arc (helps you read the code)

```
v0.1.x  Single file. Milvus-Lite, Basic embeddings.
v0.2.x  Qdrant port (this repo). llama.cpp local embedder.
v0.3.x  EPUB loader, ingest pipeline rewrite.
v0.4.0  Stripped all client-side MCP (kept only as server).
v0.5.0  Added PyWebView GUI, multi-language, packaging.
v0.6.0  Persistent SQLite embedding cache, GUI cache admin.
v0.6.1  GUI Stats tab fix (group-by API + TTL cache).
v0.7.0  Per-document removal (CLI + GUI; matches by basename, not path).
v0.8.0  Tool-calling loop + MCP host/client (stdio + streamable_http
       + legacy SSE) for third-party MCP servers.
v0.8.1  Second stdio MCP server (Telegram Bot gateway) with allowlist
       fail-closed + zero-dep stdlib urllib transport.
v0.8.2  GUI MCP editor: per-server test button + raw JSON editor +
       tool overview panel. MetaLocalHost (list_mcp_tools) becomes
       an Agent-callable tool. Log noise fix + tool-failure prompt.
v0.8.3  Full GFM Markdown rendering — CLI zero-dep regex engine
       (numbered/nested/task lists, italic, strikethrough, blockquote,
       hr, links, images, escaping) + GUI DOMPurify allow-list
       expansion (input/del/hr/img/figure) + hardenLinks() target=_blank
       rel=noopener.
v0.8.4  GUI markdown vendor fallback (gui/assets/vendor/ pinned to
       marked@12.0.2 + dompurify@3.0.11, CDN as secondary) + duck-type
       DOMPurify presence check (was `typeof === 'object'`, broken
       since DOMPurify 3 exposes itself as a named function).
v0.8.5  LLM response language plumbing. Agent.ask() / chat_turn()
       accept a `response_language` parameter (zh-TW / zh-CN / en /
       ja). _system_prompt() appends a LANGUAGE DIRECTIVE block —
       with a carve-out for filenames / library names / code
       identifiers so citation integrity isn't broken. GUI app.js
       forwards currentLang; CLI gets --lang on ask / chat + a
       /lang [code|auto] REPL command. Default None preserves
       v0.8.4 behaviour.
v0.8.6  Scheduler MCP — ScheduleManager wrapped as 8 MCP tools
        (add / delete / list / get / enable / disable / run_now /
        get_errors), exposed both in-process (`SchedulerLocalHost`
        auto-registered by `McpManager.from_config`) and as a
        stdio MCP server (`src/mcp_server/scheduler_server.py`).
        Schedule dataclass gains `last_error` / `last_error_at`
        (backward-compatible with v0.8.5 JSON via dataclass
        field introspection); Scheduler.tick_once now persists
        on_fire exceptions instead of swallowing them so the
        LLM can answer "為什麼 X 失敗了". `_system_prompt
        (tools_enabled=True)` adds a Schedule-management
        trigger block. 81 new tests
        (test_scheduler_server + test_scheduler_local_host +
        test_scheduler_v086 + test_system_prompt_scheduler).
v0.8.6.1 LocalTool argument-coercion fix. `_coerce_arguments`
        pure helper normalises `None` / bare JSON string / wrapper
        dicts (`{"args": ...}` / `{"kwargs": ...}` / `{"params": ...}`
        etc.) / single-element list into a flat dict. `_call_args`
        introspects the handler signature so `(self, args: dict)`
        convention gets the dict as a single positional arg, not as
        unpacked kwargs. 34 new tests (test_local_tool_argument_coercion).
v0.8.6.2 Schedule + Telegram trigger block. `_system_prompt
        (tools_enabled=True)` gains a Telegram / external messaging
        trigger block listing zh-TW + en + romanised trigger phrases,
        the `TELEGRAM_ALLOWED_CHAT_IDS` allowlist model, and an
        explicit "Telegram + Schedule composition (the common trap)"
        sub-block that tells the LLM a scheduled prompt mentioning
        Telegram MUST call `send_telegram_message` itself. 8 new
        tests (test_system_prompt_telegram).
v0.8.6.3 Scheduler daemon auto-start. `SchedulerLocalHost._add`
        invokes an `auto_start` callback after a successful save;
        `McpManager` carries the callback (set by `Agent`) + a
        reference to the in-process `SchedulerLocalHost`, and
        `set_auto_start_callback` pushes the callback down AFTER
        `from_config` has finished wiring the host (resolves the
        race). `Agent.mcp_manager` lazy property wires
        `self._auto_start_scheduler_daemon` on first access, which
        calls `start_scheduler()` (idempotent + exception-swallowed).
        Auto-start is GUI / chat-only; CLI `schedules daemon`
        subcommand still works the old way. 16 new tests
        (test_scheduler_daemon_autostart).
v0.8.7  Schedule lifecycle caps + LLM progress tracking.
        `Schedule` dataclass gains three new optional fields:
        `max_fires` (int cap with auto-disable + status="exhausted"
        when `fire_count` reaches it), `expires_at` (ISO-8601
        hard expiry → status="expired"), and `status` (single-
        word lifecycle marker: active / expired / exhausted /
        disabled / failed). All backward-compatible — v0.8.6 JSON
        loads with the new fields as `None` and keeps running.
        `ScheduleManager` exposes four new methods:
        `get_progress(sid)` returns the full lifecycle snapshot
        (status + fire_count + max_fires + fires_remaining +
        next_fire_at + seconds_until_next_fire + expires_at +
        seconds_until_expiry + last_error + recent_runs[5]);
        `list_runs(sid, limit=10)` reads `<sid>.history.jsonl`
        tail with answer truncated to 500 chars; `set_max_fires
        (sid, n)` / `set_expires_at(sid, iso)` are online setters
        with input validation (positive int / parseable ISO-8601
        respectively). `Scheduler.tick_once` does two-phase
        lifecycle re-evaluation per tick: phase 1 honours the
        on-disk `next_fire_at` (preserves hand-edit / test
        patchability), phase 2 calls `compute_next_fire()` and
        auto-disables + stamps `status` when a cap trips.
        `make_agent_on_fire` stamps `status="failed"` when
        `chat_turn` raises, so a fatally broken schedule can't
        loop forever. Two new MCP tools — `get_scheduled_task_status`
        + `list_scheduled_task_runs` — exposed both in-process
        (`SchedulerLocalHost`) and via the stdio MCP server
        (`scheduler_server.py`), letting the LLM answer
        "這個排程跑幾次了" / "還能跑幾次" / "過期了嗎"
        / "上次什麼時候跑的" / "上次跑得怎樣" in one roundtrip.
        CLI gains `--max-fires N` / `--expires-at ISO` flags on
        `schedules add`; `schedules list` adds a STATUS column;
        `schedules show` prints status / max_fires / expires_at /
        last_error / last_error_at. Agent system prompt gets a
        Schedule-progress trigger block (zh-TW + en + romanised
        phrases + a "為什麼 X 停了" status-decision tree).
        Bug fix: `compute_next_fire` now normalises ISO-8601
        `Z`-suffix strings (and any tz-aware input) to naive
        local datetime at the top of the function so the
        `after >= expires_at` comparison doesn't blow up with
        `TypeError: can't compare offset-naive and offset-aware
        datetimes`. 42 new tests (test_schedule_progress +
        test_schedule_status_mcp + test_schedule_cli +
        test_system_prompt_schedule_progress).
v0.9.0  Document write path from chat. QdrantLocalHost gains
        `add_document` (inline content / file path) +
        `update_document`; write sandbox anchored at
        QDRANT_IMPORT_ALLOWED_ROOT with resolve()-based
        traversal defence.
v0.9.1  Multimodal ingest: `describe_image` (Ollama Cloud vision,
        SHA-1 cache, base64 or path input) + sandbox-scoped.
v0.9.2  `resize_image` (Pillow, EXIF-preserving) to fit the
        4 MB vision cap before describe_image.
v0.9.3  FilesystemLocalHost — 15 file/directory/search/copy/
        diff/patch tools over split read/write sandbox roots
        (QDRANT_FILESYSTEM_READ_ROOT / _WRITE_ROOT), symlink-
        safe traversal, dangerous-ext blocklist, 4 MB caps.
v0.9.4  Expanded filesystem surface (tree/search_content/
        patch_file with unified-diff round-trip).
v0.9.5  Independent drawio viewer window (pywebview + drawio
        embed JSON protocol): DiagramsWindowManager + per-window
        _DiagramsViewerApi; chat-side _extractDiagramXml + the
        per-message "View in Diagrams" button. Hotfixes .1-.8:
        bare mxGraphModel extraction (JSON unicode-escape
        normalisation), raw-iframe drawio JSON protocol replacing
        the undocumented DrawioViewer wrapper, loading overlay +
        browser escape hatch, HTML defensive fallbacks + JS
        error/watchdog, pywebview bridge AFTER-DOMContentLoaded
        lifecycle fix (bridgeApi/_waitForBridge), mxfile envelope
        typo fix (<mxDiagram> → canonical <diagram>), and removal
        of the blank toolbar viewer button.
v0.9.6.0 Open-with MCP (Explorer semantics). OpenWithLocalHost —
        open_file / open_content / list_openable_types route
        chat artifacts to their associated application:
        dangerous executables refused → .drawio (and drawio-
        shaped .xml) to the in-GUI Drawio Viewer via
        McpManager.set_diagrams_opener (GUI wiring mirrors the
        scheduler auto-start pattern; CLI/stdio fall back to the
        OS file association) → os.startfile/open/xdg-open.
        open_content writes inline content atomically (4 MB cap,
        drawio sniffing forces .drawio ext). stdio server
        (src/mcp_server/openwith_server.py) for external clients.
        System prompt gains the Open-with trigger block. 43 new
        tests (test_open_with_host + test_system_prompt_openwith).
v0.9.6.1 Docs-only. mcp.json.example gains the drawio remote MCP
        example (streamable_http, with the two-services note:
        mcp.draw.io = diagram tools for the LLM, embed.diagrams.net
        = the local viewer's canvas — independent lifecycles) and
        the openwith stdio example. Failure semantics documented:
        McpManager registers remote servers once at startup, a
        connect failure lands in McpManager.failed with no
        in-session retry, and the remedies are a GUI restart or
        re-Saving mcp.json in the Admin tab (replace_config
        hot-reload).
v0.9.7.0 Log anomaly analysis MCP. New src/log_analyzer/ package
        (parser / anomalies / store) + LogAnalysisLocalHost
        (ingest_log / analyze_anomalies / search_logs /
        get_log_stats / list_log_sources / remove_logs) against the
        dedicated QDRANT_LOG_COLLECTION (default log_docs) — one
        log line per point, stable sha1 ids (re-ingest = overwrite).
        Anomaly detection = rule-based statistics (HTTP error rate,
        404 scanning, high-frequency IPs, SQLi/traversal paths,
        scanner user-agents, 401/403 bursts, SSH brute force,
        OOM/kernel events, log floods) + semantic outliers (chunked
        numpy cosine kNN isolation). Read sandbox =
        QDRANT_LOG_ALLOWED_ROOT (fallback: filesystem read root →
        WATCH_DIR → ./documents). System prompt gains the
        Log-analysis trigger block; stdio server
        src/mcp_server/log_analyzer_server.py for external clients.
        95 new tests (tests/test_log_analyzer.py).
v0.9.8.0 Archive MCP. New src/archive_tools.py (pure format logic)
        + ArchiveLocalHost (archive_create / archive_extract /
        archive_list / list_archive_formats) for zip, 7z, tar,
        tar.gz, tar.bz2 and gz inside the filesystem read/write
        sandboxes (inputs accepted from either root; outputs pinned
        to the write root). 7z via the new py7zr dependency (pure
        Python, no system 7-Zip binary). Security: Zip-Slip member
        resolution, dangerous-executable skipping (filesystem MCP
        blocklist), zip-bomb budgets (QDRANT_ARCHIVE_MAX_BYTES /
        _MAX_FILES, defaults 512 MB / 10k members), gz streaming
        cap. System prompt gains the archive trigger block; stdio
        server src/mcp_server/archive_server.py for external
        clients. 63 new tests (tests/test_archive_host.py).
v0.9.8.1 ingest_log accepts compressed archives directly (.zip /
        .7z / .tar / .tar.gz / .tgz / .tar.bz2 / .gz). Archives are
        extracted into a throwaway temp dir (never the user
        sandbox) via the archive layer's own Zip-Slip guard,
        dangerous-ext skip and zip-bomb budgets; every parsable
        member becomes its own Qdrant source
        "<archive>!<member>" so analyze/search can filter
        per-member. Non-log members are skipped-and-reported;
        log_type=auto detects per member; max_lines caps PER FILE;
        new optional members allowlist. Plain-file path unchanged.
        +11 tests (test_log_analyzer.py 95 → 106).
v0.9.9.0 Exec MCP. New src/exec_tools.py (pure execution logic) +
        ExecLocalHost (run_shell / run_bash / run_grep / run_awk /
        run_python) giving the LLM sandboxed local compute for
        format conversion and bulk text work instead of burning
        context. cwd = filesystem WRITE sandbox; child env exposes
        QDRANT_READ_ROOT / QDRANT_WRITE_ROOT. run_grep is a
        pure-Python grep subset (always available); bash/awk come
        from Git-for-Windows or the OS (WSL stub deprioritised,
        MSYS posix-path fix for awk); python = sys.executable.
        Safety: per-call timeout (default 30 s, cap 120 s,
        QDRANT_EXEC_TIMEOUT), output caps (64 KB,
        QDRANT_EXEC_MAX_OUTPUT), machine-level disaster blocklist
        (rm -rf /, mkfs, dd of=/dev/sd*, fork bombs, format, del
        /f /s, shutdown/reboot, diskpart, bcdedit...;
        QDRANT_EXEC_BLOCKLIST=off disables). System prompt gains
        the exec trigger block (tool-choice matrix + the
        "lower your own workload" rule). stdio server
        src/mcp_server/exec_server.py for external clients.
        76 new tests (tests/test_exec_host.py).
v0.9.9.1 Log-analyzer upsert hotfix. LogStore.make_entry_id emitted
        a 24-hex string — not a valid Qdrant point ID (unsigned
        int or UUID) — so EVERY ingest_log upsert died with HTTP
        400 and log_docs stayed at 0 points. The id is now the
        first 32 hex chars of the sha1 formatted as a canonical
        UUID string (still fully deterministic → re-ingest keeps
        the upsert-overwrite semantics). The test suite had
        locked the bug in (len == 24); replaced with a
        parses-as-UUID assertion. tests/test_log_analyzer.py
        106 passed; proven end-to-end on the live box.
v0.9.9.2 OpenRouter provider routing. 8 raw-string env vars
        (OPENROUTER_PROVIDER_ORDER / _ALLOW_FALLBACKS /
        _REQUIRE_PARAMETERS / _DATA_COLLECTION / _IGNORE /
        _QUANTIZATIONS / _SORT / _MODEL_SUFFIX) parsed by
        Agent._apply_openrouter_routing into the documented
        {"provider": {...}} extra_body — whitelist-validated,
        invalid tokens skipped with a warning; injected at ALL
        FOUR chat.completions.create call sites ONLY when the
        resolved provider is openrouter; empty config sends a
        byte-identical request. :nitro (max throughput) /
        :floor (lowest price) model variants append to the slug.
        27 new tests (tests/test_openrouter_routing.py).
v0.9.9.3/4 Chat time UI. v0.9.9.3 added a whole-transcript
        start/end strip above the chat log; v0.9.9.4 replaced it
        with per-bubble time headers after user feedback — every
        bubble wrapped in a .chat-msg-row with the time line
        ABOVE it (user = send time; assistant = 開始 → 結束 via
        the new startTs captured in sendChat). _chatMsgTimeText
        formats HH:MM today / full date cross-day; setLang
        re-renders. tests/test_chat_time_bar.py 9 tests incl. a
        Node+jsdom behaviour driver.
v0.9.10.0 Chat context compression (opt-in). New
        src/agent/context_compress.py — ContextCompressor wires
        three techniques into the chat path, all behind
        CHAT_CONTEXT_COMPRESSION (off | auto | aggressive;
        default off = byte-identical v0.9.9.4): (1) summarization —
        history over CHAT_COMPRESS_TRIGGER_MESSAGES (24) folds the
        older tail into ONE system summary message (LLM-generated
        via Agent._summarize_history, deterministic extractive
        fallback, per-old-prefix sha1 cache LRU 8), the most
        recent CHAT_COMPRESS_KEEP_MESSAGES (12) stay verbatim;
        (2) offloading — successful tool results over
        CHAT_OFFLOAD_THRESHOLD_CHARS (20000) are written under
        the filesystem read sandbox (chat_offloads/, LLM reads
        them back with read_file) and the tool message becomes a
        pointer + 800-char preview (errors never offloaded,
        write failure degrades to inline); (3) selective
        compression — messages over CHAT_COMPRESS_MSG_MAX_CHARS
        (4000) keep fenced code + JSON-looking blocks verbatim
        and squeeze only the surrounding prose (head+tail
        sentences + omission marker; CJK + Latin splitting).
        aggressive halves the trigger + budget. _messages_with_user
        and _run_tool_call are the two integration points; any
        failure degrades to uncompressed history / inline text.
        System prompt gains the Context-offloading block (tools
        mode only). 8 new env vars (config.py raw strings +
        .env.example block). 35 new tests
        (tests/test_context_compress.py).
v0.9.11.0 Date/time MCP. New DateTimeLocalHost (ELEVENTH in-process
        LocalHost, server name datetime-local) with one zero-arg
        read-only tool get_current_time: one call returns LOCAL time
        (ISO 8601 + UTC offset + timezone name + structured
        y/m/d h:m:s + locale-independent weekday) and UTC+0 side by
        side, plus epoch seconds. Registered by from_config before
        Meta (catalog includes it; _datetime_local_host ref kept);
        stdio server src/mcp_server/time_server.py for external
        clients. System prompt tool-selection rules gain the
        "never guess the clock" get_current_time rule (tools mode
        only). 18 new tests (tests/test_datetime_host.py).
v0.9.11.1 Chat latency. Diagnosis: glm-5.3-flash endpoint mandates
        reasoning (281 tokens / 4.4 s for a hello; {"enabled":false}
        rejected but effort=low zeroes it → 1.4 s); the 100-tool
        surface inflates every request to 36,312 prompt tokens; the
        first mcp_manager access costs ~15 s. Fixes: new
        OPENROUTER_REASONING_EFFORT env (top-level extra_body
        reasoning.effort, whitelist, openrouter-only) — the live .env
        pins low; Agent.mcp_manager double-checked-locked; GUI Api
        kicks a one-shot daemon prewarm thread from the first
        get_status poll (mcp_manager + provider off the chat path).
        +10 tests (routing effort ×5, tests/test_gui_prewarm.py ×5).
v0.9.12.0 Per-pair token usage on the chat bubble header. Agent
        extracts usage from every create response (_extract_usage,
        incl. reasoning_tokens) and aggregates the whole tool loop
        (_accumulate_usage + calls counter) into self.last_usage
        (reset at every ask/chat_turn entry); gui/api.py forwards
        it as `usage` (dict-only defence); the chat header line
        joins time + tokens via _chatTokensText (↑ prompt / ↓
        completion tok, N calls note; null for legacy sessions).
        12 new tests (tests/test_chat_usage.py) + chat_time_bar
        guards/jsdom updated.
v0.9.13.2 Gate discovery-trap fix. The declared surface (gated
        tools) and the discovery surface (list_mcp_tools full
        catalog) disagreed — the LLM faked a launch tool call in
        plain text (never dispatched). Fix: gate logic moved to
        McpManager.active_specs()/active_tools() (single source,
        last_hidden_servers tracked); MetaLocalHost lists only
        exposed tools + hidden_servers/hidden_note; system prompt
        gains the never-fake-a-tool-call rule; .env gate re-enabled
        with windows-mcp included. +2 tests (meta gate).
v0.9.15.0 Web UI mode. gui/web_server.py (stdlib http.server only)
        serves the SAME gui/assets frontend + the SAME gui.api.Api
        over POST /api/<method>; a /pywebview-shim.js injected into
        index.html <head> implements window.pywebview (api Proxy with
        Promise results, state, FileDialog + create_file_dialog via
        tkinter on the server desktop, pywebviewready fired at once)
        so app.js/diagrams.js run UNMODIFIED in the browser. Selector:
        python -m gui.app --web (default stays the pywebview window),
        divine-sense-agent-web.bat, python gui/web_server.py. Binds
        127.0.0.1:8334 by default (QDRANT_WEB_HOST/PORT, --host/
        --web-port/--no-browser; loud WARNING on non-loopback hosts).
        Dispatch rejects private/unknown methods; non-JSON results
        degrade to an error envelope. 24 new tests
        (tests/test_web_server.py).
v0.9.15.1 Web bind-any-local-IP. run_web_server loads .env BEFORE
        resolving QDRANT_WEB_HOST/PORT (v0.9.15.0 read them before
        Api() triggered src.config's load_dotenv — .env values were
        silently ignored). 0.0.0.0 lists every reachable LAN URL at
        startup (stdlib probes: UDP-connect trick / getaddrinfo /
        gethostbyname_ex) and the browser opens a usable loopback
        URL; IPv6 binds via _ThreadingHTTPServer6 (AF_INET6; :: is
        dual-stack via IPV6_V6ONLY=0) — previously ANY IPv6 host
        raised OSError; banner prints use flush=True. +16 tests
        (24 → 40 in tests/test_web_server.py).
v0.9.14.1 Tool-loop budget fix. The 6-iteration default aborted every
        GUI desktop-automation task mid-drawing (log: three
        「tool loop hit max_iterations=6」 warnings on the dog-drawing
        turns; 7 create calls = 6 loop + 1 forced bail-out) and the
        mspaint launch ladder burned 2 of 6 (start-menu miss +
        missing .exe). Fix: DEFAULT_MAX_TOOL_ITERATIONS 6 → 20;
        new MAX_TOOL_ITERATIONS env resolved per call by
        _resolve_max_tool_iterations() (bad values fall back, never
        crash); _tool_loop bail-out message now asks for completed
        steps + 繼續 instead of a bare stop; system prompt gains the
        Windows desktop automation block (App launch ladder with the
        .exe rule, PowerShell System.Drawing preferred for drawing,
        budget discipline). 17 new tests (test_max_tool_iterations.py).
v0.9.14.0 Chat-UI MCP tool picker. manager.active_specs gains a
        per-TOOL allow-list override (tool_names param; empty list
        honoured as all-off); ask/chat_turn/_chat_tools thread
        active_tool_names; gui chat_turn forwards the picker's
        active_tools; chat toolbar gains a 🧰 dropdown listing all
        tools grouped by server with enable checkboxes (DISABLED
        set in localStorage, new tools default on, N/M badge,
        All-on/All-off/Reset, i18n ×4, textContent-only). 17 new
        tests (active_servers ×6, gui_chat_tools ×11).
v0.9.13.0 MCP tool-surface gate. Agent._chat_tools() filters the
        per-request tool list by MCP_ACTIVE_SERVERS (comma list of
        server_name; empty = full surface; unknown names warned,
        zero-match falls back to full); applied at both tool-loop
        call sites. Measured: 100 tools = 36,312 prompt tokens →
        core 53 tools = 11,022 (-70%); Together auto-caches the
        36k prefix (cached=31,360) but multi-provider orders break
        hits — pinning together gives cached every turn + stable
        ~2.2 s hello. 7 new tests (tests/test_mcp_active_servers.py).
v0.9.13.1 Per-pair cache display. _extract_usage pulls
        prompt_tokens_details.cached_tokens (accumulated across the
        tool loop); _chatTokensText appends `· ⚡ N cached` when the
        provider reported a hit. tests updated (chat_usage helper
        carries both detail nests; chat_time_bar jsdom s9/s12).
v0.9.15.2 Mobile web format (viewport meta + ≤820px/≤480px
        responsive breakpoints in style.css, shared with the
        pywebview GUI). v0.9.15.3 logs the per-open-page 30 s
        get_status heartbeat at DEBUG (several open tabs each ran
        their own setInterval — the INFO lines flooded app.log).
v0.9.16.x Web upload + OCR hardening. v0.9.16.0 client file
        upload (POST /upload multipart into the filesystem write
        sandbox uploads/, 📎/📷 buttons, GUI mode hides both);
        v0.9.16.1 upload→OCR chain (describe_image mode
        auto-inference + empty-choices retry); v0.9.16.2 strips
        base64 blobs from tool-result messages (raw_image_base64
        blew the context window → OpenRouter 404 "No endpoints
        found") + one hard-error retry; v0.9.16.3 locks the Api
        lazy-init (web two-page boot raced into TWO Agents) + the
        image-OCR prompt rule; v0.9.16.4 adds the OpenRouter vision
        fallback to ImageDescriber (chain ollama → openrouter →
        openai-compat, result carries vision_backend).
v0.9.17.0 Cross-provider failover for the chat LLM itself. The
        describe_image-style backup mechanism generalized to ALL
        providers: _create_validated walks _PROVIDER_ORDER after
        2 primary attempts (OpenRouter-only extra_body stripped
        for non-openrouter fallbacks; last_provider records who
        answered; _summarize_history routed through it too).
        ImageDescriber gains the SiliconFlow vision candidate
        (chain ollama → openrouter → siliconflow → openai-compat).
v0.9.18.0 Chain order separation. chat 主鏈 and the vision OCR
        chain are two INDEPENDENT application requirements, each
        re-orderable from .env: LLM_PROVIDER_CHAIN (Agent primary
        + failover walk) and VISION_PROVIDER_CHAIN (ImageDescriber).
        Shared parser parse_provider_chain() lives in the
        ZERO-side-effect module src/provider_chain.py (deliberately
        does NOT import src.config — its import runs load_dotenv
        and would re-inject .env values a test just wiped;
        re-exported from src.config for path stability). Grammar:
        comma list, case-insensitive; empty = legacy order (zero
        behaviour change); unknown tokens skipped with a warning;
        duplicates collapse; partial lists append the unlisted
        providers in default relative order (PRIORITY, never
        exclusion). Agent gains _resolved_provider_order();
        ImageDescriber walks the FULL chain via _vision_candidates()
        (Ollama is no longer a hard-coded primary — just the first
        entry of the default chain); _fallback_vision_candidates()
        kept as a backward-compatible view. 28 new tests
        (tests/test_provider_chain_order.py).
v0.9.19.0 OpenAI-compatible API configuration — BOTH knobs:
        (1) OPENAI_CHAT_MODEL, the dedicated model for the openai
        provider (which IS the generic OpenAI-compatible escape
        hatch via OPENAI_BASE_URL). Fixes the poisoning gap: the
        openai slot's model was LLM_MODEL only, and a non-default
        LLM_MODEL GLOBALLY overrides the ollama/openrouter/
        siliconflow chat models — pointing openai at DeepSeek via
        LLM_MODEL=deepseek-chat sent "deepseek-chat" to
        ollama.com/openrouter.ai on failover. Priority mirrors the
        other 3 providers: non-default LLM_MODEL still wins; unset
        = LLM_MODEL (zero behaviour change).
        (2) FIFTH provider openai-compat — fully independent
        OPENAI_COMPAT_API_KEY / OPENAI_COMPAT_BASE_URL (REQUIRED) /
        OPENAI_COMPAT_CHAT_MODEL triple, coexisting with `openai`,
        appended LAST in _PROVIDER_ORDER (auto-detect + default
        failover order unchanged for existing users) and joining
        LLM_PROVIDER_CHAIN automatically. Key-without-URL raises
        a clear error in _pick_provider (a default would silently
        hit api.openai.com); the failover walk skips the candidate
        with a warning. Error messages name
        OPENAI_COMPAT_API_KEY via .upper().replace('-', '_').
        +9 tests (llm_providers ×8, provider_chain_order ×1);
        _resolved_agent (failover tests) now pins EVERYTHING on the
        CURRENT config object (sys.modules-resolved, reload-safe)
        fixing the pre-existing order sensitivity for good.
v0.9.20.0 Real-time chat saves. Audit of the v0.8.7 3-layer safety
        net found three gaps, all fixed in gui/assets/app.js
        (shared GUI/web): (1) SEND-TIME save — sendChat calls
        autoSaveChat() right after appending the USER bubble and
        BEFORE awaiting chat_turn, so the user's message + session
        survive a crash / tab close during a long tool-loop turn;
        (2) error bubbles now save too (both error exits returned
        without a save — a refresh dropped them from history);
        (3) web-mode unload uses navigator.sendBeacon (the
        beforeunload fetch can be CANCELLED by the browser at
        unload time — desktop and mobile alike); the beacon body
        matches the POST /api/<method> args shape and the web
        handler json.loads(raw) without checking Content-Type.
        Save timeline: send-time (user msg) → reply-time (usage/
        time header) → error bubble → refresh/close via sendBeacon
        → process crash via localStorage backup + bootDraftRecovery.
        +4 tests (test_chat_autosave.py).
v0.9.21.0 Chat-history MCP core. TWELFTH in-process LocalHost
        ChatHistoryLocalHost (server chat-history-local) — three
        READ-ONLY tools over the persisted sessions via
        SessionManager: list_chat_sessions (newest first, title
        keyword filter, scheduler-fire sessions hidden by default
        — sid prefix sched-), search_chat_history (case-insensitive
        keyword across message content, ±context snippets,
        optional sid scope + role filter), get_chat_session (most-
        recent last_n messages, per-message content capped).
        Built-in caps only (search 10 soft / 50 hard, snippet
        280/2000, total budget 8000 chars; get last 20 / 100 hard,
        content 1200/4000) — zero new env vars; a runaway history
        dump can never blow the context window. from_config
        registers it before Meta; stdio mirror
        src/mcp_server/chat_history_server.py. System prompt
        tool-selection rules gain the chat-history trigger block
        (「上次聊過什麼」 / 'did we discuss X' → query the tools,
        never fabricate from memory); legacy prompt excludes it.
        MCP_ACTIVE_SERVERS gate: live .env + .env.example gain
        chat-history-local. 37 new tests
        (tests/test_chat_history_host.py).
v0.9.21.1 Version-chip hotfix. The brand-side chip (index.html
        #app-version) carried a hardcoded 'v0.8.6' that NO JS ever
        updated (v0.9.14.1 rewired gui/api.py but missed the HTML
        fallback literal; refreshStatus only stuffed the version
        into the bottom status-bar detail). Fix: the HTML fallback
        literal emptied (single source of truth); refreshStatus
        paints r.version (pyproject dynamic value) into
        #app-version on every poll; the stale '0.9.14.1' fallback
        in setStatus's detail removed. +2 tests (no-hardcoded-
        version HTML guard + paint-chip source lock).
v0.9.22.0 Scheduler daemon boot auto-resume. Root cause of the
        missed 2026-09-07T14:00:00 fire: the daemon is a THREAD
        that dies with the process (schedules persist in
        schedules/*.json); v0.8.6.3 auto-started it ONLY when the
        LLM ADDED a schedule, and the user had restarted the web
        UI at 11:37 to apply the openai-compat provider config —
        nothing was ticking at 14:00. Fix:
        gui/api.py::_resume_scheduler_daemon(agent) hooked into
        _prewarm_agent() (first get_status poll = boot): resumes
        the daemon whenever at least one schedule is enabled +
        lifecycle-active (start_scheduler is idempotent), INFO
        logs the schedule names, ALL failures swallowed. The
        missed next_fire_at catches up on the first tick
        (tick_once phase-1 gate: <= now → fire). Opt-out:
        QDRANT_SCHEDULER_AUTORESUME=off. 11 new tests
        (tests/test_scheduler_boot_resume.py).
v0.9.23.0 Schedule-fire LLM summary fix (answer=0 chars, dual root
        cause) + empty-final-answer wrap-up. (1) make_agent_on_fire
        read result.get("answer") but Agent.chat_turn returns the
        TUPLE (answer, context_md, sources) — isinstance(dict) was
        False so the answer was ALWAYS None even when the LLM had
        produced a full summary; _fire now accepts both shapes.
        (2) the telegram stdio entry was missing from config/mcp.json
        so send_telegram_message was never in the catalog (the LLM
        called list_mcp_tools hunting for it). Hardening: _tool_loop
        does ONE no-tools wrap-up create when the final answer after
        a tool round is empty (reasoning models can emit content=""
        with finish_reason="stop"); wrap-up failure degrades to the
        empty string. Gate wiring: live .env + .env.example
        MCP_ACTIVE_SERVERS gain `telegram`. +6 tests (test_scheduler
        ×2, test_tool_loop_wrapup ×4).
v0.9.24.0 兆豐 MegaAPI stock query + order MCP extension. THIRTEENTH
        in-process LocalHost MegaStockLocalHost (server stock-local)
        — 6 tools: stock_get_quote / stock_get_kline /
        stock_get_account_summary / stock_list_orders /
        stock_place_order / stock_cancel_order; core logic in
        src/mega_stock_tools.py (MegaStockEngine, pure module).
        Mock-first safety: MEGA_STOCK_MODE=mock (default) runs a
        fully simulated in-memory book — deterministic per-symbol
        quotes (simulated=true), in-memory mock order book
        (MOCK-YYYYMMDD-nnnn ids; market orders fill immediately,
        limit orders stay open for cancel drills) — NO socket, NO
        megaSpeedy import, NO real orders in development/testing.
        live requires the megaspeedy Windows component + credentials
        and THREE gates (mode=live + MEGA_STOCK_TRADING_ENABLED=on +
        per-call confirm=true); the live wiring ships LOCKED
        (聯調未完成 — untested order paths stay locked, no fabricated
        API calls from the docs). from_config registers before Meta
        (_stock_local_host ref); stdio mirror
        src/mcp_server/stock_server.py. System prompt gains the
        stock trigger block (「股價」 / 'quote' → stock-local tools;
        MUST state 「模擬資料，非即時行情」 when answering from mock
        data; confirm=true required for orders); legacy prompt
        excludes it. .env.example gains the full MEGA_STOCK block +
        the gate example line gains stock-local (hidden-by-gate =
        the v0.9.13.2 discovery trap otherwise). 26 new tests
        (tests/test_stock_host.py; no test ever touches the network
        or a real order). test_mcp_active_servers flake fixed:
        Agent() runs load_dotenv AFTER the tests' delenv — the gate
        env is re-cleared post-construction and the gate-off
        assertion is async-insert-robust (superset compare).
v0.9.26.0 兆豐 MegaAPI live wiring. The user's API permission
        opened (115/09/09: branch 7000, account 1673976, order host
        spapi.emega.com.tw:56789, quote host 119.31.179.86:34567,
        login from next-day 08:30). Component install: SpeedyAPI_PY.7z
        extracted; megaSpeedy/ placed at the project root (DLL loads
        via os.path.dirname(__file__); Temp/ travels with it). REAL
        function surface read from spdOrderAPI.py source (replacing
        the unconfirmed doc guesses): SendNewOrderEx(Market, UDD,
        Symbol, Price, Side B/S, Qty, OrderType L/M, TIF R/I/F, TS
        N/O/I/A, PE A, TWSEOrdType 0/3/4/A) → NID (0=failed →
        GetLastErrorMsg), order id arrives via OnReplyNewOrder;
        SendCancelOrderEx needs symbol/price/side — _live_cancel
        resolves them from queryStkOrder first (query_param =
        {'branch_id','cust_id','apcode','market','qry_type',
        'nocsint'}); queries queryStkOrder/queryStkMatch/
        makeStockAccountInquriy return JSON strings; K-lines ride
        the TRADING connection (GetDKChartData / GetAdjustedDKChartData);
        quotes via spdQuoteAPI.Logon + Subscribe + OnOrderBook
        (Msg.BidPrice1-5/AskPrice1-5) + OnTrade. MegaLiveSession:
        event wrapper (OnConnected → LogonProxy per the official
        testPyOrderAPI pattern; OnLogonResponse → SetAccount) with
        threading.Event sync waits (conn 15s / reply 10s / quote
        snapshot 3s), lazy long-lived connection (FIsLogon check),
        thread-safe lock; SDK import redirects stdout (the load
        banner would corrupt the stdio MCP JSON-RPC channel). Live
        QUERIES usable from the permission date; live orders wired
        (SendNewOrderEx/SendCancelOrderEx) but STILL GATED: THREE
        gates + the certificate gate (MEGA_STOCK_CERT_PATH/PASSWORD
        — queries need no cert, orders do). Live smoke pre-open:
        Connect reaches spapi.emega.com.tw:56789 and the host
        answers 「未授權使用」 (expected pre-open); errors captured
        cleanly, mock untouched, 26 tests green. .env.example +
        live .env gain the MEGA_STOCK live block (account/branch/
        hosts prefilled; MEGA_STOCK_ID/PASSWORD left for the user).
        Structure fix: _find_order_row (defensive recursive search
        of the queryStkOrder payload — real field names confirmed
        only after the first live query) moved to module tail after
        it accidentally split the MegaStockEngine class
        (place_order/cancel_order became orphans).
v0.9.64.0 hicloud HWS (Chunghwa Telecom CaaS/CVPC) cloud MCP
        extension — operate hicloud VMs through the official REST
        API (docs: https://hws.hicloud.hinet.net/hws-doc/zh_TW/).
        NINETEENTH+ in-process LocalHost HicloudLocalHost (server
        hicloud-local) — 8 tools: hicloud_list_instances /
        hicloud_describe_images / hicloud_list_instance_types /
        hicloud_run_instances / hicloud_start_instances /
        hicloud_stop_instances / hicloud_reboot_instances /
        hicloud_terminate_instances; core logic in
        src/hicloud_tools.py (HicloudEngine, pure module). The
        OFFICIAL Authentication-of-API-Calling signature is
        reproduced verbatim and LOCKED to the doc example
        (sorted params → lowercase → HMAC-SHA1(SecretKey) →
        Base64 → '+'→'*' '/'→'-' '='→removed;
        VBUfKTt48Wf6xbdny98N4Gi07f4 matches in tests). Mock-first
        safety (mega_stock pattern): HICLOUD_MODE=mock (default)
        runs a fully simulated in-memory VM book — NO HTTP; live
        requires HICLOUD_ACCESS_KEY/SECRET_KEY. Lifecycle writes
        behind TWO gates (per-call confirm=true enforced even in
        mock + HICLOUD_WRITES_ENABLED=on live master switch,
        default off). from_config registers before Meta
        (_hicloud_local_host ref); stdio mirror
        src/mcp_server/hicloud_server.py. System prompt gains the
        hicloud trigger block (all 8 tool names + gates + mock-
        data disclosure; legacy prompt excludes it). .env.example
        gains the HICLOUD block + the gate example line gains
        hicloud-local; config/mcp.json.example gains the hicloud
        stdio worked example. 44 new tests
        (tests/test_hicloud_host.py).
v0.9.65.0 chat web file download — the reverse direction of the
        v0.9.16.0 upload. GET /download?path=<p> in gui/web_server.py
        streams sandbox files with the SAME sandbox resolution as
        read_sandbox_image (file:/// strip, relative → WRITE root,
        import ∪ READ ∪ WRITE containment — never an arbitrary read)
        + dangerous-ext refusal + QDRANT_WEB_DOWNLOAD_MAX_MB cap
        (default 200, floor 1) + 1 MB chunk streaming + RFC 5987
        Content-Disposition (CJK-safe) + nosniff. gui/api.py gains
        _resolve_sandbox_file + _download_name_allowed +
        save_sandbox_file_as (GUI-mode copy, collision -N). app.js
        gains renderSandboxFiles() in renderChat() — non-image
        sandbox paths in the bubble text → ⬇ download chip
        (textContent-only; image exts excluded — those have the
        v0.9.30 previews) — and downloadSandboxFile() (web mode →
        window.open /download; GUI mode → pywebview FOLDER dialog +
        save_sandbox_file_as). i18n downloadFile/fileSaved ×4;
        .chat-download-chip css. 38 new tests
        (tests/test_web_download.py, live round-trip green).
v0.9.66.0 hicloud HWS full service surface — hicloud-local grows
        8 → 56 tools (+48 data-driven) covering ALL remaining actions
        in the user-supplied official doc dump (hicloud_rest_api_v2.md,
        CaaS + CVPC = 53 actions): VM completions
        (modifyInstanceAttribute / restoreInstance), CloudWatch
        getInstancesStats, listLog, EBS volumes (create/attach/detach/
        delete/list — radix 1-20 × 100GB), Image completions
        (createImage/deleteImage/modifyImageAttribute), SLB (create
        with CaaS instanceId+port AND CVPC vpcBusinessServiceId+
        loadBalancerType shapes / delete / list with 5 query modes /
        querySlbVIPHealthStatus / replaceLoadBalancerPolicy), Subnet
        (create — CVPC needs vpcBusinessServiceId, CaaS needs
        intranetGatewayBusinessServiceId / delete / list / modify),
        NetworkAcl firewall (create with CaaS legacy policy strings or
        CVPC subnet$/fqdn$/iprange$ strings / delete / list /
        replaceNetworkAcl), VPC (create/delete/list/modify), Internet
        Gateway + Internet IP (create/delete/list ×2), Intranet
        Gateway + Link (create/delete/list/modify), NAT + PortFwd
        (create/delete/list ×2). src/hicloud_tools.py gains _RES_SPECS
        (12 resource kinds with list/create/delete/modify actions +
        id params + deterministic mock id prefixes like
        VD550100000001) + in-memory mock resource books (_mock_res_*
        helpers) + generic _res_list/_res_create/_res_delete/
        _res_modify (mock and live share the official signature;
        _extract_list tolerates per-service list-key names); ALL
        lifecycle writes go through _gate_write (per-call confirm=true
        enforced even in mock; live also needs
        HICLOUD_WRITES_ENABLED=on). HicloudLocalHost gains the
        module-level _HICLOUD_TOOL_SPECS (48 specs, compact
        _hicloud_schema builders) + _engine_call (tool args map 1:1 to
        the engine kwargs) + a list_tools() loop — the 8 v0.9.64
        hand-written tools stay in _base_tools() untouched (existing
        tests / handlers / dispatch compatible). stdio mirror gains
        _TOOL_NAMES/_TOOL_METHODS (56-tool parity). System prompt
        hicloud block lists the new surface (queries need no confirm;
        create/delete/modify/attach/detach require confirm=true).
        Tests 44 → 47 (test_new_service_tools_advertised locks all 48
        names + schemas; test_new_service_handlers_e2e drives the mock
        CRUD chain; test_new_write_gates verifies confirm on all 34
        write tools; the 8-tool assertion updated to 56 no-duplicate
        names).
v0.9.66.1 Embedder local-ctx guard. The scheduler fire's 1240-char
        CJK answer (~1201 tokens) fed to memory_store embedding
        exceeded the llama.cpp slot ctx (n_ctx=1024) → HTTP 400
        exceed_context_size_error → cooldown → OpenRouter
        fallthrough on EVERY fire. Fix: src/ingest/embedder.py gains
        _resolve_local_max_chars() + Embedder._clip_local() —
        QDRANT_EMBED_LOCAL_MAX_CHARS (default 950 chars, 0=off)
        clips OVER-LONG inputs before the local POST in BOTH
        _embed_uncached and _embed_batch_local (cache keys stay on
        the FULL text). Local never 400s; cooldown never trips on
        long texts. +4 tests (TestLocalClipGuard).
v0.9.67.0 Telegram MarkdownV2 converter. send_telegram_markdown
        already sent parse_mode="MarkdownV2" but the LLM text was
        RAW normal markdown → unescaped . - ( ) 400'd with
        can't-parse-entities and **bold** rendered literally. NEW
        src/telegram_md.py markdown_to_telegram_v2(): placeholder
        strategy (fenced code with sanitised language tokens /
        inline code / inline links / tables→pre rendered first and
        protected as \x00N\x00; headings→bold; bullets→•; task
        lists→✅/☐; blockquotes keep >; emphasis converted
        word-boundary aware; the REST fully escaped; placeholders
        restored REVERSED so nested bold-with-link expands). Wired
        into send_telegram_markdown (convert → 4096 recheck →
        Telegram 400 degrades to plain send of the ORIGINAL text,
        mdv2_fallback) + daemon TelegramBotClient.send_markdown
        (chunked, per-chunk 400 → plain chunk) + _send_reply_md
        (error notices / voice stay plain). +28 tests
        (tests/test_telegram_md.py).
v0.9.67.1 Schedule fire INDEPENDENT CONTEXT + telegram-send
        integrity guard. Manual trigger of cron 9e924b90ac9c
        (「下午2點股市異常分析」) produced answer=1243 chars with
        ZERO tool dispatches while claiming the Telegram send — the
        reused sched-<id> session had accumulated 14 fires (30
        messages) and every fire carried the FULL history, teaching
        the model to parrot old reply shapes. make_agent_on_fire
        caps fire history via QDRANT_SCHEDULE_FIRE_HISTORY (default
        0) + the telegram-send integrity guard (prompt intent +
        reply claim + no send_telegram_* dispatch → ONE corrective
        turn; corrective answer replaces the original; failure
        degrades to the original + WARNING). +10 tests
        (tests/test_scheduler_tg_guard.py).
v0.9.68.0 Web/GUI chat bidirectional voice. gui/api.py gains TWO
        public bridge methods shared by BOTH entry modes (web shim
        POST /api/<method> and the pywebview bridge go through the
        same api.call path): ``voice_stt(audio_b64, filename)`` — the
        chat toolbar 🎤 mic button records a MediaRecorder blob
        (webm/opus, 60 s auto-stop cap), base64's it, and the
        transcript is inserted into the chat input (dictation model;
        audio NEVER touches disk or the sandbox); and
        ``tts_speak(text)`` — every assistant bubble gains a 🔊
        button that synthesizes the reply via SpeechClient, adapts it
        for the <audio> element (mp3/ogg native, pcm → wav) and
        plays the base64 data URL; ONE audio at a time + a per-text
        LRU cache (16). SpeechClient container support grew to match:
        audio_format_of sniffs EBML webm + ftyp mp4/m4a,
        _to_wav_for_stt transcodes every non-wav container, multipart
        names are truthful per container (the webm blob used to post
        under a mismatched .ogg name), and to_browser_audio adapts
        pcm → wav (None without ffmpeg → text-only degradation).
        Same env chain as Telegram voice
        (TELEGRAM_STT_*/TTS_* → OPENROUTER_API_KEY → OPENAI_API_KEY).
        Mic constraint: getUserMedia needs a secure origin
        (localhost/HTTPS) — a LAN-IP page on a 0.0.0.0 bind is
        browser-blocked (127.0.0.1 unaffected). QDRANT_UI_VOICE_MAX_MB
        caps one upload (default 8 MB base64). 30 new tests
        (tests/test_ui_voice.py); telegram_voice 39 passed
        zero regression.
v0.9.67.2 Fire memory suppression + dispatch-shape fix. The
        post-restart verification fire exposed TWO v0.9.67.1
        issues: (a) the guard MISJUDGED a real send —
        Agent.last_tool_dispatches is a plain list[str] while the
        guard parsed dict/object shapes only → corrective turn →
        DUPLICATE send (16:47:18); fix = str-first parsing.
        (b) memory recall fed the fire OLD prices — every fire's
        web_search results are ingested as kind=query memories
        (v0.9.59) and per-turn recall re-injected them into a fire
        asking for TODAY's data; fix = chat_turn /
        _chat_turn_impl / _messages_with_user gain
        use_memory: bool|None=None (None=default recall; False=skip,
        store never queried) + make_agent_on_fire passes
        _fire_memory_enabled() (env QDRANT_SCHEDULE_FIRE_MEMORY,
        default off) on BOTH turns + the memory block gains a
        HISTORICAL-SNAPSHOT hard rule (never cite stored values as
        current data for 今天/最新/現在/即時 questions — helps normal
        chat too). +14 tests; combined memory+scheduler+guard suite
        150 passed.
v0.9.25.0 Webhook trigger MCP core — the event-driven counterpart
        to the scheduler. WebhookTrigger dataclass + WebhookManager
        (src/agent/webhook.py, mirror of scheduler.py: atomic JSON
        in $QDRANT_RAG_HOME/webhooks/<id>.json + history.jsonl ring
        MAX_HISTORY=50) — every trigger carries its OWN random
        access_key (secrets.token_urlsafe(32)) and lifecycle gates
        mirroring Schedule (enabled / status / max_fires →
        exhausted / expires_at → expired / failed stamp on
        chat_turn crash). WebhookServer = stdlib
        ThreadingHTTPServer daemon thread on
        QDRANT_WEBHOOK_HOST:PORT (default 127.0.0.1:8335):
        GET|POST /trigger/<id> with ?key= or X-Access-Key,
        constant-time compared (401 wrong key / 404 unknown /
        403 lifecycle-gated / 503 no fire engine), payload
        {"payload": ...} → {payload} placeholder replace or
        [Webhook payload] append, ?async=1 → 202 background fire,
        default sync (HTTP response carries the LLM answer).
        FOURTEENTH in-process LocalHost WebhookLocalHost (server
        webhook-local) — 10 tools mirroring the schedule tools
        (add returns URL + access key SHOWN ONLY ONCE / list /
        get / get_webhook_trigger_url(reveal) / delete / enable /
        disable / run_webhook_trigger_now / list_webhook_trigger_
        runs / get_webhook_trigger_errors); add auto-starts the
        listener via McpManager.set_webhook_auto_start_callback
        (v0.8.6.3 pattern) and run_webhook_trigger_now gets the
        make_webhook_fire engine via
        set_webhook_fire_callback. Boot auto-resume:
        gui/api.py::_resume_webhook_server in _prewarm_agent
        (v0.9.22.0 pattern; QDRANT_WEBHOOK_AUTORESUME=off opts
        out). System prompt gains the webhook trigger block (key
        shown ONLY ONCE honesty rule; never fabricate URL/key);
        legacy prompt excludes it. stdio mirror
        src/mcp_server/webhook_server.py (manages triggers for
        external clients; does NOT bind the port). .env.example /
        .env gain QDRANT_WEBHOOK_HOST/PORT/AUTORESUME + the gate
        list gains webhook-local. 38 new tests
        (tests/test_webhook_host.py — real HTTP round-trips on an
        ephemeral loopback port with a stub fire engine).
v0.9.25.1 Speed hotfix — every Qdrant call paid ~2 s because
        `localhost` resolves to ::1 FIRST and a TCP connect to ::1
        is REFUSED only after ~2 s on this box (RDP/firewall
        anomaly), while qdrant-client re-opens its HTTP connection
        per request (no keep-alive reuse). Measured: query_points
        2073 ms via localhost vs 29 ms via 127.0.0.1 (~71x); raw
        requests POST 7 ms; server side 10-19 ms (actix request
        log) — the server was never the problem. Fix:
        src/config.py::_normalize_loopback_url rewrites the bare
        //localhost: loopback NAME to //127.0.0.1: at config load
        (Qdrant binds 0.0.0.0 — same server; [::1] / LAN IPs /
        remote hostnames untouched); .env / .env.example pin the
        literal. Quantization / gRPC / HNSW-ef tuning deliberately
        NOT applied (search itself is 10-19 ms; int8 would cost
        ~690 MB extra RAM for nothing at 167k points). Also
        explains the old "get_collection ~2 s" / "list_documents
        ~3 s" observations. Residual: list_documents group-by is a
        REAL ~1.7 s server-side call (behind a 60 s cache). 10 new
        tests (tests/test_qdrant_url_loopback.py).
v0.9.25.2 docs-only — Qdrant documentation sync: NEW
        C:\qdrant\README.md (server install/ops guide: version,
        start/stop, health checks, ports, collections, the
        loopback speed finding, restart via launch-detached.ps1);
        ARCHITECTURE.md arc entry + §1.3 default-URL row + §11
        debugging row + §12.1 Windows quirk bullet; CLAUDE.md §5
        Qdrant-Specific Gotchas gains the loopback bullet. No
        code / test changes.
v0.9.25.3 llama.cpp embedding server tuning (user ask: 優化 llama.cpp
        提升回應速度). Two findings: (1) the Vulkan server had been
        DOWN for two weeks — `ggml_vulkan: device fault on Vulkan0`
        at 4.8 h uptime, preceded by constant `making room for
        prompt cache entry` evictions (0.4-57 MiB entries) —
        unique-text embeddings never hit the server prompt cache, so
        it is pure overhead AND the prime VRAM-churn suspect; with
        the server down `EMBEDDING_BACKEND=auto` silently fell back
        to Ollama Cloud. (2) `.env` carried
        `LLAMACPP_EMBED_URL=http://localhost:8080` — the SAME ::1
        loopback trap as QDRANT_URL (v0.9.25.1): embedder re-opens
        its HTTP connection per requests.post, ~2 s per embed/probe.
        5-config live benchmark (12x single-query + 3x batch-of-64
        ~780-char CJK+EN chunks; harness C:\llama.cpp\_bench.py +
        _run_bench.ps1): vulkan_old 153.4 ms / 49.4 s per 64-batch;
        winner vulkan_opt_fa_ub1024 128.5 ms / 43.6 s (-16% / -12%);
        cpu_opt control 449.9 ms single and 64-batch >300 s TIMEOUT —
        Vulkan stays primary. Bat flags now `--ctx-size 4096 -ngl 99
        -fa on -ub 1024 --threads 12 --cache-ram 0` (ctx 4096 =
        1024/slot stops truncating 500-char CJK chunks at 512
        tokens); CPU bat pooling CORRECTED mean → last + --embd-
        normalize 2 (was silently producing semantically different
        4096-dim vectors); embedder.py normalises LLAMACPP_EMBED_URL
        through _normalize_loopback_url; .env/.env.example pin
        127.0.0.1. Both bats are PURE ASCII — Unicode comment
        decorations (box-drawing ──) shift cmd.exe's batch parser
        under chcp 65001 and comment fragments execute as commands.
        +3 tests (TestEmbedderUrlNormalization).
v0.9.27.0 Telegram bidirectional chat. New src/agent/telegram_bot.py
        — TelegramBotClient (stdlib urllib Bot API thin client:
        getUpdates long-poll 10 s + sendMessage 4096-chunking) +
        TelegramBotDaemon (daemon thread, per-chat persistent
        session tgbot-<chat_id>, fail-closed
        TELEGRAM_ALLOWED_CHAT_IDS — empty = ignore all) +
        make_telegram_on_message feeding whitelisted users'
        messages into Agent.chat_turn with the FULL tool surface;
        replies auto-delivered via sendMessage; anti-double-send
        framing (the prompt tells the LLM NOT to call
        send_telegram_message itself). Pure OUTBOUND HTTPS — no
        inbound port; boot auto-resumed by gui/api.py
        (QDRANT_TELEGRAM_BOT_POLLING=on default, 2 s poll
        interval).
v0.9.28.0 Live stock order consent gate. The FOURTH gate: a live
        order additionally requires Telegram approval —
        request_order_approval sends a unique STK-XXXX code to
        MEGA_STOCK_ORDER_APPROVAL_CHAT_ID (default = the first
        allowlist entry); the bot daemon resolves the reply
        BEFORE the normal chat flow (同意+code / bare code →
        approved, 拒絕+code or ambiguous → denied, timeout →
        denied); a 30 s stale-poll heartbeat check tells the
        user to enable QDRANT_TELEGRAM_BOT_POLLING. engine.
        _place_with_consent blocks on the gate; only an approval
        reaches SendNewOrderEx; cancel_order is intentionally
        NOT gated.
v0.9.28.1-.3 Live hotfixes. 兆豐 live completion on the box: the
        MEGASECCAPIx64 signing component installed (UAC); four
        live queries tested OK (5-level orderbook quote /
        kline / account summary / orders list); the
        pre-permission host reply (「未授權使用」, big5 decode →
        UnicodeDecodeError) is caught as a clean error message.
v0.9.29.0 Chat clipboard paste. Ctrl+V on the chat input: web
        mode reuses POST /upload; pywebview GUI mode converts
        the clipboard blob to base64 → new bridge
        Api.save_pasted_image writing into the SAME filesystem
        write-sandbox uploads/ dir (magic-byte sniff forces the
        ext, sanitised timestamp + collision-suffix naming,
        ~30 MB cap); the path is inserted as the [已上傳] mark
        line exactly like the 📎 flow so the LLM can read it.
v0.9.30.x Chat inline-image display. (30.0) renderChat() gains
        renderSandboxImages(): [已上傳]-mark lines (4 locales)
        and markdown local-img src are converted to data-URL
        previews INSIDE the bubble via the new bridge
        Api.read_sandbox_image (import∪READ∪WRITE roots, image
        ext whitelist + magic-byte check + 20 MB cap — NEVER an
        arbitrary read; FIFO cache 12; failure placeholder).
        (30.1) bare absolute paths and uploads/-relative paths
        also render + the "Show images in the chat" prompt rule
        forces the LLM to EMBED the path / markdown-image in
        its final message (v0.9.5.2 lesson). (30.2) live-box
        hotfix: preceding-char validation replaces the positive
        separator class so CJK punctuation glued to the path
        (「檔案位置：C:\...」) matches + the prompt now REQUIRES
        saving generated images into the write sandbox
        (Desktop/OneDrive paths can never render) + failed
        previews carry the refusal reason in alt. (30.3) the
        rule names the LITERAL write-sandbox path
        (_write_sandbox_hint() interpolated at prompt build —
        the LLM cannot see env vars).
v0.9.31.0 Telegram sendPhoto. The 4th telegram MCP tool
        send_telegram_photo(chat_id, path, caption?) —
        multipart/form-data upload of a SANDBOX image file
        (import∪READ∪WRITE roots gate via an env-only mirror
        resolver, image ext whitelist + magic-byte check + 10 MB
        Bot API cap, relative paths anchored at the WRITE root,
        allowlist checked BEFORE any HTTP request). Shared
        _bot_post response parser; the stdio `telegram` server
        now advertises 4 tools. The prompt Telegram block gains
        the photo bullet. 26 new tests (test_telegram_photo).

```

The code you see is a sibling project of `milvus-mcp-agent`. Whenever
you wonder "why is X written this oddly", grep `../milvus-mcp-agent/`
— most decisions are inherited verbatim and refined. See §13.

### 1.3 Where data lives

| What | Where | Format | Lifecycle |
|---|---|---|---|
| Vectors + payloads | `qdrant://$QDRANT_URL` (default `127.0.0.1:6333` — v0.9.25.1 rewrites a bare `localhost` to the IPv4 literal; `localhost` pays ~2 s per request on this box) | Qdrant binary collection | Persistent across runs |
| Chunks metadata (UI dump) | Inside the Qdrant payload | `{text, filename, source, content_hash, chunk_index, ...}` | Same as vectors |
| Chat sessions | `$QDRANT_RAG_HOME/sessions/<sid>.json` | JSON, one file per session | Created on save, never auto-deleted |
| Chat offloads (v0.9.10.0+) | `<filesystem read sandbox>/chat_offloads/` (or `QDRANT_OFFLOAD_DIR`) | Plain text per oversized tool result | Created on demand; the LLM reads them back with `read_file` |
| Embedding cache | `$QDRANT_RAG_HOME/embed_cache.db` (or `:memory:`) | SQLite, WAL-mode | Persists across runs, may evict by TTL |
| GUI logs | `gui/app.log` (rotating, 2 MB x 3) | Plain text | Always overwrites old |
| Image description cache | `./cache/images/<sha1>.txt` | Plain text per image | On by default in `DESCRIBE_IMAGES=true` |
| Extracted media | `./cache/media/<...>` | Binary (PDF / DOCX image / etc.) | Same as image cache |

`$QDRANT_RAG_HOME` defaults to `~/.qdrant-rag-agent/`. The cache /
sessions subtree is the right place to back up: `tar czf backup.tgz
~/.qdrant-rag-agent/` is enough to migrate.

---

## 2. Module Map & Public Surfaces

The codebase is shallow but *wide*. There is one file per
responsibility. Each one exports a small, deliberate surface.

### 2.1 `src/config.py`

Reads `.env` once at import. Exposes a `config` singleton with **both**:

* **Flat attrs** — `config.QDRANT_URL`, `config.EMBEDDING_DIM`
  (legacy v0.1.x callers).
* **Namespaced groups** — `config.ingest.chunk_size`,
  `config.embedding.backend` (mirrors the sibling
  `milvus-mcp-agent` so ingest pipelines can be cross-shared).

**When to touch it:** Adding a new env var. Add it in three places or
the round trip will silently fail:

1. Add the parse line to the right namespace class
   (`_IngestNamespace` / `_LLMNamespace` / `_EmbeddingNamespace`).
2. Add a flat alias to `Config` if existing CLI scripts reference
   `config.FOO` directly.
3. Add the default to `.env.example` with a short comment.

**Never** mutate `config` after import. It's read-once by intent — see
§10 on how lazy handles honor env without requiring a restart.

**Raw-string env knobs (v0.9.11+ pattern).** Newer knobs
(`MAX_TOOL_ITERATIONS`, `MCP_ACTIVE_SERVERS`, `OPENROUTER_REASONING_EFFORT`,
`LLM_PROVIDER_CHAIN`, `VISION_PROVIDER_CHAIN`, `OPENAI_CHAT_MODEL`,
`OPENAI_COMPAT_*`) follow a different convention: a raw-string
snapshot attr on `Config` (documentation only) while the consumer
reads `os.environ` / `os.getenv` **directly** at resolve time. This
keeps them monkeypatch-friendly in tests (no `importlib.reload`
needed) and lets resolvers run per call (e.g. `MAX_TOOL_ITERATIONS`
resolves per ask/chat turn). When adding such a knob, mirror both:
the attr (documentation) + the `os.environ` read in the resolver.

### 2.2 `src/vector_store/qdrant_client.py`

The **only** module that talks to Qdrant directly.

| Method | Returns | Notes |
|---|---|---|
| `__init__(url=…, collection=…)` | — | Lazy — does not connect; connection happens on first method call. |
| `_ensure_collection()` | — | Creates the collection if missing, with HNSW + Cosine + 2-segment split. |
| `upsert(points)` | None | Batched. Invalidates per-instance `_list_cache` + `_stats_cache`. |
| `search(query_vector, top_k, filename_filter, score_threshold)` | `list[{id, score, filename, text, source, content_hash}]` | Uses `query_points` (qdrant-client 1.10+). |
| `list_documents()` | `list[str]` (sorted, distinct filenames) | v0.6.1: uses `query_points_groups(group_by='filename', group_size=1)` + 60s TTL cache. |
| `get_stats()` | `{collection, num_entities, dim, mode}` | + 60s TTL cache on `points_count`. |
| `delete_by_source(source)` | `int` (count deleted) | Used by ingest pipeline (re-ingest cleanup). Internal API. |
| `delete_by_filename(filename)` (v0.7.0+) | `int` (-1 if count unknown, else count) | User-facing; matches Qdrant payload `filename` exactly. Pre-flight `count()` avoids the round-trip when nothing matches. |
| `delete_by_filenames(filenames)` (v0.7.0+) | `dict[filename → int]` | Batch over `delete_by_filename`. Each failure short-circuits (so a typo doesn't half-delete). |
| `delete_collection()` | None | |
| `recreate_collection()` | None | Drop + reconnect (used for HNSW param migration). |
| `scroll_by_source(source, limit=10000)` | `list[dict]` | O(N) — only used by dedup pre-checks. |
| `make_id(text)` (static) | `str` | Stable SHA-256 truncation. Use only when deterministic IDs matter for dedup. |

**Caches live here, not in `gui/api.py`.** `gui.api.get_stats()` calls
`store.get_stats()` — turning the cache off requires editing this
module, not the API surface. That's deliberate: cache invalidation
matters at the persistence boundary.

**`Retriever` wraps this.** It handles the *embed-then-search* loop
in `Retriever.search(query)`. Don't call `store.search()` directly
from `Agent` — it would skip the embedder cache.

### 2.3 `src/vector_store/retriever.py`

The thin "embed the query, search Qdrant, return hits" facade.

```python
class Retriever:
    def search(query, top_k=5, filename_filter=None,
               min_score=0.1) -> list[dict]
```

Notice it builds its own `Embedder()` inside `search()` rather than
hoisting it to `__init__` — there's no clear win and the lazy path
keeps cold-start cheap when only `stats` is called.


### 2.4 `src/ingest/loader.py`

Three entry points, deliberately redundant for backward compat:

* **module-level `load_file(path)`** — the v0.1.x API.
* **`FileLoader`** class — the v0.2.3 API, mirrors sibling.
* **`FileLoader.scan_directory(dir)`** — recursive walker that filters
  by `config.ingest.supported_extensions` and respects
  `MAX_FILE_SIZE_MB`.

Supported formats: `.txt`, `.md`, `.pdf`, `.docx`, `.pptx`, `.epub`,
`.py`, `.js`, `.ts`, `.json`, `.yaml`, `.yml`, `.csv`, `.html`,
`.css`, `.xml`. Image extraction (DOCX/PPTX embedded images) is also
here as `extract_media()`.

Returns a structure with `{text: str, images: list[Path],
metadata: dict}`. **Downstream consumers expect this shape** — when
adding a format, preserve it or update all consumers (see §7).

### 2.5 `src/ingest/chunker.py`

Splits a `loader`-returned text into overlapping `DocumentChunk`s.
Default 500-char windows with 50-char overlap (`CHUNK_SIZE`,
`CHUNK_OVERLAP`). Each chunk carries `metadata` (filename, source,
extension, chunk_index, total_chunks, file_size, last_modified) which
becomes the Qdrant payload.

Exports both `chunk_text()` module-level (legacy) and
`TextChunker` class. Always use the class in new code.

### 2.6 `src/ingest/embedder.py`

Three-tier cache, four backends. See §4 for the cache details.

| Backend | Source | Native dim | When to choose |
|---|---|---|---|
| `local` | llama.cpp server at `$LLAMACPP_EMBED_URL` | 4096 (Qwen3-Embedding-8B) | Default. No network, full privacy, free. |
| `ollama` | Ollama Cloud (`OLLAMA_BASE_URL=https://ollama.com/v1`) or local server | 768 (nomic-embed-text), 1024 (mxbai), 384 (all-minilm), 768 (embeddinggemma) | Multiple choices, easy to swap models. |
| `openai` | OpenAI-compatible API | 1536 (`text-embedding-3-small`) | If you have an OpenAI key and want top-tier semantic quality. |
| `hash` | Deterministic fallback | configurable | Last resort. Not semantic — text→vector is word-hash projection. |

Selection is **explicit per collection** because the dim is baked
into the Qdrant collection at create-time — you cannot mix dims in
one collection. Set `EMBEDDING_BACKEND=…` and `QDRANT_COLLECTION=…`,
then `recreate-collection` + `ingest`.

**v0.9.25.3 — llama.cpp server tuning + URL normalisation.** The
`local` backend assumes the server described by
`C:\llama.cpp-vulkan\start-llamacpp-8b.bat` (renamed v0.9.113.2; the
un-suffixed `start-llamacpp.bat` was the 8B primary until v0.9.113.1):
Qwen3-Embedding-8B Q4_K_M,
`--pooling last --embd-normalize 2` (4096-dim), plus the measured
flags `--ctx-size 4096 -ngl 99 -fa on -ub 1024 --threads 12
--cache-ram 0` (single embed 153→128 ms, 64-batch 49.4→43.6 s on the
live box; `--cache-ram 0` disables the server prompt cache whose
eviction churn is also the device-fault suspect). The CPU build bat
is FALLBACK only (449.9 ms single, 64-batch >300 s timeout) and MUST
keep the same pooling or its vectors silently mismatch the
collection. `LLAMACPP_EMBED_URL` is normalised through
`src.config::_normalize_loopback_url` at Embedder construction — a
bare `localhost` value (the `::1`-first loopback trap) is rewritten
to `127.0.0.1`; `[::1]` / LAN IPs / hostnames pass through.

**Public methods:**

* `embed(text) -> list[float]` — single text.
* `embed_batch(texts) -> list[list[float]]` — batched; respects cache.
* `backend_info() -> dict` — for GUI diagnostic tab (backend / dim /
  LRU cache / disk cache stats).
* `cache_clear_all() -> {lru_cleared, disk_cleared}` — wipes both.
* `cache_info() / cache_clear()` — LRU-only.
* `_embed_batch_{local,ollama,openai}` — the three real batched calls.

### 2.7 `src/ingest/disk_cache.py`

SQLite-backed persistent cache. See §4.2.

```python
class DiskEmbeddingCache:
    def __init__(db_path=None, in_memory=False, max_entries=200_000)
    @staticmethod
    def make_key(text, backend, model) -> str
    def get(key) -> list[float] | None
    def put(key, backend, model, vec)
    def has(key) -> bool
    def clear() -> int
    def stats() -> dict
    def evict_lru(target) -> int
    def maybe_evict() -> int
    def close()

def cache_mode_from_env() -> str            # "disk" | "memory" | "off"
def build_cache(mode=None) -> DiskEmbeddingCache | None
```

Env vars:

| Var | Default | Effect |
|---|---|---|
| `EMBEDDING_CACHE` | `disk` | `disk` → SQLite file. `memory` → `:memory:` (tests). `off` → no layer. |
| `EMBEDDING_CACHE_MAX_ENTRIES` | `200000` | Soft cap. Oldest 10% evicted on overflow. |

### 2.8 `src/ingest/image_describer.py`

Calls the vision chain (default: Ollama Cloud → OpenRouter →
SiliconFlow → generic OpenAI-compatible endpoint) to turn images
extracted by `loader.extract_media()` into bilingual
`[English]: ... [中文]: ...` descriptions. Cached by SHA-1 hash of
the image bytes so re-ingesting the same document is free after the
first run.

**v0.9.18.0 — chain-orderable.** `VISION_PROVIDER_CHAIN` reorders
the whole chain (comma list; empty = the legacy order above — zero
behaviour change). `_vision_candidates()` returns the FULL chain
including the Ollama entry; Ollama is no longer a hard-coded
primary, just the first entry of the default chain. `_chain_names()`
reads the env via `os.getenv` through the shared
`src/provider_chain.py` parser (unknown tokens skipped with a
warning; duplicates collapse; partial lists keep failover
completeness; aliases `ollama-cloud` / `openai-compat` accepted).

`ImageDescriber.enabled` is `True` when ANY chain candidate has a
key configured (`OLLAMA_API_KEY` / `OPENROUTER_API_KEY` /
`SILICONFLOW_API_KEY` / `OPENAI_API_KEY`) — `False` only when no
key at all is set; no-op in that case. The pipeline still runs the
loader + chunker steps without filling image placeholders.
`last_backend` records which provider answered
(`ollama-cloud` / `openrouter` / `siliconflow` / `openai-compat`).


### 2.9 `src/ingest/pipeline.py`

Glues the previous three together. One method per public call:

| Method | Returns | Used by |
|---|---|---|
| `ingest_file(path)` | `int` (chunks added) | `cli.py ingest <file>`, GUI Ingest tab, MCP `ingest_file` |
| `ingest_directory(dir)` | `dict[filename → int]` | `cli.py ingest <dir>` |
| `__init__()` | — | Builds all sub-modules lazily; connections are sub-module responsibility. |

Pipeline contract:

1. Delete old chunks for that source (idempotent re-ingest).
2. Load -> fill images -> chunk -> embed -> upsert.
3. Per-batch size `EMBED_BATCH_SIZE=64` and `INSERT_BATCH_SIZE=500`.
4. **Per-file try/except** — one bad file never aborts a directory
   run. Errors are logged with file basename.

`_SKIP_EXTS` is the canonical "do not even try to ingest this" list.
Add binary / archive / font formats there when you encounter them.

### 2.10 `src/agent/agent.py`

```python
class Agent:
    def ask(question, top_k=5, temperature=0.3,
           use_web_search=False, web_max_results=5,
           use_tools=True, max_tool_iterations=None,
           response_language=None, active_tool_names=None) -> str
    def chat_turn(question, history, top_k, temperature,
                  use_web_search, web_max_results,
                  use_tools=True, max_tool_iterations=None,
                  response_language=None, active_tool_names=None)
                  -> tuple[answer, context_md, sources_list]
    def shutdown() -> None
```

`ask()` is single-turn (used by `cli.py ask`). `chat_turn()` is
multi-turn, returns the rendered context for the GUI sources panel
and the list of filenames to display.

`max_tool_iterations` (v0.8.0+; v0.9.14.1+) — the tool-loop
iteration budget for THIS call. `None` (the default) resolves per
call via `_resolve_max_tool_iterations()`: the `MAX_TOOL_ITERATIONS`
env var when it parses to a positive int, else
`Agent.DEFAULT_MAX_TOOL_ITERATIONS` (= **20**; the historical 6
aborted every windows-mcp desktop-automation task mid-drawing —
log forensics in the v0.9.14.1 arc). An explicit int still wins.
When the budget is exhausted the loop injects one final user turn
asking the LLM to report which steps completed / which step is
next / invite 「繼續」 — never to claim unfinished work is done.
The `tools_enabled=True` system prompt also carries the
"Windows desktop automation (windows-mcp)" trigger block: the
App launch ladder (mode=launch → mode=launch_executable with the
FULL absolute path INCLUDING `.exe` → `PowerShell Start-Process`),
an absolute-paths-only rule, scripted drawing preferred (ONE
PowerShell System.Drawing call renders the whole picture; mouse
Click/Move drag only on explicit 「用滑鼠畫」 requests), and
budget discipline. `tests/test_max_tool_iterations.py` locks down
the resolver, the per-call resolution, the bail-out shape, and
the prompt block (present in tools=True, absent in tools=False).

**Provider dispatch (v0.8.9+; 5 providers since v0.9.19.0).**
`_PROVIDER_ORDER` is a data-driven tuple of
`(name, key_fn, url_fn, model_fn)` entries —
`ollama → openrouter → siliconflow → openai → openai-compat` — all
served through ONE `openai.OpenAI(api_key, base_url)` client.
Resolution (`_pick_provider()`):

1. `config.LLM_PROVIDER` forced override wins (raises a helpful
   error when the key is empty, naming e.g.
   `OPENAI_COMPAT_API_KEY` via `.upper().replace('-', '_')`).
2. Otherwise auto-detect walks the RESOLVED chain and picks the
   first provider with a non-empty key.
3. No key anywhere → raise with the full env-var menu
   (now incl. `OPENAI_COMPAT_API_KEY`).

**Chain order (v0.9.18.0+).** `_resolved_provider_order()` reorders
`_PROVIDER_ORDER` by `LLM_PROVIDER_CHAIN` (read from `os.environ`
per call, monkeypatch-friendly). Both `_pick_provider()` (primary
selection) and `_provider_candidates()` (the v0.9.17.0 cross-
provider failover walk after 2 primary attempts) iterate the
resolved chain; `LLM_PROVIDER` still wins for PRIMARY selection —
the chain only reorders auto-detection and the failover walk.

**OpenAI-compatible escape hatches (v0.9.19.0+).** The `openai`
provider IS the generic compat slot (`OPENAI_BASE_URL` points
anywhere: DeepSeek / LM Studio / vLLM / Together / a company proxy)
and now has a DEDICATED `OPENAI_CHAT_MODEL` — priority: non-default
`LLM_MODEL` still wins, unset = `LLM_MODEL` (zero behaviour
change). Setting it matters because a non-default `LLM_MODEL`
globally overrides the other providers' chat models and would
break cross-provider failover. The 5th provider `openai-compat`
(`OPENAI_COMPAT_API_KEY` / `OPENAI_COMPAT_BASE_URL` REQUIRED /
`OPENAI_COMPAT_CHAT_MODEL`) is a fully independent second compat
endpoint that coexists with `openai` and joins the chain
automatically; key-without-URL raises in `_pick_provider` and is
skipped (with a warning) in the failover walk.
**Task-level model pin (v0.9.110.0+).** A background task or a
compiled plan stage can pin its OWN chat model via
`task_llm="provider:model"` — provider whitelist
(openrouter | ollama), validated at SUBMISSION time against the
LIVE catalogs (OpenRouter `/api/v1/models` ≈ 466 ids; Ollama
`/api/tags` with the `/v1` suffix auto-stripped + the `-cloud`
alias normalised to the catalog id; both TTL-cached 600 s in
`src/agent/task_llm.py` — a ZERO-side-effect module in the
`provider_chain.py` discipline). Semantics when pinned:
`_pick_provider()` honours the pin VERBATIM (no
`LLM_PROVIDER`/chain-order/model-resolution applies),
`_provider_candidates()` collapses to the pinned triple only
(cross-provider failover is DISABLED — a fallback would silently
breach the explicit model choice; the task fails instead), and
the pinned OpenRouter turn ships a CLEAN `extra_body` (the
global routing block is tuned for the DEFAULT model and would
404 a pinned model by filtering out its own endpoints). Unknown
model = rejected at the spawn site (nothing starts) with
closest-match suggestions; catalog unreachable = accepted with a
warning (the provider 404s at turn time). Discovery tool:
`list_task_models(provider?, contains?)` on `task-local`.

`active_tool_names` (v0.9.14.0+) — the per-TOOL allow-list from
the GUI chat tool picker; overrides the `MCP_ACTIVE_SERVERS` env
server gate for this request (`None` = env gate applies).

`response_language` (v0.8.5+) is an optional I18N code (`zh-TW`,
`zh-CN`, `en`, `ja`). When set, `_system_prompt()` appends a
LANGUAGE DIRECTIVE block telling the LLM to answer in that
locale. The directive is appended *after* the CITATION RULES
block and explicitly carves out an exception for filenames,
library names, and code identifiers — so citation integrity
isn't broken. Default `None` (v0.8.4 behaviour) emits no
directive and lets the LLM pick.

Prompt construction lives in `_build_context()` (Markdown context
with `### Local Documents` and optionally `### Web Search Results`)
and `_system_prompt()` (instructions + citation rules + optional
language directive + **v0.8.6 scheduler trigger block**).

The v0.8.6 scheduler trigger block lives in the
`tools_enabled=True` branch of `_system_prompt()`. It tells the
LLM:

* 「排程」「every day at」「cron」「明天下午 3 點提醒我」…
  → call `add_scheduled_task` (cron / interval_seconds /
  once_at).
* 「我有哪些排程」 → `list_scheduled_tasks`.
* 「停掉」「暫停」「disable」 → `disable_scheduled_task`;
  「啟用」「resume」 → `enable_scheduled_task`.
* 「刪除」「remove」 → `delete_scheduled_task` (id or name).
* 「為什麼 X 失敗」「show errors」 → `get_scheduled_task_errors`.
* 「立刻跑 X」 → `run_scheduled_task_now`.

**v0.8.7+** Schedule-progress trigger block (added in the
same `tools_enabled=True` branch, right after the v0.8.6
management block — same author style, same zh-TW + en +
romanised mix):

* 「這個排程跑幾次了」「fire count of X」 → `get_scheduled_task_status`
  (the `fire_count` / `max_fires` / `fires_remaining` fields
  answer this in one roundtrip — no need to scrape `history.jsonl`).
* 「還能跑幾次」「how many more fires」「fires remaining」 → also
  `get_scheduled_task_status` (`fires_remaining`).
* 「這個排程過期了嗎」「has X expired」 → `get_scheduled_task_status`
  (`status="expired"` + `expires_at` + `seconds_until_expiry`).
* 「上次排程什麼時候跑的」「when did X last fire」 → again
  `get_scheduled_task_status` (`last_fire_at`).
* 「上次跑得怎樣」「show me the last N runs」 →
  `list_scheduled_task_runs` (truncated answer preview + error
  flag; full answer via `last_session_id`).
* 「為什麼 X 停了」 → diagnostic decision tree the LLM should
  follow without guessing: `status="expired"` → expires_at has
  passed; `status="exhausted"` → fire_count >= max_fires;
  `status="disabled"` / `"failed"` → check `last_error` or call
  `get_scheduled_task_errors`.

The legacy (`tools_enabled=False`) prompt deliberately omits
both the v0.8.6 and the v0.8.7 blocks — the LLM has no MCP
tool surface at all, so listing tools it can't call would
mislead it into suggesting calls that error out.
`tests/test_system_prompt_schedule_progress.py` locks down
the asymmetry (block present in `tools_enabled=True`, absent
in `tools_enabled=False`) and the composition with
`response_language` (LANGUAGE DIRECTIVE block + progress
trigger block both render in the same prompt without
colliding).
this block — it would mislead the LLM into suggesting tool
calls it can't actually make. `tests/test_system_prompt_scheduler.py`

The v0.8.6.2 Telegram / external messaging trigger block lives
in the same `tools_enabled=True` branch (added right after the
Schedule block). It teaches the LLM:

* 「用 telegram 發」「send via telegram」「傳到 telegram」
  「notify me on telegram」「發訊息給 chat_id」… → call
  `send_telegram_message` (plain) or `send_telegram_markdown`
  (formatted) with the explicit `chat_id` (int) and `text` /
  `markdown` argument.
* The bot can ONLY send to chats whose id is in
  `TELEGRAM_ALLOWED_CHAT_IDS` — surface the error if the user
  supplies a chat_id outside the allowlist (do not silently
  retry).
* If the user is unsure of their chat_id, call
  `list_allowed_chats` first.
* **v0.9.31.0+** — to push a GENERATED / sandbox image file to an
  allowed chat, call `send_telegram_photo` with the file's sandbox
  path (PNG/JPEG/GIF/WEBP/BMP, ≤10 MB; the path must be inside the
  filesystem sandbox — Desktop / OneDrive files must be copied in
  first). Add a short `caption` (≤1024 chars) for text under the
  photo.
* **Telegram + Schedule composition (the common trap)** — a
  dedicated sub-block that names the exact "schedule fires a
  prompt mentioning Telegram → you MUST call the Telegram tool
  inside that prompt, not just describe the message in prose"
  scenario. Without this hint, the LLM would respond with a
  plain-text confirmation that "the schedule has been created"
  and never actually invoke `send_telegram_message` from inside
  the fired prompt — which is exactly what happened in the
  2026-08-29 user log (`answer=0 chars` after each fire).

The legacy `tools_enabled=False` prompt deliberately omits the
Telegram block too (same reasoning as the Schedule block).
`tests/test_system_prompt_telegram.py` asserts the asymmetry +
the `response_language` composition + that the Telegram block
does not displace the Schedule block.


asserts the asymmetry.

**The Agent does not import any CLI/GUI code.** It only knows about
`Retriever` and `OllamaWebSearch`.

### 2.11 `src/agent/session.py`

JSON session store at `$QDRANT_RAG_HOME/sessions/`. `SessionManager`
lists / loads / saves / deletes files; `Session` is a dataclass with
`{id, title, created_at, messages}`. The GUI Sessions tab and the
`cli.py sessions` sub-command both use this module.

No caching — sessions are tiny JSON files; load by `id` is instant.
`SessionManager.rename` (v0.9.103.5+) rewrites ONLY the title —
messages / created_at / updated_at untouched, the long-term-memory
re-ingest skipped (`SessionManager.save` gained keyword-only
`ingest` / `touch` kwargs whose defaults keep the old behaviour).

**v0.9.20.0+ — real-time save timeline (gui/assets/app.js, shared
GUI/web).** `autoSaveChat()` fires at every mutation point, not
just on reply: send-time (the user's message is persisted BEFORE
the LLM call — a crash / tab close during a long tool-loop turn
keeps the question) → reply-time (usage / time header) → error
bubbles → refresh/close via `navigator.sendBeacon` (web mode) →
process crash via the localStorage `chat_draft_backup` +
`bootDraftRecovery()`. The LLM can also query this store at
runtime — see the ChatHistoryLocalHost (§2.14b, v0.9.21.0+).

### 2.12 `src/tools/web_search.py`

Ollama's hosted `/api/web_search` and `/api/web_fetch`. Both are
*opt-in* — `Agent` only calls them when `use_web_search=True`. When
the API key is unset, every call returns `[None]` silently so the
agent can keep working offline.

### 2.13 `src/ui/md_render.py` (v0.2.7+, v0.8.3 full GFM)

Zero-dep ANSI-aware Markdown renderer. Used by `cli.py ask` and
`chat` REPL to print LLM answers without breaking the BBS-style box
frame in narrow terminals. The GUI does **not** use this — it has
`marked.js` + `DOMPurify` in `gui/assets/` (see §2.13b).

**Public surface:**

```python
def render_markdown(text: str) -> str
def _strip_ansi(text: str) -> str     # public for tests / width calc
def _wcswidth(s: str) -> int          # CJK-aware column width (used by table renderer)
```

**GFM coverage (v0.8.3+):** headings (h1-h6 with ▌ marker + per-level
SGR), GFM tables (CJK-width-aware ANSI boxes — added v0.2.7),
fenced code blocks (with optional `language` tag), inline code,
**bold** / *italic* / ~~strikethrough~~ (the latter two use U+2002 /
U+2003 sentinels because Windows `cmd` doesn't render SGR italic /
strike), bullet lists (`•` / `◦` / `▪` for depth 0/1/2), **numbered
lists** (auto-incrementing per indent depth), **task lists**
(`- [ ]` / `- [x]`), blockquotes (green `│` prefix), horizontal rules
(--- / *** / ___), inline links `[label](url)` (cyan + underline label
followed by plain URL), inline images `![alt](url)` (`[image: alt](url)`),
and backslash escapes (`\*`, `\_`, `` \` ``, `\|`, `\[`, `\<`,
`\>`, `\#`, `\+`, `\-`, `\.`, `\!`, `\\` — escaped chars are stashed
in the Unicode Private Use Area U+E000–U+E001 so subsequent italic /
bold regexes don't re-match them).

**Order in `render_markdown()`:** fenced code → blank → hr →
gfm-table → heading → blockquote → task → ordered → bullet →
default. Ordered numbering is tracked per-indent (`dict[str,int]`)
so nested ordered lists restart at 1 instead of continuing the outer
sequence. Bullet and ordered lists preserve leading indent so the
BBS box can wrap nested items without breaking the box.

**Style constants** (`Style` class): `RESET` / `BOLD` / `UNDERLINE` /
`ITALIC` / `STRIKE` / `DIM` / `CYAN` / `BLUE` / `GREEN` / `YELLOW` /
`RED` / `GREY` (note: v0.2.7 had `GREY = "\x90m"` — a bare ESC bug;
fixed to `"\x1b[90m"` in v0.8.3).

**Color auto-detection:** `_USE_COLOR` is False when `sys.stdout` is
not a TTY (CI / pytest), in which case `_c()` returns the bare text.
Tests use `_strip_ansi()` to compare readable content.

### 2.13b GUI Markdown rendering (v0.8.3+ full GFM)

The GUI does not use `src/ui/md_render.py`. Instead, the chat
rendering path is:

```
markdown text
   ↓ marked.js (CDN: marked@12, pinned)
HTML string
   ↓ DOMPurify.sanitize() (CDN: dompurify@3, pinned)
sanitized HTML
   ↓ hardenLinks() — post-processing pass
final HTML → innerHTML on .chat-msg.assistant div
```

**`md(text)`** in `gui/assets/app.js` calls `marked.parse(text, {
gfm:true, breaks:true, headerIds:false, mangle:false })` —
`headerIds:false` is critical because the chat log is a scrolling
container; auto-generated heading anchors triggered unwanted scroll
jumps when the LLM emitted multi-section answers. `mangle:false`
disables the obfuscation of autolinks that breaks preformatted
Markdown rendering.

**Presence duck-typing (v0.8.4 fix):** the engine-presence check is

```js
typeof window.marked !== 'undefined'
  && typeof window.marked.parse === 'function'
typeof window.DOMPurify !== 'undefined'
  && typeof window.DOMPurify.sanitize === 'function'
```

NOT `typeof === 'object'`. **This is a load-bearing detail.**
DOMPurify 3.x exposes itself as a named *function* with static
methods attached (`DOMPurify.sanitize`, `DOMPurify.setConfig`,
…). The older `typeof window.DOMPurify === 'object'` check
silently returned false and the renderer fell back to plain
text — users saw raw `**xxx**` asterisks in the chat log
without any error. `marked.parse` is also exposed via a
namespace object so the same duck-type pattern works for both.

**Vendor fallback (v0.8.4):** `gui/assets/vendor/marked.min.js`
(35 KB) and `gui/assets/vendor/purify.min.js` (21 KB) are checked
into the repo at the exact versions pinned in `index.html`
(marked@12.0.2 / dompurify@3.0.11). `index.html` loads the
**local vendor first** with a `<script onerror>` chain to the
jsdelivr CDN as a secondary path. Local-first because:

* jsdelivr is blocked in some regions (CN corp networks, certain
  VPN setups).
* The GUI runs in an Electron-style WebView2 process that may not
  have network access in offline-first deployments.
* Pinning the exact vendor version prevents surprise CDN
  upgrades breaking the DOMPurify API contract.

**Failure diagnostics:** when both vendor and CDN fail, `md()`
logs a one-shot `console.warn('[markdown] unable to load: …')`
and pops a toast — the user is told the renderer is degraded
instead of silently seeing `**xxx**` raw text. The fallback
HTML still escapes `<`, `>`, `&` so it's XSS-safe.

**DOMPurify config** (extended v0.8.3 to cover full GFM):

* `ALLOWED_TAGS` — adds `input` (GFM task-list checkboxes),
  `del`/`s` (strikethrough), `hr` (horizontal rule), `img` (inline
  image with `src`+`alt`), `figure`/`figcaption` (image captions),
  `ins`/`mark`/`small`/`sub`/`sup`/`span` (rich inline text).
* `ALLOWED_ATTR` — adds `src`/`alt`/`width`/`height`/`loading` for
  images, `type`/`checked`/`disabled` for checkboxes, `class`/`id`
  for CSS hooks, `target`/`rel` for safe links.
* `ALLOWED_URI_REGEXP` — explicitly rejects `javascript:` URIs in
  link `href` / image `src` even though the default would already
  strip them.
* `ADD_ATTR: ['target','rel']` — re-allows the link attrs that
  DOMPurify strips by default in newer versions.

**`hardenLinks(html)`** post-processing pass sweeps every `<a>` tag
and appends `target="_blank" rel="noopener noreferrer"` unless the
link already has a `rel=` attribute. Without this, DOMPurify's
default config strips `target`/`rel` and the browser reuses the
current tab — a tabnabbing risk. The function is a 5-line regex
`replace`, called once per `md()` invocation, and is the second
sweep so it sees the post-sanitize HTML.

**`_mdStartupCheck()`** (v0.8.4): runs once at boot. If either
marked or DOMPurify is missing, it fires the `md('')` warning
path proactively so the user sees the diagnostic toast even if
no chat message has been sent yet.

**Markdown test coverage:**

* `tests/test_md_render.py` — 27 unit tests for the CLI regex
  engine (8 baseline regression guards + 19 new GFM element
  tests covering numbered/nested/task lists, italic, strike,
  link, blockquote, hr, escape, image, and edge cases).
* `tests/test_gui_markdown_e2e.py` — 11 JSDOM-driven e2e tests
  that load the **actual vendor binaries** via `<script src=>`
  (not `eval`, which would put marked into CommonJS export mode
  and break the UMD attach-to-window contract). The harness
  extracts `md()` + `hardenLinks()` verbatim from
  `gui/assets/app.js` and asserts every GFM element renders
  correctly. There is also a "fallback path" test that deletes
  both libs from `window` and verifies the warning fires and
  the fallback output is XSS-safe even when the renderer is
  completely absent.

The two test files together cover the full Markdown rendering
pipeline end-to-end: zero-dep CLI renderer + browser-native GUI
renderer with the vendored fallback chain.

### 2.14 `src/mcp_server/server.py`

External MCP server (stdio). **Never imported by `Agent` / `cli.py`
/ `gui/api.py`** — it's there for external MCP clients (Claude
Desktop, Cline, …) to consume. Run with
`python -m src.mcp_server.server`. Exposes 5 tools:
`search`, `ingest_file`, `ingest_directory`, `list_documents`,
`stats`.

### 2.14c `src/agent/scheduler.py` (v0.8.0+)

Schedule manager + daemon-thread scheduler for cron / interval /
one-shot triggers. Backed by ``$QDRANT_RAG_HOME/schedules/<id>.json``
(atomic write, mirroring ``SessionManager``).

```
src/agent/scheduler.py
├── _parse_field / cron_matches / next_cron_fire
│   (5-field cron: `*` `*/n` `a-b` `a-b/n` `a,b,c`)
├── class Schedule (dataclass, 21 fields — v0.8.6 added last_error / last_error_at; v0.8.7 added max_fires / expires_at / status)
├── class ScheduleManager (CRUD over JSON files)
│   ├── v0.8.6: set_enabled / clear_error / record_error / find_by_name
│   └── v0.8.7: get_progress / list_runs / set_max_fires / set_expires_at
└── class Scheduler (daemon thread, 1s tick, two-phase lifecycle gate)
```

**Three schedule types:**

| Type | When it fires | Auto-disable |
|---|---|---|
| `interval_seconds` | every N seconds (anchored on last fire to avoid drift) | only via lifecycle cap (v0.8.7) |
| `cron` | 5-field cron (minute hour dom month dow) | only via lifecycle cap (v0.8.7) |
| `once_at` | one ISO-8601 timestamp | yes — after fire |

**Fire path** (`make_agent_on_fire(agent_factory)`):

1. Build (or reuse) a dedicated ``Session`` named
   ``sched-<id>-<fire-ts>``. Append ``{user, assistant}`` turns.
2. Call ``agent.chat_turn(prompt, history=..., use_web_search=...,
   use_tools=True)`` — full tool surface, including MCP servers
   and Ollama Web Search.
3. Update lifecycle fields (``last_fire_at``, ``fire_count``,
   ``last_session_id``, next ``next_fire_at``).
4. Append a JSON line to ``<id>.history.jsonl`` (ring-buffered to
   50 entries).

**v0.9.67.1/67.2 fire-context hardening:** repeating a fixed prompt
over the session's FULL accumulated history taught the model to
parrot old replies — a 14-fire-old cron schedule fabricated
「✅ 已成功發送至 Telegram」 with ZERO tool dispatches (log
2026-09-16 15:50). Three layers now:

1. **Fire history cap** — `QDRANT_SCHEDULE_FIRE_HISTORY` (default
   **0** = every fire is an INDEPENDENT run; the session file still
   accumulates the full transcript, only the LLM context is fresh;
   `2` = one prior Q/A pair; `-1` = legacy full history).
2. **Fire memory suppression** — `QDRANT_SCHEDULE_FIRE_MEMORY`
   (default **off**): fires ask for TODAY's data, so the per-turn
   long-term-memory recall (which contains earlier fires' ingested
   `web_search` results = OLD prices) is skipped via the
   `chat_turn(use_memory=False)` switch (v0.9.67.2; None = default
   recall for normal chat).
3. **Telegram-send integrity guard** — when the prompt asks for a
   Telegram send, the reply CLAIMS a send (regex), and the turn's
   `agent.last_tool_dispatches` (plain `list[str]` of tool names,
   v0.9.57.1 mechanism) contains NO `send_telegram_*` entry, ONE
   corrective `chat_turn` runs (no re-analysis; forced
   send_telegram_message directive) and its answer replaces the
   original in the session + history. Corrective failure degrades
   to the original + WARNING. Dispatch parsing is str-first
   (dict/attribute tolerated) — the v0.9.67.1 dict-only parser
   misjudged real sends and caused a duplicate send.

**Daemon lifecycle:**

* Lazy — ``Agent.scheduler`` is built on first access.
* ``Agent.start_scheduler()`` / ``Agent.stop_scheduler()`` control
  the daemon thread.
* ``Agent.shutdown()`` calls ``Scheduler.stop(timeout=2.0)``.
* Tick error / on_fire exception is caught + logged — never
  tears down the daemon. **v0.8.6**: the same exception is also
  recorded on the schedule via `mgr.record_error(sid, e)` so
  the LLM can later answer "為什麼 X 失敗了". The
  `last_error` / `last_error_at` fields are persisted across
  daemon restarts (atomic JSON write, same as the rest of the
  schedule). `make_agent_on_fire` is also wrapped: if
  `agent.chat_turn` itself raises (Qdrant down, OpenAI 5xx,
  network glitch), `record_error` runs before the exception
  propagates back to the daemon's outer try/except.

**Lifecycle caps (v0.8.7+):** Two optional terminal-state
conditions that auto-disable a schedule without the user
needing to call `disable_scheduled_task`:

| Field | When tripped | Stamped status |
|---|---|---|
| `max_fires: int \| None` | `fire_count >= max_fires` | `"exhausted"` |
| `expires_at: str \| None` (ISO-8601) | `now >= expires_at` | `"expired"` |

Both are evaluated inside `Schedule.compute_next_fire(after=)` at
the top of every tick (phase 2 of the two-phase gate), so a
schedule that just hit its cap or expired overnight is caught
on the next scheduler pass — no manual cron-watching required.
`None` for either field means "no cap" (default; legacy
v0.8.6 JSON files load with `None` and keep running).

A schedule with `max_fires` set but `fire_count > max_fires`
(e.g. the cap was lowered after some fires already happened)
auto-disables on the **next save** — `ScheduleManager.set_max_
fires` runs through `compute_next_fire` which flips status to
`"exhausted"` before the write lands. Same path for
`set_expires_at`.

`make_agent_on_fire` stamps a separate `status="failed"` when
`chat_turn` itself raises, so a fatally broken schedule (e.g.
LLM key revoked, Qdrant permanently down) can't loop forever
on the next tick. `last_error` / `last_error_at` carry the
rejection reason as before.

**LLM progress tracking (v0.8.7+):** Two read-only
`ScheduleManager` methods back the new MCP tools so the LLM
can answer lifecycle questions without scraping
`history.jsonl`:

```python
# One roundtrip; LLM gets everything it needs:
prog = mgr.get_progress(sid)
# {
#   "id": "...", "name": "...", "status": "active",
#   "fire_count": 4, "max_fires": 10, "fires_remaining": 6,
#   "next_fire_at": "...", "seconds_until_next_fire": 47,
#   "expires_at": "2026-12-31T23:59:59",
#   "seconds_until_expiry": 86400 * 5,
#   "last_fire_at": "...", "last_error": None,
#   "recent_runs": [...5 most recent history rows...],
# }

# Pull a larger / unbounded history tail:
runs = mgr.list_runs(sid, limit=50)
# [ {"ts", "prompt", "answer" (≤500 chars), "sources",
#    "session_id", "error"}, ...]
```

`get_progress` re-evaluates `compute_next_fire(after=now)`
before serialising so a stale `status` on disk (e.g. hand-
edited JSON, or status never re-stamped after a cap trip)
gets refreshed on read. The method does NOT write the file —
the daemon's `tick_once` does that. This keeps `get_progress`
idempotent and side-effect-free, safe for repeated chat-side
calls.

`_tail_history` reads the last `limit` lines of
`<sid>.history.jsonl` (no full-file load, so schedules that
have fired thousands of times over weeks still respond in
single-digit ms). Answers are truncated to 500 chars +
ellipsis on longer runs; the full answer lives in
`last_session_id` if the LLM wants more context.

**GUI bridge:**

8 new methods on ``gui/api.py``:
``list_schedules`` / ``add_schedule`` / ``update_schedule`` /
``delete_schedule`` / ``run_schedule_now`` /
``get_schedule_history`` / ``start_scheduler`` /
``stop_scheduler``. All catch exceptions and return
``{"error": "..."}``. The Schedules tab in the GUI uses the
same modal-style pattern as the MCP editor.

**CLI (`cli.py schedules`, v0.8.7+ lifecycle flags):** Two new
optional flags on `schedules add`:

| Flag | Purpose | Validation |
|---|---|---|
| `--max-fires N` | total fire count cap | `N` must be a positive integer |
| `--expires-at ISO` | hard expiry ISO-8601 timestamp | parsed via `datetime.fromisoformat`; `Z` suffix accepted |

Both default to `None` (no cap) so existing CLI invocations
from v0.8.6 keep working unchanged. Validation happens
up-front in `_handle_schedules` so the user sees a clean
`[錯誤]` line on stderr and exits with `rc=2` — never a
half-created schedule that silently auto-disables (the
`max_fires=0` trap: `fire_count >= 0` is true on day one, so
the schedule would never fire). `schedules list` adds a
`STATUS` column (renders `active` / `expired` / `exhausted` /
`disabled` / `failed`); `schedules show` prints the new
fields (`status` / `max_fires` / `expires_at` / `last_error`
/ `last_error_at`) so the user can see lifecycle state at a
glance without opening the JSON file. No new CLI sub-commands
needed — `add` / `list` / `show` already cover the full
lifecycle surface.

**Auto-start on LLM add (v0.8.6.3):** Without an explicit
start call, the daemon thread would never spin up just because
the LLM wrote a schedule via `add_scheduled_task`. The chain:

1. `SchedulerLocalHost._add` (host.py) gains an `_auto_start:
   Callable[[], None] | None` slot + `attach_auto_start(cb)`
   setter. After a successful save, `_add` invokes
   `self._auto_start()` inside a try/except — a misconfigured
   callback cannot poison the success response (the schedule is
   already on disk; the user can manually start the daemon via
   GUI / CLI to recover).
2. `McpManager` (manager.py) carries `auto_start_callback: Callable
   | None` (set by `Agent`) + `_scheduler_local_host: Any | None`
   (kept by `from_config` so the host reference survives after
   the manager is built) + a canonical `set_auto_start_callback
   (cb)` setter that pushes the callback down to the host. This
   resolves the race where `from_config` finishes registering the
   host before `Agent.mcp_manager` lazy property has wired the
   callback.
3. `Agent.mcp_manager` lazy property (agent.py) wires
   `set_auto_start_callback(self._auto_start_scheduler_daemon)`
   on first access. The daemon kicker wraps
   `self.start_scheduler()` in a try/except (exceptions are
   swallowed because the schedule is already on disk).

**Properties of the auto-start mechanism:**

* **Idempotent** — `Scheduler.start()` no-ops when the thread
  is alive; multiple auto-starts don't leak threads.
* **Lazy** — no daemon thread until the first
  `add_scheduled_task` succeeds; sessions that never schedule
  don't pay for an idle thread.
* **Best-effort** — callback exceptions are swallowed.
* **GUI / chat only** — the CLI `schedules daemon` subcommand
  still works the old way; auto-start is opt-in for the in-process
  Agent paths.

### 2.14d `src/mcp_server/scheduler_server.py` (v0.8.6+)

Third stdio MCP server. Wraps `ScheduleManager` as **10** MCP
tools (8 from v0.8.6 + 2 progress tools from v0.8.7) so an
LLM agent can drive the scheduler conversationally
("幫我排程...", "為什麼 X 失敗了", "立刻跑 Y",
"這個排程跑幾次了", "過期了嗎"). Same stdlib-only /
zero-dep philosophy as the Telegram server:

```
python -m src.mcp_server.scheduler_server
```

| Tool | Purpose |
|---|---|
| `add_scheduled_task` | register cron / interval / once_at; **v0.8.7+** accepts optional `max_fires` + `expires_at` in the inputSchema; returns `id` + `next_fire_at` |
| `delete_scheduled_task` | by id or human-readable name |
| `list_scheduled_tasks` | flat array with one-word `status` per task (armed / paused / errored / completed / idle) |
| `get_scheduled_task` | full detail incl. `last_error` + `last_error_at` |
| `enable_scheduled_task` / `disable_scheduled_task` | toggle with auto `next_fire_at` recompute |
| `run_scheduled_task_now` | synchronous fire, bypassing the daemon |
| `get_scheduled_task_errors` | history.jsonl filter by `since` / `limit` |
| **`get_scheduled_task_status`** *(v0.8.7+)* | one roundtrip lifecycle snapshot (status / fire_count / max_fires / fires_remaining / next_fire_at + seconds_until / expires_at + seconds_until_expiry / last_error / last 5 runs). Backs the LLM's "跑幾次了" / "還能跑幾次" / "過期了嗎" questions |
| **`list_scheduled_task_runs`** *(v0.8.7+)* | tail of `<sid>.history.jsonl` (newest last, `limit` 1-100, default 10; answer truncated to 500 chars + ellipsis). Backs "上次跑得怎樣" / "show me the last N runs" |

Validation lives in `_validate_add_args` (`prompt` required,
`schedule_type` ∈ {cron, interval_seconds, once_at}, `cron`
must be 5 fields, `interval_seconds` ≥ 5, `once_at` ISO-8601).
**v0.8.7+** validates `max_fires` (must be positive int or
`null`) and `expires_at` (must parse as ISO-8601 with optional
`Z`-suffix) up-front so a bad call returns a clean error
instead of creating a no-op schedule that auto-disables
immediately. The stdio server is **not** the default — the
in-process `SchedulerLocalHost` (§2.14e) ships in
`McpManager.from_config` catalog so most users never need to
spawn this subprocess. It exists so external MCP clients
(Claude Desktop, Cline) can drive the same schedule files
without going through the divine-sense-agent chat loop.

### 2.14e `SchedulerLocalHost` (`src/mcp_client/host.py`, v0.8.6+)

In-process mirror of §2.14d — same **10** tool names (8 from
v0.8.6 + `get_scheduled_task_status` + `list_scheduled_task_runs`
from v0.8.7), same payload shapes, but no stdio subprocess /
no JSON-RPC overhead. The LocalHost delegates to
`ScheduleManager` directly via shared `_resolve_id_or_name` /
`_validate_add_args` / `_status_label` helpers imported from
the stdio server module — so the two surfaces can't drift.

**v0.8.7+ add path** (`SchedulerLocalHost._add`) forwards
`max_fires` / `expires_at` to the `Schedule` constructor after
up-front validation (positive int / parseable ISO-8601).
Validation lives in `_add` itself (not in the dataclass) so a
bad call returns `{"error": "'max_fires' must be ..."}` without
half-creating a schedule — the on-disk state is clean.

Auto-registered by `McpManager.from_config(include_local_hosts=True)`,
so the Agent's `openai_tools()` listing already includes the
scheduler the moment the manager is built — no flag, no
opt-in. The LLM only sees the tool names / descriptions;
both surfaces look identical from its perspective.

`attach_manager(mgr_or_root)` accepts a `ScheduleManager`,
a path string, a `Path`, or any object — invalid types fall
back to a default-constructed manager (graceful degradation).

### 2.14f `src/agent/webhook.py` (v0.9.25.0+)

Webhook triggers + HTTP listener — the event-driven counterpart to
§2.14c. Mirrors the scheduler's persistence/lifecycle shape:

* `WebhookTrigger` dataclass — every trigger carries its OWN random
  `access_key` (`secrets.token_urlsafe(32)`; constant-time
  compared via `hmac.compare_digest`) and the Schedule-parity
  lifecycle gates (`enabled` / `status` / `max_fires` → exhausted /
  `expires_at` → expired; `status="failed"` stamped when the fire's
  `chat_turn` raises).
* `WebhookManager` — atomic JSON in
  `$QDRANT_RAG_HOME/webhooks/<id>.json` + `<id>.history.jsonl`
  (50-entry ring).
* `WebhookServer` — stdlib `ThreadingHTTPServer` daemon thread on
  `QDRANT_WEBHOOK_HOST:QDRANT_WEBHOOK_PORT` (default
  `127.0.0.1:8335`): `GET|POST /trigger/<id>` (`?key=` or
  `X-Access-Key`; wrong key 401 / unknown 404 / gated 403 / no
  fire engine 503), `{"payload": ...}` → `{payload}` placeholder
  replace or `[Webhook payload]` append, `?async=1` → 202 +
  background fire (default sync — the HTTP response carries the
  LLM answer). Bind failure warns instead of crashing.
* `make_webhook_fire(agent_factory)` — the fire engine (session
  `webhook-<id>-<ts>` + history row + lifecycle stamp on a FRESH
  read; returns `{ok, answer, sources, session_id, duration_s}`
  for the HTTP response). Tuple/dict dual contract, same as the
  scheduler's v0.9.23.0 lesson.

### 2.14g `WebhookLocalHost` (`src/mcp_client/host.py`, v0.9.25.0+)

In-process mirror of the schedule tools (10 tools, server
`webhook-local`): `add_webhook_trigger` (returns URL + access key
SHOWN ONLY ONCE) / `list_webhook_triggers` / `get_webhook_trigger`
/ `get_webhook_trigger_url(reveal)` / `delete` / `enable` /
`disable` / `run_webhook_trigger_now` /
`list_webhook_trigger_runs` / `get_webhook_trigger_errors`.
`add` auto-starts the listener via
`McpManager.set_webhook_auto_start_callback`; `run_webhook_
trigger_now` gets the fire engine via
`set_webhook_fire_callback`. Boot auto-resume:
`gui/api.py::_resume_webhook_server` (v0.9.22.0 pattern;
`QDRANT_WEBHOOK_AUTORESUME=off` opts out). stdio mirror
`src/mcp_server/webhook_server.py` — manages triggers for
external clients and does NOT bind the port (the listener lives
with the Agent process).

### 2.14b `src/mcp_client/` (v0.8.0+)

MCP host + client architecture so the Agent can consume
*third-party* MCP servers as ordinary tool calls and run the
OpenAI tool-calling loop on top of them.

```text
src/mcp_client/
├── base.py        # McpClient ABC + McpTool / McpCallResult
├── stdio.py       # StdioMcpClient        (subprocess spawn)
├── http_sse.py    # HttpSseMcpClient      (Streamable HTTP + SSE)
├── host.py        # LocalTool + the 21 in-process LocalHosts
│                  #   (Qdrant / WebSearch / Filesystem / Scheduler /
│                  #    Todo / OpenWith / LogAnalysis / Archive /
│                  #    Exec / DateTime / ChatHistory (v0.9.21.0+) /
│                  #    Memory (v0.9.59+) / Task (v0.9.45+) /
│                  #    MegaStock (v0.9.24.0+, mock-first) /
│                  #    Hicloud (v0.9.64.0+, mock-first, official
│                  #    HWS signature) /
│                  #    Webhook (v0.9.25.0+, per-trigger URL + key) /
│                  #    Onvif (v0.9.37+) / Cred (v0.9.39+) /
│                  #    NetScan (v0.9.40+) / Runtime (v0.9.43+) /
│                  #    Teams-assist (v0.9.69+) / Keep (v0.9.88+) /
│                  #    JevClassifier (v0.9.93+) /
│                  #    ImageGen (v0.9.104+, mock-first Image API) /
│                  #    Meta)
└── manager.py     # McpManager + ToolSpec + from_config() loader
```

`McpManager` is the single source of truth for the Agent's tool
surface:

* `register_local(LocalTool)` / `register_client(McpClient)`
  populates an internal index.
* `list_tools() -> list[McpTool]` — flattened inventory across
  all origins, with name collisions automatically suffixed as
  `server__tool`.
* `call(name, args) -> McpCallResult` — dispatches in-process
  locals vs remote clients, both via the same try/except wrapper
  so one tool failing does NOT cascade.
* `openai_tools() -> list[dict]` — renders the inventory in the
  shape `chat.completions.create(tools=...)` expects.
* `replace_config(servers)` (v0.8.0+) — drop all remote clients
  and rebuild from a fresh `servers` list, leaving local hosts
  intact. Drives the GUI fleet editor's hot-reload.

Constructed via `McpManager.from_config("config/mcp.json")` at
Agent construction time; missing / malformed / empty config
results in a manager seeded only with the local hosts.

The three transports supported:

| Transport | Class | When to use |
|---|---|---|
| `stdio` | `StdioMcpClient` | Local CLI server (e.g. our own `python -m src.mcp_server.server`, github-mcp, etc.) |
| `streamable_http` (default for remote) | `HttpSseMcpClient(transport="streamable_http")` | Modern MCP 2025-03-26+ servers |
| `sse` (legacy) | `HttpSseMcpClient(transport="sse")` | Older MCP 2024-11-05 servers (Notion / Linear / Cloudflare, …) |

Failure semantics (same for all transports):

* A single server failing to **connect** at startup -> recorded
  in `McpManager.failed` and surfaced via `/tools status` (CLI)
  or `get_status()['mcp_failed']` (GUI).
* A single tool call raising at runtime -> `McpCallResult`
  with `is_error=True`. The Agent loop feeds the error string
  back to the model so it can pivot.

Tests: `tests/test_mcp_client.py` (26 tests, all mock-driven;
no real MCP servers needed for CI).

**LocalTool argument coercion (v0.8.6.1):** `LocalTool.__call__`
in `host.py` normalises incoming payloads via the
`_coerce_arguments()` pure helper — accepts `None`, bare JSON
strings, wrapper dicts (`{"args": ...}`, `{"kwargs": ...}`,
`{"params": ...}`, `{"parameters": ...}`, `{"arguments": ...}`,
`{"input": ...}`), single-element lists / tuples, and objects
with `__dict__`. Unrecognised shapes fall back to `{}`. After
coercion, `_call_args(kwargs)` introspects the handler signature:
the `(self, args: dict)` convention used by every LocalHost gets
the normalised dict as a single positional arg, while kwargs-spread
handlers like `WebSearchLocalHost._search(query, max_results=5)`
keep working unchanged. This is the fix for the user-reported
2026-08-31 silent `AttributeError: 'str' object has no attribute 'get'`
— see `tests/test_local_tool_argument_coercion.py` (34 tests).

### 2.15 `gui/api.py`

JS-callable wrapper around the CLI handlers. Every method returns
`{"error": "..."}` on failure rather than raising — the WebView
window cannot survive a Python traceback. See §9.2.

### 2.16 `gui/app.py`, `gui/logger.py`, `gui/assets/`

PyWebView launcher with rotating file logging. The frontend
(`index.html` + `app.js` + `style.css`) is a vanilla-JS SPA — no
build step, no npm.

---

## 3. Data Flow Walk-through

A single `cli.py ask "中華電信董事長是誰?"` path step-by-step:

```
   cli.py _handle_ask
       |
       v
   Agent.ask(question) ----------------------------+
       |                                           |
       |---> Agent._build_context()                |
       |       |                                   |
       |       |---> Retriever.search(query)        |
       |       |       |                           |
       |       |       |---> Embedder.embed(query) |  <-- 3-tier cache
       |       |       |    1. LRU  2. SQLite  3. backend
       |       |       +---> QdrantStore.search(vec, top_k=5)
       |       |                                 |
       |       |                                 +---> HTTP GET -> Qdrant
       |       |
       |       |---> (optional) OllamaWebSearch.search()
       |       |       +---> POST https://ollama.com/api/web_search
       |       |
       |       v
       |   context = "### Local Documents\n[Document 1] Source: x\n..."
       |
       |---> Agent._system_prompt(web_enabled=...)
       +---> OpenAI-compatible chat.completions.create(model, messages=[...])
               +---> POST https://ollama.com/v1/chat/completions
       +---> answer = response.choices[0].message.content
                    |
                    v
             ui.md_render.render_markdown(answer)   [CLI only]
                    |
                    v
             print(...)
```

The GUI path is identical except `Agent.ask` becomes
`Agent.chat_turn` (returns `(answer, context, sources)`) and the
answer is rendered client-side with `marked.js`.

### 3.1 Payload schema (what's actually in Qdrant)

Every chunk is stored as a Qdrant point with this payload:

```
{
  "text": "<one chunk's text>",
  "filename": "<basename only, e.g. 'doc.pdf'>",
  "source": "<absolute POSIX path, used for delete-by-source>",
  "content_hash": "<sha1 of text>",
  "chunk_index": 3,
  "total_chunks": 11,
  "extension": ".pdf",
  "file_size": 1234567,
  "last_modified": "2025-01-02T03:04:05"
}
```

The vector is `config.EMBEDDING_DIM` floats. The Qdrant payload
index `filename` is what makes `group_by='filename'` work in v0.6.1
stats — without that index the query is a full scan.

`source` uses POSIX form (`as_posix()`) to dodge Windows
backslash-escape glitches in payload filter grammar. **Don't
re-introduce backslashes** without also re-checking every
`qmodels.FieldCondition(key="source", match=MatchValue(value=src))`.

---

## 4. Cache Topology

There are three caches in the system. Two are persistent-friendly;
one dies with the process. **Understand them or you'll wonder why
ingest of the same file 5 times takes 1 second instead of 4 minutes.**

### 4.1 Layer 1 — In-process LRU (`Embedder._cache`)

```python
self._cache: dict[str, tuple[float, ...]] = {}  # max ~4096 entries
```

* **Lifetime:** dies when the Embedder instance is garbage-collected.
  Inside one process: same module references = same cache.
* **Lookup:** O(1) dict, key = `sha1(text)` (just the text, no
  model qualifier).
* **Why just sha1(text)?** Because inside one process the backend
  can't change underneath you. The process's model is implicit.
* **Eviction:** FIFO at 4096 (older-and-rarely-used items drop
  first because ingest batches overwrite the end of the dict).

### 4.2 Layer 2 — SQLite Disk Cache (`DiskEmbeddingCache`)

```
$QDRANT_RAG_HOME/embed_cache.db
  +--- meta (schema_version)
  +--- embeddings (
       key      TEXT PRIMARY KEY,   -- sha1(backend|model|text)
       backend  TEXT NOT NULL,
       model    TEXT NOT NULL,
       dim      INTEGER NOT NULL,
       vec      BLOB NOT NULL,      -- float32 little-endian
       n_hits   INTEGER NOT NULL DEFAULT 0,
       created_at, last_used_at
     )
```

* **Lifetime:** survives process restarts. **Across processes,
  hits are shared.**
* **Lookup:** indexed by PRIMARY KEY (sha1), returns the vector
  blob.
* **Key design:**
  ```
  sha1(backend.encode() + b"|" + model.encode() + b"|" + text.encode())
  ```
  Why include backend+model? Because switching
  `EMBEDDING_BACKEND=ollama` for a second collection shouldn't
  silently reuse Qwen3 vectors in a nomic-embed-text collection.
* **Eviction:** soft cap via `EMBEDDING_CACHE_MAX_ENTRIES`
  (default 200 000); when full, oldest 10% by `last_used_at` are
  deleted in one transaction. Read bumps `n_hits` and
  `last_used_at`.
* **Concurrency:** WAL mode + autocommit + busy-timeout 30s +
  thread lock. Multiple processes + multiple threads are safe.
* **Schema versioning:** `_SCHEMA_VERSION = 1` mismatch triggers an
  automatic wipe on first open.


### 4.3 Layer 3 — Backend API

What you actually call. Only invoked when layers 1 + 2 both miss.

| Backend | Endpoint | Auth | Dim control |
|---|---|---|---|
| `local` (llama.cpp) | `POST $LLAMACPP_EMBED_URL` | none (assumed local) | `--pooling last --embd-normalize 2` server side |
| `ollama` | `POST $OLLAMA_BASE_URL/embeddings` or `POST $OLLAMA_BASE_URL/api/embeddings` | `Bearer $OLLAMA_API_KEY` (cloud) | depend on model |
| `openai` | `POST $OPENAI_BASE_URL/embeddings` | `Bearer $OPENAI_API_KEY` | depend on model |
| `hash` | none (deterministic SHA-based) | n/a | `EMBEDDING_DIM` |

### 4.4 The Total Lookup Order

```
embed(text, backend=B, model=M):
    key1 = sha1(text)            # LRU key
    key2 = sha1(B|M|text)        # SQLite key

    if key1 in _cache:
        return _cache[key1]                  # LRU hit (~0 ms)
    if key2 in disk_cache:
        vec = disk_cache[key2]
        _cache[key1] = vec                   # promote to LRU
        return vec                           # Disk hit (~5 ms)
    vec = call_backend_api(text)             # Cold (~200 ms +)
    _cache[key1] = vec
    disk_cache.put(key2, B, M, vec)           # persist for next time
    return vec
```

`embed_batch` does this with a vectorised "split into hits / misses"
pre-pass, so a 300-text batch with 100 hits only calls the backend
for 200 texts.

### 4.5 Cache Invalidation Invariants

| Event | What gets invalidated | Where |
|---|---|---|
| Qdrant upsert (ingest) | `list_documents` + `get_stats` caches in the **same** `QdrantStore` instance | `store.upsert()` calls `invalidate_list_cache()` |
| `QdrantStore.delete_by_source(source)` | `list_documents` + `get_stats` caches | `delete_by_source()` |
| `QdrantStore.delete_by_filename(name)` (v0.7.0+) | same as above | `_delete_by_field()` shared core |
| `QdrantStore.delete_collection()` / `recreate_collection()` | (next call re-creates collection, lists will be empty) | — |
| `Embedder.cache_clear_all()` | LRU + disk (both) | `Embedder.cache_clear_all()` |

**Why the disk embedding cache is *not* invalidated by upsert:**
ingest adds new chunks, but the cache is keyed by *text*, not by chunk.
A chunk's text is stable across re-ingest — the cache stays valid.

### 4.6 Failure modes and recovery

| Symptom | Cause | Fix |
|---|---|---|
| Inconsistent vectors for same text | Backend changed mid-run (e.g. llama.cpp died, fell back to hash, then restarted) | The cache stored the wrong vector. Wipe: `cache_clear_all`. |
| `disk_cache_full` warnings | `EMBEDDING_CACHE_MAX_ENTRIES` reached; eviction ran | Expected — silent. Bump the cap if it happens frequently. |
| SQLite is locked errors | Two writers + missing WAL | We already use WAL; if you see this, file a bug — we shouldn't. |
| `schema_version mismatch` warnings | Incompatible cache schema change | `cache_clear_all()`. The `_SCHEMA_VERSION` bump wipes on next open. |

---

## 5. Config, Env, and Naming Conventions

### 5.1 Environment variables — read once

`src/config.py` calls `load_dotenv()` at import. Every property on
`config.X` and `config.<group>.X` is computed by re-reading
`os.environ` *each access*. That means:

* You can `os.environ["FOO"] = "..."` in code and the next
  `config.FOO` read reflects it.
* The Qdrant URL is frozen into `QdrantStore.__init__` — config
  changes after that point do NOT affect an already-running store.

**Before mutating env vars mid-session, restart the process.** This is
a deliberate simplicity choice.

### 5.2 The flat + namespaced duality

Yes, this is annoying. It exists because:

* Old CLI scripts (and human muscle memory) use `config.QDRANT_URL`.
* New pipeline code uses `config.ingest.chunk_size` to match the
  sibling.

When adding a new setting:

1. Add to the matching namespace class.
2. Add a flat alias to `Config` with the exact same default.
3. Update `.env.example`.

If you skip step 2, search the code: old call-sites
(`grep "config.OLD_NAME"`) will go silently stale.

### 5.3 Naming conventions

| Symbol | Convention | Example |
|---|---|---|
| Class names | `PascalCase` | `QdrantStore`, `IngestPipeline` |
| Module-level constants | `UPPER_SNAKE` | `WEB_SEARCH_ENDPOINT`, `_SNIPPET_CHARS` |
| Private module data | `_leading_underscore` | `_LIST_CACHE_TTL` |
| CLI sub-commands | `verb` / `verb-noun` | `ingest`, `stats`, `recreate-collection` |
| GUI tabs | `tabFoo` in HTML class | `tabChat`, `panel-admin` |
| Env vars | `UPPER_SNAKE` | `EMBEDDING_DIM`, `OLLAMA_BASE_URL` |
| Cache env mode | lowercase | `disk` / `memory` / `off` |
| Backend name | lowercase | `local` / `ollama` / `openai` / `hash` |

### 5.4 The "don't hardcode" rule

Anything that depends on environment must read from `config` or
`os.environ` at runtime. Hardcoded values that are easy mistakes:

| Hardcode | Why it's wrong |
|---|---|
| `localhost:6333` | Use `config.QDRANT_URL` |
| `4096` | Use `config.EMBEDDING_DIM` — varies per backend |
| `windows-1252` | Use `charset_normalizer.from_bytes(...).best()` |
| `BAAI/bge-m3` etc. | Different Ollama models have different dims |
| `127.0.0.1:8080` for llama.cpp | Use `$LLAMACPP_EMBED_URL` |

If you do need to hardcode for a fallback / comment, add a
`# check first:` block in the change.

---

## 6. Adding a New Feature

The general pattern. Use the same process for chat_history_sidebar,
a new TUI command, or a hypothetical REST entry point.

### 6.1 Decide the shape

Ask in order:

1. **Does this only need read access to Qdrant?** Use `Retriever`
   and `cli.py search`-style commands.
2. **Does this need to mutate Qdrant (write)?** Use `IngestPipeline`
   or `QdrantStore.upsert`.
3. **Does this call an LLM?** Use `Agent.ask` / `chat_turn`.
4. **Is the entry point CLI / GUI / MCP?** Mirror the handler in
   all three (see §9).

### 6.2 The minimum-deliverable checklist

Every new feature must, at minimum:

* [ ] Have a CLI handler in `cli.py` if it changes user-visible state.
* [ ] Have a `gui/api.py` method that calls it (if a GUI tab might
      use it). The signature is `(args...) -> dict`.
* [ ] If it's an LLM tool invocation, consider exposing it via
      `src/mcp_server/server.py` too.
* [ ] Add or extend a test in `tests/test_gui_api.py` (or
      `tests/test_*.py`).
* [ ] Bump `pyproject.toml` version per README rule.
* [ ] Add a `### vX.Y.Z` entry to `README.md` `## 更新日誌`.

Skipping any of these is the #1 cause of "the new feature broke
in production and we didn't notice".

### 6.3 Decision tree: where to put new code

```
New format to ingest?
  -> src/ingest/loader.py  (add a `_load_<ext>` function)
  -> register extension in config.ingest.supported_extensions

New embedding backend?
  -> src/ingest/embedder.py
  -> see §8 in this guide

New prompt template?
  -> src/agent/agent.py
  -> edit _build_context() and _system_prompt()

New retrieval signal (BM25, re-rank, ...)?
  -> likely Retriever (search-and-return facade)
  -> or a new src/retrieval/ package if it's more than ~150 lines

New CLI sub-command?
  -> cli.py — add to argparse + dispatch table + _handle_X
  -> gui/api.py — add wrapper
  -> optionally src/mcp_server/server.py

New GUI tab?
  -> gui/assets/index.html — add <section class="panel" id="panel-X">
  -> gui/assets/app.js — add refreshX() and tab switching logic
  -> gui/assets/style.css — any new selectors

New cache?
  -> DON'T, until you've tried merging it into DiskEmbeddingCache
  -> if it's a separate concern (e.g. response cache), follow the
    same pattern: a module in src/<area>/<thing>_cache.py with
    make_key + get/put/has/clear/stats, threaded through env var.
```

---

## 7. Adding a New Loader / Format

This is the most common extension. Walk-through for "add .adoc
(AsciiDoc) loader support".

### 7.1 Required changes

1. **`src/config.py`**: append `".adoc"` to
   `_IngestNamespace.supported_extensions`.

2. **`src/ingest/loader.py`**: add `_load_adoc(path) -> str` that
   uses stdlib (`AsciiDoctor` is overkill — `textwrap` + a regex
   strip would do). The function returns plain text; downstream
   doesn't know it came from AsciiDoc.

3. **`src/ingest/loader.py` `_EXT_TO_LOADER` dispatch**: register the
   new function. **Don't add a fallback** that runs everything through
   `_load_text` — silent bugs are far worse than explicit errors.

4. **`src/ingest/pipeline.py`**: If the format has media (e.g. images
   in EPUB), add the `IMAGE_MARKER_RE` variant. Otherwise, no change.

5. **Tests**: add `def test_load_adoc_<case>` to a new
   `tests/test_loader.py` with hand-crafted tiny .adoc files. Skip if
   you trust the stdlib.

### 7.2 Common pitfalls

* **Encoding pitfalls.** All loaders must handle UTF-8 / GBK / Shift-JIS
  for CJK content. Use `charset_normalizer.from_bytes(path.read_bytes()).best()`.
  Reference `_load_txt` for the pattern.
* **PDF / DOCX exceptions.** A malformed file should return
  `("", [], {"error": str(e)})` — not raise. The pipeline catches and
  continues.
* **Empty-text case.** `chunk_text` chokes on `""`. Always return at
  least a 1-char placeholder for unusual formats, or filter at the
  pipeline level.

### 7.3 Example (terse): adding HTML body extraction

```python
# src/ingest/loader.py
from bs4 import BeautifulSoup  # already a dep

def _load_html(path: Path) -> str:
    raw = path.read_bytes()
    soup = BeautifulSoup(raw, "html.parser")
    for s in soup(["script", "style"]):
        s.decompose()
    return soup.get_text(separator="\n", strip=True)


_EXT_TO_LOADER[".html"] = _load_html
_EXT_TO_LOADER[".htm"]  = _load_html
```

That's it. The pipeline doesn't care it was HTML.

---

## 8. Adding a New Embedding Backend

Less common, but happens (e.g. Cohere embeddings, local ONNX).

### 8.1 The contract

An embedding backend is a way to turn a *list* of strings into a
*list* of equal-length float vectors. The `Embedder` handles
single-vs-batch choice, caching, normalisation, padding; you provide:

* A `_select_backend()` case
* An `embedder.embed_batch_xx(texts, missing_idx, keys, results)`
  method that fills in `results[i]` for each `i in missing_idx`.
  Return `True` on success, `False` to fall back.

The "fall back" path matters: when llama.cpp is down, we don't want
to crash — we fall back to per-item hash embedding so the user gets
*something* (with a warning). Honour this contract or things break in
production when one backend is unreachable.

### 8.2 Example: adding a Cohere backend

1. `src/config.py`: add `COHERE_API_KEY` and `COHERE_EMBED_MODEL` to
   flat attrs (no namespaced group needed unless you want one).
2. `src/ingest/embedder.py`:

```python
def _embed_batch_cohere(self, texts, missing_idx, keys, results):
    # Batched Cohere embed. Returns False to trigger per-item fallback.
    if not getattr(self, "cohere_api_key", ""):
        return False
    try:
        resp = requests.post(
            "https://api.cohere.ai/v1/embed",
            headers={"Authorization": f"Bearer {self.cohere_api_key}"},
            json={"texts": [texts[i] for i in missing_idx],
                  "model": self.cohere_model},
            timeout=30,
        )
        resp.raise_for_status()
        embs = resp.json()["embeddings"]
    except Exception as e:
        logger.warning("Cohere batch failed: %r", e)
        return False
    for j, i in enumerate(missing_idx):
        vec = self._normalize(embs[j])
        results[i] = vec
        self._cache_put(keys[i], vec)
    return True
```

3. Add `if backend == "cohere": ok = self._embed_batch_cohere(...)`
   to `embed_batch()`'s dispatch.

4. Add `"cohere"` to `_resolve_backend_model()` so the disk cache
   keys correctly.

5. Tests: skip-by-default (no API key in CI); just verify the
   `backend_info()` and dispatch plumbing.

### 8.3 Why cache key includes backend+model

If you remove this, the disk cache will return Qwen3 vectors when
the next user switches to a Cohere embedding. This is a *silent*
semantic bug — searches will look like they work but return wrong
results. Always check `_resolve_backend_model()` when adding a
backend.

---

## 9. The Three Entry Points

The same `src/` is reachable via three interfaces. They share no
code beyond `src/`.

### 9.1 CLI — `cli.py`

The "ground truth" — what you should use to verify a change before
trying it in the GUI. Every CLI command has a `_handle_*` function
that exists at module level. Sub-commands are sub-routed by
`argparse` + a long `if/elif` chain in `main()`.

Pattern for adding a command:

```python
# cli.py main()
elif args.command == "mycommand":
    _handle_mycommand(args)

# cli.py
def _handle_mycommand(args):
    from src.<area> import <thing>
    ...  # do the work
```

`cli.py` emits *no* JSON, only `print()` and `print_rich()` style
diagnostics. Use `print(f"[info] ...")` for machine-readable hints.

### 9.2 GUI — `gui/api.py`

The PyWebView bridge. Every method is callable from JavaScript as
`window.pywebview.api.<method>(arg1, arg2, ...)`.

**The contract** (it is forced by WebView semantics, not negotiable):

1. Methods MUST return JSON-serialisable dicts. Tuples -> wrap in
   `dict`. Sets -> lists.
2. Methods MUST NOT raise. Catching `Exception` and returning
   `{"error": str(e)}` is the standard.
3. Methods SHOULD log via `logger.info` (entry) and
   `logger.error(result, traceback.format_exc())` (failure).
4. Long operations should be debounced / cached on the Python side —
   the frontend has no concept of "loading…" beyond a spinner it
   controls.

Adding a method:

```python
def my_thing(self, arg1: str) -> dict[str, Any]:
    logger.info("my_thing: arg1=%s", arg1)
    try:
        result = ... do work ...
        return {"result": result}
    except Exception as e:
        logger.error("my_thing FAILED: %r\n%s", e, traceback.format_exc())
        return _err(f"my_thing failed: {e!r}")
```

Then add it to the API surface test in `tests/test_gui_api.py`:

```python
def test_api_has_all_expected_methods():
    expected = {..., 'my_thing'}
```

The frontend wires it in `gui/assets/app.js`:

```js
const r = await api.safeCall('my_thing', arg1);
if (r && !r.error) renderIt(r.result);
```

### 9.3 MCP — `src/mcp_server/server.py`

External MCP server, stdio transport. *Never* imported by `Agent` /
`cli.py` / `gui/api.py`. Its purpose: let other MCP clients
(Claude Desktop, Cline, …) connect.

```bash
python -m src.mcp_server.server
```

It exposes 5 tools: `search`, `ingest_file`, `ingest_directory`,
`list_documents`, `stats`. The plumbing is the standard `mcp`
package — consult [its docs](https://modelcontextprotocol.io/) if
extending.

When adding a tool:

1. Add the `_handle_X(args)` async method.
2. Register it in the `list_tools()` handler.
3. Add a dispatch case in `call_tool()`.

The MCP layer is **the last priority**. CLI + GUI ship first; MCP
follows when there's a use case.

### 9.4 Why no client-side MCP in v0.4.0+

Brief history (for future readers wondering why the code looks
uncomplicated): the v0.1.x era spawned a `FirefoxClient` and
`config/mcp.json` to talk to a browser-via-MCP subprocess. It was
a constant source of cross-platform pain (Playwright install on
Windows, MPSC races, etc.). v0.4.0 deleted all of it. If you ever
need browser control, add it as a separate `src/tools/browser.py`
that *this codebase imports directly*, not via an MCP stdio hop.
See the commit message of v0.4.0 for the full reasoning.

---

## 10. Logging, Errors, and Graceful Degradation

### 10.1 The two logs

* **GUI:** `gui/app.log` (rotating, 2 MB x 3) is the user-facing
  debug surface. The CLI imports `setup_logging()` only when it
  matters; the GUI does it at import-time so every code path
  (including `cli.py` import errors) gets captured.
* **stderr:** Old habits die hard. Some loaders `print()` to stderr
  with `[Qdrant]` / `[WebSearch]` prefixes for at-a-glance CLI
  feedback. Don't use stdout — it interferes with REPL prompts.

### 10.2 The "graceful degradation" mandate

Every code path that touches the network must have a try/except
that returns *something*. The user is far better served by a clear
"web search failed" empty list than by a 500 on a chat turn.

Patterns:

| Caller | Pattern |
|---|---|
| Embedder (batch) | Try batched -> fallback per-item -> fallback hash. Log warnings, don't crash. |
| Embedder (single) | Catch network, return hash fallback; never `None`. |
| QdrantStore | Raise (the caller decides). Let `gui.api` catch and `_err()`. |
| OllamaWebSearch | Catch, log to stderr, return `[]` / `None`. |
| GUI bridge | Catch *all*, return `_err(f"X failed: {e!r}")`. |
| CLI handlers | Catch per-item, print `[warn] ...`, continue loop. |
| Pipeline `ingest_directory` | Per-file try/except + `gc.collect()` after each. Never abort batch on one failure. |

### 10.3 What does NOT get gracefully degraded

Qdrant collection shape changes (delete / recreate) and the local
embedding cache wipe are *explicit* operations; they intentionally
crash-y on user error (e.g. trying to recreate a collection that
doesn't exist will raise a qdrant-client exception, not be hidden).

This is a deliberate split:

* **Best-effort ops** (ingest, embed, web search) — degrade.
* **Capability ops** (delete, recreate, cache clear) — propagate.

---

## 11. Debugging Checklist

When something misbehaves, check in this order. **Most issues are
config / cache / state.**

| Symptom | First thing to check | How |
|---|---|---|
| "找不到相關資料" but data is ingested | Dimension mismatch | `python -c "from src.vector_store.qdrant_client import QdrantStore; from src.ingest.embedder import Embedder; print(QdrantStore().dim, Embedder().dimension)"` |
| Same answer every time / wrong sources | Wrong embedding cache (different backend silently used) | Compare `Embedder.backend_info()` and `QdrantStore.dim`. Wipe cache. |
| Embedding takes forever | Backend down + no fast fallback | `curl $LLAMACPP_EMBED_URL` (or similar). Embedder logs which backend it probed. |
| GUI white-screened | JS error during render | Open DevTools (right-click -> Inspect, or `--debug` flag -> 9229). Check `app.log` for traceback. |
| "Qdrant unreachable" | Server not running / wrong URL | `curl $QDRANT_URL` and `dir C:\qdrant\` (Windows). |
| EVERY Qdrant call ~2 s (search/stats/list all slow) | Loopback-name trap: `localhost` → `::1` first, connect refused after ~2 s, qdrant-client re-opens per request | `.env` must use `127.0.0.1:6333`; `config.QDRANT_URL` rewrites bare `localhost` automatically (v0.9.25.1). Verify with `python -c "from src.config import config; print(config.QDRANT_URL)"`. |
| EVERY embed / backend probe ~2 s | Same loopback trap on `LLAMACPP_EMBED_URL` (embedder re-opens its HTTP connection per `requests.post`) | `.env` must use `127.0.0.1:8080`; the Embedder rewrites bare `localhost` automatically (v0.9.25.3). Verify with `python -c "from src.ingest.embedder import Embedder; print(Embedder().llamacpp_url)"`. |
| llama.cpp server dies after hours (`ggml_vulkan: device fault`) | Prompt-cache eviction churn (unique-text embeddings never hit the cache) | Keep `--cache-ram 0` in BOTH embedding start bats (`start-llamacpp-8b.bat` / `start-llamacpp-4b.bat`, v0.9.25.3; the `-4b` one is primary since v0.9.113.1). If the fault reappears, check the console/log for `making room for prompt cache entry` first. |
| Tests fail with SIGALRM | On Windows / macOS w/o Unix signal | `pytest.skip` is already wired for `test_get_stats_when_qdrant_up`. Skip explicitly if you add similar timeout tests. |
| Embedding cache growing unbounded | Cap not enforced | Check `EMBEDDING_CACHE_MAX_ENTRIES`. Look at `cache.evict_lru` call sites. |
| Session shows wrong messages | Session folder has mixed JSON from old versions | `ls -la $QDRANT_RAG_HOME/sessions/` to inspect. Documented schema in `src/agent/session.py` docstring. |
| "Ollama key missing" toast | `OLLAMA_API_KEY` env not loaded | `echo %OLLAMA_API_KEY%` (Windows cmd) or check `.env`. |
| CJK text rendered as `???` | `sys.stdout` encoding | `gui/api.py` already forces UTF-8; if you see elsewhere, see §12. |

### 11.1 First-time setup gotchas

* **Llamacpp default port (8080) collides with anything else on the box.**
  Choose a high port. Set `$LLAMACPP_EMBED_URL` accordingly.
* **Qdrant on Windows native writes to `C:\qdrant\storage\` by default.**
  Set `$QDRANT_URL` to the port you're exposing if non-default.
* **AMD RX 5700 XT (RDNA 1)** lacks FP16 tensor hardware, so the
  Vulkan build falls back to FP32 — but it is still the RIGHT choice
  for this model (v0.9.25.3 measured 2026-09-10: Vulkan 128.5 ms
  single-query / 43.6 s per 64-chunk batch vs CPU build 449.9 ms
  single-query and 64-batch >300 s TIMEOUT). The CPU bat is a
  fallback only, and it must keep `--pooling last --embd-normalize 2`
  so its vectors agree with the Vulkan server's.
* **Anaconda/miniconda `milvus-mcp` env is shared with the sibling.**
  That's by design; they share most of their dependencies.

### 11.2 What to capture for bug reports

A good bug report includes:

1. The exact command / GUI flow that broke.
2. The version (`divine-sense-agent.bat --version` via GUI footer — the
   `v0.X.Y` is enough).
3. `gui/app.log` (or stdout, depending on entry point).
4. Output of `python -c "from src.vector_store.qdrant_client import QdrantStore; print(QdrantStore().get_stats())"`.
5. `divine-sense-agent` config (strip API keys first).

---

## 12. Platform Quirks

### 12.1 Windows-specific

* **Path separators.** Never `\` in Qdrant payloads. Always
  `path.resolve().as_posix()` for `source` field. POSIX grammar in
  filter expressions doesn't accept backslash.
* **`Invoke-WebRequest` is unreliable** for GitHub release zip
  downloads (>100 MB). Use browser-then-drop-in-place for qdrant
  Windows binary if curl.exe doesn't work.
* **`os.system('find ...')` is not available in `cmd.exe`.** Use
  Python or PowerShell.
* **GDI text rendering for CJK** in log files needs UTF-8 codepage —
  `gui/api.py` already calls `sys.stdout.reconfigure(encoding='utf-8')`
  for this.
* **Windows file lock on `embed_cache.db`** during a previous
  Embedder's hot delete — we use `check_same_thread=False` +
  busy_timeout to coexist. If you see `PermissionError [WinError 32]`
  on `.unlink()`, you have an open pool still holding the file.
* **`localhost` is the IPv6 loopback FIRST (v0.9.25.1 speed trap).**
  `getaddrinfo('localhost')` returns `::1` before `127.0.0.1`, and on
  this box connecting to `::1` is refused only after ~2 s. qdrant-client
  re-opens its HTTP connection per request, so EVERY search/stats call
  paid ~2 s (measured 2073 ms → 29 ms after the switch). v0.9.25.1:
  `src/config.py::_normalize_loopback_url` rewrites the bare loopback
  name to `127.0.0.1` at config load — but write the IPv4 literal in
  `.env` anyway (`127.0.0.1:6333`); use `[::1]` only deliberately.
  v0.9.25.3: the SAME trap hit the embedder — `LLAMACPP_EMBED_URL`
  carried bare `localhost`, and every `requests.post` (new connection
  per call) paid the delay. The Embedder now runs its URL through the
  same `_normalize_loopback_url` helper; `.env` pins
  `127.0.0.1:8080`.
* **Unicode decorations in `.bat` files break cmd.exe parsing**
  (v0.9.25.3). Box-drawing dashes (`──`) or arrows inside REM
  comments shift the batch parser under `chcp 65001` and comment
  fragments get EXECUTED as commands (`'er' is not recognized...`).
  Keep `start-llamacpp-*.bat` files pure ASCII.

### 12.2 Vulkan / AMD / llama.cpp

The local embedding model (Qwen3-Embedding-8B Q4_K_M) runs on:

* **NVIDIA cards:** Best path. CUDA + FP16 tensor cores.
* **AMD RDNA 2+ (RX 6000, 7000):** Works via Vulkan with FP16 tensor accel.
* **AMD RDNA 1 (RX 5700 XT):** Works via Vulkan with FP32 matmul —
  still the PRIMARY path for this model (v0.9.25.3 live benchmark:
  Vulkan 128.5 ms single-query / 43.6 s per 64-chunk batch vs CPU
  build 449.9 ms single / >300 s batch timeout). Production flags in
  `C:\llama.cpp-vulkan\start-llamacpp-8b.bat`: `--ctx-size 4096 -ngl 99
  -fa on -ub 1024 --threads 12 --cache-ram 0`; the bench harness
  (`C:\llama.cpp\_bench.py` + `_run_bench.ps1`) re-runs the matrix.
* **Intel iGPU:** Vulkan path may use Intel compute runtime; fall back
  to CPU if device init fails. (2 GB shared VRAM cannot hold the 4.4 GB
  model — don't try to split weights onto it.)

### 12.3 Linux/macOS

Less battle-tested. The Windows paths are first-class. Linux
read-write tests are minimal; report issues upstream. macOS should
work with Docker Qdrant; native Qdrant binary is x86_64 only on macOS
(no Apple Silicon support from the Qdrant project at the time of
writing).

---

## 13. Sibling Sync: `milvus-mcp-agent` Parallel

There is a sibling project at `../milvus-mcp-agent/`. Whenever you
patch core (`src/ingest/*`, `src/vector_store/*`, `src/agent/*`),
the same patch almost certainly applies there.

### 13.1 What's mirrored verbatim

* `src/ingest/loader.py`, `chunker.py`, `image_describer.py`,
  `pipeline.py`.
* `src/agent/agent.py`, `session.py`.
* `src/tools/web_search.py`.
* `src/ui/md_render.py`.
* `src/config.py` (with grouped namespaces).
* All CLI sub-command structure in `cli.py`.
* GUI bridge + assets (`gui/*`).

### 13.2 What differs

* `src/vector_store/`: `milvus_client.py` vs `qdrant_client.py`,
  same shape, different APIs.
* Tests live in both repos separately; keep them in sync by hand.
* Milvus has client-side MCP (browser control etc.); Qdrant v0.4.0+
  does not. Don't re-add it.
* The embedding backend for Qwen3 (local) is wired the same way in
  both projects; divine-sense-agent got it first as of v0.2.5.

### 13.3 Sync cadence

When patching `src/`:


1. Apply to whichever repo (qdrant or milvus) you're actively working
   on.
2. Note the version bump and changelog.
3. Plan to port it to the sibling at the next sync point (this is
   mostly verbatim, with `MilvusStore` ↔ `QdrantStore` API swaps).
4. The sibling may diverge in vector-store specifics (e.g. Milvus
   `delete_by_source` is a method on Collection; Qdrant's is a
   filter-based call). When in doubt, check sibling's git log for
   the matching patch.

### 13.4 When NOT to mirror

* GUI-internal stuff that's only on one side (PyWebView vs
  Streamlit).
* Embedding cache key generation if the second project has a
  different vector store / dim scheme.
* Logging — different entry points have different requirements
  (stdio vs WebView vs MCP).

---

## Appendix A — File Inventory

**Source** (`src/`)

* `config.py` — env reader (singleton; incl. `QDRANT_LOG_COLLECTION`,
  `LLM_PROVIDER_CHAIN` / `VISION_PROVIDER_CHAIN` raw attrs +
  `OPENAI_CHAT_MODEL` / `OPENAI_COMPAT_*` escape-hatch knobs).
* `provider_chain.py` (v0.9.18.0+) — ZERO-side-effect shared parser
  for the two chain-order env vars (`LLM_PROVIDER_CHAIN` /
  `VISION_PROVIDER_CHAIN`); deliberately does NOT import `src.config`
  (whose import runs `load_dotenv`); re-exported from `src.config`
  for path stability.
* `embed_watchdog.py` (v0.9.112.0+) — PERIODIC llama.cpp embedding
  watchdog (the third auto-heal family member after
  `_ensure_qdrant_server` / `_ensure_firefox_service`; the only one
  that can KILL a process, because only a real embeddings POST detects
  the zombie state). Real-POST probe every
  `QDRANT_LLAMACPP_WATCHDOG_INTERVAL` (default 1800 s); 2 consecutive
  failures → netstat-parse the embed-port PIDs → `taskkill /F /T` →
  detached launch of `QDRANT_LLAMACPP_START_BAT` (default
  `C:\llama.cpp-vulkan\start-llamacpp-8b.bat`, fallback `C:\llama.cpp\`)
  → readiness wait ≤150 s; 600 s restart cooldown; HTTP-result-only
  (never latency — a busy healthy server mid-ingest is slow too);
  v0.9.115.1 adds the DEAD fast path: a TCP connect REFUSED failure
  (nothing on the port — a loading model answers HTTP 503, never
  refused) heals IMMEDIATELY when no llama-server.exe exists
  (guarded by a tasklist existence check against the loading-model
  race + the usual cooldown); `QDRANT_LLAMACPP_WATCHDOG_REFUSED_HEAL
  =off` reverts to the legacy 2-streak wait;
  started by `gui/api.py::_start_embed_watchdog` at prewarm;
  `QDRANT_LLAMACPP_WATCHDOG=off` opts out; `EmbedWatchdog.status()`
  surfaces via `Agent._runtime_ops("status")` → `embedding_watchdog`.
  v0.9.122.2 — `Gemma2Watchdog(EmbedWatchdog)` re-uses the ENTIRE
  engine for the gemma2 media server (:8081): same probe/streak/
  cooldown/refused knobs, own URL (`media_embed_url()`), own bat
  (`QDRANT_GEMMA2_START_BAT`, default
  `C:\llama.cpp-un\start-llamacpp-gemma2.bat`), thread name
  `gemma2-watchdog` + log prefix `[gemma2-watchdog]`; gate
  `QDRANT_GEMMA2_WATCHDOG` (empty = follow `QDRANT_MEDIA_EMBEDDING`);
  started by `gui/api.py::_start_gemma2_watchdog` right after the
  primary at prewarm; kills only PIDs listening on :8081 (the other
  llama-server is never touched); surfaces as `gemma2_watchdog` in
  `get_runtime_status` and as the `gemma2` sub-key of
  `Api.embedding_watchdog_status`.
* `agent/task_llm.py` (v0.9.110.0+) — task/stage-level model-pin
  parser + live catalog validators (`parse_task_llm` /
  `validate_task_llm` / `catalog_for` TTL cache / 
  `list_task_models`; v0.9.111.0 adds `coerce_llm_pin` for the
  schedule/webhook/plan ADD paths); provider whitelist (openrouter,
  ollama);
  reads `os.environ` directly (same zero-side-effect discipline).
* `vector_store/qdrant_client.py` — store, caches, search.
* `vector_store/retriever.py` — embed-and-search facade.
* `ingest/loader.py` — multi-format loaders.
* `ingest/chunker.py` — chunker.
* `ingest/embedder.py` — multi-backend embedder.
* `ingest/disk_cache.py` — SQLite persistent cache.
* `ingest/image_describer.py` — vision chain (Ollama → OpenRouter →
  SiliconFlow → OpenAI-compat; orderable via `VISION_PROVIDER_CHAIN`
  since v0.9.18.0).
* `ingest/pipeline.py` — glue (v0.9.112.0: payloads carry the
  ``embedding_backend`` tag from ``Embedder.last_backends``;
  ``QDRANT_EMBED_FAIL_CLOSED=on`` makes a hash-fallback batch raise
  BEFORE upsert — the file is skipped instead of silently polluted).
* `ingest/reembed.py` (v0.9.112.0+) — hash-vector audit + repair over
  any collection: recomputes every text's DETERMINISTIC hash vector
  and compares EXACTLY with the stored vector (np.allclose float32 —
  works for points tagged AND untagged), re-embeds flagged points via
  ``Embedder.embed_fresh`` (bypasses BOTH cache tiers; a point that
  re-embeds to hash again is left untouched and reported in
  ``still_hash``). Surfaces: `cli.py audit-backends` / `reembed-hash`
  + the Admin-tab Embedding card buttons.
* `ingest/headings.py` (v0.9.113.0+) — STAIR heading metadata (zero-dep):
  ATX `^#{1,6}` line scan → `heading_sections()` splits text into
  heading-delimited sections with an accumulated ``heading_path``
  (`"H1 > H2 > H3"`; the preamble gets an empty path) +
  `heading_for_text()` single-chunk reverse lookup (LAST heading inside the
  chunk wins, else the previous chunk's path carries over — best-effort for
  the leading fragment of a mid-chunk heading; code-fence `#` comment lines
  count as headings, a documented limitation) + `has_headings()`.
* `ingest/heading_backfill.py` (v0.9.113.0+) — metadata-only retro-fill for
  existing points: scroll all points sorted by `(source, chunk_index)`,
  derive the missing `heading_path` from the chunk text (sequential
  carry-over; points that already carry a value are skipped unless
  `force=True`), `set_payload` grouped by target value in batches of 256 —
  vectors/ids/chunk boundaries are NEVER touched (no re-embed); the
  `heading_path` KEYWORD payload index is created best-effort. Surfaces:
  `cli.py backfill-headings [--force] [--collection X] [--limit N]` + the
  Admin-tab Embedding card 「§ 回填標題」 button.
* `ingest/media_embed.py` (v0.9.114.0+) — MULTIMODAL MEDIA SIDE-CHANNEL
  (rule: media → EmbeddingGemma 2's shared text+image+audio 768-dim
  space, text always stays on the Qwen3 primary path). The ggema server
  (unsloth b11443 build + `--mmproj`, default
  `http://127.0.0.1:8081/v1/embeddings`, `GEMMA2_EMBED_URL`) embeds
  media into `media_docs_gemma2` (`QDRANT_MEDIA_COLLECTION`) for
  cross-modal retrieval (text query → ggema TEXT tower → media
  vectors). FAIL-CLOSED fake-success guard: media embeddings ARE wired
  upstream since llama.cpp #30054 (2026-10-06, builds b11457+) via the
  NESTED content-parts `/v1/embeddings` shape (ggml-org#30082 was
  RETRACTED — the reporter had used a shallow shape), but the LEGACY
  shapes (internal `/embedding` + `image_data`; shallow image_url
  parts) can still 200 with a TEXT-ONLY marker vector
  (cos(red,blue)≈1.0), so `media_path_available()` (nested shape FIRST
  as of v0.9.122.1) embeds two synthetic reference patterns and
  refuses to index while cos > `QDRANT_MEDIA_FAKE_COS` (0.999); this
  also blocks pre-#30054 server builds BY DESIGN (upgraded through:
  the gemma2 server runs unsloth b11541-mix-2500203 since 2026-10-13,
  previous b11443-mix kept in `_old_b11443\`; live probe = REAL,
  cos(red,blue)=0.8795; the nested `input_audio` part is live-verified
  too, cos of two waves 0.9114). Verdict is
  cached with a 60-min cooldown (auto-lights after an upgrade).
  `index_media_batch` = side-collection upsert (payload:
  media_kind/media_name/media_path/position + the stored vision
  description as `text`); pipeline hooks (`_fill_image_descriptions`
  side-car + `_ingest_media_file` standalone gate
  `QDRANT_MEDIA_INGEST` + `QDRANT_MEDIA_EMBEDDING`); surfaces: MCP
  `search_media` (in-process LocalHost + stdio mirror) — honest errors
  pointing at the description text path (search_documents) while the
  media endpoint is down. Audio/video FILES still skip in the pipeline
  ext gate (the endpoint itself is live and audio is verified; opening
  the AV ingest gate is a future step; the STT/speech path is
  unaffected).
* `eval/` (v0.9.113.0+) — the Qwen3-4B recipe toolset: `eval_20q.json`
  (20 questions × expected source file, materialized from the file_docs
  corpus), `eval_driver.py <collection> <dim>` (hit@5 / first-hit rank /
  embed ms; questions whose expect set is absent from the searched
  collection are SKIPPED) and `ingest_side.py <collection> <dim>
  [--list f]` (ingest the eval subset into ANY side collection —
  per-collection backend switching is pure process env).
* `agent/agent.py` — RAG orchestration + tool-calling loop + 5-provider
  dispatch (chain-orderable, cross-provider failover, OPENAI-compat
  escape hatches) + system prompt trigger blocks (Telegram / drawio /
  open-with / log / archive / exec / context-offloading / chat-history /
  stock / webhook).
* `agent/scheduler.py` — schedule manager + cron/interval/once daemon
  (v0.9.67.1/67.2 fire hardening: `QDRANT_SCHEDULE_FIRE_HISTORY`
  independent-fire context cap, `QDRANT_SCHEDULE_FIRE_MEMORY`
  recall switch via `chat_turn(use_memory=)`, and the
  telegram-send integrity guard with str-first
  `last_tool_dispatches` parsing).
* `agent/webhook.py` (v0.9.25.0+) — webhook triggers + HTTP listener:
  `WebhookTrigger` (per-trigger random access key, lifecycle gates
  mirroring Schedule), `WebhookManager` (atomic JSON in
  `$QDRANT_RAG_HOME/webhooks/`), `WebhookServer` (stdlib
  ThreadingHTTPServer daemon thread, `/trigger/<id>` + `/healthz`),
  `make_webhook_fire` (Agent-backed fire returning the answer for the
  HTTP response).
* `agent/session.py` — chat session persistence.
* `agent/telegram_bot.py` (v0.9.27.0+) — `TelegramBotDaemon` /
  `TelegramBotClient` (getUpdates long-poll, per-chat sessions,
  fail-closed allowlist) + bidirectional voice (v0.9.58: OpenRouter
  STT/TTS + ffmpeg OGG↔WAV; ≤N-char broadcast summary v0.9.59.1) +
  photo-hallucination guard (v0.9.57.1) + inbound file receive
  (v0.9.63: getFile → WRITE sandbox → framed chat_turn) +
  MarkdownV2 delivery via `send_markdown` / `_send_reply_md`
  (v0.9.67.0).
* `agent/speech.py` (v0.9.58.0+) — `SpeechClient` over the OpenRouter
  audio endpoints (STT default gpt-4o-mini-transcribe; TTS default
  kokoro-82m `zf_xiaoxiao`) + the mp3/pcm → OggS/Opus ffmpeg bridge.
* `agent/memory_store.py` (v0.9.59.0+) — `MemoryStore` over the
  dedicated `QDRANT_MEMORY_COLLECTION` (memory_docs): deterministic
  uuid5 ids (re-saves = upserts), `stored_at` lifecycle, three
  ingest pipes (session chat / whitelisted tool results / explicit
  facts) + `_shared_store()`; the Agent memory block gains a
  HISTORICAL-SNAPSHOT hard rule in v0.9.67.2 (memories are
  acquisition-time snapshots — never cite stored values as current
  data for 今天/最新/現在/即時 questions).
* `agent/task_runner.py` (v0.9.46.0+) — process-wide background-task
  registry (thread-safe; scheduler / webhook / telegram / GUI
  fire sources all register).
* `agent/turn_gate.py` (v0.9.45.0+) — process-wide turn gate
  (`QDRANT_MAX_CONCURRENT_TURNS` + `QDRANT_TURN_WAIT_SECONDS`).
* `agent/turn_events.py` (v0.9.123.0+) — IN-PROCESS PER-TURN
  EXECUTION EVENT STORE for the two-phase GUI/web chat:
  `record(turn_id, kind, text)` (events ≤400 chars, ≤120/turn, LRU ≤8
  turns, thread-safe, never raises) + `finish(status, result)`
  (done/error/stopped carrying the legacy chat_turn payload shape) +
  `snapshot(turn_id, cursor)` (incremental read for `Api.poll_turn`).
  Paired with `Agent.begin_turn_stream/end_turn_stream/
  signal_turn_stop/_emit_turn_event` — a threading.local binds the
  stream to the CALLING thread (Telegram poll / scheduler fires /
  webhook fires / task runner keep streaming independently); six
  emission sites in `_tool_loop` (llm_round, tool_call, tool_result,
  wrap-up note, bail-out note, stopped). `Api.start_turn/poll_turn/
  stop_turn` = the two-phase surface over the existing
  `POST /api/<method>` bridge; the frontend `pollTurnStream()` polls
  at 1.2 s and renders the collapsible ⚙ execution-log panel (the
  SAME events for LAN web clients = 遠端操作也看得到) + 🛑 abort
  button; the scoped stop Event coexists with the v0.9.120.0
  task-registry force-stop; `msg.turnLog` rides the saved session so
  history bubbles keep a 查看執行 log expander.
* `telegram_md.py` (v0.9.67.0+) — `markdown_to_telegram_v2()`:
  normal GFM markdown → strict Telegram MarkdownV2 (placeholder
  strategy: code fences / inline code / links / tables rendered
  first and protected; headings→bold, bullets→•, task lists→✅/☐,
  emphasis converted word-boundary aware, the rest fully escaped,
  placeholders restored REVERSED so nested emphasis-with-link
  expands; Telegram 400 → plain-text fallback in both callers).
* `agent/scheduler.py` — cron / interval / one-shot scheduler (v0.8.0+).
* `agent/context_compress.py` (v0.9.10.0+) — opt-in chat context
  compression: history summarization (LLM + extractive fallback +
  per-prefix cache), tool-result offloading into the read sandbox,
  structure-preserving selective prose squeeze. v0.9.104.3 — the
  `on`/`yes`/`true`/`1` values are aliases of `auto` (the live .env's
  `=on` used to fall back to a silent `off`, disabling offload
  entirely).
* `agent/system_rules.py` (v0.9.104.6+) — user-importable system
  rules: `<READ sandbox>/system_rules.md` (default) or
  `QDRANT_SYSTEM_RULES_FILE` is appended to BOTH system-prompt
  flavours as a `USER SYSTEM RULES` block. mtime-cached (edit + save
  = effective next turn, no restart), capped by
  `QDRANT_SYSTEM_RULES_MAX_CHARS` (8000, truncated + notice), never
  raises (missing file / directory → '' = prompt byte-identical);
  the block states tool/security rules win on conflict.
* `agent/rulebook.py` (v0.9.124.0+) — the agent-maintained RULE BOOK
  (Memento-3 rulebook工件; the self-evolving-agent 譜系 report):
  `<READ sandbox>/agent_rules.md` rides the system prompt as the
  `AGENT RULEBOOK` block on BOTH flavours. Rules are `### [rN] title`
  blocks (evidence/updated metas; `underspecified` marker). Every
  write is atomic and pre-snapshotted into `rulebook_history/`
  (zero-dep versioning — `QDRANT_RULEBOOK_HISTORY_MAX` ring default
  20; `rules_history` carries a unified diff; `rules_rollback` backs
  up the current state first). `rules_revise` REQUIRES evidence
  (fail-closed — the Evo² evidence gate applied to rule revisions).
  The Memento prediction-error hook: a FAILED tool call appends a
  one-line revise hint via `Agent._run_tool_call` (once per turn,
  `QDRANT_RULEBOOK_SUGGEST=off` exits). MCP surface =
  `RulebookLocalHost` (`rulebook-local`, 7 tools since v0.9.127.0 -
  rules_ x5 + the ToolRoute-override pair, in the
  `_TOOLROUTE_CORE_SERVERS` core) + stdio mirror
  `mcp_server/rulebook_server.py` + CLI `python cli.py rules …`.
  Rule add/revise/rollback events sink memory `kind="rule-lesson"`
  (store-NOT-recall; `QDRANT_RULEBOOK_MEMORY=off` exits).
  v0.9.127.0 per-rule failure telemetry: every FAILED tool call bumps
  `errors` + `last_error` on every in-force rule (`note_tool_error`,
  fire-and-forget thread; the write BYPASSES the snapshot ring -
  bookkeeping, not a revision; `QDRANT_RULEBOOK_TELEMETRY=off` exits;
  `rules_revise` rewrites the block = counter reset).
* `agent/toolroute_overrides.py` (v0.9.127.0) - LLM-proposable
  ToolRoute trigger-pattern rows in `<READ sandbox>/agent_toolroute.md`
  (`### [tN]` blocks with `- server:` / `- patterns:` /
  `- evidence:` metas), evidence-gated + snapshot ring
  (`toolroute_overrides_history/`); an uncompilable regex = the entry
  is DROPPED (routing is never poisoned). Modes
  (`QDRANT_TOOLROUTE_OVERRIDES`): `shadow` (DEFAULT - matches are
  LOGGED as `[toolroute-override] shadow hit`, not applied), `on`
  (union into `_toolroute_servers_for`, union-only), `off`. Tools:
  `toolroute_override_add` (confirm-gated) / `toolroute_override_list`
  on `rulebook-local`; stdio mirror rides
  `mcp_server/rulebook_server.py`.
* `mcp_client/host.py` note — `DateTimeLocalHost` (v0.9.11.0+,
  described under Hosts & clients below) also lives here.
* `tools/web_search.py` — Ollama web search / fetch.
* `ui/md_render.py` — CLI-side Markdown renderer.
* `log_analyzer/` (v0.9.7+) — log anomaly analysis package:
  `parser.py` (access log / syslog RFC3164+RFC5424), `anomalies.py`
  (rule engine + numpy semantic outliers), `store.py` (`LogStore`
  over the dedicated `QDRANT_LOG_COLLECTION`).
* `archive_tools.py` (v0.9.8+) — pure archive format logic
  (zip / 7z / tar / tar.gz / tar.bz2 / gz; Zip-Slip guard,
  dangerous-ext skip, zip-bomb budgets; 7z via py7zr).
* `exec_tools.py` (v0.9.9+) — local compute: disaster blocklist,
  bash/awk/grep discovery (Git-for-Windows aware), `run_shell` /
  `run_bash` / `run_awk` / `exec_python`, pure-Python `grep_files`.
* `mega_stock_tools.py` (v0.9.24.0+) — 兆豐 MegaAPI stock engine
  (`MegaStockEngine`): mock-first dual mode (`MEGA_STOCK_MODE`,
  default mock = fully simulated in-memory book, deterministic
  per-symbol quotes, in-memory mock order book; v0.9.26.0 live
  wiring via the project-root `megaSpeedy/` component —
  `MegaLiveSession` event wrapper (Connect spapi.emega.com.tw:56789
  + LogonProxy + SetAccount; quotes via spdQuoteAPI
  Logon/Subscribe/OnOrderBook), real queries
  (queryStkOrder/queryStkMatch/makeStockAccountInquriy/GetDKChartData)
  usable from the API permission date, live orders via
  SendNewOrderEx/SendCancelOrderEx still behind THREE gates
  (mode=live + MEGA_STOCK_TRADING_ENABLED=on + per-call
  confirm=true) + the certificate gate (MEGA_STOCK_CERT_PATH/
  PASSWORD — queries need no cert, orders do)).
* `hicloud_tools.py` (v0.9.64.0+) — hicloud HWS (Chunghwa Telecom
  CaaS/CVPC) cloud engine (`HicloudEngine`): the official
  「Authentication of API Calling」 signature (sorted params →
  lowercase → HMAC-SHA1(secret) → Base64 → '+'→'*' '/'→'-'
  '='→removed; locked to the official doc example in tests),
  `build_signed_url` (values URL-encoded ':'→%3A, trailing
  `api/hws/` slash kept, signature appended unencoded); mock-first
  dual mode (`HICLOUD_MODE`, default mock = fully simulated
  in-memory VM/image book — NO HTTP; live requires
  `HICLOUD_ACCESS_KEY/SECRET_KEY`); lifecycle writes behind TWO
  gates (per-call `confirm=true` enforced even in mock +
  `HICLOUD_WRITES_ENABLED=on` live master switch, default off).
* `teams_assist_tools.py` (v0.9.69.0+) — Teams 桌面自動化代答引擎
  (zero-Microsoft-permission: operates the user's own logged-in
  Teams desktop client via the windows-mcp fleet):
  `TeamsAssistWatcher` (cheap screenshot-hash poll — fires the LLM
  only on real screen change, cooldown + uniform-screen skip +
  auto-pause after 5 grab failures), `make_teams_assist_fire`
  (fresh-Agent judge-then-act turn over the persistent
  `teamsassist-watch` session + task registry + the anti-fabrication
  corrective guard), atomic `teams_assist/state.json` (watermark /
  paused / 50-row audit ring); v0.9.70.0 adds the UIA channel —
  `_send_mode()` (`QDRANT_TEAMS_ASSIST_SEND_MODE` = windows-mcp
  default | uia) + `_uia_ops()` (subprocess →
  `scripts/uia_teams_ops.ps1` -Action read|send → single-line JSON;
  send = ValuePattern fill + InvokePattern 傳送 with the yield /
  verify / invoke-once safety chain, never raises).

**Hosts & clients** (`src/mcp_client/`)

* `keep_tools.py` (v0.9.88.0+) — KeepEngine: the notebook MCP core
  in three modes (`KEEP_MODE`: `mock` in-memory | `local` JSON
  note book at `$QDRANT_RAG_HOME/notes/notes.json` — atomic
  tmp+replace writes + threading.Lock read-modify-write | `live`
  official Keep REST). OAuth rides the google-calendar identity
  (shared token, scope merge via re-consent); the official Keep
  API is enterprise-only (personal accounts get a clean why-note);
  v0.9.90.0 adds `keep_update_note` / category field /
  `keep_preview_note` markdown 檢視檔 (7 tools). The Notes UI
  (v0.9.95.0) edits the SAME local JSON store via
  `KeepEngine.local_search` (paged + filtered browse — never
  dumps the whole table).
* `jev_tools.py` (v0.9.93.0+) — JevClassifierEngine: batch text
  classification over the typesafe/jev decision backend (same
  openrouter.ai/api/alpha/decisions endpoint as rerank, reuses
  OPENROUTER_API_KEY); specified JSON input
  `{classes, items[{id?,text}], instructions?, default_class?}`;
  ThreadPoolExecutor batch (`QDRANT_JEV_CLASSIFY_WORKERS` 4, cap
  `QDRANT_JEV_CLASSIFY_BATCH_MAX` 50) with per-item error
  containment; `JEV_MODE=mock` (deterministic hash pick, NO
  network) | live. Also backs the v0.9.98.0 dynamic tool-surface
  router (`QDRANT_TOOLROUTE`) and the v0.9.94.0 grounding guard.
* `imagegen_tools.py` (v0.9.104.0+) — ImageGenEngine: the OpenRouter dedicated Image API engine (`POST /api/v1/images/generations` + `GET /api/v1/images/models` discovery). Mock-first (`IMAGEGEN_MODE=mock` default = deterministic Pillow placeholder, NO network, `simulated: true`) | live reuses ``OPENROUTER_API_KEY`` and bills per image via ``usage.cost`` (all-or-nothing billing). Two-gate writes (hicloud/keep pattern): ``confirm=true`` (mock included) + live ``IMAGEGEN_WRITES_ENABLED=on``; HTTP/JSON failures are error envelopes, never raise; the engine returns decoded BYTES (base64 blobs never re-enter the LLM context).
* `base.py` — `McpClient` ABC + `McpTool` + `McpCallResult`.
* `stdio.py` / `http_sse.py` — remote MCP transports.
* `host.py` — `LocalTool` + all in-process LocalHosts:
  `QdrantLocalHost` / `WebSearchLocalHost` / `FilesystemLocalHost` (v0.9.3+)
  / `SchedulerLocalHost` (v0.8.6+) / `TodoLocalHost` (v0.8.8+) /
  `OpenWithLocalHost` (v0.9.6+) / `LogAnalysisLocalHost` (v0.9.7+,
  archives ingested directly since v0.9.8.1) / `ArchiveLocalHost` (v0.9.8+)
  / `ExecLocalHost` (v0.9.9+) / `DateTimeLocalHost` (v0.9.11+,
  get_current_time LOCAL + UTC) / `ChatHistoryLocalHost` (v0.9.21+,
  list/search/get past chats) / `MemoryLocalHost` (v0.9.59+, server
  `memory-local`) / `TaskLocalHost` (v0.9.45+, server `task-local`) /
  `MegaStockLocalHost` (v0.9.24+,
  兆豐 stock query + mock orders, server `stock-local`) /
  `HicloudLocalHost` (v0.9.64+, 中華電信 hicloud HWS CaaS/CVPC cloud
  tools — mock-first, server `hicloud-local`) /
  `WebhookLocalHost` (v0.9.25+, webhook triggers — per-trigger URL +
  access key, 10 tools, server `webhook-local`) / `OnvifLocalHost`
  (v0.9.37+) / `CredLocalHost` (v0.9.39+) / `NetScanLocalHost`
  (v0.9.40+) / `RuntimeLocalHost` (v0.9.43+) / `TeamsAssistLocalHost`
  (v0.9.69+, server `teams-assist` — Teams 桌面自動化代答助理,
  mock-first, watch thread inside the host, 9 tools incl. the
  v0.9.70 UIA channel) / `MetaLocalHost`.
* `manager.py` — `McpManager` + `ToolSpec` + `from_config()` (auto-registers
  every LocalHost; keeps `_log_local_host` / `_archive_local_host` /
  `_exec_local_host` / `_chat_history_local_host` / `_stock_local_host` /
  `_hicloud_local_host` / `_webhook_local_host` /
  `_teams_assist_local_host` refs).

**MCP stdio servers** (`src/mcp_server/`) — external clients

* `server.py` — core document RAG tools.
* `filesystem_server.py` (v0.9.3+) — 15+ sandbox file tools.
* `scheduler_server.py` (v0.8.6+) / `todo_server.py` (v0.8.8+) /
  `telegram_server.py` (v0.8.1+) / `openwith_server.py` (v0.9.6+).
* `log_analyzer_server.py` (v0.9.7+) / `archive_server.py` (v0.9.8+) /
  `exec_server.py` (v0.9.9+) / `time_server.py` (v0.9.11+).
* `chat_history_server.py` (v0.9.21+) — list/search/get past chats.
* `stock_server.py` (v0.9.24+) — 兆豐 stock query + mock orders
  (mock-first; `MEGA_STOCK_MODE=mock` default, live orders gated by
  MEGA_STOCK_TRADING_ENABLED=on + per-call confirm=true).
* `hicloud_server.py` (v0.9.64+) — 中華電信 hicloud HWS CaaS/CVPC
  cloud tools (mock-first; `HICLOUD_MODE=mock` default = simulated
  in-memory VM book, NO HTTP; lifecycle writes gated by per-call
  confirm=true + HICLOUD_WRITES_ENABLED=on).
* `memory_server.py` (v0.9.59+) / `task_server.py` (v0.9.45+;
  4 tools since v0.9.110 — `run_task_once(llm=?)` /
  `list_task_models` + registry `llm` field) /
  `onvif_server.py` (v0.9.37+) / `net_scan_server.py` (v0.9.40+) /
  `runtime_server.py` (v0.9.43+) — the remaining host mirrors.
* `webhook_server.py` (v0.9.25+) — webhook trigger management for
  external clients (does NOT bind the HTTP port — the listener runs
  inside the Agent process).
* `teams_assist_server.py` (v0.9.69+) — Teams 桌面助理 tools for
  external clients (9 tools incl. the v0.9.70 UIA channel; the
  AUTO-fire watch loop runs inside the Agent process — use the
  in-process host for the full assistant).
* `tacti_server.py` (v0.9.109+; renamed from skill_server.py in v0.9.120.3) — tacti/workflow compiler tools
  (`compile_plan` / `run_plan` / `get_plan_status` /
  `list_plans` / `run_plan_stage` / `stop_plan`) for external
  clients (plans persist under `$QDRANT_RAG_HOME/plans`).
* `image_gen_server.py` (v0.9.104+) — OpenRouter Image API 產圖
  (4 tools, mock-first two-gate) for external clients.
* `keep_server.py` (v0.9.88+) / `jev_server.py` (v0.9.93+) — 筆記本
  keep-local / Jev 批次分類器 stdio mirrors.

**GUI** (`gui/`)

* `app.py` — PyWebView launcher (+ chat-draft close-save hooks).
* `api.py` — JS-callable wrapper (+ MCP fleet editor, schedules, diagrams).
* `logger.py` — rotating-file logger.
* `diagrams_window.py` — independent Drawio viewer windows (v0.9.5+).
* `assets/index.html` — SPA shell.
* `assets/app.js` — vanilla-JS controller (markdown, chat, i18n).
* `assets/diagrams.{html,js,css}` — drawio embed (JSON protocol).
* `assets/style.css` — auto/light/dark theme.
* `app.log` — runtime logs (rotating).

**Tests** (`tests/`) — ~90 files, all mock-driven; the current
high-volume suites:

* `test_exec_host.py` (76) / `test_archive_host.py` (63) /
  `test_log_analyzer.py` (106) — the three v0.9.7–0.9.9 extensions.
* `test_open_with_host.py` (34) / `test_system_prompt_openwith.py` (9) /
  `test_filesystem_local_host.py` (102+) / `test_mcp_client.py` (29+) /
  `test_local_tool_argument_coercion.py` (34) / `test_scheduler*.py` /
  `test_system_prompt_{telegram,diagrams,todo,schedule*}.py` /
  `test_llm_providers.py` / `test_response_language.py` /
  `test_md_render.py` / `test_chat_autosave.py` /
  `test_diagrams_window.py` / GUI e2e (Playwright, optional).
* Recent-surface suites: `test_hicloud_host.py` (47, v0.9.64/66) /
  `test_web_download.py` (38, v0.9.65) / `test_telegram_md.py` (28,
  v0.9.67.0) / `test_scheduler_tg_guard.py` (21, v0.9.67.1/67.2) /
  `test_memory_store.py` (22+, v0.9.59/67.2) /
  `test_embedder_resilience.py` (30, incl. the v0.9.66.1 local-clip
  guard) / `test_pdf_ocr.py` (v0.9.61) / `test_teams_assist.py`
  (67, v0.9.69.0/70.0 — engine, watcher, fire engine, host tools +
  reply/UIA gates, wiring, boot resume, stdio parity).
* v0.9.104.x suites: `test_imagegen_host.py` (40, v0.9.104.0) /
  `test_toolroute_prev_reply.py` (12, .104.1) / `test_firefox_route.py`
  (9, .104.2) / `test_tool_result_cap.py` (12, .104.3) /
  `test_system_rules.py` (13, .104.6) / `test_session_rename.py` +
  `test_sessions_multi.py` (v0.9.103.5) / `test_kline_compact.py`
  (v0.9.98.1).

**Top-level**

* `cli.py` — CLI entry point.
* `pyproject.toml` — package metadata + deps (incl. `py7zr>=1.0`).
* `.env.example` — all env vars documented.
* `config/mcp.json.example` — MCP fleet examples (stdio / streamable_http / sse).
* `README.md` — user-facing documentation.
* `ARCHITECTURE.md` — this file.
* `divine-sense-agent.bat` / `divine-sense-agent-gui.bat` — Windows launchers.
* `scripts/uia_teams_ops.ps1` (v0.9.70+) — UIA read|send ops against
  the Teams client (the interference-free channel the engine drives).
* `scripts/uia_probe_teams.ps1` — UIA tree diagnostic (re-run when
  the Teams UI changes to re-locate the compose box).
* `gui.spec` / `build-gui.bat` — PyInstaller packaging.

---

## Appendix B — Versioning & Release Process

* **MAJOR.** Breaking change to the public surface — `cli.py` argument
  removal, `gui.api` method signature changes, payload schema changes.
* **MINOR.** New feature added (new tab, new loader format, new
  embedding backend).
* **PATCH.** Bug fixes that don't change behaviour (performance,
  caching, doc fixes).

`pyproject.toml` is the source of truth for the current version.
The GUI footer / splash version follows it automatically:
`gui/api.py::_app_version()` reads the version out of pyproject.toml
at import (plain regex, Python 3.10-compatible, never raises) —
there is no second version literal to bump. v0.9.14.1 fixed this:
`get_status()` used to hardcode `'0.8.0'` and had silently drifted
through nine minor releases.
Update README.md `## 更新日誌` immediately after merging.

Always run:

```bash
python -m pytest tests/test_gui_api.py -v
divine-sense-agent.bat ingest test.txt    # smoke test
divine-sense-agent.bat stats             # should show 1+ chunks
```

before tagging a release.

---

* Last reviewed: 2026-10-16 (v0.9.127.0). Update this file whenever the
invariant changes — slipping it is how design docs go stale.
