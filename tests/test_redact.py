from proxy_why.redact import REDACTED, redact


def test_redacts_known_token_formats():
    text = ("aws AKIAABCDEFGHIJKLMNOP gh ghp_abcdefghijklmnopqrstuvwxyz0123456789 "
            "openai sk-proj-abcdefghijklmnopqrstuvwx slack xoxb-1234567890-abcdef")
    out, n = redact(text)
    assert n == 4
    assert "AKIA" not in out and "ghp_" not in out and "sk-proj" not in out and "xoxb" not in out


def test_redacts_secret_assignments_but_keeps_names():
    out, n = redact('DB_PASSWORD = "hunter2hunter2"\napi_key: abcdef123456')
    assert n == 2
    assert 'DB_PASSWORD = "' + REDACTED + '"' in out
    assert "api_key: " + REDACTED in out


def test_redacts_private_key_blocks():
    key = "-----BEGIN RSA PRIVATE KEY-----\nMIIEow\nabc\n-----END RSA PRIVATE KEY-----"
    out, n = redact(f"before\n{key}\nafter")
    assert n == 1 and "MIIEow" not in out and out.startswith("before")


def test_leaves_ordinary_text_alone():
    text = "We retry 3 times because the token bucket refills slowly."
    assert redact(text) == (text, 0)
