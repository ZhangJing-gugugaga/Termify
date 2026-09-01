/*
 * JS ↔ Python 渲染逻辑一致性比对脚本（方案B 本地渲染验证）。
 *
 * 方法：
 *   1. 在 Node 中加载 static/js/termify-render.js（浏览器渲染器）。
 *   2. 用同一份「合成像素」分别驱动：
 *        - Python: termify.charset.render_frame（服务端权威实现）
 *        - JS:     TermifyRender.renderRaw（浏览器本地实现）
 *      合成像素由 JS 生成后原样传给 Python 构造 PIL Image，保证输入逐位相同，
 *      因此输出 ANSI 行可以逐字符比对（排除 JPEG/重采样这类固有的输入差异）。
 *   3. 逐字符核对内容：
 *        - 亮度公式：全 256×256×256 RGB 空间的 round(0.299r+0.587g+0.114b)
 *          序列哈希（Python round vs JS pyRound 银行家舍入）+ 0..255 灰阶全表；
 *        - ramp 梯度序列：grad256 图（256 个灰阶一字排开）渲染出的字符序列；
 *        - 7 种字符集 × 3 组 fg/bg 变体 × 5 张合成图（含非均匀直方图、均匀图、
 *          奇数高度、2×3 极小图）的全帧 ANSI 输出；
 *        - custom 字符梯 sanitize（去 ANSI/控制字符、码点去重、上限 64）；
 *        - letterbox fit 矩形：真实 scale_frame 的非黑内容 bbox vs JS fitRect。
 *   4. 任一不一致 → 打印差异并以退出码 1 结束；全部一致 → 打印 PASS。
 *
 * 运行（在仓库根目录）：
 *   TERMIFY_PYTHON=/d/ZhangJing/python/python.exe node tests/js_render_consistency.mjs
 * （未设置 TERMIFY_PYTHON 时回退到 PATH 中的 python。）
 */
import { createRequire } from "node:module";
import { spawnSync } from "node:child_process";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(__dirname, "..");

global.window = {};
require(path.join(ROOT, "static", "js", "termify-render.js"));
const TR = global.window.TermifyRender;

/* ── 合成像素用例（JS 生成，原样传给 Python） ── */
const CASES = {
  // 256 个灰阶一字排开：ramp 梯度序列 + 亮度全表直读
  grad256: { w: 256, h: 1, px: (x, y) => [x, x, x] },
  // 非均匀直方图（多数暗 + 少数亮）：自适应 LUT 拉伸 + Otsu 少数侧判定
  skew: {
    w: 32, h: 8,
    px: (x, y) => {
      const i = y * 32 + x;
      const v = i < 240 ? i % 41 : 180 + (i - 240) * 5;
      return [v, v, v];
    },
  },
  // 均匀图：Otsu 退化分支（无少数侧）+ LUT 恒等回退
  uniform: { w: 16, h: 16, px: () => [128, 128, 128] },
  // 彩色渐变（奇数高度）：blocks 半块取底行钳制 + TrueColor 序列
  colorful: {
    w: 32, h: 15,
    px: (x, y) => [(x * 8) % 256, (y * 17) % 256, (x * 13 + y * 7) % 256],
  },
  // 2×3 极小图：braille max(1,..) 与 blocks 边界
  tiny: { w: 2, h: 3, px: (x, y) => [(x * 90 + y * 40) % 256, (x * 200 + y * 100) % 256, (x * 7 + y * 250) % 256] },
};
const CHARSETS = ["ascii", "shades", "binary", "geometric", "blocks", "braille", "custom"];
const VARIANTS = [
  { name: "plain", fg: null, bg: null },
  { name: "fg", fg: [0, 255, 65], bg: null },
  { name: "fgbg", fg: [0, 255, 65], bg: [10, 14, 20] },
];
const RAMPS = ["@%#*+=-:.", "@%\x07#*+=-:.\x1b[31m\u200b@A", "█▓▒░ "];

// 组装 JS 侧输入像素 + RGBA（renderRaw 输入）
const caseData = {};
for (const [name, c] of Object.entries(CASES)) {
  const rgb = [];
  for (let y = 0; y < c.h; y++) {
    for (let x = 0; x < c.w; x++) rgb.push(...c.px(x, y));
  }
  const rgba = new Uint8ClampedArray(c.w * c.h * 4);
  for (let i = 0; i < c.w * c.h; i++) {
    rgba[i * 4] = rgb[i * 3];
    rgba[i * 4 + 1] = rgb[i * 3 + 1];
    rgba[i * 4 + 2] = rgb[i * 3 + 2];
    rgba[i * 4 + 3] = 255;
  }
  caseData[name] = { w: c.w, h: c.h, rgb, rgba };
}

// letterbox 比对扫掠（src 全覆盖小尺寸 + 瘦高/极扁组合）
const fitSrc = [];
for (let w = 1; w <= 40; w++) for (const h of [1, 2, 3, 5, 8, 13, 21, 34, 55, 89]) fitSrc.push([w, h]);
for (const [w, h] of [[89, 5], [239, 1], [1, 239], [400, 240], [240, 400]]) fitSrc.push([w, h]);
const fitDst = [[80, 24], [80, 48], [160, 96], [200, 120], [400, 240], [40, 20], [1, 1], [3, 7]];

/* ── Python 子进程：用同一像素驱动 termify.charset.render_frame ── */
const PY_CODE = `
import base64, json, sys
args = json.loads(sys.stdin.buffer.read().decode("utf-8"))
sys.path.insert(0, args["root"])
from PIL import Image, ImageChops
from termify.charset import render_frame, sanitize_ramp
from termify.frames import scale_frame

out = {"renders": {}, "sanitize": [], "fitRects": {}, "lums256": []}

# 亮度公式：全 RGB 空间序列哈希（与 JS 相同的结合序与舍入）
h = 0
for r in range(256):
    a = 0.299 * r
    for g in range(256):
        ab = a + 0.587 * g
        for b in range(256):
            v = round(ab + 0.114 * b)
            h = (h * 31 + v) % 1000000007
out["lumHash"] = h

# 灰阶全表（grad256 图的实际亮度）
im = Image.new("RGB", (256, 1))
im.putdata([(x, x, x) for x in range(256)])
from termify.charset import _luminance_array
out["lums256"] = _luminance_array(im)

for name, c in args["cases"].items():
    im = Image.new("RGB", (c["w"], c["h"]))
    im.putdata([(c["rgb"][i], c["rgb"][i+1], c["rgb"][i+2]) for i in range(0, len(c["rgb"]), 3)])
    for charset in args["charsets"]:
        ramps = args["ramps"] if charset == "custom" else [None]
        for ri, ramp in enumerate(ramps):
            for var in args["variants"]:
                if charset == "blocks" and var["name"] != "plain":
                    continue  # 服务端 blocks 忽略 fg/bg
                lines = render_frame(im, charset, c["w"], c["h"],
                                     fg_color=tuple(var["fg"]) if var["fg"] else None,
                                     bg_color=tuple(var["bg"]) if var["bg"] else None,
                                     charset_ramp=ramp)
                out["renders"][name + "|" + charset + "|" + var["name"] + "|" + str(ri)] = lines

for ramp in args["ramps"]:
    try:
        out["sanitize"].append([ord(ch) for ch in sanitize_ramp(ramp)])
    except ValueError:
        out["sanitize"].append([])

black_cache = {}
for sw, sh in args["fitSrc"]:
    im = Image.new("RGB", (sw, sh), (200, 30, 40))
    for tw, th in args["fitDst"]:
        res = scale_frame(im, tw, th).convert("RGB")
        black = black_cache.get((tw, th))
        if black is None:
            black = Image.new("RGB", (tw, th), (0, 0, 0))
            black_cache[(tw, th)] = black
        bb = ImageChops.difference(res, black).getbbox()
        out["fitRects"][str(sw) + "x" + str(sh) + ">" + str(tw) + "x" + str(th)] = (
            [bb[0], bb[1], bb[2] - bb[0], bb[3] - bb[1]] if bb else [0, 0, 0, 0])

sys.stdout.buffer.write(base64.b64encode(json.dumps(out, ensure_ascii=True).encode("utf-8")))
`;

const input = {
  root: ROOT,
  cases: Object.fromEntries(Object.entries(caseData).map(([k, v]) => [k, { w: v.w, h: v.h, rgb: v.rgb }])),
  charsets: CHARSETS,
  variants: VARIANTS,
  ramps: RAMPS,
  fitSrc,
  fitDst,
};
const pyExe = process.env.TERMIFY_PYTHON || "python";
const run = spawnSync(pyExe, ["-c", PY_CODE],
  { cwd: ROOT, maxBuffer: 256 * 1024 * 1024, encoding: "buffer",
    input: Buffer.from(JSON.stringify(input), "utf-8") });
if (run.error || run.status !== 0) {
  console.error("Python 子进程失败（需 Pillow + termify 可导入）：", run.error || run.stderr.toString());
  process.exit(2);
}
const py = JSON.parse(Buffer.from(run.stdout.toString(), "base64").toString("utf-8"));

let pass = 0, fail = 0;
function check(label, ok, detail) {
  if (ok) { pass++; console.log("  PASS  " + label); }
  else { fail++; console.log("  FAIL  " + label + (detail ? "\n        " + detail : "")); }
}

/* ── 1. 亮度公式（全空间哈希 + 灰阶全表） ── */
console.log("[1] 亮度公式 round(0.299r+0.587g+0.114b) — 全 16,777,216 组合");
let jsHash = 0, mathRoundDiff = 0;
for (let r = 0; r < 256; r++) {
  const a = 0.299 * r;
  for (let g = 0; g < 256; g++) {
    const ab = a + 0.587 * g;
    for (let b = 0; b < 256; b++) {
      const x = ab + 0.114 * b;
      const v = TR.pyRound(x);
      jsHash = (jsHash * 31 + v) % 1000000007;
      if (Math.round(x) !== v) mathRoundDiff++;
    }
  }
}
check("全空间亮度序列哈希 pyRound == Python round", jsHash === py.lumHash,
  "js=" + jsHash + " py=" + py.lumHash);
const lums256 = [];
for (let x = 0; x < 256; x++) lums256.push(TR.pyRound(0.299 * x + 0.587 * x + 0.114 * x));
check("灰阶 0..255 亮度全表", JSON.stringify(lums256) === JSON.stringify(py.lums256));
console.log("        （Math.round 与银行家舍入在全空间存在 " + mathRoundDiff + " 个差异值 → 已全部改用 pyRound）");

/* ── 2. ramp 梯度序列（grad256 渲染出的逐字符映射） ── */
console.log("[2] ramp 梯度序列（256 灰阶 → 字符）");
for (const cs of ["ascii", "shades", "geometric"]) {
  const pyLine = py.renders["grad256|" + cs + "|plain|0"][0];
  const jsLine = TR.renderRaw(caseData.grad256.rgba, 256, 1, cs)[0];
  check(cs + " 梯度序列逐字符一致", pyLine === jsLine,
    "py=" + JSON.stringify(pyLine.slice(0, 64)) + "… js=" + JSON.stringify(jsLine.slice(0, 64)) + "…");
  console.log("        " + cs + ": " + pyLine.slice(0, 40) + "…");
}

/* ── 3. 全帧 ANSI 输出（7 字符集 × 变体 × 合成图） ── */
console.log("[3] 全帧 ANSI 逐字符比对");
let caseCount = 0;
for (const name of Object.keys(caseData)) {
  const c = caseData[name];
  for (const charset of CHARSETS) {
    const ramps = charset === "custom" ? RAMPS : [null];
    for (let ri = 0; ri < ramps.length; ri++) {
      for (const variant of VARIANTS) {
        if (charset === "blocks" && variant.name !== "plain") continue;
        const key = name + "|" + charset + "|" + variant.name + "|" + ri;
        const jsLines = TR.renderRaw(c.rgba, c.w, c.h, charset,
          { ramp: RAMPS[ri], fg: variant.fg, bg: variant.bg });
        const pyLines = py.renders[key];
        const ok = JSON.stringify(jsLines) === JSON.stringify(pyLines);
        check(key, ok, ok ? "" : "首处差异:\n        py=" + JSON.stringify(firstDiff(pyLines, jsLines)) +
          "\n        js=" + JSON.stringify(firstDiff(jsLines, pyLines)));
        caseCount++;
      }
    }
  }
}
console.log("        共 " + caseCount + " 组全帧比对");

/* ── 4. custom 字符梯 sanitize ── */
console.log("[4] sanitize_ramp 码点序列");
RAMPS.forEach((ramp, i) => {
  const js = TR.sanitizeRamp(ramp).map((ch) => ch.codePointAt(0));
  check("ramp[" + i + "] " + JSON.stringify(ramp.slice(0, 12)) + "…", JSON.stringify(js) === JSON.stringify(py.sanitize[i]),
    "py=" + JSON.stringify(py.sanitize[i]) + " js=" + JSON.stringify(js));
});

/* ── 5. letterbox fit 矩形（真实 scale_frame bbox vs JS fitRect） ── */
console.log("[5] letterbox fit 矩形（Python scale_frame 实测 bbox vs fitRect）");
let fitCount = 0, fitBad = [];
for (const [sw, sh] of fitSrc) {
  for (const [tw, th] of fitDst) {
    const key = sw + "x" + sh + ">" + tw + "x" + th;
    const f = TR.fitRect(sw, sh, tw, th);
    const got = [f.x, f.y, f.w, f.h];
    if (JSON.stringify(got) === JSON.stringify(py.fitRects[key])) fitCount++;
    else fitBad.push(key + " py=" + JSON.stringify(py.fitRects[key]) + " js=" + JSON.stringify(got));
  }
}
check("全部 " + (fitSrc.length * fitDst.length) + " 组 fit 矩形一致", fitBad.length === 0, fitBad.slice(0, 5).join("\n        "));

console.log("\n==== 结果: " + pass + " PASS, " + fail + " FAIL ====");
process.exit(fail ? 1 : 0);

function firstDiff(a, b) {
  if (!a) return "(missing)";
  for (let i = 0; i < Math.max(a.length, b.length); i++) {
    if (a[i] !== b[i]) return "line " + i + ": " + (a[i] || "").slice(0, 80) + " …vs… " + (b[i] || "").slice(0, 80);
  }
  return "(length differs)";
}
