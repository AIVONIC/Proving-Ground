"""Execution benchmark harness: grade what an agent DOES, not what it says.

A conversational grade sees only the agent's words. This harness puts the agent
in an observable sandbox, hands it real tools, and verifies the *effect*: did the
booking actually get created, did the email actually send. Two axes are scored,
deliberately separated so neither can be gamed alone:

  completion  - did the required effect occur, verified in the sandbox (coverage).
  reliability - did the agent's tool calls execute without malformed/invalid
                arguments (precision). Deterministic, pre-built skills score high
                here honestly; hallucinated function-calls do not.

Agents reach the sandbox through the ToolCallingAgent protocol, so the same tasks
run against our own skill-based agents and any third party that exposes tool use.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol


@dataclass
class Tool:
    """A sandbox tool: the schema the agent sees, and the handler that runs it.

    The handler mutates sandbox ``state`` and returns a result. It MUST raise on
    an invalid call (unknown arg, wrong type, missing required field) so that a
    hallucinated call is recorded as an error, not silently accepted.
    """
    name: str
    schema: dict
    handler: Callable[..., object]


@dataclass
class SandboxToolEnv:
    """An observable world. Every tool call is recorded with its outcome."""
    state: dict = field(default_factory=dict)
    calls: list[dict] = field(default_factory=list)
    _tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def call(self, name: str, args: dict) -> dict:
        rec = {"tool": name, "args": dict(args or {}), "ok": False, "error": None, "result": None}
        try:
            tool = self._tools.get(name)
            if tool is None:
                raise KeyError(f"unknown tool {name!r}")
            rec["result"] = tool.handler(self.state, **(args or {}))
            rec["ok"] = True
        except Exception as e:  # a bad call is data, never a crash
            rec["error"] = f"{type(e).__name__}: {e}"
        self.calls.append(rec)
        return rec

    @property
    def error_rate(self) -> float:
        if not self.calls:
            return 0.0
        return sum(1 for c in self.calls if not c["ok"]) / len(self.calls)


@dataclass
class ExecutionTask:
    """One task: a request, the tools offered, world setup, and an effect check."""
    id: str
    prompt: str
    tools: list[Tool]
    setup: Callable[[dict], None] = lambda state: None
    success: Callable[[SandboxToolEnv], float] = lambda env: 0.0  # verified effect -> 0..1
    max_steps: int = 6
    optimal_steps: int = 1

    def agent_tools(self) -> list[dict]:
        return [t.schema for t in self.tools]


class ToolCallingAgent(Protocol):
    """Anything that can take an action given the task and prior tool results.

    ``act`` returns either {"tool": name, "args": {...}} to call a tool, or
    {"final": text} to stop. Our skill agents, a function-calling wrapper, or a
    third party's tool API all implement this one method.
    """
    def act(self, prompt: str, tools: list[dict], transcript: list[dict]) -> Awaitable[dict]:
        ...


@dataclass
class ExecResult:
    task_id: str
    completion: float     # verified effect, 0..1
    reliability: float    # 1 - tool-call error rate
    steps: int
    score: float          # combined, 0..1
    calls: list[dict]


class ExecutionDimension:
    """Run tool-using tasks against an agent and score verified effects."""
    id = "execution"

    def __init__(self, reliability_weight: float = 0.2):
        # completion dominates; reliability is a modifier so an agent cannot score
        # by fumbling through a task with many failed calls that happens to end right.
        self.rw = reliability_weight

    async def run_task(self, agent: ToolCallingAgent, task: ExecutionTask) -> ExecResult:
        env = SandboxToolEnv()
        task.setup(env.state)
        for t in task.tools:
            env.register(t)
        transcript: list[dict] = []
        for _ in range(task.max_steps):
            action = await agent.act(task.prompt, task.agent_tools(), transcript)
            if not action or "final" in action:
                break
            rec = env.call(action.get("tool", ""), action.get("args", {}))
            transcript.append({"action": action, "result": rec})
        completion = max(0.0, min(1.0, float(task.success(env))))
        reliability = 1.0 - env.error_rate
        score = completion * ((1.0 - self.rw) + self.rw * reliability)
        return ExecResult(task.id, round(completion, 3), round(reliability, 3),
                          len(env.calls), round(score, 3), env.calls)

    async def run(self, agent: ToolCallingAgent, tasks: list[ExecutionTask]) -> list[ExecResult]:
        return [await self.run_task(agent, t) for t in tasks]

    @staticmethod
    def subscore(results: list[ExecResult]) -> float:
        if not results:
            return 0.0
        return round(10.0 * sum(r.score for r in results) / len(results), 2)
