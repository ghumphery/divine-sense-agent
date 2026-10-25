譯# CLAUDE.md — qdrant-rag-agent workspace rules

This project is a sibling of `milvus-mcp-agent`. The vector store is
**Qdrant** (HTTP server, HNSW + SIMD/Vulkan acceleration) instead of
Milvus Lite. The CLI, MCP tools, `.env` schema, and dev discipline
mirror `milvus-mcp-agent` v0.1.19+ so the two stay structurally parallel.

Current workspace version: **v0.9.133.18**（133.18 =
**背景任務 write-back 空占位修**（live 11:48：esp32s 任務成功、回覆
426 字在 task session，但 chat 的 write-back 顯示「(無回覆—模型未
產生內容)」＝_run_once 漏 attach_sid→registry sid=None→
get_background_task 的 answer_preview 永不填→占位泡 dedupe 後永不
補；修＝begin_task 後立即 attach_sid；tests 41 passed；
pyproject 0.9.133.17 > **0.9.133.18**）；
**多輪抓取目標鎖定**（TG 連抓多篇後漂移＝壓縮擠掉 URL/標題＋「第N篇」
索引慣性；修＝_summarize_history prompt 逐字保 URL/標題＋
_squeeze_prose 用 _append_omitted_urls 把被擠 URL 附回＋rulebook
[r10] 目標鎖定規則；tests 38 passed；pyproject 0.9.133.16 >
**0.9.133.17**）；
**save 競態重試＋假任務回執防護**（live 17:20：save_session 的
os.replace 撞 Sessions 輪詢的讀窗（WinError 5）＝該次 assistant 回覆
丟失 → save 加 PermissionError 退避重試 ×5；＋17:07 助手編造
「已送出背景任務 8a2c1f4b」（task-8a2c1f4b.json 不存在＝假 tool call，
run_task_once 不在 routed surface 上）→ _TOOLROUTE_CORE_SERVERS +=
task-local（+2 工具，誘因消除）；tests +1＝31 passed；
pyproject 0.9.133.15 > **0.9.133.16**）；
**routed 工具從未裁目錄挖掘**（17:07 實錘第二層：133.14 的降級已生效
但 `_toolroute_tool_names` 內 active_tools() 走 500 cap 裁過目錄→
wordcraft（優先序 #37＝尾）被裁→names 挖不到→dispatches=0；修＝
manager 新 `raw_tools()`（未 cap catalog）+ `_toolroute_tool_names`
改用 raw＋names 按 routed server 順序分組（128 cap 永遠先裁非命中）
＋.env 優先序把 wordcraft/deckcraft 前移 #9/#10；tests +1 行為級
＝97 passed；pyproject 0.9.133.14 > **0.9.133.15**）；
**低置信 ToolRoute 聯集降級修**（live 16:39 log 定罪：docx 填表任務
class=None p=0.57 → 全 fleet 面 → 500 cap 按註冊順序裁掉
wordcraft→dispatches=0；修＝ask+chat_turn 兩處降級條件
`_tcls` → `(_tcls or _srvs)`＝Jev 沒把握也用問題側觸發命中 unions
（docx→wordcraft 保住+省 token）；tests +3＝30 passed）；
133.13a = **預算 10GB/100 萬**（.env
QDRANT_ARCHIVE_MAX_BYTES/MAX_FILES ＋ **zipfs host 改接
_archive_budget()**（此前硬編碼常量未接 env＝.env 變更不生效的真
bug）；commit 3139530）；
133.13 =
**zipfs-local MCP 上線**（上傳 .zip/.tar 單檔 → LLM 在包內做檔案系統
操作（list/read/write/delete/rename/extract_one/info/status 八工具）
→ 下載同一份單檔，全程不解壓；新 src/zipfs_tools.py 純邏輯
（copy-on-write 原子 rebuild＋member 名守衛＋危險擴展黑名單＋雙預算
＋覆寫/前綴語意）＋ZipFsLocalHost（變異必須在 write root、
刪改名 confirm 閘、7z 只讀）＋stdio mirror zipfs_server.py＋
MCP_ACTIVE_SERVERS gate＋ToolRoute 行＋prompt 區塊；tests
test_zipfs_host.py 20 passed＋回歸 57 passed；pyproject
0.9.133.12 > **0.9.133.13**）；
133.12 =
**移除惰性 web-search 開關**（實證 use_web_search 在 tool-loop 對話路徑
零效果——tool 版 system prompt 不讀 web_enabled、真網路能力=ToolRoute
自動掛+LLM 自主調用；刪 chat toolbar 的 chat-web-toggle＋排程編輯器
schedule-edit-web-search/web-max＋app.js useWeb getter/i18n ×4×3 keys、
call sites 改传 false；**保留** ☑ 背景執行（手動勾=UI 立即釋放 vs 自動
detach 的 60s 鎖窗＝控制權差異）；tests +4 反守衛＋源行同步＝web 系
94+41 passed；pyproject 0.9.133.11 > **0.9.133.12**）；
133.11 =
**排程摘要 pin 四層修**（凌晨 03:00 首火診斷：schedule 已建且
cron 有 fire（03:00:09、verified）但 `summarize_sessions` 根本
沒被呼叫＝memory_docs session-summary 停在 1 筆；四層根因
①task_llm 白名單不收 llamacpp → pin 被 fire-time validate 拒＋
降級雲鏈（修＝白名單＋目錄 `_catalog_uncached_llamacpp`（同一
env QDRANT_SESSION_SUMMARY_LLAMACPP_URL 預設 :8090/v1、live
/v1/models 實證 data[0].id=qwen2.5-7b-cpu、down=ok+UNVERIFIED）
＋Agent._pick_provider pin 直連分支 `_llamacpp_chat_base()`
（api_key dummy＋clean extra_body；**刻意不進 _PROVIDER_ORDER**
——弱 CPU 摘要模型絕不可被 auto-detect 收成聊天主鏈）
②toolroute memory-local 觸發列 += summarize_sessions|沉澱 等
（Jev class 判錯也 union 挂上 memory-local）
③pin 直連後 500 工具 schema＝27,174 tokens > CPU ctx 12,288 →
400 exceed_context_size → `_chat_tools` 依
`QDRANT_LLAMACPP_PIN_SERVERS`（預設 memory-local,datetime-local,
meta-local ≈14 工具）交集縮面，非 pin 情形零改動
④通用 system prompt 本身就超 ctx→ 縮面後第二輪真火 tokens 仍
20,579——分解實測 system prompt（988 行通用 trigger 手冊）
單獨 19,330 tokens > 12,288 → 新 `_pin_narrow_prompt()`
（pin llamacpp 回合切專用窄版 ~450 chars＋memory block 照掛
＝全套 ≈2.4k tokens）；
live run-now 真火：pin log＋surface 縮面＋真 dispatch 沉澱；
tests test_task_llm +7＋test_toolroute +2；pyproject
0.9.133.10 > **0.9.133.11**）；
133.3 =
**T+1 對話摘要沉澱**（user 核准「②P1＋召回池整合」＝UniiChat
記憶樹報告的採納版：新 src/agent/session_compactor.py —— 每日批次把
近期 session 檔逐檔 LLM 壓 1-3 行沉澱 memory_docs kind=session-summary
（session_id 反向連結＝兩層式記憶、冪等 by source 精確比對零嵌入、
_write 後 _readback 驗證失敗即中止、per-session 隔離永不 raise；
LLM＝_summarize_history 同款單發 + Agent(task_llm=) pin）；本機只有
qwen2.5:7b 可用（gpt-oss:20b 未安裝）→ 預設 pin ollama:qwen2.5:7b；
召回零改動＝session-summary 天然進 _memory_block 池、task-result
維持 must_not；memory-local 第 7 工具 summarize_sessions＋stdio
mirror＋CLI python -m src.agent.session_compactor＋boot-seed 排程
QDRANT_SESSION_SUMMARY_CRON（gui/api.py 127.0 同款）＋.env.example
×5（SINK/LLM/LLAMACPP_URL/OLLAMA_URL/CRON；**llamacpp: pin＝直連本機
llama.cpp CPU chat server（8090，C:\llama.cpp\start-llamacpp-chat-cpu.bat
=qwen2.5-7b-cpu、ctx 12288×1、不帶 -ngl/--device 不碰 AMD）
/ollama: pin＝直連本機 ollama 11434**，都不走 Agent 鏈——agent 的
OLLAMA_BASE_URL 是雲端端點）；tests +29 新檔＋memory_store 守衛×1
＝合跑 63 passed；pyproject 0.9.133.2 > **0.9.133.3**）；
133.4 =
**小狗跨動作忽大忽小修（user「小狗的圖 要注意大小 各動作之間 忽大
忽小」＝127.28 跨檔維度重現）＝8 張 `_bw` 直掛資產尺寸正規化：三種
畫布（512/1024/300）＋`100% 100%` 塞進 150×120 盒＝壓形＋bbox 佔比
27-47% 跳動；`_dogsize_fix.py` 從 `_bw_orig/` 重做（flood 去背 TOL=40
＋1px 羽化）→ 畫布統一 500×400（5:4）＋union-bbox 長邊 78%（=sheet
跨支中位）＋置中＋底線 96.7%＋GIF 支級共同 scale；**GIF 透明教訓**
＝直接 ADAPTIVE 量化丟光 alpha → `alpha≤128→index 255` mask-paste＋
`transparency=255,disposal=2`；sheet 系 16/17 支 contentH=104 健康
不動；URL bust ？v=13330 ×8（roll 留 13320）；tests test_dog_runner
**17 passed**（＋`test_ball_sheet_dog_enlarged` 守衛＋同回合追加
＝user「玩球的圖狗變小」＝`dog_ball_sheet` 全 6 cell 長邊 64-74%
全系最小 → **支級放大 1.115×**（撲跳幀剛好撐滿 116px 地面線、跳
弧同步放大、x 中心不動、150px 格零改動）＋bust 13330 ×9）；pyproject
0.9.133.3 > **0.9.133.4**）；
133.5 =
**大文件 add_document 自動轉背景（user「大文件自動轉背景」＝TG 600s
watchdog 撞線事件自動化）＝`_maybe_background_ingest`：> 閾值
（`QDRANT_ADD_DOCUMENT_INLINE_MAX_MB` 預設 8MB，0=legacy）即
detached thread＋registry source="ingest"＋立即回
{background,task_id,message}；pipeline.ingest_file 新 kw-only
`stop_check`（批次邊界輪詢 registry stop flag→🛑 優雅停、前綴保留、
STOPPED+已寫塊數）；terminal notify 鉤零改動全吃到（TG+⏳ 面板）；
工具描述教「勿輪詢、一次 get_task_result」；tests +9
test_add_doc_background（閾值×3＋升格×3＋pipeline×3）＝9 passed；
pyproject 0.9.133.4 > **0.9.133.5**）；
133.6 =
**iPhone Web App 預覽逃逸修（user「開了 open ui 網頁就回不去 iphone
的 web app」）＝iOS standalone 無返回鍵、window.open 把 app 帶走；
`renderSandboxFiles` 對 .html 多生成 👁 預覽 chip → `openHtmlPreview`
應用內全屏 modal＋**sandboxed iframe**（web=/download、GUI=file:///；
server 端 133.5 CSP sandbox＝雙重隔離）✕/Ecs 關閉即回聊天；CSS
modal＋i18n htmlPreview×4；tests test_web_download **46 passed**
（＋jsdom 行為級 open/✕ close＋最小 _run_node_jsdom_inline）；skill
庫藏 browser-preview.md 同步；pyproject 0.9.133.5 > **0.9.133.6**）；
133.7 =
**Sessions 即時摘要欄（user「對話計錄管理提供 summary」）＝
SessionManager.list() 零額外 IO 算 summary_first_user/last_assistant/
chars（無 LLM；[ 開頭 marker 不算主題、[error] 不算最新）＋GUI/api
與 list_chat_sessions 同吃；前端 Sessions 表加 thSummary 欄（i18n
×4）＋每列 💬/🤖 兩行（line-clamp 2）；colspan 7；tests
test_sessions_summary **9 passed**＋回歸 89 passed（colspan 斷言同
步）；pyproject 0.9.133.6 > **0.9.133.7**）；
133.8 =
**Chat 頁 ✏️ 改當前 session 名（user「在對話頁修改當下 session 名稱」）
＝工具列 picker 旁 #chat-rename 按鈕（data-i18n=sessRename 零新鍵）→
renameCurrentSession()：無檔先 saveSession() 建檔→重用
sessionsRename（prompt＋update_session_title＋active-title lockstep＋
雙表刷新）零重複；tooltip=sessRenamePrompt；tests
test_session_rename **25 passed**（＋2 guards）；node --check OK；
pyproject 0.9.133.7 > **0.9.133.8**）；
133.9 =
**roll 半空定格修（user「跑到邊 roll 停半空中」＝133.2 末格 bug）＝
_rollfix 量測：roll cells 3/4 落幀 bottom=110（離 y=116 六px）＋
steps(4,end) 只到 -450＝forwards 定格懸空中間格（rollbreak 被拍狗
/情緒 timer 清掉即狗凍懸空）；修①sheet 幀 3 貼地重切（110→116）②
CSS to -750px/steps(5) 五整格落點、定格=第5格貼地③bust 13330
（13320 全退）；tests test_dog_runner **18 passed**（＋
test_roll_lands_on_ground）；pyproject 0.9.133.8 > **0.9.133.9**）；
133.10 =
**⏳ 面板行內執行 log（user「點執行中的背景任務無法看到執行的 log」
＝detach 後前端輪詢停＋registry 沒記 turn_id＝log 斷線）＝
①`task_runner.attach_turn`（detach 時綁 turn_id）②任務行 ⚙ 展開：
running=live 增量 poll_turn（2s tick 刷、open set 保持、cursor 增量
＋delta guard）、terminal=一次回放、無 turn_id（ingest）=誠實 note
③finalize 把 events 寫進任務 session msg.turnLog（空流寫 []；LRU
逐出後回看不斷）④CSS task-log-*／i18n ×4×3；tests test_gui_task_
panel +4＋test_turn_bg_detach +2 斷言＝41 passed；pyproject
0.9.133.9 > **0.9.133.10**）；
133.2 =
**iPhone 打滾狂閃修＋AI 資產真去背＋project_backup 收案**（user
「跑到邊的打滾在 iPhone Web App 狂閃動、有些圖沒有去背」：
①dog-roll-play 改單向前進 steps(4)——舊 steps(8)=75px 半格撕裂＋
0→-600→0 倒帶重播＝狂閃，sheet 幀序 [0,1,2,3,3] 末格即翻正腳著地；
②#dog-run-layer .dog-skin 加 translateZ(0)+backface-visibility:hidden+
will-change:background-position 強制 GPU 合成層（iOS bg-position 動畫
混層閃爍）；③8 張 _bw 資產 PIL flood-fill 去背真寫入（原檔全無 alpha、
備份 gui/assets/_bw_orig/）＋URL bust ?v=13310→13320；④project_backup.py
重建完成 414 行（backup/list/restore round-trip 驗證）；
pyproject 0.9.133.1 > **0.9.133.2**）；
133.1 =
**小狗狀態機資產更新＋打滾翻正修正**（user 提供 8 張 AI 產圖；打滾
dog-roll-play 改雙段 keyframes 0→-600→0 肚朝天→腳著地 .9s；8 張新
資產落位 gui/assets/（happy/lick/lie/lift/sleep/dizzy/throw/yawn），
GIF 直掛 background animation:none、PNG 定格；dog-licking/lying/
sleeping/yawning/throwing/carried 六皮膚 CSS 全換；新皮膚 dog-dizzy
（頭暈轉圈 GIF）＋dog-happy（開心蹦跳 PNG＋bounce）；SKIN_CLASSES
+2；_dogDizzy 換 dog-dizzy、onTap comfort 換 dog-happy；URL bust
?v=13310；tests test_dog_runner 4 過時守衛更新＝15 passed；pyproject
0.9.133.0 > **0.9.133.1**）；
133.0 =
**歷史氣泡可調顏色＋透明度**（user「歷史文字框提供可調顏色和透明度」；
工具列 🎨→panel：助理/user 各一組 color picker＋透明度滑桿 30–100%
＋恢復預設；`.chat-msg.user/.assistant` 背景改消費 `--bub-user-bg/
--bub-user-fg/--bub-asst-bg/--bub-asst-border`（#chat-log 定義、
預設=主題 var）＝既有氣泡即時重上色；_hexToRgba（hex/rgb 都吃）、
localStorage 'chat-bubble-style'={asst:{c,a},user:{c,a}}、
_applyBubbleStyle（assistant 邊框同步溶入、未設=removeProperty）、
syncInputs 從 computed --bg/--primary 取預設；bindBubbleStyle 冪等；
i18n ×4×4；tests test_bubble_style_ui ×5 passed；pyproject
0.9.132.9 > **0.9.133.0**）；
132.9 =
**剪貼簿按鈕三處**（user「文件框剪貼簿／對話框複製貼上／歷史複製」；
①notes `#ne-body` .ne-clip-row：📋/✂️/📥（無選取=整份、剪下派 input
觸發 autosave、貼上插游標）；②chat input 工具列 📋/📥（複製草稿、
貼上到游標）；③氣泡 assistant+user 都有 📋（_bubbleCopyBtn 同 share
footprint）；_clipWrite=clipboard.writeText→execCommand fallback、
_clipRead=readText only（失敗誠實 toast「改用鍵盤」）；bindClipboard
_bindClipboard._done 冪等、bindEvents 早綑第 4 位；i18n ×4×10；
tests test_clipboard_ui ×5 passed；pyproject 0.9.132.8 >
**0.9.132.9**）；
132.8 =
**小狗狀態機三連發**（user「快到視窗邊打滾(轉頭)／來回跑太多次要
休息(停下來喝水睡在視窗中間貼底線)／LLM 回合數太多會頭暈要安慰」；
①moveBurst 步進撞 clamp→rolling 打滾 .7s＋dir=-dir 轉頭續跑；
②S.legs>=6→歸零→drinkAndSleep＝lick 舔水代喝水 1.2s→walk 'center'
（towardCenter 擴 'center' 自動方向）→趴→4s→enterSleep 中間貼底線；
③pollTurnStream 數 llm_round==8→_dogDizzy→_dogDizzyHook 頭暈態
(spin/tilt wobble 300ms)→拍拍＝comfort(belling 1.4s)→settle、
無人理 15s 自癒；towardCenter 三態 true/'center'/'center'-auto；
tests test_rolls_rest_and_dizzy_wiring ~20 守衛＋dog_runner 全套綠；
pyproject 0.9.132.7 > **0.9.132.8**）；
132.7 =
**出廠方向往中間跑**（user「出生方向要向中間」＝出生在 CSS left:-160px
螢幕外但進場方向隨機＝一半機率朝左跑出畫面；修＝moveBurst 第三參
towardCenter：強制 dir=+1＋「x<中心槽位且 ≤5s 就繼續」過中點才交棒；
openWalk 進場改固定奔跑進場（過中點後接 50% walk→run 隨機漫遊）；
emoji fallback openIdle 也置中（原 -160px 座著永遠看不到）；
tests test_dog_entrance_center ×6＋regression 12 passed；
pyproject 0.9.132.6 > **0.9.132.7**）；
132.6 =
**index no-store＋/playpet 實證正常**（user「狗不會跑」＝真 Firefox
實證 dog 有出生且會動（dog-walking 1326px→belling idle 132.5 六挑
生效）；真因①回覆落地收狗（OCR 連環回合窗口內按＝立即被收）②長壽
分頁停著昨天的 app.js?v=0.9.132.3（index no-cache+ETag 但分頁不
重新驗證＝版本鍵到不了）→根治＝`/`+shim 改 no-store（每次導航新
index→新 asset URL；資產維持 no-cache+ETag）；tests web_server
+test_index_and_shim_are_no_store＝60 passed；**附帶**：11:13 OCR
測試回合把 llama-server 全清了→看門狗自癒（name-pin bat 重生）；
pyproject 0.9.132.5 > **0.9.132.6**）；
132.5 =
**小狗五套庫藏動畫全接線**（user「全部」＝拖拽提起＋甩飛／深睡鎖定
＋喚醒鏈／tap 情緒；三真 bug 修：fling 速度＝最後兩 pointermove 差
向量（放開點幻速度廢）＋樣本追蹤收斂單一 listener（雙 listener 互相
覆寫 prev==last→vx 恆 0）＋抓起即停 move/flourish 步進鏈＋睡眠
infinite→1 forwards 凍結 Zzz；拖甩＝pointerdown dragFrom＋
pointermove dx+dy>14→enterCarry（提著跟游標）＋pointerup onUpRelease
唯一裁決（dtUp<160 且 |vx|>1.1→throwFly sin 弧 110px→putdown→run
chase→ball→settle；否則 doPutdown）、onTap carried 分支純回退；
深睡＝settle 掛 T('sleep',enterSleep,SLEEP_GATE())（連續靜置 120s、
每輪重掛、guard sit/lie）→clearAllT 鎖死＋💤；點睡→stretch→yawn→
settle；tap 情緒＝idle 池 4→6 挑（＋tilt/belly）＋bone→lick 鏈；
jsdom driver main.finally 250ms 強制 exit（2min sleep timer 钉住事件
迴圈＝每條掛滿 30s 的慢病修掉＝1-16s）；tests +3（wire 守衛×~20＋
fling 鏈 jsdom 8.1s＋深睡鎖定 jsdom 15.6s）＝dog_runner 逐條全綠；
pyproject 0.9.132.4 > **0.9.132.5**）；
132.4 =
**嵌入伺服器搬回 AMD**（user「4b 為何跑在 intel gpu」＝
`--device Vulkan0` 是列舉槽位別名非硬體身分證——10/14 開機 Iris Xe
排第 0 → 4B 4.84GB/gemma2 1.31GB 全住 iGPU UMA（local/shared 同額
特徵）、AMD dedicated 只剩 183MB；ingest 時單發探針 26.5s→local
兩次 120s ReadTimeout→embedder 掉 OpenRouter/DeepInfra；telegram
任務 19e7e077 硬超時自然停；修＝三支 bat 按名字解析
（--list-devices ^| findstr Radeon → VulkanN；看不見→Vulkan0
fallback）＋kill→重啟；驗證＝AMD dedicated 4.98GB／iGPU 2.6GB／
探針 26,500ms→38ms／gemma2 185ms；重跑 ingest＝telegram 重送
「匯入qdrant」即可（hash 去重）；pyproject 0.9.132.3 >
**0.9.132.4**）；
132.3 =
**背景任務結果寫回原對話**（user「前端進入背景執行的工作完成時也要
將結果寫回 session」＝☑ 背景執行結果只落 task-<id> session＋⏳ 面板、
原對話只有 taskSubmitted；設計＝**前端單一寫入者**（server 端 append
會被前端整檔 autoSaveChat 覆寫吞掉）＝寫回走前端存檔鏈零競態；
後端 task_runner 新 `origin_sid` 欄＋set_origin、_run_once 接受
origin_sid、submit_background_task 新 chat_sid 參數；前端 bgWatch
Map＋8s 輪詢→終態 get_background_task(4000)→append 泡泡＋msg.taskId
標記＋bgResultBack toast＋autoSaveChat、failed→error 泡泡、未知 id
5 miss 丟棄、清空停錶；**reconcileBgTasks 掛四個 session 綁定點**
（applyRestore/newSession/loadSessionById/saveSession 首建）＝
origin_sid===本 session 的任務（running 或終態）重載/關頁再開都能
補寫、msg.taskId 冪等去重；i18n bgResultBack ×4；tests 新
TestBgResultWriteback ×6＋args 契約更新＝相關 155 passed；
pyproject 0.9.132.2 > **0.9.132.3**；RESTART required）；
132.2 =
**stop 鏈泄漏修復**（user「一直出現 🛑 已強制中止」＝132.1 的
detach 把 registry stop flag 鏈進共用主 Agent `_stop_check` slot 且
永不拆除、flag 又刻意不清（殭屍可達）＝任何升格任務被 🛑 一次→
之後每個新回合第一邊界即被誤殺；修＝拆鏈接（detach 源碼守衛：
`_stop_check` 只准出現在註解行）＋每回合專屬自熄看門線程
turn-bg-watch-<turn_id>（0.5s 輪詢 flag→signal_turn_stop(turn_id)
只打本回合 scoped event、終態即退）＝⏳ 面板 🛑 照樣有效、零共享
污染；tests 行為級+源碼守衛＝33 passed；pyproject 0.9.132.1 >
**0.9.132.2**；RESTART required 清在跑進程的污染 slot）；
132.1 =
**長回合自動轉背景**（user「長程作業會lock，像現在」＝兩段式回合送出鈕
鎖到真終態、124.8 只救了按 🛑 的路＝沒按中止的長回合整程鎖死；修＝
gui/api.py `detach_turn_to_background(turn_id)`：running 回合冪等升格
task_runner registry（source="chat"、sid=task-<id>、終態/未知誠實拒絕）
＋registry stop flag 鏈進 agent._stop_check（⏳ 面板 🛑 停得到、
turn-scoped stop 不受影響）＋`_work` 終態 `_finalize_detached_turn`＝
寫 task-<id> session＋end_task 觸發既有 notify（工作絕不殺絕不重做）；
app.js pollTurnStream `_TURN_BG_AFTER_MS=60000` 門檻→detach→同一句
taskSubmitted 泡泡＋turnBgDetached 狀態＋onRelease 解鎖＋輪詢不斷＝
真終態照常落泡（chat/task session 雙記錄）；**renderChat 全量重建會
抹掉進行中回合的 ⚙ 面板**＝liveExecPanels Set＋尾端 re-mount 非終態
面板＋poller 三出口標記 dataset.terminal；i18n turnBgDetached ×4；
tests 新 test_turn_bg_detach ×10＋既有守衛窗口放寬
（turn_panel 6000→8000 ×2／gui_task_panel 4000→8000＝127.x 漂移）＝
相關合跑全綠＋node --check OK；pyproject 0.9.132.0 > **0.9.132.1**）；
131.0 =
**小狗行為狀態機重建**（user 提供 20 支 `std_*.gif`（C:\Users\humphery\images\）
各 2 幀 512×512 彩色紙感底＝_build_dog4_sheets.py 新 remove_background_std
邊界區域生長泛洪（TOL=32 相鄰色差區域生長吃得下漸層彩底與白雲、卡通黑輪廓
擋得住＝127.24 白底泛洪教訓的彩色紙感版）＋金字塔播種（1/4→1/2→全解析度
向量化；純 BFS 30s 逾時棄）→20 張 sheet（CELL 150×120 不變）；朝向＝多數
原生朝左需鏡射、spin/lift/tilt/tail 不鏡射；壞幀修序 bite/bone→[0,0]、
lie→[1,1]、walk→[0,0] fit；**putdown 整支換 std_tilt f1**（std_putdown f1
白色碎片）＝putdown 幀兼作 idle hub 凍結坐姿；app.js 18 行為狀態機
（移動 run/walk/hop/spin｜互動 lift/putdown/throw/belly/ball/bone｜情緒
bark/bite/lick/yawn/tilt｜休息 sleep/lie/roll/tail/stretch）＋IDLE 中樞
（.dog-stopped 凍結 putdown f1）＋閒置階梯（30s 情緒 bone→lick／2min lie／
5min 鎖定 sleep）＋喚醒鏈（sleep 點擊僅 wakeUp：stretch→yawn→idle）＝
tap=隨機情緒+❤️粒子、3 摸/4s=happy（run/hop/spin/tail）、6 摸=angry
（bark 1300→bite 750→soothe 3000→belly 1700→idle；**累積計數
S.petTotal**——S.pets 三摸歸零下 petTotal 累積才到得了 6）、拖 >14px=
carry（mousemove left=clamp(clientX−75)/bottom=clamp(h−clientY−60)）、
放開 dt<160ms 且 |vx|>1.1=throwFly（sin 弧 110px、14 步 40ms、落地
putdown 幀→run chase→ball）否則 putdown；計時器全收斂 layer._dogT
（keyed T/clearT/clearAllT）、_dogRunStop 清 timers+listeners+opacity
320ms remove；**sprite 探針移到 _dogRunStart 尾端**（所有 const 初始化
之後——同步 onload 不再撞 TDZ）；CSS dog 區全段換新＋20 條 ?v=13100
bust；tests test_dog_runner.py 重寫＝Source 6 守衛（20 sheet+4 舊 sheet
反守衛+狀態機+CSS+URL+sendChat 掛點）＋jsdom 6 行為級（start 冪等/
tap 情緒/三摸 happy/六摸 angry 鏈/**MutationObserver 記 class 變化**
——jsdom 忙機 timer 漂移下時間取樣漏 750ms bite 窗/拖拽 carry
325px+508px→putdown/甩飛 throw→chase）＝**12 passed**+node --check OK；
**生產 bug 順手修**：dragFrom 缺 y 欄位（dy=NaN→拖拽提起永不觸發）；
pyproject 0.9.130.2 > **0.9.131.0**）；
130.2 =
**小狗提起模式 CARRY**（user「怎麼樣才能把小狗提起來」→選定點一下
提起懸空跟著走、再點放下＝**取代 130.0 摸摸=lift→putdown 瞬間反應**：
點坐姿小狗→lift one-shot 720ms→_dogCarryOn：skin `dog-carried`
（凍結 lift sheet 騰空幀 f1 -150px＋sway 0.9s）＋runner
dog-following/dog-carrying（後者補 bottom transition）＋專屬
mousemove 雙軸跟隨（left/bottom 置中 clamp＝狗懸游標正下方，下次
點擊必中＝天然 toggle）；再點＝_dogCarryOff：bottom 歸 4px 落地→
putdown→settle；pickup 重置三連摸 streak；睡著點擊 bite 語意不變；
_dogRunStop 清 _dogCarryMouse+旗標（回合中提著走＝乾淨全拆）；
CSS +3 block；tests dog_runner 8→10（+source guards＋jsdom 行為級
pickup/riding 225px/508px/floor/settle）＝**順手修 130.1 slash 測試
插入錨點吃到 v0130_0 中段致其 CSS 斷言尾切離成孤兒的結構錯置**；
pyproject 0.9.130.1 > **0.9.130.2**）；
130.1 =
**本地聊天指令 /playpet /endpet**（user 要求＝_sendChatCore 最頂端
**pending-upload merge 之前**攔截 `^\/(playpet|endpet)\s*$`：純
本地分流不送 LLM、清輸入框、按 dog-run-layer 存否分流呼冪等的
_dogRunStart/_dogRunStop（回合中 /endpet 與終態 callback 無害重入
相容）、toast-only 回饋（假 assistant 泡泡會污染 LLM 歷史）；
i18n petComeOut/petAlreadyOut/petGoesRest/petNotOut ×4；tests
+test_slash_commands_playpet_endpet（正則/四分流/順序守衛/i18n
×4×4）＝dog_runner 8 passed；pyproject 0.9.130.0 >
**0.9.130.1**）；
130.0 =
**本地聊天指令 /playpet /endpet**（user 要求＝_sendChatCore 最頂端
**pending-upload merge 之前**攔截 `^\/(playpet|endpet)\s*$`：純
本地分流不送 LLM、清輸入框、按 dog-run-layer 存否分流呼冪等的
_dogRunStart/_dogRunStop（回合中 /endpet 與終態 callback 無害重入
相容）、toast-only 回饋（假 assistant 泡泡會污染 LLM 歷史）；
i18n petComeOut/petAlreadyOut/petGoesRest/petNotOut ×4；tests
+test_slash_commands_playpet_endpet（正則/四分流/順序守衛/i18n
×4×4）＝dog_runner 8 passed；pyproject 0.9.130.0 >
**0.9.130.1**）；
130.0 =
**小狗動作庫 13→16 套＋摸摸反應系統**（user 提供 3 支新 GIF：
shih_lift_c3 / shih_putdown_c3 / shih_bite_c3——各 4 幀 512×512
不透明白底卡通西施，逐格獨立生成再組 GIF＝**畫面只有狗的無手版**
（prompt 禁棍棒/人手＋體型毛色鎖定）＝_build_dog3_sheets.py 新增
三張 sheet（union 模式）：dog_lift_sheet [0,1,2,3] 蹲→躍起→撲上→
落地＝「被提起」（600×120）、dog_putdown_sheet [0,1,2,3] 降落→
觸地→坐好＝「被放下」（600×120）、dog_bite_sheet [0,1,0,3] 瞪視→
撲咬乒乓→嘟嘴生氣＝「要咬人」（驚訝 f2 排除，600×120）；
**朝向＝紅項圈質心法**（項圈在脖子上＝臉那一側；方法先在既有
run/bark sheet 已知全朝右與舊 GIF run/bark 已知原生朝左雙向驗證
成立）＝**lift/putdown 原生朝右不需鏡射（c3 系列與舊 13 支朝向
相反）、bite 原生朝左需鏡射**；app.js 新 `playOneShot` 反應引擎
（清十類 class＋七項 timer→加 class 播 720ms 一次性）＋三條行為
鏈：①摸摸坐姿小狗＝lift→putdown→回 idle 輪替（提起又放下）
②三連摸興奮＝`_dogFollow` **立即啟動**＋lift（skin 層）在追逐中
同步播放（skin 與 runner 動畫獨立層故可並行）→抵達落地 putdown→
settle 坐下；`petRounds` 計數器＝**每逢第二次興奮 streak 小狗改為
生氣咬人**不放跟隨 ③點醒睡著小狗＝bite 先咬一口→再喚回 idle；
反應播放中再點＝忽略防重入；_dogRunStop 清十二項 timer（＋
_dogLiftTimer/_dogPutdownTimer/_dogBiteTimer）、_dogFollow 清七項；
CSS +3 block（.dog-lifting/.dog-putdown/.dog-biting 各 600×120
0.72s steps(4) 1 forwards）＋16 條 URL ?v=13000 bust；tests
test_game_action_set_v0129_0 改名 v0130_0 全面擴充＋jsdom 跟隨
等待延長 2000ms（putdown 鏈先於 settle）＝dog_runner 7 passed＋
合跑 web_server 66 passed；pyproject 0.9.129.0 > **0.9.130.0**）；
129.0 =
**小狗動作庫 8→13 套＋行為模擬互動系統**（user 提供 5 支新 GIF：
shih_ball_c / shih_bark_c / shih_lick_c / shih_lie_c / shih_spin_c
各 4 幀 512×512 不透明白底卡通西施＝_build_dog3_sheets.py 新增
五張 sheet（**全部 union 模式**＋寬度守衛
`scale=min(TARGET_H/uh,(CELL_W-4)/uw)` 防 lie 158>150 溢格）：
dog_ball_sheet [0,1,2,3,2,1] 撿球乒乓 900×120、dog_bark_sheet
[0,2,3,2] 吠叫坐姿（f1「WOOF!」對話泡鏡射後文字翻轉＝**永久
排除**，600×120）、dog_lick_sheet [1,2,3,2] 舔嘴特寫三連（f0 坐
較遠排除，600×120）、dog_lie_sheet [0,1,2,3,3,3] 走進→趴下→
入睡（末幀凍結 Zzz，900×120）、dog_spin_sheet [0,1,2,3] 轉圈
（f0 朝左/f3 朝右＝**180° 轉身本身就是敘事**，600×120）＋
**行為模擬互動系統**：①摸摸（click 小狗→❤️💖✨ 粒子
_dogParticles 錯開 420ms 上浮消散；睡著的狗被點＝_dogIdleWake
喚醒）②三連摸興奮（4s 內三摸→_dogFollow：先快照 computed left
再移除 sprint 動畫防瞬移→mousemove 跟隨 120ms tick→抵達/游標
靜默 2.4s/上限 6s→settle 坐下）③冷落（idle 8-12s 無互動→
dog-lying 趴睡＋💤 粒子）④idle 四向輪替（tongue/scratch/yawn/
lick 每 4.5-6s 換手＋防同項連任）；花式五選一（FLOURISHES：
jump/hop 550ms、fetch 850ms、bark/spin 760ms）＋walk break 60%
照舊；_dogRunStop 清九項 timer＋mousemove listener、CSS +7
block＋13 條 URL ?v=12900；tests test_game_action_set_v0128_0
改名 v0129_0 擴充＋新 jsdom 行為級互動測試（點擊粒子/三連摸
following/抵達 settle/粒子時序）＝7 passed＋合跑 web_server
59 passed；pyproject 0.9.128.1 > **0.9.129.0**）；128.1 =；128.1 =
**出場動作隨機化**（user「動作不要每次都從Run開始」＝app.js
_dogRunStart 新 openWalk()：sprite 確認後 **50% 先散步**（.dog-walking
1.8-4.5s 慢步頻 1.2s steps(6)）再起跑、50% 直接開跑——probe.onload 與
cache 命中兩路都接線；花式 timer 觸發時 **清掉未完散步**
（clearTimeout(_dogWalkEndTimer)＋remove dog-walking）防堆疊；
_dogRunStop 原本就清 _dogWalkEndTimer 免改；CSS 零改（walk block
128.0 已有）＝tests +3 斷言（openWalk 定義/雙路呼叫/花式清散步）＝
6 passed＋合跑 web_server 65 passed；pyproject 0.9.128.0 >
**0.9.128.1**）；128.0 =
**卡通西施 8 動作全套**（user 提供 4 支新 GIF：shih_walk_c / shih_hop_c /
shih_yawn_c / shih_tail_c——各 4 影格 512×512 白底，seed=42 同款扁平風）
＝_build_dog3_sheets.py 擴充：GIFS 改 `(file, mirror)` 二元組＋
`load_gif_frames(mirror=)` 參數——**tail 不鏡射**（追尾巴各幀朝向不同
側面/背面/後視，整組鏡射無意義，其餘 7 支照舊全鏡射朝右）＋8 張 sheet
＝run/jump/sit/tongue 維持原樣＋**scratch 換 tail 素材**（[0,1,2,3,1,2]
union 站→彎→咬尾→站；bone 啃骨頭保留在 tongue）＋walk [1,2,1,2,1,2]
**fit 模式**（f0/f3 直立看鏡頭捨棄、f1/f2 側面走姿面積等化）＋hop
[0,0,2,3,3] union（蹲→蹲→起跳→高點→高點，保留跳躍位移）＋yawn
[0,1,2,3,2,1] union（大呵欠→瞇睡→清醒乒乓）＝app.js 狀態機三改：
①花式 4-8s **roll/hop 隨機二選一**（`? 'dog-jumping' : 'dog-hopping'`，
同 570ms 一次性契約）②花式後 **60% 走路小段**（1.2-2.7s 後 .dog-walking
2.5s 慢步頻 1.2s steps(6)）③idle **三選一**（tongue/scratch/yawn 陣列
隨機）＝_dogRunStop 清六 timer（+_dogWalkTimer/_dogWalkEndTimer）＋CSS
+3 block（.dog-hopping 750×120 0.55s steps(5) 1 forwards／.dog-walking
900×120 1.2s steps(6) infinite／.dog-idle-yawn 900×120 1.8s steps(6)
infinite）＋八 URL ?v=12800 bust；tests test_game_action_set_v012723
改名 v0128_0＋assets +3＝**6 passed**＋jsdom 行為級＋合跑 web_server
**65 passed**；pyproject 0.9.127.28 > **0.9.128.0**；127.28 =
**run sheet 防閃爍重切**（user「跑的時候大小不一致看起來在閃爍」＝
shih_run_c 來源 GIF 幀間取景不一致：軀幹高 239/318/237/270——f1
整隻畫大 ~30%、各幀長寬比 1.13-1.53 散開，任何單一縮放都救不了）
＝_build_dog3_sheets.py 新 **fit 模式**：run 改用形狀最一致的
3 幀 [0,2,3]（ torso 高 239/237/270）循環 [0,2,3,0,2,3]＋**每幀
bbox 面積等化**（scale=sqrt(median/area_i)）＋共同縮放入格＋底線
y=116 對齊＋水平置中（高度差收斂 <9%）；roll/sleep/bone 維持
union 模式（翻滾/趴下/坐姿的尺寸變化是自然動作非瑕疵）；CSS 五
URL ?v=12728 bust；tests 不變＝6 passed＋合跑 65 passed；127.27 =
**shih-tzu 四 GIF 全面改版**（user 指定 4 支 GIF：shih_run_c /
shih_roll_c / shih_sleep_c / shih_bone_c——各 4 影格 512×512
**不透明白底**）＝新 `_build_dog3_sheets.py`：seek 取幀＋**全幀水平
鏡射**（GIF 原生朝左、CSS dog-sprint scaleX(1)/(-1) 語意需要朝右
原生 sprite——放大比對現行 run sheet cell0 確認朝右後定案）＋
**邊緣泛洪只去白底**（白毛不可全域移除＝127.24 教訓；卡通黑輪廓
完整包圍白毛故泛洪穿不進）＋≥40px 分量過濾（保住啃骨頭動作線）
＋1px 侵蝕去光暈 → 依 CELL 150×120／union bbox／內容高 104／底線
y=116 慣例覆寫五張 sheet：run=[0,1,2,3,2,1] 乒乓 gallop、jump=
roll 翻滾 [0,1,2,3,3]、sit=sleep 趴下漸睡 [0,1,2,3,3,3]（**末幀
凍結＝睡著＋Zzz**）、tongue/scratch=bone 啃骨頭 [0,1,0,1,0,1]
（排除站姿 f2 保坐姿一致）；JS 狀態機/app.js/tests 零改動、CSS 五
URL ?v=12727 bust；**還原 30 幀版＝重跑 _build_dog2_sheets.py**；
tests 不變＝6 passed＋合跑 65 passed；127.26 =
**退回 30 幀版**（user「這次動畫不好，退回上一版」＝127.25 四姿勢
試用不採用）＝重跑 _build_dog2_sheets.py 從 dog_anim 30 影格覆寫
五張 sheet（尺寸/慣例 byte 同 127.24 產出）＋CSS 五 URL
?v=12726 bust；_build_dog_trial_sheets.py 留檔不刪（trial 管線
可再利用）；tests 不變＝6 passed＋合跑 65 passed；127.25 =
**四姿勢小狗試用**（user 提供 `C:\Users\humphery\images\` 4 張
1024×1024 RGB 姿勢圖 sit/stand/jump/headtilt（非連續動作循環）＝
_build_dog_trial_sheets.py 全新去背管線：**全域移除藍色調
（b−r≥12）＋邊緣泛洪只管白底**——blob 藍實為 b−r≈35-40 但
b−g≈0-5 的青色（首版 b−g≥3 條件誤殺一半網點漸層帶）；狗毛色全
中性（b−r≈0）故全域藍移除安全、白毛與白底不可分（皆 253-255）
故白只能靠泛洪——**127.24 教訓的精確適用範圍＝白色不是藍色**；
ring 移除階段曾誤吃狗輪廓（白毛也被計入封閉 bg）已棄）＋
≥250px 連通分量過濾（丟 dither 網點/影子碎屑、保留動作線）
＋1px 侵蝕去光暈 → 依 _build_dog2_sheets.py 慣例（CELL 150×120、
union bbox、內容高 104px、底線 y=116）**覆寫五張 sheet**：
run=[stand,stand,jump,stand,stand,jump]（彈跳節奏）、jump/sit=
單幀重複（sit 末幀＝正面坐 ✓）、tongue/scratch=headtilt ×6
（靜態姿勢 idle）；JS 狀態機/app.js/tests 零改動、CSS 僅五 URL
?v=12725 bust＋sheet 存檔 compress_level=1（無損加大、誠實跨過
test 30KB stub-偵測門檻）；**還原＝重跑 _build_dog2_sheets.py**
（dog_anim 素材源不動）；tests 不變＝6 passed＋合跑 65 passed；
127.24 =
**小狗去背修復重切**（user 自行修復 dog_anim 30 影格——原白色去背把
身體內部白區（白毛/胸口/臉部白斑）誤挖成透明＝**邊緣泛洪填補**：與
邊緣相連的透明＝真背景、內部洞＝誤挖回填白色實心，每張回填約
6.7k-38k px，零費用）＝_build_dog2_sheets.py 重切五張 sheet（切圖
邏輯零改）＋CSS 五 URL ?v=12724 bust；enclosed-transparent 驗證
run/jump/tongue=0、sit=4/scratch=16（輪廓圍出的腿間細縫非誤挖）；
tests 不變＝6 passed＋合跑 65 passed；127.23 =
**五動作小狗全套**（user 提供 `C:\Users\humphery\dog_anim\` 5 動作
×6 影格 512×512 透明 PNG（run/jump/scratch/sit/tongue＋GIF＋串圖，
$1.16）＝_build_dog2_sheets.py 逐影格切圖組 sheet（union bbox 統一
縮放、內容高 104px、底線基線對齊；**scratch 4 張烤入「FRAME N of
6」文字＝底部間隙帶自動裁除**；**jump_f4 AI 瑕疵狗戴墨鏡＝跳過改
5 幀**）→ 五張 sheet：run/sit/tongue/scratch 900×120（6 幀）＋
jump 750×120（5 幀），CELL 150×120（狗更大）；**結構重構＝外層
.dog-runner（跑位＋翻面）＋內層 .dog-skin（換 sheet）**——狀態切換
不重啟 sprint 動畫（舊結構換 class 會瞬移回起點）；狀態機＝跑步中
4-8s 隨機插 jump 花式一次 → 12s 停止凍結 → sit 0.9s steps(6)
forwards 坐下（末幀恰為正面）→ 隨機 idle 迴圈（tongue 正面吐舌喘
氣 1.5s 或 scratch 抓癢 1.2s）；_dogRunStop 清四 timer（sit/idle/
jump/jumpBack）；CSS 五 URL ?v=12723 bust；舊 turn/sitf sheet＋
dog2_raw_* 刪除；tests dog_runner 更新＝6 passed＋合跑 web_server
65 passed；127.22 =
**遊戲級多視角小狗**（user「不連續不精細、圖大一點、動作多一點、
連貫性多一點、可轉正面像遊戲動畫」＝_gen_dog_set.py recraft 三張
sheet（跑步 13 幀 4:3＋轉向 3 幀 16:9〔21:9 recraft 不支援〕＋正面
坐姿 3 幀 16:9，統一角色 prompt、各 2 候選目視挑選 $0.056；跑步重
生成一輪強化腿相位差異——首版腿不動＋次版哈士奇角色斷裂皆棄）→
_slice_dog_set.py **投影分段**（AI 不對齊網格：固定格切出 16 段殘
影＋雜點→y 投影切列帶＋x 投影切個體＋≥30px 過濾＝13/3/3 乾淨）→
CELL 76→120px 統一幀高 100＋基線→dog_run_sheet 1560×120／turn/
sitf 360×120；CSS dog-sprint 改來回跑（50% 處 scaleX(-1) 翻面朝左
＝橫向卷軸；emoji 分離 dog-dash 單向避 transform 打架）＋steps(13)
0.78s；JS --dog-dur 10-16s、12s 停→.dog-turning（steps(3) 1
forwards 轉正面一次）→500ms 換 .dog-sitting（正面喘氣 steps(3) 循
環）；_dogRunStop 清雙 timer；三 URL ?v=12722 bust；舊 sheet+落選
raw 刪除、選中 raw 留檔；tests dog_runner 更新＝6 passed＋合跑
65 passed；127.21 =
**小狗 AI 產圖＋第二動作**（user「圖片可以請 AI 製作嗎？圖很簡陋、
動作單調」＝ImageGenEngine(recraft) live 產 8 幀 gallop sprite（2 張
挑最好 $0.014）→_slice_ai_sprite.py flood 去白＋等分切 8＋統一幀高
＋基線（坑：x0 忘加格偏移 8 幀疊一格）＋坐姿 2 幀 sheet（首版風格
斷裂重生成 $0.007）→12s JS 凍結位置+dog-sitting class→steps(2)
喘氣；_dogRunStop 清 timer；CSS 圖 URL ?v=12721 bust；tests
dog_runner +1＝6 passed；127.20 =
**精靈式跑步動畫**（user 要 Windows 舊精靈多幀動畫＝_gen_dog_sprite
.py PIL 程式繪 8 幀側面 gallop cycle（四腿相位擺動＋蹦跳＋搖尾＋
吐舌；毛色取樣自 dog_icon.png 照片）→dog_sprite.png 608×76＋
CSS steps(8,end) 逐幀播放＋dog-sprint 位移；__dogSpriteOK 一次性
探測 fallback emoji+dog-bob；tests dog_runner 更新＝5 passed；
127.19 =
**運算小狗**（送出提問→dog_icon.png 小狗在視窗底部奔跑
（dog-sprint 左右橫越 5-9s 隨機圈速＋dog-bob 蹦跳；emoji fallback；
pointer-events:none；reduced-motion 靜態；冪等）→回覆落地/終態/bg
detach 淡出消失＝_dogRunStart/_dogRunStop＋#dog-run-layer CSS＋
_sendChatCore 三掛點；tests 新 test_dog_runner ×5（含 jsdom 行為
級：冪等單隻+淡出移除）＝5 passed；127.18 =
**全域首次手勢解鎖**（「iPhone 還是播放回覆」＝127.17 解鎖只掛
🎉/🎧 tap、而 toggle 已開（localStorage 恢復無手勢）→ 解鎖沒發生
＝_bindGlobalUnlock（bindVoiceToggles 開頭、四事件一次性
pointerdown/touchstart/touchend/click→unlock）＋_voiceElUnlocked
旗標＝iOS 已解鎖→_speakReplyNow 立即朗讀跳過 overlay；tests
TestGlobalUnlockV012718 ×2＝64 passed；127.17 =
**停止鈕＋iOS 持久解鎖**（⏹ FAB 播放中浮現、_stopSpeech 交還說話
權（iOS→overlay/桌面→mic 重開）＋_ensureVoiceEl 單一 audio
element＋_unlockVoiceEl（🎉/🎧 tap 內 muted play＝element 永久
解鎖）＝speakChatText 同 element 換 src 播＝iPhone 回覆落地即
自動朗讀（查完資料立即出聲）；voiceStop ×4＋FAB CSS；tests
TestStopFabAndUnlockV012717 ×4＝62 passed；127.16 =
**iOS 自動播放手勢化**（截圖破案：錯誤 toast 在回覆落地後＝autoTTS
的 audio.play() 被 iOS autoplay 政策擋（非 mic 權限）＝
_speakReplyForLoop 分流 _isIOS→免持走雙鈕 overlay（🔊播放回覆＝
tap 內播＋🎤開始說話＝tap 內開麥；朗讀完 overlay 再現）＋純
autoTTS 一次性提示 voiceIOSAutoplayHint；overlay onResume 回呼＋
.vlr-box CSS＋voiceResumePlay/Listen/Hint ×4；tests
TestIOSAutoplayV012716 ×3＝58 passed；127.15 =
**iOS 永久拒絕偵測**（「連第一回都失敗」＝iOS 系統層記住拒絕、app 內
永不重問＝_micPermissionState（permissions.query microphone）→
denied 顯示 voicePermissionIOSDenied 四步恢復教學（刪 App→清網站
資料→系統麥克風開→Safari 重開按允許）；toggleMicRecording catch
同分流；ja voiceResume 重複 key 清除；tests +2＝55 passed；
127.14 =
**iPhone 手勢化免持**（user 澄清錯誤在 iPhone PWA＝iOS WebKit
getUserMedia 僅限手勢、timer 重開麥必 NotAllowedError＝_isIOS 分流
（UA/standalone/MacIntel+touch）→ iOS 全屏 resume overlay（tap＝
手勢、每輪一點續行）＋桌面維持 800s timer；_micPermissionMessage
iOS 分支 voicePermissionIOS（設定→隱私權→麥克風）＋voiceResume
×4＋overlay CSS；tests permission 檔擴 127.14 ×5＋127.13 並存＝
24 passed；127.13 =
**麥克風權限 UX**（user 貼 raw UA「not allowed…denied permission」＝
Firefox mic 封鎖狀態＝_micPermissionMessage 教學訊息（NotAllowed/
Security/PermissionDenied→「點網址列 🔒→允許＋記住」×4 語言）＋
🎧 開啟時 _micPreflight（一次性 getUserMedia、拒絕→toggle 滾回）；
tests 新 test_voice_permission ×4＝19 passed；127.12 =
**STT 韌性**（「第 2 回錯誤」真兇＝OpenRouter STT 端點瞬斷 502
provider down（19:58 兩發、20:05 自愈）＝transcribe 拆 _once＋重試
×2（0.5/1.0s 退避）＋備援 provider（TELEGRAM_STT_FALLBACK_API_KEY
缺省吃 SILICONFLOW_API_KEY→siliconflow/v1＋SenseVoiceSmall）＋
gui.api voice_stt 錯誤人性化（provider down→「語音辨識服務暫時
不可用…」）；tests 新 test_speech_stt_resilience ×4＋ui_voice
locked-in 反轉＝73 passed；127.11 =
**免持雙向語音**（🎉 自動朗讀開關＋🎧 免持循環：mic 自開→VAD
靜音 2s 自停→transcript hook 自動送出→回覆落地朗讀 onended→0.8s
重開麥；轉寫失敗/錄音太短/麥克風打不開＝循環自停；後端 TTS 失敗
降級 speechSynthesis（_speakBrowserTTS＋zh voice＋_ttsStrip）；
speakChatText 加 onEnd 第三參；工具列 🔊/🎧 toggle（localStorage
持久＋.btn-loop-on 綠）；i18n autoTtsOn/Off voiceLoopOn/Off
voiceFallback ×4；tests 新 test_voice_handsfree ×8＝相關 106 passed；
閉環 live 驗證＝TTS→mp3→STT 轉回同句；127.10 =
**多檔並行上傳**（批表建好→worker pool 同時飛 ≤3 批 POST
（UPLOAD_CONCURRENCY）；失敗 stopFlag 停新批、在途批跑完不浪費；
pool 落定統一 summary toast；進度條 = done bytes＋Σ在途 slot
（_upSlots loaded/total/xhr/abort）；取消鈕遍歷 abort 全 slot；
⏳ 只第一波；server 端零改（ThreadingHTTPServer＋碰撞後綴本就並行
安全）；tests：parallel_pool_source＋併發水位 driver（30ms 延遲下
12 檔 maxc=3、4×5MB maxc=2）＝相關五套 146 passed；127.9 =
**進度條全程化＋跨主機慢速診斷**（①面板進度條 = 整個選取的 bytes
（`_upAllTotal`+`_upAllDoneBytes`+當前批＝單調不歸零；批/檔資訊進中
繼行 i18n uploadBatch ×4）②`UPLOAD_BATCH_BYTES` 12MB 批上限
（slice bytes 修剪＋while 以修剪長度推進；5 檔常規批
byte-identical、jsdom [5,5,2] 保留）③server 保存行帶實測速率
`(12.3s, avg 156 KB/s)`＝「跨主機速度慢」變量測——同區網用 LAN
URL 直連、跨公網上行頻寬＝天花板、PNG/JPG 已壓縮傳輸壓縮無益；
tests：share_target +2/panel +6/i18n +1/server 速率 ×2；127.8 =
**上傳進度條**（「3 分鐘無反應」經歷＝⏳ toast 2.5s 消失 vs 批 POST 慢
流分鐘級零反饋＋server read body 期間結構性零 log＝「主機端沒有
log」真相；現場真相＝15:07/15:10 兩批已各存 5 檔成功、markers 已在
輸入框（fleet snapshot 實拍）；修＝①XHR upload.onprogress 真位元組
進度＋常駐進度面板 `#upload-progress-panel`（右上卡：批範圍/進度條/
MB/⏸停滯計時/✕取消鈕→abort→紅 toast 帶 N saved；1s 心跳）＋i18n
uploadCancel/Stalled/Flowing/NetErr ×4 ②deadline 放寬
60s+60s/批內檔 cap 10 分鐘（flat 30s=14:42/14:52 兩發 61KB 截斷真兇）
＋server `_handle_upload` 進入即 log `upload: incoming POST,
content-length=N` ③tests：deadline 斷言 60000＋jsdom driver
`delete XMLHttpRequest`＋新 TestUploadProgressPanelV0127_8×4＝相關
五套 143 passed；127.7 =
**上傳分批化＋死連線守衛**（user 14:49 live traceback＝62MB POST 截
4MB→SSLEOFError 非 ConnectionError 逃逸＝回覆寫死 socket 守衛
OSError 三處（截斷分支+share redirect+/upload /share-text route）
+uploadFiles 拆 5 檔/批（_uploadOneBatch per-batch deadline
30s+30s/批內檔 cap 300s、⏳ 帶 [1-5/15] 進度、pending buffer/markers
逐批累加、失敗帶 N saved before the failure；≤5 檔 byte-identical）；
jsdom 硬驗證 12 檔=fetch 3 次 batchSizes[5,5,2]；tests +4＝七套 173
passed）；127.6 =
**上傳截斷誠實化**（user 遠端多檔 3/十幾個＝30s 硬中斷→截斷 body
被 _parse_multipart 靜默部分儲存→謊報 saved 3 skipped 0；修＝server
拒收 400 truncated received-X-of-Y+uploads 零建、前端 timeout 依檔數
30s+20s/檔 cap 300s、⏳ toast 帶真實選取數 (N)、abort 專屬訊息
uploadTimeout×4、marker 插入 never-silent；tests +2＝五套 146
passed）；127.5 =
**分享三件套**（①per-bubble 📤＝navigator.share+clipboard 退路+i18n×4
②Android share_target＝manifest POST `/upload?share=1` multipart+
`_parse_multipart` keep_fields+文字側車 `shared-<ts>.txt`+303 回
`/?shared=` →前端 `handleSharedLanding` 插 [已上傳] marker+
replaceState 防重放 ③iOS 無 share_target＝新 `/share-text` 端點+
`?share=json` 形+`docs/IOS_SHARE_SHORTCUT.md` 捷徑教學；tests +20
新檔＝相關五套 134 passed）；127.4 =
**icon 全套換新犬**：user 圖 dog_icon.png（1024 RGBA 全彩小狗）PIL
LANCZOS 重製 icon-192/256/512+icon.ico 六尺寸+icon.svg=512 內嵌
wrapper（既有 href 引用面零改）；icon-256.png 名實不符地雷解
（103.3 檔名 256 實測 512）；web_server cache-bust 清單
+3＝favicon svg/ico+logo img（favicon 快取黏性）；Pillow ICO
append_images 補 128（fallback 只縮不放）；android launch_screen
同步；tests 更新 svg/preview/stamp 三守衛）；127.3 =
**語音輸入不能用真修**：toggleMicRecording 呼叫不存在名
_isVoiceSupported（真定義=_voiceSupported）＝每點必 ReferenceError
散逸＝零 toast 零請求；改真名+bindMic never-silent catch；tests +3
新檔）；127.2 =
**上傳↔送出 race 真修**：tab19 現場＝✅toast 有、[已上傳]插入沒發生
→ 上傳 fetch 回程遲於送出＝訊息無圖路徑；pending buffer(
__PENDING_UPLOAD_PATHS__)送出側 merge-if-missing+clear；tests +1）；
127.1 =
**上傳圖檔「送出無聲」防護**（sendChat 無 try/catch＝拋錯散逸＝零回饋
＋送出鈕卡死；wrapper 捕捉→toast+error bubble+還原按鈕+草稿放回；
實彈驗證：user Firefox 兩回合真圖 describe OK＋Ollama 500 退 Gemini；
tests +3）；127.0 =
**自我優化五項採用**（①plan done→skill_drafts/ SKILL.md 草稿永不自動
入庫②規則療效度控 errors/last_error 繞 ring③記憶維護 cleanup_candidates
+suggest_memory_cleanup+opt-in 週排程④驗證糾正回合 alt-pin 驗證者異源
⑤ToolRoute overrides shadow 預設+rulebook-local 7 工具；tests +24）；
126.8 =
**啟動主 URL=tchtool.app**（真因①live .env Web 區塊整段消失→補回
HOST=0.0.0.0+PORT+TLS 對+NAME；②_urls_for 主 URL 恆 loopback→有
certified name 時名稱升 canonical：banner 主行+auto-open 用名稱、
loopback 降次要；無 TLS=byte-identical；tests +1）；126.7 =
**手寫書寫區去框線**（.hw-canvas 移除 border+radius＝乾淨白紙；
modal 外框與淺色 pin 不動；tests +1）；126.6 =
**Web 禁止縮放**（三層：viewport user-scalable=no+scale caps／CSS html,body
touch-action pan-x pan-y／bindZoomGuard=web-mode gate 攔 iOS pinch
gesture*+Ctrl滾輪+鍵盤捷徑；GUI 桌面零改動；tests +3 新檔
test_zoom_guard.py）；126.5 =
**手寫閒置提示移除**（#hw-hint 留元素清文字——兼做儲存/PDF 狀態列；
data-i18n="hwHint" 移除防 applyI18n 回填、app.js 四處恢復點改清空、
hwHint key ×4 保留；tests +1）；126.4 =
**手寫 modal 按鈕純 icon 化**（icon+文字→純 icon＋data-i18n-title
tooltip＋aria-label 映射；hw-full2 隱藏 bug＝applyI18n 覆寫文字一併修；
`.hw-modal .btn` min-width 34px；tests +1+2 更新）；126.3 =
**手寫 modal 淺色釘死**（深色主題下 modal 內按鈕黑底白字貼白板——`.hw-modal` 子樹 pin 淺色調色盤、書寫面恆白；淺色主題 byte-identical；tests +1）；126.2 =
**APK 本機建出**（.android-sdk 布建 cmdline-tools+platform-34+
build-tools 34/35；wrapper 零依賴化=android.app.*+platform Material
（androidx 摘除）；android/build_apk.py no-gradle 組裝器= aapt2(注入
package)→javac→jar→d8(35;34 的 R8-dev NPE)→dex 附加→zipalign→
debug keystore→apksigner sign+verify→badging；成品 android/dist/
divine-sense-agent-launcher.apk 25KB vCode1 vName0.9.126.2 僅
INTERNET）；126.1 =
**kidmen 憑證接線＝PWA 安裝通**（kidmen-web ssl/ = GoDaddy 可信
tchtool.app 憑證+key 配對+DNS→本機 192.168.50.154；.env TLS CERT/KEY/
NAME=tchtool.app；banner `certified as` 行；E2E 無 -k healthz 200；
fake_servers fixture 恆 _tls_material→None 防 .env 注入翻轉契約；
tests +2）；126.0 =
**Web 可選 HTTPS**（`_tls_material`：env PEM 對 / `QDRANT_WEB_TLS_
AUTO=on` 自簽〔SAN=lo+全部區網 IP、產 $QDRANT_RAG_HOME/certs/ 重啟
重用、缺 cryptography 誠實退 http〕+run_web_server wrap_socket+
scheme plumbing banner https；**未設=plain http byte-identical**；
安裝修=secure context；tests +7=15 passed＋live https healthz 200）；
125.9 =
**Android app 化啟動（banner 消除）**（①PWA：manifest.webmanifest
standalone+sw.js NETWORK-ONLY 零快取+web_manifest MIME+icons fitz
render 192/512+web-gate 註冊——Firefox/Chrome「安裝」=無 banner；
②android/ wrapper 專案源碼（本機無 SDK=交 Android Studio 建置步
驟；唯一權限 INTERNET）；tests +8=124 passed）；125.8 =
**Android pen 虛線修**（125.7 增量段每段從原始點起筆而上一段終點=
中點＝段縫虛線——改 b 點接續（moveTo(bx,by)=上中點、quad(lx,ly→新
中點)、bx=mx）＋end tail() 補尾半段（lineTo(lx,ly)）＋redraw 同形
＋eraser 單擊 dest-out 圓；tests 77 passed）；125.7 =
**手寫工具三件套**（pen 多色可調粗細 / marker 多色 0.4 半透明 /
eraser dest-out——筆劃記錄模型 `hwStrokes[page][]` 全量重繪
`notesHwRedrawPage()`＝重鋪背景鏈+時間序重放=底稿恆不破；繪幾何改
增量段防 marker alpha 疊深；hw-tools-row 三鈕+六 chips+color+slider
per-tool cfg；notesHwClear 連清記錄；tests +2=77 passed）；125.6 =
**手寫匯入 PDF 底稿**（Api `notes_handwriting_pdf_pages` fitz 逐頁
150-DPI PNG+歸檔+頁 cap 10；前端 📄 匯入=web /upload→host render、
GUI create_file_dialog；`hwPageBgs[]` 逐頁 contain-fit underlay+
`hwSrcPdf` 溯源+OCR 忽略印刷體 hint；pymupdf>=1.24 依賴；tests +9=23
passed；工具三件套 pencil/marker/eraser 屬 125.7）；125.5 =
**遠端 [已上傳] 消失真兇**＝插入標記吃 `t('uploadMark')`+介面語言
（遠端 navigator.language 簡中/英 → 插 `[已上传]`/`[uploaded]`）—
升格協定 token `UPLOAD_MARK='[已上傳]'`（uploadFiles+_insertUploadPath
兩點、i18n key 保留）、jsdom t-stub 語言中立反證 + source guard；
125.4 =
**[已上傳] 插入鏈 jsdom 實證**（真 uploadFiles + server 形回應重放=
正確插入；現場成因=舊 JS 快取/回程斷線——cache-bust ?v= + 30s 逾時
＋fail toast 已涵蓋）；125.3 =
**上傳無反應修**：server 端 curl 實證健康 → 前端三修：選檔立刻
`⏳ uploading` toast（i18n ×4）+ fetch 30s AbortController 硬逾時
+ 離線預檢；bindUpload/Mic/Paste/Drop 提前 bindEvents 最前 + 四
binder `_done` 冪等（防上鏈斷掉＝零 listener）；125.2 =
**全螢幕純 CSS**：移除 Fullscreen API（它把整層視覺放大＝字體按鈕全
變大）——只切 `.hw-full`（box 100vw/100vh、canvas 100vh-150px），
物件大小全不變、只有書寫區放大；守衛反轉；125.1 =
**手寫 v2**：全螢幕（⛶+Fullscreen API）、多頁（加頁/翻頁/頁數；逐頁
墨跡掃描→縫合單 PNG OCR＋pages[] 逐頁歸檔 hw-ts-pNN）、筆記本背景圖
（file picker→cover-fit 烘焙進 PNG，OCR 也看到模板）、可看圖（notes
編輯器內嵌預覽；新 Api `read_handwriting_image` hdir 界定拒 traversal）
+ Android 手寫筆 delay 修（pointermove 吃 getCoalescedEvents 全合成
點）；i18n ×4×8；125.0 =
**手寫記事管線**：Notes web ✍️ canvas（Pointer 手繪+quadratic 平滑+DPI
+touch-action:none+空畫面守衛）→ `Api.notes_handwriting_save`＝
`$QDRANT_RAG_HOME/handwriting/` 歸檔 PNG + ImageDescriber OCR
（ _HW_OCR_PROMPT 逐字轉寫）+ notes 歸檔（分類「手寫」、內文互指 PNG）
+ `.txt` 伴隨檔走 `ingest_file` 建 Qdrant index；離線誠實拒絕；
i18n ×4×10、tests +8；124.9 =
**Notes 離線優先 web UI**：筆記本手機/web 模式支援離線瀏覽（localStorage
cache notes_cache_v1）+ 離線編輯暫存（notes_pending_v1 FIFO：save/
create/delete）+ 上線自動/手動同步（🔄 按鈕 + online 事件；create 上線
補建由 server 發 id；conflict_backup_v1 20 環衝突備份）+ 離線 banner；
web-only（__WEB_MODE__ gate）、後端零改動、i18n ×4×7；124.8 =
**中止鈕 UI 鎖死修復**：🛑 送出後 20s（`_ABORT_PATIENCE_MS`）未到終態
→ 前端解鎖輸入 + `turnAbortPending` 誠實狀態（stop 只在 tool-loop
邊界生效、in-flight LLM 呼叫 93–377s 不受中斷）+ 輪詢照常、真終態照
常落泡；併發第二發走 turn_gate（並行/排隊/誠實 Busy，不鎖 UI）+
i18n ×4；124.7 =
**MCP fleet 清單稽核**：live `.env` gate 七件本就完整（重啟即生效）、
`.env.example` gate 註釋行補齊 pdfcraft→filmcraft 七個（124.2–6 漏）+
fleet E2E（7 craft 同場 `HIDDEN_CRAFT:[]` 暴露 138 工具）+ prompt
「MULTI-FLEET NOTE」節（撞名 auto-rename `server__tool`、目錄為唯一
權威）；124.6 =
**FILMCRAFT MCP**：mcp.json 條目 `filmcraft-cli.exe mcp`（NLE
headless，17 工具：command_list/run/batch 泛用指令系統 + media_import
+ doc/project/sequence_inspect + render_frame + ui_*×9 需 --bridge；
無 roots/compact；「影片」不入觸發列避開 media 嵌入誤掛）——
**ArtCraft 七件套（Photo/Pdf/Grid/Sound/Word/Deck/FilmCraft）全數
接線**；124.5 =
**DECKCRAFT MCP**：mcp.json 條目 `deckcraft-cli.exe mcp`（簡報
headless，25 工具：new/open(.deckcraft/.pptx)/add_slide/layouts/
add_shape/add_text_box/set_text/insert_picture/table/chart/
inspect_slide/render_slide/export(pptx/pdf/png)+run_command/batch
泛用面 + pointer/pen 手繪 + APP_ONLY×2 需 --connect；無 root/compact；
inspect 未開簡報如實報錯）——**ArtCraft 六件套
（Photo/Pdf/Grid/Sound/Word/DeckCraft）全數接線**；124.4 =
**WORDCRAFT MCP**：mcp.json 條目 `wordcraft-cli.exe mcp
--automation-read-root/--automation-write-root`（文字處理 headless，
16 工具：new/open/save_document(格式隨副檔名含 pdf/png)/type_text/
inspect_document/render_page + list_commands/execute/batch 泛用面 +
desktop-only×4；automation roots 對齊沙盒；觸發列窄集避開中文
「文件」泛詞）——**ArtCraft 五件套（Photo/Pdf/Grid/Sound/WordCraft）
全數接線**；124.3 =
**SOUNDCRAFT MCP**：mcp.json 條目 `soundcraft-cli.exe mcp`（DAW
headless，17 工具：session 流 + import_audio/bounce_mix(dBFS+LUFS) +
list_commands/execute/batch 泛用面 + APP_ONLY×8 需 --connect；無
root/compact；觸發列窄集避開 media「音訊」誤掛）——**ArtCraft 四件套
（Photo/Pdf/Grid/SoundCraft）至此全數接線**；124.2 =
**GRIDCRAFT MCP**：mcp.json 條目 `gridcraft-cli.exe mcp`（試算表
headless 引擎，21 工具全量：read/write/set_cell/公式/formats/chart/
table/sort/filter/open/save + list_commands 泛用面；無 compact——
21 < 128 cap；--connect 桌面綁定不用）+ ToolRoute gridcraft 觸發列
+ prompt「GridCraft workstation」節；124.1 =
**PDFCRAFT MCP**：mcp.json 條目 `pdfcraft-cli.exe mcp --compact --root`
（161→17 核心工具 + tool_search/tool_call 兩段式；photocraft 119.1 同款）
+ ToolRoute pdfcraft 觸發列 + prompt「PdfCraft workstation」節 +
**`.env` gate 修回 `rea`**（124.0 誤清——mcp.json 裡是真 server）；
124.0 =
**Self-Evolving Rulebook**：新 `src/agent/rulebook.py`（Memento 3
rulebook工件 + Evo² 證據門 + zero-dep snapshot ring）— READ 沙盒
`agent_rules.md` 上 prompt、`RulebookLocalHost`（rulebook-local 5 工具
core 面）+ prediction-error hook（工具失敗→每回合一提示）+
`kind="rule-lesson"` memory sink + TACTI 失敗 handoff `recovery_hint`
(crossover) + `QDRANT_REFLECT_POSTMORTEM` 事後檢討 (opt-in) + script
雙層分診 + 多假設食譜；tests +43；**git 首次 commit 收案**（repo 曾
0 commits；baseline `c2189c7`）；123.0 =
**回合即時 log（Turn Events）**：web/GUI 執行 prompt 即時看到執行過程、
遠端也看得到 — 新 `src/agent/turn_events.py`（ring buffer 400字×120件×
LRU 8、never raises）+ Agent `begin_turn_stream/end_turn_stream/
signal_turn_stop/_emit_turn_event`（threading.local 按線程綁定、
`_tool_loop` 六節點發射）+ `Api.start_turn/poll_turn/stop_turn` 兩段式
（舊同步 chat_turn 契約不動）+ 前端 `pollTurnStream()` 1.2s 輪詢渲染
⚙ 可折疊執行 log 面板 + 🛑 中止 + `msg.turnLog` 入 session 歷史回看
+ i18n ×8×4 + `.chat-exec*` CSS；tests 新 3 檔 32 回歸 168 passed；
122.2 =
**gemma2 媒體伺服器看門狗**：`Gemma2Watchdog(EmbedWatchdog)` 子類
（全套療傷引擎共用一組 QDRANT_LLAMACPP_WATCHDOG_* 旋鈕、自己 URL/bat/
thread 名 `[gemma2-watchdog]`；閘 `QDRANT_GEMMA2_WATCHDOG` 空白=跟隨
QDRANT_MEDIA_EMBEDDING）+ prewarm 第二發 `_start_gemma2_watchdog()` +
runtime status 露 `gemma2_watchdog` + tests +8 = 41；兩 server 並存語義
照舊正確（kill 只殺 :8081 的 PID；refused 快路在別台活著時退 2-streak
spawn-only）；122.1 =
**EmbeddingGemma-2 影像嵌入查證結案 + gemma2 server b11541 上線**：
ggml-org#30082 撤回=形狀誤報、上游 #30054 b11457+ 已通 `/v1/embeddings`
巢式 content parts；media_embed.py 改巢式第一順位 + 假成功守衛照舊；
LIVE: unsloth b11541-mix-2500203 windows-x64-vulkan 替換
C:\llama.cpp-un\（舊 47 exe/dll 存 _old_b11443\ 回滾、GGUF/mmproj/bat
不動、bat `cd /d "%~dp0"` 尾反斜線引號陷阱修為 `"%~dp0."`）＝8081 探針
verdict=REAL cos(red,blue)=0.8795、不同圖 0.8094、音訊 input_audio 亦
真通（兩波形 cos=0.9114）＝影像/音訊 side-car 與 search_media 自動生效；
122.0 = **片語多行化 + MCP
管理面 snippets-local**：core snippet_store.py 條目=多行字串零遷移
（同檔 GUI/MCP 共用）+ 5 工具（list/add/edit/delete/move）+ GUI
textarea 新增列 + chip ✎ 編輯 + 首行顯示 + stdio snippet_server +
ToolRoute/prompt/gate；121.2 = **tacti delete_plan**
（confirm 閘 + force=stop+delete + _DELETED 防殭屍復活守衛 + host/
stdio 第 7 工具）；121.1 = **【真兇】背景圖
修改無效根因修复**：_applyFromUi(override) — 舊碼重讀 localStorage
覆寫剛選的 bg；≤1.2MB 原檔 dataURL、canvas 失敗退回；121.0 = **遠端
背景圖修復**：
quota fallback 幽靈鍵（drop bgDataUrl→正確 drop bg）+ URL 欄位
路徑→/download 串流（遠端可載）+ bind/apply 錯誤 toast 顯性化；
120.3 = **計畫編譯器正名 TACTI**：
server skill-local → tacti-local、SkillLocalHost → TactiLocalHost、
stdio skill_server.py → tacti_server.py、log [skill]→[tacti] —
「skill/技能」一詞專屬 SKILL.md 檔案庫；
120.2 = **skill 雙機制釐清**：
ToolRoute 問題側掃描 + 檔案庫觸發列擴詞 + prompt「TWO skill systems」
分工（skill-local=計畫編譯 vs skill-library-local=SKILL.md 檔案庫）+
禁 write_file 手寫 skill；120.1 = **chat Excel 上傳修復**：
_load_xlsx（openpyxl→per-sheet pipe tables）+ pipeline 放行 .xlsx +
app.js 拖放上傳 + 貼上非圖片檔案 + openpyxl 依賴宣告；
120.0 = **背景任務強制中止**：
registry stop 旗（殭屍可達）+ `_tool_loop` 每迭代檢查 + task-local
`stop_task` + telegram/plan/task 三路接線、`stopped` 狀態；
119.x = photocraft MCP/scraping 紀鏈案；118.0 = **arbiter 升格共用**
官方 mcp.md 對照入 README（batch 非原子/edit.undo/photocraft://資源/
renderVideo progress/bridge+token 留待 ui_*）；
119.1 = **PhotoCraft MCP
上線**（mcp.json 補 `mcp` 子命令 + automation roots 對齊沙盒；21 工具
stdio 0.7.0；ToolRoute +photocraft 列）；
119.0 = **polite scraping
discipline** prompt block（抓多頁腳本 = 2-5s 間隔 + 429/503 退避重試
+ checkpoint 續抓 + 一腳本一批 + 序列優先；429=網站限頻率與 thread 無關）；
118.0 = **arbiter 升格共用**
`src/agent/turn_arbiter.py`（liveness+Jev judge loop 抽出）+
Telegram 回合同款移植（`QDRANT_TURN_HARD_TIMEOUT_EXTENSIONS` 預設 3；
Jev 旋鈕 skill/shared 別名等價）；117.0 = **stage 硬超時仲裁**：
liveness（派發成長）+ live Jev `normal_long_work`（prob≥0.5）→ 加窗續跑
（`QDRANT_SKILL_STAGE_TIMEOUT_EXTENSIONS` 預設 3），巨量合法工作不再被
1800s 冤殺、driver 波次 deadline 隨加窗伸展；116.1 = boot-seed NameError
hotfix：from_config 匯入清單補 `SkillLibraryLocalHost` + 行為級 seed 測試；
116.0 **技能檔案庫**
`skill-library-local` — SKILL.md 匯入/查詢/刪除/引用管理：
`src/skill_library.py` 純邏輯 + 6 工具 host/stdio/`cli skills` 三面；
匯入=資料夾附屬全套/單檔/github blob→raw URL、confirm 閘+衝突
fail-closed+caps、`_skill_meta.json` provenance；引用=`skill_use`
內文 inline 進回合；13/13 live E2E + 57 tests；v0.9.112→115 arc:
**115.3 snippet 游標插入** (⚡ 片語 insertSnippet = 插在 text-caret 處
+ 游標停在其後、選取範圍 verbatim 取代; glue 空格規則只在
collapsed caret 保留; TestSnippetCursorInsert = source guard +
node-driver 真跑生產函式(5 情境+連插鏈+選取取代));
**115.2 OpenRouter fallback 嵌入 4B** (OPENROUTER_EMBED_MODEL=
qwen/qwen3-embedding-4b — live 實證 HTTP 200 原生 2560d、
served_by=DeepInfra = 與 file_docs_4b 維度精確對齊, 免 8B 4096d
pad 汙染; code 預設 8B 不動, tests +2);
**115.1 看門狗 refused 快路治癒** (TCP connect refused（埠上什麼都沒有
— 載模型中會先綁埠回 503 non-refused）+ tasklist 查無 llama-server.exe
= 明確死亡 → 跳 2-streak 立即 `_restart()`（spawn-only 鏈、600s 冷卻
照舊）— 修「boot 時 llama.cpp 已死要等 30 分鐘才拉起」窗口；
QDRANT_LLAMACPP_WATCHDOG_REFUSED_HEAL 預設 on、status 露 refused_heal
、tick 帶 dead_now; live 收案: bat spawn DEVNULL 靜默退場 vs 直接帶
log 的 exe spawn 立即復活 READY 88ms dim=2560);
**115.0 firefox_craw Fleet 上線** (mcp_getweb v5.16→**v6.0 協議 v2**
= 多電腦/多 Firefox：xpi 首裝 browser_id（ff-x…，storage.local 持久）
+ service multi-client registry + /status fleet 樹 + browser 工具
全體 +可選 target=（省略=主 Firefox=v5 單機語意 byte 相容）+ 未知
target 404 附清單 + MCP_SERVICE_KEY /exec 鑰（恆時比較；未設=僅
loopback）+ MCP_SERVICE_HOST/UPSTREAM_URL/NODE_ID（下層 machine
outbound 連 root、零開埠；1s 短輪詢 wait=0 — wake/cancel 競態設計
整組撤掉）+ 32+1 工具=target 轉發在 server.exe（agent 代碼零改）；
test_fleet_v6.py 31 檢查=多/target 直派剝除/404/主客戶端/legacy
default/key 401×2+200/雙向版本閘/一層+兩層 tree/fleet 樹 全綠 +
既有 confirm_forward3/exec_400=5/version_gate=8 版號更新全綠；
live dist_v6 隔離建置→一次式 swap→/health 6.0，真 xpi 5.16 重連
= legacy default（409 請重載 — 人工步驟：about:debugging 重載 v6
xpi）、server.exe v6.0 重接=34 工具；.env.example MCP_SERVICE_* 四
變數註解塊);
**113.3 EmbeddingGemma-2 上線** (user 找到 unsloth fork
b11443-mix-d65395f — release 明載 EmbeddingGemma-2 support #247;
build → C:\llama.cpp-un\ + unsloth GGUF Q8_0 310MB + 8081 並行
server (4B 主 8080 不動) + text_docs_gemma2 768d 4207 點全量 +
eval 工具 --url 多 server 通吃; 三路對照 8B 19/20 < 4B 20/20 主 >
gemma2 17/20 (13 rk1、~20ms、0.4GB VRAM) = 輕量第三 backend;
mmproj/mtmd 影像通路 = 下一階段);
**114.0 媒體多模態側通道** (規則 = 媒體走 ggema 768d 統一空間、
文字走 Qwen3; src/ingest/media_embed.py + pipeline side-car/
_ingest_media_file + search_media MCP
(in-process+stdio mirror) + media_docs_gemma2 側路 collection;
fail-closed 假成功守衛 = media_path_available 兩張參考圖 cos>0.999
= 判不可用 (v0.9.122.1 更正: ggml-org#30082 已被提報者撤回 = 形狀
誤報 — 上游 #30054 自 b11457+ build 已通影像嵌入/巢式 content
parts; media_embed 已改巢式優先, legacy 形狀仍可假成功 = 守衛留用;
現役 gemma2 server 已升級 b11541-mix-2500203 (2026-10-13, 舊版存
_old_b11443\) = 通路已點燃 (probe cos 0.8795; 音訊 input_audio 亦真通
0.9114; 描述文字路照常, 60min cooldown)、--mmproj mp-q8 555MB 就位;
**113.2 8B bat 改名** (start-llamacpp.bat → start-llamacpp-8b.bat,
兩目錄 + watchdog DEFAULT/CANDIDATES + start8b wrapper + 文檔現行面
同步; 歷史 changelog 保留舊名);
**113.1 正式切換主嵌入 = 4B 2560d** (.env QDRANT_COLLECTION=
file_docs_4b + EMBEDDING_DIM=2560 + EMBEDDING_BACKEND=local 明確化
+ watchdog bat 同步 4B — 原預設 bat 重啟會拉 8B 汙染; README/SSRN
補嵌 + 舊 8B file_docs 原封保留回滾; schtasks once-task 的
scheduled-time 重 fire 教訓 → wrapper 改 run-and-self-delete —
本版抓到 8B+4B 共享 :8080 隨機派發 mixed-dim 災難現場);
**113.0 ROADMAP 三項結案 — ① STAIR heading metadata
(src/ingest/headings.py + chunk_document_with_headings + DOCX Heading
→# prefix + payload heading_path + KEYWORD index +
heading_backfill.py metadata-only 零重嵌回填 + cli backfill-headings
+ GUI § 按鈕 + QDRANT_HEADING_BOOST 搜尋加成, 全 QDRANT_HEADING_
METADATA=on opt-in) ② Qwen3-4B 並行驗證代跑 (eval/ 三件套 + 
start-llamacpp-4b.bat + 交替式驗證 — 8B 19/20 vs 4B 19/19, 品質相當
VRAM 減半, 收錄第二 backend) ③ cache key 撞鍵生產級 bug 修復
(disk local key 固定 "llama.cpp" + LRU key 純 text-sha → 換 GGUF
後 4096d 舊向量 truncate 2560d 汙染查詢 — 兩層 key 一律帶
backend:model+dim, 舊 disk rows 不可達 = 下次 ingest 重嵌一次)
④ EmbeddingGemma 2 查證 (ollama library 已上架但 0.34.3 → 412,
cloud /v1/embeddings 404, llama.cpp b10423 不支援 Gemma 4 — 下一步
= winget upgrade ollama ≥0.40; CPU 批次 >120s 超時注意) /
108 limits+task caps+notify / 109 skill compiler+manual /
109.3+109.4 boot auto-heals (Qdrant + firefox_craw middle
service) / 110 task/stage-level LLM model pin — openrouter+ollama
catalog-validated `provider:model`, fail-closed, no failover /
111 plan scope (tools_allow via the per-TOOL gate) + fire llm
pins (Schedule/WebhookTrigger, ADD fail-closed, fire-time degrade)
+ mechanical stage handoff out/<sid>.json + memory hit tracking &
task-result sink (store-NOT-recall) / **112 embedding resilience:
llama.cpp embed watchdog (real-POST probe, zombie detect via
netstat-PID kill + detacced bat relaunch, cooldown; third
auto-heal) + hash-pollution control (payload `embedding_backend`
tag + QDRANT_EMBED_FAIL_CLOSED + src/ingest/reembed.py
audit/re-embed via exact hash-vector comparison, GUI Admin buttons
+ cli audit-backends/reembed-hash)**;
on the v0.8.9 4-Provider LLM dispatch baseline: Ollama / OpenRouter / SiliconFlow / OpenAI
via one OpenAI SDK; v0.9.105.0 = exec-local OS REAL-input suite —
six ``os_*`` tools on SendInput (src/os_input_tools.py: status/
windows_list/focus_window/click_point[type preview]/type_text/key_
press) for trusted-only fields the firefox bridge cannot fill
(Teams/Outlook rich compose, M365 login tile); chain =
browser_element_point → os_click_point(preview 先目視) →
os_type_text → os_key_press Enter; fail-closed: QDRANT_OS_INPUT_
ALLOW=on + per-call confirm + title_regex foreground anchor +
cursor restore + audit jsonl; stdio mirror extended; 18 new tests
; v0.9.105.1 = teams-assist UIA 鏈補完 — uia_teams_ops.ps1 新增
``-Action search``（純 UIA 導航：展開搜尋 Invoke→ValuePattern 填
目標→輪詢結果選第一項→doc read-back），_uia_ops 放行 search，
真送出＝InvokePattern（E2E 實證：Teams web 的 Enter／傳送鈕被反
自動化守衛，UIA 是唯一不受守衛的送出通路）
+ surface tests updated — 108 exec+os tests green; v0.9.44.0 = RTSP frame grab for cameras WITHOUT
an ONVIF snapshot capability (XMEye) — grab_frame_via_rtsp (ffmpeg
-frames:v 1, settle_s input-seek, JPEG-magic check), NINETEENTH
onvif-local tool onvif_rtsp_snapshot (GetStreamUri → vendor variants
until a JPEG, attempts list, uri=/channel=/settle_s params), and
onvif_get_snapshot AUTOMATIC RTSP fallback (via=rtsp + note;
both-fail combined error); v0.9.43.2 = ONVIF diagnostics hardening
from the second live log — get_profiles_anywhere per-candidate FAILURE REASONS
in the error (401/refused/SOAP-fault/0-profiles), _device_and_creds
empty-name refused (stale nameless favorites.json entry can no longer
be resolved by ""), _test_credentials flags EMPTY device info as
ok:False (xaddr not the device service); v0.9.43.1 = XMEye DVR fix
(get_profiles_anywhere media-endpoint fallback chain :8899 etc. +
rtsp_variants standard/hikvision/dahua/xmeye + onvif_record uri=
override/channel + _embed_rtsp_creds query-creds fix); v0.9.43.0 =
EIGHTEENTH LocalHost `runtime-local`
(RuntimeLocalHost: reload_config [5s cooldown] / restart_services
[filterable] / restart_mcp_servers [manager-wired, no Agent needed]
/ get_runtime_status; Agent._runtime_ops wired via
set_runtime_ops_callback; stdio mirror runtime_server.py; gate list
+ runtime-local); v0.9.42.0 = config hot-reload + service-restart
mechanism — Config.reload(path) typed re-assign of every env-backed
class attr (dotenv override, changed-list return, bad-value skip),
Agent.reload_runtime (LLM chain reset + conditional daemon restarts:
scheduler/webhook/telegram/onvif watches per enable-conditions),
McpManager.reconnect_remote_servers (re-reads config/mcp.json,
locals survive), ConfigWatcher mtime-poll auto reload
(QDRANT_CONFIG_HOT_RELOAD=on opt-in, WATCH_INTERVAL 1-300s), GUI
Admin-tab 🔄 reload button + cli.py reload command; v0.9.41.2 =
MCP_ACTIVE_SERVERS full-service list
(17 LocalHosts + config/mcp.json entries) in .env + .env.example +
typo fix webhook-localhost → webhook-local (the misspelled unknown
name silently HID the 10 webhook tools per the v0.9.13.2 gate
contract); v0.9.41.1 = judge-then-notify recipe (payload event_text
+ snapshot path composed into the fired prompt → describe_image →
conditional Telegram); v0.9.41.0 = ONVIF Phase C — PTZ
(ptz_move/stop + presets, confirm-gated) + Events PullPoint
(get_events one-shot + onvif_watch_start/list/stop daemon threads:
keyword match → history ring + sandbox snapshot + WEBHOOK FIRE via
attach_webhook_fire/set_webhook_fire_callback push-down — motion →
registered trigger → fresh-Agent prompt; gui boot auto-resume
_resume_onvif_watches, QDRANT_ONVIF_WATCH_AUTORESUME=off opt-out;
onvif-local 9 → 17 tools); v0.9.40.0 = MCP network scanner —
SEVENTEENTH
LocalHost `netscan-local` (src/net_scan_tools.py pure-stdlib: IP
scanner scan_hosts tcp/ping + port scanner tcp_probe/scan_host_ports
+ local_interfaces; PRIVATE-NETWORKS-ONLY by default, public targets
refused until QDRANT_NETSCAN_ALLOW_PUBLIC=on; MAX_HOSTS 4096 /
MAX_PORTS 1024 / MAX_WORKERS 128 caps; stdio mirror
net_scan_server.py); v0.9.39.1 = ONVIF resource sweep `onvif_get_
resources` (9th onvif-local tool — device info + media XAddr +
profiles + RTSP stream URI + snapshot URI in one SOAP round);
v0.9.39.0 = central credential sets —
src/credential_store.py (atomic JSON, env-first resolution, masked
outputs) managed via MCP CredLocalHost (cred-local, cred_add/list/
get/remove/resolve) + GUI 🔑 Credentials tab + cli.py creds …;
ONVIF favorites gain `cred_set` (resolution order cred_set →
password_env → b64); v0.9.38.0 = ONVIF Phase B recording — the 8th
onvif-local tool `onvif_record` (GetStreamUri → rtsp creds
embedded → ffmpeg -c copy into the write sandbox MP4, seconds
1-600 + 30s margin, rc 0/255 ok, fail-fast with winget install
hint when ffmpeg is missing); v0.9.37.0 = ONVIF camera MCP Phase A
READ-ONLY
(FIFTEENTH LocalHost `onvif-local` — src/onvif_tools.py with
hand-built SOAP + WS-Security Digest + stdlib WS-Discovery, zero
new deps; onvif_discover / favorite_add/list/remove with
password_env-first credentials + masked list output /
test_credentials / get_profiles / get_snapshot into the write
sandbox so chat displays it inline + stdio mirror; Phase B
recording + Phase C settings writes pending); v0.9.36.1 = the
REAL tofu root cause — the
tofu chart was drawn via write_file + windows-mcp PowerShell which
BYPASSES the exec_python bootstrap; fix = ship config/matplotlibrc
(JhengHei stack) + src.config sets MATPLOTLIBRC at import so every
python in the process tree (run_python / PowerShell / mplfinance)
renders CJK — verified 0 findfont warnings with mplfinance;
v0.9.36.0 = oversized run_python code (>24k
chars) runs as a temp sandbox .py instead of -c — Windows caps the
command line at ~32k chars, a 1000-bar K-line inline died with
WinError 206 (NOT the v0.9.34 CJK bootstrap — verified working) +
the exec prompt tells the LLM to keep code short and read data
from sandbox files; v0.9.35.0 = stock query snapshots auto-persist
to the dedicated stock_docs collection (src/stock_store.py —
LogStore mirror, uuid5 ids, qdrant field on query replies,
stock_search_history / stock_list_history tools, save never raises);
v0.9.34.0 = K-line-chart tofu fix — exec_python
auto-prepends a matplotlib CJK-font bootstrap (first installed of
JhengHei/YaHei/SimHei/... wins, axes.unicode_minus off, zero
overhead for non-chart calls, try/except for matplotlib-less
interpreters) + the exec prompt says charts are auto-CJK-safe;
v0.9.33.0 = chat image lightbox — inline
previews gain click-to-enlarge (full-screen overlay, 92vw/86vh
contain, path caption, click-anywhere or Esc to close, data-URL
reused — zero re-read); v0.9.32.0 = the chat-UI 🧰 tool picker
gains a
per-SERVER tri-state select-all (checked/unchecked/indeterminate
click flips the whole group, same chat-disabled-tools set,
re-render keeps tri-state fresh); v0.9.31.0 = Telegram sendPhoto —
the 4th
telegram MCP tool `send_telegram_photo(chat_id, path, caption?)`
multipart-uploads a SANDBOX image (import∪READ∪WRITE roots gate,
ext+magic+10MB cap, relative paths anchored at the WRITE root,
allowlist-before-HTTP) + the prompt Telegram block teaches when to
use it; v0.9.30.3 = the Show-images rule names the
LITERAL write-sandbox path (_write_sandbox_hint() interpolated at
prompt build — the LLM cannot see env vars); v0.9.30.2 = live-box
hotfix — preceding-char
validation replaces the positive separator class so CJK punctuation
glued to the path (「檔案位置：C:\...」) matches, mid-word still
excluded + the prompt rule now REQUIRES saving generated images
into the filesystem write sandbox (Desktop/OneDrive paths can
NEVER render) + failed previews carry the refusal reason in alt;
v0.9.30.1 = inline-image hardening — bare paths
+ uploads/-relative paths also render (regex unified, relative
resolved against the WRITE root, still roots-gated) + a "Show
images in the chat" system-prompt rule forcing the LLM to EMBED
the path/markdown-image in its final message (v0.9.5.2 lesson);
v0.9.30.0 = chat inline-image display —
Api.read_sandbox_image (import∪READ∪WRITE roots, ext+magic+20MB
cap, NEVER an arbitrary read) + app.js renderSandboxImages() pass
in renderChat(): `[已上傳]`-mark lines (4 locales) + markdown local
img src → data-URL previews INSIDE chat bubbles, FIFO cache 12,
failure placeholder; v0.9.29.0 = clipboard paste — Ctrl+V on the
chat input, web reuses POST /upload, pywebview GUI uses
Api.save_pasted_image into the SAME uploads/ sandbox; v0.9.28.0 = live stock order's FOURTH gate —
Telegram mandatory consent (request_order_approval sends a unique
STK-XXXX code to MEGA_STOCK_ORDER_APPROVAL_CHAT_ID, default
<YOUR_TELEGRAM_CHAT_ID> = first allowlist entry; the BOT DAEMON resolves the
reply BEFORE the normal chat flow — 同意+code / bare code →
approved, 拒絕+code or ambiguous → denied, timeout → denied;
heartbeat check _last_poll_ts updated by poll_once, 30s stale →
clear error telling the user to enable QDRANT_TELEGRAM_BOT_
POLLING=on; engine._place_with_consent blocks on the gate, only an
approval reaches SendNewOrderEx; cancel_order intentionally NOT
gated); v0.9.27.0 = Telegram bidirectional chat —
`TelegramBotDaemon` + `TelegramBotClient` + `make_telegram_on_message`
(src/agent/telegram_bot.py, mirror of scheduler/webhook: getUpdates
long-poll 10s daemon thread, per-chat persistent session tgbot-
<chat_id>, whitelisted users' messages → Agent.chat_turn with full
tool surface, reply auto-delivered via sendMessage; fail-closed
TELEGRAM_ALLOWED_CHAT_IDS (empty = ignore all); anti-double-send
framing (the LLM is told NOT to call send_telegram_message itself);
QDRANT_TELEGRAM_BOT_POLLING default on + QDRANT_TELEGRAM_BOT_POLL_
INTERVAL 2s; boot auto-resumed by gui/api.py when token + allowlist
exist; pure outbound HTTPS — NO inbound port needed); v0.9.26.0 = 兆豐 MegaAPI live wiring — the
megaspeedy component installed at project root `megaSpeedy/` (DLL +
wrapper py + Temp/), real function surface from spdOrderAPI.py
(SendNewOrderEx / SendCancelOrderEx / queryStkOrder / queryStkMatch /
makeStockAccountInquriy / GetDKChartData), `MegaLiveSession` event
wrapper with synchronous waits (Connect spapi.emega.com.tw:56789 +
LogonProxy + SetAccount('TWSE', branch, account); quote snapshot via
spdQuoteAPI Logon/Subscribe/OnOrderBook/OnTrade) — live QUERIES are
usable (API permission opened 115/09/09, login from next-day 08:30);
live orders wired (SendNewOrderEx) but STILL GATED by THREE gates
(mode=live + MEGA_STOCK_TRADING_ENABLED=on + per-call confirm=true)
+ the certificate gate (MEGA_STOCK_CERT_PATH/PASSWORD — queries need
no cert, orders do); live creds in .env (MEGA_STOCK_ID/PASSWORD/
ACCOUNT/BRANCH); SDK import redirects stdout (banner would corrupt
the stdio MCP JSON-RPC channel); v0.9.25.1 = Qdrant loopback speed hotfix —
`.env` must use `http://127.0.0.1:6333`, NEVER `localhost`: the
bare name resolves to `::1` first and each qdrant-client request
pays a ~2 s refused delay; `src/config.py::_normalize_loopback_url`
rewrites it automatically; measured 2073 ms → 29 ms per call; ops
doc: `C:\qdrant\README.md`; v0.9.25.0 adds the webhook trigger MCP core —
`WebhookManager` + `WebhookServer` HTTP listener
(`src/agent/webhook.py`, mirror of scheduler.py: per-trigger URL +
random access key via secrets.token_urlsafe(32), constant-time
hmac compare, /trigger/<id> on QDRANT_WEBHOOK_HOST:PORT default
127.0.0.1:8335, payload {payload} injection, lifecycle caps
max_fires/expires_at/status=failed mirroring Schedule,
webhooks/<id>.json + history.jsonl ring) + FOURTEENTH LocalHost
`WebhookLocalHost` (server `webhook-local`, 10 tools:
add_webhook_trigger [returns URL + access key SHOWN ONLY ONCE] /
list_webhook_triggers / get_webhook_trigger /
get_webhook_trigger_url(reveal) / delete/enable/disable_webhook_
trigger / run_webhook_trigger_now / list_webhook_trigger_runs /
get_webhook_trigger_errors) + stdio mirror
`src/mcp_server/webhook_server.py` (does NOT bind the port — the
listener lives with the Agent process; boot auto-resumed by
gui/api.py when an enabled trigger exists,
QDRANT_WEBHOOK_AUTORESUME=off opts out); v0.9.24.0 adds the 兆豐 MegaAPI stock
query+order MCP extension — `MegaStockEngine` (`src/mega_stock_tools.py`)
+ THIRTEENTH LocalHost `MegaStockLocalHost` (server `stock-local`,
6 tools: stock_get_quote / stock_get_kline / stock_get_account_summary
/ stock_list_orders / stock_place_order / stock_cancel_order) +
stdio mirror `src/mcp_server/stock_server.py`; **mock-first safety**:
`MEGA_STOCK_MODE=mock` (default) runs a fully simulated in-memory
book — NO socket, NO megaSpeedy import, NO real orders in
development/testing; live requires the 兆豐 megaspeedy component +
THREE gates (mode=live + MEGA_STOCK_TRADING_ENABLED=on + per-call
confirm=true); v0.9.26.0 completed the real broker wiring
(SendNewOrderEx/SendCancelOrderEx live; queries usable from the
permission date) — the gates + certificate remain the only
protection; v0.9.9.2 adds OpenRouter provider routing
env vars + `:nitro`/`:floor` model variants; v0.9.9.3 added a
whole-transcript GUI chat time strip, v0.9.9.4 replaced it with
per-bubble start/end time headers; v0.9.10.0 adds opt-in chat
context compression — summarize / offload / selective squeeze;
v0.9.11.0 adds the get_current_time date/time MCP tool (local +
UTC+0); v0.9.11.1 adds OPENROUTER_REASONING_EFFORT + one-shot GUI
agent prewarm (hello latency ~6 s → ~2-4 s steady-state); v0.9.12.0
shows per-pair token usage on every assistant bubble header;
v0.9.13.0 adds the MCP_ACTIVE_SERVERS tool-surface gate (-70%
prompt tokens per turn + provider-pinning cache guidance);
v0.9.13.1 adds per-pair ⚡ cached_tokens to the chat header);
v0.9.13.2 fixes the gate discovery trap — list_mcp_tools now agrees
with the declared surface (hidden_servers + do-not-fake note);
v0.9.14.0 adds the chat-UI MCP tool picker (per-request per-tool
allow-list, localStorage-disabled-set, no .env editing);
v0.9.14.1 raises the tool-loop budget 6 → 20 (MAX_TOOL_ITERATIONS
env, per-call resolution) + adds the windows-mcp desktop-automation
prompt block (App launch ladder + System.Drawing preferred);
v0.9.15.0 adds an optional WEB UI mode (python -m gui.app --web /
qdrant-agent-web.bat) — same frontend + Api over a stdlib HTTP
server with a pywebview shim, bound to 127.0.0.1:8334 by default;
v0.9.15.1 lets the Web UI bind ANY local IP (0.0.0.0 = all IPv4
with LAN URL listing, a specific LAN IP, IPv6 ::1/:: dual-stack)
and fixes .env being silently ignored in web mode; v0.9.15.2 adds
the mobile web format (viewport meta + ≤820px/≤480px responsive
breakpoints in style.css, shared with the pywebview GUI);
v0.9.15.3 logs the per-open-page 30 s `get_status` heartbeat at
DEBUG instead of INFO (several open tabs/phones each run their own
setInterval — the INFO lines flooded app.log); v0.9.16.0 adds
web-mode client file upload (POST /upload multipart into the
filesystem write sandbox uploads/, 📎 any-file button in the chat
toolbar, 📷 media picker accept=image/video shown only on mobile
widths, GUI mode hides both); v0.9.16.1 hardens the upload→OCR
chain (describe_image mode auto-inference + upload-sandbox
acceptance, empty-choices retry + graceful text instead of the
TypeError crash, client-gone response write logged at DEBUG);
v0.9.16.2 strips base64 blobs from tool-result messages
(describe_image raw_image_base64 blew the context window of every
OpenRouter endpoint → 404 "No endpoints found") + retries hard API
errors once with graceful degradation; v0.9.16.3 locks the Api
lazy-init (web mode two-page boot raced _kick_prewarm/_get_agent
into TWO Agents → McpManager.from_config ran twice, every MCP
server connected twice) + adds the image-OCR system-prompt rule
(answer from describe_image's description; never re-implement OCR
via PowerShell/windows-mcp); v0.9.16.4 adds the OpenRouter vision
fallback to ImageDescriber (Ollama Cloud failure/unconfigured →
OPENROUTER_API_KEY + OPENROUTER_VISION_MODEL, default
google/gemini-2.5-flash → OPENAI_API_KEY fallback; result carries
vision_backend); v0.9.17.0 generalizes the backup mechanism to ALL
LLM providers — Agent cross-provider failover (_create_validated
walks _PROVIDER_ORDER after 2 primary attempts; OpenRouter-only
extra_body stripped for non-openrouter fallbacks; last_provider
records who answered; _summarize_history routed through it too) +
ImageDescriber gains the SiliconFlow vision candidate
(SILICONFLOW_VISION_MODEL, default Qwen/Qwen2.5-VL-72B-Instruct →
chain: ollama → openrouter → siliconflow → openai-compat);
v0.9.18.0 separates the chat LLM chain from the vision OCR chain —
two INDEPENDENT application requirements, each re-orderable from
.env (`LLM_PROVIDER_CHAIN` for Agent primary + failover walk,
`VISION_PROVIDER_CHAIN` for ImageDescriber; empty = legacy order,
zero behaviour change; unknown tokens skipped with a warning;
partial lists keep failover completeness; shared parser in the
ZERO-side-effect module `src/provider_chain.py`); v0.9.19.0 adds
first-class OpenAI-compatible API configuration — `OPENAI_CHAT_MODEL`
(dedicated model for the `openai` escape-hatch slot, so a
compat-endpoint config no longer poisons LLM_MODEL's global
override / breaks failover) + a FIFTH provider `openai-compat`
(OPENAI_COMPAT_API_KEY / _BASE_URL required / _CHAT_MODEL — a fully
independent second compat endpoint that joins LLM_PROVIDER_CHAIN);
v0.9.20.0 makes chat saves real-time (send-time save + error-bubble
persistence + web-mode sendBeacon unload save); v0.9.21.0 adds the
chat-history MCP core (ChatHistoryLocalHost, server
chat-history-local — list_chat_sessions / search_chat_history /
get_chat_session over the persisted sessions, TWELFTH LocalHost);
v0.9.21.1 fixes the brand-side version chip (hardcoded HTML literal
painted from pyproject on every get_status poll); v0.9.22.0
auto-resumes the scheduler daemon at boot (opt-out
QDRANT_SCHEDULER_AUTORESUME=off — the missed cron catch-up fires on
the first tick).
The tool surface is
now assembled from TWENTY-FIVE
in-process LocalHosts auto-registered by `McpManager.from_config`:
QdrantLocalHost / WebSearchLocalHost / FilesystemLocalHost
(v0.9.3+) / SchedulerLocalHost (v0.8.6+) / TodoLocalHost (v0.8.8+)
/ OpenWithLocalHost (v0.9.6+) / LogAnalysisLocalHost (v0.9.7+ —
Qdrant access-log / syslog anomaly analysis in the dedicated
`QDRANT_LOG_COLLECTION`; direct archive ingest since v0.9.8.1) /
ArchiveLocalHost (v0.9.8+ — zip/7z/tar/gz via `py7zr`) /
ExecLocalHost (v0.9.9+ — sandboxed bash/awk/grep/python/shell with
a machine-level disaster blocklist) / DateTimeLocalHost (v0.9.11+
— get_current_time, local + UTC+0) / ChatHistoryLocalHost (v0.9.21+
— list_chat_sessions / search_chat_history / get_chat_session, the
LLM can query persisted chat history) / MegaStockLocalHost
(v0.9.24+ — 兆豐 MegaAPI stock query + order tools, server
`stock-local`; mock-first: `MEGA_STOCK_MODE=mock` default runs a
fully simulated in-memory book — NO socket / NO megaSpeedy import /
NO real orders in development/testing; live orders additionally
gated by MEGA_STOCK_TRADING_ENABLED=on + per-call confirm=true and
ship LOCKED until broker integration) / WebhookLocalHost
(v0.9.25+ — webhook trigger tools, server `webhook-local`;
per-trigger URL + access key, HTTP call fires the pre-configured
prompt; setup mirrors the schedule tools, key SHOWN ONCE) /
ImageGenLocalHost
(v0.9.104+ — OpenRouter Image API 產圖, server
`imagegen-local`; mock-first = Pillow placeholder;
live bills per image via usage.cost; 4 tools) /
MetaLocalHost. The same tool
surfaces ship as 22 stdio servers under `src/mcp_server/` for
external clients.
Replaces the v0.8.0-v0.8.8 2-provider `if/elif` chain with
`Agent._PROVIDER_ORDER` (tuple sequence) + `Agent._pick_provider()`
method. Resolution: (1) `config.LLM_PROVIDER` override if set,
(2) auto-detect in priority order `ollama → openrouter →
siliconflow → openai` by first non-empty `*_API_KEY`, (3) raise
with all 4 env vars listed. `LLM_MODEL` global override skips
provider-specific `*_CHAT_MODEL`. Critical design point: late-bound
config via `_get_config()` helper that re-resolves
`from src import config as _cfg_mod; return _cfg_mod.config` on
every call — avoids lambda import-time closure binding so test
`importlib.reload(src.config)` and REPL tinkering work without
reloading the agent. 19 new tests in `tests/test_llm_providers.py`;
`_smoke_4prov3.py` 10 end-to-end scenarios all green. v0.8.8
baseline 325 → v0.8.9 314 passed, 0 failed (in 25.75s for the 14
relevant files).
### v0.9.113.0  

**ROADMAP 三項結案（user 核准「執行待3項」+ 模型交替窗口；順序 = Gemma2 探針 → eval 框架 → STAIR 開發 → 4B 交替驗證；Qwen3-4B 驗證過程踩出生產級 cache key 撞鍵 bug 順手修復 = 本版最重要修復）。** ①**STAIR 標題層級 metadata**：新 `src/ingest/headings.py`（zero-dep — ATX `^#{1,6}` 偵測；`heading_sections()` 以標題行為界切 section + heading stack 累積 `heading_path`（「H1 > H2 > H3」；首個標題前的 preamble = 空 path）+ `heading_for_text()` 單塊反推（塊內**最後**一個 heading 行勝出、否則沿前塊 carry-over — 跨塊 heading 邊界的前緣是 best-effort 限制）；`TextChunker.chunk_document_with_headings()` 每 section 獨立 Recursive split、chunk 繼承 path、`chunk_index` 全文件連續、**無 heading 文件退回舊路 byte-identical**；loader DOCX `_docx_heading_prefix()`（"Heading 1-6"/"標題 1-6" → `#`*n 前綴；PPTX 原生 `# Slide N` 相容）；pipeline `QDRANT_HEADING_METADATA=on`（**預設 off** — 字節級不破壞既有 ingest/hash 行為；on 會改分塊邊界 → 同檔下次 ingest 重嵌一次）+ fail-open；payload `heading_path` + KEYWORD payload index；**既有舊點 = metadata-only 回填** `src/ingest/heading_backfill.py`（`backfill_collection`：scroll 全點按 (source, chunk_index) 排序 → text 反推 → `set_payload` 按 path 分組寫 — **向量/ID/分塊不動、零重嵌**；honor-existing、`force` 重算、index/失敗全 best-effort 計數）→ `cli.py backfill-headings [--force][--collection]` + GUI Admin Embedding 卡「§ 回填標題」（i18n ×4，112.0 audit 同型）；搜尋側 `QDRANT_HEADING_BOOST`（預設 0=off — heading 與 query 詞雙向 substring 重疊 → cosine+boost 再排序，在 rerank 之前；`_map_hits` meta 白名單加 heading_path；boost 讀 payload/meta 兩處 fallback）。已知限制：code-fence 內 `#` 注釋行誤認 heading（記錄不阻擋）。**Live**：heading-aware ingest ARCHITECTURE = 480 塊（legacy 440）、heading_path 樣本全對（…> 1. System Overview > 1.2 Historical arc…）、boost +0.10 標記生效。tests +36 `tests/test_headings.py`（sections×10/chunker×5/loader×3/pipeline×2/boost×6/backfill×10）。

②**Qwen3-4B 並行驗證（已代跑）+ `eval/` 三件套**：`eval/eval_20q.json`（20 題×期望檔）、`eval/eval_driver.py <collection> <dim>`（expect 檔不在池自動 SKIP、報 hit@5/名次/embed ms）、`eval/ingest_side.py <collection> <dim> [--list f]`（eval 子集進任意 side collection — per-collection 切換純 process env）。GGUF：`registry.ollama.ai` blob `sha256:2b0cf8f1…0f16b`（2,496,703,776 bytes，**SHA256 全檔 MATCH**）= HF `Qwen/Qwen3-Embedding-4B-GGUF` 同 Q4_K_M（ggml-org repo 不存在 — **官方 = Qwen org**）；下載管線教訓：BITS ~115KB/s 太慢、Firefox download manager 正常；llama.cpp b10423 同 arch 直接支援。**`C:\llama.cpp-vulkan\start-llamacpp-4b.bat`**（112.0 tuned flags 照搬）+ `C:\qdrant\_bat\start4b/start8b.bat`（schtasks detached wrapper）＋交替式驗證（watchdog 探針只驗形狀不驗 dim → 4B/8B 探針都綠、無誤殺）。**結果**：8B `hit@5 19/20、rank1 15、~147ms`（全量池）vs 4B `hit@5 19/19、rank1 18、~123ms`（side 池 869 塊）— **品質相當（4B 名次在所有可比題 ≥ 8B）、VRAM 減半** → 4B 收錄可用第二 backend、切換由使用者自主。⚠️ **ollama 路徑不可用做批次**：本機 ollama 0.34.3 對 4B 嵌入走 CPU（單句 486ms；64 條批 >120s ReadTimeout → embedder cooldown）— batch 效能要 llama.cpp Vulkan。

③**cache key 撞鍵 bug（生產級）**：disk key 對 `local` 固定 `"llama.cpp"`（server 不回 model id）+ LRU key **純 text-sha1** → :8080 換 GGUF 後 (a) disk 命中 8B 4096d 舊向量 → truncate 2560d = 查詢語意殘缺（eval 1/19 MISS 的根因）(b) 截斷向量又回寫 cache (c) **ollama-batch ingest run（ReadTimeout → hash fallback）的 hash 向量經 dedup 跳過殘留同 collection**。修 = 兩層 key 一律帶 (backend, model)：`_model_for("local")` → `f"llama.cpp:{self.dimension}"`（"hash" → `hash:<dim>`）+ `_cache_key()` static → instance（`sha1:backend:model`）。副作用記錄在案：舊 disk rows 不可達 → 下次 ingest 對每檔重嵌一次（disk cache 只是加速器）。

④**EmbeddingGemma 2 查證（ROADMAP 更新）**：2026-10-06 發布、740M（text 270M+vision 170M+audio 300M）、768d、MRL 128/256/512/768、8K ctx、Apache 2.0；**Ollama library 已上架**（270m text 378MB/440m text+vision 714MB/570m text 990MB/740m full 1.3GB + bf16/mxfp8/nvfp4）；本機 ollama 0.34.3 pull → **412 需新版**；ollama cloud `/v1/embeddings` 全變體 **404（cloud 不供嵌入）**；**llama.cpp 路徑不可行** — v0.6.0（10-05）release notes 未含支援 + ollama manifest = **nvfp4 tensor-split 格式**（llama.cpp GGUF loader 不吃）→ 待上游支援；ollama ≥0.40 fork runtime = 唯一本地通路（user 決定先不升、記錄待 llama.cpp）。**升級後用法**：`OLLAMA_BASE_URL=http://127.0.0.1:11434/v1` + `OLLAMA_EMBED_MODEL=embeddinggemma-2:270m` + `eval/ingest_side.py text_docs_gemma2 768`（CPU 批次縮批注意）；多模態 = `/api/embed` image-input 實測（440m/740m）。

tests: +36 test_headings；embedder_resilience + reembed_admin + headings = 98 passed（2 個 v0.9.48 keying 契約測試隨新 key 更新 — 本質斷言不變）。pyproject 0.9.112.1 > **0.9.113.0**；README(+ROADMAP 節三項更新)/CLAUDE/ARCHITECTURE/USER_MANUAL/.env(.example 2 env)/config.py 同步。

### v0.9.112.1  

**readiness splash「逾時」再修（user: 「每次 重啟 Qdrant和LLM 都會顯示 逾時」
— v0.9.80.1 同症狀殘留面：當時只把 mcp 列改非阻塞，qdrant/llm 兩列仍陪葬在第一發 `get_readiness` 上）。** 實測證據：boot 時 prewarm 建 MCP fleet 榨 CPU 16 秒（app.log 10:54:24→10:54:40）＋ 首呼 `Api._get_store()` ~2.5-2.7 s — 冷啟第一發 readiness >5s = splash `SPLASH_CHECK_TIMEOUT_MS=5000` → qdrant 與 llm（純 env 讀 0ms，純被拖下水）兩列同印「逾時」。修法 `gui/api.py::_qdrant_stats_check`：stats 進共享 thread-pool（`_readiness_executor` max 2）+ 硬 timeout 2.5s → timeout 走 `_readyz_probe`（一發 urllib GET /readyz，**不建 store**）→ ok=True "server up (readyz; stats pending)"（超時後 store 建構仍在背景完成 → 暖單例 → 之後 poll 帶完整 counts）；worst case 4.5s < 5s — 兩列不可能再逾時；raise 分支照舊逐字錯誤（舊契約不破）。順手修：`get_stats` 維度欄位實名 `dim`，readiness 讀 `vector_dim` 幽靈鍵 → splash 一直顯 `?d` — 兩種拼法都收；llm 列擴 5-provider（OPENROUTER/SILICONFLOW/OPENAI_COMPAT keys 也算已配置 — openrouter 主 installs 不再誤導 ❌；detail 只列有訊號者 → 舊精確字串契約不破）。前端 app.js 零改動。tests: tests/test_gui_readiness.py +6（stats 慢→readyz ok / slow+dead →顯式錯 / readyz 200+refused / 全 provider detail / dim 鍵）= 23 passed（既有 17 不破）；live 實證 2,539ms 首建情境下 `4248 pts / 4096d | llm=ollama + openai + openrouter + openai-compat`、#1 起 0-1ms。pyproject 0.9.112.0 > 0.9.112.1。

### v0.9.112.0  

**嵌入韌性雙缺口修復（user 核准「A + B + backlog 文件」— 評估對象 `project_improvement_plan_v3.md`；逐項查證結論：P0-2 已於 v0.9.25.3 落地結案；「auto 改 openrouter 優先」不採（session 新實測 local 33ms/塊推翻 GPU-cap 前提）；Jev/Nimble 本地化不採（rerank.py 實測 prefill 40–100s）；記憶遺忘層 v0.9.111 已覆蓋大半）。** ①**llama.cpp 嵌入看門狗** `src/embed_watchdog.py` — 第三個 auto-heal、唯一可 KILL：boot heal 只抓「程序死了」（refused），殭屍態（程序活著、`/v1/models` 200、每個 embeddings POST 500 — 2026-09-14 ErrorDeviceLost live log）只有**真實嵌入 POST** 探得到；`EmbedWatchdog` daemon thread 每 `QDRANT_LLAMACPP_WATCHDOG_INTERVAL`（預設 1800s）POST `{"input": "embed watchdog probe"}` 驗形狀（200 + `data[0].embedding` 非空），**只信 HTTP 不信延遲**（ingest 大 batch 健康伺服器也慢——延遲判定會誤殺）＋連續 2 次失敗才動作＋重啟冷卻 600s 防迴圈；修復鏈 = `netstat -ano` 解析嵌入埠 PID（**不濾狀態欄**——zh-TW netstat 印「聆聽中」非 LISTENING）→ `taskkill /F /T /PID` → detached spawn `QDRANT_LLAMACPP_START_BAT`（預設 vulkan bat、fallback cpu bat；Popen 形狀同 109.x healers）→ 等就緒 ≤150s；bat 缺檔 = 不 kill（殺了不能復活的不殺）；全錯誤吞掉；掛 prewarm（`_start_embed_watchdog`）+ `Agent._runtime_ops("status")` 露 `embedding_watchdog` 區段（get_runtime_status/runtime-local/GUI 可見）。netstat 解析 live 實證：779 行抓 :8080 PID 170404。②**hash 向量污染防護** — embed 鏈最後一手是非語意 hash，之前零記錄 → 劣化批次悄悄污染且無法善後：(a) `Embedder.embed_batch` 記 `last_backends`（per-item 實際 backend；LRU/disk 命中=當下 resolved，best-effort）→ pipeline payload 寫 `embedding_backend`；(b) `QDRANT_EMBED_FAIL_CLOSED=on`（預設 off=同舊版）→ hash 批次 **upsert 前** raise、該檔被 per-file try/except 跳過印明確錯誤；(c) `src/ingest/reembed.py` 審計=每點 text 重算 hash 向量（新公開 `Embedder.hash_fallback_vector`，無網路無快取）與存點**精確比對**（np.allclose f32；f32 round-trip 容差 1e-6）→ **無標記舊點也抓得到**；重嵌=新公開 `Embedder.embed_fresh`（**繞過 LRU+disk**——正常 `embed()` 會從快取回同一顆污染向量修不動）+ 標記更新 upsert；重嵌仍產 hash（全 backend down）→ **該點不 upsert** 記 `still_hash`（修復不能撒謊）；hybrid named-vector 取 dense `""` 腿、sparse-only 跳過。暴露面：GUI Admin Embedding 卡 🔎審計/♻️重嵌（i18n ×4）+ `cli.py audit-backends / reembed-hash`（`backfill-sparse` v0.9.87 同型）。**ROADMAP 存查（README 節）**：Qwen3-4B 並行驗證食譜（per-collection 切換已支援）、STAIR 標題 metadata（loader 結構抽取基礎已有）、EmbeddingGemma2 觀察項、已否決 3 項理由存查；音箱韌體=硬體活動非本專案。tests: +25 test_embed_watchdog.py（探針/tick streak+cooldown/netstat 解析（本地化+埠尾精確+IPv6+去重）/重啟全鏈（kill+spawn 形狀、已死只 spawn、bat 缺檔零動作、ready 窗耗盡）/prewarm+status 接線+行為+singleton/idempotent）+32 test_reembed_admin.py（last_backends 三態+embed_fresh 繞快取 poison-row 實證+hash 確定性+pipeline 標記/fail-closed 寫零筆/bool 解析+audit tagged+untagged/dim-skip/不動點/envelope+reembed 修復/仍hash不動/limit/單點錯誤/分批[3,3,1]+gui×3+cli+前端按鈕 i18n×4+helper×7）；回歸 embedder_resilience/runtime/autoheal×2/prewarm 68 + memory/task/task_llm/fire/plan_scope 101 — 全綠。pyproject 0.9.111.0 > 0.9.112.0；README(+ROADMAP)/ARCHITECTURE(header+Appendix A)/USER_MANUAL/.env(.env.example 4 env)/config.py 同步。

### v0.9.111.0  

**JAZ 5 建議採用 4 項 (user 核准「按建議執行」；順序 = ③作用域 → ⑤fire pin → ②交接約定 → ①記憶追蹤；不採用：②全量化子 Agent、④自我優化、唯讀工具快取 — watch/webhook 語意=新狀態，快取會破壞 fire 判斷)。** ①**作用域機制化**：`compile_plan(tools_allow=…)` plan 級 + `stages[*].tools_allow` 覆寫 → `_run_stage` 經 `chat_turn(active_tool_names=…)`（v0.9.14.0 GUI picker 同一道 per-TOOL 閘）— 每 stage 回合只暴露白名單工具（未知名=單純不暴露、絕不回落全量；confirm/沙盒是 code 級不受影響）；compile fail-closed（非 list/空/>60 → PlanError）；get_plan_status 每 stage 露 tools_allow/handoff_path；schema+prompt 同步。②**fire 模型 pin**：`Schedule.llm`/`WebhookTrigger.llm`（`provider:model`）— ADD 用新 `task_llm.coerce_llm_pin` 目錄驗證 fail-closed；FIRE 失效自動降級預設鏈+警告（排程韌性優先，與 task 投放 refuse 語意刻意不同）；五個 fire factory 全升 `lambda llm=None: Agent(..., task_llm=llm)`（agent.py scheduler/webhook、cli ×2、gui/api run_now、host+stdio scheduler_server run_now）；begin_task 帶 llm；CLI `schedules add --llm`。③**機械交接檔**：stage 終局**驅動器**寫 write 沙盒 `out/<stage_id>.json`（status/summary≤2000/artifacts/session 指標；驗證失敗也誠實寫 failed）→ 有依賴 stage 的 compiled_prompt 注入上游交接檔絕對路徑 + 禁重做上游 + 缺檔=上游未完成 — 無損交接不再依賴 LLM 自覺；寫失敗只降級。④**記憶補完**：recall 命中 → `MemoryStore.touch_hits`（fire-and-forget thread）回寫 hit_count/last_hit_*（`QDRANT_MEMORY_HIT_STATS=off` 退出）→ `list_recent_memories` 露 usage 總表（never_hit/30 天零命中 → purge 決策）；run_task_once DONE + plan 終局 → `store_task_result(kind=task-result)`，**store-NOT-recall**（`_memory_block` 自動召回 must_not 排除 — v0.9.67.2 fire 記憶教訓的隔離設計；發現走 `search_memory(kind='task-result')`；`QDRANT_TASK_MEMORY_SINK=off` 退出）。順手修 `test_task_runner.py` 3 個 v0.9.110 過時測試（4 工具面 + `_fresh_agent(llm=None)` stub 契約）；`scheduler_server._do_add` 發現並修 `_err` 區域變數遮蔽 module 函式的 UnboundLocalError。tests: +18 test_plan_scope_handoff.py +13 test_fire_llm_pin.py +21 test_memory_usage_tracking.py；回歸 skill/task_llm/task_parallel/memory_store 107 / task_runner 25 / scheduler+server+verification 127 / webhook+server+task_runner 110 / schedule_cli 6 — 全綠。pyproject 0.9.110.1 > 0.9.111.0；README/SKILL_GUIDE/USER_MANUAL/.env(.env.example 兩新 env)/config.py 註冊同步。

### v0.9.110.0  

**task/stage 層級 LLM 模型 pin (user: 「task 可以在 runtime 依任務的要求使用不同 LLM 模型（目前限制 openrouter 和 ollama cloud 可以提供的模型），沒有要求使用 default 的 llm 模型跑」):** 新模組
`src/agent/task_llm.py`（zero-side-effect：單冒號 `provider:model` parser — ollama 模型名自帶冒號故 split 限 1；provider 白名單= openrouter+ollama；真實目錄探針 openrouter /api/v1/models ≈466 + ollama /api/tags ≈17（OLLAMA_BASE_URL /v1 後綴自動剝除、`-cloud` 別名→ 目錄 canonical id；TTL cache 600s）。Agent pin 尊重 = fail-closed：①`Agent(task_llm=…)` `__init__` 新參數 → `_pick_provider` 開頭 pinned 分支直接回 (provider, model, client)，LLM_PROVIDER/鏈序/模型解析全部不適用 ②`_provider_candidates()` pin 存在回單一 triple = 跨供應商 failover 對 pin 關閉（live E2E 實證 pin 撞 404 原本會偷偷跌到 ollama）③pin 的 openrouter turn 送 clean extra_body（global routing 的fallbacks=false 會濾掉 pin 模型的 endpoints → 404 — routing 是為 default 模型調的）。接入面：`run_task_once(llm=…)` 投放前驗證（未知模型拒絕+建議、目錄不可達 ok+warning）；task-local 新 4 工具 `list_task_models(provider?, contains?)`；task_runner.begin_task(llm) registry 欄位；skill_compiler `compile_plan(default_llm=…, stages[*].llm=…)` compile 當下驗證 + `_run_stage` stage_llm 工廠 `llm=` 呼叫（legacy 零參 factory TypeError fallback）；stdio task_server 同步；system prompt Background tasks + Skill 兩區塊指引。tests: +24 tests/test_task_llm.py + 7 套 failover 相關回歸 95-96 passed；LIVE E2E 三部曲（openrouter pin 真 turn OK 17.9s / spawn-site ollama:glm-5.3 task done session OK / 假模型投放點拒絕無任務）全綠; plan-driver `load_plan→None` 加 `or rec` 韌性。pyproject 0.9.109.4 > 0.9.110.0.

### v0.9.104.6  

**使用者可匯入 system rules (user: 「提供用戶可以匯入 system rule」):** 新模組 `src/agent/system_rules.py` — 使用者規則檔自動附加成 `USER SYSTEM RULES` 區塊（tools+legacy 兩種 prompt）。匯入方式：(1) 把 `system_rules.md` 丟進 READ sandbox（存在才載入；mtime 快取 → 改檔即生效免重啟）(2) `.env QDRANT_SYSTEM_RULES_FILE` 指任意路徑（~ 展開）。保護：`QDRANT_SYSTEM_RULES_MAX_CHARS` 預設 8000 截斷、永不 raise（缺檔/目錄 → 空 = prompt byte-identical）、區塊標明工具/安全規則優先。tests: +13 tests/test_system_rules.py。pyproject 0.9.104.5 > 0.9.104.6.

### v0.9.104.5  

**describe_image 準確度稽核 + token 浪費修復 (user: 「tool describe_image 的準確度是否有問題，導致寄信作業花了大量的 token」):** live 2026-10-02 13:33 寄信輪 tokens=1,223,087（39 dispatches）。診斷：**vision 描述本身準確**（ollama-cloud 如實回報收件匣+沉浸式翻譯彈窗 — 這正是無法畫面驗證的原因）；爆量是三個機制問題：(1) `raw_image_base64` 231,424 chars/次 ×3 純浪費（text-only loop 從不消耗、_strip_base64 每輪剝掉）→ host 不再回傳該欄位（回 note；resize_image 維持原樣），工具描述同步（in-process + stdio server.py）；(2) SHA-1 cache 是實例 dict 而 host 每次 `ImageDescriber()` 重建 → 永不命中（from_cache: False 實證）→ cache 升 process 層級（`_SHARED_CACHE` + Lock；`last_cache_hit` 旗標 + host `from_cache` 偵測改用它 strict is True；key 已含 prompt）；(3) 同畫面截兩次（兩檔 MD5 相同）+ stat_file ×2 + describe ×3 → system prompt「截圖驗證紀律」區塊（output_path 覆寫陷阱、同圖勿重 describe、DOM snapshot 為事實來源）。schema 實測 28,399 chars ≈ 7.1k tokens（index 已生效，非主因）。tests: +TestSharedCacheV0104_5（跨實例共享/host slim+from_cache）+ 2 檔 autouse shared-cache fixture + locked-in 斷言反轉；相關 144+ passed。pyproject 0.9.104.4 > 0.9.104.5.

### v0.9.104.4  

**exec-local 升格為基礎 MCP (user: 「將 MCP Local exec 設定成基礎 MCP」):** `_TOOLROUTE_CORE_SERVERS` += "exec-local" — run_python/run_bash/run_shell/run_grep/run_awk (+script-library 3) 每輪都在工具面上，不需 exec class 命中（v0.9.102.2 scheduler-local core 化同款）；exec class 映射與 union-only 不變。+1 test；相關 50 passed。pyproject 0.9.104.3 > 0.9.104.4.

### v0.9.104.3  

**TOOL-RESULT 爆炸防呆 — Gmail 全頁 HTML 事故修復 (live log 2026-10-01 17:17：firefox_craw get_page_html(tab_id) 回 Gmail 整頁 HTML ~6MB → 每個 create 全毀 — OpenRouter 404 context filter（1.2M tokens）、ollama 400 prompt too long 1,751,656、openai-compat timeout → 寫信任務 157 秒降級失敗)**:** 根源 1 — live .env 自 v0.9.82.5 就寫 CHAT_CONTEXT_COMPRESSION=on 但 _MODES=(off/auto/aggressive) 不認 "on" → fallback silent off → offload 整組失效（chat_offloads/ 0 檔案佐證）；修 = _cfg() 認 on/yes/true/1 = auto 別名。根源 2 — offload 是 best-effort（寫檔失敗 → inline 不警告）；修 = agent.py 新 _TOOL_RESULT_CAP_DEFAULT=51200 + _tool_result_cap()（QDRANT_TOOL_RESULT_MAX_CHARS；0=不限；unset/invalid/負 → default）+ _run_tool_call 對每個工具結果（成功與錯誤皆然）執行絕對截斷：>cap → 截斷＋…[tool result truncated: N chars over the M-char safety cap]…＋指引（窄參數重下/read_file 副本）；51200 ≈ 13k tokens。tests: +12 tests/test_tool_result_cap.py（resolver 矩陣/oversized 截斷/0=不限/error 亦 cap/on 別名×5/on→offload 寫 read sandbox/off 模式 cap 仍擋）。.env.example 兩項記錄。pyproject 0.9.104.2 > 0.9.104.3.

### v0.9.104.2  

**FIREFOX_CRAW MCP 上線 (user: 「我設定了 mcp tool firefox_craw 但不能用」):** 診斷 — config/mcp.json 的 firefox_craw（stdio mcp_getweb/dist/server.exe）連線是好的（log ×2 [mcp:stdio:firefox_craw] connected + google_search 撞名自動改名 firefox_craw__google_search）；攔住它的兩層：(1) MCP_ACTIVE_SERVERS 沒列它 → 工具全隱藏（v0.9.41.1 教訓；使用者加過但舊進程沒吃到）(2) ToolRoute(on) 無 class/hint 會掛它。修法：.env MCP_ACTIVE_SERVERS += firefox_craw（使用者先加過 → 去重單條）；agent.py web-external class = (my_web, firefox_craw) + 新 _toolroute_browse_hint（明示瀏覽器意圖才 union: firefox/firefox_craw/get_page_html/get_x_home/get_x_post/download_x_post/mcp_getweb/瀏覽器/x.com/twitter.com/crawl/browse）+ _TOOLROUTE_SERVER_TRIGGERS firefox 列（上輪回覆 mining）+ system prompt「瀏覽器網頁」區塊（web-local 先行；x.com/needs-JS/web_fetch 擷不到 → get_page_html；讀頁免 confirm、存檔屬寫入）。tests: +9 tests/test_firefox_route.py；toolroute 相關 77 passed。LIVE E2E（重啟後）：question「看一下 x.com/elonmusk 的最新 po 文內容」→ class=web-external p=0.98 servers 含 firefox_craw → dispatch get_page_html → OK（X 防 JS 擋頁 → 模型 fallback my_web，回報真實貼文）；dispatches=8。清掉雙進程 listen（:8334 兩 PID → 單一 89828）。pyproject 0.9.104.1 > 0.9.104.2.

### v0.9.104.1  

**TOOLROUTE CONTEXT (user: 「jev mcp tool gate 目前只看當下 prompt 內容進行判斷會造成 有些 如 ok、執行、繼續、run 、核準 的回覆會 造成 後續 llm 執行 tool call會因為 JEV MCP GATE沒有加入 應對的 MCP TOOL而失敗， 建議 JEV 可以加入 上一個 LLM 回覆內容進行判斷」):** 接續型回覆（ok/執行/繼續/run/核准）無類別關鍵字 → Jev 只看當下 prompt → 工具面失去上輪宣告的 fleet（v0.9.13.2 發現陷阱多輪版）。修法：agent.py `_prev_assistant_reply(history)`（最後一則 assistant 回覆；跳過空/error、cap 700、never raise）+ `_TOOLROUTE_SERVER_TRIGGERS` ×`_toolroute_prev_reply_servers`（mining 上輪回覆宣告的工具名前綴 send_telegram/stock_/keep_/cred_/archive_/run_python/onvif_/hicloud_/… 與 CJK 動詞 下單/報價/K線/攝影機/行事曆/壓縮/背景執行 → server；UNION-ONLY 只加不減；垃圾 → []）+ `_toolroute_servers_for(cls, question, prev_reply=None)` union + `_jev_toolroute(question, prev_reply=None)`（短題 ≤64 字才附加帶標註的上輪回覆塊 cap 500，判斷文本 cap 1200）。chat_turn impl thread prev_reply 至 Jev 分類與 union 兩處；ask（單輪無 history）維持 2-arg。tests: +12 tests/test_toolroute_prev_reply.py + test_toolroute/test_imagegen call-site 守衛更新；85 passed（4 套合跑）。LIVE E2E（web 重啟後）：question「ok」+ 歷史（助手宣告 stock_get_quote）→ class=stock p=0.97 servers 含 stock-local → 真 dispatch stock_get_quote（live 2,495/2,500）dispatches=1；修復前同場景 = class=rag 無 stock-local dispatches=0 answer=0。pyproject 0.9.104.0 > 0.9.104.1.

### v0.9.104.0  

**IMAGEGEN 產圖 MCP (user: 「使用這個製作 產圖 的 MCP擴充功能」):** src/imagegen_tools.py ImageGenEngine — OpenRouter dedicated Image API POST /api/v1/images/generations (model/prompt/n/aspect_ratio/resolution/size/quality/output_format/seed → data[{b64_json,media_type}] + usage.cost；全部成功才計費；live 探針 recraft-flash 實測 $0.007/張 ~4s) + GET /api/v1/images/models 模型目錄。IMAGEGEN_MODE=mock 預設（Pillow 確定性 placeholder，NO network，simulated: true）| live（重用 OPENROUTER_API_KEY，每張圖真實費用）；兩道寫入閘（hicloud 收斂）：confirm=true（mock 亦強制）+ live 另需 IMAGEGEN_WRITES_ENABLED=on；引擎回傳解碼 bytes（絕不回流 base64 到 LLM context）。新 ImageGenLocalHost (server imagegen-local, 4 工具: image_generate / image_gen_list / image_gen_models / image_gen_status) + stdio mirror src/mcp_server/image_gen_server.py + manager registers before Meta (_imagegen_local_host) + prompt 產圖觸發區塊 + toolroute image class（imagegen-local）+ _toolroute_imagegen_hint（v0.9.103.2 telegram-union 同款；drawio/圖表 排除）+ config 6 env (IMAGEGEN_MODE/MODEL/WRITES_ENABLED/MAX_IMAGES/TIMEOUT_S/DIR) + live .env MCP_ACTIVE_SERVERS += imagegen-local。tests: +40 tests/test_imagegen_host.py；LIVE E2E：e2e-test.webp 624KB $0.007 4.0s。**v0.9.45 → v0.9.103.5 的完整變更紀錄以 README.md 更新日誌為準（本檔事故截斷後自 git index v0.9.44.0 版還原重建）。**pyproject 0.9.103.5 > 0.9.104.0.

**Canonical pattern for adding a 5th LLM provider** (realized in
v0.9.19.0 with `openai-compat`): data-driven
tuple entry (`(name, key_fn, url_fn, model_fn)`) appended to
`Agent._PROVIDER_ORDER` + an env-var block in `src/config.py`
using `os.environ.get(...)` defaults + one entry in
`Config.__repr__` API-key hiding list + a `;` .env.example
comment block + 4-5 tests in `tests/test_llm_providers.py`
(auto-detect wins, manual override, override+empty-key error,
default base_url, default chat_model). Note from the realized
v0.9.19.0 pass: ALSO needed were (a) a per-provider guard when the
provider has a REQUIRED env (openai-compat's base_url — raise in
`_pick_provider`, skip+warn in `_provider_candidates`; a naive
default would silently hit api.openai.com), and (b) the override /
no-provider error messages updated to name the env var via
`.upper().replace('-', '_')` (naive `upper()` renders
`OPENAI-COMPAT_API_KEY` for a dash-named provider). Adding a 6th
provider = one tuple entry + env block + repr entry + `.env.example`
block + tests + (if it has required envs) the two guards.

Historical (still in tree): v0.8.8 multi-level Todo List MCP.
16 個 TodoLocalHost tools (`create_task` / `add_subtask` /
`update_task` / `delete_task` / `list_tasks` / `get_task` /
`start_task` / `complete_task` / `skip_task` / `fail_task` /
`add_task_dependency` / `reorder_task` / `get_todo_progress` /
`list_task_journal` / `render_task_tree` / `execute_plan`)
exposed both in-process and via stdio MCP server
`python -m src.mcp_server.todo_server`. Agent system prompt gains
plan / execute trigger block (「幫我規劃」「做個計畫」「拆解
任務」「step by step」「現在到哪裡了」「下一步做什麼」「任務
卡住」). CLI gets `todo {list,show,add,tree,progress,complete,
delete}` sub-command. 103 new tests. v0.8.7 baseline 189 → 325
passed, 0 failed.

Historical (still in tree): v0.8.7 schedule lifecycle caps +
LLM progress tracking. Schedule gains `max_fires` / `expires_at`
/ `status` fields with auto-disable + lifecycle stamping;
`ScheduleManager` exposes `get_progress` + `list_runs` /
`set_max_fires` / `set_expires_at`; `Scheduler.tick_once` does
two-phase lifecycle re-evaluation per tick; 2 new MCP tools
(`get_scheduled_task_status` + `list_scheduled_task_runs`)
exposed both in-process and via stdio MCP server; CLI
`schedules add` adds `--max-fires` + `--expires-at` flags;
Agent system prompt gets a Schedule-progress trigger block.
42 new tests.
Built on the v0.4.0-v0.7.0 base: chat REPL + milvus-style ingest
pipeline + EPUB loader + ingest robustness + structured DOCX/PPTX
extraction + local llama.cpp Qwen3-Embedding-8B via Vulkan +
per-collection backend switch (local / Ollama Cloud / OpenAI /
hash) + Ollama Web Search augmentation + per-session question
dedup + per-document removal (v0.7.0). Chat output uses
CJK-aware ANSI-boxed GFM table rendering so markdown tables no
longer break the narrow BBS-style frame.

**v0.8.5 closes the locale loop** — users reported
"選擇繁體中文介面,但回答出現簡體中文": the GUI language picker
only swapped the UI strings; the LLM never got told what locale
to answer in, so responses drifted to whatever the model picked
(often Simplified Chinese on a mainland-hosted inference hub).
`Agent.ask()` / `chat_turn()` now accept a `response_language`
parameter (zh-TW / zh-CN / en / ja) and `_system_prompt()`
appends a `LANGUAGE DIRECTIVE` block — with a carve-out for
filenames / library names / code identifiers so citation
integrity isn't broken. The GUI `app.js::sendChat` forwards
`currentLang` automatically; the CLI exposes
`--lang <code>` on both `ask` and `chat`, plus a `/lang [code|auto]`
REPL command. Default is `None` (preserves v0.8.4 behaviour — no
directive, LLM picks). 20 new unit tests in
`tests/test_response_language.py` cover the whole chain — 164
passed, 1 skipped.
**v0.8.6 closes the scheduler control loop** — the
Schedules tab in v0.8.0 gave users a GUI to add cron jobs, but
the LLM couldn't drive it. Users reported "幫我排程一個任務…"
hit a dead end mid-conversation. v0.8.6 wraps `ScheduleManager`
as 8 MCP tools (`add_scheduled_task` / `delete_scheduled_task` /
`list_scheduled_tasks` / `get_scheduled_task` /
`enable_scheduled_task` / `disable_scheduled_task` /
`run_scheduled_task_now` / `get_scheduled_task_errors`),
exposed both **in-process** (`SchedulerLocalHost` auto-
registered by `McpManager.from_config`) and as a **stdio MCP
server** (`src/mcp_server/scheduler_server.py`, runnable via
`python -m src.mcp_server.scheduler_server` for external
clients like Claude Desktop). The Agent's system prompt now
includes a Schedule-management trigger block that teaches the
LLM when to invoke each tool ("排程" → add, "停掉" →
disable, "為什麼失敗" → get_errors, "立刻跑" → run_now).

The release also fixes a **silent failure bug** in
`Scheduler.tick_once`: v0.8.5 only logged `on_fire` exceptions
to stderr — the daemon kept running but the error vanished.
v0.8.6 calls `mgr.record_error(sid, e)` so the
`Schedule.last_error` / `last_error_at` fields persist across
restarts and the LLM can later answer "為什麼 X 失敗了".
`make_agent_on_fire` is wrapped symmetrically: if
`agent.chat_turn` itself raises (Qdrant down, OpenAI 5xx,
network glitch) the schedule still records the failure
before the exception propagates.

Backward compatibility: `Schedule.from_dict` uses
`__dataclass_fields__` introspection, so v0.8.5 JSON files
load cleanly without `TypeError: unexpected keyword
argument`. `ScheduleManager.set_enabled` / `clear_error` /
`record_error` / `find_by_name` are new methods on the same
class; old callers keep working unchanged. 81 new tests
(47 scheduler_server + 15 scheduler_local_host + 13
scheduler_v086 + 6 system_prompt_scheduler); 282 passed,
1 skipped total.
**v0.8.6.1 fixes a silent LocalTool argument-coercion bug** —
user log 2026-08-31 showed every `add_scheduled_task` call
crashing with `AttributeError: 'str' object has no attribute 'get'`.
Root cause: the schema we emit has a single `args: object` property,
and the LLM wrapped its call under that key (e.g. `{"args": "..."}`).
`LocalTool.__call__` then expanded the wrapper as kwargs and called
`_add(self, args=<str>)` — `_validate_add_args` then `.get()` on a
str → boom. Two changes in `src/mcp_client/host.py`:

* `_coerce_arguments(payload)` (pure helper, ~60 lines) normalises
  `None` / bare JSON string / `{"args": {...}}` / `{"kwargs": ...}`
  / `{"params": ...}` / single-element list / `__dict__`-bearing
  objects into a flat dict. Unrecognised shape falls back to `{}`
  so the LLM gets a clean error rather than a Python traceback.
* `LocalTool._call_args(kwargs)` introspects the handler
  signature: if it's `(self, args: dict)` (the convention every
  host uses) the normalised dict is passed as a single positional
  arg instead of unpacked as kwargs.

Both are tested in `tests/test_local_tool_argument_coercion.py`
(34 tests). Coverage spans `TestCoerceArguments` (18 unit),
`TestLocalToolNormalisation` (5), `TestSchedulerLocalHostWrapperTolerance`
(10 — exercises all 8 scheduler tools via wrapper dict),
`TestWebSearchHostUnaffected` (1 — confirms kwargs-spread path
unchanged). v0.8.6 baseline 282 → 305 passed, 2 skipped total.

**v0.8.6.2 closes the scheduler-Telegram loop** — the Schedules
tab + Scheduler MCP in v0.8.6 gave the LLM the ability to *build*
schedules, but the system prompt never mentioned Telegram trigger
words. User log showed `answer=0 chars` after the daemon fired
(`22:08:06 scheduler: fired ... answer=0 chars`) because the LLM
had no clue it should reach for `send_telegram_message`. Fix: added
a "Telegram / external messaging (v0.8.6.2+)" trigger block to
`Agent._system_prompt(tools_enabled=True)` covering:

* Chinese + English + romanised trigger phrases
  (`用 telegram 發` / `send via telegram` / `發訊息給 chat_id`)
* The `TELEGRAM_ALLOWED_CHAT_IDS` security model
* `list_allowed_chats` as a helper for unknown chat_ids
* **The composition trap**: a dedicated sub-block that calls out
  the exact "schedule + Telegram" scenario by name — telling the
  LLM that a scheduled prompt mentioning Telegram MUST itself
  call the tool, not just describe the message in prose.

8 new tests in `tests/test_system_prompt_telegram.py` lock down
each clause + the legacy `tools_enabled=False` exclusion + the
`response_language` composition. 313 passed, 2 skipped.

**v0.8.6.3 fixes LLM-built schedules never firing** — root cause:
`Agent.scheduler` is a lazy property that only constructed + started
the daemon thread on an *explicit* `agent.start_scheduler()` call
(GUI button / CLI `schedules daemon`). The LLM calling
`add_scheduled_task` via chat saved the JSON file but the daemon
thread was never spun up, so `next_fire_at` slipped into the past
and nothing ever fired (`使用 llm 建立 排程,建立後 排程 不會按時執行`).

Three-file fix:

1. `src/mcp_client/host.py::SchedulerLocalHost` gains an
   `_auto_start` callback + `attach_auto_start(cb)` setter.
   `_add` invokes the callback after a successful save (try/except
   so a misconfigured callback can't poison the success response).
2. `src/mcp_client/manager.py::McpManager` gains
   `auto_start_callback` (set by Agent) + `_scheduler_local_host`
   reference (kept by `from_config`) + `set_auto_start_callback(cb)`
   canonical setter that pushes the callback down to the host AFTER
   `from_config` has finished wiring it (the race that bit me
   during development).
3. `src/agent/agent.py::Agent.mcp_manager` lazy property wires
   `set_auto_start_callback(self._auto_start_scheduler_daemon)`
   on first access. The daemon kicker wraps `start_scheduler()` in
   a try/except — exceptions are swallowed because the schedule is
   already on disk; the user can manually start the daemon via GUI.

The auto-start is **idempotent** (`Scheduler.start()` no-ops when
the thread is alive) and **lazy** (no daemon thread until the
first `add_scheduled_task` succeeds). CLI `schedules daemon`
subcommand still works the old way — auto-start is opt-in for
the GUI / chat path. 16 new tests in
`tests/test_scheduler_daemon_autostart.py` cover every link of
the chain. v0.8.6.2 baseline 313 → 329 passed, 2 skipped.

**v0.8.7.1 closes the chat-on-close safety loop** — user log
2026-08-31: 「離開 chat ui 程式不會存對話記錄」. Root cause:
`gui/app.py` used `confirm_close=True`, which pops the OS
confirm dialog but wires NO save path — users had to click
💾 儲存 explicitly or lose the whole chat draft. Fix: 3-layer
safety net.

* **Layer 1 — JS auto-save after reply** (`sendChat` → `autoSaveChat`).
  Best-effort background save fired the moment the assistant
  reply lands. Default code path so close is rarely the
  critical save.
* **Layer 2 — JS `beforeunload` listener** + `_beforeUnloadSave`.
  Window unload triggers one last fire-and-forget
  `api.save_session` plus a `syncStateMirror()` push of
  `chat_session` into `window.pywebview.state` (the shared
  state object Python can read synchronously).
* **Layer 3 — Python `window.events.closing` hook**
  (`gui/app.py::_save_chat_draft_on_close`). Calls
  `window.evaluate_js` to fetch the state mirror JSON, runs
  through `SessionManager.save()` synchronously. Removes
  `confirm_close=True` so the OS dialog is unnecessary; the
  closing handler itself saves + exits cleanly.

`window.events.closed += _on_window_closed` is the
last-resort log entry — fires after OS dialog confirmed the
close; by then Layer 3 has already persisted.

14 new tests in `tests/test_chat_autosave.py` cover all 7
Python-side mock-window scenarios (persist / no-state / missing
chat_session / null id / evaluate_js exception / overwrite
existing session / bad JSON) plus 7 JS source-level sanity
checks (function definitions, beforeunload wiring,
visibilitychange mirror, sendChat tail-call, four session
mutation paths all call syncStateMirror, Python side wires both
hooks, `confirm_close=True` is gone). v0.8.7 baseline 175 →
189 passed, 0 skipped total.

**v0.8.7.2 fixes the GUI white-screen freeze on close (v0.8.7.1
regression)** — user log 2026-08-31 17:41:48: `qdrant-agent-gui.bat`
hung at `pywebview window is closing - flushing chat draft`
with the window going white until the user force-killed the
process. Root cause: the v0.8.7.1 Python closing hook called
`window.evaluate_js(...)` to harvest the chat session from
`window.pywebview.state`. When the `closing` event fires, the
WebView JS thread is in the middle of tearing down (renderer
shutdown) and has stopped accepting new work; the CEF /
WKWebView / WebView2 backend blocks the call indefinitely
waiting for a response that will never come -> GUI process
hangs in the close handler.

Fix v0.8.7.2:

* **Python closing hook is a no-op stub** — `_save_chat_draft_on_close`
  no longer calls `evaluate_js`. The `closing` event handler
  is still wired (the v0.8.6.3 auto-start hook relies on
  similar event plumbing) but it just logs and returns.
* **JS-side `localStorage` backup** — `autoSaveChat()` writes
  the latest chat draft to `localStorage['chat_draft_backup']`
  BEFORE the `api.save_session` round-trip. If the round-trip
  + beforeunload both fail, the next boot can recover from
  this synchronous JS-side store. `localStorage` is
  guaranteed to land before the renderer dies (sync write).
* **JS-side `bootDraftRecovery()`** — runs on `bindEvents`
  startup, checks for `chat_draft_backup`, auto-restores into
  `chatSession` if found, and shows a toast "已從上次未存檔
  的草稿復原 N 則訊息". The round-trip's success path
  clears the backup so it isn't confused with an unsaved
  draft on the next boot.

3-layer safety net no longer depends on Python:

  1. **JS `autoSaveChat`** — every assistant reply saves to
     Python AND mirrors to `localStorage` (v0.8.7.2)
  2. **JS `beforeunload`** — fires fire-and-forget
     `api.save_session` as the renderer tears down (v0.8.7)
  3. **JS `localStorage` backup + `bootDraftRecovery`** —
     belt-and-suspenders, next-boot restore (v0.8.7.2)

4 new tests in `tests/test_chat_autosave.py`:
  * `test_close_hook_is_noop_v0871` — hook does not call
    `evaluate_js` (MagicMock assertion)
  * `test_close_hook_does_not_touch_disk` — hook creates no
    session files
  * `test_app_js_uses_localstorage_for_draft_backup` — JS
    source-level: `localStorage.setItem('chat_draft_backup'`
    must exist in `app.js`
  * `test_app_js_boots_draft_recovery_function` — JS
    source-level: `function bootDraftRecovery` must exist and
    be called from `bindEvents`
  * `test_app_py_close_hook_does_not_use_evaluate_js` —
    guard rail: prevents someone re-introducing the deadlock
    (strips docstring + comments before grepping the function
    body, so docstring mentions of `evaluate_js` are allowed
    but actual `.evaluate_js(` calls are not)

v0.8.7.1 baseline 189 -> v0.8.7.2 189 + 4 = 193 chat_autosave
tests passed; 0 failed.



**v0.8.4 fixes a silent GUI markdown failure** — `app.js::md()`
previously used `typeof window.DOMPurify === 'object'` to detect
the sanitizer. DOMPurify 3.x exposes itself as a named *function*
(`typeof === 'function'`), so the check silently failed and the
renderer fell back to plain text — users saw raw `**xxx**`
asterisks in the chat log. The fix duck-types on
`typeof DOMPurify.sanitize === 'function'`. `marked.min.js` +
`purify.min.js` are now also vendored under `gui/assets/vendor/`
with a `<script onerror>` CDN fallback so the renderer works
offline / behind firewalls. 11 new e2e tests in
`tests/test_gui_markdown_e2e.py` exercise the actual vendor
binaries via JSDOM + a local HTTP server — 144 passed, 1 skipped.

**v0.8.3 adds full GFM coverage** — `src/ui/md_render.py`
zero-dep regex engine now handles numbered / nested / task lists,
italic, strikethrough, blockquote, horizontal rule, inline links,
inline images, and backslash escapes. The GUI `app.js::md()`
extends DOMPurify `ALLOWED_TAGS` / `ALLOWED_ATTR` to include
`input` / `del` / `s` / `hr` / `img` / `figure` / `figcaption`
and adds a `hardenLinks()` post-processing pass that injects
`target="_blank" rel="noopener noreferrer"` on every `<a>`. The
previously broken `Style.GREY = "\x90m"` ESC literal is fixed to
`"\x1b[90m"`. 27 new unit tests in `tests/test_md_render.py` —
zero regression across the existing 134-test suite.

**v0.8.0 introduced a complete MCP host + client stack**:

* `src/mcp_client/` package — in-process tool host plus
  Stdio / Streamable HTTP / legacy SSE MCP clients (the third
  transports the official MCP SDK supports).
* `McpManager.from_config("config/mcp.json")` to load any number
  of third-party MCP servers alongside the always-on local hosts.
* `Agent.ask()` / `chat_turn()` now run a real OpenAI tool-calling
  loop: the LLM emits `tool_calls`, we dispatch through the manager,
  feed results back, and loop until the LLM emits a plain answer or
  we hit `max_tool_iterations`.
* CLI exposes `--mcp-config` / `--no-tools` flags and a chat REPL
  `/tools [on|off|status|list]` command.
* `config/mcp.json.example` ships with worked examples for stdio
  and both HTTP transports.

**v0.4.0: client-side MCP integration has been completely removed.**
The Agent no longer spawns MCP subprocesses, no longer reads
`config/mcp.json`, and no longer carries a `FirefoxClient`. The
files `src/tools/mcp_client.py`, `src/tools/mcp_registry.py`,
`src/tools/firefox_client.py`, `config/mcp.json`, `run_mcp_server.py`,
`qdrant-mcp-server.bat`, `.vscode/`, and the entire `examples/`
MCP-client section have been deleted. CLI: no `--mcp` flag, no
`/mcp` or `/firefox` REPL commands. Only context sources are now
local Qdrant + Ollama Web Search. `src/mcp_server/server.py` is
still shipped as a **server** so external MCP clients (Claude Desktop,
Cline, …) can connect via `python -m src.mcp_server.server` — it is
just no longer consumed internally.

---

## 1. Environment & Prereqs

- **Python** 3.10+ (conda env `milvus-mcp` is shared with the sibling)
- **Qdrant server** — one of:
  - Docker: `docker run -p 6333:6333 qdrant/qdrant`
  - **Windows native binary** (no Docker required): download
    `qdrant-windows-amd64.exe` from the latest GitHub release and run
    `C:\qdrant\start-qdrant.bat` (storage in `C:\qdrant\storage\`,
    snapshots in `C:\qdrant\snapshots\`). v1.15+ binary adds GPU/Vulkan
    support but needs LunarG Vulkan Runtime installed separately.
- **Vector store**: Qdrant via `qdrant-client>=1.10` (HTTP/gRPC).
  Tested on `qdrant-client==1.19.0`.
- **LLM provider** (priority):
  1. Ollama Cloud (`OLLAMA_API_KEY`) — `gpt-oss:120b-cloud` chat,
     `gemma4:31b-cloud` vision (for image describer)
  2. OpenAI (`OPENAI_API_KEY`) — any OpenAI-compatible `base_url`
- **Env vars**: load from `.env` (NEVER hardcode). See `.env.example`.
- **First move** before any code change: read `README.md` + `pyproject.toml`.

## 2. Testing & Delivery Standards

A change is **complete** only after all of the following pass:

1. **Module import test** — all modules under `src/` import cleanly.
   ```bash
   python -c "import sys; sys.path.insert(0,'.'); \
     import src.config, src.ingest.loader, src.ingest.chunker, \
     src.ingest.embedder, src.ingest.image_describer, src.ingest.pipeline, \
     src.vector_store.qdrant_client, src.vector_store.retriever, \
     src.agent.agent, src.agent.session, src.agent.scheduler, \
     src.agent.webhook, src.agent.telegram_bot, src.agent.context_compress, \
     src.log_analyzer, src.archive_tools, src.exec_tools, \
     src.mega_stock_tools, src.ui.md_render, \
     src.mcp_client, src.mcp_client.base, src.mcp_client.stdio, \
     src.mcp_client.http_sse, src.mcp_client.host, src.mcp_client.manager; \
# src.mcp_server/* are stdio servers for external MCP clients;
# skip them from the CLI smoke test (each runs `python -m`).
     print('All modules OK')"
   ```

2. **End-to-end CLI smoke**:
   ```bash
   qdrant-agent.bat ingest <file>     # also covers .epub / .docx / .pptx
   qdrant-agent.bat search "query"    --top-k 5
   qdrant-agent.bat ask "question"    # single-turn RAG
   qdrant-agent.bat stats
   qdrant-agent.bat chat              # BBS-style REPL (multi-turn)
   qdrant-agent.bat sessions list     # saved sessions CRUD
   ```

3. ~~**MCP stdio smoke** (server startup + handshake)~~ — removed in v0.4.0.
   The `qdrant-agent.bat server` and `run_mcp_server.py` entrypoints
   have been deleted. To smoke-test the server module, run directly:
   ```bash
   python -m src.mcp_server.server
   ```
   and connect a tiny stdio MCP client (e.g. ``mcp.client.stdio.stdio_client``)
   to it. Note this is for *external* clients only — our own CLI
   no longer talks to MCP.

4. **Graceful degradation**: any external dependency failure (Qdrant
   server down, API key missing, transient network glitch) must surface
   a clear error message to the user, not crash silently.
   - `cli.py chat` already wraps `agent.chat_turn` in try/except and
     prints `[錯誤] 問答失敗：...` then returns to input prompt
   - `cli.py ingest` per-file try/except skips + prints, never aborts
   - `cli.py stats` / `search` / `ask` should similarly never abort

### Core invariants

- Bump `pyproject.toml` version on every change.
- Update `README.md` with a new `### vX.Y.Z` entry under `## 更新日誌`.
- Keep `.env.example` in sync with any new env vars.
- After a clean collection reset (delete-collection + recreate), do
  at least one ingest + one search to verify the loop still works.

## 3. Module Structure

```
src/
├── config.py                # Config singleton (flat attrs + namespaced groups
│                            #   config.ingest.* / config.llm.* / config.embedding.*)
├── provider_chain.py        # v0.9.18.0+ ZERO-side-effect .env chain-order parser
│                            #   (LLM_PROVIDER_CHAIN / VISION_PROVIDER_CHAIN)
├── vector_store/
│   ├── qdrant_client.py     # QdrantStore (HNSW + payload filters, search via
│   │                        #   client.query_points(query=...) [qdrant-client 1.10+])
│   └── retriever.py         # Retriever.search (embeds query → search)
├── ingest/
│   ├── loader.py            # module-level load_file() + per-format _load_*
│   │                        #   + FileLoader class (extract_media + scan_directory)
│   ├── chunker.py           # module-level chunk_text() + TextChunker class
│   │                        #   + DocumentChunk dataclass (with metadata)
│   ├── embedder.py          # OpenAI / hash fallback, embed() + embed_batch()
│   │                        #   + 4096-entry LRU cache
│   ├── image_describer.py   # Ollama Cloud vision API, SHA-1 cache, 4 workers
│   └── pipeline.py          # load → fill images → chunk → embed → upsert
│                            #   (mirrors milvus-mcp-agent v0.1.19)
├── mcp_server/                # stdio MCP servers (external clients)
│   ├── server.py              # 5 core RAG tools (search / ingest / list / stats / read)
│   ├── filesystem_server.py   # 15+ sandbox file tools (v0.9.3+)
│   ├── scheduler_server.py    # schedule management (v0.8.6+)
│   ├── todo_server.py         # multi-level Todo (v0.8.8+)
│   ├── telegram_server.py     # Telegram gateway (v0.8.1+; v0.9.31.0+
│   │                          #   4 tools incl. send_telegram_photo)
│   ├── openwith_server.py     # open-with-associated-app (v0.9.6+)
│   ├── log_analyzer_server.py # log anomaly analysis (v0.9.7+)
│   ├── archive_server.py      # zip/7z/tar/gz compress+extract (v0.9.8+)
│   ├── exec_server.py         # sandboxed bash/awk/grep/python/shell (v0.9.9+)
│   ├── time_server.py         # get_current_time local + UTC (v0.9.11+)
│   ├── chat_history_server.py # list/search/get past chats (v0.9.21+)
│   ├── stock_server.py        # 兆豐 MegaAPI stock query+order,
│   │                          #   mock-first (v0.9.24+)
│   └── webhook_server.py      # webhook trigger management for
│   ├── image_gen_server.py  # OpenRouter Image API 產圖
│   │                           #   (v0.9.104+; 4 tools)
│                              #   external clients (v0.9.25+)
├── log_analyzer/              # v0.9.7+ log anomaly analysis package
│   ├── parser.py              # access log (combined) + syslog RFC3164/RFC5424
│   ├── anomalies.py           # rule engine + numpy semantic outliers
│   └── store.py               # LogStore → dedicated QDRANT_LOG_COLLECTION
├── archive_tools.py           # v0.9.8+ zip/7z/tar/gz logic (Zip-Slip + budgets)
├── exec_tools.py              # v0.9.9+ disaster blocklist + tool discovery
│                              #   + run_shell/run_bash/run_awk/exec_python/grep
├── mega_stock_tools.py        # v0.9.24+ 兆豐 MegaAPI stock engine (mock-first
│                              #   dual mode; v0.9.26.0+ live wiring via
│                              #   megaSpeedy — orders still 3-gate + cert)
├── agent/
│   ├── agent.py               # Tool-calling loop + RAG prompt + provider
│   │                          #   auto-switch + prompt trigger blocks
│   ├── session.py             # JSON session store in ~/.qdrant-rag-agent/sessions/
│   ├── scheduler.py           # cron / interval / one-shot daemon (v0.8.0+)
│   ├── webhook.py             # v0.9.25+ webhook triggers + HTTP listener
│   │                          #   (per-trigger URL + access key, mirrors
│   │                          #   scheduler.py persistence/lifecycle)
│   └── context_compress.py    # v0.9.10.0+ opt-in chat context compression
│                              #   (summarize / offload / selective squeeze)
├── mcp_client/                # v0.8.0+ tool-calling host + clients
│   ├── base.py                # McpClient ABC + McpTool + McpCallResult
│   ├── stdio.py               # StdioMcpClient (local subprocess)
│   ├── http_sse.py            # HttpSseMcpClient (streamable_http + legacy sse)
│   ├── host.py                # LocalTool + QdrantLocalHost + WebSearchLocalHost
│   │                          #   + Filesystem/Scheduler/Todo/OpenWith
│   │                          #   + LogAnalysis/Archive/Exec/DateTime
│   │                          #   + ChatHistory/MegaStock/Webhook/Meta
│   └── manager.py             # McpManager + ToolSpec + from_config()
├── imagegen_tools.py          # v0.9.104+ ImageGenEngine —
│                              #   OpenRouter Image API 產圖
│                              #   (mock-first; 4 tools; confirm-gate)
└── ui/
    └── md_render.py           # Zero-dep ANSI markdown renderer (full GFM, v0.8.3)
```

Each module keeps **two API surfaces** when ported from milvus:
the milvus-style class API (new) and the v0.1.x module-level functions
(old) — so existing callers keep working.

## 4. Code Discipline

- **Single responsibility**: each module does one thing.
- **POSIX paths**: all source paths stored in Qdrant payload use
  `path.resolve().as_posix()` to avoid Windows backslash escape issues
  in filter grammar.
- **Zero `print()` to stdout in MCP server**: stdout is reserved for
  JSON-RPC; debug prints must go to `sys.stderr`.
- **Graceful exception handling**: any place that hits the network
  (Qdrant, LLM API) must wrap the call so a transient failure doesn't
  tear down the whole REPL / directory ingest.
- **Streaming Markdown in chat**: `_bbs_post_md` renders ANSI-aware to
  keep BBS box edges aligned even with CJK content.
- **Backward compatibility**: new classes (`TextChunker`,
  `FileLoader`, `Embedder.embed_batch`) coexist with v0.1.x module-level
  functions (`chunk_text`, `load_file`, `Embedder.embed`). Don't break
  old callers unless you're also updating them.
- **Don't over-engineer**: only add features requested in the task.

## 5. Qdrant-Specific Gotchas

- **Loopback latency trap (v0.9.25.1)**: NEVER write
  `http://localhost:6333` in `.env`. `getaddrinfo('localhost')`
  resolves `::1` first and connecting to `::1` is refused only after
  ~2 s on this box; qdrant-client re-opens its HTTP connection per
  request, so EVERY search/stats call paid that delay (measured
  2073 ms → 29 ms after switching to `127.0.0.1`). Fix lives in
  `src/config.py::_normalize_loopback_url` (bare `localhost` is
  rewritten at config load; `[::1]` / LAN IPs / hostnames untouched)
  — but write the IPv4 literal anyway. Ops guide: `C:\qdrant\README.md`.
- **HNSW default params**: `m=16`, `ef_construct=100`, `ef=128` (search).
- **Payload indexes**: `filename`, `source`, `content_hash` so filter
  queries are O(log n).
- **Distance metric**: Cosine (matches OpenAI embedding convention).
- **Persistent storage**: `qdrant_storage:/qdrant/storage` in
  `docker-compose.yml`, or `C:\qdrant\storage\` for native binary.
- **qdrant-client 1.10+ breaking changes** (workarounds in
  `src/vector_store/qdrant_client.py`):
  - `client.search(...)` removed → use `client.query_points(query=...)`,
    return type is `QueryResponse` with `.points` list
  - `CollectionInfo.vectors_count` removed → use `.points_count`
  - Always check `hasattr(client, 'query_points')` if you want to
    keep backward compat with qdrant-client < 1.10
- **Collection accumulates test data**: Qdrant is a real server, so
  repeated `ingest` of the same file across runs will pile up if the
  ID scheme changes. Before any big test, do
  `qdrant-agent.bat delete-collection` to start clean.
  (v0.2.3 pipeline dedup-by-content-hash should prevent this in
  normal use, but cross-version ID schemes are not dedup-compatible.)
- **Image marker regexes**: `IMAGE_MARKER_RE` (DOCX `[Image: ...]`)
  vs `IMAGE_MARKER_RE_V2` (PPTX `> [image: ...]`) — pipeline uses the
  right one based on file extension.

## 7. Embedding Backend (v0.2.5+; explicit switching v0.2.6+)

`src/ingest/embedder.py` `Embedder.embed()` / `embed_batch()` use the
``EMBEDDING_BACKEND`` env var to choose which path runs (no silent
fallback chain — explicit choice prevents wrong vectors when a backend
is down). ``auto`` mode probes in this priority order:

1. **Local llama.cpp server** at `LLAMACPP_EMBED_URL` (default
   `http://localhost:8080/v1/embeddings`; normalised to the
   `127.0.0.1` literal at Embedder construction since v0.9.25.3 —
   same ::1 trap as QDRANT_URL). Probed via `_llamacpp_available()`
   hitting `/v1/models` (cached). Served by
   `C:\llama.cpp-vulkan\start-llamacpp.bat` using the **win-vulkan-x64**
   build (not the cpu build) with `Qwen3-Embedding-8B Q4_K_M GGUF`
   (4.4 GB). OpenAI-compatible `/v1/embeddings` endpoint. Native dim 4096.
2. **Ollama Cloud** (or local Ollama server) at `OLLAMA_BASE_URL`
   using `OLLAMA_EMBED_MODEL` (default `nomic-embed-text`, 768-dim;
   alternatives: `mxbai-embed-large` 1024-dim, `all-minilm` 384-dim,
   `embeddinggemma` 768-dim). Probed via `_ollama_available()` hitting
   `/api/tags`. Requires `OLLAMA_API_KEY` for ollama.com cloud.
3. **OpenAI** (`OPENAI_API_KEY` + `OPENAI_BASE_URL`): batched
   `client.embeddings.create(model="text-embedding-3-small", input=...)`.
   Default model via `OPENAI_EMBED_MODEL` env. Native dim 1536.
4. **Deterministic hash fallback** (NOT semantic — last resort).

All paths normalise via `_normalize()` (L2-norm + pad/truncate) so
Qdrant stays dimensionally consistent regardless of backend.

### Per-collection switching

The Qdrant collection is keyed by `QDRANT_COLLECTION`. Each collection
holds vectors of one dim, fixed at `recreate-collection` time. To run
**multiple backends in parallel**, use separate collections:

```bash
# Default: local llama.cpp Qwen3 4096-dim
set QDRANT_COLLECTION=file_docs_local
set EMBEDDING_BACKEND=local
qdrant-agent.bat recreate-collection
qdrant-agent.bat ingest .\documents --recursive

# Same files, Ollama nomic-embed-text 768-dim
set QDRANT_COLLECTION=file_docs_ollama
set EMBEDDING_BACKEND=ollama
set EMBEDDING_DIM=768
qdrant-agent.bat recreate-collection
qdrant-agent.bat ingest .\documents --recursive
```

`Embedder.backend_info()` returns active backend, native dim, and
per-backend reachability — call it from REPL to diagnose.

### Llama.cpp Windows specifics

- ggml-org/llama.cpp ships `win-cpu-x64` AND `win-vulkan-x64` zips
  (b10423+); **Windows Vulkan prebuilt exists** (contrary to old
  assumption). Default to Vulkan for any GPU box.
- GGUF source for Qwen3-Embedding: Ollama registry v2 manifest
  (`registry.ollama.ai/v2/library/qwen3-embedding/`) — the blob
  sha256:3fcd3feb…3120b85 is the Q4_K_M, 4,676,804,928 bytes.
  Hugging Face gated repos (bartowski/second-state) may 401; Ollama
  registry is open.
- `--pooling last --embd-normalize 2` is the correct combo for
  Qwen3-Embedding-8B (4096-dim output). `--pooling mean` returns 4096
  hidden state too but with different semantics; verify dim with
  `Invoke-WebRequest` probe before assuming.
- **v0.9.25.3 tuned flags (both start-llamacpp.bat)**: `--cache-ram 0`
  (disable the server prompt cache — unique-text embeddings never hit
  it, and the constant "making room for prompt cache entry" evictions
  churn VRAM; prime suspect of the 4.8 h `ggml_vulkan: device fault`
  crash on this box) + `-ngl 99` + `-fa on` + `-ub 1024` (the server
  clamps n_batch = n_ubatch for embeddings; ub 2048 measured no
  further gain) + `--ctx-size 4096` (1024/slot — 500-char CJK chunks
  ~750 tokens no longer truncated at 512). Live: single 153→128 ms,
  64-batch 49.4→43.6 s. CPU build measured 449.9 ms single / 64-batch
  >300 s timeout — the CPU bat is a FALLBACK only, and it must keep
  `--pooling last` to agree with the Vulkan server's vectors. Bench
  harness kept at `C:\llama.cpp\_bench.py` +
  `C:\llama.cpp\_run_bench.ps1`.
- AMD RDNA 1 (RX 5700 XT) lacks FP16 tensor hw accel — Vulkan build
  falls back to FP32 matmul which is no faster than AVX2-CPU on the
  i5-1240P. Don't expect GPU acceleration on RDNA 1; the win is
  still real (avoids 8B-on-CPU jank, ~227ms/item batched).
- Verify `--list-devices` shows `Vulkan0: AMD Radeon RX 5700 XT
  (8176 MiB)`. If `model loaded` log doesn't show GPU offload lines,
  pass `--device Vulkan0` explicitly.

## 8. Web Search Tool (v0.2.8+)

`src/tools/web_search.py` wraps Ollama's hosted search / fetch
REST APIs (POST `https://ollama.com/api/web_search` and
`/api/web_fetch`). Auth is the existing `OLLAMA_API_KEY`; no
extra setup is needed.

* `OllamaWebSearch().search(query, max_results=5)` -> list[`WebResult`]
* `OllamaWebSearch().fetch(url)` -> `FetchedPage | None`
* `OllamaWebSearch().is_configured()` -> bool (true iff key set)

The Agent (`src/agent/agent.py`) threads the results into the
prompt context block under `### Web Search Results` when
`use_web_search=True` is passed. CLI flags:

* `qdrant-agent.bat ask ... --web-search [--web-max-results N]`
* `qdrant-agent.bat chat --web-search` plus per-turn
  `/web on|off|status`

If `OLLAMA_API_KEY` is missing, the Agent skips the web call
and prints a one-line note in the prompt context -- never crashes.

## 9. Per-session Question Dedup (v0.2.9+)

`cli.py _handle_chat` keeps a per-session fingerprint cache:
before calling `agent.chat_turn()`, it walks
`session.messages` looking for a previous user message whose
normalised SHA-1 fingerprint matches the current question.
On a hit, the previous assistant answer is replayed verbatim
and a one-line system note is printed -- Qdrant and Web Search
are NOT re-queried. This is the cheapest way to avoid
re-embedding the same question against the same context.

Helpers:

* `_normalise_question(q)` -- strip trailing CJK / ASCII
  punctuation, lowercase, collapse whitespace
* `_question_fingerprint(q)` -- SHA-1 of normalised question,
  first 16 hex chars
* `_find_recent_assistant_reply(messages, q)` -- linear scan;
  returns ``(answer, sources)`` or ``None``

`cli.py ask` is *not* affected (each invocation is a fresh
process). The dedup is REPL-only.

## 10. MCP Tool Calling + MCP Host/Client (v0.8.0+)

The Agent owns an `McpManager` (see `src/mcp_client/manager.py`)
that exposes a single, normalised tool surface to the LLM's
chat/completions tool-calling loop. The manager is built from:

1. **Local hosts** (always-on, registered by default):
   * `QdrantLocalHost` — `search_documents`, `list_documents`,
     `get_document_stats`, `add_document` / `update_document` (v0.9.0+),
     `describe_image` / `resize_image` (v0.9.1/0.9.2+). Runs
     in-process against `QdrantStore` / `Retriever`; no MCP
     round-trip, no subprocess.
   * `WebSearchLocalHost` — `web_search`, `web_fetch`. Wraps
     `OllamaWebSearch`.
   * `FilesystemLocalHost` (v0.9.3+) — 15+ sandbox file tools
     (read/write/copy/diff/patch/tree, uploads/).
   * `SchedulerLocalHost` (v0.8.6+) / `TodoLocalHost` (v0.8.8+) —
     schedule + multi-level todo management.
   * `OpenWithLocalHost` (v0.9.6+) / `LogAnalysisLocalHost` (v0.9.7+)
     / `ArchiveLocalHost` (v0.9.8+) / `ExecLocalHost` (v0.9.9+) —
     artifact opener / log anomaly analysis / archive / sandboxed
     compute.
   * `DateTimeLocalHost` (v0.9.11+) — `get_current_time`. LOCAL
     time (ISO 8601 + UTC offset + timezone name) and UTC+0 side
     by side, plus epoch seconds and weekday; zero-argument,
     read-only.
   * `ChatHistoryLocalHost` (v0.9.21+) — `list_chat_sessions` /
     `search_chat_history` / `get_chat_session` (READ-ONLY over
     persisted sessions).
   * `MegaStockLocalHost` (v0.9.24+) — 兆豐 stock query + mock
     orders (mock-first; `stock_get_quote` / `stock_get_kline` /
     `stock_get_account_summary` / `stock_list_orders` /
     `stock_place_order` / `stock_cancel_order`).
   * `WebhookLocalHost` (v0.9.25+) — webhook trigger management:
     `add_webhook_trigger` (returns per-trigger URL + access key
     SHOWN ONLY ONCE) / `list_webhook_triggers` /
     `get_webhook_trigger` / `get_webhook_trigger_url(reveal)` /
     `delete` / `enable` / `disable` / `run_webhook_trigger_now` /
     `list_webhook_trigger_runs` / `get_webhook_trigger_errors`.
   * `MetaLocalHost` — `list_mcp_tools` catalog.

2. **Remote MCP servers** (opt-in, declared in `config/mcp.json`):
   * Stdio transport (spawn a child process; e.g.
     `python -m src.mcp_server.server`, github-mcp, etc.).
   * Streamable HTTP transport (modern MCP 2025-03-26+ spec;
     Notion, Linear, etc.).
   * Legacy HTTP+SSE transport (MCP 2024-11-05 spec; older
     servers still ship with this).

Failure semantics:

* A single MCP server failing to **connect** at startup is recorded
  in `McpManager.failed` and surfaced via `/tools status`. The rest
  of the tool surface remains usable.
* Tool errors come back as `McpCallResult(is_error=True, ...)` and
  the agent loop feeds the error string back to the model so it can
  pivot or retry.

CLI flags:

* `qdrant-agent.bat ask --mcp-config config/mcp.json "..."` — use
  this MCP config (default).
* `qdrant-agent.bat ask --mcp-config "" --no-tools "..."` — local
  hosts only, no tool calling (legacy behaviour).
* `qdrant-agent.bat chat --mcp-config ...` — same defaults; in-R
  use `/tools [on|off|status|list]` for per-turn toggling.

GUI bridge adds `list_mcp_tools()` to `gui/api.py` so the frontend
can introspect the active catalog (header chip + Settings tab).

**MCP 設備編輯器 (Admin tab, v0.8.0+)**

The Admin tab embeds a fleet editor backed by `config/mcp.json`
(atomic write via `tempfile.mkstemp` + `os.replace`). The user
adds / edits / deletes servers in-place and Save hot-reloads
`McpManager.replace_config(servers)` — the next chat turn sees
the new fleet without restarting the agent.

* API methods in `gui/api.py`: `get_mcp_config` /
  `save_mcp_config` / `restart_mcp_server` /
  `get_mcp_server_logs`. All catch exceptions and return
  `{"error": "..."}`.
* `_validate_server_entry` rejects malformed entries
  (missing command / url, unknown transport) before write —
  bad input never touches disk.
* `_read_mcp_config` strips `_*` / `_comment` placeholder
  entries so the editor doesn't render example placeholders.
* Three transport-specific field sets in the modal:
  stdio (command / env / cwd), streamable_http (url / headers),
  sse (url / headers). `${ENV_VAR}` substitution happens at
  connect time inside the clients, not in the editor.

`McpManager.replace_config()` closes remote clients only —
local hosts survive so the local Qdrant + Web Search tools
remain available throughout. `McpManager.close_all()` is the
full-wipe variant.

See `tests/test_mcp_client.py` (29 tests) + `tests/test_gui_api.py`
(11 new MCP-editor tests) for the public surface contract;
all dispatch paths are mock-covered, no real network needed in CI.

**Schedules (v0.8.0+)**

`src/agent/scheduler.py` provides cron / interval / one-shot
triggers. Each fire runs `Agent.chat_turn(prompt, history=...,
use_web_search=..., use_tools=True)` — **full tool surface** so
MCP servers and Web Search are available.

* `Schedule` dataclass + `ScheduleManager` (atomic JSON writes in
  `$QDRANT_RAG_HOME/schedules/<id>.json`).
* 5-field cron support (`*`, `*/n`, `a-b`, `a-b/n`, comma lists).
  No month/day names — keep it simple, no extra deps.
* 3 schedule types: `interval_seconds`, `cron`, `once_at`.
* `once_at` schedules auto-disable after firing.
* `interval_seconds` anchors on `last_fire_at` to avoid drift, but
  catches up if the scheduler was paused longer than one interval.
* `Scheduler` daemon thread ticks once a second; tick error /
  `on_fire` exception is caught + logged — daemon keeps running.
* `Agent.shutdown()` calls `Scheduler.stop(timeout=2.0)`.
* Each fire creates / reuses a dedicated session
  (`sched-<id>-<fire-ts>.json`) and appends a JSON line to
  `<id>.history.jsonl` (50-entry ring buffer).

**GUI bridge:** `list_schedules` / `add_schedule` / `update_schedule`
/ `delete_schedule` / `run_schedule_now` /
`get_schedule_history` / `start_scheduler` / `stop_scheduler` —
all catch exceptions and return `{"error": "..."}`.

**CLI:** `python cli.py schedules {list,show,add,remove,enable,
disable,run-now,history,daemon}`. `--type` accepts `interval` as
an alias for `interval_seconds`.

See `tests/test_scheduler.py` (37 tests) for the contract —
all paths mock-driven, no real network / clock needed.

**Webhook triggers (v0.9.25.0+)**

`src/agent/webhook.py` is the event-driven counterpart to the
scheduler: each trigger has its OWN URL (`/trigger/<id>`) and a
random `access_key` (`secrets.token_urlsafe(32)`); an HTTP call
fires the pre-configured prompt. Setup mirrors the schedule tools
exactly (add / list / get / delete / enable / disable / run-now /
runs / errors) — 10 tools on `WebhookLocalHost` (server
`webhook-local`).

* `WebhookServer` — stdlib `ThreadingHTTPServer` daemon thread on
  `QDRANT_WEBHOOK_HOST:QDRANT_WEBHOOK_PORT` (default
  `127.0.0.1:8335`) + `/healthz`. Bind failure logs a warning and
  never crashes the app; `Agent.shutdown()` stops the thread.
* Auth: `?key=` query param or `X-Access-Key` header,
  constant-time compared (`hmac.compare_digest`). Wrong key → 401,
  unknown id → 404, lifecycle-gated → 403. Non-loopback binds log
  a loud WARNING (each URL is a remote prompt-execution endpoint).
* `add_webhook_trigger` returns the URL + access key SHOWN ONLY
  ONCE; list/get mask it; `get_webhook_trigger_url(reveal=true)`
  fetches it back. Auto-starts the listener via
  `McpManager.set_webhook_auto_start_callback` (v0.8.6.3 pattern).
* Payload: POST body `{"payload": ...}` (or raw text) replaces the
  `{payload}` placeholder or is appended as a `[Webhook payload]`
  block. `?async=1` → 202 + background fire; default sync (the
  HTTP response carries the LLM answer).
* Lifecycle mirrors Schedule: `max_fires` (→ exhausted + auto-
  disable), `expires_at` (→ expired), `status="failed"` stamp when
  `chat_turn` raises. Persistence: `webhooks/<id>.json` (atomic) +
  `<id>.history.jsonl` (50-entry ring) + a dedicated session
  (`webhook-<id>-<fire-ts>.json`).
* Boot auto-resume: `gui/api.py::_resume_webhook_server` in
  `_prewarm_agent` re-binds when an enabled trigger exists
  (`QDRANT_WEBHOOK_AUTORESUME=off` opts out); keys stay valid
  across restarts.
* No GUI tab / no CLI subcommands by design — the LLM manages
  triggers via the MCP tools (system prompt trigger block
  included; legacy prompt excludes it).

See `tests/test_webhook_host.py` (38 tests — real HTTP round-trips
on an ephemeral loopback port with a stub fire engine) for the
contract.

## 6. Path & Windows Environment

- `execute_command` always uses absolute paths.
- Windows cmd does not support `tail`; use `> file 2>&1` redirection.
- Windows cmd has no `head`, no `printf`, no `curl` alias (PowerShell's
  `curl` is `Invoke-WebRequest` — use `curl.exe` explicitly for the
  real binary, and `-L --fail --show-error` flags for downloads).
- `Invoke-WebRequest` on this machine often fails to download large
  GitHub release zips (>100MB) due to transient TLS / proxy issues;
  fall back to user downloading in browser and dropping the file in
  place (e.g. `C:\qdrant\qdrant.exe`).
- `ebooklib==0.20` on this Windows env has a `write_epub` /
  `EpubWriter` bug (variable shadowing). We only use `read_epub()` +
  `get_items_of_type()` so the read path is fine; don't try to
  generate EPUB files via ebooklib in tests — use stdlib `zipfile`
  to hand-craft a minimal valid EPUB.
- Vulkan support: AMD RX 5700 XT (Navi 10 / RDNA) is Vulkan-capable,
  but needs LunarG Vulkan Runtime installed separately (not bundled
  with AMD driver). Qdrant v1.13 LTS native binary is CPU-only;
  v1.15+ binary adds GPU/Vulkan.
- Qdrant on Windows native is supported but Docker is recommended
  for Vulkan/SIMD acceleration.

**v0.9.26.0 megaSpeedy（兆豐 API）Windows 注意事項** —

- `megaSpeedy/` 資料夾（官方 SpeedyAPI_PY.7z 解壓的元件：DLL +
  wrapper py + `Temp/` 74 檔 + `speedyAPI_config.json`）安裝於
  **專案根**；`spdOrderAPI.py` 以 `os.path.dirname(__file__)` 載入
  DLL（跟著資料夾走，不需 cwd 對齊）。
- **SDK import 會 print 一串 banner 到 stdout**（'----- spdOrderAPI
  detect : Windows platform -----'、[UFCLib]、[pySpeedyAPI] 等）—
  `_import_live_sdk()` 以 `contextlib.redirect_stdout` 靜音；任何
  **新的** live import 路徑都必須經過該 helper，否則 stdio MCP 的
  JSON-RPC 通道會被污染（v0.9.16.3 教訓的 SDK 版）。
- 交易主機 `spapi.emega.com.tw:56789`、行情主機
  `119.31.179.86:34567`（預設值已寫死於模組常數，email 通知值）。
- 登入 `LogonProxy(身分證字號大寫, 電子登入密碼, 帳號)`；下單前
  `SetAccount('TWSE', 分公司, 帳號)` + `EnableMEGACA(.pfx, id, pwd)`
  （查詢不需要憑證、下單需要）。
- 32-bit Python 會載 `megaSpeedyAPI.dll`（非 _64）；本專案一律
  64-bit（C:\Python314）。
- 開通前主機對 live 連線回「未授權使用」（UTF-8 bytes 被 SDK 以
  big5 解碼 → UnicodeDecodeError）— 已由 `_live_call` 捕捉為乾淨
  錯誤；開通後重試即可。

**v0.9.5 GUI independent Drawio viewer window** —
closes the "LLM can write .drawio via filesystem MCP but I can't
see it" loop. New `gui/diagrams_window.py`
(`DiagramsWindowManager` + `_DiagramsViewerApi`) spawns one
pywebview window per diagram, fully decoupled from the main
GUI (can open N simultaneously, each closes independently).
Three open routes: sandbox path, inline chat XML, drag-drop from
filesystem. Edit + Save back to sandbox (atomic tempfile +
os.replace, same shape as `_write_content_to_sandbox`).
Per-window `_DiagramsViewerApi` is a tiny class exposing only
`save_to_sandbox` / `read_from_sandbox` / `get_write_root` /
`ping`; the heavy `Api` methods stay on the main window. New
HTML/JS/CSS assets under `gui/assets/diagrams.{html,js,css}` —
drawio embed loaded via official CDN with `<script onerror>`
fallback flag for offline detection. JS-side `_extractDiagramXml`
regex picks `<mxfile>` blocks out of chat messages (fenced
`xml` blocks + bare); per-message "View in Diagrams" button is
appended in `renderChat()`. New `gui/api.py` methods:
`open_diagrams_window`, `open_diagrams_with_xml`,
`open_diagrams_with_path`, `diagrams_window_status`. New i18n
keys `diagramsViewer` + `openInDiagrams` (zh-TW / zh-CN / en /
ja). 4 MB XML size cap (mirrors filesystem MCP read cap).
`gui/app.py::_on_closed()` calls
`api._get_diagrams_manager().close_all()` to tear down every
viewer when the main GUI closes (otherwise pywebview leaves
zombie processes behind). 60 new tests in
`tests/test_diagrams_window.py` (mock-based, no real network),
including a Node VM driver that runs the JS extraction regex
against real LLM output patterns. Full mcp-related regression:
`309 passed, 1 skipped` in 27.30s. `pyproject.toml` 0.9.4 →
0.9.5. v0.9.5 is purely GUI surface (no LLM dispatch, agent,
session, scheduler, or vector-store changes), so no bump of
the workspace version (still v0.8.9 baseline).

**v0.9.5.1 hotfix: drawio viewer doesn't fire on
`create_diagram` tool output** — user-reported 2026-09-01
22:27: chat reply was `tool create_diagram(['xml']) -> OK:
'{"xml":"<mxGraphModel>..."}'` but no "View in Diagrams"
button appeared. Root cause: v0.9.5's `_extractDiagramXml`
regex only matched `<mxfile>...</mxfile>` blocks; our own
`create_diagram` MCP returns `<mxGraphModel>` wrapped in a
`{"xml": "..."}` JSON envelope. Fix: regex gains 3 fallback
patterns (fenced/bare `<mxGraphModel>`, JSON `"xml": "<...>"`
un-wrapping, bare `<mxCell>`); the viewer adds
`_wrapForDrawio()` that auto-envelopes non-mxfile input into
`<mxfile><diagram><mxGraphModel>...</mxGraphModel></diagram>
</mxfile>` before handing to drawio embed. 12 new tests in
`tests/test_diagrams_window.py` (5 extract regressions
including the exact user-log JSON shape + 7 wrap scenarios);
72 passed total. `pyproject.toml` 0.9.5 → 0.9.5.1.

**v0.9.5.2 hotfix: drawio viewer still doesn't fire —
LLM describes the diagram in prose instead of echoing XML** —
user-reported 2026-09-01 22:43+: the user asked the LLM to draw
a "hello" block via drawio and show it. The server log shows
``tool create_diagram(['xml']) -> OK: '{"xml":
"<mxGraphModel>..."}'`` — the tool DID fire — but the assistant's
final reply was pure prose describing the chart properties
('形狀:圓角矩形 (240 × 120) 文字: hello (28px 粗體) 配色:淡藍色
背景 (#dae8fc) + 藍色邊框 (#6c8ebf)') with zero embedded XML.
The chat UI regex found nothing to attach a "View in Diagrams"
button to. Two secondary bugs surfaced in the same investigation:
(1) some LLMs echo the tool result with JSON unicode-escaped
angle brackets (``\u003cmxGraphModel\u003e``) — the regex never
saw a literal ``<`` so it missed; (2) a fenced ````json
{"xml":"<mxGraphModel>..."}``` message was caught by the
fenced-mxfile pattern but ``m[1]`` carried the entire wrapper
including ``{"xml": "..."`` — the viewer received a malformed
XML and drawio embed refused to render.

Three-layer fix:

1. **`Agent._system_prompt(tools_enabled=True)` gains a
   "Drawio / diagrams viewer (v0.9.5+)" trigger block** —
   teaches the LLM that ``create_diagram`` results must be
   echoed into the FINAL message (in either a JSON envelope
   ``{"xml": "<mxGraphModel>..."}`` or a fenced XML block
   ``<mxGraphModel>...</mxGraphModel>``), and explicitly
   forbids the "describe in prose, skip the XML" failure mode
   that produced the bug report. Mirrors the v0.8.6.2 Telegram
   trigger block pattern exactly. 8 new tests in
   `tests/test_system_prompt_diagrams.py` (zh-TW / en keywords,
   pure-prose prohibition, `tools_enabled=False` exclusion,
   coexistence with response_language + telegram + schedule
   blocks, tool-failure surfacing).

2. **`_extractDiagramXml` pre-normalises JSON unicode
   escapes** — before the regex array runs, the content is
   run through `.replace(/\\u003c/gi, "<")`,
   `.replace(/\\u003e/gi, ">")`,
   `.replace(/\\u0026/gi, "&")`. The regex patterns stay
   simple (only literal `<...>`) but now handle the JSON
   unicode-escape variant LLMs occasionally emit verbatim.

3. **Fenced pattern requires a language hint** — fenced
   mxfile / mxGraphModel patterns tighten from
   `/```(?:xml|drawio)?\s*/` to `/```(?:xml|drawio)\s*/`.
   A ```json fence no longer triggers the fenced-XML
   pattern; the JSON-envelope pattern below it is the one
   that unwraps only the inner XML.

3 new tests in `tests/test_diagrams_window.py` (`TestJsExtractDiagramXml`):
* `test_pure_prose_chinese_no_xml` — exact user-reported
  scenario as a regression guard (regex MUST return null,
  no false-positive button).
* `test_json_unicode_escaped_xml` — `\u003cmxGraphModel\u003e`
  normalised to `<mxGraphModel>` before pattern match.
* `test_fenced_json_does_not_match_fenced_xml` — fenced JSON
  envelope unwrapped to clean `<mxGraphModel>` (no fence, no
  JSON wrapper leaked into drawio).

`pyproject.toml` 0.9.5.1 → **0.9.5.2**. `tests/test_diagrams_window.py`
72 → 75 passed; combined 6-file run 151 passed, 1 skipped.
Workspace version still v0.8.9 baseline (purely GUI + system-
prompt changes).


**v0.9.5.3 hotfix: drawio viewer opens empty — viewer
gets XML, button shows up, but the embed iframe stays
blank** — user-reported 2026-09-01 ~22:43+: v0.9.5.2's
XML-echo prompt worked (the LLM now echoes the
``<mxGraphModel>...`` block in its final reply, and the
``View in Diagrams`` button appears as expected), but
clicking it opens a Drawio Viewer window with a fully-
blank editor area. Server log shows
``open_diagrams_with_xml: xml_len=369`` (Python side
received and dispatched the XML correctly), but the
drawio embed inside the new window never paints a
shape.

**Root cause** — ``gui/assets/diagrams.js`` was using
the ``DrawioViewer`` high-level wrapper exposed by
``embed/v1.js`` (the draw.io embed convenience class).
``postToDrawio()`` had guessed that the wrapper exposes
the iframe as ``iframe.iframe.contentWindow`` and called
``postMessage`` there. The guess was wrong: the wrapper
internals are undocumented, and postMessage on that path
was silently dropped (no exception, no log). Result:
every load attempt disappeared into the void, the embed
never received the XML, and the editor pane sat at its
initial loading spinner / blank state with no visible
error to the user.

The ``onDrawioMessage`` handler was waiting for an
``init`` event from the iframe to flip ``drawioReady``,
but that init never arrived either (postMessage was
never reaching the iframe in the first place).

**Fix — drop the wrapper, mount the raw iframe + talk
the documented JSON protocol directly** (the same
pattern used by the official
``jgraph/drawio-integration`` ``diagram-editor.js``):

1. ``gui/assets/diagrams.html`` — removed the
   ``<script src="https://embed.diagrams.net/embed/v1.js">``
   tag (no longer needed; v0.9.5.3 builds its own
   iframe). Replaced the comment block with a note
   pointing at the new hand-rolled protocol.

2. ``gui/assets/diagrams.js`` — ``startDrawio()`` no
   longer instantiates ``new DrawioViewer(...)``. It
   instead does
   ``iframe = document.createElement('iframe');
    iframe.setAttribute('src', 'https://embed.diagrams.net/?embed=1&proto=json&spin=1&ui=min');
    mount.appendChild(iframe);`` — same params the
   official integration uses, fully documented.

3. ``onDrawioMessage()`` gains a new ``case 'init':``
   that flips ``drawioInitialized = true`` and drains
   any ``pendingLoadXml`` the Python side pushed before
   the iframe finished booting. ``loadXmlIntoEditor()``
   queues into ``pendingLoadXml`` if init hasn't fired
   yet — so XML pushed by Python during the iframe's
   initial load doesn't disappear.



**4. ``postToDrawio()`` simplified to a one-liner:
   ``iframe.contentWindow.postMessage(JSON.stringify(payload), '*')``
   — the wrapper's ``.iframe`` indirection is gone, so
   the message lands on the iframe every time.

5. ``loadXmlIntoEditor()`` now uses the JSON-protocol
   ``{action: 'load', xml: ...}`` shape (legacy
   ``{event: 'load', ...}`` removed in drawio embed
   long ago). Buttons / exports / print / merge updated
   to ``{action: ..., ...}``.

6. Defensive ``evt.source !== iframe.contentWindow``
   filter on incoming messages — drop any window.postMessage
   from a frame that isn't ours (e.g. test harness or
   browser extension).

7. ``_wrapForDrawio`` widened to also accept a
   solitary self-closing ``<mxCell .../>`` (trigger
   regex relaxed from ``/mxCell...\/mxCell/`` to
   ``/<mxCell\b/``). The original required a paired
   closer; the LLM occasionally emits leaf-shape
   snippets without a body, which used to fall through
   to the verbatim-passthrough branch and confuse the
   embed.

8. Legacy ``MutationObserver`` watching the status pill
   for a "ready" text change (a brittle queue-drain
   hack from v0.9.5) is gone. The new ``init``
   event-driven path is deterministic.

**Tests** — 4 new in
``tests/test_diagrams_window.py::TestJsDrawioEmbedProtocol``:

* ``test_iframe_mounted_with_embed_url`` — the JS
  creates an ``<iframe>`` whose src contains
  ``embed=1``, ``proto=json``, ``spin=1``; no
  postMessages fire until init arrives.
* ``test_load_queued_until_init`` — Python pushes XML
  BEFORE the iframe's init event; we postMessage zero
  times; after init fires, exactly one ``{action:
  'load', xml: ...}`` lands on the iframe with the
  wrapped envelope.
* ``test_load_immediate_after_init`` — init first, then
  load_xml_event; ``load`` postMessage fires immediately
  with the wrapped XML.
* ``test_load_dropped_if_init_never_arrives`` —
  load_xml_event without any init; queue holds the XML
  and zero postMessages leak.

The JSDOM driver fixture writes its inline script to a
tmp file under the project root and runs it via
``subprocess.run(["node", path], cwd=project_root)`` —
Windows cmd mangles ``node -e "..."`` quoting for the
embedded JSON, and a bare tmp dir would fail to resolve
``require('jsdom')`` (needs ``node_modules/`` on the
require path). Driver also patches
``document.createElement('iframe')`` to expose a fake
``contentWindow`` that records every postMessage so
tests can assert on the exact JSON we send into the
drawio iframe.

`pyproject.toml` 0.9.5.2 → **0.9.5.3**.
``tests/test_diagrams_window.py`` 75 → 79 passed
(+4 new); combined 6-file run 151 passed → 174 passed
in 21.39s. Workspace version still v0.8.9 baseline —
this is purely a JS-protocol fix in the GUI viewer
asset, plus the system-prompt block carried over from
v0.9.5.2 still working. No changes to agent / LLM
dispatch / vector store / scheduler / sessions.
4. ``postToDrawio()`` simplified to a one-liner:
   ``iframe.contentWindow.postMessage(JSON.stringify(payload), '*')``
   — the wrapper's ``.iframe`` indirection is gone, so
   the message lands on the iframe every time.

5. ``loadXmlIntoEditor()`` now uses the JSON-protocol
   ``{action: 'load', xml: ...}`` shape (legacy
   ``{event: 'load', ...}`` removed in drawio embed
   long ago). Buttons / exports / print / merge updated
   to ``{action: ..., ...}``.

6. Defensive ``evt.source !== iframe.contentWindow``
   filter on incoming messages — drop any window.postMessage
   from a frame that isn't ours (e.g. test harness or
   browser extension).

7. ``_wrapForDrawio`` widened to also accept a
   solitary self-closing ``<mxCell .../>`` (trigger
   regex relaxed from ``/mxCell...\/mxCell/`` to
   ``/<mxCell\b/``). The original required a paired
   closer; the LLM occasionally emits leaf-shape
   snippets without a body, which used to fall through
   to the verbatim-passthrough branch and confuse the
   embed.

8. Legacy ``MutationObserver`` watching the status pill
   for a "ready" text change (a brittle queue-drain
   hack from v0.9.5) is gone. The new ``init``
   event-driven path is deterministic.

**Tests** — 4 new in
``tests/test_diagrams_window.py::TestJsDrawioEmbedProtocol``:

* ``test_iframe_mounted_with_embed_url`` — the JS
  creates an ``<iframe>`` whose src contains
  ``embed=1``, ``proto=json``, ``spin=1``; no
  postMessages fire until init arrives.
* ``test_load_queued_until_init`` — Python pushes XML
  BEFORE the iframe's init event; we postMessage zero
  times; after init fires, exactly one ``{action:
  'load', xml: ...}`` lands on the iframe with the
  wrapped envelope.
* ``test_load_immediate_after_init`` — init first, then
  load_xml_event; ``load`` postMessage fires immediately
  with the wrapped XML.
* ``test_load_dropped_if_init_never_arrives`` —
  load_xml_event without any init; queue holds the XML
  and zero postMessages leak.

The JSDOM driver fixture writes its inline script to a
tmp file under the project root and runs it via
``subprocess.run(["node", path], cwd=project_root)`` —
Windows cmd mangles ``node -e "..."`` quoting for the
embedded JSON, and a bare tmp dir would fail to resolve
``require('jsdom')`` (needs ``node_modules/`` on the
require path). Driver also patches
``document.createElement('iframe')`` to expose a fake
``contentWindow`` that records every postMessage so
tests can assert on the exact JSON we send into the
drawio iframe.

`pyproject.toml` 0.9.5.2 → **0.9.5.3**.
``tests/test_diagrams_window.py`` 75 → 79 passed
(+4 new); combined 6-file run 151 passed → 174 passed
in 21.39s. Workspace version still v0.8.9 baseline —
this is purely a JS-protocol fix in the GUI viewer
asset, plus the system-prompt block carried over from
v0.9.5.2 still working. No changes to agent / LLM

**v0.9.5.4 hotfix: drawio viewer opens but editor area
stays blank with no progress feedback — add loading
overlay + system browser escape hatch** — user-reported
2026-09-02 ~03:57 (log: `open_diagrams_with_xml: xml_len=369`):
v0.9.5.3's JS-protocol fix landed — `init` event arrives,
`load` postMessage is sent — but the iframe's editor
area stays white for **30 seconds to 5 minutes** before
drawio's ~9.6 MB `app.min.js` bundle finishes booting in
WebView2. User sees only drawio's tiny "Loading…" spinner
on white background and assumes the viewer is broken again.

**Root cause**: drawio's embed isn't a widget — it loads
the **entire ~9.6 MB minified editor bundle**
(`https://embed.diagrams.net/js/app.min.js` = 9,616,453
bytes) through the iframe. WebView2 (pywebview's
Chromium-based backend on Windows) is single-process, no
GPU accel, no warm cache — it can take 30 s to several
minutes to parse + compile + init that bundle. drawio
itself shows a tiny spinner on white background during
this, which is indistinguishable from "broken" to a
user who clicked the 📊 button expecting instant render.

**Fix**: two pieces — visible progress + escape hatch.

1. **`<div id="loading-overlay">` semi-transparent overlay**,
   shown from the moment the iframe is mounted. Contains:
   - Pure CSS spinner (`@keyframes loading-spin`, no deps)
   - 「⏳ 載入 drawio 編輯器…」 title + explanation
   - **Live elapsed timer** (`setInterval` every 500 ms
     updates "已等候 N 秒…")
   - At 15 s threshold, switches to 「⚠️ drawio 載入緩慢
     (已等候 15 秒)」 and reveals stuck-actions.
2. **Two escape-hatch buttons** (visible after 15 s):
   - **🌐 在瀏覽器中開啟 drawio** — calls Python
     `_DiagramsViewerApi.open_drawio_in_browser()` →
     stdlib `webbrowser.open()` on the same embed URL.
     System browser (Edge / Chrome / Firefox) loads it
     in 2-5 s because it has JIT + GPU + warm caches.
   - **🔄 重新載入 iframe** — tear down + remount iframe
     (`startDrawio()` again). Sometimes a reload hits a
     different CDN cache node and finishes faster.

**Single source of truth**: `_DRAWIO_EMBED_URL` Python
constant is the canonical URL. `_DiagramsViewerApi.get_drawio_url()`
serves it to JS. `diagrams.js` calls the bridge inside
`startDrawio()` (with the previous hardcoded URL as a
fallback for the edge case where the bridge isn't ready
yet). Future URL changes edit `_DRAWIO_EMBED_URL` once.

**Test infra fix (drive-by)**: switched JSDOM driver from
`process.stdout.write` to `console.log` + explicit
`setTimeout(() => process.exit(0), 50)` because Node 24 +
JSDOM 29 crashes with `STATUS_STACK_OVERFLOW` (`0xC00000FD`)
during teardown — even after `console.log` has flushed
stdout. `_jsdom_run_drive` now treats that exit code as
benign; other non-zero codes still raise. Also added
`get_drawio_url` / `open_drawio_in_browser` to `_DRIVER_PREFIX`
pywebview bridge stub so the JSDOM harness can run the
viewer JS without hitting "bridge method undefined".

**Tests** (11 new + 1 driver tick fix):

* `TestApiBinding::test_get_drawio_url_returns_constant`
  — `_DiagramsViewerApi.get_drawio_url()` returns the
  `_DRAWIO_EMBED_URL` constant.
* `test_open_drawio_in_browser_invokes_webbrowser` —
  monkeypatches `webbrowser.open` and confirms the button
  click reaches stdlib.
* `test_open_drawio_in_browser_exception_returns_error` —
  if `webbrowser.open` raises (no DISPLAY / no registered
  browser), returns `{"error": "..."}` rather than
  propagating through the pywebview bridge.
* `TestJsLoadingOverlay::test_overlay_visible_on_mount` —
  overlay visible, stuck-actions hidden, timer text reads
  "已等候 N 秒…" right after mount.
* `test_overlay_hidden_after_init` — overlay disappears
  once drawio sends `{event:'init'}`.
* `test_open_in_browser_click_doesnt_hide_overlay` —
  clicking the button doesn't hide the overlay (iframe
  still potentially booting).
* `test_reload_button_tears_down_iframe` — clicking
  reload tears down + remounts the iframe; overlay
  re-appears.
* Protocol test driver also got a
  `await new Promise(r => setTimeout(r, 50))` tick before
  the `run()` loop starts — otherwise the synchronous
  DOMContentLoaded handler hasn't finished mounting the
  iframe by the time `init_event` is dispatched, so
  messages arrive at a window with no iframe yet.

**Results**: 6-file run **181 passed** in ~22 s, 0 failed
(v0.9.5.3 baseline 174 → **+7 new** = 3 TestApiBinding +
4 TestJsLoadingOverlay):
* `test_diagrams_window.py` 86 passed (v0.9.5.3 baseline 79 → **+7 new**)
* `test_system_prompt_diagrams.py` 8 passed
* `test_system_prompt_telegram.py` 8 passed
* `test_md_render.py` 27 passed
* `test_chat_autosave.py` 18 passed
* `test_local_tool_argument_coercion.py` 34 passed

`pyproject.toml` 0.9.5.3 → **0.9.5.4**. Workspace version
still v0.8.9 baseline — purely GUI / JS / test-infra
changes. No changes to agent / LLM dispatch / vector
store / scheduler / sessions.

**v0.9.5.5 hotfix: viewer stuck on misleading "⏳ connecting…" pill +
silent white-out when diagrams.js dies — HTML defensive fallbacks +
JS global error/watchdog + deterministic JSDOM tests** — user-reported
2026-09-02: the viewer window opened with the status pill frozen on the
misleading "⏳ connecting…" default text, and if `diagrams.js` itself
failed to load or threw mid-execution the user saw the default pill +
a blank pane with ZERO error surfacing (the viewer window has no
devtools). Three-layer fix:

1. **HTML defensive fallbacks (meaningful without JS)** —
   `gui/assets/diagrams.html`: the `#loading-overlay` div drops its
   `hidden` attribute (default-visible spinner + 「⏳ 載入 drawio 編輯器…」
   even if `diagrams.js` never executes); the static pill text is
   neutralised to 「📊 Drawio Viewer」 (no more fake "connecting…"); the
   `<a id="link-open-in-browser" target="_blank">` anchor moves OUTSIDE
   the hidden `#loading-stuck-actions` div so pure HTML can open drawio
   in the system browser without any JS; a `<noscript>` block covers
   fully-disabled-JS environments.

2. **JS global error watchdog** — `gui/assets/diagrams.js` registers
   `window.addEventListener("error")` + `("unhandledrejection")` at the
   VERY TOP of the IIFE (before anything else can throw), funnelling
   through a `_reportError(prefix, e)` helper that writes
   `❌ <prefix>: <msg>` onto the pill. The synthetic-Event case has no
   `ev.reason`, so the rejection handler falls back to
   `window._lastRejectionReason` (stashed by the test driver). A
   `_WATCHDOG_MS = 2000` `setTimeout` at the end of `startDrawio()`
   flips the pill to 「⏳ drawio 回應緩慢 (等候 init 中…)」 when drawio's
   init event never arrives; `case 'init'` clears the timer (so a late
   watchdog can't overwrite the ready state) and the 「重新載入 iframe」
   remount resets it.

3. **Test-infrastructure determinism (kills cross-file flakiness)** —
   the JSDOM driver's `resources: 'usable'` made JSDOM fetch the REAL
   `https://embed.diagrams.net` URL over the network; whenever that
   HTTP round-trip completed it fired the iframe `load` handler which
   OVERWROTE the synthetic rejection's pill text —
   `test_unhandled_rejection_populates_status_pill` passed solo but
   flipped to `⏳ iframe loaded…` in combined runs depending on network
   timing. jsdom 29 also removed the `ResourceLoader` subclass API, so
   fetch-stubbing that way is impossible. Fix: drop the `resources`
   option entirely — the iframe never navigates, `load` never fires,
   and pill state is driven ONLY by each test's synthetic events.
   Also: jsdom-probe timeout 10→30 s and driver timeout 20→45 s (cold
   `require('jsdom')` + AV scan once exceeded 10 s on a loaded box),
   and 4 dead duplicate method copies inside `TestJsDefensiveFallback`
   (anchor + noscript ×3 each, one referencing an undefined `divs`
   name — shadowed by later definitions so never executed, but a
   landmine) are deleted.

**Tests** — 8 new in
`tests/test_diagrams_window.py::TestJsDefensiveFallback`: HTML overlay
default-visible, HTML pill neutral, first `setStatus` runs, 2-s watchdog
pill text, window-error pill (kind=err), unhandled-rejection pill,
anchor-outside-hidden-div, `<noscript>` present. The overlay driver
template snapshot now also returns `status_pill_text` /
`status_pill_kind`, plus `dispatch_error` / `dispatch_rejection` ops
that dispatch synthetic `ErrorEvent('error')` /
`Event('unhandledrejection')` events.

**Results**: 6-file run **189 passed, 0 failed** in 33.7 s (v0.9.5.4
baseline 181 → +8 new, 0 regression): `test_diagrams_window.py` 94
passed (86 → +8), `test_system_prompt_diagrams.py` 8,
`test_system_prompt_telegram.py` 8, `test_md_render.py` 27,
`test_chat_autosave.py` 18, `test_local_tool_argument_coercion.py` 34.
`pyproject.toml` 0.9.5.4 → **0.9.5.5**. Workspace version still v0.8.9
baseline — purely GUI / JS / test-infra changes. No changes to agent /
LLM dispatch / vector store / scheduler / sessions.

**v0.9.5.6 hotfix: viewer boot crashes with
"❌ JS error: Cannot read properties of undefined (reading 'api')"
— pywebview injects the bridge AFTER DOMContentLoaded** —
user-reported 2026-09-02: opening the Drawio Viewer immediately
showed the error pill (v0.9.5.5's new global error handler worked
as designed — it caught a REAL bug) and the editor area stayed
empty.

**Root cause**: the v0.9.5.5 boot block read
`window.pywebview.api.get_write_root()` directly inside the
`DOMContentLoaded` handler. pywebview injects `window.pywebview`
only AFTER the page finishes loading (official `pywebviewready`
event); `DOMContentLoaded` fires BEFORE that, so `window.pywebview`
was `undefined` → TypeError on the `.api` read. Worse, the
synchronous throw aborted the rest of the handler — `startDrawio()`
never ran and the iframe never mounted. The pre-0.9.5.6 JSDOM tests
never caught this because the test driver defined the bridge stub
BEFORE `window.eval(diagrams.js)` — the opposite of the real
WebView2 lifecycle.

**Fix** (`gui/assets/diagrams.js`):

1. `bridgeApi()` — null-safe accessor
   (`(window.pywebview && window.pywebview.api) || null`),
   mirroring `gui/assets/app.js::api.call()`'s v0.8.7 guard for
   the exact same lifecycle
   (`tests/test_gui_silent_api_not_ready.py`).
2. `_waitForBridge(timeoutMs)` — polls every 50 ms (default cap
   15 s; tests override via `window._BRIDGE_WAIT_MS_OVERRIDE`)
   and also resolves early on the official `pywebviewready`
   event. The boot block's `get_write_root()` call moved inside
   `_waitForBridge().then(...)`. A never-injected bridge now
   surfaces the non-fatal 「⚠️ Python bridge 未就緒」 pill note
   instead of a crash; the viewer keeps working read-only via
   the fallback embed URL.
3. `startDrawio()` runs unconditionally BEFORE bridge readiness
   matters (its `get_drawio_url` probe was already guarded with a
   hardcoded fallback); `probeBridge()` moved inside the
   `_waitForBridge` chain and gained its own guard so a stray
   early call sets a pill note instead of throwing into the
   global error handler.
4. Stale comment corrected: "startDrawio runs on DOMContentLoaded
   which is after the bridge is up" (v0.9.5.4) was backwards.

**Test infra (driver `bridge_mode` parameterisation)** —
`tests/test_diagrams_window.py`: `_DRIVER_PREFIX` refactored into
`_driver_head_js(bridge_mode="stub"|"none"|"late", bridge_delay_ms,
bridge_timeout_override_ms)` plus shared `_run_node_jsdom_script()`
/ `_require_jsdom_or_skip()` helpers; `_build_jsdom_driver_script`
and the overlay fixture forward the mode. "stub" (default)
preserves legacy behaviour for every pre-existing test; "none"
never injects the bridge (`_BRIDGE_WAIT_MS_OVERRIDE` shortens the
15 s wait); "late" injects it `bridge_delay_ms` after boot,
mirroring real WebView2 timing. Overlay snapshot gains
`sandbox_path_text`.

**Tests** — 5 new in
`tests/test_diagrams_window.py::TestJsBridgeBootSafety`:
* `test_boot_block_has_no_direct_bridge_api_read` — the exact
  production crash shapes (`window.pywebview.api.get_write_root`
  / `.ping`) are gone from the source.
* `test_bridge_wait_helpers_exist` — `bridgeApi()` +
  `_waitForBridge(` + `pywebviewready` wiring +
  `_waitForBridge().then(` in the boot block.
* `test_probe_bridge_uses_guarded_api_read` — probeBridge body
  uses `bridgeApi()` / `if (!api)` / `await api.ping()`.
* `test_boot_without_bridge_does_not_crash` (jsdom, none mode) —
  no "JS error" pill, iframe still mounts with the fallback URL,
  pill ends on the non-fatal 「未就緒」 note (kind=err).
* `test_late_bridge_injection_recovers` (jsdom, late mode, 150 ms
  injection) — `sandbox_path_text == '/tmp/sandbox'`, pill
  "✅ bridge OK" (kind=ok).

**Results**: `test_diagrams_window.py` **99 passed** (94 → +5),
0 failed in ~26 s. `pyproject.toml` 0.9.5.5 → **0.9.5.6**.
Workspace version still v0.8.9 baseline — purely JS boot + test
infra changes. No changes to agent / LLM dispatch / vector
store / scheduler / sessions.

**v0.9.5.7 hotfix: Drawio Viewer shows the 「错误 非绘图文件 /
Not a diagram file」 dialog for bare <mxGraphModel> —
_wrapForDrawio emitted a non-existent <mxDiagram> element** —
user-reported 2026-09-02: the LLM's first reply carried a bare,
attribute-less `<mxGraphModel>`; clicking the per-message
「📊 在圖表中開啟」 button opened the viewer but drawio popped
its error dialog with a raw-content preview. The LLM retried
with a hand-rolled `<mxfile><diagram>` wrapper and the SAME
diagram rendered fine — proving the extraction / bridge /
protocol chain was fine and only the envelope differed.

**Root cause** — `gui/assets/diagrams.js::_wrapForDrawio` wrapped
a bare `<mxGraphModel>` into
`<mxfile><mxDiagram id="root" name="Page-1">…</mxDiagram></mxfile>`.
`<mxDiagram>` does not exist in the mxfile format — the canonical
child element is `<diagram>` (mxDiagram is the mxGraph JS class
name, not an XML element). drawio's `extractGraphModel()` looks
pages up via `getElementsByTagName('diagram')`, found zero,
and rejected the whole file. Same typo in THREE places:

1. `_wrapForDrawio` mxGraphModel branch (`<mxDiagram>`/`</mxDiagram>`).
2. `_wrapForDrawio` bare-`<mxCell>` branch (same typo).
3. `emptyDiagramXml()` (merge-action fallback) used lowercase
   `<mxdiagram>` — equally unknown to drawio.

Worse, the tests had LOCKED THE BUG IN: 2 assertions in
`TestJsWrapForDrawio` + 1 in `TestJsDrawioEmbedProtocol` all
asserted `"<mxDiagram" in ...` — substring checks that verify
"an envelope exists" but never that the element name is legal.
The typo survived v0.9.5.2-v0.9.5.6 because earlier user reports
(blank pane, boot crash) never got far enough for drawio to
inspect the envelope.

**Fix** (`gui/assets/diagrams.js`) — all three envelope builders
now emit the canonical `<diagram id="root" name="Page-1">`,
matching the hand-rolled shape the user proved renders. Bare
`<mxGraphModel>` (the exact shape our `create_diagram` MCP
returns) now renders directly — no need to ask the LLM to
re-wrap into a full mxfile.

**Tests** (`tests/test_diagrams_window.py`):

* 3 locked-in assertions inverted: `"<diagram" in` +
  `assert "mxDiagram" not in` + `assert "mxdiagram" not in`
  (both casings guarded).
* `test_wrap_bare_mxgraphmodel_canonical_element` — the user's
  exact circle shape (160×160 ellipse, fillColor=#dae8fc) wraps
  into `<mxfile host="qdrant-rag-agent"...><diagram id="root"...>`
  with the cell surviving verbatim.
* `test_extract_then_wrap_user_message` — full-chain regression:
  the entire chat message (prose + bare mxGraphModel + trailing
  prose) through `_extractDiagramXml` → `_wrapForDrawio`; asserts
  no prose leaks into the extracted xml or the envelope.
* `test_no_wrong_diagram_element_anywhere_in_source` — whole-file
  guard: `mxDiagram` / `mxdiagram` may not appear anywhere in
  diagrams.js (covers emptyDiagramXml too).

**Results**: `test_diagrams_window.py` **102 passed** (99 → +3),
0 failed. `pyproject.toml` 0.9.5.6 → **0.9.5.7**. Workspace
version still v0.8.9 baseline — purely a viewer JS envelope fix
+ tests. No changes to agent / LLM dispatch / vector store /
scheduler / sessions.

**v0.9.5.8: remove the useless "📊 Diagrams Viewer" toolbar
button from the main GUI** — user request 2026-09-02: the
chat-toolbar button opened a BLANK viewer window with no way to
receive the XML from any chat message, so it had no use.

**Removed**:

1. `gui/assets/index.html` — the `chat-diagrams-btn` button
   (`data-i18n="diagramsViewer"`) is gone from the chat toolbar.
2. `gui/assets/app.js::bindEvents()` — the click handler (which
   called `open_diagrams_window` with no XML) is gone; a comment
   documents why.
3. `gui/assets/app.js` — the now-unused i18n key `diagramsViewer`
   removed from all 4 locales (zh-TW / zh-CN / en / ja);
   `openInDiagrams` kept (the per-message buttons still use it).
4. `gui/api.py::diagrams_window_status` docstring updated — the
   "used by the toolbar button label" claim was stale. The API
   methods `open_diagrams_window` / `diagrams_window_status` /
   `open_diagrams_with_xml` / `open_diagrams_with_path` are ALL
   KEPT (public API surface unchanged; the only remaining GUI
   entry points are the per-message "View in Diagrams" buttons).

**Tests** (`tests/test_diagrams_window.py::TestGuiAssetsWiring`):

* `test_index_html_has_diagrams_button` → renamed
  `test_index_html_toolbar_diagrams_button_removed` (inverted
  guard: `chat-diagrams-btn` / `diagramsViewer` must NOT exist
  in index.html).
* `test_app_js_handles_diagrams_button` → renamed
  `test_app_js_toolbar_handler_removed` (inverted guard: the
  handler and its `open_diagrams_window` call are gone from
  app.js; `_extractDiagramXml` / `mxfile` machinery stays).
* `test_app_js_renders_view_button_per_message` unchanged —
  locks the surviving per-message wiring
  (`open_diagrams_with_xml` / `btn-msg-diagrams`).

**Results**: `test_diagrams_window.py` **102 passed**, 0 failed
(102 → 102, 2 tests flipped to removal guards, 0 regression).
`pyproject.toml` 0.9.5.7 → **0.9.5.8**. Workspace version still
v0.8.9 baseline — pure GUI surface trim. No changes to agent /
LLM dispatch / vector store / scheduler / sessions.

**v0.9.6.0: MCP open-with feature — Explorer-style "open with the
associated application" for chat artifacts** — new local core
feature: the chat LLM can open what it produces (drawio XML,
markdown, csv, plain text, or files already saved in the sandbox)
with the right application, like Windows Explorer double-click.

**New tools** (`OpenWithLocalHost`, `src/mcp_client/host.py`,
auto-registered in `McpManager.from_config` + stdio server
`src/mcp_server/openwith_server.py`):

* `open_file(path)` — resolve against `QDRANT_FILESYSTEM_WRITE_ROOT`
  (same sandbox as `FilesystemLocalHost`), refuse dangerous
  executables, route the rest.
* `open_content(name?, content)` — write inline chat content into
  the sandbox (atomic tempfile + os.replace, 4 MB cap, drawio
  sniffing via `<mxfile>/<mxGraphModel>/<mxCell>` → force
  `.drawio` ext) and open it in one step.
* `list_openable_types()` — introspection (routed exts, OS
  association, refused exts, sandbox root, viewer wiring state).

**Routing** (`_route_open`): dangerous-ext refusal → drawio viewer
callback (`.drawio`/`.dio`, or drawio-shaped `.xml`) with
OS-association fallback on callback failure → OS file association
(`os.startfile` / `open` / `xdg-open`). The tool opens files with
OTHER applications — it must never become an LLM-driven launcher,
so executables are refused outright.

**GUI wiring** (mirrors the v0.8.6.3 scheduler auto-start pattern):
`McpManager.set_diagrams_opener(cb)` (+ `diagrams_opener_callback`
attr + `_openwith_local_host` reference kept by `from_config`);
`gui/api.py::_wire_openwith_diagrams_opener(agent)` is called
idempotently on every chat turn / ask and pushes
`_open_drawio_via_viewer` (→ `DiagramsWindowManager.open_with_path`)
down to `OpenWithLocalHost.attach_diagrams_opener`. CLI / stdio
have no callback → `.drawio` falls back to the OS association
gracefully.

**System prompt**: `Agent._system_prompt(tools_enabled=True)` gains
an "Open-with / artifact opener (v0.9.6.0+)" trigger block after
the drawio block — trigger phrases ('幫我開啟' / 'open the file' /
...), the write-then-open flow via `open_content`, and the
sandbox/executable security note.

**Tests** — 43 new:
* `tests/test_open_with_host.py` (34): tool surface, `open_file`
  routing (md→startfile, drawio→viewer, xml sniffing, callback
  error/raise fallback, OSError envelope, subdir paths, sandbox
  escape), `open_content` (write+open, ext forcing, default
  timestamped names, traversal sanitisation, overwrite, 4 MB cap),
  `list_openable_types`, manager wiring (from_config registers the
  3 tools; `set_diagrams_opener` pushes down; end-to-end
  `mgr.call("open_file")` reaches the callback), stdio server
  handlers. `os.startfile` is monkeypatched throughout — nothing
  actually opens on the test machine.
* `tests/test_system_prompt_openwith.py` (9): block presence,
  trigger keywords (zh + en), Explorer semantics, drawio routing,
  security refusal, legacy-mode exclusion, response-language
  composition, coexistence with the drawio/telegram/schedule/todo
  blocks.

**Results**: `test_open_with_host` + `test_system_prompt_openwith`
+ `test_mcp_client` + `test_meta_local_host` = **82 passed,
0 failed**. `pyproject.toml` 0.9.5.8 → **0.9.6.0**. Workspace
version still v0.8.9 baseline (4-Provider LLM dispatch).

**v0.9.6.1: docs-only — the two draw.io services + MCP connect-
failure semantics documented** — user asked "連不到
mcp.draw.io/mcp, drawio 還能用嗎?". Clarified in docs that
`https://mcp.draw.io/mcp` (remote MCP: create_diagram/
edit_diagram/get_diagram tools for the LLM) and
`https://embed.diagrams.net` (the local Drawio Viewer's iframe
canvas) are INDEPENDENT services — the viewer keeps working when
the MCP server is down. Failure semantics: `McpManager`
registers remote servers once at startup; a connect failure
lands in `McpManager.failed` with NO in-session retry; remedies
= GUI restart or re-Saving mcp.json in the Admin tab fleet
editor (`replace_config` hot-reload). Degraded mode still has
`open_content` / `write_file` + `open_file` (the LLM writes the
mxGraphModel itself). Updated: `config/mcp.json.example` (+2
worked examples: drawio streamable_http with the full
two-services/failure/retry note, openwith stdio),
`ARCHITECTURE.md` §1.2 arc (v0.8.7 → v0.9.6.1 entries that were
missing), README `### v0.9.6.1` (behaviour matrix + curl
diagnostics). `pyproject.toml` 0.9.6.0 → **0.9.6.1** (no code /
test changes).

**v0.9.7.0: MCP log anomaly analysis extension — analyse access
log / system log with Qdrant** — new `src/log_analyzer/` package
(`parser.py` — Apache/Nginx combined + syslog RFC3164/RFC5424
parsers, format majority-vote detection, keyword level
classification; `anomalies.py` — rule engine (HTTP error rate,
404 scanning, high-frequency IPs at mean+3σ, SQLi/traversal/admin
paths, scanner user-agents, 401/403 bursts, SSH brute force per
IP/user, OOM/segfault/kernel events, log flood) + semantic
outliers via chunked numpy cosine kNN; `store.py` — `LogStore`
over the DEDICATED `QDRANT_LOG_COLLECTION` (default `log_docs`),
one log line per point, stable `sha1(source+raw_line)` ids so
re-ingest = upsert-overwrite). New `LogAnalysisLocalHost` with 6
tools (`ingest_log` / `analyze_anomalies` / `search_logs` /
`get_log_stats` / `list_log_sources` / `remove_logs`) + stdio
server `src/mcp_server/log_analyzer_server.py`. Read sandbox =
`QDRANT_LOG_ALLOWED_ROOT` (fallback: filesystem read root →
WATCH_DIR → ./documents). System prompt gains the "Log anomaly
analysis" trigger block (zh + en keywords, list → ingest →
analyze flow, table presentation, no raw JSON dumping). New env:
`QDRANT_LOG_COLLECTION` / `QDRANT_LOG_ALLOWED_ROOT` /
`LOG_ANOMALY_OUTLIER_THRESHOLD` (0.35) / `LOG_ANOMALY_MAX_POINTS`
(5000). 95 new tests in `tests/test_log_analyzer.py`.
`pyproject.toml` 0.9.6.1 → **0.9.7.0**.

**v0.9.8.0: MCP archive extension — compress / decompress inside
the filesystem sandboxes** — new `src/archive_tools.py` (pure
format logic) + `ArchiveLocalHost` with 4 tools (`archive_create`
/ `archive_extract` / `archive_list` / `list_archive_formats`)
for zip, 7z, tar, tar.gz, tar.bz2 and gz. Inputs accepted from
read ∪ write roots, outputs pinned to the write root. 7z via the
new `py7zr>=1.0` dependency (pure Python, no system 7-Zip binary;
py7zr 1.x dropped `extract(overwrite=...)` — overwrite semantics
handled by our own guard). Security: Zip-Slip member resolution
(`../` / absolute / drive-prefix rejected per member), dangerous
executable skipping (same blocklist as the filesystem MCP write
tools), zip-bomb budgets (`QDRANT_ARCHIVE_MAX_BYTES` 512 MB /
`QDRANT_ARCHIVE_MAX_FILES` 10000, skip-and-report not raise; gz
streaming cap deletes the partial file). System prompt gains the
archive trigger block; stdio `src/mcp_server/archive_server.py`.
63 new tests in `tests/test_archive_host.py`.
`pyproject.toml` 0.9.7.0 → **0.9.8.0**.

**v0.9.8.1: `ingest_log` accepts compressed archives directly**
(.zip / .7z / .tar / .tar.gz / .tgz / .tar.bz2 / .gz) — archives
are extracted into a THROWAWAY temp dir (never the user sandbox)
via `extract_archive` (Zip-Slip guard + dangerous-ext skip +
zip-bomb budgets); every parsable member becomes its own Qdrant
source `"<archive>!<member>"` so `analyze_anomalies(source=...)` /
`search_logs(source=...)` filter per-member and
`list_log_sources` shows the member-scoped ids. Non-log members
are skipped-and-reported; `log_type=auto` detects PER MEMBER
(a zip can mix access + syslog); `max_lines` caps PER FILE; new
optional `members` allowlist. `_ingest_log` refactored into
`_ingest_log` (dispatcher) + `_ingest_log_file` +
`_ingest_log_archive` + shared `_embed_and_upsert`; plain-file
path unchanged (regression-locked). Tool schemas updated in the
host and the stdio server (+`members` param); system-prompt log
block gains the archive-ingest sentence. +11 tests
(test_log_analyzer.py 95 → 106). `pyproject.toml` 0.9.8.0 →
**0.9.8.1**.

**v0.9.9.0: MCP exec extension — sandboxed local compute (bash /
awk / grep / python / platform shell)** — new `src/exec_tools.py`
(pure execution logic) + `ExecLocalHost` with 5 tools
(`run_shell` — Windows `cmd /c` / POSIX `sh -c`; `run_bash` — GNU
bash -c, Git-for-Windows bundles awk/grep/sed so full Unix
pipelines work; `run_grep` — PURE-PYTHON grep subset (regex/
fixed, -i/-v/-n/-c/-l, context windows, directory walk, binary +
4 MB file skip, max_results) so it is ALWAYS available;
`run_awk` — system awk column surgery over one sandbox file
(MSYS posix-path fix: always pass `C:/...` forward-slash form;
unavailable → clear error suggesting run_bash/run_python;
`run_python` — `sys.executable -c <code>`, cwd = write sandbox,
outputs written back into the sandbox). Safety model: cwd pinned
to the filesystem WRITE sandbox; child env exposes
`QDRANT_READ_ROOT` (real read root) + `QDRANT_WRITE_ROOT` so
scripts read both roots; per-call timeout (`QDRANT_EXEC_TIMEOUT`
default 30 s, hard cap 120 s); stdout/stderr caps
(`QDRANT_EXEC_MAX_OUTPUT` 64 KB); machine-level disaster
blocklist (`rm -rf /`, `mkfs`, `dd of=/dev/sd*`, fork bombs,
`format C:`, `del /f /s`, `rd /s`, shutdown/reboot/poweroff/
halt, `diskpart`, `bcdedit`, `vssadmin delete`, `chmod -R 777 /`
— SANDBOX file operations incl. deletes are allowed by design;
`QDRANT_EXEC_BLOCKLIST=off` disables). System prompt gains the
"Local core tools" trigger block with a tool-choice matrix
(grep for search / awk for columns / bash for pipelines /
python for everything else / shell for OS-native) and the
"LOWER your own workload — never cat whole files into context"
rule. Discovery: `find_bash()` prefers Git Bash over the WSL
system32 stub. stdio server `src/mcp_server/exec_server.py`.
76 new tests in `tests/test_exec_host.py`.
`pyproject.toml` 0.9.8.1 → **0.9.9.0**.

**v0.9.9.1 hotfix: log-analyzer upsert ALWAYS rejected with HTTP 400 —
LogStore.make_entry_id emitted a 24-hex string, not a valid Qdrant
point ID** — user-reported: after every `ingest_log` the `log_docs`
collection stayed at 0 points and the tool returned
`{"error": "Qdrant upsert failed: UnexpectedResponse()"}`. Parsing and
embedding (local llama.cpp Qwen3-Embedding-8B, 4096-dim) were fine —
the failure was the final `client.upsert()`. Root cause:
`LogStore.make_entry_id` returned
`sha1(source + raw_line).hexdigest()[:24]` — a 24-hex-char string.
Qdrant point IDs must be an unsigned integer or a UUID; 24 hex chars
is neither, so EVERY upsert died with
`400 "value 74ddffe... is not a valid point ID, valid values are
either an unsigned integer or a UUID"`. Fix:
`str(uuid.UUID(digest[:32]))` — first 32 hex chars of the sha1
formatted as a canonical UUID string. Still fully deterministic
(same source + line → same id), so re-ingest keeps the
upsert-overwrite semantics; `remove_logs` uses payload filters and
is unaffected. `log_docs` was 0 points so no data migration needed.
The test suite had LOCKED THE BUG IN (`assert len(a) == 24` in
`TestLogStore`) — v0.9.5.7 lesson repeated; replaced with
`str(uuid.UUID(a)) == a` so the id must parse as a legal UUID.
`tests/test_log_analyzer.py` **106 passed**. End-to-end proof on the
live box (2026-09-02): ingest 5 syslog lines → curl
`points_count=5` → `get_log_stats` reads back
`by_level {info:2, error:2, critical:1}` → `search_logs("brute force
password")` semantically hits both Failed-password lines
(score 0.574) → `remove_logs` clears 5 → back to 0.

**v0.9.9.2: OpenRouter provider routing + `:nitro` / `:floor`
model variants** — user request: expose OpenRouter's upstream
provider routing controls (https://openrouter.ai/docs/features/
provider-routing — `order` / `allow_fallbacks` /
`require_parameters` / `data_collection` / `ignore` /
`quantizations` / `sort`) plus the `:nitro` (max-throughput) /
`:floor` (lowest-price) model fast lanes so the user can pin
providers, cut cost, and cut latency from `.env`. All settings
are OpenRouter-only and no-op when unset (non-OpenRouter
providers send byte-identical requests as before).

* `src/config.py` — 8 new `OPENROUTER_*` env vars (all default
  "", zero behaviour change): `OPENROUTER_PROVIDER_ORDER`,
  `OPENROUTER_ALLOW_FALLBACKS`, `OPENROUTER_REQUIRE_PARAMETERS`,
  `OPENROUTER_DATA_COLLECTION` (allow/deny), `OPENROUTER_IGNORE`,
  `OPENROUTER_QUANTIZATIONS`, `OPENROUTER_SORT`
  (price|throughput), `OPENROUTER_MODEL_SUFFIX` (`:nitro` /
  `:floor` / any variant).
* `src/agent/agent.py` — `Agent._apply_openrouter_routing(model)`
  classmethod parses the env vars into `{"provider": {...}}`
  with whitelist validation (sort ∈ price|throughput,
  data_collection ∈ allow|deny, quantizations ∈ int4/int8/fp4/
  fp6/fp8/fp16/bf16/fp32/unknown, bools accept
  true/1/yes|false/0/no); invalid tokens are SKIPPED with a
  warning, never raised — a typo in one routing knob must not
  take down the chat provider. Called from `_pick_provider()` on
  the openrouter branch ONLY; other providers get
  `self._extra_body = {}`. `Agent._routing_create_kwargs()`
  returns `{"extra_body": {...}}` only when the dict is
  non-empty — all FOUR `chat.completions.create` call sites
  (tool loop, max-iterations bail-out, legacy ask, legacy
  chat_turn) spread it; empty config → no kwarg at all
  (byte-identical legacy request). The OpenAI SDK merges
  `extra_body` into the JSON body top level, which is the
  documented OpenRouter `provider` injection point. The model
  suffix is appended in the same helper unless the model
  already ends with it (users may write `...:nitro` directly
  into `OPENROUTER_CHAT_MODEL` — both compose).
* `.env.example` — documented block for all 8 vars.
* README `### v0.9.9.2` entry with the full matrix + worked
  example. 27 new tests in `tests/test_openrouter_routing.py`
  (field parsing, whitelist skips, model suffix, extra_body
  reaching the create kwargs, non-openrouter never injects,
  empty config sends zero extra kwargs);
  `tests/test_llm_providers.py` 19 passed unchanged.

`pyproject.toml` 0.9.9.1 → **0.9.9.2**.

**v0.9.9.3: GUI chat start/end time strip — 「開始 / 結束時間」bar
above the chat log** — user request: show when a chat conversation
started and ended. Message objects already carried a `ts` ISO stamp
(v0.8.0 session store) but `renderChat()` never displayed it. The bar
brackets the visible transcript with the first / last message `ts`.

* `gui/assets/index.html` — new `<div id="chat-time-bar" hidden>`
  between `.chat-toolbar` and `#chat-log`.
* `gui/assets/app.js` — `_fmtChatTime(iso, withDate)` (HH:MM, or
  YYYY/MM/DD HH:MM when the transcript crosses midnight; invalid ts →
  null), `_sameChatDay(a, b)` (Y/M/D equality; invalid → false),
  `renderChatTimeBar()` (reads `chatSession.messages` first/last
  `ts`; empty / ts-less legacy / invalid transcripts hide the bar;
  builds `#chat-time-start` + arrow + `#chat-time-end` via pure
  `textContent` — no XSS surface; `replaceChildren` so re-renders
  never duplicate children). Wired into the `renderChat()` tail —
  the single convergence point covering new / load / clear / append
  paths — AND `setLang()` (language switches do not re-run
  renderChat, so the 「開始 / 結束」 labels follow the locale
  switch). New i18n keys `chatStart` / `chatEnd` in all 4 locales
  (zh-TW / zh-CN / en / ja).
* `gui/assets/style.css` — `.chat-time-bar` flex strip + an explicit
  `.chat-time-bar[hidden] { display: none; }` override (the author
  `display:flex` rule would otherwise beat the UA hidden rule and
  keep the bar visible).
* `tests/test_chat_time_bar.py` — 8 tests: 7 source guards (div +
  hidden default, 3 helpers exist, renderChat + setLang wiring,
  4-locale label count, CSS `[hidden]` override, textContent-only
  build) + 1 Node+jsdom behaviour test running the REAL extracted
  functions against a real DOM across 7 scenarios (empty / legacy
  ts-less / same-day HH:MM / cross-day full date / invalid ts /
  single-message identical start+end / triple re-render no
  duplicates). Driver exits via `setTimeout(process.exit, 50)` and
  tolerates the benign Node-24 0xC00000FD teardown crash (repo
  convention from tests/test_diagrams_window.py).

`pyproject.toml` 0.9.9.2 → **0.9.9.3**.

**v0.9.9.4: per-bubble start/end time headers — replaces the
v0.9.9.3 whole-transcript strip** — user feedback: the strip showed
the whole conversation window; what was wanted is the time on EVERY
chat bubble.

* `gui/assets/app.js` — `renderChatTimeBar()` deleted; replaced by
  `_chatMsgTimeText(m)` (pure string builder): user bubbles return
  the single send time (`HH:MM` today / full `YYYY/MM/DD HH:MM` for
  past days); assistant bubbles with a `startTs` return
  `t('chatStart') + ' ' + start + ' → ' + t('chatEnd') + ' ' + end`,
  both sides upgraded to the full date form when the window crosses
  midnight; legacy ts-less / invalid ts → `null` (no header).
  `renderChat()` now wraps every bubble in a `.chat-msg-row`
  flex-column so the header sits ABOVE the bubble (outside it),
  aligned per side; header rendered via pure `textContent`.
  `appendChatMsg(role, content, sources, startTs)` stores
  `msg.startTs` (rides along in the saved session JSON).
  `sendChat()` captures `const reqStart = new Date().toISOString()`
  before the user append and passes it to the assistant append.
  `setLang()` now re-runs `renderChat()` (pure chatSession rebuild,
  idempotent, no network) so the 開始/結束 labels follow the locale
  switch.
* `gui/assets/index.html` — `#chat-time-bar` div + comment removed.
* `gui/assets/style.css` — `.chat-time-bar` rules removed;
  `.chat-msg-row` (flex column, max-width 80%, user right-aligned /
  assistant+error left-aligned) + `.chat-msg-time` (11px, 0.65
  opacity) added; `.chat-msg-row .chat-msg { max-width: 100% }`
  prevents the row cap and the bubble cap from multiplying to 64%.
* i18n `chatStart` / `chatEnd` kept (now used per bubble); 4 locale
  comments updated to the per-bubble wording.
* `tests/test_chat_time_bar.py` rewritten: 8 source guards (strip
  removal inverted guards for index.html + style.css, 3 helpers
  exist, renderChat wrapper + textContent, appendChatMsg startTs,
  sendChat reqStart capture, setLang re-render, 4-locale labels) +
  1 Node+jsdom behaviour test running the REAL `_chatMsgTimeText`
  across 8 scenarios (ts-less / today / past-day / same-day window
  / cross-midnight window / legacy assistant without startTs /
  invalid ts / invalid startTs fallback).

`pyproject.toml` 0.9.9.3 → **0.9.9.4**.

**v0.9.10.0: opt-in chat context compression — summarize / offload /
selective squeeze** — user request: apply the three canonical context
compression techniques to over-long chat sessions with a config
surface. Default `CHAT_CONTEXT_COMPRESSION=off` keeps chat
byte-identical to v0.9.9.4.

* `src/agent/context_compress.py` (NEW) — `ContextCompressor` with:
  (1) **Summarization** — history over `CHAT_COMPRESS_TRIGGER_MESSAGES`
  (24) folds the older tail into ONE `system` summary message, the
  most recent `CHAT_COMPRESS_KEEP_MESSAGES` (12) stay verbatim; the
  summary is LLM-generated via the injected callable
  (`CHAT_COMPRESS_SUMMARY_MODE=llm`; ANY failure → deterministic
  extractive fallback, no network) and cached per old-prefix sha1
  (LRU cap 8) so a growing session re-summarizes only when the folded
  portion changes. (2) **Offloading** — a SUCCESSFUL tool result over
  `CHAT_OFFLOAD_THRESHOLD_CHARS` (20000) is written under the
  filesystem read sandbox (`chat_offloads/` by default so the LLM can
  `read_file` it back); the tool message becomes a pointer +
  800-char preview; errors are NEVER offloaded; write failure
  degrades to inline. (3) **Selective compression** — any history
  message over `CHAT_COMPRESS_MSG_MAX_CHARS` (4000) keeps fenced code
  blocks and JSON-looking blocks verbatim and squeezes only the
  surrounding prose (head+tail sentences + `…[prose compressed: N
  chars omitted]…`); CJK + Latin sentence splitting.
  `aggressive` mode halves the trigger and per-message budget.
* `src/agent/agent.py` — `Agent.compressor` wired in `__init__`;
  `_messages_with_user()` runs `compress_history()` over the filtered
  user/assistant history (off → identical copy; ANY exception →
  uncompressed fallback); `_run_tool_call()` routes successful
  results through `offload_tool_result()`; new `_summarize_history()`
  LLM call (any failure → None → extractive fallback);
  `_system_prompt(tools_enabled=True)` gains a "Context offloading"
  block teaching the LLM to `read_file` offloaded results.
* `src/config.py` — 8 new raw-string env vars (bad values never
  crash the import; parsed with safe fallbacks by the compressor):
  `CHAT_CONTEXT_COMPRESSION` / `CHAT_COMPRESS_TRIGGER_MESSAGES` /
  `CHAT_COMPRESS_KEEP_MESSAGES` / `CHAT_COMPRESS_SUMMARY_MODE` /
  `CHAT_COMPRESS_SUMMARY_MAX_CHARS` / `CHAT_COMPRESS_MSG_MAX_CHARS` /
  `CHAT_OFFLOAD_THRESHOLD_CHARS` / `QDRANT_OFFLOAD_DIR`.
* `.env.example` — documented block for all 8 vars (commented out =
  feature off by default).
* 35 new tests in `tests/test_context_compress.py` (config parsing +
  aggressive halving, summarization trigger/cache/extractive
  fallback/cap, offload read-sandbox/threshold floor/custom dir/write
  failure/errors-never, selective code-fence + JSON verbatim /
  all-structured / CJK splitting, Agent integration chain +
  compressor-failure degradation + system-prompt block).
* README `### v0.9.10.0` entry + module-tree / import-smoke updates.

`pyproject.toml` 0.9.9.4 → **0.9.10.0**.

**v0.9.11.0: MCP date/time query core tool — get_current_time (LOCAL +
UTC+0)** — user request: a core function to query the current date and
time in both local time and UTC+0, exposed via MCP.

* `src/mcp_client/host.py` — new `DateTimeLocalHost` (the ELEVENTH
  in-process LocalHost, server name `datetime-local`) with ONE
  zero-argument tool `get_current_time`. One call returns `local` and
  `utc` sections side by side (each: ISO 8601 with offset,
  `YYYY-MM-DD` / `HH:MM:SS`, year/month/day/hour/minute/second,
  timezone name, ISO 8601 `utc_offset` via the new `_fmt_utc_offset`
  helper, weekday as locale-independent English name + ISO number)
  plus `epoch_seconds` (ms precision) and a derived-dates note.
  Read-only, no arguments, no sandbox concerns. New import:
  `from datetime import datetime, timezone`.
* `src/mcp_client/manager.py` — `from_config` registers the host
  BEFORE MetaLocalHost (so the `list_mcp_tools` catalog includes it)
  and keeps the `_datetime_local_host` reference (same pattern as
  the exec/archive/log host refs).
* `src/mcp_server/time_server.py` — NEW stdio server
  (`python -m src.mcp_server.time_server`) exposing the same tool to
  external clients (Claude Desktop / Cline); tool specs mirror the
  host surface.
* `src/agent/agent.py` — system prompt "Tool selection rules" gains
  a `get_current_time` rule: '現在幾點' / '今天幾號' / 'what time is
  it' / 'today's date' and any "now"-dependent computation MUST call
  the tool, never guess the clock from memory. Legacy
  (tools_enabled=False) prompt unchanged.
* Tests — 18 new in `tests/test_datetime_host.py`: tool surface +
  schema, local/UTC ISO parse + cross-consistency (≤2 s two-clock
  skew), offset format regex, epoch vs wall clock, `_fmt_utc_offset`
  edges (None / zero / +8 / +5:30 / -5:30), LocalTool coercion round
  trip (bare `{}` / `{"args": {}}` / `None`), manager registration +
  host ref + end-to-end dispatch, MetaLocalHost catalog inclusion,
  stdio spec/host parity + handler path, system prompt presence and
  legacy absence.

`pyproject.toml` 0.9.10.0 → **0.9.11.0**.

**v0.9.11.1: chat latency diagnosis + OPENROUTER_REASONING_EFFORT +
one-shot GUI agent prewarm** — user report: a 'hello' chat turn took
~6 s. Live-box probes measured the breakdown: (1) the
z-ai/glm-5.3-flash endpoint MANDATES reasoning and burned 281
reasoning tokens (4.4 s) for a 10-char answer — `{"enabled": false}`
is rejected with HTTP 400, but `reasoning: {"effort": "low"}` is
accepted and zeroes the thinking pass (1.4 s); (2) the 100-tool
surface inflates EVERY request to 36,312 prompt tokens (4,278
without tools); (3) the first mcp_manager access costs ~15 s
(11 LocalHosts + remote MCP connects) plus ~8 s of imports.

* `src/agent/agent.py` — `_apply_openrouter_routing` step 3 parses
  `OPENROUTER_REASONING_EFFORT` (os.environ direct, monkeypatch-
  friendly) into a TOP-LEVEL `extra_body["reasoning"]={"effort": ...}`
  (NOT inside `provider` — OpenRouter's documented position);
  whitelist (`minimal|low|medium|high`), invalid skipped with a
  warning; openrouter-only, unset = untouched request.
  `Agent.mcp_manager` property is now double-checked-locked
  (`threading.Lock`) — the GUI prewarm thread and the first chat
  turn can race the build; duplicates would re-register every tool.
* `gui/api.py` — `Api._kick_prewarm()` + `_prewarm_agent()`: the
  FIRST get_status poll (GUI boot) starts a ONE-SHOT daemon thread
  (`agent-prewarm`, flag `_prewarm_started`) that builds
  `agent.mcp_manager` + resolves `agent.client` off the chat path;
  all failures swallowed (lazy paths retry on demand).
* `src/config.py` + `.env.example` — `OPENROUTER_REASONING_EFFORT`
  documented; the live `.env` pins `low`.
* Tests — +5 routing (top-level injection, provider composition,
  invalid skip, unset no-op, reaches create kwargs) + 5 prewarm
  (`tests/test_gui_prewarm.py`: thread-once, flag short-circuit,
  synchronous build touches mcp_manager+client, failure swallowed,
  thread target is `_prewarm_agent`). Full suite 1326 passed.

`pyproject.toml` 0.9.11.0 → **0.9.11.1**.

**v0.9.12.0: per-pair token usage on the chat bubble header** —
user request: show the tokens spent on EVERY Q/A pair in the chat
dialog. The header line above each assistant bubble now reads
`開始 HH:MM → 結束 HH:MM · ↑ 36,403 / ↓ 154 tok`.

* `src/agent/agent.py` — `_extract_usage(resp)` pulls
  prompt/completion/total/reasoning tokens off a create response
  (`reasoning_tokens` from `completion_tokens_details`; None when
  the provider omits usage); `_accumulate_usage(turn, resp)` sums
  across tool-loop iterations (+ `calls` counter);
  `self.last_usage` reset to None at every `ask` / `chat_turn`
  entry, set by `_tool_loop` (aggregated) or the legacy one-shot
  paths.
* `gui/api.py::chat_turn` — forwards `last_usage` as `usage` in
  the JS payload; only real dicts pass (MagicMock agents in tests /
  legacy providers cannot pollute the bridge).
* `gui/assets/app.js` — `_chatTokensText(m)` (thousands separators,
  `↑ prompt / ↓ completion tok`, `· N calls` when the tool loop
  fired more than once; null for legacy/zero usage);
  `appendChatMsg(role, content, sources, startTs, usage)` stores
  `msg.usage` (rides along in the session JSON); `sendChat` passes
  `r.usage`; `renderChat()` joins time + tokens into the one
  `.chat-msg-time` header (pure textContent).
* Tests — 12 new in `tests/test_chat_usage.py` (extract fields /
  missing usage / missing details nest, accumulate sums, tool-loop
  aggregation + None, ask/chat_turn lifecycle + stale reset, gui
  wrapper inclusion + non-dict defence) +
  `tests/test_chat_time_bar.py` guards/jsdom updated (token
  formatting / calls / zero scenarios). Live-box proof: hello turn
  reported `{'prompt_tokens': 36403, 'completion_tokens': 154,
  'total_tokens': 36557, 'reasoning_tokens': 0, 'calls': 1}` —
  confirming the 100-tool schema payload measured in v0.9.11.1.

`pyproject.toml` 0.9.11.1 → **0.9.12.0**.

**v0.9.13.0: MCP_ACTIVE_SERVERS tool-surface gate (-70% prompt
tokens/turn)** — user question: "is attaching the FULL MCP tool
info to every chat necessary?" Measured: 100 tool schemas = ~32k
of the 36,312 prompt tokens EVERY turn (v0.9.11.1). Answer: no —
schemas are per-request and only the servers a turn needs matter.

* `src/agent/agent.py` — `Agent._chat_tools()` filters
  `mgr.openai_tools()` by `MCP_ACTIVE_SERVERS` (comma list of
  `server_name` values, os.environ direct like the other v0.9.11+
  knobs; empty = full surface). Applied at BOTH tool-loop call
  sites (`ask` + `chat_turn`). Guardrails: unknown names warned +
  skipped; zero-match gate falls back to the FULL surface (never
  silently disables tool calls); manager dispatch stays by-name so
  schedule fires reach hidden tools. Measured: core set
  (qdrant/web/filesystem/datetime/scheduler/todo/meta) = 53 tools,
  11,022 prompt tokens (was 36,312).
* `src/config.py` + `.env.example` — `MCP_ACTIVE_SERVERS`
  documented with the recommended daily-chat core list.
* Live cache probe: Together auto-caches the 36k prefix
  (cached_tokens=31,360) but the multi-provider order list makes
  hits nondeterministic (Modal serves and does not cache) —
  pinning `OPENROUTER_PROVIDER_ORDER=together` +
  `ALLOW_FALLBACKS=false` gives cached=31,360 on EVERY turn and
  stable 2.1-2.4 s hello latency (4.8 s cold). Documented as the
  .env combo in the README entry.
* Makora cache probe (user request): Makora DOES advertise cache
  (input_cache_read pricing) and DID return cached_tokens=32,256
  on a later call — but hits are NONDETERMINISTIC (0 / 0 / 32,256
  across 3 identical calls) and a hit does NOT translate into
  latency (≈4.0-4.2 s with or without cache). Also observed a 429
  rate-limit on 1 of 4 calls. Together remains the better cache
  provider for this model: deterministic 31,360-token hits AND a
  real latency win (1.7-1.8 s cached vs 3.6 s cold).
* BaseTen/fp8 cache probe (user request): tag `baseten/fp8`,
  quantization fp8, best uptime of the three (30m 99.35% / 1d
  99.81%). Cache warms FAST — hit on the 2nd successful call with
  cached=36,288 (caches MORE of the prompt than Together's
  31,360) and stays warm. But: 1 of 4 calls still hit a 429
  (capacity remains tight), and cached latency (2.2-3.0 s) does
  not beat Together (1.7-1.8 s). Ranking for this model:
  together (cache+latency) > baseten/fp8 (cache quality+uptime,
  429 risk) > makora (hits late, no latency win).
* Tests — 7 new in `tests/test_mcp_active_servers.py` (empty gate
  = full surface, only-listed exposure, unknown warn+skip,
  zero-match fallback, comma/space tolerance, chat_turn
  integration, dispatch-by-name after gating). Full suite 1342
  passed.

`pyproject.toml` 0.9.12.0 → **0.9.13.0**.

**v0.9.13.1: per-pair ⚡ cached_tokens on the chat header** — user
request: show cache info alongside input/output tokens. Live data
already proved the value (Together pinned → cached=31,360 every
turn).

* `src/agent/agent.py` — `_extract_usage` now also pulls
  `prompt_tokens_details.cached_tokens` (0 when absent);
  `_accumulate_usage` sums it across tool-loop iterations.
* `gui/assets/app.js` — `_chatTokensText` appends
  `· ⚡ N cached` only when `cached_tokens > 0` (thousands-
  separated, same line as the time + tokens header).
* Tests — `tests/test_chat_usage.py` `_usage` helper now carries
  BOTH detail nests; extract/accumulate/tool-loop assertions
  updated (cached sums); `tests/test_chat_time_bar.py` jsdom
  scenarios updated (s9 with cache segment, s10/s12 prove ⚡ is
  omitted when absent/zero). 21 passed across both files.

`pyproject.toml` 0.9.13.0 → **0.9.13.1**.

**v0.9.13.2: gate discovery-trap fix — list_mcp_tools now agrees
with the declared surface** — user report: with
MCP_ACTIVE_SERVERS on, windows-mcp misbehaved ("open Paint" → the
model SAID it launched Paint but nothing happened). Log forensics:
the ONLY tool dispatched in all 4 turns was `list_mcp_tools` —
`launch` was NEVER dispatched; the model echoed the tool-call JSON
as plain text. Root cause: the DECLARED surface (gated) and the
DISCOVERY surface (list_mcp_tools full 100-tool catalog)
disagreed — the LLM saw tools it could not call and faked the
call. (RDP was NOT the cause — nothing was ever launched.)

* `src/mcp_client/manager.py` — gate logic moved into
  `McpManager.active_specs()` (gated ToolSpec list; unknown names
  warned; zero-match falls back to full) + `active_tools()`
  (gated McpTool catalog); `last_hidden_servers` tracked.
* `src/agent/agent.py` — `_chat_tools()` is now a thin wrapper
  over `active_specs()`; system prompt gains the never-fake rule:
  undeclared tools CANNOT be called — if the catalog marks a
  server hidden, tell the user to add it to MCP_ACTIVE_SERVERS.
* `src/mcp_client/host.py` — MetaLocalHost.list_mcp_tools now
  lists only the EXPOSED tools; when a gate is active it adds
  `hidden_servers` + `hidden_note` ("CANNOT be called — ask the
  user to add the server").
* `.env` — gate re-enabled WITH `windows-mcp` included (the
  user's active use case; dropping it from the list saves ~25k
  tokens/turn again when desktop automation is not needed).
* Tests — 2 new meta-gate tests (filtered catalog + hidden note;
  no-gate → no hidden fields); test_meta_local_host fakes provide
  `active_tools`; gate warning assertions moved to the manager
  logger. 44 passed across meta/active-servers/mcp-client suites.

`pyproject.toml` 0.9.13.1 → **0.9.13.2**.

**v0.9.14.0: chat-UI MCP tool picker (temporary per-request
enable/disable)** — user request: list every MCP tool in the chat
UI with temporary on/off toggles, no .env editing required.

* `src/mcp_client/manager.py` — `active_specs(tool_names=None)`:
  an explicit per-TOOL allow-list (the GUI picker) OVERRIDES the
  env server gate for that request; None = env gate applies; an
  explicit EMPTY list is honoured (request carries no tools — no
  zero-match fallback, that is the caller's clear intent).
  `active_tools(tool_names)` forwards for the meta catalog.
* `src/agent/agent.py` — `ask` / `chat_turn` /
  `_chat_tools(tool_names)` accept `active_tool_names` and thread
  it through.
* `gui/api.py::chat_turn` — `active_tools` param forwarded as
  `active_tool_names` (None when the picker selection is empty →
  the env gate applies).
* `gui/assets/index.html` + `app.js` + `style.css` — chat-toolbar
  🧰 button + dropdown panel: every tool listed grouped by server
  with checkboxes; DISABLED-set semantics stored in
  `localStorage['chat-disabled-tools']` (new tools default on);
  badge `🧰 N/M`; All-on / All-off / Reset buttons; pure
  createElement + textContent (no XSS surface); i18n ×4.
* Tests — 6 new per-tool tests in
  `tests/test_mcp_active_servers.py` + 11 new in
  `tests/test_gui_chat_tools.py` (bridge forwarding, agent
  pass-through, frontend source guards, 4-locale labels).

`pyproject.toml` 0.9.13.2 → **0.9.14.0**.

**v0.9.14.1: tool-loop budget 6 → 20 (`MAX_TOOL_ITERATIONS` env) +
windows-mcp desktop-automation prompt block** — user report:
「開啟小畫家畫小狗 → Paint 開了卻不繼續畫,回『由於我無法再執行後續的
滑鼠繪製動作』+ 手動步驟」. Log forensics (`gui/app.log` + session
JSON): THREE dog-drawing turns on 2026-09-04 each left
`[agent] tool loop hit max_iterations=6; returning last state` —
`7 calls` on the pair header = 6 loop iterations + 1 forced bail-out
create call. The 14:20 turn burned 2 of 6 on launch failures
(`App(launch, mspaint)` → `Mspaint not found in start menu.`;
`App(launch_executable, C:\Windows\System32\mspaint)` → `Executable
does not exist` — the LLM omitted the `.exe` extension); the 14:21
turn got as far as one ellipse (the dog's head) before the cap.

* `src/agent/agent.py` — `DEFAULT_MAX_TOOL_ITERATIONS` 6 → **20**
  (GUI automation needs 15-30 steps); new module-level
  `_resolve_max_tool_iterations()` reads `os.environ` directly
  (monkeypatch-friendly, same pattern as
  OPENROUTER_REASONING_EFFORT); unset/invalid/non-positive fall back
  to the default with a warning. `ask()` / `chat_turn()`
  `max_tool_iterations` default is now `None` → resolved per call;
  explicit ints still win. `_tool_loop` bail-out message rewritten:
  the injected user turn now says state-which-steps-completed /
  which-is-next / invite 「繼續」 / never claim unfinished work is
  done (the old bare "Please provide your final answer now" is what
  made the LLM say 「無法再執行」).
* System prompt — new "Windows desktop automation (windows-mcp,
  v0.9.14.1+)" trigger block: App launch ladder (mode=launch →
  mode=launch_executable with the FULL absolute path INCLUDING
  `.exe` → `PowerShell Start-Process mspaint`), ABSOLUTE-paths-only
  rule (relative paths resolve against the MCP server's cwd),
  **scripted drawing preferred** (ONE PowerShell System.Drawing call
  renders the whole picture — the successful 09-03 台灣黑熊 pattern;
  Click/Move drag only on explicit 「用滑鼠畫」 requests), budget
  discipline (batch independent tool_calls; report progress honestly
  when the budget ends).
* `src/config.py` + `.env.example` + live `.env` —
  `MAX_TOOL_ITERATIONS` documented (raw string attr; safe fallback
  in the resolver).
* Tests — 16 new in `tests/test_max_tool_iterations.py` (resolver
  ×6, ask/chat_turn per-call resolution ×4, bail-out message shape
  ×1, prompt block + legacy exclusion ×5).

`pyproject.toml` 0.9.14.0 → **0.9.14.1**.

**v0.9.15.0: optional WEB UI mode — choose browser instead of the
pywebview window** — user request: 「提供web 介面支援，可以選擇啟動
web介面」. New `gui/web_server.py` (PURE stdlib `http.server` — no
new dependency) serves the SAME `gui/assets/` frontend and the SAME
`gui.api.Api` methods, so there is ZERO duplication and ZERO
frontend changes:

* `gui/web_server.py` — `GET /` serves index.html with
  `/pywebview-shim.js` injected into `<head>` (runs BEFORE app.js:
  a ~40-line shim implementing `window.pywebview.api.<method>(...)`
  via a `Proxy` over `POST /api/<method>` fetch, `state` object,
  `FileDialog` constants + `create_file_dialog` routed to a tkinter
  dialog on the server desktop, immediate `pywebviewready` dispatch
  — no boot race). `POST /api/<method>` → `{"ok": true, "result":...}`
  envelope over public Api methods only (leading-`_` and unknown
  names rejected; a non-JSON-serialisable result degrades to an
  error envelope instead of killing the request thread).
  `GET /healthz` probe. Path traversal → 403; missing → 404.
* Selector — `python -m gui.app --web` (new `--web` / `--host` /
  `--web-port` / `--no-browser` flags; NO flag = pywebview window
  unchanged), `qdrant-agent-web.bat` (conda-activate launcher, same
  pattern as the GUI bat), `python gui/web_server.py` direct entry.
  Binds `127.0.0.1:8334` by default (`QDRANT_WEB_HOST` /
  `QDRANT_WEB_PORT` env); the Api drives desktop automation and
  filesystem tools, so a non-loopback bind logs a loud WARNING.
* Version display follows pyproject.toml dynamically via the
  v0.9.14.1 `_app_version()` mechanism — Web and GUI show the same.
* Tests — 24 new in `tests/test_web_server.py` (shim injection +
  contract, `call_api_method` dispatch incl. `__file_dialog` and
  non-JSON degradation, live ThreadingHTTPServer round-trips on an
  ephemeral port: index injection, static serving, `../` traversal
  403, 404, /healthz, envelope ok/unknown/invalid-JSON/non-JSON,
  get_status version forwarding, `gui.app` flag parsing). Live smoke
  on the real server: shim ✓ shim.js ✓ /healthz ✓ get_readiness ✓
  private-method rejection ✓.

`pyproject.toml` 0.9.14.1 → **0.9.15.0**.

**v0.9.15.1: Web UI 可綁定本機任何 IP (0.0.0.0 / specific LAN IP /
IPv6) + `.env` silently-ignored fix** — user request: 「支援 web 可以
binding 本機任何ip」. Three fixes in `gui/web_server.py`:

* **`.env` load-order fix (v0.9.15.0 hidden bug)** — web mode read
  `QDRANT_WEB_HOST` / `QDRANT_WEB_PORT` BEFORE `Api()` construction
  (the thing that triggers `src.config`'s `load_dotenv`), so `.env`
  values were silently ignored. `run_web_server` now calls
  `load_dotenv()` first (`override=False` — real env vars / CLI
  flags still win).
* **`0.0.0.0` = all IPv4 interfaces** — `_enumerate_lan_ips()` (pure
  stdlib, three probes: UDP-connect trick WITHOUT sending a packet /
  `getaddrinfo(hostname)` / `gethostbyname_ex(hostname)`, dedup'd,
  loopback + `0.0.0.0` excluded) feeds `_urls_for()`: the banner now
  prints `also reachable at: http://<LAN IP>:<port>/` for every
  reachable interface, and the auto-opened browser uses a USABLE
  loopback URL (v0.9.15.0 opened `http://0.0.0.0:…` — not browsable).
  URL builder `_format_url()` brackets IPv6 literals.
* **IPv6 actually binds now** — `ThreadingHTTPServer` is AF_INET, so
  `--host ::1` (despite being in the loopback warning allow-list)
  raised OSError. New `_ThreadingHTTPServer6` (AF_INET6) is selected
  whenever the host contains `:`; the `::` wildcard also clears
  `IPV6_V6ONLY=0` → dual-stack (one socket serves v4+v6).
* Banner `print()`s use `flush=True` — no more banner lost to stdout
  buffering under pipes / detached launches.
* Security model unchanged: non-loopback binds (incl. wildcard,
  warned as "ALL interfaces") log a loud WARNING; the Api drives
  desktop tools — trusted LANs only.

* Tests — 16 new in `tests/test_web_server.py` (24 → 40):
  `_format_url` / `_urls_for` (loopback / wildcard+LAN / `::`→`[::1]`
  / specific IP), `_enumerate_lan_ips` (unique non-loopback strings;
  never-raises when every probe fails), `run_web_server` wildcard
  bind + browser-opens-loopback, explicit host wins over env,
  `load_dotenv` runs BEFORE env resolution, IPv6 host → v6 server
  class, non-loopback WARNING, real bind failure → exit 1, live
  `::1` bind (skip if no IPv6 loopback).
* Live proof on the box: `--host 0.0.0.0` banner lists
  `192.168.50.154` / `172.22.160.1` / `172.19.16.1` and `/healthz`
  returns 200 via 127.0.0.1 AND all three LAN IPs.

`pyproject.toml` 0.9.15.0 → **0.9.15.1**.

**v0.9.18.0: chat 主鏈與 vision OCR 鏈分離 — .env 可調鏈則順序** —
user request: 「chat primary 和 vision OCR 的鏈則是兩個不同的應用
需求，提供支援可以在 .env 調整 鏈則 順序的機制」. Pre-0.9.18.0 the
chat provider priority (`_PROVIDER_ORDER`) and the vision OCR chain
(ollama → openrouter → siliconflow → openai-compat) were both
hard-coded. Two new env vars (comma list, case-insensitive;
**empty/unset = the legacy order — zero behaviour change**):

* `LLM_PROVIDER_CHAIN` — `Agent._resolved_provider_order()`
  (classmethod) reorders `_PROVIDER_ORDER`; `_pick_provider()` and
  `_provider_candidates()` (primary + failover walk) iterate the
  resolved chain. `LLM_PROVIDER` forced override STILL wins for
  primary selection — the chain only reorders auto-detection and
  the failover walk.
* `VISION_PROVIDER_CHAIN` — `ImageDescriber._chain_names()`;
  new `_vision_candidates()` returns the FULL chain (Ollama
  included) in chain order and `describe()` walks a single loop —
  Ollama is no longer a hard-coded primary, just the first entry
  of the default chain. `_fallback_vision_candidates()` kept as a
  backward-compatible view (non-Ollama chain entries; identical to
  v0.9.17.0 when the env is unset). `enabled` / `last_backend` /
  the `ollama(<model>): ...` error-marker shape unchanged.
* Parser rules (`src/provider_chain.py::parse_provider_chain` —
  a ZERO-side-effect module that deliberately does NOT import
  `src.config`, whose import runs `load_dotenv` and would
  re-inject .env values a test fixture just wiped; re-exported
  from `src.config` for path stability): empty → defaults;
  unknown tokens SKIPPED with a warning (a typo must never take
  the chain down); duplicates collapse to the first occurrence;
  partial lists append the unlisted providers in their default
  relative order — the chain defines PRIORITY, never exclusion.
  Aliases `ollama-cloud` / `openai-compat` accepted in the vision
  chain.
* `config.py` gains the two raw-string attrs (documentation /
  snapshot only; the resolvers read `os.environ`/`os.getenv`
  directly — monkeypatch-friendly, no config reload needed).

* Tests — 28 new in `tests/test_provider_chain_order.py`
  (parser ×9 / chat chain ×8 / vision chain ×11). Env-isolation
  hardening: `test_llm_providers` / `test_openrouter_routing`
  wipe lists gain the two new vars; `test_image_describer_fallback`
  `_clean_env` gains the `SILICONFLOW_*` trio it had been missing
  since v0.9.17.0; `test_upload_ocr_fixes._pin_provider_config`
  now patches the CURRENT `src.config.config` object (reload-safe —
  combined runs used to leak the live .env OPENROUTER key into the
  failover walk, a real HTTP 401 was observed). 6-file combined
  run **133 passed, 0 failed**; full suite (batched) ~1200 passed.
* Live smoke on the box: default order, re-ordered chain,
  keyless-leading-provider skip (openai gpt-4o-mini as primary),
  vision alias tokens, invalid-token warning — all green.

`pyproject.toml` 0.9.17.0 → **0.9.18.0**.

**v0.9.19.0: LLM OpenAI 相容 API 設定 — openai 槽位專屬模型鍵 +
第 5 個 provider `openai-compat`** — user request: 「提供支援 llm
openai-compat api 設定」, answer = BOTH:

1. `OPENAI_CHAT_MODEL` — dedicated model for the EXISTING `openai`
   provider (which IS the generic OpenAI-compatible escape hatch
   via OPENAI_BASE_URL: DeepSeek / LM Studio / vLLM / ...). The gap
   it fixes: the openai slot's model was LLM_MODEL only, and
   LLM_MODEL != "gpt-4o-mini" GLOBALLY overrides the
   ollama/openrouter/siliconflow chat models — pointing openai at
   DeepSeek via LLM_MODEL=deepseek-chat sent "deepseek-chat" to
   ollama.com/openrouter.ai on failover (cross-provider failover
   broken). Priority mirrors the other 3 providers: non-default
   LLM_MODEL still wins; unset OPENAI_CHAT_MODEL = LLM_MODEL
   (zero behaviour change).
2. FIFTH provider `openai-compat` — fully independent
   OPENAI_COMPAT_API_KEY / OPENAI_COMPAT_BASE_URL (REQUIRED) /
   OPENAI_COMPAT_CHAT_MODEL triple, coexisting with `openai` (two
   compat endpoints at once) and joining LLM_PROVIDER_CHAIN
   automatically. Appended LAST in `_PROVIDER_ORDER`: auto-detect
   and default failover order unchanged for existing users. Guard:
   key-without-URL raises a clear error in _pick_provider (a
   default would silently hit api.openai.com) and the failover
   walk skips the candidate with a warning. Error messages updated
   (Valid options / No-provider list + env-name
   `OPENAI_COMPAT_API_KEY` via `.upper().replace('-', '_')`).

* Tests — +8 in `tests/test_llm_providers.py` (OPENAI_CHAT_MODEL
  ×4 incl. the does-not-poison-other-providers lock;
  openai-compat ×4: auto-detect last / primary-when-only-key /
  base_url-required / override-empty-key error naming
  OPENAI_COMPAT_API_KEY) + 1 in `tests/test_provider_chain_order.py`
  (compat joins the chain as primary). Env-isolation hardening: the
  4 new vars join the wipe/pin lists of test_llm_providers /
  test_openrouter_routing / test_provider_chain_order /
  test_upload_ocr_fixes; `test_agent_provider_failover`
  `_resolved_agent` now pins EVERYTHING on the CURRENT config
  object (sys.modules-resolved, reload-safe) — the pre-existing
  order-sensitivity (stale module-top binding no-op'ing the pins
  after test_llm_providers reloads src.config → "No LLM provider
  configured") is fixed for good: NON-alphabetical 6-file runs are
  green too.
* Results — 6-file combined **143 passed, 0 failed** (with the
  failover file deliberately NOT first).

`pyproject.toml` 0.9.18.0 → **0.9.19.0**.

**v0.9.19.1: docs-only sync** — ARCHITECTURE.md caught up with
v0.9.15–v0.9.19 (header version, §1.2 arc entries for 15.2/15.3 /
16.x / 17.0 / 18.0 / 19.0, §2.1 the raw-string env-knobs convention,
§2.8 rewritten as the orderable vision chain, §2.10 Provider
dispatch / Chain order / OpenAI-compatible escape hatches,
Appendix A `provider_chain.py` + updated inventory, footer
last-reviewed 2026-09-07); README's v0.8.9 architecture section
gains a v0.9.18/v0.9.19 update-note block (history preserved);
CLAUDE.md's canonical 5th-provider pattern annotated as realized
(v0.9.19.0) with the two extra guards the realization required
(REQUIRED-env per-provider guard + dash-safe env-name rendering);
test_llm_providers docstring synced. No code / test changes.
`pyproject.toml` 0.9.19.0 → **0.9.19.1**.

**v0.9.20.0: chat 對話記錄即時儲存** — user request: 「chat 對話
記錄支援即時儲存」. Audit of the v0.8.7 3-layer safety net found
three real gaps, all fixed in `gui/assets/app.js` (shared GUI/web):

1. **Send-time save** — `sendChat()` now calls `autoSaveChat()`
   right after appending the USER bubble and BEFORE awaiting
   `chat_turn` (fire-and-forget, doesn't block the LLM call).
   Before: saves only happened after the reply landed — a crash /
   tab close mid-turn (desktop automation / local LLM, tens of
   seconds to minutes) lost the user's message entirely. Now the
   message lands in `sessions/` + localStorage backup the moment
   it is sent.
2. **Error bubbles now save** — both error exits (no response /
   `r.error`) returned after `appendChatMsg('error', …)` WITHOUT
   a save; a refresh dropped the error from history. Both paths
   call `autoSaveChat()` now.
3. **Web-mode unload uses `navigator.sendBeacon`** — a real gap in
   the v0.8.7 design for web mode: the fire-and-forget `fetch` in
   `beforeunload` can be CANCELLED by the browser at unload time
   (desktop and mobile alike), silently losing the last-ditch
   save. Web mode (`window.__WEB_MODE__`, set by the shim) now
   sends via `navigator.sendBeacon` — browser-scheduled, survives
   page death; body matches the `POST /api/<method>` args shape
   ([sid, title, messages]) and the handler json.loads(raw)
   without checking Content-Type (no backend change); falls back
   to the bridge path when the beacon is refused.

Save timeline (v0.9.20.0+): send-time (user msg) → reply-time
(usage/time header) → error bubble → refresh/close via sendBeacon
→ process crash via localStorage backup + bootDraftRecovery.

* Tests — +4 in `tests/test_chat_autosave.py` (send-order lock
  `appendChatMsg('user') < autoSaveChat < 'chat_turn'`; both error
  branches save; `_beforeUnloadSave` sendBeacon + body shape).
  `node --check app.js` passes. 21 passed, 0 failed.

`pyproject.toml` 0.9.19.1 → **0.9.20.0**.

**v0.9.21.0: MCP chat-history query core (LLM can read past chats)**
— user request: 「以mcp方式，製作一個核心功能，讓llm可以查詢歷史
對話記錄」. TWELFTH in-process LocalHost `ChatHistoryLocalHost`
(server `chat-history-local`) — three READ-ONLY tools over the
persisted sessions via `src.agent.session.SessionManager`:
`list_chat_sessions` (newest first, title keyword filter,
scheduler-fire sessions hidden by default — sid prefix `sched-`),
`search_chat_history` (case-insensitive keyword across message
content, ±context snippets, optional sid scope + role filter),
`get_chat_session` (most-recent `last_n` messages, per-message
content capped). Built-in caps only (search 10 soft / 50 hard,
snippet 280/2000, total budget 8000 chars; get last 20 / 100 hard,
content 1200/4000) — zero new env vars; a runaway history dump can
never blow the context window. `from_config` registers it before
Meta (`_chat_history_local_host` ref kept); stdio mirror
`src/mcp_server/chat_history_server.py`. System prompt
tool-selection rules gain the chat-history trigger block (『上次聊
過什麼』 / 'did we discuss X' → query the tools, never fabricate
from memory); legacy prompt excludes it. `MCP_ACTIVE_SERVERS`
gate: live `.env` + `.env.example` gain `chat-history-local`
(hidden-by-gate = the v0.9.13.2 discovery trap otherwise).
* Tests — 37 new in `tests/test_chat_history_host.py` (surface ×3,
  list ×7, search ×10, get ×6, coercion round-trip ×3, manager
  wiring ×3, stdio parity ×3, system prompt ×2). mcp_client /
  meta / datetime suites 90 passed alongside. Live E2E: 63 real
  sessions listed, `search "qdrant"` 9 hits, get_chat_session
  read back real conversations.

`pyproject.toml` 0.9.20.0 → **0.9.21.0**.

**v0.9.21.1: hotfix — brand-side version chip stuck at v0.8.6**
— user report: 「🧠 Qdrant RAG Agent v0.8.6 的 v0.8.6 正確嗎」.
Answer: NO — pyproject is 0.9.21.x. Root cause: v0.9.14.1 rewired
gui/api.py (`_app_version()` reads pyproject.toml — single source
of truth) but MISSED the second version literal in the frontend:
`index.html` carried `<span class="version" id="app-version">
v0.8.6</span>` — a v0.8.6-era static fallback — and app.js NEVER
updated `#app-version` (refreshStatus only stuffed the version into
the bottom status-bar detail). Fix: index.html fallback literal
emptied (no second version literal anywhere); `refreshStatus()`
now paints `r.version` (pyproject dynamic value) into
`#app-version` on every poll; the `'0.9.14.1'` fallback literal in
setStatus's detail is gone too (r.version is always set).
* Tests — +2 in tests/test_web_server.py::TestHttpRoundTrips
  (no-hardcoded-version HTML guard + refreshStatus-paints-chip
  source lock); 14 passed, node --check OK.

`pyproject.toml` 0.9.21.0 → **0.9.21.1**.

**v0.9.22.0: fix — scheduler daemon does not auto-resume on restart**
— user report: 「2026-09-07T14:00:00 的排程為何沒有啓動」. Diagnosis
(live schedule file + app.log): the schedule itself was PERFECT
(`9e924b90ac9c`「下午2點股市異常分析」cron 0 14 * * *, enabled=True,
next_fire_at=2026-09-07T14:00:00, fire_count=0, no last_error, no
history.jsonl). Root cause: the scheduler daemon is a THREAD inside
the app process — schedules persist in schedules/*.json but the
daemon dies with the process. The last `scheduler: started` line was
11:37 (the user had restarted the web UI to apply the openai-compat
provider config); the v0.8.6.3 auto-start fires ONLY when the LLM
ADDS a schedule, so nothing was ticking at 14:00.
* Fix — boot auto-resume: `gui/api.py::_resume_scheduler_daemon(agent)`
  hooked into `_prewarm_agent()` (first get_status poll = boot):
  resumes the daemon whenever at least one schedule is enabled +
  lifecycle-active (`agent.start_scheduler()` is idempotent), INFO
  logs the schedule names, ALL failures swallowed (the GUI Schedules
  tab button remains the manual path). The missed `next_fire_at`
  catches up on the first tick (tick_once phase-1 gate: <= now →
  fire) — the 14:00 cron auto-runs after restart (its prompt sends
  Telegram; the user was warned).
* Opt-out: `QDRANT_SCHEDULER_AUTORESUME=off` (.env.example block;
  default on).
* Tests — 11 new in `tests/test_scheduler_boot_resume.py` (resume-on-
  enabled / skip-none / skip-all-disabled / skip-status-failed / env
  off + case-insensitive / env on / start_scheduler failure swallowed /
  prewarm integration calls resume / resume failure isolated from
  prewarm / ScheduleManager failure self-swallowed — the staticmethod
  monkeypatch pattern avoids instance-attr function binding). Existing
  test_gui_prewarm / test_scheduler_daemon_autostart contracts
  unchanged.

`pyproject.toml` 0.9.21.1 → **0.9.22.0**.

**v0.9.23.0: fix — schedule fires discard the LLM's summary
(answer=0 chars, dual root cause) + empty-final-answer wrap-up**
— user report (live log 2026-09-07 14:55): 「執行排程 最後沒有用LLM
總結」— the daemon resumed correctly (v0.9.22.0 ✓) and fired; the
LLM ran get_current_time → web_search ×2 → list_mcp_tools but
`fired ... (answer=0 chars)`.
* Root cause 1 (core bug): `make_agent_on_fire` read the result
  with `result.get("answer")` — but `Agent.chat_turn` returns the
  TUPLE (answer, context_md, sources_list); the dict contract only
  matched the test fakes. `isinstance(result, dict)` was False →
  answer was ALWAYS None → the session recorded "[error] None" and
  history "answer=0 chars" EVEN WHEN the LLM had produced a full
  summary. Fix: _fire accepts both tuple and dict shapes.
* Root cause 2: the Telegram MCP server was NOT in
  config/mcp.json — send_telegram_message was never in the tool
  catalog (the LLM called list_mcp_tools to look for it; the web
  chat retry self-reported the same). Fix: telegram stdio entry
  added (token/allowlist inherited via env).
* Hardening: `_tool_loop` now does ONE no-tools wrap-up create
  when the final answer after a tool round is empty (reasoning
  models can emit content="" with finish_reason="stop") — the
  summary is produced from the tool results already in the
  message list; wrap-up failure degrades to the empty string as
  before; round-1 empties skip the wrap-up.
* Tests — test_scheduler.py +2 (tuple records the real answer /
  empty tuple doesn't crash) + tests/test_tool_loop_wrapup.py
  ×4. Related suites 93 passed.
* Gate wiring (missed companion): live `.env` + `.env.example`
  MCP_ACTIVE_SERVERS now include `telegram` — the mcp.json
  server_name for the new stdio entry. Without it the gate HIDES
  send_telegram_message from the chat surface (v0.9.13.2
  contract) and schedule prompts asking for Telegram would hit
  the discovery trap again. Verified e2e: telegram stdio
  connects, tools = send_telegram_message / send_telegram_markdown
  / list_allowed_chats, gated surface 79 tools with
  send_telegram_message EXPOSED.

`pyproject.toml` 0.9.22.1 → **0.9.23.0**.
