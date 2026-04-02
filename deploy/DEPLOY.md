# Article Publisher 部署指南

## 服务器要求

- 系统: Ubuntu 20.04+ / Debian 11+
- 配置: 2核 2GB RAM
- 需要访问: PostgreSQL 数据库 (通过 SSH 隧道)

## 快速部署

### 1. 运行部署脚本

```bash
# 上传项目到服务器
scp -r article-publisher root@REDACTED:/tmp/

# SSH 登录服务器
ssh root@REDACTED

# 运行部署脚本
cd /tmp/article-publisher/deploy
bash setup.sh
```

### 2. 复制项目文件

```bash
# 复制到应用目录
cp -r /tmp/article-publisher/* /opt/article-publisher/
chown -R article-publisher:article-publisher /opt/article-publisher
```

### 3. 配置数据库

编辑 `/opt/article-publisher/config.yaml`:

```yaml
database:
  url: "postgresql+psycopg2://REDACTED:REDACTED@localhost:5432/info_article"
  echo: false
```

### 4. 初始化数据库表

```bash
cd /opt/article-publisher
source venv/bin/activate
python -m database.init_db
```

### 5. 配置 Nginx

```bash
# 复制配置
cp /opt/article-publisher/deploy/nginx.conf /etc/nginx/sites-available/article-publisher

# 启用站点
ln -s /etc/nginx/sites-available/article-publisher /etc/nginx/sites-enabled/

# 测试配置
nginx -t

# 重载 Nginx
systemctl reload nginx
```

### 6. 启动服务

```bash
# 启动服务
systemctl start article-publisher

# 开机自启
systemctl enable article-publisher

# 查看状态
systemctl status article-publisher

# 查看日志
journalctl -u article-publisher -f
```

## 管理命令

```bash
# 重启服务
systemctl restart article-publisher

# 重载配置
systemctl reload article-publisher

# 停止服务
systemctl stop article-publisher

# 查看日志
journalctl -u article-publisher -n 100

# 查看内存使用
curl http://localhost:8000/api/memory/info

# 清理缓存
curl -X POST http://localhost:8000/api/memory/clear
```

## 前端部署

```bash
# 在本地构建前端
cd frontend
npm install
npm run build

# 上传 dist 目录到服务器
scp -r dist/* root@REDACTED:/opt/article-publisher/frontend/dist/
```

## HTTPS 配置 (可选)

```bash
# 安装 Certbot
apt install certbot python3-certbot-nginx

# 获取证书
certbot --nginx -d your-domain.com

# 自动续期
certbot renew --dry-run
```

## 安全配置

项目已内置登录认证，部署前请修改 `config.yaml` 中的认证信息：

```yaml
auth:
  username: "你的用户名"
  password: "你的强密码"
  secret_key: "一个随机生成的密钥"
  token_expire_hours: 24
```

- 前端和 API 均受登录保护，未登录无法访问任何功能
- 登录后生成 Token，默认 24 小时过期
- 修改 `secret_key` 会使所有已登录用户的 Token 失效

## 故障排查

```bash
# 检查服务状态
systemctl status article-publisher

# 查看详细日志
journalctl -u article-publisher -n 200 --no-pager

# 检查端口
netstat -tlnp | grep 8000

# 检查 Nginx
tail -f /var/log/nginx/article-publisher-error.log

# 测试 API
curl http://localhost:8000/api/status
```

## 内存优化建议

服务器只有 2GB RAM，已做以下优化：

1. Uvicorn 使用 1 worker
2. 并发连接限制 20
3. 日志文件自动轮转 (2MB × 3)
4. 文章缓存上限 500 篇
5. systemd 内存限制 1GB

如需进一步优化：

```bash
# 增加交换空间
dd if=/dev/zero of=/swapfile bs=1M count=1024
chmod 600 /swapfile
mkswap /swapfile
swapon /swapfile
echo '/swapfile none swap sw 0 0' >> /etc/fstab
```
