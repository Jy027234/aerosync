"""
AeroSync Cloud - 多 Provider LLM 统一封装
支持: OpenAI, DeepSeek, Claude(Anthropic), Ollama(本地)

实现策略：
- DeepSeek / Ollama 通过 OpenAI 兼容接口调用
- Claude 优先通过 OpenAI 兼容层调用（无需额外依赖）
- 若安装了 anthropic 库，可通过环境变量 CLAUDE_NATIVE=1 启用原生 API
"""
import os
from typing import Dict, Any, Optional

import openai

from api.core.config import settings
from api.core.logging_config import get_logger

logger = get_logger("llm_provider")

# Provider 配置映射（优先级：专属配置 > 通用 LLM_API_KEY）
_PROVIDER_CFG = {
    "openai": {
        "api_key": settings.LLM_API_KEY,
        "base_url": settings.LLM_BASE_URL,
        "model": settings.LLM_MODEL,
    },
    "deepseek": {
        "api_key": settings.DEEPSEEK_API_KEY or settings.LLM_API_KEY,
        "base_url": settings.DEEPSEEK_BASE_URL,
        "model": settings.DEEPSEEK_MODEL,
    },
    "claude": {
        "api_key": settings.CLAUDE_API_KEY or settings.LLM_API_KEY,
        "base_url": settings.CLAUDE_BASE_URL,
        "model": settings.CLAUDE_MODEL,
    },
    "ollama": {
        "api_key": "ollama",
        "base_url": settings.OLLAMA_BASE_URL,
        "model": settings.OLLAMA_MODEL,
    },
}


def _supports_response_format(provider: str) -> bool:
    """判断是否支持 response_format={"type": "json_object"}"""
    # Ollama 部分模型不支持，为安全起见关闭
    return provider not in ("ollama",)


class LLMClient:
    """统一 LLM 客户端：封装多 Provider 差异"""

    def __init__(self, provider: Optional[str] = None):
        self.provider = (provider or settings.LLM_PROVIDER).lower().strip()
        self.cfg = _PROVIDER_CFG.get(self.provider)

        if not self.cfg:
            logger.warning(f"未知 provider '{self.provider}'，回退到 openai")
            self.provider = "openai"
            self.cfg = _PROVIDER_CFG["openai"]

        # 回退逻辑：专属 key 为空时尝试通用 LLM_API_KEY
        if not self.cfg.get("api_key") and settings.LLM_API_KEY:
            self.cfg = {**self.cfg, "api_key": settings.LLM_API_KEY}

        self.model = self.cfg["model"]
        self._init_native_claude()

        if not self._native_claude:
            try:
                self.client = openai.OpenAI(
                    api_key=self.cfg["api_key"],
                    base_url=self.cfg["base_url"],
                    timeout=120,
                )
                logger.info(
                    f"LLM Client ready: provider={self.provider}, model={self.model}")
            except Exception as e:
                logger.error(f"LLM Client 初始化失败 [{self.provider}]: {e}")
                self.client = None

    def _init_native_claude(self):
        """检测是否使用 Anthropic 原生 API"""
        self._native_claude = False
        if self.provider != "claude":
            return
        if os.getenv("CLAUDE_NATIVE", "0") != "1":
            return
        try:
            import anthropic  # noqa: F401
            self._native_claude = True
            logger.info("Claude native API enabled")
        except ImportError:
            logger.warning("CLAUDE_NATIVE=1 但未安装 anthropic 库，回退到兼容层")

    def chat_completion(
        self,
        system_prompt: str,
        user_content: str,
        temperature: float = 0.3,
        max_tokens: int = 4000,
        json_mode: bool = True,
    ) -> Dict[str, Any]:
        """
        统一对话完成调用

        Returns:
            {"content": str, "provider": str, "model": str, "error": str|None}
        """
        if self._native_claude:
            return self._chat_anthropic_native(
                system_prompt, user_content, temperature, max_tokens, json_mode
            )
        return self._chat_openai_compatible(
            system_prompt, user_content, temperature, max_tokens, json_mode
        )

    def _chat_openai_compatible(
        self,
        system_prompt: str,
        user_content: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> Dict[str, Any]:
        """通过 OpenAI 兼容接口调用"""
        if not getattr(self, "client", None):
            return {"content": "", "error": "LLM client not initialized",
                    "model": self.model, "provider": self.provider}

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
        ]
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if json_mode and _supports_response_format(self.provider):
            kwargs["response_format"] = {"type": "json_object"}

        try:
            resp = self.client.chat.completions.create(**kwargs)
            content = resp.choices[0].message.content or ""
            return {
                "content": content,
                "provider": self.provider,
                "model": self.model,
                "error": None,
            }
        except Exception as e:
            logger.error(f"LLM API 错误 [{self.provider}]: {e}")
            return {
                "content": "",
                "error": f"{self.provider} API error: {str(e)}",
                "provider": self.provider,
                "model": self.model,
            }

    def _chat_anthropic_native(
        self,
        system_prompt: str,
        user_content: str,
        temperature: float,
        max_tokens: int,
        json_mode: bool,
    ) -> Dict[str, Any]:
        """通过 Anthropic 原生 SDK 调用 Claude"""
        try:
            import anthropic
            client = anthropic.Anthropic(
                api_key=self.cfg["api_key"],
                base_url=self.cfg["base_url"].replace("/v1", ""),
            )
            resp = client.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                temperature=temperature,
                system=system_prompt,
                messages=[{"role": "user", "content": user_content}],
            )
            content = ""
            if resp.content:
                content = resp.content[0].text if hasattr(
                    resp.content[0], "text") else str(resp.content[0])
            return {
                "content": content,
                "provider": "claude-native",
                "model": self.model,
                "error": None,
            }
        except Exception as e:
            logger.error(f"Claude native API 错误: {e}")
            return {
                "content": "",
                "error": f"claude-native error: {str(e)}",
                "provider": "claude-native",
                "model": self.model,
            }


def get_llm_client(provider: Optional[str] = None) -> "LLMClient":
    """获取 LLM 客户端实例"""
    return LLMClient(provider=provider)
