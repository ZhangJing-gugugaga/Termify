(function () {
  "use strict";
  var S = {
    taskId: null, frames: [], htmlFrames: [], interval: 0.1,
    charset: "ascii", totalFrames: 0, width: 80, height: 24,
    wasPlaying: false, fg: null, bg: null,
    canvasFrames: [], canvasEl: null, canvasCtx: null,
    fileList: [], selIdx: 0
  };
  var latestReq = 0;
  var currentFrame = 0, playing = false, rafId = null, lastFrameTime = 0;
  var FO = ".form" + "at-option";
  var FB = ".mcu-form" + "at-btn";
  var preview = document.getElementById("animPreview");
  var progressFill = document.querySelector(".progress-fill");
  var progressBar = document.querySelector(".progress-bar");
  var frameCounter = document.querySelector(".frame-counter");
  var playBtn = document.querySelector('.control-btn[title="Play"]');
  var pauseBtn = document.querySelector('.control-btn[title="Pause"]');
  var downloadBtn = document.querySelector(".download-btn");
  var uploadZone = document.getElementById("uploadZone");
  var terminalTitle = document.querySelector(".animation-terminal .terminal-title");
  var animTerminal = document.querySelector(".animation-terminal");

  function byId(id) { return document.getElementById(id); }
  function qa(s) { return document.querySelectorAll(s); }
  var toastTimer = null;

  /* ── ANSI → HTML ── */
  function ansiToHtml(text) {
    var fg = null, bg = null, out = "";
    var toks = text.split(/(\x1b\[[0-9;]*m)/);
    var buf = "", bufFg = null, bufBg = null;

    function flush() {
      if (!buf) return;
      var esc = buf.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      if (bufFg || bufBg) {
        var st = [];
        if (bufFg) st.push("color:" + bufFg);
        if (bufBg) st.push("background-color:" + bufBg);
        out += '<span style="' + st.join(";") + '">' + esc + "</span>";
      } else {
        out += esc;
      }
      buf = "";
    }

    for (var i = 0; i < toks.length; i++) {
      var t = toks[i]; if (!t) continue;
      if (/^\x1b\[[0-9;]*m$/.test(t)) {
        var inner = t.slice(2, -1);
        if (inner === "0") { fg = null; bg = null; }
        else if (inner === "39") { fg = "#c9d1d9"; }
        else if (inner === "49") { bg = "#0a0e14"; }
        else if (inner.indexOf("38;2;") === 0) {
          var p = inner.split(";"); fg = "rgb(" + p[2] + "," + p[3] + "," + p[4] + ")";
        } else if (inner.indexOf("48;2;") === 0) {
          var p = inner.split(";"); bg = "rgb(" + p[2] + "," + p[3] + "," + p[4] + ")";
        }
        if (fg !== bufFg || bg !== bufBg) { flush(); bufFg = fg; bufBg = bg; }
        continue;
      }
      // Half-block ▀: batch same-color runs into one span
      if (t.charAt(0) === "\u2580") {
        flush();
        var top = fg || "#000", bot = bg || "#000";
        var st = "background:linear-gradient(to bottom," + top + " 50%," + bot + " 50%);";
        var chars = "";
        var esc2 = t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        for (var j = 0; j < esc2.length; j++) chars += esc2.charAt(j);
        out += '<span class="hb" style="' + st + '">' + chars + "</span>";
      } else {
        buf += t;
      }
    }
    flush();
    return out;
  }

  /* ── Blocks Canvas Renderer ── */
  // For blocks charset, parse ANSI frames into pixel data and render to <canvas>
  // This completely bypasses DOM spans with gradient backgrounds (the lag root cause)
  function initBlocksCanvas() {
    if (S.canvasEl) return;
    var canvas = document.createElement("canvas");
    canvas.style.display = "none";
    canvas.style.width = "100%";
    canvas.style.imageRendering = "pixelated";
    canvas.style.imageRendering = "crisp-edges";
    // Insert before the preview div
    preview.parentNode.insertBefore(canvas, preview);
    S.canvasEl = canvas;
    S.canvasCtx = canvas.getContext("2d");
  }

  function parseBlocksFrame(lines) {
    // Parse ANSI-encoded blocks frame into pixel array
    // Each pixel: { top: [r,g,b], bot: [r,g,b] }
    var pixels = [];
    for (var li = 0; li < lines.length; li++) {
      var line = lines[li];
      var toks = line.split(/(\x1b\[[0-9;]*m)/);
      var fg = null, bg = null;
      for (var i = 0; i < toks.length; i++) {
        var t = toks[i]; if (!t) continue;
        if (/^\x1b\[[0-9;]*m$/.test(t)) {
          var inner = t.slice(2, -1);
          if (inner === "0") { fg = null; bg = null; }
          else if (inner.indexOf("38;2;") === 0) {
            var p = inner.split(";"); fg = [parseInt(p[2]), parseInt(p[3]), parseInt(p[4])];
          } else if (inner.indexOf("48;2;") === 0) {
            var p = inner.split(";"); bg = [parseInt(p[2]), parseInt(p[3]), parseInt(p[4])];
          }
          continue;
        }
        // Count ▀ characters
        for (var j = 0; j < t.length; j++) {
          if (t.charCodeAt(j) === 0x2580) {
            pixels.push({
              top: fg || [0, 0, 0],
              bot: bg || [0, 0, 0]
            });
          }
        }
      }
    }
    return pixels;
  }

  function renderBlocksCanvas(frameIdx) {
    if (!S.canvasEl || !S.canvasCtx) return;
    var pixels = S.canvasFrames[frameIdx];
    if (!pixels || !pixels.length) return;

    var cols = S.width;
    var rows = pixels.length / cols;

    // Each ▀ encodes 2 vertical image pixels; terminal chars are ~2:1 h:w,
    // so cellH ≈ 2 × cellW keeps the image aspect ratio correct.
    var container = preview.parentNode;
    var availW = container ? container.clientWidth - 32 : 640;
    var cellW = Math.max(4, Math.floor(availW / cols));
    var cellH = cellW * 2;
    var canvasW = cols * cellW;
    var canvasH = rows * cellH;

    var canvas = S.canvasEl;
    var ctx = S.canvasCtx;

    // Resize canvas buffer if dimensions changed
    if (canvas.width !== canvasW || canvas.height !== canvasH) {
      canvas.width = canvasW;
      canvas.height = canvasH;
      canvas.style.width = canvasW + "px";
      canvas.style.height = canvasH + "px";
    }

    // Clear with black background
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, canvasW, canvasH);

    // Draw each ▀ as two half-height rectangles
    var halfH = cellH / 2;
    for (var i = 0; i < pixels.length; i++) {
      var col = i % cols;
      var row = Math.floor(i / cols);
      var x = col * cellW;
      var y = row * cellH;
      var p = pixels[i];

      // Top half
      ctx.fillStyle = "rgb(" + p.top[0] + "," + p.top[1] + "," + p.top[2] + ")";
      ctx.fillRect(x, y, cellW, halfH);

      // Bottom half
      ctx.fillStyle = "rgb(" + p.bot[0] + "," + p.bot[1] + "," + p.bot[2] + ")";
      ctx.fillRect(x, y + halfH, cellW, halfH);
    }
  }

  /* ── Toast ── */
  function toast(msg) {
    var el = byId("toast"); if (!el) return;
    el.textContent = msg; el.classList.add("show");
    if (toastTimer !== null) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove("show"); }, 2200);
  }

  /* ── Title / meta ── */
  function setTitleMeta() {
    if (terminalTitle && S.charset)
      terminalTitle.textContent = "animation preview - " + S.charset + " style";
  }

  /* ── Render a single frame into the preview terminal ── */
  function renderFrame(idx) {
    if (!preview || !S.frames.length) return;
    if (idx < 0) idx = 0;
    if (idx >= S.frames.length) idx = S.frames.length - 1;

    // Blocks style: use canvas rendering (bypasses DOM spans with gradients)
    if (S.charset === "blocks" && S.canvasEl) {
      S.canvasEl.style.display = "block";
      preview.style.display = "none";
      renderBlocksCanvas(idx);
    } else {
      // Other styles: use DOM rendering
      if (S.canvasEl) S.canvasEl.style.display = "none";
      preview.style.display = "";
      var lines = S.frames[idx];
      var joined = lines.join("\n");
      var hasAnsi = joined.indexOf("\x1b") !== -1;
      if (hasAnsi) {
        preview.innerHTML = S.htmlFrames[idx] || lines.map(ansiToHtml).join("\n");
      } else {
        preview.textContent = joined;
      }
    }

    currentFrame = idx;
    var n = S.frames.length;
    if (progressFill) progressFill.style.width = ((idx + 1) / n) * 100 + "%";
    if (frameCounter) frameCounter.textContent = (idx + 1) + " / " + n;
    setTitleMeta();
  }

  function tick() {
    if (!S.frames.length) return;
    renderFrame((currentFrame + 1) % S.frames.length);
  }

  function rafLoop(ts) {
    if (!playing) return;
    if (!lastFrameTime) {
      lastFrameTime = ts;                       // first frame: don't tick
    } else if (ts - lastFrameTime >= S.interval * 1000) {
      lastFrameTime = ts;
      tick();
    }
    rafId = requestAnimationFrame(rafLoop);
  }

  function startPlayer() {
    if (playing || S.frames.length < 2) return;
    playing = true;
    if (playBtn) playBtn.classList.add("active");
    if (pauseBtn) pauseBtn.classList.remove("active");
    lastFrameTime = 0;
    rafId = requestAnimationFrame(rafLoop);
  }

  function pausePlayer() {
    playing = false;
    if (playBtn) playBtn.classList.remove("active");
    if (pauseBtn) pauseBtn.classList.add("active");
    if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }
  }

  /* ── Apply preview data from server ── */
  function applyPreview(d) {
    S.frames = d.frames || [];
    S.interval = d.interval || 0.1;
    S.totalFrames = d.frame_count || S.frames.length;
    S.htmlFrames = [];
    S.canvasFrames = [];

    // Pre-compute HTML frames for all styles
    for (var i = 0; i < S.frames.length; i++) {
      S.htmlFrames.push(S.frames[i].map(ansiToHtml).join("\n"));
    }

    // For blocks style, also pre-compute pixel data for canvas rendering
    if (S.charset === "blocks") {
      initBlocksCanvas();
      for (var i = 0; i < S.frames.length; i++) {
        S.canvasFrames.push(parseBlocksFrame(S.frames[i]));
      }
    }

    // Set data-charset so CSS can adjust font-size per style
    if (preview) preview.dataset.charset = S.charset;
    setTitleMeta();
    renderFrame(0);
    syncTerminalHeight();
    fitTerminalFontSize();
    if (S.wasPlaying) { S.wasPlaying = false; startPlayer(); }
  }

  /* ── Sync preview terminal height to output panel ── */
  function syncTerminalHeight() {
    var term = document.querySelector(".animation-terminal");
    var panel = document.querySelector(".output-panel");
    if (!term || !panel) return;
    // With align-items:start the panel keeps its natural height.
    // Reset terminal to auto first so we get an unbiased panel measurement.
    term.style.height = "";
    var h = panel.getBoundingClientRect().height;
    if (h > 0) term.style.height = h + "px";
  }

  /* ── Fit terminal font-size to fill the window (quality changes, not size) ── */
  function fitTerminalFontSize() {
    var tb = document.querySelector(".animation-terminal .terminal-body");
    if (!tb) return;
    // Skip for blocks charset (uses canvas, not text)
    if (S.charset === "blocks") return;

    var style = getComputedStyle(tb);
    var padX = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
    var padY = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    var availW = tb.clientWidth - padX;
    var availH = tb.clientHeight - padY;
    if (availW <= 0 || availH <= 0 || !S.width || !S.height) return;

    // Monospace: char width ≈ 0.6 * font-size, line-height = 1.3 * font-size
    var charRatio = 0.6;
    var lineHeightRatio = 1.3;
    var fsW = availW / (S.width * charRatio);
    var fsH = availH / (S.height * lineHeightRatio);
    var fs = Math.min(fsW, fsH);
    fs = Math.max(2, Math.min(fs, 30));  // clamp to sane range
    tb.style.fontSize = fs + "px";
  }

  window.addEventListener("resize", function () { syncTerminalHeight(); fitTerminalFontSize(); });

  /* ── Build color query params ── */
  function colorParams() {
    var p = "";
    if (S.fg) p += "&fg=rgb(" + S.fg[0] + "," + S.fg[1] + "," + S.fg[2] + ")";
    if (S.bg) p += "&bg=rgb(" + S.bg[0] + "," + S.bg[1] + "," + S.bg[2] + ")";
    return p;
  }

  /* ── Request preview from backend ── */
  function requestPreview(charset) {
    if (!S.taskId) { toast("请先上传文件"); return; }
    var myId = ++latestReq;
    var url = "/api/preview/" + S.taskId
      + "?charset=" + charset
      + "&width=" + S.width + "&height=" + S.height
      + colorParams();
    fetch(url).then(function (r) { return r.json(); }).then(function (d) {
      if (myId !== latestReq) return;
      if (d.error) { toast(d.error); return; }
      S.wasPlaying = playing;
      if (playing) {
        if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }
        playing = false;
      }
      S.charset = charset;
      applyPreview(d);
    }).catch(function () { if (myId !== latestReq) return; toast("preview failed"); });
  }

  /* ── File list rendering ── */
  function renderFileList() {
    var container = byId("fileList");
    if (!container) return;
    if (!S.fileList.length) { container.innerHTML = ""; container.style.display = "none"; return; }
    container.style.display = "flex";
    container.innerHTML = "";
    S.fileList.forEach(function (f, i) {
      var item = document.createElement("div");
      item.className = "file-list-item" + (i === S.selIdx ? " active" : "");
      item.textContent = f.filename;
      item.title = f.filename + " — 点击下载切换";
      item.addEventListener("click", function () { selectFile(i); });
      container.appendChild(item);
    });
  }

  function selectFile(idx) {
    if (idx < 0 || idx >= S.fileList.length) return;
    S.selIdx = idx;
    var f = S.fileList[idx];
    S.taskId = f.task_id;
    S.charset = f.charset || "ascii";
    S.width = f.width || 80;
    S.height = f.height || 24;
    S.totalFrames = f.frames_count;
    S.wasPlaying = true;
    markSelected(".style-card", '[data-style="' + S.charset + '"]');
    renderFileList();
    requestPreview(S.charset);
  }

  /* ── File upload ── */
  function handleFiles(fileList) {
    var files = Array.prototype.slice.call(fileList);
    if (!files.length) return;
    var fd = new FormData();
    files.forEach(function (f) { fd.append("files", f); });
    if (uploadZone) uploadZone.classList.add("uploading");
    fetch("/api/upload-batch", { method: "POST", body: fd })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (uploadZone) uploadZone.classList.remove("uploading");
        if (d.errors && d.errors.length) {
          d.errors.forEach(function (err) { toast(err.filename + ": " + err.error); });
        }
        if (d.task_ids && d.task_ids.length) {
          S.fileList = d.task_ids.map(function (t) {
            return { task_id: t.task_id, filename: t.filename, frames_count: t.frames_count,
                     charset: "ascii", width: 80, height: 24 };
          });
          S.selIdx = 0;
          selectFile(0);
          renderFileList();
          var stylesSection = document.getElementById("styles");
          if (stylesSection) stylesSection.scrollIntoView({ behavior: "smooth", block: "start" });
          if (d.task_ids.length > 1) {
            toast("已上传 " + d.task_ids.length + " 个文件，点击文件名切换");
          }
        } else if (!d.error) {
          toast("没有有效文件被上传");
        }
        if (d.error) { toast(d.error); }
      })
      .catch(function (e) {
        if (uploadZone) uploadZone.classList.remove("uploading");
        toast("upload failed: " + e);
      });
  }

  // Backward-compatible alias
  function handleFile(file) { handleFiles([file]); }

  function markSelected(scopeSel, matchSel) {
    qa(scopeSel).forEach(function (el) { el.classList.remove("selected"); });
    if (matchSel) qa(matchSel).forEach(function (el) { el.classList.add("selected"); });
  }

  function selectedFormat() {
    var m = document.querySelector('[data-format="mcu"]');
    if (m && m.classList.contains("selected")) return "mcu";
    var opts = qa(FO);
    for (var i = 0; i < opts.length; i++) {
      if (opts[i].classList.contains("selected")) {
        var fmt = opts[i].getAttribute("data-format");
        return fmt ? fmt : (i === 0 ? "python" : "html");
      }
    }
    return "python";
  }

  /* ── Download ── */
  function doDownload() {
    if (!S.taskId) { toast("请先上传文件"); return; }
    var fmt = selectedFormat();
    if (fmt === "mcu") { toast("MCU 输出即将在 v2 支持"); return; }
    var body = {
      task_id: S.taskId, charset: S.charset, format: fmt,
      width: S.width, height: S.height
    };
    if (S.fg) body.fg = "rgb(" + S.fg[0] + "," + S.fg[1] + "," + S.fg[2] + ")";
    if (S.bg) body.bg = "rgb(" + S.bg[0] + "," + S.bg[1] + "," + S.bg[2] + ")";
    fetch("/api/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d.error) { toast(d.error); return; }
      window.location.href = d.download_url;
    }).catch(function (e) { toast("download failed: " + e); });
  }

  /* ── Hex → RGB helper ── */
  function hexToRgb(hex) {
    var r = parseInt(hex.slice(1, 3), 16);
    var g = parseInt(hex.slice(3, 5), 16);
    var b = parseInt(hex.slice(5, 7), 16);
    return [r, g, b];
  }

  /* ══════════════════════════════════════
     EVENT BINDINGS
     ══════════════════════════════════════ */

  // Style cards
  qa(".style-card").forEach(function (card) {
    card.addEventListener("click", function () {
      qa(".style-card").forEach(function (c) { c.classList.remove("selected"); });
      card.classList.add("selected");
      var s = card.getAttribute("data-style");
      requestPreview(s);
      var previewEl = document.getElementById("preview");
      if (previewEl) previewEl.scrollIntoView({ behavior: "instant", block: "start" });
    });
  });

  // Format options
  qa(FO).forEach(function (opt) {
    opt.addEventListener("click", function () {
      qa(FO).forEach(function (o) { o.classList.remove("selected"); });
      opt.classList.add("selected");
      var svg = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg>';
      if (opt.getAttribute("data-format") === "mcu") {
        if (byId("mcuPanel")) byId("mcuPanel").classList.add("visible");
        if (byId("terminalSizeCard")) byId("terminalSizeCard").style.display = "none";
        if (downloadBtn) downloadBtn.innerHTML = svg + 'download Arduino <span class="badge-v2">v2</span>';
      } else {
        if (byId("mcuPanel")) byId("mcuPanel").classList.remove("visible");
        if (byId("terminalSizeCard")) byId("terminalSizeCard").style.display = "";
        if (downloadBtn) downloadBtn.innerHTML = svg + "download animation";
      }
    });
  });

  // Terminal size buttons
  qa(".size-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      qa(".size-btn").forEach(function (b) { b.classList.remove("selected"); });
      btn.classList.add("selected");
      var m = (btn.textContent || "").match(/(\d+)\s*[x×]\s*(\d+)/);
      if (m) {
        S.width = parseInt(m[1], 10);
        S.height = parseInt(m[2], 10);
        // Set data-size on terminal for CSS font-size scaling
        if (animTerminal) animTerminal.dataset.size = m[1];
      }
      if (S.taskId) { requestPreview(S.charset); }
      else { toast("切换尺寸将在上传后应用"); }
    });
  });

  // MCU buttons
  [FB, ".mcu-res-btn"].forEach(function (s) {
    qa(s).forEach(function (btn) {
      btn.addEventListener("click", function () {
        qa(s).forEach(function (b) { b.classList.remove("selected"); });
        btn.classList.add("selected");
      });
    });
  });

  // Play / Pause
  if (playBtn) playBtn.addEventListener("click", startPlayer);
  if (pauseBtn) pauseBtn.addEventListener("click", pausePlayer);

  // Progress bar scrub
  if (progressBar) progressBar.addEventListener("click", function (e) {
    var r = progressBar.getBoundingClientRect();
    var x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }
    playing = false;
    renderFrame(Math.round(x * (S.frames.length - 1)));
  });

  // Download
  if (downloadBtn) downloadBtn.addEventListener("click", doDownload);

  // Share link copy
  var copyBtn = document.querySelector(".share-link button");
  if (copyBtn) copyBtn.addEventListener("click", function () { toast("分享功能即将上线"); });

  // Hidden file input (multi-select)
  var fileInput = (function () {
    var f = document.createElement("input"); f.type = "file"; f.multiple = true;
    f.accept = "image/gif,image/png,image/jpeg"; f.style.display = "none";
    document.body.appendChild(f); return f;
  })();

  // Upload zone
  if (uploadZone) {
    uploadZone.addEventListener("click", function (e) {
      if (e.target && e.target.closest && e.target.closest(".upload-formats")) return;
      fileInput.click();
    });
    fileInput.addEventListener("change", function () {
      if (fileInput.files.length) handleFiles(fileInput.files);
    });
    uploadZone.addEventListener("drop", function (e) {
      e.preventDefault(); uploadZone.classList.remove("drag-over");
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length)
        handleFiles(e.dataTransfer.files);
    });
    uploadZone.addEventListener("dragover", function (e) {
      e.preventDefault(); uploadZone.classList.add("drag-over");
    });
    uploadZone.addEventListener("dragleave", function () {
      uploadZone.classList.remove("drag-over");
    });
  }

  // Tweaks panel toggle
  var tweaksToggle = byId("tweaksToggle"),
      tweaksPanel = byId("tweaksPanel"),
      tweaksClose = byId("tweaksClose");
  if (tweaksToggle) tweaksToggle.addEventListener("click", function () {
    tweaksPanel.classList.toggle("open");
    tweaksToggle.style.display = tweaksPanel.classList.contains("open") ? "none" : "flex";
  });
  if (tweaksClose) tweaksClose.addEventListener("click", function () {
    tweaksPanel.classList.remove("open");
    tweaksToggle.style.display = "flex";
  });

  // Grid / scanline toggles
  function bindTweak(onSel, offSel, prop, onVal, offVal) {
    qa(onSel).forEach(function (btn) {
      btn.addEventListener("click", function () {
        document.body.style.setProperty(prop, onVal);
        qa(onSel + "," + offSel).forEach(function (b) { b.classList.remove("active"); });
        btn.classList.add("active");
      });
    });
    qa(offSel).forEach(function (btn) {
      btn.addEventListener("click", function () {
        document.body.style.setProperty(prop, offVal);
        qa(onSel + "," + offSel).forEach(function (b) { b.classList.remove("active"); });
        btn.classList.add("active");
      });
    });
  }
  bindTweak('[data-tweak="grid-on"]', '[data-tweak="grid-off"]', "--grid-opacity", "0.3", "0");
  bindTweak('[data-tweak="scan-on"]', '[data-tweak="scan-off"]', "--scanline-opacity", "1", "0");

  // Theme color toggles
  qa('[data-tweak^="theme-"]').forEach(function (btn) {
    btn.addEventListener("click", function () {
      qa('[data-tweak^="theme-"]').forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      var theme = btn.getAttribute("data-tweak").replace("theme-", "");
      var colors = {
        green:  { main: "#00ff41", dim: "#00cc33", glow: "rgba(0,255,65,0.15)" },
        amber:  { main: "#ffb000", dim: "#cc8d00", glow: "rgba(255,176,0,0.12)" },
        cyan:   { main: "#00d4ff", dim: "#00a8cc", glow: "rgba(0,212,255,0.12)" }
      };
      var c = colors[theme]; if (!c) return;
      document.documentElement.style.setProperty("--green", c.main);
      document.documentElement.style.setProperty("--green-dim", c.dim);
      document.documentElement.style.setProperty("--green-glow", c.glow);
    });
  });

  /* ── Phase 4: Color picker wiring ── */
  var fgPicker = byId("fgColorPicker"),
      bgPicker = byId("bgColorPicker"),
      colorResetBtn = byId("colorResetBtn");

  if (fgPicker) fgPicker.addEventListener("change", function () {
    S.fg = hexToRgb(fgPicker.value);
    if (S.taskId) requestPreview(S.charset);
  });
  if (bgPicker) bgPicker.addEventListener("change", function () {
    S.bg = hexToRgb(bgPicker.value);
    if (S.taskId) requestPreview(S.charset);
  });
  if (colorResetBtn) colorResetBtn.addEventListener("click", function () {
    S.fg = null; S.bg = null;
    if (fgPicker) fgPicker.value = "#00ff41";
    if (bgPicker) bgPicker.value = "#0a0e14";
    if (S.taskId) requestPreview(S.charset);
  });

  setTitleMeta();
})();
