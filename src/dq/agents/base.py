"""Agent base class: persona + tools + bounded LLM tool-calling loop.

An agent is an ephemeral object created per task — not a daemon. run() returns the
final answer and the object is discarded; all durable state lives in dq.db / files
(ADR-11). The loop is bounded by config.MAX_TOOL_ITERATIONS as a safety net.
Only A1 and A5 use this loop; A2–A4 override run() with deterministic code and
never touch the LLM (mistral.py is imported only here).
"""

import json

from .. import config, mistral, tools as toolkit


class Agent:
    def __init__(self, name: str, system_prompt: str, tool_names: list):
        self.name = name
        self.system_prompt = system_prompt
        self.tools = [toolkit.DEFINITIONS[n] for n in tool_names]

    def run(self, task: str, context: dict | None = None) -> str:
        context = context or {}
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "user", "content": task},
        ]
        for _ in range(config.MAX_TOOL_ITERATIONS):
            resp = mistral.chat_completion(messages, tools=self.tools,
                                           agent=self.name, temperature=0.0)
            if not resp["tool_calls"]:
                return resp["content"] or ""
            messages.append({"role": "assistant", "content": resp["content"],
                             "tool_calls": resp["tool_calls"]})
            for tc in resp["tool_calls"]:
                fname = tc["function"]["name"]
                try:
                    args = json.loads(tc["function"].get("arguments") or "{}")
                except json.JSONDecodeError:
                    args = {}
                result = toolkit.execute(fname, args, context)
                messages.append({"role": "tool", "tool_call_id": tc["id"],
                                 "name": fname, "content": result[:4000]})
        raise RuntimeError(
            f"{self.name}: exceeded {config.MAX_TOOL_ITERATIONS} tool iterations — aborting")
