"""
规则引擎测试
"""
import pytest
from api.services.ruler import RuleEngine


class TestKeywordTag:
    """keyword_tag 规则测试"""

    def test_add_tag(self):
        engine = RuleEngine([{
            "type": "keyword_tag",
            "keywords": ["紧急", "urgent"],
            "tag": "紧急",
            "enabled": True,
        }])
        result = engine.apply({"tags": []}, {"text": "这是一个紧急工单"})
        assert "紧急" in result["tags"]

    def test_force_tag(self):
        engine = RuleEngine([{
            "type": "keyword_tag",
            "keywords": ["urgent"],
            "tag": "紧急",
            "force": True,
        }])
        result = engine.apply({"tags": ["日常"]}, {"text": "urgent request"})
        assert result["tags"] == ["紧急"]

    def test_case_insensitive(self):
        engine = RuleEngine([{
            "type": "keyword_tag",
            "keywords": ["URGENT"],
            "tag": "紧急",
        }])
        result = engine.apply({"tags": []}, {"text": "this is urgent"})
        assert "紧急" in result["tags"]

    def test_no_match(self):
        engine = RuleEngine([{
            "type": "keyword_tag",
            "keywords": ["发动机"],
            "tag": "engine",
        }])
        result = engine.apply({"tags": []}, {"text": "起落架检修"})
        assert "engine" not in result.get("tags", [])


class TestRegexExtract:
    """regex_extract 规则测试"""

    def test_simple_regex(self):
        engine = RuleEngine([{
            "type": "regex_extract",
            "pattern": r"件号[:：]\s*([A-Z0-9\-]+)",
            "field": "part_number",
        }])
        result = engine.apply({}, {"text": "件号: BACB30FM8A4"})
        assert result["structured_data"]["part_number"] == "BACB30FM8A4"

    def test_nested_field(self):
        engine = RuleEngine([{
            "type": "regex_extract",
            "pattern": r"P/N[:：]\s*(\S+)",
            "field": "data.part_no",
        }])
        result = engine.apply({}, {"text": "P/N: 12345-ABC"})
        assert result["structured_data"]["data"]["part_no"] == "12345-ABC"

    def test_no_match(self):
        engine = RuleEngine([{
            "type": "regex_extract",
            "pattern": r"件号[:：]\s*(\S+)",
            "field": "part_number",
        }])
        result = engine.apply({}, {"text": "无件号信息"})
        assert "structured_data" not in result


class TestConditionalRoute:
    """conditional_route 规则测试"""

    def test_route_priority(self):
        engine = RuleEngine([{
            "type": "conditional_route",
            "tag": "紧急",
            "action_field": "priority",
            "action_value": "high",
        }])
        result = engine.apply({"tags": ["紧急"]}, {})
        assert result["priority"] == "high"

    def test_no_route(self):
        engine = RuleEngine([{
            "type": "conditional_route",
            "tag": "紧急",
            "action_value": "high",
        }])
        result = engine.apply({"tags": ["日常"]}, {})
        assert "priority" not in result


class TestFieldOverride:
    """field_override 规则测试"""

    def test_override_top_level(self):
        engine = RuleEngine([{
            "type": "field_override",
            "field": "doc_type",
            "value": "航材清单",
        }])
        result = engine.apply({"doc_type": "未知"}, {})
        assert result["doc_type"] == "航材清单"

    def test_override_nested(self):
        engine = RuleEngine([{
            "type": "field_override",
            "field": "structured_data.category",
            "value": "landing_gear",
        }])
        result = engine.apply({}, {})
        assert result["structured_data"]["category"] == "landing_gear"


class TestTagFilter:
    """tag_filter 规则测试"""

    def test_filter_tags(self):
        engine = RuleEngine([{
            "type": "tag_filter",
            "allowed_tags": ["航材", "工卡"],
        }])
        result = engine.apply({"tags": ["航材", "紧急", "工卡"]}, {})
        assert result["tags"] == ["航材", "工卡"]

    def test_empty_allowed(self):
        engine = RuleEngine([{
            "type": "tag_filter",
            "allowed_tags": [],
        }])
        result = engine.apply({"tags": ["a", "b"]}, {})
        assert result["tags"] == ["a", "b"]


class TestRuleErrors:
    """规则错误处理测试"""

    def test_disabled_rule(self):
        engine = RuleEngine([{
            "type": "field_override",
            "field": "x",
            "value": "y",
            "enabled": False,
        }])
        result = engine.apply({}, {})
        assert "x" not in result

    def test_invalid_regex(self):
        engine = RuleEngine([{
            "type": "regex_extract",
            "pattern": "(",  # invalid regex
            "field": "x",
            "name": "bad_regex",
        }])
        result = engine.apply({}, {"text": "test"})
        assert "_rule_errors" in result
        assert "bad_regex" in result["_rule_errors"][0]

    def test_multiple_rules(self):
        engine = RuleEngine([
            {"type": "keyword_tag", "keywords": ["紧急"], "tag": "紧急"},
            {"type": "field_override", "field": "priority", "value": "high"},
        ])
        result = engine.apply({"tags": []}, {"text": "紧急任务"})
        assert "紧急" in result["tags"]
        assert result["priority"] == "high"
