import os
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from authlib.integrations.starlette_client import OAuth
from starlette.middleware.sessions import SessionMiddleware
import httpx

app = FastAPI()

# Session middleware — used for cookie-based login state
app.add_middleware(SessionMiddleware, secret_key=os.environ.get("SESSION_SECRET", "changeme"))

# Static files
app.mount("/portal/static", StaticFiles(directory="/opt/vpn-portal/app/static"), name="static")

templates = Jinja2Templates(directory="/opt/vpn-portal/app/templates")

# Keycloak OIDC setup
oauth = OAuth()
oauth.register(
    name="keycloak",
    client_id=os.environ.get("OIDC_CLIENT_ID", "vpn-portal"),
    client_secret=os.environ.get("OIDC_CLIENT_SECRET", ""),
    server_metadata_url=os.environ.get("OIDC_DISCOVERY_URL"),
    client_kwargs={"scope": "openid profile email"},
)

OVPN_FILE = os.environ.get("OVPN_FILE", "/etc/openvpn/client.ovpn")
STATUS_FILE = os.environ.get("STATUS_FILE", "/var/log/openvpn/status.log")


def parse_status():
    """Parse OpenVPN status.log and return list of connected clients."""
    clients = []
    try:
        with open(STATUS_FILE, "r") as f:
            lines = f.readlines()
        in_client_section = False
        for line in lines:
            line = line.strip()
            if line.startswith("VIRTUAL ADDRESS,COMMON NAME"):
                in_client_section = True
                continue
            if in_client_section:
                if line.startswith("GLOBAL STATS") or line == "":
                    break
                parts = line.split(",")
                if len(parts) >= 4:
                    clients.append({
                        "vpn_ip": parts[0],
                        "common_name": parts[1],
                        "real_ip": parts[2].split(":")[0],
                        "connected_since": parts[3],
                    })
    except Exception:
        pass
    return clients


def get_user_session(email: str, clients: list):
    """Find active session for a specific user by email (matched against common_name)."""
    for c in clients:
        if c["common_name"].lower() == email.lower():
            return c
    return None


@app.get("/portal/login")
async def login(request: Request):
    redirect_uri = os.environ.get("OIDC_REDIRECT_URI")
    return await oauth.keycloak.authorize_redirect(request, redirect_uri)


@app.get("/portal/callback")
async def callback(request: Request):
    try:
        token = await oauth.keycloak.authorize_access_token(request)
        user = token.get("userinfo")
        request.session["user"] = {
            "email": user.get("email"),
            "name": user.get("name") or user.get("preferred_username"),
            "preferred_username": user.get("preferred_username"),
        }
        return RedirectResponse(url="/portal")
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Auth failed: {str(e)}")


@app.get("/portal/logout")
async def logout(request: Request):
    request.session.clear()
    keycloak_logout = (
        os.environ.get("OIDC_ISSUER")
        + "/protocol/openid-connect/logout?redirect_uri="
        + os.environ.get("BASE_URL", "https://vpn.example.com")
        + "/portal"
    )
    return RedirectResponse(url=keycloak_logout)


@app.get("/portal", response_class=HTMLResponse)
async def portal(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/portal/login")

    clients = parse_status()
    session = get_user_session(user["email"], clients)

    return templates.TemplateResponse(request=request, name="portal.html", context={
        "user": user,
        "session": session,
        "total_connected": len(clients),
    })


@app.get("/portal/download")
async def download(request: Request):
    user = request.session.get("user")
    if not user:
        return RedirectResponse(url="/portal/login")
    if not os.path.exists(OVPN_FILE):
        raise HTTPException(status_code=404, detail="Profile not found")
    return FileResponse(
        OVPN_FILE,
        media_type="application/octet-stream",
        filename="vpn-client.ovpn"
    )