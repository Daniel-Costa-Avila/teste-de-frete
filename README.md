# Teste de Frete (Selenium)

UI web (Flask) para rodar testes de frete em páginas de produto.

## Rodar localmente (dev)

```powershell
pip install -r requirements.txt
python -m app.web
```

Abra `http://127.0.0.1:5000`.

## Rodar com Selenoid (remoto)

Suba o Selenoid local:

```powershell
docker compose up -d
```

Marque “Usar Selenoid (remoto)” na UI.

## Planilha de produtos

Na home, baixe o template e envie a planilha `.xlsx`/`.csv` para executar em lote.

## Deploy no Render

Este projeto usa Selenium/Chrome. Para rodar no Render, a opção mais simples é deploy via Docker (inclui Chromium + chromedriver) e usar `HEADLESS=1`.

