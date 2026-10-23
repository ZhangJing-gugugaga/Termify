// F5 — /v/ 详情页播放按钮的状态机（static/js/view_work_page.js，不是 app.js）。
//
// 为什么另起一个：详情页的播放器是 view_work_page.js 里自己那份实现
// （playBtn/pauseBtn 按 id 取，frames 数组本地），和 app.js 完全独立。
// F2/F3 只修了 app.js，详情页上"无素材点暂停 → 翻了 class 但没暂停、也没有
// aria-pressed"照旧存在 —— 这个 harness 就是钉住这条路径的。
//
// 做法与 tests/js_player_state_machine.mjs 相同：按行把 canPlay /
// syncPlayerControls / startPlayer / pausePlayer 原文切出来，塞进最小作用域
// new Function 求值，跑的是仓库里那份源码。
//
// 输出：JSON {checks:[{name,ok,detail}], failed:n}；有失败时 exit 1。

import fs from "node:fs";

const SRC_URL = new URL("../static/js/view_work_page.js", import.meta.url);
const lines = fs.readFileSync(SRC_URL, "utf8").split("\n");

// 这个文件整体缩在 IIFE 里，函数头缩进 12 空格、闭合行就是 12 空格 + "}"
function extractFunction(name) {
  const head = new RegExp("^ {12}function " + name + "\\s*\\(");
  const start = lines.findIndex(l => head.test(l));
  if (start === -1) throw new Error("view_work_page.js 里找不到 function " + name);
  for (let i = start + 1; i < lines.length; i++) {
    if (lines[i] === "            }") return lines.slice(start, i + 1).join("\n");
  }
  throw new Error("function " + name + " 没有闭合");
}

const SRC = {
  canPlay: extractFunction("canPlay"),
  syncPlayerControls: extractFunction("syncPlayerControls"),
  startPlayer: extractFunction("startPlayer"),
  pausePlayer: extractFunction("pausePlayer"),
};

function makeBtn(id) {
  const cls = new Set();
  const attrs = {};
  return {
    id,
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
  };
}

function makeRuntime(frames) {
  const playBtn = makeBtn("playBtn");
  playBtn.classList.add("active");   // templates/view_work.html:216 出厂就是 active
  const pauseBtn = makeBtn("pauseBtn");
  const raf = { nextId: 1, scheduled: 0, cancelled: 0 };
  const chip = { cleared: 0 };
  const body = [
    "var playBtn = __playBtn, pauseBtn = __pauseBtn;",
    "var frames = __frames, interval = 0.1, currentFrame = 0;",
    "var playing = false, rafId = null, playStartTs = 0;",
    "var IS_PIECE = true, AUDIO_URL = null, workAudio = null;",
    "function setRenderChip(t) { if (!t) __chip.cleared++; }",
    "function rafLoop() {}",
    "function requestAnimationFrame(fn) { __raf.scheduled++; return __raf.nextId++; }",
    "function cancelAnimationFrame(id) { __raf.cancelled++; }",
    "function performance() {}",
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
  const api = new Function("__frames", "__playBtn", "__pauseBtn", "__raf", "__chip", body)(frames, playBtn, pauseBtn, raf, chip);
  return { api, playBtn, pauseBtn, raf, chip };
}

const checks = [];
function check(name, ok, detail) { checks.push({ name, ok: !!ok, detail }); }

function snapshot(rt) {
  return {
    playing: rt.api.state().playing,
    playActive: rt.playBtn.classList.contains("active"),
    pauseActive: rt.pauseBtn.classList.contains("active"),
    playAria: rt.playBtn.getAttribute("aria-pressed"),
    pauseAria: rt.pauseBtn.getAttribute("aria-pressed"),
  };
}

// class 给眼睛、aria-pressed 给读屏器：两者必须是同一句话
function spokenAndSeenAgree(s) {
  return s.playActive === (s.playAria === "true")
    && s.pauseActive === (s.pauseAria === "true")
    && s.playAria !== s.pauseAria
    && s.playing === (s.playAria === "true");
}

// ── 1) 无素材态（frames.length < 2）──
{
  const rt = makeRuntime([]);
  rt.api.sync();                       // 首屏对齐（脚本尾部那次 syncPlayerControls()）
  const init = snapshot(rt);
  check("无素材首屏：Play 不亮、Pause 亮", init.playActive === false && init.pauseActive === true, init);
  check("无素材首屏：aria-pressed 说暂停", init.playAria === "false" && init.pauseAria === "true", init);
  check("无素材首屏：class 与 aria-pressed 一致", spokenAndSeenAgree(init), init);

  rt.api.pause();
  const afterPause = snapshot(rt);
  check("无素材点暂停：playing 仍是 false", afterPause.playing === false, afterPause);
  check("无素材点暂停：class 一字不动",
    afterPause.playActive === init.playActive && afterPause.pauseActive === init.pauseActive, { init, afterPause });
  check("无素材点暂停：aria-pressed 一字不动",
    afterPause.playAria === init.playAria && afterPause.pauseAria === init.pauseAria, { init, afterPause });
  check("无素材点暂停：不安排也不取消 rAF", rt.raf.scheduled === 0 && rt.raf.cancelled === 0, rt.raf);

  rt.api.start();
  const afterPlay = snapshot(rt);
  check("无素材点播放：同样被拦下", afterPlay.playing === false && afterPlay.playActive === false, afterPlay);
  check("无素材点播放：aria-pressed 仍说暂停", afterPlay.playAria === "false" && afterPlay.pauseAria === "true", afterPlay);

  const one = makeRuntime([["only one frame"]]);
  one.api.sync();
  one.api.start();
  check("单帧点播放：被拦下（不足两帧）", one.api.state().playing === false && one.raf.scheduled === 0, snapshot(one));
}

// ── 2) 有素材（frames.length >= 2）──
{
  const rt = makeRuntime([["a"], ["b"]]);
  rt.api.sync();
  rt.api.start();
  const playing = snapshot(rt);
  check("2 帧点播放：playing=true 且安排了一次 rAF", playing.playing === true && rt.raf.scheduled === 1, rt.raf);
  check("2 帧点播放：Play 亮、Pause 熄灭", playing.playActive === true && playing.pauseActive === false, playing);
  check("2 帧点播放：aria-pressed 说在播", playing.playAria === "true" && playing.pauseAria === "false", playing);
  check("2 帧点播放：class 与 aria-pressed 一致", spokenAndSeenAgree(playing), playing);

  rt.api.start();
  check("播放幂等：再点一次播放不多排 rAF", rt.raf.scheduled === 1, rt.raf);

  rt.api.pause();
  const paused = snapshot(rt);
  check("2 帧点暂停：取消了 rAF", rt.raf.cancelled === 1, rt.raf);
  check("2 帧点暂停：Play 熄灭、Pause 亮", paused.playActive === false && paused.pauseActive === true, paused);
  check("2 帧点暂停：aria-pressed 说暂停", paused.playAria === "false" && paused.pauseAria === "true", paused);
  check("2 帧点暂停：class 与 aria-pressed 一致", spokenAndSeenAgree(paused), paused);

  rt.api.pause();
  check("暂停幂等：再点一次暂停不重复取消 rAF", rt.raf.cancelled === 1, rt.raf);
}

const failed = checks.filter(c => !c.ok);
console.log(JSON.stringify({ checks, failed: failed.length }, null, 2));
if (failed.length) process.exit(1);
