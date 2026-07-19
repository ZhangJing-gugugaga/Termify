# Termify

> 万物皆可终端 —— 把任何 GIF / 图片转换成终端可播放的动画

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)

**核心价值**：把"我会做终端动画"这件事的门槛降到零。上传文件 → 点击风格卡片 → 下载可运行文件，**三步出活**，无需注册、无需安装、下载即走。

## 🌐 在线体验（零安装、直接玩）

| 入口 | 链接 | 说明 |
|------|------|------|
| **🔗 在线 Demo** | [https://termify.moonzj.com](https://termify.moonzj.com) | 直接上传 GIF/PNG/JPG，生成终端动画 |
| **🖼️ 作品画廊** | [https://termify.moonzj.com/gallery](https://termify.moonzj.com/gallery) | 浏览社区作品，查看别人的终端创作 |

> 💡 **不懂命令行？直接点上面链接** —— 浏览器拖图进去就能玩，零安装、零配置。\
> 📋 **想批量处理或离线用？** 继续看下面的本地安装指南。

![终端动画效果预览](images/terminal-preview.png)

## 快速体验（30 秒做出第一个动画）

```bash
# 1. 克隆 + 安装依赖（仅第一次）
git clone https://github.com/ZhangJing-gugugaga/Termify.git
cd Termify
pip install -r requirements.txt

# 2. 启动 Web 服务
python app.py
# 浏览器自动打开 http://127.0.0.1:5000
```

在浏览器里：
1. 把 GIF / PNG / JPG 拖到页面上（或点一下选择文件）
2. 点击卡片选择风格，预览区立即播放
3. 点"下载动画文件"，拿到 `.py` 或 `.html` 文件

下载后怎么用？

| 文件类型 | 打开方式 |
|---------|---------|
| `.py` 脚本 | 打开终端，运行 `python 你下载的文件.py`，按 Ctrl+C 停止 |
| `.html` 页面 | 双击即可在浏览器里播放，无需网络、无需安装 |

## 两种用法：Web vs 命令行

| 方式 | 适合谁 | 怎么开始 |
|------|--------|---------|
| **🌐 Web 界面**（推荐） | 所有人、想要实时预览 | `python app.py` → 浏览器访问 `http://127.0.0.1:5000` |
| **🖥 命令行** | 开发者、批量处理、无桌面环境 | `python demo.py 你的图片.gif --charset all` |

**推荐先用 Web 界面** — 它提供实时预览、风格切换、GIF 播放控制，比命令行友好得多。命令行适合跑在服务器上批量处理。

## Web 界面使用指南（详细）

### Step 01 · 上传素材

- 把 GIF / PNG / JPG **直接拖拽**到页面上的虚线区域
- 或者**点击上传区域选择文件**

支持格式：`.gif` / `.png` / `.jpg` / `.mp4` / `.webm` / `.mov` / `.avi` / `.mkv`，最大 **20MB**（视频最大 30 秒）。

> 💡 **静态图片（PNG/JPG）也可以上传！** Termify 会把它当作"只有一帧的动画"，输出的播放器会循环显示同一帧 —— 适合做"终端艺术字"。

### Step 02 · 选择渲染风格

点击 5 张风格卡片中的任意一张，预览区立即切换。试试不同风格 — 每次切换都在 100ms 内完成：

| 风格 | 字符 | 适合场景 | 颜色 |
|------|------|---------|------|
| **经典 ASCII** 灰度 | `@#%*+=-:.` | 复古感、极简、任何终端 | ❌ 灰度 |
| **Unicode 色块** | `█▀▄` + TrueColor | 最像原图、视觉冲击力 | ✅ 24-bit |
| **Braille 点阵** | `⠁⠂⠄⡀` | 高分辨率、科技感 | ❌ 灰度 |
| **几何图形** | `■●◆▪▫◇○` + 透明背景 | 设计感、现代，背景完全透明 | ❌ 灰度 |
| **极简二值** | `█ ` | 复古报纸印刷感 | ❌ 纯黑白 |

**我的第一张动画选什么？** 不确定就选 **Unicode 色块** —— 它的画质最接近原图，一眼就能看出效果。

### Step 03 · 预览 + 调整

- **Play / Pause 按钮** — 控制播放
- **点击进度条** — 跳转到任意帧
- **帧计数器** — 显示"当前帧 / 总帧数"

右下角有个 **⚙️ 齿轮按钮**（Tweaks 面板），可以开关背景网格、扫描线、主题色，以及自定义前景色/背景色。

### Step 04 · 选择输出格式

右侧面板里选择：

- **Python 脚本（.py）**：在终端播放，零依赖，按 Ctrl+C 停止。
- **HTML 页面（.html）**：浏览器打开即播放，更适合分享、手机查看。
- **嵌入式设备（v2 即将支持）**：Arduino / ESP32 代码。

### Step 05 · 选择终端尺寸

右下角"终端尺寸"区域（40×20 / 80×24 / 120×36 / 160×48 / 200×60）—— 直接在预览区点数字就可以切换。切换分辨率**只改变渲染精度**，预览窗口的位置和大小保持不变，如同视频播放器切画质。

```
   画质 ←→ 文件大小

   40×20   ████░░░░░░  最轻量、最粗糙
   80×24   ██████░░░░  默认值，预览体验好
   120×36  ████████░░  高清，细节清晰
   160×48  ██████████  超清（自动缩放以适应视口）
   200×60  ██████████  极致（自动缩放，文件较大）
```

> 💡 **选择建议**：不确定就选 **120×36** —— 画质和体积的甜点。选 160×48 或 200×60 时终端会自动缩小显示，**但最终输出的文件仍是全分辨率**。

### Step 06 · 下载

点击 **"下载动画文件"** 按钮，文件就保存到本地了。

## 命令行用法

```bash
# 转换单个图片
python demo.py my_cat.gif --charset ascii

# 生成全部 5 种字符集的输出（共 10 个文件）
python demo.py my_cat.gif --charset all

# 指定终端尺寸
python demo.py my_cat.gif --charset blocks --width 120 --height 36

# 指定输出目录
python demo.py my_cat.gif --charset all --out my_outputs
```

输出文件命名规则：`{图片名}_{字符集}.py` 和 `{图片名}_{字符集}.html`，生成在 `outputs/` 目录（或指定目录）。

## 终端全屏播放

下载的 `.py` 脚本在终端中运行时，会**自动适应终端窗口大小**：
- 动画等比缩放，始终居中显示
- 拖拽终端窗口边缘改变大小时，动画实时跟随重新缩放
- 无论终端是 80×24 还是 200×60，都能自动填满

**音频支持**：将 `music.mp3` 放在 `.py` 文件同目录下，播放器会自动检测并播放（使用系统自带音频工具，无需安装额外依赖）。

> 💡 推荐使用 **Windows Terminal** 或 **macOS Terminal** 以获得最佳 TrueColor 显示效果。Windows 旧版 cmd 也可能正常显示，播放器会自动启用 ANSI 支持。

## 画质优化贴士

- **想慢动作看细节？** 默认 80×24 适合快速预览；要更清晰选 **120×36** 或更大。
- **Unicode 色块（blocks）最像原图** —— 它承载 24 位真彩色，每个单元格展示上下两个像素。
- **Braille 点阵** 每个字符覆盖 2×4 像素，但**视觉面积较小**，强烈建议配合 160×48 或 200×60 大尺寸。
- **高分辨率 = 大文件**。200×60 blocks 单帧约 60KB，100 帧 GIF 输出约 6MB。下载时间略长，但画质最好。
- **终端里看乱码？** 默认绿色来自 ANSI 转义 — 部分 Windows 旧版终端不支持，请改用 HTML 输出格式。
- **分辨率与终端尺寸**：高分辨率（如 200×60）需要更大的终端窗口才能完整显示。如果终端不够大，Python 播放器会自动等比缩放以适应当前窗口——源分辨率越高，缩放后的细节越丰富。你也可以手动放大终端窗口（PowerShell 右上角拖拽边缘，或修改字体大小）来获得最佳效果。

## 常见问题

**Q: 我没有编程经验，能玩吗？**
A: 完全可以。启动 `python app.py` → 浏览器里拖图片 → 点卡片 → 点下载，全程不用写一行代码。

**Q: 下载的 .py 文件怎么跑？**
A:
- **Windows**：打开 PowerShell（Win+R 输入 `powershell`），进入下载目录，运行 `python 文件名.py`。如果提示"python 不存在"，请安装 [Python 3.10+](https://www.python.org/downloads/)。
- **macOS / Linux**：打开 Terminal，进入下载目录，运行 `python3 文件名.py`。

**Q: 为什么终端里显示绿色 / 乱码 / 方框？**
A: 这通常有两个原因：

1. **终端不支持 TrueColor ANSI**（影响 blocks 风格）：
   - 改用 **HTML 输出格式**（推荐），浏览器打开就正常
   - 或换用 **ASCII/几何图形/二值** 等非彩色风格

2. **终端字体不支持 Unicode 字符**（影响 geometric/braille 风格）：
   - 使用 **Windows Terminal**（已内置 Unicode 支持）
   - 安装 Nerd Font 字体（如 [JetBrainsMono Nerd Font](https://www.nerdfonts.com/)），然后在终端设置中选用
   - 改用 **HTML 输出格式**，浏览器对 Unicode 支持最好

> 💡 **推荐配置**：Windows Terminal + JetBrainsMono Nerd Font 字体，可完美显示所有风格。

**Q: 手机能打开 .html 文件吗？**
A: 可以。通过微信/AirDrop/数据线传到手机，用浏览器打开；**横屏**观看效果更好。

**Q: 下载的文件多大？**
A: 取决于尺寸和帧数：
- 80×24 ascii 10 帧 → ~2 KB
- 120×36 blocks 24 帧 → ~200 KB
- 200×60 blocks 100 帧 → ~6 MB

**Q: 可以上传视频吗？**
A: 支持 📹！上传 MP4 / WEBM / MOV / AVI / MKV（最大 30 秒、20MB），后端 ffmpeg 自动抽帧转换成动画。也支持拖入 `.gif` / `.png` / `.jpg`。超过 30 秒的视频先截取片段再上传。

**Q: 200×60 超清点不了 / 播放卡怎么办？**
A: 超清输出会自动缩放以适应视口，**下载的文件仍是全分辨率**。播放卡顿可以换浏览器（Chrome 最快）；或选小一号尺寸。

**Q: 支持批量上传吗？**
A: 支持。Web 界面现在可以一次拖拽或多个选择多个文件（或 Ctrl/Cmd + 点击多选），每个文件独立处理。命令行批量仍可用 `python demo.py 文件 --charset all`。

**Q: 错误 `ModuleNotFoundError` 或 `python 不是内部命令`？**
A: Python 未安装或未加入 PATH。请安装 Python 3.10+ 并在安装时勾选"Add Python to PATH"。

## API 文档

如果你想用程序调用 Termify（比如集成到其他项目），提供 5 个接口：

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | 主页 |
| `POST` | `/api/upload` | 上传单个文件（multipart/form-data），返回 `task_id` + 元数据 |
| `POST` | `/api/upload-batch` | 批量上传多个文件（multipart，字段名 `files[]`），返回 `task_ids[]` + `errors[]` |
| `POST` | `/api/fetch-url` | 从 URL 下载图片并转换（`{"url":"..."}`），含 SSRF 防护 |
| `POST` | `/api/upload-video` | 上传视频（MP4/WEBM/MOV/AVI/MKV），后端 ffmpeg 抽帧后转换，限 30s / 20MB |
| `GET` | `/api/preview/<task_id>` | 获取帧数据。参数：`charset`（风格）、`width`、`height`、`frame`（某帧）、`fg`/`bg`（颜色，形如 `rgb(255,0,0)`）。不传 `frame` 返回全部帧。 |
| `POST` | `/api/generate` | 打包指定字符集+格式，返回 `download_url` |
| `GET` | `/api/download/<filename>` | 下载生成的文件 |

### 示例

```bash
# 上传文件
curl -X POST http://127.0.0.1:5000/api/upload -F "file=@cat.gif"
# 返回: {"task_id": "abc123", "frames_count": 24, ...}

# 获取第一帧（blocks 风格）
curl "http://127.0.0.1:5000/api/preview/abc123?charset=blocks&frame=0"

# 生成带自定义颜色的 Python 输出
curl -X POST http://127.0.0.1:5000/api/generate \
  -H "Content-Type: application/json" \
  -d '{"task_id":"abc123","charset":"ascii","format":"python","fg":"rgb(255,176,0)"}'

# 下载
curl -O http://127.0.0.1:5000/api/download/abc123_ascii.py

# 批量上传（多文件）
curl -X POST http://127.0.0.1:5000/api/upload-batch \
  -F "files=@cat.gif" -F "files=@dog.png"

# URL 直输
curl -X POST http://127.0.0.1:5000/api/fetch-url \
  -H "Content-Type: application/json" \
  -d '{"url":"https://example.com/image.gif"}'

# 视频上传（后端 ffmpeg 抽帧）
curl -X POST http://127.0.0.1:5000/api/upload-video -F "file=@clip.mp4"
```

## 桌面客户端（一键独立包）

不想装 Python？下载打包好的独立 `.exe` 文件，**双击即用**。启动后会自动打开浏览器，访问本地 Web 界面（跟在线 Demo 功能完全一致）。

> 📦 源码仓库根目录含 `termify.spec` + `termify_launcher.py` + `deploy.sh`，可在任意平台自行构建。

### Windows 用户

从 [GitHub Releases](https://github.com/ZhangJing-gugugaga/Termify/releases) 下载最新的 `Termify-<version>.zip`，解压后：

```
Termify/
├── Termify.exe    # 双击启动！
├── _internal/     # 运行时依赖（不要动这个文件夹）
└── ...
```

> ⚠️ 首次双击时 Windows 可能弹出"已保护你的电脑"。这是正常现象（自签名未认证），点击"更多信息"→"仍要运行"即可。

### macOS / Ubuntu / Fedora 用户

```bash
# 1. 从源码构建桌面包
git clone https://github.com/ZhangJing-gugugaga/Termify.git
cd Termify
pip install pyinstaller

# 2. 一键构建
pyinstaller termify.spec --clean --noconfirm

# 3. 启动
dist/Termify/Termify   # 双击或在终端中打开
```

> 🍎 macOS 用户如果遇到"无法验证开发者"，打开终端运行 `./dist/Termify/Termify` 即可。

### 桌面包 vs 在线 Demo 对比

| 场景 | 选哪个 |
|------|--------|
| 只是想试一下 | **在线 Demo**（零安装） |
| 离线环境 / 懒得连网 | **桌面包**（本地运行） |
| 源图太大（>20MB） | **桌面包**（可以绕过在线 20MB 上限） |
| 想要最新功能 | **在线 Demo**（自动随 main 分支更新） |

> 💡 桌面包启动后跟在线 Demo 完全一致 —— 上传、选风格、预览、下载 `.py/.html`，**所有产物都在你机器上**。


## 项目结构

```
Termify/
├── app.py                  # Flask 入口（路由 + 内存任务存储）
├── demo.py                 # CLI 冒烟测试
├── requirements.txt        # flask / pillow / pytest
├── termify/                # 后端转换引擎（纯 Python 库）
│   ├── charset.py          # 5 种字符集 + 像素→字符映射
│   ├── frames.py           # GIF 抽帧 + 等比缩放
│   ├── engine.py           # convert() → FrameSequence
│   ├── ansi_to_html.py     # ANSI → HTML 颜色转换
│   ├── taskstore.py        # SQLite 任务存储（多 worker 共享）
│   ├── gallery.py          # 画廊功能（SQLite 元数据 + 缩略图生成）
│   ├── share.py            # 作品分享（.termify + URL 编码）
│   ├── video.py            # 视频接入（ffmpeg 抽帧）
│   ├── urlfetch.py         # URL 直输（SSRF 防护下载）
│   └── output/
│       ├── python.py       # 生成 .py 播放脚本
│       └── html.py         # 生成 .html 播放页
├── templates/index.html    # 前端页面（Jinja2 模板）
├── static/
│   ├── css/{tokens,app}.css
│   └── js/app.js           # 前端逻辑
├── tests/                  # pytest 单元测试（217 tests）
├── ui-mockup.html          # UI 视觉唯一真相源
└── README.md               # 本文件
```

## 技术栈

- **后端**：Python 3.10+、Flask、Pillow
- **前端**：原生 HTML/CSS/JS，无框架依赖
- **测试**：pytest（217 tests，运行 `pytest -q` 即可）
- **主题**：暗色终端美学，JetBrains Mono + Space Grotesk 字体

## 🐛 反馈与 ISSUE

使用过程中遇到任何问题，欢迎提 [GitHub Issue](https://github.com/ZhangJing-gugugaga/Termify/issues/new/choose)。

### Issue 提交流程

| 步骤 | 说明 |
|------|------|
| 1. **搜索已有 Issue** | 确认没人报过同样的问题 → 避免重复 |
| 2. **选择模板** | Bug Report / Feature Request / 问题求助，选最匹配的 |
| 3. **填够信息** | 见下方模板 |

### 🐞 Bug Report 模板

```markdown
### 版本信息
- Termify 来源: 在线 Demo / 桌面包 / 本地 git clone
- 操作系统: Windows 11 / macOS 14 / Ubuntu 22.04
- 浏览器: Chrome 126 / Firefox 128 / Safari 17
- Python 版本（如适用）: 3.13.2

### 重现步骤
1. 打开 https://termify.moonzj.com（或桌面包）
2. 上传 [具体文件，最好附截图或链接]
3. 选择 [风格名 + 尺寸]
4. 点 [具体按钮]
5. 看到 [期望 vs 实际]

### 期望行为
清晰的一句话。

### 实际行为
附上截图、报错原文（不要截取部分）、浏览器控制台输出（F12 → Console）。
```

### 💡 Feature Request 模板

```markdown
### 场景描述
我遇到的问题是……（一句话说清真实场景）

### 期望方案
我希望 Termify 提供 [具体功能] —— 它应该 [做什么]、[对用户有什么好处]

### 替代方案
A. 我先用 [workaround] 工作，但不够好
B. 也可以 [备选方案]，但代价是……
```

> 💡 **小技巧**：附带一张截图或 5 秒 GIF 演示你遇到的问题，维护者能更快理解。

### 社区讨论与问答

- **Discussions**：看 Issue 列表旁边有个 [Discussions](https://github.com/ZhangJing-gugugaga/Termify/discussions) 标签，适合"怎么用 / 做了什么 / 我有个想法但还没想清楚"类问题
- **提交前 Checklist**：
  - [ ] 已经读过 [FAQ](#常见问题) + [画质优化贴士](#画质优化贴士)
  - [ ] 已搜索过已有 [Issue](https://github.com/ZhangJing-gugugaga/Termify/issues?q=is%3Aissue)
  - [ ] 已注明 Termify 来源（在线 / 桌面 / 本地）

## 参与贡献

欢迎 Pull Request！无论你是修一个 typo 还是加一个新字符集。

### 开发流程

```bash
# 1. Fork + clone 仓库
git clone https://github.com/<你的用户名>/Termify.git
cd Termify

# 2. 新建分支（分支名为 type/scope，如 fix/typo 或 feat/new-charset）
git checkout -b feat/your-feature

# 3. 改代码 + 跑全量测试
pip install -r requirements.txt
pytest -q                    # 基线 217 tests，必须全绿
```

### 代码规范

| 约定 | 说明 |
|------|------|
| **分支命名** | `type/scope`，如 `feat/new-charset` / `fix/memory-leak` / `docs/readme` / `test/regression` |
| **Commit 消息** | [Conventional Commits](https://www.conventionalcommits.org/) 中文，如 `feat(charset): 新增 emoji 字符集` |
| **PR 标题** | 同 commit 格式，注明关闭哪个 Issue（如 `Closes #42`） |
| **测试** | 新功能必须在 `tests/` 里补对应的 pytest（`git add -f` 因为 .gitignore 会吞 `test_*.py`） |
| **不破主分支** | main 受保护，只在 feature/fix 分支开发，PR 合并后不直接推 main |

### 当前技术栈

- Python 3.10+, Flask, Pillow
- 前端原生 HTML/CSS/JS，无框架依赖（Jinja2 模板）
- pytests（**217** tests，跑 `pytest -q`）
- PyInstaller 做桌面包

### 我能贡献什么？

| 技能 | 能做什么 |
|------|---------|
| Python | 新增字符集、优化转换速度、修 bug |
| 前端 | 优化 Web UI、加动画效果、改善无障碍 |
| 设计 | UI/UX 改版、Logo 重塑、网站样式 |
| 文档 | 翻译 README、写教程、修拼写错 |
| 测试 | 补端到端测试、性能回退测试 |
| 运维 | 改进部署脚本、加 CI/CD、Docker 化 |

> 💬 **不确定从哪开始？** 找标签 `good first issue` 的 Issue，或到 [Discussions](https://github.com/ZhangJing-gugugaga/Termify/discussions) 开一个帖子问。

遇到问题？先查 [FAQ](#常见问题) → 再开 [Issue](https://github.com/ZhangJing-gugugaga/Termify/issues/new/choose)。
