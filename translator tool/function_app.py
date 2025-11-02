import json
import os
import logging
import azure.functions as func

# Watsonx SDK
try:
    from ibm_watsonx_ai import Credentials
    from ibm_watsonx_ai.foundation_models import Model
except Exception:
    Credentials = None
    Model = None

app = func.FunctionApp(http_auth_level=func.AuthLevel.ANONYMOUS)


def _bad_request(msg: str, status_code: int = 400) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps({"error": msg}),
        status_code=status_code,
        mimetype="application/json",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
        },
    )

def _ok(payload: dict, status_code: int = 200) -> func.HttpResponse:
    return func.HttpResponse(
        json.dumps(payload),
        status_code=status_code,
        mimetype="application/json",
        headers={
            "Access-Control-Allow-Origin": "*",
            "Access-Control-Allow-Headers": "Content-Type",
            "Access-Control-Allow-Methods": "POST, OPTIONS",
        },
    )

def _build_prompt(text: str, source_lang: str | None) -> str:
    preface = (
        "You are a precise translation engine. "
        "Translate the following text into English.\n"
        "Rules:\n"
        "1) Output English only.\n"
        "2) No explanations, no brackets, no metadata.\n"
        "3) Preserve meaning and named entities.\n"
    )
    if source_lang:
        preface += f"Source language (hint): {source_lang}\n"
    return f"{preface}\nText:\n{text.strip()}\n\nEnglish:"

@app.function_name(name="Translate")
@app.route(route="translate", methods=["POST", "OPTIONS"])
def translate(req: func.HttpRequest) -> func.HttpResponse:
    # CORS preflight
    if req.method == "OPTIONS":
        return _ok({"ok": True})

    if Credentials is None or Model is None:
        return _bad_request(
            "Server missing IBM watsonx SDK. Make sure requirements are installed.",
            500,
        )

    try:
        payload = req.get_json()
    except Exception:
        return _bad_request("Request body must be JSON with at least a 'text' field.")

    text = (payload.get("text") or "").strip()
    source_lang = (payload.get("source_lang") or "").strip() or None
    max_new_tokens = int(payload.get("max_new_tokens", 256))

    if not text:
        return _bad_request("Missing 'text' to translate.")

    # Env/config
    api_key = "****"
    url = os.getenv("WATSONX_URL", "https://us-south.ml.cloud.ibm.com")
    project_id = "075473a2-da4b-483d-ab44-cd7538922595"

    if not api_key or not project_id:
        return _bad_request(
            "Server not configured. Set WATSONX_API_KEY and WATSONX_PROJECT_ID.", 500
        )

    model_id = os.getenv("WATSONX_MODEL_ID", "ibm/granite-3-8b-instruct")
    decoding = os.getenv("WATSONX_DECODING", "greedy")  # 'greedy' or 'sample'
    temperature = float(os.getenv("WATSONX_TEMPERATURE", "0.0"))

    try:
        creds = Credentials(url=url, api_key=api_key)
        params = {
            "decoding_method": decoding,
            "temperature": temperature,
            "max_new_tokens": max_new_tokens,
            "top_p": 1.0,
            "stop_sequences": [],
        }

        model = Model(
            model_id=model_id,
            credentials=creds,
            params=params,
            project_id=project_id,
        )

        prompt = _build_prompt(text, source_lang)
        result = model.generate_text(prompt=prompt)

        if isinstance(result, dict):
            translation = (
                result.get("results", [{}])[0].get("generated_text")
                or result.get("generated_text")
                or ""
            )
        else:
            translation = str(result)

        translation = translation.strip()
        if not translation:
            return _bad_request("Model returned empty output.", 502)

        return _ok(
            {
                "translated_text": translation,
                "model_id": model_id,
                "tokens_max": max_new_tokens,
            }
        )

    except Exception as e:
        logging.exception("watsonx translation error")
        return _bad_request(f"Translation failed: {e}", 502)
