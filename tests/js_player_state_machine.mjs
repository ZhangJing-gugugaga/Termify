// F2/F3 — 播放/暂停按钮的状态机（无浏览器、无 jsdom）。
//
// 为什么要有它：F2 说的是"无素材态点暂停，class 翻了个儿但什么都没暂停"，
// F3 说的是"播放按钮没有 aria-pressed，屏幕阅读器看不出在播"。这两条都是
// 运行时行为，光 grep 源码关键字测不出来：要真的把 startPlayer / pausePlayer
// 跑起来、真的点一下、再读 class 与 aria-pressed。
//
// 做法：从 static/js/app.js 里按行切出 canPlay / syncPlayerControls /
// startPlayer / pausePlayer 四个函数原文，塞进一个只有 S、两个按钮、
// requestAnimationFrame/cancelAnimationFrame/performance 的最小作用域里
// new Function 求值。跑的就是仓库里那份源码，不是复制品。
//
// 输出：JSON {checks:[{name,ok,detail}], failed:n}；有失败时 exit 1。

import fs from "node:fs";

const SRC_URL = new URL("../static/js/app.js", import.meta.url);
const source = fs.readFileSync(SRC_URL, "utf8");
const lines = source.split("\n");

// app.js 统一两空格缩进、函数闭合行就是 "  }"：按行切比括号配平朴素也够用。
function extractFunction(name) {
  const head = new RegExp("^  function " + name + "\\s*\\(");
  const start = lines.findIndex(l => head.test(l));
  if (start === -1) throw new Error("app.js 里找不到 function " + name);
  for (let i = start + 1; i < lines.length; i++) {
    if (lines[i] === "  }") return lines.slice(start, i + 1).join("\n");
  }
  throw new Error("function " + name + " 没有闭合");
}

const SRC = {
  canPlay: extractFunction("canPlay"),
  syncPlayerControls: extractFunction("syncPlayerControls"),
  startPlayer: extractFunction("startPlayer"),
  pausePlayer: extractFunction("pausePlayer"),
};

function makeBtn(title) {
  const cls = new Set();
  const attrs = {};
  return {
    title,
    classList: {
      add(c) { cls.add(c); },
      remove(c) { cls.delete(c); },
      contains(c) { return cls.has(c); },
      toggle(c, on) {
        if (on === undefined) { cls.has(c) ? cls.delete(c) : cls.add(c); }
        else if (on) { cls.add(c); } else { cls.delete(c); }
      },
    },
    setAttribute(k, v) { attrs[k] = String(v); },
    getAttribute(k) { return k in attrs ? attrs[k] : null; },
    _cls: cls,
    _attrs: attrs,
  };
}

function makeRuntime(frames) {
  const playBtn = makeBtn("Play");
  playBtn.classList.add("active");   // 模板 templates/index.html 出厂就是 active
  const pauseBtn = makeBtn("Pause");
  const raf = { nextId: 1, scheduled: 0, cancelled: 0 };
  const S = { frames: frames, interval: 0.1 };
  const body = [
    "var S = __S, playBtn = __playBtn, pauseBtn = __pauseBtn;",
    "var playing = false, rafId = null, playStartTs = 0, currentFrame = 0;",
    "function rafLoop() {}",
    "function requestAnimationFrame(fn) { __raf.scheduled++; return __raf.nextId++; }",
    "function cancelAnimationFrame(id) { __raf.cancelled++; }",
    "var performance = { now: function () { return 1000; } };",
    SRC.canPlay,
    SRC.syncPlayerControls,
    SRC.startPlayer,
    SRC.pausePlayer,
    "return {",
    "  canPlay: canPlay, sync: syncPlayerControls,",
    "  start: startPlayer, pause: pausePlayer,",
    "  state: function () { return { playing: playing, rafId: rafId }; },",
    "};",
  ].join("\n");
  const api = new Function("__S", "__playBtn", "__pauseBtn", "__raf", body)(S, playBtn, pauseBtn, raf);
  return { api, S, playBtn, pauseBtn, raf };
}

const checks = [];
function check(name, ok, detail) { checks.push({ name, ok: !!ok, detail }); }

function snapshot(rt) {
  return {
    playing: rt.api.state().playing,
    playActive: rt.playBtn.classList.contains("active"),
    pauseActive: rt.pauseBtn.classList.contains("active"),
  };
}

// ── 无素材（0 帧）：按钮外观必须说"暂停"，两个入口都不许动状态 ────────────
{
  const rt = makeRuntime([]);
  rt.api.sync();
  const afterInit = snapshot(rt);
  check("no-material init 显示暂停态", afterInit.playing === false &&
        afterInit.playActive === false && afterInit.pauseActive === true, afterInit);

  const before = snapshot(rt);
  rt.api.pause();                       // 点暂停：死控件的现场
  const afterPause = snapshot(rt);
  check("no-material 点暂停不动 playing",
        afterPause.playing === false, afterPause);
  check("no-material 点暂停不翻 class",
        JSON.stringify(before) === JSON.stringify(afterPause),
        { before, after: afterPause });
  check("no-material 点暂停不安排/取消 rAF",
        rt.raf.scheduled === 0 && rt.raf.cancelled === 0, rt.raf);

  rt.api.start();                       // 点播放：同样不许"看起来在播"
  const afterPlay = snapshot(rt);
  check("no-material 点播放不动 playing",
        afterPlay.playing === false, afterPlay);
  check("no-material 点播放不翻 class",
        afterPlay.playActive === false && afterPlay.pauseActive === true, afterPlay);
  check("no-material 点播放不安排 rAF", rt.raf.scheduled === 0, rt.raf);
}

// ── 单帧（< 2）：同上，rafLoop 播不了 ────────────────────────────────────
{
  const rt = makeRuntime(["only-frame"]);
  rt.api.sync();
  rt.api.start();
  check("单帧点播放被拦下", rt.api.state().playing === false && rt.raf.scheduled === 0,
        { state: snapshot(rt), raf: rt.raf });
}

// ── 有素材（2 帧）：播放/暂停真的切换状态 ────────────────────────────────
{
  const rt = makeRuntime(["a", "b"]);
  rt.api.sync();
  rt.api.start();
  const playing = snapshot(rt);
  check("2 帧点播放：playing=true",
        playing.playing === true, playing);
  check("2 帧点播放：Play.active / Pause 不再 active",
        playing.playActive === true && playing.pauseActive === false, playing);
  check("2 帧点播放：安排了一次 rAF", rt.raf.scheduled === 1, rt.raf);

  rt.api.start();                       // 再点一次播放不应叠加
  check("播放中再点播放是幂等的", rt.raf.scheduled === 1, rt.raf);

  rt.api.pause();
  const paused = snapshot(rt);
  check("2 帧点暂停：playing=false", paused.playing === false, paused);
  check("2 帧点暂停：Play 不再 active、Pause active",
        paused.playActive === false && paused.pauseActive === true, paused);
  check("2 帧点暂停：取消了 rAF", rt.raf.cancelled === 1, rt.raf);

  rt.api.pause();                       // 已经暂停了再点
  check("暂停后再点暂停是幂等的", rt.raf.cancelled === 1, rt.raf);
}

const failed = checks.filter(c => !c.ok).length;
console.log(JSON.stringify({ checks, failed }, null, 2));
if (failed) process.exit(1);
