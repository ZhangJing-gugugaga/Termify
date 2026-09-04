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
    if (taResultMeta) taResultMeta.hidden = true;  // 错误态不残留旧作品 meta
  }
  var EXAMPLES = [
    { kind: "figlet", label: "hello", value: "hello" },
    { kind: "ai", label: "火焰感的 HELLO", value: "火焰感的 HELLO" },
    { kind: "ai-direct", label: "画一只猫", value: "画一只猫" },
    { kind: "ai-direct", label: "戴墨镜的狗", value: "一只戴墨镜的狗" }
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
        (d.mode === "direct" ? "AI direct" : (d.font || "figlet")) +
        " " + d.cols + "x" + d.rows;
    }
    var label = d.mode === "direct" ? "AI 直接创作"
      : (d.mode === "params" ? "AI · " + (d.font || "") : (d.font || ""));
    if (taMetaText) taMetaText.textContent = label + " · " + d.cols + " x " + d.rows;
    if (taResultMeta) taResultMeta.hidden = false;
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
      showArt(d);
      hideFontWall();
    }).catch(function () {
      busy = false; waitbarStop();
      aiBtn.disabled = false; aiBtn.textContent = "AI 生成";
      showOutputError("网络异常，请重试 / Network error, retry");
    });
  });

  /* ── 复制 / 下载 / 分享 ── */
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
    a.download = "termify_ascii_art.txt";
    document.body.appendChild(a); a.click(); a.remove();
    URL.revokeObjectURL(a.href);
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
    var cards = (d.showcase || []).map(function (ex, i) {
      return '<button type="button" class="ta-demo-card" data-prompt="' +
        esc(ex.prompt) + '" data-idx="' + i + '">' +
        '<span class="ta-demo-art">' + esc(ex.art) + "</span>" +
        '<span class="ta-demo-label">' + esc(ex.title) + " · " +
        esc(ex.prompt) + "</span></button>";
    }).join("");
    taOutput.innerHTML =
      '<div class="ta-needconfig">' +
      '<p class="ta-nc-title">还没有接入 LLM —— 先看看 AI 能画什么：</p>' +
      '<div class="ta-demo-grid">' + cards + "</div>" +
      '<p class="ta-nc-guide">点卡片取走提示词，配置后即可生成同款 ' +
      '<button type="button" class="ta-nc-btn" id="taNcConfigBtn">⚙ 三步开通</button></p>' +
      "</div>";
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
        return '<button type="button" class="ta-fw-card" data-slug="' +
          esc(f.slug) + '" title="' + esc(f.name) + '">' +
          '<span class="ta-fw-art">' + esc(f.art) + "</span>" +
          '<span class="ta-fw-name">' + esc(f.name) + "</span></button>";
      }).join("");
      markActiveFontCard();
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
    if (convertBtn) convertBtn.click();
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
    if (ncBtn) { openSettings(); return; }
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
    } else if (convertBtn) {
      convertBtn.click();
    }
  });

  /* ── 自部署预设一键填充 ── */
  var LLM_PRESETS = {
    ollama: { base_url: "http://localhost:11434/v1", model: "qwen2.5:7b" },
    zhipu: { base_url: "https://open.bigmodel.cn/api/paas/v4", model: "glm-4-flash" },
    deepseek: { base_url: "https://api.deepseek.com/v1", model: "deepseek-chat" }
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
      });
    });

  /* ── init ── */
  loadFonts();
  syncModeHint();
  showEmpty();  // 增强空态：含示例 chips（替换 HTML 硬编码版本）
})();
