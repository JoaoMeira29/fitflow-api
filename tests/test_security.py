from datetime import UTC, datetime, timedelta

import jwt
import pytest

from app.core.config import settings
from app.core.security import (
    JWT_ALGORITHM,
    create_access_token,
    decode_access_token,
    hash_password,
    is_common_password,
    verify_password,
)


def test_hash_is_not_the_plain_password():
    password_hash = hash_password("correct horse battery")

    assert password_hash != "correct horse battery"
    assert password_hash.startswith("$argon2")


def test_same_password_gives_different_hashes():
    # Each hash has its own random salt.
    assert hash_password("correct horse battery") != hash_password(
        "correct horse battery"
    )


def test_verify_password_accepts_the_right_password():
    password_hash = hash_password("correct horse battery")

    assert verify_password("correct horse battery", password_hash) is True


def test_verify_password_rejects_the_wrong_password():
    password_hash = hash_password("correct horse battery")

    assert verify_password("wrong password", password_hash) is False


def test_verify_password_rejects_a_malformed_hash():
    assert verify_password("anything", "not-a-real-hash") is False


def test_common_password_is_detected():
    assert is_common_password("password1234") is True


def test_common_password_check_ignores_case():
    assert is_common_password("PassWord1234") is True


def test_uncommon_password_is_not_flagged():
    assert is_common_password("correct horse battery") is False


def test_token_round_trip_returns_the_subject():
    token = create_access_token("user-123")

    assert decode_access_token(token) == "user-123"


def test_expired_token_is_rejected():
    expired = jwt.encode(
        {"sub": "user-123", "exp": datetime.now(UTC) - timedelta(minutes=1)},
        settings.JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )

    with pytest.raises(jwt.ExpiredSignatureError):
        decode_access_token(expired)


def test_token_signed_with_another_secret_is_rejected():
    forged = jwt.encode(
        {"sub": "user-123", "exp": datetime.now(UTC) + timedelta(minutes=5)},
        "a-different-secret-that-is-long-enough-for-hs256",
        algorithm=JWT_ALGORITHM,
    )

    with pytest.raises(jwt.InvalidSignatureError):
        decode_access_token(forged)


def test_token_without_subject_is_rejected():
    token = jwt.encode(
        {"exp": datetime.now(UTC) + timedelta(minutes=5)},
        settings.JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )

    with pytest.raises(jwt.MissingRequiredClaimError):
        decode_access_token(token)
