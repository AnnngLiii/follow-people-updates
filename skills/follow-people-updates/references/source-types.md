# Source Types

Use this file when adding a source or when a fetch result looks wrong.

## Registry Schema

Each person record has this shape:

```json
{
  "id": "andrej-karpathy",
  "name": "Andrej Karpathy",
  "kind": "engineer",
  "notes": "Optional notes",
  "sources": [
    {
      "id": "github-activity",
      "type": "github-user-events",
      "label": "GitHub activity",
      "enabled": true,
      "url": null,
      "params": {
        "username": "karpathy"
      },
      "seen_ids": [],
      "last_checked_at": null
    }
  ]
}
```

## Supported Types

### `rss`

- Required fields:
  - `url`
- Use for standard RSS feeds from blogs, newsletters, or site news pages.
- Prefer this over a manual `web-page` source whenever a feed exists.

### `atom`

- Required fields:
  - `url`
- Use for Atom feeds, including many engineering blogs and some author pages.

### `google-scholar`

- Required fields:
  - `url`
  - `params.user`
- Use for a public Google Scholar profile page such as `https://scholar.google.com/citations?user=<id>&hl=en`.
- The fetcher reads current-year papers from the profile table, then opens each citation detail page to collect publication date and description when available.
- Keep this intentionally simple: it is good enough for title + abstract style monitoring, but not a full bibliographic sync.

### `youtube-channel`

- Required fields:
  - `url`
- Supported URL shapes:
  - `https://www.youtube.com/@handle`
  - `https://www.youtube.com/channel/<channel-id>`
  - `https://www.youtube.com/c/<custom-name>`
  - `https://www.youtube.com/user/<legacy-name>`
- The fetcher resolves the channel page to a channel ID, then uses the official YouTube video feed.
- Use this for official channels when you want recent videos to be fetched automatically.

### `news-index`

- Required fields:
  - `url`
- Use for official news or blog index pages, including RSS feeds and supported official news pages.
- Prefer official organization pages over aggregator pages.

### `github-user-events`

- Required fields:
  - `params.username`
- Use for public GitHub activity across repositories.
- Good for a person whose work spans many repos.

### `github-org-repos`

- Required fields:
  - `params.org` or a GitHub organization URL
- Use for organization-level repository activity.
- Expect noise from active organizations; combine it with preference keywords or review filtering.

### `github-repo-releases`

- Required fields:
  - `params.repo`
- `repo` format:
  - `owner/name`
- Use for projects where releases matter more than day-to-day commits.

### `github-repo-commits`

- Required fields:
  - `params.repo`
- `repo` format:
  - `owner/name`
- Use sparingly. High-volume repos can overwhelm the digest.

### `github-trending`

- Optional fields:
  - `params.language`
  - `params.spoken_language_code`
  - `params.since`
- Use for broad discovery, not for precise people tracking.
- Treat results as weak signals because GitHub trending pages are snapshots.

### `github-topic`

- Required fields:
  - `params.topic` or a GitHub topic URL
- Use for broad discovery inside a topic such as `artificial-intelligence`.
- Treat results as weak signals and review for relevance before delivery.

### `arxiv-author`

- Required fields:
  - `params.author`
- Use the author name as it appears on arXiv.
- Good for fast-moving academic work and preprints.

### `crossref-author`

- Required fields:
  - `params.author`
- Use for papers that may not appear on arXiv.
- Expect noisier matches than author-homepage or arXiv feeds.

### `web-page`

- Required fields:
  - `url`
- This type is registry-only and intentionally not fetched by the script.
- X profile pages are currently kept here on purpose: they are deferred, not automatically fetched.
- Use it to keep a manual source in the watchlist when no feed or API exists.
- Common uses:
  - X profile pages
  - X search pages
  - YouTube search pages
  - GitHub Trending or topic pages
  - Official pages that require manual review

## Selection Guidance

Prefer one source per publication channel:

- Personal site with RSS: add `rss` or `atom`
- YouTube channel with an official channel page: add `youtube-channel`
- GitHub profile: add `github-user-events`
- Important project repo: add `github-repo-releases`
- Organization-level repo monitoring: add `github-org-repos`
- arXiv profile by author name: add `arxiv-author`
- Papers without good feeds: add `crossref-author`

Avoid redundant sources that emit the same items unless the user explicitly wants overlap for verification.

## Troubleshooting

If a fetch returns nothing:

1. Verify the stored URL or param value.
2. Re-run `scripts/fetch_updates.py --no-write --days 30` to inspect raw behavior without updating state.
3. Switch to a more precise source type if the current one is noisy.
4. Fall back to manual web browsing and keep the source as `web-page` if no machine-readable endpoint exists.
