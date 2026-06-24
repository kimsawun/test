"""
블로그 글 생성 모듈 (Qwen 최적화 프롬프트 엔지니어링 버전)

핵심 설계 철학 — Qwen은 추상 지시에 약하므로:
  1. 추상적 톤 지시 대신 '실제 문장 예시(few-shot)'를 보여준다
  2. 글 전체를 통째로 요청하지 않고 '섹션 스키마'를 못박는다
  3. "하지 마"(부정)보다 "이렇게 써"(긍정 지시)를 쓴다
  4. 출력 형식을 JSON 스키마로 엄격하게 고정한다
  5. 상품타입/사진수/카테고리에 따라 글 형태 자체를 분기한다

전략 차원:
  product_type : 기구/소모품/의류/식품/디지털 (실사용 묘사가 다름)
  format       : 리뷰형/비교형/가이드형/스토리형
  tone         : 카테고리 기반 어조 (실제 예문 제공)
  image_plan   : 사진 수에 따른 배치 전략
"""

import os
import sys
import json
import re
from pathlib import Path
from loguru import logger
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.expanduser("~/vids-auto-engine/.env"))
sys.path.insert(0, os.path.expanduser("~/vids-auto-engine/vids-app"))

from src.core.llm_client import LLMClient

BASE_OUTPUT_DIR = Path(os.path.expanduser("~/vids-auto-engine/vids-backend/input"))


# ══════════════════════════════════════════════════
# 1. 전략 사전
# ══════════════════════════════════════════════════
CATEGORY_TONE = {
    "패션의류": "감성적", "화장품/미용": "감성적",
    "스포츠/레저": "효과중심", "생활/건강": "정보성",
    "출산/육아": "공감형", "디지털/가전": "스펙비교",
    "가구/인테리어": "공간연출", "식품": "성분효능",
    "헬스용품": "효과중심",
}

CATEGORY_PRODUCT_TYPE = {
    "패션의류": "의류", "화장품/미용": "소모품",
    "스포츠/레저": "기구", "생활/건강": "기구",
    "출산/육아": "기구", "디지털/가전": "디지털",
    "가구/인테리어": "기구", "식품": "식품",
    "헬스용품": "기구",
}

# 어조별 '실제 문장 예시' — Qwen은 예시 모방에 강하다
TONE_EXAMPLES = {
    "감성적": {
        "desc": "착용감과 감정, 분위기를 중심으로 부드럽게 풀어쓴다",
        "good": [
            "처음 손에 들었을 때 생각보다 가볍고 부드러워서 조금 놀랐어요.",
            "아침에 걸치기만 했는데 하루 종일 기분이 단정해지는 느낌이었어요.",
        ],
        "avoid": "스펙 나열, 딱딱한 설명",
    },
    "효과중심": {
        "desc": "구체적 시간·횟수·변화를 중심으로 신뢰감 있게 쓴다",
        "good": [
            "하루 10분씩 일주일 써보니 종아리에 은근히 자극이 오는 게 느껴졌어요.",
            "처음엔 5분도 힘들었는데 2주쯤 지나니 15분도 가뿐해지더라고요.",
        ],
        "avoid": "효과 단정 표현(무조건, 100%, 보장)",
    },
    "정보성": {
        "desc": "성분·사용법·선택 기준을 차분하고 객관적으로 정리한다",
        "good": [
            "이런 제품을 고를 때는 보통 세 가지를 먼저 확인하는 게 좋아요.",
            "용량 대비 가격을 따져보면 어떤 분께 잘 맞는지 가늠이 됩니다.",
        ],
        "avoid": "과장, 감탄사 남발",
    },
    "공감형": {
        "desc": "같은 고민을 해본 사람 입장에서 따뜻하게 공감하며 쓴다",
        "good": [
            "저도 처음엔 이게 정말 필요할까 한참 고민했었어요.",
            "밤마다 똑같은 고민 하시는 분들, 마음 충분히 이해돼요.",
        ],
        "avoid": "단정적 권유, 강압적 표현",
    },
    "스펙비교": {
        "desc": "성능·사양을 다른 선택지와 비교하며 똑똑하게 정리한다",
        "good": [
            "비슷한 가격대 제품과 비교하면 배터리 쪽에서 차이가 좀 났어요.",
            "스펙만 보면 비슷해 보여도 실제 쓰임새는 꽤 갈리더라고요.",
        ],
        "avoid": "근거 없는 우열 단정",
    },
    "공간연출": {
        "desc": "공간에 놓였을 때의 분위기와 활용을 상상하게 만든다",
        "good": [
            "거실 한쪽에 두니 분위기가 한결 정돈된 느낌이 들었어요.",
            "생각보다 자리를 적게 차지해서 좁은 방에도 잘 어울릴 것 같아요.",
        ],
        "avoid": "과장된 인테리어 효과 약속",
    },
    "성분효능": {
        "desc": "성분과 활용법을 근거 있게, 안전하게 설명한다",
        "good": [
            "성분표를 보면 어떤 분께 잘 맞을지 어느 정도 짐작할 수 있어요.",
            "하루 권장량을 지키면서 꾸준히 챙기는 게 핵심인 것 같아요.",
        ],
        "avoid": "치료·예방 효과 단정(의약품 오인 표현)",
    },
}

PRODUCT_TYPE_GUIDE = {
    "기구":   "조립/설치 난이도, 사용 공간, 작동 소음, 사용 후 느낌을 다룬다.",
    "소모품": "발림성/사용감, 지속력, 양 대비 가성비, 재구매 의향을 다룬다.",
    "의류":   "착용감, 핏, 소재 질감, 세탁 편의, 코디 활용을 다룬다.",
    "식품":   "맛/식감, 간편함, 보관 방법, 어떤 상황에 좋은지를 다룬다.",
    "디지털": "성능 체감, 배터리/연결, UI 편의, 기존 제품 대비 차이를 다룬다.",
}

FORMAT_GUIDE = {
    "리뷰형": {
        "desc": "직접 써본 경험을 솔직하게 풀어내는 1인칭 후기",
        "flow": ["공감 도입", "첫인상", "실사용 경험", "좋은 점 3가지", "아쉬운 점 1가지", "추천 대상", "마무리"],
    },
    "가이드형": {
        "desc": "상품 선택·사용법을 알려주는 정보 안내",
        "flow": ["문제 제기", "선택 기준", "이 상품의 특징", "활용 팁", "주의할 점", "추천 대상", "마무리"],
    },
    "비교형": {
        "desc": "다른 선택지와 비교하며 판단을 돕는 글",
        "flow": ["고민 도입", "비교 기준", "이 상품의 강점", "약점도 솔직히", "어떤 분께 맞나", "마무리"],
    },
    "스토리형": {
        "desc": "구매 계기부터 사용까지의 이야기 흐름",
        "flow": ["사연 도입", "구매 결심 계기", "받았을 때", "써보니", "지금은", "추천 대상", "마무리"],
    },
}


# ══════════════════════════════════════════════════
# 2. 전략 결정
# ══════════════════════════════════════════════════
def determine_strategy(
    category: str,
    image_count: int,
    description_length: int = 0,
    product_name: str = "",
) -> dict:
    """카테고리 + 상품타입 + 이미지 수 → 정교한 블로그 전략"""

    product_type = CATEGORY_PRODUCT_TYPE.get(category, "기구")

    # 이미지 임팩트 + 분량
    if image_count >= 4:
        impact = "high"
        word_count = 1300
        img_strategy = "사진이 풍부하니 사진마다 짧은 설명을 붙여 이야기처럼 전개한다."
    elif image_count >= 2:
        impact = "medium"
        word_count = 1700
        img_strategy = "도입·본문·마무리에 사진을 고르게 배치하고 앞뒤로 자연스러운 설명을 단다."
    else:
        impact = "low"
        word_count = 2200
        img_strategy = "사진이 1장뿐이니 상단에 크게 배치하고 나머지는 글의 묘사로 생생함을 채운다."

    if description_length > 1000:
        word_count = min(word_count + 300, 2600)

    # 글 형태 선택
    if image_count >= 4:
        blog_format = "스토리형"
    elif category in ("디지털/가전",):
        blog_format = "비교형"
    elif category in ("출산/육아", "생활/건강", "식품"):
        blog_format = "가이드형"
    else:
        blog_format = "리뷰형"

    tone        = CATEGORY_TONE.get(category, "정보성")
    tone_data   = TONE_EXAMPLES.get(tone, TONE_EXAMPLES["정보성"])
    type_guide  = PRODUCT_TYPE_GUIDE.get(product_type, PRODUCT_TYPE_GUIDE["기구"])
    format_data = FORMAT_GUIDE.get(blog_format, FORMAT_GUIDE["리뷰형"])

    banner_positions = ["middle", "bottom"]
    if impact == "low":
        banner_positions = ["middle", "before_end", "bottom"]

    strategy = {
        "category":         category,
        "product_type":     product_type,
        "blog_format":      blog_format,
        "format_flow":      format_data["flow"],
        "format_desc":      format_data["desc"],
        "tone":             tone,
        "tone_desc":        tone_data["desc"],
        "tone_examples":    tone_data["good"],
        "tone_avoid":       tone_data["avoid"],
        "type_guide":       type_guide,
        "image_impact":     impact,
        "image_count":      image_count,
        "word_count":       word_count,
        "img_strategy":     img_strategy,
        "banner_positions": banner_positions,
    }

    logger.info(
        f"📝 블로그 전략 결정\n"
        f"   카테고리: {category} (타입: {product_type})\n"
        f"   글 형태: {blog_format} | 어조: {tone}\n"
        f"   이미지: {image_count}장 ({impact}) → 목표 {word_count}자\n"
        f"   흐름: {' → '.join(format_data['flow'])}"
    )
    return strategy


# ══════════════════════════════════════════════════
# 3. 블로그 글 생성 (Qwen 최적화 프롬프트)
# ══════════════════════════════════════════════════
class BlogWriter:

    def __init__(self):
        self.llm = LLMClient()

    def generate(self, product_data: dict, strategy: dict, session_id: str) -> dict:
        name          = product_data.get("name", "")
        keyword       = product_data.get("keyword", "")
        category      = product_data.get("product_category", "생활용품")
        price         = product_data.get("price", "")
        affiliate_url = product_data.get("affiliate_url", "")
        partners_html = product_data.get("partners_html", "")
        description   = product_data.get("description", "")
        image_paths   = product_data.get("all_image_paths", [])
        blog_title    = product_data.get("blog_title", "")

        banner_html      = self._make_banner_html(partners_html, affiliate_url, name)
        img_placeholders = self._make_image_placeholders(image_paths)

        raw = self._generate_with_llm(
            name=name, keyword=keyword, category=category, price=price,
            description=description, blog_title=blog_title,
            strategy=strategy, img_placeholders=img_placeholders,
        )

        html = self._assemble_html(raw, banner_html, img_placeholders)

        output = {
            "title":    raw.get("title", f"{name} 솔직 후기"),
            "html":     html,
            "tags":     raw.get("tags", [keyword, category]),
            "summary":  raw.get("summary", ""),
            "strategy": strategy,
        }

        session_dir = BASE_OUTPUT_DIR / session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        with open(session_dir / "blog.json", "w", encoding="utf-8") as f:
            json.dump(output, f, ensure_ascii=False, indent=2)

        logger.info(f"✅ 블로그 글 생성 완료: {output['title'][:40]}")
        return output

    def _generate_with_llm(
        self, name, keyword, category, price, description,
        blog_title, strategy, img_placeholders,
    ) -> dict:

        img_guide = "\n".join([
            f"    - {ph['placeholder']} : {ph['position_desc']}"
            for ph in img_placeholders
        ]) if img_placeholders else "    (사진 없음 — 글의 묘사로 생생함을 채울 것)"

        tone_example_block = "\n".join([
            f'    예시{i+1}) "{s}"'
            for i, s in enumerate(strategy["tone_examples"])
        ])

        flow_block = "\n".join([
            f"    {i+1}. {step}"
            for i, step in enumerate(strategy["format_flow"])
        ])

        banner_guide = []
        if "middle" in strategy["banner_positions"]:
            banner_guide.append("    - {{BANNER_MIDDLE}} : 좋은 점/특징을 충분히 설명한 직후")
        if "before_end" in strategy["banner_positions"]:
            banner_guide.append("    - {{BANNER_BEFORE_END}} : 추천 대상을 말한 뒤, 마무리 바로 앞")
        if "bottom" in strategy["banner_positions"]:
            banner_guide.append("    - {{BANNER_BOTTOM}} : 글 맨 마지막")
        banner_block = "\n".join(banner_guide)

        prompt = f"""당신은 솔직하고 친근한 후기를 잘 쓰는 한국인 블로거입니다.
아래 상품을 실제로 써본 사람처럼 자연스러운 블로그 글을 작성하세요.

════════ 상품 정보 ════════
- 상품명: {name}
- 핵심 키워드: {keyword}
- 카테고리: {category}
- 가격: {price}원
- 참고 정보: {description[:700] if description else "별도 정보 없음 — 상품명에서 자연스럽게 유추"}

════════ 글의 성격 ════════
- 글 형태: {strategy['blog_format']} ({strategy['format_desc']})
- 어조: {strategy['tone']} — {strategy['tone_desc']}
- 이 상품 타입의 핵심 포인트: {strategy['type_guide']}
- 목표 분량: 약 {strategy['word_count']}자

════════ 어조 따라하기 (아래 예시 말투처럼 쓰세요) ════════
{tone_example_block}
    → 예시처럼 '겪어본 사람의 말투'로 쓰되, 문장을 그대로 베끼지는 마세요.
    → 피해야 할 것: {strategy['tone_avoid']}

════════ 글 구성 순서 (이 흐름대로 전개) ════════
{flow_block}

════════ 사진 배치 ════════
{img_guide}
    → 사진 전략: {strategy['img_strategy']}

════════ 광고 배너 삽입 위치 ════════
{banner_block}

════════ 반드시 지킬 규칙 ════════
1. 광고가 아니라 '진짜 써본 후기'처럼 쓰세요. 첫 문장부터 광고 티가 나면 안 됩니다.
2. 좋은 점 3가지와 아쉬운 점 1가지를 꼭 넣으세요. (아쉬운 점이 있어야 믿음이 갑니다)
3. 효과·성능을 단정하지 마세요. "~인 것 같아요", "~라고 느꼈어요"처럼 경험으로 표현하세요.
4. 소제목(h2)을 3~4개 만들어 스크롤을 유도하세요.
5. 한 단락은 2~3문장으로 짧게 끊으세요.
6. 각 소제목 섹션 끝에는 다음 내용이 궁금해지는 한 문장을 넣으세요.
7. 문체는 친근한 존댓말(~요)로 통일하세요.

════════ 출력 형식 (이 JSON만 출력, 다른 말 절대 금지) ════════
{{
  "title": "검색 잘 되도록 '{keyword}'를 넣은 30자 내외 제목",
  "summary": "클릭을 부르는 150자 이내 요약",
  "tags": ["{keyword}", "관련태그2", "관련태그3", "관련태그4", "관련태그5"],
  "sections": [
    {{ "type": "intro", "content": "<p>도입 단락</p><p>둘째 단락</p>" }},
    {{ "type": "image", "placeholder": "{{{{IMAGE_1}}}}" }},
    {{ "type": "section", "h2": "소제목", "content": "<p>본문</p>" }},
    {{ "type": "banner", "placeholder": "{{{{BANNER_MIDDLE}}}}" }},
    {{ "type": "section", "h2": "소제목", "content": "<p>본문</p>" }},
    {{ "type": "banner", "placeholder": "{{{{BANNER_BOTTOM}}}}" }}
  ]
}}

지금 위 형식에 맞춰 JSON만 출력하세요."""

        try:
            response = self.llm.chat(prompt, mode="final")
            return self._parse_json(response, name, keyword, category)
        except Exception as e:
            logger.error(f"LLM 글 생성 실패: {e}")
            return self._fallback_content(name, keyword, category)

    def _parse_json(self, response, name, keyword, category) -> dict:
        text = response.strip()
        if "```" in text:
            for part in text.split("```"):
                p = part.strip()
                if p.startswith("json"):
                    p = p[4:].strip()
                if p.startswith("{"):
                    text = p
                    break
        start, end = text.find("{"), text.rfind("}")
        if start != -1 and end != -1:
            text = text[start:end+1]
        try:
            result = json.loads(text)
            logger.info(f"✅ LLM 글 파싱 성공: {result.get('title','')[:40]}")
            return result
        except json.JSONDecodeError as e:
            logger.error(f"JSON 파싱 실패: {e} → 폴백")
            return self._fallback_content(name, keyword, category)

    def _make_banner_html(self, partners_html, affiliate_url, product_name) -> str:
        if partners_html and "<a href=" in partners_html:
            return (
                '<div style="text-align:center; margin:30px 0; padding:20px; '
                'background:#f8f9fa; border-radius:8px;">\n'
                '  <p style="margin-bottom:10px; font-size:14px; color:#666;">'
                '👇 현재 가격 확인하기</p>\n'
                f'  {partners_html}\n'
                '  <p style="margin-top:10px; font-size:13px; color:#888;">'
                '쿠팡에서 최저가로 만나보세요</p>\n</div>'
            )
        elif affiliate_url:
            return (
                '<div style="text-align:center; margin:30px 0; padding:20px; '
                'background:#f8f9fa; border-radius:8px;">\n'
                f'  <a href="{affiliate_url}" target="_blank" referrerpolicy="unsafe-url" '
                'style="display:inline-block; padding:14px 30px; background:#e8232a; '
                'color:white; text-decoration:none; border-radius:6px; '
                'font-size:16px; font-weight:bold;">🛒 쿠팡에서 최저가 확인하기</a>\n</div>'
            )
        return ""

    def _make_image_placeholders(self, image_paths) -> list:
        position_map = {
            0: "도입부 바로 아래 (가장 임팩트 있는 첫 사진)",
            1: "좋은 점을 설명하는 중간",
            2: "아쉬운 점이나 총평 근처",
            3: "추천 대상을 말하는 부분 근처",
            4: "마무리 섹션",
        }
        return [
            {
                "placeholder":   f"{{{{IMAGE_{i+1}}}}}",
                "path":          path,
                "position_desc": position_map.get(i, f"{i+1}번째 적절한 위치"),
                "alt":           f"상품 사진 {i+1}",
            }
            for i, path in enumerate(image_paths)
        ]

    def _assemble_html(self, raw, banner_html, img_placeholders) -> str:
        sections = raw.get("sections", [])
        parts = []
        for s in sections:
            t = s.get("type", "")
            if t == "intro":
                parts.append(f'<div class="blog-intro">\n{s.get("content","")}\n</div>')
            elif t == "section":
                h2 = s.get("h2", "")
                c  = s.get("content", "")
                parts.append(f'<h2>{h2}</h2>\n{c}' if h2 else c)
            elif t == "image":
                idx = self._img_idx(s.get("placeholder", ""))
                if idx is not None and idx < len(img_placeholders):
                    info = img_placeholders[idx]
                    parts.append(
                        f'<!-- IMAGE_PLACEHOLDER:{info["path"]} -->\n'
                        f'<div class="img-wrap" style="text-align:center; margin:20px 0;">\n'
                        f'  <img src="{{{{TISTORY_IMG_{idx+1}}}}}" '
                        f'alt="{info["alt"]}" style="max-width:100%;">\n</div>'
                    )
            elif t == "banner":
                ph = s.get("placeholder", "")
                if any(k in ph for k in ("MIDDLE", "BEFORE_END", "BOTTOM")):
                    parts.append(banner_html)

        parts.append(
            '\n<div style="margin-top:40px; padding:15px; background:#f0f0f0; '
            'border-radius:6px; font-size:12px; color:#888; text-align:center;">\n'
            '이 포스팅은 쿠팡 파트너스 활동의 일환으로, 이에 따른 일정액의 수수료를 '
            '제공받습니다.\n</div>'
        )
        return "\n\n".join(parts)

    def _img_idx(self, ph):
        m = re.search(r"IMAGE_(\d+)", ph)
        return int(m.group(1)) - 1 if m else None

    def _fallback_content(self, name, keyword, category) -> dict:
        return {
            "title":   f"{keyword} {name[:15]} 솔직 후기",
            "summary": f"{name}을(를) 직접 써보고 정리한 후기입니다.",
            "tags":    [keyword, category, "후기", "추천", "쿠팡"],
            "sections": [
                {"type": "intro",   "content": f"<p>{name}에 대해 알아보겠습니다.</p>"},
                {"type": "image",   "placeholder": "{{IMAGE_1}}"},
                {"type": "section", "h2": "써보니 어땠나",  "content": "<p>사용 경험을 정리했습니다.</p>"},
                {"type": "banner",  "placeholder": "{{BANNER_MIDDLE}}"},
                {"type": "section", "h2": "이런 분께 추천", "content": "<p>추천 대상을 정리했습니다.</p>"},
                {"type": "banner",  "placeholder": "{{BANNER_BOTTOM}}"},
            ],
        }


# ══════════════════════════════════════════════════
# 4. 단독 테스트
# ══════════════════════════════════════════════════
if __name__ == "__main__":
    writer = BlogWriter()

    test_cases = [
        {
            "name": "천국의계단 스텝퍼 가정용 홈트", "keyword": "스텝퍼",
            "product_category": "헬스용품", "price": "139000",
            "affiliate_url": "https://link.coupang.com/a/test1",
            "partners_html": '<a href="https://link.coupang.com/a/test1" target="_blank">구매</a>',
            "description": "가정용 계단 오르기 운동기구. 하체 운동에 좋다.",
            "all_image_paths": ["/tmp/a.jpg"],
            "blog_title": "",
        },
        {
            "name": "유아용 원목 침대 안전가드", "keyword": "유아침대",
            "product_category": "출산/육아", "price": "210000",
            "affiliate_url": "https://link.coupang.com/a/test2",
            "partners_html": '<a href="https://link.coupang.com/a/test2" target="_blank">구매</a>',
            "description": "친환경 원목 유아 침대. 안전가드 포함.",
            "all_image_paths": ["/tmp/a.jpg", "/tmp/b.jpg", "/tmp/c.jpg", "/tmp/d.jpg"],
            "blog_title": "",
        },
    ]

    for product in test_cases:
        print(f"\n{'='*60}")
        print(f"상품: {product['name']} | {product['product_category']} | 사진 {len(product['all_image_paths'])}장")

        strategy = determine_strategy(
            category=product["product_category"],
            image_count=len(product["all_image_paths"]),
            description_length=len(product.get("description", "")),
            product_name=product["name"],
        )
        print(f"→ 글형태: {strategy['blog_format']} | 어조: {strategy['tone']} | 분량: {strategy['word_count']}자")

        result = writer.generate(product, strategy, "2026-06-23_blog_test")
        print(f"\n제목: {result['title']}")
        print(f"태그: {result['tags']}")
        print(f"요약: {result['summary'][:80]}")
        print(f"HTML 길이: {len(result['html'])}자")
