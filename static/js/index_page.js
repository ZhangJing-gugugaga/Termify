// 首页：风格卡点选、上传拖拽态、Tweaks 网格/扫描线开关。
// 原内联 <script>（templates/index.html:500-568）外置，语义保持一致：
// 仅做 DOM 绑定，不依赖 app.js / termify-render.js 的任何导出。
(function () {
    // ═══════════ Style card selection ═══════════
    var styleCards = document.querySelectorAll('.style-card');
    for (var i = 0; i < styleCards.length; i++) {
        (function (card) {
            card.addEventListener('click', function () {
                var all = document.querySelectorAll('.style-card');
                for (var j = 0; j < all.length; j++) all[j].classList.remove('selected');
                card.classList.add('selected');
            });
        })(styleCards[i]);
    }

    // ═══════════ Format option selection ═══════════
    var terminalSizeCard = document.getElementById('terminalSizeCard');
    var downloadBtn = document.querySelector('.download-btn');
    // 输出格式已移入「导出动画」弹窗（app.js 两步确认）；尺寸仅滑杆，无快捷按钮
    // ═══════════ Upload zone drag effects ═══════════
    var uploadZone = document.getElementById('uploadZone');
    uploadZone.addEventListener('dragover', function (e) {
        e.preventDefault();
        uploadZone.classList.add('drag-over');
    });
    uploadZone.addEventListener('dragleave', function () {
        uploadZone.classList.remove('drag-over');
    });
    uploadZone.addEventListener('drop', function (e) {
        e.preventDefault();
        uploadZone.classList.remove('drag-over');
    });

    // ═══════════ Tweaks panel ═══════════
    // 开关绑定只在 static/js/app.js 里保留一份（ES5 那份，带 close 处理）；
    // 这里原先还重复绑了一份等价监听，一次点击 toggle 两次互相抵消 → 面板永远打不开。

    function bindTweakToggle(selector, allSelector, prop, value) {
        var btns = document.querySelectorAll(selector);
        for (var k = 0; k < btns.length; k++) {
            (function (btn) {
                btn.addEventListener('click', function () {
                    document.body.style.setProperty(prop, value);
                    var siblings = document.querySelectorAll(allSelector);
                    for (var m = 0; m < siblings.length; m++) siblings[m].classList.remove('active');
                    btn.classList.add('active');
                });
            })(btns[k]);
        }
    }

    // Tweaks: grid toggle
    bindTweakToggle('[data-tweak="grid-on"]', '[data-tweak^="grid"]', '--grid-opacity', '0.3');
    bindTweakToggle('[data-tweak="grid-off"]', '[data-tweak^="grid"]', '--grid-opacity', '0');
    // Tweaks: scanline toggle
    bindTweakToggle('[data-tweak="scan-on"]', '[data-tweak^="scan"]', '--scanline-opacity', '1');
    bindTweakToggle('[data-tweak="scan-off"]', '[data-tweak^="scan"]', '--scanline-opacity', '0');

    // 主题色选择已迁移至输出面板「配色 / Color scheme」（app.js applyPalette）

    // 进度条 / 帧计数由 app.js 的真实播放时钟驱动（updateProgressClock：
    // cycleSec / S.frames.length × S.interval）。原来这里那段 2.4 秒
    // DEMO_TOTAL 假循环已删除——没有帧时如实显示 0.0s / 0.0s。
})();
