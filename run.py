import os
import sys
import json
import shutil
import logging
import argparse
from pathlib import Path
from datetime import datetime, timezone

from src.config import Config
from src.db import Database
from src.mega_manager import MegaManager
from src.drive_manager import DriveManager
from src.video_utils import inspect_video
from src.youtube_uploader import YouTubeUploader
from src.browser_studio_uploader import BrowserStudioUploader
from src.notifier import Notifier

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("pipeline")


def parse_args():
    parser = argparse.ArgumentParser(description="Automated Sequential YouTube Uploader (MEGA / Google Drive)")
    parser.add_argument("--config", default="config.yaml", help="Path to config.yaml")
    parser.add_argument("--slot", default=None, help="Slot name for scheduling (e.g. slot1, slot2)")
    parser.add_argument("--dry-run", action="store_true", help="Simulate without uploading to YouTube")
    parser.add_argument("--force-video", default=None, help="Force upload a specific video sequence (e.g. V1, V2)")
    parser.add_argument("--engine", default="auto", choices=["auto", "browser", "api"], help="Upload engine (auto, browser, api)")
    return parser.parse_args()


def main():
    args = parse_args()
    logger.info("=== Starting YouTube Video Automation Pipeline ===")

    try:
        config = Config(args.config)
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)

    db = Database(config.database_file)
    notifier = Notifier(config.discord_webhook_url)
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # 1. Guard against duplicate slot runs on the same day
    if args.slot and not args.dry_run:
        if db.slot_already_ran_today(today_str, args.slot):
            logger.info(f"Slot '{args.slot}' has already executed successfully today ({today_str}). Exiting.")
            sys.exit(0)

    # 2. Fetch and scan videos from Storage Source (MEGA or Google Drive)
    uploaded_keys = db.get_uploaded_keys()
    logger.info(f"Previously uploaded videos ({len(uploaded_keys)}): {sorted(list(uploaded_keys))}")

    target_video_path = None
    target_json_path = None
    target_thumb_path = None
    sequence_key = None
    sequence_num = None

    mega_manager = None
    if config.storage_source == "mega":
        logger.info(f"Storage source: MEGA ({config.mega_folder_url})")
        mega_manager = MegaManager(config.mega_folder_url, config.downloads_dir)
        target_forced_file = Path(config.downloads_dir) / f"{args.force_video}.mp4" if args.force_video else None
        if target_forced_file and target_forced_file.exists():
            logger.info(f"Target video {target_forced_file.name} is already present in downloads/. Skipping re-download.")
        else:
            try:
                mega_manager.download_folder_contents()
            except Exception as e:
                err_msg = f"Failed to download from MEGA: {e}"
                logger.error(err_msg)
                notifier.send_failure(None, "MEGA Download Error", str(e))
                db.record_run(today_str, args.slot, None, "failed", err_msg)
                sys.exit(1)

        items = mega_manager.scan_sequence_items()
        if args.force_video:
            seq_num_str = "".join(c for c in args.force_video if c.isdigit())
            seq_num = int(seq_num_str) if seq_num_str else 1
            target_item = items.get(seq_num)
        else:
            target_item = mega_manager.get_next_unposted_item(uploaded_keys)

        if not target_item:
            logger.info("🎉 No pending videos left to upload on MEGA! All caught up.")
            db.record_run(today_str, args.slot, None, "skipped", "No new videos available")
            sys.exit(0)

        sequence_key = target_item.sequence_key
        sequence_num = target_item.sequence_num
        target_video_path = target_item.video_path
        target_json_path = target_item.json_path
        target_thumb_path = target_item.thumbnail_path

    else:
        # Google Drive Source
        logger.info(f"Storage source: Google Drive ({config.gdrive_folder_id})")
        drive_manager = DriveManager(
            folder_id=config.gdrive_folder_id,
            service_account_file=config.gdrive_service_account_file,
            oauth_token_file=config.youtube_oauth_token_file
        )
        if args.force_video:
            seq_num_str = "".join(c for c in args.force_video if c.isdigit())
            seq_num = int(seq_num_str) if seq_num_str else 1
            target_item = drive_manager.scan_sequence_items().get(seq_num)
        else:
            target_item = drive_manager.get_next_unposted_item(uploaded_keys)

        if not target_item:
            logger.info("🎉 No pending videos left to upload on Google Drive! All caught up.")
            db.record_run(today_str, args.slot, None, "skipped", "No new videos available")
            sys.exit(0)

        sequence_key = target_item.sequence_key
        sequence_num = target_item.sequence_num
        target_video_path, metadata_from_drive, target_thumb_path = drive_manager.download_sequence_bundle(
            target_item,
            Path(config.downloads_dir)
        )

    logger.info(f"🎯 Target video selected: {sequence_key} ({target_video_path})")

    try:
        # 3. Inspect video properties & classify Short vs Long-form
        video_info = inspect_video(target_video_path)
        logger.info(f"Video inspection: {video_info}")

        if not video_info.has_audio:
            raise ValueError(f"Video {target_video_path.name} has no audio stream! Aborting upload.")

        # Determine video format
        is_short = video_info.is_short(config.shorts_max_seconds)
        video_type_str = "short" if is_short else "longform"
        logger.info(f"Video format classified as: {video_type_str.upper()}")

        # 4. Resolve Metadata (File, Catalog, or Auto-Generated)
        metadata = None
        if target_json_path and target_json_path.exists():
            logger.info(f"Loading metadata from individual JSON file: {target_json_path}")
            with open(target_json_path, "r", encoding="utf-8") as f:
                metadata = json.load(f)
        elif hasattr(target_item, "catalog_metadata") and target_item.catalog_metadata:
            cat = target_item.catalog_metadata
            raw_title = cat.get("title", f"Part {sequence_num}")
            desc = cat.get("description", "")
            
            # Format title nicely with #Shorts if it is a Short
            if is_short and not raw_title.lower().endswith("#shorts"):
                final_title = f"{raw_title} #Shorts"
            else:
                final_title = raw_title

            # Extract hashtags from description for YouTube tags
            hashtags = [w.strip("#.,!?").strip() for w in desc.split() if w.startswith("#") and len(w) > 1]
            tags = list(dict.fromkeys(hashtags + ["Shorts", "Motivation", "Respect", "Story", "Viral"]))

            logger.info(f"Using exact catalog metadata for {sequence_key}: '{final_title}'")
            metadata = {
                "title": final_title,
                "description": desc,
                "tags": tags[:15],
                "categoryId": config.default_category_id,
                "privacyStatus": config.default_privacy_status,
                "isShort": is_short,
                "madeForKids": False
            }
        elif config.auto_generate_metadata:
            logger.info(f"Auto-generating rich Sci-Fi metadata for {sequence_key} (Episode {sequence_num})...")
            if mega_manager:
                metadata = mega_manager.generate_auto_metadata(sequence_key, sequence_num, is_short)
            else:
                metadata = {
                    "title": f"Sci-Fi Cinematic Chronicles — Episode {sequence_num}",
                    "description": f"Episode {sequence_num} of the animated sci-fi series.\n\n#scifi #animation #cgi",
                    "tags": ["scifi", "animation", "cgi", "3d animation"],
                    "categoryId": config.default_category_id,
                    "privacyStatus": config.default_privacy_status,
                    "isShort": is_short,
                    "madeForKids": False
                }
        else:
            raise ValueError(f"No metadata found for {sequence_key} and auto-generation is disabled.")

        # 5. Resolve Custom Thumbnail
        if not is_short:
            # Long-form requires thumbnail
            if not target_thumb_path or not target_thumb_path.exists():
                if config.auto_generate_thumbnail and mega_manager:
                    auto_thumb_path = target_video_path.parent / f"{sequence_key}_auto_thumb.jpg"
                    target_thumb_path = mega_manager.extract_auto_thumbnail(target_video_path, auto_thumb_path)
                    logger.info(f"Extracted high-res auto thumbnail: {target_thumb_path}")
                else:
                    raise ValueError(f"Long-form video {sequence_key} requires a custom thumbnail!")
        else:
            if target_thumb_path and target_thumb_path.exists():
                logger.info(f"Custom thumbnail found for Short: {target_thumb_path.name}")
            else:
                logger.info("No custom thumbnail for Short. YouTube will generate default.")

        # 6. Dry Run Mode
        if args.dry_run:
            logger.info("--- [DRY RUN SUMMARY] ---")
            logger.info(f"Sequence Key: {sequence_key}")
            logger.info(f"Video File: {target_video_path.name} ({target_video_path.stat().st_size / (1024*1024):.2f} MB)")
            logger.info(f"Video Type: {video_type_str}")
            logger.info(f"Title: {metadata.get('title')}")
            logger.info(f"Description:\n{metadata.get('description')}")
            logger.info(f"Tags: {metadata.get('tags')}")
            logger.info(f"Thumbnail: {target_thumb_path.name if target_thumb_path else 'None'}")
            logger.info("--- Dry run completed successfully. No YouTube upload performed. ---")
            sys.exit(0)

        # 7. Upload to YouTube
        engine = args.engine.lower()
        has_cookies = bool(os.getenv("YOUTUBE_COOKIES")) or os.path.exists("tokens/youtube_cookies.json")

        if engine == "browser" or (engine == "auto" and has_cookies):
            logger.info("Using Browser Studio UI Engine (Playwright Headless + Proxy + Cookies) 🚀")
            try:
                uploader = BrowserStudioUploader(
                    cookies_file="tokens/youtube_cookies.json",
                    proxy_url=os.getenv("STUDIO_PROXY")
                )
            except Exception as e:
                logger.warning(f"Browser uploader initialization failed: {e}. Falling back to Official API...")
                uploader = YouTubeUploader(
                    client_secret_file=config.youtube_client_secret_file,
                    oauth_token_file=config.youtube_oauth_token_file
                )
        else:
            logger.info("Using Official YouTube Data API v3 Engine 🚀")
            uploader = YouTubeUploader(
                client_secret_file=config.youtube_client_secret_file,
                oauth_token_file=config.youtube_oauth_token_file
            )

        upload_result = uploader.upload_video(
            video_path=target_video_path,
            metadata=metadata,
            is_short=is_short,
            description_footer=config.description_footer
        )

        # Upload thumbnail if available
        has_thumb_uploaded = False
        if target_thumb_path and target_thumb_path.exists():
            has_thumb_uploaded = uploader.set_thumbnail(upload_result["id"], target_thumb_path)

        # 8. Update Database State
        db.record_upload(
            sequence_key=sequence_key,
            sequence_num=sequence_num,
            youtube_id=upload_result["id"],
            youtube_url=upload_result["url"],
            title=upload_result["title"],
            video_type=video_type_str,
            has_thumbnail=has_thumb_uploaded,
            status="uploaded"
        )

        db.record_run(
            run_date=today_str,
            slot=args.slot,
            sequence_key=sequence_key,
            status="success",
            message=f"Uploaded to {upload_result['url']}"
        )

        # 9. Send Success Notification
        notifier.send_success(
            sequence_key=sequence_key,
            title=upload_result["title"],
            youtube_url=upload_result["url"],
            video_type=video_type_str,
            has_custom_thumbnail=has_thumb_uploaded
        )

    except Exception as e:
        err_msg = f"Upload pipeline failed for {sequence_key}: {e}"
        logger.exception(err_msg)
        notifier.send_failure(sequence_key, str(e))
        db.record_run(today_str, args.slot, sequence_key, "failed", str(e))
        sys.exit(1)
    finally:
        # Explicitly clean up all downloaded videos and thumbnails from disk
        downloads_dir = Path(config.downloads_dir)
        if downloads_dir.exists():
            logger.info("🧹 Cleaning up temporary video and thumbnail files from disk...")
            for item in downloads_dir.iterdir():
                try:
                    if item.is_file():
                        item.unlink()
                    elif item.is_dir():
                        shutil.rmtree(item, ignore_errors=True)
                except Exception as cleanup_err:
                    logger.warning(f"Could not delete {item}: {cleanup_err}")
            logger.info("✨ Cleanup complete! Disk space is 100% clean.")


if __name__ == "__main__":
    main()
