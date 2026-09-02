/* TermifyRender — 浏览器端字符画渲染器（与 termify/charset.py 同公式）。
   供主页（方案B 本地渲染：本地视频 + 服务端任务源帧）与画廊作品页共用。

   公开 API：
   - TermifyRender.scaleFrame(source, sw, sh) -> canvas
       等比缩放 + 黑底居中 letterbox（镜像 Python scale_frame，绝不拉伸）
   - TermifyRender.renderFrames(sources, charset, width, height, opts, onProgress)
       -> Promise<string[][]>  ANSI 帧数组；分块异步让出主线程
       sources: 可绘制对象数组（canvas / ImageBitmap / img）
       opts: { ramp(自定义字符梯), fg, bg, colorMode("source"=逐字符取源像素原色,
         传入时 fg/bg 被忽略；缺省 mono=整帧单色), cache(跨调用亮度缓存),
         cacheBudget(字节上限) }
       onProgress(done, total)
   - TermifyRender.renderRaw(data, w, h, charset, opts) -> string[]
       对「已缩放」的原始 RGBA 像素渲染单帧 ANSI（renderFrames 每帧的核心路径），
       单独暴露以便与服务端 termify.charset.render_frame 做逐字符一致性比对。
   - TermifyRender.sanitizeRamp(ramp) -> string[]
       镜像 Python sanitize_ramp（去 ANSI 转义/控制字符、按码点去重、上限 64），
       返回码点数组；空梯返回 []（调用方据此报错，服务端同场景为 400/500）。
   - TermifyRender.fitRect(srcW, srcH, dstW, dstH) -> {w,h,x,y}
       Python scale_frame 的 fit 矩形（等比 + round 取偶 + 黑底居中偏移）。
   - TermifyRender.pyRound(x)
       CPython round() 兼容的银行家舍入（.5 取偶），灰度/LUT/缩放共用。
*/
(function () {
  "use strict";

  var ESC = "\x1b";
  var RAMP_CHARS = { ascii: "@#%*+=-:. ", shades: "█▓▒░ " };
  // 预展开成码点数组（按码点索引，行为与 Python 字符串索引一致）
  var RAMP_ARRAYS = {
    ascii: Array.from(RAMP_CHARS.ascii),
    shades: Array.from(RAMP_CHARS.shades),
    geometric: Array.from("■●◆▪▫◇○ "),
  };
  var BRAILLE_DOTS = [
    [0, 0, 0x01], [0, 1, 0x02], [0, 2, 0x04],
    [1, 0, 0x08], [1, 1, 0x10], [1, 2, 0x20],
    [0, 3, 0x40], [1, 3, 0x80],
  ];
  var CUSTOM_RAMP_MAX_LEN = 64;  // 同 termify/charset.py
  var DEFAULT_CACHE_BUDGET = 48 * 1024 * 1024;  // 跨风格切换亮度缓存字节上限

  /* ── Python round() 兼容（银行家舍入：.5 取偶） ──
     Math.round 恒向 +∞ 取半，Python round 向偶取半；灰度/自适应 LUT/缩放
     尺寸全部改走 pyRound，保证与 CPython 对同一 double 的舍入逐位一致。 */
  function pyRound(x) {
    var f = Math.floor(x);
    var diff = x - f;
    if (diff > 0.5) return f + 1;
    if (diff < 0.5) return f;
    return (f % 2 === 0) ? f : f + 1;
  }

  /* ── 基础：亮度 / 直方图 / Otsu / 自适应 LUT（同 Python 公式） ── */
  function luminance(data) {
    var n = data.length / 4;
    var lums = new Uint8Array(n);
    for (var i = 0, p = 0; i < n; i++, p += 4) {
      lums[i] = pyRound(0.299 * data[p] + 0.587 * data[p + 1] + 0.114 * data[p + 2]);
    }
    return lums;
  }

  function histogram(lums) {
    var hist = new Array(256);
    for (var v = 0; v < 256; v++) hist[v] = 0;
    for (var i = 0; i < lums.length; i++) hist[lums[i]]++;
    return hist;
  }

  function otsu(lums) {
    var hist = histogram(lums);
    var total = lums.length;
    var sumAll = 0;
    for (var v = 0; v < 256; v++) sumAll += v * hist[v];
    var sumBg = 0, wBg = 0, maxVar = 0, threshold = 127;
    for (var t = 0; t < 256; t++) {
      wBg += hist[t];
      if (wBg === 0) continue;
      var wFg = total - wBg;
      if (wFg === 0) break;
      sumBg += t * hist[t];
      var mBg = sumBg / wBg;
      var mFg = (sumAll - sumBg) / wFg;
      // 乘法结合顺序对齐 Python：w_bg * w_fg * (m_bg - m_fg) ** 2
      var dm = mBg - mFg;
      var variance = wBg * wFg * (dm * dm);
      if (variance > maxVar) { maxVar = variance; threshold = t; }
    }
    var nBelow = 0;
    for (var q = 0; q <= threshold; q++) nBelow += hist[q];
    var nAbove = total - nBelow;
    if (nBelow === 0 || nAbove === 0) return [threshold, false];
    return [threshold, nAbove < nBelow];
  }

  function adaptiveLut(lums) {
    var hist = histogram(lums);
    var total = lums.length;
    var cdf = 0, cdfMin = null;
    var lut = new Array(256);
    for (var i = 0; i < 256; i++) {
      cdf += hist[i];
      if (cdfMin === null && hist[i] > 0) cdfMin = cdf;
      if (cdfMin === null) lut[i] = 0;
      else if (total === cdfMin) lut[i] = i;
      else lut[i] = pyRound((cdf - cdfMin) / (total - cdfMin) * 255);
    }
    return lut;
  }

  /* ── 自定义字符梯清理（镜像 Python sanitize_ramp，按 Unicode 码点处理） ── */
  var ANSI_CSI_RE = /\x1b\[[0-9;]*[A-Za-z]/g;
  function sanitizeRamp(ramp) {
    if (typeof ramp !== "string") return [];
    ramp = ramp.replace(ANSI_CSI_RE, "");
    var seen = {};
    var out = [];
    var cps = Array.from(ramp);
    for (var i = 0; i < cps.length; i++) {
      var ch = cps[i];
      var code = ch.codePointAt(0);
      if (code < 0x20 || code === 0x7F || (code >= 0x200B && code <= 0x200F)) continue;
      if (seen[ch]) continue;
      seen[ch] = 1;
      out.push(ch);
      if (out.length >= CUSTOM_RAMP_MAX_LEN) break;
    }
    return out;
  }

  /* ── ANSI 组装 ── */
  function ansiFg(rgb) { return ESC + "[38;2;" + rgb[0] + ";" + rgb[1] + ";" + rgb[2] + "m"; }
  function ansiBg(rgb) { return ESC + "[48;2;" + rgb[0] + ";" + rgb[1] + ";" + rgb[2] + "m"; }

  /* ── 原色（source color）模式 ──
     镜像 Python charset.py：每字符取源像素色为前景，同色 run-length 合并，
     空格不上色，行尾 reset；暗部提升地板 56 防纯黑被黑底终端吞没。 */
  var SOURCE_LUM_FLOOR = 56;

  function boostVisible(rgb) {
    var lum = pyRound(0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]);
    if (lum >= SOURCE_LUM_FLOOR) return rgb;
    if (lum === 0) return [SOURCE_LUM_FLOOR, SOURCE_LUM_FLOOR, SOURCE_LUM_FLOOR];
    var k = SOURCE_LUM_FLOOR / lum;
    return [Math.min(255, pyRound(rgb[0] * k)),
            Math.min(255, pyRound(rgb[1] * k)),
            Math.min(255, pyRound(rgb[2] * k))];
  }

  function assembleSource(data, lums, w, h, cell) {
    var lines = [];
    for (var y = 0; y < h; y++) {
      var row = "";
      var last = null;
      var base = y * w;
      for (var x = 0; x < w; x++) {
        var ch = cell[lums[base + x]];
        if (ch === " ") { row += " "; last = null; continue; }
        var p = (base + x) * 4;
        var c = boostVisible([data[p], data[p + 1], data[p + 2]]);
        if (last === null || c[0] !== last[0] || c[1] !== last[1] || c[2] !== last[2]) {
          row += ansiFg(c);
          last = c;
        }
        row += ch;
      }
      if (last !== null) row += ESC + "[0m";
      lines.push(row);
    }
    return lines;
  }

  /* ── 各字符集渲染（输入：行主序亮度数组 + 源宽高） ──
     data = 已缩放 RGBA 像素：传入时走原色分支（逐字符源色），fg/bg 被忽略 */
  function renderRamp(lums, w, h, chars, fg, bg, data) {
    var n = chars.length;
    var lut = adaptiveLut(lums);
    var mib = otsu(lums)[1];
    var cell = new Array(256);
    for (var g = 0; g < 256; g++) {
      var gray = lut[g];
      var idx = mib ? (n - 1) - Math.floor(gray * (n - 1) / 255)
                    : Math.floor(gray * (n - 1) / 255);
      cell[g] = chars[idx];
    }
    if (data) return assembleSource(data, lums, w, h, cell);
    return assemble(lums, w, h, cell, fg, bg);
  }

  function renderBinary(lums, w, h, fg, bg, data) {
    var ots = otsu(lums);
    var threshold = ots[0], mib = ots[1];
    var cell = new Array(256);
    for (var g = 0; g < 256; g++) {
      cell[g] = mib ? (g >= threshold ? "█" : " ") : (g < threshold ? "█" : " ");
    }
    if (data) return assembleSource(data, lums, w, h, cell);
    return assemble(lums, w, h, cell, fg, bg);
  }

  function renderGeometric(lums, w, h, fg, bg, data) {
    var chars = RAMP_ARRAYS.geometric;
    var n = chars.length;
    var mib = otsu(lums)[1];
    var cell = new Array(256);
    for (var g = 0; g < 256; g++) {
      var idx = mib ? (n - 1) - Math.floor(g * (n - 1) / 255)
                    : Math.floor(g * (n - 1) / 255);
      cell[g] = chars[idx];
    }
    if (data) return assembleSource(data, lums, w, h, cell);
    return assemble(lums, w, h, cell, fg, bg);
  }

  function assemble(lums, w, h, cell, fg, bg) {
    var lines = [];
    for (var y = 0; y < h; y++) {
      var row = "";
      var base = y * w;
      for (var x = 0; x < w; x++) {
        var ch = cell[lums[base + x]];
        if (fg) row += ansiFg(fg);
        if (bg) row += ansiBg(bg);
        row += ch;
      }
      if (fg || bg) row += ESC + "[0m";
      lines.push(row);
    }
    return lines;
  }

  var brailleCoordCache = {};
  function brailleCoords(srcW, srcH) {
    // 输出网格由源尺寸决定（镜像 Python：out = src/2 × src/4）
    var outW = Math.max(1, Math.floor(srcW / 2));
    var outH = Math.max(1, Math.floor(srcH / 4));
    var key = srcW + "x" + srcH;
    var table = brailleCoordCache[key];
    if (table) return { table: table, outW: outW, outH: outH };
    table = [];
    for (var by = 0; by < outH; by++) {
      for (var bx = 0; bx < outW; bx++) {
        for (var d = 0; d < 8; d++) {
          var dx = BRAILLE_DOTS[d][0], dy = BRAILLE_DOTS[d][1], mask = BRAILLE_DOTS[d][2];
          var sx = Math.min(Math.floor((bx * 2 + dx) * srcW / (outW * 2)), srcW - 1);
          var sy = Math.min(Math.floor((by * 4 + dy) * srcH / (outH * 4)), srcH - 1);
          table.push([sy * srcW + sx, mask]);
        }
      }
    }
    if (Object.keys(brailleCoordCache).length > 8) brailleCoordCache = {};
    brailleCoordCache[key] = table;
    return { table: table, outW: outW, outH: outH };
  }

  function renderBraille(lums, srcW, srcH, fg, bg, data) {
    var bc = brailleCoords(srcW, srcH);
    var table = bc.table;
    var ots = otsu(lums);
    var threshold = ots[0], mib = ots[1];
    var lines = [];
    var pos = 0;
    for (var by = 0; by < bc.outH; by++) {
      var row = "";
      var emitted = false;
      for (var bx = 0; bx < bc.outW; bx++) {
        var bits = 0;
        var sr = 0, sg = 0, sb = 0, nlit = 0;
        for (var d = 0; d < 8; d++) {
          var pix = table[pos + d][0];
          var lum = lums[pix];
          var mask = table[pos + d][1];
          if (mib ? lum >= threshold : lum < threshold) {
            bits |= mask;
            if (data) {
              var p = pix * 4;
              sr += data[p]; sg += data[p + 1]; sb += data[p + 2];
              nlit++;
            }
          }
        }
        pos += 8;
        var ch = String.fromCharCode(0x2800 + bits);
        if (data && bits) {
          var c = boostVisible([Math.floor(sr / nlit), Math.floor(sg / nlit), Math.floor(sb / nlit)]);
          row += ansiFg(c) + ch;
          emitted = true;
        } else {
          if (fg) row += ansiFg(fg);
          if (bg) row += ansiBg(bg);
          row += ch;
        }
      }
      if ((fg || bg) || emitted) row += ESC + "[0m";
      lines.push(row);
    }
    return lines;
  }

  function renderBlocks(data, srcW, srcH) {
    var lines = [];
    for (var yTop = 0; yTop < srcH; yTop += 2) {
      var yBot = yTop + 1 < srcH ? yTop + 1 : yTop;
      var rowTop = yTop * srcW * 4, rowBot = yBot * srcW * 4;
      var parts = [];
      var lastFg = null, lastBg = null;
      for (var x = 0; x < srcW; x++) {
        var p1 = rowTop + x * 4, p2 = rowBot + x * 4;
        var fg = [data[p1], data[p1 + 1], data[p1 + 2]];
        var bg = [data[p2], data[p2 + 1], data[p2 + 2]];
        if (lastFg === null || fg[0] !== lastFg[0] || fg[1] !== lastFg[1] || fg[2] !== lastFg[2]) {
          parts.push(ansiFg(fg));
          lastFg = fg;
        }
        if (lastBg === null || bg[0] !== lastBg[0] || bg[1] !== lastBg[1] || bg[2] !== lastBg[2]) {
          parts.push(ansiBg(bg));
          lastBg = bg;
        }
        parts.push("▀");
      }
      lines.push(parts.join(""));
    }
    return lines;
  }

  /* ── 等比缩放 + 黑底居中 letterbox（镜像 Python scale_frame，绝不拉伸） ── */
  function fitRect(srcW, srcH, dstW, dstH) {
    // Python scale_frame 的 fit 矩形：等比缩放尺寸（round 半取偶）+ 黑底居中偏移。
    // 独立导出，便于与服务端逐项一致性比对。
    var scale = Math.min(dstW / srcW, dstH / srcH);
    var fw = Math.max(1, pyRound(srcW * scale));
    var fh = Math.max(1, pyRound(srcH * scale));
    return { w: fw, h: fh, x: Math.floor((dstW - fw) / 2), y: Math.floor((dstH - fh) / 2) };
  }

  function scaleFrame(source, sw, sh) {
    var c = document.createElement("canvas");
    c.width = sw;
    c.height = sh;
    var ctx = c.getContext("2d");
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, sw, sh);
    var srcW = source.width || sw;
    var srcH = source.height || sh;
    var fit = fitRect(srcW, srcH, sw, sh);
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(source, fit.x, fit.y, fit.w, fit.h);
    return c;
  }

  function scaleDims(charset, width, height) {
    if (charset === "blocks") return { w: width, h: height * 2 };
    if (charset === "braille") return { w: width * 2, h: height * 4 };
    return { w: width, h: height };
  }

  /* ── 单帧核心：已缩放 RGBA 像素 → ANSI 行（renderFrames 每帧路径，
        也是 TermifyRender.renderRaw 的实现，供一致性比对） ── */
  function renderRaw(data, w, h, charset, opts) {
    opts = opts || {};
    var fg = opts.fg || null;
    var bg = opts.bg || null;
    var src = opts.colorMode === "source" ? data : null;
    if (charset === "blocks") {
      return renderBlocks(data, w, h);
    }
    var lums = luminance(data);
    if (charset === "ascii") return renderRamp(lums, w, h, RAMP_ARRAYS.ascii, fg, bg, src);
    if (charset === "shades") return renderRamp(lums, w, h, RAMP_ARRAYS.shades, fg, bg, src);
    if (charset === "custom") {
      var ramp = sanitizeRamp(opts.ramp);
      return renderRamp(lums, w, h, ramp.length ? ramp : RAMP_ARRAYS.ascii, fg, bg, src);
    }
    if (charset === "binary") return renderBinary(lums, w, h, fg, bg, src);
    if (charset === "geometric") return renderGeometric(lums, w, h, fg, bg, src);
    if (charset === "braille") return renderBraille(lums, w, h, fg, bg, src);
    return renderRamp(lums, w, h, RAMP_ARRAYS.ascii, fg, bg, src);
  }

  /* ── 缓存存取：预算内按帧缓存亮度（非 blocks）或 RGBA（blocks），
        超预算即停写（已写入的条目仍有效），保证内存有界。 ── */
  function cacheGet(cache, key) {
    return cache && cache.map ? cache.map[key] || null : null;
  }
  function cachePut(cache, key, value, cost) {
    if (!cache || cache.off) return;
    if (typeof cache.budget !== "number") cache.budget = DEFAULT_CACHE_BUDGET;
    if ((cache.used || 0) + cost > cache.budget) { cache.off = true; return; }
    cache.used = (cache.used || 0) + cost;
    cache.map[key] = value;
  }

  /* ── 公开入口：整段帧渲染（分块异步 + 进度回调） ──
     opts.cache: 跨调用缓存对象（同一任务同一尺寸切风格时复用），由调用方
     在换任务/换尺寸时换新对象实现失效。 */
  function renderFrames(sources, charset, width, height, opts, onProgress) {
    opts = opts || {};
    var fg = opts.fg || null;
    var bg = opts.bg || null;
    var sourceMode = opts.colorMode === "source";
    var cache = opts.cache || null;
    var dims = scaleDims(charset, width, height);
    var work = document.createElement("canvas");
    work.width = dims.w;
    work.height = dims.h;
    var wctx = work.getContext("2d", { willReadFrequently: true });
    // custom 空梯回退 ascii 的判定放在循环外，避免每帧重复 sanitize
    var customRamp = null;
    if (charset === "custom") {
      customRamp = sanitizeRamp(opts.ramp);
      if (!customRamp.length) customRamp = RAMP_ARRAYS.ascii;
    }

    // 等比缩放 + 黑底居中画进工作画布（镜像 Python scale_frame，绝不拉伸），
    // 返回原始 RGBA；命中缓存时跳过 drawImage + getImageData。
    // blocks 与原色模式都缓存 RGBA（原色下各风格互切共享同一份像素）。
    function scaledData(i) {
      // 缓存键必须含 dims：不同 charset 的放大尺寸不同（blocks width*2/height*2、
      // braille width*2/height*4），同 key 会串尺寸——braille 命中 ascii 的短
      // lums 后越界读 undefined，点阵整帧空白（只留顶部碎片）。
      var key = "r:" + dims.w + "x" + dims.h + ":" + i;
      var cacheData = charset === "blocks" || sourceMode;
      var hit = cacheData ? cacheGet(cache, key) : null;
      if (hit) return hit;
      var srcW = sources[i].width || dims.w;
      var srcH = sources[i].height || dims.h;
      var fit = fitRect(srcW, srcH, dims.w, dims.h);
      wctx.fillStyle = "#000";
      wctx.fillRect(0, 0, dims.w, dims.h);
      wctx.imageSmoothingEnabled = true;
      wctx.imageSmoothingQuality = "high";
      wctx.drawImage(sources[i], fit.x, fit.y, fit.w, fit.h);
      var idata = wctx.getImageData(0, 0, dims.w, dims.h);
      if (cacheData) cachePut(cache, key, idata.data, dims.w * dims.h * 4);
      return idata.data;
    }

    // 亮度只算一次：非 blocks 风格同尺寸互切时直接复用缓存
    function lumsFor(i) {
      var key = "l:" + dims.w + "x" + dims.h + ":" + i;
      var hit = cacheGet(cache, key);
      if (hit) return hit;
      var lums = luminance(scaledData(i));
      cachePut(cache, key, lums, dims.w * dims.h);
      return lums;
    }

    function renderOne(i) {
      if (charset === "blocks") return renderBlocks(scaledData(i), dims.w, dims.h);
      var data = sourceMode ? scaledData(i) : null;
      var lums = lumsFor(i);
      if (charset === "ascii") return renderRamp(lums, dims.w, dims.h, RAMP_ARRAYS.ascii, fg, bg, data);
      if (charset === "shades") return renderRamp(lums, dims.w, dims.h, RAMP_ARRAYS.shades, fg, bg, data);
      if (charset === "custom") return renderRamp(lums, dims.w, dims.h, customRamp, fg, bg, data);
      if (charset === "binary") return renderBinary(lums, dims.w, dims.h, fg, bg, data);
      if (charset === "geometric") return renderGeometric(lums, dims.w, dims.h, fg, bg, data);
      if (charset === "braille") return renderBraille(lums, dims.w, dims.h, fg, bg, data);
      return renderRamp(lums, dims.w, dims.h, RAMP_ARRAYS.ascii, fg, bg, data);
    }

    return new Promise(function (resolve, reject) {
      var frames = [];
      var i = 0;
      function step() {
        try {
          var deadline = performance.now() + 24;  // 每批 ≤24ms 后让出主线程
          while (i < sources.length && performance.now() - deadline < 24) {
            frames.push(renderOne(i));
            i++;
          }
        } catch (e) { reject(e); return; }
        if (onProgress) onProgress(i, sources.length);
        if (i < sources.length) { setTimeout(step, 0); return; }
        resolve(frames);
      }
      step();
    });
  }

  window.TermifyRender = {
    renderFrames: renderFrames,
    renderRaw: renderRaw,
    sanitizeRamp: sanitizeRamp,
    scaleFrame: scaleFrame,
    scaleDims: scaleDims,
    fitRect: fitRect,
    pyRound: pyRound,
  };
})();
