/* BUSCA FRETE — comportamentos da interface.
   Tudo aqui é progressivo: sem JS as telas continuam funcionando por formulário. */

const SECONDS_PER_QUERY = 30;

function humanizeSeconds(seconds) {
  if (!seconds || seconds <= 0) return "";
  const total = Math.round(seconds);
  if (total < 60) return "menos de 1 min";
  const minutes = Math.floor(total / 60);
  if (minutes < 60) return `${minutes} min`;
  const hours = Math.floor(minutes / 60);
  const rest = minutes % 60;
  return rest === 0 ? `${hours} h` : `${hours} h ${rest} min`;
}

function initFileDrops() {
  const drops = document.querySelectorAll(".file-drop");
  for (const drop of drops) {
    const input = drop.querySelector("input[type='file']");
    const nameEl = drop.querySelector("[data-file-name]");
    if (!input || !nameEl) continue;

    const update = () => {
      const file = input.files && input.files[0];
      nameEl.textContent = file ? file.name : "Nenhum arquivo selecionado";
      drop.classList.toggle("has-file", Boolean(file));
    };

    input.addEventListener("change", update);
    drop.addEventListener("dragover", (e) => {
      e.preventDefault();
      drop.classList.add("is-dragover");
    });
    drop.addEventListener("dragleave", () => drop.classList.remove("is-dragover"));
    drop.addEventListener("drop", () => {
      drop.classList.remove("is-dragover");
      // O browser já atualiza input.files ao soltar; só atualiza UI.
      setTimeout(update, 0);
    });

    update();
  }
}

function initTabs() {
  const groups = document.querySelectorAll("[data-tabs]");
  for (const group of groups) {
    const buttons = Array.from(group.querySelectorAll("[data-tab-target]"));
    const panels = buttons
      .map((b) => document.getElementById(b.getAttribute("data-tab-target")))
      .filter(Boolean);

    for (const button of buttons) {
      button.addEventListener("click", () => {
        const targetId = button.getAttribute("data-tab-target");
        for (const other of buttons) other.classList.toggle("is-active", other === button);
        for (const panel of panels) panel.hidden = panel.id !== targetId;
      });
    }
  }
}

function countChecked(picker) {
  return picker.querySelectorAll("[data-picker-input]:checked").length;
}

function initPickers() {
  const pickers = document.querySelectorAll("[data-picker]");
  for (const picker of pickers) {
    const search = picker.querySelector("[data-picker-search]");
    const items = Array.from(picker.querySelectorAll("[data-picker-item]"));
    const inputs = Array.from(picker.querySelectorAll("[data-picker-input]"));
    const countEl = picker.querySelector("[data-picker-count]");
    const filteredEl = picker.querySelector("[data-picker-filtered]");
    const btnSelectAll = picker.querySelector("[data-picker-select-all]");
    const btnClear = picker.querySelector("[data-picker-clear]");

    const notifyChange = () => {
      picker.dispatchEvent(new CustomEvent("picker:change", { bubbles: true }));
    };

    const updateCount = () => {
      if (countEl) countEl.textContent = String(countChecked(picker));
      notifyChange();
    };

    const applyFilter = () => {
      const q = (search?.value || "").trim().toLowerCase();
      let visible = 0;
      for (const item of items) {
        const text = (item.getAttribute("data-picker-text") || "").toLowerCase();
        const show = !q || text.includes(q);
        item.hidden = !show;
        if (show) visible += 1;
      }
      if (filteredEl) {
        if (q) {
          filteredEl.hidden = false;
          filteredEl.textContent = `· ${visible} na busca`;
        } else {
          filteredEl.hidden = true;
          filteredEl.textContent = "";
        }
      }
    };

    search?.addEventListener("input", applyFilter);
    for (const input of inputs) input.addEventListener("change", updateCount);

    btnSelectAll?.addEventListener("click", () => {
      for (const item of items) {
        if (item.hidden) continue;
        const input = item.querySelector("[data-picker-input]");
        if (input) input.checked = true;
      }
      updateCount();
    });

    btnClear?.addEventListener("click", () => {
      for (const input of inputs) input.checked = false;
      updateCount();
    });

    applyFilter();
    if (countEl) countEl.textContent = String(countChecked(picker));
  }
}

function showFormError(form, message) {
  const box = form.querySelector("[data-form-error]");
  if (!box) return false;
  box.textContent = message;
  box.hidden = false;
  box.scrollIntoView({ block: "nearest", behavior: "smooth" });
  return true;
}

function clearFormError(form) {
  const box = form.querySelector("[data-form-error]");
  if (!box) return;
  box.hidden = true;
  box.textContent = "";
}

function initEstimate() {
  const form = document.querySelector("[data-estimate-form]");
  if (!form) return;

  const parallel = Math.max(1, parseInt(form.getAttribute("data-parallel") || "1", 10) || 1);
  const totalEl = form.querySelector("[data-estimate-total]");
  const timeEl = form.querySelector("[data-estimate-time]");
  const submit = form.querySelector("[data-estimate-submit]");
  const productsPicker = form.querySelector("[data-picker-role='products']");
  const cepsPicker = form.querySelector("[data-picker-role='ceps']");
  if (!productsPicker || !cepsPicker) return;

  const idleSubtitle = timeEl ? timeEl.textContent : "";

  const update = () => {
    const products = countChecked(productsPicker);
    const ceps = countChecked(cepsPicker);
    const total = products * ceps;

    if (!total) {
      if (totalEl) totalEl.textContent = "Selecione produtos e CEPs para começar";
      if (timeEl) timeEl.textContent = idleSubtitle;
      if (submit) submit.textContent = "Executar consulta";
      return;
    }

    const label = total === 1 ? "consulta" : "consultas";
    if (totalEl) {
      totalEl.textContent = `${products} ${products === 1 ? "produto" : "produtos"} × ${ceps} ${
        ceps === 1 ? "CEP" : "CEPs"
      } = ${total} ${label}`;
    }
    if (timeEl) {
      const eta = humanizeSeconds((total / parallel) * SECONDS_PER_QUERY);
      timeEl.textContent = `Tempo estimado: cerca de ${eta} com ${parallel} ${
        parallel === 1 ? "execução" : "execuções"
      } em paralelo. Pode fechar a aba — o lote continua rodando no servidor.`;
    }
    if (submit) submit.textContent = `Executar ${total} ${label}`;
    clearFormError(form);
  };

  form.addEventListener("picker:change", update);
  form.addEventListener("submit", (e) => {
    const products = countChecked(productsPicker);
    const ceps = countChecked(cepsPicker);
    if (!products || !ceps) {
      e.preventDefault();
      e.stopImmediatePropagation();
      showFormError(form, "Selecione ao menos um produto e um CEP para executar.");
      return;
    }
    const total = products * ceps;
    if (total > 500 && !window.confirm(`Isso vai disparar ${total} consultas. Confirma?`)) {
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  });

  update();
}

function initScheduleValidation() {
  const form = document.querySelector("form[action$='/schedules']");
  if (!form) return;
  form.addEventListener("submit", (e) => {
    const mode = form.querySelector("select[name='schedule_mode']");
    if (!mode || mode.value !== "selected") return;
    const hasProducts = form.querySelectorAll("input[name='schedule_product_ids']:checked").length > 0;
    const hasCeps = form.querySelectorAll("input[name='schedule_ceps']:checked").length > 0;
    if (hasProducts && hasCeps) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    if (!showFormError(form, "No modo personalizado, escolha ao menos um produto e um CEP.")) {
      window.alert("No modo personalizado, escolha ao menos um produto e um CEP.");
    }
  });
}

function initConfirm() {
  for (const form of document.querySelectorAll("form[data-confirm]")) {
    form.addEventListener("submit", (e) => {
      const message = form.getAttribute("data-confirm") || "Confirma?";
      if (!window.confirm(message)) {
        e.preventDefault();
        e.stopImmediatePropagation();
      }
    });
  }
}

function initLoadingButtons() {
  const loading = new Set();

  for (const form of document.querySelectorAll("form")) {
    form.addEventListener("submit", () => {
      // Roda depois dos validadores: se algum cancelou o envio, não chega aqui.
      const button = form.querySelector("button[data-loading-label]");
      if (!button || button.disabled) return;
      button.dataset.originalLabel = button.textContent;
      button.textContent = button.getAttribute("data-loading-label") || "Enviando…";
      button.disabled = true;
      button.classList.add("is-loading");
      loading.add(button);
    });
  }

  // Se o navegador voltar para esta página pelo histórico, os botões voltam ao normal.
  window.addEventListener("pageshow", () => {
    for (const button of loading) {
      if (button.dataset.originalLabel) button.textContent = button.dataset.originalLabel;
      button.disabled = false;
      button.classList.remove("is-loading");
    }
    loading.clear();
  });
}

function initNotices() {
  const notices = document.querySelectorAll("[data-notice]");
  if (!notices.length) return;

  // Tira a mensagem da URL para ela não reaparecer a cada recarga.
  const url = new URL(window.location.href);
  let changed = false;
  for (const key of ["db_notice", "schedule_notice", "kind"]) {
    if (url.searchParams.has(key)) {
      url.searchParams.delete(key);
      changed = true;
    }
  }
  if (changed) window.history.replaceState({}, "", url.toString());

  for (const notice of notices) {
    const close = document.createElement("button");
    close.type = "button";
    close.className = "notice-close";
    close.setAttribute("aria-label", "Fechar aviso");
    close.textContent = "×";
    close.addEventListener("click", () => notice.remove());
    notice.appendChild(close);
  }
}

function initListSearch() {
  for (const input of document.querySelectorAll("[data-list-search]")) {
    const selector = input.getAttribute("data-list-search");
    const rows = Array.from(document.querySelectorAll(selector));
    if (!rows.length) continue;
    const empty = document.querySelector("[data-list-empty]");

    input.addEventListener("input", () => {
      const q = input.value.trim().toLowerCase();
      let visible = 0;
      for (const row of rows) {
        const text = (row.getAttribute("data-search") || row.textContent || "").toLowerCase();
        const show = !q || text.includes(q);
        row.hidden = !show;
        if (show) visible += 1;
      }
      if (empty) empty.hidden = visible > 0;
    });
  }
}

/* ---- Tela de lote: filtros e atualização ao vivo ---- */

function matchesFilter(row, filter) {
  if (filter === "all") return true;
  const group = row.getAttribute("data-group");
  const freight = row.getAttribute("data-freight");
  if (filter === "error") return group === "error" || group === "canceled";
  if (filter === "pending") return group === "queued" || group === "running";
  if (filter === "free") return freight === "FREE";
  if (filter === "paid") return freight === "PAID";
  return true;
}

function initBatchTable() {
  const table = document.querySelector("[data-batch-table]");
  if (!table) return null;

  const filters = document.querySelector("[data-batch-filters]");
  const search = document.querySelector("[data-batch-search]");
  const empty = document.querySelector("[data-batch-empty]");
  const visibleEl = document.querySelector("[data-batch-visible]");
  let activeFilter = "all";

  const apply = () => {
    const q = (search?.value || "").trim().toLowerCase();
    let visible = 0;
    for (const row of table.querySelectorAll("[data-row]")) {
      const text = row.getAttribute("data-search") || "";
      const show = matchesFilter(row, activeFilter) && (!q || text.includes(q));
      row.hidden = !show;
      if (show) visible += 1;
    }
    if (empty) empty.hidden = visible > 0;
    if (visibleEl) visibleEl.textContent = String(visible);
  };

  if (filters) {
    for (const button of filters.querySelectorAll("[data-filter]")) {
      button.addEventListener("click", () => {
        activeFilter = button.getAttribute("data-filter") || "all";
        for (const other of filters.querySelectorAll("[data-filter]")) {
          other.classList.toggle("is-active", other === button);
        }
        apply();
      });
    }
  }

  for (const shortcut of document.querySelectorAll("[data-filter-shortcut]")) {
    shortcut.addEventListener("click", (e) => {
      e.preventDefault();
      const target = shortcut.getAttribute("data-filter-shortcut");
      const button = filters?.querySelector(`[data-filter="${target}"]`);
      if (button) button.click();
      table.scrollIntoView({ behavior: "smooth", block: "start" });
    });
  }

  search?.addEventListener("input", apply);
  apply();
  return apply;
}

function applyRow(rowEl, row) {
  rowEl.setAttribute("data-group", row.status_group);
  rowEl.setAttribute("data-freight", row.freight_kind);
  rowEl.setAttribute("data-search", row.search || "");
  rowEl.className = `tr tr-batch tr-${row.status_group}`;

  const badge = rowEl.querySelector(".badge");
  if (badge) {
    badge.className = `badge badge-${row.status_group}`;
    badge.textContent = row.status_label;
  }

  const productCell = rowEl.querySelector("[data-label='Produto']");
  if (productCell) {
    let hint = productCell.querySelector(".row-hint");
    if (row.status_hint) {
      if (!hint) {
        hint = document.createElement("div");
        hint.className = "row-hint";
        productCell.appendChild(hint);
      }
      hint.textContent = row.status_hint;
    } else if (hint) {
      hint.remove();
    }
  }

  const freightCell = rowEl.querySelector("[data-label='Frete'] .freight");
  if (freightCell) {
    freightCell.className = `freight freight-${String(row.freight_kind).toLowerCase()}`;
    freightCell.textContent = row.freight_text;
  }

  const timeCell = rowEl.querySelector("[data-label='Prazo']");
  if (timeCell) timeCell.textContent = row.delivery_time || "—";

  const modeCell = rowEl.querySelector("[data-label='Modalidade']");
  if (modeCell) modeCell.textContent = row.delivery_mode || "—";
}

function setText(selector, value) {
  const el = document.querySelector(selector);
  if (el) el.textContent = value;
}

function applySummary(summary) {
  setText("[data-batch-percent]", `${summary.percent}%`);
  setText("[data-batch-done]", String(summary.done));
  setText("[data-batch-success]", String(summary.success_count));
  setText("[data-batch-warning]", String(summary.warning_count));
  setText("[data-batch-error]", String(summary.error_count));
  setText("[data-batch-running]", String(summary.running_count));
  setText("[data-batch-queued]", String(summary.queued_count));
  setText("[data-batch-free]", String(summary.free_count));
  setText("[data-batch-paid]", String(summary.paid_count));
  setText("[data-batch-unknown]", String(summary.unknown_count));
  setText("[data-batch-error-count]", String(summary.error_count));
  const withFreight = summary.free_count + summary.paid_count;
  setText("[data-batch-free-share]", withFreight ? `${summary.free_share}% dos fretes retornados` : "");
  setText("[data-batch-paid-average]", summary.paid_average_text ? `média ${summary.paid_average_text}` : "");

  const total = summary.total || 1;
  const widths = {
    ok: (summary.success_count * 100) / total,
    warning: (summary.warning_count * 100) / total,
    error: ((summary.error_count + summary.canceled_count) * 100) / total,
    running: (summary.running_count * 100) / total,
  };
  for (const [key, width] of Object.entries(widths)) {
    const seg = document.querySelector(`[data-seg="${key}"]`);
    if (seg) seg.style.width = `${width}%`;
  }
  const bar = document.querySelector(".progress-large");
  if (bar) bar.setAttribute("aria-valuenow", String(summary.percent));

  const status = document.querySelector("[data-batch-status]");
  if (status) {
    status.className = `badge badge-${String(summary.status).toLowerCase()}`;
    status.textContent = summary.status_label;
  }

  const eta = document.querySelector("[data-batch-eta]");
  if (eta) {
    eta.innerHTML = "";
    if (summary.running && summary.eta_text) {
      eta.append("Tempo restante estimado: ");
      const strong = document.createElement("strong");
      strong.textContent = `~${summary.eta_text}`;
      eta.appendChild(strong);
    }
  }

  const counts = {
    all: summary.total,
    free: summary.free_count,
    paid: summary.paid_count,
    error: summary.error_count,
    pending: summary.pending,
  };
  for (const [key, value] of Object.entries(counts)) {
    const el = document.querySelector(`[data-count="${key}"]`);
    if (el) el.textContent = String(value);
  }

  const errorTile = document.querySelector("[data-batch-error-tile]");
  if (errorTile) errorTile.classList.toggle("stat-tile-error", summary.error_count > 0);

  for (const el of document.querySelectorAll("[data-batch-live-pill], [data-batch-cancel]")) {
    el.hidden = !summary.running;
  }
}

function initBatchLive(reapplyFilters) {
  const root = document.querySelector("[data-batch-live]");
  if (!root) return;
  const url = root.getAttribute("data-batch-url");
  if (!url) return;

  let stopped = false;
  let failures = 0;

  const tick = async () => {
    if (stopped) return;
    try {
      const response = await fetch(url, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const data = await response.json();
      failures = 0;

      let missing = false;
      for (const row of data.rows || []) {
        const rowEl = document.querySelector(`[data-row-id="${row.id}"]`);
        if (rowEl) applyRow(rowEl, row);
        else missing = true;
      }
      if (missing) {
        // Chegaram consultas que a página não conhece: recarrega uma vez.
        stopped = true;
        window.location.reload();
        return;
      }

      applySummary(data.summary);
      if (reapplyFilters) reapplyFilters();

      if (!data.summary.running) {
        stopped = true;
        return;
      }
    } catch (err) {
      failures += 1;
      if (failures >= 5) {
        stopped = true;
        const pill = document.querySelector("[data-batch-live-pill]");
        if (pill) {
          pill.classList.add("live-pill-offline");
          pill.textContent = "Sem conexão com o servidor — recarregue a página";
        }
        return;
      }
    }
    setTimeout(tick, 2500);
  };

  setTimeout(tick, 2500);
}

function initRunLive() {
  const root = document.querySelector("[data-run-live]");
  if (!root) return;
  const url = root.getAttribute("data-run-url");
  if (!url) return;

  let failures = 0;
  const pending = new Set(["QUEUED", "RUNNING", "CANCEL_REQUESTED"]);

  const tick = async () => {
    try {
      const response = await fetch(url, { headers: { Accept: "application/json" } });
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const job = await response.json();
      failures = 0;
      if (!pending.has(String(job.status || "").toUpperCase())) {
        window.location.reload();
        return;
      }
    } catch (err) {
      failures += 1;
      if (failures >= 5) return;
    }
    setTimeout(tick, 2500);
  };

  setTimeout(tick, 2500);
}

function initThemeToggle() {
  const btn = document.querySelector("[data-theme-toggle]");
  if (!btn) return;

  btn.addEventListener("click", () => {
    const current = document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
    const next = current === "dark" ? "light" : "dark";
    document.documentElement.setAttribute("data-theme", next);
    localStorage.setItem("theme", next);
  });
}

document.addEventListener("DOMContentLoaded", () => {
  initFileDrops();
  initTabs();
  initPickers();
  initEstimate();
  initScheduleValidation();
  initConfirm();
  initLoadingButtons();
  initNotices();
  initListSearch();
  const reapplyFilters = initBatchTable();
  initBatchLive(reapplyFilters);
  initRunLive();
  initThemeToggle();
});
