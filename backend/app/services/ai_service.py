"""
AI service abstraction.

Switch via AI_PROVIDER env var. Currently:
- 'mock' (default): returns deterministic placeholder content so the whole pipeline runs.
- 'openai' / 'anthropic': stubs to fill in.

All methods are sync to keep things simple in MVP; wrap in Celery tasks for real calls.
"""
import random
import hashlib
from typing import List, Dict, Any, Optional
from app.config import settings


# ---------- Provider base ----------

class AIProvider:
    def chat(self, system: str, user: str, **kw) -> str:
        raise NotImplementedError


class MockProvider(AIProvider):
    def chat(self, system: str, user: str, **kw) -> str:
        # Deterministic-ish content based on input hash
        h = hashlib.md5((system + user).encode()).hexdigest()[:6]
        return f"[mock-{h}] {user[:80]}"


class OpenAIProvider(AIProvider):
    def chat(self, system: str, user: str, **kw) -> str:
        # TODO: implement real OpenAI call using settings.AI_API_KEY
        # import httpx; r = httpx.post(...)
        return MockProvider().chat(system, user, **kw)


class AnthropicProvider(AIProvider):
    def chat(self, system: str, user: str, **kw) -> str:
        # TODO: implement real Anthropic call
        return MockProvider().chat(system, user, **kw)


def _provider() -> AIProvider:
    p = (settings.AI_PROVIDER or "mock").lower()
    if p == "openai":
        return OpenAIProvider()
    if p == "anthropic":
        return AnthropicProvider()
    return MockProvider()


# ---------- High level operations ----------

class AIService:
    def __init__(self):
        self.provider = _provider()

    # ---- 1. outline ----
    def generate_outline(self, theme: str, genre: str = "", style: str = "", episodes: int = 10) -> Dict[str, Any]:
        return {
            "theme": theme,
            "genre": genre,
            "style": style,
            "logline": f"一个关于 {theme or '未知主题'} 的 {genre or '都市'} 故事，节奏 {style or '快节奏'}。",
            "act_structure": [
                {"act": 1, "title": "建立与钩子", "summary": "主角登场，世界观确立，抛出核心悬念。"},
                {"act": 2, "title": "冲突与升级", "summary": "矛盾持续升级，关键反转出现。"},
                {"act": 3, "title": "高潮与回归", "summary": "决战与情感落点。"},
            ],
            "target_episodes": episodes,
        }

    # ---- 2. split into episodes ----
    def split_episodes(self, outline: Dict[str, Any], total: int = 10) -> List[Dict[str, Any]]:
        out = []
        for i in range(1, total + 1):
            out.append({
                "episode_number": i,
                "title": f"第{i}集 · {self._pick_hook()}",
                "summary": f"本集围绕 {outline.get('theme', '主题')} 推进剧情，关键事件 #{i}。",
            })
        return out

    # ---- 3. script for one episode ----
    def generate_script(self, episode: Dict[str, Any], project_meta: Dict[str, Any]) -> Dict[str, Any]:
        ep_no = episode.get("episode_number", 1)
        title = episode.get("title", f"第{ep_no}集")
        body = (
            f"【开场钩子】{self._pick_hook()}\n"
            f"【冲突展开】主角面对突发危机，节奏紧凑。\n"
            f"【转折】出现关键反转，悬念升级。\n"
            f"【悬念结尾】{self._pick_cliff()}\n"
        )
        tags = ["#爽文", "#反转", "#高能"]
        return {
            "title": title,
            "content": body,
            "tags": tags,
            "cover_text": f"{title} | 不看后悔",
            "ai_score": round(random.uniform(72, 92), 1),
            "risk_flags": [] if random.random() > 0.2 else ["开场能量略低"],
        }

    # ---- 4. storyboard ----
    def generate_storyboard(self, script_text: str, shots: int = 8) -> List[Dict[str, Any]]:
        shots_data = []
        for i in range(1, shots + 1):
            shots_data.append({
                "shot_number": i,
                "duration_sec": round(random.uniform(2, 5), 1),
                "visual": f"镜头{i}：{self._pick_visual()}",
                "action": "主角进入画面，眼神坚定。",
                "voiceover": f"旁白第{i}句：紧张感持续累积。",
                "subtitle": f"字幕{i}",
                "sfx": random.choice(["whoosh", "boom", "tick", ""]),
                "bgm": "tense_loop_01" if i == 1 else "",
            })
        return shots_data

    # ---- 5. title/tags ----
    def generate_title_tags(self, content: str) -> Dict[str, Any]:
        return {
            "title": f"{self._pick_hook()} | 全程高能",
            "tags": ["#短剧", "#爽文", "#反转", "#必看"],
            "cover_text": "看到结尾我哭了",
        }

    # ---- 6. script rewrite operations ----
    def rewrite_script(self, original: str, instruction: str = "整体重写") -> str:
        return f"[改写·{instruction}]\n{self._pick_hook()}\n{original}\n{self._pick_cliff()}"

    def enhance_hook(self, original: str) -> str:
        return f"【强化开场钩子】{self._pick_hook()}\n\n{original}"

    def enhance_conflict(self, original: str) -> str:
        return f"{original}\n\n【冲突升级】反派出手，主角被逼入绝境，情绪 push 到顶点。"

    def enhance_cliffhanger(self, original: str) -> str:
        return f"{original}\n\n【悬念加强】{self._pick_cliff()}"

    # ---- 7. video generation (placeholder) ----
    def generate_video(self, storyboard: List[Dict[str, Any]]) -> Dict[str, Any]:
        # Mock returns a fake path — replace with real integration (Sora / Runway / SVD ...)
        fake_id = hashlib.md5(str(storyboard).encode()).hexdigest()[:10]
        return {
            "file_path": f"/storage/assets/mock_video_{fake_id}.mp4",
            "cover_path": f"/storage/assets/mock_cover_{fake_id}.jpg",
            "duration_sec": sum(s.get("duration_sec", 3) for s in storyboard),
            "status": "ready",
        }

    # ---- helpers ----
    def _pick_hook(self) -> str:
        return random.choice([
            "他刚走进门，所有人都安静了。",
            "三秒后，整条街炸了。",
            "她以为今天只是普通的一天。",
            "那个被所有人嘲笑的少年，今天拿到了一封信。",
        ])

    def _pick_cliff(self) -> str:
        return random.choice([
            "下一秒，门外传来了脚步声……",
            "可他不知道的是，真正的对手才刚刚登场。",
            "她举起电话，号码却怎么也按不下去。",
            "一切，都还远没有结束。",
        ])

    def _pick_visual(self) -> str:
        return random.choice([
            "近景，主角侧脸，逆光剪影。",
            "中景，办公室落地窗，雨夜。",
            "特写，紧握的拳头微微颤抖。",
            "广角空镜，城市夜景航拍。",
        ])


ai_service = AIService()
