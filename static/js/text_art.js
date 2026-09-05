/* Termify · 文字艺术独立页（/text-art）
   FIGlet 直转 + LLM 双模式 + 发布画廊。
   UX 契约：所有异步动作都有等待反馈（按钮 busy / 骨架 / 等待条缓冲文案）。 */
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

  /* ── Termify 弹窗（与主页同款：成功通知 / 错误提示） ── */
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
  var taWaitbar = byId("taWaitbar");
  var taWaitText = byId("taWaitText");
  var taResultMeta = byId("taResultMeta");
  var taMetaText = byId("taMetaText");
  var taPreviewTitle = byId("taPreviewTitle");
  var taFontHint = byId("taFontHint");
  var taModeHint = byId("taModeHint");
  var busy = false;

  var AI_WAIT_MESSAGES = [
    "正在理解你的描述…",
    "正在挑选合适的字形…",
    "正在排列字符画…",
    "还在润色，别急…",
    "快好了…",
  ];
  var waitTimer = null;
  function waitbarStart(mode) {
    if (!taWaitbar) return;
    taWaitbar.hidden = false;
    var i = 0;
    var first = mode === "direct" ? "AI 直接创作通常需要几秒到十几秒…" : "AI 正在解析你的描述…";
    if (taWaitText) taWaitText.textContent = first;
    waitTimer = setInterval(function () {
      i = (i + 1) % AI_WAIT_MESSAGES.length;
      if (taWaitText) taWaitText.textContent = AI_WAIT_MESSAGES[i];
    }, 2600);
  }
  function waitbarStop() {
    if (waitTimer !== null) { clearInterval(waitTimer); waitTimer = null; }
    if (taWaitbar) taWaitbar.hidden = true;
  }

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
  var EXAMPLES = [
    { kind: "figlet", label: "hello", value: "hello" },
    { kind: "ai", label: "火焰感的 HELLO", value: "火焰感的 HELLO" },
    { kind: "ai-direct", label: "画一只猫", value: "画一只猫" },
    { kind: "cjk", label: "你好世界", value: "你好世界" }
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
    if (taPreviewTitle) {
      taPreviewTitle.textContent = "text art · " +
        (d.mode === "direct" ? "AI direct"
          : (d.mode === "cjk" ? "cjk · " + (d.style || "")
            : (d.font || "figlet"))) +
        " " + d.cols + "x" + d.rows;
    }
    var label = d.mode === "direct" ? "AI 直接创作"
      : (d.mode === "params" ? "AI · " + (d.font || "")
        : (d.mode === "cjk" ? "中文艺术字 · " + (d.style || "")
          : (d.font || "")));
    if (taMetaText) taMetaText.textContent = label + " · " + d.cols + " x " + d.rows;
    if (taResultMeta) taResultMeta.hidden = false;
    if (taThemeRow) taThemeRow.hidden = false;  // 有作品 → 配色行可见
  }

  /* ── 字体列表 ── */
  function loadFonts() {
    fetch("/api/text/fonts").then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok || !taFont) return;
      taFont.innerHTML = "";
      d.fonts.forEach(function (f) {
        var o = document.createElement("option");
        o.value = f.slug;
        o.textContent = f.name;
        taFont.appendChild(o);
      });
      taFont.disabled = false;
      if (taFont.querySelector('option[value="standard"]')) {
        taFont.value = "standard";  // 与服务端 DEFAULT_FONT 对齐
      }
      if (taFontHint) taFontHint.textContent = "FIGlet 精选 " + d.fonts.length + " 款字体，选择作品的整体字形。";
    }).catch(function () {
      if (taFontHint) taFontHint.textContent = "字体加载失败，请刷新页面重试。";
    });
  }

  /* ── AI 模式提示 ── */
  function syncModeHint() {
    var el = document.querySelector('input[name="taAiMode"]:checked');
    if (taModeHint) {
      taModeHint.textContent = (el && el.value === "direct")
        ? "AI 亲自创作字符画，可表达中文与图形概念，耗时稍长。"
        : "AI 从描述中提取文字与字体，本地精确渲染，快且稳定。";
    }
  }
  var modeRadios = document.querySelectorAll('input[name="taAiMode"]');
  modeRadios.forEach(function (r) { r.addEventListener("change", syncModeHint); });

  /* ── 生成（直转） ── */
  var convertBtn = byId("taConvertBtn");
  if (convertBtn) convertBtn.addEventListener("click", function () {
    if (busy) return;
    var text = taInput ? taInput.value : "";
    if (!text.trim()) { toast("请输入文字 / Enter some text"); return; }
    busy = true;
    convertBtn.disabled = true; convertBtn.textContent = "生成中…";
    if (taResultMeta) taResultMeta.hidden = true;
    skeleton();
    fetch("/api/text/convert", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text, font: taFont ? taFont.value : "standard" })
    }).then(function (r) { return r.json(); }).then(function (d) {
      busy = false;
      convertBtn.disabled = false; convertBtn.textContent = "生成";
      if (d.error) { showOutputError(d.error); hideFontWall(); return; }
      showArt(d);
      loadFontWall(text);  // 直转成功 → 字体墙点亮，点卡片即换
    }).catch(function () {
      busy = false;
      convertBtn.disabled = false; convertBtn.textContent = "生成";
      showOutputError("网络异常，请重试 / Network error, retry");
      hideFontWall();
    });
  });

  /* ── AI 候选区 + 迭代输入条（P2 创作伙伴化） ── */
  var taIterateBar = null;

  /* 候选条/迭代条插入锚点：terminal 窗口之后（不在窗口内部） */
  function stageAnchor() {
    if (!taOutput) return null;
    return taOutput.closest(".terminal") || taOutput.parentNode;
  }

  function insertAfterTerminal(el) {
    var anchor = stageAnchor();
    if (anchor && anchor.parentNode) {
      anchor.parentNode.insertBefore(el, anchor.nextSibling);
    }
  }

  function showVariants(d) {
    // direct 多候选：首版进主舞台，其余进候选条
    showArt(d);
    if (d.variants && d.variants.length > 1) {
      removeIterateBar();
      removeVariantBar();
      var bar = document.createElement("div");
      bar.className = "ta-variant-bar";
      bar.id = "taVariantBar";
      d.variants.forEach(function (v, i) {
        var card = document.createElement("button");
        card.type = "button";
        card.className = "ta-variant-card" + (i === 0 ? " active" : "");
        card.setAttribute("data-idx", i);
        card.title = v.cols + " x " + v.rows;
        var mini = document.createElement("span");
        mini.className = "ta-variant-mini";
        mini.textContent = v.art.split("\n").slice(0, 6).join("\n");
        card.appendChild(mini);
        var label = document.createElement("span");
        label.className = "ta-variant-label";
        label.textContent = "候选 " + (i + 1) + " · " + v.cols + "x" + v.rows;
        card.appendChild(label);
        bar.appendChild(card);
      });
      if (taOutput && taOutput.parentNode) {
        insertAfterTerminal(bar);
      }
      bar.addEventListener("click", function (e) {
        var card2 = e.target.closest(".ta-variant-card");
        if (!card2) return;
        var idx = parseInt(card2.getAttribute("data-idx"), 10) || 0;
        var v = d.variants[idx];
        if (v) {
          showArt({ art: v.art, cols: v.cols, rows: v.rows,
                    mode: "direct", font: "" });
          bar.querySelectorAll(".ta-variant-card").forEach(function (c) {
            c.classList.remove("active");
          });
          card2.classList.add("active");
          showIterateBar(v.art);  // 迭代对象跟随选中的候选
        }
      });
      showIterateBar(d.variants[0].art);
    } else {
      showIterateBar(d.art);
    }
  }

  function removeVariantBar() {
    var old = byId("taVariantBar");
    if (old) old.remove();
  }

  function removeIterateBar() {
    var oldBar = byId("taIterateBar");
    if (oldBar) oldBar.remove();
    taIterateBar = null;
  }

  function showIterateBar(art) {
    removeIterateBar();  // 只清迭代条，保留候选条
    var wrap = document.createElement("div");
    wrap.className = "ta-iterate-bar";
    wrap.id = "taIterateBar";
    wrap.innerHTML =
      '<input type="text" class="ta-iterate-input" id="taIterateInput" ' +
      'maxlength="500" placeholder="继续修改：更大一点 / 加个边框 / 换成侧面…">' +
      '<button type="button" class="ta-btn ta-btn-sm ta-btn-primary" ' +
      'id="taIterateBtn">迭代</button>';
    if (taOutput && taOutput.parentNode) {
      insertAfterTerminal(wrap);
    }
    var input = byId("taIterateInput");
    var btn = byId("taIterateBtn");
    if (input && btn) {
      input.addEventListener("keydown", function (e) {
        if (e.key === "Enter" && !busy) btn.click();
      });
      btn.addEventListener("click", function () {
        var ins = (input.value || "").trim();
        if (!ins) { toast("说说要改哪里 / Describe the change"); return; }
        btn.disabled = true; btn.textContent = "修改中…";
        fetch("/api/text/iterate", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ current_art: art, instruction: ins })
        }).then(function (r) { return r.json(); }).then(function (d2) {
          btn.disabled = false; btn.textContent = "迭代";
          if (d2.error) { toast(d2.error); return; }
          showArt({ art: d2.art, cols: d2.cols, rows: d2.rows,
                    mode: "iterate", font: "" });
          showIterateBar(d2.art);  // 迭代结果可继续迭代
          if (d2.auto_fitted) toast("已自动适配尺寸");
        }).catch(function () {
          btn.disabled = false; btn.textContent = "迭代";
          toast("网络异常，请重试");
        });
      });
    }
  }

  /* ── AI 生成（双模式） ── */
  var aiBtn = byId("taAiBtn");
  if (aiBtn) aiBtn.addEventListener("click", function () {
    if (busy) return;
    var text = taInput ? taInput.value : "";
    if (!text.trim()) { toast("请描述你想要的作品 / Describe what you want"); return; }
    var modeEl = document.querySelector('input[name="taAiMode"]:checked');
    var mode = modeEl ? modeEl.value : "params";
    busy = true;
    aiBtn.disabled = true; aiBtn.textContent = "AI 生成中…";
    if (taResultMeta) taResultMeta.hidden = true;
    skeleton();
    waitbarStart(mode);
    fetch("/api/text/ai", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ prompt: text, mode: mode })
    }).then(function (r) { return r.json(); }).then(function (d) {
      busy = false; waitbarStop();
      aiBtn.disabled = false; aiBtn.textContent = "AI 生成";
      if (d.error) {
        if (d.need_config) {
          showNeedConfig(d);  // 示例墙替代裸报错
        } else {
          showOutputError(d.error);
        }
        hideFontWall();  // AI 路径与字体墙互斥
        return;
      }
      if (d.mode === "direct" && d.variants) {
        showVariants(d);  // 多候选 + 迭代条
      } else if (d.mode === "direct") {
        showVariants(d);  // 单版也带迭代条
      } else {
        showArt(d);
        showIterateBar(d.art);  // params 结果同样可迭代
      }
      hideFontWall();
    }).catch(function () {
      busy = false; waitbarStop();
      aiBtn.disabled = false; aiBtn.textContent = "AI 生成";
      showOutputError("网络异常，请重试 / Network error, retry");
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

  /* ── AI 设置 ── */
  function openSettings() {
    var panel = byId("taSettings");
    var btn = byId("taSettingsBtn");
    if (!panel) return;
    panel.hidden = false;
    if (btn) btn.setAttribute("aria-expanded", "true");
    fetch("/api/llm/config").then(function (r) { return r.json(); }).then(function (d) {
      if (!d.ok) return;
      var bu = byId("taBaseUrl"), mo = byId("taModel");
      if (bu) bu.value = d.base_url || "";
      if (mo) mo.value = d.model || "";
      var af = byId("taAdminField");
      if (af) af.hidden = !d.requires_admin;
      var st = byId("taStatus");
      if (st) {
        st.className = "ta-status";
        st.textContent = d.configured
          ? ("已配置：" + d.model + (d.has_key ? "（key 已保存）" : "（未设 key，适用于本地端点）"))
          : "未配置 / Not configured";
      }
    }).catch(function () {});
  }
  var settingsBtn = byId("taSettingsBtn");
  if (settingsBtn) settingsBtn.addEventListener("click", function () {
    var panel = byId("taSettings");
    if (!panel) return;
    if (panel.hidden) { openSettings(); }
    else { panel.hidden = true; settingsBtn.setAttribute("aria-expanded", "false"); }
  });
  var saveBtn = byId("taSaveBtn");
  if (saveBtn) saveBtn.addEventListener("click", function () {
    var body = {
      base_url: byId("taBaseUrl") ? byId("taBaseUrl").value.trim() : "",
      model: byId("taModel") ? byId("taModel").value.trim() : ""
    };
    var keyEl = byId("taApiKey");
    if (keyEl && keyEl.value.trim()) body.api_key = keyEl.value.trim();
    var pwdEl = byId("taAdminPwd");
    if (pwdEl && pwdEl.value) body.admin_pwd = pwdEl.value;
    saveBtn.disabled = true;
    var st = byId("taStatus");
    if (st) { st.textContent = "保存中…"; st.className = "ta-status"; }
    fetch("/api/llm/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body)
    }).then(function (r) { return r.json(); }).then(function (d) {
      saveBtn.disabled = false;
      if (!st) return;
      if (d.error) { st.textContent = d.error; st.className = "ta-status err"; return; }
      if (keyEl) keyEl.value = "";
      if (pwdEl) pwdEl.value = "";
      st.textContent = "已保存：" + d.model;
      st.className = "ta-status ok";
    }).catch(function () {
      saveBtn.disabled = false;
      if (st) { st.textContent = "保存失败 / Save failed"; st.className = "ta-status err"; }
    });
  });

  /* ── 未配置 LLM 时的示例墙：先看 AI 能画什么，再引导配置 ── */
  function esc(s) {
    return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function showNeedConfig(d) {
    if (!taOutput) return;
    taOutput.classList.remove("has-art");
    // 示例池按 6 主题批次排列（每批 4 幅）：初始 + 5 次刷新 = 全量 24 幅零重复
    var pool = d.showcase || [];
    var BATCH = 4, MAX_REFRESH = 5;
    var batchIdx = 0;
    function fitDemoArt() {
      taOutput.querySelectorAll(".ta-demo-art").forEach(function (el) {
        el.style.fontSize = "";
        var sw = el.scrollWidth, cw = el.clientWidth;
        if (sw > cw && sw > 0) {
          var base = parseFloat(getComputedStyle(el).fontSize) || 9;
          var fit = Math.max(4, Math.floor(base * cw / sw * 10) / 10);
          el.style.fontSize = fit + "px";
        }
      });
    }
    function renderBatch() {
      var grid = byId("taDemoGrid");
      if (!grid) return;
      var items = pool.slice(batchIdx * BATCH, batchIdx * BATCH + BATCH);
      grid.innerHTML = items.map(function (ex) {
        return '<button type="button" class="ta-demo-card" data-prompt="' +
          esc(ex.prompt) + '">' +
          '<span class="ta-demo-art">' + esc(ex.art) + "</span>" +
          '<span class="ta-demo-label">' + esc(ex.title) + " · " +
          esc(ex.prompt) + "</span></button>";
      }).join("");
      fitDemoArt();
      requestAnimationFrame(fitDemoArt);
      var btn = byId("taNcRefreshBtn");
      if (btn) {
        var left = MAX_REFRESH - batchIdx;
        if (left > 0) {
          btn.textContent = "换一批（剩 " + left + " 次）";
        } else {
          btn.textContent = "已展示全部 " + pool.length + " 幅";
          btn.disabled = true;
        }
      }
    }
    taOutput.innerHTML =
      '<div class="ta-needconfig">' +
      '<p class="ta-nc-title">还没有接入 LLM —— 先看看 AI 能画什么：</p>' +
      '<div class="ta-demo-grid" id="taDemoGrid"></div>' +
      '<p class="ta-nc-guide">点卡片取走提示词，配置后即可生成同款 ' +
      '<button type="button" class="ta-nc-btn" id="taNcRefreshBtn">换一批</button>' +
      '<button type="button" class="ta-nc-btn" id="taNcConfigBtn">⚙ 三步开通</button></p>' +
      "</div>";
    var refreshBtn = byId("taNcRefreshBtn");
    if (refreshBtn) refreshBtn.addEventListener("click", function () {
      if (batchIdx >= MAX_REFRESH) return;
      batchIdx += 1;
      renderBatch();
    });
    renderBatch();
    if (taPreviewTitle) taPreviewTitle.textContent = "ai preview";
    if (taResultMeta) taResultMeta.hidden = true;
  }
  /* ── 字体墙：直转文本在全部字体下的预览，点击即换 ── */
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

  var taInputHint = byId("taInputHint");
  var FIGLET_MAX_CHARS = 64;  // 与服务端 textart.TEXT_MAX_CHARS 一致
  function syncInputHint() {
    if (!taInput || !taInputHint) return;
    var v = taInput.value;
    if (!v.trim()) {
      taInputHint.hidden = true;
      taInputHint.classList.remove("ta-hint-ai", "ta-hint-warn");
      if (aiBtn) aiBtn.classList.remove("ta-ai-suggest");
      return;
    }
    var ascii = (v.match(/[\x20-\x7E]/g) || []).length;
    var nonAscii = /[^\x00-\x7F\s]/.test(v);
    if (nonAscii) {
      taInputHint.hidden = false;
      taInputHint.className = "ta-hint ta-input-hint ta-hint-ai";
      taInputHint.textContent =
        "检测到中文/全角字符——直转仅支持英文与数字，试试「AI 生成」（Ctrl+Enter）";
      if (aiBtn) aiBtn.classList.add("ta-ai-suggest");
      return;
    }
    if (aiBtn) aiBtn.classList.remove("ta-ai-suggest");
    taInputHint.hidden = false;
    taInputHint.className = "ta-hint ta-input-hint" +
      (ascii > FIGLET_MAX_CHARS ? " ta-hint-warn" : "");
    taInputHint.textContent = ascii + "/" + FIGLET_MAX_CHARS +
      " 字符 · Enter 直转 / Ctrl+Enter AI";
  }
  if (taInput) {
    taInput.addEventListener("input", syncInputHint);
    taInput.addEventListener("keydown", function (e) {
      if (e.key !== "Enter" || e.shiftKey || busy) return;
      e.preventDefault();
      if (e.ctrlKey || e.metaKey) {
        if (aiBtn) aiBtn.click();
      } else if (convertBtn) {
        convertBtn.click();
      }
    });
  }

  /* ── 示例墙交互：点卡片→提示词填入输入框；点「三步开通」→展开部署引导 ── */
  if (taOutput) taOutput.addEventListener("click", function (e) {
    var ncBtn = e.target.closest(".ta-nc-btn");
    // 「换一批」有自己的直达监听，不走设置面板
    if (ncBtn && ncBtn.id !== "taNcRefreshBtn") { openSettings(); return; }
    var card = e.target.closest(".ta-demo-card");
    if (card) {
      if (taInput) {
        taInput.value = card.getAttribute("data-prompt") || "";
        syncInputHint();
        toast("提示词已填入，配置后点「AI 生成」");
      }
      return;
    }
    var chip = e.target.closest(".ta-chip");
    if (!chip) return;
    if (taInput) taInput.value = chip.getAttribute("data-value") || "";
    syncInputHint();
    var kind = chip.getAttribute("data-kind");
    if (kind === "ai" || kind === "ai-direct") {
      var want = kind === "ai-direct" ? "direct" : "params";
      var radio = document.querySelector(
        'input[name="taAiMode"][value="' + want + '"]');
      if (radio && !radio.checked) { radio.checked = true; syncModeHint(); }
      if (aiBtn) aiBtn.click();
    } else if (kind === "cjk") {
      if (cjkBtn && !cjkBtn.disabled) cjkBtn.click();
      else toast("中文艺术字需先配置 LLM（点「⚙ 自部署 AI」）");
    } else if (convertBtn) {
      convertBtn.click();
    }
  });

  /* ── 自部署预设一键填充（仅支持 OpenAI 兼容服务） ── */
  var LLM_PRESETS = {
    ollama: { base_url: "http://localhost:11434/v1", model: "qwen2.5:7b" },
    zhipu: { base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4-flash" },
    deepseek: { base_url: "https://api.deepseek.com/v1", model: "deepseek-chat" },
    longcat: { base_url: "https://api.longcat.chat/openai", model: "LongCat-Flash-Chat" },
    custom: { base_url: "", model: "" }  // 清空自填：任意 OpenAI 兼容端点
  };
  Array.prototype.forEach.call(
    document.querySelectorAll(".ta-deploy-presets [data-preset]"),
    function (b) {
      b.addEventListener("click", function () {
        var p = LLM_PRESETS[b.getAttribute("data-preset")];
        if (!p) return;
        var bu = byId("taBaseUrl"), mo = byId("taModel");
        if (bu) bu.value = p.base_url;
        if (mo) mo.value = p.model;
        if (!p.base_url && bu) bu.focus();  // 自定义：清空后聚焦引导手填
      });
    });

  /* ── 中文艺术字（汉字活字引擎：字形缓存 + 本地混排） ── */
  var cjkBtn = byId("taCjkBtn");
  var cjkStyleSel = byId("taCjkStyle");
  var cjkHint = byId("taCjkHint");

  function loadCjkStyles() {
    if (!cjkStyleSel) return;
    fetch("/api/cjk/styles").then(function (r) { return r.json(); })
      .then(function (d) {
        if (!d.ok || !d.styles || !d.styles.length) return;
        cjkStyleSel.innerHTML = "";
        d.styles.forEach(function (s) {
          var o = document.createElement("option");
          o.value = s.slug;
          o.textContent = s.name + "（" + s.height + "×" + s.width + "）";
          cjkStyleSel.appendChild(o);
        });
      }).catch(function () { /* 保留 HTML 硬编码的 3 个静态选项 */ });
  }

  /* 未配置 LLM 时置灰 + 提示（对齐 AI 模式交互） */
  function syncCjkAvailability() {
    if (!cjkBtn) return;
    fetch("/api/llm/config").then(function (r) { return r.json(); })
      .then(function (d) {
        var configured = !!(d && d.configured);
        cjkBtn.disabled = !configured;
        if (cjkHint) {
          cjkHint.textContent = configured
            ? "汉字活字引擎：为每个汉字生成等宽字符画字形并缓存，首次稍慢。"
            : "中文艺术字由你自部署的 LLM 驱动——点「⚙ 自部署 AI」三步开通后可用。";
        }
      }).catch(function () { /* 查询失败时保持可用，由后端兜底提示 */ });
  }

  if (cjkBtn) cjkBtn.addEventListener("click", function () {
    if (busy || cjkBtn.disabled) return;
    var text = taInput ? taInput.value : "";
    if (!text.trim()) {
      toast("请输入 1-12 个汉字 / Enter 1-12 Chinese characters");
      return;
    }
    busy = true;
    cjkBtn.disabled = true; cjkBtn.textContent = "生成中…";
    if (taResultMeta) taResultMeta.hidden = true;
    skeleton();
    waitbarStart("direct");  // 首个未缓存字形需真实调用 LLM，耗时同 AI 直创
    fetch("/api/cjk/render", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text,
                             style: cjkStyleSel ? cjkStyleSel.value : "pixel" })
    }).then(function (r) { return r.json(); }).then(function (d) {
      busy = false; waitbarStop();
      cjkBtn.disabled = false; cjkBtn.textContent = "中文艺术字";
      if (d.error) {
        if (d.need_config) { showNeedConfig(d); }
        else { showOutputError(d.error); }
        hideFontWall();
        return;
      }
      showArt({ art: d.art, cols: d.cols, rows: d.rows,
                mode: "cjk", font: "", style: d.style,
                text: text.slice(0, 80) });
      if (d.missing && d.missing.length) {
        toast("「" + d.missing.join("") + "」生成失败，已用实心块占位");
      }
      hideFontWall();  // 中文路径与 FIGlet 字体墙互斥
    }).catch(function () {
      busy = false; waitbarStop();
      cjkBtn.disabled = false; cjkBtn.textContent = "中文艺术字";
      showOutputError("网络异常，请重试 / Network error, retry");
    });
  });

  /* ── init ── */
  loadFonts();
  loadCjkStyles();
  syncCjkAvailability();
  syncModeHint();
  showEmpty();  // 增强空态：含示例 chips（替换 HTML 硬编码版本）
})();
