FROM python:3.11-slim

WORKDIR /app

# 优化 Python 输出与缓存
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    DATA_DIR=/app/data \
    UPLOAD_DIR=/app/data/uploads

# 安装系统基础工具
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 复制源码
COPY . .

# 确保数据持久化目录存在
RUN mkdir -p /app/data /app/data/uploads

# 暴露服务端口
EXPOSE 8080

# 启动 Uvicorn 高性能异步服务
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
