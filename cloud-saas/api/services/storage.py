"""
AeroSync 存储抽象层
支持：阿里云 OSS / MinIO (S3兼容)
根据 config.STORAGE_TYPE 自动切换
"""
import os
import uuid
import hashlib
import hmac
from datetime import datetime, timedelta
from typing import Optional, Tuple
from urllib.parse import urlencode, quote

from api.core.config import settings
from api.core.logging_config import get_logger

logger = get_logger(__name__)


class StorageProvider:
    """存储提供者抽象基类"""

    def generate_upload_url(
        self, filename: str, content_type: str, tenant_id: str = "default"
    ) -> Tuple[str, str, dict]:
        """
        生成上传 URL
        返回: (object_key, 上传url, 额外请求头)
        """
        raise NotImplementedError

    def generate_download_url(self, object_key: str, expires: int = 3600) -> str:
        """生成下载 URL"""
        raise NotImplementedError

    def get_object_key(self, task_id: str, filename: str) -> str:
        """统一 object_key 格式"""
        ext = os.path.splitext(filename)[1]
        return f"uploads/{task_id}{ext}"


class OSSProvider(StorageProvider):
    """阿里云 OSS 实现"""

    def generate_upload_url(
        self, filename: str, content_type: str, tenant_id: str = "default"
    ) -> Tuple[str, str, dict]:
        task_id = str(uuid.uuid4())
        object_key = self.get_object_key(task_id, filename)
        # 提示：此处保留原有逻辑，实际项目可接入 oss2 或预签名URL
        # 为保持兼容，返回一个标识位置
        upload_url = f"https://{settings.OSS_BUCKET}.{settings.OSS_ENDPOINT}/{object_key}"
        headers = {
            "Content-Type": content_type,
            "x-oss-meta-tenant-id": tenant_id,
        }
        return object_key, upload_url, headers

    def generate_download_url(self, object_key: str, expires: int = 3600) -> str:
        # 简化处理：直接返回公开访问链接（实际生产环境应使用预签名）
        return f"https://{settings.OSS_BUCKET}.{settings.OSS_ENDPOINT}/{object_key}"


class MinIOProvider(StorageProvider):
    """
    MinIO S3兼容实现
    使用预签名URL直传
    """

    def __init__(self):
        try:
            from minio import Minio
        except ImportError as e:
            raise ImportError(
                "私有化模式需要 minio 库，请安装: pip install minio"
            ) from e

        self.client = Minio(
            settings.MINIO_ENDPOINT,
            access_key=settings.MINIO_ACCESS_KEY,
            secret_key=settings.MINIO_SECRET_KEY,
            secure=settings.MINIO_SECURE,
        )
        self.bucket = settings.MINIO_BUCKET
        self._ensure_bucket()

    def _ensure_bucket(self):
        if not self.client.bucket_exists(self.bucket):
            self.client.make_bucket(self.bucket)
            logger.info(f"[MinIO] 创建 bucket: {self.bucket}")

    def generate_upload_url(
        self, filename: str, content_type: str, tenant_id: str = "default"
    ) -> Tuple[str, str, dict]:
        task_id = str(uuid.uuid4())
        object_key = self.get_object_key(task_id, filename)
        # MinIO 预签名 PUT URL
        upload_url = self.client.presigned_put_object(
            self.bucket, object_key, expires=timedelta(minutes=10)
        )
        headers = {
            "Content-Type": content_type,
            "x-amz-meta-tenant-id": tenant_id,
        }
        return object_key, upload_url, headers

    def generate_download_url(self, object_key: str, expires: int = 3600) -> str:
        return self.client.presigned_get_object(
            self.bucket, object_key, expires=timedelta(seconds=expires)
        )


# 全局实例
_storage_instance: Optional[StorageProvider] = None


def get_storage() -> StorageProvider:
    """获取当前配置的存储实例"""
    global _storage_instance
    if _storage_instance is not None:
        return _storage_instance

    storage_type = getattr(settings, "STORAGE_TYPE", "oss").lower()
    if storage_type == "minio":
        _storage_instance = MinIOProvider()
        logger.info("[Storage] 使用 MinIO 存储引擎")
    else:
        _storage_instance = OSSProvider()
        logger.info("[Storage] 使用 OSS 存储引擎")
    return _storage_instance
