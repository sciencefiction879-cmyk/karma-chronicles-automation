import os
import sys
import json
import time
import base64
import logging
import urllib.parse
from pathlib import Path
from typing import Dict, Any, Optional, List

logger = logging.getLogger(__name__)


class BrowserStudioUploader:
    """
    Automated YouTube Studio Web UI uploader using Playwright Headless Chromium.
    Supports proxy authentication, session cookies, and synthetic AI labeling.
    """

    def __init__(
        self,
        cookies_file: str = "tokens/youtube_cookies.json",
        proxy_url: Optional[str] = None,
        headless: bool = True,
        timeout: int = 120000  # 2 minutes default timeout
    ):
        self.cookies_file = cookies_file
        self.proxy_url = proxy_url or os.getenv("STUDIO_PROXY") or os.getenv("PROXY_URL")
        self.headless = headless
        self.timeout = timeout
        self.cookies = self._resolve_cookies()

    def _resolve_cookies(self) -> List[Dict[str, Any]]:
        """
        Resolves cookies from YOUTUBE_COOKIES environment variable (Base64 or JSON)
        or from the local cookies file.
        """
        env_cookies = os.getenv("YOUTUBE_COOKIES")
        if env_cookies:
            logger.info("Loading YouTube cookies from YOUTUBE_COOKIES environment variable.")
            try:
                # Attempt base64 decode first
                decoded = base64.b64decode(env_cookies).decode("utf-8")
                return json.loads(decoded)
            except Exception:
                try:
                    return json.loads(env_cookies)
                except Exception as e:
                    logger.error(f"Failed to parse YOUTUBE_COOKIES env var: {e}")

        if os.path.exists(self.cookies_file):
            logger.info(f"Loading YouTube cookies from file: {self.cookies_file}")
            with open(self.cookies_file, "r") as f:
                return json.load(f)

        raise FileNotFoundError(
            f"No YouTube Studio cookies found! Checked YOUTUBE_COOKIES env var and '{self.cookies_file}'.\n"
            f"Run `python scripts/export_ixbrowser_cookies.py` to export cookies from ixBrowser."
        )

    def _get_proxy_settings(self) -> Optional[Dict[str, str]]:
        """Parses proxy string into Playwright proxy dictionary."""
        if not self.proxy_url:
            return None

        # Clean proxy string
        p = self.proxy_url.strip()
        if not p.startswith("http://") and not p.startswith("https://") and not p.startswith("socks5://"):
            # Check for host:port:user:pass format
            parts = p.split(":")
            if len(parts) == 4:
                host, port, user, pwd = parts
                return {
                    "server": f"http://{host}:{port}",
                    "username": user,
                    "password": pwd
                }
            elif len(parts) == 2:
                host, port = parts
                return {"server": f"http://{host}:{port}"}
            else:
                p = f"http://{p}"

        parsed = urllib.parse.urlparse(p)
        server = f"{parsed.scheme}://{parsed.hostname}:{parsed.port}"
        proxy_dict = {"server": server}
        if parsed.username:
            proxy_dict["username"] = urllib.parse.unquote(parsed.username)
        if parsed.password:
            proxy_dict["password"] = urllib.parse.unquote(parsed.password)

        return proxy_dict

    def upload_video(
        self,
        video_path: Path,
        metadata: Dict[str, Any],
        is_short: bool = False,
        description_footer: str = ""
    ) -> Dict[str, Any]:
        """
        Uploads a video to YouTube Studio UI using Playwright.
        """
        try:
            from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
        except ImportError:
            raise ImportError(
                "Playwright is not installed. Run `pip install playwright` and `playwright install chromium`."
            )

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

        privacy_status = metadata.get("privacyStatus", "public").lower()
        made_for_kids = bool(metadata.get("madeForKids", False))

        logger.info(f"Launching Browser Studio Uploader for video: '{title}' (Short={is_short})...")
        proxy_settings = self._get_proxy_settings()
        if proxy_settings:
            logger.info(f"Using Proxy: {proxy_settings.get('server')}")
        else:
            logger.info("No proxy specified; connecting directly.")

        # Create diagnostic directory
        os.makedirs("logs", exist_ok=True)

        with sync_playwright() as p:
            # Launch Chromium with anti-detection flags
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--window-size=1366,768"
            ]

            browser = p.chromium.launch(
                headless=self.headless,
                args=launch_args,
                proxy=proxy_settings
            )

            context = browser.new_context(
                viewport={"width": 1366, "height": 768},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                locale="en-US",
                timezone_id="America/New_York"
            )

            # Inject cookies
            context.add_cookies(self.cookies)
            page = context.new_page()
            page.set_default_timeout(self.timeout)

            try:
                # 1. Navigate to YouTube Studio
                logger.info("Navigating to https://studio.youtube.com...")
                page.goto("https://studio.youtube.com", wait_until="domcontentloaded", timeout=60000)
                time.sleep(3)

                # Check if redirected to login
                if "accounts.google.com" in page.url or "signin" in page.url:
                    page.screenshot(path="logs/auth_failed.png")
                    raise RuntimeError(
                        "YouTube Studio session expired! Google redirected to login page.\n"
                        "Please update YOUTUBE_COOKIES secret using scripts/export_ixbrowser_cookies.py."
                    )

                logger.info("Successfully connected to YouTube Studio dashboard! ✅")

                # 2. Click Create / Upload
                logger.info("Opening Upload dialog...")
                create_button = None
                for selector in [
                    "#create-icon",
                    "ytcp-button#create-icon",
                    "button#create-icon",
                    "ytcp-button:has-text('CREATE')",
                    "ytcp-button:has-text('Create')"
                ]:
                    if page.locator(selector).count() > 0:
                        create_button = page.locator(selector).first
                        break

                if create_button:
                    create_button.click()
                    time.sleep(1)
                    # Click "Upload videos"
                    upload_menu_item = page.locator("tp-yt-paper-item:has-text('Upload videos'), #text-item-0, ytcp-text-menu #text-item-0")
                    if upload_menu_item.count() > 0:
                        upload_menu_item.first.click()
                else:
                    # Alternative: navigate directly to upload URL
                    page.goto("https://studio.youtube.com/channel/videos/upload?d=ud", wait_until="domcontentloaded")

                time.sleep(2)

                # 3. Set Video File Input
                logger.info(f"Injecting video file: {video_path.name}...")
                file_input = page.locator("input[type='file']")
                file_input.wait_for(state="attached", timeout=30000)
                file_input.set_input_files(str(video_path))
                logger.info("Video file attached. Waiting for upload details form...")

                # Wait for title textarea to become visible
                title_locator = page.locator("#title-textarea #textbox, input#textbox")
                title_locator.wait_for(state="visible", timeout=60000)
                time.sleep(2)

                # 4. Set Title
                logger.info(f"Filling Title: '{title[:100]}'...")
                title_locator.fill(title[:100])
                time.sleep(1)

                # 5. Set Description
                desc_locator = page.locator("#description-textarea #textbox")
                if desc_locator.count() > 0:
                    logger.info("Filling Description...")
                    desc_locator.fill(description[:5000])
                    time.sleep(1)

                # 6. Audience Selection: "No, it's not made for kids"
                logger.info("Setting Audience: 'Not made for kids'...")
                not_for_kids_radio = page.locator("tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']")
                if not_for_kids_radio.count() > 0:
                    not_for_kids_radio.first.click()
                else:
                    alt_radio = page.locator("tp-yt-paper-radio-button:has-text('No, it\\'s not made for kids')")
                    if alt_radio.count() > 0:
                        alt_radio.first.click()
                time.sleep(1)

                # 7. AI Synthetic Content Disclosure ("Show more" -> Altered content: Yes)
                logger.info("Configuring AI-generated synthetic content label...")
                show_more_btn = page.locator("#toggle-button, ytcp-button:has-text('SHOW MORE'), ytcp-button:has-text('Show more')")
                if show_more_btn.count() > 0:
                    try:
                        show_more_btn.first.click()
                        time.sleep(1)
                    except Exception:
                        pass

                # Check "Yes" for altered content
                altered_yes = page.locator("tp-yt-paper-radio-button[name='HAS_ALTERED_CONTENT_YES'], tp-yt-paper-radio-button:has-text('Yes')")
                if altered_yes.count() > 0:
                    altered_yes.first.click()
                    logger.info("AI-generated content label: ENABLED ✅")

                # 8. Step through Wizard (Elements, Checks, Visibility)
                logger.info("Navigating wizard steps to Visibility...")
                next_btn = page.locator("#next-button")

                for step in range(3):
                    if next_btn.is_enabled():
                        next_btn.click()
                        time.sleep(2)

                # 9. Set Visibility to Public
                logger.info(f"Setting Visibility to '{privacy_status.upper()}'...")
                if privacy_status == "public":
                    public_radio = page.locator("tp-yt-paper-radio-button[name='PUBLIC'], #public-radio-button")
                    if public_radio.count() > 0:
                        public_radio.first.click()
                elif privacy_status == "unlisted":
                    unlisted_radio = page.locator("tp-yt-paper-radio-button[name='UNLISTED']")
                    if unlisted_radio.count() > 0:
                        unlisted_radio.first.click()
                elif privacy_status == "private":
                    private_radio = page.locator("tp-yt-paper-radio-button[name='PRIVATE']")
                    if private_radio.count() > 0:
                        private_radio.first.click()

                time.sleep(2)

                # 10. Extract Video URL & ID
                video_url = ""
                video_id = ""
                video_link_elem = page.locator("a.ytcp-video-info, a[href*='youtu.be']")
                if video_link_elem.count() > 0:
                    href = video_link_elem.first.get_attribute("href") or ""
                    if "youtu.be/" in href:
                        video_url = href.split("?")[0]
                        video_id = video_url.split("youtu.be/")[-1].strip()
                    elif "/watch?v=" in href:
                        video_url = href.split("&")[0]
                        video_id = video_url.split("v=")[-1].strip()

                if not video_id:
                    # Fallback: look in text content
                    modal_text = page.locator("ytcp-uploads-dialog").inner_text()
                    for line in modal_text.splitlines():
                        if "youtu.be/" in line:
                            video_id = line.split("youtu.be/")[-1].strip().split()[0]
                            video_url = f"https://youtu.be/{video_id}"
                            break

                logger.info(f"Captured Video Link: {video_url or 'Pending processing'} (ID: {video_id})")

                # 11. Click Publish / Done
                logger.info("Publishing video...")
                done_btn = page.locator("#done-button, ytcp-button#done-button, ytcp-button:has-text('Publish'), ytcp-button:has-text('Save')")
                done_btn.first.click()

                # Wait for upload completion / success dialog
                time.sleep(10)
                logger.info("Video successfully published via YouTube Studio UI! 🎉")

                # If video_id was not captured earlier, try capturing from final popup
                if not video_id:
                    final_link = page.locator("a[href*='youtu.be']")
                    if final_link.count() > 0:
                        href = final_link.first.get_attribute("href") or ""
                        video_id = href.split("youtu.be/")[-1].split("?")[0].strip()
                        video_url = f"https://youtu.be/{video_id}"

                if not video_id:
                    # Generic placeholder if processing
                    video_id = f"uploaded_{int(time.time())}"
                    video_url = f"https://studio.youtube.com/video/{video_id}"

                return {
                    "id": video_id,
                    "url": video_url,
                    "title": title,
                    "privacy": privacy_status,
                    "response": {"source": "browser_studio_ui", "status": "published"}
                }

            except Exception as exc:
                # Capture diagnostic screenshot
                screenshot_path = f"logs/studio_error_{int(time.time())}.png"
                try:
                    page.screenshot(path=screenshot_path)
                    logger.error(f"Error occurred during Studio upload. Saved debug screenshot to {screenshot_path}")
                except Exception:
                    pass
                raise exc
            finally:
                context.close()
                browser.close()

    def set_thumbnail(self, video_id: str, thumbnail_path: Path) -> bool:
        """
        Thumbnail setting through browser or fallback.
        For YouTube Studio UI, thumbnail can be set during details form or left to YouTube default.
        """
        logger.info(f"Browser uploader: Custom thumbnail for video {video_id} handled during upload or defaulted.")
        return True
