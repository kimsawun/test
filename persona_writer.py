"""
STEP 3 — 멀티 페르소나 대본 생성 모듈 (v2 — 프롬프트 강화)
6인 전문가 집단이 토론하여 '구매 전환 극대화' 쇼츠 대본 생성

전환 깔때기: 노출 → 0~3초 이탈방지 → 끝까지 시청 → 링크 클릭
v2 개선점:
  - 빈 나레이션 원천 차단 + 검증/재생성 로직
  - 후킹/CTA 품질 기준 강제
  - 장면 수 5~7개로 제한
  - 심리 자극 요소 의무화
"""

import os
import json
from pathlib import Path
from datetime import datetime
from typing import Optional
from loguru import logger

import sys
sys.path.insert(0, os.path.expanduser("~/vids-auto-engine/vids-app"))
from src.core.llm_client import LLMClient

BASE_INPUT_DIR = Path(os.path.expanduser("~/vids-auto-engine/vids-backend/input"))


# ──────────────────────────────────────────────
# 6인 전문가 페르소나 정의 (v2 — 더 날카롭게)
# ──────────────────────────────────────────────
PERSONAS = {
    "scroll_stopper": {
        "name": "스크롤 스토퍼",
        "emoji": "🎣",
        "duty": "0~3초 이탈 방지",
        "system": """당신은 숏폼 첫 3초만 10년간 연구한 '스크롤 스토퍼'입니다.
무한 스크롤하던 엄지손가락을 강제로 멈추게 만드는 것이 유일한 임무입니다.

[필승 후킹 공식 — 반드시 이 중 하나를 써라]
1. 충격적 사실: "하루 10분, 이게 헬스장 1시간과 같다고?"
2. 반전 질문: "다이어트 실패가 의지 문제라고요? 아닙니다."
3. 결핍 자극: "당신만 모르는 집에서 살 빼는 법"
4. 호기심 갭: "이거 하나로 층간소음 없이 유산소 끝낸 이유"

[절대 금지]
- "안녕하세요", "이런 제품 있나요?", "소개합니다" 같은 평범한 시작
- 설명조 문장. 첫 문장이 평범하면 이미 패배다.""",
    },
    "psych_hacker": {
        "name": "소비심리 해커",
        "emoji": "🧠",
        "duty": "구매 욕구 점화",
        "system": """당신은 무의식적 구매 욕구를 자극하는 '소비심리 해커'입니다.

[반드시 심는 감정]
- 손실 회피: "이거 모르고 헬스장 돈 날린 사람 많아요"
- 사회적 증거: "요즘 다들 집에서 이걸로 운동해요"
- 결핍/욕망: 제품이 아니라 '그걸 가진 후의 날씬한 나'를 욕망하게
- 희소성/긴급성: "이 가격은 지금뿐"

제품 기능 나열은 금지. 감정과 욕구를 건드려라. 단 허위·과장은 절대 금지.""",
    },
    "storyteller": {
        "name": "스토리텔러",
        "emoji": "📖",
        "duty": "3초~끝까지 시청 유지",
        "system": """당신은 60초 안에 기승전결을 완성하는 숏폼 '스토리텔러'입니다.
- 떡밥-회수 구조로 끝까지 보게 만든다.
- 매 장면이 '다음이 궁금'해야 한다. 지루한 1초도 금지.
- 정보 나열이 아니라 서사로. (예: 문제 상황 → 발견 → 변화 → 결과)""",
    },
    "shortform_pd": {
        "name": "숏폼 PD",
        "emoji": "🎬",
        "duty": "영상 리듬·체류시간",
        "system": """당신은 1억뷰 쇼츠를 만든 '숏폼 PD'입니다.
- 장면당 3~8초. 전체 5~7개 장면. 너무 잘게 쪼개지 마라.
- 상품 사진 1장으로 만들 수 있는 현실적 연출만 제안.
  (줌인/줌아웃, 텍스트 오버레이, 패닝, 비교 자막 등)
- 끝과 처음이 이어지는 루프로 재시청 유도.""",
    },
    "performance_marketer": {
        "name": "퍼포먼스 마케터",
        "emoji": "🛒",
        "duty": "링크 클릭(CTA) 전환",
        "system": """당신은 쿠팡 파트너스로 월 수천만원 버는 '퍼포먼스 마케터'.
유일한 KPI는 '링크 클릭률'이다.

[CTA 필수 요소 — 전부 넣어라]
- 긴급성: "오늘 이 가격", "재고 얼마 안 남음"
- 구체적 혜택: 정확한 가격, 할인 강조
- 행동 지시: "지금 아래 링크 확인" (단 광고처럼 보이면 실패)
- 클릭 안 하면 손해라는 감정

"지금 구매하세요" 같은 밋밋한 CTA는 금지.""",
    },
    "algorithm_analyst": {
        "name": "플랫폼 알고리즘 분석가",
        "emoji": "🔍",
        "duty": "노출·추천 최적화",
        "system": """당신은 유튜브/인스타 알고리즘을 역공학한 '알고리즘 분석가'.
- 시청 완료율을 높이는 마지막 1초 떡밥 설계.
- 댓글을 유발하는 질문/논쟁 요소.
- 해시태그는 대형(#홈트) + 중형(#스텝퍼) + 롱테일(#층간소음없는운동) 조합.""",
    },
}


class PersonaScriptWriter:
    def __init__(self):
        self.llm = LLMClient()

    def _product_brief(self, product: dict) -> str:
        return f"""[상품 정보]
- 상품명: {product.get('name')}
- 가격: {product.get('price')}원
- 카테고리: {product.get('product_category', '')}
- 키워드: {product.get('keyword')}
- 선정 이유: {product.get('keyword_reason', '')}"""

    # ── 1라운드 ──
    def round1_drafts(self, product: dict) -> dict:
        logger.info("🎬 1라운드: 6인 전문가 초안 작성")
        brief = self._product_brief(product)
        drafts = {}
        for key, persona in PERSONAS.items():
            prompt = f"""{brief}

이 상품으로 60초 쇼츠를 만듭니다.
당신은 '{persona['name']}'({persona['duty']} 담당)입니다.
당신 전문 관점에서:
1. 핵심 전략 (2문장)
2. 바로 쓸 수 있는 구체적 대사/연출 1개 (실제 문장으로)

180자 이내. 추상적 조언 금지, 바로 쓸 실전 예시로."""
            try:
                draft = self.llm.chat(prompt, role="persona",
                                      system=persona["system"], temperature=0.85)
                drafts[key] = draft.strip()
                logger.info(f"  {persona['emoji']} {persona['name']} 완료")
            except Exception as e:
                logger.error(f"  {persona['name']} 실패: {e}")
                drafts[key] = ""
        return drafts

    # ── 2라운드 ──
    def round2_debate(self, product: dict, drafts: dict) -> str:
        logger.info("💬 2라운드: 전문가 토론 통합")
        brief = self._product_brief(product)
        all_drafts = "\n\n".join([
            f"{PERSONAS[k]['emoji']} {PERSONAS[k]['name']}:\n{v}"
            for k, v in drafts.items() if v
        ])
        prompt = f"""{brief}

6인 전문가의 아이디어:

{all_drafts}

당신은 총괄 디렉터다. 위 의견을 종합해 '실행 가능한 대본 전략'으로 정리하라.
반드시 포함:
1. 첫 3초 후킹 — 구체적 문장 1개 (스크롤 강제 정지용)
2. 중간 전개 — 어떤 순서로 욕구를 점화할지
3. 마지막 CTA — 클릭 유발 문장 1개 (긴급성+혜택 포함)

각 항목에 '실제 쓸 문장'을 반드시 포함하라. 300자 이내."""
        debate = self.llm.chat(prompt, role="debate", temperature=0.7)
        logger.info("  ✅ 통합 완료")
        return debate.strip()

    # ── 3라운드: 최종 대본 (강화 프롬프트 + 검증) ──
    def round3_final_script(self, product: dict, strategy: str, retry: int = 2) -> dict:
        logger.info("✍️  3라운드: 최종 대본 생성")
        brief = self._product_brief(product)
        price = product.get("price", "")

        prompt = f"""{brief}

[확정 전략]
{strategy}

위 전략으로 60초 쇼츠 최종 대본을 JSON으로 완성하라.

[가장 중요 — 라벨을 따라 쓰지 마라]
아래 JSON의 값에 있는 "(...)" 안 설명은 '작성 지침'일 뿐이다.
절대 그 지침 문구("첫 3초 후킹 멘트", "스크롤 정지용", "마지막 링크 클릭 유도" 등)를
답변에 그대로 옮겨 쓰지 마라. 오직 완성된 실제 대사만 넣어라.

❌ 나쁜 예: "hook": "첫 3초 후킹 멘트: '집에서 운동하세요' (스크롤 정지용)"
✅ 좋은 예: "hook": "헬스장 끊고 3개월째 안 간 사람 손?"

[과장 광고 금지 — 계정 정지 방지]
- "3일/한 달 만에 살 빠진다" 같은 구체적 효과·기간 보장 절대 금지.
- "뚱보", "돌변" 같은 비하·과장 표현 금지.
- 효과는 단정하지 말고 '꾸준히 하면', '도움이 될 수 있는' 같은 권유형으로.
- 사실에 근거한 제품 특징과 '체험 권유'로 욕구를 자극하라.

[절대 규칙]
1. 모든 narration은 비울 수 없다. 최소 15자 이상 완성된 한국어 문장.
2. 장면은 정확히 5~7개.
3. hook은 충격/반전/호기심 질문 중 하나. 설명조·인사말 금지.
4. cta는 긴급성 + 가격({price}원) + 행동지시를 자연스럽게 포함.

[출력 — 아래 JSON 구조만. 괄호 안 지침은 절대 베끼지 말 것]
{{
  "title": "여기에 실제 제목만 (지침 베끼지 말 것)",
  "hook": "여기에 실제 후킹 대사만",
  "scenes": [
    {{
      "scene_no": 1,
      "duration_sec": 4,
      "narration": "여기에 실제 나레이션 대사만",
      "subtitle": "여기에 실제 자막만",
      "visual": "상품 사진 활용 연출 설명",
      "purpose": "장면 목적"
    }}
  ],
  "cta": "여기에 실제 CTA 대사만",
  "hashtags": ["홈트", "스텝퍼", "하체운동", "층간소음없는운동", "다이어트"],
  "total_duration_sec": 55,
  "loop_hook": "재시청 유도 요소"
}}"""

        for attempt in range(retry + 1):
            try:
                script = self.llm.chat_json(prompt, role="final", temperature=0.7)
                ok, reason = self._validate(script)
                if ok:
                    logger.info(f"  ✅ 대본 완성: {script.get('title')}")
                    return script
                logger.warning(f"  ⚠️ 검증 실패({reason}), 재생성 {attempt+1}/{retry}")
                prompt += f"\n\n[이전 시도 실패: {reason}. 반드시 규칙을 지켜라.]"
            except Exception as e:
                logger.error(f"  대본 생성 오류: {e}")

        logger.error("  ❌ 재생성 한도 초과, 마지막 결과 반환")
        return script

    # 프롬프트 지침이 그대로 새어나오는 라벨 패턴
    LABEL_LEAKS = [
        "후킹 멘트", "스크롤 정지", "링크 클릭 유도", "여기에 실제",
        "지침 베끼지", "완성된 문장", "장면 목적", "재시청 유도 요소",
    ]
    # 과장 광고 / 비하 표현 (계정 정지 위험)
    BANNED_PHRASES = [
        "3일 만에", "일주일 만에", "한 달 만에", "뚱보", "돌변",
        "무조건", "100%", "반드시 빠", "확실히 빠",
    ]

    @classmethod
    def _validate(cls, script: dict) -> tuple[bool, str]:
        """대본 품질 검증 — 길이 + 라벨 누출 + 과장 표현"""
        scenes = script.get("scenes", [])
        if not (5 <= len(scenes) <= 7):
            return False, f"장면 수 {len(scenes)}개 (5~7개 필요)"

        # 검사 대상 텍스트 전체 취합
        texts = [script.get("hook", ""), script.get("cta", ""), script.get("title", "")]
        for s in scenes:
            narr = (s.get("narration") or "").strip()
            if len(narr) < 15:
                return False, f"장면{s.get('scene_no')} 나레이션 부족({len(narr)}자)"
            texts.append(narr)
            texts.append(s.get("subtitle", ""))

        joined = " ".join(texts)

        # 라벨 누출 검사
        for leak in cls.LABEL_LEAKS:
            if leak in joined:
                return False, f"프롬프트 라벨 누출 감지('{leak}')"

        # 과장/비하 표현 검사
        for banned in cls.BANNED_PHRASES:
            if banned in joined:
                return False, f"과장·금지 표현 감지('{banned}')"

        if len((script.get("hook") or "").strip()) < 10:
            return False, "후킹 멘트 부족"
        if len((script.get("cta") or "").strip()) < 15:
            return False, "CTA 부족"
        return True, "ok"

    def generate(self, product: dict) -> dict:
        logger.info("=" * 50)
        logger.info(f"STEP 3 — 대본 생성: {product.get('name')[:30]}")
        logger.info("=" * 50)
        drafts   = self.round1_drafts(product)
        strategy = self.round2_debate(product, drafts)
        script   = self.round3_final_script(product, strategy)
        return {
            "product_name":  product.get("name"),
            "product_price": product.get("price"),
            "affiliate_url": product.get("affiliate_url"),
            "image_path":    product.get("local_image_path"),
            "drafts":        drafts,
            "strategy":      strategy,
            "script":        script,
            "generated_at":  datetime.now().isoformat(),
        }


def generate_scripts_for_session(session_id: str, max_products: int = 0) -> str:
    session_dir  = BASE_INPUT_DIR / session_id
    session_file = session_dir / "session.json"
    if not session_file.exists():
        logger.error(f"세션 없음: {session_id}")
        return ""
    with open(session_file, encoding="utf-8") as f:
        session = json.load(f)
    products = session.get("products", [])
    if max_products > 0:
        products = products[:max_products]

    writer  = PersonaScriptWriter()
    scripts = []
    for i, product in enumerate(products):
        logger.info(f"\n[{i+1}/{len(products)}] 대본 생성 중...")
        try:
            scripts.append(writer.generate(product))
        except Exception as e:
            logger.error(f"대본 실패 ({product.get('name')}): {e}")

    scripts_file = session_dir / "scripts.json"
    with open(scripts_file, "w", encoding="utf-8") as f:
        json.dump({"session_id": session_id, "generated_at": datetime.now().isoformat(),
                   "total": len(scripts), "scripts": scripts}, f, ensure_ascii=False, indent=2)

    session["status"] = "scripting"
    with open(session_file, "w", encoding="utf-8") as f:
        json.dump(session, f, ensure_ascii=False, indent=2)

    logger.info("=" * 50)
    logger.info(f"STEP 3 완료 — {len(scripts)}개 대본 | 💾 {scripts_file}")
    logger.info("=" * 50)
    return str(scripts_file)


if __name__ == "__main__":
    from src.product.coupang_crawler import list_sessions
    sessions = list_sessions()
    if not sessions:
        print("❌ 세션 없음. STEP 2 먼저 실행.")
        sys.exit(1)

    latest = sessions[0]["session_id"]
    print(f"📋 최신 세션: {latest}\n🎬 대본 생성 (테스트 1개)\n")
    scripts_file = generate_scripts_for_session(latest, max_products=1)

    with open(scripts_file, encoding="utf-8") as f:
        data = json.load(f)
    for s in data["scripts"]:
        sc = s["script"]
        print(f"\n{'='*50}")
        print(f"📹 제목: {sc.get('title')}")
        print(f"🎣 후킹: {sc.get('hook')}")
        print(f"\n장면 구성:")
        for scene in sc.get("scenes", []):
            print(f"  [{scene.get('scene_no')}] {scene.get('duration_sec')}초 | {scene.get('subtitle')}")
            print(f"      🎙️  {scene.get('narration')}")
            print(f"      🎥 {scene.get('visual')}")
        print(f"\n🛒 CTA: {sc.get('cta')}")
        print(f"🔁 루프: {sc.get('loop_hook')}")
        print(f"#️⃣  {' '.join('#'+h for h in sc.get('hashtags', []))}")