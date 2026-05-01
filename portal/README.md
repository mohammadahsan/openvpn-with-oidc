# VPN Self-Service Portal

> A lightweight FastAPI portal that authenticates users via OIDC and allows them to:
> - Download the `.ovpn` client profile
> - View per-OS setup instructions
> - See their active VPN session status (IP, connected since, real IP)
>
> Runs on the same VM as OpenVPN CE. Served under `https://vpn.example.com/portal`.

![portal](../image/README/1777624605601.png)
---

## Architecture

```
Browser → Nginx (443) → /portal → FastAPI (127.0.0.1:8080)
                      → /       → openvpn-auth-oauth2 (127.0.0.1:9000)

FastAPI ←→ Keycloak OIDC (login/callback)
FastAPI ←→ /var/log/openvpn/status.log (session status)
FastAPI ←→ /etc/openvpn/client.ovpn (profile download)
```

---

## Prerequisites

- OpenVPN CE running (see main VPN guide)
- Nginx with TLS already configured for your domain
- Keycloak running and accessible
- Python 3.11 on the VM

---

## Phase 1 — Install Dependencies

```bash
apt install -y python3-pip python3-venv

python3 -m venv /opt/vpn-portal
/opt/vpn-portal/bin/pip install fastapi uvicorn authlib httpx python-multipart jinja2 itsdangerous
```

---

## Phase 2 — Directory Structure

```bash
mkdir -p /opt/vpn-portal/app/templates
mkdir -p /opt/vpn-portal/app/static
```

Final layout:

```
/opt/vpn-portal/
├── bin/
│   ├── uvicorn
│   └── pip
├── lib/
│   └── python3.11/
└── app/
    ├── main.py
    ├── static/
    └── templates/
        └── portal.html
```

---

## Phase 3 — FastAPI Application

```bash
cat > /opt/vpn-portal/app/main.py <<'EOF'
# The File is in portal/app/main.py
EOF
```

---

## Phase 4 — HTML Template

```bash
cat > /opt/vpn-portal/app/templates/portal.html <<'EOF'
# The File is in portal/templates/portal.html
EOF
```

---

## Phase 5 — Environment Configuration

```bash
SESSION_SECRET=$(openssl rand -hex 16)

cat > /etc/sysconfig/vpn-portal <<EOF
OIDC_CLIENT_ID=openvpn
OIDC_CLIENT_SECRET=<your-keycloak-client-secret>
OIDC_ISSUER=https://id.ops.example.com/realms/myrealm
OIDC_DISCOVERY_URL=https://id.ops.example.com/realms/myrealm/.well-known/openid-configuration
OIDC_REDIRECT_URI=https://vpn.example.com/portal/callback
BASE_URL=https://vpn.example.com
SESSION_SECRET=${SESSION_SECRET}
OVPN_FILE=/etc/openvpn/client.ovpn
STATUS_FILE=/var/log/openvpn/status.log
EOF

chmod 600 /etc/sysconfig/vpn-portal
```

| Variable | Description |
|---|---|
| `OIDC_CLIENT_ID` | Keycloak client ID — can reuse the `openvpn` client |
| `OIDC_CLIENT_SECRET` | From Keycloak client Credentials tab |
| `OIDC_ISSUER` | Keycloak realm URL |
| `OIDC_DISCOVERY_URL` | Keycloak OIDC discovery endpoint |
| `OIDC_REDIRECT_URI` | Must match Keycloak Valid Redirect URIs |
| `BASE_URL` | Public base URL of your VPN server |
| `SESSION_SECRET` | Random secret for cookie signing (any length) |
| `OVPN_FILE` | Path to the `.ovpn` profile to serve for download |
| `STATUS_FILE` | Path to OpenVPN status log for session info |

---

## Phase 6 — Keycloak Configuration

In your Keycloak realm, add the portal callback to the existing `openvpn` client (or create a separate `vpn-portal` client):

| Setting | Value |
|---|---|
| Valid Redirect URIs | Add `https://vpn.example.com/portal/callback` |
| Web Origins | Add `https://vpn.example.com` |

If reusing the same client (as in this setup), just add the new redirect URI — no new client needed.

---

## Phase 7 — systemd Service

```bash
cat > /etc/systemd/system/vpn-portal.service <<'EOF'
[Unit]
Description=VPN Self-Service Portal
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/opt/vpn-portal/app
ExecStart=/opt/vpn-portal/bin/uvicorn main:app --host 127.0.0.1 --port 8080
EnvironmentFile=/etc/sysconfig/vpn-portal
Restart=on-failure
RestartSec=5s

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now vpn-portal
systemctl status vpn-portal
```

---

## Phase 8 — Nginx Configuration

Add the `/portal` location block to your existing Nginx server block. The full config for `vpn.example.com`:

```nginx
server {
    listen 443 ssl;
    server_name vpn.example.com;

    ssl_certificate     /etc/letsencrypt/live/vpn.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/vpn.example.com/privkey.pem;
    include /etc/letsencrypt/options-ssl-nginx.conf;
    ssl_dhparam /etc/letsencrypt/ssl-dhparams.pem;

    # VPN Self-Service Portal
    location /portal {
        proxy_pass http://127.0.0.1:8080;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }

    # openvpn-auth-oauth2 OIDC WebAuth callback
    location / {
        proxy_pass http://127.0.0.1:9000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}

server {
    listen 80;
    server_name vpn.example.com;
    return 301 https://$host$request_uri;
}
```

> ⚠️ `/portal` must come **before** `/` — Nginx matches locations in order and `/` would catch everything otherwise.

```bash
nginx -t && systemctl reload nginx
```

---

## Verification

Test the portal directly before going through Nginx:

```bash
export $(cat /etc/sysconfig/vpn-portal | xargs) && \
  /opt/vpn-portal/bin/uvicorn main:app --host 127.0.0.1 --port 8080
```

Then visit `https://vpn.example.com/portal` — you should be redirected to Keycloak login.

---

## How Session Status Works

The portal reads `/var/log/openvpn/status.log` which OpenVPN updates every 60 seconds. It matches the logged-in user's email against the `common_name` field in the status log.

> This works because `username-as-common-name` is set in `server.conf` and `openvpn-auth-oauth2` sets the username to the user's `preferred_username` (email) from the Keycloak token.

---

## File Reference

| File | Purpose |
|---|---|
| `/opt/vpn-portal/app/main.py` | FastAPI application |
| `/opt/vpn-portal/app/templates/portal.html` | Portal HTML template |
| `/etc/sysconfig/vpn-portal` | Environment configuration |
| `/etc/systemd/system/vpn-portal.service` | systemd unit |

---

## Troubleshooting

### `ModuleNotFoundError: No module named 'itsdangerous'`

```bash
/opt/vpn-portal/bin/pip install itsdangerous
systemctl restart vpn-portal
```

### `address already in use` on port 8080

```bash
fuser -k 8080/tcp
systemctl restart vpn-portal
```

### Internal Server Error after login

Run directly and check the traceback:

```bash
cd /opt/vpn-portal/app
export $(cat /etc/sysconfig/vpn-portal | xargs) && \
  /opt/vpn-portal/bin/uvicorn main:app --host 127.0.0.1 --port 8080
```

### `TypeError: unhashable type: 'dict'` in Jinja2

Newer Jinja2/Starlette versions require the updated `TemplateResponse` signature. Ensure `main.py` uses:

```python
return templates.TemplateResponse(request=request, name="portal.html", context={
    "user": user,
    ...
})
```

Not the old form with `"request": request` inside the context dict.

### Session not showing as connected

The session status matches on `common_name` in `status.log`. This field is populated by `username-as-common-name` in `server.conf`. Verify:

```bash
cat /var/log/openvpn/status.log
```

The `VIRTUAL ADDRESS` section should show the user's email as `COMMON NAME`.

### Auth callback fails

Verify the redirect URI in Keycloak exactly matches `OIDC_REDIRECT_URI` in `/etc/sysconfig/vpn-portal` — including trailing slash or lack thereof.

### Tail portal logs

```bash
journalctl -fu vpn-portal
```

---

## References

- [FastAPI Documentation](https://fastapi.tiangolo.com)
- [Authlib OIDC Integration](https://docs.authlib.org/en/latest/integrations/starlette.html)
- [Keycloak OIDC](https://www.keycloak.org/docs/latest/securing_apps/)
