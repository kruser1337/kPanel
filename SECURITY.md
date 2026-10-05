# Security policy

## Reporting a vulnerability

Please report security problems **privately**:

- **GitHub:** the repository's **Security** tab → **Report a vulnerability**
  (private vulnerability reporting).

Please don't open a public issue or post details on Reddit or Discord until a
fix is released. Helpful things to include:

- what an attacker can do, and from where (same machine, LAN, internet);
- which setup it affects (base file, `compose/lan.yml`, `compose/tailscale.yml`);
- steps to reproduce, or a proof of concept;
- the kPanel version (the panel's footer, or `kpanel/release.py`).

You'll get an answer within a week. Once a fix is out, the release notes and
[`CHANGELOG.md`](CHANGELOG.md) credit you, unless you'd rather not be named.

## Supported versions

Only the latest release gets security fixes. Updating is
`git pull && docker compose up -d --build`; see
[Upgrading](docs/operations.md#upgrading).

## Scope

In scope: the panel (`kpanel/`), the compose files and overlays, and the docs
whenever following them leaves a setup less safe than they say.

Out of scope: vulnerabilities in the upstream images themselves
([itzg/minecraft-server](https://github.com/itzg/docker-minecraft-server),
[itzg/mc-backup](https://github.com/itzg/docker-mc-backup),
[FileBrowser Quantum](https://github.com/gtsteffaniak/filebrowser),
[Tailscale](https://github.com/tailscale/tailscale)), Minecraft, and plugins.
Please report those upstream. If kPanel's configuration of one of them is the
problem, that is in scope.

The threat model and what is stored how are in the README's
[Security](README.md#security) section.
