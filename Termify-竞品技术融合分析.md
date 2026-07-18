# Termify 竞品技术融合分析

> **版本**：V1.0 · 2026-07-17
> **作者**：寇豆码（软件工程师）
> **定位**：对 Termify 的 6 个主要竞品做「技术栈 + 实现 trick + 融合可行性」拆解，
> 重点回应 Termify 当前四大短板（浏览器实时 / 视频 / 性能 / 打包分发）。
> **性质**：纯技术分析，**未改动 Termify 任何代码**。
> **资料标注**：`[已确认]` 来自官方文档/源码/实测；`[推断]` 为基于公开信息的合理推测。

---

## 0. 核心结论速览（先给答案）

| 竞品技术亮点 | Termify 能否融 | 落地难度 | 优先级 | 一句话理由 |
|---|---|---|---|---|
| AsciiCraft 的 **WASM 引擎 + MediaRecorder 音视频导出** | 视频走**后端 ffmpeg**，不照搬 WASM | 中 | P1 | Termify 护城河是「可带走文件」，后端 ffmpeg 复用现有 Pillow 流水线最稳 |
| ASCII Magic 的 **纯浏览器实时预览** | 局部可融（流式预览） | 中 | P1 | 全量 WASM 会牺牲护城河；做「后端流式 + 可选轻量 WASM 预览」即可 |
| Monochora 的 **Rayon 并行 + 误差扩散抖动** | 间接可融（numpy 向量化 + 多进程） | 中 | P2 | Python 无 GIL 并行，用 numpy/多进程等效加速 |
| ascii-animator 的 **Animation 类 API 抽象** | 可融（已有 `termify.convert`） | 低 | P2 | Termify 引擎本就可 import，补一层高层动画 API 即可 |
| gif-for-cli 的 **单元格均值 + 自适应灰度分桶 + truecolor** | **强烈建议直接抄** | 低 | P0 | 自适应分桶显著改善色调，改动仅在 `charset.py` |
| SalaryCat 的 **ColorCache + 脏矩形 diff + PyInstaller CI** | **已部分借鉴，继续抄** | 低 | P0 | 性能与打包的最佳范本，几乎零成本 |

**一句话结论**：Termify 最该先融的三件事（按 ROI 排序）——
① **gif-for-cli 的自适应灰度分桶**（改几行，画质立升）；
② **SalaryCat 的 ColorCache + 脏矩形 diff + PyInstaller 矩阵构建**（性能与分发双收）；
③ **后端 ffmpeg 视频抽帧**接现有 `convert()`（补齐最大短板，不碰前端架构）。
全量「引擎编译成 WASM 跑浏览器」**不推荐**——会丢掉 Termify 唯一护城河（自包含可运行文件）。

---

## 1. 研究方法与关键术语

- **分析对象**：Termify 引擎（`termify/` 包）+ Web（`app.py`）+ 本地参考 `SalaryCat/`。
- **竞品资料来源**：各项目官网 FAQ、GitHub README、PyPI、deepwiki 源码解读、本地代码阅读；逐项标注 `[已确认]` / `[推断]`。
- **关键术语**：
  - *Cell（单元格）*：终端里「1 个字符」对应原图的 `cell_width × cell_height` 像素块；因字符高≈宽 2 倍，常取 `cell_w=1, cell_h=2`（half-block）或 `cell_w=3, cell_h=6`（gif-for-cli）。
  - *TrueColor*：`ESC[38;2;r;g;bm` 24 位前景色 ANSI。
  - *Color delta*：相邻 cell 颜色不变则不重发转义码（Termify 已落地）。
  - *脏矩形（dirty-rectangle）*：只重绘发生变化的行，而非整屏重绘。
  - *误差扩散抖动（error diffusion）*：Floyd–Steinberg / Atkinson / Stucki / Bayer，把量化误差分摊到邻域，减少色带。

---

## 2. 逐个竞品拆解

### 2.1 ASCII Magic（在线纯前端）

| 维度 | 内容 |
|---|---|
| **技术栈** | 纯浏览器，**HTML5 Canvas 2D**（核心），无服务器、无上传；导出 PNG/JPG/GIF/MP4 全在浏览器内；13+ 艺术风格 + 10 种可叠 post-effect `[已确认]` |
| **实现 trick** | ① `canvas.getContext('2d').getImageData()` 拿到 `Uint8ClampedArray`，逐 cell 取亮度映射到字符 ramp；② **零网络往返**→滑块/换风格即时重渲染；③ 视频：把 `<video>` 每帧 `drawImage` 到离屏 canvas 再逐帧处理；④ MP4 导出走浏览器内编码（推断为 `MediaRecorder`/`VideoEncoder`，类似 asciify.art 的 WebCodecs 路径） |
| **Termify 融合** | **直接抄：否（会丢掉护城河）。可融：实时预览思路**——把「全帧一次返回」改为 SSE/流式逐帧推送，前端边收边播，先见首帧。字符 ramp 的「可配置 + 实时拖拽」交互可借鉴 |
| **风险 / 工作量** | 中（需改 `app.py` 预览接口为流式 + 前端播放器适配） |

**核心伪代码（其实现思路）**：
```js
// ASCII Magic 类工具的典型实时管线
const ctx = canvas.getContext('2d');
function renderToAscii(source, W, H, cellW, cellH, RAMP) {
  ctx.drawImage(source, 0, 0, W, H);              // 图片或 video 当前帧
  const { data } = ctx.getImageData(0, 0, W, H);  // 像素数组
  const out = [];
  for (let y = 0; y < H; y += cellH) {
    let line = '';
    for (let x = 0; x < W; x += cellW) {
      const i = (y * W + x) * 4;
      const lum = 0.299*data[i] + 0.587*data[i+1] + 0.114*data[i+2];
      line += RAMP[Math.round(lum / 255 * (RAMP.length - 1))];
    }
    out.push(line);
  }
  return out;
}
// 视频：在 requestAnimationFrame / requestVideoFrameCallback 里反复调用
```

**为什么比本地工具「即时」**：本地工具要 `git clone → pip install → python xxx.py`；ASCII Magic 打开网址即跑，且处理 100% 在本地（隐私友好、无服务器瓶颈）。但**它只「看」不「带走」**——这是 Termify 的差异点。

---

### 2.2 AsciiCraft（在线纯前端，视频+音频）

| 维度 | 内容 |
|---|---|
| **技术栈** | 纯浏览器，**WebAssembly 驱动的转换引擎**（`[已确认]` asciicraft.com 明确写 "WebAssembly powered"）；72 字符集、实时预览；视频→ASCII 导出 **WebM / MP4（含原声，Premium）**；文本→ASCII 用 329 个 figlet 字体 `[已确认]` |
| **实现 trick** | ① **WASM 热循环**：像素→字符的密集映射放 WASM（Rust/C++/Go→WASM），比纯 JS 快 10–100×，支撑大图/视频实时；② **视频+音频导出**：`<video>`/摄像头 → canvas 逐帧 ASCII；`canvas.captureStream(fps)` 拿视频轨，Web Audio 取原声轨，`MediaRecorder` 把双轨 mux 成 WebM/MP4；③ 72 字符集查表 + 亮度/对比度/饱和度微调 |
| **Termify 融合** | **视频/音频导出思路可融（后端版）**；**WASM 引擎不照搬**。AsciiCraft 的「WASM + MediaRecorder 带音频导出」是浏览器方案，Termify 应改为**后端 ffmpeg 抽帧 + 后端合成**（见 §3.2）。若未来做浏览器端即时预览，可参考其 WASM 加速思路 |
| **风险 / 工作量** | 中（视频后端化中；若真上 WASM 则高） |

**浏览器端视频+音频导出伪代码（AsciiCraft 类方案）**：
```js
// 前端把 ASCII 画布 + 原声合成视频
const canvasStream = asciiCanvas.captureStream(30);        // 视频轨
const audioDest = audioCtx.createMediaStreamDestination();
sourceNode.connect(audioDest);                              // 原声轨
canvasStream.addTrack(audioDest.stream.getAudioTracks()[0]);
const rec = new MediaRecorder(canvasStream, { mimeType: 'video/webm;codecs=vp9' });
rec.ondataavailable = e => chunks.push(e.data);
rec.start();
// ...播放 ASCII 动画...
rec.stop();  // → Blob → 下载 WebM/MP4
```

---

### 2.3 Monochora（Rust CLI）

| 维度 | 内容 |
|---|---|
| **技术栈** | **Rust**；`gif` crate 解帧、`image` crate 缩放、`rayon` 数据并行；支持本地文件 + **URL 直接拉取转换**；误差扩散抖动（Atkinson/Bayer/Floyd–Steinberg/Stucki）；可导出彩色 ASCII 文本或「ASCII 字符 GIF」 `[已确认 README，源码未抓到→内部为推断]` |
| **实现 trick** | ① **Rayon 并行**：各帧相互独立 → `frames.par_iter().for_each(...)` 多核并行转换（零成本抽象、无 GIL）；② **智能尺寸计算**：按字符高宽比（≈1:2）反推目标列/行，避免拉伸；③ **误差扩散抖动**：把量化误差按权重分摊到右侧/下方邻域，平滑渐变 |
| **Termify 融合** | Rust 重写不现实；**并行与抖动的「思想」可融**：Python 用 `multiprocessing.Pool` 并行帧（gif-for-cli 范式）+ numpy 向量化映射等效加速；抖动可作为 `blocks` 风格的可选后处理 |
| **风险 / 工作量** | 中（并行简单；抖动需新增模块） |

**Monochora 式并行伪代码（Rust 思想 → Python 等价）**：
```python
# Rust: frames.par_iter().for_each(|f| convert(f))
# Python 等价（gif-for-cli 范式）:
from multiprocessing import Pool
def convert_frame(frame):
    scaled = frame.image.resize((w, h), Image.LANCZOS)
    return map_with_dithering(scaled, CHARSETS["blocks"]["chars"])
with Pool() as p:
    ascii_frames = p.map(convert_frame, prepared_frames)
```

**为什么快**：① 编译型语言 + SIMD 友好的 `image` resize；② Rayon work-stealing 吃满多核；③ 无 Python GIL/解释开销；④ 抖动在像素数组上原地操作，缓存友好。

---

### 2.4 ascii-animator（Python 库）

| 维度 | 内容 |
|---|---|
| **技术栈** | **纯 Python**（`requires-python >=3.9`）；CLI `ascii-art-animator` 内部用 `ascii-magic` 转换 + `list2term` 上屏；`pip install ascii_animator` 即可用 `[已确认 PyPI/GitHub]` |
| **实现 trick** | **面向开发者的干净 API 抽象**：`Animator(animation=..., speed=..., max_loops=...)`；用户继承 `Animation` 实现 `grid` 属性 + `cycle()` 方法即可造自定义动画；`cycle()` 也支持 generator 形式（逐步可视化）；内置排序/矩阵/生命游戏等示例 |
| **Termify 融合** | **可融（低成本）**。Termify 引擎 `from termify import convert` 已可 import，但**缺「高层动画 API」叙事**。可补一个 `termify.animations` 高层接口（类似 `Animation` 抽象），让 AI agent / 开发者用数据造动画，强化「库集成」定位（对标 ascii-animator，且 Termify 还有 Web+REST 双形态优势） |
| **风险 / 工作量** | 低（加一个薄抽象层 + 文档） |

**ascii-animator 的 API 形态（值得借鉴）**：
```python
from ascii_animator import Animator, Animation, Speed
class Bouncer(Animation):
    def __init__(self, width=20):
        self.x_size = width; self.position = 0; self.direction = 1
        self._grid = [[" "] * width]; self._draw()
    @property
    def grid(self): return self._grid
    def cycle(self):
        if self.position in (0, self.x_size-1): self.direction *= -1
        self.position += self.direction; self._draw()
        return self.position == 0
Animator(animation=Bouncer(), speed=Speed.NORMAL, max_loops=3)
```

---

### 2.5 google/gif-for-cli（Python，已停更的标杆）

| 维度 | 内容 |
|---|---|
| **技术栈** | **Python**；`ffmpeg` 抽帧+缩放（`scale=w=cols*cell_w:h=rows*cell_h:force_original_aspect_ratio=decrease`）；`multiprocessing.Pool` 并行帧；`x256` 做 256 色映射；`Pillow` 处理 `[已确认 源码]` |
| **实现 trick** | **① 单元格均值（cell averaging）**：1 字符 = `cell_w×cell_h` 像素块的**平均色**，而非 1px→1char，更贴合终端字符物理尺寸；**② 自适应灰度分桶（重点）**：统计所有 cell 灰度直方图，按 `budget = 总cell数 / 字符数` 等频分桶，使字符分布均匀、不偏亮/偏暗；**③ `memoize` + 多进程**加速；**④ truecolor cell**：`ESC[38;2;r;g;bm` + 一个块字符（`STORED_CELL_CHAR`） |
| **Termify 融合** | **强烈建议直接抄 ② 自适应分桶**（改动仅在 `charset.py` 的灰度映射，画质提升明显，尤其处理过亮/过暗图）；① 单元格均值可与现有 letterbox 缩放结合；④ truecolor 思路 Termify 的 `blocks` 已是近似实现 |
| **风险 / 工作量** | 低（分桶逻辑约 20 行） |

**gif-for-cli 的两个核心 trick 源码还原**：
```python
# Trick A：单元格平均色（cell averaging）
def get_avg_for_em(px, x, y, cell_h, cell_w):
    pixels = [px[sx, sy] for sy in range(y, y+cell_h)
                          for sx in range(x, x+cell_w)]
    return [round(n) for n in map(mean, zip(*pixels))]   # 每块 RGB 均值

# Trick B：自适应灰度分桶（直方图等频映射，避免偏色）
num_cells_per_char = len(chars_nocolor) / len(NOCOLOR_CHARS)
char_counts = OrderedDict()
for cell in sorted(chars_nocolor):
    char_counts[cell] = char_counts.get(cell, 0) + 1
cur_count = 0; cur_char_idx = 0; char_idxs = {}
for cell, n in char_counts.items():
    if cur_count > num_cells_per_char:
        cur_count = 0; cur_char_idx += 1
    char_idxs[cell] = cur_char_idx; cur_count += n
# 之后 chars_nocolor[i] -> NOCOLOR_CHARS[char_idxs[gray]]
```
> 注：Termify 当前是 `idx = gray * (n-1) // 255` 的**线性映射**，暗图会全挤在密字符、亮图全挤在疏字符。换成等频分桶后，明暗细节都拉开。

---

### 2.6 SalaryCat（本地参考，Termify 已部分借鉴）

| 维度 | 内容 |
|---|---|
| **技术栈** | **Python + Pillow**（仅依赖 Pillow）；`TerminalRenderer` + `gif_loader` + `audio_player`；PyInstaller 一键二进制；GitHub Actions 矩阵构建 `[已确认 本地代码]` |
| **实现 trick** | **① ColorCache**：`dict[(r,g,b)→ANSI码]` 记忆化，重复颜色直接查表，避免每 cell 拼字符串；**② 脏矩形 diff**：`draw()` 只重绘 `previous_buffer[row] != line` 的行（`ESC[row;1H{line}`），不全屏重绘；**③ Floyd–Steinberg 抖动 + alpha 清理 + `crop_frames_to_content` 裁掉透明边**；**④ `sleep_until` 用 `time.perf_counter()` 做帧精确调度**；**⑤ PyInstaller `--onefile --add-data` + `sys._MEIPASS` 资源解析 + Actions 矩阵（macOS arm/intel + Windows）自动发 Release** |
| **Termify 融合** | **已借鉴 half-block / color delta / PyInstaller 思路**。还能继续抄：① ColorCache 记忆化（Termify `charset.py` 每 cell 现拼 ANSI，可加缓存）；② 脏矩形 diff（Termify `.py` 播放器每帧全屏重绘，可改为 diff）；③ 内容裁剪 + 抖动选项；④ 帧精确调度器；⑤ 直接复用其 CI 矩阵脚本做 Termify 一键包 |
| **风险 / 工作量** | 低（均为局部增强，无架构改动） |

**SalaryCat 脏矩形 diff 伪代码（值得 Termify `.py` 播放器借鉴）**：
```python
def draw(self, buffer, full_redraw=False):
    force = full_redraw or len(self.previous_buffer) != len(buffer)
    updates = []
    for row, line in enumerate(buffer, start=1):
        if force or self.previous_buffer[row-1] != line:
            updates.append(f"\x1b[{row};1H{line}")   # 只动变化的行
    if updates:
        self.stream.write("".join(updates)); self.stream.flush()
        self.previous_buffer = buffer
```

---

## 3. Termify 四大短板 · 技术融合可行性清单

### 3.1 浏览器端实时转换（对标 ASCII Magic）

**现状**：Termify 是「后端 Flask 抽帧→全帧 JSON 一次返回→前端 `<pre>` 播放」。200×60 全帧 ≈6MB 单请求，首帧延迟高（§4.4 Badcase 4）。

**三条路线对比**：

| 路线 | 做法 | 可行性 | 代价 |
|---|---|---|---|
| A. 全量引擎编译 WASM | Python→Pyodide 或 Rust 重写→浏览器跑 | ❌ 不推荐 | 丢护城河（只预览不能带走）；Pyodide ~10MB 慢；Rust 重写成本高 |
| B. 后端流式预览 | SSE/分块逐帧推送，前端边收边播 | ✅ 推荐 | 改 `app.py` 预览接口 + 前端播放器 |
| C. 轻量 WASM 仅预览 | 只把「小图实时预览」放 WASM，导出仍走后端 | ◐ 远期 | 维护两套引擎 |

**结论**：**走 B（后端流式）**，不碰 A。Termify 的护城河是「自包含可运行文件」，全量 WASM 会把它变成又一个「只能看」的在线工具，正好撞上 ASCII Magic / AsciiCraft 的强项。**可选远期做 C**：用 WASM 给「小尺寸实时拖拽预览」加速，导出文件仍由后端生成——既借到浏览器实时性，又不丢护城河。

**落地改动点**：`app.py` 的 `/api/preview` 改为 SSE 流式首帧优先返回；`static/js/app.js` 播放器支持增量帧缓冲。

---

### 3.2 视频处理（对标 AsciiCraft）

**两条路线对比**：

| 路线 | 做法 | 可行性 | 说明 |
|---|---|---|---|
| 后端 ffmpeg | `ffmpeg`/opencv 抽帧 → 现有 `convert()` | ✅ **推荐** | 复用 Pillow 流水线，改动最小；Termify 本就是 Python 后端 |
| 前端 ffmpeg.wasm | 浏览器内解码视频 | ❌ 不推荐 | 包体大、需 COOP/COEP、慢；且仍不产「可带走文件」 |

**结论**：**后端 ffmpeg（或 imageio-ffmpeg / opencv）**。具体接法——把视频按帧抽成 RGBA 序列后，**直接喂给现有 `extract_frames` 之后的 `scale_frame + render_frame` 流水线**，几乎不用改引擎核心。这同时补上 PRD §8「视频直传 P1」短板。

**落地伪代码**：
```python
# 在 frames.py 增加视频入口，复用现有 scale/render
def extract_frames_from_video(path, fps=15):
    # imageio_ffmpeg / cv2 逐帧读出 -> list[(RGBA, duration)]
    frames = []
    for rgb, t in decode_with_ffmpeg(path, fps):
        frames.append((Image.fromarray(rgb).convert("RGBA"), t))
    return frames
# engine.convert() 上游判断：是视频则走 extract_frames_from_video，其余不变
```

---

### 3.3 性能优化（对标 Monochora / gif-for-cli）

**瓶颈定位**：Termify 热路径是 `charset.py` 里**纯 Python 逐像素/逐 cell 字符映射**（双重 for 循环 + 每 cell 拼 ANSI 串）。缩放已用 LANCZOS（不慢），慢在映射。

**加速点清单（按 ROI 排序）**：

| # | 加速点 | 来源灵感 | 改动文件 | 预期收益 | 工作量 |
|---|---|---|---|---|---|
| 1 | **numpy 向量化映射**：`lum = np.dot(rgb, [.299,.587,.114])` → `np.searchsorted(ramp_lut, lum)` 批量出字符索引 | Monochora 思想 + numpy | `charset.py` | 10–50× | 中 |
| 2 | **自适应灰度分桶**（§2.5 Trick B） | gif-for-cli | `charset.py` | 画质↑（非纯速度） | 低 |
| 3 | **ColorCache 记忆化 ANSI 串** | SalaryCat | `charset.py` | 字符串分配↓ | 低 |
| 4 | **多进程并行帧** | gif-for-cli / Monochora | `engine.py` | 多核利用 | 中 |
| 5 | **脏矩形 diff 播放器** | SalaryCat | `output/python.py` | 终端重绘量↓ | 低 |
| 6 | **帧精确调度器**（`perf_counter`） | SalaryCat | `output/python.py` | 播放更稳 | 低 |

**结论**：**先做 2+3+5+6（全是低工作量、局部改动，立竿见影），再上 1（numpy 向量化，最大提速），最后 4（多进程）**。Python 无 GIL 并行，但 numpy 单进程已能吃掉大部分热点，多进程是锦上添花。

---

### 3.4 打包分发（对标 SalaryCat）

**两类交付物 + 最佳实践**：

**A. 在线 Demo（公开站点）**——PRD P0
- `Flask + gunicorn --workers 1`（内存 `TASKS` 字典不适多 worker，PRD §C.2 已说明）+ Docker + Caddy/ngrok 反代 + HTTPS。
- 加 API 限流 + 任务 TTL 持久化（PRD 技术债 P2）防内存泄漏。

**B. 一键桌面包**——PRD P0，直接复用 SalaryCat 范本
- `PyInstaller --onefile --name termify --add-data "music.mp3;." --add-data "sample.gif;." app.py`（Windows 用 `;`）。
- **关键前提**：`termify/` 引擎已与 Flask 解耦（无 `app.py` 依赖），可独立打包；但 `app.py` 自带 Web 服务，打包时建议提供一个 `cli.py` 入口（或直接打 `demo.py`）作为无服务器形态。
- **CI 矩阵**（抄 SalaryCat `build-binaries.yml`）：`macos-arm64 / macos-intel / windows-latest` 三个 job → 自动上传 GitHub Release。
- **体积优化**：`--onefile` + UPX 压缩；`requirements.txt` 仅 Pillow，依赖极小 → 二进制可控在十几 MB。

**结论**：**低风险高回报，直接照 SalaryCat 抄 CI + PyInstaller spec 即可**。这是 Termify 拉平「启动门槛」短板（PRD 问题#1）的最快路径。

---

## 4. 融合路线建议（按优先级排序）

| 优先级 | 动作 | 竞品来源 | 涉及文件 | 工作量 | 价值 |
|---|---|---|---|---|---|
| **P0-1** | 自适应灰度分桶 | gif-for-cli | `charset.py` | 低 | 画质立升 |
| **P0-2** | ColorCache + 脏矩形 diff + 帧精确调度 | SalaryCat | `charset.py` / `output/python.py` | 低 | 性能+体验 |
| **P0-3** | PyInstaller spec + CI 矩阵一键包 | SalaryCat | 新增 `tools/build.spec` / `.github/workflows` | 低 | 补齐分发短板 |
| **P1-1** | 后端 ffmpeg 视频抽帧接 `convert()` | AsciiCraft(后端化) | `frames.py` / `engine.py` | 中 | 补齐视频短板 |
| **P1-2** | 后端流式预览（SSE 首帧优先） | ASCII Magic(思路) | `app.py` / `static/js/app.js` | 中 | 降首帧延迟 |
| **P2-1** | numpy 向量化映射 | Monochora 思想 | `charset.py` | 中 | 最大提速 |
| **P2-2** | 多进程并行帧 | gif-for-cli / Monochora | `engine.py` | 中 | 多核利用 |
| **P2-3** | 高层 `termify.animations` API | ascii-animator | 新增模块 | 低 | 强化库定位 |
| **远期** | 轻量 WASM 仅做小图实时预览 | AsciiCraft | 新增 wasm 分支 | 高 | 体验增强（不丢护城河） |

---

## 5. 附录

### 5.1 Termify 现状快照（分析基准）

| 文件 | 职责 | 相对竞品的差距 |
|---|---|---|
| `termify/frames.py` | 抽帧（`ImageSequence`）、LANCZOS 缩放 + letterbox、20MB 上限 | 无视频抽帧；无内容裁剪 |
| `termify/charset.py` | 5 种字符集 + 逐 cell 映射（线性灰度） | 线性映射偏色；无 ColorCache；无抖动 |
| `termify/engine.py` | `convert()` 编排；`FrameSequence` 为 ANSI 字符串 | 缺像素级出口（PRD M3）；无并行 |
| `termify/output/python.py` | `.py` 播放器（全屏自适应 + 音频） | 每帧全屏重绘（无 diff）；`time.sleep` 分块调度 |
| `termify/output/html.py` | `.html` 自包含播放器 | blocks 走 canvas，其余预渲染 OK |
| `termify/ansi_to_html.py` | ANSI→HTML span | 已实现，质量好 |
| `app.py` | Flask 三端点 + 内存 `TASKS` | 全帧一次返回；无流式；无 TTL；单 worker 限制 |

### 5.2 参考与引用

- ASCII Magic 官网 FAQ：<https://www.ascii-magic.com/>（Canvas 实时、无服务器、视频/MP4 导出 `[已确认]`）
- AsciiCraft 官网：<https://asciicraft.com/>（WebAssembly 引擎、视频 WebM/MP4 带音频导出 `[已确认]`）
- Monochora GitHub：<https://github.com/ralphmodales/monochora>（Rust + rayon 并行 + 误差扩散抖动 `[已确认 README]`）
- ascii-animator PyPI：<https://pypi.org/project/ascii-animator/>（Python 库 + `Animation` 类 API `[已确认]`）
- google/gif-for-cli 源码：<https://github.com/google/gif-for-cli>（ffmpeg 抽帧、cell averaging、自适应分桶、truecolor `[已确认 源码]`）
- SalaryCat 本地代码：`E:/Desktop/工作/SalaryCat/`（half-block、ColorCache、脏矩形、PyInstaller CI `[已确认 本地]`）
- Termify 本地代码与 PRD：`E:/Desktop/工作/观猹/Termify/`（PRD V2.0.0）

---

*本文档为纯技术分析，未修改 Termify 仓库任何代码。所有「融合可行性」均为工程建议，落地前需结合 PRD 里程碑（M0–M3）排期。*
