"""
AeroSync Cloud - Webhook 推送服务
支持 HMAC 签名验证和重试机制
"""
import json
import hmac
import hashlib
import time
from typing import Optional, Dict, Any

import requests

from api.core.logging_config import get_logger

logger = get_logger("webhook")


class WebhookPusher:
    """
    Webhook 推送器
    - 支持 HMAC-SHA256 签名防篡改
    - 支持自定义 Headers
    - 自动重试（指数退避）
    """

    def __init__(
        self,
        url: str,
        secret: Optional[str] = None,
        max_retries: int = 3,
        timeout: int = 30
    ):
        self.url = url
        self.secret = secret
        self.max_retries = max_retries
        self.timeout = timeout

    def _sign_payload(self, payload: Dict[str, Any]) -> str:
        """生成 HMAC-SHA256 签名"""
        if not self.secret:
            return ""
        body = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
        signature = hmac.new(
            self.secret.encode('utf-8'),
            body.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return f"sha256={signature}"

    def send(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """
        发送 Webhook 请求
        Returns: {"success": bool, "status_code": int, "response": str, "retries": int}
        """
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "AeroSync-Webhook/1.0",
            "X-AeroSync-Timestamp": str(int(time.time())),
        }

        # 添加签名
        if self.secret:
            signature = self._sign_payload(payload)
            headers["X-Signature"] = signature

        last_error = None
        for attempt in range(self.max_retries):
            try:
                logger.info(f"Webhook 发送: {self.url}, 尝试 {attempt + 1}/{self.max_retries}")
                resp = requests.post(
                    self.url,
                    json=payload,
                    headers=headers,
                    timeout=self.timeout
                )

                # 2xx 成功
                if resp.status_code < 300:
                    return {
                        "success": True,
                        "status_code": resp.status_code,
                        "response": resp.text[:2000],
                        "retries": attempt
                    }

                # 4xx 客户端错误不重试（签名错误等）
                if 400 <= resp.status_code < 500:
                    return {
                        "success": False,
                        "status_code": resp.status_code,
                        "response": resp.text[:2000],
                        "error": f"Client error {resp.status_code}",
                        "retries": attempt
                    }

                # 5xx 服务端错误，继续重试
                last_error = f"Server error {resp.status_code}"

            except requests.Timeout:
                last_error = "Request timeout"
            except requests.ConnectionError:
                last_error = "Connection error"
            except Exception as e:
                last_error = str(e)

            # 指数退避：1s, 2s, 4s
            if attempt < self.max_retries - 1:
                sleep_time = 2 ** attempt
                time.sleep(sleep_time)

        return {
            "success": False,
            "error": last_error,
            "retries": self.max_retries,
            "response": ""
        }