"""Push the public X question files (output/questions/x-*.jsonl) to HF."""

import argparse
import pathlib

from huggingface_hub import HfApi

HERE = pathlib.Path(__file__).resolve().parent
QUESTIONS_DIR = HERE / "output" / "questions"
DEFAULT_REPO = "desearch/dataset"
PATH_IN_REPO = "x"


def _hf_token():
    env = HERE.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip() in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
                    return v.strip().strip('"').strip("'")
    return None


def push(repo, path_in_repo, dry_run):
    files = sorted(QUESTIONS_DIR.glob("x-*.jsonl"))
    if not files:
        print(f"no x-*.jsonl files in {QUESTIONS_DIR}")
        return 1

    n_rows = sum(sum(1 for _ in f.open()) for f in files)
    print(f"{len(files)} X files, {n_rows} questions -> {repo}/{path_in_repo}/")
    if dry_run:
        print("dry-run: nothing uploaded")
        return 0

    api = HfApi(token=_hf_token())
    api.upload_folder(
        repo_id=repo,
        repo_type="dataset",
        folder_path=str(QUESTIONS_DIR),
        path_in_repo=path_in_repo,
        allow_patterns="x-*.jsonl",
        commit_message=f"Add {len(files)} X question files ({n_rows} questions)",
    )
    print(f"pushed -> https://huggingface.co/datasets/{repo}/tree/main/{path_in_repo}")
    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--path-in-repo", default=PATH_IN_REPO)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()
    raise SystemExit(push(args.repo, args.path_in_repo, args.dry_run))


if __name__ == "__main__":
    main()
