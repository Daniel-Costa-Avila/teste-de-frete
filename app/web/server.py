from __future__ import annotations

import hmac
import json
import os
import re
import sqlite3
from io import BytesIO
from queue import Empty, Queue
import threading
import uuid
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse
from pathlib import Path

from flask import Flask, Response, abort, jsonify, redirect, render_template, request, send_file, send_from_directory, url_for
from selenium.common.exceptions import WebDriverException
from werkzeug.middleware.proxy_fix import ProxyFix

from app.config import Settings
from app.infra.driver_factory import build_driver
from app.infra.cep import normalize_cep
from app.infra.artifacts import cleanup_expired_artifacts
from app.infra.results_csv import append_result
from app.infra.results_xlsx import build_results_workbook
from app.infra.product_sheet import parse_products_file
from app.services.freight_test_service import FreightTestService

_ACTIVE_DRIVERS_LOCK = threading.Lock()
_ACTIVE_DRIVERS: dict[str, Any] = {}
_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_DB_PATH = str(_PROJECT_ROOT / "artifacts" / "base_frete.db")
_SCHEDULER_BOOT_LOCK = threading.Lock()
_SCHEDULER_BOOTED = False
_SCHEDULER_LEADER_CONN: sqlite3.Connection | None = None
_WEEKDAY_LABELS = {
    -1: "Todos os dias",
    0: "Segunda-feira",
    1: "Terca-feira",
    2: "Quarta-feira",
    3: "Quinta-feira",
    4: "Sexta-feira",
    5: "Sabado",
    6: "Domingo",
}

def _load_env_file(path: str = ".env") -> None:
    if not os.path.exists(path):
        return

    try:
        with open(path, "r", encoding="utf-8") as handle:
            for raw_line in handle:
                line = raw_line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.startswith("export "):
                    line = line[len("export ") :].lstrip()
                if "=" not in line:
                    continue
                key, value = line.split("=", 1)
                key = key.strip()
                value = value.strip().strip("'").strip('"')
                if not key or key in os.environ:
                    continue
                os.environ[key] = value
    except OSError:
        return


_load_env_file()


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _local_now() -> datetime:
    return datetime.now().astimezone()

def _results_download_name() -> str:
    now = datetime.now()
    return f"busca-frete-{now:%d-%m-%Y}.xlsx"


def _to_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


def _db_path() -> str:
    raw = (os.getenv("FREIGHT_DB_PATH") or "").strip()
    if not raw:
        return _DEFAULT_DB_PATH
    path = Path(raw)
    if path.is_absolute():
        return str(path)
    return str((_PROJECT_ROOT / path).resolve())


def _scheduler_lock_path() -> str:
    base_dir = os.path.dirname(os.path.abspath(_db_path())) or "."
    return os.path.join(base_dir, "scheduler.lock.db")


def _try_acquire_scheduler_leader() -> bool:
    global _SCHEDULER_LEADER_CONN

    if _SCHEDULER_LEADER_CONN is not None:
        return True

    lock_path = _scheduler_lock_path()
    os.makedirs(os.path.dirname(lock_path) or ".", exist_ok=True)
    conn = sqlite3.connect(lock_path, timeout=0.1, isolation_level=None)
    try:
        conn.execute("BEGIN EXCLUSIVE")
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS scheduler_lock (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                owner TEXT NOT NULL,
                acquired_at TEXT NOT NULL
            )
            """
        )
        conn.execute("DELETE FROM scheduler_lock WHERE id = 1")
        conn.execute(
            "INSERT INTO scheduler_lock (id, owner, acquired_at) VALUES (1, ?, ?)",
            (f"{os.getpid()}:{threading.get_ident()}", _utc_now_iso()),
        )
    except sqlite3.OperationalError:
        try:
            conn.close()
        except Exception:
            pass
        return False
    except Exception:
        try:
            conn.close()
        except Exception:
            pass
        raise

    _SCHEDULER_LEADER_CONN = conn
    return True


def _ensure_base_db(path: str) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE IF NOT EXISTS ceps (
                cep TEXT PRIMARY KEY,
                uf TEXT NOT NULL DEFAULT '',
                cidade TEXT,
                regiao TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS products (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                group_name TEXT,
                product_name TEXT,
                product_id TEXT UNIQUE,
                url TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS product_ceps (
                product_id INTEGER NOT NULL,
                cep TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                PRIMARY KEY (product_id, cep),
                FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
                FOREIGN KEY (cep) REFERENCES ceps(cep) ON DELETE RESTRICT
            );
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                run_mode TEXT NOT NULL,
                weekday INTEGER NOT NULL DEFAULT -1,
                hour INTEGER NOT NULL,
                minute INTEGER NOT NULL,
                product_ids_json TEXT NOT NULL DEFAULT '[]',
                ceps_json TEXT NOT NULL DEFAULT '[]',
                active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                updated_at TEXT NOT NULL DEFAULT (datetime('now')),
                last_run_at TEXT,
                last_run_local_date TEXT,
                last_batch_id TEXT,
                last_error TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_products_group_name ON products(group_name);
            CREATE INDEX IF NOT EXISTS idx_product_ceps_cep ON product_ceps(cep);
            CREATE INDEX IF NOT EXISTS idx_schedules_active ON schedules(active);
            """
        )
        existing_columns = {row[1] for row in conn.execute("PRAGMA table_info(schedules)").fetchall()}
        if "weekday" not in existing_columns:
            conn.execute("ALTER TABLE schedules ADD COLUMN weekday INTEGER NOT NULL DEFAULT -1")
        conn.commit()
    finally:
        conn.close()


def _extract_ceps(raw_text: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for m in re.finditer(r"\d{5}-?\d{3}", raw_text or ""):
        cep = normalize_cep(m.group(0))
        if not cep or cep in seen:
            continue
        seen.add(cep)
        out.append(cep)
    return out


def _load_products_and_ceps_from_db(path: str) -> tuple[list[dict[str, Any]], list[str]]:
    if not os.path.exists(path):
        raise ValueError(f"Base de dados nao encontrada: {path}")

    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        products_rows = conn.execute(
            """
            SELECT
                id,
                COALESCE(group_name, '') AS group_name,
                COALESCE(product_name, '') AS product_name,
                COALESCE(product_id, '') AS product_id,
                url
            FROM products
            WHERE url IS NOT NULL AND TRIM(url) <> ''
            ORDER BY id ASC
            """
        ).fetchall()
        cep_rows = conn.execute(
            """
            SELECT cep
            FROM ceps
            WHERE cep IS NOT NULL AND TRIM(cep) <> ''
            ORDER BY cep ASC
            """
        ).fetchall()
    finally:
        conn.close()

    products = [
        {
            "id": int(r["id"]),
            "group": (r["group_name"] or "").strip() or None,
            "product_name": (r["product_name"] or "").strip() or None,
            "product_id": (r["product_id"] or "").strip() or None,
            "url": (r["url"] or "").strip(),
        }
        for r in products_rows
    ]
    ceps = [normalize_cep((r["cep"] or "").strip()) for r in cep_rows]
    ceps = [c for c in ceps if c]
    return products, ceps


def _load_schedules_from_db(path: str) -> list[dict[str, Any]]:
    _ensure_base_db(path)
    conn = sqlite3.connect(path)
    try:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                id,
                name,
                run_mode,
                weekday,
                hour,
                minute,
                product_ids_json,
                ceps_json,
                active,
                created_at,
                updated_at,
                last_run_at,
                last_run_local_date,
                last_batch_id,
                last_error
            FROM schedules
            ORDER BY active DESC, hour ASC, minute ASC, id DESC
            """
        ).fetchall()
    finally:
        conn.close()

    out: list[dict[str, Any]] = []
    for r in rows:
        try:
            product_ids = json.loads(r["product_ids_json"] or "[]")
        except Exception:
            product_ids = []
        try:
            ceps = json.loads(r["ceps_json"] or "[]")
        except Exception:
            ceps = []
        out.append(
            {
                "id": int(r["id"]),
                "name": (r["name"] or "").strip(),
                "run_mode": (r["run_mode"] or "").strip(),
                "weekday": int(r["weekday"] if r["weekday"] is not None else -1),
                "weekday_label": _WEEKDAY_LABELS.get(int(r["weekday"] if r["weekday"] is not None else -1), "Todos os dias"),
                "hour": int(r["hour"]),
                "minute": int(r["minute"]),
                "product_ids": [int(v) for v in product_ids if str(v).strip().isdigit()],
                "ceps": [normalize_cep(str(v)) for v in ceps if normalize_cep(str(v))],
                "active": bool(int(r["active"] or 0)),
                "created_at": r["created_at"],
                "updated_at": r["updated_at"],
                "last_run_at": r["last_run_at"],
                "last_run_local_date": r["last_run_local_date"],
                "last_batch_id": r["last_batch_id"],
                "last_error": r["last_error"],
            }
        )
    return out


def _recent_batches_with_flags(store: "JobStore", limit: int = 20) -> list[dict[str, Any]]:
    recent = store.list_recent_batches(limit=limit)
    out: list[dict[str, Any]] = []
    for b in recent:
        running = (b.get("running_count") or 0) + (b.get("queued_count") or 0) > 0
        status = "RUNNING" if running else ("ERROR" if (b.get("error_count") or 0) > 0 else "DONE")
        row = dict(b)
        row["status"] = status
        out.append(row)
    return out


class JobStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._jobs: dict[str, dict[str, Any]] = {}

    def create(
        self,
        *,
        url: str,
        cep: str,
        headless: bool,
        use_remote: bool,
        batch_id: str | None = None,
        group: str | None = None,
        product_id: str | None = None,
        input_product_name: str | None = None,
    ) -> str:
        job_id = uuid.uuid4().hex
        cep = normalize_cep(cep)
        job = {
            "id": job_id,
            "batch_id": batch_id,
            "group": group,
            "url": url,
            "cep": cep,
            "headless": headless,
            "use_remote": use_remote,
            "product_id": product_id,
            "input_product_name": input_product_name,
            "status": "QUEUED",
            "created_at": _utc_now_iso(),
            "started_at": None,
            "finished_at": None,
            "result": None,
            "error": None,
        }
        with self._lock:
            self._jobs[job_id] = job
        return job_id

    def update(self, job_id: str, **patch: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            job.update(patch)

    def get(self, job_id: str) -> dict[str, Any] | None:
        with self._lock:
            job = self._jobs.get(job_id)
            return dict(job) if job else None

    def list_by_batch(self, batch_id: str) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [j for j in self._jobs.values() if j.get("batch_id") == batch_id]
        jobs.sort(key=lambda j: j["created_at"], reverse=False)
        return [dict(j) for j in jobs]

    def cancel_job(self, job_id: str) -> bool:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return False
            status = str(job.get("status") or "").upper()
            if status in {"DONE", "ERROR", "SUCCESS", "TIMEOUT", "CANCELED", "BLOCKED", "ERRO_NO_LINK"}:
                return True
            if status == "QUEUED":
                job["status"] = "CANCELED"
                job["finished_at"] = _utc_now_iso()
                job["error"] = "Cancelado pelo usuario."
                return True
            job["status"] = "CANCEL_REQUESTED"
            job["error"] = "Cancelamento solicitado pelo usuario."
            return True

    def cancel_batch(self, batch_id: str) -> int:
        jobs = self.list_by_batch(batch_id)
        if not jobs:
            return 0
        changed = 0
        for j in jobs:
            if self.cancel_job(j["id"]):
                changed += 1
        return changed

    def list_recent_batches(self, limit: int = 10) -> list[dict[str, Any]]:
        limit = max(1, int(limit or 10))
        with self._lock:
            jobs = list(self._jobs.values())

        batches: dict[str, dict[str, Any]] = {}
        for job in jobs:
            batch_id = job.get("batch_id")
            if not batch_id:
                continue
            b = batches.get(batch_id)
            created_at = job.get("created_at") or ""
            if not b:
                b = {
                    "batch_id": batch_id,
                    "created_at": created_at,
                    "total_jobs": 0,
                    "done_count": 0,
                    "running_count": 0,
                    "queued_count": 0,
                    "error_count": 0,
                    "last_status": job.get("status"),
                }
                batches[batch_id] = b
            else:
                if created_at and (not b["created_at"] or created_at < b["created_at"]):
                    b["created_at"] = created_at

            b["total_jobs"] += 1
            status = str(job.get("status") or "").upper()
            b["last_status"] = status or b["last_status"]
            if status == "RUNNING":
                b["running_count"] += 1
            elif status == "QUEUED":
                b["queued_count"] += 1
            elif status == "ERROR":
                b["error_count"] += 1
            else:
                b["done_count"] += 1

        recent = list(batches.values())
        recent.sort(key=lambda b: b.get("created_at") or "", reverse=True)
        return recent[:limit]


class BatchProgressStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._progress: dict[str, dict[str, Any]] = {}

    def start(self, batch_id: str, total_items: int, chunk_size: int) -> None:
        with self._lock:
            self._progress[batch_id] = {
                "state": "running",
                "total_items": total_items,
                "chunk_size": chunk_size,
                "current_start": 0,
                "current_end": 0,
                "finished_items": 0,
                "cancel_requested": False,
            }

    def update_chunk(self, batch_id: str, current_start: int, current_end: int, finished_items: int) -> None:
        with self._lock:
            p = self._progress.get(batch_id)
            if not p:
                return
            p["current_start"] = current_start
            p["current_end"] = current_end
            p["finished_items"] = finished_items

    def mark_done(self, batch_id: str) -> None:
        with self._lock:
            p = self._progress.get(batch_id)
            if not p:
                return
            p["state"] = "done"
            p["current_start"] = 0
            p["current_end"] = 0
            p["finished_items"] = p.get("total_items", 0)

    def mark_error(self, batch_id: str) -> None:
        with self._lock:
            p = self._progress.get(batch_id)
            if not p:
                return
            p["state"] = "error"

    def request_cancel(self, batch_id: str) -> None:
        with self._lock:
            p = self._progress.get(batch_id)
            if not p:
                self._progress[batch_id] = {"state": "canceled", "cancel_requested": True}
                return
            p["cancel_requested"] = True
            if p.get("state") == "running":
                p["state"] = "canceled"

    def get(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock:
            p = self._progress.get(batch_id)
            return dict(p) if p else None


def _run_job(store: JobStore, job_id: str) -> None:
    job = store.get(job_id)
    if not job:
        return
    if str(job.get("status") or "").upper() == "CANCELED":
        return

    settings = Settings().with_overrides(
        headless=bool(job["headless"]),
        use_remote=bool(job["use_remote"]),
    )

    store.update(job_id, status="RUNNING", started_at=_utc_now_iso())

    def _run_once(*, run_settings: Settings, attempt: str):
        driver = None
        try:
            latest = store.get(job_id) or {}
            if str(latest.get("status") or "").upper() in {"CANCELED", "CANCEL_REQUESTED"}:
                return None

            driver = build_driver(run_settings)
            with _ACTIVE_DRIVERS_LOCK:
                _ACTIVE_DRIVERS[job_id] = driver

            service = FreightTestService(driver, run_settings)
            result = service.execute(url=job["url"], cep=job["cep"], artifact_prefix=f"{job_id}_{attempt}")
            return result
        finally:
            with _ACTIVE_DRIVERS_LOCK:
                _ACTIVE_DRIVERS.pop(job_id, None)
            if driver is not None:
                try:
                    driver.quit()
                except Exception:
                    pass

    driver = None
    try:
        latest = store.get(job_id) or {}
        if str(latest.get("status") or "").upper() in {"CANCELED", "CANCEL_REQUESTED"}:
            store.update(job_id, status="CANCELED", finished_at=_utc_now_iso(), error="Cancelado pelo usuario.")
            return

        result = _run_once(run_settings=settings, attempt="primary")
        if result is None:
            store.update(job_id, status="CANCELED", finished_at=_utc_now_iso(), error="Cancelado pelo usuario.")
            return

        fallback_enabled = getattr(settings, "enable_browser_fallback", False)
        retryable_statuses = {
            "TIMEOUT",
            "ERROR",
            "BROWSER_DISCONNECTED",
            "ERRO_NO_LINK",
            "FREIGHT_NOT_RETURNED",
            "FREIGHT_BUTTON_NOT_FOUND",
            "CEP_FIELD_NOT_FOUND",
        }
        should_fallback = fallback_enabled and str(result.status or "").upper() in retryable_statuses

        if should_fallback:
            fallback_settings = settings
            # Estratégia padrão:
            # - Se estiver usando remoto (Selenoid), tenta Chrome local.
            # - Se estiver usando local, tenta remoto (Selenoid) quando configurado.
            if settings.use_remote:
                fallback_settings = settings.with_overrides(use_remote=False)
            else:
                if getattr(settings, "selenoid_fallback_enabled", False):
                    fallback_settings = settings.with_overrides(use_remote=True)
                else:
                    fallback_settings = settings

            if fallback_settings != settings:
                fallback = _run_once(run_settings=fallback_settings, attempt="fallback")
                if fallback is not None:
                    if str(fallback.status or "").upper() == "SUCCESS":
                        fallback.errors.append("Tentativa automatica em Chrome (fallback) executada apos falha na primeira tentativa.")
                        result = fallback
                    else:
                        result.errors.append("Fallback em Chrome foi tentado, mas nao resolveu o problema.")
                        if fallback.errors:
                            result.errors.append("Erros do fallback:")
                            result.errors.extend(fallback.errors[:6])

        latest = store.get(job_id) or {}
        if str(latest.get("status") or "").upper() == "CANCEL_REQUESTED":
            store.update(job_id, status="CANCELED", finished_at=_utc_now_iso(), error="Cancelado pelo usuario.")
            return

        store.update(job_id, status=result.status, result=result.to_dict(), finished_at=_utc_now_iso())
        try:
            append_result(settings.results_csv_path, result)
        except Exception as exc:
            store.update(job_id, error=f"Falha ao gravar CSV: {type(exc).__name__}: {exc!r}")
    except WebDriverException as exc:
        latest = store.get(job_id) or {}
        if str(latest.get("status") or "").upper() in {"CANCEL_REQUESTED", "CANCELED"}:
            store.update(job_id, status="CANCELED", error="Cancelado pelo usuario.", finished_at=_utc_now_iso())
            return
        msg = f"{type(exc).__name__}: {exc!r}"
        if getattr(exc, "msg", None):
            msg += f" | WebDriver msg: {exc.msg}"
        if settings.use_remote:
            msg += f" (verifique SELENOID_URL={settings.selenoid_url} e se o Selenoid estÃƒÂ¡ rodando)"
        store.update(job_id, status="ERROR", error=msg, finished_at=_utc_now_iso())
    except Exception as exc:
        latest = store.get(job_id) or {}
        if str(latest.get("status") or "").upper() in {"CANCEL_REQUESTED", "CANCELED"}:
            store.update(job_id, status="CANCELED", error="Cancelado pelo usuario.", finished_at=_utc_now_iso())
            return
        msg = f"{type(exc).__name__}: {exc!r}"
        if settings.use_remote:
            msg += f" (verifique SELENOID_URL={settings.selenoid_url} e se o Selenoid estÃƒÂ¡ rodando)"
        store.update(job_id, status="ERROR", error=msg, finished_at=_utc_now_iso())
    finally:
        # Drivers são criados e finalizados dentro de _run_once.
        pass


def _run_jobs_in_chunks(
    *,
    store: JobStore,
    job_ids: list[str],
    batch_id: str,
    chunk_size: int,
    progress_store: BatchProgressStore,
    logger: Any,
) -> None:
    total = len(job_ids)
    progress_store.start(batch_id=batch_id, total_items=total, chunk_size=chunk_size)

    try:
        parallelism = max(1, int(chunk_size or 1))
        q: Queue[str] = Queue()
        for jid in job_ids:
            q.put(jid)

        finished = 0
        finished_lock = threading.Lock()
        state_lock = threading.Lock()
        state = {"current_start": 1, "current_end": min(parallelism, total)}
        progress_store.update_chunk(
            batch_id,
            current_start=state["current_start"],
            current_end=state["current_end"],
            finished_items=0,
        )
        logger.info(
            "Processando lote em pool (total=%s, workers=%s, batch_id=%s)",
            total,
            parallelism,
            batch_id,
        )

        def _worker() -> None:
            nonlocal finished
            while True:
                p = progress_store.get(batch_id) or {}
                if p.get("cancel_requested") or p.get("state") == "canceled":
                    return
                try:
                    jid = q.get_nowait()
                except Empty:
                    return

                try:
                    _run_job(store, jid)
                finally:
                    with finished_lock:
                        finished += 1
                        current_finished = finished
                    with state_lock:
                        start = state["current_start"]
                        end = state["current_end"]
                        if current_finished >= end and current_finished < total:
                            state["current_start"] = current_finished + 1
                            state["current_end"] = min(current_finished + parallelism, total)
                            start = state["current_start"]
                            end = state["current_end"]
                    progress_store.update_chunk(
                        batch_id,
                        current_start=start if current_finished < total else 0,
                        current_end=end if current_finished < total else 0,
                        finished_items=current_finished,
                    )
                    q.task_done()

        workers: list[threading.Thread] = []
        for _ in range(parallelism):
            t = threading.Thread(target=_worker, daemon=True)
            workers.append(t)
            t.start()

        for t in workers:
            t.join()

        p = progress_store.get(batch_id) or {}
        if p.get("cancel_requested") or p.get("state") == "canceled":
            while True:
                try:
                    remaining_id = q.get_nowait()
                except Empty:
                    break
                store.cancel_job(remaining_id)
                q.task_done()
            return

        progress_store.mark_done(batch_id)
        logger.info("Processamento finalizado para batch_id=%s (total=%s)", batch_id, total)
    except Exception:
        progress_store.mark_error(batch_id)
        logger.exception("Falha no processamento em lotes (batch_id=%s)", batch_id)


def create_app() -> Flask:
    app = Flask(__name__)
    from app.web.brasil_api import create_blueprint
    app.register_blueprint(create_blueprint())
    settings = Settings()
    cleanup_expired_artifacts(settings.artifacts_dir, settings.artifact_retention_days)
    # When mounted behind a reverse proxy (e.g. under /frete), trust X-Forwarded-* headers.
    # This makes url_for() generate correct prefixed URLs for static assets and routes.
    app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)
    public_url_prefix = (os.getenv("PUBLIC_URL_PREFIX") or "").strip()
    if public_url_prefix:
        if not public_url_prefix.startswith("/"):
            public_url_prefix = "/" + public_url_prefix
        if public_url_prefix != "/":
            public_url_prefix = public_url_prefix.rstrip("/")

        class _ForcePublicUrlPrefix:
            def __init__(self, wsgi_app, prefix: str) -> None:
                self._wsgi_app = wsgi_app
                self._prefix = prefix

            def __call__(self, environ, start_response):
                # If a proxy already provides X-Forwarded-Prefix, let ProxyFix handle it.
                if environ.get("HTTP_X_FORWARDED_PREFIX"):
                    return self._wsgi_app(environ, start_response)

                prefix = self._prefix
                if not prefix or prefix == "/":
                    return self._wsgi_app(environ, start_response)

                script_name = (environ.get("SCRIPT_NAME") or "").rstrip("/")
                path_info = environ.get("PATH_INFO") or ""

                # If requests arrive already prefixed, strip it for routing.
                if path_info == prefix:
                    environ["SCRIPT_NAME"] = f"{script_name}{prefix}"
                    environ["PATH_INFO"] = "/"
                elif path_info.startswith(prefix + "/"):
                    environ["SCRIPT_NAME"] = f"{script_name}{prefix}"
                    environ["PATH_INFO"] = path_info[len(prefix) :] or "/"
                elif not environ.get("SCRIPT_NAME"):
                    # If requests arrive without the prefix (e.g. Tailscale Serve strips it),
                    # still force it for URL generation.
                    environ["SCRIPT_NAME"] = prefix

                return self._wsgi_app(environ, start_response)

        app.wsgi_app = _ForcePublicUrlPrefix(app.wsgi_app, public_url_prefix)
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5MB
    app_settings = Settings()
    store = JobStore()
    batch_progress = BatchProgressStore()

    auth_user = (app_settings.basic_auth_user or "").strip()
    auth_pass = (app_settings.basic_auth_pass or "").strip()
    auth_enabled = bool(auth_user and auth_pass)
    if (app_settings.basic_auth_user or app_settings.basic_auth_pass) and not auth_enabled:
        app.logger.warning("BASIC_AUTH_USER/BASIC_AUTH_PASS incompleto: autenticação desativada.")

    allowed_hosts = tuple(h.strip().lower() for h in (app_settings.allowed_url_hosts or ()) if h.strip())

    def _is_url_allowed(url: str) -> bool:
        if not allowed_hosts:
            return True
        try:
            parsed = urlparse(url)
        except Exception:
            return False
        if parsed.scheme not in {"http", "https"}:
            return False
        host = (parsed.hostname or "").strip().lower()
        if not host:
            return False

        for entry in allowed_hosts:
            e = entry
            if e.startswith("*."):
                e = e[1:]  # "*.example.com" -> ".example.com"
            if e.startswith("."):
                base = e.lstrip(".")
                if host == base or host.endswith(e):
                    return True
                continue
            if host == e:
                return True
        return False

    def _create_batch_from_products_and_ceps(
        *,
        products: list[dict[str, Any]],
        ceps: list[str],
        parallel_limit: int,
        trigger_label: str,
    ) -> str:
        if not products:
            raise ValueError("Nenhum produto valido para executar.")
        if not ceps:
            raise ValueError("Nenhum CEP valido para executar.")

        total_jobs = len(products) * len(ceps)
        if app_settings.max_db_jobs is not None and total_jobs > app_settings.max_db_jobs:
            raise ValueError(
                f"Execucao gera muitas execucoes ({total_jobs}); maximo permitido: {app_settings.max_db_jobs}."
            )

        batch_id = uuid.uuid4().hex
        created: list[str] = []
        for product in products:
            url = (product.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            if not _is_url_allowed(url):
                continue
            for cep in ceps:
                job_id = store.create(
                    url=url,
                    cep=normalize_cep(cep),
                    headless=app_settings.headless,
                    use_remote=app_settings.use_remote,
                    batch_id=batch_id,
                    group=(product.get("group") or None),
                    product_id=(product.get("product_id") or None),
                    input_product_name=(product.get("product_name") or None),
                )
                created.append(job_id)

        if not created:
            if allowed_hosts:
                raise ValueError("Nenhuma URL permitida encontrada para a execucao.")
            raise ValueError("Nenhuma URL valida encontrada para a execucao.")

        manager = threading.Thread(
            target=_run_jobs_in_chunks,
            kwargs={
                "store": store,
                "job_ids": created,
                "batch_id": batch_id,
                "chunk_size": parallel_limit,
                "progress_store": batch_progress,
                "logger": app.logger,
            },
            daemon=True,
            name=f"batch-{trigger_label}-{batch_id[:8]}",
        )
        manager.start()
        return batch_id

    def _scheduler_products_and_ceps(schedule: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
        db_products, db_ceps = _load_products_and_ceps_from_db(_db_path())
        if str(schedule.get("run_mode") or "").lower() == "full":
            return db_products, db_ceps

        selected_product_ids = {int(v) for v in (schedule.get("product_ids") or [])}
        selected_ceps = {normalize_cep(v) for v in (schedule.get("ceps") or []) if normalize_cep(v)}
        products = [p for p in db_products if int(p.get("id") or 0) in selected_product_ids]
        ceps = [c for c in db_ceps if c in selected_ceps]
        return products, ceps

    def _run_schedule_once(schedule: dict[str, Any]) -> str:
        products, ceps = _scheduler_products_and_ceps(schedule)
        return _create_batch_from_products_and_ceps(
            products=products,
            ceps=ceps,
            parallel_limit=app_settings.db_parallel_limit,
            trigger_label=f"schedule-{schedule['id']}",
        )

    def _scheduler_loop() -> None:
        while True:
            try:
                now_local = _local_now()
                today = now_local.strftime("%Y-%m-%d")
                schedules = _load_schedules_from_db(_db_path())
                for schedule in schedules:
                    if not schedule.get("active"):
                        continue
                    if schedule.get("last_run_local_date") == today:
                        continue
                    weekday = int(schedule.get("weekday") if schedule.get("weekday") is not None else -1)
                    if weekday >= 0 and weekday != now_local.weekday():
                        continue
                    if int(schedule.get("hour") or 0) != now_local.hour:
                        continue
                    if int(schedule.get("minute") or 0) != now_local.minute:
                        continue

                    path = _db_path()
                    conn = sqlite3.connect(path)
                    try:
                        try:
                            batch_id = _run_schedule_once(schedule)
                            conn.execute(
                                """
                                UPDATE schedules
                                SET
                                    last_run_at = ?,
                                    last_run_local_date = ?,
                                    last_batch_id = ?,
                                    last_error = '',
                                    updated_at = datetime('now')
                                WHERE id = ?
                                """,
                                (_utc_now_iso(), today, batch_id, schedule["id"]),
                            )
                            app.logger.info("Agendamento %s executado com batch_id=%s", schedule["id"], batch_id)
                        except Exception as exc:
                            conn.execute(
                                """
                                UPDATE schedules
                                SET
                                    last_run_at = ?,
                                    last_run_local_date = ?,
                                    last_error = ?,
                                    updated_at = datetime('now')
                                WHERE id = ?
                                """,
                                (_utc_now_iso(), today, f"{type(exc).__name__}: {exc}", schedule["id"]),
                            )
                            app.logger.exception("Falha ao executar agendamento id=%s", schedule["id"])
                        conn.commit()
                    finally:
                        conn.close()
            except Exception:
                app.logger.exception("Falha no loop do agendador automatico.")
            time.sleep(30)

    @app.before_request
    def _enforce_basic_auth() -> Response | None:
        if not auth_enabled:
            return None
        if request.endpoint == "health":
            return None
        auth = request.authorization
        if not auth:
            return Response(status=401, headers={"WWW-Authenticate": 'Basic realm="Frete"'})
        if not (
            hmac.compare_digest(auth.username or "", auth_user)
            and hmac.compare_digest(auth.password or "", auth_pass)
        ):
            return Response(status=401, headers={"WWW-Authenticate": 'Basic realm="Frete"'})
        return None

    global _SCHEDULER_BOOTED
    with _SCHEDULER_BOOT_LOCK:
        if not _SCHEDULER_BOOTED:
            if _try_acquire_scheduler_leader():
                scheduler_thread = threading.Thread(target=_scheduler_loop, daemon=True, name="schedule-runner")
                scheduler_thread.start()
                app.logger.info("Agendador automatico ativado neste processo.")
            else:
                app.logger.info("Agendador automatico ja esta ativo em outro processo.")
            _SCHEDULER_BOOTED = True

    def _format_money(amount: float, currency: str) -> str:
        currency = (currency or "BRL").upper()
        if currency == "BRL":
            # 1234.5 -> R$ 1.234,50
            s = f"{amount:,.2f}"
            s = s.replace(",", "X").replace(".", ",").replace("X", ".")
            return f"R$ {s}"
        return f"{currency} {amount:.2f}"

    @app.template_filter("format_freight_price")
    def format_freight_price(freight: Any) -> str:
        if not isinstance(freight, dict):
            return "-"
        kind = str(freight.get("price_kind") or "").upper()
        price = freight.get("price")
        currency = str(freight.get("currency") or "BRL")

        if kind == "FREE" or price == 0 or price == 0.0:
            return "Grátis"
        if price is None:
            return str(freight.get("price_text") or "-")
        try:
            return _format_money(float(price), currency)
        except Exception:
            return str(freight.get("price_text") or price)

    @app.template_filter("weekday_label")
    def weekday_label(value: Any) -> str:
        try:
            weekday = int(value)
        except Exception:
            weekday = -1
        return _WEEKDAY_LABELS.get(weekday, "Todos os dias")

    @app.get("/")
    def index():
        default_url = "https://probel.com.br/colchao-casal-mola-ensacada-probel-excede-premium/p"
        recent_batches = _recent_batches_with_flags(store, limit=10)
        last_batch_id = (request.cookies.get("last_batch_id") or "").strip()
        last_batch: dict[str, Any] | None = None
        if last_batch_id:
            jobs = store.list_by_batch(last_batch_id)
            if jobs:
                done_count = sum(1 for j in jobs if j["status"] in {"DONE", "ERROR"})
                running_count = sum(1 for j in jobs if j["status"] == "RUNNING")
                queued_count = sum(1 for j in jobs if j["status"] == "QUEUED")
                running = any(j["status"] in {"QUEUED", "RUNNING"} for j in jobs)
                last_batch = {
                    "batch_id": last_batch_id,
                    "running": running,
                    "done_count": done_count,
                    "running_count": running_count,
                    "queued_count": queued_count,
                }
        db_products_count = 0
        db_ceps_count = 0
        db_total_jobs = 0
        db_products_preview: list[dict[str, Any]] = []
        db_ceps_preview: list[str] = []
        schedules: list[dict[str, Any]] = []
        db_error: str | None = None
        db_notice = (request.args.get("db_notice") or "").strip()
        schedule_notice = (request.args.get("schedule_notice") or "").strip()
        try:
            _ensure_base_db(_db_path())
            db_products, db_ceps = _load_products_and_ceps_from_db(_db_path())
            schedules = _load_schedules_from_db(_db_path())
            db_products_count = len(db_products)
            db_ceps_count = len(db_ceps)
            db_total_jobs = db_products_count * db_ceps_count
            db_products_preview = db_products[:500]
            db_ceps_preview = db_ceps[:500]
        except Exception as exc:
            db_error = str(exc)

        return render_template(
            "index.html",
            default_url=default_url,
            default_cep="79800-002",
            recent_batches=recent_batches,
            last_batch=last_batch,
            db_products_count=db_products_count,
            db_ceps_count=db_ceps_count,
            db_total_jobs=db_total_jobs,
            db_max_jobs=app_settings.max_db_jobs,
            db_products_preview=db_products_preview,
            db_ceps_preview=db_ceps_preview,
            schedules=schedules,
            db_error=db_error,
            db_notice=db_notice,
            schedule_notice=schedule_notice,
        )

    @app.get("/manual")
    def manual():
        return render_template("manual.html")

    @app.post("/run")
    def run_test():
        url = (request.form.get("url") or "").strip()
        cep = normalize_cep(request.form.get("cep"))
        if not url or not cep:
            abort(400, "Informe URL e CEP.")
        if not _is_url_allowed(url):
            abort(400, "URL não permitida. Ajuste ALLOWED_URL_HOSTS no servidor.")

        use_remote = app_settings.use_remote

        job_id = store.create(url=url, cep=cep, headless=app_settings.headless, use_remote=use_remote)
        t = threading.Thread(target=_run_job, args=(store, job_id), daemon=True)
        t.start()
        resp = redirect(url_for("run_detail", job_id=job_id))
        resp.set_cookie("last_job_id", job_id, max_age=7 * 24 * 3600, samesite="Lax")
        return resp

    @app.get("/templates/produtos.xlsx")
    def template_produtos_xlsx():
        path = os.path.abspath(os.path.join(app_settings.artifacts_dir, "produtos_entrada_template.xlsx"))
        if not os.path.exists(path):
            abort(404)
        return send_file(path, as_attachment=True, download_name="produtos_entrada_template.xlsx")

    @app.get("/templates/produtos.csv")
    def template_produtos_csv():
        path = os.path.abspath(os.path.join(app_settings.artifacts_dir, "produtos_entrada_template.csv"))
        if not os.path.exists(path):
            abort(404)
        return send_file(path, as_attachment=True, download_name="produtos_entrada_template.csv")

    @app.post("/run-products-sheet")
    def run_products_sheet():
        cep_default = normalize_cep(request.form.get("cep"))
        if not cep_default:
            abort(400, "Informe o CEP.")

        file = request.files.get("sheet")
        if not file or not file.filename:
            abort(400, "Envie a planilha (.xlsx ou .csv).")

        use_remote = app_settings.use_remote

        data = file.read()
        try:
            rows = parse_products_file(file.filename, data)
        except ValueError as exc:
            abort(400, str(exc))

        if not rows:
            abort(400, "Planilha sem linhas válidas.")
        if app_settings.max_sheet_rows is not None and len(rows) > app_settings.max_sheet_rows:
            abort(400, f"Planilha muito grande (máximo: {app_settings.max_sheet_rows} linhas).")

        batch_id = uuid.uuid4().hex
        total_jobs = 0
        for r in rows:
            ceps = getattr(r, "ceps", None) or ()
            total_jobs += len(ceps) if ceps else 1
        if app_settings.max_sheet_jobs is not None and total_jobs > app_settings.max_sheet_jobs:
            abort(
                400,
                f"Planilha gera muitas execuções (máximo: {app_settings.max_sheet_jobs}). Reduza CEPs/linhas.",
            )

        created: list[str] = []
        for r in rows:
            url = (r.url or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            if not _is_url_allowed(url):
                continue
            group = (getattr(r, "group", "") or "").strip() or None
            ceps = getattr(r, "ceps", None) or ()
            ceps_to_run = list(ceps) if ceps else [cep_default]
            for cep in ceps_to_run:
                job_id = store.create(
                    url=url,
                    cep=normalize_cep(cep),
                    headless=app_settings.headless,
                    use_remote=use_remote,
                    batch_id=batch_id,
                    group=group,
                    product_id=(r.product_id or "").strip() or None,
                    input_product_name=(r.product_name or "").strip() or None,
                )
                created.append(job_id)

        if not created:
            if allowed_hosts:
                abort(400, "Nenhuma URL válida/permitida encontrada na planilha (verifique ALLOWED_URL_HOSTS).")
            abort(400, "Nenhuma URL válida encontrada na planilha.")

        manager = threading.Thread(
            target=_run_jobs_in_chunks,
            kwargs={
                "store": store,
                "job_ids": created,
                "batch_id": batch_id,
                "chunk_size": app_settings.sheet_parallel_limit,
                "progress_store": batch_progress,
                "logger": app.logger,
            },
            daemon=True,
        )
        manager.start()
        resp = redirect(url_for("batch_detail", batch_id=batch_id))
        resp.set_cookie("last_batch_id", batch_id, max_age=7 * 24 * 3600, samesite="Lax")
        return resp

    @app.post("/run-products-db")
    def run_products_db():
        use_remote = app_settings.use_remote

        try:
            db_products, db_ceps = _load_products_and_ceps_from_db(_db_path())
        except ValueError as exc:
            abort(400, str(exc))
        except Exception as exc:
            abort(500, f"Falha ao ler base local: {type(exc).__name__}: {exc!r}")

        if not db_products:
            abort(400, "Base sem produtos validos para executar.")
        if not db_ceps:
            abort(400, "Base sem CEPs validos para executar.")

        total_jobs = len(db_products) * len(db_ceps)
        if app_settings.max_db_jobs is not None and total_jobs > app_settings.max_db_jobs:
            abort(
                400,
                (
                    f"Base gera muitas execucoes ({total_jobs}). "
                    f"Maximo permitido: {app_settings.max_db_jobs}. "
                    "Ajuste MAX_DB_JOBS ou reduza produtos/CEPs."
                ),
            )

        batch_id = uuid.uuid4().hex
        created: list[str] = []
        for product in db_products:
            url = (product.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            if not _is_url_allowed(url):
                continue
            for cep in db_ceps:
                job_id = store.create(
                    url=url,
                    cep=normalize_cep(cep),
                    headless=app_settings.headless,
                    use_remote=use_remote,
                    batch_id=batch_id,
                    group=(product.get("group") or None),
                    product_id=(product.get("product_id") or None),
                    input_product_name=(product.get("product_name") or None),
                )
                created.append(job_id)

        if not created:
            if allowed_hosts:
                abort(400, "Nenhuma URL permitida na base (verifique ALLOWED_URL_HOSTS).")
            abort(400, "Nenhuma URL valida encontrada na base.")

        manager = threading.Thread(
            target=_run_jobs_in_chunks,
            kwargs={
                "store": store,
                "job_ids": created,
                "batch_id": batch_id,
                "chunk_size": app_settings.db_parallel_limit,
                "progress_store": batch_progress,
                "logger": app.logger,
            },
            daemon=True,
        )
        manager.start()
        resp = redirect(url_for("batch_detail", batch_id=batch_id))
        resp.set_cookie("last_batch_id", batch_id, max_age=7 * 24 * 3600, samesite="Lax")
        return resp

    @app.post("/run-products-db-selected")
    def run_products_db_selected():
        use_remote = app_settings.use_remote
        selected_product_ids = {int(v) for v in request.form.getlist("product_ids") if str(v).strip().isdigit()}
        selected_ceps = {normalize_cep(v) for v in request.form.getlist("ceps") if normalize_cep(v)}

        if not selected_product_ids:
            abort(400, "Selecione ao menos um link/produto da base.")
        if not selected_ceps:
            abort(400, "Selecione ao menos um CEP da base.")

        try:
            db_products, db_ceps = _load_products_and_ceps_from_db(_db_path())
        except ValueError as exc:
            abort(400, str(exc))
        except Exception as exc:
            abort(500, f"Falha ao ler base local: {type(exc).__name__}: {exc!r}")

        products = [p for p in db_products if int(p.get("id") or 0) in selected_product_ids]
        ceps = [c for c in db_ceps if c in selected_ceps]
        if not products:
            abort(400, "Nenhum produto valido encontrado na selecao.")
        if not ceps:
            abort(400, "Nenhum CEP valido encontrado na selecao.")

        total_jobs = len(products) * len(ceps)
        if app_settings.max_db_jobs is not None and total_jobs > app_settings.max_db_jobs:
            abort(400, f"Selecao gera muitas execucoes ({total_jobs}); maximo permitido: {app_settings.max_db_jobs}.")

        batch_id = uuid.uuid4().hex
        created: list[str] = []
        for product in products:
            url = (product.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            if not _is_url_allowed(url):
                continue
            for cep in ceps:
                job_id = store.create(
                    url=url,
                    cep=normalize_cep(cep),
                    headless=app_settings.headless,
                    use_remote=use_remote,
                    batch_id=batch_id,
                    group=(product.get("group") or None),
                    product_id=(product.get("product_id") or None),
                    input_product_name=(product.get("product_name") or None),
                )
                created.append(job_id)

        if not created:
            if allowed_hosts:
                abort(400, "Nenhuma URL permitida na selecao (verifique ALLOWED_URL_HOSTS).")
            abort(400, "Nenhuma URL valida encontrada na selecao.")

        manager = threading.Thread(
            target=_run_jobs_in_chunks,
            kwargs={
                "store": store,
                "job_ids": created,
                "batch_id": batch_id,
                "chunk_size": app_settings.db_parallel_limit,
                "progress_store": batch_progress,
                "logger": app.logger,
            },
            daemon=True,
        )
        manager.start()
        resp = redirect(url_for("batch_detail", batch_id=batch_id))
        resp.set_cookie("last_batch_id", batch_id, max_age=7 * 24 * 3600, samesite="Lax")
        return resp

    @app.post("/db/add-ceps")
    def db_add_ceps():
        raw = (request.form.get("ceps_text") or "").strip()
        ceps = _extract_ceps(raw)
        if not ceps:
            abort(400, "Informe ao menos um CEP valido para cadastrar.")

        path = _db_path()
        _ensure_base_db(path)
        conn = sqlite3.connect(path)
        inserted = 0
        try:
            for cep in ceps:
                cur = conn.execute(
                    "INSERT OR IGNORE INTO ceps (cep, uf, cidade, regiao) VALUES (?, '', '', '')",
                    (cep,),
                )
                if cur.rowcount:
                    inserted += 1
            conn.commit()
        finally:
            conn.close()

        return redirect(url_for("index", _anchor="db-manage", db_notice=f"CEPs processados: {len(ceps)} | novos: {inserted}"))

    @app.post("/db/add-product")
    def db_add_product():
        group_name = (request.form.get("group_name") or "").strip() or None
        product_name = (request.form.get("product_name") or "").strip() or None
        product_id = (request.form.get("product_id") or "").strip() or None
        url = (request.form.get("url") or "").strip()
        ceps_raw = (request.form.get("ceps_for_product") or "").strip()

        if not url or not url.startswith(("http://", "https://")):
            abort(400, "Informe um link valido (http/https).")

        ceps = _extract_ceps(ceps_raw)
        path = _db_path()
        _ensure_base_db(path)
        conn = sqlite3.connect(path)
        try:
            conn.execute(
                """
                INSERT INTO products (group_name, product_name, product_id, url)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(url) DO UPDATE SET
                    group_name=excluded.group_name,
                    product_name=excluded.product_name,
                    product_id=COALESCE(excluded.product_id, products.product_id)
                """,
                (group_name, product_name, product_id, url),
            )
            product_db_id = conn.execute("SELECT id FROM products WHERE url = ?", (url,)).fetchone()[0]

            linked = 0
            for cep in ceps:
                conn.execute("INSERT OR IGNORE INTO ceps (cep, uf, cidade, regiao) VALUES (?, '', '', '')", (cep,))
                cur = conn.execute(
                    "INSERT OR IGNORE INTO product_ceps (product_id, cep) VALUES (?, ?)",
                    (product_db_id, cep),
                )
                if cur.rowcount:
                    linked += 1

            conn.commit()
        finally:
            conn.close()

        msg = "Produto salvo na base."
        if ceps:
            msg += f" CEPs vinculados: {linked}."
        return redirect(url_for("index", _anchor="db-manage", db_notice=msg))

    @app.post("/schedules")
    def schedule_create():
        name = (request.form.get("schedule_name") or "").strip() or "Pesquisa automatica"
        run_mode = (request.form.get("schedule_mode") or "full").strip().lower()
        weekday_raw = (request.form.get("schedule_weekday") or "-1").strip()
        hour_raw = (request.form.get("schedule_hour") or "").strip()
        minute_raw = (request.form.get("schedule_minute") or "").strip()

        if run_mode not in {"full", "selected"}:
            abort(400, "Modo de agendamento invalido.")
        if not weekday_raw.lstrip("-").isdigit():
            abort(400, "Dia da semana invalido.")
        if not hour_raw.isdigit() or not minute_raw.isdigit():
            abort(400, "Informe hora e minuto validos.")

        weekday = int(weekday_raw)
        hour = int(hour_raw)
        minute = int(minute_raw)
        if weekday < -1 or weekday > 6:
            abort(400, "Dia da semana invalido.")
        if hour < 0 or hour > 23 or minute < 0 or minute > 59:
            abort(400, "Horario invalido. Use hora 0-23 e minuto 0-59.")

        product_ids: list[int] = []
        ceps: list[str] = []
        if run_mode == "selected":
            product_ids = sorted({int(v) for v in request.form.getlist("schedule_product_ids") if str(v).strip().isdigit()})
            ceps = sorted({normalize_cep(v) for v in request.form.getlist("schedule_ceps") if normalize_cep(v)})
            if not product_ids:
                abort(400, "Selecione ao menos um produto para o agendamento.")
            if not ceps:
                abort(400, "Selecione ao menos um CEP para o agendamento.")

        path = _db_path()
        _ensure_base_db(path)
        conn = sqlite3.connect(path)
        try:
            conn.execute(
                """
                INSERT INTO schedules (name, run_mode, weekday, hour, minute, product_ids_json, ceps_json, active, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, datetime('now'))
                """,
                (name, run_mode, weekday, hour, minute, json.dumps(product_ids), json.dumps(ceps)),
            )
            conn.commit()
        finally:
            conn.close()

        return redirect(url_for("index", _anchor="scheduler", schedule_notice="Agendamento salvo com sucesso."))

    @app.post("/schedules/<int:schedule_id>/toggle")
    def schedule_toggle(schedule_id: int):
        path = _db_path()
        _ensure_base_db(path)
        conn = sqlite3.connect(path)
        try:
            row = conn.execute("SELECT active FROM schedules WHERE id = ?", (schedule_id,)).fetchone()
            if not row:
                abort(404)
            new_active = 0 if int(row[0] or 0) else 1
            conn.execute(
                "UPDATE schedules SET active = ?, updated_at = datetime('now') WHERE id = ?",
                (new_active, schedule_id),
            )
            conn.commit()
        finally:
            conn.close()
        return redirect(url_for("index", _anchor="scheduler", schedule_notice="Status do agendamento atualizado."))

    @app.post("/schedules/<int:schedule_id>/run")
    def schedule_run_now(schedule_id: int):
        schedules = _load_schedules_from_db(_db_path())
        schedule = next((s for s in schedules if s["id"] == schedule_id), None)
        if not schedule:
            abort(404)
        path = _db_path()
        try:
            batch_id = _run_schedule_once(schedule)
        except Exception as exc:
            conn = sqlite3.connect(path)
            try:
                conn.execute(
                    """
                    UPDATE schedules
                    SET
                        last_run_at = ?,
                        last_run_local_date = ?,
                        last_error = ?,
                        updated_at = datetime('now')
                    WHERE id = ?
                    """,
                    (_utc_now_iso(), _local_now().strftime("%Y-%m-%d"), f"{type(exc).__name__}: {exc}", schedule_id),
                )
                conn.commit()
            finally:
                conn.close()
            return redirect(url_for("index", _anchor="scheduler", schedule_notice=f"Falha ao rodar agendamento: {exc}"))

        conn = sqlite3.connect(path)
        try:
            conn.execute(
                """
                UPDATE schedules
                SET
                    last_run_at = ?,
                    last_run_local_date = ?,
                    last_batch_id = ?,
                    last_error = '',
                    updated_at = datetime('now')
                WHERE id = ?
                """,
                (_utc_now_iso(), _local_now().strftime("%Y-%m-%d"), batch_id, schedule_id),
            )
            conn.commit()
        finally:
            conn.close()
        resp = redirect(url_for("batch_detail", batch_id=batch_id))
        resp.set_cookie("last_batch_id", batch_id, max_age=7 * 24 * 3600, samesite="Lax")
        return resp

    @app.post("/schedules/<int:schedule_id>/delete")
    def schedule_delete(schedule_id: int):
        path = _db_path()
        _ensure_base_db(path)
        conn = sqlite3.connect(path)
        try:
            conn.execute("DELETE FROM schedules WHERE id = ?", (schedule_id,))
            conn.commit()
        finally:
            conn.close()
        return redirect(url_for("index", _anchor="scheduler", schedule_notice="Agendamento removido."))

    @app.get("/results")
    def results_center():
        recent_batches = _recent_batches_with_flags(store, limit=30)
        schedules = _load_schedules_from_db(_db_path())
        return render_template("results.html", recent_batches=recent_batches, schedules=schedules)

    @app.get("/batches/<batch_id>")
    def batch_detail(batch_id: str):
        jobs = store.list_by_batch(batch_id)
        if not jobs:
            abort(404)

        progress = batch_progress.get(batch_id)
        done_count = sum(1 for j in jobs if j["status"] not in {"QUEUED", "RUNNING"})
        running_count = sum(1 for j in jobs if j["status"] == "RUNNING")
        queued_count = sum(1 for j in jobs if j["status"] == "QUEUED")

        running = any(j["status"] in {"QUEUED", "RUNNING"} for j in jobs)
        return render_template(
            "batch.html",
            batch_id=batch_id,
            jobs=jobs,
            auto_refresh=running,
            progress=progress,
            done_count=done_count,
            running_count=running_count,
            queued_count=queued_count,
        )

    @app.post("/batches/<batch_id>/cancel")
    def batch_cancel(batch_id: str):
        batch_progress.request_cancel(batch_id)
        store.cancel_batch(batch_id)
        jobs = store.list_by_batch(batch_id)
        with _ACTIVE_DRIVERS_LOCK:
            drivers = [(_ACTIVE_DRIVERS.get(j["id"])) for j in jobs if isinstance(j, dict)]
        for driver in drivers:
            if driver is None:
                continue
            try:
                driver.quit()
            except Exception:
                pass
        return redirect(url_for("batch_detail", batch_id=batch_id))

    @app.get("/batches/<batch_id>/results.xlsx")
    def batch_results_xlsx(batch_id: str):
        jobs = store.list_by_batch(batch_id)
        if not jobs:
            abort(404)

        data = build_results_workbook(jobs)
        return send_file(
            BytesIO(data),
            as_attachment=True,
            download_name=_results_download_name(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @app.get("/runs/<job_id>")
    def run_detail(job_id: str):
        job = store.get(job_id)
        if not job:
            abort(404)

        result = job.get("result")
        artifacts = {}
        if isinstance(result, dict):
            artifacts = (result.get("artifacts") or {}) if isinstance(result.get("artifacts"), dict) else {}

        def _artifact_link(path_value: str | None) -> str | None:
            if not path_value:
                return None
            settings = Settings()
            root = os.path.abspath(settings.artifacts_dir)
            abs_path = os.path.abspath(path_value)
            try:
                if os.path.commonpath([abs_path, root]) != root:
                    return None
            except Exception:
                return None
            rel = os.path.relpath(abs_path, root).replace("\\", "/")
            if rel.startswith("../"):
                return None
            return url_for("artifact", filename=rel)

        screenshot_url = _artifact_link(artifacts.get("screenshot"))
        html_url = _artifact_link(artifacts.get("html"))

        auto_refresh = job["status"] in {"QUEUED", "RUNNING"} and job.get("finished_at") is None
        json_pretty = json.dumps(result, ensure_ascii=False, indent=2) if isinstance(result, dict) else None

        return render_template(
            "run.html",
            job=job,
            auto_refresh=auto_refresh,
            json_pretty=json_pretty,
            screenshot_url=screenshot_url,
            html_url=html_url,
        )

    @app.post("/runs/<job_id>/cancel")
    def run_cancel(job_id: str):
        store.cancel_job(job_id)
        with _ACTIVE_DRIVERS_LOCK:
            driver = _ACTIVE_DRIVERS.get(job_id)
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass
        return redirect(url_for("run_detail", job_id=job_id))

    @app.get("/runs/<job_id>/results.xlsx")
    def run_results_xlsx(job_id: str):
        job = store.get(job_id)
        if not job:
            abort(404)

        data = build_results_workbook([job])
        return send_file(
            BytesIO(data),
            as_attachment=True,
            download_name=_results_download_name(),
            mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    @app.get("/api/runs/<job_id>")
    def run_api(job_id: str):
        job = store.get(job_id)
        if not job:
            abort(404)
        return jsonify(job)

    @app.get("/artifacts/<path:filename>")
    def artifact(filename: str):
        filename = (filename or "").replace("\\", "/")
        if not filename or filename.startswith("../") or "/../" in filename:
            abort(404)
        directory = os.path.abspath(app_settings.artifacts_dir)
        return send_from_directory(directory, filename, as_attachment=False)

    @app.get("/health")
    def health():
        return jsonify({"ok": True})

    return app


def main() -> None:
    host = os.getenv("HOST", "127.0.0.1")
    port = int(os.getenv("PORT", "5000"))
    debug = _to_bool(os.getenv("DEBUG", "1"))

    app = create_app()
    try:
        print(f"Starting server on http://{host}:{port} (debug={debug})")
        app.run(host=host, port=port, debug=debug, threaded=True)
    except OSError as exc:
        winerror = getattr(exc, "winerror", None)
        errno = getattr(exc, "errno", None)
        print(
            "Failed to start server. "
            f"host={host!r} port={port!r} errno={errno!r} winerror={winerror!r} error={exc!r}"
        )
        raise
