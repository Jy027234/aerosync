"""
钉钉群文件 Webhook 连接器
处理钉钉机器人回调，支持文件链接/文本消息解析
"""
import hmac
import hashlib
import base64
import json
from typing import Dict, Any, List, Optional
from urllib.parse import urlparse

import requests
from api.connectors.base import BaseConnector, SourceFile
from api.core.logging_config import get_logger

logger = get_logger(__name__)


class DingTalkConnector(BaseConnector):
    type = "dingtalk"

    def __init__(self, tenant_id: str, config: Dict[str, Any]):
        super().__init__(tenant_id, config)
        self.webhook_secret = config.get("webhook_secret", "")
        self.allowed_senders = config.get("allowed_senders", [])  # 白名单，空则允许所有
        self.max_size_mb = config.get("max_size_mb", 50)

    def verify_signature(self, timestamp: str, sign: str) -> bool:
        """验证钉钉机器人签名"""
        if not self.webhook_secret:
            return True
        string_to_sign = f"{timestamp}\n{self.webhook_secret}"
        hmac_code = hmac.new(
            self.webhook_secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        expected = base64.b64encode(hmac_code).decode("utf-8")
        return expected == sign

    def handle_webhook(self, payload: Dict[str, Any]) -> Optional[SourceFile]:
        """
        处理钉钉回调消息
        返回 SourceFile 如果包含可下载的文件
        """
        if not self.enabled:
            return None

        msg_type = payload.get("msgtype", "")
        sender = payload.get("senderStaffId", "")

        if self.allowed_senders and sender not in self.allowed_senders:
            logger.warning(f"[DingTalk] 发送者不在白名单: {sender}")
            return None

        # 处理文件消息
        if msg_type == "file" and "file" in payload:
            file_info = payload["file"]
            download_url = file_info.get("downloadUrl", "")
            filename = file_info.get("fileName", "unknown")
            return self._download_file(download_url, filename, meta={
                "sender": sender,
                "conversation_type": payload.get("conversationType", ""),
            })

        # 处理文本消息中的链接（简单正则匹配）
        if msg_type == "text":
            content = payload.get("text", {}).get("content", "")
            # 尝试提取 URL
            import re
            urls = re.findall(r"https?://[^\s]+", content)
            for url in urls:
                parsed = urlparse(url)
                fname = parsed.path.split("/")[-1] or "download"
                if "." in fname:
                    return self._download_file(url, fname, meta={
                        "sender": sender,
                        "source": "text_link",
                    })
        return None

    def _download_file(self, url: str, filename: str, meta: Dict[str, Any]) -> Optional[SourceFile]:
        if not url:
            return None
        try:
            resp = requests.get(url, timeout=60, stream=True)
            resp.raise_for_status()
            data = resp.content
            if len(data) > self.max_size_mb * 1024 * 1024:
                logger.warning(f"[DingTalk] 下载文件超过限制: {filename}")
                return None
            return SourceFile(
                source_id=url,
                filename=filename,
                content_type=resp.headers.get("Content-Type", "application/octet-stream"),
                size=len(data),
                raw_bytes=data,
                meta=meta,
            )
        except Exception as e:
            logger.error(f"[DingTalk] 下载文件失败: {e}")
            return None

    def scan(self) -> List[SourceFile]:
        # 钉钉连接器为被动模式，不支持主动扫描
        return []

    def health_check(self) -> Dict[str, Any]:
        return {"healthy": True, "message": "webhook ready"}
