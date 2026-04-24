import os
import re
import uuid
from urllib.parse import urlparse
from selenium.common.exceptions import InvalidSessionIdException, TimeoutException, WebDriverException
from app.infra.cep import normalize_cep
from app.domain.models import TestResult
from app.pages.probel_product_page import ProbelProductPage
from app.pages.freight_widget_product_page import FreightWidgetProductPage


class FreightTestService:
    def __init__(self, driver, settings):
        self.driver = driver
        self.settings = settings
        os.makedirs(settings.artifacts_dir, exist_ok=True)

    def _artifact_path(self, name: str, artifact_prefix: str | None) -> str:
        filename = f"{artifact_prefix}_{name}" if artifact_prefix else name
        return os.path.join(self.settings.artifacts_dir, filename)

    def _save_screenshot(self, name: str, artifact_prefix: str | None = None) -> str:
        path = self._artifact_path(name, artifact_prefix)
        try:
            self.driver.save_screenshot(path)
        except (InvalidSessionIdException, WebDriverException):
            return ""
        return path

    def _save_html(self, name: str, artifact_prefix: str | None = None) -> str:
        path = self._artifact_path(name, artifact_prefix)
        try:
            page_source = self.driver.page_source
        except (InvalidSessionIdException, WebDriverException):
            return ""
        with open(path, "w", encoding="utf-8") as f:
            f.write(page_source)
        return path

    @staticmethod
    def _norm_text(value: object) -> str:
        text = str(value or "").strip().lower()
        return re.sub(r"\s+", " ", text)

    def _dedupe_freight_options(self, options: list[dict]) -> list[dict]:
        out: list[dict] = []
        seen: set[tuple[object, ...]] = set()
        for raw in options:
            if not isinstance(raw, dict):
                continue
            option = dict(raw)
            price = option.get("price")
            try:
                price = None if price is None else round(float(price), 2)
            except Exception:
                price = None
            key = (
                self._norm_text(option.get("price_kind")),
                price,
                self._norm_text(option.get("delivery_time_text")),
                self._norm_text(option.get("delivery_mode")),
                self._norm_text(option.get("price_text")),
            )
            if key in seen:
                continue
            seen.add(key)
            out.append(option)
        return out

    def _select_page(self, url: str):
        host = (urlparse(url).netloc or "").lower()
        if host.startswith("www."):
            host = host[4:]

        if "probel.com.br" in host:
            return "probel", ProbelProductPage(
                driver=self.driver,
                timeout=self.settings.wait_timeout_seconds,
                slow_type_delay_ms=self.settings.slow_type_delay_ms,
            )

        if "carrefour.com.br" in host:
            return "carrefour", FreightWidgetProductPage(
                driver=self.driver,
                timeout=self.settings.wait_timeout_seconds,
                slow_type_delay_ms=self.settings.slow_type_delay_ms,
            )

        # Generic fallback for stores that expose a standard freight/CEP widget.
        return "generic", FreightWidgetProductPage(
            driver=self.driver,
            timeout=self.settings.wait_timeout_seconds,
            slow_type_delay_ms=self.settings.slow_type_delay_ms,
        )

    def execute(self, url: str, cep: str, artifact_prefix: str | None = None) -> TestResult:
        cep = normalize_cep(cep)
        artifact_prefix = artifact_prefix or uuid.uuid4().hex[:12]
        source, page = self._select_page(url)
        result = TestResult(
            source=source,
            url=url,
            cep=cep,
            status="STARTED"
        )

        try:
            page.open(url)
            if page.is_blocked():
                result.status = "BLOCKED"
                result.errors.append("Possivel desafio anti-bot detectado.")
                result.artifacts.screenshot = self._save_screenshot("blocked.png", artifact_prefix)
                result.artifacts.html = self._save_html("blocked.html", artifact_prefix)
                return result

            result.product_name = page.get_product_name()

            try:
                page.fill_cep(cep)
            except TimeoutException as exc:
                result.status = "CEP_FIELD_NOT_FOUND"
                result.errors.append(f"Timeout ao localizar campo CEP: {exc!r}")
                raise
            except RuntimeError as exc:
                message = str(exc)
                if message.startswith("CEP_FIELD_VALUE_MISMATCH"):
                    result.status = "CEP_FIELD_VALUE_MISMATCH"
                    result.errors.append(message)
                else:
                    # Selector/content changed, or input did not receive the value.
                    result.status = "CEP_FIELD_NOT_FOUND"
                    result.errors.append(f"Falha ao preencher CEP: {exc!r}")
                raise

            try:
                page.calculate_freight()
            except TimeoutException as exc:
                result.status = "FREIGHT_BUTTON_NOT_FOUND"
                result.errors.append(f"Timeout ao clicar no botao de frete: {exc!r}")
                raise
            except RuntimeError as exc:
                result.status = "FREIGHT_BUTTON_NOT_FOUND"
                result.errors.append(f"Botao de frete nao encontrado: {exc!r}")
                raise

            try:
                freight = page.read_freight_result()
            except RuntimeError as exc:
                # Retry once for widgets that render shipping options asynchronously after the first click.
                if str(exc) == "FREIGHT_RESULT_NOT_FOUND":
                    try:
                        page.calculate_freight()
                        freight = page.read_freight_result()
                    except Exception:
                        raise
                else:
                    raise
            deduped_options = self._dedupe_freight_options(list(freight.get("options") or []))
            if deduped_options:
                primary = deduped_options[0]
                result.freight.price = primary.get("price")
                result.freight.price_kind = primary.get("price_kind") or "UNKNOWN"
                result.freight.price_text = primary.get("price_text")
                result.freight.delivery_time_text = primary.get("delivery_time_text")
                result.freight.delivery_mode = primary.get("delivery_mode")
                result.freight.options = deduped_options
            else:
                result.freight.price = freight["price"]
                result.freight.price_kind = freight.get("price_kind") or "UNKNOWN"
                result.freight.price_text = freight.get("price_text")
                result.freight.delivery_time_text = freight["delivery_time_text"]
                result.freight.delivery_mode = freight["delivery_mode"]
                result.freight.options = []

            if result.freight.options or result.freight.price is not None or result.freight.delivery_time_text:
                result.status = "SUCCESS"
            else:
                result.status = "FREIGHT_NOT_RETURNED"
                result.errors.append("Resultado de frete nao identificado no DOM.")
                result.artifacts.screenshot = self._save_screenshot("freight_not_returned.png", artifact_prefix)
                result.artifacts.html = self._save_html("freight_not_returned.html", artifact_prefix)

        except TimeoutException as exc:
            if result.status == "STARTED":
                result.status = "TIMEOUT"
            result.errors.append(f"TimeoutException: {exc!r}")
            screenshot = self._save_screenshot("error.png", artifact_prefix)
            html = self._save_html("error.html", artifact_prefix)
            if screenshot:
                result.artifacts.screenshot = screenshot
            if html:
                result.artifacts.html = html
        except WebDriverException as exc:
            message = str(exc)
            if "invalid session id" in message.lower() or "session deleted" in message.lower():
                result.status = "BROWSER_DISCONNECTED"
            elif result.status == "STARTED":
                result.status = "ERROR"
            result.errors.append(f"WebDriverException: {exc!r}")
            if getattr(exc, "msg", None):
                result.errors.append(f"WebDriver msg: {exc.msg}")
            screenshot = self._save_screenshot("error.png", artifact_prefix)
            html = self._save_html("error.html", artifact_prefix)
            if screenshot:
                result.artifacts.screenshot = screenshot
            if html:
                result.artifacts.html = html
        except Exception as exc:
            if result.status == "STARTED":
                result.status = "ERROR"
            result.errors.append(f"{type(exc).__name__}: {exc!r}")
            screenshot = self._save_screenshot("error.png", artifact_prefix)
            html = self._save_html("error.html", artifact_prefix)
            if screenshot:
                result.artifacts.screenshot = screenshot
            if html:
                result.artifacts.html = html

        return result
