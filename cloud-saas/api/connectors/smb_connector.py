"""
SMB/NFS 网盘连接器（挂载监控模式）
设计假设：SMB/NFS 已通过操作系统挂载到本地路径
通过扫描挂载点的新增/修改文件实现
"""
import os
from pathlib import Path
from typing import List, Dict, Any
from datetime import datetime, timezone

from api.connectors.base import BaseConnector, SourceFile
from api.core.logging_config import get_logger

logger = get_logger(__name__)


class SMBConnector(BaseConnector):
    type = "smb"

    def __init__(self, tenant_id: str, config: Dict[str, Any]):
        super().__init__(tenant_id, config)
        self.mount_path = config.get("mount_path", "/mnt/shared")
        self.pattern = config.get("pattern", "*")   # 文件匹配模式，如 *.pdf
        self.check_interval = config.get("check_interval", 300)
        self.seen_files = set(config.get("seen_files", []))
        self.max_size_mb = config.get("max_size_mb", 100)

    def scan(self) -> List[SourceFile]:
        if not self.enabled:
            return []
        files = []
        root = Path(self.mount_path)
        if not root.exists():
            logger.warning(f"[SMB] 挂载路径不存在: {self.mount_path}")
            return []

        try:
            for fp in root.rglob(self.pattern):
                if not fp.is_file():
                    continue
                rel = str(fp.relative_to(root))
                mtime = fp.stat().st_mtime
                file_id = f"{rel}:{mtime}"
                if file_id in self.seen_files:
                    continue
                size = fp.stat().st_size
                if size > self.max_size_mb * 1024 * 1024:
                    logger.warning(f"[SMB] 文件超过大小限制: {rel}")
                    continue
                files.append(SourceFile(
                    source_id=file_id,
                    filename=fp.name,
                    content_type="application/octet-stream",
                    size=size,
                    raw_bytes=fp.read_bytes(),
                    meta={
                        "relative_path": rel,
                        "mtime": datetime.fromtimestamp(mtime, tz=timezone.utc).isoformat(),
                        "mount_path": self.mount_path,
                    }
                ))
                self.seen_files.add(file_id)
        except Exception as e:
            logger.error(f"[SMB] 扫描失败: {e}")
        return files

    def health_check(self) -> Dict[str, Any]:
        root = Path(self.mount_path)
        if not root.exists():
            return {"healthy": False, "message": f"mount path not found: {self.mount_path}"}
        if not os.access(root, os.R_OK):
            return {"healthy": False, "message": "mount path not readable"}
        return {"healthy": True, "message": f"{len(list(root.iterdir()))} items in root"}
