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

```bash
# Install in development mode
pip install -e .

# Run the tool (interactive mode)
task-agent

# Run single task
task-agent "列出当前目录的文件"

# With options
task-agent "安装项目依赖" --timeout 120 --model qwen2.5:7b --verbose
```

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
