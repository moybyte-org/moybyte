# Hosting Moybyte off-box

Two concerns, two right tools:

| Want | Tool | Why |
|---|---|---|
| **Live interactive console** in a browser | a small always-on VM | it's a persistent Python process with a stateful WebSocket that steps the console every frame — Netlify/Pages/serverless can't hold that |
| **Firmware `.bin` builds** | GitHub Actions | ephemeral runners, no idle cost, images drop out as artifacts |
| Static demo / marketing site | Netlify / Pages / S3 | fine for the `site/` or a recorded **playback**, but not live input |

> **Netlify note:** it can host a *static playback* of the console (recorded frames + the JS replayer baked into one page) or the marketing site — but **not** the live, input-driven console. That needs the VM below.

## 1. Live console on a VM (AWS Lightsail / EC2)

Ubuntu 22.04/24.04. Sizing (measured: the suite peaks ~116 MB, a render ~200 MB):

- **Recommended:** Lightsail **2 GB / 2 vCPU / 60 GB** (~$12/mo flat), or EC2 `t4g.small`.
- **Floor:** 1 GB / 1 vCPU works for the console alone.

**Setup — paste `setup-web-console.sh` into the Lightsail "Launch script" (or EC2 user-data), or run it by hand.** Edit the two lines at the top first:

- `REPO_URL` — the repo is **private**, so use a fine-grained read-only PAT:
  `https://<TOKEN>@github.com/moybyte-org/moybyte.git` (or add a deploy key to the box first).
- `BRANCH` — `master` for stable, or a branch to preview WIP.

It installs deps, builds the venv, and runs the console as a `systemd` service
(`moybyte-web`) on port **8080**, serving the windowed desktop at
`http://<public-ip>:8080/`.

**Open the firewall** for TCP 8080 (Lightsail: Networking tab; EC2: Security Group)
and **scope the source to your IP** — the console has no auth.

### Public URL with TLS + a password (optional)
The console ships no auth, so don't expose 8080 to the world raw. Put Caddy in front:

```
# /etc/caddy/Caddyfile
console.example.com {
    reverse_proxy 127.0.0.1:8080
    basicauth { you <bcrypt-hash> }   # caddy hash-password
}
```

`apt install caddy`, point DNS at the box, open 80/443 (not 8080), done — Caddy
auto-provisions Let's Encrypt TLS.

### Manage
```
systemctl status moybyte-web
journalctl -u moybyte-web -f
sudo -u moybyte git -C /opt/moybyte pull --ff-only && systemctl restart moybyte-web
```

## 2. Firmware builds on GitHub Actions

`.github/workflows/firmware-build.yml` builds the ESP32 images on GitHub's
runners — no VM. Trigger it from the **Actions** tab → *Firmware build* → *Run
workflow*, pick `tdeck`, `p4`, or `both`. The `.bin` images upload as artifacts
(`moybyte-firmware-<board>`), 14-day retention.

Notes:
- Manual-only by default (no minutes burned on ordinary pushes). Uncomment the
  `push:` trigger in the workflow to also archive every `master` build.
- First run installs the ESP-IDF toolchains (~2–3 GB, cached after) so it's the
  slow one; later runs are faster via the toolchain + ccache caches.
- The T-Deck build runs twice on purpose — a fresh checkout fetches ESP-IDF
  mid-build, so the #43 PSRAM-DMA patch only lands on the second pass.
