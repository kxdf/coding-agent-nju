import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Config:
    api_key: str
    base_url: str
    model: str
    workspace: Path
    max_steps: int
    timeout_seconds: int
    auto_approve: bool
    enable_logging: bool


def load_config() -> Config:
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("Missing OPENAI_API_KEY environment variable.")

    base_url = os.environ.get("OPENAI_BASE_URL", "https://api.deepseek.com").strip()
    model = os.environ.get("MODEL_NAME", "deepseek-chat").strip()
    workspace = Path(os.environ.get("AGENT_WORKSPACE", "agent_workspace")).resolve()
    max_steps = int(os.environ.get("AGENT_MAX_STEPS", "20"))
    timeout_seconds = int(os.environ.get("AGENT_COMMAND_TIMEOUT", "20"))
    auto_approve = _env_bool("AGENT_AUTO_APPROVE", False)
    enable_logging = _env_bool("AGENT_ENABLE_LOGGING", True)

    return Config(
        api_key=api_key,
        base_url=base_url.rstrip("/"),
        model=model,
        workspace=workspace,
        max_steps=max_steps,
        timeout_seconds=timeout_seconds,
        auto_approve=auto_approve,
        enable_logging=enable_logging,
    )


def _env_bool(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}
