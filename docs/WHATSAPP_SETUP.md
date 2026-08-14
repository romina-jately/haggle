# WhatsApp setup

There is no "Shopping API". A storefront is a Meta catalog plus a phone
number that knows how to talk about it. Six steps, once.

## 1. Meta app and WhatsApp product

Create an app at developers.facebook.com, add the WhatsApp product, and note
the test phone number ID. `WA_PHONE_NUMBER_ID`.

## 2. Business verification

Required before you can message numbers other than your own test recipients.
This takes days, sometimes longer. Start it before you need it.

## 3. Catalog

Create a catalog in Meta Commerce Manager. Note the catalog ID.
`META_CATALOG_ID`.

Items need `retailer_id` (your SKU), `name`, `description`, `price` in
cents, `currency`, `image_url`, `availability`, and `url`. The image must be
publicly reachable; Meta fetches it.

## 4. Connect the catalog to the phone number

In Commerce Manager, link the catalog to the WhatsApp Business Account. Then
set commerce settings on the phone number: catalog visibility and whether
cart is enabled.

## 5. Tokens

- `META_ACCESS_TOKEN` — system user token with `whatsapp_business_messaging`
  and `catalog_management`. Temporary tokens expire in 24 hours; generate a
  system user token for anything real.
- `META_APP_SECRET` — used to verify webhook signatures. Not optional.
- `WA_VERIFY_TOKEN` — any string. Paste the same value into the Meta webhook
  form and into `.env`.

## 6. Webhook

Point Meta at `https://your-host/webhooks/whatsapp` and subscribe to the
`messages` field.

Meta sends a GET with `hub.verify_token` and `hub.challenge` to confirm the
endpoint. `main.py:verify` handles it. Every POST is signed with
`x-hub-signature-256`; `whatsapp.verify_signature` checks it and the route
returns 401 if it fails.

For local development, tunnel with ngrok or cloudflared and update the
callback URL each time it changes.

## Message types

| Type | Use |
| --- | --- |
| `text` | Everything conversational, including all negotiation turns |
| `interactive.product` | One item as a product detail card |
| `interactive.product_list` | Up to 30 items across up to 10 sections |
| `interactive.catalog_message` | Link to the whole catalog |

## Limits worth knowing before you design around them

- No native checkout. A completed cart arrives as an `order` webhook and you
  reply with a payment link.
- Product messages sent through the Cloud API do not show the cart icon in
  the thread header the way the WhatsApp Business app does.
- Businesses based in India have extra compliance requirements.
- Catalog items are reviewed. Policy-violating items get flagged and can be
  appealed in Commerce Manager.
- Unverified businesses can connect at most two phone numbers to a WABA.

## Policy

Read the WhatsApp Commerce Policy before listing anything. Several
categories are prohibited outright, and a rejected catalog can take the
phone number down with it.
