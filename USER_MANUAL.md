# Divine Sense Agent 完整使用說明書 (USER MANUAL)

> **Divine Sense Agent**（專案代號 `qdrant-rag-agent`）— 以 **Qdrant**
> 向量資料庫為核心的本機私人知識庫 RAG 助理。LLM + 27 個 MCP 核心
> 工具面 + Telegram 雙向對話 + 排程/背景任務/戰術編譯器 TACTI，一支程序
> 執行一切。本書為**使用者操作參考**；內部設計見 `ARCHITECTURE.md`，
> 戰術編譯器 TACTI 專冊見 `SKILL_GUIDE.md`（SKILL.md 技能檔案庫在 §8 末段）。
> 對應版本：**v0.9.127.0+**。

---

## 目錄

1. [認識本專案](#1-認識本專案)
2. [安裝與啟動](#2-安裝與啟動)
3. [設定 (.env) 總覽](#3-設定-env-總覽)
4. [CLI 指令完整參考](#4-cli-指令完整參考)
5. [GUI 與 Web UI 操作](#5-gui-與-web-ui-操作)
6. [聊天：Agent 能力面](#6-聊天agent-能力面)
7. [資料處理：Ingest / 搜尋 / 集合管理](#7-資料處理ingest--搜尋--集合管理)
 8. [戰術編譯器 TACTI (tacti-local)](#8-戰術編譯器-tacti-tacti-local)
9. [背景任務 (task-local)](#9-背景任務-task-local)
10. [排程 (scheduler-local)](#10-排程-scheduler-local)
11. [Webhook 觸發器 (webhook-local)](#11-webhook-觸發器)
12. [Telegram 全功能](#12-telegram-全功能)
13. [股市：兆豐 MegaAPI (stock-local)](#13-股市兆豐-megaapi)
14. [攝影機 ONVIF (onvif-local)](#14-攝影機-onvif)
15. [內網掃描 (netscan-local)](#15-內網掃描)
16. [長期記憶 (memory-local)](#16-長期記憶)
17. [Keep 筆記本 (keep-local)](#17-keep-筆記本)
18. [Jev 批次分類器 (jev-classifier-local)](#18-jev-批次分類器)
19. [AI 產圖 (imagegen-local)](#19-ai-產圖)
20. [中華電信雲 (hicloud-local)](#20-中華電信雲)
21. [Teams 桌面助理 (teams-assist)](#21-teams-桌面助理)
22. [本地核心工具 (exec / filesystem / archive / openwith / datetime / log / chat-history)](#22-本地核心工具)
23. [MCP 伺服器總表與設備編輯器](#23-mcp-伺服器總表與設備編輯器)
24. [安全模型](#24-安全模型)
25. [疑難排解](#25-疑難排解)
附錄 A：[環境變數快速索引](#附錄-a環境變數快速索引)
附錄 B：[資料夾/檔案地圖](#附錄-b資料夾檔案地圖)

---

## 1. 認識本專案

```
你說話 → Agent（LLM 工具迴圈，預算內自主呼叫工具）→ 工具面 27 個 LocalHost
                                            ↘ 外部 MCP（drawio/firefox_craw/google-calendar…）
結果：Qdrant 知識庫 / 檔案沙盒 / 排程·背景任務 / Telegram / 股市 / 攝影機 / 產圖…
```

**核心概念**：
- **LocalHost 工具**：程序內註冊的 MCP 工具艦（零 subprocess 開銷）。
 外部客戶端（Claude Desktop/Cline）用 `python -m src.mcp_server.<name>_server` stdio 面。
- **沙盒**：檔案工具一律在 `QDRANT_FILESYSTEM_READ_ROOT` / `WRITE_ROOT` 範圍內。
- **回合預算**：每個 LLM 回合最多 `MAX_TOOL_ITERATIONS` 次工具迭代，用盡如實收尾。
- **mock-first**：外部服務（股市/雲/Keep/Jev/產圖）預設模擬執行 — 開發期不碰真服務。

---

## 2. 安裝與啟動

### 2.1 依賴
- Python 3.10+（本機 3.14）
- Qdrant server：Docker `docker run -p 6333:6333 qdrant/qdrant`，
  或 Windows 原生 `C:\qdrant\start-qdrant.bat`（v1.15+ 有 GPU/Vulkan）
- Embedding 後端（擇一）：本機 llama.cpp Vulkan — 主 = `C:\llama.cpp-vulkan\start-llamacpp-4b.bat` + Qwen3-Embedding-4B（2560 維，v0.9.113.1 起）；舊 8B = `C:\llama.cpp-vulkan\start-llamacpp-8b.bat`（4096 維）/ Ollama / OpenAI / OpenRouter：
  ```bash
  cp .env.example .env   # 編輯填入至少一個 API key
  pip install -e .[test] # + GUI 需要 pywebview
  ```

### 2.2 啟動方式（四選一）

| 方式 | 指令 | 特點 |
|---|---|---|
| **CLI**（最省資源） | `python cli.py chat` / 進入點 `divine-sense-agent.bat` | BBS 風格 REPL，自動存 session |
| **GUI**（視窗） | `divine-sense-agent-gui.bat` → `python -m gui.app` | pywebview 視窗 |
| **Web UI** | `divine-sense-agent-web.bat` → `python -m gui.app --web` | 系統瀏覽器 http://127.0.0.1:8334 |
| **Web 綁定本機其他 IP** | `python -m gui.app --web --host 0.0.0.0`（banner 列 LAN URLs）/ IPv6 `--host ::1` | 手機可連；非 loopback 有安全警告 |

- `--lang zh-TW|zh-CN|en|ja`：UI/回應語言；`--no-browser`；`--debug --port 9229`（DevTools）。
- 啟動時自動恢復：排程 daemon / webhook listener / Telegram bot / ONVIF watches / Teams watch。

### 2.3 日誌
`gui/app.log` — **每日午夜輪替、固定保留 7 天**（`app.log.2026-10-04` 命名，
逾齡自動刪；v0.9.106.1 起）。Status 分頁 / `gui/app.log` 尾查錯。

---

## 3. 設定 (.env) 總覽

**`.env`** = 唯一事實源；**改檔後跑 `python cli.py reload`（或 GUI 🔄 reload）
即可生效，免重啟程序**。完整索引見附錄 A。分區速覽：

| 分區 | 主要 key |
|---|---|
| LLM 提供 | `LLM_PROVIDER_CHAIN`、`OLLAMA_API_KEY`、`OPENROUTER_API_KEY/_CHAT_MODEL/_REASONING_EFFORT`、`OPENAI_API_KEY/_CHAT_MODEL`、`OPENAI_COMPAT_*`、`SILICONFLOW_*` |
| OpenRouter 路由/快取 | `OPENROUTER_PROVIDER_ORDER=together`、`OPENROUTER_ALLOW_FALLBACKS=false`、`:nitro/:floor` 附加 |
| Embedding | `EMBEDDING_BACKEND=local|ollama|openai|openrouter|hash`、`EMBEDDING_DIM`、`LLAMACPP_EMBED_URL` |
| 儲存 | `QDRANT_URL`（**寫 `http://127.0.0.1:6333`**，勿用 localhost）、`QDRANT_COLLECTION`、`QDRANT_RAG_HOME` |
| 沙盒 | `QDRANT_FILESYSTEM_READ_ROOT` / `WRITE_ROOT` |
| 工具面 | `MCP_ACTIVE_SERVERS`（工具閘）、`MCP_SURFACE_MODE=full|index`、`MCP_SURFACE_MAX_TOOLS` |
| 迴圈控管 | `MAX_TOOL_ITERATIONS`、`QDRANT_TOOL_RESULT_MAX_CHARS`、`QDRANT_TOOL_CALL_TIMEOUT_S`、`QDRANT_MAX_CONCURRENT_TURNS`+`QDRANT_TURN_WAIT_SECONDS` |
| 壓縮 | `CHAT_CONTEXT_COMPRESSION=on|auto|aggressive|off`、`CHAT_OFFLOAD_THRESHOLD_CHARS=20000`、`QDRANT_OFFLOAD_DIR` |
| 任務併發/通知 | `QDRANT_TASK_MAX_PARALLEL`、`QDRANT_TASK_NOTIFY`、`QDRANT_TASK_NOTIFY_CHAT_ID`、`QDRANT_TASK_MONITOR_INTERVAL_S` |
| 路由器 v2 | `QDRANT_ROUTE`、`QDRANT_ROUTE_CLASSES`、`QDRANT_TIME_ROUTE`、`QDRANT_TOOLROUTE` |

**LLM Provider 自動鏈則**（`LLM_PROVIDER_CHAIN`）：逗號清單排優先序（空 = 舊順序
ollama→openrouter→siliconflow→openai（→`openai-compat` 附加第 5 provider）。
主 provider 兩次失敗 → **跨 provider failover** 走完整鏈。Vision OCR 鏈則獨立：
`VISION_PROVIDER_CHAIN`。

---

## 4. CLI 指令完整參考

進入點：`divine-sense-agent.bat <cmd>`（= `python cli.py`）。全域旗標：
`--mcp-config config/mcp.json`（預設）｜`--no-tools`（關工具呼叫、純 RAG）。

| 指令 | 用途 | 常用示例 |
|---|---|---|
| `ingest <path>[-r]` | 匯入檔案/資料夾（md/txt/pdf/epub/docx/pptx…；PDF 可 OCR） | `bat ingest .\documents -r` |
| `search "query" --top-k 5` | 語意搜尋（不經 LLM） | `bat search "HNSW" --top-k 5` |
| `ask "question" [--web-search --lang zh-TW --top-k 5]` | 單次 RAG 問答 | `bat ask "本專案怎麼部署" --lang zh-TW` |
| `chat [-s sessionID --web-search --lang zh-TW --top-k N]` | 互動 REPL（自動存 session） | `bat chat` |
| `stats` | 集合統計 + embedder 後端健檢 | `bat stats` |
| `sessions {list,show,delete,rename}` | session 管 | `bat sessions list` |
| `schedules {list,show,add,remove,enable,disable,run-now,history,daemon}` | 排程 | §10 |
| `todo {list,show,add,tree,progress,complete,delete}` | 多層 todo | §5 |
| `creds {list,show,add,remove,test}` | 帳號集（masked） | §14 |
| `delete-collection` / `recreate-collection` | 集合刪除/重建（資料會掉） | `bat recreate-collection` |
| `backfill-sparse` | 為既有 points 補 bm25 向量（hybrid 搜尋遷移） | `QDRANT_HYBRID=on` 重建後跑 |
| `audit-backends` | 唯讀掃描：列出 hash fallback 產生的向量點（v0.9.112+） | `python cli.py audit-backends` |
| `reembed-hash` | 用目前健康 backend 重嵌 hash 向量點（繞快取；v0.9.112+） | 重跑前先 `audit-backends` |
| `backfill-headings` | 為既有 points 回填 `heading_path` payload（metadata-only、向量不動、零重嵌；v0.9.113+） | `python cli.py backfill-headings`（STAIR 開啟前的舊點） |
| `keep-auth` | Google Keep OAuth 一次授權 | §17 |
| `reload` | 熱載 .env + 重啟 daemon 服務 + 重連 MCP | `bat reload` |
| `remove-files <filename...>` | 按檔名移除既有檢索文件 | `bat remove-files old.md` |

**REPL 內建指令**（`chat` 裡）：`/info` `/reset` `/list` `/load <id>`
`/tools on|off|status|list`（工具開關）`/web on|off|status`（網頁搜尋增源）
`/lang [code|auto]`；`exit`/Ctrl-C 離開。同題重問 → **問題去重**直接重播上答。

---

## 5. GUI 與 Web UI 操作

分頁一覽（pywebview GUI 與 Web UI 同源）——每 tab 對應一組 CLI：

| 分頁 | 能做什麼 |
|---|---|
| **聊天** | 對話（回覆帶 開始/結束時間 + token 使用 `↑N/↓M tok·N calls·⚡cached`）；工具 🧰 picker（**臨時**開關個別工具，存 localStorage；All on/off/Reset）；📎 上傳任意檔（進 write 沙盒 uploads/，可觸發 OCR）；Ctrl+V 貼圖；⚡ 片語面板（**多行片段** — 新增列=textarea、管理模式可 ✎ 逐條編輯；MCP `snippets-local` 同步管理、全裝置共用）輸出的圖片/截圖 **inline 預覽**+點一下放大；聊過即時儲存（送出即存+回覆後存+關窗 sendBeacon）；「📊 在圖表中開啟」按鈕把 `<mxGraphModel>` 訊息開進 Drawio Viewer 視窗（可編輯存回沙盒）；**v0.9.123.0+：執行 prompt 即時看到 ⚙ 執行 log 面板**（每輪 LLM 生成 / 🔧 工具呼叫 / 結果 ok·err·耗時；標題點擊折疊、🛑 中止按鈕）— 完成後照常渲染回覆，執行紀錄隨 session 存檔、歷史泡泡可「查看執行 log」回看；web 遠端裝置同一介面同一事件流（`start_turn`/`poll_turn` 兩段式、輪詢 1.2 秒） |
| **文件** | ingest（選檔/資料夾/OCR 選項）+ 集合瀏覽 + 移除某檔 |
| **Sessions** | 列出/重命名/刪除/開啟舊對話（durable，程序重啟不丟）；v0.9.133.7+ 每列帶**摘要**（💬 主題＋🤖 最新回答）；**Chat 分頁工具列 ✏️（v0.9.133.8+）直接改當前對話標題**（未存檔先自動建立；picker/Sessions 同步刷新） |
| **排程** | 排程 CRUD/enable/disable/run-now/history；daemon 開關；驗證旗標 `verified` 呈現 |
| **任務** | 背景任務面板（task-registry 全來源：running/done/failed + 通知進度） |
| **筆記** | Keep notes 查看/預覽（mock/local/live） |
| **🔑 Credentials** | 中央帳號集（masked 顯示；GUI/CLI 同一 store） |
| **集合** | collections 瀏覽/統計（log_docs/memory_docs/stock_docs/file_docs…） |
| **Admin** | MCP 設備編輯器（mcp.json CRUD + Save 熱替換 fleet）+ 🔄 reload + 服務重啟 |
| **狀態** | 版本（跟隨 pyproject）、provider/model、就緒、log |

手機/窄寬（≤820/480px）自動 RWD；Web 模式支援檔案上傳（含行動 📷 媒體 picker）。

---

## 6. 聊天：Agent 能力面

- **工具選擇規則**（LLM 系統提示內建）：本知識庫優先 `search_documents`；
  時間問題必呼叫 `get_current_time`；歷史查詢用 chat-history 三工具；
  未宣告的工具絕不假呼叫（discovery 一致性守衛）。
- **Route v2 快速路徑**：`QDRANT_TIME_ROUTE=on`（時間問句免 LLM 直接答）；
  `QDRANT_ROUTE=on`（時間/問候/狀態/天氣類別分流 — 問候語直接模板回覆）。
- **ToolRoute（工具面閘聯動）**：回合按問題類別自動限定相關 server 允許面，
 接續語（「ok」「執行」「繼續」）會把**上輪回覆宣告的工具**併回工具面（UNION-ONLY）。
- **回合防呆**：
  - 工具結果絕對上限 `QDRANT_TOOL_RESULT_MAX_CHARS`（預設 51200 字元，超過截斷+指引）
  - 大結果 offload（§3 壓縮區）→ pointer + 800 字預覽（`read_file` 讀回）；
    `read_file/read_multiple` 已**豁免 offload**（v0.9.107 洋蔥迴圈根因）
  - 工具呼叫 watchdog（`QDRANT_TOOL_CALL_TIMEOUT_S`，預設 300...live 600）：
    卡死呼叫 → 立刻回 tool error，不再無聲掛死
  - 空最終回答 wrap-up 總結；圖片幻覺守衛（宣稱送圖但沒 dispatch → 自動糾正一輪）
- **Result grounding**：結論需有工具回執支撐；依長期記憶作答時自動標註引用。
- **回應語言**：`--lang`/`/lang`/GUI 語言選單 — `zh-TW/zh-CN/en/ja`；`auto` 跟 UI。
- **自訂規則**：`QDRANT_SYSTEM_RULES_FILE` 指一個 md 檔（或把 `system_rules.md`
  放 read 沙盒）→ 每回合自動附加 USER SYSTEM RULES（上限 8000 字元截斷；
  改檔即生效）。
- **Agent Rulebook（v0.9.124.0+）**：agent 自己維護的規則簿 — READ 沙盒
  `agent_rules.md`，以 MCP 工具 `rules_add` / `rules_revise`（**revise 必附
  證據**，Evo² 證據門）/ `rules_rollback` 回滾 / `rules_history` 快照與 diff
  管理；寫入前自動快照（zero-dep 版本化）；每回合自動上 prompt（空檔無影響）。
  工具失敗時 LLM 收到一行修訂提示（`QDRANT_RULEBOOK_SUGGEST=off` 退出）；
  規則變動記憶 `kind="rule-lesson"`（`QDRANT_RULEBOOK_MEMORY=off` 退出）。
  CLI：`python cli.py rules list/show/add/revise/rollback/history`。
  TACTI 計畫失敗亦可在 handoff 檔看到 `skill_search` crossover 提示；
  `QDRANT_REFLECT_POSTMORTEM=on` 開啟失敗計畫的雙角色事後檢討（預設 off）。
  **v0.9.127.0 擴充**：①規則療效度控 — 每次工具失敗＝一次被觀察的
  prediction error，所有生效中規則自動累計 `errors`＋`last_error` 蓋章
  （繞過快照 ring；`rules_revise` 重置；`QDRANT_RULEBOOK_TELEMETRY=off`
  退出），rollback/晉升決策有量化證據；②ToolRoute 觸發列增補 —
  `toolroute_override_add`（confirm 閘 + evidence 閘）把新 (server,
  patterns) 寫進 READ 沙盒 `agent_toolroute.md`（快照 ring 同款），
  套用與否由 `QDRANT_TOOLROUTE_OVERRIDES` 決定（**shadow 預設＝命中只
  進 log 不掛工具面；on = union 掛上；off = 不用**）；rulebook-local
  工具面 5 → 7。
- **Context 壓縮**（`CHAT_CONTEXT_COMPRESSION`）：`auto`/`aggressive` — 歷史過長
  折疊成摘要（LLM 產出、失敗 fallback 抽取式）、超長訊息做選擇性壓縮（程式碼
  保持原樣）、超大工具結果 offload 成檔案。

---

## 7. 資料處理：Ingest / 搜尋 / 集合管理

- 支援：`.md .txt .pdf（含掃描 OCR） .epub .docx（圖片 marker） .pptx（image 說明）`
  及一般文字；資料夾 `--recursive`。
- **去重**：同一檔重匯 = 內容 hash 去重不重複堆積；改檔 = 覆寫同 point。
- **搜尋進階**：
  - **Hybrid（dense + bm25 sparse）**：以 `QDRANT_HYBRID=on` 重建成集合（schema
    含 sparse 欄位）；既有集合用 `backfill-sparse` 遷移。
  - **Rerank（QDRANT_RERANK）**、**查詢改寫（query rewrite）**、K-line **compact**
    （股票行情快照只攜帶近 10 根 → 其餘 offload 檔可回讀）。
- **嵌入看門狗 + hash 污染防護（v0.9.112+）**：嵌入最後一手是「非語意 hash」，
  本版起 ① pipeline 把實際產出向量的 backend 記進每一點的
  `embedding_backend` 欄位；② llama.cpp 嵌入伺服器由**看門狗**每 30 分鐘以
  「真實嵌入 POST」體檢（程序活著但每次 POST 失敗的殭屍態探得到）— 連續 2 次
  失敗會自動殺 PID 並重啟 `QDRANT_LLAMACPP_START_BAT` 指向的 bat（現行 = `start-llamacpp-4b.bat`；冷卻 600 秒；只信 HTTP 結果
  不信延遲；`QDRANT_LLAMACPP_WATCHDOG=off` 退出）；**v0.9.115.1+：連線被拒
  （refused = 程序整個不在；載模型中會先綁埠回 503）是明確死亡 → 第一次
  探測就立即重啟**（tasklist 查無 llama-server.exe 才動作，防誤殺載入中的
  模型；`QDRANT_LLAMACPP_WATCHDOG_REFUSED_HEAL=off` 回舊 2-streak 行為）；
  **v0.9.122.2+：同一套看門狗也照看 gemma2 媒體伺服器（:8081）** —
  同組週期/streak/冷卻/refused 旋鈕共用，自己的 bat 為
  `QDRANT_GEMMA2_START_BAT`（預設 `C:\llama.cpp-un\start-llamacpp-gemma2.bat`）、
  閘門 `QDRANT_GEMMA2_WATCHDOG`（空白 = 跟隨 `QDRANT_MEDIA_EMBEDDING`，
  顯式 on/off 覆蓋）— media 開著時 GUI/web 開機就跟 4B 一起自動帶起；
  log 前綴 `[gemma2-watchdog]` 可與 4B 區分；get_runtime_status 露
  `gemma2_watchdog` 段；
  ③ hash 向量可審計/重嵌：
  Admin 頁 Embedding 卡「🔎 審計 hash 向量 / ♻️ 重嵌 hash」或
  `python cli.py audit-backends` → `reembed-hash` — 審計以「重算 hash 向量
  精確比對」偵測（無標記的舊資料點也抓得到）；`QDRANT_EMBED_FAIL_CLOSED=on`
  則在嵌入退到 hash 時直接跳過該檔不寫入。
- **Image OCR / 描述**：上傳圖片 → `describe_image`（vision 鏈：ollama-cloud →
 openrouter → siliconflow → openai-compat；回 `vision_backend`）；`resize_image`。
- **集合管理**：單 backend 單維度 = 一個 collection；跑多 embedding 並行 →
  不同 `QDRANT_COLLECTION`（file_docs_local / file_docs_ollama…）分別 `recreate`。
- **STAIR 標題層級 metadata（v0.9.113+，預設 off）**：`.env` 設
  `QDRANT_HEADING_METADATA=on` → ingest 走「標題分段」：md 原生 `#`、
  PPTX 的 `# Slide N`、DOCX 的 Heading 樣式都會產生每塊的
  `heading_path`（「H1 > H2 > H3」堆疊）payload，搜尋結果帶出上下文層級。
  既有舊點（開啟前 ingest 的）跑 `python cli.py backfill-headings`
  回填（metadata-only、向量不動、零重嵌、`--force` 可重算）。
  搜尋精排可再開 `QDRANT_HEADING_BOOST=0.1`（heading 與問題詞重疊的
  命中加權）。注意 on 會改變分塊邊界 → 同檔下次 ingest 重嵌一次。
- **Embedding 品質對照工作台（v0.9.113+，`eval/`）**：換 embedding
  模型前先跑對照 — `python eval/ingest_side.py <side collection>
  <dim>` 把評測子集 ingest 進旁路 collection（主力 collection 不動），
  再 `python eval/eval_driver.py <side collection> <dim>` 跑 20 題基準
  （報 hit@5 / 名次 / 嵌入延遲）。實測：Qwen3-8B（4096d）hit@5 19/20
  vs Qwen3-4B（2560d）hit@5 19/19、嵌入更快、VRAM 減半。
  **v0.9.113.1：4B 已正式切換為主嵌入**（`QDRANT_COLLECTION=file_docs_4b`
  + `EMBEDDING_DIM=2560` + `EMBEDDING_BACKEND=local`；看門狗 bat 已同步
  指向 `start-llamacpp-4b.bat`）。舊 8B collection（`file_docs`，4248
  塊/4096d）**原封保留** — 回滾 = `.env` 改回 `QDRANT_COLLECTION=file_docs`
  + `EMBEDDING_DIM=4096` + 啟動 `start-llamacpp-8b.bat` 再重啟 GUI。
  快取鍵已帶 model 識別（v0.9.113）— 換模型/換維度不會再互撞向量。
- **媒體多模態側通道（v0.9.114+，EmbeddingGemma 2）— 嵌入分工規則**：
  文字 chunks 一律走 Qwen3-4B 主路；**文件內附圖（DOCX/PPTX media）與
  獨立圖檔**的語意向量走 EmbeddingGemma 2 的 768d 文字+影像+音訊
  統一空間（`C:\llama.cpp-un\start-llamacpp-gemma2.bat`，unsloth
  build + `--mmproj`，port 8081 與主 server 並行、0.4GB VRAM）→
  側路 collection `media_docs_gemma2`。兩道閘：`QDRANT_MEDIA_EMBEDDING=on`
  （文件附圖 side-car）+ `QDRANT_MEDIA_INGEST=on`（獨立媒體檔可直接
  ingest = vision 描述進 Qwen3 文字池 + 媒體向量進媒體池）。
  **跨模態搜尋** = `search_media` MCP 工具（文字 query 找圖/媒體向量；
  通路不可用時誠實回覆並指向 `search_documents` 的描述檢索 — 媒體
  語意無論如何都在 Qwen3 池可用）。✅ **v0.9.122.1 上線完成（2026-10-13）**：
  上游 #30054（b11457+ build）已把媒體嵌入接進 embeddings 端點
  （#30082 為形狀誤報已撤回；正確請求 = 巢式 content parts），
  **gemma2 server 已升級** unsloth `b11541-mix-2500203` Windows-Vulkan
  （原 b11443-mix 保留於 `C:\llama.cpp-un\_old_b11443\` 可回滾）。
  8081 探針實測 **verdict=REAL**（cos(red,blue)=0.8795；假模式 = 1.0）
  = 媒體向量點寫入已點燃：下次 ingest 的附圖 side-car / 獨立媒體檔 /
  `search_media` 跨模態檢索全部生效。
  音訊通路同樣真通（巢式 `input_audio`；220Hz sine vs 2kHz square
  cos=0.9114 ≠ 假 1.0）；AV 影片檔在 pipeline 仍跳過（端點通、AV ingest
  分流待下一版開啟；語音訊息的 STT 文字路不受影響）。

---

## 8. 戰術編譯器 TACTI (tacti-local)

**「workflow、戰術（tacti）、策略 — 需多個回合分工、非單回合可完成的工作 → 編譯 (compile) 後執行」**。
v0.9.109–120.2 期間名為「技能編譯器 (skill-local)」，v0.9.120.3 更名
TACTI — 「skill/技能」一詞自 v0.9.116 起專屬 **SKILL.md 技能檔案庫**
（§8 末段，skill-library-local），兩套系統不可混稱。

完整手冊 = 專案根 `SKILL_GUIDE.md`。速覽：

```
compile_plan(name, stages=[{title,prompt,depends_on?,budget?,verify?}],
             on_failure=stop|continue, notify_chat_id?)
   → 驗證（環偵測/去重/未知依賴）→ 依賴分波 → 併發分批（4/波）
     → 每 stage 注入：獨立回合+預算+立即 stat_file+verify_artifact+checkpoint+Telegram 回報
run_plan(plan_id)     → 波次驅動背景執行；RESUME（孤兒歸位/done 跳過/skipped 復活）
get_plan_status(id)   → 每 stage 狀態/驗證/錯誤
list_plans()          → 計畫庫（TACTI plans；SKILL.md 技能庫用 skill_list）
run_plan_stage(id,sid)→ 單一 failed/skipped stage 重跑
stop_plan(id)         → 停止（成果保留）
```

每 stage = 一個獨立背景回合（fresh Agent、共用工具艦、硬超時 1800s、
session `plan-<id>-<stage>`）；`verify` 不通過 = 失敗（never fake success）。

v0.9.127.0 補完（自我優化迴路）：
- **成功經驗固化**：plan 以 done 收場且 ≥3 stages → 自動草擬 SKILL.md 到
  `$QDRANT_RAG_HOME/skill_drafts/plan-<名><id>/`（meta 標 pending-confirm）—
  **永不自動入庫**；複核後 `skill_import(source=<資料夾>, confirm=true)`
  才進技能檔案庫（`QDRANT_PLAN_SKILL_DRAFT=off` 退出）。
- **驗證者異源**：排程 fire「宣稱成功但零驗證證據」觸發的糾正回合會改用
  鏈上**另一個** provider 模型（task_llm 可 pin 的 openrouter/ollama）；
  單供應商環境自動退回原行為（`QDRANT_VERIFIER_ALT_PIN=off` 退出）。

**技能檔案庫（skill-library-local，v0.9.116+）** — Claude SKILL.md
「規範文件包」的匯入/查詢/刪除/引用管理，與上面的 plans（可執行計畫）互補。
一技能一資料夾（SKILL.md + references/ examples/）存於
`QDRANT_SKILLS_DIR`（預設 `$QDRANT_RAG_HOME/skills`）：
`skill_import(source, confirm=true)`（本機資料夾全套附屬檔 / 單檔 / URL —
github blob 自動轉 raw；同名需 overwrite=true）→
`skill_list` / `skill_get(name)` / `skill_search(query)`（0 依賴關鍵字搜尋）→
`skill_remove(name, confirm=true)`（不可逆）→
**引用 `skill_use(name)`**：規範內文直接注入本回合（+usage_note），
此後的回覆與產出文件即遵守該技能。CLI：`python cli.py skills
{list,show,search,add,remove,use}`。chat 觸發：「匯入技能」「技能庫」
「引用 ste 技能」等。
v0.9.127.0 起，**成功的 TACTI plan 會自動在 skill_drafts/ 草擬 SKILL.md**
（僅草稿、永不自動入庫）——複核該資料夾後以上述 `skill_import`
入庫，即是「成功經驗固化」的標準路徑。

---

## 9. 背景任務 (task-local)

- `run_task_once(title, prompt, use_web_search?)` → 立即回 `task_id+sid`
  （session `task-<id>`）；一輪 LLM 在背景執行緒跑完整工具面。
- **併發上限** `QDRANT_TASK_MAX_PARALLEL`（預設 4；0=不限）：超限在
  投放點即拒＋分批指引（防 05:41 的 6 並行風暴）。
- **模型 pin（v0.9.110+）**：`run_task_once(prompt, title, llm="provider:model")` — 這個背景任務用你指定的模型（限
  `openrouter:…` / `ollama:…` 目錄；投放前驗證，未知模型直接拒絕
  並附最近候選；pin 住就不會被 failover 偷偷換掉）。不指定 =
  預設 LLM 鏈照舊。
- **探索可用模型**：`list_task_models(provider?, contains?)` —
  兩家目錄快照（openrouter ≈466 / ollama ≈17；TTL 快取 6 分鐘）。
- `list_tasks(status?, limit?)` / `get_task_result(task_id)` — registry 是
 **程序内存、重啟即清空**；durable 對帳用 `sessions/task-*.json`。
- **強制中止（v0.9.120+）**：`stop_task(task_id)` — 對 running 任務
  （含 Telegram 產生的回合）設定 stop 旗；回合在下一個工具段落
  合作收尾（最壞 ≈ 一次在飛工具呼叫），已完成成果保留，registry
  狀態 → `stopped`（`list_tasks` 露 `stop_requested`）。殭屍回合
  （已被超時仲裁棄置）同樣服從。
- **完成/失敗 Telegram 通知**（fail-closed）＋進度監控 daemon（§12/§3）。
- GUI：任務分頁；`chat：`「背景執行…」觸發。

---

## 10. 排程 (scheduler-local)

- **排程類型**：`interval_seconds`（錨點防漂移+追落）、`cron`（5 欄）、
  `once_at`（到點自動 disabled）。
- **MCP 工具**：`add/get/delete/enable/disable_scheduled_task`、
  `list_scheduled_tasks`、`run_scheduled_task_now`（真 fire）、
  `get_scheduled_task_status`（progress + `verify_required`/`last_verified`/
  `unverified_recent`）、`list_scheduled_task_runs`（per-run `verified`）、
  `get_scheduled_task_errors`、`verify_artifact`（第 11 工具 — 機械評估器）。
- **驗證三守則硬擋（v0.9.106+）**：fire 自動注入 VERIFICATION DIRECTIVE（每個
  產出強制 verify）；LLM 宣稱成功但無證據 → **自動加播一輪糾正**；
  `verified=True/False` 三態寫進 history/session（不美化）；豁免
  `QDRANT_SCHEDULE_VERIFY=off`／單一排程 `verify_required=false`。
- **生命週期**：`max_fires`（→ exhausted auto-disable）、`expires_at`（→ expired）。
- **恢復**：重啟自動 resume（`QDRANT_SCHEDULER_AUTORESUME=off` 可關）；
  錯過的 `next_fire_at` 第一 tick 追落。
- CLI：`schedules add --name --prompt --cron/interval/once-at --web-search
 --max-fires --expires-at --llm [--disabled]`；`schedules daemon` 前景執行。
- **fire 模型 pin（v0.9.111+）**：排程/webhook 觸發器可帶 `llm="provider:model"`
 （限 openrouter/ollama，建立時對真實目錄驗證、fail-closed）— 高頻 fire 配
 便宜模型直接省費；fire 時 pin 失效會自動降級預設鏈並警告（排程韌性優先）。

---

## 11. Webhook 觸發器

事件驅動的反面（排程）：每個 trigger 自有 URL + access key（**僅 show once**；
`get_webhook_trigger_url(reveal=true)` 可再顯）。HTTP 呼叫 → fire 預設 prompt
（payload 取代 `{payload}` 或以 `[Webhook payload]` 附加；`?async=1` 202 背景跑）。
生命週期同排程（max_fires/expires_at/status）。監聽器 `127.0.0.1:8335`
（`QDRANT_WEBHOOK_HOST/PORT`）；重啟自動 resume；401/404/403 分別（wrong key/
unknown/gate）。10 個 MCP 工具（add/list/get/url/delete/enable/disable/run-now/
runs/errors）。

```bash
curl -X POST "http://127.0.0.1:8335/trigger/<id>?key=<KEY>" -H "Content-Type: application/json" -d "{\"payload\": \"BTC 突破 100k\"}"
```

---

## 12. Telegram 全功能

**雙向 bot**（`QDRANT_TELEGRAM_BOT_POLLING=on` 預設開；純出站 HTTPS、零入站 port）：

- 白名單 chat：`TELEGRAM_ALLOWED_CHAT_IDS=<YOUR_TELEGRAM_CHAT_ID>`；每 chat 對應持久對話
  `tgbot-<chat_id>`（上下文窗口取最近 `TELEGRAM_HISTORY_CAP`（預設 12）；
  存檔 ring 上限 `TELEGRAM_SESSION_MAX_MESSAGES`（200））。
- 回覆自動 MarkdownV2（表格/程式碼保留）；長文自動分段；重試。
- **檔案入站**：照片 → 存沙盒 → OCR/描述回覆；文件/壓縮檔 → 入站沙盒 → 可
  進一步處理（翻譯/摘要/上傳知識庫）。
- **語音**：inbound voice → STT → 對話；回答可選語音播報
  （`QDRANT_TELEGRAM_VOICE_REPLY`，condense 至 `…VOICE_MAX_CHARS` 預設 50 字）。
- **session 指令**：`/new /clear /reset`（或「清除對話」）即場清空。
- **下單同意閘**：股市即時單發 unique code 至 approval chat →「同意/拒絕+code」
  → engine 等 gate 才送真單（§13）。
- **主動通知**（背景任務/排程/計畫完成、進度監控）— v0.9.108+；fail-closed。

---

## 13. 股市：兆豐 MegaAPI

`stock-local`：`stock_get_quote / stock_get_quotes / stock_get_kline /
stock_get_account_summary / stock_list_orders / stock_place_order /
stock_cancel_order`。

- **預設 mock**（`MEGA_STOCK_MODE=mock`）：記憶簿模擬、零 socket、
  回覆自帶 `simulated: true`（必須向使用者言明）。
- **live 三閘**：`MEGA_STOCK_MODE=live` + `MEGA_STOCK_TRADING_ENABLED=on` +
  每次呼叫 `confirm=true`；下單另需憑證（`MEGA_STOCK_CERT_PATH/PASSWORD`）。
  `MEGA_STOCK_ID/PASSWORD/ACCOUNT/BRANCH` 走 .env。
- 實機注意：SDK 執行序、開通時程、錯誤訊息 big5 → 已由引擎包裝為乾淨錯誤。

---

## 14. 攝影機 ONVIF

`onvif-local`（17 工具）：

- `onvif_discover`（WS-Discovery 內建）→ `onvif_favorite_add/list/remove`
（帳密走 credential sets：`cred_set` → `password_env` → 帳密欄；帳密 masked）。
- `onvif_get_snapshot`（無 snapshot 能力者 → 自動 **RTSP 抓幀 fallback**）；
  `onvif_rtsp_snapshot`（`uri=/channel=/settle_s=` 參數）。
- `onvif_record`（ffmpeg ` -c copy` 定時錄製 MP4 進 write 沙盒；秒數 1-600）。
- `onvif_get_profiles / get_streams / get_resources / get_device_info / test_credentials…`。
- **Phase C**：`onvif_ptz_move/stop/presets`（confirm-gated）；`onvif_get_events` +
  `onvif_watch_start/list/stop` — 事件 daemon threads（keyword 匹配 →
  歷史 ring → **webhook fire**，動作 → 觸發已註冊 trigger → 新 Agent prompt）；
  重啟自動 resume（`QDRANT_ONVIF_WATCH_AUTORESUME=off` 可關）。
- 進階：`get_profiles_anywhere`（跨廠 media endpoint 嘗試 8899 等）+ vendor
  RTSP 變體（standard/hikvision/dahua/xmeye）— XMEye DVR 特例也吃得住。

## 15. 內網掃描

`netscan-local`（純 stdlib）：`net_scan_hosts`（tcp/ping）、`net_scan_ports`、
`net_my_interfaces`。**預設僅私有網段**；公網目標需 `QDRANT_NETSCAN_ALLOW_PUBLIC=on`
。上限：MAX_HOSTS 4096 / MAX_PORTS 1024 / MAX_WORKERS 128。

## 16. 長期記憶

`memory-local`：`remember_info / list_recent_memories / search_memory /
purge_memories`（memory_docs collection；stored_at 生命週期）。Agent 對外部查詢
（web_search/web_fetch）結果自動存放（kind=query）→ 未來回合 recall＋
**引用標註**（「— 依長期記憶作答（取得的時間…）」）。

v0.9.111.0 補完：
- **使用追蹤**：被召回的記憶自動累計 `hit_count`/`last_hit`（背景
  thread，不擋回合）；`list_recent_memories` 回 `usage` 總表
  （never_hit / 30 天零命中數）— 清理失效快照的決策依據。
- **任務結論沉澱**：背景任務（run_task_once）與技能計畫終局的自動
  存成 `kind=task-result` — **只存不自動召回**（歷史快照不進自動
  上下文，避免過期數值誤導）；要翻舊結論 →
  `search_memory(kind='task-result', query='…')`。
  （皆可 `QDRANT_MEMORY_HIT_STATS=off` /
  `QDRANT_TASK_MEMORY_SINK=off` 退出。）

v0.9.127.0 記憶維護：
- **清理建議（DRY-RUN）**：`suggest_memory_cleanup(older_than_s?, limit?)`
  列出「零召回命中且超齡」的記憶（最舊在前，id/kind/摘要/age）——**只報告
  不刪除**；實際清理仍走 `purge_memories` / `forget_memory`（confirm 閘）。
- **每週維護排程（opt-in）**：`.env` 設 `QDRANT_MEMORY_MAINTENANCE=on` 後，
  開機自動建立「記憶維護（每週）」排程（cron `0 9 * * 1`）——fire 時只回報
  候選清單給使用者，**絕不自動清除**（同名排程存在 = 冪等不重建）。

v0.9.133.3 對話摘要沉澱（T+1）：
- **`summarize_sessions(days/llm/dry_run)`**（memory-local 第 7 工具＋
  `python -m src.agent.session_compactor`）：近期 session 檔逐檔壓成
  1-3 行 → `memory_docs` `kind=session-summary`（session_id 反向連結；
  冪等——已寫過的 session 跳過；寫入後精確回讀驗證）。
  **會自動召回**（LONG-TERM MEMORY 含 session-summary；task-result
  維持 store-NOT-recall）。預設 LLM pin `ollama:qwen2.5:7b`（CPU）。
- **每日排程（opt-in）**：`.env` 設 `QDRANT_SESSION_SUMMARY_CRON=0 3 * * *`
  → 開機自動建立「每日對話摘要沉澱（T+1）」排程（模型 pin 可用
  `QDRANT_SESSION_SUMMARY_LLM` 調整——`llamacpp:<alias>`＝直連本機
  llama.cpp CPU chat server（8090）／`ollama:<model>`＝直連本機
  ollama（11434）；端點鍵分別為 `QDRANT_SESSION_SUMMARY_LLAMACPP_URL`
  與 `QDRANT_SESSION_SUMMARY_OLLAMA_URL`）；`QDRANT_SESSION_SUMMARY_SINK=off`
  全關。

v0.9.133.11 排程 pin 修復（首次 03:00 自動火診斷）：
- `llamacpp:<model>` 現在是**正式 task pin provider**（排程/背景任務/
  技能階段都可 pin 直連本機 8090 CPU server；`list_task_models` 看得見
  本機目錄）。因本機 server 只有 12k context，pin 到 llamacpp 時工具面
  會自動縮到 `QDRANT_LLAMACPP_PIN_SERVERS`（預設
  `memory-local,datetime-local,meta-local`）——排程摘要的 fire 標準場景
  就是這個面。

v0.9.133.13 zipfs-local（把 .zip/.tar 當檔案系統，v0.9.133.13+）：
- **上傳一個 .zip／.tar（.tar.gz/.tar.bz2）**（任何檔案上傳通路）→
  對話裡直接說「列出 zip 裡面有什麼」「把這個檔案寫進 zip」「改名
  /刪掉壓縮檔裡的目錄」——LLM 用 `zipfs_list/read/write/delete/
  rename/extract_one/info/status` **在包內原地操作，不解壓**。
- 修改後**仍是同一個單檔**（路徑不變，如 `uploads/bundle.zip`）——
  前端直接用既有 ⬇ chip 下載。
- 限制：`.exe/.bat/…` 危險擴展成員拒收；member 數／未壓縮總量雙
  預算；刪除/改名需 confirm；**7z 只讀**（可列/讀/抽單檔，不可
  變異）；gz 單檔流請用 archive 工具。壓縮/解壓（不打開包內編輯）
  仍走 archive_create/archive_extract。

## 17. Keep 筆記本

`keep-local`（mock 預設 / local / live）：`keep_list_notes（搜尋·分類過濾） /
keep_create_note（confirm 閘；markdown 原樣） / keep_update_note / keep_get_note
 / keep_delete_note（不可逆 confirm） / keep_preview_note`（產生 sandbox 檢視檔）。
live 模式 = 真實 Google Keep；一次授權：`python cli.py keep-auth`（token.json）。

## 18. Jev 批次分類器

`jev-classifier-local`（mock 預設=hash pick；live 重用 OPENROUTER_API_KEY）：
`jev_schema` → `jev_classify`（payload_json inline 或 sandbox JSON 檔；
choice+機率+統計；`write_result=true` 寫沙盒）。**Route v2 / ToolRoute /
JevAbandon** 內部皆以 Jev 為分類引擎（mock 模式 = 離線可用）。

## 19. AI 產圖

`imagegen-local`：`image_generate(prompt, model?, aspect_ratio?,…?)/
image_gen_list / image_gen_models / image_gen_status`。**mock** = Pillow
placeholder（零網路）；**live** = OpenRouter Image API（真實費用約 $0.007/張，
usage.cost 回報）。產出 bytes 直寫沙盒 → 聊天 inline 顯示；兩道閘
（confirm=true + `IMAGEGEN_WRITES_ENABLED=on`）。

## 20. 中華電信雲

`hicloud-local`（mock-first）：VM 生命週期/清單/狀態/規格/快照等 56 工具面
（依 hicloud REST API v2 文件）— 開發/測試期零 HTTP；live 需 key
（`HICLOUD_*`），下單類工具同樣 confirm-gated。

## 21. Teams 桌面助理

`teams-assist`：`teams_assist_list_chats/config/drafts/status/start_watch` —
watch daemon 偵測目標畫面 → **judge-then-act**（新 Agent 判讀）→ 草稿/送出 gate
（不可自動打字送出）。**UIA 鏈**（`uia_teams_ops.ps1`）：`invoke`／`search` —
純 UIA 導航 + read-back 驗證（送出= InvokePattern； Teams web 的合成輸入守衛
擋不住 UIA）。配合 **os-input 工具組**（`os_*`，SendInput 真實鍵鼠 —
trusted-only 欄位用）：`browser_element_point → os_click_point (preview 先目視)
→ os_type_text → os_key_press`；`QDRANT_OS_INPUT_ALLOW=on` + per-call confirm +
foreground 標題 regex + 游標復原 + audit jsonl（fail-closed）。

## 22. 本地核心工具

| 伺服器 | 工具 | 要點 |
|---|---|---|
| `exec-local` | `run_shell / run_bash / run_grep / run_awk / run_python` + script-library | cwd 釘在 write 沙盒；超時 `QDRANT_EXEC_TIMEOUT`（預設 30s，硬 cap 120）；輸出上限 64KB；**災難黑名單**（rm -rf /、mkfs、del /f /s、diskpart…）；`QDRANT_EXEC_BLOCKLIST=off` 可關；`run_python` >24k 字元 → 暫存 .py（避 Windows 32k 命令行）；自動 CJK matplotlib bootstrap（`config/matplotlibrc`） |
| `filesystem-local` | 讀寫/複製/搬移/diff/patch/tree/搜尋/search_content | 沙盒 root 限制；**walk 排除** `.cache/.git/node_modules/AppData…`（v0.9.107）；Zip 拆解在 archive |
| `archive-local` | `archive_create/extract/list/formats` | zip/7z/tar/gz/py7zr；Zip-Slip 防護 + zip bomb 預算 |
| `openwith` | `open_file / open_content / list_openable_types` | 關聯程式開啟（Explorer 語意）；.drawio → 圖表 viewer 回呼 |
| `datetime-local` | `get_current_time` | 本地+UTC+雙鐘、epoch、星期 |
| `log-analysis-local` | `ingest_log / analyze_anomalies / search_logs / stats/sources/remove` | Apache/Nginx combined + syslog RFC3164/5424；規則引擎 + numpy 語意 outlier；入站可直接吃壓縮檔（成員範圍過濾）；專用 `log_docs` collection |
| `chat-history-local` | `list_chat_sessions / search_chat_history / get_chat_session` | READ-ONLY 過所有持久對話（caps 內建） |
| `meta-local` | `list_mcp_tools` | 主動檢查目錄（與工具面一致 + hidden_servers 揭示） |
| `qdrant-local` | `search/add/update/documents/stats/describe_image/resize_image`… | 知識庫本體 |
| `web-local`/`web_search MCP` | `web_search / web_fetch` | Ollama 託管搜尋（OLLAMA_API_KEY 同鑰） |

## 23. MCP 伺服器總表與設備編輯器

**27 個 in-process LocalHosts**（manager 自動註冊）：qdrant / web / filesystem /
scheduler / todo / openwith / log-analysis / archive / exec / datetime /
chat-history / stock / webhook / imagegen / meta / memory / task / cicloud /
onvif / netscan / runtime / cred / keep / jev-classifier / teams-assist /
**skill**（+hicloud）。

**22+ stdio mirrors**：`python -m src.mcp_server.<x>_server`（server / filesystem
 / scheduler / todo / telegram / openwith / log_analyzer / archive / exec /
 time / chat_history / stock / webhook / image_gen / onvif / net_scan /
 runtime / task / memory / hicloud / teams_assist / keep / jev / skill）。

**外部 MCP**（config/mcp.json 或 Admin tab）：stdio（subprocess）/ streamable_http
 / sse 3 transport；`${ENV_VAR}` 連線時替換；**連線失敗 = `failed` 記錄、不重試**
（重啟或 Admin Save 重連）；`firefox_craw`（get_page_html/get_x_post…
 google_search 撞名自動改名）與 `google-calendar`（讀取/事件/空閒 — 行事曆問題
一律真取，不從記憶）為主要遠端面。

---

## 24. 安全模型

- **沙盒根**：檔案類工具一律 root 內；相對路徑錨 write root；symlink 逃逸拒下鑽；
  危險執行檔（.exe/.bat…）拒寫/拒開。
- **confirm 閘**：不可逆/外部副作用工具（下單/產圖寫入/keep 寫改刪/PTZ/Teams
  送出…）皆要求 `confirm=true`；真實下單另加兩閘 + Telegram 同意（§13）。
- **fail-closed 通知/入站**：Telegram 白名單空 = 忽略全部；webhook 非回環 bind 警告
 + random key 常數時間比對；netscan 私網限定。
- **災難黑名單**（exec）+ zip 預算 + 4MB/10MB 內容與上傳上限 + base64 剝除。
- **觀測**：audit jsonl（os-input）、history.jsonl ring（排程/webhook）、
 verified 三態、ResultRef、TurnTiming — 一切輸出可追蹤。

---

## 25. 疑難排解

| 症狀 | 先查 |
|---|---|
| 回答用簡體/英文 | `--lang zh-TW`／GUI 語言；`/lang` |
| 搜尋為 0 / 集合空 | `bat stats`（Qdrant up? backend dim vs collection dim ✓?） |
| 每次查詢慢 2s | `.env` 的 `QDRANT_URL` 用 **127.0.0.1** literal（localhost 會走 ::1→ 拒絕 ~2s） |
| 工具都不見 | `MCP_ACTIVE_SERVERS`（v0.9.41+）清單＋拼字（拼錯 = 該組靜默隱藏）；`list_mcp_tools` 對賬 |
| LLM 404 / prompt too long | 削減 `MCP_ACTIVE_SERVERS`；工具結果上限已內建；`MCP_SURFACE_MODE=index` |
| 背景 task 全不見 | registry 重啟即清空 — 對帳 `sessions/task-*.json` |
| 排程沒在跑 | Schedules 分頁 daemon 狀態；重啟應自動 resume；missed fire 會追落 |
| Telegram 無回應 | `TELEGRAM_BOT_TOKEN`/白名單/`QDRANT_TELEGRAM_BOT_POLLING`；polling 瞬斷自動重試 |
| 卡住不動超時? | watchdog（600s tool / 1800s stage·telegram turn）會如實回 tool error；`QDRANT_TOOL_CALL_TIMEOUT_S` |
| 亂碼/tofu 圖表 | matplotlibrc 已內建（JhengHei）；確認 `run_python` 路徑產圖（非外部 PowerShell 直寫） |
| log 檔太多/太舊 | v0.9.106.1 起每日輪替留 7 天；舊 `app.log.1..3` 手動刪 |

---

## 附錄 A：環境變數快速索引

見 `.env.example`（同一事實源、含說明塊）。重要新 knobs 速查：

```ini
MAX_TOOL_ITERATIONS=50
QDRANT_TOOL_RESULT_MAX_CHARS=          # 空間=51200 預設；0=不限
QDRANT_TOOL_CALL_TIMEOUT_S=600         # 工具 watchdog
QDRANT_MAX_CONCURRENT_TURNS=           # 全程序回合併發；空=不限
QDRANT_TURN_WAIT_SECONDS=              # 排隊上限秒（預設 120）
QDRANT_TASK_MAX_PARALLEL=4             # run_task_once 併發
QDRANT_TASK_NOTIFY=on                  # 任務完成 TG 推送
QDRANT_TASK_NOTIFY_CHAT_ID=            # 預設第 1 個 allowlist chat
QDRANT_TASK_MONITOR_INTERVAL_S=300     # 進度推送
QDRANT_SKILL_STAGE_TIMEOUT_S=1800
QDRANT_SKILL_STAGE_TIMEOUT_EXTENSIONS=3  # 硬超時加窗上限（0=off）
QDRANT_TURN_HARD_TIMEOUT_EXTENSIONS=3  # Telegram 回合加窗上限（0=off）
QDRANT_SKILL_WAVE_CAP=4
QDRANT_SKILL_PLANS_DIR=
QDRANT_SKILLS_DIR=                     # 技能檔案庫根（預設 $QDRANT_RAG_HOME/skills）
QDRANT_SKILL_FILE_MAX_BYTES=2000000    # 匯入單檔上限
QDRANT_SKILL_LIB_MAX_FILES=64          # 單技能包檔案數上限
QDRANT_SKILL_LIB_BODY_CHARS=20000      # skill_get/skill_use 內文上限
QDRANT_SCHEDULE_VERIFY=off             # 全域退出排程驗證
QDRANT_SCHEDULER_AUTORESUME=off        # 關閉開機自動續跑
QDRANT_CONFIG_HOT_RELOAD=on + CONFIG_WATCH_INTERVAL=…  # 熱載 watcher
QDRANT_SYSTEM_RULES_FILE=…md
QDRANT_RULEBOOK_ENABLED=on             # agent 規則簿總開關（v0.9.124.0+）
QDRANT_RULEBOOK_FILE=…md               # 預設 <READ 沙盒>/agent_rules.md
QDRANT_RULEBOOK_MAX_CHARS=4000         # prompt 注入截斷
QDRANT_RULEBOOK_SUGGEST=off            # 關閉工具失敗修訂提示
QDRANT_RULEBOOK_HISTORY_MAX=20         # 快照 ring 深度
QDRANT_RULEBOOK_MEMORY=off             # 關閉規則事件記憶 sink
QDRANT_REFLECT_POSTMORTEM=on           # TACTI 失敗計畫事後檢討（預設 off）
QDRANT_RULEBOOK_TELEMETRY=off          # 關閉規則療效度控（errors 計數）
QDRANT_PLAN_SKILL_DRAFT=off            # 關閉成功 plan→SKILL.md 自動草稿
QDRANT_VERIFIER_ALT_PIN=off            # 關閉驗證糾正回合的異源模型 pin
QDRANT_MEMORY_MAINTENANCE=on           # 每週記憶維護排程（只報告不清除）
QDRANT_TOOLROUTE_OVERRIDES=on          # ToolRoute 增補套用（shadow/off/on）
```

## 附錄 B：資料夾/檔案地圖

```
$QDRANT_RAG_HOME (預設 ~/.qdrant-rag-agent)/
 ├ sessions/          對話持久 JSON（task-*/sched-*/webhook-*/tgbot-*/GUI）
 ├ schedules/         排程定義 + <id>.history.jsonl
 ├ webhooks/          webhook trigger 定義 + history
 ├ plans/             技能編譯產物（sk-*.json）
 ├ todos/ notes/ teams_assist/ onvif/（watches/favorites）/ credentials.json
 ├ embed_cache.db     embedding 磁碟快取（SQLite WAL）
 └ uploads/（write 沙盒）/ chat_offloads/（read 沙盒）
gui/app.log(.YYYY-MM-DD)   日誌（7 天保留）
config/mcp.json(.example)  MCP 設備清單；config/matplotlibrc
```

---

*手冊對應 v0.9.111.0；工具面 27 LocalHosts / 24 stdio servers 現況快照。
（v0.9.110：背景任務與 skill stage 可 pin 專用 LLM 模型；v0.9.111：
skill plan/stage 工具作用域 tools_allow + 機械交接檔 + fire 模型 pin +
記憶使用追蹤/任務結論沉澱。）*
*歷史細節以 `README.md 更新日誌`為準。*
