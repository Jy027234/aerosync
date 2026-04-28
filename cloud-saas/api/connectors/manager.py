"""
连接器管理器
根据 TenantConfig.connectors 配置动态初始化并执行扫描
"""
from typing import Dict, Any, List, Optional
from sqlalchemy.orm import Session

from api.core.logging_config import get_logger
from api.core.database import SessionLocal
from api.models import FileTask, TenantConfig
from api.connectors.base import BaseConnector, SourceFile
from api.connectors.imap_connector import IMAPConnector
from api.connectors.smb_connector import SMBConnector
from api.connectors.dingtalk_connector import DingTalkConnector
from worker.celery_worker import process_file_task

logger = get_logger(__name__)

CONNECTOR_REGISTRY = {
    "imap": IMAPConnector,
    "smb": SMBConnector,
    "dingtalk": DingTalkConnector,
}


class ConnectorManager:
    """连接器管理器"""

    def __init__(self):
        self._connectors: Dict[str, BaseConnector] = {}

    def build_connectors(self, tenant_id: str, configs: List[Dict[str, Any]]) -> List[BaseConnector]:
        """根据配置列表构建连接器实例"""
        results = []
        for cfg in configs:
            ctype = cfg.get("type")
            if ctype not in CONNECTOR_REGISTRY:
                logger.warning(f"[ConnectorManager] 未知连接器类型: {ctype}")
                continue
            cls = CONNECTOR_REGISTRY[ctype]
            try:
                inst = cls(tenant_id, cfg)
                results.append(inst)
            except Exception as e:
                logger.error(f"[ConnectorManager] 创建连接器失败 {ctype}: {e}")
        return results

    def scan_tenant(self, tenant_id: str, db: Session) -> int:
        """
        扫描指定租户的所有连接器
        返回新创建任务数量
        """
        config = db.query(TenantConfig).filter(TenantConfig.tenant_id == tenant_id).first()
        if not config or not config.connectors:
            return 0

        connectors = self.build_connectors(tenant_id, config.connectors)
        created = 0
        for conn in connectors:
            try:
                files = conn.scan()
                for sf in files:
                    if self._task_exists(db, sf.source_id, tenant_id):
                        continue
                    task = FileTask(
                        tenant_id=tenant_id,
                        filename=sf.filename,
                        file_type=self._guess_ext(sf.filename),
                        object_key=sf.source_id,
                        status="pending",
                        source="connector",
                        meta={
                            "connector_type": conn.type,
                            "source_id": sf.source_id,
                            **(sf.meta or {}),
                        }
                    )
                    db.add(task)
                    db.commit()
                    db.refresh(task)
                    # 提交 Celery 任务
                    process_file_task.delay(task.id)
                    created += 1
                    logger.info(f"[ConnectorManager] 创建任务: {task.id} 来源={conn.type}")
            except Exception as e:
                logger.error(f"[ConnectorManager] 连接器扫描失败 {conn.type}: {e}")
        return created

    def scan_all(self) -> Dict[str, int]:
        """扫描所有租户，返回 {tenant_id: created_count}"""
        db = SessionLocal()
        try:
            tenants = db.query(TenantConfig).all()
            results = {}
            for t in tenants:
                if not t.connectors:
                    continue
                count = self.scan_tenant(t.tenant_id, db)
                if count:
                    results[t.tenant_id] = count
            return results
        finally:
            db.close()

    def _task_exists(self, db: Session, source_id: str, tenant_id: str) -> bool:
        return db.query(FileTask).filter(
            FileTask.tenant_id == tenant_id,
            FileTask.object_key == source_id,
        ).first() is not None

    def _guess_ext(self, filename: str) -> str:
        parts = filename.rsplit(".", 1)
        return parts[-1].lower() if len(parts) == 2 else "unknown"


# 全局管理器实例
connector_manager = ConnectorManager()
