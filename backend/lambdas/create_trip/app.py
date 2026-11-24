import os
import json
import uuid
from datetime import datetime, timezone
import boto3

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["TRIPS_TABLE"])

from decimal import Decimal


def _dec_default(o):
    """DynamoDB devuelve numeros como Decimal; los convertimos a int/float."""
    if isinstance(o, Decimal):
        return int(o) if o == o.to_integral_value() else float(o)
    raise TypeError(f"No serializable: {type(o)}")


def _clean(body):
    return json.loads(json.dumps(body, default=_dec_default))


def _to_decimal(payload):
    """boto3/DynamoDB no acepta float; los pasamos a Decimal antes de guardar."""
    return json.loads(json.dumps(payload), parse_float=Decimal)


def _extract_payload(event: dict) -> dict:
    """
    Soporta dos formas de invocacion:
    1) Via API Gateway (Canal 1, desde el frontend): el payload viene en
       event['body'] como JSON string.
    2) Invocacion directa (Canal 2, desde una Tool del OrchestratorAgent via
       boto3 lambda.invoke): el 'event' YA ES el payload (un dict plano).
    """
    if isinstance(event, dict) and "httpMethod" in event:
        body = event.get("body") or "{}"
        payload = json.loads(body) if isinstance(body, str) else (body or {})
        if event.get("pathParameters"):
            payload.update(event["pathParameters"])
        if event.get("queryStringParameters"):
            payload.update(event["queryStringParameters"])
        return payload
    return event or {}


def _response(event: dict, status_code: int, body: dict) -> dict:
    """Si vino de API Gateway, respeta el formato de proxy integration
    (con CORS). Si vino de invocacion directa (Tool), devuelve el dict tal cual."""
    if isinstance(event, dict) and "httpMethod" in event:
        return {
            "statusCode": status_code,
            "headers": {
                "Access-Control-Allow-Origin": "*",
                "Content-Type": "application/json",
            },
            "body": json.dumps(body, default=str),
        }
    return body

_extract_payload_raw = _extract_payload
_response_raw = _response


def _extract_payload(event):
    return _to_decimal(_extract_payload_raw(event))


def _response(event, status_code, body):
    return _response_raw(event, status_code, _clean(body))


def lambda_handler(event, context):
    try:
        data = _extract_payload(event)
    except (ValueError, TypeError, AttributeError):
        return _response(event, 400, {"error": "El cuerpo de la solicitud no es un JSON valido"})

    required = ["userId", "origin", "destination", "startDate", "endDate"]
    missing = [f for f in required if not data.get(f)]
    if missing:
        return _response(event, 400, {"error": f"Faltan campos obligatorios: {', '.join(missing)}"})

    try:
        travelers = int(data.get("travelers", 1))
    except (TypeError, ValueError):
        travelers = 0
    if travelers < 1:
        return _response(event, 400, {"error": "travelers debe ser un entero mayor o igual a 1"})

    budget = data.get("budget")
    if budget is not None:
        try:
            budget = Decimal(str(budget).replace(",", "").strip())
        except Exception:
            return _response(event, 400, {"error": "budget debe ser un numero"})
        if budget < 0:
            return _response(event, 400, {"error": "budget no puede ser negativo"})

    now = datetime.now(timezone.utc).isoformat()
    trip = {
        "tripId": str(uuid.uuid4()),
        "userId": data["userId"],
        "origin": data["origin"],
        "destination": data["destination"],
        "startDate": data["startDate"],
        "endDate": data["endDate"],
        "travelers": travelers,
        "budget": budget,
        "status": "PLANEANDO",
        "createdAt": now,
        "updatedAt": now,
    }

    table.put_item(Item=trip)
    return _response(event, 201, trip)
