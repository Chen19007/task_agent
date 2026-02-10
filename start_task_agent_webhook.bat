@echo off
chcp 65001 >nul
cd /d "%~dp0"

task-agent-webhook -a openai -b http://localhost:3000/v1 -m "minimax-m2"