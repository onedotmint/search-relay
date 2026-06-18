import pytest

from app.security import (
    create_session_token,
    generate_relay_key,
    hash_secret,
    verify_secret,
    verify_session_token,
)


def test_hash_secret_verifies_and_rejects_wrong_value():
    hashed = hash_secret("correct-password")

    assert verify_secret("correct-password", hashed)
    assert not verify_secret("wrong-password", hashed)


def test_relay_key_generation_has_prefix_and_hashes():
    relay_key = generate_relay_key()
    hashed = hash_secret(relay_key)

    assert relay_key.startswith("relay_")
    assert verify_secret(relay_key, hashed)


def test_session_token_round_trip():
    token = create_session_token({"admin": True}, secret_key="secret")
    payload = verify_session_token(token, secret_key="secret")

    assert payload["admin"] is True


def test_session_token_rejects_wrong_secret():
    token = create_session_token({"admin": True}, secret_key="secret")

    with pytest.raises(ValueError):
        verify_session_token(token, secret_key="different")
