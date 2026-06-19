# Bot Admin Dashboard

A simple web panel for a non-technical owner to supervise and control the WhatsApp
bot — **start/stop it, see if it's online, and monitor customers by label**. It does
**not** show conversations.

## What it shows / does

- **ONLINE / OFFLINE** status + whether WhatsApp is connected, the model, and uptime.
- **▶ Start bot** / **■ Stop bot (kill switch)** — controls the openclaw gateway.
  The dashboard keeps running even when the bot is off, so you can start it anytime.
- **Customer counts** by label: New customers, Hot leads, Important — plus a customer list.

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
