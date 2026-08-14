# BUSCA FRETE (Selenium)

UI web (Flask) para rodar testes de frete em páginas de produto.

## Manual completo

Veja `MANUAL.md`.

## Rodar localmente (dev)

```powershell
pip install -r requirements.txt
python -m app.web
```

Abra `http://127.0.0.1:5000`.

## Rodar com Selenoid (opcional)

Suba o Selenoid local:

```powershell
docker compose -f docker-compose.server.yml up -d selenoid
```

Ative o remoto via variável de ambiente:

```powershell
$env:USE_REMOTE="1"
python -m app.web
```

## Planilha de produtos

Na home, baixe o template e envie a planilha `.xlsx`/`.csv` para executar em lote.

## Base local de produtos e CEPs (SQLite)

Para manter uma base pronta para reutilizar, gere o banco local:

```powershell
.\.venv\Scripts\python.exe scripts\build_base_dados.py
```

Isso cria/atualiza `artifacts/base_frete.db` com:

- `ceps`: dados da planilha `Faixas de CEP.xlsx`
- `products`: cadastro de produtos
- `product_ceps`: relacao produto x CEP

Se quiser importar produtos junto:

```powershell
.\.venv\Scripts\python.exe scripts\build_base_dados.py --products-file artifacts\produtos_entrada_template.xlsx
```

Se existir `Base de teste.xlsx` na raiz do projeto, ele passa a ser usado como base padrão de produtos ao rodar o script sem `--products-file`.

Limites de volume:

- Por padrão, o sistema opera sem limite para quantidade de linhas/execuções.
- Se quiser limitar, configure variáveis de ambiente:
  - `MAX_SHEET_ROWS`
  - `MAX_SHEET_JOBS`
  - `MAX_DB_JOBS`
- Valor `0` (ou negativo) significa ilimitado.

Desempenho (paralelismo):

- `MAX_CONCURRENT_JOBS`: base de paralelismo global.
- `SHEET_PARALLEL_LIMIT`: workers para execução via planilha.
- `DB_PARALLEL_LIMIT`: workers para execução via base salva.

Sugestão inicial:

- Ambiente local forte: `SHEET_PARALLEL_LIMIT=8` e `DB_PARALLEL_LIMIT=8`
- Ambiente remoto/Selenoid: começar com `2` a `4` e subir gradualmente

## Contrato do frete (API)

No JSON de resultado (`/api/runs/<id>`):

- `result.freight.price`: `number` (valor) ou `null` (desconhecido/nÃ£o identificado)
- `result.freight.price_kind`: `"FREE"` (frete grÃ¡tis), `"PAID"` (valor identificado), `"UNKNOWN"` (sem valor)
- Regra: frete grÃ¡tis Ã© representado como `price = 0.0` + `price_kind = "FREE"`

## Deploy no Render

Este projeto usa Selenium/Chrome. Para rodar no Render, a opção mais simples é deploy via Docker (inclui Chromium + chromedriver) e usar `HEADLESS=1`.

## Deploy em servidor (Docker)

Recomendado para VPS/servidor próprio: subir o app como container e colocar um proxy (Nginx/Caddy/Traefik) na frente se precisar de domínio/HTTPS.

Arquivos incluídos:

- `.env.example` (copie para `.env` e ajuste)
- `docker-compose.server.yml` (sobe o app e persiste `./artifacts`)

Comandos (no servidor):

```bash
cp .env.example .env
docker compose -f docker-compose.server.yml up -d --build
```

Segurança (fortemente recomendado):

- Defina `BASIC_AUTH_USER` e `BASIC_AUTH_PASS` no `.env`
- Defina `ALLOWED_URL_HOSTS` para restringir quais domínios podem ser testados
- Ajuste `ARTIFACT_RETENTION_DAYS=2` para limpar screenshots e HTML antigos automaticamente

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
