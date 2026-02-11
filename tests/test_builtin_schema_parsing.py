import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = PROJECT_ROOT / "src"
sys.path.insert(0, str(SRC_ROOT))

import task_agent.cli as cli


def test_parse_hint_command_success():
    command = "builtin.hint\naction: unload\n"
    args, error = cli._parse_hint_command(command)
    assert error is None
    assert args["action"] == "unload"


def test_parse_hint_command_load_requires_name():
    command = "builtin.hint\naction: load\n"
    args, error = cli._parse_hint_command(command)
    assert args == {}
    assert error == "hint load 需要参数: name"


def test_parse_read_file_command_supports_file_alias():
    command = "builtin.read_file\nfile: demo.txt\n"
    args, error = cli._parse_read_file_command(command)
    assert error is None
    assert args["path"] == "demo.txt"


def test_parse_job_log_command_supports_id_alias():
    command = "builtin.get_job_log\nid: abc\n"
    args, error = cli._parse_job_log_command(command)
    assert error is None
    assert args["job_id"] == "abc"


def test_parse_smart_edit_command_merges_schema_defaults():
    command = (
        "builtin.smart_edit\n"
        "path: demo.txt\n"
        "old_text:\n"
        "<<<\n"
        "a\n"
        ">>>\n"
        "new_text:\n"
        "<<<\n"
        "b\n"
        ">>>\n"
    )
    args, error = cli._parse_smart_edit_command(command)
    assert error is None
    assert args["path"] == "demo.txt"
    assert args["mode"] == "Patch"
    assert args["old_text"] == "a"
    assert args["new_text"] == "b"
