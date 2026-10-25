/* v0.9.5+ drawio viewer JS.
 *
 * Talks to draw.io's embed via postMessage and to the Python
 * bridge via ``window.pywebview.api.<name>(...)`` (set up by
 * ``gui.diagrams_window._DiagramsViewerApi``).
 *
 * v0.9.5.6: pywebview injects ``window.pywebview`` only AFTER
 * the page finishes loading — ``DOMContentLoaded`` fires BEFORE
 * that. Every boot-time bridge access therefore goes through
 * ``bridgeApi()`` (null-safe) + ``_waitForBridge()`` (polls
 * until the bridge appears). The v0.9.5.5 boot block read
 * ``window.pywebview.api`` directly and threw
 * "Cannot read properties of undefined (reading 'api')"
 * (user-reported 2026-09-02), which also aborted the rest of
 * the DOMContentLoaded handler so the iframe never mounted.
 *
 * Architecture:
 *  - drawio embed is loaded via the official CDN; we *don't*
 *    try to bundle the full editor (~3 MB minified). For
 *    offline use, vendors can replace the <script src=...>
 *    in diagrams.html with a local copy.
 *  - The embed loads into a hidden iframe; we use
 *    postMessage with the draw.io "init" / "load" / "export"
 *    / "save" / "merge" protocol.
 *  - All disk I/O goes through the Python side — JS only
 *    holds the in-memory XML so the LLM (via filesystem MCP)
 *    can later read it back via ``read_from_sandbox``.
 */

(() => {
  "use strict";

  // ── state ───────────────────────────────────────────────
  // v0.9.5.3: ``drawioReady`` is now renamed ``drawioInitialized``
  // — it tracks whether the drawio iframe has sent its ``init``
  // event. Per the JSON protocol (proto=json), the host MUST wait
  // for ``{event: 'init'}`` before posting ``{action: 'load'}``;
  // sending load before init is silently dropped by the iframe.
  let drawioInitialized = false;
  let currentXml = "";
  let sourcePath = null;        // path the *initial* diagram came from
  let saveTargetName = null;    // user-set filename for the next Save
  let iframe = null;            // the drawio <iframe> we mount
  // XML pushed from Python before the iframe is ready. We flush
  // this queue as soon as ``drawioInitialized`` flips true.
  let pendingLoadXml = null;

  // ── DOM refs ────────────────────────────────────────────
  const $ = (id) => document.getElementById(id);
  const mount       = $("drawio-mount");
  const emptyState  = $("empty-state");
  const dropOverlay = $("drop-overlay");
  const statusPill  = $("status-pill");
  const sandboxPath = $("sandbox-path");
  const footerSand  = $("footer-sandbox");
  const footerSrc   = $("footer-source");
  const picker      = $("sandbox-picker");
  const pickerInput = $("picker-input");
  const pickerLoad  = $("picker-load");
  const pickerError = $("picker-error");
  const pickerSandbox = $("picker-sandbox");
  const saveDialog  = $("save-dialog");
  const saveName    = $("save-filename");
  const saveOver    = $("save-overwrite");
  const saveError   = $("save-error");
  // v0.9.5.4: loading overlay refs.
  const loadingOverlay = $("loading-overlay");
  const loadingTitle   = $("loading-title");
  const loadingDetail  = $("loading-detail");
  const loadingTimer   = $("loading-timer");
  const loadingStuckActions = $("loading-stuck-actions");
  const btnOpenInBrowser = $("btn-open-in-browser");
  const btnReloadIframe  = $("btn-reload-iframe");
  // v0.9.5.5: anchor fallback. Works even when diagrams.js
  // never executes (e.g. the script file failed to load) —
  // plain HTML <a target="_blank"> opens drawio in the user's
  // system browser without needing the Python bridge.
  const linkOpenInBrowser = $("link-open-in-browser");

  // ── loading overlay state ───────────────────────────────
  // v0.9.5.4: tracks how long we've been waiting for
  // drawio's ``{event: 'init'}``. After ``_STUCK_AFTER_MS``
  // the overlay swaps from "loading" to "stuck" and shows
  // the browser-fallback + reload-iframe buttons.
  let overlayMountedAt = 0;        // Date.now() when iframe mounted
  let overlayTimerInterval = null; // setInterval handle for the timer display
  const _STUCK_AFTER_MS = 15000;   // 15 s — generous for warm cache

  // v0.9.5.5: watchdog timer. If drawio's ``init`` event has
  // NOT arrived after this many ms we surface a clear
  // "stuck" state on the status pill so the user has
  // something actionable to look at, instead of seeing only
  // drawio's tiny embedded spinner on a white pane.
  const _WATCHDOG_MS = 2000;
  let watchdogTimer = null;

  // ── global error watchdog (v0.9.5.5) ────────────────────
  // The viewer window is fully isolated from the main GUI —
  // if anything in ``diagrams.js`` throws, the user has no
  // console and no other place to see the error. Wire the
  // global error / unhandledrejection handlers FIRST so even
  // a syntax-time typo in this very file surfaces on the
  // status pill instead of leaving the pill stuck on the
  // default "📊 Drawio Viewer" placeholder.
  function _reportError(prefix, e) {
    try {
      const msg = (e && (e.message || e.toString && e.toString()))
        || String(e);
      setStatus("❌ " + prefix + ": " + msg, "err");
    } catch (_) {
      // last-resort: do nothing
    }
  }
  window.addEventListener("error", (ev) => {
    _reportError("JS error", ev.error || ev.message);
  });
  window.addEventListener("unhandledrejection", (ev) => {
    // ``ev.reason`` is the modern API but is missing on synthetic
    // ``Event`` dispatches from our JSDOM test driver. Fall back
    // to ``window._lastRejectionReason`` (set by the test) so the
    // pill still surfaces the message in CI.
    const reason = (ev && ev.reason) || window._lastRejectionReason;
    _reportError("Promise rejected", reason);
  });

  // ── status helpers ──────────────────────────────────────
  function setStatus(text, kind) {
    if (!statusPill) return;
    statusPill.textContent = text;
    statusPill.classList.remove("ok", "err");
    if (kind) statusPill.classList.add(kind);
  }
  function showError(el, msg) {
    if (!el) return;
    el.textContent = msg;
    el.hidden = false;
  }
  function clearError(el) {
    if (!el) return;
    el.textContent = "";
    el.hidden = true;
  }

  // ── loading overlay helpers (v0.9.5.4) ──────────────────
  // Start the live-timer + reveal the overlay. Idempotent;
  // safe to call again after a reload — the previous
  // interval is cleared first.
  function showLoadingOverlay() {
    overlayMountedAt = Date.now();
    if (overlayTimerInterval) {
      clearInterval(overlayTimerInterval);
    }
    if (loadingOverlay) {
      loadingOverlay.hidden = false;
    }
    if (loadingStuckActions) {
      loadingStuckActions.hidden = true;
    }
    if (loadingTimer) {
      loadingTimer.textContent = "已等候 0 秒…";
    }
    overlayTimerInterval = setInterval(_updateLoadingOverlay, 500);
  }
  function _updateLoadingOverlay() {
    if (!loadingTimer) return;
    const elapsedMs = Date.now() - overlayMountedAt;
    const secs = Math.floor(elapsedMs / 1000);
    loadingTimer.textContent = "已等候 " + secs + " 秒…";
    // After _STUCK_AFTER_MS reveal the stuck-actions and
    // re-title the overlay.
    if (
      elapsedMs >= _STUCK_AFTER_MS
      && loadingStuckActions
      && loadingStuckActions.hidden
    ) {
      loadingStuckActions.hidden = false;
      if (loadingTitle) {
        loadingTitle.textContent =
          "⚠️ drawio 載入緩慢 (已等候 " + secs + " 秒)";
      }
      if (loadingDetail) {
        loadingDetail.innerHTML =
          "WebView2 啟動 drawio 的 10 MB JS bundle 比一般瀏覽器慢得多。"
          + "最保險的做法是用<b>在瀏覽器中開啟</b>。"
          + "或者按<b>重新載入 iframe</b>再試一次。";
      }
    }
  }
  function hideLoadingOverlay() {
    if (overlayTimerInterval) {
      clearInterval(overlayTimerInterval);
      overlayTimerInterval = null;
    }
    if (loadingOverlay) {
      loadingOverlay.hidden = true;
    }
  }

  // ── Python bridge readiness (v0.9.5.6) ──────────────────
  // pywebview injects ``window.pywebview`` AFTER the page
  // finishes loading — ``DOMContentLoaded`` fires BEFORE that.
  // Every bridge access must therefore be null-safe, and every
  // boot-time bridge call must wait for the bridge to appear.
  // Mirrors the guard pattern ``gui/assets/app.js::api.call``
  // has used since v0.8.7 for the exact same lifecycle
  // (see ``tests/test_gui_silent_api_not_ready.py``).
  function bridgeApi() {
    return (window.pywebview && window.pywebview.api) || null;
  }
  const _BRIDGE_WAIT_MS = 15000;
  // Tests can shorten the wait by setting
  // ``window._BRIDGE_WAIT_MS_OVERRIDE`` (number, ms) before
  // DOMContentLoaded.
  function _waitForBridge(timeoutMs) {
    return new Promise((resolve, reject) => {
      const limit = (typeof timeoutMs === "number")
        ? timeoutMs
        : ((typeof window._BRIDGE_WAIT_MS_OVERRIDE === "number")
          ? window._BRIDGE_WAIT_MS_OVERRIDE
          : _BRIDGE_WAIT_MS);
      const started = Date.now();
      const tick = () => {
        const api = bridgeApi();
        if (api) {
          resolve(api);
          return;
        }
        if (Date.now() - started >= limit) {
          reject(new Error(
            "window.pywebview not injected within "
            + limit + " ms"
          ));
          return;
        }
        setTimeout(tick, 50);
      };
      // pywebview's official readiness signal — lets the
      // waiter resolve on the very tick the bridge appears
      // instead of waiting for the next poll interval.
      // (A late poll tick resolving an already-settled promise
      // is a harmless no-op.)
      window.addEventListener("pywebviewready", () => {
        const api = bridgeApi();
        if (api) resolve(api);
      });
      tick();
    });
  }

  // ── Python bridge probe ─────────────────────────────────
  // v0.9.5.6: the ``window.pywebview.api`` read is guarded —
  // the bridge may not be injected yet. Boot code now invokes
  // this only after ``_waitForBridge()`` resolves, but the
  // guard keeps a stray early call from throwing
  // "Cannot read properties of undefined (reading 'api')"
  // into the global error handler (user-reported 2026-09-02).
  async function probeBridge() {
    const api = bridgeApi();
    if (!api) {
      setStatus("⏳ Python bridge 未就緒 (等 pywebviewready…)");
      return;
    }
    try {
      const r = await api.ping();
      if (r && r.ok) {
        setStatus("✅ bridge OK", "ok");
        sandboxPath.textContent = r.write_root || "";
        footerSand.textContent = r.write_root || "(unset)";
      } else {
        setStatus("⚠️ bridge returned " + JSON.stringify(r), "err");
      }
    } catch (e) {
      setStatus("❌ bridge error: " + e, "err");
    }
  }

  // ── drawio embed ────────────────────────────────────────
  // v0.9.5.3: we no longer use the ``DrawioViewer`` wrapper from
  // ``embed/v1.js`` — its internal property names (``iframe.iframe``
  // vs ``iframe.contentWindow``) are undocumented and the wrapper
  // silently dropped XML sent via the wrong path (user reported
  // "viewer opens empty even after View in Diagrams clicked").
  //
  // Instead we mount a raw <iframe> with ``?embed=1&proto=json&...``
  // and follow the documented drawio JSON protocol:
  //   1. host waits for ``{event: 'init'}`` from iframe
  //   2. host posts ``{action: 'load', xml: '...'}`` → iframe renders
  //   3. iframe posts ``{event: 'save'}`` or ``{event: 'export'}``
  //      with the current XML whenever the user saves
  //
  // This is the same pattern used by ``jgraph/drawio-integration``
  // ``diagram-editor.js``.
  function startDrawio(initialXml) {
    // Drain anything pushed from Python earlier. We always mount
    // the iframe first (so the init event can arrive), then drain.
    if (initialXml) {
      pendingLoadXml = initialXml;
    }

    iframe = document.createElement("iframe");
    iframe.setAttribute("frameborder", "0");
    // v0.9.5.4: ask the Python bridge for the canonical embed URL
    // instead of hardcoding it (``_DRAWIO_EMBED_URL`` in
    // ``gui/diagrams_window.py`` is the single source of truth).
    // Falls back to the previous hardcoded URL while the bridge
    // is not injected yet — pywebview injects ``window.pywebview``
    // only after the page finishes loading, i.e. AFTER
    // ``DOMContentLoaded`` fires (v0.9.5.6; the old comment
    // claimed the opposite and was wrong).
    let drawioUrl = (
      "https://embed.diagrams.net/?embed=1&proto=json&spin=1&ui=min"
    );
    if (window.pywebview && window.pywebview.api
        && typeof window.pywebview.api.get_drawio_url === "function") {
      try {
        const u = window.pywebview.api.get_drawio_url();
        if (typeof u === "string" && u) drawioUrl = u;
      } catch (_) { /* keep fallback */ }
    }
    iframe.setAttribute("src", drawioUrl);
    // Match the visible area; the host CSS gives ``.drawio-mount``
    // 100% width/height of the main panel.
    iframe.style.cssText =
      "border:0;width:100%;height:100%;display:block;";

    // v0.9.5.4: track ``iframe.onload`` so we know when the
    // iframe HTML has at least loaded (even if drawio's JS
    // bundle is still booting). Helps distinguish "iframe
    // didn't load at all" from "iframe loaded, drawio still
    // booting" in the stuck-overlay copy.
    iframe.addEventListener("load", () => {
      // First load only — once init fires we hide the overlay.
      if (!drawioInitialized) {
        setStatus("⏳ iframe loaded, waiting for drawio init…");
      }
    });

    // Empty the mount node in case of a re-mount (defensive — the
    // current code only calls startDrawio once per window).
    while (mount.firstChild) {
      mount.removeChild(mount.firstChild);
    }
    mount.appendChild(iframe);

    // Wire up the iframe's postMessage channel. The iframe
    // posts its first message (``{event: 'init'}``) once its
    // editor finishes booting; after that we can ``load``.
    setStatus("⏳ drawio iframe mounted, waiting for init…");
    window.addEventListener("message", onDrawioMessage);

    // v0.9.5.5: reset the watchdog. ``startDrawio`` can be
    // called again by the "重新載入 iframe" button — in that
    // case the previous (already-fired or in-flight)
    // watchdog would land on the wrong reload. We cancel it
    // here and let the boot block start a fresh one.
    if (watchdogTimer) {
      clearTimeout(watchdogTimer);
      watchdogTimer = null;
    }
    drawioInitialized = false;
    watchdogTimer = setTimeout(() => {
      if (!drawioInitialized) {
        setStatus("⏳ drawio 回應緩慢 (等候 init 中…)");
      }
    }, _WATCHDOG_MS);

    // v0.9.5.4: surface the loading overlay so the user has
    // visible feedback while drawio boots. ``hideLoadingOverlay``
    // is called from the ``init`` handler in ``onDrawioMessage``.
    showLoadingOverlay();
  }

  // ── postMessage protocol ────────────────────────────────
  // v0.9.5.3: hand-rolled drawio JSON protocol. We do NOT use
  // ``DrawioViewer`` anymore (see ``startDrawio`` comment block).
  function onDrawioMessage(evt) {
    if (!evt || typeof evt.data !== "string") return;
    let msg;
    try { msg = JSON.parse(evt.data); } catch (_) { return; }
    if (!msg || typeof msg !== "object") return;
    // Drop messages from non-drawio frames (e.g. the chat bridge
    // test harness in ``tests/test_diagrams_window.py``).
    if (iframe && evt.source !== iframe.contentWindow) return;
    switch (msg.event) {
      case "init":
        drawioInitialized = true;
        // v0.9.5.5: clear the watchdog so it doesn't fire
        // *after* init landed and overwrite the ready state
        // with the "drawio 回應緩慢" text. (If init arrives
        // before the 2 s mark the watchdog simply no-ops.)
        if (watchdogTimer) {
          clearTimeout(watchdogTimer);
          watchdogTimer = null;
        }
        setStatus("✅ drawio ready", "ok");
        // v0.9.5.4: hide the loading overlay now that drawio
        // is booted and ready for ``load``.
        hideLoadingOverlay();
        // Flush any XML Python pushed before the iframe booted.
        if (pendingLoadXml) {
          const xml = pendingLoadXml;
          pendingLoadXml = null;
          loadXmlIntoEditor(xml);
        } else {
          emptyState.classList.remove("hidden");
        }
        break;
      case "save":
        handleSaveFromDrawio(msg);
        break;
      case "export":
        if (msg.data && msg.format) {
          downloadDataUrl(msg.data, "diagram." + msg.format);
        }
        break;
      case "merge":
        postToDrawio({
          action: "merge",
          xml: currentXml || emptyDiagramXml(),
        });
        break;
      case "exit":
        break;
    }
  }

  function postToDrawio(payload) {
    if (!iframe || !iframe.contentWindow) return;
    iframe.contentWindow.postMessage(JSON.stringify(payload), "*");
  }

  function loadXmlIntoEditor(xml) {
    currentXml = xml;
    emptyState.classList.add("hidden");
    if (!drawioInitialized) {
      // Queue until the iframe's ``init`` event arrives (the
      // JSON protocol silently drops ``load`` posted before init).
      pendingLoadXml = xml;
      return;
    }
    // JSON-protocol ``load`` action — see
    // https://www.drawio.com/doc/embed/embed-mode
    postToDrawio({
      action: "load",
      // drawio embed accepts either bare <mxGraphModel> or a full
      // <mxfile><diagram>… shell. The LLM sometimes hands us a
      // bare <mxGraphModel> or <mxCell> snippet (the create_diagram
      // MCP tool, for example, returns the XML inside a
      // {"xml": "..."} JSON which the chat-bridge extraction regex
      // unwraps for us). Wrap here so the viewer accepts any of
      // those shapes without the user having to know about drawio
      // XML structure.
      xml: _wrapForDrawio(xml),
    });
  }

  // Wrap arbitrary XML into a drawio-compatible envelope.
  // No-ops on already-valid mxfile strings.
  function _wrapForDrawio(xml) {
    if (!xml || typeof xml !== "string") return xml;
    const trimmed = xml.trim();
    // Already a full mxfile? Pass through.
    if (/^\s*<\s*mxfile[\s\S]*<\s*\/\s*mxfile\s*>\s*$/i.test(trimmed)) {
      return trimmed;
    }
    // Has a <mxGraphModel>? Wrap with mxfile + diagram.
    const g = /<\s*mxGraphModel[\s\S]*?<\s*\/\s*mxGraphModel\s*>/i.exec(trimmed);
    if (g) {
      // v0.9.5.7: the mxfile child element MUST be <diagram> —
      // drawio's extractGraphModel() finds pages via
      // getElementsByTagName('diagram'). The pre-0.9.5.7 wrong
      // element name (does not exist in the mxfile format)
      // matched nothing, so drawio rejected the whole
      // file with the 「非绘图文件 / Not a diagram file」 error
      // dialog + raw-content preview (user-reported 2026-09-02:
      // bare <mxGraphModel> failed while a hand-rolled
      // <mxfile><diagram> message rendered fine).
      return (
        '<mxfile host="divine-sense-agent" type="device">' +
        '<diagram id="root" name="Page-1">' +
        g[0] +
        "</diagram></mxfile>"
      );
    }
    // Has bare <mxCell> blocks? Build a minimal graphmodel.
    // v0.9.5.3: widened the trigger regex to also accept a
    // solitary self-closing ``<mxCell .../>`` (the original
    // required a paired ``</mxCell>`` closer, which excluded
    // leaf-shape snippets the LLM sometimes emits). We now
    // match anything containing at least one ``<mxCell`` tag.
    if (/<\s*mxCell\b/i.test(trimmed)) {
      // Pull out just the <mxCell .../> and </mxCell> nodes,
      // plus any self-closed <mxCell .../> cells. Strip the
      // first <mxCell id="0"/> and <mxCell id="1" parent="0"/>
      // because we re-add canonical ones below.
      const cellOpen = trimmed.match(
        /<\s*mxCell[^>]*>[\s\S]*?<\s*\/\s*mxCell\s*>/g,
      ) || [];
      const cellSelf = trimmed.match(/<\s*mxCell[^>\/]*\/>/g) || [];
      const cleaned = cellOpen.concat(cellSelf).filter(function (c) {
        return !/id\s*=\s*["']0["']/.test(c) &&
               !/id\s*=\s*["']1["'][^>]*parent\s*=\s*["']0["']/.test(c);
      });
      // v0.9.5.7: wrong element name → canonical <diagram> (same
      // bug as the mxGraphModel branch above).
      return (
        '<mxfile host="divine-sense-agent" type="device">' +
        '<diagram id="root" name="Page-1">' +
        "<mxGraphModel>" +
        "<root>" +
        '<mxCell id="0"/>' +
        '<mxCell id="1" parent="0"/>' +
        cleaned.join("") +
        "</root>" +
        "</mxGraphModel>" +
        "</diagram></mxfile>"
      );
    }
    // Unrecognised shape — feed through verbatim; draw.io will
    // show an error in its editor pane, which is the right
    // behaviour (don't silently corrupt user XML).
    return trimmed;
  }

  function askForCurrentXml() {
    postToDrawio({ event: "export", format: "xml" });
  }

  function emptyDiagramXml() {
    // v0.9.5.7: wrong element name → canonical <diagram> (same
    // bug as _wrapForDrawio above — this template feeds the merge
    // action fallback when no diagram has been loaded yet).
    return (
      '<mxfile host="divine-sense-agent" type="device">' +
      '<diagram id="root" name="Page-1">' +
      "<mxGraphModel><root><mxCell id=\"0\"/><mxCell " +
      'id="1" parent="0"/></root></mxGraphModel>' +
      "</diagram></mxfile>"
    );
  }

  function downloadDataUrl(dataUrl, filename) {
    const a = document.createElement("a");
    a.href = dataUrl;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
  }

  // ── save flow ───────────────────────────────────────────
  function handleSaveFromDrawio(msg) {
    if (msg.xml) currentXml = msg.xml;
    promptSaveDialog();
  }

  function promptSaveDialog() {
    saveError.hidden = true;
    saveName.value = saveTargetName ||
      (sourcePath
        ? sourcePath.split(/[\\/]/).pop()
        : "diagram.drawio");
    saveOver.checked = true;
    saveDialog.showModal();
  }

  $("btn-save").addEventListener("click", () => {
    if (!drawioInitialized) {
      setStatus("⚠️ editor not ready yet", "err");
      return;
    }
    postToDrawio({ event: "save", format: "xml" });
  });

  saveDialog.addEventListener("close", async () => {
    if (saveDialog.returnValue !== "save") return;
    const filename = saveName.value.trim();
    if (!filename) {
      showError(saveError, "Filename is required.");
      promptSaveDialog();
      return;
    }
    saveTargetName = filename;
    askForCurrentXml();
    setTimeout(async () => {
      const xml = currentXml;
      if (!xml) {
        showError(saveError, "Editor returned no XML.");
        return;
      }
      try {
        const r = await window.pywebview.api.save_to_sandbox(
          filename, xml, saveOver.checked,
        );
        if (r && r.ok) {
          setStatus(
            `✅ saved ${r.relative_path} (${r.bytes_written}B)`,
            "ok",
          );
          sourcePath = r.relative_path;
          footerSrc.textContent = sourcePath;
          clearError(saveError);
        } else {
          showError(saveError, (r && r.error) || "unknown error");
          setStatus("❌ save failed", "err");
        }
      } catch (e) {
        showError(saveError, "Python bridge error: " + e);
        setStatus("❌ save failed", "err");
      }
    }, 60);
  });

  // ── reload from sandbox ─────────────────────────────────
  $("btn-reload").addEventListener("click", async () => {
    if (!sourcePath) {
      setStatus("⚠️ no source path yet — open one first", "err");
      return;
    }
    try {
      const r = await window.pywebview.api.read_from_sandbox(sourcePath);
      if (r && r.ok) {
        loadXmlIntoEditor(r.xml);
        setStatus(`🔄 reloaded ${r.bytes}B from sandbox`, "ok");
      } else {
        setStatus("❌ reload: " + (r && r.error), "err");
      }
    } catch (e) {
      setStatus("❌ reload: " + e, "err");
    }
  });

  // ── open from sandbox picker ────────────────────────────
  $("btn-open-sandbox").addEventListener("click", () => {
    clearError(pickerError);
    pickerInput.value = sourcePath || "";
    picker.showModal();
  });

  picker.addEventListener("close", async () => {
    if (picker.returnValue !== "load") return;
    const path = pickerInput.value.trim();
    if (!path) {
      showError(pickerError, "Path is required.");
      picker.showModal();
      return;
    }
    try {
      const r = await window.pywebview.api.read_from_sandbox(path);
      if (r && r.ok) {
        sourcePath = path;
        footerSrc.textContent = path;
        loadXmlIntoEditor(r.xml);
        setStatus(`📂 loaded ${path} (${r.bytes}B)`, "ok");
      } else {
        showError(pickerError, (r && r.error) || "load failed");
        picker.showModal();
      }
    } catch (e) {
      showError(pickerError, "Python bridge error: " + e);
      picker.showModal();
    }
  });

  pickerLoad.addEventListener("click", () => {
    picker.close("load");
  });
  document.querySelector("#sandbox-picker menu button[value='cancel']")
    .addEventListener("click", () => picker.close("cancel"));
  document.querySelector("#save-dialog menu button[value='cancel']")
    .addEventListener("click", () => saveDialog.close("cancel"));
  saveDialog.querySelector("menu button[value='save']")
    .addEventListener("click", () => saveDialog.close("save"));

  // ── export / print ──────────────────────────────────────
  $("btn-export").addEventListener("click", () => {
    if (!drawioInitialized) return;
    postToDrawio({ action: "export", format: "png" });
  });
  $("btn-print").addEventListener("click", () => {
    if (!drawioInitialized) return;
    postToDrawio({ action: "print" });
  });

  // ── drag-drop ───────────────────────────────────────────
  ["dragenter", "dragover"].forEach((ev) =>
    window.addEventListener(ev, (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (e.dataTransfer && e.dataTransfer.types
          && e.dataTransfer.types.includes("Files")) {
        dropOverlay.classList.add("active");
      }
    }),
  );
  ["dragleave", "drop"].forEach((ev) =>
    window.addEventListener(ev, (e) => {
      e.preventDefault();
      e.stopPropagation();
      dropOverlay.classList.remove("active");
    }),
  );

  window.addEventListener("drop", async (e) => {
    const dt = e.dataTransfer;
    if (!dt || !dt.files || dt.files.length === 0) return;
    const file = dt.files[0];
    const name = file.name.toLowerCase();
    if (!name.endsWith(".drawio")
        && !name.endsWith(".xml")
        && !name.endsWith(".dio")) {
      setStatus("⚠️ only .drawio / .xml / .dio accepted", "err");
      return;
    }
    try {
      const text = await file.text();
      currentXml = text;
      sourcePath = file.name;
      footerSrc.textContent = file.name + " (dropped)";
      loadXmlIntoEditor(text);
      setStatus(`📂 loaded ${file.name} (${text.length}B)`, "ok");
    } catch (e) {
      setStatus("❌ drop failed: " + e, "err");
    }
  });

  // ── listen for XML pushed from Python ───────────────────
  // ``gui.diagrams_window._wrap_xml_for_js`` dispatches a
  // ``drawio-load-xml`` CustomEvent carrying the XML in
  // ``event.detail``. ``loadXmlIntoEditor`` queues into
  // ``pendingLoadXml`` if the iframe hasn't booted yet, and
  // ``onDrawioMessage`` flushes the queue as soon as ``init``
  // arrives — so this listener can be a one-liner.
  window.addEventListener("drawio-load-xml", (evt) => {
    if (!evt || typeof evt.detail !== "string") return;
    loadXmlIntoEditor(evt.detail);
  });

  // ── boot ────────────────────────────────────────────────
  document.addEventListener("DOMContentLoaded", () => {
    // v0.9.5.5: as the FIRST thing we do on DOMContentLoaded,
    // flip the status pill so the user has visual confirmation
    // that JS is at least running. If anything below throws,
    // the global ``error`` listener above will overwrite this
    // with the actual error message — but at least the user
    // sees *something* happen on the pill instead of the
    // static default "📊 Drawio Viewer" forever.
    setStatus("⚙️ JS 啟動中…");

    // v0.9.5.6: pywebview injects ``window.pywebview`` only AFTER
    // the page finishes loading (``pywebviewready`` event) —
    // ``DOMContentLoaded`` fires BEFORE that. The v0.9.5.5 boot
    // block read ``window.pywebview.api`` directly here and threw
    // "Cannot read properties of undefined (reading 'api')"
    // (user-reported 2026-09-02), and because the throw aborted
    // the rest of this handler, ``startDrawio()`` never ran and
    // the iframe never mounted. All bridge calls are now deferred
    // behind ``_waitForBridge()``; ``startDrawio()`` below runs
    // immediately and unconditionally (it guards its own
    // ``get_drawio_url`` probe with a hardcoded fallback URL).
    _waitForBridge().then((api) => {
      if (typeof api.get_write_root === "function") {
        api.get_write_root().then((p) => {
          sandboxPath.textContent = p || "";
          footerSand.textContent = p || "(unset)";
          pickerSandbox.textContent = p || "(unset)";
        }).catch(() => { /* ignore */ });
      }
      probeBridge();
    }).catch((e) => {
      // Bridge never showed up (pywebview failure, or the page
      // opened in a plain browser). Non-fatal: the viewer keeps
      // working read-only via the hardcoded fallback URL.
      setStatus(
        "⚠️ Python bridge 未就緒 — " + ((e && e.message) || e),
        "err",
      );
    });

    // v0.9.5.4: wire the loading-overlay buttons.
    if (btnOpenInBrowser) {
      btnOpenInBrowser.addEventListener("click", () => {
        // Fire-and-forget — we don't care about the response,
        // the browser will pop open externally.
        if (window.pywebview && window.pywebview.api
            && typeof window.pywebview.api.open_drawio_in_browser
               === "function") {
          window.pywebview.api.open_drawio_in_browser().catch(() => {});
        }
        setStatus("🌐 已嘗試在瀏覽器中開啟 drawio", "ok");
      });
    }
    if (btnReloadIframe) {
      btnReloadIframe.addEventListener("click", () => {
        // Tear down + remount the iframe with the same URL.
        // We do NOT clear pendingLoadXml — if there's XML
        // waiting, it'll re-fire on the new init event.
        drawioInitialized = false;
        if (iframe && iframe.parentNode) {
          iframe.parentNode.removeChild(iframe);
        }
        iframe = null;
        startDrawio(/* initialXml = */ null);
        // ``currentXml`` is preserved so a subsequent Save
        // back to sandbox keeps the user's edits.
        setStatus("🔄 重新載入 iframe…", "ok");
      });
    }

    // v0.9.5.6: ``probeBridge()`` moved INSIDE the
    // ``_waitForBridge().then(...)`` chain above — calling it
    // directly here would always race the not-yet-injected
    // bridge and flip the pill to a misleading bridge error.
    // v0.9.5.5: wrap ``startDrawio`` so a synchronous throw
    // (e.g. ``mount.appendChild`` on a missing ref, bridge
    // method missing, etc.) shows up on the status pill
    // rather than disappearing into the void.
    try {
      startDrawio(/* initialXml = */ null);
    } catch (e) {
      _reportError("startDrawio crashed", e);
      return;
    }

    // v0.9.5.5: 2-second watchdog. drawio's CDN is usually
    // fast enough that ``init`` arrives within ~1 s when the
    // bundle is already cached. If we still don't see it
    // after 2 s the user is staring at an apparently-stuck
    // viewer; flip the pill so they know JS is alive and we
    // are just waiting on drawio. ``init`` will overwrite
    // this when (if) it eventually arrives.
    if (watchdogTimer) clearTimeout(watchdogTimer);
    watchdogTimer = setTimeout(() => {
      if (!drawioInitialized) {
        setStatus("⏳ drawio 回應緩慢 (等候 init 中…)");
      }
    }, _WATCHDOG_MS);
  });
})();
