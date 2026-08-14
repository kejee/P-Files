# 📦 P-Files 安全文件分享系统 (文件快递柜)

一个轻量、高效、安全且支持全方位访问控制的独立文件分享系统。支持 **Docker 一键部署**、**提取码取件**、**IP 离线高精度地区解析**、**多维度策略自由组合（单选/多选/全选）** 以及 **工业级防爆破与阅后即焚**。

---

## ✨ 核心特性

- 🚀 **Docker 极速部署**：一键 `docker compose up -d`，所有数据与配置自动持久化挂载至本地 `./data`。
- 🎯 **自由组合的安全分享策略**（支持单选、多选、全选）：
  1. 🔑 **访问口令保护**：设置查看/下载密码，防止分享外泄。
  2. ⏳ **有效时长限制**：支持永久、10分钟、1小时、1天、7天、30天或自定义小时。
  3. 🔥 **双模式阅后即焚**：
     - **模式 1 (仅失效链接)**：下载完成后分享链接失效，但源文件保留在后台文件库中，管理员可随时一键“重新开启分享”，无需重复上传大文件。
     - **模式 2 (彻底物理销毁)**：下载完成后立即从服务器底层磁盘彻底 `unlink` 物理删除，不留痕迹。
  4. 🎯 **最大下载次数限制**：达到设定下载次数后自动关闭分享。
  5. 🛡️ **IP 白名单访问控制**：支持单 IP、多 IP、CIDR 网段（如 `192.168.1.0/24`），非白名单 IP 即使知道密码也直接拦截。
- 📊 **访客 IP 与离线地区审计**：
  - 内置 `ip2region` 离线高精度 IP 数据库，毫秒级解析访客省份、城市、运营商（如：`中国·广东省·深圳市 (电信)`）。
  - 绝不调用第三方外部 API，彻底保护访客与服务器隐私。
  - 自动记录查看、下载、口令输错、非法 IP 拦截等全方位审计日志。
- 🔒 **严密的安全防护体系**：
  - **防爆破限流 (Rate Limiting)**：连续输错密码或口令自动临时锁定 IP。
  - **路径穿越防御**：文件以 UUID 物理隔离存储，杜绝文件名注入和目录穿越。
  - **后台过期守护**：异步守护协程自动轮询清理过期记录与物理残留。
- 💎 **轻奢现代 UI 体验**：深色玻璃拟态、微动效卡片、拖拽上传、一键复制分享链接、自适应手机与 PC 访问。

---

## 🚀 快速开始

### 方式 1：Linux 服务器一键全自动安装（极力推荐，无 Docker 自动装）

登录服务器后直接执行以下命令（自动检测/安装 Docker，自动生成安全密钥并后台启动）：

```bash
curl -fsSL https://raw.githubusercontent.com/<你的用户名>/<仓库名>/main/install.sh | bash
```
> 或下载项目后直接运行：`sudo bash install.sh`

---

### 方式 2：使用 Docker Compose 手动部署

1. 克隆或下载本项目至服务器：
```bash
git clone <your-repo-url> P-Files
cd P-Files
```

2. 启动服务：
```bash
docker compose up -d --build
```

3. 访问系统：
- **公共文件提取页**：`http://服务器IP:52080`
- **管理控制台**：`http://服务器IP:52080/admin`
  - 默认用户名：`useradmin`
  - 默认初始密码：`admin123456`（登录后可在后台右上角直接修改）

---

### 方式 2：纯 Docker 命令行运行

```bash
# 1. 构建镜像
docker build -t pfiles:latest .

# 2. 运行容器并挂载本地数据目录
docker run -d \
  --name pfiles-server \
  --restart unless-stopped \
  -p 52080:52080 \
  -v $(pwd)/data:/app/data \
  -e ADMIN_USERNAME=useradmin \
  -e ADMIN_PASSWORD=admin123456 \
  pfiles:latest
```

---

## 🛡️ IP/HTTP 环境下的安全保障与进阶配置

如果你直接使用 `http://IP:8080` 访问，系统内置了多层防护：
1. **提取码与口令防爆破**：任何 IP 5分钟内连续错误尝试 5 次即触发限流封禁。
2. **IP 白名单控制**：可在后台针对机密文件指定特定 IP/网段访问。
3. **彻底物理销毁**：阅后即焚触发时，操作系统层面立即安全擦除文件。

### 进阶：配置 HTTPS 域名访问（彻底告别明文）

#### 推荐方案：使用 Nginx 反向代理并配置 SSL
在你的服务器 Nginx 配置文件中加入：

```nginx
server {
    listen 80;
    server_name share.yourdomain.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name share.yourdomain.com;

    ssl_certificate /path/to/fullchain.pem;
    ssl_certificate_key /path/to/privkey.pem;

    client_max_body_size 1024M; # 允许上传大文件

    location / {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

---

## 📁 目录结构说明

```text
├── config.py           # 系统配置中心（环境变量、存储路径、安全阈值）
├── database.py         # SQLite 异步模型（文件记录表、IP审计日志表、系统设置）
├── security.py         # JWT 鉴权、密码哈希、防爆破限流器、IP 白名单引擎
├── ip_locator.py       # 离线 IP 归属地查询服务
├── xdb_searcher.py     # ip2region xdb 纯 Python 毫秒级离线查询引擎
├── cleanup.py          # 后台异步自动轮询清理过期文件
├── main.py             # FastAPI 核心业务逻辑与 API 路由
├── templates/          # 前端 HTML 模板 (index.html, login.html, admin.html)
├── static/             # 静态 CSS 样式与轻奢 UI 设计系统
├── data/               # 持久化数据目录 (pfiles.db 数据库、uploads 存储、ip2region.xdb)
├── Dockerfile          # 容器构建文件
└── docker-compose.yml  # Docker Compose 编排文件
```
