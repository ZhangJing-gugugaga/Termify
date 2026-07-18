# Termify 开发目标提示词（Goal Prompt）

> 版本：v1.0 ｜ 创建：2026-07-17 ｜ 维护：齐活林团队 → 任意接手 agent
> 作用：本文件是 Termify 项目的「开发目标提示词」。**任意 agent（任意模型：Claude / Codex / Copilot / Cursor / Gemini 等）把本文件粘贴进对话，即可接管项目，进行长期的开发、沉淀、优化、维护。** 真相来源是项目里的 Markdown 文件，不是某次对话，因此换模型无损接手。

---

## 0. 这是什么 / 怎么用

- **本文件 = 接手说明书 + 操作铁律**。新 agent 读完即知：项目是什么、先读哪些文件、怎么自查、怎么提交、怎么维护文档。
- **配合 rpd 技能使用**（路径见 §2）。rpd 提供跨 agent 的状态文件机制与校验/备份/安全脚本，是本提示词的「执行底座」。
- **使用方式**：把本文件全文（或 §12 精简版）作为首条消息发给新 agent，并附上工作区绝对路径 `E:\Desktop\工作\观猹\Termify`。

---

## 1. 项目基线（30 秒抓全貌）

| 项 | 内容 |
|----|------|
| 定位 | 「万物皆可终端」——把 **GIF / PNG / JPG** 转成终端可播放动画（`.py` / `.html`），并规划嵌入式输出与公开在线 Demo。MIT 开源、零注册、零云端。 |
| 技术栈 | 后端 Python 3.10+ / Flask / Pillow；前端原生 HTML/CSS/JS（无框架）；测试 pytest（当前 **123**，随轮次扩展，本轮目标 107+，详见 §10）。 |
| 已完成 v1.0 | 5 种渲染风格（ascii/blocks/braille/geometric/binary）、`.py`+`.html` 输出、Web+CLI 双形态、全屏自适应+`music.mp3` 音频、REST API（upload/preview/generate/download）。 |
| **护城河铁律（最高优先级）** | 所有改动必须**强化**「产出可下载 / 可运行 / 可分享的文件」，而**不是弱化**它。在线竞品只"看"不"带走"，这是 Termify 差异点。 |
| 关键架构决策 | ① 视频走**后端 ffmpeg**，不在前端跑 ffmpeg.wasm；② **不**把引擎全量编译成 WASM 导出；③ 嵌入式 = PC 离线生成 C 数组 + MCU 仅播放（引擎不上 MCU）；④ 实时预览用后端 SSE 流式首帧，远期轻量 WASM 仅做小图预览。 |
| 路线里程碑 | M0 止血（兼容/文档/缩略图）→ M1 增长引擎（在线 Demo + 一键包）→ M2 补齐短板（视频/批量/分享）→ M3 差异化纵深（嵌入式/画廊/技术债）。 |
| 参考项目 | 开发参考 `E:\Desktop\工作\SalaryCat`（half-block 渲染 / color delta / PyInstaller 思路来源）；竞品对标 ASCII Magic、AsciiCraft、Monochora、ascii-animator、google/gif-for-cli。 |

---

## 2. rpd 技能路径与加载（CRITICAL）

rpd 是跨 agent、跨会话的 PRD/状态管理机制，自带各主流模型的 adapter，天然模型无关。

| 项 | 值 |
|----|----|
| **rpd 绝对路径** | `C:\Users\laotie_nb666\.claude\skills\rpd\` |
| 模型适配文件 | `AGENT.md` / `CLAUDE.md` / `GEMINI.md` / `.codex-plugin` / `.copilot-plugin` / `.cursor-plugin`（位于 rpd 根目录，对应各 agent 自动生效） |
| 状态文件（项目记忆） | `E:\Desktop\工作\观猹\Termify\.project-state.md`（YAML frontmatter + 9 章节，项目唯一真相来源之一） |
| PRD 主文档 | `E:\Desktop\工作\观猹\Termify\PRD.md`（V2.0.0，rpd 15 节结构） |

**启动命令（新 agent 首步）**：
```bash
# Windows (PowerShell / Git Bash 均可；Git Bash 用 /c/Users/... 形式)
python "C:\Users\laotie_nb666\.claude\skills\rpd\scripts\intent-router.py" "继续开发 Termify"
```
输出 `recommended_flow: continued` → 进入 **Flow C（继续开发）**，自动读取 `.project-state.md` + `PRD.md`。

**rpd 配套脚本（位于 `scripts/` 目录）**：

| 脚本 | 用途 | 何时用 |
|------|------|--------|
| `intent-router.py` | 意图路由，判定 new/continued/unknown | 每次启动首步 |
| `state-validator.py` | 校验 `.project-state.md` 结构合法 | 改完状态文件后 |
| `state-guard.py` | 覆盖前自动备份状态文件 | 改写状态文件前（`--action backup`） |
| `security-scanner.py` | 安全扫描（密钥/敏感信息/危险调用） | 提交前、PR 前 |
| `prd-validator.py` | 校验 PRD 结构/必填章节 | 改完 PRD 后 |
| `project-scanner.py` | 扫描项目结构生成快照 | 大改前基线 |

> ⚠️ 若新 agent 环境没有 rpd 技能，**也无妨**：`.project-state.md` + `PRD.md` + 本文件本身就是完整交接包，纯读文件即可运作。rpd 脚本只是加分项。

---

## 3. 工作区与关键文件清单

| 文件（绝对路径） | 作用 | 维护频率 |
|------------------|------|---------|
| `E:\Desktop\工作\观猹\Termify\GOAL-PROMPT.md` | 本文件（接手说明书） | 流程/铁律变更时 |
| `E:\Desktop\工作\观猹\Termify\.project-state.md` | 跨 agent 状态记忆（进度/决策/诊断） | **每次提交** |
| `E:\Desktop\工作\观猹\Termify\PRD.md` | PRD V2.0.0（rpd 15 节） | 需求/功能变更时 |
| `E:\Desktop\工作\观猹\Termify\README.md` | 用户可见的使用说明 | 用法/功能变更时 |
| `E:\Desktop\工作\观猹\Termify\requirements.txt` | Python 依赖 | 新增/升级依赖时 |
| `E:\Desktop\工作\观猹\Termify\termify/` | 转换引擎包（charset/frames/engine/output…） | 功能开发 |
| `E:\Desktop\工作\观猹\Termify\app.py` | Flask 入口 | 后端/API 开发 |
| `E:\Desktop\工作\观猹\Termify\tests/` | pytest 用例（当前 123+，本轮目标 107+） | 改引擎/加功能时补测试 |
| `E:\Desktop\工作\SalaryCat\cat.GIF` | 用户提供的真实测试猫图 | 视觉回归、端到端测试必跑素材（见 §10.2 T3） |
| `E:\Desktop\工作\观猹\Termify\Termify-四视角分析.md` | 外行/小白/PM/嵌入式四视角分析 | 参考，少改 |
| `E:\Desktop\工作\观猹\Termify\Termify-用户问题手册.md` | 小白/初级/高级三层 35 条 Q&A | 能力/API 变更时 |
| `E:\Desktop\工作\观猹\Termify\Termify-竞品融合建议.md` | 竞品产品创意融合（许清楚） | 参考 |
| `E:\Desktop\工作\观猹\Termify\Termify-竞品技术融合分析.md` | 竞品技术栈/实现融合（寇豆码，含伪代码） | 参考 |
| `E:\Desktop\工作\观猹\Termify\.workbuddy\memory\YYYY-MM-DD.md` | 本项目每日工作日志 | **每日** |

> Git Bash 下路径写作 `/e/Desktop/工作/观猹/Termify/...`；PowerShell 用 `E:\Desktop\工作\观猹\Termify\...`。

---

## 4. 开发流程路由（rpd 驱动 + 团队 SOP）

1. **起手式**：读 `.project-state.md`（全貌）→ 读 `PRD.md`（细节）→ 从 `功能进度清单` 选 **P0** 未开始项。
2. **小改动（≤10 文件，如「自适应灰度分桶」~20 行）** → 快速模式：工程师直接改 + 自检 + QA 验证。
3. **大块（在线 Demo / 一键包 / 嵌入式 v2）** → 标准 SOP：产品经理(增量PRD) → 架构师(设计+任务分解) → 工程师(实现) → QA(测试)。
4. **每次改动闭环**：实现 → §5 自检 → §6 提交流程 → §7 文档更新 → 回写 `.project-state.md`。

---

## 5. Agent 自检逻辑（核心）

### 5.1 自检触发点
- ✅ 每完成一个功能 / 一个文件批次后
- ✅ 每次 `git commit` **前**
- ✅ 每次提 PR / merge **前**
- ✅ 每轮测试（QA）后
- ✅ 改完 `.project-state.md` / `PRD.md` 后跑对应 validator

### 5.2 「错误」的定义（四级，先分级再处理）

| 级别 | 名称 | 判定标准（明确红线） |
|------|------|----------------------|
| **L0 致命** | blocker | 代码无法 import / 启动即崩；`pytest` 全挂或无法收集；安全漏洞（密钥泄露、命令注入、任意文件读）；数据/产物丢失 |
| **L1 严重** | critical | 核心功能输出错误：画质退化、Windows 乱码未处理、生成的 `.py`/`.html` **不可播放**、API 不可用、护城河被弱化（如误把导出改成纯前端） |
| **L2 一般** | major | 非核心功能缺陷；性能退化但可用；文档与实现不符（如 PRD 写 41 测试代码却是 42）；UI/交互不一致；风格选错不报错 |
| **L3 轻微** | minor | 格式、注释、命名、文案错别字、图片未压缩 |

### 5.3 自检方法清单（按测试类别）

| 类别 | 方法 | 何时用 |
|------|------|--------|
| **静态** | `python -c "import termify"` 通过；`python -m py_compile app.py termify/*.py` 无错；（若装了 ruff/flake8）跑一遍 | 每次提交前 |
| **动态（必过）** | `pytest -q` —— **基线测试须全过**（123+ 随轮次增长）。再对样例 GIF（`E:\Desktop\工作\SalaryCat\cat.GIF`）实际跑 `python demo.py 猫图.gif --charset all` 生成全部 5 种风格的 `.py` 与 `.html`，并**真实运行验证可播放** | 每次提交前 + 每次 PR |
| **端到端 .py 执行** | `subprocess.run()` 启动生成的 .py，捕获 stderr，1-2s 后 kill；验证退出码 0、无 Traceback、stdout 含预期字符 | T1 测试（详见 §10.2） |
| **端到端 HTML 渲染** | 用 BeautifulSoup 解析 HTML，验证无语法错、所有 FRAMES 非空、canvas/pre/JS 函数存在 | T2 测试 |
| **视觉回归** | 对每种 charset × 标准猫图做输出 FrameSequence hash；改代码后 hash 变化需人工 review | T9 测试 |
| **CLI 批处理** | `python demo.py file1.gif file2.gif --charset all` 验证多文件全产出 | T5/T6 测试 |
| **Web API 端到端** | `flask.test_client()` 走 upload→preview→generate→download 全链路 | T7 测试 |
| **终端兼容** | mock 测试 `_enable_windows_ansi()` 调用和失败降级路径 | T8 测试 |
| **错误处理** | 上传非 GIF/PNG/JPG、超大文件、损坏文件、空文件 | T10 测试 |
| **文档一致性** | grep 校验关键数字在 PRD/README/状态文件三处一致——**测试数、风格数=5、API 端点数、版本号** | 每次改文档后 |
| **安全** | `python "C:\Users\laotie_nb666\.claude\skills\rpd\scripts\security-scanner.py" .` 扫描当前目录 | 每次提交前 |

> ⚠️ **每轮必须包含**：T1（端到端 .py 执行）+ T2（端到端 HTML 渲染）+ T3（真实图片）。这是底线，缺一不可。

### 5.4 错误处置流程（六步，闭环）
1. **复现 & 定位**：记录复现步骤、报错堆栈、受影响文件行号（截图/日志）。
2. **分级**：按 §5.2 定为 L0–L3。
3. **修复**：遵循**最小变更原则**，不顺手重构无关代码；涉及架构决策先查 `.project-state.md` 关键决策记录。
4. **验证**：重新跑 §5.3 全部相关项，确认该错误已消除且未引入新 L0/L1。
5. **判定解决**：
   - L0 / L1：**必须清零**才能 commit / merge，无例外。
   - L2：本里程碑内解决，或明确写入状态文件「已知问题」并标注 `deferred(原因)`。
   - L3：顺手修，不阻塞流程。
6. **沉淀**：见 §5.5。

### 5.5 经验总结与沉淀（让项目越做越聪明）
- 每解决一个 **非平凡**（L0/L1 或反复出现的 L2）问题，追加一条到 `.project-state.md` 的 **诊断记录 / 经验** 章节：`问题 → 根因 → 修法 → 预防`。
- 同步追加到本项目 memory：`E:\Desktop\工作\观猹\Termify\.workbuddy\memory\YYYY-MM-DD.md` 的「避坑」段。
- 累计形成「**避坑清单**」，后续 agent 开工前先扫一遍，避免重蹈覆辙。
- 若发现的是**流程级**教训（如"文档改动别混进 fix 分支"），更新本文件 §6/§7。

---

## 6. 版本管理（Git）提交规则

> 当前仓库已采用分支工作流：`main` 受保护，开发在功能分支（如观测到的 `fix/download-player-issues`），远程 `origin = https://github.com/ZhangJing-gugugaga/Termify.git`。本规则将其规范化。

### 6.1 分支策略
- **禁止在 `main` 直接提交/推送**。每次逻辑改动开独立分支：`type/scope-简述`。
- **type 取值**：`feat`(新功能) / `fix`(修复) / `docs`(文档) / `refactor`(重构) / `perf`(性能) / `test`(测试) / `chore`(杂项)。
- **分支命名示例**：`feat/charset-adaptive-bucket`、`fix/windows-truecolor-fallback`、`docs/prd-v2-handoff`、`perf/numpy-vectorize`。
- **文档类改动单独走 `docs/` 分支**（重要）：当前仓库里 PRD.md 与分析文档的未提交改动混在了 `fix/download-player-issues` 分支上——接手 agent 应先将它们 `git stash`/迁出到 `docs/handoff-docs` 分支再提交，保持分支单一职责。

### 6.2 Commit Message 规范（Conventional Commits，中文）
```
type(scope): 中文简述（关联 P0/P1/P2 任务）

body（为什么做、影响范围、参考来源）
```
- 示例：
  - `feat(charset): 灰度映射改自适应分桶(P0)，画质提升`
  - `fix(player): Windows 旧终端 TrueColor 自动降级为 ascii(P0)`
  - `docs(prd): PRD 升至 v2.0，修复 4 处与实现不符项`
  - `perf(engine): charset 映射 numpy 向量化，提速约 20x(P1)`
- **禁止**：无类型、无 scope、纯英文无中文简述、`"update"`/`"fix bug"` 之类无意义信息、`--no-verify` 跳过钩子。

### 6.3 合并规则（不直接 merge main）
- 改完 → 推到 `origin` **同名分支** → 提 **PR / MR**。
- PR 须经：§5 自检全过（L0/L1 清零）+ QA 验证通过 +（人工或 reviewer agent）确认。
- **agent 不自动 merge 到 main**；merge 动作由人或被显式授权时执行。
- 合并后删除远程功能分支（可选保留本地）。

### 6.4 禁止项（红线）
- ❌ 在 `main` 直接 `commit` / `push`
- ❌ `git push --force` 到 `main` 或已开 PR 的分支
- ❌ 提交密钥、`.env`、大二进制（>20MB 素材）、`venv/`、`__pycache__/`、`node_modules/`
- ❌ `git commit --no-verify`
- ❌ 把文档改动与代码改动混在同一分支/同一 commit（除非强相关）

### 6.5 提交前必跑（gate）
```
pytest -q                              # 基线全过（123+，随轮次增长）
python .../security-scanner.py .       # 无高危
# 若改了 PRD/状态文件：
python .../prd-validator.py PRD.md
python .../state-validator.py .project-state.md
```

---

## 7. 文档维护规则（提交即更新）

**铁律：代码改动提交前，必须同步更新对应文档；文档与代码不一致即 L2 错误。**

### 7.1 改动后必须同步更新的文件

| 改动类型 | 必须更新的文档 | 更新内容 |
|----------|----------------|----------|
| 新增/修改功能 | `PRD.md` | 功能进度清单对应行状态（⏳→✅）、需求变更、API 章节 |
| 新增/修改功能 | `.project-state.md` | 进度清单 + **变更日志**（日期+内容）+ 若涉决策则更新关键决策记录 |
| 新增/修改功能 | `README.md` | 用法、示例、新能力说明 |
| 新增/修改功能 | `Termify-用户问题手册.md`（高级章节） | API/部署/新能力对应的 Q&A |
| 新增依赖 | `requirements.txt` | 包名==版本 |
| 改 API | `PRD.md`(API 章) + `Termify-用户问题手册.md`(高级) | 端点、参数、示例 |
| 纯文档改动 | `PRD.md` / `.project-state.md` | 对应章节 + 变更日志 |
| 任何改动 | `.workbuddy/memory/YYYY-MM-DD.md` | 当日工作摘要（append） |

### 7.2 更新后校验
- `python .../state-validator.py .project-state.md` 通过
- （改 PRD 时）`python .../prd-validator.py PRD.md` 通过
- 手动 grep 关键数字（测试数 / 风格 5 / 版本号）三处一致

### 7.3 示例：完成「自适应灰度分桶」后
1. `termify/charset.py` 改灰度映射为自适应分桶（~20 行）→ `pytest` 全过。
2. `PRD.md`：进度清单「融合-自适应灰度分桶」状态 ⏳→✅；若发现需补测试则记到测试章节。
3. `.project-state.md`：进度清单同改；变更日志加一行 `2026-07-17 feat(charset): 自适应分桶画质提升`；若踩坑则诊断记录加一条。
4. `README.md`：如有用法变化则更新（本例可不改）。
5. `git` 走 `feat/charset-adaptive-bucket` 分支提交，commit 关联 P0。
6. 当日 memory 追加一条。

---

## 8. 长期开发节奏与沉淀机制

- **推进顺序**：严格按 `功能进度清单` 的 P0→P1→P2；每个 P 级任务完成后回写状态文件 + memory + 跑全量回归。
- **里程碑回顾**：每完成一个 M 阶段（M0–M3），做一次回顾——升 `.project-state.md` 版本号、memory 浓缩、可选落盘交付报告到 `deliverables/software-company/`。
- **避坑清单累积**：§5.5 的经验持续追加，形成项目专属知识库；新 agent 开工前先读「诊断记录 / 经验」章节。
- **技术债看板**：`.project-state.md`「当前阻塞项」持续维护，解决一项划掉一项，新增一项补一项。
- **参考文档保鲜**：竞品/技术文档（四视角/竞品融合）作为决策依据保留，新融合落地后在对应文档标注「已实施」。

---

## 9. 铁律红线（不可违反，违反即 L0/L1）

1. 🔴 **护城河铁律**：所有改动强化「可下载/可运行/可分享文件」，不得弱化。
2. 🔴 **不把引擎全量编译成 WASM 导出**（会丢护城河 + 撞竞品强项）；实时只做预览增强。
3. 🔴 **视频走后端 ffmpeg**，不在前端跑 ffmpeg.wasm。
4. 🔴 **不跳过 §5 自检**；L0/L1 未清零禁止 commit/merge。
5. 🔴 **不在 `main` 直接提交**，必须走分支 + PR。
6. 🔴 **提交必更新文档**（§7），文档与代码不一致 = L2。
7. 🔴 **不提交密钥/大文件/venv/__pycache__**。

---

## 10. 当前轮次专项：Bug 修复 + 测试集扩展（Round 1）

> **本轮定位**：用户人工实测 Web 端发现多个产品级 bug，现有 123 个测试未能覆盖（只验引擎数学，未验用户实际拿到的产品）。目标：**修复所有用户报告的 bug** + **大幅扩展测试集**（123 → 107+）。完成后由主理人审查，审查通过后再更新 GOAL-PROMPT 开启 T1.2-T1.12 开发。

### 10.1 用户实测发现的 Bug（必修，至少要解决到 L1 清零）

| ID | Bug | 截图证据 | 根因（已读代码核实） | 修复方向 |
|----|-----|---------|------------------|---------|
| **B1** | Binary HTML 在所有分辨率下输出过于稀疏（猫图几乎全空） | `clipboard-2026-07-17T17-04-05-037Z-050a44a4.png` | `termify/charset.py` `_render_binary` 用**固定阈值** `_luminance(r,g,b) < 128`，**未走 `_adaptive_lut`**；对偏亮/偏暗图像大面积空白 | 把 `_adaptive_lut` 应用到 binary；用拉伸后 luma 的 127 作为二值分界 |
| **B2** | Braille HTML 也有类似稀疏问题（程度较轻） | 同 B1 | `_render_braille` 已用 `_adaptive_lut` 但分界仍是**硬编码 128**，对极端分布图像不友好 | 分界改为动态（如用 luma 中位数或固定 127 on stretched value）；补 extreme histogram 测试 |
| **B3** | Unicode 色块 .py 在 Windows 终端输出完全乱码 | `clipboard-2026-07-17T17-04-05-039Z-4b5268c4.png` / `...-041Z-15d8d739.png` | `termify/output/python.py` `_enable_windows_ansi()` 失败时**静默 except**；老 cmd.exe/旧 PowerShell 不支持 VT100；`▀` 字符在某些 Windows 终端字体不可见 | (a) 检测失败时 stderr 警告 + 引导用 HTML 格式；(b) `blocks` 风格在 ANSI 不可用时降级为 `ascii` 并提示；(c) 跑前检测终端能力（`TERM`/`WT_SESSION`/`COLORTERM`） |
| **B4** | 高分辨率下 HTML 超出页面 | 用户描述，img1 周边滚动条可见 | `termify/output/html.py` CSS `pre { font-size:13px; white-space:pre; overflow:auto }` 在 200×60 风格下撑爆视口 | CSS 加 `max-height:80vh`；或 JS 动态计算最佳字号自适应 |

> 其他未列出的 bug（如其他 charset/尺寸的潜在问题）以用户/QA 实测为准，**不限于上表**。

### 10.2 必须新增的测试集（按类别，覆盖 T1-T10）

| 类别 | 覆盖内容 | 最低用例数 | 关联 Bug/场景 |
|------|---------|-----------|--------------|
| **T1 端到端 .py 执行** | `subprocess.run()` 启动生成的 .py，捕获 stderr，1-2s 后 kill；验证退出码 0、无 Traceback、stdout 含预期字符 | 5 charset × 2 尺寸 = **10** | B3 |
| **T2 端到端 HTML 渲染** | 用 BeautifulSoup 解析 HTML，验证无语法错、所有 FRAMES 非空、canvas/pre/JS 函数存在 | 5 charset × 2 尺寸 = **10** | B1/B2/B4 |
| **T3 真实图片** | 用 `E:\Desktop\工作\SalaryCat\cat.GIF`（用户提供），跑 5 charset × 3 尺寸（80×24, 120×36, 200×60），输出文件可读且非空 | 5×3 = **15** | B1/B2/B3 |
| **T4 Binary 阈值质量** | 黑图(全█)、白图(全空格)、中灰图(50/50)、偏亮图(>50% 亮)、偏暗图(>50% 暗) | **5** | B1 |
| **T5 CLI (demo.py)** | `python demo.py cat.GIF --charset all` 一次生成 5 文件验证存在；参数化 `--width/--height` | **3** | 全场景 |
| **T6 批量处理** | `python demo.py file1.gif file2.gif file3.png --charset all` 多文件全产出 | **2** | 性能 + 错误处理 |
| **T7 Web API 端到端** | `flask.test_client()` 走 upload→preview→generate→download，下载内容可解析 | 5 charset × 1 = **5** | Web 集成 |
| **T8 终端兼容性** | 检测生成的 .py 包含 `\x1b[38;2;` 和 `\x1b[48;2;`；mock 测试 `_enable_windows_ansi()` 调用和失败降级路径 | **3** | B3 |
| **T9 视觉回归** | 对每种 charset × SalaryCat 猫图做输出 FrameSequence hash；改代码后 hash 变化需人工 review | **5** | 防回归 |
| **T10 错误处理** | 上传非 GIF/PNG/JPG、超大文件、损坏文件、空文件 | **4** | 健壮性 |

**合计最低 62 个新测试**（123 → 107+）。每个测试必须有 docstring 说明覆盖的 bug 或场景。

### 10.3 完成标准（验收清单）

1. ✅ 上述 B1-B4 全部修复，并被对应测试覆盖（T1-T10 中至少一项）
2. ✅ 测试集达到 §10.2 最低数量，全绿 0 跳过
3. ✅ 真实 SalaryCat 猫图用所有 charset 生成 .py 和 .html，QA 用子进程/浏览器实际验证可运行
4. ✅ `pytest -q` 全绿，GOAL-PROMPT §1 中测试数更新为 107+
5. ✅ `.project-state.md` 与 `PRD.md` 中测试数三处一致
6. ✅ 提交走 `fix/<scope>` 分支，commit 关联 B1-B4 / T1-T10 编号
7. ✅ 文档与代码同步更新（PRD §5.5 阈值说明、README 用法、问题手册）

### 10.4 本轮执行方式（标准 SOP）

- **许清楚（产品经理）**：基于本节生成**增量 PRD §A.1-Bug 跟踪表**（与 B1-B4 同步）
- **高见远（架构师）**：B1/B2 涉及 `_adaptive_lut` 跨 charset 复用、B3 涉及终端能力检测层——先做 5 分钟的架构影响评估（不需完整设计，**不阻塞工程师起步**）
- **寇豆码（工程师）**：实现 B1-B4 修复 + 编写 T1-T10 测试 → 全量一致性审查（IS_PASS: YES）→ 提交 PR
- **严过关（QA）**：在 PR 上复跑测试 + 用真实猫图 + 子进程/无头浏览器验证 → 智能路由判定
- **齐活林（主理人）**：审查代码、测试、QA 报告 → 决定 merge main → 审查后**再次更新 GOAL-PROMPT** 开启 T1.2-T1.12 下一轮

### 10.5 铁律追加（Round 1 特别强调）

1. 🔴 **任何对 `_adaptive_lut` 的修改必须保持向后兼容**（其他 charset 已在用，不能回归 ascii/geometric/braille）
2. 🔴 **B1-B4 修复不允许扩大改动面**（不顺手重构无关代码）
3. 🔴 **T1-T10 测试必须可独立运行**（不依赖网络、外部服务）
4. 🔴 **视觉回归测试 hash 必须能被人工 review**（不要 hash 整个 HTML，只 hash 关键 frame 数据）
5. 🔴 **真实猫图 SalaryCat cat.GIF 是本轮必跑素材**（不可以用合成图替代）

---

## 11. 关联参考文档（深度依据，按需读取）
- `PRD.md` — PRD V2.0.0（问题→方案映射 / 扩展 TODO / 参考项目 / 上线部署）
- `Termify-四视角分析.md` — 外行小白/小白程序员/产品经理/嵌入式四视角结论
- `Termify-用户问题手册.md` — 三层 35 条 Q&A
- `Termify-竞品融合建议.md` — 竞品产品创意融合（许清楚）
- `Termify-竞品技术融合分析.md` — 竞品技术栈/实现融合（寇豆码，含伪代码）
- `E:\Desktop\工作\SalaryCat` — 开发参考项目（half-block / color delta / PyInstaller 思路来源）；`cat.GIF` 是本轮必跑测试素材
- rpd 技能：`C:\Users\laotie_nb666\.claude\skills\rpd\`（SKILL.md / references / scripts / 各模型 adapter）

---

## 12. 启动提示词（精简版）

把以下文本作为首条消息发给新 agent，即可让任意模型接管项目：

```
工作区：`E:\Desktop\工作\观猹\Termify`
项目：Termify — 把 GIF/PNG/JPG 转成终端动画（.py / .html），MIT 开源、零注册。

【先读这 3 个文件再动手】
1. `.project-state.md` — 项目状态、决策、当前轮次任务、阻塞项
2. `PRD.md` — PRD V2.0.0（rpd 15 节）
3. `GOAL-PROMPT.md` — 本文件（铁律/自检/版本管理/文档维护）

【rpd 技能路径】
`C:\Users\laotie_nb666\.claude\skills\rpd\`
先跑 `python .../intent-router.py "继续开发 Termify"` 路由意图。

【铁律（不可违反）】
1. 所有改动强化「产出可下载/可运行/可分享文件」
2. 不把引擎全量编译成 WASM 导出
3. 视频走后端 ffmpeg
4. 不在 main 直接提交
5. L0/L1 未清零禁止 commit/merge
6. 提交必更新文档

【当前轮次】
看 `.project-state.md`「当前阻塞项」+ GOAL-PROMPT §10（本轮专项：Bug 修复 + 测试集扩展）。

【自检（提交前必跑）】
- `pytest -q` 基线全过（当前 123+，本轮目标 107+）
- 端到端 .py 用 subprocess 跑一遍确认不崩（T1）
- 端到端 HTML 用 BeautifulSoup 解析确认有内容（T2）
- 真实猫图 SalaryCat cat.GIF 跑全部 charset（T3）
- 文档三处测试数一致

【完成后】
更新 `.project-state.md` 进度+变更日志，记当日 memory。
```

（精简版不含完整 SOP 细节，复杂决策回查本文件正文。）
