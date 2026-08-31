/* TermifyRender — 浏览器端字符画渲染器（与 termify/charset.py 同公式）。
   供主页（本地视频方案B）与画廊作品页（视频作品客户端渲染）共用。

   公开 API：
   - TermifyRender.scaleFrame(source, sw, sh) -> canvas
       等比缩放 + 黑底居中 letterbox（镜像 Python scale_frame，绝不拉伸）
   - TermifyRender.renderFrames(sources, charset, width, height, opts, onProgress)
       -> Promise<string[][]>  ANSI 帧数组；分块异步让出主线程
       sources: 可绘制对象数组（canvas / ImageBitmap / img）
       opts: { ramp(自定义字符梯), fg, bg }；onProgress(done, total)
*/
(function () {
  "use strict";

  var ESC = "\x1b";
  var RAMP_CHARS = { ascii: "@#%*+=-:. ", shades: "█▓▒░ " };
  var BRAILLE_DOTS = [
    [0, 0, 0x01], [0, 1, 0x02], [0, 2, 0x04],
    [1, 0, 0x08], [1, 1, 0x10], [1, 2, 0x20],
    [0, 3, 0x40], [1, 3, 0x80],
  ];

  /* ── 基础：亮度 / 直方图 / Otsu / 自适应 LUT（同 Python 公式） ── */
  function luminance(imageData) {
    var d = imageData.data;
    var n = d.length / 4;
    var lums = new Array(n);
    for (var i = 0, p = 0; i < n; i++, p += 4) {
      lums[i] = Math.round(0.299 * d[p] + 0.587 * d[p + 1] + 0.114 * d[p + 2]);
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
      var variance = wBg * wFg * (mBg - mFg) * (mBg - mFg);
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
      else lut[i] = Math.round((cdf - cdfMin) / (total - cdfMin) * 255);
    }
    return lut;
  }

  /* ── ANSI 组装 ── */
  function ansiFg(rgb) { return ESC + "[38;2;" + rgb[0] + ";" + rgb[1] + ";" + rgb[2] + "m"; }
  function ansiBg(rgb) { return ESC + "[48;2;" + rgb[0] + ";" + rgb[1] + ";" + rgb[2] + "m"; }

  /* ── 各字符集渲染（输入：行主序亮度数组 + 源宽高） ── */
  function renderRamp(lums, w, h, chars, fg, bg) {
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
    return assemble(lums, w, h, cell, fg, bg);
  }

  function renderBinary(lums, w, h, fg, bg) {
    var ots = otsu(lums);
    var threshold = ots[0], mib = ots[1];
    var cell = new Array(256);
    for (var g = 0; g < 256; g++) {
      cell[g] = mib ? (g >= threshold ? "█" : " ") : (g < threshold ? "█" : " ");
    }
    return assemble(lums, w, h, cell, fg, bg);
  }

  function renderGeometric(lums, w, h, fg, bg) {
    var chars = "■●◆▪▫◇○ ";
    var n = chars.length;
    var mib = otsu(lums)[1];
    var cell = new Array(256);
    for (var g = 0; g < 256; g++) {
      var idx = mib ? (n - 1) - Math.floor(g * (n - 1) / 255)
                    : Math.floor(g * (n - 1) / 255);
      cell[g] = chars[idx];
    }
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

  function renderBraille(lums, srcW, srcH, fg, bg) {
    var bc = brailleCoords(srcW, srcH);
    var table = bc.table;
    var ots = otsu(lums);
    var threshold = ots[0], mib = ots[1];
    var lines = [];
    var pos = 0;
    for (var by = 0; by < bc.outH; by++) {
      var row = "";
      for (var bx = 0; bx < bc.outW; bx++) {
        var bits = 0;
        for (var d = 0; d < 8; d++) {
          var lum = lums[table[pos + d][0]];
          var mask = table[pos + d][1];
          if (mib ? lum >= threshold : lum < threshold) bits |= mask;
        }
        pos += 8;
        var ch = String.fromCharCode(0x2800 + bits);
        if (fg) row += ansiFg(fg);
        if (bg) row += ansiBg(bg);
        row += ch;
      }
      if (fg || bg) row += ESC + "[0m";
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
  function scaleFrame(source, sw, sh) {
    var c = document.createElement("canvas");
    c.width = sw;
    c.height = sh;
    var ctx = c.getContext("2d");
    ctx.fillStyle = "#000";
    ctx.fillRect(0, 0, sw, sh);
    var srcW = source.width || sw;
    var srcH = source.height || sh;
    var scale = Math.min(sw / srcW, sh / srcH);
    var fw = Math.max(1, Math.round(srcW * scale));
    var fh = Math.max(1, Math.round(srcH * scale));
    ctx.imageSmoothingEnabled = true;
    ctx.imageSmoothingQuality = "high";
    ctx.drawImage(source, Math.floor((sw - fw) / 2), Math.floor((sh - fh) / 2), fw, fh);
    return c;
  }

  function scaleDims(charset, width, height) {
    if (charset === "blocks") return { w: width, h: height * 2 };
    if (charset === "braille") return { w: width * 2, h: height * 4 };
    return { w: width, h: height };
  }

  /* ── 公开入口：整段帧渲染（分块异步 + 进度回调） ── */
  function renderFrames(sources, charset, width, height, opts, onProgress) {
    opts = opts || {};
    var fg = opts.fg || null;
    var bg = opts.bg || null;
    var dims = scaleDims(charset, width, height);
    var work = document.createElement("canvas");
    work.width = dims.w;
    work.height = dims.h;
    var wctx = work.getContext("2d", { willReadFrequently: true });

    return new Promise(function (resolve, reject) {
      var frames = [];
      var i = 0;
      function step() {
        try {
          var deadline = performance.now() + 24;  // 每批 ≤24ms 后让出主线程
          while (i < sources.length && performance.now() - deadline < 24) {
            // 等比缩放 + 黑底居中，直接画进工作画布（镜像 Python scale_frame，绝不拉伸）
            var srcW = sources[i].width || dims.w;
            var srcH = sources[i].height || dims.h;
            var sc = Math.min(dims.w / srcW, dims.h / srcH);
            var fw = Math.max(1, Math.round(srcW * sc));
            var fh = Math.max(1, Math.round(srcH * sc));
            wctx.fillStyle = "#000";
            wctx.fillRect(0, 0, dims.w, dims.h);
            wctx.imageSmoothingEnabled = true;
            wctx.imageSmoothingQuality = "high";
            wctx.drawImage(sources[i],
              Math.floor((dims.w - fw) / 2), Math.floor((dims.h - fh) / 2), fw, fh);
            var idata = wctx.getImageData(0, 0, dims.w, dims.h);
            if (charset === "blocks") {
              frames.push(renderBlocks(idata.data, dims.w, dims.h));
            } else {
              var lums = luminance(idata);
              if (charset === "ascii") frames.push(renderRamp(lums, dims.w, dims.h, RAMP_CHARS.ascii, fg, bg));
              else if (charset === "shades") frames.push(renderRamp(lums, dims.w, dims.h, RAMP_CHARS.shades, fg, bg));
              else if (charset === "custom") frames.push(renderRamp(lums, dims.w, dims.h, opts.ramp || RAMP_CHARS.ascii, fg, bg));
              else if (charset === "binary") frames.push(renderBinary(lums, dims.w, dims.h, fg, bg));
              else if (charset === "geometric") frames.push(renderGeometric(lums, dims.w, dims.h, fg, bg));
              else if (charset === "braille") frames.push(renderBraille(lums, dims.w, dims.h, fg, bg));
              else frames.push(renderRamp(lums, dims.w, dims.h, RAMP_CHARS.ascii, fg, bg));
            }
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
    scaleFrame: scaleFrame,
    scaleDims: scaleDims,
  };
})();
