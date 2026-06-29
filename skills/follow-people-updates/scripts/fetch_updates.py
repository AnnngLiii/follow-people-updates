#!/usr/bin/env python3

import argparse
import json
import multiprocessing as mp
import os
import re
import socket
import time
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from pathlib import Path

from bs4 import BeautifulSoup


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
DEFAULT_REGISTRY = SKILL_DIR / "assets" / "tracking-registry.json"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/136.0.0.0 Safari/537.36 "
    "follow-people-updates/1.0"
)
GITHUB_API_TOKEN = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
REQUEST_TIMEOUT = float(os.environ.get("FOLLOW_PEOPLE_UPDATES_REQUEST_TIMEOUT", "15"))
REQUEST_RETRIES = max(1, int(os.environ.get("FOLLOW_PEOPLE_UPDATES_REQUEST_RETRIES", "3")))
SOURCE_TIMEOUT = float(os.environ.get("FOLLOW_PEOPLE_UPDATES_SOURCE_TIMEOUT", "20"))
ENABLE_HEAVY_ENRICHMENT = os.environ.get("FOLLOW_PEOPLE_UPDATES_HEAVY_ENRICHMENT", "").strip().lower() in {"1", "true", "yes", "on"}
HOST_MIN_INTERVAL_SECONDS = {
    "scholar.google.com": 3.0,
    "api.crossref.org": 1.0,
    "api.github.com": 0.5,
}
LAST_REQUEST_AT = {}
THEME_KEYWORDS = {
    "ai-infra": [
        "inference", "serving", "compiler", "systems", "runtime", "throughput",
        "latency", "memory", "distributed", "training", "pre-training", "pretraining",
        "benchmark", "evaluation", "rl", "agentic rl", "scaling", "optimization",
        "gpu", "cluster", "data pipeline", "retrieval",
    ],
    "applications": [
        "application", "product", "workflow", "assistant", "customer", "enterprise",
        "deployment", "robot", "healthcare", "education", "finance", "clinical",
        "navigation", "manipulation", "painting", "drug", "document-grounded",
    ],
    "agents": [
        "agent", "agentic", "tool use", "web navigation", "coding agent",
        "planning", "reasoning", "multi-turn", "execution feedback",
    ],
    "robotics": [
        "robot", "robotic", "manipulation", "control", "trajectory", "sim2real",
        "embodied", "dynamics", "policy",
    ],
    "research": [
        "research", "paper", "benchmark", "dataset", "preprint", "arxiv",
        "evaluation", "study", "experiment", "method", "algorithm",
    ],
    "ethics": [
        "safety", "alignment", "ethics", "policy", "governance", "privacy",
        "bias", "harm", "risk", "responsible ai", "regulation",
    ],
}
YOUTUBE_AI_KEYWORDS = [
    "ai", "artificial intelligence", "genai", "gpt", "chatgpt", "llm", "llms",
    "language model", "language models", "foundation model", "foundation models",
    "agent", "agents", "agentic", "rag", "retrieval", "embedding", "inference",
    "fine-tuning", "fine tuning", "pretraining", "pre-training", "benchmark",
    "evaluation", "reasoning", "multimodal", "vision-language", "vla", "vlm",
    "machine learning", "deep learning", "neural network", "robot", "robotics",
    "openai", "anthropic", "claude", "gemini", "llama", "mistral", "cursor",
    "copilot", "diffusion", "reinforcement learning", "rl",
]


def registry_path() -> Path:
    override = os.environ.get("FOLLOW_PEOPLE_UPDATES_REGISTRY")
    return Path(override).expanduser() if override else DEFAULT_REGISTRY


def load_registry(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        data = json.load(handle)
    data.setdefault("version", 2)
    data.setdefault("defaults", {"max_seen_ids_per_source": 100})
    data.setdefault("preferences", {"themes": [], "keyword_boosts": [], "keyword_penalties": []})
    data["preferences"].setdefault("themes", [])
    data["preferences"].setdefault("keyword_boosts", [])
    data["preferences"].setdefault("keyword_penalties", [])
    data.setdefault("people", [])
    for person in data["people"]:
        person.setdefault("identities", {})
        person.setdefault("sources", [])
        for source in person["sources"]:
            source.setdefault("resolution", {})
    return data


def save_registry(path: Path, registry) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(registry, handle, ensure_ascii=False, indent=2)
        handle.write("\n")


def open_url(url: str, accept: str, *, timeout=None, retries=None):
    host = urllib.parse.urlparse(url).netloc.lower()
    min_interval = HOST_MIN_INTERVAL_SECONDS.get(host, 0)
    if min_interval > 0:
        elapsed = time.time() - LAST_REQUEST_AT.get(host, 0)
        if elapsed < min_interval:
            time.sleep(min_interval - elapsed)

    timeout = float(timeout if timeout is not None else REQUEST_TIMEOUT)
    retries = max(1, int(retries if retries is not None else REQUEST_RETRIES))
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": accept,
    }
    if GITHUB_API_TOKEN and "api.github.com" in url:
        headers["Authorization"] = f"Bearer {GITHUB_API_TOKEN}"
    last_error = None
    for attempt in range(retries):
        req = urllib.request.Request(url, headers=headers)
        try:
            response = urllib.request.urlopen(req, timeout=timeout)
            LAST_REQUEST_AT[host] = time.time()
            return response
        except urllib.error.HTTPError as exc:
            LAST_REQUEST_AT[host] = time.time()
            if host == "scholar.google.com" and exc.code == 429:
                raise
            if exc.code in {429, 500, 502, 503, 504} and attempt + 1 < retries:
                retry_after = exc.headers.get("Retry-After") if exc.headers else None
                try:
                    delay = max(float(retry_after), 1.0) if retry_after else min(2 ** attempt, 5)
                except ValueError:
                    delay = min(2 ** attempt, 5)
                time.sleep(delay)
                continue
            raise
        except (urllib.error.URLError, socket.timeout, TimeoutError) as exc:
            LAST_REQUEST_AT[host] = time.time()
            last_error = exc
            if attempt + 1 >= retries:
                raise
            time.sleep(min(2 ** attempt, 3))
    if last_error is not None:
        raise last_error
    raise RuntimeError(f"open_url() failed unexpectedly for {url}")


def request(url: str, accept: str, *, timeout=None, retries=None):
    with open_url(url, accept, timeout=timeout, retries=retries) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def request_json(url: str, *, timeout=None, retries=None):
    return json.loads(request(url, "application/json", timeout=timeout, retries=retries))


def request_xml(url: str, *, timeout=None, retries=None):
    return ET.fromstring(
        request(
            url,
            "application/atom+xml, application/rss+xml, application/xml, text/xml",
            timeout=timeout,
            retries=retries,
        )
    )


def fast_request_timeout():
    return min(REQUEST_TIMEOUT, 6.0)


def request_fast_html(url: str):
    return request(url, "text/html,application/xhtml+xml", timeout=fast_request_timeout(), retries=1)


def request_fast_json(url: str):
    return request_json(url, timeout=fast_request_timeout(), retries=1)


def request_fast_html_excerpt(url: str, max_bytes=600000):
    with open_url(url, "text/html,application/xhtml+xml", timeout=fast_request_timeout(), retries=1) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read(max_bytes).decode(charset, errors="replace")


def local_name(tag: str) -> str:
    return tag.split("}", 1)[-1] if "}" in tag else tag


def child_text(node, *names):
    wanted = set(names)
    for child in node:
        if local_name(child.tag) in wanted and (child.text or "").strip():
            return child.text.strip()
    return None


def first_link_from_atom(entry):
    for child in entry:
        if local_name(child.tag) != "link":
            continue
        href = child.attrib.get("href")
        rel = child.attrib.get("rel", "alternate")
        if href and rel in {"alternate", ""}:
            return href
    for child in entry:
        if local_name(child.tag) == "link" and child.attrib.get("href"):
            return child.attrib["href"]
    return None


def normalize_date(value):
    if not value:
        return None
    for candidate in (value, value.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc).isoformat()
        except ValueError:
            pass
    try:
        dt = parsedate_to_datetime(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except (TypeError, ValueError, IndexError):
        return value


def parse_timestamp(value):
    if not value:
        return None
    normalized = normalize_date(value)
    if not normalized:
        return None
    try:
        return datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError:
        return None


def date_from_parts(parts):
    if not parts:
        return None
    year = parts[0] if len(parts) > 0 else 1
    month = parts[1] if len(parts) > 1 else 1
    day = parts[2] if len(parts) > 2 else 1
    return datetime(year, month, day, tzinfo=timezone.utc).isoformat()


def parse_rss_or_atom(url: str, limit: int, *, timeout=None, retries=None):
    items = []
    with open_url(
        url,
        "application/atom+xml, application/rss+xml, application/xml, text/xml",
        timeout=timeout,
        retries=retries,
    ) as response:
        context = ET.iterparse(response, events=("start", "end"))
        root_tag = None
        for event, elem in context:
            tag = local_name(elem.tag)
            if event == "start" and root_tag is None:
                root_tag = tag
                continue
            if event != "end":
                continue
            if tag == "entry":
                title = child_text(elem, "title") or "(untitled)"
                link = first_link_from_atom(elem)
                item_id = child_text(elem, "id") or link or title
                published = normalize_date(child_text(elem, "published", "updated"))
                summary = child_text(elem, "summary", "content")
                authors = [
                    child_text(child, "name") or (child.text or "").strip()
                    for child in elem
                    if local_name(child.tag) == "author"
                ]
                items.append(
                    {
                        "id": item_id,
                        "title": title,
                        "url": link,
                        "published_at": published,
                        "summary": summary,
                        "authors": [author for author in authors if author],
                    }
                )
            elif tag == "item":
                title = child_text(elem, "title") or "(untitled)"
                link = child_text(elem, "link")
                item_id = child_text(elem, "guid") or link or title
                published = normalize_date(
                    child_text(elem, "pubDate", "published", "updated", "date")
                )
                summary = child_text(elem, "description")
                items.append(
                    {
                        "id": item_id,
                        "title": title,
                        "url": link,
                        "published_at": published,
                        "summary": summary,
                    }
                )
            if tag in {"entry", "item"}:
                elem.clear()
                if len(items) >= limit:
                    break
    return items


def parse_atom_entries(root, limit: int):
    items = []
    for entry in root:
        if local_name(entry.tag) != "entry":
            continue
        title = child_text(entry, "title") or "(untitled)"
        link = first_link_from_atom(entry)
        item_id = child_text(entry, "id") or link or title
        published = normalize_date(child_text(entry, "published", "updated"))
        summary = child_text(entry, "summary", "content")
        authors = [
            child_text(child, "name") or (child.text or "").strip()
            for child in entry
            if local_name(child.tag) == "author"
        ]
        items.append(
            {
                "id": item_id,
                "title": title,
                "url": link,
                "published_at": published,
                "summary": summary,
                "authors": [author for author in authors if author],
            }
        )
        if len(items) >= limit:
            break
    return items


def re_search(pattern, text):
    import re

    match = re.search(pattern, text)
    return match.group(1) if match else None


def clean_html_text(value):
    if not value:
        return None
    import re

    value = re.sub(r"<[^>]+>", " ", value)
    value = unescape(value)
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def clean_crossref_abstract(value):
    return clean_html_text(value)


def extract_page_summary(url: str):
    if not url:
        return None
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return None
    try:
        html = request(url, "text/html,application/xhtml+xml")
    except Exception:
        return None

    soup = BeautifulSoup(html, "html.parser")
    meta_candidates = [
        soup.select_one('meta[name="description"]'),
        soup.select_one('meta[property="og:description"]'),
        soup.select_one('meta[name="twitter:description"]'),
        soup.select_one('meta[itemprop="description"]'),
    ]
    for meta in meta_candidates:
        if not meta:
            continue
        content = clean_html_text(meta.get("content"))
        if content:
            return content

    paragraphs = []
    for node in soup.select("article p, main p, p"):
        text = clean_html_text(node.get_text(" ", strip=True))
        if not text:
            continue
        if len(text) < 60:
            continue
        paragraphs.append(text)
        if len(" ".join(paragraphs)) >= 400:
            break

    summary = " ".join(paragraphs).strip()
    return summary[:500] if summary else None


def fetch_youtube_page_metadata(url: str):
    if not url:
        return {}
    try:
        html = request(url, "text/html,application/xhtml+xml")
    except Exception:
        return {}

    soup = BeautifulSoup(html, "html.parser")
    description = None
    for meta in [
        soup.select_one('meta[name="description"]'),
        soup.select_one('meta[property="og:description"]'),
        soup.select_one('meta[name="twitter:description"]'),
    ]:
        if not meta:
            continue
        description = clean_html_text(meta.get("content"))
        if description:
            break

    keywords = []
    meta_keywords = soup.select_one('meta[name="keywords"]')
    if meta_keywords and meta_keywords.get("content"):
        keywords = [
            keyword.strip() for keyword in meta_keywords.get("content", "").split(",")
            if keyword.strip()
        ]

    return {
        "description": description,
        "keywords": keywords,
    }


def fetch_youtube_transcript_text(url: str):
    script = SCRIPT_DIR / "fetch_youtube_transcript.py"
    try:
        raw = subprocess.check_output(
            ["python3", str(script), "--url", url, "--json"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=20,
        )
    except Exception:
        return None

    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None

    segments = data.get("segments") or []
    text = " ".join(
        segment.get("text", "").strip()
        for segment in segments
        if segment.get("text")
    ).strip()
    return text[:4000] if text else None


def extract_yt_initial_data(html: str):
    match = re.search(r"var ytInitialData = (\{.*?\});", html, re.S) or re.search(r"ytInitialData\s*=\s*(\{.*?\});", html, re.S)
    if not match:
        return None
    try:
        return json.loads(match.group(1))
    except json.JSONDecodeError:
        return None


def youtube_title_text(value):
    if not value:
        return None
    simple = value.get("simpleText")
    if simple:
        return simple.strip()
    runs = value.get("runs") or []
    text = "".join(run.get("text", "") for run in runs).strip()
    return text or None


def parse_relative_youtube_time(value: str):
    if not value:
        return None
    text = value.strip().lower()
    now = datetime.now(timezone.utc)
    patterns = [
        (r"(\d+)\s*hour[s]?\s*ago", 3600),
        (r"(\d+)\s*day[s]?\s*ago", 86400),
        (r"(\d+)\s*week[s]?\s*ago", 7 * 86400),
        (r"(\d+)\s*month[s]?\s*ago", 30 * 86400),
        (r"(\d+)\s*year[s]?\s*ago", 365 * 86400),
        (r"(\d+)\s*小时前", 3600),
        (r"(\d+)\s*天前", 86400),
        (r"(\d+)\s*周前", 7 * 86400),
        (r"(\d+)\s*个月前", 30 * 86400),
        (r"(\d+)\s*年前", 365 * 86400),
        (r"(\d+)\s*時間前", 3600),
        (r"(\d+)\s*日前", 86400),
        (r"(\d+)\s*週間前", 7 * 86400),
        (r"(\d+)\s*か月前", 30 * 86400),
        (r"(\d+)\s*ヶ月前", 30 * 86400),
        (r"(\d+)\s*年前", 365 * 86400),
    ]
    for pattern, scale in patterns:
        match = re.search(pattern, text)
        if match:
            seconds = int(match.group(1)) * scale
            return (now.timestamp() - seconds)
    if text in {"yesterday", "昨天", "昨日"}:
        return now.timestamp() - 86400
    return None


def youtube_relative_time_to_iso(value: str):
    timestamp = parse_relative_youtube_time(value)
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def iter_youtube_video_renderers(node):
    if isinstance(node, dict):
        for key in ("videoRenderer", "gridVideoRenderer"):
            renderer = node.get(key)
            if renderer:
                yield renderer
        for value in node.values():
            yield from iter_youtube_video_renderers(value)
    elif isinstance(node, list):
        for value in node:
            yield from iter_youtube_video_renderers(value)


def youtube_channel_videos_url(source):
    url = (source.get("url") or "").rstrip("/")
    if not url:
        return url
    if url.endswith("/videos"):
        return url
    return url + "/videos"


def fetch_youtube_channel_from_page(source, limit: int):
    url = youtube_channel_videos_url(source)
    html = request_fast_html(url)
    data = extract_yt_initial_data(html)
    if not data:
        raise ValueError(f"Could not extract ytInitialData from {url}")

    items = []
    seen = set()
    for renderer in iter_youtube_video_renderers(data):
        video_id = renderer.get("videoId")
        if not video_id or video_id in seen:
            continue
        seen.add(video_id)
        title = youtube_title_text(renderer.get("title")) or f"YouTube video {video_id}"
        published_text = youtube_title_text(renderer.get("publishedTimeText"))
        summary = None
        description_snippet = renderer.get("descriptionSnippet") or renderer.get("descriptionText")
        if description_snippet:
            summary = youtube_title_text(description_snippet)
        items.append(
            {
                "id": f"yt:video:{video_id}",
                "title": title,
                "url": f"https://www.youtube.com/watch?v={video_id}",
                "published_at": youtube_relative_time_to_iso(published_text) or normalize_date(published_text),
                "summary": summary,
            }
        )
        if len(items) >= limit:
            break
    return items


def extract_google_scholar_user(url):
    return re_search(r"[?&]user=([^&#]+)", url or "")


def scholar_detail_url(profile_user, citation_for_view):
    return (
        "https://scholar.google.com/citations"
        f"?view_op=view_citation&hl=en&user={urllib.parse.quote(profile_user)}"
        f"&citation_for_view={urllib.parse.quote(citation_for_view, safe=':')}"
    )


def scholar_profile_url(profile_user):
    return f"https://scholar.google.com/citations?user={urllib.parse.quote(profile_user)}&hl=en&sortby=pubdate"


def absolute_url(base: str, href: str):
    return urllib.parse.urljoin(base, href)


def normalize_person_name(value):
    if not value:
        return None
    import re

    parts = re.findall(r"[A-Za-z0-9]+", value.lower())
    if parts:
        return " ".join(parts)
    compact = re.sub(r"\s+", "", value).lower()
    return compact or None


def person_name_variants(value):
    normalized = normalize_person_name(value)
    if not normalized:
        return set()
    variants = {normalized}
    parts = normalized.split()
    if len(parts) >= 2:
        variants.add(" ".join(reversed(parts)))
    return variants


def item_mentions_author(author_name, candidate_names):
    expected = person_name_variants(author_name)
    if not expected:
        return False
    for candidate in candidate_names or []:
        normalized = normalize_person_name(candidate)
        if normalized and normalized in expected:
            return True
    return False


def resolve_youtube_channel_feed(source):
    url = source.get("url") or ""
    params = source.get("params") or {}
    channel_id = params.get("channel_id")
    if not channel_id and "/channel/" in url:
        channel_id = url.rstrip("/").split("/channel/")[-1].split("/", 1)[0]
    if not channel_id:
        html = request_fast_html(url)
        channel_id = re_search(r'"channelId":"(UC[^"]+)"', html) or re_search(r'"externalId":"(UC[^"]+)"', html)
    if not channel_id:
        raise ValueError(f"Could not resolve YouTube channel ID from {url}")
    return f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def fetch_github_user_events(source, limit: int):
    username = source["params"]["username"]
    url = f"https://api.github.com/users/{urllib.parse.quote(username)}/events/public?per_page={limit}"
    try:
        events = request_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code != 403:
            raise
        return fetch_github_user_events_atom(username, limit)
    items = []
    for event in events[:limit]:
        repo = (event.get("repo") or {}).get("name")
        event_type = event.get("type", "Event")
        payload = event.get("payload") or {}
        title = f"{event_type} on {repo}" if repo else event_type
        link = f"https://github.com/{repo}" if repo else None
        if event_type == "PushEvent" and repo:
            ref_name = (payload.get("ref") or "").split("/")[-1]
            title = f"Pushed to {repo}:{ref_name}" if ref_name else f"Pushed to {repo}"
            link = f"https://github.com/{repo}/commits/{ref_name}" if ref_name else link
        elif event_type == "ReleaseEvent" and repo:
            release = payload.get("release") or {}
            tag_name = release.get("tag_name")
            if tag_name:
                title = f"Released {repo} {tag_name}"
            link = release.get("html_url") or link
        elif event_type == "PullRequestEvent" and repo:
            pr = payload.get("pull_request") or {}
            action = payload.get("action") or "updated"
            pr_title = pr.get("title")
            title = f"{action.capitalize()} PR in {repo}"
            if pr_title:
                title = f"{title}: {pr_title}"
            link = pr.get("html_url") or link
        items.append(
            {
                "id": event.get("id") or title,
                "title": title,
                "url": link,
                "published_at": normalize_date(event.get("created_at")),
                "summary": None,
            }
        )
    return items


def fetch_github_user_events_atom(username: str, limit: int):
    feed_url = f"https://github.com/{urllib.parse.quote(username)}.atom"
    items = parse_rss_or_atom(feed_url, limit)
    for item in items:
        item["summary"] = clean_html_text(item.get("summary"))
    return items


def extract_repo_readme_summary(repo_full_name: str):
    repo_url = f"https://github.com/{repo_full_name}"
    try:
        html = request_fast_html(repo_url)
    except Exception:
        return None
    soup = BeautifulSoup(html, "html.parser")
    article = soup.select_one("article.markdown-body")
    meta = soup.select_one('meta[name="description"]') or soup.select_one('meta[property="og:description"]')
    text = " ".join(article.stripped_strings) if article else ""
    if not text and meta:
        text = (meta.get("content") or "").strip()
    text = re.sub(r"<!--.*?-->", " ", text or "", flags=re.S)
    lines = [line.strip() for line in text.splitlines()]
    chunks = []
    for line in lines:
        if (
            not line
            or line.startswith("#")
            or line.startswith("![")
            or line.startswith("[![")
            or line.startswith("<")
            or line.startswith("|")
            or "src=" in line
            or "img.shields.io" in line
        ):
            continue
        if line.startswith("```"):
            continue
        cleaned = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line)
        cleaned = re.sub(r"`([^`]+)`", r"\1", cleaned).strip()
        cleaned = re.sub(r"<[^>]+>", " ", cleaned)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned:
            chunks.append(cleaned)
        if len(" ".join(chunks)) >= 280:
            break
    summary = " ".join(chunks).strip()
    return summary[:400] if summary else None


def fetch_github_org_repos(source, limit: int):
    org = source["params"]["org"]
    url = f"https://api.github.com/orgs/{urllib.parse.quote(org)}/repos?sort=updated&direction=desc&per_page={limit}"
    try:
        repos = request_json(url)
    except urllib.error.HTTPError as exc:
        if exc.code != 403:
            raise
        return fetch_github_org_repos_html(org, limit)
    items = []
    for repo in repos[:limit]:
        repo_name = repo.get("full_name") or repo.get("name")
        summary_parts = []
        if repo.get("description"):
            summary_parts.append(repo["description"].strip())
        topics = repo.get("topics") or []
        if topics:
            summary_parts.append("Topics: " + ", ".join(topics[:6]))
        readme_summary = extract_repo_readme_summary(repo_name) if ENABLE_HEAVY_ENRICHMENT and repo_name else None
        if readme_summary:
            summary_parts.append(readme_summary)
        items.append(
            {
                "id": str(repo.get("id") or repo_name),
                "title": repo_name or "(untitled repo)",
                "url": repo.get("html_url"),
                "published_at": normalize_date(repo.get("updated_at") or repo.get("pushed_at") or repo.get("created_at")),
                "summary": " ".join(part for part in summary_parts if part),
            }
        )
    return items


def fetch_github_org_repos_html(org: str, limit: int):
    html = request(f"https://github.com/{urllib.parse.quote(org)}?tab=repositories", "text/html,application/xhtml+xml")
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen = set()
    for anchor in soup.select(f'a[itemprop=\"name codeRepository\"], a[href^=\"/{org}/\"]'):
        href = anchor.get("href") or ""
        if not href.startswith(f"/{org}/"):
            continue
        repo_name = href.strip("/")
        if "/" not in repo_name or repo_name in seen:
            continue
        seen.add(repo_name)
        container = (
            anchor.find_parent("li", class_=re.compile(r"pinned-item-list-item"))
            or anchor.find_parent("li", class_=re.compile(r"source"))
            or anchor.find_parent(["li", "article", "div"])
        )
        summary = None
        published_at = None
        if container:
            desc = container.select_one("p")
            if desc:
                summary = clean_html_text(desc.get_text(" ", strip=True))
            reltime = container.select_one("relative-time")
            if reltime:
                published_at = normalize_date(reltime.get("datetime") or reltime.get_text(" ", strip=True))
        items.append(
            {
                "id": absolute_url("https://github.com", href),
                "title": repo_name,
                "url": absolute_url("https://github.com", href),
                "published_at": published_at,
                "summary": summary,
            }
        )
        if len(items) >= limit:
            break
    return items


def fetch_github_repo_releases(source, limit: int):
    repo = source["params"]["repo"]
    url = f"https://api.github.com/repos/{repo}/releases?per_page={limit}"
    releases = request_json(url)
    items = []
    for release in releases[:limit]:
        name = release.get("name") or release.get("tag_name") or "(untitled release)"
        items.append(
            {
                "id": str(release.get("id") or name),
                "title": name,
                "url": release.get("html_url"),
                "published_at": normalize_date(release.get("published_at") or release.get("created_at")),
                "summary": release.get("body"),
            }
        )
    return items


def fetch_github_trending(source, limit: int):
    since = (datetime.now(timezone.utc) - timedelta(days=7)).date().isoformat()
    url = (
        "https://api.github.com/search/repositories"
        f"?q=pushed:%3E%3D{since}&sort=stars&order=desc&per_page={limit}"
    )
    try:
        data = request_fast_json(url)
        repos = (data or {}).get("items") or []
        items = []
        for repo in repos[:limit]:
            repo_name = repo.get("full_name") or repo.get("name")
            topics = repo.get("topics") or []
            summary_parts = []
            if repo.get("description"):
                summary_parts.append(repo["description"].strip())
            if topics:
                summary_parts.append("Topics: " + ", ".join(topics[:6]))
            items.append(
                {
                    "id": str(repo.get("id") or repo_name),
                    "title": repo_name or "(untitled repo)",
                    "url": repo.get("html_url"),
                    "published_at": normalize_date(repo.get("pushed_at") or repo.get("updated_at") or repo.get("created_at")),
                    "summary": " ".join(part for part in summary_parts if part),
                }
            )
        if items:
            return items
    except urllib.error.HTTPError as exc:
        if exc.code != 403:
            raise

    html = request_fast_html_excerpt(source["url"])
    soup = BeautifulSoup(html, "html.parser")
    items = []
    scanned_at = datetime.now(timezone.utc).isoformat()
    for article in soup.select("article.Box-row"):
        link = article.select_one("h2 a")
        if not link:
            continue
        href = absolute_url("https://github.com", link.get("href"))
        repo_name = "/".join(part.strip() for part in link.get_text(" ", strip=True).split("/"))
        summary = None
        desc = article.select_one("p")
        if desc:
            summary = desc.get_text(" ", strip=True)
        items.append(
            {
                "id": href,
                "title": repo_name,
                "url": href,
                "published_at": scanned_at,
                "summary": summary,
            }
        )
        if len(items) >= limit:
            break
    return items


def fetch_github_topic(source, limit: int):
    topic = source["params"]["topic"]
    url = (
        "https://api.github.com/search/repositories"
        f"?q=topic:{urllib.parse.quote(topic)}&sort=updated&order=desc&per_page={limit}"
    )
    try:
        data = request_fast_json(url)
        repos = (data or {}).get("items") or []
        items = []
        for repo in repos[:limit]:
            repo_name = repo.get("full_name") or repo.get("name")
            topics = repo.get("topics") or []
            summary_parts = []
            if repo.get("description"):
                summary_parts.append(repo["description"].strip())
            if topics:
                summary_parts.append("Topics: " + ", ".join(topics[:6]))
            items.append(
                {
                    "id": str(repo.get("id") or repo_name),
                    "title": repo_name or "(untitled repo)",
                    "url": repo.get("html_url"),
                    "published_at": normalize_date(repo.get("updated_at") or repo.get("pushed_at") or repo.get("created_at")),
                    "summary": " ".join(part for part in summary_parts if part) or f"GitHub topic repository for {topic}",
                }
            )
        if items:
            return items
    except urllib.error.HTTPError as exc:
        if exc.code != 403:
            raise

    html = request_fast_html(source["url"])
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen = set()
    blocked_owners = {"sponsors", "topics", "collections", "events", "features", "orgs", "marketplace", "search", "login", "signup"}
    scanned_at = datetime.now(timezone.utc).isoformat()
    for link in soup.select('a[href^="/"][href*="/"]'):
        href = link.get("href") or ""
        if href.count("/") != 2:
            continue
        repo_name = href.strip("/")
        owner = repo_name.split("/", 1)[0].lower()
        if owner in blocked_owners:
            continue
        if repo_name in seen or repo_name.startswith("topics/"):
            continue
        seen.add(repo_name)
        full_url = absolute_url("https://github.com", href)
        card = link.find_parent(["article", "div"])
        text = card.get_text(" ", strip=True) if card else link.get_text(" ", strip=True)
        items.append(
            {
                "id": full_url,
                "title": repo_name,
                "url": full_url,
                "published_at": scanned_at,
                "summary": text[:400] if text else f"GitHub topic repository for {topic}",
            }
        )
        if len(items) >= limit:
            break
    return items


def fetch_github_repo_commits(source, limit: int):
    repo = source["params"]["repo"]
    url = f"https://api.github.com/repos/{repo}/commits?per_page={limit}"
    commits = request_json(url)
    items = []
    for commit in commits[:limit]:
        commit_info = commit.get("commit") or {}
        message = (commit_info.get("message") or "").splitlines()[0] or "(empty commit message)"
        items.append(
            {
                "id": commit.get("sha") or message,
                "title": message,
                "url": commit.get("html_url"),
                "published_at": normalize_date(((commit_info.get("author") or {}).get("date"))),
                "summary": None,
            }
        )
    return items


def fetch_arxiv_author(source, limit: int):
    author = source["params"]["author"]
    query = urllib.parse.quote(f'au:"{author}"')
    fetch_limit = min(max(limit * 2, 10), 20)
    url = (
        "https://export.arxiv.org/api/query"
        f"?search_query={query}&start=0&max_results={fetch_limit}"
        "&sortBy=submittedDate&sortOrder=descending"
    )
    try:
        root = request_xml(url, timeout=fast_request_timeout(), retries=1)
        items = parse_atom_entries(root, fetch_limit)
        filtered_items = [
            item for item in items
            if item_mentions_author(author, item.get("authors"))
        ]
        if filtered_items:
            return filtered_items[:limit]
    except (urllib.error.HTTPError, urllib.error.URLError, socket.timeout, TimeoutError):
        pass

    return fetch_arxiv_author_search(author, limit)


def fetch_arxiv_author_search(author: str, limit: int):
    query = urllib.parse.quote(author)
    search_limit = min(max(limit * 10, 30), 50)
    url = (
        "https://search.arxiv.org/"
        f"?query={query}&searchtype=author&abstracts=hide&order=-announced_date_first&size={search_limit}"
    )
    html = request_fast_html(url)
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for cell in soup.select("td.snipp"):
        author_text = " ".join(
            clean_html_text(node.get_text(" ", strip=True)) or ""
            for node in cell.select("span.author")
        ).strip()
        if not item_mentions_author(author, [author_text]):
            continue
        title_node = cell.select_one("span.title")
        link_node = cell.select_one("a.url")
        snippet_node = cell.select_one("span.snippet")
        title = clean_html_text(title_node.get_text(" ", strip=True)) if title_node else None
        abs_url = clean_html_text(link_node.get_text(" ", strip=True)) if link_node else None
        if not title or not abs_url:
            continue
        items.append(
            {
                "id": abs_url.rsplit("/", 1)[-1],
                "title": title,
                "url": abs_url,
                "published_at": fetch_arxiv_abs_date(abs_url),
                "summary": clean_html_text(snippet_node.get_text(" ", strip=True)) if snippet_node else None,
                "authors": [author_text] if author_text else [],
            }
        )
        if len(items) >= limit:
            break
    return items


def fetch_arxiv_abs_date(url: str):
    try:
        html = request(url, "text/html,application/xhtml+xml", timeout=min(REQUEST_TIMEOUT, 4.0), retries=1)
    except Exception:
        return None

    date_match = re.search(r'<meta name="citation_date" content="([^"]+)"', html)
    if date_match:
        return parse_date_to_iso(date_match.group(1))

    submitted_match = re.search(r"Submitted on (\d+ \w+ \d+)", html)
    if submitted_match:
        return parse_date_to_iso(submitted_match.group(1))
    return None


def fetch_crossref_author(source, limit: int):
    author = source["params"]["author"]
    encoded_author = urllib.parse.quote(author)
    # Crossref author queries can return many loosely matched works; cap the
    # candidate set so broad names do not consume the whole source timeout.
    fetch_limit = min(max(limit * 3, 30), 100)
    url = (
        "https://api.crossref.org/works"
        f"?query.author={encoded_author}&sort=published&order=desc&rows={fetch_limit}"
    )
    data = request_json(url)
    works = ((data or {}).get("message") or {}).get("items") or []
    items = []
    for work in works:
        title_list = work.get("title") or ["(untitled)"]
        authors = [
            " ".join(part for part in [author_info.get("given"), author_info.get("family")] if part).strip()
            for author_info in (work.get("author") or [])
        ]
        if not item_mentions_author(author, authors):
            continue
        published = (
            date_from_parts(((work.get("published-print") or {}).get("date-parts") or [[None]])[0])
            or date_from_parts(((work.get("published-online") or {}).get("date-parts") or [[None]])[0])
            or date_from_parts(((work.get("created") or {}).get("date-parts") or [[None]])[0])
        )
        doi = work.get("DOI")
        url = work.get("URL") or (f"https://doi.org/{doi}" if doi else None)
        summary = clean_crossref_abstract(work.get("abstract"))
        if not summary and url and ENABLE_HEAVY_ENRICHMENT:
            summary = extract_page_summary(url)
        items.append(
            {
                "id": doi or url or title_list[0],
                "title": title_list[0],
                "url": url,
                "published_at": published,
                "summary": summary,
                "authors": authors,
            }
        )
        if len(items) >= limit:
            break
    return items


def fetch_youtube_channel(source, limit: int):
    feed_url = resolve_youtube_channel_feed(source)
    try:
        items = parse_rss_or_atom(feed_url, limit)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            raise
        items = fetch_youtube_channel_from_page(source, limit)
    for item in items:
        item["summary"] = clean_html_text(item.get("summary"))
        if ENABLE_HEAVY_ENRICHMENT:
            metadata = fetch_youtube_page_metadata(item.get("url"))
            if metadata.get("description"):
                item["summary"] = metadata["description"]
            if metadata.get("keywords"):
                item["tags"] = metadata["keywords"]
            transcript = fetch_youtube_transcript_text(item.get("url"))
            if transcript:
                item["transcript"] = transcript
    return items


def fetch_google_scholar(source, limit: int):
    profile_user = (source.get("params") or {}).get("user") or extract_google_scholar_user(source.get("url"))
    if not profile_user:
        raise ValueError("google-scholar source requires params.user or a profile URL with ?user=<id>.")

    html = request(scholar_profile_url(profile_user), "text/html")
    soup = BeautifulSoup(html, "html.parser")
    current_year = str(datetime.now(timezone.utc).year)
    items = []
    for row in soup.select("tr.gsc_a_tr"):
        title_el = row.select_one("a.gsc_a_at")
        if not title_el:
            continue
        year_el = row.select_one(".gsc_a_y span") or row.select_one(".gsc_a_y")
        year_text = year_el.get_text(" ", strip=True) if year_el else ""
        if year_text != current_year:
            continue

        citation_href = title_el.get("href") or ""
        citation_for_view = re_search(r"citation_for_view=([^&]+)", citation_href)
        title = title_el.get_text(" ", strip=True)
        authors = [el.get_text(" ", strip=True) for el in row.select(".gs_gray")[:1]]
        venue = row.select(".gs_gray")
        venue_text = venue[1].get_text(" ", strip=True) if len(venue) > 1 else None
        published_at = f"{current_year}-01-01T00:00:00+00:00"
        summary = venue_text
        url = scholar_profile_url(profile_user)
        item_id = citation_for_view or title

        if citation_for_view and ENABLE_HEAVY_ENRICHMENT:
            detail_html = request(scholar_detail_url(profile_user, citation_for_view), "text/html")
            detail_soup = BeautifulSoup(detail_html, "html.parser")
            fields = {}
            for block in detail_soup.select("#gsc_oci_table .gs_scl"):
                label = block.select_one(".gsc_oci_field")
                value = block.select_one(".gsc_oci_value")
                if not label or not value:
                    continue
                fields[label.get_text(" ", strip=True)] = value.get_text(" ", strip=True)
            publication_date = fields.get("Publication date")
            if publication_date:
                parts = publication_date.split("/")
                if len(parts) >= 3:
                    published_at = f"{parts[0]}-{parts[1].zfill(2)}-{parts[2].zfill(2)}T00:00:00+00:00"
                elif len(parts) >= 1 and parts[0]:
                    published_at = f"{parts[0]}-01-01T00:00:00+00:00"
            summary = fields.get("Description") or summary
            url = scholar_detail_url(profile_user, citation_for_view)
            item_id = citation_for_view
            if fields.get("Authors"):
                authors = [fields.get("Authors")]

        items.append(
            {
                "id": item_id,
                "title": title,
                "url": url,
                "published_at": published_at,
                "summary": summary,
                "authors": authors,
            }
        )
        if len(items) >= limit:
            break
    return items


def parse_date_to_iso(value: str):
    if not value:
        return None
    value = value.strip()
    for fmt in ("%b %d, %Y", "%B %d, %Y", "%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc).isoformat()
        except ValueError:
            continue
    return normalize_date(value)


def title_from_slug(url: str):
    slug = urllib.parse.urlparse(url).path.rstrip("/").split("/")[-1]
    if not slug:
        return url
    return slug.replace("-", " ").replace("_", " ").strip().title()


def fetch_news_index(source, limit: int):
    site = (source.get("params") or {}).get("site")
    url = source["url"]
    items = []

    if site == "openai":
        items = parse_rss_or_atom("https://openai.com/news/rss.xml", limit, timeout=fast_request_timeout(), retries=1)
        for item in items:
            item["summary"] = clean_html_text(item.get("summary"))
    elif site == "google-ai":
        feed_url = url.rstrip("/") + "/rss/"
        items = parse_rss_or_atom(feed_url, limit, timeout=fast_request_timeout(), retries=1)
        for item in items:
            item["summary"] = clean_html_text(item.get("summary"))
    elif site == "claude-blog":
        html = request_fast_html_excerpt(url)
        soup = BeautifulSoup(html, "html.parser")
        seen = set()
        for article in soup.select("article"):
            link = None
            for candidate in article.select('a[href*="/blog/"]'):
                href = candidate.get("href") or ""
                if "/blog/category/" in href:
                    continue
                link = candidate
                break
            if not link:
                continue
            href = link.get("href") or ""
            full_url = absolute_url(url, href)
            if full_url in seen:
                continue
            seen.add(full_url)
            title = clean_html_text(link.get_text(" ", strip=True))
            if not title:
                continue
            article_text = article.get_text("\n", strip=True)
            date_match = re.search(
                r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]* \d{1,2}, \d{4}",
                article_text,
            )
            summary = None
            for paragraph in article.select("p"):
                text = clean_html_text(paragraph.get_text(" ", strip=True))
                if text and text != title:
                    summary = text
                    break
            items.append(
                {
                    "id": full_url,
                    "title": title,
                    "url": full_url,
                    "published_at": parse_date_to_iso(date_match.group(0)) if date_match else None,
                    "summary": summary,
                }
            )
            if len(items) >= limit:
                break
    else:
        raise ValueError(f"Unsupported news index site: {site}")

    return items


FETCHERS = {
    "rss": lambda source, limit: parse_rss_or_atom(source["url"], limit),
    "atom": lambda source, limit: parse_rss_or_atom(source["url"], limit),
    "google-scholar": fetch_google_scholar,
    "news-index": fetch_news_index,
    "youtube-channel": fetch_youtube_channel,
    "github-user-events": fetch_github_user_events,
    "github-org-repos": fetch_github_org_repos,
    "github-repo-releases": fetch_github_repo_releases,
    "github-repo-commits": fetch_github_repo_commits,
    "github-trending": fetch_github_trending,
    "github-topic": fetch_github_topic,
    "arxiv-author": fetch_arxiv_author,
    "crossref-author": fetch_crossref_author,
}


def _source_worker(source, limit: int, queue):
    try:
        source_type = source.get("type")
        fetcher = FETCHERS.get(source_type)
        if not fetcher:
            queue.put({"ok": False, "kind": "unsupported", "error": f"Unsupported source type: {source_type}"})
            return
        items = fetcher(source, limit)
        queue.put({"ok": True, "items": items})
    except urllib.error.HTTPError as exc:
        queue.put(
            {
                "ok": False,
                "kind": "http",
                "code": exc.code,
                "reason": exc.reason,
                "error": f"HTTP {exc.code}: {exc.reason}",
            }
        )
    except urllib.error.URLError as exc:
        queue.put({"ok": False, "kind": "url", "error": str(exc.reason)})
    except (socket.timeout, TimeoutError) as exc:
        queue.put({"ok": False, "kind": "timeout", "error": str(exc) or "request timed out"})
    except Exception as exc:  # pragma: no cover - defensive error path
        queue.put({"ok": False, "kind": "error", "error": str(exc)})


def _source_timeout_process(source, limit: int):
    ctx = mp.get_context("fork") if "fork" in mp.get_all_start_methods() else mp.get_context("spawn")
    queue = ctx.Queue()
    process = ctx.Process(target=_source_worker, args=(source, limit, queue))
    process.start()
    process.join(SOURCE_TIMEOUT)
    if process.is_alive():
        process.terminate()
        process.join(1)
        if process.is_alive():
            process.kill()
            process.join(1)
        return {"ok": False, "kind": "timeout", "error": f"source exceeded {SOURCE_TIMEOUT:.0f}s timeout"}
    if queue.empty():
        return {"ok": False, "kind": "error", "error": "source exited without returning a result"}
    return queue.get()


def unique_preserving_order(values):
    seen = set()
    result = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def normalize_keywords(values):
    return [value.strip().lower() for value in values or [] if value and value.strip()]


def effective_preferences(registry, args):
    saved = registry.get("preferences") or {}
    themes = args.theme if args.theme is not None else saved.get("themes", [])
    keyword_boosts = args.keyword_boost if args.keyword_boost is not None else saved.get("keyword_boosts", [])
    keyword_penalties = args.keyword_penalty if args.keyword_penalty is not None else saved.get("keyword_penalties", [])
    return {
        "themes": unique_preserving_order(normalize_keywords(themes)),
        "keyword_boosts": unique_preserving_order(normalize_keywords(keyword_boosts)),
        "keyword_penalties": unique_preserving_order(normalize_keywords(keyword_penalties)),
    }


def score_item(item, preferences):
    text = " ".join(
        part for part in [
            item.get("title") or "",
            item.get("summary") or "",
            " ".join(item.get("tags") or []),
            item.get("transcript") or "",
        ] if part
    ).lower()
    score = 0
    matches = []

    for theme in preferences["themes"]:
        keywords = THEME_KEYWORDS.get(theme, [])
        theme_hits = [keyword for keyword in keywords if keyword in text]
        if theme_hits:
            score += 3 + min(len(theme_hits) - 1, 2)
            matches.append(f"theme:{theme}")

    for keyword in preferences["keyword_boosts"]:
        if keyword in text:
            score += 2
            matches.append(f"boost:{keyword}")

    for keyword in preferences["keyword_penalties"]:
        if keyword in text:
            score -= 2
            matches.append(f"penalty:{keyword}")

    return score, unique_preserving_order(matches)


def filter_youtube_items_by_relevance(items, preferences):
    if not items:
        return []
    filtered = []
    for item in items:
        text = " ".join(
            part for part in [
                item.get("title") or "",
                item.get("summary") or "",
                " ".join(item.get("tags") or []),
                item.get("transcript") or "",
            ] if part
        ).lower()
        if any(keyword in text for keyword in YOUTUBE_AI_KEYWORDS):
            filtered.append(item)
    return filtered


def sort_items_by_preference(items, preferences):
    if not items:
        return []

    enriched = []
    for item in items:
        copied = dict(item)
        score, matches = score_item(copied, preferences)
        copied["preference_score"] = score
        copied["preference_matches"] = matches
        enriched.append(copied)

    if preferences["themes"] or preferences["keyword_boosts"] or preferences["keyword_penalties"]:
        enriched.sort(
            key=lambda item: (
                item.get("preference_score", 0),
                item.get("published_at") or "",
                item.get("title") or "",
            ),
            reverse=True,
        )
    else:
        enriched.sort(key=lambda item: (item.get("published_at") or "", item.get("title") or ""), reverse=True)
    return enriched


def filter_people(registry, person_query=None):
    people = registry.get("people", [])
    if not person_query:
        return people
    lowered = person_query.strip().lower()
    matches = [
        person for person in people
        if person.get("id", "").lower() == lowered or person.get("name", "").lower() == lowered
    ]
    if matches:
        return matches
    matches = [
        person for person in people
        if lowered in [alias.lower() for alias in person.get("aliases", [])]
    ]
    if matches:
        return matches
    matches = [
        person for person in people
        if lowered in person.get("id", "").lower()
        or lowered in person.get("name", "").lower()
        or any(lowered in alias.lower() for alias in person.get("aliases", []))
    ]
    if len(matches) == 1:
        return matches
    if not matches:
        raise ValueError(f"No person matched '{person_query}'.")
    matched = ", ".join(person["name"] for person in matches)
    raise ValueError(f"Ambiguous person '{person_query}'. Matches: {matched}")


def filter_sources(person, source_query=None):
    sources = [source for source in person.get("sources", []) if source.get("enabled", True)]
    if not source_query:
        return sources
    lowered = source_query.strip().lower()
    matches = [
        source for source in sources
        if source.get("id", "").lower() == lowered or source.get("label", "").lower() == lowered
    ]
    if matches:
        return matches
    matches = [
        source for source in sources
        if lowered in source.get("id", "").lower() or lowered in source.get("label", "").lower()
    ]
    if len(matches) == 1:
        return matches
    if not matches:
        raise ValueError(f"No source matched '{source_query}' for {person['name']}.")
    matched = ", ".join(source["label"] for source in matches)
    raise ValueError(f"Ambiguous source '{source_query}'. Matches: {matched}")


def evaluate_source(source, limit: int):
    source_type = source.get("type")
    if source_type == "web-page":
        url = (source.get("url") or "").lower()
        label = (source.get("label") or "").lower()
        if "x.com/" in url or "twitter.com/" in url or label.startswith("x "):
            return {
                "status": "deferred",
                "error": "x sources temporarily deferred",
                "items": [],
            }
        return {
            "status": "manual",
            "error": "manual source",
            "items": [],
        }
    fetcher = FETCHERS.get(source_type)
    if not fetcher:
        return {
            "status": "unsupported",
            "error": f"Unsupported source type: {source_type}",
            "items": [],
        }
    outcome = _source_timeout_process(source, limit)
    if outcome.get("ok"):
        return {"status": "ok", "error": None, "items": outcome.get("items") or []}
    if source_type == "google-scholar" and outcome.get("kind") == "http" and outcome.get("code") in {403, 429}:
        return {
            "status": "manual",
            "error": f"Google Scholar rate limited (HTTP {outcome['code']}); use web fallback",
            "items": [],
        }
    if outcome.get("kind") == "unsupported":
        return {"status": "unsupported", "error": outcome.get("error"), "items": []}
    if outcome.get("kind") == "timeout":
        return {"status": "error", "error": outcome.get("error"), "items": []}
    return {"status": "error", "error": outcome.get("error"), "items": []}


def main():
    parser = argparse.ArgumentParser(description="Fetch recent items from the follow-people-updates registry.")
    parser.add_argument("--person", help="Only check one person.")
    parser.add_argument("--source", help="Only check one source label or id.")
    parser.add_argument("--limit", type=int, default=5, help="Max items per source.")
    parser.add_argument("--days", type=int, help="Only keep items published within the last N days.")
    parser.add_argument("--new-only", action="store_true", help="Only show unseen items.")
    parser.add_argument("--no-write", action="store_true", help="Do not update seen-item state.")
    parser.add_argument("--theme", action="append", help="Preference theme such as ai-infra, applications, agents, robotics, research, or ethics.")
    parser.add_argument("--keyword-boost", action="append", help="Boost items containing this keyword.")
    parser.add_argument("--keyword-penalty", action="append", help="Demote items containing this keyword.")
    parser.add_argument("--min-preference-score", type=int, default=None, help="Hide items below this preference score.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args()

    path = registry_path()
    if not path.exists():
        print(f"Registry does not exist: {path}", file=sys.stderr)
        return 1

    try:
        registry = load_registry(path)
        people = filter_people(registry, args.person)
    except ValueError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    max_seen = int((registry.get("defaults") or {}).get("max_seen_ids_per_source", 100))
    preferences = effective_preferences(registry, args)
    results = []
    now = datetime.now(timezone.utc).isoformat()
    now_dt = datetime.now(timezone.utc)
    touched = False

    for person in people:
        try:
            sources = filter_sources(person, args.source)
        except ValueError as exc:
            print(f"Error: {exc}", file=sys.stderr)
            return 1

        person_result = {
            "person_id": person.get("id"),
            "person_name": person.get("name"),
            "kind": person.get("kind"),
            "sources": [],
        }
        for source in sources:
            outcome = evaluate_source(source, args.limit)
            previous_seen = list(source.get("seen_ids", []))
            dated_items = outcome["items"]
            if args.days is not None:
                cutoff = now_dt.timestamp() - (args.days * 86400)
                dated_items = [
                    item for item in outcome["items"]
                    if (parsed := parse_timestamp(item.get("published_at"))) is not None
                    and parsed.timestamp() >= cutoff
                ]
            new_items = [
                item for item in dated_items
                if item.get("id") not in previous_seen
            ]
            visible_items = new_items if args.new_only else dated_items
            visible_items = sort_items_by_preference(visible_items, preferences)
            new_items = sort_items_by_preference(new_items, preferences)
            if source.get("type") == "youtube-channel":
                visible_items = filter_youtube_items_by_relevance(visible_items, preferences)
                new_items = filter_youtube_items_by_relevance(new_items, preferences)
            if args.min_preference_score is not None:
                visible_items = [
                    item for item in visible_items
                    if item.get("preference_score", 0) >= args.min_preference_score
                ]
                new_items = [
                    item for item in new_items
                    if item.get("preference_score", 0) >= args.min_preference_score
                ]
            person_result["sources"].append(
                {
                    "source_id": source.get("id"),
                    "label": source.get("label"),
                    "type": source.get("type"),
                    "status": outcome["status"],
                    "error": outcome["error"],
                    "items": visible_items,
                    "new_items": new_items,
                }
            )
            if not args.no_write and outcome["status"] == "ok":
                current_ids = [item.get("id") for item in dated_items]
                source["seen_ids"] = unique_preserving_order(current_ids + previous_seen)[:max_seen]
                source["last_checked_at"] = now
                touched = True
        results.append(person_result)

    if touched and not args.no_write:
        save_registry(path, registry)

    if args.json:
        print(json.dumps({"checked_at": now, "preferences": preferences, "results": results}, ensure_ascii=False, indent=2))
        return 0

    for person_result in results:
        print(f"== {person_result['person_name']} [{person_result['kind']}] ==")
        if not person_result["sources"]:
            print("  (no enabled sources)")
            continue
        for source_result in person_result["sources"]:
            header = f"  [{source_result['source_id']}] {source_result['label']} ({source_result['type']})"
            print(header)
            if source_result["status"] == "manual":
                print("    manual-check source; use web browsing")
                continue
            if source_result["status"] != "ok":
                print(f"    error: {source_result['error']}")
                continue
            if not source_result["items"]:
                note = "no new items" if args.new_only else "no items"
                print(f"    {note}")
                continue
            for item in source_result["items"]:
                published = item.get("published_at") or "unknown-date"
                title = item.get("title") or "(untitled)"
                score = item.get("preference_score", 0)
                matches = ", ".join(item.get("preference_matches") or [])
                suffix = f" | score={score}" if preferences["themes"] or preferences["keyword_boosts"] or preferences["keyword_penalties"] else ""
                print(f"    - {published} | {title}{suffix}")
                if matches:
                    print(f"      matches: {matches}")
                if item.get("url"):
                    print(f"      {item['url']}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
