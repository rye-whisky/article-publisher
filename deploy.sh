#!/bin/bash
# deploy.sh — 一键部署/更新 Article Publisher 到服务器
# 用法：bash deploy.sh [服务器IP] [用户名]
# 环境变量：DEPLOY_HOST, DEPLOY_USER, DEPLOY_SSH_KEY
# 示例：DEPLOY_SSH_KEY=~/.ssh/id_rsa bash deploy.sh 1.2.3.4 root

set -euo pipefail

# ── 配置 ─────────────────────────────────────────────────────────
REMOTE_HOST="${1:-${DEPLOY_HOST:-}}"
REMOTE_USER="${2:-${DEPLOY_USER:-root}}"
SSH_KEY="${DEPLOY_SSH_KEY:-}"
REMOTE_DIR="/opt/article-publisher"
SERVICE_NAME="article-publisher"
REMOTE_TAR="/tmp/${SERVICE_NAME}-deploy.tar.gz"
LOCAL_TAR="/tmp/${SERVICE_NAME}-deploy.tar.gz"

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*"; exit 1; }

# ── 前置检查 ─────────────────────────────────────────────────────
PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
[ -d "$PROJECT_DIR/backend" ] || error "请在项目根目录运行此脚本"
[ -f "$PROJECT_DIR/backend/api.py" ] || error "backend/api.py 不存在"
[ -z "$REMOTE_HOST" ] && error "请设置 DEPLOY_HOST 或传入服务器IP"
[ -z "$SSH_KEY" ] && error "请设置 DEPLOY_SSH_KEY 环境变量"

# SSH 密钥路径（Windows Git Bash 兼容）
if [ ! -f "$SSH_KEY" ]; then
    SSH_KEY="$(cygpath -u "$SSH_KEY" 2>/dev/null || echo "$SSH_KEY")"
fi
[ -f "$SSH_KEY" ] || error "SSH 密钥不存在: $SSH_KEY"

SSH_OPTS=(-i "$SSH_KEY" -o StrictHostKeyChecking=no)
run_ssh() { ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "$@"; }
run_scp() { scp "${SSH_OPTS[@]}" "$@"; }

info "目标: ${REMOTE_USER}@${REMOTE_HOST}"
info "项目: ${PROJECT_DIR}"
echo ""

# ── 1. 本地构建前端 ──────────────────────────────────────────────
info "1/4 构建前端..."
if [ -d "$PROJECT_DIR/frontend/node_modules" ]; then
    (cd "$PROJECT_DIR/frontend" && npx vite build --logLevel error 2>&1) || error "前端构建失败"
    info "前端构建完成"
else
    warn "跳过前端构建（node_modules 不存在）"
fi

# ── 2. 打包 + 上传 ──────────────────────────────────────────────
info "2/4 打包并上传..."

# 打包：backend/ + requirements.txt + frontend/dist/
cd "$PROJECT_DIR"
INCLUDE="backend/api.py backend/cli.py"
for d in services pipelines routes models utils ai_pipelines middleware config; do
    [ -d "backend/$d" ] && INCLUDE="$INCLUDE backend/$d"
done
tar czf "$LOCAL_TAR" $INCLUDE requirements.txt frontend/dist

run_scp "$LOCAL_TAR" "${REMOTE_USER}@${REMOTE_HOST}:${REMOTE_TAR}"
rm -f "$LOCAL_TAR"
info "上传完成"

# ── 3. 远程部署 ──────────────────────────────────────────────────
info "3/4 远程部署..."
run_ssh bash -s <<DEPLOY
set -e
cd ${REMOTE_DIR}

echo "  停止服务..."
systemctl stop ${SERVICE_NAME} 2>/dev/null || true

echo "  清理 __pycache__..."
find backend -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

echo "  解压新代码..."
tar xzf ${REMOTE_TAR}
rm -f ${REMOTE_TAR}

echo "  检查依赖..."
if [ -f requirements.txt ]; then
    venv/bin/pip install -q -r requirements.txt 2>&1 | tail -1
fi

echo "  设置权限..."
chown -R ${SERVICE_NAME}:${SERVICE_NAME} backend/ frontend/dist/ 2>/dev/null || true

echo "  启动服务..."
systemctl start ${SERVICE_NAME}
sleep 2

if systemctl is-active --quiet ${SERVICE_NAME}; then
    echo "  服务启动成功"
else
    echo "  ERROR: 服务启动失败"
    journalctl -u ${SERVICE_NAME} --no-pager -n 20
    exit 1
fi
DEPLOY

# ── 4. 显示结果 ──────────────────────────────────────────────────
info "4/4 确认状态..."
echo ""
run_ssh "systemctl is-active ${SERVICE_NAME} && journalctl -u ${SERVICE_NAME} --no-pager -n 5"
echo ""
info "部署完成！http://${REMOTE_HOST}:8000"
