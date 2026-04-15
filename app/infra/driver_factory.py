import os
import platform
import shutil
import subprocess
from pathlib import Path

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.common.exceptions import WebDriverException
from app.config import Settings


def _run_version(cmd: list[str]) -> str | None:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3, check=False)
        out = (proc.stdout or proc.stderr or "").strip()
        return out or None
    except Exception:
        return None


def _collect_diagnostics(settings: Settings) -> str:
    chrome_candidates = [
        settings.chrome_binary_path,
        shutil.which("chromium"),
        shutil.which("chromium-browser"),
        shutil.which("google-chrome"),
        shutil.which("chrome"),
    ]
    chrome_candidates = [c for c in chrome_candidates if c]

    driver_candidates = [
        settings.chromedriver_path,
        shutil.which("chromedriver"),
    ]
    driver_candidates = [d for d in driver_candidates if d]

    chrome_version = None
    if chrome_candidates:
        chrome_version = _run_version([chrome_candidates[0], "--version"])

    driver_version = None
    if driver_candidates:
        driver_version = _run_version([driver_candidates[0], "--version"])

    has_display = bool(os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY") or os.getenv("MIR_SOCKET"))
    effective_headless = bool(settings.headless or (platform.system() != "Windows" and not has_display))

    return (
        "Diagnostics: "
        f"os={platform.platform()} "
        f"headless={settings.headless} "
        f"effective_headless={effective_headless} "
        f"use_remote={settings.use_remote} "
        f"chrome_path={chrome_candidates[0] if chrome_candidates else None} "
        f"chromedriver_path={driver_candidates[0] if driver_candidates else None} "
        f"chrome_version={chrome_version} "
        f"chromedriver_version={driver_version}"
    )


def build_driver(settings: Settings) -> webdriver.Remote:
    options = Options()

    has_display = bool(os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY") or os.getenv("MIR_SOCKET"))
    effective_headless = bool(settings.headless or (platform.system() != "Windows" and not has_display))

    # stable behavior for ecommerce pages
    options.add_argument("--window-size=1440,1200")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-gpu")
    options.add_argument("--disable-software-rasterizer")
    options.add_argument("--lang=pt-BR")

    if effective_headless:
        # Prefer classic headless for better compatibility across Chromium builds.
        options.add_argument("--headless")

    chrome_bin = settings.chrome_binary_path
    if not chrome_bin:
        chrome_bin = (
            shutil.which("chromium")
            or shutil.which("chromium-browser")
            or shutil.which("google-chrome")
            or shutil.which("chrome")
        )
    if chrome_bin:
        options.binary_location = chrome_bin

    if settings.use_remote:
        options.set_capability("browserName", settings.browser_name)
        options.set_capability("browserVersion", settings.browser_version)
        options.set_capability("selenoid:options", {
            "enableVNC": True,
            "enableVideo": False,
            "sessionTimeout": "5m",
            "name": "freight-test-probel"
        })

        driver = webdriver.Remote(
            command_executor=settings.selenoid_url,
            options=options
        )
    else:
        driver_path = settings.chromedriver_path or shutil.which("chromedriver")
        service_args: list[str] | None = None
        log_output: str | None = None
        if settings.chromedriver_verbose:
            Path(os.path.dirname(settings.chromedriver_log_path) or ".").mkdir(parents=True, exist_ok=True)
            service_args = ["--verbose"]
            log_output = settings.chromedriver_log_path

        service = Service(
            executable_path=driver_path,
            service_args=service_args,
            log_output=log_output,
        )
        try:
            driver = webdriver.Chrome(options=options, service=service)
        except WebDriverException as exc:
            diag = _collect_diagnostics(settings)
            msg = f"{type(exc).__name__}: {exc!r} | {diag}"
            if getattr(exc, "msg", None):
                msg += f" | WebDriver msg: {exc.msg}"
            raise WebDriverException(msg) from exc

    driver.set_page_load_timeout(settings.page_timeout_seconds)
    driver.implicitly_wait(0)  # priorizar explicit wait
    return driver
