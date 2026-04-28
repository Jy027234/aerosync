"""
AeroSync Cloud - HITL (人在环路) 审查服务
支持触发条件判断、通知、超时自动通过
"""
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List

from api.core.logging_config import get_logger

logger = get_logger("hitl")


# 默认 HITL 配置
DEFAULT_HITL_CONFIG = {
    "enabled": False,
    "min_confidence": 0.8,
    "trigger_tags": [],
    "trigger_keywords": [],
    "timeout_hours": 24,
    "notify_channels": ["web"],  # web / dingtalk / email
    "dingtalk_webhook": "",
}


class HITLService:
    """HITL 审查服务"""

    def __init__(self, config: Optional[Dict[str, Any]] = None):
        self.cfg = {**DEFAULT_HITL_CONFIG, **(config or {})}

    def should_trigger(self, ai_result: Dict[str, Any]) -> bool:
        """
        判断是否触发人工审查
        触发条件（任意满足一条即触发）：
        1. 置信度低于 min_confidence
        2. AI 标签包含 trigger_tags 中任意标签
        3. 摘要包含 trigger_keywords 中任意关键词
        """
        if not self.cfg.get("enabled"):
            return False

        confidence = ai_result.get("confidence", 0.0)
        tags = ai_result.get("tags", [])
        summary = ai_result.get("summary", "")

        # 条件 1: 置信度低
        if confidence < self.cfg.get("min_confidence", 0.8):
            logger.info(f"HITL 触发: 置信度 {confidence} < {self.cfg.get('min_confidence')}")
            return True

        # 条件 2: 标签触发
        trigger_tags = self.cfg.get("trigger_tags", [])
        if trigger_tags and any(t in tags for t in trigger_tags):
            matched = [t for t in tags if t in trigger_tags]
            logger.info(f"HITL 触发: 标签匹配 {matched}")
            return True

        # 条件 3: 关键词触发
        trigger_keywords = self.cfg.get("trigger_keywords", [])
        summary_lower = summary.lower()
        if trigger_keywords and any(kw.lower() in summary_lower for kw in trigger_keywords):
            matched = [kw for kw in trigger_keywords if kw.lower() in summary_lower]
            logger.info(f"HITL 触发: 关键词匹配 {matched}")
            return True

        return False

    def build_review_payload(self, task_id: int, filename: str, ai_result: Dict[str, Any], raw_text_preview: str = "") -> Dict[str, Any]:
        """构建审核页面所需的数据"""
        return {
            "task_id": task_id,
            "filename": filename,
            "suggested_tags": ai_result.get("tags", []),
            "suggested_doc_type": ai_result.get("doc_type", "未知"),
            "suggested_summary": ai_result.get("summary", ""),
            "structured_data": ai_result.get("structured_data", {}),
            "confidence": ai_result.get("confidence", 0),
            "priority": ai_result.get("priority", "normal"),
            "raw_preview": raw_text_preview[:1000],
            "review_url": f"/admin/tasks/{task_id}/hitl",  # 管理后台审核链接
        }

    def notify(self, payload: Dict[str, Any]) -> None:
        """发送审核通知（简单打印日志，实际可扩展为钉钉/邮件）"""
        channels = self.cfg.get("notify_channels", ["web"])

        if "web" in channels:
            logger.info(f"[HITL Web 通知] 任务 {payload['task_id']} 待审核: {payload['filename']}")

        if "dingtalk" in channels:
            self._notify_dingtalk(payload)

        if "email" in channels:
            self._notify_email(payload)

    def _notify_dingtalk(self, payload: Dict[str, Any]) -> None:
        """钉钉群机器人通知（简单 markdown 模式）"""
        webhook = self.cfg.get("dingtalk_webhook", "")
        if not webhook:
            logger.warning("HITL 钉钉通知未配置 webhook")
            return

        try:
            import requests
            msg = {
                "msgtype": "markdown",
                "markdown": {
                    "title": "航空文档待审核",
                    "text": f"### 航空文档待审核\n\n"
                            f"- 任务ID: {payload['task_id']}\n"
                            f"- 文件名: {payload['filename']}\n"
                            f"- AI 类型: {payload['suggested_doc_type']}\n"
                            f"- AI 标签: {', '.join(payload['suggested_tags'])}\n"
                            f"- 置信度: {payload['confidence']}\n"
                            f"- 摘要: {payload['suggested_summary'][:100]}...\n\n"
                            f"请登录管理后台确认或修改标签。"
                }
            }
            requests.post(webhook, json=msg, timeout=10)
            logger.info(f"[HITL 钉钉通知] 已发送: 任务 {payload['task_id']}")
        except Exception as e:
            logger.error(f"[HITL 钉钉通知] 发送失败: {e}")

    def _notify_email(self, payload: Dict[str, Any]) -> None:
        """邮件通知（占位，可接入邮件服务）"""
        logger.info(f"[HITL 邮件通知] 占位: 任务 {payload['task_id']} 待审核")

    def check_timeout(self, task_created_at: datetime) -> bool:
        """检查是否已超时"""
        timeout_hours = self.cfg.get("timeout_hours", 24)
        deadline = task_created_at + timedelta(hours=timeout_hours)
        return datetime.utcnow() > deadline
