#!/usr/bin/env python3
"""
One-Click Local ixBrowser YouTube Uploader.
Connects directly to your local ixBrowser app, uses your active proxy and logged-in
YouTube session, and publishes videos in the background without moving your mouse cursor.
"""

import os
import sys
import time
import argparse
import subprocess
import logging
from pathlib import Path
from datetime import datetime, timezone

from src.config import Config
from src.db import Database
from src.mega_manager import MegaManager
from src.video_utils import inspect_video
from src.ixbrowser_studio_uploader import IXBrowserStudioUploader

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("ixbrowser_runner")


def ensure_ixbrowser_running():
    uploader = IXBrowserStudioUploader()
    if not uploader.is_ixbrowser_running():
        logger.info("ixBrowser is not running. Launching /Applications/ixBrowser.app...")
        subprocess.run(["open", "/Applications/ixBrowser.app"])
        for i in range(15):
            time.sleep(1)
            if uploader.is_ixbrowser_running():
                logger.info("ixBrowser is ready! ✅")
                return uploader
        logger.error("Could not connect to ixBrowser Local API. Please ensure ixBrowser is open.")
        sys.exit(1)
    return uploader


def main():
    parser = argparse.ArgumentParser(description="Local ixBrowser YouTube Studio Uploader")
    parser.add_argument("--list", action="store_true", help="List all ixBrowser profiles")
    parser.add_argument("--profile-id", type=int, default=None, help="ixBrowser Profile ID (e.g. 24, 25)")
    parser.add_argument("--force-video", type=str, default=None, help="Force upload specific video (e.g. V1, V2)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without uploading")
    args = parser.parse_args()

    uploader = ensure_ixbrowser_running()

    if args.list:
        profiles = uploader.list_profiles()
        print("\n📋 Available ixBrowser Profiles:")
        print("-" * 60)
        if profiles and isinstance(profiles, list):
            for p in profiles:
                print(f"Profile ID: {p.get('profile_id')} | Name: {p.get('name')} | Proxy: {p.get('proxy_type')}")
        else:
            print("No profiles returned or API format variation:", profiles)
        print("-" * 60)
        return

    config = Config("config.yaml")
    db = Database(config.database_file)
    uploaded_keys = db.get_uploaded_keys()

    # Find next video
    mega_manager = MegaManager(config.mega_folder_url, config.downloads_dir)
    items = mega_manager.scan_sequence_items()

    if args.force_video:
        seq_num = int("".join(c for c in args.force_video if c.isdigit()))
        target_item = items.get(seq_num)
    else:
        target_item = mega_manager.get_next_unposted_item(uploaded_keys)

    if not target_item:
        logger.info("🎉 No pending videos left to upload! All caught up.")
        return

    logger.info(f"Target Video: {target_item.sequence_key} ({target_item.video_path.name})")

    video_info = inspect_video(target_item.video_path)
    is_short = video_info.is_vertical or video_info.duration <= config.shorts_max_seconds

    metadata = mega_manager.get_item_metadata(
        target_item=target_item,
        is_short=is_short,
        auto_generate=True,
        channel_name=config.channel_name
    )

    if args.dry_run:
        logger.info("--- [DRY RUN] Everything is ready to upload via ixBrowser! ---")
        logger.info(f"Title: {metadata['title']}")
        logger.info(f"Type: {'Short' if is_short else 'Longform'}")
        return

    if not args.profile_id:
        logger.error("Please provide --profile-id <ID> (Run with --list to see your profile IDs)")
        sys.exit(1)

    result = uploader.upload_video(
        video_path=target_item.video_path,
        metadata=metadata,
        profile_id=args.profile_id,
        is_short=is_short,
        description_footer=config.description_footer
    )

    db.record_upload(
        sequence_key=target_item.sequence_key,
        sequence_num=target_item.sequence_num,
        youtube_id=result["id"],
        youtube_url=result["url"],
        title=result["title"],
        video_type="short" if is_short else "longform",
        has_thumbnail=False,
        status="uploaded"
    )
    logger.info(f"Upload completed and recorded: {result['url']}")


if __name__ == "__main__":
    main()
