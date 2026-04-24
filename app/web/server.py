from __future__ import annotations

import json
import os
from io import BytesIO
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, send_from_directory, url_for
from selenium.common.exceptions import WebDriverException

from app.config import Settings
from app.infra.driver_factory import build_driver
from app.infra.cep import normalize_cep
from app.infra.results_csv import append_result
from app.infra.results_xlsx import build_results_workbook
from app.infra.product_sheet import parse_products_file
from app.services.freight_test_service import FreightTestService


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _to_bool(value: str | None) -> bool:
    if value is None:
        return False
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


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

    def get(self, batch_id: str) -> dict[str, Any] | None:
        with self._lock:
            p = self._progress.get(batch_id)
            return dict(p) if p else None


def _run_job(store: JobStore, job_id: str) -> None:
    job = store.get(job_id)
    if not job:
        return

    settings = Settings().with_overrides(
        headless=bool(job["headless"]),
        use_remote=bool(job["use_remote"]),
    )

    store.update(job_id, status="RUNNING", started_at=_utc_now_iso())

    driver = None
    try:
        driver = build_driver(settings)
        service = FreightTestService(driver, settings)
        result = service.execute(url=job["url"], cep=job["cep"], artifact_prefix=job_id)
        store.update(job_id, status="DONE", result=result.to_dict(), finished_at=_utc_now_iso())
        try:
            append_result(settings.results_csv_path, result)
        except Exception as exc:
            store.update(job_id, error=f"Falha ao gravar CSV: {type(exc).__name__}: {exc!r}")
    except WebDriverException as exc:
        msg = f"{type(exc).__name__}: {exc!r}"
        if getattr(exc, "msg", None):
            msg += f" | WebDriver msg: {exc.msg}"
        if settings.use_remote:
            msg += f" (verifique SELENOID_URL={settings.selenoid_url} e se o Selenoid estÃƒÂ¡ rodando)"
        store.update(job_id, status="ERROR", error=msg, finished_at=_utc_now_iso())
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc!r}"
        if settings.use_remote:
            msg += f" (verifique SELENOID_URL={settings.selenoid_url} e se o Selenoid estÃƒÂ¡ rodando)"
        store.update(job_id, status="ERROR", error=msg, finished_at=_utc_now_iso())
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
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
        finished = 0
        for i in range(0, total, chunk_size):
            start = i + 1
            end = min(i + chunk_size, total)
            progress_store.update_chunk(batch_id, current_start=start, current_end=end, finished_items=finished)
            logger.info("Processando lote %s-%s de %s itens (batch_id=%s)", start, end, total, batch_id)

            workers: list[threading.Thread] = []
            for job_id in job_ids[i:end]:
                t = threading.Thread(target=_run_job, args=(store, job_id), daemon=True)
                workers.append(t)
                t.start()

            for t in workers:
                t.join()

            finished = end
            progress_store.update_chunk(batch_id, current_start=start, current_end=end, finished_items=finished)

        progress_store.mark_done(batch_id)
        logger.info("Processamento finalizado para batch_id=%s (total=%s)", batch_id, total)
    except Exception:
        progress_store.mark_error(batch_id)
        logger.exception("Falha no processamento em lotes (batch_id=%s)", batch_id)


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5MB
    app_settings = Settings()
    store = JobStore()
    batch_progress = BatchProgressStore()

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
            return "GrÃƒÂ¡tis"
        if price is None:
            return str(freight.get("price_text") or "-")
        try:
            return _format_money(float(price), currency)
        except Exception:
            return str(freight.get("price_text") or price)

    @app.get("/")
    def index():
        default_url = "https://probel.com.br/colchao-casal-mola-ensacada-probel-excede-premium/p"
        return render_template(
            "index.html",
            default_url=default_url,
            default_cep="79800-002",
        )

    @app.post("/run")
    def run_test():
        url = (request.form.get("url") or "").strip()
        cep = normalize_cep(request.form.get("cep"))
        if not url or not cep:
            abort(400, "Informe URL e CEP.")

        use_remote = app_settings.use_remote

        job_id = store.create(url=url, cep=cep, headless=app_settings.headless, use_remote=use_remote)
        t = threading.Thread(target=_run_job, args=(store, job_id), daemon=True)
        t.start()
        return redirect(url_for("run_detail", job_id=job_id))

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
        if len(rows) > app_settings.max_sheet_rows:
            abort(400, f"Planilha muito grande (máximo: {app_settings.max_sheet_rows} linhas).")

        batch_id = uuid.uuid4().hex
        total_jobs = 0
        for r in rows:
            ceps = getattr(r, "ceps", None) or ()
            total_jobs += len(ceps) if ceps else 1
        if total_jobs > app_settings.max_sheet_jobs:
            abort(
                400,
                f"Planilha gera muitas execuções (máximo: {app_settings.max_sheet_jobs}). Reduza CEPs/linhas.",
            )

        created: list[str] = []
        for r in rows:
            url = (r.url or "").strip()
            if not url.startswith(("http://", "https://")):
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

        return redirect(url_for("batch_detail", batch_id=batch_id))

    @app.get("/batches/<batch_id>")
    def batch_detail(batch_id: str):
        jobs = store.list_by_batch(batch_id)
        if not jobs:
            abort(404)

        progress = batch_progress.get(batch_id)
        done_count = sum(1 for j in jobs if j["status"] in {"DONE", "ERROR"})
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

    @app.get("/batches/<batch_id>/results.xlsx")
    def batch_results_xlsx(batch_id: str):
        jobs = store.list_by_batch(batch_id)
        if not jobs:
            abort(404)

        data = build_results_workbook(jobs)
        return send_file(
            BytesIO(data),
            as_attachment=True,
            download_name=f"batch_{batch_id[:10]}_results.xlsx",
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

    @app.get("/runs/<job_id>/results.xlsx")
    def run_results_xlsx(job_id: str):
        job = store.get(job_id)
        if not job:
            abort(404)

        data = build_results_workbook([job])
        return send_file(
            BytesIO(data),
            as_attachment=True,
            download_name=f"run_{job_id[:10]}_results.xlsx",
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
    app.run(host=host, port=port, debug=debug, threaded=True)
