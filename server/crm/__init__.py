"""Isolated CRM pilot. Production routes remain disabled without explicit configuration."""

import os


def enabled():
    return os.environ.get("HARD_CRM_PILOT") == "1"
