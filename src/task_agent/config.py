"""配置管理模块"""

from dataclasses import dataclass
from pathlib import Path


def load_local_env(env_file: str = ".env", overwrite: bool = False) -> None:
    """从项目本地 .env 文件加载环境变量。"""
    import os

    env_path = Path(env_file)
    if not env_path.is_absolute():
        env_path = Path.cwd() / env_path
    if not env_path.exists() or not env_path.is_file():
        return

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("export "):
            line = line[7:].strip()
        if "=" not in line:
            continue

        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue

        if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
            value = value[1:-1]

        if overwrite or key not in os.environ:
            os.environ[key] = value


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

    # Webhook 配置
    webhook_platform: str = "feishu"
    webhook_app_id: str = ""
    webhook_app_secret: str = ""
    webhook_host: str = "0.0.0.0"
    webhook_port: int = 8080

    @classmethod
    def from_env(cls) -> "Config":
        """从环境变量加载配置"""
        import os
        load_local_env(".env", overwrite=False)

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
            webhook_platform=os.environ.get("WEBHOOK_PLATFORM", "feishu"),
            webhook_app_id=os.environ.get("WEBHOOK_APP_ID", ""),
            webhook_app_secret=os.environ.get("WEBHOOK_APP_SECRET", ""),
            webhook_host=os.environ.get("WEBHOOK_HOST", "0.0.0.0"),
            webhook_port=int(os.environ.get("WEBHOOK_PORT", "8080")),
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
