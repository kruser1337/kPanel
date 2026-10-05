"""Shape checks on the compose files.

Configuration rather than behaviour, so these are regression guards, not TDD.
Each stands for a mistake that was actually made and was silent when made.
"""

import pathlib
import re
import unittest

import yaml

ROOT = pathlib.Path(__file__).parent.parent


class ComposeLoader(yaml.SafeLoader):
    """Reads compose's merge tags (!override, !reset) as the plain value they tag."""


ComposeLoader.add_multi_constructor("!", lambda loader, tag, node: (
    loader.construct_sequence(node) if isinstance(node, yaml.SequenceNode)
    else loader.construct_mapping(node) if isinstance(node, yaml.MappingNode)
    else loader.construct_scalar(node)))
BASE_TEXT = (ROOT / "docker-compose.yml").read_text()
BASE = yaml.safe_load(BASE_TEXT)
LAN = yaml.load((ROOT / "compose" / "lan.yml").read_text(), Loader=ComposeLoader)
ENV_EXAMPLE = (ROOT / ".env.example").read_text()

# A single $ is a compose variable; $$ is an escaped literal dollar for the shell.
VAR = re.compile(r"(?<!\$)\$\{([A-Z_][A-Z0-9_]*)(?::-[^}]*)?\}")


class Base(unittest.TestCase):
    """docker compose up -d, with no .env, must give a working panel and server."""

    def test_every_variable_has_a_default(self):
        """Zero-config: a variable with no default is a value someone must supply."""
        bare = [m.group(0) for m in re.finditer(r"(?<!\$)\$\{[A-Z_][A-Z0-9_]*\}", BASE_TEXT)]
        self.assertEqual(bare, [])

    def test_every_variable_is_documented(self):
        used = set(VAR.findall(BASE_TEXT))
        documented = {l.split("=")[0].lstrip("# ") for l in ENV_EXAMPLE.splitlines() if "=" in l}
        self.assertEqual(used - documented, set())

    def test_the_server_generates_its_own_rcon_password(self):
        """itzg generates one only while RCON_PASSWORD is unset; set to "" it is empty."""
        for name in ("mc", "mc-backup", "kpanel"):
            env = BASE["services"][name].get("environment") or {}
            self.assertNotIn("RCON_PASSWORD", env, name)

    def test_panel_and_files_are_published_on_loopback_only(self):
        """That, and only that, is what makes running them without a login safe."""
        for name in ("kpanel", "filebrowser"):
            for spec in BASE["services"][name]["ports"]:
                self.assertTrue(spec.startswith("127.0.0.1:"), f"{name}: {spec}")

    def test_only_the_game_is_published_to_the_network(self):
        wide = [(n, p) for n, s in BASE["services"].items() for p in s.get("ports", [])
                if not p.startswith("127.0.0.1:")]
        self.assertEqual(wide, [("mc", "25565:25565")])

    def test_no_tailscale_in_the_base(self):
        self.assertNotIn("tailscale", BASE["services"])

    def test_the_panel_edits_this_file(self):
        self.assertEqual(BASE["services"]["kpanel"]["environment"]["COMPOSE_PATH"], "docker-compose.yml")

    def test_the_base_sets_no_property_the_panel_edits(self):
        """The image rewrites every key it has a variable for on each start, so
        one set here would silently undo the Settings page's edits to it."""
        import settings as st
        env = BASE["services"]["mc"]["environment"]
        editable = {p.env for p in st.PROPS.values() if p.env and not p.locked}
        self.assertEqual(editable & set(env), set())

    def test_the_panel_can_write_server_properties(self):
        self.assertIn("mc-data:/data", BASE["services"]["kpanel"]["volumes"])

    def test_the_default_server_is_a_stable_paper_build(self):
        """A beta can damage a world, and upgrades are one-way: opting in is the user's call."""
        env = BASE["services"]["mc"]["environment"]
        self.assertNotIn("PAPER_CHANNEL", env)
        self.assertTrue(env["PAPER_BUILD"].isdigit())  # pinned, never floating

    def test_the_base_carries_no_profiles(self):
        for name, svc in BASE["services"].items():
            self.assertIsNone(svc.get("profiles"), name)


LAN_TEXT = (ROOT / "compose" / "lan.yml").read_text()
TAILSCALE_TEXT = (ROOT / "compose" / "tailscale.yml").read_text()
TAILSCALE = yaml.load(TAILSCALE_TEXT, Loader=ComposeLoader)


class LanOverlay(unittest.TestCase):
    """Publishing wider must cost a password, enforced, not suggested."""

    def test_bind_addresses_are_literal(self):
        """An empty ${BIND} renders ":8080:8080", which means every interface."""
        for name in ("kpanel", "filebrowser"):
            for spec in LAN["services"][name]["ports"]:
                self.assertNotIn("${", spec, name)

    def test_it_replaces_the_loopback_binding_rather_than_adding_to_it(self):
        self.assertRegex(LAN_TEXT, r"(?m)^  kpanel:\n    # .*\n    ports: !override")
        self.assertRegex(LAN_TEXT, r"(?m)^  filebrowser:\n    ports: !override")

    def test_the_file_manager_password_is_required_by_compose(self):
        """${VAR:?} makes `up` fail before any container starts."""
        self.assertIn("${FILES_PASSWORD:?", LAN_TEXT)

    def test_the_panel_login_is_a_hash_passed_through_from_the_base(self):
        """The base carries it; compose can't require "a hash or the old plaintext",
        so the panel enforces it (test_the_panel_itself_also_refuses_without_a_login)."""
        env = BASE["services"]["kpanel"]["environment"]
        self.assertEqual(env["KPANEL_PASSWORD_HASH"], "${KPANEL_PASSWORD_HASH:-}")
        self.assertNotIn("KPANEL_BASIC_AUTH", LAN["services"]["kpanel"]["environment"])

    def test_the_example_env_holds_no_usable_password(self):
        """A placeholder someone uncomments unchanged is a public password."""
        for line in ENV_EXAMPLE.splitlines():
            if line.startswith(("# KPANEL_BASIC_AUTH=", "# FILES_PASSWORD=")):
                self.assertEqual(line.split("=", 1)[1], "", line)

    def test_the_panel_itself_also_refuses_without_a_login(self):
        self.assertEqual(LAN["services"]["kpanel"]["environment"]["KPANEL_ALLOW_NO_AUTH"], "")

    def test_the_file_manager_uses_the_password(self):
        entry = LAN["services"]["filebrowser"]["entrypoint"][2]
        self.assertIn("adminPassword", entry)
        self.assertNotIn("noauth", entry)

    def test_it_only_overrides_services_that_exist(self):
        """A name that matches nothing in the base silently does nothing."""
        self.assertEqual(set(LAN["services"]) - set(BASE["services"]), set())


class TailscaleOverlay(unittest.TestCase):
    def test_nothing_but_the_game_is_published(self):
        """The tailnet is the boundary; a published port would bypass it."""
        self.assertRegex(TAILSCALE_TEXT, r"(?m)^  kpanel:\n(    #.*\n)*    ports: !reset \[\]")
        self.assertRegex(TAILSCALE_TEXT, r"(?m)^  filebrowser:\n    ports: !reset \[\]")

    def test_its_variables_are_required(self):
        """An empty TS_HOSTNAME builds a serve config for ".", silently serving nothing."""
        for var in ("TS_AUTHKEY", "TS_HOSTNAME", "TS_TAILNET"):
            self.assertIn("${" + var + ":?", TAILSCALE_TEXT)

    def test_the_sidecar_has_no_container_hostname(self):
        """It would collide with a service name in the compose network's DNS."""
        self.assertIsNone(TAILSCALE["services"]["tailscale"].get("hostname"))

    def test_every_variable_is_documented(self):
        used = set(VAR.findall(TAILSCALE_TEXT))
        documented = {l.split("=")[0].lstrip("# ") for l in ENV_EXAMPLE.splitlines() if "=" in l}
        self.assertEqual(used - documented, set())


if __name__ == "__main__":
    unittest.main()
