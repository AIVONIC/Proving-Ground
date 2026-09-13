# n8n reference build

```bash
docker compose -f stacks/n8n/docker-compose.yml up -d
set -a; source <(grep -E '^(OPENAI|ANTHROPIC|XAI|GOOGLE)_API_KEY=' \
  /home/sitemind/Aivonic/AI-Agent-UI/platform/backend/.env); set +a
../../ref-venv/bin/python build_n8n.py
```

## Where the keys come from

`OPENAI_API_KEY` and the three judge keys live in
**`/home/sitemind/Aivonic/AI-Agent-UI/platform/backend/.env`** - a different repo
from this one, and gitignored, so no code search in proving-ground will find
them. `export PROVING_GROUND_REQUIRE_JUDGES=all` before any run that publishes,
or a missing key grades with three labs and says nothing.
