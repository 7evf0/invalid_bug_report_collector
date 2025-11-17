# Tensorflow Closed Issue Label Analyzer

This script scans all **closed issues** in a GitHub repository and extracts only those that:

* were **labeled** with a specific label (default: `type:bug`)
* later had that label **removed**
* and **never had it reapplied**

It uses GitHub’s **Issues API** and **Issue Timeline API** to detect label history and writes all matching issues into a JSON file.

---

## 📦 Installation

### 1. Clone the repository

```bash
git clone https://github.com/7evf0/invalid_bug_report_collector.git
cd invalid_bug_report_collector
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

Or manually:

```bash
pip install requests python-dotenv
```

---

## ⚙️ Configuration

Create a `.env` file in the same directory as the script:

```
GITHUB_TOKEN=ghp_xxxxxx
GITHUB_OWNER=your_owner
GITHUB_REPO=your_repo
```

* **GITHUB_TOKEN** must be a GitHub Personal Access Token (PAT) with `repo` read permissions.
* If `--owner` and `--repo` are not supplied as arguments, the script uses these environment variables.

---

## ▶️ Usage

Basic run:

```bash
python github.py
```

Specify a custom label:

```bash
python github.py --bug_label "type:bug"
```

Custom output file:

```bash
python github.py --data_output_path cleaned_issues.json
```

Explicitly set repo:

```bash
python github.py --owner tensorflow --repo tensorflow
```

---

## 🧾 Command-Line Arguments

| Argument             | Description                           | Default         |
| -------------------- | ------------------------------------- | --------------- |
| `--owner`            | GitHub owner/organization to scan     | `$GITHUB_OWNER` |
| `--repo`             | Repository name                       | `$GITHUB_REPO`  |
| `--bug_label`        | Label to detect and track removal of  | `"type:bug"`    |
| `--data_output_path` | Output JSON file for collected issues | `"data.json"`   |

---

## 💡 How Filtering Works (Short Explanation)

For each closed issue:

1. Skip pull requests.
2. Fetch its `/timeline` events.
3. Extract all `labeled` and `unlabeled` events for the label given by `--bug_label`.
4. Keep the issue only if:

   * the label was applied at least once
   * the **latest event** for that label is `unlabeled`

This identifies issues where the label was removed and **stayed removed**.

---

## 🚦 Rate Limit Behavior

* Timeline requests are expensive, so rate limits will occur for large repos.
* When the rate limit is hit, the script **stops gracefully** and **saves all collected issues so far** to the output JSON file.
* No progress is lost.
