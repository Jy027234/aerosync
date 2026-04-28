"""
AeroSync Cloud - 可编排处理管道 (Pipeline Orchestration)
支持租户级自定义处理阶段组合
"""
from datetime import datetime
from typing import Dict, Any, List, Optional

from sqlalchemy.orm import Session

from api.core.config import settings
from api.core.logging_config import get_logger
from api.models import FileTask, TenantConfig

logger = get_logger("pipeline")

# 预置管道模板
PIPELINE_TEMPLATES = {
    "minimal": {
        "name": "极简模式",
        "description": "仅解析文档并原样推送，不进行AI分析",
        "stages": ["extract", "deliver"],
    },
    "standard": {
        "name": "标准模式",
        "description": "解析 → AI分析 → 规则引擎 → 推送",
        "stages": ["extract", "ai_analyze", "rule_tag", "deliver"],
    },
    "aviation_strict": {
        "name": "航空严格模式",
        "description": "完整流程，含数据验证、HITL人工审查",
        "stages": ["extract", "validate", "ai_analyze", "hitl_review", "rule_tag", "deliver"],
    },
    "lightweight": {
        "name": "轻量模式",
        "description": "解析 → 推送（无AI、无规则）",
        "stages": ["extract", "deliver"],
    },
}

DEFAULT_PIPELINE = {"template": "standard", "stages": PIPELINE_TEMPLATES["standard"]["stages"]}


class PipelineRunner:
    """
    管道执行器
    根据租户配置的 stage 列表逐步执行
    """

    def __init__(self, db: Session, task: FileTask, cfg: TenantConfig):
        self.db = db
        self.task = task
        self.cfg = cfg
        self.pipeline = self._resolve_pipeline()
        self.context: Dict[str, Any] = {}  # 各 stage 共享上下文

    def _resolve_pipeline(self) -> List[str]:
        """解析管道配置：支持模板名或自定义 stage 列表"""
        raw = self.cfg.pipeline if self.cfg and self.cfg.pipeline else {}
        if isinstance(raw, str):
            # 简写：直接指定模板名
            tmpl = PIPELINE_TEMPLATES.get(raw, PIPELINE_TEMPLATES["standard"])
            return tmpl["stages"]
        if isinstance(raw, dict):
            if "template" in raw and raw["template"] in PIPELINE_TEMPLATES:
                tmpl = PIPELINE_TEMPLATES[raw["template"]]
                stages = tmpl["stages"].copy()
                # 支持在模板基础上增减 stage
                if "add" in raw:
                    for s in raw["add"]:
                        if s not in stages:
                            stages.append(s)
                if "remove" in raw:
                    stages = [s for s in stages if s not in raw["remove"]]
                return stages
            if "stages" in raw:
                return raw["stages"]
        return DEFAULT_PIPELINE["stages"]

    def run(self) -> bool:
        """
        执行管道
        Returns:
            True: 管道完成
            False: 管道暂停（HITL 等待人工确认）
        """
        stages = self.pipeline
        logger.info(f"[Task {self.task.id}] 开始执行管道: {stages}")

        for stage in stages:
            logger.info(f"[Task {self.task.id}] Stage: {stage}")
            handler = getattr(self, f"_stage_{stage}", None)
            if not handler:
                logger.warning(f"[Task {self.task.id}] 未知 stage: {stage}，跳过")
                continue

            result = handler()
            if result == "PAUSE":
                logger.info(f"[Task {self.task.id}] 管道暂停在 stage: {stage}")
                return False

        logger.info(f"[Task {self.task.id}] 管道执行完成")
        return True

    # ===== 各 Stage 实现 =====

    def _stage_extract(self):
        """文档解析"""
        self._update_status("parsing", "正在解析文档...")

        from api.services.parser import parse_document, extract_aviation_entities
        parsed = parse_document(self.task.object_key, self.task.filename)

        entities = extract_aviation_entities(parsed.get("text", ""))
        parsed["aviation_entities"] = entities

        self.task.parsed_data = parsed
        self.db.commit()
        self.context["parsed"] = parsed
        logger.info(f"[Task {self.task.id}] 解析完成")

    def _stage_validate(self):
        """数据验证（简单版）"""
        self._update_status("validating", "数据验证中...")
        parsed = self.context.get("parsed") or self.task.parsed_data or {}
        text = parsed.get("text", "")

        # 简单校验：空文件检测
        if not text or len(text.strip()) < 10:
            raise ValueError("文档内容为空或过少，可能是扫描件或无法识别的文件")

        logger.info(f"[Task {self.task.id}] 验证通过")

    def _stage_ai_analyze(self):
        """AI 智能分析"""
        self._update_status("analyzing", "AI 分析中...")

        parsed = self.context.get("parsed") or self.task.parsed_data or {}
        from api.services.analyzer import AIAnalyzer
        custom_prompt = self.cfg.custom_prompt if self.cfg and self.cfg.custom_prompt else None
        analyzer = AIAnalyzer(custom_prompt=custom_prompt)
        ai_result = analyzer.analyze(parsed, self.task.filename)

        self.task.ai_result = ai_result
        self.db.commit()
        self.context["ai_result"] = ai_result
        logger.info(f"[Task {self.task.id}] AI分析完成")

    def _stage_hitl_review(self):
        """人工审查：若触发则返回 PAUSE"""
        hitl_cfg = self.cfg.hitl_config if self.cfg and self.cfg.hitl_config else {}
        if not hitl_cfg.get("enabled"):
            return

        ai_result = self.context.get("ai_result") or self.task.ai_result or {}
        from api.services.hitl_service import HITLService
        hitl = HITLService(hitl_cfg)

        if hitl.should_trigger(ai_result):
            self._update_status("hitl_review", "待人工审核...")
            self.task.hitl_status = "pending"
            self.db.commit()

            parsed = self.context.get("parsed") or self.task.parsed_data or {}
            review_payload = hitl.build_review_payload(
                task_id=self.task.id,
                filename=self.task.filename,
                ai_result=ai_result,
                raw_text_preview=parsed.get("text", "")
            )
            hitl.notify(review_payload)
            logger.info(f"[Task {self.task.id}] HITL 审查已触发，暂停管道")
            return "PAUSE"

    def _stage_rule_tag(self):
        """规则引擎修正"""
        if not (self.cfg and self.cfg.rules):
            return

        self._update_status("ruling", "规则引擎处理中...")
        ai_result = self.context.get("ai_result") or self.task.ai_result or {}
        parsed = self.context.get("parsed") or self.task.parsed_data or {}

        from api.services.ruler import RuleEngine
        engine = RuleEngine(self.cfg.rules)
        ai_result = engine.apply(ai_result, parsed)
        self.task.ai_result = ai_result
        self.db.commit()
        self.context["ai_result"] = ai_result
        logger.info(f"[Task {self.task.id}] 规则引擎应用完成")

    def _stage_deliver(self):
        """组装 Payload 并 Webhook 推送"""
        ai_result = self.context.get("ai_result") or self.task.ai_result or {}
        parsed = self.context.get("parsed") or self.task.parsed_data or {}

        payload = {
            "task_id": self.task.id,
            "tenant_id": self.task.tenant_id,
            "filename": self.task.filename,
            "file_type": self.task.file_type,
            "doc_type": ai_result.get("doc_type", "未知"),
            "summary": ai_result.get("summary", ""),
            "tags": ai_result.get("tags", []),
            "priority": ai_result.get("priority", "normal"),
            "confidence": ai_result.get("confidence", 0),
            "structured_data": ai_result.get("structured_data", {}),
            "aviation_entities": parsed.get("aviation_entities", {}),
            "raw_preview": parsed.get("text", "")[:2000],
            "hitl_status": self.task.hitl_status,
            "processed_at": datetime.utcnow().isoformat(),
        }
        self.task.final_payload = payload
        self.db.commit()

        webhook_url = self.task.webhook_url or settings.DEFAULT_WEBHOOK_URL
        if webhook_url:
            self._update_status("delivering", "Webhook 推送中...")
            from api.services.webhook import WebhookPusher
            pusher = WebhookPusher(
                url=webhook_url,
                secret=self.task.webhook_secret or settings.DEFAULT_WEBHOOK_SECRET,
                max_retries=3
            )
            push_result = pusher.send(payload)
            self.task.webhook_response = __import__('json').dumps(push_result, ensure_ascii=False)[:2000]

            if push_result["success"]:
                self._update_status("delivered", "推送成功", completed=True)
                logger.info(f"[Task {self.task.id}] Webhook 推送成功")
            else:
                raise Exception(f"Webhook delivery failed: {push_result.get('error')}")
        else:
            self._update_status("delivered", "处理完成（未配置Webhook）", completed=True)
            logger.info(f"[Task {self.task.id}] 处理完成（无Webhook配置）")

    def _update_status(self, status: str, message: str, completed: bool = False):
        """更新任务状态"""
        self.task.status = status
        self.task.status_message = message
        self.task.updated_at = datetime.utcnow()
        if completed:
            self.task.completed_at = datetime.utcnow()
        self.db.commit()


def run_pipeline(db: Session, task: FileTask, cfg: TenantConfig) -> bool:
    """便捷函数：执行管道
    Returns: True=完成, False=暂停（HITL）
    """
    runner = PipelineRunner(db, task, cfg)
    return runner.run()
