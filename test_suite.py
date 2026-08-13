import asyncio
import os
import io
from httpx import AsyncClient, ASGITransport
from main import app, settings
from database import init_db, AsyncSessionLocal, FileItem, AccessLog

async def test_full_file_sharing_lifecycle():
    # 初始化测试环境
    await init_db()
    from ip_locator import IPLocator
    from security import get_password_hash
    from database import AdminSetting
    from sqlalchemy import select
    IPLocator.init("data/ip2region.xdb")
    async with AsyncSessionLocal() as db:
        res = await db.execute(select(AdminSetting).where(AdminSetting.key == "admin_password"))
        if not res.scalar_one_or_none():
            db.add(AdminSetting(key="admin_password", value=get_password_hash(settings.ADMIN_PASSWORD)))
            await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        
        # 1. 登录管理员后台
        login_res = await client.post("/api/admin/login", data={
            "username": "admin",
            "password": settings.ADMIN_PASSWORD
        })
        assert login_res.status_code == 200, f"Login failed: {login_res.text}"
        token = login_res.json()["token"]
        auth_headers = {"Authorization": f"Bearer {token}"}
        print("✅ 1. 管理员登录成功")

        # 2. 测试上传文件：组合策略 (口令 + 阅后即焚物理销毁 + IP白名单 + 备注)
        dummy_file_content = b"Confidential Payload Content 2026"
        files = {"file": ("secret.txt", io.BytesIO(dummy_file_content), "text/plain")}
        upload_data = {
            "custom_code": "sec888",
            "password": "pass_xyz_123",
            "expire_hours": "1",
            "burn_mode": "2", # 彻底销毁
            "max_downloads": "1",
            "allowed_ips": "183.14.132.228, 192.168.1.0/24", # 仅限特定IP访问
            "remark": "高危私密文件"
        }
        up_res = await client.post("/api/admin/upload", data=upload_data, files=files, headers=auth_headers)
        assert up_res.status_code == 200, f"Upload failed: {up_res.text}"
        assert up_res.json()["data"]["share_code"] == "sec888"
        print("✅ 2. 多策略自由组合文件上传成功 (提取码: sec888)")

        # 3. 测试 IP 白名单拦截：模拟非法 IP 访问
        bad_ip_headers = {"X-Forwarded-For": "203.0.113.199"} # 非白名单公网 IP
        blocked_res = await client.post("/api/share/query", data={"code": "sec888"}, headers=bad_ip_headers)
        assert blocked_res.status_code == 403, f"Expected 403 but got {blocked_res.status_code}"
        assert "不在允许的白名单" in blocked_res.json()["detail"]
        print("✅ 3. IP 白名单安全拦截验证通过 (返回 403 Forbidden)")

        # 4. 测试合法 IP 查询信息 (模拟 183.14.132.228 深圳电信)
        good_ip_headers = {"X-Forwarded-For": "183.14.132.228"}
        query_res = await client.post("/api/share/query", data={"code": "sec888"}, headers=good_ip_headers)
        assert query_res.status_code == 200
        q_data = query_res.json()["data"]
        assert q_data["requires_password"] is True
        assert q_data["burn_mode"] == 2
        assert "中国·广东省·深圳市" in q_data["client_location"]
        print(f"✅ 4. 访客提取信息与 IP 归属地解析成功: {q_data['client_location']}")

        # 5. 测试口令校验错误
        wrong_pwd_res = await client.post("/api/share/verify", data={"code": "sec888", "password": "wrong_password"}, headers=good_ip_headers)
        assert wrong_pwd_res.status_code == 400
        print("✅ 5. 口令错误安全校验拦截成功")

        # 6. 测试口令校验正确
        right_pwd_res = await client.post("/api/share/verify", data={"code": "sec888", "password": "pass_xyz_123"}, headers=good_ip_headers)
        assert right_pwd_res.status_code == 200
        dl_token = right_pwd_res.json()["download_token"]
        print("✅ 6. 口令正确验证通过，生成临时下载凭证")

        # 7. 测试下载文件与阅后即焚（彻底销毁模式）
        dl_res = await client.get(f"/api/share/download/sec888?token={dl_token}", headers=good_ip_headers)
        assert dl_res.status_code == 200
        assert dl_res.content == dummy_file_content
        print("✅ 7. 文件下载流传输成功")

        # 等待后台任务执行完成
        await asyncio.sleep(0.5)

        # 8. 验证阅后即焚生效：第二次查询或下载应失效
        burn_check_res = await client.post("/api/share/query", data={"code": "sec888"}, headers=good_ip_headers)
        assert burn_check_res.status_code in (404, 410)
        print("✅ 8. 阅后即焚生效：二次提取被拦截")

        # 9. 测试阅后即焚模式 1 (仅失效链接，保留源文件，并支持一键重新分享)
        files2 = {"file": ("manual.pdf", io.BytesIO(b"User Manual PDF 2026"), "application/pdf")}
        up2_res = await client.post("/api/admin/upload", data={
            "custom_code": "doc101",
            "burn_mode": "1", # 仅失效链接
        }, files=files2, headers=auth_headers)
        assert up2_res.status_code == 200
        file_id2 = up2_res.json()["data"]["id"]

        # 模拟下载该文件触发失效
        dl2_res = await client.get("/api/share/download/doc101")
        assert dl2_res.status_code == 200

        # 后台查看该文件状态应为 burned
        files_res = await client.get("/api/admin/files", headers=auth_headers)
        f_target = next(f for f in files_res.json()["data"] if f["id"] == file_id2)
        assert f_target["status"] == "burned"

        # 重新分享
        reshare_res = await client.post(f"/api/admin/files/{file_id2}/re-share", data={
            "custom_code": "doc102_new",
            "expire_hours": "24",
            "burn_mode": "0"
        }, headers=auth_headers)
        assert reshare_res.status_code == 200
        print("✅ 9. 阅后即焚（保留源文件模式）与重新开启分享测试通过")

        # 10. 测试管理后台日志审计接口
        logs_res = await client.get(f"/api/admin/files/{file_id2}/logs", headers=auth_headers)
        assert logs_res.status_code == 200
        assert len(logs_res.json()["data"]) >= 1

        stats_res = await client.get("/api/admin/stats", headers=auth_headers)
        assert stats_res.status_code == 200
        assert stats_res.json()["data"]["blocked_count"] >= 1
        print("✅ 10. 管理后台审计与 IP 日志报表验证通过")

if __name__ == "__main__":
    asyncio.run(test_full_file_sharing_lifecycle())
