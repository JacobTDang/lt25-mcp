"""Shared test doubles.

These live here rather than in conftest.py: pytest imports conftest under its
own module name, so importing it again as `tests.conftest` would create a
second copy of these classes with their own `instances` list, and assertions
would silently look at the wrong one.
"""

import json


class StubSession:
    """Stands in for a live amp session."""

    instances = []

    def __init__(self, preset_raw):
        self._raw = preset_raw
        self.calls = []
        self.closed = False
        StubSession.instances.append(self)

    def request(self, *, expect=None, timeout_ms=3000, **payload):
        kind = next(iter(payload))
        self.calls.append(kind)
        raw = self._raw
        outer = self

        class Reply:
            class presetJSONMessage:
                data = json.dumps(raw)
                slotIndex = payload.get("retrievePreset", {}).get("slot", 0)

            class auditionStateStatus:
                isAuditioning = "auditionPreset" in outer.calls and (
                    "exitAuditionPreset" not in outer.calls
                )

            class firmwareVersionStatus:
                version = "2.1.4"

            class productIdentificationStatus:
                id = "mustang-lt-25"

        return Reply

    def firmware_version(self):
        return "2.1.4"

    def product_id(self):
        return "mustang-lt-25"

    def open(self):
        return self

    def close(self):
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.closed = True


def call(tool):
    """FastMCP-style decorators keep the original function on .fn."""
    return getattr(tool, "fn", tool)
