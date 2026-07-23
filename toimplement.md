Obsiden
sqlite

QMD to store obsiden


advance memory: 
knowledge graph

raffity ya bonefire: 


retail api replace with openclaw


ask from ali taufiq:


concurrency to maintain

if it is feasible can we :

go for retail.



chat automations , meta is blocking



To make it always-on (so it survives reboots)

sudo cp admin/openclaw-admin.service /etc/systemd/system/
sudo systemctl enable --now openclaw-admin
## Coexistence & labels checklist (added 2026-07-23)

- [ ] Kapso already reports is_coexistence=true for Decor Moments — check the
      phone: do Decor Moments chats appear in the WhatsApp Business app? If yes,
      coexistence is already done (no QR needed). If no, reconnect in Kapso via
      "Keep using the WhatsApp Business app" (QR scan).
- [ ] Once chats show in the app: test whether the bot's label changes appear
      there automatically. (Kapso has no labels API today — probe returned
      "Unsupported endpoint" — so likely not.)
- [ ] If labels don't show in the app: build the Baileys "label bridge" — a
      small linked-device client that ONLY applies labels from the customer DB;
      all messaging stays on Kapso. Reuse the reconciler logic from
      openclaw-patches. Note: unofficial client = small ToS risk, low volume.
