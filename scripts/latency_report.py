"""Client time beside the provider's own server time, per profile, from a run's saved searches."""

import argparse
import json
import re
import statistics
from pathlib import Path


def server_seconds(row):
    """The provider's reported processing time, when its response carries one."""
    raw = row.get("raw") if isinstance(row.get("raw"), dict) else {}
    if isinstance(raw.get("searchTime"), (int, float)):
        return raw["searchTime"] / 1000
    if isinstance(raw.get("response_time"), (int, float)):
        return float(raw["response_time"])
    total = (row.get("server_ms") or {}).get("total")
    if isinstance(total, (int, float)):
        return total / 1000
    headers = row.get("server_headers") or {}
    for name in (
        "x-response-time",
        "x-request-duration",
        "x-process-time",
        "x-envoy-upstream-service-time",
    ):
        value = headers.get(name)
        found = re.search(r"[\d.]+", value or "")
        if found:
            number = float(found.group(0))
            return number if "s" in value and "ms" not in value else number / 1000
    durations = [
        float(d) for d in re.findall(r"dur=([\d.]+)", headers.get("server-timing", ""))
    ]
    return sum(durations) / 1000 if durations else None


def pct(values, p):
    values = sorted(values)
    return values[min(len(values) - 1, int(p * len(values)))] if values else None


def report(run):
    rows = []
    for folder in sorted(p for p in (Path(run) / "searches").iterdir() if p.is_dir()):
        client, server = [], []
        for f in folder.glob("*.json"):
            row = json.loads(f.read_text())
            if row.get("error"):
                continue
            if row.get("elapsed_seconds"):
                client.append(row["elapsed_seconds"])
            s = server_seconds(row)
            if s is not None:
                server.append(s)
        if client:
            rows.append(
                {
                    "profile": folder.name,
                    "n": len(client),
                    "client_p50": statistics.median(client),
                    "client_p95": pct(client, 0.95),
                    "server_p50": statistics.median(server) if server else None,
                    "server_p95": pct(server, 0.95),
                    "server_n": len(server),
                }
            )
    return rows


def fmt(x):
    return f"{x:.2f}s" if x is not None else "n/a"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("runs", nargs="+")
    args = parser.parse_args()
    print(
        "| Run | Profile | n | Client p50 / p95 | Server p50 / p95 | Network and queue (client − server p50) |"
    )
    print("| --- | --- | ---: | --- | --- | --- |")
    for run in args.runs:
        for r in report(run):
            gap = (
                fmt(r["client_p50"] - r["server_p50"])
                if r["server_p50"] is not None
                else "n/a"
            )
            print(
                f"| {Path(run).parent.name}/{Path(run).name} | {r['profile']} | {r['n']} | {fmt(r['client_p50'])} / {fmt(r['client_p95'])} | {fmt(r['server_p50'])} / {fmt(r['server_p95'])} | {gap} |"
            )


if __name__ == "__main__":
    main()
