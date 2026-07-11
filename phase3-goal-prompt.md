# Termify Phase 3 Goal Prompt — Flask 联调

将 Phase 1 后端引擎 `termify/` 与 Phase 2 前端 `templates/`+`static/` 通过 Flask 接通，实现：上传 GIF → 选字符集 → 实时预览 → 下载 .py/.html 完整闭环。

项目根：`E:\Desktop\工作\观猹\Termify\`

## 已有资产（复用，禁止重写）

- `termify.convert(path, charset, w=80, h=24) -> FrameSequence`（字段：`lines_per_frame`, `interval`, `width`, `height`, `charset`）
- `termify.output.render(sequence, format) -> str`（format: `"python"`/`"html"`）
- `termify.charset.CHARSETS`：键为 `ascii`/`blocks`/`braille`/`geometric`/`binary`
- `tests/` 42 个测试全绿；`demo.py --charset all` 可端到端跑通
- `static/js/app.js`：IIFE，硬编码 `DEMO_FRAMES`(2帧)，下载按钮 toast `"Phase 3"`
- `ui-mockup.html`：视觉唯一真相源
- 依赖锁：`flask`/`pillow`/`pytest`，禁止引入新依赖

## SPEC 约束

- PRD §6 三端点：`POST /api/upload`(multipart, 返回 task_id+元数据) → `GET /api/preview/<task_id>?charset=&frame=`(返回 lines 数组) → `POST /api/generate`(JSON body, 返回 download_url) → `GET /api/download/<filename>`(返回文件)
- 文件上限 20MB；上传文件存 `uploads/`，输出存 `tmp/`（均 gitignored）
- 任务状态用内存字典+`threading.Lock`，无数据库
- 前后端分离：Flask 仅提供 API+静态资源，前端用 `fetch()` 通信
- 视觉对齐 `ui-mockup.html`，Phase 3 不改 CSS

## 第一轮：后端 API

创建 `app.py`，实现：

1. `GET /` → `render_template("index.html")`
2. `POST /api/upload` → 校验格式/大小 → 存 `uploads/{task_id}_{filename}` → 惰性导入 `from termify import convert` → 调用 convert 获取 FrameSequence → 存入 `_tasks[task_id]`(加锁) → 返回 `{task_id, frames_count, original_size, target_size}`
3. `GET /api/preview/<task_id>?charset=&frame=` → 白名单校验 charset → convert 取帧 → 返回 `{lines, charset, width, height, frame_count, interval}`
4. `POST /api/generate` → JSON body `{task_id, charset, format}` → convert + render → 写 `tmp/{task_id}_{charset}.{ext}` → 返回 `{download_url, file_size}`
5. `GET /api/download/<filename>` → `send_file("tmp/"+filename, as_attachment=True)`

**防错**：函数内部惰性导入避免循环引用；`try/finally` 确保文件句柄关闭；charset 白名单校验；`_tasks` 用 Lock 保护。

**验证**：`python app.py` 启动；4 条 curl 命令测试 upload/preview/generate/download 全返回正确 JSON；下载的 .py 可 `python xxx.py` 播放。

## 第二轮：前端集成

改造 `static/js/app.js`：

1. 删除 `DEMO_FRAMES`，新增 `state = {taskId, frames, interval, charset, totalFrames}`
2. 上传 → `fetch("/api/upload")` → 存 `state.taskId` → 请求第一帧预览
3. 风格卡片点击 → `fetch("/api/preview/"+taskId+"?charset="+newCharset)` → 更新 `state.frames` → `renderFrame(0)`。用递增 requestId 丢弃并发过期响应
4. 播放器：`state.frames` 作帧源，`state.interval*1000` 作间隔，进度条/帧计数器用真实 `totalFrames`
5. 下载按钮 → `fetch("/api/generate")` → `window.location=download_url` 触发下载。MCU 格式保留 toast "v2 即将支持"

**验证**：上传 GIF 后预览区显示真实帧（非硬编码猫）；切换字符集预览变化；下载 .py/.html 可执行；浏览器控制台无报错。

## 第三轮：联调与回归

1. 逐一验证：上传拖拽、5 风格卡片、播放/暂停/进度条、Python/HTML/MCU 格式切换、Tweaks 面板、分享按钮——全部与后端正确联动，下载按钮不再 toast "Phase 3"
2. 视觉对比 `ui-mockup.html` 与运行页面，无偏差
3. `pytest tests/ -v` 42 项全绿
4. `python demo.py --charset all` 正常输出 10 个文件
5. `app.js` 括号平衡（圆括号/花括号各自开闭一致）

## 完成标准

`python app.py` 启动 → 浏览器上传 GIF → 预览真实转换结果 → 切换 5 种字符集 → 下载可执行的 .py/.html → pytest 无回归 → 控制台零报错。
