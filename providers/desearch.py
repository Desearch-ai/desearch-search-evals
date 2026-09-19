from utils import read_jsonl


def load_local(path, questions, profiles):
    allowed = {p["id"] for p in profiles if p["transport"] == "local"}
    indexed = {q["id"]: q for q in questions}
    results = {}
    for row in read_jsonl(path):
        profile = row.get("profile_id", row.get("profile"))
        identifier = row["question_id"]
        if profile not in allowed or identifier not in indexed:
            raise ValueError("Local result does not belong to this run")
        if row.get("query") != indexed[identifier]["question"]:
            raise ValueError("Local result query differs from the frozen question")
        row = {**row, "profile_id": profile, "cost_usd": 0.0}
        key = f"{profile}/{identifier}.json"
        if key in results and results[key] != row:
            raise ValueError("Local results contain conflicting duplicate rows")
        results[key] = row
    return results
