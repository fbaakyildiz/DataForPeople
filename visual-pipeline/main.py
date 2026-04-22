import os, json, asyncio, base64, re, time
from typing import Optional
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

app = FastAPI(title="Visual Storytelling Pipeline")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

ANTHROPIC_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
GEMINI_KEY    = os.environ.get("GEMINI_API_KEY", "AIzaSyAAI-jmRIuk01VIIL79IzQGWEBHtvDs970")
GEMINI_MODEL  = "gemini-3-pro-image-preview"
CLAUDE_MODEL  = "claude-sonnet-4-20250514"

# ── helpers ──────────────────────────────────────────────────────────────────

def parse_json(text: str) -> dict:
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    return json.loads(text.strip())

async def call_claude(system: str, user: str, max_tokens: int = 1200) -> dict:
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": ANTHROPIC_KEY,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": CLAUDE_MODEL,
                "max_tokens": max_tokens,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            },
        )
    data = r.json()
    if "error" in data:
        raise HTTPException(500, f"Claude error: {data['error']['message']}")
    text = "".join(c.get("text", "") for c in data["content"])
    return parse_json(text)

async def fetch_article(url: str) -> str:
    try:
        async with httpx.AsyncClient(timeout=15, follow_redirects=True) as client:
            r = await client.get(url, headers={"User-Agent": "Mozilla/5.0"})
        html = r.text
        # strip tags crudely
        clean = re.sub(r"<(script|style|nav|header|footer)[^>]*>.*?</\1>", "", html, flags=re.S|re.I)
        clean = re.sub(r"<[^>]+>", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        return clean[:6000]
    except Exception as e:
        return f"Could not fetch article. URL: {url}. Error: {e}"

async def generate_image(prompt: str, variant: str) -> Optional[str]:
    """Call Gemini and return base64 data-URI or None."""
    body = {
        "contents": [{"parts": [{"text": (
            f"Generate a photorealistic, cinematic image for variant {variant}. "
            "No text overlays, no charts, no graphs, no faces as primary subject. "
            f"Scene: {prompt}"
        )}]}],
        "generationConfig": {"responseModalities": ["TEXT", "IMAGE"]},
    }
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={GEMINI_KEY}"
    )
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.post(url, json=body)
        data = r.json()
        if "error" in data:
            print(f"Gemini error variant {variant}: {data['error']}")
            return None
        parts = data.get("candidates", [{}])[0].get("content", {}).get("parts", [])
        for part in parts:
            if "inlineData" in part:
                mime = part["inlineData"]["mimeType"]
                b64  = part["inlineData"]["data"]
                return f"data:{mime};base64,{b64}"
    except Exception as e:
        print(f"Gemini exception variant {variant}: {e}")
    return None

# ── agent prompts ─────────────────────────────────────────────────────────────

A1_SYS = """You are a precise analyst. Compress information, never invent.
Find what a visual can encode. Find the single most important human truth — that is core_tension.
Output only valid JSON, no preamble, no markdown fences."""

A2_SYS = """You are a visual poet and precise prompt engineer for AI image models.
No memory of past runs. Each dataset is a new canvas.
Output only valid JSON, no preamble, no markdown fences."""

A3_SYS = """You are a rigorous visual fact-checker and editorial director.
Describe what you see before scoring. Never score what you assumed.
Output only valid JSON, no preamble, no markdown fences."""

A0_SYS = """You are a self-auditing meta-agent for a visual storytelling pipeline.
Your job: evaluate whether a pipeline run truly succeeded end-to-end.
Be strict. Output only valid JSON, no preamble, no markdown fences."""

# ── pipeline logic ────────────────────────────────────────────────────────────

async def run_a1(url: str, text: str) -> dict:
    return await call_claude(A1_SYS, f"""
Extract only what a visual can encode. Output JSON exactly:
{{"headline":"string","core_tension":"one sentence — human meaning of the data",
"key_facts":["up to 8 strings, each under 15 words"],
"numbers":[{{"label":"string","value":0,"unit":"string"}}],
"domain":"string","tone":"alarming|hopeful|neutral|complex|urgent",
"sensitive":false,"source_url":"{url}"}}
Source:\n{text}""", 800)

async def run_a2(a1: dict, retry_note: Optional[str] = None) -> dict:
    retry_block = f"\nRETRY — invert your previous approach: {retry_note}" if retry_note else ""
    return await call_claude(A2_SYS, f"""
Read core_tension first — it is your brief. Invent a visual metaphor. Map data to visual properties.{retry_block}
Output JSON exactly:
{{"concept_title":"string","metaphor":"one sentence",
"data_mappings":[{{"datum":"string","visual_property":"string"}}],
"format":"image","generation_prompt":"string — vivid cinematic scene, no text/charts/graphs/faces as primary",
"negative_prompt":"string","model_params":{{"aspect_ratio":"16:9","style_tags":["cinematic","photorealistic"]}},
"accuracy_constraints":["string"],"retry_note":null}}
Input: {json.dumps(a1)}""", 1000)

async def run_generation(prompt: str) -> list:
    results = await asyncio.gather(
        generate_image(prompt, "A"),
        generate_image(prompt, "B"),
        generate_image(prompt, "C"),
        return_exceptions=True,
    )
    return [r if isinstance(r, str) else None for r in results]

async def run_a3(a1: dict, a2: dict, images: list, run_index: int) -> dict:
    gen_status = [
        f"Variant {v}: {'image generated OK' if images[i] else 'generation FAILED'}"
        for i, v in enumerate(["A", "B", "C"])
    ]
    return await call_claude(A3_SYS, f"""
Score 3 image variants. Describe each, then score.
Weights: accuracy×0.35 + legibility×0.30 + distortion×0.25 + tone_match×0.10
Route: confidence≥7.5 → publish | 6.0-7.4 AND run_index<2 → retry | else → human_queue
Hard flags (always human_queue): out_of_context, too_abstract, sensitive_topic, data_contradiction
Output JSON exactly:
{{"variant_descriptions":{{"A":"string","B":"string","C":"string"}},
"scores":{{"A":{{"accuracy":0,"legibility":0,"distortion":0,"tone_match":0,"composite":0}},
"B":{{"accuracy":0,"legibility":0,"distortion":0,"tone_match":0,"composite":0}},
"C":{{"accuracy":0,"legibility":0,"distortion":0,"tone_match":0,"composite":0}}}},
"winner_variant":"A","confidence":0,
"verdict":"publish|retry|human_queue",
"flags":[],"retry_note":null,"human_reason":null,
"visual_description":"string","alt_text":"string","approved_at":null}}
A1: {json.dumps(a1)}
A2: {json.dumps({{k:a2[k] for k in ["concept_title","metaphor","data_mappings","accuracy_constraints"] if k in a2}})}
Generation: {"; ".join(gen_status)}
run_index: {run_index}""", 1000)

async def run_a0_audit(url: str, a1: dict, a2: dict, a3: dict,
                       images: list, run_index: int, elapsed_s: float) -> dict:
    """Meta-agent: audits the entire run end-to-end and gives overall confidence."""
    img_ok = sum(1 for i in images if i)
    return await call_claude(A0_SYS, f"""
Audit this complete pipeline run and decide if the system is working correctly.
Check: Did A1 extract meaningful data? Did A2 produce a coherent visual metaphor?
Did image generation succeed ({img_ok}/3 images OK)? Did A3 score fairly?
Is the verdict ({a3.get("verdict")}) justified by the confidence ({a3.get("confidence")})?
Are there any signs of hallucination, JSON errors, or concept/data mismatch?

Output JSON exactly:
{{"overall_confidence": 0.0,
"pipeline_healthy": true,
"issues": ["list any problems found"],
"a1_quality": "good|weak|failed",
"a2_quality": "good|weak|failed",
"generation_quality": "good|partial|failed",
"a3_quality": "good|weak|failed",
"recommendation": "accept|retry_full|flag_for_human",
"summary": "one sentence on whether this run is trustworthy"}}

URL: {url}
A1 headline: {a1.get("headline","")}
A1 core_tension: {a1.get("core_tension","")}
A2 concept: {a2.get("concept_title","")} — {a2.get("metaphor","")}
Images generated: {img_ok}/3
A3 verdict: {a3.get("verdict")} | confidence: {a3.get("confidence")} | flags: {a3.get("flags",[])}
A3 visual_description: {a3.get("visual_description","")}
run_index: {run_index} | elapsed: {elapsed_s:.1f}s""", 600)

# ── main pipeline endpoint ────────────────────────────────────────────────────

class RunRequest(BaseModel):
    url: str

@app.post("/run")
async def run_pipeline(req: RunRequest):
    url = req.url.strip()
    if not url.startswith("http"):
        raise HTTPException(400, "Invalid URL")

    t0 = time.time()
    log = []

    def step(msg): log.append(msg); print(msg)

    # A1
    step("A1: fetching article…")
    article_text = await fetch_article(url)
    step("A1: running ingestion agent…")
    a1 = await run_a1(url, article_text)
    step(f"A1 done — headline: {a1.get('headline','?')[:60]}")

    a2, images, a3 = None, [None, None, None], None

    for run_index in range(3):  # max 3 attempts
        step(f"A2: concepting (run {run_index})…")
        retry_note = a3.get("retry_note") if a3 else None
        a2 = await run_a2(a1, retry_note)
        step(f"A2 done — concept: {a2.get('concept_title','?')}")

        step("GEN: generating 3 Gemini variants in parallel…")
        images = await run_generation(a2["generation_prompt"])
        ok = sum(1 for i in images if i)
        step(f"GEN done — {ok}/3 images OK")

        step("A3: auditing…")
        a3 = await run_a3(a1, a2, images, run_index)
        step(f"A3 verdict: {a3.get('verdict')} | confidence: {a3.get('confidence')}")

        if a3.get("verdict") == "publish":
            break
        if a3.get("verdict") == "human_queue":
            break
        # verdict == retry → loop

    # A0 meta-audit
    step("A0: self-auditing full run…")
    elapsed = time.time() - t0
    a0 = await run_a0_audit(url, a1, a2, a3, images, run_index, elapsed)
    step(f"A0 overall_confidence: {a0.get('overall_confidence')} | {a0.get('summary','')[:80]}")

    winner_idx = {"A": 0, "B": 1, "C": 2}.get(a3.get("winner_variant", "A"), 0)

    return {
        "url": url,
        "run_index": run_index,
        "elapsed_s": round(elapsed, 1),
        "a1": a1,
        "a2": a2,
        "a3": a3,
        "a0": a0,
        "images": images,          # list of 3 base64 data-URIs or None
        "winner_image": images[winner_idx],
        "log": log,
    }

# ── utility endpoints ─────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {"status": "ok", "gemini_model": GEMINI_MODEL, "claude_model": CLAUDE_MODEL}

# Serve frontend (index.html in /static)
app.mount("/static", StaticFiles(directory="static", html=True), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")
