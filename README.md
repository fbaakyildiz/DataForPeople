# DataForPeople

DataForPeople turns a public article or report URL into a visual story concept and generated image. The app extracts the core tension from the source text, creates a visual metaphor, generates three image variants, runs a critic loop, and routes the result to publish or human review.

The app is designed to run locally with your own API keys.

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
