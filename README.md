# DataForPeople — Visual Storytelling Pipeline
## Design Document

---

## What This System Does

DataForPeople takes any news article or research report URL as input and automatically produces a visually compelling image that communicates the core meaning of the data — using visual metaphors, not charts or graphs.

The core design principle: **every visual must be readable in under 5 seconds without a caption.** The metaphor emerges from the data itself, not applied to it.

---

## Architecture Overview

```
URL Input
    ↓
A1 — Ingestion Agent
    ↓
A2 — Concept + Prompt Engineer
    ↓
GEN — Image Generation (×3 parallel variants)
    ↓
Critic Loop — Visualizer-Critic (up to 3 rounds)
    ↓
A3 — Final Quality Gate
    ↓
Publish or Human Queue
```

---

## Agents

### A0 — Pre-deployment Validator *(startup only)*

A0 runs **once** when the server starts, before accepting any production traffic. Its job is to validate the entire pipeline end-to-end before real users can reach it.

**How it works:**
1. Asks Gemini to find 5 real, currently accessible news article URLs
2. Tests the full pipeline (A1 → A2 → Gen → Critic → A3) with each URL
3. Checks every agent output for validity and correctness
4. If success rate ≥ 90%: writes `READY.txt`, opens production
5. If below 90%: identifies failures, attempts auto-fixes, retries up to 3 cycles
6. After completion (pass or fail): **permanently disabled** — never runs again during production requests

**Why it exists:** To catch API key issues, model availability problems, or broken pipeline logic before real users encounter errors.

---

### A1 — Ingestion Agent

**Purpose:** Convert a raw URL into a compact, structured JSON that the rest of the pipeline reasons about. A1 only compresses — it never invents.

**Input:** Article URL  
**Output:**
```json
{
  "headline": "string",
  "core_tension": "the human meaning of this data in one sentence",
  "key_facts": ["up to 8 facts, each under 15 words"],
  "numbers": [{"label": "string", "value": 0, "unit": "string"}],
  "domain": "economics | health | climate | politics | technology | ...",
  "tone": "alarming | hopeful | neutral | complex | urgent",
  "sensitive": false,
  "source_url": "string"
}
```

**The most important field — `core_tension`:**  
This is not a summary. It is the human meaning behind the data.

| Bad | Good |
|-----|------|
| "GDP growth slowed to 0.3% in Q3" | "An economy that built its identity on making things is quietly running out of things to make" |
| "Arctic ice coverage reached a new minimum" | "The last stable thing on Earth is becoming unstable" |

---

### A2 — Concept + Prompt Engineer

**Purpose:** Read A1's structured data, invent a visual metaphor that makes the data's meaning legible in under 5 seconds, then write the complete image generation prompt.

**Why concept and prompt are merged (not two agents):**  
Separating concept from prompt engineering requires a handoff where the concept is re-interpreted by a second agent. This double-interpretation degrades fidelity. A2 holds both the creative intent and the technical knowledge of model syntax simultaneously.

**Input:** A1 JSON  
**Output:**
```json
{
  "concept_title": "string",
  "metaphor": "one sentence describing the visual idea",
  "data_mappings": [
    {"datum": "45% unemployment", "visual_property": "nearly half the chairs in a vast empty hall are overturned"}
  ],
  "format": "image",
  "generation_prompt": "complete, vivid, cinematic scene description",
  "negative_prompt": "no charts, no graphs, no text overlays, no faces as primary subject",
  "model_params": {"aspect_ratio": "16:9", "style_tags": ["cinematic", "photorealistic"]},
  "accuracy_constraints": ["list of things that must not be distorted"]
}
```

**Hard constraints:**
- No charts, graphs, or data visualization elements
- No human faces as the primary subject
- The metaphor must be readable without a caption
- If `sensitive: true` in A1, use a grounded, non-dramatic concept

---

### GEN — Image Generation

**Purpose:** Generate 3 image variants from A2's prompt in parallel using different seeds for natural variance.

**Why 3 variants:**  
Sequential retry (generate → fail → generate) takes multiple full cycles. Generating 3 variants at once lets A3 pick the best one in a single round — significant throughput advantage at scale.

**Model:** Replicate Imagen 4 (`google/imagen-4`)  
**Format:** 16:9 aspect ratio, photorealistic

---

### Critic Loop *(inspired by PaperBanana)*

**Purpose:** Iteratively refine the generated image using a Visualizer-Critic loop before final scoring.

**Inspired by:** [PaperBanana: Automating Academic Illustration for AI Scientists](https://arxiv.org/abs/2601.23265) (Zhu et al., 2026, Google Cloud AI Research & Peking University). PaperBanana introduced a Visualizer-Critic loop where a Critic agent inspects each generated image and provides a refined description back to the Visualizer — repeating for T=3 rounds. We adopt this exact architecture for visual refinement.

**How it works:**
1. Pick the best available image from the 3 GEN variants
2. Call Gemini vision with: the image + A1 core_tension + A2 data_mappings
3. Critic checks:
   - Are the data mappings visually obvious?
   - Is the core_tension readable in 5 seconds?
   - What specific elements are wrong or missing?
4. If issues found: generate a `refined_prompt` and regenerate 3 new variants
5. Loop maximum **T=3 rounds** (same as PaperBanana), stop early if no issues

**Why Critic is separate from A3:**  
The Critic loop handles *refinement* — it actively improves the prompt and regenerates. A3 is purely a *quality gate* — it makes a final binary decision. Merging them would conflate two different cognitive tasks.

---

### A3 — Final Quality Gate

**Purpose:** Score the final image from the Critic loop and make a routing decision.

**Input:** Final best image + A1 data + A2 concept  
**Scoring (each 0–10):**
- `accuracy` ×0.35 — does the image match the data facts?
- `legibility` ×0.30 — can someone understand the message in 5 seconds?
- `distortion` ×0.25 — is any element misleading about scale or severity? (10 = no distortion)
- `tone_match` ×0.10 — does the visual mood match the data's tone?

**Routing:**
- `confidence ≥ 7.0` → **publish** (auto, no human needed)
- `confidence < 7.0` → **human_queue**
- Any hard flag → **human_queue** regardless of score

**Hard flags:**
- `out_of_context` — visual emotional register contradicts the data
- `too_abstract` — cannot describe the visual's meaning in one sentence
- `sensitive_topic` — sensitive data + potentially exploitative visual
- `data_contradiction` — a specific mapping is visually inverted

**A3 does NOT retry.** The Critic loop already handled all refinement. A3 is purely a binary decision maker.

---

## Why This Agent Count (4+1)

The optimal number was determined by balancing precision gain against token cost:

| Agents | Precision Gain | Notes |
|--------|---------------|-------|
| 1 | baseline | |
| 1→4 | ~60% | Major gain |
| 4→6 | ~6% | Minimal gain, double token cost |

The one valid addition beyond 4: the **Critic loop** between GEN and A3, adopted from PaperBanana. This is not a separate agent but an iterative refinement step that addresses the core weakness of one-shot image generation.

**What was deliberately removed:**
- A3 retry loop → replaced by Critic loop (same job, better architecture)
- `retry_note` passing between A3 and A2 → redundant with Critic loop
- A0 from production `/run` flow → validation is a deployment concern, not a per-request concern

---

## Technology Stack

| Component | Technology |
|-----------|-----------|
| Backend | Python, FastAPI |
| Text agents (A1, A2, A3, A0, Critic) | Gemini 2.5 Flash |
| Image generation | Replicate Imagen 4 (`google/imagen-4`) |
| Deployment | Railway (auto-deploy from GitHub) |
| Rate limiting | Global async rate limiter — 1 request per 12 seconds (Gemini free tier: 5 RPM) |

---

## Rate Limiting

Gemini free tier allows **5 requests per minute**. The pipeline makes multiple sequential Gemini calls per run (A1 → A2 → Critic × up to 3 → A3). Without rate limiting, consecutive calls trigger quota errors.

**Solution:** Global async rate limiter enforcing minimum 12 seconds between any two Gemini calls (60s ÷ 5 = 12s). Automatic retry with 65-second wait on quota errors.

---

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/` | GET | Frontend UI |
| `/run` | POST | Run full pipeline on a URL |
| `/status` | GET | A0 validation result and pipeline health |
| `/health` | GET | Model names and server status |

### `/run` Response
```json
{
  "url": "string",
  "elapsed_s": 0.0,
  "a1": { "headline": "...", "core_tension": "...", "..." },
  "a2": { "concept_title": "...", "metaphor": "...", "..." },
  "critic_rounds": 2,
  "critic_issues": ["issue found in round 1", "issue found in round 2"],
  "a3": { "scores": {}, "confidence": 8.2, "verdict": "publish" },
  "images": ["data:image/webp;base64,...", "...", "..."],
  "winner_image": "data:image/webp;base64,...",
  "log": ["A1: fetching...", "A2: concepting...", "Critic round 1...", "A3: scoring..."]
}
```

---

## Human Queue

Items land in the human queue when:
- A3 confidence < 7.0
- Any hard flag is present
- A0 confidence < 70% (during validation)

Human reviewers see: the image, concept title, flag reason, original headline, and scores. **Two actions only: Approve + Publish / Discard.** No re-prompting from the queue.

---

## Quality Targets

| Metric | Target |
|--------|--------|
| Auto-publish rate | > 72% |
| Human queue rate | < 10% |
| Mean A3 confidence | > 7.5 |
| Critic loop rounds needed | < 2 average |

---

## References

- Zhu, D., Meng, R., Song, Y., Wei, X., Li, S., Pfister, T., & Yoon, J. (2026). *PaperBanana: Automating Academic Illustration for AI Scientists*. arXiv:2601.23265. Google Cloud AI Research & Peking University.
  - Adopted: Visualizer-Critic iterative refinement loop (T=3 rounds)
  - Adopted: Principle of separating content planning from visual rendering
  - Adopted: Self-critique mechanism for visual accuracy verification
