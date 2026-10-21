#!/usr/bin/env node
// 把 ascii-magic-art 的 634 个程序化作品烘焙成 Termify 可直接播放的帧数据。
//
//   npm pack ascii-magic-art@1.0.2 && tar -xzf ascii-magic-art-1.0.2.tgz
//   node scripts/bake_pieces.mjs package
//
// 产物：
//   static/anim/<slug>.json.gz   每个作品一个文件（页面只取被打开的那一个）
//   static/anim/manifest.json    供 seed_pieces.py 建库的元数据
//
// 帧数据形态：作品本来就是 (t)=>frame 的纯函数，浏览器里跑不了 JS，
// 所以在构建期采样成「字符网格 + 调色板索引网格」。字符原样保留（保真），
// 调色板存 hex（可还原原色）。两个 Unsplash 衍生作品按 NOTICE 剔除。

import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { pathToFileURL } from "node:url";

const FRAMES = 16;          // 每个作品采样帧数
const INTERVAL = 0.125;     // 播放间隔（秒）→ 8 fps，一轮 2 秒
const EXCLUDE = new Set(["ocean-sunset", "starry-ridge"]);  // NOTICE：非 MIT

const pkgDir = process.argv[2];
if (!pkgDir) {
  console.error("用法: node scripts/bake_pieces.mjs <ascii-magic-art 解包目录>");
  process.exit(1);
}
const piecesDir = path.join(pkgDir, "pieces");
const outDir = path.join(process.cwd(), "static", "anim");
fs.mkdirSync(outDir, { recursive: true });

const categories = JSON.parse(fs.readFileSync(path.join(pkgDir, "categories.json"), "utf8"));
const catName = new Map(categories.map((c) => [c.id, c.name]));

/** 把一帧切回 rows 行 × cols 列；不足补空格，避免短帧错位。 */
function grid(text, rows, cols) {
  const lines = text.split("\n");
  const out = [];
  for (let y = 0; y < rows; y++) {
    const ln = lines[y] || "";
    out.push(ln.length >= cols ? ln.slice(0, cols) : ln + " ".repeat(cols - ln.length));
  }
  return out;
}

async function bakeOne(file) {
  const mod = await import(pathToFileURL(path.join(piecesDir, file)).href);
  const meta = mod.meta;
  if (!meta || EXCLUDE.has(meta.slug)) return null;
  if (typeof mod.default !== "function") return null;
  const make = mod.default();
  if (typeof make !== "function") return null;

  const cols = meta.cols, rows = meta.rows;
  const palette = meta.palette || {};
  const frames = [];      // [{chars:[row], colors:[row]|null}]
  const usedColors = new Set();

  for (let i = 0; i < FRAMES; i++) {
    const fr = make(i / meta.fps);
    const isColor = fr && typeof fr === "object" && typeof fr.text === "string";
    const text = isColor ? fr.text : String(fr);
    const rowsText = grid(text, rows, cols);
    let rowsColor = null;
    if (isColor) {
      rowsColor = grid(fr.color, rows, cols);
      for (const r of rowsColor) for (const ch of r) if (ch !== " ") usedColors.add(ch);
    }
    frames.push({ c: rowsText, k: rowsColor });
  }

  // 只保留真正用到的调色板项，按首次出现顺序固定，便于稳定 diff
  const keep = [...usedColors].sort();
  const keys = keep.length ? keep : [];
  const palOut = {};
  for (const k of keys) if (palette[k]) palOut[k] = palette[k];

  const data = {
    slug: meta.slug,
    name: meta.name || meta.slug,
    category: meta.category || "",
    categoryName: catName.get(meta.category) || meta.category || "",
    note: meta.note || "",
    tags: Array.isArray(meta.tags) ? meta.tags : [],
    cols, rows,
    cell: meta.cell || "tall",
    fps: meta.fps,
    interval: INTERVAL,
    palette: palOut,
    frames,
  };
  const raw = Buffer.from(JSON.stringify(data), "utf8");
  const gz = zlib.gzipSync(raw, { level: 9 });
  fs.writeFileSync(path.join(outDir, meta.slug + ".json.gz"), gz);
  return {
    slug: meta.slug,
    name: data.name,
    category: data.category,
    categoryName: data.categoryName,
    note: data.note,
    tags: data.tags,
    cols, rows,
    cell: data.cell,
    fps: meta.fps,
    interval: INTERVAL,
    frames: FRAMES,
    hasColor: keys.length > 0,
  };
}

const files = fs.readdirSync(piecesDir).filter((f) => f.endsWith(".js"));
const manifest = [];
let bytes = 0;
for (const f of files) {
  let item;
  try {
    item = await bakeOne(f);
  } catch (e) {
    console.warn("跳过 " + f + ": " + e.message);
    continue;
  }
  if (!item) continue;
  manifest.push(item);
  bytes += fs.statSync(path.join(outDir, item.slug + ".json.gz")).size;
}

manifest.sort((a, b) => a.slug.localeCompare(b.slug));
fs.writeFileSync(path.join(outDir, "manifest.json"), JSON.stringify({
  generatedFrom: "ascii-magic-art",
  license: "MIT (见 static/anim/NOTICE)",
  frames: FRAMES,
  interval: INTERVAL,
  count: manifest.length,
  items: manifest,
}, null, 1), "utf8");

console.log("烘焙完成: " + manifest.length + " 个作品, 共 " +
            (bytes / 1e6).toFixed(2) + " MB, 平均 " +
            (bytes / manifest.length / 1024).toFixed(1) + " KB");
