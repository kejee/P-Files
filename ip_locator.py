# -*- coding: utf-8 -*-
import ipaddress
import logging
import os
import urllib.request
from typing import Optional
from xdb_searcher import XdbSearcher

logger = logging.getLogger("app.ip_locator")

XDB_PATH = os.path.join(os.path.dirname(__file__), "data", "ip2region.xdb")
XDB_DOWNLOAD_URL = "https://raw.githubusercontent.com/lionsoul2014/ip2region/master/data/ip2region.xdb"

class IPLocator:
    _searcher: Optional[XdbSearcher] = None
    _content_buff: Optional[bytes] = None

    @classmethod
    def init(cls, db_path: str = XDB_PATH):
        import shutil
        if not os.path.exists(db_path):
            # 1. 优先尝试从内置备份目录恢复 (应对 Docker 空卷挂载覆盖)
            builtin_path = os.path.join(os.path.dirname(__file__), "builtin_ip2region.xdb")
            if os.path.exists(builtin_path):
                try:
                    os.makedirs(os.path.dirname(db_path), exist_ok=True)
                    shutil.copyfile(builtin_path, db_path)
                    logger.info(f"已自动从内置备份恢复离线 IP 库至: {db_path}")
                except Exception as err:
                    logger.error(f"复制内置 IP 库失败: {err}")
            
            # 2. 尝试从上一级或当前目录寻找
            if not os.path.exists(db_path):
                alt_path = os.path.join(os.path.dirname(__file__), "ip2region.xdb")
                if os.path.exists(alt_path):
                    db_path = alt_path

        if os.path.exists(db_path):
            try:
                cls._content_buff = XdbSearcher.load_content_from_file(db_path)
                cls._searcher = XdbSearcher(content_buff=cls._content_buff)
                logger.info(f"成功加载离线 IP 数据库: {db_path}")
            except Exception as e:
                logger.error(f"加载离线 IP 数据库失败: {e}")
        else:
            logger.warning(f"未找到 ip2region.xdb 文件 ({db_path})，将使用内置基础解析器。")

    @classmethod
    def is_private_ip(cls, ip: str) -> bool:
        try:
            ip_obj = ipaddress.ip_address(ip)
            return ip_obj.is_private or ip_obj.is_loopback or ip_obj.is_reserved or ip_obj.is_link_local
        except ValueError:
            return False

    @classmethod
    def get_location(cls, ip: str) -> str:
        """
        解析 IP 归属地，返回可读字符串，例如：
        '中国·广东省·深圳市 (电信)' 或 '内网局域网'
        """
        if not ip or ip in ("127.0.0.1", "localhost", "::1", "0.0.0.0"):
            return "本地环回 / Localhost"

        # 清理 IPv6 映射的 IPv4 (如 ::ffff:192.168.1.1)
        if ip.startswith("::ffff:"):
            ip = ip.replace("::ffff:", "")

        if cls.is_private_ip(ip):
            return "本地局域网 (LAN)"

        if cls._searcher:
            try:
                region_raw = cls._searcher.search(ip)
                # 原始格式通常是: 国家|区域|省份|城市|ISP，例如 "中国|0|广东省|深圳市|电信"
                if region_raw:
                    parts = [p.strip() for p in region_raw.split("|") if p.strip() and p.strip() != "0"]
                    if parts:
                        # 组合成友好字符串
                        if len(parts) >= 2:
                            geo = "·".join(parts[:-1]) if parts[-1] in ("电信", "联通", "移动", "铁通", "教育网", "广电", "BGP") else "·".join(parts)
                            isp = f" ({parts[-1]})" if parts[-1] in ("电信", "联通", "移动", "铁通", "教育网", "广电", "BGP") else ""
                            return f"{geo}{isp}"
                        return "·".join(parts)
            except Exception as e:
                logger.error(f"IP 解析异常 [{ip}]: {e}")

        return "未知公网地区"
