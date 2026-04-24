from __future__ import annotations

import os
from dataclasses import dataclass, field
from dataclasses import replace as _replace


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}


@dataclass(frozen=True)
class Settings:
    use_remote: bool = field(default_factory=lambda: _env_bool("USE_REMOTE", False))
    selenoid_url: str = field(default_factory=lambda: os.getenv("SELENOID_URL", "http://localhost:4444/wd/hub"))
    browser_name: str = field(default_factory=lambda: os.getenv("BROWSER_NAME", "chrome"))
    browser_version: str = field(default_factory=lambda: os.getenv("BROWSER_VERSION", "128.0"))

    chrome_binary_path: str | None = field(default_factory=lambda: os.getenv("CHROME_BINARY_PATH") or None)
    chromedriver_path: str | None = field(default_factory=lambda: os.getenv("CHROMEDRIVER_PATH") or None)
    chromedriver_verbose: bool = field(default_factory=lambda: _env_bool("CHROMEDRIVER_VERBOSE", False))
    chromedriver_log_path: str = field(
        default_factory=lambda: os.getenv(
            "CHROMEDRIVER_LOG_PATH",
            os.path.join(os.getenv("ARTIFACTS_DIR", "artifacts"), "chromedriver.log"),
        )
    )

    headless: bool = field(default_factory=lambda: _env_bool("HEADLESS", False))
    page_timeout_seconds: int = field(default_factory=lambda: int(os.getenv("PAGE_TIMEOUT_SECONDS", "60")))
    wait_timeout_seconds: int = field(default_factory=lambda: int(os.getenv("WAIT_TIMEOUT_SECONDS", "25")))
    slow_type_delay_ms: int = field(default_factory=lambda: int(os.getenv("SLOW_TYPE_DELAY_MS", "90")))
    max_concurrent_jobs: int = field(
        default_factory=lambda: max(
            1,
            int(os.getenv("MAX_CONCURRENT_JOBS", "1" if _env_bool("USE_REMOTE", False) else "6")),
        )
    )
    max_batch_lines: int = field(default_factory=lambda: max(1, int(os.getenv("MAX_BATCH_LINES", "200"))))
    max_sheet_rows: int = field(default_factory=lambda: max(1, int(os.getenv("MAX_SHEET_ROWS", "200"))))
    max_sheet_jobs: int = field(default_factory=lambda: max(1, int(os.getenv("MAX_SHEET_JOBS", "200"))))
    sheet_parallel_limit: int = field(default_factory=lambda: max(1, int(os.getenv("SHEET_PARALLEL_LIMIT", "5"))))
    artifacts_dir: str = field(default_factory=lambda: os.getenv("ARTIFACTS_DIR", "artifacts"))
    results_csv_path: str = field(
        default_factory=lambda: os.getenv(
            "RESULTS_CSV_PATH",
            os.path.join(os.getenv("ARTIFACTS_DIR", "artifacts"), "results.csv"),
        )
    )

    def with_overrides(self, **kwargs) -> "Settings":
        return _replace(self, **kwargs)
