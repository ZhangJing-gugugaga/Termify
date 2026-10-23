        var toastTimer = null;
        function toast(msg) {
            var el = document.getElementById("toast"); if (!el) return;
            el.textContent = msg; el.classList.add("show");
            if (toastTimer !== null) clearTimeout(toastTimer);
            toastTimer = setTimeout(function () { el.classList.remove("show"); }, 2200);
        }

        var ADMIN_PWD = "";

        // 存储型 XSS 防线：作品 title/author、举报 reason/description 均为用户
        // 可控原文（服务端只截断不转义），插进 innerHTML 前必须转义。
        // 含引号转义（属性上下文也不可逃逸）。
        function escapeHtml(s) {
            return String(s == null ? "" : s)
                .replace(/&/g, "&amp;")
                .replace(/</g, "&lt;")
                .replace(/>/g, "&gt;")
                .replace(/"/g, "&quot;")
                .replace(/'/g, "&#39;");
        }

        // Login
        document.getElementById("adminLoginBtn").addEventListener("click", doLogin);
        document.getElementById("adminPwd").addEventListener("keydown", function (e) {
            if (e.key === "Enter") doLogin();
        });

        // 先校验口令再切面板（B5）：错口令收掉登录框会把用户困在空面板里
        // 无路可退，只剩刷新（且已填密码也不该被当成有效会话）。
        function doLogin() {
            var pwd = document.getElementById("adminPwd").value;
            if (!pwd) { toast("请输入密码"); return; }
            ADMIN_PWD = pwd;
            setLoginError("");
            fetch("/api/gallery/admin", { headers: { "X-Termify-Admin-Pwd": ADMIN_PWD } })
                .then(function (r) {
                    if (r.status === 401 || r.status === 403) throw new Error("unauthorized");
                    if (!r.ok) throw new Error("HTTP " + r.status);
                    return r.json();
                })
                .then(function (d) {
                    if (d && d.error) { loginFailed(d.error); return; }
                    showAdmin(d);
                })
                .catch(function (err) {
                    loginFailed(err && err.message === "unauthorized"
                        ? "口令错误，请重试 / Wrong password."
                        : "加载失败，请重试 / Failed to load, retry.");
                });
        }

        // 失败态：留在登录框、保留已输入内容与焦点、常驻错误提示。
        function loginFailed(msg) {
            ADMIN_PWD = "";
            setLoginError(msg);
            document.getElementById("adminPanel").style.display = "none";
            document.getElementById("adminLogin").style.display = "block";
            var input = document.getElementById("adminPwd");
            if (input) { input.focus(); input.select(); }
        }

        function setLoginError(msg) {
            var el = document.getElementById("adminLoginError");
            if (!el) return;
            el.textContent = msg || "";
            el.style.display = msg ? "block" : "none";
        }

        function showAdmin(data) {
            setLoginError("");
            document.getElementById("adminLogin").style.display = "none";
            document.getElementById("adminPanel").style.display = "block";
            if (data) { renderWorks(data.works); renderReports(data.reports); }
            else loadData();
        }

        // 面板里的退路：清掉口令回到登录态（B5）。
        function signOut() {
            ADMIN_PWD = "";
            document.getElementById("adminPwd").value = "";
            document.getElementById("adminPanel").style.display = "none";
            document.getElementById("adminLogin").style.display = "block";
            setLoginError("");
            document.getElementById("adminPwd").focus();
        }
        document.getElementById("adminSignOut").addEventListener("click", signOut);

        function loadData() {
            fetch("/api/gallery/admin", { headers: { "X-Termify-Admin-Pwd": ADMIN_PWD } })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    if (d.error) { toast(d.error); return; }
                    renderWorks(d.works);
                    renderReports(d.reports);
                })
                .catch(function () { toast("加载失败"); });
        }

        function renderWorks(works) {
            var tbody = document.getElementById("adminWorksBody");
            var empty = document.getElementById("adminWorksEmpty");
            if (!works.length) { tbody.innerHTML = ""; empty.style.display = "block"; return; }
            empty.style.display = "none";
            tbody.innerHTML = works.map(function (w) {
                return '<tr>' +
                    '<td><a href="/v/' + escapeHtml(w.id) + '" target="_blank">' + escapeHtml(w.id) + '</a></td>' +
                    '<td>' + escapeHtml(w.title) + '</td>' +
                    '<td>' + escapeHtml(w.author || '匿名') + '</td>' +
                    '<td>' + escapeHtml(w.like_count) + '</td>' +
                    '<td>' + escapeHtml(w.view_count) + '</td>' +
                    '<td><button class="admin-del-btn" data-id="' + escapeHtml(w.id) + '">删除</button></td>' +
                    '</tr>';
            }).join("");
        }

        function renderReports(reports) {
            var tbody = document.getElementById("adminReportsBody");
            var empty = document.getElementById("adminReportsEmpty");
            if (!reports.length) { tbody.innerHTML = ""; empty.style.display = "block"; return; }
            empty.style.display = "none";
            tbody.innerHTML = reports.map(function (r) {
                return '<tr>' +
                    '<td>' + escapeHtml(r.id) + '</td>' +
                    '<td><a href="/v/' + escapeHtml(r.work_id) + '" target="_blank">' + escapeHtml(r.work_id) + '</a></td>' +
                    '<td>' + escapeHtml(r.reason) + '</td>' +
                    '<td class="admin-report-desc">' + escapeHtml(r.description || '') + '</td>' +
                    '<td>' +
                    '<button class="admin-resolve-btn" data-id="' + escapeHtml(r.id) + '">处理</button> ' +
                    '<button class="admin-dismiss-btn" data-id="' + escapeHtml(r.id) + '">忽略</button>' +
                    '</td>' +
                    '</tr>';
            }).join("");
        }

        // Tab switching
        document.querySelectorAll(".admin-tab").forEach(function (tab) {
            tab.addEventListener("click", function () {
                document.querySelectorAll(".admin-tab").forEach(function (t) { t.classList.remove("active"); });
                tab.classList.add("active");
                var which = tab.dataset.tab;
                document.getElementById("tab-works").classList.toggle("active", which === "works");
                document.getElementById("tab-reports").classList.toggle("active", which === "reports");
            });
        });

        // Delete work (delegation)
        document.getElementById("adminWorksBody").addEventListener("click", function (e) {
            var btn = e.target.closest(".admin-del-btn");
            if (!btn) return;
            if (!confirm("确定删除该作品？")) return;
            var id = btn.dataset.id;
            fetch("/api/gallery/admin/" + id, { method: "DELETE", headers: { "X-Termify-Admin-Pwd": ADMIN_PWD } })
                .then(function (r) { return r.json(); })
                .then(function (d) {
                    if (d.error) { toast(d.error); return; }
                    toast("已删除");
                    loadData();
                }).catch(function () { toast("删除失败"); });
        });

        // Resolve / dismiss report (delegation)
        document.getElementById("adminReportsBody").addEventListener("click", function (e) {
            var btn = e.target.closest(".admin-resolve-btn, .admin-dismiss-btn");
            if (!btn) return;
            var id = btn.dataset.id;
            var status = btn.classList.contains("admin-resolve-btn") ? "resolved" : "dismissed";
            fetch("/api/gallery/admin/report/" + id, {
                method: "POST",
                headers: { "Content-Type": "application/json", "X-Termify-Admin-Pwd": ADMIN_PWD },
                body: JSON.stringify({ status: status })
            }).then(function (r) { return r.json(); }).then(function (d) {
                if (d.error) { toast(d.error); return; }
                toast("已处理");
                loadData();
            }).catch(function () { toast("操作失败"); });
        });