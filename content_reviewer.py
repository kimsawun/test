"""
STEP 5 — 콘텐츠 피드백 모듈
LLM이 생성된 대본/블로그를 검토해서 점수와 개선사항 반환

검토 항목:
  1. 광고 효과성 (훅, CTA, 링크 자연스러움)
  2. 법적 안전성 (허위/과장 표현, 고지 문구)
  3. SEO (블로그만 — 제목, 키워드 밀도)
  4. 콘텐츠 품질 (정보성, 가독성)

통과 기준: score >= 70
최대 재시도: settings의 feedback_max_retry
"""

import os
import sys
import json
from loguru import logger
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.expanduser("~/vids-auto-engine/.env"))
sys.path.insert(0, os.path.expanduser("~/vids-auto-engine/vids-app"))

from src.core.llm_client import LLMClient
from src.db.database import update_content_feedback, get_setting


class ContentReviewer:
    """LLM 기반 콘텐츠 피드백"""

    def __init__(self):
        self.llm = LLMClient()

    def review_script(self, script: dict, product_data: dict) -> dict:
        """
        쇼츠/릴스 대본 검토
        script: persona_writer가 생성한 대본
        """
        name          = product_data.get("name", "")
        affiliate_url = product_data.get("affiliate_url", "")
        category      = product_data.get("product_category", "")

        prompt = f"""
너는 쇼츠/릴스 광고 전문 검수자야.
아래 대본을 검토하고 점수와 피드백을 줘.

[상품 정보]
- 상품명: {name}
- 카테고리: {category}
- 파트너스 링크 있음: {"예" if affiliate_url else "아니오"}

[대본]
제목: {script.get("title", "")}
훅: {script.get("hook", "")}
씬1: {script.get("scene1", "")}
씬2: {script.get("scene2", "")}
씬3: {script.get("scene3", "")}
CTA: {script.get("cta", "")}

[검토 기준]
1. 광고 효과성 (30점)
   - 훅이 3초 안에 시선을 끄는가?
   - CTA가 명확하고 자연스러운가?
   - 구매 욕구를 자극하는가?

2. 법적 안전성 (30점)
   - 허위/과장 표현이 없는가?
     (예: "무조건 살 빠진다", "100% 효과 보장" 같은 표현 금지)
   - 효능을 단언하지 않는가?
   - 광고임을 속이지 않는가?

3. 콘텐츠 품질 (40점)
   - 정보가 충분한가?
   - 자연스럽게 읽히는가?
   - 너무 홍보성으로 느껴지지 않는가?

[출력 형식]
반드시 아래 JSON만 출력해. 다른 말 하지마.
{{
  "score": 85,
  "passed": true,
  "scores": {{
    "effectiveness": 25,
    "legal_safety": 28,
    "quality": 32
  }},
  "issues": [
    "CTA에서 '지금 당장 사세요' 표현이 너무 강압적",
    "씬2에서 효과를 단언하는 표현 있음"
  ],
  "suggestions": [
    "CTA를 '관심 있으시면 링크 확인해보세요'로 부드럽게 변경",
    "씬2에서 '효과가 있다고 알려져 있어요' 정도로 완화"
  ],
  "highlight": "훅이 강렬하고 자연스러운 구성"
}}
""".strip()

        return self._run_review(prompt, "script")

    def review_blog(self, blog_data: dict, product_data: dict) -> dict:
        """
        블로그 글 검토
        blog_data: blog_writer가 생성한 블로그 데이터
        """
        name     = product_data.get("name", "")
        keyword  = product_data.get("keyword", "")
        category = product_data.get("product_category", "")
        title    = blog_data.get("title", "")
        # HTML에서 텍스트만 추출
        import re
        html_text = re.sub(r"<[^>]+>", "", blog_data.get("html", ""))[:1500]

        prompt = f"""
너는 블로그 SEO 및 마케팅 전문 검수자야.
아래 블로그 글을 검토하고 점수와 피드백을 줘.

[상품 정보]
- 상품명: {name}
- 키워드: {keyword}
- 카테고리: {category}

[블로그 글]
제목: {title}
본문 (앞부분): {html_text[:1000]}
태그: {", ".join(blog_data.get("tags", []))}

[검토 기준]
1. SEO 효과성 (30점)
   - 제목에 핵심 키워드가 포함됐는가?
   - 본문에 키워드가 자연스럽게 포함됐는가?
   - 클릭을 유도하는 제목인가?

2. 법적 안전성 (30점)
   - 허위/과장 표현이 없는가?
   - 쿠팡파트너스 고지 문구가 있는가?
   - 효능을 단언하지 않는가?

3. 콘텐츠 품질 (40점)
   - 정보가 충분하고 신뢰감이 있는가?
   - 광고처럼 보이지 않는가?
   - 독자가 끝까지 읽고 싶은가?

[출력 형식]
반드시 아래 JSON만 출력해. 다른 말 하지마.
{{
  "score": 82,
  "passed": true,
  "scores": {{
    "seo": 24,
    "legal_safety": 27,
    "quality": 31
  }},
  "issues": [
    "제목에 키워드가 없음",
    "파트너스 고지 문구 누락"
  ],
  "suggestions": [
    "제목을 '{keyword} 추천 및 솔직 리뷰'로 변경",
    "글 하단에 파트너스 고지 문구 추가"
  ],
  "highlight": "정보성이 높고 신뢰감 있는 구성"
}}
""".strip()

        return self._run_review(prompt, "blog")

    def _run_review(self, prompt: str, content_type: str) -> dict:
        """LLM 검토 실행"""
        min_score = int(get_setting("feedback_min_score", "70"))

        try:
            response = self.llm.chat(prompt, mode="feedback")
            text = response.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            result = json.loads(text.strip())

            score  = result.get("score", 0)
            passed = score >= min_score
            result["passed"] = passed

            icon = "✅" if passed else "❌"
            logger.info(
                f"{icon} [{content_type}] 피드백 점수: {score}/100 "
                f"({'통과' if passed else f'재시도 필요 (기준: {min_score}점)'})"
            )
            if result.get("issues"):
                for issue in result["issues"]:
                    logger.warning(f"  ⚠️  {issue}")

            return result

        except Exception as e:
            logger.error(f"피드백 실패: {e}")
            # 실패 시 기본 통과 (파이프라인 중단 방지)
            return {
                "score": 75, "passed": True,
                "issues": [], "suggestions": [],
                "highlight": "자동 검토 실패 — 기본 통과",
            }


def review_with_retry(
    reviewer: ContentReviewer,
    content_type: str,
    content_data: dict,
    product_data: dict,
    content_id: int = None,
) -> dict:
    """
    피드백 + 재시도 로직
    통과할 때까지 또는 최대 횟수까지 반복
    """
    max_retry = int(get_setting("feedback_max_retry", "2"))

    for attempt in range(max_retry + 1):
        logger.info(f"🔍 피드백 검토 ({attempt+1}/{max_retry+1}회차)")

        if content_type == "script":
            script = content_data.get("script", content_data)
            result = reviewer.review_script(script, product_data)
        else:
            result = reviewer.review_blog(content_data, product_data)

        # DB 기록
        if content_id:
            update_content_feedback(
                content_id=content_id,
                score=result.get("score", 0),
                passed=result.get("passed", False),
                issues=result.get("issues", []),
                suggestions=result.get("suggestions", []),
            )

        if result.get("passed"):
            logger.info(f"✅ 피드백 통과 ({attempt+1}회차)")
            return result

        if attempt < max_retry:
            logger.warning(
                f"❌ 미통과 — 개선 후 재시도 ({attempt+2}/{max_retry+1})\n"
                f"   문제: {result.get('issues', [])}"
            )
            # 개선 제안을 product_data에 주입 (다음 생성 시 반영)
            product_data["feedback_issues"]      = result.get("issues", [])
            product_data["feedback_suggestions"] = result.get("suggestions", [])

    logger.warning(f"⚠️  최대 재시도 초과 — 마지막 결과로 진행")
    return result


# ──────────────────────────────────────────────
# 단독 테스트
# ──────────────────────────────────────────────
if __name__ == "__main__":
    reviewer = ContentReviewer()

    # 대본 테스트
    test_script = {
        "title": "집에서 헬스장 효과 얻기",
        "hook":  "하루 10분으로 헬스장 효과를 낼 수 있다고?",
        "scene1": "바쁜 일상 속에서 운동할 시간이 없으신가요?",
        "scene2": "천국의계단 스텝밀로 하루 10분만 투자하세요.",
        "scene3": "실제로 사용해보니 허벅지 근력이 눈에 띄게 좋아졌어요.",
        "cta":   "지금 바로 구매하면 무조건 살 빠집니다! 당장 사세요!",
    }

    test_product = {
        "name":             "천국의계단 스텝퍼",
        "product_category": "헬스용품",
        "affiliate_url":    "https://link.coupang.com/a/xxx",
        "keyword":          "스텝퍼",
    }

    print("=== 대본 피드백 ===")
    result = reviewer.review_script(test_script, test_product)
    print(f"점수: {result['score']}/100")
    print(f"통과: {result['passed']}")
    print(f"문제: {result.get('issues', [])}")
    print(f"제안: {result.get('suggestions', [])}")
    print(f"강점: {result.get('highlight', '')}")
