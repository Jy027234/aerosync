"""
AeroSync Cloud - SQLAlchemy 数据模型
"""
from sqlalchemy import Column, Integer, String, DateTime, JSON, Text, BigInteger
from api.core.database import Base
from datetime import datetime


class FileTask(Base):
    __tablename__ = "file_tasks"

    id = Column(Integer, primary_key=True, index=True)
    tenant_id = Column(String(64), index=True, nullable=False)
    filename = Column(String(255), nullable=False)
    object_key = Column(String(512), nullable=False, index=True)
    file_size = Column(BigInteger)
    file_type = Column(String(20))  # xlsx/pdf/docx

    # 处理状态: pending -> parsing -> analyzing -> ruling -> delivering -> delivered / failed
    status = Column(String(20), default="pending", index=True)
    status_message = Column(String(255))  # 当前步骤描述

    # 各阶段结果
    parsed_data = Column(JSON)      # 文档解析结果
    ai_result = Column(JSON)        # AI分析结果
    final_payload = Column(JSON)    # 最终推送Payload

    # Phase 3: HITL 人工审查
    hitl_status = Column(String(20), default="skipped")  # skipped / pending / approved / rejected / auto_approved
    hitl_reviewed_by = Column(String(64))   # 审核人
    hitl_reviewed_at = Column(DateTime)     # 审核时间
    hitl_comment = Column(Text)             # 审核备注
    hitl_modified_data = Column(JSON)       # 人工修改后的 AI 结果

    # 推送配置
    webhook_url = Column(String(512))
    webhook_secret = Column(String(255))
    webhook_response = Column(Text)  # Webhook返回内容

    # 错误追踪
    error_msg = Column(Text)
    retry_count = Column(Integer, default=0)

    # 时间戳
    created_at = Column(DateTime, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = Column(DateTime)

    def to_dict(self):
        """序列化为字典"""
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "filename": self.filename,
            "object_key": self.object_key,
            "file_size": self.file_size,
            "file_type": self.file_type,
            "status": self.status,
            "status_message": self.status_message,
            "parsed_data": self.parsed_data,
            "ai_result": self.ai_result,
            "final_payload": self.final_payload,
            "hitl_status": self.hitl_status,
            "hitl_reviewed_by": self.hitl_reviewed_by,
            "hitl_reviewed_at": self.hitl_reviewed_at.isoformat() if self.hitl_reviewed_at else None,
            "hitl_comment": self.hitl_comment,
            "error_msg": self.error_msg,
            "retry_count": self.retry_count,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }


class TenantConfig(Base):
    __tablename__ = "tenant_configs"

    id = Column(Integer, primary_key=True)
    tenant_id = Column(String(64), unique=True, index=True, nullable=False)
    name = Column(String(128))

    # Webhook 推送配置
    webhook_url = Column(String(512))
    webhook_secret = Column(String(255))

    # Phase 2: 规则引擎配置
    rules = Column(JSON, default=list)

    # Phase 2: 自定义Prompt（覆盖默认）
    custom_prompt = Column(Text)

    # Phase 3: HITL 人工审查配置
    hitl_config = Column(JSON, default=dict)

    # Phase 4: 管道可编排配置
    pipeline = Column(JSON, default=dict)  # {"template": "standard"} 或 {"stages": ["extract", "ai_analyze", "deliver"]}

    # Phase 8: 多源连接器配置
    connectors = Column(JSON, default=list)  # [{"type":"imap", "host":"...", ...}, {"type":"smb", ...}]

    # 状态
    enabled = Column(Integer, default=1)
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    def to_dict(self):
        return {
            "id": self.id,
            "tenant_id": self.tenant_id,
            "name": self.name,
            "webhook_url": self.webhook_url,
            "rules": self.rules,
            "custom_prompt": self.custom_prompt,
            "hitl_config": self.hitl_config,
            "pipeline": self.pipeline,
            "connectors": self.connectors,
            "enabled": self.enabled,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }