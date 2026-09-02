(function () {
  "use strict";
  var S = {
    taskId: null, frames: [], htmlFrames: [], interval: 0.1,
    charset: "ascii", totalFrames: 0, width: 80, height: 24,
    wasPlaying: false, fg: null, bg: null, ramp: "",
    colorMode: "mono",  // mono | source（原色：逐字符取源像素真彩）
    colorDepth: "truecolor",  // 原色导出深度：truecolor | 256（老终端兼容）
    srcW: 0, srcH: 0,   // 素材原始宽高（分辨率滑杆行高自动推导用）
    canvasFrames: [], canvasEl: null, canvasCtx: null,
    fileList: [], selIdx: 0, sourceFile: null,
    musicFile: null, musicUploadedFor: null  // T25 背景音乐
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

  /* ── xterm-256 调色板索引 → RGB（16-231 色立方 + 232-255 灰阶），
        供预览解析服务端 source256 回退帧的 38;5/48;5 SGR ── */
  var Q256_LEVELS = [0, 95, 135, 175, 215, 255];
  function xterm256Rgb(idx) {
    idx = Math.max(0, Math.min(255, idx | 0));
    if (idx >= 232) {
      var g = 8 + (idx - 232) * 10;
      return [g, g, g];
    }
    var n = idx - 16;
    return [Q256_LEVELS[Math.floor(n / 36)],
            Q256_LEVELS[Math.floor(n / 6) % 6],
            Q256_LEVELS[n % 6]];
  }

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
        } else if (inner.indexOf("38;5;") === 0) {
          var p = inner.split(";"); var cf = xterm256Rgb(parseInt(p[2], 10));
          fg = "rgb(" + cf[0] + "," + cf[1] + "," + cf[2] + ")";
        } else if (inner.indexOf("48;5;") === 0) {
          var p = inner.split(";"); var cb = xterm256Rgb(parseInt(p[2], 10));
          bg = "rgb(" + cb[0] + "," + cb[1] + "," + cb[2] + ")";
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

    // 固定窗口内适配：cellW 取宽/高两个方向的较小值，画布永远装进预览框。
    // ▀ 编码 2 个纵向像素，cellH = 2 × cellW 保持内容宽高比。
    var tb = preview.parentNode;
    var tbStyle = getComputedStyle(tb);
    var padX = parseFloat(tbStyle.paddingLeft) + parseFloat(tbStyle.paddingRight);
    var padY = parseFloat(tbStyle.paddingTop) + parseFloat(tbStyle.paddingBottom);
    var availW = tb.clientWidth - padX;
    var availH = tb.clientHeight - padY;
    var cellW = Math.max(2, Math.floor(Math.min(availW / cols, availH / (2 * rows))));
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
      terminalTitle.textContent = "动画预览 - " + S.charset + " 风格 / animation preview - " + S.charset + " style";
  }

  /* ── Progress clock（rAF 匀速秒制，像视频播放器） ── */
  function fmtSecs(v) { return v.toFixed(1) + "s"; }
  function updateProgressClock(cycleSec) {
    var n = S.frames.length;
    var totalSec = n * S.interval;
    if (progressFill) progressFill.style.width = (totalSec > 0 ? (cycleSec / totalSec) * 100 : 0) + "%";
    if (frameCounter) frameCounter.textContent = fmtSecs(cycleSec) + " / " + fmtSecs(totalSec);
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
    if (!playing) updateProgressClock((idx + 1) * S.interval);
    setTitleMeta();
  }

  var playStartTs = 0;  // performance.now() 锚点：进度条按真实时间匀速推进

  function rafLoop(ts) {
    if (!playing) return;
    var n = S.frames.length;
    if (n < 2 || S.interval <= 0) { rafId = requestAnimationFrame(rafLoop); return; }
    var totalSec = n * S.interval;
    var cycle = ((ts - playStartTs) / 1000) % totalSec;
    if (cycle < 0) cycle += totalSec;  // 锚点瞬态防御
    updateProgressClock(cycle);
    var idx = Math.floor(cycle / S.interval) % n;
    if (idx !== currentFrame) renderFrame(idx);
    rafId = requestAnimationFrame(rafLoop);
  }

  function startPlayer() {
    if (playing || S.frames.length < 2) return;
    playing = true;
    if (playBtn) playBtn.classList.add("active");
    if (pauseBtn) pauseBtn.classList.remove("active");
    playStartTs = performance.now() - currentFrame * S.interval * 1000;
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
    window.__termifyHasTask = S.frames.length > 0;

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
    syncTerminalHeight();
    fitTerminalFontSize();   // 先定窗口与字号，再画首帧（blocks 画布依赖窗口尺寸）
    renderFrame(0);
    if (S.wasPlaying) { S.wasPlaying = false; startPlayer(); }
  }

  /* ── 固定窗口：高度由 CSS 恒定（min(62vh,500px)），不再锚定侧栏面板 ── */
  function syncTerminalHeight() {
  }

  /* ── 窗口贴合内容：高度按网格固有像素比例（宽定高），字号双向填满 ──
     10:3 系预设（80×24/120×36/160×48/200×60）像素比恒为 1.538，
     窗口随宽度自适应后字号同时满足宽/高两个方向 → 内容 100% 填满；
     换风格不换网格 → 窗口零跳动（40×20 竖比网格以 62vh 封顶后横向留白）。 */
  function fitTerminalFontSize() {
    var tb = document.querySelector(".animation-terminal .terminal-body");
    if (!tb) return;

    var style = getComputedStyle(tb);
    var padX = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
    var padY = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
    var availW = tb.clientWidth - padX;
    if (availW <= 0 || !S.width || !S.height) return;

    var charRatio = 0.6;        // JetBrains Mono 等宽 advance ≈ 0.6em
    var lineHeightRatio = 1.3;
    var gridAspect = (S.width * charRatio) / (S.height * lineHeightRatio);
    var maxH = Math.max(300, Math.round(window.innerHeight * 0.62));
    var idealH = Math.round(availW / gridAspect) + Math.round(padY);
    var boxH = Math.max(240, Math.min(maxH, idealH));
    tb.style.height = boxH + "px";

    var availH = boxH - padY;
    var fs = Math.min(availW / (S.width * charRatio),
                      availH / (S.height * lineHeightRatio));
    fs = Math.max(2, Math.min(fs, 30));  // clamp to sane range
    tb.style.fontSize = fs + "px";
    return fs;
  }

  window.addEventListener("resize", function () {
    syncTerminalHeight();
    fitTerminalFontSize();
    if (S.frames.length) renderFrame(Math.min(currentFrame, S.frames.length - 1));
  });

  /* ── Build color query params ── */
  function colorParams() {
    var p = "";
    if (S.fg) p += "&fg=rgb(" + S.fg[0] + "," + S.fg[1] + "," + S.fg[2] + ")";
    if (S.bg) p += "&bg=rgb(" + S.bg[0] + "," + S.bg[1] + "," + S.bg[2] + ")";
    return p;
  }

  /* ── Request preview from backend ── */
  function requestPreview(charset, opts) {
    // 方案B：本地视频 → JS 分块异步渲染，不经服务器，切换瞬时完成
    if (S.localVideo) {
      if (charset === "custom" && !TermifyRender.sanitizeRamp(S.ramp || "").length) {
        toast("请先在 Tweaks 面板填写有效自定义字符 / Please set a valid custom ramp in Tweaks");
        return;
      }
      var myReq = ++latestReq;
      if (animTerminal) animTerminal.classList.add("rendering");
      localRenderFrames(charset, S.width, S.height, myReq).then(function (localFrames) {
        if (myReq !== latestReq || !localFrames) return;  // 竞态或空帧
        if (animTerminal) animTerminal.classList.remove("rendering");
        S.charset = charset;
        applyPreview({
          frames: localFrames,
          interval: S.localVideo.interval,
          frame_count: localFrames.length,
          charset: charset
        });
      });
      return;
    }
    if (!S.taskId) {
      if (!(opts && opts.silent)) toast("请先上传文件 / Please upload a file first");
      return;
    }
    if (charset === "custom" && !TermifyRender.sanitizeRamp(S.ramp || "").length) {
      toast("请先在 Tweaks 面板填写有效自定义字符 / Please set a valid custom ramp in Tweaks");
      return;
    }
    var myId = ++latestReq;
    // 方案B 推广：服务端任务优先本地渲染（源帧一次性拉取后，风格/尺寸切换零请求）
    if (!taskFramesFailed(S.taskId)) {
      if (animTerminal) animTerminal.classList.add("rendering");
      ensureTaskFrames(S.taskId).then(function (entry) {
        if (myId !== latestReq) return;
        syncSourceAspect(entry);  // 真实素材比例到手 → 重推行高再渲染
        renderTaskFramesLocally(entry, charset, myId);
      }, function () {
        // 404 / 413 / 解码 / 网络失败 → 回退服务端 /api/preview（旧路径）
        if (myId !== latestReq) return;
        serverPreview(charset, opts, myId);
      });
      return;
    }
    serverPreview(charset, opts, myId);
  }

  /* ── 服务端渲染回退路径（方案B 之前的既有流程，完整保留） ── */
  function serverPreview(charset, opts, myId) {
    var url = "/api/preview/" + S.taskId
      + "?charset=" + charset
      + "&width=" + S.width + "&height=" + S.height
      + colorParams();
    if (S.colorMode === "source") url += "&color=" + S.colorMode;
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
      item.title = f.filename + " · 点击下载切换";
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
    applyColumns(S.width, false);  // 同步滑杆/行数 UI（不重渲染）
    S.totalFrames = f.frames_count;
    S.wasPlaying = true;
    markSelected(".style-card", '[data-style="' + S.charset + '"]');
    renderFileList();
    updateMusicCard();
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
      unsupported.forEach(function (f) { toast(f.name + ": 不支持的格式 / Unsupported format"); });
    }

    // Upload images via batch endpoint
    if (images.length) {
      uploadImages(images);
    }

    // Upload videos one at a time (endpoint is single-file)
    videos.forEach(function (v) { importVideoLocal(v); });

    // Fallthrough: nothing to upload
    if (!images.length && !videos.length) return;
  }

  /* ══════════ T23 方案B：浏览器本地视频解码 + JS 渲染（瞬时切换） ══════════
     视频 ` video → 本地抽帧（播放捕获/rVFC）→ ImageBitmap 存内存
     → JS 镜像渲染器（与 Python 同公式）生成 ANSI 帧 → 复用现有预览管线
     风格/分辨率切换纯前端完成；仅下载/分享时才把源文件上传服务器（懒上传）。 */

  var localSeq = 0;
  var LOCAL_MAX_FRAMES = 600;

  function fitDims(vw, vh, maxW, maxH) {
    var scale = Math.min(1, maxW / (vw || maxW), maxH / (vh || maxH));
    return { w: Math.max(2, Math.round((vw || maxW) * scale / 2) * 2),
             h: Math.max(2, Math.round((vh || maxH) * scale / 2) * 2) };
  }

  function captureBySeek(video, fps, total, bitmaps, dims) {
    // 兼容回退：逐时间点 seek + drawImage
    return new Promise(function (resolve, reject) {
      var i = 0;
      var timer = setInterval(function () {
        if (i >= total) {
          clearInterval(timer);
          resolve();
          return;
        }
        video.currentTime = i / fps;
        var fc = document.createElement("canvas");
        fc.width = dims.w;
        fc.height = dims.h;
        fc.getContext("2d").drawImage(video, 0, 0, dims.w, dims.h);
        bitmaps.push(fc);
        setModalProgress((i + 1) / total * 100);
        modalText.textContent = Math.round((i + 1) / total * 100) + "% · " +
          (i + 1) + "/" + total + " 帧（本地解码·seek 模式）";
        i++;
      }, 40);
      video.onerror = function () { clearInterval(timer); reject(new Error("本地解码失败")); };
    });
  }

  function captureByPlayback(video, fps, total, bitmaps, dims) {
    // 主路径：静音 16× 速放 + rVFC 逐呈现帧捕获（硬件解码）
    return new Promise(function (resolve, reject) {
      var lastIdx = -1;
      var watchdog = setTimeout(function () {
        if (bitmaps.length > 0) { video.pause(); resolve(); }
        else reject(new Error("本地解码超时"));
      }, 120000);
      function finish() {
        clearTimeout(watchdog);
        video.pause();
        resolve();
      }
      function onFrame() {
        var idx = Math.min(total - 1, Math.floor(video.currentTime * fps));
        if (idx !== lastIdx && video.currentTime > 0) {
          lastIdx = idx;
          var fc = document.createElement("canvas");
          fc.width = dims.w;
          fc.height = dims.h;
          fc.getContext("2d").drawImage(video, 0, 0, dims.w, dims.h);
          bitmaps.push(fc);
          var pct = Math.min(100, bitmaps.length / total * 100);
          setModalProgress(pct);
          modalText.textContent = Math.round(pct) + "% · " + bitmaps.length + "/" +
            total + " 帧（本地解码）";
        }
        if (bitmaps.length >= total || video.ended) { finish(); return; }
        if (typeof video.requestVideoFrameCallback === "function") {
          video.requestVideoFrameCallback(onFrame);
        } else {
          requestAnimationFrame(onFrame);
        }
      }
      video.onended = finish;
      // rVFC 按显示器刷新率(~60Hz)呈现帧：要捕满 total 帧，
      // 播放速率需让呈现速率 ≥ 采样率 —— 按 时长×55/目标帧数 动态设定（55 留余量）
      video.playbackRate = Math.max(1, Math.min(16, (video.duration || 1) * 55 / total));
      var p = video.play();
      if (p && p.catch) p.catch(function (err) { clearTimeout(watchdog); reject(err); });
      if (typeof video.requestVideoFrameCallback === "function") {
        video.requestVideoFrameCallback(onFrame);
      } else {
        requestAnimationFrame(onFrame);
      }
    });
  }

  function importVideoLocal(file) {
    // 本地解码（不占服务器），失败回退服务器上传
    var url = URL.createObjectURL(file);
    var video = document.createElement("video");
    video.muted = true;
    video.playsInline = true;
    video.preload = "auto";
    video.src = url;
    showModal("本地解码 " + file.name, "读取视频元数据…", false, true);
    setModalProgress(0);
    video.onloadedmetadata = function () {
      var dur = isFinite(video.duration) ? video.duration : 0;
      if (!dur) {
        hideModal();
        toast("本地解码失败，正在用兼容模式重新处理");
        uploadVideo(file);
        return;
      }
      var fps = Math.min(10, 900 / Math.max(0.1, dur));
      var total = Math.max(1, Math.min(LOCAL_MAX_FRAMES, Math.floor(dur * fps)));
      var dims = fitDims(video.videoWidth, video.videoHeight, 400, 240);
      var bitmaps = [];
      var capture = (typeof video.requestVideoFrameCallback === "function")
        ? captureByPlayback(video, fps, total, bitmaps, dims)
        : captureBySeek(video, fps, total, bitmaps, dims);
      capture.then(function () {
        URL.revokeObjectURL(url);
        hideModal();
        registerLocalVideo(file, bitmaps, 1 / fps);
        // 尽力检测视频自带音频（Chromium 特性，仅供即时提示；服务器会权威探测）
        var hasAudio = !!(video.mozHasAudio ||
          (video.audioTracks && video.audioTracks.length > 0) ||
          video.webkitAudioDecodedByteCount > 0);
        if (hasAudio) toast("♪ 检测到视频自带音频，导出时自动合成");
      }).catch(function (err) {
        URL.revokeObjectURL(url);
        hideModal();
        toast("本地解码失败（" + err.message + "），正在用兼容模式重新处理");
        uploadVideo(file);
      });
    };
    video.onerror = function () {
      hideModal();
      toast("本地解码失败，正在用兼容模式重新处理");
      uploadVideo(file);
    };
  }

  function registerLocalVideo(file, bitmaps, interval) {
    localSeq++;
    var lv = {
      bitmaps: bitmaps, interval: interval, filename: file.name,
      file: file, local: true, id: "local:" + localSeq
    };
    if (bitmaps.length && bitmaps[0].width) {
      S.srcW = bitmaps[0].width; S.srcH = bitmaps[0].height;
    }
    var vf = {
      task_id: lv.id, filename: file.name,
      frames_count: bitmaps.length, charset: "ascii", width: 80, height: 24,
      kind: "video", interval: interval, sourceFile: file,
      local: true, localVideo: lv
    };
    S.fileList = S.fileList.concat([vf]);
    S.selIdx = S.fileList.length - 1;
    selectFile(S.selIdx);
    renderFileList();
    toast("导入完成（" + bitmaps.length + " 帧）· 风格切换零等待");
    var stylesSection = document.getElementById("styles");
    if (stylesSection) stylesSection.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /* ── T23 JS 渲染已迁移至 static/js/termify-render.js（与画廊作品页共用） ── */
  // 渲染入参：原色模式下 fg/bg 被忽略（与服务端 color=source 语义一致）
  function renderOpts(width, height) {
    var o = { ramp: S.ramp, fg: S.fg, bg: S.bg,
              cache: lumCacheFor(S.taskId || "local", width, height) };
    if (S.colorMode === "source") { o.colorMode = "source"; o.fg = null; o.bg = null; }
    return o;
  }

  function localRenderFrames(charset, width, height, myReq) {
    var lv = S.localVideo;
    if (!lv || !lv.bitmaps.length) return Promise.resolve(null);
    return TermifyRender.renderFrames(lv.bitmaps, charset, width, height,
      renderOpts(width, height),
      function (done, total) {
        if (done % 60 === 0 && animTerminal) {
          animTerminal.dataset.renderPct = Math.round(done / total * 100) + "%";
        }
      });
  }

  /* ══════════ 方案B 推广：服务端任务源帧本地渲染 ══════════
     上传/链接抓取拿到 task_id 后（selectFile → requestPreview 首次触发）
     懒加载 GET /api/task-frames/<task_id>，base64 JPEG 解码为位图常驻内存；
     之后 7 风格 × 5 尺寸的切换全部在浏览器本地完成（零网络请求）。
     404 / 413 / 解码失败 / 网络失败 → 记忆失败状态（本会话不再重试），
     回退既有服务端 /api/preview 流程（serverPreview，完整保留可用）。 */
  var taskFrameCache = {};   // task_id -> { state: loading|ok|failed|evicted, promise, bitmaps, interval, bytes }
  var lumCaches = {};        // task_id|WxH -> { map, used, budget } 跨风格切换的亮度/RGBA 缓存
  var LUM_CACHE_BUDGET = 48 * 1024 * 1024;
  // 位图常驻内存的字节预算（FIFO 淘汰非当前任务；600 帧 400×240 ≈ 218MB，
  // 不设上限时多任务累积可拖垮低端机）。
  var FRAME_BUDGET_BYTES = 160 * 1024 * 1024;
  var frameBudgetUsed = 0;
  var frameFifo = [];        // 按 task_id 缓存顺序，最旧在前

  function b64ToBitmap(b64) {
    var url = "data:image/jpeg;base64," + b64;
    function viaImage() {
      return new Promise(function (resolve, reject) {
        var img = new Image();
        img.onload = function () { resolve(img); };
        img.onerror = function () { reject(new Error("frame decode failed")); };
        img.src = url;
      });
    }
    if (typeof createImageBitmap !== "function" || typeof fetch !== "function") {
      return viaImage();
    }
    return fetch(url).then(function (r) { return r.blob(); })
      .then(function (b) { return createImageBitmap(b); })
      .catch(viaImage);
  }

  function ensureTaskFrames(taskId) {
    var entry = taskFrameCache[taskId];
    // evicted：位图被预算淘汰（非失败）——重置后重新拉取
    if (entry && entry.state === "evicted") {
      delete taskFrameCache[taskId];
      entry = null;
    }
    if (entry) return entry.promise;
    entry = { state: "loading", bitmaps: [], interval: 0.1, bytes: 0 };
    entry.promise = new Promise(function (resolve, reject) {
      entry.resolve = resolve;
      entry.reject = reject;
    });
    taskFrameCache[taskId] = entry;
    fetch("/api/task-frames/" + taskId)
      .then(function (r) {
        // 404 / 413 / 5xx 一律视为不可本地渲染 → 回退服务端
        if (!r.ok) throw new Error("HTTP " + r.status);
        return r.json();
      })
      .then(function (d) {
        if (!d || !d.ok || !d.frames || !d.frames.length || !d.w || !d.h) {
          throw new Error("empty task frames");
        }
        return Promise.all(d.frames.map(b64ToBitmap)).then(function (bitmaps) {
          var usable = bitmaps.filter(function (b) { return b && b.width; });
          if (!usable.length) throw new Error("frame decode failed");
          entry.bitmaps = usable;
          entry.interval = d.interval || 0.1;
          entry.srcW = d.w; entry.srcH = d.h;  // 消费方按此重推行高
          S.srcW = d.w; S.srcH = d.h;  // 源帧原始尺寸 → 行高自动推导
          entry.state = "ok";
          // 估算常驻字节数并执行 FIFO 预算淘汰（绝不淘汰当前任务）
          entry.bytes = usable.length * d.w * d.h * 4;
          frameBudgetUsed += entry.bytes;
          frameFifo.push(taskId);
          evictFrameCacheOverBudget(taskId);
          entry.resolve(entry);
        });
      })
      .catch(function (err) {
        entry.state = "failed";  // 记忆失败状态
        entry.error = err;
        entry.reject(err);
      });
    return entry.promise;
  }

  function evictFrameCacheOverBudget(activeTaskId) {
    var guard = frameFifo.length;  // 防御：只剩当前任务且仍超预算时不死循环
    while (frameBudgetUsed > FRAME_BUDGET_BYTES && frameFifo.length > 1 && guard-- > 0) {
      var oldest = frameFifo.shift();
      if (oldest === activeTaskId) {
        frameFifo.push(oldest);  // 当前任务不淘汰，轮到下一个
        continue;
      }
      var e = taskFrameCache[oldest];
      if (e && e.state === "ok") {
        frameBudgetUsed -= e.bytes || 0;
        e.bitmaps = [];
        e.bytes = 0;
        e.state = "evicted";  // 之后切换回该任务时自动重新拉取
      }
    }
  }

  function taskFramesFailed(taskId) {
    var e = taskFrameCache[taskId];
    return !!(e && e.state === "failed");
  }

  function lumCacheFor(taskId, width, height) {
    var key = taskId + "|" + width + "x" + height;
    var c = lumCaches[key];
    if (!c) {
      c = { map: {}, used: 0, budget: LUM_CACHE_BUDGET };
      lumCaches[key] = c;
    }
    return c;
  }

  /* selectFile 时 S.srcW/srcH 可能还是 0（首传）或上一个素材的旧值（多文件
     切换），真实尺寸随 task-frames 异步到手——此处重推行高，保证首次渲染
     与导出就用推导后的行数（宽度尊重用户已选列数，只重推行数）。 */
  function syncSourceAspect(entry) {
    if (!entry || !entry.srcW || !entry.srcH) return;
    if (entry.srcW === S.srcW && entry.srcH === S.srcH) return;
    S.srcW = entry.srcW;
    S.srcH = entry.srcH;
    S.height = rowsForCols(S.width);
    var meta = byId("sizeMeta");
    if (meta) {
      meta.textContent = S.width + " × " + S.height + " · 行数按素材比例自动 / rows auto-fit to source";
    }
  }

  function renderTaskFramesLocally(entry, charset, myReq) {
    if (animTerminal) animTerminal.classList.add("rendering");
    TermifyRender.renderFrames(entry.bitmaps, charset, S.width, S.height,
      renderOpts(S.width, S.height),
      function (done, total) {
        if (done % 60 === 0 && animTerminal) {
          animTerminal.dataset.renderPct = Math.round(done / total * 100) + "%";
        }
      }).then(function (localFrames) {
        if (myReq !== latestReq) return;  // 已被更新的请求取代
        if (animTerminal) animTerminal.classList.remove("rendering");
        if (!localFrames || !localFrames.length) {
          serverPreview(charset, {}, myReq);  // 理论不可达的兜底
          return;
        }
        S.charset = charset;
        applyPreview({
          frames: localFrames,
          interval: entry.interval,
          frame_count: localFrames.length,
          charset: charset
        });
      }).catch(function () {
        // 本地渲染异常 → 同样回退服务端（一次性，失败任务已记忆则不再进本地路径）
        if (myReq !== latestReq) return;
        serverPreview(charset, {}, myReq);
      });
  }

  /* 懒上传：本地视频首次下载/分享时才把源文件交给服务器 */
  function uploadVideoXhr(file) {
    return new Promise(function (resolve, reject) {
      var fd = new FormData();
      fd.append("file", file);
      var xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/upload-video");
      xhr.upload.addEventListener("progress", function (e) {
        if (e.lengthComputable) {
          setModalProgress(e.loaded / e.total * 100);
          modalText.textContent = Math.round(e.loaded / e.total * 100) + "% · 上传源文件中";
        }
      });
      xhr.addEventListener("load", function () {
        try { resolve(JSON.parse(xhr.responseText)); }
        catch (err) { reject(new Error("HTTP " + xhr.status)); }
      });
      xhr.addEventListener("error", function () { reject(new Error("网络错误")); });
      xhr.send(fd);
    });
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

  /* ══════════ T25 背景音乐 ══════════
     视频自带音频：服务端上传时自动抽取，MP4/HTML 导出自动合成。
     用户上传音乐：优先于视频原声；本地任务在懒上传时一并交给服务器。 */
  var MUSIC_EXTS = [".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac"];
  var MUSIC_MAX_BYTES = 20 * 1024 * 1024;

  function currentIsVideo() {
    var f = S.fileList[S.selIdx];
    return !!(S.localVideo || (f && (f.kind === "video" || f.localVideo)));
  }

  function updateMusicCard() {
    var card = byId("musicCard");
    if (!card) return;
    card.style.display = currentIsVideo() ? "" : "none";
    refreshMusicRows();
  }

  function refreshMusicRows() {
    var emptyRow = byId("musicEmptyRow"), chosenRow = byId("musicChosenRow"),
        chip = byId("musicChip");
    if (!emptyRow || !chosenRow) return;
    if (S.musicFile) {
      emptyRow.style.display = "none";
      chosenRow.style.display = "flex";
      if (chip) chip.textContent = "♪ " + S.musicFile.name +
        " (" + Math.max(1, Math.round(S.musicFile.size / 1024)) + "KB)";
    } else {
      emptyRow.style.display = "flex";
      chosenRow.style.display = "none";
    }
  }

  function uploadMusicXhr(taskId, file) {
    return new Promise(function (resolve, reject) {
      var fd = new FormData();
      fd.append("task_id", taskId);
      fd.append("file", file);
      var xhr = new XMLHttpRequest();
      xhr.open("POST", "/api/upload-music");
      xhr.addEventListener("load", function () {
        try {
          var d = JSON.parse(xhr.responseText);
          if (xhr.status === 200 && d.ok) resolve(d);
          else reject(new Error(d.error || "HTTP " + xhr.status));
        } catch (err) { reject(new Error("HTTP " + xhr.status)); }
      });
      xhr.addEventListener("error", function () { reject(new Error("网络错误")); });
      xhr.send(fd);
    });
  }

  function removeMusicOnServer(taskId) {
    if (!taskId || String(taskId).indexOf("local:") === 0) return;
    try {
      fetch("/api/remove-music", {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ task_id: taskId })
      });
    } catch (e) { /* best-effort */ }
  }

  function initMusicUI() {
    var addBtn = byId("musicAddBtn"), rmBtn = byId("musicRemoveBtn");
    if (!addBtn) return;
    var input = document.createElement("input");
    input.type = "file";
    input.accept = "audio/mpeg,audio/wav,audio/mp4,audio/aac,audio/ogg,audio/flac,.mp3,.wav,.m4a,.aac,.ogg,.flac";
    input.style.display = "none";
    document.body.appendChild(input);
    addBtn.addEventListener("click", function () { input.click(); });
    input.addEventListener("change", function () {
      var f = input.files && input.files[0];
      if (!f) return;
      var ext = f.name.slice(f.name.lastIndexOf(".")).toLowerCase();
      if (MUSIC_EXTS.indexOf(ext) < 0) {
        toast("不支持的音乐格式（" + ext + "），支持 MP3/WAV/M4A/AAC/OGG/FLAC / Unsupported music format (" + ext + "), supported: MP3/WAV/M4A/AAC/OGG/FLAC");
        input.value = ""; return;
      }
      if (f.size > MUSIC_MAX_BYTES) {
        toast("音乐文件过大（上限 20MB）/ Music file too large (max 20MB)");
        input.value = ""; return;
      }
      // 换音乐 / 换任务：旧音乐若已上传过则从服务器移除
      if (S.musicUploadedFor && S.musicUploadedFor !== (S.taskId || "")) {
        removeMusicOnServer(S.musicUploadedFor);
        S.musicUploadedFor = null;
      }
      S.musicFile = f;
      refreshMusicRows();
      toast("音乐已就绪，导出时自动合成 / Music ready, merged on export");
      input.value = "";
    });
    if (rmBtn) rmBtn.addEventListener("click", function () {
      if (S.musicUploadedFor) removeMusicOnServer(S.musicUploadedFor);
      S.musicFile = null;
      S.musicUploadedFor = null;
      refreshMusicRows();
    });
  }

  /* ── Download ── */
  function doDownload() {
    if (!S.taskId && !S.localVideo) { toast("请先上传文件 / Please upload a file first"); return; }
    if (S.charset === "custom" && !TermifyRender.sanitizeRamp(S.ramp || "").length) {
      toast("请先在 Tweaks 面板填写有效自定义字符 / Please set a valid custom ramp in Tweaks");
      return;
    }
    var fmt = selectedFormat();
    function ensureMusicUploaded(taskId) {
      // 音乐随首次导出一并交给服务器（懒上传），已传过则跳过
      if (!S.musicFile || S.musicUploadedFor === taskId) {
        return Promise.resolve();
      }
      showModal("正在上传背景音乐", S.musicFile.name, false, true);
      setModalProgress(0);
      return uploadMusicXhr(taskId, S.musicFile).then(function () {
        S.musicUploadedFor = taskId;
        hideModal();
      }, function (err) {
        hideModal();
        toast("音乐上传失败（" + err.message + "），本次导出不含自定义音乐");
        // 不阻塞导出：服务器会退回视频自带音频（如有）
      });
    }
    function generateWithTask(taskId) {
      var body = {
        task_id: taskId, charset: S.charset, format: fmt,
        width: S.width, height: S.height
      };
      if (S.charset === "custom") body.chars = S.ramp;
      if (S.colorMode === "source") {
        body.color = (S.colorDepth === "256") ? "source256" : "source";
      } else {
        if (S.fg) body.fg = "rgb(" + S.fg[0] + "," + S.fg[1] + "," + S.fg[2] + ")";
        if (S.bg) body.bg = "rgb(" + S.bg[0] + "," + S.bg[1] + "," + S.bg[2] + ")";
      }
      if (fmt === "mp4") {
        // 实测 ~100k 字符格/秒（字节合成 + x264），加 3s 编码固定开销
        var est = Math.max(5, Math.round((S.totalFrames || 1) * S.width * S.height / 100000) + 3);
        showModal("正在导出 MP4 视频", "预计约 " + est + " 秒，完成后自动下载…");
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
    function generateWithMusic(taskId) {
      ensureMusicUploaded(taskId).then(function () { generateWithTask(taskId); });
    }
    var needsUpload = S.localVideo &&
      (!S.taskId || String(S.taskId).indexOf("local:") === 0);
    if (needsUpload) {
      // 懒上传：本地视频首次下载/分享时才把源文件交给服务器
      showModal("正在准备导出素材", "首次下载需要处理源视频，请稍候…", false, true);
      setModalProgress(0);
      uploadVideoXhr(S.localVideo.file).then(function (d) {
        hideModal();
        if (d.error) { toast(d.error); return; }
        S.taskId = d.task_id;
        var cur = S.fileList[S.selIdx];
        if (cur) cur.task_id = d.task_id;
        if (d.has_audio) toast("♪ 已检测到视频自带音频，将合成进导出文件");
        generateWithMusic(S.taskId);
      }).catch(function (e) {
        hideModal();
        toast("上传失败: " + e);
      });
      return;
    }
    generateWithMusic(S.taskId);
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
      if (s === "custom" && !S.ramp) {
        openCustomCharsetModal();  // 没填过字符 → 打开引导编辑器
      } else {
        requestPreview(s, { silent: !S.taskId });
      }
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
      if (downloadBtn) downloadBtn.innerHTML = svg + "下载动画文件 / Download animation";
    });
  });

  /* ══════════ 分辨率：列数滑杆 + 行高按素材比例自动推导 ══════════
     服务端宽高钳制 1-400；行数 = 列数 × 素材高宽比 ÷ 2（字符格 1:2），
     钳到 8-240。无源尺寸信息时按 2:1 网格退化（rows ≈ cols/2）。 */
  function rowsForCols(cols) {
    var ar = (S.srcW && S.srcH) ? (S.srcH / S.srcW) : 0.5;
    var rows = TermifyRender.pyRound(cols * ar / 2);
    return Math.max(8, Math.min(240, rows));
  }

  function applyColumns(cols, rerender) {
    cols = Math.max(20, Math.min(400, cols));
    S.width = cols;
    S.height = rowsForCols(cols);
    if (animTerminal) animTerminal.dataset.size = String(cols);
    var slider = byId("sizeSlider");
    if (slider && parseInt(slider.value, 10) !== cols) slider.value = String(cols);
    var colsVal = byId("sizeColsVal");
    if (colsVal) colsVal.textContent = String(cols);
    var meta = byId("sizeMeta");
    if (meta) {
      meta.textContent = cols + " × " + S.height + " · 行数按素材比例自动 / rows auto-fit to source";
    }
    var warn = byId("sizeWarn");
    if (warn) warn.hidden = cols <= 320;
    try { localStorage.setItem("termify_cols", String(cols)); } catch (e) {}
    if (rerender && S.taskId) requestPreview(S.charset);
  }

  qa(".size-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      qa(".size-btn").forEach(function (b) { b.classList.remove("selected"); });
      btn.classList.add("selected");
      applyColumns(parseInt(btn.getAttribute("data-cols"), 10) || 80, true);
      if (!S.taskId) { toast("切换尺寸将在上传后应用 / Size applies after upload"); }
    });
  });

  (function initSizeSlider() {
    var slider = byId("sizeSlider");
    if (!slider) return;
    var pendingCols = null;
    // 拖动中只更新数字（避免高频重渲染），松手（change）才真正重渲染
    slider.addEventListener("input", function () {
      var cols = parseInt(slider.value, 10) || 80;
      qa(".size-btn").forEach(function (b) {
        b.classList.toggle("selected", parseInt(b.getAttribute("data-cols"), 10) === cols);
      });
      var colsVal = byId("sizeColsVal");
      if (colsVal) colsVal.textContent = String(cols);
      var warn = byId("sizeWarn");
      if (warn) warn.hidden = cols <= 320;
      S.width = cols;
      S.height = rowsForCols(cols);
      var meta = byId("sizeMeta");
      if (meta) meta.textContent = cols + " × " + S.height + " · 行数按素材比例自动 / rows auto-fit to source";
      pendingCols = cols;
    });
    slider.addEventListener("change", function () {
      if (pendingCols === null) return;
      applyColumns(pendingCols, true);
      pendingCols = null;
      if (!S.taskId) { toast("切换尺寸将在上传后应用 / Size applies after upload"); }
    });
    // 恢复上次会话的列数偏好
    var saved = null;
    try { saved = parseInt(localStorage.getItem("termify_cols"), 10); } catch (e) {}
    if (saved && saved >= 20 && saved <= 400 && saved !== 80) {
      qa(".size-btn").forEach(function (b) { b.classList.remove("selected"); });
      applyColumns(saved, false);
    }
  })();

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
      urlBtn.disabled = true; urlBtn.textContent = "下载中… / Fetching…";
      fetch("/api/fetch-url", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url: url })
      }).then(function (r) { return r.json(); }).then(function (d) {
        urlBtn.disabled = false; urlBtn.textContent = "下载 / Fetch";
        if (d.error) { toast(d.error); return; }
        S.fileList = [{ task_id: d.task_id, filename: d.filename || "url-image",
                        frames_count: d.frames_count, charset: "ascii", width: 80, height: 24 }];
        S.selIdx = 0; selectFile(0); renderFileList();
        var stylesSection = byId("styles");
        if (stylesSection) stylesSection.scrollIntoView({ behavior: "smooth", block: "start" });
        urlInput.value = "";
      }).catch(function (e) {
        urlBtn.disabled = false; urlBtn.textContent = "下载 / Fetch";
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

  /* ══════════ 配色面板：一次选择驱动预览 chrome + 字符色 + 导出 ══════════
     fg/bg 进导出链路（S.fg/S.bg 或 colorMode=source）；--green 系 CSS 变量
     同步预览窗口主题——根治"预览绿、导出黑白"的两套颜色系统。 */
  var PALETTES = {
    mono:   { fg: "#ffffff", bg: "#0a0e14", main: "#e6edf3", dim: "#b0b8c4", glow: "rgba(230,237,243,0.10)" },
    green:  { fg: "#00ff41", bg: "#0a0e14", main: "#00ff41", dim: "#00cc33", glow: "rgba(0,255,65,0.15)" },
    amber:  { fg: "#ffb000", bg: "#140d02", main: "#ffb000", dim: "#cc8d00", glow: "rgba(255,176,0,0.12)" },
    ice:    { fg: "#00d4ff", bg: "#04121f", main: "#00d4ff", dim: "#00a8cc", glow: "rgba(0,212,255,0.12)" },
    source: { fg: null, bg: null, main: "#c9d1d9", dim: "#8b949e", glow: "rgba(201,209,217,0.10)" }
  };

  function applyPaletteChrome(main, dim, glow) {
    document.documentElement.style.setProperty("--green", main);
    document.documentElement.style.setProperty("--green-dim", dim);
    document.documentElement.style.setProperty("--green-glow", glow);
  }

  function markPalette(key) {
    qa(".palette-btn").forEach(function (b) {
      b.classList.toggle("active", b.getAttribute("data-palette") === key);
    });
  }

  function applyPalette(key, rerender) {
    var customRow = byId("customColorRow");
    var q256Row = byId("q256Row");
    S.colorMode = "mono";
    if (key === "source") {
      S.colorMode = "source";  // 逐字符取源像素真彩（本地渲染 + 导出 color=source）
      var p = PALETTES.source;
      S.fg = null; S.bg = null;
      applyPaletteChrome(p.main, p.dim, p.glow);
      if (customRow) customRow.hidden = true;
      if (q256Row) q256Row.hidden = false;
    } else if (key === "custom") {
      if (customRow) customRow.hidden = false;
      if (q256Row) q256Row.hidden = true;
      var fp = byId("fgColorPicker"), bp = byId("bgColorPicker");
      var m = fp ? fp.value : "#ffffff";
      S.fg = hexToRgb(m);
      S.bg = hexToRgb(bp ? bp.value : "#0a0e14");
      applyPaletteChrome(m, m, "rgba(255,255,255,0.10)");
    } else {
      var c = PALETTES[key] || PALETTES.mono;
      S.fg = hexToRgb(c.fg);
      S.bg = hexToRgb(c.bg);
      applyPaletteChrome(c.main, c.dim, c.glow);
      if (customRow) customRow.hidden = true;
      if (q256Row) q256Row.hidden = true;
    }
    markPalette(key);
    try { localStorage.setItem("termify_palette", key); } catch (e) {}
    if (rerender && S.taskId) requestPreview(S.charset);
    else if (rerender) toast("配色已应用，上传后生效 / Palette applies after upload");
  }

  qa(".palette-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      applyPalette(btn.getAttribute("data-palette") || "mono", true);
    });
  });

  /* ── 自定义前景/背景取色器（配色面板「自定义」展开区） ── */
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
    if (fgPicker) fgPicker.value = "#ffffff";
    if (bgPicker) bgPicker.value = "#0a0e14";
    if (S.taskId) requestPreview(S.charset);
  });

  /* ── 原色模式的 py 256 色兼容选项 ── */
  var q256Toggle = byId("q256Toggle");
  if (q256Toggle) {
    try {  // 色深偏好与配色一起持久化，刷新后导出色深不漂移
      if (localStorage.getItem("termify_color_depth") === "256") {
        q256Toggle.checked = true;
        S.colorDepth = "256";
      }
    } catch (e) {}
    q256Toggle.addEventListener("change", function () {
      S.colorDepth = q256Toggle.checked ? "256" : "truecolor";
      try { localStorage.setItem("termify_color_depth", S.colorDepth); } catch (e) {}
    });
  }

  // 恢复上次会话的配色偏好；默认「磷光绿」——预览与导出从第一帧起同色
  // （历史 bug：预览 chrome 绿而导出无色，根因即默认不进导出链路）。
  (function initPalette() {
    var saved = null;
    try { saved = localStorage.getItem("termify_palette"); } catch (e) {}
    if (saved && (saved === "mono" || saved === "green" || saved === "amber" ||
                  saved === "ice" || saved === "source" || saved === "custom")) {
      applyPalette(saved, false);
    } else {
      applyPalette("green", false);
    }
  })();

  /* ── T20: custom charset ramp ── */
  var rampInput = byId("customRampInput");
  var rampApply = byId("customRampApply");

  // 自定义字符集编辑弹窗（引导式 + 预设字符库）
  var PALETTE_GROUPS = [
    { name: "块元素", chars: "█▓▒░▄▀▌▐■□▪▫" },
    { name: "盲文点", chars: "⠁⠂⠄⡀⠈⠐⠠⢀⠋⠛⣶⣿" },
    { name: "几何", chars: "●○◉◌◆◇▲△▼▽◭◮" },
    { name: "线条", chars: "─━│┃┌┐└┘├┤┬┴┼═║" },
    { name: "星与花", chars: "★☆✦✧✱✲✳✴✵✶❋❀" },
    { name: "点与圈", chars: "·•∘○◌⊙⊚⊛" },
    { name: "箭头", chars: "←↑→↓↖↗↘↙⇐⇒" },
    { name: "密集符号", chars: "@#%&$8BWM0O" },
    { name: "稀疏符号", chars: "=+~-:;·^\"',." },
  ];
  var ccModal = byId("customCharsetModal");
  var ccInput = byId("ccRampInput");
  var ccStrip = byId("ccPreviewStrip");
  var ccPalette = byId("ccPalette");

  function refreshCcPreview() {
    if (!ccStrip) return;
    var ramp = (ccInput.value || "").replace(/\s+/g, "");
    var dedup = [];
    var seen = {};
    for (var i = 0; i < ramp.length; i++) {
      if (!seen[ramp[i]]) { seen[ramp[i]] = 1; dedup.push(ramp[i]); }
    }
    ccStrip.textContent = dedup.join(" ") || "（空）";
  }

  function openCustomCharsetModal() {
    if (!ccModal) return;
    ccInput.value = S.ramp || rampInput.value || "@%#*+=-:.";
    refreshCcPreview();
    modal.hidden = true;
    ccModal.hidden = false;
    ccInput.focus();
  }
  function closeCustomCharsetModal() {
    if (ccModal) ccModal.hidden = true;
  }
  function applyCustomCharset() {
    var v = (ccInput.value || "").trim();
    if (!v) { toast("字符梯不能为空"); return; }
    S.ramp = v;
    if (rampInput) rampInput.value = v;
    closeCustomCharsetModal();
    if (S.taskId) requestPreview("custom");
    else showStyleDemo("custom");
  }
  (function buildCcPalette() {
    if (!ccPalette) return;
    PALETTE_GROUPS.forEach(function (group) {
      var row = document.createElement("div");
      row.className = "cc-palette-group";
      var label = document.createElement("span");
      label.className = "cc-palette-name";
      label.textContent = group.name;
      row.appendChild(label);
      Array.prototype.forEach.call(group.chars, function (ch) {
        var chip = document.createElement("button");
        chip.type = "button";
        chip.className = "cc-chip";
        chip.textContent = ch;
        chip.addEventListener("click", function () {
          if (ccInput.value.indexOf(ch) >= 0) return;  // 已在梯中，去重
          ccInput.value += ch;
          refreshCcPreview();
        });
        row.appendChild(chip);
      });
      ccPalette.appendChild(row);
    });
  })();
  if (ccInput) ccInput.addEventListener("input", refreshCcPreview);
  var ccApplyBtn = byId("ccApplyBtn");
  var ccCancelBtn = byId("ccCancelBtn");
  var ccClose = byId("customCharsetClose");
  var ccBackdrop = byId("customCharsetBackdrop");
  if (ccApplyBtn) ccApplyBtn.addEventListener("click", applyCustomCharset);
  if (ccCancelBtn) ccCancelBtn.addEventListener("click", closeCustomCharsetModal);
  if (ccClose) ccClose.addEventListener("click", closeCustomCharsetModal);
  if (ccBackdrop) ccBackdrop.addEventListener("click", closeCustomCharsetModal);
  if (ccInput) ccInput.addEventListener("keydown", function (e) {
    if (e.key === "Enter") applyCustomCharset();
  });

  function applyRamp() {
    var v = rampInput ? rampInput.value.trim() : "";
    if (!v) { openCustomCharsetModal(); return; }  // 没填过 → 打开引导弹窗
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
    if (!(cur.sourceFile || S.sourceFile)) { toast("请先上传文件 / Please upload a file first"); return; }
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
    var cur = S.fileList[idx];
    // 必须在 _origSelectFile（内部会触发 requestPreview）之前设置，
    // 否则本地分支首切永远走服务端路径 → "Task not found"
    S.localVideo = (cur && cur.localVideo) || null;
    // 换任务 → 上一任务的亮度缓存全部失效（内存有界）
    lumCaches = {};
    _origSelectFile(idx);
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
    if (!src) { toast("请先上传文件 / Please upload a file first"); return; }
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
    if (S.colorMode === "source") {
      params.color = (S.colorDepth === "256") ? "source256" : "source";
    } else {
      if (S.fg) params.fg = S.fg;
      if (S.bg) params.bg = S.bg;
    }
    if (cur.kind === "video") {
      params.kind = "video";
      params.interval = cur.interval || 0.1;
    }
    fd.append("params", JSON.stringify(params));
    var shareHasMusic = cur.kind === "video" && S.musicFile;
    if (shareHasMusic) fd.append("music", S.musicFile);

    this.disabled = true; this.textContent = shareHasMusic ? "发布中（含音乐）..." : "发布中...";
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

  initMusicUI();
  updateMusicCard();
})();
