# Onyx reference stack (pinned to v4.6.2)

Onyx's own compose files are **fetched, not vendored**, so this repo carries no
copy of someone else's source to drift out of date:

```bash
TAG=v4.6.2
BASE=https://raw.githubusercontent.com/onyx-dot-app/onyx/$TAG/deployment/docker_compose
curl -fsSL "$BASE/docker-compose.yml"           -o docker-compose.yml
curl -fsSL "$BASE/docker-compose.onyx-lite.yml" -o docker-compose.onyx-lite.yml

cp .env.example .env      # then set USER_AUTH_SECRET (openssl rand -hex 32)
docker compose -f docker-compose.yml \
               -f docker-compose.onyx-lite.yml \
               -f docker-compose.pg-ref.yml up -d
```

`docker-compose.pg-ref.yml` is the only file here that is ours. It changes
nothing about Onyx beyond making it safe to run beside other stacks on one
workstation: loopback-only ports off the common ranges, and no nginx or web UI
(Onyx's own file binds host `:80` and `:3000`).

Wait for `curl localhost:8390/health` to return 200, then:

```bash
OPENAI_API_KEY=sk-... ../../ref-venv/bin/python ../../build_onyx.py
```

Tear the stack down with `docker compose ... down -v` once the grade is
promoted. Reference containers are transient by design; the build script is the
artifact, not the container.
