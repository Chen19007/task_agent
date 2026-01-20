"""配置管理模块"""

from dataclasses import dataclass


@dataclass
class Config:
    """Agent 配置类"""

    # API 类型
    api_type: str = "ollama"  # "ollama" | "openai"

    # Ollama 配置
    ollama_host: str = "http://localhost:11434"

    # OpenAI 配置
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"

    # 通用配置
    model: str = "qwen3:4b"
    timeout: int = 300
    max_output_tokens: int = 4096
    num_ctx: int = 4096

    @classmethod
    def from_env(cls) -> "Config":
        """从环境变量加载配置"""
        import os

        return cls(
            api_type=os.environ.get("LLM_API_TYPE", "ollama"),
            ollama_host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            openai_api_key=os.environ.get("OPENAI_API_KEY", ""),
            openai_base_url=os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1"),
            model=os.environ.get("OLLAMA_MODEL", "qwen3:4b"),
            timeout=int(os.environ.get("OLLAMA_TIMEOUT", "300")),
            max_output_tokens=int(os.environ.get("OLLAMA_MAX_OUTPUT_TOKENS", "4096")),
            num_ctx=int(os.environ.get("OLLAMA_NUM_CTX", "4096")),
        )

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "api_type": self.api_type,
            "ollama_host": self.ollama_host,
            "openai_base_url": self.openai_base_url,
            "model": self.model,
            "timeout": self.timeout,
            "max_output_tokens": self.max_output_tokens,
            "num_ctx": self.num_ctx,
        }
