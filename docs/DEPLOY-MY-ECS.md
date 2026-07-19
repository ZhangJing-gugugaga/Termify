# Termify · 阿里云 ECS 部署操作清单

> 目标：把 Termify 部署到 **123.57.30.132**，通过 `https://termify.moonzj.com` 访问。
>
> 适用系统：Ubuntu 22.04+ / Debian 12+（用 root 跑命令）。
>
> 预计耗时：首次部署 **10–15 分钟**（含 pip 安装 + Let's Encrypt 签证书）。
>
> 全部代码已在分支 `feat/deploy-ecs` 提交 `08fb88d`，可直接拉取。

---

## 0. 前置条件清单（开始前确认）

| # | 项目 | 验证命令（Windows PowerShell 跑） | 通过标志 |
|---|------|----------------------------------|---------|
| 0.1 | DNS 已解析 | `nslookup termify.moonzj.com` | 返回 `123.57.30.132` |
| 0.2 | 80 端口可达 | `Test-NetConnection 123.57.30.132 -Port 80` | `TcpTestSucceeded: True` |
| 0.3 | 443 端口可达 | `Test-NetConnection 123.57.30.132 -Port 443` | `TcpTestSucceeded: True` |
| 0.4 | 22 端口可达 | `Test-NetConnection 123.57.30.132 -Port 22` | `TcpTestSucceeded: True` |
| 0.5 | 本地有 SSH 私钥 | — | 能 `ssh root@123.57.30.132` 登录 |

> ⚠️ **80 端口必须开**：Caddy 用 HTTP-01 challenge 申请证书，没 80 就签不下来。
> ⚠️ **443 第一次会失败**：还没签证书前 HTTPS 不可用，跑完第 6 步才会通。

### 阿里云控制台手动配置

**DNS（云解析 DNS）**：
- 域名 `moonzj.com` → 解析设置 → 添加记录
  | 主机记录 | 记录类型 | 记录值 | TTL |
  |---------|---------|--------|-----|
  | `termify` | A | `123.57.30.132` | 600 |

**安全组（ECS → 网络与安全 → 安全组 → 配置规则）**：
| 方向 | 协议 | 端口 | 源 | 策略 |
|------|------|------|------|------|
| 入方向 | TCP | 80 | `0.0.0.0/0` | 允许 |
| 入方向 | TCP | 443 | `0.0.0.0/0` | 允许 |
| 入方向 | TCP | 22 | `你的办公 IP/32` | 允许（**不要** 0.0.0.0/0） |
| 入方向 | TCP | 5000 | `127.0.0.1/32` | 拒绝（Caddy 反代后 5000 只走 loopback，**外网必须封**） |

---

## 1. SSH 登录 ECS

```bash
# Windows PowerShell / Git Bash 均可
ssh root@123.57.30.132
```

第一次会问 fingerprint，确认 `yes`。登录后看到 `#` 提示符 = 你是 root。

> **创建专用用户（可选，安全性更高）**：跳过本期，用 root 简化首版。后续再 `useradd -r -s /usr/sbin/nologin -d /opt/termify termify`。

---

## 2. 安装 Caddy

> Caddy 负责 HTTPS 证书 + 反代到 Flask。**不在 deploy.sh 里装**，因为装完要等 DNS 解析生效再签证书。

```bash
# 2.1 加 Caddy 官方源
apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/deb/debian_any_version.list' \
  | tee /etc/apt/sources.list.d/caddy-stable.list

# 2.2 装
apt update
apt install caddy

# 2.3 验证
caddy version
# 应看到 v2.7.0 或更新
```

> **如果嫌麻烦的备选**：`apt install caddy`（Ubuntu 仓库版本可能旧，但能用）。

---

## 3. 拉取 Termify 仓库

```bash
# 3.1 创建部署目录
mkdir -p /opt
cd /opt

# 3.2 git clone（首次）
git clone https://github.com/ZhangJing-gugugaga/Termify.git termify
cd termify

# 3.3 切到部署分支（含 4 个部署文件）
git fetch origin
git checkout feat/deploy-ecs

# 3.4 确认 4 个文件都在
ls -la Caddyfile termify.service deploy.sh docs/DEPLOY-ECS.md
# 应看到 4 个文件
```

---

## 4. 跑 deploy.sh 一键部署

```bash
cd /opt/termify
chmod +x deploy.sh
sudo ./deploy.sh
```

**deploy.sh 做的事**（每步出错会立即 `set -e` 退出）：
1. root 权限校验
2. `apt install python3-pip python3-venv git ca-certificates curl`
3. 创建 `/opt/termify/{logs,data,uploads/gallery}/`
4. `git clone` 或 `git pull`（**重跑幂等**）
5. 创建 venv + `pip install -r requirements.txt gunicorn`
6. 把 `termify.service` 装到 `/etc/systemd/system/` + `daemon-reload`
7. `systemctl enable --now termify`
8. 探活 `127.0.0.1:5000` 并打印下一步

**预期成功输出**（最后几行）：
```
[*] 检测到系统：Ubuntu 22.04.x
[✓] 系统依赖安装完成
[✓] 目录就绪：/opt/termify{/logs,/data,/uploads/gallery}
[✓] clone 完成
[✓] venv 创建完成
[✓] 项目依赖安装完成
[✓] gunicorn 安装完成
[✓] 已复制 termify.service -> /etc/systemd/system/termify.service
[✓] systemd 重载完成
[✓] termify.service 运行中
[✓] 本地 127.0.0.1:5000 响应正常

== 部署完成 ✅ ==
```

**如果失败**：脚本会打印提示，复制完整输出给我看。常见原因：
- `apt update` 锁文件被占（另一 apt 在跑，等几分钟重试）
- pip install 网络超时（重跑 deploy.sh 即可，已做幂等）

---

## 5. 配 Caddyfile 并启动 Caddy

```bash
# 5.1 备份默认 Caddyfile（如果存在）
[ -f /etc/caddy/Caddyfile ] && cp /etc/caddy/Caddyfile /etc/caddy/Caddyfile.bak.$(date +%Y%m%d)

# 5.2 用 Termify 的 Caddyfile 覆盖（最简单）
cp /opt/termify/Caddyfile /etc/caddy/Caddyfile

# 5.3 启动 Caddy（首次会自动申请 Let's Encrypt 证书）
systemctl enable --now caddy

# 5.4 实时看 Caddy 日志（证书签发过程会显示）
journalctl -u caddy -f
# 看到 "certificate obtained successfully" 或 "served HTTPS" = 成功
# Ctrl+C 退出
```

> **如果保留其他站点**：用方案 B（include），编辑 `/etc/caddy/Caddyfile` 末尾加 `import /etc/caddy/Caddyfile.d/*.caddy`，然后 `cp /opt/termify/Caddyfile /etc/caddy/Caddyfile.d/termify.caddy`。
>
> **DNS 没生效会失败**：如果 `caddy` 启动报 `acme: error: 403`，先 `systemctl stop caddy`，等 DNS 生效（dig 确认），再 `systemctl start caddy`。

---

## 6. 部署后验收（6 项必过）

打开浏览器（**手机或另一台电脑**更佳），访问 `https://termify.moonzj.com`：

| # | 测试 | 通过标志 |
|---|------|---------|
| 6.1 | HTTPS 锁 | 地址栏有 🔒 标志，证书是 Let's Encrypt |
| 6.2 | 首页加载 | Hero 区显示"图片 → 终端字符画"标题，无 502/504 |
| 6.3 | 上传 PNG/JPG（≤ 1MB）→ 选 Python → 出结果 | 30 秒内出现 .py 下载按钮 |
| 6.4 | 上传 5–10MB GIF → 选 Blocks 风格 | 不报 413，能看到字符画预览 |
| 6.5 | 极值上传：拖一个 30MB 文件 | Caddy 应返回 413（拦下） |
| 6.6 | 进 `/gallery` → 上传一个作品 → 复制短链 `/v/<id>` → 浏览器新窗口打开 | 看到完整 Termify + 预填状态 |

附加验收（可选）：
```bash
# 静态资源缓存头
curl -I https://termify.moonzj.com/static/css/app.css | grep -i cache-control
# 应见: cache-control: public, max-age=86400

# 安全响应头
curl -I https://termify.moonzj.com | grep -iE "x-content-type|x-frame"
# 应见: X-Content-Type-Options: nosniff / X-Frame-Options: SAMEORIGIN
```

---

## 7. 配置 SQLite + uploads 每日备份

```bash
# 7.1 创建备份脚本
cat > /opt/termify/backup.sh <<'BASH'
#!/usr/bin/env bash
set -euo pipefail
APP=/opt/termify
DST=${APP}/backups
mkdir -p "${DST}"
TS=$(date +%Y%m%d-%H%M%S)

# SQLite 走 .backup 命令（一致性快照）
sqlite3 "${APP}/data/termify.db" ".backup '${DST}/termify-${TS}.db'"

# uploads 走 tar 压缩
tar -czf "${DST}/uploads-${TS}.tar.gz" -C "${APP}" uploads

# 只保留 14 天
find "${DST}" -mtime +14 -delete
BASH
chmod +x /opt/termify/backup.sh

# 7.2 装 sqlite3 CLI（如果没装）
apt install -y sqlite3

# 7.3 加 cron：每天凌晨 3 点
echo "0 3 * * * /opt/termify/backup.sh >> /var/log/termify-backup.log 2>&1" | crontab -

# 7.4 验证 cron 装上了
crontab -l
# 应见: 0 3 * * * /opt/termify/backup.sh >> /var/log/termify-backup.log 2>&1

# 7.5 手动跑一次看效果
/opt/termify/backup.sh
ls /opt/termify/backups/
# 应见 termify-<时间戳>.db 和 uploads-<时间戳>.tar.gz
```

---

## 8. 常用运维命令速查

```bash
# 状态
systemctl status termify        # Termify 服务状态
systemctl status caddy          # Caddy 反代状态
ss -ltnp | grep :5000           # 看 Flask 是否监听

# 日志
journalctl -u termify -f        # 实时看 Termify 日志
journalctl -u caddy -f          # 实时看 Caddy 日志
tail -f /opt/termify/logs/error.log   # Flask 错误日志
tail -f /var/log/caddy/termify.log    # Caddy 访问日志

# 升级
cd /opt/termify && git pull
sudo /opt/termify/deploy.sh     # 完整重部署（含拉代码 + 重启）

# 只重启服务（不动代码）
systemctl restart termify
systemctl reload caddy

# 改完 Caddyfile 后重载
systemctl reload caddy
# 或彻底重启（重读证书）
systemctl restart caddy
```

---

## 9. 常见问题（FAQ）

### Q1. Caddy 报 `acme: error: 403`（证书签发失败）
**A**：DNS 没解析到 ECS 或 80 端口被挡。
```bash
# 验证
dig termify.moonzj.com +short     # 应见 123.57.30.132
curl -I http://123.57.30.132/     # 应见 200（Caddy 起的 80 端口）

# 修复
# 1. DNS 加 A 记录，等 5 分钟
# 2. 安全组放行 80
# 3. systemctl restart caddy
```

### Q2. 浏览器报 `502 Bad Gateway`
**A**：termify.service 没起或 Flask 没监听 5000。
```bash
systemctl status termify
journalctl -u termify -n 50 --no-pager
ss -ltnp | grep :5000            # 应见 gunicorn 监听 127.0.0.1:5000

# 最常见：第一次启动 gunicorn 还没装好（deploy.sh 里装过了，应不会）
# fallback：临时改成 python app.py 排查
systemctl edit termify
# 加: [Service]
#     ExecStart=
#     ExecStart=/opt/termify/venv/bin/python app.py
systemctl daemon-reload
systemctl restart termify
```

### Q3. 上传 5MB 文件报 413
**A**：Caddyfile 没生效。`max_size 25MB` 改完必须 reload：
```bash
cp /opt/termify/Caddyfile /etc/caddy/Caddyfile
systemctl reload caddy
```

### Q4. 转换大 GIF 超时（5+ 分钟）
**A**：默认 gunicorn `--timeout 300`。可调大：
```bash
systemctl edit termify
# 加:
#   [Service]
#   Environment=GUNICORN_TIMEOUT=600
# 同步改 termify.service 把 --timeout 300 改成 --timeout 600
nano /opt/termify/termify.service   # 改 --timeout
systemctl daemon-reload
systemctl restart termify
# 同步 Caddyfile read_timeout 300s -> 600s
systemctl reload caddy
```

### Q5. `data/termify.db` 锁了
**A**：并发写画廊时偶发。Termify 已用 WAL 模式；如还出现：
```bash
sqlite3 /opt/termify/data/termify.db "PRAGMA busy_timeout = 5000;"
```

---

## 10. 回滚指南（万一出问题）

```bash
# 10.1 看历史版本
cd /opt/termify && git log --oneline -5

# 10.2 回滚到上一个版本
cd /opt/termify && git reset --hard <commit-hash>
systemctl restart termify

# 10.3 极端情况：彻底回滚
systemctl stop termify
rm -rf /opt/termify
git clone https://github.com/ZhangJing-gugugaga/Termify.git /opt/termify
cd /opt/termify
git checkout <branch-name>
sudo ./deploy.sh
```

数据库无需手动迁移（`GalleryDB.init_db()` 幂等）。

---

## 🎉 部署成功标志

- ✅ `systemctl status termify` → `active (running)`
- ✅ `systemctl status caddy` → `active (running)`
- ✅ `curl -I https://termify.moonzj.com` → `HTTP/2 200`
- ✅ 浏览器 6 项验收全过
- ✅ `crontab -l` 含 backup cron

**任何一步报错**：把 `journalctl -u termify -n 100` 和 `journalctl -u caddy -n 50` 的输出复制贴给我，我帮你诊断。

部署顺利 🍀
