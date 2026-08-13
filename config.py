# -*- coding: utf-8 -*-
import os
from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    APP_NAME: str = "P-Files 安全文件分享柜"
    VERSION: str = "1.0.0"
    
    # 基础路径配置
    BASE_DIR: str = os.path.dirname(os.path.abspath(__file__))
    DATA_DIR: str = os.getenv("DATA_DIR", os.path.join(BASE_DIR, "data"))
    UPLOAD_DIR: str = os.getenv("UPLOAD_DIR", os.path.join(DATA_DIR, "uploads"))
    
    # 数据库路径
    DATABASE_URL: str = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{os.path.join(DATA_DIR, 'pfiles.db')}")
    
    # 安全配置
    ADMIN_USERNAME: str = os.getenv("ADMIN_USERNAME", "admin")
    ADMIN_PASSWORD: str = os.getenv("ADMIN_PASSWORD", "admin123456") # 首次运行默认密码，可在后台修改
    SECRET_KEY: str = os.getenv("SECRET_KEY", "pfiles-super-secret-key-change-it-in-production-2026")
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7 # 7天免登
    
    # 防爆破限流配置 (每个 IP 窗口时间内最大失败次数)
    RATE_LIMIT_MAX_ATTEMPTS: int = int(os.getenv("RATE_LIMIT_MAX_ATTEMPTS", "5"))
    RATE_LIMIT_WINDOW_SECONDS: int = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "300")) # 5分钟
    
    # 端口与服务
    HOST: str = os.getenv("HOST", "0.0.0.0")
    PORT: int = int(os.getenv("PORT", "8080"))

    class Config:
        env_file = ".env"
        extra = "allow"

settings = Settings()

# 自动确保数据与上传目录存在
os.makedirs(settings.DATA_DIR, exist_ok=True)
os.makedirs(settings.UPLOAD_DIR, exist_ok=True)
