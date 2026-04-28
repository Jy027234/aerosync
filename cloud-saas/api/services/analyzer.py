"""
AeroSync Cloud - AI 分析服务
基于 LLM 的航空业务文档智能分析
支持多 Provider: OpenAI / DeepSeek / Claude / Ollama
"""
import json
from typing import Dict, Any, Optional

from api.core.config import settings
from api.core.logging_config import get_logger
from api.services.llm_provider import get_llm_client, LLMClient

logger = get_logger("analyzer")


class AIAnalyzer:
    """航空文档 AI 分析器"""

    DEFAULT_SYSTEM_PROMPT = """你是航空业务文档分析专家。分析文档内容，输出严格合法的 JSON：
{
  "summary": "核心内容摘要（100字内）",
  "tags": ["标签1", "标签2"],
  "structured_data": {
    "件号": "",
    "机型": "",
    "数量": "",
    "ATA章节": "",
    "工卡编号": "",
    "维修项目": "",
    "供应商": "",
    "文档类型": ""
  },
  "priority": "normal",
  "doc_type": "未知",
  "confidence": 0.0
}

文档类型识别规则：
- 航材清单/备件单 → doc_type: "航材清单"
- 工卡/维修工单 → doc_type: "工卡"
- 采购合同/订单 → doc_type: "采购合同"
- 报价单 → doc_type: "报价单"
- 技术通告/文件 → doc_type: "技术文件"
- 发票/财务凭证 → doc_type: "财务凭证"

标签规则：
- 航材清单提取件号、数量、适用机型
- 工卡提取飞机型号、工卡编号、维修项目
- 含 PMA 件标记 "PMA件"
- 起落架相关标记 "起落架"
- 紧急/加急标记 "紧急"
- 涉及发动机标记 "发动机"
- 涉及机身标记 "机身"

字段提取指南：
- "件号"：提取所有发现的件号 (Part Number)，多个用逗号分隔
- "机型"：B737-800, A320 等
- "数量"：数字+单位
- "ATA章节"：ATA XX 格式
- "工卡编号"：工卡/卡片的编号
- "维修项目"：维修内容的简要描述
- "供应商"：供货方名称

priority 规则：
- 含"紧急"、"加急"、"Critical"、"Urgent" → "high"
- 含"Routine"、"定期检查" → "low"
- 其他 → "normal"

注意事项：
- 如果某个字段未找到，设为空字符串 ""
- confidence 是整体识别置信度 (0.0-1.0)
- 必须输出合法 JSON，不要包含 markdown 代码块标记"""

    def __init__(self, custom_prompt: Optional[str] = None, provider: Optional[str] = None):
        """
        Args:
            custom_prompt: 覆盖默认 system prompt
            provider: 指定 LLM provider（openai/deepseek/claude/ollama），默认走配置
        """
        self.system_prompt = custom_prompt or self.DEFAULT_SYSTEM_PROMPT
        self.provider = provider

        # 本地开发模式：无 API Key 时不初始化 LLM 客户端
        if not settings.LLM_API_KEY and not settings.DEEPSEEK_API_KEY and not settings.CLAUDE_API_KEY:
            if (provider or settings.LLM_PROVIDER) != "ollama":
                self.client: Optional[LLMClient] = None
                logger.info("[AIAnalyzer] 本地开发模式：无 API Key，将使用 Mock 分析")
                return

        self.client = get_llm_client(provider=provider)
        if not self.client or not getattr(self.client, "client", None):
            logger.warning("[AIAnalyzer] LLM 客户端初始化失败，将使用 Mock 分析")

    def analyze(self, parsed_data: Dict[str, Any], filename: str) -> Dict[str, Any]:
        """
        对解析后的文档进行 AI 分析
        """
        text = parsed_data.get("text", "")
        structured = parsed_data.get("structured", {})
        file_type = parsed_data.get("type", "unknown")

        # 本地开发模式
        if not self.client or not getattr(self.client, "client", None):
            logger.info(f"[MockAnalyze] 本地开发模式，返回模拟分析结果: {filename}")
            return self._mock_analyze(text, filename, file_type)

        # 截断文本以适应模型上下文
        max_chars = 12000
        truncated_text = text[:max_chars]
        if len(text) > max_chars:
            truncated_text += f"\n\n[... 内容已截断，原长度 {len(text)} 字符]"

        user_content = f"""请分析以下航空业务文档：

文件名: {filename}
文件类型: {file_type}
文档结构信息: {json.dumps(structured, ensure_ascii=False)}

文档内容:
{truncated_text}
"""

        try:
            result = self.client.chat_completion(
                system_prompt=self.system_prompt,
                user_content=user_content,
                temperature=0.3,
                max_tokens=4000,
                json_mode=True,
            )

            if result.get("error"):
                raise Exception(result["error"])

            content = result["content"]
            ai_result = json.loads(content)

            # 校验必要字段
            ai_result.setdefault("summary", "")
            ai_result.setdefault("tags", [])
            ai_result.setdefault("structured_data", {})
            ai_result.setdefault("priority", "normal")
            ai_result.setdefault("doc_type", "未知")
            ai_result.setdefault("confidence", 0.5)

            # 如果 structured_data 是空对象，从已有信息填充
            sd = ai_result["structured_data"]
            if not sd or all(not v for v in sd.values()):
                from api.services.parser import extract_aviation_entities
                entities = extract_aviation_entities(text)
                sd["件号"] = ", ".join(entities.get("part_numbers", []))
                sd["机型"] = ", ".join(entities.get("aircraft_models", []))
                sd["ATA章节"] = ", ".join(entities.get("ata_chapters", []))

            # 添加 provider 信息便于调试
            ai_result["_llm_provider"] = result.get("provider")
            ai_result["_llm_model"] = result.get("model")

            return ai_result

        except json.JSONDecodeError as e:
            logger.warning(f"JSON解析失败: {e}")
            return {
                "summary": f"JSON解析失败: {str(e)}",
                "tags": ["解析错误"],
                "structured_data": {},
                "priority": "normal",
                "doc_type": "未知",
                "confidence": 0.0,
                "error": f"JSON decode error: {str(e)}"
            }
        except Exception as e:
            logger.exception(f"AI分析异常: {e}")
            return {
                "summary": f"分析异常: {str(e)}",
                "tags": ["异常"],
                "structured_data": {},
                "priority": "normal",
                "doc_type": "未知",
                "confidence": 0.0,
                "error": str(e)
            }

    def _mock_analyze(self, text: str, filename: str, file_type: str) -> Dict[str, Any]:
        """本地开发模式：返回模拟的 AI 分析结果"""
        from api.services.parser import extract_aviation_entities
        entities = extract_aviation_entities(text)

        fname_lower = filename.lower()
        if any(k in fname_lower for k in ["航材", "part", "list", "备件", "material"]):
            doc_type = "航材清单"
        elif any(k in fname_lower for k in ["工卡", "task", "card", "维修", "repair"]):
            doc_type = "工卡"
        elif any(k in fname_lower for k in ["合同", "contract", "采购", "purchase", "order"]):
            doc_type = "采购合同"
        elif any(k in fname_lower for k in ["报价", "quote", "price"]):
            doc_type = "报价单"
        elif any(k in fname_lower for k in ["发票", "invoice", "财务", "finance"]):
            doc_type = "财务凭证"
        else:
            doc_type = "未知"

        tags = []
        if entities.get("pma_marked"):
            tags.append("PMA件")
        if entities.get("landing_gear_marked"):
            tags.append("起落架")
        if "紧急" in text or "urgent" in text.lower():
            tags.append("紧急")
            priority = "high"
        else:
            priority = "normal"

        mock_result = {
            "summary": f"【模拟模式】文件名: {filename}, 类型: {doc_type}, "
                       f"本地开发模式下未调用LLM，仅做基础实体提取。",
            "tags": tags if tags else ["本地开发"],
            "structured_data": {
                "件号": ", ".join(entities.get("part_numbers", [])),
                "机型": ", ".join(entities.get("aircraft_models", [])),
                "数量": ", ".join(entities.get("quantities", [])),
                "ATA章节": ", ".join(entities.get("ata_chapters", [])),
                "工卡编号": "",
                "维修项目": "",
                "供应商": "",
                "文档类型": doc_type,
            },
            "priority": priority,
            "doc_type": doc_type,
            "confidence": 0.6,
            "_mock": True,
            "_llm_provider": "mock",
            "_llm_model": "none",
        }
        logger.info(f"[MockAnalyze] 返回模拟结果: doc_type={doc_type}, tags={tags}")
        return mock_result

    def analyze_with_retry(self, parsed_data: Dict[str, Any], filename: str, max_retries: int = 2) -> Dict[str, Any]:
        """带重试的分析"""
        last_error = None
        for attempt in range(max_retries + 1):
            result = self.analyze(parsed_data, filename)
            if not result.get("error"):
                return result
            last_error = result.get("error")
        result["error"] = f"{last_error} (after {max_retries + 1} retries)"
        return result
