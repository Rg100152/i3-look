# i3-look

**Public Profile OSINT & Analytics Web Portal** — a simple, basic, zero-bloat
Flask app for checking publicly available Instagram profile metadata. Built for
Linux (Pop!_OS / Ubuntu, 4 GB RAM) with no database, no frameworks, and no
animations.

```
 _     ___ ___  _     ___    _  _   _   ___  ___
| |   |_ _/ _ \| |   / _ \  | \| | /_\ | _ \/ __|
| |__  | | (_) | |__| (_) | | .` |/ _ \|   /\__ \
|____||___\___/|____|\___/  |_|\_/_/ \_\_|_\|___/
```

## Ethical use disclaimer

This tool reads **only public metadata**. It does not bypass authentication,
does not access private accounts, does not scrape login-walled pages, and does
not store credentials. Use it responsibly and comply with Meta's Terms of
Service and the laws of your jurisdiction. The authors are not responsible for
misuse. When counts are not present in public HTML they are displayed as `—`;
for authoritative metrics, configure the official Meta Graph API below.

## Features

- Clean, basic dark UI (single search box + A-to-Z structured results)
- Strict username validation: `^[A-Za-z0-9._]{1,30}$`
- 15-minute bounded in-memory cache (max 200 entries) to reduce requests
- HD avatar proxy (`/proxy-image`) so profile pictures always render, plus
  **Download HD Photo (DP)** button
- Four data layers: official Meta Graph API (optional), public `web_profile_info`,
  the public web view (`?__a=1`) that still exposes exact OG counts, and normal
  OpenGraph / JSON-LD parsing — all with HTML entity unescaping. No numbers are
  ever guessed. Verified badge shows `Unknown` when no public signal exists.
- Avatar is proxied from the original signed CDN URL (resizing it breaks the
  signature, so resolution depends on what Instagram serves).
- Clear "Lookup notice" with quick OSINT links when Instagram hides metrics
- Privacy / verified badges, core metrics with follower-to-following ratio
- Public emails, links, `@mentions` and `#hashtags` extracted from the bio
- Quick OSINT links: Instagram profile, Google dork, WayBack Machine
- Client-side **Export Report (.json)** and last-5 `localStorage` history
- Clear statuses: `Public`, `Private`, `User Not Found`,
  `Private Account / Login Required`, `Rate Limited`, `Unavailable`
- Auto port fallback: 5000, then 5001 … 5010; the exact URL is printed
- Endpoints: `/api/lookup`, `/proxy-image`, `/health`

## Installation

```bash
cd i3-look
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python3 app.py
```

Open the exact URL printed in the terminal (normally
http://127.0.0.1:5000). If port 5000 is already used by another program,
`app.py` automatically moves to 5001 (or the next free port up to 5010).

If you prefer to install system-wide, `pip install -r requirements.txt` works
too (use a virtual environment if your distro enforces PEP 668).

## Optional: official Meta Graph API mode

Create a Meta Developer app, enable the Instagram Graph API, link a
professional (Business/Creator) Instagram account, and obtain a long-lived
access token plus the linked Instagram Business Account ID. Then:

```bash
export IG_ACCESS_TOKEN="your-long-lived-token"
export IG_BUSINESS_ID="your-ig-business-account-id"
export IG_GRAPH_VERSION="v19.0"   # optional, defaults to v19.0
python3 app.py
```

With both variables set, `i3-look` uses `business_discovery` as the primary
source and falls back to public metadata only if the API call fails. Credentials
are read from the environment at startup and are never written to disk.

## Usage

1. Enter a public Instagram username (with or without a leading `@`).
2. Click **Look up**. The card shows the HD avatar, privacy / verified / ratio
   badges, followers / following / posts, the A-to-Z metadata table, extracted
   entities, and quick OSINT links.
3. Use **Download HD Photo (DP)** to save the avatar or **Export Report (.json)**
   to save the full report locally.
4. Click any name under **Recent lookups** to check it again.

You can also query the API directly:

```bash
curl -X POST http://127.0.0.1:5000/api/lookup \
  -H 'Content-Type: application/json' \
  -d '{"username":"example"}'
```

## Project files

```
i3-look/
├── app.py
├── requirements.txt
├── DESIGN.md
├── LICENSE
├── README.md
└── templates/
    └── index.html
```

Full architecture and design details are in [`DESIGN.md`](DESIGN.md).

## Credits

Built by **Raj Gautam (Open Source Lover)** —
https://github.com/Rg100152/i3-look

Licensed under the MIT License, Copyright (c) 2026 Raj Gautam.
