"""Lot L1 du rapport V2 : version mobile (APP-46), URL Redis (APP-43),
clé du limiteur (APP-11), jetons uniques (APP-10)."""
from types import SimpleNamespace

from app.core.app_version import is_outdated, parse_version
from app.core.config import Settings
from app.core.rate_limit import client_key
from app.core.security import create_token, decode_token


class TestAppVersion:
    def test_parse(self):
        assert parse_version("1.2.3") == (1, 2, 3)
        assert parse_version("2.0") == (2, 0, 0)
        assert parse_version("1.4.0-beta+42") == (1, 4, 0)
        assert parse_version("") is None and parse_version(None) is None

    def test_outdated(self):
        assert is_outdated("1.9.9", "2.0.0")
        assert not is_outdated("2.0.0", "2.0.0")
        assert not is_outdated("2.1", "2.0.0")

    def test_missing_header_never_blocked(self):
        assert not is_outdated(None, "2.0.0")
        assert not is_outdated("illisible", "2.0.0")


class TestRedisUrl:
    def test_password_is_encoded(self):
        s = Settings(REDIS_URL="redis://redis:6379/0", REDIS_PASSWORD="a@b:c/d")
        assert s.redis_url == "redis://:a%40b%3Ac%2Fd@redis:6379/0"

    def test_no_password_unchanged(self):
        s = Settings(REDIS_URL="redis://redis:6379/0", REDIS_PASSWORD="")
        assert s.redis_url == "redis://redis:6379/0"

    def test_existing_userinfo_replaced(self):
        s = Settings(REDIS_URL="redis://:old@redis:6379/1", REDIS_PASSWORD="new")
        assert s.redis_url == "redis://:new@redis:6379/1"


def _request(auth: str | None, ip: str = "1.2.3.4"):
    headers = {"authorization": auth} if auth else {}
    return SimpleNamespace(headers=headers, client=SimpleNamespace(host=ip))


class TestRateLimitKey:
    def test_authenticated_user_key(self):
        tok = create_token("user-42", "access", "enseignant")
        assert client_key(_request(f"Bearer {tok}")) == "user:user-42"

    def test_refresh_token_not_used_as_identity(self):
        tok = create_token("user-42", "refresh", "enseignant")
        assert client_key(_request(f"Bearer {tok}")) == "ip:1.2.3.4"

    def test_anonymous_or_invalid_falls_back_to_ip(self):
        assert client_key(_request(None)) == "ip:1.2.3.4"
        assert client_key(_request("Bearer pas-un-jeton")) == "ip:1.2.3.4"


class TestUniqueTokens:
    def test_two_tokens_same_second_differ(self):
        a = create_token("u", "refresh", "enseignant")
        b = create_token("u", "refresh", "enseignant")
        assert a != b
        assert decode_token(a)["jti"] != decode_token(b)["jti"]
