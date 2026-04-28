"""
AeroSync Cloud - Pytest 全局配置
"""
import sys
import os
from unittest.mock import MagicMock

# 将项目根目录加入 sys.path，确保 `import api.xxx` 正常工作
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, PROJECT_ROOT)

# =====================================================================
# Mock 缺失的外部依赖（网络/二进制库未安装时）
# =====================================================================
MISSING_MODULES = [
    "oss2",
    "pdfplumber",
    "docx",
    "openai",
    "celery",
    "redis",
    "kombu",
    "vine",
    "billiard",
    "click_didyoumean",
    "click_repl",
    "click_plugins",
    "amqp",
]

for mod_name in MISSING_MODULES:
    sys.modules[mod_name] = MagicMock()

# 让 oss2.Auth / oss2.Bucket 可被正常实例化
_oss_mock = sys.modules["oss2"]
_oss_mock.Auth = MagicMock
_oss_mock.Bucket = MagicMock

# 让 celery.Celery 可被正常实例化
_celery_mock = sys.modules["celery"]

def _fake_celery(*args, **kwargs):
    app = MagicMock()
    app.conf = MagicMock()
    app.conf.update = MagicMock()

    def _task_decorator(*t_args, **t_kwargs):
        def _wrapper(func):
            func.delay = MagicMock()
            return func
        return _wrapper

    app.task = _task_decorator
    return app

_celery_mock.Celery = _fake_celery

# 让 openai.OpenAI 可被正常实例化
_openai_mock = sys.modules["openai"]
_openai_mock.OpenAI = MagicMock
_openai_mock.APIError = Exception

# =====================================================================
# Patch sqlalchemy.create_engine 以适配 SQLite（移除 PostgreSQL 专有参数）
# =====================================================================
import sqlalchemy
_original_create_engine = sqlalchemy.create_engine

def _patched_create_engine(url, **kwargs):
    if str(url).startswith("sqlite"):
        # SQLite 不支持这些参数
        kwargs.pop("pool_size", None)
        kwargs.pop("max_overflow", None)
        kwargs.pop("pool_pre_ping", None)
    return _original_create_engine(url, **kwargs)

sqlalchemy.create_engine = _patched_create_engine

# =====================================================================
# 数据库配置：使用内存 SQLite 替代 PostgreSQL
# =====================================================================
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ["REDIS_URL"] = "redis://localhost:6379/0"
os.environ["API_TOKEN"] = "test-token"
os.environ["OSS_ACCESS_KEY_ID"] = "test-ak"
os.environ["OSS_ACCESS_KEY_SECRET"] = "test-sk"
os.environ["OSS_ENDPOINT"] = "oss-test.aliyuncs.com"
os.environ["OSS_BUCKET"] = "test-bucket"
os.environ["LLM_API_KEY"] = "test-llm-key"
os.environ["LLM_BASE_URL"] = "https://test.example.com/v1"
os.environ["LLM_MODEL"] = "gpt-test"

# =====================================================================
# Pytest fixtures
# =====================================================================
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

from api.core.database import Base, get_db
from api.models import FileTask, TenantConfig


@pytest.fixture(scope="session")
def engine():
    """创建内存 SQLite 引擎"""
    _engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        pool_pre_ping=True,
    )
    Base.metadata.create_all(bind=_engine)
    return _engine


@pytest.fixture(scope="function")
def db_session(engine):
    """每个测试函数独立的 DB Session"""
    connection = engine.connect()
    transaction = connection.begin()
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=connection)
    session = SessionLocal()

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture(scope="function")
def client(db_session):
    """FastAPI TestClient，注入内存数据库"""
    from fastapi.testclient import TestClient
    from api.main import app

    def override_get_db():
        try:
            yield db_session
        finally:
            pass

    app.dependency_overrides[get_db] = override_get_db

    with TestClient(app) as c:
        yield c

    app.dependency_overrides.clear()


@pytest.fixture
def auth_headers():
    """默认鉴权 Header"""
    return {
        "Authorization": "Bearer test-token",
        "X-Tenant-Id": "tenant-test",
    }
