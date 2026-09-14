"""Consultas pontuais à BrasilAPI, sem dependência do projeto Node local."""
import json
import math
import re
import threading
import time
from collections import OrderedDict
from datetime import date
from urllib.parse import urlencode
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

UFS = tuple('AC AL AP AM BA CE DF ES GO MA MT MS MG PA PB PR PE PI RJ RN RS RO RR SC SP SE TO'.split())


class BrasilAPIError(Exception):
    def __init__(self, message, status=502):
        super().__init__(message)
        self.status = status


class BrasilAPI:
    def __init__(self):
        self._cache = OrderedDict()
        self._lock = threading.Lock()

    def lookup(self, resource, value='', **params):
        value = value.strip()
        if resource == 'cep':
            if not re.fullmatch(r'[0-9]{5}-?[0-9]{3}', value):
                raise BrasilAPIError('Informe um CEP com 8 dígitos.', 400)
            path = 'cep/v2/' + value.replace('-', '')
        elif resource == 'estados':
            path = 'ibge/uf/v1'
        elif resource == 'municipios':
            if value.upper() not in UFS:
                raise BrasilAPIError('Selecione uma UF válida.', 400)
            path = 'ibge/municipios/v1/' + value.upper()
        elif resource == 'geolocalizacao':
            coords = {}
            for key, limit in [('latitude', 90), ('longitude', 180)]:
                try:
                    raw = str(params.get(key, '')).strip().replace(',', '.')
                    number = float(raw)
                    if not math.isfinite(number) or not -limit <= number <= limit:
                        raise ValueError()
                except (ValueError, TypeError):
                    raise BrasilAPIError(f'Informe {key} entre -{limit} e {limit}.', 400)
                coords[key] = format(number, '.12f').rstrip('0').rstrip('.') or '0'
            path = 'geolocation/v1?' + urlencode(coords)
        elif resource == 'diasuteis':
            dates = {}
            try:
                for key in ('dataInicial', 'dataFinal'):
                    raw = params.get(key, '')
                    if not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', raw):
                        raise ValueError()
                    dates[key] = date.fromisoformat(raw)
                start, end = dates.values()
                if start > end or (end - start).days > 366:
                    raise ValueError()
            except (ValueError, TypeError):
                raise BrasilAPIError('Informe datas válidas em ordem, com intervalo máximo de 366 dias.', 400)
            holidays = params.get('incluirFeriadosNacionais', 'true')
            if holidays not in ('true', 'false'):
                raise BrasilAPIError('A opção de feriados deve ser true ou false.', 400)
            path = 'diasuteis/v1?' + urlencode({**dates, 'incluirFeriadosNacionais': holidays})
        else:
            raise BrasilAPIError('Recurso não disponível.', 404)
        # A trava também evita consultas simultâneas duplicadas ao provedor.
        with self._lock:
            cached = self._cache.get(path)
            if cached and cached[0] > time.monotonic():
                self._cache.move_to_end(path)
                return cached[1]
            try:
                req = Request('https://brasilapi.com.br/api/' + path,
                              headers={'Accept': 'application/json', 'User-Agent': 'BuscaFrete/1.0'})
                with urlopen(req, timeout=10) as response:
                    data = json.load(response)
            except HTTPError as exc:
                if exc.code == 403:
                    raise BrasilAPIError('A BrasilAPI recusou a consulta (HTTP 403). Tente novamente mais tarde.', 503) from exc
                if exc.code == 404:
                    raise BrasilAPIError('Nenhum resultado encontrado.', 404) from exc
                if exc.code == 429:
                    raise BrasilAPIError('Limite de consultas atingido. Tente novamente mais tarde.', 503) from exc
                raise BrasilAPIError('BrasilAPI indisponível. Tente novamente mais tarde.') from exc
            except (URLError, OSError, ValueError) as exc:
                raise BrasilAPIError('Não foi possível consultar a BrasilAPI. Tente novamente.') from exc
            if resource == 'cep':
                valid = isinstance(data, dict) and all(isinstance(data.get(k), str) and data[k] for k in ('cep', 'city', 'state'))
            elif resource == 'geolocalizacao':
                valid = isinstance(data, dict) and all(isinstance(data.get(k), str) for k in ('city', 'country'))
            elif resource == 'diasuteis':
                valid = isinstance(data, dict) and isinstance(data.get('diasUteis'), list)
                if valid:
                    try:
                        days = [date.fromisoformat(d) for d in data['diasUteis']]
                        valid = all(start <= d <= end and d.weekday() < 5 for d in days) and days == sorted(set(days))
                    except (ValueError, TypeError):
                        valid = False
            else:
                valid = isinstance(data, list) and all(isinstance(row, dict) and isinstance(row.get('nome'), str) for row in data)
                if valid and resource == 'municipios':
                    valid = all(isinstance(row.get('codigo_ibge'), (str, int)) for row in data)
            if not valid:
                raise BrasilAPIError('A BrasilAPI retornou uma resposta inválida.')
            self._cache[path] = (time.monotonic() + 3600, data)
            self._cache.move_to_end(path)
            while len(self._cache) > 256:
                self._cache.popitem(last=False)
            return data
