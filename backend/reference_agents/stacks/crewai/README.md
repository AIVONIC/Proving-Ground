# CrewAI reference stack (pinned to crewai 1.15.17)

CrewAI is the one member of the cohort with no server to point a grader at, so
`build_crewai.py` is both the build and the runtime. It needs a venv rather than
a container:

```bash
python3 -m venv venv
venv/bin/pip install "crewai==1.15.17" fastapi uvicorn
OPENAI_API_KEY=sk-... venv/bin/python ../../build_crewai.py   # serves 127.0.0.1:8391
```

Read the docstring at the top of `build_crewai.py` before reading CrewAI's grade.
It states the three things that limit what the number means: the HTTP wrapper is
ours, conversation memory is the wrapper's rather than the platform's, and a
single-agent crew exercises none of the orchestration CrewAI is actually chosen
for.

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
