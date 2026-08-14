#!/usr/bin/env bash
# ==============================================================================
# P-Files 安全文件分享柜 - Linux 服务器一键全自动安装/部署脚本
# ==============================================================================

set -e

# 颜色输出
GREEN="\033[32m"
CYAN="\033[36m"
YELLOW="\033[33m"
RED="\033[31m"
BOLD="\033[1m"
NC="\033[0m"

APP_NAME="p-files"
INSTALL_DIR="/opt/p-files"
PORT=52080

echo -e "${CYAN}${BOLD}"
echo "========================================================"
echo "    📦 欢迎使用 P-Files 安全文件分享系统一键安装程序    "
echo "========================================================"
echo -e "${NC}"

# 1. 检查 root 权限
if [ "$(id -u)" -ne 0 ]; then
    echo -e "${RED}❌ 错误: 请使用 root 或 sudo 权限运行此脚本！${NC}"
    exit 1
fi

# 2. 检测并安装 Docker
echo -e "${GREEN}🔍 步骤 1/4: 检查 Docker 环境...${NC}"
if ! command -v docker >/dev/null 2>&1; then
    echo -e "${YELLOW}⚠️ 未检测到 Docker，正在为您自动安装 Docker...${NC}"
    curl -fsSL https://get.docker.com | bash
    systemctl enable docker
    systemctl start docker
    echo -e "${GREEN}✅ Docker 安装完成并已启动服务！${NC}"
else
    echo -e "${GREEN}✅ Docker 已安装: $(docker --version)${NC}"
fi

# 3. 检测 Docker Compose
if ! docker compose version >/dev/null 2>&1; then
    echo -e "${YELLOW}⚠️ 未检测到 Docker Compose 插件，正在配置...${NC}"
    if command -v apt-get >/dev/null 2>&1; then
        apt-get update && apt-get install -y docker-compose-plugin
    elif command -v yum >/dev/null 2>&1; then
        yum install -y docker-compose-plugin
    fi
fi

# 4. 创建安装目录与数据持久化
echo -e "${GREEN}📁 步骤 2/4: 创建安装与持久化数据目录: ${INSTALL_DIR}${NC}"
mkdir -p "${INSTALL_DIR}/data/uploads"

# 5. 生成随机密钥与 docker-compose.yml
echo -e "${GREEN}⚙️ 步骤 3/4: 生成安全配置与编排文件...${NC}"
RANDOM_SECRET=$(openssl rand -hex 16 2>/dev/null || date +%s%N | md5sum | head -c 32)

cat <<EOF > "${INSTALL_DIR}/docker-compose.yml"
services:
  pfiles:
    image: ghcr.io/\${GH_REPO:-kejee/p-files}:latest
    build:
      context: .
      dockerfile: Dockerfile
    container_name: pfiles-server
    restart: unless-stopped
    ports:
      - "${PORT}:${PORT}"
    volumes:
      - ./data:/app/data
    environment:
      - HOST=0.0.0.0
      - PORT=${PORT}
      - ADMIN_USERNAME=useradmin
      - ADMIN_PASSWORD=admin123456
      - SECRET_KEY=${RANDOM_SECRET}
      - RATE_LIMIT_MAX_ATTEMPTS=5
      - RATE_LIMIT_WINDOW_SECONDS=300
      - ENABLE_DOCS=false
EOF

# 如果当前目录存在源码，则拷贝到安装目录以支持本地构建
if [ -f "./Dockerfile" ]; then
    cp -r . "${INSTALL_DIR}/" 2>/dev/null || true
fi

# 6. 启动容器
echo -e "${GREEN}🚀 步骤 4/4: 启动 P-Files 服务容器...${NC}"
cd "${INSTALL_DIR}"
docker compose up -d --build

# 获取服务器公网 IP
SERVER_IP=$(curl -s --max-time 3 https://api.ipify.org || curl -s --max-time 3 http://ifconfig.me || echo "你的服务器公网IP")

echo -e "\n${GREEN}${BOLD}🎉 P-Files 部署成功！服务正在运行中...${NC}\n"
echo -e "--------------------------------------------------------"
echo -e "${CYAN}🌐 公共文件提取页${NC} : http://${SERVER_IP}:${PORT}"
echo -e "${CYAN}🔐 管理后台控制台${NC} : http://${SERVER_IP}:${PORT}/admin"
echo -e "--------------------------------------------------------"
echo -e "${YELLOW}👤 默认管理员账号${NC} : useradmin"
echo -e "${YELLOW}🔑 默认初始密码  ${NC} : admin123456"
echo -e "--------------------------------------------------------"
echo -e "${RED}⚠️ 重要提示: 请首次登录后立即在后台右上角修改默认密码！${NC}"
echo -e "${GREEN}📂 数据持久化目录${NC} : ${INSTALL_DIR}/data"
echo -e "--------------------------------------------------------"
echo -e "日常运维管理命令:"
echo -e "  查看日志: cd ${INSTALL_DIR} && docker compose logs -f"
echo -e "  重启服务: cd ${INSTALL_DIR} && docker compose restart"
echo -e "  停止服务: cd ${INSTALL_DIR} && docker compose down"
echo -e "--------------------------------------------------------\n"
