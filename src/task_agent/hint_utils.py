"""Hint 文件解析辅助函数。"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .platform_utils import get_hint_platform_suffix


def select_hint_file(hint_dir: Path, ext: str) -> Optional[Path]:
    """根据平台选择 hint 文件路径。

    优先级：
    1) hint_<platform>.<ext>
    2) <hintname>_<platform>.<ext>
    3) hint.<ext>
    4) <hintname>.<ext>
    """
    suffix = get_hint_platform_suffix()
    name = hint_dir.name
    candidates = [
        hint_dir / f"hint_{suffix}{ext}",
        hint_dir / f"{name}_{suffix}{ext}",
        hint_dir / f"hint{ext}",
        hint_dir / f"{name}{ext}",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None
