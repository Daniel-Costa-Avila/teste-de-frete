import random
import re
import time
from decimal import Decimal
from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC


class ProbelProductPage:
    PRODUCT_TITLE = (By.CSS_SELECTOR, "h1")
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
        self.wait.until(EC.presence_of_element_located(self.PRODUCT_TITLE))

    def get_product_name(self) -> str:
        return self.wait.until(
            EC.visibility_of_element_located(self.PRODUCT_TITLE)
        ).text.strip()

    def fill_cep(self, cep: str) -> None:
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

    def calculate_freight(self) -> None:
        _, _, button = self._get_freight_form_elements()
        self.wait.until(lambda d: button.is_displayed() and button.is_enabled())
        time.sleep(0.5)
        button.click()

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
        row = table.find_element(*self.FREIGHT_TABLE_ROW)
        cells = row.find_elements(By.CSS_SELECTOR, "td")
        cell_texts = [c.text.strip() for c in cells if c.text and c.text.strip()]
        text_blob = " ".join(cell_texts) if cell_texts else (row.text or "")

        prazo_match = re.search(r"Em at\u00e9\s+\d+\s+dias?\s+\u00fateis", text_blob, flags=re.IGNORECASE)
        modo_match = re.search(r"\b(Normal|Expresso|Econ\u00f4mico)\b", text_blob, flags=re.IGNORECASE)
        price_match = re.search(r"R\$\s?(\d{1,3}(?:\.\d{3})*,\d{2})", text_blob)

        price = None
        if not price_match:
            # Sometimes the currency parts are split into spans; fall back to textContent.
            text_content = row.get_attribute("textContent") or ""
            price_match = re.search(r"R\$\s?(\d{1,3}(?:\.\d{3})*,\d{2})", text_content)
        if price_match:
            raw = price_match.group(1).replace(".", "").replace(",", ".")
            price = float(Decimal(raw))

        return {
            "delivery_time_text": prazo_match.group(0) if prazo_match else None,
            "delivery_mode": modo_match.group(1) if modo_match else None,
            "price": price
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
        def _find_cep_input(driver):
            for by, sel in self.FREIGHT_CEP_INPUT_FALLBACKS:
                try:
                    els = driver.find_elements(by, sel)
                    for el in els:
                        try:
                            if el.is_displayed() and el.is_enabled():
                                return el
                        except Exception:
                            continue
                except Exception:
                    continue
            return None

        cep_input = self.wait.until(lambda d: _find_cep_input(d))

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
            pass

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
                try:
                    if b.is_displayed() and b.is_enabled():
                        button = b
                        break
                except Exception:
                    continue

        if button is None:
            raise RuntimeError("FREIGHT_BUTTON_NOT_FOUND")

        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", form)
        time.sleep(0.2)
        return form, cep_input, button
