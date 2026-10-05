# Changelog

## 0.3.0 — security release

A security audit of the panel, prompted by reports from users, found that the
default setup could be taken over by a web page open on the same machine.
Every finding is fixed here. **Please update**:

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
  `/files/`**, behind the same checks and login, and has no port of its own.
- **The panel password was stored in plain text (Medium).** It is now an
  argon2id hash, `KPANEL_PASSWORD_HASH`, made by `hashpw.py`. Passwords with
  non-ASCII characters (ä, é, …) now work; before, they could never log in.
- **A Minecraft plugin could read the panel's secrets (Medium).** The panel
  shares the server's process namespace and user, so code in the server could
  read the panel's environment: the login and a GitHub token. The panel now
  hides its process from the server's.
- Lower-severity fixes:
  - Cross-site posts from browsers that don't send fetch metadata (Safari
    before 16.4) are refused.
  - Five wrong passwords in a row start a backoff (HTTP 429).
  - The example passwords from `.env.example` are refused.
  - Malformed or stalled requests can no longer tie up the panel.
  - A Content Security Policy has been added.
  - The `Tailscale-User-Login` header is trusted only behind Tailscale.
  - `FILES_PASSWORD` is no longer pasted unescaped into the file manager's
    YAML config.
  - The panel container runs with no capabilities on a read-only filesystem,
    and no container can gain privileges.
  - `itzg/mc-backup` is pinned instead of tracking `latest`.
  - CI actions are pinned by commit.

Nothing secret was ever committed to this repository; no key needs rotating
because of kPanel itself.

### Upgrading

- **Base file only (no login):** nothing to do. The file manager moves from
  <http://localhost:8081> to <http://localhost:8080/files/> (the panel's
  **Files** link); update your bookmark.
- **With `compose/lan.yml` (`KPANEL_BASIC_AUTH` in `.env`):** your login keeps
  working, and every page shows a reminder until you swap it for a hash:
  1. `docker compose exec kpanel python hashpw.py`
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
- **Coolify, or any compose file you built by hand:** an old file keeps working
  as it is. To get the fixes:
  - Copy the `filebrowser` service and the `FILES_UPSTREAM`,
    `KPANEL_ALLOWED_HOSTS` and `KPANEL_TRUST_TS_HEADERS` lines from the new
    files (see [`docs/coolify.md`](docs/coolify.md)).
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
