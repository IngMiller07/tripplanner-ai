import os
import json
import uuid
from datetime import datetime, timezone
import boto3

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["ITINERARIES_TABLE"])

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
    if isinstance(event, dict) and "httpMethod" in event:
        body = event.get("body") or "{}"
        return json.loads(body) if isinstance(body, str) else (body or {})
    return event or {}


def _response(event: dict, status_code: int, body: dict) -> dict:
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
    """
    'days' es una lista simple, ej:
    [
      {"day": 1, "activities": ["Llegada a Cartagena", "Check-in hotel"]},
      {"day": 2, "activities": ["City tour centro historico", "Playa Bocagrande"]}
    ]
    """
    try:
        data = _extract_payload(event)
    except (ValueError, TypeError, AttributeError):
        return _response(event, 400, {"error": "El cuerpo de la solicitud no es un JSON valido"})
    trip_id = data.get("tripId")
    days = data.get("days")
    if not trip_id or not days:
        return _response(event, 400, {"error": "Faltan tripId o days"})

    if not isinstance(days, list):
        return _response(event, 400, {"error": "days debe ser una lista de dias con sus actividades"})

    now = datetime.now(timezone.utc).isoformat()
    itinerary = {
        "tripId": trip_id,
        "itineraryId": str(uuid.uuid4()),
        "days": days,
        "createdAt": now,
        "updatedAt": now,
    }
    table.put_item(Item=itinerary)
    return _response(event, 201, itinerary)
