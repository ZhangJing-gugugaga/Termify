// G-2 fork 短 URL — 跳转侧不再把正文塞进查询串，接收侧一律从作品接口取回原字画。
//
// 为什么要有它：线上 /v/CWhZ2QHX 的 fork URL 实测 12298 字节，超过约 4096 的
// 请求行上限，被前置代理回裸 400（二分：4079 字节 200 / 4088 字节 400）。
// 纯 grep 断言测不出"URL 真的短了"和"打开短 URL 后编辑器真的还显示原字画"，
// 所以把两侧的源码原文切出来真跑一遍：
//   · 跳转侧：view_work_page.js 里 forkBtn 的 click 处理器，拦截 window.location.href；
//   · 接收侧：text_art.js 里 pickFont / artDims / showForkedArt / loadForkedWork /
//     applyForkParams，喂可编排的 fetch，看 showArt 收到什么、失败时 toast 什么。
//
// 做法与 tests/js_view_work_player_state.mjs 相同（按行切源码 + 最小作用域求值）。
//
// 输出：JSON {checks:[{name,ok,detail}], failed:n}；有失败时 exit 1。

import fs from "node:fs";

const VIEW_SRC = fs.readFileSync(new URL("../static/js/view_work_page.js", import.meta.url), "utf8");
const TA_SRC = fs.readFileSync(new URL("../static/js/text_art.js", import.meta.url), "utf8");

// 长正文作品：120 行 × 100 列 = 12000 字符，尺寸在入库红线内（MAX_ART_ROWS=120 /
// MAX_ART_COLS=440），长度与线上那件 12298 字节 URL 的作品同量级。
function bigArt() {
  const rows = [];
  for (let r = 0; r < 120; r++) {
    let line = "";
    for (let c = 0; c < 100; c++) line += String.fromCharCode(97 + ((r * 7 + c * 3) % 26));
    rows.push(line);
  }
  return rows.join("\n");
}
const LONG_ART = bigArt();
const WORK_ID = "CWhZ2QHX";

const checks = [];
function check(name, ok, detail) { checks.push({ name: name, ok: !!ok, detail: detail }); }

/* ── 跳转侧：forkBtn click 处理器 ──────────────────────────────── */
function extractForkHandlerBlock() {
  const lines = VIEW_SRC.split("\n");
  const start = lines.findIndex(l => l.indexOf('var _forkBtn = document.getElementById("forkBtn");') >= 0);
  if (start === -1) throw new Error("view_work_page.js 里找不到 forkBtn 接线");
  for (let i = start + 1; i < lines.length; i++) {
    if (/^ {12}\}\);\s*$/.test(lines[i])) return lines.slice(start, i + 1).join("\n");
  }
  throw new Error("forkBtn 处理器没有闭合");
}

const FORK_BLOCK = extractForkHandlerBlock();
const viewFactory = new Function(
  "document", "window", "IS_TEXT", "params", "WORK_ID", "textArtString",
  "curCharset", "curWidth", "curHeight", "curTheme", "THEME_COLORS", "__st",
  FORK_BLOCK + "\n;return { handler: __st.handler };"
);

// 跑一遍 click 处理器，返回它真正要跳去的 URL（不真跳）
function clickFork(opts) {
  const st = { artReads: 0, handler: null };
  const el = { addEventListener: function (t, fn) { st.handler = fn; } };
  const documentStub = { getElementById: function (id) { return id === "forkBtn" ? el : null; } };
  const win = { location: {} };
  const textArtString = function () {
    st.artReads++;
    return opts.art === undefined ? LONG_ART : opts.art;
  };
  viewFactory(documentStub, win, opts.isText, opts.params || {}, opts.workId || WORK_ID,
              textArtString, "ascii", 80, 24, "saved", {}, st);
  if (typeof st.handler !== "function") throw new Error("forkBtn 没有绑上 click 处理器");
  st.handler();
  return { href: String(win.location.href || ""), artReads: st.artReads };
}

const forked = clickFork({ isText: true, params: { font: "standard" } });
check("跳转 URL 上界 < 512 字节",
      forked.href.length > 0 && forked.href.length < 512,
      "href=" + forked.href + " len=" + forked.href.length + "（正文 " + LONG_ART.length + " 字符未进 URL）");
check("跳转 URL 只带 fork + font",
      forked.href === "/text-art?fork=" + WORK_ID + "&font=standard",
      "href=" + forked.href);
check("跳转 URL 里没有正文（没有 text 参数）",
      forked.href.indexOf("text=") === -1,
      "href=" + forked.href);
check("跳转侧不再为了拼 URL 去读整幅正文",
      forked.artReads === 0,
      "textArtString() 调用次数=" + forked.artReads);

// 对照：修复前的形态（正文直接拼进查询串）在同一份正文上会顶到十几 KB
const legacyHref = "/text-art?fork=" + WORK_ID + "&font=standard&text=" + encodeURIComponent(LONG_ART);
check("对照：旧形态确实超请求行上限（> 4096）",
      legacyHref.length > 4096,
      "旧形态 len=" + legacyHref.length + " vs 新形态 len=" + forked.href.length);

// 图片作品分支不受影响（仍是风格/尺寸/配色的短参数）
const imgFork = clickFork({ isText: false, params: {}, art: "" });
check("图片作品的 fork 分支保持原样",
      imgFork.href === "/?fork=" + WORK_ID + "&charset=ascii&width=80&height=24",
      "href=" + imgFork.href);

/* ── 接收侧：text_art.js ──────────────────────────────────────── */
function extractTaFunction(name) {
  const lines = TA_SRC.split("\n");
  const head = new RegExp("^ {2}function " + name + "\\s*\\(");
  const start = lines.findIndex(l => head.test(l));
  if (start === -1) throw new Error("text_art.js 里找不到 function " + name);
  for (let i = start + 1; i < lines.length; i++) {
    if (lines[i] === "  }") return lines.slice(start, i + 1).join("\n");
  }
  throw new Error("function " + name + " 没有闭合");
}

const TA_SRC_PARTS = ["pickFont", "artDims", "showForkedArt", "loadForkedWork", "applyForkParams"]
  .map(extractTaFunction).join("\n");

const taFactory = new Function(
  "__st", "taFont", "window", "fetch", "URLSearchParams",
  [
    "function toast(m) { __st.toast.push(m); }",
    "function hideFontWall() { __st.hideFontWall++; }",
    "function showArt(o) { __st.showArt.push(o); }",
    "function showEmpty() { __st.showEmpty++; }",
    TA_SRC_PARTS,
    "return { apply: applyForkParams };",
  ].join("\n")
);

const tick = function () { return new Promise(function (r) { setTimeout(r, 0); }); };

// 用一段真实 search 打开编辑器，fetch 按 plan 依次给响应
async function openEditor(search, plan) {
  const st = { toast: [], showArt: [], showEmpty: 0, hideFontWall: 0, fetch: [] };
  const taFont = {
    value: "",
    options: [{ value: "standard", disabled: false }, { value: "slant", disabled: false }],
  };
  const fakeFetch = function (url) {
    st.fetch.push(String(url));
    const next = plan.length ? plan.shift() : { ok: true, status: 200, body: {} };
    if (next.reject) return Promise.reject(new Error("network down"));
    return Promise.resolve({
      ok: next.ok !== false,
      status: next.status || 200,
      json: function () {
        if (next.badJson) return Promise.reject(new Error("not json"));
        return Promise.resolve(next.body || {});
      },
    });
  };
  const api = taFactory(st, taFont, { location: { search: search } }, fakeFetch, URLSearchParams);
  api.apply(Promise.resolve(true));   // fontsReady
  await tick(); await tick(); await tick();
  st.taFont = taFont;   // 字体下拉最终选中的值
  return st;
}

// ① 打开跳转侧刚拼出的那个短 URL：原字画必须从作品接口回来，并且一模一样
const apiBody = { id: WORK_ID, params: { kind: "text", font: "slant", frames: [LONG_ART] } };
const opened = await openEditor(forked.href.slice(forked.href.indexOf("?")),
                                [{ ok: true, status: 200, body: apiBody }]);
check("打开短 URL 后只请求作品接口",
      opened.fetch.length === 1 && opened.fetch[0] === "/api/gallery/work/" + WORK_ID,
      "fetch=" + JSON.stringify(opened.fetch));
check("打开短 URL 后编辑器拿到原字画（逐字符相同）",
      opened.showArt.length === 1 && opened.showArt[0].art === LONG_ART,
      "showArt 次数=" + opened.showArt.length +
      "，长度=" + (opened.showArt[0] ? String(opened.showArt[0].art).length : 0) +
      "，期望长度=" + LONG_ART.length);
check("原字画的网格/字体按作品数据落地",
      opened.showArt.length === 1 && opened.showArt[0].rows === 120 &&
      opened.showArt[0].cols === 100 && opened.showArt[0].font === "standard",
      JSON.stringify(opened.showArt[0] ? {
        rows: opened.showArt[0].rows, cols: opened.showArt[0].cols,
        font: opened.showArt[0].font, mode: opened.showArt[0].mode,
      } : null));

// URL 不带 font 时用作品数据里的字体
const fontFromWork = await openEditor("?fork=" + WORK_ID, [{ ok: true, status: 200, body: apiBody }]);
check("URL 不带 font 时用作品数据里的字体（下拉也对齐）",
      fontFromWork.showArt.length === 1 && fontFromWork.showArt[0].font === "slant" &&
      fontFromWork.taFont.value === "slant",
      JSON.stringify({ art: fontFromWork.showArt[0] && fontFromWork.showArt[0].font,
                       select: fontFromWork.taFont.value }));
check("载入成功后给出提示而不是静默",
      opened.toast.length === 1 && opened.toast[0].indexOf("已载入原作品") >= 0,
      JSON.stringify(opened.toast));

// URL 里显式带 font 时以 URL 为准（与改动前同语义）
const fontOverride = await openEditor("?fork=" + WORK_ID + "&font=standard",
                                      [{ ok: true, status: 200, body: apiBody }]);
check("URL 里的 font 优先于作品数据",
      fontOverride.showArt.length === 1 && fontOverride.showArt[0].font === "standard",
      JSON.stringify(fontOverride.showArt[0] && fontOverride.showArt[0].font));

// ② 接口 404（作品已删除/不存在）：给明确降级提示 + 回空态，不许静默、不许崩
const notFound = await openEditor("?fork=gone999", [{ ok: false, status: 404, body: { error: "Work not found" } }]);
check("接口 404 → 空态 + 提示，不静默",
      notFound.showEmpty === 1 && notFound.toast.length === 1 &&
      notFound.toast[0].indexOf("读取失败") >= 0,
      "showEmpty=" + notFound.showEmpty + " toast=" + JSON.stringify(notFound.toast));
check("接口 404 → 不画任何东西",
      notFound.showArt.length === 0,
      "showArt 次数=" + notFound.showArt.length);

// ③ 网络失败 / 非 JSON 响应：同样降级
const netFail = await openEditor("?fork=" + WORK_ID, [{ reject: true }]);
check("网络失败 → 降级提示",
      netFail.showEmpty === 1 && netFail.toast.length === 1 &&
      netFail.toast[0].indexOf("读取失败") >= 0,
      "showEmpty=" + netFail.showEmpty + " toast=" + JSON.stringify(netFail.toast));
const badJson = await openEditor("?fork=" + WORK_ID, [{ ok: true, badJson: true }]);
check("响应不是 JSON → 降级提示",
      badJson.showEmpty === 1 && badJson.toast.length === 1,
      "showEmpty=" + badJson.showEmpty + " toast=" + JSON.stringify(badJson.toast));

// ④ 可见性只认接口：作品数据为空（私有被拒/无帧）时不展示任何正文
const emptyFrames = await openEditor("?fork=" + WORK_ID,
                                     [{ ok: true, status: 200, body: { params: { kind: "text", frames: [""] } } }]);
check("接口给不出帧 → 不展示任何正文",
      emptyFrames.showArt.length === 0 && emptyFrames.toast.length === 1,
      "showArt=" + emptyFrames.showArt.length + " toast=" + JSON.stringify(emptyFrames.toast));

// ⑤ 旧链接（正文曾随 URL 来）不再被信任：URL 里那坨正文一律忽略，只认接口
const legacyQuery = "?fork=" + WORK_ID + "&font=standard&text=" + encodeURIComponent("URL-INJECTED-ART");
const legacyOpen = await openEditor(legacyQuery, [{ ok: false, status: 404, body: { error: "Work not found" } }]);
check("旧链接里 URL 自带的正文不被采信（接口 404 就不出图）",
      legacyOpen.showArt.length === 0 && legacyOpen.showEmpty === 1 &&
      legacyOpen.fetch.length === 1 && legacyOpen.fetch[0] === "/api/gallery/work/" + WORK_ID,
      "showArt=" + legacyOpen.showArt.length + " fetch=" + JSON.stringify(legacyOpen.fetch));

// ⑥ 没有 fork 参数时统统不请求
const noFork = await openEditor("?font=standard", [{ ok: true, status: 200, body: apiBody }]);
check("没有 fork 参数就不发请求",
      noFork.fetch.length === 0 && noFork.showArt.length === 0,
      "fetch=" + JSON.stringify(noFork.fetch));

const failed = checks.filter(c => !c.ok).length;
console.log(JSON.stringify({ checks: checks, failed: failed }, null, 1));
if (failed) process.exitCode = 1;
