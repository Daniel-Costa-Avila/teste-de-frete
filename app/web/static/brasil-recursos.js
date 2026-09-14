const municipioSearch = document.querySelector('[data-municipio-search]');
if (municipioSearch) {
  const rows = [...document.querySelectorAll('[data-municipios] li')];
  const normalize = (value) => value.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  municipioSearch.addEventListener('input', () => {
    const query = normalize(municipioSearch.value.trim());
    let visible = 0;
    rows.forEach((row) => {
      row.hidden = !normalize(row.textContent).includes(query);
      if (!row.hidden) visible++;
    });
    document.querySelector('[data-municipio-count]').textContent = `${visible} de ${rows.length} registros encontrados.`;
  });
}
