"""Pruebas de las 10 Lambdas de negocio con DynamoDB simulado (moto).

Ejecutar desde la carpeta backend:
    pip install "moto[dynamodb]" boto3 pytest
    python -m pytest tests -q
"""
import importlib.util
import json
import os
import pathlib

import boto3
import pytest
from moto import mock_aws

ROOT = pathlib.Path(__file__).resolve().parents[1] / "lambdas"
os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
os.environ["TRIPS_TABLE"] = "TripPlanner-Trips"
os.environ["ITINERARIES_TABLE"] = "TripPlanner-Itineraries"
os.environ["RESERVATIONS_TABLE"] = "TripPlanner-Reservations"


def _make_tables():
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    ddb.create_table(
        TableName="TripPlanner-Trips", BillingMode="PAY_PER_REQUEST",
        AttributeDefinitions=[{"AttributeName": "tripId", "AttributeType": "S"}, {"AttributeName": "userId", "AttributeType": "S"}],
        KeySchema=[{"AttributeName": "tripId", "KeyType": "HASH"}],
        GlobalSecondaryIndexes=[{"IndexName": "userId-index", "KeySchema": [{"AttributeName": "userId", "KeyType": "HASH"}], "Projection": {"ProjectionType": "ALL"}}],
    )
    for table, sk in (("TripPlanner-Itineraries", "itineraryId"), ("TripPlanner-Reservations", "reservationId")):
        ddb.create_table(
            TableName=table, BillingMode="PAY_PER_REQUEST",
            AttributeDefinitions=[{"AttributeName": "tripId", "AttributeType": "S"}, {"AttributeName": sk, "AttributeType": "S"}],
            KeySchema=[{"AttributeName": "tripId", "KeyType": "HASH"}, {"AttributeName": sk, "KeyType": "RANGE"}],
        )


@pytest.fixture()
def fn():
    """Crea las tablas simuladas y carga cada Lambda con su propio nombre de modulo."""
    with mock_aws():
        _make_tables()
        loaded = {}

        def load(name):
            if name not in loaded:
                spec = importlib.util.spec_from_file_location(f"lambda_{name}", ROOT / name / "app.py")
                mod = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(mod)
                loaded[name] = mod
            return loaded[name].lambda_handler
        yield load


def http(method, body=None, query=None, path_params=None):
    """Evento tipo API Gateway (proxy integration)."""
    return {
        "httpMethod": method,
        "body": json.dumps(body) if body is not None else None,
        "queryStringParameters": query,
        "pathParameters": path_params,
    }


def body_of(resp):
    return json.loads(resp["body"])


TRIP = {"userId": "ana", "origin": "Bogota", "destination": "Cartagena", "startDate": "2026-12-05", "endDate": "2026-12-09", "travelers": 3, "budget": 5000000}


# ---------------- Viajes ----------------

def test_trip_lifecycle_via_api_gateway(fn):
    r = fn("create_trip")(http("POST", TRIP), None)
    assert r["statusCode"] == 201
    assert r["headers"]["Access-Control-Allow-Origin"] == "*"
    trip = body_of(r)
    assert trip["status"] == "PLANEANDO" and trip["travelers"] == 3 and trip["budget"] == 5000000

    r = fn("get_trips")(http("GET", query={"userId": "ana"}), None)
    assert [t["tripId"] for t in body_of(r)["trips"]] == [trip["tripId"]]
    assert body_of(fn("get_trips")(http("GET", query={"userId": "otro"}), None))["trips"] == []

    r = fn("update_trip")(http("PUT", {"status": "reservado", "travelers": 4}, path_params={"tripId": trip["tripId"]}), None)
    assert r["statusCode"] == 200
    assert body_of(r)["status"] == "RESERVADO" and body_of(r)["travelers"] == 4


def test_trip_direct_invoke_returns_plain_dict(fn):
    """Asi lo invoca la Tool del Orchestrator (boto3 lambda.invoke): evento = payload."""
    trip = fn("create_trip")(dict(TRIP, budget=4999999.5), None)
    assert trip["tripId"] and trip["budget"] == 4999999.5
    assert fn("get_trips")({"userId": "ana"}, None)["trips"][0]["tripId"] == trip["tripId"]


def test_trip_validations(fn):
    create = fn("create_trip")
    assert create(http("POST", {"userId": "ana"}), None)["statusCode"] == 400
    assert "Faltan campos" in body_of(create(http("POST", {"userId": "ana"}), None))["error"]
    assert create(http("POST", dict(TRIP, travelers=0)), None)["statusCode"] == 400
    assert create(http("POST", dict(TRIP, travelers="abc")), None)["statusCode"] == 400
    assert create(http("POST", dict(TRIP, budget="mucho")), None)["statusCode"] == 400
    assert create(http("POST", dict(TRIP, budget=-5)), None)["statusCode"] == 400
    bad = {"httpMethod": "POST", "body": "{no es json"}
    assert create(bad, None)["statusCode"] == 400
    assert fn("get_trips")(http("GET", query=None), None)["statusCode"] == 400


def test_update_trip_validations_and_404(fn):
    upd = fn("update_trip")
    assert upd(http("PUT", {"status": "PLANEANDO"}), None)["statusCode"] == 400  # falta tripId
    assert upd(http("PUT", {}, path_params={"tripId": "x"}), None)["statusCode"] == 400  # nada que actualizar
    assert upd(http("PUT", {"status": "VOLANDO"}, path_params={"tripId": "x"}), None)["statusCode"] == 400
    r = upd(http("PUT", {"status": "CANCELADO"}, path_params={"tripId": "no-existe"}), None)
    assert r["statusCode"] == 404  # antes creaba un registro fantasma
    assert fn("get_trips")({"userId": "no-existe"}, None)["trips"] == []


# ---------------- Itinerarios ----------------

DAYS = [{"day": 1, "activities": ["Llegada", "Centro historico"]}, {"day": 2, "activities": ["Playa"]}]


def test_itinerary_lifecycle(fn):
    r = fn("create_itinerary")(http("POST", {"tripId": "t1", "days": DAYS}), None)
    assert r["statusCode"] == 201
    it = body_of(r)
    got = body_of(fn("get_itineraries")(http("GET", query={"tripId": "t1"}), None))["itineraries"]
    assert got[0]["itineraryId"] == it["itineraryId"] and got[0]["days"] == DAYS

    new_days = DAYS + [{"day": 3, "activities": ["Islas del Rosario"]}]
    r = fn("update_itinerary")(http("PUT", {"days": new_days}, path_params={"tripId": "t1", "itineraryId": it["itineraryId"]}), None)
    assert r["statusCode"] == 200 and len(body_of(r)["days"]) == 3


def test_itinerary_validations_and_404(fn):
    assert fn("create_itinerary")(http("POST", {"tripId": "t1"}), None)["statusCode"] == 400
    assert fn("create_itinerary")(http("POST", {"tripId": "t1", "days": "texto"}), None)["statusCode"] == 400
    assert fn("get_itineraries")(http("GET", query={}), None)["statusCode"] == 400
    upd = fn("update_itinerary")
    assert upd(http("PUT", {"days": DAYS}, path_params={"tripId": "t1"}), None)["statusCode"] == 400
    assert upd(http("PUT", {"days": "x"}, path_params={"tripId": "t1", "itineraryId": "i1"}), None)["statusCode"] == 400
    assert upd(http("PUT", {"days": DAYS}, path_params={"tripId": "t1", "itineraryId": "no-existe"}), None)["statusCode"] == 404


# ---------------- Reservas ----------------

def test_reservation_lifecycle(fn):
    details = {"airline": "Avianca", "flight": "AV9455", "price_usd": 121.5}
    r = fn("create_reservation")(http("POST", {"tripId": "t1", "type": "vuelo", "details": details}), None)
    assert r["statusCode"] == 201
    res = body_of(r)
    assert res["type"] == "VUELO" and res["status"] == "SIMULADA" and res["details"]["price_usd"] == 121.5

    listed = body_of(fn("get_reservations")(http("GET", query={"tripId": "t1"}), None))["reservations"]
    assert len(listed) == 1

    for status in ("PENDIENTE", "CONFIRMADA"):
        r = fn("update_reservation")(http("PUT", {"status": status}, path_params={"tripId": "t1", "reservationId": res["reservationId"]}), None)
        assert r["statusCode"] == 200 and body_of(r)["status"] == status


def test_reservation_direct_invoke(fn):
    res = fn("create_reservation")({"tripId": "t9", "type": "HOTEL", "details": {"name": "Hotel Caribe"}}, None)
    assert res["status"] == "SIMULADA" and res["reservationId"]


def test_reservation_validations_and_404(fn):
    create = fn("create_reservation")
    assert create(http("POST", {"tripId": "t1"}), None)["statusCode"] == 400
    assert create(http("POST", {"tripId": "t1", "type": "AVION"}), None)["statusCode"] == 400
    assert create(http("POST", {"tripId": "t1", "type": "HOTEL", "status": "PAGADA"}), None)["statusCode"] == 400
    assert create(http("POST", {"tripId": "t1", "type": "HOTEL", "details": "texto"}), None)["statusCode"] == 400
    assert fn("get_reservations")(http("GET", query={}), None)["statusCode"] == 400
    upd = fn("update_reservation")
    assert upd(http("PUT", {"status": "CONFIRMADA"}, path_params={"tripId": "t1"}), None)["statusCode"] == 400
    assert upd(http("PUT", {"status": "PAGADA"}, path_params={"tripId": "t1", "reservationId": "r1"}), None)["statusCode"] == 400
    assert upd(http("PUT", {"status": "CONFIRMADA"}, path_params={"tripId": "t1", "reservationId": "no-existe"}), None)["statusCode"] == 404


def test_reservation_delete(fn):
    """Eliminar una reserva: por API Gateway (DELETE) y por invocacion directa (Tool delete_reservation)."""
    create, get, delete = fn("create_reservation"), fn("get_reservations"), fn("delete_reservation")
    r1 = body_of(create(http("POST", {"tripId": "t1", "type": "VUELO", "details": {"airline": "LATAM"}}), None))
    r2 = create({"tripId": "t1", "type": "HOTEL", "details": {"name": "Hotel Caribe"}}, None)
    assert len(body_of(get(http("GET", query={"tripId": "t1"}), None))["reservations"]) == 2

    r = delete(http("DELETE", path_params={"tripId": "t1", "reservationId": r1["reservationId"]}), None)
    assert r["statusCode"] == 200 and body_of(r) == {"deleted": True, "tripId": "t1", "reservationId": r1["reservationId"]}
    assert r["headers"]["Access-Control-Allow-Origin"] == "*"
    left = body_of(get(http("GET", query={"tripId": "t1"}), None))["reservations"]
    assert [x["reservationId"] for x in left] == [r2["reservationId"]]               # solo se borro esa

    out = delete({"tripId": "t1", "reservationId": r2["reservationId"]}, None)        # invocacion directa (tool del agente)
    assert out["deleted"] is True
    assert body_of(get(http("GET", query={"tripId": "t1"}), None))["reservations"] == []

    # validaciones y 404 (borrar dos veces / reserva de otro viaje)
    assert delete(http("DELETE", path_params={"tripId": "t1"}), None)["statusCode"] == 400
    assert delete(http("DELETE", path_params={"tripId": "t1", "reservationId": r1["reservationId"]}), None)["statusCode"] == 404
    other = body_of(create(http("POST", {"tripId": "t2", "type": "HOTEL"}), None))
    assert delete(http("DELETE", path_params={"tripId": "t1", "reservationId": other["reservationId"]}), None)["statusCode"] == 404
    assert len(body_of(get(http("GET", query={"tripId": "t2"}), None))["reservations"]) == 1   # la de otro viaje sigue ahi
