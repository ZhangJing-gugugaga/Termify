# Termify · 阿里云 ECS 部署指南

> 目标：把 Termify（Flask + Pillow 的图片转字符画服务）部署到阿里云 ECS，
> 通过 Caddy 反代对外提供 `https://YOUR_DOMAIN`，由 systemd 守护进程。
>
> 适用系统：**Ubuntu 22.04+ / Debian 12+**（其他发行版把 `apt` 换成对应包管理器即可）。
>
> 预计耗时：首次部署 10–15 分钟（含 pip 安装 + Caddy 签发证书）。

---

## 1. 前置条件

| 项目 | 要求 | 怎么验 |
|------|------|--------|
| **DNS** | `YOUR_DOMAIN` 的 A 记录已解析到 ECS 公网 IP | `dig YOUR_DOMAIN +short` |
| **端口** | 安全组放行 TCP 80 / 443（入方向） | 阿里云控制台 → ECS → 安全组 |
| **Caddy** | 已安装 Caddy v2.7+ | `caddy version` |
| **Python** | 3.10+（系统自带） | `python3 --version` |
| **本仓库** | `Caddyfile` / `termify.service` / `deploy.sh` 三个文件已在仓库根目录 | `ls` |

> ⚠️ **80 端口必须开**：Caddy 申请 Let's Encrypt 证书时走 HTTP-01 challenge，
> 如果 80 被封或被 nginx 占着，TLS 签发会失败。

---

## 2. 安装 Caddy（二选一）

### 2.1 走 apt 官方源（**推荐**）

```bash
sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
  | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/deb/debian_any_version.list' \
  | sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt update
sudo apt install caddy
```

### 2.2 Ubuntu 仓库直接装（更省事，但版本可能略旧）

```bash
sudo apt install caddy
```

装完确认：

```bash
caddy version    # 应 >= v2.7.0
```

---

## 3. 一键部署（推荐）

```bash
# 1. 把仓库拉到本地后 scp/rsync 到 ECS，或直接在 ECS 上 git clone
cd /tmp
git clone https://github.com/ZhangJing-gugugaga/Termify.git
cd Termify

# 2. 执行部署脚本
sudo chmod +x deploy.sh
sudo ./deploy.sh
```

`deploy.sh` 做了这些事（每一步出错立即 `set -e` 退出）：

1. 校验 root 权限
2. `apt update && install python3-pip python3-venv git`
3. 创建 `/opt/termify` 及 `logs/ data/ uploads/gallery/` 子目录
4. `git clone` 或 `git pull --rebase`（**重跑幂等**）
5. `python3 -m venv venv` 并 `pip install -r requirements.txt gunicorn`
6. 把 `termify.service` 装到 `/etc/systemd/system/` 并 `daemon-reload`
7. `systemctl enable --now termify`
8. 探活 `127.0.0.1:5000` 并打印后续步骤

部署完会输出一段绿色提示，照着做 Caddy 配置即可。

---

## 4. 配置 Caddy 反代

把仓库里的 `Caddyfile` 接进 Caddy：

### 方案 A：直接覆盖主配置（**最简单**）

```bash
sudo cp /opt/termify/Caddyfile /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

### 方案 B：include 进去（保留你原有站点）

```bash
sudo mkdir -p /etc/caddy/Caddyfile.d
sudo cp /opt/termify/Caddyfile /etc/caddy/Caddyfile.d/termify.caddy
# 在 /etc/caddy/Caddyfile 末尾加：
#     import /etc/caddy/Caddyfile.d/*.caddy
sudo systemctl reload caddy
```

第一次 `systemctl start caddy` 时会自动去 Let's Encrypt 申请证书。
等 10–30 秒后查看：

```bash
sudo journalctl -u caddy -f          # 看到 "certificate obtained successfully" 就成了
curl -I https://YOUR_DOMAIN   # 应返回 200/302 + 证书有效
```

---

## 5. 部署后验收清单

打开浏览器访问 `https://YOUR_DOMAIN`，逐项勾选：

- [ ] **首页加载** — Hero 区显示"图片 → 终端字符画"标题，无 502/504
- [ ] **HTTPS 锁** — 浏览器地址栏有 🔒，证书是 Let's Encrypt / ZeroSSL
- [ ] **单图转换** — 上传 ≤ 1MB 的 PNG/JPG，选"python"格式，30 秒内出结果
- [ ] **大图上传** — 上传 5–10MB 的 GIF，验证分块字符画能渲染（不报 413）
- [ ] **极值上传** — 尝试上传 25MB 文件，应被 Caddy 拦下（413）；20MB 应能成功
- [ ] **画廊 / 分享** — 进入 `/gallery`，点赞、发布到画廊、访问 `/v/<id>` 链接均正常

附加可选项：

- [ ] 静态资源缓存头：`curl -I https://YOUR_DOMAIN/static/css/main.css` 应见 `cache-control: public, max-age=86400`
- [ ] X-Content-Type-Options / X-Frame-Options 响应头存在

---

## 6. 常见问题（FAQ）

### 6.1 Caddy 申请证书失败：`acme: error: 403 ...`

- **80 端口没开**：安全组必须放行 TCP 80 入站。
- **DNS 没解析**：`dig YOUR_DOMAIN` 应返回 ECS 公网 IP，否则 Let's Encrypt 校验不通过。
- **被本地 nginx/apache 占了 80**：`sudo systemctl stop nginx` 再 `systemctl restart caddy`。
- **Caddy 拿到了自签证书**：删掉 `/var/lib/caddy/.local/share/caddy` 重新申请。

### 6.2 上传 5MB 文件报 413

Caddy 默认请求体上限是 10MB，已在 `Caddyfile` 里调到 25MB。
改完后 **必须** `sudo systemctl reload caddy` 才会生效。

### 6.3 报 502 Bad Gateway

`termify.service` 没起来或 Flask 没监听 5000：

```bash
sudo systemctl status termify
sudo journalctl -u termify -n 100 --no-pager
sudo ss -ltnp | grep :5000
```

最常见原因：第一次启动时 gunicorn 还没装好，把 systemd 单元里的
`ExecStart` 临时换成 `python app.py` 排错。

### 6.4 转换大 GIF 超时

默认 gunicorn `--timeout 300`（5 分钟）。如果图特别大、worker 被占满：
- 临时调大 `termify.service` 里 `--timeout 600` + `Restart=always`
- 或加 worker：`--workers 4`（默认）改 `--workers 8`（注意 CPU 核数）
- Caddy 侧 `read_timeout` 也同步调到 600s

### 6.5 systemd 看不到 print 输出

`Environment=PYTHONUNBUFFERED=1` 已经加在 service 文件里，确保它生效：
`sudo systemctl show termify | grep -i env`。如果还看不到，`journalctl -u termify`
可能是被 `StandardOutput=journal` 截断了——这是正常现象，看 `logs/error.log` 即可。

### 6.6 SQLite "database is locked"

并发写画廊时偶发。临时方案：`PRAGMA journal_mode=WAL;`（已经在 Phase 2
处理过）；如果还出现，部署时挂载 tmpfs：

```bash
sudo mount -t tmpfs -o size=64M tmpfs /opt/termify/data
```

---

## 7. 备份策略

`data/termify.db` 是 SQLite 库，`uploads/gallery/` 是用户上传的原图——
这两个目录是 Termify 唯一的持久化数据。强烈建议每天 cron 备份。

### 7.1 备份脚本

```bash
sudo tee /opt/termify/backup.sh > /dev/null <<'EOF'
#!/usr/bin/env bash
set -euo pipefail
APP=/opt/termify
DST=${APP}/backups
mkdir -p "${DST}"
TS=$(date +%Y%m%d-%H%M%S)

# SQLite 走 .backup 命令拿到一致快照（比 cp 安全）
sqlite3 "${APP}/data/termify.db" ".backup '${DST}/termify-${TS}.db'"

# uploads 走 tar 压缩
tar -czf "${DST}/uploads-${TS}.tar.gz" -C "${APP}" uploads

# 只保留最近 14 天
find "${DST}" -mtime +14 -delete
EOF
sudo chmod +x /opt/termify/backup.sh
```

### 7.2 cron 每天 03:00 跑一次

```bash
echo "0 3 * * * /opt/termify/backup.sh >> /var/log/termify-backup.log 2>&1" \
  | sudo crontab -
```

### 7.3 异地同步（可选）

如果是 ECS，建议再用 `ossutil` 把 `backups/` 同步到阿里云 OSS：

```bash
ossutil cp -r /opt/termify/backups/ oss://your-bucket/termify-backups/
```

---

## 8. 升级 / 回滚

```bash
# 升级到最新
cd /opt/termify && sudo git pull
sudo systemctl restart termify

# 回滚到上一个版本
cd /opt/termify && sudo git log --oneline -5
sudo git reset --hard <commit-hash>
sudo systemctl restart termify
```

数据库迁移：Termify 的 `GalleryDB.init_db()` 是幂等的，升级不需要手动跑 schema。

---

## 9. 安全检查清单

- [ ] Termify 服务**只**监听 `127.0.0.1:5000`（外网直接访问 5000 应被拒）
- [ ] 安全组 22 只对白名单 IP 开放 SSH
- [ ] 安全组 80/443 对 0.0.0.0/0 开放
- [ ] `termify.service` 后续可改成专用用户 `termify` 收紧权限
- [ ] 启用 `fail2ban` 防 SSH 爆破
- [ ] 启用 UFW：`ufw allow 22,80,443/tcp && ufw enable`

---

部署顺利 🍀  任何坑可以提 issue 附上 `journalctl -u termify -n 200` 的输出。
