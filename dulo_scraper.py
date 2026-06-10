#!/usr/bin/env python3
"""
dulo-tv-epg — generate.py
Fetches live channel data from dulo.tv, produces:
  - dulo.m3u       (M3U playlist with EPG header)
  - dulo.xml       (merged XMLTV EPG, uncompressed)

EPG data sourced from epg.pw per-channel XML API.
"""

import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from xml.etree import ElementTree as ET

import cloudscraper
import requests

# ── Config ────────────────────────────────────────────────────────────────────
# REPO        = "BuddyChewChew/dulo-tv-epg"
# BRANCH      = "main"
# BASE_RAW    = f"https://raw.githubusercontent.com/{REPO}/{BRANCH}"
# EPG_URL     = f"{BASE_RAW}/dulo.xml"  # Changed from .xml.gz to .xml
M3U_OUT     = "dulo.m3u"
EPG_OUT     = "dulo.xml"     # Changed from .xml.gz to .xml

CHANNELS_API = "https://dulo.tv/api/live-tv/channels"
EPG_API      = "https://epg.pw/api/epg.xml?channel_id={channel_id}"

MAX_WORKERS  = 10  # Number of concurrent EPG downloads

# Stream headers required to prevent 403 Forbidden errors
STREAM_USER_AGENT = "otg/1.5.1 (AppleTv Apple TV 4; tvOS16.0; appletv.client) libcurl/7.58.0 OpenSSL/1.0.2o zlib/1.2.11 clib/1.8.56"
STREAM_REFERER    = "https://dulo.tv/"

EPG_HEADERS = {
    "User-Agent": STREAM_USER_AGENT,
    "Accept": "application/xml, text/xml, */*",
    "Referer": "https://epg.pw",
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


def get_best_stream_url(ch: dict) -> str:
    """Extracts the operational stream URL by trying multiple likely keys."""
    # 1. Check primary source_url
    if ch.get("source_url"):
        return ch["source_url"]
    
    # 2. Check fallback url key
    if ch.get("url"):
        return ch["url"]
        
    # 3. Check for nested stream structures if API formatting varies
    streams = ch.get("streams", [])
    if isinstance(streams, list) and len(streams) > 0:
        first_stream = streams[0]
        if isinstance(first_stream, dict):
            return first_stream.get("url", first_stream.get("source_url", ""))
        return str(first_stream)
        
    return ""


def fetch_channels() -> list[dict]:
    print("Fetching channel list from dulo.tv …")
    scraper = cloudscraper.create_scraper(
        browser={"browser": "chrome", "platform": "windows", "mobile": False}
    )
    try:
        r = scraper.get(CHANNELS_API, timeout=30)
        if r.status_code != 200:
            print(f"  [error] HTTP {r.status_code} from dulo.tv")
            print(f"  Response: {r.text[:300]}")
            sys.exit(1)
        data = r.json()
    except Exception as e:
        print(f"  [error] Failed to fetch or parse JSON from dulo.tv: {e}")
        sys.exit(1)
        
    channels = data.get("channels", data) if isinstance(data, dict) else data
    print(f"  → {len(channels)} channels found")
    return channels


def build_m3u(channels: list[dict]) -> str:
    lines = [f'#EXTM3U\n']
    for ch in channels:
        ch_id   = ch.get("id", "")
        name    = ch.get("name", "Unknown")
        logo    = ch.get("logo_url", "")
        group   = ch.get("category", "General").title()
        stream  = get_best_stream_url(ch)
        epg_cid = extract_epg_channel_id(ch.get("epg_source_url", "")) or ch_id

        if not stream:
            continue

        # Injects user-agent and referer metadata properties into the M3U structure
        lines.append(
            f'#EXTINF:-1 tvg-id="{epg_cid}" tvg-name="{name}" tvg-logo="{logo}" group-title="{group}",{name}\n'
            f'#EXTVLCOPT:http-user-agent={STREAM_USER_AGENT}\n'
            f'#EXTVLCOPT:http-referrer={STREAM_REFERER}\n'
            f'{stream}\n'
        )
    return "".join(lines)


def fetch_epg_xml(session: requests.Session, channel_id: str) -> ET.Element | None:
    url = EPG_API.format(channel_id=channel_id)
    try:
        r = session.get(url, timeout=15)
        if r.status_code != 200:
            return None
        # Use fromstring directly, handle potential decoding/malformed quirks safely
        return ET.fromstring(r.content)
    except Exception:
        return None


def build_epg(channels: list[dict]) -> bytes:
    session = requests.Session()
    session.headers.update(EPG_HEADERS)

    tv = ET.Element("tv")
    tv.set("generator-info-name", "Dulo Channels Scraper")

    seen_channels: set[str] = set()
    programme_elements: list[ET.Element] = []

    # Filter channels down to valid targets needing external EPG lookups
    tasks = {}
    for ch in channels:
        ch_id = extract_epg_channel_id(ch.get("epg_source_url", ""))
        if ch_id:
            tasks[ch_id] = ch.get('name', ch_id)

    print(f"  → Spawning parallel downloads for {len(tasks)} EPG dependencies...")
    
    # Run multi-threaded downloads to optimize script speed drastically
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_id = {executor.submit(fetch_epg_xml, session, cid): (cid, name) for cid, name in tasks.items()}
        
        for idx, future in enumerate(as_completed(future_to_id), 1):
            cid, name = future_to_id[future]
            try:
                root = future.result()
                if root is None:
                    continue
                
                for chan_el in root.findall("channel"):
                    c_id = chan_el.get("id", "")
                    if c_id and c_id not in seen_channels:
                        seen_channels.add(c_id)
                        tv.append(chan_el)

                for prog_el in root.findall("programme"):
                    programme_elements.append(prog_el)
            except Exception as e:
                print(f"    [warn] Failed processing EPG for {name}: {e}")

    for prog_el in programme_elements:
        tv.append(prog_el)

    # Automatically add indentation and line breaks to the XML tree structure
    ET.indent(tv, space="  ", level=0)

    # Convert to string and explicitly insert a newline after the root element opening tag
    xml_str = ET.tostring(tv, encoding="unicode")
    target_tag = '<tv generator-info-name="Dulo Channels Scraper">'
    xml_str = xml_str.replace(target_tag, target_tag + "\n")

    xml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tv, encoding="unicode").encode('utf-8')
    return xml_bytes


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    start_time = time.time()
    channels = fetch_channels()

    print("\nBuilding M3U playlist …")
    m3u_content = build_m3u(channels)
    with open(M3U_OUT, "w", encoding="utf-8") as f:
        f.write(m3u_content)
    print(f"  → wrote {M3U_OUT} ({len(m3u_content):,} bytes)")

    print("\nFetching EPG data from epg.pw …")
    xml_bytes = build_epg(channels)
    
    # Fixed: Changed from gzip.open to standard open for an uncompressed file output
    with open(EPG_OUT, "wb") as f:
        f.write(xml_bytes)
    print(f"  → wrote {EPG_OUT} ({len(xml_bytes):,} bytes uncompressed)")

    print(f"\nDone in {time.time() - start_time:.2f} seconds.")


if __name__ == "__main__":
    main()
