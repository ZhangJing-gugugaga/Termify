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
  // mode 决定预览的字符格比例：cjk=1:2 点阵、figlet=1:1、其余(图片)=1.3
  var TA = { art: "", cols: 0, rows: 0, font: "", text: "", fg: [51, 255, 51], mode: "figlet" };
  var taMode = "text";   // text = 字符艺术化 · image = 图片艺术化
  var taInput = byId("taInput");
  var taFont = byId("taFont");
  var taOutput = byId("taOutput");
  var taResultMeta = byId("taResultMeta");
  var taMetaText = byId("taMetaText");
  var taPreviewTitle = byId("taPreviewTitle");
  var taFontHint = byId("taFontHint");
  /* 结果区动作按钮：没有作品时保持 disabled（模板首屏就带 disabled 属性），
     有作品才可用——按钮常驻是「结果行首屏即真内容」的一部分 */
  var resultActions = taResultMeta ?
    taResultMeta.querySelectorAll(".ta-result-actions .ta-btn") : [];
  function setActionsEnabled(on) {
    for (var i = 0; i < resultActions.length; i++) {
      resultActions[i].disabled = !on;
    }
  }
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
    // 只有从未生成过作品时才把按钮关掉、文本位退回占位。
    setActionsEnabled(!!TA.art);
    if (!TA.art && taMetaText) taMetaText.textContent = "— · 0 字符";
    if (taResultMeta && TA.art && taMetaText) {
      taMetaText.textContent = "上一作品仍可导出 · " + TA.cols + " x " +
        TA.rows + "（本次生成失败：" + msg.split(" / ")[0] + "）";
    }
  }

  /* 示例 chips：覆盖英文 / 中文两条路径 */
  /* 字体墙首屏样例：墙体是静态字体列表，不依赖用户输入，首屏先用它出卡片 */
  var FW_SAMPLE = "Hello";
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
    // 结果行常驻：没有作品时给占位文本 + 按钮 disabled，而不是整块隐藏
    if (taMetaText) taMetaText.textContent = "— · 0 字符";
    setActionsEnabled(false);
  }
  /* 预览窗口：框高由 CSS 固定（.ta-output 的 clamp(240px,36vh,360px)），
     JS 只缩字号让整幅作品塞进框内（宽、高两个方向同时满足）。
     2026-10-26 起不再按作品网格比例反推框高：那会让框高随作品变化，
     插入作品时把结果行/字体墙/页脚整片顶下去——/text-art 交互期唯一的
     大 layout-shift（0.0257）。固定框高是 CLS=0 的前提。
     行高按来源模式定：中文点阵是 1:2 网格（1.2）、FIGlet 是 1:1 网格
     （1.05）、图片艺术化服务端按 1.3 生成（1.3，与 frames.CELL_H_EM 同源）。
     返回实际字号，供调用方决定要不要提示"已缩到最小"。 */
  // 字符格高宽比（行高倍数）：主预览与字体墙卡片必须用同一份，
  // 否则同一幅字在两个位置的胖瘦不一样（用户第一次报"预览与字体墙不一致"）。
  //   cjk    1:2 点阵（服务端 cell_w = 2 × 行数）
  //   figlet FIGlet 字形按近方形字格设计
  //   img    服务端 frames.CELL_H_EM = 1.3
  //   braille 盲文打包（分享版）：1:2 网格，同 cjk
  var OUTPUT_LH = { cjk: 1.2, figlet: 1.05, img: 1.3, braille: 1.2 };
  /* 等宽 webfont（JetBrains Mono）没有盲文字形（U+2800-28FF，所有主流
     等宽字体都没有），浏览器回退到系统符号字体，advance 不再是 0.6em——
     字号贴合若写死 0.6 会横向溢出。用 canvas 按当前字体栈实测：同一字体
     栈解析出同一个回退字体，实测 advance 就是最终渲染宽度。按字符缓存。 */
  var ratioCache = {};
  function measuredRatio(ch, fallback) {
    if (!(ch in ratioCache)) {
      var w = fallback;
      try {
        var cs = getComputedStyle(taOutput);
        var ctx = document.createElement("canvas").getContext("2d");
        ctx.font = "100px " + cs.fontFamily;
        var m = ctx.measureText(ch);
        if (m && m.width > 0) w = m.width / 100;
      } catch (e) { /* 量不出就按 0.6 兜底 */ }
      ratioCache[ch] = w;
    }
    return ratioCache[ch];
  }
  function artCharRatio(art) {
    var s = String(art || "");
    for (var i = 0; i < s.length; i++) {
      var c = s.charCodeAt(i);
      if (c >= 0x2800 && c <= 0x28ff) return measuredRatio("\u28ff", 0.6);
    }
    return 0.6;  // 非盲文：等宽字符格 0.6em
  }
  function fitOutputFont() {
    if (!taOutput || !TA.art || !TA.cols || !TA.rows) return 0;
    var lh = OUTPUT_LH[TA.mode] || OUTPUT_LH.figlet;
    var cs = getComputedStyle(taOutput);
    var padX = (parseFloat(cs.paddingLeft) || 0) +
               (parseFloat(cs.paddingRight) || 0);
    var padY = (parseFloat(cs.paddingTop) || 0) +
               (parseFloat(cs.paddingBottom) || 0);
    var availW = taOutput.clientWidth - padX;
    var availH = taOutput.clientHeight - padY;
    if (availW <= 0 || availH <= 0) return 0;
    var CELL_W = artCharRatio(TA.art);
    var fs = Math.min(availW / (TA.cols * CELL_W), availH / (TA.rows * lh));
    // 下限 2px：超宽作品（如 4 字 × 50 行 = 400 列）需要 ~3px 才塞得下，
    // 旧的 4px 下限会让它横向溢出、右侧"显示不完全"。宁可小到看不清，
    // 也要整幅在框内；要原始尺寸用 .txt / .py / PNG 导出。
    fs = Math.max(2, Math.min(fs, 30));
    taOutput.style.lineHeight = lh;
    taOutput.style.fontSize = fs + "px";
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
    TA.mode = d.mode || "figlet";
    if (!taOutput) return;
    taOutput.classList.add("has-art");
    taOutput.textContent = d.art;
    var modeLabel = d.mode === "cjk"
      ? "中文点阵 · " + (d.font || "") +
        (CJK_CHARSET_NAMES[d.charset]
          ? " · " + CJK_CHARSET_NAMES[d.charset] : "")
      : d.mode === "braille"
      ? "盲文点阵 · 分享版（粘贴到记事本/微信都对齐）"
      : (d.font || "figlet");
    if (taPreviewTitle) {
      taPreviewTitle.textContent = "text art · " + modeLabel +
        " " + d.cols + "x" + d.rows;
    }
    setActionsEnabled(true);
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
  // 中文点阵的"点亮"字符集（字符墙选中项，slug 与服务端 CJK_CHARSETS 一致）。
  // 默认盲文：█+空格混排在中文字体环境（记事本/微信，█ 全角、空格半角）
  // 整幅错位没法分享；盲文整行同区同宽，复制/下载到哪都对齐（2026-10-09）。
  var taCJKCharset = "braille";
  var CJK_CHARSET_NAMES = { block: "字块", shade: "明暗", dot: "圆点",
                            geometric: "几何", braille: "盲文", ascii: "经典" };

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

  /* 字体提示文案：款数取自已加载的字体表。加载中不写数字——
     字体表是异步 fetch，写死 FIGLET_FONTS.length 会让首屏永远停在
     "精选 0 款字体"（选项其实早已 24 个）。 */
  function syncFontHint() {
    if (!taFontHint) return;
    if (fontSource === "cjk") {
      taFontHint.textContent = "中文点阵：宋体 / 楷体（系统字体光栅化）。默认盲文——复制或下载后粘到记事本、微信都对齐可分享；字符墙可换字块等终端样式。";
      return;
    }
    taFontHint.textContent = FIGLET_FONTS.length
      ? "FIGlet 精选 " + FIGLET_FONTS.length + " 款字体，选择作品的整体字形。字符墙首张「盲文·分享」卡把作品转成粘到哪都对齐的点阵形态。"
      : "FIGlet 精选字体加载中，选择作品的整体字形。字符墙首张「盲文·分享」卡把作品转成粘到哪都对齐的点阵形态。";
  }

  function setFontSource(source) {
    // 可见性不能放在"源未变化就 return"后面：首屏 fontSource 已是 figlet，
    // 第一次输入英文时不会再走 fillFontSelect 分支——尺寸分组（含英文字符
    // 宽度，2026-10-09 用户报"字符高度无法选择"）必须每次都刷新。
    var changed = fontSource !== source;
    fontSource = source;
    if (changed) fillFontSelect();
    var cjk = source === "cjk";
    if (cjkHeightGroup) {
      // 空输入时仍隐藏（没有可生成的对象）；有输入则两种模式都显示
      cjkHeightGroup.hidden = !taInput || !taInput.value.trim();
      var f1 = byId("taCjkHeightField"), f2 = byId("taCjkWidthHint"),
          f3 = byId("taFigWidthField"), f4 = byId("taFigHeightHint");
      if (f1) f1.hidden = !cjk;
      if (f2) f2.hidden = !cjk;
      if (f3) f3.hidden = cjk;
      if (f4) f4.hidden = cjk;
    }
    syncFontHint();
  }

  function loadFonts() {
    fetch("/api/text/fonts").then(function (r) { return r.json(); }).then(function (d) {
      if (d.ok && d.fonts) FIGLET_FONTS = d.fonts;
      if (fontSource === "figlet") fillFontSelect();
      syncFontHint();   // 字体表到位 → 立刻把真实款数写回提示
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

  /* 超上限判定：合法返回 null，超限返回 {kind, count, max, dropped}。
     服务端对英文是 400 拒绝、对汉字是静默截断（只取前 8 个），所以前端
     统一在超限时禁用生成按钮——不能让用户"点了才知道"。 */
  function inputOverflow() {
    var v = taInput ? taInput.value : "";
    if (!v.trim()) return null;
    if (hasCJK(v)) {
      var cjk = (v.match(/[\u4E00-\u9FFF\u3400-\u4DBF]/g) || []).length;
      if (cjk <= CJK_MAX_CHARS) return null;
      return { kind: "cjk", count: cjk, max: CJK_MAX_CHARS,
               dropped: cjk - CJK_MAX_CHARS };
    }
    var ascii = (v.match(/[\x20-\x7E]/g) || []).length;
    if (ascii <= FIGLET_MAX_CHARS) return null;
    return { kind: "figlet", count: ascii, max: FIGLET_MAX_CHARS,
             dropped: ascii - FIGLET_MAX_CHARS };
  }

  function syncConvertEnable() {
    if (!convertBtn) return;
    convertBtn.disabled = !!inputOverflow() || busy;
  }

  function syncInputHint() {
    if (!taInput || !taInputHint) return;
    var v = taInput.value;
    var over = inputOverflow();
    if (!v.trim()) {
      taInputHint.hidden = true;
      taInputHint.className = "ta-hint ta-input-hint";
      setFontSource("figlet");
      syncConvertEnable();
      return;
    }
    var cjkMode = hasCJK(v);
    var n = cjkMode ? (v.match(/[\u4E00-\u9FFF\u3400-\u4DBF]/g) || []).length
                    : (v.match(/[\x20-\x7E]/g) || []).length;
    var cap = cjkMode ? CJK_MAX_CHARS : FIGLET_MAX_CHARS;
    setFontSource(cjkMode ? "cjk" : "figlet");
    taInputHint.hidden = false;
    taInputHint.className = "ta-hint ta-input-hint" +
      (over ? " ta-hint-error" : "");
    if (over) {
      taInputHint.textContent = cjkMode
        ? "超出上限（" + n + "/" + cap + " 个汉字）：请删到 " + cap +
          " 个汉字以内再生成 / Over " + cap + " CJK chars: trim to " + cap +
          " to generate"
        : "超出上限（" + n + "/" + cap + " 字符）：请删到 " + cap +
          " 字符以内再生成 / Over " + cap + " chars: trim to " + cap +
          " to generate";
    } else if (cjkMode) {
      taInputHint.textContent = "检测到中文 → 点阵字体 · " +
        n + "/" + cap + " 个汉字";
    } else {
      taInputHint.textContent = n + "/" + cap + " 字符 · FIGlet 字体";
    }
    syncConvertEnable();
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

  /* 行高就地纠正：min/max 只约束 spinner 箭头，手打 9999 照样能提交
     （服务端内部会钳到 64，但不报错，用户以为生效了）。 */
  function clampCjkHeight() {
    if (!cjkHeightInput || cjkHeightInput.value === "") return;
    var min = parseInt(cjkHeightInput.min, 10) || 10;
    var max = parseInt(cjkHeightInput.max, 10) || 64;
    var n = parseInt(cjkHeightInput.value, 10);
    if (isNaN(n)) { cjkHeightInput.value = cjkHeightInput.defaultValue; return; }
    var c = Math.min(Math.max(n, min), max);
    if (String(c) === cjkHeightInput.value) return;
    cjkHeightInput.value = String(c);
    toast("字符高度限 " + min + "-" + max + " 行，已纠正为 " + c +
          " / Height clamped to " + min + "-" + max);
  }
  if (cjkHeightInput) {
    cjkHeightInput.addEventListener("change", clampCjkHeight);
    cjkHeightInput.addEventListener("blur", clampCjkHeight);
  }
  if (convertBtn) convertBtn.addEventListener("click", function () {
    if (busy) return;
    var text = taInput ? taInput.value : "";
    if (!text.trim()) { toast("请输入文字 / Enter some text"); return; }
    // 英文超 64 服务端必然 400 → 兜一层（按钮此时已 disabled，回车/程序化
    // 点击也不该发注定失败的请求）。中文超限服务端是静默截断，放行并提示。
    var overInfo = inputOverflow();
    if (overInfo && overInfo.kind !== "cjk") {
      toast("超出 64 字符上限：请删到 64 以内 / Max 64 chars");
      return;
    }
    busy = true;
    convertBtn.disabled = true; convertBtn.textContent = "生成中…";
    skeleton();
    var body = { text: text, font: taFont ? taFont.value : "" };
    // 中文点阵：行高随输入（10-64 行），英文 FIGlet 尺寸由字体决定、
    // 只露"字符宽度"（列上限，超宽换行）
    var requestedH = 0;
    if (fontSource === "cjk" && cjkHeightInput && cjkHeightInput.value) {
      clampCjkHeight();   // 手打 9999 也先纠正再发，服务端不接受越界值
      requestedH = parseInt(cjkHeightInput.value, 10) || 0;
      body.height = cjkHeightInput.value;
      body.charset = taCJKCharset;   // 字符墙选中的"点亮"字符
    } else if (fontSource === "figlet") {
      var figW = byId("taFigWidth");
      if (figW && figW.value) body.width = figW.value;
    }
    fetch("/api/text/convert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); }).then(function (d) {
      busy = false;
      convertBtn.textContent = "生成";
      if (d.error) {
        toast(d.error);   // 失败原因必须落到 toast（结果区小字常被忽略）
        showOutputError(d.error);
        hideFontWall();
        syncConvertEnable();
        return;
      }
      showArt(d);
      var notices = [];
      // 服务端对超长汉字是静默截断：真发生了就必须说出来，
      // 否则用户以为整段都生成了
      if (overInfo && overInfo.kind === "cjk") {
        notices.push("已按前 " + overInfo.max + " 个汉字生成，其余 " +
                     overInfo.dropped + " 个已忽略");
      }
      if (d.mode === "cjk") {
        loadCJKWall(text);   // 中文字符墙：点卡片换点亮字符（样张 → 重渲染）
        if (d.charset && CJK_CHARSET_NAMES[d.charset]) {
          taCJKCharset = d.charset;  // 服务端回落时同步本地状态
        }
        // 高度被按文本长度自动收缩时给出提示（宽度红线，见
        // textart.cjk_effective_height）
        if (requestedH >= 10 && d.height &&
            requestedH - d.height >= 2) {
          notices.push("字符高度已按文本长度收缩到 " + d.height +
                       " 行（总宽上限 " + (d.cols) + " 列，字越多收缩越狠）");
        }
      } else {
        loadFigletWall(text);  // FIGlet 成功 → 字体墙点亮，点卡片即换
      }
      if (notices.length) toast(notices.join(" · "));
      syncConvertEnable();
    }).catch(function () {
      busy = false;
      convertBtn.textContent = "生成";
      toast("网络异常，请重试 / Network error, retry");
      showOutputError("网络异常，请重试 / Network error, retry");
      hideFontWall();
      syncConvertEnable();
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
    // 还没有作品时配色点只是摆设：点了也不改状态（首屏可见，不能报错）
    if (!dot || dot.hidden || !TA.art) return;
    applyTheme(dot.getAttribute("data-theme"));
  });
  /* 配色行现在两种模式共用（原来图片模式把配色放在左面板，位置和英文字符化
     不一致）。「原色」只有图片艺术化有源像素可取，文字模式下隐藏。
     配色行本身常驻（首屏就在结果行里），不再按有无作品隐藏。 */
  function syncThemeRow() {
    if (!taThemeRow) return;
    if (taThemeSource) taThemeSource.hidden = (taMode !== "image");
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


  /* ── /text-art 首屏瘦身：字体墙 + 图片艺术化改成按需加载 ──
     这两块（原 734-922、952-1127 行）搬进 static/js/text_art_lazy.js，
     只有「字符生成成功」「切到图片模式」时才注入脚本；本文件只留 4 个薄壳。
     桥：本文件暴露 __TA_LAZY_CTX（渲染/状态句柄），懒包回传 __TA_LAZY（4 入口），
     两边都不复制渲染逻辑。 */
  var LAZY_SRC = "/static/js/text_art_lazy.js?v=20261027a";
  var lazyApi = null, lazyLoading = false, lazyQueue = [];

  function lazyReady(fn) {
    if (lazyApi) { if (fn) fn(lazyApi); return; }
    if (fn) lazyQueue.push(fn);
    if (lazyLoading) return;
    lazyLoading = true;
    var s = document.createElement("script");
    s.src = LAZY_SRC;
    s.async = true;
    s.onload = function () {
      lazyLoading = false;
      lazyApi = window.__TA_LAZY || null;
      var q = lazyQueue;
      lazyQueue = [];
      for (var i = 0; i < q.length; i++) q[i](lazyApi);
    };
    s.onerror = function () { lazyLoading = false; lazyQueue = []; };
    document.head.appendChild(s);
  }

  function hideFontWall() { if (lazyApi) lazyApi.hideFontWall(); }
  function fitFontwallArt() { if (lazyApi) lazyApi.fitFontwallArt(); }
  function loadFigletWall(text) { lazyReady(function (api) { if (api) api.loadFigletWall(text); }); }
  function loadCJKWall(text) { lazyReady(function (api) { if (api) api.loadCJKWall(text); }); }

  window.__TA_LAZY_CTX = {
    byId: byId, toast: toast, esc: esc,
    showArt: showArt, artCharRatio: artCharRatio, hasCJK: hasCJK,
    applyTheme: applyTheme, syncThemeRow: syncThemeRow,
    fitOutputFont: fitOutputFont, skeleton: skeleton,
    setActionsEnabled: setActionsEnabled,
    showOutputError: showOutputError, OUTPUT_LH: OUTPUT_LH, TA: TA,
    els: {
      output: taOutput, previewTitle: taPreviewTitle, metaText: taMetaText,
      resultMeta: taResultMeta, input: taInput, font: taFont, convertBtn: convertBtn
    },
    state: {
      mode: function () { return taMode; },
      cjkCharset: function () { return taCJKCharset; },
      setCjkCharset: function (v) { taCJKCharset = v; },
      fontSource: function () { return fontSource; },
      isBusy: function () { return busy; }
    },
    theme: function () { return currentTheme; }
  };

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
    if (isImage) lazyReady();  // 图片模式的控制逻辑在懒包里，先拉下来再绑事件
    if (taTextPanel) taTextPanel.hidden = isImage;
    if (taImagePanel) taImagePanel.hidden = !isImage;
    hideFontWall();  // 切模式时清字体墙
    if (taOutput) taOutput.style.color = "";  // 换模式别留上一模式的主题色
    syncThemeRow();
  });


  /* ── init ── */
  loadFonts();
  syncInputHint();
  showEmpty();  // 增强空态：含示例 chips（替换 HTML 硬编码版本）
  /* 字体墙首屏就渲染真卡片（默认样例）：生成时只换卡片内容、容器高度不变。
     代价是首屏多 1 次 /static/js/text_art_lazy.js + 1 次 /api/text/fontwall。 */
  loadFigletWall(FW_SAMPLE);
  /* webfont（JetBrains Mono）就绪/晚到会改变字符 advance → 实测缓存作废，
     重贴一次字号（盲文走系统回退字体，不受 webfont 加载影响）。 */
  if (document.fonts) {
    var refitAll = function () {
      ratioCache = {};
      fitOutputFont();
      fitFontwallArt();
    };
    if (document.fonts.ready && document.fonts.ready.then) {
      document.fonts.ready.then(refitAll);
    }
    if (document.fonts.addEventListener) {
      document.fonts.addEventListener("loadingdone", refitAll);
    }
  }
})();
