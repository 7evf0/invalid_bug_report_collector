"""Fetch all issue and PR review comments for rows listed in a CSV and save to CSV + JSONL.

Usage:
  python scripts/fetch_issue_comments.py --input ../brave_scripts/invalid_sample_366.csv

Requirements:
  - Python 3.8+
  - requests (pip install requests)

Authentication:
  - Set environment variable GITHUB_TOKEN with a personal access token for higher rate limits.

Outputs:
  - invalid_sample_366_comments.csv  (one row per comment)
  - invalid_sample_366_issue_comments.jsonl  (one JSON object per issue with its comments)

The script will:
  - parse the `html_url` or `issue_number` & repo inferred from the CSV
  - fetch issue comments (GET /repos/{owner}/{repo}/issues/{number}/comments)
  - if the issue is a pull request, also fetch PR review comments (GET /repos/{owner}/{repo}/pulls/{number}/comments)
  - handle pagination and rate-limit gently
"""

import argparse
import os
import sys
import time
import json
import csv
import re
from urllib.parse import urlparse

import requests
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

GITHUB_API = "https://api.github.com"


def parse_owner_repo_number_from_url(url):
    """Parse owner, repo and issue/PR number from a GitHub URL or API URL.
    Returns (owner, repo, number) or (None, None, None) on failure.
    Examples:
      - https://api.github.com/repos/brave/brave-browser/issues/42040
      - https://github.com/brave/brave-browser/issues/42040
    """
    if not isinstance(url, str) or not url:
        return None, None, None
    try:
        p = urlparse(url)
        path = p.path
        # handle api path
        m = re.search(r"/repos/([^/]+)/([^/]+)/issues/(\d+)", path)
        if m:
            return m.group(1), m.group(2), int(m.group(3))
        # handle normal github url
        m2 = re.search(r"/([^/]+)/([^/]+)/issues/(\d+)$", path)
        if m2:
            return m2.group(1), m2.group(2), int(m2.group(3))
    except Exception:
        pass
    return None, None, None


def get_auth_headers(token):
    headers = {
        "Accept": "application/vnd.github.v3+json"
    }
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


def paginate_get(session, url, params=None, headers=None, sleep=0.2):
    results = []
    params = params or {"per_page": 100}
    while url:
        r = session.get(url, params=params, headers=headers)
        if r.status_code == 403 and r.headers.get("X-RateLimit-Remaining") == "0":
            reset = int(r.headers.get("X-RateLimit-Reset", time.time() + 60))
            wait = max(reset - time.time(), 1)
            print(f"Rate limited. Sleeping {wait:.0f}s until reset...")
            time.sleep(wait + 1)
            continue
        r.raise_for_status()
        page = r.json()
        if isinstance(page, list):
            results.extend(page)
        else:
            # some endpoints return object
            results.append(page)
        # pagination
        link = r.headers.get("Link")
        next_url = None
        if link:
            for part in link.split(","):
                if 'rel="next"' in part:
                    next_url = part[part.find('<') + 1:part.find('>')]
                    break
        url = next_url
        params = None
        if sleep:
            time.sleep(sleep)
    return results


def fetch_issue(session, owner, repo, number, headers, sleep=0.2):
    url = f"{GITHUB_API}/repos/{owner}/{repo}/issues/{number}"
    r = session.get(url, headers=headers)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    if sleep:
        time.sleep(sleep)
    return r.json()


def fetch_issue_comments(session, owner, repo, number, headers, sleep=0.2):
    url = f"{GITHUB_API}/repos/{owner}/{repo}/issues/{number}/comments"
    return paginate_get(session, url, headers=headers, sleep=sleep)


def fetch_pr_review_comments(session, owner, repo, number, headers, sleep=0.2):
    url = f"{GITHUB_API}/repos/{owner}/{repo}/pulls/{number}/comments"
    return paginate_get(session, url, headers=headers, sleep=sleep)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True, help="Path to input CSV with the 366 samples")
    ap.add_argument("--out-csv", default="invalid_sample_no_duplicate_366_comments.csv", help="Output CSV for flattened comments")
    ap.add_argument("--out-jsonl", default="invalid_sample_no_duplicate_366_issue_comments.jsonl", help="Output per-issue JSONL")
    ap.add_argument("--sleep", type=float, default=0.2, help="Seconds to sleep between requests")
    args = ap.parse_args(argv)

    token = os.getenv("GITHUB_TOKEN")

    df = pd.read_csv(args.input, low_memory=False)

    session = requests.Session()
    headers = get_auth_headers(token)

    out_rows = []
    with open(args.out_jsonl, "w", encoding="utf-8") as jsonl_f:
        for idx, row in df.reset_index(drop=True).iterrows():
            html_url = row.get("html_url") or row.get("url") or ''
            owner, repo, number = parse_owner_repo_number_from_url(html_url)
            if not (owner and repo and number):
                # try to use columns: maybe repo in column 'repository' etc.
                print(f"Could not parse owner/repo/number from url for row {idx}: {html_url}")
                continue
            print(f"Fetching comments for {owner}/{repo}#{number} ({idx + 1}/{len(df)})")

            issue_obj = fetch_issue(session, owner, repo, number, headers, sleep=args.sleep)
            if issue_obj is None:
                print(f"  Issue {owner}/{repo}#{number} not found (skipping)")
                continue

            issue_comments = fetch_issue_comments(session, owner, repo, number, headers, sleep=args.sleep)
            pr_review_comments = []
            if issue_obj.get("pull_request"):
                pr_review_comments = fetch_pr_review_comments(session, owner, repo, number, headers, sleep=args.sleep)

            comments_combined = []
            for c in issue_comments:
                comments_combined.append({
                    "type": "issue_comment",
                    "id": c.get("id"),
                    "body": c.get("body"),
                    "user": c.get("user", {}).get("login"),
                    "created_at": c.get("created_at"),
                    "updated_at": c.get("updated_at"),
                    "url": c.get("html_url") or c.get("url"),
                })
            for c in pr_review_comments:
                comments_combined.append({
                    "type": "review_comment",
                    "id": c.get("id"),
                    "body": c.get("body"),
                    "user": c.get("user", {}).get("login"),
                    "created_at": c.get("created_at"),
                    "updated_at": c.get("updated_at"),
                    "url": c.get("html_url") or c.get("url"),
                })

            # write per-issue JSONL
            jsonl_entry = {
                "issue_number": number,
                "owner": owner,
                "repo": repo,
                "html_url": html_url,
                "issue_title": issue_obj.get("title") if issue_obj else None,
                "comments": comments_combined,
            }
            jsonl_f.write(json.dumps(jsonl_entry, ensure_ascii=False) + "\n")

            # flatten comments to CSV rows
            for c in comments_combined:
                out_rows.append({
                    "issue_number": number,
                    "owner": owner,
                    "repo": repo,
                    "html_url": html_url,
                    "issue_title": issue_obj.get("title") if issue_obj else None,
                    "comment_type": c.get("type"),
                    "comment_id": c.get("id"),
                    "comment_user": c.get("user"),
                    "comment_created_at": c.get("created_at"),
                    "comment_updated_at": c.get("updated_at"),
                    "comment_url": c.get("url"),
                    "comment_body": c.get("body"),
                })

    # write CSV
    if out_rows:
        keys = list(out_rows[0].keys())
        with open(args.out_csv, "w", newline='', encoding="utf-8") as outf:
            writer = csv.DictWriter(outf, fieldnames=keys)
            writer.writeheader()
            for r in out_rows:
                writer.writerow(r)
        print(f"Wrote {len(out_rows)} comment rows to {args.out_csv}")
    else:
        print("No comments fetched; no CSV written.")


if __name__ == '__main__':
    main(sys.argv[1:])
