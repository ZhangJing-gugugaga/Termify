        var toastTimer = null;
        function toast(msg) {
            var el = document.getElementById("toast"); if (!el) return;
            el.textContent = msg; el.classList.add("show");
            if (toastTimer !== null) clearTimeout(toastTimer);
            toastTimer = setTimeout(function () { el.classList.remove("show"); }, 2200);
        }

        var G = { sort: "latest", tags: [], page: 1, limit: 24, total: 0, hasMore: false };

        // 深链（B1）：sort/page/limit/tags 从 location.search 读，非法值按服务端
        // 同一套回退规则归一（sort 白名单、page>=1、limit 1..60、tags 最多 10 个）。
        function readStateFromUrl() {
            var q = new URLSearchParams(window.location.search);
            var sort = q.get("sort");
            if (sort === "hot" || sort === "random" || sort === "latest") G.sort = sort;
            else G.sort = "latest";
            var page = parseInt(q.get("page"), 10);
            G.page = (isNaN(page) || page < 1) ? 1 : page;
            var limit = parseInt(q.get("limit"), 10);
            G.limit = (isNaN(limit) || limit < 1) ? 24 : Math.min(60, limit);
            var raw = (q.get("tags") || "").split(",");
            var tags = [];
            for (var i = 0; i < raw.length; i++) {
                var t = raw[i].trim();
                if (t && tags.indexOf(t) < 0 && tags.length < 10) tags.push(t);
            }
            G.tags = tags;
        }

        // 状态一变就把地址栏同步成同一状态（replaceState：不新增历史记录）。
        // 默认值不写进 URL，/gallery 保持干净。
        function syncUrl() {
            var p = new URLSearchParams();
            if (G.sort !== "latest") p.set("sort", G.sort);
            if (G.page > 1) p.set("page", G.page);
            if (G.limit !== 24) p.set("limit", G.limit);
            if (G.tags.length) p.set("tags", G.tags.join(","));
            var qs = p.toString();
            if (window.history && history.replaceState) {
                history.replaceState(null, "", window.location.pathname + (qs ? "?" + qs : ""));
            }
        }

        // 存储型 XSS 防线：服务端只截断不转义，所有插进 innerHTML 的
        // 用户可控字符串（title/author/tags 等）必须先过 escapeHtml。
        // 含引号转义（属性上下文 alt="..."/href="..." 也不能逃逸）。
        function escapeHtml(s) {
            return String(s == null ? "" : s)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#39;");
        }

        function showSkeleton(on) {
            // 首屏/翻页期间先摆 8 个骨架卡：网格高度稳定，卡片到达时
            // 不再"啪"地顶一下整页（布局跳动的最大来源）
            var grid = document.getElementById("galleryGrid");
            if (!grid) return;
            if (!on) {
                if (grid.querySelector(".gallery-skeleton")) grid.innerHTML = "";
                return;
            }
            var html = "";
            for (var i = 0; i < 8; i++) {
                html += '<div class="gallery-skeleton"><div class="gallery-skeleton-img"></div>' +
                    '<div class="gallery-skeleton-info">' +
                    '<div class="gallery-skeleton-line"></div>' +
                    '<div class="gallery-skeleton-line short"></div></div></div>';
            }
            grid.innerHTML = html;
        }

        function setGalleryError(on) {
            var err = document.getElementById("galleryError");
            if (err) err.hidden = !on;
            if (!on) return;
            var empty = document.getElementById("galleryEmpty");
            if (empty) empty.style.display = "none";
            var pg = document.getElementById("galleryPagination");
            if (pg) pg.innerHTML = "";
        }

        function fetchGallery() {
            var params = new URLSearchParams({ sort: G.sort, page: G.page, limit: G.limit });
            if (G.tags.length) params.set("tags", G.tags.join(","));
            syncUrl();
            setGalleryError(false);
            showSkeleton(true);
            fetch("/api/gallery/list?" + params.toString())
                .then(function (r) {
                    if (!r.ok) throw new Error("HTTP " + r.status);  // 500/502 也走错误态
                    return r.json();
                })
                .then(function (d) {
                    showSkeleton(false);
                    G.total = d.total;
                    G.hasMore = d.has_more;
                    renderCards(d.items);
                    renderPagination(d);
                })
                .catch(function () {
                    // 失败要看得见：清空骨架 + 一行错误 + 重试按钮，不再只弹 2 秒 toast
                    showSkeleton(false);
                    setGalleryError(true);
                });
        }

        var galleryRetry = document.getElementById("galleryErrorRetry");
        if (galleryRetry) galleryRetry.addEventListener("click", function () { fetchGallery(); });

        // 空态有三种：站里真没作品 / 标签筛选没命中 / 页码越界（total>0 但当前页
        // 没有 items）。第三种不能念"暂无作品，去上传第一个"——那是自相矛盾，
        // 必须给跳回真实页面的动作（B2）。
        function renderEmptyState() {
            var empty = document.getElementById("galleryEmpty");
            if (!empty) return;
            if (G.tags.length > 0) {
                empty.innerHTML = '<p>没有匹配的作品，换个标签试试 / No works match this filter.</p>' +
                    '<p><button type="button" class="gallery-page-btn" id="galleryEmptyClear">清除筛选 / Clear filter</button></p>';
                var clr = document.getElementById("galleryEmptyClear");
                if (clr) clr.addEventListener("click", function () {
                    G.tags = [];
                    G.page = 1;
                    syncCustomTagSelection();
                    fetchGallery();
                });
            } else if (G.total === 0) {
                empty.innerHTML = '<p>暂无作品，<a href="/">去上传第一个 →</a> / No works yet. <a href="/">Upload the first one →</a></p>';
            } else {
                var lastPage = Math.ceil(G.total / G.limit) || 1;
                empty.innerHTML = '<p>第 ' + escapeHtml(G.page) + ' 页没有作品（共 ' + escapeHtml(lastPage) +
                    ' 页 / ' + escapeHtml(G.total) + ' 个作品）。This page is out of range.</p>' +
                    '<p><button type="button" class="gallery-page-btn" id="galleryEmptyFirst">回到第 1 页 / First page</button> ' +
                    '<button type="button" class="gallery-page-btn" id="galleryEmptyLast">最后一页 / Last page</button></p>';
                var firstBtn = document.getElementById("galleryEmptyFirst");
                if (firstBtn) firstBtn.addEventListener("click", function () {
                    G.page = 1;
                    fetchGallery();
                    window.scrollTo({ top: 0, behavior: "smooth" });
                });
                var lastBtn = document.getElementById("galleryEmptyLast");
                if (lastBtn) lastBtn.addEventListener("click", function () {
                    G.page = lastPage;
                    fetchGallery();
                    window.scrollTo({ top: 0, behavior: "smooth" });
                });
            }
            empty.style.display = "block";
        }

        function renderCards(items) {
            var grid = document.getElementById("galleryGrid");
            var empty = document.getElementById("galleryEmpty");
            if (!items.length) {
                grid.innerHTML = "";
                renderEmptyState();
                return;
            }
            empty.style.display = "none";
            grid.innerHTML = items.map(function (w) {
                // 类型标识：文字/图片艺术化作品与动画作品卡片观感统一，
                // 角标区分来源（kind=text 字符艺术；其余为动画工坊作品）
                var kind = (w.params && w.params.kind) || "";
                var badge = kind === "text"
                    ? '<span class="gallery-card-badge" aria-hidden="true">字符</span>' : "";
                // 卡片可访问名称只念一次「标题 · 作者」（B4）：缩略图是同一作品的
                // 装饰性重复，alt="" 让它退出名称计算；❤/👁 计数 aria-hidden，
                // 免得读屏把数字串进标题。
                return '<a class="gallery-card" href="/v/' + escapeHtml(w.id) + '" aria-label="' + escapeHtml(w.title) + ' · ' + escapeHtml(w.author || "匿名") + '">' +
                    '<div class="gallery-card-img">' +
                    '<img src="' + escapeHtml(w.thumbnail_url) + '" alt="" loading="lazy">' +
                    badge +
                    '</div>' +
                    '<div class="gallery-card-info">' +
                    '<div class="gallery-card-title">' + escapeHtml(w.title) + '</div>' +
                    '<div class="gallery-card-meta">' +
                    '<span class="gallery-card-author">' + escapeHtml(w.author || '匿名') + '</span>' +
                    '<span class="gallery-card-stats" aria-hidden="true">' +
                    '<span>❤ ' + escapeHtml(w.like_count) + '</span>' +
                    '<span>👁 ' + escapeHtml(w.view_count) + '</span>' +
                    '</span>' +
                    '</div></div></a>';
            }).join("");
        }

        function renderPagination(d) {
            var el = document.getElementById("galleryPagination");
            if (d.total <= d.limit) { el.innerHTML = ""; return; }
            var totalPages = Math.ceil(d.total / d.limit);
            // 页码越界：不再渲染会继续空翻的"上一页"，只标一句越界说明，
            // 跳页动作交给上面空态里的按钮（B2）。
            if (d.page > totalPages) {
                el.innerHTML = '<span class="gallery-page-info">第 ' + escapeHtml(d.page) + ' / ' + escapeHtml(totalPages) + ' 页（已越界 / out of range）</span>';
                return;
            }
            var html = "";
            if (d.page > 1) html += '<button class="gallery-page-btn" data-page="' + escapeHtml(d.page - 1) + '">← 上一页</button>';
            html += '<span class="gallery-page-info">第 ' + escapeHtml(d.page) + ' / ' + escapeHtml(totalPages) + ' 页</span>';
            if (d.has_more) html += '<button class="gallery-page-btn" data-page="' + escapeHtml(d.page + 1) + '">下一页 →</button>';
            el.innerHTML = html;
        }

        // Sort tabs：选中态同时落在 class 与 aria-pressed 上（B3），
        // 容器是 role="group"（不是 tablist：这里没有配套的 tabpanel）。
        function syncSortTabs() {
            document.querySelectorAll(".sort-tab").forEach(function (b) {
                var on = b.dataset.sort === G.sort;
                b.classList.toggle("active", on);
                b.setAttribute("aria-pressed", on ? "true" : "false");
            });
        }
        document.querySelectorAll(".sort-tab").forEach(function (btn) {
            btn.addEventListener("click", function () {
                G.sort = btn.dataset.sort;
                G.page = 1;
                syncSortTabs();
                fetchGallery();
            });
        });

        // Custom tags dropdown（T34）：多选，命中任意标签即返回；按热度排序
        var ctBtn = document.getElementById("customTagsBtn");
        var ctPanel = document.getElementById("customTagsPanel");
        var ctList = document.getElementById("customTagsList");
        var ctLoading = document.getElementById("customTagsLoading");
        var ctEmpty = document.getElementById("customTagsEmpty");
        var ctFoot = document.getElementById("customTagsFoot");
        var ctLoaded = false;

        function syncCustomTagSelection() {
            ctList.querySelectorAll(".custom-tag-item").forEach(function (item) {
                item.classList.toggle("active", G.tags.indexOf(item.dataset.tag) >= 0);
            });
            var btn = document.getElementById("customTagsBtn");
            if (btn) {
                var n = G.tags.length;
                btn.innerHTML = "自定义标签" + (n ? " (" + n + ") " : "") +
                    ' <span class="custom-tags-caret" aria-hidden="true">▾</span>';
                btn.classList.toggle("filtering", n > 0);
            }
        }
        function loadCustomTags() {
            if (!ctLoading || !ctList || !ctEmpty || !ctFoot) return;
            ctLoading.hidden = false;
            ctEmpty.hidden = true;
            ctFoot.hidden = true;
            ctList.innerHTML = "";
            fetch("/api/gallery/custom-tags").then(function (r) { return r.json(); })
                .then(function (d) {
                    ctLoading.hidden = true;
                    var tags = (d && d.tags) || [];
                    if (!tags.length) { ctEmpty.hidden = false; return; }
                    tags.forEach(function (t) {
                        var b = document.createElement("button");
                        b.type = "button";
                        b.className = "custom-tag-item";
                        b.dataset.tag = t.tag;
                        b.textContent = t.tag + " " + t.count;
                        b.addEventListener("click", function () {
                            var i = G.tags.indexOf(t.tag);
                            if (i >= 0) G.tags.splice(i, 1); else G.tags.push(t.tag);
                            G.page = 1;
                            syncCustomTagSelection();
                            fetchGallery();
                        });
                        ctList.appendChild(b);
                    });
                    ctFoot.hidden = false;
                    syncCustomTagSelection();
                })
                .catch(function () {
                    ctLoading.hidden = true;
                    ctEmpty.hidden = false;
                    ctEmpty.textContent = "加载失败，请重试";
                });
            ctLoaded = true;
        }
        if (ctBtn) ctBtn.addEventListener("click", function () {
            if (!ctPanel) return;
            var willOpen = ctPanel.hidden;
            ctPanel.hidden = !willOpen;
            ctBtn.setAttribute("aria-expanded", willOpen ? "true" : "false");
            if (willOpen && !ctLoaded) loadCustomTags();
        });
        var ctClear = document.getElementById("customTagsClear");
        if (ctClear) ctClear.addEventListener("click", function () {
            G.tags = [];
            G.page = 1;
            syncCustomTagSelection();
            fetchGallery();
        });
        document.addEventListener("click", function (e) {
            if (!ctPanel || ctPanel.hidden) return;
            if (ctPanel.contains(e.target) || (ctBtn && ctBtn.contains(e.target))) return;
            ctPanel.hidden = true;
            if (ctBtn) ctBtn.setAttribute("aria-expanded", "false");
        });

        // Pagination click delegation
        document.getElementById("galleryPagination").addEventListener("click", function (e) {
            var btn = e.target.closest(".gallery-page-btn");
            if (!btn) return;
            G.page = parseInt(btn.dataset.page, 10);
            fetchGallery();
            window.scrollTo({ top: 0, behavior: "smooth" });
        });

        // Tweaks panel
        var tweaksToggle = document.getElementById("tweaksToggle");
        var tweaksPanel = document.getElementById("tweaksPanel");
        var tweaksClose = document.getElementById("tweaksClose");
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
            document.querySelectorAll(onSel).forEach(function (btn) {
                btn.addEventListener("click", function () {
                    document.body.style.setProperty(prop, onVal);
                    document.querySelectorAll(onSel + "," + offSel).forEach(function (b) { b.classList.remove("active"); });
                    btn.classList.add("active");
                });
            });
            document.querySelectorAll(offSel).forEach(function (btn) {
                btn.addEventListener("click", function () {
                    document.body.style.setProperty(prop, offVal);
                    document.querySelectorAll(onSel + "," + offSel).forEach(function (b) { b.classList.remove("active"); });
                    btn.classList.add("active");
                });
            });
        }
        bindTweak('[data-tweak="grid-on"]', '[data-tweak="grid-off"]', "--grid-opacity", "0.3", "0");
        bindTweak('[data-tweak="scan-on"]', '[data-tweak="scan-off"]', "--scanline-opacity", "1", "0");

        // Theme color toggles
        document.querySelectorAll('[data-tweak^="theme-"]').forEach(function (btn) {
            btn.addEventListener("click", function () {
                document.querySelectorAll('[data-tweak^="theme-"]').forEach(function (b) { b.classList.remove("active"); });
                btn.classList.add("active");
                var theme = btn.getAttribute("data-tweak").replace("theme-", "");
                var colors = {
                    green: { main: "#00ff41", dim: "#00cc33", glow: "rgba(0,255,65,0.15)" },
                    amber: { main: "#ffb000", dim: "#cc8d00", glow: "rgba(255,176,0,0.12)" },
                    cyan:  { main: "#00d4ff", dim: "#00a8cc", glow: "rgba(0,212,255,0.12)" }
                };
                var c = colors[theme]; if (!c) return;
                document.documentElement.style.setProperty("--green", c.main);
                document.documentElement.style.setProperty("--green-dim", c.dim);
                document.documentElement.style.setProperty("--green-glow", c.glow);
            });
        });

        // 首次进入：先按地址栏恢复状态，再拉列表（B1）
        readStateFromUrl();
        syncSortTabs();
        syncCustomTagSelection();
        fetchGallery();