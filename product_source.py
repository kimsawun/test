"""
상품 소스 추상화 레이어 — 네이버 쇼핑(개발) ↔ 쿠팡 파트너스(운영) 전환

핵심 개념:
  모든 상품 검색/링크생성/이미지를 공통 인터페이스로 추상화.
  쿠팡 파트너스 API 승인 후 .env 한 줄(PRODUCT_SOURCE=coupang)만 바꾸면 전환.

공통 인터페이스 (ProductSource):
  - search_products(keyword, limit) → list[Product]
  - generate_affiliate_link(product) → str   ← 수익의 핵심
  - 각 Product는 이미지 URL 포함

전환 방법 (.env):
  PRODUCT_SOURCE=naver    → 네이버 쇼핑 (개발용, 현재)
  PRODUCT_SOURCE=coupang  → 쿠팡 파트너스 API (운영용, 승인 후)
"""

import os
import re
import hmac
import hashlib
import requests
from datetime import datetime
from typing import Optional
from urllib.parse import quote
from dataclasses import dataclass, field, asdict
from dotenv import load_dotenv
from loguru import logger

load_dotenv(dotenv_path=os.path.expanduser("~/vids-auto-engine/.env"))

PRODUCT_SOURCE = os.getenv("PRODUCT_SOURCE", "naver")  # naver | coupang

NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")

COUPANG_ACCESS_KEY  = os.getenv("COUPANG_ACCESS_KEY", "")
COUPANG_SECRET_KEY  = os.getenv("COUPANG_SECRET_KEY", "")
COUPANG_PARTNER_ID  = os.getenv("COUPANG_PARTNER_ID", "AF1693978")

# 수동 생성 링크 매핑 파일 (manual 모드용)
MANUAL_LINKS_FILE = os.path.expanduser("~/vids-auto-engine/vids-app/manual_coupang_links.json")


# ──────────────────────────────────────────────
# 공통 상품 데이터 구조
# ──────────────────────────────────────────────
@dataclass
class Product:
    name:          str
    price:         str
    image_url:     str
    product_url:   str
    affiliate_url: str = ""          # 내 전용 파트너스 링크 (수익 핵심)
    mall_name:     str = ""
    keyword:       str = ""
    source:        str = ""          # naver | coupang | manual
    product_id:    str = ""
    is_rocket:     bool = False       # 쿠팡 로켓배송 여부 (운영시)
    platform_name:        str = "coupang"  # coupang | musinsa
    trend_mode:           str = "current"  # current | seasonal
    seasonal_target_date: str = ""         # 시즌 예측 기준일
    extra:         dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ──────────────────────────────────────────────
# 추상 베이스
# ──────────────────────────────────────────────
class ProductSource:
    """모든 상품 소스가 구현해야 할 공통 인터페이스"""

    name = "base"

    def search_products(self, keyword: str, limit: int = 5) -> list[Product]:
        raise NotImplementedError

    def generate_affiliate_link(self, product: Product) -> str:
        """내 전용 파트너스 링크 생성 — 수익의 핵심"""
        raise NotImplementedError

    def is_available(self) -> bool:
        raise NotImplementedError


# ──────────────────────────────────────────────
# 1. 네이버 쇼핑 소스 (개발용)
# ──────────────────────────────────────────────
class NaverProductSource(ProductSource):
    name = "naver"
    SHOP_URL = "https://openapi.naver.com/v1/search/shop.json"

    def __init__(self):
        self.headers = {
            "X-Naver-Client-Id":     NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
        }

    def is_available(self) -> bool:
        return bool(NAVER_CLIENT_ID and NAVER_CLIENT_SECRET)

    def search_products(
        self, keyword: str, limit: int = 5,
        keyword_data: dict = None,
        platform_name: str = "coupang",
    ) -> list[Product]:
        # keyword_data: trend_extractor가 넘겨주는 키워드 메타 정보
        kd            = keyword_data or {}
        trend_mode    = kd.get("trend_mode", "current")
        seasonal_date = kd.get("seasonal_target_date", "")

        params = {"query": keyword, "display": limit, "sort": "sim"}
        try:
            resp = requests.get(self.SHOP_URL, headers=self.headers, params=params, timeout=10)
            resp.raise_for_status()
            items = resp.json().get("items", [])
        except Exception as e:
            logger.error(f"[네이버] 검색 실패: {e}")
            return []

        products = []
        for item in items:
            name = re.sub(r"<[^>]+>", "", item.get("title", ""))
            link = item.get("link", "")
            pid_match = re.search(r"/products/(\d+)", link)
            pid = pid_match.group(1) if pid_match else ""

            product = Product(
                name=name,
                price=item.get("lprice", "0"),
                image_url=item.get("image", ""),
                product_url=link,
                mall_name=item.get("mallName", ""),
                keyword=keyword,
                source="naver",
                product_id=pid,
                platform_name=platform_name,
                trend_mode=trend_mode,
                seasonal_target_date=seasonal_date,
            )
            product.affiliate_url = self.generate_affiliate_link(product)
            products.append(product)

        logger.info(f"[네이버] '{keyword}' {len(products)}개 검색")
        return products

    def generate_affiliate_link(self, product: Product) -> str:
        """
        개발 모드: 네이버 상품이 쿠팡 상품이면 partnersSub 링크 생성.
        아니면 원본 링크 (운영 모드에서 진짜 쿠팡 링크로 교체됨).
        """
        if product.product_id and "coupang" in product.product_url:
            return f"https://www.coupang.com/vp/products/{product.product_id}?partnersSub={COUPANG_PARTNER_ID}"
        # 쿠팡 상품이 아니면 일단 원본 (운영 모드에서 쿠팡 검색으로 대체)
        return product.product_url


# ──────────────────────────────────────────────
# 2. 쿠팡 파트너스 소스 (운영용 — 승인 후 활성화)
# ──────────────────────────────────────────────
class CoupangProductSource(ProductSource):
    name = "coupang"
    BASE_URL = "https://api-gateway.coupang.com"
    SEARCH_PATH = "/v2/providers/affiliate_open_api/apis/openapi/products/search"
    DEEPLINK_PATH = "/v2/providers/affiliate_open_api/apis/openapi/v1/deeplink"

    def __init__(self):
        self.access_key = COUPANG_ACCESS_KEY
        self.secret_key = COUPANG_SECRET_KEY
        self.partner_id = COUPANG_PARTNER_ID

    def is_available(self) -> bool:
        return bool(
            self.access_key and self.secret_key and
            self.access_key not in ("", "여기에_액세스키")
        )

    def _auth_header(self, method: str, path_with_query: str) -> dict:
        dt = datetime.utcnow().strftime("%y%m%dT%H%M%SZ")
        # 쿼리 분리
        if "?" in path_with_query:
            path, query = path_with_query.split("?", 1)
        else:
            path, query = path_with_query, ""
        message = dt + method + path + query
        signature = hmac.new(
            self.secret_key.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return {
            "Authorization": (
                f"CEA algorithm=HmacSHA256, access-key={self.access_key}, "
                f"signed-date={dt}, signature={signature}"
            ),
            "Content-Type": "application/json;charset=UTF-8",
        }

    def search_products(self, keyword: str, limit: int = 5) -> list[Product]:
        query = f"keyword={quote(keyword)}&limit={limit}"
        path_with_query = f"{self.SEARCH_PATH}?{query}"
        headers = self._auth_header("GET", path_with_query)
        try:
            resp = requests.get(self.BASE_URL + path_with_query, headers=headers, timeout=10)
            resp.raise_for_status()
            data = resp.json().get("data", {})
            items = data.get("productData", [])
        except Exception as e:
            logger.error(f"[쿠팡] 검색 실패: {e}")
            return []

        products = []
        for item in items:
            product = Product(
                name=item.get("productName", ""),
                price=str(item.get("productPrice", "0")),
                image_url=item.get("productImage", ""),
                product_url=item.get("productUrl", ""),
                keyword=keyword,
                source="coupang",
                product_id=str(item.get("productId", "")),
                is_rocket=item.get("isRocket", False),
                extra={"category": item.get("categoryName", "")},
            )
            # 쿠팡 API는 productUrl에 이미 파트너스 정보 포함되지만,
            # deeplink로 내 전용 단축링크 생성
            product.affiliate_url = self.generate_affiliate_link(product)
            products.append(product)

        logger.info(f"[쿠팡] '{keyword}' {len(products)}개 검색")
        return products

    def generate_affiliate_link(self, product: Product) -> str:
        """쿠팡 deeplink API로 내 전용 단축 파트너스 링크 생성 — 수익 핵심"""
        import json as _json
        body = {"coupangUrls": [product.product_url]}
        body_str = _json.dumps(body)
        headers = self._auth_header("POST", self.DEEPLINK_PATH)
        try:
            resp = requests.post(
                self.BASE_URL + self.DEEPLINK_PATH,
                headers=headers, data=body_str, timeout=10
            )
            resp.raise_for_status()
            data = resp.json().get("data", [])
            if data and isinstance(data, list):
                return data[0].get("shortenUrl", product.product_url)
        except Exception as e:
            logger.error(f"[쿠팡] deeplink 생성 실패: {e}")
        return product.product_url


# ──────────────────────────────────────────────
# 3. 수동 링크 소스 (manual — API 승인 전 실제 수익용)
# ──────────────────────────────────────────────
class ManualProductSource(NaverProductSource):
    """
    상품 검색은 네이버로 하되,
    파트너스 링크는 수동 생성한 매핑 파일에서 가져온다.
    쿠팡 파트너스 사이트에서 직접 만든 진짜 수익 링크 사용.
    """
    name = "manual"

    def __init__(self):
        super().__init__()
        self.manual_links = self._load_manual_links()

    def _load_manual_links(self) -> dict:
        """수동 링크 매핑 파일 로드"""
        import json
        if os.path.exists(MANUAL_LINKS_FILE):
            try:
                with open(MANUAL_LINKS_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                logger.info(f"[manual] 수동 링크 {len(data)}개 로드")
                return data
            except Exception as e:
                logger.error(f"[manual] 링크 파일 로드 실패: {e}")
        else:
            logger.warning(f"[manual] 링크 파일 없음: {MANUAL_LINKS_FILE}")
        return {}

    def generate_affiliate_link(self, product: Product) -> str:
        """
        수동 매핑에서 키워드/상품명으로 링크 찾기.
        매칭되는 링크가 없으면 빈 문자열 (블로그/영상에서 '링크 준비중' 처리)
        """
        # 1) 키워드 단위 매핑 우선 (키워드별로 대표 상품 링크 1개)
        keyword = product.keyword
        if keyword in self.manual_links:
            entry = self.manual_links[keyword]
            if isinstance(entry, dict):
                return entry.get("link", "")
            return entry  # 문자열이면 바로 링크

        # 2) 상품명 부분 매칭
        for key, val in self.manual_links.items():
            if key in product.name:
                return val.get("link", "") if isinstance(val, dict) else val

        logger.warning(f"[manual] 링크 없음: {keyword} / {product.name[:20]}")
        return ""


# ──────────────────────────────────────────────
# 팩토리 — 현재 모드에 맞는 소스 반환
# ──────────────────────────────────────────────
def get_product_source() -> ProductSource:
    """
    PRODUCT_SOURCE 설정에 따라 적절한 소스 반환.
    coupang 설정이지만 키가 없으면 자동으로 naver 폴백.
    """
    mode = PRODUCT_SOURCE.lower()

    if mode == "coupang":
        coupang = CoupangProductSource()
        if coupang.is_available():
            logger.info("🛒 상품 소스: 쿠팡 파트너스 API (운영 모드)")
            return coupang
        else:
            logger.warning("⚠️ 쿠팡 API 키 없음 → manual로 폴백")
            return ManualProductSource()

    if mode == "manual":
        logger.info("🔗 상품 소스: 네이버 검색 + 수동 파트너스 링크 (실수익 모드)")
        return ManualProductSource()

    logger.info("🔍 상품 소스: 네이버 쇼핑 (개발 모드)")
    return NaverProductSource()


# ──────────────────────────────────────────────
# 단독 테스트
# ──────────────────────────────────────────────
if __name__ == "__main__":
    print(f"현재 PRODUCT_SOURCE 설정: {PRODUCT_SOURCE}\n")

    source = get_product_source()
    print(f"활성 소스: {source.name}\n")

    # keyword_data 포함 테스트 (trend_mode, seasonal_target_date)
    keyword_data = {
        "keyword": "헬스 스텝퍼",
        "trend_mode": "current",
        "seasonal_target_date": "",
    }
    products = source.search_products(
        "헬스 스텝퍼", limit=3,
        keyword_data=keyword_data,
        platform_name="coupang",
    )
    print(f"\n{'='*50}")
    print(f"검색 결과: {len(products)}개")
    for p in products:
        print(f"\n📦 {p.name[:40]}")
        print(f"   가격: {p.price}원 | 판매처: {p.mall_name}")
        print(f"   소스: {p.source} | 플랫폼: {p.platform_name}")
        print(f"   트렌드: {p.trend_mode} | 예측일: {p.seasonal_target_date or '-'}")
        print(f"   🔗 파트너스 링크: {p.affiliate_url[:70]}")
        print(f"   🖼️  이미지: {p.image_url[:60]}")