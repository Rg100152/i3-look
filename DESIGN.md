# Software Design Document (SDD)

## i3-look — Public Profile OSINT & Analytics Web Portal

**Version:** 1.0.0
**Author:** Raj Gautam (Open Source Lover)
**Repository:** https://github.com/Rg100152/i3-look
**License:** MIT License, Copyright 2026 Raj Gautam

---

## 1. Introduction

### 1.1 Purpose

`i3-look` is an open-source, lightweight Flask web application for inspecting
publicly available Instagram profile metadata. It provides a distraction-free
single-page A-to-Z dashboard: HD avatar (served through a local image proxy),
identity and status badges, core metrics with follower/following ratio, a full
metadata table, public emails / links / @mentions / #hashtags extracted from the
bio, quick OSINT jumps, JSON report export, and a profile-completeness score.

It is optimised for Linux desktops with limited RAM (Pop!_OS / Ubuntu, 4 GB) and
runs with no background workers, no database, and no animations.

### 1.2 Scope and Legal Compliance

- **Authorised scope:** public profile attributes only — username, display name,
  avatar URL, public bio text, follower/following/post counts, privacy and
  verified signals, public URLs/emails/@mentions/#hashtags found in that bio,
  and the profile URL.
- **Prohibited:** bypassing authentication, reading private accounts, scraping
  login-walled content, storing credentials, or evading platform rate limits.
- **Official API mode:** when `IG_ACCESS_TOKEN` and `IG_BUSINESS_ID` are set, the
  app uses Meta's official `business_discovery` endpoint as the primary source.
- **Public fallback:** otherwise the app reads public OpenGraph metadata and
  JSON-LD from a public profile URL with a strict 8-second timeout.
- The user is responsible for complying with Instagram/Meta Terms of Service and
  applicable law. This tool is for awareness, auditing, and education only.

---

## 2. System Architecture

### 2.1 High-Level Architecture

Stateless client-server monolith with a bounded in-memory TTL cache:

```
+-------------------+   HTTP GET / POST   +---------------------------+
|  Web Browser      | ------------------> |  Flask server (app.py)    |
|  index.html       | <------------------ |  first free port: 5000+   |
|  inline CSS + JS  |  JSON + image bytes +---------------------------+
+-------------------+                              |
        |                                          |-- CACHE (TTL 900 s, max 200 entries)
        |-- localStorage (last 5 usernames)        |-- /proxy-image -> Instagram CDN
        |-- JSON report export (client-side)       |-- Primary: Meta Graph API (optional)
                                                   |-- Fallback: public OpenGraph / JSON-LD
```

### 2.2 Technology Stack

| Layer | Technology |
| --- | --- |
| Backend | Python 3.8+, Flask 3.x, `requests`, `re`, `html.unescape`, `json` |
| Frontend | Semantic HTML5, inline CSS3, vanilla ES6 JavaScript |
| Storage | In-memory Python dict (`CACHE`) + browser `localStorage` |
| Database | None (zero SQL overhead) |
| Target | Linux (Pop!_OS / Ubuntu), 4 GB RAM |

---

## 3. Directory Structure

```
i3-look/
├── app.py                 # Flask server, validation, cache, data sources
├── requirements.txt       # flask, requests
├── DESIGN.md              # this document
├── LICENSE                # MIT License (2026 Raj Gautam)
├── README.md              # setup, usage, disclaimer, credit
└── templates/
    └── index.html         # minimal dark UI with inline CSS and JS
```

---

## 4. Visual Identity and UI Design

### 4.1 Colour Palette

| Role | Hex |
| --- | --- |
| Background | `#121212` |
| Container card | `#1e1e1e` |
| Borders | `#2e2e2e` |
| Accent (buttons, links) | `#00a8e8` |
| Primary text | `#f1f5f9` |
| Muted text | `#9aa4b2` |
| Success / Public | `#10b981` |
| Error / warning | `#ff4757` |

### 4.2 UI Components

1. **Header** — plain `i3-look` title and ethical-mode tagline. No logo image or
   heavy graphics, so the page renders instantly.
2. **Search console** — single centred input plus one lookup button. Client-side
   regex validation mirrors the server: `^[A-Za-z0-9._]{1,30}$`.
3. **Results card** — HD avatar (through `/proxy-image`) with a download button,
   privacy / verified / ratio badges, a 3-column Followers / Following / Posts
   grid, an A-to-Z metadata table (name, handle, status, bio, emails, links,
   mentions, hashtags, photo URL, completeness), quick OSINT links, and an
   Export Report button. A footer line reports source, cache state and warnings.
4. **Recent lookups** — last five usernames kept in `localStorage`, rendered as
   clickable buttons for one-click re-analysis.
5. **Footer** — ethical OSINT disclaimer, author credit, and licence.

No CSS frameworks, no animations, no external fonts or assets.

---

## 5. Backend Processing and Caching

### 5.1 Input Validation

- Strip whitespace and any leading `@` characters.
- Reject anything not matching `^[A-Za-z0-9._]{1,30}$` before any outbound request.
- This also prevents SSRF: the only network destinations are constructed from
  the validated username against fixed `instagram.com` / `graph.facebook.com` hosts.

### 5.2 Lookup Pipeline

1. **Cache lookup** — `CACHE[username]`; fresh for `900` seconds (15 min).
   Expired entries are removed lazily; the cache is capped at 200 entries.
2. **Four layers are combined** — normal public HTML / JSON-LD (weakest), the
   public web view (`?__a=1`, simple User-Agent) which still returns exact
   `og:description` counts, the public `web_profile_info` endpoint with
   `X-IG-App-ID` (no login, no credentials), and the official Graph API
   (authoritative) when configured. Missing values stay empty; fabricated or
   guessed counts are never produced. The verified badge reports `Unknown`
   unless a layer actually exposes that signal.
3. **Official API mode (optional)** — if `IG_ACCESS_TOKEN` and `IG_BUSINESS_ID`
   are present, request:
   `GET https://graph.facebook.com/{version}/{ig_business_id}?fields=business_discovery.username({target}){username,name,profile_picture_url,biography,followers_count,follows_count,media_count}&access_token=...`
4. **Public fallback** — `GET https://www.instagram.com/{username}/` with an
   8-second timeout; parse `og:title`, `og:description`, `og:image`, optional
   JSON-LD `Person` data, and unescape HTML entities with `html.unescape`.
   Counts come from the description (`1,234 followers`, `12.5K followers`,
   `89 posts`) and, if absent, from embedded JSON in the HTML. Bio text is
   scanned for public emails, URLs, `@mentions` and `#hashtags`.
5. **Derived intelligence** — follower/following ratio, privacy (`Public` /
   `Private` / `Unknown`), verified badge detection, HD avatar URL, local
   proxy URL, and quick OSINT links (Instagram, Google dork, WayBack Machine).
6. **Status mapping** — `404` → `User Not Found`; `429` → `Rate Limited`;
   other 4xx/5xx → `Unavailable`; private markers → `Private`; login wall with
   no metadata → `Private Account / Login Required`; metadata present → `Public`.
7. **Completeness score** — weighted presence of name (25), avatar (20), bio
   (20), followers (15), following (10), posts (10) = 0–100.

### 5.3 HTTP API

| Method | Path | Body / query | Result |
| --- | --- | --- | --- |
| GET | `/` | — | Dashboard HTML |
| POST | `/api/lookup` | `{"username": "name"}` | JSON result object |
| GET | `/api/lookup?username=name` | — | Same JSON result (convenience) |
| GET | `/proxy-image?url=...&username=...` | CDN URL | Streamed image bytes |
| GET | `/proxy-image?...&download=1` | CDN URL | Image with `Content-Disposition: attachment` |
| GET | `/health` | — | Service status and cache size |

Example response:

```json
{
  "username": "example",
  "full_name": "Example Account",
  "followers": 1234,
  "following": 567,
  "posts": 89,
  "status": "Public",
  "privacy": "Public",
  "verified": false,
  "bio": "Public bio text hello@example.com #opensource",
  "avatar": "https://scontent-...cdninstagram.com/.../s1080x1080/...",
  "avatar_hd": "https://scontent-...cdninstagram.com/.../s1080x1080/...",
  "avatar_proxy": "/proxy-image?url=...&username=example",
  "emails": ["hello@example.com"],
  "links": [],
  "mentions": [],
  "hashtags": ["#opensource"],
  "ratio": "2.17:1",
  "ratio_value": 2.17,
  "profile_url": "https://www.instagram.com/example/",
  "osint_links": {"instagram": "...", "google": "...", "wayback": "..."},
  "completeness": 100,
  "source": "Meta Graph API + Public Metadata",
  "cached": false,
  "error": ""
}
```

Invalid usernames return HTTP 400 with `{"status": "Invalid Input", "error": "..."}`.

---

## 6. Non-Functional Requirements

- **Resource footprint:** Flask + requests only; target under ~45 MB RSS on Linux.
  No workers, no database, no CSS animation, bounded cache.
- **Security:** Jinja2 auto-escaping for templates; DOM `textContent` for dynamic
  values in JavaScript; strict regex and fixed hosts to prevent SSRF; the image
  proxy only accepts HTTPS URLs on Instagram / Meta CDN hosts and caps streams
  at 10 MB; no credentials stored; no private-content scraping.
- **Reliability:** 8-second network timeout, graceful 404/429/login handling,
  15-minute cache to reduce repeated requests.
- **Auto port fallback:** startup probes 5000–5010 and prints the exact
  `http://127.0.0.1:<port>` URL, so a busy port never crashes the app.
- **Portability:** Python 3.8+; runs directly with `python3 app.py`.
- **Accuracy note:** Instagram's public HTML does not always expose exact
  follower/following/post counts without a session. When counts are absent the
  UI shows `—`. For authoritative, rate-limit-safe metrics, configure the
  official Meta Graph API mode.

---

## 7. Licence

MIT License — Copyright (c) 2026 Raj Gautam. See `LICENSE`.
