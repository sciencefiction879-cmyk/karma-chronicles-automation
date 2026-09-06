import os
import re
import json
import logging
import subprocess
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Any, Tuple

from src.video_utils import VideoInfo, inspect_video

logger = logging.getLogger(__name__)

VIDEO_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v"}
METADATA_EXTENSIONS = {".json"}
THUMBNAIL_EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp"}

class MegaSequenceItem:
    def __init__(
        self,
        sequence_num: int,
        sequence_key: str,
        video_path: Optional[Path] = None,
        json_path: Optional[Path] = None,
        thumbnail_path: Optional[Path] = None,
        catalog_metadata: Optional[Dict[str, Any]] = None
    ):
        self.sequence_num = sequence_num
        self.sequence_key = sequence_key  # e.g. "V1"
        self.video_path = video_path
        self.json_path = json_path
        self.thumbnail_path = thumbnail_path
        self.catalog_metadata = catalog_metadata

    @property
    def has_video(self) -> bool:
        return self.video_path is not None and self.video_path.exists()

    @property
    def has_metadata(self) -> bool:
        return (self.json_path is not None and self.json_path.exists()) or (self.catalog_metadata is not None)

    @property
    def has_thumbnail(self) -> bool:
        return self.thumbnail_path is not None and self.thumbnail_path.exists()

    def __repr__(self):
        return (
            f"<MegaSequenceItem {self.sequence_key} (num={self.sequence_num}): "
            f"video={bool(self.video_path)}, json={bool(self.json_path)}, thumb={bool(self.thumbnail_path)}>"
        )


class MegaManager:
    def __init__(self, mega_folder_url: str, downloads_dir: str = "downloads"):
        self.mega_folder_url = mega_folder_url
        self.downloads_dir = Path(downloads_dir)
        self.downloads_dir.mkdir(parents=True, exist_ok=True)
        self._check_megatools()

    def _check_megatools(self):
        # Look for megatools in common paths
        if not shutil.which("megatools") and not Path("/opt/homebrew/bin/megatools").exists():
            logger.warning("megatools binary not found in standard PATH.")

    def _get_megatools_bin(self) -> str:
        if shutil.which("megatools"):
            return "megatools"
        if Path("/opt/homebrew/bin/megatools").exists():
            return "/opt/homebrew/bin/megatools"
        if Path("/usr/bin/megatools").exists():
            return "/usr/bin/megatools"
        return "megatools"

    def download_folder_contents(self) -> Path:
        """Downloads all files from the public MEGA folder into downloads_dir."""
        logger.info(f"Downloading files from MEGA folder: {self.mega_folder_url}...")
        megatools_bin = self._get_megatools_bin()
        
        cmd = [
            megatools_bin,
            "dl",
            "--no-progress",
            "--print-names",
            f"--path={self.downloads_dir}",
            self.mega_folder_url
        ]

        try:
            result = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
            logger.info(f"megatools output:\n{result.stdout}")
        except subprocess.CalledProcessError as e:
            logger.error(f"megatools dl failed: {e.stderr}")
            # If files already exist in downloads_dir, proceed
            if not any(self.downloads_dir.iterdir()):
                raise RuntimeError(f"Failed to download from MEGA folder: {e.stderr}")

        return self.downloads_dir

    def _extract_sequence_number(self, filename: str) -> Optional[int]:
        stem = Path(filename).stem
        match = re.search(r"^(?:video|v|thumbnail|thumb|t)?[\s_-]*(\d+)", stem, re.IGNORECASE)
        if match:
            return int(match.group(1))
        return None

    def load_unified_catalog(self) -> Dict[str, Dict[str, Any]]:
        """Loads unified catalog metadata from videos_metadata.json if available."""
        catalog: Dict[str, Dict[str, Any]] = {}
        possible_paths = [
            self.downloads_dir / "videos_metadata.json",
            Path("data/videos_metadata.json"),
        ]
        if self.downloads_dir.exists():
            for p in self.downloads_dir.rglob("*metadata*.json"):
                possible_paths.append(p)

        for cat_path in possible_paths:
            if cat_path.exists():
                try:
                    with open(cat_path, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        if isinstance(data, list):
                            for entry in data:
                                key = entry.get("video_number") or entry.get("video_file")
                                if key:
                                    stem = Path(key).stem.upper()
                                    catalog[stem] = entry
                            logger.info(f"Loaded {len(catalog)} metadata entries from {cat_path}")
                            break
                except Exception as e:
                    logger.warning(f"Failed to parse catalog metadata from {cat_path}: {e}")

        return catalog

    def scan_sequence_items(self) -> Dict[int, MegaSequenceItem]:
        """Scans the downloaded directory and maps all V{N} files."""
        items: Dict[int, MegaSequenceItem] = {}
        catalog = self.load_unified_catalog()

        # Scan recursively in downloads_dir
        for file_path in self.downloads_dir.rglob("*"):
            if not file_path.is_file() or file_path.name.startswith("."):
                continue

            name = file_path.name
            ext = file_path.suffix.lower()
            seq_num = self._extract_sequence_number(name)

            if seq_num is None:
                continue

            seq_key = f"V{seq_num}"
            if seq_num not in items:
                items[seq_num] = MegaSequenceItem(
                    sequence_num=seq_num,
                    sequence_key=seq_key,
                    catalog_metadata=catalog.get(seq_key.upper())
                )

            item = items[seq_num]
            if not item.catalog_metadata and seq_key.upper() in catalog:
                item.catalog_metadata = catalog[seq_key.upper()]

            if ext in VIDEO_EXTENSIONS:
                item.video_path = file_path
            elif ext in METADATA_EXTENSIONS:
                item.json_path = file_path
            elif ext in THUMBNAIL_EXTENSIONS:
                item.thumbnail_path = file_path

        return dict(sorted(items.items()))

    def get_next_unposted_item(self, uploaded_keys: set) -> Optional[MegaSequenceItem]:
        """Finds the lowest sequence number item (V1, then V2, then V3) that hasn't been uploaded."""
        items = self.scan_sequence_items()
        for seq_num in sorted(items.keys()):
            item = items[seq_num]
            if item.sequence_key not in uploaded_keys:
                if not item.has_video:
                    logger.warning(f"{item.sequence_key} has no video file found! Skipping.")
                    continue
                return item
        return None

    def generate_auto_metadata(self, sequence_key: str, seq_num: int, is_short: bool) -> Dict[str, Any]:
        """Automatically generates catchy Sci-Fi / Cinematic metadata if no JSON is provided."""
        if is_short:
            title = f"Sci-Fi Cinematic Universe | Part {seq_num} #Shorts"
            description = (
                f"Experience the world of science fiction and cinematic CGI.\n"
                f"Part {seq_num} of the series.\n\n"
                f"🔔 Subscribe for more sci-fi animations and short films!\n\n"
                f"#shorts #scifi #cinematic #cgi #animation #3d #conceptart"
            )
        else:
            title = f"Chronicles of the Wasteland | Episode {seq_num} [4K Sci-Fi CGI]"
            description = (
                f"Immerse yourself in a dystopian sci-fi world.\n\n"
                f"Episode {seq_num} — Watch in high definition.\n"
                f"Created with high quality 3D animation and cinematic sound design.\n\n"
                f"👍 Like and Subscribe to support the series!\n\n"
                f"#scifi #sciencefiction #cgi #animation #3danimation #cinematic #shortfilm #wasteland"
            )

        return {
            "title": title,
            "description": description,
            "tags": [
                "scifi", "science fiction", "cinematic", "cgi", "3d animation",
                "blender", "unreal engine", "animation", "short film", "sci-fi short"
            ],
            "categoryId": "24",  # Entertainment
            "privacyStatus": "public",
            "isShort": is_short,
            "madeForKids": False
        }

    def extract_auto_thumbnail(self, video_path: Path, output_thumb_path: Path) -> Path:
        """Extracts a crisp frame from the video using ffmpeg at 20% mark as thumbnail."""
        output_thumb_path.parent.mkdir(parents=True, exist_ok=True)
        
        info = inspect_video(video_path)
        timestamp = max(1.0, info.duration * 0.2)  # Extract at 20% mark
        
        cmd = [
            "ffmpeg",
            "-y",
            "-ss", str(timestamp),
            "-i", str(video_path),
            "-vframes", "1",
            "-q:v", "2",
            str(output_thumb_path)
        ]
        
        logger.info(f"Extracting auto thumbnail from {video_path.name} at {timestamp:.1f}s -> {output_thumb_path}...")
        subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
        return output_thumb_path
