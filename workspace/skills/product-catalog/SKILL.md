---
name: product_catalog
description: Display furniture products from ./data/products.json ONLY. Always use send_product.py to send photo + details as ONE WhatsApp message. Never use send_image.py directly. Never invent products.
---

# Product Catalog Skill

## CRITICAL RULES

1. **Only show products from `./data/products.json`** — never invent products from training data.
2. **Always use `send_product.py`** to send each product — it delivers the photo AND details together as one WhatsApp message. Never use `send_image.py` directly.
3. Never mention CRM, software, or anything unrelated to furniture.
4. Send up to **10** products per `send_product.py` call. If fewer than 10 match the customer's request, send every matching one.
5. **SOFA PRICING IS PER SEAT.** Whenever you quote a sofa's price — in a photo caption (handled automatically) OR in your own text — you MUST say "PKR X **per seat**", never just "PKR X". E.g. "This sofa is PKR 45,000 per seat." This applies to any sofa (Sofa Set, Sofa Sets, L-Shaped Sofa, sofa bed). Other items are priced per piece as normal.

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
- Pass the product `id` values (from `products.json`) in `--ids`, comma-separated. Up to 10 per call; send all matches if fewer than 10 exist.
- The script prints `[OK] <id> sent` per product. If it prints `[FAIL]`, tell the customer that product's details in plain text as a fallback.
- After sending, add a short follow-up line in chat (e.g. "Would you like to see more options, or shall I note your details for our team?").
- Do not use emojis anywhere.
- Do NOT use `send_image.py` or `openclaw message send --media` directly — they do not deliver WhatsApp images. Always use `send_product.py`.

## Finding the right products — use search_products.py (do NOT filter by hand)

To find which products match a customer's request, run **`search_products.py`**. It
filters by **category** (so "sofa" returns only sofas — never settees or chairs) and
price, cheapest first, and prints the matching ids:
```
python3 /home/it-admin/wa-lead-gen/workspace/search_products.py --query "sofa" --max-price 100000
```
It prints `IDS: id1,id2,...` (up to 10; all matches if fewer than 10 exist). Pass those
ids straight to send_product.py — do NOT eyeball products.json and pick by hand:
```
python3 /home/it-admin/wa-lead-gen/workspace/send_product.py --to <phone> --ids "<ids from search>"
```
If `IDS:` is empty, tell the customer nothing matched and offer alternatives. After
sending, ask: "Would you like to see more options?"

## Category Listing (no image needed)

We carry **934 products, all in stock**. When a user first asks "what do you have" or "what categories", present these customer-facing categories:

```
Welcome to Decor Moments. Here is our range:

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
