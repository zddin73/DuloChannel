#!/usr/bin/env python3
"""
dulo-tv-epg — generate.py
Fetches live channel data from dulo.tv, produces:
  - dulo.m3u       (M3U playlist with EPG header)
  - dulo.xml.gz    (merged XMLTV EPG, gzip-compressed)

EPG data sourced from epg.pw per-channel XML API.
Run every 4 hours via GitHub Actions to handle tokenised stream URLs.
"""

import gzip
import re
import sys
import time
from xml.etree import ElementTree as ET

import cloudscraper
import requests

# ── Config ────────────────────────────────────────────────────────────────────
REPO        = "BuddyChewChew/dulo-tv-epg"
BRANCH      = "main"
BASE_RAW    = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
EPG_URL     = f"{BASE_RAW}/dulo.xml.gz"
M3U_OUT     = "dulo.m3u"
EPG_OUT     = "dulo.xml.gz"

CHANNELS_API = "https://dulo.tv/api/live-tv/channels"
EPG_API      = "https://epg.pw/api/epg.xml?channel_id={channel_id}"

EPG_FETCH_DELAY = 0.5

EPG_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36"
    ),
    "Accept": "application/xml, text/xml, */*",
    "Referer": "https://epg.pw/",
}

# ── Helpers ───────────────────────────────────────────────────────────────────

def extract_epg_channel_id(epg_source_url: str) -> str | None:
    if not epg_source_url:
        return None
    m = re.search(r"channel_id=(\d+)", epg_source_url)
    if m:
        return m.group(1)
    m = re.search(r"/(\d+)\.html", epg_source_url)
    if m:
        return m.group(1)
    return None


def fetch_channels() -> list[dict]:
    print("Fetching channel list from dulo.tv …")
    # cloudscraper solves Cloudflare JS/cookie challenges automatically
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    r = scraper.get(CHANNELS_API, timeout=30)
    if r.status_code != 200:
        print(f"  [error] HTTP {r.status_code} from dulo.tv")
        print(f"  Response: {r.text[:300]}")
        sys.exit(1)
    data = r.json()
    channels = data.get("channels", data) if isinstance(data, dict) else data
    print(f"  → {len(channels)} channels")
    return channels


def build_m3u(channels: list[dict]) -> str:
    lines = [f'#EXTM3U url-tvg="{EPG_URL}" x-tvg-url="{EPG_URL}"\n']
    for ch in channels:
        ch_id   = ch.get("id", "")
        name    = ch.get("name", "Unknown")
        logo    = ch.get("logo_url", "")
        group   = ch.get("category", "General").title()
        stream  = ch.get("source_url", "")
        epg_cid = extract_epg_channel_id(ch.get("epg_source_url", "")) or ch_id

        if not stream:
            continue

        lines.append(
            f'#EXTINF:-1 tvg-id="{epg_cid}" tvg-name="{name}" '
            f'tvg-logo="{logo}" group-title="{group}",{name}\n'
            f'{stream}\n'
        )
    return "".join(lines)


def fetch_epg_xml(session: requests.Session, channel_id: str) -> ET.Element | None:
    url = EPG_API.format(channel_id=channel_id)
    try:
        r = session.get(url, timeout=20)
        if r.status_code != 200:
            return None
        root = ET.fromstring(r.content)
        return root
    except Exception as e:
        print(f"    [warn] EPG fetch failed for channel_id={channel_id}: {e}")
        return None


def build_epg(channels: list[dict]) -> bytes:
    session = requests.Session()
    session.headers.update(EPG_HEADERS)

    tv = ET.Element("tv", attrib={
        "source-info-name": "epg.pw",
        "generator-info-name": f"github.com/{REPO}",
    })

    seen_channels: set[str] = set()
    programme_elements: list[ET.Element] = []

    total = len(channels)
    for i, ch in enumerate(channels, 1):
        ch_id = extract_epg_channel_id(ch.get("epg_source_url", ""))
        if not ch_id:
            continue

        print(f"  [{i}/{total}] EPG for {ch.get('name', ch_id)} (id={ch_id})")
        root = fetch_epg_xml(session, ch_id)
        if root is None:
            time.sleep(EPG_FETCH_DELAY)
            continue

        for chan_el in root.findall("channel"):
            cid = chan_el.get("id", "")
            if cid and cid not in seen_channels:
                seen_channels.add(cid)
                tv.append(chan_el)

        for prog_el in root.findall("programme"):
            programme_elements.append(prog_el)

        time.sleep(EPG_FETCH_DELAY)

    for prog_el in programme_elements:
        tv.append(prog_el)

    xml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tv, encoding="unicode").encode()
    return xml_bytes


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    channels = fetch_channels()

    print("\nBuilding M3U playlist …")
    m3u_content = build_m3u(channels)
    with open(M3U_OUT, "w", encoding="utf-8") as f:
        f.write(m3u_content)
    print(f"  → wrote {M3U_OUT} ({len(m3u_content):,} bytes)")

    print("\nFetching EPG data from epg.pw …")
    xml_bytes = build_epg(channels)
    with gzip.open(EPG_OUT, "wb") as f:
        f.write(xml_bytes)
    print(f"  → wrote {EPG_OUT} ({len(xml_bytes):,} bytes uncompressed)")

    print("\nDone.")


if __name__ == "__main__":
    main()
