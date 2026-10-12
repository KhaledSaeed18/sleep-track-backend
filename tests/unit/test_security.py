from __future__ import annotations

import base64
import json
import uuid
from datetime import timedelta
from typing import Any

import jwt
import pytest
from pydantic import SecretStr

from app.core.config import Settings
from app.core.errors import TokenExpiredError, TokenInvalidError
from app.core.passwords import (
    COMMON_PASSWORDS,
    MAX_LENGTH,
    MIN_LENGTH,
    password_problems,
    validate_password,
)
from app.core.security import (
    ACCESS_TOKEN_TYPE,
    AccessTokenClaims,
    PasswordHasher,
    TokenCodec,
    constant_time_equals,
    generate_token,
    get_password_hasher,
    hash_token,
    pkce_challenge,
)
from app.core.timeutils import utcnow

SECRET = "unit-secret-key-that-is-comfortably-longer-than-32-bytes"


def make_settings(**overrides: Any) -> Settings:
    values: dict[str, Any] = {
        "secret_key": SecretStr(SECRET),
        "access_token_ttl_seconds": 900,
        "argon2_time_cost": 1,
        "argon2_memory_cost": 1024,
        "argon2_parallelism": 1,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture
def codec() -> TokenCodec:
    return TokenCodec(make_settings())


@pytest.fixture(scope="module")
def hasher() -> PasswordHasher:
    return PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1)


def _issue(codec: TokenCodec, **kwargs: Any) -> tuple[str, AccessTokenClaims]:
    issued = codec.issue_access_token(
        user_id=kwargs.pop("user_id", uuid.uuid4()),
        session_id=kwargs.pop("session_id", uuid.uuid4()),
        role=kwargs.pop("role", "user"),
        **kwargs,
    )
    return issued.token, issued.claims


def _payload(**overrides: Any) -> dict[str, Any]:
    now = int(utcnow().timestamp())
    payload: dict[str, Any] = {
        "sub": str(uuid.uuid4()),
        "sid": str(uuid.uuid4()),
        "role": "user",
        "typ": ACCESS_TOKEN_TYPE,
        "jti": uuid.uuid4().hex,
        "iat": now,
        "nbf": now,
        "exp": now + 600,
        "iss": "sleeptrack-api",
        "aud": "sleeptrack-app",
    }
    payload.update(overrides)
    return {key: value for key, value in payload.items() if value is not None}


def _b64(data: dict[str, Any]) -> str:
    return base64.urlsafe_b64encode(json.dumps(data).encode()).rstrip(b"=").decode()


# =============================================================================== tokens
class TestTokenCodec:
    def test_roundtrip(self, codec: TokenCodec) -> None:
        user_id, session_id = uuid.uuid4(), uuid.uuid4()
        issued = codec.issue_access_token(user_id=user_id, session_id=session_id, role="admin")

        assert issued.expires_in == 900
        assert issued.claims.expires_at - issued.claims.issued_at == 900
        decoded = codec.decode_access_token(issued.token)
        assert decoded == issued.claims
        assert decoded.user_id == user_id
        assert decoded.session_id == session_id
        assert decoded.role == "admin"

    def test_payload_shape(self, codec: TokenCodec) -> None:
        token, claims = _issue(codec)
        assert jwt.get_unverified_header(token)["alg"] == "HS256"
        payload = jwt.decode(token, options={"verify_signature": False})
        assert payload == {
            "sub": str(claims.user_id),
            "sid": str(claims.session_id),
            "role": "user",
            "typ": "access",
            "jti": claims.jti,
            "iat": claims.issued_at,
            "nbf": claims.issued_at,
            "exp": claims.expires_at,
            "iss": "sleeptrack-api",
            "aud": "sleeptrack-app",
        }

    def test_each_token_has_unique_jti(self, codec: TokenCodec) -> None:
        assert _issue(codec)[1].jti != _issue(codec)[1].jti

    def test_configured_algorithm(self) -> None:
        codec = TokenCodec(make_settings(jwt_algorithm="HS512", secret_key=SecretStr(SECRET * 2)))
        token, claims = _issue(codec)
        assert jwt.get_unverified_header(token)["alg"] == "HS512"
        assert codec.decode_access_token(token) == claims

    def test_expired(self, codec: TokenCodec) -> None:
        token, _ = _issue(codec, now=utcnow() - timedelta(seconds=900 + 30))
        with pytest.raises(TokenExpiredError) as caught:
            codec.decode_access_token(token)
        assert caught.value.status_code == 401
        assert caught.value.code == "token_expired"

    def test_leeway_tolerates_small_clock_skew(self, codec: TokenCodec) -> None:
        token, claims = _issue(codec, now=utcnow() - timedelta(seconds=900 + 1))
        assert codec.decode_access_token(token) == claims

    def test_not_yet_valid(self, codec: TokenCodec) -> None:
        token, _ = _issue(codec, now=utcnow() + timedelta(minutes=5))
        with pytest.raises(TokenInvalidError):
            codec.decode_access_token(token)

    def test_tampered_payload(self, codec: TokenCodec) -> None:
        token, _ = _issue(codec)
        header, payload, signature = token.split(".")
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
        claims["role"] = "admin"
        with pytest.raises(TokenInvalidError):
            codec.decode_access_token(f"{header}.{_b64(claims)}.{signature}")

    def test_signed_with_another_key(self, codec: TokenCodec) -> None:
        other = TokenCodec(make_settings(secret_key=SecretStr("another-" + SECRET)))
        token, _ = _issue(other)
        with pytest.raises(TokenInvalidError):
            codec.decode_access_token(token)

    @pytest.mark.parametrize(
        "overrides", [{"jwt_audience": "another-app"}, {"jwt_issuer": "another-issuer"}]
    )
    def test_wrong_audience_or_issuer(self, codec: TokenCodec, overrides: dict[str, str]) -> None:
        token, _ = _issue(TokenCodec(make_settings(**overrides)))
        with pytest.raises(TokenInvalidError):
            codec.decode_access_token(token)

    def test_wrong_token_type(self, codec: TokenCodec) -> None:
        token = jwt.encode(_payload(typ="refresh"), SECRET, algorithm="HS256")
        with pytest.raises(TokenInvalidError, match="Unexpected token type"):
            codec.decode_access_token(token)

    @pytest.mark.parametrize("claim", ["exp", "iat", "sub", "sid", "jti", "typ"])
    def test_missing_required_claim(self, codec: TokenCodec, claim: str) -> None:
        token = jwt.encode(_payload(**{claim: None}), SECRET, algorithm="HS256")
        with pytest.raises(TokenInvalidError):
            codec.decode_access_token(token)

    @pytest.mark.parametrize("claim", ["sub", "sid"])
    def test_malformed_identifier(self, codec: TokenCodec, claim: str) -> None:
        token = jwt.encode(_payload(**{claim: "not-a-uuid"}), SECRET, algorithm="HS256")
        with pytest.raises(TokenInvalidError, match="invalid"):
            codec.decode_access_token(token)

    def test_role_defaults_to_user(self, codec: TokenCodec) -> None:
        token = jwt.encode(_payload(role=None), SECRET, algorithm="HS256")
        assert codec.decode_access_token(token).role == "user"

    def test_unsigned_token_is_rejected(self, codec: TokenCodec) -> None:
        token = f"{_b64({'alg': 'none', 'typ': 'JWT'})}.{_b64(_payload())}."
        with pytest.raises(TokenInvalidError):
            codec.decode_access_token(token)

    @pytest.mark.parametrize("token", ["", "garbage", "a.b.c"])
    def test_garbage(self, codec: TokenCodec, token: str) -> None:
        with pytest.raises(TokenInvalidError):
            codec.decode_access_token(token)


# =============================================================================== hashing
class TestPasswordHasher:
    def test_hash_and_verify(self, hasher: PasswordHasher) -> None:
        hashed = hasher.hash("correct horse 42")
        assert hashed.startswith("$argon2id$")
        assert hashed != hasher.hash("correct horse 42")  # salted
        assert hasher.verify("correct horse 42", hashed) == (True, None)

    def test_wrong_password(self, hasher: PasswordHasher) -> None:
        assert hasher.verify("wrong", hasher.hash("correct horse 42")) == (False, None)

    def test_missing_hash_still_does_the_work(
        self, hasher: PasswordHasher, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[str] = []
        original = hasher._hash.verify

        def spy(password: str, hashed: str) -> bool:
            calls.append(hashed)
            return original(password, hashed)

        monkeypatch.setattr(hasher._hash, "verify", spy)
        assert hasher.verify("anything", None) == (False, None)
        assert calls == [hasher._dummy_hash]

    @pytest.mark.parametrize("stored", ["", "not-a-hash", "$argon2id$v=19$broken", "$2b$12$abc"])
    def test_malformed_hash(self, hasher: PasswordHasher, stored: str) -> None:
        assert hasher.verify("password", stored) == (False, None)

    def test_outdated_parameters_return_new_hash(self, hasher: PasswordHasher) -> None:
        legacy = hasher.hash("correct horse 42")
        stronger = PasswordHasher(time_cost=2, memory_cost=2048, parallelism=1)

        valid, updated = stronger.verify("correct horse 42", legacy)

        assert valid is True
        assert updated is not None
        assert updated != legacy
        assert "m=2048,t=2,p=1" in updated
        assert stronger.verify("correct horse 42", updated) == (True, None)

    def test_burn(self, hasher: PasswordHasher) -> None:
        hasher.burn("whatever")

    def test_get_password_hasher_is_cached_per_parameters(self) -> None:
        first = get_password_hasher(make_settings())
        assert get_password_hasher(make_settings()) is first
        assert get_password_hasher(make_settings(argon2_memory_cost=2048)) is not first


# =============================================================================== opaque tokens
def test_generate_token() -> None:
    tokens = {generate_token() for _ in range(20)}
    assert len(tokens) == 20
    assert all(len(token) == 43 for token in tokens)
    assert len(generate_token(16)) == 22


def test_hash_token() -> None:
    assert hash_token("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
    assert hash_token("abc") == hash_token("abc")
    assert hash_token("abc") != hash_token("abd")


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("token", "token", True),
        ("token", "tokem", False),
        ("token", "token-longer", False),
        (None, "token", False),
        ("token", None, False),
        (None, None, False),
        ("", "", False),
    ],
)
def test_constant_time_equals(a: str | None, b: str | None, expected: bool) -> None:
    assert constant_time_equals(a, b) is expected


def test_pkce_challenge_rfc7636_vector() -> None:
    verifier = "dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk"
    assert pkce_challenge(verifier) == "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM"


# =============================================================================== password policy
class TestPasswordPolicy:
    @pytest.mark.parametrize("password", ["sleepy0wl", "a1" * 4, "x" * 127 + "1", "Ünïcødé9x"])
    def test_acceptable(self, password: str) -> None:
        assert password_problems(password) == []
        assert validate_password(password) == password

    @pytest.mark.parametrize(
        ("password", "problem"),
        [
            ("abc1234", f"must be between {MIN_LENGTH} and {MAX_LENGTH} characters"),
            ("a1" * 64 + "x", f"must be between {MIN_LENGTH} and {MAX_LENGTH} characters"),
            ("onlyletters", "must contain at least one letter and one digit"),
            ("1234567890", "must contain at least one letter and one digit"),
            ("Password123", "is too common; choose something less guessable"),
        ],
    )
    def test_single_problem(self, password: str, problem: str) -> None:
        assert password_problems(password) == [problem]

    def test_multiple_problems_are_joined(self) -> None:
        with pytest.raises(ValueError, match=r"^password must be between .*; must contain"):
            validate_password("short")

    def test_common_list_is_lowercase(self) -> None:
        assert all(entry == entry.lower() for entry in COMMON_PASSWORDS)

    @pytest.mark.parametrize("password", ["PASSWORD123", "SleepTrack1", "Qwerty123"])
    def test_common_check_ignores_case(self, password: str) -> None:
        assert "is too common; choose something less guessable" in password_problems(password)
