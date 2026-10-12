"""Settings from ``SCANAMZN_*`` environment variables.

This lets the bot run with no config file at all, which is what the GitHub
Actions (phone-only) setup uses: the user fills in a few repository secrets and
the workflow passes them in as environment variables. When a config file is
also present, environment variables override it.

Empty values are ignored, because GitHub expands a secret that was never
created to an empty string.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

PREFIX = "SCANAMZN_"


class EnvConfigError(Exception):
    pass


# name (without prefix) -> (table, key, kind)
_SIMPLE: dict[str, tuple[str, str, str]] = {
    "ASINS": ("amazon", "asins", "words"),
    "SEARCH_QUERIES": ("amazon", "search_queries", "lines"),
    "DOMAIN": ("amazon", "domain", "str"),
    "PROXY": ("amazon", "proxy", "str"),
    "HTTP_BACKEND": ("amazon", "http_backend", "str"),
    "INTERVAL_SECONDS": ("monitor", "interval_seconds", "float"),
    "SEARCH_EVERY_CYCLES": ("monitor", "search_every_cycles", "int"),
    "MAX_DISCOVERED": ("monitor", "max_discovered", "int"),
    "REALERT_COOLDOWN_MINUTES": ("monitor", "realert_cooldown_minutes", "float"),
    "NOTIFY_WHEN_SOLD_OUT": ("monitor", "notify_when_sold_out", "bool"),
    "HEALTH_ALERT_AFTER_MINUTES": ("monitor", "health_alert_after_minutes", "float"),
    "DAILY_STATUS_HOUR": ("monitor", "daily_status_hour", "int"),
    "TIMEZONE": ("monitor", "timezone", "str"),
    "STATUS_FILE": ("monitor", "status_file", "str"),
    "HEARTBEAT_URL": ("monitor", "heartbeat_url", "str"),
    "DEBUG_DUMP_DIR": ("monitor", "debug_dump_dir", "str"),
    "MAX_MARKUP_PERCENT": ("filter", "max_markup_percent", "float"),
    "MAX_PRICE": ("filter", "max_price", "float"),
    "SMTP_HOST": ("email", "smtp_host", "str"),
    "SMTP_PORT": ("email", "smtp_port", "int"),
    "SMTP_SECURITY": ("email", "security", "str"),
    "SMTP_USER": ("email", "username", "str"),
    "SMTP_PASSWORD": ("email", "password", "secret"),
    "EMAIL_FROM": ("email", "from_addr", "str"),
    "EMAIL_TO": ("email", "to", "words"),
}
_WHATSAPP = (
    "WHATSAPP_PHONE",
    "CALLMEBOT_APIKEY",
    "TWILIO_ACCOUNT_SID",
    "TWILIO_AUTH_TOKEN",
    "TWILIO_FROM",
    "TWILIO_CONTENT_SID",
    "META_TOKEN",
    "META_PHONE_NUMBER_ID",
    "META_TEMPLATE",
    "META_TEMPLATE_LANGUAGE",
    "META_API_VERSION",
)
ALL_NAMES = tuple(PREFIX + n for n in (*_SIMPLE, *_WHATSAPP))


def _get(environ: Mapping[str, str], name: str) -> str:
    return (environ.get(PREFIX + name) or "").strip()


def _words(value: str) -> list[str]:
    return [w for w in re.split(r"[\s,;]+", value) if w]


def _convert(name: str, value: str, kind: str) -> Any:
    try:
        if kind == "int":
            return int(value)
        if kind == "float":
            return float(value)
    except ValueError:
        raise EnvConfigError(f"{PREFIX}{name} must be a number, got {value!r}") from None
    if kind == "bool":
        lowered = value.lower()
        if lowered in ("1", "true", "yes", "on"):
            return True
        if lowered in ("0", "false", "no", "off"):
            return False
        raise EnvConfigError(f"{PREFIX}{name} must be true or false, got {value!r}")
    if kind == "words":
        return _words(value)
    if kind == "lines":
        # Search queries contain spaces, so they are separated by ';' or newlines.
        return [q.strip() for q in re.split(r"[;\n]+", value) if q.strip()]
    if kind == "secret":
        # Google shows app passwords as "abcd efgh ijkl mnop"; spaces are not part of it.
        return value.replace(" ", "") if re.fullmatch(r"(?:[a-z]{4} ){3}[a-z]{4}", value) else value
    return value


def overlay(environ: Mapping[str, str], base: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Nested settings (same shape as config.toml) built from the environment.

    ``base`` is the parsed config file, if any.
    """
    data: dict[str, Any] = {}
    for name, (table, key, kind) in _SIMPLE.items():
        value = _get(environ, name)
        if value:
            data.setdefault(table, {})[key] = _convert(name, value, kind)

    if "email" in data:
        data["email"].setdefault("enabled", True)

    whatsapp = _whatsapp(environ, bool((base or {}).get("whatsapp")))
    if whatsapp:
        data["whatsapp"] = whatsapp
    return data


def _whatsapp(environ: Mapping[str, str], file_configures_whatsapp: bool) -> dict[str, Any]:
    if not any(_get(environ, n) for n in _WHATSAPP):
        return {}
    phones = _words(_get(environ, "WHATSAPP_PHONE"))
    if not phones:
        if file_configures_whatsapp:
            # The config file sets up WhatsApp and may reference these variables as ${...}.
            return {}
        raise EnvConfigError(f"{PREFIX}WHATSAPP_PHONE is not set (your number with country code, e.g. +972521234567)")

    if _get(environ, "CALLMEBOT_APIKEY"):
        keys = _words(_get(environ, "CALLMEBOT_APIKEY"))
        if len(keys) != len(phones):
            raise EnvConfigError(
                f"{PREFIX}CALLMEBOT_APIKEY has {len(keys)} key(s) but {PREFIX}WHATSAPP_PHONE has "
                f"{len(phones)} number(s); give one key per number, in the same order"
            )
        recipients = [{"phone": p, "apikey": k} for p, k in zip(phones, keys, strict=True)]
        return {"enabled": True, "provider": "callmebot", "callmebot": {"recipients": recipients}}

    if _get(environ, "TWILIO_ACCOUNT_SID"):
        twilio = {
            "account_sid": _get(environ, "TWILIO_ACCOUNT_SID"),
            "auth_token": _get(environ, "TWILIO_AUTH_TOKEN"),
            "from_number": _get(environ, "TWILIO_FROM"),
            "content_sid": _get(environ, "TWILIO_CONTENT_SID"),
            "to": phones,
        }
        return {"enabled": True, "provider": "twilio", "twilio": twilio}

    if _get(environ, "META_TOKEN"):
        meta: dict[str, Any] = {
            "access_token": _get(environ, "META_TOKEN"),
            "phone_number_id": _get(environ, "META_PHONE_NUMBER_ID"),
            "template_name": _get(environ, "META_TEMPLATE"),
            "to": phones,
        }
        for name, key in (("META_TEMPLATE_LANGUAGE", "template_language"), ("META_API_VERSION", "api_version")):
            if _get(environ, name):
                meta[key] = _get(environ, name)
        return {"enabled": True, "provider": "meta", "meta": meta}

    raise EnvConfigError(
        f"{PREFIX}WHATSAPP_PHONE is set but no WhatsApp provider key is: set {PREFIX}CALLMEBOT_APIKEY "
        f"(or {PREFIX}TWILIO_ACCOUNT_SID / {PREFIX}META_TOKEN)"
    )


def merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge two settings dicts; values in ``override`` win."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = merge(out[key], value)
        else:
            out[key] = value
    return out
