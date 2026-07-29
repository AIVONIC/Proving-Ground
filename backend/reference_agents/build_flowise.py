"""Build the Northwind reference agent on Flowise via its open local API.

Starts from Flowise's own 'Simple Conversation Chain' template (guaranteed-valid
flowData for the running version), then swaps in the controlled variables: model
gpt-4o-mini and the shared Northwind system prompt. Prints the deployed chatflow
id, which the grade adapter targets. Reproducible: rerun to rebuild identically.

    OPENAI_API_KEY=sk-... python build_flowise.py
"""
import json
import os
import sys

import httpx

from northwind import MODEL, SYSTEM_PROMPT

B = os.environ.get("FLOWISE_URL", "http://localhost:3300")
KEY = os.environ["OPENAI_API_KEY"]


def main() -> int:
    with httpx.Client(base_url=B, timeout=30) as c:
        # 1. template flowData (valid for this Flowise version)
        rows = c.get("/api/v1/marketplaces/templates").json()
        tpl = next(x for x in rows if (x.get("name") or x.get("templateName")) == "Simple Conversation Chain")
        fd = tpl["flowData"]
        fd = json.loads(fd) if isinstance(fd, str) else fd

        # 2. OpenAI credential (stored encrypted; node references it by id)
        cred = c.post("/api/v1/credentials", json={
            "name": "pg-openai", "credentialName": "openAIApi",
            "plainDataObj": {"openAIApiKey": KEY},
        }).json()
        cred_id = cred["id"]

        # 3. patch the controlled variables into the node graph
        for n in fd["nodes"]:
            d = n["data"]
            if d.get("name") == "chatOpenAI":
                d["inputs"]["modelName"] = MODEL
                d["inputs"]["temperature"] = 0.3
                d["credential"] = cred_id
                d["inputs"]["credential"] = cred_id
            elif d.get("name") == "conversationChain":
                d["inputs"]["systemMessagePrompt"] = SYSTEM_PROMPT

        # 4. deploy the chatflow
        cf = c.post("/api/v1/chatflows", json={
            "name": "Northwind Support (Flowise reference)",
            "flowData": json.dumps(fd),
            "deployed": True, "isPublic": False, "type": "CHATFLOW",
        }).json()
        cf_id = cf["id"]
        print("CHATFLOW_ID=" + cf_id)

        # 5. smoke test (single prediction with a session id)
        r = c.post(f"/api/v1/prediction/{cf_id}", json={
            "question": "How long does standard shipping take?",
            "overrideConfig": {"sessionId": "smoke-1"},
        }, timeout=60).json()
        reply = r.get("text") or r.get("json") or r
        print("SMOKE_REPLY=" + str(reply)[:300])
    return 0


if __name__ == "__main__":
    sys.exit(main())
