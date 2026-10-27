"""LLM 适配层:OpenAI 兼容协议(DeepSeek/GLM/Qwen/Kimi/OpenAI/Ollama 均可用)。

- 流式输出通过 on_delta 回调透传给 SSE;
- json_mode 优先使用 response_format,供应商不支持时自动降级重试;
- extract_json 负责从模型输出中稳健地抽取 JSON(容忍代码围栏/前后缀);
- 内置 mock 模式(base_url 以 mock:// 开头),离线跑通全流程,用于演示与测试。
"""
import json
import re
import time

from openai import OpenAI

from app.config import is_mock


class LLMError(RuntimeError):
    pass


def chat(profile, messages, json_mode=False, on_delta=None, max_attempts=3):
    if is_mock(profile):
        text = _mock_chat(messages, json_mode)
        if on_delta:
            for i in range(0, len(text), 24):
                on_delta(text[i:i + 24])
                time.sleep(0.015)
        return text
    api_key = profile.get("api_key")
    if not api_key:
        raise LLMError(f"模型档案「{profile.get('name')}」未配置 API Key:请在设置页填写,或切换到「离线演示」档案。")
    client = OpenAI(
        base_url=profile["base_url"],
        api_key=api_key,
        timeout=max(60, int(profile.get("timeout", 180))),
        max_retries=0,
    )
    last_err = None
    for attempt in range(max_attempts):
        kwargs = dict(
            model=profile.get("model", "gpt-4o-mini"),
            messages=messages,
            temperature=float(profile.get("temperature", 0.8)),
            max_tokens=int(profile.get("max_tokens", 8192)),
            stream=True,
        )
        use_json = json_mode
        try:
            if use_json:
                kwargs["response_format"] = {"type": "json_object"}
            try:
                return _collect(client, kwargs, on_delta)
            except Exception as e:
                # 部分供应商不支持 response_format,去掉后重试一次
                if "response_format" in kwargs:
                    kwargs.pop("response_format")
                    return _collect(client, kwargs, on_delta)
                raise
        except Exception as e:
            last_err = e
            if attempt < max_attempts - 1:
                time.sleep(1.5 * (attempt + 1))
    raise LLMError(f"模型调用失败:{last_err}")


def _collect(client, kwargs, on_delta):
    out = []
    stream = client.chat.completions.create(**kwargs)
    for chunk in stream:
        try:
            delta = chunk.choices[0].delta.content
        except Exception:
            delta = None
        if delta:
            out.append(delta)
            if on_delta:
                on_delta(delta)
    text = "".join(out).strip()
    if not text:
        raise LLMError("模型返回了空内容")
    return text


def extract_json(text):
    text = (text or "").strip()
    m = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    dec = json.JSONDecoder()
    for i, ch in enumerate(text):
        if ch in "{[":
            try:
                obj, _ = dec.raw_decode(text[i:])
                return obj
            except Exception:
                continue
    raise LLMError("模型未返回有效的 JSON,请重试或更换模型。")


# ---------------- mock 实现(离线演示/自动化测试) ----------------

_MOCK_REVIEW = {
    "overall": 78,
    "dimensions": {"情节推进": 75, "人物一致性": 82, "文笔表现": 76, "节奏张力": 74},
    "issues": [
        {"type": "逻辑", "severity": "中", "desc": "【模拟审校】主角转变的动机铺垫略薄,第三段情绪跳跃突兀。"},
        {"type": "重复", "severity": "低", "desc": "【模拟审校】「握紧了拳头」在两段中重复出现。"},
    ],
    "suggestions": [
        "【模拟】在冲突爆发前增加一次沉默对视,放大张力。",
        "【模拟】结尾钩子可改为更具悬念的反问或异象。",
    ],
}

_MOCK_STATE = {
    "characters": [
        {"name": "林辰", "role": "主角", "profile": "(模拟)性情坚韧的少年,背负旧誓", "status": "修为突破,获得关键信物"},
        {"name": "苏绾", "role": "女主", "profile": "(模拟)外冷内热的医女", "status": "身世线索浮现"},
    ],
    "foreshadowing": [
        {"title": "夜里发烫的玉佩", "description": "(模拟)玉佩在雷雨夜隐隐发烫,指向身世之谜", "action": "plant"},
    ],
}


def _mock_chat(messages, json_mode):
    user = ""
    if messages and messages[-1].get("content"):
        user = messages[-1]["content"]
    if json_mode:
        if "全书大纲" in user or "输出 JSON" in user and "章节大纲" in user:
            import re as _re2

            m2 = _re2.search(r"共\s*(\d+)\s*章", user)
            n = min(max(int(m2.group(1)) if m2 else 3, 1), 60)
            chs = [
                {
                    "idx": i,
                    "title": ["雷夜来客", "市集风云", "旧宅疑云", "初入修行", "身世浮现", "强敌现身"][min(i - 1, 5)] + (f"·{i}" if i > 6 else ""),
                    "summary": f"(模拟)第{i}章:主角为达成阶段目标而行动,遭遇阻碍,情节发生转折,关系出现新变化。",
                    "hook": "悬念收束",
                }
                for i in range(1, n + 1)
            ]
            return json.dumps(
                {
                    "volumes": [{"title": "第一卷·风起", "chapters": [1, min(10, n)], "summary": "(模拟)主角踏入修行界,卷入城中之乱。"}],
                    "chapters": chs,
                },
                ensure_ascii=False,
            )
        if "节拍" in user:
            return json.dumps(
                {"beats": [
                    "场景一:主角在市集察觉异动,与熟人交换情报。",
                    "场景二:夜探旧宅,发现与玉佩相关的刻痕。",
                    "场景三:强敌现身,初次交手落了下风,狼狈撤离。",
                    "场景四:疗伤时自省,立下阶段目标,收在悬念上。",
                ]},
                ensure_ascii=False,
            )
        if "审校" in user or "评审" in user:
            return json.dumps(_MOCK_REVIEW, ensure_ascii=False)
        if "核心角色" in user or ("characters" in user and "foreshadowing" not in user):
            return json.dumps({"characters": _MOCK_STATE["characters"]}, ensure_ascii=False)
        if "状态" in user or "伏笔" in user:
            return json.dumps(_MOCK_STATE, ensure_ascii=False)
        return json.dumps({"ok": True}, ensure_ascii=False)

    # 文本输出:按「约N字」的要求生成模拟正文/润色稿
    import re as _re

    m = _re.search(r"约\s*(\d{2,5})\s*字", user)
    target = min(int(m.group(1)) if m else 1200, 6000)
    paras = [
        "雨脚如麻,青石长街被夜色浸得发亮。檐角的灯笼在风里晃出昏黄的光,照见行人匆匆的侧影。",
        "他停在巷口,指腹摩挲着怀中那枚旧玉。玉身微温,像有心跳,又像只是错觉。远处的更鼓敲了三下,声音闷闷的,散在雨里。",
        "「你终究还是来了。」墙影里走出一个披蓑衣的人,斗笠压得很低,看不清面目,只露出一截苍白的下颌。",
        "他后退半步,手按上刀柄。雨声忽然大了,大得像是要把整条街的灯火都浇熄。两人对峙,谁都没有先动,唯有檐水一线一线地落,砸出细碎的白花。",
        "变故起于俄顷。蓑衣人袖中滑出一道寒光,快得几乎看不见轨迹。他侧身避过,肩头还是被划开一道血口,疼意这才后知后觉地涌上来。",
        "巷子尽头传来更夫的吆喝。蓑衣人收了手,退回黑暗里,只留下一句轻飘飘的话:「玉佩认主之日,就是你启程之时。」",
        "他靠着湿冷的墙滑坐下来,喘息渐渐平稳。掌心的玉又烫了几分,烫得像一句催促。",
    ]
    out, i, total = [], 0, 0
    while total < target:
        out.append(paras[i % len(paras)])
        total += len(paras[i % len(paras)])
        i += 1
    return "\n\n".join(out) + "\n\n【以上为离线演示模型的模拟正文,请在设置页配置真实模型后重新生成。】"
