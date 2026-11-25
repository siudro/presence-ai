# syntax=docker/dockerfile:1
#
# PRESENCE AI — deployed image.
#
# The app is standard library only: no requirements.txt, no `pip install`.
# That is a deliberate design rule of the project, not an oversight. This
# image only has to get three things right: a Python interpreter, an
# unprivileged user, and a writable data folder.

FROM python:3.12-slim

# Unbuffered output, so `railway logs` shows the startup banner immediately
# instead of holding it in a buffer until the buffer fills.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONIOENCODING=utf-8 \
    DATA_DIR=/data \
    PORT=8080

# Copied in as the unprivileged user from the start. Nothing in this app needs
# root, and code the service cannot write to cannot be changed by a request.
COPY --chown=10001:10001 presence/ /srv/preface-tmp/presence/
RUN mv /srv/preface-tmp/presence /srv/presence && rmdir /srv/preface-tmp \
    && mkdir -p /data \
    && chown -R 10001:10001 /srv /data \
    && chmod -R u+rwX,go+rX,go-w /srv/presence

WORKDIR /srv/presence

# A volume Railway mounts arrives owned by root, which uid 10001 cannot write
# to. The entrypoint fixes ownership (as root, for that one step) and then
# drops to 10001 before Python starts.
COPY --chown=10001:10001 deploy/entrypoint.sh /srv/entrypoint.sh
USER root
RUN chmod 0755 /srv/entrypoint.sh
USER 10001:10001
ENTRYPOINT ["/srv/entrypoint.sh"]

# app.py:main reads $PORT and binds $HOST (default 0.0.0.0). Railway injects
# PORT; no EXPOSE is required, but it documents the default.
EXPOSE 8080
CMD ["python3", "app.py"]
