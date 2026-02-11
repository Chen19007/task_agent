import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from task_agent.config import Config
from task_agent.agent import SimpleAgent


def _get_system_prompt(config: Config, runtime_scene: str) -> str:
    agent = SimpleAgent(config=config, runtime_scene=runtime_scene)
    assert agent.history
    assert agent.history[0].role == "system"
    return agent.history[0].content


def test_builtin_section_dynamic_numbering_without_schedule():
    config = Config(
        webhook_app_id="app",
        webhook_app_secret="secret",
        webhook_calendar_id="calendar_1",
    )
    prompt = _get_system_prompt(config, runtime_scene="cli")

    assert "**2. 内置工具（使用 <builtin> 标签）**" in prompt
    assert "**2.1 read_file（builtin.read_file）**" in prompt
    assert "**2.2 后台任务日志（builtin.get_job_log）：**" in prompt
    assert "**2.3 内置文件编辑（builtin.smart_edit）**" in prompt
    assert "**2.4 记忆查询（builtin.memory_query）**" in prompt
    assert "**2.5 Hint 内置工具（适合子任务/预定义 Agent）**" in prompt
    assert "创建日程（builtin.create_schedule）" not in prompt


def test_builtin_schedule_injected_only_in_webhook_scene():
    config = Config(
        webhook_app_id="app",
        webhook_app_secret="secret",
        webhook_calendar_id="calendar_1",
    )
    prompt = _get_system_prompt(config, runtime_scene="webhook")

    assert "**2.5 创建日程（builtin.create_schedule）**" in prompt
    assert "**2.6 Hint 内置工具（适合子任务/预定义 Agent）**" in prompt
    assert "用于创建飞书日程（依赖外部 API，仅在系统注入该能力时可用）。" in prompt
    assert "summary: 日程标题" in prompt
    assert "start_time: 2026-02-12 10:00" in prompt
    assert "end_time 可选，缺省时默认开始时间 + 30 分钟" in prompt
    assert "当用户明确要求“创建日程/安排会议”时优先使用该工具" in prompt


def test_builtin_schedule_not_injected_when_missing_required_config():
    config = Config(
        webhook_app_id="app",
        webhook_app_secret="secret",
        webhook_calendar_id="",
    )
    prompt = _get_system_prompt(config, runtime_scene="webhook")

    assert "创建日程（builtin.create_schedule）" not in prompt
    assert "**2.5 Hint 内置工具（适合子任务/预定义 Agent）**" in prompt
