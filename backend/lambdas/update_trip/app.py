import os
import json
from datetime import datetime, timezone
import boto3
from botocore.exceptions import ClientError

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

TRIP_STATUSES = ["PLANEANDO", "RESERVADO", "COMPLETADO", "CANCELADO"]
UPDATABLE_FIELDS = ["origin", "destination", "startDate", "endDate", "travelers", "budget", "status"]


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
    try:
        data = _extract_payload(event)
    except (ValueError, TypeError, AttributeError):
        return _response(event, 400, {"error": "El cuerpo de la solicitud no es un JSON valido"})
    trip_id = data.get("tripId")
    if not trip_id:
        return _response(event, 400, {"error": "Falta tripId"})

    updates = {k: v for k, v in data.items() if k in UPDATABLE_FIELDS and v is not None}
    if not updates:
        return _response(event, 400, {"error": "No hay campos para actualizar"})

    if "status" in updates:
        updates["status"] = str(updates["status"]).upper()
        if updates["status"] not in TRIP_STATUSES:
            return _response(event, 400, {"error": "status debe ser uno de: " + ", ".join(TRIP_STATUSES)})
    if "travelers" in updates:
        try:
            updates["travelers"] = int(updates["travelers"])
        except (TypeError, ValueError):
            updates["travelers"] = 0
        if updates["travelers"] < 1:
            return _response(event, 400, {"error": "travelers debe ser un entero mayor o igual a 1"})

    updates["updatedAt"] = datetime.now(timezone.utc).isoformat()

    expr_names = {f"#{k}": k for k in updates}
    expr_values = {f":{k}": v for k, v in updates.items()}
    update_expr = "SET " + ", ".join(f"#{k} = :{k}" for k in updates)

    try:
        result = table.update_item(
            Key={"tripId": trip_id},
            UpdateExpression=update_expr,
            ConditionExpression="attribute_exists(tripId)",
            ExpressionAttributeNames=expr_names,
            ExpressionAttributeValues=expr_values,
            ReturnValues="ALL_NEW",
        )
    except ClientError as exc:
        if exc.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return _response(event, 404, {"error": "El viaje no existe"})
        raise

    return _response(event, 200, result.get("Attributes", {}))
