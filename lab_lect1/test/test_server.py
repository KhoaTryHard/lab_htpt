"""Registration API compatibility and static assets."""

from uuid import UUID

from fastapi.testclient import TestClient

from latency_lab.server import MAX_BODY_BYTES, create_app


def test_registration_and_normalized_duplicate(client, valid) -> None:
    assert client.get("/api/health").json() == {"ok": True, "registrations": 1}
    result = client.post("/api/register", json=valid)
    assert result.status_code == 201
    assert result.json()["ok"] is True
    assert result.json()["message"] == "Đăng ký thành công."
    assert UUID(result.json()["registrationId"]).version == 4
    duplicate = client.post("/api/register", json={**valid, "email": "  A@EXAMPLE.TEST  "})
    assert duplicate.status_code == 409
    assert duplicate.json() == {
        "ok": False,
        "code": "EMAIL_EXISTS",
        "errors": {"email": "Email này đã được đăng ký."},
    }
    assert client.get("/api/health").json()["registrations"] == 2


def test_server_independently_rejects_bad_input(client, valid) -> None:
    result = client.post("/api/register", json={**valid, "email": "broken"})
    assert result.status_code == 422
    assert result.json() == {
        "ok": False,
        "code": "VALIDATION_ERROR",
        "errors": {"email": "Email không đúng định dạng."},
    }
    assert client.get("/api/health").json()["registrations"] == 1


def test_reset_is_opt_in(client, valid) -> None:
    client.post("/api/register", json=valid)
    assert client.post("/api/test/reset").json() == {"ok": True}
    assert client.get("/api/health").json()["registrations"] == 1
    with TestClient(create_app()) as production:
        assert production.post("/api/test/reset").status_code == 404


def test_assets_and_missing_routes(client) -> None:
    assert 'id="registration-form"' in client.get("/").text
    assert "validateRegistration" in client.get("/app.js").text
    assert client.get("/styles.css").status_code == 200
    module = client.get("/shared/validation.mjs")
    assert module.status_code == 200
    assert "javascript" in module.headers["content-type"]
    assert client.get("/api/missing").json() == {"ok": False, "code": "NOT_FOUND"}
    assert client.get("/missing.txt").status_code == 404


def test_bad_json_and_body_limit(client) -> None:
    assert client.post("/api/register", content=b"{").status_code == 400
    assert client.post("/api/register", content=b"x" * (MAX_BODY_BYTES + 1)).status_code == 413
    null_result = client.post("/api/register", content=b"null")
    assert null_result.status_code == 422
    assert len(null_result.json()["errors"]) == 4
