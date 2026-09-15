import json
import random
import re
import time
from decimal import Decimal

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.common.exceptions import StaleElementReferenceException
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import WebDriverWait

from app.infra.cep import normalize_cep
from app.infra.overlay_dismisser import OverlayDismisser


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

    def __init__(
        self,
        driver: WebDriver,
        timeout: int = 25,
        slow_type_delay_ms: int = 90,
        overlays: OverlayDismisser | None = None,
    ):
        self.driver = driver
        self.wait = WebDriverWait(driver, timeout)
        self.slow_type_delay_ms = slow_type_delay_ms
        self.overlays = overlays or OverlayDismisser(driver, enabled=False)
        # Bloco onde o botao de calcular foi encontrado. Guardado no clique
        # porque, na hora de ler, o widget pode estar vazio e a busca por texto
        # subiria demais — foi assim que o preco de um servico virou "frete".
        self._freight_container = None

    def open(self, url: str) -> None:
        self.driver.get(url)
        self.wait.until(lambda d: self._read_product_name() is not None)
        # Banners de cookie e modais de boas-vindas costumam chegar depois do load.
        self.overlays.settle()

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
        _, cep_input = self._get_cep_context()
        return (cep_input.get_attribute("value") or "").strip()

    def fill_cep(self, cep: str) -> None:
        cep = normalize_cep(cep)
        # Um pop-up aberto pode conter o proprio campo de CEP e roubar a busca.
        self.overlays.recheck()
        form, cep_input = self._get_cep_context()
        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", cep_input)
        time.sleep(0.35)
        self.overlays.clear_path(cep_input)

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
        cep_input = self._find_cep_input()
        found = self._find_freight_button(cep_input)
        if found is None:
            # Sem botao proprio: cai no caminho antigo (form/section ao redor).
            form, cep_input, button = self._get_freight_form_elements()
        else:
            form, button, habilitado = found
            self._freight_container = form
            if not habilitado:
                # Botao so libera depois que o CEP e aceito pelo site.
                try:
                    self.wait.until(lambda d: button.is_enabled())
                except Exception:
                    raise RuntimeError("FREIGHT_BUTTON_DISABLED")

        self.driver.execute_script("arguments[0].scrollIntoView({block: 'center'});", form)
        time.sleep(0.2)
        self.overlays.clear_path(button)
        try:
            button.click()
        except Exception:
            # Clique interceptado costuma ser pop-up que subiu agora: fecha e repete.
            self.overlays.clear_path(button)
            try:
                button.click()
            except Exception:
                try:
                    cep_input.send_keys(Keys.ENTER)
                except Exception:
                    self.driver.execute_script("arguments[0].click();", button)

    def read_freight_result(self) -> dict:
        container = None

        def _has_result(driver):
            # O container e reavaliado a cada tentativa. Logo apos o clique o
            # bloco do frete ainda esta vazio, e fixa-lo cedo demais fazia a
            # leitura escorregar para o bloco errado da pagina.
            nonlocal container
            candidate = self._get_freight_container()
            if candidate is None:
                return False
            try:
                text = (candidate.text or "").strip()
            except Exception:
                return False
            if not text:
                return False
            # "Calculando frete..." ainda nao e resultado.
            if self._RE_CALCULANDO.search(text):
                return False
            achou = bool(
                re.search(r"R\$\s?\d", text)
                or re.search(r"\bgr[aá]tis\b", text, flags=re.IGNORECASE)
                or self._RE_PRAZO.search(text)
            )
            if achou:
                container = candidate
            return achou

        self.wait.until(_has_result)

        try:
            raw_text = (container.text or "").strip()
        except StaleElementReferenceException:
            container = self._get_freight_container()
            raw_text = (container.get_attribute("textContent") or "").strip()
        if not raw_text:
            try:
                raw_text = (container.get_attribute("textContent") or "").strip()
            except StaleElementReferenceException:
                container = self._get_freight_container()
                raw_text = (container.get_attribute("textContent") or "").strip()
        if not raw_text:
            raise RuntimeError("FREIGHT_RESULT_NOT_FOUND")

        lines = [line.strip() for line in re.split(r"[\r\n]+", raw_text) if line.strip()]
        lines = [line for line in lines if not self._RE_RUIDO_DE_PRECO.search(line)]
        chunks = self._split_freight_chunks(lines)
        parsed_chunks = (self._parse_freight_chunk(chunk) for chunk in chunks)
        options = [parsed for parsed in parsed_chunks if self._is_freight_option(parsed)]
        if not options:
            options = [parsed for parsed in (self._parse_freight_chunk(raw_text),) if self._is_freight_option(parsed)]
        if not options:
            raise RuntimeError("FREIGHT_RESULT_NOT_FOUND")

        summary = dict(options[0])
        summary["options"] = [dict(option) for option in options]
        return summary

    @staticmethod
    def _is_freight_option(parsed: dict | None) -> bool:
        """Um trecho so e opcao de frete se traz valor, gratuidade ou prazo.

        Sem isso, o cabecalho do bloco ("Calcular frete e prazo") entra como
        primeira opcao e o resultado sai como "nao informado" mesmo com o preco
        visivel logo abaixo.
        """
        if not parsed:
            return False
        if parsed.get("price") is not None:
            return True
        if str(parsed.get("price_kind") or "").upper() == "FREE":
            return True
        return bool(parsed.get("delivery_time_text"))

    # Linhas de preco do produto que nunca sao frete. Sem esse filtro o
    # parcelamento ("7x de R$ 114,14") passa por valor de frete.
    _RE_RUIDO_DE_PRECO = re.compile(
        r"(\d+\s*x\s+de|sem juros|[àa]\s+vista|no pix|mais op[cç][oõ]es de pagamento|cashback)",
        flags=re.IGNORECASE,
    )
    _RE_CALCULANDO = re.compile(r"(calculando|carregando|aguarde|loading)", flags=re.IGNORECASE)

    # Sinais usados para decidir onde uma opcao de frete termina.
    _RE_VALOR = re.compile(r"R\$\s?\d")
    _RE_GRATIS = re.compile(r"\bgr[aá]tis\b", flags=re.IGNORECASE)
    _RE_PRAZO = re.compile(
        r"(a partir de|em at[eé]\s*\d+|\d+\s*(a|até|ate)?\s*\d*\s*dias?\s*[uú]tei?s?)",
        flags=re.IGNORECASE,
    )

    @classmethod
    def _linha_tem_valor(cls, line: str) -> bool:
        return bool(cls._RE_VALOR.search(line) or cls._RE_GRATIS.search(line))

    def _split_freight_chunks(self, lines: list[str]) -> list[str]:
        """Agrupa as linhas em um bloco por opcao de frete.

        A quebra depende de onde o valor aparece dentro da opcao. Ha widgets
        que listam "R$ 20,00 / em ate 5 dias" e outros que listam
        "em ate 5 dias / R$ 20,00" — fixar uma das convencoes gruda o prazo de
        uma opcao no preco da outra. Entao a ordem e descoberta no proprio
        texto: quem aparecer primeiro, valor ou prazo, define se o valor abre
        ou fecha o bloco.
        """
        primeiro_valor = next(
            (i for i, line in enumerate(lines) if self._linha_tem_valor(line)), None
        )
        primeiro_prazo = next(
            (i for i, line in enumerate(lines) if self._RE_PRAZO.search(line)), None
        )
        # Sem valor nenhum nao ha o que separar.
        if primeiro_valor is None:
            return ["\n".join(lines)] if lines else []

        valor_abre = primeiro_prazo is None or primeiro_valor < primeiro_prazo

        chunks: list[list[str]] = []
        current: list[str] = []
        for line in lines:
            if valor_abre:
                # "R$ 20,00 / prazo": um novo valor comeca a proxima opcao.
                if current and self._linha_tem_valor(line):
                    chunks.append(current)
                    current = []
                current.append(line)
            else:
                # "prazo / R$ 20,00": o valor encerra a opcao corrente.
                current.append(line)
                if self._linha_tem_valor(line):
                    chunks.append(current)
                    current = []
        if current:
            chunks.append(current)
        return ["\n".join(chunk) for chunk in chunks if any(chunk)]

    def _parse_freight_chunk(self, text: str) -> dict | None:
        text = (text or "").strip()
        if not text:
            return None

        prazo_match = re.search(
            r"(A partir de [^\n]+"
            r"|Em at[e\u00e9]\s+\d+\s+dias?\s+[u\u00fa]teis?"
            r"|\d+\s+dias?\s+[u\u00fa]teis?"
            # Lojas que informam a data em vez da quantidade de dias.
            r"|Receb[ae]\s+(?:at[e\u00e9]\s+)?[^\n]+"
            r"|Chega\s+(?:at[e\u00e9]\s+)?[^\n]+"
            r"|Entrega\s+(?:prevista|em|at[e\u00e9])\s+[^\n]+)",
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

    _JS_SUBARVORE_DO_FRETE = r"""
    const cep = arguments[0];
    const COMPRA = /(adicionar ao carrinho|comprar agora|mais op[cç][oõ]es de pagamento|cashback)/i;
    const SINAL = /(R\$\s?\d|\bgr[aá]tis\b|dias?\s+[uú]tei?s|a partir de|em at[ée]\s*\d+|calculando)/i;
    let node = cep.parentElement;
    for (let i = 0; i < 8 && node && node !== document.body; i++) {
      const txt = node.innerText || '';
      // Chegou na caixa de compra: subiu demais, o frete nao esta aqui.
      if (COMPRA.test(txt)) return null;
      if (SINAL.test(txt)) return node;
      node = node.parentElement;
    }
    return null;
    """

    _JS_CRESCER_ATE_O_RESULTADO = r"""
    const inicio = arguments[0];
    if (!inicio || !inicio.isConnected) return null;
    const COMPRA = /(adicionar ao carrinho|comprar agora|mais op[cç][oõ]es de pagamento|cashback)/i;
    const VALOR = /(R\$\s?\d|\bgr[aá]tis\b)/i;
    let node = inicio;
    for (let i = 0; i < 4; i++) {
      // Assim que o bloco ja mostra um valor, ele e o menor que serve.
      if (VALOR.test(node.innerText || '')) return node;
      const pai = node.parentElement;
      if (!pai || pai === document.body) break;
      // Nao invadir a caixa de compra nem os blocos de servico acima dela.
      if (COMPRA.test(pai.innerText || '')) break;
      node = pai;
    }
    return node;
    """

    def _freight_subtree(self):
        """O bloco do widget de frete, para ler o resultado do lugar certo.

        Ancorar no widget evita o erro mais perigoso: ler o preco do produto,
        o parcelamento ou o valor de um servico como se fosse o frete.
        """
        # 1) A partir do bloco onde o botao de calcular estava, cresce ate
        #    incluir a caixa de resultado — que costuma ser irma da linha do
        #    botao, e nao filha dela.
        anchored = self._freight_container
        if anchored is not None:
            try:
                grown = self.driver.execute_script(self._JS_CRESCER_ATE_O_RESULTADO, anchored)
            except Exception:
                grown = None
            if grown is not None:
                return grown

        # 2) Sem essa ancora, cai na busca por texto em volta do campo de CEP.
        try:
            cep_input = self._find_cep_input()
        except Exception:
            return None
        try:
            return self.driver.execute_script(self._JS_SUBARVORE_DO_FRETE, cep_input)
        except Exception:
            return None

    def _get_freight_container(self):
        anchored = self._freight_subtree()
        if anchored is not None:
            return anchored

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

    # Sobe do campo de CEP ate o ancestral que tambem contem o botao de
    # calcular. Em varias lojas o botao fica muito acima do input, e ele nasce
    # desabilitado ate o CEP ser digitado — por isso devolvemos tambem o estado
    # em vez de simplesmente ignorar botoes desabilitados.
    _JS_BOTAO_DE_FRETE = r"""
    const cep = arguments[0];
    const SIM = /(calcular|simular|consultar|buscar|pesquisar|frete|prazo)/i;
    const NAO = /(comprar|carrinho|adicionar|finalizar|checkout|login|entrar|cadastr|n[ãa]o sei|limpar|alterar|trocar|remover)/i;
    function visivel(el) {
      const r = el.getBoundingClientRect();
      if (r.width < 2 || r.height < 2) return false;
      const st = window.getComputedStyle(el);
      return st.display !== 'none' && st.visibility !== 'hidden';
    }
    let node = cep.parentElement;
    for (let i = 0; i < 8 && node && node !== document.body; i++) {
      // Links ficam de fora de proposito: "Nao sei meu CEP" tiraria a
      // automacao da pagina do produto.
      for (const b of node.querySelectorAll('button, [role=button], input[type=submit], input[type=button]')) {
        if (!visivel(b)) continue;
        const txt = [b.innerText, b.getAttribute('aria-label'), b.getAttribute('title'), b.value]
          .map(v => v || '').join(' ').trim();
        if (!txt || NAO.test(txt)) continue;
        if (!SIM.test(txt)) continue;
        return {botao: b, container: node, habilitado: !b.disabled};
      }
      node = node.parentElement;
    }
    return null;
    """

    def _find_freight_button(self, cep_input):
        """Devolve (container, botao, habilitado) ou None."""
        try:
            found = self.driver.execute_script(self._JS_BOTAO_DE_FRETE, cep_input)
        except Exception:
            return None
        if not found:
            return None
        return found.get("container"), found.get("botao"), bool(found.get("habilitado"))

    def _get_cep_context(self):
        """Campo de CEP e um container ao redor, sem exigir o botao.

        Preencher o CEP nao depende do botao — e em varias lojas o botao so
        habilita depois. Exigi-lo aqui fazia a consulta morrer antes de digitar.
        """
        cep_input = self._find_cep_input()
        return self._container_for_element(cep_input), cep_input

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
