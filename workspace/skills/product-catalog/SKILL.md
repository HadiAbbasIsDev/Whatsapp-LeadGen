---
name: product_catalog
description: Display furniture products from ./data/products.json ONLY. For every product shown, you MUST first run send_image.py to send the product photo, then send the text card. Never invent products.
---

# Product Catalog Skill

## CRITICAL RULES

1. **Only show products from `./data/products.json`** — never invent products from training data.
2. **For every product displayed, ALWAYS send the image first using the shell command below.**
3. Never mention CRM, software, or anything unrelated to furniture.

## Data Source

Read `./data/products.json` fresh on every request. Fields per product:
- `id`, `name`, `category`, `price.amount`, `price.currency`, `image` (absolute path), `link`, `availability`

## How to Display a Product — Follow This Exact Order

### Step 1 — Send the product image via shell

Run this command using the exec/shell tool, replacing values with the actual product data and the customer's phone number from the channel context:

```
python3 /home/it-admin/wa-lead-gen/workspace/send_image.py \
  --image <product.image> \
  --to <customer_phone_e164>
```

Example:
```
python3 /home/it-admin/wa-lead-gen/workspace/send_image.py \
  --image /home/it-admin/wa-lead-gen/database/images/0101.jpg \
  --to +923012345678
```

**Run this for EVERY product you show. Do not skip it.**

### Step 2 — Send the text card as your reply

```
🛏️ *{name}*
📂 {category}

💰 *PKR {price.amount:,}*
✅ {availability}

🔗 {link}

Interested? Share your name & number and our team will get back to you! 😊
```

## Listing Multiple Products

When showing multiple products (e.g. "show me all beds"):
- For each product: run Step 1 (send_image.py), then send Step 2 text.
- Max 3 products per batch. After 3, ask: "Want to see more options?"

## Category Listing (no image needed)

When user first asks "what do you have" or "what categories":

```
Welcome to Hilius Solution! 🛋️ Here's what we carry:

🛏️ *Beds & Bedroom Sets*
🍽️ *Dining & Kitchen*
💼 *Office Furniture*
🇵🇰 *Local Pakistani Brands*

Which room are you furnishing? I'll show you the options with photos!
```

## Price / Budget Filter

If user asks "show me under PKR X":
- Filter `price.amount <= X`, sort cheapest first, show top 3 with images.

## Tone

Warm, conversational, WhatsApp-friendly. Always end with an invitation to enquire.
