# iPhone 分享進 Divine Sense — iOS 捷徑設定教學

> v0.9.127.5 起。

**背景**：iOS Safari **不支援** PWA `share_target`（Web Share Target API
只有 Android 上的 Chromium 系瀏覽器支援——Chrome / Edge / Samsung
Internet；Firefox 全平台、Safari 全平台都不支援）。所以：

| 平台 | 其他 App 分享進來 | 作法 |
|---|---|---|
| Android（Chrome/Edge/Samsung） | ✅ 原生 | 把 Web App 裝成 PWA 後自動出現在分享選單（`share_target` 已寫進 manifest） |
| **iPhone（iOS 全版本）** | ❌ 無 share_target | 用 iOS 內建「捷徑（Shortcuts）」直接打 HTTP API — 本教學 |

## 前提

- Web App 網址：`https://tchtool.app:8334/`
- server 端為 v0.9.127.5+（`/upload?share=json` 與 `/share-text` 已上線）。
  - 這兩個端點跟 `POST /upload` 一樣**無帳號驗證**（與 Web UI 同信任等
    級）；server 只開在自家 TLS 網址，請勿把它暴露到不可信網路。

## 捷徑 A：分享「文字 / 網址」

1. 「捷徑」App → 右上 **＋** 新增，命名如「存進 Divine Sense」。
2. 加動作 **「取得文字輸入」**（Get Text from Input）— 從分享選單進來
   的文字 / URL 會變成捷徑輸入。
3. 加動作 **「取得 URL 內容」**（Get Contents of URL）:
   - URL: `https://tchtool.app:8334/share-text`
   - Method: **POST**
   - Request Body: 類型選 **「表單」** → 加一個欄位:key = `share_text`、
     value 選「捷徑輸入 / Text」。
4. （可加）動作 **「顯示通知」** 內文 = 「已存:」+ 取得內容的回應。
   server 回應是 JSON `{"ok": true, "path": "C:/.../uploads/…-shared.txt"}`。
5. 捷徑設定 → 開 **「顯示在分享表格中」**（Show in Share Sheet），
   接收類型勾選「文字」與「URL」。

之後在 Safari / 備忘錄 / 任何 App 按分享 → 選這個捷徑 → 文字存進
`uploads/`；到 Divine Sense 把分享回執裡的路徑貼進對話（或直接在輸入
框引用），AI 就能讀到原文。

## 捷徑 B：分享「照片 / 檔案」

1. 同上建捷徑（命名「傳圖給 Divine Sense」）。
2. 加動作 **「取得 URL 內容」**:
   - URL: `https://tchtool.app:8334/upload?share=json`
   - Method: **POST**
   - Request Body: 類型 **「表單」** → 欄位 key = `files`、value 類型
     選 **檔案**、值 = 捷徑輸入（影像 / 檔案）。
3. 回應 JSON: `{"ok": true, "files": [{"path": …}, …]}`。加動作
   **「從輸入取得字典值」**（Get Dictionary Value）→ `files` →
   `path`（取 First）。
4. （完成迴路）動作 **「打開 URL」**:
   `https://tchtool.app:8334/?shared=` **＋** 上一步的 path —
   app 開啟後（Safari 分頁或已安裝的 PWA），分享的檔案會以
   `[已上傳]` 標記**自動進入輸入框**，直接補一句話送出即可。
5. 捷徑設定 → 開 **「顯示在分享表格中」**，接收類型勾「影像」
   （按需再加「檔案」）。

## 為什麼 iPhone 需要 `share=json`

分享選單把內容 POST 到 `share_target.action` 後，**瀏覽器會把回應整頁
顯示出來**——所以 Android 端 PWA 的 share POST 走
`?share=1`（server 回 303 轉回 app 頁面，路徑經 `?shared=` 帶入輸入框）。
但 iOS 捷徑不需要「導覽」、要的是**可解析的 JSON 回應** —
`?share=json` 正是這個形 (`{"ok", "files", "share": {…}}`)，
捷徑再以「打開 URL `/?shared=<path>`」完成最後一里。

## 已知限制

- iOS Safari / 分享選單**不會**把 Divine Sense 列成分享目標（平台限制，
  等 WebKit 支援 share_target 才有救）；捷徑是現行唯一通路。
- `share-url` 無自訂 scheme 深連結；捷徑「打開 URL」在 Safari 分頁開啟
  app（已安裝 PWA 也不保證進 standalone 窗——iOS 不把任意 https 連結
  路由進安裝的 PWA）。