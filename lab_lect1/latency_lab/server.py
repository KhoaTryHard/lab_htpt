"""Registration API and static frontend, served by a single Uvicorn worker."""

import json
import os
from pathlib import Path
from uuid import uuid4

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException

from latency_lab.store import RegistrationStore
from latency_lab.validation import validate_registration

ROOT = Path(__file__).resolve().parent.parent
MAX_BODY_BYTES = 16 * 1024


def create_app(
    *, store: RegistrationStore | None = None, allow_test_reset: bool = False
) -> FastAPI:
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    registrations = store if store is not None else RegistrationStore()

    @app.exception_handler(HTTPException)
    async def http_error(_request: Request, error: HTTPException) -> JSONResponse:
        code = "NOT_FOUND" if error.status_code == 404 else "HTTP_ERROR"
        return JSONResponse({"ok": False, "code": code}, status_code=error.status_code)

    @app.get("/api/health")
    async def health() -> dict:
        return {"ok": True, "registrations": registrations.size()}

    @app.post("/api/register")
    async def register(request: Request) -> JSONResponse:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_BODY_BYTES:
                return JSONResponse({"ok": False, "code": "BODY_TOO_LARGE"}, status_code=413)
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            return JSONResponse({"ok": False, "code": "INVALID_JSON"}, status_code=400)

        checked = validate_registration(payload)
        if not checked["valid"]:
            return JSONResponse(
                {"ok": False, "code": "VALIDATION_ERROR", "errors": checked["errors"]},
                status_code=422,
            )
        value = checked["value"]
        # No await between duplicate check and insert: atomic within this one-worker lab.
        if registrations.has(value["email"]):
            return JSONResponse(
                {
                    "ok": False,
                    "code": "EMAIL_EXISTS",
                    "errors": {"email": "Email này đã được đăng ký."},
                },
                status_code=409,
            )
        record = {"id": str(uuid4()), "fullName": value["fullName"], "email": value["email"]}
        registrations.add(record)
        return JSONResponse(
            {"ok": True, "registrationId": record["id"], "message": "Đăng ký thành công."},
            status_code=201,
        )

    if allow_test_reset:

        @app.post("/api/test/reset")
        async def reset() -> dict:
            registrations.reset()
            return {"ok": True}

    @app.api_route("/api/{unknown:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE"])
    async def missing_api(unknown: str) -> JSONResponse:
        return JSONResponse({"ok": False, "code": "NOT_FOUND"}, status_code=404)

    # .mjs must be served as JavaScript on Windows as well as Unix.
    import mimetypes

    mimetypes.add_type("text/javascript", ".mjs")
    app.mount("/shared", StaticFiles(directory=ROOT / "shared"), name="shared")
    app.mount("/", StaticFiles(directory=ROOT / "public", html=True), name="public")
    return app


def main() -> None:
    uvicorn.run(
        create_app(allow_test_reset=os.environ.get("ALLOW_TEST_RESET") == "1"),
        host="127.0.0.1",
        port=int(os.environ.get("SERVER_PORT", "3001")),
        workers=1,
        access_log=False,
    )


if __name__ == "__main__":
    main()
