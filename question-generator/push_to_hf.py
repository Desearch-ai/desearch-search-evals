"""Push output/hf_dataset/questions/*.jsonl to the HF dataset repo (public files only)."""

import argparse
import pathlib

from huggingface_hub import HfApi

HERE = pathlib.Path(__file__).resolve().parent
QUESTIONS_DIR = HERE / "output" / "hf_dataset" / "questions"
DEFAULT_REPO = "desearch/dataset"


def _hf_token():
    env = HERE.parent / ".env"
    if env.exists():
        for line in env.read_text().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                if k.strip() in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
                    return v.strip().strip('"').strip("'")
    return None


def push(repo, dry_run):
    files = sorted(QUESTIONS_DIR.glob("*.jsonl"))
    if not files:
        print(f"no files in {QUESTIONS_DIR}")
        return 1

    n_rows = sum(sum(1 for _ in f.open()) for f in files)
    print(f"{len(files)} files, {n_rows} questions -> {repo}/questions/")
    if dry_run:
        print("dry-run: nothing uploaded")
        return 0

    api = HfApi(token=_hf_token())
    api.upload_folder(
        repo_id=repo,
        repo_type="dataset",
        folder_path=str(QUESTIONS_DIR),
        path_in_repo="questions",
        allow_patterns="*.jsonl",
        commit_message=f"Add {len(files)} dated question files ({n_rows} questions)",
    )
    print(f"pushed -> https://huggingface.co/datasets/{repo}/tree/main/questions")

    return 0


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", default=DEFAULT_REPO)
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    raise SystemExit(push(args.repo, args.dry_run))


if __name__ == "__main__":
    main()
