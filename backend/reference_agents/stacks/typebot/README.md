# Typebot reference stack (pinned to 3.18.0)

```bash
cp .env.example .env      # then fill both values
docker compose up -d      # builder :3400, viewer :3401, postgres :5436 (all loopback)
```

`PG_TYPEBOT_ENCRYPTION_SECRET` must be **exactly 32 characters**: Typebot uses the
raw UTF-8 bytes of it as an AES-256 key, and the build script ports that
encryption to store the OpenAI credential the way Typebot itself would.

```bash
OPENAI_API_KEY=sk-... ../../ref-venv/bin/python ../../build_typebot.py
```

The graded surface is the **viewer** (`:3401`), which serves Typebot's public
chat API. The builder is only here because it runs the Prisma migrations.

Tear down with `docker compose down -v` once the grade is promoted.

## Where the keys come from

`OPENAI_API_KEY` and the three judge keys live in **`/home/sitemind/Aivonic/AI-Agent-UI/platform/backend/.env`** - a different repo
from this one, and gitignored, so no code search in proving-ground will ever find
them. Load them the way `regrade_both.sh` does:

```bash
set -a; source <(grep -E '^(OPENAI|ANTHROPIC|XAI|GOOGLE)_API_KEY=' \
  /home/sitemind/Aivonic/AI-Agent-UI/platform/backend/.env); set +a
export PROVING_GROUND_REQUIRE_JUDGES=all
```

Written here because "OPENAI_API_KEY=sk-..." told a reader the name of the
variable and nothing about where to get it. On 2026-09-13 that cost an hour and a
false report that grading was blocked for want of credentials that were present
the whole time.
