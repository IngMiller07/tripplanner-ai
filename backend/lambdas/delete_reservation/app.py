import os
import json
from datetime import datetime, timezone
import boto3
from botocore.exceptions import ClientError

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
        payload = json.loads(body) if isinstance(body, str) else (body or {})
        if event.get("pathParameters"):
            payload.update(event["pathParameters"])
        return payload
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
    """Elimina una reserva (borrado real en DynamoDB). Requiere tripId y reservationId."""
    try:
        data = _extract_payload(event)
    except (ValueError, TypeError, AttributeError):
        return _response(event, 400, {"error": "El cuerpo de la solicitud no es un JSON valido"})
    trip_id = data.get("tripId")
    reservation_id = data.get("reservationId")
    if not trip_id or not reservation_id:
        return _response(event, 400, {"error": "Faltan tripId o reservationId"})

    try:
        table.delete_item(
            Key={"tripId": str(trip_id), "reservationId": str(reservation_id)},
            ConditionExpression="attribute_exists(tripId)",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return _response(event, 404, {"error": "La reserva no existe o ya fue eliminada"})
        raise

    return _response(event, 200, {"deleted": True, "tripId": str(trip_id), "reservationId": str(reservation_id)})
