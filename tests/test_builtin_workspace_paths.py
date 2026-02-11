import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

import task_agent.cli as cli


def test_read_file_relative_path_resolved_by_workspace(tmp_path):
    target = tmp_path / "demo.txt"
    target.write_text("line1\nline2\n", encoding="utf-8")

    result = cli._execute_builtin_read_file({"path": "demo.txt"}, workspace_dir=str(tmp_path))
    assert result.returncode == 0
    assert "line1" in result.stdout
    assert str(target.resolve()) in result.stdout


def test_read_file_relative_path_fails_without_workspace():
    result = cli._execute_builtin_read_file({"path": "demo.txt"}, workspace_dir="")
    assert result.returncode == 1
    assert "workspace_dir 未设置" in result.stderr


def test_smart_edit_create_relative_path_resolved_by_workspace(tmp_path):
    result = cli._execute_builtin_smart_edit(
        {
            "path": "created.txt",
            "mode": "Create",
            "new_text": "hello",
        },
        workspace_dir=str(tmp_path),
    )
    assert result.returncode == 0
    assert (tmp_path / "created.txt").read_text(encoding="utf-8") == "hello"


def test_smart_edit_relative_path_fails_without_workspace():
    result = cli._execute_builtin_smart_edit(
        {
            "path": "created.txt",
            "mode": "Create",
            "new_text": "hello",
        },
        workspace_dir="",
    )
    assert result.returncode == 1
    assert "workspace_dir 未设置" in result.stderr
