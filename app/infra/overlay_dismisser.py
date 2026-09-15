"""Fecha pop-ups, modais e banners de cookies que atrapalham a automacao.

A rotina e generica de proposito: nao ha selector por site. Um elemento so e
tratado como sobreposicao quando parece um overlay pela estrutura (role=dialog,
position fixed, z-index alto, classe/id com modal|cookie|popup...) E se comporta
como um: cobre boa parte da tela, fala de cookies/consentimento, ou esta
literalmente na frente do elemento que a automacao precisa clicar — isso ultimo
medido com document.elementFromPoint, nao por chute.

Ordem de tentativa, da menos para a mais invasiva:
  1. clicar num botao de dispensa dentro do proprio overlay (X, Fechar, Aceitar...)
  2. tecla ESC
  3. ultimo recurso: ocultar o elemento via script — so quando ha prova de que
     ele bloqueia um clique especifico, nunca na varredura preventiva.

Toda a inspecao pesada roda em JS numa chamada so: o Selenium faz milhares de
consultas por lote e um round-trip por atributo sairia caro.
"""

from __future__ import annotations

import logging
import time

from selenium.webdriver.common.by import By
from selenium.webdriver.common.keys import Keys
from selenium.webdriver.remote.webdriver import WebDriver

logger = logging.getLogger(__name__)


_JS_HELPERS = r"""
function ovStyle(el) {
  try { return window.getComputedStyle(el); } catch (e) { return null; }
}
function ovClass(el) {
  const c = el.className;
  return String(c && c.baseVal !== undefined ? c.baseVal : (c || ''));
}
function ovVisible(el, st) {
  if (!st) return false;
  if (st.display === 'none' || st.visibility === 'hidden') return false;
  if (parseFloat(st.opacity || '1') < 0.05) return false;
  const r = el.getBoundingClientRect();
  if (r.width < 2 || r.height < 2) return false;
  if (r.bottom < 0 || r.right < 0) return false;
  if (r.top > window.innerHeight || r.left > window.innerWidth) return false;
  return true;
}
function ovDescribe(el) {
  if (!el) return '<nulo>';
  const cls = ovClass(el).trim().split(/\s+/).filter(Boolean).slice(0, 2).join('.');
  const txt = (el.innerText || '').replace(/\s+/g, ' ').trim().slice(0, 70);
  return '<' + el.tagName.toLowerCase() + (el.id ? '#' + el.id : '')
    + (cls ? '.' + cls : '') + '>' + (txt ? ' "' + txt + '"' : '');
}
"""

# Estrutura de overlay: e o tipo de elemento que PODE ser uma sobreposicao.
_JS_LOOKS_LIKE = r"""
const OV_NAMED = /(modal|popup|pop-up|overlay|dialog|cookie|consent|lgpd|gdpr|privac|backdrop|lightbox|interstitial|drawer|newsletter|subscribe|paywall)/i;
const OV_CHROME = /(header|navbar|nav-bar|topbar|top-bar|menu|breadcrumb|footer|skip-link)/i;
const OV_FECHAR = /^\s*(fechar|close|dismiss|cerrar|×|✕|✖|⨯|x)\s*$/i;

// Um controle de fechar explicito e o sinal mais forte de "isto e um pop-up
// dispensavel". Menus e dropdowns praticamente nunca tem um botao X.
function ovHasCloseControl(el) {
  let visto = 0;
  for (const c of el.querySelectorAll('button, [role=button], a, [aria-label], [class*=close], [id*=close]')) {
    if (visto++ > 30) break;
    const rotulos = [
      c.getAttribute('aria-label') || '',
      c.getAttribute('title') || '',
      (c.innerText || '').trim(),
    ];
    if (rotulos.some(t => OV_FECHAR.test(t))) return true;
  }
  return false;
}

// Caminho inverso: um botao "Fechar" visivel denuncia o pop-up ao redor dele.
// Pega os casos que a varredura por classe/posicao nao alcanca — caixas
// ancoradas, fundas no DOM e com nomes de classe gerados por build.
function ovRootsFromCloseButton(btn) {
  // Devolve todos os ancestrais posicionados e de tamanho plausivel. Qual
  // deles e o pop-up de fato quem decide e ovIsIntrusive, logo adiante.
  const roots = [];
  let node = btn.parentElement;
  for (let i = 0; i < 8 && node && node !== document.body; i++) {
    const st = ovStyle(node);
    if (st && (st.position === 'fixed' || st.position === 'absolute' || st.position === 'sticky')) {
      const r = node.getBoundingClientRect();
      // Nunca subir ate um ancestral do tamanho da pagina inteira.
      if (r.height <= window.innerHeight * 1.5 && r.width <= window.innerWidth * 1.5) {
        roots.push(node);
      }
    }
    node = node.parentElement;
  }
  return roots;
}

function ovLooksLikeOverlay(el, st) {
  const role = (el.getAttribute('role') || '').toLowerCase();
  if (role === 'dialog' || role === 'alertdialog') return true;
  if (el.getAttribute('aria-modal') === 'true') return true;
  if (el.tagName === 'DIALOG' && el.hasAttribute('open')) return true;
  // Cabecalho/menu fixo nao e pop-up: nunca deve ser fechado.
  if (el.tagName === 'HEADER' || el.tagName === 'NAV' || el.tagName === 'FOOTER') return false;
  if (role === 'banner' || role === 'navigation' || role === 'contentinfo') return false;
  if (OV_CHROME.test(ovClass(el)) || OV_CHROME.test(el.id || '')) return false;
  const z = parseInt(st.zIndex, 10);
  const highZ = Number.isFinite(z) && z >= 100;
  const nomeado = OV_NAMED.test(ovClass(el)) || OV_NAMED.test(el.id || '');
  if (st.position === 'fixed' || st.position === 'sticky') {
    return highZ || nomeado;
  }
  // Caixas ancoradas (position: absolute) tambem sao pop-ups quando oferecem
  // um botao de fechar proprio. O z-index costuma ser baixo nesses casos.
  if (st.position === 'absolute') {
    return highZ || nomeado || ovHasCloseControl(el);
  }
  return false;
}
"""

# Comportamento intrusivo: entre os que "podem ser", quais realmente atrapalham.
_JS_IS_INTRUSIVE = r"""
const OV_CONSENT = /(cookie|consent|lgpd|privacidade|privacy|aceit|concord|newsletter|inscrev|assine|not(i|í)fica)/i;
function ovIsIntrusive(el, st) {
  // Nao bloqueia clique: por definicao nao atrapalha a automacao.
  if (st.pointerEvents === 'none') return false;
  // Bem maior que a tela: e o corpo da pagina, nao uma sobreposicao. Sem esta
  // trava a raiz do app seria tratada como backdrop e fechada.
  const box = el.getBoundingClientRect();
  if (box.height > window.innerHeight * 1.5 || box.width > window.innerWidth * 1.5) return false;
  // Container de portal vazio (comum em SPA) nao e pop-up: sem texto e sem
  // nada clicavel dentro, nao ha o que dispensar.
  const temTexto = (el.innerText || '').trim().length > 0;
  const temControle = !!el.querySelector('button, a, input, textarea, select, [role=button]');
  if (!temTexto && !temControle) return false;

  const role = (el.getAttribute('role') || '').toLowerCase();
  if (role === 'dialog' || role === 'alertdialog' || el.getAttribute('aria-modal') === 'true') return true;
  if (el.tagName === 'DIALOG' && el.hasAttribute('open')) return true;
  const r = el.getBoundingClientRect();
  const viewport = window.innerWidth * window.innerHeight;
  if (viewport <= 0) return false;
  const area = Math.max(0, Math.min(r.right, window.innerWidth) - Math.max(r.left, 0))
             * Math.max(0, Math.min(r.bottom, window.innerHeight) - Math.max(r.top, 0));
  // Cobre meia tela: modal ou backdrop.
  if (area >= viewport * 0.5) return true;
  // Fala de cookies/consentimento/newsletter: barra de aviso.
  const txt = (el.innerText || '').slice(0, 400);
  if (txt && OV_CONSENT.test(txt) && area >= viewport * 0.02) return true;
  // Caixa fixa no meio da tela.
  const cx = window.innerWidth / 2, cy = window.innerHeight / 2;
  if (r.left <= cx && r.right >= cx && r.top <= cy && r.bottom >= cy
      && area >= viewport * 0.15) return true;
  // Caixa com botao de fechar proprio e que pede algum dado: e um pop-up,
  // mesmo pequeno. E o caso do "informe seu CEP" que rouba o campo do produto.
  if (area >= viewport * 0.02 && ovHasCloseControl(el)
      && el.querySelector('input, textarea, select')) return true;
  return false;
}
"""

_JS_COLLECT = _JS_HELPERS + _JS_LOOKS_LIKE + _JS_IS_INTRUSIVE + r"""
const MAX = arguments[0];
const candidates = new Set();
const selector = '[role=dialog],[role=alertdialog],[aria-modal=true],dialog[open],'
  + '[class*=modal i],[class*=popup i],[class*=overlay i],[class*=cookie i],'
  + '[class*=consent i],[class*=lgpd i],[class*=backdrop i],[class*=lightbox i],'
  + '[id*=modal i],[id*=popup i],[id*=overlay i],[id*=cookie i],[id*=consent i]';
try {
  for (const el of document.querySelectorAll(selector)) candidates.add(el);
} catch (e) { /* :has/i em atributo nao suportado: segue com a varredura rasa */ }
// Sobreposicoes fixas quase sempre vivem nos primeiros niveis do body.
for (const el of document.querySelectorAll('body > *, body > * > *, body > * > * > *')) {
  candidates.add(el);
}
// Pop-ups fundos no DOM: chega neles pelo botao de fechar.
let vistos = 0;
for (const btn of document.querySelectorAll('[aria-label], [title], button, [role=button]')) {
  if (vistos++ > 250) break;
  const rotulos = [
    btn.getAttribute('aria-label') || '',
    btn.getAttribute('title') || '',
    (btn.innerText || '').trim(),
  ];
  if (!rotulos.some(t => OV_FECHAR.test(t))) continue;
  const r = btn.getBoundingClientRect();
  if (r.width < 1 || r.height < 1) continue;
  for (const root of ovRootsFromCloseButton(btn)) candidates.add(root);
}

const found = [];
for (const el of candidates) {
  const st = ovStyle(el);
  if (!ovVisible(el, st)) continue;
  if (!ovLooksLikeOverlay(el, st)) continue;
  if (!ovIsIntrusive(el, st)) continue;
  found.push(el);
}
// Mantem so os ancestrais: se A contem B, B faz parte do mesmo overlay.
const roots = found.filter(el => !found.some(o => o !== el && o.contains(el)));
return roots.slice(0, MAX);
"""

_JS_BLOCKER_AT = r"""
const target = arguments[0];
const r = target.getBoundingClientRect();
if (r.width === 0 && r.height === 0) return null;
const cx = Math.min(Math.max(r.left + r.width / 2, 1), window.innerWidth - 1);
const cy = Math.min(Math.max(r.top + r.height / 2, 1), window.innerHeight - 1);
const hit = document.elementFromPoint(cx, cy);
if (!hit) return null;
if (hit === target || target.contains(hit) || hit.contains(target)) return null;
return hit;
"""

_JS_OVERLAY_ROOT_OF = _JS_HELPERS + r"""
// Sobe do elemento que interceptou o clique ate a raiz da sobreposicao,
// sem nunca englobar o alvo que a automacao precisa clicar.
const hit = arguments[0];
const target = arguments[1];
let node = hit;
let root = hit;
while (node && node !== document.body && node !== document.documentElement) {
  if (target && node.contains(target)) break;
  const st = ovStyle(node);
  if (st) {
    const z = parseInt(st.zIndex, 10);
    if (st.position === 'fixed' || st.position === 'sticky' || (Number.isFinite(z) && z >= 100)) {
      root = node;
    }
  }
  node = node.parentElement;
}
return root;
"""

_JS_DESCRIBE = _JS_HELPERS + "return ovDescribe(arguments[0]);"

_JS_IS_GONE = _JS_HELPERS + r"""
const el = arguments[0];
if (!el || !el.isConnected) return true;
const st = ovStyle(el);
if (!st) return true;
return !ovVisible(el, st);
"""

_JS_NEUTRALIZE = r"""
const el = arguments[0];
if (!el || !el.isConnected) return false;
el.style.setProperty('display', 'none', 'important');
el.setAttribute('data-frete-popup-oculto', '1');
// Modais costumam travar a rolagem enquanto estao abertos.
for (const node of [document.documentElement, document.body]) {
  node.style.removeProperty('overflow');
  node.style.removeProperty('position');
}
return true;
"""

# Procura o melhor botao de dispensa dentro do overlay, tudo em uma chamada.
# Niveis: 0 = fechar de verdade, 1 = consentir/ciente, 2 = recusa suave.
_JS_FIND_DISMISS = _JS_HELPERS + r"""
const root = arguments[0];
if (!root || !root.isConnected) return null;

const TIERS = [
  ['fechar', 'close', 'dismiss', 'cerrar', '×', '✕', '✖', '⨯', 'x'],
  ['aceitar todos', 'aceitar e fechar', 'concordar e fechar', 'aceitar cookies',
   'aceitar', 'aceito', 'concordar', 'concordo', 'entendi', 'entendido',
   'permitir', 'accept all', 'accept', 'agree', 'got it', 'i understand',
   'allow', 'continuar', 'prosseguir', 'continue', 'proceed', 'ok'],
  ['agora nao', 'agora não', 'depois', 'nao, obrigado', 'não, obrigado',
   'no thanks', 'maybe later', 'talvez depois', 'recusar', 'rejeitar',
   'nao permitir', 'não permitir'],
];
// Acoes que tirariam a automacao da pagina do produto ou fariam algo indesejado.
const NEVER = /(comprar|adicionar ao carrinho|adicionar|carrinho|finalizar|checkout|entrar|login|cadastr|criar conta|assinar|inscrever|buy|add to cart|sign in|sign up|subscribe|register|calcular frete|calcular|simular|confirmar)/i;

function norm(v) { return String(v || '').replace(/\s+/g, ' ').trim().toLowerCase(); }

const sel = 'button, [role=button], a, input[type=button], input[type=submit],'
  + ' [data-dismiss], [data-testid], [class*=close], [id*=close], [aria-label]';

let best = null;
let bestScore = [99, 99];
let scanned = 0;

for (const el of root.querySelectorAll(sel)) {
  if (scanned++ > 60) break;
  const st = ovStyle(el);
  if (!st || st.display === 'none' || st.visibility === 'hidden') continue;
  const r = el.getBoundingClientRect();
  if (r.width < 1 || r.height < 1) continue;
  if (el.disabled) continue;

  const label = norm(el.innerText) || norm(el.getAttribute('aria-label'));
  if (label && NEVER.test(label)) continue;

  // Link que navega para outro lugar levaria a automacao para fora do produto.
  if (el.tagName === 'A') {
    const href = el.getAttribute('href') || '';
    if (href && !href.startsWith('#') && !href.toLowerCase().startsWith('javascript:')) continue;
  }

  const sources = [
    norm(el.getAttribute('aria-label')),
    norm(el.getAttribute('title')),
    norm(el.getAttribute('data-testid')),
    norm(el.innerText),
    norm(ovClass(el)),
    norm(el.id),
  ];

  for (let tier = 0; tier < TIERS.length; tier++) {
    const words = TIERS[tier];
    for (let w = 0; w < words.length; w++) {
      const word = words[w];
      for (let s = 0; s < sources.length; s++) {
        const text = sources[s];
        if (!text) continue;
        // Rotulos curtos (x, ok) so valem quando sao o texto inteiro.
        const hit = word.length <= 2 ? (text === word) : text.includes(word);
        if (!hit) continue;
        const score = [tier, w + s];
        if (score[0] < bestScore[0] || (score[0] === bestScore[0] && score[1] < bestScore[1])) {
          bestScore = score;
          best = el;
        }
        break;
      }
    }
  }
}
return best;
"""

_JS_CLICK = r"""
const el = arguments[0];
if (!el || !el.isConnected) return false;
try { el.scrollIntoView({block: 'center'}); } catch (e) {}
el.click();
return true;
"""


class OverlayDismisser:
    """Detecta e fecha sobreposicoes que atrapalham a automacao."""

    def __init__(
        self,
        driver: WebDriver,
        *,
        enabled: bool = True,
        settle_seconds: float = 2.0,
        max_attempts: int = 3,
        max_overlays: int = 4,
    ) -> None:
        self.driver = driver
        self.enabled = enabled
        self.settle_seconds = max(0.0, float(settle_seconds))
        self.max_attempts = max(1, int(max_attempts))
        self.max_overlays = max(1, int(max_overlays))
        # Historico legivel do que foi fechado; o servico anexa ao resultado.
        self.events: list[str] = []

    # ---------------------------------------------------------------- publico

    def settle(self, *, wait: bool = True) -> int:
        """Espera a pagina assentar e fecha as sobreposicoes que aparecerem.

        Roda logo depois do load, quando banners de cookie e modais de
        boas-vindas costumam surgir com atraso. Aqui nunca ocultamos nada via
        script: sem um alvo concreto nao ha prova de que o elemento atrapalha.
        """
        if not self.enabled:
            return 0
        if wait and self.settle_seconds:
            time.sleep(self.settle_seconds)

        closed = 0
        for _ in range(self.max_attempts):
            overlays = self._collect_overlays()
            if not overlays:
                break
            progressed = False
            for overlay in overlays:
                if self._dismiss(overlay, reason="apos o carregamento", allow_hide=False):
                    closed += 1
                    progressed = True
            if not progressed:
                break
            # Fechar um modal costuma revelar o proximo (cookie atras do popup).
            time.sleep(0.4)
        return closed

    def recheck(self) -> int:
        """Nova varredura antes de uma acao importante, sem a espera inicial."""
        return self.settle(wait=False)

    def clear_path(self, target) -> bool:
        """Garante que nada esteja na frente de `target`.

        So mexe na pagina quando ha mesmo algo cobrindo o elemento. Devolve
        True se o caminho esta livre no fim — inclusive quando ja estava.
        """
        if not self.enabled or target is None:
            return True

        last_described = ""
        for attempt in range(1, self.max_attempts + 1):
            blocker = self._blocker_for(target)
            if blocker is None:
                return True

            overlay = self._overlay_root_of(blocker, target)
            last_described = self._describe(overlay)
            self._dismiss(
                overlay,
                reason=f"cobrindo o elemento (tentativa {attempt}/{self.max_attempts})",
                allow_hide=True,
            )
            time.sleep(0.35)

        if self._blocker_for(target) is None:
            return True

        message = f"Pop-up nao pode ser fechado e segue cobrindo o elemento: {last_described}"
        logger.warning(message)
        self.events.append(message)
        return False

    # ---------------------------------------------------------------- interno

    def _collect_overlays(self) -> list:
        try:
            found = self.driver.execute_script(_JS_COLLECT, self.max_overlays)
        except Exception as exc:
            logger.debug("Falha ao varrer sobreposicoes: %r", exc)
            return []
        return [el for el in (found or []) if el is not None]

    def _blocker_for(self, target):
        try:
            return self.driver.execute_script(_JS_BLOCKER_AT, target)
        except Exception as exc:
            logger.debug("Falha ao medir o que cobre o elemento: %r", exc)
            return None

    def _overlay_root_of(self, blocker, target):
        try:
            root = self.driver.execute_script(_JS_OVERLAY_ROOT_OF, blocker, target)
        except Exception:
            root = None
        return root or blocker

    def _describe(self, element) -> str:
        try:
            return self.driver.execute_script(_JS_DESCRIBE, element) or "<elemento>"
        except Exception:
            return "<elemento>"

    def _is_gone(self, element) -> bool:
        try:
            return bool(self.driver.execute_script(_JS_IS_GONE, element))
        except Exception:
            return True

    def _dismiss(self, overlay, *, reason: str, allow_hide: bool) -> bool:
        """Tenta fechar uma sobreposicao. True se ela sumiu."""
        if overlay is None or self._is_gone(overlay):
            return False

        described = self._describe(overlay)
        logger.info("Pop-up detectado (%s): %s", reason, described)

        strategies = [
            (self._click_dismiss_button, "botao de dispensa"),
            (self._press_escape, "tecla ESC"),
        ]
        if allow_hide:
            strategies.append((self._hide, "ocultado via script"))

        for strategy, label in strategies:
            try:
                acted = strategy(overlay)
            except Exception as exc:
                logger.debug("Estrategia '%s' falhou: %r", label, exc)
                continue
            if not acted:
                continue
            time.sleep(0.45)
            if self._is_gone(overlay):
                message = f"Pop-up fechado ({label}): {described}"
                logger.info(message)
                self.events.append(message)
                return True

        return False

    def _click_dismiss_button(self, overlay) -> bool:
        try:
            button = self.driver.execute_script(_JS_FIND_DISMISS, overlay)
        except Exception:
            return False
        if button is None:
            return False
        try:
            return bool(self.driver.execute_script(_JS_CLICK, button))
        except Exception:
            try:
                button.click()
                return True
            except Exception:
                return False

    def _press_escape(self, overlay) -> bool:
        try:
            self.driver.find_element(By.TAG_NAME, "body").send_keys(Keys.ESCAPE)
            return True
        except Exception:
            return False

    def _hide(self, overlay) -> bool:
        try:
            done = bool(self.driver.execute_script(_JS_NEUTRALIZE, overlay))
        except Exception:
            return False
        if done:
            logger.warning(
                "Pop-up sem botao de fechar reconhecivel; ocultado via script: %s",
                self._describe(overlay),
            )
        return done
