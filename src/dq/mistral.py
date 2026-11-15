"""Low-level Mistral API client (ADR-05).

This is the ONLY module in the codebase that performs HTTP calls to the LLM.
Agents A2/A3/A4 never import it — grep-able proof that the LLM is used only
in A1 (contract drafting) and A5 (issue resolution).
"""

import json
import time
import urllib.error
import urllib.request

from . import config, db


def chat_completion(messages, tools=None, agent="unknown", temperature=0.0):
    """One chat.completions call with optional tool definitions.

    Returns {"content": str|None, "tool_calls": [...]} and logs token usage
    to the llm_calls table (feeds the cost-per-run KPI). Retries on 5xx/timeouts.
    """
    if not config.MISTRAL_API_KEY:
        raise RuntimeError(
            "MISTRAL_API_KEY is not set. Create a key at https://console.mistral.ai "
            "(workspace → API keys) and export it (ADR-12).")

    payload = {"model": config.MODEL, "messages": messages, "temperature": temperature}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        f"{config.MISTRAL_BASE_URL}/chat/completions",
        data=body,
        headers={
            "Authorization": f"Bearer {config.MISTRAL_API_KEY}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    resp = None
    last_err = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                resp = json.load(r)
            break
        except urllib.error.HTTPError as e:
            detail = e.read()[:300].decode("utf-8", "replace")
            if e.code in (400, 401, 403):
                raise RuntimeError(f"Mistral API error {e.code}: {detail}") from e
            last_err = f"HTTP {e.code}: {detail}"
        except (urllib.error.URLError, TimeoutError) as e:
            last_err = str(e)
        time.sleep(2 ** attempt)
    if resp is None:
        raise RuntimeError(f"Mistral API unreachable after 3 attempts: {last_err}")

    msg = resp["choices"][0]["message"]
    usage = resp.get("usage", {})
    _log_call(agent, usage)
    return {"content": msg.get("content"), "tool_calls": msg.get("tool_calls") or []}


def _log_call(agent: str, usage: dict) -> None:
    """Best-effort token logging; never breaks the main flow if it fails."""
    try:
        with db.state() as conn:
            conn.execute(
                "INSERT INTO llm_calls (agent, model, prompt_tokens, completion_tokens,"
                " total_tokens, iterations, at) VALUES (?,?,?,?,?,?,datetime('now'))",
                (agent, config.MODEL, usage.get("prompt_tokens"), usage.get("completion_tokens"),
                 usage.get("total_tokens"), 1),
            )
    except Exception:
        pass
