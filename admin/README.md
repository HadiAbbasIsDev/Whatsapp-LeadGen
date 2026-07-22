# Bot Admin Dashboard (CRM)

A CRM-style web panel for a non-technical owner to supervise and control the
WhatsApp bot and manage customers by label. It does **not** show conversations.

## What it shows / does

- **ONLINE / OFFLINE** status + whether WhatsApp is connected, the model, and uptime.
- **▶ Start bot** / **■ Stop bot (kill switch)** — controls the openclaw gateway.
  The dashboard keeps running even when the bot is off, so you can start it anytime.
- **All customers, CRM-style**: search (name/number/notes), sortable columns,
  CSV export, wa.me links, relative "last seen" times.
- **Filter chips for every label** with live counts: New customer, Important,
  Hot leads, Follow-up, Junk, Complaints, and the team lists (Ahsan, Ahmed,
  Imran, Rafay).
- **Change a customer's label** from a dropdown on each row. This goes through
  `workspace/db.py set-category` (the sole locked category writer), so the
  WhatsApp Business labels and the inbound category gate stay in sync. A confirm
  dialog warns when a move will silence or un-silence the bot on that chat
  (silenced chats show a ⏸ marker).
- **Captured leads** table (read-only).

The dashboard manages the gateway as a subprocess (applies the openclaw patches first,
then launches it), so the owner never touches the terminal.

## Run it (quick)

```bash
python3 admin/app.py
```

It prints the URL and login, e.g.:

```
Local:   http://127.0.0.1:8088
Network: http://192.168.0.111:8088     <- open this from your laptop/phone on the same Wi-Fi
Login:   admin  /  <password>
```

The password is auto-generated on first run and stored in `admin/admin_password.txt`
(gitignored). To set your own, run with `ADMIN_PASS=your-password python3 admin/app.py`.

## Run it always-on (recommended for production)

So the dashboard is always available (survives reboots), install the systemd service:

```bash
sudo cp admin/openclaw-admin.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now openclaw-admin
sudo systemctl status openclaw-admin      # check it's running
```

Then the owner just bookmarks `http://<this-machine-ip>:8088` and uses the buttons.
(The bot itself is still started/stopped on demand from the dashboard.)

## Security notes

- The dashboard can **start/stop the bot**, so protect it:
  - It binds to all interfaces (`0.0.0.0:8088`) so you can reach it on your LAN, and
    requires the `admin` password. **Set a strong `ADMIN_PASS`.**
  - Keep it on a trusted network. For remote access, prefer an SSH tunnel or Tailscale
    rather than exposing port 8088 to the internet.
  - To restrict to this machine only, set `ADMIN_HOST=127.0.0.1`.

## Config (env vars)

| Var | Default | Meaning |
|---|---|---|
| `ADMIN_PASS` | generated | Dashboard password (user is always `admin`) |
| `ADMIN_HOST` | `0.0.0.0` | Bind address (`127.0.0.1` = this machine only) |
| `ADMIN_PORT` | `8088` | Port |
