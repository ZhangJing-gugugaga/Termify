/* Termify · 字符艺术独立页（/text-art）
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
  var taMode = "text";   // text = 字符艺术化 · image = 图片艺术化
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
  /* 输出字号自适应：等宽字符画列数随内容变化（中文点阵最宽可到 440 列、
     FIGlet 更宽），固定 0.62rem 会横向溢出容器——按超出比例缩小字号，
     保证整幅作品完整可见（与字体墙同算法）。
     返回实际字号，供调用方决定要不要提示"已缩到最小"。 */
  function fitOutputFont() {
    if (!taOutput || !TA.art) return 0;
    taOutput.style.fontSize = "";
    // clientWidth 含 padding，scrollWidth 也是"内容 + padding"——两者直接
    // 比较会在作品宽度落在 (内容宽, clientWidth) 之间时漏判，作品横向溢出
    // 容器（右侧被切 + 横向滚动条）。先把 padding 扣掉再比。
    var cs = getComputedStyle(taOutput);
    var padX = (parseFloat(cs.paddingLeft) || 0) +
               (parseFloat(cs.paddingRight) || 0);
    var sw = taOutput.scrollWidth, cw = taOutput.clientWidth - padX;
    var fs = parseFloat(cs.fontSize) || 10;
    if (sw > cw && sw > 0) {
      // 下限 2px：超宽作品（如 4 字 × 50 行 = 400 列）需要 ~3px 才塞得下，
      // 旧的 4px 下限会让它横向溢出、右侧"显示不完全"。宁可小到看不清，
      // 也要整幅在框内；要原始尺寸用 .txt / .py / PNG 导出。
      fs = Math.max(2, Math.floor(fs * cw / sw * 10) / 10);
      taOutput.style.fontSize = fs + "px";
    }
    return fs;
  }
  var outputFitTimer = null;
  window.addEventListener("resize", function () {
    if (outputFitTimer !== null) clearTimeout(outputFitTimer);
    outputFitTimer = setTimeout(fitOutputFont, 120);
  });

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
    if (taResultMeta) taResultMeta.hidden = false;
    syncThemeRow();   // 有作品 → 配色行可见（两种模式共用同一行）
    var fs = fitOutputFont();
    if (taMetaText) {
      taMetaText.textContent = modeLabel + " · " + d.cols + " x " + d.rows +
        // 缩到下限以下 = 已经看不清了，明说"预览已缩到最小"，并指出
        // 原始尺寸走导出——否则用户以为作品被切了
        (fs && fs < 4 ? " · 预览已缩到最小，导出为原始尺寸" : "");
    }
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
    // 中文点阵：行高随输入（10-64 行），英文 FIGlet 尺寸由字体决定
    var requestedH = 0;
    if (fontSource === "cjk" && cjkHeightInput && cjkHeightInput.value) {
      requestedH = parseInt(cjkHeightInput.value, 10) || 0;
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
        loadCJKWall(text);   // 中文字体墙：点卡片换字形（样张 → 重渲染）
        // 高度被按文本长度自动收缩时给出提示（宽度红线，见
        // textart.cjk_effective_height）
        if (requestedH >= 10 && d.height &&
            requestedH - d.height >= 2) {
          toast("字符高度已按文本长度收缩到 " + d.height +
                " 行（总宽上限 " + (d.cols) + " 列，字越多收缩越狠）");
        }
      } else {
        loadFigletWall(text);  // FIGlet 成功 → 字体墙点亮，点卡片即换
      }
    }).catch(function () {
      busy = false;
      convertBtn.disabled = false; convertBtn.textContent = "生成";
      showOutputError("网络异常，请重试 / Network error, retry");
      hideFontWall();
    });
  });

  /* ── 复制 / 下载 / 分享 + 导出矩阵（ANSI / .py / 终端命令 / PNG / HTML）+ 配色 ── */
  var currentTheme = "green";
  var taThemeRow = byId("taThemeRow");
  var taThemeSource = taThemeRow ? taThemeRow.querySelector(
    '.ta-theme-dot[data-theme="source"]') : null;
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
    if (!dot || dot.hidden) return;
    applyTheme(dot.getAttribute("data-theme"));
  });
  /* 配色行现在两种模式共用（原来图片模式把配色放在左面板，位置和英文字符化
     不一致）。「原色」只有图片艺术化有源像素可取，文字模式下隐藏。 */
  function syncThemeRow() {
    if (!taThemeRow) return;
    if (taThemeSource) taThemeSource.hidden = (taMode !== "image");
    taThemeRow.hidden = !TA.art;
  }

  function exportName() {
    return (TA.text || "textart").slice(0, 40) +
      (TA.font ? "_" + TA.font : "");
  }

  /* 复制纯文本：剥掉 TrueColor 转义。原色作品的 art 里带着 ESC 序列，
     直接复制出去在记事本/编辑器里是一串乱码，而且和预览看到的不一致。 */
  function stripAnsi(s) {
    return String(s).replace(/\x1b\[[0-9;]*[A-Za-z]/g, "");
  }
  function hasAnsi(s) { return /\x1b\[[0-9;]*[A-Za-z]/.test(String(s)); }

  var copyBtn = byId("taCopyBtn");
  /* ── 移动端兼容助手 ──
     copyText：clipboard API 在 UC/X5 等 webview 常被拒 → execCommand 回退。
     postDownload：blob+a.click 下载在 UC/X5 不可靠（0KB 失败）→ 隐藏表单
     POST 走服务端 attachment，浏览器原生下载。 */
  function copyText(text, okMsg, failMsg) {
    function legacy() {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.style.cssText = "position:fixed;top:0;left:0;opacity:0";
      document.body.appendChild(ta);
      ta.focus(); ta.select();
      var ok = false;
      try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
      document.body.removeChild(ta);
      toast(ok ? (okMsg || "已复制") : (failMsg || "复制失败 / Copy failed"));
    }
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(text).then(
        function () { toast(okMsg || "已复制"); }, legacy);
    } else { legacy(); }
  }
  function postDownload(path, fields) {
    var form = document.createElement("form");
    form.method = "POST";
    form.action = path;
    Object.keys(fields).forEach(function (k) {
      var input = document.createElement("input");
      input.type = "hidden";
      input.name = k;
      input.value = fields[k];
      form.appendChild(input);
    });
    document.body.appendChild(form);
    form.submit();
    form.remove();
  }
  if (copyBtn) copyBtn.addEventListener("click", function () {
    if (!TA.art) return;
    var plain = stripAnsi(TA.art);
    copyText(plain, hasAnsi(TA.art)
      ? "已复制纯文本（已剥颜色转义）" : "已复制 / Copied",
      "复制失败 / Copy failed");
  });
  var dlBtn = byId("taDownloadBtn");
  if (dlBtn) dlBtn.addEventListener("click", function () {
    if (!TA.art) return;
    postDownload("/api/text/export-txt",
                 { art: TA.art, name: exportName() });
  });

  /* ANSI 彩色复制：整行着色一次 reset。注意它**不是**给 shell 提示符用的
     ——把 ESC[38;2;…m 粘到 PowerShell / cmd 提示符必然是 ParserError
     （`[` 后面缺少类型名称），那是 shell 在解析转义而不是终端在显示。
     能显色的场景是「粘进终端窗口」或「粘进文件再 cat」。复制前按体积
     给出提示，大作品直接推 .py。 */
  var ansiBtn = byId("taAnsiBtn");
  if (ansiBtn) ansiBtn.addEventListener("click", function () {
    if (!TA.art) { toast("请先生成 / Generate first"); return; }
    fetch("/api/text/export-ansi", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ art: TA.art, theme: TA.theme || currentTheme })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d.error) { toast(d.error); return; }
      copyText(d.ansi, "已复制 ANSI（粘进终端窗口即显色；粘到 PowerShell/cmd " +
               "提示符会报错，请改用「下载 .py」）",
               "复制失败 / Copy failed");
    }).catch(function () { toast("网络异常，请重试"); });
  });

  /* 可执行 .py：python xxx.py 直接出图。终端命令撞命令行长度上限时的主推 */
  var pyBtn = byId("taPyBtn");
  if (pyBtn) pyBtn.addEventListener("click", function () {
    if (!TA.art) { toast("请先生成 / Generate first"); return; }
    postDownload("/api/text/export-py", {
      art: TA.art, theme: TA.theme || currentTheme, name: exportName()
    });
  });

  /* 终端命令（python -c，zlib+base64 压缩，已比旧版小 5~10 倍）。
     仍然超长（>7000 字符）时 cmd.exe 粘不进去，改为提示下载 .py。 */
  var termBtn = byId("taTermBtn");
  if (termBtn) termBtn.addEventListener("click", function () {
    if (!TA.art) { toast("请先生成 / Generate first"); return; }
    fetch("/api/text/terminal-command", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ art: TA.art, theme: TA.theme || currentTheme })
    }).then(function (r) { return r.json(); }).then(function (d) {
      if (d.error) { toast(d.error); return; }
      if (d.too_long) {
        postDownload("/api/text/export-py", {
          art: TA.art, theme: TA.theme || currentTheme, name: exportName()
        });
        toast("作品太大（命令行 " + d.cmd_len + " 字符，超出 cmd.exe 上限），" +
              "已改为下载 .py：python 跑一下即可");
        return;
      }
      copyText(d.cmd, "命令已复制（" + d.cmd_len +
               " 字符），粘贴到任何终端运行",
               "复制失败 / Copy failed");
    }).catch(function () { toast("网络异常，请重试"); });
  });

  /* PNG / HTML 下载：表单 POST → 服务端 attachment 原生下载
     （blob+a.click 在 UC/X5 等移动浏览器 0KB 失败） */
  var pngBtn = byId("taPngBtn");
  if (pngBtn) pngBtn.addEventListener("click", function () {
    if (!TA.art) { toast("请先生成 / Generate first"); return; }
    postDownload("/api/text/export-png", {
      art: TA.art, theme: TA.theme || currentTheme, name: exportName()
    });
  });
  var htmlBtn = byId("taHtmlBtn");
  if (htmlBtn) htmlBtn.addEventListener("click", function () {
    if (!TA.art) { toast("请先生成 / Generate first"); return; }
    postDownload("/api/text/export-html", {
      art: TA.art, theme: TA.theme || currentTheme, name: exportName()
    });
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
      if (checked.length > 3) { this.checked = false; toast("最多选 3 个标签"); }
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
    var title = byId("galleryTitle").value.trim() || "字符艺术";
    var desc = byId("galleryDesc").value.trim();
    var author = byId("galleryAuthor").value.trim();
    var vis = document.querySelector('input[name="galleryVis"]:checked');
    var tags = [];
    document.querySelectorAll('.gallery-tag-checkbox input:checked').forEach(function (cb) {
      tags.push(cb.value);
    });
    var body = {
      art: TA.art, font: TA.font, fg: TA.fg,
      palette: TA.theme || currentTheme,  // 配色随结果区主题（source=原色）
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

  /* ── 字体墙：英文字体 / 中文字体 / 图片字符集共用一套卡片，点击即换 ──
     卡片把整幅作品用 transform: scale 等比缩进固定大小的框里：不截行、
     不裁字、不出内部滚动条。等宽字体下 advance ≈ 0.6em、line-height 1.05，
     所以 cols/rows 一到手就能算出缩放比，不必等布局回流。 */
  var taFontwall = byId("taFontwall");
  var taFontwallGrid = byId("taFontwallGrid");
  var taFwTitle = byId("taFwTitle");
  var fwAbort = null;
  var FW_CH = 0.6;     // 1em = 基准字号下单个字符的宽度
  var FW_LH = 1.05;    // 行高倍数（与 CSS .ta-fw-art line-height 对齐）

  function hideFontWall() {
    if (taFontwall) taFontwall.hidden = true;
    if (fwAbort) { fwAbort.abort(); fwAbort = null; }
  }

  function markActiveFontCard(slug) {
    if (!taFontwallGrid) return;
    taFontwallGrid.querySelectorAll(".ta-fw-card").forEach(function (c) {
      c.classList.toggle("active", c.getAttribute("data-slug") === slug);
    });
  }

  /* 缩放：按卡片可用宽高取 min，但**留出边距**——正好铺满卡片时作品
     会顶到左右边缘（框内无 padding + overflow:hidden），首尾字符看着
     就是被切掉一个（用户报的"字体墙预览偏移"）。基准 8px 字号。 */
  var FW_PAD_X = 16;   // 左右合计留白（每侧 8px）
  var FW_PAD_Y = 10;   // 上下合计留白
  function fitFontwallArt() {
    if (!taFontwallGrid) return;
    var cards = taFontwallGrid.querySelectorAll(".ta-fw-card");
    for (var i = 0; i < cards.length; i++) {
      var box = cards[i].querySelector(".ta-fw-box");
      var art = cards[i].querySelector(".ta-fw-art");
      if (!box || !art) continue;
      var cols = parseInt(art.getAttribute("data-cols"), 10) || 1;
      var rows = parseInt(art.getAttribute("data-rows"), 10) || 1;
      var base = 8;
      var w = Math.max(40, (box.clientWidth || 150) - FW_PAD_X);
      var h = Math.max(24, (box.clientHeight || 108) - FW_PAD_Y);
      var scale = Math.min(1, w / (cols * base * FW_CH),
                            h / (rows * base * FW_LH));
      // 下限 0.3：再小就不可读了，宁可让极罕见的超高字体溢出裁切
      art.style.transform = "scale(" + Math.max(0.3, scale) + ")";
    }
  }
  var fwFitTimer = null;
  window.addEventListener("resize", function () {
    if (!taFontwall || taFontwall.hidden) return;
    if (fwFitTimer !== null) clearTimeout(fwFitTimer);
    fwFitTimer = setTimeout(fitFontwallArt, 120);
  });

  function renderFontWall(fonts, activeSlug, title) {
    if (!taFontwall || !taFontwallGrid || !fonts || !fonts.length) return;
    taFontwall.hidden = false;
    if (taFwTitle) taFwTitle.textContent = title || "字体墙 · 点击切换";
    taFontwallGrid.innerHTML = fonts.map(function (f) {
      // data-full 携带完整作品 → 点卡片本地切换，零请求（不触发限流）；
      // 为空（如图片字符集墙）时回落到重新生成。
      return '<button type="button" class="ta-fw-card" data-slug="' +
        esc(f.slug) + '" data-full="' + esc(f.full || "") +
        '" data-cols="' + (f.cols || 0) + '" data-rows="' + (f.rows || 0) +
        '" title="' + esc(f.name) + " · " + (f.cols || 0) + "x" +
        (f.rows || 0) + '">' +
        '<span class="ta-fw-box"><span class="ta-fw-art" data-cols="' +
        (f.cols || 0) + '" data-rows="' + (f.rows || 0) + '">' +
        esc(f.art) + "</span></span>" +
        '<span class="ta-fw-name">' + esc(f.name) + "</span></button>";
    }).join("");
    markActiveFontCard(activeSlug);
    fitFontwallArt();
    requestAnimationFrame(fitFontwallArt);  // 首帧字体回退后再校正一次
  }

  function wallLoading() {
    if (!taFontwall || !taFontwallGrid) return;
    taFontwall.hidden = false;
    taFontwallGrid.innerHTML = '<p class="ta-fw-loading">字体墙渲染中…</p>';
  }
  function wallFailed() {
    if (taFontwallGrid && taFontwallGrid.querySelector(".ta-fw-loading")) {
      taFontwallGrid.innerHTML =
        '<p class="ta-fw-loading">字体墙加载失败，可重试生成</p>';
    }
  }
  function postJSON(path, body) {
    if (fwAbort) fwAbort.abort();
    fwAbort = (typeof AbortController !== "undefined")
      ? new AbortController() : null;
    return fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal: fwAbort ? fwAbort.signal : undefined
    }).then(function (r) { return r.json(); });
  }

  /* 英文 FIGlet 字体墙 */
  function loadFigletWall(text) {
    if (!taFontwall || !text || !text.trim()) return;
    wallLoading();
    postJSON("/api/text/fontwall", { text: text }).then(function (d) {
      if (!d.ok || !d.fonts) { wallFailed(); return; }
      renderFontWall(d.fonts, taFont ? taFont.value : "",
                     "字体墙 · FIGlet " + d.fonts.length + " 款 · 点击切换");
    }).catch(wallFailed);
  }

  /* 中文字体墙（宋/黑/楷）。卡片里是 2 字样张，点卡片作用于输入全文。 */
  function loadCJKWall(text) {
    if (!taFontwall) return;
    wallLoading();
    postJSON("/api/cjk/ttf/fontwall", { text: text || "" }).then(function (d) {
      if (!d.ok || !d.fonts) { wallFailed(); return; }
      renderFontWall(d.fonts, taFont ? taFont.value : "",
                     "字体墙 · 中文字体 · 点击切换");
    }).catch(wallFailed);
  }

  /* 点卡片：优先本地切换（data-full），否则回到生成按钮重渲染 */
  function onWallPick(card) {
    var slug = card.getAttribute("data-slug");
    if (!slug) return;
    var full = card.getAttribute("data-full");
    markActiveFontCard(slug);
    if (full && taMode === "text" && !hasCJK(taInput ? taInput.value : "")) {
      if (taFont) taFont.value = slug;
      showArt({ art: full,
                cols: parseInt(card.getAttribute("data-cols"), 10) || 0,
                rows: parseInt(card.getAttribute("data-rows"), 10) || 0,
                font: slug, mode: "figlet",
                text: (taInput ? taInput.value : "").slice(0, 80) });
      return;
    }
    // 中文 / 图片：墙卡只是样张，得拿当前输入重新渲染一次
    if (taMode === "image") {
      if (imgCharset) { imgCharset.value = slug; syncRampVisibility(); }
      if (imgConvertBtn) imgConvertBtn.click();
    } else {
      if (taFont) taFont.value = slug;
      if (convertBtn) convertBtn.click();
    }
  }

  if (taFontwallGrid) taFontwallGrid.addEventListener("click", function (e) {
    var card = e.target.closest(".ta-fw-card");
    if (!card || busy || imgBusy) return;
    if (taFont && card.getAttribute("data-slug") === taFont.value &&
        taMode === "text") return;
    onWallPick(card);
  });
  if (taFont) taFont.addEventListener("change", function () {
    markActiveFontCard(taFont.value);
  });

  /* ── 示例 chips：点卡片填入输入框并触发生成 ── */
  if (taOutput) taOutput.addEventListener("click", function (e) {
    var chip = e.target.closest(".ta-chip");
    if (!chip) return;
    if (taInput) taInput.value = chip.getAttribute("data-value") || "";
    syncInputHint();
    if (convertBtn) convertBtn.click();
  });

  /* ── 模式切换：字符艺术化 / 图片艺术化 ══ */
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
    taMode = isImage ? "image" : "text";
    if (taTextPanel) taTextPanel.hidden = isImage;
    if (taImagePanel) taImagePanel.hidden = !isImage;
    hideFontWall();  // 切模式时清字体墙
    if (taOutput) taOutput.style.color = "";  // 换模式别留上一模式的主题色
    syncThemeRow();
  });

  /* ── 图片艺术化（配色点 + 原色，导出复用结果区按钮行）── */
  var imgFile = byId("taImgFile");
  var imgPickBtn = byId("taImgPickBtn");
  var imgNameHint = byId("taImgName");
  var imgCharset = byId("taImgCharset");
  var imgRamp = byId("taImgRamp");
  var imgConvertBtn = byId("taImgConvertBtn");
  var imgBusy = false;

  function syncRampVisibility() {
    if (imgRamp && imgCharset) imgRamp.hidden = imgCharset.value !== "custom";
  }

  if (imgPickBtn) imgPickBtn.addEventListener("click", function () {
    if (imgFile) imgFile.click();
  });
  if (imgFile) imgFile.addEventListener("change", function () {
    if (imgNameHint && imgFile.files.length) {
      imgNameHint.textContent = "已选：" + imgFile.files[0].name;
      hideFontWall();  // 换图 → 旧字符集墙作废
    }
  });
  if (imgCharset) imgCharset.addEventListener("change", syncRampVisibility);

  function imgParams() {
    return {
      palette: (taMode === "image" ? (TA.theme || currentTheme) : "green"),
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
    TA.theme = d.palette || "green";  // 导出（ANSI/.py/PNG/HTML）随配色
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
    syncThemeRow();   // 配色行在预览下方，与英文字符化同一位置
    fitOutputFont();
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
        loadImgWall();
      })
      .catch(function (msg) {
        imgBusy = false;
        imgConvertBtn.disabled = false; imgConvertBtn.textContent = "生成";
        showOutputError(String(msg || "网络异常，请重试"));
      });
  });

  /* 图片字符集墙：同一张图 × 全部字符集，一次上传出齐所有变体。
     （与英文/中文字体墙同一套卡片，点卡片即换即看。）
     墙按「图 + 尺寸 + 配色 + 翻转 + 字符集」缓存：点卡片换字符集时只重渲染
     结果、不重传图片。 */
  var imgWallKey = null;
  function imgWallCacheKey() {
    if (!imgFile || !imgFile.files.length) return null;
    var f = imgFile.files[0];
    var p = imgParams();
    // 不含 charset：墙内容与当前字符集无关（点卡片换字符集不该重传图）
    return [f.name, f.size, f.lastModified, p.width, p.height, p.palette,
            p.flip].join("|");
  }
  function loadImgWall(force) {
    if (!taFontwall || !imgFile || !imgFile.files.length) return;
    var p = imgParams();
    if (p.charset === "custom" && !p.charset_ramp) {
      // 自定义字符没有固定字形，墙无意义
      hideFontWall();
      imgWallKey = null;
      return;
    }
    var key = imgWallCacheKey();
    if (!force && key === imgWallKey && !taFontwall.hidden) return;
    imgWallKey = key;
    wallLoading();
    var fd = new FormData();
    fd.append("file", imgFile.files[0]);
    ["width", "height", "palette", "flip"].forEach(function (k) {
      fd.append(k, p[k]);
    });
    if (fwAbort) fwAbort.abort();
    fwAbort = (typeof AbortController !== "undefined")
      ? new AbortController() : null;
    fetch("/api/text/imgwall", { method: "POST", body: fd,
                                 signal: fwAbort ? fwAbort.signal : undefined })
      .then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok || !d.fonts) { wallFailed(); return; }
        renderFontWall(d.fonts, imgCharset ? imgCharset.value : "",
                       "字符墙 · " + d.fonts.length + " 种字符集 · 点击切换");
      }).catch(function () { wallFailed(); imgWallKey = null; });
  }

  /* ── init ── */
  loadFonts();
  syncInputHint();
  showEmpty();  // 增强空态：含示例 chips（替换 HTML 硬编码版本）
})();
