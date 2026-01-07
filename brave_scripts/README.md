# brave_scripts — quick usage

A short README describing how to run the helper scripts in this folder.

## Requirements
- Python 3.8+
- For fetch_issue_comments.py: requests, pandas, python-dotenv
- For gemini_classify.py: pandas, python-dotenv, google-genai

## Environment
- Set GITHUB_TOKEN for GitHub API access (recommended for higher rate limits).
- Set GOOGLE_API_KEY or GEMINI_API_KEY for Gemini calls.

## fetch_issue_comments.py 🔧
Fetches issue and PR review comments for issues listed in a CSV.

Usage:
```
python fetch_issue_comments.py --input invalid_sample_366.csv
```
Optional args: `--out-csv`, `--out-jsonl`, `--sleep`.
Outputs (defaults): `invalid_sample_no_duplicate_366_comments.csv`, `invalid_sample_no_duplicate_366_issue_comments.jsonl`.

## gemini_classify.py 🤖
Classifies issues using a Gemini model based on a system prompt and comments JSONL.

Usage:
```
python gemini_classify.py --input-jsonl invalid_sample_no_duplicate_366_issue_comments.jsonl \
  --system-prompt system_prompt.txt --out-csv classified.csv --model gemini-2.5-flash
```
Optional args: `--temperature`, `--max-output-tokens`, `--sleep`, `--truncate-chars`.
Outputs (defaults): `invalid_sample_no_duplicate_366_classified.csv`, `invalid_sample_no_duplicate_366_classified.jsonl`.

## Notes
- Use `--temperature 0` for deterministic classifications.
- Both scripts read environment variables (via `python-dotenv`) if present.
- Keep calls rate-limited and be mindful of API quotas.

