"""
트렌드 키워드 추출 — 네이버 데이터랩 API 기반
두 가지 모드:
  current  — 오늘 기준 급상승 카테고리/키워드
  seasonal — 작년 (오늘+offset)일 급상승 키워드 (한달 뒤 선점)
  both     — current + seasonal 혼합

API:
  쇼핑인사이트: 카테고리별 클릭 추이
    POST https://openapi.naver.com/v1/datalab/shopping/categories
  통합검색어 트렌드: 키워드 검색량 추이 (과거 데이터 가능)
    POST https://openapi.naver.com/v1/datalab/search

윤달 처리:
  dateutil.relativedelta 사용 (2월 29일 등 안전 처리)
"""

import os
import sys
import json
import requests
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
from loguru import logger
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.expanduser("~/vids-auto-engine/.env"))
sys.path.insert(0, os.path.expanduser("~/vids-auto-engine/vids-app"))

from src.core.llm_client import LLMClient
from src.db.database import get_setting

NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

HEADERS = {
    "X-Naver-Client-Id":     NAVER_CLIENT_ID,
    "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
    "Content-Type":          "application/json",
}

# 네이버 쇼핑 카테고리 ID 매핑
SHOPPING_CATEGORIES = [
    {"name": "패션의류",     "param": ["50000000"]},
    {"name": "화장품/미용",  "param": ["50000002"]},
    {"name": "식품",         "param": ["50000003"]},
    {"name": "스포츠/레저",  "param": ["50000005"]},
    {"name": "생활/건강",    "param": ["50000006"]},
    {"name": "가구/인테리어","param": ["50000007"]},
    {"name": "디지털/가전",  "param": ["50000008"]},
    {"name": "출산/육아",    "param": ["50000009"]},
    {"name": "여행/문화",    "param": ["50000011"]},
]


# ──────────────────────────────────────────────
# 날짜 계산 유틸 (윤달 안전)
# ──────────────────────────────────────────────
def get_seasonal_target_date(
    base_date: datetime = None,
    offset_days: int = 30,
    years_back: int = 1,
) -> datetime:
    """
    시즌 예측 기준일 계산 (윤달 안전)
    오늘 + offset_days → 작년 동일 시점
    예: 6/22 + 30일 = 7/22 → 작년 7/22
    윤달: 2024/2/29 + 30일 = 3/30 → 작년 3/30 (안전)
    """
    if base_date is None:
        base_date = datetime.now()

    target_this_year = base_date + timedelta(days=offset_days)
    target_last_year = target_this_year - relativedelta(years=years_back)

    logger.info(
        f"📅 시즌 예측 기준일: {target_last_year.strftime('%Y-%m-%d')} "
        f"(오늘 {base_date.strftime('%Y-%m-%d')} + {offset_days}일의 작년 시점)"
    )
    return target_last_year


def make_date_range(
    center: datetime,
    window_days: int = 7,
    max_end: datetime = None,
) -> tuple[str, str]:
    """
    center ± window_days 날짜 범위 생성
    미래 날짜 자동 조정 (네이버 API는 미래 데이터 없음)
    """
    start = center - timedelta(days=window_days)
    end   = center + timedelta(days=window_days)
    if max_end is None:
        max_end = datetime.now() - timedelta(days=1)  # 어제까지
    if end > max_end:
        end = max_end
    if start > max_end:
        start = max_end - timedelta(days=window_days * 2)
    return start.strftime("%Y-%m-%d"), end.strftime("%Y-%m-%d")


# ──────────────────────────────────────────────
# 네이버 데이터랩 API 호출
# ──────────────────────────────────────────────
class NaverDataLabCollector:
    """네이버 데이터랩 API로 트렌드 데이터 수집"""

    SHOPPING_URL = "https://openapi.naver.com/v1/datalab/shopping/categories"
    SEARCH_URL   = "https://openapi.naver.com/v1/datalab/search"

    def get_shopping_trends(
        self,
        start_date: str,
        end_date: str,
        time_unit: str = "week",
    ) -> list[dict]:
        """
        쇼핑인사이트 API — 카테고리별 클릭 추이
        네이버 API 제한: 한 번에 최대 5개 카테고리 → 나눠서 요청
        """
        import time as _time
        all_raw = []
        chunk_size = 3
        for i in range(0, len(SHOPPING_CATEGORIES), chunk_size):
            chunk = SHOPPING_CATEGORIES[i:i + chunk_size]
            payload = {
                "startDate": start_date,
                "endDate":   end_date,
                "timeUnit":  time_unit,
                "category":  chunk,
            }
            try:
                resp = requests.post(
                    self.SHOPPING_URL,
                    headers=HEADERS,
                    data=json.dumps(payload),
                    timeout=15,
                )
                resp.raise_for_status()
                all_raw.extend(resp.json().get("results", []))
                _time.sleep(0.3)
            except Exception as e:
                logger.error(f"쇼핑인사이트 API 실패 (chunk {i}): {e}")
                continue
        results = all_raw

        trends = []
        for r in results:
            data = r.get("data", [])
            if not data:
                continue
            ratios = [d.get("ratio", 0) for d in data]
            if len(ratios) < 2:
                continue
            # 마지막 값 vs 평균으로 트렌드 판단
            avg    = sum(ratios[:-1]) / len(ratios[:-1]) if ratios[:-1] else 0
            latest = ratios[-1]
            trend  = "up" if latest > avg * 1.1 else ("down" if latest < avg * 0.9 else "stable")
            trends.append({
                "category": r.get("title", ""),
                "latest_ratio": round(latest, 2),
                "avg_ratio":    round(avg, 2),
                "trend":        trend,
                "change_pct":   round((latest - avg) / avg * 100, 1) if avg else 0,
            })

        # 급상승 순으로 정렬
        trends.sort(key=lambda x: x["change_pct"], reverse=True)
        logger.info(f"쇼핑인사이트: {len(trends)}개 카테고리 분석")
        return trends

    def get_search_trend(
        self,
        keywords: list[str],
        start_date: str,
        end_date: str,
        time_unit: str = "week",
    ) -> list[dict]:
        """
        통합검색어 트렌드 API — 키워드 검색량 추이
        과거 날짜 지정 가능 → 시즌 예측에 활용
        최대 5개 키워드 그룹
        """
        # 키워드를 5개씩 그룹으로
        keyword_groups = [
            {"groupName": kw, "keywords": [kw]}
            for kw in keywords[:5]
        ]
        payload = {
            "startDate":  start_date,
            "endDate":    end_date,
            "timeUnit":   time_unit,
            "keywordGroups": keyword_groups,
        }
        try:
            resp = requests.post(
                self.SEARCH_URL,
                headers=HEADERS,
                data=json.dumps(payload),
                timeout=15,
            )
            resp.raise_for_status()
            results = resp.json().get("results", [])
        except Exception as e:
            logger.error(f"검색어 트렌드 API 실패: {e}")
            return []

        trend_data = []
        for r in results:
            data = r.get("data", [])
            if not data:
                continue
            ratios = [d.get("ratio", 0) for d in data]
            avg    = sum(ratios) / len(ratios) if ratios else 0
            peak   = max(ratios) if ratios else 0
            trend_data.append({
                "keyword":   r.get("title", ""),
                "avg_ratio": round(avg, 2),
                "peak_ratio": round(peak, 2),
                "trend":     "up" if ratios[-1] > avg * 1.1 else "stable",
            })

        trend_data.sort(key=lambda x: x["peak_ratio"], reverse=True)
        return trend_data


# ──────────────────────────────────────────────
# LLM 키워드 분석
# ──────────────────────────────────────────────
class KeywordAnalyzer:
    def __init__(self):
        self.llm = LLMClient()

    def analyze(
        self,
        trend_data: list[dict],
        limit: int = 5,
        trend_mode: str = "current",
        seasonal_target_date: str = "",
    ) -> list[dict]:
        if not trend_data:
            return []

        if trend_mode == "seasonal":
            context = (
                f"아래는 {seasonal_target_date} 기준 작년에 급상승했던 쇼핑 트렌드야. "
                f"올해 같은 시점(약 한 달 뒤)에 다시 급상승할 가능성이 높아. "
                f"지금 미리 광고를 준비하면 경쟁자보다 선점 효과가 있어."
            )
        else:
            context = "아래는 지금 현재 네이버 쇼핑에서 급상승하고 있는 트렌드야."

        prompt = f"""
{context}

트렌드 데이터:
{json.dumps(trend_data, ensure_ascii=False, indent=2)}

이 데이터를 분석해서 쿠팡/무신사 파트너스 제휴 광고로 수익을 낼 수 있는
상위 {limit}개 키워드를 골라줘.

각 키워드에 대해 다음 형식의 JSON 배열로만 답해줘:
[
  {{
    "keyword": "구체적인 상품 키워드 (카테고리 말고 실제 상품명 수준)",
    "product_category": "상품 카테고리",
    "reason": "선정 이유 1줄",
    "estimated_purchase_rate": "높음/중간/낮음",
    "trend_mode": "{trend_mode}",
    "seasonal_target_date": "{seasonal_target_date}",
    "suggested_products": ["추천 상품 예시1", "추천 상품 예시2"]
  }}
]

JSON 배열만 출력하고 다른 말은 하지마.
""".strip()

        try:
            response = self.llm.chat(prompt)
            text = response.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
            result = json.loads(text.strip())
            logger.info(f"✅ LLM 분석 완료: {len(result)}개 키워드")
            return result[:limit]
        except Exception as e:
            logger.error(f"LLM 분석 실패: {e}")
            return [
                {
                    "keyword": d.get("category", ""),
                    "product_category": d.get("category", ""),
                    "reason": f"트렌드 변화율 {d.get('change_pct', 0)}%",
                    "estimated_purchase_rate": "중간",
                    "trend_mode": trend_mode,
                    "seasonal_target_date": seasonal_target_date,
                    "suggested_products": [],
                }
                for d in trend_data[:limit]
            ]


# ──────────────────────────────────────────────
# 통합 파이프라인
# ──────────────────────────────────────────────
class TrendKeywordPipeline:
    def __init__(self):
        self.collector = NaverDataLabCollector()
        self.analyzer  = KeywordAnalyzer()

    def run(self) -> list[dict]:
        trend_mode     = get_setting("trend_mode", "both")
        offset_days    = int(get_setting("seasonal_offset_days", "30"))
        window_days    = int(get_setting("seasonal_window_days", "7"))
        current_count  = int(get_setting("current_keyword_count", "3"))
        seasonal_count = int(get_setting("seasonal_keyword_count", "2"))
        max_keywords   = int(get_setting("max_keywords", "5"))

        results = []
        today   = datetime.now()

        # ── 현재 트렌드 ──
        if trend_mode in ("current", "both"):
            logger.info("📈 현재 트렌드 수집 중...")
            start, end = make_date_range(today - timedelta(days=1), window_days=window_days)
            trends = self.collector.get_shopping_trends(start, end)
            if trends:
                analyzed = self.analyzer.analyze(
                    trends, limit=current_count, trend_mode="current"
                )
                results.extend(analyzed)
                logger.info(f"  현재 트렌드 키워드: {len(analyzed)}개")
            else:
                logger.warning("현재 트렌드 데이터 없음")

        # ── 시즌 예측 ──
        if trend_mode in ("seasonal", "both"):
            logger.info(f"🔮 시즌 예측 수집 중... (오늘+{offset_days}일의 작년 데이터)")
            target_date = get_seasonal_target_date(
                base_date=today, offset_days=offset_days
            )
            target_str  = target_date.strftime("%Y-%m-%d")
            start, end  = make_date_range(
                target_date, window_days=window_days,
                max_end=today - timedelta(days=1),
            )
            logger.info(f"  검색 기간: {start} ~ {end}")
            trends = self.collector.get_shopping_trends(start, end, time_unit="week")
            if trends:
                analyzed = self.analyzer.analyze(
                    trends, limit=seasonal_count,
                    trend_mode="seasonal",
                    seasonal_target_date=target_str,
                )
                results.extend(analyzed)
                logger.info(f"  시즌 예측 키워드: {len(analyzed)}개 (기준일: {target_str})")
            else:
                logger.warning("시즌 예측 데이터 없음")

        logger.info(f"✅ 총 키워드: {len(results)}개")
        return results[:max_keywords]

    def preview_seasonal_date(self) -> dict:
        offset_days = int(get_setting("seasonal_offset_days", "30"))
        window_days = int(get_setting("seasonal_window_days", "7"))
        today       = datetime.now()
        target      = get_seasonal_target_date(offset_days=offset_days)
        start, end  = make_date_range(
            target, window_days=window_days,
            max_end=today - timedelta(days=1),
        )
        return {
            "today":             today.strftime("%Y-%m-%d"),
            "offset_days":       offset_days,
            "target_this_year":  (today + timedelta(days=offset_days)).strftime("%Y-%m-%d"),
            "target_last_year":  target.strftime("%Y-%m-%d"),
            "search_range":      f"{start} ~ {end}",
            "description": (
                f"오늘({today.strftime('%m/%d')}) 기준 {offset_days}일 후"
                f"({(today + timedelta(days=offset_days)).strftime('%m/%d')})에 "
                f"급상승 예상 → 작년 {target.strftime('%Y/%m/%d')} 데이터 분석"
            ),
        }


# ──────────────────────────────────────────────
# 단독 테스트
# ──────────────────────────────────────────────
if __name__ == "__main__":
    pipeline = TrendKeywordPipeline()

    # 시즌 예측 날짜 미리보기
    print("\n=== 시즌 예측 날짜 계산 ===")
    preview = pipeline.preview_seasonal_date()
    for k, v in preview.items():
        print(f"  {k}: {v}")

    # 윤달 테스트
    print("\n=== 윤달 처리 테스트 ===")
    test_cases = [
        (datetime(2024, 2, 29), 30, "2월 29일 + 30일의 작년"),
        (datetime(2024, 1, 31), 30, "1월 31일 + 30일의 작년"),
        (datetime(2026, 6, 22), 30, "오늘 + 30일의 작년"),
        (datetime(2026, 12, 15), 30, "12월 + 30일 (연도 넘김)"),
    ]
    for base, offset, desc in test_cases:
        result = get_seasonal_target_date(base, offset)
        target_this = base + timedelta(days=offset)
        print(f"  {desc}")
        print(f"    {base.strftime('%Y-%m-%d')} + {offset}일 "
              f"= {target_this.strftime('%Y-%m-%d')} "
              f"→ 작년: {result.strftime('%Y-%m-%d')}")

    # 실제 파이프라인
    print("\n=== 트렌드 키워드 수집 ===")
    keywords = pipeline.run()
    print(f"\n총 {len(keywords)}개 키워드:")
    for kw in keywords:
        mode_icon = "📈" if kw.get("trend_mode") == "current" else "🔮"
        print(f"\n{mode_icon} [{kw.get('trend_mode')}] {kw.get('keyword')}")
        print(f"   카테고리: {kw.get('product_category')}")
        print(f"   이유: {kw.get('reason')}")
        print(f"   구매전환: {kw.get('estimated_purchase_rate')}")
        if kw.get("trend_mode") == "seasonal":
            print(f"   예측 기준일: {kw.get('seasonal_target_date')}")
        if kw.get("suggested_products"):
            print(f"   추천 상품: {', '.join(kw['suggested_products'])}")