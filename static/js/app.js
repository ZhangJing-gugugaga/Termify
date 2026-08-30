(function () {
  "use strict";
  var S = {
    taskId: null, frames: [], htmlFrames: [], interval: 0.1,
    charset: "ascii", totalFrames: 0, width: 80, height: 24,
    wasPlaying: false, fg: null, bg: null, ramp: "",
    canvasFrames: [], canvasEl: null, canvasCtx: null,
    fileList: [], selIdx: 0, sourceFile: null
  };
  var latestReq = 0;
  var currentFrame = 0, playing = false, rafId = null, lastFrameTime = 0;
  var FO = ".form" + "at-option";
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
    var totalSec = n * S.interval;
    var curSec = (idx + 1) * S.interval;
    if (progressFill) progressFill.style.width = (totalSec > 0 ? (curSec / totalSec) * 100 : 0) + "%";
    if (frameCounter) frameCounter.textContent = curSec.toFixed(1) + "s / " + totalSec.toFixed(1) + "s";
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
  function requestPreview(charset, opts) {
    if (!S.taskId) {
      if (!(opts && opts.silent)) toast("请先上传文件");
      return;
    }
    if (charset === "custom" && !S.ramp) { toast("请先在 Tweaks 面板填写自定义字符"); return; }
    var myId = ++latestReq;
    var url = "/api/preview/" + S.taskId
      + "?charset=" + charset
      + "&width=" + S.width + "&height=" + S.height
      + colorParams();
    if (charset === "custom") url += "&chars=" + encodeURIComponent(S.ramp);
    // 渲染中状态：大尺寸/长视频首切需要数秒，让等待可见
    if (animTerminal) animTerminal.classList.add("rendering");
    fetch(url).then(function (r) { return r.json(); }).then(function (d) {
      if (myId !== latestReq) return;
      if (animTerminal) animTerminal.classList.remove("rendering");
      if (d.error) { toast(d.error); return; }
      S.wasPlaying = playing;
      if (playing) {
        if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }
        playing = false;
      }
      S.charset = charset;
      applyPreview(d);
    }).catch(function () {
      if (myId !== latestReq) return;
      if (animTerminal) animTerminal.classList.remove("rendering");
      toast("preview failed");
    });
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

  /* ── File type routing ── */
  var VIDEO_EXTS = [".mp4", ".webm", ".mov", ".avi", ".mkv"];
  var IMAGE_EXTS = [".gif", ".png", ".jpg", ".jpeg"];
  function extOf(name) {
    var idx = name.lastIndexOf(".");
    return idx >= 0 ? name.slice(idx).toLowerCase() : "";
  }
  function isVideo(file) { return VIDEO_EXTS.indexOf(extOf(file.name)) >= 0; }
  function isImage(file) { return IMAGE_EXTS.indexOf(extOf(file.name)) >= 0; }

  /* ── File upload ── */
  function handleFiles(fileList) {
    var files = Array.prototype.slice.call(fileList);
    if (!files.length) return;

    // Split by type
    var videos = files.filter(isVideo);
    var images = files.filter(isImage);
    var unsupported = files.filter(function (f) { return !isVideo(f) && !isImage(f); });

    if (unsupported.length) {
      unsupported.forEach(function (f) { toast(f.name + ": 不支持的格式"); });
    }

    // Upload images via batch endpoint
    if (images.length) {
      uploadImages(images);
    }

    // Upload videos one at a time (endpoint is single-file)
    videos.forEach(function (v) { uploadVideo(v); });

    // Fallthrough: nothing to upload
    if (!images.length && !videos.length) return;
  }

  function uploadImages(files) {
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
          var newFiles = d.task_ids.map(function (t) {
            return { task_id: t.task_id, filename: t.filename, frames_count: t.frames_count,
                     charset: "ascii", width: 80, height: 24 };
          });
          S.fileList = S.fileList.concat(newFiles);
          S.selIdx = S.fileList.length - newFiles.length;
          selectFile(S.selIdx);
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

  function probeVideoDuration(file, cb) {
    // 本地解码视频元数据拿时长（不产生上传流量），用于诚实估算转换耗时
    try {
      var v = document.createElement("video");
      v.preload = "metadata";
      v.onloadedmetadata = function () {
        var d = v.duration;
        URL.revokeObjectURL(v.src);
        cb(isFinite(d) ? d : null);
      };
      v.onerror = function () { URL.revokeObjectURL(v.src); cb(null); };
      v.src = URL.createObjectURL(file);
    } catch (e) { cb(null); }
  }

  function uploadVideo(file) {
    probeVideoDuration(file, function (d) {
      file.__durationSec = d;
      startVideoUpload(file);
    });
  }

  function startVideoUpload(file) {
    var fd = new FormData();
    fd.append("file", file);
    if (uploadZone) uploadZone.classList.add("uploading");
    var startedAt = Date.now();
    showModal("上传视频 " + file.name,
      "0% · 计算预计时间…", false, true);
    setModalProgress(0);
    var xhr = new XMLHttpRequest();
    xhr.open("POST", "/api/upload-video");
    xhr.upload.addEventListener("progress", function (e) {
      if (!e.lengthComputable) return;
      var pct = e.loaded / e.total * 100;
      setModalProgress(pct);
      var elapsed = (Date.now() - startedAt) / 1000;
      var speed = e.loaded / Math.max(0.1, elapsed);
      var eta = Math.max(0, Math.round((e.total - e.loaded) / Math.max(1, speed)));
      modalTitle.textContent = "上传视频 " + file.name;
      modalText.textContent = pct.toFixed(0) + "% · " +
        (e.loaded / 1024 / 1024).toFixed(1) + " / " +
        (e.total / 1024 / 1024).toFixed(1) + "MB · 预计剩余 " + eta + " 秒";
    });
    xhr.upload.addEventListener("load", function () {
      // 上传完成 → 服务器抽帧+渲染。实测 50s/720p ≈ 6s（抽帧已缩放+单遍渲染），
      // 转换耗时主要由视频时长决定，按 0.12s/秒视频 估。
      var dur = file.__durationSec;
      var est = dur ? Math.max(3, Math.round(dur * 0.12))
                    : Math.max(5, Math.round(file.size / 1024 / 1024 * 0.5) + 3);
      modalTitle.textContent = "正在转换视频";
      modalText.textContent = "抽帧 + 渲染中，预计约 " + est + " 秒（视频越长越久，请耐心等待）…";
      setModalProgress(100);
    });
    xhr.addEventListener("load", function () {
      if (uploadZone) uploadZone.classList.remove("uploading");
      hideModal();
      var d = null;
      try { d = JSON.parse(xhr.responseText); } catch (err) { d = null; }
      if (xhr.status !== 200 || !d || d.error) {
        toast((d && d.error) || "视频转换失败 (HTTP " + xhr.status + ")");
        return;
      }
      var vf = { task_id: d.task_id, filename: d.filename || file.name,
                 frames_count: d.frames_count, charset: "ascii", width: 80, height: 24,
                 kind: "video", interval: d.interval || 0.1, sourceFile: file };
      S.fileList = S.fileList.concat([vf]);
      S.selIdx = S.fileList.length - 1;
      selectFile(S.selIdx);
      renderFileList();
      var stylesSection = document.getElementById("styles");
      if (stylesSection) stylesSection.scrollIntoView({ behavior: "smooth", block: "start" });
    });
    xhr.addEventListener("error", function () {
      if (uploadZone) uploadZone.classList.remove("uploading");
      hideModal();
      toast("video upload failed: 网络错误");
    });
    xhr.send(fd);
  }

  // Backward-compatible alias
  function handleFile(file) { handleFiles([file]); }

  function markSelected(scopeSel, matchSel) {
    qa(scopeSel).forEach(function (el) { el.classList.remove("selected"); });
    if (matchSel) qa(matchSel).forEach(function (el) { el.classList.add("selected"); });
  }

  function selectedFormat() {
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
    if (S.charset === "custom" && !S.ramp) { toast("请先在 Tweaks 面板填写自定义字符"); return; }
    var fmt = selectedFormat();
    var body = {
      task_id: S.taskId, charset: S.charset, format: fmt,
      width: S.width, height: S.height
    };
    if (S.charset === "custom") body.chars = S.ramp;
    if (S.fg) body.fg = "rgb(" + S.fg[0] + "," + S.fg[1] + "," + S.fg[2] + ")";
    if (S.bg) body.bg = "rgb(" + S.bg[0] + "," + S.bg[1] + "," + S.bg[2] + ")";
    if (fmt === "mp4") {
      // 实测 ~100k 字符格/秒（字节合成 + x264），加 3s 编码固定开销
      var est = Math.max(5, Math.round((S.totalFrames || 1) * S.width * S.height / 100000) + 3);
      showModal("正在导出 MP4 视频", "预计约 " + est + " 秒，编码在服务器进行，完成后自动下载…");
      fetch("/api/generate", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body)
      }).then(function (r) { return r.json(); }).then(function (d) {
        if (d.error) {
          showModal("导出失败", d.error, true);
          return;
        }
        hideModal();
        toast("MP4 已生成 (" + (d.file_size || "?") + ")，开始下载");
        window.location.href = d.download_url;
      }).catch(function (e) {
        showModal("导出失败", "网络错误: " + e, true);
      });
      return;
    }
    fetch("/api/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d.error) { toast(d.error); return; }
      window.location.href = d.download_url;
    }).catch(function (e) { toast("download failed: " + e); });
  }

  /* ── T21: termify modal (no native alert/confirm) ── */
  var modal = byId("termifyModal");
  var modalTitle = byId("termifyModalTitle");
  var modalText = byId("termifyModalText");
  var modalSpinner = byId("termifyModalSpinner");
  var modalProgressWrap = byId("termifyModalProgressWrap");
  var modalProgress = byId("termifyModalProgress");
  function showModal(title, text, isError, withProgress) {
    if (!modal) return;
    modal.hidden = false;
    modal.classList.toggle("error", !!isError);
    if (modalSpinner) modalSpinner.style.display = isError ? "none" : "block";
    if (modalProgressWrap) modalProgressWrap.hidden = !withProgress;
    if (modalTitle) modalTitle.textContent = title;
    if (modalText) modalText.textContent = text || "";
  }
  function setModalProgress(pct) {
    if (modalProgress) modalProgress.style.width = Math.max(0, Math.min(100, pct)) + "%";
  }
  function hideModal() {
    if (modal) modal.hidden = true;
  }
  var modalClose = byId("termifyModalClose");
  if (modalClose) modalClose.addEventListener("click", hideModal);

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
  var styleDemos = {};
  qa(".style-card").forEach(function (card) {
    var p = card.querySelector(".style-preview");
    if (p) styleDemos[card.getAttribute("data-style")] = p.textContent;
  });
  function customDemoArt() {
    var ramp = S.ramp || "@%#*+=-:.";
    var row = "";
    for (var i = 0; i < 22; i++) row += ramp[i % ramp.length];
    return row + "\n" + row + "\n " + row + "\n " + row + "\n  " + row + "\n  " + row +
           "\n " + row + "\n " + row;
  }
  function showStyleDemo(style) {
    if (!preview) return;
    if (S.canvasEl) S.canvasEl.style.display = "none";
    preview.style.display = "";
    if (style === "custom") {
      preview.textContent = customDemoArt();
    } else if (styleDemos[style]) {
      preview.textContent = styleDemos[style];
    }
    setTitleMeta();
  }
  qa(".style-card").forEach(function (card) {
    card.addEventListener("click", function () {
      qa(".style-card").forEach(function (c) { c.classList.remove("selected"); });
      card.classList.add("selected");
      var s = card.getAttribute("data-style");
      if (!S.taskId) showStyleDemo(s);
      requestPreview(s, { silent: !S.taskId });
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
      if (byId("terminalSizeCard")) byId("terminalSizeCard").style.display = "";
      if (downloadBtn) downloadBtn.innerHTML = svg + "download animation";
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

  // Hidden file input (multi-select)
  var fileInput = (function () {
    var f = document.createElement("input"); f.type = "file"; f.multiple = true;
    f.accept = "image/gif,image/png,image/jpeg,video/mp4,video/webm,video/quicktime,video/x-msvideo,video/x-matroska"; f.style.display = "none";
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

  // URL fetch
  (function () {
    var urlSect = byId("urlFetch");
    var urlInput = byId("urlInput");
    var urlBtn = byId("urlFetchBtn");
    if (!urlSect || !urlInput || !urlBtn) return;
    urlSect.style.display = "flex";
    function isVideoPlatformUrl(url) {
      try {
        var host = (new URL(url)).hostname.toLowerCase();
      } catch (e) { return false; }
      var fams = ["bilibili.com", "b23.tv", "douyin.com", "iesdouyin.com",
                  "youtube.com", "youtu.be"];
      return fams.some(function (f) { return host === f || host.indexOf("." + f) !== -1; });
    }
    function finishVideoTask(d) {
      var vf = { task_id: d.task_id, filename: d.filename || "video-link",
                 frames_count: d.frames_count, charset: "ascii", width: 80, height: 24,
                 kind: "video", interval: d.interval || 0.1 };
      S.fileList = S.fileList.concat([vf]);
      S.selIdx = S.fileList.length - 1;
      selectFile(S.selIdx);
      renderFileList();
      var stylesSection = document.getElementById("styles");
      if (stylesSection) stylesSection.scrollIntoView({ behavior: "smooth", block: "start" });
      urlInput.value = "";
    }
    function doFetchVideoUrl(url) {
      showModal("解析视频链接", "正在从平台获取视频（预计 15-60 秒，取决于视频大小）…");
      fetch("/api/fetch-video-url", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url })
      }).then(function (r) { return r.json(); }).then(function (d) {
        hideModal();
        if (d.error) { showModal("解析失败", d.error, true); return; }
        toast("视频已解析（" + d.frames_count + " 帧）");
        finishVideoTask(d);
      }).catch(function (e) {
        hideModal();
        showModal("解析失败", "网络错误: " + e, true);
      });
    }
    function doFetchUrl() {
      var url = urlInput.value.trim();
      if (!url) { toast("请输入 URL"); return; }
      if (isVideoPlatformUrl(url)) { doFetchVideoUrl(url); return; }
      urlBtn.disabled = true; urlBtn.textContent = "下载中...";
      fetch("/api/fetch-url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url })
      }).then(function (r) { return r.json(); }).then(function (d) {
        urlBtn.disabled = false; urlBtn.textContent = "下载";
        if (d.error) { toast(d.error); return; }
        S.fileList = [{ task_id: d.task_id, filename: d.filename || "url-image",
                        frames_count: d.frames_count, charset: "ascii", width: 80, height: 24 }];
        S.selIdx = 0; selectFile(0); renderFileList();
        var stylesSection = byId("styles");
        if (stylesSection) stylesSection.scrollIntoView({ behavior: "smooth", block: "start" });
        urlInput.value = "";
      }).catch(function (e) {
        urlBtn.disabled = false; urlBtn.textContent = "下载";
        toast("fetch failed: " + e);
      });
    }
    urlBtn.addEventListener("click", doFetchUrl);
    urlInput.addEventListener("keydown", function (e) {
      if (e.key === "Enter") doFetchUrl();
    });
  })();

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

  /* ── T20: palette presets ── */
  qa(".palette-swatch").forEach(function (sw) {
    sw.addEventListener("click", function () {
      qa(".palette-swatch").forEach(function (b) { b.classList.remove("active"); });
      sw.classList.add("active");
      var fg = sw.getAttribute("data-fg");
      var bg = sw.getAttribute("data-bg");
      if (fgPicker) fgPicker.value = fg;
      if (bgPicker) bgPicker.value = bg;
      S.fg = hexToRgb(fg);
      S.bg = hexToRgb(bg);
      if (S.taskId) requestPreview(S.charset);
      else toast("配色已应用，上传后生效");
    });
  });

  /* ── T20: custom charset ramp ── */
  var rampInput = byId("customRampInput");
  var rampApply = byId("customRampApply");
  function applyRamp() {
    var v = rampInput ? rampInput.value.trim() : "";
    if (!v) { toast("请先输入自定义字符（密→疏排列）"); return; }
    S.ramp = v;
    var customCard = document.querySelector('[data-style="custom"]');
    if (customCard) customCard.click();
  }
  if (rampApply) rampApply.addEventListener("click", applyRamp);
  if (rampInput) rampInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") applyRamp();
  });

  setTitleMeta();

  /* ── Gallery share: store source file on upload (images only) ── */
  var _origHandleFiles = handleFiles;
  handleFiles = function (fileList) {
    var files = Array.prototype.slice.call(fileList);
    var firstImage = files.filter(function (f) { return isImage(f); })[0];
    if (firstImage) {
      S.sourceFile = firstImage;
    } else if (files.length && files.every(function (f) { return isVideo(f); })) {
      S.sourceFile = null;  // 视频任务暂不支持分享画廊
    }
    _origHandleFiles(fileList);
  };

  /* ── Gallery share: open/close modal + submit ── */
  var shareBtn = byId("shareToGalleryBtn");
  var galleryModal = byId("galleryModal");

  function openGalleryModal() {
    var cur = S.fileList[S.selIdx] || {};
    if (!(cur.sourceFile || S.sourceFile)) { toast("请先上传文件"); return; }
    galleryModal.classList.add("open");
    var title = byId("galleryTitle");
    var srcName = ((cur.sourceFile || S.sourceFile) || {}).name || "";
    if (!title.value && srcName) {
      title.value = srcName.replace(/\.[^.]+$/, "").slice(0, 60);
      updateCounts();
    }
  }
  function closeGalleryModal() { galleryModal.classList.remove("open"); }

  if (shareBtn) shareBtn.addEventListener("click", openGalleryModal);
  if (byId("galleryModalClose")) byId("galleryModalClose").addEventListener("click", closeGalleryModal);
  if (galleryModal) galleryModal.addEventListener("click", function (e) {
    if (e.target === galleryModal) closeGalleryModal();
  });

  /* ── Gallery share: show button for tasks that have a source ── */
  var _origSelectFile = selectFile;
  selectFile = function (idx) {
    _origSelectFile(idx);
    var cur = S.fileList[idx];
    var src = (cur && cur.sourceFile) || S.sourceFile;
    if (shareBtn) shareBtn.style.display = src ? "flex" : "none";
  };

  /* ── Character counters ── */
  function updateCounts() {
    var t = byId("galleryTitle"), d = byId("galleryDesc"), a = byId("galleryAuthor");
    if (byId("titleCount") && t) byId("titleCount").textContent = t.value.length + "/60";
    if (byId("descCount") && d) byId("descCount").textContent = d.value.length + "/500";
    if (byId("authorCount") && a) byId("authorCount").textContent = a.value.length + "/20";
  }
  ["galleryTitle", "galleryDesc", "galleryAuthor"].forEach(function (id) {
    var el = byId(id);
    if (el) el.addEventListener("input", updateCounts);
  });

  /* ── Tag checkbox limit (max 3) ── */
  var tagChecks = document.querySelectorAll('.gallery-tag-checkbox input[type="checkbox"]');
  tagChecks.forEach(function (cb) {
    cb.addEventListener("change", function () {
      var checked = document.querySelectorAll('.gallery-tag-checkbox input:checked');
      if (checked.length > 3) { this.checked = false; toast("最多选 3 个标签"); }
    });
  });

  /* ── Submit gallery upload ── */
  if (byId("gallerySubmitBtn")) byId("gallerySubmitBtn").addEventListener("click", function () {
    var cur = S.fileList[S.selIdx] || {};
    var src = cur.sourceFile || S.sourceFile;
    if (!src) { toast("请先上传文件"); return; }
    var title = byId("galleryTitle").value.trim() || src.name.replace(/\.[^.]+$/, "");
    var desc = byId("galleryDesc").value.trim();
    var author = byId("galleryAuthor").value.trim();
    var isPrivate = document.querySelector('input[name="galleryVis"]:checked').value;
    var tags = [];
    document.querySelectorAll('.gallery-tag-checkbox input:checked').forEach(function (cb) {
      tags.push(cb.value);
    });
    var fd = new FormData();
    fd.append("source", src);
    fd.append("title", title);
    fd.append("description", desc);
    fd.append("author", author);
    fd.append("tags", JSON.stringify(tags));
    fd.append("is_private", isPrivate);
    var params = { charset: S.charset, width: S.width, height: S.height };
    if (cur.kind === "video") {
      params.kind = "video";
      params.interval = cur.interval || 0.1;
    }
    fd.append("params", JSON.stringify(params));

    this.disabled = true; this.textContent = "发布中...";
    fetch("/api/gallery/upload", { method: "POST", body: fd })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        this.disabled = false; this.textContent = "发布到画廊";
        if (d.error) { toast(d.error); return; }
        closeGalleryModal();
        var url = window.location.origin + d.url;
        // termify 弹窗展示链接（不用浏览器原生 confirm）
        if (navigator.clipboard && navigator.clipboard.writeText) {
          navigator.clipboard.writeText(url).catch(function () {});
        }
        showModal("已发布到画廊！", "短链：" + url + "\n已复制到剪贴板，可直接分享。");
      }.bind(this))
      .catch(function () { this.disabled = false; this.textContent = "发布到画廊"; toast("发布失败"); }.bind(this));
  });
})();
