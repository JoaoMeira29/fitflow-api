from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

from app.core.config import settings

JWT_ALGORITHM = "HS256"

PASSWORD_MIN_LENGTH = 12
PASSWORD_MAX_LENGTH = 40

# Passwords of PASSWORD_MIN_LENGTH or more characters from the SecLists
# "xato-net-10-million-passwords-1000000" list, lowercased, one per line.
_COMMON_PASSWORDS_FILE = (
    Path(__file__).resolve().parent.parent / "data" / "common_passwords.txt"
)

_password_hasher = PasswordHasher()


@lru_cache
def _common_passwords() -> frozenset[str]:
    return frozenset(_COMMON_PASSWORDS_FILE.read_text(encoding="utf-8").splitlines())


def is_common_password(password: str) -> bool:
    """True if the password is on the common-password blocklist. The check
    ignores case, so "Password1234" is caught by "password1234"."""
    return password.casefold() in _common_passwords()


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return _password_hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def create_access_token(subject: str) -> str:
    """Create a signed JWT whose `sub` claim identifies the user."""
    now = datetime.now(UTC)
    payload = {
        "sub": subject,
        "iat": now,
        "exp": now + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES),
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_access_token(token: str) -> str:
    """Return the token's subject. Raises jwt.InvalidTokenError if the token
    is malformed, expired or signed with a different secret."""
    payload = jwt.decode(
        token,
        settings.JWT_SECRET,
        algorithms=[JWT_ALGORITHM],
        options={"require": ["sub", "exp"]},
    )
    return payload["sub"]
