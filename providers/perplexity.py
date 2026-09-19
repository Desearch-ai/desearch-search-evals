from . import openrouter


async def search(session, question, profile, api_key):
    if profile.get("engine") != "perplexity":
        raise ValueError("The Perplexity search adapter requires engine=perplexity")
    if profile.get("transport", "openrouter") != "openrouter":
        raise ValueError("The Perplexity search adapter requires OpenRouter transport")
    return await openrouter.search(session, question, profile, api_key)
