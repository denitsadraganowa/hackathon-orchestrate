import os
import json
import logging
import requests
from typing import Any, Dict, List, Optional
from urllib.parse import quote_plus

import azure.functions as func

logging.basicConfig(level=logging.INFO)

# =========================
# Config / API Endpoints
# =========================
GITHUB_API = "https://api.github.com"
STACK_API = "https://api.stackexchange.com/2.3"
STACK_SITE = os.getenv("STACKEXCHANGE_SITE", "stackoverflow")
HF_API_MODELS = "https://huggingface.co/api/models"               # ?search=<q>
PWC_API_PAPERS = "https://paperswithcode.com/api/v1/papers/"      # ?q=<q>

GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "***")
STACK_KEY = os.getenv("STACKEXCHANGE_KEY", "")
HF_TOKEN = os.getenv("HF_TOKEN", "***")

# Kaggle (optional) - requires kaggle package & credentials
KAGGLE_ENABLED = False
try:
    import kaggle  # type: ignore
    kaggle_username = "denitsadraganova"
    kaggle_key = "0b510b48476861cd7362c49efc60bbc0"
    # Kaggle library uses ~/.kaggle/kaggle.json or env KAGGLE_USERNAME/KAGGLE_KEY
    if kaggle_username and kaggle_key:
        KAGGLE_ENABLED = True
except Exception:
    KAGGLE_ENABLED = False

REQ_TIMEOUT = 20
DEFAULT_MAX = 10
PER_SOURCE_CAP = 20

# =========================
# Utilities
# =========================
def _http_get(url: str, headers: Optional[Dict[str, str]] = None, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    r = requests.get(url, headers=headers or {}, params=params or {}, timeout=REQ_TIMEOUT)
    # allow 429/403 to surface cleanly
    r.raise_for_status()
    try:
        return r.json()
    except Exception:
        return {"raw": r.text}

def _score_match(text: str, terms: List[str]) -> float:
    if not text:
        return 0.0
    t = text.lower()
    score = 0.0
    for term in terms:
        if not term:
            continue
        term = term.lower()
        if term in t:
            score += 2.0
        score += t.count(term) * 0.5
    return score

def _build_terms(ideas: List[str], extra_keywords: Optional[List[str]]) -> List[str]:
    import re
    stop = set("and or for the a an to of on in with by from at into over under across".split())
    tokens: List[str] = []

    def add_tokens(s: str):
        s = re.sub(r"[^\w\s\-+/]", " ", s)      # drop punctuation keeping hyphens
        parts = []
        for w in s.split():
            # split hyphenated / slashed terms: "LLM-powered" -> ["LLM","powered"]
            parts.extend(re.split(r"[-/+]", w))
        for w in parts:
            w = w.strip()
            if len(w) >= 2 and w.lower() not in stop:
                tokens.append(w)

    for s in ideas:
        add_tokens(s)
    if extra_keywords:
        for k in extra_keywords:
            if isinstance(k, str):
                add_tokens(k)

    # expand synonyms/aliases
    expanded: List[str] = []
    for t in tokens:
        tl = t.lower()
        expanded.append(t)
        if tl == "llm":
            expanded.extend(["large language model"])
        if tl == "rag":
            expanded.extend(["retrieval augmented generation"])
        if tl in ("geospatial", "gis"):
            expanded.extend(["geopandas", "openstreetmap", "osmnx", "mapbox"])
        if tl == "langchain":
            expanded.append("chatbot")

    # dedupe preserving order, cap length
    seen = set()
    out = []
    for t in expanded:
        key = t.lower()
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out[:20]

def _dedupe_keep_best(items: List[Dict[str, Any]], key_fields=("profile_url", "username")) -> List[Dict[str, Any]]:
    bykey: Dict[str, Dict[str, Any]] = {}
    for it in items:
        k = "||".join((it.get(key_fields[0]) or "").lower() + "|" + (it.get(key_fields[1]) or "").lower())
        if k in bykey:
            if it.get("score", 0) > bykey[k].get("score", 0):
                bykey[k] = it
        else:
            bykey[k] = it
    return list(bykey.values())

def _clip(items: List[Dict[str, Any]], n: int) -> List[Dict[str, Any]]:
    return sorted(items, key=lambda x: x.get("score", 0), reverse=True)[:n]

# =========================
# GitHub
# =========================
def search_github_users(terms: List[str], location: Optional[str], per_page: int = 10) -> List[Dict[str, Any]]:
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    def run(q: str) -> List[Dict[str, Any]]:
        data = _http_get(f"{GITHUB_API}/search/users", headers, {"q": q, "per_page": per_page})
        return data.get("items", []) if isinstance(data, dict) else []

    # Build an OR query from top terms
    key_terms = [t for t in terms if len(t) >= 3][:6]
    if not key_terms:
        key_terms = ["developer"]

    or_block = " OR ".join([f'"{t}"' for t in key_terms])
    qualifiers = "in:login in:name in:bio in:readme"
    q_core = f"({or_block}) {qualifiers}"

    # Pass A: targeted + location
    qA = q_core + (f" location:{location}" if location else "")
    items = run(qA)

    # Pass B: broader, no location
    if not items:
        qB = f"({or_block}) in:login in:name in:bio"
        items = run(qB)

    out: List[Dict[str, Any]] = []
    for u in items:
        username = u.get("login")
        if not username:
            continue
        user = _http_get(f"{GITHUB_API}/users/{username}", headers)
        repos = _http_get(
            f"{GITHUB_API}/users/{username}/repos",
            headers,
            {"sort": "stars", "direction": "desc", "per_page": 5},
        )
        repo_list = repos if isinstance(repos, list) else []
        text = " ".join([
            str(user.get("bio") or ""),
            str(user.get("company") or ""),
            str(user.get("blog") or ""),
            " ".join([str(r.get("name") or "") for r in repo_list]),
        ])
        score = _score_match(text, terms)
        out.append({
            "source": "github",
            "name": user.get("name") or username,
            "username": username,
            "profile_url": user.get("html_url") or f"https://github.com/{username}",
            "public_email": user.get("email"),
            "blog": user.get("blog"),
            "location": user.get("location"),
            "company": user.get("company"),
            "evidence": {
                "top_repos": [
                    {"name": r.get("name"),
                     "url": r.get("html_url"),
                     "stars": r.get("stargazers_count"),
                     "language": r.get("language")}
                    for r in repo_list
                ]
            },
            "score": score + min(len(repo_list), 5) * 0.3,
        })
    return out

# =========================
# StackExchange (Stack Overflow)
# =========================
def search_stackexchange_users(terms: List[str], location: Optional[str], pagesize: int = 10) -> List[Dict[str, Any]]:
    top_term = terms[0] if terms else "engineer"
    params = {"order": "desc", "sort": "reputation", "inname": top_term, "site": STACK_SITE, "pagesize": pagesize}
    if STACK_KEY:
        params["key"] = STACK_KEY
    users = _http_get(f"{STACK_API}/users", params=params)
    out: List[Dict[str, Any]] = []
    for u in users.get("items", []):
        uid = u.get("user_id")
        display = u.get("display_name")
        profile_url = u.get("link")
        # Fetch top tags
        tparams = {"site": STACK_SITE, "pagesize": 5}
        if STACK_KEY:
            tparams["key"] = STACK_KEY
        top_tags = _http_get(f"{STACK_API}/users/{uid}/top-tags", params=tparams)
        tag_names = [it.get("tag_name") for it in top_tags.get("items", []) if it.get("tag_name")]
        text = " ".join((display or "", " ".join(tag_names)))
        score = _score_match(text, terms)
        if location and u.get("location") and location.lower() in str(u.get("location")).lower():
            score += 1.0
        out.append({
            "source": "stackexchange",
            "name": display,
            "username": str(uid),
            "profile_url": profile_url,
            "public_email": None,  # API does not expose
            "blog": u.get("website_url"),
            "location": u.get("location"),
            "company": None,
            "evidence": {"top_tags": tag_names},
            "score": score
        })
    return out

# =========================
# Hugging Face (via models)
# =========================
def search_huggingface_authors(terms: List[str], max_models: int = 30) -> List[Dict[str, Any]]:
    q = " ".join(terms[:4])
    headers = {}
    if HF_TOKEN:
        headers["Authorization"] = f"Bearer {HF_TOKEN}"
    params = {"search": q}
    models = _http_get(HF_API_MODELS, headers=headers, params=params)
    # models is a list; group by "author"
    by_author: Dict[str, List[Dict[str, Any]]] = {}
    if isinstance(models, list):
        for m in models[:max_models]:
            author = m.get("author") or m.get("modelId", "").split("/")[0]
            if not author:
                continue
            by_author.setdefault(author, []).append(m)
    out: List[Dict[str, Any]] = []
    for author, amodels in by_author.items():
        namestr = author
        profile_url = f"https://huggingface.co/{author}"
        # build evidence
        top = []
        text_blob = []
        for m in amodels[:5]:
            mid = m.get("modelId") or ""
            likes = m.get("likes") or 0
            downloads = m.get("downloads") or 0
            top.append({"model": mid, "likes": likes, "downloads": downloads, "url": f"https://huggingface.co/{mid}"})
            text_blob.extend([mid, str(likes), str(downloads)])
        score = _score_match(" ".join(text_blob), terms) + min(len(amodels), 5) * 0.2
        out.append({
            "source": "huggingface",
            "name": namestr,
            "username": author,
            "profile_url": profile_url,
            "public_email": None,
            "blog": None,
            "location": None,
            "company": None,
            "evidence": {"top_models": top},
            "score": score
        })
    return out

# =========================
# Papers with Code
# =========================
def search_paperswithcode_authors(terms: List[str], per_page: int = 25) -> List[Dict[str, Any]]:
    q = " ".join(terms[:6])
    params = {"q": q, "page_size": per_page}
    papers = _http_get(PWC_API_PAPERS, params=params)
    out: List[Dict[str, Any]] = []
    if isinstance(papers, dict):
        for p in papers.get("results", []):
            title = p.get("title")
            url_abs = p.get("url_abs")
            authors = p.get("authors") or []
            repo = p.get("repository") or {}
            repo_url = repo.get("url")
            # Suggest each author with their paper as evidence
            for a in authors[:3]:  # limit authors per paper
                name = a.get("name") if isinstance(a, dict) else str(a)
                # profile url best-effort: search the author on paperswithcode (no direct profile API)
                profile_url = None
                if isinstance(a, dict) and a.get("profile_url"):
                    profile_url = a["profile_url"]
                # fallback: google-scholar-ish search URL on PWC
                if not profile_url:
                    profile_url = f"https://paperswithcode.com/search?q_author={quote_plus(name)}"
                text = f"{title} {name}"
                score = _score_match(text, terms)
                out.append({
                    "source": "paperswithcode",
                    "name": name,
                    "username": None,
                    "profile_url": profile_url,
                    "public_email": None,
                    "blog": None,
                    "location": None,
                    "company": None,
                    "evidence": {"paper": {"title": title, "url": url_abs, "repo": repo_url}},
                    "score": score + (1.0 if repo_url else 0.0)
                })
    return out

# =========================
# Kaggle (optional)
# =========================
def search_kaggle_owners_by_datasets(terms: List[str], max_rows: int = 20) -> List[Dict[str, Any]]:
    """
    Uses kaggle package (if enabled) to list datasets by search terms and promotes dataset owners as candidates.
    Requires env KAGGLE_USERNAME/KAGGLE_KEY and pip package `kaggle`.
    """
    if not KAGGLE_ENABLED:
        return []
    # kaggle.api.dataset_list(search=...) -> list of Dataset
    out: List[Dict[str, Any]] = []
    try:
        q = " ".join(terms[:4])
        ds = kaggle.api.dataset_list(search=q, page=1, max_size=None, min_size=None)  # type: ignore
        # ds is a list of Dataset (objects with attributes)
        rows = ds[:max_rows] if isinstance(ds, list) else []
        by_owner: Dict[str, Dict[str, Any]] = {}
        for d in rows:
            owner = getattr(d, "ownerRef", None) or getattr(d, "ownerUser", None)
            title = getattr(d, "title", None)
            url = f"https://www.kaggle.com/{owner}/{getattr(d, 'datasetSlug', '')}"
            if owner:
                e = by_owner.setdefault(owner, {"count": 0, "datasets": []})
                e["count"] += 1
                e["datasets"].append({"title": title, "url": url})
        for owner, info in by_owner.items():
            text = " ".join([x["title"] for x in info["datasets"] if x.get("title")])
            score = _score_match(text, terms) + min(info["count"], 5) * 0.3
            out.append({
                "source": "kaggle",
                "name": owner,
                "username": owner,
                "profile_url": f"https://www.kaggle.com/{owner}",
                "public_email": None,  # Kaggle API doesn't expose emails
                "blog": None,
                "location": None,
                "company": None,
                "evidence": {"datasets": info["datasets"][:5]},
                "score": score
            })
    except Exception as e:
        logging.warning("Kaggle search failed: %s", e)
    return out

# =========================
# Azure Function
# =========================
app = func.FunctionApp()

@app.function_name(name="suggest_collaborators")
@app.route(route="suggest-collaborators", methods=[func.HttpMethod.POST], auth_level=func.AuthLevel.ANONYMOUS)
def suggest_collaborators(req: func.HttpRequest) -> func.HttpResponse:
    """
    POST /api/suggest-collaborators
    Body:
    {
      "ideas": ["..."],                 # required
      "keywords": ["python","rag"],     # optional
      "location": "Europe",             # optional (coarse filter/boost)
      "max_results": 15                 # optional (default 10)
    }

    Returns:
    {
      "candidates": [ ... ],
      "meta": {
        "query_terms": [...],
        "location": "..."
      }
    }
    """
    try:
        payload = req.get_json()
    except ValueError:
        return func.HttpResponse(json.dumps({"error": "Invalid JSON body"}), status_code=400, mimetype="application/json")

    ideas = payload.get("ideas")
    if not isinstance(ideas, list) or not all(isinstance(x, str) and x.strip() for x in ideas):
        return func.HttpResponse(json.dumps({"error": "'ideas' must be a non-empty list of strings"}), status_code=400, mimetype="application/json")

    keywords = payload.get("keywords") or []
    if not isinstance(keywords, list):
        return func.HttpResponse(json.dumps({"error": "'keywords' must be a list of strings"}), status_code=400, mimetype="application/json")

    location = payload.get("location")
    max_results = int(payload.get("max_results") or DEFAULT_MAX)

    # Build search terms
    terms = _build_terms(ideas, keywords)

    # Fan-out to sources (errors are logged and skipped)
    results: List[Dict[str, Any]] = []
    try:
        results += search_github_users(terms, location, per_page=min(PER_SOURCE_CAP, max_results))
    except Exception as e:
        logging.warning("GitHub search failed: %s", e)
    try:
        results += search_stackexchange_users(terms, location, pagesize=min(PER_SOURCE_CAP, max_results))
    except Exception as e:
        logging.warning("StackExchange search failed: %s", e)
    try:
        results += search_huggingface_authors(terms, max_models=40)
    except Exception as e:
        logging.warning("Hugging Face search failed: %s", e)
    try:
        results += search_paperswithcode_authors(terms, per_page=min(50, max_results*2))
    except Exception as e:
        logging.warning("Papers with Code search failed: %s", e)
    try:
        results += search_kaggle_owners_by_datasets(terms, max_rows=min(40, max_results*2))
    except Exception as e:
        logging.warning("Kaggle search failed: %s", e)

    # Basic location boost (already applied in SO)
    if location:
        for r in results:
            loc = (r.get("location") or "")
            if isinstance(loc, str) and location.lower() in loc.lower():
                r["score"] = r.get("score", 0) + 0.5

    # Dedupe, rank, clip
    deduped = _dedupe_keep_best(results, key_fields=("profile_url", "username"))
    ranked = _clip(deduped, max_results)

    body = {
        "candidates": ranked,
        "meta": {
            "query_terms": terms,
            "location": location
        }
    }
    return func.HttpResponse(json.dumps(body, ensure_ascii=False), mimetype="application/json")
