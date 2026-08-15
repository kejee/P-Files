# -*- coding: utf-8 -*-
import datetime
from sqlalchemy import Column, Integer, String, Boolean, DateTime, Text, ForeignKey, select, event, Engine
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import declarative_base, relationship
from config import settings

Base = declarative_base()

class FileItem(Base):
    __tablename__ = "files"

    id = Column(Integer, primary_key=True, index=True)
    share_code = Column(String(32), unique=True, index=True, nullable=False) # 提取码
    original_filename = Column(String(255), nullable=False) # 原始文件名
    stored_filename = Column(String(255), nullable=False)   # 磁盘存储文件名
    file_size = Column(Integer, default=0)                  # 字节大小
    content_type = Column(String(128), default="application/octet-stream")
    
    # 策略与限制 (支持单选/多选/全选任意组合)
    has_password = Column(Boolean, default=False)           # 是否启用了口令
    password_hash = Column(String(255), nullable=True)      # 口令哈希
    
    expire_at = Column(DateTime, nullable=True)             # 过期时间 (None 表示不限时)
    
    # 阅后即焚模式: 0 = 关闭; 1 = 仅失效分享(保留源文件); 2 = 彻底销毁(删除物理文件)
    burn_mode = Column(Integer, default=0)
    # 即焚触发条件: 'download' (下载后即焚), 'view' (预览后即焚), 'any' (预览或下载任意交互后即焚)
    burn_trigger = Column(String(32), default="download")
    
    # 权限通道独立控制: 默认私有状态下全为 False
    allow_preview = Column(Boolean, default=False)          # 是否允许在线预览
    allow_download = Column(Boolean, default=False)         # 是否允许下载源文件
    
    max_downloads = Column(Integer, default=0)              # 最大下载次数限制 (0 表示不限)
    allowed_ips = Column(Text, nullable=True)               # IP白名单 (支持单个/多个/CIDR)
    remark = Column(String(255), nullable=True)             # 备注说明

    # 统计数据
    view_count = Column(Integer, default=0)                 # 查看次数
    download_count = Column(Integer, default=0)             # 下载次数
    
    # 状态: 'stored' (私有云盘), 'active' (分享中), 'expired' (已过期), 'burned' (已即焚), 'revoked' (已取消分享), 'deleted' (彻底删除)
    status = Column(String(32), default="stored", index=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    # 关联日志
    logs = relationship("AccessLog", back_populates="file", cascade="all, delete-orphan", order_by="desc(AccessLog.created_at)")

class AccessLog(Base):
    __tablename__ = "access_logs"

    id = Column(Integer, primary_key=True, index=True)
    file_id = Column(Integer, ForeignKey("files.id", ondelete="CASCADE"), nullable=True)
    share_code = Column(String(32), index=True)
    action = Column(String(32), nullable=False) # view | download | blocked_ip | wrong_pwd | expired_attempt
    ip = Column(String(64), nullable=False)
    location = Column(String(128), default="未知地区")
    user_agent = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=datetime.datetime.utcnow)

    file = relationship("FileItem", back_populates="logs")

class AdminSetting(Base):
    __tablename__ = "settings"

    key = Column(String(64), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime, default=datetime.datetime.utcnow, onupdate=datetime.datetime.utcnow)

# 异步引擎配置
engine = create_async_engine(
    settings.DATABASE_URL,
    echo=False,
    connect_args={"check_same_thread": False} if "sqlite" in settings.DATABASE_URL else {}
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False
)

async def get_db():
    async with AsyncSessionLocal() as session:
        try:
            yield session
        finally:
            await session.close()

async def init_db():
    async with engine.begin() as conn:
        # 开启 SQLite WAL 模式以提升并发性能
        if "sqlite" in settings.DATABASE_URL:
            await conn.exec_driver_sql("PRAGMA journal_mode=WAL;")
        await conn.run_sync(Base.metadata.create_all)
        
        # 针对已有的 SQLite 数据库执行自适应平滑迁移
        if "sqlite" in settings.DATABASE_URL:
            try:
                # 获取 files 表现有所有字段名
                res = await conn.exec_driver_sql("PRAGMA table_info(files);")
                columns = [row[1] for row in res.fetchall()]
                
                if "allow_preview" not in columns:
                    await conn.exec_driver_sql("ALTER TABLE files ADD COLUMN allow_preview BOOLEAN DEFAULT 0;")
                if "allow_download" not in columns:
                    await conn.exec_driver_sql("ALTER TABLE files ADD COLUMN allow_download BOOLEAN DEFAULT 0;")
                if "burn_trigger" not in columns:
                    await conn.exec_driver_sql("ALTER TABLE files ADD COLUMN burn_trigger VARCHAR(32) DEFAULT 'download';")
            except Exception:
                pass
