import pytest

from scanamzn.config import ConfigError, build_notifiers, load
from scanamzn.envconfig import ALL_NAMES, EnvConfigError, merge, overlay

PHONE_ONLY = {
    "SCANAMZN_SMTP_USER": "me@gmail.com",
    "SCANAMZN_SMTP_PASSWORD": "abcd efgh ijkl mnop",
    "SCANAMZN_WHATSAPP_PHONE": "+919876543210",
    "SCANAMZN_CALLMEBOT_APIKEY": "123456",
}


def test_env_only_config_without_file(tmp_path):
    s = load(tmp_path / "missing.toml", environ=PHONE_ONLY)
    notifiers = build_notifiers(s)
    email, whatsapp = notifiers
    assert email.to_addrs == ["me@gmail.com"]  # alerts go to yourself by default
    assert email.from_addr == "me@gmail.com"
    assert email.password == "abcdefghijklmnop"  # Google shows app passwords with spaces
    assert whatsapp.phone == "+919876543210" and whatsapp.apikey == "123456"
    assert s.search_queries  # defaults still apply


def test_missing_file_and_no_env_is_an_error(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load(tmp_path / "missing.toml", environ={})


def test_empty_values_are_ignored(tmp_path):
    # Docker/CI tooling often passes unset secrets as empty strings.
    cfg = tmp_path / "config.toml"
    cfg.write_text("")
    env = {name: "" for name in ALL_NAMES} | {"SCANAMZN_ASINS": "b0abcdefgh, B0ABCDEFGJ"}
    s = load(cfg, environ=env)
    assert s.asins == ["B0ABCDEFGH", "B0ABCDEFGJ"]
    assert build_notifiers(s) == []


def test_env_overrides_file(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text('[amazon]\nasins = ["B0AAAAAAAA"]\n[monitor]\ninterval_seconds = 90\n')
    s = load(cfg, environ={"SCANAMZN_INTERVAL_SECONDS": "60", "SCANAMZN_DAILY_STATUS_HOUR": "9"})
    assert s.asins == ["B0AAAAAAAA"]
    assert s.interval_seconds == 60 and s.daily_status_hour == 9


def test_search_queries_split_on_semicolons():
    data = overlay({"SCANAMZN_SEARCH_QUERIES": "playstation gift card; psn wallet\nps store card"})
    assert data["amazon"]["search_queries"] == ["playstation gift card", "psn wallet", "ps store card"]


@pytest.mark.parametrize(
    "env, message",
    [
        ({"SCANAMZN_INTERVAL_SECONDS": "fast"}, "must be a number"),
        ({"SCANAMZN_NOTIFY_WHEN_SOLD_OUT": "maybe"}, "true or false"),
        ({"SCANAMZN_CALLMEBOT_APIKEY": "1"}, "WHATSAPP_PHONE is not set"),
        ({"SCANAMZN_WHATSAPP_PHONE": "+91987"}, "no WhatsApp provider"),
        ({"SCANAMZN_WHATSAPP_PHONE": "+911, +912", "SCANAMZN_CALLMEBOT_APIKEY": "1"}, "one key per number"),
    ],
)
def test_env_errors(env, message):
    with pytest.raises(EnvConfigError, match=message):
        overlay(env)


def test_callmebot_key_used_as_placeholder_by_config_file(tmp_path):
    cfg = tmp_path / "config.toml"
    cfg.write_text(
        '[whatsapp]\nenabled = true\nprovider = "callmebot"\n[whatsapp.callmebot]\n'
        'recipients = [{ phone = "+919999999999", apikey = "${SCANAMZN_CALLMEBOT_APIKEY}" }]\n'
    )
    import os

    os.environ["SCANAMZN_CALLMEBOT_APIKEY"] = "777"
    try:
        s = load(cfg, environ={"SCANAMZN_CALLMEBOT_APIKEY": "777"})
        (n,) = build_notifiers(s)
        assert n.phone == "+919999999999" and n.apikey == "777"
    finally:
        del os.environ["SCANAMZN_CALLMEBOT_APIKEY"]


def test_twilio_and_meta_from_env():
    tw = overlay({"SCANAMZN_WHATSAPP_PHONE": "+911", "SCANAMZN_TWILIO_ACCOUNT_SID": "AC1",
                  "SCANAMZN_TWILIO_AUTH_TOKEN": "t", "SCANAMZN_TWILIO_FROM": "+1415"})
    assert tw["whatsapp"]["provider"] == "twilio" and tw["whatsapp"]["twilio"]["to"] == ["+911"]
    meta = overlay({"SCANAMZN_WHATSAPP_PHONE": "+911", "SCANAMZN_META_TOKEN": "x",
                    "SCANAMZN_META_PHONE_NUMBER_ID": "5", "SCANAMZN_META_API_VERSION": "v26.0"})
    assert meta["whatsapp"]["meta"]["api_version"] == "v26.0"


def test_merge_is_deep():
    assert merge({"a": {"x": 1, "y": 2}, "b": 1}, {"a": {"y": 3}}) == {"a": {"x": 1, "y": 3}, "b": 1}


def test_missing_file_with_only_placeholder_secrets_says_file_not_found(tmp_path):
    # README quick start: secrets exported for ${...} placeholders, but a wrong -c path.
    with pytest.raises(ConfigError, match="missing.toml not found"):
        load(tmp_path / "missing.toml", environ={"SCANAMZN_SMTP_PASSWORD": "pw"})
    with pytest.raises(ConfigError, match="missing.toml not found.*incomplete"):
        load(tmp_path / "missing.toml", environ={"SCANAMZN_CALLMEBOT_APIKEY": "1"})
