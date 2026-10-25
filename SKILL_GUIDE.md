# tacti-local 使用說明 — Tacti (戰術) 計畫編譯器

> 適用版本：v0.9.112.0+（in-process LocalHost `tacti-local`；v0.9.109–120.2
> 期間名為 `skill-local`，v0.9.120.3 改名以免與 SKILL.md 技能檔案庫混淆）
> 核心模組：`src/agent/skill_compiler.py`；工具面：`TactiLocalHost`
> 原則：**先 compile（優化）再執行** — 需「多個回合分工運作、非單一 chat
> 回合可完成」的工作，先編譯成 wave-plan，再由波次驅動器在背景逐波執行。

---

## 1. 什麼時候用哪個機制（決策表）

| 你的工作形態 | 用什麼 | 為什麼 |
|---|---|---|
| 一個回合內就能做完（查個報價、答一句話、開個檔案） | **直接做，什麼都不要編譯** | 編譯是為「多回合分工」設計的，單回合工作用它反而是 overhead |
| 「幫我在背景跑 X，好了跟我說」— 單一長工（一段深研究、一次轉檔、一批桌面自動化） | `run_task_once`（task-local） | 一個 prompt 丟一個背景回合；獨立 session + 獨立預算 |
| **多個分工單元、有先後依賴、要並行、可重試可恢復**（逐章翻譯、多檔分析、六階段交易研究、多帳號盤點） | `compile_plan` → `run_plan` | 編譯器自動驗證依賴/分波分批/注入驗證節點；每 stage 獨立回合預算；失敗只重跑失敗單元 |
| 定時重複執行（每天收盤分析） | `add_scheduled_task`（排程） | cron/interval/once_at 的到點觸發 |

與 Todo 樹的關係：Todo 是**看板**（狀態追蹤/依賴可視/跨回合恢復），不會自己執行；
plan 是**執行單位**。要做「排程化的多階段大工作」時，可以兩者並用：
Todo 記藍圖、`compile_plan`+`run_plan` 負責實際執行與驗證。

---

## 2. 事前設定（一次性）

```ini
# .env — 讓 LLM 看得到這組工具（沒列 = 整組隱藏）
MCP_ACTIVE_SERVERS=...,tacti-local

# 每 stage 的硬超時（秒；0 = off）— 防無聲掛死
QDRANT_SKILL_STAGE_TIMEOUT_S=1800
# 每波最多並行的 stage 數（0 = 不限）
QDRANT_SKILL_WAVE_CAP=4
# 可選：plans 存放目錄覆寫（預設 $QDRANT_RAG_HOME/plans）
#QDRANT_SKILL_PLANS_DIR=

# 讓 stage 完成/失敗主動 Telegram 通知（fail-closed：需 token + allowlist）
QDRANT_TASK_NOTIFY=on
# 進度監控：有任務在跑時每 N 秒推送 ⏳ 摘要；0 = off
QDRANT_TASK_MONITOR_INTERVAL_S=300
# 通知對象（預設 = 承認清單第 1 個 chat）
#QDRANT_TASK_NOTIFY_CHAT_ID=<YOUR_TELEGRAM_CHAT_ID>
```

改完 **重啟 GUI/web**（RESTART required）。驗收：chat 裡問「本地有哪些
 MCP 功能」→ `list_mcp_tools` 應列出 `tacti-local`。
 注意：TACTI = 戰術「計畫編譯器」(compile_plan/run_plan)；
 SKILL.md「技能檔案庫」是另一套（skill-library-local）。
 注意：TACTI = 戰術「計畫編譯器」(compile_plan/run_plan)；
 SKILL.md「技能檔案庫」是另一套（skill-library-local）。
 注意：TACTI = 戰術「計畫編譯器」(compile_plan/run_plan)；
 SKILL.md「技能檔案庫」是另一套（skill-library-local）。

 §2.5 自進化食譜（v0.9.124.0+，self-evolving-agent 譜系借鏡）

**多假設並存（Memento population 借鏡）**：做法本身不確定時，不要押一個
長賭注 stage——`compile_plan` 編 **兩個獨立並行 stage**（同 context、不同
假設；wave 原生支援並行），再加一個合成 stage `depends_on` 兩者，讀兩份
`out/<stage_id>.json`（get_plan_status 有 handoff_path）依共享證據裁決獲勝
假設，只讓獲勝路線進後續 waves。

**失敗 crossover（Evo² 借鏡）**：stage 失敗時，該 stage 的 handoff 檔
`out/<sid>.json` 會帶 `recovery_hint`——重試輪/下游 stage 應先
`skill_search('<主題>')` 技能檔案庫找已驗證配方補位（adapt donor），而不是
原地重推導；同時把教訓寫進規則簿（`rules_add` / `rules_revise`，證據=引這次
失敗），之後每一回合都吃得到。

**事後檢討（Evo² Reflector→Proposer 借鏡，opt-in）**：`.env` 設
`QDRANT_REFLECT_POSTMORTEM=on` 後，plan 以 failed/partial 收場會自動在背景
追加一個雙角色檢討回合（讀失敗 stage 的 session + handoff → 分診「實作 bug
vs 理解錯誤」→ 無證據不得修訂規則簿）。預設 off（多燒 LLM tokens）。

**成功經驗固化（v0.9.127.0，與失敗迴路對稱的收尾）**：plan 以 **done** 收場
且 ≥3 stages（`QDRANT_PLAN_SKILL_DRAFT_MIN_STAGES`）時，驅動器自動把整個
plan 草擬成一份 SKILL.md（frontmatter + 每 stage 的 prompt/驗證/工具白名單
+ handoff 慣例）落在 `$QDRANT_RAG_HOME/skill_drafts/plan-<名><id>/`——
**只產草稿、永不自動入庫**；複核後
`skill_import(source=<draft 資料夾>, confirm=true)` 即完成「做成功一次 →
以後 0 token 照配方走」的固化。`QDRANT_PLAN_SKILL_DRAFT=off` 退出。
同版配套：驗證糾正回合的「驗證者異源 pin」（
`QDRANT_VERIFIER_ALT_PIN`，預設 on——糾正回合改用鏈上另一家
openrouter/ollama 模型，單供應商自動退回）；規則療效度控（規則檔每條自動
累計 `errors`/`last_error`，rollback 決策量化）；ToolRoute 觸發列增補
（`toolroute_override_add` → `agent_toolroute.md`，
`QDRANT_TOOLROUTE_OVERRIDES`=shadow/on/off，rulebook-local 工具面 5→7）。

**外部客戶端**（Claude Desktop / Cline）：

```json
{
  "name": "tacti",
  "transport": "stdio",
  "command": ["python", "-m", "src.mcp_server.tacti_server"]
}
```

（stdio 獨立程序內每 stage 用自建的 Agent，首次建立無共享工具艦 —
偏好 in-process 面像。）

---

## 3. 快速上手 — 跟 agent 講話即可（使用者視角）

你**不需要**親自呼叫工具；觸發詞出現時 LLM 會自己走
compile_plan → run_plan 流程。有效的說法：

| 觸發語（zh/en） | 會發生什麼 |
|---|---|
| 「把這個 workflow 編譯成計畫再執行」 | compile（驗證/分波/注入驗證） → 回你 plan_id + waves 摘要 → 等你說執行 |
| 「這是戰術/技能/策略：第一步…第二步…，分階段在背景執行」 | 編譯 + 直接開跑 |
| 「multi-stage, run it in the background」 | 同上 |
| 「計畫跑到哪了？」 | LLM 用 `get_plan_status` 回報每 stage 狀態 |
| 「重跑 ch5 那一步」 | `run_plan_stage`（單 stage 重試） |
| 「停掉那個計畫」 | `stop_plan` |
| 「我有哪些技能/計畫？」 | `list_plans` |
| 「**整個計畫用 qwen3:30b 跑**」（或某 stage 換模型） |
#compile_plan(default_llm…)/stage.llm（v0.9.110+） |
| 「這個計畫只准用檔案/搜尋這幾個工具」（工具白名單） |
#compile_plan(tools_allow=[…])/stage.tools_allow（v0.9.111+）— 機制化作用域 |

### 完整示例（Telegram 上的 OpenIntro 全書翻譯）

```
你：  幫我把 OpenIntro Statistics 8 個章節翻成繁中，每章一個 stage
      編譯成計畫執行；每章翻完存 stats_chapters/chX_中文翻譯.md 並
      用 stat_file 驗證大小；完成 Telegram 通知我。

Bot： 已編譯 plan sk-openintro-...-a1b2c3：10 stages / 2 waves
      （wave1: 下載原版+切檔；wave2: ch1..ch8 並行×4 波次分批）
      ⏳ run_plan 啟動中…

（隨後每 5 分鐘）⏳ 背景任務進度：3 個 running —
      翻譯 ch1 (180s)；翻譯 ch2 (120s)；翻譯 ch3 (90s)

（每章完成）✅ 背景任務完成：〔openintro-...▸翻譯 ch1〕— 回覆 300 字

（全部完成）✅ 計畫〔OpenIntro 全書翻譯〕完成 — 8 done / 0 failed
      （共 10 stages）｜get_plan_status 可查細節
```

如果 ch5 失敗：

```
你：  ch5 為什麼失敗 → 重跑 ch5
Bot： （get_plan_status 顯示 ch5 verify failed: artifact does not
      exist）→ run_plan_stage 重試 → 成功 → run_plan 續跑 → ✅ 2/2
```

---

## 4. 六個工具規格（LLM/進階使用者視角）

### 4.1 `compile_plan(name, stages, description?, on_failure?, notify_chat_id?, default_budget?, wave_cap?, stage_timeout_s?, default_llm?, tools_allow?) -> dict`

**stages**（1..20 個，每個一個 dict）：

| 欄位 | 必填 | 說明 |
|---|---|---|
| `title` | ✅ | 人類可讀的 stage 名（顯示在看板/通知） |
| `prompt` | ✅ | **這一個 stage 的完整執行 prompt** — 它會成為一個獨立回合的問題本體；背景 agent 不會回問你，prompt 必須自足（路徑寫絕對/沙盒內相對、產出檔名寫清楚、驗證要求寫清楚 — 寫不清楚 compiler 會注入，但不確定的意圖救不了） |
| `id` |  | 自訂 stage id（預設從 title slug 化；依賴引用用 id 或標題皆可） |
| `budget` |  | 此 stage 的工具迭代上限（預設 = `.env` MAX_TOOL_ITERATIONS；clamp 1..200） |
| `llm` |  | 可選。**此 stage 專用模型** `provider:model`（限 openrouter/ollama；compile 當下就對真實目錄驗證 — 錯模型直接 PlanError）。覆寫 `default_llm`（plan 級預設）；兩者皆無 = 預設 LLM 鏈 |
| `tools_allow` |  | 可選。**本 stage 的工具白名單**（機制化作用域，v0.9.111+）：回合的工具面只暴露清單內工具（chat_turn 的 per-TOOL 閘）；非 list/空 list compile 直接拒絕。覆寫 plan 級 `tools_allow`；兩者皆無 = 完整工具面 |
| `depends_on` | | 上游 stage 的 id 或 title 清單 — 依賴完成才會跑；**有依賴的 stage 會自動注入「上游交接檔」讀取指引**（見 §5.1） |
| `verify` | | 機械驗證節點：`{path, checks?}` — path 為產出檔（相對 = write 沙盒）；跑完自動 `verify_artifact`，不通過 = stage 失敗（見 §7 checks 全表） |

**plan 層參數**：

| 參數 | 預設 | 說明 |
|---|---|---|
| `on_failure` | `"stop"` | `"stop"` = 有 stage 失敗 → 後續波 skipped、計畫 failed；`"continue"` = 失敗 stage 的**下游** skipped，互不相關的 stage 照跑 |
| `notify_chat_id` | None | 每 stage 完成時，編譯進 prompt 的 send_telegram 回報對象 |
| `default_budget` | MAX_TOOL_ITERATIONS | 全 plan 的預設 stage 預算 |
| `wave_cap` | 4（env 可調） | 每波最多並行 stage；超過的分批到後波 |
| `stage_timeout_s` | 1800（env 可調） | 每 stage 硬超時（0=off） |
| `tools_allow` | None | plan 級工具白名單預設（每個 stage 繼承；stage 級可覆寫；None = 完整工具面） |

**回傳**：

```json
{"ok": true,
 "plan_id": "sk-openintro-a1b2c3",
 "name": "OpenIntro 全書翻譯",
 "waves": 2, "stages": 10,
 "wave_detail": [{"wave":1,"stage_ids":["stage-1","stage-2"],
                  "titles":["下載原版 PDF","逐章切檔"]}, ...],
 "budget_total": 500,
 "notify_chat_id": <YOUR_TELEGRAM_CHAT_ID>}
```

編譯錯誤＝明確 error 字串（會指出第幾個 stage 錯什麼 — 依賴不存在/
環/缺 title/prompt 等），**永不靜默**。

### 4.2 `run_plan(plan_id) -> dict`（執行/續跑）

- 啟動波次驅動 daemon，立即回 `{"ok": true, ..., "status": "running"}`。
- **RESUME 語意**（重點，三種情況）：
  1. **程序曾重啟**：孤兒 `running` stages 歸位 pending，其餘照波次推進；
  2. **failed/partial plan**：done 的跳過；上波 failed 由你先 `run_plan_stage` 補齊 → 再 `run_plan` 時 skipped 且依賴已補齊的**下游 stage 復活重跑**；
  3. **done plan 再 run**：全 reset 從頭重跑（技能可重跑）；只想重跑某一步 → 用 `run_plan_stage`。
- 進度查 `get_plan_status`；list_tasks 也看得到每 stage（source=`plan`）。

### 4.3 `get_plan_status(plan_id) -> dict`

```json
{"id":"sk-...", "name":"...", "status":"running",
 "waves":2, "progress":"5/10", "running":3,
 "on_failure":"stop", "notify_chat_id":<YOUR_TELEGRAM_CHAT_ID>,
 "stages":[{"id":"stage-3","title":"翻譯 ch1","wave":2,
            "status":"done","task_id":"e12ab","sid":"plan-...-stage-3",
            "budget":50,"verify_ok":true,"answer_chars":300,
            "error":null}, ...],
 "note":"get_chat_session(sid) 讀 stage 全文；..."}
```

### 4.4 `list_plans(limit?=20)` — 技能庫清單（id/name/狀態/stages 進度/waves/updated_at）

### 4.5 `run_plan_stage(plan_id, stage_id) -> dict` — 只重跑**一個** failed/skipped/pending stage（done 的會拒絕）。跑完若要推進依賴它的後續波 → 再 `run_plan`（done 自跳）。

### 4.6 `stop_plan(plan_id) -> dict` — 請求停止：running → failed(`stopped by user`)、pending → skipped、已完成保留；計畫狀態 `stopped`。

---

## 5. compile 自動注入的執行規則（每個 stage 回合都會看到）

```
[PLAN STAGE] plan=<name> stage=<title> (wave <w>/<total>, stage <i>/<n>)

<你的 stage prompt 原文>

──────────── 執行規則（compiler 自動附加，強制）────────────
1. 本 stage 是一個獨立回合 — 預算 = <N> 次工具迭代；只做本 stage
   範圍內的工作，其他 stage 的內容不要做。
2. 每個產出檔案寫完立即 stat_file 驗證存在且非空；多產出逐一驗證。
3. [帶 verify] 最後呼叫 verify_artifact(path='…') 機械驗證；
   驗證失敗 → 你的回覆必須以「驗證失敗 + 原因」開頭，禁止任何
   成功結論。
   [無 verify] 回覆時如實列出「已產出檔案 + 大小」；產不出來就以
   「執行失敗 + 原因」開頭，禁止空口宣稱完成。
4. 檢查點：多步驟時每個子步完成即把進度寫進 checkpoint 檔；
   若這是重跑本 stage，先找先前 checkpoint，已完成部分直接跳過。
5. [有依賴] 上游交接檔（驅動器自動寫出；read_file 讀取）：
    - <上游 title> (id=…) -> <write_root>/out/<上游id>.json
   先讀上游摘要再動工；禁止重做上游已完成的工；檔案不存在 =
   上游未完成 → 「執行失敗 + 原因」回報。（你本 stage 的結果摘要
   也會由驅動器寫進 out/<stage_id>.json 供下游承接。）
6. [notify_chat_id] 結尾用 send_telegram_message(chat_id=…, text=…)
   回報「<title> 完成/失敗 + 產出與大小」。
```

### 5.1 機械交接檔（v0.9.111+）— LLM 自覺不是契約的一部分

每個 stage 終局時由**驅動器**（不是 stage LLM）把結果摘要寫進
write 沙盒下的 `out/<stage_id>.json`：

```json
{"plan_id": "sk-…", "stage_id": "stage-3", "status": "done",
 "summary": "<stage 回覆全文的前 2000 字>",
 "artifacts": [{"path": "<verify.path>", "kind": "verify"}],
 "session": "plan-…-stage-3", "written_at": "…"}
```

下游 stage 的 compiled_prompt 會注入這些檔案的**絕對路徑**（read_file
直接讀 → 拿到摘要 + 正式產出位置 + session 指標可 get_chat_session
回全文）。驗證失敗的 stage 也會誠實寫 `status: failed`。
寫檔失敗只降級（不影響 stage 本身）。 plan 終局時整個計畫的結論另
會沉澱成長期記憶 `kind=task-result`（**只存不自動召回** — 沿用
v0.9.67.2 fire 記憶教訓的隔離設計；查歷史計畫結論用
`search_memory(kind='task-result', query=…)`）。

---

## 6. 波次 / 併發 / 預算模型

```
依賴圖  A ──┬──► C ──► E
            B ──► D ──┘        A,B 無依賴；C depends A；D depends B；E depends C,D

topo 分波：wave0 = A,B；wave1 = C,D；wave2 = E
cap 分批（cap=2）：wave0=[A,B]（剛好 2）→ wave1=[C,D] → wave2=[E]
cap=1：[A] [B] [C] [D] [E]（全序列）
cap=0：A,B | C,D | E（依賴才分波，獨立不限）

執行：每一波 = N 個平行背景回合（各自預算=stage budget；總 token ≈ Σ budget
+ 每 stage 的 prompt 重付）。牆鐘 ≈ 波數 × 最慢 stage。
```

- **預算**：每 stage 回合是完整的 tool loop（最多該 stage 的 budget 次迭代，
  預設 50/由 env）；stage 爆預算 → bail-out 如實收尾（哪些檔已驗證存在）→
  回你「下一步」— 由 run_plan_stage 接手。
- **並行上限**：預設 4，防 provider 429/瞬時 token ×N/本機資源踩踏（05:41 事故教訓；
  大量單元 → 分批自動處理）。
- **模型 pin（v0.9.110+）**：stage `llm` / `default_llm` 住進「這個 stage 用哪個模型」；pin 住 = **不參與跨供應商 failover**（撞 provider 錯誤 → stage failed 如實記錄），發現面 =
  `list_task_models(contains=…)`（task-local）。
- **工具作用域（v0.9.111+）**：plan/stage `tools_allow` — 每 stage 回合的
  工具面**機制化**縮到白名單（chat_turn per-TOOL 閘，與 GUI 工具挑選器
  同一道）；confirm 閘/沙盒是 code 級不受影響；未知工具名 = 該工具單純
  不暴露（絕不回落全量 — fail-closed）。
- **fire 模型 pin（v0.9.111+）**：排程/webhook 觸發器也能帶 `llm` 欄位
  （ADD 時目錄驗證、fail-closed；fire 時失效自動降級預設鏈 + 警告 —
  排程韌性優先）。高頻 fire 配便宜模型（如 `openrouter:z-ai/glm-4.6`）
  = 直接省 token 費用的主要槓桿。

---

## 7. verify 機械驗證 — checks 全表（verify_artifact）

| check.type | 參數 | 意義 |
|---|---|---|
| `min_bytes` | `value` | 檔案 ≥ N bytes（空殼檔防呆） |
| `json_fields`（預設） | `field`（dotted `a.b[0].c`；可 `required` 子欄位清單） | JSON 欄位存在（任一 check 帶 field → 全檔先 JSON 解析） |
| `json_number` | `field` + `min`/`max` | 數值範圍 |
| `json_array` | `field` + `min_length` | 陣列長度 |
| `json_string` | `field` + `pattern`（regex） | 字串型別 |
| `file_exists` | `field` | JSON 內記錄的路徑再檢查 |

未知 type **fail-closed**（打錯字永不靜默通過）；沙盒外路徑拒絕；永不 raise —
結果 = `{ok: bool, errors: [...]}`，是 stage 判 dòo 結的單一機器事實。

---

## 8. 失敗 / 重試 / 恢復（語意總表）

| 情境 | 行為 |
|---|---|
| stage 撞回合預算 | bail-out 如實收尾（不假裝完成）→ stage 狀態 = done 但回覆列「未完成步驟」；需要才用 run_plan_stage 重跑該 stage |
| stage 例外/LLM 失敗 | stage failed（error 欄有 repr） |
| stage 硬超時 | **v0.9.117.0 仲裁**：到期點判決 — 派發數仍在成長（liveness）或 live Jev 判 `normal_long_work`（prob≥0.5）→ **加窗續跑**（上限 `QDRANT_SKILL_STAGE_TIMEOUT_EXTENSIONS` 預設 3 窗）；耗盡/abnormal/gate off → stage failed（`stage hard timeout after Ns`；有加窗歷史時附 `(+N extension(s))`） |
| stage 驗證失敗 | stage failed（error = `verify failed: …`；verify_ok=false） |
| on_failure=stop | 當波有 failed → 計畫 failed：**未開波的 stage 全部 skipped** |
| on_failure=continue | failed stage 的直接下游 skip；彼此獨立的照跑；計畫結果 partial/failed |
| 重試單一 failed/skipped | `run_plan_stage` → 順利後 `run_plan` 推後續波（skipped 且依賴補齊者復活） |
| 使用者要求停止 | `stop_plan` → stopped（成果保留）+ **v0.9.120.0：撒 stop 旗到全部 stage threads — 在飛/殭屍 stage 回合在下一個工具段落合作收尾（registry `stopped`）** |
| 程序重啟（crash/Ctrl+C/升級） | plan 檔仍在 → `run_plan` RESUME（孤兒歸位） |
| done 計畫想再跑一輪 | `run_plan` = 全 reset 重跑（技能可重跑） |

---

## 9. 通知與監控矩陣（哪些通知你收得到）

| 機制 | 條件 | 內容 |
|---|---|---|
| stage 完成/失敗（即時） | `QDRANT_TASK_NOTIFY=on` + token + allowlist | 「✅ 背景任務完成：〔計畫▸stage〕— 回覆 N 字」 |
| 進度摘要（定時） | 同上 + `QDRANT_TASK_MONITOR_INTERVAL_S>0` | 「⏳ 背景任務進度：3 running — 翻譯 ch1 (180s)；…」 |
| plan 最終總結 | 同上（v0.9.109.1 起自動接線） | 「✅ 計畫〔…〕完成 — 8 done / 0 failed（共 10 stages）」 |
| stage prompt 內建回報 | compile 有 notify_chat_id | stage agent 主動 send_telegram_message 的細節（含產出檔+大小） |

---

## 10. 限制 / FAQ

- `QDRANT_SKILL_MAX_STAGES = 20`（單 plan）；更多單元 → 拆多個 plan 或分批由 LLM 投放。
- 單 stage 預算 clamp 1..200（預設 = env 的 MAX_TOOL_ITERATIONS）。
- **stage prompt 自足原則**：背景 agent 不會回問；路徑/檔名/驗證寫清楚。
- **下載/爬取類 stage 的節流紀律（v0.9.119+）**：抓多頁的腳本 = 每
  請求固定 2-5s 間隔 + 429/503 退避重試（30+random×30s，同頁 ≤4 次，
  尊重 Retry-After）+ checkpoint 續抓（done-IDs 寫沙盒 JSON）+
  **一個腳本做完一批**（不要每頁一次工具派發）+ 預設序列。429 =
  網站限「請求頻率」— 序列照樣會中，不是 thread 多（2026-10-09/10
  小說批次實證）。
- **單一回合力所能及的工作不要編譯**（會多付 prompt 重份與波次等待）。
- 大檔讀取：stage 內 >100KB 分段/摘要（v0.9.107 已豁免 offload 洋蔥，但 token 仍爆）。
- plan 檔（plans/*.json）是 durable record：**不要手編 stage 內部欄位**
  （status/task_id 等）；要改計畫 = 重新 compile 一份（名稱相同會產生新 plan_id，
  舊計畫保留在技能庫）。
- **交接檔 out/<stage_id>.json** 在 write 沙盒下（v0.9.111+）— stage 可以
  把更豐富的摘要寫同一檔（驅動器終局仍會以其記錄狀態/摘要為準覆寫）；
  下游一律以該檔為承接點，不需要等 verify.path 的正式 artifact。
- plan 結論沉澱記憶（kind=task-result）**不會**出現在自動召回；要翻計畫
  結論 → `search_memory(kind='task-result', query='…')`。
- 診斷：app.log 查 `[skill]` 行（compiled/driver/stage 轉場）；session 檔
  `sessions/plan-<id>-<stage>.json` 看 stage 全文；`get_task_result`/`list_tasks`
  （source=plan）看執行現況。

---

## 11. 技能檔案庫（skill-library-local，v0.9.116.0+）

與 §1-9 的 plans（可執行波次計畫）互補：檔案庫存**規範文件包** —
Claude SKILL.md 慣例（一技能一資料夾：SKILL.md frontmatter +
references/ examples/ 附屬檔），根目錄 `QDRANT_SKILLS_DIR`
（預設 `$QDRANT_RAG_HOME/skills`），另寫 `_skill_meta.json` 來源記錄。

| 操作 | 工具 | 要點 |
|---|---|---|
| 匯入 | `skill_import(source, confirm=true)` | 本機資料夾（附屬檔全套複製）/單一檔/URL（github blob→raw 轉換）。名稱取 frontmatter `name`；同名需 `overwrite=true`；library 內來源拒絕；caps：`QDRANT_SKILL_FILE_MAX_BYTES`（2MB/檔）、`QDRANT_SKILL_LIB_MAX_FILES`（64） |
| 目錄 | `skill_list` | 描述/檔案/bytes/mtime/來源；缺 SKILL.md 的資料夾標 invalid |
| 查詢 | `skill_get(name)` / `skill_search(query)` | frontmatter+原文（`QDRANT_SKILL_LIB_BODY_CHARS` 20000 cap）/零依賴關鍵字搜尋（case-insensitive、name×10+desc×5+body hits≤20 評分、snippet） |
| 刪除 | `skill_remove(name, confirm=true)` | 整資料夾 rmtree；confirm 閘；library-root 防護 |
| 引用 | `skill_use(name)` | **一次性載入**：SKILL.md 內文直接 inline 進本回合工具結果 + usage_note（「本次回覆與產出文件遵守此規範」）+ 附屬檔清單 — Claude「/ste 觸發→生效」語意的對應物 |

CLI：`python cli.py skills {list,show,search,add,remove,use}`（add/remove
有 y/N 確認提示，`--yes`/`-y` 覆寫）。chat 觸發詞：「匯入技能」「技能庫」
「查詢技能」「刪除技能」「引用 XX 技能」/ 'import a skill file' 等。
stdio 外部客戶端：`python -m src.mcp_server.skill_library_server`。
一次性用法（不常駐）：聊天說「引用 ste 技能」→ skill_use → 本回合
按規範產文件；把同名指令寫進 run_task_once / stage prompt 即可套
背景工作。

---

*本指南對應更新日誌：v0.9.108.0（通知/併發上限）、v0.9.109.0（編譯器
MVP）、v0.9.109.1（plan 級總結通知接線）、**v0.9.110.0（stage/default_llm 模型 pin + list_task_models 發現面）**、**v0.9.111.0（tools_allow 機制化作用域 + 機械交接檔 out/<stage_id>.json + plan 結論沉澱記憶 + 排程/webhook fire 模型 pin）**、**v0.9.116.0（§11 技能檔案庫 skill-library-local：匯入/查詢/刪除/引用）**。*