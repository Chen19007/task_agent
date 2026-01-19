"""配置管理模块"""

from dataclasses import dataclass


@dataclass
class Config:
    """Agent 配置类"""

    # Ollama 配置
    ollama_host: str = "http://localhost:11434"
    model: str = "qwen3:4b"

    # 执行配置
    timeout: int = 300
    max_output_tokens: int = 1024

    @classmethod
    def from_env(cls) -> "Config":
        """从环境变量加载配置"""
        import os
        
        return cls(
            ollama_host=os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
            model=os.environ.get("OLLAMA_MODEL", "qwen3:4b"),
            timeout=int(os.environ.get("OLLAMA_TIMEOUT", "300")),
            max_output_tokens=int(os.environ.get("OLLAMA_MAX_OUTPUT_TOKENS", "1024")),
        )

    def to_dict(self) -> dict:
        """转换为字典"""
        return {
            "ollama_host": self.ollama_host,
            "model": self.model,
            "timeout": self.timeout,
            "max_output_tokens": self.max_output_tokens,
        }
