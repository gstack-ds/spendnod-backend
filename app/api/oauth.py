"""OAuth 2.1 endpoints for MCP authentication.

Endpoints:
    GET  /.well-known/oauth-authorization-server  — RFC 8414 metadata
    GET  /oauth/authorize                          — login form
    POST /oauth/login                              — credential validation → login_proof JWT
    GET  /oauth/consent                            — consent page
    POST /oauth/consent                            — issue auth code → redirect
    POST /oauth/token                              — code exchange (PKCE)
    POST /oauth/register                           — dynamic client registration (RFC 7591)
"""

import secrets
import uuid
from typing import Annotated, Optional
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from jose import JWTError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models.database import OAuthClient, User
from app.services import oauth_service

router = APIRouter()

DbDep = Annotated[AsyncSession, Depends(get_db)]

# ---------------------------------------------------------------------------
# Minimal HTML helpers
# ---------------------------------------------------------------------------

_BASE_STYLE = """
  body{font-family:system-ui,sans-serif;background:#0f172a;color:#e2e8f0;
       display:flex;align-items:center;justify-content:center;min-height:100vh;margin:0}
  .card{background:#1e293b;border:1px solid #334155;border-radius:12px;
        padding:32px;width:100%;max-width:400px;box-sizing:border-box}
  h1{margin:0 0 6px;font-size:1.25rem;font-weight:600;color:#f8fafc}
  p{margin:0 0 20px;color:#94a3b8;font-size:.875rem;line-height:1.5}
  label{display:block;margin-bottom:4px;font-size:.8125rem;font-weight:500;color:#cbd5e1}
  input{width:100%;padding:9px 12px;background:#0f172a;border:1px solid #475569;
        border-radius:8px;color:#f1f5f9;font-size:.875rem;box-sizing:border-box;
        outline:none;transition:border-color .15s}
  input:focus{border-color:#6366f1}
  .btn{width:100%;padding:10px;background:#6366f1;color:#fff;border:none;
       border-radius:8px;font-size:.875rem;font-weight:600;cursor:pointer;
       margin-top:16px;transition:background .15s}
  .btn:hover{background:#4f46e5}
  .btn-deny{background:#1e293b;border:1px solid #475569;color:#94a3b8}
  .btn-deny:hover{background:#334155;color:#e2e8f0}
  .err{color:#f87171;font-size:.8125rem;margin-top:8px}
  .field{margin-bottom:14px}
  .logo{display:flex;align-items:center;gap:8px;margin-bottom:20px}
  .logo svg{width:24px;height:24px;color:#818cf8}
  .logo span{font-weight:700;font-size:1.1rem;color:#f8fafc}
  .scope-box{background:#0f172a;border:1px solid #334155;border-radius:8px;
             padding:12px 14px;margin-bottom:20px;font-size:.8125rem;color:#94a3b8}
  .scope-box strong{color:#e2e8f0}
"""

_LOGO = """
  <div class="logo">
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
      <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/>
    </svg>
    <span>SpendNod</span>
  </div>
"""


def _login_page(
    client_name: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    code_challenge_method: str,
    scope: str,
    state: str,
    response_type: str,
    error: Optional[str] = None,
) -> str:
    err_html = f'<p class="err">{error}</p>' if error else ""
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Sign in — SpendNod</title>
<style>{_BASE_STYLE}
.forgot-link{{font-size:.8125rem;color:#818cf8;text-decoration:none;float:right;margin-top:6px}}
.forgot-link:hover{{text-decoration:underline}}
</style></head>
<body><div class="card">
{_LOGO}
<div id="login-section">
<h1>Sign in to SpendNod</h1>
<p><strong style="color:#e2e8f0">{client_name}</strong> is requesting access to your SpendNod account.</p>
<form method="post" action="/oauth/login">
  <input type="hidden" name="client_id" value="{client_id}">
  <input type="hidden" name="redirect_uri" value="{redirect_uri}">
  <input type="hidden" name="code_challenge" value="{code_challenge}">
  <input type="hidden" name="code_challenge_method" value="{code_challenge_method}">
  <input type="hidden" name="scope" value="{scope}">
  <input type="hidden" name="state" value="{state}">
  <input type="hidden" name="response_type" value="{response_type}">
  <div class="field">
    <label for="email">Email</label>
    <input type="email" id="email" name="email" required autofocus placeholder="you@example.com">
  </div>
  <div class="field">
    <label for="password">Password</label>
    <input type="password" id="password" name="password" required placeholder="••••••••">
    <a href="#" class="forgot-link" onclick="showForgot();return false;">Forgot your password?</a>
    <div style="clear:both"></div>
  </div>
  {err_html}
  <button type="submit" class="btn">Sign in</button>
</form>
</div>
<div id="reset-section" style="display:none">
<h1>Reset your password</h1>
<p>Enter your email and we&apos;ll send you a link to reset your password.</p>
<form method="post" action="/oauth/forgot-password">
  <div class="field">
    <label for="reset-email">Email</label>
    <input type="email" id="reset-email" name="email" required autofocus placeholder="you@example.com">
  </div>
  <button type="submit" class="btn">Send reset link</button>
  <button type="button" class="btn btn-deny" onclick="showLogin()" style="margin-top:8px">Back to sign in</button>
</form>
</div>
<script>
function showForgot(){{document.getElementById('login-section').style.display='none';document.getElementById('reset-section').style.display='';document.getElementById('reset-email').focus();}}
function showLogin(){{document.getElementById('login-section').style.display='';document.getElementById('reset-section').style.display='none';}}
</script>
</div></body></html>"""


def _consent_page(client_name: str, login_proof: str, scope: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Authorize — SpendNod</title>
<style>{_BASE_STYLE}</style></head>
<body><div class="card">
{_LOGO}
<h1>Allow access?</h1>
<p><strong style="color:#e2e8f0">{client_name}</strong> is asking for permission to submit authorization requests on behalf of your agents.</p>
<div class="scope-box">
  <strong>Permissions requested:</strong><br>
  Submit transactions for authorization via your SpendNod agents
</div>
<form method="post" action="/oauth/consent">
  <input type="hidden" name="login_proof" value="{login_proof}">
  <button type="submit" name="action" value="approve" class="btn">Allow</button>
  <button type="submit" name="action" value="deny" class="btn btn-deny" style="margin-top:8px">Deny</button>
</form>
</div></body></html>"""


def _reset_sent_page(email: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Check your email — SpendNod</title>
<style>{_BASE_STYLE}</style></head>
<body><div class="card">
{_LOGO}
<h1>Check your email</h1>
<p>We sent a password reset link to <strong style="color:#e2e8f0">{email}</strong>.<br>
Click the link in the email to set a new password, then come back and sign in.</p>
</div></body></html>"""


def _error_page(message: str) -> str:
    return f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8">
<title>Error — SpendNod</title>
<style>{_BASE_STYLE}</style></head>
<body><div class="card">
{_LOGO}
<h1>Something went wrong</h1>
<p class="err">{message}</p>
</div></body></html>"""


# ---------------------------------------------------------------------------
# RFC 8414 metadata
# ---------------------------------------------------------------------------

@router.get("/.well-known/oauth-authorization-server")
async def oauth_metadata() -> JSONResponse:
    base = settings.API_URL.rstrip("/")
    return JSONResponse({
        "issuer": base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint": f"{base}/oauth/token",
        "registration_endpoint": f"{base}/oauth/register",
        "response_types_supported": ["code"],
        "grant_types_supported": ["authorization_code"],
        "code_challenge_methods_supported": ["S256"],
        "token_endpoint_auth_methods_supported": ["none"],
    })


# ---------------------------------------------------------------------------
# Authorization endpoint — login form
# ---------------------------------------------------------------------------

@router.get("/oauth/authorize", response_class=HTMLResponse)
async def oauth_authorize_form(
    request: Request,
    db: DbDep,
    response_type: str = "code",
    client_id: str = "",
    redirect_uri: str = "",
    code_challenge: str = "",
    code_challenge_method: str = "S256",
    scope: str = "authorize",
    state: str = "",
) -> HTMLResponse:
    if response_type != "code":
        return HTMLResponse(_error_page("Unsupported response_type. Only 'code' is supported."), status_code=400)
    if not client_id or not redirect_uri or not code_challenge:
        return HTMLResponse(_error_page("Missing required parameters: client_id, redirect_uri, code_challenge."), status_code=400)

    client = await oauth_service.get_client(db, client_id)
    if client is None:
        return HTMLResponse(_error_page(f"Unknown client_id: {client_id}"), status_code=400)
    if not oauth_service.validate_redirect_uri(client, redirect_uri):
        return HTMLResponse(_error_page("redirect_uri is not allowed for this client."), status_code=400)

    client_name = client.client_name or client_id
    return HTMLResponse(_login_page(
        client_name=client_name,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope,
        state=state,
        response_type=response_type,
    ))


# ---------------------------------------------------------------------------
# Login form POST — validate credentials, issue login_proof JWT
# ---------------------------------------------------------------------------

@router.post("/oauth/login", response_class=HTMLResponse)
async def oauth_login(
    db: DbDep,
    client_id: str = Form(...),
    redirect_uri: str = Form(...),
    code_challenge: str = Form(...),
    code_challenge_method: str = Form("S256"),
    scope: str = Form("authorize"),
    state: str = Form(""),
    response_type: str = Form("code"),
    email: str = Form(...),
    password: str = Form(...),
) -> HTMLResponse:
    client = await oauth_service.get_client(db, client_id)
    client_name = (client.client_name if client else None) or client_id

    def show_error(msg: str) -> HTMLResponse:
        return HTMLResponse(_login_page(
            client_name=client_name,
            client_id=client_id,
            redirect_uri=redirect_uri,
            code_challenge=code_challenge,
            code_challenge_method=code_challenge_method,
            scope=scope,
            state=state,
            response_type=response_type,
            error=msg,
        ), status_code=401)

    if client is None or not oauth_service.validate_redirect_uri(client, redirect_uri):
        return show_error("Invalid client or redirect_uri.")

    supabase_uid_str = await oauth_service.validate_supabase_credentials(email, password)
    if supabase_uid_str is None:
        return show_error("Incorrect email or password.")

    try:
        supabase_uid = uuid.UUID(supabase_uid_str)
    except (ValueError, TypeError):
        return show_error("Authentication error. Please try again.")

    result = await db.execute(select(User).where(User.supabase_auth_id == supabase_uid))
    user = result.scalar_one_or_none()
    if user is None:
        return show_error("No SpendNod account found for this email. Sign up at the dashboard first.")

    login_proof = oauth_service.create_login_proof(
        user_id=user.id,
        client_id=client_id,
        redirect_uri=redirect_uri,
        code_challenge=code_challenge,
        code_challenge_method=code_challenge_method,
        scope=scope,
        state=state,
    )
    return RedirectResponse(f"/oauth/consent?lp={login_proof}", status_code=303)


# ---------------------------------------------------------------------------
# Consent page
# ---------------------------------------------------------------------------

@router.get("/oauth/consent", response_class=HTMLResponse)
async def oauth_consent_form(db: DbDep, lp: str = "") -> HTMLResponse:
    if not lp:
        return HTMLResponse(_error_page("Missing login proof."), status_code=400)
    try:
        payload = oauth_service.decode_login_proof(lp)
    except JWTError:
        return HTMLResponse(_error_page("Login session expired. Please start again."), status_code=400)

    client = await oauth_service.get_client(db, payload["client_id"])
    client_name = (client.client_name if client else None) or payload["client_id"]
    return HTMLResponse(_consent_page(client_name=client_name, login_proof=lp, scope=payload["scope"]))


# ---------------------------------------------------------------------------
# Consent form POST — issue auth code
# ---------------------------------------------------------------------------

@router.post("/oauth/consent")
async def oauth_consent_submit(
    db: DbDep,
    login_proof: str = Form(...),
    action: str = Form("approve"),
) -> RedirectResponse:
    try:
        payload = oauth_service.decode_login_proof(login_proof)
    except JWTError:
        return HTMLResponse(_error_page("Login session expired. Please start again."), status_code=400)

    redirect_uri = payload["redirect_uri"]
    state = payload.get("state", "")

    if action != "approve":
        params = urlencode({"error": "access_denied", "state": state} if state else {"error": "access_denied"})
        return RedirectResponse(f"{redirect_uri}?{params}", status_code=303)

    code = await oauth_service.create_auth_code(
        db=db,
        user_id=uuid.UUID(payload["sub"]),
        client_id=payload["client_id"],
        redirect_uri=redirect_uri,
        code_challenge=payload["code_challenge"],
        code_challenge_method=payload["code_challenge_method"],
        scope=payload["scope"],
    )

    params_dict = {"code": code}
    if state:
        params_dict["state"] = state
    params = urlencode(params_dict)
    return RedirectResponse(f"{redirect_uri}?{params}", status_code=303)


# ---------------------------------------------------------------------------
# Forgot password — calls Supabase recover endpoint
# ---------------------------------------------------------------------------

@router.post("/oauth/forgot-password", response_class=HTMLResponse)
async def oauth_forgot_password(email: str = Form(...)) -> HTMLResponse:
    # Always show the confirmation page — never reveal whether the email exists
    await oauth_service.send_password_reset(email)
    return HTMLResponse(_reset_sent_page(email))


# ---------------------------------------------------------------------------
# Token endpoint — PKCE code exchange
# ---------------------------------------------------------------------------

@router.post("/oauth/token")
async def oauth_token(
    db: DbDep,
    grant_type: str = Form(...),
    code: str = Form(...),
    redirect_uri: str = Form(...),
    client_id: str = Form(...),
    code_verifier: str = Form(...),
) -> JSONResponse:
    if grant_type != "authorization_code":
        return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)

    token, error = await oauth_service.exchange_code_for_token(
        db=db,
        code=code,
        code_verifier=code_verifier,
        redirect_uri=redirect_uri,
        client_id=client_id,
    )
    if error:
        return JSONResponse({"error": error}, status_code=400)

    return JSONResponse({
        "access_token": token,
        "token_type": "bearer",
        "expires_in": oauth_service._TOKEN_TTL_DAYS * 86400,
        "scope": "authorize",
    })


# ---------------------------------------------------------------------------
# Dynamic client registration (RFC 7591)
# ---------------------------------------------------------------------------

@router.post("/oauth/register")
async def oauth_register(request: Request, db: DbDep) -> JSONResponse:
    body = await request.json()
    redirect_uris = body.get("redirect_uris", [])
    client_name = body.get("client_name", "MCP Client")

    if not redirect_uris:
        return JSONResponse({"error": "invalid_client_metadata", "error_description": "redirect_uris is required"}, status_code=400)

    client_id = f"dyn-{secrets.token_urlsafe(16)}"
    client = OAuthClient(
        client_id=client_id,
        redirect_uri_prefixes=redirect_uris,
        client_name=client_name,
    )
    db.add(client)
    await db.commit()

    return JSONResponse({
        "client_id": client_id,
        "redirect_uris": redirect_uris,
        "client_name": client_name,
        "token_endpoint_auth_method": "none",
        "grant_types": ["authorization_code"],
        "response_types": ["code"],
    }, status_code=201)
