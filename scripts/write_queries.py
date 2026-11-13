"""Write a search objective and 1-3 keyword queries once per question with the fixed reader, for providers whose docs ask for them (Parallel)."""

import argparse
import asyncio
import json
import re
from pathlib import Path

import aiohttp

from scripts import run_answers as ra
from utils import read_jsonl

PROMPT = """You are preparing one web search for the question below. Do not answer it.

Write:
- "objective": one or two sentences stating what the searcher needs to find and which sources would settle it.
- "search_queries": 1 to 3 keyword queries of 3 to 6 words each, each under 200 characters.

Return only JSON: {{"objective": "...", "search_queries": ["...", "..."]}}

Question: {question}"""


def parse(text):
    found = re.search(r"\{.*\}", text or "", flags=re.S)
    if not found:
        return None
    try:
        data = json.loads(found.group(0))
    except ValueError:
        return None
    queries = [
        q.strip()[:200]
        for q in data.get("search_queries") or []
        if isinstance(q, str) and q.strip()
    ][:3]
    objective = (data.get("objective") or "").strip()
    return (
        {"objective": objective, "search_queries": queries}
        if objective and queries
        else None
    )


async def write(questions, out, concurrency=12):
    token = ra.chutes_token()
    done = json.loads(out.read_text()) if out.exists() else {}
    slots = asyncio.Semaphore(concurrency)

    async def one(session, q):
        if q["question"] in done:
            return
        body = {
            "model": ra.READER,
            "messages": [
                {"role": "user", "content": PROMPT.format(question=q["question"])}
            ],
            "max_tokens": 2000,
            "temperature": 0.6,
            "top_p": 0.95,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        async with slots:
            for _ in range(3):
                try:
                    data = await ra.post(session, ra.CHUTES, token, body)
                except RuntimeError:
                    continue
                written = parse(data["choices"][0]["message"].get("content"))
                if written:
                    done[q["question"]] = written
                    return

    async with aiohttp.ClientSession() as session:
        await asyncio.gather(*(one(session, q) for q in questions))
    out.write_text(json.dumps(done, indent=1, ensure_ascii=False))
    return len(done), len(questions)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", nargs="+", required=True)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--env-file")
    args = parser.parse_args()
    if args.env_file:
        ra.load_env(args.env_file)
    questions = [q for path in args.questions for q in read_jsonl(path)]
    print("written %d of %d" % asyncio.run(write(questions, args.out)))


if __name__ == "__main__":
    main()
