"""The file manager, served by the panel under /files/.

FileBrowser runs without a login and is published nowhere: the only way in
is through the panel, so it gets the panel's Host check (DNS rebinding), its
login, and its cross-site check for free. It used to listen on
127.0.0.1:8081 on its own, where a rebinding page could write to /data, e.g. a
plugin .jar, and so run code in the server.

A plain streaming proxy, stdlib only: request bodies of any size go through
in chunks, and responses are relayed as they arrive (FileBrowser's live
updates are a long-lived event stream). FileBrowser is configured with
baseURL /files/, so paths and redirects need no rewriting.
"""

import http.client
from urllib.parse import urlsplit

PREFIX = "/files/"
CHUNK = 64 * 1024
# Connection-scoped headers (RFC 9110 7.6.1) are not forwarded. Authorization is
# the panel's own login and stays here; FileBrowser has no use for it.
_HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "proxy-connection",
        "te", "trailer", "transfer-encoding", "upgrade", "authorization"}
# The file manager is on the panel's origin, so its responses get the panel's
# framing and sniffing rules whatever FileBrowser sends (N-02): no site may frame
# it (clickjacking a panel without a login), and nothing is content-sniffed.
# FileBrowser's own Content-Security-Policy is kept; this is a second one, and
# browsers enforce both.
_REPLACED = {"x-frame-options", "x-content-type-options"}
ALWAYS = [("X-Frame-Options", "DENY"), ("X-Content-Type-Options", "nosniff"),
          ("Content-Security-Policy", "frame-ancestors 'none'")]
# Under /api/ FileBrowser serves the files in /data themselves, inline: any page
# a plugin or a user drops there would run as the panel's origin. sandbox gives
# such a page an origin of its own and no scripts, forms or popups; images and
# text still show in the browser, which Content-Disposition: attachment would
# turn into downloads. The UI never opens /api/ as a page, so nothing breaks.
SANDBOXED = (PREFIX + "api/", PREFIX + "public/")
CONNECT_TIMEOUT = 10
# Long, not infinite: the event stream idles between updates, but a hung
# FileBrowser must not hold a panel thread forever.
READ_TIMEOUT = 600


def handles(path: str) -> bool:
    return path == PREFIX.rstrip("/") or path.startswith(PREFIX)


def safety_headers(path: str):
    """The headers every /files/ response carries, FileBrowser's or our own."""
    sandbox = urlsplit(path).path.startswith(SANDBOXED)
    return ALWAYS + ([("Content-Security-Policy", "sandbox")] if sandbox else [])


def forward(handler, upstream: str):
    """Relay handler's current request to `upstream` (e.g. http://filebrowser:80)."""
    if handler.path == PREFIX.rstrip("/"):
        return _plain(handler, 308, "", {"Location": PREFIX})
    if "chunked" in handler.headers.get("Transfer-Encoding", "").lower():
        # Browsers send uploads with a length; refusing beats buffering a stream.
        return _plain(handler, 411, "a Content-Length is required")
    try:
        length = int(handler.headers.get("Content-Length") or 0)
    except ValueError:
        length = -1
    if length < 0:
        return _plain(handler, 400, "bad Content-Length")

    u = urlsplit(upstream)
    conn = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=CONNECT_TIMEOUT)
    try:
        conn.connect()
        conn.sock.settimeout(READ_TIMEOUT)
        conn.putrequest(handler.command, handler.path, skip_host=True, skip_accept_encoding=True)
        for k, v in handler.headers.items():
            if k.lower() not in _HOP and k.lower() != "content-length":
                conn.putheader(k, v)
        client = handler.client_address[0]
        prior = handler.headers.get("X-Forwarded-For")
        conn.putheader("X-Forwarded-For", f"{prior}, {client}" if prior else client)
        conn.putheader("X-Forwarded-Host", handler.headers.get("Host", ""))
        conn.putheader("X-Forwarded-Proto", "http")
        if length or handler.command in ("POST", "PUT", "PATCH"):
            conn.putheader("Content-Length", str(length))
        conn.endheaders()
        left = length
        while left:
            chunk = handler.rfile.read(min(CHUNK, left))
            if not chunk:
                raise ConnectionError("client went away mid-upload")
            conn.send(chunk)
            left -= len(chunk)
        resp = conn.getresponse()
    except (OSError, http.client.HTTPException) as ex:
        conn.close()
        print(f"files: upstream {upstream} failed: {ex}", flush=True)
        return _plain(handler, 502, "The file manager is not reachable (is the filebrowser container running?)")

    try:
        handler.send_response(resp.status, resp.reason)
        for k, v in resp.getheaders():
            if k.lower() not in _HOP and k.lower() not in _REPLACED:
                handler.send_header(k, v)
        for k, v in safety_headers(handler.path):
            handler.send_header(k, v)
        # HTTP/1.0 to the browser: without a length, the body ends when the
        # connection closes, which is how an event stream is relayed as it comes.
        handler.send_header("Connection", "close")
        handler.end_headers()
        handler.close_connection = True
        if handler.command != "HEAD":
            while chunk := resp.read1(CHUNK):
                handler.wfile.write(chunk)
                handler.wfile.flush()
    except OSError:
        pass  # the browser went away (closed the tab, navigated): nothing to tell it
    finally:
        conn.close()


def _plain(handler, code, text, headers=None):
    b = text.encode()
    handler.send_response(code)
    for k, v in [*(headers or {}).items(), *safety_headers(handler.path)]:
        handler.send_header(k, v)
    handler.send_header("Content-Type", "text/plain; charset=utf-8")
    handler.send_header("Content-Length", str(len(b)))
    handler.end_headers()
    handler.wfile.write(b)
