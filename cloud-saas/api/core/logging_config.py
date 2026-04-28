"""
AeroSync Cloud - 结构化日志配置
为每个服务提供统一的日志格式
"""
import logging
import sys


def setup_logging(level: str = "INFO") -> None:
    """配置全局结构化日志"""
    log_format = (
        "%(asctime)s | %(levelname)-8s | %(name)-20s | %(message)s"
    )
    logging.basicConfig(
        level=getattr(logging, level.upper(), logging.INFO),
        format=log_format,
        handlers=[logging.StreamHandler(sys.stdout)],
        force=True,
    )


def get_logger(name: str) -> logging.Logger:
    """获取命名日志器"""
    return logging.getLogger(name)
