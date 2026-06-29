#!/usr/bin/env python3

import argparse
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_REGISTRY = SKILL_DIR / "assets" / "tracking-registry.json"
SUPPORTED_SOURCE_TYPES = {
    "rss",
    "atom",
    "google-scholar",
    "news-index",
    "youtube-channel",
    "github-user-events",
    "github-org-repos",
    "github-repo-releases",
    "github-repo-commits",
    "github-trending",
    "github-topic",
    "arxiv-author",
    "crossref-author",
    "web-page",
}
SUPPORTED_KINDS = {"scholar", "engineer", "mixed", "other"}
SOURCE_TYPE_ALIASES = {
    "rss": "rss",
    "atom": "atom",
    "blog": "rss",
    "scholar": "google-scholar",
    "google-scholar": "google-scholar",
    "news": "news-index",
    "blog-index": "news-index",
    "news-index": "news-index",
    "youtube": "youtube-channel",
    "youtube-channel": "youtube-channel",
    "yt": "youtube-channel",
    "x": "web-page",
    "twitter": "web-page",
    "x-profile": "web-page",
    "github": "github-user-events",
    "gh": "github-user-events",
    "github-user-events": "github-user-events",
    "github-org": "github-org-repos",
    "github-org-repos": "github-org-repos",
    "github-releases": "github-repo-releases",
    "repo-releases": "github-repo-releases",
    "github-repo-releases": "github-repo-releases",
    "github-commits": "github-repo-commits",
    "repo-commits": "github-repo-commits",
    "github-repo-commits": "github-repo-commits",
    "github-trending": "github-trending",
    "github-topic": "github-topic",
    "arxiv": "arxiv-author",
    "arxiv-author": "arxiv-author",
    "crossref": "crossref-author",
    "crossref-author": "crossref-author",
    "web": "web-page",
    "web-page": "web-page",
}


def registry_path() -> Path:
    override = os.environ.get("FOLLOW_PEOPLE_UPDATES_REGISTRY")
    return Path(override).expanduser() if override else DEFAULT_REGISTRY


def default_preferences():
    return {
        "themes": [],
        "keyword_boosts": [],
        "keyword_penalties": [],
    }


def default_registry():
    return {
        "version": 2,
        "defaults": {"max_seen_ids_per_source": 100},
        "preferences": default_preferences(),
        "people": [],
    }


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def slugify(value: str) -> str:
    lowered = value.strip().lower()
    slug = re.sub(r"[^\w]+", "-", lowered, flags=re.UNICODE)
    slug = re.sub(r"_+", "-", slug)
    slug = re.sub(r"-{2,}", "-", slug).strip("-")
    if slug:
        return slug
    codepoints = "-".join(f"{ord(ch):x}" for ch in value.strip() if not ch.isspace())
    return f"u-{codepoints}" if codepoints else "item"


def make_unique_id(existing_ids, base: str) -> str:
    candidate = base
    counter = 2
    while candidate in existing_ids:
        candidate = f"{base}-{counter}"
        counter += 1
    return candidate


def unique_preserving_order(values):
    seen = set()
    result = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def ensure_source_shape(source):
    source.setdefault("enabled", True)
    source.setdefault("url", None)
    source.setdefault("params", {})
    source.setdefault("seen_ids", [])
    source.setdefault("last_checked_at", None)
    source.setdefault("resolution", {})
    source["resolution"].setdefault("confidence", "confirmed")
    source["resolution"].setdefault("canonical_url", source.get("url"))
    source["resolution"].setdefault("canonical_handle", None)
    return source


def normalize_person(person):
    person.setdefault("notes", "")
    person.setdefault("aliases", [])
    person.setdefault("identities", {})
    person.setdefault("sources", [])
    person["sources"] = [ensure_source_shape(source) for source in person["sources"]]
    return person


def ensure_registry_shape(data):
    if not isinstance(data, dict):
        raise ValueError("Registry root must be a JSON object.")
    data.setdefault("version", 2)
    data.setdefault("defaults", {"max_seen_ids_per_source": 100})
    data.setdefault("preferences", default_preferences())
    prefs = data["preferences"]
    prefs.setdefault("themes", [])
    prefs.setdefault("keyword_boosts", [])
    prefs.setdefault("keyword_penalties", [])
    data.setdefault("people", [])
    data["people"] = [normalize_person(person) for person in data["people"]]
    return data


def load_registry(path: Path):
    if not path.exists():
        return default_registry()
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    return ensure_registry_shape(data)


def save_registry(path: Path, registry) -> None:
    ensure_parent(path)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(registry, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def parse_params(param_list):
    params = {}
    for item in param_list or []:
        if "=" not in item:
            raise ValueError(f"Invalid --param value '{item}'. Use key=value.")
        key, value = item.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key or not value:
            raise ValueError(f"Invalid --param value '{item}'. Use key=value.")
        params[key] = value
    return params


def match_candidates(items, query, name_key="name"):
    lowered = query.strip().lower()
    exact = [
        item for item in items
        if item.get("id", "").lower() == lowered or item.get(name_key, "").lower() == lowered
    ]
    if exact:
        return exact
    partial = [
        item for item in items
        if lowered in item.get("id", "").lower() or lowered in item.get(name_key, "").lower()
    ]
    return partial


def resolve_person(registry, query):
    people = registry.get("people", [])
    lowered = query.strip().lower()
    matches = match_candidates(people, query)
    if not matches:
        matches = [
            person for person in people
            if lowered in [alias.lower() for alias in person.get("aliases", [])]
        ]
    if not matches:
        raise ValueError(f"No person matched '{query}'.")
    if len(matches) > 1:
        matched = ", ".join(person["name"] for person in matches)
        raise ValueError(f"Ambiguous person '{query}'. Matches: {matched}")
    return matches[0]


def resolve_source(person, query):
    sources = person.get("sources", [])
    matches = match_candidates(sources, query, name_key="label")
    if matches:
        if len(matches) > 1:
            matched = ", ".join(source["label"] for source in matches)
            raise ValueError(f"Ambiguous source '{query}'. Matches: {matched}")
        return matches[0]

    canonical_type = SOURCE_TYPE_ALIASES.get(query.strip().lower())
    if canonical_type:
        typed_matches = [source for source in sources if source.get("type") == canonical_type]
        if len(typed_matches) == 1:
            return typed_matches[0]
        if len(typed_matches) > 1:
            matched = ", ".join(source["label"] for source in typed_matches)
            raise ValueError(f"Ambiguous source '{query}'. Matches: {matched}")

    raise ValueError(f"No source matched '{query}' for {person['name']}.")


def validate_source_fields(source_type: str, url: str, params):
    if source_type not in SUPPORTED_SOURCE_TYPES:
        supported = ", ".join(sorted(SUPPORTED_SOURCE_TYPES))
        raise ValueError(f"Unsupported source type '{source_type}'. Supported: {supported}")

    if source_type in {"rss", "atom", "youtube-channel", "google-scholar", "news-index", "web-page"} and not url:
        raise ValueError(f"Source type '{source_type}' requires --url.")
    if source_type == "github-user-events" and "username" not in params:
        raise ValueError("Source type 'github-user-events' requires --param username=<name>.")
    if source_type == "github-org-repos" and "org" not in params:
        raise ValueError("Source type 'github-org-repos' requires --param org=<name>.")
    if source_type in {"github-repo-releases", "github-repo-commits"} and "repo" not in params:
        raise ValueError(f"Source type '{source_type}' requires --param repo=owner/name.")
    if source_type == "github-topic" and "topic" not in params:
        raise ValueError("Source type 'github-topic' requires --param topic=<slug>.")
    if source_type in {"arxiv-author", "crossref-author"} and "author" not in params:
        raise ValueError(f"Source type '{source_type}' requires --param author='Full Name'.")
    if source_type == "google-scholar" and "user" not in params:
        raise ValueError("Source type 'google-scholar' requires a Google Scholar profile URL or --param user=<id>.")
    if source_type == "news-index" and "site" not in params:
        raise ValueError("Source type 'news-index' requires --param site=<openai|google-ai|claude-blog>.")


def canonical_source_type(source_hint: Optional[str], url: Optional[str]):
    if source_hint:
        lowered = source_hint.strip().lower()
        if lowered in SOURCE_TYPE_ALIASES:
            return SOURCE_TYPE_ALIASES[lowered]
        supported = ", ".join(sorted(SOURCE_TYPE_ALIASES))
        raise ValueError(f"Unsupported source hint '{source_hint}'. Supported: {supported}")

    if not url:
        raise ValueError("Provide either --source or --url.")

    lowered = url.lower()
    if "youtube.com" in lowered or "youtu.be" in lowered:
        return "youtube-channel"
    if "github.com/trending" in lowered:
        return "github-trending"
    if "github.com/topics/" in lowered:
        return "github-topic"
    if "github.com" in lowered:
        return "web-page"
    if "scholar.google.com/citations" in lowered:
        return "google-scholar"
    if "openai.com/news" in lowered or "blog.google/innovation-and-ai" in lowered or "claude.com/blog" in lowered:
        return "news-index"
    if "x.com" in lowered or "twitter.com" in lowered:
        return "web-page"
    if "arxiv.org" in lowered:
        return "arxiv-author"
    if lowered.endswith(".atom") or "atom" in lowered:
        return "atom"
    if lowered.endswith(".rss") or lowered.endswith(".xml") or "feed" in lowered:
        return "rss"
    return "web-page"


def preferred_author_name(person, key):
    author_names = (person.get("identities") or {}).get("author_names") or {}
    values = author_names.get(key) or []
    return values[0] if values else person["name"]


def extract_x_handle(url: str):
    match = re.match(r"https?://(?:www\.)?(?:x|twitter)\.com/([^/?#]+)/?$", url, flags=re.IGNORECASE)
    return match.group(1) if match else None


def extract_google_scholar_user(url: str):
    match = re.search(r"[?&]user=([^&#]+)", url, flags=re.IGNORECASE)
    return match.group(1) if match else None


def extract_youtube_handle(url: str):
    match = re.search(r"youtube\.com/@([^/?#]+)", url, flags=re.IGNORECASE)
    return match.group(1) if match else None


def extract_youtube_channel_id(url: str):
    match = re.search(r"youtube\.com/channel/(UC[^/?#]+)", url, flags=re.IGNORECASE)
    return match.group(1) if match else None


def request_text(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def resolve_youtube_channel_metadata(url: str):
    handle = extract_youtube_handle(url)
    channel_id = extract_youtube_channel_id(url)
    if channel_id:
        return handle, channel_id
    try:
        html = request_text(url)
    except Exception:
        return handle, channel_id
    match = re.search(r'"channelId":"(UC[^"]+)"', html) or re.search(r'"externalId":"(UC[^"]+)"', html)
    if match:
        channel_id = match.group(1)
    return handle, channel_id


def extract_github_username(url: str):
    match = re.match(r"https?://(?:www\.)?github\.com/([^/?#]+)/?$", url, flags=re.IGNORECASE)
    if not match:
        return None
    value = match.group(1)
    if value.lower() in {"search", "topics", "trending"}:
        return None
    return value


def extract_github_repo(url: str):
    match = re.match(r"https?://(?:www\.)?github\.com/([^/?#]+)/([^/?#]+)/?", url, flags=re.IGNORECASE)
    if not match:
        return None
    return f"{match.group(1)}/{match.group(2)}"


def source_signature(source_type: str, url: Optional[str], params):
    params = params or {}
    if source_type in {"arxiv-author", "crossref-author"}:
        return (source_type, (params.get("author") or "").strip().lower())
    if source_type == "google-scholar":
        return (source_type, (params.get("user") or "").strip().lower())
    if source_type == "github-user-events":
        return (source_type, (params.get("username") or "").strip().lower())
    if source_type == "github-org-repos":
        return (source_type, (params.get("org") or "").strip().lower())
    if source_type in {"github-repo-releases", "github-repo-commits"}:
        return (source_type, (params.get("repo") or "").strip().lower())
    if source_type == "github-topic":
        return (source_type, (params.get("topic") or "").strip().lower())
    return (source_type, (url or "").strip().lower())


def merge_identity_data(person, identity_updates):
    if not identity_updates:
        return
    identities = person.setdefault("identities", {})
    for key, value in identity_updates.items():
        if isinstance(value, dict):
            target = identities.setdefault(key, {})
            for inner_key, inner_value in value.items():
                if isinstance(inner_value, list):
                    target[inner_key] = unique_preserving_order((target.get(inner_key) or []) + inner_value)
                else:
                    target[inner_key] = inner_value
        elif isinstance(value, list):
            identities[key] = unique_preserving_order((identities.get(key) or []) + value)
        else:
            identities[key] = value


def infer_source_definition(person, source_hint: Optional[str], url: Optional[str], label: Optional[str]):
    source_type = canonical_source_type(source_hint, url)
    person_name = person["name"]
    identities = person.get("identities") or {}
    identity_updates = {}
    resolution = {"confidence": "confirmed" if url else "inferred", "canonical_url": url, "canonical_handle": None}
    params = {}

    if source_type in {"rss", "atom"}:
        if not url:
            raise ValueError("Blog or feed sources require --url.")
        label = label or ("RSS feed" if source_type == "rss" else "Atom feed")

    elif source_type == "arxiv-author":
        author_name = preferred_author_name(person, "arxiv")
        params = {"author": author_name}
        if not url:
            quoted = urllib.parse.quote(author_name)
            url = (
                "https://export.arxiv.org/api/query"
                f"?search_query=au:%22{quoted}%22&start=0&max_results=10&sortBy=submittedDate&sortOrder=descending"
            )
        label = label or "arXiv author"
        resolution["canonical_url"] = url
        identity_updates["author_names"] = {"arxiv": [author_name]}

    elif source_type == "crossref-author":
        author_name = preferred_author_name(person, "crossref")
        params = {"author": author_name}
        if not url:
            quoted = urllib.parse.quote(author_name)
            url = f"https://api.crossref.org/works?query.author={quoted}&sort=published&order=desc&rows=20"
        label = label or "Crossref author"
        resolution["canonical_url"] = url
        identity_updates["author_names"] = {"crossref": [author_name]}

    elif source_type == "google-scholar":
        known_scholar = (identities.get("google_scholar") or {}).get("user")
        user = extract_google_scholar_user(url or "") or known_scholar
        if not user:
            raise ValueError("Google Scholar sources require a profile URL with ?user=<id>.")
        url = url or f"https://scholar.google.com/citations?user={user}&hl=en"
        params = {"user": user}
        label = label or "Google Scholar"
        resolution["canonical_url"] = url
        identity_updates["google_scholar"] = {"user": user, "url": url}

    elif source_type == "news-index":
        lowered = (url or "").lower()
        if "openai.com/news" in lowered:
            site = "openai"
            label = label or "Official news"
        elif "blog.google/innovation-and-ai" in lowered:
            site = "google-ai"
            label = label or "Official blog"
        elif "claude.com/blog" in lowered:
            site = "claude-blog"
            label = label or "Claude blog"
        else:
            raise ValueError("Unsupported news index URL. Supported: OpenAI news, Google innovation-and-ai, Claude blog.")
        params = {"site": site}
        resolution["canonical_url"] = url

    elif source_type == "youtube-channel":
        known_youtube = identities.get("youtube") or {}
        if not url:
            url = known_youtube.get("url")
        if not url:
            url = f"https://www.youtube.com/results?search_query={urllib.parse.quote(person_name)}"
            source_type = "web-page"
            label = label or "YouTube search"
            resolution["confidence"] = "needs_review"
            resolution["canonical_url"] = url
        else:
            label = label or "YouTube channel"
            handle, channel_id = resolve_youtube_channel_metadata(url)
            if channel_id:
                params["channel_id"] = channel_id
            resolution["canonical_url"] = url
            resolution["canonical_handle"] = handle or channel_id
            update_payload = {"url": url}
            if handle:
                update_payload["handle"] = handle
            if channel_id:
                update_payload["channel_id"] = channel_id
            identity_updates["youtube"] = update_payload

    elif source_type == "github-user-events":
        known_username = ((identities.get("github") or {}).get("username"))
        username = None
        if url:
            username = extract_github_username(url)
        if not username:
            username = known_username
        if not username:
            url = f"https://github.com/search?q=%22{urllib.parse.quote_plus(person_name)}%22&type=users"
            source_type = "web-page"
            label = label or "GitHub search"
            resolution["confidence"] = "needs_review"
            resolution["canonical_url"] = url
        else:
            params = {"username": username}
            url = url or f"https://github.com/{username}"
            label = label or "GitHub activity"
            resolution["canonical_url"] = url
            resolution["canonical_handle"] = username
            identity_updates["github"] = {"username": username, "url": url}

    elif source_type == "github-org-repos":
        known_username = ((identities.get("github") or {}).get("username"))
        org = None
        if url:
            org = extract_github_username(url)
        if not org:
            org = known_username
        if not org:
            raise ValueError("GitHub org sources require an org URL like https://github.com/deepseek-ai.")
        params = {"org": org}
        url = url or f"https://github.com/{org}"
        label = label or "GitHub repositories"
        resolution["canonical_url"] = url
        resolution["canonical_handle"] = org
        identity_updates["github"] = {"username": org, "url": url}

    elif source_type in {"github-repo-releases", "github-repo-commits"}:
        repo = extract_github_repo(url or "")
        if not repo:
            raise ValueError("GitHub repo sources require a repo URL like https://github.com/owner/name.")
        params = {"repo": repo}
        url = url or f"https://github.com/{repo}"
        label = label or ("GitHub releases" if source_type == "github-repo-releases" else "GitHub commits")
        resolution["canonical_url"] = url
        resolution["canonical_handle"] = repo

    elif source_type == "github-trending":
        url = url or "https://github.com/trending?since=daily"
        label = label or "GitHub trending"
        resolution["canonical_url"] = url

    elif source_type == "github-topic":
        topic = None
        if url:
            match = re.search(r"github\.com/topics/([^/?#]+)", url, flags=re.IGNORECASE)
            if match:
                topic = match.group(1)
        if not topic:
            raise ValueError("GitHub topic sources require a topic URL like https://github.com/topics/artificial-intelligence.")
        params = {"topic": topic}
        url = url or f"https://github.com/topics/{topic}"
        label = label or "GitHub topic"
        resolution["canonical_url"] = url
        resolution["canonical_handle"] = topic

    elif source_type == "web-page":
        if url and ("x.com" in url.lower() or "twitter.com" in url.lower()):
            label = label or "X profile"
            handle = extract_x_handle(url)
            resolution["canonical_handle"] = handle
            identity_updates["x"] = {"url": url, "handle": handle}
        else:
            label = label or "Web page"
        if not url and source_hint and source_hint.strip().lower() in {"x", "twitter"}:
            url = f"https://x.com/search?q=%22{urllib.parse.quote(person_name)}%22&src=typed_query&f=live"
            label = label or "X search"
            resolution["confidence"] = "needs_review"
        elif not url:
            raise ValueError("Web-page sources require --url.")
        resolution["canonical_url"] = url

    validate_source_fields(source_type, url, params)
    source = {
        "type": source_type,
        "label": label.strip(),
        "enabled": True,
        "url": url,
        "params": params,
        "seen_ids": [],
        "last_checked_at": None,
        "resolution": resolution,
    }
    return source, identity_updates


def cmd_init(args):
    path = registry_path()
    if path.exists() and not args.force:
        print(f"Registry already exists: {path}")
        return 0
    save_registry(path, default_registry())
    print(f"Initialized registry: {path}")
    return 0


def cmd_list(args):
    path = registry_path()
    registry = load_registry(path)
    print(f"Registry: {path}")
    if args.preferences:
        print("Preferences:")
        print(json.dumps(registry.get("preferences", {}), ensure_ascii=False, indent=2))
    people = registry.get("people", [])
    if not people:
        print("No tracked people.")
        return 0
    for person in sorted(people, key=lambda item: item["name"].lower()):
        enabled_count = sum(1 for source in person.get("sources", []) if source.get("enabled", True))
        source_count = len(person.get("sources", []))
        print(f"- {person['name']} [{person['id']}] kind={person.get('kind', 'other')} sources={enabled_count}/{source_count} enabled")
        if args.sources:
            for source in person.get("sources", []):
                state = "enabled" if source.get("enabled", True) else "disabled"
                handle = (source.get("resolution") or {}).get("canonical_handle")
                detail = f" handle={handle}" if handle else ""
                print(f"    - {source['label']} [{source['id']}] type={source['type']} {state}{detail}")
    return 0


def cmd_show(args):
    path = registry_path()
    registry = load_registry(path)
    person = resolve_person(registry, args.person)
    print(json.dumps(person, ensure_ascii=False, indent=2))
    return 0


def cmd_add_person(args):
    if args.kind not in SUPPORTED_KINDS:
        supported = ", ".join(sorted(SUPPORTED_KINDS))
        raise ValueError(f"Unsupported kind '{args.kind}'. Supported: {supported}")

    path = registry_path()
    registry = load_registry(path)
    existing_ids = {person["id"] for person in registry["people"]}
    base_id = slugify(args.name)
    person_id = make_unique_id(existing_ids, base_id)

    person = {
        "id": person_id,
        "name": args.name.strip(),
        "kind": args.kind,
        "notes": args.notes or "",
        "identities": {},
        "sources": [],
    }
    registry["people"].append(person)
    save_registry(path, registry)
    print(f"Added person: {person['name']} [{person['id']}]")
    return 0


def cmd_remove_person(args):
    path = registry_path()
    registry = load_registry(path)
    person = resolve_person(registry, args.person)
    registry["people"] = [item for item in registry["people"] if item["id"] != person["id"]]
    save_registry(path, registry)
    print(f"Removed person: {person['name']} [{person['id']}]")
    return 0


def cmd_add_source(args):
    path = registry_path()
    registry = load_registry(path)
    person = resolve_person(registry, args.person)
    params = parse_params(args.param)
    validate_source_fields(args.type, args.url, params)

    existing_ids = {source["id"] for source in person.get("sources", [])}
    base_id = slugify(args.source_id or args.label)
    source_id = make_unique_id(existing_ids, base_id)

    source = {
        "id": source_id,
        "type": args.type,
        "label": args.label.strip(),
        "enabled": True,
        "url": args.url,
        "params": params,
        "seen_ids": [],
        "last_checked_at": None,
        "resolution": {
            "confidence": "confirmed",
            "canonical_url": args.url,
            "canonical_handle": None,
        },
    }
    person.setdefault("sources", []).append(source)
    save_registry(path, registry)
    print(f"Added source: {person['name']} -> {source['label']} [{source['id']}]")
    return 0


def cmd_link_source(args):
    path = registry_path()
    registry = load_registry(path)
    person = resolve_person(registry, args.person)
    source, identity_updates = infer_source_definition(person, args.source, args.url, args.label)
    signature = source_signature(source["type"], source.get("url"), source.get("params"))
    for existing in person.get("sources", []):
        if source_signature(existing["type"], existing.get("url"), existing.get("params")) == signature:
            existing.setdefault("resolution", {})
            existing["resolution"]["confidence"] = source["resolution"].get("confidence", existing["resolution"].get("confidence"))
            existing["resolution"]["canonical_url"] = source["resolution"].get("canonical_url") or existing["resolution"].get("canonical_url")
            existing["resolution"]["canonical_handle"] = source["resolution"].get("canonical_handle") or existing["resolution"].get("canonical_handle")
            if not existing.get("url") and source.get("url"):
                existing["url"] = source["url"]
            if source.get("params"):
                merged_params = dict(existing.get("params") or {})
                merged_params.update(source["params"])
                existing["params"] = merged_params
            merge_identity_data(person, identity_updates)
            save_registry(path, registry)
            print(f"Source already linked: {person['name']} -> {existing['label']} [{existing['id']}]")
            return 0

    existing_ids = {item["id"] for item in person.get("sources", [])}
    base_id = slugify(args.source_id or source["label"])
    source["id"] = make_unique_id(existing_ids, base_id)
    person.setdefault("sources", []).append(source)
    merge_identity_data(person, identity_updates)
    save_registry(path, registry)
    print(f"Linked source: {person['name']} -> {source['label']} [{source['id']}]")
    return 0


def cmd_remove_source(args):
    path = registry_path()
    registry = load_registry(path)
    person = resolve_person(registry, args.person)
    source = resolve_source(person, args.source)
    person["sources"] = [item for item in person.get("sources", []) if item["id"] != source["id"]]
    save_registry(path, registry)
    print(f"Removed source: {person['name']} -> {source['label']} [{source['id']}]")
    return 0


def cmd_toggle_source(args, enabled: bool):
    path = registry_path()
    registry = load_registry(path)
    person = resolve_person(registry, args.person)
    source = resolve_source(person, args.source)
    source["enabled"] = enabled
    save_registry(path, registry)
    status = "enabled" if enabled else "disabled"
    print(f"{status.capitalize()} source: {person['name']} -> {source['label']} [{source['id']}]")
    return 0


def cmd_set_preferences(args):
    path = registry_path()
    registry = load_registry(path)
    prefs = registry["preferences"]

    if args.clear_themes:
        prefs["themes"] = []
    elif args.theme is not None:
        prefs["themes"] = unique_preserving_order(args.theme)

    if args.clear_keyword_boosts:
        prefs["keyword_boosts"] = []
    elif args.keyword_boost is not None:
        prefs["keyword_boosts"] = unique_preserving_order(args.keyword_boost)

    if args.clear_keyword_penalties:
        prefs["keyword_penalties"] = []
    elif args.keyword_penalty is not None:
        prefs["keyword_penalties"] = unique_preserving_order(args.keyword_penalty)

    save_registry(path, registry)
    print(json.dumps(prefs, ensure_ascii=False, indent=2))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(description="Manage the follow-people-updates registry.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init_parser = subparsers.add_parser("init", help="Initialize the registry file.")
    init_parser.add_argument("--force", action="store_true", help="Overwrite an existing registry.")
    init_parser.set_defaults(func=cmd_init)

    list_parser = subparsers.add_parser("list", help="List tracked people.")
    list_parser.add_argument("--sources", action="store_true", help="Show source lines under each person.")
    list_parser.add_argument("--preferences", action="store_true", help="Show saved output preferences.")
    list_parser.set_defaults(func=cmd_list)

    show_parser = subparsers.add_parser("show", help="Show one person with all sources.")
    show_parser.add_argument("--person", required=True, help="Person id or name.")
    show_parser.set_defaults(func=cmd_show)

    add_person_parser = subparsers.add_parser("add-person", help="Add a tracked person.")
    add_person_parser.add_argument("--name", required=True, help="Display name.")
    add_person_parser.add_argument("--kind", default="other", help="scholar, engineer, mixed, or other.")
    add_person_parser.add_argument("--notes", help="Optional notes.")
    add_person_parser.set_defaults(func=cmd_add_person)

    remove_person_parser = subparsers.add_parser("remove-person", help="Remove a tracked person.")
    remove_person_parser.add_argument("--person", required=True, help="Person id or name.")
    remove_person_parser.set_defaults(func=cmd_remove_person)

    add_source_parser = subparsers.add_parser("add-source", help="Add a source to a person using explicit fields.")
    add_source_parser.add_argument("--person", required=True, help="Person id or name.")
    add_source_parser.add_argument("--type", required=True, help="Source type.")
    add_source_parser.add_argument("--label", required=True, help="Human-friendly source label.")
    add_source_parser.add_argument("--source-id", help="Optional stable source id.")
    add_source_parser.add_argument("--url", help="Source URL when applicable.")
    add_source_parser.add_argument("--param", action="append", help="Source parameter in key=value form.")
    add_source_parser.set_defaults(func=cmd_add_source)

    link_source_parser = subparsers.add_parser("link-source", help="Link a person to a source using person + source/url.")
    link_source_parser.add_argument("--person", required=True, help="Person id or name.")
    link_source_parser.add_argument("--source", help="Source hint like arxiv, youtube, x, github, blog.")
    link_source_parser.add_argument("--url", help="Optional URL to canonicalize and store.")
    link_source_parser.add_argument("--label", help="Optional custom label.")
    link_source_parser.add_argument("--source-id", help="Optional stable source id.")
    link_source_parser.set_defaults(func=cmd_link_source)

    remove_source_parser = subparsers.add_parser("remove-source", help="Remove one source.")
    remove_source_parser.add_argument("--person", required=True, help="Person id or name.")
    remove_source_parser.add_argument("--source", required=True, help="Source id, label, or shorthand like arxiv.")
    remove_source_parser.set_defaults(func=cmd_remove_source)

    unlink_source_parser = subparsers.add_parser("unlink-source", help="Alias for remove-source.")
    unlink_source_parser.add_argument("--person", required=True, help="Person id or name.")
    unlink_source_parser.add_argument("--source", required=True, help="Source id, label, or shorthand like arxiv.")
    unlink_source_parser.set_defaults(func=cmd_remove_source)

    enable_source_parser = subparsers.add_parser("enable-source", help="Enable one source.")
    enable_source_parser.add_argument("--person", required=True, help="Person id or name.")
    enable_source_parser.add_argument("--source", required=True, help="Source id, label, or shorthand.")
    enable_source_parser.set_defaults(func=lambda args: cmd_toggle_source(args, True))

    disable_source_parser = subparsers.add_parser("disable-source", help="Disable one source.")
    disable_source_parser.add_argument("--person", required=True, help="Person id or name.")
    disable_source_parser.add_argument("--source", required=True, help="Source id, label, or shorthand.")
    disable_source_parser.set_defaults(func=lambda args: cmd_toggle_source(args, False))

    prefs_parser = subparsers.add_parser("set-preferences", help="Set saved output preferences.")
    prefs_parser.add_argument("--theme", action="append", help="Preferred theme, e.g. ai-infra or applications.")
    prefs_parser.add_argument("--keyword-boost", action="append", help="Boost items containing this keyword.")
    prefs_parser.add_argument("--keyword-penalty", action="append", help="Demote items containing this keyword.")
    prefs_parser.add_argument("--clear-themes", action="store_true", help="Clear saved themes.")
    prefs_parser.add_argument("--clear-keyword-boosts", action="store_true", help="Clear saved keyword boosts.")
    prefs_parser.add_argument("--clear-keyword-penalties", action="store_true", help="Clear saved keyword penalties.")
    prefs_parser.set_defaults(func=cmd_set_preferences)

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        return args.func(args)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
