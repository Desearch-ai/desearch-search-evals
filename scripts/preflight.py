"""Pre-flight: live-test every credential an agent/benchmark needs, so nothing is
dead before work starts. Run: python3 scripts/preflight.py

Reports ALIVE / DEAD / MISSING per key. The WORKING OpenAI key is read from the
sn22 miner .env (the harness .env's OPENAI_API_KEY is stale on purpose).
"""

from __future__ import annotations

import os
import re
import sys
import urllib.request
import urllib.error
import json
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from providers.common import load_env  # noqa: E402

load_env()  # harness .env -> SCRAPINGDOG/EXA/TAVILY/OPENROUTER/HF/DESEARCH/FETCH_PROXY

MINER_ENV = Path("/Users/mirian/Documents/sn22/neurons/miners/.env")


def working_openai_key() -> str | None:
    if MINER_ENV.exists():
        m = re.search(r"^OPENAI_API_KEY=(.+)$", MINER_ENV.read_text(), re.M)
        if m:
            return m.group(1).strip().strip('"').strip("'")
    return None


def _http(method, url, headers=None, body=None, timeout=25):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read()[:4000]
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:400]
    except Exception as e:
        return None, str(e)[:200].encode()


results = []


def check(name, source, ok, detail):
    results.append((name, source, "ALIVE" if ok else "DEAD", detail))


# --- OpenAI (working key from miner .env): chat + embeddings + judge model ---
oai = working_openai_key()
if not oai:
    results.append(("OPENAI (chat/embed/judge)", "sn22 miner .env", "MISSING", "key not found"))
else:
    H = {"Authorization": f"Bearer {oai}", "Content-Type": "application/json"}
    for model, label in [("gpt-4.1-nano", "gen"), ("gpt-5.4-mini", "judge")]:
        st, b = _http("POST", "https://api.openai.com/v1/chat/completions", H,
                      {"model": model, "messages": [{"role": "user", "content": "ok"}],
                       "max_completion_tokens": 5})
        check(f"OPENAI {model} ({label})", "sn22 miner .env", st == 200, f"HTTP {st}")
    st, b = _http("POST", "https://api.openai.com/v1/embeddings", H,
                  {"model": "text-embedding-3-small", "input": "test"})
    check("OPENAI embeddings", "sn22 miner .env", st == 200, f"HTTP {st}")

# --- ScrapingDog (SERP for build_corpus + miner web search) ---
sd = os.environ.get("SCRAPINGDOG_API_KEY")
if not sd:
    results.append(("SCRAPINGDOG (SERP)", "harness .env", "MISSING", ""))
else:
    st, b = _http("GET", f"https://api.scrapingdog.com/google?api_key={sd}&query=test&results=1")
    ok = st == 200 and b"organic" in b
    check("SCRAPINGDOG (SERP)", "harness .env", ok, f"HTTP {st}" + ("" if ok else " / no organic results"))

# --- Exa (benchmark competitor) ---
exa = os.environ.get("EXA_API_KEY")
if not exa:
    results.append(("EXA", "harness .env", "MISSING", ""))
else:
    st, b = _http("POST", "https://api.exa.ai/search",
                  {"x-api-key": exa, "Content-Type": "application/json"},
                  {"query": "test", "numResults": 1})
    check("EXA", "harness .env", st == 200, f"HTTP {st}")

# --- Tavily (benchmark competitor) ---
tav = os.environ.get("TAVILY_API_KEY")
if not tav:
    results.append(("TAVILY", "harness .env", "MISSING", ""))
else:
    st, b = _http("POST", "https://api.tavily.com/search",
                  {"Content-Type": "application/json"},
                  {"api_key": tav, "query": "test", "max_results": 1})
    check("TAVILY", "harness .env", st == 200, f"HTTP {st}")

# --- OpenRouter (Perplexity sonar in the harness) ---
orouter = os.environ.get("OPENROUTER_API_KEY")
if not orouter:
    results.append(("OPENROUTER (perplexity)", "harness .env", "MISSING", ""))
else:
    st, b = _http("POST", "https://openrouter.ai/api/v1/chat/completions",
                  {"Authorization": f"Bearer {orouter}", "Content-Type": "application/json"},
                  {"model": "perplexity/sonar-pro", "messages": [{"role": "user", "content": "ok"}],
                   "max_tokens": 16})
    check("OPENROUTER (perplexity)", "harness .env", st == 200, f"HTTP {st}")

# --- HF token (dataset push) ---
hf = os.environ.get("HF_TOKEN")
if not hf:
    results.append(("HF_TOKEN", "harness .env", "MISSING", ""))
else:
    st, b = _http("GET", "https://huggingface.co/api/whoami-v2",
                  {"Authorization": f"Bearer {hf}"})
    name = ""
    try:
        name = json.loads(b).get("name", "")
    except Exception:
        pass
    check("HF_TOKEN", "harness .env", st == 200, f"HTTP {st} {name}")

# --- geonode proxy (article fetch) ---
proxy = os.environ.get("FETCH_PROXY")
if not proxy:
    results.append(("FETCH_PROXY (geonode)", "harness .env", "MISSING", ""))
else:
    h = urllib.request.ProxyHandler({"http": proxy, "https": proxy})
    op = urllib.request.build_opener(h)
    try:
        r = op.open("https://api.ipify.org?format=json", timeout=25)
        ip = json.loads(r.read()).get("ip", "")
        check("FETCH_PROXY (geonode)", "harness .env", bool(ip), f"exit IP {ip}")
    except Exception as e:
        check("FETCH_PROXY (geonode)", "harness .env", False, str(e)[:80])

# --- report ---
print(f"\n{'KEY':30} {'SOURCE':20} {'STATUS':8} DETAIL")
print("-" * 78)
dead = 0
for name, src, status, detail in results:
    if status != "ALIVE":
        dead += 1
    print(f"{name:30} {src:20} {status:8} {detail}")
print("-" * 78)
print(f"{'ALL ALIVE ✓' if dead == 0 else f'{dead} NOT ALIVE — fix before starting agents'}")
sys.exit(1 if dead else 0)
