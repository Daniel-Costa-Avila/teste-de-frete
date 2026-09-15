"""Cadastro de produtos e CEPs pela tela da base.

O que estes testes protegem: nenhum caminho de cadastro pode devolver erro 500.
Duplicidade de ID ou de link e situacao normal do dia a dia e precisa virar
mensagem na tela.
"""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from urllib.parse import parse_qs, urlparse

from app.web.server import create_app


class CadastroNaBaseTests(unittest.TestCase):
    def setUp(self) -> None:
        # O agendador segura scheduler.lock.db ao lado do banco e no Windows
        # isso impede apagar a pasta; nao e problema do que esta sendo testado.
        self._tmp = tempfile.TemporaryDirectory(prefix="frete-base-", ignore_cleanup_errors=True)
        self.db = os.path.join(self._tmp.name, "base.db")
        self._db_anterior = os.environ.get("FREIGHT_DB_PATH")
        os.environ["FREIGHT_DB_PATH"] = self.db

        app = create_app()
        app.testing = False  # queremos ver o 500 se ele voltar, nao a excecao
        self.client = app.test_client()

        # Base minima: um produto ja cadastrado.
        self.post_produto(product_id="PA100", url="https://loja.com/produto-a", nome="Produto A")

    def tearDown(self) -> None:
        if self._db_anterior is None:
            os.environ.pop("FREIGHT_DB_PATH", None)
        else:
            os.environ["FREIGHT_DB_PATH"] = self._db_anterior
        self._tmp.cleanup()

    # ------------------------------------------------------------------ apoio

    def post_produto(self, *, product_id="", url="", nome="", ceps="") -> tuple[int, str, str]:
        resposta = self.client.post(
            "/db/add-product",
            data={
                "product_id": product_id,
                "url": url,
                "product_name": nome,
                "ceps_for_product": ceps,
            },
        )
        return self._aviso(resposta)

    def post_ceps(self, texto: str) -> tuple[int, str, str]:
        return self._aviso(self.client.post("/db/add-ceps", data={"ceps_text": texto}))

    def _aviso(self, resposta) -> tuple[int, str, str]:
        query = parse_qs(urlparse(resposta.headers.get("Location", "")).query)
        return (
            resposta.status_code,
            query.get("kind", ["success"])[0],
            query.get("db_notice", [""])[0],
        )

    def produtos(self) -> list[tuple]:
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(
                "SELECT product_id, url, product_name FROM products ORDER BY id"
            ).fetchall()
        finally:
            conn.close()

    def sites(self) -> list[str]:
        conn = sqlite3.connect(self.db)
        try:
            return [r[0] for r in conn.execute("SELECT site FROM products ORDER BY id").fetchall()]
        finally:
            conn.close()

    # ----------------------------------------------------------------- testes

    def test_produto_novo_e_cadastrado(self) -> None:
        status, kind, msg = self.post_produto(
            product_id="PA200", url="https://loja.com/produto-b", nome="Produto B"
        )

        self.assertEqual(status, 302)
        self.assertEqual(kind, "success")
        self.assertIn("cadastrado", msg)
        self.assertEqual(len(self.produtos()), 2)

    def test_mesmo_produto_em_lojas_diferentes_vira_um_cadastro_por_loja(self) -> None:
        """O caso real: o mesmo SKU vendido em varios sites."""
        _, kind2, msg2 = self.post_produto(
            product_id="PA100", url="https://outraloja.com/produto-a", nome="Produto A"
        )
        _, kind3, _ = self.post_produto(
            product_id="PA100", url="https://terceiraloja.com/produto-a", nome="Produto A"
        )

        self.assertEqual(kind2, "success")
        self.assertEqual(kind3, "success")
        self.assertIn("mesmo ID já está em mais", msg2)
        # Tres cadastros independentes, um por loja.
        self.assertEqual(
            self.produtos(),
            [
                ("PA100", "https://loja.com/produto-a", "Produto A"),
                ("PA100", "https://outraloja.com/produto-a", "Produto A"),
                ("PA100", "https://terceiraloja.com/produto-a", "Produto A"),
            ],
        )

    def test_a_loja_e_gravada_a_partir_do_link(self) -> None:
        self.post_produto(product_id="PA100", url="https://www.outraloja.com.br/x", nome="A")

        self.assertEqual(
            self.sites(), ["loja.com", "outraloja.com.br"]
        )

    def test_link_repetido_atualiza_em_vez_de_duplicar(self) -> None:
        status, kind, msg = self.post_produto(
            product_id="PA999", url="https://loja.com/produto-a", nome="Produto A renomeado"
        )

        self.assertEqual(status, 302)
        self.assertEqual(kind, "success")
        self.assertIn("atualizado", msg)
        self.assertEqual(
            self.produtos(), [("PA999", "https://loja.com/produto-a", "Produto A renomeado")]
        )

    def test_produto_sem_id_pode_ser_cadastrado(self) -> None:
        status, kind, _ = self.post_produto(url="https://loja.com/sem-id", nome="Sem ID")

        self.assertEqual(status, 302)
        self.assertEqual(kind, "success")
        self.assertEqual(len(self.produtos()), 2)

    def test_dois_produtos_sem_id_nao_conflitam(self) -> None:
        self.post_produto(url="https://loja.com/sem-id-1")
        status, kind, _ = self.post_produto(url="https://loja.com/sem-id-2")

        self.assertEqual(status, 302)
        self.assertEqual(kind, "success")
        self.assertEqual(len(self.produtos()), 3)

    def test_link_invalido_vira_mensagem_e_nao_pagina_de_erro(self) -> None:
        status, kind, msg = self.post_produto(product_id="PA300", url="isso-nao-e-um-link")

        self.assertEqual(status, 302)
        self.assertEqual(kind, "error")
        self.assertIn("http", msg)

    def test_ceps_novos_sao_cadastrados(self) -> None:
        status, kind, msg = self.post_ceps("01001-000, 02002-000")

        self.assertEqual(status, 302)
        self.assertEqual(kind, "success")
        self.assertIn("2 CEPs novos", msg)

    def test_ceps_repetidos_nao_duplicam(self) -> None:
        self.post_ceps("01001-000")
        status, kind, msg = self.post_ceps("01001-000")

        self.assertEqual(status, 302)
        self.assertEqual(kind, "success")
        self.assertIn("Nenhum CEP novo", msg)

    def test_cep_invalido_vira_mensagem_e_nao_pagina_de_erro(self) -> None:
        status, kind, msg = self.post_ceps("abc def")

        self.assertEqual(status, 302)
        self.assertEqual(kind, "error")
        self.assertIn("00000-000", msg)

    def test_cadastro_com_ceps_vincula_ao_produto(self) -> None:
        status, kind, msg = self.post_produto(
            product_id="PA400",
            url="https://loja.com/produto-c",
            nome="Produto C",
            ceps="01001-000 02002-000",
        )

        self.assertEqual(status, 302)
        self.assertEqual(kind, "success")
        self.assertIn("CEPs vinculados: 2", msg)


if __name__ == "__main__":
    unittest.main()
