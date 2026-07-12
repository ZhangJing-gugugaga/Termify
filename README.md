# Termify

> 万物皆可终端 —— 把任何 GIF / 图片转换成终端可播放的动画

## 这是什么

Termify 是一个开源 Web 工具。上传一张 GIF 动图或图片，选择喜欢的字符渲染风格，下载一个能在终端直接运行的 `.py` 脚本或自包含的 `.html` 播放页。无需注册、无需安装、下载即走。

**核心价值**：把"我会做终端动画"这件事的门槛降到零。

## 功能一览

- 支持 GIF（多帧动画）、PNG、JPG 上传，最大 20MB
- 5 种渲染风格：
  - **经典 ASCII 灰度**（`@#%*+=-:.`）—— 最复古，任何终端都能显示
  - **Unicode 色块**（`█▀▄` + TrueColor 24 位 ANSI）—— 视觉冲击力最强
  - **Braille 点阵**（`⠁⠂⠄⡀`）—— 高分辨率，科技感
  - **几何图形**（`■□▪▫●○◆◇`）—— 现代设计感
  - **极简二值**（`█ `）—— 纯黑白，复古印刷感
- 2 种输出格式：
  - **Python 脚本**：`python play.py` 直接在终端播放动画
  - **HTML 页面**：浏览器打开即播放，零依赖
- 实时预览：切换字符集或终端尺寸后即时刷新
- 终端尺寸可选 40×20 / 80×24 / 120×36

## 快速开始

### 环境要求

- Python 3.10+
- pip

### 安装与运行

```bash
# 1. 克隆仓库
git clone https://github.com/ZhangJing-gugugaga/Termify.git
cd Termify

# 2. 安装依赖
pip install -r requirements.txt

# 3. 启动 Web 服务
python app.py
# 浏览器打开 http://127.0.0.1:5000

# 4. 运行测试（可选）
pytest tests/

# 5. 命令行冒烟测试（可选）
python demo.py your_image.gif --charset all
# 会在 outputs/ 目录生成 5 套 .py + .html 文件
```

## 使用教程

### Web 界面（推荐）

1. **打开页面**：启动 `python app.py` 后，浏览器访问 `http://127.0.0.1:5000`

2. **上传文件**（Step 01）：拖拽 GIF/PNG/JPG 到上传区域，或点击选择文件。上传完成后页面自动滚动到预览区

3. **选择渲染风格**（Step 02）：点击 5 张风格卡片中的任意一张，预览区立即更新为对应字符集的渲染效果

4. **预览动画**（Step 03）：预览区以终端窗口样式展示动画播放，可以：
   - 点击 Play/Pause 按钮控制播放
   - 点击进度条跳转到指定帧
   - 查看当前帧 / 总帧数

5. **选择输出格式**：在右侧面板选择 Python 脚本或 HTML 页面

6. **选择终端尺寸**（可选）：40×20 / 80×24 / 120×36，切换后预览自动刷新

7. **下载**：点击"下载动画文件"按钮，获得 `.py` 或 `.html` 文件

8. **运行下载的文件**：
   - Python 脚本：`python cat_ascii.py`（终端中播放动画）
   - HTML 页面：双击 `.html` 文件，浏览器中直接播放

### 命令行（CLI）

```bash
# 转换单个图片，使用 ASCII 字符集
python demo.py my_cat.gif --charset ascii

# 转换并生成全部 5 种字符集的输出
python demo.py my_cat.gif --charset all

# 指定终端尺寸
python demo.py my_cat.gif --charset blocks --width 120 --height 36

# 指定输出目录
python demo.py my_cat.gif --charset all --out my_outputs
```

输出文件命名规则：`{图片名}_{字符集}.py` 和 `{图片名}_{字符集}.html`，生成在 `outputs/` 目录（或指定目录）下。

## API 文档

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | 主页 |
| `POST` | `/api/upload` | 上传文件（multipart/form-data），返回 `task_id` + 元数据 |
| `GET` | `/api/preview/<task_id>?charset=&frame=&width=&height=` | 获取帧数据；不传 `frame` 返回全部帧 |
| `POST` | `/api/generate` | 打包指定字符集+格式，返回 `download_url` |
| `GET` | `/api/download/<filename>` | 下载生成的文件 |

### API 示例

```bash
# 上传
curl -X POST http://127.0.0.1:5000/api/upload -F "file=@cat.gif"
# 返回: {"task_id": "abc123", "frames_count": 24, ...}

# 预览第一帧
curl "http://127.0.0.1:5000/api/preview/abc123?charset=ascii&frame=0"
# 返回: {"lines": [...], "charset": "ascii", ...}

# 生成 Python 输出
curl -X POST http://127.0.0.1:5000/api/generate \
  -H "Content-Type: application/json" \
  -d '{"task_id":"abc123","charset":"ascii","format":"python"}'
# 返回: {"download_url": "/api/download/abc123_ascii.py", ...}

# 下载
curl -O http://127.0.0.1:5000/api/download/abc123_ascii.py
```

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
│   └── output/
│       ├── python.py       # 生成 .py 播放脚本
│       └── html.py         # 生成 .html 播放页
├── templates/index.html    # 前端页面（Jinja2 模板）
├── static/
│   ├── css/{tokens,app}.css
│   └── js/app.js           # 前端逻辑
├── tests/                  # pytest 单元测试（42 tests）
├── ui-mockup.html          # UI 视觉唯一真相源
└── README.md               # 本文件
```

## 技术栈

- 后端：Python 3.10+、Flask、Pillow
- 前端：原生 HTML/CSS/JS，无框架依赖
- 测试：pytest
- 主题：暗色终端美学，JetBrains Mono + Space Grotesk 字体

## 开发

```bash
# 安装开发依赖
pip install -r requirements.txt

# 运行测试
pytest tests/ -v

# 启动开发服务器
python app.py  # debug 模式，修改代码后手动重启
```

前端代码直接编辑 `static/js/app.js` 和 `static/css/app.css`。`ui-mockup.html` 仅作视觉参考，不会自动同步到生产文件。

## License

MIT
