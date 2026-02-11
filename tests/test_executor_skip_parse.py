from task_agent.agent import Action, Executor, SimpleAgent, StepResult


class _DummyAgent:
    def __init__(self, results):
        self._results = list(results)
        self.calls = []
        self.depth = 0
        self._global_subagent_count = 0

    def should_auto_compact(self):
        return False

    def step(self, skip_parse=False):
        self.calls.append(skip_parse)
        return self._results.pop(0)


def test_executor_skip_parse_is_one_shot():
    executor = Executor()
    agent = _DummyAgent(
        [
            StepResult(outputs=["first"], action=Action.CONTINUE),
            StepResult(outputs=["second"], action=Action.WAIT),
        ]
    )
    executor.current_agent = agent
    executor._is_running = True
    executor.arm_skip_next_parse("test")

    list(executor._execute_loop())

    assert agent.calls == [True, False]
    assert executor.is_waiting_for_input() is True


def test_executor_skip_parse_no_residue_after_wait():
    executor = Executor()
    first_agent = _DummyAgent([StepResult(outputs=["first"], action=Action.WAIT)])
    executor.current_agent = first_agent
    executor._is_running = True
    executor.arm_skip_next_parse("test")
    list(executor._execute_loop())
    assert first_agent.calls == [True]

    second_agent = _DummyAgent([StepResult(outputs=["second"], action=Action.WAIT)])
    executor.current_agent = second_agent
    executor._is_running = True
    list(executor._execute_loop())
    assert second_agent.calls == [False]


def test_simple_agent_step_skip_parse_ignores_tool_tags():
    agent = SimpleAgent(init_system_prompt=False)
    agent.start("测试任务")
    agent._call_llm = lambda: ("<ps_call>echo hello</ps_call>", "think")  # type: ignore[attr-defined]

    result = agent.step(skip_parse=True)

    assert result.action == Action.WAIT
    assert result.pending_commands == []
    joined = "".join(result.outputs)
    assert "[已跳过本轮解析]" in joined
    assert "[等待用户输入]" in joined
