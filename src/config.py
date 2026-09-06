import os
import yaml
from pathlib import Path
from dotenv import load_dotenv

# Load .env if present
load_dotenv()

class Config:
    def __init__(self, config_path: str = "config.yaml"):
        self.config_path = Path(config_path)
        if not self.config_path.exists():
            raise FileNotFoundError(f"Configuration file not found at: {config_path}")
        
        with open(self.config_path, "r", encoding="utf-8") as f:
            self.data = yaml.safe_load(f) or {}

    @property
    def channel_id(self) -> str:
        return self.data.get("channel", {}).get("id", "main_channel")

    @property
    def storage_source(self) -> str:
        return os.environ.get("STORAGE_SOURCE", self.data.get("storage_source", "mega")).lower()

    @property
    def mega_folder_url(self) -> str:
        return os.environ.get("MEGA_FOLDER_URL", self.data.get("mega", {}).get("folder_url", ""))

    @property
    def gdrive_folder_id(self) -> str:
        return os.environ.get("GDRIVE_FOLDER_ID", self.data.get("google_drive", {}).get("folder_id", ""))

    @property
    def gdrive_service_account_file(self) -> str:
        return os.environ.get("GDRIVE_SERVICE_ACCOUNT_FILE", self.data.get("google_drive", {}).get("service_account_file", "credentials/gdrive_service_account.json"))

    @property
    def youtube_client_secret_file(self) -> str:
        return os.environ.get("YOUTUBE_CLIENT_SECRET_FILE", self.data.get("youtube", {}).get("client_secret_file", "credentials/client_secret.json"))

    @property
    def youtube_oauth_token_file(self) -> str:
        return os.environ.get("YOUTUBE_OAUTH_TOKEN_FILE", self.data.get("youtube", {}).get("oauth_token_file", "tokens/oauth_token.json"))

    @property
    def default_category_id(self) -> str:
        return str(self.data.get("youtube", {}).get("default_category_id", "24"))

    @property
    def default_privacy_status(self) -> str:
        return self.data.get("youtube", {}).get("default_privacy_status", "public")

    @property
    def default_made_for_kids(self) -> bool:
        return bool(self.data.get("youtube", {}).get("default_made_for_kids", False))

    @property
    def shorts_max_seconds(self) -> int:
        return int(self.data.get("youtube", {}).get("shorts_max_seconds", 180))

    @property
    def description_footer(self) -> str:
        return self.data.get("youtube", {}).get("description_footer", "")

    @property
    def database_file(self) -> str:
        return self.data.get("settings", {}).get("database_file", "data/state.db")

    @property
    def downloads_dir(self) -> str:
        return self.data.get("settings", {}).get("downloads_dir", "downloads")

    @property
    def logs_dir(self) -> str:
        return self.data.get("settings", {}).get("logs_dir", "logs")

    @property
    def auto_generate_metadata(self) -> bool:
        return bool(self.data.get("settings", {}).get("auto_generate_metadata", True))

    @property
    def auto_generate_thumbnail(self) -> bool:
        return bool(self.data.get("settings", {}).get("auto_generate_thumbnail", True))

    @property
    def discord_webhook_url(self) -> str:
        return os.environ.get("DISCORD_WEBHOOK_URL", self.data.get("discord_webhook_url", ""))
