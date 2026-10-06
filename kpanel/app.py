"""kPanel: dashboard, server settings, players and gamerules live.

Two kinds of change, two paths:

* server.properties (Settings): Minecraft reads it only at startup, so a change
  takes effect on the next restart, which the panel can do (RCON stop; the
  restart policy brings the server back). Two modes, see settings_mode():
  by default the panel writes the file itself. With a GitHub fork configured it
  never writes it: it opens a PR that edits the compose file (COMPOSE_PATH), and
  merging deploys it. Git is then the single source of truth; edits made on the
  server show up as importable differences.
* Whitelist, ops, kicks (Players) and gamerules (Gamerules) are live server
  state, sent over RCON: immediate, no restart, no git. whitelist.json and
  ops.json on the server are the only source of truth for players.

UI rule: show values and controls, not prose. Explanations live in hover
tooltips (title attributes), so the page stays scannable.

Who can reach it is the authentication boundary: loopback by default, a
login with compose/lan.yml, the tailnet with compose/tailscale.yml. It refuses
to start unless told which (require_auth_boundary).
"""

import base64
import collections
import glob
import hashlib
import html
import json
import os
import pathlib
import re
import socket
import struct
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import yaml

import auth
import compose_edit as ce
import filesproxy
import history
import procstats
import release
import gamerules as gr
import rcon
import settings as st
import ui
from github import BRANCH_PREFIX, GitHub, GitHubError
from rcon import Rcon, RconError

CFG = {
    "token": os.environ.get("GITHUB_TOKEN", ""),
    "repo": os.environ.get("GITHUB_REPO", ""),
    "base": os.environ.get("GITHUB_BRANCH", "main"),
    "compose": os.environ.get("COMPOSE_PATH", "docker-compose.yml"),
    "props": os.environ.get("PROPERTIES_PATH", "/data/server.properties"),
    "data": os.environ.get("DATA_DIR", "/data"),
    "backups": os.environ.get("BACKUPS_DIR", "/backups"),
    # The file manager, served by the panel under /files/ (see filesproxy).
    "files_upstream": os.environ.get("FILES_UPSTREAM", ""),
    # Where the Files link points: /files/ when the panel serves it.
    "files_url": os.environ.get("FILES_URL", "") or ("/files/" if os.environ.get("FILES_UPSTREAM") else ""),
    "public_host": os.environ.get("PUBLIC_HOST", "localhost"),
    "public_port": int(os.environ.get("PUBLIC_PORT", "25565")),
    "game_host": os.environ.get("GAME_HOST", "mc"),
    "game_port": int(os.environ.get("GAME_PORT", "25565")),
    "rcon_host": os.environ.get("RCON_HOST", "mc"),
    "rcon_port": int(os.environ.get("RCON_PORT", "25575")),
    # Empty: use the password the server generated (see rcon_password()).
    "rcon_password": os.environ.get("RCON_PASSWORD", ""),
    "history": os.environ.get("HISTORY_PATH", "/var/lib/kpanel/history.json"),
    "sample_seconds": int(os.environ.get("SAMPLE_SECONDS", "300")),
    # The login, HTTP basic auth: KPANEL_USER plus an argon2id hash made by
    # hashpw.py. None of these set means the panel trusts whoever can reach it,
    # which is only safe on loopback or behind a tailnet or VPN.
    "user": os.environ.get("KPANEL_USER", "admin"),
    "password_hash": os.environ.get("KPANEL_PASSWORD_HASH", ""),
    # Deprecated: "user:password" in plain text. Works in 0.3 with a warning.
    "basic_auth": os.environ.get("KPANEL_BASIC_AUTH", ""),
    # Asks api.github.com twice a day whether a newer kPanel exists. 0 turns it off.
    "update_check": os.environ.get("KPANEL_UPDATE_CHECK", "1") != "0",
    # Host names the panel answers to besides localhost, comma-separated; "*" is
    # any. See host_allowed(): without a login, this is what stops DNS rebinding.
    "allowed_hosts": os.environ.get("KPANEL_ALLOWED_HOSTS", ""),
    # 1 only behind `tailscale serve` (compose/tailscale.yml): it sets
    # Tailscale-User-Login. Anywhere else that header is whatever the client says.
    "trust_ts_headers": os.environ.get("KPANEL_TRUST_TS_HEADERS", "") == "1",
    # Other containers of this stack reach the panel straight over the compose
    # network, past the published port. See peer_allowed(): service names whose
    # requests are refused, and (tailscale.yml) the only ones answered.
    "refuse_peers": os.environ.get("KPANEL_REFUSE_PEERS", ""),
    "only_peers": os.environ.get("KPANEL_ONLY_PEERS", ""),
}
SPARK_POINTS = 5  # per graph: the last 4 samples plus the live value
UNSET_DEFAULTS = {"blank", "[random text]", ""}

# Shown when the server sends no icon of its own (no server-icon.png): Paper's
# logo, the same logo.png Paper ships in its jar. Embedded, so no external request.
try:
    import base64 as _b64
    PAPER_ICON = "data:image/png;base64," + _b64.b64encode(
        (pathlib.Path(__file__).parent / "paper-logo.png").read_bytes()).decode()
except OSError:
    PAPER_ICON = ""

# The panel's own icon (tab, bookmarks, header): Minetest Game's 16x16 diamond
# pickaxe by BlockMen, CC BY-SA 3.0 (see favicon.LICENSE.txt). Mojang's own
# texture is copyrighted; this is the closest openly licensed one.
try:
    FAVICON_PNG = (pathlib.Path(__file__).parent / "favicon.png").read_bytes()
except OSError:
    FAVICON_PNG = b""

# Live changes leave no PR behind, so keep a visible trail. In memory: it
# resets when the panel restarts. Each entry is also printed to the container log.
ACTIONS = collections.deque(maxlen=50)

e = html.escape


def rcon_password():
    """RCON_PASSWORD if set, otherwise the server's own.

    With RCON_PASSWORD unset, the itzg image generates a random password on
    every start and writes it to server.properties. Read per connection, not
    once, because a restart changes it.
    """
    return CFG["rcon_password"] or _live_props().get("rcon.password", "")


def client():
    return Rcon(CFG["rcon_host"], CFG["rcon_port"], rcon_password())


_LOGIN_NAME = re.compile(r"[A-Za-z0-9._%+@-]{1,100}")


def who_from(headers):
    """Who made a change, for the action log and PR bodies: the tailnet login, or ""."""
    if not CFG["trust_ts_headers"]:
        return ""
    v = headers.get("Tailscale-User-Login", "")
    return v if _LOGIN_NAME.fullmatch(v) else ""


def record(who, what, reply):
    entry = (time.strftime("%Y-%m-%d %H:%M:%S"), who or "panel", what, reply)
    ACTIONS.appendleft(entry)
    print(f"LIVE {entry[0]} {entry[1]}: {what} -> {reply}", flush=True)


def _read_json(name):
    try:
        with open(os.path.join(CFG["data"], name), encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, PermissionError):
        return []


def _live_props():
    try:
        with open(CFG["props"], encoding="utf-8") as f:
            return st.parse_properties(f.read())
    except (FileNotFoundError, PermissionError):
        return {}


# --- settings state ---------------------------------------------------------------

def load():
    """Everything the Settings page needs. Raises GitHubError / OSError with a readable message."""
    gh = GitHub(CFG["token"], CFG["repo"], CFG["base"])
    commit = gh.head()
    text, blob = gh.read(CFG["compose"], commit)
    env = yaml.safe_load(text)["services"]["mc"].get("environment") or {}
    custom = ce.custom_properties(text)
    live = _live_props()
    rows = {}
    for key, p in st.PROPS.items():
        if p.env and p.env in env:
            raw = ce.raw_value(text, p.env)
            external = ce.is_externally_managed(raw)
            git = None if external else str(env[p.env])
            source = "coolify" if external else "env"
        elif not p.env and key in custom:
            git, source = custom[key], "custom"
        else:
            git, source = None, None
        lv = live.get(key)
        default = "" if p.default.lower() in UNSET_DEFAULTS else p.default
        unpinned = git is None and source != "coolify"
        rows[key] = {
            "p": p, "git": git, "source": source, "live": lv,
            "value": git if git is not None else (lv if lv is not None else default),
            # git says X, the server says Y: a merge not yet deployed, or a hand edit
            # the image will overwrite on the next start.
            "drift": git is not None and lv is not None and st.normalize(p, git) != st.normalize(p, lv),
            # nothing in git, and someone moved it off the Minecraft default
            "live_only": unpinned and lv is not None and st.normalize(p, lv) != st.normalize(p, default),
        }
    return gh, commit, text, blob, rows, gh.open_panel_prs()


def load_file():
    """The Settings rows in file mode: server.properties is the only source."""
    try:
        with open(CFG["props"], encoding="utf-8") as f:
            live = st.parse_properties(f.read())
    except FileNotFoundError:
        raise OSError("No server.properties yet: the server writes it on its first start.") from None
    rows = {}
    for key, p in st.PROPS.items():
        default = "" if p.default.lower() in UNSET_DEFAULTS else p.default
        lv = live.get(key)
        rows[key] = {"p": p, "git": None, "source": None, "live": lv,
                     "value": lv if lv is not None else default, "drift": False, "live_only": False}
    return rows


def save_file(wanted: dict, who: str):
    """Write the validated values into server.properties, in place.

    The panel runs as its own uid in the server's group (so the server can't
    read the panel's processes), and /data is not group-writable: there is no
    temp file and rename, only the file's own group write bit. One write of a
    ~2 KB file, then truncate and fsync. O_NOFOLLOW: the server owns /data, so a
    symlink planted in place of the file must not redirect the write.
    """
    path = CFG["props"]
    with open(path, encoding="utf-8") as f:
        text = f.read()
    new = st.write_properties(text, wanted).encode("ascii")
    try:
        fd = os.open(path, os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0))
    except PermissionError:
        raise OSError(f"The panel may not write {path}: it needs the file group-writable "
                      f"(the server's default). Fix it with: docker compose exec mc chmod g+w {path}") from None
    with os.fdopen(fd, "wb") as f:
        f.write(new)
        f.truncate()
        f.flush()
        os.fsync(f.fileno())
    for k, v in sorted(wanted.items()):
        record(who, f"set {k}={v}", "saved to server.properties")


RESTART_DELAY = 1.0  # seconds: lets the response reach the browser first


def restart(who):
    """Stop the server; restart: unless-stopped starts it again.

    The panel shares mc's process namespace (pid: service:mc), so it goes down
    with the server and comes back once it is up. That is why the stop is sent
    after the response, not before it.
    """
    c = client()
    c.command("list")  # fail now, with a readable error, if RCON is unreachable
    record(who, "stop", "restarting")
    threading.Timer(RESTART_DELAY, lambda: _safe(lambda: c.command("stop"))).start()


def make_pr(gh, commit, text, blob, rows, wanted: dict, who: str, why: str) -> str:
    """wanted: {property-key: new value} (already validated). Returns the PR URL."""
    env_changes, custom = {}, {}
    for key, v in wanted.items():
        p = rows[key]["p"]
        if p.env:
            env_changes[p.env] = v   # dedicated itzg env var
        else:
            custom[key] = v          # no env var exists: CUSTOM_SERVER_PROPERTIES
    new_text = ce.apply_changes(text, env_changes, custom)
    names = ", ".join(f"{k}={v}" for k, v in sorted(wanted.items()))
    title = f"kPanel: {why} {names}"[:120]
    lines = ["| Key | Was | Now | Set via |", "|---|---|---|---|"]
    for k, v in sorted(wanted.items()):
        r, p = rows[k], rows[k]["p"]
        was = r["git"] if r["git"] is not None else f"(not in git; live: {r['live']})"
        lines.append(f"| `{k}` | `{was}` | `{v}` | {'`' + p.env + '`' if p.env else '`CUSTOM_SERVER_PROPERTIES`'} |")
    body = "\n".join([
        f"Opened from kPanel{f' by `{who}`' if who else ''}: {why}.", "",
        *lines, "",
        "**Merging redeploys and restarts the server.** Merge when nobody is online.",
    ])
    branch = f"{BRANCH_PREFIX}{time.strftime('%Y%m%d-%H%M%S')}"
    return gh.open_pr(path=CFG["compose"], new_text=new_text, blob_sha=blob, from_commit=commit,
                      branch=branch, title=title, body=body)


# --- dashboard state --------------------------------------------------------------

def _varint(n):
    out = b""
    while True:
        b, n = n & 0x7F, n >> 7
        out += bytes([b | (0x80 if n else 0)])
        if not n:
            return out


def _read_varint(s):
    n = shift = 0
    while True:
        b = s.recv(1)
        if not b:
            raise OSError("connection closed")
        n |= (b[0] & 0x7F) << shift
        shift += 7
        if not b[0] & 0x80:
            return n


_DATA_PNG = re.compile(r"data:image/png;base64,[A-Za-z0-9+/]+={0,2}")


def _int(v):
    try:
        return int(v)
    except (TypeError, ValueError):
        return 0


def status_ping(host, port, timeout=4.0):
    """Minecraft Server List Ping: exactly what a client's server list shows."""
    t0 = time.monotonic()
    with socket.create_connection((host, port), timeout=timeout) as s:
        hs = _varint(0) + _varint(-1 & 0xFFFFFFFF) + _varint(len(host)) + host.encode() + \
            struct.pack(">H", port) + _varint(1)
        s.sendall(_varint(len(hs)) + hs + b"\x01\x00")
        _read_varint(s), _read_varint(s)
        n = _read_varint(s)
        data = b""
        while len(data) < n:
            chunk = s.recv(n - len(data))
            if not chunk:
                raise OSError("connection closed")
            data += chunk
    ms = round((time.monotonic() - t0) * 1000)
    r = json.loads(data)
    d = r.get("description", "")
    motd = d if isinstance(d, str) else d.get("text", "") + "".join(x.get("text", "") for x in d.get("extra", []))
    # int(): the counts go into the page unescaped, and a server is free to send anything.
    return {"version": r.get("version", {}).get("name", "?"), "online": _int(r.get("players", {}).get("online")),
            "max": _int(r.get("players", {}).get("max")), "motd": rcon._COLOR.sub("", motd), "ms": ms,
            # data:image/png;base64,... when the server has a server-icon.png
            # Checked against the base64 alphabet, not just the prefix: the icon goes
            # into a src="..." attribute, and a quote in it would close the attribute
            # early and let the rest become markup.
            "favicon": (fav if _DATA_PNG.fullmatch(fav := str(r.get("favicon", "") or "")) else None)}


def _dir_size(path):
    total = 0
    for root, _, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total


def _human_bytes(n):
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or unit == "TB":
            return f"{n:.0f} {unit}" if unit in ("B", "KB") else f"{n:.1f} {unit}"
        n /= 1024


def _ago(ts):
    d = int(time.time() - ts)
    for size, unit in ((86400, "d"), (3600, "h"), (60, "min")):
        if d >= size:
            return f"{d // size} {unit} ago"
    return "just now"


def _first_float(reply):
    """'TPS from last 1m, 5m, 15m: 20.0, 19.9, 20.0' -> 20.0 (Paper may prefix '*')."""
    m = re.search(r":\s*\*?\s*(\d+(?:\.\d+)?)", reply or "")
    return float(m.group(1)) if m else None


def dashboard_state():
    s = {}
    try:
        s["ping"] = status_ping(CFG["game_host"], CFG["game_port"])
    except (OSError, ValueError) as ex:
        s["ping"], s["ping_error"] = None, str(ex)
    try:
        replies = client().run("list", "tps")
        s["players"], _ = rcon.parse_list(replies[0])
        s["tps"] = _first_float(replies[1])
    except RconError:
        s["players"], s["tps"] = None, None
    try:
        s["ip"] = socket.gethostbyname(CFG["public_host"])
    except OSError:
        s["ip"] = None
    props = _live_props()
    s["difficulty"], s["gamemode"] = props.get("difficulty"), props.get("gamemode")
    s["whitelisted"] = len(_read_json("whitelist.json"))
    s["ops"] = len(_read_json("ops.json"))
    s["world"] = _dir_size(os.path.join(CFG["data"], props.get("level-name", "world")))
    backups = sorted(glob.glob(os.path.join(CFG["backups"], "*.tar.gz")), key=os.path.getmtime)
    s["backups"] = len(backups)
    s["last_backup"] = (os.path.getmtime(backups[-1]), os.path.getsize(backups[-1])) if backups else None
    s["cpu"] = CPU.percent()
    s["mem"] = procstats.memory()
    return s


HISTORY = history.History(CFG["history"])
CPU = procstats.CpuMeter()  # shared by page loads and the sampler (see procstats)


def take_sample():
    """One history point. Missing metrics are simply left out of the sample."""
    s = {"t": int(time.time())}
    try:
        p = status_ping(CFG["game_host"], CFG["game_port"])
        s.update(players=p["online"], max=p["max"], ms=p["ms"])
    except (OSError, ValueError):
        pass
    try:
        s["tps"] = _first_float(client().command("tps"))
    except RconError:
        pass
    s["whitelisted"] = len(_read_json("whitelist.json"))
    s["world"] = _dir_size(os.path.join(CFG["data"], _live_props().get("level-name", "world")))
    cpu, mem = CPU.percent(), procstats.memory()
    if cpu is not None:
        s["cpu"] = round(cpu, 1)
    if mem.get("rss") is not None:
        s["rss"] = mem["rss"]
    return s


def sampler():
    while True:
        try:
            HISTORY.add(take_sample())
        except Exception as ex:  # never let one bad sample stop the graphs
            print(f"history: sample failed: {ex}", flush=True)
        time.sleep(CFG["sample_seconds"])


def _points(key, live):
    pts = HISTORY.series(key, SPARK_POINTS - (live is not None))
    return pts + [(time.time(), live)] if live is not None else pts


def spark(key, live, **kw):
    """Graph of the last stored samples plus the live value, so it ends at the shown number."""
    return history.sparkline(_points(key, live), **kw)


def span_label(key, live):
    """'last 20 min' for the time the graph actually covers ('' if no graph)."""
    pts = [pt for pt in _points(key, live) if pt[1] is not None]
    if len(pts) < 2:
        return ""
    mins = round((pts[-1][0] - pts[0][0]) / 60)
    return f"last {mins} min" if mins < 120 else f"last {round(mins / 60)} h"


# --- rendering ----------------------------------------------------------------

JS = """
document.querySelectorAll('form.track').forEach(form=>{const btn=form.querySelector('button.go');
 function sync(){let n=0;form.querySelectorAll('[data-orig]').forEach(el=>{
  const ch=el.value.trim()!==el.dataset.orig;el.closest('tr').classList.toggle('changed',ch);if(ch)n++;});
  btn.disabled=!n;btn.textContent=n?btn.dataset.label.replace('N',n):btn.dataset.idle;}
 form.addEventListener('input',sync);sync();});
const q=document.getElementById('q');if(q)q.addEventListener('input',ev=>{const v=ev.target.value.toLowerCase();
 document.querySelectorAll('tr[data-k]').forEach(tr=>{tr.hidden=v&&!tr.dataset.k.includes(v)});
 document.querySelectorAll('section.card[data-g]').forEach(s=>{s.hidden=![...s.querySelectorAll('tr[data-k]')].some(t=>!t.hidden)})});
document.querySelectorAll('form[data-confirm]').forEach(f=>f.addEventListener('submit',ev=>{
 if(!confirm(f.dataset.confirm))ev.preventDefault()}));
const term=document.querySelector('.term');if(term)term.scrollTop=term.scrollHeight;
if(document.body.dataset.refresh)setTimeout(()=>location.reload(),+document.body.dataset.refresh*1000);
if(!/^(localhost|127\\.0\\.0\\.1|\\[::1\\])$/.test(location.hostname))document.querySelectorAll('a[href^="http://localhost:"]')
 .forEach(a=>{const u=new URL(a.href);u.hostname=location.hostname;a.href=u});
"""

# Defence in depth behind the escaping: even injected markup could run no script
# but the panel's own (pinned by hash), load nothing, and post nowhere else.
CSP = ("default-src 'none'; img-src 'self' data:; style-src 'unsafe-inline'; "
       f"script-src 'sha256-{base64.b64encode(hashlib.sha256(JS.encode()).digest()).decode()}'; "
       "form-action 'self'; frame-ancestors 'none'; base-uri 'none'")

def settings_mode():
    """"git" when the panel can open PRs, otherwise "file".

    "file", the default: the panel writes server.properties directly. That only
    sticks for keys the compose leaves unset, because the image rewrites every
    key it has a variable for on each start; the base compose sets none of the
    editable ones. "git": changes become PRs against the compose, for setups
    where git is the source of truth (opt-in, needs a fork and a token).
    """
    return "git" if CFG["token"] and CFG["repo"] else "file"


_LOGIN = {}


def login():
    """The auth.Login for the current CFG (rebuilt only when CFG's login changes)."""
    key = (CFG["user"], CFG["password_hash"], CFG["basic_auth"])
    if key not in _LOGIN:
        _LOGIN.clear()
        _LOGIN[key] = auth.Login(*key)
    return _LOGIN[key]


def auth_configured():
    """True when the panel asks for a login."""
    return login().configured()


PLAINTEXT_WARNING = (
    "The panel password is stored in plain text (KPANEL_BASIC_AUTH). Run "
    "<code>docker compose run --rm --build hashpw</code>, put the line it prints "
    "into .env in place of KPANEL_BASIC_AUTH, and run <code>docker compose up -d</code>. "
    "kPanel 0.4 will refuse to start with KPANEL_BASIC_AUTH.")


def require_auth_boundary(basic_auth, allow_no_auth):
    """Refuse to start a panel that anyone who can reach it can control.

    The panel has RCON, can op and ban players, read the world and the logs.
    Behind a tailnet that is fine and the sidecar is the boundary; published on
    a public port it is not. One of the two has to be a deliberate choice.
    """
    if basic_auth or allow_no_auth:
        return
    raise SystemExit(
        "kPanel refuses to start without an access boundary.\n"
        "  Set KPANEL_PASSWORD_HASH (from `docker compose run --rm --build hashpw`)\n"
        "  to require a login, or\n"
        "  set KPANEL_ALLOW_NO_AUTH=1 if the panel is only reachable over a\n"
        "  tailnet or VPN and that network is the boundary.")


PEER_TTL = 10  # seconds a resolved service address is trusted; containers get new IPs on restart
_peer_cache = {}  # names -> (expiry, frozenset of addresses)
_peer_lock = threading.Lock()


def resolve_peers(names: str) -> frozenset:
    """The addresses compose's DNS gives these service names now; unknown names add none."""
    now = time.monotonic()
    with _peer_lock:
        hit = _peer_cache.get(names)
    if hit and hit[0] > now:
        return hit[1]
    found = set()
    for name in (n.strip() for n in names.split(",")):
        if not name:
            continue
        try:
            found |= {ai[4][0] for ai in socket.getaddrinfo(name, None, type=socket.SOCK_STREAM)}
        except OSError:
            pass  # not running right now (or no such service): nothing to match
    with _peer_lock:
        _peer_cache[names] = (now + PEER_TTL, frozenset(found))
    return frozenset(found)


def peer_allowed(addr, refuse=None, only=None, resolve=resolve_peers):
    """May a request from this TCP peer be answered?

    Every container in the stack can reach kpanel:8080 directly, with any Host
    header it likes, so without a login the host check doesn't stop them: a
    plugin in mc could op itself or open pull requests with the panel's token.
    The published port's peer is the bridge gateway, the real client, or
    something else again depending on the engine and its proxy settings, so it
    can't be allowlisted in general. What can be named are the siblings:
    KPANEL_REFUSE_PEERS (the base file: mc, mc-backup, filebrowser). Behind
    tailscale.yml nothing is published and the sidecar is the only way in, so
    KPANEL_ONLY_PEERS names just it. The panel's own container (loopback: the
    healthcheck) is always answered.
    """
    refuse = CFG["refuse_peers"] if refuse is None else refuse
    only = CFG["only_peers"] if only is None else only
    if addr in ("127.0.0.1", "::1"):
        return True
    if only:
        return addr in resolve(only)
    return not (refuse and addr in resolve(refuse))


LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "[::1]"}
# Never in a Host header a browser sends: userinfo ('localhost:8080@evil.example'
# read as 'localhost'), a path, whitespace or control characters.
MALFORMED_HOST = re.compile(r"[@/\\\s\x00-\x1f\x7f]")


def host_malformed(value):
    return bool(value) and bool(MALFORMED_HOST.search(value.strip()))


def host_name(value):
    """The host part of a Host header or netloc, normalised: 'Example.COM.:8080' -> 'example.com'.

    IPv6 keeps its brackets ('[::1]:8080' -> '[::1]'). '' for anything unusable,
    including a value no browser would send (see MALFORMED_HOST).
    """
    if host_malformed(value):
        return ""
    v = (value or "").strip().lower()
    if v.startswith("["):
        end = v.find("]")
        return v[:end + 1] if end > 0 else ""
    if v.count(":") > 1:  # a bare IPv6 address, e.g. from an allowlist entry
        return f"[{v}]"
    return v.partition(":")[0].rstrip(".")


def host_allowed(host_header, allowed=None, login=None):
    """May a request that names this Host be answered?

    DNS rebinding: a page on attacker.example re-points its own name at
    127.0.0.1, and the browser then treats the panel as part of the attacker's
    site, same-origin, so Sec-Fetch-Site doesn't help. The Host header still
    says attacker.example, and that is what this rejects.

    Without a login the panel answers only to localhost and the names in
    KPANEL_ALLOWED_HOSTS. With a login, a rebinding page gets nothing anyway
    (the browser keeps credentials per origin), so any name is fine unless
    KPANEL_ALLOWED_HOSTS narrows it.
    """
    if host_malformed(host_header):
        return False  # in every mode: no browser sends it, so only a tool does
    allowed = CFG["allowed_hosts"] if allowed is None else allowed
    login = auth_configured() if login is None else login
    extra = {host_name(h) if h.strip() != "*" else "*" for h in allowed.split(",") if h.strip()}
    if "*" in extra or (login and not extra):
        return True
    name = host_name(host_header)
    return bool(name) and (name in LOOPBACK_HOSTS or name in extra)


def same_origin(headers):
    """CSRF: a state-changing request must come from one of the panel's own pages.

    Browsers send Sec-Fetch-Site on every request; one that doesn't (Safari
    before 16.4, old webviews) is judged by Origin, then Referer. A request with
    none of the three is refused: no browser page sends that, and a cross-site
    form from one of those browsers is exactly what it would look like.
    """
    origin = headers.get("Origin")
    if origin and origin != "null" and urlsplit(origin).netloc.lower() != (headers.get("Host") or "").lower():
        return False
    site = headers.get("Sec-Fetch-Site")
    if site is not None:
        return site in ("same-origin", "none")
    source = origin or headers.get("Referer") or ""
    return bool(source) and source != "null" and \
        urlsplit(source).netloc.lower() == (headers.get("Host") or "").lower()


ALL_NAV = [("/", "Dashboard"), ("/settings", "Settings"), ("/players", "Players"),
       ("/gamerules", "Gamerules"), ("/logs", "Logs")]


def head(active, filter_placeholder="", refresh=0):
    links = "".join(f'<a href="{h}"{" class=on" if h == active else ""}>{t}</a>' for h, t in ALL_NAV)
    files = (f'<a href="{e(CFG["files_url"])}">Files {ui.icon("external-link")}</a>'
             if CFG["files_url"] else "")
    q = (f'<input id="q" type=search placeholder="{e(filter_placeholder)}" aria-label="Filter" autocomplete="off">'
         if filter_placeholder else "")
    r = f' data-refresh="{refresh}"' if refresh else ""
    return (f"<!doctype html><html lang=en><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'>"
            f"<title>kPanel</title><link rel=icon type=image/png href=/favicon.png>"
            f"<style>{ui.CSS}</style></head><body{r}><header><div class=bar-in><h1>"
            f"{'<img class=logo src=/favicon.png alt=>' if FAVICON_PNG else ''}kPanel</h1>"
            f"<nav>{links}{files}</nav>{q}</div></header><main>"
            + (f'<div class="card warn note" role=alert>{ui.icon("alert-triangle")}<div>{PLAINTEXT_WARNING}</div></div>'
               if login().deprecated else ""))


COFFEE_URL = "https://buymeacoffee.com/kruser1337"


def about():
    """kPanel's own line: version, licence, author, and a newer release if there is one."""
    state, tag = release.status()
    gh = f"https://github.com/{release.REPO}"
    upd = ""
    if state == "update":
        upd = (f' · <a class=update href="{e(release.release_url(tag))}">{ui.icon("circle-arrow-up")}'
               f'{e(tag)} available</a>')
    elif state == "current":
        upd = " · up to date"
    return (f'<div class=about><a href="{gh}">kPanel</a> v{e(release.VERSION)}{upd} · '
            f'<a href="{gh}/blob/main/LICENSE">MIT License</a> · '
            f'made by <a href="https://github.com/kruser1337">kruser1337</a> · '
            f'<a class=coffee href="{COFFEE_URL}">{ui.icon("coffee")}Buy me a coffee</a></div>')


def foot(credit=""):
    return (f"</main><footer>{f'<div>{credit}</div>' if credit else ''}{about()}</footer>"
            f"<script>{JS}</script></body></html>")


WIKI_CREDIT = 'Hover a name for its description · <a href="https://minecraft.wiki">Minecraft Wiki</a>, CC BY-NC-SA 3.0'


def banners(msg, err):
    return ((f'<div class="card note">{ui.icon("info-circle")}<div>{msg}</div></div>' if msg else "") +
            (f'<div class="card err note" role=alert>{ui.icon("alert-triangle")}'
             f'<div><b>Error:</b> {e(err)}</div></div>' if err else ""))


def action_log():
    if not ACTIONS:
        return ""
    rows = "".join(f"<tr><td class=muted>{e(t[11:])}</td><td><code>{e(a)}</code></td>"
                   f"<td class=muted title='{e(w)}'>{e(r)}</td></tr>" for t, w, a, r in ACTIONS)
    return f'<div class=card><h2>Recent live changes</h2><table>{rows}</table></div>'


def tile(label, value, sub="", graph="", href="", tone=""):
    """One stat. tone "warn"/"bad" adds an icon and colours the sub line, which
    then has to say what is wrong: colour is never the only signal."""
    mark = ui.icon("alert-triangle") if tone else ""
    body = (f'<div class=l>{e(label)}{ui.icon("external-link") if href else ""}</div>'
            f'<div class=v>{mark}{e(value)}</div>'
            + (f'<div class=s>{e(sub)}</div>' if sub else "") + graph)
    cls = f"tile is-{tone}" if tone else "tile"
    if href:
        return f'<a class="{cls} link" href="{e(href)}">{body}</a>'
    return f'<div class="{cls}">{body}</div>'


def tps_tone(tps):
    """Paper runs at 20 ticks a second; below 18 players notice, below 15 it lags."""
    if tps is None or tps >= 18:
        return "", "target 20"
    return ("bad", "lagging, target 20") if tps < 15 else ("warn", "below target 20")


BACKUP_STALE = 26 * 3600  # backups run every 12 h; past a day, two were missed


def dashboard_page(s, err=""):
    out = [head("/", refresh=30), banners("", err)]
    p = s["ping"]
    icon = (p or {}).get("favicon") or PAPER_ICON
    px = " px" if (p or {}).get("favicon") else ""  # real 64x64 server icons are pixel art
    img = (f'<img class="icon{px}" src="{e(icon)}" alt="" title="'
           f'{"server icon" if (p or {}).get("favicon") else "Paper (no server-icon.png set)"}">') if icon else ""
    # Identity: what a player types to connect, and what they connect to.
    addr = f'{CFG["public_host"]}' + ("" if CFG["public_port"] == 25565 else f':{CFG["public_port"]}')
    ip = f' <span>({e(s["ip"])})</span>' if s["ip"] else ""
    meta = (f'<div class=meta><span title="Address players connect to">Address <code>{e(addr)}</code>{ip}</span>'
            f'<span>Version <code>{e(p["version"]) if p else "—"}</code></span></div>')
    if p:
        status = '<span class="status up" title="answers the server-list ping">Online</span>'
        motd = f'<div class=motd><span class=tag>MOTD</span> {e(p["motd"])}</div>' if p["motd"] else ""
        pgraph = spark("players", p["online"], lo=0, hi=max(p["max"], 1), width=200, height=48)
        if pgraph:  # say what it is: it sits far from the number it belongs to
            pgraph += f'<div class=sparkcap>players · {span_label("players", p["online"])}</div>'
        who = ('<div class=who aria-label="online now">' + "".join(f"<code>{e(n)}</code>" for n in s["players"])
               + "</div>") if s["players"] else ""
        out.append(f'<section class="card herocard">{img}<div class=herotext><div class=hero><span class=num>{p["online"]}</span>'
                   f'<span class=of>/ {p["max"]} players</span>{status}</div>{motd}{meta}{who}</div>'
                   f'<div class=herospark>{restart_form()}{pgraph}</div></section>')
    else:
        out.append(f'<section class="card herocard">{img}<div class=herotext><div class=hero><span class="status down" '
                   f'title="{e(s.get("ping_error", ""))}">Offline</span></div>{meta}</div></section>')
    lb = s["last_backup"]
    tps = s["tps"]
    cpu, mem = s.get("cpu"), s.get("mem") or {}
    tone, tps_sub = tps_tone(tps)
    stale = not lb or time.time() - lb[0] > BACKUP_STALE
    health = [
        tile("TPS", f"{tps:.1f}" if tps is not None else "—", tps_sub,
             spark("tps", tps, lo=0, hi=20, fmt=lambda v: f"{v:.1f}"), tone=tone),
        tile("Latency", f'{p["ms"]} ms' if p else "—", "panel → server",
             spark("ms", p["ms"] if p else None, lo=0, fmt=lambda v: f"{v} ms")),
        tile("CPU", f"{cpu:.0f}%" if cpu is not None else "—", f"of {procstats.NCPU} cores",
             spark("cpu", round(cpu, 1) if cpu is not None else None, lo=0, hi=100, fmt=lambda v: f"{v:.0f}%")),
        tile("Memory", _human_bytes(mem["rss"]) if mem.get("rss") else "—",
             f'host {_human_bytes(mem["available"])} free of {_human_bytes(mem["total"])}'
             if mem.get("total") and mem.get("available") is not None else "",
             spark("rss", mem.get("rss"), lo=0, hi=mem.get("total"), fmt=_human_bytes)),
    ]
    world = [
        tile("Difficulty", (s["difficulty"] or "—").capitalize(), (s["gamemode"] or "").capitalize()),
        tile("Whitelisted", str(s["whitelisted"]), f'{s["ops"]} op' + ("s" if s["ops"] != 1 else ""),
             spark("whitelisted", s["whitelisted"], lo=0)),
        tile("World size", _human_bytes(s["world"]), "", spark("world", s["world"], lo=0, fmt=_human_bytes)),
        tile("Last backup", _ago(lb[0]) if lb else "none",
             (f'{_human_bytes(lb[1])} · {s["backups"]} kept' + (" · overdue" if stale else "")) if lb
             else "no backup yet",
             href=(CFG["files_url"].rstrip("/") + "/files/backups/") if CFG["files_url"] else "",
             tone="warn" if stale else ""),
    ]
    out.append(f'<section class=group><h2>Health</h2><div class=tiles>{"".join(health)}</div></section>')
    out.append(f'<section class=group><h2>World</h2><div class=tiles>{"".join(world)}</div></section>')
    return "".join(out) + foot()


def field(r):
    p, val, key = r["p"], r["value"] or "", r["p"].key
    name, orig = f"p:{key}", e(st.normalize(p, val) if p.kind in ("bool", "enum") and p.strict else val)
    if p.locked or r["source"] == "coolify":
        why = p.locked or "Set from a variable in the compose file; change the variable (in .env, or your host's settings)."
        return (f'<div class=row><input value="{e(val)}" disabled>'
                f'<span class=lock title="{e(why)}">{ui.icon("lock", "locked")}</span></div>')
    if p.kind == "bool" or (p.kind == "enum" and p.strict):
        opts = [("true", ""), ("false", "")] if p.kind == "bool" else list(p.options)
        cur = st.normalize(p, val)
        if cur not in [o[0] for o in opts]:
            opts.insert(0, (cur, "current value"))
        o = "".join(f'<option value="{e(v)}" title="{e(d)}"{" selected" if v == cur else ""}>'
                    f'{e(v)}{" (default)" if st.normalize(p, p.default) == v else ""}</option>' for v, d in opts)
        return f'<select name="{e(name)}" data-orig="{orig}">{o}</select>'
    attrs = ""
    if p.kind == "int":
        attrs = ' type="number" step="1"' + (f' min="{p.min}"' if p.min is not None else "") + \
                (f' max="{p.max}"' if p.max is not None else "")
    dl = ""
    if p.options:
        dl = f'<datalist id="dl-{e(key)}">' + "".join(
            f'<option value="{e(v)}">{e(d[:80])}</option>' for v, d in p.options) + "</datalist>"
        attrs += f' list="dl-{e(key)}"'
    ph = f' placeholder="{e(p.default)}"' if p.default and p.default.lower() not in UNSET_DEFAULTS else ""
    return f'<input name="{e(name)}" value="{e(val)}" data-orig="{orig}"{attrs}{ph}>{dl}'


def _tip(p):
    return " ".join(x for x in (p.desc, p.note) if x)


def page(rows, prs, msg="", err=""):
    out = [head("/settings", "Filter…"), banners(msg, err)]
    if prs:
        out.append('<div class="card warn"><h2>Open PRs</h2>' + "".join(
            f'<div><a href="{e(u)}">{f"#{n}" if n else "new"}</a> {e(t)}</div>' for n, t, u in prs) + "</div>")

    imports = [k for k, r in rows.items() if not r["p"].locked and (r["live_only"] or r["drift"])]
    if imports:
        out.append('<form method=post action="/import" class="card warn">'
                   '<h2 title="Live value differs from git (or from the default, with nothing in git). '
                   'Tick to pin the live value in git via a PR.">Changed on the server</h2><table>')
        for k in imports:
            r = rows[k]
            out.append(f'<tr><td class=k><label><input type=checkbox name="i:{e(k)}" value="{e(r["live"])}" '
                       f'> <code>{e(k)}</code></label></td>'
                       f'<td><code>{e(r["live"])}</code> <span class=muted>live</span></td>'
                       f'<td><code>{e(r["git"]) if r["git"] is not None else "—"}</code> <span class=muted>git</span></td></tr>')
        out.append('</table><p><button>Pin ticked in git</button></p></form>')

    out.append('<form method=post action="/save" class=track>')
    for group, keys in st.grouped(list(rows)):
        out.append(f'<section class=card data-g="{e(group)}"><h2>{e(group)}</h2><table>')
        for k in keys:
            r, p = rows[k], rows[k]["p"]
            badge = (f'<span class="badge drift" title="live: {e(r["live"])}">differs</span>' if r["drift"] else "")
            out.append(f'<tr data-k="{e(k)} {e(p.desc.lower()[:200])}"><td class=k>'
                       f'<code title="{e(_tip(p))}">{e(k)}</code>{badge}</td><td>{field(r)}</td></tr>')
        out.append("</table></section>")
    if settings_mode() == "git":
        go = ('data-label="Open PR (N)" data-idle="No changes" '
              'title="Merging the PR redeploys and restarts the server"')
    else:
        go = 'data-label="Save (N)" data-idle="No changes" title="Takes effect when the server restarts"'
    out.append(f'<div class=bar><button class=go {go} disabled>No changes</button></div></form>')
    return "".join(out) + foot(WIKI_CREDIT)


RESTART_CONFIRM = "Restart the server? Everyone online is disconnected for about a minute."


def restart_form(label="Restart server"):
    return (f'<form method=post action="/restart" style="display:inline" data-confirm="{e(RESTART_CONFIRM)}">'
            f'<button class=sec title="Saves the world, stops the server, and starts it again">'
            f'{e(label)}</button></form>')


def restarting_page():
    # The panel restarts with the server, so the reload has to wait for both.
    out = [head("/", refresh=60), banners(
        "Restarting. The server saves the world and starts again, which takes about a minute; "
        "the panel restarts with it and this page reloads itself.", "")]
    return "".join(out) + foot()


# --- players (live) -------------------------------------------------------------

def players_state():
    whitelist = sorted((p.get("name", "?") for p in _read_json("whitelist.json")), key=str.lower)
    ops = {p.get("name"): p.get("level") for p in _read_json("ops.json")}
    banned = sorted((p.get("name", "?") for p in _read_json("banned-players.json")), key=str.lower)
    online, maxp = rcon.parse_list(client().command("list"))
    return whitelist, ops, banned, online, maxp


def _btn(action, name, label, cls="sec", confirm=""):
    c = f' data-confirm="{e(confirm)}"' if confirm else ""
    return (f'<form method=post action="/players" style="display:inline"{c}>'
            f'<input type=hidden name=action value="{action}"><input type=hidden name=name value="{e(name)}">'
            f'<button class="{cls}">{label}</button></form>')


def players_page(state, msg="", err=""):
    out = [head("/players"), banners(msg, err)]
    if state is None:
        return "".join(out) + foot()
    whitelist, ops, banned, online, maxp = state
    out.append('<div class=card><form method=post action="/players" class=row>'
               '<input type=hidden name=action value=add>'
               '<input name=name placeholder="Minecraft username" pattern="[A-Za-z0-9_]{3,16}" required '
               'autocomplete=off><button>Add to whitelist</button></form></div>')
    out.append(f'<div class=card><h2>Whitelist <span class=pill>{len(whitelist)}</span> · Online '
               f'<span class=pill>{len(online)}/{maxp if maxp is not None else "?"}</span></h2><table>')
    if not whitelist:
        out.append('<tr><td class=muted>Empty. Nobody can join.</td></tr>')
    lower_online = {n.lower() for n in online}
    for name in whitelist:
        level = ops.get(name)
        is_on = name.lower() in lower_online
        badges = (f' <span class=pill title="operator, level {level}">op</span>' if level else "") + \
                 (' <span class="status up">online</span>' if is_on else "")
        acts = (_btn("deop", name, "Remove op") if level else
                _btn("op", name, "Make op", confirm=f"Give {name} operator rights?"))
        if is_on:
            acts += " " + _btn("kick", name, "Kick")
        acts += " " + _btn("remove", name, "Remove", "sec danger", confirm=f"Remove {name} from the whitelist?")
        out.append(f"<tr><td class=k><code>{e(name)}</code>{badges}</td><td class=act>{acts}</td></tr>")
    for name in (n for n in online if n not in whitelist):
        out.append(f"<tr><td class=k><code>{e(name)}</code> <span class=muted>online, not whitelisted</span>"
                   f"</td><td class=act>{_btn('kick', name, 'Kick')}</td></tr>")
    out.append("</table></div>")
    # A banned player is on no other list and is not online, so there is no row
    # to hang a button on: the name has to be typed, as for the whitelist.
    out.append(f'<div class=card><h2>Banned <span class=pill>{len(banned)}</span></h2>'
               '<form method=post action="/players" class=row data-confirm="Ban this player?">'
               '<input type=hidden name=action value=ban>'
               '<input name=name placeholder="Minecraft username" pattern="[A-Za-z0-9_]{3,16}" required '
               'autocomplete=off><button class="sec danger">Ban</button></form><table>')
    if not banned:
        out.append('<tr><td class=muted>Nobody is banned.</td></tr>')
    for name in banned:
        out.append(f"<tr><td class=k><code>{e(name)}</code></td><td class=act>"
                   f"{_btn('pardon', name, 'Pardon', confirm=f'Let {name} connect again?')}</td></tr>")
    out.append("</table></div>")
    out.append(action_log())
    return "".join(out) + foot()


PLAYER_ACTIONS = {
    "add": "whitelist add {}", "remove": "whitelist remove {}",
    "op": "op {}", "deop": "deop {}", "kick": "kick {}",
    # The whitelist already turns strangers away, so a ban is a backstop: it
    # refuses them before the whitelist check and leaves a dated record in
    # banned-players.json. /ban disconnects the player itself, so unlike
    # "remove" it needs no follow-up kick.
    "ban": "ban {}", "pardon": "pardon {}",
}


def do_player(form, who):
    action, name = form.get("action", ""), form.get("name", "").strip()
    if action not in PLAYER_ACTIONS:
        raise ValueError("unknown action")
    if not rcon.valid_name(name):
        raise ValueError("A Minecraft username is 3–16 letters, digits or underscores.")
    cmds = [PLAYER_ACTIONS[action].format(name)]
    if action == "remove":
        cmds.append(f"kick {name} You are no longer whitelisted")  # ENFORCE would, but not instantly
    replies = client().run(*cmds)
    record(who, cmds[0], replies[0])
    return replies[0]


# --- gamerules (live) -----------------------------------------------------------

def gamerules_page(values, msg="", err=""):
    out = [head("/gamerules", "Filter…"), banners(msg, err)]
    if values is None:
        return "".join(out) + foot()
    out.append('<form method=post action="/gamerules" class=track>')
    for cat, names in gr.grouped(list(values)):
        out.append(f'<section class=card data-g="{e(cat)}"><h2>{e(cat)}</h2><table>')
        for n in names:
            rule, v = gr.RULES[n], values[n]
            badge = "" if v == rule.default else f'<span class=badge title="default: {e(rule.default)}">changed</span>'
            if rule.kind == "bool":
                inp = (f'<select name="g:{e(n)}" data-orig="{e(v)}">' + "".join(
                    f'<option value="{o}"{" selected" if o == v else ""}>{o}{" (default)" if o == rule.default else ""}</option>'
                    for o in ("true", "false")) + "</select>")
            else:
                inp = (f'<input name="g:{e(n)}" value="{e(v)}" data-orig="{e(v)}" type=number step=1 '
                       f'min="{rule.min}" max="{rule.max}" placeholder="{e(rule.default)}">')
            out.append(f'<tr data-k="{e(n)} {e(rule.desc.lower()[:200])}"><td class=k>'
                       f'<code title="{e(rule.desc)}">{e(n)}</code>{badge}</td><td>{inp}</td></tr>')
        out.append("</table></section>")
    out.append('<div class=bar><button class=go data-label="Apply now (N)" data-idle="No changes" '
               'title="Applies immediately over RCON. No PR, no restart" disabled>No changes</button></div></form>')
    out.append(action_log())
    return "".join(out) + foot(WIKI_CREDIT)


def do_gamerules(form, who):
    c = client()
    values = gr.current(c)
    wanted, errors = {}, []
    for field_name, raw in form.items():
        if not field_name.startswith("g:"):
            continue
        n = field_name[2:]
        if n not in values:  # only rules the server itself confirmed
            errors.append(f"unknown gamerule {n}")
            continue
        try:
            v = gr.validate(gr.RULES[n], raw)
        except ValueError as ex:
            errors.append(str(ex))
            continue
        if v != values[n]:
            wanted[n] = v
    if errors:
        raise ValueError("; ".join(errors))
    if not wanted:
        return values, "Nothing changed."
    replies = c.run(*(f"gamerule {n} {v}" for n, v in sorted(wanted.items())))
    for (n, v), r in zip(sorted(wanted.items()), replies):
        record(who, f"gamerule {n} {v}", r)
    after = gr.current(c)
    failed = [n for n, v in wanted.items() if after.get(n) != v]
    if failed:
        raise ValueError(f"The server did not take: {', '.join(failed)}")
    return after, "Applied: " + ", ".join(f"<code>{e(n)}</code> = {e(v)}" for n, v in sorted(wanted.items()))


# --- logs (read-only) -----------------------------------------------------------

LOG_TAIL = 300
# The panel polls RCON every few minutes and each poll leaves a started/shutting
# down pair behind, which buries everything else. Matches the thread name only:
# "System chat: [Rcon: ...]" lines are commands someone actually ran, and those
# must survive the filter.
RCON_NOISE = re.compile(r"\[RCON (?:Client|Listener)")


def read_log(hide_rcon=True, limit=LOG_TAIL):
    """The newest lines of the server's own log, or None if there is no log yet.

    The name is fixed, so there is no path for a caller to traverse out of.
    """
    try:
        with open(os.path.join(CFG["data"], "logs", "latest.log"),
                  encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except (FileNotFoundError, PermissionError, NotADirectoryError):
        return None
    if hide_rcon:
        lines = [ln for ln in lines if not RCON_NOISE.search(ln)]
    # Filter first, then tail: 300 lines of polling noise would leave almost no
    # real events on the page.
    return lines[-limit:]


def logs_page(lines, hide_rcon):
    out = [head("/logs", "Filter…", refresh=30)]
    toggle = ('<a href="/logs?rcon=1">Show RCON chatter</a>' if hide_rcon
              else '<a href="/logs">Hide RCON chatter</a>')
    out.append(f'<div class=card><h2>Server log <span class=pill>{len(lines or [])}</span> '
               f'<span class=muted>{toggle}</span></h2><div class=term><table>')
    if not lines:
        out.append('<tr><td class=muted>No log file yet.</td></tr>')
    for ln in lines or []:
        cls = " class=warn" if "/WARN]" in ln else (" class=err" if "/ERROR]" in ln else "")
        out.append(f'<tr data-k="{e(ln.lower()[:200])}"><td><code{cls}>{e(ln)}</code></td></tr>')
    out.append("</table></div></div>")
    return "".join(out) + foot()


# --- HTTP ---------------------------------------------------------------------

MAX_FORM = 200_000  # bytes; the Settings form is about 10 KB


class Handler(BaseHTTPRequestHandler):
    server_version = "kPanel"
    sys_version = ""  # no "Python/3.13.x" in the Server header (N-09)

    def version_string(self):
        return self.server_version
    # Seconds a client may stall mid-request before its connection is dropped,
    # so slow or never-finished requests can't pin worker threads.
    timeout = 30

    def _send(self, code, body, ctype="text/html; charset=utf-8", cache=False):
        b = body if isinstance(body, bytes) else body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(b)))
        self.send_header("Cache-Control", "public, max-age=86400" if cache else "no-store")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", CSP)
        self.end_headers()
        self.wfile.write(b)

    def _challenge(self):
        b = b"authentication required"
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="kPanel"')
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _misdirected(self):
        """Refuse a request for a host name the panel doesn't answer to (see host_allowed)."""
        name = host_name(self.headers.get("Host"))[:100]
        name = "".join(c for c in name if c.isprintable()) or "(none)"
        print(f"refused request for host {name!r}: not localhost or in KPANEL_ALLOWED_HOSTS", flush=True)
        return self._send(403, (
            f"kPanel does not answer to the host name {name!r}.\n\n"
            "This protects a panel without a login from DNS rebinding: a web page\n"
            "pointing its own name at this machine to control the panel.\n\n"
            "If you opened the panel by this name on purpose, add it to\n"
            "KPANEL_ALLOWED_HOSTS (comma-separated) in .env and restart:\n\n"
            f"  KPANEL_ALLOWED_HOSTS={name}\n"), "text/plain; charset=utf-8")

    def _gate(self):
        """True if the request may go on; otherwise the refusal has been sent.

        The peer first (other containers of the stack), then the Host (DNS
        rebinding), then the login, if there is one.
        """
        if not peer_allowed(self.client_address[0]):
            print(f"refused request from {self.client_address[0]}: another container of this stack "
                  "(KPANEL_REFUSE_PEERS / KPANEL_ONLY_PEERS)", flush=True)
            self._send(403, "kPanel does not answer other containers of its stack.\n", "text/plain; charset=utf-8")
            return False
        if not host_allowed(self.headers.get("Host")):
            self._misdirected()
            return False
        lg = login()
        if not lg.configured():
            return True
        ok = lg.check(self.headers.get("Authorization", ""), self.client_address[0])
        if ok is None:
            wait = lg.retry_after(self.client_address[0])
            b = f"too many failed logins; try again in {wait} s".encode()
            self.send_response(429)
            self.send_header("Retry-After", str(wait))
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers()
            self.wfile.write(b)
            return False
        if not ok:
            self._challenge()
        return ok

    def _files(self, path):
        """True if this is a file-manager request, which has then been relayed."""
        if not (CFG["files_upstream"] and filesproxy.handles(path)):
            return False
        filesproxy.forward(self, CFG["files_upstream"])
        return True

    def _other_method(self):
        """PUT, PATCH, DELETE, HEAD: only the file manager uses them."""
        if not self._gate():
            return
        if self.command != "HEAD" and not same_origin(self.headers):
            return self._send(403, "cross-site request refused", "text/plain")
        if not self._files(self.path.split("?")[0]):
            self._send(405, "method not allowed", "text/plain")

    do_PUT = do_PATCH = do_DELETE = do_HEAD = _other_method

    def do_GET(self):
        path = self.path.split("?")[0]
        # Exempt: the container healthcheck has no credentials to offer, and it
        # reveals nothing.
        if path == "/healthz":
            return self._send(200, "ok", "text/plain")
        if not self._gate():
            return
        if self._files(path):
            return
        if path in ("/favicon.png", "/favicon.ico") and FAVICON_PNG:
            # /favicon.ico too: some browsers ask for it regardless of the <link>,
            # and every current one renders a PNG served from that path.
            return self._send(200, FAVICON_PNG, "image/png", cache=True)
        if path == "/":
            return self._send(200, dashboard_page(dashboard_state()))
        if path == "/logs":
            hide = parse_qs(self.path.partition("?")[2]).get("rcon", ["0"])[0] != "1"
            return self._send(200, logs_page(read_log(hide_rcon=hide), hide))
        if path == "/players":
            try:
                return self._send(200, players_page(players_state()))
            except RconError as ex:
                return self._send(502, players_page(None, err=str(ex)))
        if path == "/gamerules":
            try:
                return self._send(200, gamerules_page(gr.current(client())))
            except RconError as ex:
                return self._send(502, gamerules_page(None, err=str(ex)))
        if path != "/settings":
            return self._send(404, "not found", "text/plain")
        try:
            rows, prs = load()[4:] if settings_mode() == "git" else (load_file(), [])
        except (GitHubError, OSError, KeyError, yaml.YAMLError) as ex:
            return self._send(502, page({}, [], err=str(ex)))
        self._send(200, page(rows, prs))

    def do_POST(self):
        if not self._gate():
            return
        # Refuse cross-site posts, so another page open in your browser can't act
        # through this panel.
        if not same_origin(self.headers):
            return self._send(403, "cross-site request refused", "text/plain")
        if self._files(self.path.split("?")[0]):
            return
        if self.path not in ("/save", "/import", "/players", "/gamerules", "/restart"):
            return self._send(404, "not found", "text/plain")
        if self.path == "/import" and settings_mode() != "git":
            return self._send(404, "not found", "text/plain")
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = -1
        if n < 0:  # rfile.read(-1) would wait for the client to hang up
            return self._send(400, "bad Content-Length", "text/plain")
        if n > MAX_FORM:
            return self._send(413, "form too large", "text/plain")
        form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode("utf-8", "replace"),
                                              keep_blank_values=True).items()}
        who = who_from(self.headers)
        if self.path == "/players":
            try:
                reply = do_player(form, who)
                return self._send(200, players_page(players_state(), msg=f"Server: <code>{e(reply)}</code>"))
            except ValueError as ex:
                return self._send(400, players_page(_safe(players_state), err=str(ex)))
            except RconError as ex:
                return self._send(502, players_page(None, err=str(ex)))
        if self.path == "/restart":
            try:
                restart(who)
            except RconError as ex:
                return self._send(502, dashboard_page(dashboard_state(), err=f"Could not restart: {ex}"))
            return self._send(200, restarting_page())
        if self.path == "/gamerules":
            try:
                values, msg = do_gamerules(form, who)
                return self._send(200, gamerules_page(values, msg=msg))
            except ValueError as ex:
                return self._send(400, gamerules_page(_safe(lambda: gr.current(client())), err=str(ex)))
            except RconError as ex:
                return self._send(502, gamerules_page(None, err=str(ex)))
        git = settings_mode() == "git"
        try:
            gh, commit, text, blob, rows, prs = load() if git else (None, None, None, None, load_file(), [])
            wanted, errors = {}, []
            prefix = "p:" if self.path == "/save" else "i:"
            for name, raw in form.items():
                if not name.startswith(prefix):
                    continue
                key = name[2:]
                if key not in rows:
                    errors.append(f"unknown key {key}")
                    continue
                r = rows[key]
                if self.path == "/save":
                    # Only what the person actually changed from what they were shown.
                    if st.normalize(r["p"], raw) == st.normalize(r["p"], r["value"]):
                        continue
                try:
                    wanted[key] = st.validate(r["p"], raw)
                except ValueError as ex:
                    errors.append(str(ex))
            if errors:
                return self._send(400, page(rows, prs, err="; ".join(errors)))
            if not wanted:
                return self._send(200, page(rows, prs, msg="Nothing changed, so no PR was opened." if git
                                            else "Nothing changed."))
            if not git:
                save_file(wanted, who)
                names = ", ".join(f"<code>{e(k)}</code>" for k in sorted(wanted))
                return self._send(200, page(load_file(), [], msg=(
                    f"Saved {names}. Takes effect when the server restarts. {restart_form('Restart now')}")))
            url = make_pr(gh, commit, text, blob, rows, wanted, who,
                          "set" if self.path == "/save" else "import from server")
        except (GitHubError, ce.ComposeEditError, OSError, KeyError, ValueError, yaml.YAMLError) as ex:
            return self._send(502, page({}, [], err=str(ex)))
        self._send(200, page(rows, prs + [(0, "just opened", url)],
                             msg=f'PR opened: <a href="{e(url)}">{e(url)}</a>'))

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} {fmt % args}", flush=True)


PR_SET_DUMPABLE = 4


def make_undumpable(libc=None):
    """Defence in depth for the main process; not the boundary.

    kpanel shares mc's PID namespace (for the CPU and memory graphs). The
    boundary is the uid: the panel runs as 1001, the server and its plugins as
    1000, and the kernel lets no process read another uid's /proc/<pid>/environ,
    mem or root. That covers every process in this container, the healthcheck
    and `docker compose exec` included. Non-dumpable additionally makes this
    one's /proc entries root's. Linux only; returns whether it took.
    """
    try:
        if libc is None:
            import ctypes
            libc = ctypes.CDLL(None, use_errno=True)
        return libc.prctl(PR_SET_DUMPABLE, 0, 0, 0, 0) == 0
    except (OSError, AttributeError):
        return False


def shares_server_uid(props=None, uid=None):
    """True if the panel runs as the uid that owns the server's files.

    Then a plugin could read this container's processes (environment: the login
    hash, the GitHub token). The image runs as 1001, so this means someone set
    `user:` to the server's uid.
    """
    try:
        owner = os.stat(props or CFG["props"]).st_uid
    except OSError:
        return False
    return owner == (os.getuid() if uid is None else uid)


def _safe(fn):
    """Re-render after an error without letting a second failure hide the first."""
    try:
        return fn()
    except RconError:
        return None


if __name__ == "__main__":
    if not make_undumpable() and os.path.exists("/proc/self"):
        print("WARNING: could not make the panel non-dumpable", flush=True)
    if shares_server_uid():
        print(f"WARNING: the panel runs as uid {os.getuid()}, the owner of {CFG['props']}: code in the "
              "server could read the panel's environment. Run it as another uid in the server's "
              "group (the image's default is 1001:1000).", flush=True)
    try:
        login().validate()
    except auth.LoginError as ex:
        raise SystemExit(f"kPanel refuses to start: {ex}") from None
    require_auth_boundary(auth_configured(), os.environ.get("KPANEL_ALLOW_NO_AUTH", ""))
    if login().deprecated:
        print("WARNING: " + re.sub(r"</?code>", "`", PLAINTEXT_WARNING), flush=True)
    elif CFG["basic_auth"]:
        print("WARNING: KPANEL_BASIC_AUTH is ignored because KPANEL_PASSWORD_HASH is set; "
              "remove it from .env.", flush=True)
    port = int(os.environ.get("PORT", "8080"))
    print(f"kPanel on :{port}, settings {settings_mode()}, "
          f"auth {('basic, plaintext (deprecated)' if login().deprecated else 'basic, argon2id') if auth_configured() else 'none (network is the boundary)'}, "
          f"rcon {CFG['rcon_host']}:{CFG['rcon_port']} password {'set' if CFG['rcon_password'] else 'from server.properties'}",
          flush=True)
    threading.Thread(target=sampler, daemon=True, name="history-sampler").start()
    if CFG["update_check"]:
        release.start()
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
