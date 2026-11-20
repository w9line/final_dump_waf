from fastapi import FastAPI, Request
from threading import Lock

app = FastAPI()
_count = 0
_lock = Lock()


@app.api_route("/{path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
async def echo(request: Request, path: str):
    global _count
    with _lock:
        _count += 1
        count = _count
    body = await request.body()
    return {
        "backend": "fastapi",
        "count": count,
        "method": request.method,
        "host": request.headers.get("host"),
        "x_lab_token": request.headers.get("x-lab-token"),
        "path": request.url.path,
        "query": request.url.query,
        "body": body.decode("utf-8", errors="replace"),
    }
