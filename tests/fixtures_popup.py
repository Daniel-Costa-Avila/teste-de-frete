"""Paginas de teste que reproduzem os padroes de pop-up encontrados nas lojas.

Nao imitam nenhum site especifico: sao as formas genericas (barra de cookie,
modal com backdrop, modal teimoso sem botao, cabecalho fixo legitimo) que a
rotina precisa distinguir.
"""

from __future__ import annotations

_BASE_CSS = """
* { box-sizing: border-box; }
body { margin: 0; font-family: system-ui, Arial, sans-serif; }
.conteudo { padding: 24px; }
button { font: inherit; padding: 8px 14px; cursor: pointer; }
input { font: inherit; padding: 8px; }
"""

_COOKIE_BAR = """
<div class="cookie-bar" id="cookieBar" style="
  position: fixed; left: 0; right: 0; bottom: 0; z-index: 9000;
  background: #222; color: #fff; padding: 18px; display: flex;
  align-items: center; justify-content: space-between; gap: 16px;">
  <span>Cookies: este site utiliza cookies para personalizar conteudos e melhorar
  a sua experiencia. Ao continuar navegando voce concorda com a nossa Politica de Privacidade.</span>
  <button type="button" onclick="document.getElementById('cookieBar').remove()">Concordar e fechar</button>
</div>
"""

_LOCATION_MODAL = """
<div class="modal-backdrop" id="locBackdrop" style="
  position: fixed; inset: 0; z-index: 9500; background: rgba(0,0,0,0.5);">
  <div role="dialog" aria-modal="true" style="
    position: absolute; top: 80px; left: 40px; width: 380px; background: #fff;
    padding: 20px; border-radius: 8px;">
    <button type="button" aria-label="Fechar" style="float: right; border: 0; background: none;"
      onclick="document.getElementById('locBackdrop').remove()">&times;</button>
    <h2>Escolha sua localizacao</h2>
    <p>Para ver as melhores condicoes de <b>frete</b> e entrega para sua regiao.</p>
    <input id="cepDoModal" type="text" placeholder="Insira o CEP" />
    <button type="button">Confirmar</button>
  </div>
</div>
"""

# Modal sem nenhum botao: so ESC ou ocultar via script resolvem.
_STUBBORN_MODAL = """
<div class="popup-overlay" id="teimoso" style="
  position: fixed; inset: 0; z-index: 9800; background: rgba(20,20,20,0.75);
  display: flex; align-items: center; justify-content: center; color: #fff;">
  <div style="background:#333; padding:40px; width:60%; height:40%;">
    Oferta especial so hoje
  </div>
</div>
"""

_PRODUTO_JSON_LD = """
<script type="application/ld+json">
{"@context":"https://schema.org","@type":"Product","name":"Colchao Casal Teste 138x28cm"}
</script>
"""

_WIDGET_FRETE = """
<section class="frete" style="margin-top: 24px; border: 1px solid #ccc; padding: 16px;">
  <h3>Calcular frete e prazo</h3>
  <input id="cepDoProduto" type="text" placeholder="Informe o CEP" />
  <button type="button" id="btnFrete">Calcular frete</button>
  <div id="resultadoFrete" style="margin-top: 12px;"></div>
</section>
<script>
  document.getElementById('btnFrete').addEventListener('click', function () {
    var cep = document.getElementById('cepDoProduto').value;
    document.getElementById('resultadoFrete').innerText =
      'R$ 189,90\\nEm até 9 dias úteis\\nNormal\\n' + cep;
  });
</script>
"""


def _pagina(titulo: str, corpo: str) -> str:
    return f"""<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8"><title>{titulo}</title>
<style>{_BASE_CSS}</style></head>
<body>{corpo}</body></html>"""


def cookie_bar() -> str:
    """Barra de cookies fixa no rodape, com botao de aceite."""
    return _pagina("Barra de cookies", '<div class="conteudo"><h1>Produto</h1></div>' + _COOKIE_BAR)


def location_modal() -> str:
    """Modal central com backdrop, campo de CEP proprio e botao de fechar."""
    return _pagina("Modal de localizacao", '<div class="conteudo"><h1>Produto</h1></div>' + _LOCATION_MODAL)


def stubborn_modal() -> str:
    """Sobreposicao que cobre a tela e nao oferece botao de dispensa."""
    return _pagina(
        "Modal teimoso",
        '<div class="conteudo"><h1>Produto</h1>'
        '<button type="button" id="alvo">Calcular frete</button></div>' + _STUBBORN_MODAL,
    )


def sticky_header() -> str:
    """Cabecalho fixo legitimo: a rotina nao pode fechar nada aqui."""
    corpo = """
    <header style="position: sticky; top: 0; z-index: 500; background: #eee; padding: 16px;">
      <nav>Departamentos | Servicos | Ajuda</nav>
    </header>
    <div class="conteudo"><h1>Produto</h1><p>Conteudo normal da pagina.</p></div>
    """
    return _pagina("Cabecalho fixo", corpo)


def produto_com_popups() -> str:
    """Pagina de produto completa com barra de cookie E modal por cima."""
    corpo = (
        _PRODUTO_JSON_LD
        + '<header style="position: sticky; top: 0; z-index: 400; background: #eee; padding: 12px;">'
        '<nav>Departamentos</nav></header>'
        + '<div class="conteudo"><h1>Colchao Casal Teste 138x28cm</h1>'
        + _WIDGET_FRETE
        + "</div>"
        + _COOKIE_BAR
        + _LOCATION_MODAL
    )
    return _pagina("Produto com pop-ups", corpo)
