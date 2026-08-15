# -*- coding: utf-8 -*-
import asyncio
import datetime
import logging
import os
from sqlalchemy import select, update
from config import settings
from database import AsyncSessionLocal, FileItem

logger = logging.getLogger("app.cleanup")

async def cleanup_expired_files_loop():
    """
    后台守护任务：定期扫描并清理过期文件
    """
    logger.info("后台过期文件清理守护协程已启动...")
    while True:
        try:
            await asyncio.sleep(60) # 每 60 秒轮询一次
            now = datetime.datetime.utcnow()
            
            async with AsyncSessionLocal() as session:
                # 查找已过期的活跃文件
                stmt = select(FileItem).where(
                    FileItem.status == "active",
                    FileItem.expire_at.is_not(None),
                    FileItem.expire_at <= now
                )
                result = await session.execute(stmt)
                expired_files = result.scalars().all()
                
                for f in expired_files:
                    f.status = "expired"
                    logger.info(f"文件已过期: [{f.share_code}] {f.original_filename}")
                    
                    # 若开启了彻底销毁(burn_mode == 2)或正常过期策略
                    if f.burn_mode == 2:
                        for fn in [f.stored_filename, f.raw_stored_filename]:
                            if fn:
                                file_path = os.path.join(settings.UPLOAD_DIR, fn)
                                if os.path.exists(file_path):
                                    try:
                                        os.remove(file_path)
                                        logger.info(f"彻底销毁物理文件: {file_path}")
                                    except Exception as err:
                                        logger.error(f"删除物理文件失败: {err}")
                                
                if expired_files:
                    await session.commit()
                    
        except asyncio.CancelledError:
            logger.info("后台清理协程已停止")
            break
        except Exception as e:
            logger.error(f"清理过期文件任务异常: {e}")
