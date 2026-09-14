from flask import Blueprint, jsonify, render_template, request
from app.services.brasil_api import BrasilAPI, BrasilAPIError, UFS


def create_blueprint():
    bp = Blueprint('brasil_api', __name__)
    client = BrasilAPI()

    def query(resource, value):
        params = {key: request.args[key] for key in (
            'latitude', 'longitude', 'dataInicial', 'dataFinal', 'incluirFeriadosNacionais'
        ) if key in request.args}
        return client.lookup(resource, value, **params)

    @bp.get('/api/brasil/<resource>')
    def lookup(resource):
        try:
            return jsonify(data=query(resource, request.args.get('valor', '')))
        except BrasilAPIError as exc:
            return jsonify(error=str(exc)), exc.status

    @bp.get('/brasil-api')
    def page():
        resource = request.args.get('recurso', 'cep')
        value = request.args.get('valor', '')
        data, error, status = None, None, 200
        if 'recurso' in request.args:
            try:
                data = query(resource, value)
            except BrasilAPIError as exc:
                error, status = str(exc), exc.status
        return render_template('brasil_api.html', resource=resource, value=value,
                               data=data, error=error, ufs=UFS), status

    return bp
