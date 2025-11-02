# function_app.py
import os
import json
from typing import Any, Dict, List, Optional

import azure.functions as func
import re

# ---------- JSON extraction helper (kept from your working app) ----------
def _extract_json(text: str) -> Any:
    """Try to parse JSON from an LLM response that might contain prose."""
    try:
        return json.loads(text)
    except Exception:
        pass

    fence = re.search(r"```(?:json)?\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if fence:
        candidate = fence.group(1).strip()
        try:
            return json.loads(candidate)
        except Exception:
            pass

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


# -------- watsonx.ai setup (same SDK pattern you used) --------
try:
    from ibm_watsonx_ai import Credentials
    from ibm_watsonx_ai.foundation_models import Model
except Exception:
    Credentials = None  # type: ignore
    Model = None        # type: ignore


# Azure Functions v2 Python programming model
app = func.FunctionApp()


# ---------- Prompt builder (GENERATOR) ----------
GEN_SCHEMA = {
    "type": "object",
    "properties": {
        "topic": {"type": "string"},
        "ideas": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "summary": {"type": "string"},
                    "first_step": {"type": "string"},
                },
                "required": ["title", "summary", "first_step"],
            },
        },
    },
    "required": ["topic", "ideas"],
}

GEN_EXAMPLE = {
    "topic": "Reducing parking congestion near city centers",
    "ideas": [
        {
            "title": "Dynamic Curb Pricing Lite",
            "summary": "Pilot demand-based pricing on two busiest blocks using existing sensor feeds to reduce circling.",
            "first_step": "Enable sensor data feed and enact a 90-day pilot ordinance."
        }
    ]
}

def build_generator_prompt(topic: str, source_text: str, n_ideas: int) -> str:
    return (
        "SYSTEM:\n"
        "You are an innovation strategist.\n\n"
        "INSTRUCTIONS:\n"
        f"- Generate EXACTLY {n_ideas} ideas.\n"
        "- Each idea must be 2–4 sentences, concrete, feasible, and explicitly reference the source material where relevant.\n"
        "- Provide a short, punchy title; a concise summary; and a concrete first step for validation/build.\n"
        "- Return ONLY a single JSON object. No markdown, no explanations, no code fences.\n\n"
        "OUTPUT JSON SCHEMA (for reference):\n"
        f"{json.dumps(GEN_SCHEMA, ensure_ascii=False)}\n\n"
        "OUTPUT EXAMPLE (for style, not content):\n"
        f"{json.dumps(GEN_EXAMPLE, ensure_ascii=False)}\n\n"
        "TOPIC:\n"
        f"{topic}\n\n"
        "SOURCE MATERIAL:\n"
        f"\"\"\"{source_text.strip()}\"\"\"\n"
    )


# ---------- watsonx.ai invocation (mirrors your working call) ----------
def call_watsonx_llm(prompt: str, *, model_id: Optional[str] = None) -> str:
    """Call a watsonx foundation model and return the raw generated text."""
    if Credentials is None or Model is None:
        raise RuntimeError("ibm-watsonx-ai package not installed. Add 'ibm-watsonx-ai' to requirements.txt.")

    # Prefer ENV; (you used literals in your working code—keep if you must, but env is safer)
    api_key = os.getenv("WATSONX_API_KEY") or "CU_AoDPLRa00dbeeyyBSwZW_Emn4hdT6eZ22rFV5Puga"
    url = os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")
    project_id = os.getenv("WATSONX_PROJECT_ID") or "075473a2-da4b-483d-ab44-cd7538922595"
    model_id = model_id or os.getenv("WATSONX_MODEL_ID", "meta-llama/llama-3-3-70b-instruct")

    if not api_key or not project_id:
        raise RuntimeError("Missing WATSONX_API_KEY or WATSONX_PROJECT_ID environment variables.")

    creds = Credentials(api_key=api_key, url=url)

    # Generation parameters tuned for JSON adherence
    gen_params: Dict[str, Any] = {
        "decoding_method": "greedy",       # maximize determinism/JSON adherence
        "max_new_tokens": 700,
        "min_new_tokens": 0,
        "temperature": 0.0,
        "repetition_penalty": 1.05,
        "stop_sequences": ["SYSTEM:", "INSTRUCTIONS:", "TOPIC:", "SOURCE MATERIAL:"],
    }

    model = Model(
        model_id=model_id,
        credentials=creds,
        params=gen_params,   # pass params here (this is how your working code did it)
        project_id=project_id,
    )

    response = model.generate(prompt=prompt)

    if isinstance(response, dict):
        # Typical shape: {'results': [{'generated_text': '...'}]}
        return (
            response.get("results", [{}])[0].get("generated_text")
            or response.get("result", {}).get("generated_text")
            or response.get("generated_text")
            or json.dumps(response)
        )
    return str(response)


# ---------- HTTP Function: GENERATE ----------
@app.function_name(name="generate_ideas")
@app.route(route="generate", methods=[func.HttpMethod.POST], auth_level=func.AuthLevel.ANONYMOUS)
def generate(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/generate
    {
      "topic": "string (required)",
      "text": "string (required)",
      "n_ideas": 5,
      "model_id": "optional watsonx model id (e.g., ibm/granite-13b-instruct-v2)"
    }
    """
    try:
        payload = req.get_json()
    except ValueError:
        return func.HttpResponse(
            json.dumps({"error": "Invalid JSON body"}), status_code=400, mimetype="application/json"
        )

    topic = (payload.get("topic") or "").strip()
    source_text = (payload.get("text") or "").strip()
    n_ideas = int(payload.get("n_ideas") or 5)

    if not topic or not source_text:
        return func.HttpResponse(
            json.dumps({"error": "'topic' and 'text' are required"}), status_code=400, mimetype="application/json"
        )
    if n_ideas <= 0:
        n_ideas = 5

    model_id = payload.get("model_id")

    try:
        prompt = build_generator_prompt(topic, source_text, n_ideas)
        raw = call_watsonx_llm(prompt, model_id=model_id)

        # Parse to JSON; if model adds prose, _extract_json will salvage it.
        data = _extract_json(raw)

        # Light validation & topic echo
        if not isinstance(data, dict) or "ideas" not in data:
            raise ValueError("Model output missing 'ideas' array")
        if "topic" not in data:
            data["topic"] = topic

        # Ensure exactly n_ideas (truncate or pad if necessary)
        ideas = data.get("ideas", [])
        if isinstance(ideas, list):
            data["ideas"] = ideas[:n_ideas]

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
