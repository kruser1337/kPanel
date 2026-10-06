"""Hand the stack's volumes to its service accounts, then exit.

    python owners.py     (the compose file's volume-init service, as root)

The stack runs as service accounts with high ids, not as uid 1000: on most
Linux hosts 1000 is the first person's login, and without user-namespace
remapping a container's uid is the host's, so a process that escaped a
container (or a file left on the host) would be that person's. Up to 0.3 the
server, backups and file manager ran as 1000 and the panel as 1001.

The ids live here and nowhere else; test_compose checks that the compose file
and the Dockerfile use the same ones. This runs before the services that use
the volumes, on every `up`, so it also upgrades volumes made by older versions.
Only files with another owner are touched, so after the first run it changes
nothing. It still reads through every volume on every `up`, the world
included: for a big one that adds a moment to each deploy, and to the server's
downtime when the server is recreated.

/data is here too although the itzg image re-owns it for UID and GID: the image
first runs `usermod -u`, which re-owns the files in the user's home (/data) by
uid only, and then skips its own chown because /data has the right uid. Every
file would keep group 1000, and the panel, which writes server.properties
through the group, couldn't.
"""

import os
import sys

GROUP = 10000            # shared: the panel reads and writes the server's files through it
SERVER = (10000, GROUP)  # the Minecraft server, the backups, the file manager
PANEL = (10001, GROUP)   # its own uid: no server process can read the panel's (see app.py)

# Where the volume-init service mounts each volume, and who gets it.
VOLUMES = {
    "/volumes/mc-data": SERVER,
    "/volumes/mc-backups": SERVER,
    "/volumes/filebrowser-db": SERVER,
    "/volumes/kpanel-state": PANEL,
}


def hand_over(top, uid, gid, chown=os.chown):
    """Give every file under `top` to uid:gid; return how many changed.

    The volumes are in use while this runs (the server, backups, uploads), so
    files come and go and a directory can be swapped for a symlink mid-walk.
    Each entry is looked at and re-owned relative to its directory's open file
    descriptor, without following symlinks (a link is re-owned itself), and
    fwalk checks a directory is still the one it listed before entering it. So
    nothing outside the volume is changed, and a file that is gone by the time
    it is reached is skipped.
    """
    changed = 0
    if _hand(top, None, uid, gid, chown):
        changed += 1
    for _root, dirs, files, fd in os.fwalk(top):
        for name in dirs + files:
            if _hand(name, fd, uid, gid, chown):
                changed += 1
    return changed


def _hand(name, dir_fd, uid, gid, chown):
    try:
        st = os.stat(name, dir_fd=dir_fd, follow_symlinks=False)
        if (st.st_uid, st.st_gid) == (uid, gid):
            return False
        chown(name, uid, gid, dir_fd=dir_fd, follow_symlinks=False)
        return True
    except FileNotFoundError:
        return False


def main(volumes=VOLUMES):
    """0 when every volume is handed over; 1 when one isn't mounted (a compose
    file that lost a line here would otherwise start on unconverted volumes)."""
    missing = [top for top in volumes if not os.path.isdir(top)]
    for top in missing:
        print(f"volume-init: {top} is not mounted; check the volume-init service's volumes:", flush=True)
    for top, (uid, gid) in volumes.items():
        if top in missing:
            continue
        n = hand_over(top, uid, gid)
        if n:
            print(f"volume-init: handed {n} file(s) in {top} to {uid}:{gid}", flush=True)
    return 1 if missing else 0


if __name__ == "__main__":
    sys.exit(main())
