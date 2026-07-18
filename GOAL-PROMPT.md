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
| 技术栈 | 后端 Python 3.10+ / Flask / Pillow；前端原生 HTML/CSS/JS（无框架）；测试 pytest（当前 **128**，随轮次扩展；Round 1 已完结，下一轮由用户指定）。 |
| 已完成 v1.0 | 5 种渲染风格（ascii/blocks/braille/geometric/binary）、`.py`+`.html` 输出、Web+CLI 双形态、全屏自适应+`music.mp3` 音频、REST API（upload/preview/generate/download）、blocks TrueColor ANSI 修复（已合 main）。 |
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
| `E:\Desktop\工作\观猹\Termify\tests/` | pytest 用例（当前 **128**） | 改引擎/加功能时补测试 |
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
| **动态（必过）** | `pytest -q` —— **基线测试须全过**（128，随轮次增长）。再对样例 GIF（`E:\Desktop\工作\SalaryCat\cat.GIF`）实际跑 `python demo.py 猫图.gif --charset all` 生成全部 5 种风格的 `.py` 与 `.html`，并**真实运行验证可播放** | 每次提交前 + 每次 PR |
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
pytest -q                              # 基线全过（128，随轮次增长）
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

## 10. ✅ 已完成轮次：Bug 修复 + 测试集扩展（Round 1）



> B1-B4 全部修复（含 blocks TrueColor ANSI 渲染大修）、T1-T10 测试集已扩展至 **128 全绿**、已合 main（34efcf2）。
> 具体涉及：`_enable_windows_ansi` 重写（PTY 下返回 True）、`_setup_output_encoding`（Win 始终 UTF-8）、SGR 正则匹配真实 `\x1b`、parse/encode 保留 fg+bg、`_ANSI_OK` 降级单色。
> 详见 `.project-state.md` 变更日志和 `.workbuddy/memory/2026-07-18.md`。

**下一步**：功能扩展（上线前先补实用功能）。见 §10 → §11 焦点推荐。

---

## 11. 💡 当前开发焦点：功能扩展（本期三项）

> 本期只做 ① Web 批量上传 → ② URL 直输 → ③ ffmpeg 视频接入。
> 每项完成后：合并 main → 更新 `.project-state.md` + memory → 通知主理人审查。

---

### ① Web 批量上传 — ✅ 已完成（feat/batch-upload 分支，PR #3 已批准）

**变更**:
- 后端：新增 `POST /api/upload-batch`（multipart `files[]`），返回 `task_ids[]` + `errors[]`
- 前端：上传区支持多文件（`multiple` + 拖拽多文件），调用 batch 端点
- 测试：+5 tests（T11-batch: 双文件/三文件混合/无效过滤/空拒绝/独立 task_id），128→133 全绿
- 文档：README API 章节 + FAQ + 上传提示文案

---

### ② URL 直输（P1 — 次项）

**需求**：Web 界面支持一次上传多个文件（选择或拖拽多个 GIF/PNG/JPG），批量转换，每个文件可单独选风格/尺寸/格式下载；文件之间不相互阻塞。

**技术要点**：
- 后端：新增 `POST /api/upload-batch`（multipart 多文件），返回 `task_ids[]`。复用现有 `termify.convert()` 流水线，不要改引擎。
- 前端：上传区改造为多文件支持，拖拽/点选允许多选，每个文件独立的状态卡片（文件名、风格选择、下载按钮），用 fetch 并行上传。
- 不需要写轮子——Flask 原生支持 multiple file input（`request.files.getlist("files")`）。

**涉及文件**：`app.py`（新增路由）、`static/js/app.js`（改造上传组件）、`static/css/app.css`（多文件卡片布局）、`templates/index.html`（上传区 DOM 微调）

**UI 改动时参考的设计 Skills（下面有完整路径索引）**：`ui-ux-pro-max`、`ux-design`、`ux-review`

**测试**：新增 T11-batch 测试（flask test_client 模拟多文件上传，验证各文件独立状态正确）。

---
### ② URL 直输 — ✅ 已完成（feat/batch-upload 分支，PR #3）

**变更**: 后端 POST /api/fetch-url + SSRF 防护（内网IP拦截/15s超时/20MB限制/PIL验证）；前端粘贴链接输入框；+6 tests 133→140 全绿

---
### ② URL 直输（P1 — 次项）~~旧版~~

**需求**：用户粘贴一个图片/GIF 在线链接 → 服务器下载该 → 进入转换流程（无需先下载再上传）。

**技术要点**：
- 后端：新增 `POST /api/fetch-url`，接收 `{"url":"..."}` → `urllib.request.urlopen(url)` → 校验响应头 Content-Type 是否为 image/gif/png/jpeg → 写入临时文件 → 复用 `termify.convert()` 流水线。
- 安全：限制 IP（防 SSRF）、设置下载超时（15s）、限制文件大小（同上传 20MB）、校验真实类型（PIL 尝试打开，防类型伪装）。
- 前端：上传区新增"粘贴链接"输入框或按钮，与拖拽上传并列。

**涉及文件**：`app.py`（新增路由）、`static/js/app.js`（前端链接输入组件）、`static/css/app.css`

**FireCrawl 分析结论**：排除。`github.com/firecrawl/firecrawl` 的 `extractImages.ts` 是**网页 HTML 爬图片**（从 `<img>`/`<meta>` 标签提取 URL），不适合直接下载用户粘贴的图片直链。`video.ts` 调用外部 avgrab 服务做视频发现，与 Termify 不需要的依赖。（**不需依赖 FireCrawl**，一个 `urllib` 调用即可。）

**UI 改动时参考的设计 Skills**：`ui-ux-pro-max`、`ux-design`

---
### ③ 后端 ffmpeg 视频接入 — ✅ 已完成（feat/batch-upload 分支，PR #3）

**变更**:
- 后端：新增 `POST /api/upload-video`，`termify/video.py` ffmpeg subprocess 抽帧
- 校验：扩展名/大小(20MB)/时长(30s via ffprobe)
- 流程：上传 → validate → ffmpeg 抽帧(10fps) → 逐帧 convert → FrameSequence
- 前端：上传区格式提示 MP4/WEBM 已支持
- 测试：+6 tests (T13-video-upload)，140→146 全绿

---
### ③ 后端 ffmpeg 视频接入（P1 — 后续）~~旧版~~

**需求**：用户上传 MP4/WEBM 视频 → 后端用 ffmpeg 抽帧 → 进入引擎转换。

**技术要点**：
- 依赖：新增 `ffmpeg-python` 或直接 `subprocess ffmpeg`。**不走前端 ffmpeg.wasm**（铁律）。
- 流程：上传 → 校验视频（扩展名/大小/时长限制） → ffmpeg 抽帧为 PNG 序列 → `termify.convert()` 逐帧处理。建议限制视频时长 ≤30s、文件大小同 20MB。
- API：复用 `POST /api/upload`（自动检测视频类型走不同路径），或新增 `POST /api/upload-video`。
- 需在 `requirements.txt` 和部署文档标注 ffmpeg 是可选运行时依赖（不像 Pillow 是纯 Python 包）。

**涉及文件**：`app.py`（新增视频处理分支）、`termify/engine.py`（可能需适配帧序列输入）、新文件 `termify/video.py`（ffmpeg 抽帧逻辑）、`tests/`（新增视频端到端测试）、`PRD.md`（更新进度清单）、`requirements.txt`（加 ffmpeg-python 标记）。

**约束**：遵守 PRD §5.2「视频处理走后端 ffmpeg」和护城河铁律（不因视频输入弱化可下载可运行文件输出）。

**测试**：新增视频端到端测试（ffmpeg 抽小测试视频 → convert → 验证输出），以及异常测试（格式不支持、超长时长、大文件）。**运行时需要 ffmpeg 安装在测试环境。**

---
### 🎨 前端 UI 改动时的设计 Skills 索引

当前端界面需要修改时，加载以下 Skills 获得设计规范、组件库和审查标准：

| Skill 路径 | 作用 | 加载方式 |
|-----------|------|---------|
| `D:\Skills and plugins\design\skills\ui-ux-pro-max` | UI/UX 全流程设计规范（布局、配色、动效、组件） | `Skill("ui-ux-pro-max")` 或 `Skill(command="D:/Skills and plugins/design/skills/ui-ux-pro-max")` |
| `D:\Skills and plugins\design\skills\ux-design` | 用户体验设计原则（流程、交互、可访问性） | 同上 |
| `D:\Skills and plugins\design\skills\ux-review` | UI 审查清单（一致性、无障碍、响应式） | 同上 |

**用法**：每当改 `static/js/app.js`、`static/css/app.css`、`templates/index.html` 或新增前端页面时，先加载对应 Skill 获得规范指引，改完后用 `ux-review` 做审查。

---
### 约束提醒
- 护城河铁律不动摇：所有改动强化「可下载/可运行/可分享文件」
- 视频走后端 ffmpeg，不碰 ffmpeg.wasm；不把引擎全量编译 WASM 导出
- 每项完成后更新 `.project-state.md` 进度清单 + 变更日志 + 当日 memory
- 测试数文档三处一致（PRD/状态/README）

---

## 12. 关联参考文档（深度依据，按需读取）
- `PRD.md` — PRD V2.0.0（问题→方案映射 / 扩展 TODO / 参考项目 / 上线部署）
- `Termify-四视角分析.md` — 外行小白/小白程序员/产品经理/嵌入式四视角结论
- `Termify-用户问题手册.md` — 三层 35 条 Q&A
- `Termify-竞品融合建议.md` — 竞品产品创意融合（许清楚）
- `Termify-竞品技术融合分析.md` — 竞品技术栈/实现融合（寇豆码，含伪代码）
- `E:\Desktop\工作\SalaryCat` — 开发参考项目（half-block / color delta / PyInstaller 思路来源）；`cat.GIF` 是本轮必跑测试素材
- rpd 技能：`C:\Users\laotie_nb666\.claude\skills\rpd\`（SKILL.md / references / scripts / 各模型 adapter）

---

## 13. 启动提示词（精简版）

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
看 `.project-state.md`「当前阻塞项」+ GOAL-PROMPT §11（功能扩展三期：① Web 批量上传 → ② URL 直输 → ③ ffmpeg 视频接入）。

【前端 UI 改动必读】
涉及 `static/` 或 `templates/` 的更改，先加载对应设计 Skill：
- `Skill("ui-ux-pro-max")` — UI/UX 规范
- `Skill("ux-design")` — 交互设计
- `Skill("ux-review")` — 审查清单

【自检（提交前必跑）】
- `pytest -q` 基线全过（当前 128）
- 端到端 .py 用 subprocess 跑一遍确认不崩（T1）
- 端到端 HTML 用 BeautifulSoup 解析确认有内容（T2）
- 真实猫图 SalaryCat cat.GIF 跑全部 charset（T3）
- 文档三处测试数一致

【完成后】
更新 `.project-state.md` 进度+变更日志，记当日 memory。
```

（精简版不含完整 SOP 细节，复杂决策回查本文件正文。）