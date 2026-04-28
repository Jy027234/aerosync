"""
API 端点测试
覆盖：上传URL、上传通知、任务查询、租户配置
"""
import pytest
from unittest.mock import MagicMock

from api.models import FileTask, TenantConfig


class TestUploadUrl:
    """上传 URL 相关测试"""

    def test_upload_url_success(self, client, auth_headers, monkeypatch):
        """正常请求上传 URL"""
        # mock OSS bucket
        mock_bucket = MagicMock()
        mock_bucket.sign_url.return_value = "https://test.oss.aliyuncs.com/upload?signature=xxx"
        monkeypatch.setattr(client.app.state, "oss_bucket", mock_bucket)

        resp = client.post(
            "/api/v1/upload-url",
            json={"filename": "report.xlsx", "size": 1024},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "upload_url" in data
        assert "object_key" in data
        assert data["object_key"].startswith("uploads/tenant-test/")
        assert data["expires_in"] == 15 * 60
        mock_bucket.sign_url.assert_called_once()

    def test_upload_url_unauthorized(self, client):
        """缺少或无效鉴权应返回 401"""
        resp = client.post(
            "/api/v1/upload-url",
            json={"filename": "report.xlsx", "size": 1024},
            headers={"X-Tenant-Id": "tenant-test", "Authorization": "InvalidToken"},
        )
        assert resp.status_code == 401

    def test_upload_url_oss_unavailable(self, client, auth_headers, monkeypatch):
        """OSS 未初始化时返回 503"""
        monkeypatch.setattr(client.app.state, "oss_bucket", None)
        resp = client.post(
            "/api/v1/upload-url",
            json={"filename": "report.xlsx", "size": 1024},
            headers=auth_headers,
        )
        assert resp.status_code == 503

    def test_upload_url_invalid_body(self, client, auth_headers):
        """非法请求体应返回 422"""
        resp = client.post(
            "/api/v1/upload-url",
            json={"filename": "", "size": -1},
            headers=auth_headers,
        )
        assert resp.status_code == 422


class TestNotifyUpload:
    """上传完成通知测试"""

    def test_notify_success(self, client, auth_headers, db_session, monkeypatch):
        """正常通知创建任务"""
        # mock Celery 任务
        mock_task = MagicMock()
        monkeypatch.setattr("api.main.process_file_task", mock_task)

        # 先创建租户配置
        cfg = TenantConfig(tenant_id="tenant-test", webhook_url="https://hooks.example.com")
        db_session.add(cfg)
        db_session.commit()

        resp = client.post(
            "/api/v1/notify",
            json={
                "object_key": "uploads/tenant-test/20240101/report.xlsx",
                "filename": "report.xlsx",
                "size": 1024,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "queued"
        assert "task_id" in data
        mock_task.delay.assert_called_once()

    def test_notify_forbidden_object_key(self, client, auth_headers):
        """object_key 不属于当前租户时返回 403"""
        resp = client.post(
            "/api/v1/notify",
            json={
                "object_key": "uploads/other-tenant/20240101/report.xlsx",
                "filename": "report.xlsx",
                "size": 1024,
            },
            headers=auth_headers,
        )
        assert resp.status_code == 403


class TestTaskQuery:
    """任务查询测试"""

    def test_get_task(self, client, db_session, auth_headers):
        """查询单个任务"""
        task = FileTask(
            tenant_id="tenant-test",
            filename="test.pdf",
            object_key="uploads/tenant-test/20240101/test.pdf",
            file_size=2048,
            file_type="pdf",
            status="pending",
        )
        db_session.add(task)
        db_session.commit()
        db_session.refresh(task)

        resp = client.get(f"/api/v1/tasks/{task.id}", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == task.id
        assert data["filename"] == "test.pdf"

    def test_get_task_not_found(self, client, auth_headers):
        """查询不存在的任务"""
        resp = client.get("/api/v1/tasks/99999", headers=auth_headers)
        assert resp.status_code == 404

    def test_list_tasks(self, client, db_session, auth_headers):
        """分页列表任务"""
        for i in range(5):
            db_session.add(
                FileTask(
                    tenant_id="tenant-test",
                    filename=f"file{i}.xlsx",
                    object_key=f"uploads/tenant-test/20240101/file{i}.xlsx",
                    file_size=100,
                    file_type="xlsx",
                    status="pending" if i % 2 == 0 else "delivered",
                )
            )
        db_session.commit()

        resp = client.get("/api/v1/tasks?page=1&page_size=3", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] == 5
        assert len(data["tasks"]) == 3
        assert data["page"] == 1
        assert data["page_size"] == 3

    def test_list_tasks_filter_by_status(self, client, db_session, auth_headers):
        """按状态筛选"""
        db_session.add(
            FileTask(
                tenant_id="tenant-test",
                filename="a.xlsx",
                object_key="uploads/tenant-test/a.xlsx",
                file_size=100,
                status="failed",
            )
        )
        db_session.commit()

        resp = client.get("/api/v1/tasks?status=failed", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert all(t["status"] == "failed" for t in data["tasks"])


class TestTenantConfig:
    """租户配置管理测试"""

    def test_get_tenant_config(self, client, db_session):
        """获取租户配置"""
        cfg = TenantConfig(
            tenant_id="tenant-abc",
            name="Test Airline",
            webhook_url="https://hooks.example.com",
            rules=[{"type": "keyword_tag", "tag": "urgent"}],
        )
        db_session.add(cfg)
        db_session.commit()

        resp = client.get("/api/v1/admin/tenants/tenant-abc/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["tenant_id"] == "tenant-abc"
        assert data["name"] == "Test Airline"
        assert data["rules"][0]["type"] == "keyword_tag"

    def test_get_tenant_config_not_found(self, client):
        """租户配置不存在"""
        resp = client.get("/api/v1/admin/tenants/nonexist/config")
        assert resp.status_code == 404

    def test_update_tenant_config(self, client, db_session):
        """更新租户配置"""
        cfg = TenantConfig(tenant_id="tenant-abc", name="Old Name")
        db_session.add(cfg)
        db_session.commit()

        resp = client.put(
            "/api/v1/admin/tenants/tenant-abc/config",
            json={"name": "New Name", "webhook_url": "https://new.example.com"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["name"] == "New Name"
        assert data["webhook_url"] == "https://new.example.com"

    def test_create_tenant_config(self, client):
        """创建租户配置"""
        resp = client.post(
            "/api/v1/admin/tenants/tenant-new/config",
            json={"name": "New Tenant", "enabled": 1},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["tenant_id"] == "tenant-new"
        assert data["name"] == "New Tenant"

    def test_create_tenant_config_conflict(self, client, db_session):
        """重复创建应返回 409"""
        db_session.add(TenantConfig(tenant_id="tenant-dup"))
        db_session.commit()

        resp = client.post(
            "/api/v1/admin/tenants/tenant-dup/config",
            json={"name": "Dup"},
        )
        assert resp.status_code == 409


class TestAdminTasks:
    """管理后台任务接口测试"""

    def test_admin_list_tasks(self, client, db_session):
        """管理后台列表所有任务"""
        db_session.add(
            FileTask(
                tenant_id="tenant-a",
                filename="a.xlsx",
                object_key="uploads/tenant-a/a.xlsx",
                file_size=100,
                status="pending",
            )
        )
        db_session.commit()

        resp = client.get("/api/v1/admin/tasks")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total"] >= 1

    def test_admin_list_tasks_filter(self, client, db_session):
        """管理后台按租户筛选"""
        db_session.add(
            FileTask(
                tenant_id="tenant-filter",
                filename="b.xlsx",
                object_key="uploads/tenant-filter/b.xlsx",
                file_size=100,
                status="delivered",
            )
        )
        db_session.commit()

        resp = client.get("/api/v1/admin/tasks?tenant_id=tenant-filter&status=delivered")
        assert resp.status_code == 200
        data = resp.json()
        assert all(t["tenant_id"] == "tenant-filter" for t in data["tasks"])

    def test_retry_task(self, client, db_session, monkeypatch):
        """手动重试失败任务"""
        task = FileTask(
            tenant_id="tenant-test",
            filename="fail.xlsx",
            object_key="uploads/tenant-test/fail.xlsx",
            file_size=100,
            status="failed",
            retry_count=1,
        )
        db_session.add(task)
        db_session.commit()
        db_session.refresh(task)

        mock_task = MagicMock()
        monkeypatch.setattr("api.main.process_file_task", mock_task)

        resp = client.post(f"/api/v1/admin/tasks/{task.id}/retry")
        assert resp.status_code == 200
        assert resp.json()["task_id"] == task.id
        mock_task.delay.assert_called_once_with(task.id)

        db_session.refresh(task)
        assert task.status == "pending"
        assert task.error_msg is None

    def test_retry_task_not_found(self, client):
        """重试不存在的任务"""
        resp = client.post("/api/v1/admin/tasks/99999/retry")
        assert resp.status_code == 404

    def test_retry_task_invalid_status(self, client, db_session):
        """状态不允许重试"""
        task = FileTask(
            tenant_id="tenant-test",
            filename="pending.xlsx",
            object_key="uploads/tenant-test/pending.xlsx",
            file_size=100,
            status="pending",
        )
        db_session.add(task)
        db_session.commit()
        db_session.refresh(task)

        resp = client.post(f"/api/v1/admin/tasks/{task.id}/retry")
        assert resp.status_code == 400
