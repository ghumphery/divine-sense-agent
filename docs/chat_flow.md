# Chat 問題處理流程（詳細版）

> Moved out of README.md for skimmability; README links here.

---

## Chat 問題處理流程

`cli.py chat` 啟動後，使用者輸入一個問題到 LLM 回複，會經過以下 9 個階段：

```
1️⃣ 輸入問題 (cli.py _handle_chat)
    │ user input  = raw question string (e.g. "qdrant 跟 milvus 差異？")
    │
    ▼
2️⃣ Per-session 問題去重 (cli.py _find_recent_assistant_reply)
    │  SHA-1 fingerprint 比對 session.messages，若 fingerprint 重複
    │  → replay 上次 assistant 答案，不重複查 Qdrant / Web / MCP。
    │
    ▼
3️⃣ Agent.chat_turn() 調用 (src/agent/agent.py)
    │ 傳入 question, history, top_k, use_web_search, use_mcp 等參數。
    │ 
    ▼
4️⃣ _build_context() 拼裝 prompt context (Agent._build_context)
    │ 按優先序堆積 sections（每段可獨立關掉）：
    │ 
    │ (a) ### Local Documents
    │     Retriever.search() → Qdrant query_points() → top-k chunks
    │     每 chunk: [Document N] Source: \n<text>
    │ 
    │ (b) ### Web Search Results  (if use_web_search=True)
    │     OllamaWebSearch.search() → POST ollama.com/api/web_search
    │     每 hit: [Web N] <title>\nURL: <url>\n<content>
    │ 
    │ (c) ### Firefox Web Fetch  (legacy only: use_firefox=True & !use_mcp)
    │     FirefoxClient.fetch_for_question(query, use_google=True)
    │     為 firefox_craw server 跑一個 google_search 預取。
    │ 
    │ (d) ### MCP Tools  (if use_mcp=True)
    │     對 config/mcp.json 中每個 enabled server：
    │       - firefox_craw：調 FirefoxClient.fetch_for_question() 自動預取
    │       - 其他 server：呼叫 client.list_tools() 列出可用 tools + 描述
    │     每 server 一個 #### <name> sub-section。
    │ 
    ▼
5️⃣ _system_prompt() 拼裝 system message (Agent._system_prompt)
    │ Base instructions + CITATION RULES（一定要帶 Source filename）
    │ + context-specific rules：
    │   - use_web_search：加 [Web N] <title> (<url>) 引用規則
    │   - use_firefox (legacy)：加 [Firefox N] <tool>(<args>) 規則
    │   - use_mcp：加 [MCP N: <server>] <tool>(<args>) 規則
    │ 
    ▼
6️⃣ LLM 調用 (Agent.client.chat.completions.create)
    │ model: gpt-oss:120b-cloud (or OPENAI_BASE_URL 的 OpenAI-compatible model)
    │ messages: [
    │   {role: system, content: <system prompt>},
    │   {role: user, content: "Context:\n\n<context>\n\nQuestion: <q>\n\n..."
    │                "Please answer based on the context above."}
    │ ]
    │ temperature: 0.3  (chat_turn) / 0.3  (ask)
    │ 
    ▼
7️⃣ LLM 回答接收 + Markdown 渲染
    │ answer = resp.choices[0].message.content
    │ cli.py 用 src.ui.md_render.render_markdown() 渲染成 ANSI-aware
    │ Markdown（包含表格框 + CJK 寬度計算），避免 BBS 窄框破表格。
    │ 
    ▼
8️⃣ 記憶存檔
    │ mgr.append_message(session.id, {role: user, content: q, ts})
    │ mgr.append_message(session.id, {role: assistant, content: a, ts,
 sources})
    │ history.append(...) → JSON atomic write (tmp + os.replace) 到
    │ ~/.qdrant-rag-agent/sessions/<id>.json
    │ 
    ▼
9️⃣ BBS-style UI 輸出
    │ print assistant header (divine-sense-agent) + rendered answer
    │ + sources line (▌ 來源: ...)
    │ + _bbs_footer() menu
```

### Chat 問題處理流程重要設計決定

- **Local 優先**：本地 Qdrant chunks 永遠是第一個 context section。Web / MCP
  只用來補充本地沒有的資訊，不能取代。
- **CITATION 強制**：LLM 引用時必須帶 filename，不能只靠 `[Document N]` 標籤。
  這個限制在 `_system_prompt()` 裡寫死。
- **Graceful degradation**：任何環節失敗 (Qdrant down / OLLAMA_API_KEY 缺 /
  MCP subprocess 啟不動) 都會印 `[錯誤] ...` 或 `(error: ...)` 而不 abort REPL。
- **Per-session dedup**：同 session 內重發相同問題 (SHA-1 fingerprint match)
  直接 replay 舊答案，不重複查 Qdrant。節省 token 也讓使用者看到答案的一致性。
- **Context 壓縮（v0.9.10.0+，opt-in）**：`CHAT_CONTEXT_COMPRESSION=auto|aggressive`
  時，過長 history 會被三層壓縮 — 舊訊息摘要成一段 system 摘要（LLM 生成 +
  extractive 後備 + 前綴快取）、超大工具輸出卸載到 read sandbox 檔案（LLM
  之後 `read_file` 讀回）、超長單訊息保留 code/JSON 只壓縮周邊 prose。
  預設 off = 行為完全不變；詳見「更新日誌 v0.9.10.0」。
### v0.9.67.0 — Telegram bot 改用 MarkdownV2 + 一般 Markdown 轉換器

使用者需求:「mcp telegram bot 使用 markdown v2，而非一般 markdown，
需實作轉換器」。`send_telegram_markdown` 雖已送
`parse_mode="MarkdownV2"`，但 LLM 寫的是**一般 markdown** — 未跳脫的
`. - ( )` 等字元會讓 Telegram 以 "can't parse entities" 400 掉整封
訊息，且 `**bold**` 會原樣顯示（MDV2 粗體是 `*x*`）。

* **新模組 `src/telegram_md.py`** — `markdown_to_telegram_v2()`：
  一般（GFM 風格）markdown → 嚴格 Telegram MarkdownV2。佔位符策略：
  (1) 具備 Telegram 精確語意的結構先渲染為最終 MDV2 形態並以
  `\x00N\x00` 保護 — 圍欄程式碼（language token 清洗為
  `[A-Za-z0-9_+-]`、內部只跳脫 `` ` ``/`\`，未閉合 fence 也收尾）、
  行內程式碼、行內連結（文字全跳脫、URL 依規則只跳脫 `\`/`)`）、
  表格（整表轉 pre 等寬）；(2) 行級結構 — 標題 → 粗體、
  `-/*/+/` 項目符號 → `•`、任務清單 → ✅/☐、區塊引用保留 `>` 標記、
  水平線 → `—`；(3) 行內強調 — `***x***` → `*_x_*`、`**x**` →
  `*x*`、`__x__` 底線、`~~x~~` → `~x~`、單星/單底線斜體
  （word-boundary 判定 — `snake_case` 不被誤判為斜體）；
  (4) 其餘全部跳脫；(5) 依插入序**反向**還原（嵌套片段正確展開，
  例：粗體內含連結）。
* **接線 1 — `send_telegram_markdown`**（`telegram_server.py
  _do_send`）：MarkdownV2 模式先轉換再送（轉換後重新檢查 4096
  上限，錯誤訊息建議改用純文字工具）；Telegram 400
  （parser 拒絕）→ 以**原始文字**退回純文字發送（`mdv2_fallback:
 true`）— 訊息永不因 parser 邊角丟失；工具描述更新為「寫一般
  markdown，自動轉換」。
* **接線 2 — bot daemon 雙向回覆**（`telegram_bot.py`）：
  `TelegramBotClient.send_markdown()`（分塊 + parse_mode；單塊解析
  失敗 400 時該塊退純文字）+ `_deliver_reply()` 主回覆走
  `_send_reply_md()`（轉換 → `send_markdown`；client 無此方法或
  發送失敗 → 退 `_send_text` 純文字）。錯誤通知/語音摘要保持純文字。
* **測試** — 新 `tests/test_telegram_md.py` **28 tests**
  （三種跳脫範圍 ×3、轉換器 ×15、server 接線 ×5、daemon 接線 ×4、
  client 分塊 ×3 — 含巢狀粗體連結、`snake_case` 保留、未閉合
  fence、語言清洗、URL 範圍規則）；telegram 全套回歸 **145 passed**。

`pyproject.toml` 0.9.66.1 → **0.9.67.0**。

### v0.9.67.1 — 排程 fire 獨立上下文 + Telegram 發送完整性守衛（偽造「已發送」根因修復）

使用者回報:「手動觸發排程 下午2點股市異常分析 9e924b90ac9c 但沒有
訊息送至 telegram」。日誌鑑識（2026-09-16 15:50:52 手動觸發）：

* task 71ef2efe 15:51:10 done（answer=1243 字），**全程零工具呼叫**
  （連 web_search 都沒打），回覆卻宣稱「✅ 已成功發送至 Telegram
  （訊息長度 1,024 字元）」＝完全偽造；
* 對照組：同分鐘手動觸發的另一排程「積極型股票追蹤」正常呼叫了
  stock 工具 + `send_telegram_message`（真發送，ok:true）；
* 根因：該排程的 session（`sched-<id>-<ts>`）自 09-07 起被**重用**，
  已累積 14 次 fire（30 則訊息），每次 fire 帶「全部」歷史 — 固定
  prompt 重複越多次，模型越容易照抄歷史回覆的形狀、跳過工具呼叫。

* **fire 獨立上下文**（`scheduler.py::make_agent_on_fire`）—
  新 `_fire_history_limit()` + `QDRANT_SCHEDULE_FIRE_HISTORY`
  （預設 **0** = 每次 fire 獨立執行；session 檔仍累積完整記錄，
  只是 LLM 上下文乾淨；`2` = 保留前一組 Q/A；`-1` = 還原舊行為）。
* **Telegram 發送完整性守衛** — fire 結束後若 (a) prompt 含
  telegram 發送意圖、(b) 回覆宣稱已發送（regex：已發送/發送至/
  sent to…）、(c) 本回合 `agent.last_tool_dispatches`（v0.9.57.1
  機制）**無任何 `send_telegram_*`** → 觸發**糾正回合**：不重新
  分析，強制要求立即呼叫 `send_telegram_message`，糾正答案取代
  原答案寫入 session 與 `history.jsonl`；糾正失敗 → 保留原答案
  並記 WARNING（行為不變，不會 crash）。
* **測試** — 新 `tests/test_scheduler_tg_guard.py` **10 tests**
  （history cap ×4：預設 0 / env 2 取尾 / -1 還原全量 / resolver；
  守衛 ×6：偽造→糾正、已發送→不糾正、無意圖→不糾正、糾正失敗
  降級、helper 單元、session 記錄糾正答案）；排程相關全套
  **101 passed** 零回歸。

`pyproject.toml` 0.9.67.0 → **0.9.67.1**。

### v0.9.67.2 — fire 不再召回長期記憶（舊股價根因）+ 守衛 dispatch 形狀修復（重複發送根因）

重啟 web UI 後的驗證 fire（2026-09-16 16:46）暴露 v0.9.67.1 的兩個
新問題：

* **守衝誤判 → 重複發送**：16:46:39 fire 已真實呼叫
  `send_telegram_message`（ok:true），但 16:46:58 守衛仍判「未發送」
  → 觸發糾正回合 → 16:47:18 **第二次發送**（使用者收到兩封）。根因：
  `Agent.last_tool_dispatches`（v0.9.57.1）存的是**純字串名稱的
  `list[str]`**，而 v0.9.67.1 的 `_dispatched_telegram_send` 只解析
  dict/object 形狀 → 永遠匹配不到 → 每次都誤判。修復：先判 `str`，
  dict/attribute 形狀仍相容（測試 fake 用）。
* **fire 用了長期記憶的舊股價**（使用者回報：「異常分析使用長期記憶
  資料使用舊的股價」）：v0.9.59.0 的記憶管線把每次 fire 的
  web_search 結果存成 `kind=query` 記憶；fire 獨立上下文後（
  v0.9.67.1），每回合記憶召回反而把**之前的舊股價**注入「問今天」
  的 LLM 上下文。
* **修復 1 — fire 預設不召回記憶**：`chat_turn` /
  `_chat_turn_impl` / `_messages_with_user` 新增
  `use_memory: bool | None = None`（None = 預設召回；False = 本回合
  跳過召回且不查 store）；`make_agent_on_fire` 的主回合與糾正回合
  都傳 `_fire_memory_enabled()`（env `QDRANT_SCHEDULE_FIRE_MEMORY`，
  預設 **off**；on = 恢復需要延續脈絡的排程）。
* **修復 2 — 記憶 block 歷史快照硬規則**（一般 chat 也受益）：
  block 開頭明示「每筆記憶都是取得當下的歷史快照；涉及
  今天/最新/現在/即時的數據必須重新查詢工具，禁止把記憶數值當作
  當前資料引用」。
* **測試** — tg_guard 檔 +11（dispatch 形狀 ×4：字串匹配/不匹配/
  dict+object 相容/真發送不糾正；記憶開關 ×4 + resolver）、
  memory_store +3（enabled=False 不觸 store、歷史快照規則行、
  `_messages_with_user` 傳遞）；memory + scheduler + guard 全套
  **150 passed** 零回歸。

`pyproject.toml` 0.9.67.1 → **0.9.67.2**。

### v0.9.68.0 — Web/GUI 聊天介面語音雙向溝通（🎤 mic → STT ／ 🔊 回覆 → TTS）

使用者需求:「在web和ui 介面，提供語音雙向溝通的機制」。Web 模式
（`python -m gui.app --web`）與 pywebview GUI 共用同一套前端，語音
橋接直接建在 `gui/api.py` — 兩種模式走同一條 `api.call` 路徑：

* **voice-IN（dictation model）** — 聊天工具列新增 🎤 按鈕：點擊
  開始錄音（`MediaRecorder`，webm/opus，紅色脈衝狀態），再點停止
  （或 60 秒自動停止）→ blob 转 base64 →
  `Api.voice_stt(audio_b64, filename)` → `SpeechClient.transcribe`
  → **辨識文字插入輸入框**（使用者確認後自行送出）。音訊不落盤、
  不寫沙箱（base64 直接解碼餵 STT）；大小上限
  `QDRANT_UI_VOICE_MAX_MB`（預設 8 MB base64）。
* **voice-OUT** — 每個 assistant 氣泡新增 🔊 按鈕 →
  `Api.tts_speak(text)` → `SpeechClient.synthesize` →
  `to_browser_audio`（mp3/ogg 原生播放、PCM → wav）→ base64
  `data:` URL → 動態 `new Audio(...).play()`。**同時只有一個音訊
  在播**：點別顆氣泡停止目前播放、點同一顆按鈕切換停止。每段
  文字快取（LRU 16）避免重複付 TTS 費用。
* **SpeechClient 容器擴充**（`src/agent/speech.py`）：`audio_format_of`
  新增 webm（EBML `1A 45 DF A3`）與 mp4/m4a（`ftyp`）嗅探；
  `_to_wav_for_stt` 對非 wav 容器一律 ffmpeg 轉 16 kHz mono WAV；
  multipart 檔名/_mime_ 依容器如實對應（webm 不再誤掛 `.ogg` 名）。
  新 `to_browser_audio(data, container)`：mp3/ogg 直通、pcm → wav
  （ffmpeg 缺失 → None → 呼叫端以文字降級，不放壞掉的 audio 元素）。
* **共用設定**：與 Telegram 語音同一組 env（`TELEGRAM_STT_*/TTS_*`
  → `OPENROUTER_API_KEY` → `OPENAI_API_KEY`）；web shim 無需改動
  （`POST /api/voice_stt`、`/api/tts_speak` 走既有派發）。
* **安全/限制**：錄音經 getUserMedia — 需要**安全來源**
  （localhost 或 HTTPS）；綁 `0.0.0.0` 時從區網 IP 開啟的頁面
  會被瀏覽器擋下（`127.0.0.1` 不受限），失敗會以 toast 告知原因。
  未設定 STT/TTS key → 明確錯誤訊息（不 crash）。
* **測試** — 新 `tests/test_ui_voice.py` **30 tests**（容器嗅探 ×4、
  webm 轉碼/名稱對應、`voice_stt` ×7、`tts_speak` ×6、resolver ×2、
  真實 HTTP round-trip ×2、前端接線 source guards ×6）；
  telegram_voice 39 passed 零回歸（合計 69）。

`pyproject.toml` 0.9.67.2 → **0.9.68.0**。

### v0.9.68.1 — 修復 mcp.json 中無效的 `pi-computer-use` 條目（Connection closed 根因）

使用者回報 boot log：
`[mcp:stdio:pi-computer-use] connect failed: McpError('Connection closed')`。
診斷（live 驗證）：該條目是 `npx -y @injaneity/pi-computer-use` —
套件存在（npm v0.5.1）但 **`package.json` 沒有 `bin` 欄位**，npx
以 `npm error could not determine executable to run` 立即退出 →
stdio 子程序在 MCP handshake 前就死掉 → "Connection closed"。
而且讀該套件 README：它**根本不是 MCP server** — 是 Pi agent
平台的擴充套件（`pi install npm:@injaneity/pi-computer-use`），其
README 明言「not a replacement for ... MCP servers」。

* 修復：從 `config/mcp.json` 移除 `pi-computer-use` 條目（Windows
  桌面自動化已由 fleet 中的 `windows-mcp` 涵蓋）。
* 驗證：`McpManager.from_config` 重連 →
  `connected=['drawio','google-calendar','my_web','telegram',
  'windows-mcp']`、`failed={}` 零失敗。
* 防呆：`config/mcp.json.example` 的 `$comment` 新增 pitfall 說明 —
  stdio 條目必須是真 MCP server；npm 套件要先確認有 `bin`
  （`npm view <pkg> bin`）且講 MCP JSON-RPC 協定。

`pyproject.toml` 0.9.68.0 → **0.9.68.1**。

### v0.9.69.0 — Teams 桌面自動化代答助理（零 Microsoft 權限，mock-first MCP 擴充）

使用者需求:「chat 可以介接 Teams，提供個人的文字回應助理嗎」— 後續
釐清為「以**我的個人身份**代替我回答/討論煩人的問題」「不想手動轉
寄、討論可能很壟長」，且使用者**沒有也不會有 Microsoft Graph API
權限**（tenant 鎖死：不能註冊應用程式、不能同意權限）。Graph 委派
與 Bot Framework 路徑因此全部不可行；唯一能自動化且**零權限**的
架構是操作使用者**自己登入的 Teams 桌面客戶端**（經既有 windows-mcp
fleet 讀視窗/打字送出）— 訊息以使用者本人身份發出，對方無感。

以 MCP 方式擴充（hicloud/stock/onvif/webhook 同一套收斂模式），
**純增量、零侵入**：未啟用時行為與 v0.9.68.1 完全一致。

* **核心引擎 `src/teams_assist_tools.py`**（純邏輯、mock-first）：
  * screen-watch — `TeamsAssistWatcher` 便宜截圖 hash 輪詢
    （64×64 灰階 sha1）：畫面**沒變不花 LLM token**；uniform 畫面
    （鎖屏/全黑）跳過；`QDRANT_TEAMS_ASSIST_MIN_FIRE_INTERVAL`（預設
    120 s）冷卻防洪水；連續 5 次截圖失敗自動暫停 + 審計。
  * fire engine — `make_teams_assist_fire`（mirror
    `make_webhook_fire`）：fresh Agent `chat_turn`（judge-then-act
    配方，v0.9.41.1）、session `teamsassist-watch` 持久多輪（長篇
    討論承接）、task registry 登記、tuple/dict 雙契約。
  * **反捏造 guard**（v0.9.57.1 教訓）：回答宣稱「已回覆/已送出」
    但本回合無 `teams_assist_reply` 派發 → 一次糾正回合（補註冊或
    誠實回報；糾正回合明示「切勿重複打字以免雙重發送」）。
  * 狀態 `$QDRANT_RAG_HOME/teams_assist/state.json`（atomic tmp +
    os.replace）：水位線/paused/audit 環（50）。
* **`TeamsAssistLocalHost`**（host.py 檔尾追加，server `teams-assist`
  ，7 工具）：`teams_assist_status` / `teams_assist_check`（live 模式
  截圖存進 write sandbox `teams_assist/`，保留最後 3 張，供
  `describe_image` 讀）/ `teams_assist_start_watch`（**fail-closed**
  ：`TEAMS_ASSIST_ALLOW_CHATS` 空白拒絕啟動）/ `_stop_watch` /
  `teams_assist_reply`（**write gate**：AUTO off 時自動回合只能
  `confirm=false` 記草稿；使用者明確要求才 `confirm=true+manual=
  true`；打字一律由 windows-mcp 執行，本工具是送出後註冊+審計）/
  `teams_assist_pause` / `_resume`（「暫停 teams」煞車）。watch
  執行緒住在 host 內（OnvifLocalHost 模式）。
* **接線（全部既有模式）**：`McpManager.set_teams_assist_fire_
  callback`（WebhookLocalHost `attach_fire` 模式）+ `from_config`
  註冊於 Meta 前 + `Agent.mcp_manager` 接 `self._teams_assist_fire`
  + `Agent.shutdown()` 停止 watch + `gui/api.py::_resume_teams_
  assist` 開機自動恢復（prewarm 掛點；`QDRANT_TEAMS_ASSIST_AUTORESUME`
  off 可關；白名單空 = fail-closed 不恢復）。
* **人設代答**：`TEAMS_MY_NAME` / `TEAMS_MY_ROLE` /
  `TEAMS_REPLY_STYLE` 由 fire prompt 字面內嵌（LLM 看不到 env var
  — v0.9.30.3 教訓）；prompt 教導「代使用者本人回覆，不是以助理
  身份」+ 白名單內才處理 + AUTO off 不得打字送出。
* **stdio 鏡射** `src/mcp_server/teams_assist_server.py`（7 工具同
  面外；註明 AUTO-fire 迴圈要跑在 Agent 程序內需用 in-process host）。
* **`.env.example`** Teams 區塊（10 env vars 全記錄；預設全 off/
  mock — 零行為變更）。system prompt 新增 Teams desktop-assist
  trigger block（legacy `tools_enabled=False` 不含）。
* **測試** — 新 `tests/test_teams_assist.py` **51 tests**（resolvers
  ×5、guard ×4、watcher ×9、fire engine ×6、host 工具面+閘門 ×11、
  manager/agent 接線 ×7、boot resume ×5、stdio parity + prompt ×8）
  ；合併回歸 mcp_client/meta/telegram/boot-resume/webhook/gui-chat
  **180 passed 零失敗**；模組 import 煙霧 34 模組全綠。

`pyproject.toml` 0.9.68.1 → **0.9.69.0**。

### v0.9.70.0 — Teams 助理 UIA 零干擾通道（spike 實證 + 實作；解決「代答時不能動滑鼠鍵盤」）

使用者問題：「使用的時間，我是不是不能動我的 mouse 和 keyboard」→
接著問「整合 windows 虛擬桌面是否可以改善干擾」。分析結論：虛擬桌面
只改變畫面可見性、**不隔離輸入流**（合成輸入打字仍須把視窗帶到作用
中桌面前景 → 畫面被拉走更擾人）。真正解法 = **UI Automation（UIA）
背景操作**。spike 在本機 Teams（新版 `ms-teams.exe`，視窗 class
`TeamsWebView`）實測 **1.3 秒完成、零鍵鼠零搶焦點**：

* **spike 實證**（`scripts/uia_probe_teams.ps1`，保留供日後診斷）：
  1. 訊息輸入框有**穩定識別**：`name='輸入訊息'`、`AutomationId`
     前綴 `new-message-`，支援 **ValuePattern**（SetValue 實際寫入 —
     鐵證：填入後 `傳送 (Ctrl+Enter)` 按鈕出現且 `enabled=True`、
     支援 **InvokePattern** → 可背景送出）。
  2. Chromium quirk：填入後 read-back 仍回報佔位符 → 「輸入框有
     內容」的可靠代理訊號 = **傳送按鈕是否存在**（正好兼任讓路偵測）。
  3. 聊天 Document 暴露 **TextPattern** → 聊天內容可直接文字讀取
     （live 實測讀出 3,841 字元完整對話，連截圖 OCR 都可省）。
* **`scripts/uia_teams_ops.ps1`**（新）— `read`（TextPattern 讀聊天，
  唯讀）與 `send`（安全鏈：①傳送按鈕**已存在** = 你在打字 → **讓路**
  不碰不送 ②ValuePattern 填入 ③驗證按鈕出現（文字落地證據）
  ④**Invoke 恰一次** ⑤驗證按鈕消失（送成）；按鈕仍在 → 如實回報
  絕不重複觸發）。輸出單行 JSON（UTF-8）；文字經 UTF-8 暫存檔傳遞
  （CJK 命令列安全）。
* **引擎整合**（`src/teams_assist_tools.py`）：`_send_mode()`
  （`QDRANT_TEAMS_ASSIST_SEND_MODE` = `windows-mcp`（預設，行為不變）
  | `uia`）+ `_uia_ops()`（subprocess 執行 PS + JSON 解析 + reason
  中文提示表 `UIA_REASON_HINTS`；永不 raise）。
* **工具面 7 → 9**（`TeamsAssistLocalHost` + stdio 鏡射同步）：
  * `teams_assist_read_uia` — 唯讀，聊天文字直讀（替代截圖 OCR 路徑）
  * `teams_assist_send_uia(text, chat?, confirm, manual?)` — UIA 背景
    送出；write gate 與 `teams_assist_reply` 完全相同（AUTO off 只能
    `confirm=false` 記草稿；`manual=true` 繞過）；失敗（yield/未確認
    送出）記 `send-uia-failed` 審計、成功記 `sent-uia`
* **prompt 更新**：UIA 模式的 fire prompt 步驟 1 改用
  `teams_assist_read_uia`（失敗才退回截圖 OCR）、AUTO+UIA 步驟 5 教
  `teams_assist_send_uia`（單一段落 — ValuePattern 換行語意未驗證）；
  system prompt 增補 Send modes 說明。
* **UIA 模式的使用者體感**：watch（截圖 hash）+ 讀信（TextPattern）+
  送出（Value/Invoke）全鏈**零鍵鼠零搶焦點** — 可以一邊工作一邊全自動
  代答；唯一殘留的合成輸入只在 windows-mcp 模式。
* **測試** — `tests/test_teams_assist.py` 52 → **67 tests**（+15：
  send-mode resolver ×1、PS 腳本安全鏈存在性 ×1、script missing/
  bad-action/empty-text ×2、newlines collapse + 暫存檔清理 + JSON 解析
  ×1、run-failed ×1、bad-json + reason hint ×1、host send_uia 閘門
  ×4（draft/gate/manual成功/yield失敗/run失敗審計）、read_uia 透傳 ×1、
  prompt UIA 分支 ×3）；全套 **67 passed 零失敗**。

`pyproject.toml` 0.9.69.0 → **0.9.70.0**。

### v0.9.70.1 — 文件同步（docs-only）

v0.9.69.0 / v0.9.70.0 的**結構性文件**補齊（更新日誌先前已寫，本次
同步其餘文件面）：

* **`README.md`** — MCP Tools 總表新增「Teams 桌面助理」完整區塊
  （零權限架構、9 工具、watch 引擎、UIA 通道、安全模型）；架構圖
  LocalHost 列舉補 Teams-assist；stdio server 計數 19 → 20。
* **`ARCHITECTURE.md`** — header Version v0.9.68.0 → v0.9.70.0 +
  §1.2 弧線補 v0.9.69.0 / v0.9.70.0 兩段；TL;DR LocalHost 列舉補
  Teams-assist；Appendix A 新增 `teams_assist_tools.py` 條目、
  host.py 清單 + `manager.py` refs 補 `_teams_assist_local_host`、
  stdio servers 補 `teams_assist_server.py`、測試清單補
  `test_teams_assist.py` (67)、Top-level 補 `scripts/` 兩支 PS
  腳本；footer last-reviewed → v0.9.70.0。
* **`.clinerules/CLAUDE.md`** — LocalHost 計數 TWENTY-ONE →
  TWENTY-TWO + 列舉補 TeamsAssistLocalHost、stdio 19 → 20、§3 模組
  樹補 `teams_assist_tools.py` 與 `teams_assist_server.py`、§10 逐項
  清單補 TeamsAssistLocalHost。
* **`config/mcp.json.example`** — 新 teams-assist stdio worked
  example（9 工具說明 + env 繼承 + 「AUTO-fire 需 in-process host」
  註記）。

`pyproject.toml` 0.9.70.0 → **0.9.70.1**（docs-only，無程式/測試變更）。

### v0.9.87.4 — splash 版號 hotfix（`#splash-version` 的 v0.8.6 漂移殘留）

使用者回報初始化畫面顯示「v0.8.6」— **v0.9.21.1 的第二次漂移**：
當年修了品牌列 `#app-version` 的寫死字面值，但 **splash 的
`#splash-version` 元素漏網** — `gui/assets/index.html` 仍帶
`<p id="splash-version">v0.8.6</p>` 靜態 fallback（v0.8.6 時期寫死，
從未被 JS 更新）。

* **`gui/assets/index.html`** — `#splash-version` 靜態字面值清空
  （pyproject.toml = 單一真相來源，不允許第二個版本字面值）。
* **`gui/api.py`** — `get_readiness` payload 帶 `version`（
  `_APP_VERSION`，import 時從 pyproject 讀取）— splash 開場時第一個
  回應即有版號可用。
* **`gui/assets/app.js`** — `runReadinessChecks` 成功路徑把
  `r.version` 畫進 `#splash-version`（`textContent = 'v' + r.version`，
  與品牌列同款）。
* 測試：`test_web_server.py` +2（`#splash-version` 不得硬編碼版本 +
  全 HTML 不得出現 `v0.8.6` 字面值 + app.js 必須畫 splash 版號）；
  `test_gui_readiness.py` +1（get_readiness payload 帶 pyproject
  版號 parity）。gui_readiness 16 + web_server 30 + splash_e2e 6
  全綠。
* `pyproject.toml` 0.9.87.3 → **0.9.87.4**。

### v0.9.87.3 — splash 等 MCP 全部完成準備才切主頁（chat web 登入檢查）
### v0.9.87.3 — splash 等 MCP 全部完成準備才切主頁（chat web 登入檢查）

使用者要求「chat web 需確認所有 MCP 都完成準備，才能關閉登入檢查切到
主頁」— 檢查發現 v0.9.80.1 引入的輪詢被自己一段**重複的立即呼叫**
打掉：

* **`gui/assets/app.js`** — mcp 列輪詢排程後，一段殘留的
  `if (chk.mcp && probe['mcp']) { probe['mcp'](ok/fail) }` 會在第一個
  pending 回應（`ok:false, done:false`）時**立即**把 mcp 列結算成
  fail → `Promise.all(checkPromises)` 提前解算 → splash 在 fleet 還
  在建置時就消散。修：刪除該重複區塊 — pending 時保持 ⏳ + 繼續
  輪詢（每 1.5s 直到 `done !== false`、90s deadline 或 Skip）。
* **`done !== false` 語意** — 輪詢與首響應判定從 `if (m.done)` 改為
  `m.done !== false`：後端永遠帶 `done` 欄位，缺欄位（舊 payload）
  視為 terminal — splash 永不因缺欄位卡死。
* **waitOnce 上限 600 → 1800**（30s → 90s，對齊
  `SPLASH_MCP_TIMEOUT_MS`）— 慢 fleet 不再在建置中途消散。
* **e2e 回歸測試** — `tests/test_gui_splash_e2e.py` 新 fixture
  `mcp_pending_then_ok`（前兩次 get_readiness 回 pending、第三次
  done）+ `test_splash_waits_for_mcp_pending_then_ok`：assert 消散時
  mcp 列 = ok（舊碼會在 pending 時就消散、mcp 列 fail）；既有
  fixture（all_ok/all_fail）補 `done: true` 對齊真實後端形狀。
  splash_e2e **6 passed**。
* `pyproject.toml` 0.9.87.2 → **0.9.87.3**（純前端 + 測試）。

### v0.9.87.2 — teamsassist-watch 停用（使用者判定：功能不可靠）
### v0.9.87.2 — teamsassist-watch 停用（使用者判定：功能不可靠）

三層煞車全部拉上（**全部可逆** — 想再啟用反向操作即可）：

* **`.env`** —
  1. `MCP_ACTIVE_SERVERS` 移除 `teams-assist` → 12 個
     `teams_assist_*` 工具從 LLM 工具面消失（LLM 不再能啟動 watch，
     也省 prompt tokens）
  2. `TEAMS_ASSIST_ALLOW_CHATS=*` 註解掉 → 空白 = fail-closed（即使
     有什麼東西試圖啟動/恢復，watch 一律拒絕）
  3. 新增 `QDRANT_TEAMS_ASSIST_AUTORESUME=off` → 開機不再自動恢復
* **state.json** — `$QDRANT_RAG_HOME/teams_assist/state.json` 的
  `watch.running` True → **False**（atomic tmp + os.replace），開機
  恢復條件與 `teams_assist_status` 都回到誠實狀態。
* **執行中的 watch 執行緒** 隨下一次 app 重啟終止（thread 與程序同
  壽命；重啟同時載入 v0.9.87.1 hotfix）。
* 程式碼不動 — 功能本體保留，只是不再啟用。
* `pyproject.toml` 0.9.87.1 → **0.9.87.2**（設定變更）。

### v0.9.87.1 — hybrid search 啟用 hotfix（SOP 首跑抓到的三個問題）
### v0.9.87.1 — hybrid search 啟用 hotfix（SOP 首跑抓到的三個問題）

使用者照 SOP 首跑 `recreate-collection`/`ingest`/`backfill-sparse`
的結果暴露三件事：

* **① schema 檢查路徑錯誤（fusion 從未真正觸發）** —
  `collection_has_sparse` 讀 `config.sparse_vectors_config`，但
  qdrant-client 的實際屬性在 **`config.params.sparse_vectors`** →
  永遠 False → fusion 靜默走 dense 回落（連前一晚的 live selftest
  都是「假成功」— dense 也命中所以看不出來）。修：主路徑
  `config.params.sparse_vectors` + 舊形狀 fallback；live 驗證
  `bm25 in schema: True` + spy 確認 query_points 收到
  **prefetch + FusionQuery**（RRF 1.000 top-1 命中華邦電 2344）。
  mock 測試同步真實形狀（+1 個 fallback 測試，22 tests）。
* **② `.env` 標記行錯位** — cmd `echo #...` 把 `#` 吃進了行中
  （`QDRANT_HYBRID=on #v0.9.87.0…`）→ 尾巴贅字讓嚴格解析 off。
  修：`hybrid_enabled()` 取**第一個空白前 token**（容忍行尾註解/
  贅字）+ live .env 該行清成乾淨的 `QDRANT_HYBRID=on`。
* **③ `ingest <壞路徑>` 噴 traceback**（rglob FileNotFoundError）
  → `_handle_ingest` 先檢查路徑存在，友善錯誤 + 使用提示。
* live：`file_docs` 已帶 bm25 schema 重建完成（空 corpus，
  backfill 0 點）；待使用者重新 ingest 語料後 hybrid 即全面生效。
* `pyproject.toml` 0.9.87.0 → **0.9.87.1**。

### v0.9.87.0 — Hybrid search（dense + BM25 sparse RRF fusion，Agentic-RAG slice ③）
### v0.9.87.0 — Hybrid search（dense + BM25 sparse RRF fusion，Agentic-RAG slice ③）

檢索策略的根本升級 — dense embedding 對**精確 token**（股票代號
2344/6770、人名、型號）是死穴，BM25 語意補上。Qdrant 1.10+ 原生
sparse vectors + RRF fusion，零新依賴（`QDRANT_HYBRID` off 預設 =
byte-identical dense-only）：

* **新 `src/vector_store/sparse.py`** — 依賴自由 tokenizer：CJK
  **bigram**（不裝 jieba 的中文解法）+ 小寫英數詞；確定性
  token→index（sha1 前 6 hex，2^24 空間）、權重 = 詞頻；sparse 設定
  用 `modifier=IDF`（Qdrant 伺服器端算 IDF = 正統 BM25 權重）；
  `collection_has_sparse()` 檢查 schema（舊 corpus 沒有 → 一律
  dense 回落）；`backfill()` 走 scroll + update_vectors 補建。
* **`QdrantStore`** — `_ensure_collection` 在 hybrid on 時帶
  `sparse_vectors_config` 建立 schema（單一 create 呼叫，sparse
  失敗不擋建立）；`upsert` 時 schema 有 sparse 欄位 → 每點送
  `{""(dense), "bm25"(sparse)}` 雙腿；`search()` 新 `query_text`
  參數 → hybrid on + schema 支援 → **一個 `query_points` 呼叫雙
  prefetch + RRF fusion**（limit = top_k×2 候選池）；任何 fusion
  失敗 → dense 回落 fail-open；fusion 模式無 cosine score_threshold
  （RRF 排名分數，真正的評分由下游 rerank 做）。`backfill_sparse()`
  方法 + `cli.py backfill-sparse [--batch]` 子命令（舊 corpus 遷移；
  需先 recreate 建立欄位）。
* **`Retriever.search`** 傳 `query_text=query`（含 query-rewrite 後
  的查詢）— CLI/MCP/GUI 三條路徑同時獲得 hybrid。
* Env：`QDRANT_HYBRID`（off 預設）；config.py 記錄 + .env.example。
* 測試：新 `tests/test_hybrid_search.py` ×**21**（tokenizer CJK
  bigram/英數/確定性/標點 ×4、sparse 向量形狀/IDF 設定 ×4、schema
  gate ×2、store 接線：fusion 請求體/hybrid off 純 dense/schema 無
  sparse 回落/fusion 失敗回落/ingest 雙腿 dict/legacy list ×6、
  backfill 需 schema/走訪更新/無文字跳過 ×3、create schema on/off
  ×2）。
* **live E2E**：拋棄式 collection 建立（sparse 欄位在 schema）→
  ingest 3 點 → FUSED `2344 報價` top-1 = 華邦電 2344（RRF 0.64，
  ticker 命中）→ `南亞科 財報` 正確 → 刪除 ✓。
* 舊 corpus 遷移 SOP：`set QDRANT_HYBRID=on` → `recreate-collection`
  → `ingest`；已有 corpus：recreate 後 `backfill-sparse`（無需
  重跑 embed — dense 向量不動，只補 bm25 腿）。
* `pyproject.toml` 0.9.86.0 → **0.9.87.0**。

### v0.9.86.0 — Agentic-RAG slices ①②（查詢改寫閘 + 答案支持驗證）
### v0.9.86.0 — Agentic-RAG slices ①②（查詢改寫閘 + 答案支持驗證）

借鏡 Agentic RAG 圖卡（@PythonPr 2026-09-20）的兩個缺口，沿 .84.x
決策閘門模式落地（皆預設 off，shadow 先行）：

* **① 查詢改寫閘**（新 `src/agent/query_rewrite.py` + Retriever
  單一插入點）— `Retriever.search` 在 embed 前跑一次 `jev_noul`
  「需要改寫嗎？」（<12 字元短查詢跳過）；p ≥
  `QDRANT_REWRITE_PROB`（0.6）→ 一次便宜 LLM（`QDRANT_REWRITE_MODEL`
  預設 gpt-4o-mini, max_tokens 200, temp 0）把口語問題改寫成檢索
  查詢；**shadow 計算並記 log 不套用**（`[Rewrite] SHADOW orig=…
  new=…`）；任何失敗（無 key/HTTP/空輸出）保留原查詢 fail-open。
  覆蓋 CLI/MCP search_documents/GUI 全三條路徑（與 rerank 同點）。
  診斷存 `Retriever.last_rewrite_meta`。
* **② 答案支持驗證**（agent.py `_check_answer_support`，掛在
  `_chat_turn_impl` 工具路徑尾端）— 本回合有用 RAG 內容（記憶命中
  或 search_documents 派發）的答案：一次 `jev_noul`「是否含未被
  檢索內容支持的斷言？」→ `on` 且 p ≥ `QDRANT_SUPPORT_CHECK_PROB`
  （0.7）→ **一次無工具糾正回合**「僅依檢索內容重寫，未支持的部分
  明說資料中沒有」→ 新答案過 `_append_memory_citation` 後取代原
  答案；shadow 只記 log；Jev 失敗/低信心/糾正失敗/空輸出 → 保留
  原答案 fail-open。糾正 create 不帶 tools。
* Env：`QDRANT_REWRITE`(+`_PROB`+`_MODEL`)、`QDRANT_SUPPORT_CHECK`
  (+`_PROB`)；config.py 記錄 + .env.example + live .env 註解標記。
* 測試：新 `tests/test_query_rewrite.py` ×**11**（resolver ×3 +
  off 不呼叫/短查詢跳過/shadow 計算不套用/on 套用/低信心/Jev 失敗/
  LLM 失敗保留原查詢 + Retriever 掛點驗證 embed 收到改寫後查詢）；
  新 `tests/test_support_check.py` ×**11**（resolver ×2 + off/無
  RAG 跳過/記憶命中也算 RAG/shadow 不糾正/on 糾正帶 no-tools note/
  低信心/Jev 失敗/糾正失敗保留原答案/空輸出保留原答案）。
* `pyproject.toml` 0.9.85.0 → **0.9.86.0**。

### v0.9.85.0 — 大量 tool call 的三分收斂（Ollama 冷卻 + Jev 探索放棄閘 + 批次工具）
### v0.9.85.0 — 大量 tool call 的三分收斂（Ollama 冷卻 + Jev 探索放棄閘 + 批次工具）

使用者問「大量 tool call 改由 jev 觸發？」— **Jev 是分類器不生成
參數，取代不了規劃**；但三件套砍掉重複成本（live log 2026-09-22
12:49 推文回合：16 次迭代、688k tokens、撞 20 上限）：

* **① Ollama 端點冷卻**（`src/tools/web_search.py`）— 當天每個
  `web_fetch` 都白付一次 `ollama.com/api/web_fetch` 404 來回。
  404 → 該端點冷卻 `OLLAMA_WEB_ENDPOINT_COOLDOWN_S`（預設 300s,
  0=off）— 期間直接跳過死腿（wrapper 照常降 OpenRouter）；
  5xx/401 不觸發、成功即清除、search/fetch 各自獨立。
* **② Jev 探索放棄閘**（`src/agent/agent.py`，.84.x 同族）— 工具
  迴圈追蹤**連續**空結果/錯誤（`_tool_result_failed` 啟發式：空
  字串 / `"content": ""` / `（參數定義：` 錯誤信封 / Error 開頭）；
  連續 ≥ `QDRANT_JEV_ABANDON_STREAK`（3）→ **一次** `jev_noul`
  「是否該停止嘗試變體、改用現有資訊作答？」→ `on` 且 p ≥
  `QDRANT_JEV_ABANDON_PROB`（0.75）→ 注入「停止探索」system note
  （**不拔工具**，模型仍可發最後一通真正需要的呼叫）；shadow 只
  記 log；Jev 失敗/低信心 fail-open。每回合至多一次閘門決策；
  成功結果重置 streak。
* **③ 批次工具** — `web_fetch_batch(urls=[...])`（WebSearchLocalHost
  第 3 個工具；ThreadPoolExecutor 4 workers 並行、每 URL 失敗
  containment、上限 `QDRANT_WEB_FETCH_BATCH_MAX`=8）+
  `stock_get_quotes(symbols=[...])`（MegaStockLocalHost 9 工具；
  串行 — live engine 共用 session；上限 `QDRANT_STOCK_BATCH_MAX`=
  10；stdio mirror 同步）。系統提示詞加兩行觸發規則（多網址一次
  抓 / 多標的一次問）。
* Env：`OLLAMA_WEB_ENDPOINT_COOLDOWN_S` / `QDRANT_WEB_FETCH_BATCH_MAX`
  / `QDRANT_STOCK_BATCH_MAX` / `QDRANT_JEV_ABANDON`（off 預設）+
  `_PROB` + `_STREAK`；config.py 記錄 + .env.example。
* 測試：`test_web_search_fallback.py` 24 → **32**（404 冷卻/非
  404 不觸發/成功清除/0=off/search 獨立冷卻 + batch 工具面/平行
  結果/失敗 containment/上限）；新 `tests/test_jev_abandon.py`
  ×**11**（resolver ×3 + 啟發式 + off 不呼叫/shadow 不注入/on 注入/
  低信心/Jev 失敗 fail-open/成功重置/每回合一次）；stock host 28 →
  **30**（批次 e2e + parity + prompt）。
* `pyproject.toml` 0.9.84.4 → **0.9.85.0**。

### v0.9.84.4 — OpenRouter web server tools 接入（web_fetch 主路徑 + web_search opt-in）
### v0.9.84.4 — OpenRouter web server tools 接入（web_fetch 主路徑 + web_search opt-in）

使用者要求把 OpenRouter 的 server tools 接入服務：
`openrouter:web_search` / `openrouter:web_fetch`（beta,走 chat
completions 的 `tools` 陣列）。**live 探針 2026-09-22（8 次實驗矩陣）**：

* **web_fetch 可靠執行** — gpt-4o-mini 會真的發 tool_call,OpenRouter
  伺服器端抓取+擷取,raw 文字經模型回答帶回（engine auto / exa /
  native 全 200、`max_content_tokens` 參數接受;`engine: openrouter`
  與 `max_results` 參數被 400 拒絕 — beta quirk）。llama-3.1-8b 會
  寫 text-shaped 假呼叫（v0.9.13.2 教訓重演）→ fetch 需真
  tool-calling 模型。
* **web_search 不執行** — 兩個模型（llama-8b / gpt-4o-mini）在裸
  呼叫、強制指示、甚至 `tool_choice` 強制下都**不發 tool_call**
  （「I cannot call external tools」;cost ~$0.00003 = 搜尋根本沒跑）
  → beta search tool 不可靠。
* **最終設計**（誠實收斂）：
  - **fetch** → `openrouter:web_fetch` 當主路徑（`OPENROUTER_WEB_FETCH_MODEL`
    預設 `openai/gpt-4o-mini`;`parameters` 只帶
    `max_content_tokens=20000`,`engine` 僅在 `OPENROUTER_WEB_FETCH_ENGINE`
    設定時帶,預設 omit = auto）;direct-GET 保留為最終降級。
  - **search** → 已證實的 legacy web plugin 當主路徑（annotations
    + 內文,~$0.007/次）;beta server tool 走
    `OPENROUTER_WEB_SEARCH_MODE=server` opt-in（先 server tool →
    annotations 解析 → markdown 連結掃描 → plugin retry）。
* Env：`OPENROUTER_WEB_SEARCH_MODE`（plugin 預設|server）+
  `_SEARCH_ENGINE`（exa）+ `_FETCH_MODEL`（gpt-4o-mini）+
  `_FETCH_ENGINE`（空=auto）— .env.example 區塊。
* 測試：`tests/test_web_search_fallback.py` 17 → **24**（預設
  plugin 不浪費 RTT、server 模式請求體 + annotations 直取、server
  失敗降 plugin、markdown 連結掃描、fetch server-tool 主路徑 +
  direct-GET 降級、engine/model env 覆寫 ×7）。
* live E2E：search（ollama 主路徑）3 筆真結果;fetch（server tool）
  example.com 真內容 135 字元。
* `pyproject.toml` 0.9.84.3 → **0.9.84.4**。

### v0.9.84.3 — web_fetch 鏈雙修（live E2E 2026-09-22 抓到的兩個真 bug）
### v0.9.84.3 — web_fetch 鏈雙修（live E2E 2026-09-22 抓到的兩個真 bug）

Telegram 追推文連結的回合（tgbot-<YOUR_TELEGRAM_CHAT_ID>）暴露 web_fetch 工具鏈
兩個 bug — fallback 每次成功取回網頁都白失敗：

* **bug 1（AttributeError）**：`WebSearchLocalHost._fetch` 讀
  `page.text` — `FetchedPage` 的欄位自 v0.2.8 就叫 **`.content`**；
  只有 ollama 404 → fallback 成功取回的頁面會走到這行 →
  `'FetchedPage' object has no attribute 'text'` 三連發。修：讀
  `.content` + 防禦性 `getattr`（頁物件形狀錯誤時回空字典，不再炸）。
* **bug 2（TypeError + fallback 被跳過）**：Ollama `/api/web_fetch`
  回 200 但欄位帶 null（`"links": null`）→
  `list(data.get("links", []))` 對 None 迭代爆 TypeError，且
  `FetchedPage` 建構在 **try 外面** — 異常直接穿透 `fetch()`，
  OpenRouter fallback 永遠沒機會跑。修：欄位全部 `str(x or "")` /
  `list(x or [])` 合併 + 建構移進 try（任何建構失敗降級 None →
  fallback 照常執行）。
* 測試：`tests/test_web_search_fallback.py` 13 → **17**（handler 讀
  `.content`、異形頁物件防禦、null 欄位合併、建構錯誤仍走 fallback
  ×4）。
* `pyproject.toml` 0.9.84.2 → **0.9.84.3**。

### v0.9.84.2 — 訊息路由 v2：單次 choice 分類 + greeting/status/weather 處理器
### v0.9.84.2 — 訊息路由 v2：單次 choice 分類 + greeting/status/weather 處理器

**每回合一次 Jev `choice` 呼叫把訊息分到處理器；高信心者跳過 LLM
工具迴圈**。v2 啟用時取代 .84.1 的時間 noul 路徑（無雙重 Jev 呼叫）；
v2 關閉時 .84.1 相容路徑照舊。

* **`src/agent/rerank.py`** — `jev_choice(instructions, state, options)`
  （choice + probabilities 解析；criteria 各 option 帶 null）。
* **`src/agent/agent.py`** — `_route_mode()`（QDRANT_ROUTE：off 預設
  | shadow | on）+ `_route_prob()`（0.85）+ `_route_classes()`（預設
  time,greeting,status,weather；未列出的類別回 LLM）+
  **`_jev_class_route(mgr, question, lang)`**：
  - `time` → 共用 `_format_time_answer(mgr)`（從 .84.1 抽出）
  - `greeting` → 4 語系模板（zh-TW/zh-CN/en/ja），**零工具零 LLM**
  - `status` → `teams_assist_status` 摘要（含閘門省下次數）
  - `weather` → **pre-fetch**：確定性 `web_search("<問題> 預報 中央氣象署")`
    → 回 `("prefetch", results)` → `_chat_turn_impl` 以**單次 LLM
    摘要**（system note 帶結果、無工具參數）完成回答 — 省工具迴圈往返
  - **shadow 只記 log**（`[Route] class=… p=…`）不派發；低信心/chat/
    未啟用類別/任何工具失敗 → 回落 LLM 主路徑（fail-closed）
* 接線：`_chat_turn_impl` v2 先行；`QDRANT_ROUTE` off 時 .84.1 的
  `QDRANT_TIME_ROUTE` 照常作用（雙保險不雙呼叫）。
* Env：`QDRANT_ROUTE`（live .env 設 **shadow**）+ `_PROB` +
  `_CLASSES`；config.py raw 記錄 + .env.example。
* 測試：新 `tests/test_route_v2.py` ×**17**（jev_choice 客戶端 ×2、
  resolver ×3、off/shadow/greeting 直答/語系模板/time 直答重用/
  status 格式化/weather pre-fetch/低信心/未啟用類/chat 類/Jev 失敗/
  工具失敗回落/接線順序衛兵）；**測試抓到真 bug**：choice 回傳未啟用
  的類別也會派發 → 已補 `choice not in enabled` 檢查。
* 合併迴歸 route_v2+time_route+teams+rerank **194 passed**；
  pyproject 0.9.84.1 → **0.9.84.2**。

### v0.9.84.1 — Jev 時間路由（規劃書 §5.1 窄切片：shadow → 直答，②）

**「今天是幾月幾日／現在幾點」類訊息：Jev noul 判斷（p ≥ 0.85）→
真呼叫 `get_current_time` + 模板直答，跳過整個 LLM 工具迴圈**
（<1 秒、省 ~12k tokens；直答不進記憶召回 = 沒有舊快照抄錯日期的
風險 — 正是 17:50 錯日期事件的解）。

* **`src/agent/agent.py`** — `_time_route_mode()`（off 預設 | shadow
  | on）+ `_time_route_prob()`（0.85，clamp 0-1）+
  **`_jev_time_route(mgr, question)`**：shadow 只記 log
  （`[TimeRoute] jev_p=…`）不動行為；on + p 達標 → 呼叫真工具、
  格式化「今天是 2026-09-21（星期一）；本地時間 18:30:00（UTC+8:00）」；
  任何 Jev/工具失敗 → **fail-closed 回落 LLM 主路徑**。
* 插入點：`_chat_turn_impl` 在 `mgr = self.mcp_manager` 之後、
  memory recall 之前 — 直答路徑不經過記憶注入（乾淨上下文）。
* Env：`QDRANT_TIME_ROUTE`（live .env 設 **shadow** — 建議跑一週
  看 `[TimeRoute]` 誤判率再切 on）+ `QDRANT_TIME_ROUTE_PROB`；
  config.py raw 記錄 + .env.example。
* 測試：新 `tests/test_time_route.py` ×**12**（mode/prob resolver ×8、
  off 不觸發/shadow 只 log/on 直答含日期星期時間/低分回落/Jev 失敗
  回落/工具錯誤 fail-closed/agent 整合 source-guard）。
* `pyproject.toml` 0.9.84.0 → **0.9.84.1**。

### v0.9.84.0 — Jev watch gate（規劃書 §5.3：watch 觸發過濾，①）

**每次 Teams watch fire 先走「確定性 UIA 讀取 + 一次 Jev noul 決策」，
誤報直接跳過整個 LLM 回合**（~12k tokens + 3-5 秒/次；log 實證：
38 次連續相同觸發 = 38 個被浪費的 LLM 回合）。

* **`src/agent/rerank.py`** — 抽出模組級 `_decisions_post()`（rerank
  客戶端與決策 helper 共用的 HTTP 層）+ 新增 **`jev_noul(instructions,
  state)`** 通用決策入口（獨立於 `QDRANT_RERANK` — 各功能用自己的
  env 開關；回 `(p|None, meta)`，任何失敗 p=None 由呼叫端決定
  fail-open/closed）。rerank 29 測試零回歸。
* **`src/teams_assist_tools.py`** — `_watch_gate_skip()`：gate 開啟
  且 mode=live 時，UIA 讀取聊天內文（非 LLM）→ Jev「目前是否沒有
  需要使用者本人回覆的新訊息？」→ p ≥ `QDRANT_WATCH_GATE_SKIP_PROB`
  （0.7，clamp 0-1）→ fire 直接結束（audit `watch-gate-skip` +
  `state.gate` 計數）。**任何失敗 fail-open 到 LLM 回合**（漏報比
  浪費貴，規劃書 §7）；mock 模式不啟用。
* `teams_assist_status` 暴露 `gate`（skipped 累計/last_p/last_at）。
* Env：`QDRANT_WATCH_GATE`（off 預設；live .env 已設 on）+
  `QDRANT_WATCH_GATE_SKIP_PROB`；config.py raw 記錄 + .env.example。
* 測試：`test_teams_assist.py` 127 → **135**（gate off/跳過審計/
  放行/UIA 失敗回落/Jev 失敗回落/mock 不啟用/門檻 resolver/
  status 暴露 ×8）；`pyproject.toml` 0.9.83.8 → **0.9.84.0**。

### v0.9.83.8 — 🧰 選擇器改單回合語意（根因治理收尾）+ A/B 回滾 glm

**選擇器語意修正**（使用者拍板執行）：

* **單回合覆寫**：🧰 停用集只作用於**下一次送出**，請求完成後立即
  復原全開（`chatDisabledTools = null` + badge + 面板重繪）— 永遠
  不可能再變成靜默全域限制。長期工具配置歸 `.env` 的
  `MCP_ACTIVE_SERVERS`，瀏覽器不再擔任配置層。
* **開機遷移**：`loadDisabledTools()` 改為一次性
  `localStorage.removeItem('chat-disabled-tools')` — 任何還存著舊
  disabled-set 的瀏覽器/分頁載入頁面即自動清乾淨。
* `saveDisabledTools()` 變 no-op（僅保留函式名供面板呼叫點）。
* 測試 `test_gui_chat_tools.py` 19 → **22**（開機清殘留、送出後
  重置、永不再持久化的反向衛兵 ×3）；`node --check app.js` 綠；
  gui_chat_tools + gui_api + web_server **104 passed 1 skipped**。
* **A/B 回滾**（user：模型改回 glm5.3-flash provider together）：
  `.env` `OPENROUTER_CHAT_MODEL=z-ai/glm-5.3-flash` +
  `PROVIDER_ORDER=together` + `ALLOW_FALLBACKS=false` +
  `REASONING_EFFORT=medium`（原 low；A/B 計畫約定的回滾配套）；
  gpt-4o-mini 保留為註解便於再切換。
* `pyproject.toml` 0.9.83.7 → **0.9.83.8**。

### v0.9.83.7 — 真根因：🧰 選擇器舊 disabled-set 隱藏核心工具（對照實驗鐵證）

使用者質疑「應該不是模型問題」— **完全正確**。對照實驗（同一個
正在跑的 app、同一個 agent、gpt-4o-mini、127 工具全在）：

* `active_tools=null` 直驅「今天是幾月幾日」→
  **`calls: 2` 真派發、回答「2026 年 9 月 21 日（星期一）」正確、
  prompt 37,706 tok（完整表面）**。
* GUI 同問題 → 12.8k tok、零派發、從 9/20 的記憶抄出**錯的日期**。

**根因**：瀏覽器 🧰 chat 工具選擇器的 `chat-disabled-tools`
localStorage 舊 disabled-set → `effectiveChatTools()` 在**每次**
GUI 請求都送出一個不含 `get_current_time` / `web_search` /
`search_documents` 的 allow-list → 模型無工具可呼叫（v0.9.13.2
宣告面 vs 請求面不一致陷阱，選擇器版）：glm 寫文字假呼叫、
gpt-4o-mini 乖巧地用記憶回答或說「我將查詢」。也解釋了整條
echo 時間線。

* 修復（觀測 + 警示）：`Api.chat_turn` 對非 null 的
  `active_tools` 記 INFO（覆寫生效），且當 allow-list 隱藏核心
  工具時記 WARNING（提示用 🧰 All-on 重開）。
* **使用者立即解法（免重啟）**：聊天工具列 🧰 → 「全部開啟」→
  badge 顯示全數 → localStorage 清除 → 下回合起工具回來。
* 測試 gui_chat_tools + gui_api **58 passed 1 skipped**；
  pyproject 0.9.83.6 → **0.9.83.7**。

### v0.9.83.6 — A/B phase 1：聊天模型切 gpt-4o-mini（生成端根因解）

使用者拍板：「兩個都試 — 先 gpt-4o-mini A/B，不行再回 glm+medium」。

* `.env`：`OPENROUTER_CHAT_MODEL=openai/gpt-4o-mini`（glm 保留為註解
  便於回滾）+ **解除 together 釘選**（`PROVIDER_ORDER`/
  `ALLOW_FALLBACKS` 註解化 — 釘選正是之前 gpt-4o-mini 三個端點
  全 404 的兇手）。
* 直測驗證（同介面 tools + tool_choice=auto）：
  `finish=tool_calls`、`get_current_time({})` 正確派發、content 空
  — 同一介面下 gpt-4o-mini 走 tool_calls 機制，不寫文字呼叫。
* A/B 判準（live）：「今天是幾月幾日」等問題 → log 應有
  `[agent] tool get_current_time(...)` 真派發、回答帶實際時間；
  若仍出現 echo → guard 會代跑（.83.3-.83.4 防線）→ 回滾路徑：
  `.env` 註解 gpt-4o-mini、還原 glm + `REASONING_EFFORT=medium`。
* config-only（無程式/測試變更）；`pyproject.toml` 0.9.83.5 →
  **0.9.83.6**。

### v0.9.83.5 — 根因治理：echo 答案不存記憶 + system prompt 硬規則（不再追形態）

使用者指示：「從根因改善，而不是補丁」。根因鏈診斷：

1. **生成端** — glm-5.3-flash@together 間歇性把 tool call 寫成文字
   （v0.9.82.0/82.1/82.3 三次實證 + 今日 5 種形態）；jev rerank 只在
   檢索層、壓縮只縮 prompt，兩者**不影響 LLM 的輸出形態**（時間線：
   15:47-15:58 的 echo 在壓縮啟用**之前**，是 136k 退化造成；壓縮
   恰好治了那個）。
2. **模仿迴圈（本次確認的真根因）** — echo 回答若被存成長期記憶／
   session 歷史，下一回合模型就**模仿自己以前 echo 的形狀**。

根因修復（本次）：

* **memory_store.ingest_session_pair** 新增 `_is_echo_artifact` 過濾
  — 引用塊剝除後 ≤200 字、且形狀是 `tool(...)` 黏括號／latin 工具名
  直接黏 CJK／整體只有工具名 → **拒存**（echo 無資訊價值，只會教
  下一回合模仿）；guard 修復過的回答（含「系統代為執行」）與正常
  敘述（「get_current_time 的結果如下：…」）照存。
* **system prompt 硬規則**（tool rules 區塊）：工具呼叫**只有**
  tool_calls 機制才算數 — 以文字寫出工具呼叫（含黏死形態）= 失職；
  legacy prompt 不含。
* 掃描 live `memory_docs`（452 點）— 今天 17:00 後的 echo **未入
  庫**（0 筆命中），無需再清。
* 測試：`test_memory_store.py` 29 → **36**（echo/裸名/多黏死拒存、
  guard 修復過與正常短回答照存、引用尾巴不計長度 ×7 + prompt 規則
  ×2）；與 teams 合跑 **161 passed**。
* `pyproject.toml` 0.9.83.4 → **0.9.83.5**。

### v0.9.83.4 — 黏死查詢剝除升級 + 多工具黏串／URL 特判（17:21 假呼叫）

v0.9.83.3 上線後的 live E2E 又兩個形態（17:20:46 重啟的進程是 .83.2，
17:21 回合 log 仍無 `[agent]` echo 行 — .83.3 尚未載入；離線驗證現有
代碼 CASE1 `get_current_timetimeframetoday` 已可真跑）：

* **17:21:30**「今天的天氣」→
  `get_current_timetimeframetodayweb_searchmax_results5query台北 今天
  天氣 預報 中央氣象署` — **兩個工具黏成一串**。
* 查詢剝除原本只吃 8 個拉丁字元（`max_resu`）→ 剩
  `lts5query台北 今天 天` 垃圾查詢。
* **修復**：
  - 查詢型工具黏死時剝除**整段開頭的拉丁/數位黏串**
    （`^[A-Za-z_0-9]+` — `max_results5query台北…` → `台北…`）；
  - `web_fetch` 特判：只剝 `url` 鍵（貪食剝除會把 `https` 吃掉）；
  - 尾巴擷取 `(.{0,24})` → `(.{0,160})`（URL/長查詢不再被切斷；
    查詢上限 80 字、url 上限 200 字）。
* 測試 `test_teams_assist.py` 125 → **127**（多工具黏串：time 查詢
  兩工具各正確取參；web_fetch 粘死 URL 不被吃掉）。
* `pyproject.toml` 0.9.83.3 → **0.9.83.4**。

### v0.9.83.3 — echo guard 升級：裸名／黏死形態 + 查詢來源（17:02/17:03 假呼叫）

v0.9.83.2 重啟後的 live E2E 又兩個 echo 變體（guard 的 `tool(...)`
括號 regex 都接不住）：

* **17:02**「中華電信董事長是誰」→ `search_documentsq中華電信 董事長`
  — 工具名和參數鍵**黏死**（無括號、無 JSON）。
* **17:03**「今天是幾月幾日」→ `get_current_time` — **光禿工具名**
  （↓7 tok，連括號都沒有）。

* **新增裸名／黏死 pass**（`_ensure_list_chats_summary` 第二段）：
  - 工具名黏著 1-8 個拉丁參數鍵字元 → 剝鍵名後的字尾當查詢
    （`search_documentsq中華電信 董事長` →
    `search_documents({"query": "中華電信 董事長"})`）；
  - 工具名**獨佔一行**（整個回答就是工具名）→ 零參數工具直接
    `tool({})` 跑；查詢型工具用 **stash 的本回合問題**當查詢
    （`Agent._last_turn_question`，ask/chat_turn 進場時與
    last_tool_dispatches 同步重置）；
  - 查詢型工具清單 `_ECHO_QUERY_TOOLS` = web_search / web_fetch /
    search_documents / search_memory / search-events（web_fetch 用
    url 鍵、查不到 URL 就跳過）；
  - **散文提及永不觸發**（名字後面是空白/CJK → 跳過）；
  - `teams_assist_list_chats` 讓路給專用 block（避免重複真呼叫）。
* `_ECHO_SAFE_TOOLS` + `search_documents` / `search_memory`（唯讀）。
* 測試 `test_teams_assist.py` 121 → **125**（黏死 search_documents
  剝鍵真跑、裸名 get_current_time 空參真跑、無字尾回落本回合問題、
  散文提及不觸發）；與 surface_mode + active_servers 合跑
  **152 passed**。
* 過程修正：編輯 agent.py 時誤插了猜測的 `_messages_with_user(...)`
  片段（ask 路徑），已讀回清除 — `py_compile` 綠、全部測試綠。
* `pyproject.toml` 0.9.83.2 → **0.9.83.3**。

### v0.9.83.2 — echo guard 補 web_search/web_fetch（「中華電信董事長是誰」假呼叫）

Live E2E 2026-09-21 16:52（壓縮已生效，↑12,886 tok — 歷史 0）：
「中華電信董事長是誰」— 模型把
`web_search({"query": "中華電信董事長 2026 現任"})` **當文字 echo**（零
派發、零 ECHO 警告），答案由 **9/15 的舊記憶**（web_search 存檔）撐著。

* 根因：`_ECHO_SAFE_TOOLS`（v0.9.82.0 的 26 個唯讀工具）**漏了
  `web_search` / `web_fetch`** — echo guard 的 regex 有對到，但名單
  沒有這兩個工具就跳過了。
* 修復：web_search / web_fetch 加入 echo-safe 名單 — 都是唯讀外部
  查詢（Ollama/OpenRouter search、best-effort GET），echo 且本回合
  未派發 → agent **真的跑**，並把結果文字（≤400 字）附進回答 —
  「現況事實」問題拿到新資料，舊記憶答案下馬。
* 已派發過的不重跑（dispatch 集檢查照舊）；寫入型工具仍然永不代跑。
* 測試 `test_teams_assist.py` 119 → **121**（web_search echo 真跑 +
  結果浮現 + 已派發不重跑；web_fetch echo 解析 url 並真取）。
* `pyproject.toml` 0.9.83.1 → **0.9.83.2**。

### v0.9.83.1 — rerank 低置信防幻覺閘（QDRANT_RERANK_MIN_PROB=0.3）

接續 v0.9.83.0 的實證：cosine 對「語意近但回答不了」的段落給 0.517
假信心、Jev 誠實給 ≤0.20；真命中段 0.87-0.96 — 中間是乾淨的空隙。

* **閘門**（`Retriever.search`）：`QDRANT_RERANK=on` 且 rerank 成功時，
  最佳段落的 noul 機率 < `QDRANT_RERANK_MIN_PROB`（預設 **0.3**，
  **0 = 停用**；clamp 0.0-1.0，壞值回落預設）→ 回傳**空結果**並記錄
  `Retriever.last_rerank_meta`（`gate: low-confidence-empty` +
  `best_score` + `min_prob`）— 語料無可回答內容時不再把近失段落餵給
  LLM（防幻覺），MCP `search_documents` 回「查無結果」、模型會自行改
  走 web_search 等其他工具。
* 只在 `meta.reranked=True` 時生效 — rerank 失敗回落 cosine 原序，
  閘門不參與（無 Jev 判斷就不下刀）。
* Live E2E（2026-09-21 真實語料）：
  「房屋折舊怎麼計算」→ **0 results**，meta `best=0.19 < 0.3`；
  語料原句查詢 → 5 results、top rerank **0.95**、gate None。
* Env：`QDRANT_RERANK_MIN_PROB`（config.py raw 記錄 + .env.example +
  live .env 註解行）。
* 測試 `tests/test_rerank.py` 23 → **29**（min_prob resolver ×1；
  閘門：低於門檻回空/meta 記錄、MIN_PROB=0 停用、top 過門檻保留、
  rerank 失敗不觸發、feature-off 不觸發 ×5）。
* **測試基建教訓（v0.9.81.1 lazy-load_dotenv 注入重演）**：
  `_clean_env` fixture 現在**先 `import src.config` 再 delenv** —
  之前 `test_rerank_off_unchanged` 在測試中途才第一次 import
  retriever→qdrant_client→config，load_dotenv 把 live .env 的
  `QDRANT_RERANK=on` 注入、OFF 測試反而拿到 ON（單跑測試檔才會中，
  合併跑因為別的測試檔已 import 過 config 而被遮住）。widen/rerank/
  slice 測試同步設 `MIN_PROB=0`（該測試驗證的是 v0.9.83.0 接線，
  假分數全 < 0.3 不應觸發閘）。
* `pyproject.toml` 0.9.83.0 → **0.9.83.1**。

### v0.9.83.0 — Jev rerank：cosine 召回池的「能否回答」精排層（Rank wide, read narrow）

使用者痛點（規劃書 Phase 1）：cosine 只看「語意相近」，不知道某段
「能不能回答查詢」— top-k 裡常混入不相關的段落。

**實證先行（live probe 2026-09-21，全部 200）**：

* T1 最小 noul：「是 bug 回報嗎？」→ 0.95，0.63 s。
* T2 rerank 形狀（3 段正負控制）：折舊公式段 **0.95** / 相關不回答
  **0.10** / 無關 **0.01** — 區分度完美。
* T3a 真實語料（負向）：語料內沒有答案時 Jev 最高只給 **0.17**，
  cosine 卻給 0.517 的假信心 — 誠實分辨「語意近」vs「能回答」。
* T3b 正向控制（語料原句）：**0.96 / 0.89 / 0.89…** 命中段排最前。
* **單一 call 裝 20 個 noul 問題 = 0.4-0.8 秒、$0.0002/查詢**。

**新模組 `src/agent/rerank.py`（`RerankClient`）**：

* 端點 `POST https://openrouter.ai/api/alpha/decisions`（沒有 `/v1`；
  `/api/v1/api/alpha/decisions` 等都 404）；重用既有
  `OPENROUTER_API_KEY`（provider=TypeSafe，回應 model 解析為
  `typesafe/jev-1.13-20260917`）。
* 請求：`state = {"query", "chunks": ["1. …", …]}` +
  `questions = {chunk_i: {"type": "noul", "instructions": "編號 i 的
  段落能直接回答查詢嗎？"}}`；回應 `answers.chunk_i.noul` 機率排序
  （stable — 同分維持 cosine 原序），每段附 `rerank_score`。
* **失敗即回落**：缺 key / HTTP 402（credits）/ 逾時 / 壞 JSON /
  不支援的 backend → 一律返回 cosine 原序（`reranked: false`），
  召回品質只升不降；單次嘗試不重試。

**插入點 `Retriever.search()`（單點覆蓋全鏈）**：CLI `ask`/`search`、
Agent RAG 上下文、MCP `search_documents`、GUI/web 搜尋頁、stdio
server 全部經過。`QDRANT_RERANK=on` 時召回池放寬到
`QDRANT_RERANK_CANDIDATES`（20）→ Jev 精排 → 只取 top_k；feature off
（預設）= 位元組相同旧行為。

* Env：`QDRANT_RERANK`（off 預設）/ `QDRANT_RERANK_BACKEND`
  （jev；**nimble 執行期拒用** — RDNA1 prefill 20 段實測 40-100 秒，
  本地只能做排程/離線）/ `QDRANT_RERANK_CANDIDATES=20`（clamp 2-50）/
  `QDRANT_RERANK_TIMEOUT_S=10`（clamp 2-60）/ `QDRANT_RERANK_CHUNK_CHARS=600`
  （clamp 100-4000）/ `JEV_MODEL=typesafe/jev-1.13`（官方 API 要
  `jev-1.13.0`；裸名 `jev-1.13` 會 400 Unknown model）。
* config.py raw-string 記錄 + `.env.example` 區塊；live `.env` 設
  `QDRANT_RERANK=on`。
* 新測試 `tests/test_rerank.py` ×23（resolvers/request 形狀/解析
  排序/機率 clamp/HTTP 402、timeout、缺 key、feature-off fallback/
  Retriever widen-rerank-slice 接線/nimble 不放寬/env.example 衛兵）；
  與 memory_store + collection_browser 合跑 **75 passed**。
* `pyproject.toml` 0.9.82.5 → **0.9.83.0**。

### v0.9.82.5 — 啟用 chat 歷史上下文壓縮（修 web/tgbot 長 session 136k prompt tokens）

log 實證：web chat session 歷史累積至 **444 則**，每回合把**全量歷史**
塞進 prompt（`↑136,236 tok` = 工具面 ~12k + 歷史 ~124k）— 模型在超大
context 下劣化（echo 半殘的 `run_shellcommanddir …`、忘記用 stat_file
檢查檔案、直接印命令文字）。

* **啟用既有機制**（v0.9.10.0 `ContextCompressor`，opt-in）—
  `.env` 設 `CHAT_CONTEXT_COMPRESSION=on`：歷史超過
  `CHAT_COMPRESS_TRIGGER_MESSAGES`（24）則時，較舊的尾部摺疊成**一段
  LLM 摘要**（快取 per old-prefix sha1），最近
  `CHAT_COMPRESS_KEEP_MESSAGES`（12）則保留原文；超長單則訊息另做
  選擇性壓縮（code block 保留）。
* **效果**：web/tgbot 長 session 的 prompt tokens 從 136k 回到
  **約 12-16k**（工具面 ~12k + 壓縮後歷史）；session 檔仍保留完整
  對話（壓縮只作用於餵給 LLM 的上下文）。
* **config-only 變更，無程式碼修改**；重啟生效。

`pyproject.toml` 0.9.82.4 → **0.9.82.5**。

### v0.9.82.4 — task panel 輪詢日誌降噪（`list_background_tasks` / `get_readiness` → DEBUG）

使用者問：「為何有大量的 gui.web: POST /api/list_background_tasks」。
**行為是設計如此，不是 bug**：chat 工具列的 ⏳ 任務面板開啟時每
**2 秒**自動刷新一次（v0.9.47.0 設計，面板不關就一直輪詢）；splash
的 readiness 輪詢（v0.9.80.1 每 1.5 秒）同理。問題是每一筆都以 INFO
記進 app.log 刷屏。

* **`gui/web_server.py::HEARTBEAT_METHODS`** — 由 `{get_status}`
  擴充為 `{get_status, list_background_tasks, get_readiness}`：
  心跳方法的請求日誌改 **DEBUG**，其他方法（chat_turn / ingest /
  list_sessions / …）維持 INFO。輪詢行為與頻率**不變**（面板/啟動
  檢查需要它）。v0.9.15.3 `get_status` 降噪的同款教訓第三例。
* **測試** — `test_web_server` + `test_gui_task_panel` +
  `test_mobile_web` **79 passed** 零失敗。

`pyproject.toml` 0.9.82.3 → **0.9.82.4**。

### v0.9.82.3 — watch fire 誤用 windows-mcp 截圖鏈修復（fallback 模型不守 UIA 規則）+ fallback 鏈重排

使用者回報：app.log 出現被執行的 `Screenshot`。log 還原：兩次都是
**teams-assist watch fire 的背景任務**；`qwen/qwen3.8-flash` 持續 404
（OpenRouter 單端點仍未恢復）→ failover 到 **ollama
`glm-5.3-flash:cloud`** → glm 不守 fire prompt 的 UIA 規則，反而
`list_mcp_tools` → `Snapshot`（截圖）→ `App(restore)`（把行事曆視窗
帶回前景）→ `Click`（點擊桌面）— 走了舊截圖流程並干擾桌面。

* **fire prompt UIA 分支加硬禁令**（`_compose_fire_prompt`）：嚴禁
  windows-mcp 的 Snapshot/Screenshot/App/Click/Type 讀取或操作
  Teams（截圖/還原視窗/點擊 = 干擾）；本 fire 只准 UIA 通道
  （讀取 = read_uia；送出僅 AUTO=on 經 UIA）。
* **`.env` fallback 鏈重排** — `LLM_PROVIDER_CHAIN=openrouter,
  openai-compat,ollama`：qwen 404 時改 fallback 到**自架
  qwen3.6-35b**（openai-compat 端點）而非 glm-5.3-flash — glm 降為
  最後位（其不守規則的行為已兩度實證）。
* **測試** — `test_teams_assist` **119 passed**（0 失敗；禁令措辭
  避開 `teams_assist_send_uia` 字面以符合 AUTO=off 斷言）。

`pyproject.toml` 0.9.82.2 → **0.9.82.3**。

### v0.9.82.2 — MCP_SURFACE_MAX_TOOLS 工具數硬上限（修 gpt-4o-mini 400 array_above_max_length）+ 模型切換

使用者換模型後 log 實測：qwen3.8-flash 間歇性 404（**OpenRouter 該
model 只有單一 Alibaba 端點，被健康度 Filter 刷掉就整段時間全
404**；本機以 app 同形狀請求 — 228 lean tools + system + reasoning-
low — 實測通過，index 機制與模型本身都無問題）。切到
`openai/gpt-4o-mini` 後暴露**供應商硬限制：`tools` 陣列上限 128**，
228 個直接 `400 array_above_max_length`。

* **新 env `MCP_SURFACE_MAX_TOOLS`**（`manager.py`）— 工具數硬上限
  （`_surface_tool_limit()`，預設 0 = 不限；非法值忽略）。超過時
  `_apply_tool_cap()` **依優先序裁切**：servers 在
  `MCP_ACTIVE_SERVERS` 列出順序**較前者優先保留**（使用者用清單順序
  控制優先先權），邊界 server 允許部分裁切；被裁掉的 servers 併入
  `last_hidden_servers`（meta 目錄照樣標示 CANNOT be called）。
* **live `.env`** — `MCP_SURFACE_MAX_TOOLS=127` +
  `MCP_ACTIVE_SERVERS` 重排優先序（qdrant/web/filesystem/datetime/
  meta/**google-calendar**/telegram/**teams-assist**/windows-mcp…
  提前）。
* **`OPENROUTER_CHAT_MODEL` → `openai/gpt-4o-mini`**（多供應商 —
  一家掛掉自動路由別家，404 機率大降）。
* **實測**：`openai/gpt-4o-mini` + 127 lean tools → `finish=
  tool_calls`，正確派發 `get_current_time`（trim 警告正確列出
  隱藏 servers）。
* **測試** — `test_mcp_surface_mode` +3（cap 裁切+hidden 記錄、
  0=不限、非法值忽略）；active_servers 3 個 gate 測試補
  `MCP_SURFACE_MAX_TOOLS=0` env 隔離；合計 **62 passed**。

`pyproject.toml` 0.9.82.1 → **0.9.82.2**。

### v0.9.82.1 — Google Calendar FIRST 硬規則 + echo 保底加行事曆唯讀工具

使用者回報：「我下星期有那幾行事曆工作」→ **零 dispatch**（log 無
`[agent] tool` 行，answer_len=112）— 模型憑記憶編造行事曆答案。lean
表面本身沒問題（本機實證：`search-events`/`list-events` 的名稱、
60 字描述、timeMin/timeMax 參數鍵一清二楚）。

* **system prompt「Google Calendar FIRST」硬規則**（v0.9.53
  search-first 同款）：行事曆/行程/會議問題 → 必須真的呼叫
  `get_current_time`（算區間）+ `search-events`/`list-events`（
  timeMin/timeMax）或 `get-freebusy`；calendar id 未知用
  `list-calendars`；**沒有真派發就回答行程內容 = 捏造，禁止**。
* **echo 保底擴及 google-calendar 唯讀工具** — list-calendars /
  list-events / search-events / get-event / get-freebusy / list-colors
  加入 `_ECHO_SAFE_TOOLS`（echo regex 修正為 `[\w\-]+` — 連字號工具
  名原本匹配不到）；create/update/delete-event、respond-to-event、
  manage-accounts 等**寫入類永不在清單**。
* **測試** — +2（search-events echo → 代跑 + 結果浮現 + 寫入排除；
  prompt 區塊 + legacy 排除）**119 passed**。

`pyproject.toml` 0.9.82.0 → **0.9.82.1**。

### v0.9.82.0 — echo 保底泛化為全 fleet 唯讀安全清單（修 get_current_time fake-call + 參數幻覺）

使用者回報（index 模式上線後首個 fake-call）：「現在幾點」→ 模型把
`get_current_time({"format": …, "timezone": …})` 當文字 echo（↓26 tok
零 dispatch），且**幻覺出不存在的參數**（get_current_time 是零參數
工具）。

* **`_ECHO_SAFE_TOOLS` 泛化**（teams 5 個 → 全 fleet **26 個唯讀
  安全工具**）：get_current_time / list_mcp_tools / chat-history 三件
  套 / list_recent_memories / qdrant 讀取 / scheduler-webhook-task
  讀取 / list_allowed_chats / get_runtime_status / log 讀取 /
  local_interfaces / list_credentials 等。**寫入類（reply / send_uia /
  send_telegram_* / 下單 / 檔案寫入）永不在清單**。
* **echo regex 泛化** — `(\w+)\(([^)]*)\)`：安全清單內的任何工具被
  當文字呼叫且本回合未派發 → 代跑；結果文字（≤400 字）**附加進回答**
  （使用者問「現在幾點」就真的看得到時間）。
* **`_parse_echo_args` 升級** — 先試 JSON（glm-flash 的 echo 是
  `{"format": …}` 形狀），再退 key=value 解析；幻覺參數容錯透傳。
* **測試** — `test_teams_assist` + `test_mcp_surface_mode` **126
  passed**（+1：get_current_time echo → 代跑 + 結果浮現 + 幻覺參數
  透傳 + 非安全工具不代跑）。

`pyproject.toml` 0.9.81.1 → **0.9.82.0**。

### v0.9.81.1 — lean 模式加碼：描述截短 60 字（index 實測 -64%）+ live .env 啟用 index

使用者回報「token 數量沒有變少」— 實測定位兩個原因：
(1) `.env` 沒設 `MCP_SURFACE_MODE`（resolver 仍 full）；
(2) 只剝 schema 細節僅 -25%（210,585 → 157,573 字元）— **工具描述
本身才是大頭**。

* **lean 模式描述截短**（`ToolSpec.to_openai`）— index 模式下描述取
  首行、≤60 字（完整描述仍可按需取得：錯誤 hint + meta 目錄）。
  實測 **210,585 → 75,216 字元（-64%）** — 使用者目前的 25-server
  表面 ≈ 62k → **約 22k tokens**。
* **live `.env`** — 啟用 `MCP_SURFACE_MODE=index`。
* **測試** — +1（lean 描述截 60/去換行/full 模式保留全文）**9
  passed**；`clean_env` fixture 改 setenv("full")（delenv 會被
  `load_dotenv` 以 .env 的 index 回填 — 環境污染防禦）。

`pyproject.toml` 0.9.81.0 → **0.9.81.1**。

### v0.9.81.0 — MCP_SURFACE_MODE=「精簡核心 + 按需展開」（Two-stage Prompting / lean schema + error 擴充層）

使用者核准的 Phase 1 計畫：降低工具面 context 佔用，同時避免 glm-
5.3-flash 的 fake-call 惡化（維持標準 tool-calling，不加間接層）。

* **`ToolSpec.lean`** — `to_openai()` 在 lean 模式輸出「精簡核心」
  schema：參數**鍵 + 型別**（`{"key": {"type": ...}}`）+ required；
  enum / property descriptions / 範例全數剝除。協議合法 — 模型仍可
  直接呼叫（不走 dispatcher）。
* **`MCP_SURFACE_MODE`** = `full`（預設，byte-identical）| `index`
  （`active_specs()` 依 env 套 lean；`_LEAN_EXEMPT_SERVERS` =
  `qdrant-local` / `meta-local` 恆為完整 schema）；`active_specs(
  lean=)` 可顯式覆寫；GUI picker（`active_tools`）與 🧰 名單不受影響。
* **按需展開（擴充層）** — `manager.call` 對**失敗**的呼叫（例外
  `is_error` 與 LocalTool 的 `{"error": ...}` 驗證 envelope 兩種形狀
  都涵蓋）在錯誤文字後附加完整參數定義 `（參數定義：key:type（enum）
  —desc；…）`，模型可即時自我修正重試 — 不需第二輪發現。
* **測試** — 新 `tests/test_mcp_surface_mode.py` **8 tests**（resolver
  三態、lean schema 剝除矩陣、full byte-identical、index lean+豁免
  全矩陣 + e2e dispatch、error hint 附著/成功不附/不重複、agent 面
  整合）；相關回歸 mcp_client/meta/active_servers/teams/gui_chat_
  tools/gui_api **224 passed 1 skipped**。

`pyproject.toml` 0.9.80.1 → **0.9.81.0**。
### v0.9.80.1 — readiness mcp 檢查改為非阻塞（修「qdrant/LLM 顯示逾時」）

使用者回報：v0.9.80.0 的同步建置把**整個** get_readiness 回應擋住
15-25 秒 → 快速的 qdrant/LLM 兩列等到回應落地時早已超過 5 秒 per-row
timeout，splash 顯示「逾時」。

* **`get_readiness` mcp 檢查重寫為非阻塞**：
  1. `self._kick_prewarm()`（冪等）— 背景 prewarm 執行緒建 agent +
     mcp_manager（v0.9.11.1 機制，DCL 共用）；
  2. 新 class flag `Api._prewarm_done`（`_prewarm_agent` 的
     `finally` 設定；`_kick_prewarm` 啟動時重置 False）；
  3. mcp slot 三態：**pending**（`done:false`、detail「初始化中…」，
     排除在 `ready` 外）／**done**（tools 數 + 失敗 server 名單）／
     **skipped**（agent 建不出 → 排除在 `ready` 外）。
* **前端**（app.js）— mcp 列改為**輪詢**（每 1.5 s 讀 get_readiness
  直到 `done:true`，上限 SPLASH_MCP_TIMEOUT_MS 90 s）；第一次回應
  快速落地 → backend/qdrant/llm 照舊在 5 秒內轉 ✅；safeCall race
  window 回復 6 秒短窗；`Promise.all(checkPromises)` 仍等 mcp 列
  settle 才淡出（都跑完才進主畫面的契約不變）。
* **測試** — `test_gui_readiness` 15 → **16 tests**（+1：prewarm
  進行中 → mcp pending done=False、排除在 ready 外；autouse fixture
  同步 stub `_kick_prewarm`）。

`pyproject.toml` 0.9.80.0 → **0.9.80.1**。

### v0.9.80.0 — 啟動檢查加入「MCP 工具初始化」（第 4 列；全部完成才進主畫面）

使用者需求：chat UI 把 MCP 初始化也納入啟動檢查項目，**都跑完才切入
主畫面**。原本 splash 只檢查 backend/qdrant/llm，MCP fleet（12+ 個
in-process hosts + stdio/HTTP 連線 ≈ 15-25 s）在背景 prewarm，使用者
進主畫面後工具面可能還沒就緒。

* **`gui/api.py::get_readiness` 新增 `mcp` slot** — **同步**建
  McpManager（`agent.mcp_manager`，double-checked-locked，與背景
  prewarm 執行緒安全共用）+ `active_tools()` 計數 + `mgr.failed`
  列出連線失敗的 server（有失敗 → ok=False，detail 帶失敗名單）。
  agent 本身建不出來（如無 LLM keys — llm 列已標示）→ slot 標
  `skipped` 並**排除在 `ready` 之外**（原本三列契約不變）。
* **前端**（`gui/assets/app.js` + `index.html`）— splash 新增第 4 列
  「MCP 工具初始化」；MCP 列專用 timeout `SPLASH_MCP_TIMEOUT_MS`
  （90 s — 覆蓋真實 fleet 建置時間；Skip 仍可隨時略過）；整體
  `safeCall` race window 同步放大；進度 `N/4` 動態計數；
  向後相容：舊 payload 無 `mcp` slot → 該列標 `ok/n/a` 不卡畫面。
* **i18n ×4**（readinessMcp：MCP 工具初始化/工具初始化/MCP tool
  init/MCP ツール初期化）。
* **測試** — `test_gui_readiness` 13 → **15 tests**（+2：failed
  server → not ready、agent 建不出 → skipped 排除；all_pass 改用
  stub agent + autouse `_stub_agent_build` fixture — 測試不再起真實
  stdio fleet，13 → 1.6 s）；`test_gui_splash_e2e` 5 passed（harness
  DOM/fixtures 加 mcp 列 + NODE_PATH 修正 — jsdom 本來就裝在
  `.cache/jsdom` 但 require 找不到）。

`pyproject.toml` 0.9.79.3 → **0.9.80.0**。

### v0.9.79.3 — GUI 🧰 picker 改用 gated 工具面（與 LLM 一致；修「移除 teams-assist 後 picker 仍顯示全部」）

使用者回報：從 `.env` 的 `MCP_ACTIVE_SERVERS` 移除 teams-assist 並重啟
後，chat UI 的 🧰 工具選擇表**仍列出所有 MCP 工具**。後端驗證（本機
實測）：gate 本身正確 — `active_specs()` = 228 個工具、
`teams_assist_*` 為 0、`list_mcp_tools`（MCP 工具）也同樣為 0 — 但
🧰 picker 走的是 **GUI bridge** `Api.list_mcp_tools`。

* **根因**：`gui/api.py::list_mcp_tools` 用的是 `mgr.list_tools()` —
  **未套 gate 的完整清單**，與 LLM 真正的表面（`active_tools()`，
  v0.9.13.2 契約）不一致 → picker 顯示的工具「模型其實不能呼叫」，
  正是 v0.9.13.2 discovery-trap 的前端版。
* **修法**：`Api.list_mcp_tools` 改用 `mgr.active_tools()` — 與
  LLM 的工具迴圈同一個 gate 結果（含 zero-match fallback）。picker
  從此只列模型真的能呼叫的工具。
* **測試** — `test_gui_api` + `test_gui_chat_tools` **58 passed
  1 skipped**、mcp_client/meta 35 passed（既有契約不變；picker 資料
  源改為 gated 面）。

`pyproject.toml` 0.9.79.2 → **0.9.79.3**。

### v0.9.79.2 — teams 工具描述瘦身（fake-call 擴散的 A/B 觀察緩解）

使用者 A/B 實證：**關閉 teams-assist 後 fake-call 消失；開啟時連查
kline 都出現 fake-call**。12 個 `teams_assist_*` 工具（多個長 CJK
描述 + config 的 11 屬性 schema）對 glm-5.3-flash 造成工具混淆，
fake-call 從 teams 擴散到其他工具。

* **描述瘦身**（`src/mcp_client/host.py`）— config 352→145、
  reply 170→114、send_uia 201→122、list_chats 171→42、drafts
  158→30、config schema 屬性描述縮短；**總描述 1,529 → 930 字**。
  行為指引留在 system prompt（工具描述只留一句話語意）。工具名稱/
  schema 結構/handler 不變（介面契約不動，stdio 鏡射自動同步）。
* **操作建議（文件化）**：不主動用 Teams 助理時，可從
  `MCP_ACTIVE_SERVERS` 暫時移除 `teams-assist`（或用 chat 的 🧰
  工具挑選器逐回合關閉）；或換用 fake-call 紀錄較少的模型。
* **測試** — 全套 116 passed（描述瘦身不改變工具面契約）。

`pyproject.toml` 0.9.79.1 → **0.9.79.2**。

### v0.9.79.1 — echo 保底擴及 config/start_watch/drafts（含 echo 參數解析）；reply/send_uia 絕不代跑

log 實證（15:19–15:24）：模型對 config 也是 fake-call — 15:19
`answer_len=49` 零 dispatch（設定沒生效 → 15:21 過濾空白）；15:23 的
「已把過濾條件設為 *」更是**無派發的捏造**（23 秒生成、零 tool 行，
從 15:21 歷史表格編出來的）。

* **`_ECHO_SAFE_TOOLS`** = config / start_watch / list_chats /
  drafts / status — 回答 echo 到這些呼叫且本回合未派發時，agent 解析
  echo 內的 key=value 參數（`_parse_echo_args`，ast.literal_eval
  容錯）後**代為執行**，並在回答附註「（系統代為執行 … → OK）」。
  **`reply` / `send_uia` 永不在白名單** — 寫入 Teams 的動作絕不自動
  代跑。已派發的工具不重複執行。
* **測試** — 115 → **117 tests**（+2：echo config 參數解析與代跑 +
  附註、write 工具排除 + 已派發不重跑）。

`pyproject.toml` 0.9.79.0 → **0.9.79.1**。

### v0.9.79.0 — list_chats「喊工具名不派發」修復（fake-call 偵測 → agent 確定性代跑）

live 證據（15:00–15:02）：三個回合的 bubble 內文**就是字串
`teams_assist_list_chats`** — 模型（z-ai/glm-5.3-flash）把工具名當
文字回、完全沒有派發（log 零 `[agent] tool` 行；v0.9.13.2 fake-call
同款），因此 .76.3 的 stash→append 保底從未觸發。且「表格已呈現」
的單一 marker `watch：` 太寬鬆 — 模型一行 `watch：執行中。` 就會誤判。

* **`_ensure_list_chats_summary` 升級雙路徑**：
  1. 派發過 → append stash 的 summary（.76.3 不變）；
  2. **回答含 `teams_assist_list_chats` 字串但沒有派發** → agent 自己
     `mcp_manager.call("teams_assist_list_chats", {})` 確定性代跑、
     取 summary 附上（log：`list_chats was ECHOED not dispatched`）。
* **表格判定改多鍵** — `("✅ 符合", "❌ 不符合", "👁", "聊天清單（")`
  任一出現才算已呈現；單行 `watch：…` 不再誤判。
* **測試** — `tests/test_teams_assist.py` 114 → **115 tests**（+1：
  echo → 確定性代跑 + summary 附上、錯誤結果不動回答；post-process
  測試補「單行 watch： 不算表格」案例）。

`pyproject.toml` 0.9.78.0 → **0.9.79.0**。

### v0.9.78.0 — `teams_assist_drafts` 草稿佇列工具（工具面 11 → 12）+ fire answer_chars 修正

使用者回饋：「沒有提供回應的草稿」。log 實證：14:42 fire 已正確讀到
黃宜軒並 `teams_assist_reply` 記了草稿（draft, chat=黃宜軒）— 草稿在
背景任務/session 裡，使用者**看不到**；且 task registry 的
`answer_chars=None`（fire 忘了帶）。

* **修 `make_teams_assist_fire`** — `end_task(..., answer_chars=
  len(answer_text))`：⏳ 任務面板從此顯示 fire 的回答長度。
* **新工具 `teams_assist_drafts`**（host + stdio 鏡射）：從 audit 環
  列出 draft/sent/sent-uia/send-uia-failed（最多 20 筆，新→舊），
  帶時間/聊天/狀態/草稿全文（audit 已存 text[:500]）+ 確定性
  `summary` 表格（同 list_chats 的呈現教訓）。核准送出某筆 →
  `teams_assist_send_uia(text, chat, confirm=true)`。
* **prompt** — system prompt 增補「看 teams 草稿」「有什麼草稿」觸發
  （明示草稿在背景任務裡、使用者不問就看不到）；fire prompt AUTO=off
  步驟 5 收緊：回答**必須附草稿全文**（不足 20 字視為失職）。
* **測試** — `tests/test_teams_assist.py` 110 → **114 tests**（+4：
  drafts 列舉/排序/全文、空佇列、end_task answer_chars、prompt 區塊；
  工具面 11 → 12 + stdio parity 同步）。

`pyproject.toml` 0.9.77.0 → **0.9.78.0**。

### v0.9.77.0 — 背景聊天限制釐清：fire 不得污染無關聊天的 session + prompt 明教「背景聊天未開啟」

使用者 live 回饋：「針對 黃宜軒 的 watch 沒有後續」。audit 實證：watch
有 fire（14:31/14:35，UIA 文字 hash 偵測到左側清單未讀標記變化），
但 `read_uia` 只能讀到**作用中**聊天（智凱與中華小夥伴 <3）— UIA 樹
只含當前視窗渲染的聊天內文（Chromium 只畫可見窗格），背景聊天只剩
左側清單未讀標記 → LLM 回「白名單外」，且兩筆問答被**誤掛進無關聊天
的 session**（teamsassist-智凱與中華小夥伴 3）。

* **`_fire_session_for()`** — 現在只有作用中聊天**符合過濾條件**時才
  落 per-chat session；不符（背景聊天訊息、作用中是別的聊天）→ 落回
  主 `teamsassist-watch` — 無關聊天的 session 不再被污染、不會虛建。
* **fire prompt（UIA 模式步驟 1 增補）** — 明確教導：UIA 只能讀到
  『目前開啟』的聊天內文；背景聊天的新訊息無法在不搶焦點的情況下
  讀內文 → 如實回「(背景聊天未開啟 — 請將該聊天開在 Teams 目前
  視窗)」，**不得杜撰**背景聊天內容。
* **本質限制（文件化）**：零干擾（UIA）通道 = 只能代答「目前開在
  Teams 視窗」的聊天；要 watch 黃宜軒 → 把黃宜軒聊天開為目前視窗
  （或暫時讓 Teams 停在那個聊天）。要讀背景聊天 = 必須搶焦點點選
  （windows-mcp 模式才做得到）。
* **測試** — `tests/test_teams_assist.py` 109 → **110 tests**（+1：
  作用中聊天不符過濾 → 落回主 session 且無關聊天 session 不虛建；
  既有 per-chat/session 分檔測試補 allowlist 匹配前置）。

`pyproject.toml` 0.9.76.3 → **0.9.77.0**。

### v0.9.76.3 — list_chats 表格確定性後處理（prompt 規則不夠 → agent 保底補上）

live 證據：14:23 app 重啟後（.1 已載入）模型（z-ai/glm-5.3-flash）仍把
清單壓成 `answer_len=46` — v0.9.59.2 教訓重演：**prompt 教的規則對某些
模型不保證**，需確定性後處理。

* **`Agent._run_tool_call`** — 派發 `teams_assist_list_chats` 成功時，
  從工具結果 stash `summary` 到 `self._last_list_chats_summary`
  （每回合入口隨 `last_tool_dispatches` 重置）。
* **`Agent._ensure_list_chats_summary()`** — `_tool_loop` 四個返回點
  （正常 / wrap-up / 空答案 fallback / max-iterations bail-out）統一
  套用：本回合有 list_chats 且回答不含 `watch：` 表格 → 直接把
  summary 表格附在回答後面（log 記錄壓縮事件）。
* **測試** — `tests/test_teams_assist.py` 107 → **109 tests**（+2：
  後處理矩陣（一句話→補、已含表格→不動、無工具→不動）、
  `_run_tool_call` stash（成功/錯誤結果））。

`pyproject.toml` 0.9.76.2 → **0.9.76.3**。

### v0.9.76.2 — speech.py docstring 殘留舊 TTS 預設 `alloy` 修正（docs-only）

`TTS_DEFAULT_VOICE` 實際是 `zf_xiaoxiao`，docstring 誤留舊值。

### v0.9.76.1 — list_chats 回答被壓縮成一句話 → 工具直接產出格式化 summary 表格

使用者 live 回饋（↓26/49 tok）：LLM 呼叫 `teams_assist_list_chats` 後
只回一句話，清單沒攤開。

* **`_list_chats` 新增 `summary` 欄** — 確定性格式化表格（watch 狀態
  / 過濾條件 / 作用中聊天 / 每標題 `✅ 符合|❌ 不符合` + `👁 已watch|
  — 未watch` 行 / 已 watch 的 session 清單或「尚無」），note 尾加
  「請把 summary 表格完整呈現（不要壓縮成一句話）」。
* **prompt** — list_chats 觸發行補「PRESENT the summary table
  VERBATIM — NEVER compress the list into a single sentence」。
* **測試** — +1（summary 表格矩陣）**107 passed**。

`pyproject.toml` 0.9.76.0 → **0.9.76.1**。

### v0.9.76.0 — `teams_assist_list_chats`：列出聊天標題 + watch 狀態（工具面 10 → 11）

使用者需求：「可以使用 chat 列出有那些聊天標題 那些已經被 watch 嗎」。

* **新工具 `teams_assist_list_chats`**（`TeamsAssistLocalHost` + stdio
  鏡射同步）一次回報：
  * `chats` — UIA TextPattern 直讀 Teams **左側聊天清單**的標題
    （`_uia_chat_titles()`：讀取文字截到訊息窗格邊界「調整 左 面板
    大小」、過濾 UI chrome/時間戳/超長訊息行、去重；best-effort
    啟發式），每個標題附 `allowed`（符合目前過濾條件）與 `engaged`
    （已有該聊天的助理 session = 被 watch 過/正在 watch）
  * `active_chat` / `active_chat_allowed` — 目前作用中的聊天（window
    標題解析）是否符合條件
  * `engaged_sessions` — 所有 `teamsassist-<標題>` session（標題、
    訊息數、最後活動時間，新→舊）
  * `watch_running` / `watch_mode` / `filters` / `allow_all` — watch
    執行狀態與目前過濾條件
* **live 實證**（本機 Teams）：46 個標題正確抽出（只濾掉 chrome
  `3/返回/向前`），active chat 解析正確。
* **prompt** — system prompt 增補「列出 teams 聊天」「有哪些聊天被
  watch」觸發詞 → `teams_assist_list_chats`。
* **測試** — `tests/test_teams_assist.py` 101 → **106 tests**（+5：
  標題解析（noise/訊息窗格排除）、mock/失敗優雅、allowed/engaged
  標記、mock 仍報 session、prompt 區塊；工具面 10 → 11 + stdio
  parity 同步）。

`pyproject.toml` 0.9.75.0 → **0.9.76.0**。

### v0.9.75.0 — Teams 助理白名單升級為過濾條件（glob 樣式）

使用者需求：「可以使用 filter 方式選 聊天標題 嗎」。原本每項只能是
完全比對的標題（或 `*` 全開）。

* **過濾語意**（`src/teams_assist_tools.py`）— 新 `_chat_allowed()`
  （`fnmatchcase`，區分大小寫）：每項 allowlist 條件 = 聊天標題的
  『完全比對』或『含 `*` 的 glob 樣式』— `加密分持*` = 以此開頭、
  `*會議*` = 標題含此字；`*` 單獨仍是全開（v0.9.73.0）；空清單/空
  標題 fail-closed。
* **prompt** — fire prompt 步驟 2 改寫為過濾語意（含樣式舉例 +
  當前條件列舉）；system prompt 白名單規則同步。
* **工具** — `teams_assist_config` 的 `allow_chats` 描述補過濾語意
  （runtime 設定即可套用，watch 下次輪詢生效）。
* **測試** — `tests/test_teams_assist.py` 100 → **101 tests**
  （+1：完全比對/前綴/包含/大小寫敏感/全開/fail-closed 全矩陣）。

`pyproject.toml` 0.9.74.0 → **0.9.75.0**。

### v0.9.74.0 — Teams 助理每聊天獨立 session + fire 歷史截斷

使用者需求：「回答時可以會參考之討論的內容嗎」→「執行，另外每個聊天
標題是否可以採用獨立 session 記錄不要混在一起」。實測本機 Teams UIA
window 標題帶出作用中的聊天名稱（`聊天 | <title> | …`）— fire 當下
即可確定聊天。

* **每聊天獨立 session**（`src/teams_assist_tools.py`）— 新
  `_active_chat_title()`（UIA read 的 `window` 欄解析；mock 模式/
  解析失敗/非聊天視窗 → None）+ `_chat_slug()`（sid-safe slug，CJK
  保留、路徑字元清洗、≤40 字）+ `_fire_session_for()`：
  `teamsassist-<聊天標題>` 獨立 session（title
  `[teams-assist] <標題>`），解析失敗退回主 `teamsassist-watch`。
  同一討論串的歷史不再與其他聊天混雜 — LLM 每次只看該聊天的上下文；
  GUI 對話歷史清單也依聊天分檔可瀏覽。fire 結果新增 `chat` 欄位。
* **fire 歷史截斷**（移植 v0.9.67.1 排程機制）— 新
  `QDRANT_TEAMS_ASSIST_FIRE_HISTORY`（預設 **-1** = 帶該聊天完整
  歷史 — session 已按聊天分檔，成長有界；0 = 各 fire 獨立；N = 只
  帶最後 N 則；非法值退回 -1）。只截斷**餵給 LLM** 的 history
  （`_cap_history`，糾正回合同步套用），session 檔永遠保留完整記錄。
* **測試** — `tests/test_teams_assist.py` 91 → **100 tests**（+9：
  slug、window 標題解析、mock 不碰 UIA、失敗優雅退回、limit
  resolver、per-chat session 建立、兩聊天分檔、退回主 session、
  截斷（N>0）與 0=獨立）。

`pyproject.toml` 0.9.73.0 → **0.9.74.0**。

### v0.9.73.0 — Teams 助理白名單萬用字元（`*` = 回覆所有討論區）

使用者需求：「如果要回覆所有討論區要如何設定」。原本白名單只能逐一
列聊天標題（空白 = fail-closed 拒絕啟動）。

* **`TEAMS_ASSIST_ALLOW_CHATS=*`**（萬用字元）= 助理可處理**任何**
  聊天（新 `_allow_all()`；fire prompt 白名單規則改寫為「可以處理
  任何聊天；頻道通知/機器人貼文一律不回」，不再有『白名單外』分支）；
  空白仍 fail-closed。
* **runtime 設定** — `teams_assist_config({"allow_chats": "所有"})`
  也通（`所有` / `all` / `全部` 正規化為 `*`）；`teams_assist_status`
  新增 `allow_all` 欄位；config 工具描述與 system prompt 補「回覆
  所有討論區 → allow_chats='*'」觸發詞。
* **測試** — `tests/test_teams_assist.py` 87 → **91 tests**（+4：
  resolver、fire prompt 萬用字元分支、watcher start 接受 `*`、config
  同義詞正規化 + status `allow_all`）。

`pyproject.toml` 0.9.72.0 → **0.9.73.0**。

### v0.9.72.0 — Teams 助理 runtime 組態工具（`teams_assist_config` — LLM 暫時調整設定）

使用者需求：支援透過 LLM **暫時**調整 Teams 助理組態（mode /
send_mode / watch_mode / AUTO / 白名單 / 人設 / 輪詢參數等），不用改
`.env`、不用重啟。

* **引擎**（`src/teams_assist_tools.py`）— `_set_config()`：
  * 11 個可設鍵（`_CONFIG_KEYS`）：`mode`(mock|live)、`send_mode`
    (windows-mcp|uia)、`watch_mode`(screen|uia)、`auto`(on|off，接受
    true/1/yes…)、`allow_chats`(逗號字串或 list；空 = fail-closed)、
    `my_name`/`my_role`/`reply_style`、`window_rect`('x,y,w,h' 或
    null 清除)、`poll_interval`、`min_fire_interval`。
  * **暫時性契約**：只寫 `os.environ`（所有 resolver 本來就逐次讀
    env）— watch 執行緒下次輪詢即採用新值；**不寫 .env**，重啟後
    回到 .env 的值。
  * **all-or-nothing**：任何鍵驗證失敗 → 一個都不套用，回 error +
    valid_keys；成功回 `applied`（old/new）+ `effective`（經 resolver
    clamp 後的生效值快照）+ 暫時性 note。
* **host** — `TeamsAssistLocalHost` 第 10 個工具 `teams_assist_config`
  （全 optional 屬性 schema + enum）；stdio 鏡射 dispatch 同步。
* **prompt** — system prompt Teams 區塊增補 Runtime config 說明
  （觸發詞：「設定 teams」「teams 改自動送出」「把 teams 監看改成
  uia」；明示 TEMPORARILY / 不寫 .env）。
* **測試** — `tests/test_teams_assist.py` 76 → **87 tests**（+11：
  多鍵生效+水位、unknown key 拒絕+all-or-nothing、invalid value
  all-or-nothing、allow_chats list/空、window_rect 設定/清除/非法、
  interval clamp、auto 布林詞、人設、host dispatch+status 反映、
  system prompt 區塊+legacy 排除；工具面 9 → 10、stdio parity 同步）。

`pyproject.toml` 0.9.71.0 → **0.9.72.0**。

### v0.9.71.0 — Teams 助理 watch 偵測升級為 UIA 聊天文字 hash（`QDRANT_TEAMS_ASSIST_WATCH_MODE=uia`）

使用者需求：把 watch 偵測面從「像素 hash（任何畫面變化都是候選，
靠 120 s 冷卻壓誤觸）」升級為「聊天文字 hash」— 只有聊天真的有新
訊息才觸發 LLM 回合，幾乎零誤觸，且 Teams 收在背景/最小化也有效。

* **引擎**（`src/teams_assist_tools.py`）：
  * `_watch_mode()` — `QDRANT_TEAMS_ASSIST_WATCH_MODE` = `screen`
    （預設，行為完全不變）| `uia`；非法值退回 `screen`。
  * `_default_uia_read()` — 走既有 `_uia_ops("read")` TextPattern 通道
    讀聊天文字；失敗/空白 raise。
  * `_uia_signature()` — CRLF 正規化後 sha1 + 字元數（純本地運算，
    零 LLM token）。
  * `TeamsAssistWatcher(uia_read=...)` 可注入（mock/測試）；`poll_once`
    在 watch_mode=uia 時先走 `_poll_uia`：文字 hash 沒變 → 直接跳過
    （不燒 LLM token）；變了 → 共用冷卻閘（`_cooldown_gate` 抽出共用）
    → fire payload 帶 `watch_mode:"uia"` / `chat_text_hash` /
    `text_chars`。**UIA 讀取失敗自動退回像素 hash 路徑**（記
    `state.last_uia_error`，絕不因此暫停 watch）。
  * state 新增 `last_uia_hash` / `last_uia_error` 水位線；`start()`
    在 `watch` 記錄 `watch_mode`；像素路徑 payload 也補 `watch_mode:
    "screen"`。
* **host** — `teams_assist_status` 新增 `watch_mode` / `send_mode`
  欄位（stdio 鏡射同步，工具數不變仍是 9）。
* **prompt** — system prompt Teams 區塊增補 Watch modes 說明。
* **env** — `.env.example` 新增 `#QDRANT_TEAMS_ASSIST_WATCH_MODE=screen`
  註解區塊；live `.env` 啟用 `uia`。
* **測試** — `tests/test_teams_assist.py` 67 → **76 tests**（+9：
  resolver、CRLF 正規化、變化→fire/不變→跳過+水位線、冷卻閘、UIA
  失敗退回像素路徑、空白退回、start 記錄 watch_mode、失敗永不暫停、
  status 欄位）。

`pyproject.toml` 0.9.70.1 → **0.9.71.0**。

### v0.9.66.1 — Embedder local llama.cpp 嵌入長度護欄（排程長回答 400 → cooldown 根因修復）
### v0.9.66.1 — Embedder local llama.cpp 嵌入長度護欄（排程長回答 400 → cooldown 根因修復）

Live log 2026-09-16 14:44（排程「下午2點股市異常分析」answer=1240 字
完成後）:`[Embedder] backend 'local' failed (HTTPError 400 ... )` —
local llama.cpp 嵌入被拒、進入 300 秒 cooldown、期間嵌入退到
OpenRouter(Nebius)。

* **根因（live 探測實證）**:llama.cpp 伺服器正常（`qwen3-embedding.gguf`
  載入中、短文本 4096-dim 200）,但嵌入 slot 的 `n_ctx=1024` —
  排程任務完成後 SessionManager.save → memory_store 把**最後一組
  Q/A**（問題 + 1240 字回答 ≈ 1201 tokens）送嵌入,超過上限 →
  `{"error":{"code":400,"type":"exceed_context_size_error",
  "n_prompt_tokens":1201,"n_ctx":1024}}`。實測 ratio ≈ 0.78-1.0
  token/字（6300 字 → 4901 tokens）。
* **修復（應用端護欄）** — `src/ingest/embedder.py` 新增
  `_resolve_local_max_chars()` + `Embedder._clip_local()`:
  `QDRANT_EMBED_LOCAL_MAX_CHARS`（預設 **950** 字,安全低於 1024
  tokens;0 = 停用;無效值退回預設）— 超過上限的文字在**送進 local
  POST 前截斷**（`_embed_uncached` 的 local leg + `_embed_batch_local`
  兩處）;正常長度文字 byte-identical 通過;向量快取仍以**原始全文**
  為鍵（語意與既有 cache 行為不變,與 OpenAI leg 的 `[:8000]` 慣例
  同型）。效果:local 後端**永不 400** → cooldown 不再被長文本觸發,
  正常嵌入全部留在本機。
* **治本選項（未動,供維運參考）**:調高 llama.cpp 啟動參數
  `--ctx-size`（`C:\llama.cpp\start-llamacpp.bat`,目前 4096 =
  4 slots × 1024;可改 8192/16384 並重啟伺服器）— 應用端護欄與之
  互補,不衝突。
* **測試** — `tests/test_embedder_resilience.py` +4
  （`TestLocalClipGuard`:resolver 預設/覆寫/無效值/0 停用 ×1、
  passthrough+截斷+停用 ×1、單筆 POST payload 實際截斷且 cache 鍵
  為全文 ×1、batch payload 逐項截斷 ×1）;全套 30 passed。

`pyproject.toml` 0.9.66.0 → **0.9.66.1**。

### v0.9.66.0 — hicloud HWS 完整服務面補完（依 hicloud_rest_api_v2.md：8 → 56 工具）

### `cli.py chat` 支援的 toggle 與管理指令

| 指令 | 用途 | 影響 |
|---|---|---|
| `/info` | 顯示 session 資訊 | 唯讀 |
| `/reset` | 清空 history + session 訊息 | 下一輪重頭開始 |
| `/list` | 列出 ~/.qdrant-rag-agent/sessions/ 所有 session | 唯讀 |
### v0.9.66.0 — hicloud HWS 完整服務面補完（依 hicloud_rest_api_v2.md：8 → 56 工具）

使用者需求:「依 hicloud_rest_api_v2.md 補完 mcp hicloud-local 功能」。
以官方文件（CaaS + CVPC 共 53 個 action）為依據，把 `hicloud-local`
從 v0.9.64 的 8 工具補完到 **56 工具**（+48 個資料驅動工具，涵蓋
12 個服務的所有剩餘動作）。

* **`src/hicloud_tools.py` — 資料驅動 CRUD** — 新增 `_RES_SPECS`
  （12 類資源的 list/create/delete/modify action + id 參數名 +
  mock id 前綴）+ 記憶體 mock 資源簿（volume/slb/image_user/subnet/
  acl/vpc/igw/internet_ip/intranet_gw/intranet_link/nat/port_fwd，
  `_mock_res_*` helper；確定性 mock id 如 `VD550100000001`）+
  通用 `_res_list/_res_create/_res_delete/_res_modify`（mock 與
  live 同構；live 的 `_api_get` 沿用官方簽章；清單 key 以
  `_extract_list` 容錯）。生命週期寫入一律過 `_gate_write`
  （每呼叫 `confirm=true`；live 另需 `HICLOUD_WRITES_ENABLED=on`）。
* **新增服務面**：
  * **VM 補完** — `modify_instance_attribute`（原機升降級/供裝監控）、
    `restore_instance`（快照復原）
  * **CloudWatch** — `get_instance_stats`（CPU/DISK/Network 近即時）
  * **Log** — `log_list`（listLog，平台操作紀錄）
  * **EBS** — volume create/attach/detach/delete/list（radix 1-20 ×
    100GB；attach/detach mock 更新 attachment 欄位）
  * **Image 補完** — image create/delete/modify（由線上 VM 建範本）
  * **SLB** — create（CaaS 形狀 instanceId+port；CVPC 簡易/進階形狀
    vpcBusinessServiceId+loadBalancerType+portsInfo/ripsInfo）/
    delete/list（5 種查詢模式）/vip_health/replace
  * **Subnet** — create（CVPC 需 vpc_bsid；CaaS 需
    intranetGatewayBusinessServiceId）/delete/list/modify
  * **NetworkAcl（防火牆）** — create（CaaS 舊式 policy 字串 /
    CVPC 新式 `subnet$/fqdn$/iprange$…`）/delete/list/replace
  * **VPC（CVPC）** — create/delete/list/modify
  * **Internet Gateway + IP** — igw create/delete/list（ipVersion）、
    internet_ip create/delete/list
  * **Intranet** — gw create/delete/list/modify、link create/delete/list
  * **NAT + PortFwd** — nat create/delete/list、pfwd create/delete/list
* **`HicloudLocalHost` — 資料驅動工具面** — 模組級
  `_HICLOUD_TOOL_SPECS`（48 個 spec：name/desc/schema/engine
  method）+ `_hicloud_schema` compact builders + `_engine_call()`
  （tool args 1:1 映射 engine kwargs）+ `list_tools()` 迴圈生成
  （8 個 v0.9.64 手寫工具保留在 `_base_tools()` 不動 — 既有測試
  與 dispatch 相容）。stdio mirror `hicloud_server.py` 新 48 工具改
  走 `_TOOL_NAMES/_TOOL_METHODS` dispatch（56 工具 parity）。
* **System prompt** — hicloud 區塊補 v0.9.66 服務面清單（查詢類
  不需 confirm；建立/刪除/異動/掛卸載需 confirm=true）。
* **測試** — `test_hicloud_host.py` 44 → **47**（新增
  `test_new_service_tools_advertised`（48 名稱+schema 全數在位）、
  `test_new_service_handlers_e2e`（mock CRUD round-trip 全鏈）、
  `test_new_write_gates`（34 個寫入動作逐一驗 confirm 閘）；
  `test_eight_tools_advertised` 更新為 56 工具面 + 無重複名稱；
  confirm 欄位檢查擴充到全部 39 個寫入工具）。mock 引擎手動煙霧：
  12 類資源 CRUD 全綠。

`pyproject.toml` 0.9.65.0 → **0.9.66.0**。
| `/load <id>` | 切換到指定 session | 影響 history + banner |
| `/web on` / `/web off` / `/web status` | 切換 Ollama Web Search 注入 | 影響 _build_context() |
| `/mcp on` / `/mcp off` / `/mcp status` | 切換 MCP context injection | 影響 _build_context() |
| `/mcp servers` | probe enabled servers 連線 | 唯讀 |
| `/mcp on <name>` / `/mcp off <name>` | enable/disable server | auto-save config/mcp.json |
| `/mcp list` / `/mcp status <name>` | 列出/probe server | 唯讀 |
| `exit` / `quit` / `:q` / `e` / `Ctrl-C` | 離開 | finally 呼叫 agent.shutdown() |

### v0.9.65.0 — chat web 檔案下載（⬇ chip + `GET /download` + GUI 另存新檔）

使用者需求:「chat web介面提供檔案下載功能」。上傳（v0.9.16.0
`POST /upload`）的反方向補齊：LLM 在沙箱產出的檔案（`write_file` /
`exec_python` 圖表 / archive / `uploads/` 上傳檔 / `onvif_record`
MP4）可以直接從 chat 介面下載回來。

* **`gui/web_server.py` — `GET /download?path=<p>`** — 沙箱檔案串流
  下載端點：與 `Api.read_sandbox_image` 同一套解析（`file:///` 剝除、
  相對路徑錨定 WRITE root、import ∪ READ ∪ WRITE roots 包含檢查 —
  **絕不是任意檔案讀取**）；危險執行檔拒絕（`_FS_DANGEROUS_WRITE_EXTS`
  同款 blocklist — 403）；大小上限 `QDRANT_WEB_DOWNLOAD_MAX_MB`
  （預設 200 MB，最小 1，無效值退回預設 — 對齊 `_upload_max_bytes`
  模式，超限 413）；**1 MB 分塊串流**（大檔不進記憶體）+
  `Content-Disposition`（**RFC 5987 `filename*`** — CJK 檔名安全）
  + `X-Content-Type-Options: nosniff` + mimetypes Content-Type；
  錯誤乾淨 JSON 包絡（400 無參數 / 403 沙箱外或危險副檔名 /
  404 不存在 / 400 目錄 / 413 超限）。
* **`gui/api.py`** — 新 `_resolve_sandbox_file()`（共用解析 helper，
  回 `(path, status, msg)`）+ `_download_name_allowed()`（危險 ext
  檢查）+ **`save_sandbox_file_as(path, dest_dir)`**（GUI 模式下載
  橋接：複製沙箱檔到使用者選的資料夾，碰撞 `-N` 去重）。
* **`gui/assets/app.js`** — `renderChat()` 尾新增
  `renderSandboxFiles()` pass（`renderSandboxImages()` 之後）：掃描
  每個氣泡文字中的**非圖片**沙箱路徑（`C:/...` 絕對、`/` 磁碟根、
  `uploads/` 相對 — 與圖片 pass 同款 regex + before-char 驗證；
  `png/jpe?g/gif/webp/bmp/svg` 已有預覽故排除），每個路徑生成
  「**⬇ 下載檔案 · 檔名**」chip（`textContent`-only，無 XSS 面）。
  點擊 → **web 模式**：`window.open('/download?path=...')`（瀏覽器
  下載）；**GUI 模式**：`pywebview.create_file_dialog(FOLDER)` 原生
  選目的地 → `save_sandbox_file_as` 複製 → toast 回報存檔位置。
  i18n `downloadFile` / `fileSaved` ×4 locales。
* **`gui/assets/style.css`** — `.chat-download-chip` 膠囊樣式
  （hover/active 狀態、ellipsis 截斷）。
* **安全模型**：下載 = 已在沙箱內的檔案（上傳工具本就拒寫執行檔 →
  沙箱不會有執行檔；download 端再擋一層保持一致）；web server 仍受
  綁定位址警告保護（非 loopback 記 WARNING）。
* **測試** — 新 `tests/test_web_download.py` **38 tests**（resolver
  ×8、危險 ext ×2、GUI 複製 ×5、上限 ×3、Content-Disposition ×3、
  live round-trip ×8（200+headers / 相對 uploads/ / 404 / 403 /
  400 / 413 / 無參數）、前端 source guards ×9）。

`pyproject.toml` 0.9.64.0 → **0.9.65.0**。

### v0.9.64.0 — hicloud HWS 雲服務 MCP 擴充（中華電信 CaaS/CVPC，mock-first + 簽章）
