# Manual — BUSCA FRETE

Sistema web para consultar/capturar **frete** em páginas de produto usando automação (Selenium) e gerar **resultados em tela + XLSX/CSV**.

---

## 1) Para quem é este sistema

- Operação / suporte / comercial que precisa validar frete por **URL + CEP**
- Auditoria em lote via planilha (Excel/CSV)

---

## 2) O que o sistema faz (visão simples)

1. Você envia uma **planilha** com links dos produtos e CEP(s)
2. O sistema abre as páginas, preenche o CEP e captura:
   - Valor do frete (ou grátis)
   - Prazo
   - Modalidade
3. Você acompanha em tempo real e baixa o resultado em **XLSX**

---

## 3) Como acessar (URL)

- Local: `http://127.0.0.1:<PORTA>`
- Servidor com domínio/túnel (ex.: Tailscale/Funnel): `https://SEU-DOMINIO/frete`

Observação: quando o sistema é servido em um caminho (ex.: `/frete`), use `PUBLIC_URL_PREFIX=/frete` para garantir que botões/links e CSS funcionem corretamente.

---

## 4) Instalação (Windows, modo local)

Pré-requisitos:
- Python 3.12+ instalado

Passo a passo (PowerShell):

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Crie o `.env` (copie do exemplo):

```powershell
Copy-Item .env.example .env
```

Inicie:

```powershell
python -m app.web
```

Abra no navegador:
- `http://127.0.0.1:5100` (ou a porta do seu `.env`)

---

## 5) Configuração (.env)

O arquivo `.env` define como o sistema vai rodar. Principais variáveis:

- `HOST` / `PORT`: endereço e porta do servidor web
  - local: `HOST=127.0.0.1`
  - para acesso externo/túnel: `HOST=0.0.0.0`
- `HEADLESS`: `1` roda o navegador “em segundo plano” (recomendado em servidor)
- `USE_REMOTE`: `1` usa um Selenium remoto (Selenoid)
- `SELENOID_URL`: endereço do Selenoid (quando `USE_REMOTE=1`)
- `ALLOWED_URL_HOSTS`: restringe quais domínios podem ser testados (segurança)
- `BASIC_AUTH_USER` / `BASIC_AUTH_PASS`: coloca senha no painel (recomendado em servidor)
- `PUBLIC_URL_PREFIX`: quando o sistema estiver publicado em subcaminho (ex.: `PUBLIC_URL_PREFIX=/frete`)

Dica: veja todos os exemplos comentados em `.env.example`.

---

## 6) Usando o sistema (passo a passo)

### 6.1) Lote por planilha (principal)

1) Na home, baixe o template (XLSX/CSV)  
2) Preencha com os links dos produtos  
3) Envie a planilha e informe o CEP padrão  
4) Acompanhe a tela do lote (atualiza automaticamente)  
5) Baixe o XLSX final

### 6.2) Retomar pesquisas

Se você voltar para a home no meio de uma execução, o sistema mostra:
- “Retomar pesquisa”
- “Pesquisas recentes”

Assim você não perde o acesso ao lote em andamento.

### 6.3) Cancelar

Na tela do lote, você pode:
- **Cancelar lote** (para de iniciar novas execuções e tenta parar as atuais)

Na tela da execução, você pode:
- **Cancelar** (tenta parar a execução atual)

---

## 7) Status (o que significa cada resultado)

Os status aparecem na tela e também no XLSX/CSV.

- `SUCCESS`: frete capturado com sucesso
- `FREIGHT_NOT_RETURNED`: sistema não encontrou resultado de frete no DOM (mesmo após tentativas)
- `ERRO NO LINK`: link não abriu corretamente ou não foi possível identificar o produto
- `BLOCKED`: possível bloqueio/anti-bot detectado (captcha, acesso negado, etc.)
- `TIMEOUT`: tempo excedido em alguma etapa
- `BROWSER_DISCONNECTED`: sessão do navegador caiu (mais comum em Selenium remoto com timeout baixo)
- `ERROR`: erro inesperado durante a execução
- `CANCELED` / `CANCEL REQUESTED`: cancelado pelo usuário

---

## 8) Arquivos gerados (artifacts)

O sistema salva evidências em `artifacts/`:

- `*_error.png` / `*_error.html`: screenshot e HTML quando dá erro
- `*_freight_not_returned.png` / `*_freight_not_returned.html`: quando não encontrou o frete
- `results.csv`: histórico em CSV (incremental)

Por padrão, screenshots e HTML com mais de 2 dias são apagados automaticamente.

Isso é importante para auditoria e diagnóstico.

---

## 9) Instalação com Docker (recomendado para servidor)

Pré-requisitos:
- Docker + Docker Compose

```bash
cp .env.example .env
docker compose -f docker-compose.server.yml up -d --build
```

---

## 10) Selenium remoto (Selenoid) — quando usar

Use quando:
- você quer isolar o navegador do processo do app
- precisa de mais estabilidade em servidor

Passos (exemplo local):

```powershell
docker compose -f docker-compose.server.yml up -d selenoid
$env:USE_REMOTE="1"
python -m app.web
```

Se estiver em servidor Docker, use o `docker-compose.server.yml` que já inclui o serviço `selenoid`.

---

## 11) Publicar com Tailscale (acesso público)

Se você quer que **qualquer pessoa** acesse sem instalar Tailscale, use **Funnel**.

Requisitos:
- Tailscale instalado e Funnel habilitado no admin da tailnet

Exemplo (publicar `/frete` para o app local):

```powershell
tailscale serve reset
tailscale funnel --bg --https 443 --set-path /frete http://127.0.0.1:5100
tailscale serve status
```

Importante:
- No `.env`, configure `PUBLIC_URL_PREFIX=/frete`

---

## 12) Segurança (recomendado em produção)

- Ative senha no painel:
  - `BASIC_AUTH_USER`
  - `BASIC_AUTH_PASS`
- Restrinja domínios permitidos:
  - `ALLOWED_URL_HOSTS=probel.com.br,.probel.com.br`
- Evite publicar sem autenticação em internet aberta.

---

## 13) Solução de problemas (rápido)

### 13.1) “Caiu” / não abre no domínio
- Verifique se o app está no ar:
  - `http://127.0.0.1:5100/health`
- Se estiver usando Tailscale Funnel, confira:
  - `tailscale serve status`

### 13.2) Página sem CSS / “quebrada” em `/frete`
- Defina `PUBLIC_URL_PREFIX=/frete` e reinicie o app

### 13.3) Frete visível no screenshot mas vazio no resultado
- Baixe/abra o `*_error.html` e `*_error.png` e compare com o DOM
- Isso normalmente é variação de layout, carregamento assíncrono ou bloqueio/anti-bot

---

## 14) Estrutura do projeto (para manutenção)

- `app/web/server.py`: servidor web (rotas, UI, jobs em lote, cancelamento)
- `app/services/freight_test_service.py`: orquestra execução (abrir, preencher CEP, ler frete)
- `app/pages/*`: regras por tipo de página (Probel / widget genérico)
- `app/infra/*`: exportações (XLSX/CSV), parsing de planilha, etc.
- `artifacts/`: evidências e resultados
