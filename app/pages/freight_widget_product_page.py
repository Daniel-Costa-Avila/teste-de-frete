import json
import random
import re
import time
from decimal import Decimal

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.infra.cep import normalize_cep


class FreightWidgetProductPage:
    PRODUCT_TITLE_SELECTORS = [
        (By.CSS_SELECTOR, "h1"),
        (By.CSS_SELECTOR, "meta[property='og:title']"),
        (By.CSS_SELECTOR, "meta[name='twitter:title']"),
    ]

    FREIGHT_CEP_INPUT_FALLBACKS = [
        (By.CSS_SELECTOR, "input[placeholder*='CEP' i]"),
        (By.CSS_SELECTOR, "input[aria-label*='CEP' i]"),
        (By.CSS_SELECTOR, "input[name*='cep' i]"),
        (By.CSS_SELECTOR, "input[id*='cep' i]"),
        (By.CSS_SELECTOR, "input[inputmode='numeric'][maxlength='9']"),
        (By.CSS_SELECTOR, "input[type='tel']"),
        (By.CSS_SELECTOR, "input[type='text']"),
    ]

    def __init__(self, driver: WebDriver, timeout: int = 25, slow_type_delay_ms: int = 90):
        self.driver = driver
        self.wait = WebDriverWait(driver, timeout)
        self.slow_type_delay_ms = slow_type_delay_ms

    def open(self, url: str) -> None:
        self.driver.get(url)
        self.wait.until(lambda d: self._read_product_name() is not None)

    def get_product_name(self) -> str:
        name = self.wait.until(lambda d: self._read_product_name())
        if not name:
            raise RuntimeError("PRODUCT_NAME_NOT_FOUND")
        return name

    def _read_product_name(self) -> str | None:
        try:
            scripts = self.driver.find_elements(By.CSS_SELECTOR, "script[type='application/ld+json']")
        except Exception:
            scripts = []

        for script in scripts:
            raw = (script.get_attribute("textContent") or "").strip()
            if not raw:
                continue
            try:
                payload = json.loads(raw)
            except Exception:
                continue

            items = payload if isinstance(payload, list) else [payload]
            for item in items:
                if not isinstance(item, dict):
                    continue
                if str(item.get("@type", "")).lower() != "product":
                    continue
                name = item.get("name")
                if isinstance(name, str) and name.strip():
                    return name.strip()

        try:
            meta = self.driver.find_element(By.CSS_SELECTOR, "meta[property='og:title']")
            content = (meta.get_attribute("content") or "").strip()
            if content:
                return re.sub(r"\s+-\s+.*$", "", content).strip()
        except Exception:
            pass

        try:
            meta = self.driver.find_element(By.CSS_SELECTOR, "meta[name='twitter:title']")
            content = (meta.get_attribute("content") or "").strip()
            if content:
                return re.sub(r"\s+-\s+.*$", "", content).strip()
        except Exception:
            pass

        for by, selector in self.PRODUCT_TITLE_SELECTORS[:1]:
            try:
                el = self.driver.find_element(by, selector)
            except Exception:
                continue
            text = (el.text or "").strip()
            if text:
                return text

        return None

    def is_blocked(self) -> bool:
        try:
            body_text = self.driver.find_element(By.TAG_NAME, "body").text.lower()
        except Exception:
            body_text = ""

        text_signals = [
            "verifique se voc",
            "nao sou um rob",
            "captcha",
            "unusual traffic",
            "acesso negado",
            "access denied",
            "forbidden",
        ]
        if any(s in body_text for s in text_signals):
            return True

        try:
            frames = self.driver.find_elements(By.CSS_SELECTOR, "iframe[src*='recaptcha']")
            if any(f.is_displayed() for f in frames):
                return True
        except Exception:
            pass

        return False

    def get_cep_value(self) -> str:
        _, cep_input, _ = self._get_freight_form_elements()
        return (cep_input.get_attribute("value") or "").strip()

    def fill_cep(self, cep: str) -> None:
        cep = normalize_cep(cep)
        form, cep_input, _ = self._get_freight_form_elements()
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", cep_input)
        time.sleep(0.35)

        try:
            cep_input.click()
        except Exception:
            self.driver.execute_script("arguments[0].focus();", cep_input)

        cep_input.send_keys(Keys.CONTROL, "a")
        cep_input.send_keys(Keys.DELETE)

        for ch in cep:
            cep_input.send_keys(ch)
            time.sleep((self.slow_type_delay_ms + random.randint(10, 60)) / 1000)

        value = (cep_input.get_attribute("value") or "").strip()
        if not any(ch.isdigit() for ch in value):
            self.driver.execute_script(
                """
                const el = arguments[0];
                const v = arguments[1];
                el.focus();
                el.value = v;
                el.dispatchEvent(new Event('input', { bubbles: true }));
                el.dispatchEvent(new Event('change', { bubbles: true }));
                """,
                cep_input,
                cep,
            )
            value = (cep_input.get_attribute("value") or "").strip()
            if not any(ch.isdigit() for ch in value):
                raise RuntimeError("CEP_FIELD_NOT_FOUND")

        try:
            form.click()
        except Exception:
            pass

        value = normalize_cep(cep_input.get_attribute("value") or "")
        if value != cep:
            raise RuntimeError(f"CEP_FIELD_VALUE_MISMATCH: expected={cep!r} actual={value!r}")

    def calculate_freight(self) -> None:
        form, cep_input, button = self._get_freight_form_elements()
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", form)
        time.sleep(0.2)
        try:
            button.click()
        except Exception:
            try:
                cep_input.send_keys(Keys.ENTER)
            except Exception:
                self.driver.execute_script("arguments[0].click();", button)

    def read_freight_result(self) -> dict:
        container = self._get_freight_container()

        def _has_result(driver):
            try:
                text = (container.text or "").strip()
            except Exception:
                return False
            if not text:
                return False
            return bool(
                re.search(r"R\$\s?\d", text)
                or re.search(r"\bgr[aá]tis\b", text, flags=re.IGNORECASE)
                or re.search(r"\bA partir de\b", text, flags=re.IGNORECASE)
                or re.search(r"\bEm até\b", text, flags=re.IGNORECASE)
            )

        self.wait.until(_has_result)

        raw_text = (container.text or "").strip()
        if not raw_text:
            raw_text = (container.get_attribute("textContent") or "").strip()
        if not raw_text:
            raise RuntimeError("FREIGHT_RESULT_NOT_FOUND")

        lines = [line.strip() for line in re.split(r"[\r\n]+", raw_text) if line.strip()]
        chunks = self._split_freight_chunks(lines)
        options = [parsed for parsed in (self._parse_freight_chunk(chunk) for chunk in chunks) if parsed is not None]
        if not options:
            options = [parsed for parsed in (self._parse_freight_chunk(raw_text),) if parsed is not None]
        if not options:
            raise RuntimeError("FREIGHT_RESULT_NOT_FOUND")

        summary = dict(options[0])
        summary["options"] = [dict(option) for option in options]
        return summary

    def _split_freight_chunks(self, lines: list[str]) -> list[str]:
        chunks: list[list[str]] = []
        current: list[str] = []
        for line in lines:
            if current and re.search(r"R\$\s?\d", line):
                chunks.append(current)
                current = [line]
                continue
            current.append(line)
        if current:
            chunks.append(current)
        return ["\n".join(chunk) for chunk in chunks if any(chunk)]

    def _parse_freight_chunk(self, text: str) -> dict | None:
        text = (text or "").strip()
        if not text:
            return None

        prazo_match = re.search(
            r"(A partir de [^\n]+|Em at\u00e9\s+\d+\s+dias?\s+\u00fateis?)",
            text,
            flags=re.IGNORECASE,
        )
        modo_match = re.search(
            r"\b(Expressa|Expresso|Normal|Econ\u00f4mico|Econômico|R\u00e1pida|Rápida)\b",
            text,
            flags=re.IGNORECASE,
        )
        price_match = re.search(r"R\$\s?(\d{1,3}(?:\.\d{3})*,\d{2})", text)
        is_free = re.search(r"\bgr[aá]tis\b", text, flags=re.IGNORECASE) is not None

        price = None
        if price_match:
            raw = price_match.group(1).replace(".", "").replace(",", ".")
            price = float(Decimal(raw))
        elif is_free:
            price = 0.0

        price_kind = "UNKNOWN"
        if price is None:
            price_kind = "UNKNOWN"
        elif is_free or price == 0.0:
            price_kind = "FREE"
        else:
            price_kind = "PAID"

        price_text = None
        for line in reversed([line.strip() for line in re.split(r"[\r\n]+", text) if line.strip()]):
            if re.search(r"R\$\s?\d", line) or re.search(r"\bgr[aá]tis\b", line, flags=re.IGNORECASE):
                price_text = line
                break
        if not price_text:
            price_text = text or None

        return {
            "delivery_time_text": prazo_match.group(0).strip() if prazo_match else None,
            "delivery_mode": modo_match.group(1).strip() if modo_match else None,
            "price": price,
            "price_kind": price_kind,
            "price_text": price_text,
        }

    def _get_freight_container(self):
        def _is_visible(el) -> bool:
            try:
                return el.is_displayed() and el.is_enabled()
            except Exception:
                return False

        def _candidate_containers():
            selectors = [
                "form",
                "section",
                "aside",
                "div",
            ]
            seen = set()
            for selector in selectors:
                try:
                    elements = self.driver.find_elements(By.CSS_SELECTOR, selector)
                except Exception:
                    continue
                for el in elements:
                    if not _is_visible(el):
                        continue
                    try:
                        text = (el.text or "").strip().lower()
                    except Exception:
                        continue
                    if not text:
                        continue
                    if "frete" in text or "prazo" in text or "cep" in text:
                        key = id(el)
                        if key not in seen:
                            seen.add(key)
                            yield el

        for container in _candidate_containers():
            try:
                inputs = container.find_elements(By.XPATH, ".//input[not(@type='hidden')]")
            except Exception:
                inputs = []
            if inputs:
                return container

        # Fallback to the first visible CEP-like input.
        cep_input = self._find_cep_input()
        try:
            return cep_input.find_element(By.XPATH, "ancestor::form[1]")
        except Exception:
            pass
        try:
            return cep_input.find_element(By.XPATH, "ancestor::section[1]")
        except Exception:
            pass
        try:
            return cep_input.find_element(By.XPATH, "ancestor::aside[1]")
        except Exception:
            pass
        return cep_input.find_element(By.XPATH, "ancestor::div[1]")

    def _get_freight_form_elements(self):
        def _is_visible(el) -> bool:
            try:
                return el.is_displayed() and el.is_enabled()
            except Exception:
                return False

        cep_input = self._find_cep_input()
        container = self._container_for_element(cep_input)

        button = None
        try:
            candidates = container.find_elements(By.XPATH, ".//button[not(@disabled)]")
        except Exception:
            candidates = []

        preferred: list = []
        for candidate in candidates:
            if not _is_visible(candidate):
                continue
            try:
                aria = (candidate.get_attribute("aria-label") or "").lower()
                title = (candidate.get_attribute("title") or "").lower()
                text = (candidate.text or "").lower()
                combined = " ".join([aria, title, text])
            except Exception:
                combined = ""
            if any(keyword in combined for keyword in ("frete", "calcular", "buscar", "pesquisar", "consultar")):
                preferred.append(candidate)
            elif button is None:
                button = candidate

        if preferred:
            button = preferred[0]

        if button is None:
            raise RuntimeError("FREIGHT_BUTTON_NOT_FOUND")

        return container, cep_input, button

    def _find_cep_input(self):
        def _is_visible(el) -> bool:
            try:
                return el.is_displayed() and el.is_enabled()
            except Exception:
                return False

        for by, sel in self.FREIGHT_CEP_INPUT_FALLBACKS:
            try:
                elements = self.driver.find_elements(by, sel)
            except Exception:
                continue
            for el in elements:
                if _is_visible(el):
                    return el
        raise RuntimeError("FREIGHT_CEP_INPUT_NOT_FOUND")

    def _container_for_element(self, element):
        for xp in ("ancestor::form[1]", "ancestor::section[1]", "ancestor::aside[1]", "ancestor::div[1]"):
            try:
                container = element.find_element(By.XPATH, xp)
            except Exception:
                continue
            try:
                if container.is_displayed():
                    return container
            except Exception:
                return container
        return element
