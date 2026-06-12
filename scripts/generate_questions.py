"""Daily question generator — invert fresh news articles into self-contained,
web-answerable benchmark questions with private gold answers.

Pipeline: collect recent articles (RSS + sitemaps, reusing lowtier/news_crawler)
-> fetch full text -> LLM inverts each article into K diverse Q+gold -> filter
(leakage guard, length, embedding dedup, per-source quota) -> balance -> save.

Outputs (local, on disk):
  <out>/questions/<date>.jsonl   reference-free: {id, difficulty, question, source, date, lane}
  <out>/golds/<date>.jsonl       PRIVATE: {id, question, gold_answer, answer_span, source_url}

HF: --hf appends questions/<date>.jsonl to the dataset repo (needs HF_TOKEN); dry-run otherwise.

Usage:
  python3 scripts/generate_questions.py --articles 60 --target 150          # small experiment
  python3 scripts/generate_questions.py --articles 500 --target 1000        # full run
  python3 scripts/generate_questions.py --articles 500 --target 1000 --hf --repo desearch/desearch-search-evals
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import random
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import aiohttp

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from providers.common import load_env  # noqa: E402

load_env()

from openai import AsyncOpenAI  # noqa: E402

from evaluators.common import is_contaminated  # noqa: E402
from lowtier import news_crawler as nc  # noqa: E402

GEN_MODEL = "gpt-4.1-nano"
EMBED_MODEL = "text-embedding-3-small"
QUESTIONS_PER_ARTICLE = 4
MIN_TEXT_CHARS = 700
MAX_TEXT_CHARS = 6000
DEDUP_COSINE = 0.86
PER_SOURCE_CAP_FRAC = 0.04  # no single source > 4% of the final set
DIFFICULTY_MIX = {"easy": 0.40, "medium": 0.40, "hard": 0.20}
ANSWER_TYPE_MIX = {"short": 0.55, "explanatory": 0.30, "summary": 0.15}
_MAX_GOLD_WORDS = {"short": 8, "explanatory": 45, "summary": 90}

_client: AsyncOpenAI | None = None


def oai() -> AsyncOpenAI:
    global _client
    if _client is None:
        _client = AsyncOpenAI(timeout=60.0)
    return _client


GEN_PROMPT = """You create benchmark questions for testing AI WEB SEARCH engines on RECENT events.
Given ONE news article, write a DIVERSE MIX of up to {k} questions whose answer the article supports. Quality over quantity. Vary the ANSWER SHAPE across the questions:
- "short": a single factoid; gold_answer is one crisp fact <=6 words (who / what / when / how-many / where / which).
- "explanatory": a why / how question; gold_answer is a focused 1-3 sentence explanation built from the article's concrete facts (causes, mechanism, consequence).
- "summary": asks for the key points / main developments / what changed / how someone responded; gold_answer is a faithful 3-6 sentence synthesis of the concrete facts (names, numbers, dates, outcomes).
Aim for roughly half "short" and the rest a mix of "explanatory" and "summary".
For "explanatory" and "summary", the SUBJECT must be a public event, decision, policy, deal, product launch, or widely-covered figure/organization. NEVER ask "why does X feel/believe/think/like ...", about a private individual's motivation or emotions, an author's or interviewee's personal biography, or a fictional character. The answer must be FACT-DENSE — named people/orgs, numbers, dates, concrete outcomes — not a characterization, a feeling, or a paraphrase of one source's framing.

HARD RULES (apply to EVERY type):
- SELF-CONTAINED: stands alone for someone who never saw the article. NEVER write "the article/report/study/episode", "according to the author", "is/are mentioned", "discussed". Name the specific people, organizations, places, dates, events.
- PUBLIC & WEB-ANSWERABLE: about a real, widely-reported RECENT development (last ~30 days) that MANY outlets cover. NOT a private individual in one human-interest story, NOT a personal biography, NOT a quote-recall of a non-famous person. NOT a historical fact (birth year, old film, past World Cup) mentioned in passing.
- GROUNDED & FACT-DENSE: only what THIS article states; quote the supporting sentence(s) in answer_span. Explanatory/summary answers must be CONCRETE (names/numbers/dates), never vague or opinion.
- UNIQUE FOCUS: the question targets one specific event/topic so a search engine knows exactly what to answer.

ANSWER-LENGTH by type: short <=6 words (never a sentence, never restate the question, absolute dates not weekdays); explanatory 1-3 sentences; summary 3-6 sentences.

Return ONLY a JSON array (0 to {k} objects), each:
{{"question": str, "gold_answer": str, "answer_span": str, "difficulty": "easy"|"medium"|"hard", "answer_type": "short"|"explanatory"|"summary", "qtype": str}}

Article title: {title}
Published: {published}
Article text:
{text}"""

# Phrases that betray a non-self-contained or meta question (references its
# source, the publication/app, or article metadata — not a newsworthy fact).
_BANNED_RE = re.compile(
    r"(?i)\b(the|this)\s+(article|report|study|piece|story|author|text|passage|essay|interview|episode|podcast|column|op-?ed)\b"
    r"|according to (the|this)\b|as (mentioned|described|stated|reported|explained|noted) (in|by) (the|this)\b"
    r"|in the (article|report|story|piece|episode|podcast)\b"
    r"|\bis mentioned\b|\bare mentioned\b|\bis discussed\b|\bare discussed\b|\bbeing discussed\b"
    r"|\bnews app\b|\bdownload(ing)? the\b|\bsubscribe\b|\bnewsletter\b"
    r"|article (was )?published|publication date|on what date was .* published"
    r"|\b(france|usa|italia|mexico|spain|germany|argentina|brazil|korea/?japan)\s*'?9\d\b"
    r"|world cup (19\d\d|200\d|201\d|202[0-4])"
)

# A 4-digit year <= 2024 in the question signals a historical fact, not recent news.
_STALE_YEAR_RE = re.compile(r"\b(19\d\d|20[0-1]\d|202[0-4])\b")

# Boilerplate lines to strip from article text before generation (app promos,
# follow/subscribe footers) so questions aren't generated from navigation chrome.
_BOILERPLATE_RE = re.compile(
    r"(?im)^.*(download the .* app|follow us on|sign up (for|to)|subscribe to|"
    r"newsletter|available on (the )?app store|get it on google play|"
    r"this article was|read more:|related stories?:).*$"
)

GRADE_PROMPT = """You screen candidate questions for an AI WEB SEARCH benchmark. A question is GOOD only if ALL hold:
1. SELF-CONTAINED: stands alone, names the specific entities, never references "the article/report/study/episode/author" or uses "is/are mentioned"/"discussed".
2. PUBLIC & WEB-ANSWERABLE: about a real, widely-reported recent event (named public figures, governments, companies, agencies, teams, named products/events). NOT a private person in one human-interest story, NOT a personal biography, NOT a quote-recall of what a non-famous person (clinician, local expert) said. A web search engine could answer it WITHOUT the original article.
3. UNIQUE ANSWER: exactly one answer is correct across the open web; reject vague "which country/location/species/app" with many candidates.
4. APPROPRIATE ANSWER (each Q is tagged [short]/[explanatory]/[summary]): [short] = one crisp fact <=6 words, absolute dates, no question-restating; [explanatory] = a focused 1-3 sentence reason from concrete facts; [summary] = a faithful 3-6 sentence synthesis of concrete facts (names/numbers/dates). Any type: specific, never vague, never opinion/value-judgment, never invented.
5. RECENT: about a development from the last ~30 days, NOT a historical fact (birth year, old film, past election/World Cup) the article mentions in passing.
5b. PUBLIC & FACT-DENSE (esp. explanatory/summary): the subject is a public event/decision/policy/deal/product or major public figure, and the answer states concrete facts (named people/orgs, numbers, dates, outcomes). REJECT "why does X feel/believe/think", a private person's motivations/emotions, an author/interviewee biography, a fictional character, or an answer that is a vague characterization or circular restatement.
6. NOT META: not about an app/website/publication, downloads, subscriptions, or when an article was published.

Return ONLY a JSON array, one object per question: {{"i": <index int>, "keep": true|false}}."""


def _bad_gold(question: str, gold: str, answer_type: str = "short") -> bool:
    """Reject golds whose length is wrong for their answer_type, that restate a
    short question, or that are vague. Short = a crisp fact; explanatory/summary
    may be 1-6 sentences but must still be concrete."""
    words = gold.split()
    cap = _MAX_GOLD_WORDS.get(answer_type, 8)
    if not words or len(words) > cap:
        return True
    import re as _re
    if answer_type == "short":
        # a short answer must not restate the question or be a bare weekday
        ql = [w.lower().strip(".,") for w in question.split()]
        gl = [w.lower().strip(".,") for w in words]
        for i in range(len(gl) - 2):
            tri = gl[i:i + 3]
            for j in range(len(ql) - 2):
                if ql[j:j + 3] == tri:
                    return True
        if _re.fullmatch(r"(?i)(mon|tues|wednes|thurs|fri|satur|sun)day\.?", gold.strip()):
            return True
    else:
        # explanatory/summary must be fact-dense, not a vague one-liner
        if len(words) < 6:
            return True
    if _re.search(r"(?i)\b(maybe|roughly|various|unclear)\b", gold) and len(words) <= 3:
        return True
    return False


def _publisher_family(source: str) -> str:
    """Collapse bbc_world/bbc_science -> bbc so one publisher cannot dominate."""
    return source.split(":", 1)[-1].split("_", 1)[0]


async def collect_articles(lookback_days: int, want: int, concurrency: int,
                           fetch_timeout: float, use_sitemaps: bool) -> list[dict]:
    """Gather recent article URLs (RSS + optional sitemaps), round-robin across
    sources for diversity, fetch full text, keep articles with enough body."""
    cutoff = datetime.now(timezone.utc).timestamp() - lookback_days * 86400
    feed_items = nc.collect_feed_items()
    items = feed_items
    if use_sitemaps:
        sm = nc.collect_sitemap_items(datetime.now(timezone.utc) - __import__("datetime").timedelta(days=lookback_days))
        items = nc.merge_dedupe(feed_items, sm)
    random.shuffle(items)
    # round-robin across sources so no outlet dominates the fetch queue
    candidates = nc._round_robin(items, cap=10**9)

    print(f"[collect] {len(items)} candidate URLs from {len({i['source'] for i in items})} sources; "
          f"fetching text for up to {want} (bounded, early-exit)", flush=True)

    out: list[dict] = []
    sem = asyncio.Semaphore(concurrency)
    async with aiohttp.ClientSession() as session:
        async def one(it: dict):
            async with sem:
                text = await nc.fetch_via_trafilatura(session, it["url"], fetch_timeout)
            if text:
                text = _BOILERPLATE_RE.sub("", text).strip()
            if text and MIN_TEXT_CHARS <= len(text):
                return {
                    "url": it["url"], "title": it["title"],
                    "text": text[:MAX_TEXT_CHARS],
                    "published": it.get("feed_date") or "", "source": it["source"],
                }
            return None

        # Fetch in bounded chunks so a tail of slow/timing-out sites can't stall
        # the whole gather, and stop as soon as we have enough usable articles.
        chunk = max(64, concurrency * 6)
        for i in range(0, len(candidates), chunk):
            if len(out) >= want:
                break
            results = await asyncio.gather(*[one(it) for it in candidates[i:i + chunk]])
            out.extend(r for r in results if r)
            print(f"[collect] {len(out)}/{want} usable after {min(i + chunk, len(candidates))} attempts",
                  flush=True)
    out = out[:want]
    print(f"[collect] fetched {len(out)} usable articles from {len({a['source'] for a in out})} sources",
          flush=True)
    return out


def _parse_json_array(text: str) -> list[dict]:
    m = re.search(r"\[.*\]", text or "", re.DOTALL)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
        return [x for x in arr if isinstance(x, dict)]
    except Exception:
        return []


async def gen_for_article(article: dict, k: int) -> list[dict]:
    prompt = GEN_PROMPT.format(k=k, title=article["title"],
                               published=article["published"], text=article["text"])
    try:
        resp = await oai().chat.completions.create(
            model=GEN_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.5,
        )
        raw = _parse_json_array(resp.choices[0].message.content or "")
    except Exception as e:
        print(f"  [gen fallback] {type(e).__name__}: {e}")
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
        qs.append({
            "question": q,
            "gold_answer": gold,
            "answer_span": str(o.get("answer_span", "")).strip()[:1200],
            "difficulty": diff,
            "answer_type": atype,
            "qtype": str(o.get("qtype", "")).strip()[:40],
            "source_url": article["url"],
            "source": article["source"],
        })
    return qs


async def embed(texts: list[str], batch: int = 256) -> list[list[float]]:
    out: list[list[float]] = []
    for i in range(0, len(texts), batch):
        chunk = texts[i:i + batch]
        resp = await oai().embeddings.create(model=EMBED_MODEL, input=chunk)
        out.extend(d.embedding for d in resp.data)
    return out


def _cos(a: list[float], b: list[float]) -> float:
    s = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return s / (na * nb + 1e-9)


async def dedup(questions: list[dict]) -> list[dict]:
    """Drop near-duplicate questions by embedding cosine (greedy, keep-first).
    Vectorized with numpy: each candidate is scored against the kept matrix in
    one matrix-vector product, so this stays fast at ~10k questions."""
    if not questions:
        return []
    import numpy as np
    vecs = np.asarray(await embed([q["question"] for q in questions]), dtype=np.float32)
    vecs /= (np.linalg.norm(vecs, axis=1, keepdims=True) + 1e-9)

    kept: list[dict] = []
    kept_mat = np.zeros((len(questions), vecs.shape[1]), dtype=np.float32)
    n = 0
    for q, v in zip(questions, vecs):
        if n and float((kept_mat[:n] @ v).max()) >= DEDUP_COSINE:
            continue
        kept.append(q)
        kept_mat[n] = v
        n += 1
    print(f"[dedup] {len(questions)} -> {len(kept)} after near-duplicate removal", flush=True)
    return kept


async def quality_grade(questions: list[dict], batch: int = 20) -> list[dict]:
    """Second-pass LLM screen: keep only self-contained, web-answerable, specific
    questions. Runs in batches; a question survives only on an explicit keep=true."""
    if not questions:
        return []

    async def grade_batch(start: int, chunk: list[dict]) -> set[int]:
        listing = "\n".join(
            f'{i}. [{q.get("answer_type","short")}] Q: {q["question"]}  A: {q["gold_answer"]}'
            for i, q in enumerate(chunk)
        )
        try:
            resp = await oai().chat.completions.create(
                model=GEN_MODEL,
                messages=[{"role": "user", "content": GRADE_PROMPT.format(listing=listing)}],
                temperature=0,
            )
            verdicts = _parse_json_array(resp.choices[0].message.content or "")
            return {start + int(v["i"]) for v in verdicts
                    if isinstance(v, dict) and v.get("keep") is True and "i" in v}
        except Exception as e:
            print(f"  [grade fallback keep-all] {type(e).__name__}: {e}")
            return set(range(start, start + len(chunk)))

    sem = asyncio.Semaphore(8)

    async def run(start, chunk):
        async with sem:
            return await grade_batch(start, chunk)

    results = await asyncio.gather(*[
        run(s, questions[s:s + batch]) for s in range(0, len(questions), batch)
    ])
    keep_idx = set().union(*results) if results else set()
    kept = [q for i, q in enumerate(questions) if i in keep_idx]
    print(f"[quality] {len(questions)} -> {len(kept)} after web-answerability screen")
    return kept


def balance(questions: list[dict], target: int) -> list[dict]:
    """Cap per-source/family/article, then sample toward the answer-type mix."""
    random.shuffle(questions)
    fam_cap = max(8, int(target * 0.15))      # no publisher family > 15%
    art_cap = 2                                # no single article > 2 questions
    by_fam: dict[str, int] = defaultdict(int)
    by_art: dict[str, int] = defaultdict(int)
    capped = []
    for q in questions:
        fam = _publisher_family(q["source"])
        if by_fam[fam] >= fam_cap or by_art[q["source_url"]] >= art_cap:
            continue
        capped.append(q)
        by_fam[fam] += 1
        by_art[q["source_url"]] += 1

    buckets: dict[str, list[dict]] = defaultdict(list)
    for q in capped:
        buckets[q["answer_type"]].append(q)
    out: list[dict] = []
    for atype, frac in ANSWER_TYPE_MIX.items():
        want = int(target * frac)
        out.extend(buckets[atype][:want])
    # top up to target from whatever remains
    if len(out) < target:
        chosen = {id(q) for q in out}
        for q in capped:
            if len(out) >= target:
                break
            if id(q) not in chosen:
                out.append(q)
    random.shuffle(out)
    return out[:target]


def _qid(question: str) -> str:
    return "q" + hashlib.sha1(question.strip().lower().encode()).hexdigest()[:12]


def load_existing(out_dir: Path, date: str) -> list[dict]:
    """Reconstruct internal question dicts from a previously-saved date (join
    questions/<date>.jsonl for difficulty with golds/<date>.jsonl for the rest)."""
    qpath = out_dir / "questions" / f"{date}.jsonl"
    gpath = out_dir / "golds" / f"{date}.jsonl"
    if not qpath.exists() or not gpath.exists():
        return []
    diffs = {}
    for line in qpath.open():
        r = json.loads(line)
        diffs[r["id"]] = r["difficulty"]
    out = []
    for line in gpath.open():
        g = json.loads(line)
        out.append({
            "question": g["question"], "gold_answer": g["gold_answer"],
            "answer_span": g.get("answer_span", ""), "difficulty": diffs.get(g["id"], "medium"),
            "answer_type": g.get("answer_type", "short"),
            "qtype": g.get("qtype", ""), "source_url": g["source_url"], "source": g["source"],
        })
    return out


def save_local(out_dir: Path, date: str, questions: list[dict], lane: str = "news") -> tuple[Path, Path]:
    qdir = out_dir / "questions"
    gdir = out_dir / "golds"
    qdir.mkdir(parents=True, exist_ok=True)
    gdir.mkdir(parents=True, exist_ok=True)
    qpath = qdir / f"{date}.jsonl"
    gpath = gdir / f"{date}.jsonl"
    with qpath.open("w") as qf, gpath.open("w") as gf:
        for q in questions:
            qid = _qid(q["question"])
            qf.write(json.dumps({
                "id": qid, "difficulty": q["difficulty"], "answer_type": q["answer_type"],
                "question": q["question"], "source": q["source"], "date": date, "lane": lane,
            }, ensure_ascii=False) + "\n")
            gf.write(json.dumps({
                "id": qid, "question": q["question"], "gold_answer": q["gold_answer"],
                "answer_span": q["answer_span"], "answer_type": q["answer_type"], "qtype": q["qtype"],
                "source_url": q["source_url"], "source": q["source"], "date": date,
            }, ensure_ascii=False) + "\n")
    return qpath, gpath


def upload_hf(repo: str, date: str, qpath: Path, dry_run: bool) -> None:
    """Append questions/<date>.jsonl to the HF dataset (golds stay private/local)."""
    token = os.environ.get("HF_TOKEN")
    if dry_run or not token:
        print(f"[hf] dry-run (set --hf and HF_TOKEN to push): would add questions/{date}.jsonl to {repo}")
        return
    from huggingface_hub import CommitOperationAdd, HfApi
    api = HfApi(token=token)
    api.create_commit(
        repo_id=repo, repo_type="dataset",
        operations=[CommitOperationAdd(f"questions/{date}.jsonl", str(qpath))],
        commit_message=f"Add generated questions for {date}",
    )
    print(f"[hf] pushed questions/{date}.jsonl to {repo}")


async def main_async(args) -> int:
    random.seed(args.seed)
    date = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")

    articles = await collect_articles(args.lookback, args.articles, args.concurrency,
                                      args.fetch_timeout, args.sitemaps)
    if not articles:
        print("No articles fetched — aborting.")
        return 1

    print(f"[gen] inverting {len(articles)} articles into questions ({QUESTIONS_PER_ARTICLE} each)")
    sem = asyncio.Semaphore(args.gen_concurrency)

    async def gen(a):
        async with sem:
            return await gen_for_article(a, QUESTIONS_PER_ARTICLE)

    batches = await asyncio.gather(*[gen(a) for a in articles])
    raw_qs = [q for b in batches for q in b]
    print(f"[gen] {len(raw_qs)} raw questions")

    # leakage guard + length + banned source-reference phrases
    clean = [q for q in raw_qs
             if not is_contaminated(q["source_url"], q["question"], q["gold_answer"])
             and 15 <= len(q["question"]) <= 300
             and not _BANNED_RE.search(q["question"])
             and not _STALE_YEAR_RE.search(q["question"])
             and not _STALE_YEAR_RE.search(q["gold_answer"])
             and not _bad_gold(q["question"], q["gold_answer"], q.get("answer_type", "short"))]
    print(f"[filter] {len(raw_qs)} -> {len(clean)} after leakage/length/self-contained filter")

    unique = await dedup(clean)
    graded = await quality_grade(unique)

    # --append: re-screen the existing date's questions through the (current)
    # mechanical filters and merge them into the pool, so a second run tops up
    # the same day toward the target instead of overwriting it.
    if args.append:
        existing = load_existing(Path(args.out), date)
        existing_clean = [q for q in existing
                          if not _BANNED_RE.search(q["question"])
                          and not _STALE_YEAR_RE.search(q["question"])
                          and not _STALE_YEAR_RE.search(q["gold_answer"])
                          and not _bad_gold(q["question"], q["gold_answer"], q.get("answer_type", "short"))]
        print(f"[append] merging {len(existing_clean)} kept existing "
              f"(of {len(existing)}) with {len(graded)} new")
        graded = await dedup(existing_clean + graded)

    final = balance(graded, args.target)

    qpath, gpath = save_local(Path(args.out), date, final)
    by_src = len({q["source"] for q in final})
    by_diff = defaultdict(int)
    for q in final:
        by_diff[q["difficulty"]] += 1
    print(f"\n[done] {len(final)} questions from {by_src} sources | difficulty {dict(by_diff)}")
    print(f"  questions -> {qpath}")
    print(f"  golds     -> {gpath}")
    upload_hf(args.repo, date, qpath, dry_run=not args.hf)
    return 0


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--target", type=int, default=1000, help="final number of questions")
    p.add_argument("--articles", type=int, default=500, help="articles to fetch text for")
    p.add_argument("--lookback", type=int, default=3, help="days back for recency")
    p.add_argument("--sitemaps", action="store_true", help="also use sitemaps (more, slower)")
    p.add_argument("--concurrency", type=int, default=24, help="article-fetch concurrency")
    p.add_argument("--gen-concurrency", type=int, default=12, help="LLM gen concurrency")
    p.add_argument("--fetch-timeout", type=float, default=20.0)
    p.add_argument("--out", default=str(REPO / "output"))
    p.add_argument("--date", default=None, help="YYYY-MM-DD (default: today UTC)")
    p.add_argument("--seed", type=int, default=7)
    p.add_argument("--append", action="store_true",
                   help="merge with the existing date's questions (top up toward target)")
    p.add_argument("--hf", action="store_true", help="push questions to HF (needs HF_TOKEN)")
    p.add_argument("--repo", default="desearch/desearch-search-evals")
    args = p.parse_args()
    return asyncio.run(main_async(args))


if __name__ == "__main__":
    raise SystemExit(main())
