# Repository Guidelines

## Project Structure & Module Organization
- Source code lives in `src/task_agent/`.
- Key modules: `cli.py` (CLI entry), `agent.py` (core agent logic), `config.py` (env config), `llm/` (LLM integration).
- Package metadata is in `pyproject.toml` and `setup.py`.
- No dedicated `tests/` directory exists currently.

## Build, Test, and Development Commands
- `pip install -e .` installs the package in editable mode for local development.
- `task-agent` starts the interactive CLI (entry point defined in `pyproject.toml`).
- `task-agent "list files in current directory"` runs a single task non-interactively.
- There is no build step beyond standard Python packaging via setuptools.

## Coding Style & Naming Conventions
- Follow standard Python style (PEP 8) with 4-space indentation.
- Keep user-facing prompts and messages in Chinese (project convention).
- Module and function names are snake_case; classes are PascalCase.
- Keep logic concise and document complex flows with brief comments.

## Testing Guidelines
- No testing framework is configured yet.
- If you add tests, place them under a new `tests/` directory and name files `test_*.py`.
- Use `pytest` style and document how to run tests in this file when added.

## Commit & Pull Request Guidelines
- Commit subjects in history use short prefixes like `Fix:`, `Add:`, or `Refactor:` followed by a brief summary.
- Prefer imperative, single-line subjects; include context in the body when needed.
- PRs should describe the behavior change, mention any user-visible output changes, and link issues if applicable.

## Configuration & Environment
- Runtime settings are controlled via environment variables in `config.py`.
- Common variables include `OLLAMA_HOST`, `OLLAMA_MODEL`, `OLLAMA_TIMEOUT`, and `AGENT_LOG_FILE`.
- The CLI checks the LLM backend before running tasks; keep that behavior intact.

## Agent-Specific Notes
- The agent executes PowerShell on Windows and bash on other platforms.
- Tool tags in LLM responses are parsed and executed; keep tag formats stable if edited.
- 注意：已新增用于 Linux bash_call 的系统提示词与 hint 机制等上下文；修改 LLM 相关逻辑时请同步检查这些新增上下文。
- 项目根目录新增 `TODO.md`，记录长期待办事项（非会话内 TODO）。
