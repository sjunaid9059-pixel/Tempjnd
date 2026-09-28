"""
TempMailer Mini App — Render-ready
Serves the premium UI + proxies mail.tm (fixes CORS).
"""

import os
from pathlib import Path

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

MAIL_API = "https://api.mail.tm"
ROOT = Path(__file__).parent

app = FastAPI(title="TempMailer Mini App")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = httpx.AsyncClient(
    base_url=MAIL_API,
    timeout=15.0,
    headers={"User-Agent": "TempMailerMini/1.0", "Accept": "application/json"},
)


@app.get("/")
async def index():
    return FileResponse(ROOT / "index.html")


@app.api_route("/api/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def proxy(path: str, request: Request):
    """Proxy all /api/* calls to api.mail.tm"""
    if request.method == "OPTIONS":
        return Response(status_code=204)

    url = f"/{path}"
    if request.url.query:
        url += f"?{request.url.query}"

    headers = {}
    auth = request.headers.get("authorization")
    if auth:
        headers["Authorization"] = auth
    ct = request.headers.get("content-type")
    if ct:
        headers["Content-Type"] = ct

    body = await request.body()

    try:
        r = await client.request(
            request.method,
            url,
            headers=headers,
            content=body if body else None,
        )
    except httpx.TimeoutException:
        return JSONResponse({"message": "upstream timeout"}, status_code=504)
    except Exception as e:
        return JSONResponse({"message": str(e)[:200]}, status_code=502)

    # Pass through JSON / text
    media = r.headers.get("content-type", "application/json")
    return Response(content=r.content, status_code=r.status_code, media_type=media)


@app.get("/health")
async def health():
    return {"ok": True}


# Optional: mount if extra static assets added later
# app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8080))
    uvicorn.run("app:app", host="0.0.0.0", port=port, reload=False)
