"""Generate X (Twitter) benchmark questions from fresh tweets via the desearch API."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import random
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

from utils import is_contaminated, load_env  # noqa: E402

_ROOT_ENV = HERE.parent / ".env"
load_env(_ROOT_ENV if _ROOT_ENV.exists() else None)

import x_sources  # noqa: E402
from hf_schema import build_row, validate_row  # noqa: E402
from openai import (  # noqa: E402
    APIConnectionError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)

EMBED_MODEL = "text-embedding-3-small"
DEDUP_COSINE = float(os.environ.get("DEDUP_COSINE", "0.86"))
DESEARCH_BASE_URL = os.environ.get("DESEARCH_BASE_URL", "https://api.desearch.ai")

QUESTIONS_PER_TWEET = 4
_MAX_GOLD_WORDS = {"short": 8, "explanatory": 45, "summary": 90}

MIN_FOLLOWERS = 500
MIN_VIEWS = 100
MIN_TWEET_CHARS = 80
MAX_HASHTAGS = 6

TW_FMT = "%a %b %d %H:%M:%S %z %Y"
OP_FMT = "%Y-%m-%d_%H:%M:%S_UTC"
ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"

_RETRY_ERRS = (RateLimitError, APITimeoutError, APIConnectionError, InternalServerError)
_FATAL_ERRS = (AuthenticationError, BadRequestError)


GEN_MODEL = GRADE_MODEL = ""
EMBED_BACKEND = "openai"
_LLM_BASE_URL = _LLM_KEY = None
_CHUTES = False
_client: AsyncOpenAI | None = None
_LLM_SEM: asyncio.Semaphore | None = None


def _configure_provider(provider: str) -> None:
    global \
        GEN_MODEL, \
        GRADE_MODEL, \
        EMBED_BACKEND, \
        _LLM_BASE_URL, \
        _LLM_KEY, \
        _CHUTES, \
        _client
    provider = (provider or "openai").lower()
    _CHUTES = provider == "chutes"
    if _CHUTES:
        GEN_MODEL = os.environ.get("GEN_MODEL", "Qwen/Qwen3.6-27B-TEE")
        _LLM_BASE_URL = os.environ.get("CHUTES_BASE_URL", "https://llm.chutes.ai/v1")
        _LLM_KEY = os.environ.get("CHUTES_API_TOKEN") or os.environ.get(
            "CHUTES_API_KEY"
        )
        EMBED_BACKEND = os.environ.get("EMBED_BACKEND", "lexical")
    else:
        GEN_MODEL = os.environ.get("GEN_MODEL", "gpt-4.1-nano")
        _LLM_BASE_URL = os.environ.get("OPENAI_BASE_URL")
        _LLM_KEY = os.environ.get("OPENAI_API_KEY")
        EMBED_BACKEND = os.environ.get("EMBED_BACKEND", "openai")
    GRADE_MODEL = os.environ.get("GRADE_MODEL", GEN_MODEL)
    _client = None


_configure_provider(os.environ.get("LLM_PROVIDER", "openai"))


def oai() -> AsyncOpenAI:
    global _client
    if _client is None:
        kw: dict = {
            "timeout": 30.0,
            "max_retries": 0,
        }
        if _LLM_KEY:
            kw["api_key"] = _LLM_KEY
        if _LLM_BASE_URL:
            kw["base_url"] = _LLM_BASE_URL
        _client = AsyncOpenAI(**kw)
    return _client


async def _chat(
    messages: list[dict],
    model: str,
    temperature: float,
    max_tokens: int,
    tries: int = 6,
) -> str:
    """One chat call, retried on transient errors and empty content."""
    kw: dict = {}
    if _CHUTES:
        kw["extra_body"] = {"chat_template_kwargs": {"enable_thinking": False}}
    for i in range(tries):
        try:
            async with _LLM_SEM:
                resp = await oai().chat.completions.create(
                    model=model,
                    messages=messages,
                    temperature=temperature,
                    max_tokens=max_tokens,
                    **kw,
                )
            content = resp.choices[0].message.content or ""
            if content.strip():
                return content
        except _FATAL_ERRS:
            raise
        except _RETRY_ERRS:
            pass
        except (httpx.TimeoutException, httpx.TransportError):
            pass
        except Exception as e:
            if i == 0:
                print(f"  [chat retry] {type(e).__name__}: {str(e)[:120]}", flush=True)
        if i < tries - 1:
            await asyncio.sleep(min(2**i, 20) + random.random())
    return ""


def _parse_json_array(text: str) -> list[dict]:
    m = re.search(r"\[.*\]", text or "", re.DOTALL)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
        return [x for x in arr if isinstance(x, dict)]
    except Exception:
        return []


class Desearch:
    def __init__(
        self, api_key: str, base_url: str = DESEARCH_BASE_URL, max_conn: int = 64
    ):
        self.base = base_url.rstrip("/")
        self._c = httpx.AsyncClient(
            headers={"Authorization": api_key},
            timeout=httpx.Timeout(120.0),
            limits=httpx.Limits(
                max_connections=max_conn, max_keepalive_connections=max_conn
            ),
        )

    async def aclose(self):
        await self._c.aclose()

    async def search(
        self,
        query: str,
        sort: str = "Latest",
        count: int = 20,
        lang: str | None = None,
        min_likes: int | None = None,
        tries: int = 4,
    ) -> list[dict]:
        params: dict = {"query": query, "sort": sort, "count": count}
        if lang:
            params["lang"] = lang
        if min_likes:
            params["min_likes"] = min_likes
        for i in range(tries):
            try:
                r = await self._c.get(f"{self.base}/twitter", params=params)
            except (httpx.TimeoutException, httpx.TransportError):
                await asyncio.sleep(min(2**i, 15) + random.random())
                continue
            if r.status_code == 200:
                data = r.json()
                if isinstance(data, list):
                    return data
                if isinstance(data, dict):
                    for k in ("tweets", "data", "results", "miner_tweets"):
                        if isinstance(data.get(k), list):
                            return data[k]
                return []
            if r.status_code in (429, 500, 502, 503, 504):
                await asyncio.sleep(min(2**i, 15) + random.random())
                continue
            return []
        return []


def _parse_created(s: str) -> datetime | None:
    try:
        return datetime.strptime(s, TW_FMT)
    except (ValueError, TypeError):
        return None


def _op(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(OP_FMT)


_T_CO = re.compile(r"\s*https?://t\.co/\S+")
_SPAM_RE = re.compile(
    r"(?i)\b(giveaway|airdrop|free\s+mint|whitelist|presale|pre-sale|"
    r"pump|moon|dm me|join now|link in bio|click the link|t\.me/|"
    r"telegram\.me|claim your|tag \d+ friends)\b"
)


def _clean_tweet(text: str) -> str:
    return _T_CO.sub("", re.sub(r"\s+", " ", text or "")).strip()


def _good_tweet(t: dict) -> bool:
    if t.get("is_retweet"):
        return False
    text = (t.get("text") or "").strip()
    if len(text) < MIN_TWEET_CHARS or text.count("#") > MAX_HASHTAGS:
        return False
    if _SPAM_RE.search(text):
        return False
    vc = t.get("view_count")
    if vc is not None and vc < MIN_VIEWS:
        return False
    u = t.get("user") or {}
    if (u.get("followers_count") or 0) < MIN_FOLLOWERS:
        return False
    return True


def _record(t: dict, day: str) -> dict | None:
    u = t.get("user") or {}
    dt = _parse_created(t.get("created_at", ""))
    url = t.get("url")
    if dt is None or not url:
        return None
    handle = (u.get("username") or "unknown").lower()
    return {
        "url": url,
        "text": _clean_tweet(t.get("text") or ""),
        "published": dt.astimezone(timezone.utc).strftime(ISO_FMT),
        "source": f"x:{handle}",
        "author": handle,
        "verified": bool(u.get("verified") or u.get("is_blue_verified")),
        "day": day,
    }


async def _harvest_query(
    client: Desearch,
    base_query: str,
    start: datetime,
    end: datetime,
    per_query: int,
    count: int,
    sort: str,
    lang: str | None,
    min_likes: int | None,
    sleep: float,
) -> list[dict]:
    """Walk one query backward over [start, end] via the until: cursor."""
    seen: dict[str, dict] = {}
    cursor = end
    stagnant = 0
    while len(seen) < per_query and cursor > start:
        q = f"{base_query} since:{_op(start)} until:{_op(cursor)}"
        tweets = await client.search(
            q, sort=sort, count=count, lang=lang, min_likes=min_likes
        )
        if not tweets:
            break
        new = 0
        oldest = cursor
        for t in tweets:
            dt = _parse_created(t.get("created_at", ""))
            if dt is None:
                continue
            if dt < oldest:
                oldest = dt
            if dt < start or dt > end:
                continue
            tid = str(t.get("id") or t.get("url"))
            if tid not in seen:
                seen[tid] = t
                new += 1
        nxt = oldest - timedelta(seconds=1)
        if nxt >= cursor:
            break
        cursor = nxt
        stagnant = stagnant + 1 if new == 0 else 0
        if stagnant >= 2:
            break
        if sleep:
            await asyncio.sleep(sleep)
    return list(seen.values())


async def harvest_days(
    client: Desearch,
    queries: list[dict],
    windows: list[tuple[str, datetime, datetime]],
    args,
) -> dict[str, list[dict]]:
    """Harvest every (day, query) pair concurrently."""
    sem = asyncio.Semaphore(args.harvest_concurrency)
    out: dict[str, list[dict]] = {label: [] for label, _, _ in windows}
    seen: dict[str, set] = {label: set() for label, _, _ in windows}
    total = len(windows) * len(queries)
    done = 0

    async def one(label: str, start: datetime, end: datetime, qd: dict) -> None:
        nonlocal done
        ml = None if qd["kind"] == "account" else args.min_likes
        async with sem:
            tweets = await _harvest_query(
                client,
                qd["query"],
                start,
                end,
                args.per_query,
                args.count,
                args.sort,
                args.lang,
                ml,
                args.api_sleep,
            )
        for t in tweets:
            if not _good_tweet(t):
                continue
            tid = str(t.get("id") or t.get("url"))
            if tid in seen[label]:
                continue
            seen[label].add(tid)
            rec = _record(t, label)
            if rec:
                out[label].append(rec)
        done += 1
        if done % 100 == 0 or done == total:
            got = sum(len(v) for v in out.values())
            print(
                f"[harvest] {done}/{total} query-days -> {got} usable tweets",
                flush=True,
            )

    await asyncio.gather(
        *[one(label, s, e, qd) for label, s, e in windows for qd in queries]
    )
    for label in out:
        random.shuffle(out[label])
        out[label] = out[label][: args.max_tweets]
    return out


GEN_PROMPT = """You create benchmark questions for testing an AI SEARCH engine over X/Twitter on RECENT events.
Given ONE tweet (its text, author handle, and date), write a DIVERSE MIX of up to {k} questions whose answer the tweet directly supports. Quality over quantity — return [] if the tweet is not a concrete, verifiable, widely-reported public development.

ONLY mint questions when the tweet reports a CONCRETE PUBLIC development that MANY accounts/outlets would also cover: an announcement, decision, launch, deal, appointment, result, data release, incident, or an official statement from a recognized organization or public figure. This is where X is authoritative.
NEVER mint questions from: personal opinions / hot-takes, motivational or promotional posts, giveaways / marketing, vague commentary, memes, replies that need missing context, or anything answerable only from this one obscure tweet.

Vary the ANSWER SHAPE across the questions:
- "short": a single factoid; gold_answer is one crisp fact <=6 words (who / what / when / how-many / where / which).
- "explanatory": a why / how question; gold_answer is a focused 1-3 sentence explanation built from the tweet's concrete facts.
- "summary": asks what was announced / what changed / how someone responded; gold_answer is a faithful 2-5 sentence synthesis of concrete facts (names, numbers, dates, outcomes).

HARD RULES (apply to EVERY question):
- SELF-CONTAINED: stands alone for someone who never saw the tweet. NEVER write "the tweet / this post / the thread / the author / according to @...". Name the specific people, organizations, products, places.
- NO DATE/TIME IN THE QUESTION: never put a year, month, calendar date, or relative time ("recently", "this week", "in late June 2026", "today", "currently") in the QUESTION text — the dataset supplies the date range separately. Ask only about the entities, facts, and outcomes. (A date may still be the gold_answer for a "when" question.)
- PUBLIC & CORROBORATED: about a real, widely-reported development from the LAST FEW DAYS that other sources also cover — NOT a private individual, NOT a personal anecdote, NOT a quote-recall of a non-famous person.
- GROUNDED: only what THIS tweet states; quote the supporting text in answer_span.
- UNIQUE FOCUS: targets one specific event/topic so a search engine knows exactly what to answer.

ANSWER-LENGTH by type: short <=6 words (never a sentence, never restate the question); explanatory 1-3 sentences; summary 2-5 sentences.

Return ONLY a JSON array (0 to {k} objects), each:
{{"question": str, "gold_answer": str, "answer_span": str, "difficulty": "easy"|"medium"|"hard", "answer_type": "short"|"explanatory"|"summary", "qtype": str}}

Author: @{author}{verified_note}
Posted: {published}
Tweet:
{text}"""

GRADE_PROMPT = """You screen candidate questions for an AI SEARCH benchmark answered over X/Twitter and the open web. A question is GOOD only if ALL hold:
1. SELF-CONTAINED: stands alone, names the specific entities, never references "the tweet / this post / the thread / the author / @..." or "is/are mentioned".
2. PUBLIC & ANSWERABLE: about a real, widely-reported recent development (named public figures, governments, companies, agencies, teams, named products/events) that a search engine could answer WITHOUT the original tweet. NOT a private person, NOT a personal anecdote, NOT a promo/giveaway, NOT an opinion or prediction.
3. UNIQUE ANSWER: exactly one answer is correct across the open web; reject vague "which coin/team/app" with many candidates.
4. APPROPRIATE ANSWER (each Q is tagged [short]/[explanatory]/[summary]): [short] = one crisp fact <=6 words, no question-restating; [explanatory] = a focused 1-3 sentence reason from concrete facts; [summary] = a faithful 2-5 sentence synthesis of concrete facts. Any type: specific, never vague, never opinion, never invented.
5. RECENT: about a development from the last few days, not a historical fact mentioned in passing.

Return ONLY a JSON array, one object per question: {{"i": <index int>, "keep": true|false}}.

{listing}"""

_BANNED_RE = re.compile(
    r"(?i)\b(the|this)\s+(tweet|post|thread|reply|account|user|author|poster|tweeter|article|report)\b"
    r"|according to (the|this)\s+(tweet|post|thread|account|user|author)\b"
    r"|\bin (the|this) (tweet|post|thread)\b"
    r"|\b(tweets?|tweeted|retweets?|retweeted|reposts?|reposted)\b"
    r"|\bis mentioned\b|\bare mentioned\b"
    r"|\bgiveaway\b|\bairdrop\b|\bsubscribe\b|\bfollow (me|us)\b"
)
_STALE_YEAR_RE = re.compile(r"\b(19\d\d|20[0-1]\d|202[0-4])\b")


def _bad_gold(question: str, gold: str, answer_type: str = "short") -> bool:
    words = gold.split()
    cap = _MAX_GOLD_WORDS.get(answer_type, 8)
    if not words or len(words) > cap:
        return True
    if answer_type == "short":
        ql = [w.lower().strip(".,") for w in question.split()]
        gl = [w.lower().strip(".,") for w in words]
        for i in range(len(gl) - 2):
            tri = gl[i : i + 3]
            for j in range(len(ql) - 2):
                if ql[j : j + 3] == tri:
                    return True
        if re.fullmatch(
            r"(?i)(mon|tues|wednes|thurs|fri|satur|sun)day\.?", gold.strip()
        ):
            return True
    elif len(words) < 6:
        return True
    if re.search(r"(?i)\b(maybe|roughly|various|unclear)\b", gold) and len(words) <= 3:
        return True
    return False


async def gen_for_tweet(rec: dict, k: int) -> list[dict]:
    prompt = GEN_PROMPT.format(
        k=k,
        author=rec["author"],
        published=rec["published"],
        text=rec["text"],
        verified_note=" (verified account)" if rec.get("verified") else "",
    )
    try:
        raw = _parse_json_array(
            await _chat([{"role": "user", "content": prompt}], GEN_MODEL, 0.5, 1500)
        )
    except Exception as e:
        print(f"  [gen fail] {type(e).__name__}: {e}")
        return []
    qs = []
    for o in raw:
        q = str(o.get("question", "")).strip()
        gold = str(o.get("gold_answer", "")).strip()
        if not q or not gold or len(q) < 15:
            continue
        diff = str(o.get("difficulty", "medium")).lower()
        if diff not in ("easy", "medium", "hard"):
            diff = "medium"
        atype = str(o.get("answer_type", "short")).lower()
        if atype not in ("short", "explanatory", "summary"):
            atype = "short"
        qs.append(
            {
                "question": q,
                "gold_answer": gold,
                "answer_span": str(o.get("answer_span", "")).strip()[:1200],
                "difficulty": diff,
                "answer_type": atype,
                "qtype": str(o.get("qtype", "")).strip()[:40],
                "source_url": rec["url"],
                "source": rec["source"],
                "published": rec["published"],
                "day": rec["day"],
            }
        )
    return qs


async def gen_all(records: list[dict], k: int, workers: int) -> list[dict]:
    """Invert every tweet via a streaming worker pool."""
    queue: asyncio.Queue = asyncio.Queue()
    for r in records:
        queue.put_nowait(r)
    out: list[dict] = []
    total = len(records)
    done = 0

    async def worker() -> None:
        nonlocal done
        while True:
            try:
                r = queue.get_nowait()
            except asyncio.QueueEmpty:
                return
            qs = await gen_for_tweet(r, k)
            out.extend(qs)
            done += 1
            if done % 2000 == 0 or done == total:
                print(f"[gen] {done}/{total} tweets -> {len(out)} raw qs", flush=True)

    await asyncio.gather(*[worker() for _ in range(min(workers, total))])
    return out


_st_embedder = None


def _local_embedder():
    global _st_embedder
    if _st_embedder is None:
        from sentence_transformers import SentenceTransformer

        _st_embedder = SentenceTransformer("BAAI/bge-small-en-v1.5")
    return _st_embedder


async def embed(texts: list[str], batch: int = 256) -> list[list[float]]:
    if EMBED_BACKEND == "local":
        m = _local_embedder()
        return await asyncio.to_thread(
            lambda: m.encode(
                texts,
                batch_size=256,
                normalize_embeddings=True,
                convert_to_numpy=True,
                show_progress_bar=False,
            ).tolist()
        )
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        chunk = texts[i : i + batch]
        async with _LLM_SEM:
            resp = await oai().embeddings.create(model=EMBED_MODEL, input=chunk)
        out.extend(d.embedding for d in resp.data)
    return out


def _lexical_dedup(questions: list[dict], jaccard: float = 0.8) -> list[dict]:
    kept: list[dict] = []
    sigs: list[set] = []
    for q in questions:
        toks = set(re.sub(r"[^a-z0-9 ]", "", q["question"].lower()).split())
        if not toks:
            continue
        if any(len(toks & s) / (len(toks | s) or 1) >= jaccard for s in sigs):
            continue
        kept.append(q)
        sigs.append(toks)
    return kept


async def dedup(questions: list[dict]) -> list[dict]:
    if not questions:
        return []
    if EMBED_BACKEND == "lexical":
        return _lexical_dedup(questions)
    import numpy as np

    try:
        embs = await embed([q["question"] for q in questions])
    except Exception as e:
        print(
            f"[dedup] embedding failed ({type(e).__name__}); lexical fallback",
            flush=True,
        )
        return _lexical_dedup(questions)
    vecs = np.asarray(embs, dtype=np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9
    kept: list[dict] = []
    kept_mat = np.zeros((len(questions), vecs.shape[1]), dtype=np.float32)
    n = 0
    for q, v in zip(questions, vecs):
        if n and float((kept_mat[:n] @ v).max()) >= DEDUP_COSINE:
            continue
        kept.append(q)
        kept_mat[n] = v
        n += 1
    return kept


async def quality_grade(questions: list[dict], batch: int = 20) -> list[dict]:
    """Batched LLM screen."""
    if not questions:
        return []

    async def grade_batch(start: int, chunk: list[dict]) -> set[int]:
        listing = "\n".join(
            f"{i}. [{q.get('answer_type', 'short')}] Q: {q['question']}  A: {q['gold_answer']}"
            for i, q in enumerate(chunk)
        )
        try:
            verdicts = _parse_json_array(
                await _chat(
                    [{"role": "user", "content": GRADE_PROMPT.format(listing=listing)}],
                    GRADE_MODEL,
                    0.0,
                    1500,
                )
            )
            return {
                start + int(v["i"])
                for v in verdicts
                if isinstance(v, dict) and v.get("keep") is True and "i" in v
            }
        except Exception as e:
            print(f"  [grade keep-all] {type(e).__name__}: {e}")
            return set(range(start, start + len(chunk)))

    results = await asyncio.gather(
        *[
            grade_batch(s, questions[s : s + batch])
            for s in range(0, len(questions), batch)
        ]
    )
    keep = set().union(*results) if results else set()
    return [q for i, q in enumerate(questions) if i in keep]


def _author_family(source: str) -> str:
    return source.split(":", 1)[-1]


def balance(
    questions: list[dict],
    target: int,
    per_author_frac: float = 0.08,
    per_tweet: int = 3,
) -> list[dict]:
    """Per-author + per-tweet diversity caps, shuffled."""
    random.shuffle(questions)
    author_cap = max(8, int(target * per_author_frac))
    by_author: dict[str, int] = defaultdict(int)
    by_tweet: dict[str, int] = defaultdict(int)
    out = []
    for q in questions:
        a = _author_family(q["source"])
        if by_author[a] >= author_cap or by_tweet[q["source_url"]] >= per_tweet:
            continue
        out.append(q)
        by_author[a] += 1
        by_tweet[q["source_url"]] += 1
    random.shuffle(out)
    return out


def _qid(question: str) -> str:
    return "q" + hashlib.sha1(question.strip().lower().encode()).hexdigest()[:12]


def load_existing(out_dir: Path, date: str) -> list[dict]:
    gpath = out_dir / "golds" / f"{date}.jsonl"
    if not gpath.exists():
        return []
    out = []
    for line in gpath.open():
        g = json.loads(line)
        out.append(
            {
                "question": g["question"],
                "gold_answer": g["gold_answer"],
                "answer_span": g.get("answer_span", ""),
                "difficulty": g.get("difficulty", "medium"),
                "answer_type": g.get("answer_type", "short"),
                "qtype": g.get("qtype", ""),
                "source_url": g.get("source_url", g.get("source", "")),
                "source": g.get("source", "x:unknown"),
                "published": g.get("published", ""),
                "day": date,
            }
        )
    return out


def save_local(
    out_dir: Path, date: str, questions: list[dict], window_days: int = 7
) -> tuple[Path, Path]:
    qdir = out_dir / "questions"
    gdir = out_dir / "golds"
    qdir.mkdir(parents=True, exist_ok=True)
    gdir.mkdir(parents=True, exist_ok=True)
    qpath = qdir / f"{date}.jsonl"
    gpath = gdir / f"{date}.jsonl"
    with qpath.open("w") as qf, gpath.open("w") as gf:
        for q in questions:
            qid = _qid(q["question"])
            row = build_row(
                qid, q["question"], q["difficulty"], q["published"], window_days
            )
            validate_row(row)
            qf.write(json.dumps(row, ensure_ascii=False) + "\n")
            gf.write(
                json.dumps(
                    {
                        "id": qid,
                        "question": q["question"],
                        "gold_answer": q["gold_answer"],
                        "answer_span": q["answer_span"],
                        "answer_type": q["answer_type"],
                        "difficulty": q["difficulty"],
                        "qtype": q["qtype"],
                        "source": q["source"],
                        "source_url": q["source_url"],
                        "published": q["published"],
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
    return qpath, gpath


def _windows(args) -> list[tuple[str, datetime, datetime]]:
    """Build the (label, start, end) day-windows to harvest."""
    if args.days:
        anchor = (
            datetime.strptime(args.end_day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if args.end_day
            else datetime.now(timezone.utc)
        )
        anchor = anchor.replace(hour=0, minute=0, second=0, microsecond=0)
        out = []
        for i in range(1, args.days + 1):
            d = anchor - timedelta(days=i)
            out.append(("x-" + d.strftime("%Y-%m-%d"), d, d + timedelta(days=1)))
        return out
    if args.day:
        d = datetime.strptime(args.day, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return [(args.date or ("x-" + args.day), d, d + timedelta(days=1))]
    end = datetime.now(timezone.utc)
    return [
        (
            args.date or ("x-" + end.strftime("%Y-%m-%d")),
            end - timedelta(hours=args.window_hours),
            end,
        )
    ]


def _filter(raw: list[dict]) -> list[dict]:
    return [
        q
        for q in raw
        if not is_contaminated(q["source_url"], q["question"], q["gold_answer"])
        and 15 <= len(q["question"]) <= 300
        and not _BANNED_RE.search(q["question"])
        and not _STALE_YEAR_RE.search(q["question"])
        and not _STALE_YEAR_RE.search(q["gold_answer"])
        and not _bad_gold(
            q["question"], q["gold_answer"], q.get("answer_type", "short")
        )
    ]


async def main_async(args) -> int:
    global _LLM_SEM
    random.seed(args.seed)
    api_key = os.environ.get("DESEARCH_API_KEY")
    if not api_key:
        print("DESEARCH_API_KEY not set (env or repo-root .env).")
        return 1
    if not _LLM_KEY:
        print(
            f"No LLM key — set {'CHUTES_API_TOKEN' if _CHUTES else 'OPENAI_API_KEY'}."
        )
        return 1

    _LLM_SEM = asyncio.Semaphore(args.gen_concurrency)
    windows = _windows(args)

    if not args.overwrite:
        qdir = Path(args.out) / "questions"
        skip = [
            w
            for w in windows
            if (qdir / f"{w[0]}.jsonl").exists()
            and (qdir / f"{w[0]}.jsonl").stat().st_size > 0
        ]
        if skip:
            print(
                f"[resume] skipping {len(skip)} already-done day(s): {[w[0] for w in skip]}"
            )
        windows = [w for w in windows if w not in skip]
        if not windows:
            print("[resume] all requested days already done — nothing to do.")
            return 0

    queries = x_sources.build_queries(
        domains=args.domains or None,
        include_accounts=not args.topics_only,
        include_topics=not args.accounts_only,
    )
    print(
        f"[x] {len(windows)} day(s) {windows[-1][0]}..{windows[0][0]} | {len(queries)} queries "
        f"| provider={'chutes' if _CHUTES else 'openai'} {GEN_MODEL} "
        f"| llm-concurrency={args.gen_concurrency} harvest-concurrency={args.harvest_concurrency}",
        flush=True,
    )

    client = Desearch(api_key, max_conn=max(32, args.harvest_concurrency))
    try:
        by_day = await harvest_days(client, queries, windows, args)
    finally:
        await client.aclose()

    all_recs = [r for recs in by_day.values() for r in recs]
    print(
        f"[x] harvested {len(all_recs)} usable tweets across {len(windows)} day(s)",
        flush=True,
    )
    if not all_recs:
        print("No tweets harvested — aborting.")
        return 1

    raw = await gen_all(all_recs, args.questions_per_tweet, args.gen_concurrency)

    raw_by_day: dict[str, list[dict]] = defaultdict(list)
    for q in raw:
        raw_by_day[q["day"]].append(q)

    deduped: dict[str, list[dict]] = {}
    for label, _, _ in windows:
        clean = _filter(raw_by_day.get(label, []))
        if args.append:
            clean += [
                q
                for q in load_existing(Path(args.out), label)
                if not _BANNED_RE.search(q["question"])
                and not _bad_gold(
                    q["question"], q["gold_answer"], q.get("answer_type", "short")
                )
            ]
        deduped[label] = await dedup(clean)
    print(
        f"[filter+dedup] {sum(len(v) for v in deduped.values())} questions across days; grading...",
        flush=True,
    )

    labels = [w[0] for w in windows]

    async def grade_and_save(label: str) -> int:
        """Grade + save one day as soon as it's ready."""
        graded = await quality_grade(deduped[label])
        final = balance(graded, args.target, args.per_author_frac, args.per_tweet)
        qpath, _ = save_local(Path(args.out), label, final, args.window_days)
        bt = defaultdict(int)
        for q in final:
            bt[q["answer_type"]] += 1
        print(
            f"  saved {label}: {len(final)} questions ({dict(bt)}) -> {qpath.name}",
            flush=True,
        )
        return len(final)

    counts = await asyncio.gather(*[grade_and_save(label) for label in labels])
    total = sum(counts)
    print(
        f"\n[done] {total} X questions across {len(windows)} day(s) "
        f"(avg {total // max(1, len(windows))}/day)"
    )
    return 0


def main() -> int:
    global MIN_FOLLOWERS
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument(
        "--target",
        type=int,
        default=2000,
        help="soft target (sizes per-author cap; not exact)",
    )
    p.add_argument(
        "--days",
        type=int,
        default=None,
        help="harvest the last N full UTC days, one file each",
    )
    p.add_argument(
        "--end-day",
        default=None,
        help="with --days, count back from this UTC day (default: today) — lets you chunk a month",
    )
    p.add_argument("--day", default=None, help="harvest ONE past UTC day YYYY-MM-DD")
    p.add_argument(
        "--window-hours",
        type=int,
        default=24,
        help="single-run harvest window (no --day/--days)",
    )
    p.add_argument(
        "--window-days",
        type=int,
        default=7,
        help="answer search window the validator uses",
    )
    p.add_argument("--provider", default=None, choices=["openai", "chutes"])
    p.add_argument("--domains", nargs="*", default=None)
    p.add_argument("--accounts-only", action="store_true")
    p.add_argument("--topics-only", action="store_true")
    p.add_argument(
        "--per-query", type=int, default=150, help="max tweets to pull per query-day"
    )
    p.add_argument(
        "--count", type=int, default=20, help="tweets per API call (page size)"
    )
    p.add_argument(
        "--max-tweets", type=int, default=10000, help="cap on tweets to invert per day"
    )
    p.add_argument("--sort", default="Latest", choices=["Latest", "Top"])
    p.add_argument("--lang", default="en", help="language filter (empty to disable)")
    p.add_argument(
        "--min-likes", type=int, default=10, help="engagement floor for TOPIC queries"
    )
    p.add_argument("--min-followers", type=int, default=MIN_FOLLOWERS)
    p.add_argument(
        "--harvest-concurrency",
        type=int,
        default=48,
        help="concurrent desearch query-walks",
    )
    p.add_argument(
        "--api-sleep", type=float, default=0.0, help="delay between paged API calls"
    )
    p.add_argument(
        "--gen-concurrency",
        type=int,
        default=96,
        help="global LLM concurrency (gen+grade)",
    )
    p.add_argument("--questions-per-tweet", type=int, default=QUESTIONS_PER_TWEET)
    p.add_argument("--per-author-frac", type=float, default=0.08)
    p.add_argument("--per-tweet", type=int, default=3)
    p.add_argument(
        "--append", action="store_true", help="merge with each day's existing questions"
    )
    p.add_argument(
        "--overwrite",
        action="store_true",
        help="regenerate days even if their file already exists (default: skip/resume)",
    )
    p.add_argument("--out", default=str(HERE / "output"))
    p.add_argument("--date", default=None, help="label override (single-day only)")
    p.add_argument("--seed", type=int, default=7)
    args = p.parse_args()

    if args.provider:
        _configure_provider(args.provider)
    if not args.lang:
        args.lang = None
    MIN_FOLLOWERS = args.min_followers
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
