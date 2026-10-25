/* Divine Sense Agent - frontend logic (vanilla JS, no build step).
 * pywebview contract: every Api method is callable as
 *   await window.pywebview.api.methodName(arg1, arg2, ...)
 * Promises always resolve; errors come back as {"error": "..."}.
 */
'use strict';

/* v0.9.61.1 — global JS error surface (diagrams v0.9.5.5 lesson):
 * a silent exception inside any click handler used to vanish into
 * the void (e.g. "the MCP editor's edit button does nothing") with
 * zero user-visible feedback. Surface EVERY uncaught error /
 * unhandled rejection as a toast so failures are diagnosable. */
(() => {
  const report = (msg) => {
    try {
      const box = document.getElementById("toast-container");
      if (!box) { console.error(msg); return; }
      const t = document.createElement("div");
      t.className = "toast err";
      t.textContent = "⚠ JS error: " + msg;
      box.appendChild(t);
      setTimeout(() => t.remove(), 8000);
    } catch (e) { console.error(msg); }
  };
  window.addEventListener("error", (ev) => report(
    (ev && ev.message) || "unknown error"));
  window.addEventListener("unhandledrejection", (ev) => report(
    (ev && ev.reason && (ev.reason.message || String(ev.reason)))
      || "unhandled rejection"));
})();

(() => {
  const api = {
    call(method, ...args) {
      if (!window.pywebview || !window.pywebview.api) {
        return Promise.reject(new Error('pywebview API not ready'));
      }
      const fn = window.pywebview.api[method];
      if (typeof fn !== 'function') {
        return Promise.reject(new Error('api.' + method + ' is not a function'));
      }
      try { return Promise.resolve(fn(...args)); }
      catch (e) { return Promise.reject(e); }
    },
    safeCall(method, ...args) {
      return this.call(method, ...args).catch((e) => {
        // v0.8.7+: pywebview injects ``window.pywebview.api``
        // AFTER the page finishes loading. Calls fired during
        // boot (splash readiness probe, refreshStatus poll,
        // session picker) race against that injection and reject
        // with ``pywebview API not ready``. This is an expected
        // boot condition — the very next call after injection
        // succeeds. Silently swallow so the user doesn't see a
        // confusing "pywebview API not ready" toast in the top
        // right on every launch; the periodic setInterval(refreshStatus)
        // and the user-triggered actions will retry successfully.
        const msg = (e && e.message) || String(e);
        if (msg === 'pywebview API not ready') return null;
        toast(t('errGeneric', { msg }), 'error', 6000);
        return null;
      });
    },
  };

  // i18n strings - 4 locales (English placeholders; per-locale
  // translations can be added later without touching the code).
  const I18N = {
    'zh-TW': {
      appTitle: 'Divine Sense Agent',
      // Theme picker (v0.9.97.0+)
      themeLabel: '主題',
      themeAuto: '🌗 自動（跟隨系統）',
      themeLight: '☀️ 淺色',
      themeDark: '🌙 深色',
      // Appearance dialog (v0.9.99.0+)
      appearanceBtn: '🖌️ 外觀設定',
      appearanceTitle: '🎨 外觀設定',
      appearanceBg: '🖼️ 背景圖',
      appearanceBgUrl: '或貼上圖片 URL…',
      appearanceBgDim: '暗化',
      appearanceBgClear: '清除背景圖',
      appearanceBgFit: '佈放格式',
      appearanceBgFitCover: '滿格縮放（cover）',
      appearanceBgFitContain: '完整顯示（contain）',
      appearanceBgFitCenter: '原尺置中（center）',
      appearanceBgFitTile: '重複平鋪（tile）',
      appearanceBgTooBig: '圖片太大，請換較小圖片',
      appearanceBgDropped: '空間不足，背景圖已移除（其餘設定保留）',
      appearanceBgUrlBad:
        '背景圖 URL 無效 — 貼 http(s):// 圖片網址或 uploads/ 沙盒路徑',
      appearanceSaveFailed: '外觀設定儲存失敗（瀏覽器儲存空間不足？）',
      appearanceBindFail: '外觀設定初始化失敗：',
      appearanceApply: '套用',
      appearanceAccent: '🎨 自選色調',
      appearanceAccentReset: '重設',
      appearanceFont: '🔤 字型',
      appearanceFontCustom: '自訂字型 CSS…',
      appearanceFontSystem: '系統預設',
      appearanceFontStd: '標準',
      appearanceFontSmall: '小',
      appearanceFontLarge: '大',
      appearanceFontXL: '特大',
      appearanceMono: '程式碼字型',
      appearanceFontSize: '字級',
      appearanceReset: '全部重設',
      appearanceSaved: '外觀已套用',
      tabChat: '💬 對話', tabIngest: '📥 攝入', tabSearch: '🔍 搜尋',
      tabAdmin: '🛠 系統管理', tabStats: '📊 統計', tabSessions: '🗂 對話紀錄',
      newSession: '＋ 新對話',
      save: '💾 儲存', clear: '🗑 清除', send: '送出 ➤',
      askPlaceholder: '對文件提問…',
      ingestRun: '▶ 攝入', pickFile: '📄 選檔…', pickDir: '📁 選資料夾…',
      ingestRecursive: '遞迴掃描', ingestResult: '結果',
      ingestHint: '提示:選資料夾會遞迴攝入所有支援的檔案 (.pdf .docx .pptx .epub .txt .md .py .json …),重複檔案會用 SHA-1 自動略過。',
      searchPlaceholder: '搜尋向量資料庫…',
      searchFilterPlaceholder: '檔名過濾(可選)',
      search: '🔍 搜尋', refresh: '🔄 重新整理',
      loadData: '📂 載入資料', dataBrowser: '資料管理（分頁瀏覽）',
      pagerPrev: '⬆ 上一頁', pagerNext: '⬇ 下一頁',
      dataPos: '第 {p} 頁 · 本頁顯示 {n} 筆', dataEmpty: '此 collection 沒有資料',
      deletePoint: '刪除此資料點',
      confirmDeletePoint: '確定要刪除此資料點（{id}…）？此操作無法復原。',
      pointDeleted: '已刪除 {n} 筆',
      noDocIndex: '此 collection 沒有 filename 索引，請使用下方「資料管理」分頁瀏覽器。',
      statCollection: 'Collection', statVectors: '向量數',
      statDim: '維度', statDocs: '文件數',
      docList: '已索引文件',
      thTitle: '標題', thMsgs: '訊息數', thUpdated: '更新時間', thActions: '操作',
      // v0.9.103.1 — Sessions-tab multi-select toolbar
      sessSelAll: '全選', sessDeleteSel: '刪除所選（{n}）',
      sessExportSel: '匯出所選（{n}）',
      sessNone: '請先勾選要操作的對話紀錄',
      sessDeleted: '已刪除 {ok} 筆（略過 {skip}）',
      sessExported: '已匯出 {n} 筆對話紀錄',
      // v0.9.103.5 — per-row session title rename
      sessRename: '改標題', sessRenamePrompt: '請輸入新的對話標題：',
      sessRenameEmpty: '標題不可為空白',
      sessRenamed: '已更新標題「{title}」',
      adminTitle: '系統管理',
      adminHint: '檢視 Qdrant collection 與 embedding backend 狀態。危險操作需確認。',
      qdrantInfo: 'Qdrant', embedInfo: 'Embedding',
      dangerZone: '危險操作',
      recreateCollection: '重建 Collection',
      recreateHint: '刪除並重建 collection,套用最新 HNSW 設定。所有向量會被清空,需重新攝入。',
      recreate: '♻ 重建',
      deleteCollection: '刪除 Collection',
      deleteHint: '永久刪除 collection,無法復原。',
      delete: '✖ 刪除',
      qdrant: 'Qdrant', llm: 'LLM',
      ok: '✓ 就緒', err: '✗ 錯誤', warn: '⚠ 警告',
      errGeneric: '錯誤:{msg}',
      confirmRecreate: '確定要重建 collection?所有向量會被刪除!',
      confirmDelete: '確定要刪除 collection?此操作無法復原!',
      // v0.9.59.1 — all-collections inventory + per-row delete
      collectionsZone: 'Collections',
      collectionsHint: '刪除為破壞性操作(無法復原),每列需個別確認。',
      colName: 'Collection', colPoints: '向量數', colDim: '維度',
      colKind: '用途', colActions: '操作',
      kind_rag: 'RAG 文件', kind_log: '日誌分析', kind_stock: '股市快照',
      kind_memory: '長期記憶', kind_other: '其他',
      confirmDeleteCollection: '確定要刪除 collection「{name}」?此操作無法復原!',
      colDeleted: '已刪除 collection「{name}」',
      yesRecreate: '是,重建', yesDelete: '是,刪除', cancel: '取消',
      sourcesLabel: '來源', loading: '載入中…',
      // v0.9.5+ 訊息旁的圖表開啟按鈕
      openInDiagrams: '📊 在圖表中開啟',
      // v0.9.127.5+ — share out (per-bubble 📤) + share-in landing toast
      shareReply: '📤 分享這則回覆',
      sharedIn: '📥 已接收 {n} 個分享檔',
      shareCopied: '已複製回覆到剪貼簿',
      // v0.9.133.0 — bubble style (顏色/透明度)
      styleBtn: '🎨 氣泡顏色／透明度',
      styleAsst: '助理氣泡',
      styleUser: '使用者的訊息',
      styleReset: '恢復預設',
      // v0.9.132.9 — clipboard buttons (input / bubbles / notes editor)
      clipCopyMsg: '📋 複製這則訊息',
      clipCopyInput: '📋 複製輸入框內容',
      clipPasteInput: '📥 貼上（把剪貼簿文字插入輸入框）',
      clipCopySel: '📋 複製所選文字',
      clipCutSel: '✂️ 剪下所選文字',
      clipPasteSel: '📥 貼上到所選位置',
      clipCopied: '已複製到剪貼簿',
      clipPasted: '已貼上',
      clipPasteFail: '無法讀取剪貼簿（未授權或非文字）— 請改用鍵盤貼上',
      clipEmpty: '沒有內容可複製',
      shareFail: '分享失敗：{msg}',
      uploadTimeout: '上傳逾時（連線太慢或中斷）',
      uploadBatch: '批 {i}/{n} · 檔 {d}/{t}',
      uploadCancel: '取消上傳',
      uploadStalled: '⏸ 停滯 {s} 秒',
      uploadFlowing: '⬆ 傳輸中',
      uploadNetErr: '網路錯誤（連線中斷）',
      autoTtsOn: '🔊 自動朗讀：開（回覆落地即播放）',
      autoTtsOff: '🔊 自動朗讀：關',
      voiceLoopOn: '🎧 免持對話：開（聆聽→自動送出→朗讀→再聆聽）',
      voiceLoopOff: '🎧 免持對話：關',
      voiceFallback: '🔊 後端語音暫不可用，改用系統語音',
      voicePermission: '🎤 麥克風權限被拒 — 點網址列左側 🔒 圖示，將「麥克風」設為「允許」並勾選記住後重試',
      voicePermissionIOS: '🎤 iPhone 麥克風被拒 — iOS 設定 → 隱私權與安全性 → 麥克風 → 允許 Safari／本 App（曾被拒需清除網站資料或重裝 App）',
      voicePermissionIOSDenied: '🎤 iPhone 麥克風已被永久拒絕 — ①刪除主畫面本 App ②iOS 設定 → Apps → Safari → 進階 → 網站資料 → 刪除 tchtool.app ③確認 設定 → 隱私權與安全性 → 麥克風 內 Safari 已開 ④用 Safari 重開本站並在首次詢問按「允許」',
      voiceResumePlay: '播放回覆',
      voiceResumeListen: '開始說話',
      voiceIOSAutoplayHint: '🔊 iOS 不允許自動播放 — 每則回覆請點氣泡旁的 🔊 播放（或開啟 🎧 免持，用點按方式聽與說）',
      voiceStop: '⏹ 停止朗讀',
      voiceResume: '點一下繼續聆聽',
      // v0.9.65.0+ — chat 氣泡內沙箱檔案的 ⬇ 下載 chip
      downloadFile: '下載檔案', fileSaved: '檔案已存到 {dest}',
      // v0.9.133.7 — Sessions 表的即時摘要欄
      thSummary: '摘要',
      // v0.9.133.6 — LLM 產出 .html 的應用內預覽（iPhone Web App 安全）
      htmlPreview: '預覽', htmlPreviewClose: '關閉預覽',
      // v0.9.68.0+ — chat 語音雙向（🎤 錄音輸入 / 🔊 朗讀回覆）
      voiceMic: '🎤 語音輸入（點擊開始/停止）',
      voiceRecording: '🔴 錄音中…（點擊停止）',
      voiceTranscribing: '🎙 語音辨識中…',
      voiceSpeak: '🔊 朗讀回覆',
      voiceFail: '語音功能失敗:{msg}',
      voiceMicErr: '無法啟動麥克風:{msg}',
      voiceNoSupport: '此環境不支援麥克風錄音',
      // v0.9.100.0 — 快速片語（⚡ 使用者自訂片語/命令，一鍵插入輸入框）
      snipsBtn: '⚡',
      snipsAdd: '➕ 新增',
      snipsPlaceholder: '多行片語（Enter 換行、Ctrl+Enter 加入）…',
      turnExecRun: '⚙️ 執行中…',
      turnExecDone: '✅ 完成（{n} 事件）',
      turnExecError: '⚠️ 未能完成',
      turnExecStopped: '🛑 已中止',
      turnAbort: '🛑 中止',
      turnAbortSent: '已送出中止請求…',
      turnAbortPending: '已送出中止——後台回合仍在收尾（長 LLM 呼叫可能需 1–3 分鐘），可先送新訊息',
      turnBgDetached: '已自動轉為背景任務（超過 60 秒）— 可先送新訊息，回覆完成後照常落在這裡',
      turnGone: '與伺服器連線中斷（可能已重啟）— 此回合狀態不明',
      turnLogShow: '查看執行 log',
      snipsCount: '{n} 個片語',
      snipsDup: '此片語已存在',
      snipsMax: '片語數已達上限（50）',
      snipsEmpty: '尚無片語 — 按「顯示新增列」建立；所有裝置共用同一份片語。',
      snipsEmpty: '尚無片語 — 在上方輸入後按 ➕ 新增，點片語即可插入輸入框。',
      snipsHideAdd: '▲ 隱藏新增列',
      snipsShowAdd: '顯示新增列',
      // v0.9.9.4+ 每則對話方塊上方的開始 / 結束時間
      chatStart: '開始', chatEnd: '結束',
      chatTools: '🧰 工具', chatToolsAll: '全開',
      chatToolsNone: '全關', chatToolsReset: '重設',
      // v0.9.47.0+ 平行多功：背景任務面板 + 背景執行勾選
      chatTasks: '⏳ 任務',
      // v0.9.133.10 — 任務行內 ⚙ 執行紀錄展開
      taskLogShow: '執行紀錄',
      taskLogNoteIngest: '純匯入任務沒有執行紀錄 — 完成/失敗會自動通知',
      taskLogNoteNone: '此任務沒有執行紀錄可顯示', taskRefresh: '重新整理',
      taskNone: '（沒有背景任務）', taskRunning: '執行中',
      taskDone: '完成', taskFailed: '失敗',
      bgRun: '背景執行',
      taskSubmitted:
        '已送出背景任務 {id}（session: {sid}）— 進度用「⏳ 任務」面板查看',
      bgResultBack: '⏳ 背景任務 {id} 完成 — 結果已寫回這個對話',
      // v0.9.130.1+ local /playpet + /endpet chat commands
      petComeOut: '🐶 小狗出來玩了！輸入 /endpet 讓牠回去休息',
      petAlreadyOut: '🐶 小狗已經在玩了',
      petGoesRest: '🐾 小狗回去休息了。輸入 /playpet 再叫牠出來',
      petNotOut: '小狗還沒出來。輸入 /playpet 叫牠出來',
      // v0.9.16.0+ web-mode client upload（GUI 模式隱藏）
      uploadMark: '[已上傳]', uploadOk: '已上傳 {ok} 個檔案',
      uploading: '⏳ 上傳中… {f}',
      uploadSkip: '（略過 {skip} 個）', uploadFail: '上傳失敗：{msg}',
      // v0.9.39.0+ — credentials tab
      tabCreds: '🔑 帳號', credsTitle: '🔑 帳號集管理',
      credsHint: '所有需要帳號的服務共用這組帳號集（目前：ONVIF 攝影機）。密碼建議用環境變數參照（本店不存密碼）；明文密碼僅 base64 遮蔽保存，非加密。可透過此介面、MCP（cred_add / cred_list / …）與 CLI（python cli.py creds …）管理。',
      credsUser: '帳號', credsSource: '來源', credsService: '服務',
      credsNotes: '備註', credsAddTitle: '新增 / 覆蓋帳號集',
      credsOverwrite: '覆蓋同名', credsSave: '💾 儲存',
      credsDelete: '🗑 刪除', credsSaved: '帳號集已儲存',
      credsDeleted: '帳號集已刪除', credsConfirmDelete: '確定刪除帳號集',
      // v0.8.7+ 啟動動畫
      splashTitle: 'Divine Sense Agent',
      splashChecking: '正在檢查資源…',
      splashReady: '準備好了',
      splashDone: '完成 {n} / 3',
      splashSkipped: '已略過',
      splashSkip: '略過 →',
      readinessBackend: '後端 API',
      readinessQdrant: 'Qdrant 連線',
      readinessLLM: 'LLM API 金鑰',
      readinessMcp: 'MCP 工具初始化',
      readinessTimeout: '逾時',
      docsError: '無法列出文件: {msg}',
      removeOneTitle: '從索引中移除這個檔案',
      removeSelected: '🗑 移除選取 (0)',
      confirmRemoveOne: '從 Qdrant 索引中永久移除 "{name}"?此操作無法復原。',
      confirmRemoveMany: '從 Qdrant 索引中永久移除 {n} 個檔案?此操作無法復原。',
      removedToast: '已移除 {files} 個檔案,共 {chunks} 個 chunks。',
      removedMissing: ' ({n} 個檔案不在索引中,可能是打錯字)',
      cacheZone: 'Embedding 快取',
      clearCacheTitle: '清除 embedding 快取',
      clearCacheHint: '會清掉記憶體 LRU + 永久 SQLite 快取。下次需要時會重新呼叫 embedding API。',
      clearCache: '🧹 清除',
      confirmClearCache: '確定要清除所有 embedding 快取?',
      cacheCleared: '已清除 {lru} 個 LRU + {disk} 個磁碟快取',
      auditBackends: '🔎 審計 hash 向量',
      auditBackendsHint: '審計為唯讀；重嵌會對被標記的 points 重新呼叫 embedding 後端（需確認）。',
      reembedHash: '♻️ 重嵌 hash',
      backfillHeadings: '§ 回填標題',
      backfillDone: '回填完成：{n} / {scanned} 點已補 heading_path（錯誤 {errors}）',
      auditDone: '審計完成：掃描 {scanned} 點、{hash} 點為 hash 向量',
      reembedDone: '重嵌完成：{n} / {hash} 已修復（仍是 hash: {still}）',
      confirmReembed: '對所有 hash 向量重新嵌入（費用/時間視後端而定），確定?',
      reloadZone: '設定熱重載',
      reloadTitle: '重新載入 .env / mcp.json',
      reloadHint: '重讀 .env（含型別轉換）、重置 LLM provider 鏈、按啟用條件重啟常駐服務（排程 / webhook / Telegram / ONVIF 監看）並重連 MCP 設備。刪除環境變數仍需重啟（dotenv 不會清除 process env）。',
      reloadBtn: '🔄 重新載入',
      reloadDone: '已重載。變更: {changed}；服務: {svc}',
      // MCP 設備編輯器 (v0.8.0+)
      mcpEditorTitle: '🔌 MCP 設備(第三方 servers)',
      mcpEditorHint: '編輯目前 config/mcp.json 的設備清單。儲存會立即熱重載 Manager — 下一次對話就會用新工具,不必重啟。本地 Qdrant + 網搜主機永遠開啟,不可在此編輯。',
      mcpRefresh: '🔄 重新整理',
      mcpAdd: '+ 新增 server',
      mcpSaveAll: '💾 儲存並重連',
      mcpLoading: '讀取中…',
      mcpServerCount: '已設定 {n} 個遠端 server',
      mcpInvalid: '設定無效',
      mcpValid: '設定有效',
      mcpRestart: '重連',
      mcpRestartHint: '關閉後重新連線此 server',
      mcpEditTitle: '編輯 MCP server',
      mcpFieldName: '名稱',
      mcpFieldNamePh: 'my-server',
      mcpFieldNameHint: '作為工具前綴,在設備清單中必須唯一。',
      mcpFieldTransport: '傳輸方式',
      mcpFieldCommand: '啟動指令',
      mcpFieldCommandPh: 'python -m my_mcp_server',
      mcpFieldCommandHint: '以空白切開存成 argv 陣列。',
      mcpFieldEnv: '環境變數(KEY=value,每行一組)',
      mcpFieldEnvPh: 'MY_TOKEN=',
      mcpFieldCwd: '工作目錄(選填)',
      mcpFieldCwdPh: '/path/to/server',
      mcpFieldUrl: '端點 URL',
      mcpFieldUrlPh: 'https://mcp.example.com/v1/mcp',
      mcpFieldHeaders: 'HTTP 標頭(KEY=value,每行一組)',
      mcpFieldHeadersPh: 'Authorization=Bearer ${MY_TOKEN}',
      mcpFieldHeadersHint: '支援 ${ENV_VAR} 在連線時替換。',
      mcpFieldNameRequired: '請輸入名稱',
      mcpFieldCommandRequired: '請輸入啟動指令',
      mcpFieldUrlRequired: '請輸入 URL',
      mcpFieldNameDuplicate: '已有名為 {name} 的 server',
      mcpSaved: '已儲存 {n} 個 server,總工具數 {tools}',
      mcpRestarted: '{name} 重連完成 (工具 {tools})',
      mcpParseError: '設定檔解析失敗:{msg}',
      confirmRemoveMcp: '刪除 MCP server 「{name}」 ?',
      // MCP editor 增強 (v0.8.2+)
      mcpToggleRaw: '📝 Raw JSON',
      mcpOverviewToggle: '📋 Tool 總覽',
      mcpTest: '🧪 測試',
      mcpTestHint: '查詢連線狀態與此 server 提供的工具清單',
      mcpTestTitle: '測試 {name}',
      mcpTestCount: '共 {n} 個工具',
      mcpRawTitle: '📝 直接編輯 config/mcp.json (raw JSON)',
      mcpRawHint: '整個檔案一次編輯,儲存時做完整 validation + atomic write。適合批次編輯、貼 JSON、或手動改 _comment 範例。',
      mcpRawFormat: '🎨 格式化',
      mcpRawCancel: '取消',
      mcpRawSave: '💾 儲存 raw JSON',
      mcpOverviewTitle: '📋 擴充 MCP 提供哪些功能 (Tool 總覽)',
      mcpOverviewHint: '目前已連線 / 已註冊的全部 MCP tools,依 server 分組。含本地 Qdrant + Web Search hosts,以及 config/mcp.json 中所有啟用的遠端 servers。LLM 下一次對話會從這裡挑選要呼叫的工具。',
      mcpOverviewLoading: '載入中…',
      mcpOverviewEmpty: '目前沒有任何 tool — 試著新增一個 server 或重啟 QdrantLocalHost。',
      mcpOverviewServerFailed: '此 server 連線失敗,沒有可用的 tool。',
      // Notes 筆記本 (v0.9.95.0+)
      tabNotes: '📝 筆記本',
      notesTitle: '📝 筆記本',
      notesHint: '本地筆記本（markdown）— 新增/刪除/修改/搜尋（日期・標題・內容）/分類。清單逐頁非同步載入，不整表傾倒。',
      notesSearchPh: '搜尋標題／內容…',
      notesAllCats: '全部分類',
      notesSearch: '🔍 搜尋',
      notesNew: '➕ 新增',
      notesEmpty: '（尚無筆記 — 按「新增」開始）',
      notesPageOf: '第 {p} / {pages} 頁 · 共 {total} 筆',
      notesTitlePh: '標題…',
      notesCatPh: '分類（可空）',
      notesBodyPh: 'markdown 內容…（支援 GFM：粗體、清單、表格、程式碼）',
      notesPreview: '👁 預覽',
      notesEdit: '✏️ 編輯',
      notesSave: '💾 儲存',
      notesDelete: '🗑 刪除',
      notesCancel: '✖ 關閉',
      notesSaved: '已儲存',
      notesSyncBtn: '🔄 同步離線編輯（{n}）',
      notesSynced: '已同步 {n} 筆離線編輯（{f} 筆待重試）',
      notesOfflineBanner: '📡 離線模式 — 顯示 localStorage 暫存的列表',
      notesOfflineStash: '離線暫存成功——上線後自動同步',
      notesStillOffline: '仍在離線——離線編輯保留在暫存中，稍後再試',
      notesUpToDate: '沒有待同步的離線編輯',
      notesLoadOffline2: '離線且沒有暫存 — 上線後會自動載入',
      notesHw: '✍️ 手寫',
      hwTitle: '✍️ 手寫記事',
      hwHint: '用手指／觸控筆／滑鼠在下方書寫',
      hwClear: '🧹 清除',
      hwSave: '💾 儲存並 OCR',
      hwOcring: '傳至主機 OCR 中…（存檔→轉寫→建索引）',
      hwDone: '手寫已歸檔並建索引（{n} 塊）',
      hwEmpty: '畫面是空白的 — 先寫點內容',
      hwOffline: '離線狀態無法手寫建檔（需送往主機 OCR），請先恢復連線',
      hwPageOf: '第 {p} / {n} 頁',
      hwPageAdd: '➕ 加頁',
      hwFull: '⛶ 全螢幕',
      hwBg: '🖼 背景圖',
      hwBgClear: '✕ 去除背景',
      hwBgApplied: '已套用背景圖（畫面已重置）',
      hwBgCleared: '已去除背景圖',
      hwViewOriginal: '手寫原文',
      hwPdf: '📄 匯入 PDF',
      hwPdfImporting: '⏳ PDF 頁面產生中… {f}',
      hwPdfDone: '已匯入 {n} 頁 PDF 底稿',
      hwPdfFail: 'PDF 匯入失敗：{m}',
      hwPen: '🖊 筆', hwMarker: '🖍 螢光', hwEraser: '🧽 擦',
      notesDeleted: '已刪除',
      notesConfirmDelete: '確定要刪除這則筆記？（不可復原）',
      notesTextPh: '文字',
      notesExpandAll: '▾ 全部展開',
      notesCollapseAll: '▸ 全部收合',
      notesListShow: '📋 顯示清單',
      notesListHide: '📋 隱藏清單',
      notesSbChars: '字',
      notesSbLines: '行',
      notesSbSaved: '已自動存於瀏覽器',
      notesSbSavedAt: '已儲存於 {t}',
      // Schedules (v0.8.0+)
      tabSchedules: '⏰ 排程',
      schedulesTitle: '⏰ 排程',
      schedulesHint: '在 cron / interval / 一次性時間點執行預先定義的 prompt。每次觸發會走完整工具鏈 (web_search / mcp_server / ...),結果會寫到專屬 session。daemon 在 Agent 內常駐,開此 tab 可看即時狀態。',
      schedulesAdd: '+ 新增排程',
      schedulesEnabledOnly: '只看啟用',
      schedulesLoading: '載入中…',
      schedulesStatusText: '共 {n} 個排程 ({enabled} 啟用) · daemon {daemon}',
      schedulesRunNow: '立即觸發',
      schedulesEnable: '啟用',
      schedulesDisable: '停用',
      scheduleEditTitle: '編輯排程',
      scheduleFieldName: '名稱',
      scheduleFieldPrompt: 'Prompt',
      scheduleFieldPromptHint: '會送進 Agent.chat_turn,完整工具 (web_search / MCP) 都可用。',
      scheduleFieldType: '排程類型',
      scheduleFieldInterval: '間隔(秒)',
      scheduleFieldIntervalHint: '60 = 每分鐘,3600 = 每小時,86400 = 每天。',
      scheduleFieldCron: 'Cron 表達式',
      scheduleFieldCronHint: '分 時 日 月 週 (0-6,週日=0)。例: <code>0 9 * * *</code> 每天 9 點,<code>*/15 * * * *</code> 每 15 分鐘,<code>0 0 * * 1</code> 每週一午夜。',
      scheduleFieldOnceAt: '執行時間 (ISO datetime)',
      scheduleFieldUseTools: '使用 MCP 工具',
      scheduleFieldEnabled: '啟用',
      scheduleFieldNameRequired: '請輸入名稱',
      scheduleFieldPromptRequired: '請輸入 prompt',
      scheduleFieldIntervalRequired: '請輸入正整數秒數',
      scheduleFieldCronRequired: '請輸入 cron 表達式',
      scheduleFieldOnceAtRequired: '請輸入 ISO datetime',
      scheduleSaved: '已儲存排程 {id}',
      scheduleToggled: '排程 {id} 已{state}',
      scheduleDeleted: '已刪除排程 {id}',
      scheduleFired: '排程 {id} 已觸發,session={session}',
      confirmDeleteSchedule: '刪除排程 「{id}」? 歷史紀錄也會一併清除。',
    },
    'zh-CN': {
      appTitle: 'Divine Sense Agent',
      // Theme picker (v0.9.97.0+)
      themeLabel: '主题',
      themeAuto: '🌗 自动（跟随系统）',
      themeLight: '☀️ 浅色',
      themeDark: '🌙 深色',
      // Appearance dialog (v0.9.99.0+)
      appearanceBtn: '🖌️ 外观设置',
      appearanceTitle: '🎨 外观设置',
      appearanceBg: '🖼️ 背景图',
      appearanceBgUrl: '或贴上图片 URL…',
      appearanceBgDim: '暗化',
      appearanceBgClear: '清除背景图',
      appearanceBgFit: '佈放格式',
      appearanceBgFitCover: '滿格縮放（cover）',
      appearanceBgFitContain: '完整顯示（contain）',
      appearanceBgFitCenter: '原尺置中（center）',
      appearanceBgFitTile: '重複平鋪（tile）',
      appearanceBgTooBig: '图片太大，请换较小图片',
      appearanceBgDropped: '空间不足，背景图已移除（其余设定保留）',
      appearanceBgUrlBad:
        '背景图 URL 无效 — 贴 http(s):// 图片网址或 uploads/ 沙盒路径',
      appearanceSaveFailed: '外观设置保存失败（浏览器存储空间不足？）',
      appearanceBindFail: '外观设置初始化失败：',
      appearanceApply: '套用',
      appearanceAccent: '🎨 自选色调',
      appearanceAccentReset: '重设',
      appearanceFont: '🔤 字型',
      appearanceFontCustom: '自订字型 CSS…',
      appearanceFontSystem: '系统预设',
      appearanceFontStd: '标准',
      appearanceFontSmall: '小',
      appearanceFontLarge: '大',
      appearanceFontXL: '特大',
      appearanceMono: '程式码字型',
      appearanceFontSize: '字级',
      appearanceReset: '全部重设',
      appearanceSaved: '外观已套用',
      tabChat: '💬 对话', tabIngest: '📥 摄取', tabSearch: '🔍 搜索',
      tabAdmin: '🛠 系统管理', tabStats: '📊 统计', tabSessions: '🗂 会话记录',
      newSession: '＋ 新会话',
      save: '💾 保存', clear: '🗑 清除', send: '发送 ➤',
      askPlaceholder: '对文件提问…',
      ingestRun: '▶ 摄取', pickFile: '📄 选文件…', pickDir: '📁 选文件夹…',
      ingestRecursive: '递归扫描', ingestResult: '结果',
      ingestHint: '提示:选文件夹会递归摄取所有支持的文件 (.pdf .docx .pptx .epub .txt .md .py .json …),重复文件会用 SHA-1 自动跳过。',
      searchPlaceholder: '搜索向量数据库…',
      searchFilterPlaceholder: '文件名过滤(可选)',
      search: '🔍 搜索', refresh: '🔄 刷新',
      loadData: '📂 载入数据', dataBrowser: '数据管理（分页浏览）',
      pagerPrev: '⬆ 上一页', pagerNext: '⬇ 下一页',
      dataPos: '第 {p} 页 · 本页显示 {n} 条', dataEmpty: '此 collection 没有数据',
      deletePoint: '删除此数据点',
      confirmDeletePoint: '确定要删除此数据点（{id}…）？此操作无法恢复。',
      pointDeleted: '已删除 {n} 条',
      noDocIndex: '此 collection 没有 filename 索引，请使用下方「数据管理」分页浏览器。',
      statCollection: 'Collection', statVectors: '向量数',
      statDim: '维度', statDocs: '文件数',
      docList: '已索引文件',
      thTitle: '标题', thMsgs: '消息数', thUpdated: '更新时间', thActions: '操作',
      // v0.9.103.1 — Sessions-tab multi-select toolbar
      sessSelAll: '全选', sessDeleteSel: '删除所选（{n}）',
      sessExportSel: '导出所选（{n}）',
      sessNone: '请先勾选要操作的会话记录',
      sessDeleted: '已删除 {ok} 条（跳过 {skip}）',
      sessExported: '已导出 {n} 条会话记录',
      // v0.9.103.5 — per-row session title rename
      sessRename: '改标题', sessRenamePrompt: '请输入新的会话标题：',
      sessRenameEmpty: '标题不可为空白',
      sessRenamed: '已更新标题「{title}」',
      adminTitle: '系統管理',
      adminHint: '查看 Qdrant collection 与 embedding backend 状态。危险操作需确认。',
      qdrantInfo: 'Qdrant', embedInfo: 'Embedding',
      dangerZone: '危险操作',
      recreateCollection: '重建 Collection',
      recreateHint: '删除并重建 collection,套用最新 HNSW 设置。所有向量将被清空,需重新摄取。',
      recreate: '♻ 重建',
      deleteCollection: '删除 Collection',
      deleteHint: '永久删除 collection,无法恢复。',
      delete: '✖ 删除',
      qdrant: 'Qdrant', llm: 'LLM',
      ok: '✓ 就绪', err: '✗ 错误', warn: '⚠ 警告',
      errGeneric: '错误:{msg}',
      confirmRecreate: '确定要重建 collection?所有向量将被删除!',
      confirmDelete: '确定要删除 collection?此操作无法恢复!',
      // v0.9.59.1 — all-collections inventory + per-row delete
      collectionsZone: 'Collections',
      collectionsHint: '删除为破坏性操作(无法恢复),每列需个别确认。',
      colName: 'Collection', colPoints: '向量数', colDim: '维度',
      colKind: '用途', colActions: '操作',
      kind_rag: 'RAG 文档', kind_log: '日志分析', kind_stock: '股市快照',
      kind_memory: '长期记忆', kind_other: '其他',
      confirmDeleteCollection: '确定要删除 collection「{name}」?此操作无法恢复!',
      colDeleted: '已删除 collection「{name}」',
      yesRecreate: '是,重建', yesDelete: '是,删除', cancel: '取消',
      sourcesLabel: '来源', loading: '加载中…',
      // v0.9.5+ 消息旁的图表打开按钮
      openInDiagrams: '📊 在图表中打开',
      // v0.9.127.5+ — share out (per-bubble 📤) + share-in landing toast
      shareReply: '📤 分享该回复',
      sharedIn: '📥 已接收 {n} 个分享文件',
      shareCopied: '已复制回复到剪贴板',
      // v0.9.133.0 — bubble style (顏色/透明度)
      styleBtn: '🎨 气泡颜色／透明度',
      styleAsst: '助理气泡',
      styleUser: '用户消息',
      styleReset: '恢复默认',
      // v0.9.132.9 — clipboard buttons (input / bubbles / notes editor)
      clipCopyMsg: '📋 复制这条消息',
      clipCopyInput: '📋 复制输入框内容',
      clipPasteInput: '📥 粘贴（插入剪贴板文字）',
      clipCopySel: '📋 复制所选文字',
      clipCutSel: '✂️ 剪切所选文字',
      clipPasteSel: '📥 粘贴到所选位置',
      clipCopied: '已复制到剪贴板',
      clipPasted: '已粘贴',
      clipPasteFail: '无法读取剪贴板（未授权或非文字）— 请改用键盘粘贴',
      clipEmpty: '没有内容可复制',
      shareFail: '分享失败：{msg}',
      uploadTimeout: '上传超时（连接太慢或中断）',
      uploadBatch: '批次 {i}/{n} · 文件 {d}/{t}',
      uploadCancel: '取消上传',
      uploadStalled: '⏸ 停滞 {s} 秒',
      uploadFlowing: '⬆ 传输中',
      uploadNetErr: '网络错误（连接中断）',
      autoTtsOn: '🔊 自动朗读：开（回复落地即播放）',
      autoTtsOff: '🔊 自动朗读：关',
      voiceLoopOn: '🎧 免提对话：开（聆听→自动发送→朗读→再聆听）',
      voiceLoopOff: '🎧 免提对话：关',
      voiceFallback: '🔊 后端语音暂不可用，改用系统语音',
      voicePermission: '🎤 麦克风权限被拒 — 点地址栏左侧 🔒 图标，将「麦克风」设为「允许」并勾选记住后重试',
      voicePermissionIOS: '🎤 iPhone 麦克风被拒 — iOS 设置 → 隐私权与安全性 → 麦克风 → 允许 Safari／本 App（曾被拒需清除网站数据或重装 App）',
      voicePermissionIOSDenied: '🎤 iPhone 麦克风已被永久拒绝 — ①删除主画面本 App ②iOS 设置 → Apps → Safari → 高级 → 网站数据 → 删除 tchtool.app ③确认 设置 → 隐私权与安全性 → 麦克风 内 Safari 已开 ④用 Safari 重开本站并在首次询问按「允许」',
      voiceResumePlay: '播放回复',
      voiceResumeListen: '开始说话',
      voiceIOSAutoplayHint: '🔊 iOS 不允许自动播放 — 每则回复请点气泡旁的 🔊 播放（或开启 🎧 免提，用点按方式听与说）',
      voiceStop: '⏹ 停止朗读',
      voiceResume: '点一下继续聆听',
      // v0.9.65.0+ — chat 气泡内沙箱文件的 ⬇ 下载 chip
      downloadFile: '下载文件', fileSaved: '文件已存到 {dest}',
      // v0.9.133.7 — Sessions 表的即时摘要列
      thSummary: '摘要',
      // v0.9.133.6 — LLM 产出 .html 的应用内预览（iPhone Web App 安全）
      htmlPreview: '预览', htmlPreviewClose: '关闭预览',
      // v0.9.68.0+ — chat 語音雙向（🎤 錄音輸入 / 🔊 朗讀回覆）
      voiceMic: '🎤 语音输入（点击开始/停止）',
      voiceRecording: '🔴 录音中…（点击停止）',
      voiceTranscribing: '🎙 语音识别中…',
      voiceSpeak: '🔊 朗读回复',
      voiceFail: '语音功能失败:{msg}',
      voiceMicErr: '无法启动麦克风:{msg}',
      voiceNoSupport: '此环境不支持麦克风录音',
      // v0.9.100.0 — 快速片语（⚡ 用户自定义片语/命令，一键插入输入框）
      snipsBtn: '⚡',
      snipsAdd: '➕ 新增',
      snipsPlaceholder: '多行片语（Enter 换行、Ctrl+Enter 加入）…',
      turnExecRun: '⚙️ 执行中…',
      turnExecDone: '✅ 完成（{n} 个事件）',
      turnExecError: '⚠️ 未能完成',
      turnExecStopped: '🛑 已中止',
      turnAbort: '🛑 中止',
      turnAbortSent: '已送出中止请求…',
      turnAbortPending: '已送出中止——后台回合仍在收尾（长 LLM 呼叫可能需 1–3 分钟），可先送新消息',
      turnBgDetached: '已自动转为后台任务（超过 60 秒）— 可先发送新消息，回复完成后照常落在这里',
      turnGone: '与服务器连线中断（可能已重启）— 此回合状态不明',
      turnLogShow: '查看执行 log',
      snipsCount: '{n} 个片语',
      snipsDup: '此片语已存在',
      snipsMax: '片语数已达上限（50）',
      snipsEmpty: '尚无片语 — 按「显示新增列」建立；所有装置共用同一份片语。',
      snipsEmpty: '尚无片语 — 按「显示新增列」建立；所有装置共用同一份片语。',
      snipsHideAdd: '▲ 隐藏新增列',
      snipsShowAdd: '显示新增列',
      // v0.9.9.4+ 每则对话气泡上方的开始 / 结束时间
      chatStart: '开始', chatEnd: '结束',
      chatTools: '🧰 工具', chatToolsAll: '全开',
      chatToolsNone: '全关', chatToolsReset: '重置',
      // v0.9.47.0+ 平行多功：后台任务面板 + 后台执行勾选
      chatTasks: '⏳ 任务',
      // v0.9.133.10 — 任务行 ⚙ 执行纪录展开
      taskLogShow: '执行纪录',
      taskLogNoteIngest: '纯汇入任务没有执行纪录 — 完成/失败会自动通知',
      taskLogNoteNone: '此任务没有执行纪录可显示', taskRefresh: '刷新',
      taskNone: '（没有后台任务）', taskRunning: '执行中',
      taskDone: '完成', taskFailed: '失败',
      bgRun: '后台执行',
      taskSubmitted:
        '已提交后台任务 {id}（session: {sid}）— 进度用「⏳ 任务」面板查看',
      bgResultBack: '⏳ 后台任务 {id} 完成 — 结果已写回这个对话',
      // v0.9.130.1+ local /playpet + /endpet chat commands
      petComeOut: '🐶 小狗出来玩了！输入 /endpet 让它回去休息',
      petAlreadyOut: '🐶 小狗已经在玩了',
      petGoesRest: '🐾 小狗回去休息了。输入 /playpet 再叫它出来',
      petNotOut: '小狗还没出来。输入 /playpet 叫它出来',
      // v0.9.16.0+ web-mode client upload（GUI 模式隐藏）
      uploadMark: '[已上传]', uploadOk: '已上传 {ok} 个文件',
      uploading: '⏳ 上传中… {f}',
      uploadSkip: '（跳过 {skip} 个）', uploadFail: '上传失败：{msg}',
      // v0.9.39.0+ — credentials tab
      tabCreds: '🔑 账号', credsTitle: '🔑 账号集管理',
      credsHint: '所有需要账号的服务共用这组账号集（目前：ONVIF 摄影机）。密码建议用环境变量参照（本店不存密码）；明文密码仅 base64 遮蔽保存，非加密。可通过此界面、MCP（cred_add / cred_list / …）与 CLI（python cli.py creds …）管理。',
      credsUser: '账号', credsSource: '来源', credsService: '服务',
      credsNotes: '备注', credsAddTitle: '新增 / 覆盖账号集',
      credsOverwrite: '覆盖同名', credsSave: '💾 保存',
      credsDelete: '🗑 删除', credsSaved: '账号集已保存',
      credsDeleted: '账号集已删除', credsConfirmDelete: '确定删除账号集',
      // v0.8.7+ 启动动画
      splashTitle: 'Divine Sense Agent',
      splashChecking: '正在检查资源…',
      splashReady: '准备好了',
      splashDone: '完成 {n} / 3',
      splashSkipped: '已略过',
      splashSkip: '略过 →',
      readinessBackend: '后端 API',
      readinessQdrant: 'Qdrant 连接',
      readinessLLM: 'LLM API 密钥',
      readinessMcp: 'MCP 工具初始化',
      readinessTimeout: '逾时',
      docsError: '无法列出文件: {msg}',
      removeOneTitle: '从索引中移除这个文件',
      removeSelected: '🗑 移除选中 (0)',
      confirmRemoveOne: '从 Qdrant 索引中永久移除 "{name}"?此操作无法恢复。',
      confirmRemoveMany: '从 Qdrant 索引中永久移除 {n} 个文件?此操作无法恢复。',
      removedToast: '已移除 {files} 个文件,共 {chunks} 个 chunks。',
      removedMissing: ' ({n} 个文件不在索引中,可能是打错字)',
      cacheZone: 'Embedding 缓存',
      clearCacheTitle: '清除 embedding 缓存',
      clearCacheHint: '会清掉内存 LRU + 永久 SQLite 缓存。下次需要时会重新调用 embedding API。',
      clearCache: '🧹 清除',
      confirmClearCache: '确定要清除所有 embedding 缓存?',
      cacheCleared: '已清除 {lru} 个 LRU + {disk} 个磁盘缓存',
      auditBackends: '🔎 审计 hash 向量',
      auditBackendsHint: '审计为只读；重嵌会对被标记的 points 重新调用 embedding 后端（需确认）。',
      reembedHash: '♻️ 重嵌 hash',
      backfillHeadings: '§ 回填标题',
      backfillDone: '回填完成：{n} / {scanned} 点已补 heading_path（错误 {errors}）',
      auditDone: '审计完成：扫描 {scanned} 点、{hash} 点为 hash 向量',
      reembedDone: '重嵌完成：{n} / {hash} 已修复（仍是 hash: {still}）',
      confirmReembed: '对所有 hash 向量重新嵌入（费用/时间视后端而定），确定?',
      reloadZone: '配置热重载',
      reloadTitle: '重新载入 .env / mcp.json',
      reloadHint: '重读 .env（含类型转换）、重置 LLM provider 链、按启用条件重启常驻服务（排程 / webhook / Telegram / ONVIF 监看）并重连 MCP fleet。删除环境变量仍需重启（dotenv 不会清除 process env）。',
      reloadBtn: '🔄 重新载入',
      reloadDone: '已重载。变更: {changed}；服务: {svc}',
      // MCP fleet editor (v0.8.0+)
      mcpEditorTitle: '🔌 MCP 设备(第三方 servers)',
      mcpEditorHint: '编辑当前 config/mcp.json 的设备清单。保存会立即热重载 Manager — 下一次对话就会用新工具,不必重启。本地 Qdrant + 网搜主机永远开启,不可在此编辑。',
      mcpRefresh: '🔄 刷新',
      mcpAdd: '+ 新增 server',
      mcpSaveAll: '💾 保存并重连',
      mcpLoading: '读取中…',
      mcpServerCount: '已配置 {n} 个远端 server',
      mcpInvalid: '配置无效',
      mcpValid: '配置有效',
      mcpRestart: '重连',
      mcpRestartHint: '关闭后重新连接此 server',
      mcpEditTitle: '编辑 MCP server',
      mcpFieldName: '名称',
      mcpFieldNamePh: 'my-server',
      mcpFieldNameHint: '作为工具前缀,在设备清单中必须唯一。',
      mcpFieldTransport: '传输方式',
      mcpFieldCommand: '启动命令',
      mcpFieldCommandPh: 'python -m my_mcp_server',
      mcpFieldCommandHint: '以空白拆开存成 argv 数组。',
      mcpFieldEnv: '环境变量(KEY=value,每行一组)',
      mcpFieldEnvPh: 'MY_TOKEN=',
      mcpFieldCwd: '工作目录(选填)',
      mcpFieldCwdPh: '/path/to/server',
      mcpFieldUrl: '端点 URL',
      mcpFieldUrlPh: 'https://mcp.example.com/v1/mcp',
      mcpFieldHeaders: 'HTTP 头(KEY=value,每行一组)',
      mcpFieldHeadersPh: 'Authorization=Bearer ${MY_TOKEN}',
      mcpFieldHeadersHint: '支持 ${ENV_VAR} 在连接时替换。',
      mcpFieldNameRequired: '请输入名称',
      mcpFieldCommandRequired: '请输入启动命令',
      mcpFieldUrlRequired: '请输入 URL',
      mcpFieldNameDuplicate: '已存在名为 {name} 的 server',
      mcpSaved: '已保存 {n} 个 server,总工具数 {tools}',
      mcpRestarted: '{name} 重连完成 (工具 {tools})',
      mcpParseError: '配置文件解析失败:{msg}',
      confirmRemoveMcp: '删除 MCP server "{name}" ?',
      // MCP editor 增强 (v0.8.2+)
      mcpToggleRaw: '📝 Raw JSON',
      mcpOverviewToggle: '📋 Tool 总览',
      mcpTest: '🧪 测试',
      mcpTestHint: '查询连接状态与此 server 提供的工具清单',
      mcpTestTitle: '测试 {name}',
      mcpTestCount: '共 {n} 个工具',
      mcpRawTitle: '📝 直接编辑 config/mcp.json (raw JSON)',
      mcpRawHint: '整个档案一次编辑,储存时做完整 validation + atomic write。适合批次编辑、贴 JSON、或手动改 _comment 范例。',
      mcpRawFormat: '🎨 格式化',
      mcpRawCancel: '取消',
      mcpRawSave: '💾 储存 raw JSON',
      mcpOverviewTitle: '📋 扩充 MCP 提供哪些功能 (Tool 总览)',
      mcpOverviewHint: '目前已连线 / 已注册的全部 MCP tools,依 server 分组。含本地 Qdrant + Web Search hosts,以及 config/mcp.json 中所有启用的远端 servers。LLM 下一次对话会从这里挑选要呼叫的工具。',
      mcpOverviewLoading: '载入中…',
      mcpOverviewEmpty: '目前没有任何 tool — 试著新增一个 server 或重启 QdrantLocalHost。',
      mcpOverviewServerFailed: '此 server 连线失败,没有可用的 tool。',
      // Notes 筆記本 (v0.9.95.0+)
      tabNotes: '📝 笔记本',
      notesTitle: '📝 笔记本',
      notesHint: '本地笔记本（markdown）— 新增/删除/修改/搜索（日期・标题・内容）/分类。清单逐页异步载入，不整表倾倒。',
      notesSearchPh: '搜索标题／内容…',
      notesAllCats: '全部分类',
      notesSearch: '🔍 搜索',
      notesNew: '➕ 新增',
      notesEmpty: '（尚无笔记 — 按「新增」开始）',
      notesPageOf: '第 {p} / {pages} 页 · 共 {total} 笔',
      notesTitlePh: '标题…',
      notesCatPh: '分类（可空）',
      notesBodyPh: 'markdown 内容…（支援 GFM：粗体、清单、表格、代码）',
      notesPreview: '👁 预览',
      notesEdit: '✏️ 编辑',
      notesSave: '💾 保存',
      notesDelete: '🗑 删除',
      notesCancel: '✖ 关闭',
      notesSaved: '已保存',
      notesSyncBtn: '🔄 同步离线编辑（{n}）',
      notesSynced: '已同步 {n} 笔离线编辑（{f} 笔待重试）',
      notesOfflineBanner: '📡 离线模式 — 显示 localStorage 暂存的列表',
      notesOfflineStash: '离线暂存成功——上线后自动同步',
      notesStillOffline: '仍在离线——离线编辑保留在暂存中，稍后再试',
      notesUpToDate: '没有待同步的离线编辑',
      notesLoadOffline2: '离线且没有暂存 — 上线后会自动载入',
      notesHw: '✍️ 手写',
      hwTitle: '✍️ 手写记事',
      hwHint: '用手指／触控笔／滑鼠在下方书写',
      hwClear: '🧹 清除',
      hwSave: '💾 保存并 OCR',
      hwOcring: '传至主机 OCR 中…（存档→转写→建索引）',
      hwDone: '手写已归档并建索引（{n} 块）',
      hwEmpty: '画面是空白的 — 先写点内容',
      hwOffline: '离线状态无法手写建档（需送往主机 OCR），请先恢复连线',
      hwPageOf: '第 {p} / {n} 页',
      hwPageAdd: '➕ 加页',
      hwFull: '⛶ 全屏幕',
      hwBg: '🖼 背景图',
      hwBgClear: '✕ 去除背景',
      hwBgApplied: '已套用背景图（画面已重置）',
      hwBgCleared: '已去除背景图',
      hwViewOriginal: '手写原文',
      hwPdf: '📄 导入 PDF',
      hwPdfImporting: '⏳ PDF 页面生成中… {f}',
      hwPdfDone: '已导入 {n} 页 PDF 底稿',
      hwPdfFail: 'PDF 导入失败：{m}',
      hwPen: '🖊 笔', hwMarker: '🖍 荧光', hwEraser: '🧽 擦',
      notesDeleted: '已删除',
      notesConfirmDelete: '确定要删除这则笔记？（不可复原）',
      notesTextPh: '文字',
      notesExpandAll: '▾ 全部展开',
      notesCollapseAll: '▸ 全部收合',
      notesListShow: '📋 显示清单',
      notesListHide: '📋 隐藏清单',
      notesSbChars: '字',
      notesSbLines: '行',
      notesSbSaved: '已自动存于浏览器',
      notesSbSavedAt: '已储存于 {t}',
      // Schedules (v0.8.0+)
      tabSchedules: '⏰ 排程',
      schedulesTitle: '⏰ 排程',
      schedulesHint: '在 cron / 间隔 / 一次性时间点执行预定义的 prompt。每次触发走完整工具链 (web_search / mcp_server / ...),结果写到专属 session。daemon 在 Agent 内常驻,开此 tab 看实时状态。',
      schedulesAdd: '+ 新增排程',
      schedulesEnabledOnly: '只看启用',
      schedulesLoading: '加载中…',
      schedulesStatusText: '共 {n} 个排程 ({enabled} 启用) · daemon {daemon}',
      schedulesRunNow: '立即触发',
      schedulesEnable: '启用',
      schedulesDisable: '停用',
      scheduleEditTitle: '编辑排程',
      scheduleFieldName: '名称',
      scheduleFieldPrompt: 'Prompt',
      scheduleFieldPromptHint: '会送进 Agent.chat_turn,完整工具 (web_search / MCP) 都可用。',
      scheduleFieldType: '排程类型',
      scheduleFieldInterval: '间隔(秒)',
      scheduleFieldIntervalHint: '60 = 每分钟,3600 = 每小时,86400 = 每天。',
      scheduleFieldCron: 'Cron 表达式',
      scheduleFieldCronHint: '分 时 日 月 周 (0-6,周日=0)。例: <code>0 9 * * *</code> 每天 9 点,<code>*/15 * * * *</code> 每 15 分钟,<code>0 0 * * 1</code> 每周一午夜。',
      scheduleFieldOnceAt: '执行时间 (ISO datetime)',
      scheduleFieldUseTools: '使用 MCP 工具',
      scheduleFieldEnabled: '启用',
      scheduleFieldNameRequired: '请输入名称',
      scheduleFieldPromptRequired: '请输入 prompt',
      scheduleFieldIntervalRequired: '请输入正整数秒数',
      scheduleFieldCronRequired: '请输入 cron 表达式',
      scheduleFieldOnceAtRequired: '请输入 ISO datetime',
      scheduleSaved: '已保存排程 {id}',
      scheduleToggled: '排程 {id} 已{state}',
      scheduleDeleted: '已删除排程 {id}',
      scheduleFired: '排程 {id} 已触发,session={session}',
      confirmDeleteSchedule: '删除排程 "{id}" ? 历史记录一并清除。',
    },
    'en': {
      appTitle: 'Divine Sense Agent',
      // Theme picker (v0.9.97.0+)
      themeLabel: 'Theme',
      themeAuto: '🌗 Auto (system)',
      themeLight: '☀️ Light',
      themeDark: '🌙 Dark',
      // Appearance dialog (v0.9.99.0+)
      appearanceBtn: '🖌️ Appearance',
      appearanceTitle: '🎨 Appearance',
      appearanceBg: '🖼️ Background image',
      appearanceBgUrl: 'or paste an image URL…',
      appearanceBgDim: 'Dim',
      appearanceBgClear: 'Clear background',
      appearanceBgFit: 'Layout',
      appearanceBgFitCover: 'Cover (fill screen)',
      appearanceBgFitContain: 'Contain (full image)',
      appearanceBgFitCenter: 'Center (original size)',
      appearanceBgFitTile: 'Tile (repeat)',
      appearanceBgTooBig: 'image too large — pick a smaller one',
      appearanceBgDropped: 'storage full — background removed (other settings kept)',
      appearanceBgUrlBad:
        'invalid background URL — use an http(s):// image URL or an '
        + 'uploads/ sandbox path',
      appearanceSaveFailed:
        'appearance save failed (browser storage full?)',
      appearanceBindFail: 'appearance init failed:',
      appearanceApply: 'Apply',
      appearanceAccent: '🎨 Accent color',
      appearanceAccentReset: 'Reset',
      appearanceFont: '🔤 Font',
      appearanceFontCustom: 'custom font CSS…',
      appearanceFontSystem: 'System default',
      appearanceFontStd: 'Standard',
      appearanceFontSmall: 'Small',
      appearanceFontLarge: 'Large',
      appearanceFontXL: 'X-Large',
      appearanceMono: 'Code font',
      appearanceFontSize: 'Font size',
      appearanceReset: 'Reset all',
      appearanceSaved: 'Appearance applied',
      tabChat: 'Chat', tabIngest: 'Ingest', tabSearch: 'Search',
      tabAdmin: 'Admin', tabStats: 'Stats', tabSessions: 'Sessions',
      newSession: '+ New',
      save: 'Save', clear: 'Clear', send: 'Send',
      askPlaceholder: 'Ask anything...',
      ingestRun: 'Run', pickFile: 'Pick file', pickDir: 'Pick folder',
      ingestRecursive: 'Recursive', ingestResult: 'Result',
      ingestHint: 'Tip: pick a folder to ingest supported files recursively.',
      searchPlaceholder: 'Search vector database...',
      searchFilterPlaceholder: 'filename filter (optional)',
      search: 'Search', refresh: 'Refresh',
      loadData: '📂 Load data', dataBrowser: 'Data management (paged)',
      pagerPrev: '⬆ Prev', pagerNext: '⬇ Next page',
      dataPos: 'page {p} · showing {n} this page',
      dataEmpty: 'this collection is empty',
      deletePoint: 'Delete this point',
      confirmDeletePoint: 'Delete this point ({id}…)? This cannot be undone.',
      pointDeleted: 'deleted {n} point(s)',
      noDocIndex: 'This collection has no filename index — use the paged data browser below.',
      statCollection: 'Collection', statVectors: 'Vectors',
      statDim: 'Dim', statDocs: 'Docs',
      docList: 'Indexed documents',
      thTitle: 'Title', thMsgs: 'Msgs', thUpdated: 'Updated', thActions: 'Actions',
      // v0.9.103.1 — Sessions-tab multi-select toolbar
      sessSelAll: 'Select all', sessDeleteSel: 'Delete selected ({n})',
      sessExportSel: 'Export selected ({n})',
      sessNone: 'No sessions selected',
      sessDeleted: 'Deleted {ok} (skipped {skip})',
      sessExported: 'Exported {n} sessions',
      // v0.9.103.5 — per-row session title rename
      sessRename: 'Rename', sessRenamePrompt: 'Enter the new session title:',
      sessRenameEmpty: 'Title cannot be empty',
      sessRenamed: 'Title updated: "{title}"',
      adminTitle: 'Admin',
      adminHint: 'Overview. Destructive ops need confirmation.',
      qdrantInfo: 'Qdrant', embedInfo: 'Embedding',
      dangerZone: 'Danger zone',
      recreateCollection: 'Recreate collection',
      recreateHint: 'Drops + recreates. Vectors wiped.',
      recreate: 'Recreate',
      deleteCollection: 'Delete collection',
      deleteHint: 'Permanently deletes. Cannot be undone.',
      delete: 'Delete',
      qdrant: 'Qdrant', llm: 'LLM',
      ok: 'OK', err: 'Error', warn: 'Warn',
      errGeneric: 'Error: {msg}',
      confirmRecreate: 'Recreate? All vectors will be deleted!',
      confirmDelete: 'Delete? This cannot be undone.',
      // v0.9.59.1 — all-collections inventory + per-row delete
      collectionsZone: 'Collections',
      collectionsHint: 'Deletion is destructive (cannot be undone); each row needs its own confirmation.',
      colName: 'Collection', colPoints: 'Points', colDim: 'Dim',
      colKind: 'Kind', colActions: 'Actions',
      kind_rag: 'RAG docs', kind_log: 'Logs', kind_stock: 'Stock',
      kind_memory: 'Memory', kind_other: 'Other',
      confirmDeleteCollection: 'Delete collection "{name}"? This cannot be undone!',
      colDeleted: 'Deleted collection "{name}"',
      yesRecreate: 'Yes, recreate', yesDelete: 'Yes, delete', cancel: 'Cancel',
      sourcesLabel: 'Sources', loading: 'Loading...',
      // v0.9.5+ per-message "View in Diagrams" button
      openInDiagrams: '📊 View in Diagrams',
      // v0.9.127.5+ — share out (per-bubble 📤) + share-in landing toast
      shareReply: '📤 Share this reply',
      sharedIn: '📥 Received {n} shared file(s)',
      shareCopied: 'Reply copied to the clipboard',
      // v0.9.133.0 — bubble style (顏色/透明度)
      styleBtn: '🎨 Bubble colour / opacity',
      styleAsst: 'Assistant bubble',
      styleUser: 'User message',
      styleReset: 'Reset to defaults',
      // v0.9.132.9 — clipboard buttons (input / bubbles / notes editor)
      clipCopyMsg: '📋 Copy this message',
      clipCopyInput: '📋 Copy the input box',
      clipPasteInput: '📥 Paste (insert clipboard text)',
      clipCopySel: '📋 Copy the selection',
      clipCutSel: '✂️ Cut the selection',
      clipPasteSel: '📥 Paste at the selection',
      clipCopied: 'Copied to the clipboard',
      clipPasted: 'Pasted',
      clipPasteFail: 'Cannot read the clipboard (not allowed / non-text) — use the keyboard paste instead',
      clipEmpty: 'Nothing to copy',
      shareFail: 'Share failed: {msg}',
      uploadTimeout: 'upload timed out (connection too slow or dropped)',
      uploadBatch: 'batch {i}/{n} · files {d}/{t}',
      uploadCancel: 'Cancel upload',
      uploadStalled: '⏸ stalled for {s}s',
      uploadFlowing: '⬆ streaming',
      uploadNetErr: 'network error (connection dropped)',
      autoTtsOn: '🔊 Auto-read: ON (replies spoken as they land)',
      autoTtsOff: '🔊 Auto-read: OFF',
      voiceLoopOn: '🎧 Hands-free: ON (listen → auto-send → speak → listen)',
      voiceLoopOff: '🎧 Hands-free: OFF',
      voiceFallback: '🔊 Backend voice unavailable — using system voice',
      voicePermission: '🎤 Microphone permission denied — click the 🔒 icon at the left of the address bar, set Microphone to Allow (tick Remember), then retry',
      voicePermissionIOS: '🎤 iPhone mic denied — iOS Settings → Privacy & Security → Microphone → allow Safari / this app (a prior denial needs website data cleared or the app reinstalled)',
      voicePermissionIOSDenied: '🎤 iPhone mic PERMANENTLY denied — ① delete this home-screen app ② iOS Settings → Apps → Safari → Advanced → Website Data → remove tchtool.app ③ confirm Settings → Privacy & Security → Microphone has Safari enabled ④ reopen this site in Safari and tap Allow on the first prompt',
      voiceResumePlay: 'Play the reply',
      voiceResumeListen: 'Start speaking',
      voiceIOSAutoplayHint: '🔊 iOS blocks autoplay — tap the 🔊 beside each reply to hear it (or turn on 🎧 hands-free and use taps to listen & speak)',
      voiceStop: '⏹ Stop speaking',
      voiceResume: 'Tap to continue listening',
      // v0.9.65.0+ — ⬇ download chip for sandbox files in a bubble
      downloadFile: 'Download', fileSaved: 'Saved to {dest}',
      // v0.9.133.7 — instant Summary column in the Sessions table
      thSummary: 'Summary',
      // v0.9.133.6 — in-app preview for LLM-made .html (iPhone safe)
      htmlPreview: 'Preview', htmlPreviewClose: 'Close preview',
      // v0.9.68.0+ — chat bidirectional voice (🎤 input / 🔊 speak)
      voiceMic: '🎤 Voice input (click to start/stop)',
      voiceRecording: '🔴 Recording… (click to stop)',
      voiceTranscribing: '🎙 Transcribing…',
      voiceSpeak: '🔊 Speak this reply',
      voiceFail: 'Voice feature failed: {msg}',
      voiceMicErr: 'Microphone unavailable: {msg}',
      voiceNoSupport: 'Microphone recording is not supported here',
      // v0.9.100.0 — quick phrases (⚡ user-defined snippets, one-click insert)
      snipsBtn: '⚡',
      snipsAdd: '➕ Add',
      snipsPlaceholder: 'Multi-line phrase (Enter = newline, Ctrl+Enter to add)…',
      turnExecRun: '⚙️ Working…',
      turnExecDone: '✅ Done ({n} events)',
      turnExecError: '⚠️ Not completed',
      turnExecStopped: '🛑 Stopped',
      turnAbort: '🛑 Abort',
      turnAbortSent: 'Abort requested…',
      turnAbortPending: 'Abort sent — the background turn is still winding down (a long LLM call may take 1–3 min); you can send new messages meanwhile',
      turnBgDetached: 'Auto-moved to a background task (60 s+ elapsed) — send new messages freely; the reply still lands here when done',
      turnGone: 'Server connection lost (possibly restarted) — turn state unknown',
      turnLogShow: 'View execution log',
      snipsCount: '{n} phrases',
      snipsDup: 'This phrase already exists',
      snipsMax: 'Phrase limit reached (50)',
      snipsEmpty: 'No phrases yet — click "Show add row" below the chips; shared across all devices.',
      snipsHideAdd: '▲ Hide add row',
      snipsShowAdd: 'Show add row',
      // v0.9.9.4+ per-bubble start / end time above each chat bubble
      chatStart: 'Start', chatEnd: 'End',
      chatTools: '🧰 Tools', chatToolsAll: 'All on',
      chatToolsNone: 'All off', chatToolsReset: 'Reset',
      // v0.9.47.0+ parallel multi-task: task panel + bg-run toggle
      chatTasks: '⏳ Tasks',
      // v0.9.133.10 — inline ⚙ execution-log expander on task rows
      taskLogShow: 'Execution log',
      taskLogNoteIngest: 'Pure ingest task has no execution log —'
        + ' you will be notified on completion or failure',
      taskLogNoteNone: 'This task has no execution log', taskRefresh: 'Refresh',
      taskNone: '(no background tasks)', taskRunning: 'running',
      taskDone: 'done', taskFailed: 'failed',
      bgRun: 'Background',
      taskSubmitted:
        'Background task {id} submitted (session: {sid}) — check the ⏳ Tasks panel.',
      bgResultBack: 'Background task {id} finished — result written back to this chat',
      // v0.9.130.1+ local /playpet + /endpet chat commands
      petComeOut: '🐶 The puppy is out playing! Type /endpet to send it back to rest.',
      petAlreadyOut: '🐶 The puppy is already out playing.',
      petGoesRest: '🐾 The puppy went back to rest. Type /playpet to call it out again.',
      petNotOut: 'The puppy is not out yet. Type /playpet to call it out.',
      // v0.9.16.0+ web-mode client upload (hidden in the pywebview GUI)
      uploadMark: '[uploaded]', uploadOk: 'Uploaded {ok} file(s)',
      uploading: '⏳ Uploading… {f}',
      uploadSkip: ' ({skip} skipped)', uploadFail: 'Upload failed: {msg}',
      // v0.9.39.0+ — credentials tab
      tabCreds: '🔑 Credentials', credsTitle: '🔑 Credential sets',
      credsHint: 'One shared account store for every service that needs a login (currently: ONVIF cameras). Passwords are RECOMMENDED as env references (the store never holds the secret); plain passwords are stored base64-OBSCURED, not encrypted. Managed here, via MCP (cred_add / cred_list / …) and the CLI (python cli.py creds …).',
      credsUser: 'User', credsSource: 'Source', credsService: 'Service',
      credsNotes: 'Notes', credsAddTitle: 'Add / replace credential set',
      credsOverwrite: 'overwrite existing', credsSave: '💾 Save',
      credsDelete: '🗑 Delete', credsSaved: 'Credential set saved',
      credsDeleted: 'Credential set deleted',
      credsConfirmDelete: 'Delete this credential set?',
      // v0.8.7+ splash screen
      splashTitle: 'Divine Sense Agent',
      splashChecking: 'Checking resources…',
      splashReady: 'Ready',
      splashDone: '{n} / 3 complete',
      splashSkipped: 'Skipped',
      splashSkip: 'Skip →',
      readinessBackend: 'Backend API',
      readinessQdrant: 'Qdrant connection',
      readinessLLM: 'LLM API key',
      readinessMcp: 'MCP tool init',
      readinessTimeout: 'timed out',
      docsError: 'Could not list documents: {msg}',
      removeOneTitle: 'Remove this document from the index',
      removeSelected: '🗑 Remove selected (0)',
      confirmRemoveOne: 'Permanently remove "{name}" from the Qdrant index? This cannot be undone.',
      confirmRemoveMany: 'Permanently remove {n} documents from the Qdrant index? This cannot be undone.',
      removedToast: 'Removed {files} document(s), {chunks} chunks total.',
      removedMissing: ' ({n} filenames were not in the index — typo?)',
      cacheZone: 'Embedding cache',
      clearCacheTitle: 'Clear embedding cache',
      clearCacheHint: 'Wipes both in-memory LRU and persistent SQLite cache. The next ingest/chat will re-call the embedding API.',
      clearCache: '🧹 Clear',
      confirmClearCache: 'Clear all embedding caches?',
      cacheCleared: 'Cleared {lru} LRU + {disk} disk entries',
      auditBackends: '🔎 Audit hash vectors',
      auditBackendsHint: 'Audit is read-only; Re-embed re-calls the embedding backend for flagged points (confirm first).',
      reembedHash: '♻️ Re-embed hash',
      backfillHeadings: '§ Backfill headings',
      backfillDone: 'Backfill done: {n} / {scanned} points filled with heading_path (errors: {errors})',
      auditDone: 'Audit done: {scanned} points scanned, {hash} hash vectors',
      reembedDone: 'Re-embed done: {n} / {hash} repaired (still hash: {still})',
      confirmReembed: 'Re-embed all hash vectors (cost/time depends on the backend) — proceed?',
      reloadZone: 'Config hot reload',
      reloadTitle: 'Reload .env / mcp.json',
      reloadHint: 'Re-reads .env (typed re-assign), resets the LLM provider chain, restarts daemon services (scheduler / webhook / Telegram / ONVIF watches per their enable-conditions) and reconnects the MCP fleet. Deleting an env var still needs a restart (dotenv never clears the process env).',
      reloadBtn: '🔄 Reload',
      reloadDone: 'Reloaded. Changed: {changed}; services: {svc}',
      // MCP fleet editor (v0.8.0+)
      mcpEditorTitle: '🔌 MCP devices (3rd-party servers)',
      mcpEditorHint: 'Edit the active config/mcp.json fleet. Save hot-reloads the manager — the next chat turn uses the new tool set without restarting. Local Qdrant + Web Search hosts are always on and cannot be edited here.',
      mcpRefresh: '🔄 Refresh',
      mcpAdd: '+ Add server',
      mcpSaveAll: '💾 Save & reload',
      mcpLoading: 'loading…',
      mcpServerCount: '{n} remote servers configured',
      mcpInvalid: 'invalid',
      mcpValid: 'valid',
      mcpRestart: 'restart',
      mcpRestartHint: 'Close and reconnect this server',
      mcpEditTitle: 'Edit MCP server',
      mcpFieldName: 'Name',
      mcpFieldNamePh: 'my-server',
      mcpFieldNameHint: 'Used as the tool prefix. Must be unique across the fleet.',
      mcpFieldTransport: 'Transport',
      mcpFieldCommand: 'Command',
      mcpFieldCommandPh: 'python -m my_mcp_server',
      mcpFieldCommandHint: 'Split by whitespace into an argv array on save.',
      mcpFieldEnv: 'Env (KEY=value, one per line)',
      mcpFieldEnvPh: 'MY_TOKEN=',
      mcpFieldCwd: 'Working dir (optional)',
      mcpFieldCwdPh: '/path/to/server',
      mcpFieldUrl: 'Endpoint URL',
      mcpFieldUrlPh: 'https://mcp.example.com/v1/mcp',
      mcpFieldHeaders: 'Headers (KEY=value, one per line)',
      mcpFieldHeadersPh: 'Authorization=Bearer ${MY_TOKEN}',
      mcpFieldHeadersHint: '${ENV_VAR} references are substituted at connect time.',
      mcpFieldNameRequired: 'Name is required',
      mcpFieldCommandRequired: 'Command is required',
      mcpFieldUrlRequired: 'URL is required',
      mcpFieldNameDuplicate: 'A server named {name} already exists',
      mcpSaved: 'Saved {n} servers, total tool count {tools}',
      mcpRestarted: '{name} reconnected (tools {tools})',
      mcpParseError: 'config parse failed: {msg}',
      confirmRemoveMcp: 'Delete MCP server "{name}" ?',
      // MCP editor enhancements (v0.8.2+)
      mcpToggleRaw: '📝 Raw JSON',
      mcpOverviewToggle: '📋 Tool overview',
      mcpTest: '🧪 Test',
      mcpTestHint: 'Check connection status and tool list for this server',
      mcpTestTitle: 'Test {name}',
      mcpTestCount: '{n} tools total',
      mcpRawTitle: '📝 Edit config/mcp.json directly (raw JSON)',
      mcpRawHint: 'Edit the whole file in one go. Save does full validation + atomic write. Good for batch edits, paste-JSON, or tweaking _comment examples.',
      mcpRawFormat: '🎨 Format',
      mcpRawCancel: 'Cancel',
      mcpRawSave: '💾 Save raw JSON',
      mcpOverviewTitle: '📋 Tool overview (which MCP features are exposed)',
      mcpOverviewHint: 'Every connected / registered MCP tool, grouped by server. Includes the always-on local Qdrant + Web Search hosts and every enabled remote server in config/mcp.json. The next chat turn picks from this list.',
      mcpOverviewLoading: 'loading…',
      mcpOverviewEmpty: 'No tools registered yet — try adding a server or restarting QdrantLocalHost.',
      mcpOverviewServerFailed: 'Server failed to connect; no tools available.',
      // Notes notebook (v0.9.95.0+)
      tabNotes: '📝 Notes',
      notesTitle: '📝 Notes',
      notesHint: 'Local markdown notebook — create / delete / edit / search (date, title, content) / categories. The list loads page by page (async) — never dumps the whole table.',
      notesSearchPh: 'Search title / content…',
      notesAllCats: 'All categories',
      notesSearch: '🔍 Search',
      notesNew: '➕ New',
      notesEmpty: '(no notes yet — press "New" to start)',
      notesPageOf: 'Page {p} / {pages} · {total} notes',
      notesTitlePh: 'Title…',
      notesCatPh: 'Category (optional)',
      notesBodyPh: 'markdown body… (GFM: bold, lists, tables, code)',
      notesPreview: '👁 Preview',
      notesEdit: '✏️ Edit',
      notesSave: '💾 Save',
      notesDelete: '🗑 Delete',
      notesCancel: '✖ Close',
      notesSaved: 'Saved',
      notesSyncBtn: '🔄 Sync offline edits ({n})',
      notesSynced: 'Synced {n} offline edits ({f} still pending)',
      notesOfflineBanner: '📡 Offline — showing the localStorage cached list',
      notesOfflineStash: 'Edit stashed offline — it syncs when back online',
      notesStillOffline: 'Still offline — offline edits kept in the stash, try again later',
      notesUpToDate: 'No offline edits awaiting sync',
      notesLoadOffline2: 'Offline with no cached list — it will load once online',
      notesHw: '✍️ Handwrite',
      hwTitle: '✍️ Handwritten note',
      hwHint: 'Write with a finger / stylus / mouse below',
      hwClear: '🧹 Clear',
      hwSave: '💾 Save & OCR',
      hwOcring: 'Sending to the host for OCR… (archive → transcribe → index)',
      hwDone: 'Handwriting archived and indexed ({n} chunks)',
      hwEmpty: 'The canvas is empty — write something first',
      hwOffline: 'Handwriting needs the host (offline); reconnect first',
      hwPageOf: 'Page {p} / {n}',
      hwPageAdd: '➕ Add page',
      hwFull: '⛶ Fullscreen',
      hwBg: '🖼 Background',
      hwBgClear: '✕ Remove bg',
      hwBgApplied: 'Background applied (canvas reset)',
      hwBgCleared: 'Background removed',
      hwViewOriginal: 'Handwritten original',
      hwPdf: '📄 Import PDF',
      hwPdfImporting: '⏳ Rendering PDF pages… {f}',
      hwPdfDone: 'Imported {n} PDF page(s) as underlay',
      hwPdfFail: 'PDF import failed: {m}',
      hwPen: '🖊 Pen', hwMarker: '🖍 Marker', hwEraser: '🧽 Erase',
      notesDeleted: 'Deleted',
      notesConfirmDelete: 'Delete this note? (cannot be undone)',
      notesTextPh: 'text',
      notesExpandAll: '▾ Expand all',
      notesCollapseAll: '▸ Collapse all',
      notesListShow: '📋 Show list',
      notesListHide: '📋 Hide list',
      notesSbChars: 'chars',
      notesSbLines: 'lines',
      notesSbSaved: 'auto-saved in browser',
      notesSbSavedAt: 'saved at {t}',
      // Schedules (v0.8.0+)
      tabSchedules: '⏰ Schedules',
      schedulesTitle: '⏰ Schedules',
      schedulesHint: 'Run pre-defined prompts on a cron / interval / one-shot trigger. Every fire uses the full tool surface (web_search, mcp_server, …) and writes to a dedicated session. The scheduler daemon lives inside the Agent — open this tab to see live status.',
      schedulesAdd: '+ Add schedule',
      schedulesEnabledOnly: 'Enabled only',
      schedulesLoading: 'loading…',
      schedulesStatusText: '{n} schedule(s) ({enabled} enabled) · daemon {daemon}',
      schedulesRunNow: 'Run now',
      schedulesEnable: 'Enable',
      schedulesDisable: 'Disable',
      scheduleEditTitle: 'Edit schedule',
      scheduleFieldName: 'Name',
      scheduleFieldPrompt: 'Prompt',
      scheduleFieldPromptHint: 'Runs through Agent.chat_turn with full tool surface — web_search and any registered MCP server are available.',
      scheduleFieldType: 'Schedule type',
      scheduleFieldInterval: 'Interval (seconds)',
      scheduleFieldIntervalHint: '60 = every minute, 3600 = every hour, 86400 = every day.',
      scheduleFieldCron: 'Cron expression',
      scheduleFieldCronHint: 'minute / hour / day-of-month / month / day-of-week (0-6, Sun=0). Examples: <code>0 9 * * *</code> daily at 9am, <code>*/15 * * * *</code> every 15 min, <code>0 0 * * 1</code> weekly Monday midnight.',
      scheduleFieldOnceAt: 'Fire at (ISO datetime)',
      scheduleFieldUseTools: 'Use MCP tools',
      scheduleFieldEnabled: 'Enabled',
      scheduleFieldNameRequired: 'Name is required',
      scheduleFieldPromptRequired: 'Prompt is required',
      scheduleFieldIntervalRequired: 'Positive integer seconds required',
      scheduleFieldCronRequired: 'Cron expression required',
      scheduleFieldOnceAtRequired: 'ISO datetime required',
      scheduleSaved: 'Saved schedule {id}',
      scheduleToggled: 'Schedule {id} {state}',
      scheduleDeleted: 'Deleted schedule {id}',
      scheduleFired: 'Schedule {id} fired — session={session}',
      confirmDeleteSchedule: 'Delete schedule "{id}" ? Fire history is also wiped.',
    },
    'ja': {
      appTitle: 'Divine Sense Agent',
      // Theme picker (v0.9.97.0+)
      themeLabel: 'テーマ',
      themeAuto: '🌗 自動（システムに従う）',
      themeLight: '☀️ ライト',
      themeDark: '🌙 ダーク',
      // Appearance dialog (v0.9.99.0+)
      appearanceBtn: '🖌️ 外観設定',
      appearanceTitle: '🎨 外観設定',
      appearanceBg: '🖼️ 背景画像',
      appearanceBgUrl: 'または画像 URL を貼り付け…',
      appearanceBgDim: '暗さ',
      appearanceBgClear: '背景をクリア',
      appearanceBgFit: 'レイアウト',
      appearanceBgFitCover: 'カバー（全画面）',
      appearanceBgFitContain: 'コンテイン（全体表示）',
      appearanceBgFitCenter: '中央（原寸）',
      appearanceBgFitTile: 'タイル（繰り返し）',
      appearanceBgTooBig: '画像が大きすぎます — 小さい画像を選んでください',
      appearanceBgDropped: '容量不足 — 背景画像を削除しました（他の設定は保持）',
      appearanceBgUrlBad:
        '背景 URL が無効です — http(s):// の画像 URL か uploads/ パスを',
      appearanceSaveFailed: '外観設定の保存に失敗（ストレージ不足？）',
      appearanceBindFail: '外観設定の初期化に失敗：',
      appearanceApply: '適用',
      appearanceAccent: '🎨 アクセントカラー',
      appearanceAccentReset: 'リセット',
      appearanceFont: '🔤 フォント',
      appearanceFontCustom: 'カスタムフォント CSS…',
      appearanceFontSystem: 'システム既定',
      appearanceFontStd: '標準',
      appearanceFontSmall: '小',
      appearanceFontLarge: '大',
      appearanceFontXL: '特大',
      appearanceMono: 'コードフォント',
      appearanceFontSize: '文字サイズ',
      appearanceReset: 'すべてリセット',
      appearanceSaved: '外観を適用しました',
      tabChat: '💬 チャット', tabIngest: '📥 取り込み', tabSearch: '🔍 検索',
      tabAdmin: '🛠 系統管理', tabStats: '📊 統計', tabSessions: '🗂 セッション',
      newSession: '＋ 新規セッション',
      save: '💾 保存', clear: '🗑 クリア', send: '送信 ➤',
      askPlaceholder: 'ドキュメントについて質問…',
      ingestRun: '▶ 取り込み', pickFile: '📄 ファイル選択…', pickDir: '📁 フォルダ選択…',
      ingestRecursive: '再帰スキャン', ingestResult: '結果',
      ingestHint: 'ヒント:フォルダを選ぶと対応ファイル (.pdf .docx .pptx .epub .txt .md .py .json …) を再帰的に取り込みます。重複は SHA-1 で自動スキップされます。',
      searchPlaceholder: 'ベクターデータベースを検索…',
      searchFilterPlaceholder: 'ファイル名フィルター(任意)',
      search: '🔍 検索', refresh: '🔄 更新',
      loadData: '📂 データ読込', dataBrowser: 'データ管理（ページング）',
      pagerPrev: '⬆ 前へ', pagerNext: '⬇ 次へ',
      dataPos: '{p} ページ目 · このページ {n} 件',
      dataEmpty: 'データがありません',
      deletePoint: 'このポイントを削除',
      confirmDeletePoint: 'このデータポイント（{id}…）を削除しますか？元に戻せません。',
      pointDeleted: '{n} 件削除しました',
      noDocIndex: 'この collection には filename インデックスがありません。下のデータ管理ブラウザをご利用ください。',
      statCollection: 'Collection', statVectors: 'ベクトル数',
      statDim: '次元', statDocs: 'ドキュメント数',
      docList: 'インデックス済みドキュメント',
      thTitle: 'タイトル', thMsgs: 'メッセージ数', thUpdated: '更新日時', thActions: '操作',
      // v0.9.103.1 — Sessions-tab multi-select toolbar
      sessSelAll: 'すべて選択', sessDeleteSel: '選択を削除（{n}）',
      sessExportSel: '選択を書き出し（{n}）',
      sessNone: 'セッションが未選択です',
      sessDeleted: '{ok}件削除（{skip}件スキップ）',
      sessExported: '{n}件書き出しました',
      // v0.9.103.5 — per-row session title rename
      sessRename: 'タイトル変更', sessRenamePrompt: '新しいセッションタイトルを入力：',
      sessRenameEmpty: 'タイトルは空にできません',
      sessRenamed: 'タイトルを「{title}」に更新しました',
      adminTitle: '系統管理',
      adminHint: 'Qdrant collection、embedding backend の状態を表示します。破壊的操作は確認が必要です。',
      qdrantInfo: 'Qdrant', embedInfo: 'Embedding',
      dangerZone: '危険ゾーン',
      recreateCollection: 'Collection を再作成',
      recreateHint: 'Collection を削除し現在の HNSW 設定で再作成します。すべてのベクトルが削除されるため再取り込みが必要です。',
      recreate: '♻ 再作成',
      deleteCollection: 'Collection を削除',
      deleteHint: 'Collection を完全に削除します。元に戻せません。',
      delete: '✖ 削除',
      qdrant: 'Qdrant', llm: 'LLM',
      ok: '✓ 準備完了', err: '✗ エラー', warn: '⚠ 警告',
      errGeneric: 'エラー:{msg}',
      confirmRecreate: 'Collection を再作成しますか?すべてのベクトルが削除されます!',
      confirmDelete: 'Collection を削除しますか?この操作は元に戻せません!',
      // v0.9.59.1 — all-collections inventory + per-row delete
      collectionsZone: 'Collections',
      collectionsHint: '削除は破壊的操作です(元に戻せません)。各行ごとに確認が必要です。',
      colName: 'Collection', colPoints: 'ベクトル数', colDim: '次元',
      colKind: '用途', colActions: '操作',
      kind_rag: 'RAG 文書', kind_log: 'ログ分析', kind_stock: '株式快照',
      kind_memory: '長期記憶', kind_other: 'その他',
      confirmDeleteCollection: 'Collection「{name}」を削除しますか?元に戻せません!',
      colDeleted: 'Collection「{name}」を削除しました',
      yesRecreate: 'はい、再作成', yesDelete: 'はい、削除', cancel: 'キャンセル',
      sourcesLabel: '出典', loading: '読み込み中…',
      // v0.9.5+ メッセージの「図で開く」ボタン
      openInDiagrams: '📊 図で開く',
      // v0.9.127.5+ — share out (per-bubble 📤) + share-in landing toast
      shareReply: '📤 この返信を共有',
      sharedIn: '📥 {n} 件の共有ファイルを受け取りました',
      shareCopied: '返信をクリップボードにコピーしました',
      // v0.9.133.0 — bubble style (顏色/透明度)
      styleBtn: '🎨 気泡の色／透明度',
      styleAsst: 'アシスタント気泡',
      styleUser: 'ユーザーメッセージ',
      styleReset: '既定に戻す',
      // v0.9.132.9 — clipboard buttons (input / bubbles / notes editor)
      clipCopyMsg: '📋 このメッセージをコピー',
      clipCopyInput: '📋 入力欄の内容をコピー',
      clipPasteInput: '📥 貼り付け（クリップボードの文字を挿入）',
      clipCopySel: '📋 選択範囲をコピー',
      clipCutSel: '✂️ 選択範囲を切り取り',
      clipPasteSel: '📥 選択位置に貼り付け',
      clipCopied: 'クリップボードにコピーしました',
      clipPasted: '貼り付けました',
      clipPasteFail: 'クリップボードを読めません（未許可／非文字）— キーボードで貼り付けてください',
      clipEmpty: 'コピーする内容がありません',
      shareFail: '共有に失敗しました：{msg}',
      uploadTimeout: 'アップロードがタイムアウトしました（接続が遅い／切断）',
      uploadBatch: 'バッチ {i}/{n}・ファイル {d}/{t}',
      uploadCancel: 'アップロードをキャンセル',
      uploadStalled: '⏸ {s} 秒停止中',
      uploadFlowing: '⬆ 転送中',
      uploadNetErr: 'ネットワークエラー（接続切断）',
      autoTtsOn: '🔊 自動読み上げ：ON（返信が着き次第再生）',
      autoTtsOff: '🔊 自動読み上げ：OFF',
      voiceLoopOn: '🎧 ハンズフリー：ON（聴き取り→自動送信→読み上げ→再聴き取り）',
      voiceLoopOff: '🎧 ハンズフリー：OFF',
      voiceFallback: '🔊 バックエンド音声が利用できないためシステム音声に切替',
      voicePermission: '🎤 マイクの権限が拒否されました — アドレスバー左の 🔒 アイコンでマイクを「許可」（記憶にチェック）にして再試行',
      voicePermissionIOS: '🎤 iPhone のマイクが拒否されました — iOS 設定 → プライバシーとセキュリティ → マイク → Safari／本 App を許可（拒否済みはサイトデータ消去か再インストールが必要）',
      voicePermissionIOSDenied: '🎤 iPhone のマイクは永久に拒否済み — ①ホーム画面の本 App を削除 ②iOS 設定 → Apps → Safari → 詳細 → Webサイトデータから tchtool.app を削除 ③設定 → プライバシーとセキュリティ → マイクで Safari を許可 ④Safari で再オープンし最初の確認で「許可」',
      voiceResumePlay: '返信を再生',
      voiceResumeListen: '話し始める',
      voiceIOSAutoplayHint: '🔊 iOS は自動再生を許可しません — 各返信は気泡横の 🔊 で再生してください（🎧 ハンズフリーならタップで聴き取りと発話）',
      voiceStop: '⏹ 読み上げを停止',
      voiceResume: 'タップして聴き取りを続ける',
      // v0.9.65.0+ — chat バブル内のサンドボックスファイル ⬇ DL chip
      downloadFile: 'ダウンロード', fileSaved: '保存先: {dest}',
      // v0.9.133.7 — Sessions 表のインスタント要約列
      thSummary: '要約',
      // v0.9.133.6 — LLM 産出 .html のアプリ内プレビュー（iPhone 安全）
      htmlPreview: 'プレビュー', htmlPreviewClose: 'プレビューを閉じる',
      // v0.9.68.0+ — chat 音声双方向（🎤 入力 / 🔊 読み上げ）
      voiceMic: '🎤 音声入力（クリックで開始/停止）',
      voiceRecording: '🔴 録音中…（クリックで停止）',
      voiceTranscribing: '🎙 音声認識中…',
      voiceSpeak: '🔊 返答を読み上げ',
      voiceFail: '音声機能に失敗: {msg}',
      voiceMicErr: 'マイクを利用できません: {msg}',
      voiceNoSupport: 'この環境ではマイク録音に対応していません',
      // v0.9.100.0 — クイックフレーズ（⚡ ユーザー定義、ワンクリック挿入）
      snipsBtn: '⚡',
      snipsAdd: '➕ 追加',
      snipsPlaceholder: '複数行フレーズ（Enter=改行、Ctrl+Enter で追加）…',
      turnExecRun: '⚙️ 実行中…',
      turnExecDone: '✅ 完了（{n} イベント）',
      turnExecError: '⚠️ 完了できませんでした',
      turnExecStopped: '🛑 中止済み',
      turnAbort: '🛑 中止',
      turnAbortSent: '中止を要求しました…',
      turnAbortPending: '中止を要求しました——バックグラウンドのターンはまだ終了処理中（長いLLM呼び出しは1〜3分かかる場合あり）；先に新しいメッセージを送れます',
      turnBgDetached: '自動でバックグラウンドタスクに切り替えました（60秒超過）— 新しいメッセージを送れます；返信は完了後ここに表示されます',
      turnGone: 'サーバーとの接続が切断（再起動の可能性）— 状態不明',
      turnLogShow: '実行ログを表示',
      snipsCount: '{n} 件のフレーズ',
      snipsDup: 'このフレーズは既に存在します',
      snipsMax: 'フレーズ数が上限（50）に達しました',
      snipsEmpty: 'フレーズはまだありません — 「追加列を表示」から作成；全デバイスで共有。',
      snipsEmpty: 'フレーズはまだありません — 「追加列を表示」から作成；全デバイスで共有。',
      snipsHideAdd: '▲ 追加列を隠す',
      snipsShowAdd: '追加列を表示',
      // v0.9.9.4+ 各チャットバブル上の開始 / 終了時刻
      chatStart: '開始', chatEnd: '終了',
      chatTools: '🧰 ツール', chatToolsAll: '全オン',
      chatToolsNone: '全オフ', chatToolsReset: 'リセット',
      // v0.9.47.0+ 並列マルチタスク：タスクパネル + バックグラウンド実行
      chatTasks: '⏳ タスク',
      // v0.9.133.10 — タスク行 ⚙ 実行ログ展開
      taskLogShow: '実行ログ',
      taskLogNoteIngest: '取り込み専用タスクに実行ログはありません'
        + ' — 完了/失敗は自動通知されます',
      taskLogNoteNone: 'このタスクには実行ログがありません', taskRefresh: '更新',
      taskNone: '（バックグラウンドタスクなし）', taskRunning: '実行中',
      taskDone: '完了', taskFailed: '失敗',
      bgRun: 'バックグラウンド',
      taskSubmitted:
        'バックグラウンドタスク {id} を送出しました（session: {sid}）—「⏳ タスク」パネルで確認',
      bgResultBack: 'バックグラウンドタスク {id} が完了 — 結果をこのチャットに書き戻しました',
      // v0.9.130.1+ local /playpet + /endpet chat commands
      petComeOut: '🐶 子犬が遊びに出ました！/endpet で休憩に戻せます',
      petAlreadyOut: '🐶 子犬はもう遊んでいます',
      petGoesRest: '🐾 子犬は休憩に戻りました。/playpet でもう一度呼べます',
      petNotOut: '子犬はまだ出ていません。/playpet で呼び出してください',
      // v0.9.16.0+ web-mode client upload（GUI モードでは非表示）
      uploadMark: '[アップロード済]', uploadOk: '{ok} 件アップロードしました',
      uploading: '⏳ アップロード中… {f}',
      uploadSkip: '（{skip} 件スキップ）', uploadFail: 'アップロード失敗：{msg}',
      // v0.9.39.0+ — credentials tab
      tabCreds: '🔑 アカウント', credsTitle: '🔑 アカウントセット管理',
      credsHint: 'ログインが必要な全サービスで共有するアカウントセット（現在：ONVIF カメラ）。パスワードは環境変数参照を推奨（ストアに秘密を保存しない）；平文パスワードは base64 難読化保存のみで暗号化ではありません。この UI・MCP（cred_add / cred_list / …）・CLI（python cli.py creds …）で管理できます。',
      credsUser: 'ユーザー', credsSource: 'ソース', credsService: 'サービス',
      credsNotes: 'メモ', credsAddTitle: '追加 / 上書き',
      credsOverwrite: '同名を上書き', credsSave: '💾 保存',
      credsDelete: '🗑 削除', credsSaved: 'アカウントセットを保存しました',
      credsDeleted: 'アカウントセットを削除しました',
      credsConfirmDelete: 'このアカウントセットを削除しますか？',
      // v0.8.7+ スプラッシュ画面
      splashTitle: 'Divine Sense Agent',
      splashChecking: 'リソース確認中…',
      splashReady: '準備完了',
      splashDone: '完了 {n} / 3',
      splashSkipped: 'スキップ済み',
      splashSkip: 'スキップ →',
      readinessBackend: 'バックエンド API',
      readinessQdrant: 'Qdrant 接続',
      readinessLLM: 'LLM API キー',
      readinessMcp: 'MCP ツール初期化',
      readinessTimeout: 'タイムアウト',
      docsError: 'ファイル一覧取得失敗: {msg}',
      removeOneTitle: 'このファイルをインデックスから削除',
      removeSelected: '🗑 選択を削除 (0)',
      confirmRemoveOne: '"{name}" を Qdrant インデックスから完全に削除しますか? 元に戻せません。',
      confirmRemoveMany: '{n} 個のファイルを Qdrant インデックスから完全に削除しますか? 元に戻せません。',
      removedToast: '{files} ファイル、合計 {chunks} chunks を削除しました。',
      removedMissing: ' ({n} ファイルがインデックスに無し — タイプミス?)',
      cacheZone: 'Embedding キャッシュ',
      clearCacheTitle: 'Embedding キャッシュをクリア',
      clearCacheHint: 'メモリ LRU と永続 SQLite キャッシュを両方クリアします。次回の取り込み/チャットで embedding API が再呼び出しされます。',
      clearCache: '🧹 クリア',
      confirmClearCache: 'すべての embedding キャッシュをクリアしますか?',
      cacheCleared: '{lru} LRU + {disk} ディスクキャッシュを削除しました',
      auditBackends: '🔎 hash ベクトル監査',
      auditBackendsHint: '監査は読み取り専用。再埋め込みは対象 points で embedding バックエンドを再呼び出しします（確認必要）。',
      reembedHash: '♻️ hash 再埋め込み',
      backfillHeadings: '§ 見出しをバックフィル',
      backfillDone: 'バックフィル完了: {n} / {scanned} 点に heading_path を補完（エラー {errors}）',
      auditDone: '監査完了: {scanned} 点を走査、{hash} 点が hash ベクトル',
      reembedDone: '再埋め込み完了: {n} / {hash} 修復（hash のまま: {still}）',
      confirmReembed: 'すべての hash ベクトルを再埋め込みしますか（費用/時間はバックエンド次第）?',
      reloadZone: '設定ホットリロード',
      reloadTitle: '.env / mcp.json を再読込',
      reloadHint: '.env を再読込（型変換あり）、LLM provider チェーンをリセット、常駐サービス（スケジューラ / webhook / Telegram / ONVIF 監視）を有効条件に従って再起動し、MCP fleet を再接続します。環境変数の削除は再起動が必要です（dotenv は process env を消去しません）。',
      reloadBtn: '🔄 再読込',
      reloadDone: '再読込完了。変更: {changed}；サービス: {svc}',
      // MCP fleet editor (v0.8.0+)
      mcpEditorTitle: '🔌 MCP 設備(サードパーティ)',
      mcpEditorHint: 'アクティブな config/mcp.json 設備を編集します。保存で Manager を即時リロード — 次のチャットで新しいツールが使えます(再起動不要)。ローカルの Qdrant/Web 検索は常に有効で、ここでは編集できません。',
      mcpRefresh: '🔄 更新',
      mcpAdd: '+ 追加',
      mcpSaveAll: '💾 保存してリロード',
      mcpLoading: '読み込み中…',
      mcpServerCount: 'リモートサーバー {n} 件',
      mcpInvalid: '設定が無効',
      mcpValid: '設定が有効',
      mcpRestart: '再接続',
      mcpRestartHint: '切断して再接続',
      mcpEditTitle: 'MCP サーバーを編集',
      mcpFieldName: '名前',
      mcpFieldNamePh: 'my-server',
      mcpFieldNameHint: 'ツールのプレフィックスとして使用、設備内で一意である必要があります。',
      mcpFieldTransport: 'トランスポート',
      mcpFieldCommand: '起動コマンド',
      mcpFieldCommandPh: 'python -m my_mcp_server',
      mcpFieldCommandHint: '空白で分割して argv 配列として保存。',
      mcpFieldEnv: '環境変数(KEY=値、1行ずつ)',
      mcpFieldEnvPh: 'MY_TOKEN=',
      mcpFieldCwd: '作業ディレクトリ(任意)',
      mcpFieldCwdPh: '/path/to/server',
      mcpFieldUrl: 'エンドポイント URL',
      mcpFieldUrlPh: 'https://mcp.example.com/v1/mcp',
      mcpFieldHeaders: 'ヘッダー(KEY=値、1行ずつ)',
      mcpFieldHeadersPh: 'Authorization=Bearer ${MY_TOKEN}',
      mcpFieldHeadersHint: '${ENV_VAR} は接続時に置換されます。',
      mcpFieldNameRequired: '名前は必須です',
      mcpFieldCommandRequired: 'コマンドは必須です',
      mcpFieldUrlRequired: 'URL は必須です',
      mcpFieldNameDuplicate: '{name} というサーバーは既に存在します',
      mcpSaved: '{n} サーバーを保存、合計ツール数 {tools}',
      mcpRestarted: '{name} を再接続しました(ツール {tools})',
      mcpParseError: '設定ファイルの解析に失敗:{msg}',
      confirmRemoveMcp: 'MCP サーバー "{name}" を削除しますか?',
      // MCP エディタ拡張 (v0.8.2+)
      mcpToggleRaw: '📝 Raw JSON',
      mcpOverviewToggle: '📋 ツール一覧',
      mcpTest: '🧪 テスト',
      mcpTestHint: '接続状態とこのサーバーのツール一覧を確認',
      mcpTestTitle: '{name} をテスト',
      mcpTestCount: 'ツール {n} 件',
      mcpRawTitle: '📝 config/mcp.json を直接編集 (raw JSON)',
      mcpRawHint: 'ファイル全体を一度に編集。保存時に完全な validation + atomic write を実行。バッチ編集、JSON 貼り付け、_comment 例の編集に最適。',
      mcpRawFormat: '🎨 整形',
      mcpRawCancel: 'キャンセル',
      mcpRawSave: '💾 raw JSON を保存',
      mcpOverviewTitle: '📋 拡張 MCP が提供する機能 (ツール一覧)',
      mcpOverviewHint: '現在接続 / 登録済みの全 MCP ツールをサーバー別に表示。ローカルの Qdrant + Web 検索ホストと、config/mcp.json で有効な全リモートサーバーを含みます。次のチャットはこの一覧からツールを選びます。',
      mcpOverviewLoading: '読み込み中…',
      mcpOverviewEmpty: 'ツールが登録されていません — サーバーを追加するか QdrantLocalHost を再起動してください。',
      mcpOverviewServerFailed: 'サーバーの接続に失敗しました。利用可能なツールはありません。',
      // Notes ノート (v0.9.95.0+)
      tabNotes: '📝 ノート',
      notesTitle: '📝 ノート',
      notesHint: 'ローカルノート（markdown）— 追加/削除/編集/検索（日付・タイトル・本文）/カテゴリ。リストはページごとに非同期読み込み — 全件一括表示はしません。',
      notesSearchPh: 'タイトル／本文を検索…',
      notesAllCats: '全カテゴリ',
      notesSearch: '🔍 検索',
      notesNew: '➕ 新規',
      notesEmpty: '（ノートはまだありません — 「新規」から始めてください）',
      notesPageOf: '{p} / {pages} ページ · 全 {total} 件',
      notesTitlePh: 'タイトル…',
      notesCatPh: 'カテゴリ（任意）',
      notesBodyPh: 'markdown 本文…（GFM対応：太字、リスト、表、コード）',
      notesPreview: '👁 プレビュー',
      notesEdit: '✏️ 編集',
      notesSave: '💾 保存',
      notesDelete: '🗑 削除',
      notesCancel: '✖ 閉じる',
      notesSaved: '保存しました',
      notesSyncBtn: '🔄 オフライン編集を同期（{n}）',
      notesSynced: 'オフライン編集を {n} 件同期しました（{f} 件保留）',
      notesOfflineBanner: '📡 オフライン — localStorage に暫存したリストを表示',
      notesOfflineStash: 'オフライン暫存完了——オンラインで自動同期',
      notesStillOffline: 'まだオフライン——暫存に保持されています、後でもう一度',
      notesUpToDate: '同期待ちのオフライン編集はありません',
      notesLoadOffline2: 'オフラインかつ暫存なし — オンラインで自動読込',
      notesHw: '✍️ 手書き',
      hwTitle: '✍️ 手書きメモ',
      hwHint: '指／スタイラス／マウスで下に書く',
      hwClear: '🧹 クリア',
      hwSave: '💾 保存して OCR',
      hwOcring: 'ホストへ送信し OCR 中…（保存→転写→索引）',
      hwDone: '手書きをアーカイブし索引化（{n} チャンク）',
      hwEmpty: 'キャンバスが空 — まず何か書いてください',
      hwOffline: 'オフラインでは手書き作成不可（ホストOCR 送信必要）— 先に接続を回復',
      hwPageOf: 'ページ {p} / {n}',
      hwPageAdd: '➕ ページ追加',
      hwFull: '⛶ 全画面',
      hwBg: '🖼 背景',
      hwBgClear: '✕ 背景解除',
      hwBgApplied: '背景を適用しました（キャンバスはリセット）',
      hwBgCleared: '背景を解除しました',
      hwViewOriginal: '手書き原文',
      hwPdf: '📄 PDF 取り込み',
      hwPdfImporting: '⏳ PDF ページ生成中… {f}',
      hwPdfDone: '{n} ページの PDF 下地を読み込みました',
      hwPdfFail: 'PDF 取り込み失敗：{m}',
      hwPen: '🖊 ペン', hwMarker: '🖍 蛍光', hwEraser: '🧽 消',
      notesDeleted: '削除しました',
      notesConfirmDelete: 'このノートを削除しますか？（元に戻せません）',
      notesTextPh: 'テキスト',
      notesExpandAll: '▾ すべて展開',
      notesCollapseAll: '▸ すべて折りたたむ',
      notesListShow: '📋 一覧を表示',
      notesListHide: '📋 一覧を隠す',
      notesSbChars: '文字',
      notesSbLines: '行',
      notesSbSaved: 'ブラウザに自動保存',
      notesSbSavedAt: '{t} に保存済み',
      // Schedules (v0.8.0+)
      tabSchedules: '⏰ スケジュール',
      schedulesTitle: '⏰ スケジュール',
      schedulesHint: 'cron / インターバル / 単発実行で定義済みプロンプトを実行します。発火ごとにフルツール(web_search / mcp_server / ...)を利用でき、専用セッションに記録されます。daemon は Agent 内で常駐 — このタブで状態を確認できます。',
      schedulesAdd: '+ スケジュール追加',
      schedulesEnabledOnly: '有効のみ',
      schedulesLoading: '読み込み中…',
      schedulesStatusText: '{n} 件(有効 {enabled}) · daemon {daemon}',
      schedulesRunNow: '今すぐ実行',
      schedulesEnable: '有効化',
      schedulesDisable: '無効化',
      scheduleEditTitle: 'スケジュールを編集',
      scheduleFieldName: '名前',
      scheduleFieldPrompt: 'Prompt',
      scheduleFieldPromptHint: 'Agent.chat_turn 経由で実行 — web_search と登録済み MCP サーバーが利用可能です。',
      scheduleFieldType: 'スケジュール種別',
      scheduleFieldInterval: '間隔(秒)',
      scheduleFieldIntervalHint: '60 = 毎分、3600 = 毎時、86400 = 毎日。',
      scheduleFieldCron: 'Cron 式',
      scheduleFieldCronHint: '分 時 日 月 曜日 (0-6、日=0)。例: <code>0 9 * * *</code> 毎日 9 時、<code>*/15 * * * *</code> 15 分毎、<code>0 0 * * 1</code> 毎週月曜深夜。',
      scheduleFieldOnceAt: '実行日時 (ISO datetime)',
      scheduleFieldUseTools: 'MCP ツールを使う',
      scheduleFieldEnabled: '有効',
      scheduleFieldNameRequired: '名前は必須です',
      scheduleFieldPromptRequired: 'prompt は必須です',
      scheduleFieldIntervalRequired: '正の秒数を入力してください',
      scheduleFieldCronRequired: 'cron 式を入力してください',
      scheduleFieldOnceAtRequired: 'ISO datetime を入力してください',
      scheduleSaved: 'スケジュール {id} を保存しました',
      scheduleToggled: 'スケジュール {id} を {state} にしました',
      scheduleDeleted: 'スケジュール {id} を削除しました',
      scheduleFired: 'スケジュール {id} を発火しました — session={session}',
      confirmDeleteSchedule: 'スケジュール "{id}" を削除しますか? 履歴も削除されます。',
    },
  };

  let currentLang = (navigator.language || 'zh-TW');
  if (currentLang === 'zh-TW' || currentLang.startsWith('zh-Hant')) currentLang = 'zh-TW';
  else if (currentLang.startsWith('zh')) currentLang = 'zh-CN';
  else if (currentLang.startsWith('ja')) currentLang = 'ja';
  else if (currentLang.startsWith('en')) currentLang = 'en';
  else currentLang = 'zh-TW';
  try {
    const saved = localStorage.getItem('qdrant-rag.lang');
    if (saved && I18N[saved]) currentLang = saved;
  } catch (_) {}

  function t(key, vars) {
    vars = vars || {};
    const dict = I18N[currentLang] || I18N['zh-TW'];
    let s = dict[key];
    if (s === undefined) s = (I18N['en'][key] || key);
    return String(s).replace(/\{(\w+)\}/g, function (_, k) { return (k in vars ? vars[k] : '{' + k + '}'); });
  }

  function applyI18n() {
    document.documentElement.lang = currentLang;
    document.querySelectorAll('[data-i18n]').forEach(function (el) {
      el.textContent = t(el.dataset.i18n);
    });
    document.querySelectorAll('[data-i18n-ph]').forEach(function (el) {
      el.placeholder = t(el.dataset.i18nPh);
    });
    // v0.9.97.0 — title-attribute i18n (theme picker tooltip);
    // v0.9.126.4 — also mirrors onto aria-label (icon-only buttons in the
    // handwriting pad rely on title for both tooltip and screen readers).
    document.querySelectorAll('[data-i18n-title]').forEach(function (el) {
      el.title = t(el.dataset.i18nTitle);
      el.setAttribute('aria-label', t(el.dataset.i18nTitle));
    });
    const sel = document.getElementById('lang-select');
    if (sel) sel.value = currentLang;
  }

  function setLang(lang) {
    if (!I18N[lang]) return;
    currentLang = lang;
    try { localStorage.setItem('qdrant-rag.lang', lang); } catch (_) {}
    applyI18n();
    if (typeof renderSessionList === 'function') renderSessionList();
    if (typeof refreshAdmin === 'function') refreshAdmin();
    // v0.9.9.4+ — the per-bubble time headers carry t() labels, so
    // a language switch re-renders the transcript. renderChat() is
    // a pure rebuild from chatSession (idempotent, no network).
    if (typeof renderChat === 'function') renderChat();
  }


  // ===== Toast =====
  function toast(msg, level, timeoutMs) {
    const c = document.getElementById('toast-container');
    if (!c) { console.log('[toast]', msg); return; }
    const div = document.createElement('div');
    div.className = 'toast ' + (level || 'info');
    div.textContent = msg;
    c.appendChild(div);
    const ms = timeoutMs || 4000;
    setTimeout(function () {
      div.style.opacity = '0';
      div.style.transition = 'opacity 0.3s';
      setTimeout(function () { div.remove(); }, 300);
    }, ms);
  }

  // ===== Theme（v0.9.97.0 — 三態選擇：auto / light / dark） =====
  function applyTheme(theme) {
    document.documentElement.dataset.theme = theme;
    try { localStorage.setItem('qdrant-rag.theme', theme); } catch (_) {}
  }
  function effectiveTheme() {
    try {
      const saved = localStorage.getItem('qdrant-rag.theme');
      if (saved === 'light' || saved === 'dark' || saved === 'auto') {
        return saved;
      }
    } catch (_) {}
    return 'auto';
  }
  /* ── 外觀設定（v0.9.99.0 — 背景圖 / 自選色調 / 字型） ──────────
     全部存在 localStorage['qdrant-rag.appearance']（單一 JSON），
     套用方式 = documentElement.style 的 CSS 變數覆寫（inline 變數
     優先於主題 palette，theme auto/light/dark 切換不受影響）。 */

  const APPEAR_KEY = 'qdrant-rag.appearance';
  const APPEAR_BG_MAX_CHARS = 2600000; // ~2.5 MB base64 guard

  function _appearanceState() {
    try {
      const raw = localStorage.getItem(APPEAR_KEY);
      if (raw) return JSON.parse(raw);
    } catch (_) {}
    return {};
  }

  function _saveAppearance(st) {
    try { localStorage.setItem(APPEAR_KEY, JSON.stringify(st)); }
    catch (e) {
      // quota (usually the bg dataURL) — retry WITHOUT the image.
      // v0.9.121.0 FIX: the old fallback targeted a NON-EXISTENT
      // state key — the retry re-saved the same oversized
      // payload, failed too, and the whole appearance change was
      // silently lost (the remote 「修改無效」 case).
      try {
        const slim = Object.assign({}, st, { bg: '' });
        slim.bgIsData = false;
        localStorage.setItem(APPEAR_KEY, JSON.stringify(slim));
        toast('⚠️ ' + t('appearanceBgDropped'));
      } catch (_) {
        toast('⚠️ ' + t('appearanceSaveFailed'));
      }
    }
  }

  function _hexToRgba(hex, alpha) {
    let h = String(hex || '').trim().replace('#', '');
    if (h.length === 3) h = h.split('').map(c => c + c).join('');
    if (!/^[0-9a-fA-F]{6}$/.test(h)) return null;
    const r = parseInt(h.slice(0, 2), 16);
    const g = parseInt(h.slice(2, 4), 16);
    const b = parseInt(h.slice(4, 6), 16);
    return `rgba(${r},${g},${b},${alpha})`;
  }

  function _applyAppearance(st) {
    // v0.9.121.0 — apply failures SURFACE (console + toast): a remote
    // browser that dropped the background used to fail silently with
    // zero diagnosis.
    try {
      _applyAppearanceInner(st);
    } catch (e) {
      console.warn('[appearance] apply failed:', e);
      try { toast('⚠️ appearance apply failed: '
        + ((e && e.message) || e), 'error', 6000); } catch (_) {}
    }
  }

  function _applyAppearanceInner(st) {
    const root = document.documentElement;
    // 1) 背景圖：body background（佈放格式 st.bgFit：cover=滿格 /
    //    contain=完整顯示 / center=原尺置中 / tile=重複）+ 暗化 + 半透明面板
    if (st.bg) {
      const dim = Math.min(0.8, Math.max(0, Number(st.bgDim) || 0));
      const fit = st.bgFit || 'cover';
      document.body.classList.add('has-bg');
      const grad = `linear-gradient(rgba(0,0,0,${dim}), rgba(0,0,0,${dim})),`;
      document.body.style.backgroundImage =
        `${grad} url("${st.bg.replace(/"/g, '%22')}")`;
      // 佈放格式 → data-body-bgfit + CSS 按 data 屬性設定 size/pos/repeat
      document.body.dataset.bgFit = fit;
      const cs = getComputedStyle(document.documentElement);
      const bg = _hexToRgba(cs.getPropertyValue('--bg'), 0.86);
      const bg2 = _hexToRgba(cs.getPropertyValue('--bg-2'), 0.92);
      const bg3 = _hexToRgba(cs.getPropertyValue('--bg-3'), 0.96);
      if (bg) root.style.setProperty('--bg', bg);
      if (bg2) root.style.setProperty('--bg-2', bg2);
      if (bg3) root.style.setProperty('--bg-3', bg3);
    } else {
      document.body.classList.remove('has-bg');
      delete document.body.dataset.bgFit;
      document.body.style.backgroundImage = '';
      root.style.removeProperty('--bg');
      root.style.removeProperty('--bg-2');
      root.style.removeProperty('--bg-3');
    }
    // 2) 自選色調（--primary + --accent 同色統一）
    if (st.accent) {
      root.style.setProperty('--primary', st.accent);
      root.style.setProperty('--accent', st.accent);
    } else {
      root.style.removeProperty('--primary');
      root.style.removeProperty('--accent');
    }
    // 3) 字型
    if (st.font) root.style.setProperty('--user-font', st.font);
    else root.style.removeProperty('--user-font');
    if (st.fontSize) root.style.setProperty('--user-font-size', st.fontSize);
    else root.style.removeProperty('--user-font-size');
    if (st.mono) root.style.setProperty('--user-mono', st.mono);
    else root.style.removeProperty('--user-mono');
  }

  function loadAppearance() { _applyAppearance(_appearanceState()); }

  function openAppearance() {
    const st = _appearanceState();
    const dlg = document.getElementById('appearance-dialog');
    if (!dlg) return;
    document.getElementById('ap-accent').value = st.accent || '#2563eb';
    document.getElementById('ap-font').value = st.fontPreset || '';
    document.getElementById('ap-font-custom').value =
      st.fontPreset ? '' : (st.font || '');
    document.getElementById('ap-font-size').value = st.fontSize || '';
    document.getElementById('ap-mono').value = st.mono || '';
    document.getElementById('ap-bg-url').value =
      st.bgIsUrl ? (st.bg || '') : '';
    document.getElementById('ap-bg-dim').value = st.bgDim || 0;
    document.getElementById('ap-bg-dim-val').textContent =
      (st.bgDim || 0) + '%';
    const fitSel = document.getElementById('ap-bg-fit');
    if (fitSel) fitSel.value = st.bgFit || 'cover';
    dlg.hidden = false;
  }

  function _appearanceFromInputs() {
    const st = _appearanceState();
    const custom =
      document.getElementById('ap-font-custom').value.trim();
    st.fontPreset = document.getElementById('ap-font').value || '';
    st.font = custom || document.getElementById('ap-font').value || '';
    st.fontSize = document.getElementById('ap-font-size').value || '';
    st.mono = document.getElementById('ap-mono').value.trim();
    return st;
  }

  function bindAppearance() {
    const btn = document.getElementById('appearance-btn');
    if (!btn) {
      // v0.9.121.0 — missing button = stale cached page: say so.
      console.warn('[appearance] #appearance-btn missing (stale'
        + ' page? hard-refresh)');
      return;
    }
    if (btn.dataset.bound) return;
    btn.dataset.bound = '1';
    btn.addEventListener('click', openAppearance);
    // v0.9.121.0 — per-part binding with NAMED failures: a throw or
    // a missing element used to break every remaining bind silently
    // (the remote 「修改無效」 case = zero diagnosis).
    const failed = [];
    const _b = function (id, fn) {
      try {
        const el = document.getElementById(id);
        if (!el) { throw new Error('element missing'); }
        fn(el);
      } catch (e) {
        console.warn('[appearance] bind failed: #' + id, e);
        failed.push(id);
      }
    };
    _b('appearance-close', function (el) {
      el.addEventListener('click', function () {
        document.getElementById('appearance-dialog').hidden = true;
      });
    });
    try {
      _bindAppearanceBg();
    } catch (e) {
      console.warn('[appearance] bg bind failed:', e);
      failed.push('bg');
    }
    try {
      _bindAppearanceAccent();
    } catch (e) {
      console.warn('[appearance] accent bind failed:', e);
      failed.push('accent');
    }
    try {
      _bindAppearanceFont();
    } catch (e) {
      console.warn('[appearance] font bind failed:', e);
      failed.push('font');
    }
    _b('ap-reset', function (el) {
      el.addEventListener('click', function () {
        _saveAppearance({});
        _applyAppearance({});
        openAppearance();
        toast('♻️ ' + t('appearanceReset'));
      });
    });
    if (failed.length) {
      try { toast('⚠️ ' + t('appearanceBindFail')
        + ' ' + failed.join(', '), 'error', 6000); } catch (_) {}
    }
  }

  function _bgUrlFor(v) {
    // v0.9.121.0 — normalize the bg URL field so it works on EVERY
    // browser (remote devices included). Accepts http(s):// • data: •
    // a sandbox uploads path (C:/…/uploads/x.jpg、uploads/x.jpg、
    // /uploads/x.jpg、a 「[已上傳] <path>」-mark line) → the latter
    // group is converted to the server-streamed
    // ``/download?path=…`` endpoint (a REMOTE browser cannot load a
    // server-local file path — this is the actual remote-mode gap).
    // Returns '' for empty input, null when refused.
    let s = String(v || '').trim();
    const mMark = s.match(
      /^\[(?:已上傳|已上传|uploaded|アップロード済)\]\s*(\S+)/i);
    if (mMark) s = mMark[1];
    if (!s) return '';
    if (/^https?:\/\//i.test(s) || s.startsWith('data:')) return s;
    if (/^[A-Za-z]:[\\/].+/.test(s) || /^uploads\//.test(s)
        || /^\/uploads\//.test(s)) {
      s = s.replace(/\\/g, '/');
      return '/download?path=' + encodeURIComponent(s);
    }
    if (s.charAt(0) === '/') return s;   // same-origin path
    return null;                          // refused (hint toast)
  }

  function _bindAppearanceBg() {
    // 背景檔案 / URL / 暗化 / 佈放格式（cover=滿格縮放、contain=
    // 完整顯示、center=原尺置中、tile=重複）— 套用即存。
    const _applyFromUi = function (override) {
      // v0.9.121.1 — THE ROOT-CAUSE FIX for 「選本地圖檔修改無效」:
      // this function re-read the state from localStorage, so the
      // just-picked bg set by the FILE/URL handlers was CLOBBERED
      // before apply+save (the ✅ toast still fired). Handlers now
      // pass the freshly-picked values as an OVERRIDE merge.
      const st = Object.assign(_appearanceState(), override || {});
      st.bgFit =
        (document.getElementById('ap-bg-fit') || {}).value || 'cover';
      _applyAppearance(st); _saveAppearance(st);
    };
    // 背景：檔案 → dataURL（≤1.2MB 直接用原檔 dataURL；更大 → canvas
    // 縮圖 ≤1920px → JPEG；解碼/縮圖異常退回原檔 dataURL）。
    // v0.9.121.1 — the bg reaches apply/save via the OVERRIDE merge
    // (previously _applyFromUi() re-read localStorage and CLOBBERED
    // the just-picked bg — 選本地圖檔修改無效的根因)。
    document.getElementById('ap-bg-file').addEventListener(
      'change', function () {
        const file = this.files && this.files[0];
        if (!file) return;
        const reader = new FileReader();
        reader.onload = function () {
          const dataUrl = reader.result;
          const applyPicked = function (dUrl) {
            if (!dUrl || String(dUrl).length > APPEAR_BG_MAX_CHARS) {
              toast('⚠️ ' + t('appearanceBgTooBig'), 'error', 6000);
              return;
            }
            _applyFromUi({ bg: String(dUrl), bgIsUrl: false,
              bgIsData: true });
            toast('✅ ' + t('appearanceSaved'));
          };
          if (file.size <= 1.2 * 1024 * 1024) {
            applyPicked(dataUrl);     // direct — no canvas quirks
            return;
          }
          const img = new Image();
          img.onload = function () {
            try {
              if (!img.width) { applyPicked(dataUrl); return; }
              const maxW = 1920;
              const scale = Math.min(1, maxW / img.width);
              const cv = document.createElement('canvas');
              cv.width = Math.round(img.width * scale);
              cv.height = Math.round(img.height * scale);
              cv.getContext('2d').drawImage(
                img, 0, 0, cv.width, cv.height);
              const url = cv.toDataURL('image/jpeg', 0.85);
              if (url && url.length > 100
                  && url.length <= APPEAR_BG_MAX_CHARS) {
                applyPicked(url);
                return;
              }
            } catch (e) {
              console.warn('[appearance] canvas re-encode failed:', e);
            }
            applyPicked(dataUrl);     // fall back to the original
          };
          img.onerror = function () {
            toast('⚠️ image load failed', 'error', 6000);
          };
          img.src = dataUrl;
        };
        reader.readAsDataURL(file);
      });
    // 背景：URL（v0.9.121.0 — path/URL 正規化 + 拒絕時明確提示；
    // sandbox 路徑轉 /download 串流 = 遠端瀏覽器可載入）
    document.getElementById('ap-bg-url-apply').addEventListener(
      'click', function () {
        const raw =
          document.getElementById('ap-bg-url').value.trim();
        const url = _bgUrlFor(raw);
        const st = _appearanceState();
        if (!url) {
          toast('⚠️ ' + t('appearanceBgUrlBad'), 'error', 6000);
          return;
        }
        st.bg = url; st.bgIsUrl = true; st.bgIsData = false;
        _applyFromUi({ bg: url, bgIsUrl: true, bgIsData: false });
        toast('✅ ' + t('notesSaved'));
      });
    // 暗化
    document.getElementById('ap-bg-dim').addEventListener(
      'input', function () {
        document.getElementById('ap-bg-dim-val').textContent =
          this.value + '%';
        _applyFromUi();
      });
    // 佈放格式
    document.getElementById('ap-bg-fit').addEventListener(
      'change', _applyFromUi);
    document.getElementById('ap-bg-clear').addEventListener(
      'click', function () {
        const st = _appearanceState();
        st.bg = ''; st.bgDim = 0;
        st.bgIsUrl = false; st.bgIsData = false;
        _applyAppearance(st); _saveAppearance(st);
      });
  }

  function _bindAppearanceAccent() {
    document.getElementById('ap-accent').addEventListener(
      'input', function () {
        const st = _appearanceState();
        st.accent = this.value;
        _applyAppearance(st); _saveAppearance(st);
      });
    document.querySelectorAll('.ap-swatch').forEach(function (b) {
      b.addEventListener('click', function () {
        const st = _appearanceState();
        st.accent = b.dataset.color;
        document.getElementById('ap-accent').value = st.accent;
        _applyAppearance(st); _saveAppearance(st);
      });
    });
    document.getElementById('ap-accent-reset').addEventListener(
      'click', function () {
        const st = _appearanceState();
        st.accent = '';
        _applyAppearance(st); _saveAppearance(st);
      });
  }

  function _bindAppearanceFont() {
    document.getElementById('ap-font').addEventListener(
      'change', function () {
        const st = _appearanceFromInputs();
        _applyAppearance(st); _saveAppearance(st);
      });
    document.getElementById('ap-font-custom').addEventListener(
      'input', function () {
        clearTimeout(this._deb);
        this._deb = setTimeout(function () {
          const st = _appearanceFromInputs();
          _applyAppearance(st); _saveAppearance(st);
        }, 300);
      });
    document.getElementById('ap-font-size').addEventListener(
      'change', function () {
        const st = _appearanceFromInputs();
        _applyAppearance(st); _saveAppearance(st);
      });
    document.getElementById('ap-mono').addEventListener(
      'input', function () {
        clearTimeout(this._deb);
        this._deb = setTimeout(function () {
          const st = _appearanceFromInputs();
          _applyAppearance(st); _saveAppearance(st);
        }, 300);
      });
  }

  function setupTheme() {
    applyTheme(effectiveTheme());
    if (window.matchMedia) {
      const mq = window.matchMedia('(prefers-color-scheme: dark)');
      const handler = function () { applyTheme(effectiveTheme()); };
      if (mq.addEventListener) mq.addEventListener('change', handler);
    }
    // v0.9.97.0 — three-state picker replaces the v0.8.x checkbox
    // (which could never return to follow-the-system 'auto'). The
    // localStorage key 'qdrant-rag.theme' is kept: existing users'
    // saved light/dark choices keep working.
    const sel = document.getElementById('theme-select');
    if (sel) {
      sel.value = effectiveTheme();
      sel.addEventListener('change', function () {
        applyTheme(sel.value);
      });
    }
  }

  // ===== Tabs / status =====
  function switchTab(tabName) {
    document.querySelectorAll('.tab').forEach(function (el) {
      el.classList.toggle('active', el.dataset.tab === tabName);
    });
    document.querySelectorAll('.panel').forEach(function (el) {
      el.classList.toggle('active', el.id === 'panel-' + tabName);
    });
    // v0.9.60.4 — populate the collection pickers when the user goes
    // DIRECTLY to the Search tab (they used to stay empty until the
    // Stats tab was visited first).
    if (tabName === 'search') refreshCollectionSelectors();
    if (tabName === 'stats') refreshStats();
    if (tabName === 'sessions') refreshSessions();
    if (tabName === 'schedules') refreshSchedules();
    if (tabName === 'notes') { notesLoad(1); }
    if (tabName === 'admin') refreshAdmin();
  }

  /* ── 筆記本 UI（v0.9.95.0+ — 本地筆記本 store；分頁瀏覽永不整表傾倒） ── */

  let notesPage = 1;
  let notesPageSize = 50;
  let notesEditingId = '';
  let notesListVisible = false; // v0.9.99.4 — list hidden by default

  function notesListApply() {
    const list = document.getElementById('notes-list');
    const tools = document.getElementById('notes-list-tools');
    const pager = document.getElementById('notes-pager');
    const btn = document.getElementById('notes-list-toggle');
    if (!list) return;
    list.hidden = !notesListVisible;
    if (tools) tools.hidden = !notesListVisible;
    if (pager) pager.hidden = !notesListVisible;
    if (btn) btn.textContent =
      notesListVisible ? t('notesListHide') : t('notesListShow');
  }

  function notesListToggle() {
    notesListVisible = !notesListVisible;
    notesListApply();
    if (notesListVisible) notesLoad(1);
  }

  function notesBind() {
    if (document.getElementById('notes-search-btn').dataset.bound) return;
    document.getElementById('notes-search-btn').dataset.bound = '1';
    const q = document.getElementById('notes-q');
    let deb = null;
    q.addEventListener('input', function () {
      clearTimeout(deb);
      deb = setTimeout(function () { notesLoad(1); }, 300); // async debounce
    });
    document.getElementById('notes-search-btn').addEventListener(
      'click', function () { notesLoad(1); });
    document.getElementById('notes-cat').addEventListener(
      'change', function () { notesLoad(1); });
    document.getElementById('notes-from').addEventListener(
      'change', function () { notesLoad(1); });
    document.getElementById('notes-to').addEventListener(
      'change', function () { notesLoad(1); });
    document.getElementById('notes-new-btn').addEventListener(
      'click', function () { notesOpen(''); });
    document.getElementById('notes-list-toggle').addEventListener(
      'click', notesListToggle);
    document.getElementById('notes-expand-all').addEventListener(
      'click', function () {
        document.querySelectorAll('#notes-list .note-row')
          .forEach(function (row) { row.classList.remove('collapsed'); });
      });
    document.getElementById('notes-collapse-all').addEventListener(
      'click', function () {
        document.querySelectorAll('#notes-list .note-row')
          .forEach(function (row) { row.classList.add('collapsed'); });
      });
    document.getElementById('notes-prev').addEventListener(
      'click', function () { notesLoad(notesPage - 1); });
    document.getElementById('notes-next').addEventListener(
      'click', function () { notesLoad(notesPage + 1); });
    document.getElementById('ne-save').addEventListener('click', notesSave);
    document.getElementById('ne-delete').addEventListener('click', notesDelete);
    document.getElementById('ne-cancel').addEventListener('click', notesClose);
    // v0.9.125.0 — handwriting wiring
    document.getElementById('notes-hw-btn').addEventListener(
      'click', notesHwOpen);
    document.getElementById('hw-close').addEventListener(
      'click', notesHwClose);
    document.getElementById('hw-clear').addEventListener(
      'click', notesHwClear);
    document.getElementById('hw-save').addEventListener(
      'click', notesHwSave);
    if (!window.__NOTES_HW_BOUND__) {
      window.__NOTES_HW_BOUND__ = true;
      notesHwBind();
    }
    document.getElementById('ne-preview-btn').addEventListener(
      'click', notesTogglePreview);
    document.querySelectorAll('.ne-btn').forEach(function (btn) {
      btn.addEventListener('click', function () {
        notesMdInsert(btn.dataset.md);
      });
    });
    document.getElementById('ne-body').addEventListener('input', function () {
      notesSbUpdate();
      clearTimeout(deb);
      deb = setTimeout(function () {
        if (!document.getElementById('ne-preview').hidden) {
          notesRenderPreview();
        }
      }, 300);
    });
    document.getElementById('ne-body').addEventListener(
      'keyup', notesSbUpdate);
    document.getElementById('ne-body').addEventListener(
      'click', notesSbUpdate);
    // v0.9.124.9 — offline sync wiring (web mode only)
    const sbtn = document.getElementById('notes-sync-btn');
    if (sbtn && !sbtn.dataset.bound) {
      sbtn.dataset.bound = '1';
      sbtn.addEventListener('click', function () {
        notesOfflineSync();
      });
    }
    if (window.__WEB_MODE__ && !window.__NOTES_ONLINE_BOUND__) {
      window.__NOTES_ONLINE_BOUND__ = true;
      window.addEventListener('online', function () {
        if (notesPendingLoad().length) notesOfflineSync();
        else notesLoad(1);
      });
      window.addEventListener('offline', function () {
        notesOfflineBannerRefresh(true);
      });
    }
    notesOfflineBannerRefresh(false);
  }

  // ─── v0.9.124.9 — Notes offline-first (WEB MODE ONLY) ───────────
  // The web/phone Notes tab may lose its LAN/server connection. This
  // layer keeps the tab usable offline: the last successful
  // notes_page payload is cached in localStorage; saves/deletes that
  // fail because the bridge is unreachable go into a pending FIFO
  // queue; when the bridge answers again (auto on the `online`
  // event, or via the 🔄 button) the queue is replayed in order —
  // new notes are CREATED then (the server assigns the id). All of
  // this is a no-op in pywebview desktop mode (the bridge IS the
  // host process; there is no offline there).
  // Keys: notes_cache_v1 (last list payload) · notes_pending_v1
  // (FIFO ops {op:'save'|'create'|'delete', payload, edited_at}) ·
  // notes_conflict_backup_v1 (pre-overwrite server copies, 20 ring).
  function notesOfflineActive() {
    return !!(window.__WEB_MODE__
      && typeof localStorage !== 'undefined');
  }
  function notesOfflineBannerRefresh(offline) {
    const ban = document.getElementById('notes-offline-banner');
    const btn = document.getElementById('notes-sync-btn');
    if (!ban || !btn) return;
    const n = notesPendingLoad().length;
    ban.hidden = !offline;
    if (offline) {
      ban.textContent = (t('notesOfflineBanner')
        + (n ? ' — ' + t('notesSyncBtn').replace('{n}', n) : ''));
    }
    btn.hidden = !window.__WEB_MODE__;
    btn.textContent = t('notesSyncBtn').replace('{n}', n);
  }
  function notesCacheSave(r) {
    if (!r) return;
    try { localStorage.setItem('notes_cache_v1', JSON.stringify(r)); }
    catch (_) { /* quota / private mode — non-fatal */ }
  }
  function notesCacheGet() {
    try {
      const s = localStorage.getItem('notes_cache_v1');
      return s ? JSON.parse(s) : null;
    } catch (_) { return null; }
  }
  function notesPendingLoad() {
    try {
      return JSON.parse(
        localStorage.getItem('notes_pending_v1') || '[]');
    } catch (_) { return []; }
  }
  function notesPendingSave(q) {
    try {
      localStorage.setItem('notes_pending_v1',
        JSON.stringify(q.slice(0, 500)));
    } catch (_) { /* non-fatal */ }
    notesOfflineBannerRefresh(false);
  }
  function notesOfflineEnqueue(op) {
    const q = notesPendingLoad();
    q.push(op);
    notesPendingSave(q);
  }
  // LWW conflict safety net: BEFORE our queued save overwrites a
  // server note, keep the server's current copy in a 20-ring backup
  // so an accidental clobber is always recoverable by hand.
  async function notesConflictBackupBeforeSave(payload) {
    try {
      const id = (payload && payload.note_id) || '';
      if (!id) return;
      const cur = await api.safeCall('notes_get', { note_id: id });
      if (!cur || cur.error) return;
      const key = 'notes_conflict_backup_v1';
      const arr = JSON.parse(localStorage.getItem(key) || '[]')
        || [];
      arr.push({
        note_id: id, server: cur,
        at: new Date().toISOString(),
      });
      while (arr.length > 20) arr.shift();
      localStorage.setItem(key, JSON.stringify(arr));
    } catch (_) { /* best-effort safety net */ }
  }
  let notesSyncBusy = false;
  async function notesOfflineSync() {
    if (!notesOfflineActive() || notesSyncBusy) return;
    const q0 = notesPendingLoad();
    if (!q0.length) { toast('✅ ' + t('notesUpToDate')); return; }
    notesSyncBusy = true;
    try {
      const probe = await api.safeCall('notes_categories');
      if (!probe) {
        toast('⚠️ ' + t('notesStillOffline'));
        return;
      }
      let done = 0, fail = 0;
      const remain = [];
      for (const op of q0) {
        let r = null;
        if (op.op === 'save') {
          await notesConflictBackupBeforeSave(op.payload);
          r = await api.safeCall('notes_save', op.payload);
        } else if (op.op === 'create') {
          // the server assigns the id — empty note_id
          r = await api.safeCall('notes_save', {
            title: op.payload.title,
            body: op.payload.body,
            category: op.payload.category,
          });
        } else if (op.op === 'delete') {
          r = await api.safeCall('notes_delete', op.payload);
        } else {
          done += 1; // unknown op — drop it honestly
          continue;
        }
        if (r && !r.error) done += 1;
        else { fail += 1; remain.push(op); }
      }
      notesPendingSave(remain);
      toast(done || fail
        ? '🔄 ' + t('notesSynced', { n: done, f: fail })
        : '⚠️ ' + t('notesStillOffline'));
      notesLoad(1);
    } finally {
      notesSyncBusy = false;
      notesOfflineBannerRefresh(false);
    }
  }

  async function notesLoad(page) {
    notesBind();
    notesPage = Math.max(1, page || 1);
    const r = await api.safeCall('notes_page', {
      page: notesPage,
      page_size: notesPageSize,
      query: document.getElementById('notes-q').value.trim(),
      category: document.getElementById('notes-cat').value,
      date_from: document.getElementById('notes-from').value,
      date_to: document.getElementById('notes-to').value,
    });
    const list = document.getElementById('notes-list');
    if (!r) {
      // v0.9.124.9 — bridge unreachable (web offline): render the
      // last cached pages payload + pending offline-creates under
      // the offline banner. Zero server round-trips happen here.
      const cache = notesCacheGet() || {};
      const pend = notesPendingLoad()
        .filter(function (o) { return o.op === 'create'; })
        .map(function (o, i) {
          return {
            id: 'pending:' + i,
            title: o.payload.title,
            category: o.payload.category,
            updateTime: o.edited_at || '',
            text: o.payload.body,
          };
        });
      if (!(cache.notes || []).length && !pend.length) {
        list.textContent = '⚠️ ' + t('notesLoadOffline2');
        notesOfflineBannerRefresh(true);
        return;
      }
      notesOfflineBannerRefresh(true);
      notesRender(Object.assign({}, cache, {
        notes: (cache.notes || []).concat(pend),
        total: (cache.total || 0) + pend.length,
        categories: cache.categories || [],
      }));
      return;
    }
    notesCacheSave(r);
    notesOfflineBannerRefresh(false);
    if (r.error) {
      list.textContent = '⚠️ ' + r.error;
      return;
    }
    notesRender(r);
  }

  function notesRender(r) {
    const list = document.getElementById('notes-list');
    if (r.error) {
      list.textContent = '⚠️ ' + r.error;
      return;
    }
    list.replaceChildren();
    if (!r.total) {
      const empty = document.createElement('div');
      empty.className = 'notes-empty';
      empty.textContent = t('notesEmpty');
      list.appendChild(empty);
    }
    (r.notes || []).forEach(function (n) {
      const row = document.createElement('div');
      row.className = 'note-row';
      const head = document.createElement('div');
      head.className = 'note-row-head';
      const title = document.createElement('span');
      title.className = 'note-title';
      title.textContent = n.title || '(no title)';
      const meta = document.createElement('span');
      meta.className = 'note-meta';
      meta.textContent = (n.category ? '[' + n.category + '] ' : '')
        + (n.updateTime || '').replace('T', ' ').slice(0, 16);
      // v0.9.99.3 — 合併折疊：snippet 可折疊（點標題列展開/收合）
      row.classList.add('collapsed');
      const body = document.createElement('div');
      body.className = 'note-snippet md';
      body.innerHTML = md((n.text || '').slice(0, 160));
      row.appendChild(head);
      head.appendChild(title);
      head.appendChild(meta);
      row.appendChild(body);
      head.addEventListener('click', function (ev) {
        ev.stopPropagation();
        row.classList.toggle('collapsed');
      });
      row.addEventListener('click', function () { notesOpen(n.id); });
      list.appendChild(row);
    });
    const total = r.total || 0;
    const pages = Math.max(1, Math.ceil(total / notesPageSize));
    notesPage = Math.min(Math.max(1, notesPage), pages);
    document.getElementById('notes-pageinfo').textContent =
      t('notesPageOf').replace('{p}', notesPage).replace('{pages}', pages)
        .replace('{total}', String(total));
    document.getElementById('notes-prev').disabled = notesPage <= 1;
    document.getElementById('notes-next').disabled = notesPage >= pages;
    notesListApply(); // v0.9.99.4 — keep list hidden unless toggled on
    const sel = document.getElementById('notes-cat');
    const cur = sel.value;
    sel.replaceChildren(Object.assign(document.createElement('option'),
      { value: '', text: t('notesAllCats') }));
    const dl = document.getElementById('ne-cats');
    dl.replaceChildren();
    (r.categories || []).forEach(function (c) {
      const opt = document.createElement('option');
      opt.value = c.name; opt.textContent = c.name + ' (' + c.count + ')';
      sel.appendChild(opt);
      const d = document.createElement('option');
      d.value = c.name; dl.appendChild(d);
    });
    sel.value = cur;
  }

  function notesSbUpdate() {
    const ta = document.getElementById('ne-body');
    const bar = document.getElementById('ne-status-chars');
    if (!ta || !bar) return;
    const v = ta.value;
    document.getElementById('ne-status-chars').textContent =
      String(v.length);
    document.getElementById('ne-status-lines').textContent =
      String(v ? v.split('\n').length : 1);
    const posEl = document.getElementById('ne-status-pos');
    if (posEl && ta.selectionStart != null) {
      const before = v.slice(0, ta.selectionStart);
      const ln = before.split('\n');
      posEl.textContent = 'Ln ' + ln.length + ', Col '
        + (ln[ln.length - 1].length + 1);
    }
  }

  async function notesOpen(id) {
    notesBind();
    notesEditingId = id || '';
    document.getElementById('notes-editor').hidden = false;
    document.getElementById('ne-title').value = '';
    document.getElementById('ne-category').value = '';
    document.getElementById('ne-body').value = '';
    document.getElementById('ne-preview').hidden = true;
    document.getElementById('ne-body').hidden = false;
    document.getElementById('ne-preview-btn').textContent = t('notesPreview');
    document.getElementById('ne-delete').hidden = !id;
    notesSbUpdate();
    if (!id) return;
    let r = await api.safeCall('notes_get', { note_id: id });
    if (!r && notesOfflineActive()) {
      // v0.9.124.9 — offline: serve the editor from the cache, or
      // from the matching pending offline-create ('pending:<i>').
      if (String(id).indexOf('pending:') === 0) {
        const idx = parseInt(String(id).slice(8), 10);
        const pend = notesPendingLoad()
          .filter(function (o) { return o.op === 'create'; })[idx];
        r = pend ? { id: id, title: pend.payload.title,
          category: pend.payload.category,
          text: pend.payload.body, offline_created: true } : null;
      } else {
        const cache = notesCacheGet();
        const hit = cache && (cache.notes || []).filter(
          function (n) { return String(n.id) === String(id); })[0];
        r = hit ? { id: id, title: hit.title,
          category: hit.category, text: hit.text } : null;
      }
    }
    if (r && !r.error) {
      document.getElementById('ne-title').value = r.title || '';
      document.getElementById('ne-category').value = r.category || '';
      document.getElementById('ne-body').value = r.text || '';
      notesSbUpdate();
      notesHwShowArchived(r.text || '');
    } else {
      toast('⚠️ ' + (r && r.error ? r.error : 'load failed'));
    }
  }

  async function notesSave() {
    const title = document.getElementById('ne-title').value.trim();
    if (!title) { toast('⚠️ ' + t('notesTitlePh')); return; }
    const r = await api.safeCall('notes_save', {
      note_id: notesEditingId,
      title: title,
      body: document.getElementById('ne-body').value,
      category: document.getElementById('ne-category').value.trim(),
    });
    if (r && !r.error) {
      toast('✅ ' + t('notesSaved'));
      document.getElementById('ne-status-saved').textContent =
        t('notesSbSavedAt').replace('{t}',
          new Date().toTimeString().slice(0, 5));
      document.getElementById('notes-editor').hidden = true;
      notesLoad(notesPage);
    } else if (!r && notesOfflineActive()) {
      // v0.9.124.9 — bridge unreachable: stash the edit for the
      // reconnect sync. Editing a pending offline-create updates that
      // same queued op (it has no server id yet); everything else is
      // enqueued as save/create (create = server assigns the id ON
      // CONNECT, not now).
      const payload = { note_id: notesEditingId, title: title,
        body: document.getElementById('ne-body').value,
        category: document.getElementById('ne-category').value.trim() };
      if (String(notesEditingId).indexOf('pending:') === 0) {
        const idx = parseInt(String(notesEditingId).slice(8), 10);
        let seen = 0;
        const q = notesPendingLoad();
        for (const op of q) {
          if (op.op !== 'create') continue;
          if (seen === idx) {
            op.payload.title = title;
            op.payload.body = payload.body;
            op.payload.category = payload.category;
            op.edited_at = new Date().toISOString();
            break;
          }
          seen += 1;
        }
        notesPendingSave(q);
      } else {
        notesOfflineEnqueue({
          op: notesEditingId ? 'save' : 'create',
          payload: payload,
          edited_at: new Date().toISOString(),
        });
      }
      toast('📦 ' + t('notesOfflineStash'));
      document.getElementById('ne-status-saved').textContent =
        t('notesSbSavedAt').replace('{t}',
          new Date().toTimeString().slice(0, 5));
      document.getElementById('notes-editor').hidden = true;
      notesLoad(notesPage);
    } else {
      toast('⚠️ ' + (r && r.error ? r.error : 'save failed'));
    }
  }

  async function notesDelete() {
    if (!notesEditingId) return;
    if (!confirm(t('notesConfirmDelete'))) return;
    const r = await api.safeCall('notes_delete',
                                 { note_id: notesEditingId });
    if (r && !r.error) {
      toast('🗑 ' + t('notesDeleted'));
      document.getElementById('notes-editor').hidden = true;
      notesLoad(1);
    } else if (!r && notesOfflineActive()) {
      // v0.9.124.9 — offline delete: a queued offline-create is just
      // removed from the queue; an existing note enqueues a delete
      // op that replays on reconnect.
      if (String(notesEditingId).indexOf('pending:') === 0) {
        const idx = parseInt(String(notesEditingId).slice(8), 10);
        let seen = 0;
        const q = notesPendingLoad();
        let at = -1;
        for (let i = 0; i < q.length; i++) {
          if (q[i].op !== 'create') continue;
          if (seen === idx) { at = i; break; }
          seen += 1;
        }
        if (at >= 0) {
          q.splice(at, 1);
          notesPendingSave(q);
          toast('🗑 ' + t('notesDeleted'));
          document.getElementById('notes-editor').hidden = true;
          notesLoad(1);
          return;
        }
      } else {
        notesOfflineEnqueue({
          op: 'delete',
          payload: { note_id: notesEditingId },
          edited_at: new Date().toISOString(),
        });
        toast('📦 ' + t('notesOfflineStash'));
        document.getElementById('notes-editor').hidden = true;
        notesLoad(1);
        return;
      }
      notesLoad(1);
    } else {
      toast('⚠️ ' + (r && r.error ? r.error : 'delete failed'));
    }
  }

  // ─── v0.9.125.0 — handwriting note (canvas → host OCR →
  // Archive + Notes note + Qdrant index). Draw = Pointer Events with
  // quadratic mid-point smoothing; the canvas is DPI-scaled; sending
  // = a base64 data-URL to Api.notes_handwriting_save (the host
  // saves the PNG, transcribes via the vision chain, files the note
  // (category 手寫) and ingests the .txt transcript = the index).
  // isWebMode() is the existing helper further down (6072-era).
  // v0.9.125.1 — multipage + background + fullscreen state
  let hwPages = [];   // data-URL per page ('' = blank page)
  let hwPage = 0;     // current page index into hwPages
  let hwBgImg = null; // background Image (baked into the exported PNG)
  // v0.9.125.6 — per-page PDF underlay Images (📄 匯入 PDF); win over
  // the global notebook background, contain-fitted (whole page visible)
  let hwPageBgs = [];
  let hwSrcPdf = '';  // the imported PDF's server path (note provenance)
  // v0.9.125.7 — writing tools: 🖊 pen / 🖍 marker (translucent) /
  // 🧽 eraser. Strokes are RECORDED page-locally; an eraser stroke
  // replays destination-out over the re-baked background so it can
  // never tear the PDF/notebook underlay.
  let hwTool = 'pen';
  let hwCfg = {   // per-tool colour + width, switched by the tool btn
    pen:    { c: '#000000', w: 3 },
    marker: { c: '#ffe94a', w: 14 },
    eraser: { c: '', w: 24 },
  };
  let hwStrokes = [];   // hwStrokes[page] = [{tool,color,width,pts[]}]
  function notesHwOpen() {
    notesBind();
    hwPages = [''];
    hwPage = 0;
    hwBgImg = null;
    hwPageBgs = [];
    hwSrcPdf = '';
    // v0.9.125.7 — reset to the pen + its defaults
    hwTool = 'pen';
    hwStrokes = [];
    notesHwApplyToolUI();
    const bclr = document.getElementById('hw-bg-clear');
    if (bclr) bclr.hidden = true;
    const m = document.getElementById('hw-modal');
    m.hidden = false;
    document.getElementById('hw-note-title').value = '';
    const hint = document.getElementById('hw-hint');
    if (hint) hint.textContent = '';   // v0.9.126.5 — idle hint removed
    notesHwInfo();
    requestAnimationFrame(function () {
      notesHwInitCanvas();
      document.getElementById('hw-canvas')
        .scrollIntoView({ block: 'nearest' });
    });
  }
  function notesHwClose() {
    document.getElementById('hw-modal').hidden = true;
  }
  function notesHwInitCanvas() {
    const c = document.getElementById('hw-canvas');
    const rect = c.getBoundingClientRect();
    const dpr = window.devicePixelRatio || 1;
    c.width = Math.max(1, Math.round(rect.width * dpr));
    c.height = Math.max(1, Math.round(rect.height * dpr));
    const g = c.getContext('2d');
    g.setTransform(dpr, 0, 0, dpr, 0, 0);
    if (hwPageBgs[hwPage] || hwBgImg) {
      const src = hwPageBgs[hwPage] || hwBgImg;
      const iw = src.width || 1, ih = src.height || 1;
      g.fillStyle = '#ffffff';
      g.fillRect(0, 0, rect.width, rect.height);
      if (hwPageBgs[hwPage]) {
        // v0.9.125.6 — PDF underlay: CONTAIN-fit (the whole page
        // stays visible with a correct aspect ratio — margin-drawn)
        const s = Math.min(rect.width / iw, rect.height / ih);
        const dw = iw * s, dh = ih * s;
        g.drawImage(src, (rect.width - dw) / 2,
                    (rect.height - dh) / 2, dw, dh);
      } else {
        // v0.9.125.1 — 筆記本背景圖：cover-fit across the canvas
        const s = Math.max(rect.width / iw, rect.height / ih);
        const dw = iw * s, dh = ih * s;
        g.drawImage(src, (rect.width - dw) / 2,
                    (rect.height - dh) / 2, dw, dh);
      }
    } else {
      g.fillStyle = '#ffffff';
      g.fillRect(0, 0, rect.width, rect.height);
    }
    g.strokeStyle = '#111111';
    g.lineWidth = 2.6;
    g.lineCap = 'round';
    g.lineJoin = 'round';
    return g;
  }
  function notesHwClear() {
    // v0.9.125.7 — clear the page's RECORDED strokes too, or the next
    // eraser redraw would resurrect the wiped ink from the replay.
    hwStrokes[hwPage] = [];
    notesHwInitCanvas();
  }
  // ─── v0.9.125.1 — multipage / background / fullscreen ──────────
  function notesHwInfo() {
    const el = document.getElementById('hw-pageinfo');
    if (!el) return;
    el.textContent = hwPages.length > 1
      ? t('hwPageOf').replace('{p}', String(hwPage + 1))
          .replace('{n}', String(hwPages.length))
      : '';
  }
  function notesHwFlushPage() {
    const c = document.getElementById('hw-canvas');
    if (!c) return;
    while (hwPages.length <= hwPage) hwPages.push('');
    hwPages[hwPage] = c.toDataURL('image/png');
  }
  function notesHwShow(idx) {
    notesHwFlushPage();
    hwPage = Math.max(0, Math.min(idx, hwPages.length - 1));
    notesHwInfo();
    const g = notesHwInitCanvas();
    const c = document.getElementById('hw-canvas');
    const url = hwPages[hwPage];
    if (!url) return; // blank page — freshly initialised
    const im = new Image();
    im.onload = function () {
      const r = c.getBoundingClientRect();
      g.drawImage(im, 0, 0, r.width, r.height);
    };
    im.src = url;
  }
  function notesHwAddPage() {
    notesHwFlushPage();
    if (hwPage === hwPages.length - 1) hwPages.push('');
    hwPage += 1;
    notesHwInfo();
    notesHwInitCanvas();
  }
  function notesHwPrev() {
    if (hwPage > 0) notesHwShow(hwPage - 1);
  }
  function notesHwNext() {
    notesHwFlushPage();
    if (hwPage < hwPages.length - 1) notesHwShow(hwPage + 1);
  }
  function notesHwBgClearF() {
    hwBgImg = null;
    hwPageBgs = [];   // v0.9.125.6 — clearing covers the PDF underlay too
    const b = document.getElementById('hw-bg-btn');
    if (b) b.classList.remove('hw-bg-active');
    const x = document.getElementById('hw-bg-clear');
    if (x) x.hidden = true;
    notesHwInitCanvas();
    toast('🖼 ' + t('hwBgCleared'));
  }
  function notesHwBgPick() {
    const fi = document.getElementById('hw-bg-file');
    if (fi) fi.click();
  }
  function notesHwBgChange() {
    const fi = document.getElementById('hw-bg-file');
    const f = fi && fi.files && fi.files[0];
    if (!f) return;
    const fr = new FileReader();
    fr.onload = function () {
      const im = new Image();
      im.onload = function () {
        hwBgImg = im;
        const b = document.getElementById('hw-bg-btn');
        if (b) b.classList.add('hw-bg-active');
        const x = document.getElementById('hw-bg-clear');
        if (x) x.hidden = false;
        notesHwInitCanvas();
        toast('🖼 ' + t('hwBgApplied'));
      };
      im.src = String(fr.result || '');
    };
    fi.value = '';
    fr.readAsDataURL(f);
  }
  function notesHwPdfPick() {
    const fi = document.getElementById('hw-pdf-file');
    if (fi) fi.click();
  }
  // v0.9.125.6 — PDF underlay import: web mode POSTs the FILE to
  // /upload (the bridge cannot carry file bytes) then asks the host
  // to render the pages; GUI (pywebview) mode re-picks the SAME pdf
  // via the native OPEN dialog (same-machine absolute path → host
  // render). Pages reset the pad: one blank ink page per PDF page.
  async function notesHwPdfChange() {
    const fi = document.getElementById('hw-pdf-file');
    const f = fi && fi.files && fi.files[0];
    if (fi) fi.value = '';
    if (!f) return;
    if (!/\.pdf$/i.test(f.name || '')) {
      toast('⚠️ ' + t('hwPdfFail')
        .replace('{m}', 'not a .pdf file'), 'error', 6000);
      return;
    }
    if (isWebMode() && !navigator.onLine) {
      toast('⚠️ ' + t('hwOffline'));
      return;
    }
    const btn = document.getElementById('hw-pdf-btn');
    const hint = document.getElementById('hw-hint');
    if (btn) btn.disabled = true;
    if (hint) hint.textContent =
      t('hwPdfImporting').replace('{f}', f.name);
    try {
      let path = '';
      if (isWebMode()) {
        const fd = new FormData();
        fd.append('files', f, f.name);
        const ac = (typeof AbortController === 'function')
          ? new AbortController() : null;
        const tid = ac ? setTimeout(function () {
          try { ac.abort(); } catch (_) {}
        }, 30000) : null;
        let resp;
        try {
          resp = await fetch('/upload', {
            method: 'POST', body: fd,
            signal: ac ? ac.signal : undefined,
          });
        } finally {
          if (tid) clearTimeout(tid);
        }
        const data = await resp.json();
        if (!data || !data.ok || !data.files || !data.files.length) {
          throw new Error('upload failed');
        }
        path = data.files[0].path;
      } else {
        const pv = window.pywebview || null;
        if (!pv || typeof pv.create_file_dialog !== 'function') {
          throw new Error('no native dialog');
        }
        const picked = await pv.create_file_dialog(
          (pv.FileDialog && pv.FileDialog.OPEN) || 'open', {}, null);
        path = (picked && picked[0]) || '';
        if (!path) {
          if (hint) hint.textContent = '';   // v0.9.126.5 — idle hint removed
          return;
        }
      }
      const r = await api.safeCall('notes_handwriting_pdf_pages',
        { path: path });
      if (!r || r.error) {
        throw new Error(String((r && r.error) || 'import failed'));
      }
      const pgs = (r.pages || []);
      if (!pgs.length) throw new Error('no pages rendered');
      // preload the per-page underlay Images, then reset the pad
      const ims = pgs.map(function (pg) {
        const im = new Image();
        im.src = pg.data_url;
        return im;
      });
      await Promise.all(ims.map(function (im) {
        return new Promise(function (res) {
          im.onload = function () { res(); };
          im.onerror = function () { res(); };
        });
      }));
      hwPages = ims.map(function () { return ''; });
      hwPageBgs = ims;
      hwPage = 0;
      hwSrcPdf = String(r.pdf || '');
      notesHwInfo();
      notesHwInitCanvas();
      if (r.truncated) {
        toast('📄 ' + String(r.pages_total) + ' 頁 — '
          + String(pgs.length) + ' 頁', 'warn', 6000);
      }
      toast('📄 ' + t('hwPdfDone').replace('{n}', String(pgs.length)));
    } catch (e) {
      toast('⚠️ ' + t('hwPdfFail')
        .replace('{m}', String((e && e.message) || e).slice(0, 120)),
        'error', 6000);
    } finally {
      if (btn) btn.disabled = false;
      if (hint) hint.textContent = '';   // v0.9.126.5 — idle hint removed
    }
  }
  function notesHwFullToggle() {
    // v0.9.125.2 — CSS-only fullscreen: the Fullscreen API scales the
    // whole 94vw top-layer element up to the screen (fonts and
    // buttons visually zoom = the user reported「所有物件都放大」).
    // Now NOTHING is resized except the writing area (canvas): same
    // fonts, same buttons, same paddings — pure CSS toggle.
    const m = document.getElementById('hw-modal');
    const on = !m.classList.contains('hw-full');
    m.classList.toggle('hw-full', on);
    requestAnimationFrame(function () { notesHwInitCanvas(); });
  }
  async function notesHwSave() {
    notesHwFlushPage();
    const c = document.getElementById('hw-canvas');
    const save = document.getElementById('hw-save');
    const hint = document.getElementById('hw-hint');
    // ink-scan every page; blank pages are dropped honestly
    const urls = [];
    for (let i = 0; i < hwPages.length; i++) {
      const u = (i === hwPage) ? c.toDataURL('image/png') : hwPages[i];
      if (!u) continue;
      const hasInk = await new Promise(function (res) {
        const im = new Image();
        im.onload = function () {
          try {
            const oc = document.createElement('canvas');
            oc.width = im.width; oc.height = im.height;
            const og = oc.getContext('2d');
            og.drawImage(im, 0, 0);
            const d = og.getImageData(0, 0, oc.width, oc.height).data;
            for (let j = 0; j < d.length; j += 64) {
              if (d[j + 3] !== 0 && (d[j] < 245 || d[j + 1] < 245
                || d[j + 2] < 245)) { res(true); return; }
            }
            res(false);
          } catch (_) { res(true); }
        };
        im.onerror = function () { res(false); };
        im.src = u;
      });
      if (hasInk) urls.push(u);
    }
    if (!urls.length) { toast('✍️ ' + t('hwEmpty')); return; }
    // multi-page → stitch vertically into ONE PNG (single archive +
    // single OCR pass); per-page PNGs ride along as payload.pages.
    let main = urls[0];
    const perPage = urls.length > 1 ? urls : undefined;
    if (urls.length > 1) {
      const ims = await Promise.all(urls.map(function (u) {
        return new Promise(function (res) {
          const im = new Image();
          im.onload = function () { res(im); };
          im.onerror = function () { res(null); };
          im.src = u;
        });
      }));
      const ok = ims.filter(Boolean);
      if (ok.length) {
        const W = Math.max.apply(null, ok.map(function (im) {
          return im.width; }));
        const H = ok.reduce(function (s, im) { return s + im.height; },
                            0) + (ok.length - 1) * 24;
        const stitch = document.createElement('canvas');
        stitch.width = W; stitch.height = H;
        const sg = stitch.getContext('2d');
        sg.fillStyle = '#ffffff'; sg.fillRect(0, 0, W, H);
        let y = 0;
        for (const im of ok) {
          sg.drawImage(im, 0, y);
          y += im.height + 24;
        }
        main = stitch.toDataURL('image/png');
      }
    }
    if (isWebMode() && !navigator.onLine) {
      toast('⚠️ ' + t('hwOffline'));
      return;
    }
    if (save) { save.disabled = true; }
    if (hint) hint.textContent = t('hwOcring');
    let r = null;
    try {
      r = await api.safeCall('notes_handwriting_save', {
        data_url: main,
        pages: perPage,
        title: (document.getElementById('hw-note-title').value
          || '').trim(),
        // v0.9.125.6 — PDF underlay provenance + the OCR hint that
        // the printed text baked into the image is NOT the content
        source_pdf: hwSrcPdf,
        has_pdf_bg: hwPageBgs.length > 0,
      });
    } catch (_) { r = null; }
    if (save) { save.disabled = false; }
    if (hint) hint.textContent = '';   // v0.9.126.5 — idle hint removed
    if (!r) {
      toast('⚠️ ' + (notesOfflineActive()
        ? t('hwOffline') : t('errGeneric', { msg: 'send failed' })));
      return;
    }
    if (r.error) {
      toast('⚠️ ' + String(r.error).slice(0, 160), 'error', 6000);
      return;
    }
    toast('✅ ' + t('hwDone').replace(
      '{n}', String(r.indexed_chunks ?? 0)));
    hwPages = [''];
    hwPage = 0;
    notesHwClose();
    if (r.id) notesOpen(r.id);   // review/edit the transcribed note
    else notesLoad(1);
  }
  function notesHwRestoreStyle(g) {
    // v0.9.125.7 — put the 2d context back to the neutral state the
    // init/settled drawing expects (after a pen/marker stroke).
    g.globalCompositeOperation = 'source-over';
    g.globalAlpha = 1;
    g.strokeStyle = '#111111';
    g.lineWidth = 2.6;
  }
  function notesHwRedrawPage() {
    // v0.9.125.7 — re-bake the background (underlay → global bg →
    // white) and replay EVERY recorded stroke in order; eraser
    // strokes go destination-out ABOVE the ink, so wiping never
    // tears the PDF/notebook underlay. Geometry replicates the
    // incremental mid-point smooth segments.
    const c = document.getElementById('hw-canvas');
    if (!c) return;
    const g = notesHwInitCanvas();
    const strokes = hwStrokes[hwPage] || [];
    for (const s of strokes) {
      const pts = s.pts || [];
      if (!pts.length) { continue; }
      if (pts.length < 2 && s.tool !== 'eraser') { continue; }
      g.globalCompositeOperation = (s.tool === 'eraser')
        ? 'destination-out' : 'source-over';
      g.globalAlpha = (s.tool === 'marker') ? 0.4 : 1;
      if (s.tool !== 'eraser') { g.strokeStyle = s.color; }
      g.lineWidth = s.width;
      if (pts.length < 2) {
        // eraser single TAP = a destination-out dot (the replay is
        // what makes the tap wipe visibly)
        g.beginPath();
        g.arc(pts[0].x, pts[0].y, s.width / 2 + 1, 0, Math.PI * 2);
        g.fill();
        continue;
      }
      // v0.9.125.7b — the SOLID geometry (same segments the live
      // draw painted): the pen travels through the MID-POINTS, each
      // curve starts exactly where the last one ended; the final
      // half-segment lands as lineTo. One path + ONE stroke → no
      // alpha stacking, no segment gaps = no dashes.
      g.beginPath();
      g.moveTo(pts[0].x, pts[0].y);
      let lx = pts[0].x, ly = pts[0].y;
      let bx = pts[0].x, by = pts[0].y;
      for (let i = 1; i < pts.length; i++) {
        const p = pts[i];
        const mx = (lx + p.x) / 2, my = (ly + p.y) / 2;
        g.quadraticCurveTo(lx, ly, mx, my);
        bx = mx; by = my;
        lx = p.x; ly = p.y;
      }
      g.lineTo(lx, ly);
      g.stroke();
    }
    notesHwRestoreStyle(g);
  }
  function notesHwApplyToolUI() {
    // v0.9.125.7 — mirror hwTool/hwCfg onto the toolbar (slider,
    // width label, colour picker, active button).
    const cfg = hwCfg[hwTool] || { c: '#000000', w: 3 };
    const w = document.getElementById('hw-width');
    const wv = document.getElementById('hw-width-val');
    const col = document.getElementById('hw-color');
    if (w) w.value = String(cfg.w);
    if (wv) wv.textContent = String(cfg.w);
    if (col && /^#[0-9a-fA-F]{6}$/.test(cfg.c)) col.value = cfg.c;
    for (const tk of ['pen', 'marker', 'eraser']) {
      const b = document.getElementById('hw-tool-' + tk);
      if (b) b.classList.toggle('active', tk === hwTool);
    }
  }
  function notesHwBind() {
    const draw = function (c, g) {
      let drawing = false;
      let lx = 0, ly = 0;   // the last RAW sample point
      let bx = 0, by = 0;   // v0.9.125.7b — the PEN position: every
      // segment starts from the PREVIOUS MID-POINT, not from the raw
      // point — segment-to-segment they share that point exactly, so
      // the line is SOLID (starting from the raw point left a gap
      // between the previous segment's end and the new start = the
      // 「畫線變虛線」 the user saw on the Android pen).
      let st = null;   // the stroke being recorded (v0.9.125.7)
      const pos = function (ev) {
        const r = c.getBoundingClientRect();
        return { x: ev.clientX - r.left, y: ev.clientY - r.top };
      };
      const tail = function () {
        // the final half-segment: last mid-point → last raw point
        if (st && st.pts.length > 1) {
          g.beginPath();
          g.moveTo(bx, by);
          g.lineTo(lx, ly);
          g.stroke();
        }
      };
      c.addEventListener('pointerdown', function (ev) {
        drawing = true;
        c.setPointerCapture(ev.pointerId);
        const p = pos(ev);
        lx = p.x; ly = p.y;
        bx = p.x; by = p.y;   // pen parked on the first sample
        // v0.9.125.7 — tool-aware stroke style + the recorded stroke
        if (!hwStrokes[hwPage]) hwStrokes[hwPage] = [];
        st = { tool: hwTool, color: hwCfg[hwTool].c,
               width: hwCfg[hwTool].w, pts: [p] };
        hwStrokes[hwPage].push(st);
        g.globalCompositeOperation = (hwTool === 'eraser')
          ? 'destination-out' : 'source-over';
        g.globalAlpha = (hwTool === 'marker') ? 0.4 : 1;
        if (hwTool !== 'eraser') { g.strokeStyle = hwCfg[hwTool].c; }
        g.lineWidth = hwCfg[hwTool].w;
        g.beginPath(); g.moveTo(p.x, p.y);
        ev.preventDefault();
      });
      c.addEventListener('pointermove', function (ev) {
        if (!drawing) return;
        ev.preventDefault();
        // v0.9.125.1 — Android-stylus anti-lag: pointermove fires at
        // display refresh rate; the higher-rate samples BETWEEN
        // frames ride getCoalescedEvents(). Stroking every coalesced
        // point removes the "筆劃會 delay" lag felt on Android pads.
        const evs = (typeof ev.getCoalescedEvents === 'function'
          && ev.getCoalescedEvents().length)
          ? ev.getCoalescedEvents() : [ev];
        for (const cev of evs) {
          const p = pos(cev);
          // v0.9.125.7 — incremental SEGMENT draw (one beginPath per
          // segment): a translucent marker stroke must NOT re-stroke
          // the whole path every move (that stacks alpha = the line
          // darkens the longer it gets — the old whole-path stroke()
          // model breaks exactly when alpha < 1).
          // v0.9.125.7b — each segment starts from the previous
          // MID-POINT (bx,by) → curveTo(raw point → new mid-point):
          // segments share that joint = solid line, no dashes.
          const mx = (lx + p.x) / 2, my = (ly + p.y) / 2;
          g.beginPath();
          g.moveTo(bx, by);
          g.quadraticCurveTo(lx, ly, mx, my);
          g.stroke();
          bx = mx; by = my;
          if (st) st.pts.push(p);
          lx = p.x; ly = p.y;
        }
      });
      const end = function (ev) {
        if (!drawing) return;
        drawing = false;
        tail();   // settle the final half-segment before any reset
        st = null;
        // v0.9.125.7 — an eraser stroke tears the underlay on the live
        // composite canvas (destination-out); re-bake background +
        // replay ALL strokes so the page settles with the underlay
        // intact and only the erased ink gone.
        if (hwTool === 'eraser') { notesHwRedrawPage(); }
        else { notesHwRestoreStyle(g); }
        try { c.releasePointerCapture(ev.pointerId); } catch (_) {}
      };
      c.addEventListener('pointerup', end);
      c.addEventListener('pointercancel', end);
      c.addEventListener('pointerleave', end);
    };
    const c = document.getElementById('hw-canvas');
    if (c) draw(c, notesHwInitCanvas());
    // v0.9.125.1 — multipage / background / fullscreen wiring
    const on = function (id, fn) {
      const el = document.getElementById(id);
      if (el) el.addEventListener('click', fn);
    };
    on('hw-addpage', notesHwAddPage);
    on('hw-prev', notesHwPrev);
    on('hw-next', notesHwNext);
    on('hw-bg-btn', notesHwBgPick);
    on('hw-bg-clear', notesHwBgClearF);
    on('hw-pdf-btn', notesHwPdfPick);
    on('hw-full', notesHwFullToggle);
    on('hw-full2', notesHwFullToggle);
    const bfile = document.getElementById('hw-bg-file');
    if (bfile) bfile.addEventListener('change', notesHwBgChange);
    const pfile = document.getElementById('hw-pdf-file');
    if (pfile) pfile.addEventListener('change', notesHwPdfChange);
    // v0.9.125.7 — writing-tool toolbar: tool buttons switch hwTool
    // and reload that tool's own colour/width; chips/picker/slider
    // edit the CURRENT tool's cfg (eraser ignores colour —
    // destination-out).
    on('hw-tool-pen', function () {
      hwTool = 'pen'; notesHwApplyToolUI();
    });
    on('hw-tool-marker', function () {
      hwTool = 'marker'; notesHwApplyToolUI();
    });
    on('hw-tool-eraser', function () {
      hwTool = 'eraser'; notesHwApplyToolUI();
    });
    document.querySelectorAll('.hw-tools-row .hw-chip')
      .forEach(function (chip) {
        const cv = chip.getAttribute('data-col') || '';
        chip.style.background = cv; // static hex constant from HTML
        chip.addEventListener('click', function () {
          hwCfg[hwTool].c = cv;
          const col = document.getElementById('hw-color');
          if (col) col.value = cv;
        });
      });
    const col = document.getElementById('hw-color');
    if (col) col.addEventListener('input', function () {
      hwCfg[hwTool].c = String(col.value || '');
    });
    const wid = document.getElementById('hw-width');
    if (wid) wid.addEventListener('input', function () {
      hwCfg[hwTool].w =
        Math.max(1, Math.round(parseFloat(wid.value) || 1));
      const wv = document.getElementById('hw-width-val');
      if (wv) wv.textContent = String(hwCfg[hwTool].w);
    });
    notesHwApplyToolUI();
    // DPI/vp changes invalidate the bitmap — re-init honestly (the
    // strokes are cleared rather than smeared).
    window.addEventListener('resize', function () {
      const m = document.getElementById('hw-modal');
      if (m && !m.hidden) notesHwInitCanvas();
    });
  }

  function notesClose() {
    document.getElementById('notes-editor').hidden = true;
    const box = document.getElementById('ne-hw-img');
    if (box) { box.hidden = true; box.replaceChildren(); }
  }
  // v0.9.125.1 — 可看圖: when the note body links an archived
  // handwriting PNG (🖼 手寫原文：<path>), show it inline in the
  // editor through the hdir-scoped read_handwriting_image endpoint.
  function notesHwShowArchived(text) {
    const box = document.getElementById('ne-hw-img');
    if (!box) return;
    const m = /🖼\s*手寫原文：(\S+)/.exec(text || '');
    if (!m || !m[1]) { box.hidden = true; box.replaceChildren(); return; }
    api.safeCall('read_handwriting_image', { path: m[1] })
      .then(function (r) {
        if (r && !r.error && r.data_url) {
          box.replaceChildren();
          const img = document.createElement('img');
          img.src = r.data_url;
          img.alt = t('hwViewOriginal');
          box.appendChild(img);
          box.hidden = false;
        } else {
          box.hidden = true;
          box.replaceChildren();
        }
      });
  }

  function notesRenderPreview() {
    const body = document.getElementById('ne-body').value;
    document.getElementById('ne-preview').innerHTML = md(body);
  }

  function notesTogglePreview() {
    const pv = document.getElementById('ne-preview');
    const ta = document.getElementById('ne-body');
    const btn = document.getElementById('ne-preview-btn');
    if (pv.hidden) {
      notesRenderPreview(); pv.hidden = false; ta.hidden = true;
      btn.textContent = t('notesEdit');
    } else {
      pv.hidden = true; ta.hidden = false;
      btn.textContent = t('notesPreview');
    }
  }

  function notesMdInsert(cmd) {
    const ta = document.getElementById('ne-body');
    const s = ta.selectionStart, e = ta.selectionEnd;
    const v = ta.value, sel = v.slice(s, e);
    const wrap = { bold: ['**', '**'], italic: ['*', '*'],
                   code: ['`', '`'], fence: ['\n```\n', '\n```\n'],
                   quote: ['\n> ', '\n'] };
    let ins = '', caret = null;
    if (wrap[cmd]) {
      ins = wrap[cmd][0] + (sel || t('notesTextPh')) + wrap[cmd][1];
      caret = s + wrap[cmd][0].length;
    } else if (cmd === 'h2') {
      ins = '\n## ' + (sel || t('notesTextPh')) + '\n';
      caret = s + 4;
    } else if (cmd === 'ul') {
      ins = '\n- ' + (sel || t('notesTextPh')) + '\n';
      caret = s + 3;
    } else if (cmd === 'link') {
      ins = '[' + (sel || t('notesTextPh')) + '](https://)';
      caret = s + 1;
    } else if (cmd === 'table') {
      ins = '\n| 欄位 | 內容 |\n|---|---|\n| A | B |\n';
      caret = s + ins.length;
    }
    if (!ins) return;
    ta.value = v.slice(0, s) + ins + v.slice(e);
    ta.focus();
    if (caret !== null) {
      ta.setSelectionRange(caret, caret + (sel ? sel.length : 0));
    }
    ta.dispatchEvent(new Event('input'));
  }

  function setStatus(state, detail) {
    const qDot = document.getElementById('status-qdrant');
    const lDot = document.getElementById('status-llm');
    const det  = document.getElementById('status-detail');
    if (qDot) {
      qDot.dataset.state = state.qdrant_ok ? 'ok' : (state.qdrant_error ? 'error' : 'unknown');
      qDot.textContent = state.qdrant_ok ? 'OK' : 'ERR';
    }
    const llmOk = !!(state.ollama_key_set || state.openai_key_set);
    if (lDot) {
      lDot.dataset.state = llmOk ? 'ok' : 'error';
      lDot.textContent = llmOk ? 'OK' : 'ERR';
    }
    if (det) {
      const parts = [];
      if (state.collection) parts.push(state.collection);
      if (state.llm_provider) parts.push(state.llm_provider);
      if (detail) parts.push(detail);
      det.textContent = parts.join(' | ');
    }
  }

  async function refreshStatus() {
    const r = await api.safeCall('get_status');
    if (r && !r.error) {
      setStatus(r, 'v' + (r.version || ''));
      // v0.9.21.1+ — the brand-side version chip (index.html
      // ``#app-version``) carried a hardcoded 'v0.8.6' that was NEVER
      // updated by JS (the v0.9.14.1 fix rewired gui/api.py but
      // missed this HTML fallback literal). pyproject.toml is the
      // single source of truth — paint it here on every poll.
      const el = document.getElementById('app-version');
      if (el && r.version) el.textContent = 'v' + r.version;
      // v0.9.47.0+ — background-task badge (⏳ button) from the
      // 30 s status poll; the panel itself refreshes on its own.
      if (typeof r.running_tasks === 'number') {
        updateTaskBadge(r.running_tasks);
      }
    }
  }

  // ===== Markdown + escape =====
  // v0.8.3+: full GFM coverage — task lists (input[type=checkbox]),
  // strikethrough (del/s), nested lists, images, figure/figcaption,
  // and safe link attributes (target=_blank + rel=noopener noreferrer).
  //
  // v0.8.3 patch: load marked.min.js + purify.min.js from the local
  // vendor/ dir first (CDN fallback via <script onerror> in
  // index.html). When BOTH paths fail the helper logs a console
  // warning AND raises a one-shot toast — the user no longer sees
  // raw `**xxx**` text silently. They know markdown is degraded.
  function md(text) {
    if (!text) return '';
    // Duck-type check — works whether marked.parse / DOMPurify.sanitize
    // are exposed as a namespace object (marked) or a function with
    // attached methods (DOMPurify 3 is a named function).
    const markedOK = typeof window.marked !== 'undefined' &&
                     typeof window.marked.parse === 'function';
    const purifyOK = typeof window.DOMPurify !== 'undefined' &&
                     typeof window.DOMPurify.sanitize === 'function';
    if (markedOK && purifyOK) {
      const html = window.marked.parse(text, {
        gfm: true,
        breaks: true,
        // Don't emit ids/anchors by default — they pollute the chat
        // log and trigger unnecessary scroll jumps.
        headerIds: false,
        mangle: false,
      });
      return window.DOMPurify.sanitize(html, {
        ALLOWED_TAGS: [
          // block
          'p','br','div','hr','blockquote','pre','figure','figcaption',
          'h1','h2','h3','h4','h5','h6',
          'ul','ol','li',
          'table','thead','tbody','tfoot','tr','th','td','caption',
          // inline text
          'b','strong','i','em','u','s','del','ins','mark','small',
          'span','sub','sup',
          'code',
          // links & media
          'a','img',
          // task-list checkbox (GFM)
          'input',
        ],
        ALLOWED_ATTR: [
          // links — safe target + rel
          'href','title','target','rel',
          // images
          'src','alt','width','height','loading',
          // task-list checkbox state
          'type','checked','disabled',
          // generic
          'class','id',
        ],
        // Forbid inline event handlers / javascript: URLs explicitly.
        ALLOWED_URI_REGEXP: /^(?:(?:https?|mailto|tel|ftp):|[^a-z]|[a-z+.\-]+(?:[^a-z+.\-:]|$))/i,
        ADD_ATTR: ['target','rel'],
      });
    }
    // ── markdown engine unavailable — surface a warning so we can
    // diagnose it, then fall back to plain escaped text. The fallback
    // is intentionally lossy: better to show plain text with a console
    // hint than to silently show raw `**xxx**` asterisks.
    if (!window.__md_warned) {
      window.__md_warned = true;
      const missing = [];
      if (!markedOK) missing.push('marked');
      if (!purifyOK) missing.push('dompurify');
      console.warn(
        '[markdown] unable to load: ' + missing.join(', ') +
        '. Both local vendor/ and CDN fallback failed. Showing ' +
        'plain text — check gui/assets/vendor/ for the vendored ' +
        'marked.min.js + purify.min.js copies.'
      );
      try { toast('Markdown renderer unavailable — see console', 'error', 4000); }
      catch (e) { /* toast not ready yet */ }
    }
    const esc = function (s) {
      return s.replace(/[&<>"']/g, function (c) {
        return { '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c];
      });
    };
    return '<p>' + esc(text).replace(/\n/g, '<br>') + '</p>';
  }

  // Patch links after sanitize so we always get target=_blank +
  // rel=noopener noreferrer on every <a>. DOMPurify strips unknown
  // attrs by default; we add them back here in a single sweep.
  function hardenLinks(html) {
    if (typeof html !== 'string') return html;
    return html.replace(/<a\s+([^>]*?)>/gi, function (full, attrs) {
      // Skip if it already has a rel= attribute (avoid clobbering).
      if (/\brel=/i.test(attrs)) return full;
      // Skip empty href / href starting with javascript:
      if (!/\bhref=/i.test(attrs)) return full;
      return '<a ' + attrs + ' target="_blank" rel="noopener noreferrer">';
    });
  }

  // ── Early self-check: warn at startup if the markdown engine
  // can't load. Runs after DOM ready so we can also pop a toast.
  // The vendor/ + CDN fallback should make this a no-op in 99% of
  // installs; the warning is a diagnostic tripwire for the rare
  // misconfigured environment.
  function _mdStartupCheck() {
    const markedOK = typeof window.marked !== 'undefined' &&
                     typeof window.marked.parse === 'function';
    const purifyOK = typeof window.DOMPurify !== 'undefined' &&
                     typeof window.DOMPurify.sanitize === 'function';
    if (!markedOK || !purifyOK) {
      // Defer one tick so toast() has a chance to wire up.
      setTimeout(function () {
        if (typeof window.__md_warned === 'undefined') {
          // md() hasn't run yet — fire its warning path now.
          md('');
        }
      }, 100);
    }
  }

  function escapeHtml(s) {
    return (s || '').replace(/[&<>"']/g, function (c) {
      return { '&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;' }[c];
    });
  }


  // ===== Chat =====
  const chatSession = { id: null, title: '', messages: [] };

  // v0.9.9.4+ — per-bubble time header above every chat bubble.
  // (v0.9.9.3's whole-transcript strip was replaced per user
  // feedback: each bubble gets its own time line instead.)
  // A user bubble shows its single send time; an assistant bubble
  // shows 開始 → 結束 (request start → reply landed) when the
  // ``startTs`` stamp is known. Legacy (ts-less) or invalid
  // timestamps → ``null`` (no header rendered). Pure string
  // building — the caller renders via ``textContent`` (no XSS).
  function _fmtChatTime(iso, withDate) {
    const d = new Date(iso);
    if (isNaN(d.getTime())) return null;
    const pad = function (n) { return (n < 10 ? '0' : '') + n; };
    const hm = pad(d.getHours()) + ':' + pad(d.getMinutes());
    if (!withDate) return hm;
    return (d.getFullYear() + '/' + pad(d.getMonth() + 1) + '/' +
            pad(d.getDate()) + ' ' + hm);
  }

  function _sameChatDay(a, b) {
    const da = new Date(a), db = new Date(b);
    if (isNaN(da.getTime()) || isNaN(db.getTime())) return false;
    return (da.getFullYear() === db.getFullYear() &&
            da.getMonth() === db.getMonth() &&
            da.getDate() === db.getDate());
  }

  function _chatMsgTimeText(m) {
    if (!m || !m.ts) return null;
    const endStr = _fmtChatTime(m.ts, false);
    if (!endStr) return null;
    let startStr = null;
    if (m.role === 'assistant' && m.startTs) {
      // An INVALID startTs (unparseable) behaves exactly like an
      // absent one — the header degrades to the single end time.
      startStr = _fmtChatTime(m.startTs, false);
    }
    if (startStr) {
      const cross = !_sameChatDay(m.startTs, m.ts);
      const s = cross ? _fmtChatTime(m.startTs, true) : startStr;
      const e = cross ? _fmtChatTime(m.ts, true) : endStr;
      return t('chatStart') + ' ' + s + ' → ' + t('chatEnd') + ' ' + e;
    }
    // Single-time bubble: include the date when it is not today.
    const withDate = !_sameChatDay(m.ts, new Date().toISOString());
    return withDate ? _fmtChatTime(m.ts, true) : endStr;
  }

  // v0.9.12.0+ — per-pair token usage (prompt sent up / completion
  // came down), aggregated across the whole tool loop by the Agent.
  // Returns null when the message carries no usage (legacy saved
  // sessions, provider omitted usage, or all-zero counters).
  function _chatTokensText(m) {
    const u = m && m.usage;
    if (!u || typeof u !== 'object') return null;
    const p = Number(u.prompt_tokens) || 0;
    const c = Number(u.completion_tokens) || 0;
    if (!p && !c) return null;
    const fmt = function (n) { return n.toLocaleString('en-US'); };
    let s = '↑ ' + fmt(p) + ' / ↓ ' + fmt(c) + ' tok';
    // v0.9.13.1+ — prompt-cache info: how much of the prompt the
    // provider served from its cache (cost + latency saver). Only
    // shown when the provider actually reported a cache hit.
    const cached = Number(u.cached_tokens) || 0;
    if (cached > 0) s += ' · ⚡ ' + fmt(cached) + ' cached';
    const calls = Number(u.calls) || 0;
    if (calls > 1) s += ' · ' + calls + ' calls';
    return s;
  }

  const toolsPeek = new Set();
  function _chatToolsText(m) {
    if (!m) return null;
    if (!Array.isArray(m.toolsUsed)) return null;
    if (!m.toolsUsed.length) return null;
    const counts = {};
    const names3 = [];
    for (let i = 0; i < m.toolsUsed.length; i++) {
      const nm = String(m.toolsUsed[i]).trim();
      if (!nm) continue;
      if (!counts[nm]) { counts[nm] = 0; names3.push(nm); }
      counts[nm] = counts[nm] + 1;
    }
    return names3.map(function (nm) {
      const c4 = counts[nm];
      return c4 > 1 ? nm + ' x' : nm;
    }).join(' / ');
  }

  // v0.9.14.0+ — per-request MCP tool picker (chat UI).
  // v0.9.83.8 — PER-TURN semantics: the override applies to the NEXT
  // send only and is wiped right after that request completes — it
  // can never become a silent global restriction again (the .83.7
  // root cause: a stale localStorage 'chat-disabled-tools' set rode
  // on every request for days and HID get_current_time / web_search
  // / search_documents). Long-term tool config belongs to
  // MCP_ACTIVE_SERVERS (.env), not the browser.
  let chatAllToolNames = [];
  let chatDisabledTools = null;

  function loadDisabledTools() {
    // Boot migration: wipe any stale persistent set once — every
    // browser that ever stored one gets a clean, all-on tool surface.
    try {
      localStorage.removeItem('chat-disabled-tools');
    } catch (_) {}
    chatDisabledTools = null;
  }

  function saveDisabledTools() {
    // v0.9.83.8 — the override lives in memory for THIS turn only;
    // nothing is persisted to localStorage any more.
  }

  function updateChatToolsBadge() {
    const btn = document.getElementById('chat-tools-btn');
    if (!btn) return;
    const total = chatAllToolNames.length;
    const off = chatDisabledTools ? chatDisabledTools.size : 0;
    btn.textContent = '🧰 ' + (total - off) + '/' + total;
  }

  function renderChatToolsPanel() {
    const list = document.getElementById('chat-tools-list');
    if (!list) return;
    list.replaceChildren();
    const bySrv = {};
    chatAllToolNames.forEach(function (t) {
      (bySrv[t.server] = bySrv[t.server] || []).push(t.name);
    });
    Object.keys(bySrv).sort().forEach(function (srv) {
      const names = bySrv[srv];
      // v0.9.32.0+ — per-SERVER select-all toggle (tri-state):
      // every tool on → checked; every tool off → unchecked;
      // mixed → indeterminate. Clicking flips the WHOLE group
      // (all-on → all-off; mixed / all-off → all-on — the browser
      // resolves the indeterminate click to checked=true). Same
      // localStorage disabled-set as the per-tool rows.
      const head = document.createElement('label');
      head.className = 'chat-tool-srv';
      const srvCb = document.createElement('input');
      srvCb.type = 'checkbox';
      srvCb.className = 'chat-tool-srv-cb';
      srvCb.dataset.srv = srv;
      const offCount = names.filter(function (n) {
        return chatDisabledTools && chatDisabledTools.has(n);
      }).length;
      if (offCount === 0) {
        srvCb.checked = true;
      } else if (offCount === names.length) {
        srvCb.checked = false;
      } else {
        srvCb.indeterminate = true;
      }
      srvCb.addEventListener('change', function () {
        if (!chatDisabledTools) chatDisabledTools = new Set();
        const turnOn = srvCb.checked; // browser already flipped it
        names.forEach(function (n) {
          if (turnOn) chatDisabledTools.delete(n);
          else chatDisabledTools.add(n);
        });
        saveDisabledTools();
        updateChatToolsBadge();
        renderChatToolsPanel();
      });
      const srvSpan = document.createElement('span');
      srvSpan.textContent = srv;
      head.appendChild(srvCb);
      head.appendChild(srvSpan);
      list.appendChild(head);
      names.sort().forEach(function (name) {
        const row = document.createElement('label');
        row.className = 'chat-tool-row';
        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.checked = !(chatDisabledTools && chatDisabledTools.has(name));
        cb.addEventListener('change', function () {
          if (!chatDisabledTools) chatDisabledTools = new Set();
          if (cb.checked) chatDisabledTools.delete(name);
          else chatDisabledTools.add(name);
          saveDisabledTools();
          updateChatToolsBadge();
          // re-render keeps the per-server tri-state fresh
          renderChatToolsPanel();
        });
        const span = document.createElement('span');
        span.textContent = name;
        row.appendChild(cb);
        row.appendChild(span);
        list.appendChild(row);
      });
    });
  }

  async function toggleChatToolsPanel() {
    const panel = document.getElementById('chat-tools-panel');
    if (!panel) return;
    if (!panel.hidden) { panel.hidden = true; return; }
    const r = await api.safeCall('list_mcp_tools');
    const tools = (r && !r.error && r.tools) || [];
    chatAllToolNames = tools.map(function (t) {
      return { name: t.name, server: t.server };
    });
    if (!chatDisabledTools) loadDisabledTools();
    renderChatToolsPanel();
    updateChatToolsBadge();
    panel.hidden = false;
  }

  function setAllChatTools(on) {
    if (on) {
      chatDisabledTools = new Set();
    } else {
      chatDisabledTools = new Set(
        chatAllToolNames.map(function (t) { return t.name; }),
      );
    }
    saveDisabledTools();
    renderChatToolsPanel();
    updateChatToolsBadge();
  }

  function resetChatTools() {
    chatDisabledTools = null;
    saveDisabledTools();
    renderChatToolsPanel();
    updateChatToolsBadge();
  }

  function effectiveChatTools() {
    // null = no override (the .env gate applies); otherwise the
    // explicit enabled tool-name list for this request.
    if (!chatDisabledTools || !chatDisabledTools.size) return null;
    return chatAllToolNames
      .map(function (t) { return t.name; })
      .filter(function (n) { return !chatDisabledTools.has(n); });
  }

  // v0.9.47.0+ — background task panel (⏳ toolbar button). Lists
  // running + recent background LLM turns across ALL sources (manual
  // submit / scheduler / webhook / telegram) from the process-wide
  // task registry; auto-refreshes every 2 s while open. The badge on
  // the ⏳ button paints from get_status's ``running_tasks`` on every
  // 30 s poll AND from every panel refresh.
  let taskPanelTimer = null;

  function updateTaskBadge(runningCount) {
    const btn = document.getElementById('chat-tasks-btn');
    if (!btn) return;
    const n = Number(runningCount) || 0;
    btn.textContent = n > 0
      ? (t('chatTasks') + ' (' + n + ')')
      : t('chatTasks');
    btn.classList.toggle('chat-tasks-active', n > 0);
  }

  function _taskStatusLabel(status) {
    if (status === 'running') return t('taskRunning');
    if (status === 'done') return t('taskDone');
    if (status === 'failed') return t('taskFailed');
    return status || '?';
  }

  function _renderTaskRow(r) {
    const row = document.createElement('div');
    row.className = 'chat-task-row chat-task-' + (r.status || '?');
    const dot = document.createElement('span');
    dot.className = 'task-dot';
    dot.textContent = '●';
    const body = document.createElement('span');
    body.className = 'task-body';
    const title = document.createElement('div');
    title.className = 'task-title';
    title.textContent =
      (r.title || '(untitled)') + ' · ' + _taskStatusLabel(r.status);
    const meta = document.createElement('div');
    meta.className = 'task-meta';
    const bits = [];
    if (r.source) bits.push(r.source);
    if (r.turn_id) bits.push('⚙ ' + r.turn_id);   // v0.9.133.10
    if (r.sid) bits.push(r.sid);
    if (r.started_at) bits.push(r.started_at);
    if (r.status === 'done' && Number(r.answer_chars) >= 0) {
      bits.push(r.answer_chars + ' chars');
    }
    if (r.error) bits.push('⚠ ' + String(r.error).slice(0, 80));
    meta.textContent = bits.join(' · ');
    body.appendChild(title);
    body.appendChild(meta);
    row.appendChild(dot);
    row.appendChild(body);
    // v0.9.133.10 — inline execution-log expander:
    //   running + turn_id  = LIVE poll (poll_turn incremental)
    //   terminal + turn_id = one full replay (poll_turn snapshot)
    //   no turn_id         = honest note (ingest tasks have no log;
    //                        the notify hook reports their outcome)
    const tBtn = document.createElement('button');
    tBtn.type = 'button';
    tBtn.className = 'task-log-toggle';
    tBtn.textContent = '⚙ ' + t('taskLogShow');
    const logBox = document.createElement('div');
    logBox.className = 'chat-exec-lines task-log-lines';
    logBox.hidden = true;
    tBtn.addEventListener('click', async function (e) {
      e.stopPropagation();
      const open = _taskLogOpen.has(r.task_id);
      if (open) {
        _taskLogOpen.delete(r.task_id);
        logBox.hidden = true;
        return;
      }
      _taskLogOpen.add(r.task_id);
      logBox.hidden = false;
      await _taskLogRefresh(r, logBox);
    });
    if (_taskLogOpen.has(r.task_id)) {
      // panel re-builds every 2 s — keep expanded rows expanded
      logBox.hidden = false;
    }
    row.appendChild(tBtn);
    row.appendChild(logBox);
    _taskLogBoxes.set(r.task_id, logBox);
    return row;
  }

  // v0.9.133.10 — per-task log state: expanded ids, the poll cursor,
  // and the open log boxes so the 2 s panel rebuild can refresh the
  // lines in place. Running tasks poll incrementally; terminal tasks
  // replay once from the snapshot; ingest tasks show an honest note.
  const _taskLogOpen = new Set();
  const _taskLogCursor = new Map();
  const _taskLogBoxes = new Map();
  const _taskLogText = new Map();   // last rendered text (delta guard)

  async function _taskLogRefresh(rec, logBox) {
    if (!logBox) {
      logBox = _taskLogBoxes.get(rec.task_id) || null;
    }
    if (!logBox) return;
    const tid = rec.turn_id;
    if (!tid) {
      logBox.textContent = t(rec.source === 'ingest'
        ? 'taskLogNoteIngest' : 'taskLogNoteNone');
      return;
    }
    let snap = null;
    try {
      snap = await api.call('poll_turn', tid,
        Number(_taskLogCursor.get(rec.task_id) || 0));
    } catch (_) { return; }
    if (!snap) return;
    if (!snap.found) {
      logBox.textContent = t('turnGone');
      return;
    }
    _taskLogCursor.set(rec.task_id, Number(snap.next_cursor || 0));
    const evs = (snap.events || []);
    if (evs.length && _taskLogText.get(rec.task_id) !==
        JSON.stringify(evs.slice(-1)[0] || '')) {
      _taskLogText.set(rec.task_id,
        JSON.stringify(evs.slice(-1)[0] || ''));
      evs.forEach(function (ev) {
        if (!ev) return;
        const ln = document.createElement('div');
        ln.className = 'chat-exec-line';
        ln.textContent =
          ((ev.ts || '') + ' ' + (ev.text || '')).trim();
        logBox.appendChild(ln);
      });
      logBox.scrollTop = logBox.scrollHeight;
    }
  }

  // ─── v0.9.132.3 — background-task result write-back ──────────────
  // Tasks submitted from THIS chat (☑ 背景執行) are tracked; when the
  // registry reports them terminal, the result is fetched from the
  // task session and appended into THIS conversation, then auto-saved
  // like every other message. The client is the single writer of its
  // chat session file, so the write-back rides the normal save path
  // (no server-side file race). ``origin_sid`` (server-side registry
  // field) lets a page RELOAD re-bind tasks whose origin is this chat.
  const bgWatch = new Map();   // task_id → consecutive "unknown" polls
  let bgWatchTimer = null;

  function watchBgTask(taskId) {
    if (!taskId || bgWatch.has(taskId)) return;
    bgWatch.set(taskId, 0);
    ensureBgWatchTimer();
  }

  function ensureBgWatchTimer() {
    if (bgWatchTimer || !bgWatch.size) return;
    bgWatchTimer = setInterval(bgWatchTick, 8000);
    // first probe shortly after (setTimeout keeps the tick async-safe)
    setTimeout(function () { try { bgWatchTick(); } catch (_) {} }, 400);
  }

  function stopBgWatchTimerIfNeeded() {
    if (!bgWatch.size && bgWatchTimer) {
      clearInterval(bgWatchTimer);
      bgWatchTimer = null;
    }
  }

  async function bgWatchTick() {
    if (!bgWatch.size) { stopBgWatchTimerIfNeeded(); return; }
    const ids = Array.from(bgWatch.keys());
    let listing = null;
    try {
      listing = await api.call('list_background_tasks', null, 50);
    } catch (_) { return; }
    const rows = (listing && listing.tasks) || [];
    const byId = {};
    rows.forEach(function (r) {
      if (r && r.task_id) byId[r.task_id] = r;
    });
    for (let i = 0; i < ids.length; i++) {
      const tid = ids[i];
      const rec = byId[tid];
      if (!rec) {
        // registry restarted / trimmed — tolerate a few misses, then
        // drop the watch (the task may be gone for good).
        const misses = (bgWatch.get(tid) || 0) + 1;
        if (misses >= 5) bgWatch.delete(tid);
        else bgWatch.set(tid, misses);
        continue;
      }
      if (rec.status === 'running') continue;
      bgWatch.delete(tid);
      stopBgWatchTimerIfNeeded();
      // Dedupe (boot reconciliation / a previous write-back may have
      // already landed this result in the session).
      const already = (chatSession.messages || []).some(function (m) {
        return m.taskId === tid;
      });
      if (already) continue;
      let det = null;
      try { det = await api.call('get_background_task', tid, 4000); }
      catch (_) {}
      if (!det || det.error) {
        appendChatMsg('error',
          '⏳ ' + tid + ': '
          + ((det && det.error) || 'result unavailable'));
      } else if (det.status === 'failed') {
        appendChatMsg('error',
          '⏳ ' + tid + ' — ' + (det.error || t('taskFailed')));
      } else {
        const text = (det.answer_preview || '').trim()
          || '(無回覆 — 模型未產生內容)';
        appendChatMsg('assistant', text);
        const msgs = chatSession.messages || [];
        if (msgs.length && msgs[msgs.length - 1].role === 'assistant') {
          msgs[msgs.length - 1].taskId = tid;
        }
        try {
          toast('✅ ' + t('bgResultBack', { id: tid }), 'success', 3000);
        } catch (_) {}
      }
      autoSaveChat();
      try { renderTaskPanel(); } catch (_) {}
    }
    stopBgWatchTimerIfNeeded();
  }

  // Boot / session-load reconciliation: re-bind tasks whose origin is
  // THIS chat session — covers the page-reload and page-closed-then-
  // -returned cases (dedupe via msg.taskId keeps it idempotent).
  async function reconcileBgTasks() {
    if (!chatSession || !chatSession.id) return;
    let listing = null;
    try {
      listing = await api.call('list_background_tasks', null, 50);
    } catch (_) { return; }
    const rows = (listing && listing.tasks) || [];
    rows.forEach(function (r) {
      if (r && r.origin_sid === chatSession.id) {
        watchBgTask(r.task_id);
      }
    });
  }

  async function renderTaskPanel() {
    const count = document.getElementById('chat-tasks-count');
    const list = document.getElementById('chat-tasks-list');
    if (!list) return;
    const r = await api.safeCall('list_background_tasks');
    list.innerHTML = '';
    if (!r || r.error) {
      const span = document.createElement('span');
      span.textContent = (r && r.error) || '—';
      list.appendChild(span);
      return;
    }
    const tasks = r.tasks || [];
    if (count) {
      count.textContent =
        t('taskRunning') + ': ' + (Number(r.running) || 0) +
        ' / ' + (Number(r.total) || 0);
    }
    updateTaskBadge(r.running);
    if (!tasks.length) {
      const span = document.createElement('span');
      span.className = 'task-none';
      span.textContent = t('taskNone');
      list.appendChild(span);
      return;
    }
    tasks.forEach(function (rec) { list.appendChild(_renderTaskRow(rec)); });
    // v0.9.133.10 — refresh the INLINE logs of expanded rows (the
    // rebuild replaces the boxes; the open-ids Set keeps them open).
    await (async function () {
      for (const tt of tasks) {
        if (_taskLogOpen.has(tt.task_id)) {
          try {
            await _taskLogRefresh(tt, _taskLogBoxes.get(tt.task_id));
          } catch (_) {}
        }
      }
    })();
  }

  async function toggleTaskPanel() {
    const panel = document.getElementById('chat-tasks-panel');
    if (!panel) return;
    if (!panel.hidden) {
      panel.hidden = true;
      if (taskPanelTimer) {
        clearInterval(taskPanelTimer);
        taskPanelTimer = null;
      }
      return;
    }
    await renderTaskPanel();
    panel.hidden = false;
    // Auto-refresh while open (2 s — cheap in-memory registry read).
    if (taskPanelTimer) clearInterval(taskPanelTimer);
    taskPanelTimer = setInterval(function () {
      if (panel.hidden) {
        clearInterval(taskPanelTimer);
        taskPanelTimer = null;
        return;
      }
      renderTaskPanel();
    }, 2000);
  }

  // v0.9.132.1 — live ⚙ execution-log panels of in-flight turns must
  // survive renderChat's full-log rebuild (any appendChatMsg during a
  // running poll — e.g. the auto-background taskSubmitted bubble —
  // would otherwise wipe the panel + 🛑 abort of the still-running
  // turn). renderChat re-mounts every non-terminal panel; the poller
  // stamps panel.dataset.terminal at the true terminal state.
  const liveExecPanels = new Set();

  // ─── v0.9.132.9 — clipboard helpers (buttons on input / bubbles /
  // notes editor) ───────────────────────────────────────────────────
  // Write: navigator.clipboard first, then a hidden-textarea
  // execCommand fallback (non-secure contexts / WebView quirks).
  async function _clipWrite(text) {
    try {
      await navigator.clipboard.writeText(text || '');
      return true;
    } catch (_) {}
    try {
      const ta = document.createElement('textarea');
      ta.value = text || '';
      ta.setAttribute('readonly', 'readonly');
      ta.style.position = 'fixed';
      ta.style.opacity = '0';
      document.body.appendChild(ta);
      ta.focus();
      ta.select();
      const ok = document.execCommand('copy');
      ta.remove();
      return !!ok;
    } catch (_) {
      return false;
    }
  }
  // Read: navigator.clipboard only (there is no safe fallback for
  // reading) — null = the caller shows the honest "use the keyboard"
  // toast.
  async function _clipRead() {
    try {
      const t = await navigator.clipboard.readText();
      return (t == null) ? null : String(t);
    } catch (_) {
      return null;
    }
  }
  function _bubbleCopyBtn(text) {
    const b = document.createElement('button');
    b.className = 'btn-msg-copy';
    b.type = 'button';
    b.textContent = '📋';
    b.title = t('clipCopyMsg');
    b.addEventListener('click', async function () {
      const ok = await _clipWrite(text);
      toast(ok ? '📋 ' + t('clipCopied')
               : '⚠️ ' + t('clipPasteFail'), ok ? 'ok' : 'error', 2200);
    });
    return b;
  }
  // Insert clipboard text at the caret (replaces the selection).
  function _insertAtCaret(el, text) {
    const s = el.selectionStart || 0;
    const e = el.selectionEnd || 0;
    el.value = el.value.slice(0, s) + text + el.value.slice(e);
    const np = s + text.length;
    try { el.setSelectionRange(np, np); } catch (_) {}
  }

  // ─── v0.9.133.0 — bubble colour & transparency (歷史氣泡可調) ──────
  // Per-role colour + opacity, persisted in localStorage
  // ('chat-bubble-style' = {asst:{c,a}, user:{c,a}}) and live-applied
  // via CSS custom properties on #chat-log (every existing bubble
  // restyles instantly; new bubbles inherit). Unset role = the theme
  // default (the vars simply aren't overridden).
  function _hexToRgba(c, alpha) {
    // Accepts '#rrggbb' or a computed 'rgb(a)' string → 'rgba(r,g,b,a)'
    let r = null, g = null, b = null;
    const mHex = /^#([0-9a-f]{6})$/i.exec(String(c || '').trim());
    if (mHex) {
      const v = parseInt(mHex[1], 16);
      r = (v >> 16) & 255; g = (v >> 8) & 255; b = v & 255;
    } else {
      const mRgb = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(String(c || ''));
      if (mRgb) { r = +mRgb[1]; g = +mRgb[2]; b = +mRgb[3]; }
    }
    if (r === null) return null;
    const a = Math.max(0, Math.min(1, Number(alpha)));
    return 'rgba(' + r + ',' + g + ',' + b + ',' + a + ')';
  }
  function _loadBubbleStyle() {
    try {
      const raw = localStorage.getItem('chat-bubble-style');
      if (!raw) return null;
      const o = JSON.parse(raw);
      return (o && typeof o === 'object') ? o : null;
    } catch (_) { return null; }
  }
  function _saveBubbleStyle(o) {
    try { localStorage.setItem('chat-bubble-style', JSON.stringify(o)); }
    catch (_) {}
  }
  function _applyBubbleStyle() {
    const logEl = document.getElementById('chat-log');
    if (!logEl) return;
    const st = _loadBubbleStyle();
    const roles = ['asst', 'user'];
    roles.forEach(function (role) {
      const cfg = st && st[role];
      const bgVar = role === 'asst' ? '--bub-asst-bg' : '--bub-user-bg';
      const bdVar = '--bub-asst-border';
      if (cfg && cfg.c) {
        const rgba = _hexToRgba(cfg.c, typeof cfg.a === 'number'
          ? cfg.a : 1);
        if (rgba) {
          logEl.style.setProperty(bgVar, rgba);
          if (role === 'asst') logEl.style.setProperty(bdVar, rgba);
        }
        return;
      }
      logEl.style.removeProperty(bgVar);
      if (role === 'asst') logEl.style.removeProperty(bdVar);
    });
  }

  function renderChat(peekIdx, peekDelta) {
    const log = document.getElementById('chat-log');
    if (!log) return;
    log.innerHTML = '';
    chatSession.messages.forEach(function (m, mIdx) {
      const div = document.createElement('div');
      div.className = 'chat-msg ' + m.role;
      if (m.role === 'assistant') {
        div.innerHTML = hardenLinks(md(m.content || ''));
      } else {
        div.textContent = m.content || '';
      }
      // v0.9.68.0+ — per-bubble 🔊 voice-out: click to TTS-speak this
      // assistant reply (one audio at a time; speakChatText toggles).
      if (m.role === 'assistant' && (m.content || '').trim()) {
        const vbtn = document.createElement('button');
        vbtn.className = 'btn-msg-voice';
        vbtn.type = 'button';
        vbtn.textContent = '🔊';
        vbtn.title = t('voiceSpeak');
        const btext = m.content;
        vbtn.addEventListener('click', function () {
          speakChatText(vbtn, btext);
        });
        div.appendChild(vbtn);
      }
      // v0.9.127.5+ — per-bubble 📤 share-out: opens the OS share
      // sheet (navigator.share; iOS Safari / Android Chrome both
      // implement it) so a reply can be handed to any app. Desktop
      // Firefox / pywebview GUI lack share() → clipboard fallback.
      if (m.role === 'assistant' && (m.content || '').trim()) {
        const shbtn = document.createElement('button');
        shbtn.className = 'btn-msg-share';
        shbtn.type = 'button';
        shbtn.textContent = '📤';
        shbtn.title = t('shareReply');
        const stext = m.content;
        shbtn.addEventListener('click', function () {
          _shareChatText(stext);
        });
        div.appendChild(shbtn);
      }
      // v0.9.132.9 — per-bubble 📋 copy (assistant AND user bubbles).
      if ((m.content || '').trim()) {
        div.appendChild(_bubbleCopyBtn(m.content));
      }
      if (m.sources && m.sources.length) {
        const s = document.createElement('div');
        s.className = 'sources';
        s.textContent = t('sourcesLabel') + ': ' + m.sources.join(', ');
        div.appendChild(s);
      }
      // v0.9.5+ — if the assistant response carries an inline
      // ``<mxfile>`` XML block (common when the LLM was given a
      // drawio MCP tool), append a per-message "View in Diagrams"
      // button that opens a separate viewer window. Bare
      // diagrams.net URLs in the prose are left alone — the
      // user can ctrl-click to open them in their browser;
      // routing a remote diagrams.net URL through our offline
      // embed would require a fetch round-trip the LLM can't
      // legitimately proxy anyway.
      if (m.role === 'assistant' && m.content) {
        const diagramXml = _extractDiagramXml(m.content);
        if (diagramXml) {
          const btn = document.createElement('button');
          btn.className = 'btn-msg-diagrams';
          btn.type = 'button';
          btn.textContent =
            t('openInDiagrams') || '📊 View in Diagrams';
          btn.title =
            'Open the embedded draw.io XML in the viewer window';
          btn.addEventListener('click', function () {
            api.call(
              'open_diagrams_with_xml',
              'Diagram from chat',
              diagramXml,
            ).then(function (r) {
              if (r && r.error) {
                toast('Diagrams: ' + r.error, 'error', 3000);
              }
            }).catch(function (e) {
              toast('Diagrams: ' + e, 'error', 3000);
            });
          });
          div.appendChild(btn);
        }
      }
      // v0.9.9.4+ — wrap every bubble in a column row so the
      // per-message time header sits ABOVE the bubble (outside
      // it), aligned with the bubble's side.
      // v0.9.12.0+ — the same header line also carries the pair's
      // token usage (prompt / completion) when the Agent reported
      // it; legacy sessions without usage keep the time-only line.
      const row = document.createElement('div');
      row.className = 'chat-msg-row ' + m.role;
      // v0.9.103.0 — the tools-peek scroll re-anchor needs the row's
      // message index after the full rebuild (querySelector anchor).
      row.dataset.midx = String(mIdx);
      const metaParts = [];
      const timeText = _chatMsgTimeText(m);
      if (timeText) metaParts.push(timeText);
      let toolsArr = null;
      if (m.role === 'assistant') {
      if (Array.isArray(m.toolsUsed)) {
      if (m.toolsUsed.length) {
      toolsArr = m.toolsUsed;
      }
      }
      }
      let toolsText = null;
      if (toolsArr) {
      toolsText = _chatToolsText(m);
      }
      const tokText = _chatTokensText(m);
      if (tokText) metaParts.push(tokText);
      if (metaParts.length) {
        const timeDiv = document.createElement('div');
        timeDiv.className = 'chat-msg-time';
        timeDiv.textContent = metaParts.join(' · ');
        if (toolsArr) {
          const chip = document.createElement('button');
          chip.type = 'button';
          chip.className = 'btn-msg-tools';
          chip.textContent = String.fromCodePoint(0x1F9F0) + ' ' + toolsArr.length;
          chip.title = t('chatTools');
          const pkKey = 'idx-' + mIdx;
          chip.addEventListener('click', function () {
            // v0.9.103.0 — capture where the toggled bubble sits in
            // the viewport BEFORE the rebuild; renderChat(peekIdx,
            // peekDelta) re-anchors the scroll to that exact offset
            // instead of jumping to the bottom.
            const rowEl = chip.closest('.chat-msg-row');
            const logEl = document.getElementById('chat-log');
            let deltaTop = 32;
            if (rowEl && logEl) {
              deltaTop = rowEl.getBoundingClientRect().top -
                logEl.getBoundingClientRect().top;
            }
            if (toolsPeek.has(pkKey)) { toolsPeek.delete(pkKey); } else { toolsPeek.add(pkKey); }
            renderChat(mIdx, deltaTop);
          });
          timeDiv.appendChild(chip);
        }
        row.appendChild(timeDiv);
        const showTools = toolsPeek.has('idx-' + mIdx);
        if (toolsText) {
          if (showTools) {
            const tlDiv = document.createElement('div');
            tlDiv.className = 'chat-msg-tools';
            tlDiv.textContent = toolsText;
            row.appendChild(tlDiv);
          }
        }
      }
      // v0.9.123.0+ — replay the saved execution log (the two-phase
      // turn stores msg.turnLog; history bubbles get a 查看執行 log
      // expander built with textContent — no innerHTML/XSS surface).
      const turnLogArr = (m.role === 'assistant'
        && Array.isArray(m.turnLog)) ? m.turnLog : null;
      let logBox = null;
      if (turnLogArr && turnLogArr.length) {
        const view = document.createElement('button');
        view.type = 'button';
        view.className = 'chat-exec-view';
        view.textContent = '⚙ ' + t('turnLogShow');
        logBox = document.createElement('div');
        logBox.className = 'chat-exec-lines chat-exec-static';
        logBox.hidden = true;
        turnLogArr.forEach(function (e) {
          if (!e) return;
          const ln = document.createElement('div');
          ln.className = 'chat-exec-line';
          ln.textContent = ((e.ts || '') + ' ' + (e.text || '')).trim();
          logBox.appendChild(ln);
        });
        view.addEventListener('click', function () {
          logBox.hidden = !logBox.hidden;
        });
        row.appendChild(view);
      }
      row.appendChild(div);
      if (logBox) row.appendChild(logBox);
      log.appendChild(row);
    });
    // v0.9.132.1 — re-mount the live execution-log panels of
    // still-running turns (the rebuild above wipes them).
    liveExecPanels.forEach(function (p) {
      if (p && !p.dataset.terminal && !p.isConnected) {
        log.appendChild(p);
      }
    });
    // v0.9.30.0+ — convert uploaded / markdown image paths in the
    // freshly rebuilt bubbles into inline previews (data URLs via
    // the read_sandbox_image bridge).
    renderSandboxImages();
    // v0.9.65.0+ — non-image sandbox file paths → ⬇ download chips.
    renderSandboxFiles();
    // v0.9.103.0 — the tools-peek chip toggle must NOT yank the page
    // to the bottom: re-anchor the scroll so the toggled bubble stays
    // exactly where the user was reading. Every other render path
    // (new message, load, clear…) keeps the stick-to-bottom jump.
    if (typeof peekIdx === 'number') {
      try {
        const el = log.querySelector(
          '.chat-msg-row[data-midx="' + String(peekIdx) + '"]'
        );
        if (el) {
          const lr = log.getBoundingClientRect();
          const cur = el.getBoundingClientRect().top - lr.top;
          const prev = (typeof peekDelta === 'number') ? peekDelta : cur;
          log.scrollTop = log.scrollTop + (cur - prev);
        } else {
          log.scrollTop = log.scrollHeight;
        }
      } catch (e) {
        log.scrollTop = log.scrollHeight;
      }
    } else {
      log.scrollTop = log.scrollHeight;
    }
  }

  // ─── v0.9.30.0+ — inline image display inside chat bubbles ───────
  // Uploaded / pasted image paths (`[已上傳] C:/... .png` lines in
  // any of the 4 locales) and markdown `![alt](local path)` images
  // are converted into data-URL previews INSIDE the bubble, via the
  // ``Api.read_sandbox_image`` bridge (sandbox membership, image
  // extension whitelist, magic-byte check and a 20 MB cap are all
  // enforced Python-side). Failed loads show a dim placeholder —
  // the bubble text itself is never modified.
  const _sandboxImgCache = new Map(); // path → data URL (FIFO cap)
  const _SANDBOX_IMG_CACHE_MAX = 12;

  function _isLocalImgPath(s) {
    return /^[A-Za-z]:[\\/]/.test(s) || s.indexOf('//') === 0 ||
      (s.charAt(0) === '/' && s.charAt(1) !== '/') ||
      s.toLowerCase().indexOf('uploads/') === 0;
  }

  function renderSandboxImages() {
    const log = document.getElementById('chat-log');
    if (!log) return;
    const jobs = [];
    log.querySelectorAll('.chat-msg').forEach(function (bubble) {
      const existing = Object.create(null);
      // (a) markdown-rendered <img> whose src is a local sandbox path
      bubble.querySelectorAll('img').forEach(function (img) {
        const src = img.getAttribute('src') || '';
        if (/^(data:|https?:|blob:)/i.test(src)) return;
        if (!_isLocalImgPath(src)) return;
        existing[src.toLowerCase()] = 1;
        jobs.push({ path: src, img: img });
        _makeChatImgClickable(img, src);
      });
      // (b) image paths in the bubble TEXT (v0.9.30.1+): the
      // "[已上傳] <path>" mark lines (any locale — bracket shape is
      // shared), BARE absolute paths the LLM echoes, and
      // uploads/-relative paths. One regex — the path token must be
      // preceded by start / whitespace / bracket / paren so it never
      // matches mid-word, and the ext must end a token (no
      // a.png.bak / a.pngx false positives).
      const txt = bubble.textContent || '';
      const re = /((?:[A-Za-z]:[\\/]|uploads[\\/])[^\s]+\.(?:png|jpe?g|gif|webp|bmp))(?![\w.\/\\])/gi;
      let m;
      while ((m = re.exec(txt)) !== null) {
        // v0.9.30.2+ — validate the char BEFORE the token instead of
        // a positive separator class: CJK punctuation glued straight
        // onto the path (「檔案位置：C:\...」) must still match, while
        // genuine mid-word hits (myC:/x.png) stay excluded.
        const before = m.index > 0 ? txt.charAt(m.index - 1) : '';
        if (/[\w/\\]/.test(before)) continue;
        const p = m[1];
        if (existing[p.toLowerCase()]) continue;
        existing[p.toLowerCase()] = 1;
        const img = document.createElement('img');
        img.className = 'chat-inline-image';
        img.alt = p.split(/[\\/]/).pop();
        img.title = p;
        bubble.appendChild(img);
        jobs.push({ path: p, img: img });
        _makeChatImgClickable(img, p);
      }
    });
    jobs.forEach(function (it) { _loadSandboxImage(it.path, it.img); });
  }

  function _loadSandboxImage(path, img) {
    if (!img || img.dataset.sbx === '1') return;
    img.dataset.sbx = '1';
    const cached = _sandboxImgCache.get(path);
    if (cached) { img.src = cached; return; }
    const api = (window.pywebview && window.pywebview.api) || null;
    if (!api || typeof api.read_sandbox_image !== 'function') {
      img.classList.add('chat-image-failed');
      return;
    }
    Promise.resolve(api.read_sandbox_image(path)).then(function (r) {
      if (r && r.ok && r.data_base64 && r.mime) {
        const url = 'data:' + r.mime + ';base64,' + r.data_base64;
        if (_sandboxImgCache.size >= _SANDBOX_IMG_CACHE_MAX) {
          _sandboxImgCache.delete(_sandboxImgCache.keys().next().value);
        }
        _sandboxImgCache.set(path, url);
        img.src = url;
      } else {
        img.classList.add('chat-image-failed');
        const why = (r && r.error) || 'image unavailable';
        img.title = why;
        img.alt = '⚠ ' + why;
      }
    }).catch(function () {
      img.classList.add('chat-image-failed');
      img.alt = '⚠ image unavailable';
    });
  }

  // ─── v0.9.65.0+ — chat file download chips ───────────────────────
  // renderSandboxImages() turns IMAGE paths into inline previews;
  // this pass turns every OTHER sandbox file path in the bubble text
  // (absolute C:/... · drive-root /... · uploads/-relative) into a
  // ⬇ download chip. Same regex shape as the image pass (path prefix
  // + before-char validation), image extensions excluded (those
  // already have a preview + lightbox). Click → web mode opens
  // GET /download?path=... (a browser download); GUI mode asks for a
  // destination folder (native pywebview dialog) and copies the file
  // there via save_sandbox_file_as.
  const _DL_IMG_EXTS = ['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'];

  function renderSandboxFiles() {
    const log = document.getElementById('chat-log');
    if (!log) return;
    log.querySelectorAll('.chat-msg').forEach(function (bubble) {
      if (bubble.querySelector('.chat-download-chip')) return;
      const txt = bubble.textContent || '';
      const re =
        /((?:[A-Za-z]:[\\/]|uploads[\\/])[^\s]+\.[A-Za-z0-9]{1,8})(?![\w.\/\\])/gi;
      const per = Object.create(null);
      let m;
      while ((m = re.exec(txt)) !== null) {
        // Same before-char validation as the image pass: CJK
        // punctuation glued onto the path still matches, genuine
        // mid-word hits stay excluded.
        const before = m.index > 0 ? txt.charAt(m.index - 1) : '';
        if (/[\w/\\]/.test(before)) continue;
        const p = m[1];
        const low = p.toLowerCase();
        if (per[low]) continue;
        per[low] = 1;
        const ext = (p.split('.').pop() || '').toLowerCase();
        if (_DL_IMG_EXTS.indexOf(ext) !== -1) continue;
        const name = p.split(/[\\/]/).pop();
        if (!name) continue;
        const ext2 = ext;
        const chip = document.createElement('button');
        chip.type = 'button';
        chip.className = 'chat-download-chip';
        chip.title = p;
        chip.textContent = '⬇ ' + t('downloadFile') + ' · ' + name;
        chip.addEventListener('click', function (e) {
          e.stopPropagation();
          downloadSandboxFile(p);
        });
        // v0.9.133.6 — .html previews open INSIDE the app (an
        // in-app sandboxed iframe modal), never a window.open: on an
        // iOS standalone Web App a window.open navigates the app away
        // and there is no back button to return.
        if (ext2 === 'html') {
          const pv = document.createElement('button');
          pv.type = 'button';
          pv.className = 'chat-download-chip chat-html-preview-chip';
          pv.title = p;
          pv.textContent = '👁 ' + t('htmlPreview') + ' · ' + name;
          pv.addEventListener('click', function (e) {
            e.stopPropagation();
            openHtmlPreview(p, name);
          });
          bubble.appendChild(pv);
        }
        bubble.appendChild(chip);
      }
    });
  }

  // ─── v0.9.133.6 — in-app preview for LLM-made .html files ─────────
  // A full-screen in-app modal with a SANDBOXED iframe renders the
  // server's /download response (which itself is CSP-sandboxed). This
  // is the iPhone-Web-App-safe path: window.open would navigate the
  // standalone app away with no back button, the in-app modal simply
  // restores on ✕ / Esc. GUI (pywebview) mode previews the same file
  // via a file:// iframe.
  let _htmlPreviewEl = null;

  function _htmlPreviewSrc(path) {
    if (isWebMode()) {
      return '/download?path=' + encodeURIComponent(path);
    }
    return 'file:///' + String(path).replace(/^[A-Za-z]:[\\/]+/, '');
  }

  function openHtmlPreview(path, name) {
    _closeHtmlPreview();
    const overlay = document.createElement('div');
    overlay.className = 'chat-html-preview';
    const bar = document.createElement('div');
    bar.className = 'chat-html-preview-bar';
    const ttl = document.createElement('span');
    ttl.className = 'chat-html-preview-title';
    ttl.textContent = (name || 'preview');
    ttl.title = path || '';
    const x = document.createElement('button');
    x.type = 'button';
    x.className = 'chat-html-preview-close';
    x.setAttribute('aria-label', t('htmlPreviewClose'));
    x.textContent = '✕';
    x.addEventListener('click', function (e) {
      e.stopPropagation();
      _closeHtmlPreview();
    });
    bar.appendChild(ttl);
    bar.appendChild(x);
    const frame = document.createElement('iframe');
    frame.className = 'chat-html-preview-frame';
    frame.setAttribute('sandbox',
      'allow-scripts allow-popups allow-popups-to-escape-sandbox');
    frame.src = _htmlPreviewSrc(path);
    overlay.appendChild(bar);
    overlay.appendChild(frame);
    overlay.addEventListener('click', function (e) {
      if (e.target === overlay) _closeHtmlPreview();
    });
    document.body.appendChild(overlay);
    _htmlPreviewEl = overlay;
    document.addEventListener('keydown', _htmlPreviewEsc);
  }

  function _closeHtmlPreview() {
    if (!_htmlPreviewEl) return;
    const el = _htmlPreviewEl;
    if (el.parentNode) el.parentNode.removeChild(el);
    _htmlPreviewEl = null;
    document.removeEventListener('keydown', _htmlPreviewEsc);
  }

  function _htmlPreviewEsc(e) {
    if (e.key === 'Escape') _closeHtmlPreview();
  }

  async function downloadSandboxFile(path) {
    if (isWebMode()) {
      // Web mode: the browser downloads straight from the server
      // (GET /download — sandbox-gated, dangerous-ext refused).
      window.open('/download?path=' + encodeURIComponent(path), '_blank');
      return;
    }
    const pv = window.pywebview || null;
    const api = (window.pywebview && window.pywebview.api) || null;
    if (!pv || typeof pv.create_file_dialog !== 'function' ||
        !api || typeof api.save_sandbox_file_as !== 'function') {
      toast(t('errGeneric', { msg: 'no native dialog' }), 'warn');
      return;
    }
    try {
      const dirs = await pv.create_file_dialog(
        (pv.FileDialog && pv.FileDialog.FOLDER) || 'folder', {}, null);
      const dir = (dirs && dirs[0]) || '';
      if (!dir) return;
      const r = await api.save_sandbox_file_as(path, dir);
      if (r && r.ok) {
        toast(t('fileSaved', { dest: r.dest }), 'ok');
      } else {
        toast(t('errGeneric',
          { msg: (r && r.error) || 'download failed' }), 'err');
      }
    } catch (e) {
      toast(t('errGeneric', { msg: String(e) }), 'err');
    }
  }

  // ─── v0.9.33.0+ — click an inline image to enlarge it ────────────
  // A full-screen lightbox overlay shows the image at ~90 % viewport
  // size with the path as caption; click anywhere (or Esc) closes.
  // Pure DOM + textContent — no XSS surface.
  let _chatLightboxEl = null;

  function _makeChatImgClickable(img, path) {
    img.classList.add('chat-clickable');
    img.addEventListener('click', function (e) {
      e.stopPropagation();
      _openChatLightbox(img.src, path || img.alt || '');
    });
  }

  function _openChatLightbox(src, caption) {
    _closeChatLightbox();
    const overlay = document.createElement('div');
    overlay.className = 'chat-lightbox';
    const img = document.createElement('img');
    img.src = src;
    img.alt = caption || '';
    const cap = document.createElement('div');
    cap.className = 'chat-lightbox-cap';
    cap.textContent = caption || '';
    overlay.appendChild(img);
    overlay.appendChild(cap);
    overlay.addEventListener('click', function () {
      _closeChatLightbox();
    });
    document.body.appendChild(overlay);
    _chatLightboxEl = overlay;
    document.addEventListener('keydown', _chatLightboxEsc);
  }

  function _chatLightboxEsc(e) {
    if (e.key === 'Escape') _closeChatLightbox();
  }

  function _closeChatLightbox() {
    if (_chatLightboxEl) {
      _chatLightboxEl.remove();
      _chatLightboxEl = null;
      document.removeEventListener('keydown', _chatLightboxEsc);
    }
  }

  // v0.9.5+ — extract the first draw.io XML block found in
  // ``content``, or ``null``. The LLM may inline any of these
  // shapes (we see all four in the wild):
  //
  //   1. ``<mxfile>...</mxfile>`` — the canonical drawio XML
  //      wrapper. Often emitted by ``drawio`` MCP servers and
  //      hand-crafted diagrams.
  //   2. ``<mxGraphModel>...</mxGraphModel>`` — the export-
  //      ready form (no mxfile wrapper). Emitted by the
  //      ``create_diagram`` tool we ship + some online
  //      editors.
  //   3. JSON-wrapped: ``{"xml": "<...>..."}`` or
  //      ``"xml": "<...>..."`` — what create_diagram returns
  //      when the LLM tool-result is rendered verbatim in the
  //      assistant reply.
  //   4. Bare ``<mxCell>...</mxCell>`` blocks (rare, hand-
  //      rolled minimal diagrams).
  //
  // The viewer wraps whichever shape we hand it into a proper
  // ``<mxfile><diagram><mxGraphModel>`` shell, so we don't
  // need to do that here. We just pick the *first* matching
  // XML-shaped substring and return it as-is.
  function _extractDiagramXml(content) {
    if (!content || typeof content !== 'string') return null;

    // Each pattern is tried in order; first match wins. We
    // intentionally accept whitespace around tag names (real
    // drawio XML never has it but we tolerate it defensively
    // so an indent in a markdown render doesn't kill the
    // match).
    //
    // Tag-name matchers also accept the JSON unicode-escape
    // forms LLMs occasionally emit verbatim from a tool
    // result: ``\u003cmxfile\u003e`` etc. We normalise those
    // to literal angle brackets before the regex even runs.
    const patterns = [
      // Fenced code block: ```xml <mxfile ...>...</mxfile> ```
      // (or ```drawio ... ```). ``json`` / empty fence is
      // intentionally NOT matched here — those go through
      // the JSON envelope pattern below so we capture only
      // the XML, not the surrounding {"xml": "..."} wrapper.
      /```(?:xml|drawio)\s*([\s\S]*?<\s*mxfile[\s\S]*?<\s*\/\s*mxfile\s*>[\s\S]*?)```/i,
      // Fenced ```xml <mxGraphModel>...</mxGraphModel>```
      /```(?:xml|drawio)\s*([\s\S]*?<\s*mxGraphModel[\s\S]*?<\s*\/\s*mxGraphModel\s*>[\s\S]*?)```/i,
      // JSON-wrapped "xml": "<...>..." — capture the XML
      // string between the unescaped quotes. The inner XML
      // may itself contain escaped quotes (\") which we
      // tolerate as long as the JSON key is on one line.
      // We also accept a bare ``{"xml": "<mxGraphModel>"}``
      // envelope on its own line (no fence) because that is
      // exactly the shape ``create_diagram`` MCP returns.
      /"\s*xml\s*"\s*:\s*"([\s\S]*?<\s*(?:mxfile|mxGraphModel|mxCell)[\s\S]*?<\s*\/\s*(?:mxfile|mxGraphModel|mxCell)\s*>)"/i,
      // Bare <mxfile ...>...</mxfile>
      /<\s*mxfile[\s\S]*?<\s*\/\s*mxfile\s*>/i,
      // Bare <mxGraphModel>...</mxGraphModel>
      /<\s*mxGraphModel[\s\S]*?<\s*\/\s*mxGraphModel\s*>/i,
      // Last resort: a bare <mxCell ...>...</mxCell> (some
      // drawio tools return only the cell tree without a
      // graphmodel wrapper).
      /<\s*mxCell[\s\S]*?<\s*\/\s*mxCell\s*>/i,
    ];

    // Pre-normalise: replace JSON unicode-escape variants of
    // the three opening / closing angle brackets that LLMs
    // sometimes emit verbatim from a tool result. We do
    // this BEFORE the regex runs so the angle-bracket
    // matchers below can stay simple.
    const normalised = content
        .replace(/\\u003c/gi, "<")
        .replace(/\\u003e/gi, ">")
        .replace(/\\u0026/gi, "&");

    for (let i = 0; i < patterns.length; i++) {
      const m = patterns[i].exec(normalised);
      if (!m) continue;
      // Fenced + JSON-envelope patterns put the XML in
      // group 1; bare patterns put it in m[0]. Prefer group
      // 1 when present so we don't drag the surrounding
      // fence / {"xml": "..."} wrapper into the viewer.
      const hit = (m[1] !== undefined ? m[1] : m[0]);
      if (hit) return hit.trim();
    }
    return null;
  }

  // ─── v0.9.123.0 — live execution-log stream (two-phase turn) ────
  // Renders a collapsible ⚙ panel under the user bubble and polls
  // ``poll_turn`` until the terminal status. Resolves with:
  //   null                     — start failed / bridge dead
  //   {error: "..."}           — terminal error (or unknown turn id)
  //   {status, events, result} — done / stopped (result carries the
  //                              chat payload; events ride into the
  //                              session as msg.turnLog so history
  //                              bubbles keep a 查看執行 log expander).
  async function pollTurnStream(startResp, onRelease) {
    const tid = startResp && startResp.turn_id;
    if (!tid) {
      if (startResp && startResp.error) return { error: startResp.error };
      return null;
    }
    const logEl = document.getElementById('chat-log');
    const panel = document.createElement('div');
    panel.className = 'chat-exec';
    const head = document.createElement('div');
    head.className = 'chat-exec-head';
    const statusEl = document.createElement('span');
    statusEl.className = 'chat-exec-status';
    statusEl.textContent = t('turnExecRun');
    const abortBtn = document.createElement('button');
    abortBtn.type = 'button';
    abortBtn.className = 'chat-exec-abort';
    abortBtn.textContent = t('turnAbort');
    abortBtn.addEventListener('click', async function () {
      abortBtn.disabled = true;
      abortRequested = true;
      abortSentAt = Date.now();
      try { await api.call('stop_turn', tid); } catch (_) {}
      statusEl.textContent = t('turnAbortSent');
    });
    head.appendChild(statusEl);
    head.appendChild(abortBtn);
    // Header click collapses/expands the log (abort button exempt).
    head.addEventListener('click', function (ev) {
      if (ev.target === abortBtn) return;
      panel.classList.toggle('collapsed');
    });
    const lines = document.createElement('div');
    lines.className = 'chat-exec-lines';
    panel.appendChild(head);
    panel.appendChild(lines);
    const addLine = function (e) {
      const div = document.createElement('div');
      div.className = 'chat-exec-line k-' + (e.kind || 'note');
      const ts = document.createElement('span');
      ts.className = 'chat-exec-ts';
      ts.textContent = e.ts || '';
      div.appendChild(ts);
      const tx = document.createElement('span');
      tx.className = 'chat-exec-text';
      tx.textContent = ' ' + (e.text || '');
      div.appendChild(tx);
      lines.appendChild(div);
      if (logEl) logEl.scrollTop = logEl.scrollHeight;
    };
    if (logEl) {
      logEl.appendChild(panel);
      logEl.scrollTop = logEl.scrollHeight;
    }
    // v0.9.132.1 — the panel must survive any mid-poll renderChat.
    liveExecPanels.add(panel);
    const eventsAll = [];
    let cursor = 0;
    let misses = 0;
    // v0.9.132.8 — llm_round counting: a turn with TOO MANY rounds
    // makes the puppy dizzy (安慰 = a tap; it recovers alone in 15 s).
    let llmRounds = 0;
    const _DIZZY_AT = 8;
    // v0.9.124.8 — abort patience: the stop Event only bites at the
    // NEXT tool-loop boundary; an in-flight long LLM round (93-330 s
    // observed with a 300-tool surface + an upstream 504) kept the
    // send button disabled for minutes = the reported「UI 就 lock」.
    // After 20 s of waiting past the abort request, RELEASE the UI
    // (the next submit just queues/parallelises via the turn gate)
    // while the poller keeps waiting for the true terminal state.
    let abortRequested = false;
    let abortSentAt = 0;
    let abortUnlocked = false;
    const _ABORT_PATIENCE_MS = 20000;
    // v0.9.132.1 — long-turn auto-background threshold: a foreground
    // turn still running after this long is promoted into the
    // background-task registry (same UX as the ☑ 背景執行 checkbox)
    // and the input unlocks; the poller keeps waiting so the reply
    // still lands in this chat.
    let bgDetached = false;
    const startedAt = Date.now();
    const _TURN_BG_AFTER_MS = 60000;
    for (;;) {
      let r = null;
      try { r = await api.call('poll_turn', tid, cursor); } catch (_) {}
      if (!r || (r && !r.found && r.status === 'unknown')) {
        misses += 1;
        if (misses >= 3) {
          const gone = document.createElement('div');
          gone.className = 'chat-exec-line k-note';
          gone.textContent = '❗ ' + t('turnGone');
          lines.appendChild(gone);
          panel.classList.add('collapsed');
          // v0.9.132.1 — the poller is done; the panel must not be
          // re-mounted by later renderChat rebuilds.
          panel.dataset.terminal = '1';
          liveExecPanels.delete(panel);
          if (onRelease) onRelease();
          return { status: 'unknown', events: eventsAll, result: null };
        }
        await new Promise(function (res) { setTimeout(res, 1500); });
        continue;
      }
      // A {error: ...} envelope from the bridge itself is transient
      // too — retry with backoff before giving up.
      if (!r || r.error) {
        misses += 1;
        if (misses >= 3) {
          const gone = document.createElement('div');
          gone.className = 'chat-exec-line k-note';
          gone.textContent = '❗ ' + (r && r.error
            ? r.error : t('turnGone'));
          lines.appendChild(gone);
          panel.classList.add('collapsed');
          // v0.9.132.1 — the poller is done; the panel must not be
          // re-mounted by later renderChat rebuilds.
          panel.dataset.terminal = '1';
          liveExecPanels.delete(panel);
          if (onRelease) onRelease();
          return { error: (r && r.error) || t('turnGone') };
        }
        await new Promise(function (res) { setTimeout(res, 1500); });
        continue;
      }
      misses = 0;
      (r.events || []).forEach(function (e) {
        addLine(e);
        eventsAll.push(e);
        // v0.9.132.8 — the dizzy threshold: too many LLM rounds = the
        // puppy gets dizzy and needs comforting
        if (e.kind === 'llm_round') {
          llmRounds += 1;
          if (llmRounds === _DIZZY_AT) {
            try { _dogDizzy(); } catch (_) {}
          }
        }
      });
      cursor = r.next_cursor || cursor;
      const st = r.status || 'running';
      if (st === 'running') {
        // v0.9.132.1 — LONG-TURN AUTO-BACKGROUND: once the foreground
        // turn has been running past the patience threshold, promote it
        // into the background-task registry (same UX as the ☑ 背景執行
        // checkbox: taskSubmitted bubble + ⏳ Tasks panel tracking),
        // release the input, and KEEP polling — the reply still lands
        // in this chat when the turn reaches its terminal state.
        if (!abortRequested && !bgDetached
            && (Date.now() - startedAt) >= _TURN_BG_AFTER_MS) {
          bgDetached = true;  // fire once — never re-armed on later polls
          let det = null;
          try {
            det = await api.call('detach_turn_to_background', tid);
          } catch (_) {}
          if (det && det.task_id) {
            statusEl.textContent = t('turnBgDetached');
            appendChatMsg('assistant',
              t('taskSubmitted', { id: det.task_id, sid: det.sid }));
            autoSaveChat();
            try { renderTaskPanel(); } catch (_) {}
            if (onRelease) onRelease();
          }
        }
        // v0.9.124.8 — unlock the UI while the turn is still winding
        // down after an abort (the true terminal state keeps being
        // polled and will still render the bubble below).
        if (abortRequested && !abortUnlocked
            && (Date.now() - abortSentAt) >= _ABORT_PATIENCE_MS) {
          abortUnlocked = true;
          statusEl.textContent = t('turnAbortPending');
          if (onRelease) onRelease();
        }
        await new Promise(function (res) { setTimeout(res, 1200); });
        continue;
      }
      // v0.9.132.1 — the live panel leaves with the terminal render
      // (renderChat no longer re-mounts it).
      panel.dataset.terminal = '1';
      liveExecPanels.delete(panel);
      abortBtn.disabled = true;
      abortBtn.style.display = 'none';
      statusEl.textContent =
        st === 'done' ? t('turnExecDone', { n: eventsAll.length })
        : st === 'stopped' ? t('turnExecStopped')
        : t('turnExecError');
      // Success logs are noise — collapse by default; failures stay
      // open for the post-mortem.
      panel.classList.toggle('collapsed', st === 'done');
      if (onRelease) onRelease();
      const result = r.result || {};
      if (st === 'error' && result.error) {
        return { status: st, events: eventsAll, result: result,
                 error: result.error };
      }
      return { status: st, events: eventsAll, result: result };
    }
  }

  function appendChatMsg(role, content, sources, startTs, usage, toolsUsed) {
    // ``startTs`` (v0.9.9.4+) — on assistant replies, the moment
    // the user's request was sent, so the bubble header can show
    // the 開始 → 結束 generation window. Rides along in the saved
    // session JSON (plain object field).
    // ``usage`` (v0.9.12.0+) — the per-pair token counts reported
    // by the Agent (prompt / completion / total / reasoning /
    // create-calls); rides along in the session JSON too.
    const msg = {
      role: role,
      content: content || '',
      ts: new Date().toISOString(),
      sources: sources || [],
    };
    if (startTs) msg.startTs = startTs;
    // v0.9.102.9 — restored: the v0.9.102.6 patch REPLACED this line
    // with the toolsUsed storage below, so every new bubble lost its
    // per-pair token usage (the v0.9.12.0 header went empty).
    if (usage) msg.usage = usage;
    if (Array.isArray(toolsUsed) && toolsUsed.length) {
        msg.toolsUsed = toolsUsed
          .map(function (x) { return String(x || '').trim(); })
          .filter(Boolean);
    }
    chatSession.messages.push(msg);
    renderChat();
  }

  async function sendChat() {
    // v0.9.127.1 — SEND MUST NEVER DIE SILENTLY. Any client-side
    // failure (bridge gone / fetch refused / renderer bug) previously
    // propagated out of this event handler = a silent no-op with the
    // send button left disabled forever (the reported「按送出完全沒
    // 有反應」). Now: toast + error bubble + restored button + the
    // draft is put back into the input instead of vanishing.
    const inp0 = document.getElementById('chat-input');
    const draft = ((inp0 && inp0.value) || '').trim();
    if (!draft) return;
    try {
      await _sendChatCore();
    } catch (e) {
      const msg = (((e && e.message) || String(e)) || 'unknown error')
        .slice(0, 140);
      try {
        toast('⚠️ ' + t('errGeneric', { msg: msg }), 'error', 6000);
      } catch (_) {}
      try {
        appendChatMsg('error', t('errGeneric', { msg: msg }));
        autoSaveChat();
      } catch (_) {}
      const btn = document.getElementById('chat-send');
      if (btn) {
        btn.disabled = false;
        btn.textContent = t('send');
      }
      try {
        const inp = document.getElementById('chat-input');
        if (inp && !inp.value.trim()) inp.value = draft;
      } catch (_) {}
    }
  }

  async function _sendChatCore() {
    const input = document.getElementById('chat-input');
    const sendBtn = document.getElementById('chat-send');
    // v0.9.130.1 — LOCAL chat commands /playpet + /endpet: the dog
    // appears / goes back to rest WITHOUT an LLM turn. Checked BEFORE
    // the pending-upload merge so a command can never consume the
    // upload buffer (the next real message still carries the markers).
    // _dogRunStart/_dogRunStop are both idempotent and coexist with
    // the turn hooks (stopping the dog mid-turn is safe — the reply's
    // terminal callback re-stops it harmlessly).
    const slash =
      /^\/(playpet|endpet)\s*$/i.exec((input.value || '').trim());
    if (slash) {
      input.value = '';
      const cmd = slash[1].toLowerCase();
      const out = !!document.getElementById('dog-run-layer');
      try {
        if (cmd === 'playpet') {
          if (out) toast(t('petAlreadyOut'));
          else { _dogRunStart(); toast(t('petComeOut')); }
        } else {
          if (out) { _dogRunStop(); toast(t('petGoesRest')); }
          else toast(t('petNotOut'));
        }
      } catch (_) {}
      return;
    }
    // v0.9.127.2 — merge PENDING upload paths (an upload whose fetch
    // resolved after the user began typing / sending): the markers
    // MUST ride the outgoing message — never silently dropped by the
    // race. Skipped when the input already carries them (the normal
    // flow inserted them inline).
    try {
      const pend = window.__PENDING_UPLOAD_PATHS__ || [];
      if (pend.length) {
        const marks = pend.map(function (p) {
          return UPLOAD_MARK + ' ' + p;
        });
        if (!(input.value || '').includes(marks[0])) {
          input.value = input.value
            ? (input.value.replace(/\s*$/, '\n') + marks.join('\n') + '\n')
            : (marks.join('\n') + '\n');
        }
        window.__PENDING_UPLOAD_PATHS__ = [];
      }
    } catch (_) {}
    const text = (input.value || '').trim();
    if (!text) return;

    input.value = '';
    // v0.9.9.4+ — the send moment is the 開始 of both this user
    // bubble and the assistant generation window that follows.
    const reqStart = new Date().toISOString();
    appendChatMsg('user', text);
    // v0.9.127.19 — the running dog appears while the model works.
    try { _dogRunStart(); } catch (_) {}
    autoSaveChat();

    const history = chatSession.messages
      .slice(0, -1)
      .filter(function (m) { return m.role === 'user' || m.role === 'assistant'; })
      .map(function (m) { return { role: m.role, content: m.content }; });

    sendBtn.disabled = true;
    sendBtn.textContent = t('loading');

    // v0.9.47.0+ — background execution: when the Background checkbox
    // is on, submit the message as a background LLM task. Returns
    // task_id immediately; the turn runs in a daemon thread (turn
    // gate applies) with the full tool surface and lands in session
    // task-<id>. The chat stays responsive.
    const bgToggle = document.getElementById('chat-bg-toggle');
    const bgRun = !!(bgToggle && bgToggle.checked);
    if (bgRun) {
      // v0.9.132.3 — pass the ORIGIN chat session id so the finished
      // result can be written back into THIS conversation (watched by
      // bgWatch below; survives a page reload via origin_sid).
      const r = await api.call(
        'submit_background_task', text.slice(0, 40), text, false,
        (chatSession && chatSession.id) || null,
      );
      sendBtn.disabled = false;
      sendBtn.textContent = t('send');
      // v0.9.127.19 — the background turn detached from this window:
      // the dog leaves with the foreground reply.
      try { _dogRunStop(); } catch (_) {}
      if (!r || r.error) {
        appendChatMsg(
          'error',
          (r && r.error) || t('errGeneric', { msg: 'no response' }),
        );
      } else {
        appendChatMsg(
          'assistant',
          t('taskSubmitted', { id: r.task_id, sid: r.sid }),
        );
        watchBgTask(r.task_id);  // v0.9.132.3 — result write-back
        renderTaskPanel();  // refresh badge + open panel if any
      }
      autoSaveChat();
      return;
    }

    // v0.8.5+: forward the current UI language so the LLM answers in
    // the same locale the user is reading. Falls back to 'zh-TW' when
    // no language has been picked yet (initial load before applyI18n).
    const lang = (typeof currentLang === 'string' && currentLang) || 'zh-TW';
    // v0.9.123.0+ — TWO-PHASE TURN: ``start_turn`` registers the turn
    // and returns turn_id immediately (the turn runs on a server-side
    // daemon thread); ``pollTurnStream`` then polls the live execution
    // log and renders the final bubble on the terminal status. Works
    // over the LAN web UI too — whatever client is watching pulls the
    // same events. The legacy payload shape (answer/sources/usage/
    // tools_used) is preserved by the terminal record.
    const r = await pollTurnStream(
      await api.call(
        'start_turn', text, history, false, 5, 5, lang, effectiveChatTools(),
      ),
      function () {
        sendBtn.disabled = false;
        sendBtn.textContent = t('send');
        // v0.9.127.19 — terminal state (reply landed or errored):
        // the dog naturally leaves.
        try { _dogRunStop(); } catch (_) {}
      },
    );
    // v0.9.83.8 — per-turn semantics: the 🧰 override applied to THIS
    // request only; reset right after the response so it can never
    // silently restrict later turns.
    chatDisabledTools = null;
    updateChatToolsBadge();
    renderChatToolsPanel();
    sendBtn.disabled = false;
    sendBtn.textContent = t('send');

    if (!r) {
      appendChatMsg('error', t('errGeneric', { msg: 'no response' }));
      // v0.9.20.0+ — error 泡泡也入檔（即時儲存：任何畫面上的訊息
      // 都要進 session,否則重新整理後歷史不完整）。
      autoSaveChat();
      return;
    }
    if (r.error) {
      appendChatMsg('error', r.error);
      autoSaveChat();
      return;
    }
    const res = r.result || {};
    appendChatMsg('assistant', res.answer, res.sources, reqStart,
                  res.usage, res.tools_used);
    // v0.9.123.0+ — the execution log rides along in the saved
    // session: history bubbles keep a 查看執行 log expander.
    try {
      const msgs = (chatSession
        && Array.isArray(chatSession.messages)) ? chatSession.messages
        : null;
      if (msgs && msgs.length
          && msgs[msgs.length - 1].role === 'assistant') {
        msgs[msgs.length - 1].turnLog = r.events || [];
      }
    } catch (_) { /* best-effort */ }
    // v0.9.127.11 — auto-TTS / hands-free loop: speak the reply the
    // moment it lands; the loop restarts the mic after playback.
    try {
      if (_autoTTS || _voiceLoop) {
        _speakReplyForLoop(res.answer || '');
      }
    } catch (_) { /* best-effort */ }
    refreshSessionPicker();
    autoSaveChat();
  }

  // v0.8.7+: best-effort fire-and-forget save after every reply.
  // Walks back-forth: this is the *belt*. The Python closing hook
  // (gui/app.py::_save_chat_draft_on_close) + this file's
  // ``beforeunload`` listener are the suspenders. Together they
  // form a 3-layer safety net so the user can close the window
  // mid-stream without losing the in-flight chat.
  //
  // We tolerate any error — save failures here just fall through
  // to the next layer. Toast is best-effort too; a save failure
  // mid-stream shouldn't be an alert storm.
  async function autoSaveChat() {
    if (!chatSession || !chatSession.id) {
      // No session yet (still in transient new-message state). The
      // closing hook will see ``id: null`` and skip.
      syncStateMirror();
      return;
    }
    if (!chatSession.messages || chatSession.messages.length === 0) {
      syncStateMirror();
      return;
    }
    // v0.8.7.1+: localStorage backup. Written BEFORE the API
    // call so even if the round-trip + Python save + close
    // hook all fail simultaneously, the next boot can recover
    // the draft. localStorage is a synchronous, JS-side store
    // that cannot be torn down by pywebview until the renderer
    // is actually gone - so this write is guaranteed to land
    // even if the user force-kills the process.
    try {
      localStorage.setItem('chat_draft_backup',
        JSON.stringify({
          id: chatSession.id,
          title: chatSession.title || '',
          messages: chatSession.messages,
          saved_at: new Date().toISOString(),
        })
      );
    } catch (_) {
      // localStorage quota / disabled - non-fatal.
    }
    try {
      const r = await api.call('save_session',
        chatSession.id, chatSession.title || '', chatSession.messages);
      syncStateMirror();
      // Don't toast on every send — that'd be 30 toasts/minute.
      // Surface only failures (rare).
      if (r && r.error) {
        if (typeof toast === 'function') {
          toast(t('errGeneric', { msg: r.error || 'save failed' }), 'warn');
        }
      }
      // Round-trip succeeded - clear the localStorage backup so
      // we don't carry it forward to the next boot (it would
      // be confused with an unsaved draft).
      try { localStorage.removeItem('chat_draft_backup'); } catch (_) {}
    } catch (e) {
      // Swallow — the localStorage backup has the latest state
      // and the next boot's bootDraftRecovery() will offer to
      // restore it.
      syncStateMirror();
    }
  }

  // v0.8.7.1+: boot-time draft recovery. On startup, if
  // ``chat_draft_backup`` exists in localStorage, the previous
  // session's auto-save round-trip must have failed (or the
  // beforeunload path was bypassed) - ask the user whether
  // to restore it. Default: restore (better to have the draft
  // than to lose it; the user can always switch sessions).
  function bootDraftRecovery() {
    var raw = null;
    try { raw = localStorage.getItem('chat_draft_backup'); }
    catch (_) { return; }
    if (!raw) return;
    var draft = null;
    try { draft = JSON.parse(raw); } catch (_) { return; }
    if (!draft || !draft.id || !Array.isArray(draft.messages)
        || draft.messages.length === 0) {
      // Empty / malformed - clean it up and move on.
      try { localStorage.removeItem('chat_draft_backup'); } catch (_) {}
      return;
    }
    // If the current in-memory session already has the same id
    // and is up to date, the backup is stale - just clear it.
    if (chatSession && chatSession.id === draft.id
        && Array.isArray(chatSession.messages)
        && chatSession.messages.length >= draft.messages.length) {
      try { localStorage.removeItem('chat_draft_backup'); } catch (_) {}
      return;
    }
    // Surface a toast offering to restore. Auto-apply after a
    // short delay so the user who closed the app abruptly last
    // time gets their draft back without clicking anything.
    var applyRestore = function () {
      chatSession.id = draft.id;
      chatSession.title = draft.title || '';
      chatSession.messages = draft.messages || [];
      renderChat();
      syncStateMirror();
      try { reconcileBgTasks(); } catch (_) {}  // v0.9.132.3
      // Persist to Python so the Sessions tab shows the draft.
      api.call('save_session', chatSession.id,
        chatSession.title, chatSession.messages);
      // Clear the backup now that it's been restored.
      try { localStorage.removeItem('chat_draft_backup'); } catch (_) {}
      if (typeof toast === 'function') {
        try { toast('已從上次未存檔的草稿復原 ' + chatSession.messages.length + ' 則訊息', 'success', 4000); }
        catch (_) {}
      }
    };
    applyRestore();
  }

  // v0.8.7+: mirror the chat session to pywebview.state so the
  // Python closing handler can read it synchronously without a
  // JS round-trip. pywebview.state is a shared state object
  // observable from both Python and JS — setting on the JS side
  // mirrors to Python on the next event-loop tick. Saves ~50ms
  // vs evaluate_js during the closing event.
  function syncStateMirror() {
    try {
      if (window.pywebview && window.pywebview.state
          && typeof window.pywebview.state.set === 'function') {
        window.pywebview.state.set('chat_session', {
          id: chatSession && chatSession.id || null,
          title: chatSession && chatSession.title || '',
          messages: (chatSession && chatSession.messages) || [],
          saved_at: new Date().toISOString(),
        });
      } else if (window.pywebview && window.pywebview.state) {
        // Fallback: assign-through triggers the observer.
        window.pywebview.state.chat_session = {
          id: chatSession && chatSession.id || null,
          title: chatSession && chatSession.title || '',
          messages: (chatSession && chatSession.messages) || [],
          saved_at: new Date().toISOString(),
        };
      }
    } catch (e) {
      // pywebview.state might not exist in dev (jsdom, cdn mode).
      // Ignore — closing hook handles that case by calling
      // evaluate_js as a fallback.
    }
  }

  // v0.8.7+: ``beforeunload`` last-chance save. Returns the
  // standard "are you sure?" string to trigger the browser's
  // native close confirmation (pywebview on Win32 / GTK /
  // Cocoa all surface this differently; we just want the JS
  // event so we can fire-and-forget a save). Modern WebView2
  // / WKWebView ignore the returnValue from beforeunload for
  // non-user-initiated closes, but the synchronous JS path
  // here still gives us one more chance to call api.save_session
  // before the renderer tears down.
  //
  // Why three layers?
  //   1. autoSaveChat() after each assistant reply (foreground)
  //   2. window.pywebview.state mirror (read by closing hook)
  //   3. this beforeunload (last-ditch)
  // Plus:
  //   4. Python window.events.closing (suspender)
  //   5. Python window.events.closed (last-resort log)
  function _beforeUnloadSave(e) {
    syncStateMirror();
    // Fire-and-forget — the user closes *now*; we don't block
    // the close for an async I/O round-trip.
    if (chatSession && chatSession.id && chatSession.messages
        && chatSession.messages.length > 0) {
      // v0.9.20.0+ — web 模式：unload 時普通 fetch 可能被瀏覽器取消
      // （這是 v0.8.7 設計在 web 模式的實際缺口 — 手機/桌機瀏覽器
      // 都一樣）。改用 navigator.sendBeacon：為 unload 場景設計、
      // 由瀏覽器保證排程發送、不隨頁面死亡取消。body 形狀與 shim 的
      // POST /api/<method> 相同（args list：[sid, title, messages]），
      // web_server 的 handler 只 json.loads(raw)，不依賴 Content-Type。
      if (window.__WEB_MODE__ === true
          && typeof navigator !== 'undefined'
          && typeof navigator.sendBeacon === 'function') {
        try {
          navigator.sendBeacon(
            '/api/save_session',
            new Blob([JSON.stringify([
              chatSession.id, chatSession.title || '', chatSession.messages,
            ])], { type: 'application/json' }),
          );
          return;  // beacon 已交給瀏覽器排程 — 完成
        } catch (_) {
          // beacon 被拒（quota/尺寸極端值）— 落到 GUI 橋接路徑再試一次
        }
      }
      try {
        // synchronous return keeps the WebView from tearing down
        // mid-call. ``api.call`` returns a Promise but the
        // underlying transport synchronously dispatches the message
        // before yielding; if the WebView tears down before the
        // response comes back we still get the dispatch on the
        // Python side.
        api.call('save_session',
          chatSession.id, chatSession.title || '', chatSession.messages);
      } catch (_) {
        // Fall through — closing hook catches the rest.
      }
    }
    // Returning a string shows the native "are you sure" prompt;
    // returning undefined suppresses it. We DON'T prompt — the
    // Python closing handler already saves — we just want the
    // side-effect.
    // (If you want to add a confirm dialog, return
    //  (e.returnValue = t('confirmClose')));
  }

  async function refreshSessionPicker() {
    const r = await api.safeCall('list_sessions');
    const picker = document.getElementById('chat-session-picker');
    if (!picker || !r || r.error) return;
    picker.innerHTML = '';
    const placeholder = document.createElement('option');
    placeholder.value = '';
    placeholder.textContent = '(' + ((r.sessions || []).length) + ')';
    picker.appendChild(placeholder);
    (r.sessions || []).forEach(function (s) {
      const opt = document.createElement('option');
      opt.value = s.id;
      opt.textContent = s.id + ' - ' + s.title + ' (' + s.n_messages + ')';
      picker.appendChild(opt);
    });
  }

  async function newSession() {
    const r = await api.call('create_session', '');
    if (!r || r.error) {
      toast(t('errGeneric', { msg: (r && r.error) || 'create failed' }), 'error');
      return;
    }
    chatSession.id = r.id;
    chatSession.title = r.title;
    chatSession.messages = [];
    renderChat();
    refreshSessionPicker();
    syncStateMirror();  // v0.8.7+: closing hook sees the new id.
    try { reconcileBgTasks(); } catch (_) {}  // v0.9.132.3
    toast('OK', 'success', 1500);
  }

  async function loadSessionById(sid) {
    if (!sid) return;
    const r = await api.call('load_session', sid);
    if (!r || r.error) {
      toast(t('errGeneric', { msg: (r && r.error) || 'load failed' }), 'error');
      return;
    }
    chatSession.id = r.id;
    chatSession.title = r.title;
    chatSession.messages = r.messages || [];
    renderChat();
    syncStateMirror();  // v0.8.7+: closing hook sees the loaded session.
    try { reconcileBgTasks(); } catch (_) {}  // v0.9.132.3
    toast('OK', 'success', 1500);
  }

  async function saveSession() {
    if (!chatSession.id) {
      const created = await api.call('create_session', chatSession.title);
      if (!created || created.error) {
        toast(t('errGeneric', { msg: (created && created.error) || 'create failed' }), 'error');
        return;
      }
      chatSession.id = created.id;
      try { reconcileBgTasks(); } catch (_) {}  // v0.9.132.3
    }
    const firstUser = chatSession.messages.find(function (m) { return m.role === 'user'; });
    if (!chatSession.title && firstUser) {
      chatSession.title = firstUser.content.slice(0, 40);
    }
    const r = await api.call('save_session',
      chatSession.id, chatSession.title, chatSession.messages);
    syncStateMirror();  // v0.8.7+: keep Python mirror in lockstep.
    if (!r || r.error) {
      toast(t('errGeneric', { msg: (r && r.error) || 'save failed' }), 'error');
      return;
    }
    toast('OK ' + t('save'), 'success', 1500);
    refreshSessionPicker();
  }

  function clearChat() {
    if (chatSession.messages.length === 0) return;
    if (!confirm(t('confirmDelete'))) return;
    chatSession.messages = [];
    chatSession.id = null;
    chatSession.title = '';
    renderChat();
    syncStateMirror();  // v0.8.7+: clear Python mirror too.
  }


  // ===== Ingest =====
  async function runIngest() {
    const pathEl = document.getElementById('ingest-file-path');
    const out = document.getElementById('ingest-output');
    const btn = document.getElementById('ingest-go');
    const p = (pathEl.value || '').trim();
    if (!p) { toast(t('errGeneric', { msg: 'path empty' }), 'warn'); return; }

    btn.disabled = true;
    out.textContent = t('loading');

    const isDir = pathEl.dataset.isDir === '1';
    let r;
    if (isDir) {
      r = await api.call('ingest_directory', p);
      out.textContent = JSON.stringify(r, null, 2);
      if (!r.error) toast('OK ' + (r.total_chunks || 0) + ' chunks', 'success');
    } else {
      r = await api.call('ingest_file', p);
      out.textContent = JSON.stringify(r, null, 2);
      if (!r.error) toast('OK ' + (r.chunks || 0) + ' chunks', 'success');
    }
    btn.disabled = false;
    if (r && r.error) toast(t('errGeneric', { msg: r.error }), 'error', 6000);
  }

  async function pickIngestFile() {
    if (!window.pywebview || !window.pywebview.create_file_dialog) {
      toast(t('errGeneric', { msg: 'no native dialog' }), 'warn');
      return;
    }
    const result = await window.pywebview.create_file_dialog(
      window.pywebview.FileDialog.OPEN, {}, null
    );
    if (result && result.length) {
      const pathEl = document.getElementById('ingest-file-path');
      pathEl.value = result[0];
      pathEl.dataset.isDir = '0';
    }
  }

  async function pickIngestDir() {
    if (!window.pywebview || !window.pywebview.create_file_dialog) {
      toast(t('errGeneric', { msg: 'no native dialog' }), 'warn');
      return;
    }
    const result = await window.pywebview.create_file_dialog(
      window.pywebview.FileDialog.FOLDER, {}, null
    );
    if (result && result.length) {
      const pathEl = document.getElementById('ingest-file-path');
      pathEl.value = result[0];
      pathEl.dataset.isDir = '1';
    }
  }

  // ===== Search =====
  async function runSearch() {
    const q = document.getElementById('search-query').value.trim();
    const topk = parseInt(document.getElementById('search-topk').value, 10) || 5;
    const filter = document.getElementById('search-filter').value.trim() || null;
    let sel = document.getElementById('search-collection');
    // v0.9.60.4 — self-healing: if the picker never got populated
    // (e.g. an early search before boot finished), fill it now.
    if (!sel || !sel.options.length) {
      await refreshCollectionSelectors();
      sel = document.getElementById('search-collection');
    }
    const collection = (sel && sel.value) || null;
    const out = document.getElementById('search-results');
    if (!q) { toast(t('errGeneric', { msg: 'query empty' }), 'warn'); return; }
    out.innerHTML = '<p>' + t('loading') + '</p>';

    const r = await api.call('search', q, topk, filter, collection);
    if (!r || r.error) {
      out.innerHTML = '<p style="color:var(--danger)">' +
        escapeHtml(t('errGeneric', { msg: (r && r.error) || 'fail' })) + '</p>';
      return;
    }
    const items = r.results || [];
    if (items.length === 0) {
      out.innerHTML = '<p class="hint">no results</p>';
      return;
    }
    out.innerHTML = '';
    const coll = r.collection || collection;
    items.forEach(function (it, i) {
      const card = document.createElement('div');
      card.className = 'result-card';
      const head = document.createElement('div');
      head.className = 'head';
      head.innerHTML = '<span class="filename">#' + (i + 1) + ' ' +
        escapeHtml(it.filename || '?') + '</span>' +
        '<span class="score">score=' + (it.score || 0).toFixed(4) + '</span>';
      const snip = document.createElement('div');
      snip.className = 'snippet';
      snip.textContent = it.text || (it.payload && it.payload.text) || '';
      card.appendChild(head);
      // v0.9.60.3 — meta chips (kind / stored_at / level / symbol …)
      if (it.meta && Object.keys(it.meta).length) {
        const meta = document.createElement('div');
        meta.className = 'result-meta';
        meta.textContent = Object.entries(it.meta)
          .slice(0, 4)
          .map(function (kv) { return kv[0] + ': ' + kv[1]; })
          .join('  ·  ');
        card.appendChild(meta);
      }
      card.appendChild(snip);
      // v0.9.60.0+: per-result data management (delete this point)
      if (coll && it.id != null) {
        const del = document.createElement('button');
        del.className = 'btn btn-danger btn-sm';
        del.textContent = '🗑';
        del.title = t('deletePoint');
        del.addEventListener('click', async function () {
          if (!confirm(t('confirmDeletePoint', { id: String(it.id).slice(0, 8) }))) return;
          const dr = await api.call('delete_points', coll, [String(it.id)]);
          if (dr && dr.error) { toast(t('errGeneric', { msg: dr.error }), 'err'); return; }
          toast(t('pointDeleted', { n: (dr && dr.deleted) || 0 }), 'ok');
          runSearch();  // re-run so the deleted point disappears
        });
        card.appendChild(del);
      }
      out.appendChild(card);
    });
  }


  // ===== v0.9.60.0+: collection pickers + paged data browser =====
  // Search / Stats can now target ANY Qdrant collection, and the
  // Stats tab browses points PAGE BY PAGE (native Qdrant scroll
  // offset pagination) with per-row delete — a 167k-point
  // collection never gets dumped into the DOM at once.

  async function refreshCollectionSelectors() {
    const r = await api.safeCall('list_collections');
    if (!r || r.error) return;
    const cols = (r.collections || []).slice();
    // configured (★) first, then alphabetical
    cols.sort(function (a, b) {
      if (!!a.configured !== !!b.configured) return a.configured ? -1 : 1;
      return String(a.name).localeCompare(String(b.name));
    });
    ['search-collection', 'stats-collections'].forEach(function (id) {
      const sel = document.getElementById(id);
      if (!sel) return;
      const prev = sel.value;
      sel.innerHTML = '';
      cols.forEach(function (c) {
        const opt = document.createElement('option');
        opt.value = c.name;
        opt.textContent = (c.configured ? '★ ' : '') + c.name;
        sel.appendChild(opt);
      });
      // keep the user's previous selection when it still exists
      if (prev && cols.some(function (c) { return c.name === prev; })) {
        sel.value = prev;
      }
    });
  }

  const statsData = {
    collection: null, curOffset: null, nextOffset: null,
    stack: [],
  };

  function resetStatsDataBrowser() {
    statsData.collection = null;
    statsData.curOffset = null;
    statsData.nextOffset = null;
    statsData.stack = [];
    const table = document.getElementById('stats-data');
    if (table) table.innerHTML = '';
    const info = document.getElementById('stats-data-info');
    if (info) info.textContent = '';
    const pos = document.getElementById('stats-data-pos');
    if (pos) pos.textContent = '';
    const nx = document.getElementById('stats-data-next');
    if (nx) nx.disabled = true;
    const pv = document.getElementById('stats-data-prev');
    if (pv) pv.disabled = true;
  }

  async function loadStatsData(reset) {
    const sel = document.getElementById('stats-collections');
    const collection = (sel && sel.value) || '';
    if (!collection) return;
    if (reset || statsData.collection !== collection) {
      statsData.collection = collection;
      statsData.curOffset = null;
      statsData.nextOffset = null;
      statsData.stack = [];
    }
    const info = document.getElementById('stats-data-info');
    const table = document.getElementById('stats-data');
    const r = await api.call('collection_browse', collection, 50,
                             statsData.curOffset);
    if (!r || r.error) {
      if (info) info.textContent = '⚠ ' + ((r && r.error) || 'fail');
      return;
    }
    // Batch display: ONE page of 50 rows per request.
    table.innerHTML = '';
    const thead = document.createElement('thead');
    const trh = document.createElement('tr');
    ['id', 'payload', ''].forEach(function (label) {
      const th = document.createElement('th');
      th.textContent = label;
      trh.appendChild(th);
    });
    thead.appendChild(trh);
    table.appendChild(thead);
    const tbody = document.createElement('tbody');
    (r.items || []).forEach(function (row) {
      const tr = document.createElement('tr');
      const tdId = document.createElement('td');
      tdId.className = 'mono';
      tdId.textContent = String(row.id).slice(0, 10);
      tdId.title = String(row.id);
      const tdPay = document.createElement('td');
      tdPay.className = 'payload-cell';
      let txt = '';
      try { txt = JSON.stringify(row.payload || {}); }
      catch (e) { txt = String(row.payload); }
      if (txt.length > 240) txt = txt.slice(0, 240) + '…';
      tdPay.textContent = txt;
      tdPay.title = txt;
      const tdDel = document.createElement('td');
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'btn btn-danger btn-sm';
      btn.textContent = '🗑';
      btn.title = t('deletePoint');
      btn.addEventListener('click', async function () {
        if (!confirm(t('confirmDeletePoint',
                       { id: String(row.id).slice(0, 8) }))) return;
        const dr = await api.call('delete_points', collection,
                                  [String(row.id)]);
        if (dr && dr.error) {
          toast(t('errGeneric', { msg: dr.error }), 'err');
          return;
        }
        toast(t('pointDeleted', { n: (dr && dr.deleted) || 0 }), 'ok');
        loadStatsData(false);  // reload the CURRENT page
      });
      tdDel.appendChild(btn);
      tr.appendChild(tdId);
      tr.appendChild(tdPay);
      tr.appendChild(tdDel);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    if (info) {
      info.textContent = (r.items || []).length
        ? '' : t('dataEmpty');
    }
    statsData.nextOffset = r.next_offset || null;
    // v0.9.60.2 — page indicator (current page + rows on it) so a
    // million-point collection is navigable with orientation.
    const pos = document.getElementById('stats-data-pos');
    if (pos) {
      pos.textContent = t('dataPos', {
        p: statsData.stack.length + 1,
        n: (r.items || []).length,
      });
    }
    const nx = document.getElementById('stats-data-next');
    if (nx) nx.disabled = !statsData.nextOffset;
    const pv = document.getElementById('stats-data-prev');
    if (pv) pv.disabled = statsData.stack.length === 0;
  }

  function statsDataNext() {
    if (!statsData.nextOffset) return;
    statsData.stack.push(statsData.curOffset);
    statsData.curOffset = statsData.nextOffset;
    loadStatsData(false);
  }

  function statsDataPrev() {
    if (!statsData.stack.length) return;
    statsData.curOffset = statsData.stack.pop() || null;
    loadStatsData(false);
  }


  // ===== Admin =====
  let cachedStatus = null;

  async function refreshAdmin() {
    const r = await api.safeCall('get_status');
    if (!r || r.error) return;
    cachedStatus = r;
    fillDl('admin-qdrant', {
      'URL': r.qdrant_url || '-',
      'Collection': r.collection || '-',
      'Status': r.qdrant_ok ? 'OK' : (r.qdrant_error || 'down'),
      'Dim': r.embedding_dim || '-',
    });
    // v0.9.55.0: the admin-tab LLM card was REMOVED (user request —
    // it only echoed llm_provider/llm_model, which the footer status
    // bar already shows; the fields stay in get_status for it).
    // Embedding card needs separate backend_info call (more detail than
    // get_status). When it fails, fall back to the minimal info.
    const eb = await api.safeCall('backend_info');
    if (eb && !eb.error) renderEmbedAdmin(eb);
    else fillDl('admin-embed', {
      'Backend': r.embedding_dim || '-',
      '(error)': (eb && eb.error) || 'unavailable',
    });
    // v0.8.0+ MCP fleet editor — refresh on every admin visit
    // so the user always sees the latest config.
    if (typeof refreshMcp === 'function') await refreshMcp();
    // v0.9.59.1 — all-collections inventory + per-row delete.
    await refreshCollections();
  }

  async function refreshCollections() {
    const box = document.getElementById('admin-collections');
    if (!box) return;
    const r = await api.safeCall('list_collections');
    if (!r || r.error) {
      box.innerHTML = '';
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 5;
      td.textContent = (r && r.error) || 'unavailable';
      tr.appendChild(td);
      box.appendChild(tr);
      return;
    }
    const cols = r.collections || [];
    box.innerHTML = '';
    const thead = document.createElement('tr');
    [t('colName'), t('colPoints'), t('colDim'), t('colKind'), t('colActions')]
      .forEach(function (label) {
        const th = document.createElement('th');
        th.textContent = label;
        thead.appendChild(th);
      });
    box.appendChild(thead);
    if (!cols.length) {
      const tr = document.createElement('tr');
      const td = document.createElement('td');
      td.colSpan = 5;
      td.textContent = '(none)';
      tr.appendChild(td);
      box.appendChild(tr);
      return;
    }
    cols.forEach(function (c) {
      const tr = document.createElement('tr');
      if (c.configured) tr.className = 'configured';
      const tdName = document.createElement('td');
      tdName.textContent = (c.configured ? '★ ' : '') + (c.name || '?');
      tr.appendChild(tdName);
      const tdPts = document.createElement('td');
      tdPts.className = 'num';
      tdPts.textContent = String(c.points != null
        ? Number(c.points).toLocaleString() : '-');
      tr.appendChild(tdPts);
      const tdDim = document.createElement('td');
      tdDim.className = 'num';
      tdDim.textContent = c.dim != null ? String(c.dim) : '-';
      tr.appendChild(tdDim);
      const tdKind = document.createElement('td');
      tdKind.textContent = t('kind_' + (c.kind || 'other'));
      tr.appendChild(tdKind);
      const tdBtn = document.createElement('td');
      const btn = document.createElement('button');
      btn.className = 'btn btn-warn btn-small';
      btn.textContent = t('deleteCollection');
      btn.addEventListener('click', function () {
        adminDeleteCollection(c.name);
      });
      tdBtn.appendChild(btn);
      tr.appendChild(tdBtn);
      box.appendChild(tr);
    });
  }

  async function adminDeleteCollection(name) {
    if (!confirm(t('confirmDeleteCollection', { name }))) return;
    const r = await api.call('delete_collection_by_name', name);
    if (r && !r.error) {
      toast(t('colDeleted', { name }), 'success');
      refreshAdmin(); refreshStatus(); refreshStats();
    } else {
      toast(t('errGeneric', { msg: (r && r.error) || 'fail' }), 'error', 6000);
    }
  }

  function renderEmbedAdmin(info) {
    const disk = (info && info.disk_cache) || {};
    const mem = (info && info.memory_cache) || {};
    const diskPct = (disk.hit_ratio || 0) * 100;
    const diskSizeMB = disk.path ? '(see logs)' : '';
    fillDl('admin-embed', {
      'Backend': info.backend || '-',
      'Dim': (info.configured_dim || '-') + ' (native ' +
             (info.native_dim || '-') + ')',
      'In-mem cache': (mem.size || 0) + ' / ' + (mem.max || 0),
      'Disk cache': disk.enabled
        ? ((disk.n_entries || 0) + ' / ' +
           (disk.max_entries || 0) + ', ' +
           diskPct.toFixed(1) + '% hit ratio')
        : 'off',
    });
    const btn = document.getElementById('admin-clear-cache');
    if (btn) btn.disabled = !disk.enabled;
  }

  function fillDl(id, kv) {
    const dl = document.getElementById(id);
    if (!dl) return;
    dl.innerHTML = '';
    Object.keys(kv).forEach(function (k) {
      const dt = document.createElement('dt');
      dt.textContent = k;
      const dd = document.createElement('dd');
      dd.textContent = kv[k];
      dl.appendChild(dt);
      dl.appendChild(dd);
    });
  }

  async function adminRecreate() {
    if (!confirm(t('confirmRecreate'))) return;
    const r = await api.call('recreate_collection');
    if (r && !r.error) {
      toast('OK', 'success');
      refreshAdmin(); refreshStatus(); refreshStats();
    } else {
      toast(t('errGeneric', { msg: (r && r.error) || 'fail' }), 'error', 6000);
    }
  }

  async function adminDelete() {
    if (!confirm(t('confirmDelete'))) return;
    const r = await api.call('delete_collection');
    if (r && !r.error) {
      toast('OK', 'success');
      refreshAdmin(); refreshStatus(); refreshStats();
    } else {
      toast(t('errGeneric', { msg: (r && r.error) || 'fail' }), 'error', 6000);
    }
  }

  async function adminClearCache() {
    if (!confirm(t('confirmClearCache'))) return;
    const r = await api.call('cache_clear_all');
    if (r && !r.error) {
      toast(t('cacheCleared',
        { lru: r.lru_cleared, disk: r.disk_cleared }), 'success', 4000);
      refreshAdmin();
    } else {
      toast(t('errGeneric', { msg: (r && r.error) || 'fail' }), 'error', 6000);
    }
  }

  // ===== hash-pollution audit + repair (v0.9.112.0+) =====
  async function adminAuditBackends() {
    const r = await api.call('audit_embedding_backends');
    if (r && !r.error && r.ok !== false) {
      toast(t('auditDone', {
        scanned: r.scanned || 0, hash: (r.flagged || []).length,
      }), 'success', 7000);
    } else {
      toast(t('errGeneric', {
        msg: (r && (r.error || 'fail')) || 'fail',
      }), 'error', 6000);
    }
  }

  async function adminReembedHash() {
    if (!confirm(t('confirmReembed'))) return;
    const r = await api.call('reembed_hash_points');
    if (r && !r.error && r.ok !== false) {
      toast(t('reembedDone', {
        n: r.reembedded || 0, hash: (r.flagged || []).length,
        still: (r.still_hash || []).length,
      }), 'success', 9000);
      refreshAdmin();
    } else {
      toast(t('errGeneric', {
        msg: (r && (r.error || 'fail')) || 'fail',
      }), 'error', 6000);
    }
  }

  // ===== STAIR heading-path backfill (v0.9.113.0+) =====
  async function adminBackfillHeadings() {
    // api.call is positional: backfill_headings(collection, limit, force)
    const r = await api.call('backfill_headings', null, 0, false);
    if (r && !r.error && r.ok !== false) {
      toast(t('backfillDone', {
        n: r.updated || 0, scanned: r.scanned || 0,
        errors: r.errors || 0,
      }), r.errors ? 'warn' : 'success', 9000);
      refreshAdmin();
    } else {
      toast(t('errGeneric', { msg: (r && r.error) || 'fail' }), 'error', 6000);
    }
  }

  // ===== Hot reload + service restart (v0.9.42.0+) =====
  async function adminReloadConfig() {
    const r = await api.call('reload_runtime_config');
    if (r && r.status === 'ok') {
      const changed = (r.changed || []).join(', ') || '-';
      const svc = Object.entries(r.services || {})
        .map(([k, v]) => k + '=' + v).join(', ') || '-';
      toast(t('reloadDone', { changed, svc }), 'success', 7000);
      refreshAdmin(); refreshStatus();
    } else {
      toast(t('errGeneric', { msg: (r && r.error) || 'fail' }), 'error', 6000);
    }
  }

  // ===== MCP fleet editor (v0.8.0+) =====
  // We hold the working copy of the config in ``mcpConfig`` so
  // edits never touch disk until the user presses Save.

  let mcpConfig = null;        // {path, exists, servers: [...]}
  let mcpEditing = null;       // original entry when editing (or null = add)

  function escapeHtml(s) {
    return String(s || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function parseKvLines(text) {
    const out = {};
    String(text || '').split('\n').forEach((line) => {
      const m = line.match(/^\s*([A-Za-z0-9_\-.]+)\s*=\s*(.*)\s*$/);
      if (m) out[m[1]] = m[2];
    });
    return out;
  }

  function kvToText(obj) {
    if (!obj) return '';
    return Object.entries(obj)
      .map(([k, v]) => `${k}=${v}`)
      .join('\n');
  }

  async function refreshMcp() {
    const r = await api.safeCall('get_mcp_config');
    if (!r || r.error) {
      toast(t('errGeneric',
        { msg: (r && r.error) || 'fail' }), 'error', 6000);
      return;
    }
    mcpConfig = r;
    renderMcp();
  }

  function renderMcp() {
    if (!mcpConfig) return;
    const pathEl = document.getElementById('mcp-config-path');
    if (pathEl) {
      const p = mcpConfig.path || '(no file)';
      pathEl.textContent =
        p + (mcpConfig.exists ? '' : '  [will be created]');
    }
    const summary = document.getElementById('mcp-status-summary');
    if (summary) {
      const n = (mcpConfig.servers || []).length;
      summary.textContent =
        t('mcpServerCount', { n }) +
        '  ·  + local hosts (always on)';
    }
    const banner = document.getElementById('mcp-error-banner');
    if (banner) {
      if (mcpConfig.parse_error) {
        banner.textContent =
          t('mcpParseError', { msg: mcpConfig.parse_error });
        banner.hidden = false;
      } else {
        banner.hidden = true;
      }
    }
    const list = document.getElementById('mcp-server-list');
    if (!list) return;
    list.innerHTML = '';
    (mcpConfig.servers || []).forEach((srv, idx) => {
      list.appendChild(renderMcpItem(srv, idx));
    });
  }

  function renderMcpItem(srv, idx) {
    const li = document.createElement('li');
    li.className = 'mcp-server-item';
    if (srv.error) li.classList.add('invalid');

    const info = document.createElement('div');
    info.className = 'mcp-server-info';
    const name = srv.entry.name || `<unnamed #${idx + 1}>`;
    const transport = srv.entry.transport || 'stdio';
    const statusBadge = srv.error
      ? `<span class="badge err">⚠ ${t('mcpInvalid')}</span>`
      : `<span class="badge ok">✓ ${t('mcpValid')}</span>`;
    const transportBadge =
      `<span class="badge">${escapeHtml(transport)}</span>`;
    const preview = JSON.stringify(srv.entry, null, 2);
    info.innerHTML =
      `<h4>
         <strong>${escapeHtml(name)}</strong>
         ${statusBadge}
         ${transportBadge}
       </h4>` +
      (srv.error
        ? `<div class="mcp-error-banner" style="margin:4px 0">${escapeHtml(srv.error)}</div>`
        : '') +
      `<pre>${escapeHtml(preview)}</pre>`;

    const actions = document.createElement('div');
    actions.className = 'mcp-server-actions';

    const restartBtn = document.createElement('button');
    restartBtn.className = 'btn';
    restartBtn.textContent = '🔄 ' + t('mcpRestart');
    restartBtn.title = t('mcpRestartHint');
    restartBtn.addEventListener('click', () =>
      restartMcpServer(name));

    // v0.8.2+: per-server connection / tool probe. Calls
    // ``get_mcp_server_logs`` and pops a modal with status +
    // advertised tool inventory. Doesn't restart anything.
    const testBtn = document.createElement('button');
    testBtn.className = 'btn';
    testBtn.textContent = '🧪 ' + t('mcpTest');
    testBtn.title = t('mcpTestHint');
    testBtn.addEventListener('click', () =>
      testMcpServer(name));

    const editBtn = document.createElement('button');
    editBtn.className = 'btn';
    editBtn.textContent = '✏ ' + t('edit');
    editBtn.addEventListener('click', () =>
      openMcpEditor(srv.entry));

    const delBtn = document.createElement('button');
    delBtn.className = 'btn btn-danger';
    delBtn.textContent = '🗑 ' + t('delete');
    delBtn.addEventListener('click', () =>
      removeMcpServer(name));

    actions.appendChild(restartBtn);
    actions.appendChild(testBtn);
    actions.appendChild(editBtn);
    actions.appendChild(delBtn);

    li.appendChild(info);
    li.appendChild(actions);
    return li;
  }

  function openMcpEditor(entry) {
    mcpEditing = entry || null;
    document.getElementById('mcp-edit-orig-name').value =
      (entry && entry.name) || '';
    document.getElementById('mcp-edit-name').value =
      (entry && entry.name) || '';
    const transportSel =
      document.getElementById('mcp-edit-transport');
    transportSel.value = (entry && entry.transport) || 'stdio';
    transportSel.dispatchEvent(new Event('change'));

    document.getElementById('mcp-edit-command').value =
      entry && Array.isArray(entry.command)
        ? entry.command.join(' ') : '';
    document.getElementById('mcp-edit-env').value =
      entry ? kvToText(entry.env) : '';
    document.getElementById('mcp-edit-cwd').value =
      entry && entry.cwd ? entry.cwd : '';
    const urlSh = document.getElementById('mcp-edit-url-sh');
    const urlSse = document.getElementById('mcp-edit-url-sse');
    if (urlSh) urlSh.value = entry && entry.url ? entry.url : '';
    if (urlSse) urlSse.value = entry && entry.url ? entry.url : '';
    document.getElementById('mcp-edit-headers-sh').value =
      entry ? kvToText(entry.headers) : '';
    document.getElementById('mcp-edit-headers-sse').value =
      entry ? kvToText(entry.headers) : '';

    const errBox = document.getElementById('mcp-edit-error');
    if (errBox) {
      errBox.hidden = true;
      errBox.textContent = '';
    }
    document.getElementById('mcp-edit-modal').hidden = false;
  }

  function closeMcpEditor() {
    document.getElementById('mcp-edit-modal').hidden = true;
    mcpEditing = null;
  }

  function updateMcpTransportFields() {
    const tr = document.getElementById('mcp-edit-transport').value;
    document.querySelectorAll('.mcp-transport-fields').forEach((el) => {
      if (el.dataset.transport === tr) el.classList.add('active');
      else el.classList.remove('active');
    });
  }

  function buildMcpEntryFromForm() {
    const name = document.getElementById('mcp-edit-name').value.trim();
    const transport =
      document.getElementById('mcp-edit-transport').value;
    if (!name) return { error: t('mcpFieldNameRequired') };
    const entry = { name, transport };
    if (transport === 'stdio') {
      const cmd =
        document.getElementById('mcp-edit-command').value.trim();
      if (!cmd) return { error: t('mcpFieldCommandRequired') };
      entry.command = cmd.split(/\s+/).filter(Boolean);
      const envTxt = document.getElementById('mcp-edit-env').value;
      const env = parseKvLines(envTxt);
      if (Object.keys(env).length) entry.env = env;
      const cwd =
        document.getElementById('mcp-edit-cwd').value.trim();
      if (cwd) entry.cwd = cwd;
    } else {
      const urlId = transport === 'sse'
        ? 'mcp-edit-url-sse'
        : 'mcp-edit-url-sh';
      const url = document.getElementById(urlId).value.trim();
      if (!url) return { error: t('mcpFieldUrlRequired') };
      entry.url = url;
      const hdrId = transport === 'sse'
        ? 'mcp-edit-headers-sse'
        : 'mcp-edit-headers-sh';
      const headers = parseKvLines(
        document.getElementById(hdrId).value);
      if (Object.keys(headers).length) entry.headers = headers;
    }
    return { entry };
  }

  function saveMcpFromEditor() {
    const out = buildMcpEntryFromForm();
    if (out.error) {
      const errBox = document.getElementById('mcp-edit-error');
      errBox.textContent = out.error;
      errBox.hidden = false;
      return;
    }
    const newEntry = out.entry;
    const origName = document
      .getElementById('mcp-edit-orig-name').value;
    const servers = (mcpConfig.servers || [])
      .map((s) => JSON.parse(JSON.stringify(s.entry)));
    if (origName) {
      const i = servers.findIndex((s) => s.name === origName);
      if (i >= 0) servers[i] = newEntry;
      else servers.push(newEntry);
    } else {
      if (servers.some((s) => s.name === newEntry.name)) {
        const errBox = document.getElementById('mcp-edit-error');
        errBox.textContent = t('mcpFieldNameDuplicate',
          { name: newEntry.name });
        errBox.hidden = false;
        return;
      }
      servers.push(newEntry);
    }
    saveMcpServers(servers, true);
  }

  function removeMcpServer(name) {
    if (!confirm(t('confirmRemoveMcp', { name }))) return;
    const servers = (mcpConfig.servers || [])
      .filter((s) => s.entry.name !== name)
      .map((s) => JSON.parse(JSON.stringify(s.entry)));
    saveMcpServers(servers, false);
  }

  async function saveMcpServers(servers, closeEditorOnSuccess) {
    const r = await api.safeCall('save_mcp_config', { servers });
    if (!r || r.error) {
      toast(t('errGeneric',
        { msg: (r && r.error) || 'save failed' }),
        'error', 6000);
      return;
    }
    toast(t('mcpSaved',
      { n: r.saved, tools: r.tools }), 'success', 3500);
    if (closeEditorOnSuccess) closeMcpEditor();
    await refreshMcp();
  }

  // ── v0.8.2+: per-server connection / tool probe ─────────────
  // Calls ``get_mcp_server_logs`` and pops a modal that shows
  // connection state + the tool inventory the server actually
  // exposed after handshake. Independent of restart — useful for
  // diagnosing "why is this server showing ⚠ invalid".
  async function testMcpServer(name) {
    const r = await api.safeCall('get_mcp_server_logs', name);
    if (!r || r.error) {
      toast(t('errGeneric',
        { msg: (r && r.error) || 'test failed' }),
        'error', 6000);
      return;
    }
    const status = r.connected
      ? `<span class="badge ok">✓ connected</span>`
      : `<span class="badge err">⚠ disconnected</span>`;
    const failLine = r.failed_reason
      ? `<div class="mcp-error-banner" style="margin:6px 0">` +
        escapeHtml(String(r.failed_reason)) + `</div>`
      : '';
    const toolsList = (r.tools || []).length
      ? `<ul>${r.tools.map((tn) =>
          `<li><code>${escapeHtml(tn)}</code></li>`
        ).join('')}</ul>`
      : `<em>(server did not advertise any tools)</em>`;
    const html =
      `<h3>🧪 ${t('mcpTestTitle', { name: escapeHtml(name) })}</h3>` +
      `<p>${status}  <span class="hint">` +
      t('mcpTestCount', { n: r.tool_count || 0 }) +
      `</span></p>` +
      failLine +
      `<div>${toolsList}</div>`;
    showInfoModal(t('mcpTest'), html);
  }

  // ── v0.8.2+: raw JSON editor toggle / save / format ──────────
  function toggleMcpRawEditor() {
    const el = document.getElementById('mcp-raw-editor');
    if (!el) return;
    const willShow = el.hidden;
    el.hidden = !willShow;
    if (willShow) {
      const ta = document.getElementById('mcp-raw-textarea');
      if (ta) {
        ta.value = (mcpConfig && mcpConfig.raw_text)
          || JSON.stringify(
              { servers: (mcpConfig.servers || [])
                  .map((s) => s.entry) },
              null, 2,
            );
      }
      hideMcpRawError();
    }
  }

  function hideMcpRawError() {
    const box = document.getElementById('mcp-raw-error');
    if (box) { box.hidden = true; box.textContent = ''; }
  }

  function showMcpRawError(msg) {
    const box = document.getElementById('mcp-raw-error');
    if (!box) return;
    box.textContent = msg;
    box.hidden = false;
  }

  function formatMcpRawJson() {
    const ta = document.getElementById('mcp-raw-textarea');
    if (!ta) return;
    try {
      const obj = JSON.parse(ta.value || '{}');
      ta.value = JSON.stringify(obj, null, 2);
      hideMcpRawError();
    } catch (e) {
      showMcpRawError(`JSON 解析失敗: ${e.message}`);
    }
  }

  async function saveMcpRawJson() {
    const ta = document.getElementById('mcp-raw-textarea');
    if (!ta) return;
    let parsed;
    try {
      parsed = JSON.parse(ta.value || '{}');
    } catch (e) {
      showMcpRawError(`JSON 解析失敗: ${e.message}`);
      return;
    }
    if (
      !parsed
      || typeof parsed !== 'object'
      || Array.isArray(parsed)
      || !Array.isArray(parsed.servers)
    ) {
      showMcpRawError(
        'root 必須是 JSON object,含 "servers" array'
      );
      return;
    }
    await saveMcpServers(parsed.servers, false);
    hideMcpRawError();
    document.getElementById('mcp-raw-editor').hidden = true;
  }

  async function restartMcpServer(name) {
    const r = await api.safeCall('restart_mcp_server', name);
    if (!r || r.error) {
      toast(t('errGeneric',
        { msg: (r && r.error) || 'restart failed' }),
        'error', 6000);
      return;
    }
    toast(t('mcpRestarted',
      { name, tools: r.tools }), 'success', 3000);
    await refreshMcp();
  }

  // ── v0.8.2+: tool 總覽 (active McpManager catalog) ──────────
  async function refreshMcpOverview() {
    const box = document.getElementById('mcp-tools-overview-content');
    if (!box) return;
    box.innerHTML = `<em>${t('mcpOverviewLoading')}</em>`;
    const r = await api.safeCall('list_mcp_tools');
    if (!r || r.error) {
      box.innerHTML =
        `<div class="mcp-error-banner">` +
        escapeHtml((r && r.error) || 'list_mcp_tools failed') +
        `</div>`;
      return;
    }
    const tools = r.tools || [];
    const failed = r.failed || {};
    if (!tools.length && !Object.keys(failed).length) {
      box.innerHTML = `<div class="mcp-tools-empty">` +
        t('mcpOverviewEmpty') + `</div>`;
      return;
    }
    const groups = new Map();
    for (const tl of tools) {
      if (!groups.has(tl.server)) groups.set(tl.server, []);
      groups.get(tl.server).push(tl);
    }
    let html = '';
    for (const [srv, items] of groups) {
      const isFailed = !!failed[srv];
      const countCls = isFailed ? 'failed' : 'ok';
      const countText = isFailed
        ? `⚠ ${escapeHtml(String(failed[srv]).slice(0, 60))}`
        : `${items.length} tools`;
      html += `<div class="mcp-tools-group">` +
        `<div class="mcp-tools-group-header">` +
        `<span class="mcp-tools-group-name">` +
        escapeHtml(srv) + `</span>` +
        `<span class="mcp-tools-group-count ${countCls}">` +
        countText + `</span>` +
        `</div>`;
      for (const tool of items) {
        const params = renderToolParams(tool.schema);
        const desc = tool.description
          ? `<div class="mcp-tool-item-desc">` +
            escapeHtml(tool.description) + `</div>`
          : '';
        html += `<div class="mcp-tool-item">` +
          `<div class="mcp-tool-item-name">` +
          escapeHtml(tool.name) + `</div>` +
          desc + params + `</div>`;
      }
      html += `</div>`;
    }
    // Surface fully-failed servers (configured but not connected).
    const connectedServers = new Set(groups.keys());
    for (const srv of Object.keys(failed)) {
      if (connectedServers.has(srv)) continue;
      html += `<div class="mcp-tools-group">` +
        `<div class="mcp-tools-group-header">` +
        `<span class="mcp-tools-group-name">` +
        escapeHtml(srv) + `</span>` +
        `<span class="mcp-tools-group-count failed">` +
        `⚠ ${escapeHtml(String(failed[srv]).slice(0, 60))}` +
        `</span>` +
        `</div>` +
        `<div class="mcp-tools-empty">` +
        t('mcpOverviewServerFailed') + `</div>` +
        `</div>`;
    }
    box.innerHTML = html;
  }

  function renderToolParams(schema) {
    if (
      !schema
      || typeof schema !== 'object'
      || !schema.properties
      || typeof schema.properties !== 'object'
    ) return '';
    const required = Array.isArray(schema.required)
      ? new Set(schema.required) : new Set();
    const keys = Object.keys(schema.properties);
    if (!keys.length) return '';
    const spans = keys.map((k) => {
      const type = schema.properties[k].type || '?';
      const isReq = required.has(k);
      const cls = isReq ? 'mcp-tool-item-param req' : 'mcp-tool-item-param';
      return `<span class="${cls}">` +
        escapeHtml(k) + ':' + escapeHtml(type) +
        (isReq ? '*' : '') + `</span>`;
    }).join('');
    return `<div class="mcp-tool-item-params">` +
      `<span class="hint">params:</span> ` + spans + `</div>`;
  }

  function toggleMcpOverview() {
    const el = document.getElementById('mcp-tools-overview');
    if (!el) return;
    const willShow = el.hidden;
    el.hidden = !willShow;
    if (willShow) refreshMcpOverview();
  }

  // ── v0.8.2+: tiny info modal (reuses #mcp-edit-modal markup) ──
  // Falls back to alert() if the modal markup isn't present.
  function showInfoModal(titleHtml, bodyHtml) {
    const modal = document.getElementById('mcp-edit-modal');
    const header = modal && modal.querySelector('.modal-header h3');
    const body = modal && modal.querySelector('.mcp-edit-form');
    if (!modal || !header || !body) {
      alert(bodyHtml.replace(/<[^>]+>/g, ''));
      return;
    }
    header.innerHTML = titleHtml;
    body.innerHTML = bodyHtml;
    const footer = modal.querySelector('.modal-footer');
    if (footer) footer.style.display = 'none';
    modal.hidden = false;
  }

  function bindMcpEditor() {
    document.getElementById('mcp-refresh')
      .addEventListener('click', refreshMcp);
    document.getElementById('mcp-add')
      .addEventListener('click', () => openMcpEditor(null));
    document.getElementById('mcp-save-all')
      .addEventListener('click', () => {
        const servers = (mcpConfig.servers || [])
          .map((s) => JSON.parse(JSON.stringify(s.entry)));
        saveMcpServers(servers, false);
      });
    // v0.8.2+: raw JSON editor + tool 總覽 toggles.
    const rawToggle = document.getElementById('mcp-toggle-raw');
    if (rawToggle) rawToggle.addEventListener('click',
      toggleMcpRawEditor);
    const overviewToggle = document.getElementById(
      'mcp-overview-toggle');
    if (overviewToggle) overviewToggle.addEventListener('click',
      toggleMcpOverview);
    const rawFormat = document.getElementById('mcp-raw-format');
    if (rawFormat) rawFormat.addEventListener('click',
      formatMcpRawJson);
    const rawCancel = document.getElementById('mcp-raw-cancel');
    if (rawCancel) rawCancel.addEventListener('click',
      () => {
        document.getElementById('mcp-raw-editor').hidden = true;
        hideMcpRawError();
      });
    const rawSave = document.getElementById('mcp-raw-save');
    if (rawSave) rawSave.addEventListener('click', saveMcpRawJson);

    document.getElementById('mcp-edit-close')
      .addEventListener('click', closeMcpEditor);
    document.getElementById('mcp-edit-cancel')
      .addEventListener('click', closeMcpEditor);
    document.getElementById('mcp-edit-transport')
      .addEventListener('change', updateMcpTransportFields);
    document.getElementById('mcp-edit-form')
      .addEventListener('submit', (e) => {
        e.preventDefault();
        saveMcpFromEditor();
      });
    updateMcpTransportFields();
  }


  // ===== Stats =====
  async function refreshStats() {
    await refreshCollectionSelectors();
    const sel = document.getElementById('stats-collections');
    const collection = (sel && sel.value) || null;
    if (collection && statsData.collection !== collection) {
      resetStatsDataBrowser();
    }
    const r = await api.safeCall('get_stats', collection);
    if (!r || r.error) return;
    document.getElementById('stat-collection').textContent = r.collection || '-';
    document.getElementById('stat-vectors').textContent =
      (r.num_entities != null) ? r.num_entities : '-';
    document.getElementById('stat-dim').textContent =
      r.dim != null ? r.dim : '-';
    document.getElementById('stat-docs').textContent =
      r.doc_count != null ? r.doc_count : ((r.documents || []).length);
    const ul = document.getElementById('stats-doc-list');
    if (ul) {
      ul.innerHTML = '';
      // If the backend told us list_documents failed (rare — usually
      // Qdrant down), surface that instead of leaving the list empty.
      if (r.docs_error) {
        const li = document.createElement('li');
        li.className = 'doc-list-error';
        li.textContent = t('docsError', { msg: r.docs_error });
        ul.appendChild(li);
        return;
      }
      // Render each doc as a checkbox + label + 🗑 button so the
      // user can multi-select then bulk-remove. v0.7.0+.
      const docs = (r.documents || []);
      if (docs.length === 0) {
        // v0.9.60.0+: non-RAG collections have no filename payload
        // index — point the user at the paged browser below.
        const li = document.createElement('li');
        li.className = 'hint';
        li.textContent = t('noDocIndex');
        ul.appendChild(li);
      }
      docs.forEach(function (d) {
        const li = document.createElement('li');
        li.className = 'doc-list-item';

        const cb = document.createElement('input');
        cb.type = 'checkbox';
        cb.value = d;
        cb.className = 'doc-list-cb';
        cb.addEventListener('click', updateBulkDeleteBtn);

        const label = document.createElement('span');
        label.className = 'doc-list-label';
        label.textContent = d;
        label.title = d;

        const btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn btn-danger doc-list-del';
        btn.dataset.filename = d;
        btn.textContent = '🗑';
        btn.title = t('removeOneTitle');
        btn.addEventListener('click', function () {
          removeOneDocument(d);
        });

        li.appendChild(cb);
        li.appendChild(label);
        li.appendChild(btn);
        ul.appendChild(li);
      });
      // Refresh the Bulk Delete button state after each render.
      updateBulkDeleteBtn();
    }
  }

  // Toggle the bulk-delete button based on how many checkboxes are ticked.
  function updateBulkDeleteBtn() {
    const btn = document.getElementById('docs-bulk-delete');
    if (!btn) return;
    const ticked = document.querySelectorAll('.doc-list-cb:checked').length;
    btn.disabled = ticked === 0;
    btn.textContent = t('removeSelected', { n: ticked });
  }

  // Remove a single document after confirmation.
  async function removeOneDocument(filename) {
    if (!confirm(t('confirmRemoveOne', { name: filename }))) return;
    await removeDocumentsAndRefresh([filename]);
  }

  // Remove the documents currently checked in the list.
  async function removeSelectedDocuments() {
    const ticked = Array.from(
      document.querySelectorAll('.doc-list-cb:checked')
    ).map(function (cb) { return cb.value; });
    if (ticked.length === 0) return;
    if (!confirm(t('confirmRemoveMany', { n: ticked.length }))) return;
    await removeDocumentsAndRefresh(ticked);
  }

  // Shared handler: call api.remove_documents, render feedback, refresh.
  async function removeDocumentsAndRefresh(filenames) {
    const r = await api.safeCall('remove_documents', filenames);
    if (!r || r.error) return; // toast already shown by safeCall
    const removed = r.removed || {};
    const missing = r.missing || [];
    const removedCount = Object.values(removed)
      .reduce(function (a, b) { return a + b; }, 0);
    let msg = t('removedToast',
      { files: Object.keys(removed).length, chunks: removedCount });
    if (missing.length > 0) {
      msg += t('removedMissing', { n: missing.length });
    }
    toast(msg, 'success', 4000);
    refreshStats();
  }

  // ===== Schedules (v0.8.0+) =====
  let schedulesCache = [];

  async function refreshSchedules() {
    const r = await api.safeCall('list_schedules');
    if (!r || r.error) {
      toast(t('errGeneric',
        { msg: (r && r.error) || 'fail' }), 'error', 6000);
      return;
    }
    schedulesCache = r.schedules || [];
    renderSchedules();
    const status = document.getElementById('schedules-status');
    if (status) {
      const n = schedulesCache.filter((s) => s.enabled).length;
      status.textContent = t('schedulesStatusText', {
        n: schedulesCache.length,
        enabled: n,
        daemon: r.daemon,
      });
    }
  }

  function renderSchedules() {
    const tbody = document.getElementById('schedules-tbody');
    if (!tbody) return;
    const enabledOnly =
      !!document.getElementById('schedules-enabled-only')?.checked;
    tbody.innerHTML = '';
    const rows = enabledOnly
      ? schedulesCache.filter((s) => s.enabled)
      : schedulesCache;
    for (const s of rows) {
      tbody.appendChild(renderScheduleRow(s));
    }
  }

  function renderScheduleRow(s) {
    const tr = document.createElement('tr');
    const detail = scheduleDetail(s);
    tr.innerHTML =
      `<td><strong>${escapeHtml(s.name || s.id)}</strong><br/>` +
      `<small>${escapeHtml(s.id)}</small></td>` +
      `<td>${escapeHtml(s.schedule_type)}${detail ? '<br/><small>' + escapeHtml(detail) + '</small>' : ''}</td>` +
      `<td>${escapeHtml(s.next_fire_at || '-')}</td>` +
      `<td>${s.fire_count}</td>` +
      `<td><span class="badge ${s.enabled ? 'on' : 'off'}">${s.enabled ? 'ON' : 'OFF'}</span></td>` +
      `<td class="actions"></td>`;
    const actions = tr.querySelector('.actions');
    const editBtn = makeBtn('✏ ' + t('edit'),
      () => openScheduleEditor(s));
    const runBtn = makeBtn('▶ ' + t('schedulesRunNow'),
      () => runScheduleNow(s.id));
    const toggleBtn = makeBtn(
      s.enabled ? '⏸ ' + t('schedulesDisable') : '▶ ' + t('schedulesEnable'),
      () => toggleSchedule(s.id, !s.enabled),
    );
    const delBtn = makeBtn('🗑 ' + t('delete'),
      () => deleteSchedule(s.id));
    actions.appendChild(editBtn);
    actions.appendChild(runBtn);
    actions.appendChild(toggleBtn);
    actions.appendChild(delBtn);
    return tr;
  }

  function scheduleDetail(s) {
    if (s.schedule_type === 'interval_seconds') return `${s.interval_seconds}s`;
    if (s.schedule_type === 'cron') return s.cron || '';
    if (s.schedule_type === 'once_at') return s.once_at || '';
    return '';
  }

  function makeBtn(label, onClick) {
    const b = document.createElement('button');
    b.className = 'btn';
    b.textContent = label;
    b.addEventListener('click', onClick);
    return b;
  }

  function openScheduleEditor(s) {
    document.getElementById('schedule-edit-orig-id').value =
      (s && s.id) || '';
    document.getElementById('schedule-edit-name').value =
      (s && s.name) || '';
    document.getElementById('schedule-edit-prompt').value =
      (s && s.prompt) || '';
    const typeSel = document.getElementById('schedule-edit-type');
    typeSel.value = (s && s.schedule_type) || 'interval_seconds';
    typeSel.dispatchEvent(new Event('change'));
    document.getElementById('schedule-edit-interval').value =
      s && s.interval_seconds ? s.interval_seconds : '';
    document.getElementById('schedule-edit-cron').value =
      s && s.cron ? s.cron : '';
    document.getElementById('schedule-edit-once-at').value =
      s && s.once_at ? s.once_at : '';
    document.getElementById('schedule-edit-use-tools').checked =
      s ? !!s.use_tools : true;
    document.getElementById('schedule-edit-enabled').checked =
      s ? !!s.enabled : true;
    const errBox = document.getElementById('schedule-edit-error');
    if (errBox) {
      errBox.hidden = true;
      errBox.textContent = '';
    }
    document.getElementById('schedule-edit-modal').hidden = false;
  }

  function closeScheduleEditor() {
    document.getElementById('schedule-edit-modal').hidden = true;
  }

  function updateScheduleTypeFields() {
    const stype = document.getElementById('schedule-edit-type').value;
    document.querySelectorAll('.schedule-transport-fields')
      .forEach((el) => {
        if (el.dataset.stype === stype) el.classList.add('active');
        else el.classList.remove('active');
      });
  }

  function buildScheduleFromForm() {
    const name = document.getElementById('schedule-edit-name').value.trim();
    const prompt = document.getElementById('schedule-edit-prompt').value;
    const stype = document.getElementById('schedule-edit-type').value;
    if (!name) return { error: t('scheduleFieldNameRequired') };
    if (!prompt) return { error: t('scheduleFieldPromptRequired') };
    const out = {
      name,
      prompt,
      schedule_type: stype,
      use_tools:
        document.getElementById('schedule-edit-use-tools').checked,
      enabled:
        document.getElementById('schedule-edit-enabled').checked,
    };
    if (stype === 'interval_seconds') {
      const v = parseInt(
        document.getElementById('schedule-edit-interval').value);
      if (!v || v <= 0)
        return { error: t('scheduleFieldIntervalRequired') };
      out.interval_seconds = v;
    } else if (stype === 'cron') {
      const v = document.getElementById('schedule-edit-cron').value.trim();
      if (!v) return { error: t('scheduleFieldCronRequired') };
      out.cron = v;
    } else if (stype === 'once_at') {
      const v =
        document.getElementById('schedule-edit-once-at').value.trim();
      if (!v) return { error: t('scheduleFieldOnceAtRequired') };
      out.once_at = v;
    }
    return { payload: out };
  }

  async function saveScheduleFromEditor() {
    const out = buildScheduleFromForm();
    if (out.error) {
      const errBox = document.getElementById('schedule-edit-error');
      errBox.textContent = out.error;
      errBox.hidden = false;
      return;
    }
    const origId =
      document.getElementById('schedule-edit-orig-id').value;
    let r;
    if (origId) {
      r = await api.safeCall('update_schedule', origId, out.payload);
    } else {
      r = await api.safeCall('add_schedule', out.payload);
    }
    if (!r || r.error) {
      toast(t('errGeneric',
        { msg: (r && r.error) || 'save failed' }),
        'error', 6000);
      return;
    }
    toast(t('scheduleSaved', { id: r.schedule.id }),
      'success', 2500);
    closeScheduleEditor();
    await refreshSchedules();
  }

  async function toggleSchedule(id, enabled) {
    const r = await api.safeCall(
      'update_schedule', id, { enabled }
    );
    if (!r || r.error) {
      toast(t('errGeneric',
        { msg: (r && r.error) || 'fail' }), 'error', 6000);
      return;
    }
    toast(t('scheduleToggled',
      { id, state: enabled ? 'enabled' : 'disabled' }),
      'success', 2000);
    await refreshSchedules();
  }

  async function deleteSchedule(id) {
    if (!confirm(t('confirmDeleteSchedule', { id }))) return;
    const r = await api.safeCall('delete_schedule', id);
    if (!r || r.error) {
      toast(t('errGeneric',
        { msg: (r && r.error) || 'fail' }), 'error', 6000);
      return;
    }
    toast(t('scheduleDeleted', { id }), 'success', 2000);
    await refreshSchedules();
  }

  async function runScheduleNow(id) {
    const r = await api.safeCall('run_schedule_now', id);
    if (!r || r.error) {
      toast(t('errGeneric',
        { msg: (r && r.error) || 'fire failed' }),
        'error', 8000);
      return;
    }
    toast(t('scheduleFired',
      { id, session: r.session_id }), 'success', 4000);
    await refreshSchedules();
  }

  function bindSchedules() {
    const refreshBtn = document.getElementById('schedules-refresh');
    if (refreshBtn) refreshBtn.addEventListener('click', refreshSchedules);
    const addBtn = document.getElementById('schedules-add');
    if (addBtn) addBtn.addEventListener('click',
      () => openScheduleEditor(null));
    const enabledOnly =
      document.getElementById('schedules-enabled-only');
    if (enabledOnly)
      enabledOnly.addEventListener('change', renderSchedules);
    document.getElementById('schedule-edit-close')
      .addEventListener('click', closeScheduleEditor);
    document.getElementById('schedule-edit-cancel')
      .addEventListener('click', closeScheduleEditor);
    document.getElementById('schedule-edit-type')
      .addEventListener('change', updateScheduleTypeFields);
    document.getElementById('schedule-edit-form')
      .addEventListener('submit', (e) => {
        e.preventDefault();
        saveScheduleFromEditor();
      });
    updateScheduleTypeFields();
  }

  // ===== Sessions =====
  async function refreshSessions() {
    const r = await api.safeCall('list_sessions');
    const tbody = document.getElementById('sessions-tbody');
    if (!tbody) return;
    tbody.innerHTML = '';
    if (!r || r.error) {
      tbody.innerHTML = '<tr><td colspan="7">' +
        escapeHtml(t('errGeneric', { msg: (r && r.error) || 'fail' })) +
        '</td></tr>';
      return;
    }
    (r.sessions || []).forEach(function (s) {
      const tr = document.createElement('tr');
      // v0.9.133.7 — instant summary line (topic + latest answer), no
      // LLM call: computed server-side by SessionManager.list().
      const su = s.summary_first_user || '';
      const la = s.summary_last_assistant || '';
      const summaryHtml =
        '<div class="sess-summary" title="'
        + escapeHtml(su) + '">💬 ' + escapeHtml(
          (su || '(—)')) + '</div>' +
        (la ? '<div class="sess-summary sess-summary-2" title="'
             + escapeHtml(la) + '">🤖 ' + escapeHtml(la) + '</div>'
            : '');
      tr.innerHTML =
        '<td class="sel-col"><input type="checkbox" class="sess-sel"' +
        ' data-id="' + escapeHtml(s.id) + '"></td>' +
        '<td><code>' + escapeHtml(s.id) + '</code></td>' +
        '<td>' + escapeHtml(s.title || '') + '</td>' +
        '<td>' + s.n_messages + '</td>' +
        '<td>' + escapeHtml((s.updated_at || '').slice(0, 19)) + '</td>' +
        '<td class="sess-summary-cell">' + summaryHtml + '</td>' +
        '<td class="actions">' +
          '<button class="btn" data-act="load" data-id="' + escapeHtml(s.id) + '">Load</button>' +
          // v0.9.103.5 — per-row session title rename ✏️.
          '<button class="btn" data-act="rename" data-id="' + escapeHtml(s.id) + '">✏️ ' +
            escapeHtml(t('sessRename')) + '</button>' +
          '<button class="btn btn-danger" data-act="delete" data-id="' + escapeHtml(s.id) + '">' +
            escapeHtml(t('delete')) + '</button>' +
        '</td>';
      tbody.appendChild(tr);
      // Hand the CURRENT title to the ✏️ handler via the dataset —
      // property assignment, so CJK / quotes need NO html escaping.
      const renBtn = tr.querySelector('button[data-act="rename"]');
      if (renBtn) renBtn.dataset.curTitle = s.title || '';
    });
    tbody.onclick = async function (ev) {
      const btn = ev.target.closest('button[data-act]');
      if (!btn) return;
      const id = btn.dataset.id;
      const act = btn.dataset.act;
      if (act === 'load') {
        await loadSessionById(id);
        switchTab('chat');
      } else if (act === 'rename') {
        // v0.9.103.5 — per-row session title rename.
        await sessionsRename(id, btn.dataset.curTitle || '');
      } else if (act === 'delete') {
        if (!confirm(t('confirmDelete'))) return;
        const d = await api.call('delete_session', id);
        if (!d || d.error) {
          toast(t('errGeneric', { msg: (d && d.error) || 'fail' }), 'error');
        } else {
          toast('OK', 'success', 1500);
          refreshSessions();
        }
      }
    };
    // v0.9.103.1 — per-row checkbox → toolbar count / tri-state sync.
    tbody.onchange = function (ev) {
      if (ev.target.classList && ev.target.classList.contains('sess-sel')) {
        _updateSessionsToolbar();
      }
    };
    _updateSessionsToolbar();
    document.getElementById('sess-del-sel').onclick = sessionsDeleteSelected;
    document.getElementById('sess-export-sel').onclick = sessionsExportSelected;
  }

  // ─── v0.9.103.1 — Sessions-tab multi-select batch ops ───────────
  function _sessSelectedIds() {
    const out = [];
    document.querySelectorAll('.sess-sel:checked').forEach(function (cb) {
      const id = cb.dataset.id || '';
      if (id && out.indexOf(id) === -1) out.push(id);
    });
    return out;
  }

  function _updateSessionsToolbar() {
    const delBtn = document.getElementById('sess-del-sel');
    const expBtn = document.getElementById('sess-export-sel');
    if (!delBtn || !expBtn) return;
    const ids = _sessSelectedIds();
    delBtn.textContent = '🗑 ' + t('sessDeleteSel', { n: ids.length });
    delBtn.disabled = ids.length === 0;
    expBtn.textContent = '⬇ ' + t('sessExportSel', { n: ids.length });
    expBtn.disabled = ids.length === 0;
    // ☑ tri-state select-all: checked / unchecked / indeterminate.
    const head = document.getElementById('sess-sel-all');
    if (!head) return;
    const all = document.querySelectorAll('.sess-sel');
    if (all.length === 0) {
      head.checked = false;
      head.indeterminate = false;
      return;
    }
    head.checked = ids.length === all.length;
    head.indeterminate = ids.length > 0 && ids.length < all.length;
  }

  function _sessSelectAllHandler() {
    const head = document.getElementById('sess-sel-all');
    if (!head) return;
    head.addEventListener('change', function () {
      const on = !!head.checked;
      document.querySelectorAll('.sess-sel').forEach(function (cb) {
        cb.checked = on;
      });
      head.indeterminate = false;
      _updateSessionsToolbar();
    });
  }

  async function sessionsDeleteSelected() {
    const ids = _sessSelectedIds();
    if (!ids.length) { toast(t('sessNone'), 'warn'); return; }
    if (!confirm(t('sessDeleteSel', { n: ids.length }))) return;
    const r = await api.call('delete_sessions', { ids: ids });
    if (!r || r.error) {
      toast(t('errGeneric', { msg: (r && r.error) || 'fail' }), 'error');
      return;
    }
    // The ACTIVE chat session was deleted → drop the in-editor state
    // so the next autoSave cannot resurrect a deleted id.
    if (typeof chatSession !== 'undefined' && chatSession &&
        chatSession.id && ids.indexOf(chatSession.id) !== -1) {
      chatSession.id = null;
      chatSession.messages = [];
      renderChat();
      syncStateMirror();
    }
    toast(t('sessDeleted', { ok: r.deleted || 0,
      skip: (r.not_found || 0) + _sessErrorCount(r) }), 'success', 2500);
    refreshSessions();
  }

  function _sessErrorCount(r) {
    return ((r && r.results) || []).filter(function (x) {
      return x && x.status === 'error';
    }).length;
  }

  async function sessionsExportSelected() {
    const ids = _sessSelectedIds();
    if (!ids.length) { toast(t('sessNone'), 'warn'); return; }
    const r = await api.call('export_sessions', { ids: ids });
    if (!r || r.error || !r.ok || !r.path) {
      toast(t('errGeneric', { msg: (r && r.error) || 'export failed' }), 'error');
      return;
    }
    await downloadSandboxFile(r.path);
    toast(t('sessExported', { n: r.count || 0 }), 'success', 2500);
  }

  // ─── v0.9.103.5 — per-row session title rename ──────────────────
  async function sessionsRename(id, currentTitle) {
    if (!id) return;
    const input = prompt(t('sessRenamePrompt'), currentTitle || '');
    if (input === null) return;  // cancelled
    const title = (input || '').trim();
    if (!title) { toast(t('sessRenameEmpty'), 'warn'); return; }
    const r = await api.call('update_session_title',
      { id: id, title: title });
    if (!r || r.error) {
      toast(t('errGeneric', { msg: (r && r.error) || 'fail' }), 'error');
      return;
    }
    // Renaming the ACTIVE chat session keeps the in-editor title in
    // lockstep (the title rides along on the next save).
    if (typeof chatSession !== 'undefined' && chatSession &&
        chatSession.id === id) {
      chatSession.title = r.title || title;
      syncStateMirror();
    }
    toast(t('sessRenamed', { title: title }), 'success', 2500);
    refreshSessionPicker();
    refreshSessions();
  }

  window.renderSessionList = refreshSessions;

  // v0.9.133.8 — rename the CURRENT session from the chat tab:
  // no session file yet → saveSession() establishes it first (same
  // 💾 path); then reuse sessionsRename for the prompt + the
  // active-title lockstep + the picker/Sessions refreshes.
  async function renameCurrentSession() {
    if (!chatSession.id) {
      await saveSession();
    }
    if (!chatSession.id) {
      toast(t('errGeneric', { msg: 'no session' }), 'warning');
      return;
    }
    await sessionsRename(chatSession.id, chatSession.title || '');
  }


  // ===== Boot =====
  // ===== v0.9.16.0+ web-mode client upload =====
  //
  // Web mode (shim sets window.__WEB_MODE__): the 📎 / 📷 buttons open
  // hidden <input type=file> pickers; the selected Files are POSTed as
  // multipart/form-data to /upload, which saves them into the
  // filesystem write sandbox under uploads/ and returns the absolute
  // paths. The paths are inserted into the chat input so the user can
  // reference them in the next prompt (filesystem tools / ingest).
  // pywebview GUI mode: same-machine picking already exists via
  // create_file_dialog + the Ingest tab, so the buttons are hidden.
  function isWebMode() {
    return window.__WEB_MODE__ === true;
  }

  // v0.9.125.5 — the "[已上傳] <path>" insert mark is a PROTOCOL
  // token, NOT a locale string: it must be identical on every device
  // (a remote browser running the zh-CN/en UI used to insert
  // "[已上传]"/"[uploaded]" — the user searching for 已上傳 saw
  // "nothing"). Keep the i18n uploadMark key for rendering contracts;
  // the insertion chain never reads it.
  const UPLOAD_MARK = '[已上傳]';

  // v0.9.127.7 — batched uploads: at most 5 files per POST.
  const UPLOAD_BATCH_SIZE = 5;

  // v0.9.127.8 — the persistent upload-progress panel (fixed card,
  // REAL byte progress from XHR upload.onprogress, cancel button).
  // Root cause of the「3 分鐘無反應」experience: the ⏳ toast lives
  // 2.5 s while a batch POST streams for MINUTES on a slow link,
  // and the server logs nothing until the body read completes.
  let _uppLast = 0;
  let _uppTimer = null;
  // v0.9.127.9 — WHOLE-SELECTION progress: the bar tracks ALL of the
  // picked files (all bytes), not the current 5-file batch (a big
  // pick used to show the bar reset to 0 % at every batch start).
  let _upAllTotal = 0;
  let _upAllDoneBytes = 0;
  let _upAllFiles = 0;
  let _upAllFilesDone = 0;
  // v0.9.127.10 — PARALLEL batches: every running POST gets a slot
  // carrying its own byte progress; the bar sums all in-flight
  // slots on top of the already-finished bytes.
  let _upSlots = [];
  let _upBatchDone = 0;
  let _upBatchTotal = 0;
  // The worker pool runs at most this many batch POSTs at once (a
  // single connection rarely saturates a slow cross-host link).
  const UPLOAD_CONCURRENCY = 3;
  // Size-aware batches: five huge PNGs on a slow link = ONE POST
  // stuck for minutes; cap the batch at 12 MB as well (≤5 files of
  // typical sizes stay byte-identical).
  const UPLOAD_BATCH_BYTES = 12 * 1024 * 1024;

  function _fmtMB(n) {
    if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
    return Math.round(n / 1024) + ' KB';
  }

  function _showProgressPanel(title) {
    let p = document.getElementById('upload-progress-panel');
    if (p) {
      p.querySelector('.upp-title').textContent = title || '⬆️';
      return p;
    }
    p = document.createElement('div');
    p.id = 'upload-progress-panel';
    const t1 = document.createElement('div');
    t1.className = 'upp-title';
    t1.textContent = title || '⬆️';
    const track = document.createElement('div');
    track.className = 'upp-track';
    const fill = document.createElement('div');
    fill.className = 'upp-fill';
    track.appendChild(fill);
    const meta = document.createElement('div');
    meta.className = 'upp-meta';
    const cancel = document.createElement('button');
    cancel.className = 'upp-cancel';
    cancel.type = 'button';
    cancel.textContent = '✕ ' + (t('uploadCancel') || '取消上傳');
    cancel.addEventListener('click', function () {
      // v0.9.127.10 — cancel EVERY in-flight slot (parallel posts).
      for (let s = 0; s < _upSlots.length; s++) {
        const sl = _upSlots[s];
        if (sl && typeof sl.abort === 'function') {
          try { sl.abort(); } catch (_) {}
        }
      }
    });
    p.appendChild(t1);
    p.appendChild(track);
    p.appendChild(meta);
    p.appendChild(cancel);
    document.body.appendChild(p);
    return p;
  }

  function _uppRepaint(title) {
    const p = document.getElementById('upload-progress-panel');
    if (!p) return;
    if (title) p.querySelector('.upp-title').textContent = title;
    // v0.9.127.9 — the bar = the WHOLE selection (all bytes of all
    // files, monotonic across batches); batch/files detail goes to
    // the meta line so the bar never jumps back to 0 %.
    // v0.9.127.10 — the bar additionally sums the byte progress of
    // EVERY in-flight parallel slot.
    let inflight = 0;
    for (let i = 0; i < _upSlots.length; i++) {
      inflight += (_upSlots[i] && _upSlots[i].loaded) || 0;
    }
    const done = _upAllDoneBytes + inflight;
    const pct = _upAllTotal > 0
      ? Math.round(done * 100 / _upAllTotal) : 0;
    p.querySelector('.upp-fill').style.width = pct + '%';
    const stalledSec = Math.floor((Date.now() - _uppLast) / 1000);
    const state = stalledSec >= 3
      ? (t('uploadStalled', { s: stalledSec })
         || ('⏸ stalled ' + stalledSec + 's'))
      : (t('uploadFlowing') || '⬆ streaming');
    p.querySelector('.upp-meta').textContent =
      t('uploadBatch', { i: _upBatchDone, n: _upBatchTotal,
        d: _upAllFilesDone, t: _upAllFiles })
      + ' · ' + pct + '% · ' + _fmtMB(done) + ' / '
      + _fmtMB(_upAllTotal) + ' · ' + state;
  }

  function _closeProgressPanel() {
    if (_uppTimer) { clearInterval(_uppTimer); _uppTimer = null; }
    const p = document.getElementById('upload-progress-panel');
    if (p) p.remove();
    _upSlots = [];
  }

  // One batch = one POST with its own deadline + its own slot
  // (v0.9.127.10 parallel). Returns
  // { saved, skipped, error } — never throws into the caller.
  function _uploadOneBatch(slice, total, start, slot) {
    return new Promise(function (resolve) {
      const busyNames = slice.map(function (f) {
        return f.name || 'file';
      }).join('、').slice(0, 60);
      const range = (total > slice.length)
        ? ('[' + (start + 1) + '-' + (start + slice.length) + '/'
           + total + '] ')
        : '';
      // v0.9.125.3 — IMMEDIATE feedback (an uploading toast right at
      // select time, never a silent hang while the POST streams).
      // v0.9.127.10 — parallel: only the FIRST wave toasts (the
      // later waves are already visible in the persistent panel).
      if (slot.first) {
        toast('⏳ ' + t('uploading').replace('{f}', range + busyNames),
              'success', 2500);
        slot.first = false;
      }
      // v0.9.127.8 — REAL byte progress + persistent panel; deadline
      // 60 s base + 60 s per file of THIS slice, 10-minute ceiling
      // (the flat 30 s of v0.9.125.3 died mid-stream on slow links).
      slot.loaded = 0;
      slot.total = slice.reduce(function (s, f) {
        return s + (f && f.size || 0);
      }, 0);
      _upSlots.push(slot);
      _uppLast = Date.now();
      _showProgressPanel('⬆️ ' + range + busyNames);
      _uppRepaint();
      if (!_uppTimer) {
        _uppTimer = setInterval(function () { _uppRepaint(); }, 1000);
      }

      const fd = new FormData();
      slice.forEach(function (f) { fd.append('files', f, f.name); });
      const tmo = Math.min(600000, 60000 + slice.length * 60000);
      const finish = function (out) {
        const si = _upSlots.indexOf(slot);
        if (si >= 0) _upSlots.splice(si, 1);
        if (_upSlots.length === 0) {
          _closeProgressPanel();
        } else {
          _uppRepaint();
        }
        resolve(out);
      };
      // XHR is the ONLY transport carrying upload progress; where it
      // is absent (jsdom test drivers delete it) fall back to the
      // fetch path — same deadline, same return shape.
      if (typeof XMLHttpRequest === 'function') {
        const xhr = new XMLHttpRequest();
        slot.xhr = xhr;
        // v0.9.127.10 — the cancel button drives EVERY slot's abort.
        slot.abort = function () {
          try { xhr.abort(); } catch (_) {}
        };
        xhr.open('POST', '/upload', true);
        xhr.timeout = tmo;
        xhr.upload.onprogress = function (ev) {
          if (ev && ev.lengthComputable) {
            slot.loaded = ev.loaded;
            slot.total = ev.total;
          }
          _uppLast = Date.now();
          _uppRepaint();
        };
        xhr.onload = function () {
          let data = {};
          try {
            data = JSON.parse(xhr.responseText || '{}');
          } catch (_) {}
          if (xhr.status >= 200 && xhr.status < 300
              && data.ok !== false) {
            finish({ saved: data.files || [],
                     skipped: data.skipped || [], error: null });
          } else {
            finish({ saved: [], skipped: [],
              error: ((data && data.error)
                || ('HTTP ' + (xhr.status || 0))) });
          }
        };
        xhr.ontimeout = function () {
          finish({ saved: [], skipped: [],
            error: (t('uploadTimeout') || 'upload timed out') });
        };
        xhr.onerror = function () {
          finish({ saved: [], skipped: [],
            error: (t('uploadNetErr')
              || 'network error') });
        };
        xhr.onabort = function () {
          finish({ saved: [], skipped: [],
            error: (t('uploadCancel') || 'upload cancelled') });
        };
        xhr.send(fd);
        return;
      }
      const ctrl = (typeof AbortController === 'function')
        ? new AbortController() : null;
      // v0.9.127.10 — slot abort hooks the fetch fallback too.
      if (ctrl) {
        slot.abort = function () {
          try { ctrl.abort(); } catch (_) {}
        };
      }
      const timer = ctrl
        ? setTimeout(function () { ctrl.abort(); }, tmo) : null;
      fetch('/upload', {
        method: 'POST', body: fd,
        signal: ctrl ? ctrl.signal : undefined,
      }).then(function (resp) {
        return resp.json().catch(function () { return {}; })
          .then(function (data) {
            if (!resp.ok || data.ok === false) {
              throw new Error((data && data.error)
                || ('HTTP ' + resp.status));
            }
            return { saved: data.files || [],
                     skipped: (data.skipped || []), error: null };
          }).finally(function () {
            if (timer) clearTimeout(timer);
          });
      }).catch(function (e) {
        if (timer) clearTimeout(timer);
        const isAbort = !!e && (e.name === 'AbortError'
          || /abort/i.test(String(e && e.message)));
        return { saved: [], skipped: [], error: isAbort
          ? (t('uploadTimeout') || 'upload timed out')
          : ((e && e.message) || String(e)) };
      }).then(finish, function (e) {
        finish({ saved: [], skipped: [],
          error: (e && e.message) || String(e) });
      });
    });
  }

  async function uploadFiles(fileList) {
    const files = Array.from(fileList || []);
    if (!files.length) return;
    if (!isWebMode()) {
      toast(t('uploadFail', { msg: 'GUI mode — use Pick file… / Ingest tab' }), 'warn', 6000);
      return;
    }
    if (navigator.onLine === false) {
      toast('📡 ' + t('uploadFail', { msg: 'offline — reconnect first' }),
            'warn', 6000);
      return;
    }
    // v0.9.127.7 — BATCHED UPLOADS: one giant POST of 十幾個 files
    // dies on slow links — live 2026-10-16 14:49: a 62 MB request
    // truncated at 4 MB (the flat 30 s abort closed the connection
    // half-way and the server silently saved only the parts that had
    // fully arrived, reporting "saved 3, skipped 0"). The selection
    // is now split into groups of 5 files, each its own POST with
    // its own scaled deadline; completed groups keep their [已上傳]
    // markers, the last batch reports the cumulative green toast,
    // and a failure reports exactly where the sequence stopped.
    // v0.9.127.9 — whole-selection progress state (see the panel):
    // the bar tracks ALL bytes / files of THIS pick, monotonic.
    _upAllTotal = files.reduce(function (s, f) {
      return s + (f && f.size || 0);
    }, 0);
    _upAllDoneBytes = 0;
    _upAllFiles = files.length;
    _upAllFilesDone = 0;
    let savedTotal = [];
    let skippedTotal = 0;
    let failureMsg = null;
    // v0.9.127.10 — PARALLEL BATCH UPLOADS: build the size-trimmed
    // batch list ONCE, then consume it with a worker pool running up
    // to UPLOAD_CONCURRENCY batch POSTs at the same time (a single
    // connection rarely saturates a slow cross-host link; parallel
    // posts fill the pipe instead). Marker/pending buffering still
    // rides EVERY completed batch; a failure stops NEW batches while
    // in-flight ones are allowed to finish (no sent bytes wasted).
    const batches = [];
    let i = 0;
    while (i < files.length) {
      let slice = files.slice(i, i + UPLOAD_BATCH_SIZE);
      // v0.9.127.9 — size-aware trim: cap the batch at 12 MB so a
      // pick of five huge files never becomes one stuck mega-POST
      // (≤5 files of typical sizes stay byte-identical).
      let bBytes = 0;
      let take = 0;
      for (; take < slice.length; take++) {
        const sz = (slice[take] && slice[take].size) || 0;
        if (take > 0 && bBytes + sz > UPLOAD_BATCH_BYTES) break;
        bBytes += sz;
      }
      if (take < slice.length) slice = slice.slice(0, take);
      batches.push({ slice: slice, bytes: bBytes, start: i });
      i += slice.length;
    }
    _upBatchTotal = batches.length;
    _upBatchDone = 0;
    let nextBatch = 0;
    let stopFlag = false;
    const runWorker = async function () {
      while (!stopFlag) {
        const bi = nextBatch;
        nextBatch += 1;
        if (bi >= batches.length) return;
        const b = batches[bi];
        const slot = { loaded: 0, total: b.bytes, xhr: null,
                       abort: null,
                       first: bi < UPLOAD_CONCURRENCY };
        const r = await _uploadOneBatch(
          b.slice, files.length, b.start, slot);
        if (r.error) {
          stopFlag = true;
          if (!failureMsg) failureMsg = r.error;
          return;
        }
        _upAllDoneBytes += b.bytes;
        _upAllFilesDone += r.saved.length;
        _upBatchDone += 1;
        savedTotal = savedTotal.concat(r.saved);
        skippedTotal += r.skipped.length;
        // v0.9.127.2 — PENDING-UPLOAD BUFFER: the successful upload
        // must ride the NEXT message even if the user starts
        // typing/sending BEFORE this fetch resolves (the reported
        // race: ✅ saved but the [已上傳] marker never made it into
        // the sent message = the AI "didn't see the image").
        try {
          window.__PENDING_UPLOAD_PATHS__ =
            (window.__PENDING_UPLOAD_PATHS__ || [])
              .concat(r.saved.map(function (f) {
                return f.path; }));
        } catch (_) {}
        const input = document.getElementById('chat-input');
        if (input && r.saved.length) {
          const lines = r.saved.map(function (f) {
            return UPLOAD_MARK + ' ' + f.path;
          }).join('\n');
          input.value = input.value
            ? (input.value.replace(/\s*$/, '\n') + lines + '\n')
            : (lines + '\n');
          input.focus();
        } else if (r.saved.length) {
          // v0.9.127.6 — never silent: a missing input still reports.
          toast(t('uploadFail', { msg: 'chat-input not found' }),
                'warn', 4000);
        }
      }
    };
    const workers = [];
    for (let bwi = 0; bwi < Math.min(UPLOAD_CONCURRENCY,
      batches.length); bwi++) {
      workers.push(runWorker());
    }
    await Promise.all(workers);
    // the ONE summary toast after the whole pool settles
    if (failureMsg) {
      // v0.9.127.7 — an abort reads as "connection too slow", and the
      // user always sees how far the pool got.
      toast(t('uploadFail', {
        msg: failureMsg + ' — ' + savedTotal.length
          + ' saved before the failure',
      }), 'error', 6000);
    } else {
      toast(
        t('uploadOk', { ok: savedTotal.length })
        + (skippedTotal ? t('uploadSkip', { skip: skippedTotal }) : ''),
        savedTotal.length ? 'success' : 'warn', 6000,
      );
    }
  }

  function bindUpload() {
    // v0.9.125.3 — idempotent (the group now binds early in
    // bindEvents; this guard makes a second call harmless).
    if (bindUpload._done) return;
    bindUpload._done = true;
    const ids = ['chat-upload-btn', 'chat-media-btn',
                 'chat-upload-input', 'chat-media-input'];
    if (!isWebMode()) {
      // pywebview GUI: hide the whole client-upload UI (inline
      // style also beats the desktop CSS rule for #chat-media-btn).
      ids.forEach(function (id) {
        const el = document.getElementById(id);
        if (el) el.style.display = 'none';
      });
      return;
    }
    const fileBtn = document.getElementById('chat-upload-btn');
    const fileIn = document.getElementById('chat-upload-input');
    const mediaBtn = document.getElementById('chat-media-btn');
    const mediaIn = document.getElementById('chat-media-input');
    if (fileBtn && fileIn) {
      fileBtn.addEventListener('click', function () { fileIn.click(); });
      fileIn.addEventListener('change', function () {
        uploadFiles(fileIn.files);
        fileIn.value = '';  // allow re-picking the same file
      });
    }
    if (mediaBtn && mediaIn) {
      mediaBtn.addEventListener('click', function () { mediaIn.click(); });
      mediaIn.addEventListener('change', function () {
        uploadFiles(mediaIn.files);
        mediaIn.value = '';
      });
    }
  }

  // ===== v0.9.127.5 — share plumbing =====
  //
  // OUT (per-bubble 📤 button): navigator.share → OS share sheet;
  // clipboard copy fallback where the API is absent.
  // IN (Android share_target): the installed PWA POSTs to
  // /upload?share=1; the server replies 303 → /?shared=…, and the
  // landing handler below inserts the [已上傳] markers + rewrites
  // the URL so a reload can never replay the same share.
  // iOS has NO share_target in Safari — the Shortcuts app hits
  // /upload?share=json or /share-text instead (see
  // docs/IOS_SHARE_SHORTCUT.md).
  function _shareChatText(text) {
    if (navigator.share) {
      navigator.share({
        title: t('appTitle') || 'Divine Sense Agent',
        text: text,
      }).catch(function (e) {
        if (e && e.name === 'AbortError') return;
        toast(t('shareFail',
          { msg: (e && e.message) || String(e) }), 'error', 4000);
      });
      return;
    }
    const copy = (navigator.clipboard && navigator.clipboard.writeText)
      ? navigator.clipboard.writeText(text)
      : Promise.reject(new Error('clipboard unavailable'));
    copy.then(function () {
      toast(t('shareCopied'), 'success', 3000);
    }).catch(function (e) {
      toast(t('shareFail',
        { msg: (e && e.message) || String(e) }), 'error', 4000);
    });
  }

  function handleSharedLanding() {
    if (handleSharedLanding._done) return;
    handleSharedLanding._done = true;
    if (!isWebMode()) return;  // pywebview GUI never gets a landing
    let q = null;
    try {
      q = new URLSearchParams(window.location.search);
    } catch (_) {
      return;
    }
    if (q.get('shared_err')) {
      toast('⚠️ ' + q.get('shared_err'), 'error', 6000);
    }
    if (q.get('skipped')) {
      toast(t('uploadSkip', { skip: q.get('skipped') }),
            'warn', 6000);
    }
    const shared = q.get('shared') || '';
    const paths = shared.split('\n').map(function (s) {
      return s.trim();
    }).filter(Boolean);
    if (paths.length) {
      const input = document.getElementById('chat-input');
      if (input) {
        const lines = paths.map(function (p) {
          return UPLOAD_MARK + ' ' + p;
        }).join('\n');
        input.value = input.value
          ? (input.value.replace(/\s*$/, '\n') + lines + '\n')
          : (lines + '\n');
        input.focus();
      }
      toast(t('sharedIn', { n: paths.length }), 'success', 6000);
    }
    // strip the query so a reload / bfcache restore can never replay
    try {
      if (window.history && window.history.replaceState) {
        window.history.replaceState(
          null, '',
          window.location.pathname + window.location.hash);
      }
    } catch (_) {}
  }

  // ===== v0.9.29.0+ clipboard image paste =====
  //
  // Paste an image (screenshot copy, "copy image" from browsers, …)
  // straight into the chat input. Web mode reuses the existing
  // /upload multipart flow (server-side timestamp naming); pywebview
  // GUI mode converts the clipboard blob to base64 and calls the new
  // Api.save_pasted_image bridge, which writes into the SAME
  // <write sandbox>/uploads/ dir. The saved path is inserted as
  // [已上傳] <path> — identical to the 📎 flow.
  function _pasteExt(file) {
    const sub = (file.type || '').split('/')[1] || 'png';
    return sub === 'jpeg' ? 'jpg' : (sub || 'png').slice(0, 5);
  }

  function _pasteStamp(idx) {
    const d = new Date();
    const p = function (n) { return String(n).padStart(2, '0'); };
    const stamp = ('' + d.getFullYear() + p(d.getMonth() + 1)
      + p(d.getDate()) + '-' + p(d.getHours()) + p(d.getMinutes())
      + p(d.getSeconds()) + '-' + String(d.getMilliseconds()).padStart(3, '0'));
    return 'paste-' + stamp + '-' + (idx || 0);
  }

  function _savePastedImageViaBridge(b64, filename) {
    const api = (window.pywebview && window.pywebview.api) || null;
    if (!api || typeof api.save_pasted_image !== 'function') {
      return Promise.reject(new Error('bridge unavailable'));
    }
    return Promise.resolve(api.save_pasted_image(b64, filename))
      .then(function (r) {
        if (r && r.ok && r.path) return r.path;
        throw new Error((r && r.error) || 'save_pasted_image failed');
      });
  }

  function _insertUploadPath(path) {
    const input = document.getElementById('chat-input');
    if (!input) return;
    const line = UPLOAD_MARK + ' ' + path;
    input.value = input.value
      ? (input.value.replace(/\s*$/, '\n') + line + '\n')
      : (line + '\n');
    input.focus();
  }

  async function handlePastedImage(file, idx) {
    if (isWebMode()) {
      await uploadFiles([file]);  // toast + [已上傳] insertion inside
      return;
    }
    // pywebview GUI mode: base64 → Api.save_pasted_image bridge
    const b64 = await new Promise(function (resolve) {
      try {
        const fr = new FileReader();
        fr.onload = function () {
          const s = String(fr.result || '');
          const i = s.indexOf('base64,');
          resolve(i >= 0 ? s.slice(i + 7) : '');
        };
        fr.onerror = function () { resolve(''); };
        fr.readAsDataURL(file);
      } catch (_) { resolve(''); }
    });
    if (!b64) throw new Error('clipboard image read failed');
    const path = await _savePastedImageViaBridge(
      b64, _pasteStamp() + '-' + idx + '.' + _pasteExt(file),
    );
    toast(t('uploadOk', { ok: 1 }), 'success', 6000);
    _insertUploadPath(path);
  }

  function onChatPaste(e) {
    const items = (e.clipboardData && e.clipboardData.items) || [];
    const imgs = [];
    const files = [];
    for (let i = 0; i < items.length; i++) {
      const it = items[i];
      if (it.kind !== 'file') continue;
      if (it.type && it.type.indexOf('image/') === 0) {
        const f = it.getAsFile();
        if (f) imgs.push(f);
      } else if (typeof it.getAsFile === 'function') {
        // v0.9.120.1 — ANY non-image clipboard file (a copied Excel /
        // PDF / zip from Explorer, a file drag in some apps) uploads
        // the same way instead of doing nothing.
        const f = it.getAsFile();
        if (f && f.name) files.push(f);
      }
    }
    if (files.length) {
      e.preventDefault();
      uploadFiles(files);
      return;
    }
    if (!imgs.length) return;  // plain-text paste — untouched
    e.preventDefault();        // drop the empty image pseudo-text
    imgs.forEach(function (f, idx) {
      handlePastedImage(f, idx).catch(function (err) {
        toast(t('uploadFail',
          { msg: (err && err.message) || String(err) }), 'error', 6000);
      });
    });
  }

  function bindPaste() {
    if (bindPaste._done) return;
    bindPaste._done = true;
    const input = document.getElementById('chat-input');
    if (input) input.addEventListener('paste', onChatPaste);
  }

  // ===== v0.9.120.1 — drag-and-drop upload (web mode) =====
  //
  // Drop ANY file (Excel, PDF, zip, …) anywhere on the page → the
  // same POST /upload flow as the 📎 button. This closes the "Excel
  // 無法上傳" gap: the upload endpoint always accepted them, but the
  // only entries were the toolbar buttons and image-only paste.
  function bindDrop() {
    if (bindDrop._done) return;
    bindDrop._done = true;
    if (!isWebMode()) return;
    document.addEventListener('dragover', function (e) {
      e.preventDefault();
      document.body.classList.add('drag-over');
    });
    document.addEventListener('dragleave', function (e) {
      if (!e.relatedTarget) {
        document.body.classList.remove('drag-over');
      }
    });
    document.addEventListener('drop', function (e) {
      document.body.classList.remove('drag-over');
      const dropped = e.dataTransfer && e.dataTransfer.files;
      if (dropped && dropped.length) {
        e.preventDefault();
        uploadFiles(dropped);
      }
    });
  }

  // ─── v0.9.68.0+ — chat bidirectional voice (web + GUI) ───────────
  // voice-IN: the 🎤 toolbar button toggles a MediaRecorder mic
  // capture (webm/opus); on stop the blob is base64'd and posted
  // through api.voice_stt → the transcript is inserted into the chat
  // input (dictation model — the user reviews before sending).
  // voice-OUT: every assistant bubble gains a 🔊 button that calls
  // api.tts_speak and plays the returned data URL. Both directions
  // reuse the SAME SpeechClient the Telegram voice uses
  // (src/agent/speech.py); only ONE audio element plays at a time.
  const _VOICE_REC_MAX_MS = 60000;   // auto-stop cap (1 min)
  const _VOICE_MIME = 'audio/webm';  // Chromium/WebView2 default
  let _recState = null;   // {recorder, chunks, stream, timer}
  let _voiceAudio = null; // the single playing <audio>
  let _voiceBtn = null;   // the 🔊 button that owns _voiceAudio

  function _voiceSupported() {
    return !!(navigator.mediaDevices
      && typeof navigator.mediaDevices.getUserMedia === 'function'
      && typeof window.MediaRecorder === 'function');
  }

  function _blobToBase64(blob) {
    return new Promise(function (resolve, reject) {
      try {
        const fr = new FileReader();
        fr.onload = function () {
          const s = String(fr.result || '');
          const i = s.indexOf('base64,');
          resolve(i >= 0 ? s.slice(i + 7) : '');
        };
        fr.onerror = function () { reject(new Error('blob read failed')); };
        fr.readAsDataURL(blob);
      } catch (e) { reject(e); }
    });
  }

  function _micBtn() {
    return document.getElementById('chat-mic-btn');
  }

  // v0.9.127.13 — humanise getUserMedia failures: the raw UA string
  // (「The request is not allowed by the user agent or the platform…」)
  // tells the user nothing; the DENIED-permission case gets a
  // TEACHING message (where the permission lives and how to fix it).
  function _micPermissionMessage(e) {
    const name = (e && e.name) || '';
    if (name === 'NotAllowedError' || name === 'SecurityError'
        || name === 'PermissionDeniedError') {
      // v0.9.127.14 — iOS denies at the SYSTEM layer (Settings →
      // Privacy → Microphone), and a previously-denied web app needs
      // its website data cleared; teach the platform-specific path.
      if (_isIOS()) {
        return t('voicePermissionIOS')
          || '🎤 iPhone 麥克風被拒 — iOS 設定 → 隱私權與安全性 → 麥克風 → 允許 Safari／本 App';
      }
      return t('voicePermission')
        || '🎤 麥克風權限被拒 — 請點網址列左側 🔒 圖示，將麥克風設為「允許」';
    }
    return t('voiceMicErr', { msg: name
      || (e && e.message) || String(e) });
  }

  // v0.9.127.13 — PERMISSION PRE-FLIGHT: ask for the mic UP FRONT,
  // during the deliberate 🎧 click (where a permission prompt is
  // EXPECTED) instead of mid-loop where a denial just kills the
  // round silently. One-shot getUserMedia, tracks released at once.
  async function _micPreflight() {
    if (!_voiceSupported()) {
      toast(t('voiceNoSupport'), 'warn', 6000);
      return false;
    }
    // v0.9.127.15 — probe the permission STATE first: iOS remembers
    // a denial at the SYSTEM level and NEVER re-prompts inside the
    // app, so every later attempt (even a proper gesture) throws
    // NotAllowedError. A denied state gets the full recovery
    // walkthrough instead of a generic retry suggestion.
    if (await _micPermissionState() === 'denied') {
      toast(t('voicePermissionIOSDenied')
        || t('voicePermissionIOS')
        || t('voicePermission'), 'error', 12000);
      return false;
    }
    try {
      const s = await navigator.mediaDevices.getUserMedia(
        { audio: true });
      s.getTracks().forEach(function (tr) { tr.stop(); });
      return true;
    } catch (e) {
      toast(_micPermissionMessage(e), 'error', 8000);
      return false;
    }
  }

  // v0.9.127.15 — the Permissions API state probe (granted | denied |
  // prompt; iOS Safari may not implement it for the microphone →
  // 'unknown' and the normal flow applies).
  async function _micPermissionState() {
    try {
      if (!navigator.permissions || !navigator.permissions.query) {
        return 'unknown';
      }
      const st = await navigator.permissions.query(
        { name: 'microphone' });
      return st.state || 'unknown';
    } catch (_) {
      return 'unknown';
    }
  }

  function _micSetStateBtn(recording) {
    const btn = _micBtn();
    if (!btn) return;
    btn.classList.toggle('btn-mic-rec', !!recording);
    btn.textContent = recording ? '⏹' : '🎤';
    btn.title = recording ? t('voiceRecording') : t('voiceMic');
  }

  async function toggleMicRecording() {
    if (_recState) {
      // stop + transcribe
      try { _recState.recorder.stop(); } catch (_) { /* already dead */ }
      return;
    }
    if (!_voiceSupported()) {
      toast(t('voiceNoSupport'), 'warn', 6000);
      return;
    }
    let stream;
    try {
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (e) {
      // v0.9.127.15 — a DENIED state (iOS remembers it system-wide and
      // never re-prompts) gets the full recovery walkthrough; other
      // denials keep the teaching message.
      let denied = false;
      try {
        denied = (await _micPermissionState()) === 'denied';
      } catch (_) {}
      toast((denied && _isIOS())
        ? (t('voicePermissionIOSDenied')
           || _micPermissionMessage(e))
        : _micPermissionMessage(e), 'error', 12000);
      return;
    }
    const chunks = [];
    let recorder;
    try {
      recorder = new MediaRecorder(stream);
    } catch (e) {
      stream.getTracks().forEach(function (tr) { tr.stop(); });
      toast(t('voiceNoSupport'), 'warn', 6000);
      return;
    }
    _recState = { recorder: recorder, chunks: chunks, stream: stream };
    recorder.ondataavailable = function (e) {
      if (e.data && e.data.size) chunks.push(e.data);
    };
    recorder.onstop = function () {
      stream.getTracks().forEach(function (tr) { tr.stop(); });
      _micSetStateBtn(false);
      const recorded = chunks.slice();
      _recState = null;
      const blob = new Blob(recorded,
        { type: recorder.mimeType || _VOICE_MIME });
      if (!blob || blob.size < 512) {
        toast(t('voiceFail', { msg: 'recording too short' }), 'warn', 4000);
        // v0.9.127.11 — a too-short take stops the hands-free loop.
        if (_voiceLoop) _voiceLoopStopAll();
        return;
      }
      _transcribeBlob(blob);
    };
    recorder.start();
    _micSetStateBtn(true);
    // v0.9.68.0 — safety cap: auto-stop after 60 s so a forgotten
    // click can't stream forever.
    setTimeout(function () {
      if (_recState && _recState.recorder === recorder
          && recorder.state === 'recording') {
        try { recorder.stop(); } catch (_) { /* died already */ }
      }
    }, _VOICE_REC_MAX_MS);
  }

  function chunksNow(ch) { return ch; }
  function chunksNowSafe(ch) { return ch; }

  async function _transcribeBlob(blob) {
    const btn = _micBtn();
    if (btn) {
      btn.textContent = '🎙';
      btn.title = t('voiceTranscribing');
    }
    try {
      const b64 = await _blobToBase64(blob);
      const ext = ((blob.type || '').split('/')[1] || 'webm').split(';')[0];
      const fname = 'voice.' + ext;
      const r = await api.call('voice_stt', b64, fname);
      if (!r || r.ok !== true || !r.text) {
        throw new Error((r && r.error) || 'no transcript');
      }
      const input = document.getElementById('chat-input');
      if (input) {
        const line = (r.text || '').trim();
        if (line) {
          input.value = input.value
            ? (input.value.replace(/\s*$/, '') + ' ' + line)
            : line;
          input.focus();
          // v0.9.127.11 — hands-free loop: the transcript just
          // landed; auto-SEND it (the reply will be spoken and the
          // mic reopens after playback ends).
          if (_voiceLoop && typeof _voiceTranscriptHook
              === 'function') {
            setTimeout(function () {
              try { _voiceTranscriptHook(line); } catch (_) {}
            }, 150);
          }
        }
      }
    } catch (e) {
      toast(t('voiceFail', { msg: (e && e.message) || String(e) }),
        'error', 6000);
      // v0.9.127.11 — a failed transcription STOPS the hands-free
      // loop (an endless toast storm is worse than losing it).
      if (_voiceLoop) _voiceLoopStopAll();
    } finally {
      _micSetStateBtn(false);
    }
  }

  // v0.9.127.11 — the browser's own speechSynthesis fallback (zero
  // cost; Windows ships zh voices; Firefox/Edge both support it).
  function _speakBrowserTTS(text, onEnd) {
    try {
      if (!window.speechSynthesis
          || !window.SpeechSynthesisUtterance) {
        throw new Error('no speechSynthesis');
      }
      const u = new window.SpeechSynthesisUtterance(
        _ttsStrip(text));
      const vs = (window.speechSynthesis.getVoices() || [])
        .filter(function (v) {
          return (v.lang || '').toLowerCase().indexOf('zh') === 0;
        });
      if (vs.length) u.voice = vs[0];
      _showVoiceStopFab();
      u.onend = function () {
        _hideVoiceStopFab();
        if (typeof onEnd === 'function') {
          try { onEnd(); } catch (_) {}
        }
      };
      u.onerror = u.onend;
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(u);
      return true;
    } catch (_) {
      _hideVoiceStopFab();
      if (typeof onEnd === 'function') {
        try { onEnd(); } catch (_) {}
      }
      return false;
    }
  }

  // ─── v0.9.127.17 — stop-speaking FAB + persistent unlocked
  // audio element ──────────────────────────────────────────────────
  let _voiceEl = null;
  let _voiceStopFabEl = null;

  function _ensureVoiceEl() {
    if (_voiceEl) return _voiceEl;
    _voiceEl = new Audio();
    try {
      _voiceEl.setAttribute('playsinline', '');
      _voiceEl.setAttribute('webkit-playsinline', '');
    } catch (_) {}
    return _voiceEl;
  }

  // iOS autoplay unlock: play (muted) ONCE inside a user gesture —
  // that element is then allowed to play programmatically forever,
  // so every reply can be spoken IMMEDIATELY as it lands.
  let _voiceElUnlocked = false;

  function _unlockVoiceEl() {
    try {
      const el = _ensureVoiceEl();
      el.muted = true;
      el.volume = 0;
      const p = el.play();
      if (p && p.then) {
        p.then(function () {
          _voiceElUnlocked = true;
          try { el.pause(); } catch (_) {}
          el.muted = false;
          el.volume = 1;
        }).catch(function () {
          el.muted = false;
          el.volume = 1;
        });
      } else {
        _voiceElUnlocked = true;  // legacy sync engines
      }
    } catch (_) {}
  }

  function _bindGlobalUnlock() {
    // v0.9.127.18 — unlock on the FIRST user gesture ANYWHERE: the
    // 🎉/🎧 toggles alone miss sessions where they were already on
    // (restored from localStorage after a reload, without a gesture —
    // the live iPhone still seeing the「播放回覆」overlay). Any touch
    // on the page completes the unlock.
    const once = function () {
      _unlockVoiceEl();
      const evs = ['pointerdown', 'touchstart', 'touchend', 'click'];
      for (let i = 0; i < evs.length; i++) {
        try {
          document.removeEventListener(evs[i], once);
        } catch (_) {}
      }
    };
    const evs = ['pointerdown', 'touchstart', 'touchend', 'click'];
    for (let i = 0; i < evs.length; i++) {
      try {
        document.addEventListener(evs[i], once, { passive: true });
      } catch (_) {}
    }
  }

  function _showVoiceStopFab() {
    if (_voiceStopFabEl) return;
    const d = document.createElement('div');
    d.id = 'voice-stop-fab';
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'vsf-btn';
    b.textContent = '⏹';
    b.title = t('voiceStop') || '停止朗讀';
    b.addEventListener('click', function () { _stopSpeech(); });
    d.appendChild(b);
    document.body.appendChild(d);
    _voiceStopFabEl = d;
  }

  function _hideVoiceStopFab() {
    if (_voiceStopFabEl) {
      try { _voiceStopFabEl.remove(); } catch (_) {}
      _voiceStopFabEl = null;
    }
  }

  // Manual stop: kill playback AND the fallback engine; the loop
  // treats a stop as "my turn to talk" (iOS → overlay, desktop →
  // mic reopens).
  function _stopSpeech() {
    if (_voiceAudio) {
      try { _voiceAudio.pause(); } catch (_) {}
      const prev = _voiceBtn;
      _voiceAudio = null;
      _voiceBtn = null;
      if (prev) prev.textContent = '🔊';
    }
    try {
      if (window.speechSynthesis) window.speechSynthesis.cancel();
    } catch (_) {}
    _hideVoiceStopFab();
    if (_voiceLoop) {
      if (_isIOS()) _voiceLoopResumeOverlay(null, null);
      else _voiceLoopRestartMic();
    }
  }

  // voice-OUT: speak one assistant bubble's text through TTS. Only
  // ONE audio plays at a time — clicking another bubble stops the
  // current one; clicking the SAME button toggles stop.
  async function speakChatText(btn, text, onEnd) {
    if (_voiceAudio) {
      try { _voiceAudio.pause(); } catch (_) { /* stopped */ }
      const prev = _voiceBtn;
      _voiceAudio = null;
      _voiceBtn = null;
      if (prev) prev.textContent = '🔊';
      if (prev === btn) return;  // toggle → stopped, do not restart
    }
    if (!text) return;
    btn.disabled = true;
    try {
      btn.textContent = '⏳';
      const r = await api.call('tts_speak', String(text || ''));
      if (!r || r.ok !== true || !r.data_base64) {
        // v0.9.127.11 — degrade to the BROWSER's own TTS engine
        // (zero cost, works offline) instead of a dead toast.
        toast(t('voiceFallback'), 'ok', 3000);
        if (!_speakBrowserTTS(text, onEnd)) {
          toast(t('voiceFail', { msg: (r && r.error) || 'no audio' }),
            'error', 6000);
        }
        return;
      }
      const url = 'data:' + (r.mime || 'audio/mpeg') + ';base64,'
        + r.data_base64;
      // v0.9.127.17 — ONE persistent element: iOS unlocks an element
      // that played inside a user gesture; a fresh Audio() per reply
      // would re-trip the autoplay policy. Reuse + swap src, and show
      // the stop FAB while it plays.
      const audio = _ensureVoiceEl();
      _voiceAudio = audio;
      _voiceBtn = btn;
      btn.textContent = '⏹';
      _showVoiceStopFab();
      audio.onended = function () {
        btn.textContent = '🔊';
        _hideVoiceStopFab();
        if (_voiceAudio === audio) { _voiceAudio = null; _voiceBtn = null; }
        if (typeof onEnd === 'function') {
          try { onEnd(); } catch (_) {}
        }
      };
      audio.onerror = function () {
        btn.textContent = '🔊';
        _hideVoiceStopFab();
        toast(t('voiceFail', { msg: 'playback failed' }), 'error', 4000);
        if (_voiceAudio === audio) { _voiceAudio = null; _voiceBtn = null; }
        if (typeof onEnd === 'function') {
          try { onEnd(); } catch (_) {}
        }
      };
      audio.src = url;
      await audio.play();
    } catch (e) {
      toast(t('voiceFail', { msg: (e && e.message) || String(e) }),
        'error', 6000);
    } finally {
      btn.disabled = false;
      if (_voiceBtn !== btn) btn.textContent = '🔊';
    }
  }

  // ─── v0.9.127.11 — hands-free voice loop + auto-TTS + browser
  // fallback ────────────────────────────────────────────────────────
  // 🎉 auto-TTS: every assistant reply is SPOKEN the moment it lands
  //    (toggle, persisted in localStorage).
  // 🎧 hands-free loop: after the reply finishes playing the mic
  //    reopens automatically; a VAD silence watchdog (2 s of quiet
  //    after speech) stops the recording, the transcript auto-SENDS,
  //    and the loop continues — ChatGPT-voice-mode style, hands off
  //    the keyboard. Any transcription failure stops the loop (an
  //    endless toast storm is worse than losing hands-free).
  // 🔤 fallback: when the backend TTS (OpenRouter kokoro) fails or
  //    is unconfigured, the BROWSER's own speechSynthesis engine
  //    speaks instead (zero cost; zh voices exist on Windows).
  const _VAD_SILENCE_MS = 2000;     // stop after this much quiet
  const _VAD_MIN_SPEECH_MS = 1200;  // never stop before this
  let _autoTTS = false;
  let _voiceLoop = false;
  let _vadTimer = null;
  let _vadCtx = null;
  let _vadStartTs = 0;
  let _voiceTranscriptHook = null;

  function _ttsStrip(text) {
    return String(text || '')
      .replace(/\[([^\]]*)\]\([^)]*\)/g, '$1')
      .replace(/[#*`_>~|]+/g, ' ')
      .replace(/\s{2,}/g, ' ')
      .trim();
  }

  function toggleAutoTTS() {
    _autoTTS = !_autoTTS;
    try {
      localStorage.setItem('chat-auto-tts', _autoTTS ? '1' : '0');
    } catch (_) {}
    // v0.9.127.17 — the toggle tap IS a gesture: unlock the voice
    // element NOW so every later reply can be spoken automatically
    // (iOS autoplay policy).
    if (_autoTTS) _unlockVoiceEl();
    _autoTTSPaint();
    toast(_autoTTS ? t('autoTtsOn') : t('autoTtsOff'),
          'success', 2500);
  }

  function toggleVoiceLoop() {
    _voiceLoop = !_voiceLoop;
    try {
      localStorage.setItem('chat-voice-loop', _voiceLoop ? '1' : '0');
    } catch (_) {}
    _voiceLoopPaint();
    if (_voiceLoop) {
      toast(t('voiceLoopOn'), 'success', 3500);
      // v0.9.127.17 — unlock inside THIS tap (gesture) so the replies
      // can be spoken automatically later (iOS autoplay policy).
      _unlockVoiceEl();
      // v0.9.127.13 — pre-flight the mic permission UP FRONT: the
      // prompt appears during this deliberate click (expected), and
      // a DENIAL rolls the toggle back instead of failing mid-loop.
      Promise.resolve(_micPreflight()).then(function (ok) {
        if (!_voiceLoop) return;  // user already toggled off
        if (ok) {
          _voiceLoopStartMic();
        } else {
          _voiceLoopStopAll();
        }
      });
    } else {
      toast(t('voiceLoopOff'), 'ok', 2500);
      _vadStop();
    }
  }

  function _autoTTSPaint() {
    const btn = document.getElementById('chat-autotts-btn');
    if (!btn) return;
    btn.classList.toggle('btn-loop-on', !!_autoTTS);
    btn.title = _autoTTS ? t('autoTtsOn') : t('autoTtsOff');
  }

  function _voiceLoopPaint() {
    const btn = document.getElementById('chat-loop-btn');
    if (!btn) return;
    btn.classList.toggle('btn-loop-on', !!_voiceLoop);
    btn.title = _voiceLoop ? t('voiceLoopOn') : t('voiceLoopOff');
  }

  // Speak the reply the moment it lands (auto-TTS or hands-free
  // loop); when the loop is on, restart the mic AFTER playback ends.
  let _iosAutoplayHintShown = false;

  function _speakReplyNow(t2) {
    const ghost = document.createElement('span');
    ghost.textContent = '🔊';
    try {
      Promise.resolve(speakChatText(ghost, t2, function () {
        if (_voiceLoop) _voiceLoopRestartMic();
      })).catch(function () {
        if (_voiceLoop) _voiceLoopRestartMic();
      });
    } catch (_) {
      if (_voiceLoop) _voiceLoopRestartMic();
    }
  }

  function _speakReplyForLoop(text) {
    const t2 = String(text || '');
    // v0.9.127.16/18 — iOS autoplay policy blocks a gesture-less
    // audio.play() UNLESS the element was unlocked by an earlier
    // gesture (127.17/18). Unlocked → speak immediately; locked →
    // the overlay tap does it (hands-free) or a one-time hint (bare
    // autoTTS).
    if (_isIOS()) {
      if (_voiceElUnlocked) {
        _speakReplyNow(t2);
        return;
      }
      if (_voiceLoop) {
        _voiceLoopResumeOverlay(t2, null);
        return;
      }
      if (t2 && !_iosAutoplayHintShown) {
        _iosAutoplayHintShown = true;
        toast(t('voiceIOSAutoplayHint'), 'ok', 9000);
      }
      return;
    }
    if (!t2) {
      if (_voiceLoop) _voiceLoopRestartMic();
      return;
    }
    _speakReplyNow(t2);
  }

  function _voiceLoopStartMic() {
    if (!_voiceLoop) return;
    if (_recState) return;  // already recording
    Promise.resolve(toggleMicRecording()).then(function () {
      if (_voiceLoop && _recState) {
        _vadStart();
      } else if (_voiceLoop) {
        // mic could not start (permission / insecure context) —
        // stop the loop instead of hammering the permission dialog.
        _voiceLoopStopAll();
      }
    });
  }

  function _voiceLoopRestartMic() {
    if (!_voiceLoop) return;
    // v0.9.127.14 — iOS WebKit only allows getUserMedia inside a
    // USER GESTURE; a timer-driven reopen throws NotAllowedError
    // (the live iPhone「not allowed…denied permission」report). On
    // iOS the loop shows a big resume button instead — the tap IS
    // the gesture, the mic opens in it, and the loop continues.
    if (_isIOS()) {
      _voiceLoopResumeOverlay();
      return;
    }
    setTimeout(function () { _voiceLoopStartMic(); }, 800);
  }

  // v0.9.127.14 — iOS / iPadOS detection (iPadOS 13+ reports as
  // MacIntel with touch points; Safari PWA sets navigator.standalone).
  function _isIOS() {
    try {
      const ua = navigator.userAgent || '';
      if (/ip(hone|od|ad)/i.test(ua)) return true;
      if (navigator.standalone === true) return true;
      return (navigator.platform === 'MacIntel'
        && navigator.maxTouchPoints > 1);
    } catch (_) {
      return false;
    }
  }

  function _voiceLoopResumeOverlay(text, onResume) {
    let ov = document.getElementById('voice-loop-resume');
    if (ov) return;  // already waiting
    ov = document.createElement('div');
    ov.id = 'voice-loop-resume';
    const box = document.createElement('div');
    box.className = 'vlr-box';
    // v0.9.127.16 — with a pending reply, the tap plays the spoken
    // audio INSIDE the gesture (iOS autoplay-legal) and the mic
    // reopens after playback; a bare tap opens the mic directly.
    if (text) {
      const pb = document.createElement('button');
      pb.type = 'button';
      pb.className = 'vlr-btn';
      pb.textContent = '🔊 ' + (t('voiceResumePlay')
        || '播放回覆');
      pb.addEventListener('click', function () {
        try { ov.remove(); } catch (_) {}
        const ghost = document.createElement('span');
        ghost.textContent = '🔊';
        try {
          Promise.resolve(speakChatText(ghost, text, function () {
            if (_voiceLoop) _voiceLoopRestartMic();
          })).catch(function () {
            if (_voiceLoop) _voiceLoopRestartMic();
          });
        } catch (_) {}
        if (typeof onResume === 'function') {
          try { onResume(); } catch (_) {}
        }
      });
      box.appendChild(pb);
    }
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'vlr-btn vlr-btn-mic';
    btn.textContent = '🎤 ' + (t('voiceResumeListen')
      || '開始說話');
    btn.addEventListener('click', function () {
      try { ov.remove(); } catch (_) {}
      _voiceLoopStartMic();
      if (typeof onResume === 'function') {
        try { onResume(); } catch (_) {}
      }
    });
    box.appendChild(btn);
    ov.appendChild(box);
    document.body.appendChild(ov);
  }

  function _voiceLoopStopAll() {
    _voiceLoop = false;
    try { localStorage.setItem('chat-voice-loop', '0'); } catch (_) {}
    _voiceLoopPaint();
    _vadStop();
  }

  // VAD: watch the recording stream's RMS; after the user HAS spoken
  // and then stays quiet for _VAD_SILENCE_MS, stop the recorder (the
  // normal stop path transcribes → the transcript hook auto-sends).
  function _vadStart() {
    _vadStop();
    if (!_voiceLoop || !_recState || !_recState.stream) return;
    try {
      const AC = (window.AudioContext || window.webkitAudioContext);
      if (!AC) return;  // no analyser → the 60 s cap still bounds it
      _vadCtx = new AC();
      const src = _vadCtx.createMediaStreamSource(_recState.stream);
      const an = _vadCtx.createAnalyser();
      an.fftSize = 512;
      src.connect(an);
      const buf = new Float32Array(an.fftSize);
      _vadStartTs = Date.now();
      let speechSeen = false;
      let lastVoiceTs = Date.now();
      _vadTimer = setInterval(function () {
        try { an.getFloatTimeDomainData(buf); } catch (_) { return; }
        let sum = 0;
        for (let i = 0; i < buf.length; i++) {
          sum += buf[i] * buf[i];
        }
        const rms = Math.sqrt(sum / buf.length);
        if (rms > 0.025) {
          speechSeen = true;
          lastVoiceTs = Date.now();
        }
        const elapsed = Date.now() - _vadStartTs;
        const silentFor = Date.now() - lastVoiceTs;
        if (elapsed > _VAD_MIN_SPEECH_MS && speechSeen
            && silentFor >= _VAD_SILENCE_MS) {
          _vadStop();
          if (_recState) {
            try { _recState.recorder.stop(); } catch (_) {}
          }
        }
      }, 200);
    } catch (_) { /* no VAD — the 60 s cap still applies */ }
  }

  function _vadStop() {
    if (_vadTimer) { clearInterval(_vadTimer); _vadTimer = null; }
    if (_vadCtx) {
      try { _vadCtx.close(); } catch (_) {}
      _vadCtx = null;
    }
  }

  // The hands-free loop auto-SENDS whatever the transcription lands
  // (sendChat is hoisted — the hook can be wired at boot).
  _voiceTranscriptHook = function () {
    if (_voiceLoop) {
      try { sendChat(); } catch (_) {}
    }
  };

  function bindVoiceToggles() {
    if (bindVoiceToggles._done) return;
    bindVoiceToggles._done = true;
    // v0.9.127.18 — the FIRST touch anywhere unlocks the voice
    // element (iOS autoplay): toggles restored from localStorage
    // never see a gesture of their own.
    _bindGlobalUnlock();
    try {
      _autoTTS = (localStorage.getItem('chat-auto-tts') === '1');
    } catch (_) { _autoTTS = false; }
    try {
      _voiceLoop = (localStorage.getItem('chat-voice-loop') === '1');
    } catch (_) { _voiceLoop = false; }
    const a = document.getElementById('chat-autotts-btn');
    if (a) a.addEventListener('click', function () {
      try { toggleAutoTTS(); } catch (_) {}
    });
    const l = document.getElementById('chat-loop-btn');
    if (l) l.addEventListener('click', function () {
      try { toggleVoiceLoop(); } catch (_) {}
    });
    _autoTTSPaint();
    _voiceLoopPaint();
  }

  // ─── v0.9.127.19 — the RUNNING DOG (LLM busy indicator) ──────────
  // While a model turn is computing, a little dog sprints back and
  // forth along the bottom of the window (game-style: it flips to
  // face the direction it runs). v0.9.128.0 — EIGHT AI-generated
  // v0.9.129.0 — floating emoji particles (pets = hearts, sleep = Zzz).
  // Pure DOM: absolutely-positioned glyphs rise + fade inside the
  // runner box and remove themselves. Never raises; a no-op when the
  // layer is already gone.
  function _dogParticles(host, emojis, n, ttl) {
    try {
      if (!host) return;
      for (let i = 0; i < n; i++) {
        setTimeout(function () {
          try {
            if (!document.getElementById('dog-run-layer')) return;
            const p = document.createElement('div');
            p.className = 'dog-particle';
            p.textContent =
              emojis[Math.floor(Math.random() * emojis.length)];
            p.style.left = (20 + Math.random() * 90) + 'px';
            p.style.bottom = (40 + Math.random() * 40) + 'px';
            p.style.animationDuration =
              (1 + Math.random() * 0.6).toFixed(2) + 's';
            host.appendChild(p);
            setTimeout(function () {
              try { p.remove(); } catch (_) {}
            }, ttl);
          } catch (_) {}
        }, i * 420);
      }
    } catch (_) {}
  }

  // ===== v0.9.131.0 — DOG BEHAVIOUR STATE MACHINE (18 behaviours) =====
  // Rebuilt around the behaviour model's 4 categories:
  //   MOVEMENT  run / walk / hop / spin   — interruptible JS-driven bursts
  //   EMOTION   bark / bite / lick /
  //             yawn / tilt               — interruptible
  //   REST      sleep / lie / roll / tail /
  //             stretch                   — deep sleep is LOCKED (wake chain)
  // IDLE (the sitting hub, frozen putdown pose) is the centre:
  //   tap the mover   -> hearts + the pet streak (3 = follow, 6 = bite)
  //   idle rotation   -> bone(tail-chase -> lick chain) / yawn / lick
  //                      / tilt / belly — six mood picks
  //   drag 14px+      -> GRAB -> carried (rides the cursor)
  //   slow release    -> PUTDOWN -> idle
  //   fast fling      -> THROW (arc 110px) -> land -> chase -> ball -> idle
  //   hit the edge    -> ROLL over and turn back (打滾轉頭)
  //   6 legs of run   -> DRINK -> walk to centre -> lie -> SLEEP there
  //   sit 7-10s       -> LIE (趴下, 💤);  sit 11-15s -> up and gallop again
  //   resting 2min    -> SLEEP (LOCKED — only the wake chain leaves)
  //   tap the sleeper -> stretch -> yawn -> sit (the wake chain)
  //   8 LLM rounds    -> DIZZY (a tap comforts: belly -> sit; alone: 15 s)
function _dogRunStart() {
    if (document.getElementById('dog-run-layer')) return;
    const layer = document.createElement('div');
    layer.id = 'dog-run-layer';
    layer.setAttribute('aria-hidden', 'true');
    const dog = document.createElement('div');
    dog.className = 'dog-runner';
    const skin = document.createElement('div');
    skin.className = 'dog-skin';
    dog.appendChild(skin);
    layer.appendChild(dog);
    document.body.appendChild(layer);

    let dogSpriteOK = false;
    const V = '?v=13200';

    const SKIN_CLASSES = ['dog-running', 'dog-walking', 'dog-hopping',
      'dog-spinning', 'dog-lifting', 'dog-putdown', 'dog-throwing',
      'dog-belling', 'dog-balling', 'dog-boning', 'dog-barking',
      'dog-biting', 'dog-licking', 'dog-yawning', 'dog-tilting',
      'dog-lying', 'dog-sleeping', 'dog-rolling', 'dog-tail-chasing',
      'dog-stretching', 'dog-carried',
      // v0.9.133.1 — AI 產 GIF/PNG 皮膚（頭暈轉圈／開心蹦跳）
      'dog-dizzy', 'dog-happy'];
    const S = { st: 'boot', pets: 0, streaks: 0 };
    layer._dogT = {};
    const clearT = function (k) {
      if (layer._dogT[k]) {
        clearTimeout(layer._dogT[k]);
        delete layer._dogT[k];
      }
    };
    const T = function (k, fn, ms) {
      clearT(k);
      layer._dogT[k] = setTimeout(fn, ms);
    };
    const clearAllT = function () {
      const t = layer._dogT || {};
      Object.keys(t).forEach(function (k) {
        clearTimeout(t[k]);
      });
      layer._dogT = {};
    };
    // deep-sleep gate: ms of CONTINUOUS REST before the locked sleep
    // (jsdom tests shrink it via layer._SLEEP_GATE_MS)
    const SLEEP_GATE = function () {
      return layer._SLEEP_GATE_MS || 120000;
    };
    const setSkin = function (cls) {
      for (let i = 0; i < SKIN_CLASSES.length; i++) {
        skin.classList.remove(SKIN_CLASSES[i]);
      }
      if (cls) skin.classList.add(cls);
    };
    const face = function (dir) {
      try {
        dog.style.transform = dir < 0 ? 'scaleX(-1)' : 'scaleX(1)';
      } catch (_) {}
    };
    const clampX = function (x) {
      const w = window.innerWidth || 1024;
      return Math.max(4, Math.min(x, w - 154));
    };
    const curLeft = function () {
      try {
        return parseFloat(getComputedStyle(dog).left) || 0;
      } catch (_) {
        return 0;
      }
    };

    // ---- movement: JS-driven trot/gallop bursts ----
    const MOVE = { run: ['dog-running', 90], walk: ['dog-walking', 45] };
    // towardCenter: true = forced rightward (the off-screen-left
    // entrance, runs until past the centre); 'center' = auto-dir that
    // heads for the middle slot; undefined = random direction.
    const moveBurst = function (kind, next, towardCenter) {
      if (!document.getElementById('dog-run-layer')) return;
      const cfg = MOVE[kind] || MOVE.run;
      S.st = kind;
      dog.classList.remove('dog-stopped');
      setSkin(cfg[0]);
      const w = window.innerWidth || 1024;
      const cx = Math.max(40, Math.min(w / 2 - 77, w - 154));
      let dir;
      if (towardCenter === true) dir = 1;
      else if (towardCenter === 'center') {
        dir = (curLeft() < cx) ? 1 : -1;
      } else {
        dir = Math.random() < 0.5 ? -1 : 1;
      }
      let x = curLeft();
      face(dir);
      const started = Date.now();
      const total = 1600 + Math.random() * 2200;
      let rolling = false;
      const step = function () {
        if (!document.getElementById('dog-run-layer')) return;
        const nx = x + dir * cfg[1];
        const clampedNx = clampX(nx);
        if (!rolling && Math.abs(clampedNx - nx) > 2) {
          // v0.9.132.8 — 狗快到視窗邊＝打滾轉頭：roll once and
          // head back the other way
          rolling = true;
          setSkin('dog-rolling');
          dir = -dir;
          try {
            x = clampedNx;
            dog.style.left = x + 'px';
          } catch (_) {}
          face(dir);
          T('rollbreak', function () {
            rolling = false;
            if (S.st !== kind) return;  // the burst was superseded
            setSkin(cfg[0]);
            T('move', step, 100);
          }, 900);
          return;
        }
        x = clampedNx;
        try { dog.style.left = x + 'px'; } catch (_) {}
        face(dir);
        const toC = towardCenter === true
          ? x < cx
          : (towardCenter === 'center'
              ? (dir > 0 ? x < cx : x > cx)
              : false);
        if (toC && Date.now() - started < 5000) {
          // keep going until the middle is passed (≤5s safety cap)
          T('move', step, 100);
          return;
        }
        if (Date.now() - started < total) {
          T('move', step, 100);
          return;
        }
        if (next) {
          next();
        } else if (kind === 'run') {
          // v0.9.132.8 — leg accounting: too many back-and-forth runs
          // = the puppy is TIRED (drink -> lie -> sleep at the centre)
          S.legs = (S.legs || 0) + 1;
          if (S.legs >= 6) {
            S.legs = 0;
            drinkAndSleep();
            return;
          }
          T('move', function () { moveBurst('run'); }, 200);
        } else {
          moveBurst('run');
        }
      };
      T('move', step, 100);
      if (kind === 'run') {
        T('flourish', playFlourish, 4000 + Math.random() * 4000);
      }
    };

    // ---- flourish pool (five-pick) + the 60% walk break ----
    const FLOURISH = [
      ['dog-rolling', 900], ['dog-hopping', 550], ['dog-balling', 850],
      ['dog-barking', 760], ['dog-spinning', 760],
    ];
    const playFlourish = function () {
      if (S.st !== 'run' && S.st !== 'walk') return;
      clearT('move');
      const pick = FLOURISH[Math.floor(Math.random() * FLOURISH.length)];
      S.st = 'flourish';
      setSkin(pick[0]);
      T('flourish', function () {
        if (S.st !== 'flourish') return;
        if (Math.random() < 0.6) {
          moveBurst('walk', function () { moveBurst('run'); });
        } else {
          moveBurst('run');
        }
      }, pick[1]);
    };
// ---- the sitting hub + the idle rotation (six mood picks) ----
    const IDLE = ['dog-boning', 'dog-tail-chasing', 'dog-yawning',
      'dog-licking', 'dog-tilting', 'dog-belling'];
    let idlePick = -1;
    const idleTick = function () {
      if (S.st !== 'sit') return;
      let i;
      do {
        i = Math.floor(Math.random() * IDLE.length);
      } while (i === idlePick && IDLE.length > 1);
      idlePick = i;
      setSkin(IDLE[i]);
      // the bone pick plays the finished-eating chain: bone -> lick
      if (IDLE[i] === 'dog-boning') {
        T('food', function () {
          if (S.st !== 'sit') return;
          setSkin('dog-licking');
        }, 950);
      }
      T('idle', idleTick, 4500 + Math.random() * 1500);
    };
    const settle = function () {
      S.st = 'sit';
      clearT('move');
      clearT('flourish');
      clearT('petTimer');
      S.pets = 0;
      dog.classList.add('dog-stopped');
      setSkin(null);
      T('idle', idleTick, 1500);
      T('bored', lieDown, 7000 + Math.random() * 3000);
      // the puppy never stays parked: after a sit (and a short lie) it
      // gets up and gallops again on its own
      T('again', wakeAndRun, 11000 + Math.random() * 4000);
      // a stretch of CONTINUOUS rest deepens into the locked sleep
      T('sleep', enterSleep, SLEEP_GATE());
    };
    // ---- bored: the ignored dog lies down (frozen Zzz) ----
    const lieDown = function () {
      if (S.st !== 'sit') return;
      S.st = 'lie';
      clearT('idle');
      setSkin('dog-lying');
      _dogParticles(dog, ['💤'], 1, 1600);
    };
    // the wake-up-and-run tick: sits/lying dogs resume the gallop
    const wakeAndRun = function () {
      if (S.st !== 'sit' && S.st !== 'lie') return;
      moveBurst('run');
    };
    // v0.9.132.8 — 來回跑太多次要休息：停下來、喝水(舔水)、走到視窗
    // 中間貼底線趴下、4 秒後深睡在正中間；點醒＝伸懶腰→呵欠→坐下，
    // 腿數已在觸發時歸零（休息完重新算）。
    const drinkAndSleep = function () {
      if (S.st !== 'run' && S.st !== 'walk') return;
      S.st = 'drink';
      clearT('move');
      clearT('flourish');
      setSkin('dog-licking');  // 喝水（沒有喝水 GIF——舔水 sheet 代班）
      T('drink', function () {
        if (S.st !== 'drink') return;
        moveBurst('walk', function () {
          if (S.st !== 'drink') return;
          // arrived at the centre, on the ground line: lie down
          S.st = 'lie';
          setSkin('dog-lying');
          _dogParticles(dog, ['💤'], 1, 1600);
          T('rest', function () {
            if (S.st !== 'lie') return;
            enterSleep();  // sleep at the centre, hugging the baseline
          }, 4000);
        }, 'center');
      }, 1200);
    };
    // ---- deep sleep: LOCKED; only the wake chain leaves ----
    const enterSleep = function () {
      if (S.st !== 'sit' && S.st !== 'lie') return;
      S.st = 'sleep';
      clearAllT();  // nothing automatic may fire past this
      setSkin('dog-sleeping');  // plays once and freezes at the Zzz
      _dogParticles(dog, ['💤'], 1, 1600);
    };
    const wakeUp = function () {
      if (S.st !== 'sleep') return;
      S.st = 'stretch';
      clearAllT();
      setSkin('dog-stretching');
      T('wake', function () {
        if (S.st !== 'stretch') return;
        setSkin('dog-yawning');
        T('wake', function () {
          if (S.st !== 'stretch') return;
          settle();
        }, 1000);
      }, 800);
    };
    // ---- angry: a grumbling bite (2nd streak / waking the sleeper) ----
    const getAngry = function () {
      S.st = 'angry';
      clearT('move');
      clearT('flourish');
      clearT('idle');
      clearT('follow');
      detachFollowMouse();
      dog.classList.remove('dog-stopped');
      setSkin('dog-biting');
      T('angry', function () { settle(); }, 750);
    };
    // ---- excitement follow: the 3-pet streak chases the cursor ----
    // pointer events (NOT mousemove): iOS touch drags stream pointermove
    const detachFollowMouse = function () {
      if (layer._dogFollowMouse) {
        document.removeEventListener('pointermove', layer._dogFollowMouse);
        layer._dogFollowMouse = null;
      }
    };
    const startFollow = function () {
      S.st = 'follow';
      clearT('move');
      clearT('flourish');
      clearT('idle');
      clearT('bored');
      dog.classList.remove('dog-stopped');
      setSkin('dog-running');
      let mx = null;
      const mt = { t: Date.now() };
      const onMove = function (ev) {
        mx = ev.clientX;
        mt.t = Date.now();
      };
      layer._dogFollowMouse = onMove;
      document.addEventListener('pointermove', onMove);
      const started = Date.now();
      const arrive = function () {
        detachFollowMouse();
        doPutdown();
      };
      const tick = function () {
        if (S.st !== 'follow' ||
            !document.getElementById('dog-run-layer')) {
          detachFollowMouse();
          return;
        }
        const now = Date.now();
        if (mx !== null) {
          const target = clampX(mx - 75);
          const x = curLeft();
          const dx = target - x;
          if (Math.abs(dx) <= 8) {
            arrive();
            return;
          }
          face(dx);
          try {
            dog.style.left =
              (x + Math.sign(dx) * Math.min(Math.abs(dx), 16)) + 'px';
          } catch (_) {}
        }
        if (now - mt.t > 2400 || now - started > 6000) {
          arrive();
          return;
        }
        T('follow', tick, 120);
      };
      T('follow', tick, 120);
    };
// ---- carry: tap the sitting dog = pick up; tap again = put down ----
    const detachCarryMouse = function () {
      if (layer._dogCarryMouse) {
        document.removeEventListener('pointermove', layer._dogCarryMouse);
        layer._dogCarryMouse = null;
      }
      layer._dogCarry = false;
    };
    const enterCarry = function () {
      S.st = 'carried';
      layer._dogCarry = true;
      setSkin('dog-carried');
      dog.classList.add('dog-carrying');
      // centre the dog under the lifting tap/finger right away
      const pt = layer._dogTapPt;
      if (pt && isFinite(pt.x) && isFinite(pt.y)) {
        try {
          const w = window.innerWidth || 1024;
          const h = window.innerHeight || 768;
          let x = Math.max(4, Math.min(pt.x - 75, w - 154));
          const bottom = Math.max(4,
            Math.min(h - pt.y - 60, h - 130));
          dog.style.left = x + 'px';
          dog.style.bottom = bottom + 'px';
        } catch (_) {}
        layer._dogTapPt = null;
      }
      const onMove = function (ev) {
        try {
          const w = window.innerWidth || 1024;
          const h = window.innerHeight || 768;
          let x = ev.clientX - 75;
          x = Math.max(4, Math.min(x, w - 154));
          const bottom = Math.max(4,
            Math.min(h - ev.clientY - 60, h - 130));
          dog.style.left = x + 'px';
          dog.style.bottom = bottom + 'px';
        } catch (_) {}
      };
      layer._dogCarryMouse = onMove;
      document.addEventListener('pointermove', onMove);
    };
    const pickUp = function () {
      S.st = 'lift';
      S.pets = 0;
      clearT('petTimer');
      clearT('idle');
      clearT('bored');
      dog.classList.remove('dog-stopped');
      setSkin('dog-lifting');
      T('lift', enterCarry, 720);
    };
    const doPutdown = function () {
      S.st = 'putdown';
      detachCarryMouse();
      detachFollowMouse();
      dog.classList.remove('dog-carrying');
      setSkin('dog-putdown');
      T('pd', function () {
        try { dog.style.bottom = '4px'; } catch (_) {}
        settle();
      }, 650);
    };
    // 丟出: the flung puppy arcs to the landing point, then runs the
    // chase and finishes playing with the ball
    const throwFly = function (vx) {
      S.st = 'throw';
      detachCarryMouse();
      detachFollowMouse();
      dog.classList.remove('dog-carrying');
      setSkin('dog-throwing');
      const x = curLeft();
      let y0 = 4;
      try { y0 = parseFloat(dog.style.bottom) || 4; } catch (_) {}
      const w = window.innerWidth || 1024;
      const dir = vx < 0 ? -1 : 1;
      const dist = Math.min(
        Math.max(Math.abs(vx) * 260, 200), w * 0.55);
      const target = clampX(x + dir * dist);
      const steps = 14;
      let i = 0;
      const step = function () {
        if (!document.getElementById('dog-run-layer')) return;
        i++;
        const t = i / steps;
        try {
          dog.style.left = (x + (target - x) * t) + 'px';
          dog.style.bottom = (4 + (y0 - 4) * (1 - t) +
            Math.sin(t * Math.PI) * 110) + 'px';
        } catch (_) {}
        face(dir);
        if (i < steps) {
          T('fly', step, 40);
          return;
        }
        try { dog.style.bottom = '4px'; } catch (_) {}
        setSkin('dog-putdown');
        T('land', function () {
          moveBurst('run', function () {
            setSkin('dog-balling');
            T('ball', function () { settle(); }, 1300);
          });
        }, 650);
      };
      step();
    };
    // ---- pointer: one tap dispatches by state (pointerup = tap works
    // for BOTH mouse and iPhone touch; click alone misses iOS drags) ----
    const onTap = function (ev) {
      try {
        layer._dogTapPt = { x: ev.clientX, y: ev.clientY };
      } catch (_) {}
      if (layer._dogCarry) {
        // carried: the release is resolved by onUpRelease (fling vs
        // gentle putdown); the tap itself must not pre-empt it.
        return;
      }
      if (S.st === 'dizzy') {
        // v0.9.132.8 — 安慰：拍拍頭暈的狗＝被撫摸，回坐姿
        // v0.9.133.1 — 皮膚換 AI 產開心蹦跳 PNG（dog-happy-bounce）
        S.st = 'comfort';
        clearT('wobble');
        clearT('dizzyAuto');
        setSkin('dog-happy');
        T('comfort', function () {
          if (S.st !== 'comfort') return;
          settle();
        }, 1400);
        return;
      }
      if (S.st === 'sleep') {
        wakeUp();  // locked: the wake chain is the only way out
        return;
      }
      if (S.st === 'lie') {
        getAngry();
        return;
      }
      if (S.st === 'sit') {
        pickUp();
        return;
      }
      if (S.st === 'lift' || S.st === 'putdown' || S.st === 'angry' ||
          S.st === 'boot' || S.st === 'throw' || S.st === 'stretch') {
        return;  // a reaction is playing: ignore (anti re-entry)
      }
      // a pet on the moving puppy: hearts + the 3-pet streak
      _dogParticles(dog, ['❤️', '💖', '✨'],
        2 + Math.floor(Math.random() * 2), 1600);
      S.pets++;
      T('petTimer', function () { S.pets = 0; }, 4000);
      if (S.pets >= 3) {
        S.pets = 0;
        clearT('petTimer');
        S.streaks++;
        if (S.streaks >= 2) {
          S.streaks = 0;
          getAngry();
        } else {
          startFollow();
        }
      }
    };
    dog.addEventListener('pointerup', onTap);
    // ---- drag-grab + fling-release plumbing (pointer events; the tap
    // is still the dog's own pointerup above — pointerdown/move/up only
    // feed the drag & fling decisions) ----
    let dragFrom = null;
    const onDown = function (ev) {
      if (S.st === 'sleep') {
        wakeUp();  // locked: the wake chain is the only way out
        return;
      }
      dragFrom = { x: ev.clientX, y: ev.clientY, t: Date.now() };
    };
    const onDragTrack = function (ev) {
      // the single pointermove sample-tracker (fling speed = the delta
      // between the last TWO moves; also the drag-grab detector)
      layer._prevMoveX = layer._lastMoveX;
      layer._prevMoveT = layer._lastMoveT;
      layer._lastMoveX = ev.clientX;
      layer._lastMoveT = Date.now();
      if (!dragFrom || layer._dogCarry) return;
      const dx = Math.abs(ev.clientX - dragFrom.x);
      const dy = Math.abs(ev.clientY - dragFrom.y);
      if (dx + dy > 14) {
        dragFrom = null;
        // stop the burst step-chain: a grabbed dog must not keep
        // trotting while it rides the cursor
        clearT('move');
        clearT('flourish');
        enterCarry();  // a real drag = grab mid-air
      }
    };
    const onUpRelease = function (ev) {
      if (layer._dogCarry) {
        // fling vs gentle putdown: the speed between the last two
        // pointermove samples while the release is still "hot"
        const now = Date.now();
        const dtUp = now - (layer._lastMoveT || 0);
        let vx = 0;
        if (dtUp < 160 && layer._prevMoveT !== undefined &&
            layer._lastMoveT - layer._prevMoveT >= 0) {
          // same-millisecond samples = instantaneous speed (÷1)
          vx = (layer._lastMoveX - layer._prevMoveX) /
            Math.max(layer._lastMoveT - layer._prevMoveT, 1);
        }
        dragFrom = null;
        if (Math.abs(vx) > 1.1) throwFly(vx);
        else doPutdown();
        return;
      }
      dragFrom = null;
    };
    dog.addEventListener('pointerdown', onDown);
    document.addEventListener('pointermove', onDragTrack);
    document.addEventListener('pointerup', onUpRelease);
    layer._dogDragMove = onDragTrack;
    layer._dogDragUp = onUpRelease;
    // v0.9.132.8 — LLM 回合數太多會頭暈要安慰：pollTurnStream counts
    // the llm_round events and calls _dogDizzy() at the threshold.
    // 頭暈＝原地轉圈＋搖頭晃腦小碎步（spin/tilt 交替）；拍拍它＝躺下
    // 被撫摸(belling)→回坐姿；沒人安慰 15 秒後自己緩過來。
    layer._dogDizzyHook = function () {
      if (S.st === 'carried' || S.st === 'sleep' || S.st === 'throw' ||
          S.st === 'lift' || S.st === 'putdown' || S.st === 'dizzy' ||
          S.st === 'drink' || S.st === 'comfort') {
        return;  // don't yank the dog out of a physical moment mid-flight
      }
      clearT('move');
      clearT('flourish');
      clearT('bored');
      clearT('again');
      clearT('idle');
      clearT('food');
      clearT('follow');
      detachFollowMouse();
      S.st = 'dizzy';
      dog.classList.remove('dog-stopped');
      let wx = curLeft();
      // v0.9.133.1 — 頭暈皮膚＝AI 產 GIF（原地轉圈），wobble 只負責
      // 左右小碎步位移，GIF 幀自帶暈眩效果（不再交替 spin/tilt）
      setSkin('dog-dizzy');
      const wob = function () {
        if (S.st !== 'dizzy') return;
        wx = clampX(wx + (Math.random() < 0.5 ? -12 : 12));
        try { dog.style.left = wx + 'px'; } catch (_) {}
        T('wobble', wob, 300);
      };
      wob();
      T('dizzyAuto', function () {
        if (S.st !== 'dizzy') return;
        settle();  // nobody comforted it — shake it off alone
      }, 15000);
    };
    // hoisted declarations: the probe may fire before these are reached
    function openIdle() {
      // v0.9.132.7 — the emoji fallback parks at the centre (the CSS
      // birth slot is off-screen-left = invisible); sprite mode keeps
      // its own run-in entrance.
      try {
        dog.style.left = Math.max(4, Math.min(
          (window.innerWidth || 1024) / 2 - 77,
          (window.innerWidth || 1024) - 154)) + 'px';
      } catch (_) {}
      settle();
    }
    // entrance: ALWAYS run IN toward the middle first (the dog is born
    // off-screen-left), then normal random roaming (50% walk break)
    function openWalk() {
      moveBurst('run', function () {
        if (Math.random() < 0.5) {
          moveBurst('walk', function () { moveBurst('run'); });
        } else {
          moveBurst('run');
        }
      }, true);
    }
    // sprite probe LAST (after every const above is initialised)
    const probe = new Image();
    probe.onload = function () {
      dogSpriteOK = true;
      skin.style.removeProperty('background-image');
      openWalk();
    };
    probe.onerror = function () {
      skin.textContent = '🐕';
      skin.classList.add('dog-emoji');
      openIdle();
    };
    probe.src = 'dog_run_sheet.png' + V;
    // 12s gallop cap: settle into the sitting hub (only while roaming)
    T('stop', function () {
      if (S.st === 'run' || S.st === 'walk' || S.st === 'flourish') {
        settle();
      }
    }, 12000);
  }

  // v0.9.132.8 — the LLM-round dizzy hook: called by pollTurnStream
  // when a turn's llm_round count crosses the dizzy threshold. No-op
  // when the dog isn't out.
  function _dogDizzy() {
    const layer = document.getElementById('dog-run-layer');
    if (layer && typeof layer._dogDizzyHook === 'function') {
      try { layer._dogDizzyHook(); } catch (_) {}
    }
  }

  function _dogRunStop() {
    const layer = document.getElementById('dog-run-layer');
    if (!layer) return;
    try {
      // the state machine keeps every timer in layer._dogT (keyed)
      const t = layer._dogT || {};
      Object.keys(t).forEach(function (k) {
        clearTimeout(t[k]);
      });
      layer._dogT = {};
      if (layer._dogCarryMouse) {
        document.removeEventListener('pointermove', layer._dogCarryMouse);
        layer._dogCarryMouse = null;
      }
      if (layer._dogFollowMouse) {
        document.removeEventListener('pointermove', layer._dogFollowMouse);
        layer._dogFollowMouse = null;
      }
      if (layer._dogDragMove) {
        document.removeEventListener('pointermove', layer._dogDragMove);
        layer._dogDragMove = null;
      }
      if (layer._dogDragUp) {
        document.removeEventListener('pointerup', layer._dogDragUp);
        layer._dogDragUp = null;
      }
      layer._dogCarry = false;
      layer.style.transition = 'opacity .3s ease';
      layer.style.opacity = '0';
      setTimeout(function () {
        try { layer.remove(); } catch (_) {}
      }, 320);
    } catch (_) {}
  }

  // v0.9.132.9 — clipboard buttons: chat input copy/paste + the notes
  // document body copy/cut/paste. Null-guarded + idempotent (same
  // pattern as bindUpload/bindMic).
  function bindClipboard() {
    if (bindClipboard._done) return;
    bindClipboard._done = true;
    const inp = document.getElementById('chat-input');
    const inpCopy = document.getElementById('chat-clip-copy');
    const inpPaste = document.getElementById('chat-clip-paste');
    if (inpCopy) {
      inpCopy.addEventListener('click', async function () {
        try {
          const v = (inp && inp.value) || '';
          if (!v.trim()) {
            toast('📋 ' + t('clipEmpty'), 'ok', 2000);
            return;
          }
          const ok = await _clipWrite(v);
          toast(ok ? '📋 ' + t('clipCopied')
                   : '⚠️ ' + t('clipPasteFail'),
            ok ? 'ok' : 'error', 2500);
        } catch (_) {}
      });
    }
    if (inpPaste) {
      inpPaste.addEventListener('click', async function () {
        try {
          const txt = await _clipRead();
          if (txt == null) {
            toast('⚠️ ' + t('clipPasteFail'), 'error', 3000);
            return;
          }
          if (inp) {
            _insertAtCaret(inp, txt);
            inp.focus();
          }
          toast('📥 ' + t('clipPasted'), 'ok', 2000);
        } catch (_) {}
      });
    }
    // the notes DOCUMENT BODY (文件框): copy / cut the selection (or
    // the whole body when nothing is selected) / paste at the caret.
    const neClip = function (mode) {
      const ta = document.getElementById('ne-body');
      if (!ta) return;
      const s = ta.selectionStart || 0;
      const e = ta.selectionEnd || 0;
      if (mode === 'copy' || mode === 'cut') {
        const txt = (e > s) ? ta.value.slice(s, e) : ta.value;
        _clipWrite(txt).then(function (ok) {
          if (!ok) {
            toast('⚠️ ' + t('clipPasteFail'), 'error', 2500);
            return;
          }
          if (mode === 'cut' && e > s) {
            ta.value = ta.value.slice(0, s) + ta.value.slice(e);
            try { ta.setSelectionRange(s, s); } catch (_) {}
            ta.dispatchEvent(new Event('input', { bubbles: true }));
            ta.focus();
          }
          toast('📋 ' + t('clipCopied'), 'ok', 2000);
        });
        return;
      }
      _clipRead().then(function (txt) {
        if (txt == null) {
          toast('⚠️ ' + t('clipPasteFail'), 'error', 3000);
          return;
        }
        _insertAtCaret(ta, txt);
        ta.dispatchEvent(new Event('input', { bubbles: true }));
        ta.focus();
        toast('📥 ' + t('clipPasted'), 'ok', 2000);
      });
    };
    const map = [
      ['ne-clip-copy', 'copy'], ['ne-clip-cut', 'cut'],
      ['ne-clip-paste', 'paste'],
    ];
    map.forEach(function (pair) {
      const btn = document.getElementById(pair[0]);
      if (btn) {
        btn.addEventListener('click', function () { neClip(pair[1]); });
      }
    });
  }

  function bindMic() {
    if (bindMic._done) return;
    bindMic._done = true;
    const btn = _micBtn();
    if (btn) btn.addEventListener('click', function () {
      // v0.9.127.3 — the mic toggle must NEVER die silently (the true
      // root cause of「語音輸入不能用」: the support-check call drifted
      // to _isVoiceSupported — a name that does not exist — so every
      // 🎤 click threw an invisible ReferenceError before anything
      // could toast). Any error now surfaces as the voiceFail toast.
      try {
        Promise.resolve(toggleMicRecording()).catch(function (e) {
          toast('⚠️ ' + t('voiceFail',
            { msg: ((e && e.message) || String(e)).slice(0, 140) }),
            'error', 6000);
        });
      } catch (e) {
        toast('⚠️ ' + t('voiceFail',
          { msg: ((e && e.message) || String(e)).slice(0, 140) }),
          'error', 6000);
      }
    });
  }

  // ─── v0.9.39.0+ — Credentials tab (central account sets) ─────────
  // Same CredentialStore the MCP cred_* tools and the CLI manage.
  // Table + add form; every value shown is ALREADY masked Python-side.

  function renderCredsTable() {
    const tbody = document.getElementById('creds-tbody');
    if (!tbody) return;
    tbody.replaceChildren();
    api.safeCall('list_credentials').then(function (r) {
      const entries = (r && !r.error && r.credentials) || [];
      if (!entries.length) {
        const tr = document.createElement('tr');
        const td = document.createElement('td');
        td.colSpan = 6;
        td.textContent = '(empty)';
        tr.appendChild(td);
        tbody.appendChild(tr);
        return;
      }
      entries.forEach(function (e) {
        const tr = document.createElement('tr');
        const vals = [
          e.name || '', e.username_masked || '',
          e.credential_masked || '', e.service || '',
          e.notes || '',
        ];
        vals.forEach(function (v) {
          const td = document.createElement('td');
          td.textContent = v;
          tr.appendChild(td);
        });
        const tdAct = document.createElement('td');
        const del = document.createElement('button');
        del.className = 'btn btn-danger';
        del.textContent = t('credsDelete') || '🗑';
        del.addEventListener('click', function () {
          if (!confirm(t('credsConfirmDelete'))) return;
          api.safeCall('remove_credential', e.name).then(function () {
            toast(t('credsDeleted'), 'ok', 2500);
            renderCredsTable();
          });
        });
        tdAct.appendChild(del);
        tr.appendChild(tdAct);
        tbody.appendChild(tr);
      });
    });
  }

  function saveCredFromForm() {
    const name = (document.getElementById('cred-name') || {}).value || '';
    if (!name.trim()) {
      toast("'name' is required", 'error', 3000);
      return;
    }
    const payload = {
      name: name.trim(),
      username: (document.getElementById('cred-user') || {}).value || '',
      password: (document.getElementById('cred-pw') || {}).value || '',
      password_env:
        (document.getElementById('cred-pw-env') || {}).value || '',
      service:
        (document.getElementById('cred-service') || {}).value || 'generic',
      notes: (document.getElementById('cred-notes') || {}).value || '',
      overwrite:
        !!(document.getElementById('cred-overwrite') || {}).checked,
    };
    api.safeCall('save_credential', payload).then(function (r) {
      if (r && r.error) {
        toast(r.error, 'error', 3500);
        return;
      }
      toast(t('credsSaved'), 'ok', 2500);
      ['cred-name', 'cred-user', 'cred-pw', 'cred-pw-env',
       'cred-service', 'cred-notes'].forEach(function (id) {
        const el = document.getElementById(id);
        if (el) el.value = '';
      });
      const ow = document.getElementById('cred-overwrite');
      if (ow) ow.checked = false;
      renderCredsTable();
    });
  }

  /* ── Quick phrases (v0.9.101.0) — GLOBAL store, all devices share
   * $QDRANT_RAG_HOME/snippets.json via Api snippets_get/snippets_save.
   * localStorage is only an OFFLINE CACHE fallback. The ➕ add row is
   * hideable (▲/▼ manage toggle); opening the panel SHRINKS #chat-log
   * so the input box never moves. ── */
  const SNIPS_KEY = 'qdrant-rag.snippets';
  const SNIPS_MAX = 50;
  let chatSnippets = [];
  let snipsManageOn = false;   /* v0.9.101.3 — default = add row hidden */

  function _snipsLog() {
    return document.getElementById('chat-log');
  }

  function _snipsAdjustLog(on) {
    /* shrink the chat log by the panel height so the input box stays
       put (requirement: enabling snippets must not move the input) */
    const panel = document.getElementById('chat-snips-panel');
    const log = _snipsLog();
    if (!panel || !log) return;
    if (on && !panel.hidden) {
      if (!log.dataset.snipsOrig) {
        log.dataset.snipsOrig = String(log.offsetHeight);
      }
      const h = Math.max(160,
        parseInt(log.dataset.snipsOrig, 10) - panel.offsetHeight);
      log.style.height = h + 'px';
    } else if (!on && log.dataset.snipsOrig) {
      log.style.height = '';
      delete log.dataset.snipsOrig;
    }
  }

  function _snipsCacheRead() {
    try {
      const arr = JSON.parse(localStorage.getItem(SNIPS_KEY) || '[]');
      return Array.isArray(arr) ? arr.filter(
        s => typeof s === 'string' && s.trim()).slice(0, SNIPS_MAX) : [];
    } catch (e) { return []; }
  }

  function _snipsCacheWrite() {
    try { localStorage.setItem(SNIPS_KEY, JSON.stringify(chatSnippets)); }
    catch (e) { /* quota — cache only, global store still works */ }
  }

  async function loadSnippets() {
    /* GLOBAL: fetch the shared server-side list; localStorage is the
       offline fallback only. */
    try {
      const r = await api.call('snippets_get');
      const arr = r && Array.isArray(r.snippets) ? r.snippets : null;
      if (arr) {
        chatSnippets = arr.slice(0, SNIPS_MAX);
        _snipsCacheWrite();
        return chatSnippets;
      }
    } catch (e) { /* fall through to cache */ }
    chatSnippets = _snipsCacheRead();
    return chatSnippets;
  }

  async function saveSnippets() {
    _snipsCacheWrite();
    try {
      const r = await api.call('snippets_save',
        { snippets: chatSnippets });
      if (r && Array.isArray(r.snippets)) {
        chatSnippets = r.snippets;   /* server-sanitized list */
        _snipsCacheWrite();
      } else if (r && r.error) {
        toast('⚠ ' + r.error);
      }
    } catch (e) { toast('⚠ ' + e); }
  }

  function renderSnippets() {
    const list = document.getElementById('chat-snips-list');
    const count = document.getElementById('chat-snips-count');
    if (!list) return;
    list.replaceChildren();
    chatSnippets.forEach((s, i) => {
      const chip = document.createElement('span');
      chip.className = 'snip-chip';
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'snip-text';
      /* v0.9.122.0 — MULTI-LINE entries: the chip label shows the
         FIRST line (+ a ⏎ marker when there is more), the tooltip
         carries the FULL text; inserting stays the verbatim
         multi-line insert (insertSnippet). */
      const firstLine = String(s).split('\n')[0];
      const nl = (String(s).match(/\n/g) || []).length;
      btn.textContent = nl ? `${firstLine} ⏎${nl}`
        : firstLine;
      btn.title = s;
      btn.addEventListener('click', () => insertSnippet(s));
      /* v0.9.122.0 — ✎ edit (管理 mode): the chip swaps into an
         inline multi-line editor (✔ save / ✕ cancel). */
      const ed = document.createElement('button');
      ed.type = 'button';
      ed.className = 'snip-edit';
      ed.textContent = '✎';
      ed.hidden = !snipsManageOn;
      ed.setAttribute('aria-label', 'edit');
      ed.addEventListener('click', () => _snipEditChip(chip, i));
      const del = document.createElement('button');
      del.type = 'button';
      del.className = 'snip-del';
      del.textContent = '✕';
      del.hidden = !snipsManageOn;
      del.setAttribute('aria-label', 'delete');
      del.addEventListener('click', () => {
        chatSnippets.splice(i, 1);
        saveSnippets().then(renderSnippets);
      });
      chip.appendChild(btn);
      chip.appendChild(ed);
      chip.appendChild(del);
      list.appendChild(chip);
    });
    /* v0.9.101.4 — the "N 個片語" count row is gone (user: 不用出現) */
    if (count) {
      const head = count.parentElement;   /* .chat-tools-head */
      if (head) head.hidden = true;
    }
    const empty = document.getElementById('chat-snips-empty');
    if (empty) empty.hidden = chatSnippets.length > 0;
    /* v0.9.101.2 — the add row is OUTSIDE #chat-snips-list (a sibling
       in .snips-row) so replaceChildren can never swallow it; just
       sync its visibility here. */
    const wrap = document.getElementById('chat-snips-add-wrap');
    if (wrap) wrap.hidden = !snipsManageOn;
    _snipsAdjustLog(true);
  }

  function _snipsManageApply() {
    const mg = document.getElementById('chat-snips-manage');
    if (mg) mg.textContent = snipsManageOn
      ? t('snipsHideAdd') : t('snipsShowAdd');
    document.querySelectorAll('.snip-chip .snip-del').forEach(
      el => { el.hidden = !snipsManageOn; });
    document.querySelectorAll('.snip-chip .snip-edit').forEach(
      el => { el.hidden = !snipsManageOn; });
    const wrap = document.getElementById('chat-snips-add-wrap');
    if (wrap) wrap.hidden = !snipsManageOn;
  }

  /* v0.9.122.0 — inline MULTI-LINE editor for one chip. */
  function _snipEditChip(chip, i) {
    chip.replaceChildren();
    const ta = document.createElement('textarea');
    ta.className = 'snip-edit-area';
    ta.rows = Math.min(8, Math.max(2, chatSnippets[i]
      .split('\n').length));
    ta.value = chatSnippets[i];
    const ok = document.createElement('button');
    ok.type = 'button'; ok.className = 'btn';
    ok.textContent = '✔';
    ok.setAttribute('aria-label', 'save');
    const cancel = document.createElement('button');
    cancel.type = 'button'; cancel.className = 'btn';
    cancel.textContent = '✕';
    cancel.setAttribute('aria-label', 'cancel');
    ok.addEventListener('click', async () => {
      const v = (ta.value || '').trim();
      if (!v) { toast('⚠ ' + t('snipsEmpty')); return; }
      if (chatSnippets.includes(v)) {
        toast('⚠ ' + t('snipsDup')); return;
      }
      chatSnippets[i] = v;
      await saveSnippets();
      renderSnippets();
    });
    cancel.addEventListener('click', renderSnippets);
    chip.appendChild(ta);
    chip.appendChild(ok);
    chip.appendChild(cancel);
    ta.focus();
  }
  function insertSnippet(text) {
    /* v0.9.115.3 — CURSOR insertion (user: 不要每次重頭開始):
       the snippet lands AT the caret — replacing the selection when
       one exists — and the caret ends up RIGHT AFTER the inserted
       text, so consecutive drops continue where the last one ended
       instead of always appending at the tail.
       The old separator rule is preserved, applied AT the insertion
       point: one space when the character BEFORE the caret is
       non-whitespace (append-at-end keeps producing "text 片語"). */
    const input = document.getElementById('chat-input');
    if (!input) return;
    const cur = input.value;
    let start = typeof input.selectionStart === 'number'
      ? input.selectionStart : cur.length;
    let end = typeof input.selectionEnd === 'number'
      ? input.selectionEnd : cur.length;
    if (end < start) { const tmp = start; start = end; end = tmp; }
    const before = cur.slice(0, start);
    const after = cur.slice(end);
    /* glue-space rule applies at a COLLAPSED caret only (typing
       semantics); replacing an explicit selection swaps it verbatim */
    const needsSp = start === end && before
      && !/\s$/.test(before);
    const ins = (needsSp ? ' ' : '') + text;
    input.value = before + ins + after;
    input.focus();
    const caret = start + ins.length;
    input.setSelectionRange(caret, caret);
  }

  function toggleSnipsPanel(force) {
    const panel = document.getElementById('chat-snips-panel');
    if (!panel) return;
    const show = typeof force === 'boolean'
      ? force : panel.hidden;
    panel.hidden = !show;
    if (show) {
      loadSnippets().then(renderSnippets);
      _snipsAdjustLog(true);
    } else {
      _snipsAdjustLog(false);
    }
  }

  async function addSnippetFromInput() {
    const inp = document.getElementById('chat-snips-input');
    if (!inp) return;
    const v = (inp.value || '').trim();
    if (!v) { return; }
    if (chatSnippets.includes(v)) { toast('⚠ ' + t('snipsDup')); return; }
    if (chatSnippets.length >= SNIPS_MAX) {
      toast('⚠ ' + t('snipsMax')); return;
    }
    chatSnippets.push(v);
    inp.value = '';
    await saveSnippets();
    renderSnippets();
  }

  function bindSnippets() {
    const btn = document.getElementById('chat-snips-btn');
    if (!btn) return;
    btn.addEventListener('click', () => toggleSnipsPanel());
    const add = document.getElementById('chat-snips-add');
    const inp = document.getElementById('chat-snips-input');
    if (add) add.addEventListener('click', addSnippetFromInput);
    if (inp) inp.addEventListener('keydown', (ev) => {
      /* v0.9.122.0 — the add row is a TEXTAREA (multi-line):
         Enter inserts a newline; Ctrl+Enter / Cmd+Enter submits. */
      if (ev.key === 'Enter' && (ev.ctrlKey || ev.metaKey)) {
        ev.preventDefault(); addSnippetFromInput();
      }
    });
    const mg = document.getElementById('chat-snips-manage');
    if (mg) mg.addEventListener('click', () => {
      snipsManageOn = !snipsManageOn;
      _snipsManageApply();
      _snipsAdjustLog(true);
    });
    _snipsManageApply();   /* v0.9.101.3 — paint the default label */
  }

  // ===== v0.9.126.6 — no-zoom guard (web mode) ==========================
  // The web app must NOT be zoomable. Layer 1 = the viewport meta
  // (user-scalable=no); iOS Safari ignores it for pinch, so layer 2
  // cancels the zoom gestures/shortcuts at the event level. Gated to
  // window.__WEB_MODE__ so the desktop pywebview GUI keeps stock zoom
  // behaviour; scrolling (pan) is never touched.
  function bindZoomGuard() {
    if (!isWebMode()) return;
    // iOS Safari pinch — the gesture events user-scalable=no cannot stop
    ['gesturestart', 'gesturechange', 'gestureend'].forEach(function (ev) {
      document.addEventListener(ev, function (e) { e.preventDefault(); },
        { passive: false });
    });
    // desktop ctrl/cmd + wheel zoom
    document.addEventListener('wheel', function (e) {
      if (e.ctrlKey || e.metaKey) e.preventDefault();
    }, { passive: false });
    // ctrl/cmd + '+' / '-' / '0' keyboard zoom shortcuts
    document.addEventListener('keydown', function (e) {
      if ((e.ctrlKey || e.metaKey)
          && (e.key === '+' || e.key === '-' || e.key === '='
              || e.key === '0')) {
        e.preventDefault();
      }
    });
  }

  // v0.9.133.0 — bubble style UI: the 🎨 toggle + colour inputs +
  // opacity sliders + reset. Live-applies and persists.
  function bindBubbleStyle() {
    if (bindBubbleStyle._done) return;
    bindBubbleStyle._done = true;
    const btn = document.getElementById('chat-style-btn');
    const panel = document.getElementById('chat-style-panel');
    const logEl = document.getElementById('chat-log');
    if (!btn || !panel) return;
    const roles = ['asst', 'user'];
    const els = {};
    roles.forEach(function (role) {
      els[role] = {
        color: document.getElementById(
          'bub-' + (role === 'asst' ? 'asst' : 'user') + '-color'),
        alpha: document.getElementById(
          'bub-' + (role === 'asst' ? 'asst' : 'user') + '-alpha'),
      };
    });
    function syncInputs() {
      const st = _loadBubbleStyle() || {};
      const cs = getComputedStyle(logEl || document.body);
      roles.forEach(function (role) {
        const cfg = (st && st[role]) || null;
        if (cfg && cfg.c) {
          // normalise any stored colour to #rrggbb for the colour input
          const rgba = _hexToRgba(cfg.c, 1) || '';
          const m = /rgba\((\d+),\s*(\d+),\s*(\d+)/.exec(rgba);
          if (m) {
            els[role].color.value = '#' +
              ((1 << 24) + (+m[1] << 16) + (+m[2] << 8) + (+m[3]))
                .toString(16).slice(1);
          }
        } else {
          // theme default: pre-fill from the live base rule var
          const v = (cs.getPropertyValue(
            role === 'asst' ? '--bg' : '--primary') || '').trim();
          const m = /rgba?\((\d+),\s*(\d+),\s*(\d+)/.exec(v) ||
            /^#([0-9a-f]{6})$/i.exec(v);
          if (m) {
            els[role].color.value = m[4] === undefined && m[1].indexOf('#') === 0
              ? m[1].toLowerCase()
              : '#' + ((1 << 24) + (+m[1] << 16) + (+m[2] << 8) + (+m[3]))
                  .toString(16).slice(1);
          }
        }
        els[role].alpha.value = (cfg && typeof cfg.a === 'number')
          ? Math.round(cfg.a * 100) : 100;
      });
    }
    function collectAndApply() {
      const st = _loadBubbleStyle() || {};
      roles.forEach(function (role) {
        st[role] = {
          c: els[role].color.value || '#000000',
          a: (parseInt(els[role].alpha.value, 10) || 100) / 100,
        };
      });
      _saveBubbleStyle(st);
      _applyBubbleStyle();
    }
    roles.forEach(function (role) {
      els[role].color.addEventListener('input', collectAndApply);
      els[role].alpha.addEventListener(
        'input', collectAndApply);
    });
    const rst = document.getElementById('chat-style-reset');
    if (rst) {
      rst.addEventListener('click', function () {
        try { localStorage.removeItem('chat-bubble-style'); } catch (_) {}
        _applyBubbleStyle();
        syncInputs();
      });
    }
    btn.addEventListener('click', function () {
      panel.hidden = !panel.hidden;
      if (!panel.hidden) { syncInputs(); }
    });
    _applyBubbleStyle();  // restore any saved style on boot
  }

  function bindEvents() {

    // v0.9.125.3 — bind the upload/mic/paste/drop group FIRST: any
    // missing-toolbar-button TypeError further down used to abort
    // bindEvents BEFORE bindUpload ran = the 📎 button dead with zero
    // listeners (=「按了上傳沒有反應」). Bind the risky-late group up
    // front — all four are individually null-guarded.
    bindUpload();
    bindMic();
    // v0.9.132.9 — the clipboard buttons (chat input + notes 文件框).
    bindClipboard();
    // v0.9.133.0 — bubble colour/opacity (歷史氣泡樣式).
    bindBubbleStyle();
    // v0.9.127.11 — auto-TTS + hands-free voice-loop toggles.
    bindVoiceToggles();
    bindPaste();
    bindDrop();
    // v0.9.126.6 — no-zoom guard (web mode only): block pinch gestures
    // (iOS Safari ignores user-scalable=no) + ctrl/cmd wheel & keyboard
    // zoom on the desktop browser.
    bindZoomGuard();
    // v0.9.127.5 — Android share_target landing (/?shared=… query
    // behind the 303): early, idempotent, null-guarded; it inserts
    // the markers and rewrites the URL away so a reload never
    // replays the same share.
    handleSharedLanding();

    document.querySelectorAll('.tab').forEach(function (el) {
      el.addEventListener('click', function () {
        switchTab(el.dataset.tab);
        // v0.9.39.0+ — refresh the credential table whenever its
        // tab becomes visible (cheap local read, no network).
        if (el.dataset.tab === 'creds') renderCredsTable();
      });
    });
    const langSel = document.getElementById('lang-select');
    if (langSel) langSel.addEventListener('change', function (e) { setLang(e.target.value); });

    document.getElementById('chat-send').addEventListener('click', sendChat);
    // v0.9.14.0+ — chat tool picker (per-request allow-list)
    document.getElementById('chat-tools-btn')
      .addEventListener('click', toggleChatToolsPanel);
    // v0.9.100.0 — quick-phrases panel (⚡ button in the chat toolbar)
    bindSnippets();
    document.getElementById('chat-tools-all')
      .addEventListener('click', function () { setAllChatTools(true); });
    document.getElementById('chat-tools-none')
      .addEventListener('click', function () { setAllChatTools(false); });
    document.getElementById('chat-tools-reset')
      .addEventListener('click', resetChatTools);
    // v0.9.47.0+ — background task panel (⏳ toolbar button)
    document.getElementById('chat-tasks-btn')
      .addEventListener('click', toggleTaskPanel);
    const tasksRefresh = document.getElementById('chat-tasks-refresh');
    if (tasksRefresh) {
      tasksRefresh.addEventListener('click', renderTaskPanel);
    }
    loadDisabledTools();
    updateChatToolsBadge();
    // v0.9.39.0+ — Credentials tab (central account sets)
    const credsRefresh = document.getElementById('creds-refresh');
    if (credsRefresh) {
      credsRefresh.addEventListener('click', renderCredsTable);
      renderCredsTable();
    }
    const credSave = document.getElementById('cred-save');
    if (credSave) credSave.addEventListener('click', saveCredFromForm);
    // v0.9.16.0+ — web-mode client upload (📎 any file / 📷 media on
    // mobile web; hidden entirely in pywebview GUI mode).
    bindUpload();
    // v0.9.68.0+ — chat voice-IN mic button (both web and GUI).
    bindMic();
    // v0.9.29.0+/120.1 — paste & drop are bound EARLY at the top of
    // bindEvents (v0.9.125.3: the risky-late group — upload/mic/
    // paste/drop — now runs first so a dead toolbar element can no
    // longer skip the upload wiring).
    document.getElementById('chat-new').addEventListener('click', newSession);
    document.getElementById('chat-save').addEventListener('click', saveSession);
    document.getElementById('chat-clear').addEventListener('click', clearChat);
    // v0.9.133.8 — ✏️ rename the CURRENT session from the chat tab
    // (tooltip carries the prompt text; never silently dead).
    const renBtn = document.getElementById('chat-rename');
    if (renBtn) {
      renBtn.title = t('sessRenamePrompt');
      renBtn.addEventListener('click', renameCurrentSession);
    }
    // v0.9.5.8: the chat-toolbar "📊 Diagrams Viewer" button was
    // removed — it opened a BLANK viewer window and had no way to
    // receive the XML from any chat message, so it had no use.
    // The per-message "View in Diagrams" buttons in renderChat()
    // (open_diagrams_with_xml) remain the only entry points.
    // v0.8.7+: 3-layer safety net for unsaved chat drafts.
    // (1) autoSaveChat is already called at the end of sendChat.
    // (2) beforeunload fires one last save when the renderer
    //     starts tearing down (X click, Alt-F4, programmatic
    //     window.destroy).
    // (3) Page Visibility API: when the user alt-tabs away
    //     mid-stream, mirror to pywebview.state so the closing
    //     hook can pick it up even if beforeunload is bypassed.
    window.addEventListener('beforeunload', _beforeUnloadSave);
    document.addEventListener('visibilitychange', function () {
      if (document.visibilityState === 'hidden') {
        syncStateMirror();
      }
    });
    // Initial mirror at boot — covers the edge case where the
    // previous session's last reply wasn't fully saved before
    // the renderer was torn down (e.g. force-killed process).
    // First autoSaveChat below flushes any pending writes; then
    // syncStateMirror pushes the full state into Python.
    setTimeout(function () {
      // Defer one tick so chatSession has been initialised.
      if (typeof chatSession !== 'undefined') {
        syncStateMirror();
        bootDraftRecovery();
      }
    }, 0);
    const picker = document.getElementById('chat-session-picker');
    if (picker) picker.addEventListener('change', function (e) { loadSessionById(e.target.value); });
    // v0.9.103.1 — the ☑ select-all tri-state lives on the static
    // table header; bind ONCE at boot (addEventListener would stack
    // if rebound per refresh).
    _sessSelectAllHandler();
    const chatInput = document.getElementById('chat-input');
    if (chatInput) {
      chatInput.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
          e.preventDefault();
          sendChat();
        }
      });
    }

    document.getElementById('ingest-go').addEventListener('click', runIngest);
    document.getElementById('ingest-pick-file').addEventListener('click', pickIngestFile);
    document.getElementById('ingest-pick-dir').addEventListener('click', pickIngestDir);

    document.getElementById('search-go').addEventListener('click', runSearch);

    document.getElementById('admin-recreate').addEventListener('click', adminRecreate);
    document.getElementById('admin-delete').addEventListener('click', adminDelete);
    const cc = document.getElementById('admin-clear-cache');
    if (cc) cc.addEventListener('click', adminClearCache);
    // v0.9.112.0+ hash-pollution audit + repair
    const ab = document.getElementById('admin-audit-backends');
    if (ab) ab.addEventListener('click', adminAuditBackends);
    const rh = document.getElementById('admin-reembed-hash');
    if (rh) rh.addEventListener('click', adminReembedHash);
    // v0.9.113.0+ STAIR heading-path backfill
    const bh = document.getElementById('admin-backfill-headings');
    if (bh) bh.addEventListener('click', adminBackfillHeadings);
    // v0.9.42.0+ hot reload + service restart
    const rc = document.getElementById('admin-reload-config');
    if (rc) rc.addEventListener('click', adminReloadConfig);

    // v0.8.0+ MCP fleet editor wiring
    if (typeof bindMcpEditor === 'function') bindMcpEditor();

    document.getElementById('stats-refresh').addEventListener('click', refreshStats);
    // v0.9.60.0+: collection picker + paged data browser wiring
    const statSel = document.getElementById('stats-collections');
    if (statSel) statSel.addEventListener('change', function () {
      resetStatsDataBrowser();
      refreshStats();
    });
    const sld = document.getElementById('stats-load-data');
    if (sld) sld.addEventListener('click', function () { loadStatsData(true); });
    const snx = document.getElementById('stats-data-next');
    if (snx) snx.addEventListener('click', statsDataNext);
    const spv = document.getElementById('stats-data-prev');
    if (spv) spv.addEventListener('click', statsDataPrev);
    document.getElementById('sessions-refresh').addEventListener('click', refreshSessions);
    if (typeof bindSchedules === 'function') bindSchedules();
    const bdb = document.getElementById('docs-bulk-delete');
    if (bdb) bdb.addEventListener('click', removeSelectedDocuments);
  }

  function boot() {
    _mdStartupCheck();
    setupTheme();
    loadAppearance(); // v0.9.99.0 — bg/accent/font before first paint
    applyI18n();
    bindEvents();
    bindAppearance();
    refreshStatus();
    // v0.9.60.4 — fill the Search/Stats collection pickers AT BOOT
    // (direct-to-Search used to show an empty dropdown).
    refreshCollectionSelectors();
    refreshSessionPicker();
    // v0.9.125.9 — PWA installability (web mode only): registering a
    // NETWORK-ONLY service worker satisfies the Chrome install gate;
    // the installed app launches STANDALONE (no browser banner).
    if (window.__WEB_MODE__ === true
        && 'serviceWorker' in navigator) {
      try {
        navigator.serviceWorker.register('sw.js')
          .catch(function () {});   // never a boot blocker
      } catch (_) {}
    }
    setInterval(refreshStatus, 30000);
  }

  // ─── v0.8.7+ Splash overlay + readiness checks ────────────────
  //
  // Runs BEFORE the main boot(). Three rows (backend / Qdrant /
  // LLM keys) settle in parallel; each row has a 5 s timeout so
  // a slow Qdrant can never block forever, and a Skip button is
  // always visible for the impatient user. The bridge call also
  // has a single-line catch-all so any backend mis-wire still
  // falls through to the main app instead of freezing the GUI.
  //
  // All state lives in a single closure — no global leaks.

  const SPLASH_CHECK_TIMEOUT_MS = 5000;
  // v0.9.80.0+: the MCP row builds the whole McpManager (12+ local
  // hosts + stdio/HTTP connects ≈ 15-25 s on the real box) — it gets
  // its own, much longer timeout so the splash waits for it instead
  // of failing it at 5 s. The Skip button still bypasses everything.
  const SPLASH_MCP_TIMEOUT_MS = 90000;
  const SPLASH_FADE_MS = 400;

  function splashSetRow(check, state, detail) {
    const li = document.querySelector(
      '#splash-checks li[data-check="' + check + '"]'
    );
    if (!li) return;
    li.dataset.state = state;
    const icon = li.querySelector('.splash-check-icon');
    if (icon) {
      icon.textContent =
        state === 'ok' ? '✅' : state === 'fail' ? '❌' : '⏳';
    }
    const detailEl = li.querySelector('.splash-check-detail');
    if (detailEl && detail !== undefined && detail !== null) {
      detailEl.textContent = detail ? String(detail) : '';
    }
  }

  function splashSetStatus(text) {
    const el = document.getElementById('splash-status');
    if (el && text !== undefined) el.textContent = text;
  }

  function splashSetProgress(done, total) {
    splashSetStatus(
      t('splashDone', { n: String(done) }) || (done + ' / ' + total)
    );
  }

  function splashFadeOut(callback) {
    const overlay = document.getElementById('splash-overlay');
    if (!overlay) { if (callback) callback(); return; }
    overlay.classList.add('is-fading');
    setTimeout(function () {
      overlay.classList.add('is-hidden');
      if (callback) callback();
    }, SPLASH_FADE_MS);
  }

  /**
   * Returns a Promise that an external caller (the probe below)
   * resolves to mark the row ok/fail. Each promise self-expires
   * with a localised "timed out" fail after SPLASH_CHECK_TIMEOUT_MS
   * so a stuck probe can never wedge the overlay.
   */
  function runCheck(name) {
    return new Promise(function (resolve) {
      let settled = false;
      let tid = null;
      const finish = function (state, detail) {
        if (settled) return;
        settled = true;
        if (tid) clearTimeout(tid);
        splashSetRow(name, state, detail);
        resolve({ state: state, detail: detail });
      };
      // Backend = trivial (the overlay rendered = bridge alive).
      if (name === 'backend') {
        finish('ok', 'self');
        return;
      }
      tid = setTimeout(function () {
        finish('fail', t('readinessTimeout'));
      }, name === 'mcp'
        ? SPLASH_MCP_TIMEOUT_MS
        : SPLASH_CHECK_TIMEOUT_MS);
      window.__splashProbe__ = window.__splashProbe__ || {};
      window.__splashProbe__[name] = finish;
    });
  }

  /**
   * Runs the three readiness rows in parallel; resolves when
   * either all rows settle, the Skip button is clicked, or any
   * per-row timeout fires. The actual get_readiness roundtrip
   * is best-effort — if the backend call itself throws we still
   * fade the overlay and call boot(), so a missing API never
   * strands the GUI at the splash.
   */
  async function runReadinessChecks() {
    const overlay = document.getElementById('splash-overlay');
    if (!overlay) { boot(); return; }
    const skipBtn = document.getElementById('splash-skip');

    const skipPromise = new Promise(function (resolve) {
      if (!skipBtn) return;
      skipBtn.addEventListener(
        'click',
        function () {
          document
            .querySelectorAll(
              '#splash-checks li[data-state="pending"]'
            )
            .forEach(function (li) {
              splashSetRow(
                li.dataset.check, 'fail', t('splashSkipped')
              );
            });
          splashSetStatus(t('splashSkipped'));
          resolve('skipped');
        },
        { once: true }
      );
    });

    const checkPromises = [
      runCheck('backend'),
      runCheck('qdrant'),
      runCheck('llm'),
      runCheck('mcp'),
    ];

    const probePromise = (async function () {
      try {
        // Race the get_readiness call against a hard timeout so a
        // wedged bridge never strands the splash. The per-row
        // setTimeout in runCheck() handles the USER-facing
        // timeout; this one is an extra safety net for the
        // api.safeCall() promise itself, which can hang if the
        // bridge round-trip itself stalls (e.g. pywebview's
        // channel locked). v0.9.80.0+: sized for the MCP row
        // (its get_readiness call builds the whole fleet).
        const r = await Promise.race([
          api.safeCall('get_readiness'),
          new Promise(function (resolve) {
            setTimeout(function () {
              resolve({ error: 'probe_timeout' });
              // v0.9.80.1: the first response is FAST again (the mcp
              // row polls separately) — back to the short window.
            }, SPLASH_CHECK_TIMEOUT_MS + 1000);
          }),
        ]);
        if (!r || r.error || !r.checks || typeof r.checks !== 'object') {
          const err = (r && r.error) || 'get_readiness returned no checks';
          const msg = String(err).slice(0, 120);
          if (window.__splashProbe__ &&
              window.__splashProbe__['qdrant']) {
            window.__splashProbe__['qdrant']('fail', msg);
          }
          if (window.__splashProbe__ &&
              window.__splashProbe__['llm']) {
            window.__splashProbe__['llm']('fail', msg);
          }
          if (window.__splashProbe__ &&
              window.__splashProbe__['mcp']) {
            window.__splashProbe__['mcp']('fail', msg);
          }
          return;
        }
        const chk = r.checks;
        const probe = window.__splashProbe__ || {};
        // v0.9.87.4 — paint the splash version from the readiness
        // payload (pyproject single source of truth). The splash HTML
        // used to carry a hardcoded 'v0.8.6' fallback literal — the
        // exact v0.9.21.1 drift bug repeating on ``#splash-version``.
        const vEl = document.getElementById('splash-version');
        if (vEl && r.version) vEl.textContent = 'v' + r.version;
        if (chk.backend && probe['backend']) {
          probe['backend'](
            chk.backend.ok ? 'ok' : 'fail',
            chk.backend.detail
          );
        }
        if (chk.qdrant && probe['qdrant']) {
          probe['qdrant'](
            chk.qdrant.ok ? 'ok' : 'fail',
            chk.qdrant.detail
          );
        }
        if (chk.llm && probe['llm']) {
          probe['llm'](
            chk.llm.ok ? 'ok' : 'fail',
            chk.llm.detail
          );
        }
        // v0.9.80.1 — the mcp row POLLS (the backend reports
        // non-blocking prewarm progress; first response may carry
        // done:false while the fleet builds in the background).
        if (probe['mcp']) {
          const mcpFinish = probe['mcp'];
          const deadline = Date.now() + SPLASH_MCP_TIMEOUT_MS;
          const pollMcp = async function () {
            try {
              const r2 = await api.safeCall('get_readiness');
              const m = r2 && r2.checks && r2.checks.mcp;
              // done !== false → terminal (the backend always sets
              // done; an undefined done = legacy/missing field →
              // treat as terminal so the splash can never strand)
              if (m && m.done !== false) {
                mcpFinish(m.ok ? 'ok' : 'fail', m.detail);
                return;
              }
            } catch (e) { /* transient — keep polling */ }
            if (Date.now() < deadline) {
              setTimeout(pollMcp, 1500);
            } else {
              mcpFinish('fail', t('readinessTimeout'));
            }
          };
          if (chk.mcp && chk.mcp.done !== false) {
            mcpFinish(
              chk.mcp.ok ? 'ok' : 'fail',
              chk.mcp.detail
            );
          } else {
            // v0.9.87.3 — PENDING: keep the row ⏳ and poll; the
            // splash MUST NOT dismiss until the whole MCP fleet has
            // finished preparing (or the 90 s deadline / Skip).
            // (The duplicate immediate-finish that used to live here
            // resolved the row as 'fail' on the first pending
            // response and dismissed the splash while the fleet was
            // still building — exactly the bug this fixes.)
            setTimeout(pollMcp, 800);
          }
        }
      } catch (e) {
        const msg = ((e && e.message) || String(e)).slice(0, 120);
        const probe = window.__splashProbe__ || {};
        if (probe['qdrant']) probe['qdrant']('fail', msg);
        if (probe['llm']) probe['llm']('fail', msg);
        if (probe['mcp']) probe['mcp']('fail', msg);
      } finally {
        delete window.__splashProbe__;
      }
    })();

    Promise.race([Promise.all(checkPromises), skipPromise])
      .catch(function () {})
      .then(function () { return probePromise; })
      .then(function () {
        function allTerminal() {
          const rows = document.querySelectorAll('#splash-checks li');
          if (!rows.length) return true;
          for (let i = 0; i < rows.length; i++) {
            const s = rows[i].dataset.state;
            if (s !== 'ok' && s !== 'fail') return false;
          }
          return true;
        }
        let tries = 0;
        function waitOnce() {
          // v0.9.87.3 — 600×50 ms (30 s) was shorter than the mcp
          // row's own 90 s deadline: a slow fleet faded the splash
          // mid-build. 1800 tries = 90 s, aligned with
          // SPLASH_MCP_TIMEOUT_MS (the Skip button still bypasses).
          if (allTerminal() || tries++ > 1800) {
            const rows = document.querySelectorAll('#splash-checks li');
            const done = document.querySelectorAll(
              '#splash-checks li[data-state="ok"]'
            ).length;
            splashSetProgress(done, rows.length || 4);
            splashFadeOut(boot);
            return;
          }
          setTimeout(waitOnce, 50);
        }
        waitOnce();
      });
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', runReadinessChecks);
  } else {
    runReadinessChecks();
  }
})();
