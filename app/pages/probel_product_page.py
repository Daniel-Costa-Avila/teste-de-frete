import json
import random
import re
import time
from decimal import Decimal
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from app.infra.cep import normalize_cep


class ProbelProductPage:
    PRODUCT_TITLE_SELECTORS = [
        (By.CSS_SELECTOR, "h1.vtex-store-components-3-x-productNameContainer"),
        (By.CSS_SELECTOR, "h1.vtex-store-components-3-x-productNameContainer span"),
        (By.CSS_SELECTOR, "meta[property='og:title']"),
    ]
    FREIGHT_CEP_INPUT = (By.CSS_SELECTOR, "input[data-bind-no-frete='1']")
    # Fallback selectors (site can change attributes/classes)
    FREIGHT_CEP_INPUT_FALLBACKS = [
        (By.CSS_SELECTOR, "input[data-bind-no-frete='1']"),
        (By.CSS_SELECTOR, "input[placeholder*='CEP' i]"),
        (By.CSS_SELECTOR, "input[name*='cep' i]"),
        (By.CSS_SELECTOR, "input[id*='cep' i]"),
        (By.CSS_SELECTOR, "input[inputmode='numeric'][maxlength='9']"),
    ]

    FREIGHT_CALC_BUTTON = (By.CSS_SELECTOR, "button[type='submit'][data-bind-no-frete='1']")
    FREIGHT_TABLE = (By.CSS_SELECTOR, "table.taace8-shipping-simulator-1-x-shippingTable")
    FREIGHT_TABLE_ROW = (By.CSS_SELECTOR, "tbody.taace8-shipping-simulator-1-x-shippingTableBody tr")

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
                return re.sub(r"\s+-\s+Probel Colchões.*$", "", content).strip()
        except Exception:
            pass

        for by, selector in self.PRODUCT_TITLE_SELECTORS[:2]:
            try:
                el = self.driver.find_element(by, selector)
            except Exception:
                continue
            text = (el.text or "").strip()
            if text:
                return text

        return None

    def get_cep_value(self) -> str:
        _, cep_input, _ = self._get_freight_form_elements()
        return (cep_input.get_attribute("value") or "").strip()

    def fill_cep(self, cep: str) -> None:
        cep = normalize_cep(cep)
        form, cep_input, _ = self._get_freight_form_elements()
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", cep_input)
        time.sleep(0.4)

        cep_input.click()
        cep_input.send_keys(Keys.CONTROL, "a")
        cep_input.send_keys(Keys.DELETE)

        for ch in cep:
            cep_input.send_keys(ch)
            time.sleep((self.slow_type_delay_ms + random.randint(10, 60)) / 1000)

        # Basic sanity check: value should contain digits.
        value = (cep_input.get_attribute("value") or "").strip()
        if not any(ch.isdigit() for ch in value):
            # Fallback for masked inputs: set value via JS and dispatch events.
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

        # Small blur to trigger masked inputs / validators.
        form.click()

        value = normalize_cep(cep_input.get_attribute("value") or "")
        if value != cep:
            raise RuntimeError(f"CEP_FIELD_VALUE_MISMATCH: expected={cep!r} actual={value!r}")

    def calculate_freight(self) -> None:
        _, _, button = self._get_freight_form_elements()
        self.wait.until(lambda d: button.is_displayed() and button.is_enabled())
        time.sleep(0.5)
        try:
            button.click()
        except Exception:
            # Some pages overlay elements or use custom handlers; fallback to JS click.
            self.driver.execute_script("arguments[0].click();", button)

    def read_freight_result(self) -> dict:
        form, _, _ = self._get_freight_form_elements()

        def _has_rows(driver):
            try:
                table = form.find_element(*self.FREIGHT_TABLE)
                if not table.is_displayed():
                    return False
                rows = table.find_elements(*self.FREIGHT_TABLE_ROW)
                return len(rows) > 0
            except Exception:
                return False

        self.wait.until(_has_rows)

        table = form.find_element(*self.FREIGHT_TABLE)
        rows = table.find_elements(*self.FREIGHT_TABLE_ROW)
        parsed_rows: list[dict] = []
        for row in rows:
            parsed = self._parse_freight_row(row)
            if parsed is not None:
                parsed_rows.append(parsed)

        if not parsed_rows:
            raise RuntimeError("FREIGHT_RESULT_NOT_FOUND")

        options = [dict(row) for row in parsed_rows]
        summary = dict(options[0])
        summary["options"] = options
        return summary

    def _parse_freight_row(self, row) -> dict | None:
        cells = row.find_elements(By.CSS_SELECTOR, "td")
        cell_texts = [c.text.strip() for c in cells if c.text and c.text.strip()]
        text_blob = " ".join(cell_texts) if cell_texts else ((row.text or "").strip())
        if not text_blob:
            return None

        prazo_match = re.search(r"Em at\u00e9\s+\d+\s+dias?\s+\u00fateis", text_blob, flags=re.IGNORECASE)
        modo_match = re.search(r"\b(Normal|Expresso|Econ\u00f4mico)\b", text_blob, flags=re.IGNORECASE)
        price_match = re.search(r"R\$\s?(\d{1,3}(?:\.\d{3})*,\d{2})", text_blob)
        is_free = re.search(r"\bgr[a\u00e1]tis\b", text_blob, flags=re.IGNORECASE) is not None

        price = None
        if not price_match:
            # Sometimes the currency parts are split into spans; fall back to textContent.
            text_content = row.get_attribute("textContent") or ""
            price_match = re.search(r"R\$\s?(\d{1,3}(?:\.\d{3})*,\d{2})", text_content)
            if not is_free:
                is_free = re.search(r"\bgr[a\u00e1]tis\b", text_content, flags=re.IGNORECASE) is not None
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
        for t in reversed(cell_texts):
            if t:
                price_text = t
                break
        if not price_text:
            price_text = text_blob or None

        return {
            "delivery_time_text": prazo_match.group(0) if prazo_match else None,
            "delivery_mode": modo_match.group(1) if modo_match else None,
            "price": price,
            "price_kind": price_kind,
            "price_text": price_text,
        }

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

    def _get_freight_form_elements(self):
        def _is_visible(el) -> bool:
            try:
                return el.is_displayed() and el.is_enabled()
            except Exception:
                return False

        def _find_by_fallbacks(driver):
            for by, sel in self.FREIGHT_CEP_INPUT_FALLBACKS:
                try:
                    els = driver.find_elements(by, sel)
                except Exception:
                    continue
                for el in els:
                    if _is_visible(el):
                        return el
            return None

        def _find_by_calculate_widget(driver):
            # Many pages have a "Calcular Frete e prazo" widget with an input + button "Calcular o frete".
            btn_xpath = (
                "//button["
                "contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'calcular')"
                " and contains(translate(normalize-space(.), 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'frete')"
                "]"
            )
            try:
                buttons = driver.find_elements(By.XPATH, btn_xpath)
            except Exception:
                buttons = []

            for b in buttons:
                if not _is_visible(b):
                    continue

                container = None
                for xp in ("ancestor::form[1]", "ancestor::section[1]", "ancestor::div[1]"):
                    try:
                        container = b.find_element(By.XPATH, xp)
                        break
                    except Exception:
                        container = None

                if container is None:
                    continue

                try:
                    inputs = container.find_elements(By.XPATH, ".//input[not(@type='hidden')]")
                except Exception:
                    inputs = []

                for inp in inputs:
                    if _is_visible(inp):
                        return container, inp, b

            return None

        found = self.wait.until(lambda d: _find_by_calculate_widget(d) or _find_by_fallbacks(d))

        if isinstance(found, tuple):
            form, cep_input, button = found
        else:
            cep_input = found
            # Some pages don't wrap it in a <form>. Keep a "container" that can be clicked to blur.
            try:
                form = cep_input.find_element(By.XPATH, "ancestor::form[1]")
            except Exception:
                form = cep_input.find_element(By.XPATH, "ancestor::*[self::section or self::div][1]")

            button = None
            # Prefer the original selector (most precise)
            try:
                button = form.find_element(*self.FREIGHT_CALC_BUTTON)
            except Exception:
                button = None

            if button is None:
                # Fallbacks: a submit button near the CEP input within the same container.
                button_candidates = []
                try:
                    button_candidates.extend(form.find_elements(By.CSS_SELECTOR, "button[type='submit']"))
                except Exception:
                    pass
                try:
                    button_candidates.extend(form.find_elements(By.CSS_SELECTOR, "button[data-bind-no-frete='1']"))
                except Exception:
                    pass
                try:
                    button_candidates.extend(
                        form.find_elements(
                            By.XPATH,
                            ".//button[contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'frete') or "
                            "contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'calcular') or "
                            "contains(translate(., 'ABCDEFGHIJKLMNOPQRSTUVWXYZ', 'abcdefghijklmnopqrstuvwxyz'), 'simular')]",
                        )
                    )
                except Exception:
                    pass

                for b in button_candidates:
                    if _is_visible(b):
                        button = b
                        break

            if button is None:
                raise RuntimeError("FREIGHT_BUTTON_NOT_FOUND")

        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", form)
        time.sleep(0.2)
        return form, cep_input, button
