#!/usr/bin/env python3
import sys, subprocess, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from xml.etree import ElementTree as ET

try:
    import requests
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests
REPO = "BuddyChewChew/dulo-tv-epg"
BRANCH = "main"
BASE_RAW = f"https://githubusercontent.com{REPO}/{BRANCH}"

M3U_IN = f"{BASE_RAW}/dulo.m3u"
M3U_OUT = "dulo.m3u"
EPG_OUT = "dulo.xml"
EPG_URL = f"{BASE_RAW}/dulo.xml"
EPG_API = "https://epg.pw{channel_id}"

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
REF = "https://dulo.tv"
def extract_id(url):
    if not url: return None
    m = re.search(r"channel_id=(\d+)", url)
    return m.group(1) if m else None

def fetch_src_m3u():
    print("Downloading upstream dulo.m3u...")
    r = requests.get(M3U_IN, timeout=15)
    if r.status_code != 200:
        print("Failed to get playlist file."); sys.exit(1)
    return r.text

def parse_channels(m3u_text):
    channels = []
    current_meta = None
    for line in m3u_text.splitlines():
        line = line.strip()
        if line.startswith("#EXTINF:"):
            current_meta = line
        elif line and not line.startswith("#"):
            if current_meta:
                name = re.search(r',([^,]+)$', current_meta)
                name = name.group(1) if name else "Unknown"
                tvg_id = re.search(r'tvg-id="([^"]+)"', current_meta)
                tvg_id = tvg_id.group(1) if tvg_id else "Unknown"
                logo = re.search(r'tvg-logo="([^"]+)"', current_meta)
                logo = logo.group(1) if logo else ""
                grp = re.search(r'group-title="([^"]+)"', current_meta)
                grp = grp.group(1) if grp else "General"
                
                channels.append({
                    "name": name, "id": tvg_id, "logo": logo,
                    "group": grp, "url": line
                })
                current_meta = None
    return channels
def fetch_epg_xml(session, cid):
    try:
        r = session.get(EPG_API.format(channel_id=cid), timeout=15)
        return ET.fromstring(r.content) if r.status_code == 200 else None
    except: return None

def build_epg(channels):
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Referer": "https://epg.pw"})
    tv = ET.Element("tv", {"generator-info-name": "DuloScraper"})
    seen, progs = set(), []
    
    cids = [ch["id"] for ch in channels if ch["id"] != "Unknown"]
    print(f"Fetching EPG for {len(cids)} channels...")
    
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_epg_xml, session, cid): cid for cid in cids}
        for f in as_completed(futures):
            root = f.result()
            if root is None: continue
            for chan in root.findall("channel"):
                if chan.get("id") not in seen:
                    seen.add(chan.get("id")); tv.append(chan)
            for prog in root.findall("programme"):
                progs.append(prog)
                
    for p in progs: tv.append(p)
    return b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tv, encoding="unicode").encode('utf-8')
def build_m3u_file(channels):
    lines = [f'#EXTM3U url-tvg="{EPG_URL}" x-tvg-url="{EPG_URL}"\n']
    for ch in channels:
        lines.append(
            f'#EXTINF:-1 tvg-id="{ch["id"]}" tvg-name="{ch["name"]}" tvg-logo="{ch["logo"]}" group-title="{ch["group"]}",{ch["name"]}\n'
            f'#EXTVLCOPT:http-user-agent={UA}\n'
            f'#EXTVLCOPT:http-referrer={REF}\n'
            f'{ch["url"]}|User-Agent={UA}&Referer={REF}\n'
        )
    return "".join(lines)

def main():
    m3u_text = fetch_src_m3u()
    channels = parse_channels(m3u_text)
    print(f"Found {len(channels)} channels.")
    
    with open(M3U_OUT, "w", encoding="utf-8") as f:
        f.write(build_m3u_file(channels))
        
    xml_bytes = build_epg(channels)
    with open(EPG_OUT, "wb") as f:
        f.write(xml_bytes)
    print("Finished updating M3U and uncompressed XML EPG!")

if __name__ == "__main__":
    main()
