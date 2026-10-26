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

## Automation (scheduled tasks)

The **Automation** page (`/automation`) lists every background task. You can stop/start it, change its daily
time and options, run it now, or reset it to the `.env` defaults. UI edits are saved in the
`scheduled_task_settings` table and take effect within 20 seconds. No redeploy is needed.

| Task | What it does | `.env` defaults |
| --- | --- | --- |
| Instagram stories | Publishes random kaymio.com products as stories with a CTA and QR code | `INSTAGRAM_STORY_AUTOPUBLISH_*` |
| Instagram auto-post | Amazon trending product, then Telegram approval, then WooCommerce product and Instagram carousel | `INSTAGRAM_AUTO_POST_*`, `AMAZON_TRENDING_*` |

### Auto-post flow

1. Crawls Amazon's lists (`AMAZON_TRENDING_SOURCE_URLS`, tried in order) through the self-hosted **Crawl4AI**
   service (`CRAWL4AI_BASE_URL`, `CRAWL4AI_API_TOKEN`).
2. Picks the highest-ranked product that is **not already on kaymio.com** (ASINs from WooCommerce `external_url`/SKU),
   has never been suggested before, and meets the rating and review thresholds. Then crawls its product page for
   title, price, images, bullets and category. Canopy fills any gaps when `CANOPY_API_KEY` is set.
3. Generates the store title/description/tags and the Instagram caption (same CTA and hashtag format as manual posts).
4. Sends the top image and details to Telegram with **Approve** and **Cancel** buttons. Nothing is published before you approve.
5. On approval, it creates the WooCommerce external product and posts the images as an Instagram carousel
   (padded to 1080×1350). It also registers the post so "comment BUY" replies DM the affiliate link.
   If a step fails, you can retry it from the Automation page, and steps that already succeeded are skipped.

Telegram setup: create a bot with @BotFather, set `TELEGRAM_BOT_TOKEN`, send `/start` to the bot to see your chat id,
and set `TELEGRAM_CHAT_ID`. Button clicks are received by long polling (`TELEGRAM_UPDATES_MODE=polling`, the default)
or by webhook (`webhook` plus `TELEGRAM_WEBHOOK_SECRET` and `PUBLIC_APP_BASE_URL`).
