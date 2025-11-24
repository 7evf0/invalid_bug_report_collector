import re
import requests
import json
import time
import os
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
INPUT_FILE = os.getenv("INPUT_FILE","tensorflow_current_log.txt")
OUTPUT_FILE = os.getenv("OUTPUT_FILE","tensorflow_issue_data.json")
REPO_OWNER = os.getenv("GITHUB_OWNER","tensorflow")
REPO_NAME = os.getenv("GITHUB_REPO","tensorflow")

HEADERS = {
    "Authorization": f"token {GITHUB_TOKEN}",
    "Accept": "application/vnd.github.v3+json"
}

def extract_issue_numbers(file_path):
    """
    Parses the log file to find issue numbers following the pattern:
    'Found issue #12345'
    """
    if not os.path.exists(file_path):
        print(f"Error: File {file_path} not found.")
        return []

    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # Regex to find numbers after 'Found issue #'
    issue_ids = re.findall(r"Found issue #(\d+)", content)
    
    # Remove duplicates and sort
    unique_ids = sorted(list(set(issue_ids)))
    print(f"Found {len(unique_ids)} unique issues to process.")
    return unique_ids

def fetch_issue_data(issue_number):
    """
    Fetches metadata and the timeline (comments + events) for a single issue.
    """
    base_url = f"https://api.github.com/repos/{REPO_OWNER}/{REPO_NAME}/issues/{issue_number}"
    
    # 1. Fetch Basic Metadata
    try:
        meta_response = requests.get(base_url, headers=HEADERS)
        if meta_response.status_code == 404:
            print(f"Issue #{issue_number} not found (404).")
            return None
        elif meta_response.status_code == 403:
            print("Rate limit exceeded. Check your token.")
            return None
            
        metadata = meta_response.json()
        
        # 2. Fetch Timeline (includes comments and label events)
        # We use pagination to ensure we get all events if there are many
        timeline_items = []
        page = 1
        while True:
            timeline_url = f"{base_url}/timeline?per_page=100&page={page}"
            timeline_response = requests.get(timeline_url, headers=HEADERS)
            
            if timeline_response.status_code != 200:
                break
                
            data = timeline_response.json()
            if not data:
                break
                
            timeline_items.extend(data)
            page += 1

        # Structure the final object
        issue_data = {
            "issue_number": issue_number,
            "title": metadata.get("title"),
            "state": metadata.get("state"),
            "created_at": metadata.get("created_at"),
            "closed_at": metadata.get("closed_at"),
            "body": metadata.get("body"),
            "user": metadata.get("user", {}).get("login"),
            "current_labels": [l["name"] for l in metadata.get("labels", [])],
            "timeline_data": timeline_items # Contains comments, labeled, unlabeled events
        }
        
        return issue_data

    except Exception as e:
        print(f"Error fetching issue #{issue_number}: {e}")
        return None

def main():
    if GITHUB_TOKEN == "YOUR_GITHUB_ACCESS_TOKEN_HERE":
        print("Please update the script with your GitHub Personal Access Token.")
        return

    ids = extract_issue_numbers(INPUT_FILE)
    
    all_data = []
    
    print("Starting data collection...")
    
    for i, issue_id in enumerate(ids):
        print(f"[{i+1}/{len(ids)}] Processing Issue #{issue_id}...")
        
        data = fetch_issue_data(issue_id)
        if data:
            all_data.append(data)
        
        # slight delay to be polite to the API
        time.sleep(0.5)

    # Save to JSON
    with open(OUTPUT_FILE, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, indent=4, ensure_ascii=False)
        
    print(f"\nDone! Data saved to {OUTPUT_FILE}")

if __name__ == "__main__":
    main()