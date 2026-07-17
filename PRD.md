# Termify PRD

> **版本**：V 2.0.0
> **日期**：2026-07-17
> **作者**：许清楚（产品经理）
> **定位**：把 GIF / PNG / JPG 转成终端可播放动画（.py / .html），MIT 开源
> **本版说明**：基于 v1.0 PRD + 四视角产品分析（2026-07-17）修复更新。v1.0 迭代日志原样保留，见「附：迭代记录」。

---

## 一、概述

### 1.1 产品信息

| 项 | 内容 |
|---|---|
| 名称 | Termify（Terminal + -ify，使……终端化） |
| 一句话描述 | 上传 GIF / PNG / JPG，选择渲染风格，下载能在任何终端 / 浏览器播放的动画文件（**视频 v2 规划中**） |
| 技术栈 | Python 3.10+ / Flask / Pillow 后端；原生 HTML/CSS/JS 前端；pytest（**实测 41 个测试，全绿**） |
| 已交付 | 转换引擎（termify/ 包）+ Web 界面（app.py）+ CLI（demo.py）+ 5 种渲染风格 + 全屏自适应 + music.mp3 音频 + REST API（upload / preview / generate / download） |
| 协议 | MIT（零注册、零云端、隐私友好） |

### 1.2 目标用户

- **主要用户**：程序员、终端爱好者、Linux 用户，喜欢在命令行折腾。
- **次要用户**：泛技术人群，看到酷炫终端动画截图觉得好玩想自己试试。
- **传播节点**：技术社区（V2EX、掘金、GitHub Trending、Reddit r/commandline、HN）。
- **三类典型角色**：0 技术外行小白、基础小白程序员、嵌入式 / 集成开发者。

### 1.3 核心场景

- **场景一（GIF 表情包终端化）**：用户在群里看到 SalaryCat 终端动画截图，觉得很酷；打开 Termify，上传喜欢的 GIF，选「Unicode 色块」风格，预览满意，下载 .py 在终端跑起来截图分享。
- **场景二（静态图片 ASCII art）**：用户想把自己的头像转成 ASCII art 当终端登录 banner；上传头像，选「经典 ASCII 灰度」，下载 .html 在浏览器查看。
- **场景三（AI / 脚本批量生成，v2 重点）**：AI agent 或脚本通过 REST API 批量把一批素材转成终端动画文件，嵌入自己的工作流。

### 1.4 需求背景（四视角共识）

| 视角 | 核心结论 |
|---|---|
| ① 外行小白 | 内核方向对，但「启动门槛（git/pip/python）」+「Windows 乱码」+「术语天书」劝退；当前形态不推荐给纯小白，须先修「开箱即用」。 |
| ② 小白程序员 | 引擎质量在业余项目属上乘（45 测试 + docstring + PRD 对齐），是优质学习样本；但双注册表 / 20MB 双定义 / 内存存储无淘汰是真实技术债。 |
| ③ 产品经理 | 护城河真实：产出**自包含、可运行、可分享**的文件，接收方无需装 Termify；在线竞品做「看」不做「带走」；须补「在线 Demo + 小白开箱」两大短板。 |
| ④ 嵌入式开发者 | 嵌入式路线可行但 `FrameSequence` 缺像素级出口，须先给引擎补像素出口；PC 离线生成 + MCU 仅播放的职责划分正确。 |

**一句话画像**：内核很香、门面劝退——引擎质量在小众开源项目里属上乘，但「Web 界面」实为本地自托管，对纯小白和主流用户存在隐形高墙。

### 1.5 需求目标

1. **把「我会做终端动画」的门槛降到零**：上传 + 点选 + 下载即可，无需编程与终端知识（v1 已基本实现核心链路）。
2. **守住差异化护城河**：产出**可下载、可运行、可分享**的自包含文件，接收方零安装。
3. **补齐增长短板（v2 核心）**：公开在线 Demo + 一键桌面包 + Windows TrueColor 兼容，让主流用户真正用得上。

### 1.6 需求范围

- **本期（v1.0 已交付）**：GIF/PNG/JPG 输入；5 种风格；.py / .html 输出；Web + CLI；REST API；全屏自适应；音频（music.mp3）。
- **不在本期**：视频直传（v2 P1）、单片机输出（v2 P2/M3）、用户账号（MIT 零注册）、在线分享链接 / 社区画廊（v2 P2）、自定义字符集（v2 视需求）。

### 1.7 ⭐ 问题 → 解决方案映射表（核心，来自四视角分析）

| # | 问题（来自四视角分析） | 对应解决方案 | 优先级 |
|---|---|---|---|
| 1 | 启动门槛高：git / pip / python 三连击劝退主流用户 | 公开在线 Demo（打开网址即用）+ 一键 exe / AppImage 包（下载双击即用，参考 SalaryCat 的 PyInstaller 思路） | P0 |
| 2 | Windows 旧终端 TrueColor 乱码 | 自动降级到非彩色风格 / 提示用 HTML 版本 | P0 |
| 3 | Web 一次只能处理一个文件 | Web 批量上传 | P1 |
| 4 | 200×60 超清卡顿（全帧返回 ≈6MB 单请求） | 预览分块加载 / 默认降一档尺寸 / 输出体积优化（颜色 delta 编码已落地） | P1 |
| 5 | 技术债：内存存储无淘汰、20MB 双定义、双注册表、`FrameSequence` 缺像素出口 | 单注册表 + `config.py` 抽常量 + 持久化（TTL）+ 引擎补像素出口 | P2 |
| 6 | 留存弱、无网络效应（玩具属性强） | 社区画廊 / 分享链接 | P2 |
| 7 | 在线竞品（ASCII Magic / AsciiCraft）降维打击「即用性」 | 守住「可下载可运行可分享文件」护城河，并放大（在线 Demo + 一键包拉平门槛，文件可带走是竞品没有的） | 持续 |
| 8 | 视频无法直接转 | 视频直传（先转 GIF 的 workaround 已存在） | P1 |
| 9 | 嵌入式无像素出口 | 引擎暴露 `scaled_frames` / `convert_pixels()`，新增 `output/embedded.py` | P2 / M3 |

---

## 二、功能列表

### 2.1 功能模块与优先级

| 模块 | 功能 | 子任务 | 优先级 |
|---|---|---|---|
| 转换引擎 | 帧提取 | GIF 逐帧 / 图片单帧 | ✅ 已交付 |
| 转换引擎 | 缩放适配 | LANCZOS 等比缩放 + letterbox | ✅ 已交付 |
| 转换引擎 | 字符映射 | ascii / blocks / braille / geometric / binary | ✅ 已交付 |
| 转换引擎 | 像素级出口 | `scaled_frames` / `convert_pixels()`（嵌入式前置） | P2 / M3 |
| 输出 | .py 终端脚本 | 嵌入 FRAMES + 播放逻辑 + 全屏自适应 + 音频 | ✅ 已交付 |
| 输出 | .html 自播放页 | 自包含 + ANSI→HTML + 预渲染 | ✅ 已交付 |
| 输出 | 嵌入式 C 数组 | `output/embedded.py`（OLED→TFT） | P2 / M3 |
| Web | 上传 / 预览 / 选风格 / 下载 | 拖拽 + 5 风格卡片 + 终端模拟器播放 | ✅ 已交付 |
| Web | 批量上传 | 多文件一次转换 | P1 |
| Web | 风格卡大白话 + 缩略图 | 术语零门槛 + 预览缩略图 | P1（M0 起） |
| Web | Windows TrueColor 自动降级 | 检测旧终端提示 HTML / 非彩色 | P0 |
| CLI | demo.py 批量 | `--charset all` / `--format` | ✅ 已交付 |
| 部署 | 公开在线 Demo | Flask+gunicorn+Docker + 子域名 + HTTPS | P0 |
| 部署 | 一键桌面包 | PyInstaller exe / AppImage | P0 |
| 部署 | API 限流 + 持久化 | TTL + 单进程 / Redis | P2 |
| 音频 | music.mp3 自动播放 | .py 同目录检测 + 系统播放器 | ✅ 已交付（超出 MVP） |
| 输入 | 视频直传 | ffmpeg / opencv 抽帧 | P1 |
| 增长 | 社区画廊 / 分享 | 分享链接 + 画廊 | P2 |

### 2.2 扩展功能 TODO 与里程碑（来自四视角分析路线）

- **P0（地基）**：公开在线 Demo 站点、一键可执行包（PyInstaller，参考 SalaryCat）、Windows TrueColor 自动降级。
- **P1（增长补齐）**：视频直传、Web 批量、风格卡大白话 + 预览缩略图。
- **P2（纵深 + 清债）**：嵌入式 Arduino/ESP32 输出（v2）、社区画廊 / 分享、API 限流 + 持久化（技术债清理）。

**里程碑**：
- **M0（1–2 周 止血）**：Windows 兼容降级、README 大白话 + 动图、风格卡预览缩略图。
- **M1（2–4 周 增长引擎）**：公开在线 Demo + 一键 exe / AppImage 包。
- **M2（1–2 月 补齐短板）**：视频直传、Web 批量、音频内嵌引导。
- **M3（2–3 月 差异化纵深）**：① 引擎补像素出口 ② 嵌入式 v2（单色 OLED→压缩→RGB TFT）③ 社区画廊 ④ 技术债清理（持久化 + 限流 + 单注册表 + config 常量）。

---

## 三、页面结构与导航

```
┌─────────────────────────────────────────────┐
│  顶部：标题 + 一句话说明 + （v2）在线提示       │
├─────────────────────────────────────────────┤
│  上传区：拖拽 / 点击选择 GIF·PNG·JPG（≤20MB）   │
│         ↓ 上传后自动滚动到风格区               │
├─────────────────────────────────────────────┤
│  风格区：5 张风格卡片（大白话名 + 缩略图预览）  │
│         ↓ 点击后自动滚动到预览区               │
├─────────────────────────────────────────────┤
│  预览区：终端模拟器播放（播放/暂停/进度条）     │
│         输出格式选择：.py / .html              │
├─────────────────────────────────────────────┤
│  下载区：下载按钮 + （v2）分享链接             │
│  底部：GitHub 链接 + 「字符集不够用？提 Issue」 │
└─────────────────────────────────────────────┘
```

导航原则（v1 已落地）：上传完成自动滚到风格区，点风格卡自动滚到预览区，形成「上传 → 选风格 → 看预览」的流畅引导。

---

## 四、功能说明

### 4.1 用户流程

```
访问网页
  ↓
上传文件（拖拽或点击选择 GIF/PNG/JPG，≤20MB）
  ↓
后端处理：抽帧 → LANCZOS 缩放适配终端尺寸 → 像素映射
  ↓
前端展示 5 种渲染风格预览（大白话名 + 缩略图）
  ↓
用户选择一个风格
  ↓
前端播放完整动画预览（终端模拟器样式）
  ↓
用户选择输出格式（.py 终端脚本 / .html 网页）
  ↓
下载文件
  ↓
（v2 可选）生成分享链接 / 发布到社区画廊
```

### 4.2 异常

| 异常 | 触发 | 处理 |
|---|---|---|
| 格式不支持 | 非 GIF/PNG/JPG | 400 + 扩展名白名单提示 |
| 文件过大 | >20MB | 413 + 「请压小到 20MB 内」 |
| 任务不存在 | 错误 task_id | 404 |
| 终端不支持 TrueColor | 旧终端（v2 检测） | 提示用 HTML / 非彩色风格 |
| 预览卡顿 | 尺寸过大全帧返回 | 分块加载 / 默认降档（v2） |

### 4.3 状态机（任务生命周期）

```
上传成功 ──→ 已抽帧(READY) ──→ 已生成(GENERATED) ──→ 已下载(DONE)
                    │                                      │
                    └────────── 过期淘汰(TTL, v2) ←────────┘
```

### 4.4 字段

- `task_id`：任务唯一 ID。
- `frames_count`：帧数。
- `original_size` / `target_size`：原始 / 目标宽高。
- `charset`：风格（ascii / blocks / braille / geometric / binary）。
- `format`：输出格式（python / html）。
- `download_url`：下载路径。
- `file_size`：输出文件体积。

### 4.5 文案

- 最佳效果提示：「彩色风格需支持 TrueColor 的终端（推荐 Windows Terminal）」。
- 不支持的字符集在预览时即可见，不满意可换风格。
- 「字符集不够用？来提 Issue」链接指向 GitHub。

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
│  文件接收 │ 抽帧 │ LANCZOS 缩放 │ 字符映射 │ 生成输出 │
└──────────────────────────────────────────────┘
```

引擎层 `termify/`（charset / frames / engine / output）与 Web 层 `app.py` 解耦；内存任务存储（`TASKS` 全局字典），无数据库，20MB 上限。

### 5.2 技术栈

- **语言**：Python 3.10+。
- **Web 框架**：Flask（轻量，够用）。
- **图像处理**：Pillow（PIL）—— GIF 帧提取、缩放、色彩量化。
- **视频处理（v2）**：ffmpeg-python 或 opencv。
- **字符映射引擎**：自研，支持多种字符集。
- **前端**：纯 HTML + CSS + JavaScript（极简，无框架）；`<pre>` + 等宽字体 + ANSI 渲染；暗色主题。

### 5.3 核心转换流程（**⚠️ 已修复：缩放算法**）

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
    【修复】缩放算法统一为 Image.LANCZOS（高质量下采样）。
    原 PRD 写「双线性插值（照片）或最近邻」，经代码实测为 LANCZOS，
    且 LANCZOS 质量优于原规格 → 标注「实现优于原规格」。
  ↓
[3] 像素 → 字符映射
    灰度模式: RGB → 亮度 → **CDF 自适应分桶** → 映射到字符集（按密度排序）
            （直方图均衡化：_build_adaptive_lut() 构建累积分布查找表，
             无论原图亮度分布如何都充分利用字符密度，画质显著优于线性映射）
    色块模式: RGB → TrueColor ANSI 转义 + Unicode block 字符
    Braille模式: 2x4 像素块 → Braille 字符映射
    二值模式: 灰度阈值 → █ 或 空格
  ↓
[4] 生成帧序列
    每帧 = 一组字符串行 + 帧间隔时间
    （注：FrameSequence 当前为 ANSI 字符串，缺像素出口，见 M3）
  ↓
[5] 打包输出
    Python脚本: 嵌入帧数据 + 播放逻辑（ANSI 清屏 + 按帧间隔输出 + 全屏自适应 + 音频）
    HTML页面: 嵌入帧数据 + JS 播放器（<pre> + 定时器 + ANSI→HTML 预渲染）
```

### 5.4 字符集配置

```python
CHARSETS = {
    "ascii":    {"name": "经典ASCII灰度", "chars": " @#%*+=-:.", "color": False, "description": "最复古的味道，任何终端都能显示"},
    "blocks":   {"name": "Unicode色块",   "chars": "█▀▄",        "color": True,  "description": "视觉冲击力最强，需要终端支持24位色"},
    "braille":  {"name": "Braille点阵",   "chars": "⠁⠂⠄...",     "color": False, "description": "分辨率高，科技感十足"},
    "geometric":{"name": "几何图形",       "chars": "■□▪▫●○◆◇", "color": False, "description": "现代设计感"},
    "binary":   {"name": "极简二值",       "chars": "█ ",         "color": False, "description": "纯黑白，像老式报纸印刷"},
}
```

> ⚠️ 扩展陷阱：`CHARSETS`（元数据）与 `_RENDERERS`（函数）为**双注册表**，新增风格须两处都改，否则静默走错分支（v2 合并为单注册表）。

### 5.5 输出格式详情

#### Python 终端播放脚本（.py）

```python
#!/usr/bin/env python3
"""Termify generated terminal animation"""
FRAMES = [ ["  @@  ", " @@@@ ", ...], ... ]   # 嵌入帧数据
FRAME_INTERVAL = 0.1
# 运行时：全屏自适应（_fit_frames / _compose_screen）+ 可选音频（_start_audio）
# 彩色风格需终端支持 TrueColor；Windows 用 _enable_windows_ansi() 启用 VT100
```

#### HTML 自播放页面（.html）

自包含 HTML，内嵌帧数据 + JS 播放器；通过 `ansi_to_html.py` 把 TrueColor ANSI 转成带 CSS 渐变的 `<span>`，浏览器直接渲染（不依赖终端）。

---

## 六、API 设计

### 6.1 端点总览

| 方法 | 路径 | 说明 |
|---|---|---|
| POST | `/api/upload` | 上传文件，返回 task_id |
| GET | `/api/preview/<task_id>?charset=&frame=` | 取某风格某帧预览 |
| POST | `/api/generate` | 生成输出（指定 charset + format），返回 download_url |
| GET | `/api/download/<filename>` | 下载生成的文件 |

### 6.2 上传

```
POST /api/upload
Content-Type: multipart/form-data
Request:  file: <GIF/PNG/JPG>
Response: {"task_id":"abc123","frames_count":24,
           "original_size":{"width":480,"height":360},
           "target_size":{"width":80,"height":24}}
```

### 6.3 预览

```
GET /api/preview/abc123?charset=ascii&frame=0
Response: {"lines":["  @@  ",...],"charset":"ascii","width":80,"height":24}
```

### 6.4 生成与下载

```
POST /api/generate
Request:  {"task_id":"abc123","charset":"ascii","format":"python"}
Response: {"download_url":"/api/download/abc123_termify.py","file_size":"12KB"}

GET /api/download/abc123_termify.py   → 文件流
```

### 6.5 ⭐ 面向 AI / 脚本的程序化批量调用（核心集成价值）

Termify 的 REST API 即「AI 可程序化调用」接口——AI agent 或脚本用 HTTP 直接调用即可批量转换；本地无网环境用 CLI `python demo.py 图片.gif --charset all`。

**curl 示例（批量思路：循环文件）**
```bash
curl -F "file=@cat.gif" http://127.0.0.1:5000/api/upload
# → {"task_id":"abc123", ...}
curl -X POST http://127.0.0.1:5000/api/generate \
     -H "Content-Type: application/json" \
     -d '{"task_id":"abc123","charset":"blocks","format":"html"}'
# → {"download_url":"/api/download/abc123_termify.html", ...}
curl -O http://127.0.0.1:5000/api/download/abc123_termify.html
```

**Python requests 示例（AI agent 直接嵌入工作流）**
```python
import requests
BASE = "http://127.0.0.1:5000"
r = requests.post(f"{BASE}/api/upload", files={"file": open("cat.gif", "rb")})
tid = r.json()["task_id"]
g = requests.post(f"{BASE}/api/generate",
                  json={"task_id": tid, "charset": "blocks", "format": "html"})
url = BASE + g.json()["download_url"]
open("out.html", "wb").write(requests.get(url).content)
```

**CLI 离线批量**
```bash
python demo.py 图片.gif --charset all     # 生成全部 5 种风格
python demo.py 图片.gif --format html      # 指定格式
```

---

## 七、问题校验（6 项）

| # | 校验项 | 结论 |
|---|---|---|
| 1 | 目标用户是否太泛？ | 否。聚焦「终端文化 + 泛技术人群」，三层角色清晰，不是所有人。 |
| 2 | 核心场景是否真实且高频？ | 是。表情包终端化、ASCII banner、AI 批量生成均为真实动机；高频性待在线 Demo 验证。 |
| 3 | 解决的问题是否值得做（真痛点）？ | 是。「零门槛做终端动画 + 产出可带走文件」是真实差异点，竞品未覆盖。 |
| 4 | MVP 边界是否清晰（有没有偷塞功能）？ | v1 边界清晰（上传→预览→选风格→下载）。音频/全屏自适应为实测后超 MVP 追加，已标注。 |
| 5 | 成功 / 失败能否被衡量？ | 能。见第十三章量化指标（访问量 / 下载量 / Star / Issue）。 |
| 6 | 是否有不可替代的差异化价值（护城河）？ | 有。自包含可运行可分享文件（接收方零安装）+ MIT 零注册零云端，竞品（ASCII Magic 等）只「看」不「带走」。 |

---

## 八、输入设计

| 项 | 说明 |
|---|---|
| 输入项 | GIF（动图，核心）/ PNG（单帧）/ JPG（单帧） |
| 必填 | 一个文件（multipart） |
| 质量约束 | 格式白名单（gif/png/jpg）；大小 ≤20MB；建议帧数适中、分辨率不过低 |
| 不足处理 | 非白名单格式 → 400；超 20MB → 413；损坏文件 → 抽帧失败报错 |
| 视频（v2） | MP4/WEBM 直传（P1）；当前 workaround：先转 GIF |

---

## 九、输出设计（每类输出用户能做什么）

| 输出 | 用户能做什么 | 接收方门槛 |
|---|---|---|
| **.py 终端脚本** | 在终端 `python xxx.py` 播放；支持全屏自适应 + 可选 music.mp3 音频；可当 banner 嵌入 shell 启动 | 需装 Python + 支持 TrueColor 的终端（彩色风格） |
| **.html 网页** | 浏览器打开即播；可发微信/邮件/链接；零安装 | 任意现代浏览器，零门槛 |
| **嵌入式 C 数组（v2）** | 烧录到 Arduino/ESP32 驱动 OLED/TFT 屏播放 | MCU + 屏幕硬件 |
| **分享链接 / 画廊（v2 P2）** | 一键分享、社区展示 | 打开链接即看 |

---

## 十、转换引擎流水线（替代「AI Workflow」，无 LLM）

> 本产品**不含任何 AI / LLM 环节**，纯确定性图像处理流水线。以下 7 步标注角色。

| 步 | 角色 | 动作 |
|---|---|---|
| 1 | 用户 | 上传 GIF/PNG/JPG，选择风格与输出格式 |
| 2 | 系统 | 接收文件、校验格式/大小、存入内存任务（TASKS） |
| 3 | 引擎 | 抽帧（GIF 逐帧 / 图片单帧） |
| 4 | 引擎 | LANCZOS 缩放适配目标终端尺寸 + letterbox |
| 5 | 引擎 | 像素 → 字符映射（按 charset 调用对应 `_render_xxx`） |
| 6 | 引擎 | 生成帧序列 / 打包（.py 或 .html，含全屏自适应 + 音频钩子） |
| 7 | 系统 | 返回预览 / 下载链接给用户 |

---

## 十一、引擎职责拆解（替代「AI 职责」）

> 引擎为纯函数式图像处理模块，无智能决策。职责拆分如下：

| 模块 | 职责 | 状态 |
|---|---|---|
| `termify/frames.py` | 抽帧（ImageSequence.Iterator）、LANCZOS 缩放、letterbox、20MB 上限（**⚠️ 与 app.py 双定义，待抽 config**） | ✅ |
| `termify/charset.py` | `CHARSETS` 元数据注册 + `_RENDERERS` 函数注册（**⚠️ 双注册表，待合并**） | ✅ |
| `termify/engine.py` | `convert()` 编排：抽帧→缩放→映射；**待补 `convert_pixels()` / 暴露 `scaled_frames`**（嵌入式前置） | ✅ / P2 |
| `termify/output/python.py` | 生成 .py：嵌入 FRAMES + 播放循环 + 全屏自适应 + 音频 | ✅ |
| `termify/output/html.py` | 生成 .html：自包含 + JS 播放器 + ANSI→HTML | ✅ |
| `termify/ansi_to_html.py` | TrueColor ANSI → 带 CSS 渐变 `<span>`（浏览器不解释 ANSI） | ✅ |
| `termify/output/embedded.py` | （v2）像素 → C 头文件/数组 + `.ino` 播放草图 | P2 / M3 |

---

## 十二、Badcase 分析（≥8 个）

| # | 场景 | 现象 | 根因 | 缓解 / 修复 |
|---|---|---|---|---|
| 1 | 上传非 GIF（txt/zip/pdf） | 400 / 乱码 | 扩展名白名单外 | 白名单校验 + 友好提示 |
| 2 | 超大文件（>20MB） | 413 拒收 | 20MB 上限 | 提示压小；v2 视频直传也限时长 |
| 3 | Windows 旧终端 TrueColor 乱码 | 方块/颜色错 | 旧终端不支持 24 位色 | v2 自动降级 + 提示用 HTML |
| 4 | 200×60 超清卡顿 | 预览一卡一卡 | 全帧返回 ≈6MB 单请求 | 分块加载 / 默认降档 / 预渲染（已部分做） |
| 5 | 风格选错显丑 | 灰白/马赛克 | 风格与内容不匹配或终端不支持 | 预览即见真实效果，可换风格 |
| 6 | Web 批量超限 | 一次只能一个 | Web 仅单文件上传 | v2 Web 批量（P1） |
| 7 | 终端不支持 TrueColor | 彩色失效 | 终端能力差异 | 非彩色风格兜底 / 提示 Windows Terminal |
| 8 | 音频缺失 | .py 静音 | 无 music.mp3 同目录 | 文档引导放 music.mp3；.html 音频待完善 |
| 9 | 嵌入体积超标 | MCU Flash 爆 | RGB TFT 原始像素量大 | 按目标屏降采样 + RLE/调色板量化（v2） |
| 10 | 双注册表不一致 | 静默走错渲染分支 | `CHARSETS`/`_RENDERERS` 只改其一 | v2 合并单注册表 + 单测 |

---

## 十三、验证目标（可量化 + 收集方法）

| 指标 | 目标 | 收集方法 |
|---|---|---|
| 首周访问量（在线 Demo） | 500+ | 服务器访问日志 |
| 文件生成下载量 | 100+ | 下载端点计数 |
| GitHub Star | 50+ | GitHub API |
| Issue 提交 | 5+ | GitHub Issues |
| 字符集选择分布 | 无极端偏斜 | generate 端点统计 |
| 风格卡缩略图点击率 | 提升选风格转化率 | 前端埋点（v2） |

**失败判断**：首周访问 <100（传播力不足）；下载率 <10%（预览不够吸引）；无人提 Issue（无迭代需求）。

---

## 十四、PRD 风险与下一步

- **最大不确定性**：在线竞品（ASCII Magic / AsciiCraft）「打开即用 + 视频 + 导出 MP4」体验碾压本地工具，Termify 能否靠「可带走文件」护城河 + 在线 Demo 拉平门槛并转化留存，需 M1 验证。
- **成功扩展**：M1 在线 Demo 上线后访问/下载达标 → 推进 M2 视频直传 / Web 批量 → M3 嵌入式 + 社区画廊，形成「增长引擎 → 补齐短板 → 差异化纵深」。
- **失败调整**：若在线 Demo 仍低转化，说明「终端动画」小众天花板低，则收缩为「社区驱动 + 技术影响力资产」，重投入商业化不划算（MIT + 零注册难强商业化）。

---

## 十五、自检清单

### 六大盲区

| 盲区 | 自检 |
|---|---|
| 目标用户是否太泛 | 否，三层角色清晰（一、1.2） |
| 是否偷塞功能 | v1 边界清晰；音频/全屏为超 MVP 追加已标注 |
| 成功能否衡量 | 能，第十三章量化指标 |
| 失败能否收手 | 能，第十四章失败判断 |
| 护城河是否真实 | 是，可带走文件 + MIT 零云端 |
| 扩展性是否预留 | 是，charset 扩展点 + REST API + 像素出口规划 |

### 安全设计自检

- [x] 上传格式白名单 + 20MB 上限（413/400）。
- [x] 不存储用户文件，处理完即删（MIT 零注册零云端）。
- [x] 在线版无账号系统，无 PII 收集。
- [x] 一键包不含硬编码本地路径（v1 已清理 demo.py 硬编码）。
- [ ] v2 在线 Demo 需补：API 限流（防滥用）+ 任务持久化 TTL（防内存泄漏）。
- [ ] v2 需防：上传文件类型伪装（建议 PIL 实际解码校验）。

---

## 附 A：迭代记录（保留 v1.0 修复日志，真实资产）

### v1.0 放行后修复与优化（2026-07-11）

浏览器实测发现 blocks 彩色预览显示为 ANSI 字面量乱码 + 半色块双色失效，一并修复：

- [x] **ANSI→HTML 转换** — 新增 `termify/ansi_to_html.py`，将 TrueColor ANSI 转成带 CSS 渐变的 `<span>`（浏览器不解释 ANSI，参考 SalaryCat 走的是终端 stdout 路径）；`html.py` 嵌入前转换，`app.js` 添加 JS 侧 `ansiToHtml`。
- [x] **blocks 半色块 fg≡bg 修复** — 根因是预缩放到 (w,h) 后 `y_top==y_bot`（同采样点），垂直分辨率加倍失效。改为引擎缩放到 **(w, 2h)** + 渲染器逐对采样行，fg≠bg 双色恢复。
- [x] **颜色 delta 编码** — 相邻 cell 颜色不变不重发转义码（与参考 SalaryCat 一致），输出体积从 ~12MB 降到 ~3MB，终端播放更流畅。

### v1.0 放行后修复与优化（2026-07-12）

浏览器实测 + 人工测验发现 blocks 预览溢出界面、播放卡顿、进度条乱跳等问题，逐一修复：

**Bug 修复：**
- [x] **CJK Windows 下 blocks 溢出** — `▀`(U+2580) 在中文 Windows 上被浏览器渲染为双倍宽度（East Asian Ambiguous Width），导致 80 个 `▀` 撑满 160 字符宽度冲出容器。修复：每个 `▀` 独立包裹在 `<span class="hb">` 中，CSS 强制 `display:inline-block;width:1ch;height:1.3em;overflow:hidden`；容器从 `display:flex;justify-content:center` 改为 `display:block;overflow-x:auto`。
- [x] **进度条乱跳** — `requestPreview` 切换字符集时立即调用 `startPlayer()`，但新帧还没从 API 返回，播放器用旧帧数据跑。修复：引入 `wasPlaying` 标记，先停播放器→等 API 返回→预渲染新帧→再重启播放器。
- [x] **其他风格播放不了** — 同一根因：旧 rAF 循环未被正确停止。修复后切换字符集会先停播放器→等 API 返回→预渲染新帧→再启动。
- [x] **blocks 首帧跳帧** — `rafLoop` 首次回调时 `lastFrameTime=0` 导致首帧立即跳到下一帧。修复：首次回调初始化 `lastFrameTime=ts`。

**性能优化：**
- [x] **预渲染所有帧** — `applyPreview` 一次性把帧全部转成 HTML 字符串存入 `S.htmlFrames`，播放时直接取 `innerHTML`，不再每帧解析 ANSI。
- [x] **空 span 替代 background-clip:text** — blocks 的 `▀` 渲染改为空 `<span class="hb">` + 纯 `background:linear-gradient`，视觉一致但渲染开销大幅降低。
- [x] **requestAnimationFrame 替代 setInterval** — 与浏览器 paint cycle 对齐，避免丢帧。

**功能增强：**
- [x] **上传后自动滚动** — 上传完成自动滚到风格区；点风格卡自动滚到预览区。
- [x] **上传后自动播放** — `wasPlaying=true` 使动画上传后自动开始。

**安全清理：**
- [x] **移除 demo.py 硬编码本地路径** — `E:\Desktop\...` 改为相对路径 `sample.gif`。

**经验教训：**
- **build_frontend.py 覆盖风险** — `tools/build_frontend.py` 从 `ui-mockup.html` 拆分 JS/CSS 会覆盖 `app.js`/`app.css` 生产代码（mockup 不含 API 集成等）。**禁止在生产代码修改后运行此脚本**，改前端直接改 `static/`。
- **Read 工具不显示 ESC 控制字符** — `\x1b` 在 Read 输出不可见，需用 `open(path,'rb').read()` 检查实际字节。
- **空 inline-block span 高度坍塌** — 去掉 `▀` 文字后需加显式 `height:1.3em`。

### v1.0 放行后修复与优化（2026-07-12 续）

**功能增强 — Python 播放器全屏自适应 + 音频：**
- [x] **终端全屏自适应** — 下载的 .py 运行时自动适应终端窗口大小，等比缩放居中；新增 `_get_terminal_size()`（Windows `GetConsoleScreenBufferInfo` 的 `srWindow`）/`_fit_frames()`/`_compose_screen()`/`_scale_ansi_line()`。
- [x] **可选音频播放** — `music.mp3` 放 .py 同目录自动检测并用系统工具播放（Windows MediaElement / macOS afplay）；`_start_audio()` + `_stop_audio()` 管理生命周期。
- [x] **ANSI 解析与编码** — `_parse_ansi_line()` / `_encode_ansi_line()` 确保缩放后颜色不丢失。
- [x] **Windows ANSI 支持** — `_enable_windows_ansi()` 调用 `kernel32.SetConsoleMode` 启用 VT100。

> ⚠️ **音频状态修正**：原 PRD §3.2 把「音频支持」列为本版不做，但上述 v1.0 修复日志**已实现 music.mp3 自动播放**。本版统一为「**音频已支持（v1.0 已实现，超出 MVP）**」。

---

## 附 B：参考项目

### 开发参考（灵感来源）
- **`Einswen/SalaryCat`**（GitHub）— 终端 cat 动画播放器。Termify 的 **half-block 渲染**、**color delta 编码**、**PyInstaller 独立二进制（一键包）** 思路直接来自它；其 README 的 `--half-block`、standalone binary、PyInstaller build 是 Termify 一键桌面包的范本。

### 竞品 / 对标（来自四视角分析）
- **`ASCII Magic`** — 在线纯前端 ASCII 动画工具，体验碾压本地工具（打开即用、支持视频、导出 MP4），但只「看」不「带走」；对标其「即用性」，Termify 用在线 Demo + 一键包拉平。
- **`AsciiCraft`** — 在线纯前端 ASCII/像素艺术生成器，交互体验好；同样不产出可运行终端文件；对标其「零门槛」，Termify 护城河在「可下载可运行可分享」。
- **`Monochora`** — Rust 写的 CLI ASCII 动画工具；对标其「命令行高性能」，Termify 用 Python 更易扩展、有 Web。
- **`ascii-animator`** — Python 库，可在代码中调用生成 ASCII 动画；对标其「库集成」，Termify 额外提供 Web + REST API。
- **`google/gif-for-cli`** — Google 出品、已停更的 GIF→终端动画工具；对标其「标杆定位」，Termify 在其停更后承接需求并扩展更多风格/输出。

---

## 附 C：上线与部署方案

### C.1 域名（**不需要新买域名**）
现有 `www.moonzj.com` 已部署个人站，Termify 用**子域名**即可，例如 `termify.moonzj.com`（或个人站下 `/termify` 路径）。DNS 加一条记录，反向代理（nginx / Caddy）把子域名指向 Flask 服务，**强制 HTTPS**。

### C.2 部署形态
- **Flask + gunicorn + Docker**，置于现有主机 / VPS。
- ⚠️ 内存任务存储（`TASKS` 全局字典）不适合多 worker：先用**单进程**（`gunicorn --workers 1`）或加简单持久化（见技术债 P2）。
- 前置 nginx / Caddy 反代 + HTTPS；Dockerfile 与部署文档为 P0 在线 Demo 配套交付物。

### C.3 面向 AI 的批量调用（核心集成价值）
Termify 已内置 REST API（`POST /api/upload`、`GET /api/preview/<id>`、`POST /api/generate`、`GET /api/download/<filename>`）。AI agent / 脚本用 HTTP 直接调用即可批量转换（示例见第六章 6.5）；本地无网环境用 CLI `python demo.py 图片.gif --charset all` 批量生成。**「AI 可程序化调用」是 Termify 的核心集成价值**，建议在 README / 文档突出。

### C.4 一键桌面包（小白「下载即走」）
参考 SalaryCat 的 PyInstaller build 方式，打包 **exe（Windows）/ AppImage（Linux）/ 通用二进制（macOS）**，让用户下载双击即用、无需 Python。命令范式（参考 SalaryCat README）：
```bash
py -m PyInstaller --onefile --name termify --add-data "sample.gif;." --add-data "music.mp3;." app.py
```
（Windows 用 `;` 分隔 `--add-data`，macOS/Linux 用 `:`）

---

*Termify PRD V2.0.0 | 2026-07-17 | 许清楚（产品经理）*
*基于 v1.0 PRD + 四视角产品分析修复更新；v1.0 迭代日志原样保留于附 A。纯文档，未改动仓库代码。*
