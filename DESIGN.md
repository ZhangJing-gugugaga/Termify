# DESIGN.md — Termify 设计契约

> 本文件是 Termify 全部 UI 改动的**唯一事实来源**。任何 agent 或人类在动 `static/css/`、`static/js/`、`templates/` 之前，必须先在本文件里找到对应规则；找不到就先加规则，再加代码。
>
> 本文的两个性质必须分清：**记录既有事实**（代码里已经这样写，规则只是把它文字化）与**已拍板决策**（方向定了但代码尚未完全落地）。二者用 confidence 字段区分，不得混淆。

---

## 0. 证据口径与图例

### 0.1 每条规则的字段

| 字段 | 含义 |
|---|---|
| **role** | 这个值在界面里承担什么角色 |
| **value** | 具体数值 / 颜色 / 时长 / 选择器行为 |
| **source** | `文件:行号`，必须能 grep 命中原文 |
| **scope** | 作用范围 |
| **recurrence** | 在代码里重复的次数（grep 实测数字，命令见附录 A） |
| **confidence** | 见 0.2 |

### 0.2 confidence 四值

| 值 | 判据 |
|---|---|
| `confirmed` | 代码里实锤——source 行直接证明，无解释余地 |
| `observed` | 多处一致——单行不足以证明，但 N 处写法收敛到同一模式 |
| `inferred` | 推测——从代码形状推断意图，代码本身没写死 |
| `decided` | 已拍板的设计决策——证据来自项目决策而非现存代码；代码可能尚未落地或部分落地 |

> `decided` 是本地扩展的第四个值。用 `confirmed` 冒充已拍板决策会污染「实锤」的含义——一个后来者读到 `confirmed` 会以为代码已经这样写了，而实际没有。宁可多一个值，不混淆两种权威。

### 0.3 三条纪律

1. **prefer no finding to an unsupported one** —— 没有 `文件:行号` 的规则不写。宁可这一节短。
2. **absence of design documentation is not a finding** —— 「原来没有 DESIGN.md」不是问题，不写进正文。
3. **数字必须实测** —— 所有计数、对比度、行数均为本次 grep / python 实算，复现命令见附录 A。任务书给的数字亦已逐条复核，分歧单列于对应小节。

---

## 1. 品牌与身份

### 1.1 三层叠加身份：网格 → 磷光 → 扫描线

| role | value | source | scope | recurrence | confidence |
|---|---|---|---|---|---|
| 底层网格 | `body::before`，双向 `linear-gradient`，`background-size: 60px 60px`，`opacity: 0.3`，`pointer-events: none` | `static/css/app.css:34-45` | 全站（fixed inset 0） | 1 | confirmed |
| CRT 扫描线 | `body::after`，`repeating-linear-gradient(0deg, ...)`，2px 透明 / 2px `rgba(0,0,0,0.03)` 交替 | `static/css/app.css:48-62` | 全站（fixed inset 0） | 1 | confirmed |
| 主色 | `--green: #00ff41` | `static/css/tokens.css:30` | 全站 | 140 次 `var(--green)` | confirmed |

### 1.2 设计意图（inferred，依据在代码形状里）

- **网格 = 「显示器支架」**：60px 是 12 的倍数、也接近 `--space-16`(64px)，用 `--border-dim`（最暗的边框色）而非独立色值，说明作者要的是「几乎看不见的坐标系」，不是装饰纹理。opacity 0.3 进一步压低。
- **扫描线 = 「显像管」**：横向 2px 条纹、alpha 仅 0.03、`z-index: 9999` 覆盖全部内容——它定义的不是某个组件，而是「整块屏幕」这一层。这是唯一一个盖在所有 UI 之上的层（见 §7）。
- **绿色磷光 = 「信号」**：`#00ff41` 是 P1 磷光绿的高饱和近似。它在 `--bg-surface` 上 13.27:1（见 §3.3），是全调色板里除了 `--text-primary` 外对比度最高的强调色——所以它独占「可交互 / 活跃 / 输出正确」的语义。

### 1.3 不可推翻

网格层与扫描线是**既有资产**（`confirmed`，见 1.1 source）。任何改版不得删除 `body::before` / `body::after` 这两条规则。允许调 `opacity`，不允许换掉 `repeating-linear-gradient` 的 2px/2px 节奏——那是 CRT 可辨识度的全部来源。

> **与任务书的分歧（必须记录）**：任务书称「扫描线没关」属于 reduced-motion 缺口。实测 `body::after`（`app.css:48-62`）**没有任何 `animation` 声明**，它是静态叠加层。它有一条 `will-change: transform`（`app.css:61`）但全文件无对应 `transform`，是一条死提示。因此扫描线**不需要** reduced-motion 守卫，真正漏关的是别的（见 §6.4）。本节按实测写，未按任务书措辞写。

### 1.4 扫描线开关是死的（observed）

| role | value | source | scope | recurrence | confidence |
|---|---|---|---|---|---|
| 开关写入 | JS 写 `document.body.style.setProperty('--scanline-opacity', '1'/'0')` | `static/js/app.js:1623`、`templates/index.html:551`、`templates/index.html:558`、`templates/gallery.html:352`、`templates/view_work.html:1192` | 5 处 | 5 | confirmed |
| 开关读取 | **无任何 CSS 规则消费 `--scanline-opacity`** | grep `scanline-opacity static/css/` 返回空 | `static/css/` 全目录 | 0 | confirmed |

结论：tweaks 面板的「扫描线」开关点了不生效——变量被写入但无人读取。改这块时要么在 `body::after` 上补 `opacity: var(--scanline-opacity, 1)`，要么把开关删掉；不要留着假控件（反模式见 §10 第 9 条）。

---

## 2. Typography

### 2.1 全 mono（decided）

| role | value | source | scope | recurrence | confidence |
|---|---|---|---|---|---|
| 字体族 | 只保留 `--font-mono`，删除 `--font-sans` 与 Space Grotesk | 现存 `static/css/tokens.css:53`（`--font-sans`）、`static/css/tokens.css:11-17`（@font-face）、`static/css/app.css:20`（唯一消费者） | 全站 | `var(--font-mono)` 122 次 vs `var(--font-sans)` **1 次** | decided |
| 可变字重 | `font-weight: 100 800`（单文件可变字重，100–800 连续可取） | `static/css/tokens.css:7` | 全站 | 1 | confirmed |
| 字体文件 | 自托管 `../fonts/web/JetBrainsMono-VF.ttf`，`font-display: swap` | `static/css/tokens.css:4-10` | 全站 | 1 | confirmed |

**删除 Space Grotesk 的落地清单（三处，缺一不可）**：

1. 删 `static/css/tokens.css:11-17`（整段 `@font-face { font-family: 'Space Grotesk'; }`）
2. 删 `static/css/tokens.css:53`（`--font-sans: 'Space Grotesk', system-ui, sans-serif;`）
3. 改 `static/css/app.css:20` —— `body { font-family: var(--font-sans); }` → `var(--font-mono)`

第 3 步会让**正文默认变成等宽**。这不是副作用，这正是「全 mono」的定义。同时注意 `static/css/app.css:15` 的 `html { font-size: 16px; }` 是所有 `rem` 的基准，删字体不碰它。

> 自托管理由已写在 `static/css/tokens.css:1-3` 的注释里（CDN 在国内手机端加载失败会回退到系统等宽，系统等宽缺块字符/盲文/几何字形，宽度不一导致整行漂移）。这条注释是字符画区域的命脉，改字体时必须理解它。

### 2.2 字号实测清单（grep 实测，非估算）

**统计口径与总数**（两个口径都给，便于核对）：

```
$ grep -o 'font-size' static/css/app.css | wc -l     → 146
$ grep -o 'font-size' static/css/text_art.css | wc -l → 31
合计 177 条 font-size 声明
```

按单位拆分后完全对齐：

| 单位 | app.css | text_art.css | 小计 |
|---|---:|---:|---:|
| `px` 字面量 | 122 | 2 | **124** |
| `rem` | 22 | 29 | 51 |
| `clamp()` | 2 | 0 | 2 |
| **合计** | **146** | **31** | **177** |

`122 + 22 + 2 = 146` ✓　`2 + 29 = 31` ✓　`124 + 51 + 2 = 177` ✓

**px 字面量清单（124 条，逐值）**：

| 值 | 次数 | 典型用途（实测选择器） | 建议映射 token |
|---|---:|---|---|
| `13px` | **25** | `.nav-links a`、`.terminal-body`、`#toast`、`.admin-tab`、`.export-confirm`、`.gallery-form-input` | `--fs-md` |
| `12px` | **24** | `.hero-badge`、`.nav-links`、`.footer-text`、`.style-name`、`.terminal-title`、`.url-fetch-input` | `--fs-sm` |
| `11px` | **23** | `.format-tag`、`.style-desc`、`.style-note`、`.gallery-card-badge`、`.render-chip`、`.admin-table th` | `--fs-xs` |
| `14px` | **15** | `.logo-icon`、`.upload-text p`、`.feature-desc`、`.gallery-card-title`、`.download-btn` | `--fs-base` |
| `10px` | **12** | `.terminal-body`（兜底）、`.palette-hint`、`.color-picker-label`、`.size-meta`、`.termify-modal-hint` | `--fs-2xs` |
| `18px` | 5 | `.hero-subtitle`、`.gallery-modal-title`、`.tweaks-close`、`.view-banner-close` | `--fs-xl` |
| `16px` | 5 | `html`（根）、`.upload-text h3`、`.feature-icon-box`、`.cc-preview-strip` | `--fs-lg` |
| `15px` | 4 | `.studio-sub`、`.section-title-en`、`.feature-title`、`.export-modal-title` | `--fs-lg` |
| `9px` | 2 | `.size-btn small`、`.tweak-hint` | `--fs-2xs` |
| `28px` | 2 | `.section-title`、`.admin-login-box h1` | `--fs-3xl` |
| `20px` | 2 | `.logo-text`、`.termify-modal-close` | `--fs-2xl` |
| `8px` | 1 | `.style-preview`（字符画缩略框，4:3 内塞整幅 ASCII） | `--fs-2xs`（例外，见下） |
| `24px` | 1 | `.view-banner-title` | `--fs-2xl` |
| `22px` | 1 | `.gallery-modal-close` | `--fs-2xl` |
| **合计** | **124** | | |

`text_art.css` 的 2 条 px：`40px`（`.ta-title`，`static/css/text_art.css:18`）、`8px`（`static/css/text_art.css:403`）。

**出现次数 ≥10 的 5 个值**：`13px`(25)、`12px`(24)、`11px`(23)、`14px`(15)、`10px`(12)，合计 99 条，占全部 124 条 px 的 **79.8%**。这五个值就是「真实在用的字阶」，其余 9 个值（8/9/15/16/18/20/22/24/28）合计 23 条，加上 `text_art.css` 的 2 条（40/8）构成标题与图标尺寸。

### 2.3 token 化建议（decided，10 档）

```css
--fs-2xs: 10px;   /* 吸收 8 / 9 / 10 —— 徽标、密集提示、字符画缩略 */
--fs-xs:  11px;   /* 11 —— 标签、说明、角标 */
--fs-sm:  12px;   /* 12 —— 次级 UI 文本、导航、卡片标题行 */
--fs-md:  13px;   /* 13 —— 终端正文、表单、Toast（最常用） */
--fs-base:14px;   /* 14 —— 正文默认 */
--fs-lg:  16px;   /* 吸收 15 / 16 —— 区块小标题、图标盒 */
--fs-xl:  18px;   /* 18 —— hero 副标题、弹窗标题 */
--fs-2xl: 24px;   /* 吸收 20 / 22 / 24 —— 大标题、关闭按钮 */
--fs-3xl: 28px;   /* 28 —— section-title */
--fs-4xl: 40px;   /* 40 —— /text-art 页标题 */
```

两处 `clamp()` 保留原样、不进字阶：
- `static/css/app.css:207` — `.hero h1 { font-size: clamp(36px, 6vw, 64px); }`
- `static/css/app.css:1703` — `.gallery-hero h1 { font-size: clamp(28px, 5vw, 48px); }`

**全库只剩 3 条保留的字面 px 字号，除此之外禁止新增字面值**（以下行号为 P0+P1 落地后的现行值）：

- `static/css/app.css:15` — `html { font-size: 16px; }`：这是 rem 基准，**禁止 token 化**，改它会让全站 rem 字号一起漂移。
- `static/css/app.css:583` — `.style-preview` 的 8px：该框固定 `aspect-ratio: 4/3`（`app.css:576`）且 `overflow: hidden`（`app.css:585`），字号决定的是一个缩略视窗里能塞下多少字符列；把它抬到 10px 会让预览被裁掉右边缘。改动此项必须同时复核视觉。
- `static/css/text_art.css:399` — `.ta-fw-art` 的 8px：`static/js/text_art.js:686` 有硬编码 `var base = 8;` 参与缩放公式 `scale = Math.min(1, w / (cols * base * ch), h / (rows * base * lh))`，只改 CSS 不改 JS 会让实际字形比算出的放大大 25%，从而溢出裁切。这条必须与 JS 同步才能动。

### 2.4 rem 的现状（observed）

`app.css` 有 22 条 rem、`text_art.css` 有 29 条 rem，且值域涣散（0.42 / 0.55 / 0.6 / 0.62 / 0.7 / 0.72 / 0.75 / 0.78 / 0.8 / 0.82 / 0.84 / 0.85 / 0.86 / 0.88 / 0.9 / 0.92 / 0.95 / 0.98rem，共 18 个离散值）。换算回 16px 基准后落在 6.7–15.7px 之间，与 §2.2 的 px 阶梯大量重叠但**不对齐任何一档**。

规则：**新代码一律用 px 值走 `--fs-*` token**，不再新增 rem 字号。存量 rem 在触碰对应选择器时才就近归并，不做批量替换——批量替换 51 条 rem 会改变每一个组件的高度和换行，回归面远大于收益。

### 2.5 字重（observed）

实测四种为主：`400`（默认，未写则不写）、`500`（`.style-name`、`.studio-sub`）、`600`（`.section-title`、`.feature-title`、`.download-btn`、`.url-fetch-btn`）、`700`（`.logo-icon`、`.hero h1`、`.ta-title`）。可变字重范围 100–800 允许更细的取值，但**不要在正文里用 <400**：细字重下笔画会碎，而 `--text-dim` 在 `--bg-surface` 上仅 3.06:1（见 §3.3），本就处在临界。

---

## 3. Color

### 3.1 全量 token（抄自 `static/css/tokens.css:19-73`）

**背景（5 档）**

| token | 值 | source | 用途 | 引用次数 |
|---|---|---|---:|---|
| `--bg-deep` | `#060a10` | `tokens.css:20` | `body` 最底层 | 1 |
| `--bg-base` | `#0a0e14` | `tokens.css:21` | 终端容器、输入框 | 23 |
| `--bg-surface` | `#111620` | `tokens.css:22` | **卡片默认底**（全部卡片） | 20 |
| `--bg-elevated` | `#1a1e24` | `tokens.css:23` | 浮层、tweaks 面板 | 10 |
| `--bg-hover` | `#222830` | `tokens.css:24` | 骨架屏、轨道 | 7 |

**边框（3 档，严格递增）**

| token | 值 | source | 用途 | 引用次数 |
|---|---|---|---:|---|
| `--border-dim` | `#1e2530` | `tokens.css:26` | 卡片默认边框、分隔线、网格线 | **49** |
| `--border-subtle` | `#2a3040` | `tokens.css:27` | 输入框、终端窗边框 | 35 |
| `--border-active` | `#3a4555` | `tokens.css:28` | **hover 态边框** | 14 |

**语义色**

| token | 值 | source | 引用次数 | 语义 |
|---|---|---|---:|---|
| `--green` | `#00ff41` | `tokens.css:30` | **140** | 主色 = 可交互 / 活跃 / 正确 |
| `--green-dim` | `#00cc33` | `tokens.css:31` | 15 | 次级绿（hover 边框） |
| `--green-glow` | `rgba(0,255,65,0.15)` | `tokens.css:32` | 38 | 光晕（只做装饰，见 §10 第 1 条） |
| `--green-text` | `#00ff41` | `tokens.css:33` | 1 | 与 `--green` 同值；唯一引用在 `text_art.css:406` |
| `--amber` | `#ffb000` | `tokens.css:35` | 5 | 警告 |
| `--amber-dim` | `#cc8d00` | `tokens.css:36` | **0** | 死 token |
| `--amber-glow` | `rgba(255,176,0,0.12)` | `tokens.css:37` | 0 | 死 token |
| `--cyan` | `#00d4ff` | `tokens.css:39` | 2 | 信息（braille 预览） |
| `--cyan-dim` | `#00a8cc` | `tokens.css:40` | **0** | 死 token |
| `--red` | `#ff3b30` | `tokens.css:42` | 13 | 危险 / 举报 / halftone 预览 |
| `--red-dim` | `#cc2f26` | `tokens.css:43` | **0** | 死 token |

**文本色**

| token | 值 | source | 引用次数 |
|---|---|---|---:|
| `--text-primary` | `#e0e6ed` | `tokens.css:45` | 31 |
| `--text-secondary` | `#8899aa` | `tokens.css:46` | 45 |
| `--text-dim` | `#556677` | `tokens.css:47` | **69** |
| `--text-inverse` | `#0a0e14` | `tokens.css:48` | 3 |

**其它**

| 类别 | token 与值 | source |
|---|---|---|
| 字体 | `--font-mono`（7 段栈）、`--font-sans`（待删） | `tokens.css:50-53` |
| 圆角 | `--radius-sm: 2px` / `--radius-md: 4px` / `--radius-lg: 8px`（引用 25 / 33 / 6） | `tokens.css:55-57` |
| 间距 | `--space-1..20`（见 §4） | `tokens.css:59-69` |
| 动效 | `--transition-fast: 120ms ease-out` / `--transition-base: 200ms ease-out` / `--transition-slow: 350ms ease-out` | `tokens.css:71-73` |

**四个死 token 与一个同值别名**（`confirmed`，grep 实测）：`--amber-dim`、`--amber-glow`、`--cyan-dim`、`--red-dim` 引用数均为 **0**；`--green-text` 与 `--green` 同值 `#00ff41`，引用数 **1**（`text_art.css:406`）。新增代码**不得使用**这四个死 token；`--green-text` 虽被用了一次，但它与主色同值——改 `--green` 的色相不会带着它走，等于埋一个只在一个选择器上不生效的改色。新增代码一律直接写 `var(--green)`，不造同值别名。

### 3.2 硬编码色：口径

`app.css` + `text_art.css` 里 tokens 之外的硬编码 hex 共 **29 处**、`rgba()` 字面量共 **36 处**。分布中值得点名的：

| hex | 次数 | 说明 |
|---|---:|---|
| `#00ff41` | 8 | **`--green` 的字面重复**——必须改回 `var(--green)` |
| `#9aa4b2` | 4 | `text_art.css` 内联的灰，未 token 化 |
| `#0a0e14` | 4 | `--bg-base` / `--text-inverse` 的字面重复 |
| `#8b949e` | 2 | 作为 `var(--text-dim, #8b949e)` 的**兜底值**——兜底色与 `--text-dim` 真实值 `#556677` 相差极大，一旦 token 丢失会静默变亮 |

规则：`var(--token, #fallback)` 形态的兜底值必须与 token 真实值**同值**，否则等于埋一个静默视觉漂移。

### 3.3 WCAG 对比度实测（python 复算，公式见附录 A12）

相对亮度按 WCAG 2.x：单通道 `c/12.92`（`c ≤ 0.04045`）否则 `((c+0.055)/1.055)^2.4`；`L = 0.2126R + 0.7152G + 0.0722B`；`CR = (Lhi+0.05)/(Llo+0.05)`。

**文本色 × 五级背景全矩阵**

| 前景 | vs `--bg-deep` | vs `--bg-base` | **vs `--bg-surface`** | vs `--bg-elevated` | vs `--bg-hover` |
|---|---:|---:|---:|---:|---:|
| `--text-primary` `#e0e6ed` | 15.79 | 15.39 | **14.41** | 13.31 | 11.82 |
| `--text-secondary` `#8899aa` | 6.79 | 6.62 | **6.20** | 5.72 | 5.08 |
| `--text-dim` `#556677` | 3.36 | 3.27 | **3.06** | **2.83 ✗** | **2.51 ✗** |
| `--green` `#00ff41` | 14.53 | 14.16 | **13.27** | 12.25 | 10.88 |
| `--green-dim` `#00cc33` | 9.15 | 8.92 | **8.35** | 7.72 | 6.85 |
| `--amber` `#ffb000` | 10.83 | 10.56 | **9.89** | 9.13 | 8.10 |
| `--amber-dim` `#cc8d00` | 6.97 | 6.79 | **6.36** | 5.88 | 5.22 |
| `--cyan` `#00d4ff` | 11.20 | 10.92 | **10.23** | 9.45 | 8.39 |
| `--cyan-dim` `#00a8cc` | 7.06 | 6.88 | **6.45** | 5.95 | 5.28 |
| `--red` `#ff3b30` | 5.59 | 5.45 | **5.11** | 4.72 | **4.19 ✗** |
| `--red-dim` `#cc2f26` | 3.78 | 3.69 | **3.45** | 3.19 | **2.83 ✗** |
| `--text-inverse` `#0a0e14` on `--green` 实底 | — | — | **14.16** | — | — |

边框色（非文本，仅作见证，不适用 4.5:1 门槛）：`--border-dim` 1.18、`--border-subtle` 1.38、`--border-active` 1.86（均 vs `--bg-surface`）。

### 3.4 `--text-dim` 不达 AA —— 已拍板替换

| | 值 | vs `--bg-surface` | vs `--bg-base` | vs `--bg-elevated` | vs `--bg-hover` | 判定 |
|---|---|---:|---:|---:|---:|---|
| 现状 | `#556677` | **3.06** | 3.27 | 2.83 | 2.51 | 正常文本 **不达 AA(4.5)**；仅勉强过 AA-large(3.0) |
| 已拍板替代 | `#8494a6` | **5.84** | 6.23 | 5.39 | 4.79 | **五级背景全过 AA** |

`--text-dim` 是全站第 2 高引用的文本色（**69 次**），它承载的是 `.style-desc`、`.style-note`、`.palette-hint`、`.format-tag`、`.ta-hint` 这类说明性文本——正是最需要清晰的低对比场景。替换为 `#8494a6` 是净改善，且仍是灰蓝系、不破坏终端冷色调。

**复核结论：任务书给的两个数字（3.06:1 与 5.84:1）与本次实测完全一致，无分歧。** 额外发现两处任务书未提、但同一量级的问题：
- `--text-dim` 在 `--bg-elevated`（**2.83**）与 `--bg-hover`（**2.51**）上**连 AA-large 都不到**。`#8494a6` 在两级上分别 5.39 / 4.79，一并修复。
- `--red-dim` 在 `--bg-hover` 上 2.83、`--red` 在 `--bg-hover` 上 4.19，均不达 AA；不过两者分别只用于边框/图标（非正文），且 `--red-dim` 引用数为 0，可暂缓。

### 3.5 颜色口径红线：非 source/source256 一律强制单色

这是渲染管线的**行为契约**，不是审美偏好。

| role | value | source | scope | recurrence | confidence |
|---|---|---|---|---|---|
| 触发条件 | `tint = fg is not None and color_mode not in ("source","source256")` | `termify/charset.py:535` | blocks 渲染 | 1 | confirmed |
| 同上（JS 镜像） | `var tint = !!fg && colorMode !== "source" && colorMode !== "source256";` | `static/js/termify-render.js:309` | blocks 渲染 | 1 | confirmed |
| 256 级色带 | 对 `i ∈ [0,255]`，`ramp[i] = base + (fg-base) * (i/255)`，逐通道取整 | `termify/charset.py:538-541` / `static/js/termify-render.js:315-318` | 同上 | 2（两端各一） | confirmed |
| 亮度公式 | `0.299R + 0.587G + 0.114B`，结果取整为 0–255 索引 | `termify/charset.py:554-555` / `static/js/termify-render.js:331-332` | 同上 | 2 | confirmed |
| 舍入 | Python `round()` = 银行家舍入（.5 取偶）；JS 侧必须用 `pyRound` 复刻 | `static/js/termify-render.js:50-55` | 灰度、LUT、缩放、色带全部经过 | `pyRound` 被调用 12 处 | confirmed |
| 像素优先分支 | 无 `fg` 或 `color_mode` 为 source/source256 时直接用原始像素色 | `termify/charset.py:542-543` / `static/js/termify-render.js:333-334` | 同上 | 2 | confirmed |

**红线**：修改 `_render_blocks` 或 `renderBlocks` 时，以下三件事必须**两端同改且逐字节等价**——

1. `tint` 的判定条件（多一个字符就改变整条分支）
2. 色带构造的插值公式与 `i/255` 的除法位置（先除后乘 vs 先乘后除，浮点结果不同）
3. 舍入方式（JS 里 `Math.round` 与 `pyRound` 在 `.5` 上分开，必须用 `pyRound`）

`round()` 在 Python 3 是对 double 的银行家舍入，JS `Math.round` 恒向 +∞。`pyRound` 的实现（`termify-render.js:50-55`）已精确复刻，**任何新公式进渲染管线都必须包 `pyRound`**，否则服务端与前端对同一输入产出不同字符，且只在部分边界样本上暴露——这类 bug 极难复现。

---

## 4. Spacing

### 4.1 全量（`static/css/tokens.css:59-69`）

| token | 值 | 引用次数 | 评级 |
|---|---:|---:|---|
| `--space-1` | 4px | 12 | 低 |
| `--space-2` | 8px | **68** | **高频** |
| `--space-3` | 12px | **74** | **最高频** |
| `--space-4` | 16px | **55** | **高频** |
| `--space-5` | 20px | 31 | 中 |
| `--space-6` | 24px | 21 | 中 |
| `--space-8` | 32px | 15 | 中 |
| `--space-10` | 40px | 6 | 低 |
| `--space-12` | 48px | 6 | 低 |
| `--space-16` | 64px | 9 | 低（区块纵向 padding） |
| `--space-20` | 80px | 3 | 低（最大区块留白） |
| **合计** | | **300** | |

### 4.2 实际高频的三个值

`--space-3`(12px)、`--space-2`(8px)、`--space-4`(16px) 合计 **197 次 / 300 = 65.7%**。这三个覆盖了全部「组件内部间距 + 组件间 gap + 列表项间距」的场景。写新组件时先在这三档里选，选不下再往 5/6/8 走。

### 4.3 命名不是连续序列

token 名跳过 7、9、11、13–15、17–19。这不是遗漏，是 **4 的倍数刻度**（1=4, 2=8, 3=12, 4=16, 5=20, 6=24, 8=32, 10=40, 12=48, 16=64, 20=80）。新增档位必须同时满足：是 4 的倍数，且命名 = 值/4。不要新增 `--space-7`（28px）这类破坏刻度的值。

### 4.4 违反项

两个 CSS 文件里有大量无 token 的裸 px padding/margin/gap（例如 `static/css/app.css:1580` 的 `padding: 10px 18px`、`static/css/app.css:1767` 的 `padding: 4px 12px`、`static/css/app.css:1837` 的 `top: 8px; right: 8px`）。规则见 §10 第 3 条：**新代码禁止**，存量在触碰对应选择器时顺手归一。

---

## 5. Layout

### 5.1 容器与栅格

| role | value | source | scope | recurrence | confidence |
|---|---|---|---|---|---|
| 页面容器 | `.container { max-width: 1200px; margin: 0 auto; padding: 0 var(--space-6); }` | `static/css/app.css:67-73` | 全站每个 section | 每个 section 一层 | confirmed |
| 网格层 z | `.container { position: relative; z-index: 1; }` | `static/css/app.css:71-72` | 全站 | 1 | confirmed |
| 风格网格（桌面） | `.style-grid { grid-template-columns: repeat(5, 1fr); gap: var(--space-4); }` | `static/css/app.css:531-536` | studio 风格区 | 1 | confirmed |
| 现有双栏 | `.animation-layout { grid-template-columns: 1fr 320px; align-items: start; gap: var(--space-8); }` | `static/css/app.css:677-683` | 动画预览区 | 1 | confirmed |
| hero 假终端 | `.hero-terminal { max-width: 600px; margin: 0 auto; text-align: left; }` | `static/css/app.css:227-231` | 首页 hero | 1 | confirmed |

`1200px` + `--space-6`(24px) 双侧内边距意味着**实际内容宽 1152px**。不要再给 `.container` 加第二层 max-width，也不要改用百分比容器。

### 5.2 现存断点：5 个离散值、7 处声明（实测，比任务书多 2 处）

| 断点 | source | 作用 |
|---|---|---|
| `max-width: 1024px` | `static/css/app.css:1532` | `.style-grid` → 3 列；`.animation-layout` → 单列 |
| `max-width: 1024px` | `static/css/app.css:2120` | `.gallery-grid` → 3 列 |
| `max-width: 768px` | `static/css/app.css:1541` | `.style-grid` → 2 列；`.features-grid` → 单列；`.nav-links` 缩排 |
| `max-width: 768px` | `static/css/app.css:2123` | `.gallery-grid` → 2 列；`.view-banner-head` 竖排 |
| `max-width: 375px` | `static/css/app.css:2128` | `.gallery-grid` → 1 列 |
| `max-width: 720px` | `static/css/app.css:2484` | `.text-art-actions` / `.text-art-controls` 竖排 |
| `max-width: 940px` | `static/css/text_art.css:37` | `.ta-workbench` 双栏 → 单列 |

> **与任务书的分歧**：任务书列了 5 处（1024×2、768×2、375），实际还有 720（`app.css:2484`）与 940（`text_art.css:37`）。共 7 处声明、5 个离散值。

### 5.3 目标断点体系（decided）

收敛到 **4 档**，把 720 与 940 折叠进来：

| 档 | 触发 | 语义 | 来源折叠 |
|---|---|---|---|
| **Desktop** | `> 1024px` | 满容 1200px，多列 | — |
| **Tablet** | `≤ 1024px` | 主栅格降列（5→3）；双栏布局一律转单列 | 吸收原 `940px`（`text_art.css:37`） |
| **Mobile** | `≤ 768px` | 2 列或单列；导航收起；控件区竖排 | 吸收原 `720px`（`app.css:2484`） |
| **Small** | `≤ 375px` | 单列，最小可用 | — |

折叠 940 的理由：`.ta-workbench` 的 `340px + var(--space-8) + 弹性` 在 1024px 时只剩 684px 弹性区，仍可用；而在 940 与 1024 之间维持双栏会让右栏窄到放不下一行 13px 等宽文本。折叠 720 的理由同上——768 与 720 之间的 48px 差值不足以支撑一套独立布局决策，两套并存的成本是每次改控件都要写两遍。

**落地纪律**：新写媒体查询只能用 `1024 / 768 / 375` 三个值。存量 `720`、`940` 在触碰对应区块时并入目标档，不做一次性批量替换。

### 5.4 Studio 改两栏（decided，未落地）

**现状**：`.studio-block` 是纵向堆叠的三个区块（上传 → 风格 → 预览），见 `static/css/app.css:2590-2595`，靠 `border-top: 1px solid var(--border-dim)` + `margin-top: var(--space-10)` 分隔。风格卡在 `.style-grid` 里 5 列铺开（`app.css:531-536`），10 张卡即占两行。

**目标**：

| 区 | 内容 | 行为 |
|---|---|---|
| 左栏 | 风格网格（`.style-grid`） | 正常滚动；平板及以下转单列并置于预览上方 |
| 右栏 | 终端预览 | `position: sticky`，`top` = 导航高度 + `--space-6`，随左栏滚动常驻 |

**依据（inferred）**：`grep 'position: sticky' static/css/` 全仓仅 1 处命中（`app.css:81`，`nav`）。也就是说 sticky 这套机制在本项目**尚未被用于任何内容区**。落地时注意三点：`nav` 已是 `sticky; top: 0; z-index: 100`（`app.css:78-85`），右栏 `top` 必须大于导航实际高度，否则会被导航压住；`.container` 有 `z-index: 1`，sticky 元素在容器内层叠正常；`≤1024px` 必须 `position: static`，否则单列移动端会粘住半屏。

### 5.5 Hero 假终端换真动画（decided，未落地）

**现状**：`templates/index.html:43-70` 的 `.hero-terminal` 是**纯静态 HTML**——一段写死的 `$ termify convert cat.gif ...` 命令、四行 `✓` 输出、以及一个 `<span class="terminal-cursor"></span>` 光标（`templates/index.html:65`）。光标有 `animation: blink 1s step-end infinite`（`static/css/app.css:305`），除此之外整块不动。

**目标**：换成真实播放的字符动画（与 studio 一致的数据管线），而不是继续堆静态 DOM。

**约束**：hero 位于首屏，任何真实动画必须满足 §6.4 的 reduced-motion 全覆盖，且必须在首帧内可读（不能先白后黑）。当前 `.hero` 自带 `animation: fadeInUp 0.6s ease-out`（`app.css:1572`），新动画要与这个入场动画的时序错开，否则首屏会出现两次位移。

---

## 6. Motion

### 6.1 三个时长 token 的实测使用

| token | 值 | 引用次数 | 判定 |
|---|---|---:|---|
| `--transition-fast` | `120ms ease-out` | **48** | 交互反馈主档 |
| `--transition-base` | `200ms ease-out` | 4 | 卡片级位移 |
| `--transition-slow` | `350ms ease-out` | 1 | 几乎不用 |

`120ms` 与 `200ms` 构成全部交互反馈时长。这与「交互反馈 ≤200ms」的规则天然吻合——**现有 token 已经合规，问题不在时长，在属性**（见 6.2）。

### 6.2 `transition: all` —— 33 处（实测）

```
$ grep -rc 'transition: *all' static/css/*.css
static/css/app.css:28
static/css/text_art.css:5
static/css/tokens.css:0
合计 33
```

**`app.css` 的 28 处行号**：159, 345, 507, 544, 849, 929, 1012, 1101, 1137, 1215, 1311, 1389, 1684, 1723, 1737, 1767, 1814, 1869, 1890, 1926, 1997, 2025, 2067, 2079, 2107, 2150, 2245, 2584

**`text_art.css` 的 5 处行号**：344, 385, 434, 450, 475

其中 `--transition-fast` 占 30 处、`--transition-base` 占 3 处（`app.css:345, 544, 1215`）。**分布特征**：全部 33 处都落在「可交互元素」上——按钮、卡片、标签、输入框、tab。没有一处落在纯静态块上，说明作者的本意始终是「交互反馈」，只是用了 `all` 这个偷懒写法。

### 6.3 规则（decided）

> **只动 `transform` / `opacity`；交互反馈时长 ≤200ms；禁止 `transition: all`。**

例外：`border-color` / `color` / `background-color` 属于不触发重排的绘制属性，允许显式列出——现有代码大量依赖 hover 换边框色（`--border-active` 引用 14 次）。规则精确写法：

```css
/* 允许 */ transition: transform var(--transition-fast), border-color var(--transition-fast);
/* 允许 */ transition: opacity var(--transition-fast);
/* 允许 */ transition: color var(--transition-fast), background-color var(--transition-fast);
/* 禁止 */ transition: all var(--transition-fast);
```

**为什么必须改**：`all` 会把 `width`/`height`/`padding` 一起纳入过渡。本项目大量元素由 JS 改 `height`（例如 `.animation-terminal` 的注释明说「JS 按内容网格比设定 terminal-body 高度」，`app.css:687`），一旦这些元素上挂了 `transition: all`，每次重算高度都会插一段 120ms 的补间，视觉上表现为「画面在抖」。

### 6.4 `prefers-reduced-motion` 覆盖率 —— 现状 4 个动画名 / 16 条声明

实测 `animation:` 生效声明 **16 条**（`app.css` 13 条 + `text_art.css` 3 条；已排除 4 处 `animation: none` 复位），覆盖 **9 个不同动画名**。其中仅 **4 个动画名**有 reduced-motion 守卫（对应 **5 条**声明），**5 个动画名 / 11 条声明**完全无守卫。

**已守卫（4 个动画名 / 5 条声明）**

| 动画 | source | 守卫 |
|---|---|---|
| `gallery-skeleton-shimmer` | `app.css:1795`、`app.css:1799` | `app.css:1807-1809` ✓ |
| `ta-caret` | `text_art.css:210` | `text_art.css:217-219` ✓ |
| `ta-shimmer` | `text_art.css:224` | `text_art.css:233-235` ✓ |
| `ta-indeterminate` | `text_art.css:248` | `text_art.css:258-260` ✓ |

**未守卫（5 个动画名 / 11 条声明，必须补）**

| 动画 | source | 说明 |
|---|---|---|
| `blink`（终端光标） | `app.css:305` | 1s 无限闪烁，**最刺眼的一条** |
| `pulse`（hero-badge 圆点） | `app.css:197` | 2s 无限呼吸 |
| `fadeInUp`（区块入场） | `app.css:1572-1575` | 0.6s 位移入场，4 条 |
| `fadeInUp`（弹窗/浮层） | `app.css:1699`、`app.css:1882`、`app.css:1951` | 0.3–0.6s，3 条 |
| `termifySpin` | `app.css:1621` | 0.8s 无限旋转 |
| `view-spin` | `app.css:2167` | 0.7s 无限旋转 |

**规则（decided）**：`prefers-reduced-motion: reduce` 必须**全覆盖**——每一条新的 `animation` 声明都必须在同文件内配一条守卫。守卫写法沿用现有 4 处的模式：

```css
@media (prefers-reduced-motion: reduce) {
    .selector { animation: none; }
}
```

对 `fadeInUp` 这类入场动画，`animation: none` 意味着元素直接以终态出现，是正确的（不会导致元素不可见——它们本来就没有 `opacity: 0` 的初始态）。

### 6.5 一个空守卫（observed）

`static/css/text_art.css:361-363` 是一个**空的** `@media (prefers-reduced-motion: reduce) { }` 块，对应 `@keyframes ta-ai-pulse`（`text_art.css:357`）。而 `ta-ai-pulse` 在 CSS 里**从未被任何选择器引用**（grep 只命中定义行 357）。

结论：一个死 keyframes + 一个空守卫，两者都是残骸。要么补上 `animation: ta-ai-pulse ...` 的使用点并在空守卫里关掉它，要么一起删。**不要留空守卫**——它会让后来者误以为这条动画已经处理过。

---

## 7. z-index 阶梯

### 7.1 现状：19 处声明 / 14 个离散值

> **与任务书的分歧**：任务书称「19 个手写值」。实测 19 是**声明条数**，离散值只有 **14** 个（`1` 出现 6 次，`200` 出现 2 次）。下表按离散值组织，保留每个值的全部声明位置。

| 值 | 出现次数 | source | 承担角色 |
|---:|---:|---|---|
| `0` | 1 | `app.css:44` | 网格背景层 `body::before` |
| `1` | 6 | `app.css:72`（`.container`）、`386`、`402`、`432`、`453`、`496` | 页面内容层（含上传区各子块） |
| `2` | 1 | `app.css:1837` | 卡片内角标 `.gallery-card-badge` |
| `5` | 1 | `app.css:1673` | 终端内「渲染中」覆盖层 |
| `6` | 1 | `app.css:2541` | 渲染进度 chip `.render-chip` |
| `30` | 1 | `app.css:1757` | 自定义标签下拉 `.custom-tags-dropdown` |
| `90` | 1 | `app.css:2177` | 导出格式弹窗背景 `.export-backdrop` |
| `100` | 1 | `app.css:84` | 导航 `nav`（sticky） |
| `200` | 2 | `app.css:1312`（toggle）、`1329`（panel） | Tweaks 面板 |
| `300` | 1 | `app.css:1582` | Toast `#toast` |
| `400` | 1 | `app.css:1590` | 通用弹窗 `.termify-modal` |
| `500` | 1 | `app.css:1940` | 画廊分享弹窗 `.gallery-modal-backdrop` |
| `9999` | 1 | `app.css:60` | **CRT 扫描线 `body::after`**（覆盖一切） |

注意两个**已存在的层叠异常**（不是本轮要修的，但 token 化时必须知道）：

1. **Export backdrop (90) < Nav (100)**：`.export-backdrop` 打开时，`nav` 会盖在弹窗遮罩之上。这是既有视觉事实，**token 化时不得「顺手修好」**——修它等于改视觉，属于另一个变更。
2. **Toast (300) < Modal (400)**：弹窗打开时 toast 在弹窗之下，可能看不见。同样是既有事实。

### 7.2 token 化命名（decided）—— 值不变，只加名字

> **硬约束：不允许重新编号。** 层叠关系是相对顺序决定的，任何「整理成整齐的 10/20/30」都会直接引入层叠回归（上面两条异常就是活证据：相对顺序一改，`export-backdrop` 与 `nav` 的关系会翻转）。

| token | = 现有值 | 消费点 |
|---|---:|---|
| `--z-grid` | `0` | `body::before` |
| `--z-content` | `1` | `.container` 及其 5 个子块 |
| `--z-badge` | `2` | `.gallery-card-badge` |
| `--z-terminal-overlay` | `5` | 终端内渲染覆盖层 |
| `--z-chip` | `6` | `.render-chip` |
| `--z-popover` | `30` | `.custom-tags-dropdown` |
| `--z-export` | `90` | `.export-backdrop` |
| `--z-nav` | `100` | `nav` |
| `--z-tweaks` | `200` | `.tweaks-toggle` / `.tweaks-panel` |
| `--z-toast` | `300` | `#toast` |
| `--z-modal` | `400` | `.termify-modal` |
| `--z-modal-gallery` | `500` | `.gallery-modal-backdrop` |
| `--z-scanline` | `9999` | `body::after` |

13 个 token，取值与现有 14 个值一一对应（`1` 六处共用一个 token）。**顺序表**（从低到高，改版时照抄此序，不要重排）：

```
grid(0) < content(1) < badge(2) < terminal-overlay(5) < chip(6)
< popover(30) < export(90) < nav(100) < tweaks(200) < toast(300)
< modal(400) < modal-gallery(500) < scanline(9999)
```

新增层级必须**插入到两值之间取中值**（例如 nav 与 tweaks 之间用 `150`），不得重排既有值。

---

## 8. 组件配方

### 8.1 `.style-card` 与 `.feature-card` 共用同一配方（confirmed）

| 属性 | `.style-card`（`app.css:538-546`） | `.feature-card`（`app.css:1210-1216`） |
|---|---|---|
| `background` | `var(--bg-surface)` | `var(--bg-surface)` |
| `border` | `1px solid var(--border-dim)` | `1px solid var(--border-dim)` |
| `border-radius` | `var(--radius-md)` | `var(--radius-md)` |
| `transition` | `all var(--transition-base)` | `all var(--transition-base)` |
| hover | `border-color: var(--border-active); transform: translateY(-2px);`（`:548-551`） | 同（`:1218-1221`） |
| 独有 | `overflow: hidden; cursor: pointer; position: relative;` | `padding: var(--space-8);` |

**配方（standard card）**：

```css
background: var(--bg-surface);
border: 1px solid var(--border-dim);
border-radius: var(--radius-md);
transition: transform var(--transition-base), border-color var(--transition-base);
:hover { border-color: var(--border-active); transform: translateY(-2px); }
```

`.style-card.selected`（`app.css:553-556`）在配方之上叠 `border-color: var(--green)` + `box-shadow: 0 0 20px var(--green-glow), inset 0 0 20px var(--green-glow)`，并用 `::after` 画一个 8×8 的绿点（`app.css:558-568`）。这是 **selected 态**，不是第三种状态机成员。

### 8.2 第三个同配方实例，位移值分叉（observed）

`.gallery-card`（`static/css/app.css:1810-1821`）是同一配方的第三个实例，但 hover 用 **`translateY(-3px)`** 而不是 `-2px`，并额外加了 `box-shadow: 0 8px 32px rgba(0,0,0,0.4)`。

规则：`translateY` 在卡片 hover 上一律用 **`-2px`**（2 个实例 vs 1 个实例的多数）。`.gallery-card` 是分叉点，触碰时统一。`box-shadow` 不是配方的一部分，画廊卡的特例可保留。

### 8.3 状态机缺口（confirmed，已知缺口）

三类卡片（`.style-card` / `.feature-card` / `.gallery-card`）合起来只实现了 **3 个状态**：

| 状态 | 有无 | 依据 |
|---|---|---|
| default | ✓ | 三条基础规则 |
| hover | ✓ | `app.css:548-551`、`1218-1221`、`1817-1821` |
| selected | ✓（仅 `.style-card`） | `app.css:553-556` |
| **loading** | ✗ | 全文件无 `.style-card.loading` / `.feature-card.loading` |
| **error** | ✗ | 全文件无 `.style-card.error` |
| **disabled** | ✗ | 全文件无 `.style-card:disabled` / `[aria-disabled]` |

**这是已知缺口。** 具体表现：上传后渲染失败时，风格卡没有任何视觉反馈，用户只看到预览区不动；`.ta-select:disabled` 有 `opacity: 0.55`（`text_art.css:93`），但卡片类没有任何对应物。

补齐时的配方（decided，与既有语言一致）：

```css
/* loading：沿用骨架屏语言 */
.card.loading { opacity: .6; pointer-events: none; }
/* disabled：沿用 .ta-select:disabled 的 0.55 */
.card:disabled, .card[aria-disabled="true"] { opacity: .55; cursor: not-allowed; }
/* error：用 --red 走边框，不用 glow（见 §10 第 1 条） */
.card.error { border-color: var(--red); }
```

### 8.4 字符画预览框的色板（observed）

`.style-preview.*` 用不同色相区分风格（`app.css:593-633`）：`.ascii` → `var(--green)`（`app.css:594`）；`.blocks` → `#ff6b35`（**硬编码，唯一一个不在 token 里的橙**，`app.css:600`）；`.braille` → `var(--cyan)`（`app.css:606`）；`.geometric` → `var(--amber)`（`app.css:613`）；`.binary` → `var(--text-primary)`（`app.css:619`）；`.halftone` → `var(--red)`（`app.css:625`）；`.dither` → `var(--green)`（`app.css:631`）。

规则：新增风格预览必须从 `--green / --amber / --cyan / --red / --text-primary` 里选，不得新增硬编码 hex。`#ff6b35` 是存量例外，触碰时改为 `--amber`。

---

## 9. 不可违反的产品约束

以下约束**不是风格偏好**，破坏它们会导致产出不正确或功能失效。

### 9.1 Embed 形态（`termify/output/embed.py`）

| 约束 | value | source | confidence |
|---|---|---|---|
| 无依赖运行时 | 一段 IIFE，不引入任何库；`fetch` + `setInterval` 手写 | `embed.py:21-94` | confirmed |
| 挂载点 | `<termify-play src="...">` 自定义元素 | `embed.py:152` | confirmed |
| 同页只注册一次 | `if (window.customElements && customElements.get('termify-play')) return;` + `customElements.define` 在末尾 | `embed.py:22`、`embed.py:87-88` | confirmed |
| 帧数据纯文本、无 ANSI | `strip_ansi()` 逐行剥离 ESC…m；输出 `{w,h,interval,charset,frames}` JSON | `embed.py:98-129` | confirmed |
| 颜色交给宿主 | 文档串明写「Colour is therefore the host page's job (`:host { color }`) and this stays plain text」 | `embed.py:104` | confirmed |
| reduced-motion 只画一帧 | `matchMedia('(prefers-reduced-motion: reduce)').matches` → `if (reduce) return;` | `embed.py:62-64` | confirmed |
| 离屏停表 | `IntersectionObserver` → `isIntersecting ? start() : stop()`，并叠加 `visibilitychange` | `embed.py:72-81` | confirmed |

**约束细则**：`embed.py:104` 说颜色交给宿主，但运行时实际在 `embed.py:23` 硬写了默认 `color:#c9d1d9` 与 `background:#0a0e14`。二者不矛盾——默认值是为了「宿主没设时也能看」，`#0a0e14` 与 `--bg-base` 一致。**改这两个默认值必须同步 `static/css/tokens.css:20-21`**，否则嵌入块与站内视觉分叉。

**禁止**：给 embed 运行时加框架、加 polyfill、加 ANSI 转义、把帧数据改成 base64 或二进制。目标环境是 README 渲染器与 CMS 净化器——它们会丢弃或改写转义序列，纯文本是该约束唯一可行的形态。

### 9.2 首页风格卡预览由脚本烘焙（`scripts/bake_style_previews.py`）

| 约束 | value | source | confidence |
|---|---|---|---|
| 输出尺寸 | `312 × 234`（= 6×13px 字符格 × 4:3），由 `FONT_SIZE = 10` 推导 | `scripts/bake_style_previews.py:60` | confirmed |
| 帧数 | `FRAMES = 12`（取作品 16 帧的 12 帧，控制单 GIF < 120KB） | `scripts/bake_style_previews.py:61` | confirmed |
| 调色板 | `COLORS = 256`，全部帧共享**一个** 256 色板（从 4 列 montage 采样） | `scripts/bake_style_previews.py:62`、`:82-86` | confirmed |
| 硬闸值 | 平均像素偏差 `> 6.0` 即 `SystemExit` | `scripts/bake_style_previews.py:104` | confirmed |
| 校验内容 | 帧数、几何、逐帧 `ImageChops.difference` 均值 | `scripts/bake_style_previews.py:93-108` | confirmed |
| 卡片数 | 10 张（`data-style` = ascii / ascii-lite / blocks / braille / geometric / binary / shades / halftone / dither / custom） | `templates/index.html:114` 起 | confirmed |

**约束细则**：`312×234` 在模板里以 `width="312" height="234"` 硬写在 `<img>` 上（从 `templates/index.html:119-121` 起共 10 处），且容器 `.style-preview` 是 `aspect-ratio: 4/3`。**改烘焙尺寸必须同时改模板的 10 组 width/height 与 `.style-preview` 的宽高比**，否则会出现布局跳动（CLS）。

`> 6.0` 的闸值是「解码后的 GIF 与源帧的平均像素差」，它同时守住三件事：调色板量化没有崩、几何没错位、帧数没丢。**不得放宽**——放宽后烘焙产物在终端里看起来会「糊」，而肉眼在 312px 缩略图上判断不出来。

### 9.3 `source` / `source256` 输出必须逐字节不变

| role | value | source | confidence |
|---|---|---|---|
| 既有作品观感基线 | 「没有 `fg`，或 `color_mode` 为 `source`/`source256` 时像素色优先——那是既有作品存下来的观感基线，必须逐字不变」 | `termify/charset.py:530-534`（注释）、`termify/charset.py:535`（`tint` 判定） | confirmed |
| JS 镜像 | `var tint = !!fg && colorMode !== "source" && colorMode !== "source256";` | `static/js/termify-render.js:309` | confirmed |

**这条是最高优先级红线**：数据库里所有历史作品都按这套像素逻辑渲染并持久化。任何对 `_render_blocks` / `renderBlocks` 的改动，**在 `source` 与 `source256` 两种 `color_mode` 下的输出必须逐字节与改动前相同**。验证方式：取一个已知作品，改前改后各渲染一次，`cmp` 两段输出。做不到就等于让历史作品改观。

### 9.4 其它不可违反项

| 约束 | value | source |
|---|---|---|
| 隐藏语义优先 | `[hidden] { display: none !important; }` | `app.css:31` |
| 字符缩略用 nearest | `image-rendering: pixelated`（硬边字符画就近放大，双线性会把整幅糊成马赛克） | `app.css:1831`，注释在 `:1829-1830` |
| 无尾部 ANSI reset | 每个 `▀` 自带 fg/bg 码，reset 会清状态导致下一行渲染成黑色 | `termify/charset.py:566-567`（注释） |
| 渲染终端不回放大 | `.animation-terminal { max-width: 100%; }`，高度由 JS 按内容网格比设定 | `app.css:686-688` |
| 等宽字体自托管 | 见 §2.1，CDN 失败会回退到缺字形的系统等宽 | `static/css/tokens.css:1-10` |

---

## 10. 反模式清单

违反任意一条的改动不予合并。

| # | 禁止 | 为什么（证据） | 替代做法 |
|---|---|---|---|
| 1 | **把 glow 当主 affordance** | `--green-glow` 被引用 38 次，已泛滥。glow 表示「发光 = 注意力」，全站铺开后等于没有层级的强调；`box-shadow: 0 0 20px` 还会把卡片边缘糊掉 | 主 affordance 用 `--green` 边框或 `--green` 填充 + `--text-inverse` 文字（14.16:1）。glow 只留给「当前选中」一处 |
| 2 | **`transition: all`** | 33 处实测（§6.2）。会把由 JS 频繁改动的 `height`/`width` 纳入补间，导致画面随重算抖动 | 显式列属性：`transform` / `opacity` / `color` / `border-color` / `background-color` |
| 3 | **无 token 的硬编码 px** | 现有裸 px 遍布（`padding: 10px 18px` 等）。硬编码 px 让「改一处、全站对齐」变成不可能 | 间距用 `--space-*`，字号用 `--fs-*`，圆角用 `--radius-*` |
| 4 | **新增 `--font-sans` 类西文字体** | 全 mono 已拍板（§2.1）。`--font-sans` 现存引用数 1（`app.css:20`），正是要删的那一条 | 只用 `--font-mono`。需要层次就调字重（100–800）与字号，不换字体族 |
| 5 | **重新编号 z-index** | 层叠是相对顺序，重排会翻转既有关系（`export(90) < nav(100)` 是既有事实，见 §7.1） | 用 §7.2 的 token，值不变；新增层级取相邻中值 |
| 6 | **新增同值色 token** | `--green-text` 与 `--green` 同值 `#00ff41`（1 处引用，`text_art.css:406`）；同值别名让「改主色」变成要记得改两处 | 直接写 `var(--green)`，不造别名 |
| 7 | **`var(--token, #fallback)` 的兜底值与 token 不同值** | `app.css:2502` / `app.css:2624` 用 `var(--text-dim, #8b949e)`，而 `--text-dim` 真实值 `#556677`——token 丢失时静默变亮两级 | 兜底值必须与 token 同值，或直接不写兜底 |
| 8 | **留空 `@media (prefers-reduced-motion)` 块** | `text_art.css:361-363` 已有先例，会让后来者误以为该动画已处理 | 要么补全守卫，要么整块删除 |
| 9 | **假控件** | `--scanline-opacity` 被 5 处 JS 写入但无任何 CSS 读取（§1.4），开关点了不生效 | 要么接上 CSS，要么删掉开关 |
| 10 | **在 `source`/`source256` 路径上改渲染逻辑** | 会改历史作品的观感（§9.3） | 只在 `tint` 分支内改动，改后用 `cmp` 验证两种模式逐字节不变 |
| 11 | **新增断点值** | 已有 5 个离散断点（§5.2），每多一个就让每次改控件多写一遍 | 只用 `1024 / 768 / 375` |
| 12 | **给 embed 运行时加依赖** | 目标环境会净化掉一切非纯文本/非内联的东西（§9.1） | 保持单段 IIFE、零依赖 |

---

## 附录 A：实测数据复现命令

所有计数均可用以下命令复现（工作目录 = 仓库根）。

```bash
# A1 · font-size 总数（两口径）
grep -o 'font-size' static/css/app.css | wc -l                    # 146
grep -o 'font-size' static/css/text_art.css | wc -l               # 31
grep -o 'font-size: *[0-9]*px' static/css/app.css | wc -l         # 122
grep -o 'font-size: *[0-9]*px' static/css/text_art.css | wc -l    # 2

# A2 · px 字面量直方图
grep -o 'font-size: *[0-9]*px' static/css/app.css \
  | sed 's/font-size: *//' | sort -n | uniq -c | sort -rn

# A3 · rem / clamp 计数
grep -o 'font-size: *[0-9.]*rem' static/css/app.css | wc -l       # 22
grep -o 'font-size: *[0-9.]*rem' static/css/text_art.css | wc -l  # 29
grep -o 'font-size: *clamp' static/css/app.css | wc -l            # 2

# A4 · transition: all
grep -rc 'transition: *all' static/css/*.css                      # 28 / 5 / 0
grep -n 'transition: *all' static/css/app.css static/css/text_art.css

# A5 · z-index 全量
grep -rn 'z-index' static/css/

# A6 · 断点
grep -n '@media' static/css/*.css

# A7 · reduce-motion 覆盖审计
# 生效 animation 声明：app.css 13 条 + text_art.css 3 条 = 16 条
grep -c 'animation:' static/css/app.css static/css/text_art.css   # 14 / 6（含 4 条 animation: none 复位）
grep -n 'animation:' static/css/app.css static/css/text_art.css
grep -rn 'prefers-reduced-motion' static/css/

# A8 · token 引用次数
for t in green text-dim text-primary border-dim; do
  printf '%s %s\n' "$t" "$(grep -o "var(--$t)" static/css/app.css static/css/text_art.css | wc -l)"
done

# A9 · 间距 token 引用次数
for n in 1 2 3 4 5 6 8 10 12 16 20; do
  printf 'space-%s %s\n' "$n" "$(grep -o "var(--space-$n)" static/css/app.css static/css/text_art.css | wc -l)"
done

# A10 · 死 token 验证
for t in amber-dim amber-glow cyan-dim red-dim; do grep -rn "var(--$t)" static/css/; done   # 应无输出
grep -rn "var(--green-text)" static/css/   # 应命中 1 行：text_art.css:406

# A11 · 扫描线开关（变量被写但无人读）
grep -rn 'scanline-opacity' static/ templates/                    # 5 处 JS/模板
grep -rn 'scanline-opacity' static/css/                           # 空
```

**A12 · WCAG 对比度**（python，脚本用完即删）

```python
def lin(c):
    c = c / 255.0
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

def L(hexs):
    h = hexs.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)

def cr(a, b):
    la, lb = L(a), L(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)

print(cr('#556677', '#111620'))   # 3.06  --text-dim on --bg-surface
print(cr('#8494a6', '#111620'))   # 5.84  候选替代值
print(cr('#8899aa', '#111620'))   # 6.20  --text-secondary
print(cr('#e0e6ed', '#111620'))   # 14.41 --text-primary
print(cr('#00ff41', '#111620'))   # 13.27 --green
```

---

## 附录 B：已知缺口台账（全部 `confirmed`，按可执行顺序排）

| # | 缺口 | 证据 | 动作 |
|---|---|---|---|
| B1 | `--text-dim` 不达 AA（3.06:1） | §3.4 | 改 `tokens.css:47` 为 `#8494a6`（5.84:1） |
| B2 | 16 条动画声明中 11 条（5 个动画名）无 reduced-motion 守卫 | §6.4 | 每条 `animation` 配 @media 守卫 |
| B3 | 33 处 `transition: all` | §6.2 | 逐个改为显式属性 |
| B4 | 卡片缺 loading / error / disabled 三态 | §8.3 | 补 §8.3 的配方 |
| B5 | Space Grotesk 未删（3 处） | §2.1 | 按 §2.1 三步删 |
| B6 | `--scanline-opacity` 写了没人读 | §1.4 | 接上 CSS 或删控件 |
| B7 | 4 个死 token（amber-dim / amber-glow / cyan-dim / red-dim） | §3.1 | 从 `tokens.css` 删除 |
| B8 | `ta-ai-pulse` 死 keyframes + 空守卫 | §6.5 | 补使用点或连空块一起删 |
| B9 | z-index 未 token 化 | §7.2 | 加 13 个 `--z-*`，值不变 |
| B10 | 8 处 `#00ff41` 硬编码、2 处错值兜底 | §3.2 | 换回 `var(--green)` / 修正兜底值 |
| B11 | `.gallery-card` hover 位移 `-3px` 分叉 | §8.2 | 统一为 `-2px` |
| B12 | Studio 仍三区块纵排 | §5.4 | 改两栏 + sticky |
| B13 | Hero 是静态假终端 | §5.5 | 换真实字符动画 |
| B14 | 5 个离散断点 | §5.2 / §5.3 | 收敛到 3 个（1024/768/375） |

---

*本文件所有 `文件:行号` 引用均在本次会话中 grep 命中原文校验，全部以 pre-refactor 基线 `01e5ea1` 为准。用 P0+P1 落地后的代码复测：120 条唯一 CSS 引用中 12 条未动、108 条漂移、最大偏移 17 行。因此引用规则时**以引号内的规则原文 grep 为准，不要信行号**；行号会继续漂移，但 source 指向的规则原文保持不变。*
