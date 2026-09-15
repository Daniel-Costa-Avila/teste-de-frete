from __future__ import annotations

import os
from dataclasses import dataclass, field
from dataclasses import replace as _replace


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "y", "on"}

def _env_csv(name: str) -> tuple[str, ...]:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return ()
    raw = raw.replace(";", ",")
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    return tuple(parts)

def _env_optional_positive_int(name: str, default: int | None) -> int | None:
    raw = os.getenv(name)
    if raw is None or str(raw).strip() == "":
        return default
    value = int(raw)
    if value <= 0:
        return None
    return value


def _default_parallel_limit() -> int:
    raw = os.getenv("SHEET_PARALLEL_LIMIT")
    if raw is not None and str(raw).strip():
        return max(1, int(raw))
    max_jobs_raw = os.getenv("MAX_CONCURRENT_JOBS")
    if max_jobs_raw is not None and str(max_jobs_raw).strip():
        return max(1, int(max_jobs_raw))
    return 1 if _env_bool("USE_REMOTE", False) else 6


@dataclass(frozen=True)
class Settings:
    use_remote: bool = field(default_factory=lambda: _env_bool("USE_REMOTE", False))
    selenoid_url: str = field(default_factory=lambda: os.getenv("SELENOID_URL", "http://localhost:4444/wd/hub"))
    browser_name: str = field(default_factory=lambda: os.getenv("BROWSER_NAME", "chrome"))
    browser_version: str = field(default_factory=lambda: os.getenv("BROWSER_VERSION", "128.0"))
    selenoid_session_timeout: str = field(default_factory=lambda: os.getenv("SELENOID_SESSION_TIMEOUT", "15m"))

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
    enable_browser_fallback: bool = field(default_factory=lambda: _env_bool("ENABLE_BROWSER_FALLBACK", False))

    # Fechamento automatico de pop-ups, modais e banners de cookie.
    popup_handling: bool = field(default_factory=lambda: _env_bool("POPUP_HANDLING", True))
    popup_settle_seconds: float = field(default_factory=lambda: float(os.getenv("POPUP_SETTLE_SECONDS", "2")))
    popup_max_attempts: int = field(default_factory=lambda: max(1, int(os.getenv("POPUP_MAX_ATTEMPTS", "3"))))
    selenoid_fallback_enabled: bool = field(default_factory=lambda: _env_bool("SELENOID_FALLBACK_ENABLED", False))
    max_concurrent_jobs: int = field(
        default_factory=lambda: max(
            1,
            int(os.getenv("MAX_CONCURRENT_JOBS", "1" if _env_bool("USE_REMOTE", False) else "6")),
        )
    )
    max_batch_lines: int | None = field(default_factory=lambda: _env_optional_positive_int("MAX_BATCH_LINES", None))
    max_sheet_rows: int | None = field(default_factory=lambda: _env_optional_positive_int("MAX_SHEET_ROWS", None))
    max_sheet_jobs: int | None = field(default_factory=lambda: _env_optional_positive_int("MAX_SHEET_JOBS", None))
    max_db_jobs: int | None = field(default_factory=lambda: _env_optional_positive_int("MAX_DB_JOBS", None))
    sheet_parallel_limit: int = field(default_factory=_default_parallel_limit)
    db_parallel_limit: int = field(default_factory=lambda: max(1, int(os.getenv("DB_PARALLEL_LIMIT", str(_default_parallel_limit())))))
    artifacts_dir: str = field(default_factory=lambda: os.getenv("ARTIFACTS_DIR", "artifacts"))
    artifact_retention_days: int = field(default_factory=lambda: int(os.getenv("ARTIFACT_RETENTION_DAYS", "2")))
    results_csv_path: str = field(
        default_factory=lambda: os.getenv(
            "RESULTS_CSV_PATH",
            os.path.join(os.getenv("ARTIFACTS_DIR", "artifacts"), "results.csv"),
        )
    )

    basic_auth_user: str | None = field(default_factory=lambda: (os.getenv("BASIC_AUTH_USER") or "").strip() or None)
    basic_auth_pass: str | None = field(default_factory=lambda: (os.getenv("BASIC_AUTH_PASS") or "").strip() or None)
    allowed_url_hosts: tuple[str, ...] = field(default_factory=lambda: _env_csv("ALLOWED_URL_HOSTS"))

    def with_overrides(self, **kwargs) -> "Settings":
        return _replace(self, **kwargs)
