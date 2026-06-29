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
- `skills/follow-people-updates/assets/tracking-registry.minimal.example.json`: smallest possible registry template, with one person and one source.
- `skills/follow-people-updates/assets/focus-profile.example.json`: safe example focus and output profile.
- `scripts/follow_people_updates_fetch.sh`: portable wrapper for the fetcher.
- `automations/daily-people-updates.example.toml`: example automation prompt and schedule.

## Privacy Model

The public repo does not include personal run history, automation memory, local machine paths, API keys, or a real `tracking-registry.json`.

Private local files are ignored by default:

- `skills/follow-people-updates/assets/tracking-registry.json`
- `skills/follow-people-updates/assets/focus-profile.json`
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

For the smallest possible starting point, use the minimal example instead:

```bash
cp skills/follow-people-updates/assets/tracking-registry.minimal.example.json \
  skills/follow-people-updates/assets/tracking-registry.json
```

Create your private focus profile from the example:

```bash
cp skills/follow-people-updates/assets/focus-profile.example.json \
  skills/follow-people-updates/assets/focus-profile.json
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

## Customize Tracking And Output

This project has two user-owned inputs:

- `tracking-registry.json`: who to track and which sources to scan.
- `focus-profile.json`: what counts as relevant and how the digest is formatted.

Both files are ignored by git. Start from the example files and edit your private copies.

### Focus Profile

Edit `skills/follow-people-updates/assets/focus-profile.json` when you want to change relevance and output style.

Common fields:

- `focus_keywords`: the main topic filter. Replace the example AI terms with your own focus, such as climate tech, biotech, security, education, policy, investing, or a specific product category.
- `secondary_insight_rules`: optional "why this matters" rules. Change the labels, keywords, and notes to match your project goals or audience.
- `recommended_tracks`: example people, labs, companies, or channels to suggest adding next.
- `low_signal_keywords`: heuristics for suggesting which tracked people or sources to downgrade.
- `item_template`: the Markdown output structure for each delivered item.

The default `item_template` writes:

```text
### Person | Title
- Date: YYYY-MM-DD
- Source: Source label (source-type)
- Background: ...
- What changed: ...
- Method: ...
- Result: ...
- Link: ...
```

Change the template if you want a different format, language, section order, scoring field, short-form summary, or newsletter-ready output. You usually do not need to edit Python for output-format changes.

### Tracking Registry

Edit `skills/follow-people-updates/assets/tracking-registry.json` when you want to add, remove, enable, or disable tracked people and sources.

User input requirements for each tracked person:

- `name`: the public name to show in the digest.
- `kind`: one of `scholar`, `engineer`, `mixed`, or `other`.
- `notes`: why you are tracking this person or organization.
- `sources`: one or more confirmed source endpoints.

Do not rely on guessed sources for recurring automation. Manually confirm each URL or source parameter before adding it:

- For GitHub users, confirm the username and use `github-user-events`.
- For GitHub organizations, confirm the org slug and use `github-org-repos`.
- For YouTube, confirm the official channel URL and use `youtube-channel`.
- For arXiv, confirm the author name spelling and use `arxiv-author`.
- For Crossref, confirm the author name is specific enough and expect noisier matches.
- For Google Scholar, confirm the profile URL and `user` id from the URL.
- For official blogs or news pages, prefer RSS/Atom feeds when available; otherwise use `news-index` or `web-page`.
- For X/Twitter or pages without reliable public feeds, keep them as `web-page`; they are manual/deferred sources.

The registry management commands below help edit the file, but they cannot verify identity by themselves. Treat the source URL or source parameter as user-confirmed input.

Generate a digest from the current registry:

```bash
python3 skills/follow-people-updates/scripts/generate_daily_digest.py \
  --days 3 \
  --limit 5 \
  --max-items 10 \
  --registry skills/follow-people-updates/assets/tracking-registry.json \
  --focus-profile skills/follow-people-updates/assets/focus-profile.json \
  --output-dir news
```

By default, the digest generator only uses items from your registry. To also include built-in discovery sources such as GitHub trending and Claude blog parsing, add `--include-discovery`:

```bash
python3 skills/follow-people-updates/scripts/generate_daily_digest.py \
  --days 3 \
  --limit 5 \
  --registry skills/follow-people-updates/assets/tracking-registry.json \
  --focus-profile skills/follow-people-updates/assets/focus-profile.json \
  --include-discovery \
  --output-dir news
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

Expected matches should be documentation references to environment variable names only. Do not commit real paths, credentials, private digest history, your private `tracking-registry.json`, or your private `focus-profile.json`.
