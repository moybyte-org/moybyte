#!/usr/bin/env bash
#
# proxmox-setup.sh -- create a minimal Proxmox LXC for the Moybyte web console
# + Claude Code, carefully.
#
# RUN THIS ON THE PROXMOX HOST (it uses pct/pveam/pvesm/pvesh), as root.
#
# "Carefully" means: it validates the host, the target storages, the bridge and
# the container id BEFORE creating anything; it NEVER overwrites an existing
# container; it downloads the Debian template only if it is missing; it asks for
# one confirmation (skip with --yes); and it keeps your Git token off the command
# line / process list (fed over stdin, stored 0600 inside the container only).
#
# Usage:
#   ./proxmox-setup.sh [--yes] [--dry-run]
#
# Everything is configured with environment variables (all optional):
#   CTID=200                 container id            (default: next free id)
#   CT_HOSTNAME=moybyte
#   CORES=2  MEMORY=2048  SWAP=512  DISK=16          (MB / MB / GB)
#   STORAGE=local-lvm        rootfs storage (needs content 'rootdir'); auto if unset
#   TEMPLATE_STORAGE=local   template storage (needs content 'vztmpl'); auto if unset
#   BRIDGE=vmbr0
#   IP=dhcp                  or static: IP=192.168.1.50/24 GATEWAY=192.168.1.1
#   NAMESERVER=1.1.1.1       optional DNS for the CT
#   SSH_KEYS=/root/.ssh/id_ed25519.pub   optional pubkey file to inject
#   UNPRIVILEGED=1           (0 only if you know you need it)
#   WEB_PORT=8080
#
#   # Web console (optional -- the repo is PRIVATE, so give a read-only token):
#   REPO_URL=https://github.com/nikola-j/moybyte.git
#   BRANCH=master
#   GIT_TOKEN=github_pat_...   fine-grained, read-only (Contents). Omit to skip the
#                              web console and only install Claude Code + deps.
#
# Re-running with the same CTID is refused (no clobber). Destroy first if you mean it:
#   pct stop <CTID>; pct destroy <CTID>

set -euo pipefail

# ---- defaults ---------------------------------------------------------------
CT_HOSTNAME="${CT_HOSTNAME:-moybyte}"
CORES="${CORES:-2}"
MEMORY="${MEMORY:-2048}"
SWAP="${SWAP:-512}"
DISK="${DISK:-16}"
BRIDGE="${BRIDGE:-vmbr0}"
IP="${IP:-dhcp}"
GATEWAY="${GATEWAY:-}"
NAMESERVER="${NAMESERVER:-}"
SSH_KEYS="${SSH_KEYS:-}"
UNPRIVILEGED="${UNPRIVILEGED:-1}"
WEB_PORT="${WEB_PORT:-8080}"
BRANCH="${BRANCH:-master}"
REPO_URL="${REPO_URL:-}"
GIT_TOKEN="${GIT_TOKEN:-}"
ASSUME_YES=0
DRY_RUN=0

# ---- pretty logging ---------------------------------------------------------
if [ -t 1 ]; then C_R=$'\033[31m'; C_Y=$'\033[33m'; C_G=$'\033[32m'; C_B=$'\033[36m'; C_0=$'\033[0m'
else C_R=; C_Y=; C_G=; C_B=; C_0=; fi
info() { printf '%s==>%s %s\n' "$C_B" "$C_0" "$*"; }
ok()   { printf '%s ok%s  %s\n' "$C_G" "$C_0" "$*"; }
warn() { printf '%swarn%s %s\n' "$C_Y" "$C_0" "$*" >&2; }
die()  { printf '%serr%s  %s\n' "$C_R" "$C_0" "$*" >&2; exit 1; }
run()  { if [ "$DRY_RUN" = 1 ]; then printf '%s(dry-run)%s %s\n' "$C_Y" "$C_0" "$*"; else eval "$@"; fi; }

for a in "$@"; do
  case "$a" in
    --yes|-y) ASSUME_YES=1 ;;
    --dry-run|-n) DRY_RUN=1 ;;
    -h|--help) sed -n '2,40p' "$0"; exit 0 ;;
    *) die "unknown argument: $a (see --help)" ;;
  esac
done

# ---- 1. host sanity ---------------------------------------------------------
[ "$(id -u)" = 0 ] || die "run as root on the Proxmox host."
for c in pct pveam pvesm; do command -v "$c" >/dev/null || die "'$c' not found -- this must run on a Proxmox VE host."; done
command -v pveversion >/dev/null && ok "Proxmox VE: $(pveversion | head -1)"

numeric() { case "$1" in ''|*[!0-9]*) return 1 ;; *) return 0 ;; esac; }
for v in CORES MEMORY SWAP DISK WEB_PORT; do numeric "${!v}" || die "$v must be a number (got '${!v}')"; done
[ "$CORES" -ge 1 ] || die "CORES must be >= 1"
[ "$MEMORY" -ge 512 ] || warn "MEMORY=${MEMORY}MB is very low; Node/npm installs may struggle (>=1024 suggested)."

# ---- 2. choose + validate the container id ----------------------------------
if [ -z "${CTID:-}" ]; then
  CTID="$(pvesh get /cluster/nextid 2>/dev/null || true)"
  if [ -z "$CTID" ]; then CTID=100; while pct status "$CTID" >/dev/null 2>&1 || qm status "$CTID" >/dev/null 2>&1; do CTID=$((CTID+1)); done; fi
fi
numeric "$CTID" || die "CTID must be numeric (got '$CTID')"
[ "$CTID" -ge 100 ] || die "CTID must be >= 100"
if pct status "$CTID" >/dev/null 2>&1; then
  die "CT $CTID already exists -- refusing to touch it. Pick another CTID, or destroy it first."
fi
if qm status "$CTID" >/dev/null 2>&1; then die "id $CTID is already a VM. Pick another CTID."; fi
ok "container id: $CTID (free)"

# ---- 3. validate / auto-pick storages ---------------------------------------
storage_supports() { pvesm status --content "$2" 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$1"; }
first_storage_for() { pvesm status --content "$1" 2>/dev/null | awk 'NR>1{print $1; exit}'; }

if [ -z "${STORAGE:-}" ]; then
  STORAGE="$(first_storage_for rootdir)"; [ -n "$STORAGE" ] || die "no storage supports container rootfs ('rootdir'). Set STORAGE=."
  ok "rootfs storage (auto): $STORAGE"
else
  storage_supports "$STORAGE" rootdir || die "storage '$STORAGE' does not exist or does not allow 'rootdir'. Check: pvesm status --content rootdir"
  ok "rootfs storage: $STORAGE"
fi
if [ -z "${TEMPLATE_STORAGE:-}" ]; then
  TEMPLATE_STORAGE="$(first_storage_for vztmpl)"; [ -n "$TEMPLATE_STORAGE" ] || die "no storage supports templates ('vztmpl'). Set TEMPLATE_STORAGE=."
  ok "template storage (auto): $TEMPLATE_STORAGE"
else
  storage_supports "$TEMPLATE_STORAGE" vztmpl || die "storage '$TEMPLATE_STORAGE' does not allow 'vztmpl'. Check: pvesm status --content vztmpl"
  ok "template storage: $TEMPLATE_STORAGE"
fi

# ---- 4. validate the bridge -------------------------------------------------
[ -d "/sys/class/net/$BRIDGE" ] || die "network bridge '$BRIDGE' not found. Set BRIDGE= to one of: $(ls /sys/class/net | tr '\n' ' ')"
ok "bridge: $BRIDGE   ip: $IP"

# ---- 5. find or download the Debian 12 template -----------------------------
TEMPLATE_VOLID="$(pveam list "$TEMPLATE_STORAGE" 2>/dev/null | awk '/debian-12-standard/{print $1; exit}' || true)"
if [ -n "$TEMPLATE_VOLID" ]; then
  ok "template present: $TEMPLATE_VOLID"
else
  info "no debian-12 template on $TEMPLATE_STORAGE; looking one up..."
  run "pveam update >/dev/null"
  TPL_NAME="$(pveam available --section system 2>/dev/null | awk '/debian-12-standard/{print $2}' | sort -V | tail -1 || true)"
  [ -n "$TPL_NAME" ] || die "could not find a debian-12-standard template via 'pveam available'."
  info "will download template: $TPL_NAME"
  run "pveam download '$TEMPLATE_STORAGE' '$TPL_NAME' >/dev/null"
  TEMPLATE_VOLID="${TEMPLATE_STORAGE}:vztmpl/${TPL_NAME}"
  ok "template ready: $TEMPLATE_VOLID"
fi

# ---- 6. build the pct create argument list ----------------------------------
NET="name=eth0,bridge=${BRIDGE},ip=${IP}"
[ "$IP" != dhcp ] && [ -n "$GATEWAY" ] && NET="${NET},gw=${GATEWAY}"

create_args=(
  "$CTID" "$TEMPLATE_VOLID"
  --hostname "$CT_HOSTNAME"
  --cores "$CORES" --memory "$MEMORY" --swap "$SWAP"
  --rootfs "${STORAGE}:${DISK}"
  --net0 "$NET"
  --features nesting=1,keyctl=1
  --unprivileged "$UNPRIVILEGED"
  --onboot 1
)
[ -n "$NAMESERVER" ] && create_args+=(--nameserver "$NAMESERVER")
[ -n "$SSH_KEYS" ] && { [ -f "$SSH_KEYS" ] || die "SSH_KEYS file not found: $SSH_KEYS"; create_args+=(--ssh-public-keys "$SSH_KEYS"); }

# ---- 7. confirm -------------------------------------------------------------
cat <<SUMMARY

  ${C_B}Plan${C_0}
    container   $CTID  ($CT_HOSTNAME, $( [ "$UNPRIVILEGED" = 1 ] && echo unprivileged || echo PRIVILEGED ))
    resources   ${CORES} vCPU / ${MEMORY} MB RAM / ${SWAP} MB swap / ${DISK} GB disk
    storage     rootfs=$STORAGE  template=$TEMPLATE_STORAGE
    network     $NET
    template    $TEMPLATE_VOLID
    web console $( [ -n "$REPO_URL" ] && echo "yes  ($REPO_URL @ $BRANCH, port $WEB_PORT)" || echo "no (set REPO_URL + GIT_TOKEN to enable)" )
    claude code yes

SUMMARY
if [ -n "$REPO_URL" ] && [ -z "$GIT_TOKEN" ]; then
  warn "REPO_URL set but GIT_TOKEN empty; a private repo clone will fail. Provide a read-only token or unset REPO_URL."
fi
if [ "$ASSUME_YES" != 1 ] && [ "$DRY_RUN" != 1 ]; then
  read -r -p "Proceed? [y/N] " ans; case "$ans" in y|Y|yes) ;; *) die "aborted."; esac
fi

# ---- 8. create + start ------------------------------------------------------
info "creating container $CTID ..."
run "pct create ${create_args[*]}"
info "starting container $CTID ..."
run "pct start '$CTID'"

if [ "$DRY_RUN" = 1 ]; then info "dry-run complete; nothing was created."; exit 0; fi

# wait for the network to come up (DHCP can lag a few seconds)
info "waiting for the container network ..."
CT_IP=""
for _ in $(seq 1 30); do
  CT_IP="$(pct exec "$CTID" -- ip -4 -o addr show dev eth0 2>/dev/null | awk '{print $4}' | cut -d/ -f1 | head -1 || true)"
  [ -n "$CT_IP" ] && break
  sleep 2
done
[ -n "$CT_IP" ] || warn "could not read the container IP yet; check later with: pct exec $CTID -- ip a"
[ -n "$CT_IP" ] && ok "container IP: $CT_IP"

# ---- 9. in-container bootstrap (deps + Claude Code) --------------------------
# A secret-free bootstrap script pushed into the CT and executed there.
BOOTSTRAP="$(mktemp)"; trap 'rm -f "$BOOTSTRAP"' EXIT
cat > "$BOOTSTRAP" <<'GUEST'
set -euo pipefail
export DEBIAN_FRONTEND=noninteractive
echo "== apt base packages"
apt-get update -y
apt-get install -y --no-install-recommends \
  curl git python3 python3-venv python3-dev build-essential ca-certificates gnupg

echo "== Node.js 20 (for Claude Code)"
if ! command -v node >/dev/null; then
  curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
  apt-get install -y nodejs
fi
echo "== Claude Code"
npm install -g @anthropic-ai/claude-code >/dev/null 2>&1 || npm install -g @anthropic-ai/claude-code
node --version; claude --version || true

# Optional web console. The bootstrap sources /root/.mb-env (0600, pushed over
# stdin) ONLY if it exists; the token never appears on any command line.
if [ -f /root/.mb-env ]; then
  set -a; . /root/.mb-env; set +a
  if [ -n "${REPO_URL:-}" ] && [ -n "${GIT_TOKEN:-}" ]; then
    echo "== web console: clone + service"
    HOST="$(printf '%s' "$REPO_URL" | sed -E 's#https?://##; s#/.*##')"
    # store creds in a 0600 file (NOT in the repo remote URL) so a token scrub is trivial
    umask 077; printf 'https://x-access-token:%s@%s\n' "$GIT_TOKEN" "$HOST" > /root/.git-credentials
    git config --global credential.helper store
    git config --global --add safe.directory /opt/moybyte || true
    # Run the repo's own careful setup script (creates the moybyte service user,
    # builds the venv, installs the systemd unit on the chosen port).
    if [ ! -d /opt/moybyte/.git ]; then
      git clone --branch "${BRANCH:-master}" "$REPO_URL" /opt/moybyte
    fi
    PORT="${WEB_PORT:-8080}" REPO_URL="$REPO_URL" BRANCH="${BRANCH:-master}" \
      bash /opt/moybyte/deploy/setup-web-console.sh || { echo "web-console setup failed"; exit 1; }
    # The remote is the plain (tokenless) URL -- auth comes from the 0600 credential
    # store, so nothing to scrub. Give the service user its OWN 0600 store so that
    # `sudo -u moybyte git pull` updates keep working.
    if id moybyte >/dev/null 2>&1; then
      install -o moybyte -g moybyte -m 600 /root/.git-credentials /home/moybyte/.git-credentials
      sudo -u moybyte git config --global credential.helper store || true
    fi
  fi
  shred -u /root/.mb-env 2>/dev/null || rm -f /root/.mb-env
fi
echo "== bootstrap done"
GUEST

# push the token over stdin into a 0600 env file (keeps it off argv / host ps)
if [ -n "$REPO_URL" ] && [ -n "$GIT_TOKEN" ]; then
  info "staging web-console config (token via stdin, stored 0600 in the CT) ..."
  printf 'REPO_URL=%s\nBRANCH=%s\nWEB_PORT=%s\nGIT_TOKEN=%s\n' "$REPO_URL" "$BRANCH" "$WEB_PORT" "$GIT_TOKEN" \
    | pct exec "$CTID" -- bash -c 'umask 077; cat > /root/.mb-env'
fi

info "running in-container bootstrap (installs deps, Node, Claude Code$( [ -n "$REPO_URL" ] && echo ", web console" ) ) ..."
pct push "$CTID" "$BOOTSTRAP" /root/mb-bootstrap.sh
pct exec "$CTID" -- bash /root/mb-bootstrap.sh
pct exec "$CTID" -- rm -f /root/mb-bootstrap.sh

# ---- 10. summary ------------------------------------------------------------
cat <<DONE

${C_G}Done.${C_0}  Container $CTID ($CT_HOSTNAME) is up.

  Enter it:            pct enter $CTID
  Claude Code:         run 'claude' inside the CT and log in (or set ANTHROPIC_API_KEY)
DONE
if [ -n "$REPO_URL" ]; then
  cat <<DONE2
  Web console:         http://${CT_IP:-<ct-ip>}:${WEB_PORT}/
  Service health:      pct exec $CTID -- systemctl status moybyte-web
  Update it later:     pct exec $CTID -- sudo -u moybyte git -C /opt/moybyte pull --ff-only \\
                         && pct exec $CTID -- systemctl restart moybyte-web

  ${C_Y}Note:${C_0} the console has NO auth -- keep port ${WEB_PORT} on your LAN only, or put
  Caddy/nginx (TLS + basic-auth) in front (see deploy/README.md). Revoke the
  fine-grained token when you no longer need pull access.
DONE2
else
  cat <<DONE3
  Web console:         skipped (set REPO_URL + GIT_TOKEN to install it, or clone
                       the repo inside the CT and run deploy/setup-web-console.sh).
DONE3
fi
