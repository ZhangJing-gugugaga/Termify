/* Termify · 文字艺术独立页（/text-art）
   单入口自动路由：中文输入 → TTF 点阵（/api/text/convert 服务端分流），
   英文/数字 → FIGlet。无 LLM。
   UX 契约：所有异步动作都有等待反馈（按钮 busy / 骨架）。 */
(function () {
  "use strict";

  function byId(id) { return document.getElementById(id); }

  /* ── Toast ── */
  var toastTimer = null;
  function toast(msg) {
    var el = byId("toast");
    if (!el) return;
    el.textContent = msg;
    el.classList.add("show");
    if (toastTimer !== null) clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.classList.remove("show"); }, 2200);
  }

  /* ── Termify 弹窗（成功通知 / 错误提示） ── */
  var modal = byId("termifyModal");
  function showModal(title, text, isError) {
    if (!modal) return;
    modal.hidden = false;
    modal.classList.toggle("error", !!isError);
    var spinner = byId("termifyModalSpinner");
    if (spinner) spinner.style.display = isError ? "none" : "block";
    var t = byId("termifyModalTitle"), x = byId("termifyModalText");
    if (t) t.textContent = title;
    if (x) x.textContent = text || "";
  }
  var modalClose = byId("termifyModalClose");
  if (modalClose) modalClose.addEventListener("click", function () { modal.hidden = true; });

  /* ── 状态 ── */
  var TA = { art: "", cols: 0, rows: 0, font: "", text: "", fg: [51, 255, 51] };
  var taInput = byId("taInput");
  var taFont = byId("taFont");
  var taOutput = byId("taOutput");
  var taResultMeta = byId("taResultMeta");
  var taMetaText = byId("taMetaText");
  var taPreviewTitle = byId("taPreviewTitle");
  var taFontHint = byId("taFontHint");
  var busy = false;

  function skeleton() {
    if (!taOutput) return;
    taOutput.classList.remove("has-art");
    taOutput.innerHTML = '<div class="ta-skeleton">' +
      '<div class="ta-skeleton-line" style="width:72%"></div>' +
      '<div class="ta-skeleton-line" style="width:88%"></div>' +
      '<div class="ta-skeleton-line" style="width:60%"></div>' +
      '<div class="ta-skeleton-line" style="width:80%"></div>' +
      '<div class="ta-skeleton-line" style="width:46%"></div>' +
      "</div>";
  }
  function showOutputError(msg) {
    if (!taOutput) return;
    taOutput.classList.remove("has-art");
    taOutput.innerHTML = "";
    var el = document.createElement("div");
    el.className = "ta-error";
    el.textContent = msg;
    taOutput.appendChild(el);
    if (taPreviewTitle) taPreviewTitle.textContent = "text art";
    // 错误时保留上一作品的导出操作（复制 ANSI / 下载等不因一次报错消失），
    // 仅在从未生成过作品时才整体隐藏。
    if (taResultMeta) taResultMeta.hidden = !TA.art;
    if (taResultMeta && TA.art && taMetaText) {
      taMetaText.textContent = "上一作品仍可导出 · " + TA.cols + " x " +
        TA.rows + "（本次生成失败：" + msg.split(" / ")[0] + "）";
    }
  }

  /* 示例 chips：覆盖英文 / 中文两条路径 */
  var EXAMPLES = [
    { kind: "figlet", label: "hello", value: "hello" },
    { kind: "figlet", label: "TERMIFY", value: "TERMIFY" },
    { kind: "cjk", label: "你好世界", value: "你好世界" },
    { kind: "cjk", label: "龙腾", value: "龙腾" }
  ];

  function showEmpty() {
    if (!taOutput) return;
    taOutput.classList.remove("has-art");
    var chips = EXAMPLES.map(function (ex) {
      return '<button type="button" class="ta-chip" data-kind="' + ex.kind +
        '" data-value="' + ex.value.replace(/"/g, "&quot;") + '">' +
        ex.label + "</button>";
    }).join("");
    taOutput.innerHTML = '<div class="ta-empty-wrap">' +
      '<div class="ta-empty"><span class="ta-empty-prompt">等待生成</span>' +
      '<span class="ta-empty-caret" aria-hidden="true"></span></div>' +
      '<div class="ta-chips">' + chips + "</div></div>";
    if (taResultMeta) taResultMeta.hidden = true;
  }
  function showArt(d) {
    TA.art = d.art; TA.cols = d.cols; TA.rows = d.rows;
    TA.font = d.font || ""; TA.text = d.text || "";
    if (!taOutput) return;
    taOutput.classList.add("has-art");
    taOutput.textContent = d.art;
    var modeLabel = d.mode === "cjk" ? "中文点阵 · " + (d.font || "")
      : (d.font || "figlet");
    if (taPreviewTitle) {
      taPreviewTitle.textContent = "text art · " + modeLabel +
        " " + d.cols + "x" + d.rows;
    }
    if (taMetaText) taMetaText.textContent = modeLabel + " · " + d.cols + " x " + d.rows;
    if (taResultMeta) taResultMeta.hidden = false;
    if (taThemeRow) taThemeRow.hidden = false;  // 有作品 → 配色行可见
  }

  /* ── 输入语言检测 + 字体源切换 ── */
  function hasCJK(v) {
    for (var i = 0; i < v.length; i++) {
      var c = v.charCodeAt(i);
      if (c >= 0x4E00 && c <= 0x9FFF || c >= 0x3400 && c <= 0x4DBF) return true;
    }
    return false;
  }

  /* 字体表缓存：figlet（slug/name）与 cjk（slug/name/available）两套 */
  var FIGLET_FONTS = [];
  var CJK_FONTS = [];
  var fontSource = "figlet";  // 当前左栏 select 展示的字体源

  function fillFontSelect() {
    if (!taFont) return;
    var list = fontSource === "cjk" ? CJK_FONTS : FIGLET_FONTS;
    var prev = taFont.value;
    taFont.innerHTML = "";
    list.forEach(function (f) {
      var o = document.createElement("option");
      o.value = f.slug;
      o.textContent = f.available === false ? f.name + "（不可用）" : f.name;
      if (f.available === false) o.disabled = true;
      taFont.appendChild(o);
    });
    taFont.disabled = false;
    // 恢复之前选中的字体（若仍存在），否则选默认项
    if (prev && taFont.querySelector('option[value="' + prev + '"]:not([disabled])')) {
      taFont.value = prev;
    } else if (fontSource === "figlet" &&
               taFont.querySelector('option[value="standard"]')) {
      taFont.value = "standard";  // 与服务端 DEFAULT_FONT 对齐
    } else {
      var first = taFont.querySelector("option:not([disabled])");
      if (first) taFont.value = first.value;
    }
  }

  function setFontSource(source) {
    if (fontSource === source) return;
    fontSource = source;
    fillFontSelect();
    if (cjkHeightGroup) cjkHeightGroup.hidden = source !== "cjk";
    if (taFontHint) {
      taFontHint.textContent = source === "cjk"
        ? "中文点阵：选择汉字字形（系统字体光栅化）。"
        : "FIGlet 精选 " + FIGLET_FONTS.length + " 款字体，选择作品的整体字形。";
    }
  }

  function loadFonts() {
    fetch("/api/text/fonts").then(function (r) { return r.json(); }).then(function (d) {
      if (d.ok && d.fonts) FIGLET_FONTS = d.fonts;
      if (fontSource === "figlet") fillFontSelect();
    }).catch(function () {
      if (taFontHint) taFontHint.textContent = "字体加载失败，请刷新页面重试。";
    });
    fetch("/api/cjk/ttf/fonts").then(function (r) { return r.json(); }).then(function (d) {
      if (d.ok && d.fonts) CJK_FONTS = d.fonts;
      if (fontSource === "cjk") fillFontSelect();
    }).catch(function () { /* 中文下拉留空，后端会兜底默认字体 */ });
  }

  var FIGLET_MAX_CHARS = 64;  // 与服务端 textart.TEXT_MAX_CHARS 一致
  var CJK_MAX_CHARS = 8;      // 与服务端 textart.CJK_MAX_CHARS 一致
  var taInputHint = byId("taInputHint");

  function syncInputHint() {
    if (!taInput || !taInputHint) return;
    var v = taInput.value;
    if (!v.trim()) {
      taInputHint.hidden = true;
      taInputHint.classList.remove("ta-hint-warn");
      setFontSource("figlet");
      return;
    }
    if (hasCJK(v)) {
      var cjkCount = (v.match(/[\u4E00-\u9FFF\u3400-\u4DBF]/g) || []).length;
      setFontSource("cjk");
      taInputHint.hidden = false;
      taInputHint.className = "ta-hint ta-input-hint" +
        (cjkCount > CJK_MAX_CHARS ? " ta-hint-warn" : "");
      taInputHint.textContent = "检测到中文 → 点阵字体 · " +
        Math.min(cjkCount, CJK_MAX_CHARS) + "/" + CJK_MAX_CHARS + " 个汉字";
    } else {
      var ascii = (v.match(/[\x20-\x7E]/g) || []).length;
      setFontSource("figlet");
      taInputHint.hidden = false;
      taInputHint.className = "ta-hint ta-input-hint" +
        (ascii > FIGLET_MAX_CHARS ? " ta-hint-warn" : "");
      taInputHint.textContent = ascii + "/" + FIGLET_MAX_CHARS +
        " 字符 · FIGlet 字体";
    }
  }
  if (taInput) {
    taInput.addEventListener("input", syncInputHint);
    taInput.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" || e.shiftKey || busy) return;
      e.preventDefault();
      if (convertBtn) convertBtn.click();
    });
  }

  /* ── 生成（唯一入口，服务端自动分流） ── */
  var convertBtn = byId("taConvertBtn");
  var cjkHeightGroup = byId("taCjkHeightGroup");
  var cjkHeightInput = byId("taCjkHeight");
  if (convertBtn) convertBtn.addEventListener("click", function () {
    if (busy) return;
    var text = taInput ? taInput.value : "";
    if (!text.trim()) { toast("请输入文字 / Enter some text"); return; }
    busy = true;
    convertBtn.disabled = true; convertBtn.textContent = "生成中…";
    if (taResultMeta) taResultMeta.hidden = true;
    skeleton();
    var body = { text: text, font: taFont ? taFont.value : "" };
    // 中文点阵：行高随输入（10-40 行），英文 FIGlet 尺寸由字体决定
    if (fontSource === "cjk" && cjkHeightInput && cjkHeightInput.value) {
      body.height = cjkHeightInput.value;
    }
    fetch("/api/text/convert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); }).then(function (d) {
      busy = false;
      convertBtn.disabled = false; convertBtn.textContent = "生成";
      if (d.error) { showOutputError(d.error); hideFontWall(); return; }
      showArt(d);
      if (d.mode === "cjk") {
        hideFontWall();  // 中文路径无字体墙
      } else {
        loadFontWall(text);  // FIGlet 成功 → 字体墙点亮，点卡片即换
      }
    }).catch(function () {
      busy = false;
      convertBtn.disabled = false; convertBtn.textContent = "生成";
      showOutputError("网络异常，请重试 / Network error, retry");
      hideFontWall();
    });
  });

  /* ── 复制 / 下载 / 分享 + 导出矩阵（ANSI / 终端命令 / PNG / HTML）+ 配色 ── */
  var currentTheme = "green";
  var taThemeRow = byId("taThemeRow");
  var THEME_COLORS = {
    green: "rgb(51, 255, 51)", cyan: "rgb(0, 212, 255)",
    amber: "rgb(255, 176, 0)", magenta: "rgb(255, 79, 216)",
    red: "rgb(255, 59, 48)", white: "rgb(224, 230, 237)"
  };

  function applyTheme(theme) {
    currentTheme = theme;
    var c = THEME_COLORS[theme] || THEME_COLORS.green;
    if (taOutput) taOutput.style.color = c;  // 预览同步
    TA.fg = theme === "green" ? [51, 255, 51] : null;  // PNG 入库用，由后端主题映射
    TA.theme = theme;
    if (taThemeRow) {
      taThemeRow.querySelectorAll(".ta-theme-dot").forEach(function (d) {
        d.classList.toggle("active", d.getAttribute("data-theme") === theme);
      });
    }
  }
  if (taThemeRow) taThemeRow.addEventListener("click", function (e) {
    var dot = e.target.closest(".ta-theme-dot");
    if (dot) applyTheme(dot.getAttribute("data-theme"));
  });

  function exportName() {
    return (TA.text || "textart").slice(0, 40) +
      (TA.font ? "_" + TA.font : "");
  }

  var copyBtn = byId("taCopyBtn");
  if (copyBtn) copyBtn.addEventListener("click", function () {
    if (!TA.art) return;
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(TA.art).then(function () { toast("已复制 / Copied"); },
        function () { toast("复制失败 / Copy failed"); });
    }
  });
  var dlBtn = byId("taDownloadBtn");
  if (dlBtn) dlBtn.addEventListener("click", function () {
    if (!TA.art) return;
    var blob = new Blob([TA.art], { type: "text/plain;charset=utf-8" });
    var a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = exportName() + ".txt";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(a.href);
  });

  /* ANSI 彩色复制（对齐安全：整行着色一次 reset） */
  var ansiBtn = byId("taAnsiBtn");
  if (ansiBtn) ansiBtn.addEventListener("click", function () {
    if (!TA.art) { toast("请先生成 / Generate first"); return; }
    fetch("/api/text/export-ansi", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ art: TA.art, theme: TA.theme || currentTheme })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d.error) { toast(d.error); return; }
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(d.ansi).then(
          function () { toast("已复制 ANSI（粘贴到终端即显色）"); },
          function () { toast("复制失败 / Copy failed"); });
      }
    }).catch(function () { toast("网络异常，请重试"); });
  });

  /* 终端命令复制（python -c，base64 免疫引号/换行，跨平台一致） */
  var termBtn = byId("taTermBtn");
  if (termBtn) termBtn.addEventListener("click", function () {
    if (!TA.art) { toast("请先生成 / Generate first"); return; }
    var b64 = btoa(unescape(encodeURIComponent(TA.art)));
    var cmd = 'python -c "import sys,base64;sys.stdout.write(' +
      "base64.b64decode('" + b64 + "').decode('utf-8'))\"";
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(cmd).then(
        function () { toast("命令已复制，粘贴到任何终端运行"); },
        function () { toast("复制失败 / Copy failed"); });
    }
  });

  /* PNG 下载（走后端渲染，配色随主题） */
  function downloadBlob(url, filename) {
    fetch(url, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ art: TA.art, theme: TA.theme || currentTheme,
                             name: exportName() })
    }).then(function (r) {
      if (!r.ok) return r.json().then(function (d) { throw d.error || "export failed"; });
      return r.blob();
    }).then(function (blob) {
      var a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = filename;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(a.href);
    }).catch(function (msg) { toast(String(msg || "导出失败")); });
  }
  var pngBtn = byId("taPngBtn");
  if (pngBtn) pngBtn.addEventListener("click", function () {
    if (!TA.art) { toast("请先生成 / Generate first"); return; }
    downloadBlob("/api/text/export-png", exportName() + ".png");
  });
  var htmlBtn = byId("taHtmlBtn");
  if (htmlBtn) htmlBtn.addEventListener("click", function () {
    if (!TA.art) { toast("请先生成 / Generate first"); return; }
    downloadBlob("/api/text/export-html", exportName() + ".html");
  });
  var shareBtn = byId("taShareBtn");
  if (shareBtn) shareBtn.addEventListener("click", function () {
    if (!TA.art) { toast("请先生成艺术字 / Generate art first"); return; }
    var gm = byId("galleryModal");
    if (!gm) return;
    gm.classList.add("open");
    var t = byId("galleryTitle");
    if (t && !t.value && TA.text) { t.value = TA.text.slice(0, 60); updateCounts(); }
  });

  /* ── 发布弹窗（共享 include）：计数 / 关闭 / 提交 ── */
  function readCustomTags() {
    var el = byId("galleryCustomTags");
    if (!el) return [];
    return el.value.split(/[,，、]/).map(function (s) { return s.trim(); })
      .filter(Boolean).slice(0, 3);
  }
  function updateCounts() {
    var t = byId("galleryTitle"), d = byId("galleryDesc"), a = byId("galleryAuthor");
    var tc = byId("titleCount"), dc = byId("descCount"), ac = byId("authorCount");
    if (tc && t) tc.textContent = t.value.length + "/60";
    if (dc && d) dc.textContent = d.value.length + "/500";
    if (ac && a) ac.textContent = a.value.length + "/20";
    var ct = byId("galleryCustomTags"), cc = byId("customTagsCount");
    if (cc && ct) {
      var n = readCustomTags().length;
      cc.textContent = n + "/3";
      cc.classList.toggle("over", n > 3);
    }
  }
  ["galleryTitle", "galleryDesc", "galleryAuthor", "galleryCustomTags"].forEach(function (id) {
    var el = byId(id);
    if (el) el.addEventListener("input", updateCounts);
  });
  var tagChecks = document.querySelectorAll('.gallery-tag-checkbox input[type="checkbox"]');
  tagChecks.forEach(function (cb) {
    cb.addEventListener("change", function () {
      var checked = document.querySelectorAll('.gallery-tag-checkbox input:checked');
      if (checked.length > 3) { this.checked = false; toast("预设标签最多选 3 个"); }
    });
  });
  var gModal = byId("galleryModal");
  var gClose = byId("galleryModalClose");
  if (gClose) gClose.addEventListener("click", function () { gModal.classList.remove("open"); });
  if (gModal) gModal.addEventListener("click", function (e) {
    if (e.target === gModal) gModal.classList.remove("open");
  });
  var gSubmit = byId("gallerySubmitBtn");
  if (gSubmit) gSubmit.addEventListener("click", function () {
    if (!TA.art) { toast("请先生成艺术字 / Generate art first"); return; }
    var title = byId("galleryTitle").value.trim() || "文字艺术字";
    var desc = byId("galleryDesc").value.trim();
    var author = byId("galleryAuthor").value.trim();
    var vis = document.querySelector('input[name="galleryVis"]:checked');
    var tags = [];
    document.querySelectorAll('.gallery-tag-checkbox input:checked').forEach(function (cb) {
      tags.push(cb.value);
    });
    var body = {
      art: TA.art, font: TA.font, fg: TA.fg,
      title: title, description: desc, author: author,
      tags: tags, custom_tags: readCustomTags(),
      is_private: vis ? vis.value : "0"
    };
    gSubmit.disabled = true; gSubmit.textContent = "发布中…";
    fetch("/api/gallery/upload-text", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); }).then(function (d) {
      gSubmit.disabled = false; gSubmit.textContent = "发布到画廊";
      if (d.error) { toast(d.error); return; }
      gModal.classList.remove("open");
      var url = window.location.origin + d.url;
      if (navigator.clipboard && navigator.clipboard.writeText) {
        navigator.clipboard.writeText(url).catch(function () {});
      }
      showModal("已发布到画廊！", "短链：" + url + "\n已复制到剪贴板，可直接分享。");
    }).catch(function () {
      gSubmit.disabled = false; gSubmit.textContent = "发布到画廊";
      toast("发布失败 / Publish failed");
    });
  });

  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  /* ── 字体墙：FIGlet 文本在全部字体下的预览，点击即换 ── */
  var taFontwall = byId("taFontwall");
  var taFontwallGrid = byId("taFontwallGrid");
  var fwAbort = null;

  function hideFontWall() {
    if (taFontwall) taFontwall.hidden = true;
    if (fwAbort) { fwAbort.abort(); fwAbort = null; }
  }

  function markActiveFontCard() {
    if (!taFontwallGrid || !taFont) return;
    var cards = taFontwallGrid.querySelectorAll(".ta-fw-card");
    for (var i = 0; i < cards.length; i++) {
      cards[i].classList.toggle("active",
        cards[i].getAttribute("data-slug") === taFont.value);
    }
  }

  /* 宽字形预览缩放：按卡片可用宽度等比缩小字号，完整露出作品
     （等宽字体下 scrollWidth 与字号线性相关，一次测量即可换算）。 */
  function fitFontwallArt() {
    if (!taFontwallGrid) return;
    var cards = taFontwallGrid.querySelectorAll(".ta-fw-card");
    for (var i = 0; i < cards.length; i++) {
      var art = cards[i].querySelector(".ta-fw-art");
      if (!art) continue;
      art.style.fontSize = "";  // 先回到 CSS 基准字号再测量
      var sw = art.scrollWidth, cw = art.clientWidth;
      if (sw > cw && sw > 0) {
        var base = parseFloat(getComputedStyle(art).fontSize) || 8;
        var fit = Math.max(4, Math.floor(base * cw / sw * 10) / 10);
        art.style.fontSize = fit + "px";
      }
    }
  }
  var fwFitTimer = null;
  window.addEventListener("resize", function () {
    if (!taFontwall || taFontwall.hidden) return;
    if (fwFitTimer !== null) clearTimeout(fwFitTimer);
    fwFitTimer = setTimeout(fitFontwallArt, 120);
  });

  function loadFontWall(text) {
    if (!taFontwall || !taFontwallGrid || !text || !text.trim()) return;
    taFontwall.hidden = false;
    taFontwallGrid.innerHTML =
      '<p class="ta-fw-loading">字体墙渲染中…</p>';
    if (fwAbort) fwAbort.abort();
    fwAbort = (typeof AbortController !== "undefined")
      ? new AbortController() : null;
    fetch("/api/text/fontwall", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text }),
      signal: fwAbort ? fwAbort.signal : undefined
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok || !taFontwallGrid) return;
      taFontwallGrid.innerHTML = d.fonts.map(function (f) {
        // data-full 携带完整作品 → 点卡片本地切换，零请求（不触发限流）
        return '<button type="button" class="ta-fw-card" data-slug="' +
          esc(f.slug) + '" data-full="' + esc(f.full || "") +
          '" data-cols="' + (f.cols || 0) + '" data-rows="' + (f.rows || 0) +
          '" title="' + esc(f.name) + '">' +
          '<span class="ta-fw-art">' + esc(f.art) + "</span>" +
          '<span class="ta-fw-name">' + esc(f.name) + "</span></button>";
      }).join("");
      markActiveFontCard();
      fitFontwallArt();
      requestAnimationFrame(fitFontwallArt);  // 首帧字体回退/滚动条稳定后再校正一次
    }).catch(function () {
      if (taFontwallGrid && taFontwallGrid.querySelector(".ta-fw-loading")) {
        taFontwallGrid.innerHTML =
          '<p class="ta-fw-loading">字体墙加载失败，可重试生成</p>';
      }
    });
  }

  if (taFontwallGrid) taFontwallGrid.addEventListener("click", function (e) {
    var card = e.target.closest(".ta-fw-card");
    if (!card || busy) return;
    var slug = card.getAttribute("data-slug");
    if (!slug || (taFont && taFont.value === slug)) return;
    if (taFont) taFont.value = slug;  // 与左栏下拉保持同步
    var full = card.getAttribute("data-full");
    if (full) {
      // 本地切换：fontwall 响应已含完整作品，不再请求 convert
      showArt({ art: full,
                cols: parseInt(card.getAttribute("data-cols"), 10) || 0,
                rows: parseInt(card.getAttribute("data-rows"), 10) || 0,
                font: slug,
                text: (taInput ? taInput.value : "").slice(0, 80) });
      markActiveFontCard();
    } else if (convertBtn) {
      convertBtn.click();  // 兜底（无 data-full 时回落请求）
    }
  });
  if (taFont) taFont.addEventListener("change", markActiveFontCard);

  /* ── 示例 chips：点卡片填入输入框并触发生成 ── */
  if (taOutput) taOutput.addEventListener("click", function (e) {
    var chip = e.target.closest(".ta-chip");
    if (!chip) return;
    if (taInput) taInput.value = chip.getAttribute("data-value") || "";
    syncInputHint();
    if (convertBtn) convertBtn.click();
  });

  /* ── 模式切换：文字艺术化 / 图片艺术化 ══ */
  var modeTabs = byId("taModeTabs");
  var taTextPanel = byId("taTextPanel");
  var taImagePanel = byId("taImagePanel");
  if (modeTabs) modeTabs.addEventListener("click", function (e) {
    var tab = e.target.closest(".ta-mode-tab");
    if (!tab) return;
    modeTabs.querySelectorAll(".ta-mode-tab").forEach(function (t) {
      t.classList.toggle("active", t === tab);
    });
    var isImage = tab.getAttribute("data-mode") === "image";
    if (taTextPanel) taTextPanel.hidden = isImage;
    if (taImagePanel) taImagePanel.hidden = !isImage;
    hideFontWall();  // 切模式时清字体墙
  });

  /* ── 图片艺术化（配色点 + 原色，导出复用结果区按钮行）── */
  var imgFile = byId("taImgFile");
  var imgPickBtn = byId("taImgPickBtn");
  var imgNameHint = byId("taImgName");
  var imgCharset = byId("taImgCharset");
  var imgRamp = byId("taImgRamp");
  var imgConvertBtn = byId("taImgConvertBtn");
  var imgPaletteRow = byId("taImgPalette");
  var imgPalette = "green";   // 当前配色：主题名或 "source"（原色）
  var imgBusy = false;

  if (imgPickBtn) imgPickBtn.addEventListener("click", function () {
    if (imgFile) imgFile.click();
  });
  if (imgFile) imgFile.addEventListener("change", function () {
    if (imgNameHint && imgFile.files.length) {
      imgNameHint.textContent = "已选：" + imgFile.files[0].name;
    }
  });
  if (imgCharset) imgCharset.addEventListener("change", function () {
    if (imgRamp) imgRamp.hidden = imgCharset.value !== "custom";
  });
  if (imgPaletteRow) imgPaletteRow.addEventListener("click", function (e) {
    var dot = e.target.closest(".ta-theme-dot");
    if (!dot) return;
    imgPalette = dot.getAttribute("data-palette") || "green";
    imgPaletteRow.querySelectorAll(".ta-theme-dot").forEach(function (d) {
      d.classList.toggle("active", d === dot);
    });
  });

  function imgParams() {
    return {
      palette: imgPalette,
      width: byId("taImgWidth") ? byId("taImgWidth").value : "80",
      height: byId("taImgHeight") ? byId("taImgHeight").value : "40",
      charset: imgCharset ? imgCharset.value : "ascii",
      charset_ramp: imgRamp && !imgRamp.hidden ? imgRamp.value : "",
      flip: byId("taImgFlip") ? byId("taImgFlip").value : "none"
    };
  }

  function showImgArt(d, meta) {
    TA.art = d.art; TA.cols = d.cols; TA.rows = d.rows;
    TA.font = ""; TA.text = meta;
    TA.theme = imgPalette;  // 导出（ANSI/PNG/HTML）随图片配色
    if (!taOutput) return;
    taOutput.classList.add("has-art");
    // 原色：art 内嵌 TrueColor ANSI → 逐段着色 HTML；主题色纯文本 → CSS 着色
    if (d.mode === "source") {
      taOutput.innerHTML = ansiToHtml(d.art);
      taOutput.style.color = "";
    } else {
      taOutput.textContent = d.art;
      applyTheme(d.palette || "green");
    }
    if (taPreviewTitle) {
      taPreviewTitle.textContent = "img art · " + meta +
        " " + d.cols + "x" + d.rows;
    }
    if (taMetaText) {
      taMetaText.textContent = "图片艺术化 · " + meta + " · " +
        d.cols + " x " + d.rows;
    }
    if (taResultMeta) taResultMeta.hidden = false;
    if (taThemeRow) taThemeRow.hidden = true;  // 配色已由面板圆点决定
  }

  /* TrueColor ANSI → HTML（逐行逐段解析 SGR，fg 着色 span）*/
  function ansiToHtml(text) {
    var sgr = /\x1b\[([0-9;]*)m/g;  // imgascii 只产 SGR 转义
    var html = "";
    var color = "";
    var last = 0;
    var m;
    function flush(seg) {
      if (!seg) return;
      var safe = seg.replace(/&/g, "&amp;").replace(/</g, "&lt;")
        .replace(/>/g, "&gt;");
      html += color
        ? '<span style="color:' + color + '">' + safe + "</span>"
        : safe;
    }
    while ((m = sgr.exec(text)) !== null) {
      flush(text.slice(last, m.index));
      var parts = m[1].split(";");
      if (parts[0] === "38" && parts[1] === "2") {
        color = "rgb(" + parts[2] + "," + parts[3] + "," + parts[4] + ")";
      } else if (parts[0] === "0" || parts[0] === "") {
        color = "";
      }
      last = m.index + m[0].length;
    }
    flush(text.slice(last));
    return html;
  }

  if (imgConvertBtn) imgConvertBtn.addEventListener("click", function () {
    if (imgBusy) return;
    if (!imgFile || !imgFile.files.length) {
      toast("请先选择图片 / Choose an image first"); return;
    }
    imgBusy = true;
    imgConvertBtn.disabled = true; imgConvertBtn.textContent = "生成中…";
    if (taResultMeta) taResultMeta.hidden = true;
    skeleton();
    var p = imgParams();
    var fd = new FormData();
    fd.append("file", imgFile.files[0]);
    Object.keys(p).forEach(function (k) { fd.append(k, p[k]); });
    fetch("/api/text/imgascii", { method: "POST", body: fd })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        imgBusy = false;
        imgConvertBtn.disabled = false; imgConvertBtn.textContent = "生成";
        if (d.error) {
          if (d.redirect) {
            showOutputError(d.error);
            toast("正在跳转动画工坊…");
            setTimeout(function () { window.location.href = d.redirect; }, 1200);
          } else {
            showOutputError(d.error);
          }
          return;
        }
        showImgArt(d, imgFile.files[0].name);
      })
      .catch(function (msg) {
        imgBusy = false;
        imgConvertBtn.disabled = false; imgConvertBtn.textContent = "生成";
        showOutputError(String(msg || "网络异常，请重试"));
      });
  });

  /* ── init ── */
  loadFonts();
  syncInputHint();
  showEmpty();  // 增强空态：含示例 chips（替换 HTML 硬编码版本）
})();
