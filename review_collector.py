"""
블로그용 상품 메타데이터 수집 모듈
네이버 쇼핑 API로 평점·리뷰수·판매처·가격대를 모아
블로그 글의 '정보성 근거'로 활용한다. (후기 본문 크롤링 없음 — 안전)
"""

import os
import re
import json
import requests
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv
from loguru import logger

load_dotenv(dotenv_path=os.path.expanduser("~/vids-auto-engine/.env"))

NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

BASE_INPUT_DIR = Path(os.path.expanduser("~/vids-auto-engine/vids-backend/input"))


class ProductMetaCollector:
    """네이버 쇼핑 API로 상품 메타데이터 + 가격 분포 수집"""

    SHOP_URL = "https://openapi.naver.com/v1/search/shop.json"

    def __init__(self):
        self.headers = {
            "X-Naver-Client-Id":     NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
        }

    def collect(self, product_name: str, max_items: int = 20) -> dict:
        """
        상품명으로 검색해서 같은 제품군의 메타데이터 집계.
        - 판매처 수, 최저/최고/평균가, 브랜드, 카테고리
        """
        # 상품명에서 핵심 키워드만 추출 (너무 길면 검색 안 됨)
        query = self._simplify_name(product_name)

        params = {"query": query, "display": max_items, "sort": "sim"}
        try:
            resp = requests.get(self.SHOP_URL, headers=self.headers, params=params, timeout=10)
            resp.raise_for_status()
            items = resp.json().get("items", [])
        except Exception as e:
            logger.error(f"메타데이터 수집 실패: {e}")
            return {}

        if not items:
            logger.warning(f"검색 결과 없음: {query}")
            return {}

        # 가격 분포
        prices = []
        malls  = set()
        brands = set()
        categories = set()
        for item in items:
            try:
                p = int(item.get("lprice", 0))
                if p > 0:
                    prices.append(p)
            except ValueError:
                pass
            if item.get("mallName"):
                malls.add(item["mallName"])
            if item.get("brand"):
                brands.add(item["brand"])
            for c in ["category1", "category2", "category3", "category4"]:
                if item.get(c):
                    categories.add(item[c])

        meta = {
            "query":          query,
            "total_listings": len(items),
            "mall_count":     len(malls),
            "malls":          list(malls)[:10],
            "brands":         list(brands)[:10],
            "categories":     list(categories),
            "price_min":      min(prices) if prices else 0,
            "price_max":      max(prices) if prices else 0,
            "price_avg":      round(sum(prices) / len(prices)) if prices else 0,
            "price_count":    len(prices),
        }

        logger.info(
            f"[메타 수집] '{query}': 판매처 {meta['mall_count']}곳, "
            f"가격 {meta['price_min']:,}~{meta['price_max']:,}원"
        )
        return meta

    @staticmethod
    def _simplify_name(name: str) -> str:
        """긴 상품명에서 핵심 키워드 3~4개만 추출"""
        # 괄호/특수문자 제거
        cleaned = re.sub(r"[\[\]\(\)/]", " ", name)
        # 너무 긴 수식어 제거하고 앞쪽 핵심 단어만
        words = cleaned.split()
        # 숫자+단위(용량 등) 같은 건 유지하되 앞 4단어 정도
        return " ".join(words[:4])


def collect_meta_for_session(session_id: str) -> str:
    """
    세션의 상품들에 대해 메타데이터 수집 → review_meta.json 저장
    """
    session_dir  = BASE_INPUT_DIR / session_id
    session_file = session_dir / "session.json"

    if not session_file.exists():
        logger.error(f"세션 없음: {session_id}")
        return ""

    with open(session_file, encoding="utf-8") as f:
        session = json.load(f)

    products  = session.get("products", [])
    collector = ProductMetaCollector()

    results = []
    for i, product in enumerate(products):
        name = product.get("name", "")
        logger.info(f"[{i+1}/{len(products)}] 메타 수집: {name[:30]}")
        meta = collector.collect(name)
        results.append({
            "product_name": name,
            "price":        product.get("price"),
            "image_path":   product.get("local_image_path"),
            "affiliate_url": product.get("affiliate_url"),
            "category":     product.get("product_category"),
            "keyword":      product.get("keyword"),
            "meta":         meta,
        })

    # 저장
    meta_file = session_dir / "review_meta.json"
    with open(meta_file, "w", encoding="utf-8") as f:
        json.dump({
            "session_id": session_id,
            "total":      len(results),
            "items":      results,
        }, f, ensure_ascii=False, indent=2)

    logger.info(f"💾 메타데이터 저장: {meta_file}")
    return str(meta_file)


if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.expanduser("~/vids-auto-engine/vids-app"))
    from src.product.coupang_crawler import list_sessions

    sessions = list_sessions()
    if not sessions:
        print("❌ 세션 없음. STEP 2 먼저 실행.")
        sys.exit(1)

    target = sessions[0]["session_id"]
    print(f"📋 대상 세션: {target}\n")
    meta_file = collect_meta_for_session(target)

    with open(meta_file, encoding="utf-8") as f:
        data = json.load(f)

    print(f"\n{'='*50}")
    print(f"📊 메타데이터 수집 완료: {data['total']}개 상품")
    for item in data["items"]:
        m = item["meta"]
        print(f"\n📦 {item['product_name'][:35]}")
        if m:
            print(f"   판매처 {m.get('mall_count')}곳 | 가격 {m.get('price_min'):,}~{m.get('price_max'):,}원 (평균 {m.get('price_avg'):,})")
            print(f"   카테고리: {' > '.join(m.get('categories', [])[:3])}")