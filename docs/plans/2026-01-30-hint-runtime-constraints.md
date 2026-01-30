# Hint 资源与脚本约束 Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 引入“hint”概念与强隔离资源/脚本加载机制，让 LLM 只需调用函数与内置工具而无需感知路径。

**Architecture:** 在现有 Agent/CLI 解析链路中新增 hint 管理器与资源解析逻辑，限制资源可见范围为“当前激活 hint 的 resources”，并支持 PS 模块路径集合刷新。LLM 通过 `builtin.get_resource` 访问资源，调用 `ps_call` 执行由 hint 模块提供的函数。

**Tech Stack:** Python（现有 Agent/CLI 架构）、PowerShell 模块（psm1）

---

### Task 1: 设计 hint 目录结构与加载协议

**Files:**
- Create: `docs/plans/2026-01-30-hint-runtime-constraints.md`
- Modify: `src/task_agent/agent.py`
- Modify: `src/task_agent/cli.py`
- Create: `src/task_agent/hint_registry.py`
- Create: `src/task_agent/hint_state.py`

**Step 1: 明确目录约定并写入注释文档**

将以下约定写入 `src/task_agent/hint_registry.py` 文件头注释，作为实现依据：
- 每个 hint 在独立目录：`hints/<name>/`
- 目录内包含：`hint.md`（提示词）、`hint.yaml`（元数据描述，含 `name`/`description`/`system_prompt_injection`）、`resources/`（资源文件）、`modules/`（PowerShell 模块）
- 激活 hint 时只刷新可用模块路径列表（不在此处执行 Import/Remove）
- 资源仅允许：当前 hint 的 `resources/`

**Step 2: 资源目录范围**

资源仅允许访问当前激活 hint 的 `resources/`，不引入全局资源目录。

**Step 3: 定义 hint 状态结构**

在 `src/task_agent/hint_state.py` 定义：
- 当前激活 hint 名称（单一）
- 已加载模块列表
- 资源根目录列表（当前 hint）

---

### Task 2: 新增 hint 管理器与资源解析能力

**Files:**
- Create: `src/task_agent/hint_registry.py`
- Modify: `src/task_agent/agent.py`
- Modify: `src/task_agent/cli.py`

**Step 1: 实现 HintRegistry 基础能力**

在 `src/task_agent/hint_registry.py` 中实现：
- `load_hint(name)`: 解析目录、验证文件存在、生成提示词文本、确定资源根目录、确定模块路径
- `unload_hint()`: 返回需要卸载的模块列表
- `get_active_resources()`: 返回资源根目录列表（当前 hint）

**Step 2: 仅允许单一 hint 激活**

在 `load_hint` 中，如果已有激活 hint，先执行 `unload_hint` 清理后再加载新 hint（确保同一时间只有一个 hint）。

**Step 3: hint 元数据与提示词消息**

- 元数据来源：`hints/<name>/hint.yaml`
- 汇总为 JSON 列表后注入到 `templates/system_prompt.txt` 的 `{hint_metadata}` 占位符
- 提示词来源：`hints/<name>/hint.md`，由 `builtin.hint` 返回给调用方，作为普通消息使用（不注入系统提示词）

---

### Task 3: 新增内置工具 builtin.get_resource

**Files:**
- Modify: `src/task_agent/cli.py`

**Step 1: 扩展内置工具路由**

在 `_execute_builtin_tool` 中新增 `builtin.get_resource` 分支，统一处理资源读取逻辑。

**Step 2: 资源读取规则**

实现 `builtin.get_resource`：
- 只允许在当前激活 hint 的 `resources/` 中读取
- 参数包含 `path`（相对资源目录）
- 拒绝绝对路径与路径跳转（..）
- 返回内容与统一错误信息

---

### Task 4: PowerShell 模块加载/卸载流程

**Files:**
- Modify: `src/task_agent/agent.py`
- Modify: `src/task_agent/cli.py`

**Step 1: 模块加载策略**

在 hint 激活时：
- 收集 `modules/*.psm1`
- 仅刷新“可用模块路径列表”，不立即 Import
- 在每次执行 `ps_call` 前自动增加 `Import-Module`（因为每次为独立 PowerShell 进程）

**Step 2: 模块卸载策略**

在 hint 切换或关闭时：
- 不执行 `Remove-Module`
- 不清理提示词注入与隔离逻辑（避免复杂度）

**Step 3: LLM 调用方式保持不变**

LLM 仅需调用函数：
- `ps_call` 内执行函数名
- 不暴露模块路径

---

### Task 5: hint 切换与关闭接口

**Files:**
- Modify: `src/task_agent/agent.py`
- Modify: `src/task_agent/cli.py`

**Step 1: 增加 hint 控制标签或内置命令**

沿用“无需新标签”的约束：
- 通过内置命令 `builtin.hint`（新增）或已有控制通道触发
- 参数示例：
  ```
  <builtin>
  hint
  action: load
  name: xxx
  </builtin>

  <builtin>
  hint
  action: unload
  </builtin>
  ```

**Step 2: 执行流程**

- load：卸载当前 hint → 加载新 hint → 返回提示词内容（由调用方决定注入方式）
- unload：卸载模块 → 返回确认信息

---

### Task 6: CLI/GUI 显示与用户提示

**Files:**
- Modify: `src/task_agent/cli_output.py`
- Modify: `src/task_agent/gui/message_parser.py`（如需）

**Step 1: 增加 hint 状态提示**

在 CLI 输出中展示当前 hint 状态（可选），以便用户确认启用/切换成功。

---

### Task 7: 测试与验证（如引入 tests）

**Files:**
- Create: `tests/test_hint_registry.py`

**Step 1: HintRegistry 单测**

覆盖：
- 加载有效 hint
- 切换 hint 时资源根目录变化
- 读取非法路径拒绝（.. / 绝对路径）

**Step 2: 运行测试**

Run: `pytest e:\project\python\task_agent\tests\test_hint_registry.py -v`
Expected: PASS

---

Plan complete and saved to `docs/plans/2026-01-30-hint-runtime-constraints.md`. Two execution options:

1. Subagent-Driven (this session) - I dispatch fresh subagent per task, review between tasks, fast iteration

2. Parallel Session (separate) - Open new session with executing-plans, batch execution with checkpoints

Which approach?
