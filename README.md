# DataForPeople

DataForPeople turns a public article or report URL into a visual story concept and generated image. The app extracts the core tension from the source text, creates a visual metaphor, generates three image variants, runs a PaperBanana-inspired critic loop, and routes the result to publish or human review.

The app is designed to run locally with your own API keys.

Design document: [docs/DESIGN.md](docs/DESIGN.md)

## Idea

The project is built around a simple problem: data-heavy articles are often reduced to charts, but many stories need a visual metaphor that makes the human meaning visible quickly. DataForPeople separates that work into small steps:

- extract only the facts that can be shown visually
- identify the `core_tension`, meaning the human conflict behind the data
- turn that tension into a visual metaphor
- generate several image variants
- critique the generated image before final scoring

The critic loop is inspired by the Visualizer-Critic pattern from *PaperBanana: Automating Academic Illustration for AI Scientists*. In that paper, a generated visual is inspected by a critic model, the critic writes concrete feedback, and the visual description is revised before another generation round. DataForPeople uses the same pattern for data-journalism visuals: the critic checks whether the image shows the source facts and whether the `core_tension` is readable without a caption.

## Workflow

```text
URL
  -> A1 ingestion
  -> A2 concept and prompt
  -> 3 image variants
  -> critic loop
  -> A3 quality gate
  -> publish or human queue
```

## Models

- Text and vision reasoning: Gemini 2.5 Flash
- Image generation: Hugging Face Inference API, FLUX.1-dev through the `fal-ai` provider

## Critic Loop

The first generated image is not assumed to be correct. The critic checks whether the image actually shows the source facts and whether the core tension is readable without a caption. If the image misses the point, the critic writes a refined prompt and the system generates a new set of variants.

The implementation keeps the best-scoring round instead of blindly using the last regenerated image. This avoids a common failure mode where a later prompt revision makes the image worse than an earlier round.

## Run Locally

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload --port 8000
```

Open:

```text
http://localhost:8000
```

The browser asks for:

- Gemini API key
- Hugging Face token

Keys are stored in browser `localStorage` and sent to the local FastAPI server with each `/run` request. They are not committed to the repository.

For scripted use, the server also reads:

```bash
export GEMINI_API_KEY="..."
export HF_TOKEN="..."
```

## API

| Endpoint | Method | Description |
|---|---|---|
| `/` | GET | Web UI |
| `/run` | POST | Runs the full pipeline for a public URL |
| `/health` | GET | Server status and model names |

Example `/run` body:

```json
{
  "url": "https://example.com/article",
  "gemini_key": "...",
  "hf_key": "..."
}
```

## Notes

- Local/private network URLs are blocked.
- Raw API keys should stay in environment variables or browser local storage.
- Image generation may use paid Hugging Face quota depending on account settings.

## Reference

- Zhu et al. (2026), *PaperBanana: Automating Academic Illustration for AI Scientists*, arXiv:2601.23265.
