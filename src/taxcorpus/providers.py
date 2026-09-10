"""Профили провайдера модели (P4 плана ПО) и выбор по уровню конфиденциальности дела (F9).

Профиль = адрес Anthropic-совместимого API + ключ + модель + флаги. Источники (по убыванию
приоритета): config/providers.json (в .gitignore; ключи — через имена переменных окружения,
не литералами), затем один профиль «default» из ANTHROPIC_API_KEY / ANTHROPIC_BASE_URL /
TAXCORPUS_MODEL (.env). Локальный профиль (location = local, например LiteLLM/vLLM с
Anthropic-совместимым входом) нужен для чувствительных дел: без него облачному провайдеру
чувствительное дело не уходит — маскируются персональные данные (redact.py), а если
TAXCORPUS_ALLOW_CLOUD_SENSITIVE не задан, запрос отклоняется.

config/providers.json:
{
  "default": "deepseek",
  "local":   "vllm",
  "profiles": {
    "deepseek": {"base_url": "https://api.deepseek.com/anthropic", "api_key_env": "DEEPSEEK_API_KEY",
                 "model": "deepseek-chat", "location": "cloud", "label": "DeepSeek (облако)"},
    "anthropic": {"base_url": null, "api_key_env": "ANTHROPIC_API_KEY", "model": "claude-opus-5",
                  "location": "cloud", "label": "Anthropic (облако)", "fallbacks": true},
    "vllm": {"base_url": "http://gpu-host:4000", "api_key_env": "LOCAL_LLM_KEY", "model": "qwen2.5-7b",
             "location": "local", "label": "локальная модель"}
  }
}
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from . import load_dotenv

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "providers.json"


class ProviderError(RuntimeError):
    pass


@dataclass(frozen=True)
class Provider:
    name: str
    model: str
    location: str                 # cloud | local
    label: str
    base_url: str | None = None
    api_key: str | None = None
    fallbacks: bool = False       # серверный фолбэк при отказе — только у Anthropic

    def badge(self) -> str:
        where = "локально" if self.location == "local" else "облако"
        return f"{self.label} · {self.model} · данные уходят: {where}"


def _from_env() -> Provider:
    base_url = os.environ.get("ANTHROPIC_BASE_URL") or None
    model = os.environ.get("TAXCORPUS_MODEL") or "claude-opus-5"
    if base_url and "deepseek" in base_url:
        label = "DeepSeek (облако)"
    elif base_url:
        label = f"совместимый API {base_url}"
    else:
        label = "Anthropic (облако)"
    location = "local" if base_url and ("localhost" in base_url or "127.0.0.1" in base_url) else "cloud"
    return Provider("default", model, location, label, base_url,
                    os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"),
                    fallbacks=not base_url)


def load_profiles(config_path: str | Path | None = None) -> tuple[dict[str, Provider], str, str | None]:
    """-> (профили, имя профиля по умолчанию, имя локального профиля или None)."""
    load_dotenv()
    path = Path(config_path) if config_path else CONFIG_PATH
    profiles: dict[str, Provider] = {}
    default_name, local_name = "default", None
    if path.exists():
        cfg = json.loads(path.read_text(encoding="utf-8"))
        for name, p in cfg.get("profiles", {}).items():
            profiles[name] = Provider(
                name=name, model=p["model"], location=p.get("location", "cloud"),
                label=p.get("label", name), base_url=p.get("base_url"),
                api_key=os.environ.get(p.get("api_key_env", "")) if p.get("api_key_env") else p.get("api_key"),
                fallbacks=bool(p.get("fallbacks", False)))
        default_name = cfg.get("default", next(iter(profiles), "default"))
        local_name = cfg.get("local")
    if "default" not in profiles and default_name not in profiles:
        profiles["default"] = _from_env()
        default_name = "default"
    if local_name is None:
        local_name = next((n for n, p in profiles.items() if p.location == "local"), None)
    default_name = os.environ.get("TAXCORPUS_PROVIDER", default_name)
    return profiles, default_name, local_name


def choose(confidentiality: str = "standard", requested: str | None = None,
           config_path: str | Path | None = None) -> Provider:
    """Профиль для дела: явный requested > локальный для sensitive > default."""
    profiles, default_name, local_name = load_profiles(config_path)
    if requested:
        if requested not in profiles:
            raise ProviderError(f"нет профиля провайдера «{requested}»; есть: {', '.join(profiles)}")
        return profiles[requested]
    if confidentiality == "sensitive":
        if local_name and local_name in profiles:
            return profiles[local_name]
        if not os.environ.get("TAXCORPUS_ALLOW_CLOUD_SENSITIVE"):
            raise ProviderError(
                "чувствительное дело: локальный профиль модели не настроен (config/providers.json, "
                "location = local). Разрешить облако с маскировкой персональных данных — "
                "TAXCORPUS_ALLOW_CLOUD_SENSITIVE=1")
    return profiles[default_name]


def make_client(provider: Provider, max_retries: int = 4):
    import anthropic
    kwargs = {"max_retries": max_retries}
    if provider.base_url:
        kwargs["base_url"] = provider.base_url
    if provider.api_key:
        kwargs["api_key"] = provider.api_key
    return anthropic.Anthropic(**kwargs)


def make_agent(corpus, provider: Provider, effort: str = "high", calendar=None):
    from .agent import TaxAgent
    agent = TaxAgent(make_client(provider), corpus, model=provider.model, effort=effort,
                     fallbacks=provider.fallbacks, calendar=calendar)
    agent.provider = provider  # type: ignore[attr-defined]
    return agent
