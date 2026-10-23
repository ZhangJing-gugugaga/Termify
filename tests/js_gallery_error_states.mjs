// T48 — 画廊列表失败态 / 重试的行为测试（无浏览器、无 jsdom）。
//
// 为什么要有它：上一轮把"失败的列表请求"退化成 2 秒 toast，页面随后只剩
// 一片空网格；只有把 static/js/gallery_page.js 真的跑起来、真的喂它一个
// ok:false 的响应，才能断言 #galleryError 真的显形、#galleryErrorRetry
// 真的会再发一次请求、成功后又真的收回去。纯 grep 断言测不出这个。
//
// 做法：手写一个够用的 DOM stub（只实现 gallery_page.js 用到的那几个成员），
// fetch 用可编排的成功/failure 队列替换，脚本用 vm 在沙箱里跑。
//
// 输出：JSON {checks:[{name,ok,detail}], failed:n}；有失败时 exit 1。

import fs from "node:fs";
import vm from "node:vm";

const SRC_URL = new URL("../static/js/gallery_page.js", import.meta.url);
const source = fs.readFileSync(SRC_URL, "utf8");

function makeEl(id) {
  const listeners = {};
  const cls = new Set();
  const el = {
    id: id,
    hidden: false,
    textContent: "",
    dataset: {},
    children: [],
    _html: "",
    _attrs: {},
    style: {
      display: "",
      setProperty: function () {},
    },
    classList: {
      add: function (c) { cls.add(c); },
      remove: function (c) { cls.delete(c); },
      contains: function (c) { return cls.has(c); },
      toggle: function (c, on) {
        if (on === undefined) { if (cls.has(c)) { cls.delete(c); } else { cls.add(c); } }
        else if (on) { cls.add(c); } else { cls.delete(c); }
      },
    },
    addEventListener: function (t, fn) { (listeners[t] = listeners[t] || []).push(fn); },
    dispatch: function (t, ev) { (listeners[t] || []).forEach(function (fn) { fn(ev || { target: el }); }); },
    setAttribute: function (k, v) { el._attrs[k] = String(v); },
    getAttribute: function (k) { return k in el._attrs ? el._attrs[k] : null; },
    removeAttribute: function (k) { delete el._attrs[k]; },
    appendChild: function (c) { el.children.push(c); return c; },
    contains: function () { return false; },
    closest: function () { return null; },
    querySelector: function (sel) {
      if (sel === ".gallery-skeleton") return el._html.indexOf("gallery-skeleton") >= 0 ? {} : null;
      return null;
    },
    querySelectorAll: function () { return []; },
  };
  Object.defineProperty(el, "innerHTML", {
    get: function () { return el._html; },
    set: function (v) { el._html = String(v); },
  });
  return el;
}

const els = {};
function byId(id) {
  if (!els[id]) els[id] = makeEl(id);
  return els[id];
}

const document = {
  documentElement: makeEl("html"),
  body: makeEl("body"),
  getElementById: byId,
  querySelectorAll: function () { return []; },
  createElement: function (tag) { return makeEl(tag); },
  addEventListener: function () {},
};

const window_ = {
  location: { search: "", pathname: "/gallery" },
  history: { replaceState: function () {} },
  scrollTo: function () {},
};

// 可编排的响应队列：每次 fetch 吃掉一个，用完后一律 200 空列表。
const calls = [];
const plan = [
  { ok: false, status: 500 },
  { ok: false, status: 502 },
  { ok: true, status: 200, body: {
    total: 1, page: 1, limit: 24, has_more: false,
    items: [{
      id: "abc123", title: "Tethered", author: "wet",
      thumbnail_url: "/uploads/abc123.gif", like_count: 3, view_count: 9,
      params: { kind: "text" },
    }],
  } },
];
function fakeFetch(url) {
  calls.push(String(url));
  const next = plan.length ? plan.shift() : { ok: true, status: 200, body: { total: 0, page: 1, limit: 24, has_more: false, items: [] } };
  return Promise.resolve({
    ok: next.ok,
    status: next.status,
    json: function () { return Promise.resolve(next.body || {}); },
  });
}

const sandbox = {
  document: document,
  window: window_,
  history: window_.history,
  fetch: fakeFetch,
  URLSearchParams: URLSearchParams,
  setTimeout: setTimeout,
  clearTimeout: clearTimeout,
  console: console,
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: "static/js/gallery_page.js" });

async function settle() {
  for (let i = 0; i < 6; i++) await new Promise(function (r) { setImmediate(r); });
}

const checks = [];
function check(name, ok, detail) {
  checks.push({ name: name, ok: !!ok, detail: detail === undefined ? "" : String(detail) });
}

await settle();

const err = byId("galleryError");
const grid = byId("galleryGrid");
const empty = byId("galleryEmpty");
const pg = byId("galleryPagination");
const retry = byId("galleryErrorRetry");

// 1) 首次列表 500：错误块必须可见，且网格/空态/分页让位
check("request-1-sent", calls.length === 1, "calls=" + calls.length);
check("request-1-url", /\/api\/gallery\/list\?/.test(calls[0] || ""), calls[0]);
check("error-visible-on-500", err.hidden === false, "hidden=" + err.hidden);
check("grid-cleared-on-500", grid.innerHTML === "", JSON.stringify(grid.innerHTML.slice(0, 40)));
check("empty-hidden-on-500", empty.style.display === "none", empty.style.display);
check("pagination-cleared-on-500", pg.innerHTML === "", JSON.stringify(pg.innerHTML.slice(0, 40)));

// 2) 点重试 → 再发一次列表请求（这次 502，仍是失败态，错误块不消失）
retry.dispatch("click");
await settle();
check("retry-refetches", calls.length === 2, "calls=" + calls.length);
check("retry-url-is-list", /\/api\/gallery\/list\?/.test(calls[1] || ""), calls[1]);
check("error-still-visible-on-502", err.hidden === false, "hidden=" + err.hidden);

// 3) 再点一次 → 这次成功：错误块收回，卡片进网格
retry.dispatch("click");
await settle();
check("retry-refetches-again", calls.length === 3, "calls=" + calls.length);
check("error-hidden-on-success", err.hidden === true, "hidden=" + err.hidden);
check("grid-gets-cards", grid.innerHTML.indexOf("gallery-card") >= 0, JSON.stringify(grid.innerHTML.slice(0, 60)));

// 4) 失败不得再走"只弹 2 秒 toast"的老路
check("no-toast-on-failure-path", source.indexOf('toast("加载失败")') < 0, "old toast path removed");

const failed = checks.filter(function (c) { return !c.ok; }).length;
process.stdout.write(JSON.stringify({ checks: checks, failed: failed }) + "\n");
process.exit(failed ? 1 : 0);
