# Kaymio Affiliate App

A Flask-powered control room for Kaymio's affiliate drops. Upload a product hero, enrich the copy, invoke Gemini (Gemeni) for creative edits, and push final artwork straight to Pinterest.

## Getting started

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` file with your secrets:

```
FLASK_SECRET_KEY=replace-me
GOOGLE_API_KEY=xxxxxxxxxxxx
OPENAI_API_KEY=xxxxxxxxxxxx
PINTEREST_ACCESS_TOKEN=xxxxxxxxxxxx
PINTEREST_BOARD_ID=1234567890
```

Launch the dev server:

```bash
flask --app app run --debug
```

## Workflow overview

- **Product Data Entry** – pick a marketplace (Shein, Amazon, AliExpress, Temu, …) and supply the SKU/link.
- **Manual product assets** – upload a hero shot, type the title/description, and add your affiliate link. This data flows into the AI prompts.
- **Pinterest prompt booster** – add extra angles or promo text and click **Generating for Pinterest**.
- Behind the scenes the POST route in `app.py` orchestrates:
  - `openai_helper.extract_concept_from_text` / `generate_tags_for_product_for_pintrest` for polished copy + SEO tags.
  - `gemeni_api_helper.edit_image` to rework the uploaded asset via Gemini.
  - `pinterest_helper.create_pinterest_pin` to publish the final creatives to the configured Pinterest board.

Outputs (title, description, tags, and Pin URL) are surfaced back on the dashboard once everything succeeds.
