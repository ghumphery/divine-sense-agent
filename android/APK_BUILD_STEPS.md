# APK 建置（v0.9.126.2 起：本機已可直建，不需 Android Studio）

## 本機一鍵建置（已驗證；SDK 已裝在 `.android-sdk/`）
```
python android\build_apk.py
```
→ **`android\dist\divine-sense-agent-launcher.apk`**（~25 KB，
versionCode=1／versionName=0.9.126.2、apksigner verify 通過）。

步驟全自動：aapt2 compile/link（含 AGP8 manifest 的 package 注入）→
javac → jar → d8（build-tools **35**；34.0.0 的 R8 8.2.2-dev 對本輸入
NPE）→ classes.dex 附加 → zipalign → 首次自動產 debug keystore
（`android/.debug.keystore`）→ apksigner sign+verify → badging 檢查。

SDK 組件（僅需一次，已完成）：`commandlinetools-win` 解壓到
`.android-sdk\cmdline-tools\latest` + `sdkmanager --licenses` +
`platform-tools`/`build-tools;34.0.0`/`build-tools;35.0.0`/
`platforms;android-34`。

## 安裝到 Android 平板
1. 把 APK 傳到平板（adb / 雲端 / 區網下載皆可）
2. 檔案管理員點開 → 允許「安裝未知來源」（自簽 debug key，非商店）
3. 開啟即 standalone WebView → **無任何瀏覽器 banner**
4. 預設載 `https://tchtool.app:8334/`（GoDaddy 可信憑證名稱——
   選單「**設定服務網址**」可即改即存）

## Android Studio 路線（備用）
wrapper 已改 **零依賴**（androidx/material 摘除、platform Material
theme）——`android/` 仍可直接 Open + Build APK(s)；兩條路線同一份源碼。

## 為什麼本專案還提供 PWA？
PWA（路 1）**不需要建置任何東西**——Android Firefox/Chrome 的「安裝」
即等於 standalone app（無 banner）。APK 的差別僅在：獨立於瀏覽器、
圖示/工作分頁完全 app 化、可在無「安裝 PWA」選項的舊機上使用。兩者
的**版面效果相同**。