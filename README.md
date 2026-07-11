# Termify

> 万物皆可终端 —— 把任何 GIF、图片转换成终端可播放的动画

## What is this?

Termify 是一个把 GIF / PNG / JPG 转成终端可播放动画的 Web 工具。上传文件 → 选渲染风格 → 下载一个能在终端直接运行的 `.py` 脚本或自包含的 `.html` 播放页。无需注册、无需数据库、下载即用。

状态：**MVP 三阶段全部完成**（后端引擎 → 前端 UI → Flask 联调接通），可正常运行。

## Features (MVP)

- 支持 GIF（多帧动画）/ PNG / JPG（静态图）上传
- 5 种渲染风格：
  - 经典 ASCII（`@#%*+=-:.`，灰度密度）
  - Unicode 色块 + TrueColor 24 位 ANSI（`█▀▄`）
  - Braille 点阵（`⠁⠂⠄⡀`，2×4 像素 → 一个码点）
  - 几何图形（`■□▪▫`，8 级灰度）
  - 极简二值（`█ `，阈值化）
- 2 种输出格式：
  - Python 终端播放脚本（嵌入帧数据 + `time.sleep` 循环）
  - 自包含 HTML 播放页（无外部依赖、无 CDN）
- 实时预览：切换字符集 / 终端尺寸立刻刷新
- 终端尺寸可选 40×20 / 80×24 / 120×36

## Tech Stack

- 后端：Python 3.10+、Flask、Pillow（无 ORM、无数据库）
- 前端：原生 HTML/CSS/JS（无框架），暗色 ANSI 终端主题
- 上传文件仅存于临时目录（`uploads/`、`tmp/`，gitignore），处理完即弃

## Quick Start

```bash
# 1. 安装依赖
pip install flask pillow

# 2. 启动服务
python app.py
# 打开 http://127.0.0.1:5000

# 3. 跑测试
pytest tests/            # 42 个单测，全绿

# 4. CLI 端到端冒烟（使用内置示例 GIF）
python demo.py --charset all    # 在 outputs/ 产出 5 套 .py + .html
```

## API

| 方法 | 路径 | 说明 |
|------|------|------|
| `POST` | `/api/upload` | 上传文件（multipart），返回 `task_id` + 元数据 |
| `GET` | `/api/preview/<task_id>?charset=&frame=&width=&height=` | 渲染帧数据（JSON `lines` / `frames`） |
| `POST` | `/api/generate` | 打包指定字符集+格式，返回 `download_url` |
| `GET` | `/api/download/<filename>` | 下载生成的 `.py` / `.html` 文件 |
| `GET` | `/` | 主页 |

上传限制 20MB（`MAX_CONTENT_LENGTH`）；处理超时 30s。

## Project Structure

```
Termify/
├── app.py                  # Flask 入口：路由 + 内存任务存储
├── demo.py                 # CLI 端到端冒烟
├── requirements.txt        # flask / pillow / pytest
├── termify/                # Phase 1 后端转换引擎（纯 Python 库）
│   ├── charset.py          # 5 种字符集定义 + 像素→字符映射
│   ├── frames.py           # GIF 抽帧 + 等比缩放（20MB 上限）
│   ├── engine.py           # convert(path, charset, w, h) → FrameSequence
│   └── output/
│       ├── python.py       # 生成自包含 .py 播放脚本
│       └── html.py         # 生成自包含 .html 播放页
├── templates/index.html    # Jinja2 模板（url_for 静态资源）
├── static/
│   ├── css/{tokens,app}.css
│   └── js/app.js           # 前端逻辑（IIFE，API 驱动）
├── tools/
│   └── build_frontend.py   # 把 ui-mockup.html 拆分为 templates/ + static/
├── tests/                  # pytest 单测（42 tests）
├── ui-mockup.html          # 视觉唯一真相源
├── CLAUDE.md               # 项目说明（给 Claude Code 会话用）
└── README.md               # 本文件
```

`uploads/`、`tmp/`、`outputs/` 为运行时临时目录（gitignore），按需生成。

## License

MIT
