#!/usr/bin/env bash
# =============================================================================
# Termify 一键部署脚本 — 阿里云 ECS / Ubuntu 22.04+ / Debian 12+
# -----------------------------------------------------------------------------
# 适用场景：在干净的云服务器上把 Termify 部署成 systemd 服务，由 Caddy 反代。
# 作用范围：
#   - 安装系统依赖（python3-pip / python3-venv / git）
#   - git clone + 拉取最新代码到 /opt/termify
#   - 创建 venv 并安装 requirements.txt（gunicorn 在此脚本里额外装）
#   - 注册并启动 termify.service
#
# 不会做的事：
#   - 安装 Caddy（请按 docs/DEPLOY-ECS.md 单独安装，apt 装 caddy 一行就完事）
#   - 申请/配置 TLS 证书（Caddy 自动做，前提是 DNS 已解析 + 80/443 开放）
#   - 改写你的 SSH / 安全组 / 防火墙
#
# 用法：
#   chmod +x deploy.sh
#   sudo ./deploy.sh
#
# 重跑是幂等的：第二次执行会 git pull + 重启服务，不会覆盖数据。
# =============================================================================

set -euo pipefail

# ---------- 颜色输出 --------------------------------------------------------
if [[ -t 1 ]]; then
    C_RESET='\033[0m'
    C_RED='\033[31m'
    C_GREEN='\033[32m'
    C_YELLOW='\033[33m'
    C_BLUE='\033[34m'
    C_BOLD='\033[1m'
else
    C_RESET=''; C_RED=''; C_GREEN=''; C_YELLOW=''; C_BLUE=''; C_BOLD=''
fi

log()    { printf "${C_BLUE}[*]${C_RESET} %s\n" "$*"; }
ok()     { printf "${C_GREEN}[✓]${C_RESET} %s\n" "$*"; }
warn()   { printf "${C_YELLOW}[!]${C_RESET} %s\n" "$*"; }
err()    { printf "${C_RED}[✗]${C_RESET} %s\n" "$*" >&2; }
header() { printf "\n${C_BOLD}${C_BLUE}== %s ==${C_RESET}\n" "$*"; }

# ---------- 0. root 权限校验 ------------------------------------------------
header "环境检查"
if [[ $EUID -ne 0 ]]; then
    err "请用 root 跑：sudo $0"
    exit 1
fi
ok "root 权限 OK"

# ---------- 1. 系统信息 ------------------------------------------------------
. /etc/os-release
log "检测到系统：${PRETTY_NAME:-Linux}"

# ---------- 2. 安装系统依赖 --------------------------------------------------
header "安装系统依赖（python3-pip / python3-venv / git）"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv \
    git ca-certificates curl
ok "系统依赖安装完成"

# ---------- 3. 准备部署目录 --------------------------------------------------
APP_DIR=/opt/termify
REPO_URL=https://github.com/ZhangJing-gugugaga/Termify.git
SERVICE_FILE=/etc/systemd/system/termify.service

header "准备部署目录 ${APP_DIR}"
mkdir -p "${APP_DIR}"
mkdir -p "${APP_DIR}/logs"
mkdir -p "${APP_DIR}/data"
mkdir -p "${APP_DIR}/uploads/gallery"
ok "目录就绪：${APP_DIR}{,/logs,/data,/uploads/gallery}"

# ---------- 4. clone 或 pull -------------------------------------------------
header "同步代码仓库"
if [[ ! -d "${APP_DIR}/.git" ]]; then
    log "首次部署：git clone ${REPO_URL} -> ${APP_DIR}"
    git clone "${REPO_URL}" "${APP_DIR}"
    ok "clone 完成"
else
    log "已存在 .git，执行 git pull（带 submodule）"
    git -C "${APP_DIR}" pull --rebase --autostash
    ok "pull 完成"
fi

# 强制把可能存在的 test 截图带进来（README 提到 tests/ 需要 git add -f）
# 实际仓库已经把 tests/ 提交了，这里只是兜底，幂等无害。
git -C "${APP_DIR}" log -1 --pretty=%h 2>/dev/null | awk '{print "当前 HEAD: " $1}'

# ---------- 5. Python 虚拟环境 -----------------------------------------------
header "创建 venv 并安装依赖"
VENV=${APP_DIR}/venv
if [[ ! -d "${VENV}" ]]; then
    log "创建虚拟环境 ${VENV}"
    python3 -m venv "${VENV}"
    ok "venv 创建完成"
else
    log "检测到已有 venv，复用"
fi

log "升级 pip"
"${VENV}/bin/pip" install --upgrade pip wheel setuptools --quiet

log "安装 requirements.txt（项目核心依赖）"
"${VENV}/bin/pip" install -r "${APP_DIR}/requirements.txt" --quiet
ok "项目依赖安装完成"

log "安装 gunicorn（生产 WSGI 服务器，不写入 requirements.txt）"
"${VENV}/bin/pip" install gunicorn --quiet
ok "gunicorn 安装完成"

# ---------- 6. 注册 systemd 服务 --------------------------------------------
header "注册 termify.service"
if [[ -f "${APP_DIR}/termify.service" ]]; then
    install -m 0644 "${APP_DIR}/termify.service" "${SERVICE_FILE}"
    ok "已复制 termify.service -> ${SERVICE_FILE}"
else
    err "找不到 ${APP_DIR}/termify.service，请确认仓库根目录有该文件"
    exit 1
fi

systemctl daemon-reload
ok "systemd 重载完成"

# ---------- 7. 启动并设置开机自启 -------------------------------------------
header "启动 termify 服务"
systemctl enable termify.service
systemctl restart termify.service

# 给 gunicorn 1-2 秒拉起
sleep 2

# 健康检查
if systemctl is-active --quiet termify.service; then
    ok "termify.service 运行中"
else
    err "termify.service 启动失败，查看日志："
    warn "  journalctl -u termify -n 50 --no-pager"
    warn "  cat ${APP_DIR}/logs/error.log"
    exit 1
fi

# 简单探活：Flask 默认 5000 端口
if curl -sSf -o /dev/null http://127.0.0.1:5000/; then
    ok "本地 127.0.0.1:5000 响应正常"
else
    warn "本地 5000 端口未响应，可能是首次冷启动，再等几秒："
    warn "  journalctl -u termify -f"
fi

# ---------- 8. 完成提示 -----------------------------------------------------
header "部署完成 ✅"
cat <<EOF
${C_GREEN}接下来请按 docs/DEPLOY-ECS.md 完成 Caddy 反代配置：${C_RESET}

  1) 安装 Caddy（如果还没装）：
       apt install -y debian-keyring debian-archive-keyring apt-transport-https
       curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' | gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
       curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/deb/debian_any_version.list' | tee /etc/apt/sources.list.d/caddy-stable.list
       apt update && apt install caddy

  2) 把仓库根目录的 Caddyfile 拷到 /etc/caddy/Caddyfile（或 include 进去）：
       sudo cp ${APP_DIR}/Caddyfile /etc/caddy/Caddyfile.d/termify.caddy
       # 或直接覆盖 /etc/caddy/Caddyfile（推荐先备份）

  3) 启动 Caddy：
       sudo systemctl enable --now caddy

  4) 访问 https://YOUR_DOMAIN/ 验收。

常用运维命令：
  - 查看服务状态：   sudo systemctl status termify
  - 实时日志：       sudo journalctl -u termify -f
  - 错误日志：       tail -f ${APP_DIR}/logs/error.log
  - 重新部署：       sudo ${APP_DIR}/deploy.sh
  - 手动重启：       sudo systemctl restart termify
EOF
