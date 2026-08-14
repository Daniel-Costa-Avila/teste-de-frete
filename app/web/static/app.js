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

function initAccordion() {
  const groups = document.querySelectorAll("[data-accordion-group]");
  for (const group of groups) {
    const items = group.querySelectorAll("details[data-accordion-item]");
    for (const item of items) {
      item.addEventListener("toggle", () => {
        if (!item.open) return;
        for (const other of items) {
          if (other !== item) other.open = false;
        }
      });
    }
  }
}

function initPickers() {
  const pickers = document.querySelectorAll("[data-picker]");
  for (const picker of pickers) {
    const search = picker.querySelector("[data-picker-search]");
    const items = Array.from(picker.querySelectorAll("[data-picker-item]"));
    const inputs = Array.from(picker.querySelectorAll("[data-picker-input]"));
    const countEl = picker.querySelector("[data-picker-count]");
    const btnSelectAll = picker.querySelector("[data-picker-select-all]");
    const btnClear = picker.querySelector("[data-picker-clear]");

    const updateCount = () => {
      const selected = inputs.filter((i) => i.checked).length;
      if (countEl) countEl.textContent = String(selected);
    };

    const applyFilter = () => {
      const q = (search?.value || "").trim().toLowerCase();
      for (const item of items) {
        const text = (item.getAttribute("data-picker-text") || "").toLowerCase();
        const visible = !q || text.includes(q);
        item.style.display = visible ? "" : "none";
      }
    };

    search?.addEventListener("input", applyFilter);
    for (const input of inputs) {
      input.addEventListener("change", updateCount);
    }

    btnSelectAll?.addEventListener("click", () => {
      for (const item of items) {
        if (item.style.display === "none") continue;
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
    updateCount();
  }

  const dbForm = document.querySelector("form[action*='run-products-db-selected']");
  if (dbForm) {
    dbForm.addEventListener("submit", (e) => {
      const hasProducts = dbForm.querySelectorAll("input[name='product_ids']:checked").length > 0;
      const hasCeps = dbForm.querySelectorAll("input[name='ceps']:checked").length > 0;
      if (!hasProducts || !hasCeps) {
        e.preventDefault();
        alert("Selecione ao menos 1 produto e 1 CEP para executar.");
      }
    });
  }

  const scheduleForm = document.querySelector("form[action$='/schedules']");
  if (scheduleForm) {
    scheduleForm.addEventListener("submit", (e) => {
      const mode = scheduleForm.querySelector("select[name='schedule_mode']");
      if (!mode || mode.value !== "selected") return;
      const hasProducts = scheduleForm.querySelectorAll("input[name='schedule_product_ids']:checked").length > 0;
      const hasCeps = scheduleForm.querySelectorAll("input[name='schedule_ceps']:checked").length > 0;
      if (!hasProducts || !hasCeps) {
        e.preventDefault();
        alert("No modo de selecao personalizada, escolha ao menos 1 produto e 1 CEP.");
      }
    });
  }
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
  initAccordion();
  initPickers();
  initThemeToggle();
});
