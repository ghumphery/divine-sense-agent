# Android 檔案夾 — WebView wrapper APK 專案（v0.9.125.9）

**目的**：在 Android 平板/手機上以一個**無網址列 banner** 的獨立 app 視窗
啟動本服務網頁（用 Firefox 開 `http://<主機>:8334` 會有 Firefox 上方
banner 吃掉版面）。

## 兩條路（內容相同效果相同，擇一即可）

### 路 1 — PWA 安裝（推薦、零建置；v0.9.125.9 內建、v0.9.126.1 起 HTTPS 就緒）

**server 已設定可信 HTTPS（.env：kidmen-web 的 GoDaddy 憑證，
SAN=tchtool.app；`QDRANT_WEB_TLS_NAME=tchtool.app`）——請一律以
「憑證名稱」訪問，不要用 IP（IP 不在 SAN 內會出警告）**：

1. Android 上用 **Firefox 或 Chrome** 開 `https://tchtool.app:8334/`
   （**https** —— GoDaddy 可信憑證、無任何警告）
2. 選單 →「**安裝**」（Firefox）/「**安裝應用程式**」（Chrome）
   ← **secure context 滿足、不會再「無法安裝」**
3. 從桌面圖示啟動 → **standalone 全螢幕，無任何瀏覽器 banner**、
   獨立工作分頁、自己的 app 圖示

> server 端備妥：`/manifest.webmanifest`（display: standalone）＋
> `/sw.js`（NETWORK-ONLY）＋ icon-192/512＋啟動 banner 印
> `certified as: https://tchtool.app:8334/`（安裝網址以它為準）。

### 路 2 — 真 APK（本目錄源碼；需要 Android Studio / SDK 本機建置）
本機（Windows）沒有 Android SDK，**無法在此直接出 .apk**。
本資料夾是一個**最小、可直接在 Android Studio 開啟建置**的 WebView
wrapper 專案：

```
android/
├── settings.gradle
├── build.gradle                     # root（AGP 版本集中宣告）
├── gradle.properties
├── app/
│   ├── build.gradle                 # minSdk 24 / targetSdk 34
│   ├── proguard-rules.pro
│   └── src/main/
│       ├── AndroidManifest.xml      # INTERNET 權限，無其他權限
│       ├── res/values/strings.xml   # app 名稱
│       ├── res/drawable/launch_screen.xml  # 啟動純色畫面（不用 icon 也行）
│       ├── res/mipmap-anydpi/…      # （直接複製 gui/assets/icon-*.png 到 res/mipmap-*)
│       └── java/.../MainActivity.java
└── APK_BUILD_STEPS.md               # 三步建置說明
```

**預設載入位址**：`MainActivity.java` 頂部
`DEFAULT_START_URL = "http://192.168.50.154:8334/"`（改主機 IP 時
同改此處；app 內也提供選單換址持久化，見該檔註解）。

## 安全/權限
- WebView 只載該 http 位址；不開檔案存取（`setAllowFileAccess(false)`）、
  不開外部連結（同窗導航）、back 鍵=WebView 歷史返回。
- 唯一權限：`INTERNET`。