import json
import logging
import requests
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

class Notifier:
    def __init__(self, webhook_url: Optional[str] = None):
        self.webhook_url = webhook_url

    def send_success(
        self,
        sequence_key: str,
        title: str,
        youtube_url: str,
        video_type: str,
        has_custom_thumbnail: bool
    ):
        if not self.webhook_url:
            logger.info("No Discord webhook configured. Skipping notification.")
            return

        payload = {
            "embeds": [
                {
                    "title": f"🎬 YouTube Upload Success: {sequence_key}",
                    "description": f"**Title:** {title}\n**Link:** [Watch on YouTube]({youtube_url})",
                    "color": 5814783,  # Green / YouTube Red mix (Hex #58B9FF / Greenish)
                    "fields": [
                        {"name": "Sequence", "value": sequence_key, "inline": True},
                        {"name": "Format", "value": "Short 📱" if video_type == "short" else "Long-Form 📺", "inline": True},
                        {"name": "Thumbnail", "value": "✅ Custom Uploaded" if has_custom_thumbnail else "ℹ️ Default YouTube", "inline": True},
                        {"name": "YouTube URL", "value": youtube_url, "inline": False}
                    ],
                    "footer": {"text": "YouTube Google Drive Automation"}
                }
            ]
        }

        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            resp.raise_for_status()
            logger.info("Success notification sent to Discord.")
        except Exception as e:
            logger.error(f"Failed to send Discord notification: {e}")

    def send_failure(
        self,
        sequence_key: Optional[str],
        error_message: str,
        details: Optional[str] = None
    ):
        if not self.webhook_url:
            logger.info("No Discord webhook configured. Skipping failure notification.")
            return

        payload = {
            "embeds": [
                {
                    "title": f"❌ YouTube Upload Failed: {sequence_key or 'Pipeline'}",
                    "description": f"**Error:** {error_message}",
                    "color": 15158332,  # Red (Hex #E74C3C)
                    "fields": [
                        {"name": "Sequence", "value": sequence_key or "N/A", "inline": True},
                        {"name": "Details", "value": (details or "Check GitHub Actions logs for full stack trace.")[:1000], "inline": False}
                    ],
                    "footer": {"text": "YouTube Google Drive Automation"}
                }
            ]
        }

        try:
            resp = requests.post(self.webhook_url, json=payload, timeout=10)
            resp.raise_for_status()
            logger.info("Failure notification sent to Discord.")
        except Exception as e:
            logger.error(f"Failed to send Discord failure notification: {e}")
