#!/usr/bin/env python3
"""
REST-only retrieval of Brave invalid bug reports + PR-closure annotation,
then RANDOMLY SAMPLE 356 issues from the invalid set.

Design:
[1] Fetch ALL PRs via /pulls?state=all&per_page=100&page=N  -> PR_CACHE (metadata only from this listing)
[2] Fetch invalid closed issues by label via /issues?state=closed&labels=... (dedupe)
[3] Randomly sample k=356 issue numbers from the invalid set (optionally seeded)
[4] For each sampled issue:
      - Fetch timeline
      - Look for "closed" event -> closer.number
      - If closer.number in PR_CACHE:
            closed_with_pr = True
            closed_by_pr = PR_CACHE[closer.number]
        Else:
            closed_with_pr = False
            closed_by_pr = null

Output:
  - JSONL file with sampled issue metadata + closed_with_pr + closed_by_pr + retrieval timestamp

Requirements:
  - GITHUB_TOKEN in environment or .env file (load_dotenv enabled)

Run:
  python brave_retrieval.py --sample-size 356 --seed 42
"""

import os
import re
import json
import argparse
import datetime as dt
import random
from typing import Dict, List, Optional, Any
import requests
from dotenv import load_dotenv

load_dotenv()

OWNER = "brave"
REPO = "brave-browser"
API = "https://api.github.com"

INVALID_LABELS = [
    "closed/invalid",
    "closed/duplicate",
    "closed/workaround",
    "closed/works-for-me",
    "closed/fixable-by-custom-rules",
]


def headers(token: str, timeline: bool = False) -> Dict[str, str]:
    h = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "brave-invalid-issue-fetcher-rest",
    }
    if timeline:
        h["Accept"] = "application/vnd.github.mockingbird-preview+json, application/vnd.github+json"
    return h


def parse_next_link(link_header: Optional[str]) -> Optional[str]:
    if not link_header:
        return None
    parts = [p.strip() for p in link_header.split(",")]
    for p in parts:
        if 'rel="next"' in p:
            m = re.search(r"<([^>]+)>", p)
            return m.group(1) if m else None
    return None


def http_get(token: str, url_or_path: str, params: Optional[dict] = None, timeline: bool = False) -> Any:
    url = url_or_path if url_or_path.startswith("http") else f"{API}{url_or_path}"
    r = requests.get(url, headers=headers(token, timeline=timeline), params=params, timeout=60)

    if r.status_code == 403 and "X-RateLimit-Remaining" in r.headers:
        rem = r.headers.get("X-RateLimit-Remaining")
        reset = r.headers.get("X-RateLimit-Reset")
        raise RuntimeError(f"403 rate-limited. Remaining={rem}, reset_epoch={reset}")

    r.raise_for_status()
    return r.json(), r.headers


def is_pull_request_issue(issue_obj: dict) -> bool:
    return "pull_request" in issue_obj


def fetch_all_prs_listing(token: str, per_page: int = 100) -> Dict[int, dict]:
    pr_cache: Dict[int, dict] = {}

    url = f"{API}/repos/{OWNER}/{REPO}/pulls"
    params = {"state": "all", "per_page": per_page, "page": 1}

    while True:
        data, hdrs = http_get(token, url, params=params, timeline=False)
        if not data:
            break

        for pr in data:
            num = pr.get("number")
            if not isinstance(num, int):
                continue

            pr_cache[num] = {
                "number": num,
                "url": pr.get("html_url"),
                "title": pr.get("title"),
                "state": pr.get("state"),
                "draft": pr.get("draft"),
                "created_at": pr.get("created_at"),
                "updated_at": pr.get("updated_at"),
                "closed_at": pr.get("closed_at"),
                "merged_at": pr.get("merged_at"),
                "user_login": (pr.get("user") or {}).get("login"),
                "base_ref": ((pr.get("base") or {}).get("ref")),
                "head_ref": ((pr.get("head") or {}).get("ref")),
            }

        next_url = parse_next_link(hdrs.get("Link"))
        if not next_url:
            break
        url = next_url
        params = None

    return pr_cache


def list_closed_issues_by_label(token: str, label: str, per_page: int = 100) -> List[dict]:
    results: List[dict] = []
    url = f"{API}/repos/{OWNER}/{REPO}/issues"
    params = {"state": "closed", "labels": label, "per_page": per_page, "page": 1}

    while True:
        data, hdrs = http_get(token, url, params=params, timeline=False)
        if not data:
            break
        results.extend(data)

        next_url = parse_next_link(hdrs.get("Link"))
        if not next_url:
            break
        url = next_url
        params = None

    return results


def get_issue_timeline(token: str, number: int) -> List[dict]:
    events: List[dict] = []
    url = f"{API}/repos/{OWNER}/{REPO}/issues/{number}/timeline"
    params = {"per_page": 100, "page": 1}

    while True:
        data, hdrs = http_get(token, url, params=params, timeline=True)
        if not data:
            break
        events.extend(data)

        next_url = parse_next_link(hdrs.get("Link"))
        if not next_url:
            break
        url = next_url
        params = None

    return events


def find_closer_number_from_timeline(timeline_events: List[dict]) -> Optional[int]:
    for ev in timeline_events:
        if ev.get("event") != "closed":
            continue
        closer = ev.get("closer")
        if isinstance(closer, dict):
            n = closer.get("number")
            if isinstance(n, int):
                return n
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", type=str, default="invalid_random_sample_356.jsonl", help="Output JSONL file.")
    ap.add_argument("--sample-size", type=int, default=356, help="Random sample size from invalid issues.")
    ap.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility.")
    ap.add_argument("--skip-timeline", action="store_true", help="If set, do not call timeline (closed_with_pr will be False).")
    args = ap.parse_args()

    token = os.getenv("GITHUB_TOKEN")
    if not token:
        raise SystemExit("Missing GITHUB_TOKEN environment variable (or .env).")

    retrieved_at_utc = dt.datetime.now(dt.timezone.utc).isoformat()

    # [1] Fetch ALL PRs -> PR_CACHE (cheap for ~2400 PRs)
    print("Fetching ALL PRs (listing endpoint)...")
    pr_cache = fetch_all_prs_listing(token)
    print(f"PR_CACHE size: {len(pr_cache)}")

    # [2] Fetch invalid closed issues by label and dedupe
    print("Fetching invalid closed issues by label...")
    candidates: Dict[int, dict] = {}
    for lab in INVALID_LABELS:
        print(f"  Listing closed issues with label: {lab}")
        issues = list_closed_issues_by_label(token, lab)
        for it in issues:
            if is_pull_request_issue(it):
                continue
            num = it.get("number")
            if isinstance(num, int):
                candidates[num] = it

    all_numbers = sorted(candidates.keys())
    total = len(all_numbers)
    print(f"Unique candidate invalid closed issues: {total}")

    # [3] Randomly sample 356 issue numbers
    k = args.sample_size
    if k > total:
        raise SystemExit(f"Sample size {k} is larger than candidate pool {total}.")

    rng = random.Random(args.seed)
    sampled_numbers = sorted(rng.sample(all_numbers, k))
    print(f"Randomly sampled: {len(sampled_numbers)} issues (seed={args.seed})")

    # [4] For each sampled issue, annotate closure PR (from cache only)
    inspected = 0
    with open(args.out, "w", encoding="utf-8") as f:
        for num in sampled_numbers:
            inspected += 1
            issue = candidates[num]  # from listing call

            closed_with_pr = False
            closed_by_pr = None
            closer_number = None

            if not args.skip_timeline:
                timeline = get_issue_timeline(token, num)
                closer_number = find_closer_number_from_timeline(timeline)
                if isinstance(closer_number, int) and closer_number in pr_cache:
                    closed_with_pr = True
                    closed_by_pr = pr_cache[closer_number]

            labels = [l.get("name") for l in (issue.get("labels") or []) if isinstance(l, dict) and l.get("name")]
            assignees = [a.get("login") for a in (issue.get("assignees") or []) if isinstance(a, dict) and a.get("login")]

            record = {
                "repo": f"{OWNER}/{REPO}",
                "number": issue.get("number"),
                "title": issue.get("title"),
                "url": issue.get("html_url"),
                "state": issue.get("state"),
                "createdAt": issue.get("created_at"),
                "updatedAt": issue.get("updated_at"),
                "closedAt": issue.get("closed_at"),
                "author": (issue.get("user") or {}).get("login"),
                "labels": labels,
                "assignees": assignees,
                "comments_total": issue.get("comments"),
                "retrievedAt": retrieved_at_utc,

                "closer_number": closer_number,
                "closed_with_pr": closed_with_pr,
                "closed_by_pr": closed_by_pr,
                "sample_seed": args.seed,
            }

            f.write(json.dumps(record, ensure_ascii=False) + "\n")

            if inspected % 50 == 0:
                print(f"Progress: {inspected}/{k}")

    print("\n=== DONE ===")
    print(f"RetrievedAt (UTC): {retrieved_at_utc}")
    print(f"Candidate pool: {total}")
    print(f"Sample size: {k} (seed={args.seed})")
    print(f"Wrote: {args.out}")


if __name__ == "__main__":
    main()
