# Follow People Updates

Track recent work from people, labs, companies, and channels you care about, then produce a deduped daily digest from machine-readable sources and primary pages.

This repository is designed for local Codex-style automation, but the scripts are plain Python and shell. It intentionally ships with example state only. Your private tracking registry and generated digest history should stay local.

## What Is Included

- `skills/follow-people-updates/SKILL.md`: operating instructions for the skill.
- `skills/follow-people-updates/scripts/fetch_updates.py`: fetches recent source items and updates seen-item state.
- `skills/follow-people-updates/scripts/manage_tracking_registry.py`: creates and edits your tracking registry.
- `skills/follow-people-updates/scripts/fetch_youtube_transcript.py`: enriches YouTube items with transcripts when available.
- `skills/follow-people-updates/scripts/generate_daily_digest.py`: optional digest generator built on top of the fetcher.
- `skills/follow-people-updates/references/source-types.md`: registry source schema and source-type notes.
- `skills/follow-people-updates/assets/tracking-registry.example.json`: safe example registry.
- `scripts/follow_people_updates_fetch.sh`: portable wrapper for the fetcher.
- `automations/daily-people-updates.example.toml`: example automation prompt and schedule.

## Privacy Model

The public repo does not include personal run history, automation memory, local machine paths, API keys, or a real `tracking-registry.json`.

Private local files are ignored by default:

- `skills/follow-people-updates/assets/tracking-registry.json`
- `news/daily-digest-*.md`
- `.env`
- `.codex/`

The fetcher can use `GITHUB_TOKEN` or `GH_TOKEN` from your environment to improve GitHub API limits. Do not commit tokens or `.env` files.

## Install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## First Run

Create your private registry from the example:

```bash
cp skills/follow-people-updates/assets/tracking-registry.example.json \
  skills/follow-people-updates/assets/tracking-registry.json
```

Preview recent updates without mutating seen-item state:

```bash
./scripts/follow_people_updates_fetch.sh --json --no-write --days 3 --limit 5
```

Run the normal structured fetch and mark seen items:

```bash
./scripts/follow_people_updates_fetch.sh --json --new-only --days 3 --limit 20
```

The wrapper resolves paths relative to the repository root. If you keep your registry somewhere else, set:

```bash
export FOLLOW_PEOPLE_UPDATES_REGISTRY=/absolute/path/to/tracking-registry.json
```

## Manage The Registry

List tracked people and sources:

```bash
python3 skills/follow-people-updates/scripts/manage_tracking_registry.py list --sources --preferences
```

Add a person:

```bash
python3 skills/follow-people-updates/scripts/manage_tracking_registry.py add-person \
  --name "Andrej Karpathy" \
  --kind engineer \
  --notes "Track blog posts, videos, and GitHub activity."
```

Add a GitHub user source:

```bash
python3 skills/follow-people-updates/scripts/manage_tracking_registry.py add-source \
  --person "Andrej Karpathy" \
  --type github-user-events \
  --label "GitHub activity" \
  --param username=karpathy
```

Add an arXiv author source:

```bash
python3 skills/follow-people-updates/scripts/manage_tracking_registry.py add-source \
  --person "Fei-Fei Li" \
  --type arxiv-author \
  --label "arXiv author" \
  --param "author=Fei-Fei Li"
```

Disable a noisy source without deleting it:

```bash
python3 skills/follow-people-updates/scripts/manage_tracking_registry.py disable-source \
  --person "OpenAI" \
  --source github-repositories
```

## Supported Sources

Common source types:

- `rss` and `atom` for feeds.
- `news-index` for official news or blog pages.
- `youtube-channel` for official channel feeds.
- `github-user-events` for a person's public GitHub activity.
- `github-org-repos` for organization repository activity.
- `github-repo-releases` and `github-repo-commits` for specific repos.
- `github-trending` and `github-topic` for broad discovery.
- `arxiv-author`, `crossref-author`, and `google-scholar` for research outputs.
- `web-page` for manual sources such as X profiles or pages without reliable feeds.

See `skills/follow-people-updates/references/source-types.md` for schema details.

## Daily Automation

Use `automations/daily-people-updates.example.toml` as a template for your local automation. Replace any model, schedule, and working-directory settings to match your environment.

The recommended structured fetch is:

```bash
FOLLOW_PEOPLE_UPDATES_REQUEST_TIMEOUT=8 \
FOLLOW_PEOPLE_UPDATES_SOURCE_TIMEOUT=12 \
./scripts/follow_people_updates_fetch.sh --json --new-only --days 3 --limit 20
```

For recurring use, dedupe from both:

- the registry's `seen_ids`
- local digest files in `news/daily-digest-*.md`

Generated digest files are intentionally ignored by git.

## Public Repo Checklist

Before committing your own fork, run:

```bash
rg -n "(/Users/|Desktop/|CODEX_HOME|OPENAI_API_KEY|ANTHROPIC_API_KEY|GITHUB_TOKEN|GH_TOKEN|api[_-]?key|secret|password|token)" .
```

Expected matches should be documentation references to environment variable names only. Do not commit real paths, credentials, private digest history, or your private `tracking-registry.json`.
