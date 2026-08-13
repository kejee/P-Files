# -*- coding: utf-8 -*-
"""
ip2region v2.0 xdb 纯 Python 离线搜索器
官方算法实现，无需额外 C 扩展，毫秒级快速定位
"""
import io
import os
import socket
import struct

class XdbSearcher(object):
    # 常量定义
    HEADER_INFO_LENGTH = 256
    VECTOR_INDEX_ROWS = 256
    VECTOR_INDEX_COLS = 256
    VECTOR_INDEX_SIZE = 8
    SEGMENT_INDEX_SIZE = 14

    def __init__(self, dbfile: str = None, content_buff: bytes = None):
        self.db_file = dbfile
        self.handle = None
        self.header = None
        self.vector_index = None
        self.content_buff = content_buff

        if content_buff is not None:
            pass
        elif dbfile and os.path.exists(dbfile):
            self.handle = io.open(dbfile, "rb")

    def close(self):
        if self.handle is not None:
            self.handle.close()
            self.handle = None

    @staticmethod
    def load_content_from_file(dbfile: str):
        if not os.path.exists(dbfile):
            return None
        with io.open(dbfile, "rb") as f:
            return f.read()

    @staticmethod
    def check_ip(ip: str):
        try:
            socket.inet_aton(ip)
            return True
        except socket.error:
            return False

    @staticmethod
    def ip_to_long(ip: str):
        _ip = socket.inet_aton(ip)
        return struct.unpack("!I", _ip)[0]

    def search(self, ip: str) -> str:
        if isinstance(ip, str):
            if not self.check_ip(ip):
                return ""
            ip_val = self.ip_to_long(ip)
        else:
            ip_val = ip

        # 256x256 vector index
        il0 = (ip_val >> 24) & 0xFF
        il1 = (ip_val >> 16) & 0xFF
        idx = il0 * self.VECTOR_INDEX_COLS * self.VECTOR_INDEX_SIZE + il1 * self.VECTOR_INDEX_SIZE

        if self.content_buff:
            v_index = self.content_buff
            s_ptr = struct.unpack("<I", v_index[self.HEADER_INFO_LENGTH + idx : self.HEADER_INFO_LENGTH + idx + 4])[0]
            e_ptr = struct.unpack("<I", v_index[self.HEADER_INFO_LENGTH + idx + 4 : self.HEADER_INFO_LENGTH + idx + 8])[0]
        elif self.handle:
            self.handle.seek(self.HEADER_INFO_LENGTH + idx)
            buff = self.handle.read(8)
            s_ptr, e_ptr = struct.unpack("<II", buff)
        else:
            return ""

        if s_ptr == 0:
            return ""

        # binary search in index block
        low = 0
        mid = 0
        high = int((e_ptr - s_ptr) / self.SEGMENT_INDEX_SIZE)
        data_len = 0
        data_ptr = 0

        while low <= high:
            mid = int((low + high) >> 1)
            pos = s_ptr + mid * self.SEGMENT_INDEX_SIZE

            if self.content_buff:
                buff = self.content_buff[pos : pos + self.SEGMENT_INDEX_SIZE]
            else:
                self.handle.seek(pos)
                buff = self.handle.read(self.SEGMENT_INDEX_SIZE)

            sip, eip, data_len, data_ptr = struct.unpack("<IIHI", buff)

            if ip_val < sip:
                high = mid - 1
            elif ip_val > eip:
                low = mid + 1
            else:
                # found
                break
        else:
            return ""

        if data_len == 0:
            return ""

        if self.content_buff:
            data = self.content_buff[data_ptr : data_ptr + data_len]
        else:
            self.handle.seek(data_ptr)
            data = self.handle.read(data_len)

        return data.decode("utf-8")
