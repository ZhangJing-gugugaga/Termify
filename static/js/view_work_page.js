        // ═══════════ View page: simplified viewer ═══════════
        (function () {
            // 服务端作品数据经 <script type="application/json" id="termifyWorkData">
            // 过河（不可执行类型不受 script-src 管辖，tojson 已转义 < 与引号，
            // 存储型 XSS 防线不变）。params/tags 仍是 JSON 字符串，parse 语义与原
            // 内联块完全一致；window.TERMIFY_GALLERY_WORK 保留以兼容外部读取。
            var dataEl = document.getElementById("termifyWorkData");
            var work = JSON.parse(dataEl.textContent);
            work.params = JSON.parse(work.params || "{}");
            work.tags = JSON.parse(work.tags || "[]");
            window.TERMIFY_GALLERY_WORK = work;
            var WORK_ID = work.id;
            var params = work.params || {};
            // 文字作品（kind=text）：艺术字存 params.frames（单帧），无动画/
            // 风格/尺寸切换，预览直接回放 + 单色配色（params.fg）。
            var IS_TEXT = params.kind === "text";
            // 作品库（kind=piece）：服务端按字符网格重渲染，网格本身就是
            // 原始作品的 cols×rows，换字符集时行数要跟着原比例走。
            var IS_PIECE = params.kind === "piece";
            var PIECE_AR = (params.width && params.height)
                ? (params.height / params.width) : 0;
            var curCharset = params.charset || "blocks";
            var curWidth = params.width || 80;
            var curHeight = params.height || 24;
            // 自定义字符梯（每请求参数，不入库）：默认 ascii 梯，空格→空字符
            var viewRamp = params.ramp || "@%#*+=-:. ";
            var rampRow = document.getElementById("viewRampRow");
            var rampInput = document.getElementById("viewRampInput");
            function syncRampRow() {
                if (rampRow) rampRow.hidden = curCharset !== "custom";
            }
            var rampApply = document.getElementById("viewRampApply");
            if (rampInput && rampApply) {
                rampInput.value = viewRamp;
                rampApply.addEventListener("click", function () {
                    var v = rampInput.value || "";
                    if (!v.trim()) { toast("字符梯不能为空 / Ramp required"); return; }
                    viewRamp = v;
                    toast("字符梯已应用");
                    loadPreview();
                });
            }

            // 文字作品页的风格/尺寸/配色/导出/嵌入块由模板条件渲染直接不输出
            // （见 templates/view_work.html 的 is_text_work），此处只补文字作品元信息。
            if (IS_TEXT) {
                var _meta = document.getElementById("textWorkMeta");
                if (_meta) {
                    _meta.textContent = (params.font ? params.font + " · " : "") +
                        curWidth + "×" + curHeight + " · 静态字符作品";
                }
            }
            function textArtString() {
                return (params.frames && params.frames[0]) ? String(params.frames[0]) : "";
            }
            function textFgColor() {
                return Array.isArray(params.fg) && params.fg.length === 3
                    ? "rgb(" + (params.fg[0] | 0) + "," + (params.fg[1] | 0) + "," + (params.fg[2] | 0) + ")"
                    : null;
            }

            // ── 配色回放：作品保存的配色在预览与导出中原样重现 ──
            // color=source/source256 → 本地渲染逐字符原色（256 仅影响 py 导出，
            // 预览统一 24-bit）；否则回放保存的 fg/bg。
            var Q256_LEVELS = [0, 95, 135, 175, 215, 255];
            function xterm256Rgb(idx) {
                idx = Math.max(0, Math.min(255, idx | 0));
                if (idx >= 232) { var g = 8 + (idx - 232) * 10; return [g, g, g]; }
                var n = idx - 16;
                return [Q256_LEVELS[Math.floor(n / 36)],
                        Q256_LEVELS[Math.floor(n / 6) % 6],
                        Q256_LEVELS[n % 6]];
            }
            function isRgbArr(v) {
                return Array.isArray(v) && v.length === 3 &&
                    v.every(function (x) { return (x | 0) >= 0 && (x | 0) <= 255; });
            }
            function viewerRenderOpts() {
                var o = { fg: null, bg: null };
                if (curCharset === "custom") o.ramp = viewRamp;
                var savedSource = params.color === "source" || params.color === "source256";
                if (curTheme === "saved") {
                    if (savedSource) {
                        o.colorMode = "source";
                    } else {
                        if (isRgbArr(params.fg)) o.fg = params.fg;
                        if (isRgbArr(params.bg)) o.bg = params.bg;
                    }
                    return o;
                }
                var t = THEME_COLORS[curTheme];
                if (t) { o.fg = t.fg; o.bg = t.bg; }
                return o;
            }
            function viewerQueryColors() {
                var q = "";
                // 主题色（非「原作」）强制单色：丢掉作品保存的 color/fg/bg，
                // 改发 color=mono + 主题 fg/bg，服务端才不会被原色模式回退覆盖
                var themed = (curTheme !== "saved" && THEME_COLORS[curTheme]) ? THEME_COLORS[curTheme] : null;
                if (themed) {
                    q += "&color=mono";
                } else if (params.color) {
                    q += "&color=" + encodeURIComponent(String(params.color));
                }
                var qfg = themed ? themed.fg : params.fg;
                var qbg = themed ? themed.bg : params.bg;
                if (isRgbArr(qfg)) q += "&fg=rgb(" + qfg.map(function (x) { return x | 0; }).join(",") + ")";
                if (isRgbArr(qbg)) q += "&bg=rgb(" + qbg.map(function (x) { return x | 0; }).join(",") + ")";
                if (curCharset === "custom") q += "&chars=" + encodeURIComponent(viewRamp);
                return q;
            }

            var frames = [], interval = 0.1, totalFrames = 0;
            var currentFrame = 0, playing = false, rafId = null, lastFrameTime = 0;
            var preview = document.getElementById("animPreview");
            var progressFill = document.getElementById("progressFill");
            var frameCounter = document.getElementById("frameCounter");
            var playBtn = document.getElementById("playBtn");
            var pauseBtn = document.getElementById("pauseBtn");
            var previewTitle = document.getElementById("previewTitle");
            var latestReq = 0;
            // 本地渲染缓存（缩放 RGBA + 亮度）：换风格时跨风格复用，
            // 换尺寸（滑杆）时整体作废重建。结构与 termify-render 的
            // cachePut 契约一致（map/used，budget 首写时自动补默认值）。
            var renderCache = { map: {}, used: 0 };

            // ── helpers ──
            function setRenderChip(text) {
                // 切风格时保留旧画面，用右上角悬浮条提示渲染进度
                //（旧版把预览清成 spinner——"渲染中/播放中交替闪烁"的根源）
                var chip = document.getElementById("renderChip");
                if (!chip) return;
                if (!text) { chip.hidden = true; return; }
                chip.hidden = false;
                chip.textContent = text;
            }
            function toast(msg) {
                var el = document.getElementById("toast"); if (!el) return;
                el.textContent = msg; el.classList.add("show");
                clearTimeout(window.__vw_tt);
                window.__vw_tt = setTimeout(function () { el.classList.remove("show"); }, 2200);
            }
            // 存储型 XSS 防线：用户可控字符串（tags 等）插进 innerHTML 前转义。
            // 含引号转义，属性/文本上下文都不可逃逸；横幅区 title/description/author
            // 走 Jinja 自动转义，ANSI 帧走 canvas 绘制（paintAnsiFrame），此处兜底全页面。
            function escapeHtml(t) {
                return String(t == null ? "" : t)
                    .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
                    .replace(/"/g, "&quot;").replace(/'/g, "&#39;");
            }
            // ── 彩色帧 canvas 播放：ANSI 帧不再拼上万 span 的 innerHTML
            //    （每帧重排是 blocks 原色播放时滑动/点击卡顿的根源），
            //    改为在 <canvas> 上按格画矩形/字形，单帧绘制仅几毫秒。 ──
            var frameCanvas = null;
            var CELL_W = 8, CELL_H = 16;  // 画布格子分辨率，CSS 拉伸到终端窗
            function ensureFrameCanvas() {
                if (!frameCanvas) {
                    frameCanvas = document.createElement("canvas");
                    frameCanvas.className = "frame-canvas";
                }
                if (frameCanvas.width !== curWidth * CELL_W ||
                    frameCanvas.height !== curHeight * CELL_H) {
                    frameCanvas.width = curWidth * CELL_W;
                    frameCanvas.height = curHeight * CELL_H;
                }
                if (frameCanvas.parentNode !== preview) {
                    preview.textContent = "";
                    preview.appendChild(frameCanvas);
                }
                return frameCanvas;
            }
            // 盲文点阵：与 termify/output/video.py:_draw_braille_cell 同样的
            // (col,row,mask) 表，按 2x4 子格画矩形。
            var BRAILLE_DOTS = [
                [0, 0, 0x01], [0, 1, 0x02], [0, 2, 0x04],
                [1, 0, 0x08], [1, 1, 0x10], [1, 2, 0x20],
                [0, 3, 0x40], [1, 3, 0x80]
            ];
            function isBrailleChar(ch) {
                return ch >= "\u2800" && ch <= "\u28ff";
            }
            function drawBrailleCell(ctx, ch, x, y, w, h, color) {
                var bits = ch.charCodeAt(0) - 0x2800;
                if (!bits) return;
                var dotW = w / 2, dotH = h / 4;
                ctx.fillStyle = color;
                for (var i = 0; i < BRAILLE_DOTS.length; i++) {
                    if (!(bits & BRAILLE_DOTS[i][2])) continue;
                    ctx.fillRect(x + BRAILLE_DOTS[i][0] * dotW,
                                 y + BRAILLE_DOTS[i][1] * dotH, dotW, dotH);
                }
            }
            function paintAnsiFrame(lines) {
                var cv = ensureFrameCanvas();
                var ctx = cv.getContext("2d");
                ctx.clearRect(0, 0, cv.width, cv.height);
                ctx.font = Math.floor(CELL_H * 0.82) + 'px "JetBrains Mono", Consolas, monospace';
                ctx.textBaseline = "top";
                var defFg = textFgColor() || "#c9d1d9";
                for (var y = 0; y < lines.length && y < curHeight; y++) {
                    var fg = null, bg = null, x = 0;
                    var toks = lines[y].split(/(\x1b\[[0-9;]*m)/);
                    for (var t = 0; t < toks.length; t++) {
                        var tok = toks[t];
                        if (!tok) continue;
                        if (/^\x1b\[[0-9;]*m$/.test(tok)) {
                            var inner = tok.slice(2, -1);
                            if (inner === "0") { fg = null; bg = null; }
                            else if (inner === "39") { fg = null; }
                            else if (inner === "49") { bg = null; }
                            else if (inner.indexOf("38;2;") === 0) { var p = inner.split(";"); fg = "rgb(" + p[2] + "," + p[3] + "," + p[4] + ")"; }
                            else if (inner.indexOf("48;2;") === 0) { var pb = inner.split(";"); bg = "rgb(" + pb[2] + "," + pb[3] + "," + pb[4] + ")"; }
                            else if (inner.indexOf("38;5;") === 0) { var pf = inner.split(";"); var cf = xterm256Rgb(parseInt(pf[2], 10)); fg = "rgb(" + cf[0] + "," + cf[1] + "," + cf[2] + ")"; }
                            else if (inner.indexOf("48;5;") === 0) { var pk = inner.split(";"); var ck = xterm256Rgb(parseInt(pk[2], 10)); bg = "rgb(" + ck[0] + "," + ck[1] + "," + ck[2] + ")"; }
                            continue;
                        }
                        for (var k = 0; k < tok.length && x < curWidth; k++) {
                            var chr = tok[k];
                            if (bg) {
                                ctx.fillStyle = bg;
                                ctx.fillRect(x * CELL_W, y * CELL_H, CELL_W, CELL_H);
                            }
                            if (chr !== " ") {
                                if (chr === "▀") {
                                    if (fg) { ctx.fillStyle = fg; ctx.fillRect(x * CELL_W, y * CELL_H, CELL_W, CELL_H / 2); }
                                } else if (chr === "▄") {
                                    if (fg) { ctx.fillStyle = fg; ctx.fillRect(x * CELL_W, y * CELL_H + CELL_H / 2, CELL_W, CELL_H / 2); }
                                } else if (chr === "█") {
                                    if (fg) { ctx.fillStyle = fg; ctx.fillRect(x * CELL_W, y * CELL_H, CELL_W, CELL_H); }
                                } else if (isBrailleChar(chr)) {
                                    // 盲文无内置 Web 字体（JetBrains Mono / DejaVu 都缺
                                    // U+2800-28FF）：交给字体就是整屏豆腐块，改矢量画点。
                                    drawBrailleCell(ctx, chr, x * CELL_W, y * CELL_H,
                                        CELL_W, CELL_H, fg || defFg);
                                } else {
                                    ctx.fillStyle = fg || defFg;
                                    ctx.fillText(chr, x * CELL_W, y * CELL_H + 2);
                                }
                            }
                            x++;
                        }
                        if (x >= curWidth) break;
                    }
                }
            }
            function renderFrame(idx) {
                if (!frames.length) return;
                var lines = frames[idx];
                var joined = lines.join("\n");
                if (joined.indexOf("\x1b") !== -1) {
                    paintAnsiFrame(lines);
                } else if (frameCanvas && frameCanvas.parentNode === preview) {
                    // 从彩色帧切回纯文本帧：清掉 canvas，恢复文本流
                    frameCanvas.remove();
                    preview.textContent = joined;
                } else {
                    preview.textContent = joined;
                }
                currentFrame = idx;
                if (!playing) updateProgressClock((idx + 1) * interval);
            }
            // ── 秒制匀速进度（与主页一致的 rAF 时钟） ──
            function fmtSecs(v) { return v.toFixed(1) + "s"; }
            function updateProgressClock(cycleSec) {
                var totalSec = frames.length * interval;
                if (progressFill) progressFill.style.width = (totalSec > 0 ? (cycleSec / totalSec) * 100 : 0) + "%";
                if (frameCounter) frameCounter.textContent = (frames.length && interval > 0) ? (fmtSecs(cycleSec) + " / " + fmtSecs(totalSec)) : "--";
            }
            var playStartTs = 0;
            function rafLoop(ts) {
                if (!playing) return;
                if (frames.length < 2 || interval <= 0) { rafId = requestAnimationFrame(rafLoop); return; }
                var totalSec = frames.length * interval;
                var cycle = ((ts - playStartTs) / 1000) % totalSec;
                if (cycle < 0) cycle += totalSec;
                updateProgressClock(cycle);
                var idx = Math.floor(cycle / interval) % frames.length;
                if (idx !== currentFrame) renderFrame(idx);
                rafId = requestAnimationFrame(rafLoop);
            }
            function startPlayer() {
                if (playing || frames.length < 2) return;
                playing = true;
                if (IS_PIECE) setRenderChip(null);  // 作品库循环播放中不再留渲染提示
                if (playBtn) playBtn.classList.add("active");
                if (pauseBtn) pauseBtn.classList.remove("active");
                playStartTs = performance.now() - currentFrame * interval * 1000;
                // T25 音频同步：从当前帧对应的时间点继续播
                if (AUDIO_URL) {
                    ensureAudio();
                    if (workAudio) {
                        try {
                            workAudio.currentTime = (currentFrame * interval) %
                                Math.max(0.1, workAudio.duration || 1e9);
                        } catch (e) { /* seek 前元数据未就绪则从头播 */ }
                        workAudio.play().catch(function () { /* 自动播放策略拒绝则静音播放 */ });
                    }
                }
                rafId = requestAnimationFrame(rafLoop);
            }
            function pausePlayer() {
                playing = false;
                if (playBtn) playBtn.classList.remove("active");
                if (pauseBtn) pauseBtn.classList.add("active");
                if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }
                if (workAudio && !workAudio.paused) workAudio.pause();
            }

            // ── T25 音频：视频作品自带音轨 → 预览同步播放 ──
            var AUDIO_URL = (params.kind === "video" && params.audio_file)
                ? "/gallery/file/" + WORK_ID + "/audio" : null;
            var workAudio = null;
            function ensureAudio() {
                if (!AUDIO_URL || workAudio !== null) return;
                workAudio = new Audio(AUDIO_URL);
                workAudio.loop = true;
                workAudio.preload = "auto";
            }
            (function initAudioBadge() {
                if (!AUDIO_URL) return;
                var badge = document.getElementById("audioBadge");
                if (badge) badge.style.display = "inline-flex";
            })();

            // ── load preview ──
            // 方案B：视频作品取回源帧（服务端逐帧下发的 JPEG），此后全部切换本地渲染
            var IS_VIDEO = params.kind === "video";
            var srcFrames = null, srcLoading = false, srcLoadFailed = false;
            /* PF-1 分帧下发：整包 /source-frames/<id> 是 514 帧 base64 拼的单个
               12.1MB JSON，首帧要等最后一帧到齐（实测 13.8s 白屏）。改成三段：
               清单（几百字节）→ 第 0 帧（≈20KB）先出画面 → 其余帧受限并发补齐，
               到齐后再进入播放。单帧 URL 带 immutable 长缓存，二访/回退零成本。 */
            var SRC_FRAME_CONCURRENCY = 3;
            var SRC_FRAMES_URL = "/api/gallery/source-frames/" + WORK_ID;
            /* 单帧 URL 必须带清单里的 version：帧被重新生成后 URL 随之变化，
               "immutable 一年" 才真的成立（否则老客户端会拿旧帧一整年）。 */
            var srcFrameVersion = "";

            // 单帧 URL → canvas；尺寸用清单给的源像素尺寸（与整包路径同源）。
            function loadSourceFrame(index, w, h) {
                return new Promise(function (resolve) {
                    var img = new Image();
                    img.onload = function () {
                        var c = document.createElement("canvas");
                        c.width = w; c.height = h;
                        c.getContext("2d").drawImage(img, 0, 0);
                        resolve(c);
                    };
                    img.onerror = function () { resolve(null); };
                    img.src = SRC_FRAMES_URL + "/frame/" + index +
                        (srcFrameVersion ? "?v=" + encodeURIComponent(srcFrameVersion) : "");
                });
            }

            /* 受限并发补齐第 1..count-1 帧：并发 3 把其余连接留给音频/样式，
               不让 514 个请求同时压满连接池；按索引回填，顺序不乱。 */
            function fillSourceFrames(meta, first) {
                var total = meta.count;
                var byIndex = new Array(total);
                byIndex[0] = first;
                if (total <= 1) return Promise.resolve(byIndex);
                var next = 1, active = 0, done = 0;
                return new Promise(function (resolve) {
                    function startOne(i) {
                        active++;
                        loadSourceFrame(i, meta.w, meta.h).then(function (bmp) {
                            active--;
                            done++;
                            byIndex[i] = bmp;
                            setRenderChip("补齐源帧 " +
                                Math.min(99, Math.round(done / (total - 1) * 100)) + "%");
                            launch();
                        });
                    }
                    function launch() {
                        while (active < SRC_FRAME_CONCURRENCY && next < total) {
                            startOne(next);
                            next++;
                        }
                        if (next >= total && active === 0) resolve(byIndex);
                    }
                    launch();
                });
            }

            function ensureSourceFrames() {
                if (srcFrames || srcLoading || !IS_VIDEO) return;
                srcLoading = true;
                fetch(SRC_FRAMES_URL + "/manifest").then(function (r) { return r.json(); })
                    .then(function (m) {
                        if (!m || !m.ok || !m.count) throw new Error("manifest unavailable");
                        srcFrameVersion = m.version || "";
                        return loadSourceFrame(0, m.w, m.h).then(function (bmp) {
                            if (!bmp) throw new Error("first frame failed");
                            firstFrameReady(m, bmp);
                        });
                    })
                    .catch(function () {
                        // 清单/首帧都取不到才回退服务端整包预览
                        srcLoading = false;
                        srcFrames = null;
                        srcLoadFailed = true;
                        loadPreview();
                    });
            }

            function firstFrameReady(meta, first) {
                srcLoading = false;
                srcFrames = { bitmaps: [first], interval: meta.interval };
                loadPreview();  // 首帧先出画，不再等整包
                fillSourceFrames(meta, first).then(function (byIndex) {
                    var bitmaps = [];
                    for (var i = 0; i < byIndex.length; i++) if (byIndex[i]) bitmaps.push(byIndex[i]);
                    if (!bitmaps.length) { srcFrames = null; srcLoadFailed = true; loadPreview(); return; }
                    // 帧集齐 → 完整本地渲染，渲染完开播
                    srcFrames = { bitmaps: bitmaps, interval: meta.interval };
                    loadPreview();
                }, function () {
                    setRenderChip(null);  // 补齐中断：保留已到手帧照常播放
                });
            }
            function fitViewFontSize() {
                // 窗口贴合内容（与主页同算法）：高度按网格像素比宽定高，
                // 字号双向填满 → 内容 100% 充满窗口；换风格不换网格零跳动
                var tb = preview;
                var style = getComputedStyle(tb);
                var padX = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
                var padY = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
                var availW = tb.clientWidth - padX;
                if (availW <= 0 || !curWidth || !curHeight) return;
                var gridAspect = (curWidth * 0.6) / (curHeight * 1.3);
                var maxH = Math.max(300, Math.round(window.innerHeight * 0.62));
                var idealH = Math.round(availW / gridAspect) + Math.round(padY);
                var boxH = Math.max(240, Math.min(maxH, idealH));
                tb.style.height = boxH + "px";
                var availH = boxH - padY;
                var fs = Math.min(availW / (curWidth * 0.6),
                                  availH / (curHeight * 1.3));
                tb.style.fontSize = Math.max(2, Math.min(fs, 30)) + "px";
            }
            function fitTextFontSize() {
                // 文字作品专用：先按宽度铺满定字号，窗口高度随内容撑开
                //（上限 75vh，超出再按高度收缩字号）——静态作品没有动画
                // 窗口约束，小字号挤在窗口左上角的排版不适配。
                var tb = preview;
                var style = getComputedStyle(tb);
                var padX = parseFloat(style.paddingLeft) + parseFloat(style.paddingRight);
                var padY = parseFloat(style.paddingTop) + parseFloat(style.paddingBottom);
                var availW = tb.clientWidth - padX;
                if (availW <= 0 || !curWidth || !curHeight) return;
                var fsByW = availW / (curWidth * 0.6);
                var maxH = Math.max(300, Math.round(window.innerHeight * 0.75));
                var fs = Math.min(fsByW, 30);
                var needH = Math.round(curHeight * 1.3 * fs + padY);
                if (needH > maxH) {
                    fs = Math.min(fs, (maxH - padY) / (curHeight * 1.3));
                    needH = maxH;
                }
                tb.style.height = Math.max(240, needH) + "px";
                tb.style.fontSize = Math.max(2, fs) + "px";
            }
            function textMetaLabel() {
                // 文字作品 meta：图片艺术化作品标注来源与配色，替代无意义的 "custom"
                var parts = [];
                if (params.color === "source") parts.push("图片艺术化 · 原色");
                else if (params.palette) parts.push("图片艺术化 · " +
                    ({green:"终端绿",cyan:"青",amber:"琥珀",
                      magenta:"品红",red:"红",white:"白"}[params.palette] || params.palette));
                else if (params.font) parts.push(params.font);
                parts.push(curWidth + "×" + curHeight + " · 静态文字作品");
                return parts.join(" · ");
            }
            function loadTextPreview() {
                var art = textArtString();
                preview.style.opacity = "1";
                if (!art) { preview.textContent = "作品内容缺失 / Missing art content"; return; }
                frames = [art.split("\n")];
                interval = 0;
                totalFrames = 1;
                preview.style.color = textFgColor() || "";
                if (previewTitle) previewTitle.textContent = "char art · " +
                    (params.font || (params.color === "source" ? "source" : "custom")) +
                    " " + curWidth + "×" + curHeight;
                var _meta2 = document.getElementById("textWorkMeta");
                if (_meta2) _meta2.textContent = textMetaLabel();
                fitTextFontSize();
                renderFrame(0);
            }
            function loadPreview() {
                if (IS_TEXT) { loadTextPreview(); return; }
                var myId = ++latestReq;
                var hasContent = frames.length > 0;
                if (hasContent) {
                    // 已有画面：暂停播放但保留旧帧，仅显示渲染提示（不闪黑）
                    pausePlayer();
                    setRenderChip("渲染中…");
                } else {
                    preview.style.transition = "opacity 0.15s ease";
                    preview.style.opacity = "0.5";
                    preview.innerHTML = '<div class="view-loading"><div class="view-spinner"></div><span>加载中...</span></div>';
                }
                // 视频作品：源帧未就绪时直接等本地渲染路径——不再先拉服务端
                // 全帧 JSON 再整体重渲（首载卡顿 + 双重渲染闪烁的根源）；
                // 源帧拉取失败时由 ensureSourceFrames 置 srcLoadFailed 回落服务端。
                if (IS_VIDEO && !srcFrames && !srcLoadFailed) return;
                // 本地渲染路径：源帧已在内存
                if (srcFrames && srcFrames.bitmaps.length) {
                    TermifyRender.renderFrames(srcFrames.bitmaps, curCharset, curWidth, curHeight,
                        (function (o) {
                            o.cache = renderCache;
                            // 用户再切一次风格/尺寸时放弃本轮渲染（新一轮会重置）
                            o.shouldCancel = function () { return myId !== latestReq; };
                            return o;
                        })(viewerRenderOpts()),
                        function (done, total) {
                            if (myId === latestReq) {
                                setRenderChip("渲染中 " + Math.round(done / total * 100) + "%");
                            }
                        }).then(function (lf) {
                            if (!lf || myId !== latestReq) return;
                            setRenderChip(null);
                            preview.style.opacity = "1";
                            frames = lf;
                            interval = srcFrames.interval;
                                        totalFrames = lf.length;
                            if (previewTitle) previewTitle.textContent = "animation preview · " + curCharset + " " + curWidth + "x" + curHeight;
                            fitViewFontSize();
                            renderFrame(0);
                            if (frames.length >= 2) startPlayer();
                        });
                    return;
                }
                var url = "/api/gallery/preview/" + WORK_ID + "?charset=" + curCharset + "&width=" + curWidth + "&height=" + curHeight + viewerQueryColors();
                fetch(url).then(function (r) { return r.json(); }).then(function (d) {
                    if (myId !== latestReq) return;
                    setRenderChip(null);
                    preview.style.opacity = "1";
                    if (d.error) { showPreviewFallback(d.error); return; }
                    frames = d.frames || [];
                    interval = d.interval || 0.1;
                        totalFrames = frames.length;
                    if (previewTitle) {
                        // 抽稀提示：帧数超体积预算时服务端均匀降采样，标题写明
                        // 「原 N 帧 → 预览 M 帧」，否则用户会以为作品被改短了
                        previewTitle.textContent = "animation preview · " + curCharset + " " + curWidth + "x" + curHeight +
                            (d.subsampled ? "（预览抽稀 " + d.total_frames + "→" + frames.length + " 帧）" : "");
                    }
                    fitViewFontSize();
                    renderFrame(0);
                    // 作品库一直在播放：换字符集/尺寸后接着放，不停在首帧
                    if (frames.length >= 2) startPlayer();
                    else setRenderChip(null);
                }).catch(function () {
                    if (myId !== latestReq) return;
                    setRenderChip(null);
                    preview.style.opacity = "1";
                    showPreviewFallback("加载失败 / Failed to load");
                });
            }

            /* 预览兜底：服务端预览取不到时退回作品缩略图 + 可执行的下一步。
               旧实现是一句死胡同提示（"请刷新页面使用新版播放器"）——刷新后
               同样失败，用户只能看着转圈或一行红字。 */
            function showPreviewFallback(msg) {
                frames = [];
                totalFrames = 0;
                if (rafId !== null) { cancelAnimationFrame(rafId); rafId = null; }
                playing = false;
                preview.innerHTML = '<div class="view-fallback">' +
                    '<img src="/gallery/file/' + WORK_ID + '/thumb" alt="作品缩略图" loading="lazy">' +
                    '<p>' + String(msg).replace(/[<>&]/g, function (c) {
                        return { "<": "&lt;", ">": "&gt;", "&": "&amp;" }[c];
                    }) + '</p>' +
                    '<p class="view-fallback-hint">拖动右侧「终端尺寸」调小列数即可重试，' +
                    '或直接导出 MP4 看完整效果 / Drag the size slider down, or export MP4</p>' +
                    '</div>';
                if (previewTitle) previewTitle.textContent = "animation preview · " + curCharset + " " + curWidth + "x" + curHeight;
            }

            // ── style cards ──
            if (!IS_TEXT) document.querySelectorAll("#styles .style-card").forEach(function (card) {
                card.addEventListener("click", function () {
                    document.querySelectorAll("#styles .style-card").forEach(function (c) { c.classList.remove("selected"); });
                    card.classList.add("selected");
                    curCharset = card.getAttribute("data-style");
                    syncRampRow();
                    loadPreview();
                });
            });
            syncRampRow();
            // ── size slider：列数 20-400，行高按素材比例自动（与主页一致）──
            // 比例来源：作品保存的 width/height 网格（上传时已按源比例生成）。
            // rows = cols × (2 × savedH / savedW) / 2 = cols × savedH / savedW。
            (function initSizeSlider() {
                var slider = document.getElementById("viewSizeSlider");
                if (!slider || IS_TEXT) return;
                var savedAr = (params.width && params.height)
                    ? (params.height / params.width) : 0.5;
                function rowsForCols(cols) {
                    var rows = TermifyRender.pyRound(cols * savedAr);
                    // 作品库按原作字符网格等比缩放：不套 8-240 的行数钳制，
                    // 否则 96x3 这类超宽横幅会被强行补到 8 行、比例失真。
                    if (IS_PIECE) return Math.max(1, Math.min(400, rows));
                    return Math.max(8, Math.min(240, rows));
                }
                function syncMeta(cols) {
                    var colsVal = document.getElementById("viewColsVal");
                    if (colsVal) colsVal.textContent = String(cols);
                    var meta = document.getElementById("viewSizeMeta");
                    if (meta) meta.textContent = cols + " × " + curHeight +
                        " · 行数按素材比例自动 / rows auto-fit to source";
                    var warn = document.getElementById("viewSizeWarn");
                    if (warn) warn.hidden = cols <= 320;
                }
                var pending = null;
                slider.addEventListener("input", function () {
                    var cols = parseInt(slider.value, 10) || 80;
                    curWidth = cols;
                    curHeight = rowsForCols(cols);
                    syncMeta(cols);
                    pending = cols;
                });
                slider.addEventListener("change", function () {
                    if (pending !== null) { renderCache = { map: {}, used: 0 }; loadPreview(); pending = null; }
                });
                // 初始值 = 作品保存的列数
                slider.value = String(curWidth);
                syncMeta(curWidth);
            })();
            // ── 配色面板（与主页一致）+ 导出弹窗 ──
            var THEME_COLORS = {
                white: { fg: [255, 255, 255], bg: [10, 14, 20] },
                green: { fg: [51, 255, 51],   bg: [10, 14, 20] },
                amber: { fg: [255, 176, 0],   bg: [20, 13, 2] },
                cyan:  { fg: [0, 212, 255],   bg: [4, 18, 31] }
            };
            var curTheme = "saved";
            var pendingFmt = "python";
            function markPaletteBtn(key) {
                document.querySelectorAll("#viewPaletteRow .palette-btn").forEach(function (b) {
                    b.classList.toggle("active", b.getAttribute("data-vtheme") === key);
                });
            }
            document.querySelectorAll("#viewPaletteRow .palette-btn").forEach(function (btn) {
                btn.addEventListener("click", function () {
                    curTheme = btn.getAttribute("data-vtheme") || "saved";
                    markPaletteBtn(curTheme);
                    if (!IS_TEXT) loadPreview();  // 配色实时重渲预览（亮度缓存命中，温切换很快）
                });
            });
            function markExportFormat(fmt) {
                document.querySelectorAll("#exportBackdrop .export-option").forEach(function (o) {
                    o.classList.toggle("selected", o.getAttribute("data-format") === fmt);
                });
            }
            function exportColorsQuery() {
                // 主题色 → 覆盖 fg/bg（mono）；「原作」→ 沿用作品保存配色（含原色模式）
                // 自定义字符梯是独立于配色的参数，两条分支都要带上
                var rampQ = (curCharset === "custom")
                    ? "&chars=" + encodeURIComponent(viewRamp) : "";
                if (curTheme !== "saved" && THEME_COLORS[curTheme]) {
                    var t = THEME_COLORS[curTheme];
                    return "&color=mono&fg=rgb(" + t.fg.join(",") + ")&bg=rgb(" + t.bg.join(",") + ")" + rampQ;
                }
                return viewerQueryColors();
            }
            function openExportModal(fmt) {
                pendingFmt = fmt || "python";
                var backdrop = document.getElementById("exportBackdrop");
                if (!backdrop) { doDownloadNow(pendingFmt); return; }
                markExportFormat(pendingFmt);
                backdrop.hidden = false;
            }
            document.querySelectorAll("#exportBackdrop .export-option").forEach(function (btn) {
                btn.addEventListener("click", function () {
                    pendingFmt = btn.getAttribute("data-format");
                    markExportFormat(pendingFmt);
                });
            });
            var exportCancelBtn = document.getElementById("exportCancelBtn");
            if (exportCancelBtn) exportCancelBtn.addEventListener("click", function () {
                document.getElementById("exportBackdrop").hidden = true;
            });
            var exportBackdropEl = document.getElementById("exportBackdrop");
            if (exportBackdropEl) exportBackdropEl.addEventListener("click", function (e) {
                if (e.target === exportBackdropEl) exportBackdropEl.hidden = true;
            });
            // 导出在途状态：连点 / 回车只认第一次提交（复用 app.css 的 .export-confirm.busy spinner）
            var exportBusy = false;
            var exportConfirmIdleText = "";
            function setExportBusy(busy) {
                exportBusy = !!busy;
                var btn = document.getElementById("exportConfirmBtn");
                if (!btn) return;
                if (!exportConfirmIdleText) exportConfirmIdleText = btn.textContent;
                btn.disabled = exportBusy;
                btn.setAttribute("aria-busy", exportBusy ? "true" : "false");
                btn.classList.toggle("busy", exportBusy);
                btn.textContent = exportBusy ? "导出中… / Exporting…" : exportConfirmIdleText;
            }
            var exportConfirmBtn = document.getElementById("exportConfirmBtn");
            if (exportConfirmBtn) exportConfirmBtn.addEventListener("click", function () {
                if (exportBusy) return;  // 在途：重复点击 / Enter 一律忽略
                exportBackdropEl.hidden = true;
                setExportBusy(true);
                doDownloadNow(pendingFmt);
            });

            function doDownloadNow(fmt) {
                var url = "/api/gallery/download/" + WORK_ID + "?charset=" + curCharset + "&width=" + curWidth + "&height=" + curHeight + "&format=" + fmt + exportColorsQuery();
                if (fmt === "embed") {
                    // 一行嵌入：as=json 取片段全文，浮层里复制 / 下载 .html（download_url 即不带 as=json 的那条）
                    fetch(url + "&as=json").then(function (r) {
                        if (!r.ok) { return r.json().then(function (d) { throw new Error((d && d.error) || "HTTP " + r.status); }); }
                        return r.json();
                    }).then(function (d) {
                        setExportBusy(false);
                        showEmbedSnippet(d.snippet, d.download_url);
                    }).catch(function (e) {
                        setExportBusy(false);
                        toast("生成失败 / Failed: " + e.message);
                    });
                    return;
                }
                var btn = fmt === "mp4" ? document.getElementById("exportAnimBtn") : null;
                if (fmt === "mp4" && btn) {
                    // 视频编码耗时较长：按钮进入等待态；无耗时预估（决策 v3.1 §2）
                    var oldText = btn.innerHTML;
                    btn.disabled = true;
                    btn.innerHTML = "⏳ 生成中…请耐心等待";
                    fetch(url).then(function (r) {
                        if (!r.ok) { return r.json().then(function (d) { throw new Error(d.error || "HTTP " + r.status); }); }
                        return r.blob();
                    }).then(function (blob) {
                        btn.disabled = false; btn.innerHTML = oldText;
                        setExportBusy(false);
                        var a = document.createElement("a");
                        a.href = URL.createObjectURL(blob);
                        a.download = "gallery_" + WORK_ID + "_" + curCharset + ".mp4";
                        document.body.appendChild(a);
                        a.click();
                        a.remove();
                    }).catch(function (e) {
                        // blob 下载在 UC/X5 等移动浏览器不可靠 → 回退原生下载
                        //（含生成接口本身报错——原生下载会展示服务端错误信息）
                        btn.disabled = false; btn.innerHTML = oldText;
                        setExportBusy(false);
                        toast("已切换浏览器直接下载");
                        window.location.href = url;
                    });
                    return;
                }
                // python/html（以及没拿到 exportAnimBtn 的 mp4）：先 fetch 再落盘。
                // 原来直接跳转，服务端 4xx/5xx 会把用户带离页面、无法重试；
                // fetch 后失败能透出 error 原文并复位按钮。
                fetch(url).then(function (r) {
                    if (!r.ok) {
                        return r.json().then(function (d) {
                            throw new Error((d && d.error) || ("HTTP " + r.status));
                        }, function () { throw new Error("HTTP " + r.status); });
                    }
                    var cd = r.headers.get("Content-Disposition") || "";
                    var mm = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(cd);
                    var name = (mm && mm[1]) || ("gallery_" + WORK_ID + "." + fmt);
                    return r.blob().then(function (blob) { return { blob: blob, name: name }; });
                }).then(function (d) {
                    setExportBusy(false);
                    toast("导出完成，正在下载 / Export ready, downloading");
                    var a = document.createElement("a");
                    a.href = URL.createObjectURL(d.blob);
                    a.download = d.name;
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                }).catch(function (e) {
                    setExportBusy(false);
                    toast(e.message || String(e));
                });
            }

            // ── 一行嵌入片段浮层：复制 / 下载 .html（与主页同一套结构） ──
            function showEmbedSnippet(snippet, downloadUrl) {
                var box = document.getElementById("embedSnippetText");
                var backdrop = document.getElementById("embedSnippetBackdrop");
                if (!box || !backdrop) {
                    if (downloadUrl) window.location.href = downloadUrl;
                    return;
                }
                box.value = snippet || "";
                var dl = document.getElementById("embedSnippetDownload");
                if (dl) dl.setAttribute("data-download-url", downloadUrl || "");
                backdrop.hidden = false;
            }
            function closeEmbedSnippet() {
                var backdrop = document.getElementById("embedSnippetBackdrop");
                if (backdrop) backdrop.hidden = true;
            }
            function copyEmbedSnippet() {
                var box = document.getElementById("embedSnippetText");
                if (!box) return;
                function legacy() {
                    box.focus();
                    box.select();
                    var ok = false;
                    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
                    toast(ok ? "已复制 / Copied" : "复制失败 / Copy failed");
                }
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(box.value).then(function () { toast("已复制 / Copied"); }, legacy);
                } else {
                    legacy();
                }
            }
            var _embedBackdrop = document.getElementById("embedSnippetBackdrop");
            if (_embedBackdrop) _embedBackdrop.addEventListener("click", function (e) {
                if (e.target === _embedBackdrop) closeEmbedSnippet();
            });
            var _embedClose = document.getElementById("embedSnippetClose");
            if (_embedClose) _embedClose.addEventListener("click", closeEmbedSnippet);
            var _embedCopy = document.getElementById("embedSnippetCopy");
            if (_embedCopy) _embedCopy.addEventListener("click", copyEmbedSnippet);
            var _embedDl = document.getElementById("embedSnippetDownload");
            if (_embedDl) _embedDl.addEventListener("click", function () {
                var url = _embedDl.getAttribute("data-download-url");
                if (url) window.location.href = url;
            });
            var _exportBtn = document.getElementById("exportAnimBtn");
            if (_exportBtn) _exportBtn.addEventListener("click", function () { openExportModal(null); });

            // ── fork：带着当前风格/尺寸/配色回到编辑器，用户换自己的素材再渲染 ──
            /* G-2：文字作品原来是裸跳转 "/text-art"，落地即空白编辑器。库里存的是
               整幅字符画（params.frames[0]），这里把作品 id / 字体 / 正文一起过 URL，
               由 text_art.js 预填。正文单件可达十几 KB，超过 6000 字符就不带，
               接收侧回落 /api/gallery/work/<id> 取同一份数据。 */
            var FORK_TEXT_MAX = 6000;
            var _forkBtn = document.getElementById("forkBtn");
            if (_forkBtn) _forkBtn.addEventListener("click", function () {
                if (IS_TEXT || params.font) {
                    var tq = "?fork=" + encodeURIComponent(WORK_ID);
                    if (params.font) {
                        tq += "&font=" + encodeURIComponent(String(params.font));
                    }
                    var _body = textArtString();
                    if (_body && _body.length <= FORK_TEXT_MAX) {
                        tq += "&text=" + encodeURIComponent(_body);
                    }
                    window.location.href = "/text-art" + tq;
                    return;
                }
                var q = "?fork=" + encodeURIComponent(WORK_ID) +
                    "&charset=" + encodeURIComponent(curCharset) +
                    "&width=" + curWidth + "&height=" + curHeight;
                if (curTheme !== "saved" && THEME_COLORS[curTheme]) {
                    q += "&color=mono";
                } else if (params.color === "source" || params.color === "source256") {
                    q += "&color=source";
                }
                window.location.href = "/" + q;
            });

            // ── play / pause ──
            if (playBtn) playBtn.addEventListener("click", startPlayer);
            if (pauseBtn) pauseBtn.addEventListener("click", pausePlayer);

            // ── banner ──
            document.getElementById("viewBannerClose").addEventListener("click", function () {
                document.getElementById("viewBanner").style.display = "none";
            });
            // render tags
            var tagsEl = document.getElementById("viewBannerTags");
            if (work.tags && work.tags.length && tagsEl) {
                tagsEl.innerHTML = work.tags.map(function (t) { return '<span class="view-banner-tag">' + escapeHtml(t) + '</span>'; }).join("");
            }
            // like — check existing state on load, then wire click
            var likeBtnEl = document.getElementById("likeBtn");
            fetch("/api/gallery/work/" + WORK_ID).then(function (r) { return r.json(); }).then(function (wd) {
                if (wd && wd.is_liked) { likeBtnEl.classList.add("liked"); }
            }).catch(function () {});
            likeBtnEl.addEventListener("click", function () {
                fetch("/api/gallery/like/" + WORK_ID, { method: "POST" }).then(function (r) { return r.json(); }).then(function (d) {
                    if (d.error) { toast(d.error); return; }
                    document.getElementById("bannerLikes").textContent = d.count;
                    likeBtnEl.classList.toggle("liked", d.liked);
                    if (d.liked) toast("已点赞");
                }).catch(function () { toast("操作失败"); });
            });
            // 复制：clipboard API 在权限策略/非聚焦文档下会以 NotAllowedError 被拒
            // （人工测试里「复制链接」直接报「复制失败」），必须回退 execCommand。
            function copyText(text, okMsg, failMsg) {
                function ok() { toast(okMsg); }
                function fail() { toast(failMsg); }
                function legacy() {
                    var ta = document.createElement("textarea");
                    ta.value = text;
                    ta.setAttribute("readonly", "");
                    ta.style.position = "fixed";
                    ta.style.top = "-1000px";
                    ta.style.opacity = "0";
                    document.body.appendChild(ta);
                    var copied = false;
                    try {
                        ta.select();
                        ta.setSelectionRange(0, ta.value.length);
                        copied = document.execCommand("copy");
                    } catch (e) { copied = false; }
                    document.body.removeChild(ta);
                    if (copied) { ok(); } else { fail(); }
                }
                if (navigator.clipboard && navigator.clipboard.writeText) {
                    navigator.clipboard.writeText(text).then(ok, legacy);
                } else {
                    legacy();
                }
            }
            // copy link
            document.getElementById("copyLinkBtn").addEventListener("click", function () {
                copyText(window.location.origin + "/v/" + WORK_ID, "链接已复制", "复制失败 / Copy failed");
            });
            // report modal
            var reportModal = document.getElementById("reportModal");
            document.getElementById("reportBtn").addEventListener("click", function () { reportModal.classList.add("open"); });
            document.getElementById("reportModalClose").addEventListener("click", function () { reportModal.classList.remove("open"); });
            reportModal.addEventListener("click", function (e) { if (e.target === reportModal) reportModal.classList.remove("open"); });
            document.getElementById("reportSubmitBtn").addEventListener("click", function () {
                var reason = document.querySelector('input[name="reportReason"]:checked').value;
                var desc = document.getElementById("reportDesc").value;
                fetch("/api/gallery/report/" + WORK_ID, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ reason: reason, description: desc }) })
                    .then(function (r) { return r.json(); }).then(function (d) {
                        if (d.error) { toast(d.error); return; }
                        toast("举报已提交，感谢反馈"); reportModal.classList.remove("open");
                    }).catch(function () { toast("提交失败"); });
            });

            // ── text works: copy / download .txt ──
            var _copyV = document.getElementById("textCopyBtnV");
            if (_copyV) _copyV.addEventListener("click", function () {
                var art = textArtString();
                if (!art) return;
                copyText(art, "已复制 / Copied", "复制失败 / Copy failed");
            });
            var _dlV = document.getElementById("textDownloadBtnV");
            if (_dlV) _dlV.addEventListener("click", function () {
                var art = textArtString();
                if (!art) return;
                var blob = new Blob([art], { type: "text/plain;charset=utf-8" });
                var a = document.createElement("a");
                a.href = URL.createObjectURL(blob);
                a.download = "termify_" + WORK_ID + "_text_art.txt";
                document.body.appendChild(a); a.click(); a.remove();
                URL.revokeObjectURL(a.href);
            });

            // ── initial selection highlight ──
            document.querySelectorAll("#styles .style-card").forEach(function (c) {
                if (c.getAttribute("data-style") === curCharset) { c.classList.add("selected"); }
            });
            if (!IS_TEXT) {
                // Default: match exact size, otherwise find closest by area
                var sizePresets = ["40x20", "80x24", "120x36", "160x48", "200x60"];
                var exactMatch = curWidth + "x" + curHeight;
                var matchedSize = sizePresets.indexOf(exactMatch) >= 0 ? exactMatch : null;
                if (!matchedSize) {
                    var targetArea = curWidth * curHeight;
                    var bestDiff = Infinity;
                    for (var si = 0; si < sizePresets.length; si++) {
                        var parts = sizePresets[si].split("x");
                        var area = parseInt(parts[0]) * parseInt(parts[1]);
                        var diff = Math.abs(area - targetArea);
                        if (diff < bestDiff) { bestDiff = diff; matchedSize = sizePresets[si]; }
                    }
                }
                // Snap curWidth/curHeight to the matched preset so initial preview matches the highlighted button
                var matchedParts = matchedSize.split("x");
                curWidth = parseInt(matchedParts[0]);
                curHeight = parseInt(matchedParts[1]);
            }

            // ── kick off first preview ──
            // 顺序即优先级：视频作品先发清单+首帧请求，loadPreview 在上面的
            // IS_VIDEO 分支直接返回，不会同时再打一笔 6.2MB 的整包预览。
            ensureSourceFrames();
            loadPreview();
            window.addEventListener("resize", function () {
                if (IS_TEXT) { fitTextFontSize(); return; }
                fitViewFontSize();
            });
        })();