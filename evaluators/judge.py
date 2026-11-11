"""Bounded OpenRouter judge requests with per-attempt usage records."""

import asyncio
import hashlib
import json
import math
import os
import re

import aiohttp

API = "https://openrouter.ai/api/v1/chat/completions"
OPENAI_API = "https://api.openai.com/v1/chat/completions"
PRICES = {"gpt-5.4-mini": (0.75e-6, 0.075e-6, 4.5e-6)}


def openai_direct(model) -> bool:
    """Whether an openai/ model goes straight to OpenAI: GRADE_VIA=openai."""
    return model.startswith("openai/") and os.environ.get("GRADE_VIA") == "openai"


def openai_body(body) -> dict:
    """An OpenRouter chat body as OpenAI's API takes it."""
    out = {k: v for k, v in body.items() if k not in ("reasoning", "max_tokens")}
    out["model"] = body["model"].split("/", 1)[1]
    if "max_tokens" in body:
        out["max_completion_tokens"] = body["max_tokens"]
    reasoning = body.get("reasoning") or {}
    effort = "none" if reasoning.get("enabled") is False else reasoning.get("effort")
    if effort:
        out["reasoning_effort"] = effort
    if effort not in (None, "none"):
        out.pop("temperature", None)
    messages = body.get("messages") or [{}]
    prefix = str(messages[0].get("content", ""))[:1024]
    out["prompt_cache_key"] = hashlib.sha1(prefix.encode()).hexdigest()[:16]
    return out


def priced(model, usage) -> float | None:
    """A call's USD cost from its token usage, for models in PRICES."""
    price = PRICES.get(model.split("/")[-1])
    if not price or not usage:
        return None
    cached = (usage.get("prompt_tokens_details") or {}).get("cached_tokens") or 0
    fresh = usage.get("prompt_tokens", 0) - cached
    return (
        fresh * price[0]
        + cached * price[1]
        + usage.get("completion_tokens", 0) * price[2]
    )


def route(model, key, body) -> tuple[str, str, dict]:
    """The URL, key and body for a chat call: OpenAI direct or OpenRouter."""
    if openai_direct(model):
        return OPENAI_API, os.environ.get("OPENAI_API_KEY", ""), openai_body(body)
    return API, key, body


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
    url, auth, sent = route(model, key, body)
    for attempt in range(3):
        try:
            async with session.post(
                url,
                headers={"Authorization": f"Bearer {auth}"},
                json=sent,
                timeout=aiohttp.ClientTimeout(total=120),
            ) as response:
                data = await response.json(content_type=None)
                usage = data.get("usage") if isinstance(data, dict) else None
                if url == OPENAI_API and isinstance(usage, dict):
                    usage["cost"] = priced(model, usage)
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
