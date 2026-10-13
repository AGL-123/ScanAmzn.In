from pathlib import Path

import pytest

from scanamzn.config import ConfigError, build_notifiers, from_dict, load


def test_defaults():
    s = from_dict({})
    assert s.domain == "www.amazon.in"
    assert s.search_queries and s.asins == ["B07K6RYVHR", "B093QF35KZ", "B07K6RYVJ5", "B0CYNZJLBC"]  # known listings
    assert s.interval_seconds == 45 and s.max_markup_percent == 10
    assert build_notifiers(s) == []


def test_example_config_parses(monkeypatch):
    monkeypatch.setenv("SCANAMZN_SMTP_PASSWORD", "app-pass")
    monkeypatch.setenv("SCANAMZN_CALLMEBOT_APIKEY", "123456")
    s = load(Path(__file__).parent.parent / "config.example.toml")
    names = [n.name for n in build_notifiers(s)]
    assert names[0] == "email" and names[1].startswith("whatsapp/callmebot")


def test_asins_validated_and_deduplicated():
    s = from_dict({"amazon": {"asins": ["b0abcdefgh", "B0ABCDEFGH"], "watch_known_listings": False}})
    assert s.asins == ["B0ABCDEFGH"]
    with pytest.raises(ConfigError, match="ASIN"):
        from_dict({"amazon": {"asins": ["not-an-asin"]}})


def test_rejects_too_fast_polling_and_bad_types():
    with pytest.raises(ConfigError, match="at least 10"):
        from_dict({"monitor": {"interval_seconds": 2}})
    with pytest.raises(ConfigError, match="wrong type"):
        from_dict({"monitor": {"interval_seconds": "fast"}})
    with pytest.raises(ConfigError, match="nothing to watch"):
        from_dict({"amazon": {"search_queries": [], "watch_known_listings": False}})


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


def test_fetcher_sends_a_current_consistent_chrome_identity(monkeypatch):
    from datetime import date

    import requests

    from scanamzn import fetcher as fetcher_mod

    assert fetcher_mod.chrome_major(date(2025, 9, 2)) == 140
    assert 150 <= fetcher_mod.chrome_major(date(2026, 10, 12)) <= 153
    # Brand lists real Chrome sent (decoy brand and order depend on the version).
    assert fetcher_mod._sec_ch_ua(120) == '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"'
    assert fetcher_mod._sec_ch_ua(124) == '"Chromium";v="124", "Google Chrome";v="124", "Not-A.Brand";v="99"'
    assert fetcher_mod._sec_ch_ua(131) == '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"'
    for rotation in range(3):
        h = fetcher_mod.chrome_headers(rotation, date(2026, 10, 12))
        major = str(fetcher_mod.chrome_major(date(2026, 10, 12)))
        assert f"Chrome/{major}.0.0.0" in h["User-Agent"] and f'"Chromium";v="{major}"' in h["sec-ch-ua"]
        platform = h["sec-ch-ua-platform"].strip('"')
        assert {"Windows": "Windows NT", "macOS": "Mac OS X", "Linux": "Linux"}[platform] in h["User-Agent"]

    # The client-hints profile makes no warm-up request of its own (recorded,
    # not raised - the warm-up swallows exceptions).
    calls = []
    monkeypatch.setattr(requests.Session, "request", lambda self, *a, **k: calls.append(a))
    f = fetcher_mod.Fetcher(backend="requests")
    f.profile_index = fetcher_mod.PROFILES.index("client-hints")
    session = f._new_session()
    assert calls == [] and session.headers["sec-ch-ua-mobile"] == "?0"


def test_focus_listings_by_default():
    from scanamzn.config import FOCUS_LISTINGS, from_dict

    s = from_dict({})
    assert s.focus_asins == list(FOCUS_LISTINGS) == ["B07K6RYVJ5", "B093QF35KZ"]
    assert s.focus_interval_seconds == 30
    mine = from_dict({"amazon": {"asins": ["b0mine0001"]}})
    assert mine.focus_asins == ["B07K6RYVJ5", "B093QF35KZ", "B0MINE0001"]  # the user's own are kept
    assert from_dict({"amazon": {"watch_known_listings": False, "asins": ["B0MINE0001"]}}).focus_asins == []
    assert from_dict({"amazon": {"focus_asins": ["B0CYNZJLBC"]}}).focus_asins == ["B0CYNZJLBC"]
    with pytest.raises(ConfigError):
        from_dict({"amazon": {"focus_asins": ["nope"]}})
    with pytest.raises(ConfigError):
        from_dict({"monitor": {"focus_interval_seconds": 5}})
