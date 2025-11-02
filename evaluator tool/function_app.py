import os
import json
from typing import Any, Dict, List, Optional

import azure.functions as func

# --- Optional: robust JSON extraction for LLM outputs ---
import re

def _extract_json(text: str) -> Any:
    """Try to parse JSON from an LLM response that might contain prose.
    - First try json.loads directly
    - Then try to extract the first fenced code block
    - Then try to find the first {...} or [...] with a naive bracket match
    Returns Python object or raises ValueError.
    """
    # 1) direct
    try:
        return json.loads(text)
    except Exception:
        pass

    # 2) fenced block
    fence = re.search(r"```(?:json)?\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        candidate = fence.group(1).strip()
        try:
            return json.loads(candidate)
        except Exception:
            pass

    # 3) naive bracket matching
    # Find first { ... } or [ ... ] region with balanced braces/brackets
    for opener, closer in [("{", "}"), ("[", "]")]:
        start = text.find(opener)
        if start != -1:
            depth = 0
            for i in range(start, len(text)):
                if text[i] == opener:
                    depth += 1
                elif text[i] == closer:
                    depth -= 1
                    if depth == 0:
                        candidate = text[start : i + 1]
                        try:
                            return json.loads(candidate)
                        except Exception:
                            break

    raise ValueError("Could not extract valid JSON from model output")


# -------- watsonx.ai setup --------
# This uses the official IBM Watsonx.ai Python SDK (ibm-watsonx-ai)
# Install via: pip install ibm-watsonx-ai
# Docs: https://ibm.github.io/watsonx-ai-python-sdk/

try:
    from ibm_watsonx_ai import Credentials
    from ibm_watsonx_ai.foundation_models import Model
except Exception:
    # Soft import fallback so the Function can start and return a helpful error if missing
    Credentials = None  # type: ignore
    Model = None  # type: ignore


# Azure Functions v2 Python programming model
app = func.FunctionApp()


# ---------- Prompt builder ----------
SYSTEM_DIRECTIVE = (
    "You are the Feasibility & Impact Agent. Rate each idea for Technical Feasibility and Market Impact. "
    "Return STRICT JSON only. Use 0-5 integers for scores. Include rationale, key_risks, and next_step."
)

JSON_INSTRUCTIONS = {
    "schema": {
        "type": "object",
        "properties": {
            "evaluations": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "idea": {"type": "string"},
                        "feasibility": {"type": "integer", "minimum": 0, "maximum": 5},
                        "impact": {"type": "integer", "minimum": 0, "maximum": 5},
                        "rationale": {"type": "string"},
                        "key_risks": {"type": "array", "items": {"type": "string"}},
                        "next_step": {"type": "string"},
                    },
                    "required": [
                        "idea",
                        "feasibility",
                        "impact",
                        "rationale",
                        "key_risks",
                        "next_step",
                    ],
                },
            }
        },
        "required": ["evaluations"],
    },
    "example": {
        "evaluations": [
            {
                "idea": "Smart parking app for mid-size EU cities",
                "feasibility": 4,
                "impact": 3,
                "rationale": "Uses mature sensors & maps; moderate integration complexity; market is competitive.",
                "key_risks": ["Data-sharing with municipalities", "Hardware deployment costs"],
                "next_step": "Pilot with one municipality and measure adoption & enforcement outcomes.",
            }
        ]
    },
}


def build_prompt(ideas: List[str]) -> str:
    return (
        f"SYSTEM:\n{SYSTEM_DIRECTIVE}\n\n"
        "TASK: Evaluate the following ideas. For each item, produce one JSON object in 'evaluations'.\n\n"
        f"IDEAS: {json.dumps(ideas, ensure_ascii=False)}\n\n"
        "OUTPUT FORMAT: Return ONLY a single JSON object matching this schema and style:\n"
        f"{json.dumps(JSON_INSTRUCTIONS, ensure_ascii=False)}\n"
    )


# ---------- watsonx.ai invocation ----------

def call_watsonx_llm(prompt: str, *, model_id: Optional[str] = None) -> str:
    """Call a watsonx foundation model and return the raw generated text."""
    if Credentials is None or Model is None:
        raise RuntimeError(
            "ibm-watsonx-ai package not installed. Add 'ibm-watsonx-ai' to requirements.txt."
        )

    api_key = "CU_AoDPLRa00dbeeyyBSwZW_Emn4hdT6eZ22rFV5Puga"
    url = os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")
    project_id = "075473a2-da4b-483d-ab44-cd7538922595"
    model_id = model_id or os.getenv("WATSONX_MODEL_ID", "meta-llama/llama-3-3-70b-instruct")

    if not api_key or not project_id:
        raise RuntimeError("Missing WATSONX_API_KEY or WATSONX_PROJECT_ID environment variables.")

    creds = Credentials(api_key=api_key, url=url)

    # Generation parameters can be tuned as needed
    gen_params: Dict[str, Any] = {
        "decoding_method": "greedy",
        "max_new_tokens": 600,
        "min_new_tokens": 0,
        "stop_sequences": ["SYSTEM:", "TASK:", "OUTPUT"],
        "temperature": 0.0,
        "repetition_penalty": 1.05,
    }

    model = Model(
        model_id=model_id,
        credentials=creds,
        params=gen_params,
        project_id=project_id,
    )

    response = model.generate(prompt=prompt)

    # SDK returns dict with 'results' or text depending on version; normalize
    if isinstance(response, dict):
        # v1 style: {'results': [{'generated_text': '...'}], ...}
        try:
            return response["results"][0]["generated_text"]
        except Exception:
            # some versions: {'result': {'generated_text': '...'}}
            return (
                response.get("result", {}).get("generated_text")
                or response.get("generated_text")
                or json.dumps(response)
            )
    return str(response)


# ---------- HTTP Function ----------
@app.function_name(name="score_ideas")
@app.route(route="score", methods=[func.HttpMethod.POST], auth_level=func.AuthLevel.ANONYMOUS)

def score(req: func.HttpRequest) -> func.HttpResponse:
    """HTTP POST /api/score
    Body JSON:
    {
      "ideas": ["short idea text", ...],
      "model_id": "optional watsonx model id (e.g., ibm/granite-13b-instruct-v2)"
    }

    Returns 200 with JSON {"evaluations": [...]} or 400/500 on error.
    """
    try:
        payload = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON body"}), status_code=400, mimetype="application/json"
        )

    ideas = payload.get("ideas")
    if not isinstance(ideas, list) or not all(isinstance(x, str) and x.strip() for x in ideas):
        return func.HttpResponse(
            json.dumps({"error": "'ideas' must be a non-empty list of strings"}),
            status_code=400,
            mimetype="application/json",
        )

    model_id = payload.get("model_id")

    try:
        prompt = build_prompt(ideas)
        raw = call_watsonx_llm(prompt, model_id=model_id)
        data = _extract_json(raw)

        # Optional: light post-validation of expected structure
        if not isinstance(data, dict) or "evaluations" not in data:
            raise ValueError("Model output missing 'evaluations'")

        # Attach simple aggregate metrics
        try:
            evals = data.get("evaluations", [])
            if isinstance(evals, list):
                feas = [e.get("feasibility", 0) for e in evals if isinstance(e, dict)]
                imp = [e.get("impact", 0) for e in evals if isinstance(e, dict)]
                data["summary"] = {
                    "avg_feasibility": round(sum(feas) / len(feas), 2) if feas else None,
                    "avg_impact": round(sum(imp) / len(imp), 2) if imp else None,
                }
        except Exception:
            pass

        return func.HttpResponse(json.dumps(data, ensure_ascii=False), mimetype="application/json")

    except RuntimeError as e:
        return func.HttpResponse(
            json.dumps({"error": str(e)}), status_code=500, mimetype="application/json"
        )
    except ValueError as e:
        return func.HttpResponse(
            json.dumps({"error": f"Failed to parse model output: {str(e)}", "raw": raw}),
            status_code=502,
            mimetype="application/json",
        )
    except Exception as e:
        return func.HttpResponse(
            json.dumps({"error": f"Unhandled error: {str(e)}"}), status_code=500, mimetype="application/json"
        )
