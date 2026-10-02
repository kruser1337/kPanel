# kpanel

The panel at <http://localhost:8080>. For how to *use* it, see
[`docs/operations.md`](../docs/operations.md). This file is for changing it.

**The split it's built on:** anything Minecraft reads only at startup
(`server.properties`) goes out as a **PR**, so git stays the source of truth.
Live server state (whitelist, ops, kicks, gamerules) goes over **RCON** and
applies immediately: no PR, no restart.

## How it works

```
browser ──▶ kpanel:8080   (localhost; your LAN with a login; or tailscale serve :443)
              │  reads /data/server.properties (ro), talks RCON to mc
              │  Settings: reads the compose from GitHub (pinned commit)
              ▼
   PR on kpanel/<ts> ──merge──▶ `docker compose up -d` (or Coolify redeploys)
```

| File | Role |
|---|---|
| `app.py` | HTTP server (stdlib), page rendering, form handling |
| `settings.py` | Per-key routing, validation, suggestions, locks, grouping |
| `compose_edit.py` | Rewrites only the env lines it must, then proves by parsing that nothing else changed |
| `github.py` | Four GitHub REST calls (stdlib) |
| `rcon.py` | Source RCON client (stdlib), username guard, reply parsers |
| `ui.py` | Design system: colour tokens, component CSS, icons ([rules](DESIGN.md)) |
| `release.py` | Version (`VERSION`, bump it in the commit you tag `v<VERSION>`) and the twice-daily latest-release check |
| `gamerules.py` | Gamerule types, validation; keeps only rules the server confirms |
| `properties_meta.json` | Generated key list (see below) |
| `tools/gen_meta.py` | Regenerates `properties_meta.json` |
| `gamerules_meta.json` | Generated gamerule candidates (`tools/gen_gamerules.py`) |

Design rules worth keeping:

- **Never write to the server.** Git is the only source of truth; the panel's
  write path is a PR.
- **Never dump the compose with a YAML library.** It would delete every comment.
  `compose_edit` changes lines and then verifies the parsed result.
- **No `$` in values.** Coolify and compose would both interpolate it.
- **Validate everything that goes into an RCON command.** Usernames must match
  `[A-Za-z0-9_]{3,16}`, gamerule names must be ones the server itself
  confirmed, values must be `true`/`false` or an integer, and the client refuses
  line breaks. A command string can't be built from free text.
- **Branch from the commit that was read**, so a PR never silently reverts a
  change that landed on `main` in between.

## Tests

```sh
cd kpanel
python -m venv .venv && .venv/bin/pip install pyyaml==6.0.2
.venv/bin/python -m unittest test_compose_edit test_rcon       # editor + settings, RCON client vs a fake TCP server
PROPS_FIXTURE=/path/to/server.properties .venv/bin/python -m unittest test_app   # full HTTP flow, fake GitHub + fake server
```

`PROPS_FIXTURE` is optional: `testdata/server.properties` ships with the tests
and is used by default, so a fresh clone runs the whole suite with no setup.

Set it to test against a real server's file instead. Take the copy **with the
secrets blanked**, and check the result by value rather than by key name --
anything long and random is a secret whatever it is called:

```sh
docker compose exec mc cat /data/server.properties \
  | sed -E 's/^(rcon\.password|management-server-secret|management-server-tls-keystore-password)=.*/\1=/' \
  > /tmp/live.properties
# nothing long should survive:
awk -F= 'length($2) > 24' /tmp/live.properties
```

## Regenerating the key list

After a Minecraft upgrade adds or removes `server.properties` keys:

```sh
.venv/bin/python tools/gen_meta.py /tmp/live.properties > properties_meta.json
```

Only keys present in the live file are included. That's deliberate: keys that
newer Minecraft dropped (`pvp`, `spawn-monsters`, … are gamerules now) would
otherwise show up as settings that do nothing. Then check `settings.py`'s
hand-written `OPTION_TEXT`, `LOCKED` and `GROUPS` for new keys, and rerun the
tests.

Gamerules: `tools/gen_gamerules.py > gamerules_meta.json` regenerates the
candidate list. Extra or missing rules are harmless either way, because the
panel shows only what the server answers for.

The favicon (`favicon.png`) is Minetest Game's diamond pickaxe by BlockMen,
[CC BY-SA 3.0](https://creativecommons.org/licenses/by-sa/3.0/). See `favicon.LICENSE.txt`.
Mojang's own texture is copyrighted, so it can't be used.

Descriptions come from the [Minecraft Wiki](https://minecraft.wiki/w/Server.properties)
(CC BY-NC-SA 3.0), credited in the panel's footer.
