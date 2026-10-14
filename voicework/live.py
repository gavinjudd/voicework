"""The single list of generation providers. Import from here instead of repeating names."""
LIVE_PROVIDERS = ("codex", "anthropic", "claude")
PROVIDER_MODES = ("offline",) + LIVE_PROVIDERS
PROVIDER_LABELS = {"offline": "Offline rehearsal", "codex": "Codex", "anthropic": "Anthropic API", "claude": "Claude"}


def is_live(mode):
    return mode in LIVE_PROVIDERS


def provider_check_sql():
    return "CHECK(provider IN (%s))" % ",".join("'%s'" % mode for mode in PROVIDER_MODES)
