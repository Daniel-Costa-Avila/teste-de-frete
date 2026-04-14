from __future__ import annotations

import json
import os
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from flask import Flask, abort, jsonify, redirect, render_template, request, send_file, send_from_directory, url_for
from selenium.common.exceptions import WebDriverException

from app.config import Settings
from app.infra.driver_factory import build_driver
from app.infra.results_csv import append_result
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
        product_id: str | None = None,
        input_product_name: str | None = None,
    ) -> str:
        job_id = uuid.uuid4().hex
        job = {
            "id": job_id,
            "batch_id": batch_id,
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

    def list_recent(self, limit: int = 20, q: str | None = None) -> list[dict[str, Any]]:
        with self._lock:
            jobs = list(self._jobs.values())

        if q:
            needle = q.strip().lower()
            if needle:
                def _match(job: dict[str, Any]) -> bool:
                    hay = " ".join(
                        str(job.get(k) or "")
                        for k in ("id", "batch_id", "url", "cep", "status", "product_id", "input_product_name")
                    ).lower()
                    return needle in hay

                jobs = [j for j in jobs if _match(j)]

        jobs.sort(key=lambda j: j["created_at"], reverse=True)
        return [dict(j) for j in jobs[:limit]]

    def list_by_batch(self, batch_id: str) -> list[dict[str, Any]]:
        with self._lock:
            jobs = [j for j in self._jobs.values() if j.get("batch_id") == batch_id]
        jobs.sort(key=lambda j: j["created_at"], reverse=False)
        return [dict(j) for j in jobs]


def _parse_batch_lines(text: str) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for raw in (text or "").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue

        parts = [p.strip() for p in line.split(";")]
        parts = [p for p in parts if p != ""]

        # formats supported:
        # - url
        # - product_id;url
        # - name;product_id;url
        # - name;product_id;url;cep
        if len(parts) == 1:
            items.append({"url": parts[0]})
        elif len(parts) == 2:
            items.append({"product_id": parts[0], "url": parts[1]})
        elif len(parts) == 3:
            items.append({"input_product_name": parts[0], "product_id": parts[1], "url": parts[2]})
        else:
            items.append({"input_product_name": parts[0], "product_id": parts[1], "url": parts[2], "cep": parts[3]})

    return items


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
        result = service.execute(url=job["url"], cep=job["cep"])
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
            msg += f" (verifique SELENOID_URL={settings.selenoid_url} e se o Selenoid está rodando)"
        store.update(job_id, status="ERROR", error=msg, finished_at=_utc_now_iso())
    except Exception as exc:
        msg = f"{type(exc).__name__}: {exc!r}"
        if settings.use_remote:
            msg += f" (verifique SELENOID_URL={settings.selenoid_url} e se o Selenoid está rodando)"
        store.update(job_id, status="ERROR", error=msg, finished_at=_utc_now_iso())
    finally:
        if driver is not None:
            try:
                driver.quit()
            except Exception:
                pass


def create_app() -> Flask:
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 5 * 1024 * 1024  # 5MB
    store = JobStore()

    @app.get("/")
    def index():
        settings = Settings()
        q = (request.args.get("q") or "").strip()
        default_url = "https://probel.com.br/colchao-casal-mola-ensacada-probel-excede-premium/p"
        return render_template(
            "index.html",
            jobs=store.list_recent(q=q),
            q=q,
            default_url=default_url,
            default_cep="79800-002",
            defaults={
                "headless": settings.headless,
                "use_remote": settings.use_remote,
            },
        )

    @app.post("/run")
    def run_test():
        url = (request.form.get("url") or "").strip()
        cep = (request.form.get("cep") or "").strip()
        if not url or not cep:
            abort(400, "Informe URL e CEP.")

        headless = _to_bool(request.form.get("headless"))
        use_remote = _to_bool(request.form.get("use_remote"))

        job_id = store.create(url=url, cep=cep, headless=headless, use_remote=use_remote)
        t = threading.Thread(target=_run_job, args=(store, job_id), daemon=True)
        t.start()
        return redirect(url_for("run_detail", job_id=job_id))

    @app.post("/run-batch")
    def run_batch():
        cep_default = (request.form.get("cep") or "").strip()
        raw_items = (request.form.get("items") or "").strip()
        if not raw_items:
            abort(400, "Informe ao menos 1 URL.")
        if not cep_default:
            abort(400, "Informe o CEP.")

        headless = _to_bool(request.form.get("headless"))
        use_remote = _to_bool(request.form.get("use_remote"))

        items = _parse_batch_lines(raw_items)
        if not items:
            abort(400, "Nenhuma linha válida encontrada.")
        if len(items) > 50:
            abort(400, "Lote muito grande (máximo: 50 linhas).")

        batch_id = uuid.uuid4().hex
        created: list[str] = []
        for item in items:
            url = (item.get("url") or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            cep = (item.get("cep") or cep_default).strip()
            if not cep:
                continue
            job_id = store.create(
                url=url,
                cep=cep,
                headless=headless,
                use_remote=use_remote,
                batch_id=batch_id,
                product_id=(item.get("product_id") or "").strip() or None,
                input_product_name=(item.get("input_product_name") or "").strip() or None,
            )
            created.append(job_id)
            t = threading.Thread(target=_run_job, args=(store, job_id), daemon=True)
            t.start()

        if not created:
            abort(400, "Nenhuma URL válida no lote.")

        return redirect(url_for("batch_detail", batch_id=batch_id))

    @app.get("/templates/produtos.xlsx")
    def template_produtos_xlsx():
        path = os.path.abspath(os.path.join(Settings().artifacts_dir, "produtos_entrada_template.xlsx"))
        if not os.path.exists(path):
            abort(404)
        return send_file(path, as_attachment=True, download_name="produtos_entrada_template.xlsx")

    @app.get("/templates/produtos.csv")
    def template_produtos_csv():
        path = os.path.abspath(os.path.join(Settings().artifacts_dir, "produtos_entrada_template.csv"))
        if not os.path.exists(path):
            abort(404)
        return send_file(path, as_attachment=True, download_name="produtos_entrada_template.csv")

    @app.post("/run-products-sheet")
    def run_products_sheet():
        cep_default = (request.form.get("cep") or "").strip()
        if not cep_default:
            abort(400, "Informe o CEP.")

        file = request.files.get("sheet")
        if not file or not file.filename:
            abort(400, "Envie a planilha (.xlsx ou .csv).")

        headless = _to_bool(request.form.get("headless"))
        use_remote = _to_bool(request.form.get("use_remote"))

        data = file.read()
        try:
            rows = parse_products_file(file.filename, data)
        except ValueError as exc:
            abort(400, str(exc))

        if not rows:
            abort(400, "Planilha sem linhas válidas.")
        if len(rows) > 50:
            abort(400, "Planilha muito grande (máximo: 50 linhas).")

        batch_id = uuid.uuid4().hex
        created: list[str] = []
        for r in rows:
            url = (r.url or "").strip()
            if not url.startswith(("http://", "https://")):
                continue
            job_id = store.create(
                url=url,
                cep=cep_default,
                headless=headless,
                use_remote=use_remote,
                batch_id=batch_id,
                product_id=(r.product_id or "").strip() or None,
                input_product_name=(r.product_name or "").strip() or None,
            )
            created.append(job_id)
            t = threading.Thread(target=_run_job, args=(store, job_id), daemon=True)
            t.start()

        if not created:
            abort(400, "Nenhuma URL válida encontrada na planilha.")

        return redirect(url_for("batch_detail", batch_id=batch_id))

    @app.get("/batches/<batch_id>")
    def batch_detail(batch_id: str):
        jobs = store.list_by_batch(batch_id)
        if not jobs:
            abort(404)

        running = any(j["status"] in {"QUEUED", "RUNNING"} for j in jobs)
        return render_template(
            "batch.html",
            batch_id=batch_id,
            jobs=jobs,
            auto_refresh=running,
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
        settings = Settings()
        directory = os.path.abspath(settings.artifacts_dir)
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
