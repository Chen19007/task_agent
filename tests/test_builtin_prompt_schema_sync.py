import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

from task_agent.agent import SimpleAgent
from task_agent.config import Config
from task_agent.builtin_schema import (
    build_builtin_read_file_example_lines,
    build_builtin_smart_edit_example_lines,
)


def test_prompt_contains_read_file_schema_example():
    prompt = SimpleAgent(config=Config(), runtime_scene="cli").history[0].content
    for line in build_builtin_read_file_example_lines():
        assert line in prompt


def test_prompt_contains_smart_edit_schema_example():
    prompt = SimpleAgent(config=Config(), runtime_scene="cli").history[0].content
    for line in build_builtin_smart_edit_example_lines():
        assert line in prompt
