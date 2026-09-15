"""Leitura do bloco de frete no widget generico.

Sem browser: exercita direto a separacao em opcoes e o parse de cada uma,
que e onde mora a ambiguidade entre "R$ 20,00 / prazo" e "prazo / R$ 20,00".
"""

from __future__ import annotations

import unittest

from app.pages.freight_widget_product_page import FreightWidgetProductPage


def _pagina() -> FreightWidgetProductPage:
    # A leitura de texto nao depende do driver; instancia sem tocar no __init__.
    return FreightWidgetProductPage.__new__(FreightWidgetProductPage)


def _opcoes(texto: str) -> list[dict]:
    page = _pagina()
    linhas = [line.strip() for line in texto.strip().splitlines() if line.strip()]
    blocos = page._split_freight_chunks(linhas)
    parsed = (page._parse_freight_chunk(bloco) for bloco in blocos)
    return [opcao for opcao in parsed if page._is_freight_option(opcao)]


class ValorAntesDoPrazoTests(unittest.TestCase):
    """Layout "R$ 20,00 / Em ate 5 dias uteis"."""

    TEXTO = """
    Calcular frete e prazo
    R$ 20,00
    Em até 5 dias úteis
    Expressa
    R$ 12,50
    Em até 9 dias úteis
    Normal
    """

    def test_separa_duas_opcoes(self) -> None:
        self.assertEqual(len(_opcoes(self.TEXTO)), 2)

    def test_cada_valor_fica_com_o_proprio_prazo(self) -> None:
        primeira, segunda = _opcoes(self.TEXTO)

        self.assertEqual(primeira["price"], 20.0)
        self.assertEqual(primeira["delivery_time_text"], "Em até 5 dias úteis")
        self.assertEqual(primeira["delivery_mode"], "Expressa")

        self.assertEqual(segunda["price"], 12.5)
        self.assertEqual(segunda["delivery_time_text"], "Em até 9 dias úteis")
        self.assertEqual(segunda["delivery_mode"], "Normal")


class PrazoAntesDoValorTests(unittest.TestCase):
    """Layout "Em ate 5 dias uteis / R$ 20,00" — o que estava trocando os pares."""

    TEXTO = """
    Calcular frete e prazo
    Em até 5 dias úteis
    Expressa
    R$ 20,00
    Em até 9 dias úteis
    Normal
    R$ 12,50
    """

    def test_separa_duas_opcoes(self) -> None:
        self.assertEqual(len(_opcoes(self.TEXTO)), 2)

    def test_cada_valor_fica_com_o_proprio_prazo(self) -> None:
        primeira, segunda = _opcoes(self.TEXTO)

        self.assertEqual(primeira["price"], 20.0)
        self.assertEqual(primeira["delivery_time_text"], "Em até 5 dias úteis")
        self.assertEqual(primeira["delivery_mode"], "Expressa")

        self.assertEqual(segunda["price"], 12.5)
        self.assertEqual(segunda["delivery_time_text"], "Em até 9 dias úteis")
        self.assertEqual(segunda["delivery_mode"], "Normal")


class CasosDeBordaTests(unittest.TestCase):
    def test_cabecalho_do_bloco_nao_vira_opcao(self) -> None:
        opcoes = _opcoes(
            """
            Calcular frete e prazo
            Informe o CEP
            R$ 189,90
            Em até 9 dias úteis
            """
        )

        self.assertEqual(len(opcoes), 1)
        self.assertEqual(opcoes[0]["price"], 189.9)

    def test_frete_gratis_separa_opcao_e_vira_free(self) -> None:
        opcoes = _opcoes(
            """
            Em até 5 dias úteis
            Grátis
            Em até 2 dias úteis
            R$ 49,90
            """
        )

        self.assertEqual(len(opcoes), 2)
        self.assertEqual(opcoes[0]["price"], 0.0)
        self.assertEqual(opcoes[0]["price_kind"], "FREE")
        self.assertEqual(opcoes[0]["delivery_time_text"], "Em até 5 dias úteis")
        self.assertEqual(opcoes[1]["price"], 49.9)
        self.assertEqual(opcoes[1]["price_kind"], "PAID")

    def test_opcao_unica_continua_inteira(self) -> None:
        opcoes = _opcoes(
            """
            R$ 189,90
            Em até 9 dias úteis
            Normal
            """
        )

        self.assertEqual(len(opcoes), 1)
        self.assertEqual(opcoes[0]["price"], 189.9)
        self.assertEqual(opcoes[0]["delivery_time_text"], "Em até 9 dias úteis")
        self.assertEqual(opcoes[0]["delivery_mode"], "Normal")

    def test_texto_sem_valor_nao_produz_opcao(self) -> None:
        self.assertEqual(_opcoes("Informe o CEP para calcular o frete"), [])

    def test_prazo_informado_como_data(self) -> None:
        """Lojas que dizem "Receba ate <data>" em vez de "N dias uteis"."""
        opcoes = _opcoes(
            """
            Calcular frete e prazo
            R$ 69,99
            Receba até quinta-feira, 3 de setembro
            """
        )

        self.assertEqual(len(opcoes), 1)
        self.assertEqual(opcoes[0]["price"], 69.99)
        self.assertEqual(opcoes[0]["delivery_time_text"], "Receba até quinta-feira, 3 de setembro")

    def test_parcelamento_do_produto_nao_vira_frete(self) -> None:
        """O erro mais perigoso: reportar o preco do produto como frete."""
        page = _pagina()
        linhas = [
            "R$ 758,25",
            "à vista no Pix (5% OFF)",
            "ou 7x de R$ 114,14 sem juros",
            "R$ 69,99",
            "Receba até quinta-feira, 3 de setembro",
        ]
        limpas = [line for line in linhas if not page._RE_RUIDO_DE_PRECO.search(line)]

        self.assertNotIn("ou 7x de R$ 114,14 sem juros", limpas)
        self.assertNotIn("à vista no Pix (5% OFF)", limpas)
        self.assertIn("R$ 69,99", limpas)


if __name__ == "__main__":
    unittest.main()
