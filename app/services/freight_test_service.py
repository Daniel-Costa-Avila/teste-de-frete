import os
from selenium.common.exceptions import TimeoutException, WebDriverException
from app.domain.models import TestResult
from app.pages.probel_product_page import ProbelProductPage


class FreightTestService:
    def __init__(self, driver, settings):
        self.driver = driver
        self.settings = settings
        os.makedirs(settings.artifacts_dir, exist_ok=True)

    def _save_screenshot(self, name: str) -> str:
        path = os.path.join(self.settings.artifacts_dir, name)
        self.driver.save_screenshot(path)
        return path

    def _save_html(self, name: str) -> str:
        path = os.path.join(self.settings.artifacts_dir, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(self.driver.page_source)
        return path

    def execute(self, url: str, cep: str) -> TestResult:
        result = TestResult(
            source="probel",
            url=url,
            cep=cep,
            status="STARTED"
        )

        page = ProbelProductPage(
            driver=self.driver,
            timeout=self.settings.wait_timeout_seconds,
            slow_type_delay_ms=self.settings.slow_type_delay_ms
        )

        try:
            page.open(url)
            if page.is_blocked():
                result.status = "BLOCKED"
                result.errors.append("Possivel desafio anti-bot detectado.")
                result.artifacts.screenshot = self._save_screenshot("blocked.png")
                result.artifacts.html = self._save_html("blocked.html")
                return result

            result.product_name = page.get_product_name()

            try:
                page.fill_cep(cep)
            except TimeoutException as exc:
                result.status = "CEP_FIELD_NOT_FOUND"
                result.errors.append(f"Timeout ao localizar campo CEP: {exc!r}")
                raise
            except RuntimeError as exc:
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

            freight = page.read_freight_result()
            result.freight.price = freight["price"]
            result.freight.delivery_time_text = freight["delivery_time_text"]
            result.freight.delivery_mode = freight["delivery_mode"]

            if result.freight.price is not None or result.freight.delivery_time_text:
                result.status = "SUCCESS"
            else:
                result.status = "FREIGHT_NOT_RETURNED"
                result.errors.append("Resultado de frete nao identificado no DOM.")
                result.artifacts.screenshot = self._save_screenshot("freight_not_returned.png")
                result.artifacts.html = self._save_html("freight_not_returned.html")

        except TimeoutException as exc:
            if result.status == "STARTED":
                result.status = "TIMEOUT"
            result.errors.append(f"TimeoutException: {exc!r}")
            result.artifacts.screenshot = self._save_screenshot("error.png")
            result.artifacts.html = self._save_html("error.html")
        except WebDriverException as exc:
            if result.status == "STARTED":
                result.status = "ERROR"
            result.errors.append(f"WebDriverException: {exc!r}")
            if getattr(exc, "msg", None):
                result.errors.append(f"WebDriver msg: {exc.msg}")
            result.artifacts.screenshot = self._save_screenshot("error.png")
            result.artifacts.html = self._save_html("error.html")
        except Exception as exc:
            if result.status == "STARTED":
                result.status = "ERROR"
            result.errors.append(f"{type(exc).__name__}: {exc!r}")
            result.artifacts.screenshot = self._save_screenshot("error.png")
            result.artifacts.html = self._save_html("error.html")

        return result
