import io
import json
import unittest
from unittest.mock import patch
from urllib.error import HTTPError, URLError
from flask import Flask
from app.services.brasil_api import BrasilAPI, BrasilAPIError
from app.web.brasil_api import create_blueprint


class BrasilAPITests(unittest.TestCase):
    def test_coordinates_validation_and_encoding(self):
        with patch('app.services.brasil_api.urlopen') as opener:
            for lat, lon in [('nan', '0'), ('0', 'inf'), ('91', '0'), ('0', '-181'), ('', '')]:
                with self.assertRaises(BrasilAPIError):
                    BrasilAPI().lookup('geolocalizacao', latitude=lat, longitude=lon)
            opener.assert_not_called()
        with patch('app.services.brasil_api.urlopen', return_value=io.BytesIO(b'{"city":"SP","country":"Brasil"}')) as opener:
            BrasilAPI().lookup('geolocalizacao', latitude='-23,55', longitude='-46.63')
            self.assertIn('latitude=-23.55&longitude=-46.63', opener.call_args.args[0].full_url)

    def test_dates_validation_before_request(self):
        with patch('app.services.brasil_api.urlopen') as opener:
            for start, end in [('', ''), ('2026-02-30', '2026-03-01'),
                               ('2026-09-08', '2026-09-01'), ('2024-01-01', '2026-01-01')]:
                with self.assertRaises(BrasilAPIError):
                    BrasilAPI().lookup('diasuteis', dataInicial=start, dataFinal=end)
            opener.assert_not_called()

    def test_business_days_empty_and_holiday_option_cache(self):
        def response(*args, **kwargs):
            return io.BytesIO(b'{"diasUteis":[]}')
        with patch('app.services.brasil_api.urlopen', side_effect=response) as opener:
            client = BrasilAPI()
            for flag in ('true', 'false', 'true'):
                self.assertEqual(client.lookup('diasuteis', dataInicial='2026-09-06', dataFinal='2026-09-06', incluirFeriadosNacionais=flag), {'diasUteis': []})
            self.assertEqual(opener.call_count, 2)
            self.assertIn('incluirFeriadosNacionais=false', opener.call_args.args[0].full_url)

    def test_business_days_rejects_out_of_range_weekend_and_duplicates(self):
        for days in [['2026-09-06'], ['2026-10-01'], ['2026-09-01', '2026-09-01'], [None]]:
            with patch('app.services.brasil_api.urlopen', return_value=io.BytesIO(json.dumps({'diasUteis': days}).encode())):
                with self.assertRaises(BrasilAPIError):
                    BrasilAPI().lookup('diasuteis', dataInicial='2026-09-01', dataFinal='2026-09-08')

    def test_new_routes_forward_parameters(self):
        app = Flask(__name__)
        app.register_blueprint(create_blueprint())
        with patch('app.services.brasil_api.urlopen', return_value=io.BytesIO(b'{"diasUteis":["2026-09-01"]}')):
            response = app.test_client().get('/api/brasil/diasuteis?dataInicial=2026-09-01&dataFinal=2026-09-01')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['data']['diasUteis'], ['2026-09-01'])
        with patch('app.services.brasil_api.urlopen', side_effect=HTTPError('url', 403, '', {}, None)):
            response = app.test_client().get('/api/brasil/geolocalizacao?latitude=0&longitude=0')
            self.assertEqual(response.status_code, 503)
            self.assertIn('403', response.json['error'])

    def test_invalid_inputs_never_call_provider(self):
        with patch('app.services.brasil_api.urlopen') as opener:
            for resource, value in [('cep', 'abc01001000'), ('cep', '123'),
                                    ('municipios', '../SP'), ('unknown', '')]:
                with self.assertRaises(BrasilAPIError):
                    BrasilAPI().lookup(resource, value)
            opener.assert_not_called()

    def test_cep_normalized_and_cached(self):
        data = {'cep': '01001000', 'city': 'São Paulo', 'state': 'SP'}
        with patch('app.services.brasil_api.urlopen', return_value=io.BytesIO(json.dumps(data).encode())) as opener:
            client = BrasilAPI()
            self.assertEqual(client.lookup('cep', '01001-000'), data)
            self.assertEqual(client.lookup('cep', '01001000'), data)
            self.assertEqual(opener.call_count, 1)
            self.assertEqual(opener.call_args.args[0].full_url, 'https://brasilapi.com.br/api/cep/v2/01001000')

    def test_failures_are_safe_and_not_cached(self):
        for failure, status in [(HTTPError('url', 404, '', {}, None), 404),
                                (HTTPError('url', 429, '', {}, None), 503),
                                (URLError('private detail'), 502), (TimeoutError(), 502)]:
            with patch('app.services.brasil_api.urlopen', side_effect=failure) as opener:
                client = BrasilAPI()
                for _ in range(2):
                    with self.assertRaises(BrasilAPIError) as caught:
                        client.lookup('cep', '01001000')
                    self.assertEqual(caught.exception.status, status)
                    self.assertNotIn('private detail', str(caught.exception))
                self.assertEqual(opener.call_count, 2)

    def test_invalid_response_rejected(self):
        for payload in [b'not json', b'{}', b'[]']:
            with patch('app.services.brasil_api.urlopen', return_value=io.BytesIO(payload)):
                with self.assertRaises(BrasilAPIError):
                    BrasilAPI().lookup('cep', '01001000')

    def test_routes(self):
        app = Flask(__name__)
        app.register_blueprint(create_blueprint())
        client = app.test_client()
        self.assertEqual(client.get('/api/brasil/cep?valor=bad').status_code, 400)
        self.assertEqual(client.get('/api/brasil/unknown').status_code, 404)
        with patch('app.services.brasil_api.urlopen', return_value=io.BytesIO(b'[{"nome":"Distrito Federal","sigla":"DF"}]')):
            response = client.get('/api/brasil/estados')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json['data'][0]['sigla'], 'DF')
