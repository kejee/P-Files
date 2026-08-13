# -*- coding: utf-8 -*-
import datetime
import ipaddress
import time
from collections import defaultdict
from typing import Optional, List, Tuple
import bcrypt
import jwt
from fastapi import Request, HTTPException, status, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from config import settings

security_bearer = HTTPBearer(auto_error=False)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    if not hashed_password:
        return True
    try:
        password_bytes = plain_password.encode('utf-8')[:72]
        hash_bytes = hashed_password.encode('utf-8')
        return bcrypt.checkpw(password_bytes, hash_bytes)
    except Exception:
        return False

def get_password_hash(password: str) -> str:
    password_bytes = password.encode('utf-8')[:72]
    salt = bcrypt.gensalt()
    return bcrypt.hashpw(password_bytes, salt).decode('utf-8')

def create_access_token(data: dict, expires_delta: Optional[datetime.timedelta] = None) -> str:
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.datetime.utcnow() + expires_delta
    else:
        expire = datetime.datetime.utcnow() + datetime.timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt

def decode_access_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except jwt.PyJWTError:
        return None

def get_client_ip(request: Request) -> str:
    """
    智能提取访客真实 IP
    按优先级解析 Cloudflare、X-Forwarded-For、X-Real-IP 或底层 client.host
    """
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip.strip()

    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        # X-Forwarded-For: client, proxy1, proxy2
        ips = [ip.strip() for ip in x_forwarded_for.split(",")]
        if ips:
            return ips[0]

    x_real_ip = request.headers.get("X-Real-IP")
    if x_real_ip:
        return x_real_ip.strip()

    if request.client and request.client.host:
        return request.client.host
    return "127.0.0.1"

def is_ip_allowed(client_ip: str, allowed_ips_str: Optional[str]) -> bool:
    """
    验证客户端 IP 是否在允许的白名单范围内
    支持单个 IP、逗号/换行分隔的多个 IP、CIDR 掩码段 (如 192.168.1.0/24)
    """
    if not allowed_ips_str or not allowed_ips_str.strip():
        return True # 未设置白名单时默认允许所有 IP

    # 清理格式并切分规则
    rules = [r.strip() for r in allowed_ips_str.replace("\n", ",").replace(";", ",").split(",") if r.strip()]
    if not rules:
        return True

    # 规范化 IPv6 映射的 IPv4
    clean_client_ip = client_ip
    if clean_client_ip.startswith("::ffff:"):
        clean_client_ip = clean_client_ip.replace("::ffff:", "")

    try:
        client_addr = ipaddress.ip_address(clean_client_ip)
    except ValueError:
        return False

    for rule in rules:
        try:
            if "/" in rule:
                # CIDR 网段匹配
                network = ipaddress.ip_network(rule, strict=False)
                if client_addr in network:
                    return True
            else:
                # 单 IP 匹配
                rule_addr = ipaddress.ip_address(rule)
                if client_addr == rule_addr:
                    return True
        except ValueError:
            continue

    return False

# 防爆破限流器 (内存记录)
class RateLimiter:
    def __init__(self, max_attempts: int = 5, window_seconds: int = 300):
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        # ip -> list of timestamp
        self.failed_attempts = defaultdict(list)

    def is_locked(self, ip: str) -> Tuple[bool, int]:
        """检查 IP 是否被锁定，返回 (是否锁定, 剩余锁定秒数)"""
        now = time.time()
        attempts = self.failed_attempts[ip]
        # 移出时间窗口外的记录
        self.failed_attempts[ip] = [t for t in attempts if now - t < self.window_seconds]
        
        valid_attempts = self.failed_attempts[ip]
        if len(valid_attempts) >= self.max_attempts:
            oldest_in_window = valid_attempts[0]
            remaining = int(self.window_seconds - (now - oldest_in_window))
            return True, max(1, remaining)
        return False, 0

    def record_failure(self, ip: str):
        now = time.time()
        self.failed_attempts[ip].append(now)

    def record_success(self, ip: str):
        if ip in self.failed_attempts:
            del self.failed_attempts[ip]

# 全局限流器实例
auth_limiter = RateLimiter(max_attempts=settings.RATE_LIMIT_MAX_ATTEMPTS, window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS)
share_pwd_limiter = RateLimiter(max_attempts=settings.RATE_LIMIT_MAX_ATTEMPTS, window_seconds=settings.RATE_LIMIT_WINDOW_SECONDS)
