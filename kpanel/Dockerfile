# kpanel: built by docker compose from this directory (see the compose file's `build:`).
FROM python:3.13-alpine

# Pinned in requirements.txt: PyYAML, and argon2 for the login's password hash.
# --only-binary: the compiled ones must come as wheels; there is no compiler here.
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir --only-binary=argon2-cffi-bindings,cffi -r /tmp/requirements.txt

WORKDIR /app
# The root filesystem is read-only at run time (compose), so no .pyc files.
ENV PYTHONDONTWRITEBYTECODE=1
COPY app.py auth.py hashpw.py compose_edit.py filesproxy.py gamerules.py github.py history.py owners.py procstats.py rcon.py release.py settings.py ui.py \
     properties_meta.json gamerules_meta.json paper-logo.png favicon.png favicon.LICENSE.txt ui.LICENSE.txt ./

# A service account: uid 10001 in group 10000 (owners.PANEL), not 1000, which on
# most hosts is the first person's login. The server (itzg) runs as 10000:10000
# and the panel shares its process namespace for the CPU and memory graphs: a
# different uid means no server process, plugins included, can read any panel
# process's environment or memory. The shared group lets the panel write
# server.properties (0664, the server's default) and read the rest of /data.
RUN addgroup -g 10000 mc && adduser -D -u 10001 -G mc admin
# Dashboard history lives here (volume kpanel-state). Owned by the panel in the
# image, so a fresh named volume mounted over it inherits that ownership; the
# volume-init service re-owns one made by an older version.
RUN mkdir -p /var/lib/kpanel && chown 10001:10000 /var/lib/kpanel
USER 10001:10000

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]
CMD ["python", "-u", "app.py"]
