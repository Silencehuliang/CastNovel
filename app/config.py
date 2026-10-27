"""全局配置:.env 默认值 + Web 设置页覆盖。

优先级:数据库中的设置(UI 修改)> .env 环境变量 > 内置默认值。
模型档案(profile)区分「强模型」(大纲/正文/审校)与「快模型」(摘要/状态抽取),
分别计费、分别调速,是控制成本的关键。
"""
import copy
import json
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)
WEB_DIR = BASE_DIR / "web"
DB_PATH = DATA_DIR / "novelforge.db"

load_dotenv(BASE_DIR / ".env")

DEFAULT_SETTINGS = {
    "profiles": [
        {
            "name": "默认模型",
            "base_url": "https://api.deepseek.com/v1",
            "api_key": "",
            "model": "deepseek-chat",
            "temperature": 0.85,
            "max_tokens": 8192,
        },
        {
            "name": "离线演示",
            "base_url": "mock://local",
            "api_key": "mock",
            "model": "mock-writer",
            "temperature": 0.7,
            "max_tokens": 4096,
        },
    ],
    "strong_profile": "默认模型",
    "fast_profile": "离线演示",
    "gen": {
        "chapter_words": 3000,
        "context_chars": 14000,
        "summary_window": 6,
        "retrieval_topk": 3,
    },
}


def _seed_env(settings: dict) -> None:
    """用 .env 中的变量填充默认档案,便于命令行/容器部署。"""
    base = os.getenv("LLM_BASE_URL")
    key = os.getenv("LLM_API_KEY")
    model = os.getenv("LLM_MODEL")
    if not (base or key or model):
        return
    p = settings["profiles"][0]
    if base:
        p["base_url"] = base
    if key:
        p["api_key"] = key
    if model:
        p["model"] = model


def load_settings(db) -> dict:
    """db: app.db 模块。返回合并后的完整设置。"""
    settings = copy.deepcopy(DEFAULT_SETTINGS)
    _seed_env(settings)
    saved = db.get_setting("settings")
    if saved:
        try:
            data = json.loads(saved)
            if isinstance(data, dict) and data.get("profiles"):
                settings = data
        except Exception:
            pass
    return settings


def save_settings(db, data: dict) -> dict:
    """校验并保存设置,返回规范化后的结果。"""
    merged = copy.deepcopy(DEFAULT_SETTINGS)
    if isinstance(data, dict):
        for k in ("profiles", "strong_profile", "fast_profile", "gen"):
            if k in data and data[k] is not None:
                merged[k] = data[k]
    names = [p.get("name", "") for p in merged["profiles"]]
    if merged["strong_profile"] not in names:
        merged["strong_profile"] = names[0] if names else ""
    if merged["fast_profile"] not in names:
        merged["fast_profile"] = names[-1] if names else ""
    db.set_setting("settings", json.dumps(merged, ensure_ascii=False))
    return merged


def find_profile(settings: dict, name: str):
    for p in settings.get("profiles", []):
        if p.get("name") == name:
            return p
    return None


def is_mock(profile: dict) -> bool:
    return str(profile.get("base_url", "")).startswith("mock")


def resolve_profile(settings: dict, role: str) -> dict:
    """按角色取档案;强/快档案若未配 Key 且不是 mock,自动降级到离线演示档案,
    保证未配置 API 时系统仍可完整体验(输出为模拟文本)。"""
    prof = find_profile(settings, settings.get(f"{role}_profile", "")) or (
        settings.get("profiles") or [{}]
    )[0]
    if not prof.get("api_key") and not is_mock(prof):
        for p in settings.get("profiles", []):
            if is_mock(p):
                return p
    return prof
