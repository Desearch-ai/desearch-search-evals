"""Bounded OpenRouter judge requests with per-attempt usage records."""

import asyncio
import json
import math
import re

import aiohttp

API = "https://openrouter.ai/api/v1/chat/completions"


def parse_json(content) -> dict:
    """The first JSON object in a model reply, tolerating code fences or stray prose."""
    match = re.search(r"\{.*\}", content or "", re.S)
    try:
        value = json.loads(match.group(0)) if match else None
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def reported_costs(attempts):
    costs = [
        ((a.get("response") or {}).get("usage") or {}).get("cost") for a in attempts
    ]
    known = [
        cost
        for cost in costs
        if isinstance(cost, (int, float))
        and not isinstance(cost, bool)
        and math.isfinite(cost)
        and cost >= 0
    ]
    unknown = len(costs) - len(known) if costs else 1
    return {
        "cost_usd": None if unknown else sum(known),
        "recorded_cost_usd": sum(known),
        "unknown_cost_attempts": unknown,
    }


async def chat(session, key, model, messages, max_tokens=1600, json_mode=True):
    body = {
        "model": model,
        "messages": messages,
        "max_tokens": max_tokens,
        "reasoning": {
            "effort": "low" if model == "google/gemini-3.8-flash" else "none"
        },
    }
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    attempts = []
    for attempt in range(3):
        try:
            async with session.post(
                API,
                headers={"Authorization": f"Bearer {key}"},
                json=body,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as response:
                data = await response.json(content_type=None)
                attempts.append({"status": response.status, "response": data})
                terminal_error = False
                if response.status == 200 and data.get("choices"):
                    choice = data["choices"][0]
                    terminal_error = choice.get("finish_reason") == "error"
                    if not terminal_error:
                        content = choice["message"].get("content") or ""
                        return {
                            "content": content,
                            "request": body,
                            "attempts": attempts,
                            **reported_costs(attempts),
                        }
                if (
                    response.status not in (429, 500, 502, 503, 504)
                    and not terminal_error
                ):
                    break
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as error:
            attempts.append({"error": type(error).__name__})
        if attempt < 2:
            await asyncio.sleep(2**attempt)

    return {
        "error": "request_failed",
        "request": body,
        "attempts": attempts,
        **reported_costs(attempts),
    }
