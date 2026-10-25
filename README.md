# Divine Sense Agent

**Divine Sense Agent 是一個跑在你自己電腦上的 AI 助理工作台。**
它把 LLM（OpenRouter / Ollama / OpenAI 相容端點 / 本機 llama.cpp CPU 伺服器）
接到 **20+ 組內建 MCP 工具面**，加上檔案沙盒、長期記憶、排程與背景任務——
讓 AI 不只是聊天，而是能「實際動手做」，而且**資料全程留在你的機器上**。

## 它能做什麼（30 秒看懂）

- 📚 **文件問答（RAG）** — 把整個資料夾的 PDF/EPUB/DOCX/PPTX/MD 一次
  ingest 進本機 Qdrant，之後用自然語言問答，回答附**來源檔名引用**。
- 🧰 **MCP 工具面** — 檔案沙盒與壓縮包原地編輯（zipfs）、Office 三件套
  （wordcraft/gridcraft/deckcraft）、桌面自動化（windows-mcp）、網頁與
  X.com 抓取（firefox_craw）、ONVIF 攝影機、Google Keep/Calendar、
  股票（兆豐 mock-first）、憑證管理、日誌分析、圖片生成、筆記本、
  技能檔案庫…（41 個 server、500+ 工具——Jev 依問題自動掛面）。
- ⏳ **背景任務與排程** — 長任務自動轉背景（⏳ 面板＋Telegram 完成
  通知）；cron／webhook 觸發；任務結論自動沉澱成可召回的長期記憶。
- 🖥 **四種介面** — 桌面 GUI（pywebview）、手機 Web App（PWA＋HTTPS 遠端可用）、
  CLI chat（BBS 風 REPL）、Telegram 雙向 bot。
- 🧠 **記憶與自演化** — 長期記憶召回＋每日對話摘要自動沉澱（T+1）＋
  E2E 驗證過的計畫技能編譯器（TACTI）＋可修訂的規則簿（rulebook）。
- 🐕 **運算小狗** — 模型思考時，視窗底部有一隻小狗替你奔跑。

> 怎麼跑起來？直接跳下方 **## 快速開始**。


## 為什麼換 Qdrant？

- **Milvus Lite 太慢**：每次 search 都要 cold-start IVF_FLAT segments；100K+ chunks 上 3-5 sec/查詢
- **Milvus Lite 鎖問題**：file lock 拒絕多 process 同時存取，無法並行 ingest + query
- **Qdrant 是真 server**：HTTP API，HNSW 預設 SIMD 加速（Linux 原生 / Vulkan / GPU），多 client 可並行
- **API 更乾淨**：沒有 IVF_FLAT vs HNSW 抉擇；HNSW 是預設且在 10K-1M 規模都好用


## 快速開始

### 1. 啟動 Qdrant server

**Docker（推薦）:**
```bash
docker run -p 6333:6333 qdrant/qdrant
```

**Windows 原生 binary（不需 Docker）:**
```powershell
# 下載最新 release
Invoke-WebRequest -Uri "https://github.com/qdrant/qdrant/releases/latest/download/qdrant-windows-amd64.exe" -OutFile "C:\qdrant\qdrant.exe"
# 啟動（含 storage path 設定，避免污染 user profile）
C:\qdrant\start-qdrant.bat
```

`start-qdrant.bat` 會設定：
- HTTP port 6333 / gRPC 6334
- storage → `C:\qdrant\storage\`
- snapshots → `C:\qdrant\snapshots\`

### 2. 設定環境變數
```bash
cp .env.example .env
# 編輯 .env 填入 LLM API key（OLLAMA_API_KEY 或 OPENAI_API_KEY）
# chat / ask 需要至少一個 key；純 ingest / search 不需要
```

### 3. 安裝依賴
```bash
pip install -e .
# 如果 qdrant_client 沒裝：pip install qdrant-client
# v0.5.0+ GUI 還需要 pywebview:
pip install pywebview
# 或一次裝齊(包含 build/test extras):
#   pip install -e .[build,test]
```

### 4. Ingest 文件
```bash
python cli.py ingest ./documents
# 或整個目錄
python cli.py ingest ./documents --recursive
```

### 5. 語意搜尋
```bash
python cli.py search "machine learning" --top-k 5
```

### 6. RAG 問答（單次）
```bash
python cli.py ask "What is HNSW?"
# --stream 旗標暫不支援（Agent 缺 ask_stream()；會 fall back 到非串流）
```

### 7. BBS-style Chat REPL（v0.2.0+）
```bash
python cli.py chat
# 或：divine-sense-agent.bat chat
# 內建指令：/info /reset /list /load <id>  ； exit / quit / Ctrl-C 退出
# 增強上下文 (v0.2.8+):/web on/off/status  (Ollama Web Search)
# 工具開關 (v0.8.0+):/tools on|off|status|list（in-process LocalHosts + config/mcp.json 遠端 servers）
# 語言指令 (v0.8.5+):/lang [zh-TW|zh-CN|en|ja|auto]
```

### 8. MCP Servers（給 Claude Desktop / Cursor / Cline 用）

v0.4.0+ 起本專案的角色是 **server** — 21 個 stdio MCP server 模組都可獨立啟動給外部 MCP 客戶端使用（內建 Agent 自己則以 in-process LocalHost 呼叫同一組工具，見下方「MCP Tools」總表）：

```bash
python -m src.mcp_server.server          # 核心：文件 RAG（search/ingest/list/stats/add/update/描述圖片/縮圖）
python -m src.mcp_server.filesystem_server  # 檔案讀寫沙盒（15 tools，v0.9.3+）
python -m src.mcp_server.scheduler_server   # 排程管理 + 產出驗證（11 tools，v0.8.6+/v0.9.106+）
python -m src.mcp_server.todo_server        # 多層 Todo 清單（v0.8.8+）
python -m src.mcp_server.telegram_server    # Telegram 發訊（v0.8.1+）
python -m src.mcp_server.openwith_server    # 成品以關聯程式開啟（v0.9.6+）
python -m src.mcp_server.log_analyzer_server  # 日誌異常分析（v0.9.7+，支援壓縮檔直接匯入）
python -m src.mcp_server.archive_server     # 壓縮解壓縮 zip/7z/tar/gz（v0.9.8+）
python -m src.mcp_server.exec_server        # 本地核心工具 bash/awk/grep/python（v0.9.9+）
python -m src.mcp_server.time_server        # 現在時間 local + UTC（v0.9.11+）
python -m src.mcp_server.chat_history_server  # 聊天歷史查詢（v0.9.21+）
python -m src.mcp_server.stock_server       # 兆豐股市查詢/下單（v0.9.24+，mock-first）
python -m src.mcp_server.webhook_server     # Webhook 觸發器管理（v0.9.25+）
python -m src.mcp_server.onvif_server       # ONVIF 攝影機工具（v0.9.37+）
python -m src.mcp_server.net_scan_server    # 內網 IP/Port 掃描（v0.9.40+）
python -m src.mcp_server.runtime_server     # 設定熱載入/服務重啟（v0.9.43+）
python -m src.mcp_server.task_server        # 背景任務（v0.9.45+）
python -m src.mcp_server.memory_server      # 長期記憶（v0.9.59+）
python -m src.mcp_server.hicloud_server     # hicloud 雲伺服器（v0.9.64+，mock-first）
python -m src.mcp_server.teams_assist_server  # Teams 桌面代答助理（v0.9.69+，UIA 零干擾）
python -m src.mcp_server.keep_server        # 筆記本 keep-local（v0.9.88+，mock/local/live）
python -m src.mcp_server.jev_server         # Jev 批次文字分類（v0.9.93+）
```

### 9. 本地 GUI（v0.5.0+，PyWebView）

GUI 是 CLI 的一對一鏡像 — 每個 tab 都對應到 CLI 子命令,
所有後端邏輯直接呼叫 `src/agent/` / `src/vector_store/` / `src/ingest/`。

```bash
# 啟動 GUI
divine-sense-agent-gui.bat

# 開機時指定語系
divine-sense-agent-gui.bat --lang ja        # / zh-TW / zh-CN / en

# 開 Chrome DevTools remote debugging (port 9229)
divine-sense-agent-gui.bat --debug

# 不開視窗,只驗證 import + API 是否健康 (smoke test)
python -m gui.app --no-gui
```

**Web 模式（v0.9.15.0+,可選）** — 想用瀏覽器而不開桌面視窗時:

```bash
# 啟動 Web 版（自動開瀏覽器;預設 http://127.0.0.1:8334）
divine-sense-agent-web.bat
# 或
python -m gui.app --web

# 不自動開瀏覽器 / 自訂埠
python -m gui.app --web --no-browser --web-port 9000

# v0.9.15.1+ — 綁本機任何 IP:
python -m gui.app --web --host 0.0.0.0          # 全部 IPv4 介面（啟動時列出所有可連的區網 URL）
python -m gui.app --web --host 192.168.50.154   # 只綁特定區網 IP
python -m gui.app --web --host ::               # IPv6 dual-stack（v4+v6 一個埠通吃）
# 或用 .env / 環境變數：QDRANT_WEB_HOST=0.0.0.0（v0.9.15.1 起 .env 值真的會被讀到）
```

與桌面 GUI 是**同一套前端 + 同一個 Api 後端** — `gui/web_server.py`
（純 stdlib `http.server`,無新依賴）服務 `gui/assets/` 並注入
pywebview shim,`app.js` 原封不動在瀏覽器運作,功能完全一致
（chat / ingest / search / sessions / 排程 / MCP 工具 picker 全部可用）。
預設只綁 `127.0.0.1` — Api 可驅動桌面自動化與檔案工具,**不要**暴露到
區網（綁非 loopback 位址會記 WARNING）;`QDRANT_WEB_HOST` /
`QDRANT_WEB_PORT` 可覆寫。**v0.9.15.1 起可綁本機任何 IP**（見上方
範例）:綁 `0.0.0.0` 時啟動 banner 會列出每個可連的區網 URL
（瀏覽器自動開 loopback 版）,其他手機 / 電腦直接點用;IPv6
（`::1` / `::` dual-stack）同樣支援。

**Web 模式的檔案進出（v0.9.16.0 上傳 / v0.9.65.0 下載）** —
聊天工具列 📎 可從客戶端瀏覽器（含手機）上傳檔案進沙箱；反方向，
LLM 產出的沙箱檔案（write_file / exec_python 圖表 / archive /
uploads/ 上傳檔）會在 chat 氣泡內顯示「⬇ 下載檔案 · 檔名」chip，
點擊直接從伺服器下載（`GET /download`，沙箱內檔案 + 危險執行檔
拒絕 + `QDRANT_WEB_DOWNLOAD_MAX_MB` 上限，預設 200 MB）。桌面
GUI 模式點擊 chip 改走原生資料夾選擇器 + 複製（save_sandbox_file_as）。

**Tabs**:
- **Chat** — 多輪對話、Web Search 開關、session picker、
  Ctrl+Enter 送出、**完整 GFM markdown 渲染** (v0.8.3 — marked.js
  + DOMPurify + `hardenLinks()` 自動加 `target=_blank rel=noopener
  noreferrer` 到每個 `<a>`,支援 task list / strikethrough / 巢狀
  list / inline image)。**v0.8.4 修了一個安靜的 silent failure**:
  vendor marked.min.js + purify.min.js 被打包進 `gui/assets/vendor/`
  (本地優先 + CDN 後備),並改成 duck-type check on
  `typeof DOMPurify.sanitize === 'function'`(DOMPurify 3 是 named
  function 不是 object — 之前 `typeof === 'object'` 一直 false
  導致 fallback 觸發,**使用者會看到 raw `**xxx**` 字元**,還以為
  是 LLM 沒寫 markdown)。**v0.8.5 補上語系閉環**:UI 切語言時
  也把 I18N 代碼 (zh-TW / zh-CN / en / ja) 傳給 Agent 的 system
  prompt,LLM 回答會跟著 UI 語言 — 不再出現「UI 繁中、回答簡中」
  的怪狀況。**v0.9.29/30 圖片鏈**: Ctrl+V 直接貼上剪貼簿圖片
  (自動存 `uploads/` 沙箱 → 路徑以 `[已上傳]` 插入輸入框);
  訊息內的圖片路徑(`[已上傳]` 標記行、裸絕對路徑、`uploads/`
  相對路徑、markdown 圖)自動轉成 data-URL **在气泡內顯示**
  (v0.9.30.0+,沙箱閘 + magic 驗證 + 20MB 上限;LLM 被 prompt
  教導要把生成的圖存進 sandbox 並嵌路徑)。
- **Ingest** — 路徑輸入 / 選檔 / 選資料夾(native dialog)、
  攝入進度 + 結果 JSON
- **Search** — 語意搜尋,結果顯示 filename + score + snippet
- **🛠 系統管理** — Collection / LLM / Embedding 狀態總覽、
  重建 / 刪除 collection(危險區,有 confirm 對話框)
- **Stats** — 卡片式 collection 統計、已索引文件清單
- **Sessions** — saved sessions CRUD table(load / delete)

**Theme**: 自動跟系統(`prefers-color-scheme`)或手動切 light / dark。
語系選擇 persist 在 `localStorage['qdrant-rag.lang']`。

**Logs**: 所有 GUI 操作 + 錯誤都寫到 `gui/app.log`(旋轉 2MB × 3)。
要 debug 開新視窗卡住,直接看 `gui/app.log` 就知道哪個 method 失敗。

**打包成 .exe**(選用):
```bash
pip install -e .[build]
build-gui.bat
# 產出 dist\divine-sense-agent-gui\divine-sense-agent-gui.exe (~50 MB folder)
```

### 10. Embedding 快取（v0.6.0+,SQLite 持久層）

`Embedder` 現在除了 in-process LRU (4096 條) 之外,
還有 **SQLite 磁碟快取** (`$QDRANT_RAG_HOME/embed_cache.db`),
跨 process / 重啟存活。

```
  第一次 ingest some.pdf   →  300 個 llama.cpp calls + 寫 300 條進 SQLite
  第二次 ingest same.pdf   →  全部 disk hit,  0 API call
  GUI chat 問一樣問題       →  disk hit(即使 GUI 重啟)
  切 Ollama 再問同問題      →  disk miss (backend key 不同,正常)
```

**三層查找順序**(`src/ingest/embedder.py`):
1. **In-process LRU** — 同一 process 內最快
2. **SQLite disk** — 跨 process 存活,WAL + autocommit,busy-safe
3. **Backend API** — 真的沒命中才打

**Cache key** 是 `SHA1(backend | model | text)` —
同一段文字在 Qwen3 (local) vs nomic (ollama) 下
會有 **不同** 的 entry,切換 backend 不會污染。

**環境變數** (`.env`):
| Var | Default | 用途 |
|---|---|---|
| `EMBEDDING_CACHE` | `disk` | `disk` / `memory` / `off` |
| `EMBEDDING_CACHE_MAX_ENTRIES` | `200000` | 超過時 evict 最舊的 10% |

**看 GUI**：「🛠 系統管理」tab 的 Embedding 卡片會即時顯示
backend / dim / LRU 用量 / disk 條數 + hit ratio。點 🧹 Clear
可一鍵清掉兩層快取(會有 confirm 對話框防誤觸)。

## CLI 對照（與 milvus-mcp-agent 相同）

| 命令 | 用途 |
|---|---|
| `cli.py ingest <path>` | 攝入單檔或目錄 |
| `cli.py search "query"` | 語意搜尋 |
| `cli.py ask "question"` | RAG 問答 |
| `cli.py stats` | 顯示 collection 統計 |
| `cli.py chat` | 互動式 BBS-style chat REPL（persistent session） |
| `cli.py sessions {list,show,delete}` | 管理 saved chat sessions |
| `cli.py schedules {list,add,...,daemon}` | 排程管理 + daemon（v0.8.0+） |
| `cli.py delete-collection` | 砍掉整個 collection（所有資料清空） |
| `cli.py recreate-collection` | 砍掉並重建 collection（套用新 HNSW 設定） |
| `cli.py remove-files <f1> [f2 ...]` | **從索引移除指定檔案（v0.7.0+,by basename）** |
| `python -m gui.app` | **啟動本地 GUI（v0.5.0+, PyWebView）** |
| `python -m gui.app --no-gui` | GUI smoke test（不開視窗,只驗 import + API） |

> 註：v0.4.0 起已移除 client-side MCP（`cli.py server` / `--mcp` / `/mcp` / `/firefox`）。
> MCP 以 server 角色提供（見「快速開始 §8」）；內建 Agent 改用 in-process LocalHost。

## MCP Tools 總表

內建 Agent 以 **in-process LocalHost** 呼叫下列工具（`McpManager.from_config` 自動註冊）；同一組工具也以 stdio server 提供給外部客戶端（見快速開始 §8）。遠端 MCP servers 可透過 `config/mcp.json` 加入（Admin 分頁可熱編輯）。

### 核心 RAG（`QdrantLocalHost` / `src.mcp_server.server`）
- `search_documents(query, top_k, filename_filter, min_score)`
- `ingest_file(file_path)` / `ingest_directory(directory)`
- `list_documents()` / `get_document_stats()`
- `add_document(mode=path|content)` / `update_document`（v0.9.0+，沙盒內寫入）
- `describe_image` / `resize_image`（v0.9.1+ / v0.9.2+）
- `remove_documents`（by filename）

### Web Search（`WebSearchLocalHost`）
- `web_search(query, max_results)` / `web_fetch(url)` — Ollama Web Search（需 `OLLAMA_API_KEY`）

### Meta（`MetaLocalHost`）
- `list_mcp_tools()` — 即時工具目錄自我介紹

### 檔案系統（`FilesystemLocalHost`，v0.9.3+）
- 8 read-only（沙盒 = `QDRANT_FILESYSTEM_READ_ROOT`）：`list_dir` / `stat_file` / `read_file` / `read_multiple` / `search_files` / `get_tree` / `search_content` / `diff_files`
- 8 mutating（沙盒 = `QDRANT_FILESYSTEM_WRITE_ROOT`）：`write_file` / `append_file` / `delete_file` / `move_file` / `mkdir` / `copy_file` / `patch_file`（+ `tree`）

### 排程（`SchedulerLocalHost`，v0.8.6+）
- `add_scheduled_task` / `delete_scheduled_task` / `list_scheduled_tasks` / `get_scheduled_task` / `enable_scheduled_task` / `disable_scheduled_task` / `run_scheduled_task_now` / `get_scheduled_task_errors`
- 進度追蹤（v0.8.7+）：`get_scheduled_task_status` / `list_scheduled_task_runs`
- 產出驗證（**v0.9.106.0+**）：`verify_artifact(path, checks?)` —
  機械式（零 LLM、零幻覺）產出檔案檢查：存在 / `min_bytes` / JSON
  解析 / 欄位檢查（dotted path `a.b[0].c`；`exists` / `nonempty` /
  `json_fields` / `json_number`(min/max) / `json_array`(min_length) /
  `json_string`(pattern) / `file_exists`）；未知 check type
  **fail-closed**，沙盒外路徑拒絕
- **驗證三守則流程硬擋（v0.9.106.0+）** — `add_scheduled_task` 預設
  `verify_required=true`：fire prompt 自動附加 VERIFICATION DIRECTIVE
  （執行 → 強制驗證 → 如實回報，失敗禁止成功結論）；事後機械守衛對
  「宣稱成功但零驗證證據」追加一次糾正補驗證回合；history / session
  記錄三態 `verified` 旗標（`get_scheduled_task_status` 呈現
  `last_verified` / `unverified_recent`）。退出：全域
  `QDRANT_SCHEDULE_VERIFY=off` 或單一排程 `verify_required=false`

### Telegram（v0.8.1+）
- `send_telegram_message` / `send_telegram_markdown` /
  `send_telegram_photo`（**v0.9.31.0+** — 沙箱內圖檔 multipart 上傳
  為照片，≤10 MB + magic 驗證 + caption ≤1024；沙箱外路徑被拒）/
  `send_telegram_voice`（**v0.9.58.0+** — OpenRouter TTS → OGG/Opus
  語音訊息）/ `send_telegram_file`（**v0.9.62.0+** — 沙箱內任意
  非執行檔以 sendDocument 上傳，≤50 MB；`.exe/.bat/.ps1` 等危險
  執行檔被拒；caption ≤1024）/ `list_allowed_chats`
  （`TELEGRAM_ALLOWED_CHAT_IDS` 白名單）
- **入站檔案接收**（**v0.9.63.0+**）— 白名單使用者從 Telegram 傳
  document / photo / video / audio 給 bot：`getFile` → 下載 → 存入
  WRITE 沙箱 `uploads/telegram/<chat_id>/`（≤20 MB；危險執行檔拒收）
  → 組框交 LLM 處理（caption 一併帶入）；接收/存檔皆有 INFO log，
  失敗回「⚠️ 檔案接收失敗」明確訊息

### Todo 清單（`TodoLocalHost`，v0.8.8+）
- 16 tools：`create_task` / `add_subtask` / `update_task` / `delete_task` / `list_tasks` / `get_task` / `start_task` / `complete_task` / `skip_task` / `fail_task` / `add_task_dependency` / `reorder_task` / `get_todo_progress` / `list_task_journal` / `render_task_tree` / `execute_plan`

### Open-with 成品開啟（`OpenWithLocalHost`，v0.9.6+）
- `open_file(path)` / `open_content(name, content)` / `list_openable_types()`

### 日誌異常分析（`LogAnalysisLocalHost`，v0.9.7+）
- `ingest_log(path, log_type=auto, members?, max_lines?)` — **支援壓縮檔直接匯入**（.zip / .7z / .tar / .tar.gz / .tgz / .tar.bz2 / .gz，成員各自成為 source `<archive>!<member>`）
- `analyze_anomalies(source?, log_type?, top_n?)` — 規則式統計（HTTP 錯誤率、404 掃描、高頻 IP、SQLi/UA 特徵、SSH 爆破、OOM、log flood）+ 語意孤立點（numpy cosine kNN）
- `search_logs(query, ...filters)` / `get_log_stats(source?)` / `list_log_sources()` / `remove_logs(source)`
- 專用 collection：`QDRANT_LOG_COLLECTION`（預設 `log_docs`），沙盒 `QDRANT_LOG_ALLOWED_ROOT`

### 壓縮解壓縮（`ArchiveLocalHost`，v0.9.8+）
- `archive_create(files, out_path?, format=zip|7z|tar|tar.gz|tar.bz2|gz)` / `archive_extract(path, to_dir?, members?, overwrite?)` — Zip-Slip 防護、危險副檔名跳過、zip-bomb 上限
- `archive_list(path)` / `list_archive_formats()` — 7z 需 py7zr

### 日期時間查詢（`DateTimeLocalHost`，v0.9.11.0+）
- `get_current_time()` — 一次呼叫同時回傳**本地時間**與 **UTC+0 時間**：各自帶 ISO 8601（含時區 offset）、`YYYY-MM-DD` / `HH:MM:SS`、年月日時分秒結構化欄位、時區名稱、UTC offset（`+08:00` 格式）與星期（英文名 + ISO 數字，locale 無關）；另附 Unix epoch 秒數（毫秒精度）
- 零參數、唯讀、無沙盒議題；system prompt 工具選擇規則教 LLM「『現在幾點』『today's date』一律呼叫此工具，不從記憶猜」
- in-process 自動註冊（第 11 個 LocalHost）；stdio server：`python -m src.mcp_server.time_server`（Claude Desktop 等外部 client 用）

### 本地核心工具執行（`ExecLocalHost`，v0.9.9+）
- `run_shell(command)` — 平台原生（Windows cmd / POSIX sh）
- `run_bash(script)` — GNU 管線（Git Bash 自帶 awk/grep/sed）
- `run_grep(pattern, paths?, -i/-v/-n/-c/-l, context)` — 純 Python，永遠可用
- `run_awk(script, file, field_separator?)` — 系統 awk 欄位加工
- `run_python(code)` — `sys.executable -c`，最彈性的轉換器
- 安全：cwd = write sandbox、timeout 30s（上限 120s）、輸出 64KB 截斷、機器級災難黑名單

### 對話歷史查詢（`ChatHistoryLocalHost`，v0.9.21+）
- `list_chat_sessions(keyword?, limit?, include_scheduled?)` — 列出已存 sessions（newest first）
- `search_chat_history(keyword, sid?, role?, ...)` — 跨 session 關鍵字搜尋（±context snippet）
- `get_chat_session(sid, last_n?, max_msg_chars?)` — 讀單一 session 訊息
- 全部 READ-ONLY + 內建上限（跑不炸 context window）；排程 fire session（`sched-` 前綴）預設隱藏
- stdio server：`python -m src.mcp_server.chat_history_server`

### 股市查詢下單（`MegaStockLocalHost`，v0.9.24+）
- `stock_get_quote(symbol)` / `stock_get_kline(symbol, interval?, bars?)` — 報價快照 / K線
- `stock_get_account_summary()` / `stock_list_orders(status?, limit?)` — 庫存權益 / 委託查詢
- `stock_place_order(symbol, side, qty, price?, order_type?, confirm)` / `stock_cancel_order(order_id)` — 下單 / 刪單
- **mock-first 安全**：`MEGA_STOCK_MODE=mock`（預設）= 完全模擬（`simulated: true`，不碰真 MegaAPI、絕不下真單）；live（v0.9.26.0 接線完成）— 查詢在 API 開通後即可用，**下單**仍受三道閘（mode=live + `MEGA_STOCK_TRADING_ENABLED=on` + `confirm=true`）+ 憑證閘（`MEGA_STOCK_CERT_PATH/PASSWORD`）保護
- stdio server：`python -m src.mcp_server.stock_server`

### hicloud 雲服務（`HicloudLocalHost`，v0.9.64.0+；v0.9.66.0 補完 56 工具）
- **VM 生命週期（v0.9.64）**：`hicloud_list_instances(instance_ids?, order_uuids?)` — 清單/狀態/IP；`hicloud_run_instances(image_id, instance_type, clone_instance_id?, instance_name?, count?, confirm)` — 建立（**會產生計費資源**）；`hicloud_start/stop/reboot_instances(ids, confirm)`；`hicloud_terminate_instances(ids, confirm)` — **不可逆**
- **VM 補完（v0.9.66）**：`hicloud_modify_instance_attribute(instance_id, instance_type?, monitoring_enabled?, confirm)` — 原機升降級；`hicloud_restore_instance(snapshot_id, confirm)` — 快照復原
- **範本 Image**：`hicloud_describe_images(image_ids?)` / `hicloud_image_create(instance_id, name?, confirm)` / `hicloud_image_delete(ids, confirm)` / `hicloud_image_modify(image_id, name?, description?, confirm)`
- **運算規格**：`hicloud_list_instance_types(os?)` — runInstances 前查合法值
- **監控 CloudWatch**：`hicloud_get_instance_stats(ids)` — CPU/DISK/Network 近即時
- **平台 Log**：`hicloud_log_list(start_date)` — ISO 8601
- **儲存空間 EBS**：`hicloud_volume_create(instance_id, radix 1-20=100GB/格, name?, confirm)` / `_attach / _detach / _delete / _list`
- **負載平衡 SLB**：`hicloud_slb_create`（CaaS：instance_ids+ports；CVPC：vpc_bsid+load_balancer_type+ports_info/rips_info）/ `_delete / _list / _vip_health / _replace`
- **子網段 Subnet**：`hicloud_subnet_create(subnet, vpc_bsid 或 intranet_gateway_bsid, confirm)` / `_delete / _list / _modify`
- **防火牆 NetworkAcl**：`hicloud_acl_create(policy_strings, instance_id 或 vpc_bsid, confirm)` / `_delete / _list / _replace`
- **CVPC 軟體資料中心**：`hicloud_vpc_create(vpc_name, region_id?, confirm)` / `_delete / _list / _modify`
- **網際網路連線**：`hicloud_igw_create / _delete / _list`；`hicloud_internet_ip_create / _delete / _list`
- **內網路由**：`hicloud_intranet_gw_create / _delete / _list / _modify`；`hicloud_intranet_link_create / _delete / _list`
- **NAT/埠轉發**：`hicloud_nat_create / _delete / _list`；`hicloud_pfwd_create / _delete / _list`
- **mock-first 安全**：`HICLOUD_MODE=mock`（預設）= 完全模擬（`simulated: true`，不連線 hicloud）；live 需 `HICLOUD_ACCESS_KEY/SECRET_KEY`（hicloud 用戶平台取得）
- **生命週期兩道閘**：每次操作 `confirm=true`（mock 亦強制）+ live 寫入需 `HICLOUD_WRITES_ENABLED=on`（預設 off）
- 簽章：官方「Authentication of API Calling」演算法（排序→小寫→HMAC-SHA1→Base64→`+`→`*` `/`→`-` `=`→刪）；以官方文件範例逐字驗證
- stdio server：`python -m src.mcp_server.hicloud_server`

### Webhook 觸發器（`WebhookLocalHost`，v0.9.25.0+）
- `add_webhook_trigger(name, prompt, max_fires?, expires_at?)` — 建立觸發器，**回傳專屬 URL + access key（僅顯示一次，請立即保存）**；自動啟動 HTTP 監聽器
- `list_webhook_triggers` / `get_webhook_trigger` / `get_webhook_trigger_url(reveal?)` / `delete` / `enable` / `disable` — 管理與生命週期
- `run_webhook_trigger_now(trigger_id, payload?)` — 手動執行一次（測試）
- `list_webhook_trigger_runs` / `get_webhook_trigger_errors` — 執行記錄 / 失敗查詢
- **運作**：HTTP `GET|POST http://<host>:8335/trigger/<id>?key=<key>` 即以 `Agent.chat_turn`（完整工具面）執行預設 prompt；POST body `{"payload": ...}` 注入 prompt 的 `{payload}` 佔位符；`?async=1` → 202 背景執行
- **安全**：key 常數時間比對；錯 key 401 / 未知 id 404 / 停用或超限 403；list/get 遮罩 key；非 loopback 綁定記 WARNING
- **生命週期**：`max_fires`（→ exhausted）/ `expires_at`（→ expired）/ `status=failed`（LLM 崩潰後停用）— 鏡像排程；持久化 `webhooks/<id>.json` + history + 專屬 session
- 監聽器：stdlib `ThreadingHTTPServer` daemon thread（`QDRANT_WEBHOOK_HOST:PORT`，預設 127.0.0.1:8335；bind 失敗僅警告）；GUI/web 重啟後 prewarm 自動恢復（`QDRANT_WEBHOOK_AUTORESUME=off` 可關）
- stdio server：`python -m src.mcp_server.webhook_server`（僅管理觸發器，**不綁 port** — 監聽器只在 Agent 程序內）

### Teams 桌面助理（`TeamsAssistLocalHost`，v0.9.69.0+；UIA 通道 v0.9.70.0+）
- **零 Microsoft 權限的個人代答助理**：操作「你自己登入的 Teams 桌面客戶端」（經 windows-mcp fleet 讀視窗/打字送出）— 不用 Teams API、不用 OAuth、訊息以你本人身份發出，對方無感
- 7 工具：`teams_assist_status` / `teams_assist_check`（live 截圖存 WRITE 沙箱 `teams_assist/`，供 `describe_image` 讀；mock 模式不截真實畫面）/ `teams_assist_start_watch`（畫面 hash 監看 daemon，**fail-closed**：`TEAMS_ASSIST_ALLOW_CHATS` 空白拒絕啟動）/ `_stop_watch` / `teams_assist_reply`（送出後註冊+審計；**write gate**：AUTO off 只能 `confirm=false` 記草稿，使用者明確要求才 `confirm=true+manual=true`）/ `pause` / `resume`（「暫停 teams」煞車）
- **watch 引擎**：64×64 灰階 hash 輪詢 — 畫面沒變不花 LLM token；uniform（鎖屏）跳過；`QDRANT_TEAMS_ASSIST_MIN_FIRE_INTERVAL`（預設 120 s）防洪水；連續 5 次截圖失敗自動暫停+審計；session `teamsassist-watch` 持久多輪（承接長篇討論）+ 反捏造 guard（宣稱已回覆但無派發 → 糾正回合）；**v0.9.71+ 偵測模式 `QDRANT_TEAMS_ASSIST_WATCH_MODE=uia`**：改 hash 聊天文字（UIA TextPattern 直讀）— 只有文字真的變了才觸發（幾乎零誤觸、最小化也有效；UIA 讀取失敗自動退回像素 hash）
- **UIA 零干擾通道（v0.9.70+，`QDRANT_TEAMS_ASSIST_SEND_MODE=uia`）**：`teams_assist_read_uia`（TextPattern 直讀聊天文字，替代截圖 OCR）+ `teams_assist_send_uia`（ValuePattern 填入 + InvokePattern 按傳送 — **零鍵鼠零搶焦點**，可一邊工作一邊全自動；輸入框有內容=你正在打字 → 自動讓路；回覆保持單一段落）— 共 10 工具（**v0.9.72+ 增 `teams_assist_config`**：LLM 可暫時調整 mode/send_mode/watch_mode/auto/白名單/人設/輪詢等組態 — 只改 process 不寫 .env，重啟即回復）
- 安全模型：聊天標題白名單 fail-closed（`'*'` = 處理所有聊天，v0.9.73.0+）、`QDRANT_TEAMS_ASSIST_AUTO` 總開關、`QDRANT_TEAMS_ASSIST_DRY_RUN`-式的 mock 模式（`QDRANT_TEAMS_ASSIST_MODE=mock` 預設，不截真實畫面）、每筆 fire/draft/sent 記入審計環；開機自動恢復（`QDRANT_TEAMS_ASSIST_AUTORESUME=off` 可關）
- **日常口令**（在聊天說即可；prompt 教 LLM 辨識，換句話說也通）：「啟動 teams 助理」/「開始監看 Teams」→ start_watch；「暫停 teams」→ pause；「恢復 teams」→ resume；「teams 助理狀態」→ status；「設定 teams…」（改 auto / 監看 uia / 換白名單 / 換人設）→ `teams_assist_config`（暫時生效，重啟回復 .env）；「幫我回 Teams：…」（明確要求 → `manual=true` 直接送出）；「核准送出」→ UIA 送出已記草稿；「看 Teams 有沒有新訊息」→ read_uia + 草擬
- stdio server：`python -m src.mcp_server.teams_assist_server`（AUTO-fire watch 迴圈要跑在 Agent 程序內 — 完整助理請用 in-process host）

### 其他 LocalHosts（歷史擴充）
- **ONVIF 攝影機**（`OnvifLocalHost`，v0.9.37+，server `onvif-local`）：`onvif_discover` / favorites（帳密 env-first + 遮罩）/ `test_credentials` / `get_profiles` / `get_snapshot` / `onvif_record`（ffmpeg 錄影 MP4，v0.9.38+）/ PTZ + Events + watches（v0.9.41+，17 tools）/ `onvif_get_resources` / `onvif_rtsp_snapshot`（v0.9.44+）
- **中央帳號集**（`CredLocalHost`，v0.9.39+，server `cred-local`）：`cred_add/list/get/remove/resolve`（atomic JSON store；GUI 🔑 Credentials 分頁與 CLI `creds` 共用）
- **內網掃描**（`NetScanLocalHost`，v0.9.40+，server `netscan-local`）：`net_scan_hosts`（tcp/ping）/ `net_scan_ports` / `net_my_interfaces` — 預設僅私有網段
- **執行時控制**（`RuntimeLocalHost`，v0.9.43+，server `runtime-local`）：`reload_config` / `restart_services` / `restart_mcp_servers` / `get_runtime_status`
- **背景任務 + 模型 pin**（`TaskLocalHost`，v0.9.45+，server `task-local`；5 工具 v0.9.120）：`run_task_once(prompt, title, use_web_search?, llm?)` — 可選 `llm="provider:model"` 限 openrouter / ollama 目錄（投放前驗證、fail-closed、不走跨供應商 failover）/ `list_tasks` / `get_task_result` / `list_task_models(provider?, contains?)` — 目錄探索（TTL 快取）/ **`stop_task(task_id)` — v0.9.120 強制中止（合作式：在下一個工具段落收尾、殭屍執行緒也服從、成果保留 → registry `stopped`）**
- **長期記憶**（`MemoryLocalHost`，v0.9.59+，server `memory-local`）：`remember_info` / `search_memory` / `purge_memories`（memory_docs collection，stored_at 生命週期）

## 架構

```
┌────────────────────────────────────────────────────┐
│           divine-sense-agent（單機 Agent）           │
│  CLI (cli.py) / GUI (gui/app.py) / stdio servers    │
│                                                    │
│  ┌──────────────────────────────────────────────┐  │
│  │  Agent（tool-calling loop + RAG prompt）      │  │
│  │        ▼ McpManager.from_config              │  │
│  │  in-process LocalHosts（零 round-trip）：      │  │
│  │   Qdrant / WebSearch / Filesystem / Scheduler │  │
│  │   Todo / OpenWith / LogAnalysis / Archive     │  │
│  │   Exec / DateTime / ChatHistory / Memory      │  │
│  │   Task / MegaStock / Hicloud / Webhook        │  │
│  │   Onvif / Cred / NetScan / Runtime / Teams-   │  │
│  │   assist / Keep / JevClassifier / Meta        │  │
│  │        ▼                                      │  │
│  │  遠端 MCP servers（config/mcp.json，可熱編輯）  │  │
│  └──────────────────┬───────────────────────────┘  │
└─────────────────────┼──────────────────────────────┘
                      │  HTTP (qdrant-client)
                      ▼
┌────────────────────────────────────────────────────┐
│          Qdrant Server（兩個 collection）             │
│  file_docs  — 文件 RAG chunks（HNSW + Cosine）       │
│  log_docs   — 日誌逐行向量（v0.9.7+ 異常分析）         │
│  - HNSW M=16, ef_construct=100, ef=128 (search)     │
│  - SIMD / Vulkan / GPU accelerated                  │
│  - Persistent storage（Docker vol / C:\qdrant）      │
└────────────────────────────────────────────────────┘
```

外部客戶端（Claude Desktop / Cursor / Cline）可透過 stdio 連到 `src/mcp_server/` 下的 21 個 server 模組（見快速開始 §8），使用與上表相同的工具面。

## Chat ↔ MCP Server 介接流程

> ⚠️ **歷史章節（v0.3.x 時代）**：以下內容描述 v0.3.x 的 client-side MCP
> 架構（`src.tools.mcp_client` / `McpRegistry` / `FirefoxClient` / `examples/`），
> 這些已在 **v0.4.0 全數移除**。v0.8.0+ 的現況：內建 Agent 以 in-process
> `McpManager` + LocalHosts 呼叫工具（見上方「MCP Tools 總表」），外部客戶端
> 改用 `src/mcp_server/` 下的 stdio servers（見快速開始 §8）。本章保留作為
> MCP 協議教學參考。

適用場景：當使用者用 **Claude Desktop** / **Cline (VS Code extension)** /
其他 MCP-compatible chat client 連接 divine-sense-agent 註冊的 MCP servers時，
會用這個三層架构進行通訊。

### 三層通訊架构

```
Chat (Claude/Cline)  ──stdio JSON-RPC 2.0──→  MCP Server (server.exe)
                                                          │
MCP Server  ──TCP 127.0.0.1:8765 (length-prefixed)──→  native_host.exe
                                                          │
native_host.exe  ──stdin/stdout (4-byte length + UTF-8 JSON)──→  Firefox extension
```

註：上圖是 v0.3.x 時代的舊架構（見章首歷史宣告）。現況（v0.8.0+）
divine-sense-agent **同時是 server 也是 client**：自己的 Agent 以
in-process LocalHosts 呼叫工具（零 round-trip），同時以
`src/mcp_server/` 下的 stdio servers 服務外部客戶端 —
`python -m src.mcp_server.server`（工具面見「MCP Tools 總表」）。

### 1. Cline (VS Code extension) 介接

設定檔路徑：
```
%APPDATA%\Code\User\globalStorage\saoudrizwan.claude-dev\settings\cline_mcp_settings.json
```

```json
{
  "mcpServers": {
    "firefox_craw": {
      "command": "C:\\Users\\humphery\\coding\\mcp_getweb\\dist\\server.exe",
      "transport": "stdio"
    }
  }
}
```

### 2. Claude Desktop 介接

設定檔路徑：
```
%APPDATA%\Claude\claude_desktop_config.json
```

```json
{
  "mcpServers": {
    "firefox_craw": {
      "command": "C:\\Users\\humphery\\coding\\mcp_getweb\\dist\\server.exe"
    }
  }
}
```

### 3. 訊息協議

| 連接路徑 | 協定 | 說明 |
|---|---|---|
| Chat ↔ MCP server | **stdio JSON-RPC 2.0** | MCP 唯一官方 transport。Chat 用 stdin 寫 request、從 stdout 讀 response。`server.py` 已把所有日誌寫 stderr，stdout 保留給 JSON-RPC。 |
| MCP server ↔ native_host | **TCP 127.0.0.1:8765 + length-prefixed JSON** | 內部 IPC。4-byte little-endian length + UTF-8 JSON payload。 |
| native_host ↔ Firefox extension | **Native Messaging (stdin/stdout)** | 瀏覽器原生訊息通道。4-byte length + UTF-8 JSON。 |

### 4. 一次完整流程（以 `google_search` 為例）

```
1. 使用者輸入：「幫我用 Google 搜尋 Python tutorial」
   ↓
2. Claude 推理決定呼叫 google_search 工具
   ↓
3. Cline/Claude Desktop 透過 stdin 寫入：
   {"jsonrpc":"2.0","id":1,"method":"tools/call",
    "params":{"name":"google_search","arguments":{"query":"Python tutorial"}}}
   ↓
4. server.exe 解析 → 建立 req_id + asyncio.Future
   ↓
5. 背景 dispatch thread 透過 TCP 8765 推 command 給 native_host
   ↓
6. native_host.exe 透過 stdout 寫給 Firefox
   ↓
7. Firefox extension (background.js):
   → handleCommand('google_search')
   → chrome.tabs 開啟 google.com/search?q=...
   → chrome.scripting 注入 extractGoogleSearchResults()
   → 抓 DOM 內容
   → nativeHost.postMessage 回傳 response
   ↓
8. native_host 透過 TCP 寫回 server
   ↓
9. server 接收 response，Future.set_result(content)
   ↓
10. google_search() await 完成，回傳字串
    ↓
11. mcp.run() 透過 stdout 寫入 JSON-RPC response 給 Chat
    ↓
12. Claude 看到工具結果，整合回答使用者
```

### 5. 關鍵概念

- **stdio 是 MCP 唯一官方 transport**，訊息格式為 **JSON-RPC 2.0**。
  跟 Native Messaging 的 length-prefixed 不同，Chat 客戶端
  （Cline/Claude Desktop）只看 JSON-RPC。
- `server.py` 已把所有日誌寫 **stderr**，**stdout 保留給 JSON-RPC**。
  若日誌走 stdout 會破壞 JSON-RPC 訊息，Chat 端會解碼失敗。
- **Chat 不感知 Firefox 存在**，只看到 MCP server 註冊的 9 個工具
  （`get_page_html` / `google_search` / `query_gemini` /
  `get_x_home` / `get_x_post` / `save_as_markdown` /
  `convert_document` / `convert_file` / `download_x_post`）。
  Firefox integration 是 `mcp_getweb` 的內部細節。
- **Chat 用 stdio JSON-RPC 與 MCP server 通訊**；
  **MCP server 內部整合 Firefox 控制能力**；最後把網頁內容回傳給 Chat。
- 同一個 process 內可以有多個 MCP server 每個跑一個 stdio 通道。
  Chat 同時連接多個 server 就能同時使用多組工具。

### 6. 開發者工具

手動測試 MCP server 可用 **MCP Inspector**（不需要啟動 Chat）：

```bash
npx @modelcontextprotocol/inspector
```

啟動 Web UI（瀏覽器自動打開），設定：

- **Transport Type**: `STDIO`
- **Command**: `C:\Users\humphery\coding\mcp_getweb\dist\server.exe`
- **Arguments**: (留空)

Inspector 會 spawn server.exe、手動 ListTools / CallTool，
可以不用開 Firefox 就能驗證 server 有沒有起來、tools 註冊對不對。

### 結論

> **Chat ↔ MCP server 透過 stdio JSON-RPC 通訊；server 內部整合 Firefox 控制能力；最後把網頁內容回傳給 Chat。**

## Embedding backend consistency（v0.3.9+）

`cli.py ingest` 和 `cli.py search`/`ask`/`chat` 會用同一個
[`Embedder`](src/ingest/embedder.py)，但同一個 Embedder 在不同
backend 下會產出不同 dim 的向量。Qdrant collection 創建時 dim
就已固定，之後改 backend 不重 ingest 就會「dim mismatch」 → search
無聲無息回空結果。

### 怎麼查當前的 backend 狀況

```bash
python cli.py stats
```

會多印一個 `Embedder backend` 區塊，跟實際的 Qdrant collection dim
交叉比對。如果一致會印 `[OK] Embedder X-dim == collection X-dim`
、不一致會印 `[!] DIMENSION MISMATCH` 加修正建議。

### Backend 原生 dim

| backend | 預設模型 | native dim | 必要環境 |
|---|---|---|---|
| `local`  | Qwen3-Embedding-8B (本地 GGUF)  | 4096 | `llama.cpp` 跑 `Qwen3-Embedding-8B-Q4_K_M` |
| `ollama` | `nomic-embed-text`              | 768  | Ollama Cloud `OLLAMA_API_KEY` 或本地 Ollama server |
| `openai` | `text-embedding-3-small`        | 1536 | `OPENAI_API_KEY` |
| `hash`   | (deterministic fallback)         | `EMBEDDING_DIM` | 離線選擇 |

`auto` 模式會依序 probe llama.cpp → Ollama → OpenAI → hash。

### 怎麼解（兩個路徑二選一）

**(a) 保留 collection dim，換 backend**
適合：現有 collection 重要、不想重 ingest。

```bash
# 例子：改用 Ollama Cloud 的 qwen3-embedding:8b
set OLLAMA_EMBED_MODEL=qwen3-embedding:8b
set EMBEDDING_BACKEND=ollama
python cli.py stats   # 確認 native_dim=4096 == collection_dim
# search / chat 不用 recreate，只要以後都使用 ollama backend
```

**(b) 重新 ingest 適應 backend**
適合：本機只有 768-dim 的 Ollama / OpenAI 1536-dim，又不能跑
4096-dim Qwen3。

```bash
# 例子：改用 Ollama Cloud 的 nomic-embed-text（768-dim）
set OLLAMA_EMBED_MODEL=nomic-embed-text
set EMBEDDING_BACKEND=ollama
set EMBEDDING_DIM=768
python cli.py delete-collection
python cli.py ingest ./documents --recursive
python cli.py stats   # 確認 collection_dim=768 == native_dim=768
```

### 快速診斷銘語

- **search 回 0 個結果但有資料** → 幾乎肯定是 dim mismatch，跑 `stats` 看
- **llama.cpp URL 設了但 `llamacpp_available=False`** → server 沒起，
  `auto` 會走下一個 backend（Ollama）→ 預期不到 dim mismatch
- **`hash` backend 被偷偷用** → 3 個 backend 都不通。`stats` 會看到
  `llamacpp_available=False / ollama_available=False /
  openai_available=False`。hash 是非語意的，search 品質會崩。
- **換 backend 後沒改 `EMBEDDING_DIM`** → 記得同步，否則 `_normalize`
  會自動 pad/truncate 進到 collection dim，但 native 語意不一定
  能完整存下來。

## 與 milvus-mcp-agent 的關係

本專案是 milvus-mcp-agent（v0.1.19.4）的 sibling 專案。Vector store 換成 Qdrant，
**其餘架構（5 個 MCP tools、CLI 子命令、`.env` 規範、開發紀律）保持不變**。

共用資源：
- Loader（多格式解析）
- Chunker（RecursiveCharacterTextSplitter）
- Embedder（OpenAI + hash fallback）
- Agent（CITATION RULES、Ollama/OpenAI provider 自動切換）
- UI md_render（ANSI-aware BBS-style）
- BBS chat REPL + Session 管理（v0.2.0 ported）

差異點：
- `src/vector_store/qdrant_client.py`（取代 milvus_client.py）
- 沒有 IVF_FLAT / nlist 參數；HNSW 是預設
- 沒有 file lock 議題（Qdrant 是 HTTP server）— 所以 chat REPL 不需要 `--mcp` subprocess 架構，直接走 in-process Retriever


## 詳細文件與歷史
- `docs/chat_flow.md` — Chat 問題處理流程**詳細版**（從 README 拆出的 1,700 行逐段流程、降級規則、工具對照）
- `CHANGELOG.md` — **版本異動**與歷史記錄（v0.2.0 起、最新在前；README 只保留現況，異動不再塞在這裡）
- `USER_MANUAL.md` — 功能使用者手冊；`ARCHITECTURE.md` — 模組/資料流；`SKILL_GUIDE.md` — 技能檔案庫；`.clinerules/CLAUDE.md` — 開發紀律與版本演進

## TODO

- [ ] `cli.py ask --stream`：qdrant `Agent` 還沒有 `ask_stream()`（目前 `--stream` 會 `AttributeError`，需 port streaming from milvus）
- [ ] Streamlit Web UI
- [ ] Dockerfile + CI
- [ ] Performance benchmark vs Milvus Lite


---

## ⚠️ Clean-fork snapshot notes（本快照說明）

This repo is a **machine-clean snapshot** of the divine-sense-agent
workspace (v0.9.133.18). Deliberately NOT included:

- `megaSpeedy/` — third-party broker SDK binaries + user data.
  **`MEGA_STOCK_MODE=mock`（預設）完全不需要它**；live 券商下單請自行
  安裝官方 megaspeedy 元件（見 CLAUDE.md v0.9.24/26 段）。
- `backups/` — Qdrant snapshots contain ingested user documents.
- 真實機敏值 — Telegram chat id 等已替換為 `<YOUR_TELEGRAM_CHAT_ID>`；
  `.env` 與 `config/mcp.json` 從不入庫（請自 `.env.example` 與
  `config/mcp.json.example` 複製並填入自己的值）。

第一行設定：`copy .env.example .env` → 填入 `OLLAMA_API_KEY` 或
`OPENROUTER_API_KEY` → `qdrant-agent.bat ingest ./documents` →
`divine-sense-agent-web.bat`。


Qdrant 版本的 `milvus-mcp-agent` — 把 Milvus Lite 換成 Qdrant（GPU 加速 HNSW + Vulkan SIMD）。

> 📖 **使用者操作手冊**（全功能總覽）：[`USER_MANUAL.md`](USER_MANUAL.md)
> 📘 **內部設計 / 維護手冊**：見 [`ARCHITECTURE.md`](ARCHITECTURE.md)
> （資料流、cache 拓樸、加新功能的 SOP、除錯 checklist、與 sibling 同步策略）。
> 本 README 講「怎麼用」，ARCHITECTURE.md 講「為什麼這樣寫」。

