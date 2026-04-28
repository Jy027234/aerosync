"""
多源连接器抽象基类
所有连接器必须继承此基类并实现 scan() / 唯一标识 type
"""
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from dataclasses import dataclass


@dataclass
class SourceFile:
    """连接器发现的源文件"""
    source_id: str           # 连接器内部 ID（如邮件 UID、文件路径）
    filename: str
    content_type: str
    size: int
    raw_bytes: Optional[bytes] = None
    meta: Dict[str, Any] = None   # 额外元数据


class BaseConnector(ABC):
    """连接器抽象基类"""

    type: str = "base"       # 子类覆盖

    def __init__(self, tenant_id: str, config: Dict[str, Any]):
        self.tenant_id = tenant_id
        self.config = config
        self.enabled = config.get("enabled", True)

    @abstractmethod
    def scan(self) -> List[SourceFile]:
        """
        扫描源并返回发现的文件列表
        """
        raise NotImplementedError

    def health_check(self) -> Dict[str, Any]:
        """健康检查，返回 { healthy: bool, message: str }"""
        return {"healthy": True, "message": "ok"}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.type,
            "tenant_id": self.tenant_id,
            "enabled": self.enabled,
            "config": self.config,
        }
