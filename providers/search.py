import os

from . import (
    desearch_api,
    desearch_index,
    exa,
    openrouter,
    parallel,
    perplexity,
    serper,
    tavily,
)

ADAPTERS = {
    "desearch_api": desearch_api,
    "desearch_index": desearch_index,
    "exa": exa,
    "parallel": parallel,
    "serper": serper,
    "perplexity": perplexity,
    "tavily": tavily,
}
KEYS = {
    "desearch_api": "DESEARCH_API_KEY",
    "desearch_index": "DESEARCH_INDEX_KEY",
    "exa": "EXA_API_KEY",
    "parallel": "PARALLEL_API_KEY",
    "serper": "SERPER_API_KEY",
    "perplexity": "OPENROUTER_API_KEY",
    "tavily": "TAVILY_API_KEY",
}


def validate_profile(profile):
    """Every profile calls its provider's own API, except Perplexity, which goes through OpenRouter."""
    transport = profile.get("transport")
    if transport == "desearch_index":
        provider = "desearch_index"
        desearch_index.validate_profile(profile)
    elif transport == "desearch_api":
        provider = "desearch_api"
        desearch_api.validate_profile(profile)
    elif transport == "exa":
        provider = "exa"
        exa.validate_profile(profile)
    elif transport == "parallel":
        provider = "parallel"
        parallel.validate_profile(profile)
    elif transport == "openrouter":
        provider = profile.get("engine")
        if provider != "perplexity":
            raise ValueError(f"Unsupported OpenRouter search engine: {provider}")
        openrouter.build_request("Validate search profile", profile)
    elif transport == "serper":
        provider = "serper"
        serper.validate_profile(profile)
    elif transport == "tavily":
        provider = "tavily"
        tavily.validate_profile(profile)
    elif transport == "local":
        provider = "desearch"
        if profile.get("engine", provider) != provider:
            raise ValueError("Local search profiles require engine=desearch")
        if profile.get("mode") not in {"fast", "standard"}:
            raise ValueError("Unsupported local Desearch search mode")
    else:
        raise ValueError(f"Unsupported search transport: {transport}")
    if profile.get("provider", provider) != provider:
        raise ValueError("Profile provider does not match its search transport")
    return provider


async def search(session, question, profile):
    provider = validate_profile(profile)
    if provider == "desearch":
        raise ValueError("Local Desearch results must be imported by the search runner")
    if not isinstance(question, str) or not question.strip():
        raise ValueError("A nonempty question is required")
    key = os.environ.get(KEYS[provider])
    if not key:
        raise RuntimeError(f"{KEYS[provider]} is not set")
    return await ADAPTERS[provider].search(session, question, profile, key)
