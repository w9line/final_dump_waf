#!/usr/bin/env python3
"""Real HTTP/TLS/SSH requests from the lab's separate client network namespace."""
from __future__ import annotations

import http.client
import json
import socket
import ssl
import sys
import urllib.request

HOST = "router"


def request(connection: http.client.HTTPConnection, method: str, path: str, body=None, headers=None):
    connection.request(method, path, body=body, headers=headers or {})
    response = connection.getresponse()
    result = (response.status, response.getheaders(), response.read())
    return result


def http_cases():
    flask = http.client.HTTPConnection(HOST, 5000, timeout=5)
    status, headers, body = request(flask, "GET", "/echo?x=1&x=two", headers={"Host": "flask.ctf.test", "X-Lab-Token": "preserve-me"})
    assert status == 200, (status, body)
    data = json.loads(body)
    assert data["backend"] == "flask" and data["method"] == "GET"
    assert data["host"] == "flask.ctf.test" and data["x_lab_token"] == "preserve-me"
    assert data["path"] == "/echo" and data["query"] == "x=1&x=two"
    status, _, body = request(flask, "POST", "/json?mode=post", json.dumps({"a": 1, "text": "hello"}), {
        "Host": "flask.ctf.test", "Content-Type": "application/json", "Connection": "keep-alive",
    })
    assert status == 200, (status, body)
    posted = json.loads(body)
    assert posted["method"] == "POST" and posted["query"] == "mode=post"
    assert json.loads(posted["body"]) == {"a": 1, "text": "hello"}
    flask.close()

    # NGINX keeps an HTTP/1.1 connection alive across two proxied requests.
    nginx = http.client.HTTPConnection(HOST, 8080, timeout=5)
    status, headers, body = request(nginx, "GET", "/one?keep=1", headers={"Host": "nginx.ctf.test"})
    assert status == 200 and dict(headers).get("X-Aegis-Backend") == "nginx", (status, headers, body)
    first_socket = nginx.sock
    status, _, body = request(nginx, "GET", "/two?keep=2", headers={"Host": "nginx.ctf.test"})
    assert status == 200 and nginx.sock is first_socket, "HTTP connection was not reused"
    nginx.close()

    fastapi = http.client.HTTPConnection(HOST, 8000, timeout=5)
    status, _, body = request(fastapi, "POST", "/items/7?q=a%2Fb", json.dumps({"ok": True}), {
        "Host": "fastapi.ctf.test", "Content-Type": "application/json", "X-Lab-Token": "fastapi-preserve",
    })
    assert status == 200, (status, body)
    data = json.loads(body)
    assert data["backend"] == "fastapi" and data["host"] == "fastapi.ctf.test"
    assert data["x_lab_token"] == "fastapi-preserve"
    assert data["path"] == "/items/7" and data["query"] == "q=a%2Fb" and json.loads(data["body"]) == {"ok": True}
    fastapi.close()


def dynamic_case():
    with urllib.request.urlopen("http://router:31337/new?service=late", timeout=5) as response:
        data = json.loads(response.read())
        assert response.status == 200 and data["backend"] == "dynamic-31337"
        assert data["host"].startswith("router") and data["query"] == "service=late"


def tls_ssh_cases():
    context = ssl._create_unverified_context()
    with urllib.request.urlopen("https://router:8443/secure", context=context, timeout=5) as response:
        assert response.status == 200 and response.headers["X-Aegis-Backend"] == "https"
        assert response.read() == b"tls-backend-ok\n"
    with socket.create_connection((HOST, 2222), timeout=5) as client:
        banner = client.recv(128)
        assert banner.startswith(b"SSH-2.0-"), banner


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "dynamic":
        dynamic_case()
    elif len(sys.argv) > 1 and sys.argv[1] == "bypass":
        tls_ssh_cases()
    else:
        http_cases()
    print("lab client checks passed")
