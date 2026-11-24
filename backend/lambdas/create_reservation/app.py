import os
import json
import uuid
from datetime import datetime, timezone
import boto3

dynamodb = boto3.resource("dynamodb")
table = dynamodb.Table(os.environ["RESERVATIONS_TABLE"])

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


RESERVATION_TYPES = ["VUELO", "HOTEL", "ACTIVIDAD"]
RESERVATION_STATUSES = ["SIMULADA", "PENDIENTE", "CONFIRMADA"]


def lambda_handler(event, context):
    """
    type: "VUELO" | "HOTEL" | "ACTIVIDAD"
    details: dict libre con los datos de esa reserva (ej. vuelo elegido,
    hotel elegido, actividad elegida).

    IMPORTANTE: esto es una reserva SIMULADA (no se integra con una
    aerolinea/hotel real) pero SI queda persistida de verdad en DynamoDB,
    tal como lo exige el enunciado del profesor.
    """
    try:
        data = _extract_payload(event)
    except (ValueError, TypeError, AttributeError):
        return _response(event, 400, {"error": "El cuerpo de la solicitud no es un JSON valido"})
    trip_id = data.get("tripId")
    res_type = data.get("type")
    details = data.get("details", {})
    if not trip_id or not res_type:
        return _response(event, 400, {"error": "Faltan tripId o type"})

    res_type = str(res_type).upper()
    if res_type not in RESERVATION_TYPES:
        return _response(event, 400, {"error": "type debe ser uno de: " + ", ".join(RESERVATION_TYPES)})
    status = str(data.get("status", "SIMULADA")).upper()
    if status not in RESERVATION_STATUSES:
        return _response(event, 400, {"error": "status debe ser uno de: " + ", ".join(RESERVATION_STATUSES)})
    if not isinstance(details, dict):
        return _response(event, 400, {"error": "details debe ser un objeto"})

    now = datetime.now(timezone.utc).isoformat()
    reservation = {
        "tripId": trip_id,
        "reservationId": str(uuid.uuid4()),
        "type": res_type,
        "details": details,
        "status": status,
        "createdAt": now,
        "updatedAt": now,
    }
    table.put_item(Item=reservation)
    return _response(event, 201, reservation)
