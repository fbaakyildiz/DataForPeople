import base64, json, asyncio, io, ipaddress, os, re, socket, time
from typing import Optional
from urllib.parse import urlparse
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from huggingface_hub import InferenceClient
from pydantic import BaseModel

# ── constants ─────────────────────────────────────────────────────────────────
# No API keys live in this file. Each request carries the caller's own
# Gemini / Hugging Face keys (entered once in the browser, stored in localStorage).
# An env var fallback exists only for convenience when calling the API directly
# (e.g. with curl) outside the browser UI.

GEMINI_TEXT_MODEL   = "gemini-2.5-flash"
HF_IMAGE_MODEL      = "black-forest-labs/FLUX.1-dev"
GEMINI_ENV_FALLBACK = os.environ.get("GEMINI_API_KEY", "")
HF_ENV_FALLBACK     = os.environ.get("HF_TOKEN", "")
ALLOWED_ORIGINS     = {
    "http://localhost:8000",
    "http://127.0.0.1:8000",
}

# ── helpers ───────────────────────────────────────────────────────────────────

def parse_json(text: str):
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    return json.loads(text.strip())

def validate_public_url(raw_url: str) -> str:
    url = raw_url.strip()
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        raise HTTPException(400, "Invalid URL")

    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(400, "Invalid URL")

    blocked_hosts = {"localhost"}
    if hostname.lower() in blocked_hosts or hostname.lower().endswith(".local"):
        raise HTTPException(400, "Local URLs are not allowed")

    try:
        addresses = socket.getaddrinfo(hostname, None)
    except socket.gaierror:
        raise HTTPException(400, "URL host could not be resolved")

    for addr in addresses:
        ip = ipaddress.ip_address(addr[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            raise HTTPException(400, "Private network URLs are not allowed")

    return url

async def call_gemini_text(system: str, user: str, api_key: str) -> str:
    if not api_key:
        raise HTTPException(400, "Missing Gemini API key")

    backoffs = [3, 8, 20]  # retry schedule for 429 RESOURCE_EXHAUSTED
    last_message = "unknown error"
    async with httpx.AsyncClient(timeout=60) as client:
        for attempt, wait in enumerate([0] + backoffs):
            if wait:
                await asyncio.sleep(wait)
            r = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_TEXT_MODEL}:generateContent?key={api_key}",
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [{"text": system + "\n\n" + user}]}]},
            )
            data = r.json()
            if "error" not in data:
                try:
                    return data["candidates"][0]["content"]["parts"][0]["text"]
                except (KeyError, IndexError) as e:
                    raise HTTPException(500, f"Gemini response malformed: {e} — got: {str(data)[:300]}")
            err = data["error"]
            last_message = err.get("message", str(err))
            if err.get("status") == "RESOURCE_EXHAUSTED" and attempt < len(backoffs):
                continue  # rate-limited, back off and retry
            code = 401 if err.get("status") in ("UNAUTHENTICATED", "PERMISSION_DENIED", "INVALID_ARGUMENT") else 500
            raise HTTPException(code, f"Gemini error: {last_message}")
    raise HTTPException(429, f"Gemini rate limit exceeded after retries: {last_message}")

async def call_gemini(system: str, user: str, api_key: str) -> dict:
    text = await call_gemini_text(system, user, api_key)
    try:
        return parse_json(text)
    except json.JSONDecodeError as e:
        raise HTTPException(500, f"Gemini returned invalid JSON: {e} — raw: {text[:300]}")

async def fetch_article(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        html = r.text
        clean = re.sub(r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", "", html, flags=re.S|re.I)
        clean = re.sub(r"<[^>]+>", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean[:6000]
    except Exception as e:
        return f"Could not fetch article. URL: {url}. Error: {e}"

HF_VARIANT_SEEDS = {"A": 1, "B": 2, "C": 3}

async def generate_image(prompt: str, variant: str, hf_key: str) -> Optional[str]:
    if not hf_key:
        print(f"Hugging Face skipped variant {variant}: no API token provided")
        return None

    full_prompt = (
        f"Photorealistic, cinematic scene, no text overlays, no charts, no graphs, "
        f"no faces as primary subject. {prompt}"
    )
    try:
        client = InferenceClient(provider="fal-ai", api_key=hf_key)
        image = await asyncio.to_thread(
            client.text_to_image,
            full_prompt,
            model=HF_IMAGE_MODEL,
            width=1024,
            height=576,
            seed=HF_VARIANT_SEEDS.get(variant, 0),
        )
        buf = io.BytesIO()
        image.save(buf, format="WEBP")
        b64 = base64.b64encode(buf.getvalue()).decode()
        return f"data:image/webp;base64,{b64}"

    except Exception as e:
        print(f"Hugging Face exception variant {variant}: {e}")
        return None

# ── agent system prompts ──────────────────────────────────────────────────────

A1_SYS = """You are a precise analyst. Compress information, never invent.
Find what a visual can encode. Find the single most important human truth — that is core_tension.
Output only valid JSON, no preamble, no markdown fences."""

A2_SYS = """You are a visual poet and precise prompt engineer for AI image models.
No memory of past runs. Each dataset is a new canvas.
Output only valid JSON, no preamble, no markdown fences."""

CRITIC_SYS = """You are a visual critic inspecting an AI-generated image for a data journalism piece.
Identify specific visual problems and produce an improved generation prompt.
Be precise: name what is wrong, not just that something is wrong.
Output only valid JSON, no preamble, no markdown fences."""

A3_SYS = """You are a rigorous visual quality gate and editorial director.
Score what you observe. Route to publish only if the image genuinely meets the bar.
Output only valid JSON, no preamble, no markdown fences."""

# ── A1: Ingestion ─────────────────────────────────────────────────────────────

async def run_a1(url: str, text: str, api_key: str) -> dict:
    return await call_gemini(A1_SYS, f"""
Extract only what a visual can encode. Output JSON exactly:
{{"headline":"string","core_tension":"one sentence — human meaning of the data",
"key_facts":["up to 8 strings, each under 15 words"],
"numbers":[{{"label":"string","value":0,"unit":"string"}}],
"domain":"string","tone":"alarming|hopeful|neutral|complex|urgent",
"sensitive":false,"source_url":"{url}"}}
Source:\n{text}""", api_key)

# ── A2: Concept + Prompt Engineer ─────────────────────────────────────────────

async def run_a2(a1: dict, api_key: str) -> dict:
    return await call_gemini(A2_SYS, f"""
Read core_tension first — it is your brief. Invent a visual metaphor. Map data to visual properties.
Output JSON exactly:
{{"concept_title":"string","metaphor":"one sentence",
"data_mappings":[{{"datum":"string","visual_property":"string"}}],
"format":"image","generation_prompt":"string — vivid cinematic scene, no text/charts/graphs/faces as primary",
"negative_prompt":"string","model_params":{{"aspect_ratio":"16:9","style_tags":["cinematic","photorealistic"]}},
"accuracy_constraints":["string"]}}
Input: {json.dumps(a1)}""", api_key)

# ── Gen ×3 ────────────────────────────────────────────────────────────────────

async def run_generation(prompt: str, hf_key: str) -> list:
    results = await asyncio.gather(
        generate_image(prompt, "A", hf_key),
        generate_image(prompt, "B", hf_key),
        generate_image(prompt, "C", hf_key),
        return_exceptions=True,
    )
    return [r if isinstance(r, str) else None for r in results]

# ── Critic Loop (PaperBanana Visualizer-Critic, T=3, best-of-rounds) ─────────
#
# Improvement over the original PaperBanana loop: each round's images are
# scored (issues found, core_tension_readable, data_mappings_visible) and the
# best-scoring round is kept, not just the last one. A refined prompt can
# regenerate a worse image than an earlier round — without this, that
# regression would silently become the final result.

async def run_critic_round(images: list, a1: dict, a2: dict, api_key: str) -> dict:
    image_uri = next((i for i in images if i), None)
    if not image_uri:
        return {
            "issues_found": ["no image available to critique"],
            "data_mappings_visible": False,
            "core_tension_readable": False,
            "refined_prompt": a2["generation_prompt"],
        }

    m = re.match(r"data:([^;]+);base64,(.+)", image_uri, re.S)
    mime_type = m.group(1)
    b64_data  = m.group(2)

    critic_prompt = f"""Inspect this image carefully against the brief below.

core_tension: {a1.get("core_tension", "")}
key_facts: {json.dumps(a1.get("key_facts", []))}
metaphor: {a2.get("metaphor", "")}
data_mappings: {json.dumps(a2.get("data_mappings", []))}

Output JSON exactly:
{{"issues_found":["specific visual problems — empty list if none"],
"data_mappings_visible":true,
"core_tension_readable":true,
"refined_prompt":"improved generation prompt that fixes the issues — keep vivid and cinematic"}}"""

    if not api_key:
        raise HTTPException(400, "Missing Gemini API key")

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_TEXT_MODEL}:generateContent?key={api_key}",
                headers={"Content-Type": "application/json"},
                json={"contents": [{"parts": [
                    {"text": CRITIC_SYS + "\n\n" + critic_prompt},
                    {"inline_data": {"mime_type": mime_type, "data": b64_data}},
                ]}]},
            )
        data = r.json()
        if "error" in data:
            raise ValueError(f"Critic API error: {data['error']}")
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        return parse_json(text)
    except Exception as e:
        print(f"Critic round failed (returning clean): {e}")
        return {
            "issues_found": [],
            "data_mappings_visible": False,
            "core_tension_readable": False,
            "refined_prompt": a2["generation_prompt"],
        }

def _round_score(critic: dict) -> int:
    score = -len(critic.get("issues_found", []))
    if critic.get("core_tension_readable"):
        score += 5
    if critic.get("data_mappings_visible"):
        score += 3
    return score

async def run_critic_loop(a1: dict, a2: dict, images: list, log_fn, api_key: str, hf_key: str) -> tuple:
    all_issues     = []
    current_images = images
    current_prompt = a2["generation_prompt"]
    best_images    = images
    best_score     = None
    best_round     = 0

    for round_num in range(1, 4):
        critic = await run_critic_round(current_images, a1, a2, api_key)
        issues = critic.get("issues_found", [])
        all_issues.extend(issues)

        score = _round_score(critic)
        if best_score is None or score > best_score:
            best_score, best_images, best_round = score, current_images, round_num

        if not issues or critic.get("core_tension_readable"):
            log_fn(f"Critic round {round_num}: clean — stopping early")
            return best_images, round_num, all_issues

        log_fn(f"Critic round {round_num}: found {len(issues)} issues, refining…")
        current_prompt = critic.get("refined_prompt") or current_prompt
        current_images = await run_generation(current_prompt, hf_key)

    log_fn(f"Critic loop complete after {round_num} rounds — best result from round {best_round}")
    return best_images, round_num, all_issues

# ── A3: Final Scorer + Router ─────────────────────────────────────────────────

async def run_a3(a1: dict, a2: dict, images: list, api_key: str) -> dict:
    gen_status = [
        f"Variant {v}: {'image generated OK' if images[i] else 'generation FAILED'}"
        for i, v in enumerate(["A", "B", "C"])
    ]
    return await call_gemini(A3_SYS, f"""
Score the 3 image variants. Describe each variant before scoring.
Weights: accuracy×0.35 + legibility×0.30 + distortion×0.25 + tone_match×0.10
Route: confidence≥7.0 → publish | confidence<7.0 → human_queue
Hard flags always route to human_queue: out_of_context, too_abstract, sensitive_topic, data_contradiction
Output JSON exactly:
{{"variant_descriptions":{{"A":"string","B":"string","C":"string"}},
"scores":{{"A":{{"accuracy":0,"legibility":0,"distortion":0,"tone_match":0,"composite":0}},
"B":{{"accuracy":0,"legibility":0,"distortion":0,"tone_match":0,"composite":0}},
"C":{{"accuracy":0,"legibility":0,"distortion":0,"tone_match":0,"composite":0}}}},
"winner_variant":"A","confidence":0,
"verdict":"publish|human_queue",
"flags":[],"visual_description":"string","alt_text":"string"}}
A1: {json.dumps(a1)}
A2: {json.dumps({k: a2[k] for k in ["concept_title","metaphor","data_mappings","accuracy_constraints"] if k in a2})}
Generation: {"; ".join(gen_status)}""", api_key)

# ── app ───────────────────────────────────────────────────────────────────────

app = FastAPI(title="Visual Storytelling Pipeline")

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(ALLOWED_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── endpoints ─────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    url: str
    gemini_key: Optional[str] = None
    hf_key: Optional[str] = None

@app.post("/run")
async def run_pipeline(req: RunRequest):
    url = validate_public_url(req.url)

    gemini_key = (req.gemini_key or GEMINI_ENV_FALLBACK).strip()
    hf_key     = (req.hf_key or HF_ENV_FALLBACK).strip()
    if not gemini_key:
        raise HTTPException(400, "Missing Gemini API key — add it in Settings")
    if not hf_key:
        raise HTTPException(400, "Missing Hugging Face API token — add it in Settings")

    t0  = time.time()
    log = []
    def step(msg): log.append(msg); print(msg)

    # A1
    step("A1: fetching article…")
    article_text = await fetch_article(url)
    step("A1: running ingestion agent…")
    a1 = await run_a1(url, article_text, gemini_key)
    step(f"A1 done — headline: {a1.get('headline', '?')[:60]}")

    # A2
    step("A2: concepting and prompt engineering…")
    a2 = await run_a2(a1, gemini_key)
    step(f"A2 done — concept: {a2.get('concept_title', '?')}")

    # Gen ×3
    step("GEN: generating 3 variants in parallel…")
    images = await run_generation(a2["generation_prompt"], hf_key)
    ok = sum(1 for i in images if i)
    step(f"GEN done — {ok}/3 images OK")
    if ok == 0:
        raise HTTPException(500, "Image generation failed: all 3 variants returned None — check your Hugging Face token/credits")

    # Critic loop
    step("CRITIC: starting visualizer-critic loop (max 3 rounds)…")
    images, critic_rounds, critic_issues = await run_critic_loop(a1, a2, images, step, gemini_key, hf_key)
    step(f"CRITIC done — {critic_rounds} round(s), {len(critic_issues)} total issues")

    # A3
    step("A3: scoring and routing…")
    a3 = await run_a3(a1, a2, images, gemini_key)
    step(f"A3 verdict: {a3.get('verdict')} | confidence: {a3.get('confidence')}")

    elapsed    = time.time() - t0
    winner_idx = {"A": 0, "B": 1, "C": 2}.get(a3.get("winner_variant", "A"), 0)

    return {
        "url": url,
        "elapsed_s": round(elapsed, 1),
        "a1": a1,
        "a2": a2,
        "critic_rounds": critic_rounds,
        "critic_issues": critic_issues,
        "a3": a3,
        "images": images,
        "winner_image": images[winner_idx],
        "log": log,
    }

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "text_model": GEMINI_TEXT_MODEL,
        "image_model": HF_IMAGE_MODEL,
    }

# Serve frontend
app.mount("/static", StaticFiles(directory="static", html=True), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")
