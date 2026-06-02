"""The parity-check store.

A "check" is one web-to-mobile parity comparison. Checks live in a JSON file
(checks.json by default) — the Phase 0 ethos is local files only, so we use JSON
rather than SQLite. The schema mirrors check_schema in prompt1.json.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field


@dataclass
class WebSpec:
    url: str
    selector: str | None = None
    target_description: str | None = None


@dataclass
class ApiSpec:
    endpoint: str | None = None
    json_path: str | None = None
    headers: dict = field(default_factory=dict)


@dataclass
class AppTarget:
    platform: str = "android"
    package: str | None = None
    goal: str = ""
    label: str = ""
    requires_build: str | None = None


@dataclass
class Check:
    name: str
    web: WebSpec
    api: ApiSpec
    app_targets: list[AppTarget] = field(default_factory=list)

    def android_target(self) -> AppTarget | None:
        for t in self.app_targets:
            if (t.platform or "android").lower() == "android":
                return t
        return self.app_targets[0] if self.app_targets else None

    @classmethod
    def from_dict(cls, d: dict) -> "Check":
        web = d.get("web", {}) or {}
        api = d.get("api", {}) or {}
        targets = [
            AppTarget(
                platform=t.get("platform", "android"),
                package=t.get("package"),
                goal=t.get("goal", ""),
                label=t.get("label", ""),
                requires_build=t.get("requires_build"),
            )
            for t in (d.get("app_targets") or [])
        ]
        return cls(
            name=d["name"],
            web=WebSpec(
                url=web.get("url", ""),
                selector=web.get("selector"),
                target_description=web.get("target_description"),
            ),
            api=ApiSpec(
                endpoint=api.get("endpoint"),
                json_path=api.get("json_path"),
                headers=api.get("headers") or {},
            ),
            app_targets=targets,
        )


def load_checks(path: str = "checks.json") -> list[Check]:
    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    if isinstance(data, dict) and "checks" in data:
        data = data["checks"]
    if not isinstance(data, list):
        raise ValueError("checks file must be a list (or an object with a 'checks' list)")
    return [Check.from_dict(c) for c in data]


def get_check(checks: list[Check], name: str) -> Check:
    for c in checks:
        if c.name == name:
            return c
    raise KeyError(f"no check named {name!r}. Available: {[c.name for c in checks]}")
