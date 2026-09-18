#!/usr/bin/env python3
"""
ixBrowser Cookie Exporter for Playwright / GitHub Actions.
Finds YouTube Studio logged-in profiles in ixBrowser and exports cookies
in Playwright format (and base64 encoded for GitHub Secrets).
"""

import os
import sys
import json
import base64
import argparse
from pathlib import Path


def find_ixbrowser_dir():
    candidates = [
        os.path.expanduser("~/Library/Application Support/ixBrowser/Browser Data"),
        os.path.expanduser("~/AppData/Roaming/ixBrowser/Browser Data"),
    ]
    for c in candidates:
        if os.path.isdir(c):
            return c
    return None


def discover_profiles(browser_data_dir):
    profiles = []
    for entry in os.scandir(browser_data_dir):
        if not entry.is_dir():
            continue
        tab_file = os.path.join(entry.path, "fs_tabs.txt")
        cookie_file = os.path.join(entry.path, "fs_cookies.txt")
        if not os.path.exists(cookie_file):
            continue

        name = entry.name
        channel_id = ""
        profile_num = ""
        email = ""

        # Parse tabs if available
        if os.path.exists(tab_file):
            try:
                with open(tab_file, "r", errors="ignore") as f:
                    tabs = json.load(f)
                    for t in tabs:
                        url = t.get("url", "")
                        if "name=" in url:
                            for part in url.split("&"):
                                if part.startswith("name="):
                                    name = part.replace("name=", "")
                                elif part.startswith("id="):
                                    profile_num = part.replace("id=", "")
                        if "channel/" in url:
                            parts = url.split("channel/")
                            if len(parts) > 1:
                                channel_id = parts[1].split("/")[0].split("?")[0]
            except Exception:
                pass

        # Scan for email in credential files
        for f in os.listdir(entry.path):
            if len(f) == 32 and not os.path.isdir(os.path.join(entry.path, f)):
                try:
                    with open(os.path.join(entry.path, f), "r", errors="ignore") as cf:
                        content = cf.read(500)
                        if "accounts.google.com" in content:
                            for line in content.splitlines():
                                if "accounts.google.com" in line:
                                    parts = line.split(",")
                                    if len(parts) >= 3:
                                        email = parts[2]
                except Exception:
                    pass

        profiles.append({
            "dir_name": entry.name,
            "profile_path": entry.path,
            "cookie_file": cookie_file,
            "profile_num": profile_num,
            "name": name,
            "channel_id": channel_id,
            "email": email,
        })
    return profiles


def convert_cookies_for_playwright(ix_cookies_file):
    with open(ix_cookies_file, "r") as f:
        cookies = json.load(f)

    playwright_cookies = []
    # Filter for Google & YouTube domains
    target_domains = ("youtube.com", "google.com")
    
    ss_map = {"0": "None", "1": "Lax", "2": "Strict"}

    for c in cookies:
        domain = c.get("domain", "")
        if not any(td in domain for td in target_domains):
            continue

        ss_raw = str(c.get("same_site", "-1"))
        same_site = ss_map.get(ss_raw, "Lax")

        secure = bool(c.get("secure", False))
        if same_site == "None" and not secure:
            same_site = "Lax"

        pw_cookie = {
            "name": c["name"],
            "value": c["value"],
            "domain": domain,
            "path": c.get("path", "/"),
            "secure": secure,
            "httpOnly": bool(c.get("http_only", False)),
            "sameSite": same_site,
        }

        exp = c.get("expiration_time")
        if exp and int(exp) > 0:
            pw_cookie["expires"] = float(exp)

        playwright_cookies.append(pw_cookie)

    return playwright_cookies


def main():
    parser = argparse.ArgumentParser(description="Export ixBrowser YouTube cookies for Playwright/GitHub Actions")
    parser.add_argument("--list", action="store_true", help="List all detected ixBrowser profiles")
    parser.add_argument("--profile", type=str, help="Directory name or profile ID to export")
    parser.add_argument("--output", type=str, default="tokens/youtube_cookies.json", help="Output file path")
    args = parser.parse_args()

    browser_dir = find_ixbrowser_dir()
    if not browser_dir:
        print("❌ Could not locate ixBrowser Data directory.")
        sys.exit(1)

    profiles = discover_profiles(browser_dir)
    if not profiles:
        print("❌ No profiles found in ixBrowser.")
        sys.exit(1)

    if args.list:
        print("\n📋 Detected ixBrowser Profiles:")
        print("-" * 80)
        for idx, p in enumerate(profiles):
            info = f"#{idx+1} [ID: {p['profile_num'] or 'N/A'}] {p['name']}"
            if p["email"]:
                info += f" | Email: {p['email']}"
            if p["channel_id"]:
                info += f" | Channel: {p['channel_id']}"
            print(info)
            print(f"    Folder: {p['dir_name']}")
        print("-" * 80)
        return

    # Select target profile
    target = None
    if args.profile:
        for p in profiles:
            if args.profile in (p["dir_name"], p["profile_num"], p["email"], p["channel_id"]):
                target = p
                break
        if not target:
            print(f"❌ Profile '{args.profile}' not found.")
            sys.exit(1)
    else:
        # Default match: look for sciencefiction879 or channel with studio.youtube.com
        for p in profiles:
            if "sciencefiction879" in p["email"]:
                target = p
                break
        if not target:
            target = profiles[0]

    print(f"✅ Selected Profile: {target['name']} ({target['dir_name']})")
    if target["email"]:
        print(f"   Email: {target['email']}")
    if target["channel_id"]:
        print(f"   YouTube Channel: https://youtube.com/channel/{target['channel_id']}")

    # Convert cookies
    pw_cookies = convert_cookies_for_playwright(target["cookie_file"])
    print(f"✅ Extracted {len(pw_cookies)} Google/YouTube cookies for Playwright.")

    # Save to output file
    out_path = Path(args.output)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w") as f:
        json.dump(pw_cookies, f, indent=2)
    print(f"📁 Saved cookies to: {out_path}")

    # Generate Base64 string for GitHub Secret
    json_bytes = json.dumps(pw_cookies).encode("utf-8")
    b64_str = base64.b64encode(json_bytes).decode("utf-8")

    b64_file = out_path.with_suffix(".b64")
    with open(b64_file, "w") as f:
        f.write(b64_str)

    print("\n" + "=" * 80)
    print("🔑 GITHUB ACTIONS SECRET SETUP:")
    print("Go to your GitHub repo -> Settings -> Secrets and variables -> Actions")
    print("Add a new repository secret:")
    print("  Name:  YOUTUBE_COOKIES")
    print(f"  Value: (Contents of file: {b64_file})")
    print("=" * 80 + "\n")


if __name__ == "__main__":
    main()
