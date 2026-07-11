(function () {
  "use strict";
  var S = { taskId: null, frames: [], interval: 0.1, charset: "ascii", totalFrames: 0, width: 80, height: 24 };
  var latestReq = 0;
  var currentFrame = 0, playing = false, tickHandle = null;
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
  function byId(id){ return document.getElementById(id); }
  function qa(s){ return document.querySelectorAll(s); }
  var toastTimer = null;
  function toast(msg) {
    var el = byId("toast"); if (!el) return;
    el.textContent = msg; el.classList.add("show");
    if (toastTimer !== null) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove("show"); }, 2200);
  }
  function setTitleMeta() {
    if (terminalTitle && S.charset) terminalTitle.textContent = "animation preview - " + S.charset + " style";
  }
  function renderFrame(idx) {
    if (!preview || !S.frames.length) return;
    if (idx < 0) idx = 0; if (idx >= S.frames.length) idx = S.frames.length - 1;
    preview.textContent = S.frames[idx].join("\n");
    currentFrame = idx;
    var n = S.frames.length;
    if (progressFill) progressFill.style.width = ((idx + 1) / n) * 100 + "%";
    if (frameCounter) frameCounter.textContent = (idx + 1) + " / " + n;
    setTitleMeta();
  }
  function tick() { if (!S.frames.length) return; renderFrame((currentFrame + 1) % S.frames.length); }
  function startPlayer() {
    if (tickHandle !== null || S.frames.length < 2) return;
    playing = true;
    if (playBtn) playBtn.classList.add("active");
    if (pauseBtn) pauseBtn.classList.remove("active");
    tickHandle = setInterval(tick, S.interval * 1000);
  }
  function pausePlayer() {
    playing = false;
    if (playBtn) playBtn.classList.remove("active");
    if (pauseBtn) pauseBtn.classList.add("active");
    if (tickHandle !== null) { clearInterval(tickHandle); tickHandle = null; }
  }
  function applyPreview(d) {
    S.frames = d.frames || [];
    S.interval = d.interval || 0.1;
    S.totalFrames = d.frame_count || S.frames.length;
    setTitleMeta(); renderFrame(0);
  }
  function requestPreview(charset) {
    if (!S.taskId) { toast("请先上传文件"); return; }
    var myId = ++latestReq;
    var url = "/api/preview/" + S.taskId + "?charset=" + charset + "&width=" + S.width + "&height=" + S.height;
    fetch(url).then(function (r) { return r.json(); }).then(function (d) {
      if (myId !== latestReq) return;
      if (d.error) { toast(d.error); return; }
      S.charset = charset; applyPreview(d);
      if (playing) { if (tickHandle !== null) { clearInterval(tickHandle); tickHandle = null; } startPlayer(); }
    }).catch(function () { if (myId !== latestReq) return; toast("preview failed"); });
  }
  function handleFile(file) {
    if (!file) return;
    var fd = new FormData(); fd.append("file", file);
    if (uploadZone) uploadZone.classList.add("uploading");
    fetch("/api/upload", { method: "POST", body: fd })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (uploadZone) uploadZone.classList.remove("uploading");
        if (d.error) { toast(d.error); return; }
        S.taskId = d.task_id; S.totalFrames = d.frames_count;
        markSelected(".style-card", '[data-style="ascii"]');
        S.width = 80; S.height = 24; requestPreview("ascii");
      })
      .catch(function (e) { if (uploadZone) uploadZone.classList.remove("uploading"); toast("upload failed: " + e); });
  }
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
  function doDownload() {
    if (!S.taskId) { toast("请先上传文件"); return; }
    var fmt = selectedFormat();
    if (fmt === "mcu") { toast("MCU 输出即将在 v2 支持"); return; }
    fetch("/api/generate", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ task_id: S.taskId, charset: S.charset, format: fmt, width: S.width, height: S.height })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d.error) { toast(d.error); return; }
      window.location.href = d.download_url;
    }).catch(function (e) { toast("download failed: " + e); });
  }
  qa(".style-card").forEach(function (card) {
    card.addEventListener("click", function () {
      qa(".style-card").forEach(function (c) { c.classList.remove("selected"); });
      card.classList.add("selected");
      var s = card.getAttribute("data-style"); requestPreview(s);
    });
  });
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
  qa(".size-btn").forEach(function (btn) {
    btn.addEventListener("click", function () {
      qa(".size-btn").forEach(function (b) { b.classList.remove("selected"); });
      btn.classList.add("selected");
      var m = (btn.textContent || "").match(/(\d+)\s*[x×]\s*(\d+)/);
      if (m) { S.width = parseInt(m[1], 10); S.height = parseInt(m[2], 10); }
      if (S.taskId) { if (tickHandle !== null) { clearInterval(tickHandle); tickHandle = null; } requestPreview(S.charset); }
      else toast("切换尺寸将在上传后应用");
    });
  });
  [FB, ".mcu-res-btn"].forEach(function (s) {
    qa(s).forEach(function (btn) {
      btn.addEventListener("click", function () {
        qa(s).forEach(function (b) { b.classList.remove("selected"); });
        btn.classList.add("selected");
      });
    });
  });
  if (playBtn) playBtn.addEventListener("click", startPlayer);
  if (pauseBtn) pauseBtn.addEventListener("click", pausePlayer);
  if (progressBar) progressBar.addEventListener("click", function (e) {
    var r = progressBar.getBoundingClientRect();
    var x = Math.min(1, Math.max(0, (e.clientX - r.left) / r.width));
    if (tickHandle !== null) { clearInterval(tickHandle); tickHandle = null; }
    renderFrame(Math.round(x * (S.frames.length - 1)));
  });
  if (downloadBtn) downloadBtn.addEventListener("click", doDownload);
  var copyBtn = document.querySelector(".share-link button");
  if (copyBtn) copyBtn.addEventListener("click", function () { toast("分享功能即将上线"); });
  var fileInput = (function () {
    var f = document.createElement("input"); f.type = "file";
    f.accept = "image/gif,image/png,image/jpeg"; f.style.display="none";
    document.body.appendChild(f); return f;
  })();
  if (uploadZone) {
    uploadZone.addEventListener("click", function (e) {
      if (e.target && e.target.closest && e.target.closest(".upload-formats")) return;
      fileInput.click();
    });
    fileInput.addEventListener("change", function () { if (fileInput.files.length) handleFile(fileInput.files[0]); });
    uploadZone.addEventListener("drop", function (e) {
      e.preventDefault(); uploadZone.classList.remove("drag-over");
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) handleFile(e.dataTransfer.files[0]);
    });
    uploadZone.addEventListener("dragover", function (e) { e.preventDefault(); uploadZone.classList.add("drag-over"); });
    uploadZone.addEventListener("dragleave", function () { uploadZone.classList.remove("drag-over"); });
  }
  var tweaksToggle = byId("tweaksToggle"), tweaksPanel = byId("tweaksPanel"), tweaksClose = byId("tweaksClose");
  if (tweaksToggle) tweaksToggle.addEventListener("click", function () {
    tweaksPanel.classList.toggle("open"); tweaksToggle.style.display = tweaksPanel.classList.contains("open") ? "none" : "flex";
  });
  if (tweaksClose) tweaksClose.addEventListener("click", function () {
    tweaksPanel.classList.remove("open"); tweaksToggle.style.display = "flex";
  });
  function bindTweak(onSel, offSel, prop, onVal, offVal) {
    qa(onSel).forEach(function (btn) { btn.addEventListener("click", function () {
      document.body.style.setProperty(prop, onVal); qa(onSel + "," + offSel).forEach(function (b) { b.classList.remove("active"); }); btn.classList.add("active"); }); });
    qa(offSel).forEach(function (btn) { btn.addEventListener("click", function () {
      document.body.style.setProperty(prop, offVal); qa(onSel + "," + offSel).forEach(function (b) { b.classList.remove("active"); }); btn.classList.add("active"); }); });
  }
  bindTweak('[data-tweak="grid-on"]', '[data-tweak="grid-off"]', "--grid-opacity", "0.3", "0");
  bindTweak('[data-tweak="scan-on"]', '[data-tweak="scan-off"]', "--scanline-opacity", "1", "0");
  qa('[data-tweak^="theme-"]').forEach(function (btn) {
    btn.addEventListener("click", function () {
      qa('[data-tweak^="theme-"]').forEach(function (b) { b.classList.remove("active"); });
      btn.classList.add("active");
      var theme = btn.getAttribute("data-tweak").replace("theme-", "");
      var colors = { green: { main: "#00ff41", dim: "#00cc33", glow: "rgba(0,255,65,0.15)" },
        amber: { main: "#ffb000", dim: "#cc8d00", glow: "rgba(255,176,0,0.12)" },
        cyan: { main: "#00d4ff", dim: "#00a8cc", glow: "rgba(0,212,255,0.12)" } };
      var c = colors[theme]; if (!c) return;
      document.documentElement.style.setProperty("--green", c.main);
      document.documentElement.style.setProperty("--green-dim", c.dim);
      document.documentElement.style.setProperty("--green-glow", c.glow);
    });
  });
  setTitleMeta();
})();
