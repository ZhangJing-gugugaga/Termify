/* Termify · /text-art 按需包（字体墙 + 图片艺术化 + 图片字符集墙）
   首屏用不到这两块：只有「生成成功」「切到图片模式」「首次打开字体墙」才需要。
   由 text_art.js 里的 lazyReady() 动态插入 <script>，HTML 不直接引用本文件。
   与核心的桥：window.__TA_LAZY_CTX（渲染/状态句柄）+ window.__TA_LAZY（导出 API）。
   约束：ES5、无构建链、不引第三方库。 */
(function () {
  "use strict";
  var CTX = window.__TA_LAZY_CTX;
  if (!CTX) return;
  window.__TA_LAZY_CTX = null;   // 桥只交一次：核心随后可回收

  var byId = CTX.byId, toast = CTX.toast, esc = CTX.esc;
  var showArt = CTX.showArt, artCharRatio = CTX.artCharRatio, hasCJK = CTX.hasCJK;
  var applyTheme = CTX.applyTheme, syncThemeRow = CTX.syncThemeRow;
  var fitOutputFont = CTX.fitOutputFont, skeleton = CTX.skeleton;
  var showOutputError = CTX.showOutputError, TA = CTX.TA, OUTPUT_LH = CTX.OUTPUT_LH;
  var taOutput = CTX.els.output, taPreviewTitle = CTX.els.previewTitle;
  var taMetaText = CTX.els.metaText, taResultMeta = CTX.els.resultMeta;
  var taInput = CTX.els.input, taFont = CTX.els.font, convertBtn = CTX.els.convertBtn;
  /* ── 字体墙：英文字体 / 中文字体 / 图片字符集共用一套卡片，点击即换 ──
     卡片把整幅作品用 transform: scale 等比缩进固定大小的框里：不截行、
     不裁字、不出内部滚动条。等宽字体下 advance ≈ 0.6em、line-height 1.05，
     所以 cols/rows 一到手就能算出缩放比，不必等布局回流。 */
  var taFontwall = byId("taFontwall");
  var taFontwallGrid = byId("taFontwallGrid");
  var taFwTitle = byId("taFwTitle");
  var fwAbort = null;
  var FW_LH = 1.05;    // 兜底行高（正常值走卡片上的 data-lh，见 OUTPUT_LH）
                       // 字符宽比例不在此写死：fitFontwallArt 按卡片内容
                       // 实测（盲文 advance 非 0.6em，见 artCharRatio）

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
      var lh = parseFloat(art.getAttribute("data-lh")) || FW_LH;
      // 盲文卡的字符 advance 来自系统回退字体（非 0.6em），按内容实测
      var ch = artCharRatio(art.textContent);
      var w = Math.max(40, (box.clientWidth || 150) - FW_PAD_X);
      var h = Math.max(24, (box.clientHeight || 108) - FW_PAD_Y);
      var scale = Math.min(1, w / (cols * base * ch),
                            h / (rows * base * lh));
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

  function renderFontWall(fonts, activeSlug, title, kind) {
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
        '<span class="ta-fw-box"><span class="ta-fw-art" data-lh="' +
        (OUTPUT_LH[kind] || OUTPUT_LH.figlet) + '" data-cols="' +
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

  /* 字体墙内容键：墙里的字形只由「端点 + 输入文本（+ 字体）」决定。
     键与上一次渲染一致就没有新内容可出——复用已有 DOM、零请求
     （与图片字符集墙 imgWallKey 同一范式）。原文本进键：服务端渲染前
     会截断/折叠空白，宁可多重传一次，也不让墙显示上一条文本的字。 */
  var fwWallKey = null;
  function fontwallReusable(key) {
    if (key !== fwWallKey) return false;
    if (!taFontwall || taFontwall.hidden) return false;
    // 上一次没成功（仍在 loading / 失败）：没有可复用的卡片
    return !(taFontwallGrid &&
             taFontwallGrid.querySelector(".ta-fw-loading"));
  }

  /* 英文 FIGlet 字体墙（首卡=盲文·分享：块字符字体粘到中文字体环境
     会全角/半角错位，见服务端 render_art_braille） */
  function loadFigletWall(text) {
    if (!taFontwall || !text || !text.trim()) return;
    var key = "figlet|" + text;
    if (fontwallReusable(key)) return;
    fwWallKey = key;
    wallLoading();
    postJSON("/api/text/fontwall", { text: text }).then(function (d) {
      if (!d.ok || !d.fonts) { fwWallKey = null; wallFailed(); return; }
      renderFontWall(d.fonts, taFont ? taFont.value : "",
                     "字体墙 · 点击切换（首张「盲文·分享」= 粘贴安全版）",
                     "figlet");
    }).catch(function () { fwWallKey = null; wallFailed(); });
  }

  /* 中文字符墙：同一点阵 × 字符集（字块/明暗/圆点/几何/盲文/经典）。
     点卡片换"点亮"字符，作用于当前输入重新渲染。 */
  function loadCJKWall(text) {
    if (!taFontwall) return;
    var font = taFont ? taFont.value : "";
    var key = "cjk|" + font + "|" + (text || "");
    if (fontwallReusable(key)) return;
    fwWallKey = key;
    wallLoading();
    postJSON("/api/cjk/ttf/fontwall", { text: text || "", font: font })
      .then(function (d) {
      if (!d.ok || !d.charsets) { fwWallKey = null; wallFailed(); return; }
      renderFontWall(d.charsets, CTX.state.cjkCharset(),
                     "字符墙 · 点亮字符 · 点击切换", "cjk");
    }).catch(function () { fwWallKey = null; wallFailed(); });
  }

  /* 点卡片：优先本地切换（data-full），否则回到生成按钮重渲染 */
  function onWallPick(card) {
    var slug = card.getAttribute("data-slug");
    if (!slug) return;
    var full = card.getAttribute("data-full");
    markActiveFontCard(slug);
    if (full && CTX.state.mode() === "text" && !hasCJK(taInput ? taInput.value : "")) {
      if (slug !== "braille-share" && taFont) taFont.value = slug;
      showArt({ art: full,
                cols: parseInt(card.getAttribute("data-cols"), 10) || 0,
                rows: parseInt(card.getAttribute("data-rows"), 10) || 0,
                font: slug === "braille-share" ? "" : slug,
                mode: slug === "braille-share" ? "braille" : "figlet",
                text: (taInput ? taInput.value : "").slice(0, 80) });
      return;
    }
    // 中文 / 图片：墙卡只是样张，得拿当前输入重新渲染一次
    if (CTX.state.mode() === "image") {
      if (imgCharset) { imgCharset.value = slug; syncRampVisibility(); }
      if (imgConvertBtn) imgConvertBtn.click();
    } else if (CTX.state.fontSource() === "cjk") {
      CTX.state.setCjkCharset(slug);          // 中文字符墙：换"点亮"字符
      if (convertBtn) convertBtn.click();
    } else {
      if (taFont) taFont.value = slug;
      if (convertBtn) convertBtn.click();
    }
  }

  if (taFontwallGrid) taFontwallGrid.addEventListener("click", function (e) {
    var card = e.target.closest(".ta-fw-card");
    if (!card || CTX.state.isBusy() || imgBusy) return;
    var active = (CTX.state.mode() === "text" && CTX.state.fontSource() === "cjk")
      ? CTX.state.cjkCharset() : (taFont ? taFont.value : "");
    if (CTX.state.mode() === "text" && card.getAttribute("data-slug") === active) return;
    onWallPick(card);
  });
  if (taFont) taFont.addEventListener("change", function () {
    markActiveFontCard(CTX.state.fontSource() === "cjk" ? CTX.state.cjkCharset() : taFont.value);
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
      palette: (CTX.state.mode() === "image" ? (TA.theme || CTX.theme()) : "green"),
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
    TA.mode = "img";
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
            toast(d.error);
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
        toast(String(msg || "网络异常，请重试 / Network error, retry"));
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
                       "字符墙 · " + d.fonts.length + " 种字符集 · 点击切换",
                       "img");
      }).catch(function () { wallFailed(); imgWallKey = null; });
  }
  /* 对外只暴露核心需要的 4 个入口（其余都是本包内部实现） */
  window.__TA_LAZY = {
    hideFontWall: hideFontWall,
    fitFontwallArt: fitFontwallArt,
    loadFigletWall: loadFigletWall,
    loadCJKWall: loadCJKWall
  };
})();