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

# 供应商预设:设置页「从模板添加」一键填好 Base URL 与默认模型,用户只需粘贴 API Key。
# 全部走 OpenAI 兼容协议(Claude / Gemini 官方也提供 OpenAI 兼容端点);模型名可点「获取模型列表」拉取。
PROVIDER_PRESETS = [
    {"label": "DeepSeek 深度求索", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat",
     "key_url": "https://platform.deepseek.com/api_keys", "note": "便宜量足,中文写作性价比高"},
    {"label": "智谱 GLM", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-plus",
     "key_url": "https://open.bigmodel.cn/usercenter/apikeys", "note": "glm-4 系列中文语感好"},
    {"label": "通义千问 Qwen", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus",
     "key_url": "https://bailian.console.aliyun.com/", "note": "阿里百炼,兼容模式端点"},
    {"label": "月之暗面 Kimi", "base_url": "https://api.moonshot.cn/v1", "model": "kimi-k2-0905-preview",
     "key_url": "https://platform.moonshot.cn/console/api-keys", "note": "长上下文,写作流畅"},
    {"label": "OpenAI", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini",
     "key_url": "https://platform.openai.com/api-keys", "note": "需国际网络"},
    {"label": "Anthropic Claude", "base_url": "https://api.anthropic.com/v1", "model": "claude-sonnet-4-5",
     "key_url": "https://console.anthropic.com/", "note": "官方 OpenAI 兼容端点,长文质量佳"},
    {"label": "Google Gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta/openai", "model": "gemini-2.5-flash",
     "key_url": "https://aistudio.google.com/apikey", "note": "AI Studio 的 Key,OpenAI 兼容端点;网页版额度见 tools/gemini_bridge.py"},
    {"label": "OpenRouter 聚合", "base_url": "https://openrouter.ai/api/v1", "model": "",
     "key_url": "https://openrouter.ai/keys", "note": "一个 Key 调多家模型,添加后点「获取模型列表」选择"},
    {"label": "硅基流动 SiliconFlow", "base_url": "https://api.siliconflow.cn/v1", "model": "deepseek-ai/DeepSeek-V3",
     "key_url": "https://cloud.siliconflow.cn/account/ak", "note": "国内聚合,含 DeepSeek/Qwen 开源模型"},
    {"label": "火山方舟(豆包)", "base_url": "https://ark.cn-beijing.volces.com/api/v3", "model": "doubao-pro-32k",
     "key_url": "https://console.volcengine.com/ark", "note": "模型名填接入点 ID 或模型名"},
    {"label": "MiniMax", "base_url": "https://api.minimax.chat/v1", "model": "abab6.5s-chat",
     "key_url": "https://platform.minimaxi.com/", "note": ""},
    {"label": "Groq", "base_url": "https://api.groq.com/openai/v1", "model": "llama-3.3-70b-versatile",
     "key_url": "https://console.groq.com/keys", "note": "推理速度极快,适合快模型"},
    {"label": "Ollama 本地", "base_url": "http://127.0.0.1:11434/v1", "model": "",
     "key_url": "https://ollama.com/library", "note": "免 Key;先 ollama pull 模型,点「获取模型列表」选择"},
    {"label": "LM Studio 本地", "base_url": "http://127.0.0.1:1234/v1", "model": "",
     "key_url": "", "note": "免 Key;在 LM Studio 里启动本地服务后拉取模型列表"},
]

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
            "name": "Gemini 网页版",
            "base_url": "http://127.0.0.1:8765/v1",
            "api_key": "web",
            "model": "gemini-web",
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
    "embedding_profile": "",  # 可选:指向一个支持 embeddings 的档案,启用向量语义检索;空 = BM25
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
    if os.getenv("CASTNOVEL_FORCE_MOCK"):
        # e2e/CI 专用:强制全流程离线,绝不调用真实模型(保护额度)。启动服务前设置本变量。
        mock = next((p for p in settings.get("profiles", []) if is_mock(p)), None)
        if mock is None:
            mock = DEFAULT_SETTINGS["profiles"][-1]
        settings["profiles"] = [dict(mock)]
        settings["strong_profile"] = settings["profiles"][0]["name"]
        settings["fast_profile"] = settings["profiles"][0]["name"]
        if settings.get("embedding_profile") and settings["embedding_profile"] != settings["profiles"][0]["name"]:
            settings["embedding_profile"] = ""  # 指向 mock 档案时保留,便于离线测试向量检索
    return settings


def save_settings(db, data: dict) -> dict:
    """校验并保存设置,返回规范化后的结果。"""
    merged = copy.deepcopy(DEFAULT_SETTINGS)
    if isinstance(data, dict):
        for k in ("profiles", "strong_profile", "fast_profile", "embedding_profile", "gen"):
            if k in data and data[k] is not None:
                merged[k] = data[k]
    names = [p.get("name", "") for p in merged["profiles"]]
    if merged["strong_profile"] not in names:
        merged["strong_profile"] = names[0] if names else ""
    if merged["fast_profile"] not in names:
        merged["fast_profile"] = names[-1] if names else ""
    if merged.get("embedding_profile") not in names:
        merged["embedding_profile"] = ""
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
