# DataForPeople Design

## Purpose

DataForPeople is a local tool for turning articles and reports into visual story concepts. It is built for data journalism workflows where the output should communicate the human meaning behind a source, not reproduce a chart.

## Pipeline

```text
URL input
  -> article fetch and text extraction
  -> A1 ingestion
  -> A2 concept and image prompt
  -> three image variants
  -> critic loop
  -> A3 quality gate
  -> publish or human queue
```

## Components

### Frontend

The frontend is a single HTML file at `static/index.html`. It handles:

- API key entry
- URL submission
- pipeline progress display
- generated image preview
- published feed
- human review queue
- lightweight analytics

### Backend

`main.py` provides the FastAPI API:

- `GET /`
- `POST /run`
- `GET /health`

The backend fetches article text, calls Gemini for structured reasoning, calls Hugging Face for image generation, and returns the complete pipeline result to the browser.

## Agents

### A1 Ingestion

Extracts the headline, key facts, numbers, tone, domain, and core tension from source text.

### A2 Concept

Turns A1 output into a visual metaphor and image-generation prompt.

### Image Generation

Generates three image variants with FLUX.1-dev through the Hugging Face Inference API.

### Critic Loop

Reviews generated images against the source facts and visual metaphor. If needed, it refines the prompt and regenerates variants.

### A3 Quality Gate

Scores the result on accuracy, legibility, distortion, and tone match. It routes outputs to publish or human review.

## Security Notes

- API keys are not stored server-side.
- Browser-entered keys stay in `localStorage`.
- `/run` accepts public `http` and `https` URLs only.
- Localhost, private IP ranges, link-local addresses, multicast, and reserved IPs are blocked.
- `.env` files and local tool settings are ignored by git.

## Limits

- The app depends on Gemini and Hugging Face account access.
- Generated images should be reviewed before use in sensitive contexts.
- Source extraction is basic HTML text extraction, not a full article parser.
- GitHub Pages can show the project, but cannot run the backend pipeline.
