"""Deterministic mock provider.

Returns schema-valid JSON for every operation so the whole pipeline runs
with no external dependencies. Output is derived from a hash of the input
so it's stable across runs.

Test hooks (via context["_mock_mode"]):
  - "bad_json_repairable": wrap valid JSON in markdown fences + trailing
    comma so strict json.loads fails but repair_loads succeeds.
  - "bad_json_fatal": return text that can never be parsed as JSON.
"""
from __future__ import annotations

import hashlib
import json
import random
from typing import Any, Dict, List, Optional

from .base import BaseProvider
from .schemas import AIResponse

_HOOKS = [
    "他刚走进门，所有人都安静了。",
    "三秒后，整条街炸了。",
    "她以为今天只是普通的一天。",
    "那个被所有人嘲笑的少年，今天拿到了一封信。",
]
_CLIFFS = [
    "下一秒，门外传来了脚步声……",
    "可他不知道的是，真正的对手才刚刚登场。",
    "她举起电话，号码却怎么也按不下去。",
    "一切，都还远没有结束。",
]
_VISUALS = [
    "近景，主角侧脸，逆光剪影。",
    "中景，办公室落地窗，雨夜。",
    "特写，紧握的拳头微微颤抖。",
    "广角空镜，城市夜景航拍。",
]


def _rng(*parts: Any) -> random.Random:
    seed = hashlib.md5("|".join(str(p) for p in parts).encode()).hexdigest()
    return random.Random(int(seed[:8], 16))


class MockProvider(BaseProvider):
    name = "mock"

    def missing_config(self) -> List[str]:
        return []

    def generate_json(
        self,
        system: str,
        user: str,
        *,
        schema: Optional[Dict[str, Any]] = None,
        context: Optional[Dict[str, Any]] = None,
    ) -> AIResponse:
        ctx = context or {}
        op = ctx.get("_op", "")
        builder = _BUILDERS.get(op, _build_generic)
        obj = builder(ctx)
        content = json.dumps(obj, ensure_ascii=False)

        mode = ctx.get("_mock_mode")
        if mode == "bad_json_repairable":
            content = f"```json\n{json.dumps(obj, ensure_ascii=False)},\n```"
        elif mode == "bad_json_fatal":
            content = "sorry, I cannot do that {{{ this is not json"

        # Cheap deterministic token estimate.
        in_tok = max(1, len(system or "") // 4 + len(user or "") // 4)
        out_tok = max(1, len(content) // 4)
        return AIResponse(
            content=content,
            provider=self.name,
            model=self.model or "mock-1",
            input_tokens=in_tok,
            output_tokens=out_tok,
            latency_ms=1,
        )


# ---------------------------------------------------------------------
# Per-op builders — each returns a dict valid for its schema.
# ---------------------------------------------------------------------
def _build_outline(ctx: Dict[str, Any]) -> Dict[str, Any]:
    theme = ctx.get("theme") or "未知主题"
    return {
        "theme": theme,
        "genre": ctx.get("genre", ""),
        "style": ctx.get("style", ""),
        "logline": f"一个关于 {theme} 的 {ctx.get('genre') or '都市'} 故事，节奏 {ctx.get('style') or '快节奏'}。",
        "act_structure": [
            {"act": 1, "title": "建立与钩子", "summary": "主角登场，世界观确立，抛出核心悬念。"},
            {"act": 2, "title": "冲突与升级", "summary": "矛盾持续升级，关键反转出现。"},
            {"act": 3, "title": "高潮与回归", "summary": "决战与情感落点。"},
        ],
        "target_episodes": int(ctx.get("target_episodes") or 10),
    }


def _build_split(ctx: Dict[str, Any]) -> Dict[str, Any]:
    total = int(ctx.get("total") or 10)
    rng = _rng("split", ctx.get("outline_json", ""), total)
    episodes = []
    for i in range(1, total + 1):
        episodes.append({
            "episode_number": i,
            "title": f"第{i}集 · {rng.choice(_HOOKS)}",
            "summary": f"本集推进剧情，关键事件 #{i}。",
        })
    return {"episodes": episodes}


def _build_script(ctx: Dict[str, Any]) -> Dict[str, Any]:
    ep_no = ctx.get("episode_number", 1)
    title = ctx.get("episode_title") or f"第{ep_no}集"
    rng = _rng("script", ep_no, title)
    hook = rng.choice(_HOOKS)
    cliff = rng.choice(_CLIFFS)
    body = (
        f"【开场钩子】{hook}\n"
        f"【冲突展开】主角面对突发危机，节奏紧凑。\n"
        f"【转折】出现关键反转，悬念升级。\n"
        f"【悬念结尾】{cliff}\n"
    )
    return {
        "episode_title": title,
        "hook": hook,
        "script": body,
        "dialogues": [
            {"character": "主角", "line": "我不会就这样认输。"},
            {"character": "对手", "line": "那就走着瞧。"},
        ],
        "cliffhanger": cliff,
        "cover_text": f"{title} | 不看后悔",
        "tags": ["#爽文", "#反转", "#高能"],
        "risk_score": round(rng.uniform(5, 25), 1),
        "quality_score": round(rng.uniform(72, 92), 1),
        "notes": "mock 生成，节奏可再压缩。",
    }


def _build_storyboard(ctx: Dict[str, Any]) -> Dict[str, Any]:
    shots_n = int(ctx.get("shot_count") or 8)
    rng = _rng("sb", ctx.get("script_text", ""), shots_n)
    shots = []
    for i in range(1, shots_n + 1):
        shots.append({
            "shot_no": i,
            "duration_sec": round(rng.uniform(2, 5), 1),
            "visual": f"镜头{i}：{rng.choice(_VISUALS)}",
            "character_action": "主角进入画面，眼神坚定。",
            "narration": f"旁白第{i}句：紧张感持续累积。",
            "subtitle": f"字幕{i}",
            "sound_effect": rng.choice(["whoosh", "boom", "tick", ""]),
            "bgm_suggestion": "tense_loop_01" if i == 1 else "",
        })
    return {"shots": shots}


def _build_title_tags(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rng = _rng("tt", ctx.get("content", ""))
    return {
        "title": f"{rng.choice(_HOOKS)} | 全程高能",
        "tags": ["#短剧", "#爽文", "#反转", "#必看"],
        "cover_text": "看到结尾我哭了",
    }


def _build_review(ctx: Dict[str, Any]) -> Dict[str, Any]:
    content = ctx.get("content", "")
    rng = _rng("review", content)
    flags: List[str] = []
    # Trivial keyword heuristics so mock review is at least somewhat reactive.
    checks = {
        "涉政": ["政府", "领导人", "政变"],
        "涉黄": ["色情", "裸"],
        "暴力血腥": ["血腥", "砍杀", "爆头"],
        "平台敏感": ["赌博", "诈骗"],
    }
    for flag, words in checks.items():
        if any(w in content for w in words):
            flags.append(flag)
    risk = 80.0 if flags else round(rng.uniform(5, 20), 1)
    return {
        "risk_score": risk,
        "risk_flags": flags,
        "originality_notes": "未检测到明显仿写，建议人工复核关键桥段。",
        "rewrite_suggestion": "如命中风险项，建议弱化敏感表达后重写。" if flags else "",
    }


def _build_rewrite(ctx: Dict[str, Any]) -> Dict[str, Any]:
    original = ctx.get("original_script", "")
    instruction = ctx.get("instruction", "整体重写")
    rng = _rng("rewrite", original, instruction)
    return {
        "script": f"【改写·{instruction}】{rng.choice(_HOOKS)}\n{original}\n{rng.choice(_CLIFFS)}",
        "notes": f"按指令「{instruction}」重写。",
    }


def _build_enhance_hook(ctx: Dict[str, Any]) -> Dict[str, Any]:
    original = ctx.get("original_script", "")
    rng = _rng("hook", original)
    return {"script": f"【强化开场钩子】{rng.choice(_HOOKS)}\n\n{original}", "notes": "强化开场。"}


def _build_enhance_conflict(ctx: Dict[str, Any]) -> Dict[str, Any]:
    original = ctx.get("original_script", "")
    return {
        "script": f"{original}\n\n【冲突升级】反派出手，主角被逼入绝境，情绪推到顶点。",
        "notes": "升级中段冲突。",
    }


def _build_enhance_cliffhanger(ctx: Dict[str, Any]) -> Dict[str, Any]:
    original = ctx.get("original_script", "")
    rng = _rng("cliff", original)
    return {"script": f"{original}\n\n【悬念加强】{rng.choice(_CLIFFS)}", "notes": "强化结尾悬念。"}


def _build_generic(ctx: Dict[str, Any]) -> Dict[str, Any]:
    return {"result": "ok", "echo": (ctx.get("prompt") or "")[:120]}


_BUILDERS = {
    "generate_outline": _build_outline,
    "split_episodes": _build_split,
    "generate_script": _build_script,
    "generate_storyboard": _build_storyboard,
    "generate_title_tags": _build_title_tags,
    "review_content": _build_review,
    "rewrite_script": _build_rewrite,
    "enhance_hook": _build_enhance_hook,
    "enhance_conflict": _build_enhance_conflict,
    "enhance_cliffhanger": _build_enhance_cliffhanger,
}
