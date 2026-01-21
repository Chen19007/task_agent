#!/usr/bin/env python3
"""任务执行 Agent - setup.py"""

from setuptools import setup, find_packages

setup(
    name="task-agent",
    version="0.1.0",
    description="通用任务执行 Agent，通过自然语言让 LLM 执行任意 bash 命令",
    author="MiniMax Agent",
    py_modules=[],
    python_requires=">=3.8",
    install_requires=[
        "requests>=2.31.0",
        "rich>=13.7.0",
    ],
    extras_require={
        "gui": [
            "dearpygui>=1.10.0",
        ],
    },
    packages=find_packages(where="src"),
    package_dir={"": "src"},
    entry_points={
        "console_scripts": [
            "task-agent=task_agent.cli:main",
            "task-agent-gui=task_agent.gui.app:main",
        ],
    },
    include_package_data=True,
)
