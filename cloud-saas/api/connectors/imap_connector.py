"""
IMAP 邮件连接器
支持：轮询邮箱未读邮件，提取附件并创建任务
"""
import imaplib
import email
from email.header import decode_header
from typing import List, Dict, Any, Optional
from datetime import datetime

from api.connectors.base import BaseConnector, SourceFile
from api.core.logging_config import get_logger

logger = get_logger(__name__)


class IMAPConnector(BaseConnector):
    type = "imap"

    def __init__(self, tenant_id: str, config: Dict[str, Any]):
        super().__init__(tenant_id, config)
        self.host = config.get("host", "imap.qq.com")
        self.port = config.get("port", 993)
        self.username = config.get("username", "")
        self.password = config.get("password", "")
        self.folder = config.get("folder", "INBOX")
        self.use_ssl = config.get("use_ssl", True)
        self.seen_uids = set(config.get("seen_uids", []))
        self.max_size_mb = config.get("max_size_mb", 50)

    def _decode_str(self, s) -> str:
        if s is None:
            return ""
        dh = decode_header(s)
        parts = []
        for b, charset in dh:
            if isinstance(b, bytes):
                parts.append(b.decode(charset or "utf-8", errors="replace"))
            else:
                parts.append(str(b))
        return "".join(parts)

    def scan(self) -> List[SourceFile]:
        if not self.enabled:
            return []
        files = []
        try:
            cls = imaplib.IMAP4_SSL if self.use_ssl else imaplib.IMAP4
            with cls(self.host, self.port) as mail:
                mail.login(self.username, self.password)
                mail.select(self.folder)
                # 搜索未读邮件
                status, data = mail.search(None, "UNSEEN")
                if status != "OK":
                    return []
                uids = data[0].split()
                for uid in uids:
                    uid_str = uid.decode()
                    if uid_str in self.seen_uids:
                        continue
                    status, msg_data = mail.fetch(uid, "(RFC822)")
                    if status != "OK":
                        continue
                    raw = msg_data[0][1]
                    msg = email.message_from_bytes(raw)
                    subject = self._decode_str(msg.get("Subject", ""))
                    sender = self._decode_str(msg.get("From", ""))

                    for part in msg.walk():
                        if part.get_content_disposition() == "attachment":
                            filename = self._decode_str(part.get_filename() or "unknown")
                            payload = part.get_payload(decode=True)
                            size = len(payload) if payload else 0
                            if size > self.max_size_mb * 1024 * 1024:
                                logger.warning(f"[IMAP] 附件超过大小限制: {filename}")
                                continue
                            files.append(SourceFile(
                                source_id=f"{uid_str}/{filename}",
                                filename=filename,
                                content_type=part.get_content_type(),
                                size=size,
                                raw_bytes=payload,
                                meta={
                                    "subject": subject,
                                    "sender": sender,
                                    "received_at": datetime.utcnow().isoformat(),
                                    "imap_uid": uid_str,
                                }
                            ))
                    self.seen_uids.add(uid_str)
        except Exception as e:
            logger.error(f"[IMAP] 扫描失败: {e}")
        return files

    def health_check(self) -> Dict[str, Any]:
        try:
            cls = imaplib.IMAP4_SSL if self.use_ssl else imaplib.IMAP4
            with cls(self.host, self.port) as mail:
                mail.login(self.username, self.password)
                mail.logout()
            return {"healthy": True, "message": "login ok"}
        except Exception as e:
            return {"healthy": False, "message": str(e)}
