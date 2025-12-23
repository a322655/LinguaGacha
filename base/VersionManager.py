import os
import platform
import re
import shutil
import signal
import sys
import threading
import time
import zipfile
from enum import StrEnum
from typing import Self

import httpx
from PyQt5.QtCore import QUrl
from PyQt5.QtGui import QDesktopServices

from base.Base import Base
from module.Localizer.Localizer import Localizer


def get_platform_info() -> tuple[str, str]:
    """Get current platform and architecture info."""
    if sys.platform == "darwin":
        arch = platform.machine()  # arm64 or x86_64
        return ("macos", arch)
    return ("windows", "x86_64")


class VersionManager(Base):
    class Status(StrEnum):
        NONE = "NONE"
        NEW_VERSION = "NEW_VERSION"
        UPDATING = "UPDATING"
        DOWNLOADED = "DOWNLOADED"

    # Temp file for updates
    TEMP_PATH: str = "./resource/update.temp"

    # URL addresses
    API_URL: str = "https://api.github.com/repos/neavo/LinguaGacha/releases/latest"
    RELEASE_URL: str = "https://github.com/neavo/LinguaGacha/releases/latest"

    def __init__(self) -> None:
        super().__init__()

        # Initialize
        self.status = __class__.Status.NONE
        self.version = "v0.0.0"
        self.extracting = False

        # Thread lock
        self.lock: threading.Lock = threading.Lock()

        # Register events
        self.subscribe(Base.Event.APP_UPDATE_EXTRACT, self.app_update_extract)
        self.subscribe(Base.Event.APP_UPDATE_CHECK_START, self.app_update_check_start)
        self.subscribe(
            Base.Event.APP_UPDATE_DOWNLOAD_START, self.app_update_download_start
        )

    @classmethod
    def get(cls) -> Self:
        if getattr(cls, "__instance__", None) is None:
            cls.__instance__ = cls()

        return cls.__instance__

    # Extract
    def app_update_extract(self, event: str, data: dict) -> None:
        with self.lock:
            if self.extracting == False:
                threading.Thread(
                    target=self.app_update_extract_task,
                    args=(event, data),
                ).start()

    # Check
    def app_update_check_start(self, event: str, data: dict) -> None:
        threading.Thread(
            target=self.app_update_check_start_task,
            args=(event, data),
        ).start()

    # Download
    def app_update_download_start(self, event: str, data: dict) -> None:
        threading.Thread(
            target=self.app_update_download_start_task,
            args=(event, data),
        ).start()

    # Extract update (Windows only)
    def app_update_extract_task(self, event: str, data: dict) -> None:
        plat, _ = get_platform_info()

        # macOS uses DMG, skip extraction and open release page
        if plat == "macos":
            self.emit(
                Base.Event.APP_TOAST_SHOW,
                {
                    "type": Base.ToastType.SUCCESS,
                    "message": Localizer.get().app_new_version_waiting_restart,
                    "duration": 60 * 1000,
                },
            )
            time.sleep(1)
            QDesktopServices.openUrl(QUrl(__class__.RELEASE_URL))
            with self.lock:
                self.extracting = False
            return

        # Windows extraction logic
        with self.lock:
            self.extracting = True

        # Remove old backup files
        try:
            os.remove("./app.exe.bak")
        except Exception:
            pass
        try:
            os.remove("./version.txt.bak")
        except Exception:
            pass

        # Backup current files
        try:
            os.rename("./app.exe", "./app.exe.bak")
        except Exception:
            pass
        try:
            os.rename("./version.txt", "./version.txt.bak")
        except Exception:
            pass

        # Start extraction
        error = None
        try:
            with zipfile.ZipFile(__class__.TEMP_PATH) as zip_file:
                zip_file.extractall("./")

            # Copy then delete to overwrite files
            shutil.copytree("./LinguaGacha/", "./", dirs_exist_ok=True)
            shutil.rmtree("./LinguaGacha/", ignore_errors=True)
        except Exception as e:
            error = e
            self.error("", e)

        # Restore backup on failure
        if error is not None:
            try:
                os.remove("./app.exe")
            except Exception:
                pass
            try:
                os.remove("./version.txt")
            except Exception:
                pass
            try:
                os.rename("./app.exe.bak", "./app.exe")
            except Exception:
                pass
            try:
                os.rename("./version.txt.bak", "./version.txt")
            except Exception:
                pass

        # Remove temp file
        try:
            os.remove(__class__.TEMP_PATH)
        except Exception:
            pass

        # Show notification
        self.emit(
            Base.Event.APP_TOAST_SHOW,
            {
                "type": Base.ToastType.SUCCESS,
                "message": Localizer.get().app_new_version_waiting_restart,
                "duration": 60 * 1000,
            },
        )

        # Wait 3 seconds then close app and open release page
        time.sleep(3)
        QDesktopServices.openUrl(QUrl(__class__.RELEASE_URL))
        os.kill(os.getpid(), signal.SIGTERM)

    # Check for updates
    def app_update_check_start_task(self, event: str, data: dict) -> None:
        try:
            # Get update info
            response = httpx.get(__class__.API_URL, timeout=60)
            response.raise_for_status()

            result: dict = response.json()
            a, b, c = re.findall(
                r"v(\d+)\.(\d+)\.(\d+)$", VersionManager.get().get_version()
            )[-1]
            x, y, z = re.findall(
                r"v(\d+)\.(\d+)\.(\d+)$", result.get("tag_name", "v0.0.0")
            )[-1]

            if (
                int(a) < int(x)
                or (int(a) == int(x) and int(b) < int(y))
                or (int(a) == int(x) and int(b) == int(y) and int(c) < int(z))
            ):
                self.set_status(VersionManager.Status.NEW_VERSION)
                self.emit(
                    Base.Event.APP_TOAST_SHOW,
                    {
                        "type": Base.ToastType.SUCCESS,
                        "message": Localizer.get().app_new_version_toast.replace(
                            "{VERSION}", f"v{x}.{y}.{z}"
                        ),
                        "duration": 60 * 1000,
                    },
                )
                self.emit(
                    Base.Event.APP_UPDATE_CHECK_DONE,
                    {
                        "new_version": True,
                    },
                )
        except Exception:
            pass

    # Download update
    def app_update_download_start_task(self, event: str, data: dict) -> None:
        try:
            # Update status
            self.set_status(VersionManager.Status.UPDATING)

            # Get update info
            response = httpx.get(__class__.API_URL, timeout=60)
            response.raise_for_status()

            # Select correct asset based on platform
            plat, arch = get_platform_info()
            assets = response.json().get("assets", [])
            browser_download_url = ""

            if plat == "macos":
                # Find macOS DMG for current architecture
                suffix = f"_macOS_{arch}.dmg"
                for asset in assets:
                    name = asset.get("name", "")
                    if name.endswith(suffix):
                        browser_download_url = asset.get("browser_download_url", "")
                        break
            else:
                # Windows: find .zip file
                for asset in assets:
                    name = asset.get("name", "")
                    if name.endswith(".zip"):
                        browser_download_url = asset.get("browser_download_url", "")
                        break

            if not browser_download_url:
                raise Exception(f"No suitable asset found for {plat}/{arch}")
            with httpx.stream(
                "GET", browser_download_url, timeout=60, follow_redirects=True
            ) as response:
                response.raise_for_status()

                # Get total file size
                total_size: int = int(response.headers.get("Content-Length", 0))
                downloaded_size: int = 0

                # Validity check
                if total_size == 0:
                    raise Exception("Content-Length is 0 ...")

                # Write file and update progress
                os.remove(__class__.TEMP_PATH) if os.path.isfile(
                    __class__.TEMP_PATH
                ) else None
                os.makedirs(os.path.dirname(__class__.TEMP_PATH), exist_ok=True)
                with open(__class__.TEMP_PATH, "wb") as writer:
                    for chunk in response.iter_bytes(chunk_size=1024 * 1024):
                        if chunk is not None:
                            writer.write(chunk)
                            downloaded_size = downloaded_size + len(chunk)
                            if total_size > downloaded_size:
                                self.emit(
                                    Base.Event.APP_UPDATE_DOWNLOAD_UPDATE,
                                    {
                                        "total_size": total_size,
                                        "downloaded_size": downloaded_size,
                                    },
                                )
                            else:
                                self.set_status(VersionManager.Status.DOWNLOADED)
                                self.emit(
                                    Base.Event.APP_TOAST_SHOW,
                                    {
                                        "type": Base.ToastType.SUCCESS,
                                        "message": Localizer.get().app_new_version_success,
                                        "duration": 60 * 1000,
                                    },
                                )
                                self.emit(Base.Event.APP_UPDATE_DOWNLOAD_DONE, {})
        except Exception as e:
            self.set_status(VersionManager.Status.NONE)
            self.emit(
                Base.Event.APP_TOAST_SHOW,
                {
                    "type": Base.ToastType.ERROR,
                    "message": Localizer.get().app_new_version_failure + str(e),
                    "duration": 60 * 1000,
                },
            )
            self.emit(Base.Event.APP_UPDATE_DOWNLOAD_ERROR, {})

    def get_status(self) -> Status:
        with self.lock:
            return self.status

    def set_status(self, status: Status) -> None:
        with self.lock:
            self.status = status

    def get_version(self) -> str:
        with self.lock:
            return self.version

    def set_version(self, version: str) -> None:
        with self.lock:
            self.version = version
