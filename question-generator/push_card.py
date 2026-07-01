"""Upload the HF dataset card (hf_card.md -> README.md) to the dataset repo."""

import argparse
import pathlib

from huggingface_hub import HfApi

HERE = pathlib.Path(__file__).resolve().parent
CARD = HERE / "hf_card.md"
DEFAULT_REPO = "desearch/dataset"


def _hf_token():
    for env in (HERE.parent / ".env", HERE / ".env"):
        if env.exists():
            for line in env.read_text().splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    if k.strip() in ("HF_TOKEN", "HUGGING_FACE_HUB_TOKEN"):
                        return v.strip().strip('"').strip("'")
    return None


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--repo", default=DEFAULT_REPO)
    args = p.parse_args()
    if not CARD.exists():
        raise SystemExit(f"card not found: {CARD}")
    HfApi(token=_hf_token()).upload_file(
        path_or_fileobj=str(CARD),
        path_in_repo="README.md",
        repo_id=args.repo,
        repo_type="dataset",
        commit_message="Update dataset card",
    )
    print(f"pushed card -> https://huggingface.co/datasets/{args.repo}")


if __name__ == "__main__":
    main()
