"""
文档解析服务测试
mock OSS 下载，测试 Excel/PDF/Word 解析逻辑
"""
import os
import sys
from unittest.mock import MagicMock, patch
import pytest

# 确保 conftest 的 mock 已生效
from api.services import parser


class TestParseExcel:
    """Excel 解析测试"""

    def test_parse_excel(self):
        """真实解析 sample.xlsx"""
        sample_path = os.path.join(os.path.dirname(__file__), "sample.xlsx")
        result = parser.parse_excel(sample_path, "sample.xlsx")

        assert result["type"] == "excel"
        assert result["filename"] == "sample.xlsx"
        assert "Sheet1" in result["text"]
        assert result["structured"]["sheet_count"] == 1
        assert len(result["tables"]) == 1
        # 验证表头
        assert result["structured"]["headers"] == ["Part Number", "Aircraft Model", "Quantity"]

    def test_parse_excel_mock_download(self, monkeypatch):
        """mock download_from_oss 测试 parse_document 入口"""
        sample_path = os.path.join(os.path.dirname(__file__), "sample.xlsx")

        def mock_download(object_key, local_path):
            # 拷贝本地文件到临时路径
            import shutil
            shutil.copy(sample_path, local_path)
            return True

        monkeypatch.setattr(parser, "download_from_oss", mock_download)

        result = parser.parse_document("uploads/tenant-test/sample.xlsx", "sample.xlsx")
        assert result["type"] == "excel"
        assert result["structured"]["sheet_count"] == 1


class TestParsePdf:
    """PDF 解析测试（mock pdfplumber）"""

    def test_parse_pdf_mock(self, monkeypatch):
        """mock pdfplumber 解析 PDF"""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "Page 1 text\nA320 Part Number: BACB30FM8A4"
        mock_page.extract_tables.return_value = [
            [["Header1", "Header2"], ["Value1", "Value2"]]
        ]

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr(parser.pdfplumber, "open", lambda path: mock_pdf)

        result = parser.parse_pdf("/tmp/fake.pdf", "sample.pdf")
        assert result["type"] == "pdf"
        assert "Page 1 text" in result["text"]
        assert result["structured"]["page_count"] == 1
        assert result["structured"]["extracted_tables"] == 1
        assert len(result["tables"]) == 1

    def test_parse_pdf_mock_download(self, monkeypatch):
        """mock OSS 下载 + mock pdfplumber 测试 parse_document 入口"""
        mock_page = MagicMock()
        mock_page.extract_text.return_value = "ATA 32"
        mock_page.extract_tables.return_value = []

        mock_pdf = MagicMock()
        mock_pdf.pages = [mock_page]
        mock_pdf.__enter__ = MagicMock(return_value=mock_pdf)
        mock_pdf.__exit__ = MagicMock(return_value=False)

        monkeypatch.setattr(parser.pdfplumber, "open", lambda path: mock_pdf)
        monkeypatch.setattr(parser, "download_from_oss", lambda ok, lp: True)

        result = parser.parse_document("uploads/tenant-test/sample.pdf", "sample.pdf")
        assert result["type"] == "pdf"
        assert "ATA 32" in result["text"]


class TestParseWord:
    """Word 解析测试（mock python-docx）"""

    def test_parse_word_mock(self, monkeypatch):
        """mock python-docx 解析 Word"""
        mock_para1 = MagicMock()
        mock_para1.text = "Hello World"
        mock_para2 = MagicMock()
        mock_para2.text = "   "
        mock_para3 = MagicMock()
        mock_para3.text = "A320 maintenance"

        mock_cell1 = MagicMock()
        mock_cell1.text = "Cell1"
        mock_cell2 = MagicMock()
        mock_cell2.text = "Cell2"
        mock_row = MagicMock()
        mock_row.cells = [mock_cell1, mock_cell2]

        mock_table = MagicMock()
        mock_table.rows = [mock_row]

        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para1, mock_para2, mock_para3]
        mock_doc.tables = [mock_table]

        monkeypatch.setattr(parser, "Document", lambda path: mock_doc)

        result = parser.parse_word("/tmp/fake.docx", "sample.docx")
        assert result["type"] == "word"
        assert "Hello World" in result["text"]
        assert "A320 maintenance" in result["text"]
        assert result["structured"]["paragraph_count"] == 2
        assert result["structured"]["table_count"] == 1
        assert len(result["tables"]) == 1

    def test_parse_word_mock_download(self, monkeypatch):
        """mock OSS 下载 + mock docx 测试 parse_document 入口"""
        mock_para = MagicMock()
        mock_para.text = "Landing gear part"

        mock_doc = MagicMock()
        mock_doc.paragraphs = [mock_para]
        mock_doc.tables = []

        monkeypatch.setattr(parser, "Document", lambda path: mock_doc)
        monkeypatch.setattr(parser, "download_from_oss", lambda ok, lp: True)

        result = parser.parse_document("uploads/tenant-test/sample.docx", "sample.docx")
        assert result["type"] == "word"
        assert "Landing gear part" in result["text"]


class TestExtractAviationEntities:
    """航空领域实体提取测试"""

    def test_extract_part_numbers(self):
        text = "件号：BACB30FM8A4"
        entities = parser.extract_aviation_entities(text)
        assert "BACB30FM8A4" in entities["part_numbers"]

    def test_extract_aircraft_models(self):
        text = "适用机型 B737-800 和 A320neo"
        entities = parser.extract_aviation_entities(text)
        assert "B737-800" in entities["aircraft_models"]
        assert "A320" in entities["aircraft_models"]

    def test_extract_ata(self):
        text = "ATA 32 起落架检修"
        entities = parser.extract_aviation_entities(text)
        assert "32" in entities["ata_chapters"]
        assert entities["landing_gear_marked"] is True

    def test_extract_quantities(self):
        text = "数量：10 EA"
        entities = parser.extract_aviation_entities(text)
        assert "10" in entities["quantities"]

    def test_pma_marked(self):
        text = "这是一个 PMA 件"
        entities = parser.extract_aviation_entities(text)
        assert entities["pma_marked"] is True
