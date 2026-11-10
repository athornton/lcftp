"""Device token generation and hashing."""

import hashlib
import secrets

__all__ = ["generate_token", "hash_token"]

TOKEN_PREFIX = "lcftp_"


def generate_token() -> str:
    """Generate a new random device token."""
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """Hash a token for storage.

    Tokens carry 256 bits of entropy, so an unsalted SHA-256 is sufficient.
    """
    return hashlib.sha256(token.encode()).hexdigest()
