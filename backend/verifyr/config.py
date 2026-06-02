"""Configuration loading for the Verifyr mobile QA agent.

Two concerns live here:
  1. Loading prompt.json and turning its line-array fields into ready-to-send
     strings. The JSON schema is the contract; we validate the keys we depend on.
  2. Reading device / app / model settings from environment variables and
     assembling the Appium capabilities dictionary.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# Anchor file paths to the project, not the current working directory, so the CLI
# and server resolve prompts/.env/runs the same way regardless of where they run.
# Layout: <PROJECT_ROOT>/backend/verifyr/config.py
ENGINE_DIR = Path(__file__).resolve().parent          # backend/verifyr
BACKEND_DIR = ENGINE_DIR.parent                       # backend
PROJECT_ROOT = BACKEND_DIR.parent                     # repo root

try:
    # Optional: load .env (prefer the project root's) if present. Never required.
    from dotenv import load_dotenv

    _root_env = PROJECT_ROOT / ".env"
    load_dotenv(_root_env if _root_env.is_file() else None)
except Exception:  # pragma: no cover - dotenv is a convenience only
    pass


# Directories searched for the prompt config, in priority order. A local copy
# (CWD, backend/, repo root, or their idea/ subdir) overrides the canonical prompt
# bundled inside the package, so the app always has a working prompt to fall back on.
_SEARCH_BASES = (Path.cwd(), BACKEND_DIR, PROJECT_ROOT, ENGINE_DIR / "prompts")


# Fields in prompt.json that are stored as arrays of lines and must be joined.
_LINE_ARRAY_FIELDS = (
    "agent_system_prompt",
    "step_input_template",
    "verifier_system_prompt",
    "verifier_input_template",
)

# Where we look for the prompt config, in order, unless PROMPT_CONFIG is set.
# Phase 1 (prompt1) supersedes Phase 0 and carries all of its fields, so prefer it.
_PROMPT_CANDIDATES = (
    "prompt1.json",
    "idea/prompt1.json",
    "prompt.json",
    "prompt0.json",
    "idea/prompt0.json",
)

# Phase 1 annotates carried/new sections with marker lines that must not be sent
# to the model, e.g. "CARRIED FROM PHASE 0." or "NEW IN PHASE 1. <real guidance>".
_ANNOTATION_RE = re.compile(r"^(?:CARRIED FROM PHASE 0|NEW IN PHASE 1)\.\s*")


def _join_lines(value: Any) -> str:
    """Join a list-of-lines field into a single newline-delimited string.

    Strips Phase 1 annotation markers: a line that is *only* a marker is dropped;
    a line that begins with a marker keeps the remaining text.
    """
    if not isinstance(value, list):
        return str(value)
    out: list[str] = []
    for line in value:
        s = str(line)
        stripped = _ANNOTATION_RE.sub("", s)
        if stripped != s and stripped.strip() == "":
            continue  # the line was just a marker
        out.append(stripped)
    return "\n".join(out)


def _resolve_prompt_path() -> str:
    explicit = os.environ.get("PROMPT_CONFIG")
    if explicit:
        if not os.path.isfile(explicit):
            raise FileNotFoundError(f"PROMPT_CONFIG points to a missing file: {explicit}")
        return explicit
    # Try each candidate under each search base (CWD, backend/, project root).
    for base in _SEARCH_BASES:
        for candidate in _PROMPT_CANDIDATES:
            path = base / candidate
            if path.is_file():
                return str(path)
    raise FileNotFoundError(
        "Could not find a prompt config. Set PROMPT_CONFIG or place one of "
        + ", ".join(_PROMPT_CANDIDATES)
        + " under one of: "
        + ", ".join(str(b) for b in _SEARCH_BASES)
    )


@dataclass
class PromptConfig:
    """Parsed prompt.json with line-arrays already joined into strings."""

    raw: dict
    model: str
    temperature: float
    max_steps: int
    use_screenshot: bool
    use_accessibility_tree: bool
    agent_system_prompt: str
    step_input_template: str
    verifier_system_prompt: str
    verifier_input_template: str
    action_space: dict
    # Phase 1 additions (empty string / defaults when running a Phase 0 file).
    web_extractor_system_prompt: str = ""
    web_extractor_input_template: str = ""
    classifier_system_prompt: str = ""
    classifier_input_template: str = ""
    api_check_first: bool = False
    escalate_to_device: bool = True
    retry_on_stale: bool = False
    stale_retry_actions: list = field(default_factory=list)

    @property
    def is_phase1(self) -> bool:
        return bool(self.classifier_system_prompt)

    @classmethod
    def load(cls, path: str | None = None) -> "PromptConfig":
        path = path or _resolve_prompt_path()
        with open(path, "r", encoding="utf-8") as fh:
            raw = json.load(fh)

        for key in ("config", "agent_system_prompt", "step_input_template",
                    "verifier_system_prompt", "verifier_input_template", "action_space"):
            if key not in raw:
                raise ValueError(f"prompt config '{path}' is missing required key: {key}")

        cfg = raw["config"]
        return cls(
            raw=raw,
            model=str(cfg.get("model", "")),
            temperature=float(cfg.get("temperature", 0)),
            max_steps=int(cfg.get("max_steps", 25)),
            use_screenshot=bool(cfg.get("screenshot", True)),
            use_accessibility_tree=bool(cfg.get("accessibility_tree", True)),
            agent_system_prompt=_join_lines(raw["agent_system_prompt"]),
            step_input_template=_join_lines(raw["step_input_template"]),
            verifier_system_prompt=_join_lines(raw["verifier_system_prompt"]),
            verifier_input_template=_join_lines(raw["verifier_input_template"]),
            action_space=raw["action_space"],
            web_extractor_system_prompt=_join_lines(raw.get("web_extractor_system_prompt", [])),
            web_extractor_input_template=_join_lines(raw.get("web_extractor_input_template", [])),
            classifier_system_prompt=_join_lines(raw.get("classifier_system_prompt", [])),
            classifier_input_template=_join_lines(raw.get("classifier_input_template", [])),
            api_check_first=bool(cfg.get("api_check_first", False)),
            escalate_to_device=bool(cfg.get("escalate_to_device", True)),
            retry_on_stale=bool(cfg.get("retry_on_stale", False)),
            stale_retry_actions=list(cfg.get("stale_retry_actions", [])),
        )


def _env_bool(name: str, default: bool) -> bool:
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class Settings:
    """Runtime device / app / model settings sourced from the environment."""

    # VLM
    vlm_provider: str = "openai"
    vlm_model: str = "gpt-4o"
    openai_api_key: str | None = None
    anthropic_api_key: str | None = None
    temperature: float = 0.0
    max_tokens: int = 1024

    # Appium / device
    appium_server_url: str = "http://127.0.0.1:4723"
    platform_name: str = "Android"
    automation_name: str = "UiAutomator2"
    device_name: str = "Android Emulator"
    avd: str | None = None
    platform_version: str | None = None
    udid: str | None = None
    new_command_timeout: int = 120
    no_reset: bool = True
    auto_start_emulator: bool = True       # launch an existing AVD if no device is running
    emulator_boot_timeout: int = 180

    # App under test
    app_package: str | None = None
    app_activity: str | None = None
    app_path: str | None = None

    # Login automation (runs as a deterministic pre-step before the agent goal)
    login_flow: str | None = None          # e.g. "dev_impersonate"; None = no login
    impersonate_key: str | None = None     # secret, read from env, never sent to the model
    impersonate_user: str | None = None    # which user to pick; None = first in the list

    # Artifacts
    runs_dir: str = "runs"

    @classmethod
    def from_env(cls, prompt: PromptConfig) -> "Settings":
        provider = os.environ.get("VLM_PROVIDER", "openai").strip().lower()
        # Model precedence: explicit env override > prompt.json default per provider.
        default_model = "gpt-4o" if provider == "openai" else (prompt.model or "claude-opus-4-8")
        return cls(
            vlm_provider=provider,
            vlm_model=os.environ.get("VLM_MODEL", default_model),
            openai_api_key=os.environ.get("OPENAI_API_KEY"),
            anthropic_api_key=os.environ.get("ANTHROPIC_API_KEY"),
            temperature=float(os.environ.get("VLM_TEMPERATURE", prompt.temperature)),
            max_tokens=int(os.environ.get("VLM_MAX_TOKENS", "1024")),
            appium_server_url=os.environ.get("APPIUM_SERVER_URL", "http://127.0.0.1:4723"),
            device_name=os.environ.get("ANDROID_DEVICE_NAME", "Android Emulator"),
            avd=os.environ.get("ANDROID_AVD"),
            platform_version=os.environ.get("ANDROID_PLATFORM_VERSION"),
            udid=os.environ.get("ANDROID_UDID"),
            new_command_timeout=int(os.environ.get("APPIUM_NEW_COMMAND_TIMEOUT", "120")),
            no_reset=_env_bool("APPIUM_NO_RESET", True),
            auto_start_emulator=_env_bool("AUTO_START_EMULATOR", True),
            emulator_boot_timeout=int(os.environ.get("EMULATOR_BOOT_TIMEOUT", "180")),
            app_package=os.environ.get("APP_PACKAGE"),
            app_activity=os.environ.get("APP_ACTIVITY"),
            app_path=os.environ.get("APP_PATH"),
            login_flow=os.environ.get("LOGIN_FLOW"),
            impersonate_key=os.environ.get("IMPERSONATE_KEY"),
            impersonate_user=os.environ.get("IMPERSONATE_USER"),
            runs_dir=os.environ.get("RUNS_DIR", str(PROJECT_ROOT / "runs")),
        )

    def appium_capabilities(self) -> dict:
        """Build the W3C capabilities dict for UiAutomator2."""
        caps: dict[str, Any] = {
            "platformName": self.platform_name,
            "appium:automationName": self.automation_name,
            "appium:deviceName": self.device_name,
            "appium:newCommandTimeout": self.new_command_timeout,
            "appium:autoGrantPermissions": True,
            "appium:noReset": self.no_reset,
        }
        if self.avd:
            caps["appium:avd"] = self.avd
        if self.platform_version:
            caps["appium:platformVersion"] = self.platform_version
        if self.udid:
            caps["appium:udid"] = self.udid

        # App: prefer an apk path; otherwise launch an installed package/activity.
        if self.app_path:
            caps["appium:app"] = os.path.abspath(self.app_path)
        elif self.app_package:
            caps["appium:appPackage"] = self.app_package
            if self.app_activity:
                caps["appium:appActivity"] = self.app_activity
        return caps

    def validate_for_run(self) -> list[str]:
        """Return a list of human-readable problems that would block a run."""
        problems: list[str] = []
        if self.vlm_provider == "openai" and not self.openai_api_key:
            problems.append("OPENAI_API_KEY is not set.")
        if self.vlm_provider == "anthropic" and not self.anthropic_api_key:
            problems.append("ANTHROPIC_API_KEY is not set.")
        if not (self.app_path or self.app_package):
            problems.append("Set APP_PATH or APP_PACKAGE (with APP_ACTIVITY) for the app under test.")
        return problems


def load_all(prompt_path: str | None = None) -> tuple[PromptConfig, Settings]:
    prompt = PromptConfig.load(prompt_path)
    settings = Settings.from_env(prompt)
    return prompt, settings


if __name__ == "__main__":
    # Quick sanity check: python config.py
    p, s = load_all()
    print("Prompt loaded. max_steps =", p.max_steps, "| model in json =", p.model)
    print("VLM provider =", s.vlm_provider, "| model =", s.vlm_model)
    print("Capabilities:", json.dumps(s.appium_capabilities(), indent=2))
    issues = s.validate_for_run()
    print("Blocking issues:" if issues else "No blocking issues.")
    for issue in issues:
        print("  -", issue)
