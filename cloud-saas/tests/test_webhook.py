"""
Webhook 推送服务测试
测试签名生成和重试逻辑（使用 unittest.mock 模拟 HTTP）
"""
import json
import time
from unittest.mock import MagicMock, patch
import pytest

from api.services.webhook import WebhookPusher


class TestSignPayload:
    """HMAC 签名测试"""

    def test_sign_with_secret(self):
        pusher = WebhookPusher("https://example.com", secret="mysecret")
        payload = {"task_id": 1, "status": "ok"}
        sig = pusher._sign_payload(payload)
        assert sig.startswith("sha256=")
        assert len(sig) == 7 + 64  # 'sha256=' + 64 hex chars

    def test_sign_without_secret(self):
        pusher = WebhookPusher("https://example.com", secret=None)
        sig = pusher._sign_payload({"a": 1})
        assert sig == ""

    def test_sign_deterministic(self):
        """相同 payload + secret 应产生相同签名"""
        pusher = WebhookPusher("https://example.com", secret="key")
        payload = {"b": 2, "a": 1}
        sig1 = pusher._sign_payload(payload)
        sig2 = pusher._sign_payload(payload)
        assert sig1 == sig2

    def test_sign_order_independent(self):
        """键排序后签名应一致（内部使用 sort_keys）"""
        pusher = WebhookPusher("https://example.com", secret="key")
        sig1 = pusher._sign_payload({"a": 1, "b": 2})
        sig2 = pusher._sign_payload({"b": 2, "a": 1})
        assert sig1 == sig2


class TestSend:
    """发送测试"""

    def test_send_success(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = '{"received": true}'

        pusher = WebhookPusher("https://hooks.example.com", secret="secret")
        with patch("api.services.webhook.requests.post", return_value=mock_resp) as mock_post:
            result = pusher.send({"task_id": 1})

        assert result["success"] is True
        assert result["status_code"] == 200
        assert result["retries"] == 0
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args.kwargs
        assert call_kwargs["json"] == {"task_id": 1}
        assert "X-Signature" in call_kwargs["headers"]
        assert "X-AeroSync-Timestamp" in call_kwargs["headers"]

    def test_send_client_error_no_retry(self):
        """4xx 错误不重试"""
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_resp.text = "Forbidden"

        pusher = WebhookPusher("https://hooks.example.com", secret="secret", max_retries=3)
        with patch("api.services.webhook.requests.post", return_value=mock_resp) as mock_post:
            result = pusher.send({"task_id": 1})

        assert result["success"] is False
        assert result["status_code"] == 403
        assert "error" in result
        assert mock_post.call_count == 1  # 不重试

    def test_send_server_error_with_retry(self):
        """5xx 错误应重试"""
        mock_resp = MagicMock()
        mock_resp.status_code = 502
        mock_resp.text = "Bad Gateway"

        pusher = WebhookPusher("https://hooks.example.com", secret="secret", max_retries=3)
        with patch("api.services.webhook.requests.post", return_value=mock_resp) as mock_post:
            with patch("api.services.webhook.time.sleep") as mock_sleep:
                result = pusher.send({"task_id": 1})

        assert result["success"] is False
        assert mock_post.call_count == 3
        assert result["retries"] == 3
        # 指数退避：1s, 2s
        mock_sleep.assert_any_call(1)
        mock_sleep.assert_any_call(2)

    def test_send_timeout_retry(self):
        """超时异常应重试"""
        from requests import Timeout

        pusher = WebhookPusher("https://hooks.example.com", secret="secret", max_retries=2)
        with patch("api.services.webhook.requests.post", side_effect=Timeout) as mock_post:
            with patch("api.services.webhook.time.sleep"):
                result = pusher.send({"task_id": 1})

        assert result["success"] is False
        assert "timeout" in result["error"].lower()
        assert mock_post.call_count == 2

    def test_send_connection_error_retry(self):
        """连接异常应重试"""
        from requests import ConnectionError

        pusher = WebhookPusher("https://hooks.example.com", secret="secret", max_retries=2)
        with patch("api.services.webhook.requests.post", side_effect=ConnectionError) as mock_post:
            with patch("api.services.webhook.time.sleep"):
                result = pusher.send({"task_id": 1})

        assert result["success"] is False
        assert "connection" in result["error"].lower()
        assert mock_post.call_count == 2

    def test_send_success_after_retry(self):
        """重试后成功"""
        mock_fail = MagicMock()
        mock_fail.status_code = 500
        mock_ok = MagicMock()
        mock_ok.status_code = 200
        mock_ok.text = "ok"

        pusher = WebhookPusher("https://hooks.example.com", secret="secret", max_retries=3)
        with patch("api.services.webhook.requests.post", side_effect=[mock_fail, mock_ok]) as mock_post:
            with patch("api.services.webhook.time.sleep"):
                result = pusher.send({"task_id": 1})

        assert result["success"] is True
        assert result["status_code"] == 200
        assert result["retries"] == 1
        assert mock_post.call_count == 2

    def test_no_secret_no_signature_header(self):
        """无 secret 时不应包含 X-Signature"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "ok"

        pusher = WebhookPusher("https://hooks.example.com", secret=None)
        with patch("api.services.webhook.requests.post", return_value=mock_resp) as mock_post:
            pusher.send({"task_id": 1})

        call_kwargs = mock_post.call_args.kwargs
        assert "X-Signature" not in call_kwargs["headers"]

    def test_response_truncation(self):
        """超长响应应被截断"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "x" * 3000

        pusher = WebhookPusher("https://hooks.example.com", secret="secret")
        with patch("api.services.webhook.requests.post", return_value=mock_resp):
            result = pusher.send({"task_id": 1})

        assert len(result["response"]) <= 2000
