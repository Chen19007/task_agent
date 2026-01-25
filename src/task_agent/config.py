"""配置管理模块"""

from dataclasses import dataclass


@dataclass
class Config:
    """Agent 配置类"""

    # API 类型
    api_type: str = "openai"  # "ollama" | "openai"

    # Ollama 配置
    ollama_host: str = "http://localhost:11434"

    # OpenAI 配置
    openai_api_key: str = "sk-1qTPR2NfODm9Y8YwQTXtGVONXF0g2bxWWreaZaMvPK4ErKOV"
    openai_base_url: str = "http://localhost:3000/v1"

    # 通用配置
    model: str = "minimax-m2"
    timeout: int = 300
    max_output_tokens: int = 4096
    num_ctx: int = 4096
    auto_compact: bool = True
    auto_compact_threshold: float = 0.75
    compact_keep_messages: int = 6
    compact_chunk_chars: int = 12000

    @classmethod
    def from_env(cls) -> "Config":
        """从环境变量加载配置"""
        import os
        def to_bool(value: str, default: bool) -> bool:
            if value is None:
                return default
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}

        def to_float(value: str, default: float) -> float:
            if value is None:
                return default
            try:
                return float(value)
            except ValueError:
                return default

        def to_int(value: str, default: int) -> int:
            if value is None:
                return default
            try:
                return int(value)
            except ValueError:
                return default

        return cls(
            api_type=os.environ.get("LLM_API_TYPE", "openai"),
            ollama_host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            openai_api_key=os.environ.get("OPENAI_API_KEY", "sk-1qTPR2NfODm9Y8YwQTXtGVONXF0g2bxWWreaZaMvPK4ErKOV"),
            openai_base_url=os.environ.get("OPENAI_BASE_URL", "http://localhost:3000/v1"),
            model=os.environ.get("OLLAMA_MODEL", "minimax-m2"),
            timeout=int(os.environ.get("OLLAMA_TIMEOUT", "300")),
            max_output_tokens=int(os.environ.get("OLLAMA_MAX_OUTPUT_TOKENS", "4096")),
            num_ctx=int(os.environ.get("OLLAMA_NUM_CTX", "4096")),
            auto_compact=to_bool(os.environ.get("AGENT_AUTO_COMPACT"), True),
            auto_compact_threshold=to_float(os.environ.get("AGENT_AUTO_COMPACT_THRESHOLD"), 0.75),
            compact_keep_messages=to_int(os.environ.get("AGENT_COMPACT_KEEP_MESSAGES"), 6),
            compact_chunk_chars=to_int(os.environ.get("AGENT_COMPACT_CHUNK_CHARS"), 12000),
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
            "auto_compact": self.auto_compact,
            "auto_compact_threshold": self.auto_compact_threshold,
            "compact_keep_messages": self.compact_keep_messages,
            "compact_chunk_chars": self.compact_chunk_chars,
        }
