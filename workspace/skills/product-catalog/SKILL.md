---
name: product_catalog
description: Display furniture products from ./data/products.json ONLY. Always use send_product.py to send photo + details as ONE WhatsApp message. Never use send_image.py directly. Never invent products.
---

# Product Catalog Skill

## CRITICAL RULES

1. **Only show products from `./data/products.json`** — never invent products from training data.
2. **Always use `send_product.py`** to send each product — it delivers the photo AND details together as one WhatsApp message. Never use `send_image.py` directly.
3. Never mention CRM, software, or anything unrelated to furniture.
4. Send up to 3 products per `send_product.py` call.

## Data Source

Read `./data/products.json` fresh on every request. Fields per product:
- `id`, `name`, `category`, `price.amount`, `price.currency`, `image` (absolute path), `link`, `availability`

## How to Display a Product — use send_product.py (image + details as ONE message)

To show products, run **`send_product.py`** with the product **id(s)** and the customer's phone number. The script looks up each product, downloads its image, builds the caption (name, price, dimensions, availability, link), and sends the **photo + details as one WhatsApp message**. You do NOT build the caption or handle the image yourself.

Run this with the exec/shell tool (phone number comes from the channel context):

```
python3 /home/it-admin/wa-lead-gen/workspace/send_product.py \
  --to "<customer_phone_e164>" \
  --ids "<id1,id2,id3>"
```

Example — show three beds:
```
python3 /home/it-admin/wa-lead-gen/workspace/send_product.py --to "+923362615506" --ids "6203,6201,6205"
```

**Rules:**
- Pass the product `id` values (from `products.json`) in `--ids`, comma-separated. Max 3 per call.
- The script prints `[OK] <id> sent` per product. If it prints `[FAIL]`, tell the customer that product's details in plain text as a fallback.
- After sending, add a short follow-up line in chat (e.g. "Would you like to see more options, or shall I note your details for our team?").
- Do not use emojis anywhere.
- Do NOT use `send_image.py` or `openclaw message send --media` directly — they do not deliver WhatsApp images. Always use `send_product.py`.

## Listing Multiple Products

When showing multiple products (e.g. "show me all beds"):
- Pass up to 3 ids at once: `send_product.py --to <phone> --ids "6203,6201,6205"`.
- After 3, ask: "Would you like to see more options?"

## Category Listing (no image needed)

We carry **934 products, all in stock**. When a user first asks "what do you have" or "what categories", present these customer-facing categories:

```
Welcome to renovate.pk. Here is our range:

- Bedroom — beds, wardrobes, dressing tables, side tables (PKR 12,000–726,000)
- Lounge & Sofas — sofa sets, single seaters, L-shaped, settees, centre tables (PKR 12,000–275,000)
- TV Lounge & Media — media walls, TV consoles (PKR 14,000–160,000)
- Dining & Kitchen — dining tables, crockery units, tea trolleys (PKR 30,000–380,000)
- Office Furniture — desks, workstations, conference tables, reception counters, pods (PKR 14,000–750,000)
- Study Room — writing desks, study tables (PKR 24,000–48,000)
- Home Decor — mirrors, paintings, lamps, consoles, shoe cabinets (PKR 4,700–132,000)
- Wedding Packages — complete bedroom sets (PKR 394,000–700,000)

Which category would you like to explore? I will share options with photos.
```

### Matching a customer's interest to the catalog

The `category` field in `products.json` uses detailed, comma-separated labels (e.g. "Lounge, Seating, Sofa Set"). To find products for a customer-facing category, filter the catalog where the `category` field **contains** any of these keywords:

- **Bedroom** → `bed`, `wardrobe`, `dressing`, `drawer chest`, `cupboard`, `bedside`, `bed wall`
- **Lounge & Sofas** → `sofa`, `single seater`, `seating`, `settee`, `ottoman`, `center table`, `l-shaped`, `chair`
- **TV Lounge & Media** → `media wall`, `tv lounge`, `tv console`
- **Dining & Kitchen** → `dining`, `dinning`, `crockery`, `tea trolley`, `kitchen`
- **Office Furniture** → `office`, `workstation`, `reception`, `conference`, `executive desk`, `pods`
- **Study Room** → `study`, `writing desk`
- **Home Decor** → `mirror`, `ornament`, `painting`, `lamp`, `light`, `curtain`, `entrance table`, `shoe cabinet`, `console`
- **Wedding Packages** → `wedding`

Match case-insensitively. If a customer asks for something specific (e.g. "bunk bed"), match that keyword directly against the `category` and `name` fields.

## Price / Budget Filter

If user asks "show me under PKR X":
- Filter `price.amount <= X`, sort cheapest first, show top 3 with images.

## Tone

Professional and concise. No emojis. Always end with an invitation to enquire.
