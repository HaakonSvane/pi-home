# pi@home

Services running on the home Raspberry Pi. Each service lives in its own directory with its own `compose.yml`, managed independently — there is no root compose file tying them together.

## Services

- `pihole/` — DNS ad-blocking.
- `mosquitto/` — MQTT broker used as the pub/sub backbone for sensors and other services.
- `dht22/` — reads the DHT22 temperature/humidity sensor (wired to GPIO4) and publishes readings to Mosquitto on `home/dht22/temperature` and `home/dht22/humidity` (retained).

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

`pihole` doesn't currently need this — it only exposes host ports.

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
```

`mosquitto` and `dht22` both need the `homelab` network to exist first (they'll fail to start otherwise); `pihole` doesn't depend on it and can start in any order.
