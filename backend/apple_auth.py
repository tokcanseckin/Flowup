"""Sign In with Apple identity token verification for SingoLing.

Verifies Apple ID tokens (JWTs) issued by Sign In with Apple using Apple's
published JWKS endpoint.  Requires PyJWT[crypto] (already in requirements.txt).

Environment variables required:
    APPLE_SIGN_IN_CLIENT_ID — the Service ID registered in Apple Developer
                               (e.g. "com.singoling.web").
"""

from __future__ import annotations

import json
import os

import httpx
import jwt as pyjwt
from jwt.algorithms import RSAAlgorithm

APPLE_SIGN_IN_CLIENT_ID = os.environ.get("APPLE_SIGN_IN_CLIENT_ID", "")

_APPLE_JWKS_URL = "https://appleid.apple.com/auth/keys"
_APPLE_ISSUER   = "https://appleid.apple.com"


async def verify_apple_id_token(id_token: str) -> dict:
    """Verify a Sign In with Apple identity token and return decoded claims.

    Raises ValueError if the token is invalid, expired, or the audience does
    not match APPLE_SIGN_IN_CLIENT_ID.

    Returns a dict with at least: sub (stable user ID).
    ``email`` and ``name`` are only present on the *first* sign-in.
    """
    if not APPLE_SIGN_IN_CLIENT_ID:
        raise ValueError(
            "APPLE_SIGN_IN_CLIENT_ID is not configured on the server. "
            "Set it to the Service ID registered in Apple Developer."
        )

    # Decode the header without verifying to find the key ID.
    try:
        header = pyjwt.get_unverified_header(id_token)
    except pyjwt.exceptions.DecodeError as exc:
        raise ValueError("Apple token is malformed") from exc

    kid = header.get("kid")
    if not kid:
        raise ValueError("Apple token header is missing 'kid'")

    # Fetch Apple's current public keys.
    async with httpx.AsyncClient() as client:
        r = await client.get(_APPLE_JWKS_URL, timeout=10)

    if not r.is_success:
        raise ValueError("Failed to fetch Apple's public keys — try again later")

    keys: list[dict] = r.json().get("keys", [])
    key_data = next((k for k in keys if k.get("kid") == kid), None)
    if key_data is None:
        raise ValueError("Apple public key not found for this token's 'kid'")

    public_key = RSAAlgorithm.from_jwk(json.dumps(key_data))

    try:
        claims: dict = pyjwt.decode(
            id_token,
            key=public_key,
            algorithms=["RS256"],
            audience=APPLE_SIGN_IN_CLIENT_ID,
            issuer=_APPLE_ISSUER,
        )
    except pyjwt.exceptions.ExpiredSignatureError as exc:
        raise ValueError("Apple token has expired") from exc
    except pyjwt.exceptions.InvalidAudienceError as exc:
        raise ValueError(
            "Apple token audience does not match this app's Service ID"
        ) from exc
    except pyjwt.exceptions.PyJWTError as exc:
        raise ValueError(f"Apple token verification failed: {exc}") from exc

    if not claims.get("sub"):
        raise ValueError("Apple token missing subject (sub)")

    return claims
