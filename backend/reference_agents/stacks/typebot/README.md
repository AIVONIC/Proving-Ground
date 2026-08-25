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
