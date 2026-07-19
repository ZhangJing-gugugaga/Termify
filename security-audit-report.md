# Termify 公开仓库安全审计报告

> 审计时间：2026-07-19 15:05
> 审计对象：https://github.com/ZhangJing-gugugaga/Termify.git（main 分支，HEAD 9ab0fa7）

---

## 一、安全风险（需要处理）

### 🔴 P0 — 高优先级

#### 1. 本地文件路径泄露（隐私泄露）
**文件**：`tests/test_t11_blocks_ansi_regression.py`、`tests/test_t3_real_image.py`、`tests/test_t17_gallery.py`

硬编码了开发机的真实路径和个人项目路径：
```python
CAT_GIF = r"E:\Desktop\工作\SalaryCat\cat.GIF"   # 出现在 3 个文件
REAL_GIF = r"E:\Desktop\工作\SalaryCat\cat.GIF"
```
**风险**：暴露 Windows 用户名 `laotie_nb666`（通过仓库 owner + 路径推断），以及本地文件结构。对外人完全无用且不可执行。

**建议**：用环境变量 `os.environ.get("TERMIFY_TEST_GIF", "sample.gif")` 替换，或者把真实图片测试放到 CI secret。

---

#### 2. Admin 密码通过 URL Query String 传输
**文件**：`app.py` 第 740、801-803、821-824 行

```python
request.args.get("pwd") == _admin_pwd()
```
**风险**：密码会出现在：
- 浏览器历史记录
- Caddy / Nginx access log（明文！）
- Referrer 头（如果页面内有外链）
- 截屏/分享 URL 中

**建议**：废弃 Query String 传密码，统一走 `X-Termify-Admin-Pwd` Header 或 Cookie（已经同时支持了）。Query String 方式仅保留做测试兼容但不推荐。

---

#### 3. Flask 缺少 SECRET_KEY（潜在会话伪造）
**文件**：`app.py`

```python
app = Flask(__name__)
# 没有 app.secret_key = ...
```
**风险**：如果未来有人引入 Flask session，会直接使用默认/空密钥，可被轻松伪造。当前代码未使用 Flask session，目前是安全的，但这是一个定时炸弹。

**建议**：添加 `app.secret_key = os.environ.get("FLASK_SECRET_KEY", os.urandom(24).hex())`。

---

### 🟡 P1 — 中优先级

#### 4. 生产域名硬编码全仓库
**文件**（7 个）：`Caddyfile`、`deploy.sh`、`docs/DEPLOY-ECS.md`、`README.md`、`app.py`、`termify.service`

域名字符串 `termify.moonzj.com` 出现在多处。
**风险**：公开暴露生产环境的攻击面（Caddy 版本、超时设置等）。攻击者可直接定位目标。

**建议**：README 可保留域名做展示；`deploy.sh` / `Caddyfile` 用 `YOUR_DOMAIN` 占位符。

---

### 🟢 P2 — 低优先级

#### 5. .gitignore 泄露开发工具链
**文件**：`.gitignore`

揭示了你使用的工具栈：
- `.claude/` — Claude AI 助手
- `.mimocode/` — Mimocode
- `.workbuddy/` — WorkBuddy 智能体
- `.playwright-mcp/` — Playwright MCP 自动化测试
- `static/test/` — 浏览器测试临时文件
- `salarycat` 项目引用

虽然不直接构成安全漏洞，但降低了对攻击者的侦察成本。

**建议**：无需紧急处理，但注释可以精简为泛用描述（如 `# AI assistant session data`）。

---

## 二、对外人无用的文件（建议移除）

### 立即移除（纯内部开发垃圾）

| 文件 | 大小 | 说明 |
|------|------|------|
| `static/test_geometric.html` | 520 KB | 内部开发测试页面，被 Flask 作为静态资源直接暴露在 `/static/test_geometric.html` |
| `static/test_geometric_fixed.py` | 152 KB / 2141 行 | 几何 ASCII 测试脚本，对外人无意义 |
| `static/test_geometric_v2.py` | 153 KB / 2157 行 | 同上，v2 版本 |
| **合计** | **~825 KB** | 无意义的仓库膨胀 + 可通过 Web 直接访问 |

> ⚠️ `.gitignore` 里有 `test_*.py` 忽略规则，但这三个文件**已经被 git 跟踪**，说明是被 `git add -f` 强制提交的——规则被绕过，且不一致。

---

### 考虑移除或泛化

| 文件 | 原因 |
|------|------|
| `deploy.sh` | 个人阿里云 ECS 部署脚本，路径 `/opt/termify`、域名 `termify.moonzj.com` 全硬编码。外人的服务器路径和域名必然不同 |
| `Caddyfile` | 同上，个人域名的 Caddy 反代配置 |
| `termify.service` | 个人 systemd 配置，`User=root` 绑定 `/opt/termify` |
| `docs/DEPLOY-ECS.md` | 个人部署文档，目录结构、域名、备份方案都是针对你自己的 ECS |
| `termify.spec` | PyInstaller 打包配置。如果确实想让人自己打包 .exe 可保留，但目前对外人用处有限 |
| `version_info.txt` | PyInstaller 配套文件，同上 |

> 💡 **建议方案**：如果想让外人能部署，单独建 `contrib/deploy/` 目录，用 `${DOMAIN}` 和 `${APP_DIR}` 占位符重写这些文件，让它们变成**模板**而不是你的个人配置。

---

## 三、Git 历史遗留（已完成但需确认）

从 git log 看：
```
9ab0fa7 chore: 移除误提交的私人部署清单 (DEPLOY-MY-ECS.md) + 加固 .gitignore
```

`DEPLOY-MY-ECS.md` 已经从当前 HEAD 删除了，但因为是 shallow clone（`--depth 1`），无法确认它是否还在历史 commit 中。如果完整 clone 的 git history 中仍含该文件，攻击者可以通过 `git log --all --full-history -- DEPLOY-MY-ECS.md` 恢复它。

**建议**：去 GitHub repo 的 commit history 界面确认该 commit 之前的版本是否还可见。如果可见，需要用 `git filter-branch` 或 `bfg` 彻底清除。

---

## 四、清单汇总

| # | 类型 | 严重度 | 文件/位置 | 问题 |
|---|------|--------|-----------|------|
| 1 | 🔒 安全 | P0 | `tests/test_t3_real_image.py:13` | 本地路径 `E:\Desktop\工作\SalaryCat\cat.GIF` 泄露 |
| 2 | 🔒 安全 | P0 | `tests/test_t11_blocks_ansi_regression.py:31` | 同上 |
| 3 | 🔒 安全 | P0 | `tests/test_t17_gallery.py:526` | 同上 |
| 4 | 🔒 安全 | P0 | `app.py:740,801-803,821` | Admin 密码在 URL query string 明文传输 |
| 5 | 🔒 安全 | P0 | `app.py:23` | Flask 缺少 SECRET_KEY |
| 6 | 🔒 信息 | P1 | `Caddyfile`, `deploy.sh`, `README.md` 等 7 文件 | 生产域名 `termify.moonzj.com` 硬编码 |
| 7 | 🔒 信息 | P2 | `.gitignore` | 开发工具链暴露（Claude/WorkBuddy/Mimocode/Playwright） |
| 8 | 🗑️ 无用 | — | `static/test_geometric.html` | 520KB 内部测试页面（可通过 Web 访问） |
| 9 | 🗑️ 无用 | — | `static/test_geometric_fixed.py` | 152KB 内部测试脚本 |
| 10 | 🗑️ 无用 | — | `static/test_geometric_v2.py` | 153KB 内部测试脚本 |
| 11 | 🗑️ 无用 | — | `deploy.sh` | 个人部署脚本，域/路径硬编码 |
| 12 | 🗑️ 无用 | — | `Caddyfile` | 个人反代配置 |
| 13 | 🗑️ 无用 | — | `termify.service` | 个人 systemd 配置 |
| 14 | 🗑️ 无用 | — | `docs/DEPLOY-ECS.md` | 个人部署文档 |
| 15 | 🗑️ 可选 | — | `termify.spec` + `version_info.txt` | PyInstaller 打包文件（外人用处有限） |
| 16 | 🗑️ 可选 | — | `.gitignore` 规则不一致 | `test_*.py` 被忽略但同名文件已被 track |
| 17 | ⚠️ 历史 | — | Git history | `DEPLOY-MY-ECS.md` 可能在历史 commit 中仍可恢复 |

---

## 五、建议操作（按优先级）

```
1. [立即] 删除 static/test_geometric.{html,fixed.py,v2.py}，从 git 中移除
2. [立即] 替换 tests/ 中的硬编码本地路径为环境变量
3. [尽快] app.py 中废弃 Query String 传 admin 密码
4. [尽快] 给 Flask 添加 SECRET_KEY
5. [本周] deploy.sh/Caddyfile/service 泛化为模板，或移入 contrib/deploy/
6. [本周] 确认 DEPLOY-MY-ECS.md 在 git history 中已无法恢复
7. [可延后] .gitignore 注释去敏
```
