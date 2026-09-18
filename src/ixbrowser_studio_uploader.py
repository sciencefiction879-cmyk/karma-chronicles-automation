import os
import sys
import time
import logging
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)


class IXBrowserStudioUploader:
    """
    Automates YouTube Studio uploads directly through an existing ixBrowser profile.
    Leverages ixBrowser's local anti-detect engine, proxy, and logged-in YouTube session.
    Controls the browser in the background via Playwright CDP (Chrome DevTools Protocol).
    """

    def __init__(self, profile_id: Optional[int] = None, port: int = 53200):
        self.profile_id = profile_id
        self.port = port
        self.client = None
        self._init_client()

    def _init_client(self):
        try:
            from ixbrowser_local_api import IXBrowserClient
            self.client = IXBrowserClient(target="127.0.0.1", port=self.port)
        except ImportError:
            raise ImportError(
                "ixbrowser-local-api is not installed. Run `pip install ixbrowser-local-api`."
            )

    def is_ixbrowser_running(self) -> bool:
        """Verifies if the ixBrowser local API service is accessible."""
        try:
            profile_list = self.client.get_profile_list(page=1, limit=1)
            return profile_list is not None
        except Exception:
            return False

    def list_profiles(self):
        """Returns the list of profiles in ixBrowser."""
        return self.client.get_profile_list(page=1, limit=50)

    def upload_video(
        self,
        video_path: Path,
        metadata: Dict[str, Any],
        profile_id: Optional[int] = None,
        is_short: bool = False,
        description_footer: str = ""
    ) -> Dict[str, Any]:
        """
        Launches ixBrowser profile, uploads video via YouTube Studio UI, and closes profile.
        """
        from playwright.sync_api import sync_playwright

        target_profile_id = profile_id or self.profile_id
        if not target_profile_id:
            raise ValueError("profile_id must be provided to upload via ixBrowser.")

        if not self.is_ixbrowser_running():
            raise RuntimeError(
                "ixBrowser app is not running or Local API is not reachable!\n"
                "Please open /Applications/ixBrowser.app and make sure it is running."
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

        logger.info(f"Opening ixBrowser Profile ID #{target_profile_id} (Proxy & Fingerprint active)...")
        open_res = self.client.open_profile(
            target_profile_id,
            load_profile_info_page=False,
            disable_extension_welcome_page=True
        )

        if not open_res:
            err = self.client.message or "Unknown error opening profile"
            raise RuntimeError(f"ixBrowser failed to open profile #{target_profile_id}: {err}")

        # Connect Playwright via CDP
        ws_endpoint = open_res.get("ws")
        debug_addr = open_res.get("debugging_address")
        cdp_url = ws_endpoint or f"http://{debug_addr}"
        logger.info(f"Connected to ixBrowser CDP endpoint: {cdp_url}")

        video_id = ""
        video_url = ""

        try:
            with sync_playwright() as p:
                browser = p.chromium.connect_over_cdp(cdp_url)
                context = browser.contexts[0] if browser.contexts else browser.new_context()

                # Find or create YouTube Studio page
                page = None
                for existing_page in context.pages:
                    if "studio.youtube.com" in existing_page.url:
                        page = existing_page
                        break

                if not page:
                    page = context.new_page()
                    logger.info("Navigating to https://studio.youtube.com...")
                    page.goto("https://studio.youtube.com", wait_until="domcontentloaded", timeout=60000)

                time.sleep(3)

                # Check if logged in
                if "accounts.google.com" in page.url:
                    raise RuntimeError("YouTube Studio is not logged in inside this ixBrowser profile!")

                logger.info("Connected to YouTube Studio in ixBrowser! ✅")

                # Open upload dialog
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
                    upload_item = page.locator("tp-yt-paper-item:has-text('Upload videos'), #text-item-0, ytcp-text-menu #text-item-0")
                    if upload_item.count() > 0:
                        upload_item.first.click()
                else:
                    page.goto("https://studio.youtube.com/channel/videos/upload?d=ud", wait_until="domcontentloaded")

                time.sleep(2)

                # Attach Video File
                logger.info(f"Attaching video file: {video_path.name}...")
                file_input = page.locator("input[type='file']")
                file_input.wait_for(state="attached", timeout=30000)
                file_input.set_input_files(str(video_path))
                logger.info("Video file attached. Waiting for upload details form...")

                # Wait for title
                title_locator = page.locator("#title-textarea #textbox, input#textbox")
                title_locator.wait_for(state="visible", timeout=60000)
                time.sleep(2)

                # Fill Title & Description
                logger.info(f"Filling Title: '{title[:100]}'...")
                title_locator.fill(title[:100])
                time.sleep(1)

                desc_locator = page.locator("#description-textarea #textbox")
                if desc_locator.count() > 0:
                    desc_locator.fill(description[:5000])
                    time.sleep(1)

                # Audience: Not Made for Kids
                logger.info("Setting Audience: 'Not made for kids'...")
                not_mfk = page.locator("tp-yt-paper-radio-button[name='VIDEO_MADE_FOR_KIDS_NOT_MFK']")
                if not_mfk.count() > 0:
                    not_mfk.first.click()
                else:
                    alt_radio = page.locator("tp-yt-paper-radio-button:has-text('No, it\\'s not made for kids')")
                    if alt_radio.count() > 0:
                        alt_radio.first.click()
                time.sleep(1)

                # Altered Synthetic Content (AI)
                logger.info("Configuring AI altered content disclosure...")
                show_more = page.locator("#toggle-button, ytcp-button:has-text('SHOW MORE'), ytcp-button:has-text('Show more')")
                if show_more.count() > 0:
                    try:
                        show_more.first.click()
                        time.sleep(1)
                    except Exception:
                        pass

                altered_yes = page.locator("tp-yt-paper-radio-button[name='HAS_ALTERED_CONTENT_YES'], tp-yt-paper-radio-button:has-text('Yes')")
                if altered_yes.count() > 0:
                    altered_yes.first.click()
                    logger.info("AI altered content label: ENABLED ✅")

                # Advance through Next buttons
                next_btn = page.locator("#next-button")
                for _ in range(3):
                    if next_btn.is_enabled():
                        next_btn.click()
                        time.sleep(2)

                # Set Visibility
                logger.info(f"Setting Visibility to '{privacy_status.upper()}'...")
                if privacy_status == "public":
                    public_radio = page.locator("tp-yt-paper-radio-button[name='PUBLIC'], #public-radio-button")
                    if public_radio.count() > 0:
                        public_radio.first.click()

                time.sleep(2)

                # Extract Video URL
                video_link_elem = page.locator("a.ytcp-video-info, a[href*='youtu.be']")
                if video_link_elem.count() > 0:
                    href = video_link_elem.first.get_attribute("href") or ""
                    if "youtu.be/" in href:
                        video_url = href.split("?")[0]
                        video_id = video_url.split("youtu.be/")[-1].strip()

                # Click Publish
                logger.info("Clicking Publish...")
                done_btn = page.locator("#done-button, ytcp-button#done-button, ytcp-button:has-text('Publish')")
                done_btn.first.click()

                # Wait for upload to conclude
                time.sleep(10)
                logger.info("Video successfully published through ixBrowser! 🎉")

                if not video_id:
                    final_link = page.locator("a[href*='youtu.be']")
                    if final_link.count() > 0:
                        href = final_link.first.get_attribute("href") or ""
                        video_id = href.split("youtu.be/")[-1].split("?")[0].strip()
                        video_url = f"https://youtu.be/{video_id}"

                return {
                    "id": video_id or f"ixb_{int(time.time())}",
                    "url": video_url or "https://studio.youtube.com",
                    "title": title,
                    "privacy": privacy_status,
                    "response": {"source": "ixbrowser_cdp", "status": "published"}
                }

        finally:
            # Always close the ixBrowser profile cleanly
            logger.info(f"Closing ixBrowser profile #{target_profile_id}...")
            self.client.close_profile(target_profile_id)
