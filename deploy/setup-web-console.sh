#!/usr/bin/env bash
#
# One-paste setup for the Moybyte LIVE web console on a fresh Ubuntu box
# (AWS Lightsail "Launch script", EC2 user-data, or just run it by hand).
#
# It installs the shared console (the same UI the T-Deck runs) and serves it to
# your browser over the draw-command WebSocket transport -- a real, interactive
# desktop, not a video. Tested on Ubuntu 22.04 / 24.04.
#
# WHAT YOU GET:  http://<public-ip>:8080/  -> the full windowed desktop, live.
#
# ---------------------------------------------------------------------------
# EDIT THESE TWO LINES before pasting (nothing else is required):
# ---------------------------------------------------------------------------
#   REPO_URL: this repo is PRIVATE, so plain https will 401. Use ONE of:
#     * a fine-grained PAT (read-only, Contents):
#         https://<TOKEN>@github.com/nikola-j/moybyte.git
#     * or leave the https URL and add a deploy key to the box yourself first.
REPO_URL="${REPO_URL:-https://github.com/nikola-j/moybyte.git}"
BRANCH="${BRANCH:-master}"          # master = stable; use a branch to preview WIP
# ---------------------------------------------------------------------------
PORT="${PORT:-8080}"
SIZE="${SIZE:-1024x600}"            # desktop canvas; 320x240 = the raw T-Deck panel
APP_DIR="${APP_DIR:-/opt/moybyte}"
RUN_USER="${RUN_USER:-moybyte}"
set -euo pipefail

echo "== [1/5] system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -y
# pygame/Pillow ship self-contained wheels, so the host needs only python + git
# + a compiler (in case any dep builds from source). No display / GPU needed.
apt-get install -y --no-install-recommends \
  git python3 python3-venv python3-dev build-essential curl ca-certificates

echo "== [2/5] service user + checkout ($BRANCH)"
id -u "$RUN_USER" >/dev/null 2>&1 || useradd --system --create-home --shell /usr/sbin/nologin "$RUN_USER"
if [ ! -d "$APP_DIR/.git" ]; then
  git clone --branch "$BRANCH" "$REPO_URL" "$APP_DIR"
else
  git -C "$APP_DIR" fetch --all --prune && git -C "$APP_DIR" checkout "$BRANCH" && git -C "$APP_DIR" pull --ff-only
fi
chown -R "$RUN_USER:$RUN_USER" "$APP_DIR"

echo "== [3/5] python env (make setup)"
sudo -u "$RUN_USER" bash -lc "cd '$APP_DIR' && make setup"
# Pillow is only needed if you also render GIFs on the box; harmless to include.
sudo -u "$RUN_USER" bash -lc "cd '$APP_DIR' && .venv/bin/pip -q install Pillow" || true

echo "== [4/5] systemd service"
cat >/etc/systemd/system/moybyte-web.service <<UNIT
[Unit]
Description=Moybyte live web console (draw-command streaming)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$APP_DIR
Environment=SDL_VIDEODRIVER=dummy
Environment=SDL_AUDIODRIVER=dummy
ExecStart=$APP_DIR/.venv/bin/python tools/web_console.py --host 0.0.0.0 --port $PORT --windowed --size $SIZE
Restart=on-failure
RestartSec=3
# hardening
NoNewPrivileges=true
ProtectSystem=full
PrivateTmp=true

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable --now moybyte-web.service

echo "== [5/5] done"
IP="$(curl -s --max-time 4 http://checkip.amazonaws.com || echo '<public-ip>')"
cat <<DONE

  Moybyte live console is up:  http://${IP}:${PORT}/

  OPEN THE FIREWALL for TCP ${PORT}:
    * Lightsail : Instance -> Networking -> add rule (Custom TCP ${PORT})
    * EC2       : the instance's Security Group -> inbound TCP ${PORT}
    Scope the source to YOUR IP -- the console has no auth, anyone who can
    reach the port can drive it. For a public URL, put Caddy/nginx in front
    with TLS + basic-auth (see deploy/README.md).

  Manage it:
    systemctl status moybyte-web       # health
    journalctl -u moybyte-web -f       # logs
    # update to latest:
    sudo -u ${RUN_USER} git -C ${APP_DIR} pull --ff-only && systemctl restart moybyte-web
DONE
