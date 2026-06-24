"""
공통 LLM 클라이언트 — Qwen(로컬) ↔ Claude Sonnet(API) 추상화 레이어

사용법:
    from src.core.llm_client import LLMClient

    # 기본(환경변수 따름)
    llm = LLMClient()

    # 특정 역할로 호출 (역할별 모델 자동 선택)
    response = llm.chat("프롬프트", role="persona")   # → Qwen
    response = llm.chat("프롬프트", role="final")     # → Sonnet (상용) / Qwen (개발)

전환 방법:
    .env 에서 LLM_MODE 만 바꾸면 됨
    LLM_MODE=dev   → 전부 Qwen (무료, 개발용)
    LLM_MODE=prod  → final/feedback 은 Sonnet, 나머지는 Qwen (하이브리드)
    LLM_MODE=full  → 전부 Sonnet (최고 품질)
"""

import os
import json
from typing import Optional, Literal
from dotenv import load_dotenv
from loguru import logger

load_dotenv(dotenv_path=os.path.expanduser("~/vids-auto-engine/.env"))

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────
LLM_MODE        = os.getenv("LLM_MODE", "dev")          # dev | prod | full
OLLAMA_MODEL    = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
OLLAMA_MODEL_FAST = os.getenv("OLLAMA_MODEL_FAST", "qwen2.5:7b")
CLAUDE_MODEL    = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# 역할(role)별 모델 매핑
# 각 모드에서 어떤 역할을 어떤 백엔드로 보낼지 정의
ROLE_ROUTING = {
    "dev": {       # 개발: 전부 로컬 Qwen
        "persona":  "qwen",
        "debate":   "qwen",
        "final":    "qwen",
        "feedback": "qwen",
        "fast":     "qwen_fast",
    },
    "prod": {      # 상용: 품질 중요한 단계만 Claude
        "persona":  "qwen",
        "debate":   "qwen",
        "final":    "claude",
        "feedback": "claude",
        "fast":     "qwen_fast",
    },
    "full": {      # 최고 품질: 전부 Claude
        "persona":  "claude",
        "debate":   "claude",
        "final":    "claude",
        "feedback": "claude",
        "fast":     "claude",
    },
}


# ──────────────────────────────────────────────
# 백엔드 1: Ollama (Qwen)
# ──────────────────────────────────────────────
class OllamaBackend:
    def __init__(self):
        import ollama
        self.ollama = ollama

    def chat(
        self,
        prompt: str,
        model: str = OLLAMA_MODEL,
        system: Optional[str] = None,
        temperature: float = 0.7,
        json_mode: bool = False,
    ) -> str:
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        options = {"temperature": temperature}
        kwargs  = {"model": model, "messages": messages, "options": options}
        if json_mode:
            kwargs["format"] = "json"

        try:
            resp = self.ollama.chat(**kwargs)
            return resp["message"]["content"]
        except Exception as e:
            logger.error(f"[Ollama] 오류: {e}")
            raise


# ──────────────────────────────────────────────
# 백엔드 2: Claude (Sonnet API)
# ──────────────────────────────────────────────
class ClaudeBackend:
    def __init__(self):
        if not ANTHROPIC_API_KEY:
            logger.warning("[Claude] ANTHROPIC_API_KEY 미설정 — Claude 호출 시 오류 발생")
            self.client = None
        else:
            from anthropic import Anthropic
            self.client = Anthropic(api_key=ANTHROPIC_API_KEY)

    def chat(
        self,
        prompt: str,
        model: str = CLAUDE_MODEL,
        system: Optional[str] = None,
        temperature: float = 0.7,
        json_mode: bool = False,
        max_tokens: int = 4096,
    ) -> str:
        if self.client is None:
            raise RuntimeError(
                "Claude 백엔드 사용 불가: ANTHROPIC_API_KEY를 .env에 설정하세요.\n"
                "개발 중에는 LLM_MODE=dev 로 두면 Qwen만 사용합니다."
            )

        # JSON 모드일 때 시스템 프롬프트에 지시 추가
        if json_mode:
            json_instruction = "\n\n반드시 유효한 JSON 형식으로만 응답하세요. 다른 설명이나 마크다운 코드블록은 절대 포함하지 마세요."
            system = (system or "") + json_instruction

        kwargs = {
            "model":       model,
            "max_tokens":  max_tokens,
            "temperature": temperature,
            "messages":    [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system

        try:
            resp = self.client.messages.create(**kwargs)
            return resp.content[0].text
        except Exception as e:
            logger.error(f"[Claude] 오류: {e}")
            raise


# ──────────────────────────────────────────────
# 통합 LLM 클라이언트
# ──────────────────────────────────────────────
class LLMClient:
    """
    역할(role) 기반으로 적절한 백엔드를 자동 선택하는 통합 클라이언트.
    """

    def __init__(self, mode: Optional[str] = None):
        self.mode = mode or LLM_MODE
        if self.mode not in ROLE_ROUTING:
            logger.warning(f"알 수 없는 LLM_MODE '{self.mode}', 'dev'로 대체")
            self.mode = "dev"

        self.routing = ROLE_ROUTING[self.mode]
        self._ollama = None   # 지연 초기화
        self._claude = None

        logger.info(f"[LLMClient] 모드: {self.mode} | 라우팅: {self.routing}")

    @property
    def ollama(self) -> OllamaBackend:
        if self._ollama is None:
            self._ollama = OllamaBackend()
        return self._ollama

    @property
    def claude(self) -> ClaudeBackend:
        if self._claude is None:
            self._claude = ClaudeBackend()
        return self._claude

    def chat(
        self,
        prompt: str,
        role: Literal["persona", "debate", "final", "feedback", "fast"] = "persona",
        system: Optional[str] = None,
        temperature: float = 0.7,
        json_mode: bool = False,
    ) -> str:
        """
        역할에 따라 자동으로 Qwen 또는 Claude 선택해서 호출
        """
        backend_type = self.routing.get(role, "qwen")

        if backend_type == "qwen":
            return self.ollama.chat(prompt, model=OLLAMA_MODEL, system=system,
                                    temperature=temperature, json_mode=json_mode)
        elif backend_type == "qwen_fast":
            return self.ollama.chat(prompt, model=OLLAMA_MODEL_FAST, system=system,
                                    temperature=temperature, json_mode=json_mode)
        elif backend_type == "claude":
            return self.claude.chat(prompt, model=CLAUDE_MODEL, system=system,
                                    temperature=temperature, json_mode=json_mode)
        else:
            raise ValueError(f"알 수 없는 백엔드 타입: {backend_type}")

    def chat_json(
        self,
        prompt: str,
        role: str = "persona",
        system: Optional[str] = None,
        temperature: float = 0.7,
    ) -> dict:
        """
        JSON 응답을 받아서 파싱까지 해주는 헬퍼
        """
        raw = self.chat(prompt, role=role, system=system,
                        temperature=temperature, json_mode=True)
        return self._parse_json(raw)

    @staticmethod
    def _parse_json(raw: str) -> dict:
        """LLM 응답에서 JSON 추출 + 파싱 (마크다운 코드블록 제거)"""
        cleaned = raw.strip()
        # ```json ... ``` 제거
        if "```" in cleaned:
            cleaned = cleaned.replace("```json", "").replace("```", "").strip()
        # 첫 { 부터 마지막 } 까지
        start = cleaned.find("{")
        end   = cleaned.rfind("}") + 1
        if start == -1 or end == 0:
            # 배열일 수도
            start = cleaned.find("[")
            end   = cleaned.rfind("]") + 1
        try:
            return json.loads(cleaned[start:end])
        except Exception as e:
            logger.error(f"JSON 파싱 실패: {e}\n원본: {raw[:300]}")
            raise


# ──────────────────────────────────────────────
# 단독 테스트
# ──────────────────────────────────────────────
if __name__ == "__main__":
    print(f"현재 LLM_MODE: {LLM_MODE}\n")

    llm = LLMClient()

    # 1. 일반 텍스트 호출 (persona 역할 → dev모드에선 Qwen)
    print("─" * 40)
    print("테스트 1: persona 역할 (텍스트)")
    print("─" * 40)
    resp = llm.chat(
        "쇼츠 영상 마케팅 전문가로서 '천국의계단 스텝퍼'의 핵심 셀링포인트 1가지를 한 문장으로 말해줘.",
        role="persona",
        temperature=0.7,
    )
    print(resp)

    # 2. JSON 호출 (final 역할 → dev모드에선 Qwen, prod모드에선 Claude)
    print("\n" + "─" * 40)
    print("테스트 2: final 역할 (JSON)")
    print("─" * 40)
    data = llm.chat_json(
        """다음 상품의 쇼츠 후킹 멘트를 JSON으로 만들어줘.
상품: 천국의계단 스텝퍼
형식: {"hook": "첫 3초 후킹멘트", "cta": "구매 유도 멘트"}""",
        role="final",
    )
    print(json.dumps(data, ensure_ascii=False, indent=2))

    print("\n✅ LLM 클라이언트 정상 작동")