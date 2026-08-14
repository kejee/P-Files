# -*- coding: utf-8 -*-
import asyncio
import datetime
import math
import os
import random
import string
import uuid
from typing import Optional, List

from fastapi import (
    FastAPI, File, UploadFile, Form, Request, HTTPException,
    Depends, status, Response, BackgroundTasks
)
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select, desc, func, delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from database import init_db, get_db, FileItem, AccessLog, AdminSetting, AsyncSessionLocal
from ip_locator import IPLocator
from security import (
    verify_password, get_password_hash, create_access_token, decode_access_token,
    get_client_ip, is_ip_allowed, auth_limiter, share_pwd_limiter, security_bearer
)
from cleanup import cleanup_expired_files_loop

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. 初始化数据库表结构
    await init_db()
    
    # 2. 初始化 IP 离线解析器
    IPLocator.init()
    
    # 3. 检查并初始化默认管理员密码
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(AdminSetting).where(AdminSetting.key == "admin_password"))
        setting = res.scalar_one_or_none()
        if not setting:
            hashed = get_password_hash(settings.ADMIN_PASSWORD)
            db.add(AdminSetting(key="admin_password", value=hashed))
            await db.commit()

    # 4. 启动后台异步清理协程
    cleanup_task = asyncio.create_task(cleanup_expired_files_loop())
    yield
    cleanup_task.cancel()

app = FastAPI(
    title=settings.APP_NAME,
    version=settings.VERSION,
    docs_url="/api/docs" if settings.ENABLE_DOCS else None,
    redoc_url=None,
    openapi_url="/api/openapi.json" if settings.ENABLE_DOCS else None,
    lifespan=lifespan
)

# 全局安全响应头中间件
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "SAMEORIGIN"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response

# 跨域与基础安全头
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 挂载静态文件与模板
static_dir = os.path.join(settings.BASE_DIR, "static")
templates_dir = os.path.join(settings.BASE_DIR, "templates")
os.makedirs(static_dir, exist_ok=True)
os.makedirs(templates_dir, exist_ok=True)

app.mount("/static", StaticFiles(directory=static_dir), name="static")
templates = Jinja2Templates(directory=templates_dir)

# 工具函数：生成高可读性随机提取码 (去除了易混淆的 0, O, 1, I, l)
def generate_share_code(length: int = 5) -> str:
    alphabet = "23456789abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ"
    return "".join(random.choices(alphabet, k=length))

def format_file_size(size_in_bytes: int) -> str:
    if size_in_bytes < 1024:
        return f"{size_in_bytes} B"
    elif size_in_bytes < 1024 * 1024:
        return f"{size_in_bytes / 1024:.1f} KB"
    elif size_in_bytes < 1024 * 1024 * 1024:
        return f"{size_in_bytes / (1024 * 1024):.2f} MB"
    else:
        return f"{size_in_bytes / (1024 * 1024 * 1024):.2f} GB"

# 鉴权依赖项：管理员验证
async def get_current_admin(
    request: Request,
    db: AsyncSession = Depends(get_db)
):
    token = None
    # 优先从 Authorization 头获取
    auth_header = request.headers.get("Authorization")
    if auth_header and auth_header.startswith("Bearer "):
        token = auth_header.split(" ")[1]
    # 其次从 Cookie 获取
    if not token:
        token = request.cookies.get("admin_token")

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="请先登录管理后台",
            headers={"WWW-Authenticate": "Bearer"},
        )

    payload = decode_access_token(token)
    if not payload or payload.get("sub") != settings.ADMIN_USERNAME:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="登录已失效，请重新登录",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return payload.get("sub")

# ----------------- 页面渲染路由 -----------------

@app.get("/", response_class=HTMLResponse)
async def page_index(request: Request):
    """访客公共提取页"""
    return templates.TemplateResponse(request=request, name="index.html", context={"code": ""})

@app.get("/s/{code}", response_class=HTMLResponse)
async def page_share_direct(request: Request, code: str):
    """直接通过链接访问提取页"""
    return templates.TemplateResponse(request=request, name="index.html", context={"code": code})

@app.get("/login", response_class=HTMLResponse)
async def page_login(request: Request):
    """管理员登录页"""
    return templates.TemplateResponse(request=request, name="login.html")

@app.get("/favicon.ico", include_in_schema=False)
@app.get("/apple-touch-icon.png", include_in_schema=False)
@app.get("/apple-touch-icon-precomposed.png", include_in_schema=False)
async def favicon():
    svg_icon = '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100"><text y=".9em" font-size="90">📦</text></svg>'
    return Response(content=svg_icon, media_type="image/svg+xml")

@app.get("/admin", response_class=HTMLResponse)
async def page_admin(request: Request):
    """管理员控制台页面 (服务端鉴权拦截，杜绝未登录闪现)"""
    token = request.cookies.get("admin_token")
    if not token:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    payload = decode_access_token(token)
    if not payload or payload.get("sub") != settings.ADMIN_USERNAME:
        return RedirectResponse(url="/login", status_code=status.HTTP_302_FOUND)

    return templates.TemplateResponse(request=request, name="admin.html")

# ----------------- 管理员 API -----------------

@app.post("/api/admin/login")
async def admin_login(
    request: Request,
    response: Response,
    username: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    ip = get_client_ip(request)
    
    # 检查防爆破锁定
    is_locked, remaining = auth_limiter.is_locked(ip)
    if is_locked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"密码错误次数过多，IP已被临时锁定，请 {remaining} 秒后再试"
        )

    # 验证用户名
    if username != settings.ADMIN_USERNAME:
        auth_limiter.record_failure(ip)
        raise HTTPException(status_code=400, detail="用户名或密码错误")

    # 查询数据库密码
    res = await db.execute(select(AdminSetting).where(AdminSetting.key == "admin_password"))
    setting = res.scalar_one_or_none()
    current_hash = setting.value if setting else get_password_hash(settings.ADMIN_PASSWORD)

    if not verify_password(password, current_hash):
        auth_limiter.record_failure(ip)
        raise HTTPException(status_code=400, detail="用户名或密码错误")

    # 登录成功，重置限流
    auth_limiter.record_success(ip)
    token = create_access_token(data={"sub": username})
    
    # 写入 Cookie
    response.set_cookie(
        key="admin_token",
        value=token,
        httponly=True,
        max_age=settings.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        samesite="lax"
    )
    return {"code": 200, "message": "登录成功", "token": token}

@app.post("/api/admin/logout")
async def admin_logout(response: Response):
    response.delete_cookie(key="admin_token")
    return {"code": 200, "message": "已退出登录"}

@app.post("/api/admin/change-password")
async def admin_change_password(
    old_password: str = Form(...),
    new_password: str = Form(...),
    admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    if len(new_password) < 6:
        raise HTTPException(status_code=400, detail="新密码长度不能少于 6 位")

    res = await db.execute(select(AdminSetting).where(AdminSetting.key == "admin_password"))
    setting = res.scalar_one_or_none()
    current_hash = setting.value if setting else get_password_hash(settings.ADMIN_PASSWORD)

    if not verify_password(old_password, current_hash):
        raise HTTPException(status_code=400, detail="原密码不正确")

    new_hash = get_password_hash(new_password)
    if setting:
        setting.value = new_hash
    else:
        db.add(AdminSetting(key="admin_password", value=new_hash))
    
    await db.commit()
    return {"code": 200, "message": "密码修改成功"}

@app.post("/api/admin/upload")
async def admin_upload_file(
    file: UploadFile = File(...),
    custom_code: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
    expire_hours: Optional[float] = Form(0), # 0 为永久有效
    burn_mode: int = Form(0), # 0=关闭, 1=仅失效分享, 2=彻底物理删除
    max_downloads: int = Form(0), # 0=不限
    allowed_ips: Optional[str] = Form(None),
    remark: Optional[str] = Form(None),
    admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    管理后台上传并配置分享策略
    支持多条件独立自由组合（口令、有效时长、两种即焚模式、最大下载次数、IP白名单）
    """
    # 提取码处理
    share_code = custom_code.strip() if (custom_code and custom_code.strip()) else generate_share_code(6)
    
    # 检查提取码是否重复
    existing = await db.execute(select(FileItem).where(FileItem.share_code == share_code))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=400, detail=f"提取码 [{share_code}] 已存在，请换一个或留空自动生成")

    # 安全处理原始文件名与扩展名（杜绝任何路径穿越特殊字符）
    clean_orig_name = os.path.basename(file.filename or "unknown_file")
    raw_ext = os.path.splitext(clean_orig_name)[1]
    safe_ext = "".join(c for c in raw_ext if c.isalnum() or c == ".")[:16] # 严格过滤扩展名
    stored_filename = f"{uuid.uuid4().hex}{safe_ext}"
    
    target_path = os.path.abspath(os.path.join(settings.UPLOAD_DIR, stored_filename))
    if not target_path.startswith(os.path.abspath(settings.UPLOAD_DIR)):
        raise HTTPException(status_code=400, detail="非法文件存储路径")

    # 流式写入并实时限制最大文件体积 (防 DoS 磁盘占满攻击)
    max_bytes = settings.MAX_UPLOAD_SIZE_MB * 1024 * 1024
    file_size = 0
    with open(target_path, "wb") as buffer:
        while chunk := await file.read(1024 * 1024): # 1MB 逐块写入
            file_size += len(chunk)
            if file_size > max_bytes:
                buffer.close()
                if os.path.exists(target_path):
                    os.remove(target_path)
                raise HTTPException(
                    status_code=413,
                    detail=f"文件体积过大，单文件上传限制为 {settings.MAX_UPLOAD_SIZE_MB} MB"
                )
            buffer.write(chunk)

    # 计算过期时间
    expire_at = None
    if expire_hours and expire_hours > 0:
        expire_at = datetime.datetime.utcnow() + datetime.timedelta(hours=expire_hours)

    # 口令处理
    has_pwd = bool(password and password.strip())
    pwd_hash = get_password_hash(password.strip()) if has_pwd else None

    # 创建数据库记录
    file_item = FileItem(
        share_code=share_code,
        original_filename=file.filename,
        stored_filename=stored_filename,
        file_size=file_size,
        content_type=file.content_type or "application/octet-stream",
        has_password=has_pwd,
        password_hash=pwd_hash,
        expire_at=expire_at,
        burn_mode=burn_mode,
        max_downloads=max_downloads,
        allowed_ips=allowed_ips.strip() if allowed_ips else None,
        remark=remark.strip() if remark else None,
        status="active"
    )

    db.add(file_item)
    await db.commit()
    await db.refresh(file_item)

    return {
        "code": 200,
        "message": "文件上传并创建分享成功",
        "data": {
            "id": file_item.id,
            "share_code": file_item.share_code,
            "filename": file_item.original_filename,
            "size_str": format_file_size(file_item.file_size),
            "expire_at": file_item.expire_at.strftime("%Y-%m-%d %H:%M:%S") if file_item.expire_at else "永久有效",
            "burn_mode": file_item.burn_mode,
            "has_password": file_item.has_password,
            "allowed_ips": file_item.allowed_ips
        }
    }

@app.get("/api/admin/files")
async def admin_get_files(
    search: Optional[str] = None,
    status_filter: Optional[str] = None,
    admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """获取所有文件列表及统计数据"""
    stmt = select(FileItem).order_by(desc(FileItem.created_at))
    
    if search:
        stmt = stmt.where(
            (FileItem.original_filename.contains(search)) |
            (FileItem.share_code.contains(search)) |
            (FileItem.remark.contains(search))
        )
    if status_filter and status_filter != "all":
        stmt = stmt.where(FileItem.status == status_filter)

    result = await db.execute(stmt)
    files = result.scalars().all()

    now = datetime.datetime.utcnow()
    file_list = []
    for f in files:
        # 判断即时是否过期
        is_expired = f.expire_at and f.expire_at <= now
        display_status = f.status
        if f.status == "active" and is_expired:
            display_status = "expired"

        file_list.append({
            "id": f.id,
            "share_code": f.share_code,
            "original_filename": f.original_filename,
            "file_size": f.file_size,
            "file_size_formatted": format_file_size(f.file_size),
            "has_password": f.has_password,
            "expire_at": f.expire_at.strftime("%Y-%m-%d %H:%M:%S") if f.expire_at else "永久有效",
            "is_expired": is_expired,
            "burn_mode": f.burn_mode,
            "max_downloads": f.max_downloads,
            "view_count": f.view_count,
            "download_count": f.download_count,
            "allowed_ips": f.allowed_ips,
            "remark": f.remark,
            "status": display_status,
            "created_at": f.created_at.strftime("%Y-%m-%d %H:%M:%S")
        })

    return {"code": 200, "data": file_list}

@app.get("/api/admin/files/{file_id}/logs")
async def admin_get_file_logs(
    file_id: int,
    admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """获取单个文件的详细访问与下载者 IP / 地区日志"""
    stmt = select(AccessLog).where(AccessLog.file_id == file_id).order_by(desc(AccessLog.created_at)).limit(100)
    result = await db.execute(stmt)
    logs = result.scalars().all()

    log_list = []
    for log in logs:
        log_list.append({
            "id": log.id,
            "action": log.action,
            "ip": log.ip,
            "location": log.location,
            "user_agent": log.user_agent,
            "created_at": log.created_at.strftime("%Y-%m-%d %H:%M:%S")
        })

    return {"code": 200, "data": log_list}

@app.get("/api/admin/logs/all")
async def admin_get_all_logs(
    limit: int = 100,
    action: Optional[str] = None,
    admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """获取全局审计日志"""
    stmt = select(AccessLog).order_by(desc(AccessLog.created_at))
    if action:
        stmt = stmt.where(AccessLog.action == action)
    stmt = stmt.limit(limit)

    result = await db.execute(stmt)
    logs = result.scalars().all()

    return {
        "code": 200,
        "data": [
            {
                "id": l.id,
                "file_id": l.file_id,
                "share_code": l.share_code,
                "action": l.action,
                "ip": l.ip,
                "location": l.location,
                "user_agent": l.user_agent,
                "created_at": l.created_at.strftime("%Y-%m-%d %H:%M:%S")
            }
            for l in logs
        ]
    }

@app.post("/api/admin/files/{file_id}/re-share")
async def admin_reshare_file(
    file_id: int,
    custom_code: Optional[str] = Form(None),
    password: Optional[str] = Form(None),
    expire_hours: Optional[float] = Form(0),
    burn_mode: int = Form(0),
    max_downloads: int = Form(0),
    allowed_ips: Optional[str] = Form(None),
    admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """
    针对已失效或未物理删除的文件，重新激活/生成新的分享策略
    （无需重新上传源文件）
    """
    res = await db.execute(select(FileItem).where(FileItem.id == file_id))
    f = res.scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="文件记录不存在")

    target_path = os.path.join(settings.UPLOAD_DIR, f.stored_filename)
    if not os.path.exists(target_path):
        raise HTTPException(status_code=400, detail="该文件物理已被彻底销毁，无法重新分享")

    # 更新分享策略
    if custom_code and custom_code.strip():
        f.share_code = custom_code.strip()
    else:
        f.share_code = generate_share_code(6)

    has_pwd = bool(password and password.strip())
    f.has_password = has_pwd
    f.password_hash = get_password_hash(password.strip()) if has_pwd else None

    if expire_hours and expire_hours > 0:
        f.expire_at = datetime.datetime.utcnow() + datetime.timedelta(hours=expire_hours)
    else:
        f.expire_at = None

    f.burn_mode = burn_mode
    f.max_downloads = max_downloads
    f.allowed_ips = allowed_ips.strip() if allowed_ips else None
    f.status = "active"

    await db.commit()
    return {"code": 200, "message": "重新开启分享成功", "share_code": f.share_code}

@app.delete("/api/admin/files/{file_id}")
async def admin_delete_file(
    file_id: int,
    force_destroy_file: bool = True,
    admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """管理员手动删除文件"""
    res = await db.execute(select(FileItem).where(FileItem.id == file_id))
    f = res.scalar_one_or_none()
    if not f:
        raise HTTPException(status_code=404, detail="文件不存在")

    if force_destroy_file:
        file_path = os.path.join(settings.UPLOAD_DIR, f.stored_filename)
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                pass
        await db.delete(f)
    else:
        f.status = "revoked"

    await db.commit()
    return {"code": 200, "message": "文件已成功删除"}

@app.get("/api/admin/stats")
async def admin_get_stats(
    admin: str = Depends(get_current_admin),
    db: AsyncSession = Depends(get_db)
):
    """统计概览看板"""
    total_files = await db.scalar(select(func.count(FileItem.id)))
    active_files = await db.scalar(select(func.count(FileItem.id)).where(FileItem.status == "active"))
    total_views = await db.scalar(select(func.sum(FileItem.view_count))) or 0
    total_downloads = await db.scalar(select(func.sum(FileItem.download_count))) or 0
    blocked_count = await db.scalar(select(func.count(AccessLog.id)).where(AccessLog.action == "blocked_ip")) or 0

    return {
        "code": 200,
        "data": {
            "total_files": total_files,
            "active_files": active_files,
            "total_views": total_views,
            "total_downloads": total_downloads,
            "blocked_count": blocked_count
        }
    }

# ----------------- 访客端公共 API -----------------

@app.post("/api/share/query")
async def share_query_file_info(
    request: Request,
    code: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    """
    访客根据提取码查询文件信息
    自动执行 IP 归属地解析、IP 白名单拦截判断与查看日志记录
    """
    clean_code = code.strip()
    client_ip = get_client_ip(request)
    location = IPLocator.get_location(client_ip)
    user_agent = request.headers.get("User-Agent", "")[:250]

    stmt = select(FileItem).where(FileItem.share_code == clean_code)
    res = await db.execute(stmt)
    f = res.scalar_one_or_none()

    if not f:
        # 记录无效尝试
        db.add(AccessLog(
            share_code=clean_code,
            action="invalid_code",
            ip=client_ip,
            location=location,
            user_agent=user_agent
        ))
        await db.commit()
        raise HTTPException(status_code=404, detail="提取码无效或文件不存在")

    # 1. 检查 IP 白名单
    if not is_ip_allowed(client_ip, f.allowed_ips):
        db.add(AccessLog(
            file_id=f.id,
            share_code=clean_code,
            action="blocked_ip",
            ip=client_ip,
            location=location,
            user_agent=user_agent
        ))
        await db.commit()
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f"访问被拒绝：当前 IP ({client_ip} - {location}) 不在允许的白名单范围内"
        )

    # 2. 检查状态与过期时间
    now = datetime.datetime.utcnow()
    if f.status != "active" or (f.expire_at and f.expire_at <= now):
        db.add(AccessLog(
            file_id=f.id,
            share_code=clean_code,
            action="expired_attempt",
            ip=client_ip,
            location=location,
            user_agent=user_agent
        ))
        await db.commit()
        raise HTTPException(status_code=410, detail="该分享已过期或已失效")

    # 3. 检查最大下载次数限制
    if f.max_downloads > 0 and f.download_count >= f.max_downloads:
        raise HTTPException(status_code=410, detail="该分享已达到最大允许下载次数")

    # 4. 记录查看（view）日志与计数递增
    f.view_count += 1
    db.add(AccessLog(
        file_id=f.id,
        share_code=clean_code,
        action="view",
        ip=client_ip,
        location=location,
        user_agent=user_agent
    ))
    await db.commit()

    return {
        "code": 200,
        "data": {
            "share_code": f.share_code,
            "filename": f.original_filename,
            "file_size_formatted": format_file_size(f.file_size),
            "requires_password": f.has_password,
            "burn_mode": f.burn_mode,
            "expire_at": f.expire_at.strftime("%Y-%m-%d %H:%M:%S") if f.expire_at else "永久有效",
            "view_count": f.view_count,
            "download_count": f.download_count,
            "max_downloads": f.max_downloads,
            "remark": f.remark,
            "client_ip": client_ip,
            "client_location": location
        }
    }

@app.post("/api/share/verify")
async def share_verify_password(
    request: Request,
    code: str = Form(...),
    password: str = Form(...),
    db: AsyncSession = Depends(get_db)
):
    """口令校验与防爆破检测"""
    clean_code = code.strip()
    client_ip = get_client_ip(request)
    location = IPLocator.get_location(client_ip)
    user_agent = request.headers.get("User-Agent", "")[:250]

    # 防爆破限流检查
    is_locked, remaining = share_pwd_limiter.is_locked(client_ip)
    if is_locked:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=f"口令错误次数过多，IP已被锁定，请 {remaining} 秒后再试"
        )

    stmt = select(FileItem).where(FileItem.share_code == clean_code)
    res = await db.execute(stmt)
    f = res.scalar_one_or_none()

    if not f or f.status != "active":
        raise HTTPException(status_code=404, detail="文件不存在或已失效")

    # 验证口令
    if f.has_password and not verify_password(password, f.password_hash):
        share_pwd_limiter.record_failure(client_ip)
        db.add(AccessLog(
            file_id=f.id,
            share_code=clean_code,
            action="wrong_pwd",
            ip=client_ip,
            location=location,
            user_agent=user_agent
        ))
        await db.commit()
        raise HTTPException(status_code=400, detail="查看口令错误")

    share_pwd_limiter.record_success(client_ip)
    # 生成一次性/临时下载授权凭证
    download_token = create_access_token(
        data={"code": clean_code, "action": "download"},
        expires_delta=datetime.timedelta(minutes=15)
    )

    return {"code": 200, "message": "口令验证通过", "download_token": download_token}

@app.get("/api/share/download/{code}")
async def share_download_file(
    code: str,
    request: Request,
    background_tasks: BackgroundTasks,
    pwd: Optional[str] = None,
    token: Optional[str] = None,
    db: AsyncSession = Depends(get_db)
):
    """
    文件下载流接口
    负责鉴权、IP记录、下载计数增加、阅后即焚（物理擦除/失效）处理
    """
    clean_code = code.strip()
    client_ip = get_client_ip(request)
    location = IPLocator.get_location(client_ip)
    user_agent = request.headers.get("User-Agent", "")[:250]

    stmt = select(FileItem).where(FileItem.share_code == clean_code)
    res = await db.execute(stmt)
    f = res.scalar_one_or_none()

    if not f:
        raise HTTPException(status_code=404, detail="文件不存在")

    # 1. IP 白名单校验
    if not is_ip_allowed(client_ip, f.allowed_ips):
        db.add(AccessLog(
            file_id=f.id,
            share_code=clean_code,
            action="blocked_ip",
            ip=client_ip,
            location=location,
            user_agent=user_agent
        ))
        await db.commit()
        raise HTTPException(status_code=403, detail="当前 IP 不在允许访问白名单内")

    # 2. 状态与过期时间检查
    now = datetime.datetime.utcnow()
    if f.status != "active" or (f.expire_at and f.expire_at <= now):
        raise HTTPException(status_code=410, detail="该分享已过期或已失效")

    # 3. 口令验证
    if f.has_password:
        authorized = False
        if token:
            payload = decode_access_token(token)
            if payload and payload.get("code") == clean_code:
                authorized = True
        elif pwd:
            if verify_password(pwd, f.password_hash):
                authorized = True

        if not authorized:
            raise HTTPException(status_code=401, detail="请先提供正确的查看口令")

    # 4. 物理文件存在性与路径沙箱校验 (彻底防御路径穿越攻击)
    file_path = os.path.abspath(os.path.join(settings.UPLOAD_DIR, f.stored_filename))
    if not file_path.startswith(os.path.abspath(settings.UPLOAD_DIR)):
        raise HTTPException(status_code=403, detail="非法文件访问路径")

    if not os.path.exists(file_path):
        f.status = "deleted"
        await db.commit()
        raise HTTPException(status_code=404, detail="底层物理文件不存在或已被销毁")

    # 5. 递增下载计数并记录下载日志
    f.download_count += 1
    db.add(AccessLog(
        file_id=f.id,
        share_code=clean_code,
        action="download",
        ip=client_ip,
        location=location,
        user_agent=user_agent
    ))

    # 6. 处理“阅后即焚”与最大下载限制
    should_burn = False
    if f.burn_mode > 0:
        should_burn = True
    elif f.max_downloads > 0 and f.download_count >= f.max_downloads:
        should_burn = True

    if should_burn:
        if f.burn_mode == 2: # 彻底物理销毁
            f.status = "burned"
            # 后台安全异步物理删除磁盘文件
            def destroy_file_disk(path: str):
                try:
                    if os.path.exists(path):
                        os.remove(path)
                except Exception:
                    pass
            background_tasks.add_task(destroy_file_disk, file_path)
        else:
            # 仅失效分享链接，保留磁盘源文件
            f.status = "burned"

    await db.commit()

    # 解决中文文件名下载乱码与响应头设置
    from urllib.parse import quote
    encoded_filename = quote(f.original_filename)
    
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{encoded_filename}",
        "X-Content-Type-Options": "nosniff"
    }

    return FileResponse(
        path=file_path,
        filename=f.original_filename,
        media_type=f.content_type,
        headers=headers
    )
