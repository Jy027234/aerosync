"""
AeroSync Cloud - 全局配置
支持环境变量覆盖，优先级：环境变量 > 默认值
"""
import os
from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # --- 数据库 ---
    DATABASE_URL: str = "postgresql://aerosync:aerosync@localhost:5432/aerosync"
    REDIS_URL: str = "redis://localhost:6379/0"

    # --- 存储配置 ---
    STORAGE_TYPE: str = "oss"  # oss | minio

    # --- 对象存储 (OSS) ---
    OSS_ACCESS_KEY_ID: str = ""
    OSS_ACCESS_KEY_SECRET: str = ""
    OSS_ENDPOINT: str = "oss-cn-shanghai.aliyuncs.com"
    OSS_BUCKET: str = "aerosync-dev"

    # --- 对象存储 (MinIO - 私有化) ---
    MINIO_ENDPOINT: str = "minio:9000"
    MINIO_ACCESS_KEY: str = "minioadmin"
    MINIO_SECRET_KEY: str = "minioadmin"
    MINIO_BUCKET: str = "aerosync"
    MINIO_SECURE: bool = False

    # --- 大语言模型 (通用配置，向后兼容) ---
    LLM_API_KEY: str = ""
    LLM_BASE_URL: str = "https://api.openai.com/v1"
    LLM_MODEL: str = "gpt-4o"

    # --- 多 Provider LLM 配置 ---
    # provider: openai | claude | deepseek | ollama
    LLM_PROVIDER: str = "openai"

    # Claude (Anthropic)
    CLAUDE_API_KEY: str = ""
    CLAUDE_BASE_URL: str = "https://api.anthropic.com/v1"
    CLAUDE_MODEL: str = "claude-3-5-sonnet-20241022"

    # DeepSeek
    DEEPSEEK_API_KEY: str = ""
    DEEPSEEK_BASE_URL: str = "https://api.deepseek.com/v1"
    DEEPSEEK_MODEL: str = "deepseek-chat"

    # Ollama (本地部署)
    OLLAMA_BASE_URL: str = "http://localhost:11434/v1"
    OLLAMA_MODEL: str = "qwen2.5:14b"

    # --- Webhook 默认配置 ---
    DEFAULT_WEBHOOK_URL: str = ""
    DEFAULT_WEBHOOK_SECRET: str = ""

    # --- 混合解析引擎 ---
    ENABLE_HYBRID_PARSER: bool = False

    # --- API 安全 ---
    API_TOKEN: str = "dev-token-change-me"

    class Config:
        env_file = ".env"
        extra = "ignore"


@lru_cache()
def get_settings() -> Settings:
    """全局单例配置"""
    return Settings()


settings = get_settings()
