"""
AeroSync Cloud - Phase 2 规则引擎
基于规则的 AI 结果修正与路由
"""
import re
from typing import List, Dict, Any

from api.core.logging_config import get_logger

logger = get_logger("ruler")


class RuleEngine:
    """
    规则引擎：根据租户配置的规则修正 AI 分析结果
    
    支持的规则类型：
    - keyword_tag: 关键词匹配添加/强制标签
    - regex_extract: 正则提取结构化字段
    - conditional_route: 条件路由（修改优先级）
    - field_override: 字段覆盖（强制修改某个字段）
    - tag_filter: 标签过滤（移除不符合条件的标签）
    """

    def __init__(self, rules: List[Dict[str, Any]]):
        self.rules = rules or []

    def apply(self, ai_result: Dict[str, Any], raw_data: Dict[str, Any]) -> Dict[str, Any]:
        """
        应用所有规则到 AI 结果
        Args:
            ai_result: AIAnalyzer.analyze 的输出
            raw_data: parser.parse_document 的原始输出（包含 text）
        Returns:
            修正后的 ai_result
        """
        text = raw_data.get("text", "")
        result = ai_result.copy()

        for rule in self.rules:
            if not rule.get("enabled", True):
                continue

            try:
                rule_type = rule.get("type")
                logger.debug(f"应用规则: {rule.get('name', 'unnamed')} ({rule_type})")

                if rule_type == "keyword_tag":
                    result = self._apply_keyword_tag(rule, result, text)
                elif rule_type == "regex_extract":
                    result = self._apply_regex_extract(rule, result, text)
                elif rule_type == "conditional_route":
                    result = self._apply_conditional_route(rule, result)
                elif rule_type == "field_override":
                    result = self._apply_field_override(rule, result)
                elif rule_type == "tag_filter":
                    result = self._apply_tag_filter(rule, result)

            except Exception as e:
                # 单条规则失败不影响其他规则
                result["_rule_errors"] = result.get("_rule_errors", [])
                result["_rule_errors"].append(f"Rule {rule.get('name', 'unnamed')}: {str(e)}")

        return result

    def _apply_keyword_tag(self, rule: Dict, result: Dict, text: str) -> Dict:
        """关键词匹配添加标签"""
        keywords = rule.get("keywords", [])
        tag = rule.get("tag", "")

        if not keywords or not tag:
            return result

        # 支持大小写不敏感匹配
        case_sensitive = rule.get("case_sensitive", False)
        check_text = text if case_sensitive else text.lower()
        check_keywords = keywords if case_sensitive else [k.lower() for k in keywords]

        if any(kw in check_text for kw in check_keywords):
            tags = result.get("tags", [])
            if rule.get("force"):
                # 强制标签模式：只保留指定标签
                tags = [tag]
            elif tag not in tags:
                tags.append(tag)
            result["tags"] = tags

        return result

    def _apply_regex_extract(self, rule: Dict, result: Dict, text: str) -> Dict:
        """正则提取字段"""
        pattern = rule.get("pattern", "")
        field = rule.get("field", "")

        if not pattern or not field:
            return result

        flags = 0
        if rule.get("ignore_case", True):
            flags |= re.IGNORECASE

        matches = re.findall(pattern, text, flags)
        if matches:
            sd = result.get("structured_data", {})
            if isinstance(matches[0], tuple):
                # 如果正则有捕获组，取第一个非空组
                match_value = next((m for m in matches[0] if m), "")
            else:
                match_value = matches[0] if len(matches) == 1 else matches

            # 支持多级字段路径，如 "data.part_number"
            keys = field.split(".")
            target = sd
            for key in keys[:-1]:
                target.setdefault(key, {})
                target = target[key]
            target[keys[-1]] = match_value
            result["structured_data"] = sd

        return result

    def _apply_conditional_route(self, rule: Dict, result: Dict) -> Dict:
        """条件路由：根据标签条件修改优先级或其他字段"""
        condition_tag = rule.get("tag", "")
        action_field = rule.get("action_field", "priority")
        action_value = rule.get("action_value", "normal")

        if condition_tag and condition_tag in result.get("tags", []):
            if action_field == "priority":
                result["priority"] = action_value
            else:
                # 通用字段修改
                result[action_field] = action_value

        return result

    def _apply_field_override(self, rule: Dict, result: Dict) -> Dict:
        """强制覆盖字段值"""
        field_path = rule.get("field", "")
        value = rule.get("value", "")

        if not field_path:
            return result

        # 支持修改嵌套字段
        if "." in field_path:
            keys = field_path.split(".")
            target = result
            for key in keys[:-1]:
                target.setdefault(key, {})
                target = target[key]
            target[keys[-1]] = value
        else:
            result[field_path] = value

        return result

    def _apply_tag_filter(self, rule: Dict, result: Dict) -> Dict:
        """标签过滤：移除不符合白名单的标签"""
        allowed_tags = rule.get("allowed_tags", [])
        if not allowed_tags:
            return result

        tags = result.get("tags", [])
        result["tags"] = [t for t in tags if t in allowed_tags]
        return result