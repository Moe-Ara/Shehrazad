"""Errors that are safe to show to users (as translated messages, never raw)."""

from __future__ import annotations


class UserError(Exception):
    """A refusal or failure with a translated, user-facing explanation.

    kind: "error" | "warning" | "info" controls the embed colour.
    """

    def __init__(self, key: str, *, kind: str = "error", **params: object) -> None:
        super().__init__(key)
        self.key = key
        self.kind = kind
        self.params = params


class PremiumRequired(UserError):
    def __init__(self, feature_key: str) -> None:
        super().__init__("premium.gate_body", kind="premium", feature_key=feature_key)
        self.feature_key = feature_key
