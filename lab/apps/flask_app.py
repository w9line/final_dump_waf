from flask import Flask, jsonify, request
from threading import Lock

app = Flask(__name__)
_count = 0
_lock = Lock()


@app.route("/", defaults={"path": ""}, methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
@app.route("/<path:path>", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"])
def echo(path):
    global _count
    with _lock:
        _count += 1
        count = _count
    return jsonify({
        "backend": "flask",
        "count": count,
        "method": request.method,
        "host": request.headers.get("Host"),
        "x_lab_token": request.headers.get("X-Lab-Token"),
        "path": request.path,
        "query": request.query_string.decode("utf-8", errors="replace"),
        "body": request.get_data(cache=True).decode("utf-8", errors="replace"),
    })


@app.route("/__counter")
def counter():
    return jsonify({"backend": "flask", "count": _count})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, threaded=True)
