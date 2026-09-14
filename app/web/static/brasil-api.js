document.querySelectorAll('[data-brasil-cep]').forEach((button) => {
  const input = button.closest('form').querySelector('input[name="cep"]');
  const output = button.nextElementSibling;
  let generation = 0;
  input.addEventListener('input', () => { generation++; output.textContent = ''; });
  button.addEventListener('click', async () => {
    const current = ++generation;
    const value = input.value.trim();
    if (!/^[0-9]{5}-?[0-9]{3}$/.test(value)) {
      output.textContent = 'Informe um CEP com 8 dígitos.';
      return;
    }
    button.disabled = true;
    output.textContent = 'Consultando endereço…';
    try {
      const url = new URL(button.dataset.brasilCep, window.location.origin);
      url.searchParams.set('valor', value);
      const response = await fetch(url, {signal: AbortSignal.timeout(15000)});
      const result = await response.json();
      if (!response.ok) throw new Error(result.error || 'Falha na consulta.');
      if (current === generation) {
        output.textContent = [result.data.street, result.data.neighborhood,
          result.data.city, result.data.state].filter(Boolean).join(' · ');
      }
    } catch (error) {
      if (current === generation) output.textContent = error.message === 'Failed to fetch'
        ? 'Não foi possível consultar o endereço. Tente novamente.' : error.message;
    } finally { button.disabled = false; }
  });
});
