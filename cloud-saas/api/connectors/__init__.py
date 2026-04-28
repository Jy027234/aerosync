"""
AeroSync 多源连接器模块
"""
from api.connectors.base import BaseConnector, SourceFile
from api.connectors.imap_connector import IMAPConnector
from api.connectors.smb_connector import SMBConnector
from api.connectors.dingtalk_connector import DingTalkConnector
from api.connectors.manager import ConnectorManager, connector_manager

__all__ = [
    "BaseConnector",
    "SourceFile",
    "IMAPConnector",
    "SMBConnector",
    "DingTalkConnector",
    "ConnectorManager",
    "connector_manager",
]
