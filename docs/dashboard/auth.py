#!/usr/bin/env python3
"""
🔐 scout auth guard — WebAuthn (passkey) passwordless authentication.

Why this exists
---------------
Before this, anyone who knew the rover's IP:port could drive it. That's a
physical-safety problem: a stranger could send a real robot down a real
sidewalk. This module locks the dashboard behind **WebAuthn passkeys** —
the same passwordless tech behind Apple/Google/1Password passkeys.

The model
---------
A *passkey* is a public/private keypair. The private key NEVER leaves your
device's secure enclave (Touch ID / Face ID / Windows Hello / a hardware
YubiKey). The rover only ever stores the *public* key. To log in you prove
possession of the private key by signing a server-issued challenge with
your fingerprint/face — nothing to phish, nothing to leak, no password.

Think of the registered passkey as your **device identity** ("id + wallet"):
  • the credential_id    → your account handle on this rover
  • the public key       → the lock the rover keeps
  • your secure enclave  → the only place the private key lives

Flow
----
  1. FIRST RUN (admin enrollment):
     - No credentials exist yet → /auth/status reports `setup_required`.
     - Admin opens the dashboard, is shown the enrollment screen, taps
       "Create passkey" → browser WebAuthn ceremony → public key saved.
     - From now on the rover is sealed.

  2. LOGIN (every session afterwards):
     - Browser requests a challenge → signs it with the passkey →
       server verifies the signature against the stored public key →
       issues a short-lived signed JWT session token.

  3. GUARD (every request):
     - The JWT is sent on WS connect (?token=) and on each /api/* call
       (Authorization: Bearer ...). `require_auth()` validates it.
       No valid token → 401, rover stays still.

Storage
-------
Credentials + the server's JWT signing secret live in a single JSON file
(`AUTH_STORE`, default .memory/.neon_auth.json, chmod 600). Self-contained, no DB.

Env knobs
---------
  NEON_AUTH_ENABLED   "true"/"false"  (default true)  — master switch
  NEON_AUTH_STORE     path            (default .memory/.neon_auth.json)
  NEON_AUTH_RP_ID     relying-party id (default derived from request host)
  NEON_AUTH_RP_NAME   display name    (default "neon · G1")
  NEON_AUTH_ORIGIN    expected origin (default derived from request)
  NEON_AUTH_TOKEN_TTL session seconds (default 86400 = 24h)
  NEON_AUTH_BOOTSTRAP_TOKEN  optional one-time secret required to enroll
                              the FIRST admin (defends open enrollment window)
"""
from __future__ import annotations

import base64
import ipaddress
import json
import os
import secrets
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import jwt  # PyJWT
from fastapi import HTTPException, Request, WebSocket

from webauthn import (
    generate_registration_options,
    verify_registration_response,
    generate_authentication_options,
    verify_authentication_response,
    options_to_json,
)
from webauthn.helpers.structs import (
    PublicKeyCredentialDescriptor,
    AuthenticatorSelectionCriteria,
    ResidentKeyRequirement,
    UserVerificationRequirement,
)
from webauthn.helpers import base64url_to_bytes, bytes_to_base64url


# config
def _bool_env(key: str, default: bool) -> bool:
    return os.getenv(key, str(default)).strip().lower() in ("1", "true", "yes", "on")


AUTH_ENABLED = _bool_env("NEON_AUTH_ENABLED", True)
AUTH_STORE = Path(os.getenv("NEON_AUTH_STORE", ".memory/.neon_auth.json")).resolve()
RP_NAME = os.getenv("NEON_AUTH_RP_NAME", "neon · G1")
TOKEN_TTL = int(os.getenv("NEON_AUTH_TOKEN_TTL", "86400"))
BOOTSTRAP_TOKEN = os.getenv("NEON_AUTH_BOOTSTRAP_TOKEN", "").strip()

# RP_ID / ORIGIN can be forced via env; otherwise derived per-request from Host.
FORCE_RP_ID = os.getenv("NEON_AUTH_RP_ID", "").strip()
FORCE_ORIGIN = os.getenv("NEON_AUTH_ORIGIN", "").strip()


# store (single JSON file, thread-safe)
_lock = threading.Lock()


def _default_store() -> Dict[str, Any]:
    return {
        "jwt_secret": secrets.token_urlsafe(48),
        "credentials": [],   # list of {id, public_key, sign_count, name, created}
        "created": time.time(),
    }


def _load() -> Dict[str, Any]:
    with _lock:
        if AUTH_STORE.exists():
            try:
                return json.loads(AUTH_STORE.read_text())
            except Exception:
                pass
        store = _default_store()
        _save(store)
        return store


def _save(store: Dict[str, Any]) -> None:
    AUTH_STORE.write_text(json.dumps(store, indent=2))
    try:
        os.chmod(AUTH_STORE, 0o600)
    except Exception:
        pass


def _jwt_secret() -> str:
    return _load()["jwt_secret"]


def has_credentials() -> bool:
    return len(_load().get("credentials", [])) > 0


def list_credentials() -> List[Dict[str, Any]]:
    return [
        {"id": c["id"], "name": c.get("name", "passkey"), "created": c.get("created")}
        for c in _load().get("credentials", [])
    ]


def rename_credential(cred_id: str, name: str) -> Dict[str, Any]:
    store = _load()
    match = next((c for c in store["credentials"] if c["id"] == cred_id), None)
    if not match:
        raise HTTPException(404, "credential not found")
    match["name"] = (name or "passkey").strip()[:64]
    _save(store)
    return {"id": cred_id, "name": match["name"]}


def delete_credential(cred_id: str) -> Dict[str, Any]:
    """Revoke a passkey. Refuses to remove the LAST one (would lock everyone
    out and re-open the rover to anyone via the setup flow)."""
    store = _load()
    creds = store["credentials"]
    if not any(c["id"] == cred_id for c in creds):
        raise HTTPException(404, "credential not found")
    if len(creds) <= 1:
        raise HTTPException(409, "cannot remove the last passkey — enroll another first")
    store["credentials"] = [c for c in creds if c["id"] != cred_id]
    _save(store)
    return {"ok": True, "removed": cred_id, "remaining": len(store["credentials"])}


# per-request RP id / origin derivation
def _host_only(host: str) -> str:
    # strip port for RP_ID (WebAuthn rpId is a domain, no port/scheme)
    return host.split(":")[0]


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def rpid_is_usable(host_only: str) -> bool:
    """WebAuthn rpId must be a registrable domain (or 'localhost'). A raw IP is
    NOT a valid rpId — browsers reject the ceremony. Use a hostname/domain."""
    if host_only == "localhost":
        return True
    if _is_ip(host_only):
        return False
    return "." in host_only or host_only.endswith(".local") or host_only != ""


def _derive_rp_id(request_or_ws) -> str:
    if FORCE_RP_ID:
        return FORCE_RP_ID
    host = request_or_ws.headers.get("host", "localhost")
    return _host_only(host)


def _derive_origin(request_or_ws) -> str:
    if FORCE_ORIGIN:
        return FORCE_ORIGIN
    # prefer the Origin header (most reliable for WebAuthn verification)
    origin = request_or_ws.headers.get("origin")
    if origin:
        return origin.rstrip("/")
    host = request_or_ws.headers.get("host", "localhost:8080")
    # best-effort scheme guess
    scheme = "https" if request_or_ws.headers.get("x-forwarded-proto") == "https" else "http"
    return f"{scheme}://{host}"


# challenge cache (short-lived, in-memory)
_challenges: Dict[str, Dict[str, Any]] = {}
_chal_lock = threading.Lock()
_CHAL_TTL = 300  # 5 min


def _stash_challenge(kind: str, challenge: bytes, extra: Optional[dict] = None) -> str:
    cid = secrets.token_urlsafe(16)
    with _chal_lock:
        # prune expired
        now = time.time()
        for k in [k for k, v in _challenges.items() if now - v["t"] > _CHAL_TTL]:
            _challenges.pop(k, None)
        _challenges[cid] = {"kind": kind, "challenge": challenge, "t": now, "extra": extra or {}}
    return cid


def _pop_challenge(cid: str, kind: str) -> Dict[str, Any]:
    with _chal_lock:
        rec = _challenges.pop(cid, None)
    if not rec or rec["kind"] != kind:
        raise HTTPException(400, "invalid or expired challenge")
    if time.time() - rec["t"] > _CHAL_TTL:
        raise HTTPException(400, "challenge expired")
    return rec


# JWT sessions
def issue_token(subject: str, name: str = "") -> str:
    now = int(time.time())
    payload = {"sub": subject, "name": name, "iat": now, "exp": now + TOKEN_TTL}
    return jwt.encode(payload, _jwt_secret(), algorithm="HS256")


def verify_token(token: str) -> Dict[str, Any]:
    try:
        return jwt.decode(token, _jwt_secret(), algorithms=["HS256"])
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "session expired")
    except Exception:
        raise HTTPException(401, "invalid session")


def _extract_token(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    # cookie fallback
    tok = request.cookies.get("neon_session")
    if tok:
        return tok
    # query fallback (used by EventSource / some proxies)
    return request.query_params.get("token")


# FastAPI guards
def require_auth(request: Request) -> Dict[str, Any]:
    """Dependency / inline guard for HTTP routes. Raises 401 if not authed."""
    if not AUTH_ENABLED:
        return {"sub": "auth-disabled"}
    token = _extract_token(request)
    if not token:
        raise HTTPException(401, "authentication required")
    return verify_token(token)


def require_ws_auth(ws: WebSocket) -> Optional[Dict[str, Any]]:
    """Guard for WebSocket connect. Returns claims or None (caller closes)."""
    if not AUTH_ENABLED:
        return {"sub": "auth-disabled"}
    token = ws.query_params.get("token")
    if not token:
        # also allow subprotocol-style or header bearer
        auth = ws.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            token = auth[7:].strip()
    if not token:
        return None
    try:
        return verify_token(token)
    except HTTPException:
        return None


# WebAuthn ceremonies (called by route handlers in dashboard_server)
def begin_registration(request: Request, label: str = "admin passkey", bootstrap: str = "") -> Dict[str, Any]:
    """Start a passkey enrollment. The FIRST enrollment seals the rover.

    If credentials already exist, enrollment of additional passkeys requires
    a valid session (handled by the route). The very first enrollment can be
    gated by NEON_AUTH_BOOTSTRAP_TOKEN to prevent a stranger grabbing the
    open window between deploy and first login.
    """
    store = _load()
    first_time = len(store["credentials"]) == 0

    if first_time and BOOTSTRAP_TOKEN:
        if not secrets.compare_digest(bootstrap or "", BOOTSTRAP_TOKEN):
            raise HTTPException(403, "bootstrap token required for first enrollment")

    rp_id = _derive_rp_id(request)
    if not rpid_is_usable(rp_id):
        raise HTTPException(
            400,
            f"WebAuthn cannot use '{rp_id}' (a raw IP) as the relying-party id. "
            "Open the dashboard via a hostname instead (e.g. https://neon.local:PORT "
            "or a /etc/hosts entry), or set NEON_AUTH_RP_ID to a domain you control.",
        )
    # stable user handle (one admin identity; extra passkeys map to same user)
    user_id = store.get("user_id")
    if not user_id:
        user_id = bytes_to_base64url(secrets.token_bytes(16))
        store["user_id"] = user_id
        _save(store)

    exclude = [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["id"]))
        for c in store["credentials"]
    ]

    opts = generate_registration_options(
        rp_id=rp_id,
        rp_name=RP_NAME,
        user_id=base64url_to_bytes(user_id),
        user_name="neon-admin",
        user_display_name="NEON Admin",
        exclude_credentials=exclude or None,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED,
        ),
    )
    cid = _stash_challenge("reg", opts.challenge, {"label": label, "rp_id": rp_id})
    return {"challenge_id": cid, "options": json.loads(options_to_json(opts))}


def finish_registration(request: Request, challenge_id: str, credential: dict) -> Dict[str, Any]:
    rec = _pop_challenge(challenge_id, "reg")
    rp_id = rec["extra"]["rp_id"]
    origin = _derive_origin(request)

    verification = verify_registration_response(
        credential=credential,
        expected_challenge=rec["challenge"],
        expected_rp_id=rp_id,
        expected_origin=origin,
    )

    store = _load()
    cred_id = bytes_to_base64url(verification.credential_id)
    # avoid dup
    if any(c["id"] == cred_id for c in store["credentials"]):
        raise HTTPException(409, "credential already registered")
    store["credentials"].append({
        "id": cred_id,
        "public_key": bytes_to_base64url(verification.credential_public_key),
        "sign_count": verification.sign_count,
        "name": rec["extra"].get("label", "passkey"),
        "created": time.time(),
    })
    _save(store)

    token = issue_token(cred_id, name=rec["extra"].get("label", "passkey"))
    return {"ok": True, "token": token, "credential_id": cred_id}


def begin_authentication(request: Request) -> Dict[str, Any]:
    store = _load()
    if not store["credentials"]:
        raise HTTPException(400, "no credentials enrolled — setup required")
    rp_id = _derive_rp_id(request)
    if not rpid_is_usable(rp_id):
        raise HTTPException(
            400,
            f"WebAuthn cannot use '{rp_id}' (a raw IP) as the relying-party id. "
            "Open the dashboard via a hostname (https://neon.local:PORT or a hosts "
            "entry), or set NEON_AUTH_RP_ID to a domain.",
        )
    allow = [
        PublicKeyCredentialDescriptor(id=base64url_to_bytes(c["id"]))
        for c in store["credentials"]
    ]
    opts = generate_authentication_options(
        rp_id=rp_id,
        allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED,
    )
    cid = _stash_challenge("auth", opts.challenge, {"rp_id": rp_id})
    return {"challenge_id": cid, "options": json.loads(options_to_json(opts))}


def finish_authentication(request: Request, challenge_id: str, credential: dict) -> Dict[str, Any]:
    rec = _pop_challenge(challenge_id, "auth")
    rp_id = rec["extra"]["rp_id"]
    origin = _derive_origin(request)

    store = _load()
    cred_id = credential.get("id") or credential.get("rawId")
    match = next((c for c in store["credentials"] if c["id"] == cred_id), None)
    if not match:
        raise HTTPException(404, "unknown credential")

    verification = verify_authentication_response(
        credential=credential,
        expected_challenge=rec["challenge"],
        expected_rp_id=rp_id,
        expected_origin=origin,
        credential_public_key=base64url_to_bytes(match["public_key"]),
        credential_current_sign_count=match.get("sign_count", 0),
        require_user_verification=False,
    )

    # update replay-protection counter
    match["sign_count"] = verification.new_sign_count
    _save(store)

    token = issue_token(cred_id, name=match.get("name", "passkey"))
    return {"ok": True, "token": token, "credential_id": cred_id}


CLOCK_SANE_EPOCH = 1_700_000_000  # 2023-11; the Jetson boots at 1970 until NTP


def clock_synced() -> bool:
    """False while the wall clock still reads the 1970 boot default (no RTC battery)."""
    return time.time() > CLOCK_SANE_EPOCH


def _token_claims(tok: str) -> dict:
    try:
        return jwt.decode(tok, _jwt_secret(), algorithms=["HS256"], options={"verify_exp": False})
    except Exception:
        return {}


def service_token(name: str = "service") -> str:
    """Return a long-lived JWT for internal service-to-service calls
    (e.g. the thinker fetching camera frames from the dashboard). Stable
    across restarts: cached in the auth store under 'service_tokens'.

    This lets trusted co-located processes bypass the interactive passkey
    flow while still riding the same signed-JWT auth the middleware checks.

    A cached token is reused only while it verifies AND is not expired AND
    was not minted at a 1970 clock (iat before CLOCK_SANE_EPOCH); such a
    token decodes fine but every consumer gets 401 from the middleware.
    Minting refuses while the clock is unsynced, so the 1980-dated token
    cannot be produced again.
    """
    store = _load()
    toks = store.setdefault("service_tokens", {})
    if name in toks:
        claims = _token_claims(toks[name])
        if claims and float(claims.get("iat", 0)) >= CLOCK_SANE_EPOCH \
                and float(claims.get("exp", 0)) > time.time() + 60:
            return toks[name]
    if not clock_synced():
        raise RuntimeError(
            "refusing to mint a service token: the clock is not synced yet "
            f"(time.time()={int(time.time())}); wait for time-sync.target")
    now = int(time.time())
    payload = {"sub": f"service:{name}", "name": name, "iat": now,
               "exp": now + 3650 * 86400}  # ~10y
    tok = jwt.encode(payload, _jwt_secret(), algorithm="HS256")
    toks[name] = tok
    _save(store)
    return tok


def status(request=None) -> Dict[str, Any]:
    store = _load()
    out = {
        "enabled": AUTH_ENABLED,
        "setup_required": len(store["credentials"]) == 0,
        "credentials": list_credentials(),
        "bootstrap_required": bool(BOOTSTRAP_TOKEN) and len(store["credentials"]) == 0,
    }
    # warn if the current origin can't do WebAuthn (raw IP host, or insecure http)
    if request is not None:
        try:
            host = _host_only(request.headers.get("host", ""))
            origin = _derive_origin(request)
            secure = origin.startswith("https://") or host == "localhost"
            usable = rpid_is_usable(host) if not FORCE_RP_ID else True
            out["rp_id"] = FORCE_RP_ID or host
            out["secure_context"] = secure
            out["rpid_usable"] = usable
            if not secure:
                out["warning"] = ("This origin is not a secure context. WebAuthn needs "
                                  "HTTPS (or http://localhost). Set DASH_TLS=true.")
            elif not usable:
                out["warning"] = (f"'{host}' is a raw IP — WebAuthn needs a hostname/domain "
                                  "as rpId. Use https://<name>:PORT or set NEON_AUTH_RP_ID.")
        except Exception:
            pass
    return out
