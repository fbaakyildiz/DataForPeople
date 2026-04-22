import json, asyncio, re, time, pathlib
from collections import Counter
from contextlib import asynccontextmanager
from typing import Optional
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel

# ── deployment state ──────────────────────────────────────────────────────────

DEPLOYMENT_VALIDATED = False
_validation_summary: dict = {}

# ── constants ─────────────────────────────────────────────────────────────────

GEMINI_KEY         = "AIzaSyAAI-jmRIuk01VIIL79IzQGWEBHtvDs970"
GEMINI_TEXT_MODEL  = "gemini-2.5-flash"
GEMINI_IMAGE_MODEL = "imagen-4-generate"

# ── helpers ───────────────────────────────────────────────────────────────────

def parse_json(text: str):
    text = re.sub(r"```json\s*", "", text)
    text = re.sub(r"```\s*", "", text)
    return json.loads(text.strip())

async def call_gemini(system: str, user: str, max_tokens: int = 1200) -> dict:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_TEXT_MODEL}:generateContent?key={GEMINI_KEY}"
    )
    async with httpx.AsyncClient(timeout=60) as client:
        r = await client.post(url, json={
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"maxOutputTokens": max_tokens},
        })
    data = r.json()
    if "error" in data:
        raise HTTPException(500, f"Gemini error: {data['error']['message']}")
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise HTTPException(500, f"Gemini response malformed: {e} — got: {str(data)[:300]}")
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

async def generate_image(prompt: str, variant: str) -> Optional[str]:
    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_IMAGE_MODEL}:predict?key={GEMINI_KEY}"
    )
    body = {
        "instances": [{"prompt": (
            f"Photorealistic, cinematic scene, no text overlays, no charts, no graphs, "
            f"no faces as primary subject. {prompt}"
        )}],
        "parameters": {"sampleCount": 1},
    }
    try:
        async with httpx.AsyncClient(timeout=90) as client:
            r = await client.post(url, json=body)
        data = r.json()
        if "error" in data:
            print(f"Imagen error variant {variant}: {data['error']}")
            return None
        b64 = data["predictions"][0]["bytesBase64Encoded"]
        return f"data:image/png;base64,{b64}"
    except Exception as e:
        print(f"Imagen exception variant {variant}: {e}")
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

async def run_a1(url: str, text: str) -> dict:
    return await call_gemini(A1_SYS, f"""
Extract only what a visual can encode. Output JSON exactly:
{{"headline":"string","core_tension":"one sentence — human meaning of the data",
"key_facts":["up to 8 strings, each under 15 words"],
"numbers":[{{"label":"string","value":0,"unit":"string"}}],
"domain":"string","tone":"alarming|hopeful|neutral|complex|urgent",
"sensitive":false,"source_url":"{url}"}}
Source:\n{text}""", 800)

# ── A2: Concept + Prompt Engineer ─────────────────────────────────────────────

async def run_a2(a1: dict) -> dict:
    return await call_gemini(A2_SYS, f"""
Read core_tension first — it is your brief. Invent a visual metaphor. Map data to visual properties.
Output JSON exactly:
{{"concept_title":"string","metaphor":"one sentence",
"data_mappings":[{{"datum":"string","visual_property":"string"}}],
"format":"image","generation_prompt":"string — vivid cinematic scene, no text/charts/graphs/faces as primary",
"negative_prompt":"string","model_params":{{"aspect_ratio":"16:9","style_tags":["cinematic","photorealistic"]}},
"accuracy_constraints":["string"]}}
Input: {json.dumps(a1)}""", 1000)

# ── Gen ×3 ────────────────────────────────────────────────────────────────────

async def run_generation(prompt: str) -> list:
    results = await asyncio.gather(
        generate_image(prompt, "A"),
        generate_image(prompt, "B"),
        generate_image(prompt, "C"),
        return_exceptions=True,
    )
    return [r if isinstance(r, str) else None for r in results]

# ── Critic Loop (PaperBanana Visualizer-Critic, T=3) ─────────────────────────

async def run_critic_round(images: list, a1: dict, a2: dict) -> dict:
    image_uri = next((i for i in images if i), None)
    if not image_uri:
        return {
            "issues_found": [],
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

    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_TEXT_MODEL}:generateContent?key={GEMINI_KEY}"
    )
    try:
        async with httpx.AsyncClient(timeout=60) as client:
            r = await client.post(endpoint, json={
                "system_instruction": {"parts": [{"text": CRITIC_SYS}]},
                "contents": [{
                    "role": "user",
                    "parts": [
                        {"inlineData": {"mimeType": mime_type, "data": b64_data}},
                        {"text": critic_prompt},
                    ],
                }],
                "generationConfig": {"maxOutputTokens": 800},
            })
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

async def run_critic_loop(a1: dict, a2: dict, images: list, log_fn) -> tuple:
    all_issues     = []
    current_images = images
    current_prompt = a2["generation_prompt"]

    for round_num in range(1, 4):
        critic = await run_critic_round(current_images, a1, a2)
        issues = critic.get("issues_found", [])
        all_issues.extend(issues)

        if not issues or critic.get("core_tension_readable"):
            log_fn(f"Critic round {round_num}: clean — stopping early")
            return current_images, round_num, all_issues

        log_fn(f"Critic round {round_num}: found {len(issues)} issues, refining…")
        current_prompt = critic.get("refined_prompt") or current_prompt
        current_images = await run_generation(current_prompt)

    log_fn(f"Critic loop complete after {round_num} rounds")
    return current_images, round_num, all_issues

# ── A3: Final Scorer + Router ─────────────────────────────────────────────────

async def run_a3(a1: dict, a2: dict, images: list) -> dict:
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
A2: {json.dumps({{k:a2[k] for k in ["concept_title","metaphor","data_mappings","accuracy_constraints"] if k in a2}})}
Generation: {"; ".join(gen_status)}""", 1000)

# ── A0: Startup Validation (runs once, never in /run) ────────────────────────

_URL_PROMPTS = [
    (
        'Give me 5 real, currently accessible news article URLs from major outlets '
        'like BBC, Reuters, Guardian, AP, TechCrunch, Bloomberg. Each URL must be a '
        'specific article page, not a homepage. Return only a JSON array of 5 URL strings.'
    ),
    (
        'List 5 direct URLs to specific published news articles (not homepages) from '
        'BBC, CNN, Reuters, AP News, or NPR. Return only a raw JSON array of strings, '
        'example: ["https://bbc.com/news/...", "https://reuters.com/..."]'
    ),
    (
        'Provide 5 URLs to specific live news articles from any major English-language '
        'outlet. Return a JSON array of strings only, no explanation.'
    ),
]

async def a0_fetch_test_urls(attempt: int = 0) -> list:
    prompt = _URL_PROMPTS[min(attempt, len(_URL_PROMPTS) - 1)]
    endpoint = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_TEXT_MODEL}:generateContent?key={GEMINI_KEY}"
    )
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(endpoint, json={
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {"maxOutputTokens": 500},
        })
    data = r.json()
    if "error" in data:
        raise ValueError(f"Gemini error: {data['error']['message']}")
    try:
        text = data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise ValueError(f"Gemini response malformed: {e}")
    try:
        urls = parse_json(text)
    except json.JSONDecodeError as e:
        raise ValueError(f"Gemini returned invalid JSON for URLs: {e}")
    if (
        isinstance(urls, list)
        and len(urls) >= 5
        and all(isinstance(u, str) and u.startswith("http") for u in urls)
    ):
        return urls[:5]
    raise ValueError(f"Invalid URL list returned: {urls}")

async def a0_test_one_url(url: str) -> dict:
    result = {"url": url, "passed": False, "reason": "", "failure_point": ""}
    try:
        article_text = await fetch_article(url)

        try:
            a1 = await run_a1(url, article_text)
            missing = [k for k in ("headline", "core_tension", "key_facts", "numbers") if k not in a1]
            if missing:
                result["reason"] = f"A1 missing fields: {missing}"
                result["failure_point"] = "a1_failure"
                return result
        except Exception as e:
            result["reason"] = f"A1 exception: {e}"
            result["failure_point"] = "a1_failure"
            return result

        try:
            a2 = await run_a2(a1)
            missing = [k for k in ("concept_title", "metaphor", "generation_prompt") if k not in a2]
            if missing:
                result["reason"] = f"A2 missing fields: {missing}"
                result["failure_point"] = "a2_failure"
                return result
        except Exception as e:
            result["reason"] = f"A2 exception: {e}"
            result["failure_point"] = "a2_failure"
            return result

        try:
            images = await run_generation(a2["generation_prompt"])
            if sum(1 for i in images if i) == 0:
                result["reason"] = "Image generation: 0/3 images returned"
                result["failure_point"] = "image_failure"
                return result
        except Exception as e:
            result["reason"] = f"Image generation exception: {e}"
            result["failure_point"] = "image_failure"
            return result

        try:
            images, _, _ = await run_critic_loop(
                a1, a2, images, lambda msg: print(f"[A0 test] {msg}")
            )
        except Exception as e:
            print(f"[A0] Critic loop non-fatal exception during test: {e}")

        try:
            a3 = await run_a3(a1, a2, images)
            missing = [k for k in ("scores", "winner_variant", "verdict", "confidence") if k not in a3]
            if missing:
                result["reason"] = f"A3 missing fields: {missing}"
                result["failure_point"] = "a3_failure"
                return result
            if float(a3.get("confidence", 0)) < 5.0:
                result["reason"] = f"A3 confidence too low: {a3.get('confidence')}"
                result["failure_point"] = "a3_failure"
                return result
        except Exception as e:
            result["reason"] = f"A3 exception: {e}"
            result["failure_point"] = "a3_failure"
            return result

        result["passed"] = True
        result["reason"] = "all checks passed"
        return result

    except Exception as e:
        result["reason"] = f"Unexpected error: {e}"
        result["failure_point"] = "unknown"
        return result

async def a0_attempt_fix(failure_point: str, failed_results: list) -> bool:
    try:
        main_py = pathlib.Path(__file__).read_text()
        failures_summary = "\n".join(
            f"- {r['url']}: {r['reason']}" for r in failed_results
        )
        fix_prompt = (
            f"You are a Python debugging agent. A FastAPI pipeline has a recurring failure.\n\n"
            f"Failure point: {failure_point}\n"
            f"Failing tests:\n{failures_summary}\n\n"
            f"Here is the current main.py:\n{main_py}\n\n"
            f"Identify the root cause of the {failure_point} failures and return a fixed version "
            f"of main.py. Return ONLY the complete fixed Python file, no explanation, no markdown fences."
        )
        endpoint = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{GEMINI_TEXT_MODEL}:generateContent?key={GEMINI_KEY}"
        )
        async with httpx.AsyncClient(timeout=120) as client:
            r = await client.post(endpoint, json={
                "contents": [{"role": "user", "parts": [{"text": fix_prompt}]}],
                "generationConfig": {"maxOutputTokens": 8192},
            })
        data = r.json()
        fixed = data["candidates"][0]["content"]["parts"][0]["text"]
        fixed = re.sub(r"```python\s*", "", fixed)
        fixed = re.sub(r"```\s*", "", fixed).strip()
        if "async def call_gemini" in fixed and "async def run_a1" in fixed:
            pathlib.Path(__file__).write_text(fixed)
            print(f"[A0] Auto-fix applied for: {failure_point}")
            return True
        print("[A0] Auto-fix produced invalid code, skipping")
        return False
    except Exception as e:
        print(f"[A0] Auto-fix failed: {e}")
        return False

async def run_a0_validation():
    global DEPLOYMENT_VALIDATED, _validation_summary

    print("[A0] Starting deployment validation…")
    all_results: list = []

    for cycle in range(3):
        print(f"[A0] Validation cycle {cycle + 1}/3")

        urls = None
        for attempt in range(3):
            try:
                urls = await a0_fetch_test_urls(attempt)
                print(f"[A0] Got test URLs: {urls}")
                break
            except Exception as e:
                print(f"[A0] URL fetch attempt {attempt + 1} failed: {e}")

        if not urls:
            print("[A0] Could not obtain test URLs, aborting validation")
            break

        cycle_results = []
        for url in urls:
            print(f"[A0] Testing: {url}")
            r = await a0_test_one_url(url)
            cycle_results.append(r)
            print(f"[A0] {'PASS' if r['passed'] else 'FAIL'}: {r['reason']}")

        all_results.extend(cycle_results)

        passed = sum(1 for r in cycle_results if r["passed"])
        total  = len(cycle_results)
        rate   = passed / total
        print(f"[A0] Cycle {cycle + 1} result: {passed}/{total} ({rate:.0%})")

        if rate >= 0.9:
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            _validation_summary = {
                "status": "passed", "cycles": cycle + 1,
                "passed": passed, "total": total,
                "success_rate": rate, "timestamp": ts,
                "test_results": cycle_results,
            }
            lines = "\n".join(
                f"{'PASS' if r['passed'] else 'FAIL'}: {r['url']} — {r['reason']}"
                for r in cycle_results
            )
            pathlib.Path("READY.txt").write_text(
                f"DEPLOYMENT VALIDATED\nCycles: {cycle + 1}\n"
                f"Success rate: {passed}/{total} ({rate:.0%})\nTimestamp: {ts}\n\n"
                f"Test results:\n{lines}\n"
            )
            print("[A0] Validation PASSED. READY.txt written.")
            DEPLOYMENT_VALIDATED = True
            return

        if cycle < 2:
            failures = [r for r in cycle_results if not r["passed"]]
            most_common = Counter(r["failure_point"] for r in failures).most_common(1)[0][0]
            print(f"[A0] Most common failure: {most_common}. Attempting auto-fix…")
            await a0_attempt_fix(most_common, failures)

    total_passed = sum(1 for r in all_results if r["passed"])
    total_all    = len(all_results)
    rate_all     = total_passed / total_all if total_all else 0
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    _validation_summary = {
        "status": "failed", "cycles": 3,
        "passed": total_passed, "total": total_all,
        "success_rate": rate_all, "timestamp": ts,
        "test_results": all_results,
    }
    lines = "\n".join(
        f"{'PASS' if r['passed'] else 'FAIL'}: {r['url']} — {r['reason']}"
        for r in all_results
    )
    pathlib.Path("FAILED.txt").write_text(
        f"DEPLOYMENT VALIDATION FAILED\nCycles attempted: 3\n"
        f"Overall success rate: {total_passed}/{total_all} ({rate_all:.0%})\n"
        f"Timestamp: {ts}\n\nAll test results:\n{lines}\n"
    )
    print("[A0] Validation FAILED after 3 cycles. FAILED.txt written. Opening production anyway.")
    DEPLOYMENT_VALIDATED = True

# ── app lifespan ──────────────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    asyncio.create_task(run_a0_validation())
    yield

app = FastAPI(title="Visual Storytelling Pipeline", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── endpoints ─────────────────────────────────────────────────────────────────

class RunRequest(BaseModel):
    url: str

@app.post("/run")
async def run_pipeline(req: RunRequest):
    if not DEPLOYMENT_VALIDATED:
        raise HTTPException(503, "Validating pipeline, try again in 60 seconds")

    url = req.url.strip()
    if not url.startswith("http"):
        raise HTTPException(400, "Invalid URL")

    t0  = time.time()
    log = []
    def step(msg): log.append(msg); print(msg)

    # A1
    step("A1: fetching article…")
    article_text = await fetch_article(url)
    step("A1: running ingestion agent…")
    a1 = await run_a1(url, article_text)
    step(f"A1 done — headline: {a1.get('headline', '?')[:60]}")

    # A2
    step("A2: concepting and prompt engineering…")
    a2 = await run_a2(a1)
    step(f"A2 done — concept: {a2.get('concept_title', '?')}")

    # Gen ×3
    step("GEN: generating 3 variants in parallel…")
    images = await run_generation(a2["generation_prompt"])
    ok = sum(1 for i in images if i)
    step(f"GEN done — {ok}/3 images OK")
    if ok == 0:
        raise HTTPException(500, "Image generation failed: all 3 variants returned None")

    # Critic loop
    step("CRITIC: starting visualizer-critic loop (max 3 rounds)…")
    images, critic_rounds, critic_issues = await run_critic_loop(a1, a2, images, step)
    step(f"CRITIC done — {critic_rounds} round(s), {len(critic_issues)} total issues")

    # A3
    step("A3: scoring and routing…")
    a3 = await run_a3(a1, a2, images)
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

@app.get("/status")
async def status():
    ready_path  = pathlib.Path("READY.txt")
    failed_path = pathlib.Path("FAILED.txt")
    summary_text = ""
    if ready_path.exists():
        summary_text = ready_path.read_text()
    elif failed_path.exists():
        summary_text = failed_path.read_text()
    return {
        "deployment_validated": DEPLOYMENT_VALIDATED,
        "validation_status": _validation_summary.get("status", "pending"),
        "success_rate": _validation_summary.get("success_rate"),
        "cycles": _validation_summary.get("cycles"),
        "test_summary": summary_text,
        "pipeline_health": {
            "text_model": GEMINI_TEXT_MODEL,
            "image_model": GEMINI_IMAGE_MODEL,
        },
    }

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "text_model": GEMINI_TEXT_MODEL,
        "image_model": GEMINI_IMAGE_MODEL,
    }

# Serve frontend
app.mount("/static", StaticFiles(directory="static", html=True), name="static")

@app.get("/")
async def root():
    return FileResponse("static/index.html")
