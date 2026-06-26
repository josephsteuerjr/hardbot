"""Тесты проверки подписи Telegram WebApp (web.validate_init_data): подделка/просрочка."""
import hashlib
import hmac
import json
import time
from urllib.parse import urlencode

import web

TOKEN = "123456:TEST-TOKEN-abcDEF"


def _make_init(token=TOKEN, user=None, auth_date=None):
    user = user or {"id": 42, "first_name": "Тест"}
    auth_date = auth_date if auth_date is not None else int(time.time())
    pairs = {"auth_date": str(auth_date), "query_id": "AAH",
             "user": json.dumps(user, separators=(",", ":"), ensure_ascii=False)}
    check = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, check.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


def test_valid_signature_accepted():
    data = web.validate_init_data(_make_init(), TOKEN)
    assert data and isinstance(data["user"], dict) and data["user"]["id"] == 42


def test_tampered_hash_rejected():
    raw = _make_init()
    tampered = raw[:-4] + ("0000" if not raw.endswith("0000") else "1111")
    assert web.validate_init_data(tampered, TOKEN) is None


def test_tampered_payload_rejected():
    raw = _make_init(user={"id": 42})
    # подменяем id на 999, не пересчитывая подпись → должно отвергнуться
    forged = raw.replace("%22id%22%3A42", "%22id%22%3A999")
    assert web.validate_init_data(forged, TOKEN) is None


def test_wrong_token_rejected():
    assert web.validate_init_data(_make_init(), "999:OTHER-TOKEN") is None


def test_expired_rejected():
    old = int(time.time()) - 48 * 3600
    assert web.validate_init_data(_make_init(auth_date=old), TOKEN, max_age=24 * 3600) is None


def test_missing_hash_rejected():
    assert web.validate_init_data("auth_date=1&user=%7B%7D", TOKEN) is None


def test_empty_rejected():
    assert web.validate_init_data("", TOKEN) is None
    assert web.validate_init_data(_make_init(), "") is None
