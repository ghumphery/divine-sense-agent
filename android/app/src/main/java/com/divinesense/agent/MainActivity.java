package com.divinesense.agent;

import android.annotation.SuppressLint;
import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.SharedPreferences;
import android.os.Bundle;
import android.view.KeyEvent;
import android.view.Menu;
import android.view.MenuItem;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.widget.EditText;
import android.widget.Toast;

/**
 * v0.9.125.9 / v0.9.126.2 — Divine Sense Agent Android launcher.
 *
 * A minimal WebView wrapper that loads the agent's web UI in a
 * STANDALONE window — no browser URL bar / banner.
 *
 * ZERO-DEPENDENCY on purpose (v0.9.126.2): the class uses only the
 * platform android.* surface (android.app.Activity + the platform
 * Material theme) so the APK can be assembled BOTH by this repo's
 * ``android/build_apk.py`` (aapt2/javac/d8/zipalign/apksigner, no
 * gradle) AND by Android Studio; androidx/material deps removed from
 * app/build.gradle accordingly.
 *
 * Security posture mirrors the web server's own:
 * - INTERNET is the ONLY permission;
 * - no file access, no content access, no geolocation;
 * - only the configured host is navigated (same-window navigation,
 *   external links blocked);
 * - the address can be changed from the menu and is persisted
 *   locally (SharedPreferences) — the default points at the dev box,
 *   the certified name https://tchtool.app:8334/ being the URL the
 *   TLS certificate (GoDaddy, SAN=tchtool.app) actually covers.
 */
public class MainActivity extends Activity {

    /** The agent web server — change the IP when the host changes. */
    private static final String DEFAULT_START_URL =
            "https://tchtool.app:8334/";

    private static final String PREFS = "dsagent";
    private static final String KEY_URL = "start_url";

    private WebView web;
    private String startUrl = DEFAULT_START_URL;

    @SuppressLint("SetJavaScriptEnabled")
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        SharedPreferences sp = getSharedPreferences(PREFS,
                Context.MODE_PRIVATE);
        startUrl = sp.getString(KEY_URL, DEFAULT_START_URL);

        web = new WebView(this);
        setContentView(web);

        WebSettings s = web.getSettings();
        s.setJavaScriptEnabled(true);           // the UI is a JS app
        s.setDomStorageEnabled(true);           // offline notes/pending FIFO
        s.setDatabaseEnabled(true);
        s.setAllowFileAccess(false);            // no local file:// reads
        s.setAllowContentAccess(false);
        s.setMediaPlaybackRequiresUserGesture(false); // mic/TTS flows
        s.setLoadWithOverviewMode(true);
        s.setUseWideViewPort(true);
        s.setSupportZoom(false);
        s.setGeolocationEnabled(false);
        s.setCacheMode(WebSettings.LOAD_DEFAULT);

        // same-window navigation only; never follow external links
        web.setWebViewClient(new WebViewClient() {
            @Override
            public boolean shouldOverrideUrlLoading(
                    WebView v, WebResourceRequest r) {
                String u = String.valueOf(r.getUrl());
                return !u.startsWith(startUrl)
                        && !u.startsWith(hostPrefix());
            }
        });

        if (savedInstanceState == null) {
            web.loadUrl(startUrl);
        } else {
            web.restoreState(savedInstanceState);
        }
    }

    /** The scheme://host:port prefix of the current start URL. */
    private String hostPrefix() {
        int schemeEnd = startUrl.indexOf("://");
        if (schemeEnd < 0) return startUrl;
        int pathStart = startUrl.indexOf('/', schemeEnd + 3);
        return pathStart < 0 ? startUrl
                : startUrl.substring(0, pathStart + 1);
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        web.saveState(outState);
    }

    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        // back = WebView history back (app-like), not instant-close
        if (keyCode == KeyEvent.KEYCODE_BACK && web != null
                && web.canGoBack()) {
            web.goBack();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    @Override
    public boolean onCreateOptionsMenu(Menu menu) {
        menu.add("設定服務網址");
        return super.onCreateOptionsMenu(menu);
    }

    @Override
    public boolean onOptionsItemSelected(MenuItem item) {
        if (item.getTitle() != null
                && "設定服務網址".contentEquals(item.getTitle())) {
            promptForUrl();
            return true;
        }
        return super.onOptionsItemSelected(item);
    }

    /** Menu action: change + persist the service URL. */
    private void promptForUrl() {
        final EditText input = new EditText(this);
        input.setText(startUrl);
        new AlertDialog.Builder(this)
                .setTitle("服務網址")
                .setView(input)
                .setPositiveButton("前往", (d, w) -> {
                    String u = String.valueOf(input.getText()).trim();
                    if (!u.startsWith("http://")
                            && !u.startsWith("https://")) {
                        u = "https://" + u;
                    }
                    if (!u.endsWith("/")) u = u + "/";
                    startUrl = u;
                    getSharedPreferences(PREFS, Context.MODE_PRIVATE)
                            .edit().putString(KEY_URL, startUrl).apply();
                    web.loadUrl(startUrl);
                    Toast.makeText(this, "已載入 " + startUrl,
                            Toast.LENGTH_SHORT).show();
                })
                .setNegativeButton("取消", null)
                .show();
    }
}