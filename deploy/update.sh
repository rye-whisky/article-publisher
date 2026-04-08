#!/bin/bash
# 更新脚本 - 只更新代码，不重新配置

set -e

echo "======================================"
echo "Article Publisher 更新"
echo "======================================"

APP_DIR="/opt/article-publisher"

# 1. 备份配置
echo "[1/3] 备份配置..."
cp $APP_DIR/config.yaml /tmp/config.yaml.bak

# 2. 拉取最新代码
echo "[2/3] 更新代码..."
CURRENT_DIR="$(pwd)"
PROJECT_ROOT="$(dirname "$CURRENT_DIR")"

cp -r $PROJECT_ROOT/backend $APP_DIR/
cp -r $PROJECT_ROOT/frontend $APP_DIR/

# 3. 更新依赖并重启服务
echo "[3/3] 更新依赖..."
source $APP_DIR/venv/bin/activate
pip install --upgrade -r $APP_DIR/requirements.txt

systemctl restart article-publisher

sleep 2

if systemctl is-active --quiet article-publisher; then
    echo "更新成功！"
else
    echo "更新失败，回滚配置..."
    cp /tmp/config.yaml.bak $APP_DIR/config.yaml
    systemctl restart article-publisher
    exit 1
fi
