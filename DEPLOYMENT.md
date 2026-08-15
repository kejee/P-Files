# 🛡️ P-Files 生产环境部署与运维指南

本文档介绍如何在生产环境中安全、高效地运行 **P-Files**，涵盖**自动化镜像更新**、**反向代理与 HTTPS 配置（突破大文件上传限制）**以及**服务器安全加固（禁止 IP/端口直连）**。

> 📌 **安全提示**：本文档中所有域名（如 `files.example.com`）和凭证均为示例，部署时请替换为您自己的实际参数。

---

## 目录
- [一、 自动更新定时任务（Auto-Update）](#一-自动更新定时任务auto-update)
  - [模式 A：安全反代模式（推荐，仅监听 127.0.0.1）](#模式-a安全反代模式推荐仅监听-127001)
  - [模式 B：公网独立直连模式（监听 0.0.0.0）](#模式-b公网独立直连模式监听-0000)
  - [配置 Crontab 定时任务](#配置-crontab-定时任务)
- [二、 域名与 HTTPS 反向代理方案](#二-域名与-https-反向代理方案)
  - [方案 1：使用 `nginx-ssl` 一键自动化工具（强烈推荐）](#方案-1使用-nginx-ssl-一键自动化工具强烈推荐)
  - [方案 2：手动配置 Nginx（原生配置）](#方案-2手动配置-nginx原生配置)
  - [方案 3：使用 Caddy 极简自动反代](#方案-3使用-caddy-极简自动反代)
- [三、 进阶安全加固：禁止 IP 直接访问](#三-进阶安全加固禁止-ip-直接访问)

---

## 一、 自动更新定时任务（Auto-Update）

当 GitHub 发布新版本镜像时，通过定时任务脚本可以实现**静默无感自动拉取并重启容器**，且无需担心数据丢失（已挂载 `-v` 数据目录）。

### 模式 A：安全反代模式（推荐，仅监听 127.0.0.1）
> **适用场景**：搭配 Nginx / Caddy 反向代理使用。后端服务仅监听本地回环地址，外网无法直接通过 `http://IP:52080` 访问，提升系统安全性。

创建脚本 `/opt/p-files/auto_update.sh`：

```bash
#!/bin/bash
IMAGE="ghcr.io/kejee/p-files:latest"
CONTAINER_NAME="pfiles-server"

# 拉取最新镜像
OUTPUT=$(docker pull $IMAGE)

if echo "$OUTPUT" | grep -q "Image is up to date"; then
    exit 0
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 发现新镜像，正在平滑升级容器..."
    docker stop $CONTAINER_NAME 2>/dev/null || true
    docker rm $CONTAINER_NAME 2>/dev/null || true
    docker run -d \
      --name $CONTAINER_NAME \
      --restart unless-stopped \
      -p 127.0.0.1:52080:52080 \
      -v /opt/p-files/data:/app/data \
      -e ADMIN_USERNAME=admin \
      $IMAGE
    docker image prune -f >/dev/null 2>&1
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 升级完成！"
fi
```

---

### 模式 B：公网独立直连模式（监听 0.0.0.0）
> **适用场景**：不使用反向代理，直接通过服务器公网 IP 和端口（`http://<服务器IP>:52080`）对外提供服务。

创建脚本 `/opt/p-files/auto_update.sh`：

```bash
#!/bin/bash
IMAGE="ghcr.io/kejee/p-files:latest"
CONTAINER_NAME="pfiles-server"

# 拉取最新镜像
OUTPUT=$(docker pull $IMAGE)

if echo "$OUTPUT" | grep -q "Image is up to date"; then
    exit 0
else
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 发现新镜像，正在平滑升级容器..."
    docker stop $CONTAINER_NAME 2>/dev/null || true
    docker rm $CONTAINER_NAME 2>/dev/null || true
    docker run -d \
      --name $CONTAINER_NAME \
      --restart unless-stopped \
      -p 52080:52080 \
      -v /opt/p-files/data:/app/data \
      -e ADMIN_USERNAME=admin \
      $IMAGE
    docker image prune -f >/dev/null 2>&1
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 升级完成！"
fi
```

---

### 配置 Crontab 定时任务

赋予脚本执行权限并加入系统的定时调度任务：

```bash
# 1. 赋予执行权限
chmod +x /opt/p-files/auto_update.sh

# 2. 编辑当前用户的定时任务
crontab -e
```

在文件末尾添加以下内容（例如每 5 分钟检测一次更新）：

```cron
*/5 * * * * /opt/p-files/auto_update.sh >> /opt/p-files/auto_update.log 2>&1
```

---

## 二、 域名与 HTTPS 反向代理方案

为了突破部分 CDN（如 Cloudflare 免费版 100MB 单文件限制）以及提供合规的 HTTPS 安全连接，推荐使用以下反向代理方案直连 VPS。

---

### 方案 1：使用 `nginx-ssl` 一键自动化工具（强烈推荐）

使用开源项目 [**nginx-ssl**](https://github.com/kejee/nginx-ssl) 可以一键完成独立反代站点创建、Let's Encrypt 免费证书签发、大文件流式传输优化及自动续期 Hook 注册。

#### 1. 安装工具
```bash
curl -fsSL https://raw.githubusercontent.com/kejee/nginx-ssl/main/install.sh | bash
```

#### 2. 一键添加站点
```bash
# 语法: add-proxy <你的域名> <本地端口>
add-proxy files.example.com 52080
```

> **✨ `nginx-ssl` 核心优势**：
> - 🚀 **零配置开箱即用**：自动生成模块化独立配置，不修改主配置，绝不破坏现有业务。
> - ⚡ **大文件无限制传输**：自动配置 `client_max_body_size 0;` 并开启 `proxy_request_buffering off;`，彻底解除上传大小限制。
> - 🔒 **自适应防火墙与自动续期**：初次申请和未来每 2 个月自动续期时，均会自动处理验证通道，安全省心。

---

### 方案 2：手动配置 Nginx（原生配置）

如果你希望手动管理 Nginx 站点配置：

#### 1. 创建站点配置文件
新建 `/etc/nginx/conf.d/pfiles.conf`：

```nginx
server {
    listen 80;
    server_name files.example.com;

    # 关键参数：0 表示取消上传大小限制
    client_max_body_size 0;

    location / {
        proxy_pass http://127.0.0.1:52080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # 开启流式直传，优化超大文件上传吞吐性能
        proxy_request_buffering off;
        proxy_read_timeout 600s;
        proxy_send_timeout 600s;
    }
}
```

#### 2. 测试配置并重载
```bash
nginx -t && systemctl reload nginx
```

#### 3. 申请 SSL 证书（Certbot）
```bash
# 安装 Certbot（Debian/Ubuntu）
apt update && apt install -y certbot python3-certbot-nginx

# 为域名一键配置 HTTPS 证书
certbot --nginx -d files.example.com
```

---

### 方案 3：使用 Caddy 极简自动反代

Caddy 默认自带 Let's Encrypt 证书自动申请与续期，且**原生无上传大小限制**。

#### 使用 Docker 一键运行 Caddy：
```bash
docker run -d \
  --name caddy \
  --restart always \
  -p 80:80 \
  -p 443:443 \
  -v caddy_data:/data \
  caddy:latest \
  caddy reverse-proxy --from files.example.com --to 127.0.0.1:52080
```

---

## 三、 进阶安全加固：禁止 IP 直接访问

为防止扫描器或恶意爬虫通过服务器公网 IP 直接探测 Web 端口，可在 Nginx 中配置默认拒绝规则。

新建 `/etc/nginx/conf.d/00-default.conf`：

```nginx
# 1. 拦截所有通过 IP 直接访问 80 端口的请求
server {
    listen 80 default_server;
    listen [::]:80 default_server;
    server_name _;
    return 444; # Nginx 特有代码：立即关闭 TCP 连接，不给扫描器任何回包
}

# 2. 拦截所有通过 IP 直接访问 443 端口的请求
server {
    listen 443 default_server ssl;
    listen [::]:443 default_server ssl;
    server_name _;
    ssl_reject_handshake on; # 直接拒绝 TLS 握手
}
```

> **注意**：如果使用的是 Ubuntu 系统，请先清理系统自带的演示文件：
> ```bash
> rm -f /etc/nginx/sites-enabled/default
> nginx -t && systemctl reload nginx
> ```
