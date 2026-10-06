"""End-to-end test of the HTTP app with a fake GitHub.

    cd kpanel && PROPS_FIXTURE=/path/to/server.properties python -m unittest test_app -v

Starts the real server on a free port. GitHub is replaced by an in-memory
fake that serves the repo's actual compose file and records the PR it would
have opened, so the full path (render, form post, validate, edit, verify, PR)
runs exactly as in production.
"""

import base64
import http.client
import json
import re
import os
import pathlib
import tempfile
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import yaml

import app
import compose_edit as ce
import settings as st

ROOT = pathlib.Path(__file__).parent.parent
# Git mode edits a fork of the base; there, panel PRs have already pinned some values.
COMPOSE = ce.apply_changes((ROOT / "docker-compose.yml").read_text(),
                           {"MOTD": "Hosted with kPanel", "DIFFICULTY": "normal", "MAX_PLAYERS": "8"})
# A sanitised server.properties ships with the tests, so a fresh clone can run
# them with no setup. PROPS_FIXTURE overrides it to test against a real file.
FIXTURE = os.environ.get("PROPS_FIXTURE") or str(pathlib.Path(__file__).parent / "testdata" / "server.properties")
# Read, don't assume: the panel itself changes these values over time.
DIFFICULTY = yaml.safe_load(COMPOSE)["services"]["mc"]["environment"]["DIFFICULTY"]
OTHER_DIFFICULTY = "easy" if DIFFICULTY != "easy" else "hard"


class FakeMinecraft:
    """Stands in for app.Rcon: answers like a 26.3 server and keeps state.

    Like the real server, whitelist and op changes are written to the JSON files
    in the data dir, which is where the Players page reads them from.
    """
    sent = []
    rules = {"pvp": "true", "keep_inventory": "false", "players_sleeping_percentage": "100"}
    online = []
    data = None

    def __init__(self, host, port, password):
        pass

    def _wl(self):
        return json.loads((FakeMinecraft.data / "whitelist.json").read_text())

    def _bans(self):
        f = FakeMinecraft.data / "banned-players.json"
        return json.loads(f.read_text()) if f.exists() else []

    def run(self, *cmds):
        out = []
        for c in cmds:
            FakeMinecraft.sent.append(c)
            p = c.split()
            if c == "list":
                out.append(f"There are {len(self.online)} of a max of 8 players online: {', '.join(self.online)}")
            elif c == "tps":
                out.append("TPS from last 1m, 5m, 15m: *20.0, 19.9, 20.0")
            elif p[0] == "gamerule" and len(p) == 2:
                out.append(f"Game rule {p[1]} is currently set to {self.rules[p[1]]}" if p[1] in self.rules
                           else "Incorrect argument for command")
            elif p[0] == "gamerule" and len(p) == 3:
                self.rules[p[1]] = p[2]
                out.append(f"Gamerule {p[1]} is now set to: {p[2]}")
            elif p[:2] == ["whitelist", "add"]:
                wl = self._wl()
                if p[2] == "Nobody404":
                    out.append("That player does not exist")
                    continue
                wl.append({"name": p[2], "uuid": "u-" + p[2]})
                (FakeMinecraft.data / "whitelist.json").write_text(json.dumps(wl))
                out.append(f"Added {p[2]} to the whitelist")
            elif p[:2] == ["whitelist", "remove"]:
                wl = [x for x in self._wl() if x["name"] != p[2]]
                (FakeMinecraft.data / "whitelist.json").write_text(json.dumps(wl))
                out.append(f"Removed {p[2]} from the whitelist")
            elif p[0] == "ban" and len(p) == 2:
                bans = self._bans()
                bans.append({"name": p[1], "uuid": "u-" + p[1], "reason": "Banned by an operator."})
                (FakeMinecraft.data / "banned-players.json").write_text(json.dumps(bans))
                out.append(f"Banned {p[1]}: Banned by an operator.")
            elif p[0] == "pardon" and len(p) == 2:
                bans = [x for x in self._bans() if x["name"] != p[1]]
                (FakeMinecraft.data / "banned-players.json").write_text(json.dumps(bans))
                out.append(f"Unbanned {p[1]}")
            else:
                out.append(f"ok: {c}")
        return out

    def command(self, c):
        return self.run(c)[0]


class FakeGitHub:
    opened = []

    def __init__(self, token, repo, base="main"):
        self.base = base

    def head(self):
        return "c0ffee"

    def read(self, path, ref):
        assert ref == "c0ffee", "must read the compose at the pinned commit"
        return COMPOSE, "blob1"

    def open_pr(self, **kw):
        FakeGitHub.opened.append(kw)
        return "https://github.com/x/y/pull/99"

    def open_panel_prs(self):
        return []


@unittest.skipUnless(FIXTURE, "set PROPS_FIXTURE to a server.properties copy")
class App(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        props = pathlib.Path(FIXTURE).read_text()
        # Simulate a hand edit in the file manager on a key git doesn't pin.
        props = props.replace("spawn-protection=16", "spawn-protection=0")
        cls.tmp = tempfile.NamedTemporaryFile("w", suffix=".properties", delete=False)
        cls.tmp.write(props)
        cls.tmp.close()
        cls.data = pathlib.Path(tempfile.mkdtemp())
        (cls.data / "whitelist.json").write_text(json.dumps([{"name": "sTEVE_", "uuid": "u1"}]))
        (cls.data / "ops.json").write_text(json.dumps([{"name": "sTEVE_", "uuid": "u1", "level": 4}]))
        FakeMinecraft.data = cls.data
        # repo as well as token: settings_mode() needs both, and the fixture used to
        # lean on a hardcoded default that no longer exists.
        app.CFG.update(token="t", repo="example/kpanel", props=cls.tmp.name,
                       files_url="https://files.example:8443/",
                       data=str(cls.data), rcon_password="pw", password_hash="")
        backups = cls.data / "backups"
        backups.mkdir()
        (backups / "world-20261001-120000.tar.gz").write_bytes(b"x" * 2048)
        app.CFG.update(backups=str(backups), public_host="localhost")
        logs = cls.data / "logs"
        logs.mkdir()
        noise = "\n".join(f"[12:{i:02d}:00] [RCON Listener #1/INFO]: Thread RCON Client /10.0.11.6 started"
                          for i in range(60))
        filler = "\n".join(f"[13:00:00] [Server thread/INFO]: filler line {i}" for i in range(400))
        (logs / "latest.log").write_text(
            "[11:00:00] [Server thread/INFO]: ANCIENT marker line\n"
            + filler + "\n" + noise + "\n"
            + "[14:00:00] [Server thread/WARN]: Can't keep up! Is the server overloaded?\n"
            + "[14:00:01] [Server thread/INFO]: System chat: [Rcon: Banned Griefer_42: Banned by an operator.]\n"
            + "[14:00:02] [Server thread/INFO]: sTEVE_ joined the game\n")
        (cls.data / "world").mkdir()
        (cls.data / "world" / "level.dat").write_bytes(b"y" * 4096)
        app.status_ping = lambda host, port, timeout=4.0: {
            "version": "Paper 26.3", "online": 1, "max": 8, "motd": "A Test Server", "ms": 3}
        app.GitHub = FakeGitHub
        app.Rcon = FakeMinecraft
        cls.srv = ThreadingHTTPServer(("127.0.0.1", 0), app.Handler)
        cls.url = f"http://127.0.0.1:{cls.srv.server_address[1]}"
        threading.Thread(target=cls.srv.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.srv.shutdown()
        os.unlink(cls.tmp.name)

    def setUp(self):
        FakeGitHub.opened.clear()
        FakeMinecraft.sent.clear()

    def get(self, path="/settings"):
        with urllib.request.urlopen(self.url + path) as r:
            return r.status, r.read().decode()

    def get_raw(self, path, headers=None):
        req = urllib.request.Request(self.url + path, headers=headers or {})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as ex:
            return ex.code, ex.read().decode()

    def post(self, path, data, headers=None):
        req = urllib.request.Request(self.url + path, data=urllib.parse.urlencode(data).encode(),
                                     headers={"Sec-Fetch-Site": "same-origin", **(headers or {})})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read().decode()
        except urllib.error.HTTPError as ex:
            return ex.code, ex.read().decode()

    def test_page_renders_every_key_and_hides_secrets(self):
        status, body = self.get()
        self.assertEqual(status, 200)
        for k in ("difficulty", "level-type", "debug", "region-file-compression", "op-permission-level"):
            self.assertRegex(body, rf"<code[^>]*>{re.escape(k)}</code>")
        for secret in ("rcon.password", "management-server-secret", "REDACTED"):
            self.assertNotIn(secret, body)
        self.assertIn("Hostile mobs deal less damage", body)       # difficulty explanations
        self.assertIn('aria-label="locked"', body)                  # locked keys shown as locked

    def test_hand_edit_shows_up_for_import(self):
        _, body = self.get()
        self.assertIn('name="i:spawn-protection" value="0"', body)

    def test_only_real_differences_are_offered_for_import(self):
        # No false alarms: live caught debug=false being offered because the wiki
        # has no default for it. Expected = the simulated hand edit, plus any key
        # where the fixture genuinely disagrees with git (the fixture is a snapshot
        # and git moves on, e.g. through panel PRs).
        import re
        import settings as st
        env = yaml.safe_load(COMPOSE)["services"]["mc"]["environment"]
        live = st.parse_properties(pathlib.Path(self.tmp.name).read_text())
        expected = {"spawn-protection"} | {
            k for k, p in st.PROPS.items()
            if not p.locked and p.env in env and "${" not in str(env[p.env]) and k in live
            and st.normalize(p, env[p.env]) != st.normalize(p, live[k])}
        _, body = self.get()
        self.assertEqual(set(re.findall(r'name="i:([^"]+)"', body)), expected)
        self.assertNotIn("debug", expected)

    def test_save_opens_pr_with_only_the_changed_keys(self):
        app.CFG["trust_ts_headers"] = True  # behind tailscale serve
        self.addCleanup(app.CFG.update, trust_ts_headers=False)
        status, body = self.post("/save", {
            "p:difficulty": OTHER_DIFFICULTY,  # changed
            "p:max-players": "8",            # unchanged: must not appear
            "p:debug": "true",               # no env var: CUSTOM_SERVER_PROPERTIES
        }, {"Tailscale-User-Login": "someone@example.com"})
        self.assertEqual(status, 200, body[:500])
        self.assertIn("PR opened", body)
        pr = FakeGitHub.opened[0]
        env = yaml.safe_load(pr["new_text"])["services"]["mc"]["environment"]
        self.assertEqual(env["DIFFICULTY"], OTHER_DIFFICULTY)
        self.assertEqual(ce.custom_properties(pr["new_text"]), {"debug": "true"})
        self.assertEqual(pr["from_commit"], "c0ffee")
        self.assertEqual(pr["blob_sha"], "blob1")
        self.assertTrue(pr["branch"].startswith("kpanel/"))
        self.assertIn("someone@example.com", pr["body"])
        self.assertNotIn("max-players", pr["title"])
        changed = [(a, b) for a, b in zip(COMPOSE.splitlines(), pr["new_text"].splitlines()) if a != b]
        self.assertEqual(changed[0], (f'      DIFFICULTY: "{DIFFICULTY}"', f'      DIFFICULTY: "{OTHER_DIFFICULTY}"'))

    def test_import_pins_live_value(self):
        status, _ = self.post("/import", {"i:spawn-protection": "0"})
        self.assertEqual(status, 200)
        env = yaml.safe_load(FakeGitHub.opened[0]["new_text"])["services"]["mc"]["environment"]
        self.assertEqual(env["SPAWN_PROTECTION"], "0")

    def test_validation_errors_open_no_pr(self):
        status, body = self.post("/save", {"p:view-distance": "99", "p:motd": "a$b"})
        self.assertEqual(status, 400)
        self.assertIn("between 3 and 32", body)
        self.assertEqual(FakeGitHub.opened, [])

    def test_locked_key_cannot_be_posted(self):
        status, body = self.post("/save", {"p:online-mode": "false"})
        self.assertEqual(status, 400)
        self.assertIn("locked", body)
        self.assertEqual(FakeGitHub.opened, [])

    def test_no_change_no_pr(self):
        status, body = self.post("/save", {"p:difficulty": DIFFICULTY})
        self.assertIn("Nothing changed", body)
        self.assertEqual(FakeGitHub.opened, [])

    def test_cross_site_post_refused(self):
        status, _ = self.post("/save", {"p:difficulty": OTHER_DIFFICULTY}, {"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        self.assertEqual(FakeGitHub.opened, [])

    def test_escaping(self):
        status, body = self.post("/save", {"p:motd": '<script>alert(1)</script>'})
        self.assertEqual(status, 200)
        self.assertNotIn("<script>alert(1)", body)

    # --- live: players ---------------------------------------------------------

    def test_players_page_lists_whitelist_ops_and_online(self):
        FakeMinecraft.online = ["sTEVE_"]
        status, body = self.get("/players")
        self.assertEqual(status, 200)
        self.assertIn("<code>sTEVE_</code>", body)
        self.assertIn('title="operator, level 4"', body)
        self.assertIn("online", body)
        FakeMinecraft.online = []

    def test_add_to_whitelist_is_live_and_needs_no_pr(self):
        status, body = self.post("/players", {"action": "add", "name": "Friend_01"},
                                 {"Tailscale-User-Login": "me@example.com"})
        self.assertEqual(status, 200, body[:300])
        self.assertIn("whitelist add Friend_01", FakeMinecraft.sent)
        self.assertIn("<code>Friend_01</code>", body)           # re-read from whitelist.json
        self.assertIn("Added Friend_01 to the whitelist", body)  # server reply shown
        self.assertIn("Recent live changes", body)
        self.assertEqual(FakeGitHub.opened, [])                 # live, not a PR
        self.post("/players", {"action": "remove", "name": "Friend_01"})

    def test_remove_also_kicks(self):
        self.post("/players", {"action": "add", "name": "Temp_User"})
        FakeMinecraft.sent.clear()
        self.post("/players", {"action": "remove", "name": "Temp_User"})
        self.assertEqual(FakeMinecraft.sent[0], "whitelist remove Temp_User")
        self.assertTrue(FakeMinecraft.sent[1].startswith("kick Temp_User"))

    def test_ban_is_live_and_lists_the_player(self):
        status, body = self.post("/players", {"action": "ban", "name": "Griefer_99"})
        self.assertEqual(status, 200)
        self.assertIn("ban Griefer_99", FakeMinecraft.sent)
        self.assertIn("<code>Griefer_99</code>", body)    # re-read from banned-players.json
        self.assertIn("Banned Griefer_99", body)          # server reply shown
        self.post("/players", {"action": "pardon", "name": "Griefer_99"})

    def test_pardon_lifts_the_ban(self):
        self.post("/players", {"action": "ban", "name": "Temp_Ban"})
        _, body = self.post("/players", {"action": "pardon", "name": "Temp_Ban"})
        self.assertIn("pardon Temp_Ban", FakeMinecraft.sent)
        self.assertNotIn("<code>Temp_Ban</code>", body)

    def test_nonexistent_player_reply_is_shown(self):
        _, body = self.post("/players", {"action": "add", "name": "Nobody404"})
        self.assertIn("That player does not exist", body)

    def test_injection_and_bad_names_never_reach_rcon(self):
        for bad in ("a;op Mallory", "x\nop y", "ab", "bad name", "op Mallory"):
            for action in ("add", "ban"):
                status, body = self.post("/players", {"action": action, "name": bad})
                self.assertEqual(status, 400, f"{action} {bad}")
        status, _ = self.post("/players", {"action": "stop", "name": "sTEVE_"})
        self.assertEqual(status, 400)
        self.assertFalse(any(c.startswith(("whitelist", "op", "stop", "ban", "pardon"))
                             for c in FakeMinecraft.sent), FakeMinecraft.sent)

    def test_players_cross_site_refused(self):
        status, _ = self.post("/players", {"action": "op", "name": "Mallory"}, {"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        self.assertEqual(FakeMinecraft.sent, [])

    # --- settings modes and the auth boundary -----------------------------------

    def test_settings_is_offered_when_github_is_configured(self):
        _, body = self.get("/")
        self.assertIn('href="/settings"', body)
        self.assertEqual(app.settings_mode(), "git")

    def file_mode(self):
        """No GitHub configured, on a copy of the fixture: the default setup."""
        props = pathlib.Path(tempfile.mkdtemp()) / "server.properties"
        props.write_text(pathlib.Path(FIXTURE).read_text())
        saved = dict(app.CFG)
        app.CFG.update(token="", props=str(props))
        self.addCleanup(lambda: app.CFG.update(saved))
        return props

    def test_without_github_settings_writes_the_file(self):
        self.file_mode()
        self.assertEqual(app.settings_mode(), "file")
        _, home = self.get("/")
        self.assertIn('href="/settings"', home)              # not hidden behind a token
        status, body = self.get()
        self.assertEqual(status, 200)
        self.assertIn('data-label="Save (N)"', body)
        self.assertNotIn('action="/import"', body)           # no git to differ from

    def test_file_mode_saves_only_the_changed_keys_and_opens_no_pr(self):
        props = self.file_mode()
        before = st.parse_properties(props.read_text())
        status, body = self.post("/save", {
            "p:difficulty": "peaceful" if before["difficulty"] != "peaceful" else "hard",
            "p:max-players": before["max-players"],          # unchanged
            "p:motd": "Grüße & hi",
        })
        self.assertEqual(status, 200, body[:500])
        self.assertIn("Takes effect when the server restarts", body)
        self.assertIn('action="/restart"', body)
        after = st.parse_properties(props.read_text())
        changed = {k for k in after if after[k] != before.get(k)}
        self.assertEqual(changed, {"difficulty", "motd"})
        self.assertEqual(after["motd"], "Grüße & hi")
        self.assertEqual(after["rcon.password"], before["rcon.password"])  # secrets untouched
        self.assertEqual(FakeGitHub.opened, [])

    def test_file_mode_writes_in_place_without_needing_a_writable_directory(self):
        """N-01: the panel is uid 10001 in the server's group 10000, and can't
        write /data itself: only the file's own group write bit is there, so no
        temp file and rename."""
        props = self.file_mode()
        inode = props.stat().st_ino
        os.chmod(props.parent, 0o500)  # the directory can't take a new entry
        self.addCleanup(os.chmod, props.parent, 0o700)
        status, body = self.post("/save", {"p:motd": "in place"})
        self.assertEqual(status, 200, body[:300])
        self.assertEqual(st.parse_properties(props.read_text())["motd"], "in place")
        self.assertEqual(props.stat().st_ino, inode)
        self.assertEqual(os.listdir(props.parent), ["server.properties"])
        # a shorter file leaves no tail of the old one behind
        before = props.read_text()
        self.post("/save", {"p:motd": "x"})
        self.assertEqual(st.parse_properties(props.read_text()).keys(), st.parse_properties(before).keys())

    def test_file_mode_refuses_to_follow_a_planted_symlink(self):
        """The server owns /data: a symlink in place of server.properties must
        not turn the panel's write into a write somewhere else."""
        props = self.file_mode()
        target = props.parent / "elsewhere"
        target.write_text(props.read_text())
        props.unlink()
        props.symlink_to(target)
        before = target.read_text()
        status, _ = self.post("/save", {"p:motd": "redirected"})
        self.assertEqual(status, 502)
        self.assertEqual(target.read_text(), before)

    def test_file_mode_explains_a_file_that_is_not_group_writable(self):
        props = self.file_mode()
        os.chmod(props, 0o444)
        self.addCleanup(os.chmod, props, 0o644)
        status, body = self.post("/save", {"p:motd": "nope"})
        self.assertEqual(status, 502)
        self.assertIn("chmod g+w", body)

    def test_file_mode_validation_errors_write_nothing(self):
        props = self.file_mode()
        before = props.read_text()
        status, _ = self.post("/save", {"p:view-distance": "99"})
        self.assertEqual(status, 400)
        status, _ = self.post("/save", {"p:online-mode": "false"})   # locked
        self.assertEqual(status, 400)
        self.assertEqual(props.read_text(), before)

    def test_file_mode_has_no_import(self):
        self.file_mode()
        status, _ = self.post("/import", {"i:spawn-protection": "0"})
        self.assertEqual(status, 404)

    def test_file_mode_before_the_first_start_explains_itself(self):
        self.file_mode().unlink()
        status, body = self.get_raw("/settings")
        self.assertEqual(status, 502)
        self.assertIn("first start", body)

    def test_restart_stops_the_server_after_answering(self):
        app.RESTART_DELAY, delay = 0, app.RESTART_DELAY
        self.addCleanup(lambda: setattr(app, "RESTART_DELAY", delay))
        status, body = self.post("/restart", {})
        self.assertEqual(status, 200)
        self.assertIn("Restarting", body)
        self.assertIn('data-refresh="60"', body)
        for _ in range(50):
            if "stop" in FakeMinecraft.sent:
                break
            threading.Event().wait(0.02)
        self.assertEqual(FakeMinecraft.sent, ["list", "stop"])

    def test_restart_is_on_the_dashboard_and_asks_first(self):
        _, body = self.get("/")
        self.assertRegex(body, r'action="/restart"[^>]*data-confirm=')

    def test_restart_cross_site_refused(self):
        status, _ = self.post("/restart", {}, {"Sec-Fetch-Site": "cross-site"})
        self.assertEqual(status, 403)
        self.assertEqual(FakeMinecraft.sent, [])

    # --- DNS rebinding and CSRF ----------------------------------------------------

    def raw(self, method, path, headers, data=None):
        """A request with exactly these headers: urllib would add or keep ones a test must leave out."""
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=5)
        body = urllib.parse.urlencode(data).encode() if data is not None else None
        c.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        for k, v in headers.items():
            c.putheader(k, v)
        if body is not None:
            c.putheader("Content-Type", "application/x-www-form-urlencoded")
            c.putheader("Content-Length", str(len(body)))
        c.endheaders(body)
        r = c.getresponse()
        out = r.status, r.read().decode()
        c.close()
        return out

    def here(self):
        return f"127.0.0.1:{self.srv.server_address[1]}"

    def test_a_rebound_host_name_is_refused_without_a_login(self):
        """What a browser sends after attacker.example re-points itself at 127.0.0.1."""
        port = self.srv.server_address[1]
        status, body = self.raw("GET", "/logs", {"Host": f"attacker.example:{port}"})
        self.assertEqual(status, 403)
        self.assertIn("KPANEL_ALLOWED_HOSTS=attacker.example", body)
        self.assertNotIn("ANCIENT", body)

    def test_a_rebound_post_changes_nothing(self):
        port = self.srv.server_address[1]
        rebound = {"Host": f"attacker.example:{port}", "Origin": f"http://attacker.example:{port}",
                   "Sec-Fetch-Site": "same-origin"}
        for path, form in (("/players", {"action": "op", "name": "Mallory"}), ("/restart", {})):
            status, _ = self.raw("POST", path, rebound, form)
            self.assertEqual(status, 403, path)
        self.assertEqual(FakeMinecraft.sent, [])

    def test_loopback_names_are_always_answered(self):
        port = self.srv.server_address[1]
        for host in (f"localhost:{port}", f"127.0.0.1:{port}", f"[::1]:{port}", "LOCALHOST.", "localhost"):
            self.assertEqual(self.raw("GET", "/logs", {"Host": host})[0], 200, host)

    def test_allowed_hosts_lets_a_named_host_in(self):
        port = self.srv.server_address[1]
        app.CFG["allowed_hosts"] = "mc.example.lan, kpanel.tail1234.ts.net:443"
        self.addCleanup(app.CFG.update, allowed_hosts="")
        self.assertEqual(self.raw("GET", "/logs", {"Host": f"MC.example.lan:{port}"})[0], 200)
        self.assertEqual(self.raw("GET", "/logs", {"Host": "kpanel.tail1234.ts.net"})[0], 200)
        self.assertEqual(self.raw("GET", "/logs", {"Host": "evil.ts.net"})[0], 403)

    def test_allowed_hosts_star_answers_any_name(self):
        app.CFG["allowed_hosts"] = "*"
        self.addCleanup(app.CFG.update, allowed_hosts="")
        self.assertEqual(self.raw("GET", "/logs", {"Host": "anything.example"})[0], 200)

    def test_a_missing_host_header_is_refused(self):
        self.assertEqual(self.raw("GET", "/logs", {})[0], 403)

    def test_with_a_login_any_host_name_is_fine_unless_narrowed(self):
        """Rebinding gets no credentials (the browser keeps them per origin), so a
        reverse proxy with its own domain keeps working."""
        self.assertTrue(app.host_allowed("panel.example.com", allowed="", login=True))
        self.assertFalse(app.host_allowed("panel.example.com", allowed="other.example", login=True))
        self.assertFalse(app.host_allowed("panel.example.com", allowed="", login=False))

    def test_a_host_no_browser_sends_is_refused_in_every_mode(self):
        """N-08: host_name split at the first ':', so 'localhost:8080@evil.example'
        counted as localhost."""
        bad = ("localhost:8080@evil.example", "localhost/evil", "localhost\\evil",
               "local host", "localhost\tx", "localhost\x00")
        for host in bad:
            self.assertEqual(app.host_name(host), "", host)
            for login, allowed in ((False, ""), (True, ""), (False, "*"), (True, "localhost")):
                self.assertFalse(app.host_allowed(host, allowed=allowed, login=login), (host, login, allowed))
        self.assertEqual(self.raw("GET", "/", {"Host": "localhost:8080@evil.example"})[0], 403)
        self.assertEqual(self.raw("GET", "/", {"Host": "localhost:8080"})[0], 200)

    def test_the_server_header_names_no_versions(self):
        """N-09: it said 'kPanel Python/3.13.x'."""
        for path in ("/", "/healthz", "/nowhere"):
            c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=5)
            c.request("GET", path, headers={"Host": "localhost"})
            r = c.getresponse()
            r.read()
            c.close()
            self.assertEqual(r.getheader("Server"), "kPanel", path)

    def test_healthz_answers_any_host(self):
        self.assertEqual(self.raw("GET", "/healthz", {"Host": "kpanel:8080"}), (200, "ok"))

    def test_host_name_normalisation(self):
        self.assertEqual(app.host_name("Example.COM.:8080"), "example.com")
        self.assertEqual(app.host_name("[::1]:8080"), "[::1]")
        self.assertEqual(app.host_name("::1"), "[::1]")
        self.assertEqual(app.host_name("[::1"), "")
        self.assertEqual(app.host_name(None), "")

    def test_post_without_fetch_metadata_is_judged_by_origin(self):
        """Safari before 16.4 sends no Sec-Fetch-Site: fall back to Origin, then Referer."""
        here = self.here()
        form = {"action": "op", "name": "Mallory"}
        self.assertEqual(self.raw("POST", "/players", {"Host": here, "Origin": "http://evil.example"}, form)[0], 403)
        self.assertEqual(self.raw("POST", "/players", {"Host": here, "Origin": "null"}, form)[0], 403)
        self.assertEqual(self.raw("POST", "/players", {"Host": here, "Referer": "http://evil.example/x"}, form)[0], 403)
        self.assertEqual(self.raw("POST", "/players", {"Host": here}, form)[0], 403)  # none of the three
        self.assertEqual(FakeMinecraft.sent, [])
        self.assertEqual(self.raw("POST", "/players", {"Host": here, "Origin": f"http://{here}"}, form)[0], 200)
        self.assertEqual(self.raw("POST", "/players", {"Host": here, "Referer": f"http://{here}/players"},
                                  {"action": "deop", "name": "Mallory"})[0], 200)

    def test_a_foreign_origin_is_refused_even_with_same_origin_fetch_metadata(self):
        here = self.here()
        status, _ = self.raw("POST", "/players", {"Host": here, "Sec-Fetch-Site": "same-origin",
                                                  "Origin": "http://evil.example"}, {"action": "op", "name": "Mallory"})
        self.assertEqual(status, 403)
        self.assertEqual(FakeMinecraft.sent, [])

    # --- request limits and headers -------------------------------------------------

    def test_pages_carry_a_csp_that_allows_exactly_the_panels_own_script(self):
        with urllib.request.urlopen(self.url + "/players") as r:
            csp, nosniff, body = r.headers["Content-Security-Policy"], r.headers["X-Content-Type-Options"], r.read().decode()
        self.assertEqual(nosniff, "nosniff")
        scripts = re.findall(r"<script>(.*?)</script>", body, re.S)
        self.assertEqual(len(scripts), 1)  # one inline script, the one the hash pins
        digest = base64.b64encode(__import__("hashlib").sha256(scripts[0].encode()).digest()).decode()
        self.assertIn(f"script-src 'sha256-{digest}'", csp)
        self.assertIn("default-src 'none'", csp)
        self.assertIn("form-action 'self'", csp)

    def test_a_negative_content_length_is_refused_at_once(self):
        """rfile.read(-1) used to block the worker thread until the client hung up."""
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=3)
        c.putrequest("POST", "/players")
        c.putheader("Sec-Fetch-Site", "same-origin")
        c.putheader("Content-Length", "-1")
        c.endheaders()
        self.assertEqual(c.getresponse().status, 400)
        c.close()

    def test_an_oversized_form_is_refused_unread(self):
        c = http.client.HTTPConnection("127.0.0.1", self.srv.server_address[1], timeout=3)
        c.putrequest("POST", "/players")
        c.putheader("Sec-Fetch-Site", "same-origin")
        c.putheader("Content-Length", str(app.MAX_FORM + 1))
        c.endheaders()
        self.assertEqual(c.getresponse().status, 413)
        c.close()
        self.assertEqual(FakeMinecraft.sent, [])

    def test_a_stalled_client_is_dropped(self):
        self.assertTrue(0 < app.Handler.timeout <= 60)

    def test_player_counts_from_the_ping_are_numbers(self):
        self.assertEqual(app._int("<script>"), 0)
        self.assertEqual(app._int(None), 0)
        self.assertEqual(app._int("7"), 7)

    def test_other_containers_of_the_stack_are_refused(self):
        """N-05: from mc, http://kpanel:8080 with Host: localhost was the full
        panel without a login. Sibling services are refused by address."""
        stack = {"mc,mc-backup,filebrowser": frozenset({"10.89.0.2", "10.89.0.3"})}
        resolve = lambda names: stack.get(names, frozenset())
        refuse = "mc,mc-backup,filebrowser"
        self.assertFalse(app.peer_allowed("10.89.0.2", refuse, "", resolve))      # mc
        self.assertTrue(app.peer_allowed("10.89.0.1", refuse, "", resolve))       # the published port's gateway
        self.assertTrue(app.peer_allowed("192.168.1.20", refuse, "", resolve))    # a LAN client, no userland proxy
        self.assertTrue(app.peer_allowed("127.0.0.1", refuse, "", resolve))       # the healthcheck
        self.assertTrue(app.peer_allowed("10.89.0.2", "", "", resolve))           # nothing configured

    def test_behind_tailscale_only_the_sidecar_is_answered(self):
        resolve = lambda names: frozenset({"10.89.0.9"}) if names == "tailscale" else frozenset()
        self.assertTrue(app.peer_allowed("10.89.0.9", "mc", "tailscale", resolve))
        self.assertFalse(app.peer_allowed("10.89.0.2", "mc", "tailscale", resolve))
        self.assertFalse(app.peer_allowed("10.89.0.1", "mc", "tailscale", resolve))  # not published there
        self.assertTrue(app.peer_allowed("127.0.0.1", "mc", "tailscale", resolve))
        # the sidecar not resolvable (restarting): nobody, rather than everybody
        self.assertFalse(app.peer_allowed("10.89.0.9", "", "tailscale", lambda n: frozenset()))

    def test_a_refused_peer_gets_a_403_before_anything_else(self):
        asked = []

        def refuse(addr):
            asked.append(addr)
            return False
        from unittest import mock
        with mock.patch.object(app, "peer_allowed", refuse):
            status, body = self.get_raw("/", {"Host": "evil.example"})  # peer before Host
            self.assertEqual(status, 403)
            self.assertIn("other containers", body)
            status, _ = self.post("/players", {"action": "op", "name": "Attacker2"},
                                  {"Sec-Fetch-Site": "same-origin"})
            self.assertEqual(status, 403)
        self.assertEqual(asked[0], "127.0.0.1")  # it is the TCP peer that is judged

    def test_service_names_resolve_and_unknown_names_add_nothing(self):
        app._peer_cache.clear()
        self.addCleanup(app._peer_cache.clear)
        self.assertIn("127.0.0.1", app.resolve_peers("localhost, no-such-service.invalid"))
        self.assertEqual(app.resolve_peers("no-such-service.invalid"), frozenset())

    def test_the_tailscale_login_header_is_ignored_unless_trusted(self):
        """Outside tailscale.yml any client can send it: it must not name anyone."""
        self.assertEqual(app.who_from({"Tailscale-User-Login": "admin@example.com"}), "")
        app.CFG["trust_ts_headers"] = True
        self.addCleanup(app.CFG.update, trust_ts_headers=False)
        self.assertEqual(app.who_from({"Tailscale-User-Login": "me@example.com"}), "me@example.com")
        for crafted in ("x\n| injected | row |", "a b", "[click](http://evil)", "x" * 101):
            self.assertEqual(app.who_from({"Tailscale-User-Login": crafted}), "", crafted)

    # N-01: the boundary against the server is the uid, proved by
    # tests/integration/plugin_isolation.sh against a running stack. Here: the
    # panel warns when it runs as the server files' owner.
    def test_the_panel_notices_when_it_shares_the_server_uid(self):
        with tempfile.NamedTemporaryFile() as f:
            owner = os.stat(f.name).st_uid
            self.assertTrue(app.shares_server_uid(f.name, uid=owner))
            self.assertFalse(app.shares_server_uid(f.name, uid=owner + 1))
        self.assertFalse(app.shares_server_uid("/nonexistent/server.properties", uid=0))

    def with_hashed_login(self, password="correct horse battery"):
        saved = dict(app.CFG)
        app.CFG.update(user="admin", password_hash=app.auth.hash_password(password))
        self.addCleanup(lambda: app.CFG.update(saved))
        return {"Authorization": "Basic " + base64.b64encode(f"admin:{password}".encode()).decode()}

    def test_a_hashed_login_challenges_then_lets_the_right_password_in(self):
        good = self.with_hashed_login()
        self.assertEqual(self.get_raw("/players")[0], 401)
        status, body = self.get_raw("/players", good)
        self.assertEqual(status, 200)
        wrong = {"Authorization": "Basic " + base64.b64encode(b"admin:wrong").decode()}
        self.assertEqual(self.get_raw("/", wrong)[0], 401)

    def test_repeated_wrong_passwords_get_429_with_retry_after(self):
        self.with_hashed_login()
        bad = {"Authorization": "Basic " + base64.b64encode(b"admin:guess").decode()}
        codes = [self.get_raw("/players", bad)[0] for _ in range(app.auth.FREE_FAILURES + 1)]
        self.assertEqual(codes[:-1], [401] * app.auth.FREE_FAILURES)
        self.assertEqual(codes[-1], 429)

    def test_healthz_stays_reachable_for_the_container_healthcheck(self):
        self.with_hashed_login()
        status, body = self.get_raw("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(body, "ok")

    def test_startup_refuses_a_panel_with_no_auth_boundary(self):
        with self.assertRaises(SystemExit):
            app.require_auth_boundary(login_configured=False, allow_no_auth="")

    def test_startup_accepts_an_explicit_no_auth_opt_in(self):
        app.require_auth_boundary(login_configured=False, allow_no_auth="1")
        app.require_auth_boundary(login_configured=True, allow_no_auth="")

    def test_a_crafted_server_icon_cannot_break_out_of_the_img_tag(self):
        """The favicon comes off the wire from the pinged server, into src="...".

        The prefix check alone does not stop a quote closing the attribute early.
        """
        original = app.status_ping
        app.status_ping = lambda host, port, timeout=4.0: {
            "version": "Paper 26.3", "online": 0, "max": 8, "motd": "x", "ms": 3,
            "favicon": 'data:image/png;base64,AAAA" onerror="alert(1)'}
        try:
            _, body = self.get("/")
            # the breakout form: a quote that closes src= and starts a handler
            self.assertNotIn('AAAA" onerror', body)
            self.assertIn("&quot;", body)      # escaped, so it stays inside the value
        finally:
            app.status_ping = original

    def test_the_ping_rejects_a_favicon_that_is_not_base64(self):
        """Defence at the source as well: the prefix alone was not enough."""
        self.assertIsNone(app._DATA_PNG.fullmatch('data:image/png;base64,AA" onerror="x'))
        self.assertIsNotNone(app._DATA_PNG.fullmatch("data:image/png;base64,AAAA=="))

    # --- logs (read-only) ------------------------------------------------------

    def test_logs_page_shows_the_newest_lines(self):
        status, body = self.get("/logs")
        self.assertEqual(status, 200)
        self.assertIn("sTEVE_ joined the game", body)
        self.assertIn("Can&#x27;t keep up!", body)

    def test_logs_page_caps_the_tail(self):
        _, body = self.get("/logs")
        self.assertNotIn("ANCIENT marker line", body)

    def test_logs_page_hides_rcon_polling_but_keeps_rcon_commands(self):
        _, body = self.get("/logs")
        self.assertNotIn("Thread RCON Client", body)      # polling noise, drowns the file
        self.assertIn("Banned Griefer_42", body)          # a command someone ran: must survive

    def test_logs_page_can_show_rcon_chatter(self):
        _, body = self.get("/logs?rcon=1")
        self.assertIn("Thread RCON Client", body)

    def test_log_lines_render_inside_a_terminal_block(self):
        _, body = self.get("/logs")
        self.assertIn("class=term", body)
        # the lines live inside it, not loose in the card
        term = body.split("class=term", 1)[1]
        self.assertIn("sTEVE_ joined the game", term.split("</div>", 1)[0])

    def test_logs_page_without_a_log_file_is_empty_not_an_error(self):
        original = app.CFG["data"]
        app.CFG["data"] = tempfile.mkdtemp()
        try:
            status, body = self.get("/logs")
            self.assertEqual(status, 200)
            self.assertIn("No log file yet", body)
        finally:
            app.CFG["data"] = original

    # --- live: gamerules -------------------------------------------------------

    def test_gamerules_page_shows_only_rules_the_server_confirms(self):
        status, body = self.get("/gamerules")
        self.assertEqual(status, 200)
        self.assertIn('name="g:pvp"', body)
        self.assertIn('name="g:players_sleeping_percentage"', body)
        self.assertNotIn('name="g:max_minecart_speed"', body)   # wiki lists it, server doesn't

    def test_change_gamerule_live_only_sends_changed_rules(self):
        status, body = self.post("/gamerules", {"g:pvp": "false", "g:keep_inventory": "false",
                                                "g:players_sleeping_percentage": "50"})
        self.assertEqual(status, 200, body[:300])
        sets = [c for c in FakeMinecraft.sent if len(c.split()) == 3]
        self.assertEqual(sorted(sets), ["gamerule players_sleeping_percentage 50", "gamerule pvp false"])
        self.assertIn("Applied", body)
        self.assertEqual(FakeGitHub.opened, [])
        self.post("/gamerules", {"g:pvp": "true", "g:players_sleeping_percentage": "100"})

    def test_gamerule_validation(self):
        for form in ({"g:pvp": "maybe"}, {"g:players_sleeping_percentage": "lots"},
                     {"g:no_such_rule": "true"}, {"g:pvp": "true; op Mallory"}):
            status, _ = self.post("/gamerules", form)
            self.assertEqual(status, 400, form)
        self.assertFalse([c for c in FakeMinecraft.sent if len(c.split()) == 3], FakeMinecraft.sent)

    # --- dashboard -----------------------------------------------------------------

    def test_dashboard_is_the_start_page(self):
        FakeMinecraft.online = ["sTEVE_"]
        status, body = self.get("/")
        FakeMinecraft.online = []
        self.assertEqual(status, 200)
        self.assertIn('<span class=num>1</span>', body)          # hero: players online
        self.assertIn("/ 8 players", body)
        self.assertIn('<span class="status up" title="answers the server-list ping">Online</span>', body)  # dot + label, not colour alone
        self.assertIn("sTEVE_", body)                           # who is online
        self.assertNotIn("View distance", body)   # removed from the dashboard
        for label in ("TPS", "CPU", "Memory", "Difficulty", "Whitelisted", "World size"):
            self.assertIn(f"<div class=l>{label}</div>", body)
        self.assertIn("<div class=l>Last backup<svg", body)   # a link to the backups, marked as leaving the panel
        hero = body.split('class="card herocard"', 1)[1].split("<div class=tiles>", 1)[0]
        self.assertIn("Address <code>localhost</code>", hero)    # identity lives in the hero, not a tile
        self.assertIn("Paper 26.3", body)
        self.assertIn(">20.0<", body)                            # TPS parsed past Paper's '*'
        self.assertIn("1 kept", body)

    def test_dashboard_hero_holds_motd_and_icon(self):
        _, body = self.get("/")
        hero = body.split('class="card herocard"', 1)[1].split("<div class=tiles>", 1)[0]
        self.assertIn("A Test Server", hero)            # MOTD inside the hero card
        self.assertIn('src="data:image/png;base64,', hero)              # Paper logo fallback
        self.assertIn("no server-icon.png set", hero)
        self.assertNotIn('title="MOTD">“', body.split("<div class=tiles>", 1)[1])  # not below the tiles

    def test_dashboard_prefers_the_servers_own_icon(self):
        real = app.status_ping
        app.status_ping = lambda *a, **k: {**real(*a, **k), "favicon": "data:image/png;base64,SERVERICON"}
        try:
            _, body = self.get("/")
        finally:
            app.status_ping = real
        self.assertIn('class="icon px" src="data:image/png;base64,SERVERICON"', body)

    def test_dashboard_marks_motd_and_draws_graphs_from_history(self):
        import history
        real = app.HISTORY
        app.HISTORY = history.History(str(self.data / "h.json"))
        for i, (pl, tps) in enumerate([(0, 20.0), (1, 19.8), (2, 20.0), (1, 20.0)]):
            app.HISTORY.add({"t": 1_700_000_000 + i * 300, "players": pl, "max": 8, "tps": tps,
                             "ms": 3 + i, "whitelisted": 1, "world": 4096 + i})
        try:
            _, body = self.get("/")
        finally:
            app.HISTORY = real
        self.assertIn("<span class=tag>MOTD</span> A Test Server", body)
        graphs = re.findall(r"<svg class=spark", body)
        self.assertEqual(len(graphs), 5)   # players, TPS, latency, whitelisted, world size
        # 4 stored + the live value = 5 points per graph, the last labelled "now"
        players_svg = body.split("<div class=herospark>", 1)[1].split("</svg>", 1)[0]
        self.assertEqual(players_svg.count("<title>"), 5)
        self.assertIn("<title>now: 1</title>", players_svg)
        self.assertIn("<div class=sparkcap>players · last ", body)   # the hero graph says what it is

    def test_no_history_yet_means_no_graphs(self):
        import history
        real = app.HISTORY
        app.HISTORY = history.History(str(self.data / "empty.json"))
        try:
            _, body = self.get("/")
        finally:
            app.HISTORY = real
        self.assertNotIn("<svg class=spark", body)    # one live point alone is not a graph

    def test_take_sample_collects_every_metric(self):
        s = app.take_sample()
        for k in ("t", "players", "max", "ms", "tps", "whitelisted", "world"):
            self.assertIn(k, s)
        self.assertEqual(s["tps"], 20.0)

    def test_dashboard_cpu_memory_and_backup_link(self):
        class Meter:
            def percent(self):
                return 12.4
        real_cpu, real_mem = app.CPU, app.procstats.memory
        app.CPU = Meter()
        app.procstats.memory = lambda: {"rss": 1_500_000_000, "total": 8_000_000_000, "available": 3_500_000_000}
        try:
            _, body = self.get("/")
        finally:
            app.CPU, app.procstats.memory = real_cpu, real_mem
        self.assertRegex(body, r"<div class=l>CPU</div><div class=v>12%</div>")
        self.assertRegex(body, r"<div class=l>Memory</div><div class=v>1\.4 GB</div>")
        self.assertIn("host 3.3 GB free of 7.5 GB", body)
        self.assertIn('<a class="tile link" href="https://files.example:8443/files/backups/">', body)

    def test_dashboard_cpu_memory_unavailable_shows_dashes(self):
        class Meter:
            def percent(self):
                return None
        real_cpu, real_mem = app.CPU, app.procstats.memory
        app.CPU, app.procstats.memory = Meter(), lambda: {"rss": None}
        try:
            status, body = self.get("/")
        finally:
            app.CPU, app.procstats.memory = real_cpu, real_mem
        self.assertEqual(status, 200)
        self.assertIn("<div class=l>CPU</div><div class=v>—</div>", body)
        self.assertIn("<div class=l>Memory</div><div class=v>—</div>", body)

    def test_dashboard_shows_offline_instead_of_failing(self):
        real = app.status_ping
        def down(*a, **k):
            raise OSError("connection refused")
        app.status_ping = down
        try:
            status, body = self.get("/")
        finally:
            app.status_ping = real
        self.assertEqual(status, 200)
        self.assertIn('<span class="status down" title="connection refused">Offline</span>', body)

    def test_rcon_password_comes_from_the_environment_when_set(self):
        self.assertEqual(app.rcon_password(), "pw")

    def test_rcon_password_falls_back_to_the_one_the_server_generated(self):
        """Zero-config: the image writes a fresh random password on every start."""
        original_pw, original_props = app.CFG["rcon_password"], app.CFG["props"]
        props = pathlib.Path(tempfile.mkdtemp()) / "server.properties"
        props.write_text("motd=x\nrcon.password=generated-abc123\n")  # gitleaks:allow (a test value)
        app.CFG.update(rcon_password="", props=str(props))
        try:
            self.assertEqual(app.rcon_password(), "generated-abc123")
            # re-read every time: a server restart changes it
            props.write_text("rcon.password=after-restart-456\n")
            self.assertEqual(app.rcon_password(), "after-restart-456")
        finally:
            app.CFG.update(rcon_password=original_pw, props=original_props)

    def test_tps_tone_says_what_is_wrong_not_just_a_colour(self):
        self.assertEqual(app.tps_tone(20.0), ("", "target 20"))
        self.assertEqual(app.tps_tone(None), ("", "target 20"))
        self.assertEqual(app.tps_tone(17.5), ("warn", "below target 20"))
        self.assertEqual(app.tps_tone(12.0), ("bad", "lagging, target 20"))

    def test_an_old_backup_is_flagged_overdue(self):
        backup = next(pathlib.Path(app.CFG["backups"]).glob("*.tar.gz"))
        fresh = backup.stat().st_mtime
        os.utime(backup, (fresh, fresh - 2 * 86400))
        try:
            _, body = self.get("/")
        finally:
            os.utime(backup, (fresh, fresh))
        self.assertIn('<a class="tile is-warn link"', body)
        self.assertIn("1 kept · overdue", body)

    def test_footer_names_version_licence_and_author(self):
        _, body = self.get("/players")
        footer = body.split("<footer>", 1)[1]
        self.assertIn(f"v{app.release.VERSION}", footer)
        self.assertIn("MIT License", footer)
        self.assertIn('<a href="https://github.com/kruser1337">kruser1337</a>', footer)
        self.assertIn('href="https://buymeacoffee.com/kruser1337"', footer)
        self.assertNotIn("available", footer)     # nothing known about releases yet

    def test_footer_announces_a_newer_release(self):
        app.release.LATEST["tag"] = "v99.0.0"
        try:
            _, body = self.get("/")
        finally:
            app.release.LATEST["tag"] = None
        self.assertIn('href="https://github.com/kruser1337/kPanel/releases/tag/v99.0.0"', body)
        self.assertIn("v99.0.0 available", body)

    def test_settings_have_no_description_prose(self):
        # Descriptions are hover tooltips now, not visible paragraphs.
        _, body = self.get("/settings")
        self.assertNotIn("<div class=d>", body)
        self.assertIn('title="The difficulty', body)

    def test_favicon(self):
        with urllib.request.urlopen(self.url + "/favicon.png") as r:
            self.assertEqual(r.headers["Content-Type"], "image/png")
            self.assertIn("max-age", r.headers["Cache-Control"])
            png = r.read()
        self.assertTrue(png.startswith(b"\x89PNG"))
        _, body = self.get("/")
        self.assertIn("<link rel=icon type=image/png href=/favicon.png>", body)
        self.assertIn("<img class=logo src=/favicon.png", body)

    def test_healthz(self):
        self.assertEqual(self.get("/healthz"), (200, "ok"))


if __name__ == "__main__":
    unittest.main()
