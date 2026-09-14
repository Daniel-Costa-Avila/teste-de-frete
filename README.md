# BUSCA FRETE (Selenium)

UI web (Flask) para rodar testes de frete em páginas de produto.

## Integração BrasilAPI

O menu **BrasilAPI** permite consultar CEPs, estados e municípios e adicionar um
CEP consultado à base de testes. Em **Planilha**, o botão
**Consultar endereço** verifica o endereço do CEP antes de executar o frete.

A integração utiliza `https://brasilapi.com.br/api`, sem chave e sem precisar
executar o projeto Node da pasta `Api Brasil`. Consultas são pontuais, acionadas
pelo usuário, com timeout de 10 segundos e cache em memória por uma hora
(até 256 consultas por processo). Indisponibilidade do provedor não impede os
testes de frete. A inclusão na base usa o cadastro existente de CEPs; endereço,
UF e município consultados são exibidos, mas não gravados nesse cadastro.

Rotas: `/brasil-api`, `/api/brasil/cep?valor=01001000`,
`/api/brasil/estados` e `/api/brasil/municipios?valor=SP`.
As rotas respeitam a autenticação Basic Auth e o prefixo público do sistema.
Valores e prazos de frete continuam sendo obtidos nas lojas.

Recursos adicionais na mesma tela:

- **Geolocalização**: cidade e país por latitude (-90 a 90) e longitude (-180 a 180).
  Aceita vírgula ou ponto decimal; não solicita localização do navegador.
  Rota: `/api/brasil/geolocalizacao?latitude=-23.55&longitude=-46.63`.
- **Dias úteis**: lista e total entre datas inclusivas, com no máximo 366 dias
  de diferença. Exclui fins de semana; por padrão desconta feriados nacionais.
  Feriados estaduais e municipais não entram no cálculo do provedor.
  Rota: `/api/brasil/diasuteis?dataInicial=2026-09-01&dataFinal=2026-09-08&incluirFeriadosNacionais=true`.
- **Municípios IBGE**: lista por UF com busca local por nome ou código,
  ignorando acentos, sem novas chamadas à API durante a filtragem.

As consultas dependem da disponibilidade da BrasilAPI. Respostas HTTP 403 do
provedor são exibidas como indisponibilidade, sem apresentar dados simulados.

Documentação do provedor: https://brasilapi.com.br/docs

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
