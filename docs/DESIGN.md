# DataForPeople Design

## Purpose

DataForPeople is a local tool for turning articles and reports into visual story concepts. It is built for data journalism workflows where the output should communicate the human meaning behind a source, not reproduce a chart.

The central design goal is to keep factual grounding and visual meaning connected through the whole pipeline. The app should not simply ask an image model to "make an illustration." It first asks what the story means, then asks what can be shown, then checks whether the generated image still matches that meaning.

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

The most important output is `core_tension`. This is not a summary. It is the underlying human meaning of the article. For example, an article about shrinking Arctic ice is not only "ice coverage declined"; the visual tension might be "a stable part of the planet is becoming unstable."

### A2 Concept

Turns A1 output into a visual metaphor and image-generation prompt.

A2 owns both the concept and prompt. Keeping these together avoids a second handoff where one agent invents a metaphor and another agent reinterprets it as a prompt. The output includes:

- concept title
- metaphor
- data-to-visual mappings
- generation prompt
- negative prompt
- accuracy constraints

### Image Generation

Generates three image variants with FLUX.1-dev through the Hugging Face Inference API.

The system generates three variants in parallel because image generation has high variance. A single image can fail compositionally even when the prompt is reasonable. Multiple variants give the critic and quality gate a better candidate set.

### Critic Loop

Reviews generated images against the source facts and visual metaphor. If needed, it refines the prompt and regenerates variants.

The critic loop is based on the Visualizer-Critic idea from *PaperBanana: Automating Academic Illustration for AI Scientists*. PaperBanana uses a critic model to inspect generated visuals, write feedback, and feed a refined description back into generation for a small number of rounds.

DataForPeople applies that pattern to article-based visual storytelling:

1. Select an available image from the current variants.
2. Send the image, `core_tension`, key facts, metaphor, and data mappings to Gemini vision.
3. Ask the critic whether the data mappings are visible and whether the core tension is readable.
4. If the critic finds problems, use its refined prompt to generate three new variants.
5. Stop early if the image is clean, otherwise run up to three rounds.

The implementation also keeps a best-of-rounds memory. Each critic round is scored with a simple rule:

```text
score = -number_of_issues
      + 5 if core_tension_readable
      + 3 if data_mappings_visible
```

The final candidate images come from the best-scoring round, not automatically from the last round. This matters because a refined prompt can sometimes overcorrect and produce a worse image.

### A3 Quality Gate

Scores the result on accuracy, legibility, distortion, and tone match. It routes outputs to publish or human review.

A3 does not regenerate images. Its job is to make the final routing decision:

- `confidence >= 7.0` -> publish
- `confidence < 7.0` -> human queue
- any hard flag -> human queue

Hard flags include:

- out of context
- too abstract
- sensitive topic
- data contradiction

The separation between Critic and A3 is intentional. The critic improves the prompt and image set. A3 only scores and routes.

## Why The Pipeline Is Split

The project uses separate stages because each stage has a different failure mode:

- A1 can hallucinate or miss the central tension.
- A2 can choose a metaphor that is visually interesting but factually weak.
- Image generation can ignore key details or overdramatize the source.
- The critic can improve a weak prompt but may overcorrect.
- A3 can stop low-quality images from being treated as publishable.

Splitting these steps makes the intermediate reasoning visible in the UI and gives a human reviewer enough context to understand why an image was accepted or queued.

## PaperBanana Reference

PaperBanana is used as the reference pattern for iterative visual refinement:

- Zhu et al. (2026), *PaperBanana: Automating Academic Illustration for AI Scientists*, arXiv:2601.23265.

DataForPeople does not replicate the full PaperBanana system. It borrows the Visualizer-Critic loop structure and adapts it for article-to-image visual storytelling.

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
