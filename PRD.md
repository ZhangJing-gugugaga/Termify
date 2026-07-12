# Termify PRD v1.0

> 万物皆可终端 —— 把任何视频、GIF、图片转换成终端可播放的动画

---

## 一、问题定义

### 1.1 用户痛点

终端社区有大量好玩的ASCII/Block动画（如nyancat、SalaryCat），但每个都是**为特定内容手写的**。普通用户想把自己喜欢的GIF、视频也变成终端动画，需要：

- 理解ANSI转义码、TrueColor、Unicode block字符
- 写Python/Shell脚本处理帧提取、缩放、字符映射
- 调试终端兼容性（Windows VT100、Linux terminfo、macOS差异）

这个门槛极高，99%的人做不了。

### 1.2 问题定义公式

**对谁**：喜欢终端文化、有分享欲的开发者和泛技术人群
**在什么场景下**：看到一个好玩的GIF/视频，想把它变成终端动画分享给朋友
**因为什么卡点**：没有通用工具，手动转换需要编程能力和终端知识
**需要什么结果**：上传文件 → 选风格 → 下载一个能直接运行的终端动画包

### 1.3 表面想法 vs 真实问题

| 表面想法 | 真实问题 |
|---------|---------|
| "我想把GIF转成ASCII" | 我想生成一个**别人能一键运行**的终端动画文件，不需要对方装环境 |
| "我要做ASCII art转换器" | 我要的是**多平台可播放**的输出（终端、网页、单片机），不只是文本 |
| "给我一个Python脚本" | 我想要一个**能分享的链接**，对方点开就能看到效果 |

---

## 二、产品定位

### 2.1 产品名称

**Termify** — Terminal + -ify（使……终端化）

### 2.2 一句话描述

上传任何视频、GIF或图片，选择渲染风格，下载能在任何终端播放的动画文件。

### 2.3 产品不是什么

- 不是终端模拟器
- 不是视频编辑器
- 不是ASCII art生成器（虽然包含这个能力）
- 不解决商业痛点，是玩具，核心价值是"酷"和"可分享"

### 2.4 核心价值

**把"我会做终端动画"这件事的门槛降到零。** 用户只需要上传文件和点击选择，剩下的全部由工具完成。

---

## 三、MVP 设计

### 3.1 本期做什么

1. **Web前端**：上传文件 → 在线预览渲染效果 → 选择字符集风格 → 选择输出格式 → 下载
2. **GIF输入**：支持上传GIF动图（核心场景），限制文件大小20MB以内
3. **静态图片输入**：支持上传PNG/JPG（单帧场景）
4. **5种渲染风格**：
   - 经典ASCII灰度：`@#%*+=-:. ` 密度映射，最复古，任何终端可显示
   - Unicode色块：`█▀▄` + TrueColor 24bit，视觉效果最好
   - Braille点阵：`⠁⠂⠄⡀⠈⠐⠠⢀⣀` 高分辨率点阵，科技感
   - 几何图形：`■□▪▫●○◆◇` 现代设计感
   - 极简二值：`█` + 空格，纯黑白，复古印刷感
5. **2种输出格式**：
   - **Python终端播放脚本**：下载.py文件，`python play.py`直接运行
   - **HTML自播放页面**：下载.html文件，浏览器打开即可看动画
6. **在线预览**：在网页上实时渲染所选风格的终端效果预览

### 3.2 本期不做什么

| 不做的事项 | 原因 |
|-----------|------|
| 视频输入（MP4等） | 文件处理复杂度高，首版聚焦GIF |
| 单片机/Arduino输出 | 需要硬件验证，第二版再做 |
| 用户账号系统 | 首版不需要保存用户数据，下载即走 |
| 多人协作 | 玩具不需要 |
| 在线分享链接 | MVP先做下载模式，验证需求后再加 |
| 自定义字符集 | 首版提供5种预设，验证用户是否需要更多 |
| 音频支持 | 复杂度高，和动画同步难，暂不做 |

### 3.3 MVP 四个检查

| 检查项 | 回答 |
|--------|------|
| 这一版到底在验证什么 | 用户是否真的愿意把GIF转成终端动画并分享 |
| 有没有偷塞太多功能 | 没有，核心链路只有：上传 → 预览 → 选风格 → 下载 |
| 做完后是否说得清验证结果 | 可以，看下载量、字符集选择分布、是否有人提issue要更多功能 |
| 失败后下一步怎么改 | 如果没人用，说明"终端动画"本身需求不够强；如果有人用但不要更多格式，说明聚焦GIF转终端即可 |

---

## 四、用户与场景

### 4.1 目标用户

- **主要用户**：程序员、终端爱好者、Linux用户，喜欢在命令行里折腾
- **次要用户**：泛技术人群，看到酷炫终端动画截图觉得好玩想自己试试
- **传播节点**：技术社区（V2EX、掘金、GitHub Trending）、程序员群聊

### 4.2 核心场景

**场景一：GIF表情包终端化**

用户在群里看到SalaryCat的终端动画截图，觉得很酷。打开Termify，上传一个自己喜欢的GIF（比如柴犬表情包），选"Unicode色块"风格，在线预览效果满意，下载Python脚本，在本地终端跑起来，截图分享到群。

**场景二：静态图片ASCII art**

用户想把自己的头像转成ASCII art当终端登录banner。上传头像图片，选"经典ASCII灰度"风格，下载HTML文件，在浏览器里查看效果。

### 4.3 用户使用流程

```
访问网页
  ↓
上传文件（拖拽或点击选择GIF/PNG/JPG）
  ↓
后端处理：抽帧 → 缩放适配终端尺寸 → 像素映射
  ↓
前端展示5种渲染风格的第一帧预览（并排对比）
  ↓
用户选择一个风格
  ↓
前端播放完整动画预览（终端模拟器样式）
  ↓
用户选择输出格式（Python脚本 / HTML页面）
  ↓
下载文件
  ↓
（可选）前端提供"字符集不够用？来提issue"的链接
```

---

## 五、技术方案

### 5.1 整体架构

```
┌──────────────────────────────────────────────┐
│                   前端（Web）                  │
│  上传组件 │ 风格预览 │ 终端模拟器播放 │ 下载   │
└──────────────┬───────────────────────────────┘
               │ HTTP API
┌──────────────┴───────────────────────────────┐
│                 后端（Python）                 │
│  文件接收 │ 抽帧 │ 缩放 │ 字符映射 │ 生成输出  │
└──────────────────────────────────────────────┘
```

### 5.2 后端技术栈

- **语言**：Python 3.10+
- **Web框架**：Flask（轻量，够用）
- **图像处理**：Pillow（PIL）—— GIF帧提取、缩放、色彩量化
- **视频处理（第二版）**：ffmpeg-python 或 opencv
- **字符映射引擎**：自研，支持多种字符集

### 5.3 核心转换流程

```
输入文件
  ↓
[1] 帧提取
    GIF: ImageSequence.Iterator 逐帧提取
    图片: 单帧处理
  ↓
[2] 缩放适配
    计算目标终端尺寸（默认 80x24，可配置）
    保持宽高比，等比缩放
    双线性插值（照片）或最近邻（像素风）
  ↓
[3] 像素 → 字符映射
    灰度模式: RGB → 灰度值 → 映射到字符集（按密度排序）
    色块模式: RGB → TrueColor ANSI转义 + Unicode block字符
    Braille模式: 2x4像素块 → Braille字符映射
    二值模式: 灰度阈值 → █ 或 空格
  ↓
[4] 生成帧序列
    每帧 = 一组字符串行 + 帧间隔时间
  ↓
[5] 打包输出
    Python脚本: 嵌入帧数据 + 播放逻辑（ANSI清屏 + 按帧间隔输出）
    HTML页面: 嵌入帧数据 + JavaScript播放器（<pre>标签 + 定时器）
```

### 5.4 前端技术栈

- **框架**：纯HTML + CSS + JavaScript（首版不引入React/Vue等框架，保持极简）
- **终端预览**：使用 `<pre>` 标签 + 等宽字体 + ANSI颜色渲染
- **上传**：原生File API + 拖拽
- **样式**：暗色主题（终端风格），CSS变量管理

### 5.5 字符集配置

```python
CHARSETS = {
    "ascii": {
        "name": "经典ASCII灰度",
        "chars": " @#%*+=-:.",  # 从密到疏
        "color": False,          # 不使用TrueColor
        "description": "最复古的味道，任何终端都能显示"
    },
    "blocks": {
        "name": "Unicode色块",
        "chars": "█▀▄",         # 配合TrueColor
        "color": True,
        "description": "视觉冲击力最强，需要终端支持24位色"
    },
    "braille": {
        "name": "Braille点阵",
        "chars": "⠁⠂⠄⡀⠈⠐⠠⢀⣀⠉⠠⠄⡁⢀⣀⠘⠒⠤⣀⣄⣆⣇⣧⣷⣿",
        "color": False,
        "description": "分辨率高，科技感十足"
    },
    "geometric": {
        "name": "几何图形",
        "chars": "■□▪▫●○◆◇",
        "color": False,
        "description": "现代设计感"
    },
    "binary": {
        "name": "极简二值",
        "chars": "█ ",
        "color": False,
        "description": "纯黑白，像老式报纸印刷"
    }
}
```

### 5.6 输出格式详情

#### Python终端播放脚本

```python
#!/usr/bin/env python3
"""Termify generated terminal animation"""
import sys, time, os

# 帧数据（嵌入式）
FRAMES = [
    # Frame 0
    ["  @@  ", " @@@@ ", "@@@@@@", " @@@@ ", "  @@  "],
    # Frame 1
    [" @@@@ ", "@@@@@@", "@@@@@@", "@@@@@@", " @@@@ "],
    # ...更多帧
]
FRAME_INTERVAL = 0.1  # 秒

def clear_screen():
    os.system('cls' if os.name == 'nt' else 'clear')

def play():
    try:
        while True:
            for frame in FRAMES:
                clear_screen()
                print('\n'.join(frame))
                time.sleep(FRAME_INTERVAL)
    except KeyboardInterrupt:
        clear_screen()
        print("Thanks for using Termify!")

if __name__ == '__main__':
    play()
```

#### HTML自播放页面

自包含的HTML文件，内嵌帧数据和JavaScript播放器，浏览器打开即播放。

---

## 六、API 设计

### 6.1 上传接口

```
POST /api/upload
Content-Type: multipart/form-data

Request:
  file: <GIF/PNG/JPG文件>

Response:
{
  "task_id": "abc123",
  "frames_count": 24,
  "original_size": {"width": 480, "height": 360},
  "target_size": {"width": 80, "height": 24}
}
```

### 6.2 预览接口

```
GET /api/preview/{task_id}?charset=ascii&frame=0

Response:
{
  "lines": ["  @@  ", " @@@@ ", "@@@@@@", " @@@@ ", "  @@  "],
  "charset": "ascii",
  "width": 80,
  "height": 24
}
```

### 6.3 生成输出接口

```
POST /api/generate

Request:
{
  "task_id": "abc123",
  "charset": "ascii",
  "format": "python"  // 或 "html"
}

Response:
{
  "download_url": "/api/download/abc123_termify.py",
  "file_size": "12KB"
}
```

---

## 七、风险与边界

### 7.1 技术风险

| 风险 | 影响 | 缓解措施 |
|------|------|----------|
| 大GIF文件导致处理超时 | 用户体验差 | 限制上传20MB，后端处理超时30秒 |
| 不同终端ANSI兼容性差异 | 下载的脚本在某些终端显示异常 | Python脚本使用最基础的ANSI码，HTML输出不依赖终端 |
| Braille字符在某些终端不渲染 | 用户困惑 | 前端预览即展示真实效果，不满意可换其他风格 |
| 颜色量化效果差 | 动画难看 | 实现Floyd-Steinberg抖动算法提升质量 |

### 7.2 成本管理

- 首版不部署到云端，可以本地运行验证
- 后续上云选择最低配即可（处理是CPU密集型，非内存密集型）
- 不存储用户文件，处理完即删，无存储成本

### 7.3 用户预期管理

- 前端明确标注"最佳效果需使用支持TrueColor的终端"
- 不支持的字符集在前端预览时就能看到问题
- 提供"字符集不够用？提Issue"链接指向GitHub

---

## 八、验证指标

### 8.1 MVP 验证指标

| 指标 | 目标 | 含义 |
|------|------|------|
| 首周访问量 | 500+ | 说明有传播力 |
| 文件生成下载量 | 100+ | 说明用户真的在用 |
| GitHub Star | 50+ | 说明用户认可项目 |
| Issue提交 | 5+ | 说明用户有需求想迭代 |
| 字符集选择分布 | 无极端偏斜 | 说明5种风格各有受众 |

### 8.2 验证失败的判断标准

- 首周访问量 < 100：传播力不足，可能"终端动画"太小众
- 下载率 < 10%：用户只看不玩，预览效果不够吸引人
- 无人提Issue：用户不在乎，没有迭代需求

---

## 九、后续进阶方向（不在MVP范围内）

1. **视频输入**：支持MP4/WEBM上传，限制30秒以内
2. **单片机输出**：生成Arduino/ESP32代码驱动OLED屏
3. **在线分享链接**：生成短链，对方打开浏览器就能看
4. **自定义字符集**：用户上传自己的字符集
5. **音频同步**：GIF+音频一起播放
6. **二维码序列输出**：极端但有趣的输出格式
7. **API开放**：让其他开发者接入转换能力
8. **终端实时预览**：前端模拟真实终端环境

---

## 十、开发计划

### Phase 1: 核心转换引擎（后端）

- [x] GIF帧提取 + 缩放逻辑 — `termify/frames.py`（`ImageSequence.Iterator` 抽帧 + LANCZOS 等比缩放 + letterbox）
- [x] 5种字符集映射引擎 — `termify/charset.py`（ascii/blocks/braille/geometric/binary）
- [x] Python脚本生成器 — `termify/output/python.py`（嵌入 FRAMES + `time.sleep` 循环）
- [x] HTML页面生成器 — `termify/output/html.py`（自包含 `<pre>` + JS 定时器）
- [x] 单元测试 — `tests/`（42 tests，全绿）

### Phase 2: Web前端

- [x] 上传组件（拖拽 + 点击）— `static/js/app.js` `handleFile()` + `FormData` 上传
- [x] 5风格预览卡片 — `.style-card[data-style]` + `requestPreview()`
- [x] 终端模拟器动画播放 — `<pre id="animPreview">` + `setInterval` 播放/暂停/进度条
- [x] 下载按钮 — `doDownload()` → `/api/generate`
- [x] 暗色主题样式 — `static/css/{tokens,app}.css`（CSS 变量 + 网格背景 + 扫描线）

### Phase 3: 联调与部署

- [x] 前后端联调 — `app.py` 三个端点直连 `termify.convert()` + `render()`
- [x] 错误处理 — 400/404/413 状态码 + 扩展名白名单 + 20MB 上限
- [x] 本地运行测试 — `python app.py` → `http://127.0.0.1:5000`
- [x] 编写README — 含 Quick Start / API 表 / 项目结构
- [x] 推送GitHub — `https://github.com/ZhangJing-gugugaga/Termify`

### v1.0 放行后修复与优化（2026-07-11）

浏览器实测发现 blocks 彩色预览显示为 ANSI 字面量乱码 + 半色块双色失效，一并修复：

- [x] **ANSI→HTML 转换** — 新增 `termify/ansi_to_html.py`，将 TrueColor ANSI 转成带 CSS 渐变的 `<span>`（浏览器不解释 ANSI，参考 SalaryCat 走的是终端 stdout 路径）；`html.py` 嵌入前转换，`app.js` 添加 JS 侧 `ansiToHtml`
- [x] **blocks 半色块 fg≡bg 修复** — 根因是预缩放到 (w,h) 后 `y_top==y_bot`（同采样点），垂直分辨率加倍失效。改为引擎缩放到 **(w, 2h)** + 渲染器逐对采样行，fg≠bg 双色恢复
- [x] **颜色 delta 编码** — 相邻 cell 颜色不变不重发转义码（与参考 SalaryCat 一致），输出体积从 ~12MB 降到 ~3MB，终端播放更流畅

### v1.0 放行后修复与优化（2026-07-12）

浏览器实测 + 人工测验发现 blocks 预览溢出界面、播放卡顿、进度条乱跳等问题，逐一修复：

**Bug 修复：**

- [x] **CJK Windows 下 blocks 溢出** — `▀`(U+2580) 在中文 Windows 上被浏览器渲染为双倍宽度（East Asian Ambiguous Width 问题），导致 80 个 `▀` 撑满 160 字符宽度冲出容器。修复：每个 `▀` 独立包裹在 `<span class="hb">` 中，CSS 强制 `display:inline-block;width:1ch;height:1.3em;overflow:hidden`；容器从 `display:flex;justify-content:center` 改为 `display:block;overflow-x:auto`
- [x] **进度条乱跳** — `requestPreview` 切换字符集时立即调用 `startPlayer()`，但新帧还没从 API 返回，播放器用旧帧数据跑，进度条从旧位置突然跳到 0。修复：引入 `wasPlaying` 标记，先停播放器→等 API 返回→预渲染新帧→再重启播放器
- [x] **其他风格播放不了** — 同一根因：旧 rAF 循环未被正确停止，新帧被旧帧覆盖。修复后切换字符集会先停播放器→等 API 返回→预渲染新帧→再启动
- [x] **blocks 首帧跳帧** — `rafLoop` 首次回调时 `lastFrameTime=0`，`ts-0` 恒为 true 导致首帧立即跳到下一帧。修复：首次回调初始化 `lastFrameTime=ts` 而非直接 tick

**性能优化：**

- [x] **预渲染所有帧** — `applyPreview` 接收帧数据后一次性把 28 帧全部转成 HTML 字符串存入 `S.htmlFrames`，播放时 `renderFrame` 直接取 `innerHTML`，不再每帧调用 `ansiToHtml` 解析 ANSI 码（原 24×80=1920 个 span 解析 ×25fps → 一次预渲染）
- [x] **空 span 替代 background-clip:text** — blocks 的 `▀` 渲染从 `<span>▀</span>` + `background-clip:text;color:transparent;-webkit-text-fill-color:transparent`（GPU 密集）改为空 `<span class="hb"></span>` + 纯 `background:linear-gradient`，视觉一致但渲染开销大幅降低
- [x] **requestAnimationFrame 替代 setInterval** — 与浏览器 paint cycle 对齐，避免丢帧和布局抖动

**功能增强：**

- [x] **上传后自动滚动** — `handleFile` 上传完成后 `scrollIntoView` 到预览区
- [x] **上传后自动播放** — `wasPlaying=true` 使动画在上传后自动开始

**安全清理：**

- [x] **移除 demo.py 硬编码本地路径** — `E:\Desktop\...` 改为相对路径 `sample.gif`，避免泄露文件系统结构

**经验教训：**

- **build_frontend.py 覆盖风险** — `tools/build_frontend.py` 从 `ui-mockup.html` 拆分 JS/CSS 到 `static/` 目录，会覆盖 `app.js` 和 `app.css` 的生产代码。Phase 3 的 API 集成、上传处理、rAF 循环等代码不在 mockup 中，一旦触发拆分就会丢失。**禁止在生产代码修改后运行此脚本**。修改前端应直接改 `static/js/app.js` 和 `static/css/app.css`，mockup 仅作视觉参考
- **Read 工具不显示 ESC 控制字符** — `\x1b`（ASCII 27）在 Read 输出中不可见，需用 `open(path,'rb').read()` 检查实际字节
- **空 inline-block span 高度坍塌** — 去掉 `▀` 文字后 span 变空，`inline-block` 无文本时高度为零，需加显式 `height:1.3em`

---

*PRD v1.0 | 2026-07-10 | Termify | 进度更新 2026-07-12*
