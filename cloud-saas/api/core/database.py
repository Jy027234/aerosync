"""
AeroSync Cloud - 数据库连接管理
SQLAlchemy + PostgreSQL
"""
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base, Session
from api.core.config import settings

# 连接池配置
echo = os.getenv("SQL_ECHO", "false").lower() == "true"
engine = create_engine(
    settings.DATABASE_URL,
    pool_size=10,
    max_overflow=20,
    pool_pre_ping=True,
    echo=echo
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """FastAPI Depends 依赖注入"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_db_session() -> Session:
    """Worker 中直接获取数据库会话"""
    return SessionLocal()