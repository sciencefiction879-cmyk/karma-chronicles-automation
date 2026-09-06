import os
import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional, List
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from googleapiclient.errors import HttpError
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request

logger = logging.getLogger(__name__)

YOUTUBE_UPLOAD_SCOPE = "https://www.googleapis.com/auth/youtube.upload"

class YouTubeUploader:
    def __init__(
        self,
        client_secret_file: str = "credentials/client_secret.json",
        oauth_token_file: str = "tokens/oauth_token.json"
    ):
        self.client_secret_file = client_secret_file
        self.oauth_token_file = oauth_token_file
        self.youtube = self._authenticate()

    def _authenticate(self):
        if not os.path.exists(self.oauth_token_file):
            raise FileNotFoundError(
                f"OAuth token file not found at: {self.oauth_token_file}\n"
                f"Please run `python auth_setup.py` once locally to generate your YouTube token."
            )

        creds = Credentials.from_authorized_user_file(
            self.oauth_token_file,
            scopes=[YOUTUBE_UPLOAD_SCOPE]
        )

        if creds.expired and creds.refresh_token:
            logger.info("OAuth token expired. Refreshing...")
            creds.refresh(Request())
            # Save refreshed credentials
            Path(self.oauth_token_file).parent.mkdir(parents=True, exist_ok=True)
            with open(self.oauth_token_file, "w", encoding="utf-8") as token_f:
                token_f.write(creds.to_json())
            logger.info("OAuth token successfully refreshed and saved.")

        return build("youtube", "v3", credentials=creds)

    def upload_video(
        self,
        video_path: Path,
        metadata: Dict[str, Any],
        is_short: bool = False,
        description_footer: str = ""
    ) -> Dict[str, Any]:
        """
        Uploads a video to YouTube with resumable chunked upload.
        """
        title = metadata.get("title", video_path.stem)
        description = metadata.get("description", "")
        if description_footer:
            description = f"{description}\n{description_footer}".strip()

        tags = metadata.get("tags", [])
        if is_short:
            if "Shorts" not in tags and "shorts" not in tags:
                tags.append("Shorts")
            if "#shorts" not in description.lower():
                description += "\n\n#shorts"

        category_id = str(metadata.get("categoryId", "22"))
        privacy_status = metadata.get("privacyStatus", "public")
        made_for_kids = bool(metadata.get("madeForKids", False))

        body = {
            "snippet": {
                "title": title[:100],  # Max 100 characters for YouTube title
                "description": description[:5000],  # Max 5000 chars for description
                "tags": tags,
                "categoryId": category_id
            },
            "status": {
                "privacyStatus": privacy_status,
                "selfDeclaredMadeForKids": made_for_kids,
                "containsSyntheticMedia": True  # AI-generated content label (required by YouTube policy)
            }
        }

        logger.info(f"Initiating upload for video: '{title}' (Type: {'Short' if is_short else 'Long-form'})...")
        logger.info("AI-generated content label: ENABLED ✅")

        media = MediaFileUpload(
            str(video_path),
            chunksize=1024 * 1024 * 10,  # 10MB chunk
            resumable=True
        )

        request = self.youtube.videos().insert(
            part="snippet,status",
            body=body,
            media_body=media
        )

        response = None
        retry_count = 0
        max_retries = 5

        while response is None:
            try:
                status, response = request.next_chunk()
                if status:
                    logger.info(f"Upload progress: {int(status.progress() * 100)}%")
            except HttpError as e:
                if e.resp.status in [500, 502, 503, 504]:
                    retry_count += 1
                    if retry_count > max_retries:
                        raise
                    sleep_time = 2 ** retry_count
                    logger.warning(f"HTTP {e.resp.status} encountered. Retrying in {sleep_time}s...")
                    time.sleep(sleep_time)
                else:
                    raise

        video_id = response.get("id")
        video_url = f"https://youtu.be/{video_id}"
        logger.info(f"Video successfully uploaded! ID: {video_id} -> {video_url}")

        return {
            "id": video_id,
            "url": video_url,
            "title": title,
            "privacy": privacy_status,
            "response": response
        }

    def set_thumbnail(self, video_id: str, thumbnail_path: Path) -> bool:
        """
        Uploads a custom thumbnail for the specified YouTube video.
        """
        if not thumbnail_path or not thumbnail_path.exists():
            logger.warning(f"Thumbnail path does not exist: {thumbnail_path}")
            return False

        logger.info(f"Setting custom thumbnail for video {video_id} from {thumbnail_path}...")
        media = MediaFileUpload(str(thumbnail_path), mimetype="image/jpeg", resumable=False)

        # YouTube sometimes takes a few seconds to register newly uploaded video
        for attempt in range(1, 4):
            try:
                self.youtube.thumbnails().set(
                    videoId=video_id,
                    media_body=media
                ).execute()
                logger.info(f"Custom thumbnail successfully set for video {video_id}!")
                return True
            except HttpError as e:
                logger.warning(f"Thumbnail upload attempt {attempt}/3 failed: {e}")
                time.sleep(5)

        logger.error(f"Failed to set thumbnail for video {video_id} after 3 attempts.")
        return False
