# kpanel: built by docker compose from this directory (see the compose file's `build:`).
FROM python:3.13-alpine

# Pinned. PyYAML is the only dependency; everything else is the stdlib.
RUN pip install --no-cache-dir pyyaml==6.0.2

WORKDIR /app
COPY app.py compose_edit.py gamerules.py github.py history.py procstats.py rcon.py release.py settings.py ui.py \
     properties_meta.json gamerules_meta.json paper-logo.png favicon.png favicon.LICENSE.txt ui.LICENSE.txt ./

# uid 1000, like the itzg image, so the panel can read and write /data/server.properties.
RUN adduser -D -u 1000 admin
# Dashboard history lives here (volume kpanel-data). Owned by 1000 in the image,
# so a fresh named volume mounted over it inherits that ownership.
RUN mkdir -p /var/lib/kpanel && chown 1000:1000 /var/lib/kpanel
USER 1000

EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/healthz', timeout=2)"
CMD ["python", "-u", "app.py"]
