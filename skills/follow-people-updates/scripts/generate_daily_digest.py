#!/usr/bin/env python3

import argparse
import importlib.util
import json
import os
import re
import subprocess
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from bs4 import BeautifulSoup


SCRIPT_DIR = Path(__file__).resolve().parent
SKILL_DIR = SCRIPT_DIR.parent
FETCH_UPDATES_PATH = SCRIPT_DIR / "fetch_updates.py"
YOUTUBE_TRANSCRIPT_PATH = SCRIPT_DIR / "fetch_youtube_transcript.py"
REGISTRY_PATH = SKILL_DIR / "assets" / "tracking-registry.json"
DEFAULT_FOCUS_PROFILE_PATH = SKILL_DIR / "assets" / "focus-profile.json"
EXAMPLE_FOCUS_PROFILE_PATH = SKILL_DIR / "assets" / "focus-profile.example.json"
USER_AGENT = "Mozilla/5.0"

DEFAULT_FOCUS_PROFILE = {
    "name": "AI research and product updates",
    "focus_keywords": [
        "ai", "artificial intelligence", "llm", "language model", "vision-language",
        "agent", "agentic", "reasoning", "benchmark", "evaluation", "alignment",
        "safety", "ethics", "governance", "privacy", "research", "preprint",
        "arxiv", "training", "pre-training", "inference", "retrieval", "rag",
        "robot", "robotic", "embodied", "multimodal", "dataset", "foundation model",
    ],
    "include_preference_score_matches": True,
    "secondary_insight_rules": [
        {
            "label": "Agent reliability and evaluation",
            "keywords": ["agent", "agentic", "reasoning", "benchmark", "evaluation", "collapse", "safety"],
            "note": "Use this to track how teams measure agent failures, reliability, and guardrail quality.",
        },
        {
            "label": "Memory and personalization",
            "keywords": ["memory", "retrieval", "rag", "knowledge", "document", "personalization", "long-context"],
            "note": "Use this to watch patterns for retrieval, long-context workflows, and user-specific state.",
        },
        {
            "label": "Applied workflow UX",
            "keywords": ["assistant", "workflow", "application", "product", "user", "enterprise"],
            "note": "Use this to identify product patterns that make AI tools easier to adopt in real work.",
        },
        {
            "label": "Safety, policy, and governance",
            "keywords": ["safety", "ethics", "policy", "governance", "privacy", "harm", "bias", "risk"],
            "note": "Use this to follow work that changes deployment risk, compliance, or evaluation boundaries.",
        },
    ],
    "low_signal_keywords": ["marketing", "hiring", "fundraising", "career", "productivity", "event"],
    "recommended_tracks": [
        {
            "name": "OpenAI",
            "channels": "GitHub activity, official news",
            "focus": "frontier model releases, agent products, platform capabilities",
            "why": "Example target for tracking product launches, platform APIs, and agent workflows.",
        },
        {
            "name": "Google DeepMind",
            "channels": "GitHub activity, official blog",
            "focus": "model research, multimodal reasoning, evaluation",
            "why": "Example target for model research, benchmarks, and research-to-product translation.",
        },
    ],
    "digest_filter_label": "current focus profile",
    "main_empty_text": "No new items matched the current focus profile after dedupe and filtering.",
    "secondary_section_title": "Secondary Insights",
    "secondary_empty_text": "No items matched the secondary insight rules.",
    "secondary_judgment_prompt": "Evaluate whether this changes product, research, market, investment, or learning priorities.",
    "downgrade_note": "Current items look weakly related to the focus profile; consider lowering priority or removing this source.",
    "item_template": [
        "### {person_name} | {title}",
        "- 日期：{published_date}",
        "- 来源类型：{source_label} ({source_type})",
        "- 背景：{background}",
        "- 做了什么：{done}",
        "- 方法：{method}",
        "- 结果：{result}",
        "- 来源链接：{url}"
    ],
}


def load_fetch_updates_module():
    spec = importlib.util.spec_from_file_location("follow_fetch_updates", FETCH_UPDATES_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def request_text(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=10) as response:
        charset = response.headers.get_content_charset() or "utf-8"
        return response.read().decode(charset, errors="replace")


def default_output_dir() -> Path:
    override = os.environ.get("FOLLOW_PEOPLE_UPDATES_OUTPUT_DIR")
    if override:
        return Path(override).expanduser()
    return Path.cwd() / "news"


def default_focus_profile_path() -> Path:
    override = os.environ.get("FOLLOW_PEOPLE_UPDATES_FOCUS_PROFILE")
    if override:
        return Path(override).expanduser()
    if DEFAULT_FOCUS_PROFILE_PATH.exists():
        return DEFAULT_FOCUS_PROFILE_PATH
    return EXAMPLE_FOCUS_PROFILE_PATH


def default_registry_path() -> Path:
    override = os.environ.get("FOLLOW_PEOPLE_UPDATES_REGISTRY")
    if override:
        return Path(override).expanduser()
    return REGISTRY_PATH


def merge_focus_profile(profile):
    merged = dict(DEFAULT_FOCUS_PROFILE)
    if profile:
        merged.update(profile)
    for key in [
        "focus_keywords",
        "secondary_insight_rules",
        "low_signal_keywords",
        "recommended_tracks",
        "item_template",
    ]:
        if not isinstance(merged.get(key), list):
            merged[key] = DEFAULT_FOCUS_PROFILE[key]
    return merged


def load_focus_profile(path: Path = None):
    profile_path = path or default_focus_profile_path()
    if profile_path and profile_path.exists():
        with profile_path.open("r", encoding="utf-8") as handle:
            return merge_focus_profile(json.load(handle)), profile_path
    return merge_focus_profile({}), None


def parse_date(value: str):
    if not value:
        return None
    for candidate in (value, value.replace("Z", "+00:00")):
        try:
            dt = datetime.fromisoformat(candidate)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def item_text(item):
    return " ".join(part for part in [item.get("title"), item.get("summary")] if part).lower()


def is_focus_related(item, profile):
    text = item_text(item)
    keywords = [keyword.lower() for keyword in profile.get("focus_keywords", [])]
    if any(keyword in text for keyword in keywords):
        return True
    return bool(profile.get("include_preference_score_matches", True) and item.get("preference_score", 0) > 0)


def split_summary_fields(summary: str):
    if not summary:
        return {
            "background": "未明确说明",
            "done": "未明确说明",
            "method": "未明确说明",
            "result": "未明确说明",
        }
    sentences = [part.strip() for part in re.split(r"(?<=[.!?。])\s+", summary.strip()) if part.strip()]
    background = sentences[0] if len(sentences) >= 1 else "未明确说明"
    done = sentences[1] if len(sentences) >= 2 else background
    method = sentences[2] if len(sentences) >= 3 else "未明确说明"
    result = sentences[-1] if len(sentences) >= 2 else "未明确说明"
    return {
        "background": background,
        "done": done,
        "method": method,
        "result": result,
    }


def first_meaningful_sentence(text: str):
    if not text:
        return None
    sentences = [part.strip() for part in re.split(r"(?<=[.!?。])\s+", text) if part.strip()]
    for sentence in sentences:
        if len(sentence) >= 20:
            return sentence
    return sentences[0] if sentences else None


def fetch_github_readme_snippet(repo_url: str):
    try:
        html = request_text(repo_url)
    except Exception:
        return None
    soup = BeautifulSoup(html, "html.parser")
    article = soup.select_one("article.markdown-body")
    if not article:
        return None
    text = " ".join(article.stripped_strings)
    return text[:900] if text else None


def fetch_github_repo_context(repo_url: str):
    try:
        html = request_text(repo_url)
    except Exception:
        return None
    soup = BeautifulSoup(html, "html.parser")
    article = soup.select_one("article.markdown-body")
    meta_desc = None
    meta = soup.select_one('meta[name="description"]') or soup.select_one('meta[property="og:description"]')
    if meta:
        meta_desc = (meta.get("content") or "").strip() or None

    if not article:
        return {"description": meta_desc, "sections": {}}

    sections = {"intro": []}
    current = "intro"
    for node in article.select("h1, h2, h3, p, li"):
        text = " ".join(node.stripped_strings)
        if not text:
            continue
        if node.name in {"h1", "h2", "h3"}:
            current = text.lower()
            sections.setdefault(current, [])
            continue
        sections.setdefault(current, []).append(text)
    return {"description": meta_desc, "sections": sections}


def choose_section_text(sections, keywords):
    for heading, blocks in sections.items():
        heading_l = heading.lower()
        if any(keyword in heading_l for keyword in keywords):
            for block in blocks:
                sentence = first_meaningful_sentence(block)
                if sentence:
                    return sentence
    return None


def choose_capability_bullets(sections, limit=2):
    bullets = []
    seen = set()
    for heading, blocks in sections.items():
        if any(token in heading for token in ["feature", "capabilit", "highlight", "overview", "what"]):
            for block in blocks:
                normalized = block.strip()
                if len(normalized) >= 16 and normalized not in seen:
                    seen.add(normalized)
                    bullets.append(block)
                if len(bullets) >= limit:
                    return "；".join(bullets[:limit])
    return None


def first_substantive_section_text(sections):
    skip_tokens = ["intro", "disclaimer", "table of contents", "install", "run", "contribut", "license"]
    for heading, blocks in sections.items():
        if any(token in heading for token in skip_tokens):
            continue
        for block in blocks:
            sentence = first_meaningful_sentence(block)
            if sentence:
                return sentence
    return None


def sentence_with_keywords(text, keywords):
    if not text:
        return None
    sentences = [part.strip() for part in re.split(r"(?<=[.!?。])\s+", text) if part.strip()]
    for sentence in sentences:
        lowered = sentence.lower()
        if any(keyword in lowered for keyword in keywords):
            return sentence
    return None


def result_like_sentence(sections):
    skip_tokens = ["disclaimer", "install", "run", "contribut", "license", "table of contents"]
    for heading, blocks in sections.items():
        if any(token in heading for token in skip_tokens):
            continue
        for block in blocks:
            sentence = sentence_with_keywords(
                block,
                ["benchmark", "performance", "accuracy", "faster", "latency", "throughput", "offline", "private", "speed"],
            )
            if sentence:
                return sentence
    return None


def build_github_summary_fields(item, context):
    fallback = item.get("summary")
    fallback_sentence = first_meaningful_sentence(fallback) if fallback else None
    if not context:
        return split_summary_fields(fallback)

    sections = context.get("sections", {})
    intro_blocks = sections.get("intro", [])
    intro_text = first_meaningful_sentence(" ".join(intro_blocks)) if intro_blocks else None
    description = first_meaningful_sentence(context.get("description"))
    substantive = first_substantive_section_text(sections)

    background = description or intro_text or fallback_sentence or "未明确说明"
    done = (
        choose_section_text(sections, ["overview", "about", "what", "introduction"])
        or substantive
        or intro_text
        or fallback_sentence
        or "未明确说明"
    )
    method = (
        choose_section_text(sections, ["how", "approach", "architecture", "method", "design", "workflow", "usage"])
        or sentence_with_keywords(" ".join(intro_blocks), ["use", "uses", "using", "built", "system employs", "framework"])
        or choose_capability_bullets(sections)
        or "未明确说明"
    )
    result = (
        choose_section_text(sections, ["result", "benchmark", "performance", "evaluation", "impact", "why"])
        or result_like_sentence(sections)
        or "未明确说明"
    )
    return {
        "background": background,
        "done": done,
        "method": method,
        "result": result,
    }


def maybe_fetch_youtube_transcript(item, max_transcripts_state):
    if max_transcripts_state["count"] >= max_transcripts_state["limit"]:
        return None
    title = (item.get("title") or "").lower()
    if "/shorts/" in (item.get("url") or ""):
        return None
    if not any(keyword in title for keyword in ["ai", "agent", "model", "llm", "benchmark", "reasoning", "safety"]):
        return None
    try:
        output = subprocess.check_output(
            [
                "python3",
                str(YOUTUBE_TRANSCRIPT_PATH),
                "--url",
                item["url"],
                "--json",
            ],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        data = json.loads(output)
        transcript_text = " ".join(segment["text"] for segment in data.get("segments", []))
        max_transcripts_state["count"] += 1
        return transcript_text[:1800] if transcript_text else None
    except Exception:
        return None


def extract_repo_root(url: str):
    match = re.match(r"https?://github\.com/([^/]+/[^/]+)", url or "")
    return f"https://github.com/{match.group(1)}" if match else None


def parse_github_trending(url: str, day_dt: datetime):
    html = request_text(url)
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for article in soup.select("article.Box-row")[:10]:
        repo_link = article.select_one("h2 a")
        if not repo_link:
            continue
        repo_path = repo_link.get("href", "").strip()
        repo_name = " ".join(repo_link.get_text(" ", strip=True).split())
        description_el = article.select_one("p")
        description = description_el.get_text(" ", strip=True) if description_el else None
        items.append(
            {
                "person_name": "AI Hot Posts",
                "source_label": "GitHub trending",
                "source_type": "web-page",
                "title": repo_name,
                "url": f"https://github.com{repo_path}",
                "published_at": day_dt.isoformat(),
                "summary": description,
            }
        )
    return items


def parse_claude_blog(url: str, day_dt: datetime):
    html = request_text(url)
    soup = BeautifulSoup(html, "html.parser")
    items = []
    seen = set()
    for link in soup.select('a[href*="/blog/"]'):
        href = link.get("href") or ""
        if "/blog/category/" in href:
            continue
        if href.startswith("/"):
            full_url = f"https://claude.com{href}"
        elif href.startswith("https://claude.com/blog/"):
            full_url = href
        else:
            continue
        if full_url in seen:
            continue
        seen.add(full_url)
        title = " ".join(link.get_text(" ", strip=True).split())
        if title.lower() == "read more":
            try:
                article_html = request_text(full_url)
                article_soup = BeautifulSoup(article_html, "html.parser")
                meta_title = article_soup.select_one('meta[property="og:title"]')
                h1 = article_soup.select_one("h1")
                title = (
                    (meta_title.get("content") or "").strip()
                    if meta_title and meta_title.get("content")
                    else (" ".join(h1.get_text(" ", strip=True).split()) if h1 else title)
                )
            except Exception:
                pass
        if not title or len(title) < 8:
            continue
        items.append(
            {
                "person_name": "Claude blog",
                "source_label": "Claude blog",
                "source_type": "web-page",
                "title": title,
                "url": full_url,
                "published_at": day_dt.isoformat(),
                "summary": "Anthropic Claude blog post or landing page excerpt.",
            }
        )
        if len(items) >= 8:
            break
    return items


def collect_items(days: int, limit: int, no_write: bool, registry_path: Path = None):
    cmd = [
        "python3",
        str(FETCH_UPDATES_PATH),
        "--json",
        "--new-only",
        "--days",
        str(days),
        "--limit",
        str(limit),
    ]
    if no_write:
        cmd.append("--no-write")
    env = os.environ.copy()
    if registry_path:
        env["FOLLOW_PEOPLE_UPDATES_REGISTRY"] = str(registry_path)
    raw = subprocess.check_output(cmd, text=True, env=env)
    data = json.loads(raw)
    items = []
    for person in data["results"]:
        for source in person["sources"]:
            for item in source.get("items", []):
                enriched = dict(item)
                enriched["person_name"] = person["person_name"]
                enriched["source_label"] = source["label"]
                enriched["source_type"] = source["type"]
                items.append(enriched)
    return data.get("preferences", {}), items, data.get("results", [])


def load_registry(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def summarize_scan_review(results, window_label: str):
    scanned_no_updates = []
    failed = []
    not_scanned = []
    for person in results:
        for source in person.get("sources", []):
            label = f"{person['person_name']} / {source.get('label')}"
            status = source.get("status")
            if status == "ok" and not source.get("items"):
                scanned_no_updates.append(label)
            elif status in {"error", "unsupported"}:
                failed.append(f"{label}: {source.get('error')}")
            elif status in {"manual", "deferred"}:
                not_scanned.append(f"{label}: {source.get('error')}")
    return scanned_no_updates, failed, not_scanned


def prune_candidates(registry, results, profile):
    notes_by_name = {person["name"]: (person.get("notes") or "") for person in registry.get("people", [])}
    candidates = []
    low_signal_keywords = [keyword.lower() for keyword in profile.get("low_signal_keywords", [])]
    focus_keywords = [keyword.lower() for keyword in profile.get("focus_keywords", [])]
    for person in results:
        combined = " ".join(
            filter(
                None,
                [notes_by_name.get(person["person_name"], "")]
                + [item.get("title", "") for source in person.get("sources", []) for item in source.get("items", [])]
                + [item.get("summary", "") or "" for source in person.get("sources", []) for item in source.get("items", [])],
            )
        ).lower()
        if any(keyword in combined for keyword in low_signal_keywords):
            if not any(keyword in combined for keyword in focus_keywords):
                candidates.append(person["person_name"])
    return sorted(set(candidates))


def recommended_tracks(profile):
    return profile.get("recommended_tracks", [])


def read_existing_markers(output_dir: Path, exclude_path: Path = None):
    markers = set()
    if not output_dir.exists():
        return markers
    for path in sorted(output_dir.glob("daily-digest*.md")):
        if exclude_path and path == exclude_path:
            continue
        text = path.read_text(encoding="utf-8")
        for line in text.splitlines():
            markers.add(line.strip())
    return markers


def build_secondary_insight(item, profile):
    text = item_text(item)
    for rule in profile.get("secondary_insight_rules", []):
        label = rule.get("label")
        keywords = [keyword.lower() for keyword in (rule.get("keywords") or [])]
        note = rule.get("note")
        if any(keyword in text for keyword in keywords):
            return label, note
    return None, None


def dedupe_items(items, markers):
    deduped = []
    seen_urls = set()
    seen_titles = set()
    for item in items:
        url = item.get("url") or ""
        title = item.get("title") or ""
        if url in markers or title in markers:
            continue
        if url and url in seen_urls:
            continue
        if title and title in seen_titles:
            continue
        if url:
            seen_urls.add(url)
        if title:
            seen_titles.add(title)
        deduped.append(item)
    return deduped


def enrich_item(item, transcript_state):
    item = dict(item)
    if "github.com/" in (item.get("url") or ""):
        repo_url = extract_repo_root(item.get("url"))
        if repo_url:
            context = fetch_github_repo_context(repo_url)
            if context:
                item["summary_fields"] = build_github_summary_fields(item, context)
            else:
                readme = fetch_github_readme_snippet(repo_url)
                if readme:
                    item["summary"] = readme
    elif item.get("source_type") == "youtube-channel":
        transcript = maybe_fetch_youtube_transcript(item, transcript_state)
        if transcript:
            item["summary"] = transcript
    return item


def format_item_block(item, profile):
    """Render one digest item using the profile's item_template."""
    published = parse_date(item.get("published_at"))
    published_str = published.strftime("%Y-%m-%d") if published else "日期未明确"
    fields = item.get("summary_fields") or split_summary_fields(item.get("summary"))
    values = {
        "person_name": item.get("person_name") or "Unknown",
        "title": item.get("title") or "Untitled",
        "published_date": published_str,
        "source_label": item.get("source_label") or "unknown source",
        "source_type": item.get("source_type") or "unknown",
        "background": fields["background"],
        "done": fields["done"],
        "method": fields["method"],
        "result": fields["result"],
        "url": item.get("url") or "未明确说明",
        "summary": item.get("summary") or "",
        "preference_score": item.get("preference_score", 0),
    }
    rendered = []
    for line in profile.get("item_template", DEFAULT_FOCUS_PROFILE["item_template"]):
        try:
            rendered.append(line.format(**values))
        except KeyError:
            rendered.append(line)
    return "\n".join(rendered)


def main():
    parser = argparse.ArgumentParser(description="Generate a dated focus-filtered daily digest.")
    parser.add_argument("--days", type=int, default=1)
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--max-items", type=int, default=0, help="Maximum digest items to include; 0 means no cap.")
    parser.add_argument("--no-write-seen", action="store_true")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--registry", type=Path, default=None, help="Path to a tracking-registry JSON file.")
    parser.add_argument("--focus-profile", type=Path, default=None, help="Path to a focus-profile JSON file.")
    args = parser.parse_args()

    today = datetime.now(timezone.utc)
    day_label = today.strftime("%Y-%m-%d")
    output_dir = args.output_dir.expanduser() if args.output_dir else default_output_dir()
    output_path = output_dir / f"daily-digest-{day_label}.md"
    output_dir.mkdir(parents=True, exist_ok=True)

    registry_path = args.registry.expanduser() if args.registry else default_registry_path()
    preferences, items, raw_results = collect_items(args.days, args.limit, args.no_write_seen, registry_path)
    registry = load_registry(registry_path)
    focus_profile, focus_profile_path = load_focus_profile(args.focus_profile.expanduser() if args.focus_profile else None)
    manual_items = []
    manual_items.extend(parse_github_trending("https://github.com/trending", today))
    manual_items.extend(parse_claude_blog("https://claude.com/blog", today))
    items.extend(manual_items)

    focus_items = [item for item in items if is_focus_related(item, focus_profile)]
    focus_items.sort(
        key=lambda item: (
            item.get("preference_score", 0),
            item.get("published_at") or "",
            item.get("title") or "",
        ),
        reverse=True,
    )

    markers = read_existing_markers(output_dir, exclude_path=output_path)
    focus_items = dedupe_items(focus_items, markers)

    if args.max_items and args.max_items > 0:
        focus_items = focus_items[:args.max_items]

    transcript_state = {"count": 0, "limit": 2}
    focus_items = [enrich_item(item, transcript_state) for item in focus_items]

    secondary_items = []
    for item in focus_items:
        label, note = build_secondary_insight(item, focus_profile)
        if label:
            secondary_items.append((item, label, note))
    secondary_items = secondary_items[:5]
    window_label = "最近24小时" if args.days == 1 else f"最近{args.days}天"
    scanned_no_updates, failed_sources, not_scanned = summarize_scan_review(raw_results, window_label)
    prune_list = prune_candidates(registry, raw_results, focus_profile)
    recommendations = recommended_tracks(focus_profile)

    lines = [
        f"# Daily Digest {day_label}",
        "",
        f"- 生成时间：{today.isoformat()}",
        f"- Focus profile：{focus_profile.get('name') or 'Untitled profile'}",
        f"- Focus profile path：{focus_profile_path or 'built-in default'}",
        f"- 过滤范围：仅保留 `{focus_profile.get('digest_filter_label')}` 相关信息；可在 focus profile JSON 中调整。",
        f"- 时间窗口：{window_label}（按执行时刻向前回看）。",
        f"- 偏好：{', '.join(preferences.get('themes', [])) or '无'}",
        f"- Source 上限：每个 source 最多抓 {args.limit} 条。",
        f"- Token 策略：YouTube 先看标题，最多对 2 条高相关视频拉 transcript；GitHub 只看标题和 README 摘要，不读全仓代码；网页类手工源只做轻量页级解析。",
        "",
        "## Main Digest",
        "",
    ]

    if not focus_items:
        lines.append(focus_profile.get("main_empty_text") or DEFAULT_FOCUS_PROFILE["main_empty_text"])
    else:
        for item in focus_items:
            lines.append(format_item_block(item, focus_profile))
            lines.append("")

    lines.extend([
        f"## {focus_profile.get('secondary_section_title') or DEFAULT_FOCUS_PROFILE['secondary_section_title']}",
        "",
    ])
    if not secondary_items:
        lines.append(focus_profile.get("secondary_empty_text") or DEFAULT_FOCUS_PROFILE["secondary_empty_text"])
    else:
        for item, label, note in secondary_items:
            lines.extend(
                [
                    f"### {item['person_name']} | {item['title']}",
                    f"- 重点关注：{label}",
                    f"- 启发：{note}",
                    f"- 进一步判断：{focus_profile.get('secondary_judgment_prompt') or DEFAULT_FOCUS_PROFILE['secondary_judgment_prompt']}",
                    f"- 来源链接：{item.get('url') or '未明确说明'}",
                    "",
                ]
            )

    lines.extend([
        "## Review",
        "",
        "### 渠道状态",
    ])
    if scanned_no_updates:
        lines.append(f"- 已扫描但{window_label}无更新：{'; '.join(scanned_no_updates[:20])}")
    else:
        lines.append(f"- 已扫描但{window_label}无更新：无")
    if failed_sources:
        lines.append(f"- 扫描失败：{'; '.join(failed_sources[:20])}")
    else:
        lines.append("- 扫描失败：无")
    if not_scanned:
        lines.append(f"- 未扫描：{'; '.join(not_scanned[:20])}")
    else:
        lines.append("- 未扫描：无")

    lines.extend([
        "",
        "### 可停更对象",
    ])
    if prune_list:
        for name in prune_list:
            lines.append(f"- {name}：{focus_profile.get('downgrade_note') or DEFAULT_FOCUS_PROFILE['downgrade_note']}")
    else:
        lines.append("- 暂无明显建议停更的人。")

    lines.extend([
        "",
        "### 建议补充关注",
    ])
    for rec in recommendations:
        lines.append(
            f"- {rec['name']}｜渠道：{rec['channels']}｜方向：{rec['focus']}｜推荐理由：{rec['why']}"
        )

    output_path.write_text("\n".join(lines).strip() + "\n", encoding="utf-8")
    print(str(output_path))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
