import os
import io
import re
import json
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
METADATA_EXTENSIONS = {".json"}
THUMBNAIL_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

class DriveSequenceItem:
    def __init__(
        self,
        sequence_num: int,
        sequence_key: str,
        video_file: Optional[Dict[str, Any]] = None,
        json_file: Optional[Dict[str, Any]] = None,
        thumbnail_file: Optional[Dict[str, Any]] = None,
    ):
        self.sequence_num = sequence_num
        self.sequence_key = sequence_key  # e.g. "V1"
        self.video_file = video_file      # {'id': '...', 'name': 'V1.mp4', 'size': ...}
        self.json_file = json_file        # {'id': '...', 'name': 'V1.json'}
        self.thumbnail_file = thumbnail_file  # {'id': '...', 'name': 'V1.jpg'}

    @property
    def has_video(self) -> bool:
        return self.video_file is not None

    @property
    def has_metadata(self) -> bool:
        return self.json_file is not None

    @property
    def has_thumbnail(self) -> bool:
        return self.thumbnail_file is not None

    def __repr__(self):
        return (
            f"<DriveSequenceItem {self.sequence_key} (num={self.sequence_num}): "
            f"video={bool(self.video_file)}, json={bool(self.json_file)}, thumb={bool(self.thumbnail_file)}>"
        )


class DriveManager:
    def __init__(
        self,
        folder_id: str,
        service_account_file: Optional[str] = None,
        oauth_token_file: Optional[str] = None
    ):
        self.folder_id = folder_id
        self.service_account_file = service_account_file
        self.oauth_token_file = oauth_token_file
        self.service = self._authenticate()

    def _authenticate(self):
        scopes = ["https://www.googleapis.com/auth/drive.readonly"]

        if self.service_account_file and os.path.exists(self.service_account_file):
            logger.info(f"Authenticating with Service Account: {self.service_account_file}")
            creds = service_account.Credentials.from_service_account_file(
                self.service_account_file, scopes=scopes
            )
            return build("drive", "v3", credentials=creds)

        if self.oauth_token_file and os.path.exists(self.oauth_token_file):
            logger.info(f"Authenticating with OAuth Token: {self.oauth_token_file}")
            creds = Credentials.from_authorized_user_file(self.oauth_token_file, scopes=scopes)
            return build("drive", "v3", credentials=creds)

        raise FileNotFoundError(
            f"No valid Google Drive credentials found. Checked:\n"
            f"  - Service Account: {self.service_account_file}\n"
            f"  - OAuth Token: {self.oauth_token_file}\n"
            f"Please place your Google Service Account JSON in credentials/gdrive_service_account.json"
        )

    def _list_all_files_in_folder_recursive(self, parent_id: str) -> List[Dict[str, Any]]:
        """Recursively list all files within a Google Drive folder and its subfolders."""
        all_files = []
        folders_to_scan = [parent_id]
        visited_folders = set()

        while folders_to_scan:
            current_folder = folders_to_scan.pop(0)
            if current_folder in visited_folders:
                continue
            visited_folders.add(current_folder)

            page_token = None
            query = f"'{current_folder}' in parents and trashed = false"

            while True:
                response = self.service.files().list(
                    q=query,
                    spaces="drive",
                    fields="nextPageToken, files(id, name, mimeType, size, modifiedTime)",
                    pageToken=page_token
                ).execute()

                files = response.get("files", [])
                for item in files:
                    if item.get("mimeType") == "application/vnd.google-apps.folder":
                        folders_to_scan.append(item["id"])
                    else:
                        all_files.append(item)

                page_token = response.get("nextPageToken")
                if not page_token:
                    break

        return all_files

    def _extract_sequence_number(self, filename: str) -> Optional[int]:
        """Extracts sequence number N from names like V1.mp4, v12.json, V3_thumb.png, V4 - Part 1.mp4"""
        stem = Path(filename).stem
        # Matches V1, v01, V_2, Video1, etc.
        match = re.search(r"^(?:video|v)[\s_-]*(\d+)", stem, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    def scan_sequence_items(self) -> Dict[int, DriveSequenceItem]:
        """
        Scans Google Drive folder and maps all V{N} files into DriveSequenceItem objects.
        Returns a dict keyed by sequence_num (e.g. 1 -> DriveSequenceItem(1, 'V1', ...))
        """
        logger.info(f"Scanning Google Drive folder ID: {self.folder_id}...")
        files = self._list_all_files_in_folder_recursive(self.folder_id)
        logger.info(f"Found {len(files)} total files in Google Drive folder.")

        items: Dict[int, DriveSequenceItem] = {}

        for file_info in files:
            name = file_info.get("name", "")
            ext = Path(name).suffix.lower()
            seq_num = self._extract_sequence_number(name)

            if seq_num is None:
                continue

            if seq_num not in items:
                items[seq_num] = DriveSequenceItem(
                    sequence_num=seq_num,
                    sequence_key=f"V{seq_num}"
                )

            item = items[seq_num]

            if ext in VIDEO_EXTENSIONS:
                # If multiple video files match, prefer the newest or exact V{N}.ext
                item.video_file = file_info
            elif ext in METADATA_EXTENSIONS:
                item.json_file = file_info
            elif ext in THUMBNAIL_EXTENSIONS:
                item.thumbnail_file = file_info

        return dict(sorted(items.items()))

    def get_next_unposted_item(self, uploaded_keys: set) -> Optional[DriveSequenceItem]:
        """
        Finds the lowest sequence number item (V1, then V2, then V3) that hasn't been uploaded yet.
        """
        items = self.scan_sequence_items()
        for seq_num in sorted(items.keys()):
            item = items[seq_num]
            if item.sequence_key not in uploaded_keys:
                if not item.has_video:
                    logger.warning(f"{item.sequence_key} has no video file found! Skipping.")
                    continue
                if not item.has_metadata:
                    logger.warning(f"{item.sequence_key} has no metadata JSON file found! Skipping.")
                    continue
                return item
        return None

    def download_file(self, file_id: str, dest_path: Path) -> Path:
        """Downloads a file from Google Drive to local destination."""
        dest_path.parent.mkdir(parents=True, exist_ok=True)
        request = self.service.files().get_media(fileId=file_id)
        
        logger.info(f"Downloading file {file_id} to {dest_path}...")
        with io.FileIO(dest_path, "wb") as fh:
            downloader = MediaIoBaseDownload(fh, request, chunksize=1024 * 1024 * 10)  # 10MB chunks
            done = False
            while not done:
                status, done = downloader.next_chunk()
                if status:
                    logger.debug(f"Download progress: {int(status.progress() * 100)}%")

        logger.info(f"Successfully downloaded: {dest_path}")
        return dest_path

    def download_sequence_bundle(
        self,
        item: DriveSequenceItem,
        target_dir: Path
    ) -> Tuple[Path, Dict[str, Any], Optional[Path]]:
        """
        Downloads the video, JSON metadata, and thumbnail (if present) for a sequence item.
        Returns (local_video_path, metadata_dict, local_thumbnail_path).
        """
        bundle_dir = target_dir / item.sequence_key
        bundle_dir.mkdir(parents=True, exist_ok=True)

        # 1. Download Video
        if not item.video_file:
            raise FileNotFoundError(f"No video file found in Drive for {item.sequence_key}")
        video_ext = Path(item.video_file["name"]).suffix
        local_video_path = bundle_dir / f"{item.sequence_key}{video_ext}"
        self.download_file(item.video_file["id"], local_video_path)

        # 2. Download JSON Metadata
        if not item.json_file:
            raise FileNotFoundError(f"No metadata JSON found in Drive for {item.sequence_key}")
        local_json_path = bundle_dir / f"{item.sequence_key}.json"
        self.download_file(item.json_file["id"], local_json_path)

        with open(local_json_path, "r", encoding="utf-8") as f:
            metadata = json.load(f)

        # 3. Download Thumbnail if available
        local_thumb_path = None
        if item.thumbnail_file:
            thumb_ext = Path(item.thumbnail_file["name"]).suffix
            local_thumb_path = bundle_dir / f"{item.sequence_key}_thumb{thumb_ext}"
            self.download_file(item.thumbnail_file["id"], local_thumb_path)

        return local_video_path, metadata, local_thumb_path
