import os
import json
import boto3
from boto3.dynamodb.conditions import Key

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
        return event.get("queryStringParameters") or {}
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
    try:
        data = _extract_payload(event)
    except (ValueError, TypeError, AttributeError):
        return _response(event, 400, {"error": "El cuerpo de la solicitud no es un JSON valido"})
    trip_id = data.get("tripId")
    if not trip_id:
        return _response(event, 400, {"error": "Falta tripId"})

    result = table.query(KeyConditionExpression=Key("tripId").eq(trip_id))
    return _response(event, 200, {"itineraries": result.get("Items", [])})
