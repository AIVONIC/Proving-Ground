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
