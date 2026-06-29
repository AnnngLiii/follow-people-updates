---
name: follow-people-updates
description: Track recent work from named people across academic and technical sources. Use when Codex needs to monitor scholars, engineers, founders, or writers for newly published papers, preprints, blog posts, newsletters, release notes, or GitHub activity; maintain a persistent watchlist; add, remove, enable, or disable tracked sources; or produce a recent-update digest from feeds, APIs, and official profile pages.
---

# Follow People Updates

## Overview

Maintain a persistent registry of people and source endpoints, then fetch only the items that are new since the last check. Use bundled scripts for registry management and supported feed or API sources; use web browsing as a fallback for unsupported source types or broken feeds.

The default registry lives at `assets/tracking-registry.json`. Override it for testing or per-project setups with `FOLLOW_PEOPLE_UPDATES_REGISTRY=/path/to/registry.json`.

The optional focus profile lives at `assets/focus-profile.json`. It controls digest relevance, secondary insight rules, recommended tracks, low-signal heuristics, and item output templates. Override it with `FOLLOW_PEOPLE_UPDATES_FOCUS_PROFILE=/path/to/focus-profile.json` or pass `--focus-profile` to `scripts/generate_daily_digest.py`.

`scripts/generate_daily_digest.py` only uses registry items by default. Pass `--include-discovery` when the user explicitly wants extra built-in discovery sources such as GitHub trending and Claude blog parsing.

Current channel rule:

1. X profile and search sources are kept in the registry, but are temporarily deferred from automatic scanning.
2. Google Scholar profile pages can now be tracked automatically through the `google-scholar` source type.

## Default Output

When the user asks for a digest, use the current focus profile when one is available. `assets/focus-profile.json` controls relevance keywords, secondary insight rules, low-signal heuristics, recommended tracks, and the `item_template` used by `scripts/generate_daily_digest.py`.

If no focus profile is available, default to the last 30 days and write one block per result with this shape:

```text
人名
成果标题
成果简介
- 背景：
- 做了什么：
- 方法：
- 结果：
来源链接
```

Requirements:

1. Use absolute dates.
2. Only include items that can reasonably be dated within the last month.
3. Base the summary on the primary source page whenever possible, not only on feed metadata.
4. If one of the four summary fields is unavailable from the source, say `未明确说明`.
5. Preserve the original source link for every item.
6. Do not emit placeholder lines such as `简介：未提供摘要` for machine-readable sources until you have attempted source-page enrichment. If the feed payload has no usable summary, open the source URL and extract the abstract, meta description, transcript, or leading content first.
7. If a machine-readable item still has no usable summary after source-page enrichment, skip it unless the title alone is clearly high-value and the final output explicitly says the source page exposed no abstract or transcript.
8. Add a review section to the output that states:
   - which channels were scanned but had no updates
   - which channels failed
   - which channels were not scanned
   - which tracked people look low-value for the current focus profile
   - which new people or labs are worth following next and why

For YouTube items:

1. Prefer `youtube-channel` sources over plain channel pages so new videos can be fetched automatically.
2. First judge whether the video is relevant to the current focus profile from its title, description, and tags. If no focus profile is configured, default to AI-related topics, including technical AI work and AI-centered organization, management, adoption, governance, or workflow topics.
3. If a YouTube item is not relevant, do not write it to the markdown digest.
4. Before summarizing a delivered video, run `scripts/fetch_youtube_transcript.py --url <video-url>` when a transcript is available.
5. Use the transcript plus the video title, description, and tags to produce the `背景 / 做了什么 / 方法 / 结果` summary.
6. If a transcript is unavailable, say so explicitly and summarize from the video metadata or linked page.
7. Do not emit YouTube entries whose final summary would be four lines of `未明确说明`; skip them instead.

## Deduplication And Archiving

When the task is recurring, treat the registry state plus the archive document as the delivery history.

Rules:

1. Prefer `scripts/fetch_updates.py --new-only --days 30` so only previously unseen items are considered for delivery.
2. Before finalizing the digest, check the archive document for the same source URL or stable item title and do not resend matches.
3. Append each delivered batch to a Markdown archive file with the run timestamp and all emitted items.
4. If no new items remain after deduplication, still append a short run log entry stating that no new items were delivered.
5. Use a stable archive path when the user does not specify one. Default to `news/daily-digest-YYYY-MM-DD.md` under the current workspace, using the execution date in the filename.

## Manage The Registry

Tracked people and sources are user-confirmed inputs. Before adding a source, verify that the profile, channel, organization, author query, or feed URL actually belongs to the intended person or organization. The management commands edit the registry, but they do not prove identity.

Minimum input for a tracked person:

1. Public display name.
2. Kind: `scholar`, `engineer`, `mixed`, or `other`.
3. Notes explaining why this target is useful.
4. At least one confirmed source endpoint.

Source confirmation guidance:

- GitHub user: confirm the username, then use `github-user-events`.
- GitHub organization: confirm the org slug, then use `github-org-repos`.
- YouTube: confirm the official channel URL, then use `youtube-channel`.
- arXiv: confirm the author spelling, then use `arxiv-author`.
- Crossref: confirm the author query is specific enough; expect noisy matches.
- Google Scholar: confirm the profile URL and `user` id, then use `google-scholar`.
- Official blogs/news: prefer `rss` or `atom`; use `news-index` or `web-page` when no feed exists.
- X/Twitter or pages without reliable public feeds: use `web-page`; these are manual/deferred sources.

Initialize the registry before first use:

```bash
python3 scripts/manage_tracking_registry.py init
```

Add a tracked person:

```bash
python3 scripts/manage_tracking_registry.py add-person \
  --name "Andrej Karpathy" \
  --kind engineer \
  --notes "Track blog posts, essays, and GitHub activity."
```

Add sources for that person:

```bash
python3 scripts/manage_tracking_registry.py add-source \
  --person "Andrej Karpathy" \
  --type github-user-events \
  --label "GitHub activity" \
  --param username=karpathy

python3 scripts/manage_tracking_registry.py add-source \
  --person "Andrej Karpathy" \
  --type rss \
  --label "Personal blog" \
  --url "https://karpathy.ai/rss.xml"
```

Inspect the registry:

```bash
python3 scripts/manage_tracking_registry.py list
python3 scripts/manage_tracking_registry.py show --person "Andrej Karpathy"
```

Remove or disable a source without deleting the person:

```bash
python3 scripts/manage_tracking_registry.py disable-source \
  --person "Andrej Karpathy" \
  --source "github-activity"

python3 scripts/manage_tracking_registry.py remove-source \
  --person "Andrej Karpathy" \
  --source "github-activity"
```

Remove a person entirely:

```bash
python3 scripts/manage_tracking_registry.py remove-person --person "Andrej Karpathy"
```

## Fetch Updates

Check every enabled source and persist seen-item state:

```bash
python3 scripts/fetch_updates.py --new-only --days 30 --limit 5
```

Check only one person:

```bash
python3 scripts/fetch_updates.py --person "Andrej Karpathy" --new-only --days 30 --limit 5
```

Preview without mutating seen-item state:

```bash
python3 scripts/fetch_updates.py --person "Andrej Karpathy" --days 30 --limit 5 --no-write
```

Request JSON output when a downstream step needs structured post-processing:

```bash
python3 scripts/fetch_updates.py --json --new-only --days 30 --limit 10
```

Summarize results after the script runs:

1. Group by person.
2. Open each source URL that will appear in the final digest whenever the feed metadata is too thin to support `简介` or `背景 / 做了什么 / 方法 / 结果`.
3. Keep absolute dates.
4. Link each item to its original URL.
5. Separate academic outputs from technical outputs when that helps readability.
6. Mention skipped or broken sources explicitly.
7. For DOI or Crossref items with missing abstracts, resolve the landing page and extract a meta description, abstract, or first substantive paragraph before deciding whether the item is deliverable.

## Choose Sources

Prefer primary sources and stable machine-readable endpoints.

For scholars, prefer this order:

1. Author homepage publication feed or lab news feed
2. arXiv author search
3. Crossref author query
4. DBLP, Semantic Scholar, Google Scholar, or ORCID pages via web fallback

For technical people, prefer this order:

1. RSS or Atom feeds for blogs, newsletters, or personal sites
2. GitHub user events for broad activity
3. YouTube channels for tutorial or announcement videos
4. GitHub repo releases for project milestones
5. GitHub repo commits for low-level code movement
6. X profile or search pages via web fallback
7. Static profile or archive pages via web fallback

Keep sources granular. If a person publishes in multiple places, add multiple sources instead of relying on one broad home page.

## Fall Back To Web

Use web browsing when:

- the source type is unsupported by `scripts/fetch_updates.py`
- the source has no feed or API
- the API result quality is poor
- the user asks for direct source verification

When falling back:

1. Read the registered source metadata first.
2. Search only the relevant source and the person name.
3. Prefer official or primary pages over aggregator summaries.
4. Use concrete dates in the final summary.
5. If a source repeatedly needs manual browsing, keep it in the registry anyway and note that it is a manual-check source.

## Supported Source Types

Read `references/source-types.md` when adding or troubleshooting sources. The bundled fetcher supports:

- `rss`
- `atom`
- `news-index`
- `youtube-channel`
- `github-user-events`
- `github-org-repos`
- `github-repo-releases`
- `github-repo-commits`
- `github-trending`
- `github-topic`
- `arxiv-author`
- `crossref-author`
- `google-scholar`
- `web-page` as a registry-only manual source

Use `web-page` for X profiles, X searches, YouTube search pages, and hot-post dashboards that do not have a reliable public feed.

## Resources

### scripts/

- `scripts/manage_tracking_registry.py`: Initialize and mutate the persistent registry.
- `scripts/fetch_updates.py`: Fetch recent items from supported sources and mark seen items.
- `scripts/fetch_youtube_transcript.py`: Fetch the transcript for one YouTube video URL or ID.

### references/

- `references/source-types.md`: Source-type schema, required parameters, and troubleshooting notes.

### assets/

- `assets/tracking-registry.json`: Persistent watchlist and per-source state.
- `assets/tracking-registry.minimal.example.json`: Minimal one-person, one-source registry example.
- `assets/focus-profile.json`: Private relevance and digest-format profile.
