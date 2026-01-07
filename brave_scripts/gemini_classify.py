"""
Classify issues using a Gemini model according to the provided system prompt.

Input:
  - JSONL where each line is one issue object containing at least:
      issue_number, owner, repo, issue_title, comments (list)
    Optionally:
      issue_body, html_url, etc.

Usage:
  python gemini_classify_jsonl.py \
    --input-jsonl invalid_sample_366_issue_comments.jsonl \
    --system-prompt system_prompt.md \
    --out-csv invalid_sample_366_classified.csv \
    --out-jsonl invalid_sample_366_classified.jsonl \
    --model gemini-2.0-flash

Requirements:
  - Python 3.8+
  - pandas
  - python-dotenv
  - google-genai   (pip install -U google-genai)

Auth:
  - Set GOOGLE_API_KEY or GEMINI_API_KEY in environment
"""

import argparse
import os
import time
import json
import re
from typing import Optional, Tuple, Any, Dict, List

from dotenv import load_dotenv
import pandas as pd

load_dotenv()

try:
    from google import genai
    from google.genai import types
except Exception:
    genai = None
    types = None

RESPONSE_SCHEMA = types.Schema(
    type=types.Type.OBJECT,
    required=["invalid_root_cause", "no_code_fix_present", "no_code_fix_comment"],
    properties={
        "invalid_root_cause": types.Schema(type=types.Type.STRING),
        "no_code_fix_present": types.Schema(type=types.Type.BOOLEAN),
        "no_code_fix_comment": types.Schema(
            type=types.Type.STRING,
            nullable=True
        ),
    },
)

# --------------------------
# Prompt helpers
# --------------------------

def load_system_prompt(path: str) -> str:
    with open(path, "r", encoding="utf-8") as f:
        return f.read()


def build_user_prompt_from_issue(
    issue: Dict[str, Any],
    max_chars: int = 6000
) -> str:
    """Build a user prompt from a JSONL issue object with 'comments' as a list."""
    issue_title = issue.get("issue_title") or issue.get("title")
    issue_body = issue.get("issue_body") or issue.get("body") or ""
    comments = issue.get("comments") or []

    parts: List[str] = []
    if issue_title:
        parts.append(f"Issue title: {issue_title}\n")
    if issue_body:
        parts.append(f"Issue description:\n{issue_body}\n")

    lines: List[str] = []
    if isinstance(comments, list):
        for c in comments:
            if not isinstance(c, dict):
                continue
            user = c.get("user") or c.get("login") or (c.get("author", {}) or {}).get("login") or "unknown"
            created = c.get("created_at") or c.get("created") or c.get("date") or ""
            body = c.get("body") or c.get("comment") or ""
            snippet = str(body).replace("\n", " ").strip()
            lines.append(f"[{user}] {created}: {snippet}")
    else:
        # if comments is not a list for some reason, stringify it
        lines.append(str(comments))

    combined = "\n".join(parts)
    combined += "\nComments (chronological):\n" + "\n".join(lines)

    # Truncate to max_chars, keeping head and tail
    if len(combined) > max_chars:
        half = max_chars // 2
        combined = combined[:half] + "\n...TRUNCATED...\n" + combined[-half:]

    return combined


# --------------------------
# JSON extraction + validation
# --------------------------

def extract_json_from_text(text: str) -> Optional[str]:
    """Try to extract the first JSON object from model output text."""
    text = re.sub(r"```(?:json)?\n", "", text)
    text = re.sub(r"\n```", "", text)

    # direct parse
    try:
        obj = json.loads(text)
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        pass

    # scan for first balanced {...}
    start = text.find("{")
    if start == -1:
        return None

    stack = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            stack += 1
        elif text[i] == "}":
            stack -= 1
            if stack == 0:
                candidate = text[start:i + 1]
                try:
                    obj = json.loads(candidate)
                    return json.dumps(obj, ensure_ascii=False)
                except Exception:
                    pass
    return None


def safe_parse_json(text: str) -> Tuple[Optional[dict], Optional[str]]:
    if not text or not isinstance(text, str):
        return None, "empty response"
    s = text.strip()

    try:
        return json.loads(s), None
    except Exception:
        extracted = extract_json_from_text(s)
        if not extracted:
            return None, "could not parse JSON from model response"
        try:
            return json.loads(extracted), None
        except Exception as e:
            return None, f"failed parsing extracted JSON: {e}"


def validate_classification(obj: dict) -> Tuple[bool, Optional[str]]:
    required_keys = {"invalid_root_cause", "no_code_fix_present", "no_code_fix_comment"}
    if not isinstance(obj, dict):
        return False, "not a JSON object"
    if not required_keys.issubset(set(obj.keys())):
        return False, f"missing keys: {required_keys - set(obj.keys())}"

    if not isinstance(obj["invalid_root_cause"], str):
        return False, "invalid_root_cause must be a string"
    if not isinstance(obj["no_code_fix_present"], bool):
        return False, "no_code_fix_present must be boolean"
    if obj["no_code_fix_present"] is False and obj["no_code_fix_comment"] is not None:
        return False, "no_code_fix_comment must be null when no_code_fix_present is false"

    return True, None


# --------------------------
# Gemini call (google-genai)
# --------------------------

def make_client():
    if genai is None or types is None:
        raise RuntimeError("google-genai not installed. Run: pip install -U google-genai")

    api_key = os.getenv("GOOGLE_API_KEY") or os.getenv("GEMINI_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GOOGLE_API_KEY or GEMINI_API_KEY environment variable")

    return genai.Client(api_key=api_key)


def call_gemini(
    client,
    system_prompt: str,
    user_prompt: str,
    model: str,
    max_output_tokens: int = 512,
    temperature: float = 0.0,
    retry: int = 3,
    sleep_backoff: float = 2.0,
) -> str:
    last_err = None
    for attempt in range(1, retry + 1):
        try:
            config = types.GenerateContentConfig(
                system_instruction=system_prompt,
                temperature=temperature,
                max_output_tokens=max_output_tokens,
                response_mime_type="application/json",
                response_schema=RESPONSE_SCHEMA,
            )

            resp = client.models.generate_content(
                model=model,
                contents=user_prompt,
                config=config,
            )

            text = getattr(resp, "text", None)
            return text if text else str(resp)

        except Exception as e:
            last_err = e
            wait = sleep_backoff ** attempt
            print(f"Call failed (attempt {attempt}/{retry}): {e}. Sleeping {wait:.1f}s and retrying.")
            time.sleep(wait)

    raise RuntimeError(f"Failed to call Gemini after {retry} attempts: {last_err}")


# --------------------------
# Main: JSONL -> classify
# --------------------------

def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-jsonl", required=True, help="Path to JSONL (one issue per line; includes comments list)")
    ap.add_argument("--system-prompt", default="system_prompt.txt", help="Path to system prompt file")
    ap.add_argument("--out-csv", default="invalid_sample_no_duplicate_366_classified.csv", help="Output CSV with classification results")
    ap.add_argument("--out-jsonl", default="invalid_sample_no_duplicate_366_classified.jsonl", help="Output JSONL with raw + parsed JSON per issue")
    ap.add_argument("--model", default="gemini-2.5-flash", help="Gemini model name")
    ap.add_argument("--temperature", type=float, default=0.0, help="Temperature (0 for deterministic)")
    ap.add_argument("--max-output-tokens", type=int, default=1536, help="Max tokens for model output")
    ap.add_argument("--sleep", type=float, default=0.5, help="Sleep between API calls (seconds)")
    ap.add_argument("--truncate-chars", type=int, default=6000, help="Max characters of combined prompt text")
    args = ap.parse_args(argv)

    system_prompt = load_system_prompt(args.system_prompt)
    client = make_client()

    out_jsonl_f = open(args.out_jsonl, "w", encoding="utf-8")
    results = []

    with open(args.input_jsonl, "r", encoding="utf-8") as f:
        for idx, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue

            issue = json.loads(line)

            issue_number = issue.get("issue_number")
            owner = issue.get("owner")
            repo = issue.get("repo")
            html_url = issue.get("html_url")
            title = issue.get("issue_title") or issue.get("title")

            base_prompt = build_user_prompt_from_issue(issue, max_chars=args.truncate_chars)

            user_prompt = (
                "Analyze the following single issue and its FULL comment thread and return ONLY the exact JSON object "
                "specified by the system prompt (no explanations):\n\n"
                + base_prompt
                + "\n\nImportant: Return exactly the JSON object with keys "
                  "'invalid_root_cause', 'no_code_fix_present', and 'no_code_fix_comment'."
            )

            raw_output = ""
            parsed_obj = None
            parse_error = None
            success = False

            try:
                raw_output = call_gemini(
                    client=client,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    model=args.model,
                    max_output_tokens=args.max_output_tokens,
                    temperature=args.temperature,
                )
                parsed_obj, parse_error = safe_parse_json(raw_output)
                if parsed_obj:
                    valid, v_err = validate_classification(parsed_obj)
                    if valid:
                        success = True
                    else:
                        parse_error = v_err or "validation failed"
            except Exception as e:
                parse_error = str(e)
            finally:
                print(f"Raw: {raw_output}")

            out = {
                "issue_number": issue_number,
                "owner": owner,
                "repo": repo,
                "html_url": html_url,
                "title": title,
                "comments_count": len(issue.get("comments") or []) if isinstance(issue.get("comments"), list) else None,
                "model": args.model,
                "raw_output": raw_output,
                "parsed_json": json.dumps(parsed_obj, ensure_ascii=False) if parsed_obj else None,
                "success": success,
                "error": parse_error,
                "invalid_root_cause": parsed_obj.get("invalid_root_cause") if isinstance(parsed_obj, dict) else None,
                "no_code_fix_present": parsed_obj.get("no_code_fix_present") if isinstance(parsed_obj, dict) else None,
                "no_code_fix_comment": parsed_obj.get("no_code_fix_comment") if isinstance(parsed_obj, dict) else None,
            }

            out_jsonl_f.write(json.dumps(out, ensure_ascii=False) + "\n")
            out_jsonl_f.flush()

            subclass = None
            fix_present = None

            if isinstance(parsed_obj, dict):
                subclass = parsed_obj.get("invalid_root_cause")
                fix_present = parsed_obj.get("no_code_fix_present")

            results.append(out)
            print(f"[{idx}] Issue {issue_number}: success={success} subclass={subclass} no_code_fix={fix_present} error={parse_error}")

            time.sleep(args.sleep)

    # Write outputs
    pd.DataFrame(results).to_csv(args.out_csv, index=False)
    print(f"Wrote classification CSV to {args.out_csv}")

    with open(args.out_jsonl, "w", encoding="utf-8") as jf:
        for r in results:
            jf.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"Wrote raw JSONL to {args.out_jsonl}")

    try:
        client.close()
    except Exception:
        pass
    finally:
        out_jsonl_f.close()


if __name__ == "__main__":
    main()
