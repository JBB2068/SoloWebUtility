#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
webcore.py - Minimal Flask-style micro-framework built ONLY on the Python 2.7
standard library (BaseHTTPServer / SocketServer / json).

Why this exists: the 3DR Solo ships stock Python 2.7 with no Flask. The modular
SoloWebUtility2 code (modules/*.py) is written against a tiny subset of the Flask
API (Blueprint, request, jsonify, Response, make_response, send_from_directory,
render_template, redirect, stream_with_context). This file provides exactly that
subset so every feature keeps working on the drone with zero third-party packages.

Also runs unchanged on Python 3 for local development.
"""

import os
import sys
import json
import time
import socket
import threading
import mimetypes
import logging

if sys.version_info[0] >= 3:
    from http.server import HTTPServer, BaseHTTPRequestHandler
    from socketserver import ThreadingMixIn
    from urllib.parse import parse_qs, urlparse
    from http.cookies import SimpleCookie
    text_type = str
    binary_type = bytes
else:
    from BaseHTTPServer import HTTPServer, BaseHTTPRequestHandler
    from SocketServer import ThreadingMixIn
    from urlparse import parse_qs, urlparse
    from Cookie import SimpleCookie
    text_type = unicode  # noqa: F821
    binary_type = str

logger = logging.getLogger("webcore")

_local = threading.local()


# ---------------------------------------------------------------------------
# Request
# ---------------------------------------------------------------------------
class _Args(object):
    """Query-string accessor: .get(key, default) returns the first value."""

    def __init__(self, qs):
        self._d = parse_qs(qs or "", keep_blank_values=True)

    def get(self, key, default=None, type=None):
        vals = self._d.get(key)
        if not vals:
            return default
        v = vals[0]
        if type is not None:
            try:
                return type(v)
            except Exception:
                return default
        return v

    def __contains__(self, key):
        return key in self._d

    def __getitem__(self, key):
        return self._d[key][0]

    def to_dict(self):
        return dict((k, v[0]) for k, v in self._d.items())


class _Headers(object):
    """Case-insensitive request header accessor."""

    def __init__(self, raw):
        self._raw = raw

    def get(self, key, default=None):
        v = self._raw.get(key)
        return default if v is None else v

    def __getitem__(self, key):
        return self._raw[key]

    def __contains__(self, key):
        return self._raw.get(key) is not None


class Request(object):
    def __init__(self, handler, method, parsed, body):
        self.method = method
        self.path = parsed.path
        self.query_string = parsed.query
        self.args = _Args(parsed.query)
        self.headers = _Headers(handler.headers)
        self.data = body
        self.remote_addr = handler.client_address[0] if handler.client_address else None
        self.cookies = {}
        raw_cookie = handler.headers.get("Cookie")
        if raw_cookie:
            try:
                jar = SimpleCookie()
                jar.load(str(raw_cookie))
                for k in jar.keys():
                    self.cookies[k] = jar[k].value
            except Exception:
                pass

    def get_data(self):
        return self.data

    def get_json(self, silent=False, force=True):
        try:
            if not self.data:
                return None
            raw = self.data
            if isinstance(raw, binary_type):
                raw = raw.decode("utf-8")
            return json.loads(raw)
        except Exception:
            if silent:
                return None
            raise


class _RequestProxy(object):
    """Thread-local `request` object, like flask.request."""

    def __getattr__(self, name):
        cur = getattr(_local, "request", None)
        if cur is None:
            raise RuntimeError("Working outside of request context.")
        return getattr(cur, name)


request = _RequestProxy()


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------
class Response(object):
    def __init__(self, response=b"", status=200, mimetype=None, headers=None, content_type=None):
        self.response = response
        self.status_code = status
        self.headers = {}
        self.cookies = []  # list of raw Set-Cookie header values
        if headers:
            for k, v in dict(headers).items():
                self.headers[k] = v
        if content_type:
            self.headers["Content-Type"] = content_type
        elif mimetype:
            if mimetype.startswith("text/") and "charset" not in mimetype:
                mimetype += "; charset=utf-8"
            self.headers["Content-Type"] = mimetype
        elif "Content-Type" not in self.headers:
            self.headers["Content-Type"] = "text/html; charset=utf-8"

    @property
    def mimetype(self):
        return self.headers.get("Content-Type", "").split(";")[0]

    def set_cookie(self, key, value="", max_age=None, path="/", httponly=False, **kwargs):
        parts = ["%s=%s" % (key, value), "Path=%s" % path]
        if max_age is not None:
            parts.append("Max-Age=%d" % int(max_age))
        if httponly:
            parts.append("HttpOnly")
        self.cookies.append("; ".join(parts))

    def delete_cookie(self, key, path="/", **kwargs):
        self.cookies.append("%s=; Path=%s; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT" % (key, path))

    def is_streamed(self):
        r = self.response
        return not isinstance(r, (binary_type, text_type))

    def body_bytes(self):
        r = self.response
        if isinstance(r, text_type):
            return r.encode("utf-8")
        return r


def jsonify(*args, **kwargs):
    if args and len(args) == 1:
        payload = args[0]
    elif args:
        payload = list(args)
    else:
        payload = kwargs
    return Response(json.dumps(payload), mimetype="application/json")


def make_response(*args):
    if not args:
        return Response()
    rv = args[0]
    status = None
    headers = None
    if len(args) > 1:
        status = args[1]
    if len(args) > 2:
        headers = args[2]
    resp = _coerce(rv)
    if status is not None:
        resp.status_code = status
    if headers:
        for k, v in dict(headers).items():
            resp.headers[k] = v
    return resp


def stream_with_context(gen):
    return gen


def redirect(location, code=302):
    r = Response(b"", status=code)
    r.headers["Location"] = location
    return r


def _coerce(rv):
    if isinstance(rv, Response):
        return rv
    if isinstance(rv, (dict, list)):
        return jsonify(rv)
    if isinstance(rv, (binary_type, text_type)):
        return Response(rv)
    if rv is None:
        return Response(b"", status=204)
    return Response(rv)  # iterable / generator


def _read_file(path):
    with open(path, "rb") as f:
        return f.read()


_MIME_OVERRIDES = {
    ".js": "application/javascript",
    ".css": "text/css",
    ".html": "text/html",
    ".json": "application/json",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".svg": "image/svg+xml",
    ".pdf": "application/pdf",
}


def send_from_directory(directory, filename):
    safe = os.path.normpath(filename).replace("\\", "/").lstrip("/")
    if safe.startswith(".."):
        return Response(b"Forbidden", status=403, mimetype="text/plain")
    full = os.path.join(directory, safe)
    if not os.path.isfile(full):
        return Response(b"Not found", status=404, mimetype="text/plain")
    ext = os.path.splitext(full)[1].lower()
    ctype = _MIME_OVERRIDES.get(ext) or mimetypes.guess_type(full)[0] or "application/octet-stream"
    if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
        ctype += "; charset=utf-8"
    return Response(_read_file(full), mimetype=ctype)


_TEMPLATE_DIRS = []


def render_template(name, **context):
    """Templates in this project contain no Jinja syntax - served verbatim."""
    for d in _TEMPLATE_DIRS:
        p = os.path.join(d, name)
        if os.path.isfile(p):
            return _read_file(p).decode("utf-8")
    raise IOError("Template not found: %s" % name)


# ---------------------------------------------------------------------------
# Routing
# ---------------------------------------------------------------------------
class Blueprint(object):
    def __init__(self, name, import_name=None):
        self.name = name
        self.rules = []  # (rule, methods, func)

    def route(self, rule, methods=None):
        meths = [m.upper() for m in (methods or ["GET"])]

        def decorator(f):
            self.rules.append((rule, meths, f))
            return f
        return decorator


class App(object):
    def __init__(self, static_dir=None, template_dir=None):
        self.static_dir = static_dir
        self.template_dir = template_dir
        self.config = {}
        self._routes = {}  # (path, METHOD) -> func
        self._paths = set()
        self.fallback = None  # func(path) -> response, for unmatched GETs
        if template_dir:
            _TEMPLATE_DIRS.append(template_dir)

    def register_blueprint(self, bp):
        for rule, methods, f in bp.rules:
            self.add_url_rule(rule, None, f, methods=methods)

    def add_url_rule(self, rule, endpoint, func, methods=None):
        for m in [x.upper() for x in (methods or ["GET"])]:
            self._routes[(rule, m)] = func
        self._paths.add(rule)

    def route(self, rule, methods=None):
        def decorator(f):
            self.add_url_rule(rule, None, f, methods=methods)
            return f
        return decorator

    def dispatch(self, method, path):
        func = self._routes.get((path, method))
        if func is None and method == "HEAD":
            func = self._routes.get((path, "GET"))
        if func is not None:
            return _coerce_view_result(func())
        if method in ("GET", "HEAD") and self.fallback is not None:
            return _coerce_view_result(self.fallback(path))
        if path in self._paths:
            return Response(json.dumps({"error": True, "message": "Method not allowed."}),
                            status=405, mimetype="application/json")
        return Response(json.dumps({"error": True, "message": "Not found."}),
                        status=404, mimetype="application/json")

    def test_client(self):
        app_self = self

        class DummyHandler(object):
            def __init__(self, headers):
                self.headers = headers
                self.client_address = ("127.0.0.1", 12345)

        class TestClient(object):
            def get(self, path, headers=None):
                return self.open(path, method="GET", headers=headers)

            def post(self, path, data=None, json=None, headers=None):
                return self.open(path, method="POST", data=data, json=json, headers=headers)

            def open(self, path, method="GET", data=None, json=None, headers=None):
                hdrs = dict(headers or {})
                body = b""
                if json is not None:
                    import json as _json
                    body = _json.dumps(json).encode("utf-8")
                    hdrs["Content-Type"] = "application/json"
                elif data is not None:
                    body = data if isinstance(data, (bytes, bytearray)) else str(data).encode("utf-8")

                u = urlparse(path)
                req = Request(DummyHandler(hdrs), method, u, body)
                _local.request = req
                try:
                    resp = app_self.dispatch(method, u.path)

                    class TestResponse(object):
                        def __init__(self, r):
                            self._r = r
                            self.status_code = r.status_code
                            self.headers = r.headers
                            self.data = r.body_bytes()

                        def get_json(self, silent=False):
                            import json as _json
                            try:
                                return _json.loads(self.data.decode("utf-8"))
                            except Exception:
                                if silent:
                                    return None
                                raise

                    return TestResponse(resp)
                finally:
                    _local.request = None

        return TestClient()

    def run(self, host="0.0.0.0", port=5000, **kwargs):
        make_server(self, host, port).serve_forever()


def _coerce_view_result(rv):
    """Handle Flask-style return values: resp | (resp, status) | (resp, status, headers)."""
    if isinstance(rv, tuple):
        body = rv[0]
        resp = _coerce(body)
        if len(rv) > 1 and rv[1] is not None:
            if isinstance(rv[1], int):
                resp.status_code = rv[1]
            elif isinstance(rv[1], dict):
                resp.headers.update(rv[1])
        if len(rv) > 2 and rv[2]:
            resp.headers.update(dict(rv[2]))
        return resp
    return _coerce(rv)


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------
class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32


def _make_handler(app):
    class Handler(BaseHTTPRequestHandler):
        server_version = "SoloWebUtility2"
        sys_version = ""

        def log_message(self, fmt, *args):
            pass

        def _handle(self, method):
            parsed = urlparse(self.path)
            body = b""
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = 0
            if length > 0:
                body = self.rfile.read(length)
            _local.request = Request(self, method, parsed, body)
            try:
                try:
                    resp = app.dispatch(method, parsed.path)
                except Exception:
                    logger.exception("Unhandled error in %s %s", method, parsed.path)
                    resp = Response(json.dumps({"error": True, "message": "Internal server error."}),
                                    status=500, mimetype="application/json")
                self._send(resp, method)
            finally:
                _local.request = None

        def _send(self, resp, method):
            try:
                self.send_response(resp.status_code)
                for k, v in resp.headers.items():
                    self.send_header(k, v)
                for c in resp.cookies:
                    self.send_header("Set-Cookie", c)
                if resp.is_streamed():
                    self.send_header("Connection", "close")
                    self.end_headers()
                    if method == "HEAD":
                        return
                    it = resp.response
                    try:
                        for chunk in it:
                            if isinstance(chunk, text_type):
                                chunk = chunk.encode("utf-8")
                            self.wfile.write(chunk)
                            self.wfile.flush()
                    finally:
                        close = getattr(it, "close", None)
                        if close:
                            try:
                                close()
                            except Exception:
                                pass
                else:
                    data = resp.body_bytes()
                    self.send_header("Content-Length", str(len(data)))
                    self.end_headers()
                    if method != "HEAD" and data:
                        self.wfile.write(data)
            except (socket.error, IOError):
                pass  # client went away

        def do_GET(self):
            self._handle("GET")

        def do_HEAD(self):
            self._handle("HEAD")

        def do_POST(self):
            self._handle("POST")

        def do_PUT(self):
            self._handle("PUT")

        def do_DELETE(self):
            self._handle("DELETE")

        def do_OPTIONS(self):
            self.send_response(204)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, DELETE, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, X-Auth-Token, Authorization")
            self.send_header("Content-Length", "0")
            self.end_headers()

    return Handler


def make_server(app, host="0.0.0.0", port=5000, max_retries=60, retry_delay=2):
    bind_host = host if host else "0.0.0.0"
    server = None
    for attempt in range(max_retries):
        try:
            server = ThreadedHTTPServer((bind_host, port), _make_handler(app))
            break
        except (socket.error, Exception) as e:
            if attempt == max_retries - 1:
                logger.error("[CRITICAL] Could not bind to %s:%d after %d retries: %s", bind_host, port, max_retries, e)
                raise
            logger.warning("[!] Port %d busy or network not ready (%s), retrying in %ds (%d/%d)...",
                           port, e, retry_delay, attempt + 1, max_retries)
            try:
                os.system("fuser -k %d/tcp 2>/dev/null || true" % port)
            except Exception:
                pass
            time.sleep(retry_delay)
    return server
