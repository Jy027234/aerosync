"""
健康检查端点测试
"""
import pytest


def test_health_check(client):
    """GET /api/v1/health 应返回 ok"""
    resp = client.get("/api/v1/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["version"] == "1.1.0"
    assert "timestamp" in data


def test_root(client):
    """GET / 应返回服务基本信息"""
    resp = client.get("/")
    assert resp.status_code == 200
    data = resp.json()
    assert data["service"] == "AeroSync Cloud API"
    assert data["docs"] == "/docs"
