import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
HINTS_ROOT = PROJECT_ROOT / "hints"
sys.path.insert(0, str(SRC_ROOT))

import task_agent.cli as cli


def _cleanup_hint_dir(name: str) -> None:
    shutil.rmtree(HINTS_ROOT / name, ignore_errors=True)


def _setup_hint(monkeypatch, name: str = "demo") -> Path:
    hints_root = HINTS_ROOT
    resources_dir = hints_root / name / "resources"
    resources_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(cli, "_get_hints_root", lambda: hints_root)
    monkeypatch.setattr(cli, "_ACTIVE_HINT", name)
    return resources_dir


def test_get_resource_requires_active_hint(monkeypatch):
    monkeypatch.setattr(cli, "_get_hints_root", lambda: HINTS_ROOT)
    monkeypatch.setattr(cli, "_ACTIVE_HINT", None)
    result = cli._execute_builtin_get_resource({"path": "sample.txt"})
    assert result.returncode == 1
    assert "未激活 hint" in result.stderr


def test_get_resource_reads_from_active_hint(monkeypatch):
    hint_name = "_pytest_demo"
    try:
        resources_dir = _setup_hint(monkeypatch, hint_name)
        target = resources_dir / "hello.txt"
        target.write_text("hello", encoding="utf-8")

        result = cli._execute_builtin_get_resource({"path": "hello.txt"})
        assert result.returncode == 0
        assert result.stderr == ""
        assert result.stdout == "hello"
    finally:
        _cleanup_hint_dir(hint_name)


def test_get_resource_rejects_path_traversal(monkeypatch):
    hint_name = "_pytest_demo"
    try:
        _setup_hint(monkeypatch, hint_name)
        result = cli._execute_builtin_get_resource({"path": "../secret.txt"})
        assert result.returncode == 1
        assert "路径不合法" in result.stderr
    finally:
        _cleanup_hint_dir(hint_name)
