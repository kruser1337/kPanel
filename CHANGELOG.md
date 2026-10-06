# Changelog

## 0.4.0 — service accounts; no plain-text password

Follows up on a review of 0.3.0: two things 0.3 left half-done, and scans
that now run on every change. **Please update**, and read *Upgrading* first if
you use `compose/lan.yml` or a compose file of your own:

```sh
git pull && docker compose up -d --build
```

### Changed

- **Every service runs as a service account, not as uid 1000.** Docker
  doesn't remap users by default, so a container's uid is the same number on
  the host, and on most Linux machines 1000 is the first person's login: a
  process breaking out of the server's container would have been that person.
  The server, the backups and the file manager now run as uid 10000, the panel
  as 10001 in group 10000 (`kpanel/owners.py` has the ids, and a test keeps
  every file in line with it). A new one-shot service, `volume-init`, hands
  the volumes to those users before anything else starts, on every `up`, so
  older installs and restored backups are converted by themselves. It is root
  for a few seconds, runs only that conversion, has no network, and holds two
  capabilities: `CHOWN`, and `DAC_READ_SEARCH` to read through directories it
  doesn't own. The backup scheduler gives up its `CHOWN` to it. (Still root,
  as before: the backup scheduler, which the image requires, and the server's
  entrypoint until it drops to 10000.)
- **`KPANEL_BASIC_AUTH`, the plain-text password, is refused**, as 0.3
  announced. The panel doesn't start while it is set, not even next to
  `KPANEL_PASSWORD_HASH`: the password would still sit in `.env` and in the
  container's environment. The log says how to replace it. The panel's only
  login is now the argon2id hash; the code that compared a typed password with
  a stored clear-text one is gone.
- **Security scans in CI.** bandit (the panel's code), pip-audit (its pinned
  dependencies) and hadolint (its Dockerfile) run on every pull request and
  weekly; see *Scans* in the README. Six findings were reviewed and judged
  harmless; each is marked `# nosec` with the reason next to it.

### Upgrading

- **`KPANEL_BASIC_AUTH` still in `.env`?** The panel won't start (the server
  and backups run on). `docker compose logs kpanel` says so. Then:
  1. `docker compose run --rm --build hashpw`
  2. In `.env`, replace the `KPANEL_BASIC_AUTH=…` line with the line it prints,
     **including its single quotes**.
  3. `docker compose -f docker-compose.yml -f compose/lan.yml up -d`
- **Everyone:** the first start re-owns every file in the volumes;
  `docker compose logs volume-init` says how many it changed. Every later
  start reads through the volumes again without changing anything, which for
  a big world adds a moment to each start. `docker compose ps -a` lists
  `volume-init` as *Exited (0)*: that is how it should look. If you ran commands in the server
  as `-u 1000`, use `-u 10000` now.
- **Coolify, or any compose file you built by hand: carry the changes over in
  the same step as the new image.** The new panel image runs as 10001 in group
  10000, and without the rest it can't read the server's files. From the new
  `docker-compose.yml`, copy:
  - the `volume-init` service, with its `restart: "no"` (Coolify would
    otherwise restart it forever), and the `depends_on: volume-init` blocks of
    `mc`, `mc-backup`, `filebrowser` and `kpanel`;
  - `UID` and `GID` under `mc`'s `environment`;
  - `mc-backup`'s `cap_add` (no `CHOWN`), `CRON_BACKUP_UID: "10000"`, and the
    removal of its `entrypoint:`;
  - `filebrowser`'s `user:`, its `environment:` (`FILEBROWSER_CONFIG`) and the
    `cacheDir:` line of its config;
  - the volumes written in the long form with `nocopy`, exactly as they are
    (same `source:` keys; see [`docs/coolify.md`](docs/coolify.md)).

## 0.3.0 — security release

A security audit of the panel, prompted by reports from users, found that the
default setup could be taken over by a web page open on the same machine. An
independent check of the fixes then found gaps, which are closed here too.
What is fixed, what is only partly fixed, and what is left for later is below.
**Please update**:

```sh
git pull && docker compose up -d --build
```

### Fixed

- **DNS rebinding could take over a panel without a login (High).** A web page
  open on the machine running kPanel could point its own domain at
  `127.0.0.1` and then drive the panel from your browser: whitelist and op
  itself, change settings, restart the server, read the log. Without a login
  the panel now answers only to `localhost` and names in
  `KPANEL_ALLOWED_HOSTS`. Logins and Tailscale setups weren't affected.
- **The same attack reached the file manager, and through it the server
  (High).** The file manager (port 8081) had no login and could write to the
  server folder, plugins included. It is now served **by the panel, at
  `/files/`**, behind the panel's host check, login and cross-site check, and
  has no port of its own. No other site may frame it (clickjacking), and a
  file from the server folder opened in the browser runs sandboxed, not as
  the panel.
- **The panel password was stored in plain text (Medium).** It is now an
  argon2id hash, `KPANEL_PASSWORD_HASH`, made with
  `docker compose run --rm --build hashpw`. Passwords with non-ASCII characters
  (ä, é, …) now work; before, they could never log in. The plain-text
  `KPANEL_BASIC_AUTH` still works in 0.3, with a warning on every page (see
  *Not done yet*).
- **A Minecraft plugin could read the panel's secrets (Medium).** The panel
  shares the server's process namespace (for the CPU and memory graphs), and
  ran as the same user, so code in the server could read the login hash and a
  GitHub token from the environment of any panel process, and the password
  from `hashpw.py`'s memory while it was being typed. The panel now runs as
  its own user (uid 1001, in the server's group), and the kernel doesn't let
  the server's processes read another user's environment or memory. `hashpw` runs in a
  container of its own, outside the server's process namespace.
- **Other containers of the stack could use the panel and the file manager
  directly (Low).** Over the compose network, past the published port, code
  in the server could op players or open pull requests with the panel's
  token in the setups without a login. The panel now refuses requests from
  the stack's other services (behind Tailscale it answers only the sidecar),
  and the file manager is on a network only the panel shares (not on
  Coolify; see below).
- **The old file-manager password stayed on disk in plain text (Low).** With
  `compose/lan.yml`, 0.2 stored it in the file manager's database. That
  database is deleted on the first start of 0.3 (see Upgrading).
- Lower-severity fixes:
  - Cross-site posts from browsers that don't send fetch metadata (Safari
    before 16.4) are refused.
  - Five wrong passwords in a row from one address start a backoff (HTTP 429),
    and more than 30 failed logins a minute from all addresses together make
    everyone not already logged in wait. The memory this takes is bounded.
  - The example passwords from `.env.example` are refused.
  - Malformed or stalled requests can no longer tie up the panel.
  - The panel's pages have a Content Security Policy.
  - The `Tailscale-User-Login` header is trusted only behind Tailscale.
  - `FILES_PASSWORD` is no longer pasted unescaped into the file manager's
    YAML config (the file manager no longer has a password at all).
  - The panel container runs with no capabilities on a read-only filesystem,
    and no container can gain privileges through setuid binaries.
  - Backups run as uid 1000 with no capabilities. Only their scheduler stays
    root (the backup image requires it), with three capabilities instead of
    Docker's default set. The backups volume is handed to uid 1000 on start;
    existing archives keep working.
  - `itzg/mc-backup` is pinned instead of tracking `latest`.
  - CI actions are pinned by commit.

### Not done yet, or only partly

- **The panel can still write most of the server folder.** Settings saves
  only `server.properties`, but the server creates its files group-writable,
  so code that takes over the panel can write into `plugins/` and so run code
  in the server. Keep the panel behind its login or on this machine.
- **Someone guessing passwords can keep you waiting.** Behind NAT or a
  reverse proxy all clients share one address, and the global limit acts on
  everyone anyway, so while someone keeps guessing, a browser that isn't
  already logged in gets HTTP 429. That is the price of the limit.
- **A container you add to the stack yourself can reach the panel** over the
  compose network: only the stack's own services are refused (see Fixed).
- **On Coolify, the stack's own containers can reach the file manager.**
  Coolify adds its own network to every service, so the file manager's
  separate network doesn't isolate it there. Only this stack's containers are
  on that network, and the only one that matters, the server, can already
  write the whole server folder; through the file manager it gains read
  access to the backups of its own world.
- **`KPANEL_BASIC_AUTH`, the plain-text login, is removed in 0.4**, not here,
  so that upgrading never locks anyone out.
- **No-login mode on Docker Engine older than 28.** Docker 28 drops packets
  for a `127.0.0.1`-published port that arrive from the network; Docker 27
  has no such rule, so a host on the same network segment may reach the
  panel. kPanel can't fix that; use Docker 28 or newer, or a login.

Nothing secret was ever committed to this repository; no key needs rotating
because of kPanel itself.

### Upgrading

- **Base file only (no login):** nothing to do. The file manager moves from
  <http://localhost:8081> to <http://localhost:8080/files/> (the panel's
  **Files** link); update your bookmark.
- **With `compose/lan.yml` (`KPANEL_BASIC_AUTH` in `.env`):** your login keeps
  working, and every page shows a reminder until you swap it for a hash:
  1. `docker compose run --rm --build hashpw` (any machine with Docker and
     this repository will do; the stack needn't be running)
  2. In `.env`, replace the `KPANEL_BASIC_AUTH=…` line with the line it prints,
     **including its single quotes**.
  3. `docker compose -f docker-compose.yml -f compose/lan.yml up -d`

  The file manager is now `http://<host>:8080/files/`, behind the panel login.
  `FILES_PASSWORD` is no longer used; delete it from `.env`. **0.4 will refuse
  to start with `KPANEL_BASIC_AUTH`.**

  **Your old file-manager password was also stored in plain text** inside the
  file manager's own database (the `filebrowser-db` volume). On its first
  start, 0.3 deletes that database (the log says "removed the file manager's
  old database"); nothing in it is needed without a login. That is an ordinary
  file deletion, not a secure erase, so **if you used that password anywhere
  else, change it there**. To be sure no copy is left, or if you run the file
  manager from a compose file of your own, remove the volume by hand (it is
  recreated empty):

  ```sh
  docker compose down
  docker volume ls --filter name=filebrowser-db   # e.g. kpanel_filebrowser-db
  docker volume rm kpanel_filebrowser-db          # the name it listed
  docker compose -f docker-compose.yml -f compose/lan.yml up -d
  ```
- **With `compose/tailscale.yml`:** nothing to do. The file manager moves from
  `:8443` to `https://<name>.<tailnet>/files/`.
- **Everyone:** the dashboard's graphs start over once. The panel now runs as
  its own user, which can't write the old `kpanel-data` volume, so its history
  (one day at most) moves to a new volume, `kpanel-state`. The old one can go:
  `docker volume ls --filter name=kpanel-data`, then `docker volume rm` the
  name it lists.
- **Plugins you don't fully trust?** Before 0.3, code in the server could read
  the panel's environment: `KPANEL_BASIC_AUTH` and, in git mode, the GitHub
  token. If that worries you, choose a new panel password and replace the
  token. The same applies if you ran a pre-release 0.3.0 from the
  `security-hardening` branch and made your hash there with
  `docker compose exec kpanel python hashpw.py`.
- **Coolify, or any compose file you built by hand:** an old file keeps
  working with the new panel image, except that dashboard history is no
  longer saved across restarts (the log says so once). To get the fixes:
  - Copy the `filebrowser` and `hashpw` services, the kpanel volume line
    `kpanel-state:/var/lib/kpanel` (and `kpanel-state:` under `volumes:`), the
    `networks:` lines and the top-level `networks:` block, and the
    `FILES_UPSTREAM`, `KPANEL_ALLOWED_HOSTS`, `KPANEL_TRUST_TS_HEADERS`,
    `KPANEL_REFUSE_PEERS` and (Tailscale) `KPANEL_ONLY_PEERS` lines from the
    new files (see [`docs/coolify.md`](docs/coolify.md)).
  - Settings writes `server.properties` through the server's group, so the
    file must stay group-writable, as the server creates it. If Settings says
    otherwise, run the `chmod` it names.
  - If you add `KPANEL_ALLOWED_HOSTS` with the wrong name, the panel shows a
    page naming the one to add.
- **Opening the panel by a LAN name or IP without a login?** That now gets a
  page saying which name to add to `KPANEL_ALLOWED_HOSTS`. This is the
  rebinding fix doing its job.

### Why the old password can't be hashed for you

The plain-text password sits in `.env` on your machine, and no container can
see or write that file, by design. If the panel hashed it at startup, the
plain text would still be in `.env`, so nothing would be gained. The one-time
command above is the safe way, and you keep a working login the whole time.

## 0.2.2 and earlier

See the [releases](https://github.com/kruser1337/kPanel/releases).
