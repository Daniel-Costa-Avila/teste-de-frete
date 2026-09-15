"""Migracao da base antiga para o modelo multi-loja.

O ponto sensivel: a tabela products e reconstruida para remover o UNIQUE de
product_id, e product_ceps aponta para ela com ON DELETE CASCADE. Se as chaves
estrangeiras ficarem ligadas durante o DROP, os vinculos produto x CEP somem.
"""

from __future__ import annotations

import glob
import os
import sqlite3
import tempfile
import unittest

from app.web.server import _ensure_base_db

# Schema anterior a migracao, com UNIQUE em product_id e sem a coluna site.
SCHEMA_ANTIGO = """
CREATE TABLE ceps (
    cep TEXT PRIMARY KEY,
    uf TEXT NOT NULL DEFAULT '',
    cidade TEXT,
    regiao TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE products (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name TEXT,
    product_name TEXT,
    product_id TEXT UNIQUE,
    url TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE product_ceps (
    product_id INTEGER NOT NULL,
    cep TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (product_id, cep),
    FOREIGN KEY (product_id) REFERENCES products(id) ON DELETE CASCADE,
    FOREIGN KEY (cep) REFERENCES ceps(cep) ON DELETE RESTRICT
);
"""


class MigracaoMultiLojaTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="frete-migra-", ignore_cleanup_errors=True)
        self.db = os.path.join(self._tmp.name, "base.db")

        conn = sqlite3.connect(self.db)
        try:
            conn.executescript(SCHEMA_ANTIGO)
            conn.execute(
                "INSERT INTO products (id, group_name, product_name, product_id, url) "
                "VALUES (1, 'Linha A', 'Colchao', 'PA100', 'https://www.loja.com.br/colchao/p')"
            )
            conn.execute(
                "INSERT INTO products (id, group_name, product_name, product_id, url) "
                "VALUES (2, 'Linha A', 'Travesseiro', 'PA200', 'https://outra.com/travesseiro')"
            )
            for cep in ("01001-000", "02002-000"):
                conn.execute("INSERT INTO ceps (cep) VALUES (?)", (cep,))
                conn.execute("INSERT INTO product_ceps (product_id, cep) VALUES (1, ?)", (cep,))
            conn.execute("INSERT INTO product_ceps (product_id, cep) VALUES (2, '01001-000')")
            conn.commit()
        finally:
            conn.close()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def consultar(self, sql: str) -> list[tuple]:
        conn = sqlite3.connect(self.db)
        try:
            return conn.execute(sql).fetchall()
        finally:
            conn.close()

    def test_migracao_preserva_produtos_e_vinculos(self) -> None:
        antes_produtos = self.consultar("SELECT id, product_id, url FROM products ORDER BY id")
        antes_vinculos = self.consultar("SELECT product_id, cep FROM product_ceps ORDER BY 1, 2")
        self.assertEqual(len(antes_vinculos), 3)

        _ensure_base_db(self.db)

        self.assertEqual(
            self.consultar("SELECT id, product_id, url FROM products ORDER BY id"), antes_produtos
        )
        # O ponto critico: os vinculos nao podem ter sido apagados em cascata.
        self.assertEqual(
            self.consultar("SELECT product_id, cep FROM product_ceps ORDER BY 1, 2"), antes_vinculos
        )
        self.assertEqual(self.consultar("PRAGMA foreign_key_check"), [])

    def test_migracao_remove_o_unique_de_product_id(self) -> None:
        _ensure_base_db(self.db)

        conn = sqlite3.connect(self.db)
        try:
            unicos = []
            for row in conn.execute("PRAGMA index_list(products)").fetchall():
                if not row[1]:
                    continue
                colunas = [i[2] for i in conn.execute(f'PRAGMA index_info("{row[1]}")').fetchall()]
                if row[2] and colunas == ["product_id"]:
                    unicos.append(row[1])
            self.assertEqual(unicos, [], "product_id nao pode mais ser unico")

            # E o mesmo SKU agora entra duas vezes, em lojas diferentes.
            conn.execute(
                "INSERT INTO products (product_name, product_id, site, url) "
                "VALUES ('Colchao', 'PA100', 'terceira.com', 'https://terceira.com/colchao')"
            )
            conn.commit()
            self.assertEqual(
                conn.execute(
                    "SELECT COUNT(*) FROM products WHERE product_id = 'PA100'"
                ).fetchone()[0],
                2,
            )
        finally:
            conn.close()

    def test_migracao_preenche_a_loja_e_guarda_backup(self) -> None:
        _ensure_base_db(self.db)

        self.assertEqual(
            self.consultar("SELECT site FROM products ORDER BY id"),
            [("loja.com.br",), ("outra.com",)],
        )
        self.assertTrue(glob.glob(self.db + "*.bak"), "a migracao precisa deixar um backup")

    def test_rodar_de_novo_nao_faz_nada(self) -> None:
        _ensure_base_db(self.db)
        depois_da_primeira = self.consultar("SELECT id, product_id, site, url FROM products ORDER BY id")
        backups = len(glob.glob(self.db + "*.bak"))

        _ensure_base_db(self.db)

        self.assertEqual(
            self.consultar("SELECT id, product_id, site, url FROM products ORDER BY id"),
            depois_da_primeira,
        )
        self.assertEqual(len(glob.glob(self.db + "*.bak")), backups, "nao deve refazer a migracao")


if __name__ == "__main__":
    unittest.main()
