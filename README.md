# Teste de Frete (Selenium)

UI web (Flask) para rodar testes de frete em páginas de produto.

## Rodar localmente (dev)

```powershell
pip install -r requirements.txt
python -m app.web
```

Abra `http://127.0.0.1:5000`.

## Rodar com Selenoid (opcional)

Suba o Selenoid local:

```powershell
docker compose up -d
```

Ative o remoto via variável de ambiente:

```powershell
$env:USE_REMOTE="1"
python -m app.web
```

## Planilha de produtos

Na home, baixe o template e envie a planilha `.xlsx`/`.csv` para executar em lote.

## Contrato do frete (API)

No JSON de resultado (`/api/runs/<id>`):

- `result.freight.price`: `number` (valor) ou `null` (desconhecido/nÃ£o identificado)
- `result.freight.price_kind`: `"FREE"` (frete grÃ¡tis), `"PAID"` (valor identificado), `"UNKNOWN"` (sem valor)
- Regra: frete grÃ¡tis Ã© representado como `price = 0.0` + `price_kind = "FREE"`

## Deploy no Render

Este projeto usa Selenium/Chrome. Para rodar no Render, a opção mais simples é deploy via Docker (inclui Chromium + chromedriver) e usar `HEADLESS=1`.

## Deploy no Firebase (Hosting + Cloud Run)

O Firebase Hosting **não executa Python**. O caminho recomendado é:

1) Deploy do backend (Flask + Selenium) no **Cloud Run** usando o `Dockerfile`
2) Configurar o **Firebase Hosting** para fazer proxy (rewrite) para o Cloud Run

Arquivos já incluídos no repositório: `firebase.json` (rewrite) e `public/`.

## Deploy no Azure (recomendado: App Service como Container)

Este projeto depende de Chromium + chromedriver (Selenium). O jeito mais confiável de rodar no Azure é publicar como
**container** (o `Dockerfile` já instala as dependências).

### Azure App Service (Web App for Containers)

1) Crie um **Web App for Containers** (Linux).
2) Configure o deploy apontando para o `Dockerfile` (GitHub Actions / ACR).
3) Em **Configurações → Variáveis de ambiente (App settings)**, defina:

- `WEBSITES_PORT=8080`
- `PORT=8080`
- `HEADLESS=1`
- `DEBUG=0`

O start do app usa `startup.sh` e respeita `PORT`/`WEBSITES_PORT`.
