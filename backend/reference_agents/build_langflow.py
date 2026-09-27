"""Build the Northwind reference agent on Langflow via its local API.

Starts from Langflow's own "Memory Chatbot" starter project - guaranteed-valid
node graph for the running version - then swaps in the controlled variables:
model gpt-4o-mini and the shared Northwind system prompt. Prints the flow id,
which the grade adapter targets. Reproducible: rerun to rebuild identically.

⛔ MEMORY CHATBOT, NOT BASIC PROMPTING, AND THE CHOICE IS LOAD-BEARING. The grade
runs in `server_session` mode, meaning the PLATFORM is expected to remember the
conversation and we send only the new turn. Building on Basic Prompting would
make Langflow score near-zero on the memory dimension for a reason that is our
build error rather than anything about Langflow.

⛔ LANGFLOW_AUTO_LOGIN=true IS NOT ENOUGH. /api/v1/flows/ still 403s an
unauthenticated caller on 1.12.1. GET /api/v1/auto_login returns a JWT and that
as a Bearer header gives 200. Measured 2026-09-13; a snapshot of someone else's
product, not a property of it.

    OPENAI_API_KEY=sk-... python build_langflow.py
"""
import json
import os
import sys

import httpx

from northwind import MODEL, SYSTEM_PROMPT

B = os.environ.get("PG_LANGFLOW_URL", "http://localhost:3500")
KEY = os.environ.get("OPENAI_API_KEY", "").strip()
if not KEY:
    # os.environ["..."] raises on UNSET and accepts EMPTY, and an empty key is
    # exactly what a failed shell substitution produces. The flow then builds and
    # saves happily, and every graded message fails with "Missing credentials" -
    # which reads as a broken platform rather than a broken command line. Cost an
    # hour on 2026-09-13.
    raise SystemExit("OPENAI_API_KEY is empty or unset; refusing to build a flow "
                     "that cannot answer a single message")
FLOW_NAME = "pg-northwind"


def _set(node_template: dict, field: str, value) -> bool:
    """Assign into a Langflow template field, which is a dict with a `value` key.

    Returns whether it landed. The caller ASSERTS on that: a silently ignored
    patch would ship an agent running the template's defaults, and a grade of the
    starter project is not a grade of anything we meant to build.
    """
    f = node_template.get(field)
    if not isinstance(f, dict):
        return False
    f["value"] = value
    return True


def main() -> int:
    with httpx.Client(base_url=B, timeout=60) as c:
        tok = c.get("/api/v1/auto_login").json()["access_token"]
        h = {"Authorization": f"Bearer {tok}"}

        tpl = next(
            t for t in c.get("/api/v1/starter-projects/", headers=h).json()
            if (t.get("name") or t.get("data", {}).get("name")) == "Memory Chatbot"
        )
        data = tpl["data"]

        # `provider` is required alongside model_name: without it the component
        # raises "Model name/provider overrides require a built-in model
        # selection" at RUN time, not at build time. The flow saves happily and
        # every graded message fails, which reads as a broken agent rather than
        # an unset field.
        # ⛔ SWAP THE GENERIC LanguageModelComponent FOR THE DEDICATED OPENAI ONE.
        #
        # On 1.12.1 the generic component's `model` field is a UI-driven selector
        # (type "model", real_time_refresh) whose options are populated by the
        # browser, and `provider`/`model_name` are labelled "Override" - they only
        # apply ON TOP of a built-in selection. A headless caller cannot make that
        # selection, and the failure is invisible at build time: the flow saves,
        # and every RUN fails with "Model name/provider overrides require a
        # built-in model selection, not a connected model object."
        #
        # OpenAIModelComponent carries plain valued fields instead, and emits the
        # same `text_output` handle, so the template's edges survive the swap. The
        # node id is kept for the same reason.
        comps = c.get("/api/v1/all", headers=h).json()
        oai = comps["openai"]["ext:openai:OpenAIModelComponent@official"]

        swapped = False
        for n in data["nodes"]:
            nd = n["data"]
            if nd["type"] != "LanguageModelComponent":
                continue
            node = json.loads(json.dumps(oai))          # deep copy
            t = node["template"]
            # ⛔ api_key ships with load_from_db=True and value "OPENAI_API_KEY",
            # meaning Langflow reads the value as the NAME of a global variable
            # stored in its own database, not as the key itself. Writing the real
            # key into `value` while that flag is set stores the key as a variable
            # name and looks up a variable that does not exist. Clear the flag.
            t["api_key"]["load_from_db"] = False
            ok = all((_set(t, "model_name", MODEL),
                      _set(t, "api_key", KEY),
                      _set(t, "system_message", SYSTEM_PROMPT)))
            if not ok:
                break
            nd["type"] = "OpenAIModel"
            nd["node"] = node
            swapped = True

        patched = {"openai component swapped in": swapped}

        missing = [k for k, ok in patched.items() if not ok]
        if missing:
            print(f"FAIL: could not patch {missing} - the template shape changed. "
                  f"A flow built from unpatched defaults would grade the starter "
                  f"project, not the Northwind agent.", file=sys.stderr)
            return 1

        # delete any previous build so a rerun is idempotent rather than additive
        for f in c.get("/api/v1/flows/", headers=h).json():
            if f.get("name") == FLOW_NAME:
                c.delete(f"/api/v1/flows/{f['id']}", headers=h)

        created = c.post("/api/v1/flows/", headers=h, json={
            "name": FLOW_NAME,
            "description": "Proving Ground reference build - Northwind support agent",
            "data": data,
        }).json()
        fid = created["id"]

    print(f"flow_id={fid}")
    print(f"endpoint={B}/api/v1/run/{fid}?stream=false")
    print(f"model={MODEL}  system_prompt={len(SYSTEM_PROMPT)} chars")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
