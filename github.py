import os
import sys
import argparse
import requests
from dotenv import load_dotenv
import json

load_dotenv()

def parse_args():
    parser = argparse.ArgumentParser(
        description="Fetch up to 10 closed issues from a GitHub repository."
    )
    parser.add_argument("--owner", default=os.getenv("GITHUB_OWNER"), help="GitHub owner or organization")
    parser.add_argument("--repo", default=os.getenv("GITHUB_REPO"), help="GitHub repository name")
    parser.add_argument("--bug_label", default="type:bug", help="Label to filter invalid issue reports")
    parser.add_argument("--data_output_path", default="data.json", help="Path to output JSON data file")
    return parser.parse_args()

def get_github_token():
    token = os.getenv("GITHUB_TOKEN")
    if not token:
        print("Error: GITHUB_TOKEN environment variable is not set.", file=sys.stderr)
        sys.exit(1)
    return token


def fetch_closed_issues(owner: str, repo: str, token: str):
    """
    Fetch closed issues page-by-page until we collect at least 1 non-PR issue.
    Returns a list (possibly length 1).
    """
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "User-Agent": "simple-github-issues-script",
    }

    collected = []
    per_page = 100
    page = 1

    while True:
        print(f"Fetching page {page}...", file=sys.stderr)
        url = f"https://api.github.com/repos/{owner}/{repo}/issues"
        params = {
            "state": "closed",
            "per_page": per_page,
            "page": page,
            "sort": "created",
            "direction": "desc",
        }

        response = requests.get(url, params=params, headers=headers, timeout=30)

        if response.status_code == 403 and response.headers.get("X-RateLimit-Remaining") == "0":
            reset = response.headers.get("X-RateLimit-Reset")
            print(
                f"Rate limit reached. Stopping at page {page}. "
                f"X-RateLimit-Reset={reset}",
                file=sys.stderr,
            )
            break

        if response.status_code != 200:
            print(f"GitHub API error: {response.status_code} {response.text}", file=sys.stderr)
            sys.exit(1)

        issues = response.json()

        if not issues:  # no more pages
            break

        # FILTERING
        for issue in issues:

            if "pull_request" in issue:
                continue

            try:
                # Filtering issues that are initially labeled as 'type:bug' but later unlabeled
                events = fetch_issue_timeline(owner, repo, issue.get("number"), token)
            except RuntimeError as e:
                # fetch_issue_timeline signals rate limit via RuntimeError
                print(str(e), file=sys.stderr)
                # stop looping, but keep collected issues
                with open('data2.json', 'w') as f:
                    json.dump(collected, f, indent=2)
                return collected
            bug_events = [
                e for e in events
                if e.get("event") in ("labeled", "unlabeled")
                and e.get("label", {}).get("name") == "type:bug"
            ]

            # 1) If there are no bug events at all -> never had type:bug
            if not bug_events:
                continue

            # 2) Ensure it was labeled as bug at least once
            if not any(e.get("event") == "labeled" for e in bug_events):
                continue

            # 3) Check the *final* state of the bug label
            last_bug_event = max(bug_events, key=lambda e: e.get("created_at"))

            if last_bug_event.get("event") != "unlabeled":
                # Last event was 'labeled', so bug is still active or re-added later
                continue

            print(f"Found issue #{issue.get('number')} with initially labelled as 'type:bug' but then unlabelled from 'type:bug'", file=sys.stderr)
            collected.append(issue)

        page += 1

    # store raw JSON
    with open('data2.json', 'w') as f:
        json.dump(collected, f, indent=2)

    return collected

def fetch_issue_timeline(owner, repo, issue_number, token):
    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}/timeline"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github.mockingbird-preview+json",
        "User-Agent": "issue-timeline-script"
    }

    response = requests.get(url, headers=headers, timeout=30)
    response.raise_for_status()

    return response.json()

def main():
    args = parse_args()
    token = get_github_token()

    issues = fetch_closed_issues(args.owner, args.repo, token)

    # Simple output – you can replace this with your own processing functions
    print(f"Fetched {len(issues)} closed issues from {args.owner}/{args.repo}")
    for issue in issues:
        number = issue.get("number")
        title = issue.get("title")
        print(f"- #{number}: {title}")

    """ timeline = fetch_issue_timeline(args.owner, args.repo, 70311, token)
    print(f"\nTimeline for issue:")
    for event in timeline:
        event_type = event.get("event")
        created_at = event.get("created_at")
        print(f"- {event_type} at {created_at}") """

if __name__ == "__main__":
    main()