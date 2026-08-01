# AGENTS.md

Guidance for AI coding agents working in this repository.

## Commands

There is no test suite, linter, or CI in this repo. The available correctness checks are:

```bash
# Validate a compose file (interpolates env vars, checks YAML/schema — catches most mistakes)
docker compose -f <service>/compose.yml config

# Syntax-check the one piece of application code in the repo
python3 -m py_compile dht22/app/reader.py
```

Deploying/operating a service (each is an independent Compose project — see Architecture):

```bash
# one-time, before starting any service that joins the shared network
docker network create homelab

docker compose -f <service>/compose.yml up -d       # start (add --build for dht22, it's the only built image)
docker compose -f <service>/compose.yml logs -f      # logs
docker compose -f <service>/compose.yml down         # stop
```

## Architecture

### Multi-project Compose layout
Each service lives in its own top-level directory with its own `compose.yml` (not `docker-compose.yml`) — there is no root compose file. Every directory is a separate Compose project, deployed and managed independently. The direct consequence: each project gets its own default Docker network, so services cannot reach each other by container name unless explicitly wired together (see below).

### The `homelab` shared network
Cross-service traffic goes over an external Docker network named `homelab`, created once (`docker network create homelab`) and joined by declaring `networks: { homelab: { external: true } }` in a service's compose file. `pihole`, `mosquitto`, `dht22`, and `caddy` all join it so they can address each other by container name (e.g. Caddy → `pihole:80`, `dht22` → `mosquitto:1883`). `homebridge` is a deliberate exception — see below.

### Data pipeline: DHT22 → MQTT → Homebridge → Apple Home
`dht22/app/reader.py` samples the sensor (wired to GPIO4 on a Raspberry Pi 5 — GPIO is exposed at `/dev/gpiochip4`, not `gpiochip0` as on older Pi models, because of the Pi 5's RP1 southbridge chip) every `SAMPLE_INTERVAL_SECONDS` (15s), and every `PUBLISH_INTERVAL_SECONDS` (120s) averages that window's sub-readings with a plain arithmetic mean — deliberately unweighted, since equal weighting minimizes variance for a slowly-changing signal like a basement cabinet, whereas recency-weighting would trade noise reduction for responsiveness that isn't needed here. It computes a dew point (Magnus-Tetens formula, Alduchov–Eskridge constants) from the averaged values and publishes three retained MQTT topics to `mosquitto`: `home/dht22/temperature`, `home/dht22/humidity`, `home/dht22/dew_point`. `homebridge` subscribes to these via the `homebridge-mqttthing` plugin (configured live through Homebridge's own UI — not checked into this repo) to expose them as HomeKit accessories.

### Homebridge breaks every networking convention here, on purpose
HomeKit's mDNS/Bonjour advertisement and dynamic per-accessory TCP ports don't survive Docker's bridge-mode NAT, so `homebridge/compose.yml` uses `network_mode: host` — which Docker Compose treats as mutually exclusive with `networks:`, so it cannot join `homelab` like everything else. Consequences that follow from this, and matter if you touch networking here: Homebridge reaches Mosquitto via `mqtt://127.0.0.1:1883` (the host's loopback, since Mosquitto's port is already published to the host) instead of the container name `mosquitto`; and Caddy reaches Homebridge's web UI via `extra_hosts: ["host.docker.internal:host-gateway"]` → `host.docker.internal:8581` instead of the container name `homebridge`. Full reasoning (mDNS advertiser choice, avahi-daemon coexistence, router-level gotchas) is in `README.md` under "Homebridge networking."

### Caddy: single ingress + custom local domain
`caddy` is the only service bound to host ports 80/443. It reverse-proxies friendly hostnames under `svane.home.arpa` to each service's web UI (e.g. `pihole.svane.home.arpa → pihole:80`) — new sites are added as blocks in `caddy/Caddyfile`. `svane.home.arpa` was chosen over `.local` because `.local` is intercepted by mDNS/Bonjour resolvers on macOS, iOS, Linux, and Windows before a real DNS query would ever fire; `home.arpa` is the IETF-standardized domain for exactly this (RFC 8375). TLS is issued from Caddy's own internal CA (`local_certs` in the Caddyfile) rather than a public one — there's no way to get a publicly-trusted cert for a made-up local domain — so client devices need that CA trusted once (steps in `README.md`).

### Conventions for any new service
- One flat `./data` directory per service for runtime state, bind-mounted directly (e.g. `./data:/etc/pihole`) — no nested subfolders. `data/` is blanket-gitignored. Static, version-controlled config lives outside `data/` instead (e.g. `mosquitto/config/mosquitto.conf`, `caddy/Caddyfile`).
- `.env` is used only when a service holds an actual secret (e.g. `pihole/.env` → `PIHOLE_PASSWORD`, interpolated via `${VAR}`); non-sensitive config (`TZ`, ports, intervals) stays directly in `compose.yml` under `environment:`.
- `container_name` is always set explicitly to match the service name; `restart: unless-stopped` is always used.
