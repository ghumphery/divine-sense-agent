# ChangeLog — divine-sense-agent

> Moved out of README.md (README = current usage only; history lives here, newest first). Sections kept verbatim.

---

## 更新日誌

### v0.9.130.2  

**小狗提起模式 CARRY（user:「怎麼樣才能把小狗提起來」→ 選定「點一下提起：懸在半空跟著你走，再點一下才放下」）。** **取代 130.0 的「摸摸坐姿小狗＝lift→putdown 瞬間反應」**——現在同一個點擊真的會把狗提起來帶著走：點坐著/發呆的小狗→❤️ 粒子＋lift one-shot（720ms 躍起）→進入 **CARRY**：skin 加 `dog-carried`（凍結在 lift sheet 騰空幀 f1 `background-position:-150px`＋`dog-carry-sway` 0.9s 輕擺）、runner 移除 `dog-stopped` 加 `dog-following`（拿掉 sprint 動畫）＋新 `dog-carrying`（`transition: left .12s linear, bottom .12s linear`——follow 原只 transition left，垂直也平滑）、**專屬 mousemove listener 同步雙軸**（left 置中游標 clamp 4..w-154、bottom 置中游標 clamp 4..h-130——狗始終懸在游標正下方，**下次點擊必然落在狗上**＝天然 toggle）；**再點一下＝放下**：`_dogCarryOff` 移除 listener＋skin class、**bottom 歸 4px 落地**→putdown sheet→settle 坐下回 idle 輪替。**提起≠摸摸**：pickup 重置三連摸 petStreak（防提著走時殘留 streak 觸發跟隨互搶）；反應播放中/睡著點擊語意不變（bite 優先）；`_dogRunStop` 清理擴及 `_dogCarryMouse` listener＋`_dogCarry` 旗標——**回合終態/`/endpet` 在提著走時＝乾淨全拆**。CSS +3 block（`.dog-carried`／`@keyframes dog-carry-sway`／`.dog-runner.dog-carrying`）。tests：dog_runner 8→**10**——+`test_carry_mode_v0130_2`（source guards：雙函式/雙 class/雙軸 bottom/listener/RunStop 清理/streak 重置/CSS 四斷言）+`test_carry_pickup_putdown_jsdom`（**行為級實證**：點坐姿小狗→先 lifting→820ms 後 `dog-carried`+riding、mousemove 後 left=225px/bottom=508px（游標置中）、再點→floor+putdown+去 carried、1800ms 後 dog-stopped settle）；v0130_0 docstring 摸摸語意同步改為 carry。**順手修復：v0.9.130.1 slash 測試插入時錨點匹配到 v0130_0 測試中段——把該測試的 CSS 斷言尾部整段切離成孤兒（卡在 jsdom 測試內、`css` 未定義必 NameError）；本次把 CSS 斷言尾搬回 v0130_0 原位**。pyproject 0.9.130.1 > **0.9.130.2**。  

### v0.9.130.1  

**本地聊天指令 `/playpet`／`/endpet`（user:「支援下命令 /playpet 小狗就會出來 /endpet 小狗就會回去休息結束」）。** `_sendChatCore` 最頂端（**pending-upload merge 之前**——指令永不吃掉待上傳 markers，下一則真訊息仍會帶上）攔截 `^\/(playpet|endpet)\s*$`（大小寫不敏感、容忍尾隨空白）：**純本地分流、不送 LLM**——清輸入框→按現況分流（`dog-run-layer` 是否存在）→呼 `_dogRunStart()`（冪等，已在場＝只 toast「小狗已經在玩了」）或 `_dogRunStop()`（冪等淡出；**回合進行中下 /endpet 也安全**——回覆終態 callback 再呼一次是無害重入）→toast 回饋。**回饋刻意 toast-only、不落泡泡**：假 assistant 泡泡會進 session 歷史被 LLM 當上下文，污染後續回合。i18n `petComeOut/petAlreadyOut/petGoesRest/petNotOut` ×4 locale。tests：`test_dog_runner.py` +`test_slash_commands_playpet_endpet`（slash 正則／四分流斷言／**順序守衛：slash handler 在 pending merge 之前**／i18n 4 keys ×4 locale）；`wired_into_send_core` 既有契約不受影響。pyproject 0.9.130.0 > **0.9.130.1**。  

### v0.9.133.18  

**背景任務 write-back 空占位修（user 貼 live 11:48-11:49：「開啟專案名稱 esp32s測試…」→「已送出背景任務 5efa5469」→ **11:49 顯示「(無回覆 — 模型未產生內容)」**——而任務本身**成功**（`task-5efa5469.json` 的 reply 426 字完整在：「專案已建好 ✅ … esp32s_test/ PlatformIO…」）。**根因**：`TaskLocalHost._run_once`（**手动 ☑ 背景執行／LLM 投放的共用路徑**）的 `begin_task` 之後**漏呼叫 `task_runner.attach_sid(task_id, sid)`**——registry 的 `rec["sid"]=None` → write-back 輪詢（`bgWatchTick → get_background_task`）的 `if rec.get("sid"):` 分支不成立 → **`answer_preview` 從來不填** → 前端抓到空字串 → **占位泡**，且 dedupe（`m.taskId`）之後**永不補寫**（資料一直躺著但 chat 泡是佔位）。對照組：detach 升級路徑（gui/api.py 1794）與 ingest 背景路徑（host 1819）**都有** attach_sid——只有這一條漏。**修**：`_run_once` 在 `begin_task` 之後立即 `attach_sid(task_id, f"task-{task_id}")`（**一行**）。tests: `test_gui_task_panel.py` 的 run_once 行為測試增 `rec["sid"]==f"task-{task_id}"` 斷言＝task_panel+turn_bg_detach **41 passed**；py_compile＋node --check 綠。pyproject **0.9.133.17 > 0.9.133.18**；**重啟生效**。

### v0.9.133.17  

**多輪抓取目標鎖定（user: 「透過Telegram 抓 twist/X.com 的新聞多篇以後會抓錯目標文章，需提醒才會正確」＝TG bot 長對話的目標漂移）。** **根因（兩路）**：①**壓縮擠掉唯一鍵**——tgbot session 永續（24+ 回合觸發壓縮）：LLM 摘要 prompt 沒要求保留 URL／文章標題；`_squeeze_prose` 只保頭尾散文句、中段被擠的句子裡的**精確連結**也一起消失 → 下一回合 LLM 只能憑殘存語意猜目標；②**索引慣性**——「第 N 篇」式的抓取引用：X 首頁時間軸的順序**每回合都變**，用 index 定位必然漂移。**修**：①`Agent._summarize_history` prompt ＋「**VERBATIM-preserve every URL / x.com post links / 文章標題**——下一回合要重用精確連結」；②`_squeeze_prose` 兩處 return 接新 `_append_omitted_urls()`（regex `https?://`）——被擠掉的 URL **逐字附回**輸出尾部「URLs present in the omitted part」（已保留的不重複）；③**rulebook 新 [r10]**（多輪抓取的目標鎖定：每篇抓取必須引用 URL/帖子連結/標題原文、禁止只用「第 N 篇」漂移索引、抓前先複述目標、抓到後不對上就先比對再問——[r8] zipfs/[r9] exec 降級之後的第三條行為規則，免重啟下回合生效）。tests: `test_context_compress.py` +3（squeeze 保 URL verbatim／不重複附回／summarize prompt 源守衛）＋hint 附加的既有斷言放寬（fire-and-forget hint 是 127.0 設計行為）＝**38 passed**。pyproject **0.9.133.16 > 0.9.133.17**；**重啟生效**。

### v0.9.133.16  

**save 競態重試＋假任務回執防護（live 17:20 連爆兩案：`save_session FAILED: PermissionError(13,'存取被拒。')`＝**每秒輪詢的 Sessions 讀窗口**與 autoSave/背景任務保存的 `os.replace` 撞鎖（WinError 5——Windows 拒絕 replace 到**開啟中的**目標，讀窗 <100ms）＝**該次 assistant 回覆**當場丟失；＋session 17:07 的助手回覆是**編造的**「已送出背景任務 8a2c1f4b (session: task-8a2c1f4b)」——查證 `task-8a2c1f4b.json` **不存在**＝假 tool call（run_task_once 根本不在 routed surface 上）。** **修**：①**`SessionManager.save` 的 `os.replace` 加 `PermissionError` 退避重試 ×5**（0.08/0.16/0.24/0.32s——讀窗級毫秒鎖全被吞掉；第 5 次仍鎖才 raise＝呼叫端的 ERROR log 照舊誠實）；②**`_TOOLROUTE_CORE_SERVERS` += `task-local`**——`run_task_once`（agent 的「不堵使用者」通道）上**每一個 routed surface**，消除「想投背景卻無工具→編造回執」的诱因（surface +2 低成本工具）。tests: `test_toolroute.py` +1 core 守衛＝**31 passed**。**附帶觀察**：`TurnTiming recall+prompt=21.6s`＝`QDRANT_RERANK=on` 的 retrieval rerank 成本（rerank.py 的 prefill 是重活——**品質/速度取捨另案**，memory recall 本身乾淨無 rerank）。pyproject **0.9.133.15 > 0.9.133.16**；**重啟生效**。

### v0.9.133.15  

**routed 工具從「未裁目錄」挖掘＋按 routed 順序分組（17:07 實錘第二層真凶：133.14 的降級已生效（ToolRoute 後無 trim），但 `_toolroute_tool_names` 內部的 `active_tools()` 走 **500 cap 的裁過目錄**→ `wordcraft` 位於 `MCP_ACTIVE_SERVERS` 優先序 #37＝目錄尾＝被 cap 裁掉→names 挖不到 wordcraft 工具→surface 仍然缺 docx 工具→`dispatches=0`；log 的 `566>500` WARNING 其實來自這個 mining 呼叫）。** **修**：①manager 新 `raw_tools()`（**未 gate 未知、未 cap 的完整 catalog** 快照）；②`_toolroute_tool_names` 改用 `raw_tools()`（**fake-managers 只實作 active_tools 時相容**）；③names **按 routed servers 順序分組排序**（同 server 內保持 catalog 穩定序）＝**hard cap（128）永遠先裁非命中工具、命中的在前**；④`.env` `MCP_ACTIVE_SERVERS` 順序調整：wordcraft/deckcraft 前移至 #9/#10（docx/簡報是日常主力、不再被 cap 概念性墊尾）。tests +1 `test_route_names_survive_surface_cap`（fake 三層目錄：60 core＋260 非 routed＋16 wordcraft 在尾——**500 cap 內無 wc、500 外有**——names 必含 wc＝舊行為會丟、新行為保住＝行為級鎖定）＝toolroute+prev_reply+meta+mcp_client+zipfs 合跑 **97 passed**。pyproject **0.9.133.14 > 0.9.133.15**；**重啟生效**。

### v0.9.133.14  

**低置信 ToolRoute 聯集降級修（live 2026-10-22 16:39 一段 log 定罪：docx 填表任務 `class=None p=0.57` → 走全 fleet 面 → `tool surface 566 > 500` 按**註冊順序**裁尾 → `wordcraft`/`deckcraft`/`filmcraft`/`blender` 被裁 → `dispatches=0`，LLM 拿不到 docx 工具只能文字回答）。** **修**：agent.py 的 ask＋chat_turn 兩處 ToolRoute 降級條件 `_tcls` → **`(_tcls or _srvs)`**——`_srvs`（`_toolroute_servers_for` 的回執）**本來就含問題側觸發列命中**（119.3 的 union-only：`…docx` → wordcraft …）＋core 集——Jev 沒把握時改用它做本回合 surface＝**命中相關工具保住 + 進一步省 token**（cap 500 情形消失）；shadow 模式照舊只 log，Jev 高置信路徑零變化。tests: `test_toolroute.py` +3 TestLowConfidenceUnionV013314（docx 問題不論 class 都命中 wordcraft／普通題不誤掛／源行守衛 ×2）＝**30 passed**。pyproject **0.9.133.13 > 0.9.133.14**。

### v0.9.133.13a  

**預算 10GB／100 萬 member（user「1.改為 10GB 和 100萬個」；commit `3139530`）。** `.env`：`QDRANT_ARCHIVE_MAX_BYTES=10737418240`+`QDRANT_ARCHIVE_MAX_FILES=1000000`（archive-local 與 zipfs-local 共用；單次寫入/抽出同 cap）；**順手真 bug 修**：zipfs host 的 `_write/_delete/_rename` 此前直接使用 512MB/10k **常量**，未接 env——改走既有 `_archive_budget()`（**.env 變更現在真的生效**）；`.env.example` 補註釋模板。實證：`load_dotenv` 後 `_archive_budget()` 回 `(10737418240, 1000000)`。

### v0.9.133.13  

**zipfs-local MCP 上線（user: 「是否可以製作擴充MCP 用來可以掛載.zip或.tar當作檔案系統操作(list、新增、修改、刪除、異動裡面的檔案和目錄等檔案系統操作)，未來使用者可以將所有想處理的檔案和目錄一起打包成一個單檔上傳到本專案進行上傳、處理和下載」）——上傳單檔壓縮包 → LLM 直接在包內做檔案系統操作 → 下載同一份單檔，全程不解壓。** **新模組 `src/zipfs_tools.py`**（純格式邏輯，~660 行）：**zip/tar/tar.gz/tar.bz2 讀寫**（copy-on-write 原子 rebuild = temp 同目錄重建→完整性探測（zip CRC/tar getmembers）→`os.replace` 覆蓋原檔——中途崩潰永不丟使用者的上傳；**覆寫語意**（同名额跳過舊份防重复膨脹）、**前綴語意**（刪/改名尾 `/` = 子樹成員全展開））；**7z 只讀**（py7zr 可選——list/read/extract_one 可用，變異工具誠實報錯）；**gz 單檔流拒絕**（請走 archive_*）。安全繼承 archive 紀律：member 名規範化並拒絕 `..`/絕對路徑/盤符；寫入拒絕 `.exe/.bat/...` 危險擴展（與 filesystem 寫沙盒同黑名單）；member 數/未壓縮總量雙預算沿用 512MB/10k。**新二十號 LocalHost `ZipFsLocalHost`（server `zipfs-local`，8 工具）**：`zipfs_list`（深度限樹+子目錄根）/`zipfs_read`（單 member——utf-8 文本解碼、二進制回 base64+hint）/`zipfs_write`（text|base64 新增或覆寫）/`zipfs_delete`（confirm 閘+子樹）/`zipfs_rename`（confirm 閘+子樹）/`zipfs_extract_one`（抽一個 member 到 write sandbox 預設 uploads/——dangerous 預設拒）/`zipfs_info`/`zipfs_status`。沙盒錨定與 ArchiveLocalHost 同款：讀=read∨write root；**變異=必須在 write root 內**（read root 的包只可瀏覽/抽取）。**接入面**：manager 註冊（archive 後、Meta 前，`_zipfs_local_host` ref）＋stdio mirror `src/mcp_server/zipfs_server.py`（8 工具對稱）＋live `.env`/`.env.example` `MCP_ACTIVE_SERVERS` += `zipfs-local`（41.1 教訓：漏列=工具全隱藏）＋ToolRoute trigger 行（`zipfs_|修改 zip|在壓縮檔內|edit the zip|in the zip|archive member`…）＋system prompt「Zip/Tar as a FILESYSTEM」區塊（info→操作→同單檔下載流程；與 archive_* 的分工）。**使用者流程閉環**：web 上傳 zip（既有 `/upload`）→ 對話直接 zipfs 操作（uploads/ 在 write root 內）→ 修改後同一檔用前端既有 ⬇ chip 下載。tests: `tests/test_zipfs_host.py` **20 passed**（純邏輯 zip/tar 往返+守衛+預算+只讀邊界、host 面板 8 工具/confirm 閘/read-only 拒/extract 預設 uploads、manager 註冊+gate 隱藏、prompt+trigger union、stdio parity）；回歸 meta/mcp_client/gui_chat_tools **57 passed**。修過的真 bug：覆寫語意（同名先跳過舊份）、前綴 rest 雙斜杠、`/abs` lstrip 洗掉絕對判斷。pyproject **0.9.133.12 > 0.9.133.13**。

### v0.9.133.12  

**移除惰性 web-search 開關（user: 「對話 的 選項( 網路搜尋 和 背景執行 ) 是否不必要再存在」）——實證後擇要刪：🌐 網路搜尋 是遺物、☑ 背景執行 保留。** **實證**：`use_web_search` 在工具鏈路（GUI 對話恆走兩段式 tool-loop）的**唯一真實作用點**有二：legacy `tools_enabled=False` prompt 的 web 預烤 RAG 與 `web_enabled` 在 prompt 尾端**兩行引用規範**——tool 版 system prompt **根本不讀** `web_enabled`；**真實網路能力** = ToolRoute/Jev 依問題意圖自動掛 my_web/firefox_craw 工具面 + LLM 自主呼叫（v0.9.104.2 live E2E）→ 手動開關在對話路徑**零效果**（背景任務/排程同型）。**移除面**：①chat toolbar `#chat-web-toggle` + `data-i18n=webSearch`；②排程編輯器 `#schedule-edit-web-search`（scheduleFieldWebSearch）＋`#schedule-edit-web-max`（scheduleFieldWebMax）——`use_web_search`/`web_max_results` 都是零效果遺物（後端 Schedule dataclass 欄位不動，缺省 false/5）；③app.js：useWeb getter、i18n `webSearch`/`scheduleFieldWebSearch`/`scheduleFieldWebMax` ×4 locale、form 讀回/提交行；兩個 call site（start_turn/submit_background_task）改傳 `false`（位置參數契約不動）。**保留**：☑ 背景執行（勾＝送出即投放背景、UI 不鎖；不勾＝前 60 秒鎖窗等 133.1 自動 detach——兩者功能等價但釋放時機不同，是控制權不是贅餘）。tests: 新 `test_web_toggle_removed.py` ×4（反守衛：toggle/getter/i18n 全不存在＋call 傳 false＋bg-toggle 仍在）＋`test_gui_task_panel` 的 `useWeb` 源行斷言同步 false＝34 passed；web 系回歸 web_server/sessions_summary/session_rename **94 passed**、task_panel+turn_bg_detach **41 passed**；`node --check` OK。pyproject **0.9.133.11 > 0.9.133.12**。

### v0.9.133.11  

**排程摘要 pin 三層修（凌晨 03:00 首火診斷：排程有建、cron 有 fire（03:00:09），但 `summarize_sessions` 根本沒被呼叫——memory_docs 的 session-summary 停在 1 筆）。** 三層根因＋修復，全部由 live log 逐層證實：**層①pin 白名單拒收**（03:00 fire log：`llm pin 'llamacpp:qwen2.5-7b-cpu' invalid — provider 'llamacpp' is not allowed`——v0.9.110.0 的 `ALLOWED_PROVIDERS` 只收 openrouter/ollama，133.3 只把 llamacpp 加進 **compactor** 的 routing，task pin 層沒加）→ **task_llm.py：白名單+目錄+Agent 三面接線**：`ALLOWED_PROVIDERS += "llamacpp"`；新 `_catalog_uncached_llamacpp`（GET `QDRANT_SESSION_SUMMARY_LLAMACPP_URL`（與 compactor 同一 env 鍵，預設 :8090/v1）+`/models`——live `/v1/models` 形狀驗證 `data[0].id="qwen2.5-7b-cpu"`；server down = ok+UNVERIFIED 降級同 ollama 慣例）；**agent.py `Agent._pick_provider` pin 分支新增 llamacpp 直連路徑**（`_llamacpp_chat_base()` module helper 同 env 同預設；api_key dummy、clean extra_body；**刻意不進 `_PROVIDER_ORDER`**——弱 CPU 摘要模型絕不可被 auto-detect 收成聊天主鏈）。**層②工具面沒 memory-local**（03:00 fire 的 Jev class=chat-history 只給了 chat/datetime/exec/filesystem…清單，memory-local 的 `summarize_sessions` 不在 surface——LLM 只能 grep 目錄確認存在再講半句結束）→ **agent.py `_TOOLROUTE_SERVER_TRIGGERS` memory-local 行擴詞**：+`summarize_sessions|session[_ ]?摘要|摘要沉澱|沉澱`（union-only：就算 Jev class 判錯，prompt 命中仍掛上 memory-local）。**層③pin 直連後 500 工具 schema 撐爆本機 ctx**（15:12 run-now 真火：pin 生效+memory-local 已掛，但 request 27,174 tokens > CPU server 12,288 ctx → `BadRequestError 400 exceed_context_size_error`——500 工具 schema 佔 ~2 萬 tokens；03:00 那次因 pin 被拒走雲端所以沒炸）→ **agent.py `_chat_tools` 新 pin 面**：pin provider=llamacpp 時 surface 與 `QDRANT_LLAMACPP_PIN_SERVERS`（預設 `memory-local,datetime-local,meta-local`，~9-14 工具）**交集**——本機弱模型 fire 場景只需要小工具面；非 pin/openrouter/ollama pin 不受影響。**層④（實測補刀）通用 system prompt 本身就超 ctx**（縮面後第二輪真火 15:21：tools 從 71 縮到 9、tokens 只降到 20,579——分解實測＝**system prompt（988 行通用 trigger 手冊）單獨 19,330 tokens** > 12,288——云端大 ctx 無感、本機小 ctx 任何 llamacpp pin turn 必爆）→ **agent.py 新 `_pin_narrow_prompt()`**：pin llamacpp 的回合 system prompt 切**專用窄版**（scheduled-task runner 批次任務指令＋誠實回報規則＋「OMIT optional args」教學，~450 chars；memory recall block 照掛）＝實測全套 prompt+9 工具 ≈ **2.4k tokens ≪ 12,288**；**窄版立竿見影**：第三輪真火 15:40 `pin surface: 71 -> 9 tools` 後 LLM **真的 dispatch `summarize_sessions(days=1)`**（qwen2.5-7b 的 tool-call 全通：8090 log 見 prefill 79-86 tok/s 逐 session 推進、memory_docs session-summary 1→3 增長中）。**層⑤llm 參數兜底**（第三輪火的第一發回執：LLM 自己**捏造** llm/junk 參數→16 發全 `LLM returned an empty summary`=except-""；真凶是裸 model 名吃了 `Agent` 兜底）→ **session_compactor `_default_llm_call`**：無 llm/junk pin/**裸 model 名**（無 provider: 前綴）一律落回 `QDRANT_SESSION_SUMMARY_LLM` env pin（單一事實源直連 8090），有效 pin 才准 override。**驗證**：tests test_task_llm +7（parse/validate 命中/UNVERIFIED/pin 直連/env 覆寫/**narrow prompt 兩條**）+test_toolroute +2＝31+27 passed；**live run-now 真火**（修復+重啟後）：`task model pin: llamacpp:...`＋`pin surface: 71 -> 9 tools` log + 工具真 dispatch → `summarize_sessions` 實跑沉澱。pyproject **0.9.133.10 > 0.9.133.11**；`.env`+`.env.example` 註記 1 新鍵。

### v0.9.133.10  

**背景任務行內執行 log（user: 「點執行中的背景任務無法看到執行的 log」＝⏳ 面板升格任務失去 ⚙ 執行紀錄的體驗）。** **根因**：前景回合的 ⚙ live log 面板由 `pollTurnStream` 驅動；132.1 的 detach 升格後**前端輪詢停了**（改由 bgWatch 的 8 秒結果輪詢），registry 也**沒記 turn_id**＝⏳ 面板只能看 status，執行紀錄斷線（且 `msg.turnLog` 進 session 的機制只走前景路——任務 session 的 assistant 訊息沒有 log 可回看）。**修：**①**registry 綁定**：`task_runner.attach_turn(task_id, turn_id)`（thread-safe；detach 時綁）＝record 出現 `turn_id` 欄。②**⏳ 面板行內 ⚙ 展開**（`_renderTaskRow` 改造 flex-wrap）：`running＋turn_id`＝**live 增量輪詢**（`poll_turn(turn_id, cursor)`——panel 的 2s tick 對 open 的行刷新 `_taskLogBoxes`／`_taskLogCursor`／delta guard；expand 狀態跨重建保留）；`terminal＋turn_id`＝一次全量 snapshot 回放（ring LRU 逐出前已由 ③ 保底）；**無 turn_id**（133.5 匯入任務等）＝誠實 note（`taskLogNoteIngest`／`taskLogNoteNone`；完成/失敗走 notification 鏈）。log 行 = 既有 `chat-exec-line` 渲染（`ts＋text`；textContent-only 零 XSS）。③**終態保底**：`_finalize_detached_turn` 把 turn events**寫進任務 session 的 assistant 訊息 `msg.turnLog`**（snapshot 全量 cap 120 件；空流也寫 `[]`＝前端自動隱藏；LRU 逐出後 Sessions/任務 session 仍有 log 可回看——與前景回合同款「查看執行 log」UI）。④CSS `.task-log-toggle`＋`.task-log-lines`（wrap 版面、max-height 38vh 可滾）＋i18n ×4×3 keys。tests：`test_gui_task_panel.py` **+4 TestTaskLogToggle**＋`test_turn_bg_detach.py` 的 finalize 行為測試**加 turnLog＋turn_id 兩斷言**＝**41 passed**；pyproject 0.9.133.9 > **0.9.133.10**；前端重整＋重啟生效。（v0.9.133.9 的 roll 落地修收在本表 v0.9.133.6 條目內——roll 系列同段。）

### v0.9.133.8  

**Chat 頁直接改當前 session 名（user: 「增加可以在對話頁中修改當下 session 的名稱」）。** **工具列 ✏️ 按鈕**（`#chat-rename`，session picker 旁；`✏️ <span data-i18n="sessRename">` 零新 i18n 鍵——改名用語 ×4 已存在），點擊呼**新 `renameCurrentSession()`**：**無 session 檔 → 先跑既有 `saveSession()`**（💾 同路建立檔案）→ 仍無 id（空聊建檔失敗）→ 誠實 toast；有 id → **重用 `sessionsRename()` 全套**（prompt 輸入＋`update_session_title` API＋**active-title lockstep**（改名後在編輯中的 chatSession.title 同步）＋picker/Sessions 兩表刷新）——**零重複實作**；tooltip = `sessRenamePrompt`（hover 提示）。bindEvents 在 chat-save/clear 綁定組同段（dead-element 安全式）。tests：`test_session_rename.py` **25 passed**（＋2：index 有按鈕＋i18n 綁定／app.js 函式+fallback+重用鏈+tooltip）。`node --check` OK。pyproject 0.9.133.7 > **0.9.133.8**；前端重整生效。

### v0.9.133.7  

**Sessions 管理即時摘要欄（user: 「對話計錄管理提供 summary 方便確認是否要改名或刪除」）。** `SessionManager.list()` 在**既有讀檔循環**裡零額外 IO 計算三個即時摘要欄（**無 LLM 無網路**）：`summary_first_user`（第一則真實 user 訊息 ~120 字 ≒ 對話主題；`[` 開頭的 marker 行**不算**）、`summary_last_assistant`（最後一則非 `[error]` 的 assistant 回覆）、`summary_chars`（全部訊息字元量）；GUI `list_sessions` 與 LLM 的 `list_chat_sessions` **同吃**。**前端 Sessions 表**：`#sessions-table` 新「摘要」欄（`thSummary` i18n ×4）＋每列 `sess-summary` 兩行（💬 主題／🤖 最新回答、`-webkit-line-clamp:2`、title=全文、error row colspan 6→7）。tests：**新 `test_sessions_summary.py` 9 passed**（欄位/120 字截斷/marker+error 跳除/空與壞檔/Api 傳遞/前端 guards ×4）＋回歸（sessions multi/rename/chat-history）＝**89 passed**（1 既有 colspan 斷言同步 7 欄）。pyproject 0.9.133.6 > **0.9.133.7**；前端重整生效（GUI/web 皆同源檔）。

### v0.9.133.6  

**iPhone Web App 預覽逃逸修——LLM 產出 `.html` 的應用內預覽（user: 「開了 open ui 網頁就回不去 iphone 的 web app」）。** **根因**：iOS standalone Web App **沒有返回鍵**——`.html` 的 chat chip 走 `window.open('/download…', '_blank')` → 在 iOS standalone 裡 `window.open` 把整個 app **導航出來**（standalone 內點連結＝原地離開、_blank＝進 Safari）＝使用者被丟在 OpenUI 頁、回不去。**修＝** ①前端 `renderSandboxFiles()` 對 `.html` 路徑**多生成一顆「👁 預覽」chip**（⬇ 下載照舊保留）——點擊呼 `openHtmlPreview(path, name)`：**應用內全屏 modal**（`#chat-html-preview`：深色 backdrop＋檔名列＋✕）內嵌 **`<iframe sandbox="allow-scripts allow-popups allow-popups-to-escape-sandbox">`** 載 `/download?path=`（**Web 模式**）／`file:///`（**GUI 模式**）——⇕ **不離開 app**：✕／Esc／backdrop 關閉即回到聊天；server 端 133.5 的 **CSP `sandbox` 已把回應變 opaque origin**，iframe sandbox 再加一層＝雙重隔離（摸不到 app 的 localStorage/cookies，CDN bundle 腳本照跑）。②CSS `.chat-html-preview*` 全屏 modal 樣式（z-index 2147482000、mobile friendly；lightbox 同款模式）。③i18n `htmlPreview`/`htmlPreviewClose` ×4。**同版追加（user: 「為什麼小狗跑到邊時的 roll 又停到半空中」＝v0.9.133.2 修狂閃留下的末格 bug）＝`_rollfix.py` 量測實證：roll sheet 幀序 cells 3/4（翻正落幀，重複幀）**bbox bottom=110（離 y=116 地面線 6px）**＋CSS `steps(4) to -600` 的 `steps(4,end)` **只走到 -450（第 4 格）＝forwards 永遠定格在離地 6px 的中間格**（第 5 格落地定格根本沒被走到）；`rollbreak`（900ms）被拍狗/情緒 timer 清掉時 `dog-rolling` 常駐＝狗凍在這個懸空定格＝「roll 停在半空中」。修：**①sheet 幀 3 貼地重切**（cells 3/4 內容下移 6px、bottom 110→116、x 保真、**冪等**）；**②CSS** `to -750px / steps(5)`＝0/150/300/450/600 **五個整格落點**（舊 4 步差一格）、定格即第 5 格（貼地翻正）；**③bust** roll `13320→13330`（13320 全退場）。tests：`test_dog_runner.py` **18 passed**（＋`test_roll_lands_on_ground`：cells 3/4 bottom==116＋CSS steps(5)/-750px＋13320==0）。pyproject 0.9.133.8 > **0.9.133.9**；頁面重整生效。**skill 庫藏同步**：`browser-preview.md` 的 Remote 節標註 133.6 的應用內 modal 行為。tests `test_web_download.py` **46 passed**（＋4：source guards（modal 函式/iframe sandbox 屬性/`_htmlPreviewSrc` 雙模式分流）/i18n×4/chip 雙顆分流/**jsdom 行為級**（openHtmlPreview 真跑→overlay+iframe+sandbox 屬性+src 斷言→✕ 關閉恢復）——jsdom runner 為本檔新增的最小 `_run_node_jsdom_inline`（tempfile＋60s cap＋Node-24 teardown 容忍）。`node --check` OK。pyproject 0.9.133.5 > **0.9.133.6**；iPhone 頁面重整 + web server 重啟（若已跑 133.5 的 inline）**重啟後生效**。

### v0.9.133.5  

**大文件 add_document 自動轉背景（user 核准「大文件自動轉背景」＝Telegram 撞線 600s watchdog 事件的自動化修正）。** **事件根因（v0.9.133.4 會話查明）**：Telegram 丟入 25MB/818 頁《Transformer 數學理論》PDF → `add_document` 同步單發 ingest（5,193 塊、全程 ~19 分鐘）撞上 `QDRANT_TOOL_CALL_TIMEOUT_S=600` 單工具 watchdog → `[tool watchdog]` 回報＋**thread 以 daemon 棄置**（未被殺，**背景繼續把全書寫完**——小機器人收到的是當下狀態，警語「沒有成功存進」是過期訊息；實況查證 5,193 塊全在、`embedding_backend=local` 全本機）。**功能（QdrantLocalHost）**：`add_document` 在 preflight 後接 `_maybe_background_ingest(target)`——**大小判定**：`st_size > QDRANT_ADD_DOCUMENT_INLINE_MAX_MB`（**預設 8 MB**；`0`=永不背景＝legacy byte-identical；壞值回退預設）→ ①**立即回** `{ok, background: true, task_id, filename, source, size_mb, message(中文)} `（含「不要輪詢、完成會自動通知」指引）；②**detached daemon thread** 跑同一條 `_ingest_via_pipeline`（dedup upsert＝重送同檔冪等續傳）＋registry `begin_task(source="ingest", title="匯入 <name>（N MB 背景任務）")`＋`attach_sid(task-<id>)`＋終態 `end_task(done/failed/stopped, answer_chars=chunks)` ——**terminal notify 鉤（gui/api.py 的 `_wire_task_notify` → Telegram send_message＋⏳ 面板）零改動全吃到**；③**stop 鏈**＝`pipeline.ingest_file(stop_check=…)` **新增 keyword-only 參數**（預設 None＝legacy byte-identical），`_embed_and_upsert` 批次邊界輪詢 registry stop flag → 🛑/`stop_task` 在下一批前優雅停、**已寫前綴保留**（break 印 `⏹ …N/total chunks partial ingest`）並標 STOPPED+answer_chars=已寫塊數；**工具描述**同步教 LLM「大檔轉背景、勿輪詢、一次 get_task_result」。失敗隔離：registry/thread 任何一步炸 → 降級回 inline sync（永不讓 tool call死）。**Live 期望**：同一本書再丟一次 → 秒回 `background=true, task_id=…`→ 19 分鐘後 Telegram 收到完成通知（5,193 塊全部 dedup skip＝0 重寫）。**同版本追加（user: 「遠端使用可以用嗎」）＝OpenUI 遠端預覽鏈**：`web_server /download` 對 **`.html` 改 `Content-Disposition: inline`＋新 `Content-Security-Policy: sandbox allow-scripts allow-popups`**——遠端（手機／區網／公網 `https://tchtool.app:8334`）點 chat 連結**直接渲染**（opaque origin＝同源 XSS 隔離：沙盒頁摸不到本 origin 的 localStorage/cookies，CDN bundle 腳本照跑）；其它副檔名維持 `attachment`；危險副檔名拒收＋大小上限照舊。tests `test_web_download.py` **42 passed**（＋4：html inline／非 html 保持 attachment／**live CSP header round-trip**／live 非 html 無 CSP）；openui skill 庫藏 `browser-preview.md` 增「Remote usage」節（Web 對話相對鏈／Telegram 絕對鏈或 `send_telegram_file`）。GUI/Telegram 重啟生效（host 是 in-process）。tests：**新 `tests/test_add_doc_background.py` 9 passed**（閾值×3（8MB inline/0=legacy/壞值回退）＋升格×3（完成 done+answer_chars+source/sid／失敗 failed+error／🛑→STOPPED+已寫前綴）＋pipeline stop_check×3（批次間 break 保留前綴／無 stop 全跑／kw-only None 契約））＋`test_qdrant_local_host_add_update` 既有 inline 路全綠（小 fake 檔不觸背景）。pyproject 0.9.133.4 > **0.9.133.5**；`.env.example` 註冊 `QDRANT_ADD_DOCUMENT_INLINE_MAX_MB`；GUI/Telegram 重啟生效（host 是 in-process）。

### v0.9.133.4  

**小狗跨動作忽大忽小 — `_bw` 資產尺寸正規化（user: 「小狗的圖 要注意大小 各動作之間 忽大忽小」；127.28「跑的時候大小不一致」教訓的跨檔維度重現）。** **根因實證**（`_dogsize_audit.py` 量測）：① **17 支 sheet 健康**——16/17 支 cell 內容高 104/120 對齊（belly 85／lie 96／throw 91 是天然姿勢差），跨支長邊中位 117/150=78%＝**不需動**（重切會傷幀連貫）；② **8 張 `_bw` 直掛是災區**——畫布 512×512／1024×1024／300×300 三種混雜，CSS `background-size: 100% 100%` 全部**非等比壓進 150×120（5:4）的 `.dog-skin` 盒**（正方形→1.25:1＝全體壓扁）＋狗 bbox 佔比 27%~47% 天差地別＝每次動作切換狗的視覺尺寸跳 30-40%。**修法（資產層正規化、CSS 零改）**＝`_dogsize_fix.py` 一條龍（源＝`_bw_orig/` 133.2 備份原件）：(a) 四角 flood 去背重做（TOL=40＋角多數決＋1px 羽化＝133.2 同參數）；(b) 畫布**統一 500×400（5:4）**＝`100% 100%` 從此比例保真不變形；(c) 狗 union-bbox **長邊等化＝畫布寬 78%**（390px，對齊 sheet 系跨支中位——躺/睡/丟等矮姿勢以面積尺度對齊而非硬拉高）；(d) 水平置中＋底線 96.7%（=sheet 系 y=116/120 地面線）；(e) GIF **支級共同 scale+offset**（幀間相對位移保真）＋duration 保留；**教訓**＝第一遍直接 `ADAPTIVE` 量化把 GIF 透明靜默丟光（frame0 transparent 0%）→ 正解＝`alpha≤128 → index 255` mask-paste P 幀＋`transparency=255, disposal=2`（驗證：五張 GIF 四角 alpha=0 透明 54-68%）；(f) **冪等閘**（已是 500×400＋長邊 66-88%＝skip——防重跑越縮越小）。**URL bust `?v=13320`→`?v=13330` ×8**（僅 `_bw` 8 條；roll sheet 內容未動留 `13320`=1 條）。**同回合追加（user: 「玩球的圖 裡面的狗變小了，圖要放大」）**＝`dog_ball_sheet.png` **支級放大 1.115×**（量測：全 6 cell 長邊 64-74%＝全系最小 vs 家族 74-97%）——撲跳幀（h=104）**剛好撐滿 116px 地面線**、其餘幀等比放大＋**跳躍弧度同步放大**（bottom 位移×scale）、x 中心不動（原地撲球）、畫布 900×120 與 150px 格**零改動**＝CSS steps(5)/size 不碰；bust `dog_ball_sheet 13200→13330`（13330 ×9）。tests **17 passed**（＋`test_ball_sheet_dog_enlarged` 6-cell 長邊 0.68-0.90 守衛）。pyproject 0.9.133.3 > **0.9.133.4**；iPhone 頁面重整生效（資產 `?v=13330`）。

### v0.9.133.3  

**T+1 對話摘要沉澱（user 核准「②P1＋召回池整合」；來源 = `uploads/uniichat_memory_report.md` 的 UniiChat 記憶樹研究 + §6 定案修正版）。** **新模組 `src/agent/session_compactor.py`**（359 行）：每日批次把「回溯視窗內 mtime 有更新」的 chat session 檔逐檔 LLM 壓成 1-3 行 → `MemoryStore.remember(kind='session-summary', source=sid, session_id=sid)` 沉澱進 memory_docs（**session_id 反向連結**＝摘要可召回、原文走 get_chat_session 追查——兩層式記憶，報告 §5/§6.3 的 80% 版；硬套二元樹逐對合併不採，報告 P5 同判）。**①契約**：`summarize_sessions(days, llm, dry_run, max_sessions)`——掃描排除機器前綴 `sched-/task-/webhook-`（chat-history-local 隱藏同款）、transcript user 600/asst 400/總 12000 cap、**冪等**（`recent(kind)` 收 source 集、精確 payload 比對 **零嵌入**）、寫入後 `_readback` 精確回讀驗證（**§6.3 失敗即中止**＝`stopped_early` 誠實回報）、per-session 失敗隔離、永不 raise。**②LLM 呼叫**＝`Agent._summarize_history` 同款單發 `_create_validated`（system prompt 專用摘要員）；pinned 走 `Agent(task_llm=…)`（v0.9.110.0 fail-closed branch）。**③模型校正（報告 §6.2 修正）**：live `/api/tags` 實測本機只有 `qwen3-embedding:4b`＋`qwen2.5:7b`——報告建議的 `gpt-oss:20b` **未安裝** → 預設 pin ＝ `ollama:qwen2.5:7b`（CPU 推理、零 API 成本；要照報告用 20b 先 `ollama pull`）。**④召回池整合（方案②核心）**：`session-summary` 天然不在 `_memory_block` 的 `must_not`（只 `task-result` 維持 store-NOT-recall——v0.9.67.2/111.0 安全設計保留）＝**召回面零改動就成立**；prompt 的 LONG-TERM MEMORY 註記增補「每日對話摘要會 automatic 沉澱＋原文查詢指引」。**⑤暴露面**：`MemoryLocalHost` 第 7 工具 `summarize_sessions(days/llm/dry_run)`（in-process + stdio mirror `_DISPATCH` +1）；CLI `python -m src.agent.session_compactor [--days][--llm][--max][--dry-run]`；**boot-seed 排程**（gui/api.py `_ensure_session_summary_schedule`，`QDRANT_SESSION_SUMMARY_CRON` 非空才建「每日對話摘要沉澱（T+1）」、llm pin `QDRANT_SESSION_SUMMARY_LLM` 預設 `ollama:qwen2.5:7b`、同名冪等、全錯誤吞掉——v0.9.127.0 記憶維護同款）＋prewarm 掛點。**⑥ env 註冊 ×5**（`.env.example`）：
`QDRANT_SESSION_SUMMARY_SINK`（off＝全關）／`…_LLM`（模型 pin，預設
`ollama:qwen2.5:7b`；**`llamacpp:<alias>`＝直連本機 llama.cpp chat
server**）／`…_LLAMACPP_URL`（**本機 llama.cpp chat 專屬鍵**，預設
`http://127.0.0.1:8090/v1`——CPU-only qwen2.5-7b（ollama blob 直吃，
`C:\llama.cpp\start-llamacpp-chat-cpu.bat`／`--alias qwen2.5-7b-cpu`／
ctx 12288×1 slot，**不帶 -ngl/--device＝絕不碰 AMD**（那兩張卡上住著
8080 qwen3-4b／8081 gemma2 兩台嵌入））／`…_OLLAMA_URL`（本機 ollama
專屬鍵——`OLLAMA_BASE_URL` 是聊天/視覺鏈的雲端端點，動它會拆掉雲端
模型；**live-404 教訓**＝本地模型名打雲端必 404）/`…_CRON`（boot-seed
進入點）。tests：**新
`test_session_compactor.py` 29 passed**（掃描×3/transcript×3/主體×8
含冪等與 dry-run/seam×1/host×5/stdio×2/boot-seed×5/prewarm order×1/
agent prompt×1/main×1）＋`test_memory_store.py` 工具面守衛更新＋回歸
（memory_store/memory_usage_tracking）＝合跑 **82 passed**；**live
E2E**＝`python -m src.agent.session_compactor --dry-run --max 1` 對真
session 檔：掃描到 `81a12e4bfd13`、本機 `qwen2.5:7b` 產出真摘要
（前兩發 Ollama-雲端 404 → ollama pin 直連本機後 `ok:true`／EXITCODE=0）。
pyproject 0.9.133.2 > **0.9.133.3**。

### v0.9.133.2  

**打滾狂閃動修復（iPhone Web App）＋8 張 AI 資產真去背＋`tools/project_backup.py` 重建收案（user: 「跑到邊的原地打轉在 iPhone 的 Web App 出現狂閃動，有些圖沒有去背」）。** ①**打滾 CSS 兩個閃爍根因**：(a) sheet 5 格×150px，`steps(8)`＝每步 75px 落在兩格中點＝**半格撕裂**；(b) 雙段 keyframes `0→-600→0` 後半段＝**整段倒帶重播**（視覺快速來回跳）→ 改**單向前進 `steps(4)`**：`@keyframes dog-roll-play { to { background-position: -600px 0; } }`（.9s 不變、`rollbreak` 900ms 同步）＝起滾→肚朝天→滾→**終格即翻正腳著地**（roll sheet 幀序 [0,1,2,3,3]，末格＝落地姿，單向播完收尾無需倒帶）。②**GPU 合成層**：`#dog-run-layer .dog-skin` 加 `transform: translateZ(0)`＋`backface-visibility: hidden`＋`will-change: background-position`——iOS Safari 的 `background-position`（CPU 重繪）與移動層混渲染＝整層狂閃。③**8 張 AI 資產真去背**（PIL flood-fill：PNG 單框、GIF 逐框保留 duration；邊緣侵入 TOL=40＋角落色多數決＋1px 羽化；原檔全無 alpha＝mode RGB/P、corner 白 or 淺藍）→ 全部真透明（審計：corners alpha=0、transparent_px 65k–873k）；原圖備份 `gui/assets/_bw_orig/`。**URL bust `?v=13310`→`?v=13320`** ×9（圖檔內容已覆寫，不 bust 會載到舊快取）。④**`tools/project_backup.py` 收案**：重建完整 414 行（`sha256_file/_excluded/zip_tree/_upload_root/collect_upload_files/qdrant_url/qdrant_snapshot/_git_commit/_multipart/qdrant_restore_snapshot/do_backup/do_restore/do_list/main`），live 驗證 backup（499 專案檔＋11 uploads）→ `list`（MANIFEST sections 計數）→ `restore --dry-run`（zip-slip 守衛＋不落地）round-trip 全綠。tests：`test_dog_runner.py` bust 守衛更新（`13310==0`＋`13320>=9`）＋sleep PNG assert。pyproject 0.9.133.1 > **0.9.133.2**；iPhone 頁面重整生效（資產 `?v=13320` 強制重抓）。

### v0.9.133.1  

**小狗狀態機資產更新＋打滾翻正修正（user 提供 8 張 AI 產圖：開心/發呆/累了/被抓起/睡覺/頭暈/空中飛/打哈欠）。** ①**打滾 180° 翻正**：`dog-roll-play` 改雙段 keyframes（0→-600 肚朝天→滾回 0 腳著地，.7s→.9s steps(8)）＝不再停在肚朝天乾等；app.js `rollbreak` 700→900ms、FLOURISH rolling 700→900 同步。②**8 張新資產落位**（`C:\Users\humphery\images\` → `gui/assets/`）：`dog_happy_bw.png`（開心蹦跳）、`dog_lick_bw.gif`（發呆舔）、`dog_lie_bw.gif`（累了趴）、`dog_lift_bw.png`（被抓起定格＋sway）、`dog_sleep_bw.png`（睡覺定格）、`dog_dizzy_bw.gif`（頭暈轉圈）、`dog_throw_bw.gif`（空中飛）、`dog_yawn_bw.gif`（打哈欠）——GIF 直掛 `background`（`animation: none`，GIF 自帶動畫）、PNG 定格；`dog-licking/lying/sleeping/yawning/throwing/carried` 六皮膚 CSS 全換；新皮膚 `.dog-dizzy`（頭暈）＋`.dog-happy`（開心＋`dog-happy-bounce` 上下跳）。③**接線**：`SKIN_CLASSES` +2（`dog-dizzy`/`dog-happy`）；`_dogDizzy()` 頭暈皮膚換 `dog-dizzy` GIF（wobble 只留位移、GIF 自帶轉圈）；`onTap` comfort 分支 `dog-belling`→`dog-happy`（被安慰＝開心蹦跳 1.4s→settle）。URL bust `?v=13310`。tests：`test_dog_runner.py` 4 過時守衛更新（flourish 900／carry `0 0`／bust 雙版本≥14+≥8／sleep 靜態 PNG 斷言）＋dizzy/comfort 新皮膚斷言＝**15 passed**；node --check OK。pyproject 0.9.133.0 > **0.9.133.1**；頁面重整生效。

### v0.9.133.0  

**歷史氣泡可調顏色＋透明度（user: 「歷史文字框 提供可調 顏色和透明度」）。** 工具列新 **🎨** 鈕 → `#chat-style-panel` 面板：**助理氣泡／user 氣泡各一組「顏色 picker＋透明度滑桿（30–100%）」**＋**恢復預設**鈕。**技術**：①`.chat-msg.user/.assistant` 的背景從寫死 `var(--primary)/var(--bg)` 改為消費 **CSS 自訂屬性 `--bub-user-bg/--bub-user-fg/--bub-asst-bg/--bub-asst-border`**（定義在 `#chat-log`，預設值＝原主题 var）——既有氣泡**即時重上色**（不用重渲染）、新氣泡自動繼承；②app.js `_hexToRgba`（吃 `#rrggbb` 與 computed `rgb(a)` 字串）、`_load/saveBubbleStyle`（**localStorage `chat-bubble-style`** ＝ `{asst:{c,a},user:{c,a}}`）、`_applyBubbleStyle`（有設定的 role 設 var＝rgba；assistant 同步把 `--bub-asst-border` 設成同色同透明＝邊框跟著溶入；未設定＝removeProperty 回主題預設）；③面板開啟時 `syncInputs`：有存值→正規化回 `#rrggbb`；沒有→**從 computed `--bg/--primary` 取預設**；任一 input `input` 事件→即存即套；**Reset**→removeItem＋removeProperty＋重填。`bindBubbleStyle()`（`_done` 冪等、bindEvents 早綁群組）。i18n ×4×4 keys（styleBtn/styleAsst/styleUser/styleReset）。tests：新 `tests/test_bubble_style_ui.py` ×5（html ids+順序錨點／CSS vars+panel+舊寫死背景反守衛／app.js 邏輯鏈／i18n ×4／node --check）＝**5 passed**；node --check OK。pyproject 0.9.132.9 > **0.9.133.0**；頁面重整生效。

### v0.9.132.9  

**剪貼簿按鈕三處上線（user: 「web ui 文件框提供剪貼簿功能，對話框提供複製和貼上，歷史文字框提供複製」＝行動鍵盤/遠端桌面選字很難受的全按鈕化）。** ①**筆記本文件框（`#ne-body`）**：textarea 下方新 `.ne-clip-row` 三鈕——📋 複製／✂️ 剪下／📥 貼上；複製/剪無選取時**整份內容**為對象；剪下移除所選並 `dispatchEvent('input')`（觸發離線暫存/autoSave 鏈）、貼上插在游標處（取代所選）＋同樣派 input。②**對話輸入框**：工具列 ⚡ 後新 📋（複製整份草稿）／📥（**貼上剪貼簿文字到游標位置**——取代所選）。③**對話歷史氣泡**：assistant **與 user** 氣泡都有 📋 複製鈕（`_bubbleCopyBtn`，同 🔊/📤 footprint 的 `.btn-msg-copy` CSS）。**技術**：`_clipWrite`＝`navigator.clipboard.writeText` → 失敗退**隱藏 textarea + `execCommand('copy')`**（非安全環境/WebView 怪癖）；`_clipRead`＝`navigator.clipboard.readText` 只此一路（讀取無安全 fallback）→ 失敗誠實 toast「請改用鍵盤貼上」；`bindClipboard()`（null-guarded + `_done` 冪等，bindEvents 早綁群組第 4 位）。i18n ×4×10 keys（clipCopyMsg/clipCopyInput/clipPasteInput/clipCopySel/clipCutSel/clipPasteSel/clipCopied/clipPasted/clipPasteFail/clipEmpty）。tests：新 `tests/test_clipboard_ui.py` ×5（index 五鈕+順序錨點／app.js helpers+wiring+input 派發／i18n ×4×10／CSS blocks／node --check）＝**5 passed**＋既 web_upload/notes_ui 契約不破（_run_jsdom 型 jsdom 未動）；node --check OK。pyproject 0.9.132.8 > **0.9.132.9**；頁面重整生效。

### v0.9.132.8  

**小狗狀態機三連發（user: 「增加 狗 狀態機：狗快到視窗邊是打滾(轉頭)；狗來回跑太多次要休息(停下來、喝水、睡覺(睡在視窗中間切底線))；LLM 回合數太多會頭暈要安慰」）。** ①**衝到視窗邊＝打滾轉頭**：`moveBurst` 步進偵測 clamp（＝撞邊）→ `dog-rolling` 打滾 .7s＋`dir=-dir` 轉頭→滾完回該爆發的移動皮膚反向繼續（`rolling` 防重入；`S.st !== kind` 守衛防爆發已被取代）；進場衝中點不撞邊不受影響。②**跑六腿 → 喝水休息**：自由跑爆發結束 `S.legs>=6` → 歸零 → `drinkAndSleep()`＝停、**`dog-licking` 舔水代班喝水**（未提供喝水 GIF）1.2s → **`moveBurst('walk', …, 'center')`（towardCenter='center'＝自動方向走向中間槽位，過中點或 5s cap）** → 貼底線趴下（💤）→ 4s 後 `enterSleep()`＝**深睡在視窗中間貼底線**；點醒＝伸懶腰→呵欠→坐（腿數觸發時已歸零＝休息完重算）。③**LLM 回合數太多 → 頭暈要安慰**：`pollTurnStream` 計數 `llm_round` 事件（`llmRounds===8` 即 `_DIZZY_AT`）→ `_dogDizzy()` → `layer._dogDizzyHook`（不從 carried/sleep/throw/lift/putdown/drink/comfort 中硬拉出）→ 頭暈態：原地轉圈＋spin/tilt 交替搖頭晃腦小碎步（300ms wobble）＋**15s 沒人安慰自動 settle**；**點頭暈的狗＝安慰**：`comfort`（`dog-belling` 躺下被撫摸 1.4s）→ settle。towardCenter 參數擴為 `true | 'center' | undefined` 三態（進場語意不變，`test_dog_entrance_center` 契約不破）。tests：`test_dog_runner.py`＋`test_rolls_rest_and_dizzy_wiring`（~20 守衛：rolling/legs/drinkAndSleep/'center'/dizzy hook/comfort/wobble/auto-recovery/llmRounds 計數）;**全部 dog_runner 套件逐條跑綠**（source 8＋jsdom 9；fling 8.1s／sleep 15.6s 等）；node --check OK。pyproject 0.9.132.7 > **0.9.132.8**；頁面重整生效。

### v0.9.132.7  

**出廠方向＝往中間跑（user: 「注意 狗 出廠的方向，要向中間跑」）。** 原本出生（CSS `left: -160px` 螢幕外）後進場方向**隨機**——一半機率朝左繼續跑出螢幕＝看起來像沒跑/直接消失。**修**：①`moveBurst(kind, next, towardCenter)` 新三參：`towardCenter=true` 時**強制 dir=+1** 且步進條件改為「**x < 螢幕中心槽位（clamp 後）且爆發 ≤5s 就繼續跑**」——從左螢幕外一路衝過中點才結束；非進場爆發維持隨機方向（正常時長）。②`openWalk()` 進場改為 `moveBurst('run', …, true)`＝**出生固定奔跑進場**（不再 50% 散步開場），過中點後交還既有流程（50% walk→run / 直接 run→隨機漫遊、花式、12s cap 坐下）。③**emoji fallback 也治了**：`openIdle()` 原本讓🐕坐在 `left:-160px` **螢幕外＝永遠看不到**——改為先搬去中間槽位再 settle。tests：新 `tests/test_dog_entrance_center.py`（源碼守衛×6：towardCenter 三參/強制 dir/過中點條件/openWalk 用 run+true/openIdle 置中）＋回歸 dog_runner source 7＋jsdom（entrance idempotent/hearts/streak/fling 鏈）＝**12 passed**；node --check OK。pyproject 0.9.132.6 > **0.9.132.7**；頁面重整生效。

### v0.9.132.6  

**index 快取根治＋/playpet 實證正常（user: 「使用 /playpet 狗不會跑」）。** **現場實證**（真 Firefox 開 https://tchtool.app:8334/＋送出鈕送 /playpet）：dog layer **有出生**——兩次 DOM 抓拍：`skin=dog-walking, left:1326px`（跑動中）→ 數秒後 `dog-stopped`＋**`skin=dog-belling`（132.5 六挑 idle 輪替真實生效）**＝**狗會跑、狀態機活的**。**真正根因（兩層）**：①**回覆落地會收狗**（設計如此）——任何回合的回覆落地／背景任務送出都 `_dogRunStop()`；你的 10:27–11:13 連續 OCR 回合落地窗口內按 /playpet＝狗一出生就被下一個回合收走。②**「永遠拿不到新版 JS」的舊傷補完**：長壽分頁的記憶體裡停著**昨天載入的 app.js?v=0.9.132.3**（實抓 DOM script tag 為證）——`/` 的 index 是 no-cache+ETag，但**長壽分頁根本不重新驗證**，asset 版本鍵永遠到不了瀏覽器。**修**：`/`、`/index.html`、`/pywebview-shim.js` 改 `Cache-Control: no-store`（無 ETag/304——每次導航必拿新 index→新 asset URL；0.9.97.1 教訓的一半補完）；資產（app.js/style.css/sheets）維持 no-cache+ETag（內容大、重複下載不划算＋版本鍵已保護）。tests：`test_web_server.py`＋`test_index_and_shim_are_no_store`（3 條路徑 no-store/pragma/無 etag）＝**60 passed**。**使用者動作**：**Ctrl+F5 硬重新整理一次**（之後每次導航自動拿最新）。**附帶**：11:13 OCR 回合「清理了所有 llama-server 程序」把 4B/gemma2 嵌入伺服器也殺了——**看門狗已自癒**（新 PID 重生，name-pin bat 生效）；嵌入功能正常。pyproject 0.9.132.5 > **0.9.132.6**；RESTART web（或下次導航 no-store 生效前先手動刷新）。

### v0.9.132.5  

**小狗最後五套行為動畫全部接線（user: 「全部」＝接回 ①拖拽提起＋甩飛 ②深睡鎖定＋喚醒鏈 ③tap 情緒）。** 庫裡躺著五套未接線的 sheet（`sleep`/`stretch`/`belly`/`tilt`/`throw`）全數點燃，並修正三個真 bug：①**fling 速度判定**改用「**最後兩個 pointermove 樣本的差向量**」（原 131 用放開點對最後 move——沒帶座標的 tap 放開會幻算出反向大速度）；**樣本追蹤收斂到單一 listener**（`onDragTrack` 恆掛 document；原 131 雙 listener 互相覆寫 prev/last＝prev==last→vx 恆 0；同毫秒樣本 ＝瞬時速度 ÷max(dT,1)）；②**抓起即停步進鏈**（drag-grab 時 `clearT('move')/('flourish')`——從 run 中抓起時殘留 move timer 會拉著提著的狗偷走）；③**睡眠 sheet 由 infinite 改 `1 forwards`**（`dog-sleep-play 1.6s steps(5) 1 forwards`——入睡動畫播完凍結在 Zzz 幀，不再循環重播趴下）。**①拖拽/甩飛**（131 版復原＋pointer 化）：`pointerdown`（狗）記 `dragFrom`；document `pointermove`（**單一樣本追蹤器**）偵測 dx+dy>14 → 停 burst＋`enterCarry()`（提著跟游標）；document `pointerup`＝`onUpRelease`（**提著中的放開唯一由它裁決**：`dtUp<160ms` 且最後兩 move 速度 |vx|>1.1 → `throwFly(vx)`＝sin 弧 110px/14 步/40ms、dist=min(max(|vx|×260,200),w×0.55)→落地 putdown→run chase→ball 1300→settle；否則 doPutdown）；`onTap` 的 carried 分支改為**純回退**（放開裁定權交 onUpRelease；舊版 tap 直接跳 doPutdown 會殺死 fling）；`_dogRunStop` 清 `_dogDragMove/_dogDragUp` 兩 listeners。**②深睡**：`settle()` 新掛 `T('sleep', enterSleep, SLEEP_GATE())`（**連續靜置 2 分鐘**＝120s gate；每輪 settle 重掛；`enterSleep` guard sit/lie＝只在休息中深睡）→ `clearAllT()` 鎖死（沒有任何自動喚醒）＋`dog-sleeping` 播完凍結 Zzz＋💤 粒子；點睡著的狗＝`wakeUp()`：`stretch`（凍結幀 .7s）→`yawn`（.9s）→`settle()` 喚醒鏈；睡中 tap 不走 pet 分支。**③tap 情緒（非干擾接法）**：idle 輪替池 4→**6 挑**（＋`tilting`/`belling`——躺討摸/歪頭進坐姿輪替）＋**bone→lick 吃飽鏈**（挑中 boning→950ms 後接 licking）；寵摸互動維持 hearts＋streak 直覺（不插一次性情緒免得 flake 掉 3/6 摸測試）。**jsdom driver 診斷修**：`main().finally` 後 **250ms 強制 `process.exit(0)`**——修掉「狗的 2 分鐘 sleep gate timer 把事件迴圈釘住＝每條 jsdom 測試都掛滿 30 秒硬頂」的慢病（console.log 同步 flush、強制 exit 同時跳過 Node 24 teardown 0xC00000FD）＝jsdom 測試從 ~30s 降到 1–16s。**tests**：`test_dog_runner.py`＋3（source：`test_deep_sleep_drag_fling_wiring` ×~20 斷言——enterSleep/wakeUp/SLEEP_GATE/throwFly arc/drag plumbing/stop 清單/CSS forwards；jsdom：`test_fling_throw_land_chase_ball`（**8.1s**——抓取即停/騎移/放開 fling/弧線/落地/追/球/坐下）＋`test_deep_sleep_locked_wake_chain`（**15.6s**——`_SLEEP_GATE_MS=1200` 縮閘→睡→點→stretch→yawn→sit）；既有 idle-pool 契約更新為六挑＋food 鏈；docstring 同步。**全部 jsdom 逐條實跑**（idempotent/hearts/streak 5.98s／pickup 17.7s 首跑修 fling 後綠／bored 綠／fling 8.1s／sleep 15.6s）＋source 7＋node --check OK。pyproject 0.9.132.4 > **0.9.132.5**；頁面重整（web）/重啟 GUI 生效。

### v0.9.132.4  

**嵌入伺服器搬家：4B/gemma2 從 Intel iGPU 移回 RX 5700 XT（user: 「qwen3 embed 4b 為何跑在 intel gpu，而不是 amd gpu?」＋「現在本專案正在ingress 檔案，可以先暫停改 amd 再執行 ingress嗎」）。** **診斷鏈（現場實證）**：①`--device Vulkan0` 是**列舉槽位別名**（「本次開機第 0 顆 Vulkan 裝置」）而非硬體身分證——2026-10-14 07:03 開機時 Iris Xe 被列舉在 0 號 → 4B（pid 17332，WDDM 本 4.84GB **local/shared 同額＝UMA 整合顯卡帳目特徵**）與 gemma2（pid 472，1.31GB）整批住進 Intel iGPU 的系統 RAM，AMD dedicated 只有桌面殘量 183MB；②ingest 期間單發 4-token 探針 **26.5–30s**（iGPU 吃下 750-token 批次根本排不動）→ local 4B 連續 120s ReadTimeout → embedder 300s cooldown → **fallback OpenRouter/DeepInfra**（同 qwen3-embedding-4b 模型語意一致、2560d 對齊，無 hash 污染）；③telegram 背景任務 `19e7e077`「匯入qdrant」15:18 開跑、17:18 被 **turn 硬超時（1800s）** 收掉＝ingest 自然停下。**搬遷（已執行）**：三支 bat（`C:\llama.cpp-vulkan\start-llamacpp-4b.bat`／`start-llamacpp-8b.bat`／`C:\llama.cpp-un\start-llamacpp-gemma2.bat`）改為**按名字解析**——`for /f … in ('llama-server.exe --list-devices ^| findstr /i "Radeon"') do set DEVICE=…`（取 Radeon 那列的 `VulkanN`；看不見 AMD → 退回 `Vulkan0` 誠實 fallback），槽位翻轉再也帶不走；kill pid 17332/472 → detached 重啟兩 bat。**驗證**：AMD LUID（`0x0001234b`）dedicated **183MB → 4.98GB**（4B＋mmproj＋gemma2 全在 VRAM）、iGPU 8GB shared → 2.6GB；單發探針 **26,500ms → 38ms**（batch 後回 131ms）、gemma2 185ms/768d；`/v1/models` 兩端點 dim 2560/768 正常。**補查（Intel 為何被 llama.cpp 略過）**：`GGML_VK_VISIBLE_DEVICES=0,1` 強制展開 → llama.cpp 實列 `Vulkan0: Intel Iris Xe (16170MiB) / Vulkan1: AMD`＝**Vulkan 支援 Intel、Iris Xe 的 igvk64 ICD 在 DriverStore 裡活著**；預設不列＝**build 有 dGPU 存在時自動濾掉 iGPU**。⇒ 10/14 之謎的真實機制＝**當次開機 AMD 曾短暫不在 Vulkan 列舉**（RDNA1 device-fault 前科）→ 無 dGPU 可濾 → **iGPU 升格 Vulkan0** → `--device Vulkan0` 綁到 Iris Xe（16GB shared＝系統 RAM 通路）。兩因素疊加：槽位別名＋dGPU 消失瞬間；name-pin 修法同時擋掉兩者。**重跑 ingest**：內容 hash 去重保護下，telegram 重送「匯入qdrant」即可續跑（已完成檔案零成本、雲端已嵌點不同 cache key 會重嵌一次但不重複）。pyproject 0.9.132.3 > **0.9.132.4**（ops-level：bat 與本記錄，無 repo 程式碼變更）。

### v0.9.132.3  

**背景任務結果寫回原對話 session（user: 「每一個新的 session 都會自動存儲不需手動按儲存，從前端進入背景執行的工作需要在完成的時候也要將結果寫回 session」）。** 現況缺口：☑ 背景執行送出的任務結果只落在 `task-<id>` session＋⏳ 面板，原對話永遠只看得到 `taskSubmitted` 提示。**設計取向＝前端單一寫入者**（chat session 檔由前端整檔儲存；server 端直接 append 會與前端的下一次 `autoSaveChat` 整檔覆寫互相吞掉）——寫回走前端既有的自動存檔鏈，零檔案競態。**後端**：①`task_runner` 記錄新欄位 `origin_sid`＋`set_origin(task_id, origin_sid)`（never raises）；②`TaskLocalHost._run_once` 接受 `origin_sid`（LLM 端 `run_task_once` 不受影響）；③`Api.submit_background_task` 新參數 `chat_sid` → args `origin_sid`。**前端 `app.js`**：①送出時帶入 `(chatSession && chatSession.id) || null`＋`watchBgTask(task_id)` 註冊追蹤；②`bgWatch`（Map task_id→misses）＋8 秒輪詢 `bgWatchTick`：`list_background_tasks` 看到終態→`get_background_task(tid, 4000)` 取回 `answer_preview`→**append assistant 泡泡＋`msg.taskId` 標記＋toast `bgResultBack`＋`autoSaveChat()`**；failed → error 泡泡（registry error 文案）；registry 未知 id 容忍 5 次後丟棄；watch 清空即停錶；③**對帳 `reconcileBgTasks()`** 掛在全部四個 session 綁定點（applyRestore 草稿復原／newSession／loadSessionById／saveSession 首建）：凡 `origin_sid === 目前 session` 的任務（**running 或已終態**）都進 watch——頁面重載、頁面關掉再開、換 session 再回來都能補寫；`msg.taskId` 去重保證冪等（已寫回過的永不重複）。i18n `bgResultBack` ×4。tests：`test_gui_task_panel.py` 新 `TestBgResultWriteback` ×6（registry origin 欄位/set_origin、Api chat_sid 轉發、`_run_once` 行為級 origin 記錄＋終態、前端 watch 機制守衛、四掛點計數、i18n ×4）＋既有 args-dict 契約更新（+`origin_sid: None`）＝相關合跑 **155 passed**（gui_task_panel+task_runner+notify 71／task_stop+turn_bg_detach 25／web_server 59）；node --check OK。pyproject 0.9.132.2 > **0.9.132.3**；RESTART（GUI/web）required。

### v0.9.132.2  

**stop 鏈泄漏修復——升格任務被 🛑 後每個新回合都被誤殺（user: 「一直出現 06:42 …🛑 (已強制中止) 使用者要求停止」＝同一則訊息反覆送、每回都立刻收到 TURN_STOPPED_TEXT）。** **根因（v0.9.132.1 引入的生產 bug）**：`detach_turn_to_background` 把 registry stop flag 串進**共用主 Agent 的 `_stop_check` slot**（`set_stop_check(_chained)`）且**永不拆除**；而 registry stop flag **刻意不隨 end_task 清除**（v0.9.120.0 殭屍可達設計）→ 只要任何一個被升格的任務被按過 🛑（⏳ 面板/`stop_task`），那個**永久留存的閉包**就讓主 Agent 之後**每一個新回合**在第一個 tool-loop 邊界被誤判「使用者要求停止」→ 原樣回「🛑 (已強制中止)…」。**修**：①拆除 `_stop_check` 鏈接——共用 slot **永不被觸碰**（`detach` 源碼守衛：`_stop_check` 只准出現在註解行）；②改用**每回合專屬自熄看門線程** `turn-bg-watch-<turn_id>`（0.5s 輪詢）：偵測到該任務 stop flag → 對**這個回合自己的** turn-scoped stop 事件 `signal_turn_stop(turn_id)` 發訊（只影響本 thread 的回合，與 ⚙ 面板 🛑 同一道）→ 回合終態（snapshot ≠ running）即自行結束——⏳ 面板 🛑 對升格回合照樣有效、零共享狀態污染。tests：`test_task_stop_bridges_to_turn_scoped_stop`（request_stop → 看門線程在時限內觸發 turn-scoped stop＋`_stop_check` 恆 None 斷言）+ `test_detach_source_never_touches_shared_stop_slot`（源碼守衛）＝相關 33 passed。pyproject 0.9.132.1 > **0.9.132.2**；**RESTART required（重啟同時清掉在跑進程裡已被污染的 `_stop_check`）**。

### v0.9.132.1  

**長回合自動轉背景（user: 「長程作業會lock，像現在」）。** 診斷：兩段式回合的送出鈕自 send 起鎖到**真終態**——v0.9.124.8 只修了「按 🛑 後 20s」的耐心解鎖，沒按中止的長回合（LLM 呼叫 93–377s 級＋工具迴圈十幾分鐘）整程鎖死＝體感的 lock。**修＝前景回合超時自動升格背景任務**：①後端 `Api.detach_turn_to_background(turn_id)`——驗證回合仍 running → `task_runner.begin_task("chat", …)` 註冊進背景任務登記表（sid=`task-<id>`；**冪等**：重複呼叫回同一 task_id；終態/未知回合誠實拒絕）＋**stop 鏈**：registry stop flag 併入 agent 的 `_stop_check`（⏳ 任務面板的 🛑 也停得到這個回合；turn-scoped stop 事件不受影響）；`_work` 終態新增 `_finalize_detached_turn`＝回合結果寫進 `task-<id>` session（user+assistant／[error]）＋`end_task(...)`（自動觸發既有 terminal notify；**執行中的工作絕不殺、絕不重做**）。②前端 `pollTurnStream` 新 **60 秒門檻**（`_TURN_BG_AFTER_MS`）：running 超過門檻→呼 detach→成功＝**同一句 `taskSubmitted` 泡泡**（帶 task_id/sid）＋`turnBgDetached` 面板狀態＋解鎖輸入＋刷新 ⏳ 面板；**輪詢不中斷**——真終態照常落 assistant 泡泡（chat 與 task session 雙記錄）；detach 失敗＝不重試、照舊等終態。③**併發 UI 順手修**：`appendChatMsg` 觸發的 `renderChat()` 全量重建會把進行中回合的 ⚙ 執行 log 面板（含 🛑 中止鈕）整個抹掉＝新 `liveExecPanels` Set＋renderChat 尾端 re-mount 非終態面板＋poller 三個出口（終態＋兩個 gone 分支）標記 `panel.dataset.terminal`。i18n `turnBgDetached` ×4。tests：新 `tests/test_turn_bg_detach.py` ×10（行為級：running detach 冪等＋finalize 寫 session+registry done/failed、terminal 拒絕、stop 鏈、never-detached no-op、session save 失敗不毀 bookkeeping＋前端守衛 ×4）＋既有守衛窗口放寬（`test_turn_panel` 6000→8000 ×2、`test_gui_task_panel` 4000→8000——v0.9.127.x wrapper 重構的既有漂移）＝相關合跑全綠；node --check OK。pyproject 0.9.132.0 > **0.9.132.1**；RESTART（GUI/web）required。

### v0.9.131.0  

**小狗行為狀態機重建（user 提供 20 支 `std_*.gif`——`C:\Users\humphery\images\` 各 2 影格 512×512 **彩色紙感底**、非白底）。** ①**精靈庫重建**＝新 `_build_dog4_sheets.py`：新 `remove_background_std` **邊界區域生長泛洪**（相鄰色差 ≤TOL=32 的區域生長——吃得下漸層彩底與白雲、卡通黑輪廓線擋得住＝白底泛洪教訓（127.24）的彩色紙感版）＋**金字塔播種**（1/4→1/2→全解析度逐級向量化；純 BFS 30s 逾時已棄）→ 20 張 sheet（CELL 150×120 慣例不變）；**朝向定案**——多數原生朝左需鏡射、spin/lift/tilt/tail 不鏡射；**壞幀修序** bite/bone→[0,0]、lie→[1,1]、walk→[0,0] fit；**putdown 整支換 std_tilt f1**（std_putdown f1 白色碎片不可用＝現行 putdown 幀＝std_tilt f1 坐姿，兼作 idle hub 凍結幀）；belly/hop 藍殘塊屬可接受妝瑕。②**app.js 18 行為狀態機**（移動 run/walk/hop/spin｜互動 lift/putdown/throw/belly/ball/bone｜情緒 bark/bite/lick/yawn/tilt｜休息 sleep/lie/roll/tail/stretch＋**IDLE 中樞**（`.dog-stopped` 凍結 putdown f1 坐姿）＋**閒置階梯**（30s 情緒 bone→lick 吃飽鏈／2min lie／5min **鎖定 sleep**）＋**喚醒鏈**（sleep 點擊僅 wakeUp：stretch→yawn→idle））：tap=隨機情緒＋❤️ 粒子；3 摸/4s=happy（run/hop/spin/tail 四選一）；**6 摸/4s=angry**（bark 1300→bite 750→soothe 3000→belly 1700→idle；累積計數 `S.petTotal`——`S.pets` 三摸歸零設計下 `petTotal` 累積才到得了 6）；**拖拽提起**（>14px=carry：mousemove `left=clamp(clientX−75)`/`bottom=clamp(h−clientY−60)` 雙軸置中跟隨）；放開依最後移速 dt<160ms 且 |vx|>1.1=**throwFly**（sin 弧 110px、dist=min(max(|vx|×260,200), w×0.55)、14 步 40ms、落地→putdown 幀→run chase→ball）否則 putdown；計時器全收斂 `layer._dogT`（keyed `T/clearT/clearAllT`）；`_dogRunStop` 清 timers＋carry/drag listeners＋opacity 320ms 淡出 remove；sleep 中 onDown 只 wakeUp；burst 中 onDown `clearT('move')` 可中斷。**sprite 探針移到 `_dogRunStart` 尾端**（所有 const 初始化之後——同步觸發的 onload 不再撞 TDZ，jsdom 同步 Image stub 實證）。③**CSS dog 區全段換新**：20 行為 class＋idle hub＋`dog-cellab` 雙幀 toggle＋carry sway＋particle＋reduced-motion＋`.dog-runner` 補 `pointer-events:auto`＋20 條 URL `?v=13100` bust（app.js `const V` 同步）。④**tests**：`test_dog_runner.py` 全面重寫＝`TestDogRunnerSource` 6 守衛（20 sheet 存在＋4 舊 sheet 反守衛＋狀態機轉移＋CSS block＋URL bust＋sendChat 掛點）＋**jsdom 行為級 6 測試**（start 冪等+teardown／tap 情緒+粒子／三摸 happy／六摸 angry bark→bite→belly（**MutationObserver 記錄 class 變化**——jsdom 忙機 timer 漂移下時間取樣會漏 750ms bite 窗，事件記錄不受漂移影響）／拖拽 carry left=325px bottom=508px→putdown／甩飛 throw→run chase）＝**12 passed**；**`node --check` OK**。**生產 bug 順手修**：`dragFrom` 缺 `y` 欄位（dy=NaN→拖拽提起永不觸發）；angry 計數（見上）。pyproject 0.9.130.2 > **0.9.131.0**。  

### v0.9.130.0  

**小狗動作庫 13→16 套＋摸摸反應系統（user 提供 3 支新 GIF：`C:\Users\humphery\images\` 的 shih_lift_c3 / shih_putdown_c3 / shih_bite_c3——各 4 影格 512×512 不透明白底卡通西施**、逐格獨立生成再組 GIF、prompt 明確禁止棍棒與人手＋體型毛色鎖定＝畫面只有狗**）。** `_build_dog3_sheets.py` 新增三張 sheet（union 模式、慣例不變）：**lift** [0,1,2,3]（蹲→躍起→撲上→落地＝「被提起」的無手版表現，600×120）＋**putdown** [0,1,2,3]（降落→觸地→坐好＝「被放下」，600×120）＋**bite** [0,1,0,3]（瞪視→撲咬乒乓→嘟嘴生氣＝「要咬人」；驚訝 f2 排除，600×120）。**朝向查證（紅項圈質心法）**：以「項圈在脖子上＝臉那一側」程式化判定——方法先在既有 run/bark sheet（已知全朝右）與舊 GIF run/bark（已知原生朝左）雙向驗證成立，再判三支新 GIF＝**lift/putdown 原生朝右（不需鏡射——c3 系列與舊 13 支相反）、bite 原生朝左（需鏡射）**。**app.js 反應接線**：新 `playOneShot` 一次性反應引擎（清 idle/lying/walking/settling/lifting/putdown/biting 十類 class＋七項 timer 再加 class 播 720ms）＋三條行為鏈——①**摸摸坐姿小狗＝lift→putdown→回 idle 輪替**（「提起又放下」）②**三連摸興奮＝`_dogFollow` 立即啟動＋lift（skin 層）在追逐中同步播放→抵達落地 putdown→settle 坐下**（skin 與 runner 動畫獨立層故可並行；`petRounds` 計數器——**每逢第二次興奮 streak 小狗改為生氣咬人**不放跟隨）③**點醒睡著小狗＝bite（生氣）先咬一口→再喚回 idle 輪替**；反應播放中再點＝忽略（防重入）；`_dogRunStop` 清理清單擴十二項 timer（＋`_dogLiftTimer/_dogPutdownTimer/_dogBiteTimer`）、`_dogFollow` 清理清單擴七項（＋三反應 timer 防跨模式堆疊）。CSS +3 block（`.dog-lifting`／`.dog-putdown`／`.dog-biting` 各 600×120 0.72s steps(4) 1 forwards）＋16 條 URL `?v=13000` bust。tests：`test_game_action_set_v0129_0` 改名 `v0130_0` 全面擴充（16 sheet/16 class/16 URL＋反應鏈三條＋十二 timer＋playOneShot 斷言）＋jsdom 跟隨測試等待延長 2000ms（putdown 鏈先於 settle）。pyproject 0.9.129.0 > **0.9.130.0**。  

### v0.9.129.0  

**小狗動作庫 8→13 套＋行為模擬互動系統（user 提供 5 支新 GIF：`C:\Users\humphery\images\` 的 shih_ball_c / shih_bark_c / shih_lick_c / shih_lie_c / shih_spin_c——各 4 影格 512×512 不透明白底卡通西施）。** `_build_dog3_sheets.py` 新增五張 sheet（union 模式、慣例不變 CELL 150×120／內容高 104／底線 y=116；**bark f1「WOOF!」對話泡鏡射後文字翻轉不可用**＝幀序排除）：**ball** [0,1,2,3,2,1]（盯球→跑向球→跳接→叼回的完整撿球故事，900×120 乒乓）＋**bark** [0,2,3,2]（坐姿吠叫：張嘴/翹尾搖動/吐舌哈氣乒乓，600×120）＋**lick** [1,2,3,2]（坐姿舔嘴特寫三連——f0 坐得較遠排除，600×120）＋**lie** [0,1,2,3,3,3]（走進→趴下→頭低→入睡，末幀凍結在 Zzz 圖案，900×120）＋**spin** [0,1,2,3]（轉圈：f0 朝左/f3 朝右＝**180° 轉身本身就是敘事**、中間兩幀蜷曲不分左右，600×120）；union 分支加**寬度守衛**（lie 全組 union 寬 373px 縮放後 158>150 格寬——`scale = min(高度比, (CELL_W-4)/uw)` 保證不溢格）。**app.js 行為模擬互動系統**：①**摸摸**（`_dogParticles` emoji 粒子＋小狗 click 接線——單次點擊 ❤️💖✨ 心心爆發；睡著的狗被點＝`_dogIdleWake` 喚回輪替 idle）②**興奮跟隨**（`_dogFollow`：4s 內三連摸→放棄當前圈速、`dog-following` 移除 sprint 動畫（先快照 computed left 防瞬移）→mousemove 跟隨游標（120ms step、left transition .12s 線性、方向翻面）→抵達/游標靜默 2.4s/上限 6s→共用 settle 坐下）③**冷落**（idle 開始後 8-12s 無互動→`dog-lying` 趴睡 sheet 一次性播完＋💤 粒子＝被冷落的狗會睡著；摸摸喚醒）④**idle 四向輪替**（tongue/scratch/yawn/lick 每 4.5-6s 換手＋防同項連任）；**花式五選一**（`FLOURISHES` pool：jump 550ms／hop 550ms／**fetch(ball) 850ms**／**bark 760ms**／**spin 760ms**，同一次性契約、walk break 機率照舊）；`_dogRunStop` 清理清單擴九項 timer＋mousemove listener。CSS +7 block（`.dog-fetching` 900×120 0.85s steps(6) forwards／`.dog-barking` 600×120 0.76s steps(4) forwards／`.dog-spinning` 600×120 0.76s steps(4) forwards／`.dog-idle-lick` 600×120 1.1s steps(4) infinite／`.dog-lying` 900×120 1.2s steps(6) forwards／`.dog-following`（animation:none＋left .12s transition）／`.dog-particle` 上浮消散 keyframes）＋13 條 URL `?v=12900` bust。tests：`test_game_action_set_v0128_0` 改名 `v0129_0` 全面擴充（13 sheet/13 class/13 URL＋粒子＋跟隨＋冷落＋喚醒＋九 timer 項）＋jsdom 段起點改 `_dogParticles`（helper 納入 driver）＋**新 jsdom 行為級互動測試**（點擊→粒子出現；三連摸→following；mousemove→抵達後 dog-stopped 坐下；粒子錯開生成 80ms=1 顆/1400ms≥2 顆）＝**7 passed**、合跑 web_server **59 passed**。pyproject 0.9.128.1 > **0.9.129.0**。  

### v0.9.128.1  

**出場動作隨機化（user：「動作不要每次都從Run開始」）。** `_dogRunStart` 新 `openWalk()`——sprite 確認後 **50% 先散步**（`.dog-walking` 1.8-4.5s、慢步頻 1.2s steps(6)）再起跑、**50% 直接開跑**；probe.onload 與 `__dogSpriteOK` cache 命中兩路都接線。花式 timer 觸發時**清掉未完的開場散步**（`clearTimeout(layer._dogWalkEndTimer)`＋remove `.dog-walking`）防堆疊；`_dogRunStop` 本就清 `_dogWalkEndTimer` 免改；CSS 零改（walk block 128.0 已有）。tests：`test_game_action_set_v0128_0` +3 斷言（openWalk 定義／雙路呼叫／花式清散步）＝**6 passed**、合跑 web_server **65 passed**。pyproject 0.9.128.0 > **0.9.128.1**。  

### v0.9.128.0  

**卡通西施 8 動作全套（user 提供 4 支新 GIF：`C:\Users\humphery\images\` 的 shih_walk_c / shih_hop_c / shih_yawn_c / shih_tail_c——各 4 影格 512×512 白底、seed=42 同款扁平風＋粗描邊）。** `_build_dog3_sheets.py` 擴充＝GIFS 改 `(file, mirror)` 二元組＋`load_gif_frames(mirror=)`——**tail 不鏡射**（追尾巴各幀朝向橫跨側面/背面/後視，整組鏡射無意義；其餘 7 支照舊全鏡射朝右）；**8 張 sheet**＝run/jump/sit/tongue 維持原樣＋**scratch 換 tail 素材**（[0,1,2,3,1,2] union 站→彎→咬尾→站；bone 啃骨頭保留在 tongue）＋**walk** [1,2,1,2,1,2] fit 模式（f0/f3 直立看鏡頭捨棄、f1/f2 側面走姿面積等化）＋**hop** [0,0,2,3,3] union（蹲→蹲→起跳→高點，保留跳躍位移）＋**yawn** [0,1,2,3,2,1] union（大呵欠→瞇睡→清醒乒乓）。app.js 狀態機三改：①跑步花式 4-8s **roll/hop 隨機二選一**（`? 'dog-jumping' : 'dog-hopping'`，同 570ms 一次性契約）②花式後 **60% 走路小段**（1.2-2.7s 後 `.dog-walking` 2.5s、慢步頻 1.2s steps(6)）③停止後 idle **三選一**（tongue 啃骨／scratch 追尾／yawn 打呵欠）；`_dogRunStop` 清六 timer（+`_dogWalkTimer`/`_dogWalkEndTimer`）。CSS +3 block（`.dog-hopping` 750×120 0.55s steps(5) 1 forwards／`.dog-walking` 900×120 1.2s steps(6) infinite／`.dog-idle-yawn` 900×120 1.8s steps(6) infinite）＋八 URL `?v=12800` bust。tests：`test_game_action_set_v012723` 改名 `v0128_0`＋assets +3＋jsdom 行為級＝**6 passed**、合跑 web_server **65 passed**。pyproject 0.9.127.28 > **0.9.128.0**。  

### v0.9.127.28  

**run sheet 防閃爍重切（user 報告：「跑的時候大小不一致看起來在閃爍」）＝shih_run_c 來源 GIF 幀間取景不一致——量測證據：軀幹（侵蝕後主體）高 239/318/237/270（f1 整隻畫大 ~30%，寬度卻一致 347-375）、各幀長寬比 1.13-1.53 散開——任何單一縮放（union bbox）都無法同時穩定高與寬。** 修法＝`_build_dog3_sheets.py` 新增 **fit 模式**：run 改用形狀最一致的 3 幀 `[0,2,3]`（捨棄畫大的 f1）、6 格循環 `[0,2,3,0,2,3]`，**每幀 bbox 面積等化**（`scale=sqrt(median_area/area_i)`）後共同縮放入格、底線 y=116 對齊＋水平置中——狗的視覺尺寸恆定（殘餘高度差 <9% vs 原本 ~30%），跑步不再忽大忽小；gallop 蹦跳的垂直位移改由底線對齊捨棄（取景不可靠，寧可穩定）。roll/sleep/bone 維持 union 模式（翻滾/趴下/坐姿的輪廓變化是自然動作、非取景瑕疵）。CSS 五 URL `?v=12728` bust；app.js/tests 零改動。tests 不動＝dog_runner **6 passed**＋合跑 web_server **65 passed**。pyproject 0.9.127.27 > **0.9.127.28**。  

### v0.9.127.27  

**shih-tzu 四 GIF 全面改版（user 指定素材：`C:\Users\humphery\images\` 的 shih_run_c／shih_roll_c／shih_sleep_c／shih_bone_c——各 4 影格 512×512 **不透明白底** GIF）＝新 `_build_dog3_sheets.py` 管線覆寫五張 production sheet。** ①**取幀＋鏡射**：GIF 影格經 seek 取出（PIL ImageSequence 一次迭代會把 4 幀誤判相同——seek 逐幀取才正確）、**全部水平鏡射**——GIF 原生朝左，而 CSS `dog-sprint` 的 scaleX(1)/(-1) 翻面語意需要「原生朝右」的 sprite（放大比對現行 run sheet cell0 確認朝右後定案）。②**去背**：不透明白底＋白毛狗＝**邊緣泛洪只去白底**（全域白鍵會挖穿白毛＝127.24 教訓），卡通黑輪廓完整包圍白毛故泛洪穿不進；≥40px 分量過濾（保住啃骨頭的咀嚼動作線）＋1px 侵蝕去光暈。③**組 sheet**（CELL 150×120／union bbox／內容高 104px／底線 y=116 慣例不變，app.js／tests 零改動）：run=[0,1,2,3,2,1] 乒乓 gallop（4 幀循環攤 6 格）、jump=roll 翻滾花式 [0,1,2,3,3]、sit=sleep 趴下漸睡 [0,1,2,3,3,3]——**停止後末幀凍結＝睡著＋Zzz**、tongue/scratch=bone 啃骨頭 [0,1,0,1,0,1]（排除站姿 f2、坐姿咬合循環）。CSS 五 URL `?v=12727` bust。**還原路徑＝重跑 `_build_dog2_sheets.py`**（dog_anim 30 影格素材源不動）。tests 不動＝dog_runner **6 passed**＋合跑 web_server **65 passed**。pyproject 0.9.127.26 > **0.9.127.27**。  

### v0.9.127.26  

**退回 30 幀版（user 判定：v0.9.127.25 四姿勢試用動畫不好，退回上一版）＝重跑 `_build_dog2_sheets.py` 從 `dog_anim\` 30 影格（run/jump/sit/tongue/scratch 逐幀）覆寫五張 production sheet——尺寸與慣例與 127.24 產出完全一致（run/sit/tongue/scratch 900×120、jump 750×120、CELL 150×120、內容高 104px、底線 y=116），CSS 五 URL `?v=12726` bust；`_build_dog_trial_sheets.py` 留檔（trial 管線可再利用，素材源 `C:\Users\humphery\images\` 不動）。** JS 狀態機／app.js／tests 零改動。tests 不動＝dog_runner **6 passed**＋合跑 web_server **65 passed**。pyproject 0.9.127.25 > **0.9.127.26**。  

### v0.9.127.25  

**四姿勢小狗試用（user 提供 `C:\Users\humphery\images\` 4 張 1024×1024 RGB 姿勢圖——sit／stand／jump／headtilt，非連續動作循環）＝全新 `_build_dog_trial_sheets.py` 去背管線餵給現行五狀態動畫試跑效果。** ①**去背**：這組素材帶淺藍卡片底＋**網點抖動漸層帶**（blob 藍實為 b−r≈35-40 但 b−g≈0-5 的青色調，首版 `b−g≥3` 條件誤殺一半網點）；狗毛色全中性（b−r≈0）→ **全域移除藍色調（b−r≥12）＋邊緣泛洪只負責白底**——白毛（253-255）與外圍白底（252-255）不可分，白只能靠泛洪（127.24 教訓的精確適用範圍：**全域色鍵對白色危險、對藍色安全**；中途試過的「深色環殼移除」曾把狗輪廓連同白毛吃掉，已棄）＋≥250px 連通分量過濾（丟 dither 網點/影子碎屑、保留跳躍動作線）＋1px 侵蝕去光暈。②**組 sheet**：沿用 `_build_dog2_sheets.py` 慣例（CELL 150×120、union bbox 統一縮放、內容高 104px、底線 y=116）**覆寫五張 production sheet**——run=[stand,stand,jump,stand,stand,jump]（兩幀彈跳節奏）、jump/sit=單幀重複（sit 末幀恰為正面坐 ✓）、tongue/scratch=headtilt ×6（靜態姿勢 idle）；**JS 狀態機／app.js／tests 零改動**，CSS 僅五 URL `?v=12725` bust；sheet 存檔 `compress_level=1`（無損加大，誠實跨過 test 的 30KB stub-偵測門檻）。**還原路徑＝重跑 `_build_dog2_sheets.py`**（`dog_anim\` 30 影格素材源不動）。tests 不動＝dog_runner **6 passed**＋合跑 web_server **65 passed**。pyproject 0.9.127.24 > **0.9.127.25**。  

### v0.9.127.24  

**小狗去背修復重切（user 報告並自行修復：v0.9.127.23 白色去背時把小狗身體內部的白色區域——白毛、胸口、臉部白斑——誤判為背景一起挖掉）＝邊緣泛洪填補演算法（從圖片邊緣標記所有與外界相連的透明區域＝真背景；身體內部「不與邊緣相連」的透明洞判定為誤挖、填回白色實心），30 張影格全部修復（每張回填約 6,700–38,000 px）、不需重新產圖零費用。** 本版 `_build_dog2_sheets.py` 以修復後素材重切五張 production sheet（切圖邏輯零改），CSS 五張 sheet URL `?v=12724` bust；重切後 enclosed-transparent 驗證：run/jump/tongue＝**0**、sit＝4／scratch＝16 px（輪廓線圍出的腿間細縫，非誤挖）。`dog_anim\` 原始影格＋3s GIF＋5 針串圖同步更新、檔名不變。tests 不動＝dog_runner **6 passed**＋合跑 web_server **65 passed**。pyproject 0.9.127.23 > **0.9.127.24**。  

### v0.9.127.23  

**小狗全套五動作取代（user 產出 `C:\Users\humphery\dog_anim\` 5 動作 ×6 影格 512×512 透明 PNG——run 跑步／jump 跳／scratch 抓癢／sit 坐下／tongue 吐舌，每動作另附 3s GIF＋5 格串圖，30 張共 $1.16）——全面取代 v0.9.127.22 的三 sheet 版本。** ①**切圖組 sheet `_build_dog2_sheets.py`**——逐影格 alpha>40 取內容、**union bbox 跨幀聯集**（保留幀間相對位移＝蹦跳/落地的節奏）、單一縮放比（內容高統一 104px）、底線基線對齊、水平置中；**兩個素材坑現場抓到**：(a) scratch f1/f2/f5/f6 底部烤入「FRAME N of 6」字樣（f3/f4 無）＝**底部間隙帶偵測自動裁除**（行投影最後一段 >12px 空隙以下的內容視為 caption 丟棄）；(b) **jump_f4 AI 瑕疵——狗在空中突然戴上墨鏡**（角色斷裂）＝該幀跳過，jump sheet 改用乾淨 5 幀。產出：`dog_run_sheet.png`／`dog_sit_sheet.png`／`dog_tongue_sheet.png`／`dog_scratch_sheet.png` 各 **900×120**（6 幀 × 150×120 CELL，狗比舊 120px 更大）＋`dog_jump_sheet.png` **750×120**（5 幀）。②**結構重構＝雙層 div**——外層 `.dog-runner` 只管 `dog-sprint` 來回跑位＋`scaleX(-1)` 翻面，內層 `.dog-skin` 揹 sprite sheet 播幀；**關鍵修復**：舊結構在同一元素上換 class＝動畫清單整組重啟＝sprint 從起點瞬移——雙層後狀態切換只動 skin 的 background/animation，跑位永不受干擾（跑步中途插 jump 花式也因此變得可行）。③**狀態機升級**——跑步中 **4-8s 隨機插一次 jump 花式**（`dog-jump-play 0.55s steps(5,end) 1 forwards`，播完自動恢復 gallop；emoji fallback 不受影響）；12s 停止→凍結位置＋`.dog-stopped`（`animation:none`＋inline left）→ **sit sheet 一次 0.9s steps(6) forwards 坐下**（素材末幀恰好是正面坐姿＝自然轉面向鏡頭）→920ms 後**隨機 idle 迴圈**：`tongue`（正面吐舌喘氣 1.5s steps(6) infinite）或 `scratch`（抓癢 1.2s steps(6) infinite）；run 幀循環加速 0.78s→0.42s steps(6)。④**收尾**——`_dogRunStop` 清**四 timer**（`_dogSitTimer`/`_dogIdleTimer`/`_dogJumpTimer`/`_dogJumpBackTimer`）；CSS 五張 sheet URL 全帶 `?v=12723` bust；舊 `dog_turn_sheet.png`/`dog_sitf_sheet.png`/`dog2_raw_{run_2,turn_1,sitf_1}.png` 刪除（原始 30 影格留 `dog_anim\` 可免費重切）；`_slice_dog_set.py`/`_gen_dog_set.py` 留檔不再使用。tests：`test_dog_runner` 更新（skin 結構／四 timer／五 sheet＋steps(6)／jump 750px／舊 sheet 反存在守衛）＝**6 passed**＋jsdom 行為級（冪等單隻+淡出移除）＋`node --check` OK；與 test_web_server 合跑 **65 passed**。pyproject 0.9.127.22 > **0.9.127.23**。  

### v0.9.127.22  

**小狗升級＝遊戲級多視角精靈（user：「通話看起來不連續也不精細，圖可以再大一點、動作再多一點、連貫性再多一點、不要都是側面、可以轉正面，像遊戲動畫一樣」）。** ①**AI 生成三張遊戲風 sheet**（`_gen_dog_set.py`，recraft-v4.1-flash live，統一角色 prompt＝垂耳灰毛白胸紅舌粗輪廓；`21:9` 不被 recraft 支援→3 幀橫排改 `16:9`、4×3 跑步網格 `4:3`）：**跑步 13 幀**（4×3+1，重生成一輪強化「每幀腿相位大幅差異」prompt——首版 16 幀腿幾乎不動＋次版哈士奇角色斷裂皆棄）、**轉向 3 幀**（側面→3/4→正面）、**正面坐姿 3 幀**（面向鏡頭吐舌喘氣）；每種各 2 候選目視挑選（總耗 $0.056）。②**投影分段切格** `_slice_dog_set.py`（AI 不對齊網格——固定 4×4 切出 16 段含跨格殘影＋雜點，改 **y 投影切列帶→x 投影切個體**＋≥30px 雜點過濾，13/3/3 幀全數乾淨）→ CELL **76→120px**（圖更大）、統一幀高 100＋共享基線；產出 `dog_run_sheet.png` 1560×120／`dog_turn_sheet.png`、`dog_sitf_sheet.png` 各 360×120。③**遊戲式狀態機**——CSS `dog-sprint` 改**來回跑**（0-48% 去程、50-98% 回程 `scaleX(-1)` 自動翻面朝左＝橫向卷軸遊戲角色；emoji fallback 分離為單向 `dog-dash` 避免和 `dog-bob` 的 transform 打架）＋`dog-play 0.78s steps(13,end)`（幀數 8→13、每幀 60ms 更連貫）；JS `--dog-dur` 5-9s→**10-16s 一趟來回**；12s 停止→**凍結位置＋`.dog-turning`**（`dog-turn-play 0.45s steps(3,end) 1 forwards` 轉正面只播一次）→500ms 後 JS 換 `.dog-sitting`（`dog-sit-play 0.9s steps(3,end)` 正面喘氣循環）——跑→停→轉頭看著你→坐下喘氣的完整敘事；`_dogRunStop` 清 `_dogSitTimer`＋`_dogTurnTimer` **雙 timer**（轉身中途回覆也不洩漏）。④**快取 bust**——三張 sheet URL 全帶 `?v=12722`；層高 92→140px、emoji 58→92px。⑤**清理**——舊 `dog_sprite.png`/`dog_sit_sheet.png`/落選 raw 刪除；選中 3 張 raw（`dog2_raw_{run_2,turn_1,sitf_1}.png`）留檔供日後免費重切。tests：`test_dog_runner` 更新（probe 指新 sheet／13 幀 steps／turn 雙 class＋雙 timer／scaleX 翻面／三 URL ?v=）＝**6 passed**＋jsdom 行為級＋`node --check` OK；與 test_web_server 合跑 **65 passed**。pyproject 0.9.127.21 > **0.9.127.22**。  

### v0.9.127.21  



**小狗換 AI 產圖＋第二動作（user：「圖片可以以 Icon 為原型請 AI 進行製作嗎？目前的圖片很簡陋，而且動作很單調」）。** ①**AI 生成 gallop sprite**——本專案 v0.9.104 的 `ImageGenEngine`（OpenRouter Image API）首次服務自己的 UI：`recraft/recraft-v4.1-flash` live 產「8 幀側面跑步毛茸卡通狗 sprite」（2 張挑最好，$0.014/2 張）；`_gen_ai_sprite.py`（prompt：8 幀橫排等距、透明背景、粗輪廓、腿姿各異）→ 原生 2×4 網格 1024×1024 webp → **`_slice_ai_sprite.py` 切格管線**（四角 flood-fill 去白底→等分切 8 格→**統一幀高 64px＋共享基線**（AI 兩排狗畫大小不同，統一縮放保體型恆定）→重排 608×76）——**踩坑**：切格首版 `x0` 忘加格橫向偏移，8 幀全疊第 1 格互相覆蓋（讀圖即見、idx 偏移修正）。②**第二動作「坐下喘氣」**（破「動作單調」）——`_gen_dog_sit.py` 同引擎再生成「坐姿吐舌 2 幀」（第一版風格與跑步版斷裂：細線淺灰→重生成對齊「粗黑輪廓＋鮮明色」，$0.007）→ `_slice_dog_sit.py` 切成 `dog_sit_sheet.png` 152×76；**行為**：`_dogRunStart` 掛 12s timer→**JS 凍結當前位置**（`style.left = getComputedStyle(dog).left`，inline left 壓過被覆蓋移除的 sprint 動畫）＋`.dog-sitting` class→`dog-sit-play 1.2s steps(2,end)` 播放坐姿喘氣；`_dogRunStop` 清 timer（快回覆不洩漏）；emoji fallback 不坐（glyph 無坐姿）。③**圖快取 bust**——CSS 內 `dog_sprite.png?v=12721`（CSS ?v= 只管 CSS 本身、圖 URL 獨立快取；URL 帶版本參數讓 127.20 的程式繪舊圖失效）。tests：`test_dog_runner` +1（AI sheets：sit 資產/12s timer/凍結/clearTimeout/steps(2)/?v= 斷言）＝**6 passed**＋jsdom 行為級＋`node --check` OK。pyproject 0.9.127.20 > **0.9.127.21**。  



### v0.9.127.20  



**小狗升級＝真·Windows 精靈式多幀跑步動畫（user：「我要的是像 Windows 作業系統舊的精靈動畫…目前的小狗照片是靜態的」）。** ①**8 幀 gallop sprite sheet**（`gui/assets/dog_sprite.png` 608×76）——`_gen_dog_sprite.py`（PIL 程式繪製，Windows 精靈的正統做法）：**側面跑步姿的逐幀繪製**——身體膠囊＋胸腹高光＋**四腿 gallop 相位擺動**（near/far 兩對、sin 相位差）＋蹦跳（`abs(sin(phase))*6`）＋搖尾＋吐舌；**毛色自動取樣自用戶的 dog_icon.png 照片**（中央 32×32 區、濾掉亮背景與藍調）→ 卡通狗配色貼合真狗；首版照片變形法（rotate/squash）因趴姿照片無法構成跑步側姿而棄。②**CSS `steps(8)` 逐幀播放**——`.dog-sprite`：`background dog_sprite.png 608×76`＋`dog-play 0.55s steps(8,end)` 幀循環＋`dog-sprint` 線性位移（兩動畫並行＝邊跑邊踏步）；**③一次性探測 fallback**——`new Image()` 載入 `dog_sprite.png`（`window.__dogSpriteOK` 緩存）：成功 → sprite 模式；失敗 → `.dog-emoji`（🐕 glyph＋bob 律動）。tests：`test_dog_runner` 更新（sprite 探測/dog-sprite+emoji class/steps(8) CSS/雙資產）＝**5 passed**＋jsdom 行為級（driver 加 Image stub）＋`node --check` OK。pyproject 0.9.127.19 > **0.9.127.20**。  



### v0.9.127.19  



**運算小狗（user：「模型諮詢運算時會跑出一隻小狗在視窗裡到處奔跑，等運算完之後小狗自然消失」）。** `_dogRunStart/_dogRunStop`（app.js）＋`#dog-run-layer` CSS——**送出提問即出現**（`_sendChatCore` 的 user bubble 後）、**回覆落地/終態/背景任務 detach 即淡出消失**（pollTurnStream 終態 callback＋bg 兩處）；**小狗主角 = 用戶自己的 dog_icon.png**（127.4 的原始圖，複製進 `gui/assets/`，載入失敗 fallback 🐕 emoji）；**奔跑動畫 = 純 CSS keyframes**（`dog-sprint` 左→右橫越視窗 5–9s 隨機圈速 infinite＋`dog-bob` 上下蹦跳 0.5s）；**pointer-events:none**（永不擋輸入）、`prefers-reduced-motion` 停為靜態、`_dogRunStart` 冪等（長回合中再送出不重疊）。tests：新 `test_dog_runner.py` ×5（函式/資產/CSS+reduced-motion/send 掛點×2/**jsdom 行為級**：start 冪等單隻、淡出後移除）。pyproject 0.9.127.18 > **0.9.127.19**。  



### v0.9.127.18  



**全域首次手勢解鎖（user：「iPhone 還是出現 播放回覆」＝127.17 的解鎖只掛在 🎉/🎧 被點的那一下——但兩 toggle 早已開啟（reload 後從 localStorage 恢復、無新手勢）→ 解鎖從未發生 → 回覆落地仍走 127.16 overlay）。** 兩件：①**`_bindGlobalUnlock()`**（bindVoiceToggles 開頭即掛）——**頁面上任何第一次 touch/click（pointerdown/touchstart/touchend/click 四事件一次性）即 `_unlockVoiceEl()`**——🎉/🎧 已開著的 session 也能解鎖；②**`_voiceElUnlocked` 旗標**（unlock 的 play.then 成功時設）——`_speakReplyForLoop` 的 iOS 分流改為：**已解鎖 → `_speakReplyNow` 立即朗讀**（跳過 overlay＝回覆落地即出聲）；未解鎖 → 免持 overlay／純 autoTTS 一次性提示（後備）。tests：`test_voice_permission` 擴 `TestGlobalUnlockV012718` ×2（全域解鎖掛載/旗標＋立即朗讀分流）＝**相關 64 passed**。pyproject 0.9.127.17 > **0.9.127.18**。  



### v0.9.127.17  



**⏹ 停止朗讀鈕＋iPhone「資料查完立即語音回覆」（user： 「增加 停止話音出話 鈕／資料查完立即語音回覆」）。** 兩件：①**停止朗讀 FAB**——朗讀播放中自動浮現**右下紅色大圓鈕 ⏹**（`#voice-stop-fab`，z-10055；後端 TTS 與 speechSynthesis 備援都涵蓋）——一按 `_stopSpeech()`：pause＋`speechSynthesis.cancel()`＋FAB 隱藏＋**把說話權交還**（免持下 iOS → resume overlay、桌面 → mic 重開＝「我打斷朗讀＝該我說了」）；②**持久解鎖音訊 element**——`_ensureVoiceEl()` 單一 `<audio playsinline>`＋`_unlockVoiceEl()`（**🎉/🎧 開啟的 tap 手勢內 muted play 一次＝該 element 永久獲得程式化播放權**，iOS autoplay 政策）——`speakChatText` 改用**同 element 換 src 播**（不再每輪 `new Audio` 重新觸發政策）→ **iPhone 上回覆落地即自動朗讀＝查完資料立即出聲**，免持 overlay 僅在解鎖未生效時作為後備。i18n `voiceStop` ×4＋FAB CSS。tests：`test_voice_permission` 擴 `TestStopFabAndUnlockV012717` ×4（stop 堆疊/循環交還/持久 element＋手勢解鎖點/FAB show-hide+i18n）＝**相關 62 passed**。pyproject 0.9.127.16 > **0.9.127.17**。  



### v0.9.127.16  



**iPhone 錯誤終局真修（user 貼截圖：錯誤 toast 出現在**回覆落地後**＝autoTTS 的 `audio.play()` 被 **iOS 自動播放政策**擋下（「not allowed…denied permission」是 WebKit 的 autoplay 阻擋訊息）——**不是麥克風權限**（此前免持兩輪成功即證）。** 修：①**iOS 手勢化朗讀**——`_speakReplyForLoop` 分流 `_isIOS()`：**免持開啟** → resume overlay 升級為**雙鈕**（`🔊 播放回覆`＝tap 內 speakChatText【手勢內播放合法】＋`🎤 開始說話`＝tap 內開麥）；朗讀結束 → overlay 再現（🎤）→ tap → 錄音 → VAD → 送出 → 回覆 → overlay……**iOS 極限＝每段一 tap**（WebKit 平台規則）；**純 autoTTS（免持關）** → 一次性提示 `voiceIOSAutoplayHint`（「iOS 不允許自動播放—請點氣泡旁 🔊」）不再錯誤風暴；②overlay `onResume` 回呼＋`.vlr-box` 直欄排版 CSS。i18n `voiceResumePlay/voiceResumeListen/voiceIOSAutoplayHint` ×4。tests：`test_voice_permission` 擴 `TestIOSAutoplayV012716` ×3（分流/雙鈕/手勢內播放/i18n+CSS）＝**相關 58 passed**（127.14/15 斷言隨簽名更新）。pyproject 0.9.127.15 > **0.9.127.16**。  



### v0.9.127.15  



**iOS 永久拒絕偵測＋完整恢復教學（user：「現在連第一回話音都出不來顯示剛才的錯誤」＝iOS 在**系統層記住拒絕**、app 內永不重問——手勢再對也一律 NotAllowedError）。** 兩件：①**Permissions API 狀態探測**（`_micPermissionState()`＝`navigator.permissions.query({name:'microphone'})`→ granted/denied/prompt、iOS 不支援回 unknown）——**`_micPreflight` 開頭先探測：denied → 直接顯示完整恢復教學**（不再瞎喊重試）；`toggleMicRecording` 的 catch 同探測分流。②**`voicePermissionIOSDenied` ×4**——**四步恢復教學**（①刪主畫面 App ②iOS 設定 → Apps → Safari → 進階 → 網站資料 → 刪 tchtool.app ③設定 → 隱私權與安全性 → 麥克風 → Safari 開啟 ④Safari 重開本站、首次詢問按「允許」）；ja 區 127.14 重複 `voiceResume` key 順手清除。tests：`test_voice_permission` +2（denied 探測教學/i18n）＝**相關 55 passed**。pyproject 0.9.127.14 > **0.9.127.15**。  



### v0.9.127.14  



**iPhone web app 錯誤真修（user 澄清「這是 iPhone web app 的錯誤」＝iOS WebKit 的手勢安全模型：`getUserMedia` 只允許在使用者手勢內發起——免持循環「朗讀完自動重開麥」是 timer 觸發＝非手勢 → iOS 直接拋 NotAllowedError；且 iOS 在系統層記住拒絕）。** 兩件：①**iOS 手勢化免持**——`_voiceLoopRestartMic` 分流：**iOS**（`_isIOS()`＝UA ip(hone|od|ad)／`navigator.standalone`／iPadOS MacIntel+maxTouchPoints）→ **全屏 resume overlay**（`#voice-loop-resume`＋中央大圓按鈕「🎤 點一下繼續聆聽」）——**tap 就是手勢**，mic 在點擊內開啟、循環續行（半免持：每輪一點）；**桌面**維持 800ms timer 全免持；②**iOS 專屬權限教導**——`_micPermissionMessage` 的 NotAllowedError 分支加 `_isIOS()` 判斷 → `voicePermissionIOS`（「iOS 設定 → 隱私權與安全性 → 麥克風 → 允許 Safari／本 App（曾被拒需清除網站資料或重裝 App）」）。i18n `voicePermissionIOS`/`voiceResume` ×4＋overlay CSS（fixed 全屏 z-10060＋藍色膠囊按鈕）。tests：`test_voice_permission.py` 擴 `TestIOSVoiceGestureV012714` ×5（偵測/分流/教導/i18n×4/CSS）＋127.13 四守衛並存＝**24 passed**（permission+handsfree+mic_wiring+resilience）。pyproject 0.9.127.13 > **0.9.127.14**。  



### v0.9.127.13  



**麥克風權限 UX＝「語音功能失敗：The request is not allowed by the user agent…」教導化（user 貼 raw UA 錯誤字串＝Firefox 對 tchtool.app 的麥克風處於封鎖/未授權）。** 兩件：①**教導式錯誤**——`_micPermissionMessage(e)`：`NotAllowedError/SecurityError/PermissionDeniedError` → **4 語言教學訊息**（「點網址列左側 🔒 圖示，將『麥克風』設為『允許』並勾選記住後重試」），非權限錯誤維持 `voiceMicErr` 原路——`toggleMicRecording` 的 catch 改用之；②**🎧 免持開啟時權限預檢**（`_micPreflight()`＝一次性 getUserMedia、tracks 立即釋放）——**權限詢問出現在用戶主動點 🎧 的當下**（預期內），**拒絕＝toggle 自動滾回關閉**（`_voiceLoopStopAll`）而非 loop 運轉中莫名死亡。i18n `voicePermission` ×4。tests：新 `test_voice_permission.py` ×4（教學訊息/預檢滾回/toggleMic 改道/i18n×4）＝**相關 19 passed**。pyproject 0.9.127.12 > **0.9.127.13**。  



### v0.9.127.12  



**STT 韌性＝免持循環「第 2 回出現錯誤」真修（user 回報→log 取證：19:58:25/19:59:10 兩發 `voice_stt FAILED: RuntimeError('provider down')`＝OpenRouter STT 端點瞬斷 502（20:05 同鏈自愈並成功跑通兩輪免持）＝「第 2 回談話出現錯誤」的真身）。** 三層修：①**自動重試 ×2**（`transcribe` 拆 `_once(base_url, api_key, model)` 閉包＋0.5s/1.0s 短退避——秒級瞬斷直接自愈，20:05 的成功就是證據）；②**STT 備援 provider**——primary 三連敗後自動切 `_once(fb_base, fb_key, fb_model)`：`TELEGRAM_STT_FALLBACK_API_KEY`（缺省吃 **SILICONFLOW_API_KEY**＝專案已持有的 key）→ `TELEGRAM_STT_FALLBACK_BASE_URL`（缺省 `https://api.siliconflow.cn/v1`）→ `TELEGRAM_STT_FALLBACK_MODEL`（缺省 `FunAudioLLM/SenseVoiceSmall`）；③**錯誤人性化**——`gui.api voice_stt` 不再吐 `repr(e)`：`provider down/connection/timeout` → 「語音辨識服務暫時不可用，請稍後再說一次（若持續失敗請改用打字）」；speech.py 的最終 raise 也人性化（「已自動重試與切換備援」）。tests：新 `test_speech_stt_resilience.py` ×4（retry 自愈×3 calls／fallback base_url+model 接線／全敗人性化 raise／api 錯誤人性化）＋`test_ui_voice` locked-in 斷言反轉（provider down 不再出現在用戶錯誤面）＝**73 passed**（ui_voice+telegram_voice+resilience）。pyproject 0.9.127.11 > **0.9.127.12**。  



### v0.9.127.11  



**雙向語音強化＝免持對話（user: 「本專案可以提供雙向語音對話嗎」→ 實彈閉環驗證（TTS 說「雙向語音測試，你好」→ 18KB mp3 → 同段餵回 STT 轉回同句）確認 v0.9.68.0 的 voice_stt/tts_speak 鏈完全可用 → 核准「動工」）。** 三件上線：①**🎉 自動朗讀開關**（工具列 🔊 toggle、localStorage `chat-auto-tts` 持久、`_sendChatCore` 回覆落地即 `_speakReplyForLoop`）——回覆落地即朗讀不再手點每泡 🔊；②**🎧 免持循環對話模式**（localStorage `chat-voice-loop`）——`_voiceLoopStartMic` 自動開麥→**VAD 靜音看門狗**（AudioContext AnalyserNode RMS 0.025、講過話後靜音 ≥2s、最短 1.2s 防誤停）→ 自動停錄→transcript 經 `_voiceTranscriptHook` **自動送出**→回覆落地→朗讀（onended）→`_voiceLoopRestartMic`（0.8s 後重開麥）＝**ChatGPT 語音模式同款免持循環**；失敗守衛：轉寫失敗/錄音太短/麥克風打不開＝**循環自動停**（`_voiceLoopStopAll`，避免 toast 風暴）；③**瀏覽器原生 TTS 備援**（`_speakBrowserTTS`＝speechSynthesis＋zh voice 挑選＋markdown 剝除 `_ttsStrip`）——後端 kokoro 失敗/未配置時降級系統語音（零費用離線可用），不再死 toast。**UI**：工具列 mic 旁兩顆 icon-only toggle（🔊/🎧、`data-i18n-title` tooltip、active 綠 `.btn-loop-on`）、boot 恢復 localStorage 狀態；**speakChatText** 加第三參 `onEnd`（播放結束/播放失敗/降級全部會呼叫＝loop 的續點）。**i18n** `autoTtsOn/Off、voiceLoopOn/Off、voiceFallback` ×4。tests：新 `test_voice_handsfree.py` ×8（兩按鈕/持久化/loop 機制/VAD/send 落地/fallback/i18n×4/CSS/bind 早綁）＝**相關 106 passed**（36＋70）＋`node --check` OK。pyproject 0.9.127.10 > **0.9.127.11**。  



### v0.9.127.10  



**多檔並行上傳（user: 「支援多檔並行上傳」）。** uploadFiles 由「批序列」改 **worker pool**：批表（12MB trim 後）一次建好，`runWorker` 以 `nextBatch` 指針消費、**同時飛 ≤`UPLOAD_CONCURRENCY`=3 個批 POST**（單連線難吃滿慢鏈路頻寬、三條並行填滿管道）；`Promise.all(workers)` 收束。**失敗語意**：任一批失敗 → `stopFlag` 只停「不再領新批」，**在途批允許跑完**（已送位元組不浪費）→ pool 落定後**統一 summary toast**（紅=「N saved before the failure」／綠=總數）。**進度條**：新增 `_upSlots`（每個在途 POST 的 slot：loaded/total/xhr/abort）——fill = `_upAllDoneBytes`＋**Σ所有 slot 的 loaded**（並行下持續爬升）；取消鈕 = **遍歷 abort 全部 slot**（XHR 與 fetch-fallback 都掛 `slot.abort`）；⏳ toast 只在第一波（`slot.first`）發（後續波面板已可見）。**批完成序**：markers/pending buffer 逐批照「完成序」入框（並行下先發後到屬正常）。**server 端零改**（stdlib ThreadingHTTPServer 每 POST 一 thread、12MB 批＋ms 級時間戳碰撞後綴本就並行安全）。tests：`test_share_target` 新 `test_parallel_pool_source`（concurrency=3／stopFlag／Promise.all／slots）＋批簽名斷言 `start, slot`；`test_web_upload` panel 斷言 pool 化＋**行為級併發水位**：jsdom driver fetch stub 帶 30ms 人為延遲＋`open/maxc` 水位記錄——12 檔→`posts [5,5,2] + marks 12 + maxc=3`（三批真重疊）、4×5MB→`[2,2] + maxc=2`＝**相關五套 146 passed**（41＋105）＋`node --check` OK。pyproject 0.9.127.9 > **0.9.127.10**。  



### v0.9.127.9  



**進度條全程化＋跨主機慢速診斷（user: 「①上傳進度bar應該是所有檔案的上傳進度，而不是每次5個檔案 ②跨主機上傳速度慢是否可以改進」）。** ①**進度條改「整個選取」**——面板進度條 = **全部檔案的 bytes**（`_upAllTotal`＝選取總 bytes、`_upAllDoneBytes`＝已完成批累計＋當前批進度 → **單調遞增不再每批歸零**）；中繼行 = `批 {i}/{n} · 檔 {d}/{t} · `（新 i18n `uploadBatch` ×4）＋ `%`＋MB；批資訊保留在標題行。②**12 MB 批上限**（`UPLOAD_BATCH_BYTES`）：五個大 PNG 的批不再卡成一個 20MB mega-POST（slice 依 bytes 修剪、`while (i < files.length)` 以修剪後長度推進——**5 檔常規尺寸 byte-identical**、jsdom 12 檔契約 `[5,5,2]` 保留）；③**server 保存行帶實測速率**（`upload: saved N (12.3s, avg 156 KB/s) -> …`＋進入行 content-length）＝**「跨主機速度慢」從感覺變成量測**——下次現場直接從 app.log 讀平均接收速率對症（同區網→用 banner 列的 LAN URL 直連；跨公網→上行頻寬即天花板、PNG/JPG 本身已壓縮傳輸壓縮無益；12MB 批讓慢連線每 90-120 秒出一次 100% 節點）。tests：`test_share_target` batch_deadline +2 斷言（bytes cap＋while 推進）、`test_web_upload` panel 測試 +6（全選進度＋批推進）＋i18n key +1（uploadBatch ×4）＋server 速率斷言 ×2。pyproject 0.9.127.8 > **0.9.127.9**。  



### v0.9.127.8  



**上傳進度條（user: 「只間無任何反應和訊息持續三分鐘以上，前端UI是否要改進有進度Bar」＋「主機端沒有log????出現了」）。** **現場定案**（app.log 15:0x 窗口＋uploads 目錄＋fleet 實拍）：14:42–14:53 三發截斷重試（got 61KB/4.0MB/61KB/5.6MB of 984KB–69MB）＋⏳ toast 兩秒半即消失＝批間 POST 慢流 **3 分鐘零反饋**；**server 在 read body 期間結構性零 log**＝「主機端沒有 log」的真相（讀完才印 saved）——**其實 15:07/15:10 兩批各存 5 檔成功、[已上傳] markers 已在輸入框**（fleet snapshot 實拍 e32＝分批前端已在跑）。**修三件**：①**XHR `upload.onprogress` 真位元組進度**取代 fetch（唯一帶上傳進度的 transport；XHR 不存在——jsdom driver——時 fallback fetch 保舊契約）＋**常駐進度面板** `#upload-progress-panel`（右上固定卡：`⬆️ [1-5/15] 檔名…`＋▓進度條＋`47% · 12.3/26.1 MB · ⬆傳輸中/⏸停滯 N 秒`＋**✕ 取消上傳鈕**＝xhr.abort()→紅 toast 帶 N saved；setInterval 1s 心跳＝連線卡死也看得到停滯計時）＋style.css 專節＋i18n `uploadCancel/uploadStalled/uploadFlowing/uploadNetErr` ×4；②**deadline 放寬 60s＋60s/批內檔、上限 10 分鐘**（125.3 flat 30s 在慢連線半途砍流＝14:42/14:52 兩發 61KB 截斷真兇；`60000` 字面供 source guards）＋server `_handle_upload` **進入即 log** `upload: incoming POST, content-length=N`（接收端永遠有生命跡象，截斷拒收也有進入行對照）；③tests：`test_web_upload.py` deadline 斷言改 60000 公式＋jsdom driver `delete XMLHttpRequest`（fetch fallback 路徑照跑）＋新 `TestUploadProgressPanelV0127_8` ×4＋`test_share_target.py` 斷言同步（非-async 簽名＋60000）＝**相關五套 143 passed**（68＋75）＋`node --check` OK。pyproject 0.9.127.7 > **0.9.127.8**。  



### v0.9.127.7  



**上傳分批化＋死連線回覆守衛（user 提供 14:49 live traceback＝127.6 攔截器第一發真實命中）。** **現場解讀**：`truncated body — got 3997145 of 61980829 bytes`＝**62 MB 大 POST** 在慢連線半途被砍（4 MB/30s ≈ 133 KB/s＝30 秒舊 abort 的算術，分頁當時還在跑舊 JS）；回寫 400 時 client 的 TLS 已整條消失 → `ssl.SSLEOFError` **不是 ConnectionError 家族** → 逃逸出窄 catch → socketserver 印 traceback（無害但髒）。**修兩件**：①**死連線回覆守衛**——截斷分支改 `except OSError`（SSLEOFError 全家）＋`_send_share_redirect` 回覆段包 OSError＋do_POST 的 `/upload`／`/share-text` route 外層同守衛（窄 `ConnectionError` catch 全模組清零）——**同類回覆寫死 socket 不再噴 traceback**；②**治本＝分批上傳**——`uploadFiles` 拆 **5 檔/批**（`UPLOAD_BATCH_SIZE` ＋ `_uploadOneBatch(slice, total, start)`）：每批自己的 POST＋自己的 deadline（30s 基底＋30s/批內檔、上限 5 分鐘）、⏳ toast 帶 `[1-5/15]` 進度＋檔名、pending buffer 與 `[已上傳]` markers **逐批累加**（中段失敗不綁票已完成批）、失敗紅 toast 帶「N saved before the failure」、最後一批統一綠 toast（無 toast 洗版）；**≤5 檔＝單批行為 byte-identical**（全部既有測試契約不破，含 jsdom driver 契約）。tests：`test_share_target.py` +4（死連線守衛 source／分批 source ×2／舊 flat abort 禁絕）＝**相關七套 173 passed**＋`node --check` OK＋jsdom 硬驗證：12 檔 → `fetchCount=3 / batchSizes=[5,5,2] / markers=15 / pending=15`。**使用端**：分頁 Ctrl+F5 拿分批前端（62MB 情境＝每批 ~20MB 內？不——5 檔/批各自小 POST，慢連線下每批 ≤ 2.5 分鐘、失敗只損該批）；server 重啟拿守衛。pyproject 0.9.127.6 > **0.9.127.7**。  

### v0.9.127.6  

**遠端多檔上傳「數量不對＋[已上傳]沒插入」真兇＝30 秒硬中斷的截斷 body 被靜默部分儲存（user: 「跨主機上傳檔案…上傳只有三個但我選了十幾個」）。** **現場實證**：app.log `14:30:37 upload: saved 3 file(s), skipped 0`＋uploads 目錄同秒戳恰好 3 個 PNG＝**單一請求只帶了 3 個檔案**；前端碼零切片、兩個 input 的 `multiple` 都在 → 掉檔在傳輸半途——`uploadFiles` 的 **30s AbortController 硬中斷**在慢連線（遠端/WAN）把還在流的 POST 砍斷 → server `read()` 收到 FIN 後 break → 拿**截斷 body** 去 `_parse_multipart` → 只保得住斷點前的完整 parts（正好 3 個）→ **謊報 saved 3, skipped 0**（靜默部分儲存把掉檔藏起來）；⏳ 上傳中 toast 只存在 2.5 秒、紅色 fail 到 30 秒才跳＝現場看不見。**修四件**：①**server 拒收截斷 body**——`remaining > 0` → 400 `upload truncated: received X of Y bytes…none of the files were saved`（warning log；**零部分儲存**、uploads dir 連建都不建）；`/share-text` 同規則；②**前端 timeout 依檔數放大**：`Math.min(300000, 30000 + files.length * 20000)`（30s 基底＋20s/檔、上限 5 分鐘；`30000` 字面保留給既有 `test_web_upload` 斷言）；③**⏳ toast 帶真實選取總數**——`t('uploading')` 的 `{f}` 帶入 `(N) 名單…`：picker 回得比選的少**當場現形**（不再事後追兇）；④abort 專屬錯誤訊息（新 i18n key `uploadTimeout` ×4＝「上傳逾時（連線太慢或中斷）」）＋marker 插入 never-silent（chat-input 不存在也 toast 警告）。**關於 marker 未插入**：成功路徑的插入與 ✅ toast 在同一 block、同進同出——若分頁跑的是舊 JS 一樣會插（125.5 已把 marker 語言中立化）——**遠端分頁請 Ctrl+F5**。tests：`test_share_target.py` +2（**raw-socket 半 body → 400 → 零落盤**／雙端截斷規則 source guard）＝相關五套 **146 passed**＋`node --check` OK。pyproject 0.9.127.5 > **0.9.127.6**。  



### v0.9.127.5  

**分享三件套：分享出去＋Android share_target 分享進來＋iOS 捷徑通路（user 轉貼 iOS 分享資料 → 查證 caniuse 後 user 核准「以上全做（出去→Android 進來→iPhone 捷徑）」）。** **①分享出去（per-bubble 📤）**：assistant 氣泡新按鈕（沿 🔊 `btn-msg-voice` 同型式同 CSS）→ `navigator.share`（iOS Safari／Android Chrome 通吃；AbortError 靜默）→ 無 API 平台（桌面 Firefox／pywebview GUI）退 clipboard 複製＋i18n toast。**②Android 分享進來（share_target）**：manifest 新 `share_target`（action=`/upload?share=1`、POST multipart、params title/text/url=`share_title`/`share_text`/`share_url`＋files accept 影像/檔案清單）——裝成 PWA 的 Android Chrome/Edge/Samsung 分享選單直接列出本 app。**server**：`_parse_multipart` 新 `keep_fields` 參數（legacy 模式 byte-identical；field-only body 在 legacy 照舊 raise）；`/upload?share=1` 完整處理——檔案照 `_save_upload_parts` 存 `uploads/`，title/url/text 合成 `[title]/[url]/[text]` 文字側車存 `uploads/shared-<ts>.txt` → **303 redirect `/?shared=\<paths\>`＋`skipped=`／`shared_err=` 旗標**（分享是瀏覽器導覽 POST，回生 JSON 會整頁顯示給人看）；前端 `handleSharedLanding()`（bindEvents 早綁、idempotent）把 paths 插成 `[已上傳]` marker 行＋toast＋**history.replaceState 清 query——重載永不重放同一分享**。**③iOS**：`/upload?share=json` 同鏈但回純 JSON（Shortcuts 可解析）＋新端點 `POST /share-text`（raw `text/plain` 或 multipart `share_text` 欄位 → 存成 `uploads/shared-<ts>.txt` → 回 `{ok, path}`）＋**新文件 `docs/IOS_SHARE_SHORTCUT.md`**（兩支捷徑逐步設定：文字版→/share-text；檔案版→/upload?share=json→字典取 path→打開 URL `/?shared=\<path\>`＝檔案自動進輸入框）。**iOS 事實面**：Safari 全版本不支援 share_target（caniuse 2026-10 現況；轉貼文「iOS 16.4 支援 share_target」**不實**——16.4 進的是推播/badging）。**Android Firefox 亦不支援 share_target**（分享進來限 Chromium 系）。tests：新 `tests/test_share_target.py` 20 條（manifest 契約／keep_fields ×4／側車 helpers ×4／live share redirect+json+legacy 契約鎖+skipped/shared_err ×4／share-text ×4／app.js 守衛×4＋css）＝相關五套 **134 passed**＋`node --check` OK。pyproject 0.9.127.4 > **0.9.127.5**。


### v0.9.127.4  

**專案 icon 全套換新犬（user 圖 `C:/Users/humphery/images/dog_icon.png`）。** PIL LANCZOS 把 1024×1024 全彩狗圖重製全套：`icon-192/256/512.png`＋`icon.ico`（16/32/48/64/128/256 六尺寸——PyInstaller `gui.spec` exe icon 沿用同檔名自動吃到）＋`icon.svg`＝**512px PNG 內嵌 wrapper**（base64 data-URI、viewBox 512）——所有既有 `icon.svg` 引用面（favicon link＋header logo img＋manifest `any` entry）**零改動 byte 通用**；v0.9.103.3 的手繪 vector dog（dog_icon_bw.svg）退役留史。**順手修兩處**：①`icon-256.png` 名實不符地雷（103.3 時期檔名 256 實際 512×512、測試還鎖著）→ 改真 256＋測試對齊；②web_server cache-bust 清單 +3——favicon 快取是出名地黏，`href="icon.svg"`／`href="icon.ico"`／`src="icon.svg"`（logo）三 ref 供應時加 `?v=<版號>`（do_GET 既有剝 query，零檔名需求）；Pillow ICO fallback 只縮不放造成 128 缺尺（append_images 補全）一併踩掉。**android `launch_screen.png` 同步**（manifest `android:icon=@drawable/launch_screen` 指向它——下次 APK 建置自動吃到新 icon；已發布 dist APK 不動）。**注意**：icon 檔本身磁碟即時供應（瀏覽器重整可見）；web_server 的 index stamp 清單在 make_handler 時固化——favicon `?v=` 戳記需**重啟 web server** 才出現。tests：`test_icon_branding.py` svg/preview 兩測改契約＋docstring 更新、`test_web_server.py` cache-bust 測試擴 favicon/logo 斷言。pyproject 0.9.127.3 > **0.9.127.4**。  

### v0.9.127.3  

**語音輸入（🎤）不能用＝名稱飄移真修（user: 「語言輸入不能用」）。** **根因＝ReferenceError 靜默炸點**：`toggleMicRecording` 呼叫 `_isVoiceSupported()`——**此名不存在**（真正定義＝`_voiceSupported()`，6727 行）→ 每次點 🎤 都在 toast 之前拋 ReferenceError 散逸＝**零提示、零錄音、零請求、伺服端 log 零痕跡**（診斷路徑：伺服端 voice/stt/record 流量 0 筆 → 逐段讀 mic 引擎 → 名稱比對）；`node --check` 抓不到（合法名字、執行期才炸）＝測試只鎖字串面長期漏網的典型。**修**：①呼叫改回真名 `_voiceSupported()`；②`bindMic` 點擊改 never-silent 包裹——`Promise.resolve(toggleMicRecording()).catch(...)` ＋同步 try 內層，任何未來名稱飄移/麥克風權限/裝置錯誤都會以 `⚠️ 語音功能失敗:{msg}` toast 顯性化（權限類錯誤本就有專屬 toast：`NotAllowedError`→無法啟動麥克風、非 secure context→`voiceNoSupport`）。**tests +3**（新 `tests/test_voice_mic_wiring.py`：`_isVoiceSupported(` 呼叫面禁絕（regex——v0.9.126.7 註解誤觸教訓沿用）/toggle 用真名/bindMic catch 包裹）。pyproject 0.9.127.2 > **0.9.127.3**；**分頁重整即生效（assets 從磁碟即時供應，無需重啟）**——重整後點 🎤：Firefox 跳麥克風權限請點「允許」，講話後再點一次＝逐字稿進輸入框。

### v0.9.127.2  

**上傳↔送出競態真修＋x360 Firefox 實地重試（user: 「我是使用 x360 的firefox上傳的，可以用firefox_craw 再試試」）。** **tab 19（user 實際分頁）現場稽取**：輸入框只留使用者打的文字（加密分持 docx 更新工作流）、**零 [已上傳] 路徑行**；版本章 v0.9.127.0 為動態繪上的伺服器版號＝**不保證分頁 JS 版本**。**真因確立＝race**：上傳 fetch 回程（插入 `[已上傳]` 行進輸入框）遲於使用者開始打字/按送出 → 送出訊息不帶圖路徑 → `start_turn` 的 prompt 沒圖 → **AI 看到問題而不見圖**（✅ toast 只代表「檔案已存 uploads/」）。**修＝pending-upload buffer**：①`uploadFiles()` 成功 → paths 併入 `window.__PENDING_UPLOAD_PATHS__`（try 包裹）；②`_sendChatCore()` 讀 text **之前**：buffer 非空且輸入框未含第一個 marker → 自動把 markers 併入訊息＋清 buffer——**插得越慢也一定搭上下一則訊息**、正常流程（已插入）零改動。附註：MCP 合成點擊不觸發 Firefox 原生選檔（需 trusted activation）＝OS 級 SendInput 試點後停手不擾用戶桌面。tests：`test_send_safety.py` +1（buffer push/merge-if-missing/clear 三契約）。pyproject 0.9.127.1 > **0.9.127.2**；**x360 重整分頁後重試**。

### v0.9.127.1  

**上傳圖檔「送出無聲無息」防護＋實彈驗證收案（user: 「上傳圖檔 UI前端沒有任何回應」→ 追問得知＝「有 ✅ 已上傳提示，但訊息送出後 AI 沒有處理圖片內容」）。** **診斷（全鏈活體實證，非猜測）**：①伺服端健康——app.log 顯示 10:18 兩筆 POST /upload 保存成功（3+1 檔 `C:/Users/humphery/uploads`）；活體探測 **serve 的 app.js?v=0.9.127.0 為最新**（start_turn/⏳toast/UPLOAD_MARK/bindZoomGuard 全在）；**POST /upload 回應契約吻合**（`{ok,files[{path}],skipped}`、檔案落盤）；單一進程（:8334 唯一 LISTEN）＋TLS 正常。②**在 user 的 Firefox 實彈重現兩回合**（firefox_craw 走真頁面）：回合 1 = 1×1 極小測試圖 → `start_turn`→ToolRoute(on)→**AI 自行派發 `describe_image`**→read_file（binary 拒讀＝正常）；回合 2 = **真實內容圖**（Pillow 渲染 HELLO8787+紅框）→ **AI 正確回答文字內容與紅框位置（2 calls, 1 describe）＋bubble 內嵌圖片預覽**＝上傳→送出→圖內容處理 **全鏈通**。③兩回合的 describe 皆見 **Ollama(gemma4:31b-cloud) 回 500 → 退 OpenRouter Gemini 補位**——小圖被 Ollama/Gemini 以「image not valid」拒絕屬內容太小的預期降級，真實圖正確描述＝vision 鏈目前可用（failover 正常發揮作用）。**④修掉的真 bug（靜默失敗類）**：`sendChat` 無 try/catch——任何 client 端拋錯（橋不存在/fetch 瞬斷/renderer 例外）自事件 handler 散逸＝**零回饋＋送出鈕永遠卡 disabled**（10:18 場景的送出在伺服端零痕跡＝就是這一類）。修＝`sendChat` 變 wrapper（capture draft→try `_sendChatCore()`→catch：**⚠️ toast＋error bubble＋autoSave＋還原送出鈕＋草稿放回輸入框**，catch 內四段各自 try 包裹永不二級爆炸）。**tests +3**（新 `tests/test_send_safety.py` ×2：wrapper/core 分離＋catch 四件套存在；`test_chat_autosave.py` ×2 body 切片改指 `_sendChatCore`）＝55 passed＋node --check OK。**使用端指示：重新整理（Ctrl+F5）或關掉重開分頁**即可拿到 127.1 前端。pyproject 0.9.127.0 > **0.9.127.1**。

### v0.9.127.0  

**自我優化五項採用＋全項實作（user 核准「按建議 全項實作(1到5項)」；針對本專案自我提升迴路的借鏡缺口補完）。** ①**成功 plan→SKILL.md 固化**（新 `skill_library.draft_from_plan` + `_sink_plan` 鉤子）：TACTI plan 以 done 收場且 ≥ `QDRANT_PLAN_SKILL_DRAFT_MIN_STAGES`（預設 3）→ **自動草擬** SKILL.md 到 `$QDRANT_RAG_HOME/skill_drafts/plan-<名><id>/`（frontmatter+步驟+verify+handoff 慣例；meta 標 `auto-drafted-from-plan`/`pending-confirm`）——**永不自動入庫**（複核 → `skill_import(source=<資料夾>, confirm=true)`；草稿目錄刻意在庫外=skill_import 拒絕庫內源）；`QDRANT_PLAN_SKILL_DRAFT=off` 退出；TACTI prompt 區塊補「成功經驗固化」節。②**規則療效度控**（`rulebook.note_tool_error` + agent `_run_tool_call` is_error 分支 fire-and-forget thread）：每次工具失敗＝一次被觀察的 prediction error，**全部 in-force 規則 `errors++` + `last_error` 蓋章**——回退/晉升決策有量化證據；**寫入繞過快照 ring**（記帳非修訂，不污染 rules_history）；`rules_revise` 重寫區塊＝歸零（新修訂從零累計）；`rules_list` 與規則檔本身都帶 errors/last_error；`QDRANT_RULEBOOK_TELEMETRY=off` 退出。③**記憶維護**：`MemoryStore.cleanup_candidates`（零命中＋超齡、最舊在前、DRY-RUN 永不刪）+ memory-local 第 6 工具 `suggest_memory_cleanup`（stdio mirror 同步）+ `gui/api._ensure_memory_maintenance_schedule`（opt-in `QDRANT_MEMORY_MAINTENANCE=on` 開機建「記憶維護（每週）」cron 0 9 * * 1，fire prompt 只回報候選、**絕不自動清除**，同名存在=no-op、啟動 scheduler daemon）。④**驗證者異源**（`scheduler._alt_verifier_pin` + `_verifier_alt_pin_enabled`）：驗證糾正回合改跑 `agent_factory(llm=<第二候選>)`——候選取 `_provider_candidates()[1:]` 中 task_llm 可 pin 的（openrouter/ollama 白名單）；guard 糾正與 `_verified_flag` 改看 `guard_agent`（糾正回合的 verify_artifact 回執計入證據）；零參 legacy factory TypeError→回退原 agent（測試契約不破）、單供應商安裝也回退；`QDRANT_VERIFIER_ALT_PIN=off` 退出。⑤**ToolRoute 可修訂化（shadow 先行）**：新 `src/agent/toolroute_overrides.py`（zero-dep）——`agent_toolroute.md`（READ 沙盒 sibling；`### [tN]` + `- server:`/`- patterns:`/`- evidence:`）+ 快照 ring `toolroute_overrides_history/` + 三態 `QDRANT_TOOLROUTE_OVERRIDES`=**shadow（預設，命中只記 `[toolroute-override] shadow hit` log 不掛）**/on（union 進 `_toolroute_servers_for`，union-only）/off；rulebook-local 升 7 工具（+`toolroute_override_add`〔confirm 閘〕/+`toolroute_override_list`）+ rulebook_server stdio 鏡像同步；壞 regex fail-closed 拒收。**config.py +8 raw attrs、.env.example 新區塊**。tests：新 `tests/test_self_improve_127.py`（telemetry 增量/ring 不污染/revise 歸零/gate-off；draft 形狀/meta/garbage 容錯/min-stages env/gate/prompt 提及）+ `tests/test_self_improve_127b.py`（cleanup_candidates 過濾/錯誤回報/host DRY-RUN；alt pin 白名單/單供應商/壞 agent/env；overrides 全生命週期/shadow-on-off 路由整合/host confirm 閘；gui 排程 opt-in/冪等/off）+ 契約更新 `test_rulebook.py` ×3 工具名清單、`test_memory_store.py` ×2。回歸：rulebook 28＋memory 53＋schedule_verification 43＋skill_library 64＋plan_scope_handoff 19＋skill_compiler 48＋config_reload 17＝**全綠**；新五檔＋2 = **24 tests new**。pyproject 0.9.126.8 > **0.9.127.0**。

### v0.9.126.8  

**啟動主 URL 正式化為 https://tchtool.app:8334（user: 「本專案 改為 https://tchtool.app:8334 但 啓動時還會去連 http://127.0.0.1:8334」）。** 兩個真因、一次修：**① live `.env` 的 Web 區塊整段消失**（v0.9.126.1 設定的 `QDRANT_WEB_HOST/PORT/TLS_CERT/KEY/TLS_NAME` 全不在現行 `.env` 中——啟動全部走預設值＝`http://127.0.0.1:8334` 純 http loopback）→ 補回完整 Web 區塊（HOST=0.0.0.0 手機/PWA/APK 才連得到＋PORT=8334＋kidmen 憑證對＋TLS_NAME=tchtool.app）。**② 程式面**：`_urls_for` 主 URL 恆為 loopback——即使 TLS 有設，banner 主行與**自動開啟的瀏覽器**也只會開 `https://127.0.0.1:8334/`（SAN 只涵蓋名稱＝憑證警告），`certified as` 只是次要列印行 → **有 certified name 時名稱升為 canonical**：banner 主行＝`[web] Divine Sense Agent web UI: https://tchtool.app:8334/`、loopback/LAN 降為 "also reachable at" 次要行、auto-open 開名稱 URL；無 TLS/無名稱 = 原行為 byte-identical。測試 +1（`test_certified_name_is_primary_and_browser_url`：banner 主行/次要行/auto-open 三斷言）。pyproject 0.9.126.7 > **0.9.126.8**；**重啟 web server 生效**。

### v0.9.126.7  

**手寫書寫區去框線（user: 「手寫區 不需要用 框線 圍起來，設計有一點低級」）。** `.hw-canvas` 移除 `border: 1px solid var(--border)`＋圓角——書寫面就是一張乾淨的白紙、不再有邊框盒；範圍由白面與周圍留白自然界定，modal 對話框本身的外框與 126.3 淺色 pin 不動。測試 +1（`test_hw_canvas_frameless_v0126_7`：`.hw-canvas` 規則區塊不得含 `border`、白底保留）。pyproject 0.9.126.6 > **0.9.126.7**；web 端重整生效。

### v0.9.126.6  

**Web APP 禁止 UI 放大縮小（user: 「Web APP要禁止UI被放大縮小」）。** 三層防縮放、捲動不受影響：**① viewport meta**（`user-scalable=no + minimum/maximum-scale=1.0`——手機捏合與雙擊縮放的宣告層；桌面 pywebview WebView 忽略 viewport＝GUI 不受影響）；**② CSS 層**（`html, body { touch-action: pan-x pan-y }`——允許 pan 捲動、CSS 層面禁止 pinch-zoom 與雙擊縮放；手寫 canvas 自帶 `touch-action:none` 不受影響）；**③ JS 事件守衛**（`bindZoomGuard()`——**`isWebMode()` gate** 桌面 pywebview 行為零改動：iOS Safari 自 iOS 10 起為無障礙**無視 `user-scalable=no`**，故攔 `gesturestart/gesturechange/gestureend` preventDefault；桌面瀏覽器攔 Ctrl/⌘+滾輪（`{passive:false}`）與 Ctrl/⌘+`+`/`-`/`0` 鍵盤縮放捷徑；掛在 bindEvents 前段（upload/mic/paste/drop 群之後）。測試 +3 新 `tests/test_zoom_guard.py`（meta 鎖定/守衛函式 web-gate+攔截面+bindEvents 接線/CSS touch-action）。pyproject 0.9.126.5 > **0.9.126.6**；web 端重整（cache-bust ?v= 隨版號）生效。

### v0.9.126.5  

**手寫 modal 閒置提示文字移除（user: 「這行字不要出現 用手指／觸控筆／滑鼠在下方書」）。** `#hw-hint` span **兼做儲存／匯入 PDF 的狀態列**（hwOcring/hwPdfImporting 進度訊息填在這）——元素保留，但**閒置態不再顯示任何字**：index.html 靜態文字與 `data-i18n="hwHint"` 移除（否則 applyI18n 每次語系套用會重填回來）+ app.js 四處恢復點（open / PDF 換頁取消 / PDF 匯入 finally / 儲存完成）`t('hwHint')` → 清空字串。`hwHint` i18n key ×4 定義保留（測試契約不破、日後要恢復只改一處）。`.hw-hint` 的 `flex:1` 仍在＝右側按鈕群佈局不變。測試 +1（`test_hw_idle_hint_removed_v0126_5`：HTML 無「用手指」與 `data-i18n="hwHint"`、app.js 無 `t('hwHint')` 呼叫、span 存在、key ×4 保留）。pyproject 0.9.126.4 > **0.9.126.5**；web 端重整生效。

### v0.9.126.4  

**手寫 modal 按鈕純 icon 化（user: 「手寫UI中的icon不要有文字說明放不下版面會亂掉」）。** hw-modal 內所有「icon+文字」按鈕改**純 icon**（⛶/🖊/🖍/🧽/➕/🖼/📄/🧹/💾/✕），說明文字改掛 `data-i18n-title` tooltip（v0.9.97.0 既有機制、隨語系切換）+ `applyI18n` 同步映射 `aria-label`（純 icon 按鈕的無障礙標註）。**修掉的隱藏 bug**：`#hw-full2` 原本掛 `data-i18n="hwFull"`——applyI18n 每次語系套用會把它 textContent 覆寫成「⛶ 全螢幕」＝該鈕在窄版面本來就會被撐開；一併轉 title 後整列不再溢出。CSS：`.hw-modal .btn { min-width: 34px }` 保純 icon 按鈕觸控目標。**淺色 pin（126.3）與全部行為/繫結不動**（id 綁定照舊、i18n keys ×4 保留）。測試 +1（icon-only 守衛：10 個 key 走 `data-i18n-title`、無殘留文字標籤、aria-label 映射、CSS min-width）+2 契約更新（hwSave/hwPdf token 轉 title）。pyproject 0.9.126.3 > **0.9.126.4**；web 端重整（cache-bust ?v= 隨版號）生效。

### v0.9.126.3  

**手寫 modal 圖示黑底白字修（user: 「手寫UI有些icon是黑底白字在白底的手寫UI看起來很怪」）。** 根因＝手寫板的書寫面永遠是白的（`.hw-box` 的 `var(--bg-1, #fff)`——`--bg-1` 從未被定義＝永遠落白色 fallback；全螢幕 `.hw-full` 亦寫死 `#ffffff`），但 modal 內的 `.btn` 按鈕吃全域主題變數 `var(--bg-3)`＋`var(--fg)`——**深色主題下＝深灰底淺色字＝黑底白字按鈕貼在純白手寫板上**。修法＝**把淺色調色盤 pin 在 `.hw-modal`**（CSS 自訂屬性就近覆蓋繼承——`--bg/--bg-2/--bg-3/--fg/--fg-2/--border/--primary/--primary-fg/--accent/--accent-soft/--code-bg` 在 modal 子樹一律淺色值）：按鈕／標題輸入框／色票 chips／hint／active 高亮全部以白底配色呈現，**淺色主題 byte-identical、深色主題其餘介面零改動**（書寫面必須保持白色＝墨色保真＋OCR PNG 正確）。測試 +1（source guard：`.hw-modal` 第二條規則含淺色變數組）。pyproject 0.9.126.2 > **0.9.126.3**；web 端重整（cache-bust ?v= 隨版號）生效。

### v0.9.126.2  

**APK 本機建出（user 核准「APK 在這台機器建出來」）。** **①SDK 布建**（`.android-sdk/`，僅此一次）：commandlinetools-win 11076708（146 MB）→ 解壓 `cmdline-tools/latest` → `sdkmanager --licenses`（200×y stdin）→ **platform-tools＋build-tools;34.0.0＋build-tools;35.0.0＋platforms;android-34**（殼被 30s 逾時殺兩次後改 **Start-Process 分離式**續裝完成；android.jar/aapt2/d8 全就位）。**②wrapper 零依賴化**：`MainActivity` 改 **android.app.Activity＋android.app.AlertDialog＋platform Material theme**（androidx/appcompat/material **摘除**）→ 無 gradle 手組可編譯；`app/build.gradle` dependencies 清空；真 icon（icon-192.png→`res/drawable-nodpi/launch_screen.png`，XML 佔位移除）。**③新 `android/build_apk.py`（no-gradle 組裝器）**：aapt2 compile→link（**AGP8 manifest 無 package 屬性→運行時注入 temp manifest**）→javac(release 17)→jar→**d8（34.0.0 的 R8 8.2.2-dev 對本輸入 NPE「String.length null」→改用 build-tools 35 stable R8 解）**→classes.dex 附加進 base.apk→zipalign→首建 debug keystore（`android/.debug.keystore`）→apksigner sign+verify→badging 稽核。**④成品**：`android/dist/divine-sense-agent-launcher.apk`（**25 KB**、package=com.divinesense.agent、versionCode=1/versionName=0.9.126.2、**唯一權限 INTERNET**、launchable=MainActivity、icon PNG 在包、**apksigner verify 通過**）；預設載 `https://tchtool.app:8334/`（憑證名稱）＋選單換址。**⑤docs/.gitignore**：APK_BUILD_STEPS 改「本機一鍵建置」為主路線（Studio 為備用）；ignore += `.android-sdk/`、`android/.debug.keystore`。pyproject 0.9.126.1 > **0.9.126.2**。

### v0.9.126.1  

**採用 kidmen-web 的可信憑證＝PWA 安裝直接通（user: 「https://tchtool.app:8334 本機的https 參考 C:\Users\humphery\coding\kidmen-web」）。** **查證**：`C:\Users\humphery\coding\kidmen-web\ssl\cert.pem`＝**GoDaddy Secure CA - G2 簽的可信憑證**（SAN=**tchtool.app + www.tchtool.app**、NotAfter **2026-12-13**、檔內含中繼 CA 鏈）＋`key.pem` **public-key 精確配對**（cryptography 比對）＋**公網 DNS `tchtool.app → 192.168.50.154`（本機 LAN IP，無 hosts hack）**→ **這對憑證對這台機器、以這個名稱是真 secure context**。**接線**：live `.env` Web 區塊設 `QDRANT_WEB_TLS_CERT/KEY=kidmen 對`＋新 `QDRANT_WEB_TLS_NAME=tchtool.app`；**run_web_server banner 補行**：https 時印 `[web] certified as: https://<NAME>:<port>/ (Android PWA install — use THIS name, not an IP)`（憑證 SAN 只涵蓋名稱不涵蓋 IP——**Android 安裝必須走名稱**）。**端到端實證（預設憑證驗證 ON、無 -k）**：`run_web_server(TLS=kidmen 對)`→`urllib.urlopen('https://tchtool.app:<port>/healthz')`=**200 {"ok":true}**——GoDaddy 鏈本機+名稱全過＝Android Firefox/Chrome 的「安裝」**不再出現「無法安裝這個應用程式」**（無需「接受風險」；Chrome 亦過——與自簽路線的差異）。**測試 +2**（TestTlsNameBanner：certified 行出現/無 TLS 不出現——**fixture 掌控點 patch**：`fake_servers` 恆 `_tls_material→None`（防 .env 的 TLS 值經 load_dotenv 重新注入翻轉既有 http 契約——dotenv 級 patch 不可靠），certified 測試自行 patch 回對）＝ web_server 42＋tls 7＋pwa 8 **全綠**；live `.env` 已就緒（**重啟 web server 後生效**＝手機以 `https://tchtool.app:8334/` 安裝）。pyproject 0.9.126.0 > **0.9.126.1**。

### v0.9.126.0  

**PWA「無法安裝這個應用程式」真因＝http 明文 origin（PWA 安裝僅在有 secure context 才被提供）——web UI 加可選 HTTPS（自簽自動）（user: 「安裝 無法安裝這個應用程式」）。** **①`gui/web_server.py` 新 TLS 裝甲**（`WEB_TLS_CERT_ENV/WEB_TLS_KEY_ENV/WEB_TLS_AUTO_ENV` 三 env）：`_tls_material()` 解析——(a) `QDRANT_WEB_TLS_CERT/KEY` 兩者皆設＋兩檔都存在=用它；(b) 半設/缺檔=誠實 WARNING 退 http；(c) `QDRANT_WEB_TLS_AUTO=on`(**on/yes/true/1** 別名)=`_self_signed_pair()` 自動自簽——**SAN=127.0.0.1＋::1＋`_enumerate_lan_ips()` 全數**（使用者以區網 IP 訪問正中）、CN=Divine Sense Agent、RSA-2048/SHA-256/10 年、**產在 `$QDRANT_RAG_HOME/certs/web-tls.{crt,key}` 並重啟重用**（每次 boot 重簽會作廢 Android「接受風險」的信任決策；key 最佳努力 0o600）；缺 cryptography=誠實退 http 不炸。**run_web_server**：bind 後 `ssl.PROTOCOL_TLS_SERVER`＋`load_cert_chain`＋`wrap_socket(server_side=True)`→ scheme='https'→ `_urls_for(host,port,scheme)`/banner 全改 https（瀏覽器開對 scheme）；TLS wrap 失敗=SystemExit 明確訊息（不靜默降半開）；**未設 TLS env=plain http byte-identical**（`_format_url/_urls_for` 加 scheme 參數、預設 'http'=既有契約值全兼容）。**②.env.example** Web-TLS 區塊（三擇一說明＋Android Firefox 信任/安裝步驟＋Chrome 對自簽可能仍拒→APK 路線）。**③測試 +7** `tests/test_web_tls.py`（material 矩陣×4/AUTO 生成＋SAN IP 稽核＋reuse 不重簽/scheme plumbing/live **真 wrapped-socket https round-trip** `GET /manifest.webmanifest`=200 standalone）＋**live 端到端**：真 `run_web_server`(TLS AUTO)→banner=`https://127.0.0.1:8391/`→`urllib` https `/healthz`=200 `{ok:true}`——自簽實檔落在 `C:\Users\humphery\.qdrant-rag-agent\certs\`。tls 7＋pwa 8 = **15 passed**。pyproject 0.9.125.9 > **0.9.126.0**。

### v0.9.125.9  

**Android 以 app 形式啟動本服務網頁——Firefox banner 不再吃版面（user: 「在 android 環境 製作 一個 apk 啟動本服務網頁，使用firefox啟動會有firefox banner破壞版面」）。** **①路 1＝PWA 安裝（主交付，零建置）**：新增 `gui/assets/manifest.webmanifest`（`display:"standalone"`＝Android Firefox/Chrome「安裝」後從桌面啟動＝**獨立 app 視窗、無任何瀏覽器 banner**、`start_url/scope=/`＋`theme_color:#1a1b1e`）＋ `gui/assets/sw.js`（**NETWORK-ONLY** service worker——`fetch` handler 空體＝預設全走網路、**零快取**：不會吃掉 `?v=` cache-bust 的職責、動態 API 永不從 SW 回舊內容）＋ `_MIME[".webmanifest"]="application/manifest+json"`＋ `index.html` head `<link rel="manifest">`＋ boot（**僅 web 模式** gate——pywebview GUI 不註冊）`navigator.serviceWorker.register('sw.js')`（永不擋 boot）＋ **icon-192/icon-512.png**（**fitz（PyMuPDF）由現有 icon.svg 512 基底等比 render**——manifest 需要實位 PNG 才會出安裝提示；`purpose:"any maskable"` 兩尺寸）。**②路 2＝真 APK wrapper 專案源碼（新 `android/` 資料夾）**：本機無 Android SDK＋無 gradle（Java 21 已在）＝**不可在本機直接出 .apk**——交付**最小可建置完整專案**：`MainActivity.java`（WebView standalone 啟動、預設 `DEFAULT_START_URL="http://192.168.50.154:8334/"`、選單換址持久化、back=WebView 歷史返回、`setAllowFileAccess(false)`/無 geolocation/**唯一權限 INTERNET**）、`AndroidManifest.xml`（usesCleartextTraffic、singleTask）、根/模組 Gradle（AGP 8.5.2/minSdk 24/targetSdk 34）、res（strings/themes—WebView2 深底）、`README.md`＋`APK_BUILD_STEPS.md`（Android Studio 三步建置）；`.gitignore` 加 `android/` 建置產物＋`*.apk`。**測試 +8** `tests/test_pwa_install.py`（manifest 契約×2/真 PNG×1/SW network-only 反證（禁 `caches.`）＋MIME/前端 link+web-gate 註冊/live round-trips：`/manifest.webmanifest=200+manifest+json+standalone`、`/sw.js=200+skipWaiting`、icon ×2 PNG magic）＝ **124 passed**（pwa 8＋web_server 40＋web_upload 35＋handwriting 25＋notes_ui…）；node --check OK。pyproject 0.9.125.8 > **0.9.125.9**。

### v0.9.125.8  

**Android pen 畫線變虛線修復（user: 「使用 android pad 的 pen 畫線會變 虛線」——125.7 增量段繪幾何的段縫副作用）。** **真兇**：每段 `beginPath()` 後從 **上一原始點** `moveTo(lx,ly)` 起筆，但上一段的終點是 **上一中點**——段與段的接點錯位一截（pen 線細＋Android pen 高頻 coalesced 點=段短→縫明顯）＝虛線。**修**（**b 點接續**）：draw 閉包新增 `bx,by`＝筆位；**down → bx/ly＝首取樣點**；**每段 = `moveTo(bx,by)` → `quadraticCurveTo(lx,ly → 新中點)` → `stroke()` → `bx,by=中點`**——段界兩邊共用同一中點＝**實線**；**末端半段補全**：`end` 先 `tail()`（`moveTo(bx,by)→lineTo(lx,ly)`，eraser/筆皆補）再走 redraw/restore。**重放（`notesHwRedrawPage`）幾何同步**：同一 b 接續形＋尾段 `lineTo`＋**一 path 一次 stroke**（無 alpha 疊繪）＝重放與直畫逐段同形。**順帶**：eraser **單擊**現在真有擦——重放對 `pts.length<2` 的 erase 筆劃畫 `width/2+1` 的 **dest-out 圓**。**測試更新**（125.7 guard 的幾何斷言升級：`moveTo(bx/by)`/`bx=mx`/尾段 lineTo/單點擦圓＋**全檔反證**舊段起筆 `moveTo(lx,ly)` 已消除）＝**77 passed**；node --check OK。pyproject 0.9.125.7 > **0.9.125.8**；web 頁面重整生效。

### v0.9.125.7  

**手寫工具三件套——🖊 原子筆（多色+可調粗細）、🖍 螢光筆（多色半透明）、🧽 橡皮擦（user 同訊追加：「需支援 eraser、marker pen (多色)、pen (多色調整、可調粗細)」）。** **核心=筆劃記錄模型**：`hwStrokes[page] = [{tool,color,width,pts[]}]`（pointerdown 建錄、move 逐點推入）；**橡皮擦安全語義**=擦筆劃在**活複合 canvas 上 destination-out**（會短暫戳破 PDF/筆記本底稿）→ **pointerup 全量重繪** `notesHwRedrawPage()`（`notesHwInitCanvas()` 重新鋪背景鏈 underlay→全域 bg→白底 → **按時間序重放全部筆劃**（erase 筆劃對「當時已畫內容」dest-out——重放即完整重演畫布演進、之後的筆不被回溯擦除）→ **底稿恆不破**、只消失被擦的墨）。**繪幾何改增量段**（每 move 一 beginPath→moveTo(lx,ly)→quad(lx,ly→中點)→stroke）：**螢光筆的半透明不可走舊「整路徑每 move 重 stroke」模型**（alpha 疊繪=線越畫越深——舊模型恰在 alpha<1 破功）；單點平滑性質保留。**工具列**（hw-modal 新 `hw-tools-row`）：三工具按鈕（active 高亮）＋六色 chips（dataset hex + `style.background` 直上，textContent/無 innerHTML=無 XSS 面）＋`<input type=color>` 自訂色＋寬度 slider 1–28＋數值 label；**per-tool config**（`hwCfg={pen:{#000,3}, marker:{#ffe94a,14}, eraser:{w:24}}`——切工具帶回該工具上次色寬；chips/picker/slider 編輯「當前工具」的 cfg，eraser 忽略色）。`notesHwClear()` 同清筆劃記錄（防重放回魂）；`notesHwRestoreStyle()` 收筆復原中性態。**測試 +2**（source guard：模型/配置/重繪序/增量段/clear 連清/chips dataset/HTML tokens/CSS selectors＋i18n ×4）＝ handwriting 25＋notes_ui 17＋web_upload 35 = **77 passed**；node --check OK。pyproject 0.9.125.6 > **0.9.125.7**；web 頁面重整生效。

### v0.9.125.6  

**手寫支援匯入 PDF 編輯（user: 「手寫 支援 匯入 PDF 編輯」＋同訊追加「需支援 eraser、marker pen (多色)、pen (多色調整、可調粗細)」——後者屬 v0.9.125.7）。** **①依賴**：`pymupdf>=1.24.0` 進 pyproject（pypdf 不能 rasterize；live 裝 1.28.2）。**②新 `Api.notes_handwriting_pdf_pages({path})`**（`_render_pdf_pages` 拆出）：`uploads/` roots 界定＋相對路徑錨定 write root＋`.pdf` 後綴＋`%PDF-` magic＋50 MB cap＋roots containment；逐頁 `get_pixmap(dpi=150)` → PNG 歸檔 `$QDRANT_RAG_HOME/handwriting/pdf-<ts>/pNN.png` → 回 `pages[{index,path,data_url,w,h}]`（in-memory data URL=GUI/web 通吃，同 `read_handwriting_image` 構型）＋`pages_total`/`truncated`；**頁數 cap 10**＝perPage 歸檔 cap 對齊；單頁 base64 cap 3 MB（超=跳過+log）；缺 fitz=「pip install pymupdf」明確提示。**③前端**：hw-row 新「📄 匯入 PDF」＋hidden `hw-pdf-file`；web 通路=`POST /upload`（復用 30s Abort/離線預檢/上傳管線）→ path → host render；GUI 通路=`pywebview.create_file_dialog(OPEN)` 原路徑直進。**新狀態 `hwPageBgs[]`（逐頁背景）＋`hwSrcPdf`**；`notesHwInitCanvas()` 背景鏈＝頁面 underlay（**contain-fit** 全頁可見比例正）→ 全域筆記本 bg（cover 不變）→ 白底；匯入後 pad 重置=每 PDF 頁一張空白墨跡頁；墨跡/翻頁/縫合/歸檔/索引管線**零改**（複合模型自然烘焙）；`hw-bg-clear` 同清兩類背景。**④語義**：save payload 帶 `source_pdf`/`has_pdf_bg` → note body 附「📄 底稿：<pdf path>」＋OCR prompt 條件尾綴「只轉寫手寫墨跡、忽略印刷體」（prompt 變動=SHA1 快取自然分流）。**tests +9**（`TestApiHandwritingPdfV0125_6` ×7 行為級——真 fitz 造 PDF：全鏈 render/相對路徑/守衛簇[後綴/missing/bad magic/outside-roots/檔案 cap]/頁 cap 誠實截斷/缺裝提示/底稿溯源+OCR hint 正反例——roots 用窄 sandbox+patch `_allowed_file_roots` 防 live WRITE_ROOT 蓋測试 tmp；前端守衛 ×2）= **23 passed**；node --check OK。pyproject `pymupdf>=1.24.0` 新依賴；0.9.125.5 > **0.9.125.6**；web 頁面重整生效。

### v0.9.125.5  

**遠端電腦上傳「沒有 [已上傳] 路徑」真兇＝插入標記跟介面語言飄（user: 「遠端電腦使用 web ui 上傳檔案 不會出現 [已上傳]…但在本機上使用 web ui 會出現」）。** **實證鏈**：①log 顯示遠端上傳**都到了主機**（`upload: saved N`）且 14:33 那回合送出的 q 開頭就有**兩行路徑**——插入鏈一直在跑；②app.js 的插入用 `t('uploadMark')`＋**預設語言 = `navigator.language`** → 遠端（簡中/英文環境）實際插的是 **`[已上传]`／`[uploaded]`**——找「已上傳」繁體字樣＝看不到；`_bgUrlFor`/`renderSandboxImages` 的行渲染認的是**路徑 token**（非標記字樣）→ 唯一受影響的是**人在輸入框看到的字樣**。**修**：**插入標記升格為協定 token**——新 IIFE 常數 `UPLOAD_MARK = '[已上傳]'`（語言無關、跨裝置/log 比對/LMM 一致），`uploadFiles` 與 `_insertUploadPath` 兩插入點全改用；i18n `uploadMark` key 保留（4 語系、渲染契約不破）。**測試強化**：jsdom driver 的 `t` stub 改**語言中立**（`'[L:key]'`）——插入結果仍必須 `[已上傳] C:/…`＝反證解耦；新增 source guard（兩插入點禁 `t('uploadMark')`、常數存在、key ×4 保留）；UPLOAD_MARK 宣告置於 uploadFiles 正上方（TDZ 狀態由 jsdom driver 抽段錨點含宣告驗證）。tests +1 = 35 passed；node --check OK。pyproject 0.9.125.4 > **0.9.125.5**。

### v0.9.125.4  

**「[已上傳] 路徑未出現」根因定位與實證（user 釐清：「之前的問題是上傳後前端UI沒有出現 [已上傳]檔案路徑」）。** **jsdom 重放實證（新增測試）**：抽出 app.js 的**真實 `uploadFiles`**於 jsdom + server 同形回應（`{ok,files:[{path,…}],skipped}`）重放全鏈 → **`[已上傳] C:/…` 正確落 `#chat-input`**（`test_upload_inserts_mark_jsdom`，含 fetch URL 斷言；driver 修訂：jsdom `File` 真形假物 + globals 綁定）——**現行插入邏輯無缺陷**。**現場案例成因收斂二選一**：①裝置端吃**舊版 app.js 快取**（v0.9.120.1 正是 Excel 插入修復；`?v=<pyproject 版本>` cache-bust 已存在——重啟 web server 後版號 URL 變化強制重抓；手機再硬重整/重開分頁）；②**上傳回程斷線**（手機送出後切背景/訊號瞬斷——server 14:06 實錄收檔成功但回應到不了瀏覽器→無插入也無 toast；已由 125.3 的 30s 硬逾時＋fail toast 讓它**必現回饋**）。兩者皆已被 125.3 修復涵蓋；測試 +1。pyproject 0.9.125.3 > **0.9.125.4**。

### v0.9.125.3  

**「Excel 或檔案按了上傳沒有反應」修复（user 回報；決定性分診實證）。** **分診**：curl.exe 真實 multipart `POST /upload` → `ok:true` + 檔案落地（server ✓）；今天 14:06 log 也有三筆成功 upload——**問題在前端**。**三個修**（gui/assets/app.js）：①**立即回饋**——`uploadFiles` 選檔後立刻 `toast('⏳ 上傳中… <檔名>' )`（新 i18n `uploading` ×4——以前 fetch 懸著時零視覺回饋＝「沒反應」體感）；②**fetch 硬 30s 逾時**（`AbortController`，慢網/半連線不再無聲永挂——任何失敗必浮出 uploadFail toast）；③**離線預檢**（`navigator.onLine === false` → 📡 離線提示不再送）。④**綁定順序防禦**——`bindUpload/bindMic/bindPaste/bindDrop` 提前到 `bindEvents()` **最前**（此前排在 bindEvents 中段——任何前面 toolbar 元素缺失的 TypeError 會中斷整段、📎 變零 listener＝「按了沒反應」的另一可能根因）；四個 binder 全部加 `_done` 冪等守衛（雙呼不雙綁＝不重複 POST）。**tests +2** `tests/test_web_upload.py`（feedback guard：uploading toast/AbortController/30s/offline 檢查/early bindEvents 頭/四 binder 冪等 + uploading ×4 語系）→ web_upload + notes_ui + turn_panel 合併 **60 passed**；node --check OK。pyproject 0.9.125.2 > **0.9.125.3**；web 頁面重整生效。**（若重整後仍零 toast=按鈕本身沒渲染——重啟 web server 後再試；GUI 桌面版上傳 UI 本來就隱藏 by design——改用 Pick file…/文件 ingest。）**

### v0.9.125.2  

**全螢幕改純 CSS（user: 「全螢幕 不是所有物件都放大，而是所有物件大小都不變，變的只有寫手的地方變大」）。** 根因＝Fullscreen API 把整個 94vw 的 top-layer 元素**視覺放大**到螢幕（字體/按鈕全部跟著縮放）。修＝**卸除 `requestFullscreen/exitFullscreen`**，⛶ 改純 CSS `.hw-full` 切換——同一份字體/按鈕/間距完全不变，「變的只有寫手的地方」（canvas 高度 100vh-150px）；rAF 重初始化 canvas 位圖保留。守衛反轉：`requestFullscreen/exitFullscreen` 不得再出現（tests/test_notes_handwriting.py 全綠 31 passed、node --check OK）。pyproject 0.9.125.1 > **0.9.125.2**；web 頁面重整生效。

### v0.9.125.1  

**手寫 v2：全螢幕／多頁／筆記本背景圖／可看圖 ＋ Android 手寫筆延遲修（user: 「1. 手寫支援全螢幕模式、多頁(可點加頁)、可使用筆記本背景圖、可看圖 2. 使用 android pad的手寫筆 筆劃會delay」）。** ①**全螢幕**：`⛶` 鈕（head×2）→ `.hw-full` CSS（box 100vw/100vh、canvas 100vh-150px）＋ Fullscreen API best-effort（不支援時 CSS 模式照用）；關閉時 `exitFullscreen`。②**多頁**：`➕ 加頁`＋`←/→` 翻頁＋`第 {p} / {n} 頁` 指示——每頁 dataURL 存 `hwPages[]`；送出＝**逐頁墨跡掃描**（空白頁誠實丟棄）→ 多頁時**垂直縫合成單一 PNG**（單歸檔+單 OCR），逐頁 PNG 以 `pages[]` 一併歸檔為 `hw-<ts>-pNN.png`（Api `notes_handwriting_save` 新 `pages` 參數：逐頁驗 magic、cap 10、壞項 skip；回傳 `pages` 清單）；筆記內文互指縫合主圖。③**筆記本背景圖**：`🖼 背景圖`（file picker，png/jpeg）→ cover-fit 繪於 canvas 底層（**烘焙進送出的 PNG**——OCR 看得到模板/表格內容）；`✕ 去除背景` 選鈕 + 按鈕 active 態。④**可看圖**：編輯器內嵌預覽——筆記內文含「🖼 手寫原文：<路徑>」時 `notesHwShowArchived` 呼叫**新 Api `read_handwriting_image`**（hdir `resolve()` 界定 + 後綴白名單 + 8 MB cap，拒絕任何 traversal＝測試實證 `C:/Windows/win.ini`/`../` 均拒）→ data-URL `<img>` 內嵌；`notesClose` 清預覽。⑤**手寫筆 delay 修**：`pointermove` 只吃每幀一點＝Android pad 高頻 stylus 樣本丟失 → 筆劃落後；現以 **`getCoalescedEvents()`** 補繪幀間全部合成點（合成事件逐點鏈式 quadratic 平滑）——經典反延遲正解。**i18n ×4×8 新 keys**（hwPageOf/hwPageAdd/hwFull/hwBg/hwBgClear/hwBgApplied/hwBgCleared/hwViewOriginal）。**tests**：TestHandwritingFrontend +3（多頁/背景/全螢幕/看圖守衛、i18n v2 ×4×8、stylus 合成事件守衛）+ TestApiHandwriting +3（pages 逐頁歸檔/壞頁 skip/read_handwriting_image 界定+traversal 拒絕）→ handwriting **14 + notes_ui 17 = 31 passed**；node --check OK。pyproject 0.9.125.0 > **0.9.125.1**；web 頁面重整生效。

### v0.9.125.0  

**手寫記事管線（user: 「web ui支援手寫記事，傳至主機再進行ocr 建index和歸檔儲存至主機」）。** 一條四站管線：**✍️ 畫 → 🏠 存 → 🔍 轉寫 → 🔎 索引**。①**前端 canvas**（Notes 工具列「✍️ 手寫」按鈕 → `#hw-modal` 疊層）：Pointer Events 手繪（滑鼠/手指/觸控筆；`setPointerCapture` + quadratic 中點平滑）＋devicePixelRatio 縮放＋`touch-action:none`（手機滑動不搶筆）＋空畫面像素守衛＋resize 誠實重初始化；送出＝`canvas.toDataURL('image/png')` → `notes_handwriting_save({data_url, title?})`。離線誠實拒絕（「離線狀態無法手寫建檔」——OCR 需主機，不塞 pending 隊列避免巨型 base64 塞爆 localStorage）。②**`Api.notes_handwriting_save`**：(1) **歸檔 PNG** 至 `$QDRANT_RAG_HOME/handwriting/hw-<ts>.png`（magic 驗 PNG/JPEG、8 MB cap）；(2) **OCR**＝專案 vision 鏈 `ImageDescriber.describe(prompt=_HW_OCR_PROMPT（繁中逐字轉寫、保段行、僅出文字）, max_tokens=1800)`——鏈序 VISION_PROVIDER_CHAIN（ollama→openrouter→siliconflow→openai-compat）+ SHA1 快取 + `ocr_backend` 回報 + marker/失敗如實降級；標題 fallback＝OCR 首行→`手寫筆記 <ts>`；(3) **歸檔筆記**＝`local_create`（分類預設「手寫」）且內文末附「🖼 手寫原文：<png path>」互指；(4) **建 index**＝伴隨檔 `hw-<ts>.txt`（標題+轉寫）走**與文件 tab 同一條** `ingest_file`（chunk→embed→RAG collection）——語意搜尋以「內容」命中手寫記事；`indexed_chunks` 回報。③UI：送出後 `notesOpen(r.id)` 直接打開轉寫筆記複核/修改。**i18n ×4×10 keys**（notesHw/hwTitle/hwHint/hwClear/hwSave/hwOcring/hwDone/hwEmpty/hwOffline＋notesTitlePh 沿用）。**tests +8** `tests/test_notes_handwriting.py`（Api 行為 stub：全管線/OCR marker 誠實降級/標題取首行/四種驗證錯/RAG home 隔離；前端源碼守衛：繪圖機制/dataURL 送出/html+css/i18n ×4）→ handwriting 8 + notes_ui 17 = **25 passed**；node --check OK。pyproject 0.9.124.9 > **0.9.125.0**；web 頁面重整生效。

### v0.9.124.9  

**Notes 筆記本 web UI 離線編輯與暫存（user: 「web ui筆記本 是否可以支援離線 編輯和暫存，等上線在存到主機端」）。** **範圍：web 模式限定**（`window.__WEB_MODE__` gate——pywebview 桌面橋即主機程序、無離線概念，行為零改動）；**後端零改動**（離線新建走「上線補建」語意：server 在同步時才發 id，解決 client 造 id 與 `local_update` 找不到即 error 的衝突）。**四層 localStorage**：①`notes_cache_v1`——每次 `notes_page` 成功即暫存整個列表 payload；②`notes_pending_v1`——FIFO 暫存隊列 `{op:'save'|'create'|'delete', payload, edited_at}`（cap 500）；③`notes_conflict_backup_v1`——同步覆寫前先把 server 現值 `notes_get` 存進 20 環備份（誤覆蓋恆可手動找回）。**前端行為**：`notesLoad` 三路分派（成功→渲染+更新暫存；`r===null`（橋接不通＝`safeCall` 約定）→ **離線模式**渲染 `notes_cache_v1` + pending 離線新建（`pending:<i>` 佔位 id）＋banner；`r.error`→原樣）；`notesOpen` 離線時從 cache/pending 餵編輯器；`notesSave`/`notesDelete` 於 `r===null` 時 enqueue（對 pending:<i> 的保存/刪除＝原地更新/移除隊列項）＋📦 toast；**同步**：`notesOfflineSync()`——FIFO 重放（save→先備份衝突→save；create→空 note_id；delete→delete），probe `notes_categories` 判在線，全部完成/殘留誠實計數；**觸發**：`window online` 事件自動 + 工具列「🔄 同步離線編輯（N）」手動按鈕（web-only 顯示、N=待同步數）；離線 banner `#notes-offline-banner`（📡「離線模式—顯示 localStorage 暫存的列表」）。**HTML/CSS**：`#notes-sync-btn`（hidden 預設，JS 管顯隱+N 標籤）＋banner div（`.notes-offline-banner` warn 色 + `[hidden]` 覆寫慣例）。**i18n ×4×7 keys**（notesSyncBtn/notesSynced/notesOfflineBanner/notesOfflineStash/notesStillOffline/notesUpToDate/notesLoadOffline2——zh-TW/zh-CN/en/ja）。**tests +2** `tests/test_notes_ui.py`（`test_notes_offline_first_layer` 源碼守衛：三 keys/九函式/WEB_MODE gate/notesCacheGet 分支/pending:id/online-offline 事件/html+css + `test_notes_offline_i18n_four_locales` ×7 keys 計數）→ **17 passed**；node --check OK；notesLoad 保持 paged（不整表傾倒）與 textContent-only 慣例。pyproject 0.9.124.8 > **0.9.124.9**；web 前端即時生效（硬重整頁面即可），GUI 桌面版不受影響。

### v0.9.124.8  

**中止鈕 UI 鎖死修復（user: 「在進行 prompt 時按下 中止鈕 只會出現 中止訊息 UI就lock」）。** **診斷（app.log 23:43–23:48 實錄）**：stop 的中斷點=**下一個 tool-loop 邊界**（worst case = 一次 in-flight 的長 LLM 呼叫——本機實錄 93–377 s 級：300 工具面 prefill + together 上遊 504 等滿 timeout）；按 🛑 後前端只把面板文字換成「已送出中止請求…」並繼續等終態——等真終態的幾分鐘裡 sendBtn 保持 disabled = 使用者體感的「UI 就 lock」。後端終態鏈是健全的（`start_turn._work` done/stopped/error 三路都有 `_te.finish`；log 亦見 stop 生效「turn ends here」＋ TurnTiming 正常落）——問題純在等待期的 UX。**修（前端 `pollTurnStream`，v0.9.124.8）**：**abort patience 機制**——🛑 按下記 `abortRequested`/`abortSentAt`；若 **20 秒**（`_ABORT_PATIENCE_MS`）後終態仍未到：面板文字改 **`turnAbortPending`**（誠實敘事：「後台回合仍在收尾（長 LLM 呼叫可能需 1–3 分鐘），可先送新訊息」）+ **立即 `onRelease()` 解鎖輸入**；輪詢**不停止**——真終態（stopped/done）到時照常渲染 assistant 泡泡 + turnLog + autoSaveChat。**併發安全已驗證**：提前解鎖後再送的新 prompt 由 `turn_gate` 處理（cap 未設=並行；有設=bounded 排隊 120s→誠實 `TurnGateBusy` 錯誤氣泡——兩路都不會鎖 UI）。**i18n ×4**（turnAbortPending：zh-TW/zh-CN/en/ja）。**tests +2** `tests/test_turn_panel.py`（abort patience 源碼守衛：常數/三狀態名/解鎖分支形狀 + i18n ×4 計數）→ 10 passed；node --check OK；turn 系回歸 20 passed。pyproject 0.9.124.7 > **0.9.124.8**；RESTART（GUI/web）required。

### v0.9.124.7  

**MCP fleet 清單稽核（user: 「前面的幾個mcp都沒有加入mcp active清單中，請檢查並加入」）。** ①**查證結論**：live `.env` 的 `MCP_ACTIVE_SERVERS` **本來就完整**——七件 craft（photocraft/pdfcraft/rea/gridcraft/soundcraft/wordcraft/deckcraft/filmcraft）全在，且 gate 每個遠端名都有 `config/mcp.json` 條目（交叉檢查 `MISSING: []`）——使用者若在 GUI/web「現行 Active 清單」沒看到新件，是 **RESTART required**（程序開機時載入舊 .env，v0.9.13.2 契約：未列=hidden）；重啟或 Admin tab Save 熱重連即生效。②**真缺口修復**：`.env.example` 的註釋 gate 行停在 `…,snippets-local,rulebook-local`——124.2–124.6 的六件只同步了 live `.env`，`.example` 漏了 pdfcraft/rea/gridcraft/soundcraft/wordcraft/deckcraft/filmcraft 七個 → 補齊（「.env.example in sync」紀律收案）。③**同場 E2E 暴露並處理新議題——多 fleet 撞名**：7 個 craft server 同開時，泛用工具名（list_commands/command_list/run/batch/doc_*/ui_*/screenshot）跨家重複，manager 自動 rename 保護（`server__tool`，裸名歸先註冊者，live mcp.json 順序＝my_web→…→photocraft→pdfcraft→gridcraft→…→filmcraft）→ **system prompt 新增「MULTI-FLEET NOTE」節**：目錄（list_mcp_tools）為唯一權威、共享動詞一律用目錄所示名字（優先 server 前綴版）打對象、「裸 command_list 不默認屬於 FilmCraft」。④**fleet E2E 實證**（7 craft 同場 + live gate）：`FAILED:{}`、`HIDDEN_CRAFT: []`、暴露數 deckcraft 25/filmcraft 17/gridcraft 21/pdfcraft 17/photocraft 21/soundcraft 17/wordcraft 16；spot dispatch wordcraft/list_commands、deckcraft/list_commands、filmcraft/command_list、gridcraft/list_functions、soundcraft/inspect_session 全 OK（pdfcraft「doc_list Unknown」為**正確**行為——compact 核心集無此工具，走 tool_search；probe 例選錯非缺陷）。tests：toolroute 回歸。pyproject 0.9.124.6 > **0.9.124.7**；RESTART required。

### v0.9.124.6  

**FILMCRAFT MCP 上線（user: 「協助設定MCP filmcraft `C:\Program Files\FilmCraft\filmcraft-cli.exe`」；ArtCraft 系第七件，GRIDCRAFT 124.2 同款簡配模式）。** ①**mcp.json 條目**：`filmcraft` stdio = `"C:\Program Files\FilmCraft\filmcraft-cli.exe", "mcp"`——probing 實證：serverInfo **filmcraft 0.7.2**、**17 工具**：核心面（泛用指令系統 command_list/**command_run**/command_batch——工具描述自稱「This is how everything is done」＝timeline/effects/bin 全走 command id；`media_import`（MP4/MOV/WAV/MP3/FLAC/PNG/JPEG 絕對路徑）；doc/project/sequence_inspect（tracks、clips ticks/frames）；render_frame/render_preview（PNG 逐格目視））+ **ui_* ×9 「Live app:」標記**（ui_click/ui_drag/ui_elements/ui_key/ui_screenshot/ui_type/ui_control/ui_inspect——需 `--bridge 127.0.0.1:PORT` 橋接 live app）。**17 < 128 cap 無 roots 無 compact**；注意本件 CLI 的 `--compact` 是輸出格式選項**非**工具精簡模式（與 pdfcraft 不同）；`--project/--demo/--save-as` 為 CLI headless 參數未納入 MCP 條目。②ToolRoute 觸發列（union-only，窄集）：`filmcraft|\.fcproj|剪輯|剪片|時間軸|時間線|混剪|逐格|\bnle\b|匯出 (mp4|mov|prores|h264|hevc)|\brenderVideo\b` → filmcraft——**「影片」刻意不入列**（media_embed 影像嵌入情境會誤掛），三例實測：`素材剪輯成 mp4` ✓、`filmcraft 時間軸` ✓、`影片影像嵌入搜尋`（media 情境）✗ 正確排除、不誤傷 deckcraft。③system prompt「FilmCraft video/NLE workstation」節：headless 全走泛用指令面（list→run/batch→import→inspect→render PNG 目視）+ ui_* 9 工具 LIVE-APP 說明 + **無 root 誠實規則（SAVE/EXPORT 進 write 沙盒回報路徑，不假造視覺）**。④**E2E 驗證**：暫存最小 config from_config 真連線 —— `FAILED:{}`、17 tools 註冊、`command_list` 回全套指令註冊表（file.newProject…）、`doc_inspect` 回 headless 空專案 JSON（Untitled/revision 1——引擎自動開工作區）。**RESTART GUI/web 生效**。pyproject 0.9.124.5 > **0.9.124.6**；RESTART required。

### v0.9.124.5  

**DECKCRAFT MCP 上線（user: 「協助設定MCP deckcraft `C:\Program Files\DeckCraft\deckcraft-cli.exe`」；ArtCraft 系第六件，GRIDCRAFT 124.2 同款簡配模式）。** ①**mcp.json 條目**：`deckcraft` stdio = `"C:\Program Files\DeckCraft\deckcraft-cli.exe", "mcp"`——probing 實證：serverInfo **deckcraft 0.6.1**、**25 工具**（簡報流 new_presentation（主題 DeckCraft/Harbor/Ember/Meadow/Nocturne/Paper/Coral Reef…）/open_presentation（.deckcraft/**.pptx**）→ add_slide（layouts title/titleAndContent/sectionHeader/twoContent/comparison…）→ add_shape（presets rect/ellipse/arrow/chevron/star…）/add_text_box/set_text/insert_picture/insert_table/**insert_chart**（column/bar/line/pie/doughnut/area/scatter…）→ inspect_slide（shapes ids/boxes/text/fills/tables/charts）/**render_slide**（PNG 目視檢查）→ save_presentation / **export**（pptx/pdf/png/jpeg/outline）+ 泛用面 list_commands/**run_command**/batch + 手繪面 pointer（slide 座標 down/drag/up）/select_tool（pen/highlighter/eraser）/key/type_text + **APP_ONLY ×2**（screenshot/call_app 需 `--connect` 桌面綁定）。**25 < 128 cap 無 compact、無 automation-roots**（路徑沙盒由 prompt 紀律承擔）。②ToolRoute 觸發列（union-only，窄集）：`deckcraft|\.deckcraft|\.pptx|\bpptx\b|\bppt\b|powerpoint|簡報|投影片|幻燈|簡報主題|slide ?deck` → deckcraft——三例實測：`報告做成 .pptx 簡報` ✓、`deckcraft 開投影片` ✓、`總結這份文件` ✗（且不誤傷 wordcraft）。③system prompt「DeckCraft presentation workstation」節：DECK 流 + 泛用面 + **手繪 slide 座標說明（pointer/select_tool/pen）**+ headless 誠實規則（不假造視覺，交付 render_slide PNG 或 export 檔案——進 write 沙盒）。④**E2E 驗證**：暫存最小 config from_config 真連線 —— `FAILED:{}`、25 tools 註冊、`list_commands(filter=slide)` 回 enabled 狀態文檔、`inspect_document` 未開簡報時**如實報錯**「no presentation open」（fail-honest ✓——LLM 收到錯誤會先 new_presentation）。**RESTART GUI/web 生效**。pyproject 0.9.124.4 > **0.9.124.5**；RESTART required。

### v0.9.124.4  

**WORDCRAFT MCP 上線（user: 「協助設定MCP wordcraft `C:\Program Files\WordCraft\wordcraft-cli.exe`」；ArtCraft 系第五件，PDFCRAFT 124.1 同款 automation-roots 模式）。** ①**mcp.json 條目**：`wordcraft` stdio = `"C:\Program Files\WordCraft\wordcraft-cli.exe", "mcp", "--automation-read-root", "C:/Users/humphery", "--automation-write-root", "C:/Users/humphery"`——probing 實證：serverInfo **wordcraft 0.7.0**、stderr banner「files confined: read/write root …」（**automation roots 生效**——省略即不授權，與 photocraft 同哲學）；**16 工具**（文檔流 new_document（blank/letter/resume/report）/open_document（.docx/.doc/.odt/.rtf/.md/.tex/.html/.txt/.json 或任意文字檔）/save_document（格式隨副檔名含 pdf/png）/type_text（caret；`\n` 跳行）/select_text/get_text/inspect_document/render_page + 泛用面 list_commands（`query` 過濾）/**execute**/batch + desktop-only ×4（screenshot/click/key/ui_inspect 需桌面 App——描述自帶標記））。**16 < 128 cap 無 compact**；`--connect HOST:PORT` = bridged-to-app 模式不用；另有 `zotero` 書目子命令（CLI 面，需 Zotero 在跑，未納入 headless config）。②ToolRoute 觸發列（union-only，窄集）：`wordcraft|\.docx|\.doc\b|\.odt|\.rtf|word 文件|word 檔|文書|\bdocx\b|追蹤修訂|頁首頁尾|\bdoc\b` → wordcraft——**「文件」中文泛詞不入列**（防「總結這份文件」誤掛），三例實測：`開 report.docx 加追蹤修訂存成 pdf` ✓、`用 wordcraft 寫一份函` ✓、`總結這份文件的重點` ✗ 正確排除。③system prompt「WordCraft word-processor workstation」節：DOCUMENT 流（new/open→type/select/get/inspect→save 進 write 沙盒回報路徑）+ list_commands/execute/batch 泛用面 + **desktop-only 誠實規則（不假造視覺，交付存檔或 render_page PNG）**。④**E2E 驗證**：暫存最小 config from_config 真連線 —— `FAILED:{}`、16 tools 註冊、`list_commands(query=table)` 回完整參數文檔、`parity` → **487 指令 97% 覆蓋率**。**RESTART GUI/web 生效**。pyproject 0.9.124.3 > **0.9.124.4**；RESTART required。

### v0.9.124.3  

**SOUNDCRAFT MCP 上線（user 確認 ArtCraft 系列收尾；GRIDCRAFT 124.2 同流程）。** ①**mcp.json 條目**：`soundcraft` stdio = `"C:\Program Files\SoundCraft\soundcraft-cli.exe", "mcp"`——probing 實證：serverInfo **soundcraft 0.6.1**、**17 工具**（session 流：new/open(.scraft)/save_session、inspect_session（track/bus/marker/transport 全 JSON）、`import_audio`（WAV/AIFF/FLAC/MP3/OGG/AAC/ALAC/CAF 上軌）、`bounce_mix`（回 peak dBFS + 整合 LUFS）+ 泛用面 list_commands/execute/batch + **APP_ONLY ×8**（screenshot/ui_inspect/ui_set/menu_invoke/click/drag/key——需 `--connect` 桌面綁定；工具描述自帶 APP_ONLY 標記）。**無 --root 無 --compact**；本專案跑 headless（不加 --connect），路徑沙盒由 prompt 紀律承擔。②ToolRoute 觸發列（union-only，**窄集設計**）：`soundcraft|\.scraft|\bscraft\b|混音|多軌|音軌|\bmixdown\b|\bbounce\b|\blufs\b|匯出 (mp3|wav|flac|ogg)|音訊後製` → soundcraft——**「音訊」刻意不入列**（v0.9.122.1 media_embed 音訊嵌入情境會誤掛），三例實測：`混音後匯出 wav` ✓、`soundcraft session` ✓、`音訊嵌入搜尋`（media 情境）✗ 正確排除。③system prompt「SoundCraft audio/DAW workstation」節：SESSION 流 + 泛用面 + **headless 誠實規則（不假造視覺/UI，交付 bounce 音檔/save session 路徑——進 write 沙盒）**。④**E2E 驗證**：暫存最小 config from_config 真連線 —— `FAILED:{}`、17 tools 註冊、`list_commands` 回全套指令註冊表、`inspect_session` 回 headless 空會話引擎狀態（Int24/30fps/Slip 正常）。至此 **ArtCraft 四件套（Photo/Pdf/Grid/SoundCraft）全數接線完成**。**RESTART GUI/web 生效**。pyproject 0.9.124.2 > **0.9.124.3**；RESTART required。

### v0.9.124.2  

**GRIDCRAFT MCP 上線（user: 「協助安裝 MCP gridcraft `C:\Program Files\GridCraft\gridcraft-cli.exe`」；PDFCRAFT 124.1 同流程）。** ①**mcp.json 條目**：`gridcraft` stdio = `"C:\Program Files\GridCraft\gridcraft-cli.exe", "mcp"`——probing 實證：serverInfo **gridcraft 0.6.1**、stderr banner「headless (in-process engine, no window)」；**21 工具**（open_workbook 這 .xlsx/.xlsm/.xls/.xlsb/.ods/.csv/.tsv/.json/read_range/write_range/set_cell（=公式重算）/get_cell/evaluate_formula（不動格）/select/format_range/insert_chart/create_table/sort_range/filter/save_workbook（格式隨副檔名）/new_workbook/undo/redo/screenshot/list_functions + 泛用面 **list_commands**（每個 ribbon 指令 id+參數文檔）/**execute_command**）——**21 < 128 cap，無 compact 概念，全量上 schema**；`mcp [--connect PORT]` 的 connect = 綁桌面 App（screenshot 用），本專案跑 **headless**（不加）；GridCraft CLI 無 `--root` 參數——路徑沙盒改由 prompt 紀律承擔（「存檔一律進 write 沙盒並回報路徑」）。②ToolRoute 觸發列（union-only）：`gridcraft|\bxlsx\b|\.xlsx|\.xlsm|\.xlsb|\.ods|\bexcel\b|試算表|活頁簿|工作簿|儲存格|vlookup|樞紐分析` → gridcraft（**csv 不入列**——資料 csv 走 exec-local/filesystem 才對；排序/篩選等動詞不入列防過度觸發）。③system prompt「GridCraft spreadsheet workstation」節：OPEN→compute→SAVE 動線 + list_commands/execute_command 泛用面 + headless 誠實規則（**無視窗不假造截圖、交付存檔路徑**）。④**E2E 驗證**：暫存最小 config from_config 真連線 —— `FAILED:{}`、21 tools 經 StdioClient 註冊、`list_functions(search=VLOOKUP)` 命中 Lookup 類、`inspect_workbook` → Ready 引擎狀態；toolroute 觸發三例實測（xlsx/gridcraft ✓、誤傷 pdfcraft ✗、無檔名排序不掛 ✓）。**RESTART GUI/web 生效**。pyproject 0.9.124.1 > **0.9.124.2**；RESTART required。

### v0.9.124.1  

**PDFCRAFT MCP 上線（user: 「協助設定 mcp pdfcraft `C:\Program Files\PdfCraft\pdfcraft-cli.exe`」；photocraft v0.9.119.1 同款模式）＋ `.env` gate 修回 `rea`。** ①**mcp.json 條目**：`pdfcraft` stdio = `"C:\Program Files\PdfCraft\pdfcraft-cli.exe", "mcp", "--compact", "--root", "C:/Users/humphery"`——probing 實證：`mcp` 子命令 = stdio JSON-RPC server（serverInfo pdfcraft 0.7.1；banner 走 stderr 不污染 stdout）；完整面 **161 工具**（doc/ocr/redact/sign/form/comment/bookmark/measure/accessibility/pdfa…）**超過 `MCP_SURFACE_MAX_TOOLS` 128 cap** → 必用 `--compact`：核心 17 工具（doc_open/save/combine/split/info/ins、text_extract/find、page_render、command_list/run/batch、edit_undo）+ **`tool_search`/`tool_call` 兩段式 on-demand**（tool_search 回 29 類別 + 命中名單與描述；tool_call 直接回展平 JSON——E2E 實證 `tool_search("watermark")` 命中 doc_watermark/doc_remove_marks、`tool_call("doc_list")` → `{"documents": []}`）。`--root` 對齊 photocraft 的沙盒哲學。②**ToolRoute 觸發列**（union-only，photocraft 同款）：`pdfcraft|\.pdf|pdf 文件|pdf 檔|ocr|文字辨識|redact|遮蔽|浮水印|電子簽|簽署|表單填|合併 pdf|拆分 pdf|頁碼|pdfa` → `pdfcraft`。③**system prompt「PdfCraft PDF workstation」節**：核心集 + tool_search 先行（「不得在未 probe 前宣稱缺功能」誠實規則）＋ tool_call 動線 + `--root` 路徑界。④**`.env` gate 行修回 `rea`**（v0.9.124.0 誤把 live `.env` 行尾 `,rea` 當殘段清掉——config/mcp.json 裡 `rea` 是真 server（binary 逆向 agent，E2E 顯示其 tools 正常連回）；現行行 = `…,rulebook-local,pdfcraft,rea`）。⑤**E2E 驗證**：暫存最小 config from_config 真連線 —— `FAILED:{}`（pdfcraft+rea 全連上）、pdfcraft 17 tools 經本專案 StdioClient 註冊、`command_list(filter=watermark)` 派發 OK、兩段式全通。**RESTART GUI/web 生效**（或 Admin tab Save 重連）。tests：既有 toolroute 套件回歸（triggers 加列 union-only 不破壞 photocraft 逆向斷言）。pyproject 0.9.124.0 > **0.9.124.1**；RESTART required。

### v0.9.124.0  

**Self-evolving-agent 譜系報告落地（user 核准「全部 P1+P2+P3 一次到位」；報告 = `~\notes\self-evolving-agent-譜系.md`，DeepRSI / Memento 3 / Evo² 三層 + 9 條建議。對照結論：報告首選 ③證據門與 ⑤checkpoint 本專案 v0.9.106–111 已落地；①規則簿 ④版本化是淨缺口 = 本版核心）＋ git 首次 commit（P0：repo 原本 0 commit；`.gitignore` 建全檔——`.env`/密鑰/logs/pycache/沙盒/雜物排除，**修復插曲：一次 over-broad `git add -A` 曾把 `google_oauth/client_secret_*.json` 等掃入暫態 commit，已 hard-reset 全數收回**；baseline = `c2189c7`（319 檔））。**

①**Agent Rulebook（最大單一改進，Memento 3 rulebook工件借鏡）** — 新 `src/agent/rulebook.py`（zero-dep ~540 行）：agent 維護的可修訂規則檔 `$QDRANT_RAG_HOME` 同位的 READ sandbox `agent_rules.md`（`QDRANT_RULEBOOK_FILE` 覆寫），每條規則一個 `### [rN] 標題` 區塊（內文可標 `underspecified`；evidence/updated 欄位）；**空檔 = prompt byte-identical**。寫入 atomic（tmp+replace）且**寫前自動快照**到 `rulebook_history/` ring（`QDRANT_RULEBOOK_HISTORY_MAX` 預設 20）＝建議④ zero-dep 版本化（`rules_history` 附 unified diff、`rules_rollback` 回滾前先備份現況）。**證據門**（Evo²）：`rules_revise(rule_id, text, evidence)` — evidence 必填（<4 字元=拒絕 fail-closed），add 鼓勵附。**RulebookLocalHost**（server `rulebook-local`，5 工具 rules_list/add/revise/rollback/history；寫入全 confirm 閘）+ stdio 鏡像 `src/mcp_server/rulebook_server.py` + manager from_config 雙掛點（import 元組+註冊，v0.9.116.1 教訓照辦）+ **`_TOOLROUTE_CORE_SERVERS` += rulebook-local**（工具失敗回合的 hint 需要同回合工具面可用——Jev 無法路由類別）+ MCP_ACTIVE_SERVERS gate 同步（live `.env` + `.env.example`；**順手修 `.env` 閘門行尾懸空殘段 `,rea`**）。**Prompt 注入**：`AGENT RULEBOOK` 區塊兩種 prompt flavor 都附（mtime 快取；`QDRANT_RULEBOOK_MAX_CHARS` 預設 4000 截斷保護）。**Prediction-error hook**（Memento observe→reflect→revise）：`Agent._run_tool_call` 的 result.is_error 分支附加一行修訂提示——**每回合至多一次**（ask/chat_turn 兩入口 reset `_rulebook_hint_shown`）、`QDRANT_RULEBOOK_SUGGEST=off` 退出、永不改變錯誤語意。**記憶_sink**：規則事件（add/revise/rollback）→ `remember_rule_event` fire-and-forget thread → memory `kind="rule-lesson"`（store-NOT-recall，`QDRANT_RULEBOOK_MEMORY=off` 退出）。CLI `python cli.py rules {list,show,add,revise,rollback,history}`。config.py 註冊 7 raw attrs。②**規則↔腳本雙層分診**（system prompt Script-library 區塊）：腳本失敗先分診「實作 bug→修腳本」vs「理解錯誤→修規則簿再重寫」，第三次同樣失敗=b 層漏診。③**Skill crossover**（Evo²）：`_write_handoff` 失敗 stage 的 handoff 檔帶 `recovery_hint`（skill_search 技能庫 adapt donor + rules 修訂建議）。④**多假設並存食譜**（Memento population）：system prompt TACTI 區塊 + SKILL_GUIDE §2.5 — 不確定方法編兩個並行 stage + 合裁 stage 讀雙 handoff 裁決。⑤**Reflector→Proposer 事後檢討**（Evo² 雙角色，**opt-in `QDRANT_REFLECT_POSTMORTEM=on` 預設 off**）：TACTI plan 以 failed/partial 收場 → 背景 post-mortem 回合（`skill_compiler._reflect_postmortem`；Reflector 讀失敗 session/handoff → Proposer 落實 rules_revise/rules_add 或修腳本建議；無證據不得修訂；task-registry 可見 `source="plan-postmortem"`；永不炸 driver）。**tests +58**：`tests/test_rulebook.py` ×28（core add/revise 證據門/rollback/history ring prune/cap/env off/hint 閘/host confirm 閘+LocalTool McpCallResult 解包/manager 行為級註冊/prompt 空=byte-identical 兩 flavor/hint 每回合一次/stdio parity/cli 守衛）+ `tests/test_rulebook_meta.py` ×15（handoff hint pass-through 600 cap/post-mortem gate 三分支+prompt 雙角色+崩潰不外洩/P2 源碼守衛 +.env.example/SKILL_GUIDE §2.5）+ 暫存煙雾腳本行為級全綠（wiring: add→prompt→hint once-per-turn）。**pyproject 0.9.123.0 > 0.9.124.0**；RESTART required。

### v0.9.123.0  

**回合即時 log（Turn Events）— web/GUI 執行 prompt 時即時看到該回合的執行過程（user: 「使用 web ui執行 prompt 可以看到該prompt 執行的即時log嗎，在web ui介面上，遠端操作也可以看的到」）— 答案 = 以前不行（`Api.chat_turn` 同步封鎖到底、執行細節只進 server 端 app.log），現在可以。** 四層零新依賴：①**新 `src/agent/turn_events.py`** — process 內 thread-safe ring buffer：`record(turn_id, kind, text)`（事件 ≤400 字、每回合 ≤120 件、超限改寫最後一條為上限注記）+ `finish(status, result)`（done/error/stopped 終態 + chat_turn 同形 payload：answer 截 8000/sources ≤20 條/usage 整數原值保留/深 dict 整數保留）+ `snapshot(turn_id, cursor)` 增量讀（next_cursor、壞 cursor 降級 0、never raises）+ LRU 8 回合。②**Agent 掛鉤** — `begin_turn_stream(turn_id, cb)`（**threading.local 按 thread 綁定** — Telegram poll/scheduler/webhook fire/task runner 併行各自獨立串流或不串流、零成本 no-op）+ `end_turn_stream` + `signal_turn_stop(turn_id)`（**回合級 scoped stop Event**，與 v0.9.120.0 task-registry stop 並存、只影響本 thread 的回合）+ `_emit_turn_event`；**`_tool_loop` 六節點發射**：llm_round（每輪）、tool_call（名+參數預覽 120 字）、tool_result（ok/err · ms · 字數）、wrap-up note、bail-out note、stopped。③**`Api` 兩段式（舊同步 `chat_turn` 契約原封不動** — CLI/背景任務零影響）：`start_turn(...)`（立即回 `{turn_id}`，daemon thread 跑回合、_get_agent 後掛串流、finally 清掛鉤、失敗落 error 終態）→ `poll_turn(turn_id, cursor)`（增量事件 + 終態 result，unknown = server 重啟）→ `stop_turn(turn_id)`（🛑 按鈕後端）。web shim `POST /api/<method>` 公開方法自動支援 = **遠端手機/LAN 裝置同一介面同一事件流**。④**前端（web + pywebview 共用 `sendChat`）** — 非背景分支改 `start_turn → pollTurnStream()` 輪詢 1.2s：可折疊「⚙ 執行 log」面板（textContent-only 無 XSS、時間戳+kind 圖示、標題點擊折疊）、**🛑 中止按鈕**、終態照舊渲染 assistant 泡泡（usage/tools_used 由 result 帶回、`msg.turnLog` 隨 session 存檔 → **歷史泡泡「查看執行 log」展開器**）；poll 連三失敗/unknown → 誠實「連線中斷」狀態；i18n ×8 keys ×4 語系；CSS `.chat-exec*` 全套。**測試**：`tests/test_turn_events.py` ×11（roundtrip/cursor 增量/截斷/cap/LRU/garbage/多 writer）+ `tests/test_turn_stream.py` ×14（hooks 序列/MagicMock name= 陷阱教訓入註解/scoped stop→TURN_STOPPED_TEXT/threading.local 隔離/Api roundtrip done+error/unknown/stop 形狀/同步契約守衛/web envelope）+ `tests/test_turn_panel.py` ×7（Api 面公開、兩段式管線、textContent 守衛、i18n ×4、CSS）+ 鎖定更新（send-time `'start_turn'`、`res.usage, res.tools_used`）= 新 32；**回歸 168 passed**（含 chat_autosave/gui_task_panel/chat_usage/chat_time_bar/chat_tools_used/web_server/mobile_web）。pyproject 0.9.122.2 → **0.9.123.0**；README/USER_MANUAL/ARCHITECTURE/CLAUDE 同步。

### v0.9.122.2  

**gemma2 媒體伺服器看門狗（user: 「gemma2 server 會自已帶起來嗎，像qwen embedding 4b一樣」）— 答案 = 之前不會，現在會。** 查證：4B 主 server（:8080）由 `EmbedWatchdog` singleton 帶（prewarm 啟動、真 POST 探針、refused 快路、zombie kill+restart）；gemma2（:8081）只有一次性的 schtasks wrapper（`c:\qdrant\_bat\startgemma2.bat` run-and-self-delete）與修好的 bat，死掉後媒體通路靜默劣化直到人工拉起。`src/embed_watchdog.py` 新 `Gemma2Watchdog(EmbedWatchdog)` 子類：**全套療傷引擎共用**（真實嵌入 POST 探針、2-streak、refused fast-heal、`netstat` 埠 PID kill、detached spawn、600s 冷卻 — 一組 `QDRANT_LLAMACPP_WATCHDOG_*` 旋鈕管兩隻）；差異面 = 探針 URL（`media_embed_url()` → :8081，localhost 改寫照舊）、start bat（`QDRANT_GEMMA2_START_BAT` 覆寫，預設 `C:\llama.cpp-un\start-llamacpp-gemma2.bat`）、thread 名 `gemma2-watchdog`、log 前綴 `[gemma2-watchdog]`（基類 `embed-watchdog` 的 instance-method log 都改 `_log_prefix` 化 = 兩隻可辨識）；**閘門** `QDRANT_GEMMA2_WATCHDOG`（空白 = 跟隨 `QDRANT_MEDIA_EMBEDDING` — 媒體關閉時不養它；顯式 on/off 覆蓋）。**兩 server 並存的語義照舊正確**：kill 只會殺 netstat 上「真在聽 :8081」的 PID；refused 快路在另一台 llama-server.exe 活著時（tasklist 命中）退回 2-streak spawn-only（最壞 30 分鐘後拉起 — 115.1 防誤殺載入中模型的保守設計如實保留）。接線：`gui/api.py::_start_gemma2_watchdog()` 掛 prewarm（主 watchdog 之後、永不 raise）+ `embedding_watchdog_status` 附加 `gemma2` 子鍵（僅閘 on 時）+ `Agent._runtime_ops("status")` 露 `gemma2_watchdog` 段。tests：`test_embed_watchdog.py` +8（閘門三態/media 流通、URL localhost 改寫、bat 覆寫、log/thread 鉤子覆寫、**spawn-only 療傷鏈斷言 gemma2 bat 而非 4B**、singleton+status、guard never-raises、prewarm source guard）+ `_clean_env(**extra)` 參數化 = **41 passed**；回歸 reembed_admin/runtime_host/gui_prewarm **52 passed**。pyproject 0.9.122.1 > **0.9.122.2**；README/USER_MANUAL/ARCHITECTURE/.env(.example 兩 env)/CLAUDE 同步。

### v0.9.122.1  

**EmbeddingGemma-2 影像嵌入通路查證結案（user: 「確認最新線上 llama.cpp vulken版是否支援 embeddinggemma-2 圖形匯入了嗎」）— 上游已通；且 v0.9.114.0「判不可用」的反向根因確診 = 我方請求形狀錯誤（不是上游沒有）。** 查證（2026-10-13）：①ggml-org#30082 已由提報者本人**撤回（closed as invalid）** — 實測 b11457 後自陳「image embeddings work fine; I was sending the wrong request shape」：三張不同圖 cos=0.891/0.781、跨模態「文字↔紅方塊圖」cos=0.729、9 張實照 ~1.8s、文字→圖檢索名次正確；②上游 **#30054（2026-10-06 合併，build b11457 起）** 已把 EmbeddingGemma-2 的 mtmd/mmproj（vision+audio encoders）接進 `/v1/embeddings`；③**正確請求形狀 = 巢式 typed content parts** — `input:[{"content":[{"type":"image_url","image_url":{"url":"data:image/jpeg;base64,…"}}]}]`（每個 input 元素 = `{content:[parts]}`；淺層 `input:[{type:…}]` = 400）；④陷阱仍在：內部 `/embedding`+`image_data` 與淺層形狀都可能 HTTP 200 回「字面 marker 純文字向量」（不同圖 cos=1.0 假成功）— **這正是 v0.9.114.0 `media_embed.py` 的第一順位嘗試 ⇒ 即使升級伺服器、假成功守衛探針也會永遠判不可用**。`src/ingest/media_embed.py` 修復：`_embed_image_bytes` 改**巢式形狀優先**（容錯列提取 helper `_row_from_body`：`embedding`/`data[0].embedding`/`embeddings[0]` 三種信封都收）+ 淺層與 legacy `/embedding` 降為後備（舊 build 容錯）；image 傳真實 mime（`_MEDIA_MIME` png/jpeg/webp/gif/bmp）；audio 增試巢式 `input_audio` content part（上游未 live 驗證，fail=None 誠實），video 照舊 legacy。**紅/藍假成功守衛與 60min cooldown 照舊** = 現役 gemma2 server（unsloth `b11443-mix`，早於 b11457）照樣被正確擋下；**升級 server 預建即自動點燃**：unsloth fork **b11541-mix-2500203**（2026-10-11、upstream b11541 ⊇ #30054）或官方 ggml-org **b11582**（官方現已直供 Windows x64 Vulcan prebuilt）。tests +2（巢式形狀優先鎖定 + call_count==1 契約、audio 巢式→legacy 鏈）。**Live 上線（同日執行）**：下載 unsloth `b11541-mix-2500203` 的 `app-…-windows-x64-vulkan.zip`（28,148,074 bytes）解壓替換 `C:\llama.cpp-un\` — **47 個舊 exe/dll → `_old_b11443\` 回滾區**（GGUF/mmproj/bat 不動）；bat 的 `cd /d "%~dp0"` 尾反斜線引號陷阱（cmd 把 `\"` 當脫逸）順修為 `"%~dp0."`。8081 重啟（build 11541, commit ce8c04021）後**真實探針全亮**：`media_path_available` = **verdict True `cos(red,blue)=0.8795 ≤ 0.999 = REAL`**（假模式 = byte-identical 1.0000）；不同圖 `cos(checker,grad)=0.8094` = 各自獨立向量；文字塔 `768d` 正常；**音訊通路也真通**（巢式 `input_audio` content part 200 + 768d；cos(220Hz sine, 2kHz square)=0.9114 ≠ 假 1.0）— **影像/音訊媒體側通道正式點燃**：下次 ingest 的附圖 side-car、獨立媒體檔、`search_media` 跨模態檢索自動生效（AV 檔案在 pipeline 的 ext 分流仍跳過 = AV ingest 閥門下一版再開）。tests 回歸：`test_media_embed` 27 passed、media+headings 63 passed。pyproject 0.9.122.0 > **0.9.122.1**；README(ROADMAP)/CLAUDE/ARCHITECTURE/USER_MANUAL/.env(.example)/config.py 措辭同步。

### v0.9.122.0 — 片語（snippets）多行化 + MCP 管理面 `snippets-local`（user: 「現在的 snippet 只有單行，以 MCP 架構提供可以編輯多行的 snippet 的功能和管理功能」）

* **核心（新 `src/snippet_store.py`，零副作用鏡像 skill_library 紀律）**：canonical 條目 = **一般字串但可含 `\n`（多行片段，逐字保留；CRLF 正規化）** — 存檔 = 既有 `$QDRANT_RAG_HOME/snippets.json`（GUI ⚡ 面板/MCP/任何裝置共用一份，**零遷移**）；上限 = **100 條 × 2000 字/條**（舊 50×200 全面放寬到可容多行模板）；**所有寫入路徑**（MCP 五操作 + GUI 整表 save）都過同一套驗證（strip/dedupe/上限/atomic tmp+replace）。API：`load/save/list_snippets(+limit)`、`add_snippet(text, at?)`、`set_snippet(index, text)`、`delete_snippet(index)`、`move_snippet(index, to)` — index 全 0-based、範圍/重複/空值 = 明確 error dict；讀取永不 raise（壞檔 → []）。
* **MCP 面（server `snippets-local`，5 工具）**：`snippet_list`（index/preview 首行+`…`/multi 旗/lines/**完整 text** — 編輯器需要）/ `snippet_add(text, at?)` / `snippet_edit(index, text)` / `snippet_delete(index)`（低風險，不設 confirm — 與 GUI ✕ 同調）/ `snippet_move(index, to)`。host `SnippetsLocalHost` + `McpManager.from_config` 註冊（**import 元組 + seed 區塊兩處 — v0.9.116.1 教訓**；`_snippets_local_host` 保留 + `mgr.failed["snippets-local"]` 防呆）+ 行為級 seed 測試（真建 manager 429 驗證）。
* **stdio 鏡像** `src/mcp_server/snippet_server.py`（`SnippetMCPServer`，5 工具對外部客戶端開放同一份檔案）。
* **GUI 多行 UX（app.js + index.html + style.css）**：①新增列 `<input>` → **`<textarea>`（Enter=換行 / Ctrl+Enter / ➕ 送出；maxlength 2000）** ②chip 顯示 = **首行 + `⏎N` 旗標**（title=全文 — 多行不再撐爆 chip 列；插入仍逐字多行）③每 chip 加 **`✎` 編輯**（管理模式下現形 → 晶片內嵌 textarea + ✔存/✕棄 — 存 = 整表 save）。i18n `snipsPlaceholder` ×4 更新。
* **接線**：ToolRoute 觸發列 +（`snippet|片語|快速片語|快速回覆` → snippets-local）；system prompt 加「Snippets / quick phrases」節（把常用回覆收進片語/列出/編輯 = 這組工具；GLOBAL 共用）；live `.env` + `.env.example` gate 加 `snippets-local`。
* **測試** +15 `tests/test_snippet_store.py`（核心 9：多行逐字/列表旗標/edit/range/空值/刪移/at 插入/**`at=0` falsy bug 現場修復**、100×2000 上限、CRLF+legacy 相容+壞檔）；GUI route-through ×1（同檔案 round-trip）+ web_server 舊鎖定更新（200→2000/50→100 + 多行案例）5 passed；host+manager-seed+stdio ×3；wiring ×2。appearance/bgfit 既有套件不受影響。`pyproject` 0.9.121.2 → **0.9.122.0**；RESTART required（gate 生效）。

### v0.9.121.2 — tacti `delete_plan`（user: 「提供 tacti 刪除的功能」）

* **core（`src/agent/skill_compiler.py`）**：新 `delete_plan(plan_id, confirm=False, force=False)` — ①confirm 閘 fail-closed（未帶 = 明確錯誤，檔案不動）②RUNNING 中的 plan 拒刪（指引先 `stop_plan` — 120.0 的 stop 旗會連殭屍 stage 回合一併收）或 `force=true`（**撒 stop 旗 + 刪除一次完成**：stop → driver loop + 全部 stage threads 於下一個邊界結束 → registry 旗 + `_DELETED` 集合）③刪除 = 檔案 + 記憶體快取 + `_EXTENSIONS` 預算 + `_STOPS`；**task-registry 列與 stage session 保留**（歷史完整）。④**防復活守衛**：`_DELETED` 永久集合（uuid 不復用）— `save_plan` 對已刪 id = no-op（殭屍 driver 的遲到 save 不會重建檔案）、`load_plan` 對已刪 id = None；清除 .tmp 殘片。
* **工具面**：`TactiLocalHost`（tacti-local）第 7 工具 **`delete_plan(plan_id, confirm, force?)`** + stdio `tacti_server` 鏡像第 7 處同步；prompt compile-plan 節補 delete 指引（不可復原；RUNNING → stop 先行 / force）。
* tests +6 `TestDeletePlan`（confirm 閘 / 刪 compiled plan = 檔案消失+list/load 讀不到 / 未知 id / RUNNING 拒絕 → force 刪成功 / **殭屍 driver save 不復活** / host 面第 7 工具 + stdio parity + E2E dispatch）。`pyproject` 0.9.121.1 → **0.9.121.2**；RESTART required。

### v0.9.121.1 — 【真兇修復】選本地圖檔背景無效：`_applyFromUi` 重新讀入覆寫了剛選的圖（user: 「選擇本地圖檔修改了還是無用」）

* **根因（100% 重現、任何瀏覽器/本機遠端都中）**：「選擇檔案」處理流程把 `st.bg = dataURL` 設在自己物件後呼叫 `_applyFromUi()` — 而 `_applyFromUi()` **重新從 localStorage 讀狀態** → 剛選好的 bg 在套用+儲存**之前就被覆寫掉** → 套用的是「沒有背景圖」的舊狀態、存的也是舊狀態，但 **✅ 已套用 toast 照樣顯示**（URL 欄位同樣中招；v0.9.121.0 的 ✅ toast 因此一直沒暴露真因）。色調/字型等直接 `_applyAppearance+save` 的路徑不受影響 — 這就是為何外觀其他功能正常、唯獨背景圖從 bgfit 重構後就死。
* **修**：`_applyFromUi(override)` — 接受覆寫合併 `Object.assign(_appearanceState(), override || {})`；檔案/URL 處理流程改傳 `_applyFromUi({ bg, bgIsUrl, bgIsData })`（暗化/佈放格式等既有無參呼叫者不變）。
* **順手加強（同輪）**： ①檔案 ≤1.2MB **直接用原檔 dataURL**（跳過 canvas 往返 — 免 decode/toDataURL 怪癖、保留 PNG 透明與 EXIF 方向）→ 更大圖才走 canvas 縮圖；canvas 失敗/空結果**退回原檔 dataURL**（舊碼在 `toDataURL` 回空字串時會存進「data:,」= 畫面無圖但 ✅ toast）。②測試 +3：`test_apply_overrides_merge_source` / `test_small_file_direct_dataurl` / **`test_file_pick_flow_real_functions`（jsdom 跑 app.js 真實函式切片：stub FileReader/Image → dispatch change → 斷言 bg 落進 localStorage + body.backgroundImage + has-bg + data-bg-fit — 即 121.1 修復的端到端驗證；jsdom 缺裝 = skip，同 diagrams 契約；jsdom 已 `npm i jsdom --no-save` 就位）**。
* 全綠：appearance+bgfit+web_upload **48 passed**（含真實函式 E2E）。`pyproject` 0.9.121.0 → **0.9.121.1**；web 模式 Ctrl+F5 後生效。

### v0.9.121.0 — 遠端電腦背景圖「修改無效」修復（user: 「遠端電腦修改 web ui 背景圖修改無效」— 模式：套用當下即失敗）

* **機制盤點**：外觀背景 = **純 client 端**（canvas 縮圖 ≤1920px → JPEG dataURL ≤2.5M chars → localStorage `qdrant-rag.appearance`；assets 已有 ETag+no-cache 防舊頁；無 CSP 阻擋）— 遠端瀏覽器照理可用。三個會造成遠端「完全沒變」的死路，全數修復：
* **① quota fallback 幽靈鍵（真 bug）**：`_saveAppearance` 的降級重試 drop 的是**不存在的 `bgDataUrl` 鍵** — 重試原樣重存同一份超額 payload → 再次失敗 → **靜默吞掉**（套用視覺已跑、儲存從未成功 = 重整即消失；某些裝置 localStorage 更緊，降級路徑必中）。修 = drop **`bg`**（真鍵）+ `bgIsData=false`（兌現既有 i18n 文案「背景圖已移除、其餘設定保留」）+ 二次失敗也 toast（新鍵 `appearanceSaveFailed`）。
* **② URL 欄位路徑轉換（遠端本質缺口）**：遠端瀏覽器**載不動伺服器的本機檔案路徑** — `url("C:/Users/humphery/uploads/x.jpg")` 到哪台遠端都無效。新 `_bgUrlFor()`：`http(s)://`、`data:` 原樣通過；**Windows 絕對路徑 / `uploads/` 相對路徑 / `/uploads/` / `[已上傳]` 標記行 → 自動轉 `/download?path=…` 串流端點（v0.9.65 的沙盒閘管線 — 任何遠端瀏覽器可載）**；無法辨識的形式 = 拒絕 + 明確 toast（新鍵 `appearanceBgUrlBad` ×4 語系）。
* **③ bind/apply 錯誤顯性化**：`bindAppearance` 逐部件 try/catch（缺元素=舊快取頁、任何 throw → 只會斷掉**後面所有綁定**且零診斷 =「修改無效」的另一可能樣貌）→ 失敗清單 toast（新鍵 `appearanceBindFail`）+ console.warn；`_applyAppearance` 包 wrapper（`_applyAppearanceInner`）— 任何套用例外 toast 具體原因。
* tests +5 `tests/test_appearance.py::TestRemoteBgFix`（來源守衛 ×4 + **Node 跑真實 app.js `_bgUrlFor`** ×1 — 8 情境：data/http 傳通、C:/路徑/uploads//已上傳-標記 → /download、垃圾拒絕）；回歸 appearance+bgfit+web_upload **45 passed**。`pyproject` 0.9.120.3 → **0.9.121.0**；web 模式強制重新整理（Ctrl+F5）後生效。

### v0.9.120.3 — 「計畫編譯器」體系正名 TACTI（skill-local → tacti-local；user: 「將 skill-local 和其 skill 都改稱 tacti (tactical)，以防稱呼上的錯亂」）

* **正名總表**：server `skill-local` → **`tacti-local`**；host class `SkillLocalHost` → **`TactiLocalHost`**（manager 註冊 attr `_tacti_local_host`）；stdio 鏡像 `src/mcp_server/skill_server.py` → **`src/mcp_server/tacti_server.py`**（class `SkillMCPServer` → `TactiMCPServer`、server 名 `divine-sense-agent-tacti`；mcp.json 範例 name `"tacti"`）；log 前綴 `[skill]` ×20 → **`[tacti]`**（app.log 對帳一致）。內部模組 `src/agent/skill_compiler.py` 保留原名（歷史檔案；docstring 標註 user-facing = TACTI）。
* **ToolRoute 語意移轉**：編譯器觸發列 → server `tacti-local`，patterns 移除 `skill|技能`、新增 `run_plan_stage|stop_plan|tacti`（工作流/戰術/策略照舊）— **「skill/技能」一詞自此專屬檔案庫列**（+`技能|skill` bare 入 skill-library-local row）→ 120.2 的「目前有那些skill」測試反轉為：掛檔案庫、**不掛 tacti**（listing = skill_list 語意）。
* **prompt**：compile-plan 節頭 → 「Tacti plan compiler (v0.9.109+, tacti-local; formerly skill-local)」；120.2 的「TWO skill systems」節升級為 **「TACTI vs SKILL (two DIFFERENT systems)」**（(a) TACTI = 戰術計畫編譯器 / (b) SKILL = SKILL.md 檔案庫）並清掉 insert 殘留的行間空行。`list_plans` 工具描述標註「這是 TACTI 計畫庫，不是 SKILL.md 技能檔案庫（那是 skill_list）」。
* **config/docs**：`.env` + `.env.example` gate 列 `skill-local` → `tacti-local`；SKILL_GUIDE/USER_MANUAL §8 正名（含更名註記；USER_MANUAL 專冊行）+ ARCHITECTURE 兩處（標頭 arc + stdio 清單 tacti_server.py）；舊版 changelog 保留原文（歷史紀錄）。gate 需重啟才吃新名（RESTART required）。
* tests：test_skill_compiler/test_plan_scope_handoff/test_skill_library 套件內 `SkillLocalHost`/`skill-local`/`SkillMCPServer` 全數跟進（×19）；`test_listing_question_mounts_both_skill_servers` → **`test_listing_question_mounts_library_not_tacti`**（目前有那些skill → 只掛 skill-library-local；不掛 tacti）；2 個鎖定斷言跟進（prompt 節頭改判 `Tacti plan compiler (v0.9.109+`、兩系統節改判 `TACTI vs SKILL`）。
* 全綠：五套合跑（skill_compiler + plan_scope_handoff + skill_library + toolroute + toolroute_prev_reply）**161 passed**（首跑 159 passed + 2 斷言跟進後重跑 9/9）。`pyproject` 0.9.120.2 → **0.9.120.3**。

### v0.9.120.2 — Skill 雙機制釐清（ToolRoute 問題側掃描 + prompt 兩系統分工；user: 「skill 的 管理有一些混亂，看 app.log，好像有兩個 skill 機制混在一起」）

* **診斷（app.log 2026-10-12 三案）**：①15:09「把本次去背景方式做成skill存檔」→ 工具面**沒有任何 skill 伺服器** → LLM 用 `write_file` 手寫「skill」到 **匯入 staging**（`C:/Users/humphery/skills/telegram-photo-background-removal.md`）— 繞過 confirm 閘/provenance，`skill_list` 永遠看不到；②22:00「目前有那些skill」→ 同樣零 skill 工具 → LLM 瞎抓 `script_list`（exec 腳本清單）回答；③22:02「list skill library」→ 只掛到 `skill-local`（計畫編譯器）→ LLM 去查 **Qdrant `search_documents`**。三案同根：**v0.9.104.1 的 `_TOOLROUTE_SERVER_TRIGGERS` 只礦「上一輪回覆」，從不掃問題本身** — 首輪/無關上文的 skill 提問完全不上工具面。
* **修 ① 問題側 mining（`_toolroute_servers_for`）**：同一份 curated 觸發列**對 question 也做 union**（union-only；掛面 ≠ 強派發）— 22:03「list skill」（恰有相關上輪回覆才偶然正確）從此對任何上下文都成立。**修 ② 檔案庫觸發列擴詞**（skill-library-local row）:+`哪些 ?skills?|有那些 ?skills?|有哪些 ?skills?|list skills?\b|skills?\s*列表|列出 ?skills?|(?:做成|存成|儲存為|保存成|儲成|寫成) ?skills?`。
* **修 ③ prompt「TWO skill systems」澄清節**（插在技能檔案庫節之前）：(a) skill-local = **計畫編譯器**（compile_plan/run_plan — 多階段背景執行）；(b) skill-library-local = **SKILL.md 檔案庫**（skill_import/list/get/search/remove/use）；列出/查詢 skill → `skill_list`（**NEVER script_list、NEVER search_documents** — 向量庫不是技能庫）；「做成/存成 skill」→ 沙盒寫 SKILL.md 資料夾 → `skill_import(confirm=true)` — **NEVER 用 write_file 手寫進 staging（C:/Users/humphery/skills）或庫根**（繞閘+無 provenance+skill_list 看不見）。
* **殘留**：staging 的孤兒檔 `C:/Users/humphery/skills/telegram-photo-background-removal.md`（2367B）保留未動 — 內容可審後說「把 telegram-photo-background-removal 匯入技能庫」正式入庫（或手動刪除）。
* **插曲（本輪修復事故，已復原）**：prompt 插入探針被重跑造成雙插入 + 後續 dedupe 的 `find(...,-1)` 回 -1 使 `text[:-1]+text[6:]` **整檔自我拼接**（478,501 chars / 10,249 行）— 依 `cur = orig[:-1] + orig[6:]` 精確反推（seam = docstring 第二出現點 −1），還原 orig 後行級去重；**復原後 py_compile + 完整性計數（docstring/from __future__/TURN_STOPPED_TEXT/各 prompt 節各 1）+ 141 測試全綠**。
* tests +6 `tests/test_skill_library.py::TestTwoSkillSystemsRouting`（三案問題路由 ×3 / 無關問題零誤掛 ×1 / prompt 澄清 ×2）；回歸 skill_library 64 + toolroute 22 + prev_reply 18 + skill_compiler 43 = **147 passed**。`pyproject` 0.9.120.1 → **0.9.120.2**；RESTART required。

### v0.9.120.1 — Chat Excel 上傳修復（xlsx 可解析 + 拖放/貼上檔案上傳；user: 「chat ui上傳 excel 無法正常上傳」）

* **根因（三層死路）**：上傳端點本身其實一直收 xlsx（只擋危險執行檔）— 但 (1) ingest pipeline 把 `.xlsx/.xls/.xlsm` 列在 `_SKIP_EXTS`「不支援的 Office 格式」→ `add_document` 直接跳過；(2) `read_file` 視 xlsx 為二進位拒讀；(3) 前端只有 📎 按鈕與**純圖片**剪貼簿貼上（從檔案總管複製 Excel 或拖放 = 完全沒反應）— 上傳後等於報廢。
* **loader（`src/ingest/loader.py`）**：新 `_load_xlsx` — openpyxl（`read_only`+`data_only`）逐 sheet 渲染 `## Sheet: <名稱>` + ` | ` 管線表格；空列/空 sheet 跳過、結尾空格修剪、**儲存格 200 字截斷+`…`**、每 sheet 200k 字上限（超大活頁簿降級不爆 chunker）；`.xlsx` 加入 `_LOADERS`；`.xls/.xlsm` 維持不支援（BIFF/巨集需 xlrd，記錄在案）。pipeline `_SKIP_EXTS` 移除 `.xlsx`。
* **前端上傳 UX（`gui/assets/app.js`）**：①`onChatPaste` 現在**收任意非圖片檔案**（Explorer 複製的 Excel/PDF/zip → Ctrl+V → 同 POST /upload 流程；圖片路線 v0.9.29.0 契約不變）②新 `bindDrop()` — **拖放上傳**：任何檔案拖進頁面（web 模式）即上傳 + `body.drag-over::after` 虛線提示（style.css）。GUI（pywebview）模式維持原生檔案對話框路線。
* **prompt**：工具規則新增「Excel / data-file uploads」一節 — 上傳 xlsx 後 `read_file` 會拒讀（binary），正確通路 = `add_document(mode=path)`（loader 已能解析並入庫檢索）或 `run_python` + openpyxl 精查儲存格；`.xls/.xlsm` 不支援。
* **依賴**：`openpyxl>=3.1` 進 `pyproject` dependencies（先前只是 pandas 的傳遞依賴 — 顯式宣告契約）。
* tests +11 `tests/test_xlsx_loader.py`（loader ×5：多 sheet/CJK/空列/空表/截斷/graceful 壞檔；pipeline skip-set ×1；JS 拖放+貼檔 ×2；CSS 提示 ×1；依賴宣告 ×1）+ test_clipboard_paste 舊斷言更新（kind 過濾契約同義改寫）；合跑 xlsx+clipboard+web_upload **59 passed**；live E2E：openpyxl 實檔 3 sheets（中文/空列/250 字儲存格）→ `load_file` 全部命中。`pyproject` 0.9.120.0 → **0.9.120.1**；web 模式重啟後生效。

### v0.9.120.0 — 背景任務強制中止機制（task registry stop-flag + stop_task 工具；user: 「提供 強製中止 背景任務 的機制，telegram所產生的任務，目前沒有任何機制可以強制中止執行」）

* **registry（`src/agent/task_runner.py`）**：新狀態 `STOPPED="stopped"` + 每 task 一枚 **stop flag（threading.Event）** — `request_stop(task_id)` / `stop_requested(task_id)`；旗標**刻意不隨 end_task 清除**（回合終局後仍有效 = **殭屍回合可達** — 超時被棄置的回合與在飛回合走同一個 loop）；`list_tasks` 每列露出 `stop_requested`；`reset_tasks` 清空。
* **Agent 合作式中止（`agent.py`）**：`__init__(stop_check=…)` + `set_stop_check(cb)`；`_tool_loop` **每次迭代開頭**（下一發 LLM 呼叫之前）檢查 → 觸發 = 直接回 `TURN_STOPPED_TEXT` 誠實固定文案（**不浪費最後一發 LLM**）、`last_tool_stopped=True`；callback 例外只 debug 不炸迴圈。Python 無法安全殺 thread — 中止的最壞延遲 = 一次在飛工具呼叫（tool 看門狗 600s 上限 / 腳本自身 timeout）+ 0-1 次 LLM 往返。
* **三條執行路徑接線**：①telegram（`make_telegram_on_message` after `begin_task` wire + 收尾分支：**回合結束後才送到的 stop 也讀 registry**（`_stopped flag or _sr(_tr)`）→ 回 `_TURN_STOPPED_REPLY` + registry `stopped`）②task（`_run_once._work` wire；收尾以 registry 判 `STOPPED`，**跳過記憶沉澱**）③plan stage（`_run_stage` wire + stop 時不再覆寫 driver 的 failed 標記；**`stop_plan` 對全部 stage task_ids 撒旗** — 連已終局的殭屍 stage thread 也照樣收）。
* **工具面**：`TaskLocalHost` 新 **`stop_task(task_id)`**（server `task-local`，第 5 工具；gate 已含 task-local 免新設定）+ stdio `task_server` 鏡像同步；`_stop`: 已設旗號 → ok+note / 未知 id → 明確錯誤。system prompt「Background tasks」區塊 + force-stop 教學（list_tasks 找 task_id → stop_task → 合作式收尾、成果保留、殭屍也服從）。
* 已知限制（如實記錄）：Telegram in-channel「停止」訊息在回合進行中**收不到**（bot 輪詢執行緒序列 = 正忙著執行回合）— 中止請求走「GUI 聊天 / 回合結束後 Telegram 下一則/LLM 呼叫 stop_task」；scheduler/webhook fire 的 stop 接線 = 下一輪（registry 旗標已備好）。
* tests +14 `tests/test_task_stop.py`（registry ×6 / loop ×3 / telegram E2E ×1（即時 stop → stopped row + 中止回覆）/ tool ×3 / plan stage ×1（stop_plan 撒旗 → 殭屍收 `stopped`））；回歸 task_runner 25（tool surface + `stop_task` 更新）/ telegram×2 39 / turn_gate+plan_scope 42 — 全綠。`pyproject.toml` 0.9.119.3 → **0.9.120.0**；RESTART required。

### v0.9.119.3 — ToolRoute 問題側 hint：Photocraft 首輪即上工具面（live 12:09 案）

**（live 2026-10-12 12:09：GUI chat「使用 photocraft畫一個圓」history=0 → class=image p=0.76 → surface 缺 photocraft → LLM 花 tokens=110,202 自行 `list_mcp_tools` + `run_grep` ×3 找工具仍無法派發。根因 = v0.9.119.1 的 photocraft 列在 `_TOOLROUTE_SERVER_TRIGGERS`，但該清單**只礦上輪 assistant 回覆**（v0.9.104.1 接續語意）— 首輪問題側從不掃它；telegram/imagegen/browse 三支 hint 才是僅有的問題側 union 面。）**

* 修：新 `_toolroute_photocraft_hint(question)`（`_toolroute_imagegen_hint` 同型窄匹配：photocraft/.psd/.pcraft/.pcdroplet/修圖/去背/圖層/裁切/縮放圖/photoshop/photo edit/bg removal/remove background）+ 接進 `_toolroute_servers_for`（browse hint 之後）— 首輪問題直接 union photocraft；純畫圖/產圖不誤觸（測試鎖定：`test_hint_not_for_plain_draw`）。+3 tests（test_toolroute.py **25 passed**）；`pyproject.toml` 0.9.119.2 → **0.9.119.3**；RESTART required。

### v0.9.119.1 — PhotoCraft MCP 上線修通（user: 「協助設定 MCP C:\Program Files\PhotoCraft\photocraft-cli.exe」）

* **診斷**：`mcp.json` 已有 `photocraft` 條目但 command **缺 `mcp` 子命令**（裸 exe = 只印 help 即退出 → stdio 握手必敗）；`MCP_ACTIVE_SERVERS` 已列但 manager 註冊不到工具 = gate 空轉。live 握手探針實證：`photocraft-cli mcp` = stdio MCP 0.7.0（protocol 2024-11-05，**21 工具**：doc_new/open/save/export/inspect/render_preview/command_list/run/batch/jobs_list/cancel/session_list/control_call + ui_*（ui_* 需 `--bridge` 連 GUI 的 control port）；檔案路徑相對啟動時的 roots）。
* **修**：`mcp.json` command → `["C:\\Program Files\\PhotoCraft\\photocraft-cli.exe", "mcp", "--automation-read-root", "C:/Users/humphery", "--automation-write-root", "C:/Users/humphery"]`（roots 對齊主程式沙盒）；live `.env` MCP_ACTIVE_SERVERS 去重（使用者先前已自加，補丁造成雙列 — 已清理）。`agent.py` `_TOOLROUTE_SERVER_TRIGGERS` +photocraft 列（photocraft/.psd/.pcraft/.pcdroplet/修圖/去背/圖層/裁切 → photocraft；產圖留在 imagegen-local 不衝突）。+3 tests（test_toolroute.py）；`pyproject.toml` 0.9.119.0 → **0.9.119.1**；RESTART required。
* **官方 docs 對照（storytold/photocraft docs/mcp.md — 我們的啟動式 = 官方推薦式一字不差）**：① `command_batch` **非原子交易** — 失敗時已完成步驟保留在文件上，恢復靠 `doc_inspect` 看歷史 + 每條 undoable step 配一次 `command_run({"id":"edit.undo"})`；undo 不回收已寫盤的檔案。② 實驗前先 `command_run` `image.duplicate` 或 `history.newDocument`（副本成為 active doc，原文件不動）。③ 資源：`photocraft://document` / `photocraft://commands`（即時 JSON）。④ 未知參數 → `-32602`（嚴格）；失敗步驟 `isError:true`；engine panic 有 poisoned-session 恢復。⑤ `file.export.renderVideo` 支援 `_meta.progressToken` 進度通知 + cancellation（寫入拒絕覆蓋既有檔）。⑥ **檔案 saves 可能覆蓋既有目標** — write root 已對齊主程式沙盒。⑦ ui_* 工具需 bridge 模式：`photocraft.exe --control <port>` + command 加 `--bridge 127.0.0.1:port` + `--control-token-file`（64-hhex token 檔）— 未接，目前 headless 引擎可用。

### v0.9.119.0 — Polite scraping / download discipline：抓多頁腳本的節流紀律寫進 system prompt（user 核准「依建議改進」）

**（user: 「大量的 http status 429 是因為同時開太多 thread嗎」診斷結論：429 = 目標網站對「同 IP 請求頻率」的伺服器端限流 — 序列執行照樣炸（小說補頁 todo 97→5 全程單執行緒一路 429）、resume16 plan 16 個 wave 零並行照樣炸、429 隨時間自癒 — 累積「請求頻率」才是元兦，thread 數不是。）**

* agent.py `_system_prompt(tools_enabled=True)` 新「Polite web scraping / download discipline (v0.9.119.0+)」觸發區塊（插在 Skill file library 與 Stock 之間）——教 LLM 把節流**寫進腳本本體**：(1) 每請求固定 2-5s 間隔 (2) 429/503 = 網站在限「請求頻率」（引今晚小說案：FULLY SEQUENTIAL 照樣 429 — 永遠不是 threads 造成的）→ 退避 30+random×30s、同頁重試 ≤4 次、尊重 Retry-After (3) done-IDs checkpoint 寫沙盒 JSON/JSONL = 重跑 RESUME 不重抓 (4) **一個自足腳本跑完一整批** > 每頁一次工具派發（per-page 派發慢、汙染 transcript）(5) 預設序列（並行只會更快撞 429，不是更好）+ NEVER 把失敗的抓取講成成功（缺頁列入報告）。涵蓋 chat/Telegram/plan stage/排程全部回合（同一份 tools prompt）。
* SKILL_GUIDE §10 同步一行（stage prompt 自足原則旁）。
* tests +8 `tests/test_system_prompt_scraping.py`（block 存在/關鍵規則 429+backoff+checkpoint/429-not-threads 教學/never-fake-success/legacy 排除/response_language 併存/來源單一出現 ×2）+ prompt 同門回歸 telegram/diagrams/openwith/todo scraping 五套 43 passed。`pyproject.toml` 0.9.118.0 → **0.9.119.0**；RESTART required。

### v0.9.118.0 — 硬超時仲裁器升格共用模組 `turn_arbiter.py` + Telegram 回合同款移植（user 核准）

**（user: 「同款 liveness+Jev arbiter 移植到 telegram_bot.py 的 turn 硬超時」— 22:48 事故復盤：Telegram 直接下巨量工作撞 bot 自己的 `chat_turn HARD TIMEOUT after 1800s`（v0.9.107.1 棄置路徑，無仲裁）；而 0.9.117 的 plan 仲裁已被 live 實證有效（23:38 c01 Jev p=0.87 加窗 #1、00:08 p=0.89 #2、`done 1-300: 76→86→94→102` 批次持續前進）。）**

* **新共用模組 `src/agent/turn_arbiter.py`**（零副作用 import；零 `src.config` 依賴）— 仲裁核心從 skill_compiler 抽出：`evidence_snapshot` / `evidence_text(subject=…)` / `jev_verdict` / `guarded_run_join`（0.5s 切片 join + liveness 成長判定 + verdict 諮詢 + 加窗 + fail-closed kill，含 worker raise re-raise / owner-stop 捷徑 / grant bookkeeping 保護）。
* **skill_compiler**：`_guarded_chat_turn` 改為薄 delegate（行為 byte-identical；`_evidence_snapshot`/`_timeout_evidence_text`/`_jev_timeout_verdict` 保留同名薄 wrapper = v0.9.117.0 測試的 monkeypatch 點不變）；extensions env 名稱不變。
* **telegram_bot**：`_chat_turn_guarded` 接共用仲裁器 — 回合超過 `QDRANT_TURN_HARD_TIMEOUT_S` 時：派發成長 → 加窗（免費）；靜音 → live Jev 判決（同一組 Jev 旋鈕）→ 加窗/棄置。預算 env = **`QDRANT_TURN_HARD_TIMEOUT_EXTENSIONS`**（預設 3；0 = off = 舊單窗行為）；超時回覆訊息/registry failed 路徑不變（加窗耗盡才走到）。
* **Jev 旋鈕別名**：`QDRANT_SKILL_TIMEOUT_JEV` / `QDRANT_SKILL_TIMEOUT_JEV_MIN_PROB` 先讀（117.0 相容），`QDRANT_TIMEOUT_JEV` / `QDRANT_TIMEOUT_JEV_MIN_PROB` 為共用主名 — 兩邊機制用同一組開關。
* tests：+18 `tests/test_turn_arbiter.py`（resolver ×4 / 閘別名 ×6 / 證據文本主語 ×1 / judge 迴圈 ×7：fast-pass/raise 穿透/liveness 免費加窗/verdict 加窗/None→kill/耗盡 fail-closed/owner-stop）+ `test_turn_hard_timeout.py` +7（resolver/wrapper 閘/liveness 加窗完成/Jev verdict 加窗/abnormal kill 訊息路徑不變/22:48 同型 E2E：Telegram 大工 7s 在 2s 窗下存活）— 19+18 全綠；skill_compiler 全檔分批 42 绿（仲裁重構後 monkeypatch 契約不破）。
* `.env.example`（TURN extensions + 共用旋鈕說明）/ `USER_MANUAL.md` 附錄同步。`pyproject.toml` 0.9.117.0 → **0.9.118.0**；RESTART required。

### v0.9.117.0 — stage 硬超時不再冤殺巨量工作：liveness + live Jev 仲裁 → 加窗續跑（user 提案）

**（user: 「使用plan執行巨量工作會發生1800 sec timeout,是否在發生timeout使用jev確認,如非異常則繼續執行，詳細看APP.log」— live 2026-10-09 18:24 `novel-wangu-scrape` plan 鐵證：s1/s2/s3 撞 1800s 硬超時被判 failed、wave1 stop；但 log 20:26-20:42 顯示被放棄的 daemon thread 還在拼命 run_bash 刮章節存檔（saved 1317/ok 2296…）= 合法巨量工作被冤殺 + 死後繼續燒 LLM 工具費。）**

* **仲裁鏈（`_guarded_chat_turn` 重寫）** — 每個硬超時到期點、stage 還活著時判決：①**確定性 liveness**（0.5s 粒度輪詢 `agent.last_tool_dispatches`，派發數成長 = 有進展 → **免費加窗**，不問 LLM）②**live Jev 仲裁**（僅 `JEV_MODE=live` 真分類；證據文本 = elapsed/派發數/最近 8 次派發/tokens/工作說明 → `normal_long_work` 且 prob ≥ 0.5 → 加窗）。**加窗上限** `QDRANT_SKILL_STAGE_TIMEOUT_EXTENSIONS`（預設 3 = 最多跑 4 個 stage_timeout 窗）；耗盡或 verdict=abnormal/None → 走 v0.9.107.1 原棄置路徑（0 extension 時錯誤訊息 byte-identical；耗盡時附 `(+N extension(s))` 字樣）。
* **失敗封閉**：gate off（`QDRANT_SKILL_TIMEOUT_JEV=off`）/ Jev mock 模式（hash 挑選無仲裁價值）/ Jevin 錯誤 / 低信心 → 一律照舊 kill；`jev_choice` 自帶 10s timeout 有界；仲裁例外永不炸 guard。
* **driver 波次 deadline 隨加窗伸展**：新純函式 `_wave_deadline(wave_started, ext_base, ext_used)`（舊 `max(60, t+60)` → ext 後 `t×(1+ext)+60`，`run_plan` 開跑清空 budget、每 0.7s 輪詢取 max）— 加窗中的 stage 不再被 driver 判 skipped。
* 已知限制（如實記錄）：被棄置的 thread 本身仍無法強殺（daemon；v0.9.107.1 同款）— 仲裁的目的正是把「合法長工」救出棄置路徑；`_sink_plan` 記憶沉澱仍在 plan 終局原樣。
* 新 env（.env.example 已註冊）：`QDRANT_SKILL_STAGE_TIMEOUT_EXTENSIONS=3` / `QDRANT_SKILL_TIMEOUT_JEV=on` / `QDRANT_SKILL_TIMEOUT_JEV_MIN_PROB=0.5`。
* tests：`tests/test_skill_compiler.py` +14（liveness E2E 加窗完成 / jev-extend 靜音 stage / abnormal kill 訊息精確 / 耗盡 suffix / gate-off=舊行為 / prob 地板+env 覆寫 / mock 不分類 / jev 錯誤→kill / deadline 數學 / evidence 文本 / 預算重置）+ 既有 `test_stage_timeout` 加環境隔離（**發現：pytest 下任意 import 鏈觸發 `src.config load_dotenv` 把 live `JEV_MODE=live` 洗進測試環境 → 舊 timing 測試被真 OpenRouter 呼叫拖慢 ~8s — 測試需明示關閉仲裁**）。全檔 43 tests 分批全綠（RunPlan+Resume 9 / 其餘 20 / 新 14）；task_runner 25、meta+toolroute 26 回歸綠。`pyproject.toml` 0.9.116.1 → **0.9.117.0**；RESTART required。

### v0.9.116.1 — hotfix：技能檔案庫 boot seed NameError（`SkillLibraryLocalHost` 沒進 from_config 匯入清單）

**（live 2026-10-09 09:21 首次重啟即炸：`[mcp:manager] SkillLibraryLocalHost seed failed: NameError("name 'SkillLibraryLocalHost' is not defined")` → 工具全隱 + `MCP_ACTIVE_SERVERS unknown servers skipped: ['skill-library-local']` → telegram 問「技能庫可以用了嗎」agent 實查 list_mcp_tools/run_grep 後如實回報不可用；v0.9.116.0 的 `tests/test_skill_library.py::TestWiring::test_manager_registration` 只 grep 原始碼文字 — 抓不到這個 NameError。）** 修：`manager.py::from_config` 的 host import 元組補 `SkillLibraryLocalHost,`（v0.9.104.4 exec-local core 化/其他 host 同一個清單 — 新 host 必改兩處的教訓收案）。**+1 行為級測試** `test_manager_actually_seeds_the_host`（真建 `McpManager.from_config(path=bogus)` = 零網路，斷言 6 工具註冊 + `skill-library-local` 不在 `failed`）；tests 57 → **58 passed**。`pyproject.toml` 0.9.116.0 → **0.9.116.1**；RESTART required。

### v0.9.116.0 — 技能檔案庫：SKILL.md 匯入/查詢/刪除/引用管理（skill-library-local）

**（user: 「提供一機制 做為 skill文件 匯入、查詢、刪除、引用 等管理機制」— Anthropic Agent-Skills 慣例：一技能一資料夾（SKILL.md frontmatter + references/ examples/ 附屬檔）。與 skill-local（可執行 wave-plan 編譯器）互補：本機制管「規範文件」的存放與引用。）**

* 新 `src/skill_library.py`（純邏輯零副作用）+ `SkillLibraryLocalHost`（server `skill-library-local`，6 工具，in-process + stdio `src/mcp_server/skill_library_server.py` 雙面）+ CLI `python cli.py skills {list,show,search,add,remove,use}` + system prompt「Skill file library」區塊 + `_TOOLROUTE_SERVER_TRIGGERS` 觸發列（匯入技能/技能庫/刪除技能/引用技能…→ skill-library-local）。
* **匯入 `skill_import(source, confirm=true)`** — (a) 本機資料夾：SKILL.md + references/examples 等附屬檔全套複製（skip 隱藏/.git/`_skill_meta.json`）(b) 單一 SKILL.md 型檔案（note 附屬未含）(c) URL（github blob 自動轉 raw.githubusercontent；只抓 SKILL.md；空內文拒絕）。名稱取 frontmatter `name`（fallback：資料夾名/stem/URL slug → 時間戳）；同名衝突 fail-closed 需 `overwrite=true`；library 內來源拒絕；caps：`QDRANT_SKILL_FILE_MAX_BYTES`（2MB/檔 skip+note）、`QDRANT_SKILL_LIB_MAX_FILES`（64，超過整批拒絕）。寫入 `_skill_meta.json` provenance（source/source_type/imported_at/files）。
* **查詢 `skill_list` / `skill_get(name)`** — 目錄帶描述/檔案清單/bytes/mtime/來源（缺 SKILL.md 的資料夾如實標 invalid）；get 回 frontmatter + SKILL.md 原文（`QDRANT_SKILL_LIB_BODY_CHARS` 20000 截斷 + 提示）+ 檔案地圖 + 來源記錄 + use_hint。**`skill_search(query)`** — 零依賴關鍵字掃 name+描述+內文（case-insensitive；評分 name×10/desc×5/body hit≤20；snippet）。
* **刪除 `skill_remove(name, confirm=true)`** — rmtree 整技能資料夾；confirm 閘 fail-closed；library-root 防護（traversal/根目錄拒絕）。
* **引用 `skill_use(name)`**（Claude skill 的「觸發生效」對應物）— SKILL.md 內文**直接 inline** 進本回合工具結果（progressive disclosure、免再 read_file 往返）+ usage_note（之後的回覆與**產出文件**都遵守該規範）+ 附屬檔清單。/ste 之類的觸發/停用語意由內文自帶、LLM 自行切換。
* live E2E 13/13 綠（ste-zh 資料夾匯入 → 衝突拒絕 → list/search/get/use → **真 GitHub URL 匯入+刪除** → 暫存技能全迴路）。⚠ `dualface/ste-zh` 上游原文為**簡體** — 要繁體輸出在引用句加「以繁體中文輸出」（skill 的輸出語言規則尊重使用者指定）。
* tests: +57 `tests/test_skill_library.py`（frontmatter 解析/名稱 sanitize/confirm 閘零副作用/衝突/overwrite/caps/URL 注入/list-get-search/評分/limit/use 截斷/remove 防護/host coercion+永不 raise/stdio parity/prompt/ToolRoute/.env.example/manager/cli 接線）；回歸 skill_compiler 29 + meta 7 + toolroute 19 + mcp_active_servers 15 + imagegen+firefox 48 + plan_scope_handoff 19 — 全綠。
* `.env.example` 新塊（gate 提示 + QDRANT_SKILLS_DIR / QDRANT_SKILL_FILE_MAX_BYTES / QDRANT_SKILL_LIB_MAX_FILES / QDRANT_SKILL_LIB_BODY_CHARS）+ live `.env` MCP_ACTIVE_SERVERS += skill-library-local。`pyproject.toml` 0.9.115.3 → **0.9.116.0**；RESTART required。

### v0.9.115.3 — 快速片語（⚡ snippets）改為「游標處插入」，游標停在插入文字之後

**（user: 「gui/web snippets 要支援 後文字游標 插入，而不是每次都要重頭開始」— v0.9.100 的 `insertSnippet` 永遠 append 在輸入框最尾、游標跳到結尾；中途想放片語只能整句重打。GUI（pywebview/WebView2）與 Web UI 共用同一份 frontend，一套修法兩邊生效。）**

* **新行為**（`gui/assets/app.js::insertSnippet`）：片段落在**當下 caret 處**；有選取範圍時 **verbatim 取代**（typing 語意，不附加分隔空白）；插入後 caret 停在**剛插入文字的後面** — 連續點片語從上一次插入位置繼續。append-at-tail 的 glue-空格規則**原封保留**、只在 collapsed caret 生效（前一個字元非空白 → 補一個空格，與 v0.9.100 相同；取代選取時不補）。
* **測試**：`tests/test_web_server.py::TestSnippetCursorInsert` +3 — source guard（selectionStart/`setSelectionRange(caret, caret)`/舊 append 實作消失）＋兩個 **Node driver 行為測試**：把**真生產 insertSnippet 自 app.js 抽出**、在真 node 下以 fake-textarea DOM stub 求值（本機專案 root 無 jsdom 也能真跑；`node --check` 語法綠）— 5 情境（空框/中途 caret/選取取代/tail glue/尾隨空白）＋連續插入鏈（`甲` → `甲 乙`、caret 同步推進）＋選取 verbatim 取代。
* `pyproject.toml` 0.9.115.2 → **0.9.115.3**。

### v0.9.115.2 — OpenRouter fallback 嵌入改 4B：與主嵌入維度**精確**一致（2560d native，免 pad 汙染）

**（user: 「openrouter 有 qwen/qwen3-embedding-4b」— live 實證：`POST /api/v1/embeddings` model=`qwen/qwen3-embedding-4b` → **HTTP 200、原生 dim=2560、served_by=DeepInfra**（OpenRouter 的 /models 目錄不列 embedding 模型，embeddings 端點直接吃 slug），正好對齊 `file_docs_4b`（2560d）主 collection。）**

* **動機**：v0.9.49+ 的 openrouter fallback leg 預設 `qwen/qwen3-embedding-8b`（4096d — 8B 時代主嵌入對齊）；v0.9.113.1 主嵌入切 4B 後，該 fallback 被 `_normalize` **pad 成 2560d → 語意空間不吻合**（2026-10-08 15:14-15:17 llama.cpp 掛掉事故的劣化面一 — 查詢向量維度對了、語意錯了，rerank 低信心 gate 全擋下）。fallback 設 4b = **同模型同維度**，llama.cpp 掛掉時查詢品質不劣化。
* **改法**：live `.env` + `.env.example` `OPENROUTER_EMBED_MODEL=qwen/qwen3-embedding-4b`（**程式碼預設不動 = 8B**；per-box/per-collection 由 .env 決定 — v0.9.113.1 回滾（8B 主）語意維持相容）；`src/ingest/embedder.py` 兩處 docstring 補 4B-era 註記。
* tests: `tests/test_embedder_resilience.py` +2（4b override 生效 / 程式預設仍 8b 契約）。
* `pyproject.toml` 0.9.115.1 → **0.9.115.2**。

### v0.9.115.1 — 看門狗 refused 快路治癒：連線被拒（明確死亡）免等 2-streak、立即重啟 llama-server

**（user: 「qwen3 embedding沒有自已帶起來嗎」— live log 2026-10-08：GUI 15:05:50 重啟、看門狗 15:05:52 首探即抓到 `ConnectionError 10061 refused — streak 1/2`；但 heal 需要「連續 2 次失敗」，第二發要等下一個 interval（預設 1800s）→ ~15:35 才會拉起；15:14-15:17 的查詢全落在此窗口 — Embedder fallback OpenRouter（Nebius）、4096d 被 `_normalize` pad 成 2560d、語意空間不吻合 → rerank 低信心 gate 直接擋下（best 0.13/0.22 < 0.30 → count 0）。live 殘留事故同場收案：bat spawn 的 llama-server 一啟即退（bat→Popen DEVNULL 吞錯 = 靜默），直接前台 exe spawn 帶 log 檔立即復活（READY ~88 ms、dim=2560 正確）— llama.cpp 的 exe 是 9-27 KB launcher stub + `*-impl.dll` 本體（8/13 的 DLL-split 布局），exe 檔案大小非問題。）**

* **根因**：v0.9.112.0 的 2-streak 是為**殭屍態**（程序活著、`POST /v1/embeddings` 500）設計的防誤殺緩衝；但 **refused（TCP 連線被拒）是零歧義的死亡狀態** — embed 埠上什麼都沒有（正在載模型的 llama-server 會**先綁埠**回 HTTP 503 loading，不會 refused）。「boot 時 llama.cpp 已死 → GUI 重啟 → 首探抓到了卻要再等 30 分鐘」= 設計保守缺口。
* **修法**（`src/embed_watchdog.py`）：`tick()` 失敗時加**原生 TCP 分類** `_port_state()`（refused / open / unknown）+ **tasklist 程序檢查** `_llama_process_alive()` — **refused 且查無 `llama-server.exe`**（活程序 + 關閉埠 = 可能還在載模型，spawn 第二隻會互搶 :8080 = v0.9.113.1 混維災難；程序表讀不到 = 保守走舊路）→ **跳過 streak 等待、立即 `_restart()`**（netstat 無 listener → spawn-only 路徑；bat 缺檔不動、600s 冷卻防迴圈照舊）。HTTP 500 / 逾時 / 壞形狀（殭屍態）與 open-port 慢回應仍一律走 streak=2。`QDRANT_LLAMACPP_WATCHDOG_REFUSED_HEAL=off` 退出（預設 on）；`status()` 露 `refused_heal` 供 `get_runtime_status` / runtime-local / GUI 檢視；tick 回報帶 `dead_now` 旗標。
* tests: `tests/test_embed_watchdog.py` +8（refused 首敗即 heal / 活程序保守觀察 / `_dead_refused` helper 矩陣 5 情境 / `_port_state` 分類 / env off 回 2-streak 契約 / 立即 heal 仍尊重 cooldown / status 鍵 / 預設 on）；既有 tick/lifecycle 測試補 `_dead_refused` mock（隔絕真網路與真 process table）= **33 passed**。
* `pyproject.toml` 0.9.115.0 → **0.9.115.1**。

### v0.9.115.0 — firefox_craw Fleet 上線（mcp_getweb v5.16→v6.0，協議 v2：多電腦／多 Firefox、唯一識別碼、service 串接 tree）

**（user 核准方案：①「每一個 firefox 都有唯一識別碼」②「service_server 支援多層串接」——全部落成 mcp_getweb 三件套升級；**qdrant-rag-agent 代碼零改動**（server.exe 為獨立 stdio MCP，工具面自動獲得））**

* **Fleet 架構（tree）**：唯一對外開埠的只有 root service（agent 機）；**下層機器全部 outbound 連回 root**（NAT/防火牆友善、零對入埠）。root 收 `/exec`（`params.target`）→ 解析：本機 browser_id 直派／屬某下層 service 的回報子樹 → 轉發（保留 target，最後一跳才剝掉送 xpi）→ response 沿原路上收。多層遞迴天然成立（子節點的 `_visible_downstream_ids` = 自身 Firefox 們 + 各子 service 的回報子樹）。
* **唯一識別碼**：xpi 首裝生成 `browser_id`（`ff-xxxxxxxxxxxx`，`storage.local` 持久），每次輪詢註冊；`/status` 新 `fleet` 樹（id/kind/hostname/via/在線/協議/子樹）；`/exec` 對 32+1 工具新增可選 `target`（省略＝主 Firefox＝v5 單機語意完全相容）；未知 target = 404 附已知清單。
* **安全**：`MCP_SERVICE_KEY` 已設 → `/exec` 全來源必須帶 `X-MCP-Key`（恆時比較）；未設 → 僅接受 loopback（v5 預設）；非 loopback bind 無 key 印大聲警告。`/poll` `/response` 不設閘（xpi 無法讀 server env；LAN 信任網段）。
* **child 模式**：`MCP_SERVICE_UPSTREAM_URL` + `MCP_SERVICE_NODE_ID`（預設 svc-\<hostname>）→ 1 秒短輪詢 root（`wait=0`，無 WS/取消/競態；ids 子樹 ≤1s 上浮、指令 ≤1s/跳）。
* **工程紀實（三次 E2E 迭代的生產級修正）**：①wake/cancel 競態設計整組**撤掉改 1s 短輪詢**（`asyncio.wait(FIRST_COMPLETED)` 的 done=∅ 症狀 + CancelledError 逸出殺死循環——簡單為王）；②exec 的 target **不可提前 pop**（service 轉發跳需保留）；③測試孤兒進程殘留會毒化後續測試（netstat 範圍清掃 + 執行實例隨機埠基準——schtask/run-and-self-delete 同款教訓）。
* **Live 換刀**：dist_v6 隔離建置 → 一次式 kill/swap/relaunch → `/health=6.0（protocol 2）`；真 xpi 5.16 在 5s 內重連為 legacy `default`（compatible: false — 如設計的升級路徑）；agent 端 stdio server.exe 已複製為 v6.0 並經 `restart_mcp_server` 重接（34 工具）。**人工步驟：about:debugging 重載 v6.0 xpi + 重整分頁**（重載前所有 browser exec = 409「請重載 xpi」— 版本閘如設計運作）。
* **測試**：`test_fleet_v6.py` 31 檢查全綠（多註冊/target 精確直派與 params 剝除/未知 target 404/無 target 主客戶端/legacy default 兼容/key 401×2+200/雙向版本閘 409/一層+兩層 tree 轉發/fleet 樹）；既有 `test_confirm_forward`（3）/`test_exec_400`（5）/`test_version_gate`（8）更新版號後全綠。
* **文件**：mcp_getweb readme（Fleet 節 + env 表 + 工具目標 note + manifest/結構 6.0）＋ modify_record 紀錄；本檔 + CLAUDE + .env.example（`MCP_SERVICE_*` 四變數註解塊）同步。pyproject 0.9.114.0 → **0.9.115.0**。

### v0.9.114.0 — 媒體多模態側通道：EmbeddingGemma 2 路面 + fail-closed 假成功守衛（規則 = 媒體走 ggema、文字走 Qwen3）

**（user: 「規則讓 EmbeddingGemma 做為 文件中附圖、音訊 或 獨立檔案或音訊 使用，文字還是走 qwen3」）** — 定義系統的**嵌入分工規則**並把通路骨架全部就位：

* **規則**：文字 chunks 一律走 Qwen3-4B 主路（`file_docs_4b`, 2560d）不變；**媒體**（文件內附圖 DOCX/PPTX media、獨立媒體檔）→ **EmbeddingGemma 2 的 768d 文字+影像+音訊統一空間** — 獨立 ggema server（unsloth b11443 build + `--mmproj`，port 8081 與主 4B server 並行）→ **側路 collection `media_docs_gemma2`（768d）** → **跨模態檢索**：文字 query 用 ggema 的文字塔嵌 → 找媒體向量點。
* **⚠ fail-closed 假成功守衛（本版核心 — v0.9.112 hash 污染教訓的同型防禦）**：上游查證（ggml-org#30082, 2026-10-07 closed as not planned + unsloth #247 內文一致）— **llama-server 的 embeddings 端點還沒接媒體輸入**：`--mmproj` 載入成功，但 `image_data` 的 POST 回 HTTP 200 + 一顆「**字面 `&lt;__image__&gt;` marker 的純文字向量**」— 兩張截然不同的圖 cos=**1.0000**（byte-identical 假成功；mtmd chat 通路則錯 `media markers (0) ≠ bitmaps (1)`）。**新模組 `src/ingest/media_embed.py` 的 `media_path_available()` 真實探針**：合成紅/藍兩張參考圖 → embed → `cos > QDRANT_MEDIA_FAKE_COS`（0.999）= 判「通路不可用」→ **零媒體點寫入**（不撒謊入庫）；verdict 快取（60 分 cooldown）— **上游 wiring 補上的那一刻，通路自動點燃、零代碼變更**。Live 實證：verdict=False（「no vector」+ 假成功兩型都有守）。
* **pipeline 掛鉤（全部 fail-open，主文字鏈 byte 不動）**：`_fill_image_descriptions` 的描述產出後加 **side-car**（每張附圖的 ggema 媒體向量 + 描述文本 + `media #N` 位置 → 媒體點）；**獨立媒體檔**：`ingest_file` 的媒體 ext 分流（`QDRANT_MEDIA_INGEST=on` = png/jpg/webp/gif/bmp/tiff 走 `_ingest_media_file` = describer 文字 → Qwen3 主池 + ggema 向量 → 媒體池；音訊/影像檔（mp3/mp4/…）= 檔名標註「wiring pending upstream」繼續跳過——媒體 embed 端點通了再開）。
* **MCP 跨模態工具**：`QdrantLocalHost.search_media(query, top_k, filename_filter)`（in-process + stdio mirror 三件套）— 誠實面：gate off / 假成功 verdict / embed 失敗都回 error + 指向 `search_documents` 的描述文字檢索（媒體語意已在 Qwen3 池，**功能不缺**）。
* **config 4+1 env**：`QDRANT_MEDIA_EMBEDDING`（預設 off；live .env **on**）+ `QDRANT_MEDIA_INGEST`（預設 off；live **on**）+ `QDRANT_MEDIA_COLLECTION`（media_docs_gemma2）+ `GEMMA2_EMBED_URL`（127.0.0.1:8081，localhost 自動改寫）+ `QDRANT_MEDIA_FAKE_COS`（0.999）。
* **server**：`start-llamacpp-gemma2.bat` 加 `--mmproj mp-q8.gguf`（555MB 已下載就位 — vision+audio encoders init OK，等 embeddings wiring）。
* tests +25 `tests/test_media_embed.py`（gate/假成功偵測/快取/endpoint 錯/無 Pillow/檔案缺/media vector/embed batch 的 payload 契約/pipeline 分流與 hook/tool 註冊與誠實 error/stdio parity）；media+headings = 61 passed。
* pyproject 0.9.113.3 → **0.9.114.0**；README(+ROADMAP)/CLAUDE/ARCHITECTURE/USER_MANUAL/.env(.example 5 env)/config.py 同步。

### v0.9.113.3 — EmbeddingGemma-2 上線：unsloth fork b11443 跑通 + 三路嵌入式對照

**（user 找到 unslothai/llama.cpp `b11443-mix-d65395f` fork build — release notes 載明 **「EmbeddingGemma-2 support (unslothai/llama.cpp#247, commit 73c2f73)」— 上游 GGUF/convert 這條路通了）**：

* **build**：`app-b11443-mix-d65395f-windows-x64-vulkan.zip`（28MB，基於上游 b11443 比現役 b10423 新 ~1000 build；另有 `llama-diffusion-gemma-visual-server.exe` 等 gemma 生態工具）→ 解壓 **`C:\llama.cpp-un\`**（**現役 `C:\llama.cpp-vulkan\` 完全不動**）；
* **GGUF**：`unsloth/embeddinggemma-2-GGUF` 的 **`embeddinggemma-2-Q8_0.gguf`**（310MB — unsloth 警告 FP16 會 NaN，BF16/Q8_0 才是推薦；mmproj-* 是影像 projector 之後再戰）；
* **server**：`C:\llama.cpp-un\start-llamacpp-gemma2.bat`（`--embedding` + 8K ctx + 同款 tuned flags；**port 8081** = 與 4B 主 server 8080 並行、0.4GB VRAM 共存無壓力）+ `c:\qdrant\_bat\startgemma2.bat`（run-and-self-delete wrapper，同款 schtasks 教訓）；
* **語意 sanity**：近義句 0.90 vs 異主題 0.74/0.56（方向正確；辨別度略平 = 未套 gemma 的 `task: search result \| query:` prompt 格式的同等不利 — 三路對照公平性一致）；
* **`eval/` 工具升級**：`ingest_side.py` / `eval_driver.py` 新增 `--url` 參數（side collection 可指向任一 embed server；LLAMACPP_EMBED_URL 覆寫）；`ingest_side.py` 的 DEFAULT_FILES 補齊 README + SSRN = 22 檔全集；
* **side collection `text_docs_gemma2`**（768d + heading_path 欄 + bm25 schema）= **4207 點全量**（README 1380 + SSRN 1962 補嵌完成）；
* **三路對照（同 20 題、全池公平）**：

  | | Qwen3-8B（4096d） | **Qwen3-4B（2560d，主）** | **EmbeddingGemma-2 270M（768d）** |
  |---|---|---|---|
  | hit@5 | 19/20 | **20/20** | 17/20 |
  | rank1 | 15 | **17** | 13 |
  | 嵌入延遲 | ~147ms | ~120ms（LRU/快取 2-9ms） | **~20ms** |
  | 本機實測 VRAM | ~5.2GB | ~2.8GB | **~0.4GB** |

* **結論**：主嵌入維持 **4B**；gemma2 收錄為**輕量第三 backend**（7 倍快於 8B、5 倍快於 4B、VRAM 最省 — 適合低成本旁路檢索/以後的影像索引）；3 個 MISS 全是 README 泛文搶位（STAIR boost / rerank 可解）；pyproject 0.9.113.2 → **0.9.113.3**；README ROADMAP + CLAUDE.md 同步。**影像/音訊嵌入**：unsloth 的 mmproj projector 與 mtmd 通路是下一階段（文字嵌入本版跑通）。

### v0.9.113.2 — 8B 啟動 bat 改名（`start-llamacpp.bat` → `start-llamacpp-8b.bat`）

**（user: 「rename 原本 8b 的.bat 檔名要加8B 字眼」）** 兩個 8B 啟動 bat 改名後，`start-llamacpp-4b.bat` 與舊主 8B bat 的名稱一眼可分：

* `C:\llama.cpp-vulkan\start-llamacpp.bat` **→ `start-llamacpp-8b.bat`**（Vulkan 主 8B）；`C:\llama.cpp\start-llamacpp.bat` **→ `start-llamacpp-8b.bat`**（CPU fallback 8B）— 兩者內容零變更（純改名）；
* `src/embed_watchdog.py`：`DEFAULT_START_BAT` + `_BAT_CANDIDATES` 同步 `-8b`；`c:\qdrant\_bat\start8b.bat` wrapper 同步；`tests/test_embed_watchdog.py` 的 Popen 斷言隨新名（1 行）；
* 現行文檔同步：`.env`／`.env.example`（watchdog/啟動器註釋）、README（回滾行 + v0.2.5 週期描述走歷史不動）、USER_MANUAL（安裝節 = 4B 主 + 8B、watchdog 行為 + 回滾行）、ARCHITECTURE（25.3 節 + ops 表 + ASCII 慣例 + Appendix A watchdog）；**歷史 changelog 週期記錄保留舊名（記當時事實）**；
* 主嵌入不受影響：`.env` 的 `QDRANT_LLAMACPP_START_BAT` 仍指向 `start-llamacpp-4b.bat`；pyproject 0.9.113.1 → **0.9.113.2**。

### v0.9.113.1 — 正式切換主嵌入 = Qwen3-Embedding-4B（2560d）

**（user: 「切換成4B，資料要重算」）** 依 v0.9.113.0 的 eval 對照結果正式把主 collection/embedding 從 8B 切到 4B：

* `.env`：`QDRANT_COLLECTION=file_docs_4b` + `EMBEDDING_DIM=2560` + `EMBEDDING_BACKEND=local`（**auto → local 明確化** — auto 在 llama.cpp 掛掉時會 failover 到 openrouter 的 4096d 模型，向量被 pad 成 2560d 寫入 = 語意汙染；明確 local 時失敗走 hash:`<dim>` key、審計抓得到）；
* **看門狗啟動 bat 同步**：`QDRANT_LLAMACPP_START_BAT=C:\llama.cpp-vulkan\start-llamacpp-4b.bat`（否則看門狗若觸發重啟會拉起 8B → 4096d 向量 pad 進 2560d collection 且形狀探針驗不出 dim 差）；
* **主 collection 資料**：side 子集 869 塊已在；缺的 README（1380 塊）+ SSRN（1962 塊）經 `eval/ingest_side.py` 補嵌（已嵌塊走 dedup 零成本）；**舊 8B collection `file_docs`（4248 塊/4096d）原封保留 = 五分鐘可回滾**（`.env` 改回 + `start-llamacpp-8b.bat`）；
* **schtasks wrapper 教訓**：`once` task 留在排程裡會在 scheduled time **再 fire 一發** — 兩個 llama-server 共享 ：8080 隨機派發 = mixed-dim 災難（本版實際抓到 8B+4B 並存 20 分鐘的現場）；wrapper 重寫為 **run-and-self-delete**（`start` detached + 秒級自刪 task — `c:\qdrant\_bat\start4b.bat` / `start8b.bat`）；
* 無程式碼變更（純 config + ops）；pyproject 0.9.113.0 → **0.9.113.1**；README ROADMAP 同步狀態。

### v0.9.113.0 — ROADMAP 三項結案：STAIR 標題層級 metadata + Qwen3-4B 並行驗證（cache key 撞鍵 bug 修復）+ EmbeddingGemma 2 查證

**使用者核准「執行待3項」（README ROADMAP 的三個 todo）+ 模型交替窗口可用。** 三項全部完成；其中 Qwen3-4B 驗證過程踩出一個**生產級 cache 汙染 bug**（順手修復，這是本版最重要的修復）。

1. **STAIR 標題層級 metadata（ROADMAP 項 2）** — 新模組 `src/ingest/headings.py`（zero-dep）：ATX heading 行偵測（`^#{1,6}`; PPTX 載入器的 `# Slide N: title` 行原生相容）→ `heading_sections()` 以標題行為界切 section、heading stack 累積成 `heading_path`（「H1 > H2 > H3」）；`TextChunker.chunk_document_with_headings()` 每個 section 獨立 Recursive split、chunk 繼承 `heading_path`、`chunk_index` 全文件連續、**無 heading 文件退回舊路（byte-identical）**；`src/ingest/loader.py` 的 DOCX 路徑加 `_docx_heading_prefix()`（Heading 1-6 / 標題 1-6 styles → ATX 前綴）；pipeline `QDRANT_HEADING_METADATA=on`（**預設 off** — 字節級不破壞既有 ingest 雜湊行為）+ fail-open；payload 新欄位 `heading_path`（全 ingest 路徑都寫，off 時為空字串）+ collection schema 的 KEYWORD payload index；**既有舊點走 metadata-only 回填**：新 `src/ingest/heading_backfill.py`（`backfill_collection` — scroll 全點按 `(source, chunk_index)` 排序、每塊由自身 text 反推 heading（塊內最後一個 heading 行勝出、否則沿前塊 carry-over，跨塊 heading 邊界的前緣是記錄在案的 best-effort 限制）、`set_payload` 按 path 分組批次寫、**向量/ID/分塊邊界完全不動、零重嵌**、`--force` 可重算、payload index best-effort）→ `cli.py backfill-headings [--force] [--collection]` + GUI Admin Embedding 卡「§ 回填標題」按鈕（i18n ×4，沿 112.0 audit-backends 同型）；搜尋側 `QDRANT_HEADING_BOOST`（浮點，預設 0=off）：heading 與 query 詞雙向 substring 重疊的命中分數 +boost 再排序（在 rerank 之前，Jev rerank 仍可接管）；`_map_hits` meta 白名單加 `heading_path`（搜尋結果自然露出）。**已知限制**：code-fence 內 `#` 註釋行會被誤認為 heading（本版記錄不阻擋 — 對主效益影響小）。**Live 實證**：heading-aware ingest ARCHITECTURE.md = 480 塊（legacy 440）、`heading_path` 樣本全對（`divine-sense-agent — Technical Design Guide > 1. System Overview > 1.2 Historical arc…`）、boost ON 對 heading 命中題 +0.10 標記生效。tests: `tests/test_headings.py` +36。

2. **Qwen3-4B 並行驗證（ROADMAP 項 1，使用者實驗）→ 已代跑完成** — **`eval/` 三件套**（食譜可再執行）：`eval/eval_20q.json`（20 題×期望來源檔，由 file_docs 20 檔取材）、`eval/eval_driver.py`（對指定 collection 跑 search top-5、expect 檔不在池的題自動 SKIP、報 hit@5/名次/embed ms）、`eval/ingest_side.py`（把 eval 子集 ingest 進任意 side collection — per-collection backend 切換即 process env）。**GGUF 供應確認**：`registry.ollama.ai` blob `sha256:2b0cf8f1…0f16b`（2,496,703,776 bytes，SHA256 全檔驗證 MATCH）= HF `Qwen/Qwen3-Embedding-4B-GGUF` 同 Q4_K_M；本地 llama.cpp b10423 同 arch 直接支援。**`C:\llama.cpp-vulkan\start-llamacpp-4b.bat`**（v0.9.25.3 tuned flags 照搬、MODEL 換名）+ `C:\qdrant\_bat\start4b.bat`/`start8b.bat`（schtasks detached wrapper）— **交替式驗證**（停 8B→跑 4B→換回；watchdog 探針「只驗形狀不驗 dim」故對 4B 也綠、無誤殺）。**對照結果（eval 20 題）**：8B baseline `hit@5 19/20、rank1 15`（全量池 4248 塊、embed ~147ms）vs 4B `hit@5 19/19、rank1 18`（side 池 869 塊、embed ~123ms — 4B 在所有可比題上名次 ≥ 8B、VRAM 減半）。**結論：品質相當（4B 略優）、速度相近、VRAM 減半 — 收錄為可用第二 backend；正式切換由使用者自主決定**（切法 = `.env` 停/起對應 bat + `QDRANT_COLLECTION=file_docs_4b` + `EMBEDDING_DIM=2560`）。pyproject 0.9.112.1 → **0.9.113.0**。

3. **⚠️ cache key 撞鍵 bug 修復（4B 驗證過程現場抓到的生產級汙染）** — `Embedder` 兩層快取的 key **都不含 model 識別**：disk key 對 `local` 固定寫 `"llama.cpp"`（llama.cpp server 不回 model id）、LRU key **只有 text-sha1** — **把 :8080 的 GGUF 從 8B 換成 4B 後**：(a) disk cache 命中 8B 的 4096d 舊向量 → `_normalize` 截斷成 2560d → **查詢向量語意殘缺**（eval 第一輪 1/19 MISS、top5 全雜訊的根因）；(b) 被截斷的向量**又被寫回 cache**；(c) 更早的 ollama-batch ingest run（ReadTimeout 後 fallback hash）把 hash 向量經 dedup 跳過機制**殘留在同 collection**（`embedding_backend` 標籤 = 實證）。修法 = **兩層 key 一律帶 (backend, model)**：`_model_for("local")` → `f"llama.cpp:{self.dimension}"`（`hash` 同型 → `hash:<dim>`）、`_cache_key()` 由 staticmethod 改 instance method（key = `sha1(text):backend:model`；explicit backend 解析零網路成本、auto 鏈有 300s probe 冷卻快取）。**副作用（記錄在案）**：舊 disk rows（`llama.cpp` 無 dim 版）不可達 → 下次 ingest 對每檔**重嵌一次**（disk cache 只是加速器；主 collection 的向量不受影響）。

4. **EmbeddingGemma 2 查證（ROADMAP 項 3）** — 2026-10-06 發布(本 session 查證)：`google/embeddinggemma-2`、Apache 2.0、740M（text 270M + vision 170M + audio 300M）、768d 統一向量空間、Mean Pooling、MRL 128/256/512/768、8K ctx。**Ollama library 已上架**（`embeddinggemma-2:270m` 378MB text-only / `:440m` 714MB text+vision / `:540m` 990MB text / `:740m` 1.3GB full — vision 與 audio 另有 bf16/mxfp8/nvfp4 變體）。**本地實證**：本機 `ollama 0.34.3` pull 該模型 → **HTTP 412「需要更新版 Ollama」**（Gemma 4 架構需要 runtime 支援）；Ollama **cloud** 端 `/v1/embeddings` 對全 embeddinggemma-2 變體 **404 = cloud 不供嵌入模型**；**llama.cpp 路徑目前不可行** — llama.cpp v0.6.0（2026-10-05 release）release notes 未含 embeddinggemma-2 支援，且 ollama manifest 的 tensor 層是 **nvfp4 量化 split 格式**（llama.cpp GGUF loader 不吃）→ **待上游對 embeddinggemma-2 的 GGUF + convert 支援落地；ollama ≥0.40（winget 有）是現階段唯一本地通路**（使用者決定先不升級 — 記錄觀望 llama.cpp）。**升級 ollama 後的用法**（就緒時）：`EMBEDDING_BACKEND=ollama + OLLAMA_BASE_URL=http://127.0.0.1:11434/v1 + OLLAMA_EMBED_MODEL=embeddinggemma-2:270m` + `eval/ingest_side.py text_docs_gemma2 768` — **注意本機 ollama 嵌入走 CPU**（單句 486ms、64 條批次 >120s 超時 — 批次需縮小或只做 eval 級單句嵌入；多模態（影像/音訊輸入）等同 runtime 支援後 `/api/embed` 實測）。**ROADMAP 節已更新狀態。**

**測試**：+36 `test_headings.py`；`test_embedder_resilience` + `test_reembed_admin` 回歸 98 passed（2 個 v0.9.48 keying 契約測試隨新 key 形狀更新 — 本質斷言不變：hash 向量永遠在 hash 標籤 key 下）。


使用者回報「每次重啟 Qdrant 和 LLM 都會顯示 逾時」— v0.9.80.1 同症狀的**殘留面**：當時只把 mcp 列改非阻塞，qdrant/llm 兩列仍「陪葬在第一發 `get_readiness` 回應」上。實測證據（本機）：`(a)` boot 時 prewarm thread 建 MCP fleet 榨 CPU **16 秒**（app.log 10:54:24→10:54:40）＋ `(b)` 首呼 `Api._get_store()` 要 **~2.5-2.7 s**（qdrant-client 連線 + collection 檢查）— 兩者疊加，冷啟動的第一發 readiness 經常 >5 秒 = splash 的 per-row timeout（`SPLASH_CHECK_TIMEOUT_MS=5000`）→ qdrant **和** llm 兩列同印「逾時」（llm 檢查本身純 env 讀取 0 ms，只是被同一發回應拖下水）。

* **`gui/api.py` — `_qdrant_stats_check()`**：stats 探針進共享 thread-pool（`_readiness_executor`，max 2 workers）＋硬 timeout `_READINESS_STATS_TIMEOUT_S=2.5`；**timeout → `_readyz_probe()`（一發 urllib GET `<QDRANT_URL>/readyz`，2s，**不建 store 不呼 qdrant-client**）** → 活著就 `ok=True, "server up (readyz; stats pending)"`（splash 語意「啟動確認」以伺服器活著為準；counts 是之後每次 poll 都拿得到的好东西）。raise 分支照舊逐字錯誤（`test_qdrant_fail_surfaces_error` 契約不變）。Worst case = 2.5 + 2 = 4.5 s < 5 s row timeout — **兩列不再可能逾時**；超時後 store 建構仍在背景完成 → 暖 `Api._store` 單例 → 之後 poll 帶完整 counts。
* **detail 修正**：`get_stats()` 的維度欄位本來就叫 `dim`，readiness 卻讀 `vector_dim`（幽靈鍵）→ splash 一直顯示 `?d`；改為 `stats.get("dim", stats.get("vector_dim", "?"))`（兩種拼法都收，舊 mock 測試不破）。
* **llm 列擴 5-provider**（v0.9.18/19.0 鏈）：`OPENROUTER_API_KEY` / `SILICONFLOW_API_KEY` / `OPENAI_COMPAT_API_KEY(+BASE_URL)` 現在也算「已配置」— openrouter 為主的安裝不再顯示誤導的 ❌ LLM API 金鑰 列；detail 為有 key 者的 `" + ".join`（order: ollama, openai, openrouter, siliconflow, openai-compat — 只列有訊號者，舊 detail 契約不變）。
* 前端 `app.js` 零改動（row timeout 5s 保留 — 修的是後端讓第一發必在期限內答）。

**測試**：`tests/test_gui_readiness.py` +6（stats 慢 → readyz ok 拿 `"readyz"` detail；慢 + readyz 死 → ok=False 顯式錯誤；`_readyz_probe` 200/refused×2；llm 全 provider detail；dim 幽靈鍵修復）— **23 passed**；同一檔既有 17 個契約（llm detail 精確字串、fail 逐字錯誤、never-raises、idempotent）全數不破。Live 實證：`_get_store` 2,539 ms 情境下 `get_readiness` 答 `4248 pts / 4096d | llm=ollama + openai + openrouter + openai-compat`（#1 之後 0-1 ms，stats 單例快取生效）。

`pyproject.toml` 0.9.112.0 → **0.9.112.1**。

### v0.9.112.0 —嵌入韌性：llama.cpp 看門狗 + hash 向量污染防護（audit/re-embed）

使用者要求評估 `project_improvement_plan_v3.md` 建議書（JAZ 風格逐項查證）：**P0-2（關 prompt cache）已於 v0.9.25.3 落地結案**；P0-4（auto 改 openrouter 優先）與 P1（Nimble 決策本地化）**不採用**（2026-10 session 新實測：幹淨重啟後 local llama.cpp ~33 ms/塊 ≈ openrouter 的 1/10-1/12 延遲，且 rerank.py 已記錄 Nimble local prefill 40-100 s 不適用互動路徑）；「記憶遺忘層」大半已由 v0.9.111 覆蓋。實際動工的是兩個真缺口：

* **A. llama.cpp 嵌入看門狗（第三個 auto-heal，唯一可 KILL 的）** — boot-time heal（v0.9.109.3/109.4）只抓「程序死了」（refused = 可探）；llama.cpp 有第三種死法 **殭屍狀態**：程序活著、`GET /v1/models` 仍 200、但每次真正的 `POST /v1/embeddings` 都失敗（2026-09-14 live log 的 Vulkan `ErrorDeviceLost` 每次都撞 500）— `Embedder._llamacpp_available()` 只探 `/v1/models`，殭屍通過可用性探測、auto 鏈每筆嵌入白付一次失敗 POST、冷卻機制把全部嵌入推到 OpenRouter（~400 ms/塊 = 健康延遲的 ~10 倍）直到有人手動重啟。新模組 `src/embed_watchdog.py`：
  * 每 `QDRANT_LLAMACPP_WATCHDOG_INTERVAL`（預設 1800s）打**真實嵌入 POST**（`{"input": "embed watchdog probe"}`）並驗回應形状（HTTP 200 + `data[0].embedding` 非空）— 只信 HTTP 結果**不信延遲**（ingest 跑大 batch 時健康伺服器也會慢；延遲判定會誤殺）；probe timeout 15s + **連續 2 次失敗**才動作。
  * 修復 = `netstat -ano` 解析嵌入埠（:8080）的 Llama-server PID → `taskkill /F /T /PID`（**netstat 狀態欄位本地化**——zh-TW 印「聆聽中」不是 LISTENING，解析故意不濾狀態，只對「local 位址.endswith(:port) + 末欄數字 PID」）→ detached spawn `QDRANT_LLAMACPP_START_BAT`（預設 `C:\llama.cpp-vulkan\start-llamacpp.bat`、fallback `C:\llama.cpp\`；Popen 形状 identical於 109.x healers）→ 等就緒 ≤150s（模型重載要幾十秒）。
  * 重啟冷卻 600s 防迴圈；bat 缺檔 = 不 kill 只留診斷（不會殺了不能复活的伺服器）；tick 錯誤全吞 + 記 state；`QDRANT_LLAMACPP_WATCHDOG=off` 退出（status 仍答）。netstat 解析在 live box 實證：779 行內正確抓到 :8080 的 PID 170404。
  * 接線：`gui/api.py::_start_embed_watchdog()`（prewarm 掛、永不 raise）+ `Agent._runtime_ops("status")` 加 `embedding_watchdog` 區段（get_runtime_status / runtime-local MCP / GUI 皆可見 last_probe/latency/streak/restarts）。
* **B. hash 向量污染防護（tag + audit + repair）** — embed 鏈最後一手是**非語意**的 hash fallback；v0.9.112 前什麼都不記 → 劣化批次悄悄污染 collection 且無法偵測/善後。三段：
  1. **TAG**：`Embedder.embed_batch` 記 `last_backends`（per-item 實際產出向量的 backend——沿用 v0.9.48.0 `_last_backend` 機制延伸；LRU/disk 命中項以「當下 RESOLVED backend」標註，best-effort）→ `pipeline._embed_and_upsert` payload 寫 **`embedding_backend`**。
  2. **fail-closed 旗標**：`QDRANT_EMBED_FAIL_CLOSED=on`（預設 off = 標記後繼續、行為同舊版）→ 退到 hash 的批次在 **upsert 之前** raise——該檔由 ingest 的 per-file try/except 跳過並印明確錯誤，collection 零污染。
  3. **AUDIT + REPAIR**：`src/ingest/reembed.py` — 審計把每個點的 `payload.text` 重算 hash 向量（經新公開 `Embedder.hash_fallback_vector()`，無網路無快取）與存點向量**精確比對**（np.allclose f32 容差）→ **標記不存在/錯誤的舊資料點也偵測得到**（tag 只是標籤，重算才是真相）。重嵌走新公開 `Embedder.embed_fresh()`（**繞過 LRU+disk 兩層快取**——正常 `embed()` 會從快取回同一顆污染向量、修不動；所有 backend 都 down 時重嵌仍產 hash → 該點**不 upsert**、記 `still_hash`，修復不能撒謊）。hybrid collection 的 named-vector 形状（dense `""` 腿）已處理、sparse-only 點跳過。暴露面：GUI Admin Embedding 卡片 🔎 審計 / ♻️ 重嵌 按鈕（`audit_embedding_backends` / `reembed_hash_points`）+ `cli.py audit-backends [--batch]` / `reembed-hash [--limit]`（照 v0.9.87 `backfill-sparse` 同型）。

**ROADMAP / Backlog（project_improvement_plan_v3 評估存查 — 本節記錄評估結論供後續）**：
* ~~**Qwen3-4B 並行驗證食譜**（P1）~~ **✅ v0.9.113.0 結案（已代跑）→ v0.9.113.1 正式切換為主嵌入**：`eval/eval_20q.json` + `eval/eval_driver.py` + `eval/ingest_side.py` 三件套 + `C:\llama.cpp-vulkan\start-llamacpp-4b.bat`（GGUF = registry blob `sha256:2b0cf8f1…0f16b` 驗證 MATCH）。**對照結果**：8B `hit@5 19/20、rank1 15、~147ms` vs 4B `hit@5 19/19、rank1 18、~123ms、VRAM 減半` — 品質相當（4B 略優）。**v0.9.113.1：`.env` 主 collection 改 `file_docs_4b`（2560d）+ `EMBEDDING_BACKEND=local` 明確化 + 看門狗 bat 同步指向 4B；缺的 README/SSRN 大檔補嵌完成；舊 8B collection `file_docs`（4248/4096d）原封保留 = 回滾只改三行（bat + QDRANT_COLLECTION + EMBEDDING_DIM）**。
* ~~**STAIR 標題層級 metadata**（P2）~~ **✅ v0.9.113.0 結案**：`src/ingest/headings.py`（ATX heading → heading_path 累積）+ `chunk_document_with_headings`（heading-aware 分段）+ DOCX Heading→`#` 前綴 + payload `heading_path` + KEYWORD index + `cli.py backfill-headings` / GUI「§ 回填標題」（metadata-only 零重嵌回填）+ `QDRANT_HEADING_BOOST`（搜尋加成，預設 off）；全部以 `QDRANT_HEADING_METADATA=on` opt-in（預設 off = byte-identical 舊 ingest）。已知限制：code-fence 內 `#` 注釋行會被誤認 heading（記錄在案）。
* **EmbeddingGemma 2 多模態**（P2 觀察項，2026-10-06 發布）— **v0.9.113.3 上線**：**unsloth fork `b11443-mix-d65395f`（2026-10-06 release notes 載「EmbeddingGemma-2 support」, unslothai#247）跑通** — build 28MB 解壓 `C:\llama.cpp-un\`（現役 llama.cpp-vulkan 不動）+ `unsloth/embeddinggemma-2-GGUF` 的 `Q8_0`（310MB — FP16 會 NaN，BF16/Q8_0 才推薦）；`start-llamacpp-gemma2.bat`（8081 並行 4B 主 server、0.4GB VRAM）+ `c:\qdrant\_bat\startgemma2.bat` wrapper；**side collection `text_docs_gemma2`（768d）= 4207 點全量**；`eval/ingest_side.py` + `eval_driver.py` 加 `--url` 通吃多 server。**三路對照（全池公平、同 20 題）**：8B 19/20（15 rk1、~147ms）< **4B 20/20（17 rk1、~120ms）← 主** vs gemma2-270M **17/20（13 rk1、~20ms、0.4GB VRAM）** — gemma2 收錄為輕量第三 backend（MISS 3 題 = README 泛文搶位，STAIR boost/rerank 可解）；**未完**：mmproj-BF16（982MB 投影器）+ mtmd 通路 = 影像/音訊嵌入下一階段；ollama ≥0.40 的 412 通路照舊觀望。**v0.9.114.0 媒體側通道規則落地**：媒體（文件附圖/獨立媒體檔）走 ggema 的 768d 統一空間（`src/ingest/media_embed.py` + pipeline side-car / `_ingest_media_file` / `search_media` MCP 工具 + `media_docs_gemma2` 側路 collection），**fail-closed 假成功守衛**（上游 #30082 — 媒體 embeddings wiring 未通、image_data 回字面 marker 的純文字向量；兩圖 cos>0.999 = 判不可用 = 零假點入庫；描述文字路（Qwen3 池）照常 = 媒體語意功能不缺；上游通後 cooldown 自動點燃）。
* **EmbeddingGemma 2 多模態**（P2 觀察項，2026-10-06 發布）：前提 = llama.cpp/Ollama 對該模型的 embed 支援成熟 + 中文品質對照 Qwen3-Embedding 實測不退；**v0.9.122.1 查證結案 + 同日上線：上游影像嵌入已通**（llama.cpp #30054、2026-10-06 合併、b11457+ build 的 `/v1/embeddings` **巢式 content-parts** 形狀；ggml-org#30082 為形狀誤報、已被提報者撤回）— `media_embed.py` 已改巢式第一順位；**server 預建已升級 unsloth b11541-mix-2500203**（2026-10-13，47 舊 exe/dll 存 `_old_b11443\` 回滾），8081 live 探針 **verdict=REAL cos(red,blue)=0.8795**、不同圖 0.8094、**音訊 input_audio 亦真通**（兩波形 cos=0.9114）＝通路已點燃。中文品質對照 Qwen3-Embedding 實測不退的前提照舊。
* **已否決項**（理由存查）：embed auto 改 openrouter 優先（v0.9.105 session 幹淨重啟實測推翻 GPU-cap 結論，local 33ms/塊最快最便宜，看門狗負責韌性）；Jev/Nimble 決策本地化（rerank.py 實測 RX 5700 XT Nimble prefill 40-100s；Jev 單呼叫 0.4-0.8s 已是折衷）；矛盾記憶自動清理（語意衝突偵測成本高，v0.9.111 hit-tracking + purge 工具已供決策依據，排程清理走現成 schedules → purge_memories）。
* 音箱韌體更新 = 硬體活動，不在本專案 code 範圍。

**測試**：＋25 `tests/test_embed_watchdog.py`（探針 ok/500/transport/空 body、tick streak/cooldown、netstat 解析（本地化 + 精確埠尾 + IPv6 + 去重）、重啟全鏈（kill→spawn 形状/cwd/DETACHED/DEVNULL、程序已死只 spawn、bat 缺檔零動作、ready 窗口耗盡）＋ prewarm/status 接線（含行為測試）＋ singleton/idempotent/stop）＋32 `tests/test_reembed_admin.py`（last_backends 標籤 local/hash/長度、embed_fresh 繞快取（poison-row 實證：embed() 回舊 hash 向量 vs embed_fresh 新真向量）、hash 向量確定性、pipeline 標記/空標籤/fail-closed on 寫零筆/off 照寫/bool 解析、audit（tagged+untagged 精確比對/dim-mismatch skip/never-mutates/envelope）、reembed（修復+標記更新/still-hash 不動/limit/單點錯誤含入/upsert 分批 3+3+1）、gui 包裝×3、cli 接線、前端按鈕/i18n ×4、純函式 helper ×7）。回歸：embedder_resilience/runtime/autoheal×2/prewarm + 全綠。

`pyproject.toml` 0.9.111.0 → **0.9.112.0**。

### v0.9.111.0 — 作用域注入 + fire 模型 pin + 機械交接檔 + 記憶使用追蹤
（user 核准採用 JAZ 框架 5 建議中的 4 項；原則 = 走既有機制、
   不自造新架構、fail-closed）

* **① 計畫/工具作用域機制化 `tools_allow`**（jAZ 第 3 項；機制 = v0.9.14.0
  per-TOOL 閘，非 prompt 規勸）：`compile_plan(tools_allow=[...])` plan
  級預設 + `stages[*].tools_allow` 可覆寫 → `_run_stage` 經
  `chat_turn(active_tool_names=…)`（v0.9.14 GUI picker 同一閘門）—
  每 stage 回合**只暴露**清單內的工具（confirm 閘/沙盒是 code 級
  不受影響；未知工具名 = 該工具不暴露、絕不回落全量）。compile
  fail-closed 驗證（非 list / 空 list / >60 個 → PlanError）。
  `get_plan_status` 每 stage 露出 `tools_allow`/`handoff_path`；
  compile tool schema + system prompt Skill 區塊同步。
* **② fire 層模型 pin**（JAZ 第 5 項殘餘槓桿；重用 v0.9.110
  task_llm）：`Schedule.llm` / `WebhookTrigger.llm` 新欄位
  （`"provider:model"`，限 openrouter/ollama）— ADD 時
  `task_llm.coerce_llm_pin`（新 helper）目錄驗證 fail-closed；FIRE
  時失效 pin **自動降級**預設鏈+警告（排程韌性優先 — pin 壞了
  排程仍要跑，與 task 投放的 refuse 語意刻意不同）。五個 fire
  factory 全部升級 `lambda llm=None: Agent(..., task_llm=llm)`
  （agent.py scheduler/webhook/teams-adjacent、cli ×2、gui/api
  run_now、host run_now、stdio scheduler_server run_now）；
  task registry 的 begin_task 帶 `llm`。CLI `schedules add --llm`
  + 驗證。
* **③ 機械交接檔約定**（JAZ 第 2 項的 80% 版，不動架構）：stage
  終局時**驅動器**（非 stage LLM）寫 `out/<stage_id>.json`
  （write sandbox 下；status/summary≤2000/artifacts/session 指標），
  有依賴的 stage 的 compiled_prompt 自動注入「上游交接檔」絕對
  路徑清單 + read_first 指引 — 無損交接不靠 LLM 自覺（LLM 合規
  不再是契約的一部分）；寫檔失敗降級、永不影響 stage 本身。
* **④ 記憶使用追蹤 + 結論沉澱**（JAZ 第 1 項；v0.9.59 補完）：
  recall 命中 → `MemoryStore.touch_hits`（fire-and-forget thread）
  回寫 `hit_count`/`last_hit_at`（既有點視為 0；
  QDRANT_MEMORY_HIT_STATS=off 退出）→ `list_recent_memories` 露出
  `usage`（total/hit_tracked/never_hit/never_hit_older_than_s）
  供 purge 清理決策；run_task_once DONE + plan 終局 →
  `store_task_result`（`kind=task-result`，**store-NOT-recall**：
  `_memory_block` 自動召回排除此 kind — v0.9.67.2 fire 記憶教訓
  的隔離設計；發現走 `search_memory(kind='task-result')`；
  `QDRANT_TASK_MEMORY_SINK=off` 退出）。
* 順手修：`test_task_runner.py` 的 3 個 v0.9.110 過時測試
  （tool surface 3→4、`_fresh_agent` 零參 stub → `lambda llm=None:`）。
* 不採用（記錄理由）：JAZ 第 2 項全量（生成子 Agent 變數傳遞 —
  違反不自造大機制；檔案即交接物，本版約定化而已）、第 4 項
  自我優化（script_library 已有 dedup+runs 計數；runs≥N 升格
  named 以 prompt 習慣處理即可）、web_fetch 等唯讀工具快取
  （watch/webhook 語意 = 新狀態，快取直接破壞 fire 判斷）。
* TESTS — +18 `tests/test_plan_scope_handoff.py`（驗證矩陣/run 期
  kwarg 形狀/交接檔注入+寫入/降級/plan sink×2/host schema×3）+
  +13 `tests/test_fire_llm_pin.py`（schedule/webhook pin 通行+降級+
  probe 例外、from_dict 相容、ADD fail-closed ×4、schema/CLI 來源
  鎖）+ +21 `tests/test_memory_usage_tracking.py`（touch 演算/閘/
  usage/排除過濾形狀/整合/沉澱閘/清場）；回歸
  skill+task_llm+task_parallel+memory_store=107 passed，
  task_runner 25、scheduler+server+verification=127、
  webhook+server+task_runner=110、schedule_cli 6 — 全綠。
* pyproject 0.9.110.1 > **0.9.111.0**。重啟 GUI 後吃新工具面/prompt。


### v0.9.109.2 — 專案完整使用者手冊（USER_MANUAL.md + PDF）

**（user: 「製作一本專案完整全功能說明書，提供使用者操作參考」）** —
新 `USER_MANUAL.md`（~21KB，25 章 + 兩附錄）：認識本專案 / 安裝啟動(四種方式) / `.env` 設定分區總覽 / CLI 指令完整參考(含 schedules add
 與 todo 子命令全參) / GUI 與 Web UI 分頁表 / 聊天 Agent 能力面 / Ingest·搜尋進階(hybrid/rerank/compact) / 技能編譯器(連 SKILL_GUIDE)
 / 背景任務 / 排程驗證 / webhook / Telegram 全功能 / 股市三閘 / ONVIF / netscan / 記憶 / Keep / Jev / 產圖 / hicloud / Teams·UIA·os-input
 / 本地核心工具十家 / MCP 總表與 Admin 編輯器 / 安全模型 / 疑難排解 / 附錄 A 環境變數索引、附錄 B 資料夾地圖。README 頂部加入手冊入口
連結。PDF 版一併輸出（`USER_MANUAL.pdf`）。
pyproject 0.9.109.1 → **0.9.109.2**（docs-only）。

### v0.9.109.3

**GUI 開機自動診斷 + 代起 Qdrant 伺服器** — live 事故 2026-10-05 15:20：
chat turn 中 `search_documents` / `list_documents` 全部
`WinError 10061`（拒絕連線）— 獨立 Qdrant 主機（`C:\qdrant`）在機器
重開機後沒有人啟動；LLM 的回答仍優雅降級完成，但 RAG 工具面無效。
修法：`gui/api.py::_prewarm_agent` 最前面新增
`Api._ensure_qdrant_server()` — `readyz` 探針
（3 秒 timeout；**僅限 loopback URL**，遠端 Qdrant 掛掉是維運問題不是
本程序能帶的）→ 不可達時以 DETACHED 方式 spawn
`QDRANT_QDRANT_START_BAT`（預設 `C:\qdrant\start-qdrant.bat`），輪詢
至多 30 秒；全程 warning 級 log、**永不 raise**（agent lazy 建構不動）、
`QDRANT_SPAWN_QDRANT=off` 可退出。LIVE E2E：Stop-Process 殺掉
qdrant.exe → 方法呼叫 → `server ready after ~2s` → readyz 200
（heal 來回 4.6s）。tests：+8 tests/test_qdrant_autoheal.py。
pyproject 0.9.109.2 → **0.9.109.3**。

### v0.9.109.4

**firefox_craw 中介 server（service_server.exe）同款自癒** — user：
「將 firefox_craw 的中介 server 也製作自代起機制」。三段式架構的中繼
樞紐（extension + chat MCP 兩端都依賴它）掛掉 = 所有 browser_* 工具
報錯，但 stdio `server.exe` 的 MCP 連線本身不受影響 — boot 時期無
人救它。修法：`gui/api.py` 新增 `Api._ensure_firefox_service()`
（v0.9.109.3 qdrant 自癒的鏡像）：探針
`GET http://127.0.0.1:<MCP_SERVICE_PORT|8766>/health` → 不可達時
DETACHED spawn `QDRANT_FIREFOX_SERVICE_START_BAT`（預設
`C:\Users\humphery\coding\mcp_getweb\run_service.bat`，runtime
log 在 exe 同層 `service_server_runtime.log`），輪詢至多 30 秒；
閘：`MCP_ACTIVE_SERVERS` 有設且不含 `firefox_craw` → 跳過（門面
已藏）、`QDRANT_SPAWN_FIREFOX_SERVICE=off` 退出；永不 raise。
LIVE E2E：Stop-Process 殺掉 → 方法 → `server ready after ~2s
(port 8766)` → /health 200（X-MCP-Service-Version=5.16）、heal
來回 4.1s；程序樹確認 onefile parent+child 雙 PID 為自然結構
（單一 listener，非殭屍）。tests：+10
tests/test_firefox_service_autoheal.py（healthy-noop/spawn+wait/
gate skip/full-surface heal/opt-out/missing bat/spawn fail/
timeout give-up/custom port+bat/prewarm wiring）。pyproject
0.9.109.3 → **0.9.109.4**。

### v0.9.110.0 — task/stage 層級 LLM 模型 pin（限 openrouter /
ollama 目錄）

**user：**「task 可以在 runtime 依任務的要求使用不同 LLM 模型（限
openrouter 和 ollama cloud 可提供的模型），沒有要求使用 default
的 llm 模型跑」。

* **新模組 `src/agent/task_llm.py`**（zero-side-effect，同
  provider_chain.py 紀律 — 直接讀 os.environ、import 時零 I/O）。
  格式單冒號 `provider:model`（split 限 1 — ollama 模型名自帶冒號
  如 `ollama:gpt-oss:120b-cloud`）；provider 白名單
  `ALLOWED_PROVIDERS=(openrouter, ollama)`。真實目錄探針：
  openrouter `GET /api/v1/models`（466 模型）＋ ollama
  `GET <OLLAMA_BASE_URL>/api/tags`（Bearer key；**/v1 後綴自動
  剝除** — live E2E 抓到 `https://ollama.com/v1/api/tags` 404）。
  TTL 快取 600 s（`clear_catalog_cache()` 重置）。
* **`validate_task_llm`**：目錄可達 → 已知模型 ok（`-cloud` 別名
  自動對應目錄 canonical id）、未知 → fail-fast 附最近候選
  （difflib+前綴；實測 `gpt-oss:120b-fake` → 建議
  `gpt-oss:120b`/`gpt-oss:20b`）；目錄不可達 → **ok+warning**
  優雅通行（真 404 由 provider 在 turn 時浮現）。
* **Agent pin 尊重 — fail-closed，兩個 live-E2E 修復**：①
  `Agent(mcp_manager=..., task_llm="provider:model")` — `__init__`
  新 `task_llm` 參數，`_pick_provider` 開頭 pinned 分支直接回
  (name, pinned_model, client)，不走 LLM_PROVIDER/鏈序/模型解析；
  ② `_provider_candidates()` pin 存在時回傳**單一 pinned
  triple** — 跨供應商 failover walk 對 pin 關閉（E2E 實證：pin
  撞 404 後原本偷偷跌到 ollama — 已堵死，fail-closed）；③ pin
  的 openrouter turn 送 **clean extra_body**（global routing 的
  allow_fallbacks=false 把 pin 模型自己的 endpoints 全濾掉 →
  404 — routing 是為 default 模型調的；pin 即 its own routing
  choice）。key 缺失 → 明確 ValueError。
* **task_runner**：`begin_task(source,title,sid?,llm?)` 登記
  `llm` 欄位（list_tasks / get_task_result 可見）。
* **task-local（4 工具）**：`run_task_once` schema + 可選 `llm`；
  handler 投放前 validate（拒絕 = **無任務被啟動** + hint 指引
  list_task_models；警告（目錄不可達）隨 ok 回傳）＋ pin 正規化
  為目錄 canonical id；背景 thread 用 pinned agent 建構。新
  **`list_task_models(provider?, contains?)`** 工具：兩 provider
  目錄快照（TTL 快取、每 provider cap 200、contains 過濾）—
  LLM 選模型的發現面。stdio `task_server.py` 鏡像同步（規格源
  host.list_tools 自動帶 + handler map/新方法）。
* **skill-local 平行能力**：`compile_plan` stage dict 可帶
  `llm` + plan 級 `default_llm`（compile 時即驗證，fail =
  PlanError）；`_run_stage` 解析 stage llm > default_llm > 預設
  鏈，factory 以 `llm=` 呼叫（legacy 零參 factory TypeError
  自動 fallback + warning）；registry 行 `source=plan` 也帶 llm。
* **system prompt**：Background tasks + Skill 兩區塊各加
  model-pin 指引（list_task_models → run_task_once(llm=…) /
  compile_plan(default_llm=…)）。
* **LIVE E2E（三部曲全綠）**：① `Agent(task_llm=
  'openrouter:z-ai/glm-4.6')` 真 turn answer='OK'(17.9s)、
  resolved model=z-ai/glm-4.6、無 404 無 failover；② spawn-site
  `run_task_once(llm='ollama:glm-5.3')` → task done、registry
  llm 記錄在案、session 尾 answer='OK'（真 ollama cloud
  turn）；③ `ollama:gpt-oss:120b-fake` 投放點拒絕 + 建議、無
  任務啟動。
* tests：**+24 tests/test_task_llm.py** + failover 相關回歸
  （llm_providers / provider_chain / agent_failover /
  mcp_active_servers / firefox_route / task_parallel_notify /
  skill_compiler）共 95-96 passed；plan-driver 對
  `load_plan→None` 加 `or rec` 韌性（測試收尾 race 實證的
  NoneType warning 清除，53 passed 0 warning）。
pyproject 0.9.109.4 → **0.9.110.0**；RESTART required（live
程式吃新 prompt/工具面）。
pyproject 0.9.109.4 → **0.9.110.0**；RESTART required（live
程式吃新 prompt/工具面）。

### v0.9.110.1 — remove_documents / web_fetch_batch /
stock_get_quotes 的 list 參數字串韌性

**live 事故 2026-10-05 16:48**（user log：5 連發
`remove_documents(filenames=...)` 全 miss）：LLM 把 `filenames`
傳成 **整串字串**（單一檔名 / 甚至 JSON 陣列字串
`'["20261005-142719-README.md",…]'`）→ handler 逐「字」比對
→ `missing: ["[", "\\", "2", "0",…]`，一個都刪不掉
（該輪 215s / 302k tokens）。修：host.py 新共享 helper
`_as_str_list(value, name)` — list/tuple/set ✓、None→[]、
字串 → JSON-array 剝殼 → 逗號分隔 → 單一項（quote/空白淨化）；
不可解形狀 → `None`（handler 回乾淨 usage error，不再瞎比對）。
三個 list 參數 handler 全部改走它：`remove_documents.filenames`
/ `web_fetch_batch.urls`（**移除 pre-wrap** — 舊碼把 JSON 字串
包成 [整串] 才 coerce → 1 個 bogus item，測試抓出）/
`stock_get_quotes.symbols`；remove_documents 描述同步。
tests：+15 tests/test_list_param_coercion.py（helper ×8 +
incident 三形態直打 handler + web_fetch_batch pre-wrap 回歸
×2 + int payload clean error）；mcp_client 回歸 43 passed。
LIVE：以事故同一 JSON-字串形態真刪 3 個舊版文檔（README 1652 +
USER_MANUAL 64 + SKILL_GUIDE 28 chunks）→ 24 → 21 docs、新批次
（16:45）完整保留。pyproject 0.9.110.0 → **0.9.110.1**;
RESTART required。


* **Live `.env`：`EMBEDDING_BACKEND` `auto`→`openrouter`** — 匯入緩慢問題的實測結論：瓶頸是本機
  llama.cpp（RDNA1 無 FP16 → 8B FP32 ≈ 644–1,037 ms/塊、GPU
  吞吐硬上限、slots 並行客戶端無增益（實測 1,012≈1,037）、
  重啟無助）；Qdrant upsert 僅 11 ms/pt 從不是瓶頸。切
  openrouter 後同型 A/B：600 → **399 ms/chunk（≈1.5–2×）**，
  同模型同 4096 維 **免重建集合**；注意離線時 hash 退化向量。
  * **⛔ 2026-10-05 17:35 重測推翻「GPU 硬上限」結論** — llama.cpp
    乾淨重啟後（無併發干擾、slots 空）重跑同型 bench：warm single
    491c = **47 ms**、batch 8×491 = **26 ms/塊**、16×863 = **32
    ms/塊**、32×491 = **32 ms/塊**（dim 4096 每發驗證；各形首發
    4.7–4.8 s = Vulkan 初始化一次性開銷；持續負載 3×64 段三輪同
    33–34 ms/塊）。比 openrouter（318–399）快 **~10×**、比事故日快
    **~20–30×** — 之前量到的 644–1,037 ms/塊應為「server 同時服務
    ingest/殘留測試负载」的污染值，而非 RDNA1 matmul 天花板。
    **本地 llama.cpp 現為最快最便宜的嵌入後端（隱私 + 零 API
    cost）**；要在雲端/本地之間切換只需改 `.env EMBEDDING_BACKEND`
    （`local` ⇄ `openrouter`）+ 重啟或 `reload`。


### v0.9.109.1 — plan 級總結通知接線 + 詳細使用說明

* `gui/api.py::_wire_task_notify` — skill_compiler 的**計畫級最終
  總結**（「✅ 計畫〔…〕完成 — 8 done / 0 failed（共 10 stages）」）
  現在沿用與 task-runner 相同的 Telegram client/chat 自動推送
  （`QDRANT_TASK_NOTIFY` gate；`kind in (progress, plan)` 分流）。
* **詳細使用說明** — 新 `SKILL_GUIDE.md`（機制決策表 / 話術觸發 /
  六工具規格逐步參數 / compile 注入規則全文 / 波次·併發·預算模型 /
  verify checks 全表 / 失敗·重試·恢復語意總表 / 通知矩陣 / 限制與
  FAQ / 診斷 checklist）。
* pyproject 0.9.109.0 → **0.9.109.1**。


### v0.9.109.0 — Skill/Workflow/戰術/策略 編譯器 MCP（skill-local，第 27 個 LocalHost)

**（user: 「使用MCP方式製作核心功能，針對技能、workflow、戰術 tact、
策略等，需多個回合分工運作，非單一 chat 回合可完成的工作進行優化
(compile) 再執行」）** — 新模組 `src/agent/skill_compiler.py`（plan
核心 + 執行器，零副作用）+ `SkillLocalHost`（server `skill-local`，
6 tools，in-process + stdio `src/mcp_server/skill_server.py` 雙面）：

* **compile_plan(name, stages, …)** — 把「多回合分工、非單一回合可
  完成」的工作（每 stage = 一個自足回合 prompt + 可選 depends_on/
  budget/verify）編譯成 wave-plan：
  1. 驗證：空 prompt/缺 title/未知依賴（id 或標題皆可）/自依賴/
     環偵測（Kahn）；duplicate stage 自動合併，帶額外 depends_on 的
     duplicate **fail-closed 拒絕**（合併會製造反向邊=循環）；
  2. 依賴 → 波次（wave = 1 + max(依賴 waves)）；超過並行上限的獨立
     stage 分批到後波（`QDRANT_SKILL_WAVE_CAP` 預設 4，0=不限）；
  3. 每 stage 自動注入：獨立回合聲明 + 預算標記（預設 =
     MAX_TOOL_ITERATIONS resolution）、立即 stat_file 驗證、
     verify_artifact 機械節點（帶 verify 的 stage）、checkpoint
     紀律、完成 send_telegram_message 回報（帶 notify_chat_id 時）；
  4. 持久 plans/<id>.json（atomic；durable record — task-registry
     重啟即清空教訓）。
* **run_plan(plan_id)** — 波次驅動 daemon：每 stage 獨立背景回合
  （fresh Agent 共用本程序 McpManager、wall-clock 硬超時
  `QDRANT_SKILL_STAGE_TIMEOUT_S` 預設 1800/0=off、自有 session
  plan-<id>-<stage>、task-registry source="plan" 可見 + 沿用
  v0.9.108 notify 鉤子做 stage 級 Telegram 通知）。RESUME 語意：
  孤兒 running 歸位、done 跳過、**skipped 且依賴已補齊的 stage 復活
  重跑**；done plan 再 run = 全 reset（技能可重跑）。on_failure=
  stop（預設）→ 失敗波即停、後波 skipped；continue → 失敗 stage 的
  下游 skipped、獨立 stage 照跑。
* **get_plan_status / list_plans / run_plan_stage（單一 failed/skipped
  stage 重跑）/ stop_plan** — 狀態面與增量重試。
* agent.py — ToolRoute 新 trigger（技能/workflow/戰術/策略/compile
  → skill-local）+ system prompt「Skill / Workflow compiler」區塊；
  MCP_ACTIVE_SERVERS += skill-local（live .env + .env.example）+
  QDRANT_SKILL_* 區塊。
* TESTS — +29 `tests/test_skill_compiler.py`（驗證矩陣×10、持久化×2、
  波次執行 E2E×7 含 verify pass/fail/stop policy/timeout/stop/notify、
  retry+resume 全迴路×2、host surface/E2E×4、stdio parity、
  原始碼守衛×3）；相關回歸 task_parallel_notify/runtime 33 passed。
* pyproject 0.9.108.0 → **0.9.109.0**；RESTART required。

### v0.9.108.0 — runtime limits 自查 + run_task_once 併發上限 + 任務完成/進度 Telegram 通知

**（user 五問後續：①LLM 能否取得 runtime 限制參數 ③turn_gate 未設 =
6 並行全開（05:41 事故）能否改進 ⑤能否自動建立監控閘定時通知進度）**

* **① `get_runtime_status` 擴充 `limits` 區塊** — resolved 值一次看齊：
  `max_tool_iterations` / `tool_call_watchdog_s` / `tool_result_cap_chars`
  / `max_concurrent_turns`（NULL = 未設 = 無上限）+ `turn_wait_seconds`
  / `task_max_parallel` / `tasks_running` + MCP_ACTIVE_SERVERS /
  壓縮與 offload / notify 設定的生效值；每欄 best-effort（一個壞
  resolver 不毀整份 status）。LLM 規劃批次/長工前先自查，不再瞎猜。
* **③ spawn 站併發上限** — `host.py` 新 `_task_max_parallel()` +
  `run_task_once` 前置檢查：`QDRANT_TASK_MAX_PARALLEL`（**預設 4**，
  0 = 不限 = 舊行為；invalid/負 → 預設）— source=task 的 running 數
  達上限時**在投放點直接拒絕**並附分批指引（不再等到 turn_gate 排隊
  後 TurnGateBusy 才失敗）。stdio task_server 走同一 handler → 自動生效。
* **⑤ 完成/進度通知閘** — `task_runner` 新 fail-closed notify hook
  （`set_notify_callback`；registry 本體仍零副作用）+ 進度監控 daemon
  （`start_monitor(interval_s)`；running>0 時每 N 秒推一次
  `⏳ 背景任務進度…含各 task 已執行秒數`）。`gui/api.py::_prewarm_agent`
  新 `_wire_task_notify()`：`QDRANT_TASK_NOTIFY=on`（fail-closed：需
  TELEGRAM_BOT_TOKEN + allowlist）→ 完成/失敗即時 Telegram 推送至
  `QDRANT_TASK_NOTIFY_CHAT_ID`（預設第 1 個 allowlisted chat）+
  `QDRANT_TASK_MONITOR_INTERVAL_S`（預設 300；0 = off）。
* 回答②③④相關 — provider cache 縮小的是「stable 前綴（tool schema
  + system prompt）」的重付；歷史與問題本體仍每 task 各付。④記憶體清空
  指「**程序**重啟」（GUI/web/Ctrl+C/crash/升級重啟），LLM chat session
  是持久 JSON 不受影響（兩者已寫進手冊 v1.1 §8）。
* TESTS — tests/test_task_parallel_notify.py（新增）; 相關回歸
  turn_gate/task_runner 套件 passed。pyproject 0.9.107.1 → **0.9.108.0**；
  RESTART required。

### v0.9.107.1 — telegram 回合級硬超時（第二次無聲掛死的最後一網）

**（log forensics 2026-10-04：12:22:52 run_python 回 OK 之後，log 完全
靜默 49 分鐘到使用者 Ctrl+C — 與 00:06 掛死同型）** v0.9.107.0 的
per-tool-call watchdog 覆盖了 MCP dispatch、LLM client 有自己的
timeout，但回合仍可能在其他未觀測點停擺。新增**最後一網**：

* **`telegram_bot` 新 `_chat_turn_guarded()`** — 整個 telegram
  `chat_turn` 跑在 wall-clock guard 下：`QDRANT_TURN_HARD_TIMEOUT_S`
  （**預設 1800s=30 分**；`0` = off 舊行為；invalid → 預設；
  其他非正整數 clamp 為 0 — 負的 watchdog 永不存在）。超時 →
  task-registry 記 `failed`、session 記錄 `turn_timed_out`、
  使用者收到誠實的「(處理超時) …」回覆；失控 thread 以 daemon 棄置。
  photo-hallucination 糾正 retry 同样受 guard（超時 → 退回原回覆）。
* `.env.example` 新 `QDRANT_TURN_HARD_TIMEOUT_S=1800` 區塊。
* TESTS — +12 `tests/test_turn_hard_timeout.py`（resolver 矩陣、
  guard 快/慢/例外/0-off、handle 掛死 E2E → 超時回覆、正常路徑不變）。
* pyproject 0.9.107.0 → **0.9.107.1**；RESTART required。

### v0.9.107.0 — telegram 卡關根因修補：offload 豁免 read_file + tool watchdog + 檔案走訪排除

**（user: 「修補」— 落地 2026-10-03/04 兩次 telegram 卡關事故的根因
修復）** 事故指紋（gui/app.log）：read_file ×62/×118 風暴 →
`max_iterations=50` 撞頂（17:30 排程輪、23:43/23:48 翻譯輪、05:41
六個並行 task 中兩個），以及 00:06 起單一工具呼叫**無聲掛死
~58 分鐘**（使用者 Ctrl+C 才解除）。三個根因修補：

* **① offload 豁免 read_file（洋蔥迴圈根因）** —
  `context_compress.py` 新 `_OFFLOAD_EXEMPT_TOOLS = {read_file,
  read_multiple}`：超大 `read_file` 結果**不再**寫 offload 檔（來源
  檔本來就在 read sandbox，「存到另一個檔再 read_file 讀回」是循環
  → 每讀一層多套一層 JSON-envelope 逃逸 → 工具預算燒光 = 卡關）。
  inline 結果仍受 `agent.py` 的 51,200-char 絕對上限保護；其他工具
  （web_search / run_python / kline…）的 offload 行為不變，且 offload
  檔內容保證為**原始結果逐字**（非再包一層 envelope）。
* **② per-tool-call watchdog（無聲掛死根因）** — `agent.py` 新
  `_dispatch_with_watchdog()` + `_tool_call_timeout_s()`：
  `QDRANT_TOOL_CALL_TIMEOUT_S`（**預設 0=off** 保持 byte-identical；
  live `.env` 設 **600**）。超時 → 回 `[tool watchdog]` tool error
  （loop 可反應、task-registry 必有 done row），失控 thread 以
  daemon 棄置；例外照舊向上傳播、`0` 走 legacy passthrough。
* **③ 檔案走訪排除（00:06 掛死的溫床）** — `host.py::_fs_walk` 新
  `_FS_SKIP_DIRS`（.cache / .git / node_modules / __pycache__ /
  AppData / site-packages / venv…）：目錄本身仍列出於清單，但
  **不再下鑽** — `search_files` / `get_tree` / `search_content` 不再
  一頭撞進 `.cache/huggingface` 這種風暴樹（read root =
  整個 user profile 時尤其致命）。
* **live `.env`** — `CHAT_OFFLOAD_THRESHOLD_CHARS` 6000 → **20000**
  （6k 對 read_file 是自殺門檻；kline 批次已有專屬 compact 機制
  v0.9.98.1，不需這麼低的門檻）+ 新增
  `QDRANT_TOOL_CALL_TIMEOUT_S=600`。
* 順帶修 — `tests/test_max_tool_iterations.py` 的 bail-out 測試補
  route-env 隔離（live `.env` 的 `QDRANT_ROUTE=on` greeting fast-path
  會攔截 "hi"，v0.9.104.6 test_response_language 同類問題）。
* TESTS — +16 `tests/test_v0107_stall_fixes.py`（豁免/非豁免
  offload、offload 檔逐字內容、watchdog 超時/0-off/例外傳遞/預算內
  不變、resolver 矩陣、`_fs_walk` 下鑽排除 + `search_files` E2E）；
  相關回歸 context_compress/tool_result_cap 47 passed、
  filesystem_local_host 161 passed、turn_gate/max_tool_iterations
  51 passed。pyproject 0.9.106.1 → **0.9.107.0**；RESTART required。

### v0.9.106.1 — app.log 保留一週（每日午夜輪替）

**（user: 「rotat app.log 保留一個星期内Log」）** — `gui/logger.py` 的
app.log 由純大小輪替（`RotatingFileHandler` 2 MB × 3 backups，無時間
概念：安靜一週時一週前的舊 log 仍佔名額、忙碌一天就可能把整週擠掉）
改為 **`TimedRotatingFileHandler`（每日午夜輪替、backupCount=7）** —
固定保留最近 7 天（`app.log.2026-10-04` 日期後綴命名），逾齡檔案由
handler 自動刪除。既有 `app.log.1/.2/.3` 為舊制殘留檔，不影響新機制，
可手動刪除。stderr handler、UTF-8、冪等 `setup_logging()` 語意不變。

* TESTS — +3 `tests/test_logger_rotation.py`（`when=MIDNIGHT` /
  `backupCount=7` / 每日 `%Y-%m-%d` 後綴斷言、size-rotator 絕跡、
  原始碼守衛）。
* pyproject 0.9.106.0 → **0.9.106.1**。
### v0.9.106.0 — 排程驗證三守則流程硬擋 + 機械式檢查獨立評估器 + 檢查點續跑

**（user: 「參考 project_improvement_plan_v2.md 改善本専案功能」— P0/P1/P2
全部以現有機制落地，不引入新框架）**

#### P0 — 驗證前置化：以「硬擋」取代「自律」

三守則（發送驗證 / 排程驗證 / 產出驗證）過去寫在 prompt 裡，LLM
可能跳過（計畫記載歷史上已發生 8 次假成功）。v0.9.106.0 把驗證變成
排程 fire 的**流程必經節點**：

* **`Schedule.verify_required`（新欄位，預設 `True`）** — 舊排程
  JSON 檔缺鍵時經 `from_dict` 載入為 True（`__dataclass_fields__`
  反射，向後相容）。
* **Fire prompt 注入 `VERIFICATION DIRECTIVE`**
  （`scheduler.py::_verification_directive`）— 結構化「步驟 N 執行 →
  步驟 N+1 強制驗證」：對每個產出檔案呼叫 `verify_artifact`、或以
  `get_chat_session` 回讀比對工具回執；驗證失敗 → 中止並回報
  「驗證失敗 + 原因」，**禁止**產出任何成功結論；驗證通過才允許寫
  報告，且需明確包含「驗證通過/驗證結果」與依據。directive 附帶
  schedule id 供 `get_chat_session` 定位 session。
* **事後機械守衛** — `_has_success_claim`（成功宣稱 regex；「已成功/
  已發送/已存檔/sent successfully…」）× `_has_verification_evidence`
  （驗證證據 regex；`verify_artifact`/驗證結果/回執比對/
  get_chat_session…）→「宣稱成功但零證據」判為未驗證，追加 **ONE**
  糾正補驗證回合（`_verification_guard_prompt`）；糾正失敗/空回覆 →
  退回原回覆但 `verified=False` 誠實記錄（**不美化**）。已 dispatch
  回執型工具（verify_artifact / get_chat_session / send_telegram_*
  — `_RECEIPT_TOOL_NAMES`）= 機器證據，不再重複糾正（避免與
  v0.9.67.1 Telegram 守衛疊加）。
* **三態 `verified` 旗標** 寫入 history.jsonl + session assistant
  message：`True`（已驗證 / 如實失敗）/ `False`（未驗證的成功宣稱）/
  鍵省略（該排程未啟用驗證 — 舊 row 形狀不變）。誠實失敗回覆與
  chat_turn 例外（degraded row）都記 `True` — 失敗路徑會如實回報，
  這正是計畫的驗收標準。
* **狀態面呈現** — `get_scheduled_task_status`（= ScheduleManager
  `get_progress`）新增 `verify_required` / `last_verified` /
  `unverified_recent`（近 5 次中未驗證成功宣稱數）；
  `list_scheduled_task_runs` 每 row 帶 `verified`。
* **退出機制** — 全域 `QDRANT_SCHEDULE_VERIFY=off`（.env.example 新
  區塊）；單一排程 `add_scheduled_task(..., verify_required=false)`
  （in-process + stdio add 均接）。

#### P1 — 獨立評估器：機械式檢查（⭐ 計畫優先項）

「驗證者不能是發現者」— 採用計畫三選一中的**機械式檢查**（零成本、
零幻覺）：

* **`verify_artifact(path, checks?)`** — 第 11 個 scheduler-local
  工具（in-process `SchedulerLocalHost` + stdio
  `scheduler_server` 雙面同引擎
  `src/agent/scheduler.py::verify_artifact`）：
  * 存在性 + `min_bytes`（「假 findings.json」空殼檔直接 FAIL）；
  * JSON 解析（任何帶 `field` 的 check 都觸發；上限 8 MB 檔 /
    512 KB payload）；
  * 每條 check：`field`（dotted path，支援 `a.b[0].c` 索引導航）、
    `type` ∈ `exists` | `nonempty` | `json_fields`（預設；可選
    `required` 子欄位清單）| `json_number`（`min`/`max`）|
    `json_array`（`min_length`）| `json_string`（`pattern` regex）|
    `file_exists`（JSON 內記錄的路徑再檢查）；
  * **未知 check type fail-closed**（先驗 type 再導航 — 打錯字
    永不靜默通過）；沙盒外路徑（write/read root 之外）拒絕；
  * 回傳 `{ok, path, resolved_path, size, json, passed, errors,
    check_count}` — `ok` 是排程驗證節點賴以判定的單一機器結論，
    永不 raise。
* 工具描述明示「MANDATORY in the schedule verification flow: 宣稱
  成功前必須呼叫；失敗時回報驗證失敗 + 原因」。

#### P2 — 檢查點與狀態延續（失敗只重跑失敗分支）

* **系統提示「Long-running job CHECKPOINTS」區塊**
  （`Agent._system_prompt(tools_enabled=True)`）：多階段任務（股市
  六階段等）每階段完成**立即**把產出寫進 write sandbox 的
  checkpoint 檔（如 `findings.json`，每階段一個 `completed` 標記，
  用 `verify_artifact` 的 `exists`/`json_fields` 檢查確認寫入）；
  **續跑（重試輪 / 重 fire / 使用者「繼續」）時先讀 checkpoint 檔，
  已完成階段跳過、只補失敗/缺少的階段**；todo 任務樹的
  `add_task_dependency` 順序與階段順序對齊（依賴圖即計畫）。
* 依計畫判斷不引入 LangGraph — 排程 + 任務樹 + 檢查點檔已覆蓋。

* TESTS — +42 `tests/test_schedule_verification.py`（directive
  注入/退出、守衛糾正/退回/誠實旗標、三態旗標、status 呈現、
  verify_artifact 全矩陣含假 findings.json 判 FAIL 的計畫驗收、
  host/stdio parity、P2 提示規則、既有 fire 語意回歸）；
  `test_scheduler_local_host.py`/`test_scheduler_server.py` 表面
  10 → 11 更新 + run-now 合約鎖定新 prompt 形狀；相關回歸
  scheduler 全家 9 檔 191 passed、system-prompt 6 檔 + coercion
  85 passed、meta/active-servers/toolroute 41 passed。
* `.env.example` — `QDRANT_SCHEDULE_VERIFY` 區塊。
* pyproject 0.9.105.1 → **0.9.106.0**；RESTART required。

### v0.9.104.6

**使用者可匯入 system rules（user: 「提供用戶可以匯入 system
rule」）** — 新模組 `src/agent/system_rules.py`：使用者把自訂規則寫進
一個檔案，Agent 每次 `_system_prompt` 建構時自動附加成
`USER SYSTEM RULES` 區塊（tools 與 legacy 兩種 prompt 都帶）。

* **匯入方式（二擇一）**：
  1. **預設路徑** — 把 `system_rules.md` 丟進 filesystem **read
     sandbox**（`QDRANT_FILESYSTEM_READ_ROOT`；存在才載入）。用
     filesystem MCP / 上傳 / 任何編輯器寫入即可，**改檔即生效、免
     重啟**（mtime 快取）。
  2. **`.env` 指定路徑** — `QDRANT_SYSTEM_RULES_FILE=C:\…\my.md`
     （支援 `~` 展開；優先於預設路徑）。
* **保護** — `QDRANT_SYSTEM_RULES_MAX_CHARS`（預設 8000）超過截斷並
  附註記；永不 raise（缺檔/是目錄/解碼失敗 → 空字串，prompt 與未
  設定時 byte-identical）；區塊標明「與工具/安全規則衝突時後者優先」。
* TESTS — +13 `tests/test_system_rules.py`（預設路徑 / env 覆寫 /
  `~` 展開 / mtime 熱更新 / cap 截斷 / 目錄不炸 / prompt 兩種
  flavour / LANGUAGE DIRECTIVE 共存 / 安全規則在前）。
* 順帶修 — `test_response_language.py` 的 mock-client 測試被 live
  `.env` 的 `QDRANT_ROUTE=on` greeting fast-path 攔截（call_args
  為 None / chat_turn 的 MagicMock answer 撞
  `_append_memory_citation`）→ setup/teardown 隔離 route env +
  mock 回真字串。
* `.env.example` 新區塊；pyproject 0.9.104.5 → 0.9.104.6。

### v0.9.104.5

**describe_image 準確度稽核 + token 浪費修復（user: 「tool
describe_image 的準確度是否有問題，導致寄信作業花了大量的
token」）** — live log 2026-10-02 13:33 寄信輪 tokens=1,223,087
（39 dispatches / 77s）。診斷結論：**vision 描述本身是準確的**
（ollama-cloud gemma4:31b-cloud 如實回報「收件匣 + 沉浸式翻譯 AI
彈窗」——正是 LLM 無法用畫面驗證的原因）；token 爆量是三個**機制**
問題，全部修復：

* **① `raw_image_base64` 純浪費 58k tokens/次** — `_describe_image`
  每次回傳 231,424 chars 的完整截圖 base64，但 text-only tool loop
  從不消耗它（v0.9.16.2 的 `_strip_base64_from_tool_text` 每輪又剝
  掉）→ 三次 describe ≈ 175k tokens 白燒。修 = **host 不再回傳該
  欄位**（回 `note` 說明省略理由；`resize_image` 維持原樣 — resize
  後的 bytes 是真正的下游資料）。工具描述同步更新（in-process +
  stdio server.py）。
* **② SHA-1 cache 永不命中（`from_cache: False` 實證）** — cache 是
  `ImageDescriber.__init__` 的實例 dict，而 host 每次呼叫都
  `ImageDescriber()` 重建 → 同一張截圖三次 describe = 三次完整
  vision API。修 = **cache 升為 process 層級**（module-level
  `_SHARED_CACHE` + `threading.Lock`，`_cache_get/_cache_put/
  cache_clear/cache_info` 委任；實例 dict 保留為 per-instance 視圖，
  key 已含 prompt 故 OCR/描述不互撞）。新增 `last_cache_hit` 旗標 +
  host `from_cache` 偵測改用它（strict `is True`，MagicMock 假件
  無法偽造；舊 before/after diff 保留為 fallback）。
* **③ 截圖驗證紀律缺失** — 同一 compose 畫面被截了兩次
  （`screenshot-…400/409` 兩檔 MD5 相同）+ 兩次 `stat_file`（同一
  173,566 bytes）+ 三次 describe。修 = system prompt 新增「截圖驗證
  紀律」區塊：無 output_path 每次覆寫同一檔、比對前後必須帶不同
  output_path、同一張不要 describe 兩次（第二次是快取命中結果相同）、
  畫面狀態驗證以 DOM `browser_snapshot` 為事實來源（畫面只補
  snapshot 看不到的視覺狀態）。
* SURFACE 註記 — 工具 schema 實測 28,399 chars ≈ 7.1k tokens
  （index 模式已生效，非本輪主因）。四次 create 的重覆計費是
  Together 快取在工具訊息重寫時的正常行為，修 ①③ 後每輪 prompt
  直接縮水。
* TESTS — `test_describe_image.py` +`TestSharedCacheV0104_5`（跨實例
  快取共享 ×2 calls、host 結果 slim + 第二次 from_cache=true）；
  `test_image_describer_fallback.py` / `test_provider_chain_order.py`
  加 autouse shared-cache 清空 fixture；locked-in 斷言反轉
  （`raw_image_base64` 不得存在 + `note` 在）。相關 144+ passed。
* pyproject 0.9.104.4 → 0.9.104.5；RESTART required。

### v0.9.104.4

**exec-local 升格為基礎 MCP（user: 「將 MCP Local exec 設定成基礎
MCP」）** — `agent.py::_TOOLROUTE_CORE_SERVERS` += `"exec-local"`：
`run_python` / `run_bash` / `run_shell` / `run_grep` / `run_awk`
（+ script-library `script_save`/`script_list`/`script_run`）現在
**每一輪都在 LLM 工具面上**，不再需要 exec class 命中 Jev 路由
（同 v0.9.102.2 scheduler-local 的 core 化模式）；exec class 的
`(exec-local, archive-local)` 映射與 union-only 語意不變（archive
仍在 exec/archive class 才掛）。`MCP_ACTIVE_SERVERS` gate 早已含
`exec-local`（v0.9.102.1 回補），本次只改 ToolRoute core。
+1 test（`test_toolroute.py::test_exec_local_is_base_core`：stock/
rag/None 三種 class 皆含 exec-local + core tuple 本體）；相關 50
passed。pyproject 0.9.104.3 → 0.9.104.4；RESTART required。

### v0.9.104.3

**TOOL-RESULT 爆炸防呆 — Gmail 全頁 HTML 事故修復**（live log
2026-10-01 17:17：firefox_craw `get_page_html(tab_id)` 回 Gmail 整頁
HTML（~6MB）→ 之後每次 create 全軍覆沒 — OpenRouter 404
「context filter removed …」（attempt 1.2M tokens）、ollama 400
「prompt too long: 1,751,656」、openai-compat timeout → 寫信任務 157
秒後降級失敗、信沒寄出）:

* **根源 1 — offload 整組靜默失效**：live `.env` 自 v0.9.82.5 就寫
  `CHAT_CONTEXT_COMPRESSION=on`，但 `_MODES=(off|auto|aggressive)`
  不認 `"on"` → fallback 成 silent off → fat 工具結果**從未被
  offload**（`chat_offloads/` 0 檔案佐證）。
  * 修 — `context_compress._cfg()` 認 **`on`/`yes`/`true`/`1` =
    auto 別名**；off 與垃圾值維持 off。
* **根源 2 — offload 是 best-effort**（寫檔失敗 → inline 不警告）：
  單一工具結果必須有一道與壓縮器無關的絕對硬閘。
  * 修 — `agent.py` 新增 `_TOOL_RESULT_CAP_DEFAULT=51200` +
    `_tool_result_cap()`（`QDRANT_TOOL_RESULT_MAX_CHARS`；**0 = 不限**、
    unset/invalid/負 → default）；`_run_tool_call` 對**每個**工具結果
    （成功與錯誤皆然）執行：超過 → 截斷 + `…[tool result truncated: N
    chars over the M-char safety cap]…` 指引文字（重下更窄參數或
    read_file 已存副本）。51200 ≈ 13k tokens。
* `.env.example` — 兩項記錄（cap + on 別名說明）。
* TESTS — +12 `tests/test_tool_result_cap.py`（resolver
  default/override/bogus/負/0=不限、oversized 截斷＋notice、小結果
  不動、env cap、0=不限、error 亦 cap 但小 error 原樣、**on 別名 ×5
  值**、on → offload 真的寫進 read sandbox（事故同形狀）、off 模式
  cap 仍然擋）＋ 相關套件合跑通過。
* pyproject 0.9.104.2 → 0.9.104.3.

### v0.9.104.2

**FIREFOX_CRAW MCP 上線**（user: 「我設定了 mcp tool firefox_craw 但
不能用」）:

* 診斷 — `config/mcp.json` 的 `firefox_craw`（stdio，
  `mcp_getweb/dist/server.exe`）**連線是好的**（log 兩次
  `[mcp:stdio:firefox_craw] connected` + `google_search` 撞名自動改名
  `firefox_craw__google_search`）；真正攔住它的兩層：(1)
  **`MCP_ACTIVE_SERVERS` 沒列它** → 工具面全被隱藏（v0.9.41.1 教訓；
  使用者自己加過但重啟的舊進程沒吃到）；(2) **ToolRoute（on）沒有
  任何 class/hint 會掛它** — 就算解閘，一般回合也被裁掉。
* 修法 — `.env` MCP_ACTIVE_SERVERS += `firefox_craw`（去重後單一條）；
  `agent.py`：`web-external` class 改為 `(my_web, firefox_craw)`、
  新增 `_toolroute_browse_hint(question)`（明示瀏覽器意圖才 union:
  firefox/firefox_craw/get_page_html/get_x_home/get_x_post/
  download_x_post/mcp_getweb/瀏覽器/x.com/twitter.com/crawl/browse）
  + `_TOOLROUTE_SERVER_TRIGGERS` 加入 firefox 列（上輪回覆 mining）+
  system prompt 新增「瀏覽器網頁」區塊（web-local 的
  web_search/web_fetch 先行；x.com / needs-JS / web_fetch 擷不到時改
  `get_page_html(url)`；讀頁免 confirm、save_as_markdown /
  convert_document 存檔屬寫入）。
* TESTS — +9 `tests/test_firefox_route.py`（browse hint 正/負例 —
  一般搜尋與產圖/時間類**不**誤 union、web-external 雙成員、
  rag+問句即 union、prev-reply mining、agent/.env/mcp.json 原始碼
  守衛）；toolroute 相關 77 passed 合跑。
* LIVE E2E（重啟後）— question「看一下 x.com/elonmusk 的最新 po
  文內容」→ `class=web-external p=0.98`、servers 含 `firefox_craw`
  → **dispatch `get_page_html` → OK**（X 防 JS 擋頁 → 模型自行 fallback
  my_web 搜尋，最終回報真實貼文）；dispatches=8。
  順帶清掉重覆開機造成的雙進程 listen（:8334 兩個 PID）。
* pyproject 0.9.104.1 → 0.9.104.2.

### v0.9.104.1

**TOOLROUTE CONTEXT — 上一輪 LLM 回覆加入路由判斷**（user: 「jev mcp
tool gate 目前只看當下 prompt 內容進行判斷會造成有些如 ok、執行、繼續、
run、核準的回覆會造成後續 llm 執行 tool call 會因為 JEV MCP GATE 沒有
加入應對的 MCP TOOL 而失敗，建議 JEV 可以加入上一個 LLM 回覆內容進行
判斷」）:

* 問題 — 接續型回覆（ok / 執行 / 繼續 / run / 核准）沒有類別關鍵字，
  Jev 分類只看當下 prompt → 工具面失去上輪宣告的 fleet → 工具呼叫
  失敗（或模型只能假呼叫 — v0.9.13.2 發現陷阱的多輪版）。
* `src/agent/agent.py` 三件套：
  * `_prev_assistant_reply(history)` — 最後一則 assistant 回覆 =
    接續動作的依據（空/error 訊息跳過、cap 700 字、永不 raise）。
  * `_TOOLROUTE_SERVER_TRIGGERS` + `_toolroute_prev_reply_servers`
    — mining 上輪回覆裡「宣告的工具名前綴
    （send_telegram / stock_ / keep_ / cred_ / archive_ / run_python /
    onvif_ / hicloud_ / …）＋ CJK 動詞（下單/報價/K線/攝影機/行事曆/
    壓縮/背景執行…）」→ 對應 server；**union-only（只加不減）**、
    垃圾輸入 → []。
  * `_toolroute_servers_for(cls, question, prev_reply=None)` — union
    prev mining；`_jev_toolroute(question, prev_reply=None)` — 短題
    （≤64 字）＋上輪回覆（cap 500、標註「僅供語境判斷」）合成 Jev
    判斷文本（cap 1200；長題自足、不帶 prev）。
* 接線 — `chat_turn` impl（history 在手）抽取 prev 並 thread 給
  Jev 分類與 union（兩者）；`ask`（單輪、無 history）維持 2-arg。
* TESTS — +12 `tests/test_toolroute_prev_reply.py`（extract
  skip/caps/never-raise、mining 矩陣、garbage、union-only 不縮面、
  ok-continuation → stock+telegram、無 prev 行為不變、Jev ctx 合成、
  長題不帶 prev、call-site 守衛）＋ test_toolroute /
  test_imagegen 的呼叫點守衛更新（2-arg ==1 + prev_reply×2）。
* LIVE E2E（web 重啟後）— question「ok」+ history（助手宣告
  stock_get_quote）→ `class=stock p=0.97`、servers 掛上
  `stock-local` → **真的 dispatch stock_get_quote（live 非模擬，
  2,495/2,500）**、dispatches=1、正常回答。修復前同場景（10:07 舊碼）
  = class=rag、無 stock-local、dispatches=0、answer=0。
* pyproject 0.9.104.0 → 0.9.104.1.

### v0.9.104.0

**IMAGEGEN 產圖 MCP 擴充 — OpenRouter dedicated Image API**（user:
「使用這個製作 產圖 的 MCP擴充功能
https://openrouter.ai/docs/guides/overview/multimodal/image-generation」）:

Live 探針（scripts/probe_imagegen.py，2026-09-30）鎖定 API 契約：
`POST https://openrouter.ai/api/v1/images/generations`
（`{model, prompt, n, aspect_ratio, resolution, size, quality,
output_format, seed}`）→
`{created, data:[{b64_json, media_type}], usage{cost}}`；recraft-flash
~1.5-4 s/張、cost $0.007（usage.cost 全部成功才計費，失敗不計費）。

* **新引擎** `src/imagegen_tools.py::ImageGenEngine`（hicloud/keep 收斂
  模式）— `IMAGEGEN_MODE=mock`（預設）= Pillow 確定性 placeholder
  （NO network，simulated: true）；live 重用 OPENROUTER_API_KEY。參數
  正規化（n 上限 `IMAGEGEN_MAX_IMAGES` 4（1-10）、aspect_ratio N:N、
  resolution 512|1K|2K|4K、quality auto|low|medium|high、output_format
  png|jpeg|webp|svg、seed）+ mock 產圖 + live HTTP（urllib，`X-OpenRouter-Title`
  識別）+ 模型目錄 `GET /api/v1/images/models`（mock 回常備清單）。
  **兩道寫入閘**（hicloud 同款）— confirm=true（mock 亦強制）+ live
  另需 `IMAGEGEN_WRITES_ENABLED=on`；HTTP/JSON 失敗一律回 error 信封
  絕不 raise；引擎回傳**解碼後 bytes**（絕不把 base64 blob 回流
  LLM context — v0.9.16.2 教訓）。
* **新 LocalHost** `ImageGenLocalHost`（server `imagegen-local`，4 工具）：
  `image_generate`（prompt 必填 + confirm；檔名 sanitize + 以實際
  media_type 強制副檔名（webp/svg 對應 .webp/.svg）+ 撞名 -2/-3 版本化 +
  atomic tmp+os.replace 寫進 write sandbox `images/`（IMAGEGEN_DIR 可
  改）+ 回傳 `display: 📷 圖片已生成：<path>` 一行）、`image_gen_list` /
  `image_gen_models` / `image_gen_status`（唯讀，不需 confirm）。
* **接線** — `McpManager.from_config` registers before Meta
  （保留 `_imagegen_local_host` ref + seed 失敗進 `failed`）；stdio
  mirror `src/mcp_server/image_gen_server.py`（`python -m
  src.mcp_server.image_gen_server`，Server "divine-sense-agent-imagegen"
  4 工具同面）。
* **Prompt + ToolRoute** — system prompt 產圖觸發區塊（「產圖」「生成
  圖片」「畫一張…」「海報/logo」＋ live/mock 防捏造條款 + path 原樣
  寫進訊息以取得 chat 內嵌預覽；legacy prompt 不含）；toolroute 增
  `image` class（→ imagegen-local）+ `_toolroute_imagegen_hint`
  （v0.9.103.2 telegram-union 同款：問句含產圖意圖即 union
  imagegen-local，不論 Jev class；「圖表/流程圖/drawio」明確排除）。
* **Config** — `src/config.py` 記錄 6 env（IMAGEGEN_MODE / MODEL /
  WRITES_ENABLED / MAX_IMAGES / TIMEOUT_S（預設 120s，30-300） /
  DIR）；`.env.example` + live `.env`（`MCP_ACTIVE_SERVERS +=
  imagegen-local`；IMAGEGEN_MODE 維持註解 = mock 零費用，解開 3 行
  即可啟 live，全部可逆）。
* LIVE E2E — 引擎直呼（live，tmp sandbox，confirm=true）真實產出
  `e2e-test.webp`（624KB，usage_cost $0.007，4.0 s）— 副檔名依
  media_type 強制、path/display/usage_cost 全鏈驗證。
* TESTS — +40 `tests/test_imagegen_host.py`（mode/上限/dir 解析器、
  mock placeholder + confirm 閘 + n/參數驗證/檔名清理、live 兩道閘 +
  injected urlopen 回應解析（webp ext/cost/created）+ HTTP/網路錯誤信封
  + 模型目錄、host e2e（sandbox 寫檔/撞名 -2/唯讀三件）、
  from_config wiring、stdio parity、system prompt block、toolroute
  hint/class union）。相關回歸：jev+keep 64、toolroute+meta 25、
  mcp_active_servers 15（77.9s — 真實 Agent 建構，本就慢）、
  mcp_client 28 passed。pyproject 0.9.103.5 → 0.9.104.0。

### v0.9.103.5

**SESSION TITLE RENAME — Sessions tab ✏️ per-row update** (user: 「提供
session 更新 標題 功能」):

* `src/agent/session.py` — `SessionManager.rename(sid, title)` — a
  METADATA-ONLY edit: messages / `created_at` / `updated_at` untouched
  (renaming must NOT re-order the newest-first session list) and the
  long-term-memory ingest skipped. Underneath it `SessionManager.save`
  gained two keyword-only flags `ingest=True` / `touch=True` (defaults
  = the previous byte-identical behaviour for every existing caller).
  Missing session / empty title → `None`.
* `gui/api.py` — `update_session_title({"id": ..., "title": ...})` —
  the single-dict payload convention (v0.9.96.3); `sid` /
  `session_id` / `new_title` key aliases and a `(sid, title)` 2-item
  sequence tolerated; clean errors for no-id / empty title /
  not-found; never raises.
* `gui/assets/app.js` — every Sessions row gains a `✏️ Rename` button
  (between Load and 刪除). `prompt()` prefilled with the current title
  (the title travels through `dataset.curTitle` — property
  assignment, no HTML escaping needed for CJK); cancel = no-op, empty
  refused. On success: the row list refreshes, the chat session
  picker label refreshes, and renaming the ACTIVE session updates
  `chatSession.title` in lockstep (`syncStateMirror()` — the next
  save keeps the new title). i18n ×4 (sessRename / sessRenamePrompt /
  sessRenameEmpty / sessRenamed).
* `cli.py` — `python cli.py sessions rename <id> <new title…>`
  sub-command (`nargs="+"` joins words with spaces; empty title
  refused; the `sessions list` usage block mentions it).
* TESTS — +23 `tests/test_session_rename.py` (rename semantics / list
  order untouched / memory-ingest spy skip / payload parser matrix /
  empty+not-found refusals / HTTP round-trip incl. not-found envelope
  / frontend + CLI source guards). Related suites: session+multi 43
  passed; memory+history+scheduler+telegram 146 passed; chat
  autosave+time bar+web server 74 passed. pyproject 0.9.103.4 →
  0.9.103.5.

### v0.9.103.4

**BRAND LOGO — 🧠 → THE DIVINE SENSE DOG** (user: 「用上面的icon換掉
chat ui/web 的這個 icon 🧠」):

* `gui/assets/index.html` — the topbar brand chip's
  `<span class="logo">🧠</span>` becomes
  `<span class="logo"><img class="logo-img" src="icon.svg" alt="" /></span>`.
* `gui/assets/style.css` — `.brand .logo .logo-img` (24×24 block,
  `border-radius: 50%` clip + a hairline `box-shadow: 0 0 0 1px
  var(--border)` so the light-disc dog art stays visible on the dark
  topbar).
* The 🧠 emoji is now gone from the entire document; the splash title
  (text-only) and everything else untouched.
* TESTS — `tests/test_icon_branding.py` +1
  `test_logo_img_replaces_emoji` (logo img wired / 🧠 banned from the
  whole document / css badge rule; spec icon guard kept as its own
  test). icon+web_server suites 61 passed. pyproject
  0.9.103.3 → 0.9.103.4.

### v0.9.103.3

**PROJECT ICON — THE DIVINE SENSE DOG** (user: 「參考
C:\Users\humphery\dog_icon_bw.svg 制作 icon 取代 本專案 icon」):

* The SVG (viewBox 0 0 512 — circle bg + 垂耳/頭部/白斑/口鼻/半閉眼/
  鼻子/吐舌, all circle/ellipse/M-Q-Z paths + strokes) is rasterized
  with a 4x supersampled pure-PIL renderer written for the build (the
  box has no cairo/magick/playwright — PIL 12 flattens the simple
  shape set exactly), then committed under `gui/assets/`:
  - `icon.svg` — the canonical vector copy (web + modern browsers);
  - `icon.ico` — multi-size 16/32/48/64/128/256 (PIL ICO frames);
  - `icon-256.png` — preview/repo art.
  web_server's `_MIME` already maps .svg/.ico and the static handler
  serves them (verified by live round-trip).

* WIRED:
  - `gui/assets/index.html` — favicon links
    `<link rel="icon" type="image/svg+xml" href="icon.svg" />` +
    `<link rel="icon" type="image/x-icon" href="icon.ico" sizes="any" />`
    (web browsers + pywebview WebView2 tab);
  - `gui.spec` — the exe icon placeholder is now real:
    `icon=str(PROJECT_ROOT / 'gui' / 'assets' / 'icon.ico')`
    (the old `PROJECT_ROOT / 'assets'` path was wrong anyway).

* TESTS — +7 `tests/test_icon_branding.py` (ico frames + loadable,
  frame render sizes, svg content, png 512 RGBA, index favicon
  links, spec icon wired + old wrong-path placeholder gone, live HTTP
  round-trips for /icon.ico + /icon.svg). icon+web_server suites
  60 passed. pyproject 0.9.103.2 → 0.9.103.3.

### v0.9.103.2

**TELEGRAM-SCHEDULE SEND CHAIN — TOOLROUTE UNION + FUTURE-TENSE CLAIM
GUARD** (user: 「今日 14:00 的排程有啟動 但確認有發送到 telegram
是沒有異常就不發送了嗎」; forensics — schedule 9e924b90ac9c 的 prompt
是「無條件發送分析結果到 chat_id <YOUR_TELEGRAM_CHAT_ID>」，但 BOTH today's fires
(14:00 + 14:06) dispatched ZERO send_telegram_*：回答只有 36/42 字的
「整理分析後發送 Telegram / 現在整理分析並發送 Telegram」宣告)：

* **A. ToolRoute telegram union** (`src/agent/agent.py`) — 新
  `_toolroute_telegram_hint(question)`（send_telegram / telegram /
  chat_id 數字 / 發送…訊息（分析結果）/ 傳送…訊息（通知）（畫面)，
  any-hit true）；`_toolroute_servers_for(cls, question=None)` 在 hint
  成立時把 `telegram` server 併入該回合工具面 — **無論 Jev 選到哪個
  class**（live 14:00 被 class=stock p=0.93 搶走 → 介面無 telegram →
  模型只有散文可寫 = v0.9.13.2 發現陷阱）。兩個呼叫點
  (`_chat_turn_impl` + `_ask_impl`) 都改傳 `question`。

* **B. Claim regex widening + negative list** (`src/agent/scheduler.py`)
  — `_CLAIM_RE` 增補未來式/宣告句型：現在發送 / 即將發送 / 準備發送 /
  待會發送 / 稍後發送 / 馬上發送 / （之後|接著|然後）後發送 / 發送後 /
  發送-telegram 鄰接 / (send|sending|sent)-telegram；新增
  `_CLAIM_NEG_RE`（不發送 / 無需發送 / 不需要發送 / 不送出 / 不用
  發送 / 不會發送 / 未發送）— 誠實的「不發送」回答保持不觸發。
  對比組：13:31 積極型追蹤排程走「已發送」句型 → 舊守衛正常觸發 →
  corrective `send_telegram_message` 真發送 OK（13:32 dispatched=True）。

* TESTS — `tests/test_toolroute.py` +5（hint 偵測器 / stock class 併入
  telegram 但 class 標籤不變 / None class 併入 / 無 hint 不變 / 兩個
  呼叫點傳參 source guard）；`tests/test_scheduler_tg_guard.py` +3
  （三則 live 未來式回答現在全部命中 / 完成態仍命中 + 無 telegram
  負例 / 明確「不發送」負例）。相關套件 88 + 39 passed（toolroute/
  tg_guard/scheduler/route_v2/boot_resume/sessions_multi），
  `node --check` N/A（純後端）。pyproject 0.9.103.1 → 0.9.103.2。

### v0.9.103.1

**SESSIONS-TAB MULTI-SELECT (batch delete + export)** (user: 「對話記錄
管理 支援 多選」+「批次刪除 + 批次匯出 JSON 檔」):

* BACKEND (`gui/api.py`) — TWO new public bridges, both on the
  single-dict payload convention (v0.9.96.3 lesson; bare list /
  comma-string tolerated; order kept, duplicates collapsed, non-str
  items stringified, 1000-cap):
  - `delete_sessions({"ids": [...]})` — per-id containment (ok /
    not_found / error per id), ONE bad id never aborts the batch,
    never raises, returns `{ok, deleted, not_found, results}`;
  - `export_sessions({"ids": [...]})` — loads each session via
    SessionManager and writes ONE combined JSON
    `{"exported_at", count, skipped, sessions:[{id,title,created_at,
    updated_at,messages}]}` ATOMICALLY (tempfile + os.replace,
    `newline=""`) under the write sandbox `uploads/
    sessions-export-<ts>.json`; empty / all-missing are error
    envelopes; returns `{ok, path, name, count, skipped, size}`;
    the frontend hands `path` to the pre-existing
    `downloadSandboxFile()` (web = GET /download browser download;
    pywebview GUI = native FOLDER dialog + save_sandbox_file_as).

* FRONTEND — `index.html` panel-sessions gains a `.sess-toolbar`:
  ☑ `全選` (tri-state `#sess-sel-all`: checked / unchecked /
  indeterminate, bound ONCE at boot from bindEvents —
  `addEventListener` would stack per refresh) + `🗑 刪除所選（N）` +
  `⬇ 匯出所選（N）` (both disabled at N=0, labels live-updated via
  `t('sessDeleteSel'/'sessExportSel', {n})`); every table row gains a
  leading `.sess-sel` checkbox column (`colspan` error row 5→6); the
  per-row Load / 刪除 buttons are UNTOUCHED. Deleting the ACTIVE
  chat session drops the in-editor state (`chatSession.id = null`) so
  the next autoSave cannot resurrect a deleted id. Export reuses the
  existing web-mode GET /download + GUI save-dialog plumbing.
  i18n ×4 (sessSelAll / sessDeleteSel / sessExportSel / sessNone /
  sessDeleted / sessExported); css `.sess-toolbar` + `.sel-col` +
  disabled opacity.

* TESTS — +20 `tests/test_sessions_multi.py` (dict/list/comma
  payloads, miss ids non-fatal, dedup + non-str, positional bridge
  shape, combined-JSON content + timestamp name + byte size,
  skipped ghost ids, empty / all-missing refusals, live HTTP
  round-trips for BOTH bridges, frontend source guards ×7); chat /
  download / notes / web_server suites **126 passed**, node --check
  green. pyproject 0.9.103.0 → 0.9.103.1.

### v0.9.103.0

**TOOLS-PEEK SCROLL ANCHOR** (user: 「點了chip開了特定對話框的工具
明細，但網頁會被接到最下面，無法維持在關閉工具明細的那個對話框」):
the 🧰 chip toggle re-ran `renderChat()` whose tail sticks the scroll
to the BOTTOM — the user was yanked away from the bubble they were
reading on every toggle.

* FIX (`gui/assets/app.js`):
  - every `.chat-msg-row` now carries `row.dataset.midx = mIdx`
    (stable DOM anchor across the full rebuild);
  - `renderChat(peekIdx, peekDelta)` — when the click handler passes
    the toggled bubble's index + the viewport offset measured BEFORE
    the rebuild (`row.getBoundingClientRect().top −
    log.getBoundingClientRect().top`), the tail re-anchors the scroll
    via `scrollTop += (cur − prev)` (getBoundingClientRect math is
    offsetParent-proof) so the toggled bubble stays pixel-stable;
    every OTHER render path keeps the stick-to-bottom jump
    (new message / load / clear untouched);
  - chip handler now passes `renderChat(mIdx, deltaTop)`.
* No backend change. Guards: +6 source locks in the always-run
  `tests/test_chat_tools_used.py` (dataset.midx / signature / chip
  pass-through / anchor selector / rect math). Chat+snap suites 61
  passed. pyproject 0.9.102.9 → 0.9.103.0.

### v0.9.102.9

**CHAT TOKEN-HEADER RESTORED** (user: 「每個對話框上的 token
資訊不見了」): ROOT CAUSE — the v0.9.102.6 appendChatMsg patch REPLACED
the v0.9.12.0 usage-storage line `if (usage) msg.usage = usage;` with
the toolsUsed storage instead of adding alongside — every NEW assistant
bubble carried NO `msg.usage` → `_chatTokensText()` returned null →
the `↑ N / ↓ N tok · ⚡ N cached` header segment vanished (old saved
bubbles kept theirs; only fresh turns lost it). The regression slipped
through because the only guard locking that line lives in
`tests/test_chat_time_bar.py` whose MODULE-LEVEL jsdom skipif skips
the WHOLE file (pure text guards included) when .cache/jsdom is
absent.

* FIX: `gui/assets/app.js::appendChatMsg` — `if (usage) msg.usage =
  usage;` restored ahead of the toolsUsed storage (both fields now
  persist side by side).
* Guard: `tests/test_chat_tools_used.py::test_source_guards` (an
  ALWAYS-RUN file) now locks BOTH `if (usage) msg.usage = usage;` +
  `msg.startTs = startTs` so a future regression cannot hide behind
  the jsdom skip. Chat suites 41 passed. pyproject
  0.9.102.8 → 0.9.102.9.

### v0.9.102.8

**CHAT RENDER `" \" IS NOT A FUNCTION` HOTFIX** (user: 2026-09-29
12:21 「今天是幾月幾日也沒有回應」+ ⚠ JS error: `"" is not a
function`): ROOT CAUSE — the v0.9.102.6 appendChatMsg storage line
ended in `.filter('')` — `Array.prototype.filter` requires a callback;
passing the empty STRING throws exactly
`TypeError: "" is not a function` the moment a turn carried ANY
non-empty `tools_used` (every turn with real tool dispatches —
including BOTH 「get 五股大門畫面」 turns, which is WHY no image / no
bubble ever appeared). The throw inside appendChatMsg killed the
sendChat reply path BEFORE the bubble was appended →「沒有回應」; the
v0.9.61.1 global error surface correctly surfaced it as
`⚠ JS error: "" is not a function`. The v0.9.102.6 source-guard test
LOCKED THE BUG IN (the v0.9.5.7 lesson AGAIN: `'msg.toolsUsed =
toolsUsed.map' in js` verified presence, never correctness).

* FIX: `gui/assets/app.js` — `.map(...)` now ends in
  `.filter(Boolean)` (drops empty strings, no callback contract
  broken); the duplicate `_isLocalImgPath` definition (a leftover
  from the v0.9.102.6 patch session — last-wins but confusing) is
  deduped to the `uploads/`-prefixed one.
* Tests: `tests/test_chat_tools_used.py` guard updated —
  `.filter(Boolean)` locked + `.filter('')` BANNED + the
  single-`_isLocalImgPath` guard. node --check green.
* Also this release ships v0.9.102.7 — CAMERA-SNAPSHOT EMBED GUARD
  (agent.py `_snapshot_paths` + `_ensure_snapshot_embed` at the four
  `_tool_loop` return sites; +24 tests). pyproject
  0.9.102.6 → 0.9.102.8.

### v0.9.102.7

**CAMERA-SNAPSHOT EMBED GUARD** (user: 2026-09-29 12:18 「get
五股大門畫面」→ 沒有 取得大門畫面): forensics — the turn REALLY
dispatched onvif_get_snapshot (OK, snapshot saved under the write
sandbox) but the flash model's final answer carried NO snapshot path
(prose only) → the chat UI's renderSandboxImages() regex found nothing
→ no inline preview; read_sandbox_image on the saved file was verified
OK (pipeline healthy — the path line was simply missing).

* FIX = the v0.9.76.3 list-chats deterministic pattern applied to
  snapshots: `src/agent/agent.py` gains `_SNAPSHOT_TOOLS`
  (onvif_get_snapshot / onvif_rtsp_snapshot) +
  `_snapshot_path_from_result()` (json path|snapshot|file key →
  drive-path regex fallback → image-ext validation)、`Agent.
  _run_tool_call` stashes every successful snapshot's path into
  `self._snapshot_paths` (reset at BOTH ask/chat_turn entries next to
  last_tool_dispatches), and the FOUR `_tool_loop` return sites
  (normal / wrap-up success / wrap-up failure fallback /
  max-iterations bail-out) now run `_ensure_snapshot_embed()` — the
  answer lacking a stashed path gets `📷 快照畫面：<path>` appended
  (the chat branch-b regex renders it inline; a path already in the
  answer → untouched; no snapshot this turn → untouched; failures
  keep the original answer).

* Tests: +24 `tests/test_snapshot_embed.py` (extraction ×7 incl.
  backslash/forward-slash fallback + mp4 skip + non-image refuse /
  stash ×6 (rtsp covered, error not stashed, dedup, attr-recovery) /
  embed ×6 (missing appends, already-carried untouched, no-stash/
  missing-attr safe, multi-snapshot lines) / integration ×4 (e2e run
  then embed, both-entry reset guard, 4 return-site wiring locked,
  tool-name set) / front-end untouched guard).
  pyproject 0.9.102.6 → 0.9.102.7.

### v0.9.102.6  
 
**CHAT HEADER MCPTOOLS + PER-BUBBLE PEEK** (user: chat llm header show which mcp tools were used, toggleable per bubble): 
 
* BACKEND: `gui/api.py::chat_turn` forwards `agent.last_tool_dispatches` (the per-turn list[str] the Agent already resets at ask/chat_turn entry) into the JS payload as `tools_used` - plain strings only ([str(x) for x in ... if x]), a non-list becomes [] so MagicMock agents / legacy providers cannot pollute the bridge. 
* FRONTEND: `appendChatMsg(..., r.usage, r.tools_used)` stores `msg.toolsUsed` (rides in the saved session JSON); `renderChat()` builds the per-bubble header with a new `#1F9F0 N` chip-button (.btn-msg-tools) showing the UNIQUE tool count - the chip title reuses the existing `chatTools` i18n key (4 locales untouched). 
* PER-BUBBLE TOGGLE: clicking a chip sets/clears that bubble's key in the new module-level `toolsPeek` Set (in-memory only) and re-renders - only THAT pair expands its `.chat-msg-tools` line (`_chatToolsText(m)` = deduped names with per-name use counts, e.g. `script_run x2 / web_search`); default = collapsed. 
* CSS: `.btn-msg-tools` (11px ghost button, opacity .8/1 on hover) + `.chat-msg-tools` (11px dim detail line). 
* Tests: +7 `tests/test_chat_tools_used.py` (bridge forward / non-list defence / app.js source guards x4 groups / api plain-string filter / css + i18n); `tests/test_chat_time_bar.py` sendChat lock updated to the 6-arg call. Chat suites 17 passed / 9 skipped (jsdom). pyproject 0.9.102.5 > 0.9.102.6.  
 
### v0.9.102.5  
  
**EXEC SCRIPT LIBRARY** (user-question: save LLM-generated python/powershell to cut tokens/time; approved build with sandbox + conda + docker isolation):  
  
* The gap: `run_python` took inline `code` only, so every recurring job re-generated the whole script (1-3k completion tokens + 2-5 s each time); the 24k+ temp-file path deleted its file after the run; context compression folded old code into summaries.  
* NEW exec-local tools (surface 5 > 8): `script_save(code, name?, lang?, description?, conda_env?, docker_image?, network?)` / `script_list()` / `script_run(name, args?, timeout?)`. 
* SAFETY: leaf-slug names only (traversal impossible), `_is_within` containment before write + run, suffix whitelist (.py/.ps1/.sh/.cmd), machine-level disaster blocklist scan at save, sha1 TAMPER check re-hashed from disk before every replay, matplotlib-CJK bootstrap wrapper on replay for chart-ish code. 
* stdio mirror `exec_server.py` = same 8-tool surface; system prompt gains the Script-library block (replay instead of regenerate + backend choices), legacy prompt untouched; `.env.example` gains `#QDRANT_EXEC_SCRIPT_LIBRARY=on` + the `QDRANT_SCRIPT_*` block. 
* Tests: +28 `tests/test_script_library.py`; exec-host surface lock 5 > 8; combined suites 138 passed / 1 skipped. pyproject 0.9.102.4 > 0.9.102.5.  
 
* REPLAY: `script_run` executes the SAVED file (python file / PowerShell -File / bash file / cmd /d /c) under the SAME exec sandbox constraints (cwd = write sandbox, READ/WRITE-root child env, 30 s default / 120 s cap timeout, 64 KB output cap) and bumps runs/last_run_at.  
* AUTO-SAVE: a SUCCESSFUL `run_python` / `run_bash` / `run_shell` call whose code is 200+ chars is written to `<WRITE_ROOT>/scripts/` and the tool result carries `auto_saved: {name, note}` - the LLM learns the replay name inside the same result. 
* REPLAY: `script_run` executes the SAVED file (python file / PowerShell -File / bash file / cmd /d /c) under the SAME exec sandbox constraints (cwd = write sandbox, READ/WRITE-root child env, 30 s default / 120 s cap timeout, 64 KB output cap) and bumps runs/last_run_at. 
* ISOLATION: direct (inherit) : `conda_env=` = `conda run --no-capture-output -n ENV python` : `docker_image=` = `docker run --rm -i --workdir /sandbox -v <write_root>:/sandbox --network none --memory 2g --cpus 2 <image> python /sandbox/scripts/<file>` (the write sandbox is the ONLY mount; bridge opt-in per script); globals `QDRANT_SCRIPT_RUNTIME` / `QDRANT_SCRIPT_CONDA_ENV` / `QDRANT_SCRIPT_DOCKER_*`; missing conda/docker = clear envelope errors. 
### v0.9.102.4

**STOCK LIVE ORDERING ENABLED (config-only)**（user: 「協助股票下單功能我啟動」）: audited all FIVE gates in the live .env — (1) MEGA_STOCK_MODE=live ✓ (already on; queries live-proven), (2) MEGA_STOCK_TRADING_ENABLED=on ✓ ADDED (the last missing switch), (3) certificate MEGA_STOCK_CERT_PATH exists on disk (5,057 bytes) ✓, (4) Telegram consent gate ORDER_APPROVAL=on + chat <YOUR_TELEGRAM_CHAT_ID> ✓ — every live order requires replying 「同意 STK-XXXX」 first, (5) per-call confirm=true ✓ (LLM prompt gate). stock-local already in MCP_ACTIVE_SERVERS. Restart required; first order flow: chat 「以 2330 限價 N 元 買進 1 張,confirm=true」 → Telegram 同意碼 → SendNewOrderEx. pyproject 0.9.102.3 -> 0.9.102.4.

### v0.9.102.3

**TOOLROUTE STATUS AUDIT**（user: 「jev的彈性mcp掛載是無法使用了嗎」）: AUDIT RESULT — the Jev dynamic tool-surface was NEVER disabled: the live .env still carries QDRANT_TOOLROUTE=on (set when the user switched shadow→on on 09-23; an initial findstr probe missed it and a duplicate block was briefly added then removed). Config unchanged; code unchanged. Current composition: core = qdrant/web/datetime/meta/filesystem/scheduler(0.9.102.2) + ONE Jev choice per turn mounts the winning class (exec-class turns mount exec-local/archive-local per 0.9.102.1) — hard-capped 128. pyproject 0.9.102.2 -> 0.9.102.3 (audit entry only).

### v0.9.102.3

**TOOLROUTE RESTORED (config-only)**（user: 「jev的彈性mcp掛載是無法
使用了嗎」）: YES — checking the live .env found the
`QDRANT_TOOLROUTE=on` line GONE (default = off → the Jev dynamic
tool-surface was dead; every turn fell back to the legacy
MCP_ACTIVE_SERVERS+cap surface with NO Jev routing). Code untouched;
restored `QDRANT_TOOLROUTE=on` + `QDRANT_TOOLROUTE_PROB=0.6` to the
live .env (with an explanatory comment block); the scheduler-local /
exec-local core+class additions from 0.9.102.1/102.2 compose with it
(core = always on-surface; exec-class turns mount exec-local).
pyproject 0.9.102.2 -> 0.9.102.3; RESTART required.

### v0.9.102.2

**SCHEDULER AS CORE**（user: 「啟動排程MCP為基本功能」）: the
scheduler-local MCP server (8 tools — add/list/get/enable/disable/
run-now/status/errors) is now a FIRST-CLASS basic capability:
(1) live .env MCP_ACTIVE_SERVERS += scheduler-local (it had been
trimmed in the v0.9.92 latency pass — the LLM could no longer manage
schedules by chat); (2) ToolRoute _TOOLROUTE_CORE_SERVERS +=
scheduler-local — present on EVERY turn's surface regardless of the
Jev class (排程管理 is now always available, no route needed);
toolroute 13 + mcp_active_servers 15 = 28 passed;
pyproject 0.9.102.1 -> 0.9.102.2; RESTART required.

### v0.9.102.1

**EXEC TOOLS REMOUNTED — 模擬中斷 ROOT CAUSE**（user: 「查 app.log
為何 報行模擬 不久就會被中斷」）: app.log forensics — the 2026-09-28
「進行模擬」/「執行歷史 K 線和模擬」 turns ended after ~15-17 s with
ONLY a plan (answer_len 210/90/0, tokens 55-100k, dispatches 0-3
read_file/kline but NEVER run_python): root cause = (a) the v0.9.92
latency pass dropped **exec-local** from MCP_ACTIVE_SERVERS —
run_python/run_bash were no longer on the LLM surface at all, and
(b) the v0.9.98.0 ToolRoute map had `"exec": ()` (stale claim that
exec lives inside filesystem-local) so even a correct class=exec
p=0.89 route mounted NOTHING. The LLM literally COULD NOT execute
the simulation — it could only describe one. FIXED: exec class now
maps to ("exec-local", "archive-local") and **exec-local is back in
MCP_ACTIVE_SERVERS** (live .env, position after filesystem-local);
archive class maps to ("archive-local",). The surface grows only on
exec-classified turns; other turns stay lean. toolroute 13 passed;
pyproject 0.9.102.0 -> 0.9.102.1; RESTART required.

### v0.9.102.0

**TELEGRAM SESSION CONTEXT DISCIPLINE**（user: 「telegram 的 session
不能更新和清除 導致 context 內容過多 llm 錯亂」）: the per-chat
session tgbot-<chat_id> grew FOREVER and EVERY turn fed the ENTIRE
history to chat_turn (schedulers already capped theirs — the bot
never did). THREE controls in src/agent/telegram_bot.py::handle:
(1) TELEGRAM_HISTORY_CAP (default 12; ≤0/-1 = legacy full) — both
chat_turn sites (main + photo-hallucination corrective retry) now
feed ONLY the last N messages; (2) TELEGRAM_SESSION_MAX_MESSAGES
(default 200; -1 = unlimited) — stored-session RING trim keeps the
newest N so the session file cannot grow unbounded; (3) in-chat
clear commands /new /clear /reset /newchat or 清除對話/清除對話記錄/
新對話/重置對話/清除記錄 wipe the chat's session immediately and
reply 🧹 (zero LLM turn burned; exact-match only — 「/news …」 never
triggers). .env + .env.example documented (live .env: cap 12/200);
tests +13 tests/test_telegram_session_cap.py (resolvers / feed
slice 30→4 / legacy full / file keeps everything / ring trim 22→10 /
clear-command matrix / normal-message not-a-command); telegram
regression 146 passed; pyproject 0.9.101.4 -> 0.9.102.0.

### v0.9.101.4

**SNIPPET COUNT ROW REMOVED**（user: 「28 個片語 這列不用出現」）:
the .chat-tools-head count row (「N 個片語」) is GONE from the
snippets panel — index.html drops the head row entirely (the panel is
just the chip row now) and renderSnippets hides the head defensively;
snipsCount i18n key retired from render; web_server 53 passed;
pyproject 0.9.101.3 -> 0.9.101.4.

### v0.9.101.3

**SNIPPETS DEFAULT-COLLAPSED ADD ROW**（user spec: default = chips +
「顯示新增列」 at the END of the chip row; click reveals the ➕ input
with the button becoming ▲ 隱藏新增列; after the add, clicking ▲ hides
the input and 顯示新增列 returns to the end of the chip row）:
#chat-snips-manage moved INTO .snips-row after #chat-snips-add-wrap
(margin-left:auto = row end); add-wrap starts hidden + default
snipsManageOn=false; _snipsManageApply paints the label at bind;
i18n snipsShowAdd = 顯示新增列 ×4 (no ▼ prefix); snipsEmpty hint
rewritten ×4 (zh-TW/zh-CN keys were missing after an earlier edit —
restored); jsdom verified the A→B→C→D cycle (default hidden+row-end
button / click reveals input+▲ / add chip / ▲ hides again + dels
hidden); guard test asserts list < add-wrap < manage inside snips-row;
web_server+notes_ui 68 passed; pyproject 0.9.101.2 -> 0.9.101.3.

### v0.9.101.2

**SNIPPET ADD-ROW VANISH BUGFIX**（user: 「1. 沒有出現 改放在 chips
列的最尾端 同一排 2. 新增列沒有消失，倒是片語 新增輸入框 一開始
就不見，按 新增片語 也不會出現」）: ROOT CAUSE found via jsdom —
v0.9.101.1 moved #chat-snips-add-wrap INSIDE #chat-snips-list, whose
renderSnippets() opens with list.replaceChildren() — the add-wrap was
SWALLOWED on the first render (null thereafter; ▲/▼ toggled an orphan
node = no visible effect). FIXED: the add row is now a SIBLING of the
chip list inside a new .snips-row flex container (chips +
input+button in ONE visual row, add row margin-left:auto = right
edge); replaceChildren can never reach it; ▲ hides it completely;
snips-list is display:contents so chips flow in the same row; jsdom
verified open/▲/▼ cycle; guard test asserts the sibling structure;
web_server 53 passed; pyproject 0.9.101.1 -> 0.9.101.2.

### v0.9.101.1

**SNIPPET ADD ROW INTO CHIP ROW**（user: 「隱藏新增列後 該列要消失，
新增片話 的按鈕 移到 片話列 的最後面」）: the ➕ add input+button
moved OUT of the panel head INTO the chip row as its LAST element
(renderSnippets node-moves #chat-snips-add-wrap after the chips —
listeners survive a move); ▲ 隱藏新增列 now makes the row vanish
COMPLETELY (inline-flex [hidden] display:none — no leftover gap) and
the ▼ button in the head brings it back; guard test asserts the
add-wrap is the list's child; web_server 53 passed;
pyproject 0.9.101.0 -> 0.9.101.1.

### v0.9.101.0

**SNIPPETS GLOBAL + MANAGE TOGGLE + LOG SHRINK**（user: 「1. 新片話
輸人列 提供隱藏功能 2. 片話 是 global 所有裝置就要看到 3. 啟用片語
不要影響對話輸入框位置，將對話顯示框高度縮小應對」）: (1) the ➕
add row is HIDEABLE — a ▲/▼ manage toggle in the panel head flips
add-row + chip ✕ delete buttons; (2) the snippet store moved from
per-device localStorage to a SERVER-SIDE GLOBAL store — new Api
snippets_get / snippets_save (single-dict positional convention,
sanitize strip/200-char/dedupe/50-cap, atomic tmp+os.replace) over
$QDRANT_RAG_HOME/snippets.json — every device (LAN web / phone /
GUI) sees the SAME phrases; localStorage is now only an OFFLINE
cache fallback; (3) opening the panel SHRINKS #chat-log by the
panel height (_snipsAdjustLog, 160px floor; panel max-height 40vh
scroll) so the chat input box NEVER moves; i18n ×4
(snipsHideAdd/snipsShowAdd); tests +1 Api round-trip
(test_snippets_global_store_api); web_server 53 passed;
pyproject 0.9.100.1 -> 0.9.101.0.

### v0.9.100.1

**SNIPPETS PANEL ABOVE INPUT**（user: 「快速片話 位置 應該在 對話輸入框
上方」）: the quick-phrases panel + empty hint moved OUT of the toolbar
area to sit DIRECTLY ABOVE the chat input box (after #chat-log, before
.chat-input) — click a chip and it inserts into the input right below;
guard test now asserts the panel position (chat-log < panel <
chat-input); web_server 52 passed; pyproject 0.9.100.0 -> 0.9.100.1.

### v0.9.100.0

**QUICK PHRASES**（user: 「提供 用戶 可以事先製定 片語或命令 可以讓
用戶快速的 䀡到 新話輸入的句子中」）: chat 工具列新 ⚡ 按鈕開啟「快速
片語」面板 — 使用者事先建立的片語/命令 chips，點擊即插入 chat 輸入框
（游標尾端、自動補空格、focus 回輸入框）；面板內 ➕ 新增（Enter 亦可）
/ ✕ 刪除；持久化 localStorage['qdrant-rag.snippets']（上限 50、去重、
壞值過濾）；XSS-safe（純 createElement+textContent）；空清單提示；
i18n ×4（7 keys）；guard test test_snippets_panel_wiring；web_server
52 passed; pyproject 0.9.99.9 -> 0.9.100.0。

### v0.9.99.9

**TABS PINNED UNDER TOPBAR**（user: 「對話、攝入、搜尋 的 tab列
並沒有固定在品牌列正下方」）: v0.9.99.8 only moved the nav in the DOM —
the page still scrolled the tabs away because the body was a normal
block flow; NOW the app shell is a flex COLUMN (body display:flex;
flex-direction:column): .topbar / .tabs / .statusbar flex:0 0 auto
(pinned), .content flex:1 1 auto + min-height:0 + width:100% (the
ONLY scroller) — the tab bar is now truly FIXED under the brand bar
on every tab; guard test test_tabs_pinned_under_topbar; web_server
51 passed; pyproject 0.9.99.8 -> 0.9.99.9.

### v0.9.99.8

**TABS UNDER TOPBAR**（user: 「搜尋頁 的 功能tab 移到 🧠 Divine Sense
Agent 下面 搜尋列 上面」）: the nav.tabs tab bar moved OUT of the
content area to sit directly under the brand topbar and above the
panels (chat toolbar / ingest / search …); ALSO removed the stray
second `<main class="content">` opener that used to wrap the admin
panels (the document now carries exactly ONE main element — invalid
nested-main HTML fixed); guard test test_index_tabs_before_main;
web_server 50 passed; pyproject 0.9.99.7 -> 0.9.99.8.

### v0.9.99.7

**NOTES STATUSBAR**（user: 「最大化顯示 要加上 最方下的 態狀bar」）: 筆記本
編輯器內容框下方新增狀態列 `.ne-statusbar` — 字數 / 行數 / 儲存狀態
（儲存成功後轉為「已儲存於 HH:MM」）/ 游標位置 Ln,Col（keyup+click 更新）；
`notesSbUpdate()` 在 notesOpen 載入、notesNew 開啟、input/keyup/click、
save 成功時刷新；i18n ×4（notesSbChars/Lines/Saved/SavedAt）；
notes_ui 15 passed; pyproject 0.9.99.6 -> 0.9.99.7。

### v0.9.99.6 — 筆記本內容框預設最大化（user: 「筆記本的內容框 的 長度 default 最大化」）

* **編輯框 `#ne-body`**：`min-height: 560px` + `height: 60vh` +
  `max-height: 80vh`（rows 14 → 28）— 打開編輯器就是大畫面，
  仍可 `resize: vertical` 手動調整；字型跟隨 `--user-mono`。
* **預覽框 `.ne-preview`** 同步最大化（min-height 560px /
  height 60vh / max-height none）— 編輯 ⇄ 預覽切換不再跳動；
  移除舊的小規則（min-height 120 / max-height 460）。
* CSS 15 行（含清掉重複規則）；合併回歸 notes_ui **15 passed**。
* pyproject 0.9.99.5 → **0.9.99.6**。

### v0.9.99.5 — hotfix：按「隱藏清單」list 不會消失（user 回報）

* **根因**：`.notes-list` / `.notes-pager` 是 `display: flex` —
  author 規則**永遠覆寫** UA 的 `[hidden] { display: none }`
  （優先序：author > UA，不看 specificity），所以 JS 設的
  `hidden` 屬性實際上無效。
* **修正**：補 `.notes-list[hidden] / .notes-pager[hidden] /
  .notes-list-tools[hidden] { display: none }`（同既有
  `#chat-tools-panel[hidden]` 慣例）。JS 邏輯本來就是對的
  （`hidden = !notesListVisible`），純 CSS 一行修。
* 測試 +guard（test_notes_ui.py：三個 `[hidden]` 選擇器必須存在
  且帶 display:none — 防回歸）；**15 passed**。
* pyproject 0.9.99.4 → **0.9.99.5**（CSS 5 行）。

### v0.9.99.4 — 筆記本清單預設隱藏（user: 「提供 筆記本 lists 隱藏功能( default 隱藏)」）

* Notes 清單（list + 展開/收合工具列 + pager）**預設隱藏** — 進入
  筆記本分頁只見搜尋工具列，畫面乾淨（搭配每列收合 =
  兩層折疊的最簡呈現）。
* 工具列新增 **📋 顯示清單 / 📋 隱藏清單** 切換按鈕 — 點擊展開
  清單（此時才呼叫 notes_page 載入第一頁，省一次 API）；再點收合。
* 實作：`notesListVisible=false` 狀態 + `notesListApply()`（同步
  list/tools/pager 的 hidden + 按鈕文案 i18n ×4：notesListShow /
  notesListHide）；`notesLoad` 尾端重套用（外部儲存/刪除後 reload
  不會意外展開）。
* 測試 +guard（test_notes_ui.py：狀態預設 false、notesListApply/
  Toggle 函式、notesLoad 尾端重套用、html list/pager 預設 hidden、
  toggle 按鈕、i18n ×4）；**15 passed**。
* pyproject 0.9.99.3 → **0.9.99.4**。

### v0.9.99.3 — 筆記本清單合併折疊（user: 「將 筆記本 的 搜尋和 list 的結果 提供 合併折疊」）

* Notes 清單每列**預設收合**（只顯示標題 + 分類 + 時間）— 點標題
  列**展開/收合** snippet（▸/▾ 指示箭頭，`stopPropagation` 不觸發
  開啟編輯器）；點列上其他區域仍可直接開啟筆記。
* 清單下方新增 **▾ 全部展開 / ▸ 全部收合** 兩顆按鈕（批次操作，
  搜尋結果多時快速掃描）。
* CSS：`.note-row.collapsed .note-snippet { display: none }` +
  head 右側 ▸/▾ 指示符（`::after`）；i18n ×4（notesExpandAll /
  notesCollapseAll）。
* 測試 +guard 更新（test_notes_ui.py：app.js collapsed/toggle/
  expand-all/collapse-all、html 兩按鈕、i18n ×4 keys、CSS collapsed
  區塊）；合併回歸 notes_ui 15 + keep_host 48 = **63 passed**。
* pyproject 0.9.99.2 → **0.9.99.3**。

### v0.9.99.2 — 背景圖佈放格式（user: 「chat ui/web 的背景圖 要提供 滿格縮放、置中等佈放格式」）

外觀對話框的背景圖區新增「佈放格式」選單（`#ap-bg-fit`）：

* **滿格縮放（cover，預設）／ 完整顯示（contain）／ 原尺置中
  （center）／ 重複平鋪（tile）** — 實作為 `body.dataset.bgFit`
  屬性 + CSS `body.has-bg[data-bg-fit=…]` 四個區塊
  （background-size/repeat + position:center + attachment:fixed）；
  清除背景時移除屬性。
* app.js：`_applyFromUi()` 統一從 UI 讀值套用（檔案/URL/暗化/佈放
  共用）；`openAppearance` 還原選單值；`bgFit` 存入
  `qdrant-rag.appearance` JSON。
* i18n ×4（appearanceBgFit / FitCover / FitContain / FitCenter /
  FitTile ×4 locale）。
* 測試 +4（`tests/test_bgfit.py`：index 選單 guard、app.js 邏輯、
  CSS 四區塊、i18n ×4）；合併回歸 bgfit 4 + appearance 5 =
  **9 passed**。
* pyproject 0.9.99.1 → **0.9.99.2**。

### v0.9.99.1 — hotfix：外觀對話框上線後全頁樣式壞掉（user: 「格式都跑掉了」）

* **根因**：v0.9.99.0 的 body font-family 包裝只包了前半 —
  `var(--user-font, -apple-system, …` **沒有閉合括號**。未閉合的
  `(` 會讓 CSS 解析器吞掉後續大量規則（直到下一個 `)`），頂欄/
  面板/表格全數失去樣式。
* **修正**：補上 `sans-serif)` 閉合括號；順帶清掉一個既有孤兒
  `}`（scrollbar 規則後，無害但讓花括號不平衡 344/345）；
  bg-url-apply 按鈕標籤從 `notesSave`（💾 儲存）改為新的
  `appearanceApply`（套用）i18n key ×4。
* **結構驗證**：花括號 344/344 平衡、深度掃描無負值、`var(--user-…`
  全數閉合；nav.tabs 位置比對原始設計（本就位於 main 內、search
  與 admin 面板之間 — 非本次損壞）。
* 合併回歸 appearance 5 + theme 5 + notes_ui 15 + web_server 33 =
  **74 passed**。
* pyproject 0.9.99.0 → **0.9.99.1**（CSS 一字修正 + label）。

### v0.9.99.0 — 外觀設定：背景圖 / 自選色調 / 字型（user: 「提供 web/ui 可以改 theme 背景圖、自選色調、字型」）

header 🖌️ 按鈕開啟「🎨 外觀設定」對話框（全前端、localStorage
`qdrant-rag.appearance` 持久化、CSS 變數 inline 覆寫 — 不干擾
主題 auto/light/dark 切換）：

* **🖼️ 背景圖**：檔案上傳（FileReader → canvas 縮圖 ≤1920px →
  JPEG 0.85 dataURL，2.5 MB 上限 guard）或圖片 URL；暗化滑桿
  0-80%（linear-gradient overlay）；套用時 `body.has-bg` +
  半透明 `--bg/--bg-2/--bg-3`（hex→rgba 換算，讓面板透出背景）
  ；清除一鍵還原；quota 滿時自動丟棄背景圖保留其餘設定。
* **🎨 自選色調**：`<input type=color>` + 6 個預設色票 — 覆寫
  `--primary` + `--accent`（inline 變數優先於 palette），重設
  即還原主題色。
* **🔤 字型**：預設選單（系統預設/微軟正黑體/Noto Sans/標楷體/
  Segoe UI/襯線/Comic Sans）+ 自訂 CSS 輸入 + 字級（標準/小/
  大/特大，`--user-font-size`）+ 程式碼字型（`--user-mono`，
  `pre/code` 附加規則）。body 的 font-family/font-size 包進
  `var(--user-font, …)` / `var(--user-font-size, 14px)` hook。
* i18n ×4（23 個 appearance keys ×4 locale）；檔案重編碼
  canvas 縮圖避免 localStorage 爆量（quota 時自動丟棄背景保其餘）。
* 測試 +5（`tests/test_appearance.py`：index 對話框/按鈕 + dialog
  在 header 外 guard、app.js 九函式 + boot 接線 + canvas 編碼、
  CSS 變數 hook、i18n ×4、**jsdom 行為 ×9 情境**（accent 套用/
  bg has-bg+url/清除/字型/字級/mono/persist/restore）；合併回歸
  appearance 5 + theme 5 + notes_ui 15 = **25 passed**。
* pyproject 0.9.98.3 → **0.9.99.0**。

### v0.9.98.3 — hotfix：kline 拿到 2022 年舊資料（user 回報「華邦電 十日內 klin」四連發都回 2022/08/11–24 區段）

* **根因**（offload 檔直接鐵證）：兆豐 `GetDKChartData` 的 rows 是
  **新在前**（rows[0] = 20260923 今日，rows[-1] = 20220810 最舊），
  而 v0.9.98.1 的 `_compact_live_kline` 用 `rows[-bars:]` 取尾 —
  拿到的是**最舊的 10 根（2022 年）**。之前 offload threshold 20k
  攔不住是因為整包歷史 ~14.6 萬 chars 有被 offload，但 compact
  slice 本身切錯端。
* **修正**：比較首尾列的日期欄位決定方向 — 新在前 → 切**頭**
  `rows[:bars]`（防禦性：升冪 payload 回退切尾）；兩者皆保證拿到
  **最新** bars。
* 測試 +2 更新（`test_kline_compact.py` 改用真實 API 的新在前
  row 順序 + 回歸鎖「尾切 = 2022 bug」+ 升冪 payload 防禦性測試）；
  **7 passed**；離線重放真實 payload 形狀（rows[0]=20260923300 …
  rows[-1]=20220811）→ candles[0] = 最新一根 ✓。
* pyproject 0.9.98.2 → **0.9.98.3**。

### v0.9.98.2 — docs-only sync（user: 「更新文件」）

三份結構文件的追趕同步（README / ARCHITECTURE / CLAUDE.md）：

* **README**：§8 stdio 清單 19 → **21** 並補 `teams_assist_server` /
  `keep_server` / `jev_server` 三行；架構章節「20 個 server 模組」→
  21；架構圖 in-process LocalHosts 列舉補 **Keep / JevClassifier**；
  更新日誌 v0.9.95.0 節序位修正（挪至 v0.9.94.0 之前，新在前）。
* **ARCHITECTURE.md**：版本頭 v0.9.70.0 → **v0.9.98.1** + 兩段
  catch-up 摘要（v0.9.71-94：Teams UIA/白名單、hybrid search +
  rerank、Keep 筆記本、calendar guard、Jev 分類器、grounding
  guard；v0.9.95-98：Notes UI、專案更名、402 failover、CRLF /
  cache-bust、主題選擇器、**QDRANT_TOOLROUTE 動態工具表面**、live
  kline compact −97%）＋ Appendix A 新增
  `keep_tools.py` / `jev_tools.py` 完整條目 ＋ Last-reviewed →
  2026-09-23。
* **CLAUDE.md**：intro TWENTY-TWO → **TWENTY-FOUR** LocalHosts +
  列舉補 KeepLocalHost / JevLocalHost；stdio 20 → 21；§3 模組樹補
  `keep_tools.py` / `jev_tools.py` / `keep_server.py` /
  `jev_server.py` 四行。
* pyproject 0.9.98.1 → **0.9.98.2**（僅文件，零程式碼）。

### v0.9.98.1 — 肥工具結果兩項優化（user: 「兩項優化」 — 回應 v0.9.98.0 上線後 stock 回合 tokens=138k/258k 的觀察）

* **鑑識**：live `stock_get_kline` **忽略 `bars`** — `GetDKChartData`
  回傳**整包歷史**（數百筆寬 rows ≈ 15-20k chars）→ 剛好低於 20k
  offload 門檻 → inline，且被**每個 tool-loop iteration 重複攜帶**
  （138k-token 單派發 / 258k-token 三派發回合）。
* **優化 ①（kline 瘦身，程式）**：live 分支套用 `_compact_live_kline` —
  保留**最近 `bars` 根**（`DEFAULT_KLINE_BARS` 20 → **10**）、每根只留
  `{date,open,high,low,close,volume}`（與 mock candle 同形）、被截斷
  時把**完整原始 rows 原子寫入 offload 檔**（read sandbox / 可
  `read_file`）+ `note` 明示「勿重複呼叫」。實測 600 筆歷史
  46,082 chars → **1,456 chars（−97%）**；任何失敗 fail-open 回
  原始 payload。
* **優化 ②（offload 門檻，config-only）**：live `.env` 新增
  `CHAT_OFFLOAD_THRESHOLD_CHARS=6000`（原 20000）— kline/quotes 批次
  等大型工具結果 >6k chars 即 offload（pointer + 800 字 preview
  inline，全文進 read sandbox），工具迴圈各 iteration 只攜帶
  pointer。
* 測試 +6（`tests/test_kline_compact.py`：截斷+compact+full_file、
  短歷史不動、`QDRANT_OFFLOAD_DIR` 優先、壞形狀 raw 透傳、mock
  形狀不變、預設 bars=10）；合併回歸 19 passed。
* pyproject 0.9.98.0 → **0.9.98.1**。

### v0.9.98.0 — Jev 動態工具表面：LLM 執行前路由 tool calling（user: 「是否以在llm執行前以JEV判斷需使用的tool calling，彈性配置當下的tool calling，讓tool calling控制在128以下」）

* **機制**：LLM 工具迴圈**執行前**用**一次** Jev `choice` 把問題分類
  到工具類別（stock/calendar/keep/chat-history/telegram/desktop/
  cloud/camera/log/…20 類）→ 該回合表面 = **核心群**
  （qdrant/web/datetime/meta/filesystem，約 27 工具）+ 中選類別的
  server 群；仍受 `MCP_ACTIVE_SERVERS` 過濾 + 90 cap，再以
  `QDRANT_TOOLROUTE_CAP`（**預設 128** = provider tools 陣列上限）
  硬頂 — **恆 < 128**。
* **三態**：`QDRANT_TOOLROUTE` off（預設，行為不變）| shadow（只記
  `[ToolRoute]` log 一週觀察）| on（套用）。`live .env` 已設
  **shadow**。
* **安全網（fail-open）**：Jev 失敗/低信心（< `PROB` 0.6）/ 路由
  server 對應 0 工具 / GUI picker 指定 / 任何 exception → 退回
  legacy 表面（不會因路由而失去工具）；`meta-local` 永在核心
  （list_mcp_tools 發現契約不破）；行事曆糾正回合維持全表面。
* 每回合 token 收益：90 →（一般問題）~27 工具 = 表面 schema 大幅
  縮減；stock/calendar 問題才掛對應 9-13 工具。
* 測試 +13（`tests/test_toolroute.py`：resolvers/off 不觸發 Jev/
  信心矩陣/Jev 失敗 fail-open/server→tool 名單轉換/0 匹配回退/
  cap 128）；`test_mcp_active_servers.py` 加 autouse
  `_toolroute_off` fixture（防 live .env 洩漏）15 passed；合併回歸
  toolroute 13 + active_servers 15 零回歸。
* pyproject 0.9.97.2 → **0.9.98.0**。

### v0.9.97.2 — stock-local 重新掛入 LLM 表面（user: 「為何 stock-local 沒有掛載」）

* **診斷**：`stock-local` **一直都有掛載**（fleet 207 tools 含其 9
  個工具；in-process MegaStockLocalHost 開機即註冊）— 它是被
  `MCP_SURFACE_MAX_TOOLS=90` 的**表面裁切**從 LLM 可見清單中藏掉：
  裁切優先序 = `MCP_ACTIVE_SERVERS` 清單順序，stock-local 排在
  倒數第 3 位 → v0.9.92/93 延遲改進把 cap 127 降到 90 後落進被藏
  尾端（log：`tool surface 207 > 90 — trimmed, hidden servers: […
  stock-local …]`）。
* **修正（config-only）**：`.env` 的 `MCP_ACTIVE_SERVERS` 把
  `stock-local` 從尾端移到 `cred-local` 之後（第 14 位）— 插入點
  在 90 cap 內（前段合計 ~80 tools），**不擠掉任何目前可見的
  server**（被擠的仍是原本就 hidden 的尾端）。
* 需重啟應用生效；chat 輪詢後 log 的 surface-trim WARNING 應不再
  列出 `stock-local`。
* pyproject 0.9.97.1 → **0.9.97.2**（僅 .env 順序）。

### v0.9.97.1 — hotfix：啟動畫面檢查「一直轉、沒變綠就跳主畫面」（user: 「啟動圖面的所有確認一直轉，然後就切到主畫面，沒有變綠的過程」）

* **鑑識**：後端鏈完全健康（實測 `get_readiness` 首回應 2.19s —
  backend/qdrant/llm 全 `ok:true`、mcp `done:false 初始化中`；
  之後每 1.5s 輪詢 0.01-0.03s，fleet ready ~14s 後 `done:true`）；
  splash e2e 6 測試（含 `mcp_pending_then_ok` 場景）全過 — JS 邏輯
  與 payload 形狀皆正確。
* **根因判定**：症狀與 **stale app.js**（v0.9.87.3 之前的
  duplicate-immediate-finish bug：首個 pending 回應把 mcp row 標
  fail → 不變綠直接跳主畫面）完全吻合 = 瀏覽器/WebView 快取層在
  資源更新後仍供應舊 `app.js`（`Cache-Control: no-cache` **沒有
  validator**（無 ETag/Last-Modified）+ bfcache 等快取層怪癖）。
* **修正（根治 stale-JS）**：
  1. **版本化資源 URL**（cache-bust）：web server 供應的 index.html
     將 `app.js` / `style.css` / `/pywebview-shim.js` 引用注入
     `?v=<pyproject 版本>` — 任何快取層以版本 keying，新版永遠拿到
     新 JS；do_GET 已 strip query。
  2. **ETag + If-None-Match → 304**：`_send_bytes` 加
     `ETag: "<sha1[:16]>"`（真 revalidate 錨點）；帶相符
     If-None-Match 的請求回 304 Not Modified。
* 測試 +3（`test_web_server.py`：index 含三個 `?v=<version>` 引用
  且版本 == pyproject、靜態資源帶 ETag + no-cache、
  If-None-Match → 304 空 body）；web_server 33 + splash e2e 6 =
  **55 passed** 零回歸。
* **使用者即時解法**：本版上線後在瀏覽器按一次 **Ctrl+F5**（強制
  載入新 index）；之後版本更新會經版本化 URL 自動破快取。
* pyproject 0.9.97.0 → **0.9.97.1**（web_server.py + 測試）。

### v0.9.97.0 — UI/Web 主題選擇設定（user: 「提供 ui/web theme 選擇設定」）

* **🎨 三態主題選擇器**（header 右側，語言切換器旁）：**🌗 自動
  （跟隨系統）/ ☀️ 淺色 / 🌙 深色** — 取代舊的雙態 🌓 checkbox
  （舊版只能在 dark ⇄ light 間切換，**無法回到跟隨系統**）。
* **持久化**：`localStorage['qdrant-rag.theme']`（key 不變 — 既有
  使用者已存的 light/dark 選擇直接生效）；重新開啟自動恢復；
  `auto` 模式監聽 `prefers-color-scheme` 變化即時跟隨系統切換。
* **CSS 端零新調色**：style.css 既有
  `<html data-theme="light|dark|auto">` 三組 CSS 變數直接驅動
  （auto 走 media query）— JS 只寫 dataset。
* **i18n ×4**（zh-TW / zh-CN / en / ja：themeLabel /
  themeAuto / themeLight / themeDark）+ `applyI18n` 新增
  `[data-i18n-title]` title 屬性支援（選擇器 tooltip 隨語系切換）。
* **行為**（app.js）：`setupTheme` boot 套用 + 綁定 `#theme-select`
  change → `applyTheme`（寫 `dataset.theme` + localStorage）；
  `effectiveTheme` 白名單驗證儲存值（壞值回退 auto）。
* 測試 +5（`tests/test_theme_picker.py`：index 三態選單 + 舊
  checkbox 移除 guard、app.js 三態邏輯 + localStorage key 不變、
  i18n ×4、CSS `.theme-picker` + 三組 data-theme 調色塊、
  **jsdom 行為測試 ×6 情境**（fresh=auto / 選 dark / 選 light /
  選回 auto / 重開恢復 / 壞值回退 — node driver 以絕對路徑 require
  `.cache/jsdom`，無環境自動 skip）；theme 5 + notes_ui 15 +
  web_server 30 = **66 passed**。
* pyproject 0.9.96.3 → **0.9.97.0**。

### v0.9.96.3 — hotfix：筆記本 UI 橋接 TypeError（user 回報 Notes 分頁顯示 `TypeError("int() argument must be a string, a bytes-like object or a real number, not 'dict'")`）

* **根因**：pywebview 橋接與 web shim 都是**位置參數**（`fn(*args)`，
  既有慣例 = 多欄位 payload 用單一 dict 參數，如
  `save_credential(payload)` / `add_schedule(payload)`）。v0.9.95.0
  的 notes_* Api 卻用具名參數簽名 — 前端送的一個 options 物件整包
  落在第一個參數 `page` 上 → `int(dict)` TypeError（notes_get/save/
  delete 同樣拿到錯位的參數）。
* **修正**（`gui/api.py`，save_credential 慣例對齊）：
  `notes_page({page,page_size,query,category,date_from,date_to})`、
  `notes_save({note_id,title,body,category})`、
  `notes_get({note_id})` / `notes_delete({note_id})`（裸 id 字串也
  容忍）；壞 page/page_size 值容忍（fallback 預設值，不再 raise）。
  app.js 前端**零改動**（本來就送 dict）。`notes_categories` 無參數
  不受影響。
* 測試 +2 更新（`tests/test_notes_ui.py`：**橋接形狀回歸** — 單一
  dict 位置參數全鏈 save→page→get→delete；壞 page 參數容忍）；
  notes_ui 15 + keep_host 48 = **63 passed**；E2E 實測：web server
  上以 shim 實際形狀 `{"args": [{...}]}` POST
  notes_page/save/get/delete 全 ok，store 往返乾淨。
* pyproject 0.9.96.2 → **0.9.96.3**。

### v0.9.96.2 — hotfix：新啟動器 bat 解析噪音（user 回報 `divine-sense-agent-web.bat` 執行時印出 `'M' is not recognized` ×4）

* **根因**：v0.9.96.0 更名腳本以文字模式讀寫 bat（`newline=""`），
  四個 launcher（divine-sense-agent{,-gui,-web}.bat + build-gui.bat）
  行尾變成 **LF-only**；web 版 REM 註解還含 UTF-8 em-dash（3 bytes）。
  cmd 批次解析器要求 CRLF，且在 `chcp 65001` 生效前以 CP950 解析
  em-dash 位元組 → 行被撕裂成碎片（`'M'`）被當指令執行。
* **修正**：四檔重寫為 **CRLF + 純 ASCII**（em-dash → "-"）；
  驗證 byte 層級 `bare-LF=0 non-ascii=0`；實跑
  `divine-sense-agent.bat --help` rc=0、**零解析噪音**、description
  正確。舊名 shims 原本即 CRLF/ascii 不受影響。
* pyproject 0.9.96.1 → **0.9.96.2**（僅 bat 檔位元組修正，無程式碼）。

### v0.9.96.1 — app.log 近期錯誤確認與修正（user: 「確認 app.log 近期的錯誤是否確認和修正」）

日誌鑑識（gui/app.log，8939 行，2026-09-18 → 09-23）：

* **402 Payment Required ×4 turn**（08:27 / 10:03 / 10:09 / 10:10，
  telegram 來源）— OpenRouter 餘額不足；**failover 機制四輪全部成功**
  降級 ollama（glm-5.3-flash:cloud）並正常回答（1,369 字等）。
  **問題確認**：`_create_validated` 對確定性錯誤也重試 2 次 —
  每個 402 turn 浪費 2 次嘗試 + ~1s + 2 次 API round-trip。
  **修正**：HTTP **401 / 402 / 403**（認證/餘額/權限）— 同 payload
  重試永不復原 → attempt 1 後**直接 failover**（log 新增
  `status NNN is non-retryable — failing over now`）；暫時性狀態
  （429 / 5xx）**保留** 2 次重試行為不變。
* **測試 traceback 污染正式 app.log ×2**（「boom」/「manager blew up」，
  11:01 / 11:19）— 時間戳與 pytest 執行完全吻合：test_web_server.py
  與 test_diagrams_window.py 故意讓 mock raise 以測降級路徑，
  `gui.web` / `gui.api` 的 `logger.exception` 卻把 traceback 寫進
  正式日誌。**修正**：兩測試檔加 autouse fixture 測試期間將
  `gui` / `gui.web` / `gui.api` logger 的 propagate 關閉。
* **telegram poll 10060（09:05）** — 暫時性網路逾時，既有
  「will retry」機制處理，非程式錯誤，無需修正。
* 其餘 WARNING（tool collision rename、surface trim 204→90、
  0.0.0.0 bind 提醒）皆為已知 by-design 行為。
* **營運提醒**：OpenRouter 帳戶餘額不足是帳務問題 — 需儲值或
  調整 OPENROUTER_CHAT_MODEL（failover 已自動掩護，但會以
  glm-5.3-flash:cloud 回答）。
* 測試 +8（`tests/test_nonretryable_failover.py`：402 單次嘗試即
  failover、401/402/403 矩陣跳過重試、429/500 保留兩次重試）；
  failover 12 + web_server 30 + diagrams_window 104 合併回歸
  **154 passed / 14 skipped**。
* pyproject 0.9.96.0 → **0.9.96.1**。

### v0.9.96.0 — 專案更名：Qdrant RAG Agent → **Divine Sense Agent**（user: 「更改 本專案名稱 由 Qdrant RAG Agnet 改為 Divine Sense Agent，連同所有 web/ui/cli介面進行修改更新」）

全面品牌更名，**web / GUI / CLI / stdio / 啟動器** 同步：

* **Web + GUI 前端**（`gui/assets/`）：`<title>`（順帶清除重複的兩行
  title）、頂欄品牌晶片 `appTitle`、啟動動畫 `splashTitle`、Admin tab
  標籤（zh「🛠 Qdrant-RAG 管理」→「🛠 系統管理」/ en「Admin」/
  ja「🛠 管理」，i18n ×4 的 `appTitle`/`splashTitle`/`tabAdmin`/
  `adminTitle` 全部更新）；diagrams.js 的 mxfile `host` 屬性；
  pywebview 視窗標題 + web banner + 啟動 log 行。
* **CLI**（`cli.py`）：REPL 標題列（`★ divine-sense-agent`）、歡迎
  BBS box、assistant header、所有 usage/tip 訊息、argparse description。
* **啟動器更名**（舊名保留為一行相容 shim，雙擊/捷徑照常運作）：
  `qdrant-agent.bat` → **`divine-sense-agent.bat`**、
  `qdrant-agent-gui.bat` → **`divine-sense-agent-gui.bat`**、
  `qdrant-agent-web.bat` → **`divine-sense-agent-web.bat`**；
  `gui.spec` + `build-gui.bat` 產出改為
  `dist\divine-sense-agent-gui\divine-sense-agent-gui.exe`。
* **stdio MCP servers**（外部客戶端可見的身分，19 個）：
  `Server("qdrant-rag-agent-*")` → `Server("divine-sense-agent-*")`；
  webhook `/healthz` 的 `server` 欄位同步。
* **HTTP 識別**：OpenRouter `X-OpenRouter-Title` header、
  web_search User-Agent（`compatible; divine-sense-agent`）、
  兆豐下單 order tag、netscan port 8334 標籤。
* **文件**：README / ARCHITECTURE 標題與現行章節、`.env.example`、
  `config/mcp.json.example`、`package-lock.json` name、pyproject
  `name = "divine-sense-agent"`。
* **刻意保留（資料相容性）**：`$QDRANT_RAG_HOME` 環境變數與
  `~/.qdrant-rag-agent/` 資料目錄（sessions / notes / schedules /
  credentials / embed_cache 全部原地不動）；uuid5 點位 ID 命名空間種子
  （stock-store / memory-store — 改了會讓既有 Qdrant 點位重複）；
  歷史更新日誌與使用者語錄原文。
* pyproject 0.9.95.0 → **0.9.96.0**。

### v0.9.88.0 — Google Keep 記事本 MCP（keep-local，mock-first）

以 MCP 方式整合**官方 Google Keep API v1**（`keep.googleapis.com/v1`），提供記事本功能。

* **方法面如實反映官方 API**：`notes.create` / `notes.get` / `notes.list`（filter 搜尋）/
  `notes.delete` — 官方 **沒有 update 方法**，故不提供「編輯」工具。
* **新核心** `src/keep_tools.py` — `KeepEngine`（hicloud 收斂模式）：
  * `KEEP_MODE=mock`（預設）= 完全模擬的記憶體記事本，**不連網、不動帳戶**；
  * `KEEP_MODE=live` = 官方 REST（requests + Bearer token），任何 HTTP 失敗回
    `{"error": ...}` 包絡不 raise；
  * OAuth：Cloud Console 啟用 Keep API → 桌面型 OAuth client secret（
    `KEEP_CLIENT_SECRET_PATH`）→ `python cli.py keep-auth` 一次性同意（需要
    `pip install google-auth-oauthlib`，僅此一次流程）→ token 存
    `KEEP_TOKEN_PATH`（預設 `$QDRANT_RAG_HOME/google_keep/token.json`），
    工具執行期只靠 requests + refresh_token 自動換新，**不需要** google-api-python-client。
* **新 LocalHost** `KeepLocalHost`（server `keep-local`，5 工具）：
  `keep_list_notes`（搜尋/列出）、`keep_create_note`（建立，需 `confirm=true`）、
  `keep_get_note`（讀取）、`keep_delete_note`（刪除，需 `confirm=true` — 不可逆）、
  `keep_status`（整合狀態）。寫入雙閘 = per-call confirm + live 另需
  `KEEP_WRITES_ENABLED=on`（hicloud 同款）。
* stdio mirror `src/mcp_server/keep_server.py`（外部客戶端同面）；
  `MCP_ACTIVE_SERVERS` 新增 `keep-local`；system prompt 新增記事本觸發區塊。
* 測試 `tests/test_keep_host.py` ×22（mock CRUD e2e、雙閘、auth resolver、
  host 表面 + schema required、manager 註冊、stdio parity、prompt 區塊）；
  `test_mcp_surface_mode.py` clean_env 補 pin `MCP_ACTIVE_SERVERS`（v0.9.81.1
  lazy-load_dotenv 老問題 — live .env 的 gate 漏進 cap 測試）。
* **注意**：官方 Keep API 以企業環境為主，個人帳戶可用性請以實測為準（mock 不受限）。
* **表面容量注意**：keep-local +5 工具後 live 全域 192 → `MCP_SURFACE_MAX_TOOLS=127`
  裁切尾端（cred/hicloud/log-analyzer/netscan/onvif/runtime/webhook）— 皆為
  `MCP_ACTIVE_SERVERS` 尾端低優先權 servers；keep-local 排前端不受影響。

`pyproject.toml` 0.9.87.4 → **0.9.88.0**。

### v0.9.88.1 — Keep 與 google-calendar 共用同一把 access token（user: 「使用跟 google 行事曆 相同的 access token」）

* **自動沿用 calendar 的 OAuth client**：`KEEP_CLIENT_SECRET_PATH` 未設時，
  從 `config/mcp.json` 的 `google-calendar` entry 讀
  `GOOGLE_OAUTH_CREDENTIALS`（退回 `google_oauth/client_secret_*.json` glob）。
* **共用 token 檔**：`KEEP_TOKEN_PATH` 未設時預設 = `<client secret 同目錄>/token.json`
  = `google_oauth/token.json` — keep 與 calendar MCP 讀寫**同一份** token。
* **一次 consent 涵蓋兩者**：`keep-auth` 的 scope = `keep` + `calendar` +
  `calendar.events`（若 shared token 已存在，其 scope 一併併入重新同意）。
* **googleapis 相容形**：`save_token` 寫 `{access_token, refresh_token, scope,
  token_type, expiry_date(ms), client_id, client_secret}`（node googleapis
  的 OAuth2Client 形）；`load_token` 同時吃自家形與 google-auth/gcloud 形
  （`token` 別名 + `expiry_date` ms）；refresh 的 client_id/secret 從 token 檔
  退回 client secret 檔（npm server 先刷新過的檔也能續用）。
* 實機驗證：secret 解析到 `google_oauth/client_secret_1042*.json`、token →
  `google_oauth/token.json`、consent scopes = keep+calendar+calendar.events。
* 測試 +7（`TestSharedCalendarToken`：路徑跟隨 calendar entry、explicit env
  優先、scopes 涵蓋/併入、googleapis 寫形讀形、fresh token 不觸發 refresh、
  creds 退回 secret 檔）；`_clean_env` fixture 補 `monkeypatch.chdir(tmp_path)`
  （隔離專案根的真 mcp.json/google_oauth）。29 passed。

`pyproject.toml` 0.9.88.0 → **0.9.88.1**。

### v0.9.88.2 — 設定落到家目錄 secret（config-only；user: 「協助設定 C:\Users\humphery\google_oauth\...json」）

* `config/mcp.json` google-calendar entry 的 `GOOGLE_OAUTH_CREDENTIALS`
  從專案相對路徑改為**家目錄絕對路徑**
  `C:/Users/humphery/google_oauth/client_secret_1042*.json` — stdio 子程序
  cwd 無關，且 KeepEngine 的自動解析（.88.1）跟著 mcp.json 走 →
  **keep 與 calendar 的共用 token 落在 `C:\Users\humphery\google_oauth\token.json`**。
* 兩份 secret（專案根 / 家目錄）`fc /b` 逐位元相同（同一把 client）。
* live `.env` 設 `KEEP_MODE=live` + `KEEP_WRITES_ENABLED=on`（token 未就緒前
  keep 工具回「先跑 keep-auth」提示包絡；consent 完成即通）。
* `google-auth-oauthlib` 1.4.1 已在 user site（`py` 3.14 import OK）。
* 實機驗證 resolver：secret/token 路徑與 scopes（keep+calendar+calendar.events）
  全部正確。

`pyproject.toml` 0.9.88.1 → **0.9.88.2**。

### v0.9.88.3 — keep-auth scope 模式（invalid_scope 診斷 + fallback；live consent 回 `Error 400: invalid_scope`）

* **根因**：Google 拒絕 consent 的 scope 組合 — client
  `1042148281919`（AI Studio 自建專案 `<YOUR_GOOGLE_CLOUD_PROJECT_ID>`）上
  **Google Keep API 未啟用**，或**個人帳戶不支援 Keep API**（官方 Keep
  API 以企業環境為主）。兩者都會在 consent 頁直接回 invalid_scope。
* **修復路徑**：到
  `https://console.cloud.google.com/apis/library/keep.googleapis.com`
  啟用 Google Keep API → 重跑 `python cli.py keep-auth`。
* **`keep-auth --scopes {combined,calendar,keep}`**（新參數）：
  * `combined`（預設）— keep+calendar 一次同意（原行為）；
  * `calendar` — **invalid_scope fallback**：只同意 calendar scopes，
    shared token 照樣落 `google_oauth/token.json`（行事曆可用，Keep 留
    mock，note 明示原因）；
  * `keep` — 只同意 keep scope（隔離診斷是哪個 scope 被拒）。
* 測試 +2（scope modes 矩陣 + mock flow 的 calendar/keep note）；
  `tests/test_keep_host.py` **31 passed**。

`pyproject.toml` 0.9.88.2 → **0.9.88.3**。

### v0.9.88.4 — 個人帳戶定論：Keep 官方 API enterprise-only（user: 「我是個人帳戶」）

* **確認**（googleapis 維護者在 google-api-dotnet-client #1930 的官方回覆）：
  Keep API 是 **enterprise-only** — 專案不屬於 Google Workspace 企業帳戶時，
  consent 端看不到 keep scope，`Error 400: invalid_scope` 是**預期行為**，
  個人帳戶無解（啟用 API 也拿不到 scope）。
* **scope 感知 guard**（`KeepEngine.has_keep_scope()`）：live 呼叫時若 shared
  token 缺少 keep scope（calendar-only token — 個人帳戶的現實），直接回
  乾淨的 why-note（enterprise-only + 留 mock + Workspace 解法），不對
  Google API 打無謂的 403。
* **live `.env` 回 `KEEP_MODE=mock`**（`KEEP_WRITES_ENABLED` 註解保留，
  可逆），.env 註明個人帳戶限制與 `--scopes calendar` 的行事曆共用路徑。
* 測試 +2（`TestKeepScopeGuard`：scope 判定 ×3 狀態 + calendar-only token
  的 live 呼叫回 why-note 而非打 API）；`tests/test_keep_host.py`
  **33 passed**。

`pyproject.toml` 0.9.88.3 → **0.9.88.4**。

### v0.9.89.0 — 自建本地筆記本（KEEP_MODE=local；user: 「以MCP方式，自建筆事本擴充功能」）

個人帳戶拿不到 keep scope（enterprise-only），所以**自建**：keep-local
新增第三種模式 — **本地持久化筆記本**，免 Google、免授權、離線可用。

* **`KEEP_MODE=local`**：同一組 5 個 keep-local 工具，改由 JSON 檔
  持久化儲存支撐：
  * 儲存位置 `$QDRANT_RAG_HOME/notes/notes.json`（`KEEP_NOTES_PATH` 可
    覆寫）；atomic 寫入（tmp + `os.replace`）；`threading.Lock` 保護
    read-modify-write（scheduler fire / 多來源回合併發安全）；
  * 筆記形狀與 Keep API 的 Note 相同（title / body.text / createTime /
    updateTime）— 未來若接上 Workspace 帳戶，資料形無縫；
  * `keep_list_notes` 關鍵字搜尋（標題+內文）+ 新到舊排序；
  * 寫入閘：每通 `confirm=true`（local 不需 live 總開關）；
  * `keep_status` 增 `notes_path` + `notes_count`。
* **live `.env` 設 `KEEP_MODE=local`** — 立即可用的自建筆記本。
* 測試 +7（`TestLocalNoteBook`：模式解析、**跨 engine 持久化**、
  confirm 閘（未 confirm 不落盤）、刪除往返、搜尋+新到舊、status 帶
  儲存資訊、免 OAuth）；`tests/test_keep_host.py` **40 passed**。

`pyproject.toml` 0.9.88.4 → **0.9.89.0**。

### v0.9.89.1 — google-calendar auth 落地（user: 「npm run auth → Missing script」）

* **根因**：`npm run auth` 是 `@cocal/google-calendar-mcp` repo 自己的
  npm script（`node build/auth-server.js`）— npm scripts 不跨包；本案是
  npx 安裝（無本地 repo），在專案根跑自然 Missing script。
* **repo 本地化**：clone + npm install（232 packages）+ build 到
  `C:\Users\humphery\coding\google-calendar-mcp` — `build/auth-server.js`
  就緒，`npm run auth` 在該目錄可用（瀏覽器 consent，ports 3500-3505，
  token 寫 `~/.config/google-calendar-mcp/tokens.json` 多帳號 vault）。
* **mcp.json 切本地 build**：google-calendar command 改
  `node C:/Users/humphery/coding/google-calendar-mcp/build/index.js`
  （擺脫 npx 下載依賴）；GOOGLE_OAUTH_CREDENTIALS 維持家目錄絕對路徑。
* **token 共用釐清**：@cocal 的 token store 是多帳號 vault
  （`GOOGLE_CALENDAR_MCP_TOKEN_PATH` / XDG / `~/.config`），與 keep 的
  單一 OAuth2 檔不同形 — 兩邊各自 consent（calendar 用 auth-server 或
  chat 內 `manage-accounts`；keep 用 `keep-auth --scopes calendar`），
  不強行共用檔案。
* **另一條零 clone 授權路**：chat 裡請 LLM 呼叫 `manage-accounts`
  （該工具永遠可用，npx 用戶的官方 auth 流程）。
* mcp.json 變更重啟生效；`node build/index.js` 啟動探測 OK。

`pyproject.toml` 0.9.89.0 → **0.9.89.1**。

### v0.9.90.0 — 日記/備忘錄/記事本 擴充：修改/分類/markdown/檢視（user: 「採MCP方式，製作日記/備忘錄/記事本擴充功能…支援新增、刪除、修改、查尋、分類…支援markdown和檢視圖」）

keep-local 從 5 工具擴到 **7 工具**，chat / telegram 同一表面可用：

* **`keep_update_note(note_id, title?, body?, category?, confirm)`** —
  修改標題/內文/分類擇一或全部（local 筆記本：lock 內 read-modify-write、
  updateTime 重蓋、跨 engine 持久化；mock 同步支援；live 如實回「官方
  Keep API 沒有 update 方法」）。寫入閘：每通 `confirm=true`。
* **`category` 分類欄位** — create/update 可帶分類、`keep_list_notes`
  支援 category 過濾、`keep_status` 回 `categories` 清單。官方 Keep
  API 無此欄位 → 僅 local/mock 支援（live 帶 category 回為什麼筆記）。
* **markdown 原樣保存** — body 就是 markdown 文字（粗體/清單/code）；
  `keep_get_note`/`keep_list_notes` 原樣回傳，chat/telegram 的 markdown
  渲染即所見即所得。
* **`keep_preview_note(note_id)`** — 檢視圖：把記事組成 markdown（標題 +
  內文 + 分類/時間 footer）**寫入 write sandbox 的 `notes/<id>-<slug>.md`**
  （atomic），回傳路徑 + markdown 全文 — chat 內可下載、LLM 用 fenced
  markdown 呈現即檢視圖。
* prompt 觸發區塊擴充（「日記」「備忘錄」「修改筆記」等關鍵字 + 7 工具
  面 + never-fabricate 條款）。
* 測試 +8（`TestNotebookFull`：update 往返+持久化、update 閘、找不到、
  分類過濾+status categories、markdown 原樣+note_markdown、live update
  誠實錯誤、host preview 寫 sandbox、host update/create handlers）；
  `tests/test_keep_host.py` **48 passed**。

`pyproject.toml` 0.9.89.1 → **0.9.90.0**。

### v0.9.91.0 — 行事曆動作完整性 guard（user log: 「沒有移成功」turn answered 1202 chars with ZERO calendar write dispatches）

* **診斷**：新 MCP 上的 fake calling 有兩態 — ①「建立+查證」回合是**真實
  派發**（create-event OK + list-events 複核，真 event id）；②「移動」
  回合模型**零派發**直接回 1202 字 — SupportCheck（p=0.66 < 0.7）與
  echo guard（模型連 echoed call 都沒寫）都擋不住。
* **`_calendar_action_guard`**（agent.py，掛 `_chat_turn_impl` 工具路徑尾端
  SupportCheck 後）：問句含行事曆名詞（行事曆/行程/活動/會議/日程/event/
  calendar）+ 動作動詞（移/搬/改/調整/刪/新增/建立/取消…）+ 非問句 +
  本回合**零 calendar 寫入派發**（create-event/create-events/update-event/
  delete-event/respond-to-event）→ **一次糾正回合**（工具開啟，directive
  教模型先 search-events 拿 id 再 update-event / delete-event /
  create-event，完成後必須回覆真實工具結果含 event id），糾正答案取代原
  答案；失敗 → 原答案 + 「⚠️ 未實際執行請重試」。
* 每回合至多一次（`_calendar_guard_ran` 於兩個 entry 重設）；任何失敗
  fail-open 保留原答案；`QDRANT_CALENDAR_GUARD` off 預設、live `.env` on。
* 測試 +10（`tests/test_calendar_guard.py`：intent 矩陣/問句跳過/已派發
  跳過/糾正答案取代+directive 帶原要求/失敗附加誠實注記/once-per-turn/
  env off）。

`pyproject.toml` 0.9.90.0 → **0.9.91.0**。

### v0.9.92.0 — web chat 延遲確認 + TurnTiming 階段計時 + 延遲改進（user: 「確認使用 web chat 從查詢到完成 為何每次都要花很多時間，並改進」）

* **確認**（`agent.py` `_chat_turn_impl` 工具路徑新增
  `[TurnTiming]` 階段計時 INFO 日誌 — recall+prompt / llm_tool_loop /
  citation / support / calendar_guard 逐段耗時 + dispatches 數）：
  實測「現在幾點？」= total 7.0s，其中 llm_tool_loop 5.63s
  （2 次 LLM 呼叫）、support 1.00s（shadow Jev 仍跑）、recall 0.35s。
  主因 = **127 工具的 prompt ≈ 44-48k tokens/呼叫 × 工具迴圈呼叫數**。
* **改進（.env，全部可逆）**：REASONING_EFFORT medium → low；
  QDRANT_SUPPORT_CHECK on → off（Jev RTT 省下）；QDRANT_REWRITE on →
  off（查詢改寫 Jev+LLM 省下）；QDRANT_TIME_ROUTE/QDRANT_ROUTE
  註解 → on（時間/問候/狀態/天氣類**免 LLM 直接回答**）；
  MCP_ACTIVE_SERVERS 移除 todo/archive/exec/openwith/scheduler
  （38 工具出表面）。
* 實測：prompt 48,386 → 43,982 tokens；cached 覆蓋率 52% → **98.7%**；
  wall 7.4s → **3.9s（−47%）**；support 1.0s → 0。
* 順帶：MCP_ACTIVE_SERVERS 的 google-calendar 切本地 build
  （npx → node <clone>/build/index.js，啟動不再依賴下載）。

`pyproject.toml` 0.9.91.0 → **0.9.92.0**（本次合併至 0.9.93.0）。

### v0.9.93.0 — Jev 分類器 MCP 核心（user: 「以MCP方式，製作核心擴充功能，使用JEV 模式製作分類器功能，輸入使用指定的json格式或json格式file，並支援批次作業」）

* **新核心** `src/jev_tools.py` — `JevClassifierEngine`：
  * 指定 JSON 輸入格式：`{"classes": [...或
    {"name","description"}], "items": [{"id"?, "text"}],
    "instructions"?, "default_class"?}`；
  * 批次：items 逐筆跑 jev_choice（ThreadPoolExecutor
    `QDRANT_JEV_CLASSIFY_WORKERS` 4；上限
    `QDRANT_JEV_CLASSIFY_BATCH_MAX` 50）；單筆失敗含在該筆 error；
  * 回傳每筆 choice + prob + **每類別統計 counts** + elapsed_s；
  * `JEV_MODE=mock`（預設 — 確定性 hash 挑選，不連網）| `live`
    （真實 Jev — 重用 OPENROUTER_API_KEY）。
* **新 LocalHost** `JevLocalHost`（server `jev-classifier-local`，3 工具）：
  `jev_classify`（payload_json inline 或 sandbox JSON `path`；
  write_result=true 寫結果 JSON 回 sandbox）、`jev_schema`（格式自查）、
  `jev_classify_status`。
* stdio mirror `src/mcp_server/jev_server.py`；MCP_ACTIVE_SERVERS +=
  `jev-classifier-local`；prompt 分類器觸發區塊；config 記錄 4 env。
* 測試 +16（`tests/test_jev_host.py`：mock 批次、格式驗證、file 模式
  + result 寫出、live jev_choice mock 注入、host e2e、manager 註冊、
  stdio parity、prompt 區塊）。
* **web chat 延遲實測（改進後）**：時間問句 wall 7.4s → **3.9s**；
  prompt 48.4k → 44.0k；cached 52% → **98.7%**。

`pyproject.toml` 0.9.92.0 → **0.9.93.0**。

### v0.9.95.0 — 筆記本 Web/手機 UI（user: 「keep local 提供WEB(含手機) /UI 介面，提供新增、刪除、修改、list(非同步，假設lists上億筆一次列會出事)、搜尋(日期、標題、內容)，提供markdwon所見即視和語法擴充」）

keep-local 的**本地筆記本**取得完整圖形介面（Web + pywebview GUI 同源共用、含手機 responsive）：

* **新引擎方法**（`src/keep_tools.py`）：
  * `local_search(query, category, date_from, date_to, offset, limit)` —
    **分頁 + 過濾瀏覽**（標題/內文關鍵字、分類、updateTime 日期區間），
    回傳 `total` + 當頁 notes，**永不整表傾倒**（假設可達上億筆：
    UI 逐頁非同步載入）；壞參數容忍（offset<0 / limit 超 clamp）。
  * `local_categories()` — 分類清單（name + count，依名稱排序）。
* **新 Api 方法**（`gui/api.py`，web shim 與 pywebview bridge 同源）：
  `notes_page`（分頁+過濾+categories）、`notes_get`、`notes_save`
  （note_id 空=建立/有=更新；UI 動作是使用者按鈕驅動，confirm 隱含
  true — MCP confirm 閘是 LLM 防護不是 UI 防護）、`notes_delete`、
  `notes_categories`。UI **永遠編輯本地 store**（`notes/notes.json`，
  與 LLM keep-local local 模式同一份，獨立於 KEEP_MODE）。
* **前端**（`gui/assets/`）：
  * 新 tab 📝 Notes（`panel-notes`）：工具列（關鍵字搜尋 300ms
    debounce、分類下拉、起迄日期、🔍/➕）、分頁清單（標題+分類+
    時間+markdown 摘要 160 字）、pager（第 N / M 頁 · 共 X 筆）。
  * **編輯器**：標題/分類（datalist 自既有分類）/內文 textarea +
    **語法擴充工具列**（B / I / H2 / 行內碼 / 圍欄 / 清單 / 引用 /
    連結 / 表格 — 插入游標處）+ **👁 預覽切換**（用 chat 同款
    `md()` 渲染 = GFM 所見即所得預覽，編輯中 300ms 即時刷新）。
  * 刪除有 confirm；手機 ≤480px 斷點自適應；i18n ×4（zh-TW / zh-CN /
    en / ja，`data-i18n-ph` placeholder 同步切換）。
* 測試 +13（`tests/test_notes_ui.py`：分頁 total/newest-first、關鍵字
  標題+內容、分類+日期過濾、壞參數容忍、categories 統計、Api CRUD
  往返、page 參數透傳、驗證錯誤、**共享 store 鐵律**（UI 寫入 =
  LLM 同一份 notes.json + lazy singleton）、前端 source guards
  ×4（tab/panel ids、app.js 函式+api 呼叫、i18n ×4 locale、CSS 類+
  斷點））；`tests/test_keep_host.py` 48 passed 零回歸。
* 順帶：README 更新日誌 v0.9.92.0 / v0.9.93.0 重複三份的舊帳清除
  （各保留一份）。

`pyproject.toml` 0.9.94.0 → **0.9.95.0**。

### v0.9.64.0 — hicloud HWS 雲服務 MCP 擴充（中華電信 CaaS/CVPC，mock-first + 簽章）

以 MCP 方式透過官方 REST API 操作 hicloud 雲服務（技術文件:
https://hws.hicloud.hinet.net/hws-doc/zh_TW/）。第 NINETEENTH 個
in-process LocalHost `HicloudLocalHost`（server `hicloud-local`，8 工具）
+ stdio mirror `src/mcp_server/hicloud_server.py`。

* **簽章（核心）** — `src/hicloud_tools.py::sign_params` 完整重現官方
  「Authentication of API Calling」演算法：參數按 key 自然排序 →
  `k=v` 以 `&` 連接 → 整串轉小寫（key+value）→ HMAC-SHA1(SecretKey) →
  Base64 → 特殊字元替換（`+`→`*`、`/`→`-`、`=`→刪除）。**以官方文件
  範例為測試向量，簽章逐字匹配**（`VBUfKTt48Wf6xbdny98N4Gi07f4`）。
  `build_signed_url` 組出帶簽章 URL（值 URL 編碼 `:`→`%3A`、endpoint
  保留 `api/hws/` 尾斜線、signature 原樣附加）。
* **工具面（8 個，`hicloud-local`）** — 查詢：`hicloud_list_instances`
  （describeInstances）/ `hicloud_describe_images`（describeImages）/
  `hicloud_list_instance_types`（運算規格合法值參考表，靜態節錄 + 完整
  表 API 連結）；生命週期：`hicloud_run_instances`（建立 VM — **會產生
  計費資源**，三種來源：公用範本 image_id+instance_type / 雲伺服器複製
  clone_instance_id / 用戶範本複製 BT*）、`hicloud_start/stop/reboot/
  terminate_instances`（terminate 不可逆）。
* **mock-first 安全（與 mega_stock 同構）** — `HICLOUD_MODE=mock`（預設）
  完全模擬：不連線 hws.hicloud.hinet.net、不送出任何真實 API 呼叫，
  VM 清單/範本/生命週期操作全在記憶體（`simulated: true`）。live 需要
  `HICLOUD_ACCESS_KEY` + `HICLOUD_SECRET_KEY`（hicloud 用戶平台取得）。
* **生命週期兩道閘** — (1) 每次操作 `confirm=true`（mock 亦強制 — 防意
  外操作演練）；(2) live 寫入另需 `HICLOUD_WRITES_ENABLED=on`（總開關，
  預設 off）。
* **接線** — `McpManager.from_config` 註冊（保留 `_hicloud_local_host`
  參考）、Agent system prompt「hicloud 雲伺服器 (v0.9.64.0+)」觸發區塊
  （8 工具全名 + 兩道閘 + 模擬資料警示；legacy prompt 不含）、
  `.env.example` HICLOUD 區塊（模式/閘/憑證/逾時）+ `MCP_ACTIVE_SERVERS`
  全服務清單加入 `hicloud-local`、`config/mcp.json.example` worked
  example。
* **stdio server** — `python -m src.mcp_server.hicloud_server`（外部
  MCP client 用；工具面與 host parity 鎖定）。
* **測試** — 新 `tests/test_hicloud_host.py` 44 tests（官方簽章測試
  向量、特殊字元替換、多值參數順序、URL 組裝、mock 引擎 CRUD、
  confirm/live-writes 閘、live HTTP monkeypatch（describe/statusMap/
  HTTP 錯誤包絡/非 JSON/逾時 clamp/expires 編碼）、host 8 工具面 +
  schema required、wrapper-dict coercion、from_config 註冊、stdio
  parity、system prompt 區塊/legacy 排除/回應語言組合）；合併回歸
  140 passed。

`pyproject.toml` 0.9.63.1 → **0.9.64.0**。
### v0.9.63.1 — 卡住的檔案回合診斷：LLM timeout 上限 + `read_file` 誤導性錯誤訊息修復

延續 v0.9.63.0（入站檔案接收）的 live log 2026-09-16 10:07-10:08：PDF 正常
接收、存檔、匯入 Obsidian vault，但 LLM 之後（10:08:33 `read_file` 回
錯誤）**整個回合停滯 8+ 分鐘無回覆**（app.log 之後零輸出、session 未存）。

* **修復 1 — LLM 呼叫無 timeout（卡住的根因）** — `Agent` 的
  `OpenAI(api_key, base_url)` 兩處（primary + failover 候選）都**沒設
  timeout** → openai SDK 預設 600 秒/嘗試 + 內建重試，巨大 prompt
  （`my_web__read_file` 把 4 MB PDF 的轉換 markdown 整份讀回）掛住單次
  create 呼叫 → telegram/scheduler/GUI turn 全部凍結。新
  `_llm_timeout_s()` + `QDRANT_LLM_TIMEOUT_S`（預設 **300 s**，clamp
  30-1200、無效值回落+警告；os.environ 直讀，monkeypatch-friendly）。
  timeout 觸發 → `APITimeoutError` → 既有重試 / 跨 provider failover
  優雅降級，不再無限掛住。
* **修復 2 — `read_file` 對不存在的路徑誤導** — 10:08:33 的
  `path 'C:/Users/humphery/科見M7單字表.md' is not a regular file.` —
  該路徑**根本不存在**（LLM 把 vault 轉換檔的路徑猜成 WRITE 根）。
  `_fs_resolve_path(must_be_file=…)` 拆分：**不存在 → `path not found:
  <路徑>`**（ValueError，所有 handler 既有捕捉不變）；存在但非常規檔
  （目錄/特殊檔）→ 原訊息不變。5 個既有斷言（describe/resize/
  add_document/diff/patch 的目錄案例）全數相容。
* **測試** — 新 `tests/test_llm_timeout_and_pathmsg.py`（resolver
  default/override/invalid/floor/clamp ×5、兩處 client 建構帶 timeout
  的 source guard、`path not found` vs `is not a regular file` vs
  `is not a directory` ×3、既有 missing-file 測試訊息相容）。

`pyproject.toml` 0.9.63.0 → **0.9.63.1**。

### v0.9.63.0 — Telegram 入站檔案接收修復（document/photo/video/audio → LLM）

使用者回報:「mcp telegram對於從 tg上傳的檔案沒有回應，在log沒有看到
收接的回應」— **根本原因**：`TelegramBotDaemon._handle_update` 只讀
`msg["text"]`；Telegram 的 document / photo / video / audio 訊息**沒有
text 欄位**，更新在 `if not text: return` 被**靜默忽略**（無 log、
無回覆、無 LLM 回合）。

* **入站檔案管線**（`src/agent/telegram_bot.py`）—
  * `_extract_file_payload(msg)` — 正規化 document / photo（從尺寸
    階梯取**最大**張）/ video / video_note / audio / animation →
    `{kind, file_id, file_name, mime_type, file_size, caption}`；
    純文字 / 貼圖 / 其他訊息回 None（不受影響）。
  * `_handle_file_message` — **INFO log 接收**（`file from chat …`，
    補上缺失的 log）+ 下載後 `file saved <path> (<n> bytes)`；
    失敗一律回「⚠️ 檔案接收失敗：<原因>」（永不再靜默丟棄）。
  * `_save_inbound_file` — `getFile` → 下載 → 存入 filesystem
    **WRITE 沙箱** `uploads/telegram/<chat_id>/`（檔名 basename 消毒
    防路徑穿越、同名自動 `-1` 去重、相對 root 錨定專案根）。
  * **安全閘**：>20 MB（Bot API getFile 上限）在 `getFile` **前**
    預檢拒絕；`.exe/.bat/.ps1` 等危險執行檔**拒收**（鏡像
    `_FS_DANGEROUS_WRITE_EXTS`，請使用者改 zip）；下載後再驗大小。
  * **組框**：`[Telegram 檔案上傳]` + 類型/檔名/MIME/大小/已存到
    （絕對路徑）/ 使用者附註（caption）/ 處理提示（照片 →
    `describe_image`；其他 → `read_file`）→ 完整 LLM 工具回合 →
    文字回覆（檔案訊息不走語音）。
* **護欄強化** — `_PHOTO_GUARD_TOOL_RE` 納入 `send_telegram_file`：
  >10 MB 快照以 document 送出是真投遞，不再誤觸發照片偽造糾正重試。
* **測試** — 新 `tests/test_telegram_file_inbound.py`（payload 正規化
  ×7、檔名消毒、存檔/去重/拒收/預檢/下載失敗、frame 組框 + log、
  `_handle_update` 接線（document/photo/純文字/空訊息）、
  `poll_once` 端到端、護欄強化、source guard）。

`pyproject.toml` 0.9.62.0 → **0.9.63.0**。

### v0.9.62.0 — Telegram 檔案上傳：`send_telegram_file`（沙箱檔案 → sendDocument）

使用者需求:「提供 mcp telegram 可以上傳檔案的功能」— 原有 Telegram
工具只能送文字 / 照片 / 語音，PDF、CSV、zip 等一般檔案無法投遞。

* **新 MCP 工具 `send_telegram_file(chat_id, path, caption?)`** —
  telegram stdio server 第 6 個工具：沙箱內**任意非執行檔**以
  multipart/form-data POST 到 Bot API `sendDocument`（純 stdlib
  urllib，重用 `_post_bot_multipart` + `_bot_post_retry`）。
  * **沙箱閘**（與 `send_telegram_photo` 同一聯集）— `path` 必須在
    import ∪ filesystem WRITE ∪ READ 根內；相對路徑錨定 WRITE 根；
    Desktop / OneDrive 檔案被拒（先複製進 write sandbox）。
  * **危險執行檔一律拒絕** — `.exe/.dll/.so/.dylib/.bat/.cmd/.sh/
    .ps1/.vbs/.js/.wsf/.scr/.com/.cpl`（鏡像
    `_FS_DANGEROUS_WRITE_EXTS` blocklist，stdio 內保持零依賴）；
    上傳通道絕不能變成執行檔外洩通道。
  * **≤50 MB**（`TELEGRAM_DOC_MAX_BYTES`，Bot API document cap），
    超限在 HTTP 前拒絕。
  * MIME 以 stdlib `mimetypes.guess_type` 判定（fallback
    `application/octet-stream`）；重用 v0.9.57.0 的暫時性錯誤重試
    （429 `retry_after` / 5xx 退避）+ `_upload_timeout` 大小縮放
    timeout（多 MB 上傳不再被固定 15 s timeout 殺掉）。
  * 輸出 `{ok, chat_id, message_id, file, file_name, mime_type,
    size_bytes, caption, sent_as}`。
* **system prompt** — Telegram 區塊新增一條：非圖片沙箱檔案
  （PDF 報告 / CSV / zip / .drawio）→ `send_telegram_file`；圖片仍走
  `send_telegram_photo`。
* **測試** — 新 `tests/test_telegram_file.py`（multipart framing
  含 filename/MIME/caption 截斷、相對路徑錨定、沙箱逃逸拒絕、
  危險副檔名拒絕、>50 MB 拒絕、file not found、allowlist fail-closed、
  handler 派發、source guard、prompt 接線 + legacy 排除）。

`pyproject.toml` 0.9.61.1 → **0.9.62.0**。

### v0.9.61.1 — MCP 設備編輯器「edit 沒反應」診斷 + 防禦強化

使用者回報:「MCP 設備(第三方 servers) 的 edit 不能用（點 ✏ 編輯按鈕沒反應、
編輯視窗沒彈出）」— **排查結論:編輯器程式本身正常**。三層驗證:
①**live web server round-trip**（get_mcp_config → save_mcp_config 同內容
重存）成功 — 5 servers 全連上、176 工具（含新 google-calendar）；
②**JSDOM 全流程重現**（真 app.js + 真 index.html + stub bridge）— Admin
分頁 → 5 個 server 列表渲染 → 點 ✏ → modal 開啟、表單正確預填（含
google-calendar 的 command/env）→ Save → modal 關閉，零 JS 錯誤；
③靜態檢查（modal DOM ids 無重複、CSS 佈局健全、served app.js == 磁碟、
`Cache-Control: no-cache`）。失敗為環境/分頁階層特異（最可能:舊分頁
記憶體中的舊 app.js，硬重新整理即可）。

* **全域 JS 錯誤 toast**（diagrams v0.9.5.5 教訓）— app.js 最頂端註冊
  `window.addEventListener("error"/"unhandledrejection")` → 任何 click
  handler 的靜默異常現在都會彈紅色 toast（此前「按鈕沒反應」零回饋）。
* **`.modal` z-index 1000 → 5000**（toast 10000 仍在最上層）排除堆疊遮蓋。
* **測試** — `tests/test_frontend_error_surface.py` 4 個 source guards。

### v0.9.61.0 — 掃描式 PDF OCR 攝入（Ollama/OpenRouter vision 辨識 + 歸檔匯入）

使用者需求:「針對掃描式PDF檔的攝入使用openrouter 和 ollama的OCR支援檔型
辨識內容並歸檔匯入」— 掃描式 PDF（圖像頁、無文字層）在 pypdf 下只抽得到
空文字，過去靜默攝入空白。

* **新模組 `src/ingest/pdf_ocr.py`** — ①`is_scanned_pdf()`（每一頁可抽文字
  < 20 字 → 掃描式判定；混排版不吃 OCR）②`extract_page_images()`（pypdf
  `page.images` 取每頁**最大內嵌影像**=掃描本體，Pillow 縮到 ≤1600px，
  `page-NNNN.png`；**零新依賴** — pypdf>=4 + Pillow 本來就是必備）。
  ③`ocr_scanned_pdf()` 逐頁經 **ImageDescriber vision 鏈**
  （`VISION_PROVIDER_CHAIN`: Ollama Cloud → OpenRouter → SiliconFlow →
  OpenAI-compat）以 `OCR_PROMPT` 逐字轉錄（max_tokens=2000）；fallback
  marker 與空轉錄跳過；無 vision provider 時**印提示跳過，永不崩潰**。
* **`ImageDescriber.describe(prompt=, max_tokens=)`** — prompt 可覆寫 +
  快取鍵納入 prompt（OCR 結果與描述結果永不互撞）；既有 describe 行為
  不變（回歸鎖定）。
* **loader `_load_pdf` 接線** — 掃描式判定成立 → 逐頁 OCR →
  `--- Page N (OCR) ---` 標記併入文件 → **正常 chunk → embed → upsert
  管道歸檔**（source = PDF 路徑、同 dedup/hash 流程）。
* **Env**: `QDRANT_INGEST_OCR`（預設 on）/ `QDRANT_PDF_OCR_MAX_PAGES`
  （預設 100，防 500 頁掃描；.env.example 已記錄）。
* **測試** — 新 `tests/test_pdf_ocr.py` 14 個（掃描判定/env/逐頁轉錄 +
  頁碼 + OCR prompt 傳遞、fallback marker 跳過、無 provider 跳過、loader
  三態、prompt 覆寫進 API、cache 鍵含 prompt）。
* **Live E2E** — PIL 渲染中文 → JPEG 手工內嵌成掃描式 PDF（無文字層）→
  真實 vision OCR **逐字辨識出兩句中文**「台積電今日收盤上漲百分之二／
  外資連續第五天買超金額約一百二十億元」→ `IngestPipeline().ingest_file`
  歸檔進 `ocr_e2e_test`（1 chunk, 4096-dim）→ 語意搜回 hit 0.670 ✓ →
  collection 清理。

### v0.9.60.4 — Search 分頁 collection 選單啟動即填充（直進 Search 不再空白）

使用者回報:「啟動web後直接換 搜尋 頁，collect list是空白的，要先選 統計 頁，
再回 搜尋頁才會出現 collect 內容可以選」— 根因:`refreshCollectionSelectors()`
只掛在 `refreshStats()`（Stats 分頁的載入路徑），直接進 Search 分頁從未填充。

* **三處修復**（`gui/assets/app.js`）:①`boot()` 啟動即呼叫
  `refreshCollectionSelectors()`（Search/Stats 兩個選單同時填充）;
  ②`switchTab('search')` 進 Search 分頁時再刷新（取得最新 collection
  清單）; ③`runSearch()` 防空自癒 — 選單無 options 時先
  `await refreshCollectionSelectors()` 再取值。
* **測試** — `tests/test_collection_browser.py` +1（三處接線的 source
  guard）; browser 套件 **27 passed**。

### v0.9.60.3 — 跨 collection 搜尋結果顯示（不再「#1 unknown」）

使用者回報:「chat 搜尋功能 除了 file_docs 正常顯示，其它collect 一律顯示 像
#1 unknown」— 根因:非 RAG collection 的 payload 沒有 `filename` 欄位
（memory_docs=content/kind/stored_at、log_docs=message/raw_line/level、
stock_docs=text/symbol/tool），`QdrantStore.search` 的結果映射寫死
`payload.get("filename")` → 全部變 "unknown"，且 `text` 空白。

* **`QdrantStore.search` 自適應映射** — 每個 hit 依 payload 形狀派生:
  顯示標籤 = filename → source → symbol → title（皆無 → `kind/tool
  #id前8碼`）；摘要 = text/message/content/raw_line/summary 首個非空；
  `meta` chips（kind / stored_at / ts / level / log_type / tool /
  symbol / session_id，巢狀 dict 不輸出）。
* **前端結果卡** — 新增 `.result-meta` meta 行（前 4 欄 · 分隔，
  textContent-only + CSS 截斷）；🗑 刪除鈕不變。
* **測試** — `tests/test_collection_browser.py` +6（memory / log /
  stock / file_docs 不變 / 空 payload 退化 id 標籤 / 前端 guard）；
  web + collections + browser **79 passed**。
* **Live 驗證**（真 Qdrant）:memory_docs → 標籤 `tgbot-111` + 內容 +
  `kind: chat · stored_at: 2026-09-15… · session_id: tgbot-111`；
  stock_docs → 標籤 `2330` + meta `ts/tool/symbol`；file_docs 行為不變。

### v0.9.60.2 — 百萬級資料管理瀏覽器強化（client 重用 + 頁碼指示）

使用者問:「資料管理（分頁瀏覽）在遇到百萬條 lists要如何處理」— 現行架構本身就是
百萬級安全的（Qdrant scroll 的 offset 是 **point-id 定位 seek，不是從頭掃描**，
每頁 O(page) 與所在位置無關；live 實測 167,931 點連翻 51 頁 latency 仍維持
5-50ms）。此版補兩個百萬級實用強化:

* **`Api._collection_store()`** — 非預設 collection 的 QdrantStore 以 dict
  重用快取（`collection_browse` + `delete_points` 共用），頁與頁之間不再重建
  HTTP client（原本每頁都重付一次連線建立）。
* **頁碼指示** — `#stats-data-pos` 改顯示「第 {p} 頁 · 本頁 {n} 筆」（頁碼 =
  offset stack 深度 + 1，i18n ×4），百萬級 ≈ 2 萬頁時使用者有位置感。
* 百萬級的設計邊界（文件化於 CLAUDE.md）:游標式分頁只支援 ⬆/⬇ 順序翻頁（無
  跳頁）；DOM 永遠只持有當前 50 列（innerHTML 清空重繪）；每頁 wire ≤ 50 ×
  截斷 payload（260 字/值，vectors 不出伺服器）；精確找點請用 Search 分頁。
* **測試** — `tests/test_collection_browser.py` +2（store 重用快取、頁碼指示
  接線）；web + collections + browser **73 passed**。Live: page1 23ms →
  page51 50ms（無深度退化）。

### v0.9.60.1 — Telegram 語音回覆 ≤50 字廣播式摘要（不再只唸 title）

使用者需求:「telegram 的語音雙向回覆，目前語音回覆太過簡短，應該把文字回覆內容
壓縮到 50 個字內的簡要說明以像一般的廣播新聞重點一樣，而不是只有說 title」— 排查
v0.9.59.0 的實作：system prompt 教模型「以 1-2 句口語摘要段落開頭」，而
`_condense_for_voice` 直接取第一段當語音版 → 模型只寫 title 式開頭，語音就只唸那個
title。

* **`_condense_for_voice` 重寫為 deterministic 壓縮器** — 從**全文**回覆壓縮：
  markdown 剝除（code fence 整段丟棄、標題/列表/編號標記去除、表格與 blockquote
  跳過、連結取 label、粗斜體還原、圖片移除）→ 語句切分（。！？；+ 換行）→ 貪婪
  打包進 `QDRANT_TELEGRAM_VOICE_MAX_CHARS`（預設 50，夾限 20-120）：優先句界、
  次選子句界（，、：；），最後硬切補「…」；開頭 filler（好的/當然/沒問題/了解/
  收到）在剩餘 ≥6 字時剝除。
* **system prompt 同步** — 移除「以口語摘要段落開頭」規則（title-only 的根源），
  改為告知語音版本是 bot 自動從全文產生的 ≤50 字新聞式摘要，答案正常撰寫、
  不可只回 title。
* **`.env.example`** — 新增 `QDRANT_TELEGRAM_VOICE_MAX_CHARS=50`（含說明）。
* **測試** — `tests/test_telegram_voice.py` +9（8 個 condense 單元 + prompt
  去除 title 規則的 guard + daemon 級長文重打包測試），telegram 全回歸
  **128 passed**。
* **Live E2E**：真實長答案（表格+標題+段落+code fence）→ 38 字摘要
  「已為您整理今日重點。台股收盤 台積電收盤上漲百分之二，來到 1,180 元。」→
  OpenRouter TTS 52.8KB mp3 → ffmpeg 29.9KB OggS（約 9-10 秒語音，廣播長度）。

### v0.9.60.0 — Search/Stats 跨 collection + 分頁資料管理（巨量資料批量顯示）

使用者需求:「1. chat 搜尋功能應該可以指定 collect 而不只有 file_docs 2. chat 統計功能
應該可以選其它 collect，並在巨量資料下支援搜尋後的資料管理，並在列表時使用批量顯示
而不一次全部 dump」— GUI 的 Search / Stats 分頁原本寫死在設定的
`QDRANT_COLLECTION`（file_docs），且 Stats 只能看不能管；167k 點的
file_docs 也沒有任何分頁瀏覽。

* **Search 分頁** — 工具列新增 collection 下拉選單
  （`list_collections` 填充，設定的 collection 標 ★ 排最前，保留
  使用者選擇）；`Api.search(query, top_k, filter, collection)` 可搜
  任意 collection（預設沿用快取的 Retriever；其它 collection 由
  `_get_retriever_for()` 建專屬 QdrantStore + Retriever，同一
  Embedder）。結果卡新增 🗑 刪除鈕（confirm 後
  `delete_points(collection, [point_id])`，成功後重跑搜尋刷新）。
* **Stats 分頁** — 新增 collection 下拉選單 + `get_stats(collection)`
  （非 RAG collection 跳過 filename group-by 的 docList，改以提示
  引導到分頁瀏覽器）+ **分頁資料管理瀏覽器**：`📂 載入資料` /
  ⬆ 上一頁 / ⬇ 下一頁（Qdrant 原生 scroll offset 分頁，一次只
  request 50 列，payload 長值截斷 + vectors 永不出伺服器；每列
  id / payload 預覽 / 🗑 刪除後重載當前頁）。
* **QdrantStore** — 新增 `browse_points(limit, offset)`（單頁
  scroll，limit 夾在 1-200，長字串/巢狀 payload 截斷，回傳
  `(rows, next_offset)`）與 `delete_points(ids)`（PointIdsList
  刪除 + 快取失效；空清單零 HTTP）。`Api` 新增
  `collection_browse(collection, page_size, offset)` 與
  `delete_points(collection, ids)`（破壞性操作，前端必須 confirm）。
* 修復 `get_stats` 的 `chunks=%d` logging 在 num_entities 缺失時
  的 TypeError（改 `%s`），並把 list_documents 失敗區塊的縮排
  回歸鎖進測試。
* **測試** — `tests/test_collection_browser.py` 18 個新測試
  （browse 分頁/截斷/夾限、delete by id、api 面路由/守衛、前端
  source guards、i18n ×4、CSS）；
  web + collections + browser 合計 **71 passed**。

### v0.9.59.2 — 長期記憶引用 deterministic 後處理（使用了記憶但沒標時間 → 自動補引用行）

使用者需求:「加一層 deterministic 的後處理」— v0.9.59.0 的「引用
記憶時註明取得的時間」是 prompt 軟性規則（模型偶爾不照做）:

* **`Agent._append_memory_citation(answer)`** — 每回合最終答案的
  deterministic 後處理（`_ask_impl` 工具/legacy 兩條路 +
  `_chat_turn_impl` 兩條路全部經過）:以 `_memory_phrases` 把本回合
  注入的記憶（`_last_memory_hits`，每回合入口重置）切成正規化
  短語（去空白/標點，≥6 字），答案正規化後任一短語命中 → 該記憶
  被視為「被使用」；答案若已含該記憶的取得日期 → 視為已引用
  （不重複）；其餘 → 在答案尾端附加
  「— 依長期記憶作答（取得的時間：`<ISO> (source=<sid>)`; …）」。
  覆蓋所有通道（GUI/CLI/telegram/scheduler）。
* Live 驗證（真 chat 回合，投資偏好問題）:模型本次自行內嵌了時間
  （「依 2026-09-15 02:59 的長期記憶」）→ 後處理正確保持沉默;
  模型未標時間的情境由 6 個單元測試鎖定（補行 / 已標時間不重複 /
  無關回答不動 / 無命中不動 / helpers / hits 記錄）。
* 測試:`tests/test_memory_store.py` 16 → **22 passed**。

### v0.9.59.1 — GUI Admin 分頁：列出所有 Qdrant collections 統計 + 每列刪除介面（像 file_docs 一樣）

使用者需求:「qdrant 統計 collections 需列出所有 collection 資訊和
刪除介面，像 file_docs 一樣」— Admin 分頁原本只顯示配置中的
`file_docs` 一個 collection:

* **`QdrantStore.list_all_collections()`** — `get_collections()` →
  逐一 `get_collection` 取 points_count / 向量維度（單向量
  VectorParams 與 named-vectors dict 兩種形狀都解析）/ status;
  每台失敗裝進該列的 `error`（清單其餘照常）。
* **`QdrantStore.delete_collection_by_name(name)`** — 依名稱刪除
  任何 collection（存在檢查 + 快取失效）; 空名/不存在 → False。
* **`gui/api.py`** — `list_collections()`（附 `kind` 標籤 rag/log/
  stock/memory/other + `configured` 旗標跟著 QDRANT_COLLECTION
  env 走）/ `delete_collection_by_name(name)`（破壞性, 前端必須
  confirm; 空名 → error）。
* **Admin 分頁 Collections 表格**（`admin-collections`）— 每列:
  Collection 名（配置中帶 ★）/ 向量數 / 維度 / 用途 / 🗑 刪除鈕
  （`confirm` 後逐列刪除 + toast + 自動刷新 Admin/Status/Stats）。
  純 `textContent` 建 table（collection 名無 XSS 面）。
  i18n ×4（zh-TW/zh-CN/en/ja）。
* 測試:新增 `tests/test_collections_ui.py` **9 tests**; GUI/web
  回歸 **92 passed**。Live E2E: 4 collections（file_docs 167,931
  points / log_docs 0 / memory_docs 1 / stock_docs 31, 全 4096-dim,
  kind/configured 標籤正確）。

### v0.9.59.0 — Qdrant 長期記憶（LLM 記憶 + 取得時間 life cycle）+ Telegram 語音雙通道投遞

使用者需求:①「telegram雙向語音對話，回覆除了語音要有文字，語音內容
簡要說明、文字可以詳細，並支援停用語音回覆」②「session內容和對外
查詢的資訊應存放於qdrant中作為LLM長期記憶，存入的資訊需標記取得時間
作為資料life cycle管理和引用」:

**② Qdrant 長期記憶（`memory_docs` collection）**
* **新模組 `src/agent/memory_store.py`** — `MemoryStore`（LogStore/
  StockStore 平行模式）: `remember` / `search` / `recent` /
  `forget` / `purge(older_than_s)` / `count`。每筆記憶 payload 帶
  **`stored_at`（ISO）+ `stored_at_epoch`（float）** — LLM 引用時
  必須註明取得的時間、life cycle 清理以它為鍵；deterministic
  uuid5 id（kind|source|content）→ 重複儲存 = upsert 不重複。
  向量走同一個 Embedder（auto 鏈 local llama.cpp 優先）。
* **三個入庫管道**:①`kind=chat` — `SessionManager.save()` 鉤
  （GUI/telegram/scheduler/webhook 所有 session 儲存路徑）自動存
  每回合最後一組 Q/A（錯誤/護欄假成功跳過; ②`kind=query` —
  `Agent._run_tool_call` 把白名單工具（`QDRANT_MEMORY_TOOLS` 預設
  `web_search,web_fetch`）的成功結果入庫; ③`kind=fact` — LLM 明確
  `remember_info`。
* **每回合召回** — `Agent._messages_with_user` 在 system prompt
  附加 `LONG-TERM MEMORY` 區塊（top-k 語意命中，每筆帶取得的
  時間; `QDRANT_MEMORY_TOP_K=5`）; prompt 區塊教導:引用記憶註明
  取得時間、「記住 X」→ remember_info、「之前/上次」→
  search_memory、life cycle → purge_memories。
* **新 MCP 工具組 `memory-local`**（MemoryLocalHost，5 工具:
  remember_info / search_memory / list_recent_memories /
  forget_memory / purge_memories）+ stdio 鏡像
  `src/mcp_server/memory_server.py`; manager 註冊 + Meta catalog。
  purge 拒絕無過濾清空（life cycle only）。
* Env: `QDRANT_MEMORY_COLLECTION` / `QDRANT_MEMORY_ENABLED`（預設
  on）/ `QDRANT_MEMORY_TOP_K` / `QDRANT_MEMORY_SESSION_INGEST` /
  `QDRANT_MEMORY_TOOLS`（config.py 記錄 + .env.example 區塊）。

**① Telegram 語音雙通道投遞**
* 語音進 → 回覆 **語音 + 文字都送**:語音 = 簡要口語版（prompt 教
  LLM 先寫 1-2 句口語摘要段落; daemon `_condense_for_voice`
  確定性取首段/句界截斷 → TTS → sendVoice）、文字 = 完整答案
  （sendMessage）。語音失敗 → 純文字（回覆永不丟失）。
* **停用語音回覆** — `QDRANT_TELEGRAM_VOICE_REPLY=off`（整體停用,
  STT 仍轉文字）; 單回臨時停用:語音內容說「改用文字回覆」→ 該回
  純文字。
* 測試:新增 `tests/test_memory_store.py` **16 tests** + 語音雙通道
  更新 4 + 新增 3（env off / 單回 opt-out / 摘要萃取）=
  telegram+memory+embedder+mcp_client+meta 合計 **218 passed**。

### v0.9.58.2 — Embedder auto 鏈重排：本地 llama.cpp（Qwen3）回排第一（openrouter 之前）

使用者需求:「將llama.cpp qwen3 embedding排在openrouter前」— v0.9.58.1
剛把 `C:\llama.cpp` 修回 GPU 健康（37/37 layers 在 5700 XT、實測單筆
p50 138.7 ms / 批次 44.7 s），本地伺服器重新變成免費且最快的選擇:

* **auto 順序 `local → openrouter → openai → hash`**
  （`Embedder._select_backend`）— `local` 在 v0.9.51.0 被移出 auto
  鏈（當時 Vulkan 管線重複退化），現在回排第一：probe 綠燈即選
  `local`（4096 維 Qwen3，零網路、零成本、全隱私）。
* **退化防護照舊** — 若 Vulkan 管線再退化（POST 500 / latency 爆
  表），v0.9.48.0 的 `EMBEDDER_BACKEND_COOLDOWN`（預設 300 s）自動
  把 local 跳過、同一個 embed 呼叫內落到 openrouter — 不需要人手
  改 .env（v0.9.51 移除時的痛點已被冷卻機制解掉）。`ollama` 維持
  explicit-only。
* **Live E2E**（真伺服器）: `EMBEDDING_BACKEND` 未設 → `auto →
  local`、`_last_backend=local`、dim 4096、單筆延遲 138-156 ms。
* 測試:`tests/test_embedder_resilience.py` 更新 4 個鏈語意測試 +
  新增 2 個（`test_auto_chain_prefers_llamacpp_first` — openrouter
  永不被 POST、`test_local_down_falls_through_to_openrouter` —
  probe 掛點自動換軌）; **26 passed**; 回歸 resilience + stock
  **45 passed**。

### v0.9.58.0 — Telegram 雙向語音溝通（OpenRouter STT/TTS + sendVoice / send_telegram_voice 工具）

使用者需求:「提供 telegram 雙向語音溝通」+「使用openrouter的tts/stt」—
全鏈語音進出，零新依賴（`openai` SDK + 既有 ffmpeg）:

* **OpenRouter 音訊端點**（2025-12+，live 驗證於 2026-09-15）:
  - STT `POST /api/v1/audio/transcriptions` — 預設
    `openai/gpt-4o-mini-transcribe`（whisper-large-v3 / Chirp /
    Voxtral 可換）; JSON/base64 或 OpenAI 式 multipart 上傳。
  - TTS `POST /api/v1/audio/speech` — 預設 `hexgrad/kokoro-82m`
    + voice `zf_xiaoxiao`（**中文女聲**，唯一一組第一試就吃 zh-TW
    文字的組合; Gemini TTS preview 只收 pcm、Deepgram voice 全
    EN/EU、Qwen/Voxtral 在測試中 400/404 — 皆可用 env 覆寫）。
  - OpenRouter TTS **voice 為必填**、`response_format` 只收
    `mp3`/`pcm` — 金鑰優先序 `TELEGRAM_STT/TTS_API_KEY` →
    `OPENROUTER_API_KEY` → `OPENAI_API_KEY`，base URL 預設跟著
    金鑰廠商走（`sk-or-` 前綴 → OpenRouter）。
* **新模組 `src/agent/speech.py`** — `SpeechClient`: `transcribe()`
  （Telegram 語音 OGG/Opus → **ffmpeg 轉 16kHz mono WAV** → STT;
  multipart **檔名副檔名必須與 payload 一致**，wav bytes 配 .ogg
  檔名會讓 provider 400）與 `synthesize()`（`response_format`
  mp3 → 400 時退 pcm/Gemini 路徑）+ `to_voice_ogg()`（mp3/pcm →
  OGG/Opus 轉碼）; `_find_ffmpeg()` 支援 winget Links shim（本機
  ffmpeg 不在部分 shell 的 PATH 上）。
* **Bot daemon 語音進出**（`src/agent/telegram_bot.py`）:
  `getUpdates` 收到 `voice` 訊息 → `getFile` → 下載 →
  `SpeechClient.transcribe` → 以 `「[語音訊息（已轉文字）]」` 前綴
  餵入 chat_turn → 回答 **TTS 成語音**（sendVoice）回傳; TTS 未
  設定/失敗 → 自動退回文字回覆（回覆永不丟失）。白名單 fail-closed
  照舊; **同意代碼（STK-XXXX）語音回覆也會被攔截**。
* **新 MCP 工具 `send_telegram_voice(chat_id, text, caption?)`** —
  telegram stdio server 第 5 個工具: OpenRouter TTS → ffmpeg 轉
  OGG/Opus → sendVoice; markdown 自動去除、輸出含
  `spoken_chars/audio_bytes/audio_format`。
* **Prompt 語音區塊** — 教 LLM: `[語音訊息（已轉文字）]` 前綴 =
  語音轉寫正常回答;「用語音回覆/唸給我聽」→ 呼叫
  `send_telegram_voice`（短句、口語化、≤2000 字）。
* `.env.example` 新增 `TELEGRAM_STT_*` / `TELEGRAM_TTS_*` 區塊
  （預設註解 — OpenRouter 金鑰存在即開箱可用）。
* **Live E2E**（真金鑰）: TTS「你好，這是語音雙向溝通的測試，今天
  天氣不錯。」→ 35KB mp3 → 20KB OggS → STT →「你好,這是語音雙向
  溝通的測試,今天天氣不錯。」round-trip 完整重現 ✓
* 測試:新增 `tests/test_telegram_voice.py` **25 tests**（env 解析
  ×5、helpers ×7 含 pcm fallback、daemon 語音流程 ×6、MCP 工具
  ×7、prompt ×2、source 接線）; telegram 全家桶回歸 **138 passed**。

### v0.9.57.1 — Telegram「抓畫面沒圖」根因修復：LLM 照片偽造偵測護欄（零工具呼叫 + 假成功宣稱 → 自動糾正重試）

使用者回報:「沒畫面」（00:03 任務 `抓五股大門camera畫面` 9 秒完成、
answer_chars=131、error=None，但 Telegram 收不到任何圖）。live 診斷
（app.log + session JSON + uploads/ 目錄）找到**真正的根因**：

* **LLM 間歇性跳過所有工具呼叫、偽造成功回覆** — 00:02:56→00:03:05
  之間**零** `[agent] tool` 紀錄；回覆宣稱「已發送 訊息 #219、檔案
  `uploads\maindoor_20260915_0902.jpg`」但該檔**不存在**（uploads/
  只有昨晚的三個舊檔）。對照 22:30-22:44 的五個「抓大門畫面」回合
  （8-32 秒、244-245 chars）**同樣全零工具呼叫** — 這就是「時收到
  時收不到」的主因：對話歷史裡累積了大量假成功模板，flash 模型
  自我強化地憑歷史編造「✅ 已發送」。**工具真的被呼叫時照片全都
  送達**（#200/#216 使用者都確認收到）。
* **修復 1 — prompt 反偽造硬規則**（`Agent._system_prompt` ONVIF 區塊）:
  「抓/看/show …畫面」等即時畫面請求**必須真的呼叫工具**
  （`onvif_get_snapshot` / `onvif_rtsp_snapshot` →
  `send_telegram_photo`），**絕對禁止**在零工具呼叫下宣稱
  已發送/已截取或回報 message_id/檔名；只能回報本回合工具輸出。
* **修復 2 — telegram bot 確定性護欄**
  （`src/agent/telegram_bot.py`）: `Agent` 新增
  `last_tool_dispatches`（每回合於 ask/chat_turn 入口重置、
  `_run_tool_call` 附加）；回合結束後若 **camera 請求 + 回覆宣稱
  已送圖（訊息 #N / message_id / 已發送…）+ 本回合沒有派發
  `send_telegram_photo`**（onvif 快照單獨出現不夠 — bot 只自動送
  文字，快照不等於送達）→ WARNING log + 追加一則糾正 user 訊息
  重試一次，改送糾正回合的回覆；原假回覆留存在 session
  （`hallucination_guarded: true` 旗標）供稽核；糾正回合失敗
  或空回覆 → 退回原回覆（永不丟失回覆）。
* 測試:新增 `tests/test_telegram_hallucination_guard.py`
  **20 tests**（偵測 regex ×7 含 live 偽造樣本、
  `last_tool_dispatches` 審計 ×4、handle 重試流程 ×5、prompt 規則
  ×3、source 接線 ×2）; telegram + agent 回歸 **126 passed**。

### v0.9.57.0 — Telegram 照片投遞可靠性修復（上傳 timeout 縮放 / 暫時性錯誤重試 / sendDocument 降級）

使用者需求:「送畫面到 telegram 有時可以收到有時收不到」— 診斷
`src/mcp_server/telegram_server.py` 找出三個間歇性投遞根因並修復:

* **固定 15 秒 timeout 打死大照片上傳** — multipart `sendPhoto`
  沿用文字訊息的 15 s `REQUEST_TIMEOUT_S`，數 MB 的 JPEG 在波動的
  上行頻寬下常規超時。新 `_upload_timeout(n_bytes)`:
  `max(30, 15 + n_bytes / 256KB)` → floor 30 s、10 MB ≈ 55 s。
* **暫時性錯誤零重試** — Telegram 429 flood / 5xx blip / DNS·TLS
  hiccup 直接失敗。新 `_bot_post_retry(req, timeout, attempts=3)`
  包住 `_bot_post`，文字（`_post_bot_method`）與照片
  (`_post_bot_multipart`) 兩條路都走:429 尊重 Telegram 的
  `retry_after`；其餘暫時性錯誤（5xx / 網路層 URLError）指數退避
  2 s / 4 s；永久性錯誤（400/401/403/404、`ok=False` 驗證）
  首試即拋出不重試。
* **>10 MB 照片直接拒絕** — sendDocument 明明允許 50 MB。
  現在 `_do_send_photo` 對超過 `TELEGRAM_PHOTO_MAX_BYTES` 的照片
  自動降級 sendDocument（`TELEGRAM_DOC_MAX_BYTES` 50 MB 以上才
  拒絕），輸出帶 `sent_as`（photo / document）讓 LLM 與呼叫端
  知道走了哪條路；工具描述同步更新（自動降級 + 自動重試）。
* 測試:新增 `tests/test_telegram_retry.py` **10 tests**（timeout
  縮放、暫時性分類 429/5xx/網路 vs 400、`retry_after` 解析、
  429 尊重 retry_after 後成功、502 指數退避、永久錯誤首試即拋、
  attempts 用盡）; `test_telegram_photo.py` 的 oversize 測試改鎖
  sendDocument 降級 + 新增 >50 MB 拒絕。telegram 三檔合計
  **93 passed**（retry 10 + photo/server 57 + bot 26）。

### v0.9.56.0 — ONVIF favorite_list 附實際網址（http_url / rtsp_url_guess / resolve=true 即時 RTSP 解析）

使用者需求:「MCP onvif 的 favorite camera 資訊加上實際網址（例如
http://.. 或 rtsp://.. 等）」:

* **靜態網址（零網路，每台必附）** — 新 helper
  `onvif_tools.device_urls(device)`:
  - `http_url` — ONVIF 裝置端點：優先用收藏的 `xaddr`（http(s) 時），
    否則推導 `http://<ip>:<port>/onvif/device_service`。
  - `rtsp_url_guess` — 慣用 `rtsp://<ip>:554/`（vendor 路徑各異，
    標明為 guess）。
* **`resolve=true` 即時解析** — `onvif_favorite_list` 新參數：對每台
  做 SOAP 巡迴（重用 `get_profiles_anywhere` 媒體端點 fallback 鏈 →
  `get_stream_uri`），附**真正的** `rtsp_url`（帳密已內嵌,
  `_embed_rtsp_creds`）、`rtsp_variants`（standard/hikvision/dahua/
  xmeye）、`media_xaddr`、`profile_count`、`snapshot_uri`（best-
  effort）。解析 timeout 上限 5 秒（`QDRANT_ONVIF_HTTP_TIMEOUT` 與
  5 取小）— 離線攝影機不會掛住清單；每台失敗裝在
  `resolve_error`（清單其餘照常）。預設 `resolve=false`（清單維持
  快速、離線安全）。
* **stdio 鏡像自動繼承** — `onvif_server._build_tool_specs` 由
  host `list_tools()` 建構（schema 變更自動到達外部 client）。
* 測試 `tests/test_onvif_favorite_urls.py` **7 passed**（靜態網址、
  xaddr 優先、resolved 旗標、resolve=true 實際 RTSP+帳密+variants、
  離線相機 contained、多相機隔離、helper 純函數）; onvif 回歸
  （onvif_host + rtsp_snapshot）**80 passed**。

### v0.9.55.0 — GUI：移除 Admin 分頁（Qdrant-RAG 管理）的 LLM 方塊

使用者需求:「將 Qdrant-RAG 管理 的 LLM 方塊移除，因為沒有用處」—
該卡片只 echo `get_status` 的 `llm_provider` / `llm_model`（footer
狀態列本來就會顯示同樣資訊）:

* **移除**: index.html 的 `admin-card`（`#admin-llm` +
  `data-i18n="llmInfo"`）、app.js `refreshAdmin()` 的
  `fillDl('admin-llm', {...})`、4 個語系的 `llmInfo` i18n key
  （adminHint 文字同步移除 LLM provider 字樣）。
* **保留**: `get_status` 的 `llm_provider` / `llm_model` /
  `ollama_key_set` / `openai_key_set` 欄位（footer 狀態列消費）與
  Qdrant / Embedding 兩張卡片。
* 新測試 `tests/test_admin_llm_box_removed.py` ×4（反向守衛:
  index.html 無卡片、app.js 無 fill、API 欄位仍在、node --check）。

### v0.9.54.0 — OpenRouter web search / fetch 做為 Ollama web search 的備援

使用者需求:「使用 openrouter 的 web search 和 web fetch 做為 ollama
的備援」。OpenRouter 的 web search 是 **chat-completions 的 web
plugin**（無獨立搜尋端點）: `plugins: [{"id": "web",
"max_results": N}]` 跑 Exa 搜尋,結果以 **`url_citation` annotations**
（title / url / 2-4k 字摘錄）回傳;**沒有 web-fetch 對應端點**:

* **實測（2026-09-14,live key）**: `meta-llama/llama-3.1-8b-instruct`
  + web plugin → 200、3 個 url_citation（Qdrant GitHub releases 等）、
  ~$0.007（Exa auto）+ 可忽略的 model token。模型的文字回答被捨棄 —
  只取 citations,故用最便宜的可用模型（`OPENROUTER_WEB_MODEL` 可換,
  預設 `meta-llama/llama-3.1-8b-instruct`）。
* **`src/tools/web_search.py` 新增**:
  - `OpenRouterWebSearch` — web plugin 客戶端: annotations 解析成
    `WebResult`（URL 去重）、與 `OllamaWebSearch` 相同的優雅契約
    （失敗回 `[]`/`None`）;`fetch()` 降級為 best-effort 直接
    HTTP GET + 零依賴 HTML 剝離（title/text/links）。
  - `WebSearchWithFallback`（繼承 `OllamaWebSearch`,介面完全不變）—
    Ollama 先行;**例外或查無結果**且有 `OPENROUTER_API_KEY` 時改用
    OpenRouter web plugin（查無結果也算「沒找到」,備援擴大涵蓋;
    ~$0.007/次）;`fetch` 失敗改試直接抓取;`last_backend` 記錄實際
    服務者（ollama/openrouter）;`WEB_SEARCH_FALLBACK=off` 可關閉
    （回到 0.9.53 行為）。
  - `build_web_search()` 工廠 — `Agent` 與 `WebSearchLocalHost` 改用
    （兩處建構點,介面零變更,`OllamaWebSearch` 原類別完全保留）。
* live E2E: 無效 ollama key 強制備援 → `fallback backend:
  openrouter`、3 個真實搜尋結果。
* 測試 `tests/test_web_search_fallback.py` **13 passed**（annotations
  解析/去重/無 key/HTTP 錯誤、鏈:成功不觸發/失敗降級/空結果降級/
  雙失敗/無 key/off 開關/is_configured/fetch 降級/工廠）。

### v0.9.53.0 — system prompt search-first 紀律：qdrant-local 為資料/知識問題的第一順位

使用者問題:「查詢資料為何 qdrant-local 不是第一順位被查詢的」。原因
與修復:

* **原因** — 舊規則「Call search_documents first when the user asks
  a question that **may be answered from** their local knowledge
  base」是**條件式**:由 LLM 自行判斷「這題會不會在本地語料」—
  股價/時事/常識類問題被模型自行跳過（去 stock 工具、web_search 或
  憑記憶回答）。tool 模式（GUI chat 預設）下 LLM 就是路由者,prompt
  裡規則的排列順序不影響選擇,是規則「內容」在影響。
* **修復** — 規則改寫為強制 search-first 紀律:
  - 任何資料/知識/研究問題（「查詢」「資料」「幫我找」 'what is X'、
    'find X'）→ **先呼叫 `search_documents`** — 先於憑記憶回答、先於
    `web_search`、先於任何其他工具。
  - 明確例外（保持規則可預測）: 純 meta 查詢（get_current_time、
    chat-history、任務管理、工具目錄）與專屬 live-data 觸發
    （stock 報價/下單、攝影機/桌面動作）。
  - 查無相關結果時必須明說,再接 web_search 或領域工具 —
    **絕不默默用記憶替代本地語料查核**。
* 測試 `tests/test_system_prompt_search_first.py` ×5 passed（規則
  存在、順序約束、例外清單、legacy 排除、response_language 共存）。

### v0.9.52.0 — OpenRouter embeddings 的 provider 路由（nebius → deepinfra）

使用者需求:「openrouter qwen3 embedding provider 使用順位為 nebius、
deepinfra」。OpenRouter 的 **embeddings 端點支援與 chat 相同的
provider 物件**（文件:`provider object | null — Provider routing
preferences for the request`）:

* **實測確認（2026-09-14,live key）**:
  - `order=[nebius,deepinfra] + allow_fallbacks=false` → **200**
    （1,419ms）
  - `order=[deepinfra] + fallbacks=false` → **200**（817ms）且回應帶
    **`"provider": "DeepInfra"`** — 路由生效且可從回應診斷實際服務者。
* **Embedder 實作**:
  - 新 env `OPENROUTER_EMBED_PROVIDER_ORDER`（逗號清單 = 順位;未設定
    = 不帶 provider 物件,零行為變更）+
    `OPENROUTER_EMBED_ALLOW_FALLBACKS`（預設 true;false = 只用清單內
    provider）。boolean 解析容錯（true/1/yes|false/0/no,壞值回落
    預設只警告）。
  - single（`_embed_uncached`）與 batch（`_embed_batch_openrouter`）
    兩條路徑都合併 `{"provider": {...}}` fragment。
  - 回應的 `provider` 欄位記入 log（`served by <provider>`）— 可直接
    看到實際服務者。
  - 冷卻語意不變: OpenRouter 內部路由失敗（5xx/429）→ 整個
    openrouter backend 進熔斷 → 落到鏈上下一位（openai → hash）。
* **live .env 啟用**: `OPENROUTER_EMBED_PROVIDER_ORDER=nebius,deepinfra`。
* **Live E2E**: single 與 batch 都記到 `served by Nebius`。
* 測試 `tests/test_embedder_resilience.py` **25 passed**（+7: 無 order
  不帶 provider 鍵、order 到達請求 body、allow_fallbacks 解析、batch
  帶路由、served-by log、slug 解析邊界）。

### v0.9.51.0 — llama.cpp 移出 Embedder auto 鏈，OpenRouter 為第一順位（含雙 backend 效能實測）

前接 v0.9.49.0 的效能實測 + 使用者需求:「移除 llama.cpp，將
qwen3-embedding 改為 openrouter 為第一順位」:

* **效能實測（2026-09-14,同為 Qwen3-Embedding-8B 4096 維）**:
  | 情境 | llama.cpp (GPU 健康時) | llama.cpp (劣化後) | OpenRouter |
  |---|---|---|---|
  | single ~12 字 | 442 ms | — | 1,096 ms |
  | single ~190 字 | 325 ms | — | 1,091 ms |
  | single 780 字 chunk | 334 ms | **13,259 ms** | 1,452 ms |
  | batch16 chunks | >30 s | — | **5.43 s (339 ms/item)** |
  | batch16 費用 | 免費 | 免費 | **$0.000115**（11,476 tok） |
  跨 backend 餘弦比對因 llama.cpp 再次劣化無法完成（本身即是發現）。
* **決定性觀察**: RX 5700 XT 的 Vulkan 管線在重啟後 ~40 分鐘內再次
  劣化 40 倍（334ms → 13.3s）— 本機 GPU 穩定性是持續性問題，
  OpenRouter（~1.1-1.5s/筆、batch 吞吐 339ms/item、每 100 萬 token
  ~$0.01）成為唯一穩定且語意正確的選擇。
* **`_select_backend()` auto 順序改為 openrouter → openai → hash**
  — auto 模式完全不探測 llama.cpp、完全不 POST（llama.cpp probe
  連 HTTP 都不發）。`local` / `ollama` legs 均為 explicit-only
  （`EMBEDDING_BACKEND=local` / `ollama` 仍完整可用，含冷卻與
  per-collection 切換）。
* 測試 `tests/test_embedder_resilience.py` **18 passed**（更新 6 個
  為新鏈語意 + 新增 `test_auto_chain_prefers_openrouter_first` —
  即使 llama.cpp probe 綠燈，auto 模式零 llama.cpp HTTP）;
  回歸 loopback + stock **42 passed**。Live E2E:
  `auto backend: openrouter dim: 4096`。

### v0.9.50.0 — Embedder auto 鏈移除 ollama（local → openrouter → openai → hash）

使用者需求:「將 ollama 從 auto 鏈順序移除」。ollama.com 的 embeddings
端點在這台機器一直壞（`/v1/embeddings` 404、原生 `/api/embed` 401），
每次 auto embed 都白付一次註定失敗的嘗試（v0.9.48.0 的熔斷只把它
壓到每 300 秒一次）:

* **`_select_backend()` auto 順序改為 local → openrouter → openai →
  hash** — 不再探測 ollama、不再嘗試 ollama POST。
* **`_embed_uncached` 的 ollama leg 改為 explicit-only** — 只有
  `EMBEDDING_BACKEND=ollama` 明確指定時才會執行（不再從 auto/local
  失敗 fall-through 進來）。per-collection 切換（CLAUDE.md §7 的
  ollama 用法）不受影響。
* `backend_info()` 補 `openrouter_url` / `openrouter_model` /
  `openrouter_available` 診斷欄位。
* 測試 `tests/test_embedder_resilience.py` **18 passed**（+2: auto 鏈
  完全不碰 ollama — 即使 probe 綠燈；explicit ollama 仍可用。既有
  5 個測試更新為新鏈語意）;回歸 loopback + stock **41 passed**。

### v0.9.49.0 — OpenRouter embeddings backend（qwen/qwen3-embedding-8b，4096 維）

使用者問題:「是否可以使用 openrouter qwen/qwen3-embedding-8b 做為
embedding 替代」。**可以** — 實測驗證 + 正式 backend:

* **實測（2026-09-14,使用 live OPENROUTER_API_KEY）**:
  `POST https://openrouter.ai/api/v1/embeddings` +
  `{"model": "qwen/qwen3-embedding-8b"}` → **200、原生維度 4096、
  5 tokens ≈ $0.00000005**。4096 維 = 本機 llama.cpp 同模型的維度 —
  向量可直接互換（同模型同語意空間），`EMBEDDING_DIM=4096` 零調整。
* **Embedder 新 backend `openrouter`**（`src/ingest/embedder.py`）:
  - auto 鏈順序: local → ollama → **openrouter** → openai → hash;
    也可 `EMBEDDING_BACKEND=openrouter` 指定。
  - 重用 `OPENROUTER_API_KEY`;模型經新 env `OPENROUTER_EMBED_MODEL`
    （預設 `qwen/qwen3-embedding-8b`,零設定即啟用）。
  - 探測: `GET {base}/models`（快取）;**無 key 一律 False、零 HTTP**。
  - 冷卻整合（v0.9.48.0 熔斷）+ batch 路徑 `_embed_batch_openrouter`
    （OpenAI 形狀 `data[N].embedding`）+ disk cache 依
    `(openrouter, model)` 鍵值。
  - **修一個真 bug**: explicit backend 時 leg 條件
    （`backend == "openrouter"` 短路）會繞過冷卻 — local / ollama /
    openrouter / openai 四個 leg 現在都先檢查
    `_in_cooldown(...)` 再 POST,冷卻在 explicit 模式同樣生效。
* `.env` + `.env.example` 記錄 `OPENROUTER_EMBED_MODEL`。
* 測試 `tests/test_embedder_resilience.py` 15 passed（+6: 無 key 不
  探測、explicit 成功與 disk 鍵值、auto 鏈 fall-through 到
  openrouter、冷卻跳過、batch dispatch、model 對應）;fixture 加固:
  預先 import `src.config` 防 dotenv 重注入 + 清掉 provider keys。
  回歸 loopback + stock + context_compress **77 passed**。live E2E:
  `backend=openrouter dim=4096`。

### v0.9.48.0 — hotfix：Embedder 熔斷冷卻 + Ollama 原生端點 + 降級向量快取污染修復（live log 2026-09-14）

使用者貼出 live log（排程「積極型股票追蹤-交易日收盤」fire 全程 108 秒、
task registry 登記正常、工具鏈 + Telegram 都成功），但每個 stock 工具
呼叫都伴隨兩行 Embedder 失敗。診斷與修復:

* **根因（live 探測確認）**: llama.cpp 的 Vulkan 管線進入
  ``vk::Queue::submit: ErrorDeviceLost`` — **程序活著、/v1/models
  回 200（availability probe 一直綠燈），但每次 /v1/embeddings POST
  都 500**。auto 鏈接著打 https://ollama.com/v1/embeddings → 404
  （ollama.com 雲端根本不供應該路徑）。結果:每個 stock 快照存檔白付
  兩次失敗 HTTP 往返 + 兩行 log，最終落入 hash fallback = **語意無效
  向量**。
* **Ops 修復**: 重啟 llama.cpp（schtasks 分離啟動
  `start-llamacpp.bat`，繞過 timeout 的 process-tree 清理）→
  embeddings 恢復 200（4096-dim 實測）。
* **程式修復（`src/ingest/embedder.py`）**:
  1. **backend 熔斷冷卻** — embeddings 硬失敗的 backend 跳過
     `EMBEDDER_BACKEND_COOLDOWN`（預設 300s；0 = 0.9.47 每次重試
     行為）；availability probe 在冷卻期間直接回 False（零 HTTP）；
     成功立即清冷卻。壞值回落預設只警告。print → logger.warning。
  2. **Ollama 原生端點 fallback** — `/embeddings` 404 時自動改試
     `{server}/api/embed`（相同 `{"embeddings": [[...]]}` 回應形狀）；
     傳輸層錯誤不重試。
  3. **降級向量快取污染修復** — `_embed_uncached` 記錄**實際**
     產出向量的 backend（`_last_backend`），disk cache 依它鍵值 —
     舊碼把 hash 向量存在 `(local, llama.cpp)` 鍵下，backend 恢復後
     仍會被命中（污染）。新鍵值規則鎖定測試。
* **資料修復**: 清除故障期間（≥ 2026-09-12）被污染的 disk cache 列
  10 筆（`backend='local'` 且 hash 向量）；故障期間寫入 stock_docs
  的 hash 向量點會在下次查詢同標的時以 uuid5 id 覆寫自癒。
* `.env` + `.env.example` + `src/config.py` 記錄
  `EMBEDDER_BACKEND_COOLDOWN`（預設註解 = 啟用 300s）。
* 新測試 `tests/test_embedder_resilience.py` ×9（冷卻跳過/探測/到期
  重試/成功清除/resolver、Ollama 原生端點 404 重試與 500 不重試、
  hash 向量鍵值正確 + 正常路徑鍵值不變）。

### v0.9.47.0 — 平行多功 Phase 3 GUI：⏳ 背景任務面板 + 「背景執行」送出勾選

* **Chat 工具列** 新增 `⏳ 任務` 按鈕 + 下拉面板（鏡像 🧰 工具選擇器
  模式）— 開啟時每 2 秒自動重新整理（in-memory registry 讀取，零成本）；
  列出 running + 最近任務（狀態點顏色 / 標題 / 來源 / sid / 時間 /
  answer_chars / 錯誤預覽），badge 顯示執行中數量：
  - badge 的即時來源 1：30 秒一次的 `get_status` 輪詢新增
    `running_tasks` 欄位 → `refreshStatus()` 上色。
  - badge 的即時來源 2：面板每次重新整理 + 送出任務後。
* **Chat 輸入區** 新增「背景執行」勾選（`chat-bg-toggle`，與 Web
  Search 同款 checkbox）：勾選後送出的訊息改走
  `submit_background_task` — 立刻回傳 task_id、聊天不卡住，chat 泡泡
  顯示「已送出背景任務 {id}（session: {sid}）」並入檔（v0.9.20.0
  即時儲存鏈完整沿用）。
* **gui/api.py 三個新橋接方法** — `list_background_tasks(status,
  limit)` / `get_background_task(task_id, preview_chars)` /
  `submit_background_task(title, prompt, use_web_search)`（重用
  Phase 2 的 `TaskLocalHost._run_once`，背景 Agent 共用主 Agent 的
  McpManager）。web 模式 shim 自動暴露（stdlib 伺服器 dispatch 公開
  方法，無需改 web_server.py）。
* **style.css** 面板樣式（狀態點:running 琥珀 / done 綠 / failed 紅）。
* **i18n ×4**（zh-TW / zh-CN / en / ja）：chatTasks / taskRefresh /
  taskNone / taskRunning / taskDone / taskFailed / bgRun /
  taskSubmitted。
* 新測試 `tests/test_gui_task_panel.py`（三個橋接方法 ×6、get_status
  running_tasks ×2、前端 source guards ×10 — 按鈕/面板/勾選/分支/
  badge/i18n 四語系/CSS）。

### v0.9.46.0 — 平行多功 Phase 2 任務框架：task-local MCP（run_task_once / list_tasks / get_task_result）+ 背景任務註冊表

Phase 1 補了安全網；本版補上「讓 LLM 與使用者看得見、用得了」的平行
多功核心:

* **`src/agent/task_runner.py`（新模組）** — 程序級背景任務註冊表:
  `begin_task(source, title, sid)` / `attach_sid` / `end_task(status,
  answer_chars, error)` / `list_tasks(status, limit)` / `get_task`
  （threading.Lock 保護、純 dict 記錄、finished 上限 100 自動裁撤、
  記憶體內 — 重啟清空但 session 檔案留存）。**零 env vars**。併發
  限制不在這裡 — 背景輪照樣過 Phase 1 的 `turn_slot()`。
* **第 18 個 in-process LocalHost `TaskLocalHost`**（server
  `task-local`，host 收到 **同一個 McpManager** — 背景 Agent 共用工具
  艦隊，不再每任務重建 ~15s manager）:
  - `run_task_once(title, prompt, use_web_search?)` — 送一輪 LLM 到
    背景執行緒，**立刻**回傳 `task_id` + `sid`（= `task-<task_id>`）；
    對話持久化到專屬 session（Sessions 分頁 / `get_chat_session` 可讀）。
  - `list_tasks(status?, limit?)` — 所有來源（task / scheduler /
    webhook / telegram）的 running + 最近任務與 live 計數。
  - `get_task_result(task_id)` — 狀態 + sid + session 內最後一則
    assistant 訊息（上限 800 字 preview，可調）。
* **三個既有 fire 進入點自動登記** — `scheduler.make_agent_on_fire` /
  `webhook.make_webhook_fire` / `telegram.make_telegram_on_message`
  在各自的 chat_turn 前後 begin/end 登記（失敗 stamp failed；登記
  失敗永遠被吞掉，不影響 fire 路徑）。
* **McpManager.from_config** 在 Meta 之前註冊（保留
  `_task_local_host` 參考）；stdio 鏡像 `src/mcp_server/task_server.py`
  （`python -m src.mcp_server.task_server` — 注意：獨立程序有自己的
  registry，agent chat 請用 in-process 面）。
* **System prompt** tool-selection rules 新增「Background tasks」
  觸發區塊（「背景執行」「在背景跑」「不要等我」 / 'run in the
  background' → run_task_once；回覆時只回報 task_id、絕不宣稱結果
  已就緒、絕不編造結果）。
* **Gate 清單**: live `.env` + `.env.example` 的
  `MCP_ACTIVE_SERVERS` 加入 `task-local`（v0.9.13.2 發現陷阱合約）。
* 新測試 `tests/test_task_runner.py`（registry ×10、host 工具面/
  提交/清單/結果 ×9、manager 接線 ×2、stdio parity、prompt 區塊 ×2、
  scheduler/webhook/telegram fire 登記 ×3）。

### v0.9.45.0 — 平行多功 Phase 1 安全網：MCP stdio/HTTP client 併發鎖 + 對話輪併發上限

使用者問題:「以 session 為基礎，支援平行多功運作是否可行」。分析結論:
可行（既有基礎:sched-/webhook-/tgbot- 專屬 sid、每個背景 fire 一個
fresh Agent 共用 McpManager、ThreadingHTTPServer 併發入口全部就緒），
但有兩個破口 — 本版補上:

* **MCP client 事件迴圈併發鎖** — `StdioMcpClient` 與
  `HttpSseMcpClient` 的 `call_tool` / `list_tools` / `connect` /
  `close` 都用同一個 asyncio loop 的 `run_until_complete`，兩個執行緒
  同時呼叫會撞 `RuntimeError: This event loop is already running`
  （GUI 聊天與排程 fire 同時打 stdio 工具的實際場景）。新
  `_serialized` decorator（`stdio.py` 定義、`http_sse.py` 共用）用
  per-client `RLock` 串行化 — RLock 讓 connect 失敗路徑的 close()
  同執行緒重入不會自鎖。in-process LocalHost 不受影響。
* **對話輪併發上限（turn gate）** — 新模組 `src/agent/turn_gate.py`
  （零副作用，鏡像 `provider_chain.py` 慣例）:
  - `QDRANT_MAX_CONCURRENT_TURNS`（空/未設 = 不限 = 既有行為；無效/
    非正整數回落不限只警告）— 限制整個程序同時執行的 LLM 對話輪數，
    GUI / scheduler / webhook / telegram / CLI 共用。
  - `QDRANT_TURN_WAIT_SECONDS`（預設 120）— 佇列等待預算，超時回
    `TurnGateBusy`（明確可動作的 RuntimeError），呼叫端既有優雅降級
    路徑（GUI error envelope / webhook 500 / scheduler last_error）
    自然承接。
  - `turn_slot()` 為**同執行緒可重入** — turn 中呼叫
    `run_scheduled_task_now` / `run_webhook_trigger_now` 再開一輪時
    重用同一名額，max=1 也不會死鎖（SchedulerLocalHost._run_now 的
    既有共享 agent 模式）。
  - gate 在首次使用時惰性建立並快取（double-checked lock）— 變更 env
    需重啟程序；`held_turns()` 提供診斷。
* **接線** — `Agent.ask` / `Agent.chat_turn` 改為薄包裝（`with
  turn_slot():`），方法體移至 `_ask_impl` / `_chat_turn_impl`（公開
  簽名不變，所有呼叫端零修改）。視覺 OCR 鏈（ImageDescriber，ingest
  4 workers）刻意不閘。
* `.env.example` + `src/config.py` 同步記錄兩個新 env vars（raw
  string 慣例）。
* 新測試 `tests/test_turn_gate.py`（resolver ×10、unlimited slot、
  cap 串行化/併發上限、巢狀不死鎖、Agent 接線 ×4、stdio/http_sse
  client 併發 ×4、RLock 重入）。

### v0.9.44.0 — RTSP + ffmpeg 截圖：無 ONVIF snapshot 能力的攝影機（XMEye 等）

使用者需求:「針對沒有提供 snapshot 功能的 camera（例如 xmeye）擴充
MCP onvif 功能，提供以 rtsp 配合 ffmpeg 的截取功能」。

* **新 `src/onvif_tools.py::grab_frame_via_rtsp(rtsp_uri, out_path,
  settle_s?, timeout_s?)`** — ffmpeg 單張抓圖：
  `-rtsp_transport tcp -i <uri> -frames:v 1 -q:v 2 out.jpg`（-c copy
  不適用單張 → 實際解碼一幀）；`settle_s` 可跳過前幾張全黑 frame
  （輸入側 `-ss`）；成功須非空檔 + JPEG magic；失敗 raise
  OnvifError（含 stderr tail，與 record_stream 同形）。
* **新 MCP 工具 `onvif_rtsp_snapshot`**（onvif-local 第 18 個工具）：
  * 候選順序：GetStreamUri（profiles 可達時）→ **vendor variants**
    （standard / hikvision / dahua / **xmeye**）逐一嘗試直到抓到
    JPEG；每個失敗記錄進 `attempts`。
  * 參數：`uri`（直接指定）/ `channel`（DVR 通道 1-64，variants
    用）/ `settle_s`（0-10）/ `path` / `profile_token`。
  * GetStreamUri 不可達時退回合成 base `rtsp://<ip>:554/onvif1`
    繼續試 variants。
* **`onvif_get_snapshot` 自動退回** — ONVIF snapshot 路徑失敗
  （no profiles / GetSnapshotUri 不可用 / fetch 失敗）→ **自動**
  走 RTSP 抓圖；成功回 `via="rtsp"` + `note` 說明 + `onvif_error`
  （原錯誤摘要）；兩條路都失敗 → 合併錯誤 + `rtsp_attempts` +
  hint。`via_rtsp=true` 可強制跳過 ONVIF 路徑。
* stdio 鏡像 + system prompt 更新（via=rtsp 即 RTSP 抓圖路徑）。
* Tests — `tests/test_rtsp_snapshot.py` **14 new**（ffmpeg cmd 形狀
  / settle / 失敗 tail / 非 JPEG 拒絕 / 無 ffmpeg ×5、host 工具 ×4
  （standard 成功、退到 xmeye、全失 attempts≥4、channel）、自動退回
  ×3（fallback / ONVIF 優先 / 雙失敗）、stdio ×1）；onvif 合計
  **80 passed**。

`pyproject.toml` 0.9.43.2 → **0.9.44.0**。

### v0.9.43.2 — 診斷強化：per-candidate 失敗原因 + 空名殘留條目防護（live log 2026-09-14 11:19）

第二次 live log（11:19）顯示 fallback 鏈跑了但**全部候選都空**，
tried 清單被截斷看不出原因；且 `onvif_test_credentials` 對**空名**
回 `ok:true` + 空欄位（誤導 LLM）。兩處診斷強化：

* **`get_profiles_anywhere` per-candidate 原因** — 每個媒體端點的
  失敗原因（`HTTP 401` / `transport refused` / `SOAP fault` /
  `HTTP OK but 0 profiles`）收集進錯誤訊息：
  `device returned no media profiles — tried: <url> → OnvifError: ... |
   <url> -> HTTP OK but 0 profiles | ...`。下一次 live 嘗試一行 log
  就能定位是哪個端點、為什麼失敗（憑證？埠不通？路徑錯？）。
* **`_device_and_creds` 空名拒絕** — live `favorites.json` 殘留一條
  **name 缺失**的條目（`get("")` 曾命中它 → test_credentials 對
  空名回 ok + 空 manufacturer）。空 name 現在直接回「device name is
  required」明確錯誤，殘留條目無法再被誤解析。
* **`_test_credentials` 空裝置資訊判定** — 裝置有回應但
  manufacturer/model/serial/firmware **全空** → `ok:False` + 「xaddr
  可能不是 ONVIF device service」（live 案例的 xaddr 指到 web 頁面
  回 200 但無欄位）— 不再回誤導性的 ok:true。
* Tests — onvif 63 → **66**（+3：reasons 斷言、transport 原因出現、
  空名拒絕、空資訊標記）。

`pyproject.toml` 0.9.43.1 → **0.9.43.2**。

### v0.9.43.1 — XMEye DVR 修復：媒體端點 fallback 鏈 + vendor RTSP variants（live log 2026-09-14「顯示五股邊巷影像」）

Live log 診斷三個問題，全部修復：

1. **`onvif_get_snapshot` → "device returned no media profiles"** —
   舊碼用 **device service XAddr** 要 media profiles；XMEye/Hisi DVR
   不在 device service 提供。新 `get_profiles_anywhere()`：
   GetCapabilities(Media) XAddr → 慣例 `media_service` →
   **`:8899/onvif/media_service`**（XMEye/Hisi 常見）→
   `http_media_service` → device XAddr 本身，依序輪詢直到非空；
   全空時錯誤訊息附 tried 清單。`_get_snapshot` / `_get_resources` /
   `_record` 全改用；snapshot/resources 回傳 `media_xaddr_used`。
2. **ffmpeg `rtsp DESCRIBE failed: 401`** — XMEye 的 RTSP 不吃標準
   userinfo 格式。新 `rtsp_variants(uri, user, pw, channel)` 回四種
   vendor 格式（standard / **hikvision** /Streaming/Channels/101 /
   **dahua** cam/realmonitor / **xmeye** query-string
   `user=..&password=..&channel=1&stream=0.sdp?real=1`）：
   * `onvif_record` 新增 **`uri` override + `channel`** 參數 —
     record 失敗（DESCRIBE/401）時錯誤回附 `rtsp_variants` + hint，
     LLM 用 `onvif_record(name=..., uri=..., channel=N)` 換格式重試。
   * `_get_resources` 回傳加 `rtsp_variants`。
   * **`_embed_rtsp_creds` 修復**：uri 的 path/query 已含
     `user=`+`password=`（XMEye 格式）時不再塞（常常空的）userinfo
     — 舊行為會把 URL 弄成 `rtsp://user:@host/...` 反而壞掉。
3. **prompt** — ONVIF 區塊記錄自動輪詢 + variants 重試流程；`head`
   不存在於 cmd 的教訓已由 v0.9.14.1 run_bash 規則涵蓋。
* **安全備註**（log 觀察）：LLM 透過 run_bash 解碼 favorites 的
  password_b64（pw_len=7）— b64 是遮蔽非加密（文件已明示）。
  建議改用 `cred_set`（password_env）— env 變數不會被沙箱檔案
  工具讀到。
* Tests — onvif 56 → **63**（+7：fallback 找到 :8899 ×1、全空錯誤 ×1、
  variants 四格式 ×2、snapshot fallback 鏈 live 回歸 ×1、record
  DESCRIBE 失敗回 variants ×1、record uri override ×1）。

`pyproject.toml` 0.9.43.0 → **0.9.43.1**。

### v0.9.43.0 — Runtime control MCP 核心工具（runtime-local，第 18 個 LocalHost）

使用者需求:「以 MCP 方式，以核心功能方式提供 reload 和 restart」。

* **新 `RuntimeLocalHost`（server `runtime-local`，4 工具）** — 把
  v0.9.42.0 的 reload/restart 機制暴露給 LLM：
  * `reload_config(restart_services?=true, reconnect_mcp?=true)` —
    重新載入 .env + 重置 provider 鏈 + 條件式重啟服務 + 重連 MCP
    fleet；回傳 changed 鍵清單。**5 秒冷卻**（連續 reload 直接回
    cooldown 錯誤，防 LLM 迴圈轟炸）。
  * `restart_services(services?)` — 只重啟常駐服務（不改設定）；
    `services=scheduler,webhook,telegram,onvif_watches` 可過濾。
  * `restart_mcp_servers(path?)` — 重讀 config/mcp.json 重建遠端
    fleet（本地 LocalHosts 存活）；**seed 時直接接 manager 的
    `reconnect_remote_servers` — 不需要 Agent**。
  * `get_runtime_status()` — 目前 provider / model / client 是否
    就緒 / last_provider（failover 診斷）。
* **接線（v0.8.6.3 雙向收斂模式）**：
  * `Agent.mcp_manager` property 接 `set_runtime_ops_callback(
    self._runtime_ops)` — reload / restart_services / status 委派給
    Agent（reload 內含 MCP 重連）。
  * `McpManager.set_runtime_ops_callback` canonical setter +
    `_runtime_local_host` ref。
  * 未接線（外部 stdio client）→ 明確「not wired」錯誤包絡，
    建議改用 `python cli.py reload`。
* stdio 鏡像 `src/mcp_server/runtime_server.py`（4 handler）。
* **system prompt** — Runtime control 區塊（觸發詞：重新載入設定 /
  套用新 .env / 重啟服務 / 重連 MCP；reload 後摘要 changed + 服務
  狀態回報使用者）。
* `MCP_ACTIVE_SERVERS` gate 清單加 `runtime-local`（live .env +
  .env.example）。
* Tests — `tests/test_runtime_host.py` **13 new**（工具面 ×2、
  cooldown + 委派 ×2、restart 過濾 ×1、MCP 重連 callback ×1、
  status 接線/未接線 ×1、manager 註冊 + setter push-down + Agent
  ops ×3、prompt + stdio ×2、gate smoke ×1）— **13 passed**。

`pyproject.toml` 0.9.42.0 → **0.9.43.0**。

### v0.9.42.0 — .env / 設定檔熱重載 + 服務重啟機制（GUI 按鈕 / CLI `reload` / mtime 自動 watcher）

使用者需求:「支援 .env 等設定檔 on the fly reload 機制和服務 restart
機制」。

* **`Config.reload(path=None)`** — 重讀 `.env`（dotenv override=True）
  並**重新賦值全部 env-backed 類屬性**（同名 env var + 型別轉換表
  `_RELOAD_TYPED`：QDRANT_URL 正規化 / EMBEDDING_DIM、CHUNK_* int /
  LLM_PROVIDER lower / DESCRIBE_IMAGES bool）；namespaced groups
  （ingest/llm/embedding）本來就是即時 property。回傳**變更清單**；
  壞值 skip + 警告（改壞一個值不會弄垮 chat）；**從 .env 刪除變數
  仍需重啟**（dotenv 不清除 process env — 已文件化）。
* **`Agent.reload_runtime(restart_services=True)`** — 共用入口：
  config.reload → **重置 LLM 鏈**（_client/_provider/_model/
  _extra_body/last_provider → None，下一輪按新 config 重新選定；
  in-flight turn 不受影響）→ **條件式重啟常駐服務**：
  * scheduler（有 enabled 排程才 stop+start）
  * webhook HTTP listener（有 enabled 觸發器才 stop+start）
  * Telegram bot（token + allowlist，fail-closed）
  * ONVIF 事件監看（resume_watches 冪等）
  * 每服務獨立 try/except — 一個服務失敗不中斷整個 reload。
  （`MCP_ACTIVE_SERVERS` / `MAX_TOOL_ITERATIONS` / reasoning effort
  本來就是 per-call 讀 env — reload 後下一輪自動生效。）
* **`McpManager.reconnect_remote_servers(path)`** — 重讀
  `config/mcp.json` → close 全部遠端 client → 重建（本地 LocalHosts
  存活）。
* **`src/config_watch.py` — `ConfigWatcher`**（選用）：mtime 輪詢
  `.env` + `config/mcp.json`（純 stdlib、Windows 安全），變更即
  callback（reload + 重啟 + MCP 重連）。`QDRANT_CONFIG_HOT_RELOAD=on`
  開啟（**預設 off** — 誤存即時生效有風險；按鈕/CLI 永遠可用）；
  `QDRANT_CONFIG_WATCH_INTERVAL` 1-300s（預設 10）。
* **三個入口**：
  1. **GUI** — Admin tab 新「🔄 重新載入」區塊 →
     `reload_runtime_config()`（agent.reload_runtime + MCP 重連，
     toast 顯示變更鍵 + 服務狀態）；i18n ×4。
  2. **CLI** — `python cli.py reload [--no-services]`。
  3. **watcher** — GUI prewarm 掛 `_start_config_watcher`
     （env 開啟才啟動；callback 失敗不殺線程）。
* Tests — `tests/test_config_reload.py` **17 new**（reload 變更偵測/
  型別轉換/壞值保護/unset 保留/namespaces/冪等 ×6、Agent LLM 重置 +
  條件式服務重啟 + no-services 旗標 ×3、MCP 重連（本地存活 + 遠端
  註冊 + 缺檔 OK）×2、watcher mtime 偵測 + 迴圈回呼 + 開關 ×3、
  GUI 橋接組合 + 錯誤包絡 ×2、CLI 摘要輸出 ×1）— **17 passed**。

`pyproject.toml` 0.9.41.2 → **0.9.42.0**。

### v0.9.41.2 — MCP_ACTIVE_SERVERS 全服務清單（17 LocalHosts + 4 外部）+ `webhook-localhost` typo 修正

使用者需求:「將所有 MCP 服務加入 .env 服務清單、更新文件」。

* **盤點**：manager 已知伺服器 = 17 個 in-process LocalHosts
  （qdrant-local / web-local / filesystem-local / scheduler-local /
  todo-local / openwith-local / log-analyzer-local / archive-local /
  exec-local / datetime-local / chat-history-local / stock-local /
  webhook-local / onvif-local / cred-local / netscan-local /
  meta-local）+ `config/mcp.json` 4 個外部（windows-mcp / telegram /
  drawio / my_web）= **21 個**。
* **修正 live `.env` 的 `MCP_ACTIVE_SERVERS`**：
  1. **typo 修正**：`webhook-localhost` → `webhook-local` — 舊值是
     未知名稱，gate 直接 skip（v0.9.13.2 契約），**webhook 觸發器
     工具一直被藏住**（排程/webhook 教學的 send 路徑因此斷鏈）。
  2. 補齊 `openwith-local` / `log-analyzer-local` / `archive-local` /
     `exec-local` / `drawio` / `my_web`。
* `.env.example` 範例行同步 + 區塊註解新增版本註記
  （v0.9.37.0 onvif-local / v0.9.39.0 cred-local / v0.9.40.0
  netscan-local / 全清單說明 + typo 會藏工具的警語 + 驗證方法：
  boot log「MCP_ACTIVE_SERVERS unknown servers skipped」應為空）。
* README v0.9.41.2（本段）。

`pyproject.toml` 0.9.41.1 → **0.9.41.2**。

### v0.9.41.1 — 「判斷再通知」配方一等公民化（watch → 快照 → LLM 判斷 → 條件通知）

使用者需求:「可以支援偵測到什麼就啓用 prompt 判斷是什麼再通知嗎」。
鏈路 v0.9.41.0 已具備（webhook fire = 全新 Agent + 完整工具面 + 視覺
OCR 鏈），本輪把它做成推薦配方並補強資料面：

* **payload 新增 `event_text`** — 人類可讀事件摘要（topic + 資料 +
  watch/裝置），判斷 LLM 不必自行拼湊 JSON 欄位。
* **`_compose_prompt` 注入鏈驗證** — payload（含 snapshot 路徑 +
  event_text）JSON 注入觸發器 prompt；fired Agent 可對沙箱內快照
  `describe_image`（vision 鏈 ollama→openrouter→siliconflow→openai）
  → 判斷畫面是什麼（人/車/動物/光影誤判）→ 符合條件才
  `send_telegram_photo` 通知，否則回「誤報已忽略」。
* **system prompt** — ONVIF 事件區塊新增「判斷再通知」推薦配方
  （觸發器 prompt 範本：先 describe_image 分析 → 條件成立才通知）。
* Tests — onvif 56 passed（+1：payload event_text/snapshot + compose
  注入斷言）。

`pyproject.toml` 0.9.41.0 → **0.9.41.1**。

### v0.9.41.0 — ONVIF Phase C：PTZ 控制 + 事件監看 × Webhook 整合（onvif-local 17 工具）

使用者需求:「製作 onvif Phase C，並問 Events 有機會結合 webhook 嗎，
例如監測畫面和 webhook 結合」。

* **SOAP 層（`src/onvif_tools.py`）**：
  * `get_service_xaddr` — GetCapabilities(Category=PTZ|Events|Media…)
    泛化（`get_media_xaddr` 的參數化版，含慣例 sibling fallback）。
  * **PTZ**：`ptz_continuous_move`（pan/tilt/zoom 夾限 -1..1 +
    Duration 0.1-10s 自動停）、`ptz_stop`、`get_presets`、
    `ptz_goto_preset`；命名空間 `_NS_TPTZ` / `_NS_TT`。
  * **Events（PullPoint）**：`create_pull_point_subscription`
    （SubscriptionReference → PullPoint URL）、`pull_messages`
    （長輪詢，解析 Topic / Source / Data SimpleItem）、
    `match_events`（不分大小寫關鍵字比對 topic+資料值）、
    `drain_events`（一次性訂閱+拉取）。每輪重新訂閱 — 韌性設計。
  * **WatchStore** — `onvif_watches.json`（原子寫，mirror
    FavoriteStore）+ 每監看 `onvif_watch_history/<name>.jsonl`
    50 筆環。
* **工具（onvif-local 9 → 17）**：
  * `onvif_ptz_move`（**confirm=true 閘門** — 攝影機實際轉動）/
    `onvif_ptz_stop` / `onvif_get_presets` / `onvif_goto_preset`
    （同樣需 confirm）。
  * `onvif_get_events` — 一次性探測 topic（建 watch 前先看）。
  * `onvif_watch_start` / `onvif_watch_list` / `onvif_watch_stop` —
    **事件監看**：背景 daemon 線程輪詢 PullPoint；命中關鍵字
    （預設 motion）→（a）事件歷史環（b）`snapshot_on_event` 快照進
    沙箱（c）`webhook_trigger_id` → **觸發已註冊的 webhook 觸發器**
    （沿用 `make_webhook_fire` 引擎，每次 fire 獨立 Agent）。
    動作防洪水位 = poll_interval（每輪至多一次動作）。
* **Webhook 整合（回答使用者問題：有，已實作）**：
  `OnvifLocalHost.attach_webhook_fire` + `McpManager.
  set_webhook_fire_callback` 推下（v0.8.6.3 雙向收斂模式）—
  監看命中 → fire(trigger, payload) → 全新 Agent 執行 prompt
  → 可 `send_telegram_message` / 存檔 / 查天氣…。
  流程：`add_webhook_trigger("motion-alert", prompt=…)` →
  `onvif_watch_start(..., webhook_trigger_id="motion-alert",
  snapshot_on_event=true)` → 偵測到移動 → 快照進沙箱 + prompt 執行
  （payload 含 event topic / 資料 / 快照路徑）。
* **開機自動恢復**：`gui/api.py::_resume_onvif_watches`（watch 設定
  持久化、線程隨程序死 — prewarm 重新拉起；
  `QDRANT_ONVIF_WATCH_AUTORESUME=off` 可關）。
* **system prompt** — ONVIF 區塊擴充 PTZ（confirm 紀律）與
  事件/webhook 組合（含「門口偵測到移動 → 快照 + Telegram」範例）。
* stdio 鏡像 8 個 handler 同步。
* Tests — `tests/test_onvif_host.py` 55 → **+14**（PTZ body 組建 +
  夾限 ×1、presets/goto ×1、事件 round-trip + 關鍵字過濾 ×1、
  xaddr fallback ×1、工具面 ×1、confirm 閘 ×1、move happy path ×1、
  events 工具 ×1、watch start/list/stop ×1、**watch 命中 → 快照 +
  webhook fire** ×1、resume 冪等 ×1、manager 接線 ×1、prompt ×1、
  stdio ×1）；onvif+mcp+cred+webhook 合計 **137 passed**。

`pyproject.toml` 0.9.40.0 → **0.9.41.0**。

### v0.9.40.0 — MCP 網路掃描擴充：IP Scanner + Port Scanner（netscan-local，第 17 個 LocalHost）

使用者需求:「以MCP方式製作擴充功能，提供ip scanner和port scanner」。

* **新 `src/net_scan_tools.py`（純 stdlib 核心，零新依賴）**：
  * `parse_targets` — CIDR（`192.168.1.0/24`）/ 單一 IP / 範圍
    （`192.168.1.10-50` 兩種寫法）→ 目標清單（去重、排序）。
  * **IP scanner `scan_hosts`** — method=tcp（預設：以常見服務埠
    TCP-connect 探測判定存活；免管理員權限）或 ping（系統 ICMP，
    支援 Windows/POSIX 參數差異 + 中文 locale「無法連線」誤判過濾）；
    每主機存活 = 任一探測埠開啟；可反解 DNS。
  * **Port scanner `scan_host_ports` / `tcp_probe`** — 單一主機並行
    TCP-connect 掃描（ThreadPoolExecutor），回報埠狀態 + 服務名稱
    （內建 LAN 常用埠映射：rtsp / rdp / smb / qdrant / onvif /
    hikvision / dahua … + `getservbyport` 後備）+ 可選 banner grab
    （recv-only，不送 payload）。
  * `local_interfaces` — 本機非 loopback IPv4（UDP-connect 技巧 +
    getaddrinfo，去重）。
* **安全模型（mirror exec/archive blocklist 紀律）**：
  * **預設僅允許私網目標** — 公網/全域 IP 一律拒絕並提示
    `QDRANT_NETSCAN_ALLOW_PUBLIC=on`（對外掃描有濫用/法務風險，
    必須使用者明確開啟；無法解析的目標 fail-closed 視為公網）。
  * 規模上限 `QDRANT_NETSCAN_MAX_HOSTS=4096` /
    `QDRANT_NETSCAN_MAX_PORTS=1024`（10.0.0.0/8 或全埠直接拒絕）；
    並行 `QDRANT_NETSCAN_MAX_WORKERS=128`；timeout 上限 5s。
  * 只做 connect/recv 探測 — 不送 payload、不做漏洞利用。
* **`NetScanLocalHost`（server `netscan-local`，3 工具）**：
  `net_scan_hosts`（targets/method/ports/timeout/resolve_names）/
  `net_scan_ports`（host/ports/timeout/grab_banner，主機名可自動
  解析） / `net_my_interfaces`（回網段建議 `/24`）。manager
  `from_config` 註冊於 Meta 前（`_net_scan_local_host` ref）。
* **stdio 鏡像** `src/mcp_server/net_scan_server.py`（外部 MCP
  客戶端；`python -m src.mcp_server.net_scan_server`）。
* **system prompt** — Network scanning 區塊：net_my_interfaces →
  net_scan_hosts → net_scan_ports 流程 + 私網預設 + 只掃自己的網路 +
  表格呈現。
* Tests — `tests/test_net_scan_host.py` **24 new**（target/port 解析
  ×6、公網防護 ×2、真 socket listener 探測/banner/sweep ×4、
  scan_hosts tcp+ping live ×2、本機介面 ×1、LocalHost handler ×6、
  manager 註冊 + prompt + stdio parity ×3）— **24 passed**（真
  socket 只綁 127.0.0.1:0，不出機器）。
* `.env.example` 新增 QDRANT_NETSCAN_* 註解區塊。

`pyproject.toml` 0.9.39.1 → **0.9.40.0**。

### v0.9.39.1 — ONVIF 資源總覽工具 `onvif_get_resources`（第 9 個 onvif-local 工具）

使用者問題:「onvif 有製作 soap 以發現 rtsp 等 camera 資源嗎」 — RTSP
一直都有（GetStreamUri，錄影時內部呼叫），但沒有獨立的資源發現入口。

* **新工具 `onvif_get_resources(name, profile_token?)`** — 一次 SOAP
  巡迴回傳：device 資訊（GetDeviceInformation）、media service
  XAddr（GetCapabilities(Media)）、media profiles（GetProfiles）、
  以及所選（預設第一個）profile 的 **RTSP stream URI**（GetStreamUri）
  與 **snapshot URI**（GetSnapshotUri）。單一 profile 的 URI 子呼叫
  失敗時回 `<unavailable: …>` 而不整體失敗。
* stdio 對應 handler + system prompt 資源總覽說明 + 測試 ×2
  （mock SOAP 全鏈 → rtsp:// 與 http:// snapshot URI、未知裝置錯誤）。
  onvif + credential 合計 **57 passed**。

`pyproject.toml` 0.9.39.0 → **0.9.39.1**。

### v0.9.39.0 — 中央帳號集（Credential Store）：MCP / UI / CLI 三介面統一管理

使用者需求:「以MCP方式和UI/cmd三種介面設定建立帳號集管理功能，並能
支援所有需使用帳號的服務使用，例如 onvif 裝置的登入密碼管理」。

* **新 `src/credential_store.py` — `CredentialStore`**（共用核心）：
  * 單一 JSON store（`<QDRANT_RAG_HOME>/credentials.json`，原子寫 +
    chmod 600 best-effort、損壞自動恢復）；entry = name / username /
    **password_env（推薦，JSON 不存秘密）** / password_b64（base64
    遮蔽，非加密）/ service 標籤 / notes / 時間戳。
  * `resolve()` env 優先語意與 ONVIF favorites 一致；list/show 輸出
    **一律遮罩**（`ad***` / `******** (b64)` / `env:VAR`）。
* **三種管理介面**：
  1. **MCP** — `CredLocalHost`（cred-local，第 16 個 LocalHost，5 工具）：
     `cred_add` / `cred_list`（service 過濾）/ `cred_get`（含 env_var_set
     存在性）/ `cred_remove` / `cred_resolve`（遮罩輸出解析狀態）。
  2. **GUI** — 新 **🔑 Credentials 分頁**：表格（遮罩）+ 新增/覆蓋表單
     + 刪除；`gui/api.py` 橋接 `list_credentials` / `save_credential` /
     `remove_credential`；i18n ×4 語系。
  3. **CLI** — `python cli.py creds {list,show,add,remove,test}`
     （全部遮罩輸出；add 支援 --password-env / --password / --service /
     --overwrite）。
* **ONVIF 整合（第一個消費者）**：`onvif_favorite_add` 新增
  `cred_set` 參數 — 引用中央帳號集名稱；`resolve_credentials` 解析
  順序改為 **cred_set → password_env → password_b64 → none**；
  favorites.json 只存 `cred_set` 名稱，密碼集中在 credentials.json /
  環境變數。
* **system prompt** — ONVIF 區塊後新增帳號集說明（cred_* 工具 +
  cred_set 引用方式 + 建議密碼放 env）。
* Tests — `tests/test_credential_store.py` **16 new**（store CRUD/
  resolve/mask/原子寫 ×8、CredLocalHost ×3、**ONVIF cred_set 整合
  ×2**（favorite 引用中央集 → resolve 出 live-secret）、manager 註冊
  /prompt/GUI 橋接/CLI handler ×4 — 遮罩洩漏斷言含 base64 檢查）；
  **16 passed**。

`pyproject.toml` 0.9.38.0 → **0.9.39.0**。

### v0.9.38.0 — ONVIF Phase B：監視錄影（GetStreamUri → RTSP → ffmpeg → 沙箱 MP4）

Phase B 交付 — `onvif_record`（第 8 個 onvif-local 工具）：

* **流程**：GetProfiles → GetCapabilities(Media) → **GetStreamUri**
  （RTP-Unicast）→ RTSP URI 注入帳密（userinfo 形式，URL-encode）→
  **ffmpeg `-rtsp_transport tcp -t <秒> -c copy`** 串流複製成 MP4
  （不轉碼 → 近零 CPU）→ 存進寫入沙箱（預設
  `uploads/onvif-record-<時間>.mp4`）。
* **ffmpeg 依賴**：`shutil.which` 檢查 — 未安裝時**fail-fast 回明確
  錯誤**（含 `winget install Gyan.FFmpeg` 安裝指引），不做任何
  SOAP/網路呼叫。實機未裝 ffmpeg → 工具如實告知。
* **邊界**：seconds 1-600（`QDRANT_ONVIF_RECORD_MAX_SECONDS` 可調）；
  子程序 timeout = seconds + 30s margin；rc 0/255 + 非空檔案才算
  成功（255 = ffmpeg 到時停止）；輸出路徑沙箱 containment +
  強制 .mp4。
* **system prompt** — ONVIF 區塊更新：錄影已開放（註明需 ffmpeg）。
* Tests — `tests/test_onvif_host.py` **+8**（rtsp 帳密注入、.mp4
  強制/沙箱、ffmpeg 缺失 fail-fast、指令構建（-c copy /
  rtsp_transport tcp）、空輸出失敗、GetStreamUri 解析、工具層
  ffmpeg 缺失/完整流程 — rtsp uri 含帳密驗證）；
  **39 passed**；mcp_client 迴歸 **28 passed**。

`pyproject.toml` 0.9.37.0 → **0.9.38.0**。

### v0.9.37.0 — ONVIF 監視器 MCP（Phase A READ-ONLY）：區網探索 / 我的最愛帳密 / media profile / 監視畫面截圖

使用者需求:「以MCP方式製作，擴充功能用以使用ONVIF抓取特定設定監視
畫面，支援區網內onvif discovery、管理我的最愛設備、監視畫面截圖、
錄影」+「支援有的設備需輸入帳密才能使用」。採**分階段**交付 —
本次 Phase A（唯讀），錄影（B）與設定修改（C）後續輪：

* **零新依賴** — SOAP envelope 手工構建（ONVIF device/media 就是
  XML round-trip）+ WS-Discovery 用 stdlib UDP multicast +
  httpx（既有）抓圖；比 zeep 更輕、更好 mock。
* **新 `src/onvif_tools.py`**（純邏輯，~710 行）：
  * `wsdiscovery_probe()` — UDP 239.255.255.250:3702 Probe
    （NetworkVideoTransmitter），可限介面 IP、去重、解析 onvif.org
    scope 名稱提示。
  * WS-Security **UsernameToken Digest**（nonce+created+SHA1）—
    ONVIF 標準認證；GetDeviceInformation / GetProfiles /
    GetCapabilities(Media) / GetSnapshotUri。
  * `FavoriteStore` — `<QDRANT_RAG_HOME>/onvif/favorites.json`
    （原子寫 + chmod 600 best-effort）；**帳密支援兩種模式**：
    `password_env`（**推薦** — JSON 不存密碼，env 優先）或明文
    base64 **遮蔽**存檔（非加密）；list 輸出一律遮罩
    （`ad***` / `********` + credential_source）。
  * `fetch_snapshot` — Digest → Basic → 無 auth 三段嘗試 +
    JPEG magic 驗證 + 8 MB 上限；`resolve_snapshot_path` 沙箱
    containment（路徑穿越拒絕）。
* **`OnvifLocalHost`**（onvif-local，第 15 個 in-process LocalHost，
  7 個工具）：`onvif_discover` / `onvif_favorite_add`（密碼明文或
  password_env、overwrite） / `onvif_favorite_list`（遮罩）/
  `onvif_favorite_remove` / `onvif_test_credentials`
  （GetDeviceInformation 輕量認證測試）/ `onvif_get_profiles` /
  `onvif_get_snapshot`（GetProfiles → GetCapabilities(Media) →
  GetSnapshotUri → 下載 → 存沙箱 uploads/ → **chat 直接內嵌顯示**
  （v0.9.30）+ 可 `send_telegram_photo` 轉發（v0.9.31））。
  manager 註冊（`_onvif_local_host` ref）+ stdio 鏡像
  `src/mcp_server/onvif_server.py`。
* **system prompt** — ONVIF 觸發區塊（discovery → favorite →
  snapshot 流程、password_env 建議、錄影/PTZ 未開放如實告知）。
* Tests — `tests/test_onvif_host.py` **31 new**（credentials ×5、
  WSSE ×2、Probe 解析 ×2、FavoriteStore ×6、snapshot helpers ×7、
  host ×6、註冊/stdio/prompt ×3 — 全 mock 驅動零網路）；
  mcp_client/meta 迴歸 **35 passed**。

`pyproject.toml` 0.9.36.1 → **0.9.37.0**。

### v0.9.36.1 — 圖中方塊字真正的根因：PowerShell 路徑繞過 bootstrap → MATPLOTLIBRC 全環境修復

使用者回報（附截圖）:「matepile（mplfinance）畫出的圖 中文還是
不知名的方塊字」。事實：log 顯示方塊那張圖是 LLM 走
**write_file + windows-mcp PowerShell** 畫的 — **繞過了 run_python**，
v0.9.34 的 bootstrap 根本沒機會執行。

* **修法：`MATPLOTLIBRC` 環境變數（matplotlib 官方支援）**：
  * 新 ship **`config/matplotlibrc`** — `font.family: sans-serif` +
    `font.sans-serif: Microsoft JhengHei, ..., DejaVu Sans` +
    `axes.unicode_minus: False`。
  * `src/config.py` import 時 `os.environ.setdefault("MATPLOTLIBRC",
    <ship 的 rc 檔>)` — **每個 import src.config 的程序**（GUI /
    exec 子程序 / MCP stdio server 含 windows-mcp）都會把這個 env
    傳給它的所有 python 子程序 → **任何路徑畫圖都有 CJK 字型**
    （run_python、PowerShell、mplfinance、手寫 script 全覆盖）。
    使用者自設的 MATPLOTLIBRC 優先（setdefault）。
* **實機驗證**（GUI env + mplfinance 3.11.1，`style="charles"`）：
  「台積電 週K線」「價格 (元)」渲染 **0 個 findfont family 警告**
  （stderr 僅剩無害的 weight 降級提示）；v0.9.34 的 rcParams 方式
  已知會被部分繪圖路徑繞過，file-based rc 是官方機制、
  mplfinance 的 style 不會覆寫字型鍵。
* **system prompt** — exec 區塊更新：所有 runner 都 CJK-safe
  （write_file + PowerShell 也是）；萬一仍需要，可在 script 頂端
  自行設 `plt.rcParams['font.sans-serif']=['Microsoft JhengHei']`。
* Tests — `tests/test_exec_host.py` +3（ship 的 rc 檔存在含 CJK
  stack、config import 設定 MATPLOTLIBRC、`_run_capture` 子程序 env
  帶 MATPLOTLIBRC）；**89 passed + 1 skipped**。

`pyproject.toml` 0.9.36.0 → **0.9.36.1**。

### v0.9.36.0 — K 線圖出不來的根因：Windows `-c` 指令行上限 → 超大 code 改暫存檔執行

使用者回報:「k圖出不來，是不是加了CJK字檔造成的」。**不是 CJK 的錯**
（bootstrap 已實測無問題）— 事實鏈：

1. 17:23 那輪 LLM 把 **1000 根 K 線資料全部 inline** 進 run_python
   的 code（~57 KB）→ **Windows `CreateProcessW` 指令行上限
   32,767 字元** → `python -c` 直接
   `WinError 206（檔名或副檔名太長）`（已實測重現）。
2. 工具呼叫過大也讓模型把它當文字貼出來（↓28,633 tok 的 JSON
   blob）— 程式根本沒執行 → `uploads/2330_weekly_kline.png` 不存在
   → 前端 regex 從貼出的 code 文字比對出路徑 →
   `⚠ file not found` 佔位框（顯示邏輯正常）。

* **`src/exec_tools.py`** — code 長度 > 24k 字元時改寫成沙箱內暫存
  `.qdrant_exec_<rand>.py` 用 `python <file>` 執行，執行後刪除
  （uuid 名稱防並發碰撞；CJK bootstrap 兩條路徑都帶）。**57 KB 的
  code 實測從必死變可執行**（live test 驗證 rc=0）。
* **system prompt** — exec 區塊加規則：KEEP run_python CODE SHORT —
  大資料存成沙箱檔再讀（write_file / 工具回覆已給 CSV），不要
  inline 貼資料集。
* Tests — `tests/test_exec_host.py` +5（暫存檔路徑 + bootstrap 內嵌
  + 檔案清理、小 code 仍 `-c`、**live ~57KB code 實跑** — 修復前
  WinError 206 的直接重現）；**86 passed + 1 skipped**。

`pyproject.toml` 0.9.35.0 → **0.9.36.0**。

### v0.9.35.0 — MCP 股票查詢自動存進 Qdrant（stock_docs）+ 語意歷史查詢工具

使用者需求:「儲存 mcp stock查詢的資料到qdrant」。鏡像 LogStore 模式
（v0.9.7）— 專用 collection + 自動存檔 + 語意搜尋：

* **新 `src/stock_store.py` — `StockStore`**（專用 collection
  ``QDRANT_STOCK_COLLECTION``，預設 ``stock_docs``，不與文件 RAG / 日誌
  混雜）：
  * 一筆查詢快照 = 一個 point；embedding 文本為
    `build_summary_text()` 產生的可讀摘要（tool + symbol + 價格/量
    純量 + bars_n/orders_n + JSON 尾段）。
  * point id = `uuid5(tool | symbol | ts_epoch)` — **確定性**：同一秒
    的同 tool+symbol 重存 = 覆寫同一點（不重複堆積）。
  * payload 索引：`tool` / `symbol`（keyword）+ `ts_epoch`（float）；
    HNSW + Cosine（同 LogStore）。
* **MegaStockLocalHost 自動存檔** — 4 個查詢工具
  （quote/kline/account/orders）成功後自動 save，回覆加 `qdrant`
  欄位（`{ok, id, collection}`）；**永不 raise** — Qdrant 掛掉時查詢
  工具照常運作（只記 warning，`qdrant: {ok: false}`）。
* **2 個新 MCP 工具**（stock-local，8 個工具）：
  * `stock_search_history(query, top_k?, tool?, symbol?)` — 語意搜尋
    歷史快照（「上次查的台積電收盤價」）。
  * `stock_list_history(limit?, tool?, symbol?)` — 最近快照清單
    （新→舊 + total）。
  * stdio `stock` server 同步（dispatch + 8 工具面）。
* **system prompt** — stock 區塊加註自動存檔 + 歷史查詢規則
  （不要憑記憶編造歷史報價）。
* **實機 E2E**（本機 Qdrant 127.0.0.1:6333）：quote 存檔 ✓ →
  `stock_list_history` 讀回（2330, 17:31:03）✓ →
  `stock_search_history("台積電 上次查的收盤價")` → 2330 quote
  **score 0.738 首位** ✓。
* Tests — `tests/test_stock_store.py` **19 new**（summary/id/save/
  search/recent + host hook 8 項 + stdio/prompt）；既有
  `test_stock_host.py` 4 項更新（8 工具面、stdio dispatch、live 閘
  測試釘空憑證防實連、e2e 隔離 save）；合計 **48 passed**。

`pyproject.toml` 0.9.34.0 → **0.9.35.0**。

### v0.9.34.0 — 修 K 線圖中文變方塊：run_python 自動注入 matplotlib CJK 字型

使用者回報:「製作K line圖，圖中的中文都會變成方塊」。根因:
matplotlib 預設字型 **DejaVu Sans 沒有任何 CJK 字形** — 中文標題/
軸標籤全部渲染成 □□□（tofu）。LLM 會忘記 rcParams 設定
（v0.9.5.2 教訓），所以改在 **exec_python 自動注入**:

* **`src/exec_tools.py`** — 新增 `_MPL_CJK_BOOTSTRAP` +
  `_prepend_mpl_cjk()`：
  * code 含 chart 觸發詞（`matplotlib` / `mplfinance` / `pyplot` /
    `plt.`）才前置 bootstrap — 非繪圖的 run_python 呼叫**零開銷**
    （`is code` 等價）。
  * bootstrap：掃 `fontManager.ttflist` → 從 8 個候選
    （**Microsoft JhengHei** 微軟正黑體優先、JhengHei UI、DFKai-SB
    標楷體、Microsoft YaHei、SimHei、PMingLiU、Noto Sans CJK TC、
    PingFang TC）選出**實際已安裝**的 → 前置到
    `rcParams["font.sans-serif"]`（使用者程式碼之後仍可覆寫）+
    `axes.unicode_minus=False`（負號在 CJK 字型的顯示修正）。
    matplotlib 不存在 → try/except 全吞，完全無影響。
* **system prompt** — run_python 工具說明加一行:圖表自動 CJK 安全，
  不要自己設 `font.sans-serif`（防止 LLM 用錯字型蓋掉）。
* **實機證明**（GUI 的 milvus-mcp env，matplotlib 3.11.1）：JhengHei
  等 **6 個 CJK 字型命中**；「台積電 K 線圖」「價格 (元)」渲染
  **0 個 findfont 警告**（不再有方塊）。
* Tests — `tests/test_exec_host.py` **+7**（bootstrap 常數、prepend
  觸發/跳過、exec_python 攜帶/未觸發、無 matplotlib 中性、live
  matplotlib CJK 渲染 E2E — 無 matplotlib 環境自動 skip）；
  C:\Python314 **82 passed + 1 skipped**；GUI env 跑同組 **7 passed**。

`pyproject.toml` 0.9.33.0 → **0.9.34.0**。

### v0.9.33.0 — chat 圖片支援放大（點擊 → 全螢幕 lightbox）

使用者需求:「chat ui的圖，支援放大功能」。內嵌圖片（320px 縮圖）
看不細清 — 點擊即放大：

* **app.js** — `_makeChatImgClickable()` 掛在兩個圖片來源
  ((a) markdown 本機圖、(b) `[已上傳]`/路徑預覽)：
  * `cursor: zoom-in` 提示可點；點擊 → **全螢幕 lightbox**
    （`_openChatLightbox`）：黑底 overlay（z-index 9999）+ 圖片
    以 92vw × 86vh contain 顯示 + 路徑 caption（`textContent`
    純文字 — 無 XSS 面）。
  * **點 overlay 任意處或按 Esc 關閉**（keydown listener 於關閉時
    一併移除；重複開啟先關舊的 — 單例）。
  * data-URL 重用 — lightbox 的 img 直接用點擊當下 `img.src`
    （已是載入好的 data URL，零重新讀取）。
* **style.css** — `.chat-lightbox` overlay（fixed inset 0、半透明
  黑底、flex 置中）+ `.chat-lightbox img`（92vw/86vh contain）+
  `.chat-lightbox-cap`（白色 12px、ellipsis）+ `.chat-clickable`
  （zoom-in/zoom-out 游標）。
* Tests — `tests/test_chat_images.py` 25 → **30**（lightbox 函式、
  兩個來源皆可點、Esc/overlay 關閉、caption textContent-only、
  CSS 規則）；**30 passed**；`node --check` 通過。

`pyproject.toml` 0.9.32.0 → **0.9.33.0**。

### v0.9.32.0 — 🧰 工具選擇器支援大項（per-server）選取：三態全選開關

使用者需求:「chat ui的MCP enable/disable開關，除了有可關閉細項功能，
也要支援大項選取」。v0.9.14.0 的 🧰 面板只有**逐工具**勾選 — 想整個
關掉某個 server（例如暫時不用 windows-mcp 的 20+ 工具）要逐項點。

* **app.js — `renderChatToolsPanel()`**：server 群組標題從純文字 div
  變成 **`<label>` + 三態 checkbox**（`chat-tool-srv-cb`）：
  * 全開 → checked；全關 → unchecked；**部分 → indeterminate**（瀏覽器
    原生三態）。
  * 點擊**整組翻轉**：全開 → 全關；部分/全關 → 全開（indeterminate
    點擊由瀏覽器解析為 checked=true → 全開）。
  * 寫入**同一個** `localStorage['chat-disabled-tools']` disabled-set —
    細項/大項語意一致；之後新增的工具仍預設啟用。
  * server 翻轉與細項點擊後都 `renderChatToolsPanel()` 重繪 — 三態與
    逐項勾選永遠同步。
* **style.css** — `.chat-tool-srv` 變 flex label（可點整列）+
  `.chat-tool-srv-cb`（accent-color）。
* Tests — `tests/test_gui_chat_tools.py` 13 → **19**（三態 checkbox、
  tri-state 映射、整組翻轉、row 重繪同步、CSS、header label）；
  **19 passed**；`node --check` 通過。

`pyproject.toml` 0.9.31.0 → **0.9.32.0**。

### v0.9.31.0 — Telegram 可發送生成的圖片：`send_telegram_photo`（沙箱圖檔 → sendPhoto）

使用者需求:「telegram可以嵌入生成的圖片嗎」。原有 Telegram 工具只有
文字（`send_telegram_message` / `send_telegram_markdown`）— 生成的
圖檔（matplotlib 圖、貼圖、resize 產物）傳不出去。新增第 4 個工具：

* **`send_telegram_photo(chat_id, path, caption?)`**（stdio MCP server
  `telegram` 同程序內新增，`MCP_ACTIVE_SERVERS` 的 `telegram` 已在
  gate 名單內 — **重啟 GUI 後生效**）：
  * multipart/form-data POST 到 Bot API `sendPhoto`（純 stdlib
    urllib，新 `_post_bot_multipart` + 共用的 `_bot_post` 解析）。
  * **沙箱閘**：`path` 必須在允許根內（import ∪ filesystem WRITE ∪
    READ — 與內嵌圖 bridge 同一聯集的 env-only 鏡像解析，stdio
    server 保持輕依賴）；相對路徑（`uploads/x.png`）對 WRITE 根解析
    後**仍過 containment**（`uploads/../../escape.png` 被拒）。
  * 圖片副檔名白名單（png/jpg/jpeg/gif/webp/bmp）+ magic-byte 驗證
    + **10 MB**（Bot API photo 上限）；caption ≤1024 字元。
  * 回傳 `ok / chat_id / message_id / file / photo_width /
    photo_height / caption`。
  * allowlist 檢查先於任何 HTTP — 非白名單 chat_id 永不出網。
* **system prompt** — Telegram 觸發區塊新增 photo 條目（生成的圖、
  caption ≤1024、沙箱外檔案先複製進來）。
* Tests — **26 new** in `tests/test_telegram_photo.py`（multipart
  框架 ×2、沙箱閘 ×10、handler ×8、註冊 ×1、prompt ×2）；telegram
  server/bot + 圖片鏈 + 上傳迴歸合計 **221 passed**。

`pyproject.toml` 0.9.30.3 → **0.9.31.0**。

### v0.9.30.3 — 實測修 2：LLM 不知道 write sandbox 的實際路徑 → prompt 嵌入字面路徑

第二輪實測（13:56）「把桌面那張熊圖複製到 sandbox」：LLM 把副本放到
`C:\Users\humphery\bear_from_desktop.png`（**碰巧**在沙箱內 — 本機
`QDRANT_FILESYSTEM_*_ROOT` = `C:\Users\humphery`），但它宣稱「聊天訊息
本身無法直接內嵌顯示 PNG」且沒有嵌圖 — 因為 GUI 還跑在 v0.9.30.2
之前的程式上（regex + prompt 都是啟動時載入）。

* **根因**：v0.9.30.2 的 prompt 規則只說「QDRANT_FILESYSTEM_WRITE_ROOT
  的 uploads/」— LLM **看不到 env var**，不知道實際目錄，只好自己猜
  路徑（猜對是運氣）。修法：新增 module-level
  `_write_sandbox_hint()` — `_system_prompt()` 建構時以
  `_resolve_filesystem_root("QDRANT_FILESYSTEM_WRITE_ROOT")` 解析
  **字面路徑**嵌進規則（本機 = `C:\Users\humphery`）；無法解析時
  退回通用提示，永不 raise。
* 效果：LLM 下一輪就知道要把圖存進/複製到哪個**確切目錄**，不再猜。
* Tests — `tests/test_chat_images.py` 24 → **25**（prompt 必須含
  解析出的實際 write-root 字面路徑）；相關 5 檔 **139 passed**；
  `node --check` 通過。

`pyproject.toml` 0.9.30.2 → **0.9.30.3**。

### v0.9.30.2 — 實測修：全形標點黏路徑沒命中 + LLM 把圖存到桌面（沙箱外永不顯示）

使用者實測（2026-09-11 13:47）:「用 python 畫一張圖存到 sandbox」
未貼圖。LLM 把 `bear_matplotlib.png` 存到 **桌面**
（`C:\Users\humphery\OneDrive\桌面\`），回覆「檔案位置**：**C:\...」
— 兩個問題疊加，气泡完全沒有圖：

1. **regex 起點分隔類別太窄** — 路徑黏在全形冒號 `：` 後面，
   v0.9.30.1 的正向分隔（行首/空白/bracket/括號）不命中。
   **改前一字元驗證**：不再要求特定分隔字元 — 掃出 token 後檢查
   `txt.charAt(m.index - 1)`，前一字元是 `\w` / `/` / `\` 才跳過
   （mid-word 排除）；`：`、`。`、`，` 等任何 CJK 標點黏住的真路徑
   都能命中。
2. **圖存到沙箱外** — 即使 regex 命中，`read_sandbox_image` 也會
   以「outside the allowed sandbox roots」拒讀（安全閘永不放行）。
   修 **system prompt「Show images in the chat」規則**：要求 LLM
   把要給使用者看的圖**存進 filesystem write sandbox**（例如
   QDRANT_FILESYSTEM_WRITE_ROOT 的 uploads/）— 桌面/OneDrive 等
   沙箱外路徑**無法**在聊天内嵌顯示；檔案已在沙箱外時先用
   filesystem copy 工具複製進來再嵌路徑。
3. 失敗佔位改善 — bridge 拒讀時佔位框的 `alt` 顯示 `⚠ <原因>`，
   使用者看得到為什麼沒圖（而不是只剩一個不明灰框）。
* Tests — `tests/test_chat_images.py` 23 → **24**（前一字元驗證
  regex、prompt 含 write-sandbox/Desktop 強化字樣、失敗 alt 帶原因）；
  相關 5 檔 **138 passed**；`node --check` 通過。

`pyproject.toml` 0.9.30.1 → **0.9.30.2**。

### v0.9.30.1 — 聊天訊息嵌入 PNG：裸路徑 + uploads/ 相對路徑 + LLM 主動嵌圖規則

使用者需求:「聊天訊息本身支援嵌入 PNG 圖片」。v0.9.30.0 只認
`[已上傳]` 標記行與 markdown 圖；本輪把缺口補齊：

* **app.js — `renderSandboxImages()` regex 統一**：氣泡文字內的
  **裸絕對路徑**（LLM 直接回 `C:/.../chart.png`，無標記、無
  markdown）與 **`uploads/` 相對路徑**（`save_pasted_image` 的
  `relative_path`）現在也內嵌顯示 — 一條 regex：路徑 token 前面必須
  是行首/空白/bracket/括號（不會 mid-word 誤配），副檔名必須是 token
  結尾（`a.png.bak` / `a.pngx` 不誤配）。`_isLocalImgPath` 同步接受
  `uploads/` 前綴；(a)/(b) 兩路徑 dedupe 合併。
* **`read_sandbox_image` 相對路徑解析**：非絕對路徑對 filesystem
  WRITE 沙箱根解析後**仍過 roots-containment 檢查** — 相對路徑穿越
  （`uploads/../../escape.png`）一樣被拒。
* **system prompt（`_system_prompt(tools_enabled=True)`）— 「Show
  images in the chat」規則**：LLM 產生/引用沙箱內圖檔時必須把
  **路徑或 `![alt](<path>)` 嵌進最終訊息**（UI 才能內嵌渲染）；
  明確禁止「純文字描述圖、丟掉路徑」（v0.9.5.2 drawio 教訓）；
  不得虛構路徑。Legacy prompt 不含此規則。
* Tests — `tests/test_chat_images.py` +6（相對路徑 OK / 穿越拒絕、
  prompt tools-mode 含規則 / legacy 排除、JS 裸路徑 regex / uploads
  前綴）；**137 passed**；`node --check` 通過。

`pyproject.toml` 0.9.30.0 → **0.9.30.1**。

### v0.9.30.0 — 聊天气泡內直接顯示圖檔（上傳/貼上路徑 → 內嵌 data-URL 預覽）

使用者需求:「chat的談話內容可以直接顯示圖檔嗎」。聊天訊息內的圖片
路徑（`[已上傳] C:/... .png` 標記行、markdown `![alt](本機路徑)`）
在渲染時自動轉成內嵌預覽 — 圖片直接顯示在气泡內，不再是純路徑文字。

* **`gui/api.py` — `Api.read_sandbox_image(path)`**（新橋接方法）：
  * 讀取**允許沙箱根內**的圖片（import ∪ filesystem READ ∪ WRITE —
    與 `describe_image` 完全同一聯集，`_allowed_file_roots()` +
    `_is_within()` 重用）→ base64 + mime 回傳 → 前端轉 `data:` URL。
  * 只服務圖片副檔名（png/jpg/jpeg/gif/webp/bmp）+ magic-byte 驗證
    （改名的文字檔永不會以破圖形式進气泡）；20 MB 上限。
  * 沙箱外 / 非圖片 → 明確錯誤 — 這**永遠不會變成任意檔案讀取**。
* **`gui/assets/app.js`** — `renderChat()` 尾掛 `renderSandboxImages()`
  後處理 pass（appendChatMsg 走 renderChat → 自動覆蓋）：
  * (a) markdown 渲染出的 `<img>` 其 src 是本機路徑 → 轉 data URL。
  * (b) 純文字 `[已上傳]`/`[uploaded]`/`[已上传]`/`[アップロード済]`
    標記行（bracket 形狀 4 語系共用，regex 通用）→ 文字下方附加
    `<img class="chat-inline-image">` 預覽；**文字保留**（LLM 歷史
    仍讀得到路徑）。
  * `_loadSandboxImage()` 經 pywebview bridge / web shim（`POST
    /api/read_sandbox_image`）取 base64；data URL 以 FIFO Map 快取
    （上限 12）避免每次 renderChat 重讀磁碟；失敗 → 半透明佔位
    （`chat-image-failed`），不破壞气泡文字。
  * 遠端 src（http/https/data/blob）完全不受影響。
* **`style.css`** — `.chat-inline-image`（block、max-height 320px、
  圓角+陰影）+ `chat-image-failed` 失敗佔位樣式。
* 安全：沙箱成員檢查在 **Python 端**強制；XSS 無新增面（img.src 只
  會被設成 Python 回的 data URL，路徑文字本來就在 textContent）。
* Tests — **17 new** in `tests/test_chat_images.py`（bridge 讀取/
  mime/magic/拒絕/上限/沙箱拒絕 ×11、JS 接線 ×6）；
  chat_images + clipboard + web_upload + web_server 合計 **110 passed**；
  `node --check` 通過。

`pyproject.toml` 0.9.29.0 → **0.9.30.0**。

### v0.9.29.0 — 聊天介面支援剪貼簿貼上圖片（Ctrl+V → 自動存 uploads/ → 路徑插入輸入框）

使用者需求:「聊天介面支援貼上圖片」。在聊天輸入框直接 **Ctrl+V**
貼上剪貼簿的圖片（截圖複製、瀏覽器「複製圖像」等）→ 圖像自動存進
filesystem 寫入沙箱 `uploads/` → 路徑以 `[已上傳] <path>` 插入輸入框
（與 📎 完全相同的形式）→ LLM 可用 `describe_image` 讀圖。

* **JS（`gui/assets/app.js`）** — `onChatPaste` 綁在聊天輸入框
  （`bindPaste()` 於 `bindEvents` 接線）：
  * `clipboardData.items` 過濾 `kind==='file'` + `type image/*` →
    `getAsFile()`；**沒有圖時完全不介入**（純文字貼上照舊）；
    有圖才 `preventDefault()`（丟掉空的 image 偽文字）。
  * **Web 模式**：直接重用既有 `uploadFiles([file])` → `/upload`
    multipart → 伺服器端時間戳命名 → toast + 路徑插入（完整既有
    鏈路，含 4 語系 toast）。
  * **pywebview GUI 模式**（原本無任何上傳途徑）：clipboard blob →
    `FileReader` → base64 → **新橋接 `Api.save_pasted_image`** →
    寫入同一個 uploads/ 沙箱 → toast + `[已上傳] <path>` 插入。
* **`gui/api.py` — `Api.save_pasted_image(data_base64, filename)`**
  （新公開橋接方法）：
  * 寫入**同一個** `<QDRANT_FILESYSTEM_WRITE_ROOT>/uploads/` 目錄
    （與 web 上傳端點、FilesystemLocalHost 同一沙箱）— LLM 可直接
    讀取 / OCR。
  * **magic-byte 嗅探**（`_sniff_image`）強制正確副檔名 — 僅
    PNG（`\x89PNG`）/ JPEG（`\xff\xd8\xff`），其他 → 明確錯誤
    「PNG/JPEG only」。
  * **名稱清理**（`_sanitize_paste_name`）— basename-only、保留
    `[A-Za-z0-9._-]`、強制嗅探副檔名；時間戳前綴 + 碰撞 `-N` 後綴
    （與 /upload 相同慣例）。
  * **上限**：base64 長度 40M 字元（~30 MB 解碼）sanity cap；
    超過回 "too large"。
* 安全：路徑穿越名稱永不逃出 uploads/（basename + 字元白名單）；
  base64 `validate=False`（寬鬆 padding，同 describe_image 慣例）。
* Tests — **18 new** in `tests/test_clipboard_paste.py`（sniff/sanitize
  單元 ×8、Api 寫入/副檔名/拒絕/碰撞/穿越 ×8、JS 接線 6 —
  `node --check` 通過；web_upload 24 + web_server 51 迴歸全綠 =
  **93 passed**）。

`pyproject.toml` 0.9.28.3 → **0.9.29.0**。

### v0.9.28.3 — 🎉 live 全通：憑證安控元件安裝（MEGASECCAPIx64）+ 四項 live 查詢實測成功

使用者安裝電子憑證並執行官方憑證下單元件後，實測 **四項 live 查詢
全數打通**（2026-09-11 11:58，實機回傳）：

| 工具 | 實測結果 |
|---|---|
| `stock_get_kline(2330, day, 5)` | ✅ 結構化日 K：20260910 台積電 開 2445 / 高 2455 / 低 2435 / **收 2450** / 量 19,835 張（-0.61%）；Title/rows 已解析 |
| `stock_get_account_summary()` | ✅ 帳務查詢成功（`result=0 執行成功`；新帳戶庫存為空） |
| `stock_get_quote(2330)` | ✅ 即時五檔：**買 2410×588 / 賣 2415×414**（@11:58:07） |
| `stock_list_orders()` | ✅ 委託查詢成功（ackList/fills 空 — 無未成交委託） |

* **簽章元件安裝** — 套件內 `MEGASECCAPIx64.exe`（官方「Windows 用
  憑證下單元件」，64 位元；`MEGAAPI目錄檔案說明.pdf` §1）以
  UAC 提權執行一次後，`EnableMEGACA` 不再回「簽章元件尚未安裝」→
  交易主機登入打通（`Connect` → `OnConnected` → `LogonProxy` →
  `SetAccount`）。
* **`json` import 修復** — `src/mega_stock_tools.py` 模組級
  `import json` 遺漏（`query()` / `kline()` 的 JSON 解析在 live
  路徑炸 NameError — 競態修復後首次踩到；mock 路徑不觸發所以
  測試沒抓到）。
* **K線結構化** — `MegaLiveSession.kline()` 解析 API 回傳的 JSON
  字串 → `{fn, title, rows}`（日期/股票代號/股票名稱/開高低收/
  成交量/漲跌/漲幅/市值/次日開盤參考價/…），失敗時降級 raw。
* Tests — 93 passed（stock 29 + telegram 26 + webhook 38）。

`pyproject.toml` 0.9.28.2 → **0.9.28.3**。

### v0.9.28.2 — live 聯調 hotfix #2：交易登入需要電子憑證（EnableMEGACA 提前到 Connect 之前）

使用者貼上第二輪 live 實測 log（2026-09-11 11:46）：交易主機連線競態
已修好（OnConnected → LogonProxy 正常觸發），收到真實的登入失敗原因
**「簽章元件尚未安裝或無有效的憑證」**。

* **根因** — 交易主機的 `LogonProxy` **登入本身**就需要簽章憑證：
  官方範本的順序是 `EnableMEGACA(.pfx)` → `Connect` → `OnConnected`
  → `LogonProxy`（teach5 步驟 4 在 Connect **之前**）。v0.9.26.0
  把 EnableMEGACA 只接在下單前（`place()` 內）— 登入階段缺憑證。
* **修復**（`MegaLiveSession.ensure_order`）— `EnableMEGACA`
  提前到 `Connect` **之前**（`MEGA_STOCK_CERT_PATH` 已設定時）；
  載入失敗回明確錯誤（含 GetLastErrorMsg）。登入失敗訊息含
  「簽章/憑證」且未設憑證時，附加指引（設定 MEGA_STOCK_CERT_PATH/
  PASSWORD；行情主機不需憑證 — 實測行情登入 OK）。
* 現況：**行情（報價快照）live 可用**（不需憑證）；交易登入（K線/
  帳務/委託/下單）待使用者安裝電子憑證後即可用。
* Tests — 55 passed（stock 29 + telegram_bot 26）無回歸。

`pyproject.toml` 0.9.28.1 → **0.9.28.2**。

### v0.9.28.1 — live 聯調 hotfix：行情登入成功（Logon OK + 64,471 商品）＋ 交易連線競態/Big5 解碼修復

使用者貼上首日 live 實測 log（2026-09-11 11:06）：行情主機
119.31.179.86:34567 **登入成功**（`OnLoginReply[1:Logon OK!]`）、
64,471 檔商品資料下載完成、`stock_get_quote` live 回 OK；但
`stock_get_kline` 回「連線交易主機失敗」且 log 出現 Big5 解碼錯誤。
三個修復：

* **交易連線競態修復**（`MegaLiveSession.ensure_order`）—
  `Connect()` 是**非同步**的：live 實測它立刻回 False，但
  `OnConnected` → `LogonProxy` 隨後才到（log 的
  `pyOrderAdapter::LogonProxy`）。官方 testPyOrderAPI 範本也不檢查
  Connect 回傳值。改為**無視 Connect 回傳值**，一律等待
  `_logon_event`（由 `OnLogonResponse` 或 OnConnected 的同步失敗
  設定）；逾時錯誤改帶 `IsConnected()` / `GetLastErrorMsg()` 診斷。
* **Big5 解碼防禦**（`_safe_str` + CB 覆寫）— SDK 的 ctypes 回呼
  wrapper 硬編碼 `decode('Big5')`，UTF-8 中文（「未授權使用」等）
  直接炸回呼（`Exception ignored on calling ctypes callback`）→
  我們的 `OnLogonResponse` 鉤子從未被呼叫。`_OrderApi` / `_QuoteApi`
  覆寫 `OnLogonResponseCB` / `OnReplyNewOrderCB` /
  `OnReplyCancelOrderCB` / `OnRejectOrderCB` / `OnFillCB`：
  big5 → utf-8 → big5-replace 三段式防禦解碼後再轉發到我們的事件
  鉤子。
* **行情快照強化** — 首次 `Logon` 觸發 64k 商品下載（實測 ~9s），
  快照前先等 `_contracts_done`（20s 上限，未完成退回直接訂閱）；
  `stock_get_quote` 的「盤外」note 改為條件式（快照含 bid/last 時
  不再誤導）。
* Tests — +3 in `tests/test_stock_host.py::TestSafeDecode`（big5 /
  utf-8 fallback / garbage 不 raise）。相關迴歸 telegram 26 +
  webhook 38 = **93 passed**。

`pyproject.toml` 0.9.28.0 → **0.9.28.1**。

### v0.9.28.0 — live 下單第四道閘：Telegram 強制同意確認（送請求 → 回覆「同意 STK-XXXX」才送單）

使用者需求:「mcp stock 下單前需強制使用 telegram 聯絡指定對象並取得
同意才可以進行（預設 chat_id <YOUR_TELEGRAM_CHAT_ID>）」。

* **`src/agent/telegram_bot.py` — 審批登記表**：
  * `request_order_approval(chat_id, summary, timeout_s)` — 送確認
    請求（唯一代碼 `STK-XXXX`，`secrets.token_hex(2)`）並**阻塞等待**
    回覆；登記先行（回覆可能比送達更早抵達）；`finally` 移除條目。
  * 解析規則（bot daemon 於一般聊天流程**之前**消費）：
    含代碼 + 拒絕詞（拒/取消/…）→ `denied`（拒絕優先）；含代碼 +
    同意詞（同意/確認/approve/…）或**僅回覆代碼** → `approved`；
    含代碼但語意不明 → `denied`（金錢安全優先）；無代碼的短回覆
    「拒」→ `denied`（僅一筆 pending 時）；其他 → 不消費（一般
    聊天繼續，等待至逾時）。
  * **心跳檢查**：`_last_poll_ts` 由 bot daemon 的 `poll_once` 更新；
    逾 30s 無心跳 → `request_order_approval` 直接回明確錯誤（bot
    daemon 是 getUpdates 的唯一消費者 — 沒有它同意回覆永遠收不到，
    提示使用者確認 `QDRANT_TELEGRAM_BOT_POLLING=on`）。
* **bot daemon 攔截**：`_handle_update` 在白名單檢查後、一般 LLM
  聊天流程前先呼叫 `_try_resolve_approval(chat_id, text)` — 同意
  回覆被「消費」（不進聊天、不觸發 LLM、不回覆），並喚醒等待中的
  下單閘。
* **`src/mega_stock_tools.py` — live 下單第四道閘**：
  `_place_with_consent` → `_telegram_approval`（阻塞等待）→ 核准才
  呼叫 `SendNewOrderEx`。確認人：
  `MEGA_STOCK_ORDER_APPROVAL_CHAT_ID`（預設 = `TELEGRAM_ALLOWED_
  CHAT_IDS` 第一個 = **<YOUR_TELEGRAM_CHAT_ID>**；無白名單時 fallback 亦是
  <YOUR_TELEGRAM_CHAT_ID>）；逾時 `MEGA_STOCK_ORDER_APPROVAL_TIMEOUT`（預設 180s，
  10-900）；`MEGA_STOCK_ORDER_APPROVAL=off` 可明確關閉（預設開）。
  同意結果在回覆帶 `consent` 欄位（`approved via Telegram` /
  `denied`）。拒絕/逾時/未經同意 → **絕不送單**，回覆明確錯誤。
* 刪單（`stock_cancel_order`）不過同意閘（降低風險的操作，不需
  核准）。
* `.env.example` / live `.env` — `MEGA_STOCK_ORDER_APPROVAL` /
  `MEGA_STOCK_ORDER_APPROVAL_CHAT_ID=<YOUR_TELEGRAM_CHAT_ID>` /
  `MEGA_STOCK_ORDER_APPROVAL_TIMEOUT` 區塊（live 預設開 + 指定
  chat）。
* System prompt stock block 補同意閘說明（LLM 得知「輪詢未運行」
  錯誤要如實告知使用者）。
* Tests — +10 in `tests/test_telegram_bot.py`（審批登記表：同意/拒絕
  /僅代碼核准/不明確拒絕/其他 chat 不消費/**daemon 攔截同意回覆
  不進聊天**/無心跳錯誤、engine 閘：denied 擋單/approved 過閘到
  broker/off 跳過/**預設 chat_id <YOUR_TELEGRAM_CHAT_ID>** — 全 fake client，
  零真實網路）。相關迴歸 stock 26 passed。

`pyproject.toml` 0.9.27.0 → **0.9.28.0**。

### v0.9.27.0 — Telegram 雙向溝通：bot polling daemon（你傳訊息 → Agent 執行 → 回覆自動送回）

使用者問題:「目前 telegram 可以雙向溝通嗎」— 答案先前是「單向」
（v0.8.1 只有 send_telegram_message / send_telegram_markdown /
list_allowed_chats 三個出站工具，入站的 getUpdates 沒有任何人讀取）。
本版補上入站半邊：

* **`src/agent/telegram_bot.py`（新）** — 與 scheduler.py / webhook.py
  同型的核心模組：
  * `TelegramBotClient` — stdlib urllib 的 Bot API 薄客戶端
    （`getUpdates` 長輪詢 10s + `sendMessage` 4096 字元分塊）
  * `make_telegram_on_message(agent_factory)` — 回 `handle(chat_id,
    text) -> reply`：訊息過 `Agent.chat_turn`（**完整工具面** — LLM
    可先呼叫 MCP 工具再回答）、每個 chat 一個持久 session
    `tgbot-<chat_id>`（多輪上下文）、訊息加防雙送框架
    （「你的回覆會由 bot 自動送回 — 不要自行呼叫
    send_telegram_message」）、chat_turn 崩潰降級為 `[內部錯誤]`
    短回覆、tuple/dict 雙契約
  * `TelegramBotDaemon` — daemon thread 長輪詢：`poll_once()`
    取 getUpdates 批次 → 白名單過濾（**fail-closed**：空名單 =
    全忽略）→ handler → `sendMessage` 回覆；offset 進階 ack
    （at-least-once）；錯誤退避（5× 間隔）不壓垮 daemon；stop()
    分段 sleep 保持響應
* **Agent 接線** — `telegram_bot` lazy property +
  `start_telegram_bot()` / `stop_telegram_bot()`（idempotent）+
  `Agent.shutdown()` 停止；每則訊息建 fresh Agent（與主執行緒不共享
  OpenAI client state）
* **開機自動恢復** — `gui/api.py::_resume_telegram_bot` 掛進
  `_prewarm_agent`：`QDRANT_TELEGRAM_BOT_POLLING` != off（預設 on）+
  `TELEGRAM_BOT_TOKEN` + `TELEGRAM_ALLOWED_CHAT_IDS` 三者齊備時自動
  啟動；全部失敗吞掉（non-fatal）
* **零開埠** — 長輪詢是純出站 HTTPS（api.telegram.org），本機即可
  運作（對比 setWebhook 方案需要 https + 公網）
* `.env.example` / live `.env` — `QDRANT_TELEGRAM_BOT_POLLING` /
  `QDRANT_TELEGRAM_BOT_POLL_INTERVAL` 區塊（live 預設 on）
* Tests — **16 new** in `tests/test_telegram_bot.py`（poll_once 批次
  處理 + 白名單過濾 + fail-closed + offset ack 跨批次 + 非文字訊息、
  handler 持久 session / 防雙送框架 / 崩潰降級 / 空回覆、Agent wiring
  start-stop、boot resume 五路徑 + prewarm 整合 — 全部 fake client，
  零真實網路）。

`pyproject.toml` 0.9.26.0 → **0.9.27.0**。

### v0.9.26.0 — 兆豐 MegaAPI live 接線完成（元件安裝 + 真實查詢/下單路徑；API 權限開通 115/09/09）

使用者取得兆豐 API 權限開通通知（分公司 7000 / 證券帳號 1673976；
證期交易主機 spapi.emega.com.tw:56789；證期即時行情
119.31.179.86:34567；開通次日 08:30 起可登入）。

* **元件安裝** — `SpeedyAPI_PY.7z`（官方
  https://www.emega.com.tw/download/API/SpeedyAPI_PY.7z）解壓後將
  `megaSpeedy/` 資料夾（`megaSpeedyAPI_64.dll` + `spdOrderAPI.py` +
  `spdQuoteAPI.py` + `Temp/` 74 檔 + `speedyAPI_config.json`）安裝至
  專案根；`from megaSpeedy.spdOrderAPI import spdOrderAPI` 可用
  （DLL 以 `os.path.dirname(__file__)` 載入，跟著資料夾走）。
* **真實函式介面**（從 `spdOrderAPI.py` 原始碼取得，取代先前未經
  文件確認的猜測）：
  * 連線 `Connect(IP, Port, TimeoutSec)` + `LogonProxy(身分證字號
    大寫, 電子登入密碼, 帳號)` + `SetAccount('TWSE', 分公司, 帳號)`
  * 下單 `SendNewOrderEx(Market, UDD, Symbol, Price, Side, OrderQty,
    OrderType, TimeInForce, TradingSession, PositionEffect,
    TWSEOrdType)` — Side B/S、OrderType L/M、TIF R(ROD)/I(IOC)/F(FOK)、
    TS N(正常)/O(盤後零股)/I(盤中零股)/A(定價)、PE A(Auto)、
    TWSEOrdType 0(現股)/3(融資)/4(融券)/A(現沖)；回傳 NID（0=失敗
    → `GetLastErrorMsg()`），OrderID 由 `OnReplyNewOrder` 事件回
  * 刪單 `SendCancelOrderEx(Market, UDD, Symbol, Price, Side,
    OrderID, TradingSession, TWSEOrdType)` — 需先從委託查詢取得
    symbol/price/side（`_live_cancel` 先查 `queryStkOrder`）
  * 查詢 `queryStkOrder` / `queryStkMatch` / `makeStockAccountInquriy`
    — `query_param = {'branch_id','cust_id','apcode','market',
    'qry_type','nocsint'}`（官方樣本值）；回 JSON 字串
  * K線（走交易連線）`GetDKChartData` / `GetAdjustedDKChartData` /
    GetMK/WKChartData
  * 行情 `spdQuoteAPI.Logon(IP, Port, ID, Password, DownloadContracts)`
    → `Subscribe(Exchange, Symbol)` / `SubscribeContract(Contract)`
    → `OnOrderBook`（Msg.BidPrice1-5 / AskPrice1-5 / DerivedBid）/
    `OnTrade`（MatchPrice/MatchQty）
* **`MegaLiveSession`**（`src/mega_stock_tools.py`）— 事件 wrapper
  （`OnConnected` → `LogonProxy`（官方 testPyOrderAPI pattern）、
  `OnLogonResponse` → `SetAccount`、`OnReplyNewOrder` /
  `OnReplyCancelOrder` / `OnRejectOrder` / `OnFill` 事件收單）+
  同步等待（`threading.Event` + 連線 15s / 回報 10s / 行情快照 3s
  超時）+ lazy 長駐連線（FIsLogon 檢查）+ thread-safe lock。SDK
  import 時 stdout 重導（banner 會污染 stdio MCP JSON-RPC）。
* **live 路徑**：`stock_get_kline`（GetAdjustedDKChartData）、
  `stock_get_account_summary`（makeStockAccountInquriy）、
  `stock_list_orders`（queryStkOrder + queryStkMatch）、
  `stock_get_quote`（行情訂閱快照，盤中）→ **live 查詢可用**（明天
  開通後）。`stock_place_order` / `stock_cancel_order` → **真實
  SendNewOrderEx / SendCancelOrderEx 接線完成**，三道閘不變
  （mode=live + MEGA_STOCK_TRADING_ENABLED=on + confirm=true）+
  憑證閘（`MEGA_STOCK_CERT_PATH/PASSWORD` — 查詢不需憑證、下單
  需要）。
* **實機驗證**（開通前）：`MEGA_STOCK_MODE=live` + 假帳密 →
  `Connect(spapi.emega.com.tw:56789)` 成功送達，主機回「未授權使用」
  （預期 — 明日 08:30 開通）；錯誤乾淨捕捉、mock 完整不受影響、
  26 tests 全綠。
* `.env.example` / live `.env` — live 連線區塊（已知值已填：帳號
  1673976、分公司 7000、主機預設官方值；`MEGA_STOCK_ID` /
  `MEGA_STOCK_PASSWORD` 留空由使用者填入 — 身分證字號 + 電子登入
  密碼不代填）。`MEGA_STOCK_MODE=mock` 保持（開發/測試預設）。
* 結構修正：`_find_order_row`（queryStkOrder 回傳 JSON 的防禦性
  遞迴搜尋 — 真實欄位名待首次 live 查詢確認）移至檔尾（先前誤插
  在 engine class 中間把 place_order/cancel_order 截斷的回歸）。

`pyproject.toml` 0.9.25.3 → **0.9.26.0**。

### v0.9.25.3 — llama.cpp embedding server tuning:單查詢 153→128ms(-16%)、64 條批次 49.4s→43.6s(-12%)、prompt cache thrashing 修除 + CPU bat pooling 正確性修正

使用者問題:「優化 llama.cpp 提升回應速度」(C:\llama.cpp)。

**發現(2026-09-10 實測,i5-1240P / RX 5700 XT / Qwen3-Embedding-8B Q4_K_M):**

1. **Vulkan server 兩週前已掛**:server.err.log 尾端
   `ggml_vulkan: device fault on Vulkan0: No fault detected`(uptime
   4.8h);啟動後不久即見大量 `making room for prompt cache entry`
   evictions(0.4-57 MiB entries 不斷進出)。embedding 工作負載每段
   文字都不同,server 端 prompt cache 是純開銷,同時也是 VRAM churn /
   device fault 的頭號嫌疑犯。server 死後 `EMBEDDING_BACKEND=auto`
   悄悄 fallback 到 Ollama Cloud(品質+延遲雙降)。
2. **`.env` 的 `LLAMACPP_EMBED_URL=http://localhost:8080` 踩到
   v0.9.25.1 同款 ::1 陷阱**:embedder 每次 `requests.post` 都新開
   HTTP 連線,bare `localhost` 先解析到 `::1`,每次 embed/probe 多付
   ~2 秒(Qdrant 修復的同一個根因,第二個受災戶)。

**基準測試(5 配置;工具:`C:\llama.cpp\_bench.py` +
`C:\llama.cpp\_run_bench.ps1`,12x 單查詢 + 3x 64 條 ~780 字元
CJK+EN 混合切片):**

| 設定 | 單查詢 avg | 批次 64 條 | ms/條 |
|---|---|---|---|
| vulkan_old(原 bat 旗標) | 153.4 ms | 49.4 s | 804 |
| vulkan_opt(+cache-ram 0 / ngl 99 / ctx 4096) | 136.0 ms | 48.1 s | 772 |
| **vulkan_opt_fa_ub1024(採用)** | **128.5 ms** | **43.6 s** | **704** |
| vulkan_opt_fa_ub2048 | 142.0 ms | 43.4 s | 694 |
| cpu_opt(對照) | 449.9 ms | >300 s 逾時 | >4700 |

**變更:**

* `C:\llama.cpp-vulkan\start-llamacpp.bat` — 旗標改為
  `--ctx-size 4096 -ngl 99 -fa on -ub 1024 --threads 12 --cache-ram 0`,
  註解記錄實測數據與 device fault 修復原理。`--ctx-size 4096`(=
  1024 ctx/slot)同時讓 500 字元 CJK 切片(~750 Qwen tokens)不再被
  512 token/slot 截斷。
* `C:\llama.cpp\start-llamacpp.bat` — **正確性修正**:原本
  `--pooling mean` 會產出與 Vulkan server 語意不同的向量(同樣
  4096-dim 形狀 → 靜默品質錯配);改為 `--pooling last --embd-normalize 2`
  (Qwen3-Embedding-8B 正確組合),並移除 `--log-disable`(保留
  device fault 診斷日誌)。
* `src/ingest/embedder.py` — `LLAMACPP_EMBED_URL` 經
  `_normalize_loopback_url()` 正規化(bare `localhost` → `127.0.0.1`;
  `[::1]`/區網 IP/主機名不動;與 QDRANT_URL 的 v0.9.25.1 同模式)。
* `.env` / `.env.example` — `LLAMACPP_EMBED_URL=http://127.0.0.1:8080/v1/embeddings`。
* 測試 — `tests/test_qdrant_url_loopback.py` 新增
  `TestEmbedderUrlNormalization` ×3(localhost 改寫 / [::1] 保留 /
  區網 IP 保留)。
* `ARCHITECTURE.md` — §1.2 arc v0.9.25.3 條目;header 版本行;
  §2.6 embedder 段加「server tuning + URL normalisation」註記;
  §11 除錯表加 2 行(EVERY embed ~2 s 的 loopback 陷阱 /
  device fault 的 cache-ram 對策);§11.1 + §12.2 的 RDNA 1
  「CPU is fine」舊建議以實測數據改正(Vulkan 勝出);§12.1
  Windows gotcha 加 localhost/embedder 與 bat 檔純 ASCII 兩條;
  footer last-reviewed 2026-09-10。

`pyproject.toml` 0.9.25.2 → **0.9.25.3**。

### v0.9.25.2 — 文件同步:ARCHITECTURE.md / CLAUDE.md Qdrant Gotchas / 新增 `C:\qdrant\README.md`(Qdrant 本機運維說明)

純文件變更,無程式碼/測試異動:

* **`C:\qdrant\README.md`(新)** — Qdrant 本機安裝的運維說明:
  binary 版本(1.19.0)、啟動方式(`start-qdrant.bat` 前景 /
  `launch-detached.ps1` 背景分離)、埠(6333 HTTP / 6334 gRPC)、
  儲存與快照路徑、collections 概況、健康檢查指令、
  loopback 速度結論、伺服器掛掉時的重啟步驟。
* **`ARCHITECTURE.md`** — §1.2 沿革新增 v0.9.25.1 / v0.9.25.2 條目、
  §1.3 資料儲存表的預設 URL 更新為 `127.0.0.1:6333`、§11 除錯
  表新增「每次 Qdrant 呼叫 ~2 s」症狀列、§12.1 Windows quirks
  新增 `localhost` → `::1` 說明、footer 日期。
* **`.clinerules/CLAUDE.md`** — 工作區版本行更新至 v0.9.25.2 +
  §5「Qdrant-Specific Gotchas」新增 loopback 延遲陷阱條目。

`pyproject.toml` 0.9.25.1 → **0.9.25.2**。

### v0.9.25.1 — hotfix:Qdrant 每次查詢多付 ~2 秒(localhost 解析到 ::1 的 loopback IPv6 拒絕延遲)— 改用 127.0.0.1 實測 2073ms → 29ms(~71×)

使用者問題:「是否可以優化 qdrant 的執行速度(C:\qdrant)」。
量測 + 對症修復,而非調參:

**量測**(file_docs,167,925 點 × 4096 維,i5-1240P / 32GB / NVMe):

| 路徑 | 延遲 |
|---|---|
| qdrant-client query_points,`QDRANT_URL=http://localhost:6333`(現況) | **mean 2073 ms** |
| qdrant-client query_points,`http://127.0.0.1:6333` | **mean 29 ms** |
| raw requests POST /points/query(127.0.0.1,keep-alive) | mean 7 ms |
| Qdrant server 端處理時間(actix request log) | 10-19 ms |
| ef=32 / ef=400 / exact=true 對照 | 無顯著差異(瓶頸不在搜尋本身) |

**根因鏈**(全部實測確認):

1. `getaddrinfo('localhost')` 回傳 **`::1` 優先**、`127.0.0.1` 次之。
2. 本機 raw TCP connect 到 `::1:6333` 要 **~2050 ms 才被 REFUSED**
   (loopback IPv6 異常;正常應 <1 ms — RDP session / 防火牆 WFP
   環境常見)。IPv4 端 127.0.0.1 connect 僅 0.7 ms。
3. **qdrant-client 的 REST 層每個請求都重建 HTTP 連線**(無
   keep-alive 重用)→ 每一次 search / get_stats / group-by 都先撞
   `::1` 的 2 秒拒絕延遲,再 fallback 到 IPv4。
4. Qdrant server 本身完全沒問題(啟動 banner 顯示 actix 15 workers、
   gRPC 6334 正常開啟;所有請求 server 端 0.6-19 ms 完成)。
   這也解釋了 v0.6.1 註解裡「get_collection 要 ~2 s」「list_documents
   ~3 s」的舊觀察 — 同一個根因,不是 optimizer 忙碌。

**修復**(config 層一次性涵蓋所有入口 — QdrantStore / LogStore /
GUI readiness 都讀 `config.QDRANT_URL`):

* `src/config.py` — 新增 `_normalize_loopback_url()`:僅把 URL 內的
  `//localhost:` 換成 `//127.0.0.1:`(Qdrant 綁 0.0.0.0,IPv4 literal
  連到同一台 server);`[::1]` / 遠端主機名 / 區網 IP 一律不動。
* `.env` / `.env.example` — `QDRANT_URL=http://127.0.0.1:6333` +
  說明註解。

**不做的事**(量測後判定不需要):scalar int8 quantization
(server 端搜尋僅 10-19 ms,167k 點 + 32GB RAM 無壓力;加 int8 副本
反而多吃 ~690MB RAM)、gRPC(grpcio 未安裝,HTTP 修好後 29ms 已足
夠;若未來 collection 成長到 >1M 點再考慮)、HNSW ef 調整(與瓶頸
無關,維持 server 預設)。

* 新測試:`tests/test_qdrant_url_loopback.py` ×6(helper 純函數
  replace 行為、http/https、127.0.0.1/區網 IP/[::1]/主機名不動、
  .env 載入後 config 值、monkeypatch 環境 + reload 鏈、預設值)。
* 實測證明:重跑 `QdrantStore().search()` + `get_stats()` +
  `list_documents()`,全部從 ~2-3 s 降到 ~20-30 ms。

`pyproject.toml` 0.9.25.0 → **0.9.25.1**。

### v0.9.25.0 — MCP 核心功能：webhook 觸發器（每個 task 專屬 URL + access key，HTTP 呼叫即啟動預設 prompt；設定方式與排程一致）

使用者需求:「以 MCP 方式，製作核心功能，有別於排程式方式啟動
prompt，提供 trigger 方式，透過每個 task 各自的 URL 和 access key，
啟動預先設定好的 prompt，設定如同排程設定一樣」。

新增 **FOURTEENTH in-process LocalHost** `WebhookLocalHost`（server
`webhook-local`）— 10 個工具，核心邏輯在 `src/agent/webhook.py`
（與 scheduler.py 平行）:

| 工具 | 說明 |
|---|---|
| `add_webhook_trigger(name, prompt, max_fires?, expires_at?)` | 建立觸發器；**回傳專屬 URL + access key（僅顯示一次）**；自動啟動監聽器 |
| `list_webhook_triggers(enabled_only?)` | 列出全部（key 遮罩） |
| `get_webhook_trigger(trigger_id)` | lifecycle 快照（key 遮罩） |
| `get_webhook_trigger_url(trigger_id, reveal?)` | 取回 URL + key（`reveal=true` 顯示完整 key） |
| `delete_webhook_trigger` / `enable_webhook_trigger` / `disable_webhook_trigger` | 生命週期管理（停用後 URL 回 403） |
| `run_webhook_trigger_now(trigger_id, payload?)` | 手動執行一次（模擬 HTTP 呼叫，寫 history + session） |
| `list_webhook_trigger_runs` / `get_webhook_trigger_errors` | 執行記錄 / 失敗查詢（鏡像排程的 runs/errors） |

**運作方式** — 每個觸發器持有一個**隨機 access key**（`secrets
.token_urlsafe(32)`，43 字元，建立時一次性生成）；呼叫 `GET` 或
`POST http://<host>:<port>/trigger/<id>?key=<key>` 即以 `Agent
.chat_turn`（完整工具面）執行預先設定好的 prompt：

* **Access key 安全**：constant-time 比對（`hmac.compare_digest`）；
  錯 key → 401、未知 id → 404、disabled/exhausted/expired/failed →
  403；list/get 一律遮罩 key（`get_webhook_trigger_url(reveal=true)`
  才取回完整值）；非 loopback 綁定記大聲 WARNING（每個 URL 都是
  遠端 prompt 執行端點，僅受 key 保護）。
* **payload 注入**：POST body `{"payload": ...}`（或純文字）→
  prompt 內 `{payload}` 佔位符被取代，無佔位符則以
  `[Webhook payload]` 區塊附加；`?async=1` → 202 立即返回、背景
  fire（預設同步，HTTP 回應帶 LLM answer）。
* **生命週期鏡像排程**：`max_fires`（耗盡 → `exhausted` + 自停）、
  `expires_at`（→ `expired`）、`status="failed"`（chat_turn 崩潰
  後停用）、`disabled` 手動停用；持久化
  `$QDRANT_RAG_HOME/webhooks/<id>.json`（atomic write）+
  `<id>.history.jsonl`（50 行 ring buffer）+ 專屬 session
  （`webhook-<id>-<ts>`，可於 Sessions 頁讀取完整對話）。
* **監聽器** — `WebhookServer`（stdlib `ThreadingHTTPServer`
  daemon thread，零新依賴）：`QDRANT_WEBHOOK_HOST:QDRANT_WEBHOOK_PORT`
  （預設 `127.0.0.1:8335`）+ `/healthz`；bind 失敗僅警告不崩潰；
  `Agent.shutdown()` 停止。
* **接線**（完整鏡像 scheduler 三層）：
  (1) `Agent.webhook_server` lazy property + `start/stop_webhook_
  server`（idempotent）；(2) `McpManager.set_webhook_auto_start_
  callback` + `set_webhook_fire_callback` canonical setters →
  `add_webhook_trigger` 自動啟動監聽器（v0.8.6.3 模式）、
  `run_webhook_trigger_now` 取得 fire 引擎；(3) 開機自動恢復
  `gui/api.py::_resume_webhook_server`（v0.9.22.0 模式 — 執行緒
  隨程序死，重啟後 prewarm 重新綁定；`QDRANT_WEBHOOK_AUTORESUME=off`
  可關閉；access key 跨重啟有效）。
* stdio 鏡像 `src/mcp_server/webhook_server.py`
  （`python -m src.mcp_server.webhook_server`；**不綁 port** —
  監聽器只在 Agent 程序內，外部 client 僅管理觸發器）。
* System prompt「Tool selection rules」新增 webhook trigger block
  （「做一個觸發器」「給我一個 URL 讓外部呼叫後執行」'webhook',
  'callback URL' → webhook-local tools；**強制告知 key 只顯示一次、
  請使用者立即保存**；絕不捏造 URL/key）；legacy prompt 不含。
* `.env.example` / `.env` — `QDRANT_WEBHOOK_HOST/PORT/AUTORESUME`
  區塊 + gate 範例列與 live gate 加 `webhook-local`。
* `config/mcp.json.example` — 加 `webhook` stdio example（13 個）。
* Tests — **38 new** in `tests/test_webhook_host.py`：dataclass（key
  生成/constant-time 比對/向後相容/lifecycle gates）、manager（CRUD/
  history ring 50/path-traversal 拒絕）、**真實 HTTP round-trip**
  （fire + placeholder 注入/plain-text body/header auth/401/404/403
  /async 202/500，stub fire 引擎、零 LLM）、fire 引擎（session/
  history/lifecycle 蓋章/failed 停用/max_fires exhausted）、host
  surface + key 遮罩、manager 註冊 + callback setters、stdio parity、
  system prompt block + legacy 排除。相關迴歸:
  test_scheduler_boot_resume + test_stock_host + test_chat_history_host
  = **74 passed**。

`pyproject.toml` 0.9.24.0 → **0.9.25.0**。

### v0.9.24.0 — MCP 股市擴充：兆豐 MegaAPI 查詢 + 下單工具（mock 模式優先，開發/測試絕不下真單）

使用者需求:「以 MCP tool 方式，製作擴充功能，提供股市查詢和下單功能，
開發測試時不要執行實際股票下單」（技術文件: 兆豐證券 MegaAPI
https://www.emega.com.tw/emega/upload/megaapi/teach0.html —
Python `spdOrderAPI` 交易 / `spdQuoteAPI` 行情，事件驅動 socket API，
Windows 元件 + 電子憑證 .pfx + 帳號登入）。

新增**第 13 個 in-process LocalHost** `MegaStockLocalHost`（server
name `stock-local`）— 6 個工具，核心邏輯在 `src/mega_stock_tools.py`
（`MegaStockEngine`，純邏輯模組，與 archive_tools/exec_tools 同模式）:

| 工具 | 說明 |
|---|---|
| `stock_get_quote(symbol)` | 即時報價快照（開高低收、漲跌、成交量） |
| `stock_get_kline(symbol, interval?, bars?)` | K線（day\|week\|month，1-200 根，預設 20） |
| `stock_get_account_summary()` | 庫存 / 權益 / 未平倉委託數摘要 |
| `stock_list_orders(status?, limit?)` | 委託查詢（all\|open\|filled\|cancelled，預設 all / 20 筆） |
| `stock_place_order(symbol, side, qty, price?, order_type?, confirm)` | 下單（buy\|sell × limit\|market；`confirm=true` 防呆閘） |
| `stock_cancel_order(order_id)` | 刪單（限 open 狀態委託） |

**雙模式設計（安全核心）** — `MEGA_STOCK_MODE`:

* `mock`（**預設**）— 開發/測試用：**完全模擬**。不 import
  megaSpeedy/pySpeedy 元件、不開 socket、絕不送出真實委託。
  報價/K線為確定性模擬（同一 symbol 重複查詢得一致價格，標記
  `simulated: true`）；下單/刪單只存在 in-memory 模擬委託簿
  （`MOCK-YYYYMMDD-nnnn` 單號；市價單立即模擬成交、限價單留
  open 供刪單演練）。
* `live` — 真實模式：v0.9.26.0 起 megaSpeedy 元件已安裝（專案根
  `megaSpeedy/`）且 live 接線完成 — 查詢（帳務/委託/成交/K線/行情）
  在 API 開通（115/09/09，次日 08:30 起登入）後即可用；**下單/刪單**
  路徑已接真實 `SendNewOrderEx` / `SendCancelOrderEx`，但仍受三道閘
  + 憑證閘保護（下單需 `MEGA_STOCK_CERT_PATH/PASSWORD` 憑證、查詢
  不需要）— 開發/測試一律保持 mock。

**下單三道閘** — (1) `MEGA_STOCK_MODE=live`、(2)
`MEGA_STOCK_TRADING_ENABLED=on`（預設 off）、(3) 每筆下單參數
`confirm=true`。mock 模式無風險（但 confirm 閘仍會擋住意外下單）。

**掛載點** — `McpManager.from_config` 註冊於 Meta 之前
（`_stock_local_host` ref 保留）；stdio 鏡像
`src/mcp_server/stock_server.py`（`python -m src.mcp_server.stock_server`，
外部 MCP client 用）。`.env` 的 `MCP_ACTIVE_SERVERS` 已加入
`stock-local`（v0.9.13.2 契約：不在清單 = hidden）。

**System prompt** — tool-selection rules 新增 stock trigger block
（「股價」「查詢台積電」「K線」「我的庫存」'quote', 'kline',
'place an order' → stock-local tools），明確要求：回答時聲明
「模擬資料，非即時行情」；下單必須 `confirm=true`；live 需 env 閘
+ 聯調完成 — 開發/測試一律 mock，絕不聲稱已送出真實委託；
legacy（tools_enabled=False）prompt 不含。

**`.env.example`** — `MEGA_STOCK_MODE` / `MEGA_STOCK_TRADING_ENABLED`
完整區塊（工具清單 + 雙模式 + 三道閘說明）+ `MCP_ACTIVE_SERVERS`
範例列與版本註解加入 `stock-local`。

**`config/mcp.json.example`** — 新增 `stock` stdio worked example
（`python -m src.mcp_server.stock_server`；完整 mock-first 安全說明 +
三道閘 + `${MEGA_STOCK_MODE}` / `${MEGA_STOCK_TRADING_ENABLED}` 傳遞）
與先前缺失的 `chat-history` example（v0.9.21.0，`${QDRANT_RAG_HOME}`
傳遞）— 現共 **12 個** worked examples，與 12 個 stdio servers 一一對齊。

**測試** — `tests/test_stock_host.py` **26 tests**（mock 引擎確定性、
下單驗證 / confirm 閘、live 三道閘鎖定、host surface、manager 註冊
+ dispatch 端到端、stdio parity、system prompt block + legacy 排除）。
**沒有任何測試 import 兆豐元件、開 socket 或下真實委託** — live
接線在聯調完成前保持未測（by design）。相關迴歸: test_stock_host +
test_mcp_client + test_meta_local_host + test_datetime_host +
test_chat_history_host = **116 passed**；test_mcp_active_servers
**15 passed**（gate 名單同步後全綠）。

`pyproject.toml` 0.9.23.0 → **0.9.24.0**。

### v0.9.23.0 — 修復：排程 fire 的 LLM 總結被丟棄（answer=0 chars 的雙重 root cause）+ 空最終回答的 wrap-up 總結

使用者回報（live log 2026-09-07 14:55）:「執行排程 最後沒有用LLM總結」—
排程 daemon 正確恢復（v0.9.22.0 ✓）並 fire，LLM 正確跑了
`get_current_time` → `web_search` ×2 → `list_mcp_tools`，但
`fired ... (answer=0 chars)`。

**診斷（實讀 fire session + mcp.json + 完整 log）— 兩個 root cause:**

1. **`scheduler.py` 的回傳契約錯誤（核心 bug）** — `make_agent_on_fire`
   用 `result.get("answer")` 讀結果，但 `Agent.chat_turn` 回的是
   **tuple** `(answer, context_md, sources_list)`（dict 契約只符合測試
   fakes）。`isinstance(result, dict)` 為 False → **answer 永遠 None** →
   session 記 `[error] None`、history 記 `answer=0 chars` — **即使 LLM
   已產生完整總結也被丟棄**。
2. **Telegram 工具未載入** — `config/mcp.json` 沒有 telegram stdio
   server，`send_telegram_message` 不在工具目錄（fire 1 的 LLM 呼叫
   `list_mcp_tools` 就是在找它；web chat 重試時 LLM 也自述「我的工具
   目錄中沒有 send_telegram_message」）。

**修法:**

1. **`src/agent/scheduler.py`** — `_fire` 支援 tuple 與 dict 兩種回傳
   （tuple: `answer=result[0]`、`sources=result[2]`、`error=None`）。
   排程 fire 從此正確記錄 LLM 的實際總結與 sources；history 的
   `answer` 有值。空回答仍記 `[error] None`（既有語意）。
2. **`src/agent/agent.py` `_tool_loop`** — **空最終回答的 wrap-up
   總結**：reasoning model 可能在工具輪之後回 `content=""` +
   `finish_reason="stop"`（14:55 的 fire 就是這個形狀）。現在「至少跑
   過一輪工具後」若最終回答為空，做**一次無工具的 wrap-up create**
   （訊息清單已含全部工具結果 + 一則「請直接輸出最終答案，不要再呼叫
   工具」的 user 訊息），回其總結；wrap-up 也失敗則回空字串（既有
   graceful 行為）。usage 一併累計。第 1 輪就空的情況不觸發
   （不浪費呼叫）。
3. **`config/mcp.json`** — 加入 telegram stdio server
   （`python -m src.mcp_server.telegram_server`）。`TELEGRAM_BOT_TOKEN`
   / `TELEGRAM_ALLOWED_CHAT_IDS` 由 stdio client 的 env 繼承
   （web UI 程序已從 .env 載入），無需在 mcp.json 重複。此後排程
   prompt 的 Telegram 發送真正可用（chat_id <YOUR_TELEGRAM_CHAT_ID> 在 allowlist）。

**測試** — `tests/test_scheduler.py` +2（tuple 回傳的 session/history
正確記錄；空 tuple answer 不炸）+ 新檔 `tests/test_tool_loop_wrapup.py`
**4 tests**（wrap-up 觸發且不帶 tools、非空回答跳過、第 1 輪空跳過、
wrap-up 失敗 graceful）。相關套件 chat_history/mcp_client/meta/
chat_autosave **93 passed**。

**生效**:重啟 web UI。之後排程 fire 的 session/history 會記錄真實的
LLM 總結；遇模型回空內容時自動補一次總結呼叫。

`pyproject.toml` 0.9.22.1 → **0.9.23.0**。

### v0.9.22.1（文件同步：ARCHITECTURE.md 補齊 v0.9.20–v0.9.22 arc、§2.11 即時儲存時序、§2.14b 12-LocalHost 樹；README 加三層鏈序實戰範例；CLAUDE.md 頂部 summary 補 v0.9.20–22）

文件同步（docs-only，無程式 / 測試變更）：

* **`ARCHITECTURE.md`** — 上次 reviewed 停在 v0.9.19.0，本次補齊：
  header 版本改為 v0.9.22.0；§1.2 Historical arc 補上 v0.9.20.0
  （chat 即時儲存：send-time save + error 泡泡入檔 + web 模式
  sendBeacon unload save，含完整存檔時序）、v0.9.21.0
  （chat-history MCP core — 第 12 個 LocalHost 三工具 + 內建上限 +
  閘門 + stdio 鏡射）、v0.9.21.1（版本晶片 hotfix）、v0.9.22.0
  （scheduler daemon 開機自動恢復 + catch-up 補跑 + opt-out）；
  §2.11 `session.py` 加「real-time save timeline」說明並交叉引用
  ChatHistoryLocalHost；§2.14b 的 mcp_client 樹更新為 12 個
  in-process LocalHosts（含 ChatHistory v0.9.21.0+）；footer
  last-reviewed 更新至 2026-09-07 (v0.9.22.0)。
* **`README.md`** — v0.8.9 架構說明段的更新註記塊加「實戰範例」：
  本機 live `.env` 的三層鏈序
  （`LLM_PROVIDER_CHAIN=openrouter,ollama,openai-compat`、
  `LLM_PROVIDER` 留空由鏈全權決定 — primary = openrouter、
  failover = ollama cloud → 自架 local 端點）。
* **`CLAUDE.md`** — 頂部 summary chain 補 v0.9.20.0（即時儲存）/
  v0.9.21.0（chat-history MCP core） / v0.9.21.1（版本晶片 hotfix）/
  v0.9.22.0（scheduler 開機自動恢復）簡述。

`pyproject.toml` 0.9.22.0 → **0.9.22.1**。

### v0.9.22.0 — 修復：排程 daemon 不隨重啟恢復（14:00 的 cron 沒有執行的 root cause）

使用者回報:「2026-09-07T14:00:00 的排程為何沒有啓動」。診斷（實讀
排程檔 + app.log）:

* 排程本身**完全正常**:`9e924b90ac9c`「下午2點股市異常分析」
  （`cron 0 14 * * *`、`enabled=True`、`next_fire_at=
  2026-09-07T14:00:00`、`fire_count=0`、無 `last_error`、無
  history.jsonl）。
* **Root cause**:scheduler daemon 是**程序內的 thread** — 排程
  持久化在 schedules/*.json,但 daemon 隨程序死亡。app.log 的最後一
  次 `scheduler: started` 在 **11:37**（使用者為套用 openai-compat
  provider 設定重啟了 web UI）,之後 daemon 停止;v0.8.6.3 的
  auto-start **只在「LLM 新增排程」時觸發**,重啟後不會自動恢復 →
  14:00 沒有任何東西在 tick。

**修法（v0.9.22.0）** — 開機自動恢復:

* `gui/api.py` 新增 `_resume_scheduler_daemon(agent)`,掛在
  `_prewarm_agent()`（第一次 `get_status` 輪詢 = 啟動）:存在
  `enabled` 且 lifecycle status 為 active 的排程時呼叫
  `agent.start_scheduler()`（idempotent）並記 INFO（含排程名稱）;
  全部失敗 swallowed（GUI Schedules tab 按鈕仍是手動路徑）。
* 錯過的 `next_fire_at` 會在 daemon 恢復後第一次 tick **立即
  catch-up 補跑**（`tick_once` 的 phase-1 gate:`next_fire_at <=
  now` 即 fire）— 即重啟後 14:00 的排程會自動補跑（prompt 含
  Telegram 發送,請留意）。
* **Opt-out**:`QDRANT_SCHEDULER_AUTORESUME=off`
  （`.env.example` 加註解區塊,預設 on）。

**測試** — `tests/test_scheduler_boot_resume.py` **11 tests**
(resume-on-enabled / skip-when-none / skip-when-all-disabled /
skip-when-status-failed / env off（含大小寫）/ env on /
start_scheduler 失敗 swallowed / prewarm 整合呼叫 resume / resume
失敗不炸 prewarm / ScheduleManager 失敗自吞)。既有
test_gui_prewarm / test_scheduler_daemon_autostart 契約不變。

**生效**:重啟 web UI 後 log 會出現
`scheduler daemon auto-resumed (1 enabled schedule(s): 下午2點股市異常分析)`,
錯過的 14:00 排程隨即補跑。

`pyproject.toml` 0.9.21.1 → **0.9.22.0**。

### v0.9.21.1 — hotfix：頂欄版本晶片永遠顯示舊版 v0.8.6（硬編碼 HTML literal 從未被 JS 更新）

使用者回報:「🧠 Qdrant RAG Agent v0.8.6 的 v0.8.6 正確嗎」— **不正確**。
正確版本是 pyproject.toml 的 **v0.9.21.x**。

**Root cause**:v0.9.14.1 把 `gui/api.py` 的硬編碼 `'0.8.0'` 改為
`_app_version()`（從 pyproject.toml 動態讀取、單一真相來源），但漏了
**前端 HTML 的第二個版本字面值**:`gui/assets/index.html` 頂欄 brand 旁
的 `<span class="version" id="app-version">v0.8.6</span>`（v0.8.6 時期
寫死的靜態 fallback）。而且 **app.js 從未更新過 `#app-version` 元素**
（`refreshStatus()` 只把 version 塞進底部 status bar 的 detail 字串）—
頂欄版本晶片永遠顯示這個 2026 年 v0.8.6 時期寫死的字面值。

**修法**:

1. `gui/assets/index.html` — 靜態 fallback 從 `v0.8.6` 改為**空白**
   （單一真相來源原則:HTML 不再攜帶第二個版本字面值;get_status 到達
   前短暫空白）。
2. `gui/assets/app.js::refreshStatus()` — 每次輪詢 `get_status` 時把
   `r.version`（= pyproject 動態值）畫進 `#app-version`
   （`textContent = 'v' + r.version`）；順手把 `setStatus` detail 的
   `'0.9.14.1'` fallback literal 移除（`r.version` 恆有值）。

**測試** — `tests/test_web_server.py::TestHttpRoundTrips` +2:
`test_index_html_has_no_hardcoded_version`（`#app-version` 內容不得
以 `v<數字>` 開頭 — 防止再次漂移）+ `test_app_js_paints_app_version_chip`
（refreshStatus 必須更新 `#app-version`）。14 passed（含既有
`test_get_status_version_forwarded` 的 pyproject parity 契約）。
`node --check app.js` 通過。

**生效方式**:重新整理 web 頁面 / 重啟 GUI 即顯示 `v0.9.21.x`（每次
get_status 輪詢都會重畫，之後 bump pyproject 即自動同步）。

`pyproject.toml` 0.9.21.0 → **0.9.21.1**。

### v0.9.21.0 — MCP 核心功能：chat-history 查詢工具（LLM 可查歷史對話記錄）

使用者需求:「以 mcp 方式，製作一個核心功能，讓 llm 可以查詢歷史對話
記錄」。新增**第 12 個 in-process LocalHost** `ChatHistoryLocalHost`
（server name `chat-history-local`）— 三個 READ-ONLY 工具，直接走
`src.agent.session.SessionManager` 讀取持久化對話
（`$QDRANT_RAG_HOME/sessions/<sid>.json`）:

| 工具 | 說明 |
|---|---|
| `list_chat_sessions(keyword?, limit?, include_scheduled?)` | 列出 sessions（newest first、title 關鍵字過濾、數量上限） |
| `search_chat_history(keyword, sid?, role?, limit?, max_chars?, include_scheduled?)` | 跨 session 關鍵字搜尋（不分大小寫、±context snippet、role 過濾、sid 限定範圍） |
| `get_chat_session(sid, last_n?, max_msg_chars?)` | 讀單一 session 訊息（取最後 N 則、每則內容截斷） |

設計要點:

* **內建上限（零新 env vars）** — 搜尋 soft default 10 命中 / hard
  50、snippet 280 字（上限 2000）、總 snippet budget 8000 字；
  get 取最後 20 則（上限 100）、每則內容 1200 字（上限 4000）—
  狂奔的歷史傾印永遠不會炸 context window。
* **排程 fire session 預設隱藏** — `sid` 以 `sched-` 開頭的排程
  自動記錄（`[schedule] <name>` title）不列在 list / search，
  `include_scheduled=true` 可納入；`get_chat_session` 用 id 仍可讀。
* **掛載點** — `McpManager.from_config` 在 Meta 之前自動註冊
  （catalog 含此工具、保留 `_chat_history_local_host` 參考）；
  stdio server `src/mcp_server/chat_history_server.py` 供外部 client
  （`python -m src.mcp_server.chat_history_server`）。
* **System prompt** tool-selection rules 新增 trigger block：『上次
  聊過什麼』『歷史對話』『我之前有問過 X 嗎』'did we discuss X' →
  不從記憶捏造，呼叫工具查證；legacy（tools_enabled=False）不含。
* **閘門** — `.env` 的 `MCP_ACTIVE_SERVERS` 已加入
  `chat-history-local`（v0.9.13.2 契約：不在清單 = hidden）；
  `.env.example` 加註解區塊。

**測試** — `tests/test_chat_history_host.py` **37 tests**
（tool surface ×3、list ×7、search ×10、get ×6、LocalTool coercion
round-trip ×3、manager 註冊 + dispatch + Meta catalog ×3、stdio
parity ×3、system prompt ×2）。相關套件：mcp_client / meta /
datetime 組 **90 passed**。真實環境 E2E：live sessions 63 個、
`search "qdrant"` 9 hits、`get_chat_session` 讀回真實對話 ✓。

`pyproject.toml` 0.9.20.0 → **0.9.21.0**。

### v0.9.20.0 — chat 對話記錄即時儲存（send-time save + error 泡泡入檔 + web 模式 sendBeacon unload save）

使用者需求:「chat 對話記錄支援即時儲存」。稽核 v0.8.7 三層安全網
後發現三個實際缺口，全部補上（`gui/assets/app.js`，GUI 與 web 共用）:

1. **送出即存** — `sendChat()` 在 append 使用者氣泡後、**await
   `chat_turn` 之前**立即 `autoSaveChat()`（fire-and-forget，不擋
   LLM 呼叫）。此前只有「回覆到達後」才存 — 長回合（桌面自動化 /
   本地大模型，數十秒到數分鐘）期間程序掛掉、分頁關閉或瀏覽器崩潰
   時，使用者訊息整則丟失。現在送出瞬間就落地 `sessions/` +
   `localStorage` backup。
2. **error 泡泡也入檔** — 兩個錯誤出口（無回應 / `r.error`）原先
   `appendChatMsg('error', …)` 後直接 return **不存檔**，重新整理
   後錯誤訊息從歷史消失。現在兩條路徑都 `autoSaveChat()`。
3. **web 模式 unload 用 `navigator.sendBeacon`** — 這是 v0.8.7
   設計在 web 模式的實際缺口：`beforeunload` 裡的 fire-and-forget
   `fetch` 可能被瀏覽器在 unload 時取消（手機/桌機皆然），
   last-ditch save 會靜默失效。web 模式（`window.__WEB_MODE__`，
   shim 設定）改用 `navigator.sendBeacon` — 瀏覽器保證排程發送、
   不隨頁面死亡取消；body 與 `POST /api/<method>` 的 args 形狀相同
   （`[sid, title, messages]`），web server handler 只
   `json.loads(raw)` 不檢查 Content-Type，無後端變更。beacon 被
   拒時落到 GUI 橋接路徑再試一次。

存檔時序（v0.9.20.0+）: 送出即存（使用者訊息）→ 回覆到達存（含
usage/時間頭）→ 錯誤泡泡存 → 重新整理/關頁時 sendBeacon 存 →
程序崩潰靠 localStorage backup + `bootDraftRecovery` 復原。

**測試** — `tests/test_chat_autosave.py` +4（送出順序鎖定:
`appendChatMsg('user') < autoSaveChat < 'chat_turn'`；兩條 error
路徑各入檔；`_beforeUnloadSave` 的 sendBeacon + body 形狀鎖定）。
`node --check app.js` 語法驗證通過。21 passed, 0 failed。

`pyproject.toml` 0.9.19.1 → **0.9.20.0**。

### v0.9.19.1（文件同步：ARCHITECTURE.md 補齊 v0.9.15–v0.9.19 arc、5-Provider dispatch 說明、raw-string env knobs 慣例；README v0.8.9 架構段加更新註記；CLAUDE.md canonical pattern 標注已兌現）

文件同步（docs-only，無程式 / 測試變更）：

* **`ARCHITECTURE.md`** — 上次 reviewed 停在 v0.9.14.1，本次補齊：
  header 版本改為 v0.9.19.0（5-Provider dispatch, chain-orderable）；
  §1.2 Historical arc 補上 v0.9.15.2/15.3、v0.9.16.x（web upload +
  OCR hardening）、v0.9.17.0（跨供應商 failover + SiliconFlow
  vision）、v0.9.18.0（鏈分離 + `provider_chain.py` 零副作用解析器）、
  v0.9.19.0（`OPENAI_CHAT_MODEL` + 第 5 個 provider `openai-compat`）；
  §2.1 加「raw-string env knobs」慣例說明（attr 快照 + resolver 直讀
  `os.environ` 的 v0.9.11+ 模式）；§2.8 改寫為 vision chain 描述
  （`VISION_PROVIDER_CHAIN` 可調、`enabled` 語意、`last_backend`）；
  §2.10 加 Provider dispatch / Chain order / OpenAI-compatible escape
  hatches 三段；Appendix A 檔案清單加 `provider_chain.py` 並更新
  `config.py` / `image_describer.py` / `agent.py` 描述；footer
  last-reviewed 更新至 2026-09-07 (v0.9.19.0)。
* **`README.md`** — v0.8.9 架構說明段尾端加「v0.9.18.0 / v0.9.19.0
  更新」註記塊（第五家 `openai-compat`、`OPENAI_CHAT_MODEL` 為真、
  鏈序可調；歷史敘述保留不改寫）。
* **`CLAUDE.md`** — 「Canonical pattern for adding a 5th LLM
  provider」標注已在 v0.9.19.0 兌現，並修正「No other code changes
  needed」的說法：實作時還需要 (a) REQUIRED env 的 per-provider
  guard（`_pick_provider` raise + `_provider_candidates` skip+警告）
  與 (b) 錯誤訊息的 env 名以 `.upper().replace('-', '_')` 產生
  （naive `upper()` 會把 dash 名渲染成 `OPENAI-COMPAT_API_KEY`）。
* `tests/test_llm_providers.py` 檔頭 docstring 同步（5 providers +
  `OPENAI_CHAT_MODEL`）。

`pyproject.toml` 0.9.19.0 → **0.9.19.1**。

### v0.9.19.0 — LLM OpenAI 相容 API 設定:openai 槽位專屬模型鍵 + 第 5 個 provider `openai-compat`

使用者需求:「提供支援 llm openai-compat api 設定」。**兩者都做**:

**1. `OPENAI_CHAT_MODEL` — 現有 `openai` provider 的專屬模型鍵。**
`openai` provider 本來就是通用 OpenAI 相容端點的逃生口
（`OPENAI_BASE_URL` 可指向 DeepSeek / LM Studio / vLLM / Together /
公司 proxy…），但它的模型只能用 `LLM_MODEL`，而
`LLM_MODEL != "gpt-4o-mini"` 會**全域覆寫** ollama / openrouter /
siliconflow 三家的 chat model — 把 openai 槽位指到 DeepSeek 並設
`LLM_MODEL=deepseek-chat` 時，failover 退到其他家會拿
`deepseek-chat` 去打 `ollama.com` / `openrouter.ai`，**跨供應商
failover 直接壞掉**。新增 `OPENAI_CHAT_MODEL` 後：

```env
OPENAI_BASE_URL=https://api.deepseek.com/v1
OPENAI_API_KEY=sk-ds-xxx
OPENAI_CHAT_MODEL=deepseek-chat   # 專屬;LLM_MODEL 保持 gpt-4o-mini 不動
```

優先序與其他三家一致：非預設的 `LLM_MODEL` 仍然最優先（全域覆寫
語意不變）；未設 `OPENAI_CHAT_MODEL` = 退回 `LLM_MODEL`
（v0.8.9-v0.9.18 行為完全不變）。

**2. 第 5 個 provider `openai-compat`** — 完全獨立的三件組，與
`openai` 並存（可同時掛兩個相容端點，例如 openai→DeepSeek、
openai-compat→LM Studio），並自動納入 `LLM_PROVIDER_CHAIN`（鏈上
多一個 failover 節點）：

```env
OPENAI_COMPAT_API_KEY=sk-xxx
OPENAI_COMPAT_BASE_URL=https://api.deepseek.com/v1   # 必填
OPENAI_COMPAT_CHAT_MODEL=deepseek-chat
```

* Append 在 `_PROVIDER_ORDER` **尾端**：auto-detect 與預設 failover
  順序對現有用戶零變化（其他任何 key 仍優先）。
* **`OPENAI_COMPAT_BASE_URL` 必填**（有 key 無 URL 時
  `_pick_provider` raise 清晰錯誤、failover 候選走訪時 skip+警告）—
  相容端點沒有合理預設值，預設會把相容 key 靜默打向
  api.openai.com。
* 錯誤訊息更新：Valid options 與 No-provider 清單加入
  `openai-compat` / `OPENAI_COMPAT_API_KEY`；override-with-empty-key
  的錯誤對含 `-` 的 provider 名正確顯示底線 env 名
  （`OPENAI_COMPAT_API_KEY`，不是 naive `upper()` 的
  `OPENAI-COMPAT_API_KEY`）。
* Chat model 優先序同 openai：非預設 `LLM_MODEL` 最優先 →
  `OPENAI_COMPAT_CHAT_MODEL` → `LLM_MODEL`。

**測試** — `tests/test_llm_providers.py` +8（OPENAI_CHAT_MODEL ×4
含「不毒化其他 provider」鎖定；openai-compat ×4：auto-detect 最後 /
唯一 key 即 primary / 必填 URL raise / override+空 key 錯誤訊息）；
`tests/test_provider_chain_order.py` +1（openai-compat 納入鏈成為
primary）。既有測試 env 隔離補強：4 個測試檔的 wipe/pin 清單加入
4 個新變數（live `.env` 設定後不得洩漏）；`test_agent_provider_failover`
的 `_resolved_agent` 改為 patch **當前** config 物件（sys.modules 解析，
reload-safe — 順序敏感的 pre-existing 汙染徹底修復，非字母序組合也全綠）。
6 檔組合 **143 passed, 0 failed**。

`pyproject.toml` 0.9.18.0 → **0.9.19.0**。

### v0.9.18.0 — chat 主鏈與 vision OCR 鏈分離:.env 可調鏈則順序

使用者需求:「chat primary 和 vision OCR 的鏈則是兩個不同的應用需求,
提供支援可以在 .env 調整鏈則順序的機制」。

v0.9.17.0 之前,chat LLM 的 provider 優先序(`_PROVIDER_ORDER`)與
vision OCR 鏈(ImageDescriber 的 ollama → openrouter → siliconflow →
openai-compat)都是**硬編碼**——chat 想讓 OpenRouter 當 primary、或
OCR 想讓一般 OpenAI 相容端點先行,都只能改程式碼。兩條鏈是**獨立的
應用需求**(一個服務對話,一個服務圖片描述),不該共用也不該綁死。

**兩個新 env 變數**(逗號分隔、不分大小寫;**空白/未設 = 預設順序,
行為與 v0.8.9-v0.9.17 完全一致**):

| 變數 | 範圍 | 預設 |
|---|---|---|
| `LLM_PROVIDER_CHAIN` | chat primary 選擇 + 跨供應商 failover 順序 | `ollama,openrouter,siliconflow,openai` |
| `VISION_PROVIDER_CHAIN` | ImageDescriber OCR 鏈(describe_image / ingest 圖片描述) | `ollama,openrouter,siliconflow,openai`(別名 `ollama-cloud` / `openai-compat` 也可) |

```env
# chat 先走 OpenRouter,失敗才退 Ollama,再來 SiliconFlow、OpenAI
LLM_PROVIDER_CHAIN=openrouter,ollama
# OCR 先走一般 OPENAI_API_KEY 相容端點,失敗才退 OpenRouter
VISION_PROVIDER_CHAIN=openai,openrouter,siliconflow,ollama
```

**解析規則**(共用解析器 `src/provider_chain.py::parse_provider_chain`,
新的**零副作用模組** — 刻意不 import `src.config`,因為 config import
會跑 `load_dotenv` 把測試剛清掉的 key 重新塞回 os.environ):

1. 空白/未設 → 預設順序(零行為變化);
2. 無效 token → **skip + 警告**(一個 typo 不打死整條鏈);
3. 重複 token → 取第一次出現;
4. **部分列表 = 列出的優先、其餘按預設相對順序接在後面** — 鏈定義
   「優先序」而不是「剔除」,failover 完整性保留(要停用某家供應商,
   把它的 key 留空即可)。

**實作**:

- `Agent._resolved_provider_order()`(classmethod)— 讀
  `LLM_PROVIDER_CHAIN` 重排 `_PROVIDER_ORDER`;`_pick_provider()` 與
  `_provider_candidates()`(primary + failover walk)改用它。
  `LLM_PROVIDER` 強制覆寫**仍然最優先** — chain 只重排 auto-detect
  與 failover 順序,不改變 forced 語意。
- `ImageDescriber._chain_names()` — 讀 `VISION_PROVIDER_CHAIN`;
  新 `_vision_candidates()` 回傳**完整鏈**(含 Ollama)按鏈序排列,
  `describe()` 改走單一鏈迴圈 — Ollama 不再硬編碼為 primary,只是
  預設鏈的第一位。`_fallback_vision_candidates()` 保留為向後相容
  視圖(鏈中非 Ollama 部分,env 未設時與 v0.9.17.0 完全一致);
  `enabled` / `last_backend` / 錯誤 marker(`ollama(<model>): ...` 的
  pre-0.9.18.0 形狀)語意不變。
- `describe_image` MCP 工具描述註明鏈序可調。
- `config.py` 新增 `LLM_PROVIDER_CHAIN` / `VISION_PROVIDER_CHAIN`
  raw attrs(文檔/快照用;解析函數讀 `os.environ`,測試可直接
  monkeypatch,不需 reload config)。

**測試** — `tests/test_provider_chain_order.py` 28 個新測試
(解析器規則 ×9 / chat 鏈 ×8 / vision 鏈 ×11);相關檔案 env 隔離
補強(`test_llm_providers` / `test_openrouter_routing` 的 wipe 清單
加入兩個新變數;`test_image_describer_fallback` 的 `_clean_env` 補上
v0.9.17.0 就缺的 `SILICONFLOW_*`);`test_upload_ocr_fixes` 的
`_pin_provider_config` 改 patch **當前** `src.config.config` 物件
(reload-safe — 修掉組合執行時 failover 打到真 endpoint 的
pre-existing 汙染)。6 檔組合 **133 passed, 0 failed**。

`pyproject.toml` 0.9.17.0 → **0.9.18.0**。

### v0.9.17.0 — 全 LLM 供應商備援:Agent 跨供應商 failover + SiliconFlow vision

使用者需求:「檢查所有 llm provider,改為例如 describe_image 與圖片描述
現在支援 OpenRouter fallback 的備援機制」。LLM 呼叫點盤點與處置:

| 呼叫點 | v0.9.17.0 前 | 現況 |
|---|---|---|
| `Agent` chat(tool loop / ask / chat_turn) | 單一 provider,同 provider 重試 2 次 | **跨供應商 failover** |
| `Agent._summarize_history` | 單一 provider,失敗 → extractive | 走 failover 版 create |
| `ImageDescriber` | ollama → openrouter → openai | + **SiliconFlow** 四級鏈 |
| `Embedder` | 已有 4 級 backend 鏈(auto) | 不動 |
| `web_search` | 非 LLM,已優雅降級 | 不動 |

- **Agent 跨供應商 failover**:`_create_validated` 嘗試序列 =
  primary 2 次(涵蓋空 choices 與暫時性 API 錯誤)→ 之後沿
  `_PROVIDER_ORDER` 優先序(ollama → openrouter → siliconflow →
  openai)對每個**已設 key** 的其他供應商各試 1 次。新
  `_provider_candidates()` / `_failover_candidates()`(fresh OpenAI
  client、各 provider 自家 model_fn 語意、openrouter 候選套用
  routing 但**不碰** primary 的 `_extra_body`)。**非 openrouter 的
  fallback 會剝除 `extra_body`**(provider routing / reasoning 是
  OpenRouter 專屬欄位,其他端點會拒絕)。`self.last_provider` 記錄
  實際應答者;每次 failover 記 WARNING、成功記 INFO。全部失敗:
  最後一個例外 re-raise(呼叫端已有優雅降級)或 `(None, False)`。
- **`_summarize_history`** 改走 `_create_validated` — 壓縮摘要也
  享有 failover;仍保持「任何失敗 → extractive fallback」契約。
- **ImageDescriber 加入 SiliconFlow**:`_fallback_vision_candidates()`
  回傳候選清單 — openrouter → **siliconflow**
  (`SILICONFLOW_API_KEY` + 新 env `SILICONFLOW_VISION_MODEL`,預設
  `Qwen/Qwen2.5-VL-72B-Instruct`)→ openai-compat;`vision_backend`
  可能值新增 `siliconflow`。
- `.env.example`:OpenRouter vision 區塊改寫(含 agent failover 說明)
  + 新 SiliconFlow 區塊;`src/config.py` 新增
  `SILICONFLOW_VISION_MODEL`。

15 個新測試在 `tests/test_agent_provider_failover.py`(候選順序 ×4、
failover ×6 — primary 失敗 fallback 應答 / extra_body 剝除與保留 /
雙敗 re-raise / 全 invalid 降級 / primary 成功不觸發、端到端 ×4 —
chat_turn 存活 primary 斷線 / summarize failover / 全失敗 → None /
全空 choices → 優雅文字)+ `tests/test_image_describer_fallback.py`
改寫為 candidates API(×6 含 siliconflow / 全鏈順序)。測試的
config 釘選為**密封式**(LLM_PROVIDER / LLM_MODEL / routing env
全部中性化)— 使用者真實 .env 的 `LLM_PROVIDER=openrouter` 會洩進
測試導致 provider 判定錯位。
`pyproject.toml` 0.9.16.4 → **0.9.17.0**。

### v0.9.16.4 — describe_image / 圖片描述的 OpenRouter vision fallback

使用者需求:「describe_image 加上對 openrouter 的支援,當 ollama cloud
無法使用時使用 openrouter 替代」。`ImageDescriber.describe()` 維持
`str` 回傳契約,但內部改跑**供應商鏈**:

1. **Ollama Cloud**(primary,不變):`OLLAMA_API_KEY` +
   `OLLAMA_BASE_URL` + `OLLAMA_VISION_MODEL`。
2. **Ollama 未設定或任何失敗**(連線 / 429 / 5xx / 空回應)→
   **OpenRouter**:`OPENROUTER_API_KEY` + `OPENROUTER_BASE_URL` +
   新 env **`OPENROUTER_VISION_MODEL`**(預設
   `google/gemini-2.5-flash` — 多模態、便宜、供給充足;`src/config.py`
   與 `.env.example` 已同步)。
3. **無 `OPENROUTER_API_KEY`** → 泛用 `OPENAI_API_KEY` +
   `OPENAI_BASE_URL` 端點。

兩者皆失敗才回既有 `[Image: ... | vision error: ollama(...): ...;
...]` marker(含兩段錯誤、不進 cache、可重試)。細節:

- 抽出共享的 `_call_openai_compat(data_url, key, url, model)` —
  Ollama primary 與 fallback 走同一個 OpenAI 相容 vision 呼叫;
  空回應視為失敗(raise)觸發鏈下一跳。
- `enabled` 屬性:Ollama key **或** fallback key 任一存在即 True —
  只有 OPENROUTER_API_KEY 時 ingest 圖片描述也能運作。
- `last_backend` 記錄實際應答的供應商(`ollama-cloud` /
  `openrouter` / `openai-compat`);`describe_image` 工具結果新增
  **`vision_backend`** 欄位;工具描述註明 fallback 行為。
- SHA-1 cache 跨供應商共享(fallback 成功一樣進 cache)。

13 個新測試在 `tests/test_image_describer_fallback.py`
(`_fallback_vision_config` ×4、fallback 鏈 ×8 — ollama 失敗由
openrouter 應答 / 直達 / openai-compat / 空回應視為失敗 / 雙敗
marker / 無供應商 marker / primary 成功不觸發 fallback / fallback
結果進 cache、host 結果帶 `vision_backend` ×1)。網路呼叫全部在
`openai.OpenAI` 層 mock。
`pyproject.toml` 0.9.16.3 → **0.9.16.4**。

### v0.9.16.3 — Api 懶載入鎖(web 雙重建構)+ 圖片 OCR prompt 規則

使用者問:「為何 gui 和 web 反應不一樣」。**chat 路徑兩者完全相同**
(同一個 `gui.api.Api` → `Agent.chat_turn` → 同一組工具面;log 中
MCP servers、collision warnings 一模一樣)。反應不同的真實原因:

1. **不同的圖片**:GUI 輪問的是 IMG_5749(舊檔),web 輪是
   IMG_5783(新圖)— `describe_image` 的 vision 描述內容不同。
2. **LLM 非確定性**(temperature 0.3 + glm-5.3-flash 取樣):GUI 輪
   模型「不滿意」描述 → 自行呼叫 windows-mcp 的 PowerShell 工具
   嘗試 Windows 內建 OCR(Windows.Media.Ocr)→ C# compile failed
   一連串 → 19 次工具呼叫、**568,940 tokens**、撞 20 迭代上限;
   web 輪描述夠用 → 直接回答(33,625 tokens、29 秒)。

針對這兩點 + log 中的雙重建構異常,三個修復:

- **`_get_agent` / `_kick_prewarm` 加鎖(v0.9.16.3 核心)**:web log
  顯示 `McpManager.from_config` 跑了**兩次**(my_web / drawio /
  windows-mcp 各連兩次,drawio 兩個 session id)。根因:web 模式
  桌面自動開的頁面 + 還開著的手機分頁**同時開機**,兩個 get_status
  競爭 `_kick_prewarm` **無鎖**的 flag 檢查 → 兩個 prewarm 執行緒 →
  兩者都在 `_get_agent` 的**無鎖** check-then-set 上建立各自的
  Agent → `Agent.mcp_manager` 的 DCL 是 per-instance,擋不住跨
  instance。修復:`Api._agent_init_lock`(class-level)同時守護
  flag 與 Agent 懶載入 — 一個程序永遠只有一個 Agent / 一次
  from_config。
- **system prompt 新增圖片 OCR 規則**(tools 模式):`describe_image`
  成功後直接以 `description` 回答;**禁止**用 PowerShell /
  windows-mcp / 外部 runtime 重做 OCR(那會燒光迭代預算);只有
  describe_image 明確報錯才換路線並告知使用者。
- **建議(不強制)**:日常聊天把 windows-mcp 排除在 tool surface 外
  (`.env` 的 `MCP_ACTIVE_SERVERS` 不含它),需要桌面自動化時用
  chat 工具 picker(🧰)暫時打開 — v0.9.13.0 的設計初衷。

4 個新測試:`tests/test_gui_prewarm.py::TestPrewarmRaceSafety` ×2
(8 執行緒 + Barrier 實測 `_kick_prewarm` 只 spawn 一條執行緒、
`_get_agent` 只建一個 Agent)、`tests/test_upload_ocr_fixes.py::
TestImageOcrPromptRule` ×2(tools prompt 含規則、legacy 不含)。
`pyproject.toml` 0.9.16.2 → **0.9.16.3**。

### v0.9.16.2 — 工具結果 base64 瘦身 + API 錯誤重試(404 "No endpoints" 修復)

使用者 log 2026-09-06:上傳 IMG_5783.jpeg 問「ocr」— v0.9.16.1 的 mode
推斷**已生效**(`describe_image(file_path, prompt)` → `ok: true,
mode: "path"`),但**下一輪 create** 死於 OpenRouter 404
`No endpoints found for z-ai/glm-5.3-flash`,路由漏斗
`Filter by Context Length: 24 → 1 → Filter by Fallback → 0`。

**根因**:`describe_image` 回傳 `raw_image_base64`(手機照片的完整
base64,數 MB)— 它被原封不動塞進 tool 結果訊息,下一輪請求的
context 直接超過所有端點上限(24 個端點經 context-length 過濾只剩
1 個,再被 `OPENROUTER_ALLOW_FALLBACKS=false` 過濾成 0 個 → 404)。
而**文字-only 的 tool loop 從不消耗這些 bytes** — 純浪費。

- **base64 瘦身(預設開)**:新 `_strip_base64_from_tool_text()` —
  (a) tool 訊息可解析為 JSON 時,凡 key 含 `base64` 且 ≥1024 字的
  值換成 `<base64 omitted: N chars>`(遞迴走訪 dict/list,
  `description` 原封不動);(b) 非 JSON 或無已知 key 時,任何
  ≥2048 字的 base64 字元 run(需 **≥16 個相異字元** — 低熵長串如
  `"rrrrr…"` 不是 base64)同樣替換。`AGENT_STRIP_TOOL_BASE64=off`
  可關閉。`.env.example` 已記錄。
- **`describe_image` / `resize_image` 工具描述更新**:註明 tool
  loop 會剝除 base64 — LLM 應依賴 `description` / `output_path`,
  不要嘗試讀 base64 bytes。
- **`_create_validated` 涵蓋硬錯誤重試**:429 / 5xx / 暫時性 404
  等 create 例外也重試一次,兩次皆失敗才 re-raise 最後一個;
  `_tool_loop` + `ask` / `chat_turn` legacy 路徑接住後回傳優雅文字
  (`(LLM 呼叫失敗,已自動重試一次仍失敗:…)`)— 不再是紅色錯誤
  toast。第一輪 66 秒空 choices 的情況維持 v0.9.16.1 的優雅降級。

9 個新測試在 `tests/test_upload_ocr_fixes.py`(瘦身 ×5 含 kill-switch
與 prose 不誤傷、`_run_tool_call` 整合 ×1、例外重試 ×3 含 404 場景
優雅降級);`tests/test_context_compress.py` 的 offload 測試揭露
低熵誤傷後已由 ≥16 相異字元檢查修復。
`pyproject.toml` 0.9.16.1 → **0.9.16.2**。

### v0.9.16.1 — 上傳→OCR 鏈路修復(empty choices 不再炸整個回合)

使用者 log 2026-09-05:手機上傳 IMG_5749.jpeg 後問「ocr」,三個問題連環爆:

1. **`describe_image` 少 `mode` 被彈回**:LLM 呼叫
   `describe_image(['file_path', 'prompt'])` 沒帶 enum 的 `mode`
   參數 → 工具回錯誤。**修復**:`mode` 缺漏/無效時自動推斷 —
   有 `file_path` → `path`,有 `data_base64` → `base64`,兩者皆無
   才回原錯誤。
2. **上傳檔被擋在 OCR 沙箱外**:新 `_allowed_file_roots()`(host.py)
   = import 沙箱 + filesystem 讀/寫沙箱(聯集、去重),套用在
   `describe_image` / `resize_image` / `add_document` 三處的路徑
   檢查 — 上傳檔(寫入沙箱 `uploads/`)不再被
   "outside the import sandbox root" 擋掉。錯誤訊息改列全部
   允許 roots。`resize_image` 的輸出改存**來源檔旁邊**
   (上傳檔 → 同一個 `uploads/` 目錄),resized 檔跟 LLM 引用的
   原檔放一起。
3. **`resp.choices` None 炸整個回合**(log:`TypeError: 'NoneType'
   object is not subscriptable` at agent.py:1254):OpenRouter 上游
   偶爾回 HTTP 200 + `choices=None`。**修復**:新
   `Agent._create_validated()` — create 後驗證,無效間隔 0.8s 重試
   一次;仍無效則回傳優雅的 `EMPTY_RESPONSE_TEXT`(雙語)而不 raise。
   套用在 `_tool_loop` + `ask` / `chat_turn` legacy 路徑。新模組級
   `_valid_completion(resp)`(choices None/[]/缺 attr 皆無效)。
4. **客戶端斷線的 ConnectionResetError 噪音**(chat turn 跑 40 秒,
   手機瀏覽器先放棄):`do_POST` 回應寫入包 `except ConnectionError`
   → DEBUG(`client gone before response`)。

15 個新測試在 `tests/test_upload_ocr_fixes.py`(_valid_completion ×4、
空 choices 優雅降級 ×4(含重試成功)、mode 推斷 ×4、上傳沙箱接受
describe/resize ×2、web server 防護原始碼檢查 ×1)。
`tests/test_describe_image.py` / `tests/test_resize_image.py` 的 4 個
沙箱拒絕測試改為釘選**全部三個 roots**(import + write + read +
WATCH_DIR)— 新語意是「所有設定 roots 的聯集之外才拒絕」,只釘一個
root 會讓真實 WATCH_DIR 洩進測試。
`pyproject.toml` 0.9.16.0 → **0.9.16.1**。

### v0.9.16.0 — Web UI 檔案上傳 + 手機媒體上傳

使用者需求:「web版提供檔案上傳功能，手機網頁版多加媒體上傳功能」。
原本 web 版唯一的上檔途徑是 `create_file_dialog` → tkinter 對話框開在
**伺服器桌面** — 手機 / 區網客戶端完全無法把檔案送進來。新流程:
聊天工具列 📎 → 選檔 → `fetch('/upload')` → 存進 filesystem 寫入沙箱
`uploads/` → 上傳路徑自動插入聊天輸入框 → LLM 可直接讀取 / 路徑可拿去
Ingest 分頁攝入。

- **`POST /upload` 端點(`gui/web_server.py`)**:純 stdlib multipart
  parser(Python 3.13+ 已移除 `cgi` 模組)。每個檔案存為
  `<QDRANT_FILESYSTEM_WRITE_ROOT>/uploads/YYYYmmdd-HHMMSS-<name>`
  (碰撞自動 `-N` 後綴);回傳 `{ok, files:[{name, path,
  relative_path, size}], skipped, dir}`。
- **安全/限制**:檔名淨化(basename-only 防 traversal、Windows 非法
  字元淨化、120 字上限);危險執行檔副檔名(.exe/.bat/.sh/…)拒絕
  — 與 filesystem MCP write tools 同一份 blocklist;單請求上限
  20 個檔;body 上限 `QDRANT_WEB_UPLOAD_MAX_MB`(預設 200 MB,
  無效值退回預設);413 / 400 提前拒絕前先 drain 最多 8 MB body,
  讓客戶端收到 JSON 錯誤而不是 socket reset。
- **前端(chat 工具列)**:📎(任意檔案,`multiple`)+ 📷(媒體,
  `accept="image/*,video/*"` — 手機上開相簿/相機膠卷)。上傳成功
  toast(4 語系)+ 每個上傳路徑以 `[已上傳] <path>` 插入輸入框。
- **手機限定 📷**:`@media (min-width: 821px)` 隱藏媒體按鈕 —
  只在 ≤820px(手機/平板寬度)出現,符合「手機網頁版多加媒體上傳」。
- **pywebview GUI 模式**:shim 設 `window.__WEB_MODE__ = true`,
  GUI 模式沒有此旗標 → `bindUpload()` 把 📎/📷 與兩個 input 全部
  `display:none`(同機選檔本來就有 create_file_dialog + Ingest 分頁)。
- `.env.example` 新增 `QDRANT_WEB_UPLOAD_MAX_MB` 註解區塊。
- 31 個新測試在 `tests/test_web_upload.py`(multipart parser ×6、
  檔名淨化 ×4、sandbox 寫入/碰撞/危險副檔名/20 檔上限 ×4、大小上限
  解析 ×3、真實 ThreadingHTTPServer round-trip ×5、shim 旗標 ×1、
  前端原始碼防護 ×8)。
- `pyproject.toml` 0.9.15.3 → **0.9.16.0**。

### v0.9.15.3 — `get_status` 心跳日誌降噪(DEBUG)

使用者問:「為何會有大量的 gui.web: POST /api/get_status」。**行為是設計
如此,不是 bug**:

- `app.js::refreshStatus()` 每 **30 秒** poll 一次 `get_status`
  (`setInterval(refreshStatus, 30000)` — 狀態列/Qdrant/provider 心跳),
  加上每次頁面載入各一次;`chat_turn` 進行中照常心跳(72 秒的對話回合
  自然疊了 2-3 次心跳)。
- log 顯示 ~130 秒內 ~26 次(≈ 每 5 秒一次)→ 代表**同時開了約 5-6 個
  web UI 頁面**(桌面瀏覽器多個分頁 + 手機),每頁各自跑自己的 30 秒
  timer;同一秒出現兩筆 = 兩頁的 timer 對齊。22:24:08 那筆
  `get_readiness` + `list_sessions` 是某一頁重新載入/新開的 boot 序列。
- 每個 POST 在 `web_server.py` 都以 INFO 記錄 → 心跳把 app.log 刷屏。

**修復**:`gui/web_server.py` 新增 `HEARTBEAT_METHODS = {"get_status"}`;
心跳方法的請求日誌改為 **DEBUG**,其他方法(chat_turn / ingest /
list_sessions / …)維持 INFO。輪詢行為與頻率**不變**(狀態列需要它)。
關掉多餘的分頁即可線性降低心跳量。

2 個新測試在 `tests/test_web_server.py::TestHeartbeatLogging`
(get_status 記在 DEBUG 且不在 INFO;其他方法維持 INFO)。
`pyproject.toml` 0.9.15.2 → **0.9.15.3**。

### v0.9.15.2 — Web UI 手機格式（responsive mobile web）

使用者需求:「web介面提供手機web格式」。Web UI 在手機瀏覽器上打開時以
980px 虛擬桌面寬度排版再縮小、聊天視窗被網址列遮住、點輸入框整頁亂放大 —
三個都是缺手機適配造成。純前端修改（`gui/assets/index.html` +
`gui/assets/style.css`），Web server / shim / Api **零改動**；pywebview
桌面 GUI 共用同一份前端但不受影響（桌面 WebView 寬度遠大於斷點，
viewport meta 對它沒有作用）。

- **viewport meta（關鍵修復）**:`index.html` `<head>` 新增
  `<meta name="viewport" content="width=device-width, initial-scale=1,
  viewport-fit=cover" />` — 沒有它手機瀏覽器永遠以虛擬桌面寬度渲染，
  所有 CSS 斷點拿不到真實寬度。另加 `theme-color`（亮/暗雙色）、
  `mobile-web-app-capable` + `apple-mobile-web-app-capable`。
- **兩個響應式斷點（`style.css` 檔尾）**:
  - `@media (max-width: 820px)`（平板 / 大手機）:topbar 可換行、
    tab 觸控目標加大、`.chat-log` 高度改 `100dvh`（可見視窗高度；
    `100vh` 會被手機瀏覽器網址列遮住底部，不支援 dvh 的瀏覽器沿用
    原 vh 規則）、聊天气泡上限 90%、`.mcp-server-item` /
    `.danger-item` 改堆疊、modal 改 `min-width: 0; width: 94vw`
    （原 `min-width: 360px` 在 360px 手機上直接水平溢出）、
    toast 佔滿頂部、Sessions / Schedules 寬表格改表格自身水平捲動。
  - `@media (max-width: 480px)`（手機）:進一步縮 padding、隱藏版本
    徽章、工具列按鈕變小、聊天輸入 textarea `min-height: 44px`
    （觸控目標下限）。
- **iOS 聚焦縮放修復**:輸入框字號 < 16px 時 iOS Safari 聚焦會自動
  放大整頁 — ≤820px 斷點內把 `input / select / textarea` 統一拉到
  `16px`,更高特異性的 `.mcp-edit-form *` / `.mcp-raw-textarea` /
  `.chat-toolbar select` 逐一覆蓋。
- **觸控基礎**:`-webkit-tap-highlight-color: transparent` +
  `touch-action: manipulation`（移除灰色點擊閃光與 300ms 延遲）。

15 個新測試在 `tests/test_mobile_web.py`（viewport / theme-color /
apple meta 存在且在 `<head>` 內、兩個斷點存在且位於檔尾、dvh / 16px /
modal / toast / 表格捲動 / 觸控樣式防護、真實 ThreadingHTTPServer
round-trip 驗證 served index 帶 viewport meta）。
`pyproject.toml` 0.9.15.1 → **0.9.15.2**。

### v0.9.15.1 — Web UI 可綁定本機任何 IP（0.0.0.0 / 特定區網 IP / IPv6）+ `.env` 載入修復

使用者需求:「支援 web 可以 binding 本機任何ip」。三個修復 + 一組新行為:

- **`.env` 載入順序修復（v0.9.15.0 隱藏 bug）**:web 模式在 `Api()`
  建構（才觸發 `src.config` 的 `load_dotenv`）**之前**就讀
  `QDRANT_WEB_HOST` / `QDRANT_WEB_PORT`,所以 `.env` 裡的值被靜默
  忽略。現在 `run_web_server` 一進來就 `load_dotenv()`
  （`override=False`,真環境變數 / CLI 旗標仍優先）。
- **`0.0.0.0` = 全部 IPv4 介面**:啟動時以純 stdlib 三探測
  （UDP-connect trick 不送封包、`getaddrinfo(hostname)`、
  `gethostbyname_ex(hostname)`,去重、排除 loopback 與 `0.0.0.0`）
  列出本機所有非 loopback IPv4,banner 印出
  `also reachable at: http://<每個區網 IP>:<port>/`;自動開瀏覽器
  一律開**可解析的** loopback URL（`0.0.0.0` 本身不是可瀏覽位址,
  v0.9.15.0 會開到打不開的網址）。
- **IPv6 真的能綁了**:原本 `ThreadingHTTPServer` 是 AF_INET,
  `--host ::1` 必 `OSError`（但 loopback 名單卻含 `::1`）。新增
  `_ThreadingHTTPServer6`（AF_INET6）;`::` wildcard 另清
  `IPV6_V6ONLY=0` 成 dual-stack（一個 socket 同時服務 v4+v6）。
  URL 顯示自動加方括號（`http://[::1]:8334/`）。
- **banner `print()` 改 `flush=True`** — 管線 / 重導 / 分離啟動時
  不再因 stdout 緩衝而看不到 URL。
- **安全模型不變**:非 loopback 綁定（含 wildcard）記大聲 WARNING
  （wildcard 文案為 "ALL interfaces"）;Api 可驅動桌面工具,請只在
  可信區網使用。
- 測試:`tests/test_web_server.py` 24 → **40 條**（+16:`_format_url`
  / `_urls_for` / `_enumerate_lan_ips` 單元、wildcard 綁定 + 瀏覽器
  開 loopback、旗標覆寫 env、`.env` 先於 env 解析、IPv6 選
  AF_INET6 server、非 loopback WARNING、真實 bind 失敗回 1、真實
  IPv6 loopback bind）。
- 實機驗證:`--host 0.0.0.0` 啟動 → banner 列出 `192.168.50.154` /
  `172.22.160.1` / `172.19.16.1`,`/healthz` 經 127.0.0.1 與全部
  3 個 IP 皆回 200。

`pyproject.toml` 0.9.15.0 → **0.9.15.1**。

### v0.9.15.0 — Web 介面模式(可選擇以瀏覽器啟動,與 pywebview GUI 同一套前端)

新增 **Web 介面啟動方式** — 使用者可自由選擇桌面視窗(pywebview)或瀏覽器:

```bat
qdrant-agent-web.bat          REM 雙擊啟動 Web 版
python -m gui.app --web       REM 明確選擇
python -m gui.app             REM 不加旗標 = 原桌面視窗(預設不變)
python gui/web_server.py      REM 直接進入點
```

**設計:零重複、零前端改動** — `gui/web_server.py`(純 stdlib
`http.server`,無新依賴)提供:

- **同一套前端**:`GET /` 服務 `gui/assets/index.html`,並在 `<head>`
  注入 `/pywebview-shim.js` — 一個 ~40 行的 shim 用 `fetch` 實作
  `window.pywebview.api.<method>(...)`(Proxy、回傳 Promise)、
  `state`、`FileDialog` + `create_file_dialog`,並立即派發
  `pywebviewready`(無 boot race)。`app.js` / `diagrams.js`
  **原封不動**在瀏覽器運作。
- **同一套後端**:`POST /api/<method>` 直接呼叫 `gui.api.Api` 的
  公開方法(位置參數、`{"ok": true, "result": ...}` 信封;私有方法
  (`_` 開頭)與未知方法一律拒絕;結果不可 JSON 序列化時降級為錯誤
  信封,請求執行緒絕不無回應斷線)。`GET /healthz` 供探活。
- **版號跟隨**:頁面 `get_status` 的 version 由 pyproject.toml
  動態讀取(v0.9.14.1 機制),Web 與 GUI 顯示一致。
- **安全模型**:預設綁 `127.0.0.1:8334`(Api 可驅動桌面自動化與
  檔案工具,不可暴露區網;非 loopback 綁定記 WARNING)。可用
  `--host` / `--web-port` 或 `QDRANT_WEB_HOST` / `QDRANT_WEB_PORT`
  覆寫;`--no-browser` 不自動開瀏覽器。`create_file_dialog` 經
  tkinter(stdlib)在**伺服器桌面**開原生對話框 — 預設 localhost
  部署下就是使用者自己的桌面。
- 啟動後自動開瀏覽器;Ctrl+C 停止。

測試:`tests/test_web_server.py` 24 條 — shim 注入/契約、
`call_api_method` dispatch(位置參數、未知/私有/不可呼叫/拋例外/
非 JSON 結果、`__file_dialog`)、真實 ThreadingHTTPServer 臨時埠
HTTP 往返(index 注入、靜態服務、`../` 路徑攻擊 403、404、
/healthz、信封正反例、get_status version 轉發)、`gui.app` 選擇
旗標解析。`pyproject.toml` 0.9.14.1 → **0.9.15.0**。

### v0.9.14.1 — 畫圖任務為何「開了小畫家卻不畫」：工具迭代上限 6 → 20（env 可調）+ windows-mcp 啟動階梯 prompt 區塊

**故障鑑識**（`gui/app.log` + session JSON）:使用者「開啟小畫家畫一隻小狗」
→ LLM 開了 Paint、畫了一個頭部橢圓就停下,回「由於我無法再執行後續的滑鼠
繪製動作」+ 手動步驟說明。三層根因:

1. **工具迴圈上限太低**:`Agent.DEFAULT_MAX_TOOL_ITERATIONS = 6`。GUI 桌面
   自動化(啟動 → 截圖 → 點工具 → 拖曳 → 上色 → 存檔)需要 15-30 步,每輪
   都在第 6 步撞上限;`_tool_loop` 塞入「Please provide your final answer
   now」強制作答 → 對話標頭的 `7 calls` = 6 輪 + 1 次保底。今天三輪畫圖
   對話**全部**留下 `tool loop hit max_iterations=6` 警告。
2. **mspaint 啟動失敗燒掉額度**:`App(launch, mspaint)` → 「Mspaint not
   found in start menu.」;改用 `launch_executable` 漏了 `.exe` →
   「Executable does not exist: C:\Windows\System32\mspaint」— 6 次額度被
   浪費 2 次。
3. **system prompt 完全沒有 windows-mcp 指引**,LLM 全靠 trial-and-error。

**修復**:

- `MAX_TOOL_ITERATIONS` env(新,預設 20)— `ask()` / `chat_turn()` 的
  `max_tool_iterations` 預設改為 `None`,每輪即時經
  `_resolve_max_tool_iterations()` 解析(`os.environ` 直接讀,壞值/非正數
  回落預設只警告,不 crash);明確傳入的 int 仍優先。`src/config.py`、
  `.env.example` 同步記錄。
- **`_tool_loop` bail-out 訊息改寫**:從硬性「provide your final answer
  now」改為「列出已完成的步驟、下一步是什麼、請使用者說『繼續』、絕不
  把沒做完的事說成完成」— 直接對症下藥,避免 LLM 把進度報告寫成
  「我無法繼續」。
- **system prompt 新增「Windows desktop automation (windows-mcp)」區塊**:
  App 啟動階梯(`launch` → `launch_executable` **含 .exe 的絕對路徑** →
  `PowerShell Start-Process mspaint`)、「畫圖」優先走 **PowerShell
  System.Drawing 腳本繪製**(一次呼叫畫整隻,對照 09-03 台灣黑熊成功案例;
  只有使用者明確要「用滑鼠畫/一步一步畫」才走 Click/Move 拖曳)、
  迭代預算守則(獨立工具呼叫盡量批次、預算用完時如實回報進度)。

- **版號同步(v0.9.14.1 追加)**:GUI footer / splash 版號原為
  `gui/api.py` 硬編碼 `'0.8.0'`,悄悄落後九個 minor 版本。現改為
  `_app_version()` 於 import 時從 **pyproject.toml**(文件明定的單一
  真相來源)regex 讀取 — 以後 bump pyproject 即全站同步,GUI 永不再
  漂移;`tests/test_gui_api.py` 改為比對 pyproject 動態值鎖死此約定。

測試:`tests/test_max_tool_iterations.py` 16 條(env 解析 ×6、ask/chat_turn
per-call 解析 ×4、bail-out 訊息形狀 ×1、prompt 區塊 ×5,含 legacy 排除)。
`pyproject.toml` 0.9.14.0 → **0.9.14.1**。

### v0.9.14.0 — Chat 介面 MCP 工具挑選器（每輪暫時啟用/關閉，不用改 .env）

Chat 工具列新增 **🧰 工具** 按鈕：展開面板列出**全部** MCP 工具（依
server 分組、checkbox 可勾選），勾選狀態作為**該輪請求的 per-TOOL
allow-list** 直接傳給後端 — 不用改 `.env`。

- **語意**：以「**停用清單**」記憶（`localStorage['chat-disabled-tools']`）
  — 之後新增的工具預設啟用，使用者只記得關掉了哪些。「暫時」= 存在
  localStorage（跨 GUI 重啟保留），刪除即恢復預設。
- **按鈕徽章**：`🧰 N/M`（啟用數/總數）；面板內提供**全開 / 全關 /
  重設**。
- **後端鏈路**：`gui/api.py::chat_turn(active_tools=…)` →
  `Agent.chat_turn(active_tool_names=…)` →
  `McpManager.active_specs(tool_names)` — **per-TOOL allow-list 覆蓋
  env 的 server 閘門**（僅該輪）；`None` = 未選擇，`.env` 的
  `MCP_ACTIVE_SERVERS` 照常生效。明確**空清單 = 全部停用**（honoured，
  不回落 — 與 server 閘門的零匹配回落不同，這是使用者的明確意圖）。
- **UI 安全**：面板用 `createElement` + `textContent` 組裝（無
  XSS 面）；`[hidden]` 明確覆寫 display；i18n 四語系
  （chatTools / chatToolsAll / chatToolsNone / chatToolsReset）。

測試：`tests/test_mcp_active_servers.py` +6（per-tool 覆蓋 env 閘門、
空清單全停、None 走 env、未知工具名忽略、hidden 追蹤、active_tools
轉發）；`tests/test_gui_chat_tools.py` 11 條（bridge 轉發/預設 None、
agent pass-through、index.html/app.js/style.css source guards、四語系）。
`pyproject.toml` 0.9.13.2 → **0.9.14.0**。


### v0.9.13.2 — 修正閘門「發現陷阱」：list_mcp_tools 與宣告面一致化

**Live 故障**（2026-09-04，使用者回報「啟用 MCP_ACTIVE_SERVERS 後
windows-mcp 異常」）：閘門隱藏 windows-mcp 時，LLM 透過
`list_mcp_tools` 仍看到全部 100 個工具的目錄 → 嘗試「使用
windows mcp」時**把 `{"mode": "launch", "name": "mspaint"}` 當純文字
輸出**，從未發出真正的 tool_call（gui/app.log 四輪中唯一的工具
dispatch 只有 `list_mcp_tools`，`launch` 一次都沒發生）。小畫家自然
沒出現 — 與 RDP 無關。

**根因**：宣告面（閘門過濾後的 tools）與**發現面**（list_mcp_tools
的完整目錄）不一致 — LLM 看得到卻呼叫不到，就會用文字模仿呼叫。

**修正（單一閘門來源）**：

- `McpManager.active_specs()` / `active_tools()`（新）：閘門邏輯
  （過濾、未知名警告、零匹配回落完整面）搬進 manager，並追蹤
  `last_hidden_servers`。
- `Agent._chat_tools()` 改為 `active_specs()` 的薄包裝（to_openai）。
- **`MetaLocalHost.list_mcp_tools` 目錄一致化**：只列**暴露的**工具；
  有閘門時附上 `hidden_servers` 清單 + `hidden_note`（「這些 server
  未暴露、**無法呼叫**，請使用者加入 MCP_ACTIVE_SERVERS 後重試」）—
  LLM 不再看到不能呼叫的工具，並有明確的請求路徑。
- **System prompt 守衛**：tool-selection rules 新增「未宣告的工具
  **無法**呼叫 — 絕不在純文字裡假裝呼叫工具；目錄標記 hidden 時，
  請使用者把 server 加入 MCP_ACTIVE_SERVERS」。
- `.env` 閘門恢復啟用，**這次包含 `windows-mcp`**（chat 桌面自動化
  需要；代價：工具面回到 ~100 個 ≈ 36k tokens/輪，不用桌面自動化時
  把 windows-mcp 從清單拿掉即省回 ~25k）。

測試：`tests/test_mcp_active_servers.py` +2（meta 目錄閘門過濾 +
hidden note、無閘門無 hidden 欄位）；`tests/test_meta_local_host.py`
fake manager 補 `active_tools`。警告訊息搬移後斷言同步。
`pyproject.toml` 0.9.13.1 → **0.9.13.2**。


### v0.9.13.1 — 對話標頭加上 prompt cache 資訊（⚡ cached）

每對對話的 token 標頭現在會顯示 **provider 快取命中量**：

```
開始 09:15 → 結束 09:17 · ↑ 11,113 / ↓ 142 tok · ⚡ 10,880 cached
```

- **Agent**：`_extract_usage()` 新增擷取 `prompt_tokens_details.cached_tokens`
  （provider 從快取服務的 prompt 部分 — Together/BaseTen 實測會回報；
  缺此欄位 → 0）；`_accumulate_usage()` 將快取 token 一併跨 tool 迴圈
  加總。
- **前端**：`_chatTokensText()` 在 `cached > 0` 時附加 `· ⚡ N cached`
  （千分位）；未命中/舊 session/provider 未回報 → 不顯示該段。
  這正是 v0.9.13.0 的「固定 together 快取」策略的成效可視化 —
  命中時 ⚡ 數字 ≈ ↑ 數字，代表該輪大部分 prefill 都走了快取
  （省錢且更快）。

測試：`tests/test_chat_usage.py` 更新（_usage helper 同時帶兩個
details nest、extract/accumulate/tool-loop 的 cached 斷言）+
`tests/test_chat_time_bar.py` jsdom 更新（s9 帶快取段、s10/s12
無快取不出現 ⚡）。
`pyproject.toml` 0.9.13.0 → **0.9.13.1**。


### v0.9.13.0 — MCP 工具面彈性閘門（MCP_ACTIVE_SERVERS，每輪省 ~25k tokens）

**問題**（v0.9.11.1 實測）：每次 chat 都附上**全部** MCP 工具 schema —
100 個工具 = 每輪 36,312 prompt tokens（system 4.3k + 工具 ~32k）。
「hello」這種閒聊輪根本用不到 95% 的工具，但 prefill 照付。

**答案：不必要。** 工具 schema 只需要在那「一輪」出現，而且只需要
該輪用得到的伺服器。改善機制：

- **`MCP_ACTIVE_SERVERS`**（逗號分隔 `server_name` 清單；空 = 完整面）：
  `Agent._chat_tools()` 依閘門過濾每次請求的工具面。實測核心組
  （qdrant/web/filesystem/datetime/scheduler/todo/meta）= 53 工具，
  **prompt 36,312 → 11,022 tokens（-70%）**。建議清單：
  `qdrant-local,web-local,filesystem-local,datetime-local,scheduler-local,todo-local,meta-local`
  （v0.9.21.0+ 加 `chat-history-local`；v0.9.23.0+ 加 `telegram`；
  v0.9.24.0+ 加 `stock-local` — 新 LocalHost 不加進清單 = 該組工具
  被隱藏，LLM 無法呼叫 = v0.9.13.2 discovery trap）
- **防護欄**：未知 server 名 → 警告 + skip；閘門匹配 0 個工具 →
  **回落完整面**（不會靜默停用工具呼叫）；`meta-local` 建議保留，
  LLM 仍可透過 `list_mcp_tools` 發現被隱藏的工具；排程 fire 等
  manager dispatch 不受閘門影響（照樣 by-name 路由）。
- **prompt cache 實測**（Together 自動快取）：current-route
  （modal,together,...）快取命中**不穩定**（cached=0/0/31,360 —
  Modal 不快取且 serving provider 會換）；**固定 together 後每輪
  cached=31,360、延遲穩定 2.1-2.4s**（未命中冷呼叫 4.8s）。
  想吃滿快取：`OPENROUTER_PROVIDER_ORDER=together` +
  `OPENROUTER_ALLOW_FALLBACKS=false`。
- **Makora 快取實測**（後續追加）：Makora 有快取（cached=32,256
  出現過一次、cache-read 定價 ~6× 便宜）但命中**不確定**
  （3 次相同請求 = 0 / 0 / 32,256，疑似後方多實例或快取寫入
  非同步），且**命中不反映到延遲**（有無快取都 ~4.0-4.2s），
  4 次中還觀察到 1 次 429 限流。結論：此模型的快取首選仍是
  Together（命中確定 + 實質加速 1.7-1.8s）。
- **BaseTen/fp8 快取實測**（後續追加）：tag `baseten/fp8`、
  quantization fp8、uptime 三者最佳（1d **99.81%**）。快取暖機
  **最快** — 第 2 次成功呼叫就命中 `cached=36,288`（比 Together
  的 31,360 快取得更完整）且持續保溫；但 4 次中仍有 1 次 429
  （容量依舊緊），且快取後延遲 2.2-3.0s 仍輸 Together 的
  1.7-1.8s。排序：**together（快取+延遲）> baseten/fp8（快取
  品質+uptime，有 429 風險）> makora（命中晚、無延遲收益）**。

** `.env` 建議組合**（可省 ~70% prefill tokens + 穩定快取）：

```env
MCP_ACTIVE_SERVERS=qdrant-local,web-local,filesystem-local,datetime-local,scheduler-local,todo-local,meta-local,chat-history-local,telegram,stock-local
OPENROUTER_PROVIDER_ORDER=together
OPENROUTER_ALLOW_FALLBACKS=false
OPENROUTER_REASONING_EFFORT=low
```

測試：`tests/test_mcp_active_servers.py` 7 條（空閘門=完整面、只暴露
listed servers、未知名警告+skip、零匹配回落、逗號空白容錯、chat_turn
整合閘門、閘門後 dispatch 仍 by-name 路由）。
`pyproject.toml` 0.9.12.0 → **0.9.13.0**。


### v0.9.12.0 — 對話方塊顯示每對對話的 token 用量

每一問答對的 token 消耗現在會顯示在助理方塊上方的標頭列（與
v0.9.9.4 的「開始 → 結束」時間同一行）：

```
開始 09:15 → 結束 09:17 · ↑ 36,403 / ↓ 154 tok
```

- **Agent 端**：`_extract_usage()` 從每次 `chat.completions.create`
  回應擷取 prompt / completion / total / reasoning tokens（provider
  未回 usage → None）；`_accumulate_usage()` 把 tool 迴圈的多次呼叫
  **加總**成一對的總成本（`calls` 記錄呼叫次數，>1 時前端會註明）；
  `Agent.last_usage` 在 `ask` / `chat_turn` 進入點重置為 None —
  provider 不回 usage 時舊數字不會殘留。
- **GUI 橋接**：`gui/api.py::chat_turn` 把 `last_usage` 包進 JS
  payload 的 `usage` 欄位（僅接受真 dict，MagicMock agent / 舊版
  provider 不會污染橋接）。
- **前端**：`_chatTokensText()`（千分位格式、`↑ prompt / ↓ completion
  tok`、多呼叫註明 `· N calls`）；`sendChat()` 把 `r.usage` 傳給
  `appendChatMsg()`，存成訊息物件的 `usage`（跟著 session JSON
  保存）；`renderChat()` 把時間與 token 合併進 `.chat-msg-time`
  標頭（純 `textContent`，無 XSS 面）；舊 session 無 usage →
  只顯示時間。

測試：`tests/test_chat_usage.py` 12 條（extract 欄位/缺 usage/缺
details、accumulate 加總、tool loop 聚合與 None、ask/chat_turn 生命
週期與舊數字重置、gui wrapper 帶 usage/非 dict 防禦）+
`tests/test_chat_time_bar.py` 更新（guards + jsdom 行為測試新增
token 格式/多呼叫/零值情境）。
`pyproject.toml` 0.9.11.1 → **0.9.12.0**。


### v0.9.11.1 — Chat 延遲診斷與改善（reasoning effort + GUI 預熱）

針對「hello 回應 ~6 秒」的實測診斷（live-box 數據）：

| 成本來源 | 實測 | 狀態 |
|---|---|---|
| glm-5.3-flash **強制 reasoning**（連 hello 都燒 281 個思考 token） | 基線 4.4s | ✅ `OPENROUTER_REASONING_EFFORT=low` → 1.4s / 0 tokens（`{"enabled": false}` 被端點 400 拒絕，但 effort 可用） |
| **100 個工具 schema**（system 4.3k + 工具 ~32k = **每輪 36,312 tokens** prefill） | +1-2s/輪 | 📋 設定面：停用不用的遠端 MCP server（`config/mcp.json`）即可縮小 |
| **首次存取 mcp_manager ~15s**（11 個 LocalHost + 遠端連線）+ import 8s | 第一輪專屬 | ✅ GUI 啟動時背景預熱 |
| Together 端點吞吐變異 | 2.5-10s 同請求 | — 端點側 |

**改善 1 — `OPENROUTER_REASONING_EFFORT`**（`minimal|low|medium|high`）：
以 **top-level** `extra_body["reasoning"]={"effort": ...}` 注入（OpenRouter
文件位置，非 `provider` 內）；白名單驗證、壞值 skip 不 raise；只影響
openrouter provider；未設定 = 請求完全不變。`.env` 已設 `low`。

**改善 2 — GUI agent 預熱**：第一次 `get_status` 輪詢（GUI 開啟即觸發）
起動一次性 daemon thread 建立 `mcp_manager` + 解析 provider — 15 秒
建置成本移出第一輪對話；`Agent.mcp_manager` property 改 **double-checked
locking**（預熱與首輪對話競爭時只建一次），`Api._prewarm_started` 保證
執行緒只起一次。

驗證：live 實測 chat_turn（tools=ON、effort=low）**7.2s → 2.5-5.8s**
（首輪含 TLS/連線建立；Together 端點吞吐變異仍存在）。
測試：+5（effort 注入/組合/壞值/未設定/到達 create）+ 5（prewarm
一次性執行緒/flag 短路/同步建置/失敗吞掉/thread target）。
`pyproject.toml` 0.9.11.0 → **0.9.11.1**。


### v0.9.11.0 — MCP 日期時間查詢核心工具（本地時間 + UTC+0）

新的 `DateTimeLocalHost`（**第 11 個 in-process LocalHost**）提供零參數
核心工具 `get_current_time`，一次呼叫同時回傳：

- **本地時間**：ISO 8601（含時區 offset，如 `2026-09-03T14:30:00+08:00`）、
  `date` / `time` 字串、年月日時分秒結構化欄位、時區名稱、UTC offset
  （ISO 8601 `+08:00` 格式，`_fmt_utc_offset()` 處理負 offset 與秒級
  offset）、星期（英文名 + ISO 數字 1-7，tuple 驅動不受 C locale 影響）
- **UTC+0 時間**：同欄位結構（offset 恆為 `+00:00`、tzname `UTC`）
- **Unix epoch 秒數**（毫秒精度）+ 派生日期提示（note）

設計要點：
- **掛載點**：in-process 由 `McpManager.from_config` 自動註冊（在 Meta
  之前，`list_mcp_tools` 目錄即含此工具；保留參考 `_datetime_local_host`）；
  stdio server `src/mcp_server/time_server.py` 供 Claude Desktop / Cline
  等外部 client（`python -m src.mcp_server.time_server`）
- **唯讀、零參數、無沙盒議題** — handler 只讀本地時鐘
- **system prompt**「Tool selection rules」新增規則：『現在幾點』『今天
  幾號』『what time is it』『today's date』以及任何需要「現在」的計算
  （deadline、排程提示）一律呼叫 `get_current_time`，絕不從記憶猜時間
- v0.8.6.1 的 LocalTool argument coercion 對此零參數工具同樣生效
  （`{}` / `{"args": {}}` / `None` 都能乾淨呼叫）

測試：`tests/test_datetime_host.py` 18 條（工具面與 schema、local/UTC
ISO 可解析 + 互相一致（≤2s 雙時鐘讀取偏差）、offset 格式 regex、epoch
貼近牆鐘、`_fmt_utc_offset` 邊界、coercion round-trip（bare/wrapper/
None）、manager 註冊 + host 參考 + 端到端 dispatch、MetaLocalHost 目錄、
stdio server spec 一致性 + handler 路徑、system prompt 出席/legacy 缺席）。
`pyproject.toml` 0.9.10.0 → **0.9.11.0**。


### v0.9.10.0 — Chat 對話 context 壓縮（摘要 / 卸載 / 選擇性壓縮，全部可選）

三層 opt-in 機制讓過長的 chat session 保持可用（**預設 `off` — 不設定 =
v0.9.9.4 行為完全不變**）：

- **摘要歷史紀錄（Summarization）**：history 超過
  `CHAT_COMPRESS_TRIGGER_MESSAGES`（預設 24）則把較舊的尾端折疊成**一則
  system 摘要訊息**，最近 `CHAT_COMPRESS_KEEP_MESSAGES`（預設 12）則原文
  保留。摘要預設用 LLM 生成（`CHAT_COMPRESS_SUMMARY_MODE=llm`；任何失敗
  自動退回無網路的 extractive 摘要器）；摘要以「舊段前綴 hash」快取 —
  session 增長時只在折疊部分變化時才重新摘要（LRU 上限 8）。
- **卸載大型資料（Offloading）**：成功的工具輸出超過
  `CHAT_OFFLOAD_THRESHOLD_CHARS`（預設 20000）就寫入檔案（預設位於
  filesystem read sandbox 的 `chat_offloads/`，LLM 之後可用 `read_file`
  讀回全文），tool 訊息變成指標 + 800 字預覽；工具**錯誤永不卸載**
  （保持可見）；寫檔失敗自動退回 inline，工具呼叫永不中斷。system
  prompt 增加 offloading 說明區塊（僅 tools_enabled 模式）。
- **語意與選擇性壓縮**：任何超過 `CHAT_COMPRESS_MSG_MAX_CHARS`（預設
  4000）的 history 訊息，圍欄 code block 與 JSON 形狀區塊**逐字保留**，
  只壓縮周邊自然語言（保留頭尾句 + 刪略標記
  `…[prose compressed: N chars omitted]…`；CJK/西式斷句都支援）。

設定機制（`.env`，全部可選；壞值一律安全回落預設，不會 crash）：

```env
CHAT_CONTEXT_COMPRESSION=off|auto|aggressive   # aggressive = 門檻/預算減半
CHAT_COMPRESS_TRIGGER_MESSAGES=24
CHAT_COMPRESS_KEEP_MESSAGES=12
CHAT_COMPRESS_SUMMARY_MODE=llm                 # 或 extractive（不呼叫 LLM）
CHAT_COMPRESS_SUMMARY_MAX_CHARS=900
CHAT_COMPRESS_MSG_MAX_CHARS=4000
CHAT_OFFLOAD_THRESHOLD_CHARS=20000
QDRANT_OFFLOAD_DIR=                # 空 = <read sandbox>/chat_offloads
```

新模組 `src/agent/context_compress.py`（`ContextCompressor`）：
`compress_history()` 掛在 `Agent._messages_with_user`、
`offload_tool_result()` 掛在 `Agent._run_tool_call` — off 模式 = 位元組
等同 no-op；壓縮任何失敗一律退回未壓縮 history。

測試：`tests/test_context_compress.py` 35 條（config 解析與壞值回落、
aggressive 減半、摘要觸發/LLM 單次呼叫+快取/extractive 後備/cap、
offload 讀回沙盒/threshold 下限/自訂目錄/寫入失敗後備/錯誤不卸載、
選擇性壓縮 code 圍欄與 JSON 逐字/全結構化不動/CJK 斷句、Agent 整合鏈路
+ 壓縮失敗降級 + system prompt 區塊）。
`pyproject.toml` 0.9.9.4 → **0.9.10.0**。

### v0.9.94.0 — 結論 grounding guard：UUID 存證 + Jev 驗證（user: 「為防止fake calling，是否為每一次 calling都啟一個uuid所保存，在LLM完成結論時使用jev確認 uuid內容以確認LLM是不只使用記憶推論」）

使用者提案直接落地（混合式，控制 Jev 成本）：

* **UUID 存證**（零成本）：每次成功的工具派發在 `_run_tool_call` 以
  `uuid8 → {tool, args, text[:1500], 特徵詞}` 存進 `_turn_result_refs`
  （回合級，兩入口重設；error 結果不入冊）；app.log 記
  `[ResultRef] <uuid> <- <tool>`。
* **結論時驗證**（`_result_grounding_guard`，掛 `_chat_turn_impl` 尾端
  calendar guard 之後）：
  1. **確定性快篩**（零 Jev 成本）— 結論含任一存證特徵詞
     （價格/日期/ID/長英文詞，`_distinctive_tokens`）= 明確有用工具
     結果 → 通過；
  2. 快篩不過 → **一次 Jev noul**「結論是否明確以下方工具結果內容為
     依據（而非只靠記憶推論）？」→ p < `QDRANT_RESULT_GROUNDING_PROB`
     （0.6）→ 一次**無工具糾正重寫**（僅依 uuid 存證內容；結果未支持
     的部分明說沒有）；
  3. shadow 只記錄；off 預設、live `.env` on；任何失敗保留原答案。
* TurnTiming 增 `grounding=%.2fs` 段。
* 測試 +10（`tests/test_result_grounding.py`：uuid 存證/error 不入冊/
  快篩通過零 Jev/off/shadow/未 grounding 觸發重寫/Jev 認為已
  grounding 保留/失敗保留/無存證跳過/雙入口重設）。

`pyproject.toml` 0.9.93.0 → **0.9.94.0**。


### v0.9.9.4 — 每則對話方塊上方顯示該則訊息的時間（取代 v0.9.9.3 整體時間條）

使用者回饋：要的是**每個訊息方塊上方**的時間，不是整個對話視窗的時間條。
v0.9.9.3 的 `#chat-time-bar` 整體條移除，改為逐則時間標頭：

- **使用者訊息**：方塊上方顯示單一送出時間 — 今天 `HH:MM`、非今天
  `YYYY/MM/DD HH:MM`。
- **助理回覆**：顯示生成區間 `開始 HH:MM → 結束 HH:MM` — `sendChat()` 在
  送出當下記錄 `reqStart`，`appendChatMsg('assistant', …, reqStart)` 把它
  存成訊息物件的 `startTs`（跟著 session JSON 一起保存）；開始/結束跨日
  （例：23:59 提問、00:01 回覆）兩側自動升級為完整日期。
- **防禦性**：舊版 session（訊息無 `ts`）或無效時間字串 → 該則不顯示標頭；
  純 `textContent` 組裝（無 XSS 面）。
- **版面**：每個方塊包進 `.chat-msg-row`（flex column）讓時間線位於方塊
  **外部上方**，並與方塊同側對齊（user 靠右、assistant/error 靠左）；
  `.chat-msg-row .chat-msg { max-width: 100% }` 避免雙層 80% 上限相乘。
- **語言切換**：標頭含 `chatStart` / `chatEnd` 標籤（四語系沿用），
  `setLang()` 改為重跑 `renderChat()`（純 chatSession 重建，冪等無網路）
  讓標籤跟著語言換。

測試：`tests/test_chat_time_bar.py` 全面改寫 — 8 條原始碼 guard
（strip 移除反轉 guard、三 helper 存在、renderChat 包裝列 + textContent、
appendChatMsg 帶 startTs、sendChat 捕捉 reqStart、setLang 重繪、四語系）+
1 條 Node+jsdom 行為測試（真實抽出 `_chatMsgTimeText` 跑 8 情境：無 ts /
今天 / 過去日 / 同日區間 / 跨午夜區間 / 無 startTs / 無效 ts / 無效
startTs 後備）。
`pyproject.toml` 0.9.9.3 → **0.9.9.4**。


### v0.9.9.3 — Chat 對話上方顯示「開始 / 結束時間」時間條

Chat 分頁的對話框（`#chat-log`）上方新增一條 `#chat-time-bar` 時間條，
以 `開始: HH:MM → 結束: HH:MM` 標示這段對話紀錄的起訖時間：

- **資料來源**：訊息物件既有的 `ts`（ISO 時間戳，`appendChatMsg` 每則
  訊息都會記）。第一則與最後一則訊息的 `ts` 夾出可見 transcript 的範圍。
- **跨日顯示**：起訖同一天只顯示 `HH:MM`；跨日自動升級為完整
  `YYYY/MM/DD HH:MM`。
- **單一收斂點**：`renderChat()` 尾端呼叫 `renderChatTimeBar()` —
  新訊息 / 載入 session / 清除 / 開新對話全部路徑自動同步；
  `setLang()` 也補呼叫，切換介面語言時「開始 / 結束」標籤跟著換。
- **防禦性**：空對話、舊版 session（訊息無 `ts`）、無效時間字串 →
  時間條整條隱藏（不顯示殘缺資訊）；純 `textContent` 組裝（無 XSS 面）；
  `replaceChildren` 重繪不累積子節點。
- **CSS**：`.chat-time-bar[hidden] { display: none; }` 明確覆寫
  `display:flex`（UA 的 hidden 預設樣式會被作者樣式蓋掉）。
- **i18n**：`chatStart` / `chatEnd` 四語系（zh-TW / zh-CN / en / ja）。

測試：`tests/test_chat_time_bar.py` 8 條 — 7 條原始碼 guard（index.html
div + hidden 預設、三個 helper 存在、`renderChat()` 與 `setLang()` 掛鉤、
四語系標籤、CSS `[hidden]` 覆寫、textContent XSS guard）+ 1 條 Node+jsdom
行為測試（真實抽出 `app.js` 函式對真 DOM 跑 7 個情境：空 / 無 ts 舊資料 /
同日 / 跨日 / 單訊息 / 無效 ts / 重繪不重複）。
`pyproject.toml` 0.9.9.2 → **0.9.9.3**。


### v0.9.9.2（OpenRouter provider routing — `order` / `allow_fallbacks` / `require_parameters` / `data_collection` / `ignore` / `quantizations` / `sort` + `:nitro` / `:floor` 快速通道）

新增 **8 個 `OPENROUTER_*` 環境變數**（`src/config.py`），由
`Agent._apply_openrouter_routing()`（classmethod）解析成 OpenRouter 官方
[provider routing](https://openrouter.ai/docs/features/provider-routing)
形狀，在 `_pick_provider()` 解析為 **openrouter** 時掛到
`self._extra_body`，並於**全部 4 個 `chat.completions.create` 呼叫點**
（`_tool_loop` 迴圈 + bail-out、`ask` legacy、`chat_turn` legacy）以
`**self._routing_create_kwargs()` 注入 `extra_body={"provider": {...}}`：

| 變數 | 對應 provider 欄位 | 解析規則 |
|---|---|---|
| `OPENROUTER_PROVIDER_ORDER` | `order: [slug, ...]` | 逗號分隔 → list |
| `OPENROUTER_ALLOW_FALLBACKS` | `allow_fallbacks: bool` | `true`/`false`（其他值 skip + 警告） |
| `OPENROUTER_REQUIRE_PARAMETERS` | `require_parameters: bool` | 同上 |
| `OPENROUTER_DATA_COLLECTION` | `data_collection` | `allow`/`deny`（其他值 skip） |
| `OPENROUTER_IGNORE` | `ignore: [slug, ...]` | 逗號分隔 → list |
| `OPENROUTER_QUANTIZATIONS` | `quantizations: [...]` | 白名單 `int4/int8/fp4/fp6/fp8/fp16/bf16/unknown`，未知名 skip |
| `OPENROUTER_SORT` | `sort` | `price`/`throughput`（其他值 skip） |
| `OPENROUTER_MODEL_SUFFIX` | model slug 後綴 | `:nitro`（最快）或 `:floor`（最低價） |

`.env` 範例（全部留空 = 行為與 v0.9.9.1 完全相同）：

```bash
OPENROUTER_PROVIDER_ORDER=DeepInfra, Parallax
OPENROUTER_ALLOW_FALLBACKS=true
OPENROUTER_REQUIRE_PARAMETERS=false
OPENROUTER_DATA_COLLECTION=deny
OPENROUTER_IGNORE=Provider1, Provider2
OPENROUTER_QUANTIZATIONS=int4,int8
OPENROUTER_SORT=throughput
OPENROUTER_MODEL_SUFFIX=:nitro
```

`:nitro` 是速度優先快速通道、`:floor` 是價格優先快速通道 —
兩者都直接附加在 model 字串後，由 OpenRouter 路由。

設計要點：

* **空配置注入 Nothing** — `_routing_create_kwargs()` 回傳 `{}` 時
  create 呼叫完全不含 `extra_body`（byte-identical 舊請求）；
  非 openrouter 供應商一律 `{}`（其他 OpenAI 相容 API 會拒絕未知欄位）。
* **容忍錯字** — 單一 routing 設定錯誤只 skip 該欄位 + log 警告，
  不讓 chat 整個掛掉。
* **SDK 語意** — `extra_body` 由 OpenAI SDK merge 進 JSON request
  body；先前版本曾把 `{"provider": ...}` 直接 `**` 展開成裸
  `provider=` kwarg（到不了 wire），已修正為
  `{"extra_body": {"provider": {...}}}`。
* model 後綴已寫在 `OPENROUTER_CHAT_MODEL`（`...:nitro`）也相容 —
  `_apply_openrouter_routing` 偵測已有後綴則不重複附加。

`.env.example` 新增完整註解區塊。**27 個新測試**
`tests/test_openrouter_routing.py`（欄位解析、白名單 skip、
model 後綴、extra_body 抵達 create 的 kwargs 斷言、非 openrouter
不注入、空配置零參數）；`test_llm_providers.py` 19 passed 不變。
`pyproject.toml` 0.9.9.1 → **0.9.9.2**。


### v0.9.9.1（hotfix：log 異常分析 upsert 全數失敗 — point ID 不是合法 UUID，Qdrant 回 400）

**症狀**：呼叫 `ingest_log` 後 `log_docs` collection 永遠是 0 points，工具回
`{"error": "Qdrant upsert failed: UnexpectedResponse()"}`。parse 與 embedding
（local llama.cpp Qwen3 4096 維）都正常 — 敗在最後一步 `client.upsert()`。

**Root cause**：`LogStore.make_entry_id()` 回傳 `sha1(source + raw_line)[:24]`
— **24 位 hex 字串**。Qdrant point ID 只接受「無號整數或 UUID」，24-hex
兩者皆非 → 每次 upsert 都被 HTTP 400 拒絕：
`Format error in JSON body: value 74ddffe... is not a valid point ID, valid
values are either an unsigned integer or a UUID`。

**修復**：改為 `str(uuid.UUID(sha1_hex[:32]))` — 取 digest 前 32 hex 格式化成
canonical UUID 字串。完全確定性（同 source + 同行 → 同 ID），re-ingest =
upsert 覆蓋語意不變；`remove_logs` 走 payload filter 完全不受影響。
`log_docs` 先前是 0 points，無既有資料需要遷移。

**測試**：`tests/test_log_analyzer.py` 既有斷言 `assert len(a) == 24`
把 bug 鎖死（v0.9.5.7 教訓重演）— 改為 `str(uuid.UUID(a)) == a`
（ID 必須能解析成合法 UUID）。**106 passed**。

**實證**（2026-09-02，本機 Qdrant + llama.cpp Qwen3-Embedding-8B）：
`ingest_log` 5 行 syslog → `points_count=5` → `get_log_stats` 讀回
`total_points=5 / by_level {info:2, error:2, critical:1}` →
`search_logs("brute force password")` 語意命中兩行 Failed password
（score 0.574）→ `remove_logs` 清 5 → 回 0。
`file_docs`（167,898 points）全程不受影響。

### v0.9.9.0（新功能：MCP 本地核心工具執行 — bash / awk / grep / python，強化 LLM 作業彈性並降低工作負載）

新的 MCP 擴充功能：給 LLM **本地運算能力** — 格式轉換、欄位加工、批量文字處理等「會燒掉 context 的工作」改在沙盒內用真實工具完成，只回傳（有上限的）結果。

**工具面（in-process `ExecLocalHost` 自動註冊 + stdio server `python -m src.mcp_server.exec_server`）：**

* `run_shell(command, timeout?)` — 平台原生 shell（Windows `cmd /c`、POSIX `/bin/sh -c`）
* `run_bash(script, timeout?)` — GNU bash；**Git for Windows 自帶 awk/grep/sed**，完整 Unix 管線可用（`grep ERROR app.log | awk -F, '{print $2}' | sort | uniq -c`）
* `run_grep(pattern, paths?, fixed?, ignore_case?, invert?, count?, filenames_only?, line_numbers?, after/before_context?, max_results?)` — **純 Python 實作的 grep**（零依賴、跨平台保證可用；目錄遞迴、二進制與 >4 MB 檔案自動跳過、輸出 200 行上限）
* `run_awk(script, file, field_separator?, timeout?)` — 系統 awk 欄位加工（`awk -F, '{s+=$3} END {print s}'`）；機器上無 awk 時回清晰錯誤並建議 `run_bash` / `run_python` 替代
* `run_python(code, timeout?)` — `sys.executable -c <code>`；最彈性的轉換器（json/csv 轉換、regex 擷取、報表計算），cwd = write sandbox，可直接寫輸出檔回沙盒

**降低 LLM 工作負載的設計**：系統提示詞明確教導「**找到答案優先用 grep/awk/python 一行指令，不要把整個檔案 cat 進 context**」；工具選擇指引（找行 → run_grep、欄位 → run_awk、多步管線 → run_bash、其他 → run_python、OS 原生 → run_shell）。

**安全模型**：

* **雙沙盒** — 每個 subprocess 的 cwd = filesystem **write** root；child env 暴露 `QDRANT_READ_ROOT`（真 read root）與 `QDRANT_WRITE_ROOT`，腳本可用相對路徑寫 write root、用 `$QDRANT_READ_ROOT` 讀 read root
* **超時** — 每次呼叫 timeout（`QDRANT_EXEC_TIMEOUT` 預設 30s、硬上限 120s），逾時回報 `timed_out: true` + 部分輸出
* **輸出上限** — stdout/stderr 各截斷（`QDRANT_EXEC_MAX_OUTPUT` 預設 65536 字元）防 context 爆量
* **災難黑名單** — `rm -rf /`、`mkfs`、`dd of=/dev/sd*`、fork bomb、`format C:`、`del /f /s`、`rd /s`、`shutdown/reboot`、`diskpart`、`bcdedit`、`chmod -R 777 /` 等機器級破壞命令直接拒絕（沙盒內檔案操作不受限）；`QDRANT_EXEC_BLOCKLIST=off` 可關閉（不建議）
* **工具探索** — bash/awk 自 Git for Windows 或 OS；grep 純 Python 永遠可用；python 用 `sys.executable` 必然存在
* Windows 細節：WSL 的 `system32\bash.EXE` stub 會在無 WSL 時失敗 — 探測優先取 Git Bash 的 bash.exe；MSYS awk 不吃反斜線路徑，一律轉 `C:/...` posix 形式傳入

**新 env vars**（`.env.example` 同步）：`QDRANT_EXEC_TIMEOUT`（預設 30）、`QDRANT_EXEC_MAX_OUTPUT`（預設 65536）、`QDRANT_EXEC_BLOCKLIST`（空 = 啟用黑名單）。`config/mcp.json.example` 附 exec stdio 範例。

**新模組** `src/exec_tools.py`：黑名單 regex 群、bash/awk/grep 探測、`run_shell/run_bash/run_awk/exec_python`（subprocess capture + timeout + 截斷）、`grep_files`（純 Python grep 語意子集）、`truncate_output`。

**測試**：`tests/test_exec_host.py` **76 tests** 全綠 — 黑名單 13 正例 + 7 反例、bash/awk 探測、shell/bash/awk/python 實跑（Windows 實測 Git Bash 工具鏈）、管線、逾時回報、grep 全旗標（regex/fixed/-i/-v/-n/-c/-l/context/範圍/二進制跳過/截斷）、host 雙沙盒（read root 外拒絕、`$QDRANT_READ_ROOT` 讀取）、黑名單拒絕、manager 註冊、stdio handlers、系統提示詞 7 條鎖定。

`pyproject.toml` 0.9.8.1 → **0.9.9.0**。Workspace version 仍為 v0.8.9 baseline（4-Provider LLM dispatch）。
### v0.9.8.1（功能強化：`ingest_log` 支援壓縮檔直接匯入 — .zip / .7z / .tar / .tar.gz / .tgz / .tar.bz2 / .gz）

Log 異常分析 MCP 的 `ingest_log` 現在可以直接吃壓縮檔：偵測到壓縮副檔名後自動解壓到一次性 temp 目錄（**不污染**使用者沙盒），逐成員解析、逐成員作為獨立 source（id 格式 `<archive>!<member>`，如 `C:/logs/logs.zip!sys/auth.log`）索引進 Qdrant。

**工作流簡化**：拿到 `logs.tar.gz` 不用先手動解壓再逐一 ingest — 一次呼叫直接完成。

**行為細節**：

* 逐成員 `log_type=auto` 多數決偵測 — 一個壓縮檔內可以混 access.log 與 syslog，各自正確解析；`log_types` 欄位回報每種格式的檔案數
* 非 log 成員（隨手打包的 notes.txt 等）**跳過並回報**（`files_skipped[].reason = "no parsable log lines"`），不讓整個 ingest 失敗
* 危險執行檔成員（`.exe/.bat/...`）由 `extract_archive` 的 blocklist 擋下，永不落地也永不索引
* Zip-Slip 防護 + zip-bomb 上限（`QDRANT_ARCHIVE_MAX_BYTES` 512 MB / `QDRANT_ARCHIVE_MAX_FILES` 10000）完整沿用 archive 層防護
* `max_lines` 語意改為**每檔**上限（預設 20000），`truncated` 逐檔回報
* `members` 新參數 — 成員名 allowlist，只解壓並 ingest 指定成員
* 重 ingest 同一壓縮檔 = upsert 覆蓋同 ID，不會重複
* 純文字檔路徑完全不變（`source` 仍為檔案 posix 路徑）

**下游相容**：`analyze_anomalies(source=...)` / `search_logs(source=...)` 直接用 `files[].source_id` 過濾單一成員；`list_log_sources` 會列出所有 `archive!member` 來源。

**同步更新**：host `ingest_log` tool schema（+`members` 參數、description）、stdio server `log_analyzer_server.py` spec、Agent 系統提示詞 flow 說明（壓縮檔直接 ingest 一句）。

**測試**：`tests/test_log_analyzer.py` 95 → **106 tests** 全綠 — zip / tar.gz / gz / 7z 四格式直接匯入、混合格式逐檔偵測、非 log 成員跳過、危險成員不索引、成員 allowlist（含全不合時 error）、重 ingest 不重複、`max_lines` 每檔 cap、沙盒外壓縮檔拒絕、`analyze` 以 member source 過濾、純文字檔回歸。順手清理 3 個先前插入錯位產生的重複測試方法。

`pyproject.toml` 0.9.8.0 → **0.9.8.1**。
### v0.9.8.0（新功能：MCP 檔案壓縮解壓縮擴充 — zip / 7z / tar / tar.gz / tar.bz2 / gzip）

新的 MCP 擴充功能：在 filesystem 沙盒內提供完整的壓縮/解壓縮能力，LLM 可以說「把這些檔案打包成 zip」「解壓這個 7z」直接完成工作。

**工具面（in-process `ArchiveLocalHost` 自動註冊 + stdio server `python -m src.mcp_server.archive_server`）：**

* `archive_create(files, out_path?, format=auto)` — 壓縮檔案與/或目錄（目錄遞迴走訪），格式：zip / 7z / tar / tar.gz / tar.bz2 / gz（gz 僅單檔，多檔會回 error 提示改用 tar.gz）。輸出檔名副檔名自動決定格式（`out_path` 未給則 `archive-<timestamp>.<fmt>`）；危險執行檔副檔名一律跳過並列出
* `archive_extract(path, to_dir?, members?, overwrite=false)` — 解壓至 write sandbox；預設目標目錄 = `<archive-stem>_extracted`；支援成員 allowlist
* `archive_list(path)` — 不解壓列出成員（名稱/大小/壓縮後大小/is_dir/修改時間，上限 500 筆防 context 爆量）
* `list_archive_formats()` — introspection（格式清單、py7zr 可用性、上限、沙盒根）

**安全模型**（`src/archive_tools.py` 統一實作，host 層再驗一次）：

* **Zip-Slip 防護** — 解壓逐成員 `resolve()` 檢查必須落在目標目錄內；`../`、絕對路徑、Windows 磁碟代號（`C:/`）成員全部拒絕並回報（`escapes the destination sandbox (zip-slip)`）
* **Zip-bomb 上限** — 解壓縮總位元組與成員數雙上限（`QDRANT_ARCHIVE_MAX_BYTES` 預設 512 MB、`QDRANT_ARCHIVE_MAX_FILES` 預設 10000），超限成員**跳過並報告**而非拋錯；gz 串流解壓超過上限會中止並刪除部分檔案
* **危險副檔名** — `.exe/.bat/.cmd/.ps1/.dll/.sh/...`（與 filesystem MCP 同一 blocklist）解壓時跳過、壓縮時不打包，防止壓縮檔被用來夾帶可執行檔
* **雙沙盒** — 輸入允許 read root 或 write root（壓縮檔通常剛由 archive_create 寫進 write root），輸出嚴格限 write root

**格式支援**：zip / tar / tar.gz / tar.bz2 / gz 用 stdlib（zipfile / tarfile / gzip）；**7z 用 py7zr 1.1.3**（新依賴，純 Python 免裝 7-Zip 主程式，pyproject dependencies 新增 `py7zr>=1.0`）。py7zr 1.x 已移除 `extract(overwrite=...)` 參數，覆蓋語意由本層 guard 統一控制。

**系統提示詞**：新增「File archive / compression (v0.9.8+)」trigger block — 觸發詞（「壓縮」「打包」「解壓縮」「解壓」「zip 起來」/ 'compress' / 'zip it up' / 'unzip' / 'extract the 7z'）、格式清單、`archive_list` 先看的流程建議、安全沙盒與 zip-bomb 說明。

**新 env vars**（`.env.example` 同步）：`QDRANT_ARCHIVE_MAX_BYTES`（預設 536870912 = 512 MB）、`QDRANT_ARCHIVE_MAX_FILES`（預設 10000）。`config/mcp.json.example` 附 archive stdio 範例。

**新模組** `src/archive_tools.py`：格式嗅探（副檔名 → 格式）、create（zip/tar*/gz/7z）、list_members（四格式統一 shape）、extract_archive（逐成員流式解壓 + 安全防護），單一職責、無 MCP 耦合。

**測試**：`tests/test_archive_host.py` **63 tests** 全綠 — 格式嗅探 10 參數化、6 格式 roundtrip（含 7z 真實 py7zr）、gz 單檔慣例（`a.txt.gz` 解出 `a.txt`）、gz 多檔/目錄拒絕、未知格式拒絕、zip-slip 四種變體（`../`、`..\`、`/abs`、`C:/`）全數阻擋、危險副檔名跳過、大小/數量上限、成員 allowlist、overwrite 語意、host 雙沙盒（read root 外拒絕、write root 外拒絕、write root 內的 archive 可讀）、單字串/逗號清單容錯、manager 註冊、stdio handlers、系統提示詞 7 條鎖定。

`pyproject.toml` 0.9.7.0 → **0.9.8.0**。Workspace version 仍為 v0.8.9 baseline（4-Provider LLM dispatch）。
### v0.9.7.0（新功能：MCP 日誌異常分析擴充 — 用 Qdrant 分析 access log / system log 的異常紀錄）

新的 MCP 擴充功能：把 web-server access log（Apache/Nginx combined）與系統日誌（syslog RFC3164 / RFC5424）逐行向量化存進**專用 Qdrant collection**，並以規則式統計 + 語意孤立點兩種方法偵測異常。

**工具面（in-process `LogAnalysisLocalHost` 自動註冊 + stdio server `python -m src.mcp_server.log_analyzer_server`）：**

* `ingest_log(path, log_type=auto, max_lines=20000, batch_size=128)` — 解析 + embed + upsert，**一行 log = 一個 point**；ID 為 `sha1(source + raw_line)`，重 ingest 同檔案是覆蓋不是重複（upsert 語意）
* `analyze_anomalies(source?, log_type?, top_n=10)` — 兩段式分析：
  * **規則式統計**：HTTP 4xx/5xx 佔比、同 IP 大量 404（目錄掃描）、單一 IP 高頻請求（mean + 3σ 統計離群）、可疑路徑（SQLi / 路徑穿越 / 後台探測 / JNDI）、掃描工具 User-Agent（sqlmap / nikto / nmap / hydra…）、401/403 集中爆破、SSH 登入失敗（per-IP / per-user）、OOM / segfault / kernel panic、log flood
  * **語意孤立點**：從 Qdrant scroll 向量，numpy 分塊計算 pairwise cosine，每行取「與其他行的最大相似度」，低於閾值（預設 0.35）即為語意上的異常行 — 「看起來跟其他行都不一樣」的訊息
* `search_logs(query, source?, log_type?, level?, min_status?, top_k, min_score)` — 語意搜尋（「找出所有 SQL injection 嘗試」）
* `get_log_stats(source?)` — log_type / level / status 分布、top IP / path、時間範圍
* `list_log_sources()` — 已 ingest 檔案與點數
* `remove_logs(source)` — DESTRUCTIVE 逐檔刪除

**安全模型**：log 檔案路徑沙盒 = `QDRANT_LOG_ALLOWED_ROOT`（fallback：`QDRANT_FILESYSTEM_READ_ROOT` → `WATCH_DIR` → `./documents`），`..` traversal 與 root 外 symlink 一律拒絕 — LLM 不能被誘導讀任意機器檔案。

**系統提示詞**：`Agent._system_prompt(tools_enabled=True)` 新增「Log anomaly analysis (v0.9.7+)」trigger block — 觸發詞（「分析 access log」「系統日誌」「異常登入」「被掃描」/ 'analyze the access log' / 'brute force' / 'who is attacking'）、list → ingest → analyze 流程、表格化呈現要求（severity + evidence + 建議動作）、不得直接傾倒 raw JSON、`remove_logs` 需先確認。

**新 env vars**（`.env.example` 同步）：`QDRANT_LOG_COLLECTION`（預設 `log_docs`，與 file_docs 分離）、`QDRANT_LOG_ALLOWED_ROOT`、`LOG_ANOMALY_OUTLIER_THRESHOLD`、`LOG_ANOMALY_MAX_POINTS`。`config/mcp.json.example` 附 loganalyzer stdio 範例（含 `${ENV_VAR}` 傳遞）。

**新模組** `src/log_analyzer/`：`parser.py`（combined access log / RFC3164+RFC5424 syslog、格式多數決偵測、keyword level 分類；單行失敗不炸整檔）、`anomalies.py`（規則引擎 + chunked numpy outlier）、`store.py`（`LogStore`：HNSW + payload index `source/log_type/level/src_ip` keyword、`status` integer、`ts_epoch` float）。

**測試**：`tests/test_log_analyzer.py` **95 tests** 全綠 — parser（combined/common/IPv6/缺欄位/RFC5424 PRI、level 關鍵字、格式偵測、max_lines 截斷）、規則引擎（每條規則正反例、severity 排序）、outlier（隔離向量、top_n cap、長度不符）、`LogStore`（FakeQdrantClient：index 建立、upsert/search roundtrip、scroll with/without vectors、delete）、host（沙盒拒絕、traversal、重 ingest 不重複、source/log_type filter、stats 聚合）、manager 註冊、stdio handlers、系統提示詞 8 條鎖定。

`pyproject.toml` 0.9.6.1 → **0.9.7.0**。Workspace version 仍為 v0.8.9 baseline（4-Provider LLM dispatch）。

### v0.9.6.1(文件更新:draw.io 的兩個服務架構釐清 — `mcp.draw.io/mcp` vs `embed.diagrams.net`,與 MCP 連線失敗的降級/補救)

純文檔更新,無程式變動。起因是使用者回報「`https://mcp.draw.io/mcp` 連不到,drawio 功能還可以用嗎?」— 這暴露了文檔沒講清楚 **draw.io 品牌下其實是兩個完全不同的服務**:

1. **`https://mcp.draw.io/mcp`** — draw.io 官方的**遠端 MCP server**(`config/mcp.json` 的 `"drawio"` server,streamable_http)。只提供 `create_diagram` / `edit_diagram` / `get_diagram` 給 LLM 呼叫,功能是「代工生成/編輯 XML」。**它不是檢視器。**
2. **`https://embed.diagrams.net`** — 本地 Drawio Viewer 視窗(`gui/diagrams_window.py` + `diagrams.js`)的 iframe **實際載入的畫布**。不同服務、不同 domain,與 mcp.draw.io 的存活完全無關。

**連不到 `mcp.draw.io/mcp` 時的行為與影響**:

* `McpManager` 在**啟動時**連線一次(`register_client` → `connect()` + `list_tools()`);失敗記入 `McpManager.failed`,**該 session 內不會自動重試** — 之後整段對話 drawio 工具都缺席,但其餘工具(Qdrant / filesystem / scheduler / todo / web search / openwith…)全部照常。
* 失去:`create_diagram` 系列遠端工具(代工寫 XML)。
* **不受影響**:Drawio Viewer 視窗(embed.diagrams.net)、訊息旁「📊 在圖表中開啟」按鈕(本地 extraction + 本地 viewer)、v0.9.6.0 的 `open_content`(LLM 自己寫 mxGraphModel → 存 sandbox → 開啟,系統 prompt 已教過 XML 格式)與 `write_file` + `open_file` — 這些**都不需要 mcp.draw.io**。
* 補救(擇一):**重啟 GUI**(重建 McpManager)或 **Admin tab MCP fleet editor 按 Save**(觸發 `McpManager.replace_config` 熱重載、立刻重連)。CLI 用 `/tools status`、GUI Settings tab 查 `failed` 清單。

**診斷方式**(實測於 2026-09-02,當時 server 連 GET 都正常回應 `Session not found. Send a POST to initialize first.` — 這是 MCP server 對未初始化 GET 的正常回應,代表服務活著):

```bat
curl.exe -s --max-time 15 https://mcp.draw.io/mcp
curl.exe -s --max-time 15 -o NUL -w "%{http_code}" https://embed.diagrams.net
```

**文檔更新**:

* `config/mcp.json.example` — 新增 2 個範例:`drawio` 遠端 MCP server(streamable_http,含「兩個服務架構 + 失敗降級 + 重連方式」完整註解)與 `openwith` stdio server(v0.9.6.0)。
* `ARCHITECTURE.md` §1.2 historical arc — 補上 v0.8.7 之後缺漏的 v0.9.0 → v0.9.6.1 條目(v0.9.0 add/update_document、v0.9.1 describe_image、v0.9.2 resize_image、v0.9.3/4 FilesystemLocalHost、v0.9.5.x Drawio Viewer 系列、v0.9.6.0 Open-with)。

`pyproject.toml` 0.9.6.0 → **0.9.6.1**(docs-only,無程式/測試變動)。

### v0.9.6.0(MCP 本地核心功能:Open-with 開啟成品 — chat LLM 產出的 drawio XML / markdown / 文件檔,像 Windows 檔案總管一樣用關聯應用程式開啟)

新功能:把「chat 過程產生的資訊」變成可開啟的成品。LLM 在對話中畫的 drawio XML、寫的 markdown、存進 sandbox 的文件檔,現在可以用一個 MCP 工具呼叫,依副檔名送到對應的應用程式 — **Windows 檔案總管「以關聯應用程式開啟」的語意**。

**新增 3 個 MCP 工具**(`OpenWithLocalHost`,`src/mcp_client/host.py`,in-process 自動註冊 + stdio server 兩條路):

| 工具 | 用途 |
|---|---|
| `open_file(path)` | 開啟 write sandbox 內已存在的檔案(先 `write_file` 再 `open_file`) |
| `open_content(name?, content)` | 把 LLM 剛生成的內容(drawio XML / markdown / csv / 純文字)存進 sandbox **並立刻開啟** — 一步到位 |
| `list_openable_types()` | 內省:哪些副檔名會被送去 Drawio Viewer、哪些走 OS 關聯、哪些被拒、sandbox root 在哪 |

**路由規則**(`_route_open`,依序):

1. **危險執行檔一律拒絕** — `.exe` / `.bat` / `.sh` / `.ps1` / ...(沿用 `_FS_DANGEROUS_WRITE_EXTS`);本工具是「用別的應用程式開文件」,絕不能變成 LLM 的啟動器。
2. **`.drawio` / `.dio`(以及內容偵測為 drawio 的 `.xml`)→ 內嵌 Drawio Viewer 視窗** — GUI 模式下 `gui/api.py` 透過 `McpManager.set_diagrams_opener()` 把 `DiagramsWindowManager.open_with_path` 掛進來(仿 v0.8.6.3 scheduler auto-start callback 模式);viewer 開啟失敗時**自動退回 OS 關聯**並在回傳值加 `note`。
3. **其餘一切走 OS 檔案關聯** — Windows `os.startfile`、macOS `open`、Linux `xdg-open`,就是檔案總管雙擊語意。

**安全模型**:路徑全部 sandbox-scoped(`QDRANT_FILESYSTEM_WRITE_ROOT`,與 FilesystemLocalHost 同一個 root);`_fs_resolve_path` 強制 `..` / symlink 不可跳出;`open_content` 4 MB 上限(同 filesystem read cap)+ atomic write(`tempfile.mkstemp` + `os.replace`,與 diagrams viewer `save_to_sandbox` 同形狀);`_fs_safe_basename` 把 traversal 名稱淨化成 sandbox 內的 leaf。

**chat 體驗**:LLM 說「幫我開啟」「打開來看」「存檔後開給我看」→ prompt 的新 trigger block(v0.9.6.0+,位於 drawio block 之後)教它呼叫 `open_content(name="circle.drawio", content=...)` 而不是只把 XML 貼在訊息裡;GUI 模式下畫好的圖會直接彈出 Drawio Viewer 視窗,markdown 會用系統預設編輯器開啟。

**檔案清單**:

* `src/mcp_client/host.py` — 新 `OpenWithLocalHost`(3 工具 + `_route_open` 路由 + drawio 內容嗅探 `mxfile/mxGraphModel/mxCell` + OS 關聯開啟 wrapper)
* `src/mcp_client/manager.py` — `from_config` 註冊(保留 host 參考 `_openwith_local_host`)+ `diagrams_opener_callback` 屬性 + `set_diagrams_opener()` canonical setter(與 `set_auto_start_callback` 對稱;host 尚未註冊時也不丟例外)
* `src/agent/agent.py` — `_system_prompt(tools_enabled=True)` 新增「Open-with / artifact opener (v0.9.6.0+)」trigger block(工具名、中英觸發詞、flow、安全拒絕)
* `src/mcp_server/openwith_server.py` — stdio MCP server(`python -m src.mcp_server.openwith_server`,供 Claude Desktop 等外部 client;thin wrapper 直接重用 `OpenWithLocalHost` 的 handler)
* `gui/api.py` — `_wire_openwith_diagrams_opener(agent)`(冪等,每次 chat_turn/ask 都呼叫)+ `_open_drawio_via_viewer` callback → `DiagramsWindowManager.open_with_path`;CLI / stdio 無 callback 時自動退回 OS 關聯

**測試**:`tests/test_open_with_host.py` 34 個(路由、危險副檔名拒絕、sandbox 逃逸防護、drawio 嗅探、callback 三種失敗路徑的 fallback、`open_content` 寫入+開啟+預設命名+覆寫+大小上限、manager wiring、stdio server handler)+ `tests/test_system_prompt_openwith.py` 9 個(trigger block 存在性、關鍵字、legacy 排除、與 language/drawio/telegram/schedule block 共存)。`os.startfile` 全程 monkeypatch,測試不會真的開啟任何視窗。

**結果**:4 檔案(`test_open_with_host` + `test_system_prompt_openwith` + `test_mcp_client` + `test_meta_local_host`)**82 passed, 0 failed**。`pyproject.toml` 0.9.5.8 → **0.9.6.0**。Workspace version 仍為 v0.8.9 baseline(4-Provider LLM dispatch)。

### v0.9.5.8(移除主視窗 chat-toolbar 的「📊 圖表檢視器」按鈕 — 開空白 viewer、無任何用處)

使用者要求移除 chat 工具列上的「📊 圖表檢視器 / Diagrams Viewer」按鈕 — 它只會開一個**空的** viewer window,沒有任何途徑接收聊天訊息裡的 XML,對使用者毫無用處。訊息旁的「📊 在圖表中開啟 / View in Diagrams」按鈕(`renderChat()` 逐訊息生成、經 `open_diagrams_with_xml` 帶 XML 開啟)**保留** — 那才是真正在用的入口。

**移除內容**:

1. `gui/assets/index.html` — 刪除 `chat-diagrams-btn` 按鈕元素(`data-i18n="diagramsViewer"`)。
2. `gui/assets/app.js::bindEvents()` — 刪除 click handler(原本呼叫 `open_diagrams_window`);留下 v0.9.5.8 註解說明移除原因。
3. `gui/assets/app.js` 4 個 locale — 刪除已無使用者的 i18n key `diagramsViewer`(zh-TW / zh-CN / en / ja);`openInDiagrams` 保留(訊息旁按鈕仍在用)。
4. `gui/api.py::diagrams_window_status` docstring 更新 — 原本宣稱「Used by the toolbar button label」已過期;`open_diagrams_window` / `diagrams_window_status` **API 方法保留**(公共 API 表面不變,外部 introspection 仍可用,測試照舊通過)。

**測試**(`tests/test_diagrams_window.py::TestGuiAssetsWiring`):

* `test_index_html_has_diagrams_button` → 改名 `test_index_html_toolbar_diagrams_button_removed`(反向 guard:`chat-diagrams-btn` / `diagramsViewer` 不得存在於 index.html)。
* `test_app_js_handles_diagrams_button` → 改名 `test_app_js_toolbar_handler_removed`(反向 guard:`chat-diagrams-btn` / `open_diagrams_window` / `diagramsViewer` 不得存在於 app.js;`_extractDiagramXml` / `mxfile` 機制必須保留)。
* `test_app_js_renders_view_button_per_message` 原樣保留 — 鎖定訊息旁按鈕的 wiring(`open_diagrams_with_xml` / `btn-msg-diagrams`)不被誤刪。

**結果**:`test_diagrams_window.py` **102 passed**,0 failed(102 → 102,2 個測試改為移除 guard,0 回歸)。`pyproject.toml` 0.9.5.7 → **0.9.5.8**。Workspace version 仍為 v0.8.9 baseline — 純 GUI 介面精簡,agent / LLM dispatch / vector store / scheduler / sessions 無變動。

### v0.9.5.7(hotfix:Drawio Viewer 跳「错误 非绘图文件」— `_wrapForDrawio` 用了不存在的 `<mxDiagram>` 元素)

使用者回報 2026-09-02:同一輪對話裡 LLM 先回了 bare `<mxGraphModel>`(無屬性、無 mxfile wrapper)— 點「📊 在圖表中開啟」後 draw.io 跳「错误 非绘图文件」對話框並把內容預覽出來;LLM 重試改用完整 `<mxfile><diagram>` 包裝後**同一份圖形就正常渲染**。兩份 XML 的圖形內容一模一樣,差別只在 envelope。

**Root cause**:`gui/assets/diagrams.js::_wrapForDrawio` 把 bare `<mxGraphModel>` 包進 mxfile 時,子元素寫成了 **`<mxDiagram>`** — mxfile 格式裡根本沒有這個元素(正確名稱是 `<diagram>`;`mxDiagram` 是 mxGraph JS class 的名字,不是 XML 元素名)。drawio 解析 mxfile 時用 `getElementsByTagName('diagram')` 找頁面 → 一個都找不到 → 整份檔案被判成「非绘图文件」。同型 typo 共 **3 處**:

1. `_wrapForDrawio` 的 mxGraphModel 分支(`<mxDiagram>` + `</mxDiagram>`)
2. `_wrapForDrawio` 的 bare `<mxCell>` 分支(同 typo)
3. `emptyDiagramXml()`(merge action 的 fallback template)用的是小寫 **`<mxdiagram>`** — 一樣不對

更糟的是既有測試把 bug 鎖死了:`TestJsWrapForDrawio` 有 2 個斷言和 protocol driver 測試 1 個斷言都是 `assert "<mxDiagram" in ...` — substring 檢查只驗「有包起來」,從沒驗過元素名稱是否合法,bug 就這樣帶著測試活過了 v0.9.5.2~v0.9.5.6(前幾版 user 看到的是 blank pane / boot crash,還輪不到 drawio 檢查 envelope)。

**修法**(`gui/assets/diagrams.js`):

1. 三處 envelope 的子元素全部改成規範的 `<diagram id="root" name="Page-1">`,與使用者手打的 `<mxfile><diagram>` 形狀一致(該形狀已被證實可渲染)。
2. 附帶效應:bare `<mxGraphModel>`(create_diagram MCP 的回傳形狀)現在會渲染成正常圖形,不再需要請 LLM 重打成完整 mxfile。

**測試**(`tests/test_diagrams_window.py`):

* 3 個鎖死 bug 的斷言反轉:`"<diagram" in` + `assert "mxDiagram" not in` + `assert "mxdiagram" not in`(雙 casing 都防)。
* `test_wrap_bare_mxgraphmodel_canonical_element` — 使用者 exact 圓形狀(160×160 ellipse、fillColor=#dae8fc)wrap 後 envelope 必須是 `<mxfile host="qdrant-rag-agent"...><diagram id="root"...>`,圖形 cell 原樣保留。
* `test_extract_then_wrap_user_message` — 全鏈回歸:整段聊天訊息(散文 + bare mxGraphModel + 尾部散文)走 `_extractDiagramXml` → `_wrapForDrawio`,斷言抽出的 xml 不帶散文、wrap 後 envelope 規範、前後散文都不會漏進圖形檔。
* `test_no_wrong_diagram_element_anywhere_in_source` — 全檔案 guard:`diagrams.js` 內任何地方都不允許 `mxDiagram` / `mxdiagram` 出現(含 `emptyDiagramXml` template)。

**結果**:`test_diagrams_window.py` **102 passed**(99 → +3),0 failed。`pyproject.toml` 0.9.5.6 → **0.9.5.7**。Workspace version 仍為 v0.8.9 baseline — 純 viewer JS envelope 修正 + 測試,agent / LLM dispatch / vector store / scheduler / sessions 無變動。

### v0.9.5.6(hotfix:啟動即「❌ JS error: Cannot read properties of undefined (reading 'api')」— pywebview bridge 注入時序)

使用者回報 2026-09-02:一啟動 Drawio Viewer,右上角 pill 立刻顯示「❌ JS error: Cannot read properties of undefined (reading 'api')」,且 editor 區全空。(v0.9.5.5 新加的全域 error handler 正常運作 — 它抓到的是一個**真實的 bug**。)

**Root cause**:v0.9.5.5 的 boot block 在 `DOMContentLoaded` handler 內**未加防護**直接讀 `window.pywebview.api.get_write_root()`。但 pywebview 是在**頁面載入完成後**才注入 `window.pywebview`(官方 `pywebviewready` event)— `DOMContentLoaded` 先觸發,當下 `window.pywebview` 是 `undefined` → 讀 `.api` 丟 TypeError。更糟的是這個同步 throw 會**中斷 handler 後續所有程式**:`startDrawio()` 根本沒跑,iframe 永遠不會 mount。舊的 JSDOM 測試抓不到,是因為測試 driver 在 `window.eval(diagrams.js)` **之前**就預先定義好 bridge stub,跟真實 WebView2 的生命週期相反。

**修法**(`gui/assets/diagrams.js`):

1. **`bridgeApi()` null-safe helper** — 所有 bridge 讀取都走 `(window.pywebview && window.pywebview.api) || null`,對齐主 GUI `app.js::api.call()` 自 v0.8.7 起的同一模式(參見 `test_gui_silent_api_not_ready.py`)。
2. **`_waitForBridge()`** — 每 50ms 輪詢直到 bridge 出現(預設上限 15s;測試可用 `window._BRIDGE_WAIT_MS_OVERRIDE` 縮短),同時監聽官方 `pywebviewready` event 即時 resolve。Boot 的 `get_write_root()` 改走此 waiter;bridge 永遠不來時 pill 顯示非致命的「⚠️ Python bridge 未就緒」,viewer 仍以 fallback URL 唯讀運作。
3. **`startDrawio()` 無條件先跑** — iframe mount 不再被 bridge 狀態綁架(`get_drawio_url` 探測本來就有 guard + fallback URL);`probeBridge()` 移進 `_waitForBridge().then()` chain 並加 guard,不再在 boot 對未注入的 bridge 開槍。順帶修正 v0.9.5.4 的錯誤註解(「DOMContentLoaded 時 bridge 已就緒」— 實際相反)。

**測試基建(driver `bridge_mode` 參數化)**:`_DRIVER_PREFIX` 重構為 `_driver_head_js(bridge_mode="stub"|"none"|"late", bridge_delay_ms, bridge_timeout_override_ms)` + 共用 `_run_node_jsdom_script()` / `_require_jsdom_or_skip()`;overlay snapshot 新增 `sandbox_path_text`。`stub`(預設)保留舊行為,既有測試零改動。

**結果**:`test_diagrams_window.py` **99 passed**(94 → +5,`TestJsBridgeBootSafety`:3 source-level + 2 jsdom),0 failed。`pyproject.toml` 0.9.5.5 → **0.9.5.6**。Workspace version 仍為 v0.8.9 baseline — 純 JS boot + 測試基建修改,agent / LLM dispatch / vector store / scheduler / sessions 無變動。

### v0.9.5.5（修 viewer 卡在「⏳ connecting…」誤導 pill + JS 掛掉全白無訊息 — HTML 防禦性 fallback + JS watchdog + 測試基建確定化）

v0.9.5.4 的 loading overlay 修好了「看得到進度」的問題,但上線測試又回報兩個新症狀:(1) viewer 一打開 status pill 停在誤導性的「⏳ connecting…」預設文字,看不出 JS 是否真的在跑;(2) 如果 `diagrams.js` 本身載入失敗或執行到一半 throw,使用者看到的是永遠的預設 pill + 全白畫面,**完全沒有任何錯誤訊息** — viewer window 沒有 devtools,錯誤無處可去。

**Root cause**:三層問題疊加 — (1) `diagrams.html` 靜態 HTML 的 overlay 預設 hidden、pill 預設文字是「⏳ connecting…」這種看起來像在等連線的文案,但實際上 JS 根本還沒啟動,文字是謊言;(2) `diagrams.js` 沒有任何全域 `error` / `unhandledrejection` handler,JS 內任何 exception 都 silently swallowed,pill 停在預設值不動;(3) 沒有 watchdog — drawio 的 init event 若永遠不來（網路壞掉、CDN 掛掉）,pill 永遠停在「iframe mounted」狀態,使用者不知道該按逃生按鈕。

**修法（三層防禦）**:

1. **HTML 防禦性 fallback（不需要 JS 就有意義）**:
   - `<div id="loading-overlay">` **移除 `hidden` attribute,預設可見** — 就算 diagrams.js 一行都沒執行,使用者看到的也是「⏳ 載入 drawio 編輯器…」+ spinner + 逃生連結,不是全白
   - status pill 預設文字從「⏳ connecting…」改成**中性的「📊 Drawio Viewer」** — 不再假裝正在連線
   - **anchor fallback 移到 stuck-actions hidden div 外面** — `<a id="link-open-in-browser" target="_blank">` 用純 HTML 就能在系統瀏覽器開 drawio,不依賴任何 JS
   - `<noscript>` 區塊 — JS 全禁用的環境也有可操作的訊息
2. **JS 全域錯誤 watchdog（v0.9.5.5）**:
   - `window.addEventListener("error")` + `window.addEventListener("unhandledrejection")` 在 IIFE 最頂端註冊 — 連這個檔案自己的語法錯誤都能上 pill
   - `_reportError(prefix, e)` helper:`setStatus("❌ " + prefix + ": " + msg, "err")`,任何 exception 都變成 pill 上看得見的錯誤文字
   - **2 秒 `_WATCHDOG_MS` watchdog**:`startDrawio()` 結尾 `setTimeout`,init event 若在時限內沒到,pill 顯示「⏳ drawio 回應緩慢 (等候 init 中…)」;init 到了就 `clearTimeout`（避免 init 之後 watchdog 又把 ready 狀態蓋掉）;「重新載入 iframe」重 mount 時 watchdog 重置
3. **測試基建確定化（殺掉 cross-file flakiness）**:
   - **JSDOM driver 移除 `resources: 'usable'`** — 這個選項讓 JSDOM 真的透過網路抓 `https://embed.diagrams.net`,fetch 完成的瞬間 iframe `load` handler 觸發,把合成 rejection 剛寫好的 pill 文字蓋掉;`test_unhandled_rejection_populates_status_pill` 在 cross-file run 時時好時壞就是這個根因。jsdom 29 也已移除 `ResourceLoader` subclass API,無法 stub fetch。改為完全不載入資源:iframe 不導航、`load` 不觸發,pill 狀態只由測試自己 dispatch 的合成事件驅動 — 完全確定
   - subprocess timeout 加大:jsdom probe 10→30 秒、drive 20→45 秒（冷載 `require('jsdom')` + AV 掃描在負載高的機器上曾撞到 10 秒上限）
   - 清掉 `TestJsDefensiveFallback` 內 3 份重複的死碼測試方法（anchor + noscript 各三份,其中一份引用未定義的 `divs` 變數 — Python 後定義覆蓋前定義所以從未執行,但是地雷）

**測試（8 新,`TestJsDefensiveFallback`）**:

* `test_loading_overlay_html_default_visible` — overlay div 不得有 `hidden` attribute
* `test_status_pill_html_default_is_neutral` — 靜態 HTML 不得含「connecting...」,必須含「Drawio Viewer」
* `test_status_pill_initial_setstatus_runs` — JS boot 後 pill 必須離開預設值（第一個 setStatus 有跑）
* `test_watchdog_pill_text_after_no_init` — init 沒來時 2 秒 watchdog 必須把 pill 換成「回應緩慢」
* `test_window_error_event_populates_status_pill` — 合成 `error` event 必須把「JS error: <msg>」放上 pill（kind=err）
* `test_unhandled_rejection_populates_status_pill` — 合成 `unhandledrejection` 必須把「Promise rejected: <reason>」放上 pill
* `test_anchor_fallback_visible_without_js` — anchor 必須在 stuck-actions hidden div 外（最近的 wrapping div 不得有 hidden）
* `test_noscript_fallback_present` — `<noscript>` 必須存在

**Test 結果**:6 個 file **189 passed**, 0 failed（v0.9.5.4 baseline 181 → **+8 new + 0 regression**）:
* `test_diagrams_window.py` **94 passed**（v0.9.5.4 baseline 86 → **+8 new**）
* `test_system_prompt_diagrams.py` 8 passed
* `test_system_prompt_telegram.py` 8 passed
* `test_md_render.py` 27 passed
* `test_chat_autosave.py` 18 passed
* `test_local_tool_argument_coercion.py` 34 passed

**File changes**:
* `gui/assets/diagrams.html` — overlay 移除 `hidden` 改預設可見;pill 預設文字中性化;anchor fallback 移出 hidden div;`<noscript>` 區塊
* `gui/assets/diagrams.js` — `_reportError()` + 全域 `error` / `unhandledrejection` handlers（IIFE 頂端）;`_WATCHDOG_MS = 2000` watchdog + init / reload 時重置;`unhandledrejection` handler 對合成 Event 的 `ev.reason` 缺失 fallback 到 `window._lastRejectionReason`（測試驅動器 stash）
* `tests/test_diagrams_window.py` — 新 `TestJsDefensiveFallback`（8 tests）;`_DRIVER_PREFIX` 移除 `resources: 'usable'`（確定化）;probe / drive timeout 10→30 / 20→45;`_OVERLAY_DRIVER_SUFFIX_TEMPLATE` 快照加 `status_pill_text` / `status_pill_kind`;新增 `dispatch_error` / `dispatch_rejection` ops;清掉 4 份重複死碼方法
* `pyproject.toml` 0.9.5.4 → **0.9.5.5**

Workspace version 仍 v0.8.9 baseline（純 GUI + JS + 測試基建改動,沒動 agent / LLM dispatch / vector store）。

### v0.9.5.3（修圖表檢視器打開是 empty view — 改用裸 iframe + drawio JSON protocol，廢掉 DrawioViewer wrapper）

v0.9.5.2 修了「按鈕沒出現」這個層面的問題，但 user 上線試了發現新症狀:這次「View in Diagrams」按鈕**有出現**、點下去 viewer window **有打開**，可是 drawio 編輯區是空白 — **完全沒載入圖表**。UI log 顯示 `open_diagrams_with_xml: xml_len=369`(XML 確實送到了),但 iframe 沒渲染。

**Root cause**:`gui/assets/diagrams.js` 用的是 drawio `embed/v1.js` 提供的 `DrawioViewer` 高階 wrapper，內部物件結構不公開。`postToDrawio` 試 `iframe.iframe.contentWindow.postMessage(...)` — `iframe.iframe` 屬性名只是猜的,真實 wrapper 用的是別的 attribute(或完全不同結構),結果 postMessage 呼叫被 silently dropped,viewer 永遠收不到 XML,iframe 一直停在空白狀態。

`onDrawioMessage` 等的 `init` / `save` event 當然也永遠不到,UI 也沒任何錯誤訊息,使用者只看到空白編輯器。

**修法（直接廢掉 wrapper）**:照官方 `jgraph/drawio-integration` `diagram-editor.js` 的同款 pattern,**直接 mount 裸 `<iframe>` + 走 embed=1 + proto=json 文檔協議**:

```javascript
iframe = document.createElement("iframe");
iframe.setAttribute("src",
  "https://embed.diagrams.net/?embed=1&proto=json&spin=1&ui=min");
iframe.style.cssText = "border:0;width:100%;height:100%;display:block;";
mount.appendChild(iframe);

// 等 iframe 送 init event 再 load;之前會 silently drop
window.addEventListener("message", onDrawioMessage);
```

`onDrawioMessage` 加 `case 'init'` 把 `drawioInitialized = true`,然後把 `pendingLoadXml` queue 一次 flush;`loadXmlIntoEditor` 改成 `postToDrawio({action: 'load', xml: ...})`(JSON protocol 用 `action`,不是 legacy 的 `event: 'load'`);`postToDrawio` 直接 `iframe.contentWindow.postMessage(...)`。

**額外修兩個 bug** (順手):

* **`_wrapForDrawio` 不接受 self-closing `<mxCell .../>`** — 原本 trigger regex 要求成對 `</mxCell>`,所以 LLM 給的裸 `<mxCell id='5'/>` 沒被包成 mxfile envelope,drawio embed 直接看到 raw leaf cell 就壞掉。改寬成 `/<\s*mxCell\b/i` — 任何含 `<mxCell` 的字串都視為 bare cell 處理。
* **Legacy `MutationObserver` queue 機制刪掉** — 等 init event 已經能準確處理 race,不需要 hack 觀察 status pill 文字。`drawio-load-xml` CustomEvent listener 也只剩一行。

**測試（4 新 + 1 wrapper regex 放寬）**:

* `tests/test_diagrams_window.py::TestJsDrawioEmbedProtocol`（新 class,4 tests）— 用 JSDOM + 假 iframe stub 跑完整 viewer JS,模擬：
  - `init_event` 從我們 mount 的 iframe.contentWindow 送 `{event:'init'}`
  - `load_xml_event` dispatch CustomEvent 把 XML 推進去
  - 攔截 viewer postMessage,驗證送進 iframe 的 JSON 內容
  - **4 個 case**：iframe URL 必須含 `embed=1,proto=json,spin=1`;init 之前的 load 要 queue;init 之後的 load 立即送;init 沒來時 load 永遠不會 leak 出 postMessage
* 1 個既有 `test_bare_mxcell_wrapped` 自動覆蓋新的 self-closing case
* JSDOM probe 用 tmp file + `cwd=project_root`（Windows cmd 對 `node -e "..."` 的 quoting 會吃 JSON escape,改成寫 tmp file 就穩了;同時確保 `require('jsdom')` 從 project root 的 `node_modules/` 解得到）

**Test 結果**:6 個 file 174 passed, 0 failed（v0.9.5.2 baseline 151 passed, 1 skipped）:
* `test_diagrams_window.py` 79 passed（v0.9.5.2 baseline 75 → **+4 new**）
* `test_system_prompt_diagrams.py` 8 passed
* `test_system_prompt_telegram.py` 8 passed
* `test_md_render.py` 27 passed
* `test_chat_autosave.py` 18 passed
* `test_local_tool_argument_coercion.py` 34 passed

**File changes**:

* `gui/assets/diagrams.html` — 移除 `<script src="...embed/v1.js">`(不再需要 wrapper)
* `gui/assets/diagrams.js` — `startDrawio` 直接 mount iframe;新增 `case 'init'` handler;`drawioInitialized` flag + `pendingLoadXml` queue 取代舊的 `drawioReady` + `queuedXml` + MutationObserver;`_wrapForDrawio` 放寬 trigger regex;legacy `event: 'load'` 改 JSON protocol `action: 'load'`;訊息過濾 `evt.source !== iframe.contentWindow` 拒絕外部 message
* `tests/test_diagrams_window.py` — 新 `TestJsDrawioEmbedProtocol` class + JSDOM driver fixture
* `pyproject.toml` 0.9.5.2 → **0.9.5.3**

Workspace version 仍 v0.8.9 baseline（純 GUI + JS 改動,沒動 agent / LLM dispatch / vector store）。

### v0.9.5.4（修圖表檢視器開了但內容全白看不到任何進度 — 加 loading overlay + 系統瀏覽器 escape hatch）

v0.9.5.3 解決了「viewer 收不到 XML」的通訊問題，但 user 2026-09-02 上線測試又回報新症狀:點「📊 在圖表中開啟」後 viewer window **有打開**(OS 視窗、toolbar、按鈕都正常),**但中間編輯區完全空白**。等 30 秒到 5 分鐘都沒看到 drawio 編輯器出現,以為 viewer 又壞了。

**Root cause**:drawio 的 embed 不是「小工具」,而是把**整個 ~9.6 MB 的 minified editor bundle**（`https://embed.diagrams.net/js/app.min.js` 實測 9,616,453 bytes）透過 `<iframe>` 載入。WebView2(pywebview 在 Windows 的 Chromium-based backend)預設是單 process、沒 GPU 加速、沒 warm cache,**載 + parse + 編譯 + 初始化這 9.6 MB JS 通常要 30 秒到幾分鐘**。drawio embed 自己有個 `spin=1` 載入動畫,但畫面是「白色背景 + 小 spinner」,對使用者來說就是「一片空白」,沒任何可讀的進度訊息。

更糟的是 drawio embed 必須等到 `{event: 'init'` 通知才接受 `{action: 'load'}` XML,在 init 之前 viewer JS 的 `pendingLoadXml` queue 一直 hold 著,使用者看不到任何「正在等你」的反饋。

**修法（兩件事:可見的進度 + 失敗時的逃生口）**:

1. **`<div id="loading-overlay">` 半透明覆蓋層**,從 iframe mount 那一刻就顯示在 viewer 上面,帶:
   - CSS spinner（純 CSS keyframes,沒外部依賴）
   - 「⏳ 載入 drawio 編輯器…」標題 + 解釋訊息（中文）
   - **live elapsed timer**(`setInterval` 每 500 ms 更新一次「已等候 N 秒…」）
   - 15 秒後切換成「⚠️ drawio 載入緩慢 (已等候 15 秒)」,並把 stuck-actions 顯示出來
2. **兩個 escape-hatch 按鈕**（stuck 時才出現）:
   - **🌐 在瀏覽器中開啟 drawio** — 呼叫 Python `_DiagramsViewerApi.open_drawio_in_browser()`,走 stdlib `webbrowser.open` 把同一個 embed URL 丟到系統預設瀏覽器（Edge / Chrome / Firefox 都行,通常 2-5 秒就完成）
   - **🔄 重新載入 iframe** — tear down 現有 iframe,再 `startDrawio()` 重來一次（drawio 有時 reload 會命中不同 CDN cache 節點,變快）

**單一來源真相 (single source of truth)**:`_DRAWIO_EMBED_URL` Python 常數是 URL 的唯一出處,`_DiagramsViewerApi.get_drawio_url()` 把 URL 餵給 JS,`diagrams.js` 在 `startDrawio()` 用這個值（fallback 寫死 URL 只在 Python bridge 還沒 ready 的極端情況）。未來 drawio 換 query string 只改 `_DRAWIO_EMBED_URL` 一處,JS / Python / 測試 fixture 全部自動跟進。

**Test infra fix（順手）**:這次把 JSDOM test driver 從 `process.stdout.write` 換成 `console.log` + 顯式 `setTimeout(() => process.exit(0), 50)`,因為 Node 24 + JSDOM 29 的組合下舊版 driver 在 Node teardown 時會 `STATUS_STACK_OVERFLOW` (`0xC00000FD`) crash,即使 `console.log` 已經 flush 完 stdout。`_jsdom_run_drive` 把這個 status code 視為 benign 收下。其他 exit code 仍然 raise。順手把 `_DRIVER_PREFIX` 的 pywebview bridge stub 加上 `get_drawio_url` / `open_drawio_in_browser` 兩個方法,讓 JSDOM 跑 viewer JS 時不會撞到「bridge method undefined」。

**測試（11 新 + 6 driver fix regression）**:

* `tests/test_diagrams_window.py::TestApiBinding::test_get_drawio_url_returns_constant` — `_DiagramsViewerApi.get_drawio_url()` 回傳 `_DRAWIO_EMBED_URL` 常數
* `test_open_drawio_in_browser_invokes_webbrowser` — 用 monkeypatch 攔截 `webbrowser.open`,確認按鈕真的呼叫到 stdlib
* `test_open_drawio_in_browser_exception_returns_error` — `webbrowser.open` raise 時回 `{"error": "..."}` 而不是把 exception 傳到 pywebview bridge 弄丟
* `TestJsLoadingOverlay::test_overlay_visible_on_mount` — viewer 一打開,overlay 就是 visible,stuck-actions 還是 hidden,timer 文字是「已等候 N 秒…」
* `test_overlay_hidden_after_init` — drawio 送 `{event:'init'}` 後 overlay 自動消失,不會擋到 iframe 點擊
* `test_open_in_browser_click_doesnt_hide_overlay` — 按「在瀏覽器中開啟」按鈕 overlay 還在（iframe 還在 boot）
* `test_reload_button_tears_down_iframe` — 按「重新載入」按鈕 iframe 真的重 mount,overlay 又回來
* 7 個 protocol test driver 也加 `await new Promise(r => setTimeout(r, 50))` tick,讓 DOMContentLoaded 的同步 handler 真的把 iframe mount 完才進 `run()`(否則第一個 init_event 會 dispatch 在 iframe 還沒準備好之前)

**Test 結果**:6 個 file **181 passed**, 0 failed(v0.9.5.3 baseline 174 → **+7 new + 0 regression**):
* `test_diagrams_window.py` **86 passed**(v0.9.5.3 baseline 79 → **+7 new**,含 3 個新 ApiBinding + 4 個新 TestJsLoadingOverlay)
* `test_system_prompt_diagrams.py` 8 passed
* `test_system_prompt_telegram.py` 8 passed
* `test_md_render.py` 27 passed
* `test_chat_autosave.py` 18 passed
* `test_local_tool_argument_coercion.py` 34 passed

**File changes**:
* `gui/assets/diagrams.html` — 新增 `<div id="loading-overlay">` 含 spinner + timer + stuck-actions 按鈕群
* `gui/assets/diagrams.css` — 新增 `.loading-overlay`、`.loading-card`、`.loading-spinner` + `@keyframes loading-spin`
* `gui/assets/diagrams.js` — 新增 `loadingOverlay` / `loadingTimer` / `loadingStuckActions` / `btnOpenInBrowser` / `btnReloadIframe` refs;新 `showLoadingOverlay()` / `_updateLoadingOverlay()` / `hideLoadingOverlay()` helpers;`startDrawio` 開頭呼叫 `showLoadingOverlay()`,結尾透過 `get_drawio_url()` 拿 URL;`onDrawioMessage` 的 `init` case 多呼一次 `hideLoadingOverlay()`;`DOMContentLoaded` handler 加上 `btnOpenInBrowser` / `btnReloadIframe` click handlers,後者 tear down + remount iframe
* `gui/diagrams_window.py` — 新 `_DRAWIO_EMBED_URL` 模組常數(URL 單一來源);`_DiagramsViewerApi` 多 `get_drawio_url()` + `open_drawio_in_browser()` 兩個方法
* `tests/test_diagrams_window.py` — 新 `TestJsLoadingOverlay` class(4 tests);`TestApiBinding` 多 3 個測試;`_js_protocol_driver` + `_js_overlay_driver` 改成 `console.log` + `process.exit(0)` + accept `STATUS_STACK_OVERFLOW` exit code;`_DRIVER_PREFIX` 加 `get_drawio_url` / `open_drawio_in_browser` stub

Workspace version 仍 v0.8.9 baseline（純 GUI + JS 改動,沒動 agent / LLM dispatch / vector store）。


### v0.9.5.2（修圖表檢視器只純文字描述就以為完成 — 強制 echo XML + JSON escape 解碼 + fenced JSON 不誤抓）

v0.9.5.1 上線後使用者回報新的變形:這次 LLM 確實呼叫了
`create_diagram` MCP tool(server log: `tool create_diagram ->
OK: '{"xml": "<mxGraphModel>..."}'`)但 final assistant
message 裡**完全沒 echo XML**,改成純文字描述圖表樣式
("已完成！✅ 我用 draw.io 繪製了一個 hello 方塊:形狀:圓角
矩形(240 × 120)文字:hello(28px 粗體)配色:淡藍色背景
(#dae8fc)+ 藍色邊框(#6c8ebf)圖表已在上方顯示出來…")。

Chat UI 的 `_extractDiagramXml` 在這種 zero-XML message 上
必然 return null — 「View in Diagrams」按鈕不會出現,
使用者只看到純文字描述。

第二個 case:有些 LLM 在 echo tool result 時會把 `<` `>` escape
成 `\u003c` `\u003e`(JSON unicode escape),regex 完全看不到
角括號所以 match 不到。

第三個 case:fenced ```json {"xml":"<mxGraphModel>..."} ``` 
的 message 在 v0.9.5.1 會被 fenced-mxfile pattern 抓到,
但 `m[1]` 包含了 `json\n{"xml": "..."` 整個 — viewer 收到
的不是純 XML 而是 `{"xml": "<mxGraphModel>..."}`,drawio
embed 解析會壞掉。

**Root cause**

1. LLM 不知道「tool result 只有 LLM 自己看得到,user 看的是
   final message」 — 這是 **agent prompt** 層次的盲點,
   不是 regex 問題
2. `_extractDiagramXml` 沒處理 JSON unicode escape
3. Fenced-XML pattern 不分語言標籤,連 ```json fence 都抓

**修法(3 個 layer)**

A. **Agent system prompt 新增 Drawio trigger block** —
   `Agent._system_prompt(tools_enabled=True)` 在 plan/todo
   block 後面、Telegram 之前的位置插入:

   ```
   - Drawio / diagrams viewer (v0.9.5+):
     * trigger phrases: 'drawio', '繪制', '方塊', '流程圖',
       '圖表檢視器' (Chinese) + 'draw.io', 'flowchart',
       'diagram', 'open in diagrams viewer' (English)
     * After calling ``create_diagram`` your FINAL message
       MUST include the returned XML verbatim — either
       as a JSON envelope ``{"xml": "<mxGraphModel>..."}``
       or as a bare XML block ``<mxGraphModel>...</mxGraphModel>``
     * Do NOT just describe the diagram in prose
       ('形狀:圓角矩形, 文字:hello, 配色:藍色') and skip
       the XML — that produces no viewer button
     * If the drawio tool fails, surface the error to the
       user instead of silently describing what you would
       have drawn
   ```

B. **`_extractDiagramXml` pre-normalise JSON unicode escape** —
   regex 之前先 `replace(/\\u003c/gi, "<")`、
   `replace(/\\u003e/gi, ">")`、`replace(/\\u0026/gi, "&")`,
   然後 regex 跑 normalize 過的版本。LLM echo tool result
   的常見 escape 變體全 cover。

C. **Fenced pattern 強制語言標籤** — 從
   ` ```(?:xml|drawio)? ``` ` 改成 ` ```(?:xml|drawio) ``` `。
   ```json fence 不再誤觸發 fenced-XML pattern;落到 JSON
   envelope pattern 才正確 unwrap 純 XML 部分。

**測試** — 11 個新 test:

* 8 個 `tests/test_system_prompt_diagrams.py`(全新檔案,
  跟 `test_system_prompt_telegram.py` 同 pattern):
  - 中文 + 英文 trigger keyword 必出現
  - 強制 XML shape 說明必出現
  - 禁止「純文字描述」這條警語必出現
  - `tools_enabled=False` legacy 模式不應包含 drawio block
  - 跟 response_language 指令共存
  - 不會撞掉 telegram / schedule 既有 block
  - 工具失敗時的處理指引必出現
* 3 個 `tests/test_diagrams_window.py::TestJsExtractDiagramXml`
  v0.9.5.2 case:
  - `test_pure_prose_chinese_no_xml` — **user 報告的 exact
    純文字 scenario**,verify regex 不會產生 false positive
  - `test_json_unicode_escaped_xml` — `\u003c...\u003e` 
    escape 在 pre-normalise 後能 match
  - `test_fenced_json_does_not_match_fenced_xml` —
    ` ```json\n{"xml": "<mxGraphModel>..."}\n``` ` 
    必須被 unwrap 成純 XML,**不**帶 fence / JSON envelope

**Test 結果**:151 passed, 1 skipped(本 release 涵蓋的 6 個
test file: `test_diagrams_window` 72→75、`test_gui_api` 36、
`test_system_prompt_diagrams` 8 新增、`test_system_prompt_telegram`
8、`test_system_prompt_schedule_progress` 4、
`test_system_prompt_todo` 9、`test_system_prompt_scheduler` 8)。

### v0.9.5.1（修圖表檢視器沒連動 — 支援 `create_diagram` 的 JSON 輸出 + `<mxGraphModel>` 形狀）

v0.9.5 上線後使用者回報「用 drawio 繪制一個 hello 方塊,並用
drawio 顯示出來 — 完成!已用 draw.io 繪製並顯示 hello 方塊」
但 chat 訊息旁的「View in Diagrams」按鈕**沒出現**,圖表
檢視器沒連動。

**Root cause**:v0.9.5 的 `_extractDiagramXml` regex 只找
`<mxfile>...</mxfile>` block,但我們自家的 `create_diagram`
MCP tool 回的是 `{"xml": "<mxGraphModel>...</mxGraphModel>"}`
**JSON 包裹**,沒有 `<mxfile>` wrapper。Regex 完全 miss,按鈕
不渲染。

**修法**

* `_extractDiagramXml` 新增 3 個 fallback pattern:
  1. `<mxGraphModel>` (fenced + bare) — drawio 標準 export
     form
  2. JSON 包裹 `{"xml": "<...>"}` 或 `"xml": "<...>"` —
     MCP tool result 渲染進 chat 的常見形狀
  3. bare `<mxCell>...</mxCell>` blocks(罕見)
* 優先順序:mxfile (canonical) → mxGraphModel → JSON.xml →
  bare mxCell,first match wins。
* Viewer 端 `loadXmlIntoEditor` 多加 `_wrapForDrawio()` —
  如果收到的 XML 不是完整 `<mxfile>`,自動包成
  `<mxfile><diagram><mxGraphModel>...</mxGraphModel></diagram>
  </mxfile>` shell 後餵進 drawio embed。drawio embed 官方
  期望這個 envelope,LLM 不需要知道 drawio XML 結構。
* `_wrapForDrawio` 處理 3 種輸入形狀:
  - 已是完整 mxfile → pass through
  - 有 `<mxGraphModel>` → 包 mxfile + diagram
  - 只有 `<mxCell>` blocks → 建 minimal graphmodel(root +
    default layer),重複的 `id="0"` / `id="1"` cells 會被去掉
    (drawio 永遠要恰好一個 root + default parent)
  - 認不得 → 原樣送進去(drawio editor 會顯示自己的 error,
    不要 silent corruption)

**測試** — `tests/test_diagrams_window.py` 新增 12 tests,
其中:

* 5 個 `TestJsExtractDiagramXml` 新 case(包含 **user 報告的
  exact JSON shape** 從 log 抓出來)
* 7 個全新 `TestJsWrapForDrawio` class,直接從 `diagrams.js`
  抽出 `_wrapForDrawio` body 餵進 Node VM 跑 — 跟既有
  `_extractDiagramXml` 的 Node driver 同樣 pattern

**Test 結果**:`tests/test_diagrams_window.py` 60 → 72 passed。
無 regression。

### v0.9.5（GUI 獨立 Drawio 視窗 — viewer + editor + 三種來源 + 兩個觸發點）

v0.9.3+v0.9.4 的 filesystem MCP 讓 LLM 已經可以讀寫 `.drawio`
檔,但使用者回報「我看不到圖」:點開檔案只能從純文字 XML 想像
長相。v0.9.5 把 drawio 內嵌編輯器搬進 GUI,**每一個檔案都是一
個獨立的 OS 視窗**,跟主 GUI 完全解耦 — 可同時開多個,各自
獨立關。

**新檔**

* `gui/diagrams_window.py` — `DiagramsWindowManager` 類別管
  全部 viewer window 的 lifecycle(lazy write_root、`open_*`
  / `save_to_sandbox` / `read_from_sandbox` / `close_all`) +
  `_DiagramsViewerApi` 每個 window 自己的 Api 物件(避免跟主
  GUI Api 糾纏)。
* `gui/assets/diagrams.html` — viewer 主頁:drawio embed CDN +
  toolbar(儲存/匯出/重新載入/列印/狀態指示)+ drop overlay
  + sandbox picker dialog + save dialog。
* `gui/assets/diagrams.js` — postMessage 跟 drawio embed 通
  訊; drag-drop 讀 `.drawio`; 透過 `window.pywebview.api` 打
  Python 端做 sandbox I/O。
* `gui/assets/diagrams.css` — 暗色 toolbar + dialog 樣式。

**主視窗整合**

* `gui/api.py` 加 4 個新方法:`open_diagrams_window()`、
  `open_diagrams_with_xml(title, xml)`、
  `open_diagrams_with_path(title, path)`、
  `diagrams_window_status()`。全部回 `{"ok": ...}` 或
  `{"error": "..."}`,統一錯誤合約。
* `gui/app.py` 在 `_on_closed()` 多加一步
  `api._get_diagrams_manager().close_all()` 主視窗關掉時把所
  有 viewer window 一起殺掉,避免 zombie pywebview process
  留在 OS。
* `gui/assets/index.html` chat-toolbar 加 `📊 Diagrams Viewer`
  按鈕(資料 i18n key `diagramsViewer`,4 locale 全翻譯)。
* `gui/assets/app.js::renderChat()` 在 assistant 訊息偵測到
  `<mxfile>` XML block 時 append「View in Diagrams」按鈕 —
  一鍵把 inline drawio XML 送進 viewer。

**三種來源**

1. **從 sandbox 路徑** — LLM 透過 filesystem MCP 寫好的
   `.drawio`,GUI 使用者在 chat toolbar 點「📊 Diagrams
   Viewer」開新視窗後,viewer 內部用「Open from sandbox…」
   picker 選檔讀進 editor。
2. **從 chat 訊息** — LLM 在 chat 回的 inline `<mxfile>` block
   旁邊會出現「📊 View in Diagrams」按鈕,一鍵開新視窗載入
   該 XML。
3. **從外部 drag-drop** — 直接把 `.drawio` 從檔案總管拖到
   viewer 視窗,JS 端讀檔餵進 drawio(不經 Python)。

**安全設計**

* 只在 write sandbox 內讀寫(`QDRANT_FILESYSTEM_WRITE_ROOT`)
  — 跟 filesystem MCP 同一個根,所以 LLM 用 filesystem MCP
  寫的圖,在 viewer 也能立刻讀到。
* `.drawio / .xml / .dio` 三種副檔名白名單;dangerous 寫入
  副檔名 blocklist(`.exe / .bat / .sh / .ps1 / ...`)雙重
  防護;path traversal(`../etc/passwd`)走 `_fs_resolve_path`
  一樣卡掉。
* 4 MB XML 上限(跟 filesystem MCP read 一致)避免惡意/
  runaway LLM 把 GUI 吃光。
* 寫入走 atomic `tempfile.mkstemp` + `os.replace`,失敗時
  原檔不被破壞。

**測試** — `tests/test_diagrams_window.py` 60 tests,全
mock-based,零網路;包含 JS extraction regex 的 Node VM
driver(直接從 `app.js` 把 `_extractDiagramXml` 抽出來 eval,
測試 LLM 真實輸出 pattern)。

**全專案 test 統計**

* `tests/test_diagrams_window.py` 新增 60 passed
* `tests/test_diagrams_window.py + test_gui_api.py + test_filesystem_local_host.py + test_chat_autosave.py + test_gui_markdown_e2e.py + test_response_language.py`
  共 `309 passed, 1 skipped, 0 failed` in 27.30s,無 regression。

### v0.9.4（Filesystem MCP 擴充：copy_file / diff_files / patch_file）

v0.9.3 上線後使用者回報「光有 read/write 還不夠,我要
能複製檔案、看出兩檔差異、把 diff 直接套回去」——把
LLM-driven code refactor 整個閉環一次補齊。filesystem
tool surface 從 12 個 → **15 個**:

* **新增 `copy_file(src, dst, overwrite?, follow_symlinks?)`**
  * atomic copy（`mkstemp` + `shutil.copyfileobj` +
    `os.replace`）,source **保留**,與 `move_file` 互補
  * `overwrite=false` 預設不覆蓋;`true` 直接覆蓋
  * `follow_symlinks=false` 時對跳出 sandbox 的 symlink
    拒絕(防 `sandbox/escape -> /etc` 攻擊鏈)
  * 跟 `write_file` 一樣擋 `_FS_DANGEROUS_WRITE_EXTS`
    (`.exe` / `.bat` / `.sh` / `.ps1` / `.js` / `.vbs` / ...),
    **src / dst 兩端都查**
  * src / dst resolve 相同 → `same file; use write_file`
    (給更 actionable 的錯誤)
  * 寫入失敗 `os.unlink(tmp_path)` 清理 — 不留半寫檔
* **新增 `diff_files(a, b, context?, max_output_bytes?)`**
  * unified diff (`difflib.unified_diff` 包裝)
  * `context` 預設 3,範圍 0–100
  * `max_output_bytes` 預設 4 MB,超出回 `truncated=true`
    並行界截斷(不會有半行 hunk header)
  * binary / non-text 副檔名直接拒絕;非 UTF-8 內容報錯
  * 兩檔完全相同 → `identical=true`, `hunks=0`, `diff=""`
* **新增 `patch_file(path, patch, dry_run?)`**
  * apply unified diff **到位** (`mkstemp` + `os.replace`)
  * 嚴格 `difflib.unified_diff` 格式:context / `-` / `+` /
    `@@ -a,b +c,d @@` headers / `\ No newline`
  * context mismatch → 顯示差異的 line number + expected vs
    found(LLM 看得出哪裡對不上)
  * hunk overlap / hunk 越界 / 未知 line tag(`!` `\` 以外)
    / 沒有 `@@` hunks 全部給 actionable 錯誤
  * `dry_run=true` 預覽不寫,回 `preview`(前 4 KB) +
    `bytes_would_write` + `hunks_applied`
  * `_FS_DANGEROUS_WRITE_EXTS` 一律擋;patch 超過 4 MB 拒絕
  * **完整 round-trip**:同一檔對 `diff_files` 出的 diff
    直接餵回 `patch_file` 即可得到 b 版內容
    (`tests/test_diff_then_patch_roundtrip` 直接驗證)

**實作細節**:

* 兩個 diff / patch helper(`_fs_make_unified_diff` /
  `_fs_apply_patch`)+ 一個小 parser(`_fs_split_patch_hunks` /
  `_fs_parse_hunk_header`)走 stdlib `difflib` + 手寫小引擎,
  **零新增 dependency**
* `_fs_apply_patch` 對 stale patch 採取「嚴格檢查」策略:
  每一行 context / `-` 都跟原檔對一遍,不符就 `raise
  ValueError`,絕不靜默套錯的 patch 把檔案弄爛

**改動檔案**:

* `src/mcp_client/host.py` — 新增 5 個 helper
  (`_FS_DEFAULT_DIFF_CONTEXT` / `_FS_DIFF_MAX_OUTPUT_BYTES` /
  `_FS_PATCH_MAX_PATCH_BYTES` / `_fs_make_unified_diff` /
  `_fs_split_patch_hunks` / `_fs_parse_hunk_header` /
  `_fs_apply_patch`)+ 2 個 LocalTool schema + 2 個 handler。
  `copy_file` handler 對齊 `move_file` 既有模式(atomic
  + overwrite + same-file 防呆)
* `src/mcp_server/filesystem_server.py` — docstring 13→15
  tools;`_TOOL_SPECS` 新增 `diff_files` / `patch_file` 兩個
  stdio Tool spec
* `tests/test_filesystem_local_host.py` — `test_thirteen_tools`
  → `test_fifteen_tools`;`TestErrorEnvelope` tool tuple
  補 `diff_files` / `patch_file`;`TestMcpManagerRegistration`
  多 2 個 assert;新增 `TestDiffFiles`(14) + `TestPatchFile`
  (23);copy_file 在 v0.9.3 後段已 22 tests
* `pyproject.toml` — version 0.9.3 → 0.9.4

**測試結果**:

* `tests/test_filesystem_local_host.py`: **161 passed** in
  3.89s (v0.9.3 baseline 102 + copy 22 + diff 14 + patch 23)
* 全 mcp 相關 8 個 test 檔:`333 passed` in 13.86s,0 regression
  (baseline 296 + 新增 37)

### v0.9.3（Filesystem MCP — LLM 可以讀寫 sandbox 內的檔案）

新增 `filesystem-local` MCP server,12 個工具給 LLM 安全地
操控磁碟檔案:

* **7 個 read-only**（沙盒 = `QDRANT_FILESYSTEM_READ_ROOT`,
  fallback `WATCH_DIR` → `./documents`）:
  * `list_dir(path, pattern?, include_dirs?, include_files?)`
  * `stat_file(path)`
  * `read_file(path, offset_bytes?, max_bytes?, encoding?)` —
    二進位副檔名直接拒絕;`offset_bytes` + `max_bytes` 支援
    大檔分頁讀,`max_bytes` 上限 4 MB 保護 context。
  * `read_multiple(paths[], max_bytes_each?)` —
    多檔批量讀,binary 個別回 per-file error 不中斷整批。
  * `search_files(path, pattern, recursive?, max_results?)` —
    fnmatch glob。
  * `get_tree(path, max_depth?, max_entries?, include_dirs?, include_files?)`
  * `search_content(path, query, is_regex?, case_sensitive?,
    file_pattern?, max_results?, context_chars?)` — text grep,
    binary 副檔名自動跳過。
* **5 個 mutating**（沙盒 = `QDRANT_FILESYSTEM_WRITE_ROOT`,
  預設跟 read 一樣但可獨立調嚴,例如只指到 `./scratch`）:
  * `write_file(path, content, encoding?, overwrite?)` —
    atomic write（`mkstemp` + `os.replace`）;
    `.exe`/`.bat`/`.sh`/`.ps1`/`.js` 等 dangerous 副檔名
    無論 sandbox 都拒絕;`overwrite=false` 預設不覆蓋。
  * `append_file(path, content, encoding?)`
  * `delete_file(path)` — 只刪檔,不刪目錄。
  * `move_file(src, dst, overwrite?)`
  * `mkdir(path, exist_ok?)`

**沙盒安全（關鍵設計）**:

* 所有 path 走 `_fs_resolve_path()` 強制 `Path.resolve()`
  雙邊比對,`..` 跳出直接 `{"error": "..."}` 不碰磁碟。
* `_fs_walk()` 對 symlink 嚴格把關:有 symlink 跳出
  sandbox 立刻 skip,避免 `sandbox/escape -> /etc` 攻擊。
* atomic write 失敗時 `os.unlink(tmp_path)` 清理,target
  不會有半寫狀態（同 v0.9.0 `add_document`）。
* 所有 handler 永遠回 dict 不 raise — 壞掉的 LLM 呼叫不會
  把 chat loop 撕掉。

**雙根設計**（預設同 `WATCH_DIR` 但可分開）:
讀類給寬鬆根(例如整個 `~/notes`),寫類綁緊一條
`./scratch`,LLM 看得到但只能動那一條路徑。

**Server 兩種安裝**:

* **in-process**:`McpManager.from_config(include_local_hosts=True)`
  預設即時可用,LLM 直接呼叫。
* **stdio MCP**: `python -m src.mcp_server.filesystem_server`,
  給 Claude Desktop / Cline 等外部 client 用,
  註冊方式 `config/mcp.json` 加 stdio entry。

**改動檔案**:

* `src/mcp_client/host.py` — 新 `_FS_TEXT_EXTENSIONS` /
  `_FS_BINARY_EXTENSIONS` / `_FS_DANGEROUS_WRITE_EXTS` 常數;
  `_resolve_filesystem_root()` / `_fs_resolve_path()` /
  `_fs_safe_basename()` / `_fs_format_entry()` / `_fs_walk()`
  helper;`FilesystemLocalHost` class（12 個 LocalTool +
  對應 handlers）。`_fs_walk` 的 symlink escape 檢查 + 深度
  上限 0 = 只 root 是邊邊角角的 fix。
* `src/mcp_client/manager.py` — `McpManager._filesystem_local_host`
  屬性 + `from_config()` 註冊（保留 reference 供後續
  introspection）。
* `src/mcp_client/__init__.py` — 公開 re-export。
* `src/mcp_server/filesystem_server.py` — **新檔** stdio MCP
  server,12 tools 對應到 `LocalTool` 規格,compact JSON 編碼。
* `.env.example` — 新增 `QDRANT_FILESYSTEM_READ_ROOT` /
  `QDRANT_FILESYSTEM_WRITE_ROOT` 註解區塊 + 兩個設定範例。
* `pyproject.toml` — version 0.9.2 → 0.9.3。
* `tests/test_filesystem_local_host.py` — **新檔** 102 tests
  跨 18 個 test classes：tool surface、root 解析、
  helper invariants、read handlers、search handlers、write
  handlers、sandbox escape、dangerous ext、symlink 拒絕、
  atomic write、`McpManager` auto-registration、錯誤信封。
  全部 mock-based,零網路。

### v0.9.2（LLM-driven image resize — 突破 4 MB vision cap）

`qdrant-local` MCP server 在 v0.9.1 加了 `describe_image`,
但碰到大於 `ImageDescriber.MAX_IMAGE_BYTES` (4 MB) 的圖時
LLM 就撞牆。v0.9.2 加 `resize_image` 工具,**Pillow 縮圖**
寫回 import sandbox 內,讓 LLM 可以:

1. `describe_image` → 拿到「too large」錯誤
2. `resize_image(mode="path", file_path="...", max_width=1024)` →
   拿 `output_path` + `raw_image_base64`
3. `describe_image(file_path=output_path)` 或
   直接以 `raw_image_base64` 餵給多模態 chat model

**Tool 介面**:

* `file_path`（必填）— sandbox 內路徑
* `max_width`（必填）— 長邊上限,aspect ratio 自動保留
* `max_height`（選填,預設 = `max_width`）
* `out_filename`（選填,預設 `<stem>_resized.<ext>`）
* `format`（選填,預設 `jpeg` — 視覺模型最佳 size/quality）
* `quality`（選填,預設 85,JPEG/WebP 用）

**回傳**:`{ok, input_path, output_path,
original_dimensions, output_dimensions, original_size_bytes,
output_size_bytes, format, quality, within_cap,
raw_image_base64, sandbox_root}`。`within_cap=True` 表示已
落在 4 MB cap 內可直接 `describe_image`,否則 LLM 應再用更小
`max_width` 再縮一次。

**關鍵設計**:

* **EXIF 保留**:`ImageOps.exif_transpose()` 先依 Orientation
  tag 旋轉像素,然後把原始 EXIF segment 寫回 output(限
  JPEG / WebP)。PNG 因為是 lossless 不重壓,alpha channel
  保留。
* **沙盒**:沿用 v0.9.0 `QDRANT_IMPORT_ALLOWED_ROOT` 規則
  (`_is_within` + `Path.resolve` 雙邊比對),`..` 跳出被拒。
  `out_filename` 只接受 leaf basename。
* **Atomic 寫入**:`tempfile.mkstemp` 寫到 sandbox,成功後
  `os.replace`;Pillow 拋例外時 `os.unlink(tmp_path)` 清理,
  target 永遠不會有半寫狀態(同 v0.9.0 `add_document`)。
* **Compact JSON via stdio MCP**:`raw_image_base64` 可能
  4 MB,`json.dumps(separators=(",", ":"))` 省 30%。
* **永不 raise**:所有錯誤路徑回 `{"error": "..."}`,
  保護 chat loop。
* **預設 jpeg + quality 85**:對視覺模型 size/quality 最佳
  trade-off,PNG 對雜訊內容會反而爆大。

**改動檔案**:

* `pyproject.toml` — `0.9.1` → `0.9.2`,加 `Pillow>=10.0.0`
* `src/mcp_client/host.py` — `_RESIZE_DEFAULT_FORMAT` /
  `_RESIZE_DEFAULT_QUALITY` 常數,新 `LocalTool("resize_image")`,
  新 handler `_resize_image`(沿用 `_is_within` 沙盒 +
  `tempfile.mkstemp` + `os.replace` atomic 寫入 + EXIF
  preservation + 完整 validation)
* `src/mcp_server/server.py` — stdio MCP `Tool("resize_image")`
  + dispatch branch + `_do_resize_image` 委派給
  `_local._resize_image`
* `tests/test_resize_image.py` — **28 個新測試**(沙盒 +
  argument validation + happy-path downscale + JPEG < PNG
  size 證明 + within_cap signal + EXIF round-trip +
  atomic-write failure rollback + LocalTool wrapper 整合)

**測試**:`v0.9.1` baseline 135 → `v0.9.2` 163 passed(28 new),
0 failed。

### v0.9.1（LLM-driven image description — qdrant-local 擴充）

`qdrant-local` MCP server（v0.9.0 起已有 `add_document` /
`update_document`）新增**圖片描述**工具 `describe_image`，
讓 LLM 在 chat 中可以把圖檔交給本地 Ollama vision model
（`OLLAMA_VISION_MODEL`，預設 `gemma4:31b-cloud`）分析讀取，
不必跳出 chat 介面手動 OCR / vision API call。

#### 設計重點：單一 tool + `mode` 切換

跟 v0.9.0 `add_document(mode="path"|"content")` 一致：

* **`mode="path"`** — 大圖檔:LLM 只傳檔案路徑(沙盒內)，
  bytes 完全不讀進 context；工具從磁碟讀圖、呼叫
  `ImageDescriber.describe()`(Ollama Cloud vision API +
  SHA-1 cache)。
* **`mode="base64"`** — 小圖 / GUI 拖圖 / 剪貼簿:
  LLM 傳 `data:image/png;base64,<...>` 或純 base64 字串
  (預設 `image/png` mime)；工具解碼 + magic-byte sniff
  (PNG/JPEG/GIF/WEBP/BMP,SVG 接受 XML 文字)+ 寫到沙盒
  tmp + 呼叫 vision,結束後自動 unlink tmp。
* 共用同一個 tool，LLM 用 `mode` 參數二選一。共用
  `QDRANT_IMPORT_ALLOWED_ROOT` 沙盒，兩條路徑都不會把
  bytes 讀進 LLM context。

#### 回傳格式（給 LLM 完整 metadata）

```jsonc
{
  "ok": true,
  "mode": "path" | "base64",
  "description": "<vision model 100-200 字雙語描述>",
  "mime": "image/png",
  "size_bytes": 1234,
  "sha1": "<40 hex>",
  "filename": "shot.png",
  "raw_image_base64": "<原始 bytes 的 base64>",
  "from_cache": false,
  "sandbox_root": "/path/to/import/root"
}
```

**關鍵**：`raw_image_base64` 讓多模態 chat model（例如
Qwen2.5-VL、GPT-4o-vision、Gemini Pro Vision）能在**下一輪**
直接把 `{"type": "image_url", "image_url": {"url":
"data:image/png;base64,<...>"}}` 接到 chat message 上讀圖，
不必 LLM 再傳第二次檔案，省一個 round-trip。

#### 安全 + 沙盒設計

* `mode="path"`：路徑必須 `Path.resolve()` 後在
  `QDRANT_IMPORT_ALLOWED_ROOT` 內，`..` traversal 在
  `_is_within()` 內擋下；副檔名必須是支援清單
  (`.png`/`.jpg`/`.jpeg`/`.gif`/`.webp`/`.bmp`/`.svg`)。
* `mode="base64"`：payload 通過 magic-byte sniff 才接受；
  size cap = 4 MB（與 `ImageDescriber.MAX_IMAGE_BYTES` 對齊）；
  4-byte aligned base64 必要，padding 不足會 raise
  (避免 silently accept garbage)。
* `mode="base64"` 的 tmp 檔(`.describe_<sha1_12>.<ext>`)
  在 `finally` block 內 `os.unlink()`，無論 vision call
  成功 / 失敗 / sandbox 內外錯誤都不會殘留。**這條是
  security/privacy 兩方面都要的** — sandbox 不能堆垃圾。
* LLM 可選傳 `prompt` override 替換 vision model 的雙語
  prompt；override 在 `finally` 後一定還原成 default，
  同一個 ImageDescriber 物件之後的請求不受污染。
* 永遠回 dict(`{"error": "..."}`)，不 raise — 保護 chat loop。

#### 雙 transport 暴露

兩個 transport 都上線，跟 v0.9.0 一致：

* **In-process `QdrantLocalHost`** — Agent tool-calling
  loop 直接呼叫。
* **stdio MCP server** — `python -m src.mcp_server.server`
  給 Claude Desktop / Cline 等外部 client，delegate 到
  `_local._describe_image`。**`raw_image_base64` 很大**（3 MB
  圖約 4 MB base64），用 compact JSON（`separators=(",", ":")`）
  編碼避免 TextContent payload 膨脹。

#### 觸發詞（給 system prompt / 使用者）

* 「這張圖是什麼」「描述一下」「分析圖片」→ `describe_image`
* LLM 拖圖 / 貼 base64 → `describe_image(mode="base64")`
* LLM 指到檔案 → `describe_image(mode="path")`

#### 改動

* **`src/mcp_client/host.py`**
  * 新 module-level 常數 `_DESCRIBE_IMAGE_MIMES`
    (PNG/JPEG/GIF/WEBP/BMP/SVG)、`_IMAGE_MAGIC` 表、
    `_DESCRIBE_IMAGE_MAX_BYTES = 4 MB`。
  * 新 helper `_ext_for_mime(mime)` — reverse ImageDescriber
    mime 對應。
  * 新 helper `_parse_data_url(data_b64)` — 接受
    `data:<mime>;base64,<...>` 或 bare base64，magic-byte
    sniff + size cap + empty payload check。
  * 新 helper `_write_base64_to_sandbox_tmp(root, bytes,
    mime)` — atomic mkstemp + os.replace，Windows fd close
    + unlink on error；產出穩定檔名 `.describe_<sha1_12>
    .<ext>`，sha1 相同 → 同檔名（concurrent calls 自然
    dedup）。
  * 新 method `_describe_image(args)` — 兩個 mode 共享
    sandbox，preflight 失敗回 dict（不 raise），vision
    exception 也回 dict（保護 chat loop），cache-hit detection
    用 SHA-1 dict 比對。
  * `QdrantLocalHost.list_tools()` 新增 `describe_image`
    LocalTool，附完整 JSON Schema (`mode` enum + 4 個
    properties)。
  * 新 module-level `from src.ingest.image_describer import
    ImageDescriber`（讓 unit-test 能 patch module attribute）。
* **`src/mcp_server/server.py`** — stdio MCP server 同步
  新增 Tool + dispatch 分支；`_do_describe_image` 用
  compact JSON 編碼結果（避開 raw_image_base64 膨脹）。
* **`pyproject.toml`** — version 0.9.0 → 0.9.1。

#### 測試 — 新檔 `tests/test_describe_image.py`

**47 個測試全綠**，涵蓋：

* `TestExtForMime` — 6 個 known mimes + 未知 fallback `.bin`
* `TestParseDataUrl` (12) — bare base64 / data URL / JPEG /
  utf8 拒絕 / 不支援 mime / 空字串 / 非字串 / bad base64 /
  magic mismatch / size cap / empty decoded
* `TestWriteBase64ToSandboxTmp` (4) — 寫入成功 / 自動建立
  sandbox root / 同 sha1 同檔名 / 不同 mime 對應不同副檔名
* `TestDescribeImageListTools` (2) — `describe_image` 工具
  出現 + schema 正確（mode enum、4 個 properties）
* `TestDescribeImagePathMode` (8) — 大圖 ingest 成功 / 沙盒
  外拒絕 / 缺檔案 / 目錄 / 不支援副檔名 / 缺 file_path /
  filename_hint 覆寫 / `..` 逃逸
* `TestDescribeImageBase64Mode` (8) — 小圖 ingest 成功
  (bare + data URL) / filename_hint / filename_hint
  strip path separators / tmp 成功後清掉 / tmp 失敗後清掉 /
  magic mismatch / 缺 data_base64
* `TestDescribeImageModeValidation` (3) — mode 缺失 / 非法 /
  非 dict payload
* `TestDescribeImageCustomPrompt` (3) — custom prompt 套用
  並在呼叫後還原 / cache hit 偵測 / vision call 例外回 dict
* `TestLocalToolWrapperIntegration` (2) — 走完整
  `LocalTool.__call__` dispatch,驗證 LLM 包成
  `{"args": {...}}` wrapper 也能正確分派 + bare kwargs spread
* `test_describe_image_mimes_include_canonical` /
  `test_image_magic_table_covers_mimes` — sanity check

與 `test_mcp_client` / `test_meta_local_host` /
`test_local_tool_argument_coercion` /
`test_qdrant_local_host_add_update` 合計 **135 個測試全綠**，
無 regression。

### v0.9.0（LLM-driven document ingest + update — qdrant-local 擴充）

`qdrant-local` server（v0.8.0+ 起的本地端核心 MCP）原本只提供
read-only / 刪除類工具（`search_documents` / `list_documents` /
`get_document_stats` / `remove_documents`）。**v0.9.0 起新增
兩個寫入類 MCP 工具**，讓 LLM 在對話中能「匯入新文件」與「修改
現有文件」，而不必請使用者手動跑 CLI。

#### 兩個新工具

* **`add_document(mode, file_path?, filename?, content?, overwrite?)`**
  — 匯入新文件。支援兩條路徑：
  * `mode="path"`（**大檔匯入**）：LLM 只傳檔案路徑，**檔案內容
    完全不讀進 LLM context window**；工具直接從磁碟讀檔、走原本
    `IngestPipeline.ingest_file` 完整流程（chunk → embed →
    upsert）。適合幾百 MB 的 PDF / DOCX / EPUB。
  * `mode="content"`（**小檔匯入**）：LLM 傳 `filename` + 完整
    `content` 字串；工具把字串寫到沙盒根的暫存檔（atomic
    write），再走同一條 ingest 流程。適合幾 KB 的 `.md` /
    `.txt` 筆記 / FAQ / 設定片段。
  * 共用同一個 tool，LLM 用 `mode` 參數二選一。`overwrite`
    預設 `false`，目標檔已存在會回 error 防止誤覆蓋。

* **`update_document(filename, content)`**
  — 修改已存在的文件。LLM 提供目前 `list_documents` 看到的檔名
  + 完整新內容；工具先 `delete_by_filename` 砍掉舊 chunks、
  再把新內容 atomic 寫到沙盒、最後重新 ingest。**filename 必須
  已存在於 Qdrant**，否則回 error 防止 typo 誤刪別的檔案。

#### 沙盒安全（關鍵設計）

* 新增 env `QDRANT_IMPORT_ALLOWED_ROOT`（預設 = `WATCH_DIR`，
  再 fallback `./documents`）。所有 `file_path` 都必須
  `Path.resolve()` 後在沙盒根目錄底下，否則回 `{"error":
  "path outside allowed root ..."}` 直接拒絕；`..` traversal
  在 `_is_within()` 內被擋下。
* `mode="content"` 的 `filename` 只接受 leaf basename（不可含
  `/` / `\` / `..`、不接受絕對路徑），副檔名必須是 pipeline
  支援的清單（`txt/.md/.pdf/.docx/.pptx/.epub/.py/.js/...`）
  否則 loader 會 silent skip — 早一關 error 更清楚。
* 寫入用 `tempfile.mkstemp` + `os.replace` atomic rename；
  Windows 上 fd 必須先 `os.close(fd)` 才能 `os.unlink` 半成品
  （否則 `[WinError 32]`）。

#### 雙 transport 暴露

兩個新工具同時在 **in-process `QdrantLocalHost`**（Agent
tool-calling loop 用）與 **stdio MCP server**（`python -m
src.mcp_server.server`，給 Claude Desktop 等外部 client 用）上
線。後者直接 delegate 到 `QdrantLocalHost._add_document` /
`_update_document`，確保 sandbox 規則一致。

#### 工具 schema（給 LLM 看）

```jsonc
// add_document
{
  "mode": "path" | "content",
  "file_path": "<abs or rel path>",       // mode=path 時必填
  "filename":  "<basename>",               // mode=content 時必填
  "content":   "<full body>",              // mode=content 時必填
  "overwrite": false                       // mode=content 預設
}

// update_document
{ "filename": "<existing basename>", "content": "<full body>" }
```

#### 觸發詞（給 system prompt / 使用者）

* 「匯入」「加入」「建立文件」「上傳」→ `add_document`
* 「更新」「修改」「改寫」「覆寫」→ `update_document`（先
  `list_documents` 確認 filename）
* 大檔 → `mode="path"`；小檔 → `mode="content"`

#### 改動

* **`src/mcp_client/host.py`**
  * `QdrantLocalHost.list_tools()` 新增 `add_document` 與
    `update_document` 兩個 `LocalTool`，附完整 JSON Schema。
  * 新 method `_ingest_via_pipeline(p)`：包 `IngestPipeline`，
    把它的 stdout chatter 轉到 `logging`。
  * 新 method `_add_document(args)` / `_update_document(args)`：
    兩條路徑共用 sandbox；preflight 失敗回 dict（不 raise），
    pipeline 例外也回 dict 帶 `error` 鍵。
  * 新 helper `_resolve_import_root()`：env → `WATCH_DIR` →
    `./documents` 三段 fallback，回 `Path.resolve()`。
  * 新 helper `_is_within(child, parent)`：用
    `Path.is_relative_to()` 做 containment check，防 `..`
    逃逸；`OSError` 直接 `False`。
  * 新 helper `_write_content_to_sandbox(root, filename,
    content, overwrite)`：驗證 basename / 副檔名 / 沙盒範圍，
    atomic write（`mkstemp` + `os.replace`），error 路徑
    close fd + unlink tmp（Windows 相容）。
* **`src/mcp_server/server.py`** — stdio MCP server 同步新增
  兩個 Tool + dispatch 分支；`_do_*` 直接 delegate 到
  `_local._add_document` / `_local._update_document`，
  JSON-encode 結果回 `TextContent`。
* **`pyproject.toml`** — version 0.8.9 → 0.9.0。
* **`.env.example`** — 新增 `QDRANT_IMPORT_ALLOWED_ROOT`
  註解區塊 + 三個設定範例。

#### 測試 — 新檔 `tests/test_qdrant_local_host_add_update.py`

47 個測試全綠，涵蓋：

* `TestResolveImportRoot` — env → WATCH_DIR → ./documents
  fallback 三段、`QDRANT_IMPORT_ALLOWED_ROOT` override 勝出。
* `TestIsWithin` — 內部、外部、`..` 逃逸、自含、父目錄否定。
* `TestWriteContentToSandbox` — atomic 寫入、副檔名 reject、
  path separator / `..` reject、既存檔 overwrite 行為、
  sandbox 自動建立、**寫入失敗不留半成品**（patch `os.fdopen`
  raise → 驗證 `.tmp` 已清掉）。
* `TestQdrantLocalHostListTools` — 6 個工具齊在、兩個新工具
  schema 正確。
* `TestAddDocumentPathMode` — 大檔 ingest 成功、沙盒外拒絕、
  缺檔案 / 目錄 / `file_path` 缺失 / `..` 逃逸。
* `TestAddDocumentContentMode` — 小檔寫入成功、缺
  `filename` / `content` / 非字串 content / 路徑分隔 / 不支援
  副檔名 / overwrite=false 保護既存檔 / overwrite=true 替換。
* `TestAddDocumentModeValidation` — mode 缺失或非法值。
* `TestLocalToolWrapperIntegration` — 走完整
  `LocalTool.__call__` dispatch，驗證 LLM 包成 `{"args":
  {...}}` wrapper 也能正確分派。
* `TestUpdateDocument` — 成功路徑、檔名不在 collection 拒絕、
  缺 `filename` / `content` / 路徑分隔 / 不支援副檔名、刪除
  成功但 re-ingest 失敗時的 recovery hint。
* `TestNoExceptionsLeak` — pipeline 拋例外時 handler 回 dict
  （不 raise），保護 chat loop。
* `test_allowed_exts_includes_canonical_text` — 沙盒允許副檔名
  清單與 `config.ingest.supported_extensions` 對齊。

與其他相關測試套件（`test_mcp_client` /
`test_meta_local_host` / `test_local_tool_argument_coercion`）
合計 **116 個測試全綠**，無 regression。

### v0.8.7（排程生命週期上限 + LLM 進度追蹤）

排程現在可以設定**總執行次數**(`max_fires`)和**終止時間**(`expires_at`),
LLM 可透過 chat 查詢每個排程的執行狀態 — 不必手動翻 `history.jsonl`。

* **`Schedule` dataclass 新增三個欄位**
  * `max_fires: int | None` — 達此次數後自動 disable + `status="exhausted"`
  * `expires_at: str | None` — ISO-8601;到達後 auto-disable + `status="expired"`
  * `status: str | None` — `"active"` / `"expired"` / `"exhausted"` /
    `"disabled"` / `"failed"`,LLM 直接讀一目了然
  * 舊 v0.8.6 JSON 仍可讀(預設值相容)
* **`ScheduleManager` 四個新方法**
  * `get_progress(sid)` — 完整 snapshot(status / fire_count /
    max_fires / fires_remaining / next_fire_at + seconds until /
    expires_at + seconds until expiry / last_error / 最近 5 次 runs)
  * `list_runs(sid, limit=10)` — 從 `<sid>.history.jsonl` 讀最新 N
    次(answer 截斷到 500 chars)
  * `set_max_fires(sid, n)` — 線上調整次數上限
  * `set_expires_at(sid, iso)` — 線上調整終止時間(驗證 ISO-8601)
* **`Scheduler.tick_once` 雙階段檢查**
  * Phase 1: 讀 `next_fire_at` (便宜 gate,保留舊 hand-edited 行為)
  * Phase 2: 跑 `compute_next_fire()` 確認 lifecycle 還活,max_fires /
    expires_at 過期就 auto-disable + 寫 status
* **兩個新 MCP tool(LLM 可呼叫)**
  * `get_scheduled_task_status(task_id)` — 一個 roundtrip 答完
    「這個排程跑幾次了」「還能跑幾次」「過期了嗎」「上次什麼時候
    跑的」
  * `list_scheduled_task_runs(task_id, limit)` — 「上次跑得怎樣」
    「show me the last N runs」
  * In-process (`SchedulerLocalHost`) + stdio MCP server 都暴露
* **CLI 兩個新 flag**
  * `schedules add --max-fires N` — 設次數上限
  * `schedules add --expires-at 2027-12-31T23:59:59` — 設終止時間
  * `schedules list` 多一欄 `STATUS`(`active` / `expired` 等)
  * `schedules show` 多印 `status` / `max_fires` / `expires_at` /
    `last_error` / `last_error_at`
* **Agent system prompt 新觸發區塊**
  * 「跑幾次了」「還能跑幾次」「過期了嗎」「上次什麼時候跑的」
    「上次跑得怎樣」→ 自動導向對應 tool
  * 包含「為什麼 X 停了」的 status 診斷決策樹
* **測試** — 5 個新 test 檔,共 **42 個新測試**,全綠
  * `tests/test_schedule_progress.py` (18) — 進度 / 生命週期
  * `tests/test_schedule_status_mcp.py` (13) — MCP tool + stdio e2e
  * `tests/test_schedule_cli.py` (6) — CLI flag 驗證
  * `tests/test_system_prompt_schedule_progress.py` (5) — 系統 prompt
* **Bug fix** — `compute_next_fire` 對 ISO-8601 字串帶 `Z`(tz-aware)
  時,與 `datetime.now()`(naive)比較會炸 `TypeError`,現在頂端
  統一 strip tzinfo,讓 naive/aware 都能混比
* **版本 bump** — `pyproject.toml` 0.8.6 → 0.8.7

### v0.8.7.1（修 GUI chat 離開不存對話記錄的安靜丟失 bug）

v0.8.7 上線後使用者回報「離開 chat ui 程式不會存對話記錄」：
在 chat 對話框打字後若沒按「💾 儲存」就直接 X 關閉，整段對話
直接消失，要重打。**根因**：`gui/app.py` 用了 pywebview 的
內建 `confirm_close=True`，但這個 flag 只顯示 OS 確認對話框，**沒
hook 任何 save 路徑**——使用者必須主動按 Save 才能持久化。
`gui/assets/app.js` 原本只在手動 Save 時才 call `api.save_session`。

**3 層安全網**（belt-and-suspenders）：

* **Layer 1 — JS auto-save after reply**：`sendChat` 收到
  assistant 回覆後立刻 `autoSaveChat()` 背景 save 一次。
  訊息**寫過就落地**，正常路徑下 close 時不需要 save。
* **Layer 2 — JS beforeunload**：window 卸載前觸發
  `_beforeUnloadSave` 做最後一次 fire-and-forget save，
  並 mirror `chat_session` 到 `window.pywebview.state`（共享
  state 物件，Python side 可同步讀）。
* **Layer 3 — Python `window.events.closing` hook**：
  `gui/app.py::_save_chat_draft_on_close(window)` 用
  `window.evaluate_js` 抓 mirror state，呼叫 `SessionManager.save`
  同步落盤。**為什麼移除 `confirm_close=True` 必要**：原本的
  OS confirm dialog 在 `closing` handler **之後**才出現，
  代表 save 已經在 fire；若保留 confirm_close=True，每次關閉
  都得多點一次 confirm，UX 也壞。

**改動**：

* `gui/app.py`：新增 `_save_chat_draft_on_close(window)` +
  `_on_window_closed()`，在 `webview.create_window(...)` 後
  註冊 `window.events.closing += _on_closing` 與
  `window.events.closed += _on_closed`，**移除 `confirm_close=True`**
* `gui/assets/app.js`：新增 `autoSaveChat()` / `syncStateMirror()`
  / `_beforeUnloadSave()`，`sendChat` 末尾 + `newSession` /
  `loadSessionById` / `saveSession` / `clearChat` 都 call
  `syncStateMirror()`；`bindEvents` 註冊 `beforeunload` 監聽器
  與 `visibilitychange` mirror（alt-tab 離開也能 mirror）

**測試**：新檔 `tests/test_chat_autosave.py`（14 tests）
— Python closing hook 的 7 個 mock window 場景
（持久化 / no-state / 缺 chat_session / null id / evaluate_js
exception / overwrite / bad JSON）+ JS source-level 7 個
檢查（必備函式定義、beforeunload wire-up、visibilitychange、
sendChat 末尾 autoSaveChat、四個 session 變更路徑都有
syncStateMirror、Python side 兩個 hook 都已 wire、
`confirm_close=True` 已移除）。
v0.8.7 baseline 175 → 189 passed（其中 14 個為新
autoSave 測試），0 skipped。

### v0.8.7.2（修 GUI 關閉時白化當機 deadlock — Python closing hook 拿掉 evaluate_js）

v0.8.7.1 雖然解決了「關閉視窗不存對話」的問題，但 user log
2026-08-31 17:41:48 又回報 `qdrant-agent-gui.bat` 關閉時整個
程式**白化當機**：

```
2026-08-31 17:41:48 [INFO] gui.app: pywebview window is closing
 — flushing chat draft
（程式卡死，使用者強制關閉）
```

**根因**：v0.8.7.1 的 Python closing hook 內呼叫
`window.evaluate_js(...)` 抓 `window.pywebview.state` 同步資料。
但 `closing` 事件觸發時，pywebview **正在 tear down 渲染器**，
JS 執行緒已停止接受新工作；pywebview 的 CEF / WKWebView /
WebView2 後端在等待一個永遠不會來的回應 → **死鎖**。
GUI 進程卡在 close handler，OS 必須強制 kill（這就是使用者
看到的「白化當機」）。

**修法**（v0.8.7.2）：

1. **Python closing hook 完全 no-op** — 移除 `evaluate_js`
   呼叫，純粹只 log。整段 `_save_chat_draft_on_close` 變成
   stub。
2. **JS 端 `localStorage` backup**（v0.8.7.2 新增）—
   `autoSaveChat()` 每次 save 之前先寫一份到
   `localStorage['chat_draft_backup']`，save 成功後清掉。
   localStorage 是**同步 JS 端儲存**，pywebview tear down 時
   來不及破壞 → 永遠落地。
3. **JS 端 `bootDraftRecovery()`**（v0.8.7.2 新增）— 啟動時
   檢查 `chat_draft_backup` 是否存在。如果存在就自動 restore
   到 `chatSession`，並 toast 通知使用者「已從上次未存檔的
   草稿復原 N 則訊息」。

**3 層保險**(不依賴 Python close hook)：

  1. **JS `autoSaveChat`** — 每次助手回覆後背景 save，同時
     寫 `localStorage` backup
  2. **JS `beforeunload`** — 渲染器 tear down 前 fire-and-forget
     `api.save_session` (已經在 v0.8.7 實作)
  3. **JS `localStorage` backup + `bootDraftRecovery`** —
     雙保險，下次啟動復原 (v0.8.7.2 新增)

**改動檔案**：

* `gui/app.py` — `_save_chat_draft_on_close()` 變 no-op
  (只 log)；`window.events.closing` hook 仍 wire，但不會
  凍結 GUI
* `gui/assets/app.js` — `autoSaveChat()` 加 `localStorage`
  backup + 成功後 `removeItem`；新增 `bootDraftRecovery()`
  函式；`bindEvents` 在 `setTimeout` 內呼叫它
* `tests/test_chat_autosave.py` — 移除 2 個預期 evaluate_js
  行為的測試（v0.8.7.1 設計被否），加 4 個新測試：
  - `test_close_hook_is_noop_v0871` — 驗證 hook 不呼叫
    `evaluate_js`
  - `test_close_hook_does_not_touch_disk` — 驗證 hook 不寫
    `sessions/`
  - `test_app_js_uses_localstorage_for_draft_backup` — JS
    source-level 檢查 `localStorage.setItem('chat_draft_backup'...)`
  - `test_app_js_boots_draft_recovery_function` — JS
    source-level 檢查 `function bootDraftRecovery` 存在
  - `test_app_py_close_hook_does_not_use_evaluate_js` —
    防止有人未來又把它加回去 (使用 docstring 外的檢查)

v0.8.7.1 baseline 189 → v0.8.7.2 189 + 4 new = 193 passed
(其中 chat_autosave 14 → 18), 0 failed。

### v0.8.8（Multi-level Todo List MCP — LLM 計畫性作業執行機制）

v0.8.8 引入**多階層 todo list MCP 子系統**，讓 chat LLM 對
複雜任務可**規劃 → 拆解 → 執行 → 追蹤**。每個任務可有自己的
子 todo list 與依賴關係，可隨時新增 / 刪除 / 異動內容。

**核心模組**：`src/core/todo_manager.py`（v0.8.8+）
  * `TodoTask` dataclass：id / parent_id / title / status
    （pending / in_progress / completed / skipped / failed /
    blocked）/ priority（high / medium / low）/ depends_on /
    notes / result / error / 時間戳
  * `TodoManager` CRUD + 階層操作 + 進度查詢
  * 儲存：`$QDRANT_RAG_HOME/todos/<task_id>.json` +
    `<task_id>.journal.jsonl`（append-only audit log）+
    `index.json`（O(1) list）

**MCP 工具表面**（16 個 tools）

In-process `TodoLocalHost` + stdio MCP server
`python -m src.mcp_server.todo_server` 暴露同一個表面：

  * `create_task` / `add_subtask` — 新增 root 或子任務
  * `update_task` / `delete_task` — 異動 / 刪除（含 cascade）
  * `list_tasks` / `get_task` / `render_task_tree` — 查詢
  * `start_task` / `complete_task` / `skip_task` / `fail_task` —
    狀態機操作
  * `add_task_dependency` / `reorder_task` — 依賴與排序
  * `get_todo_progress` / `list_task_journal` / `execute_plan` —
    進度與審計

**Agent system prompt 新觸發區塊**（v0.8.8+ plan / execute
trigger block）

  * 「幫我規劃」「做個計畫」「拆解任務」「plan X」「step by
    step」→ `create_task` 開 root + `add_subtask` 拆子任務
  * 「現在到哪裡了」「進度」「how far along」→
    `get_todo_progress`（by_status / completion_pct / ready /
    blocked / next_recommended）
  * 「下一步做什麼」「what should I do next」→
    `get_todo_progress` + `start_task`
  * 「這個任務做完沒」「is X done yet」→ `get_task`（status +
    completed_at）
  * 「任務卡住」「stuck」「blocked」→ `get_todo_progress` 看
    blocked 清單、補 depends_on 上游
  * 「重新安排 X」「X 比較急」→ `reorder_task`

  Auto-completion：所有子任務都 terminal → parent 自動 completed
  （不要手動 complete parent）。LLM 是 planner AND executor
  —— 每次 turn 推進一個 task。

**CLI**（v0.8.8+ `qdrant-agent.bat todo ...`）

  * `todo list [--parent ID] [--status X] [--include-descendants]`
  * `todo show ID`
  * `todo add --title T [--parent ID] [--description D]
    [--priority {high,medium,low}]`
  * `todo tree`
  * `todo progress [--task ID]`
  * `todo complete ID [--result "..."]`
  * `todo delete ID [--no-cascade]`

**改動檔案**：

* `src/core/todo_manager.py` (NEW, ~600 行) — 核心 TodoManager
* `src/core/__init__.py` (NEW) — 套件 marker
* `src/mcp_client/host.py` — `TodoLocalHost` (16 個 LocalTool)
  + 修 `_py_type_to_json` 支援 string annotation
* `src/mcp_client/manager.py` — `TodoLocalHost` 註冊
* `src/mcp_server/todo_server.py` (NEW) — stdio MCP server
* `src/agent/agent.py` — system prompt plan / execute trigger
  block
* `cli.py` — `todo` subparser + `_handle_todo()`

**測試**：4 個新檔案共 **103 個新測試**，全綠
  * `tests/test_todo_manager.py`（48）— 核心 CRUD / state
    machine / cascade / reorder / journal
  * `tests/test_todo_local_host.py`（29）— 16 個 LocalTool
    end-to-end + error paths + input schema
  * `tests/test_todo_server.py`（16）— stdio MCP server
    註冊 + 端到端 dispatch
  * `tests/test_system_prompt_todo.py`（10）— system prompt
    trigger block 完整性 + 與 v0.8.7+ schedule 區塊共存

無 regression：v0.8.7 baseline 189 → v0.8.8 325 passed。

### v0.8.9（4-Provider LLM dispatch — Ollama / OpenRouter / SiliconFlow / OpenAI）

v0.8.9 把原本的 2-provider `if/elif` 鏈重構為**資料驅動的四家
provider dispatch**,加 `LLM_PROVIDER` 顯式覆寫。同一個 OpenAI
SDK 走完全部 endpoint,後面要加第五家只需要一個 tuple entry。

**四家 provider + 預設端點**:

| Provider      | 預設 base\_url                  | 預設 chat model                  | 觸發 key                    |
|---------------|---------------------------------|----------------------------------|-----------------------------|
| `ollama`      | `https://ollama.com/v1`         | `gpt-oss:120b-cloud`             | `OLLAMA_API_KEY`            |
| `openrouter`  | `https://openrouter.ai/api/v1` | `anthropic/claude-3.5-sonnet`    | `OPENROUTER_API_KEY`        |
| `siliconflow` | `https://api.siliconflow.cn/v1`| `Qwen/Qwen2.5-72B-Instruct`      | `SILICONFLOW_API_KEY`       |
| `openai`      | `https://api.openai.com/v1`     | `OPENAI_CHAT_MODEL` 預設 gpt-4o-mini | `OPENAI_API_KEY`         |

**解析算法**(`Agent._pick_provider()`):

1. **顯式覆寫優先**:`config.LLM_PROVIDER` 若為 `ollama` /
   `openrouter` / `siliconflow` / `openai` 其中之一,只走該家,
   key 空就 raise 一條可讀的錯誤訊息
   （`LLM_PROVIDER='x' set but X_API_KEY is empty`）。
2. **自動偵測**:按 tuple 順序 `ollama → openrouter →
   siliconflow → openai`,回傳第一個 API key 非空者。
3. **全空 fallback**:raise 一條列出全部 4 個 env var 的
   ValueError,新手照抄即可。

`LLM_MODEL` 全域覆寫:非預設值 (`!= "gpt-4o-mini"`) 時蓋掉
provider 各自的 `*_CHAT_MODEL`,方便一鍵切換模型。

**4 個關鍵設計點**:

* **資料驅動**:`Agent._PROVIDER_ORDER` 是 tuple 序列,
  每筆 `(name, key_fn, url_fn, model_fn)`,加第五家 = 一個
  tuple entry + 一個 env-var 區塊。沒有 nested `if/elif`。
* **late-bound config**:`_get_config()` helper 在每次
  呼叫時重做 `from src import config as _cfg_mod; return
  _cfg_mod.config`。避免 lambda 在 class body 對 module-level
  名稱做 import-time closure 綁定 — 讓 test 內
  `importlib.reload(src.config)` 和 REPL tinkering 不需要
  重 load agent。
* **OpenAI SDK 統一介面**:四家都是 OpenAI-compatible endpoint,
  `openai.OpenAI(api_key=..., base_url=...)` 一個 client 走四家
  protocol,免引入 anthropic / google-genai / 各自 SDK。
* **legacy escape hatch**:`OPENAI_BASE_URL` 仍受尊重,
  想用 LM Studio / vLLM / 任何自架 OpenAI 相容伺服器,
  `OPENAI_API_KEY=lm-studio` + `OPENAI_BASE_URL=http://localhost:1234/v1`
  即可。

**測試**(`tests/test_llm_providers.py`, 19 tests, 全綠):

* 自動偵測優先序（4 providers × 多種 key 組合）
* `LLM_PROVIDER` 顯式覆寫 4 sub-cases（含大小寫不敏感 +
  unknown provider + 強制 provider + key 空 的明確錯誤訊息）
* `LLM_MODEL` 全域覆寫對 ollama / openai 生效
* 預設模型 / 預設 endpoint 名稱 / 自訂 proxy URL
* `Config.__repr__` 隱藏全部 5 個 API key（OPENAI / OLLAMA /
  QDRANT / OPENROUTER / SILICONFLOW）

`_smoke_4prov3.py` 10 個 end-to-end 場景全綠（無 keys 列 4 家
錯誤、only-OR、only-SF、4-keys 同開自動偵 ollama、override、
override+空 key 錯誤、LLM_MODEL 覆寫、custom proxy URL、
priority 順序在三種 key 組合下都正確）。

> **v0.9.18.0 / v0.9.19.0 更新**（以上為 v0.8.9 當時的狀態，仍準確，
> 另加三件事）:
>
> * **第五家已加入**:`openai-compat`（v0.9.19.0,append 在 tuple
>   尾端）— `OPENAI_COMPAT_API_KEY` / `OPENAI_COMPAT_BASE_URL`
>   （必填）/ `OPENAI_COMPAT_CHAT_MODEL` 完全獨立三件組,與
>   `openai` 並存（可同時掛兩個相容端點）;有 key 無 URL 時
>   `_pick_provider` raise 清晰錯誤,failover 走訪 skip+警告。
> * **`OPENAI_CHAT_MODEL` 為真**（v0.9.19.0）:openai 槽位的專屬
>   模型鍵。在此之前 openai 只能用 `LLM_MODEL`,而 `LLM_MODEL`
>   非預設值會全域覆寫其他三家 — 把 openai 指到 DeepSeek 並設
>   `LLM_MODEL=deepseek-chat` 會讓 failover 拿同一個模型名去打
>   `ollama.com` / `openrouter.ai`（failover 壞掉）。現在設
>   `OPENAI_CHAT_MODEL=deepseek-chat` 即可,`LLM_MODEL` 不用動;
>   非預設 `LLM_MODEL` 仍最優先,未設時 openai 槽位退回
>   `LLM_MODEL`（行為不變）。
> * **鏈序可調**（v0.9.18.0）:`LLM_PROVIDER_CHAIN` 重排
>   `_PROVIDER_ORDER`（primary 選擇 + failover 順序;`LLM_PROVIDER`
>   覆寫仍最優先）;解析器在零副作用模組 `src/provider_chain.py`,
>   規則:無效 token skip+警告、重複取第一、部分列表補齊（優先序
>   而非剔除）。詳見更新日誌 v0.9.18.0 / v0.9.19.0 條目。
>
> **實戰範例（本機 live .env，三層鏈序）**:
> ```env
> LLM_PROVIDER=
> LLM_PROVIDER_CHAIN=openrouter,ollama,openai-compat
> ```
> → primary = openrouter（z-ai/glm-5.3-flash）;failover 依序
> ollama cloud（glm-5.3-flash:cloud）→ 自架 local 端點
> `openai-compat`（111.243.86.30:11434/v1 + qwen3.6-35b）。
> `LLM_PROVIDER` 留空讓鏈全權決定（第一個已設 key 者為 primary）。
>
> **v0.9.64.0 更新**（LLM dispatch 之外，MCP 工具面已大幅成長）:
> in-process LocalHosts 已達 **21 個**（含 Meta），stdio mirrors
> **19 個**；最新擴充為**中華電信 hicloud HWS CaaS/CVPC 雲服務**
> （`hicloud-local`，8 工具：VM 清單/範本/規格 + 建立/開機/關機/重啟/
> 刪除；官方簽章演算法以文件範例逐字鎖定；mock-first 兩道閘）。
> 完整工具面見上方「MCP Tools 總表」；各版本細節見更新日誌。

**配套**:

* `pyproject.toml` 0.8.8 → 0.8.9
* `.env.example` 新增 6 個 v0.8.9+ 區塊（OpenRouter /
  SiliconFlow / LLM\_PROVIDER）
* `Config.__repr__` API-key 隱藏清單擴充至 5 把 key
* `tests/test_scheduler_local_host.py` 同步至 v0.8.7+ 的
  10 tools（v0.8.7 多了 `get_scheduled_task_status` +
  `list_scheduled_task_runs`,原本寫死 8 的 assertion 已更新）

無 regression：v0.8.8 baseline 325 → v0.8.9 314 passed in
25.75s（涵蓋 14 個相關檔案）。



### v0.8.2（MCP 編輯器三項增強 + chat MCP e2e 驗證腳本）

驗證 chat **真的**能用 MCP（不靠 LLM,純 mock 的 tool-call loop +
真實 stdio MCP server）並把 GUI MCP 編輯器從「表單式 editor」升級
成「表單 + raw JSON + Tool 總覽 + 測試」三合一。

* **`_smoke_chat_mcp.py` — chat MCP 端對端驗證腳本**
  證明 `Agent.ask()` 的 tool-calling loop 確實走完整路徑:
  1. `McpManager.from_config` 起 stdio MCP server
  2. `list_tools()` 載入完整 inventory
  3. Mock LLM 發 `tool_calls` → manager dispatch → 結果回灌成
     `role:tool` message → 下一次 LLM 呼叫看到
  4. allowlist 安全設計在 chat 路徑仍生效
* **GUI MCP 編輯器 — 三項新功能**
  * **每個 server 卡片加「🧪 測試」按鈕** —
    呼叫 `get_mcp_server_logs(server_name)` 顯示
    `connected / disconnected` 狀態 + 該 server 提供的 tool 名單,
    不重啟也不存檔,純診斷用。
  * **「📝 Raw JSON」toggle — 整個 `config/mcp.json` 直接編輯**
    `<textarea>` 內 monospace 編輯整份檔案,
    儲存走既有 `save_mcp_config`(完整 validation + atomic write +
    hot-reload),支援「🎨 格式化」+ 「取消」。對批次改
    `_comment`、複製 JSON、快速貼範例特別好用。
  * **「📋 Tool 總覽」toggle — 列出擴充 MCP 全部提供的功能**
    呼叫 `list_mcp_tools()`,依 `server_name` 分組卡片,
    每張卡片顯示 server 名 + tool 數量(成功/失敗 badge),
    每個 tool 顯示 `name` + `description` + schema 摘要
    (參數名 + 型別,required 用 `*` 標)。
    連線失敗的 server 也會出現在列表尾,以紅色 badge + 失敗原因
    顯示,讓使用者一眼看到「為什麼某個 server 沒生效」。
* **後端 `get_mcp_config` 加 `raw_text` 欄位** — 完整檔案內容,
  讓 raw editor 預設值不是空字串。
* **測試**:
  * `tests/test_gui_api.py` 加 2 個 case:
    `get_mcp_config_returns_raw_text` +
    `get_mcp_config_raw_text_empty_when_no_file`(39 tests, up from 37)
  * `tests/test_gui_e2e.py` 加 1 個 Playwright case:
    `test_mcp_editor_v082_features` 驗證新按鈕/區塊元素存在 + toggle
    行為正確
* **i18n** — 4 種語言(繁中/簡中/英/日)都加 17 個新 key
  (`mcpToggleRaw` / `mcpOverviewToggle` / `mcpTest` / `mcpRawTitle` /
  `mcpOverviewTitle` / ...)
* **改動檔案**:
  * `gui/api.py` — `get_mcp_config` 加 `raw_text` 欄位
  * `gui/assets/index.html` — MCP editor 區塊加 3 個新區塊
  * `gui/assets/app.js` — 新增 `testMcpServer` / `toggleMcpRawEditor` /
    `formatMcpRawJson` / `saveMcpRawJson` / `refreshMcpOverview` /
    `renderToolParams` / `toggleMcpOverview` / `showInfoModal`,
    `bindMcpEditor` 接線 5 個新 button
  * `gui/assets/style.css` — `.mcp-raw-editor` + `.mcp-tools-overview` +
    `.mcp-tools-group` + `.mcp-tool-item` 樣式(沿用 `--bg-2` /
    `--code-bg` / `--accent` 等 theme tokens)
  * `_smoke_chat_mcp.py` — 真實 chat MCP 端對端驗證(用
    `src.mcp_server.telegram_server` 當範例)
  * `tests/test_gui_api.py` — 2 個新 case
  * `tests/test_gui_e2e.py` — 1 個新 case
  * `pyproject.toml` 0.8.1 → 0.8.2

### v0.8.2 patch 1（MetaLocalHost + log noise + failure-handling prompt）

修日誌觀察發現的三個真實問題:

* **`list_mcp_tools` 不再只是 GUI-only — 它現在是 Agent 可呼叫的工具**
  (`src/mcp_client/host.py` 新增 `MetaLocalHost`)。
  之前使用者問「本地有哪些 MCP 功能可以使用?」,Agent 只能
  `web_search("MCP 功能")` 拿網路資料回答(看到 log:
  `tool web_search(...) -> OK: '架构概述 - 模型上下文协议 (MCP'`)。
  現在 Agent 透過 `list_mcp_tools` 直接拿到 live catalog —
  7+ 個 tools 跨 4 個 servers(`meta-local` / `qdrant-local` /
  `web-local` + 任何 `config/mcp.json` 啟用的遠端)。系統 prompt
  也加 rule:問 capability 必須先 call `list_mcp_tools`,不能猜。
  失敗的 server 也會出現在結果的 `failed` 欄位,讓 LLM 知道。
* **修 `get_status` 的 log noise** —
  之前 Qdrant 連不上時,每 30 秒 polling 一次就噴一條 WARNING,
  1 分鐘 2 行,log 整個被刷屏。現在改成「狀態轉換才 log」:
  第一次失敗 WARNING,後續降到 DEBUG,Qdrant 恢復時 INFO 一行
  確認。`_qdrant_last_ok` instance flag 追蹤上一次狀態。
* **系統 prompt 加 failure-handling rule** —
  「如果 tool 一直失敗,**告訴使用者具體錯誤並停止重試**;
  不要在失敗 tool 上 loop 超過一次,改用替代工具或直接 surface
  error」— 解掉使用者 log 裡的「Qdrant 失敗後繼續呼叫
  `list_documents`」的亂試問題。
* **`_smoke_chat_mcp.py` 加 step 5** — 證明 `list_mcp_tools`
  真的能透過 Agent 工具呼叫回傳 10 tools 跨 4 servers
  (3 telegram + 4 qdrant-local + 2 web-local + 1 meta-local)。
* **新增 `tests/test_meta_local_host.py`** — 7 個 unit tests
  覆蓋 MetaLocalHost (advertised tools / 無 manager 行為 /
  失敗 server 處理 / LocalTool 整合 / 透過 McpManager.from_config
  自動註冊)。
* **修改檔案**:
  * `src/mcp_client/host.py` — 新增 `MetaLocalHost` class
  * `src/mcp_client/manager.py` — `from_config` 註冊 MetaLocalHost
    並呼叫 `attach_manager` 注入 mgr 參考
  * `src/mcp_client/__init__.py` — 導出 `MetaLocalHost`
  * `src/agent/agent.py` — `_system_prompt(tools_enabled=True)`
    重寫:加 tool selection rules + failure handling rules
  * `gui/api.py` — `get_status` Qdrant 失敗降 noise:
    `_qdrant_last_ok` state tracking
  * `_smoke_chat_mcp.py` — +1 step (step 5 = list_mcp_tools e2e)
  * `tests/test_meta_local_host.py` — **新增** 7 tests

### v0.8.3（完整 GFM Markdown 渲染 — CLI + GUI）

之前 CLI / GUI 兩邊的 Markdown 渲染各自有缺陷：CLI
`src/ui/md_render.py` 是 zero-dep regex 渲染器，缺**編號清單、
巢狀 bullet、task list、刪除線、引用、水平線、行內連結、圖片、
斜體、反斜線跳脫**；GUI `gui/assets/app.js::md()` 的
DOMPurify `ALLOWED_TAGS` 缺 `input` / `del` / `hr` / `img`，
連結也沒 `rel="noopener"`，且 marked 預設開啟 header id 跳轉。

這版補完兩邊的 GFM 表面：

* **CLI `src/ui/md_render.py`（zero-dep regex）**：
  * **編號清單** `1. 2. 3.` — 自動遞增；巢狀重新從 1 開始
    (`ordered_counter` per-indent 追蹤)
  * **巢狀 unordered bullet** — `•` / `◦` / `▪` 三層 glyph
    區分深度；leading-whitespace 計算 indent depth
  * **Task list** `- [ ]` / `- [x]` — `[x]` 綠色、 `[ ]` 灰色
  * **斜體** `*text*` / `_text_` — 用 `\u2002` (EN SPACE) sentinel
    包夾，因為多數 Windows cmd 不渲染 SGR italic
  * **刪除線** `~~text~~` — SGR `\x1b[9m` + `\u2003` sentinel
  * **粗體** `**text**` — SGR `\x1b[1m` + `\u2003` sentinel（修掉
    v0.2.7 留下的 `\x90m` ESC bug，正確寫成 `\x1b[90m`）
  * **行內連結** `[label](url)` — cyan+underline label
    + plain URL；終端機友善，URL 仍然可讀
  * **行內圖片** `![alt](url)` — `[image: alt](url)`
  * **引用** `> text` — 綠色 `│` 開頭，支援連續段落
  * **水平線** `---` / `***` / `___` — 三字元以上統一渲染成 `─`
  * **反斜線跳脫** `\*` `\_` `` \` `` `\|` `\[` 等 — 用
    Unicode private-use area (U+E000–U+E001) stash 避免後續
    italic/bold regex 把 literal 字元當 markup
  * **Fenced code block** 支援語言標籤 ```python ... ```
  * **新工具**：`strip_ansi()` 公開 helper（test 與 width
    計算會用到）
* **GUI `gui/assets/app.js::md()`**：
  * **DOMPurify `ALLOWED_TAGS` 擴充**：`input` (task checkbox)、
    `del` / `s` (strikethrough)、`hr`、`img`、`figure`/`figcaption`、
    `ins` / `mark` / `small` / `sub` / `sup` / `span`
  * **DOMPurify `ALLOWED_ATTR` 擴充**：`src` / `alt` / `width` /
    `height` / `loading` (image)、`type` / `checked` / `disabled`
    (checkbox)、`class` / `id`
  * **`ALLOWED_URI_REGEXP`** 明確拒絕 `javascript:` URI
  * **`hardenLinks()` 後處理** — 每次 sanitize 後掃 `<a>` 補上
    `target="_blank" rel="noopener noreferrer"`，避免 tabnabbing
  * **marked 選項**：`gfm:true, breaks:true, headerIds:false,
    mangle:false` — 關掉 anchor 跳轉避免 chat log 滾動錯亂
* **新增 `tests/test_md_render.py`** — **27 個 unit tests** 覆蓋
  baseline（heading / bold / code / table / passthrough）+ 新增
  GFM 元素（numbered / nested bullet / task / italic / strike /
  link / blockquote / hr / escape / image / edge cases）。所有
  既有測試零 regression：133 passed, 1 skipped（CLI + GUI API +
  MCP + Telegram + Meta 全部）。
* **修改檔案**：
  * `src/ui/md_render.py` — 重寫 regex + inline pipeline + main
    loop（zero-dep）
  * `tests/test_md_render.py` — **新增** 27 tests
  * `gui/assets/app.js` — `md()` 擴充 + `hardenLinks()` 後處理
  * `pyproject.toml` 0.8.2 → 0.8.3

### v0.8.4（GUI markdown silent failure 修復 — duck-type + vendor fallback）

**這是 v0.8.3 留下的 bug**:使用者報告「chat UI 還是沒有把
`**xxx**` 進行 markdown render」,但 CLI 端 (`cli.py ask`)
的 `render_markdown()` 完全正常,問題只發生在 GUI。

Root cause 在 `gui/assets/app.js::md()` 的引擎偵測邏輯:

```js
// v0.8.3 (broken)
const purifyOK = typeof window.DOMPurify === 'object' && window.DOMPurify.sanitize;
```

DOMPurify 3.x 在 UMD 載入時把自己 expose 成一個 **named
function**(有 `.sanitize` / `.setConfig` / `.removeAttribute`
等 static method),所以 `typeof window.DOMPurify === 'function'`,
不是 `'object'`。`purifyOK` 永遠 false → 走 fallback path → 顯示
raw `**xxx**` 字元 + console 沒有任何錯誤,使用者還以為是
LLM 沒寫 markdown。

`marked.parse` 是 namespace object,所以 `marked` 偵測通過 —
`marked.parse()` 確實跑了,但 DOMPurify 被略過 → 整個 sanitize
被 skip,理論上還有 XSS 風險。

**修法**:

* **Duck-type check**(v0.8.4)—
  `typeof window.marked.parse === 'function'` +
  `typeof window.DOMPurify.sanitize === 'function'`,對 function /
  object 兩種 UMD expose 都通過。
* **Vendor fallback** — `gui/assets/vendor/marked.min.js` (35 KB)
  + `gui/assets/vendor/purify.min.js` (21 KB) **checked into repo**,
  pinned 到 `marked@12.0.2` / `dompurify@3.0.11`。`index.html`
  改成「**local vendor first**,`<script onerror>` 失敗才 fallback
  到 jsdelivr CDN」— 因為有些環境(中國大陸 corp firewall、offline
  模式、CDN 暫時掛了)CDN 完全無法連。
* **Failure diagnostics** — `md()` fallback path 不再 silent:
  一次性 `console.warn('[markdown] unable to load: marked, dompurify.
  Both local vendor/ and CDN fallback failed. ...')` + 嘗試
  `toast()` 通知使用者。Fallback HTML 仍然 escape `<`/`>`/`&`,
  XSS-safe。
* **`_mdStartupCheck()`** — boot 時預先檢查引擎,若缺就主動觸發
  fallback warning path,讓使用者「打開 GUI 就看到錯誤」而不是
  「送第一則訊息才知道」。
* **新增 `tests/test_gui_markdown_e2e.py`** — **11 個 JSDOM-driven
  e2e tests**:用 Node + jsdom + 本地 HTTP server,讓 vendor scripts
  透過 **真實 `<script src=>`** 載入(不是 `eval`,會把 marked 推進
  CommonJS 模式而破壞 UMD attach-to-window 合約)。然後把
  `app.js::md()` + `hardenLinks()` **直接 extract 出來**用同一份
  source code 跑,確認 `**xxx**` 真的變成 `<strong>xxx</strong>`、
  `[t](url)` 拿到 `target=_blank rel=noopener`、還有 fallback
  path 在引擎完全缺席時仍 XSS-safe。

* **重現 v0.8.3 bug 的測試**:如果把 duck-type check 改回
  `typeof === 'object'`,11 個 test 會有 8 個 fail — 證明這個
  fix 是必要的,而且不會 regression。

* **修改檔案**:
  * `gui/assets/index.html` — `<script src="vendor/marked.min.js"
    onerror="...CDN...">` 結構
  * `gui/assets/vendor/marked.min.js` — **新增** vendor file
    (35 KB, marked@12.0.2 from npm registry)
  * `gui/assets/vendor/purify.min.js` — **新增** vendor file
    (21 KB, dompurify@3.0.11 from npm registry)
  * `gui/assets/app.js` — `md()` duck-type fix + fallback warning
    + `_mdStartupCheck()` + boot hook
  * `tests/test_gui_markdown_e2e.py` — **新增** 11 tests
  * `pyproject.toml` 0.8.3 → 0.8.4

### v0.8.5（語系閉環 — LLM 回答語言跟 UI 同步）

使用者回報「UI 選繁體中文,但回答出現簡體中文」。根因:`gui/assets/
app.js` 的 `setLang()` 只換 `I18N` 字典 → UI 字串,**沒有**把語言
傳給 LLM。LLM 收到的 system prompt 是英文指令(預設指向英語),
至於繁簡取決於模型訓練語料分布 — 大陸託管的 inference endpoint
常預設簡體中文。

**修法 — `response_language` 端到端 plumbing**:

* **`Agent._system_prompt()`** 加 `response_language` 參數 — 當
  提供時,在 CITATION RULES **之後**追加 `LANGUAGE DIRECTIVE`
  區塊,內含「回應語言: <人類可讀名>」,並明確說明「檔名、
  套件名、程式碼識別字**不**翻譯」(否則會破壞 citation 完整性)。
* **`Agent.ask()` / `chat_turn()`** 加同名 kwarg,往下傳到
  `_messages_with_user()` 跟 legacy path。預設 `None` — 完全
  preserve v0.8.4 行為(零 directive)。
* **`gui/api.py::chat_turn` / `ask`** 接 `response_language`
  參數,轉發給 agent。
* **`gui/assets/app.js::sendChat()`** — 自動讀 `currentLang`
  變數(已存在),作為第 6 個參數丟給 `api.call('chat_turn', ...)`。
  切換語言後**下一則訊息立刻生效**,不需重啟 GUI。
* **CLI 對齊** — `cli.py ask` / `cli.py chat` 加 `--lang
  zh-TW|zh-CN|en|ja`(預設 None = LLM 自選)。chat REPL 加
  `/lang [code|auto]` 即時切換 + `/lang status` 顯示目前設定。
* **語言代碼 ↔ 人類可讀名稱** map(`zh-TW`→"Traditional
  Chinese" / `zh-CN`→"Simplified Chinese" / `en`→"English" /
  `ja`→"Japanese")— 避免 LLM 只認得 code 不認得全名。

**新增 `tests/test_response_language.py`** — **20 個 unit tests**:
* `TestSystemPromptLanguageDirective` (8 tests)— directive 注入
  / 不注入條件、人類可讀名稱出現、filenames 不被翻譯的
  carve-out、CITATION RULES 在前 LANGUAGE DIRECTIVE 在後的順序保證
* `TestAgentThreadsLanguageToLLM` (3 tests)— mock OpenAI
  client 確認參數真的流到 `chat.completions.create` 的 system
  message,不是只有 function signature
* `TestGuiApiForwardsLanguage` (3 tests)— Api bridge 帶參數給
  Agent,預設 None 不破壞 legacy
* `TestCliLanguageFlag` (3 tests)— `--lang` argparse choice
  constraint 拒絕未知 locale,預設 None

* **端到端驗證** — CLI 也支援,確保 REPL `/lang` 指令在 mid-session
  切換有效。

* **修改檔案**:
  * `src/agent/agent.py` — `_system_prompt` + `_messages_with_user`
    + `ask` + `chat_turn` 全加 `response_language` 參數
  * `gui/api.py` — `chat_turn` / `ask` 加同名 kwarg,log 帶 lang
  * `gui/assets/app.js` — `sendChat` 自動帶 `currentLang`
  * `cli.py` — `--lang` argparse flag (ask + chat) + `/lang` REPL 指令
  * `tests/test_response_language.py` — **新增** 20 tests
  * `pyproject.toml` 0.8.4 → 0.8.5
  * `gui/assets/index.html` 版本字串 v0.8.4 → v0.8.5

### v0.8.6（Scheduler MCP — LLM 可對話式管理排程）

v0.8.0 加入了 Schedules tab + cron daemon,但 LLM **無法**直接操控
排程 — 使用者必須手動進 GUI / CLI 設定。v0.8.6 把整個 `ScheduleManager`
包成 8 個 MCP tools,讓對話直接驅動:

* **「幫我排程每天早上 9 點檢查專案文件」** → 自動呼叫 `add_scheduled_task`
* **「暫停 morning-check」** → `disable_scheduled_task("morning-check")`
* **「為什麼 daily-summary 失敗了?」** → `get_scheduled_task_errors(...)`
* **「立刻跑 weekly-review」** → `run_scheduled_task_now("weekly-review")`

**新增 MCP surface（in-process + stdio 兩個版本）**:

| Tool | Purpose |
|------|---------|
| `add_scheduled_task` | 建立排程（cron / interval / once_at） |
| `delete_scheduled_task` | 移除（含 id 或 name 兩種） |
| `list_scheduled_tasks` | 表格化列表 + status 標籤 |
| `get_scheduled_task` | 完整細節（**含 `last_error`**） |
| `enable_scheduled_task` / `disable_scheduled_task` | 暫停/恢復 |
| `run_scheduled_task_now` | 強制觸發 |
| `get_scheduled_task_errors` | 查詢 history.jsonl 的失敗紀錄 |

**兩個傳輸路徑,同一份 contract**:

* `src/mcp_client/host.py::SchedulerLocalHost` — **in-process**,
  預設即時可用,LLM 經由 `McpManager.from_config(include_local_hosts=True)`
  自動看見,零延遲、零 subprocess。CLI / GUI 用戶**完全不需要額外設定**。
* `src/mcp_server/scheduler_server.py::SchedulerMCPServer` —
  stdio MCP server,給外部 client（Claude Desktop / Cline 等）驅動。
  註冊方式:`config/mcp.json` 加 stdio entry。

**Schedule dataclass 擴充（向後相容）**:

* `Schedule.last_error: str | None` — 最近一次 fire 的錯誤 repr
* `Schedule.last_error_at: str | None` — 對應 ISO-8601 ts
* `Schedule.from_dict` 透過 `__dataclass_fields__` 過濾,**v0.8.5 的
  JSON 檔可直接讀取**,不會爆 `TypeError: unexpected keyword
  argument`。

**ScheduleManager 新方法**:

* `set_enabled(sid, enabled)` — toggle with auto `next_fire_at` 重新計算
* `clear_error(sid)` / `record_error(sid, exc)` — error lifecycle
* `find_by_name(name)` — LLM 可以直接用「morning-check」而不是 UUID

**`Scheduler.tick_once()` 失敗追蹤（v0.8.6 修了一個安靜的 bug）**:

v0.8.5: `on_fire` 例外只 `logger.exception`,**daemon 繼續轉但排程的
錯誤永遠消失** — LLM 無法回答「為什麼 X 失敗了」。

v0.8.6: 例外時呼叫 `mgr.record_error(sid, e)`,把錯誤字串與時間戳
寫回 schedule 本身 + history.jsonl。daemon 仍然存活,daemon 仍然
`logger.exception`,只是錯誤**也**被持久化。

`make_agent_on_fire` 同步加強: `agent.chat_turn()` 自己拋例外時
（Qdrant down / OpenAI 5xx / network glitch）也走 record_error 路徑,
不再被靜默吞掉。

**System prompt 排程觸發規則**（`Agent._system_prompt(tools_enabled=True)`）:

新增區塊告訴 LLM 何時呼叫每個排程工具:

```
排程工具觸發規則:
 - 「排程」「every day at 9am」「明天下午 3 點提醒我」「cron」…
   → add_scheduled_task (schedule_type: cron/interval_seconds/once_at)
 - 「我有哪些排程」「列出排程」→ list_scheduled_tasks
 - 「停掉 X」「暫停 X」→ disable_scheduled_task / enable_scheduled_task
 - 「為什麼 X 失敗」「上次錯誤」→ get_scheduled_task_errors
 - 「立刻跑 X」→ run_scheduled_task_now
```

**改動清單**:

* `src/agent/scheduler.py` — Schedule +18 fields（last_error /
  last_error_at）; ScheduleManager 4 個新 method;
  tick_once + make_agent_on_fire 例外路徑
* `src/mcp_server/scheduler_server.py` — **新檔** stdio MCP server
* `src/mcp_client/host.py` — `SchedulerLocalHost` 永遠 in-process,
  與 stdio 共享 validation / serialization 邏輯
* `src/mcp_client/manager.py` — `from_config(include_local_hosts=True)`
  自動註冊 SchedulerLocalHost
* `src/agent/agent.py` — `_system_prompt(tools_enabled=True)`
  新增排程觸發規則區塊
* `tests/test_scheduler_server.py` — **新檔** 47 tests（模仿
  test_telegram_server.py pattern）
* `tests/test_scheduler_local_host.py` — **新檔** 15 tests
* `tests/test_scheduler_v086.py` — **新檔** 13 tests（last_error 、
  set_enabled、tick_once 失敗追蹤）
* `tests/test_system_prompt_scheduler.py` — **新檔** 6 tests
  (system prompt 包含觸發規則)
* `pyproject.toml` 0.8.5 → 0.8.6

**測試結果**: 282 passed, 1 skipped (v0.8.5 baseline 164 → v0.8.6
+118 tests)。
### v0.8.6.1（修 LocalTool argument coercion 安靜失敗）

v0.8.6 上線後第一個 user log（2026-08-31）顯示 `add_scheduled_task`
每次都被 `AttributeError: 'str' object has no attribute 'get'` 炸掉。
根本原因：我們 emit 的 schema 有單一 `args: object` 屬性，部分 LLM
會把整個 call 包成 `{"args": "<json>"}` / `{"args": {...}}`，然後
`LocalTool.__call__` 直接 `arguments.items()` 展開成 kwargs，把 `args`
這個 key 當成 handler 的位置參數，傳成 str → `_validate_add_args("...")`
`.get("prompt")` 就炸。

**修法**（`src/mcp_client/host.py`）：

* 新 helper `_coerce_arguments(payload)`：把 `None` / bare JSON
  string / `{"args": {...}}` / `{"kwargs": ...}` / `{"params": ...}`
  / 單元素 list / `__dict__`-bearing object 通通 normalise 成 flat
  dict；無法辨識的 shape 退回 `{}`，讓 LLM 收到乾淨的 error 而不是
  Python traceback。
* `LocalTool._call_args(kwargs)`：introspect handler signature，如果
  是 `(self, args: dict)`（所有 host 都用這個 convention），把整個
  normalized dict 當單一位置參數傳，否則維持 kwargs-spread。
  `WebSearchLocalHost._search(query, max_results=5)` 等 kwargs-spread
  handler 不受影響。

**測試**：新檔 `tests/test_local_tool_argument_coercion.py` 34 tests
（`TestCoerceArguments` 18 unit + `TestLocalToolNormalisation` 5 +
`TestSchedulerLocalHostWrapperTolerance` 10 — 把所有 8 個 scheduler
tools 都用 wrapper shape 跑一遍 + `TestWebSearchHostUnaffected` 1）。
v0.8.6 baseline 282 → 305 passed, 2 skipped。

### v0.8.6.2（修排程 fire 後 LLM 沒呼叫 Telegram tools）

v0.8.6 用戶 log 顯示 daemon 確實 fire 了排程，但 `answer=0 chars`
（`22:08:06 scheduler: fired ... answer=0 chars`）—— LLM 看到
scheduled prompt「send hello to chat <YOUR_TELEGRAM_CHAT_ID> via telegram」，但
**完全不知道**該呼叫 `send_telegram_message`，因為 `_system_prompt`
從未提過 Telegram trigger words。

**修法**（`src/agent/agent.py`）：在 `_system_prompt(tools_enabled=True)`
的 Schedule trigger block 之後，加 **Telegram / external messaging
(v0.8.6.2+)** trigger block：

* 中文 + 英文 + 羅馬拼音觸發詞都列上（`用 telegram 發` / `send via
  telegram` / `傳到 telegram` / `notify me on telegram` /
  `發訊息給 chat_id`）
* 標明 `TELEGRAM_ALLOWED_CHAT_IDS` 安全模型（allowlist fail-closed）
* `list_allowed_chats` 作為「不知道自己的 chat_id」時的 helper
* **「Telegram + Schedule composition（常見陷阱）」**：專門一段點名
  「schedule + Telegram」情境，明確告訴 LLM「scheduled prompt 提到
  telegram 時必須主動呼叫 telegram tool，不要只回純文字」

**測試**：新檔 `tests/test_system_prompt_telegram.py` 8 tests
（每個 clause 都驗證 + legacy `tools_enabled=False` 不會洩漏 +
`response_language` 指令共存 + 不擠掉既有 Schedule trigger）。
v0.8.6.1 baseline 305 → 313 passed, 2 skipped。

### v0.8.6.3（修 LLM 建排程但 daemon 不啟動 → 永遠不 fire）

v0.8.6 / .6.1 / .6.2 修完後，用戶新回報：「使用 llm 建立 排程，建立
後 排程 不會按時執行」。根本原因：`Agent.scheduler` 是 lazy property，
**只有當使用者手動呼叫** `agent.start_scheduler()`（GUI 按鈕 / CLI
`schedules daemon`）才會建構並啟動 daemon thread。LLM 透過 chat 呼叫
`add_scheduled_task` 寫入 JSON 後，**完全沒有路徑觸發 daemon 啟動**
—— schedule 寫進磁碟，`next_fire_at` 已經過期，但 daemon 沒人啟動，
永遠不 fire。

**修法**（3 檔串接單一資料流）：

1. `src/mcp_client/host.py::SchedulerLocalHost`：加 `_auto_start:
   Callable | None` + `attach_auto_start(cb)` setter + `attach_manager`
   從傳入的 `McpManager` 自動 capture callback。`_add` 寫入成功後
   呼叫 `self._auto_start()`，try/except 包起來防止 callback 例外
   污染成功回應（schedule 已經在磁碟，使用者可以 GUI 手動啟動
   daemon 救回）。
2. `src/mcp_client/manager.py::McpManager`：加 `auto_start_callback`
   屬性（Agent wire 它）+ `_scheduler_local_host` reference
   （`from_config` 內存 reference）+ `set_auto_start_callback(cb)`
   canonical setter（設定 callback 並推到 host，**克服 `from_config`
   跟 Agent lazy property 的 race condition**）。
3. `src/agent/agent.py::Agent.mcp_manager` lazy property：第一次建構
   manager 後呼叫 `mgr.set_auto_start_callback(self._auto_start_scheduler_daemon)`。
   `_auto_start_scheduler_daemon()` 內呼叫 `self.start_scheduler()`
   （本身已 idempotent），try/except 包起來防止任何 exception 漏出。

**特性**：

* **Idempotent**：`Scheduler.start()` 已判斷 thread 是否 alive，多次
  auto-start 不會起多個 thread
* **Lazy**：沒人排程就不浪費一個 thread；只有第一次
  `add_scheduled_task` 成功才建構並啟動 daemon
* **CLI 不受影響**：`cli.py schedules daemon` 子命令仍然存在，
  手動 daemon 模式照舊；auto-start 是給 GUI / chat 路徑的便利
* **Best-effort**：callback 內任何 exception 都不會破壞 `_add` 的
  成功回應

**測試**：新檔 `tests/test_scheduler_daemon_autostart.py` 16 tests
（`TestSchedulerLocalHostAutoStart` 7 — host 的 callback �發 /
驗證失敗不觸發 / callback 例外被吞 / `attach_manager(McpManager)`
自動 capture；`TestMcpManagerAutoStartSetter` 5 — setter 正確存值
並推到 host / `None` 不呼叫 / 例外被吞 / `from_config` 存 reference；
`TestAgentAutoStartWiring` 4 — 端對端：Agent lazy property 設 callback
/ callback 啟動 daemon / idempotent / callback 內 exception 被吞）。
v0.8.6.2 baseline 313 → 329 passed, 2 skipped。



### v0.8.1（Telegram Bot gateway as 2nd MCP server）

新增第二個 stdio MCP server `src/mcp_server/telegram_server.py`，
讓 LLM Agent 透過 MCP tools 把訊息推送到**預先核准**的 Telegram 聊天。

* **3 個 MCP tools**：
  * `send_telegram_message(chat_id, text)` — 純文字
  * `send_telegram_markdown(chat_id, text)` — Telegram MarkdownV2 渲染
  * `list_allowed_chats()` — 列出 bot 允許寫入的 chat IDs
* **安全設計 — allowlist fail-closed**：
  * `TELEGRAM_ALLOWED_CHAT_IDS` 是 comma-separated 清單，只有這些 chat_id
    可以收到訊息。其他 chat_id 在 **HTTP 送出之前** 就被拒絕，回傳
    `Error: chat_id <X> is not in the allowlist`。
  * env 未設 → 空 allowlist → 全部拒絕（fail-closed，預設安全）
  * `chat_id=True` 等會被 `isinstance(int)` 誤判的值被特別排除
  * Token 只走 env（`TELEGRAM_BOT_TOKEN`），從不寫進 tool output
* **零外部依賴**：用 stdlib `urllib.request` 呼叫 Bot API，避免引入
  `python-telegram-bot` 等大依賴；`pyproject.toml` 保持精簡
* **架構**：與既有 Qdrant server (`src/mcp_server/server.py`) 平行，
  同樣是 stdio MCP server；可被外部 MCP client (Claude Desktop /
  Cline / Cursor) 直接 spawn，也可透過 `McpManager.from_config()` 註冊
  到本專案自己的 Agent tool fleet（搭配 `config/mcp.json` 的 stdio
  entry + `env` 區塊注入 token / allowlist）。
* **訊息長度上限 4096**（Telegram `sendMessage` 硬限制），超長直接拒絕
* **測試覆蓋**：32 個 pytest cases (`tests/test_telegram_server.py`)，加上
  `_smoke_telegram_mcp.py` 真實 subprocess protocol smoke — 啟動 server、
  MCP handshake、`list_tools` 驗證 3 tools、`list_allowed_chats` 回傳、
  非 allowlist chat 被拒絕
* **檔案**：
  * `src/mcp_server/telegram_server.py` — server 主模組（~280 行）
  * `src/mcp_server/__init__.py` — 加 `TelegramMCPServer` export
  * `tests/test_telegram_server.py` — 32 unit tests
  * `_smoke_telegram_mcp.py` — 真實 MCP protocol e2e（手動跑）
  * `.env.example` — 加 `TELEGRAM_BOT_TOKEN=` / `TELEGRAM_ALLOWED_CHAT_IDS=`
  * `config/mcp.json.example` — 加可啟用的 telegram stdio entry
  * `pyproject.toml` 0.8.0 → 0.8.1

使用方式：

```bash
# 1. .env 設定
TELEGRAM_BOT_TOKEN=123456:ABCDEF...           # 從 @BotFather 拿
TELEGRAM_ALLOWED_CHAT_IDS=11111111,22222222   # 從 @userinfobot 拿

# 2. 直接以 stdio MCP server 跑（給 Claude Desktop / Cline 用）
python -m src.mcp_server.telegram_server

# 3. 或加入 config/mcp.json 讓本專案 Agent 自己用
# （已附在 config/mcp.json.example 第 61 行）

# 4. 驗證
python -m pytest -q tests/test_telegram_server.py
python _smoke_telegram_mcp.py
```

### v0.8.0（Tool calling + MCP host & client）

兩個長期缺失的能力終於補上:Agent 採用 LLM tool-calling 機制,
以及完整的 MCP host/client 架構 (支援本地端 Stdio Transport 和遠端
HTTP with SSE/Streamable Transport) 用以接取第三方 MCP 服務。

**Tool calling loop**(`src/agent/agent.py`)

* `Agent.ask()` / `chat_turn()` 走標準 OpenAI `tools=` / `tool_calls`
  protocol:
  1. 把工具清單送給 LLM
  2. LLM 發出 `tool_calls` → 透過 `McpManager` 執行對應工具
  3. 把工具結果 append 回 messages,再呼叫 LLM
  4. 直到 LLM 出 plain answer 或達 `max_tool_iterations`
     （v0.8.0 時預設 6;**v0.9.14.1 起預設 20**,可用
     `MAX_TOOL_ITERATIONS` env 覆寫,見更新日誌 v0.9.14.1）
* 單一工具錯誤以 `McpCallResult(is_error=True, ...)` 回傳,把錯誤
  string 喂回 LLM,讓模型決定 retry/pivot,**不**打斷 REPL。
* 向後相容:傳 `use_tools=False` 退回 v0.2.x 的 one-shot RAG 路徑。

**MCP client / host 架構**(`src/mcp_client/`)

```
src/mcp_client/
├── base.py        # McpClient 抽象介面 + McpTool / McpCallResult
├── stdio.py       # StdioMcpClient —— subprocess 本地端
├── http_sse.py    # HttpSseMcpClient —— Streamable HTTP + 舊版 SSE
├── host.py        # LocalTool + QdrantLocalHost + WebSearchLocalHost
└── manager.py     # McpManager —— 統一調度 + from_config() loader
```

支援 3 種 MCP transport (官方 `mcp` SDK 提供的全部):

* **Stdio**(本地):spawn child process。範例:
  ```json
  {"name":"qdrant-external",
   "transport":"stdio",
   "command":["python","-m","src.mcp_server.server"]}
  ```
* **Streamable HTTP**(遠端,新):單一 HTTP endpoint,JSON 或 SSE 雙向。
  URL 範例:`https://mcp.notion.com/v1/mcp`。headers 支援 `${ENV_VAR}`
  展開。
* **SSE**(遠端,舊):MCP 2024-11-05 spec。Notion / Linear 等早期
  server 仍走這條。

**Local hosts**(永遠 on,即使 `config/mcp.json` 缺席)

* `QdrantLocalHost`:in-process 包 `Retriever` + `QdrantStore`,
  給 LLM `search_documents` / `list_documents` /
  `get_document_stats` / `remove_documents` 4 把工具。**不會**
  spawn stdio subprocess(直接呼叫現成 store)。
* `WebSearchLocalHost`:`web_search` / `web_fetch`。

**CLI 用法**

```bash
# 預設:讀取 config/mcp.json + local hosts + tool calling on
qdrant-agent.bat ask "用 web_search 查 2024 生成式 AI 新聞,前 3 條"

# 退回 legacy (v0.7.x 行為,without tool calling)
qdrant-agent.bat ask --no-tools "..."
qdrant-agent.bat ask --mcp-config "" --no-tools "..."

# chat REPL per-turn 控制
qdrant-agent.bat chat
... you> /tools on   # 啟動 tool calling
... you> /tools list # 列出可用 tool surface
... you> /tools off  # 退回 legacy
... you> /tools status
```

啟動時 CLI 會印一行 stderr banner,告知目前生效的工具清單:

```
[tools] active tool surface: qdrant-local(4), web-local(2)
[tools] WARN <server>: <reason>     # 若某 MCP server 連不上
```

**GUI bridge**

* `gui/api.py` 加 `list_mcp_tools()` —— 回傳可用工具完整 metadata
  給前端 display。
* `get_status()` 多三個 fields:`mcp_tool_count`、`mcp_servers`、
  `mcp_failed`。

**MCP 設備編輯器 (Admin tab)**

GUI 的「🛠 Qdrant-RAG 管理」tab 內嵌一個 fleet 編輯器,可直接在
UI 上新增 / 編輯 / 刪除 `config/mcp.json` 的 server,儲存後**立即
熱重載** McpManager,不需要重啟 Agent:

* 三種 transport 各自的欄位 form(stdio:command / env / cwd;
  streamable_http + sse:url / headers)。
* Modal-style 編輯器,`${ENV_VAR}` 替換在連線時執行,不用重啟 Agent。
* 每張 server 卡片:✅/⚠ 設定有效性 badge、JSON 預覽、按鈕
  「重連」(對單一 server)、「編輯」、「刪除」。
* 「Save & reload」即使沒改也會重 rebuild manager —— 適合手動
  改 env vars 後想立刻套用。
* 寫入磁碟採 **atomic write** (`tempfile.mkstemp` + `os.replace`)，
  避免中途斷電造成半毀 config。
* API surface:`get_mcp_config` / `save_mcp_config` /
  `restart_mcp_server` / `get_mcp_server_logs`(皆 catch all
  exceptions,回傳 `{"error": "..."}` dict)。

**測試**

* `tests/test_mcp_client.py` (新增 26 tests,全部 mock) 覆蓋
  `McpManager` / `LocalTool` / 3 種 client 介面 / tool calling loop。
* `tests/test_gui_api.py` 新增 11 tests 覆蓋 MCP 編輯器 API:
  驗證 / 原子寫檔 / strip `_comment` / hot-reload 流程 / 重連。
* 全部 tests:102 passed, 2 skipped(加上 37 個
  `test_scheduler.py` 排程測試)。

**`config/mcp.json.example`**

隨 binary 附 worked example (stdio + streamable_http + legacy SSE)。
正式使用:copy `mcp.json.example` → `mcp.json`,編輯需要的 server,
不需要的就保持 `_comment` 不刪也行(MCP 不認底線開頭 key)。

`pyproject.toml` 0.7.0 → 0.8.0。

### v0.8.0（排程 + Schedules tab）

`src/agent/scheduler.py` 提供 cron / interval / one-shot 三種
trigger,daemon thread 在 Agent 內常駐,每次 fire 跑完整
`Agent.chat_turn` 路徑(`use_tools=True` + 可選 `use_web_search`),
所以 MCP server / Web Search / RAG retrieval 全部可用。

**3 種 schedule 類型**

| Type | 用法 | 範例 |
|---|---|---|
| `interval_seconds` | 每 N 秒 | 每 60 / 3600 / 86400 秒 = 每分 / 每時 / 每日 |
| `cron` | 5-field cron (分 時 日 月 週) | `0 9 * * *` = 每天 9 點,`*/15 * * * *` = 每 15 分,`0 0 * * 1` = 每週一午夜 |
| `once_at` | ISO datetime | `2026-12-31T23:59:00` |

支援 `*` `*/n` `a-b` `a-b/n` `a,b,c`(沒月名/日名 — 簡化版)。

**觸發後流程**

1. 建/重用 dedicated session(`sched-<id>-<fire-ts>`),append
   `{user, assistant}` 訊息。
2. `Agent.chat_turn(prompt, history=...)` 跑,**可**走 tool-calling
   loop → 真的 call `web_search` / `mcp_server` 之類。
3. 更新 schedule lifecycle:`last_fire_at`、`fire_count`、
   `last_session_id`、下一次 `next_fire_at`。
4. 寫一筆到 `schedules/<id>.history.jsonl`(ring buffer 50 筆)。

**CLI** (`cli.py schedules <verb>`)

```bash
# 列出
qdrant-agent.bat schedules list

# 新增 (--interval 也可用 --interval; --type 接受 interval 別名)
qdrant-agent.bat schedules add \
    --name "每小時新聞摘要" \
    --prompt "用 web_search 查最近一小時 AI 新聞,前 5 條" \
    --type interval --interval 3600 --web-search

# 跑 daemon (foreground; Ctrl-C 停)
qdrant-agent.bat schedules daemon

# 一次性觸發 (不用等時間到)
qdrant-agent.bat schedules run-now <id>

# 看最近 N 筆 fire 紀錄
qdrant-agent.bat schedules history <id> --limit 20

# 啟用 / 停用 / 刪除
qdrant-agent.bat schedules enable <id>
qdrant-agent.bat schedules disable <id>
qdrant-agent.bat schedules remove <id>
```

**GUI Schedules tab**(在 Sessions tab 後)

* 表格列出 name / type / next_fire / fires / enabled / actions。
* 「+ Add schedule」打開 modal,3 種 type 各有獨立欄位
  (interval / cron / once_at),3 個 flags:
  use_web_search / use_tools / enabled。
* 每行:▶ 立即觸發 / 啟用/停用 / 編輯 / 刪除。
* 「Enabled only」篩選 + 「daemon running/stopped」狀態列。
* 每次跑完自動 refresh,toast 顯示「排程 xxx 已觸發,session=...」。
* 對應 Sessions tab:每次 fire 會建立/延續一個 session,
  在那邊可以看完整對話紀錄。

**GUI API methods**

`list_schedules` / `add_schedule` / `update_schedule` /
`delete_schedule` / `run_schedule_now` / `get_schedule_history` /
`start_scheduler` / `stop_scheduler` 皆 catch all exceptions
回傳 `{"error": "..."}`。

**Daemon 設計**

* Lazy — `Agent.scheduler` 第一次存取才建,daemon thread 沒啟動
  時 `add_schedule` 仍可用(下一次起 Agent 才會跑)。
* Daemon 每秒 tick,看 `next_fire_at` < now 就 fire。
* Tick 內的 on_fire 失敗(例如 LLM API 暫時不通)**不**打斷
  daemon — 例外被吃 + log,下次 tick 再試。
* `Agent.shutdown()` 會 `Scheduler.stop(timeout=2.0)` 收尾。

**儲存**

* Schedule 定義:`~/.qdrant-rag-agent/schedules/<id>.json`
  (atomic write via tmp + os.replace)。
* 觸發紀錄:`schedules/<id>.history.jsonl`(JSON Lines,
  最近 50 筆 ring buffer)。
* Session:`~/.qdrant-rag-agent/sessions/sched-<id>-<ts>.json`。

**測試**

`tests/test_scheduler.py` (新增 37 tests) 覆蓋:
`_parse_field` 各語法 / `cron_matches` / `next_cron_fire` /
`Schedule.compute_next_fire`(3 種 type + disable + unknown)/
`ScheduleManager` CRUD + 原子寫 + path traversal reject +
history ring buffer + recompute next_fire /
`Scheduler.tick_once`(fire / disabled skip / one-shot auto-disable
/ on_fire exception 處理 / lifecycle)/
`make_agent_on_fire`(寫 session + 寫 history + once_at auto-disable)/
GUI bridge `_validate_schedule` / `add_schedule` / `list_schedules` /
`delete_schedule` / `get_schedule_history`。

### v0.7.0（Per-document 移除）

GUI / CLI 之前只能整個 collection 砍掉(`delete-collection`)。
v0.7.0 起可以**選擇性**移除已索引的檔案 — 透過檔名(basename),
不用給絕對路徑。

新 CLI sub-command:

```bash
# 互動確認
qdrant-agent.bat remove-files "report.pdf" "notes.txt"
# 跳過確認（適合 batch / script）
qdrant-agent.bat remove-files --yes ghost.pdf
# 即使檔案不在 collection 也只是提示 "missing",不 abort
```

GUI 改進:
- Stats tab「已索引文件」清單改為 **checkboxes + 每列 🗑 按鈕**
- 跨多選後按上方「🗑 Remove selected (3)」批次刪
- 刪除前彈 confirm 對話框(單檔 / 多檔分別文案)
- 刪完立刻 refresh,toast 顯示「已移除 N 檔共 M chunks」
- 若有 typo,toast 附加「(N filenames not in collection — typo?)」

底層新增 API (`src/vector_store/qdrant_client.py`):

- `delete_by_filename(filename) -> int` — **user-facing** 路徑,按
  `filename` (basename) 過濾。內部用 `client.count()` 做 pre-flight
  避免空 round-trip,確認有東西才 `client.delete()` 帶
  `FilterSelector(key="filename", ...)`.
- `delete_by_filenames(filenames) -> dict` — 批次版,失敗
  short-circuit。

API bridge `gui/api.py` 新增 `remove_documents(filenames) -> dict`:

- 去重複、空字串、non-string 元素
- 回傳 `{removed: {filename: count}, missing: [name, ...]}`
  給前端區分 "刪到的" vs "打錯字沒找到"
- 任何刪除後會 `invalidate_list_cache()`(已內建於 `_delete_by_field`)

測試:`tests/test_gui_api.py` 加 8 個新 test 涵蓋:

- 正確的 filter 構造 (`FieldCondition key="filename"`)
- count=0 不打 delete
- count() 失敗時仍 attempt delete,回傳 -1
- 空 filename 短路
- API dedup 跟 missing/removed split
- empty list / non-string filter / qdrant down → error envelope

`delete_by_source(source)` 仍存在,in pipeline 重 ingest 時內部用。

`pyproject.toml` 0.6.1 → 0.7.0。

### v0.6.1（GUI Stats 卡死修）

GUI 切到 **統計 (Stats) tab** 時,所有卡片顯示 `—`、
已索引文件清單永遠是空的。Root cause:

`QdrantStore.list_documents()` 用 `client.scroll(limit=1000)`
loop 抓 distinct filename。本機的 167k-pt collection 需要 **168
次 scroll**、每次 ship 1000 個完整 payload (含 chunk text),
總計好幾秒到好幾分鐘 — 在 GUI thread 上跑就會 stall。
回傳結果比 IPC timeout 慢,所以 JS 端收到的是 `{"error":"..."}`,
欄位全部 fallback 到 `—`。

修法:

- **`list_documents` 換成 `query_points_groups(group_by='filename',
  group_size=1)`** — Qdrant server 走 payload index,
  O(distinct values) 不是 O(points)。
  167k-pt / 174 files 的 collection 從幾分鐘 → **3 秒**。
- **In-process TTL cache (60 秒)** — 之後切 tab 都是 0 ms。
  Pipeline `upsert` / `delete_by_source` 後會 `invalidate_list_cache()`,
  下次 `Stats` tab Refresh 立刻看到新檔名。
- **`get_stats` 也加 TTL cache** — `client.get_collection()` 在
  167k-pt collection 上要 ~2 秒,Qdrant 在 background indexing,
  不是 client bug。Cache 讓 user 切 tab 完全 0 延遲。
- **`api.get_stats` 加 try/except 包 list_documents** — 即使
  Qdrant down,卡片仍顯示 collection / num_entities / dim,只
  是文件清單寫「無法列出: <error>」紅字。

GUI 4 個 locale 加 `docsError` key,style 加 `.doc-list-error`。
CLI `stats` 不受影響 (它沒用 list_documents)。

`tests/test_gui_api.py` 加 4 個新測試:
- `test_get_stats_passes_through_collection_stats`
- `test_get_stats_survives_list_documents_failure`
- `test_qdrant_store_list_documents_uses_groups`
- `test_qdrant_store_upsert_invalidates_cache`

`pyproject.toml` 0.6.0 → 0.6.1。

### v0.6.0（Embedding 持久快取 + GUI cache 管理）

`Embedder` 加了 **SQLite 磁碟快取** 層,在現有的 in-process LRU 上。
跨 process / 重啟存活,所以重複 ingest 同一份文件、
重複問同一個 chat 問題都會直接 hit cache,不打 embedding API。

新功能：
- `src/ingest/disk_cache.py` — `DiskEmbeddingCache`:
  - SHA-1(`backend|model|text`) cache key,backend 切換不會碰撞
  - WAL mode + autocommit,多 process 安全(`check_same_thread=False` + busy timeout)
  - `n_hits` 累計 + `last_used_at` 維護 → hit ratio 統計 + LRU eviction
  - Vec 序列化成 float32 little-endian blob,dim 安全
  - 三層 cache:`LRU → SQLite → backend API`
- `src/ingest/embedder.py` — 兩層 cache 整合
  - `embed()` / `embed_batch()` 都在打後端前先查 SQLite
  - batched 後端 call 完會 `_persist_batch()` 把結果寫進 SQLite
  - `backend_info()` 加 `disk_cache` 區塊(`/n_entries`, `/hit_ratio`, `/path`)
  - `cache_clear_all()` 一次清掉兩層
- `gui/api.py` 加 2 個 methods:`backend_info`, `cache_clear_all`
- `gui/assets/` :
  - Admin tab 的 **Embedding 卡片** 從 `backend_info` 即時拉資料
    (顯示 backend / dim / LRU 條數 / disk 條數 + hit ratio %)
  - 新 **Embedding cache** 區(緊鄰 Danger zone),有 🧹 Clear 按鈕
    (confirm 對話框防誤觸)
  - 4 個 locale 都加 6 個 i18n key:`cacheZone`, `clearCache`,
    `clearCacheHint`, `confirmClearCache`, `cacheCleared`,
    `clearCacheTitle`
- `.env.example`:`EMBEDDING_CACHE` / `EMBEDDING_CACHE_MAX_ENTRIES`
- `tests/test_gui_api.py` 加 5 個 test:
  - `test_disk_cache_roundtrip` — put/get round-trip
  - `test_disk_cache_backend_isolation` — backend 不碰撞
  - `test_disk_cache_hit_ratio_tracking` — n_hits 累計正確
  - `test_cache_clear_all_returns_counts` — API surface
  - `test_backend_info_passes_disk_cache_section_through` — 傳遞

`pyproject.toml` 0.5.0 → 0.6.0。

### v0.5.0（本地 GUI + 結構化 logging + 多語 + 打包）

整個 GUI 子系統從無到有,CLI 仍然完全保留(`qdrant-agent.bat`
跟 `qdrant-agent-gui.bat` 並存,後端共用 `src/` 邏輯)。

**新增 `gui/` 子系統**:
- `gui/api.py` (15 個 JS-callable methods,每個對應一個 CLI 子命令):
  `get_status`, `chat_turn`, `ask`, `ingest_file`, `ingest_directory`,
  `search`, `get_stats`, `list_documents`, `list_sessions`, `load_session`,
  `save_session`, `create_session`, `delete_session`,
  `delete_collection`, `recreate_collection`
- `gui/app.py`:PyWebView 啟動器、CLI flags (`--debug`, `--port`,
  `--no-gui`, `--lang`)、assets 路徑解析(支援 PyInstaller frozen)
- `gui/logger.py`:統一 logging — stderr + 旋轉檔案 `gui/app.log`
  (2 MB × 3 備份)、UTF-8、相對於原始碼或 frozen exe 自動找位置
- `gui/assets/index.html` + `style.css` + `app.js`:SPA,6 個 tabs

**多語 i18n (v0.5.0)**:
- `zh-TW` / `zh-CN` / `en` / `ja` 四種 locale,**完整翻譯**(非 placeholder)
- 自動偵測 `navigator.language`,persist 在 `localStorage`
- 切換語系會即時重新渲染所有 `data-i18n` / `data-i18n-ph` 元素
- CJK 字型 stack:MJhengHei / PingFang TC / Noto Sans CJK TC/JP / YaHei

**多媒體支援 (v0.5.0)**:
- Markdown 渲染:marked.js (via CDN, pinned v12.0.2) + DOMPurify
  (pinned v3.0.11) — LLM 回答自動渲染表格 / code block / list
- 自動跟系統主題:`prefers-color-scheme` + 手動 light / dark 切換
- Web Search 開關整合到 chat panel (`chat-web-toggle`)

**Theme**:
- CSS variables 三組:`auto` / `light` / `dark`,WebKit scrollbar 也跟主題變色

**危險操作保護**:
- `delete_collection` / `recreate_collection` 都有 `confirm()` 對話框,
  故意弄錯路徑或誤觸按鈕都有機會擋下

**Logging 整合**:
- 每個 `Api` method 進入都 `logger.info`,失敗 `logger.error` 帶 traceback
- 危險操作 (`delete_*` / `recreate_*`) 用 `logger.warning` 標 `(destructive)`
- 旋轉檔案策略:`RotatingFileHandler(maxBytes=2MB, backupCount=3)`,
  保留 `app.log.1/2/3` 歷史

**自動化測試**:
- `tests/test_gui_api.py`:9 個 pytest unit test(8 passed, 1 skip on Windows)
  - `test_api_has_all_expected_methods`:API surface 一致性
  - `test_chat_turn_returns_error_dict_on_failure` 等:錯誤路徑回傳 dict
    而非 raise,確保 GUI 不會 crash
  - `test_session_roundtrip`:create → save → load → delete 真實測過
  - `test_error_logged_to_app_log`:驗證 logger 寫入完整 traceback
- `tests/test_gui_e2e.py`:6 個 Playwright Chromium headless test
  - 啟動本地 HTTP server 載入 `gui/assets/index.html`
  - 用 `page.add_init_script()` 注入 fake `window.pywebview.api` bridge,
    仍走真實 Python Api class
  - 覆蓋:page 載入無錯、tab 切換、語系切換、chat 送出 → markdown 渲染、
    search 結果卡片、admin danger zone 按鈕、theme toggle

**打包 (PyInstaller)**:
- `gui.spec`:完整 spec,assets bundled、hiddenimports 列舉 lazy module、
  excludes 移除 tkinter/matplotlib/PyQt/streamlit 縮小體積
- `build-gui.bat`:一鍵 build,產出 `dist/qdrant-agent-gui/` 資料夾
- `pyproject.toml` 新增 `[project.optional-dependencies]`:
  `build = ["pyinstaller>=6.0"]`, `test = ["pytest>=8.0", "playwright>=1.40"]`

**新啟動腳本**:
- `qdrant-agent-gui.bat`:Windows GUI launcher(conda env 自動切換)

**Smoke test**:
- `python -m gui.app --no-gui` 5 秒內驗證 import chain + Api 15 個 method +
  Qdrant 連線 + log 寫入,適合 CI 跑

**已知限制**:
- PyWebView 在不同 OS 上用不同 backend:
  - Windows → WebView2 (Edge),已驗證 dev mode 啟動 OK
  - macOS → WKWebView,需手動確認已安裝 pyobjc / rubicon
  - Linux → GTK WebKit,需 `python3-gi gir1.2-webkit2-4.0`
- 預設不打包,`.exe` 用戶自己跑 `build-gui.bat`
- PyInstaller 不啟用 UPX (WebView2 loader 跟 UPX 不相容)

`pyproject.toml` 0.4.0 → 0.5.0

### v0.4.0（移除 client-side MCP 功能）
- 使用者反應「`/mcp` `/firefox` 指令在本地跑不起來」→ 決定把整個 client 端的 MCP 整合移除：
  - **刪除檔案**：`src/tools/mcp_client.py`、`src/tools/mcp_registry.py`、`src/tools/firefox_client.py`、`config/mcp.json`、`run_mcp_server.py`、`qdrant-mcp-server.bat`、`examples/mcp_client_basic.py`、`examples/mcp_client_advanced.py`、`examples/mcp_server_config.json`、`examples/README.md`、`.vscode/` 目錄
  - **`src/agent/agent.py`**：拿掉 `McpRegistry` / `FirefoxClient` / `_mcp_clients` / `_build_mcp_section` / `_try_self_fetch_url` / `_ensure_firefox` 等所有 MCP 相關成員；`_build_context()` / `ask()` / `chat_turn()` 簽名精減為只剩 `use_web_search` / `web_max_results` 兩個 context 來源
  - **`cli.py`**：
    - 拿掉 `--mcp` / `--mcp-max-chars` argparse flag、`mcp` subcommand dispatch + `_handle_mcp()` 函式
    - 拿掉 REPL 的 `/mcp` 系列指令（`/mcp on|off|status|list|servers`）
    - 拿掉 REPL 的 `/firefox` 系列指令（`get|search|gemini|xhome|xpost|tools|status|help`）
    - 拿掉 banner 內的 MCP 狀態列與 `/mcp` `/firefox` 指令說明
    - 拿掉 `server` subcommand dispatch + `_handle_server()` 函式
  - **`pyproject.toml`**：移除 `mcp>=1.3.0` 依賴
  - **`.env.example`**：移除 `FIREFOX_MCP_*` 註解區塊
- **保留**：
  - `src/mcp_server/server.py` 仍存在作為對外的 MCP server（可被 Claude Desktop / Cline 等 client 透過 `python -m src.mcp_server.server` 連線），但不再被本專業 CLI/Agent 內部使用
  - `src/tools/web_search.py` (Ollama Web Search) — 不是 MCP，繼續留，功能未變
- 移除原因：MCP client 端需要 Firefox extension + conda env + native_host 等外部依賴，本地常因 port 8001 未連而 timeout；client-side subprocess 模型也增加除錯複雜度。Qdrant 本身就是 HTTP server，直接走 in-process Retriever 已足夠
- 驗證：`qdrant-agent.bat ask` 與 `chat` REPL 端對端通，Qdrant 167,909 vectors 全在；`--web-search` 仍正常工作
- `pyproject.toml` 0.3.9 → 0.4.0

### v0.3.9（Embedding backend consistency 診斷：cli.py stats 警告 dim mismatch）
- 使用者反應「`cli ingest` 用的是 local 但 `chat` 用 ollama cloud ，
  需要修正」。診斷結果是實際問題：
  `.env` 設 `EMBEDDING_DIM=4096`（預期是 llama.cpp Qwen3）但
  `LLAMACPP_EMBED_URL` 設的 llama.cpp server 沒起，
  `EMBEDDING_BACKEND=auto` fallback 到 Ollama `nomic-embed-text`
  (768-dim)，Qdrant collection 實際是 4096-dim → dim 不一致，
  search / chat 會無聲無息返空。
- 修正：
  - `src/ingest/embedder.py` `Embedder._qdrant_dim()` 加 helper、
    `backend_info()` 多回傳 `collection_dim` 欄位
  - `cli.py stats` 多打 `Embedder backend` 區塊，並讀
    `collection_dim` 跟 `native_dim` 比對，不一致時印
    `[!] DIMENSION MISMATCH` + 兩個修正路徑建議。
- README 加 `## Embedding backend consistency（v0.3.9+）` 章節說明：
  - 怎麼查 backend 狀況（`cli.py stats`）
  - 4 個 backend 原生 dim 對照表
  - 兩個修正路徑二選一（保留 collection dim 換 backend vs.
    重新 ingest 適應 backend）
  - 快速診斷銘語（3 個常見問題）
- 本機狀態：Qdrant `file_docs` 是 4096-dim / 167,909 entities。
  使用者可選：
    - (a) 換 `OLLAMMA_EMBED_MODEL=qwen3-embedding:8b` (4096-dim)
    - (b) 走 `delete-collection` + 改 `EMBEDDING_DIM=768` 重 ingest
- `pyproject.toml` 0.3.8 → 0.3.9

### v0.3.8（firefox_craw unavailable 自動 fallback 抓 URL）
- 現狀：v0.3.7 fallback 在 firefox_craw 尚未連接時只印
  「unavailable」提示，要求使用者手動 `/firefox get <url>`。但
  使用者期望一次說完、自動報取網頁。
- 修正：`src/agent/agent.py` 在 firefox_craw unavailable 訊息出
 現後，自動試 `_try_self_fetch_url(query)`：如果使用者訊息中含
 有 `http(s)://...` URL（regex 抽取），調 `agent.firefox.get_page_html(url)`
  抓 HTML。若 server 不接受 stdio 還會回 Error，那 fallback 會失敗
  但仍給手動指示，graceful degradation。
- 使用情境：使用者輸入「使用 firefox_craw get https://www.hicloud.net.tw/」
  LLM 會拿到「### [Self-Fetch] get_page_html」的頁面內容並提供答案，
  而不是說「請手動試」。
- 測試：URL extraction 函式
  `_extract_url_from_query` 涵蓋 https、http、有 path/query、
  沒 URL、空字串 5 個情境。
- `pyproject.toml` 0.3.7 → 0.3.8

### v0.3.7（Agent.firefox unavailable fallback：LMM 不再當成真實答案）
- `src/agent/agent.py` `_build_mcp_section()` 修 LMM 代理被 firefox_craw
  "Firefox Extension 尚未連接" 誤導的問題：之前 firefox_craw 會回
  "Firefox Extension 尚未連接..." 當作 answer 進 context，導致 LLM
  「開始讀」就結論「無法使用 firefox」而收手。
- 修正：當 firefox_craw 的 fetch 結果包含 marker
  （`Firefox Extension 尚未連接` / `尚未連接` / `native_host`）時，
  不把該結果塞进 prompt context，改成輸出一段友善的
  `[firefox_craw unavailable — Suggest /firefox get <url> 或
  /firefox search <query>  ...]` 訊息。
- 這讓 LLM 看到時會主動提示使用者改用手動 /firefox * 指令。
- 整合測試：`Agent._build_mcp_section(firefox_craw spec, "hicloud")`
  → 印 `unavailable` 訊息（驗證 marker 被正確判別）。
- 端到端：這個修復讓使用者輸入「使用 firefox_craw search hicloud」時
  LLM 會說「請在 REPL 中用 `/firefox search hicloud` 手動調用」
  而不是看起來只能放棄。
- `pyproject.toml` 0.3.6 → 0.3.7

### v0.3.6（examples/ 參考 mcp_getweb 風格重寫 + README 指向說明）
- `examples/mcp_client_basic.py` 參考
  `C:\Users\humphery\coding\mcp_getweb\examples\python_mcp_client.py` 重寫：
  - 加 `_check_server_exists()` fail-fast：找不到 server 入口就
    `sys.exit(1)` 給明確錯誤訊息
  - 加 `_ok` / `_info` 輔助函式（用 `\u2713` 印 ✓）
  - 用 `init_result.serverInfo.name` 印 server 名字跟版本
  - 6 個 step：init → list tools → stats → list → search → ingest (可選)
- `examples/mcp_client_advanced.py` 加 `EXT_NOT_READY_MARKERS`
  偵測 firefox_craw 等外部依賴尚未就緒的字串
- `examples/README.md` 加「## 參考：sibling project 的範例」子節，
  記錄是「部分參考」 mcp_getweb 的風格
- `pyproject.toml` 0.3.5 → 0.3.6

### v0.3.5（examples/ 目錄：MCP client 介接範例程式 + Chat ↔ MCP 介接流程章節）
- 新增 `examples/` 目錄提供 3 個 MCP client 範例讓使用者能快速對接本專案的 5 個 tools：
  - `examples/mcp_client_basic.py`：基本範例，spawn `run_mcp_server.py`、
    `list_tools()` 列出 5 tools、依序呼叫 `get_document_stats` /
    `list_documents` / `search_documents`。
  - `examples/mcp_client_advanced.py`：進階， `asyncio.gather` 並行呼叫 4
    個 tools、`McpError` 攔截、故意送錯參數 / 不存在的 tool 看 server
    怎麼拒絕。
  - `examples/mcp_server_config.json`：Claude Desktop / Cline / Cursor
    設定範例（mcpServers block + env vars + 各 client 設定檔路徑）。
  - `examples/README.md`：examples 詳細說明 + 跑法 + 與 `from src.vector_store.retriever
    import Retriever` 的對比。
- README 新增「## Chat ↔ MCP Server 介接流程」章節：
  - 三層通訊架構圖（Chat stdio JSON-RPC ↔ MCP server / MCP server TCP 8765 ↔
    native_host / native_host stdin/stdout ↔ Firefox extension）
  - Cline / Claude Desktop 介接設定範例
  - 訊息協議表（stdio JSON-RPC 2.0 + 4-byte length + UTF-8 JSON）
  - 一次完整 12 步流程（以 `google_search` 為例）
  - 5 個關鍵概念
  - MCP Inspector 開發者工具使用說明
- README 新增「## 範例程式（`examples/`）」章節索引。
- `pyproject.toml` 0.3.4 → 0.3.5

### v0.3.4（修 /firefox 指令 firefox_craw 呼叫失敗：lazy-start + 讀 config/mcp.json）
- `cli.py` `/firefox get <url>` 之前會回 ``[error] McpClient not started
  (auto_start=False).``，原因是 ``FirefoxClient`` 在 `Agent.__init__` 中
  以 ``auto_start=False`` 構造，第一次 ``call_tool`` 卻被拒絕。修正：
  - `src/tools/mcp_client.py` `call_tool()` 改為 lazy-by-demand：
    ``auto_start=False`` 不再拒絕，會在第一次呼叫時自動起 subprocess。
    ``check_connection()` 同步修正。
  - `src/tools/firefox_client.py` `FirefoxClient.__init__` 預設從
    `McpRegistry` 讀 `firefox_craw` 的真實 spec（避免 hard-coded
    ``DEFAULT_*`` 指向已不存在的 dev conda env）。
  - 測試結果：第一次 ``get_page_html("https://www.google.com")`` 真的
    lazy-start ``c:\Users\humphery\coding\mcp_getweb\dist\server.exe``、
    firefox_craw 收到 `CallToolRequest`、
    回傳 ``error=Error: Firefox Extension 尚未連接...``（表示 MCP 通了，
    差別只在 Firefox 擴充還沒連上 `localhost:8765`）。
- 統合診斷 v0.3.4 實裝後仍回「Firefox Extension 尚未連接」的原因：
  - firefox_craw 是 `FastMCP("Firefox-Web-Crawler")` + stdio transport
    (見 `c:\Users\humphery\coding\mcp_getweb\mcp_server\server.py`)；
  - 它會在 background 額外起一個 Native Host TCP server 在
    `127.0.0.1:8765`，但需要 sibling project 的 `native_host.py`
    (打包成 `c:\Users\humphery\coding\mcp_getweb\dist\native_host.exe`)
    連進來中繼 Firefox 擴充的請求；
  - **如果還沒人起 native_host.exe，或是 Firefox 擴充還沒打開
    並連到 `127.0.0.1:8765`， firefox_craw 就會回
    `ACTIVE_CLIENT=False` → 「Firefox Extension 尚未連接」**。
  - 修正方式（使用者手動）：在另一個 terminal 執行
    `c:\Users\humphery\coding\mcp_getweb\dist\native_host.exe`
    作為常駐背景進程，並在 Firefox 裝好對應的 extension
    （參見 `c:\Users\humphery\coding\mcp_getweb\README.md` 補安裝說明）。
  - 未來優化：`cli.py chat` 可偵測 native_host 未起時自動
    `subprocess.Popen()` 起一個（v0.3.5+ TODO）。
- 依存性：需要 `native_host.exe` 起來 + Firefox 擴充打開連到
  `127.0.0.1:8765`。
- `pyproject.toml` 0.3.3 → 0.3.4

### v0.3.3（chat REPL 直接呼叫 firefox_craw MCP tools）
- `cli.py chat` 新增 `/firefox *` 指令，讓使用者在 REPL 內**手動**呼叫 firefox_craw
  MCP 的 tools（不走 LLM 代理，結果直接印在 console 並寫入 session）：
  - `/firefox get <url>`      抓 URL 頁面 Markdown
  - `/firefox search <query>` google_search(query)
  - `/firefox gemini <prompt>` query_gemini(prompt)
  - `/firefox xhome [limit]`  取得 X/Twitter home timeline
  - `/firefox xpost <url>`    取得單則 X/Twitter post
  - `/firefox tools`          列出 firefox_craw 所有可用 tools + 描述
  - `/firefox status`          probe 連線狀態 + 診斷
  - `/firefox help`            列出所有指令
- 使用情境：在諜 LLM 之前先手動抓個網頁/查個關鍵字，結果會寫入 session
  (`role: tool` + `name: firefox.<tool>` + 內容) 作為後續對話的 context
- 跟 v0.3.2 的 `/mcp on` 不同：`/mcp on` 是讓 LLM 自動代理（dump tool list
  到 prompt context），`/firefox get <url>` 是「我手動先調 firefox 抓過」。
  兩者互不衝突，可並行。
- 依賴：需 conda env `mcp_getweb` 存在（ v0.3.0 環境）。
- `pyproject.toml` 0.3.2 → 0.3.3

### v0.3.2（mcp.json 從 .vscode/ 搬到 config/ + chat REPL 整合 MCP context injection）
- `mcp.json` 從 `.vscode/mcp.json` 搬到 `config/mcp.json`，避免跟 VSCode IDE
  的 `.vscode/` 設定混淆：
  - `src/tools/mcp_registry.py` 的 `default_registry_paths()` 把 `config/mcp.json`
    排第一位（優先順序：env `MCP_REGISTRY_PATH` > `config/mcp.json` > `.vscode/mcp.json` > `.mcp.json`）
  - 保留 `.vscode/mcp.json` / `.mcp.json` 作為 legacy 向後相容 fallback
  - `McpRegistry.__init__` 找不到現存檔案時 fallback 預設建立 `config/mcp.json`
- `cli.py chat` 整合 MCP context injection（每輪可切換）：
  - argparse 加 `--mcp` / `--mcp-max-chars` flag
  - REPL 加 `/mcp` `on|off|status` toggle、`/mcp servers` probe enabled servers
    連線狀態
  - banner 加狀態列「狀態 : MCP tools ON/OFF, Web search ON/OFF」+
    設定檔路徑顯示 `config/mcp.json`
  - `agent.chat_turn(use_mcp=use_mcp, mcp_max_chars=mcp_max)` 完整接好，
    ON 時 prompt context 自動加 `### MCP Tools` 區塊（每個 enabled server
    貢獻 tool list + firefox_craw 自動做 google_search 預取）
- `/mcp on` (toggle 注入) 跟 `/mcp on <name>` (enable server) 透過參數有無區分，不衝突
- `pyproject.toml` 0.3.1 → 0.3.2

### v0.3.1（chat REPL：移除 `/firefox` + `/mcp` 顯示）
- `cli.py chat` 移除 firefox 相關：
  - argparse `--firefox` / `--firefox-max-chars` flag
  - REPL 內建 `/firefox`、`/firefox on`、`/firefox off`、`/firefox status` 指令
  - `use_firefox` / `firefox_max_chars` 變數與 `agent.chat_turn()` 參數
  - `from src.tools.firefox_client import FirefoxClient` import
  - welcome banner 內的 firefox_craw 說明行與 finally 註解
- 取代為 `cli.py` REPL 的 `/mcp` 指令顯示（banner + 操作說明列）：
  - `/mcp`、`/mcp list`：列出 `.vscode/mcp.json` 中所有 servers
  - `/mcp status`：probe 全部 enabled servers 的連線狀態
  - `/mcp status <name>`：probe 單一 server
  - `/mcp on|off <name>`：切換 enabled / disabled（會 auto-save 回 `.vscode/mcp.json`）
- 這些 `/mcp` 指令本來就存在（v0.3.0），v0.3.1 只把它們顯式列進 welcome banner
  跟操作說明讓使用者知道
- `firefox_craw` 仍可透過 `.vscode/mcp.json` 註冊成一般 MCP server，
  Agent 會自動加進 prompt context 的 `### MCP Tools` 區塊（v0.3.0 起的設計）
- `pyproject.toml` 0.3.0 → 0.3.1

### v0.3.0（firefox_craw MCP 整合）
- 新增 `src/tools/firefox_client.py`：`FirefoxClient` 封裝 sibling 專案
  `mcp_getweb/mcp_server/server.py` 為 stdio MCP client。透過 conda env
  `mcp_getweb` 啟動 11 個 firefox-craw tools（`get_page_html` /
  `google_search` / `get_x_home` / `get_x_post` / `query_gemini` /
  `save_as_markdown` / `convert_document` / `convert_file` /
  `download_x_post` 等）。Lazy-start、reuse session、graceful
  degradation（缺 mcp SDK / conda env / Firefox extension 都回傳
  `Error: ...` 字串而非 raise）
- `src/agent/agent.py` 加 `use_firefox` / `firefox_max_chars` 參數到
  `ask()` / `chat_turn()`:啟用時 prompt context 自動加 `### Firefox
  Web Fetch` 段、system prompt 加 `[Firefox N] <tool>(<args>)` 引用
  規則。Firefox fetch 反映使用者目前已登入瀏覽器的視角，視為 web
  資訊最權威來源
- `cli.py chat` 加 `--firefox` / `--firefox-max-chars` flag + REPL
  內建 `/firefox`、`/firefox on`、`/firefox off`、`/firefox status`
  指令，跟既有的 `/web on/off/status` 完全對稱
- REPL 退出時 `agent.shutdown()` 會把 firefox_craw subprocess 關掉，
  不會洩漏 conda 進程
- `FirefoxResult.to_prompt()` + `results_to_context()` 把結果轉成
  Markdown 區塊、跟 `OllamaWebSearch` 風格一致
- `.env.example` 加 `FIREFOX_MCP_COMMAND` / `FIREFOX_MCP_ARGS` /
  `FIREFOX_MCP_CWD` / `FIREFOX_MCP_TIMEOUT` (全 commented, 用預設即可)
- 端到端 mock 驗證：`python -c "from src.tools.firefox_client import
  FirefoxClient; fc = FirefoxClient(auto_start=False); print(fc.cwd,
  fc.args, fc.command)"` 印出 conda run -n mcp_getweb + 路徑
- CLI 變動：`cli.py chat --help` 多了 `--firefox` / `--firefox-max-chars`
- 依賴：需安裝 `mcp>=1.3.0`（已 in `pyproject.toml`）；需 conda env
  `mcp_getweb`（已存在於本機）+ Firefox 擴充已連線到 localhost:8001
- `pyproject.toml` 0.2.9 → 0.3.0

### v0.2.9（chat REPL per-session 問題 dedup）
- `cli.py` 新增 `_normalise_question` / `_question_fingerprint`
  (lowercase + trim trailing CJK 標點 + sha1[:16]) 與
  `_find_recent_assistant_reply()` helper
- `_handle_chat()` 在「Real user turn」之前比對 fingerprint:
  若同 session 內之前已答過,直接重播之前 assistant 答案 +
  印一行「[系統] 此 session 內已答過相同的問題, 直接重播上次回答
  (略過 RAG / Web 查詢).」,**不重複查 Qdrant / Web Search**
- 不影響 `cli.py ask`(每次 ask 是新 process),只對 chat REPL 生效
- 端到端 mock 驗證:重發同問題 → `agent.chat_turn` 沒被呼叫 (dedup hit),
  不同問題 → 重被呼叫 (fresh call)
- `pyproject.toml` 0.2.8 → 0.2.9

### v0.2.8（Ollama Web Search 整合）
- 新增 `src/tools/web_search.py`: 封裝 `OllamaWebSearch` (POST `https://ollama.com/api/web_search`)
  + `OllamaWebFetch` (POST `/api/web_fetch`) 兩端點, zero-dep。回傳 dataclass
  `WebResult` / `FetchedPage` 帶 `to_prompt()` 渲染成 Markdown 區塊
- `src/agent/agent.py` 加 `use_web_search` 與 `web_max_results` 參數到 `ask()` / `chat_turn()`:
  啟用時 prompt context 自動附加「### Web Search Results」段（本地 + web 雙來源）
- `_system_prompt()` 加 web 引用規則（`[Web N] <title> (<url>)`）與「本機優先」準則
- `cli.py ask` 加 `--web-search` / `--web-max-results` flag
- `cli.py chat` REPL 加全域 `--web-search` flag + per-turn `/web`、`/web on`、
  `/web off`、`/web status` 內建指令
- 端到端驗證: `qdrant-agent.bat ask "qdrant 跟 milvus 的差異" --web-search`
  → 10 row × 4 col GFM 表格 + 多個 `[Web N] (url)` 引用
- 需 `OLLAMA_API_KEY`(.env 已設)。無 key 時 graceful degradation:仍可走 Qdrant-only
- `pyproject.toml` 0.2.7 → 0.2.8

### v0.2.7（chat 表格渲染修正 + GFM table 支援）
- `src/ui/md_render.py` 新增 GFM table 偵測 + CJK 寬度感知 ANSI 表格渲染：
  - `_wcswidth()`: `unicodedata.east_asian_width` 替代 wcwidth 套件（zero-dep）
  - `_parse_table_row()`: 拆 `| col | col |` 並 strip 每個 cell
  - `_render_table()`: 計算每欄 max CJK 寬度 + 全形 `┌─┬─┐` ANSI box
- `cli.py _handle_chat` 修 BBS-style 框破壞表格：改 raw print LLM 輸出（不再用 `_bbs_wrap_ansi` 切窄寬度包表格），BBS 框僅框 header + footer + 來源行
- 修 bug：先前 `_render_table` 用 `rows[2:]` 取 body 會漏 row 1 — 改 `rows[1:]`（外層 caller 已 skip GFM separator）
- `pyproject.toml` 0.2.6 → 0.2.7

### v0.2.6（Ollama Cloud / per-collection backend switch）
- `src/ingest/embedder.py` 增加 Ollama Cloud / local Ollama server backend (`OLLAMA_BASE_URL` + `OLLAMA_EMBED_MODEL`)
- 加 `EMBEDDING_BACKEND` env var 明確指定 backend：`local` / `ollama` / `openai` / `hash` / `auto`（probe by priority）
- 加 `backend_info()` 診斷方法，報告「active backend + native dim + collection + 各 backend 連通性」
- 同一個 Qdrant server 可同時保留多個 collection（例：`file_docs_local` 走 Qwen3 4096-dim、`file_docs_ollama` 走 nomic-embed-text 768-dim）；切 backend 只要 `set EMBEDDING_BACKEND=ollama` + `set QDRANT_COLLECTION=..._ollama` + `recreate-collection`
- `src/config.py` 加 `embedding.backend` / `embedding.ollama_model` namespace
- `.env` / `.env.example` 加 `OLLAMA_EMBED_MODEL` / `OPENAI_EMBED_MODEL` / `EMBEDDING_BACKEND` 預設為 `auto`
- `pyproject.toml` 0.2.5 → 0.2.6

### v0.2.5（local llama.cpp Qwen3-Embedding via Vulkan）
- `src/ingest/embedder.py` 重寫為 3-tier backend priority：local llama.cpp server → OpenAI API → deterministic hash fallback
- `LLAMACPP_EMBED_URL` env var (default `http://localhost:8080/v1/embeddings`) + `_llamacpp_available()` probe to `/v1/models`
- `EMBEDDING_DIM` 預設從 384 → 4096（與 Qwen3-Embedding-8B native dim 一致）；OpenAI 1536-dim 仍可後向 fallback
- `C:\llama.cpp-vulkan\start-llamacpp.bat` 啟動腳本（Vulkan build，AMD RX 5700 XT 偵測為 Vulkan0）
- 取代原本的 hash fallback：search 現在有真實語意關聯（測試 "全天候策略風險平價" / "Ray Dalio 投資策略" 都返回相關 PPTX 切片）
- Required: `delete-collection` + `recreate-collection` 才能從舊 384-dim 切到新 4096-dim collection
- `pyproject.toml` 0.2.4 → 0.2.5

### v0.2.4（structured DOCX/PPTX extraction）
- `src/ingest/loader.py` 的 `FileLoader` class 補上結構化抽取，跟 milvus-mcp-agent 完全對齊：
  - DOCX 結構化：paragraphs + inline image placeholders (`[Image: name | alt='descr']`) + tables (`[Table Row] cell1 | cell2 | ...`) + headers/footers (`[Header]` / `[Footer]` 前綴)
  - PPTX 結構化：`# Slide N: <title>` H1 (從 placeholder) + bullets (`- text`) + tables 轉 markdown (`## Table` + `| h1 | h2 | ... |` + `| -- | -- | ... |` 分隔線 + 欄位 padding) + image placeholders (`> [image: name | alt='descr']`)
- `FileLoader.load_file(path)` 自動 dispatch 到結構化版本（DOCX/PPTX）；`load_file_plain()` 保留 v0.1.x 純文字行為
- 模組層 `load_file(path)` 保留向後相容 — 直接使用 `_LOADERS` dict 仍然是 v0.1.x 純文字
- Ingest pipeline 自動受益（`IngestPipeline` 透過 `self.loader.load_file` 呼叫，無需改動）
- e2e 驗證：生成的 PPTX（title + bullets + table）走 `FileLoader.load_file` → 產生 250 chars 結構化輸出（含 `## Table` + markdown 表格）；同期模組層 `load_file` → 131 chars 純文字
- `pyproject.toml` 0.2.3 → 0.2.4

### v0.2.3（milvus-style ingest pipeline）
- `src/ingest/image_describer.py` 新增（port from milvus-mcp-agent）：Ollama Cloud vision API 生成 DOCX/PPTX 內嵌圖的雙語描述，SHA-1 cache + 4 workers 平行
- `src/ingest/loader.py` 加 `FileLoader` class（`extract_media` DOCX/PPTX 抽內嵌圖、`scan_directory` 依 supported_extensions 過滤）
- `src/ingest/chunker.py` 加 `TextChunker` class 跟 `DocumentChunk`（`chunk_document` 帶 metadata：filename / extension / file_size / last_modified / chunk_index / total_chunks）
- `src/ingest/embedder.py` 加 `embed_batch()` + 4096 entries LRU cache（OpenAI `embeddings.create` 批取）
- `src/ingest/pipeline.py` 重寫：加 image description 填充、 dedup-by-content-hash（不變 skip）、full re-ingest on content change、EMBED_BATCH_SIZE=64 / INSERT_BATCH_SIZE=500、gc.collect() 釋放
- `src/config.py` 加 `ingest` / `llm` / `embedding` 命名空間（保持跟 milvus 對齊，舊扁平屬性 `config.QDRANT_URL` 等也仍可用）
- `pyproject.toml` 0.2.2 → 0.2.3
- 驗證：刪 collection 重 ingest 乾淨 18 chunks，search "HNSW" 命中 3 個來源 README.md（score 0.14–0.17）

### v0.2.2（ingest robustness）
- `src/ingest/pipeline.py` 加 `_SKIP_EXTS` 集合（30+ 個：二進制、影像、字型、執行檔、壓縮檔、不支援的 Office 格式）— 在 ingest 階段就跳過
- `src/ingest/pipeline.py` 加 per-file size cap（`MAX_FILE_SIZE_MB`，預設 50 MB）— 超過的檔印訊息跳過，避免一個 200MB 純文字意外讀進 RAM 整個 ingest 卡住數分鐘
- `src/ingest/loader.py` 跳過 Office 暫存檔（`~$xxx.pptx` / `~$xxx.docx`），不再有 `[loader] failed to read: PackageNotFoundError` 雜訊
- `ingest_directory` 在結束時多印一行 `skipped N file(s) (size/format/extension)` 讓使用者知道哪些檔沒吃
- `.env.example` 加 `MAX_FILE_SIZE_MB=50`
- 針對象：`ingest "E:\公司資料\5_分持規劃"` 遇到 280MB encrypted tar、6+ 個 6–13MB 大 PPTX、Office 暫存檔、加密檔等浪費時間的輸入，現在會自動跳過並列出

### v0.2.1（EPUB loader added）
- `src/ingest/loader.py` 加 `.epub` loader：用 `ebooklib` 讀 `ITEM_DOCUMENT` + `BeautifulSoup` 抽 text，保留 spine 順序
- `pyproject.toml` 0.2.0 → 0.2.1
- e2e 驗證：生成 minimal EPUB 走 ingest → search，`HNSW` 跟 `Cosine` 都能被搜到

### v0.2.0（chat REPL ported）
- `cli.py chat` 從 stub 變成完整 BBS-style REPL：多輪對話、`/info` `/reset` `/list` `/load <id>` 內建指令、`exit`/`quit`/`Ctrl-C` 退出
- `src/agent/session.py` ported from milvus-mcp-agent：sessions 存到 `~/.qdrant-rag-agent/sessions/`，JSON 原子寫入（tmp + os.replace）
- `cli.py sessions {list,show,delete}` 管理 saved sessions
- `cli.py delete-collection` / `cli.py recreate-collection` 補上（跟 milvus CLI 對齊）
- 移除 milvus 端的 `MilvusMCPClient` subprocess 路徑（Qdrant 是真 server，in-process Retriever 即可）
- `qdrant-agent.bat` 不變（沿用 `activate milvus-mcp` 共用 conda env）

### v0.1.0（initial scaffold）
- 5 個 MCP tools、ingest / search / ask / stats / server CLI
- HNSW + Cosine 預設；payload index on `filename` / `source` / `content_hash`


---

## 更新紀錄 v0.2.0

實際測試過程中额外修了的 Qdrant 1.19 兼容問題（不是 chat 引起的，但 port 完後第一時間撞到）：
- `qdrant_client` >=1.10 移除 `QdrantClient.search()` → 改用 `client.query_points(query=...)`（`src/vector_store/qdrant_client.py.search()` 保留舊 API 作為 fallback）
- `CollectionInfo.vectors_count` 屬性不存在 → 改用 `points_count`（`get_stats()` 保留 fallback）
- chat REPL 原本 traceback 後直接退出 → 加上 graceful exception handling，現在撞到 Qdrant 連不到或 LLM key 缺會印 `[錯誤] 問答失敗：...` 後回到 input prompt、且不污染 session
- 缺 Windows 原生 Qdrant binary 下載 + 啟動的快速開始流程（README 「快速開始 §1」已補）
