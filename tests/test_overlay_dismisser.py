"""Testes do fechamento automatico de pop-ups, com Chrome de verdade.

As paginas de teste ficam em fixtures_popup.py e sao servidas de um diretorio
temporario via file://. Se o Chrome nao estiver disponivel, os testes sao
pulados em vez de falharem.
"""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path

from tests import fixtures_popup as fx

try:
    from selenium import webdriver
    from selenium.webdriver.chrome.options import Options
    from selenium.webdriver.common.by import By

    SELENIUM_OK = True
except Exception:  # pragma: no cover - ambiente sem selenium
    SELENIUM_OK = False

from app.infra.overlay_dismisser import OverlayDismisser


def _make_driver():
    options = Options()
    options.add_argument("--headless=new")
    options.add_argument("--window-size=1280,900")
    options.add_argument("--hide-scrollbars")
    options.add_argument("--no-sandbox")
    return webdriver.Chrome(options=options)


@unittest.skipUnless(SELENIUM_OK, "Selenium nao esta disponivel neste ambiente")
class OverlayDismisserTests(unittest.TestCase):
    driver = None
    tmpdir = None

    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.driver = _make_driver()
        except Exception as exc:  # pragma: no cover - Chrome ausente
            raise unittest.SkipTest(f"Chrome nao disponivel: {exc!r}")
        cls.tmpdir = tempfile.TemporaryDirectory(prefix="frete-popup-")

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.driver is not None:
            cls.driver.quit()
        if cls.tmpdir is not None:
            cls.tmpdir.cleanup()

    # ------------------------------------------------------------------ apoio

    def _load(self, html: str, name: str) -> None:
        path = Path(self.tmpdir.name) / f"{name}.html"
        path.write_text(html, encoding="utf-8")
        self.driver.get(path.as_uri())

    def _dismisser(self, **kwargs) -> OverlayDismisser:
        kwargs.setdefault("settle_seconds", 0.1)
        kwargs.setdefault("max_attempts", 3)
        return OverlayDismisser(self.driver, **kwargs)

    def _visible(self, selector: str) -> bool:
        return bool(
            self.driver.execute_script(
                "const el = document.querySelector(arguments[0]);"
                "if (!el || !el.isConnected) return false;"
                "const st = getComputedStyle(el);"
                "if (st.display === 'none' || st.visibility === 'hidden') return false;"
                "const r = el.getBoundingClientRect();"
                "return r.width > 2 && r.height > 2;",
                selector,
            )
        )

    # ----------------------------------------------------------------- testes

    def test_fecha_barra_de_cookies(self) -> None:
        self._load(fx.cookie_bar(), "cookie")
        self.assertTrue(self._visible("#cookieBar"), "a barra deveria comecar visivel")

        dismisser = self._dismisser()
        closed = dismisser.settle()

        self.assertEqual(closed, 1)
        self.assertFalse(self._visible("#cookieBar"))
        self.assertTrue(any("fechado" in e for e in dismisser.events), dismisser.events)

    def test_fecha_modal_pelo_x_e_nao_pelo_confirmar(self) -> None:
        self._load(fx.location_modal(), "modal")
        self.assertTrue(self._visible("#locBackdrop"))

        dismisser = self._dismisser()
        dismisser.settle()

        self.assertFalse(self._visible("#locBackdrop"))
        self.assertTrue(any("botao de dispensa" in e for e in dismisser.events), dismisser.events)

    def test_nao_mexe_em_cabecalho_fixo(self) -> None:
        self._load(fx.sticky_header(), "header")

        dismisser = self._dismisser()
        closed = dismisser.settle()

        self.assertEqual(closed, 0, f"nao deveria fechar nada: {dismisser.events}")
        self.assertTrue(self._visible("header"))
        self.assertEqual(dismisser.events, [])

    def test_modal_sem_botao_e_ocultado_quando_bloqueia_o_clique(self) -> None:
        self._load(fx.stubborn_modal(), "teimoso")
        alvo = self.driver.find_element(By.ID, "alvo")

        dismisser = self._dismisser()
        # A varredura preventiva nunca oculta via script: tenta e desiste.
        dismisser.settle()
        self.assertTrue(self._visible("#teimoso"), "settle nao deveria ocultar via script")

        liberou = dismisser.clear_path(alvo)

        self.assertTrue(liberou)
        self.assertFalse(self._visible("#teimoso"))
        self.assertTrue(any("ocultado via script" in e for e in dismisser.events), dismisser.events)

    def test_clear_path_nao_faz_nada_quando_o_caminho_esta_livre(self) -> None:
        self._load(fx.sticky_header(), "livre")
        alvo = self.driver.find_element(By.TAG_NAME, "h1")

        dismisser = self._dismisser()
        self.assertTrue(dismisser.clear_path(alvo))
        self.assertEqual(dismisser.events, [])

    def test_desligado_nao_toca_na_pagina(self) -> None:
        self._load(fx.cookie_bar(), "desligado")

        dismisser = self._dismisser(enabled=False)

        self.assertEqual(dismisser.settle(), 0)
        self.assertTrue(self._visible("#cookieBar"))
        self.assertEqual(dismisser.events, [])


@unittest.skipUnless(SELENIUM_OK, "Selenium nao esta disponivel neste ambiente")
class FluxoCompletoComPopupsTests(unittest.TestCase):
    """A consulta de frete precisa terminar mesmo com pop-ups na frente."""

    driver = None
    tmpdir = None

    @classmethod
    def setUpClass(cls) -> None:
        try:
            cls.driver = _make_driver()
        except Exception as exc:  # pragma: no cover - Chrome ausente
            raise unittest.SkipTest(f"Chrome nao disponivel: {exc!r}")
        cls.tmpdir = tempfile.TemporaryDirectory(prefix="frete-fluxo-")

    @classmethod
    def tearDownClass(cls) -> None:
        if cls.driver is not None:
            cls.driver.quit()
        if cls.tmpdir is not None:
            cls.tmpdir.cleanup()

    def test_le_o_frete_com_cookie_e_modal_na_frente(self) -> None:
        from app.pages.freight_widget_product_page import FreightWidgetProductPage

        path = Path(self.tmpdir.name) / "produto.html"
        path.write_text(fx.produto_com_popups(), encoding="utf-8")

        overlays = OverlayDismisser(self.driver, settle_seconds=0.2, max_attempts=3)
        page = FreightWidgetProductPage(
            driver=self.driver,
            timeout=10,
            slow_type_delay_ms=0,
            overlays=overlays,
        )

        page.open(path.as_uri())
        self.assertEqual(page.get_product_name(), "Colchao Casal Teste 138x28cm")

        page.fill_cep("01009-907")
        # O CEP tem que ter ido para o campo do produto, nao para o do modal.
        self.assertEqual(
            self.driver.execute_script("return document.getElementById('cepDoProduto').value;"),
            "01009-907",
        )

        page.calculate_freight()
        freight = page.read_freight_result()

        self.assertEqual(freight["price"], 189.9)
        self.assertEqual(freight["price_kind"], "PAID")
        self.assertEqual(freight["delivery_time_text"], "Em até 9 dias úteis")
        self.assertEqual(freight["delivery_mode"], "Normal")
        # Os dois pop-ups (cookie e modal) precisam ter sido registrados.
        self.assertGreaterEqual(len(overlays.events), 2, overlays.events)


if __name__ == "__main__":
    unittest.main()
