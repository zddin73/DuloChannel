#!/usr/bin/env python3
import sys, subprocess, re, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from xml.etree import ElementTree as ET

try:
    import requests
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "requests"])
    import requests

# Configuration
M3U_OUT  = "dulo.m3u"
EPG_OUT  = "dulo.xml"
API_URL  = "https://dulo.tv"
EPG_API  = "https://epg.pw{channel_id}"

UA  = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
REF = "https://dulo.tv"

def fetch_channels():
    print("Scraping dulo.tv directly...")
    session = requests.Session()
    session.headers.update({
        "User-Agent": UA, "Referer": REF, "Accept": "application/json",
        "Origin": "https://dulo.tv", "Sec-Fetch-Site": "same-origin"
    })
    try:
        # Load index page first to inherit normal tracking cookies
        session.get(REF, timeout=10)
        time.sleep(1)
        r = session.get(API_URL, timeout=15)
        if r.status_code == 200:
            data = r.json()
            return data.get("channels", data) if isinstance(data, dict) else data
    except Exception as e:
        print(f"Direct API link blocked or down: {e}")
    sys.exit("Could not fetch channel data array.")

def fetch_epg_xml(session, cid):
    try:
        r = session.get(EPG_API.format(channel_id=cid), timeout=12)
        return ET.fromstring(r.content) if r.status_code == 200 else None
    except: return None

def main():
    channels = fetch_channels()
    print(f"Successfully scraped {len(channels)} channels.")
    
    # 1. Build M3U Playlist with Kodi Header properties
    m3u_lines = ['#EXTM3U url-tvg="dulo.xml" x-tvg-url="dulo.xml"\n']
    tasks = {}
    
    for ch in channels:
        name = ch.get("name", "Unknown")
        cid  = ch.get("id", "Unknown")
        logo = ch.get("logo_url", "")
        grp  = ch.get("category", "General").title()
        url  = ch.get("source_url") or ch.get("url") or ""
        
        # Extract external EPG lookup ID if available
        epg_url = ch.get("epg_source_url", "")
        m = re.search(r"channel_id=(\d+)", epg_url) or re.search(r"/(\d+)\.html", epg_url)
        epg_id = m.group(1) if m else cid
        
        if not url: continue
        if epg_id and epg_id != "Unknown": tasks[epg_id] = name
            
        m3u_lines.append(
            f'#EXTINF:-1 tvg-id="{epg_id}" tvg-name="{name}" tvg-logo="{logo}" group-title="{grp}",{name}\n'
            f'#EXTVLCOPT:http-user-agent={UA}\n'
            f'#EXTVLCOPT:http-referrer={REF}\n'
            f'{url}|User-Agent={UA}&Referer={REF}\n'
        )
        
    with open(M3U_OUT, "w", encoding="utf-8") as f:
        f.write("".join(m3u_lines))
    print(f"  → Wrote local uncompressed M3U playlist file.")

    # 2. Build Threaded Uncompressed XML EPG
    session = requests.Session()
    session.headers.update({"User-Agent": UA, "Referer": "https://epg.pw"})
    tv = ET.Element("tv", {"generator-info-name": "DuloScraper"})
    seen, progs = set(), []
    
    print(f"Downloading EPG timelines concurrently for {len(tasks)} items...")
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = {executor.submit(fetch_epg_xml, session, cid): cid for cid in tasks.keys()}
        for f in as_completed(futures):
            root = f.result()
            if root is None: continue
            for chan in root.findall("channel"):
                if chan.get("id") not in seen:
                    seen.add(chan.get("id")); tv.append(chan)
            for prog in root.findall("programme"): progs.append(prog)
                
    for p in progs: tv.append(p)
    xml_bytes = b'<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(tv, encoding="unicode").encode('utf-8')
    
    with open(EPG_OUT, "wb") as f:
        f.write(xml_bytes)
    print("  → Wrote local uncompressed XML EPG timeline data.")

if __name__ == "__main__":
    main()
