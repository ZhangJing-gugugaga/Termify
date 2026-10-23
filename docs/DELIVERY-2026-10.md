# Termify 整改交付（2026-10-23）

本文件记录一轮「深化测试 → 逐条整改 → 只读复测」的交付结果：21 个提交、29 条清单中 27 条明确落地，UX-9 以症状消解处理（口径见 §3.1），UX-11 / UX-13 判定不改，另含 1 条复测中发现并修掉的既有缺陷。

**范围声明**：全部提交只推到 `origin/main`，**未部署上线**（`git log` 顶部 `b7cec1b`）。清单原文（含 PF/S/UX 编号的完整描述）在两轮上下文压缩中丢失，本表按提交内容与三份复测报告反推归属，反推项标 †，凡与实际原文冲突以原文为准。

---

## 1. 提交链

| 提交 | 主题 | 关闭条目 |
| --- | --- | --- |
| `80c8c68` | 405 页 + JSON 分离、服务端路径洗涤、文字作品 embed-frames 404 | S-4, S-5 |
| `a6367b9` | flask-compress 开启 gzip/br | PF-3 |
| `ef50a18` | 指纹化静态资源 immutable 缓存 + woff2 字体 | PF-6 |
| `959f5d1` | preview / embed 帧缓存、条件请求与体积闸门 | PF-2, PF-5 |
| `a24f013` | 上传前置校验（体积/格式） | UX-7 |
| `2cd42b2` | 风格卡改键盘可操作的单选组 | UX-2 |
| `185e151` | 弹层焦点陷阱、Esc、焦点归还；静帧占位 -- | UX-3, UX-5, UX-6, S-7 |
| `02862b2` | 上传状态反馈（可见 .uploading + live region + 可重试） | UX-1 |
| `bc90481` / `00fa0c5` | 导出按钮单飞（首页 + 工作室页）、结果反馈 | UX-4 |
| `1ad5654` | 字体墙复用 DOM 不再重复请求；text-art 触摸目标 | UX-12 |
| `1c74d32` | 前端六项：40px 触摸目标、375 预览裁字、窄屏 nav 图标、tweaks 去重、静帧 -- | UX-8, UX-10 †, S-6 |
| `4e06cb4` | >1MiB 响应跳过压缩（identity）＋ 字体 ?v= 进 immutable | PF-3 补, PF-6 补 |
| `996ca01` | 源帧逐帧下发 + manifest（首帧不再被末帧绑死） | PF-1, PF-4 |
| `8682d13` | 列表响应剔除内联 params.frames | PF-8（顺带 UX-9 †） |
| `24a3c22` | 覆盖 PF-8 / S-1 / S-2 的回归测试 | 测试 |
| `b176da6` | 画廊骨架对齐真卡（CLS 0）+ /text-art 首屏瘦身 | PF-9 |
| `a6b2bb9` | 三处管理口令绕过口并入统一失败计数与退避 | S-1 补 |
| `48d5d92` | /text-art 结果行与字体墙首屏即真内容；manifest 短缓存 | /text-art CLS |
| `b59d3ab` | CSP 去掉 script-src 'unsafe-inline'，5 段内联脚本外置 | S-3 |
| `b7cec1b` | /v/ 换尺寸时渲染缓存保持 {map,used} 形状（复测发现的崩溃） | 缺陷修复 |

S-2（写接口同源校验）随 `4e06cb4` 进场 —— 该提交本应只含压缩闸门，误带入另一车道未提交的 `_write_origin_guard` 与口令失败计数代码，已事后补测并补测试（`24a3c22`）。

---

## 2. 关键实测（改前 → 改后）

### 2.1 传输与体积
| 指标 | 改前 | 改后 |
| --- | --- | --- |
| 首页 HTML | 45,015B | gzip 9,814B / br 8,600B；S-3 后 41,279B |
| app.css | 107,905B | gzip 16,788B / br 14,123–15,868B |
| app.js | 96,863B | gzip 29,307B / br 26,123B |
| 字体 | JetBrainsMono-VF.ttf 300,144B | woff2 113,580B（−62.2%，可变轴与 1754 字形保留） |
| 列表 `/api/gallery/list?limit=24` | 31,871B | 14,101B（`limit=60` 53,983→36,213B，响应体无 frames） |
| 静态缓存 | 全部走 werkzeug 默认 no-cache | 带 `?v=` → `public, max-age=31536000, immutable`；裸 URL → 86400；HTML → no-cache；`/gallery/file/*` 不误伤 |

### 2.2 视频页首帧（PF-1 / PF-4）
| 指标 | 改前 | 改后 |
| --- | --- | --- |
| 首帧可见 | 7,483ms（br 压缩路径下更差，最差 31.29s） | 407–561ms（缓存热 162ms） |
| 帧获取方式 | 单 JSON 内联 514 帧，首帧被末帧到达绑死 | `manifest`（80B）+ `frame/<i>.jpg`（14,132B/帧，immutable + ETag，命中 304） |
| legacy 整包 `source-frames` | br 全量压缩，TTFB 27,654ms | identity，TTFB 1.96–2.96s（12,133,260B 一次性传输，逐字节不变） |
| 主线程长任务 | 7 个 / 最长 366ms | 2 个 / 最长 325ms（补齐 514 帧后仍有 8–9s 才开播，全帧解码后 JS 堆约 103MB） |

### 2.3 预览与嵌入（PF-2 / PF-5）
| 用例 | 改前 | 改后 |
| --- | --- | --- |
| preview blocks 200×60 | 29,362,233B / 2,552ms | 2,657,712B / 275.8ms |
| preview braille 400×400 | 4,430,551B / 5,226ms | 295,100B / 350.4ms |
| embed 默认（514 帧） | 6,230,254B / 9,026ms | 2,109,114B / 2,877.5ms（kept 174/514） |
| 常规尺寸 6 例（80×24 等） | — | 逐字节不变 |

闸门：`PREVIEW_MAX_ROWS = 120`（blocks→60 行、braille→30 行，只截高度保比例）、`PREVIEW_BODY_BUDGET = 3 MiB`；响应头披露 `X-Preview-Clamped` / `X-Embed-Clamped` / `X-Embed-Frames: kept:N;total:M`。

### 2.4 稳定性与感知（PF-9 / UX）
| 指标 | 改前 | 改后 |
| --- | --- | --- |
| /gallery CLS | 0.2319 | 0（1440 与 375） |
| 热门排序（`?sort=hot`） | 526ms | 20–25ms（payload 瘦身的连带效果，未加索引） |
| /text-art 首屏 JS | 51,159B | 37,743B decoded（−26.2%），字体墙与图片艺术化按需注入 |
| /text-art 首屏 docH | 1451（1440） | 1464，且生成/点卡两步位移为 0 |
| 上传 21MiB 或 .txt | 发出请求后才失败 | 0 请求，<2ms 出文案 |
| 导出连点 5 次 + Enter | 多重提交 | 1 次请求（首页与工作室页同构） |
| 可见触摸目标 | 若干 <40px | 30/30 ≥40px（/ 与 /gallery 全量扫描） |

### 2.5 安全
| 条目 | 结果 |
| --- | --- |
| S-1 口令失败退避 | 三处绕过口（`GET work` / `DELETE work` / 私有原作直链）并入统一计数：前 9 次 403 → 第 10 次起 429 + `Retry-After: 900`，锁定期正确口令同样 429；不带头部不计失败（防访客自锁） |
| S-2 写接口同源 | 跨站 Origin → 403；同源 200；无 Origin/Referer/Sec-Fetch-Site 时降级放行（仓库既有 pytest 全用不带 Origin 的 test_client，缺失即 403 会全红） |
| S-3 CSP | `default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; media-src 'self' data: blob:; connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'`；6 路由同头、8 次加载 violation 0 |
| S-4 / S-5 | 405 与部分 404 改自有页面；文字作品 `embed-frames` 由 400 → 404；错误页洗涤服务端路径 |
| S-7 | 静帧作品 `0.0s/0.0s` → `--` |

---

## 3. 未完成与刻意不做

### 3.1 编号口径
- UX-11（版面取舍）与 UX-13（Chrome 粘滞误报）经判定**不改**，属设计取舍与浏览器误报，不是缺陷。
- UX-9 †：原始描述已丢；症状「热门排序慢」实测已由 PF-8 列表瘦身消解（526ms→20ms），未新增索引。若原文另指他处，需你补一句描述再立。
- UX-10 †：随 `1c74d32` 前端六项同批交付（375 预览裁字、窄屏 nav、tweaks 双重绑定、静帧 -- 占位）。

### 3.2 已知限制（接受现状，不再排期）
1. `embed-frames` 与 legacy `source-frames` 仍是整包（12.1 MiB）。分帧化要改消费端，收益低于风险；legacy 端点保持逐字节兼容。
2. 文字作品详情页仍内联 `params.frames`（详情页预填需要）。
3. `/gallery` 骨架卡 0ms 即可见（CLS 已为 0，骨架与真卡同高）。
4. `style-src` 仍留 `'unsafe-inline'`：模板里有内联 style 属性（`/` 页 13 处），收紧需先全量迁移到 class。样式注入风险远低于脚本注入。
5. `static/js/termify-render.js` 无 `defer`：标签位于 body 末尾（`templates/index.html:501`、`templates/view_work.html:339`），parser 阻塞成本可忽略，实测首帧无差异。

### 3.3 未做（无授权）
改 `termify/frames.py:75`、`termify/engine.py:84`、`termify/video.py:379` 做冷启动 decode/render 提速 —— profile 显示缩放只占 3–16%，主因是帧数 × decode/render，且 `Image.draft()` 只对 JPEG 生效（本仓库作品帧是 PNG）。

---

## 4. 验证方法与可复现命令

- 单元/集成：`.venv/Scripts/python.exe -m pytest -q` → 568 passed / 17 skipped（`b59d3ab` 上的全量结果）。`b7cec1b` 只新增 3 条静态断言，未重跑全量，定向子集 `t47 + t40 + t46 + t33 + t34` = 69 passed。
- 浏览器态：headless Chrome + CDP（单实例，`try/finally` 关浏览器并删 profile），采集 `securitypolicyviolation`、`error`、`unhandledrejection`、CLS、长任务与网络计时。
- 三份只读复测报告：`Temp/termify-mcp/verify3|verify4|verify5/report.md`（verify5 = `b59d3ab`，12 项全 PASS、regressed 0）。
- 已知坑：同进程跑多个测试文件时 `tests/test_t7_web_api.py` 的 `monkeypatch.chdir` 会波及 `/api/download` 的用例，出现假 404，单跑该文件即过。

## 5. 部署注意（尚未执行）

1. `requirements.txt` 新增 `flask-compress`，部署前必须 `pip install -r requirements.txt`；测试/运行实例统一用 `E:/Desktop/work/WetCha/Termify/.venv/Scripts/python.exe`（系统 python 缺该包，import 直接 ModuleNotFoundError）。
2. 静态资源版本串 `?v=20261026a` 已写进模板与 CSS，改前端资源需同步递增。
3. 服务端改动集中在 `app.py`（压缩闸门、缓存策略、分帧端点、CSP、鉴权退避、同源守卫、列表瘦身、体积闸门），部署后建议复跑 `verify5` 的 12 项指标做上线前对照。
