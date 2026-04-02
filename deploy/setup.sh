#!/bin/bash
# Article Publisher 部署脚本
# 适用于 Ubuntu/Debian 系统

set -e

echo "======================================"
echo "Article Publisher 部署脚本"
echo "======================================"

# 检查是否为root
if [ "$EUID" -ne 0 ]; then
    echo "请使用 sudo 运行此脚本"
    exit 1
fi

# 1. 系统更新
echo "[1/7] 更新系统..."
apt update && apt upgrade -y

# 2. 安装依赖
echo "[2/7] 安装系统依赖..."
apt install -y python3 python3-pip python3-venv nginx postgresql-client

# 3. 创建应用目录
APP_DIR="/opt/article-publisher"
echo "[3/7] 创建应用目录: $APP_DIR"
mkdir -p $APP_DIR
mkdir -p $APP_DIR/logs
mkdir -p $APP_DIR/data
mkdir -p $APP_DIR/output

# 4. 创建Python虚拟环境
echo "[4/7] 创建Python虚拟环境..."
python3.11 -m venv $APP_DIR/venv
source $APP_DIR/venv/bin/activate

# 5. 安装Python依赖
echo "[5/7] 安装Python依赖..."
pip install --upgrade pip

# 将需要的包列表保存到 requirements.txt
cat > $APP_DIR/requirements.txt << 'EOF'
fastapi==0.104.1
uvicorn[standard]==0.24.0
pyyaml==6.0.1
requests==2.31.0
beautifulsoup4==4.12.2
lxml==4.9.3
psycopg2-binary==2.9.9
sqlalchemy==2.0.23
psutil==5.9.6
sshtunnel==0.4.0
paramiko==3.4.0
python-multipart==0.0.6
EOF

pip install -r $APP_DIR/requirements.txt

# 6. 设置权限
echo "[6/7] 设置权限..."
# 创建非root用户运行应用
useradd -r -s /bin/false article-publisher 2>/dev/null || true
chown -R article-publisher:article-publisher $APP_DIR

# 7. 创建systemd服务
echo "[7/7] 创建systemd服务..."
cat > /etc/systemd/system/article-publisher.service << 'EOF'
[Unit]
Description=Article Publisher API Service
After=network.target

[Service]
Type=notify
User=article-publisher
Group=article-publisher
WorkingDirectory=/opt/article-publisher
Environment="PATH=/opt/article-publisher/venv/bin"
Environment="PYTHONUNBUFFERED=1"
ExecStart=/opt/article-publisher/venv/bin/uvicorn backend.api:app \\
    --host 0.0.0.0 \\
    --port 8001 \\
    --workers 1 \\
    --loop uvloop \\
    --log-config /opt/article-publisher/deploy/logging.ini \\
    --limit-concurrency 20 \\
    --backlog 10
ExecReload=/bin/kill -HUP $MAINPID
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

# 资源限制 (2GB RAM)
MemoryMax=1G
MemoryHigh=800M
CPUQuota=150%

# 安全加固
NoNewPrivileges=true
PrivateTmp=true
ProtectSystem=strict
ProtectHome=true
ReadWritePaths=/opt/article-publisher

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

echo ""
echo "======================================"
echo "部署准备完成！"
echo "======================================"
echo ""
echo "后续步骤："
echo "1. 将项目文件复制到: $APP_DIR"
echo "2. 配置 config.yaml"
echo "3. 配置 Nginx (使用 deploy/nginx.conf)"
echo "4. 启动服务: systemctl start article-publisher"
echo ""
