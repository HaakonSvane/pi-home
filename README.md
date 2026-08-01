# pi@home

Services running on the home Raspberry Pi. Each service lives in its own directory with its own `compose.yml`, managed independently — there is no root compose file tying them together.

## Conventions

- **File naming**: `compose.yml` (not `docker-compose.yml`) inside each service directory.
- **Persistent state**: any service with runtime state to persist bind-mounts a single flat `./data` directory into whatever path the container expects (e.g. `./data:/etc/pihole`, `./data:/mosquitto/data`). One `data/` per service, no nested subfolders — keeps every service's compose file predictable to read and the blanket `data/` rule in `.gitignore` trivially covers all of them. Static, version-controlled config (not runtime state) lives outside `data/` instead — e.g. `mosquitto/config/mosquitto.conf`, `caddy/Caddyfile`.
- **Env vars and secrets**: plain, non-sensitive config (`TZ`, ports, intervals, hostnames) lives directly in `compose.yml` under `environment:` — no `.env` indirection for values that aren't secret. A service only gets a `.env` file when it actually holds a credential (e.g. `pihole/.env` for `PIHOLE_PASSWORD`), referenced via `${VAR}` interpolation. `.env`/`*.env` is blanket-gitignored so any future secret is covered automatically.
- `container_name` is always set explicitly (matches the service name) and `restart: unless-stopped` is always used.
- **Log size**: every service caps its logs (`logging: driver: json-file, options: {max-size: "10m", max-file: "3"}`, ~30MB max per container) — Docker's `json-file` driver has no size limit by default, and this is an SD card, not a server disk.

## Services

- `pihole/` — DNS ad-blocking. Web UI is not published to the host — reachable only via Caddy.
- `mosquitto/` — MQTT broker used as the pub/sub backbone for sensors and other services.
- `dht22/` — reads the DHT22 sensor in the basement low-voltage cabinet (wired to GPIO4) roughly every 15s, and every 2 minutes averages that window's readings and publishes temperature, humidity, and a calculated dew point to Mosquitto on `home/dht22/{temperature,humidity,dew_point}` (retained).
- `caddy/` — reverse proxy. The only service bound to host ports 80/443. Routes friendly hostnames under `home.arpa` to each service's web UI, with HTTPS via Caddy's own internal CA.
- `homebridge/` — HomeKit bridge, exposing the DHT22 readings (via MQTT) as Apple Home accessories. Runs with `network_mode: host` — see below, it's a deliberate exception to how every other service is networked.

## Accessing services by hostname

Each service with a web UI gets a subdomain under `home.arpa` (e.g. `https://pihole.home.arpa`), proxied by Caddy. New sites are added as blocks in `caddy/Caddyfile`.

We use `home.arpa` rather than `.local` because `.local` is reserved for mDNS/Bonjour — macOS, iOS, Linux, and Windows all intercept `.local` lookups before they'd reach Pi-hole's DNS, so a wildcard record for it wouldn't resolve reliably. `home.arpa` is the IETF-standardized domain for exactly this (RFC 8375).

### One-time setup: wildcard DNS in Pi-hole

Pi-hole needs to answer for `*.home.arpa` and point it at the Pi. In the Pi-hole admin UI: **Settings → All Settings → (Expert mode) → search `dnsmasq_lines`**, and add:

```
address=/home.arpa/<pi-lan-ip>
```

This only works for devices that already use Pi-hole as their DNS resolver (should be true network-wide already). Give the Pi a DHCP reservation on your router so `<pi-lan-ip>` doesn't drift.

### One-time setup: trusting Caddy's internal CA

Caddy issues certificates from its own internal CA rather than a public one (there's no way to get a publicly-trusted cert for a made-up local domain). Each client device needs to trust that CA once, or you'll get certificate warnings:

1. Extract the root cert from the Pi: `docker cp caddy:/data/caddy/pki/authorities/local/root.crt ./caddy-root.crt`
2. **macOS**: `security add-trusted-cert -d -r trustRoot -k ~/Library/Keychains/login.keychain-db caddy-root.crt` (or double-click the file and set it to "Always Trust" in Keychain Access).
3. **iOS**: AirDrop or email the file to the device, install the profile (Settings → Profile Downloaded), then separately enable full trust under Settings → General → About → Certificate Trust Settings — installing the profile alone isn't enough as of iOS 10.3+.

`caddy/data/` persists the CA root across restarts — don't delete it, or every device will need to re-trust a new one.

## Shared network

Since each service is its own Compose project, services can't reach each other by name by default. Services that need to talk to one another join a shared external Docker network, created once per Pi:

```bash
docker network create homelab
```

Running this a second time doesn't create a duplicate or break anything — Docker just refuses with `Error response from daemon: network with name homelab already exists` and exits non-zero. Safe to ignore, or make it idempotent:

```bash
docker network inspect homelab >/dev/null 2>&1 || docker network create homelab
```

Any compose file that needs to reach another service declares:

```yaml
networks:
  homelab:
    external: true
```

`pihole`, `mosquitto`, `dht22`, and `caddy` join it today — Pi-hole needs it so Caddy can reach its web UI by container name. `homebridge` is the one exception: see below.

## Homebridge networking

HomeKit relies on mDNS/Bonjour multicast plus dynamic per-accessory TCP ports, neither of which survive Docker's normal bridge-mode NAT. The official Homebridge image requires `network_mode: host` to work at all — so, unlike every other service here, `homebridge/compose.yml` does **not** join `homelab` (Docker Compose doesn't allow `network_mode: host` and `networks:` on the same service anyway). Two consequences fall out of that:

- **Reaching Mosquitto**: Homebridge connects to `mqtt://127.0.0.1:1883` — since it shares the host's network namespace, it hits Mosquitto's already-published host port directly, no container name needed.
- **Caddy reaching Homebridge**: Caddy is still on `homelab` (a bridge network) and can't resolve `homebridge` by container name either. Instead, `caddy/compose.yml` sets `extra_hosts: ["host.docker.internal:host-gateway"]` (a Linux Docker Engine 20.10+ feature) so its `homebridge.home.arpa` site block can target `host.docker.internal:8581` — the host's own IP — without hardcoding the Pi's LAN address anywhere.

**mDNS advertiser**: Homebridge defaults to its own bundled "Ciao" mDNS responder, but Raspberry Pi OS already runs `avahi-daemon` (that's what serves `raspberrypi.local`), and running both on UDP 5353 has documented reliability issues. `homebridge/compose.yml` sets `ENABLE_AVAHI: "0"` and bind-mounts the host's `/var/run/dbus` and `/var/run/avahi-daemon/socket` so Homebridge can be pointed at the host's Avahi instead (set in the UI — see setup steps below).

### One-time setup: Homebridge

1. `docker compose -f homebridge/compose.yml up -d`, then visit `https://homebridge.home.arpa` and complete the first-run admin account wizard.
2. In the UI: **Settings → Homebridge Settings → Advertiser → Avahi**, then restart Homebridge from the UI.
3. **Plugins → search "mqttthing" → install** (`homebridge-mqttthing`).
4. Add two accessories via the UI's config editor:
   ```json
   {
     "accessory": "mqttthing",
     "type": "temperatureSensor",
     "name": "Outdoor Temperature",
     "url": "mqtt://127.0.0.1:1883",
     "topics": { "getCurrentTemperature": "home/dht22/temperature" }
   },
   {
     "accessory": "mqttthing",
     "type": "humiditySensor",
     "name": "Outdoor Humidity",
     "url": "mqtt://127.0.0.1:1883",
     "topics": { "getCurrentRelativeHumidity": "home/dht22/humidity" }
   },
   {
     "accessory": "mqttthing",
     "type": "temperatureSensor",
     "name": "Cabinet Dew Point",
     "url": "mqtt://127.0.0.1:1883",
     "topics": { "getCurrentTemperature": "home/dht22/dew_point" }
   }
   ```
   HomeKit has no native "dew point" accessory type, so the dew point is exposed as a second temperature sensor — the name makes the meaning clear even though Home shows a thermometer icon.
5. Restart Homebridge, then pair it in the Apple Home app by scanning the QR/pairing code on the Homebridge UI dashboard.

### Router checklist (UniFi)

HomeKit discovery is easy to break at the router/AP level. On a UniFi Cloud Gateway Fiber (or any UniFi OS gateway), assuming the Pi and your Apple devices are on one flat LAN:

- **Client Device Isolation must be off** on the WiFi network/SSID your iPhone/Mac uses (`Settings → WiFi → [SSID] → Advanced`) — this is the most common cause of "HomeKit can't find the bridge," since it blocks the device-to-device multicast traffic mDNS needs.
- **Multicast DNS (mDNS) Proxy** and **IGMP snooping** settings only matter if the Pi and your Apple devices sit on *different* VLANs — leave them alone on a flat single-LAN setup.
- **Zone-Based Firewall** (current UniFi OS default) already allows all traffic within one zone/VLAN, so no custom firewall rules should be needed unless you're running the Pi on an isolated VLAN — if so, that needs explicit rules for UDP 5353 and HAP's dynamic TCP ports, not just mDNS reflection.

## Running a service

From the repo root, every service follows the same pattern (swap in the service directory):

```bash
# start (add --build if the service has a Dockerfile, e.g. dht22)
docker compose -f <service>/compose.yml up -d

# view logs
docker compose -f <service>/compose.yml logs -f

# stop
docker compose -f <service>/compose.yml down
```

Concretely, to bring everything up on a fresh Pi:

```bash
docker network inspect homelab >/dev/null 2>&1 || docker network create homelab

docker compose -f pihole/compose.yml up -d
docker compose -f mosquitto/compose.yml up -d
docker compose -f dht22/compose.yml up -d --build
docker compose -f caddy/compose.yml up -d
docker compose -f homebridge/compose.yml up -d
```

`pihole`, `mosquitto`, `dht22`, and `caddy` need the `homelab` network to exist first (they'll fail to start otherwise); `homebridge` doesn't use it at all (see Homebridge networking above). Then complete the one-time setup steps above (wildcard DNS in Pi-hole, trusting Caddy's CA, the Homebridge setup wizard) before everything is reachable end to end.
