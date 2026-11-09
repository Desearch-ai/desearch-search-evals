"""Answer each question from each profile's saved top results with one fixed reader, then grade with the benchmark's official prompt."""

import argparse
import asyncio
import json
import os
import random
import re
import time
from datetime import date
from pathlib import Path

import aiohttp

from evaluators.grade import is_mirror
from utils import read_jsonl

TEMPLATES = Path(__file__).resolve().parent.parent / "evaluators" / "templates"
CHUTES = "https://llm.chutes.ai/v1/chat/completions"
OPENROUTER = "https://openrouter.ai/api/v1/chat/completions"
READER = "Qwen/Qwen3.6-27B-TEE"
READER_PRICE = (0.25e-6, 2.0e-6)
GRADERS = {
    "sealqa": ("openai/gpt-4o-mini", "sealqa_grader.txt"),
    "simpleqa_verified": ("openai/gpt-4.1", "simpleqa_verified_grader.txt"),
}
NO_SEARCH = "no-search"
GRADES = {"A": "CORRECT", "B": "INCORRECT", "C": "NOT_ATTEMPTED"}

READER_SYSTEM = """Today is {today}. Answer the question using the search results below. They may be incomplete, outdated or contradict each other: prefer the most recent reliable source, and answer from your own knowledge only when the results do not settle it.

End your reply with exactly these lines:
Exact Answer: <your succinct, final answer>
Confidence: <0-100%>"""
CLOSED_SYSTEM = """Today is {today}. Answer the question from your own knowledge.

End your reply with exactly these lines:
Exact Answer: <your succinct, final answer>
Confidence: <0-100%>"""


def chutes_token():
    token = os.environ.get("CHUTES_API_TOKEN")
    if not token and Path.home().joinpath(".zshrc").exists():
        found = re.search(
            r"CHUTES_API_TOKEN=['\"]?([^'\"\s]+)",
            Path.home().joinpath(".zshrc").read_text(),
        )
        token = found.group(1) if found else None
    if not token:
        raise SystemExit("CHUTES_API_TOKEN is not set")
    return token


def load_env(path):
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            name, _, value = line.partition("=")
            if name.strip() and value and name.strip() not in os.environ:
                os.environ[name.strip()] = value.strip().strip("'\"")


def context(search):
    blocks = []
    for row in search.get("results") or []:
        if is_mirror(row):
            continue
        head = (
            f"[{row.get('rank')}] {row.get('title') or ''}\nURL: {row.get('url') or ''}"
        )
        if row.get("published"):
            head += f"\nDate: {row['published']}"
        blocks.append(f"{head}\n{(row.get('text') or '').strip()}")
    return "\n\n".join(blocks) or "(no results)"


def exact_answer(text):
    found = re.findall(r"Exact Answer:\s*(.+)", text or "")
    return found[-1].strip() if found else ""


class Store:
    def __init__(self, path):
        self.path = path
        self.rows = {}
        if path.exists():
            for row in read_jsonl(path):
                self.rows[row["key"]] = row

    def put(self, row):
        self.rows[row["key"]] = row
        with self.path.open("a") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")


async def post(session, url, token, body, attempts=5):
    error = ""
    for attempt in range(attempts):
        try:
            async with session.post(
                url,
                json=body,
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=300),
            ) as response:
                data = await response.json(content_type=None)
                if response.status == 200 and data.get("choices"):
                    return data
                error = f"HTTP_{response.status}: {str(data)[:200]}"
                if response.status not in (408, 429, 500, 502, 503, 504):
                    break
        except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
            error = f"{type(exc).__name__}: {exc}"
        await asyncio.sleep(min(60, 4 * 2**attempt) + random.random())
    raise RuntimeError(error)


async def read(session, token, question, ctx, today, max_tokens):
    system = (CLOSED_SYSTEM if ctx is None else READER_SYSTEM).format(today=today)
    user = (
        f"Question: {question}"
        if ctx is None
        else f"Search results:\n\n{ctx}\n\nQuestion: {question}"
    )
    body = {
        "model": READER,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
        "max_tokens": max_tokens,
        "temperature": 0.6,
        "top_p": 0.95,
        "chat_template_kwargs": {"enable_thinking": True},
    }
    started = time.monotonic()
    data = await post(session, CHUTES, token, body)
    text = data["choices"][0]["message"].get("content") or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S).strip()
    usage = data.get("usage") or {}
    cost = (
        usage.get("prompt_tokens", 0) * READER_PRICE[0]
        + usage.get("completion_tokens", 0) * READER_PRICE[1]
    )
    return text, {
        "in": usage.get("prompt_tokens"),
        "out": usage.get("completion_tokens"),
        "usd": cost,
        "seconds": time.monotonic() - started,
    }


async def grade(session, key, benchmark, question, target, response):
    model, template = GRADERS[benchmark]
    prompt = (TEMPLATES / template).read_text()
    prompt = (
        prompt.replace("{question}", question)
        .replace("{target}", target)
        .replace("{predicted_answer}", response)
    )
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 5,
        "temperature": 0,
    }
    data = await post(session, OPENROUTER, key, body)
    reply = (data["choices"][0]["message"].get("content") or "").strip()
    letter = re.search(r"\b([ABC])\b", reply)
    return (
        GRADES.get(letter.group(1) if letter else "", "UNPARSED"),
        reply,
        (data.get("usage") or {}).get("cost"),
    )


async def run(args):
    load_env(args.env_file)
    run_dir = Path(args.run)
    questions = list(read_jsonl(args.questions))
    profiles = [
        p["id"] for p in json.loads(Path(args.config).read_text())["profiles"]
    ] + [NO_SEARCH]
    out = run_dir / "answers"
    out.mkdir(parents=True, exist_ok=True)
    answers, grades = Store(out / "answers.jsonl"), Store(out / "grades.jsonl")
    token, key = chutes_token(), os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY is required for grading")
    today = args.today or date.today().isoformat()
    reader_slots, grader_slots = (
        asyncio.Semaphore(args.reader_concurrency),
        asyncio.Semaphore(args.grader_concurrency),
    )
    spend = {"reader": 0.0, "grader": 0.0}
    done = [0]

    async def one(session, question, profile):
        item = f"{profile}/{question['id']}"
        if item not in answers.rows:
            ctx = None
            if profile != NO_SEARCH:
                path = run_dir / "searches" / profile / f"{question['id']}.json"
                search = json.loads(path.read_text())
                ctx = context(search) if not search.get("error") else "(search failed)"
            if spend["reader"] + spend["grader"] >= args.cap:
                return
            async with reader_slots:
                try:
                    text, usage = await read(
                        session,
                        token,
                        question["question"],
                        ctx,
                        today,
                        args.reader_tokens,
                    )
                except RuntimeError as error:
                    print(f"reader failed {item}: {error}", flush=True)
                    return
            spend["reader"] += usage["usd"]
            answers.put(
                {
                    "key": item,
                    "profile": profile,
                    "question_id": question["id"],
                    "response": text,
                    "exact": exact_answer(text),
                    "usage": usage,
                }
            )
        if item not in grades.rows:
            response = answers.rows[item]["response"]
            async with grader_slots:
                try:
                    verdict, reply, cost = await grade(
                        session,
                        key,
                        question["benchmark"],
                        question["question"],
                        question["answer"],
                        response,
                    )
                except RuntimeError as error:
                    print(f"grader failed {item}: {error}", flush=True)
                    return
            spend["grader"] += cost or 0.0
            grades.put(
                {
                    "key": item,
                    "profile": profile,
                    "question_id": question["id"],
                    "grade": verdict,
                    "reply": reply,
                    "cost_usd": cost,
                }
            )
        done[0] += 1
        if done[0] % 200 == 0:
            print(
                f"{done[0]} graded, reader ${spend['reader']:.2f}, grader ${spend['grader']:.2f}",
                flush=True,
            )

    async with aiohttp.ClientSession() as session:
        results = await asyncio.gather(*(one(session, q, p) for q in questions for p in profiles), return_exceptions=True)
        for error in [r for r in results if isinstance(r, Exception)][:5]:
            print(f"task failed: {type(error).__name__}: {error}", flush=True)
    print(
        f"done: {len(grades.rows)} grades, reader ${spend['reader']:.2f}, grader ${spend['grader']:.2f}",
        flush=True,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--questions", required=True)
    parser.add_argument("--config", required=True)
    parser.add_argument("--run", required=True)
    parser.add_argument(
        "--today", help="date the reader is told (default: the run date)"
    )
    parser.add_argument("--reader-tokens", type=int, default=8000)
    parser.add_argument("--reader-concurrency", type=int, default=8)
    parser.add_argument("--grader-concurrency", type=int, default=12)
    parser.add_argument(
        "--cap",
        type=float,
        default=60.0,
        help="stop starting new answers past this spend",
    )
    parser.add_argument("--env-file", default=".env")
    asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    main()
