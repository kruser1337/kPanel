# Reaching the panel over Tailscale

`compose/tailscale.yml` adds a [Tailscale](https://tailscale.com) sidecar that
puts the panel and the file manager on your tailnet, with real HTTPS
certificates, reachable from any of your devices wherever they are:

- `https://<TS_HOSTNAME>.<TS_TAILNET>/`, the panel
- `https://<TS_HOSTNAME>.<TS_TAILNET>/files/`, the file manager (served by the
  panel; until 0.2 it was on port 8443)

The tailnet is the boundary: only your devices can reach either one, so neither
asks for a login, and nothing but the game port is published on the host.

## Setup

1. **Turn on HTTPS** for your tailnet: admin console → DNS → enable MagicDNS
   and HTTPS certificates.
2. **Create an auth key**: admin console → Settings → Keys → *Generate auth
   key*. Make it **tagless** (no tags). Single-use is enough, because the login
   is saved in a volume and the key is only used on first boot.
3. **Fill in `.env`** (copy [`.env.example`](../.env.example)):
   ```sh
   TS_HOSTNAME=kpanel                # the device name you want
   TS_TAILNET=tail1234.ts.net        # admin console → DNS → "Tailnet name"
   TS_AUTHKEY=tskey-auth-...
   ```
4. **Start it:**
   ```sh
   docker compose -f docker-compose.yml -f compose/tailscale.yml up -d
   ```
5. In the admin console, open the new device's menu and **disable key
   expiry**. Otherwise it drops off the tailnet after 180 days.

The first HTTPS request can take a few seconds while the certificate is issued.

## Things that go wrong

- **A tagged auth key makes the sidecar hang before login, with no error**, if
  the tag has no `tagOwners` entry in your ACL. Use a tagless key.
- **The device name is taken.** If the admin console already has a device
  called `TS_HOSTNAME` (an old one, say), the new one becomes `<name>-1` and the
  HTTPS config no longer matches its name. Delete the stale device first, then
  restart the sidecar.
- **Don't approve it through the browser login link.** Without an auth key the
  sidecar gives up after a minute and comes back with a new link, so the
  approval lands on an instance that no longer exists. Set `TS_AUTHKEY` instead.
- **Rebuilding from scratch** (the `tailscale-state` volume is gone): delete the
  old device, set a fresh auth key, start again.

```sh
# is it logged in, and is serve configured?
docker compose -f docker-compose.yml -f compose/tailscale.yml exec tailscale tailscale status --self --peers=false
docker compose -f docker-compose.yml -f compose/tailscale.yml exec tailscale tailscale serve status
```

The sidecar's healthcheck asks Tailscale itself whether the node has a tailnet
address, so `unhealthy` here means "not logged in", not just "process died".
