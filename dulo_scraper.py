#!/usr/bin/env python3
"""
dulo-tv-epg — generate.py
Fetches live channel data from a stable upstream source, produces:
  - dulo.m3u       (M3U playlist with EPG header & custom Kodi headers)
  - dulo.xml       (merged XMLTV EPG, uncompressed)
"""

import sys
import subprocess

# Auto-install missing packages on GitHub Actions environment
try:
    import requests
except ImportError:
    print("Dependencies missing. Installing requests...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from xml.etree import ElementTree as ET

# ── Config ────────────────────────────────────────────────────────────────────
REPO        = "BuddyChewChew/dulo-tv-epg"
BRANCH      = "main"
BASE_RAW    = f"https://githubusercontent.com{REPO}/{BRANCH}"

# Output paths
M3U_OUT     = "dulo.m3u"
EPG_OUT     = "dulo.xml"

# Target Endpoint Fallback System (Bypasses Cloudflare block)
UPSTREAM_CHANNELS_JSON = f"{BASE_RAW}/channels.json" 
CHANNELS_API           = "https://dulo.tv"

EPG_URL      = f"{BASE_RAW}/dulo.xml"
EPG_API      = "https://epg.pw{channel_id}"
MAX_WORKERS  = 10  # Number of concurrent EPG downloads

# Humanized Browser Emulation Matrix
STREAM_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
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
    if ch.get("source_url"):
        return ch["source_url"]
    
    if ch.get("url"):
        return ch["url"]
        
    streams = ch.get("streams", [])
    if isinstance(streams, list) and len(streams) > 0:
        first_stream = streams[0]
        if isinstance(first_stream, dict):
            return first_stream.get("url", first_stream.get("source_url", ""))
        return str(first_stream)
        
    return ""


def fetch_channels() -> list[dict]:
    print("Fetching channel list...")
    session = requests.Session()
    session.headers.update({"User-Agent": STREAM_USER_AGENT})
    
    # Strategy 1: Attempt to read from the stable automated repository storage
    try:
        print(f"  → Attempting upstream storage retrieval from GitHub Raw...")
        r = session.get(UPSTREAM_CHANNELS_JSON, timeout=15)
        if r.status_code == 200:
            data = r.json()
            channels = data.get("channels", data) if isinstance(data, dict) else data
            print(f"  → Success! Parsed {len(channels)} channels from repository mirror.")
            return channels
    except Exception as e:
        print(f"  [info] Primary data source pipeline unavailable, attempting direct fetch fallback... ({e})")

    # Strategy 2: Fallback direct request
    try:
        print(f"  → Attempting direct fallback connection to {CHANNELS_API}...")
        r = session.get(CHANNELS_API, timeout=15)
        if r.status_code == 200:
            data = r.json()
            channels = data.get("channels", data) if isinstance(data, dict) else data
            print(f"  → Success! Parsed {len(channels)} channels directly.")
            return channels
    except Exception as e:
        print(f"  [error] All backend pathways failed to parse channels: {e}")
        sys.exit(1)
        
    print("  [error] Unable to acquire source array. Terminating engine context.")
    sys.exit(1)


def build_m3u(channels: list[dict]) -> str:
    lines = [f'#EXTM3U url-tvg="{EPG_URL}" x-tvg-url="{EPG_URL}"\n']
    for ch in channels:
        ch_id   = ch.get("id", "")
        name    = ch.get("name", "Unknown")
        logo    = ch.get("logo_url", "")
        group   = ch.get("category", "General").title()
        stream  = get_best_stream_url(ch)
        epg_cid = extract_epg_channel_id(ch.get("epg_source_url", "")) or ch_id

        if not stream:
            continue

        # Format optimized natively to bypass cloudflare streams on Kodi IPTV Simple Client
        lines.append(
            f'#EXTINF:-1 tvg-id="{epg_cid}" tvg-name="{name}" tvg-logo="{logo}" group-title="{group}",{name}\n'
            f'#EXTVLCOPT:http-user-agent={STREAM_USER_AGENT}\n'
            f'#EXTVLCOPT:http-referrer={STREAM_REFERER}\n'
            f'{stream}|User-Agent={STREAM_USER_AGENT}&Referer={STREAM_REFERER}\n'
        )
    return "".join(lines)


def fetch_epg_xml(session: requests.Session, channel_id: str) -> ET.Element | None:
    url = EPG_API.format(channel_id=channel_id)
    try:
        r = session.get(url, timeout=15)
        if r.status_code != 200:
            return None
        return ET.fromstring(r.content)
    except Exception:
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

    tasks = {}
    for ch in channels:
        ch_id = extract_epg_channel_id(ch.get("epg_source_url", ""))
        if ch_id:
            tasks[ch_id] = ch.get('name', ch_id)

    print(f"  → Spawning parallel downloads for {len(tasks)} EPG dependencies...")
    
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        future_to_id = {executor.submit(fetch_epg_xml, session, cid): (cid, name) for cid, name in tasks.items()}
        
        for future in as_completed(future_to_id):
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
    
    with open(EPG_OUT, "wb") as f:
        f.write(xml_bytes)
    print(f"  → wrote {EPG_OUT} ({len(xml_bytes):,} bytes uncompressed)")

    print(f"\nDone in {time.time() - start_time:.2f} seconds.")


if __name__ == "__main__":
    main()
