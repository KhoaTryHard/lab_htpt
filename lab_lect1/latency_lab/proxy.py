"""HTTP proxy adding half the simulated RTT before request and response."""

import asyncio
import math
import os
from contextlib import asynccontextmanager

import httpx
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


def parse_rtt(raw: str | float) -> float:
    delay = float(raw)
    if not math.isfinite(delay) or delay < 0:
        raise ValueError("RTT must be a finite, non-negative number of milliseconds")
    return delay


def forward_headers(headers: httpx.Headers | dict, *, response: bool = False) -> dict[str, str]:
    blocked = HOP_HEADERS | {"host", "content-length"}
    blocked |= {part.strip().lower() for part in headers.get("connection", "").split(",")}
    # HTTPX decodes compressed response bodies before returning .content.
    if response:
        blocked |= {"content-encoding"}
    return {key: value for key, value in headers.items() if key.lower() not in blocked}


def create_app(
    *,
    target_port: int = 3001,
    rtt_ms: float = 0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    default_rtt = parse_rtt(rtt_ms)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        async with httpx.AsyncClient(
            base_url=f"http://127.0.0.1:{target_port}",
            transport=transport,
            timeout=30,
            trust_env=False,
        ) as client:
            app.state.upstream = client
            yield

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.get("/api/lab-config")
    async def config() -> dict:
        return {"rttMs": default_rtt}

    @app.api_route(
        "/{path:path}", methods=["GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"]
    )
    async def forward(request: Request, path: str) -> Response:
        delayed = request.method == "POST" and path == "api/register"
        try:
            rtt = parse_rtt(request.headers.get("x-lab-rtt", str(default_rtt)))
        except ValueError:
            return JSONResponse({"ok": False, "code": "INVALID_RTT"}, status_code=400)
        body = await request.body()
        if delayed:
            await asyncio.sleep(rtt / 2000)
        try:
            upstream = await app.state.upstream.request(
                request.method,
                httpx.URL(path="/" + path, query=request.url.query.encode("ascii")),
                headers=forward_headers(dict(request.headers)),
                content=body,
            )
        except httpx.RequestError as error:
            return JSONResponse(
                {"ok": False, "code": "UPSTREAM_UNAVAILABLE", "message": str(error)},
                status_code=502,
            )
        if delayed:
            await asyncio.sleep(rtt / 2000)
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            headers=forward_headers(upstream.headers, response=True),
        )

    return app


def main() -> None:
    uvicorn.run(
        create_app(
            target_port=int(os.environ.get("SERVER_PORT", "3001")),
            rtt_ms=parse_rtt(os.environ.get("RTT_MS", "0")),
        ),
        host="127.0.0.1",
        port=int(os.environ.get("PROXY_PORT", "3000")),
        workers=1,
        access_log=False,
    )


if __name__ == "__main__":
    main()
