#!/bin/bash
# 一键部署脚本 - 复制项目并启动服务

set -e

echo "======================================"
echo "Article Publisher 一键部署"
echo "======================================"

APP_DIR="/opt/article-publisher"

# 1. 复制项目文件
echo "[1/4] 复制项目文件..."
CURRENT_DIR="$(pwd)"
PROJECT_ROOT="$(dirname "$CURRENT_DIR")"

# 停止服务（如果运行中）
systemctl stop article-publisher 2>/dev/null || true

# 清理旧文件（保留配置）
rm -rf $APP_DIR/backend
rm -rf $APP_DIR/frontend
rm -rf $APP_DIR/deploy

# 复制新文件
cp -r $PROJECT_ROOT/backend $APP_DIR/
cp -r $PROJECT_ROOT/frontend $APP_DIR/
cp -r $PROJECT_ROOT/deploy $APP_DIR/
cp -f $PROJECT_ROOT/config.yaml $APP_DIR/

# 2. 构建前端（如果需要）
echo "[2/4] 检查前端..."
if [ ! -d "$APP_DIR/frontend/dist" ]; then
    echo "前端未构建，正在构建..."
    cd $APP_DIR/frontend
    $APP_DIR/venv/bin/npm install
    $APP_DIR/venv/bin/npm run build
    cd $APP_DIR
fi

# 3. 设置权限
echo "[3/4] 设置权限..."
chown -R article-publisher:article-publisher $APP_DIR
chmod -R 755 $APP_DIR

# 4. 启动服务
echo "[4/4] 启动服务..."
systemctl daemon-reload
systemctl restart article-publisher
sleep 2

# 检查状态
if systemctl is-active --quiet article-publisher; then
    echo ""
    echo "======================================"
    echo "部署成功！"
    echo "======================================"
    echo ""
    echo "服务状态:"
    systemctl status article-publisher --no-pager -l
    echo ""
    echo "访问地址: http://$(hostname -I | awk '{print $1}')"
else
    echo ""
    echo "部署失败，查看日志:"
    journalctl -u article-publisher -n 50 --no-pager
    exit 1
fi
