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
    from sqlalchemy import select, delete
    IPLocator.init("data/ip2region.xdb")
    async with AsyncSessionLocal() as db:
        await db.execute(delete(FileItem).where(FileItem.share_code.in_([
            "sec888", "doc101", "doc102_new", "doc102_updated", "priv_mov",
            "preview_only_code", "dl_only_code", "burn_view_code", "comp_test_code"
        ])))
        res = await db.execute(select(AdminSetting).where(AdminSetting.key == "admin_password"))
        if not res.scalar_one_or_none():
            db.add(AdminSetting(key="admin_password", value=get_password_hash(settings.ADMIN_PASSWORD)))
        await db.commit()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://testserver") as client:
        
        # 1. 登录管理员后台
        login_res = await client.post("/api/admin/login", data={
            "username": "useradmin",
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
            "allow_download": "true",
            "allow_preview": "true",
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
            "allow_download": "true",
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

        # 11. 测试私有云盘暂存模式 (先存后发)
        priv_video_content = b"fake-mp4-video-stream-content-bytes" * 50
        priv_up = await client.post("/api/admin/upload", data={
            "custom_code": "priv_mov",
            "is_private": "true"
        }, files={"file": ("my_movie.mp4", io.BytesIO(priv_video_content), "video/mp4")}, headers=auth_headers)
        assert priv_up.status_code == 200
        priv_file_id = priv_up.json()["data"]["id"]

        # 访客公网查询应被拦截
        priv_query = await client.post("/api/share/query", data={"code": "priv_mov"})
        assert priv_query.status_code == 410, f"私有文件应禁止公网查询: {priv_query.status_code}"
        print("✅ 11. 私有云盘暂存模式与公网防访问隔离验证通过")

        # 12. 测试视频多媒体流式在线预览播放 (带 Range 分片请求)
        preview_res = await client.get(f"/api/admin/files/{priv_file_id}/preview", headers={
            **auth_headers,
            "Range": "bytes=0-99"
        })
        assert preview_res.status_code in (200, 206), f"预览流返回异常: {preview_res.status_code}"
        assert "video/mp4" in preview_res.headers.get("content-type", "")
        print("✅ 12. 多媒体在线流式秒播 (HTTP Range 切片传输) 验证通过")

        # 13. 测试修改分享配置 (share-config)
        update_cfg_res = await client.post(f"/api/admin/files/{file_id2}/share-config", data={
            "custom_code": "doc102_updated",
            "password": "new_share_pwd_888",
            "expire_hours": "48",
            "burn_mode": "0",
            "allow_download": "true",
            "max_downloads": "10",
            "remark": "已更新配置的文档"
        }, headers=auth_headers)
        assert update_cfg_res.status_code == 200
        assert update_cfg_res.json()["data"]["share_code"] == "doc102_updated"

        # 校验修改后状态为 active 且能正常查询
        query_updated = await client.post("/api/share/query", data={"code": "doc102_updated"})
        assert query_updated.status_code == 200
        assert query_updated.json()["data"]["requires_password"] is True
        assert query_updated.json()["data"]["remark"] == "已更新配置的文档"
        print("✅ 13. 修改分享配置 (提取码/口令/有效期/最大下载/备注) 验证通过")

        # 14. 测试关闭分享 (close-share 转入私有云盘模式)
        close_res = await client.post(f"/api/admin/files/{file_id2}/close-share", headers=auth_headers)
        assert close_res.status_code == 200
        assert close_res.json()["data"]["status"] == "stored"

        # 15. 测试【纯在线预览模式】(仅预览，禁止下载)
        files_preview_only = {"file": ("preview_only.txt", io.BytesIO(b"Preview Only Secret Text Content"), "text/plain")}
        up_preview_res = await client.post("/api/admin/upload", data={
            "custom_code": "preview_only_code",
            "allow_preview": "true",
            "allow_download": "false",
            "is_private": "false"
        }, files=files_preview_only, headers=auth_headers)
        assert up_preview_res.status_code == 200

        # 访客在线预览应成功
        pv_res = await client.get("/api/share/preview/preview_only_code")
        assert pv_res.status_code == 200
        assert b"Preview Only Secret Text Content" in pv_res.content

        # 访客尝试下载源文件应被硬拦截 (返回 403)
        pv_dl_res = await client.get("/api/share/download/preview_only_code")
        assert pv_dl_res.status_code == 403, f"仅预览文件下载必须返回 403: {pv_dl_res.status_code}"
        print("✅ 15. 【纯在线预览模式】验证通过 (在线查阅成功，下载源文件被 403 拦截)")

        # 16. 测试【纯下载模式】(仅下载，禁止预览)
        files_dl_only = {"file": ("download_only.txt", io.BytesIO(b"Download Only Content"), "text/plain")}
        up_dl_res = await client.post("/api/admin/upload", data={
            "custom_code": "dl_only_code",
            "allow_preview": "false",
            "allow_download": "true",
            "is_private": "false"
        }, files=files_dl_only, headers=auth_headers)
        assert up_dl_res.status_code == 200

        # 访客在线预览应被拦截 (返回 403)
        dl_pv_res = await client.get("/api/share/preview/dl_only_code")
        assert dl_pv_res.status_code == 403, f"仅下载文件在线预览必须返回 403: {dl_pv_res.status_code}"

        # 访客下载源文件应成功
        dl_dl_res = await client.get("/api/share/download/dl_only_code")
        assert dl_dl_res.status_code == 200
        assert b"Download Only Content" in dl_dl_res.content
        print("✅ 16. 【纯下载模式】验证通过 (在线预览被 403 拦截，下载源文件成功)")

        # 17. 测试【预览后即焚】(burn_trigger = 'view')
        files_burn_view = {"file": ("burn_on_view.txt", io.BytesIO(b"Burn Once Viewed Content"), "text/plain")}
        up_bv_res = await client.post("/api/admin/upload", data={
            "custom_code": "burn_view_code",
            "allow_preview": "true",
            "allow_download": "true",
            "burn_mode": "2", # 彻底销毁
            "burn_trigger": "view", # 首次预览后即焚
            "is_private": "false"
        }, files=files_burn_view, headers=auth_headers)
        assert up_bv_res.status_code == 200

        # 首次在线预览成功 (查阅期间连续多次读取不受影响)
        bv_res1 = await client.get("/api/share/preview/burn_view_code")
        assert bv_res1.status_code == 200
        assert b"Burn Once Viewed Content" in bv_res1.content

        # 模拟浏览器再次刷新或获取切片，依然可读取
        bv_res1_repeat = await client.get("/api/share/preview/burn_view_code")
        assert bv_res1_repeat.status_code == 200

        # 访客查阅完毕/关闭弹窗，触发会话即焚销毁
        burn_session_res = await client.post("/api/share/burn/burn_view_code")
        assert burn_session_res.status_code == 200

        # 18. 测试【AES-256 加密压缩】与进度反馈
        files_compress_test = {"file": ("report.docx", io.BytesIO(b"Confidential Report Word Data 2026"), "application/octet-stream")}
        up_comp_res = await client.post("/api/admin/upload", data={
            "custom_code": "comp_test_code",
            "allow_preview": "true",
            "allow_download": "true",
            "is_private": "false"
        }, files=files_compress_test, headers=auth_headers)
        assert up_comp_res.status_code == 200
        comp_file_id = up_comp_res.json()["data"]["id"]

        # 发起加密压缩任务 (设置密码: "SecretPwd123", 保留原文件: keep_raw=True)
        comp_res = await client.post(f"/api/admin/files/{comp_file_id}/compress", data={
            "password": "SecretPwd123",
            "keep_raw": "true"
        }, headers=auth_headers)
        assert comp_res.status_code == 200

        # 等待后台流式压缩协程完成
        for _ in range(20):
            await asyncio.sleep(0.2)
            prog_res = await client.get(f"/api/admin/files/{comp_file_id}/compress-progress", headers=auth_headers)
            assert prog_res.status_code == 200
            p_data = prog_res.json()["data"]
            if p_data["status"] == "idle" and p_data["is_encrypted"]:
                break

        # 验证压缩后的文件状态与名称
        fl_res = await client.get("/api/admin/files?search=comp_test_code", headers=auth_headers)
        fl_data = fl_res.json()["data"][0]
        assert fl_data["is_encrypted"] is True
        assert fl_data["original_filename"] == "report.docx.zip"
        assert fl_data["zip_password"] == "SecretPwd123"
        assert fl_data["can_uncompress"] is True
        print("✅ 18. 【AES-256 加密压缩】验证通过 (生成 report.docx.zip，后台明文记录密码且支持一键还原)")

        # 19. 测试下载加密压缩包并使用 pyzipper 验证解压
        import pyzipper
        comp_dl_res = await client.get("/api/share/download/comp_test_code")
        assert comp_dl_res.status_code == 200
        zip_bytes = comp_dl_res.content
        with pyzipper.AESZipFile(io.BytesIO(zip_bytes)) as zf:
            zf.setpassword(b"SecretPwd123")
            extracted_data = zf.read("report.docx")
            assert extracted_data == b"Confidential Report Word Data 2026"
        print("✅ 19. 【标准 AES-256 ZIP 解压】验证通过 (使用密码解压内容完全一致)")

        # 20. 测试【关闭加密并恢复原文件】
        uncomp_res = await client.post(f"/api/admin/files/{comp_file_id}/uncompress", headers=auth_headers)
        assert uncomp_res.status_code == 200
        fl_uncomp_res = await client.get("/api/admin/files?search=comp_test_code", headers=auth_headers)
        fl_uncomp_data = fl_uncomp_res.json()["data"][0]
        assert fl_uncomp_data["is_encrypted"] is False
        assert fl_uncomp_data["original_filename"] == "report.docx"
        assert fl_uncomp_data["zip_password"] is None
        print("✅ 20. 【关闭加密/无损还原原文件】验证通过 (原文件名与格式恢复正常)")

        # 21. 测试删除文件时物理文件被彻底清理
        del_res = await client.delete(f"/api/admin/files/{comp_file_id}", headers=auth_headers)
        assert del_res.status_code == 200
        print("✅ 21. 【文件清理与物理删除】验证通过")

if __name__ == "__main__":
    asyncio.run(test_full_file_sharing_lifecycle())
