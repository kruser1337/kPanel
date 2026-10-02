# Operations

Day-to-day running of a kPanel server: the panel's pages, server commands,
settings, plugins, backups, upgrades and troubleshooting.

Commands are run from the repository root. With an overlay, add the same `-f`
flags you started with, e.g. `docker compose -f docker-compose.yml -f compose/lan.yml ps`.

## The panel

Open <http://localhost:8080> (or your LAN / tailnet address).

### Dashboard

Refreshes every 30 s.

| Shows | From |
|---|---|
| Players online, Online/Offline, MOTD, icon, address, version | A server-list ping from the panel to the server, so you see what a client sees. The icon is your `server-icon.png`, or Paper's logo if there is none |
| TPS, latency, CPU, memory | RCON `tps`; the ping's round trip; the JVM's `/proc` entries |
| Difficulty, whitelist, world size, last backup | `server.properties`, `whitelist.json`, the world folder, the newest backup archive |

**Warnings say what is wrong, in words:** TPS below 18 is "below target", below
15 is "lagging"; a backup older than 26 hours (two missed runs) is "overdue".

The small graphs show the last few samples (one every 5 minutes) plus the live
value. Hover a point for its time and value. Anything the panel can't read shows
`—` instead of breaking the page.

### Players and Gamerules: live, no restart

Both act on the running server over RCON. Changes apply immediately, need no
restart, and open no pull request.

| Page | What | Stored in |
|---|---|---|
| **Players** | Whitelist add/remove, op/deop, kick, ban/pardon, who's online | `whitelist.json`, `ops.json`, `banned-players.json` |
| **Gamerules** | Every gamerule your server version has, grouped, with explanations | The world save |

- **Whitelist and ops live only in those files.** The compose deliberately sets
  no `WHITELIST`/`OPS` variables, so a restart never re-adds someone you
  removed. Removing someone also kicks them if they're online.
- **Names are checked** against Minecraft's username rule (3–16 letters, digits,
  `_`) before anything is sent. A name Mojang doesn't know fails with the
  server's own "That player does not exist".
- Each page lists its **recent live changes**. That list resets when the panel
  restarts; `docker compose logs kpanel` keeps every one (`LIVE …`).

### Logs

The newest 300 lines of the server log, newest at the bottom, refreshed every
30 s and searchable with the filter box. The panel polls RCON every few minutes,
and each poll leaves two lines behind, so those are hidden by default; *Show
RCON chatter* brings them back. Commands someone actually ran are never hidden.

This is the Minecraft server's log only. For the other containers, use
`docker compose logs <service>`.

### Settings

Every `server.properties` key, grouped and searchable, with dropdowns and
range-checked numbers. Hover a key for its description.

1. Change values. Changed rows are highlighted and the button counts them.
2. **Save.** The panel writes the changed keys to `server.properties` and
   leaves every other line as it was.
3. **Restart now.** Minecraft reads the file only at startup. The button (also
   on the dashboard) saves the world, stops the server, and Docker starts it
   again, about a minute. The panel restarts with it and reloads itself.

**A key the compose sets wins.** The image rewrites every key it has a variable
for on each start, so the base compose sets none of the editable ones. If you
add one, say `DIFFICULTY:`, the panel's edits to `difficulty` are undone on the
next restart.

#### Through git instead (optional)

If your setup lives in git, set `KPANEL_GITHUB_REPO` and `KPANEL_GITHUB_TOKEN`
(see [`.env.example`](../.env.example)). Settings then never writes the file:

1. **Open PR.** The panel edits `docker-compose.yml` on a new branch and opens
   a pull request listing every change.
2. **Merge**, pull, and restart: `git pull && docker compose up -d`. Merge when
   nobody is online; the server restarts.

Not editable from the panel, on purpose:

- **Secrets are never shown** (`rcon.password` and friends).
- **Locked keys show why** (`online-mode`, `white-list`, `enable-rcon`,
  `server-port`, `level-name`, …). Each can lock everyone out, let strangers
  in, break the panel's RCON, or point the server at an empty world. Change them
  by hand, deliberately.
- **Values taken from a variable** (`${…}`) are locked too; change the variable.

In git mode, if `server.properties` is changed outside the panel (the file
manager, a plugin), Settings lists the keys whose live value differs from git,
and can pin the ones you keep with a pull request. In-game commands like `/difficulty` change the
running server, not the file, so they don't show up.

## Server commands (`rcon-cli`)

Anything the panel doesn't cover:

```sh
docker compose exec mc rcon-cli list      # one command
docker compose exec mc rcon-cli           # interactive; `exit` to leave
```

Commands over RCON take no leading `/`. Useful ones: `whitelist list`,
`op <name>`, `say <text>`, `save-all flush`, `seed`, `version`.

`stop` doesn't keep the server down: `restart: unless-stopped` brings it
straight back, which is how the panel's Restart button works. To shut it down,
use `docker compose stop mc`.

## What lives where

| What | Change it in |
|---|---|
| `server.properties` keys | The Settings page, then restart |
| Gamerules (PvP, keep inventory, mob griefing, …) | The Gamerules page, or `rcon-cli gamerule <rule> <value>` |
| Paper / Spigot config (`config/paper-*.yml`, `spigot.yml`, `bukkit.yml`) | The file manager, then `docker compose restart mc` |
| Plugin config (`plugins/<Plugin>/config.yml`) | The file manager, then reload or restart |
| Version, memory, plugin list | `docker-compose.yml` |

**A key the compose sets is rewritten on every start**, so an edit to it in
`server.properties` is undone. Remove it from the compose, or change it there.

## Plugins

Check that a plugin supports your Minecraft version on Paper first. Then
declare it in `docker-compose.yml`, under `mc` → `environment`:

```yaml
      # Modrinth project slugs or IDs. Removing a line uninstalls it on the next start.
      MODRINTH_PROJECTS: |
        luckperms
        worldedit
```

`PLUGINS` (a list of download URLs) works the same way. Then
`docker compose up -d`. A plugin's config stays in `plugins/<Plugin>/`.

For a quick test you can instead drop a jar into `plugins/` with the file
manager and restart, but don't manage the same plugin both ways.

## File manager

<http://localhost:8081>: the whole server folder (world, configs, logs,
plugins), plus the backups, read-only.

- **Don't edit or replace files under `world*/` while players are online.** The
  server holds them in memory and will overwrite or corrupt your change. Stop it
  first: `docker compose stop mc`.
- Downloading a `world*/` folder is the easy way to take a copy home.
- Without `compose/lan.yml` it has no login, because only this machine can
  reach it. With it, it requires `FILES_PASSWORD`.

## Backups

- **What:** `mc-backup` archives the server folder into the `mc-backups` volume
  as `world-YYYYMMDD-HHMMSS.tar.gz`. It flushes and pauses world saves over RCON
  first, so an archive is never caught mid-write.
- **When:** at 06:00 and 18:00 in `TZ` (UTC unless you set it). Fixed clock
  times, so restarts neither skip nor add backups. There is no backup at start.
- **Kept:** the newest 4 (two days back), never older than 7 days.
- **Where:** on the same machine. That protects against mistakes and corruption,
  **not against losing the machine**. Download copies with the file manager, or
  copy the volume elsewhere, if that matters to you.

```sh
docker compose exec mc-backup ls -lh /backups    # list
docker compose exec mc-backup backup now         # take one now, e.g. before an upgrade
docker compose logs --tail 50 mc-backup          # its log
```

### Restoring

The image's `restore-tar-backup` unpacks the **newest** archive, and only into
an **empty** server folder. That is a safety check, not a bug.

1. Stop everything: `docker compose down`.
2. Keep a copy of the current state, then empty the volume. `V` is the compose
   project name, which prefixes the volume names (`docker volume ls` shows them;
   it is the folder name, lower-cased, by default):
   ```sh
   V=kpanel
   docker run --rm -v ${V}_mc-data:/data -v "$PWD":/out alpine \
     tar czf /out/mc-data-before-restore-$(date +%F-%H%M).tar.gz -C /data .
   docker run --rm -v ${V}_mc-data:/data alpine sh -c 'rm -rf /data/* /data/.[!.]*'
   ```
3. Restore the newest backup:
   ```sh
   docker run --rm -v ${V}_mc-data:/data -v ${V}_mc-backups:/backups:ro \
     itzg/mc-backup restore-tar-backup
   ```
   For an older archive, unpack it yourself instead:
   `docker run --rm -v ${V}_mc-data:/data -v ${V}_mc-backups:/backups:ro alpine tar xzf /backups/world-<ts>.tar.gz -C /data`
4. Start again: `docker compose up -d`. The server re-downloads its jar.

A backup made by a newer Minecraft version can't be used with an older one.

## Upgrading

**kPanel:** the footer says when a newer release exists. Then:

```sh
git pull
docker compose up -d --build
```

**Minecraft / Paper:** the version is pinned in `docker-compose.yml`
(`VERSION`, `PAPER_BUILD`, and `PAPER_CHANNEL` for beta builds). Never let it
float: a restart could silently change the jar. The default is the newest
version with a stable build; a beta is worth it only if you accept the risks
in the README's "Newer Minecraft versions".

```sh
# newest builds for a version; "stable": null means beta builds only
curl -s https://fill.papermc.io/v3/projects/paper/versions/26.3/builds \
  | jq '{newest: (max_by(.id) | {id, channel}), stable: ([.[] | select(.channel=="STABLE")] | max_by(.id) | .id)}'
```

1. Take a backup: `docker compose exec mc-backup backup now`.
2. Check your plugins support the new version.
3. Change the pins. A beta build needs `PAPER_CHANNEL: "experimental"`; a
   stable one doesn't.
4. `docker compose up -d` when nobody is online. Players need the matching
   client version.

**Upgrades are one-way.** A newer version migrates the world on load, and it
isn't safe to open with an older one again. Going back means restoring a backup
from before the upgrade. The Java version comes from the image tag
(`stable-java25`); check Paper's `java.minimum` before a major jump.

## Troubleshooting

| Symptom | Check |
|---|---|
| `docker compose up` says `required variable … is missing a value` | An overlay needs a value in `.env`; the message names it. See [`.env.example`](../.env.example) |
| The panel container keeps restarting, log says it `refuses to start` | It is reachable without a login and wasn't told that's intended. Set `KPANEL_BASIC_AUTH`, or use the base file alone (loopback) |
| `ports: !override` / `!reset` is rejected | Docker Compose is older than 2.24. Upgrade it |
| Players can't connect | Is 25565 open in the host's firewall, and forwarded on your router for players outside your network? `nc -z <host> 25565` from outside |
| "You are not white-listed on this server" | Add them on the Players page, or `rcon-cli whitelist add <name>` |
| Players or Gamerules page shows an RCON error | The server is still starting (wait ~30 s), or it is down: `docker compose ps` |
| A setting change didn't stick | It needs a restart. Or the compose sets that key, and rewrites it on every start: remove it there |
| Log says `Server empty for 60 seconds, pausing` | Normal: Minecraft's own pause. It resumes when someone joins |
| Boot fails: `No build found for version … with channel 'default'` | A beta build is pinned without `PAPER_CHANNEL: "experimental"` |
| Boot fails: `UnsupportedClassVersionError` | Wrong Java: the image tag must be `stable-java25` or newer |
| Settings says `HTTP 401` | The GitHub token expired or lacks Contents / Pull requests (read and write). Renew it, then `docker compose up -d` |
| A Settings pull request has a merge conflict | Another change touched the same line first. Close it and save again |
| A container is `unhealthy` | `docker compose ps` names it; `docker compose logs <service>` says why |
