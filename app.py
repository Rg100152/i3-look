#!/usr/bin/env python3
"""i3-look - ultra-lightweight public profile OSINT web portal.

Ethical use only: public metadata and the official Meta Graph API. No login
bypass, no private-account scraping, no credential storage.
"""

from __future__ import annotations

import html as html_lib
import json
import os
import re
import socket
import threading
import time
from typing import Any, Dict, Iterator, Optional, Tuple
from urllib.parse import quote, urlsplit

import requests
from flask import Flask, Response, jsonify, render_template, request

APP_NAME = "i3-look"
USERNAME_RE = re.compile(r"^[A-Za-z0-9._]{1,30}$")
CACHE_TTL = 900  # 15 minutes
REQUEST_TIMEOUT = 8  # seconds
MAX_CACHE_ENTRIES = 200  # bounded cache keeps the RAM footprint tiny
MAX_IMAGE_BYTES = 10 * 1024 * 1024  # proxy safety cap
PORT_START = 5000
PORT_ATTEMPTS = 11  # 5000 ... 5010

CACHE: Dict[str, Tuple[float, Dict[str, Any]]] = {}
CACHE_LOCK = threading.Lock()

IG_ACCESS_TOKEN = os.environ.get("IG_ACCESS_TOKEN", "").strip()
IG_BUSINESS_ID = os.environ.get("IG_BUSINESS_ID", "").strip()
GRAPH_VERSION = os.environ.get("IG_GRAPH_VERSION", "v19.0")

app = Flask(__name__)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    ),
    "Accept-Language": "en-US,en;q=0.9",
}

# Best-effort public web endpoint. No login and no credentials are used.
WEB_API_URL = "https://www.instagram.com/api/v1/users/web_profile_info/"
WEB_API_HEADERS = dict(HEADERS)
WEB_API_HEADERS["X-IG-App-ID"] = "936619743392459"
WEB_API_HEADERS["Accept"] = "*/*"

# Instagram still serves OpenGraph tags on the web view with a simple UA.
SIMPLE_HEADERS = {"User-Agent": "Mozilla/5.0", "Accept-Language": "en-US,en;q=0.9"}


# --------------------------------------------------------------------------- #
# General helpers
# --------------------------------------------------------------------------- #
def clean(value: Any) -> str:
    """Unescape HTML entities and normalise whitespace in text values."""
    if value is None:
        return ""
    return html_lib.unescape(str(value)).strip()


def sanitize_username(raw: Any) -> Optional[str]:
    """Strip leading @ / whitespace, then strictly validate the username."""
    if not isinstance(raw, str):
        return None
    value = raw.strip().lstrip("@").strip()
    if not USERNAME_RE.fullmatch(value):
        return None
    return value


def to_int(value: Any, suffix: str = "") -> Optional[int]:
    """Convert '1,234', '1.2K', '3M' or an int into a plain integer."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return int(value)
    text = clean(value).replace(",", "").replace(" ", "").upper()
    multiplier = {"K": 1_000, "M": 1_000_000, "B": 1_000_000_000}.get(
        suffix.upper(), 1
    )
    match = re.search(r"(\d+(?:\.\d+)?)", text)
    if not match:
        return None
    return int(float(match.group(1)) * multiplier)


def merge_data(target: Dict[str, Any], source: Dict[str, Any]) -> None:
    """Overlay a data layer without letting empty values erase better ones."""
    for key, value in source.items():
        if key.startswith("_") or value is None or value == "":
            continue
        if isinstance(value, (list, dict)) and not value:
            continue
        target[key] = value


def dedupe(items: Any) -> list:
    """Preserve order while removing duplicate values."""
    result: list = []
    for item in items or []:
        if item and item not in result:
            result.append(item)
    return result


def ratio(followers: Optional[int], following: Optional[int]) -> Tuple[str, Optional[float]]:
    """Follower/Following ratio as a display string and numeric value."""
    if followers is None or not following:
        return "N/A", None
    value = round(followers / following, 2)
    return f"{value}:1", value


def extract_entities(text: str) -> Tuple[list, list, list, list]:
    """Pull public URLs, emails, @mentions and #hashtags from bio text."""
    text = clean(text)
    links, emails, mentions, hashtags = [], [], [], []

    for match in re.finditer(r"(?:https?://|www\.)[^\s<>\"'()]+", text, re.I):
        links.append(match.group(0).rstrip(".,;:!?)]}'\""))

    for match in re.finditer(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text):
        emails.append(match.group(0))

    for match in re.finditer(r"(?<![\w@])@([A-Za-z0-9._]{1,30})", text):
        mentions.append("@" + match.group(1))

    for match in re.finditer(r"(?<!\w)#([A-Za-z0-9_]{1,30})", text):
        hashtags.append("#" + match.group(1))

    return dedupe(links), dedupe(emails), dedupe(mentions), dedupe(hashtags)


def hd_image_url(url: str) -> str:
    """Return the CDN URL unchanged: its signature breaks if size tokens change."""
    return clean(url)


def build_osint_links(username: str) -> Dict[str, str]:
    """Quick external OSINT jumps, built only from a validated username."""
    return {
        "instagram": f"https://www.instagram.com/{username}/",
        "google": (
            "https://www.google.com/search?q="
            + quote(f'"{username}" site:instagram.com', safe="")
        ),
        "wayback": (
            "https://web.archive.org/web/*/https://www.instagram.com/"
            f"{username}/"
        ),
    }


# --------------------------------------------------------------------------- #
# Cache
# --------------------------------------------------------------------------- #
def cache_get(username: str) -> Optional[Dict[str, Any]]:
    with CACHE_LOCK:
        entry = CACHE.get(username)
        if not entry:
            return None
        timestamp, payload = entry
        if time.time() - timestamp >= CACHE_TTL:
            CACHE.pop(username, None)
            return None
        return dict(payload)


def cache_set(username: str, payload: Dict[str, Any]) -> None:
    with CACHE_LOCK:
        if len(CACHE) >= MAX_CACHE_ENTRIES:
            oldest = min(CACHE, key=lambda key: CACHE[key][0])
            CACHE.pop(oldest, None)
        CACHE[username] = (time.time(), dict(payload))


# --------------------------------------------------------------------------- #
# HTML / OpenGraph parsing helpers
# --------------------------------------------------------------------------- #
def meta_content(page: str, attr: str, value: str) -> str:
    """Read a <meta> tag's content by one of its identifying attributes."""
    pattern = rf"<meta\b[^>]*\b{re.escape(attr)}\s*=\s*([\"']){re.escape(value)}\1[^>]*>"
    tag = re.search(pattern, page, re.IGNORECASE | re.DOTALL)
    if not tag:
        return ""
    content = re.search(r"\bcontent\s*=\s*([\"'])(.*?)\1", tag.group(0), re.I | re.S)
    return clean(content.group(2)) if content else ""


def walk_nodes(node: Any) -> Iterator[Any]:
    """Yield every dict/list node inside parsed JSON-LD data."""
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk_nodes(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk_nodes(value)


def jsonld_person(page: str) -> Dict[str, str]:
    """Extract optional Person data from JSON-LD blocks in the public page."""
    result: Dict[str, str] = {}
    blocks = re.findall(
        r'<script\b[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
        page,
        re.I | re.S,
    )
    for block in blocks:
        try:
            data = json.loads(html_lib.unescape(block.strip()))
        except (ValueError, TypeError):
            continue
        for node in walk_nodes(data):
            if not isinstance(node, dict):
                continue
            if node.get("@type") not in ("Person", "ProfilePage") and "alternateName" not in node:
                continue
            for source, target in (
                ("name", "name"),
                ("alternateName", "name"),
                ("description", "description"),
                ("image", "image"),
                ("url", "url"),
            ):
                value = node.get(source)
                if isinstance(value, str) and not result.get(target):
                    result[target] = clean(value)
    return result


def clean_title(title: str, username: str) -> str:
    """Turn 'Name (@user) • Instagram photos and videos' into 'Name'."""
    if not title:
        return ""
    name = title.split("•")[0].split("|")[0].split("(")[0].strip().lstrip("@").strip()
    name = clean(name)
    if not name or name.lower() in {"instagram", "photo", "videos", "home"}:
        return ""
    return name[:80]


def clean_bio(description: str) -> str:
    """Remove count fragments and boilerplate from the OpenGraph description."""
    if not description:
        return ""
    text = re.sub(
        r"\b[\d.,]+\s*[KMB]?\s*(?:followers?|following|posts?|photos?|media)\b",
        "",
        description,
        flags=re.I,
    )
    text = re.sub(r"[-|•]?\s*See Instagram photos and videos.*$", "", text, flags=re.I)
    text = re.sub(r"^[\s,•|\-]+", "", text)
    text = re.sub(r"\s{2,}", " ", text).strip(" ,•|-")
    return html_lib.unescape(text)


def extract_counts(description: str) -> Tuple[Optional[int], Optional[int], Optional[int]]:
    """Parse follower / following / post counts from an OpenGraph description."""
    values: Dict[str, Optional[int]] = {"followers": None, "following": None, "posts": None}
    if not description:
        return values["followers"], values["following"], values["posts"]

    pattern = re.compile(
        r"([\d][\d.,]*)\s*([KMB]?)\s*(followers?|following|posts?|photos?|media)\b",
        re.IGNORECASE,
    )
    for match in pattern.finditer(description):
        amount = to_int(match.group(1), match.group(2))
        label = match.group(3).lower()
        if label.startswith("follower"):
            key = "followers"
        elif label.startswith("following"):
            key = "following"
        else:
            key = "posts"
        if values[key] is None and amount is not None:
            values[key] = amount
    return values["followers"], values["following"], values["posts"]


def counts_from_page(page: str) -> Dict[str, int]:
    """Second-layer count recovery from embedded JSON in the public HTML."""
    patterns = {
        "followers": (
            r'"followers_count"\s*:\s*(\d+)',
            r'"follower_count"\s*:\s*(\d+)',
            r'"edge_followed_by"\s*:\s*\{[^{}]*"count"\s*:\s*(\d+)',
        ),
        "following": (
            r'"follows_count"\s*:\s*(\d+)',
            r'"following_count"\s*:\s*(\d+)',
            r'"edge_follow"\s*:\s*\{[^{}]*"count"\s*:\s*(\d+)',
        ),
        "posts": (
            r'"media_count"\s*:\s*(\d+)',
            r'"edge_owner_to_timeline_media"\s*:\s*\{[^{}]*"count"\s*:\s*(\d+)',
        ),
    }
    found: Dict[str, int] = {}
    for key, candidates in patterns.items():
        for candidate in candidates:
            match = re.search(candidate, page, re.I)
            if match:
                found[key] = int(match.group(1))
                break
    return found


def completeness(data: Dict[str, Any]) -> int:
    """Simple 0-100 score based on which public fields were available."""
    score = 0
    score += 25 if data.get("full_name") else 0
    score += 20 if data.get("avatar") else 0
    score += 20 if data.get("bio") else 0
    score += 15 if data.get("followers") is not None else 0
    score += 10 if data.get("following") is not None else 0
    score += 10 if data.get("posts") is not None else 0
    return score


# --------------------------------------------------------------------------- #
# Image proxy helpers
# --------------------------------------------------------------------------- #
def image_url_allowed(url: str) -> bool:
    """Only allow Instagram / Meta CDN hosts (prevents SSRF via proxy)."""
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
        return False
    host = parts.hostname.lower().rstrip(".")
    exact = {
        "cdninstagram.com",
        "fbcdn.net",
        "instagram.com",
        "www.instagram.com",
        "graph.facebook.com",
    }
    return host in exact or host.endswith((".cdninstagram.com", ".fbcdn.net"))


def avatar_proxy_url(avatar: str, username: str) -> str:
    if not avatar or not image_url_allowed(avatar):
        return ""
    return "/proxy-image?url=" + quote(avatar, safe="") + "&username=" + quote(username, safe="")


def image_extension(content_type: str) -> str:
    return {
        "image/jpeg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "image/gif": "gif",
    }.get(content_type, "img")


# --------------------------------------------------------------------------- #
# Data sources
# --------------------------------------------------------------------------- #
def query_web_profile_api(username: str) -> Dict[str, Any]:
    """Layer 1: public web_profile_info, if Instagram still serves it."""
    try:
        response = requests.get(
            WEB_API_URL,
            params={"username": username},
            headers=WEB_API_HEADERS,
            timeout=REQUEST_TIMEOUT,
        )
    except requests.RequestException:
        return {}
    if response.status_code != 200:
        return {}
    try:
        payload = response.json()
    except ValueError:
        return {}  # Instagram now returns an HTML login page here on many IPs.
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        return {}
    user = payload["data"].get("user")
    if not isinstance(user, dict):
        return {}

    def edge(parent: Dict[str, Any], key: str) -> Dict[str, Any]:
        value = parent.get(key)
        return value if isinstance(value, dict) else {}

    website = clean(user.get("external_url"))
    is_private = bool(user.get("is_private"))
    return {
        "user_id": clean(user.get("id")),
        "full_name": clean(user.get("full_name")) or username,
        "bio": clean(user.get("biography")),
        "avatar": hd_image_url(
            clean(user.get("profile_pic_url_hd") or user.get("profile_pic_url"))
        ),
        "followers": to_int(edge(user, "edge_followed_by").get("count")),
        "following": to_int(edge(user, "edge_follow").get("count")),
        "posts": to_int(edge(user, "edge_owner_to_timeline_media").get("count")),
        "privacy": "Private" if is_private else "Public",
        "verified": bool(user.get("is_verified")),
        "verified_known": True,
        "is_business": bool(user.get("is_business_account")),
        "category": clean(user.get("category_name")),
        "links": [website] if website else [],
        "status": "Private" if is_private else "Public",
        "source": "Instagram Public Web API",
    }


def query_og_variant(username: str) -> Dict[str, Any]:
    """Layer 2: public web view (?__a=1) which still exposes OpenGraph counts."""
    url = f"https://www.instagram.com/{username}/?__a=1"
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT, headers=SIMPLE_HEADERS)
    except requests.RequestException:
        return {}
    if response.status_code != 200:
        return {}

    page = response.text
    title = meta_content(page, "property", "og:title")
    description = meta_content(page, "property", "og:description")
    image = meta_content(page, "property", "og:image")
    if not (title or description or image):
        return {}

    followers, following, posts = extract_counts(description)
    links, emails, mentions, hashtags = extract_entities(description)
    verified = bool(
        re.search(r'"is_verified"\s*:\s*true|"verified"\s*:\s*true', page, re.I)
    )
    private = bool(
        re.search(r'"is_private"\s*:\s*true|this account is private', page, re.I)
    )
    return {
        "full_name": clean_title(title, username),
        "bio": clean_bio(description),
        "avatar": hd_image_url(image),
        "followers": followers,
        "following": following,
        "posts": posts,
        "links": links,
        "emails": emails,
        "mentions": mentions,
        "hashtags": hashtags,
        "verified": verified,
        "privacy": "Private" if private else "Public",
        "status": "Private" if private else "Public",
        "source": "Instagram Public OG View",
    }


def query_meta_api(username: str) -> Optional[Dict[str, Any]]:
    """Official Meta Graph API business_discovery source (optional)."""
    if not (IG_ACCESS_TOKEN and IG_BUSINESS_ID):
        return None

    fields = (
        "username,name,profile_picture_url,biography,"
        "followers_count,follows_count,media_count"
    )
    url = (
        f"https://graph.facebook.com/{GRAPH_VERSION}/{IG_BUSINESS_ID}"
        f"?fields=business_discovery.username({username}){{{fields}}}"
        f"&access_token={IG_ACCESS_TOKEN}"
    )
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT, headers=HEADERS)
    except requests.RequestException as exc:
        return {"_error": f"Meta API network error ({exc.__class__.__name__})"}

    if response.status_code == 429:
        return {"_error": "Meta API rate limit reached"}
    if response.status_code != 200:
        return {"_error": f"Meta API HTTP {response.status_code}"}
    try:
        payload = response.json()
    except ValueError:
        return {"_error": "Meta API returned invalid JSON"}

    discovery = payload.get("business_discovery") if isinstance(payload, dict) else None
    if not isinstance(discovery, dict) or not discovery:
        message = "Meta API returned no discovery data"
        if isinstance(payload, dict) and isinstance(payload.get("error"), dict):
            message = payload["error"].get("message", message)
        return {"_error": clean(message)}

    return {
        "full_name": clean(discovery.get("name") or discovery.get("username") or username),
        "followers": to_int(discovery.get("followers_count")),
        "following": to_int(discovery.get("follows_count")),
        "posts": to_int(discovery.get("media_count")),
        "bio": clean(discovery.get("biography")),
        "avatar": hd_image_url(clean(discovery.get("profile_picture_url"))),
        "status": "Public",
        "privacy": "Public",
    }


def query_public_metadata(username: str) -> Dict[str, Any]:
    """Public OpenGraph / JSON-LD metadata fallback."""
    url = f"https://www.instagram.com/{username}/"
    try:
        response = requests.get(url, timeout=REQUEST_TIMEOUT, headers=HEADERS)
    except requests.RequestException as exc:
        return {"_error": f"Network error ({exc.__class__.__name__})", "status": "Unavailable"}

    code = response.status_code
    if code == 404:
        return {"status": "User Not Found", "_error": "HTTP 404 - profile does not exist"}
    if code == 429:
        return {"status": "Rate Limited", "_error": "HTTP 429 - too many requests, try again later"}
    if code >= 400:
        return {"status": f"Unavailable (HTTP {code})", "_error": f"HTTP {code}"}

    page = response.text
    title = meta_content(page, "property", "og:title")
    description = meta_content(page, "property", "og:description")
    image = meta_content(page, "property", "og:image")
    person = jsonld_person(page)

    full_name = clean_title(title, username) or person.get("name") or username
    bio = person.get("description") or clean_bio(description)
    avatar = hd_image_url(image or person.get("image") or "")

    followers, following, posts = extract_counts(description)
    fallback_counts = counts_from_page(page)
    if followers is None:
        followers = fallback_counts.get("followers")
    if following is None:
        following = fallback_counts.get("following")
    if posts is None:
        posts = fallback_counts.get("posts")

    private_marker = re.search(
        r'"is_private"\s*:\s*true|this account is private', page, re.I
    )
    verified = bool(
        re.search(
            r'"is_verified"\s*:\s*true|"verified"\s*:\s*true|is_verified["=: ]+true',
            page,
            re.I,
        )
    )

    if private_marker:
        status, privacy = "Private", "Private"
    elif title or description or person:
        status, privacy = "Public", "Public"
    else:
        status, privacy = "Private Account / Login Required", "Unknown"

    entity_text = " ".join(filter(None, (bio, description, person.get("description", ""))))
    links, emails, mentions, hashtags = extract_entities(entity_text)
    if person.get("url"):
        links = dedupe(links + [person["url"]])

    return {
        "full_name": full_name,
        "followers": followers,
        "following": following,
        "posts": posts,
        "bio": bio,
        "avatar": avatar,
        "status": status,
        "privacy": privacy,
        "verified": verified,
        "links": links,
        "emails": emails,
        "mentions": mentions,
        "hashtags": hashtags,
        "source": "Public OpenGraph Metadata",
    }


# --------------------------------------------------------------------------- #
# Lookup pipeline
# --------------------------------------------------------------------------- #
def lookup(username: str) -> Dict[str, Any]:
    cached = cache_get(username)
    if cached:
        cached["cached"] = True
        return cached

    result: Dict[str, Any] = {
        "username": username,
        "full_name": username,
        "followers": None,
        "following": None,
        "posts": None,
        "status": "Unavailable",
        "privacy": "Unknown",
        "verified": False,
        "verified_known": False,
        "verified_text": "Unknown",
        "user_id": "",
        "category": "",
        "is_business": False,
        "bio": "",
        "avatar": "",
        "avatar_hd": "",
        "avatar_proxy": "",
        "links": [],
        "emails": [],
        "mentions": [],
        "hashtags": [],
        "ratio": "N/A",
        "ratio_value": None,
        "profile_url": f"https://www.instagram.com/{username}/",
        "osint_links": build_osint_links(username),
        "completeness": 0,
        "source": "",
        "cached": False,
        "error": "",
        "notice": "",
        "blocked": False,
    }

    # Weakest first: normal public page (HTML / JSON-LD).
    public = query_public_metadata(username)
    public_error = public.pop("_error", "")
    merge_data(result, public)
    sources = ["Public OpenGraph Metadata"]

    # Public web view still exposes OG counts on many networks.
    og_view = query_og_variant(username)
    if og_view:
        merge_data(result, og_view)
        sources.insert(0, "Instagram Public OG View")

    # Public web_profile_info endpoint (no login) when available.
    web_api = query_web_profile_api(username)
    if web_api:
        merge_data(result, web_api)
        sources.insert(0, "Instagram Public Web API")

    # Official Meta Graph API wins when configured.
    official = query_meta_api(username)
    official_error = ""
    if official and "_error" in official:
        official_error = official.pop("_error")
        official = None
    if official:
        merge_data(result, official)
        sources.insert(0, "Meta Graph API")
    result["source"] = " + ".join(sources)

    # Re-scan the final (merged) bio so entities survive every layer.
    entity_text = " ".join(filter(None, (result.get("bio", ""), result.get("category", ""))))
    links, emails, mentions, hashtags = extract_entities(entity_text)
    for key, extra in (
        ("links", links),
        ("emails", emails),
        ("mentions", mentions),
        ("hashtags", hashtags),
    ):
        result[key] = dedupe(list(result.get(key, [])) + extra)

    result["avatar_hd"] = hd_image_url(result.get("avatar", ""))
    result["avatar"] = result["avatar_hd"]
    result["avatar_proxy"] = avatar_proxy_url(result["avatar_hd"], username)
    if result.get("verified"):
        result["verified_text"] = "Verified"
    elif result.get("verified_known"):
        result["verified_text"] = "Standard"
    else:
        result["verified_text"] = "Unknown"
    result["ratio"], result["ratio_value"] = ratio(result["followers"], result["following"])
    result["completeness"] = completeness(result)
    if og_view or web_api or official:
        public_error = ""  # a stronger layer succeeded; don't show a stale 404/429
    result["error"] = "; ".join(part for part in (official_error, public_error) if part)

    has_metrics = any(result.get(key) is not None for key in ("followers", "following", "posts"))
    blocked = result["status"] in {
        "Private Account / Login Required",
        "Unavailable",
        "Rate Limited",
        "User Not Found",
    } or not has_metrics
    result["blocked"] = bool(blocked)
    if result["status"] == "Private":
        result["notice"] = "Account is private. Only public-facing metadata can be shown."
    elif blocked:
        result["notice"] = (
            "Instagram is hiding public metrics from unauthenticated requests on this "
            "network. No numbers are guessed. Use the quick OSINT links below, or set "
            "IG_ACCESS_TOKEN and IG_BUSINESS_ID for authoritative Meta Graph API data."
        )

    cache_set(username, result)
    return result


# --------------------------------------------------------------------------- #
# Routes
# --------------------------------------------------------------------------- #
@app.route("/")
def index():
    return render_template("index.html", app_name=APP_NAME, author="Raj Gautam")


@app.route("/api/lookup", methods=["GET", "POST"])
def api_lookup():
    if request.is_json:
        body = request.get_json(silent=True)
        raw = body.get("username", "") if isinstance(body, dict) else ""
    else:
        raw = request.values.get("username", "")

    username = sanitize_username(raw)
    if not username:
        return (
            jsonify(
                {
                    "error": "Invalid username. Use 1-30 letters, numbers, dots or underscores.",
                    "status": "Invalid Input",
                }
            ),
            400,
        )
    return jsonify(lookup(username))


@app.route("/proxy-image")
def proxy_image():
    """Stream an Instagram/Meta CDN image through the local server."""
    target = request.args.get("url", "")
    download = request.args.get("download", "0").lower() in ("1", "true", "yes")
    username = sanitize_username(request.args.get("username", "")) or "profile"

    if not image_url_allowed(target):
        return Response("Invalid image URL", status=400, mimetype="text/plain")

    try:
        upstream = requests.get(target, timeout=REQUEST_TIMEOUT, headers=HEADERS, stream=True)
    except requests.RequestException:
        return Response("Image fetch failed", status=502, mimetype="text/plain")

    if upstream.status_code != 200 or not image_url_allowed(upstream.url):
        upstream.close()
        return Response("Image unavailable", status=502, mimetype="text/plain")

    content_type = upstream.headers.get("Content-Type", "").split(";")[0].strip().lower()
    if not content_type.startswith("image/"):
        upstream.close()
        return Response("Not an image", status=502, mimetype="text/plain")

    def stream() -> Iterator[bytes]:
        total = 0
        try:
            for chunk in upstream.iter_content(8192):
                total += len(chunk)
                if total > MAX_IMAGE_BYTES:
                    break
                yield chunk
        finally:
            upstream.close()

    headers = {
        "Cache-Control": "public, max-age=3600",
        "X-Content-Type-Options": "nosniff",
    }
    if download:
        filename = f"i3look_{username}_hd.{image_extension(content_type)}"
        headers["Content-Disposition"] = f'attachment; filename="{filename}"'

    return Response(stream(), mimetype=content_type, headers=headers)


@app.route("/health")
def health():
    return jsonify({"ok": True, "service": APP_NAME, "cache_entries": len(CACHE)})


def find_free_port(start: int = PORT_START, attempts: int = PORT_ATTEMPTS) -> int:
    """Return the first bindable port, with an ephemeral-port last resort."""
    for port in range(start, start + attempts):
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind(("0.0.0.0", port))
            return port
        except OSError:
            continue
        finally:
            sock.close()

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("0.0.0.0", 0))
    port = sock.getsockname()[1]
    sock.close()
    print(" * Default port range busy; using an ephemeral port.")
    return port


if __name__ == "__main__":
    chosen_port = find_free_port()
    print(f" * {APP_NAME} ready -> http://127.0.0.1:{chosen_port}")
    print(f" * Network access -> http://0.0.0.0:{chosen_port}")
    app.run(host="0.0.0.0", port=chosen_port, debug=False, threaded=True)
