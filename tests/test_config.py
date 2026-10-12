from pathlib import Path

import pytest

from scanamzn.config import ConfigError, build_notifiers, from_dict, load


def test_defaults():
    s = from_dict({})
    assert s.domain == "www.amazon.in"
    assert s.search_queries and s.asins == []
    assert s.interval_seconds == 45 and s.max_markup_percent == 10
    assert build_notifiers(s) == []


def test_example_config_parses(monkeypatch):
    monkeypatch.setenv("SCANAMZN_SMTP_PASSWORD", "app-pass")
    monkeypatch.setenv("SCANAMZN_CALLMEBOT_APIKEY", "123456")
    s = load(Path(__file__).parent.parent / "config.example.toml")
    names = [n.name for n in build_notifiers(s)]
    assert names[0] == "email" and names[1].startswith("whatsapp/callmebot")


def test_asins_validated_and_deduplicated():
    s = from_dict({"amazon": {"asins": ["b0abcdefgh", "B0ABCDEFGH"]}})
    assert s.asins == ["B0ABCDEFGH"]
    with pytest.raises(ConfigError, match="ASIN"):
        from_dict({"amazon": {"asins": ["not-an-asin"]}})


def test_rejects_too_fast_polling_and_bad_types():
    with pytest.raises(ConfigError, match="at least 10"):
        from_dict({"monitor": {"interval_seconds": 2}})
    with pytest.raises(ConfigError, match="wrong type"):
        from_dict({"monitor": {"interval_seconds": "fast"}})
    with pytest.raises(ConfigError, match="nothing to watch"):
        from_dict({"amazon": {"search_queries": []}})


def test_env_placeholders_only_resolved_when_used(monkeypatch):
    monkeypatch.delenv("NOPE", raising=False)
    data = {
        "whatsapp": {
            "enabled": True,
            "provider": "callmebot",
            "callmebot": {"recipients": [{"phone": "+91987", "apikey": "${KEY}"}]},
            "twilio": {"auth_token": "${NOPE}"},
        }
    }
    monkeypatch.setenv("KEY", "secret")
    (n,) = build_notifiers(from_dict(data))
    assert n.apikey == "secret"

    monkeypatch.delenv("KEY")
    with pytest.raises(ConfigError, match=r"\$\{KEY\}"):
        build_notifiers(from_dict(data))


def test_twilio_and_meta_build_one_notifier_per_recipient():
    tw = {"enabled": True, "provider": "twilio",
          "twilio": {"account_sid": "AC1", "auth_token": "t", "from_number": "+1", "to": ["+911", "+912"]}}
    assert len(build_notifiers(from_dict({"whatsapp": tw}))) == 2
    meta = {"enabled": True, "provider": "meta",
            "meta": {"access_token": "t", "phone_number_id": "1", "to": ["911"]}}
    assert len(build_notifiers(from_dict({"whatsapp": meta}))) == 1


def test_whatsapp_misconfiguration():
    with pytest.raises(ConfigError, match="provider"):
        build_notifiers(from_dict({"whatsapp": {"enabled": True, "provider": "sms"}}))
    with pytest.raises(ConfigError, match="no numbers"):
        build_notifiers(from_dict({"whatsapp": {"enabled": True, "provider": "meta", "meta": {}}}))
    with pytest.raises(ConfigError, match="required"):
        build_notifiers(from_dict({"whatsapp": {"enabled": True, "provider": "meta", "meta": {"to": ["91"]}}}))


def test_wrong_type_error_hides_secret_values():
    with pytest.raises(ConfigError) as info:
        build_notifiers(from_dict({"email": {"enabled": True, "password": 123456789}}))
    assert "123456789" not in str(info.value) and "(hidden)" in str(info.value)
