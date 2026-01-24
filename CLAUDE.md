# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

**task-agent** is a Chinese-language CLI tool that acts as a general-purpose task execution agent. It uses Large Language Models (LLM) via Ollama to understand natural language commands and execute bash/PowerShell commands.

**Key characteristics:**
- Language: Chinese-focused tool with Chinese prompts and responses
- LLM backend: Ollama (local inference)
- Execution: PowerShell commands on Windows, bash elsewhere
- Architecture: Multi-agent system with recursive task decomposition

## Development Commands

### 虚拟环境设置

```bash
# 创建 Python 3.11 虚拟环境（推荐，Gradio 性能最优）
uv venv --python 3.11 .venv

# 激活虚拟环境
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate

# 安装依赖（根据需要选择）
# 仅 CLI
uv pip install -e "."
# CLI + Dear PyGui
uv pip install -e ".[gui]"
# CLI + Gradio
uv pip install -e ".[gradio]"
# 完整安装（CLI + 两种 GUI）
uv pip install -e ".[gui,gradio]"
```

### 运行命令

```bash
# CLI 模式
task-agent

# Dear PyGui GUI
task-agent-gui

# Gradio GUI
task-agent-gradio

# 单次任务
task-agent "列出当前目录的文件"

# 带参数
task-agent "安装项目依赖" --timeout 120 --model qwen2.5:7b --verbose
```

### Python 版本性能对比

| Python 版本 | CLI | Dear PyGui | Gradio |
|------------|-----|-----------|--------|
| 3.11 | ✅ 快 | ✅ 快 (~1s) | ✅ 快 (~8s) |
| 3.12 | ✅ 快 | ✅ 快 (~1s) | ✅ 较快 (~10s) |
| 3.13 | ✅ 快 | ✅ 快 (~1s) | ⚠️ 慢 (~22s, SSL 性能问题) |

**注意**：Python 3.13 + Windows 存在 httpx SSL 性能问题，Gradio 启动较慢。推荐使用 Python 3.11。

## Environment Variables

| Variable | Description | Default |
|----------|-------------|---------|
| `OLLAMA_HOST` | Ollama server address | `http://localhost:11434` |
| `OLLAMA_MODEL` | Model name | `qwen3-48k:latest` |
| `OLLAMA_TIMEOUT` | Request timeout seconds | `300` |
| `OLLAMA_MAX_OUTPUT_TOKENS` | Max LLM output tokens | `4096` |
| `AGENT_LOG_FILE` | Log file path | - |

## Architecture

### Multi-Agent System

**Core design principles:**
- **Unified agent logic**: All agents use the same `SimpleAgent` class (no parent-child distinction)
- **Depth-first execution**: Completes one sub-agent branch before moving to the next
- **Max depth**: 4 levels deep, max 16 sub-agents (4²)
- **Result aggregation**: Sub-agent results are aggregated back to parent context

**Tool tag system** (XML-like format):
- `<ps_call> command </ps_call>` - Execute PowerShell/bash commands
- `<create_agent> task </create_agent>` - Create sub-agent for task
- `<create_agent name=agent_name> task </create_agent>` - Create predefined sub-agent
- `<completion> summary </completion>` - Mark task completion

### Module Structure

| Module | Purpose | Key Notes |
|--------|---------|-----------|
| `cli.py` | CLI interface | Rich console, connection check, verbose logging |
| `agent.py` | Core agent logic | 300 lines, contains SimpleAgent class |
| `config.py` | Configuration management | Environment variable loading |
| `tools.py` | Command execution | PowerShell/bash execution with timeout |

### Execution Flow

1. CLI parses user input and checks Ollama connection
2. SimpleAgent sends prompt to LLM with tool tag instructions
3. LLM responds with XML-like tool tags
4. Agent parses and executes tools (commands or sub-agents)
5. Results are aggregated and returned to parent context
6. Top-level agent outputs final results via Rich console

## Code Conventions

- **Language**: User-facing prompts and messages are in Chinese
- **Error handling**: Check Ollama connection before running agent
- **Token estimation**: 4 characters ≈ 1 token (see `tools.py:estimate_tokens()`)
- **Command timeout**: Default 300 seconds, configurable via `--timeout`

## Agent Flow Convention

- **Agent 文件首行**: 预定义 agent 文件（如 `agents/file-edit.md`）第一行使用 `# Xxx Flow` 命名格式。
- **子 agent 用户任务首行**: 通过 `<create_agent name=...>` 创建时，用户任务行按 `用户任务: Use Xxx Flow, <任务>` 组装；如果未解析到 Flow 名称，则保持 `用户任务: <任务>`。

## Process Management

**Gradio GUI 默认运行在 `http://127.0.0.1:7860`，终止时需通过端口号精确查找 PID。**

**查找 PID：**
```cmd
netstat -ano | findstr :7860
```

**输出示例：**
```
TCP    127.0.0.1:7860    0.0.0.0:0    LISTENING    12345
```

**终止进程：**
```cmd
taskkill /PID 12345 /F
```

**注意：** 必须使用 `/PID` 参数，禁止使用 `/IM python.exe`
