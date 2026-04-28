"""
AeroSync Cloud - 混合文档解析引擎
策略：基础解析器（快）+ Docling（准）自动路由
支持格式：PDF / Word / Excel / 图片
"""
import os
from pathlib import Path
from typing import Dict, Any, Optional

from api.core.config import settings
from api.core.logging_config import get_logger

logger = get_logger("hybrid_parser")


class DoclingExtractor:
    """
    Docling 封装器
    - 复杂版面分析
    - 扫描件 OCR
    - 表格结构化提取
    """

    def __init__(self):
        self._converter = None

    @property
    def converter(self):
        if self._converter is None:
            from docling.document_converter import DocumentConverter
            self._converter = DocumentConverter()
            logger.info("Docling DocumentConverter 初始化完成")
        return self._converter

    def extract(self, file_path: str, filename: str) -> Dict[str, Any]:
        """使用 Docling 解析文档，返回统一格式"""
        try:
            result = self.converter.convert(file_path)
            doc = result.document

            # 文本输出
            text = doc.export_to_text() or ""
            markdown = doc.export_to_markdown() or ""
            full_text = text if len(text) > len(markdown) * 0.5 else markdown

            # 页面数（PDF 有页，Word 可能没有）
            page_count = len(doc.pages) if hasattr(doc, "pages") else 0

            # 表格提取
            tables = []
            for idx, table in enumerate(doc.tables):
                try:
                    rows = self._extract_table_rows(table)
                    if rows:
                        tables.append({
                            "index": idx,
                            "rows": rows,
                        })
                except Exception as e:
                    logger.debug(f"表格 {idx} 提取异常: {e}")

            # 标题/大纲
            headings = []
            for item in doc.iterate_items():
                if hasattr(item, "label") and str(item.label).lower() in ("section_header", "header", "title"):
                    try:
                        headings.append(str(item.text))
                    except Exception:
                        pass

            doc_type = Path(filename).suffix.lower().replace(".", "")

            return {
                "type": doc_type,
                "filename": filename,
                "text": full_text,
                "structured": {
                    "page_count": page_count,
                    "extracted_tables": len(tables),
                    "paragraph_count": len([p for p in doc.texts]) if hasattr(doc, "texts") else 0,
                    "headings": headings[:20],
                    "docling_version": "2.x",
                },
                "tables": tables,
                "_parser": "docling",
            }

        except Exception as e:
            logger.error(f"Docling 解析失败: {filename}, 错误: {e}")
            raise

    def _extract_table_rows(self, table) -> list:
        """从 Docling TableItem 提取二维数组"""
        rows = []
        # Docling 2.x table 对象通常有 data / grid / cells 属性
        if hasattr(table, "data") and table.data:
            for row in table.data:
                row_data = []
                for cell in row:
                    text = str(cell) if cell is not None else ""
                    row_data.append(text)
                rows.append(row_data)
        elif hasattr(table, "grid") and table.grid:
            for row in table.grid:
                row_data = [str(cell.text) if hasattr(cell, "text") else str(cell) for cell in row]
                rows.append(row_data)
        elif hasattr(table, "cells") and table.cells:
            # 尝试按行重组
            cells = table.cells
            if not cells:
                return rows
            # 假设第一个单元格告诉我们有几列
            max_col = 0
            for cell in cells:
                if hasattr(cell, "col"):
                    max_col = max(max_col, cell.col)
            cols = max_col + 1 if max_col > 0 else len(cells)
            for i in range(0, len(cells), cols):
                row = cells[i:i + cols]
                rows.append([str(c.text) if hasattr(c, "text") else str(c) for c in row])
        return rows


class DocumentRouter:
    """根据文件特征选择最佳解析器"""

    def route_pdf(self, file_path: str, probe_result: Dict[str, Any]) -> str:
        """
        PDF 路由策略：
        1. 扫描件/图片PDF → docling（OCR）
        2. 大量表格 → docling（结构化更准）
        3. 普通文本PDF → pdfplumber（更快）
        """
        page_count = probe_result.get("structured", {}).get("page_count", 1)
        text = probe_result.get("text", "")
        avg_chars = len(text) / max(page_count, 1)
        table_count = probe_result.get("structured", {}).get("extracted_tables", 0)

        # 扫描件特征：每页平均字符少于 150
        if avg_chars < 150:
            logger.info(f"PDF 路由决策: 扫描件/OCR → docling (平均{avg_chars:.0f}字/页)")
            return "docling"

        # 复杂表格
        if table_count > 5:
            logger.info(f"PDF 路由决策: 复杂表格({table_count}个) → docling")
            return "docling"

        # 超大文件（>50MB）或超多页面（>100页），Docling 可能更稳
        file_size = os.path.getsize(file_path)
        if file_size > 50 * 1024 * 1024 or page_count > 100:
            logger.info(f"PDF 路由决策: 大文件({file_size/1024/1024:.1f}MB/{page_count}页) → docling")
            return "docling"

        return "pdfplumber"

    def route_word(self, file_path: str) -> str:
        """Word 路由：目前简单文档 python-docx 足够"""
        return "python-docx"

    def route_excel(self, file_path: str) -> str:
        """Excel 路由：pandas 更稳定"""
        return "pandas"


class HybridParser:
    """混合解析器统一入口"""

    def __init__(self):
        self.router = DocumentRouter()
        self._docling: Optional[DoclingExtractor] = None

    @property
    def docling(self) -> DoclingExtractor:
        if self._docling is None:
            self._docling = DoclingExtractor()
        return self._docling

    def parse(self, file_path: str, filename: str) -> Dict[str, Any]:
        """
        主解析入口
        - 根据文件类型和特征自动选择解析器
        - 始终返回统一格式
        """
        suffix = Path(filename).suffix.lower()

        if suffix in [".xlsx", ".xls"]:
            from api.services.parser import parse_excel
            return parse_excel(file_path, filename)

        elif suffix == ".pdf":
            from api.services.parser import parse_pdf
            probe = parse_pdf(file_path, filename)
            route = self.router.route_pdf(file_path, probe)

            if route == "docling":
                try:
                    return self.docling.extract(file_path, filename)
                except Exception as e:
                    logger.warning(f"Docling 解析失败，回退到 pdfplumber: {e}")
                    return probe
            return probe

        elif suffix in [".doc", ".docx"]:
            from api.services.parser import parse_word
            return parse_word(file_path, filename)

        else:
            raise ValueError(f"不支持的文件类型: {suffix}")


# 全局实例（懒加载）
_hybrid_parser: Optional[HybridParser] = None


def get_hybrid_parser() -> HybridParser:
    """获取混合解析器实例"""
    global _hybrid_parser
    if _hybrid_parser is None:
        _hybrid_parser = HybridParser()
    return _hybrid_parser


def parse_with_hybrid(file_path: str, filename: str) -> Dict[str, Any]:
    """便捷函数：使用混合解析器解析文件"""
    return get_hybrid_parser().parse(file_path, filename)
