"""Minimal i18n: JSON translation files, RTL-aware language metadata."""
import json
import os

LOCALES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "locales")
SUPPORTED = ["ku", "ar", "en"]
RTL = {"ku", "ar"}

_cache = {}


def load(lang: str) -> dict:
    if lang not in SUPPORTED:
        lang = "ku"
    if lang not in _cache:
        path = os.path.join(LOCALES_DIR, f"{lang}.json")
        with open(path, encoding="utf-8") as f:
            _cache[lang] = json.load(f)
    return _cache[lang]


def t(lang: str, key: str, **kwargs) -> str:
    d = load(lang)
    val = d.get(key)
    if val is None:
        val = load("en").get(key, key)
    if kwargs:
        try:
            val = val.format(**kwargs)
        except Exception:
            pass
    return val


def is_rtl(lang: str) -> bool:
    return lang in RTL
