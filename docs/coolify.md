# Deploying with Coolify

[Coolify](https://coolify.io) can run kPanel as a git-connected Application, so
merging a pull request (including the ones the panel's Settings page opens)
redeploys the server.

Two Coolify behaviours shape the setup:

- **It loads exactly one compose file.** Overlays can't be added with `-f`.
- **It skips any service that has a `profiles:` key**, silently.

So you give Coolify one standalone file, generated from the base plus the
overlay you want. Tailscale is the recommended way in: the panel stays off the
public internet, and no host port besides the game's is needed (on a Coolify
host, 8080 is often taken already).

## Setup

1. **Fork this repository**, and work in your fork.
2. **Make the file** Coolify will deploy: `cp docker-compose.yml compose/coolify.yml`,
   then in `compose/coolify.yml`:
   - delete the `ports:` list of `kpanel` (keep `mc`'s);
   - under `kpanel` → `environment`, set
     `COMPOSE_PATH: "compose/coolify.yml"`, so Settings' pull requests edit the
     file Coolify deploys,
     `KPANEL_ALLOWED_HOSTS: "${TS_HOSTNAME}.${TS_TAILNET}"` (without a
     login the panel answers only to the names listed here; anything else gets
     a page saying which name to add), `KPANEL_TRUST_TS_HEADERS: "1"` (the
     action log then names who made each change), and
     `KPANEL_ONLY_PEERS: "tailscale"` (the sidecar is then the only container
     the panel answers);
   - copy the `tailscale:` service from
     [`compose/tailscale.yml`](../compose/tailscale.yml) into `services:`, and
     `tailscale-state:` into `volumes:`.

   Keep the `KEY: "value"` style for environments: the Settings page edits those
   lines and does not understand the `- KEY=value` list form. (That is also why
   `docker compose config` is no shortcut here: it writes the list form.)
3. Commit and push it.
4. **In Coolify:** New Resource → your fork → build pack **Docker Compose**,
   *Docker Compose Location* `/compose/coolify.yml`.
5. **Set the variables** under Environment Variables before the first deploy:
   `TS_HOSTNAME`, `TS_TAILNET`, `TS_AUTHKEY` (see [`tailscale.md`](tailscale.md)),
   and optionally `MC_PUBLIC_HOST`, `TZ`, `KPANEL_GITHUB_REPO`,
   `KPANEL_GITHUB_TOKEN`.
6. **Deploy**, then open `https://<TS_HOSTNAME>.<TS_TAILNET>/`.

From then on, changes to gameplay values go through the panel or a pull request
against `compose/coolify.yml`. When you pull a newer kPanel into your fork,
carry its changes to `docker-compose.yml` over into `compose/coolify.yml` (image pins, new settings).

## Things to know

- **Never rename a volume key.** Coolify names volumes `<app-uuid>_<key>`
  (`…_mc-data` is your world). A renamed key is a new, empty volume: a new
  world. Changing the *compose location* of the same Application is safe,
  because the UUID and keys stay the same. (kPanel 0.3 itself replaces
  `kpanel-data` with `kpanel-state` on purpose: the panel's new user can't
  write the old volume. It holds only the dashboard graphs, which start over.)
- **`volume-init` runs once per deploy and exits.** It hands the volumes to
  the service accounts before the other services start, so it shows as
  *Exited (0)*, which is right. Keep its `restart: "no"`: Coolify gives a
  service without a `restart:` key `unless-stopped`, which would start it
  again every time it exits. If your Coolify reports the app as degraded
  because of the exited container, add `exclude_from_hc: true` to
  `volume-init` in `compose/coolify.yml` (Coolify's key for one-shot services;
  plain compose rejects it, so it isn't in `docker-compose.yml`). Each deploy
  reads through every volume once, the world included.
- **Some volumes are in the long form** (`type: volume`, `source:`,
  `target:`, `volume: {nocopy: true}`). Copy them into `compose/coolify.yml`
  as they are: without `nocopy`, a fresh volume gets the image's owner and the
  panel can't read `/data`. The `source:` is the volume key, so the same
  never-rename rule applies.
- **The `hashpw` service** has a profile, so Coolify skips it, as it should:
  it is a one-off tool. Make the login hash in a local checkout with
  `docker compose run --rm --build hashpw`; the hash isn't tied to the host.
- **A new `${VARIABLE}` arrives empty.** Coolify creates each variable when a
  deploy first parses the file, with no value, so the deploy that introduces it
  runs without it. Merge, set the value, then deploy again.
- **`${VARIABLE:?message}`**: the overlays use this so plain compose refuses to
  start without a value. If your Coolify version doesn't accept that form,
  replace it with `${VARIABLE}` in `compose/coolify.yml`.
- **Restart vs. deploy.** Coolify's *Restart* restarts the containers; enough
  after file changes (Paper YAML, plugin configs). A changed environment variable
  needs a **Deploy**.
- **The file manager isn't network-isolated here.** Coolify attaches every
  service to a network of its own (named after the app), on top of the ones
  in the file, so `mc` and `mc-backup` can reach `filebrowser` directly, past
  the panel. Other Coolify apps can't: that network is per app. The server
  gains nothing much from it (it already writes `/data`; the file manager adds
  read access to `/backups`), but the panel's checks don't apply on that path.
- **"running:healthy" can mislead.** A service Coolify never created (a profiled
  one, say) can't be unhealthy. After a deploy, check the panel and the file
  manager actually answer.
- **Logs:** Coolify's log view may show only some containers. The panel's Logs
  page has the server log; for the rest, Coolify's terminal or
  `docker logs` on the host.
- **Running commands:** Coolify → your app → Terminal → the `mc` container,
  then `rcon-cli list` and the like (see [`operations.md`](operations.md)).
