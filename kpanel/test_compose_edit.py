"""Tests for compose_edit and settings, run against the REAL compose file (as a fork pins it).

    cd kpanel && python -m unittest -v
"""

import pathlib
import unittest

import yaml

import compose_edit as ce
import settings as st

BASE = (pathlib.Path(__file__).parent.parent / "docker-compose.yml").read_text()
# Git mode: a fork of the base in which panel PRs have already pinned some values.
COMPOSE = ce.apply_changes(BASE, {"MOTD": "Hosted with kPanel", "DIFFICULTY": "normal", "MAX_PLAYERS": "8"})


def env_of(text):
    return yaml.safe_load(text)["services"]["mc"]["environment"]


def changed_lines(a, b):
    return [(x, y) for x, y in zip(a.splitlines(), b.splitlines()) if x != y]


class EnvEdits(unittest.TestCase):
    def test_replace_existing_key_changes_only_that_line(self):
        # Read the current value: the panel itself changes it over time.
        cur = env_of(COMPOSE)["DIFFICULTY"]
        new = "easy" if cur != "easy" else "hard"
        out = ce.apply_changes(COMPOSE, {"DIFFICULTY": new})
        self.assertEqual(env_of(out)["DIFFICULTY"], new)
        self.assertEqual(changed_lines(COMPOSE, out),
                         [(f'      DIFFICULTY: "{cur}"', f'      DIFFICULTY: "{new}"')])
        self.assertEqual(len(COMPOSE.splitlines()), len(out.splitlines()))

    def test_comments_survive(self):
        out = ce.apply_changes(COMPOSE, {"MAX_PLAYERS": "10"}, {"debug": "false"})
        for line in COMPOSE.splitlines():
            if line.lstrip().startswith("#"):
                self.assertIn(line, out)

    def test_new_key_goes_under_one_marker_inside_mc_environment(self):
        out = ce.apply_changes(COMPOSE, {"SPAWN_PROTECTION": "0"})
        out2 = ce.apply_changes(out, {"ALLOW_FLIGHT": "true"})
        self.assertEqual(out2.count(ce.MARKER), 1)
        e = env_of(out2)
        self.assertEqual((e["SPAWN_PROTECTION"], e["ALLOW_FLIGHT"]), ("0", "true"))
        self.assertNotIn("ALLOW_FLIGHT", yaml.safe_load(out2)["services"]["mc-backup"]["environment"])

    def test_quotes_backslashes_and_unicode(self):
        motd = 'say "hi" \\o/ über §6gold'
        self.assertEqual(env_of(ce.apply_changes(COMPOSE, {"MOTD": motd}))["MOTD"], motd)

    def test_refuses_dollar_and_newline(self):
        for bad in ("costs $5", "two\nlines"):
            with self.assertRaises(ce.ComposeEditError):
                ce.apply_changes(COMPOSE, {"MOTD": bad})

    def test_refuses_a_value_set_from_a_variable(self):
        with self.assertRaises(ce.ComposeEditError):
            ce.apply_changes(COMPOSE, {"TZ": "x"})

    def test_finds_mc_tz_not_the_backup_services(self):
        """TZ is "${TZ:-Etc/UTC}" in both; the default form still counts as managed."""
        _, raw = ce._find(COMPOSE.splitlines(), "TZ")
        self.assertTrue(ce.is_externally_managed(raw))

    def test_no_changes_is_identity_and_newline_kept(self):
        self.assertEqual(ce.apply_changes(COMPOSE, {}), COMPOSE)
        self.assertTrue(ce.apply_changes(COMPOSE, {"DIFFICULTY": "hard"}).endswith("\n"))


class CustomBlock(unittest.TestCase):
    def test_create_then_update_block(self):
        out = ce.apply_changes(COMPOSE, {}, {"debug": "false", "chat-spam-threshold-seconds": "20"})
        self.assertEqual(env_of(out)[ce.CUSTOM], "chat-spam-threshold-seconds=20\ndebug=false\n")
        self.assertEqual(ce.custom_properties(out), {"chat-spam-threshold-seconds": "20", "debug": "false"})
        out2 = ce.apply_changes(out, {"MAX_PLAYERS": "12"}, {"debug": "true"})
        self.assertEqual(ce.custom_properties(out2), {"chat-spam-threshold-seconds": "20", "debug": "true"})
        self.assertEqual(env_of(out2)["MAX_PLAYERS"], "12")
        self.assertEqual(out2.count(ce.MARKER), 1)
        self.assertEqual(out2.count(f"{ce.CUSTOM}:"), 1)

    def test_new_env_key_after_block_still_parses_into_mc(self):
        out = ce.apply_changes(COMPOSE, {}, {"debug": "false"})
        out2 = ce.apply_changes(out, {"HARDCORE": "false"})
        self.assertEqual(env_of(out2)["HARDCORE"], "false")
        self.assertEqual(ce.custom_properties(out2), {"debug": "false"})

    def test_rejects_bad_keys_and_raw_custom(self):
        with self.assertRaises(ce.ComposeEditError):
            ce.apply_changes(COMPOSE, {}, {"Bad Key": "1"})
        with self.assertRaises(ce.ComposeEditError):
            ce.apply_changes(COMPOSE, {ce.CUSTOM: "x=1"})


class EveryProperty(unittest.TestCase):
    """Each editable key must be writable through its route and land exactly."""

    def sample(self, p):
        if p.kind == "bool":
            return "true"
        if p.options:
            return p.options[0][0]
        if p.kind == "int":
            return str(p.min if p.min is not None else 1)
        return "example"

    def test_round_trip_all(self):
        for key, p in st.PROPS.items():
            if p.locked:
                with self.assertRaises(ValueError, msg=key):
                    st.validate(p, self.sample(p))
                continue
            v = st.validate(p, self.sample(p))
            if p.env:
                if ce.is_externally_managed(ce.raw_value(COMPOSE, p.env)):
                    continue
                self.assertEqual(env_of(ce.apply_changes(COMPOSE, {p.env: v}))[p.env], v, key)
            else:
                self.assertEqual(ce.custom_properties(ce.apply_changes(COMPOSE, {}, {key: v}))[key], v, key)

    def test_secrets_never_exposed(self):
        for k in st.HIDDEN:
            self.assertNotIn(k, st.PROPS)

    def test_every_live_key_is_grouped_once(self):
        keys = list(st.PROPS)
        flat = [k for _, ks in st.grouped(keys) for k in ks]
        self.assertEqual(sorted(flat), sorted(keys))


class Validation(unittest.TestCase):
    P = st.PROPS

    def test_good(self):
        self.assertEqual(st.validate(self.P["difficulty"], "HARD"), "hard")
        self.assertEqual(st.validate(self.P["max-players"], " 12 "), "12")
        self.assertEqual(st.validate(self.P["force-gamemode"], "True"), "true")
        self.assertEqual(st.validate(self.P["op-permission-level"], "0"), "0")
        self.assertEqual(st.validate(self.P["level-type"], "minecraft:flat"), "minecraft:flat")

    def test_bad(self):
        for key, bad in [("difficulty", "nightmare"), ("max-players", "x"), ("view-distance", "64"),
                         ("view-distance", "2"), ("motd", "a$b"), ("motd", "a\nb"),
                         ("force-gamemode", "yes"), ("op-permission-level", "5"),
                         ("region-file-compression", "zstd"), ("online-mode", "false")]:
            with self.assertRaises(ValueError, msg=(key, bad)):
                st.validate(self.P[key], bad)

    def test_ranges_parsed_from_wiki(self):
        self.assertEqual((self.P["view-distance"].min, self.P["view-distance"].max), (3, 32))
        self.assertEqual(self.P["query.port"].max, 65534)

    def test_suggestions_explain_difficulty(self):
        opts = dict(self.P["difficulty"].options)
        self.assertIn("hostile", opts["peaceful"].lower())
        self.assertNotIn("Note:", [o[0] for o in self.P["player-idle-timeout"].options])

    def test_normalize(self):
        self.assertEqual(st.normalize(self.P["force-gamemode"], "TRUE"), "true")
        self.assertEqual(st.normalize(self.P["max-players"], "08"), "8")
        self.assertEqual(st.normalize(self.P["level-type"], "flat"), "minecraft:flat")


class Properties(unittest.TestCase):
    def test_parse_like_minecraft_writes_them(self):
        props = st.parse_properties(
            "#Minecraft server properties\n"
            "level-type=minecraft\\:normal\n"
            "motd=Gr\\u00FC\\u00DFe aus dem Norden\n"
            "generator-settings={}\n"
            "level-seed=\n")
        self.assertEqual(props["level-type"], "minecraft:normal")
        self.assertEqual(props["motd"], "Grüße aus dem Norden")
        self.assertEqual(props["generator-settings"], "{}")
        self.assertEqual(props["level-seed"], "")

    FILE = ("#Minecraft server properties\n#Thu Oct 01 12:00:00 UTC 2026\n"
            "difficulty=normal\nrcon.password=s3cret\nlevel-type=minecraft\\:normal\nmotd=Hi\n")

    def test_write_changes_only_the_wanted_lines(self):
        out = st.write_properties(self.FILE, {"difficulty": "hard"})
        self.assertEqual(changed_lines(self.FILE, out), [("difficulty=normal", "difficulty=hard")])
        self.assertIn("rcon.password=s3cret\n", out)              # secrets and comments untouched

    def test_write_appends_a_key_the_file_lacks(self):
        out = st.write_properties(self.FILE, {"spawn-protection": "0"})
        self.assertTrue(out.endswith("motd=Hi\nspawn-protection=0\n"))

    def test_write_escapes_like_minecraft_and_reads_back(self):
        for motd in ("Grüße aus dem Norden", "a=b: c\\d", " leading space", "§6gold 🎮", "#not a comment"):
            out = st.write_properties(self.FILE, {"motd": motd})
            self.assertTrue(out.isascii(), motd)
            self.assertEqual(st.parse_properties(out)["motd"], motd)
        self.assertIn("motd=Gr\\u00FC\\u00DFe", st.write_properties(self.FILE, {"motd": "Grüße"}))


if __name__ == "__main__":
    unittest.main()
