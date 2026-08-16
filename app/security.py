import base64
import hashlib
import hmac
import json
import secrets
import time
from typing import Any


HASH_ITERATIONS = 210_000


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(data: str) -> bytes:
    padding = "=" * (-len(data) % 4)
    return base64.urlsafe_b64decode(data + padding)


def hash_secret(secret_value: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", secret_value.encode("utf-8"), salt, HASH_ITERATIONS)
    return f"pbkdf2_sha256${HASH_ITERATIONS}${_b64(salt)}${_b64(digest)}"


def fingerprint_secret(secret_value: str) -> str:
    """Cheap deterministic fingerprint of a secret, used for indexed lookup.

    The fingerprint is NOT a verifier: the relay always confirms a matched
    row with PBKDF2 `verify_secret()`. It only turns the O(N) scan into an
    indexed lookup. Relay keys are 256-bit random tokens, so the unsalted
    SHA-256 fingerprint leaks nothing usable for offline guessing.
    """
    return "sha256$" + hashlib.sha256(secret_value.encode("utf-8")).hexdigest()


def verify_secret(secret_value: str, encoded_hash: str) -> bool:
    try:
        algorithm, iterations, salt_b64, digest_b64 = encoded_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        digest = hashlib.pbkdf2_hmac(
            "sha256",
            secret_value.encode("utf-8"),
            _unb64(salt_b64),
            int(iterations),
        )
        return hmac.compare_digest(_b64(digest), digest_b64)
    except Exception:
        return False


def generate_relay_key() -> str:
    return "relay_" + secrets.token_urlsafe(32)


def create_session_token(payload: dict[str, Any], secret_key: str, ttl_seconds: int = 86400) -> str:
    body = dict(payload)
    body["exp"] = int(time.time()) + ttl_seconds
    raw = json.dumps(body, separators=(",", ":"), sort_keys=True).encode("utf-8")
    body_b64 = _b64(raw)
    signature = hmac.new(secret_key.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256).digest()
    return f"{body_b64}.{_b64(signature)}"


def verify_session_token(token: str, secret_key: str) -> dict[str, Any]:
    try:
        body_b64, sig_b64 = token.split(".", 1)
        expected = hmac.new(secret_key.encode("utf-8"), body_b64.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(_b64(expected), sig_b64):
            raise ValueError("invalid signature")
        payload = json.loads(_unb64(body_b64))
        if int(payload.get("exp", 0)) < int(time.time()):
            raise ValueError("expired token")
        return payload
    except Exception as exc:
        raise ValueError("invalid session token") from exc
