# kpanel: built by docker compose from this directory (see the compose file's `build:`).
FROM python:3.13-alpine

# Pinned in requirements.txt: PyYAML, and argon2 for the login's password hash.
# --only-binary: the compiled ones must come as wheels; there is no compiler here.
COPY requirements.txt /tmp/
RUN pip install --no-cache-dir --only-binary=argon2-cffi-bindings,cffi -r /tmp/requirements.txt

WORKDIR /app
# The root filesystem is read-only at run time (compose), so no .pyc files.
ENV PYTHONDONTWRITEBYTECODE=1
COPY app.py auth.py hashpw.py compose_edit.py filesproxy.py gamerules.py github.py history.py procstats.py rcon.py release.py settings.py ui.py \
     properties_meta.json gamerules_meta.json paper-logo.png favicon.png favicon.LICENSE.txt ui.LICENSE.txt ./

# uid 1001 in group 1000. The server (itzg) runs as 1000:1000 and the panel shares
# its process namespace for the CPU and memory graphs: a different uid means no
# server process, plugins included, can read any panel process's environment or
# memory. Group 1000 lets the panel write server.properties (0664, the server's
# default) and read the rest of /data.
RUN addgroup -g 1000 mc && adduser -D -u 1001 -G mc admin
# Dashboard history lives here (volume kpanel-state). Owned by the panel in the
# image, so a fresh named volume mounted over it inherits that ownership.
RUN mkdir -p /var/lib/kpanel && chown 1001:1000 /var/lib/kpanel
# The server's volumes, owned as in the itzg images. Podman hands a fresh, empty
# named volume to the first container that mounts it, as that container's
# mount point is owned: without these that could be the panel's 1001, and the
# server could not write its own /data.
RUN mkdir -p /data /backups && chown 1000:1000 /data /backups && chmod 750 /data
USER 1001:1000

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"]
CMD ["python", "-u", "app.py"]
