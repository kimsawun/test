"""
STEP 2 — 상품 검색 + 이미지 다운로드 모듈
- 네이버 쇼핑 API (현재 사용)
- 쿠팡 파트너스 API (승인 후 자동 전환)
- 일자별 + 카운트 폴더 구조
- session.json 메타데이터 저장
"""

import os
import re
import json
import time
import hmac
import hashlib
import asyncio
import aiohttp
import requests
from datetime import datetime
from pathlib import Path
from typing import Optional
from urllib.parse import quote
from dotenv import load_dotenv
from loguru import logger
from PIL import Image

load_dotenv(dotenv_path=os.path.expanduser("~/vids-auto-engine/.env"))

# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────
NAVER_CLIENT_ID     = os.getenv("NAVER_CLIENT_ID")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET")
COUPANG_ACCESS_KEY  = os.getenv("COUPANG_ACCESS_KEY", "")
COUPANG_SECRET_KEY  = os.getenv("COUPANG_SECRET_KEY", "")
COUPANG_PARTNER_ID  = os.getenv("COUPANG_PARTNER_ID", "AF1693978")

BASE_INPUT_DIR = Path(os.path.expanduser("~/vids-auto-engine/vids-backend/input"))
BASE_INPUT_DIR.mkdir(parents=True, exist_ok=True)


# ──────────────────────────────────────────────
# 세션 폴더 생성 (날짜_카운트)
# ──────────────────────────────────────────────
def create_session_dir() -> tuple[Path, str]:
    """
    오늘 날짜 기준으로 실행 순서 카운트 폴더 생성
    예: 2026-06-21_001, 2026-06-21_002 ...
    반환: (폴더 Path, 세션 ID 문자열)
    """
    today = datetime.now().strftime("%Y-%m-%d")

    # 오늘 날짜로 시작하는 기존 폴더 목록 확인
    existing = sorted([
        d for d in BASE_INPUT_DIR.iterdir()
        if d.is_dir() and d.name.startswith(today)
    ])

    # 다음 카운트 번호 결정
    if existing:
        last    = existing[-1].name  # 예: 2026-06-21_003
        last_count = int(last.split("_")[-1])
        count   = last_count + 1
    else:
        count = 1

    session_id  = f"{today}_{count:03d}"
    session_dir = BASE_INPUT_DIR / session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    logger.info(f"📁 세션 폴더 생성: {session_dir}")
    return session_dir, session_id


# ──────────────────────────────────────────────
# 1. 네이버 쇼핑 API
# ──────────────────────────────────────────────
class NaverShoppingClient:
    BASE_URL = "https://openapi.naver.com/v1/search/shop.json"

    def __init__(self):
        self.headers = {
            "X-Naver-Client-Id":     NAVER_CLIENT_ID,
            "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
        }

    def search_products(self, keyword: str, limit: int = 5) -> list[dict]:
        params = {
            "query":   keyword,
            "display": limit,
            "start":   1,
            "sort":    "sim",
        }
        try:
            resp = requests.get(self.BASE_URL, headers=self.headers, params=params, timeout=10)
            resp.raise_for_status()
            items = resp.json().get("items", [])

            products = []
            for item in items:
                name  = re.sub(r"<[^>]+>", "", item.get("title", ""))
                link  = item.get("link", "")
                pid   = re.search(r"/products/(\d+)", link)
                aff   = (
                    f"https://www.coupang.com/vp/products/{pid.group(1)}?partnersSub={COUPANG_PARTNER_ID}"
                    if pid and "coupang" in link else link
                )
                products.append({
                    "name":          name,
                    "price":         item.get("lprice", "0"),
                    "image_url":     item.get("image", ""),
                    "product_url":   link,
                    "affiliate_url": aff,
                    "mall_name":     item.get("mallName", ""),
                    "keyword":       keyword,
                    "source":        "naver_shopping",
                })

            logger.info(f"[네이버 쇼핑] '{keyword}' 검색 결과: {len(products)}개")
            return products

        except Exception as e:
            logger.error(f"[네이버 쇼핑] 오류: {e}")
            return []


# ──────────────────────────────────────────────
# 2. 쿠팡 파트너스 API (승인 후 자동 전환)
# ──────────────────────────────────────────────
class CoupangAPIClient:
    BASE_URL = "https://api-gateway.coupang.com"

    def __init__(self):
        self.access_key = COUPANG_ACCESS_KEY
        self.secret_key = COUPANG_SECRET_KEY

    def is_available(self) -> bool:
        return bool(
            self.access_key and
            self.secret_key and
            self.access_key != "여기에_액세스키" and
            self.secret_key != "여기에_시크릿키"
        )

    def _generate_hmac(self, method: str, path: str) -> dict:
        dt  = datetime.utcnow().strftime("%y%m%dT%H%M%SZ")
        sig = hmac.new(
            self.secret_key.encode("utf-8"),
            (dt + method + path).encode("utf-8"),
            hashlib.sha256
        ).hexdigest()
        return {
            "Authorization": f"CEA algorithm=HmacSHA256, access-key={self.access_key}, signed-date={dt}, signature={sig}",
            "Content-Type":  "application/json;charset=UTF-8",
        }

    def search_products(self, keyword: str, limit: int = 5) -> list[dict]:
        path    = f"/v2/providers/affiliate_open_api/apis/openapi/products/search?keyword={quote(keyword)}&limit={limit}"
        headers = self._generate_hmac("GET", path)
        try:
            resp = requests.get(self.BASE_URL + path, headers=headers, timeout=10)
            resp.raise_for_status()
            products = resp.json().get("data", {}).get("productData", [])
            logger.info(f"[쿠팡 API] '{keyword}' 검색 결과: {len(products)}개")
            return products
        except Exception as e:
            logger.error(f"[쿠팡 API] 오류: {e}")
            return []


# ──────────────────────────────────────────────
# 3. 이미지 다운로더
# ──────────────────────────────────────────────
class ProductImageDownloader:
    def __init__(self, session_dir: Path):
        self.session_dir = session_dir

    async def download_images(self, products: list[dict]) -> list[dict]:
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer":    "https://search.shopping.naver.com",
        }
        async with aiohttp.ClientSession(headers=headers) as session:
            tasks   = [self._download_single(session, p) for p in products]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        success = [r for r in results if isinstance(r, dict)]
        logger.info(f"[이미지 다운로드] {len(success)}/{len(products)}개 완료")
        return success

    async def _download_single(self, session, product: dict) -> Optional[dict]:
        img_url = product.get("image_url", "")
        if not img_url:
            return None

        keyword   = product.get("keyword", "product")
        safe_name = re.sub(r"[^\w가-힣]", "_", product.get("name", "product"))[:30]
        filename  = f"{keyword}_{safe_name}.jpg"
        filepath  = self.session_dir / filename

        try:
            async with session.get(img_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                if resp.status != 200:
                    logger.warning(f"이미지 다운로드 실패 ({resp.status})")
                    return None

                content = await resp.read()
                if len(content) < 10 * 1024:
                    logger.warning(f"이미지 너무 작음 ({len(content)}bytes)")
                    return None

                with open(filepath, "wb") as f:
                    f.write(content)

                img = Image.open(filepath).convert("RGB")
                img.thumbnail((1080, 1080), Image.LANCZOS)
                img.save(filepath, "JPEG", quality=90, optimize=True)

                product["local_image_path"] = str(filepath)
                product["image_filename"]   = filename
                product["image_size"]       = f"{img.size[0]}x{img.size[1]}"
                logger.info(f"✅ 이미지 저장: {filename} ({img.size[0]}x{img.size[1]})")
                return product

        except Exception as e:
            logger.error(f"이미지 다운로드 오류 ({product.get('name')}): {e}")
            if filepath.exists():
                filepath.unlink()
            return None


# ──────────────────────────────────────────────
# 4. session.json 저장
# ──────────────────────────────────────────────
def save_session(
    session_dir: Path,
    session_id:  str,
    keywords:    list[dict],
    products:    list[dict]
) -> str:
    """세션 메타데이터를 session.json으로 저장"""
    session_data = {
        "session_id":    session_id,
        "created_at":    datetime.now().isoformat(),
        "keywords":      keywords,
        "products":      [
            {
                "name":             p.get("name"),
                "price":            p.get("price"),
                "mall_name":        p.get("mall_name"),
                "keyword":          p.get("keyword"),
                "keyword_reason":   p.get("keyword_reason"),
                "product_category": p.get("product_category"),
                "purchase_rate":    p.get("purchase_rate"),
                "affiliate_url":    p.get("affiliate_url"),
                "product_url":      p.get("product_url"),
                "local_image_path": p.get("local_image_path"),
                "image_filename":   p.get("image_filename"),
                "image_size":       p.get("image_size"),
                "source":           p.get("source"),
            }
            for p in products
        ],
        "total_products": len(products),
        "status":         "ready",   # ready → scripting → video_generated → uploaded
    }

    session_file = session_dir / "session.json"
    with open(session_file, "w", encoding="utf-8") as f:
        json.dump(session_data, f, ensure_ascii=False, indent=2)

    logger.info(f"💾 session.json 저장 완료: {session_file}")
    return str(session_file)


# ──────────────────────────────────────────────
# 5. 세션 목록 조회 유틸
# ──────────────────────────────────────────────
def list_sessions() -> list[dict]:
    """저장된 세션 목록 반환 (최신순)"""
    sessions = []
    for d in sorted(BASE_INPUT_DIR.iterdir(), reverse=True):
        if not d.is_dir():
            continue
        session_file = d / "session.json"
        if session_file.exists():
            with open(session_file, encoding="utf-8") as f:
                data = json.load(f)
            sessions.append({
                "session_id":     data.get("session_id"),
                "created_at":     data.get("created_at"),
                "total_products": data.get("total_products"),
                "status":         data.get("status"),
                "path":           str(d),
            })
    return sessions


def load_session(session_id: str) -> Optional[dict]:
    """특정 세션 ID로 session.json 로드"""
    session_dir  = BASE_INPUT_DIR / session_id
    session_file = session_dir / "session.json"
    if not session_file.exists():
        logger.error(f"세션을 찾을 수 없음: {session_id}")
        return None
    with open(session_file, encoding="utf-8") as f:
        return json.load(f)


# ──────────────────────────────────────────────
# 6. 통합 파이프라인
# ──────────────────────────────────────────────
class ProductSearchPipeline:
    def __init__(self):
        self.coupang_api = CoupangAPIClient()
        self.naver       = NaverShoppingClient()

    async def run(self, keywords: list[dict], products_per_keyword: int = 3) -> tuple[list[dict], str]:
        """
        반환: (상품 목록, session_id)
        """
        logger.info("=" * 50)
        logger.info("STEP 2 — 상품 검색 + 이미지 다운로드 시작")
        logger.info("=" * 50)

        # 세션 폴더 생성
        session_dir, session_id = create_session_dir()
        downloader = ProductImageDownloader(session_dir)

        all_products = []

        for kw_data in keywords:
            keyword = kw_data.get("keyword", "")
            if not keyword:
                continue

            logger.info(f"🔍 '{keyword}' 상품 검색 중...")

            if self.coupang_api.is_available():
                logger.info("→ 쿠팡 파트너스 API 사용")
                products = self.coupang_api.search_products(keyword, limit=products_per_keyword)
            else:
                logger.info("→ 네이버 쇼핑 API 사용")
                products = self.naver.search_products(keyword, limit=products_per_keyword)

            for p in products:
                p["keyword_reason"]   = kw_data.get("reason", "")
                p["product_category"] = kw_data.get("product_category", "")
                p["purchase_rate"]    = kw_data.get("estimated_purchase_rate", "")

            all_products.extend(products)
            time.sleep(0.3)

        # 이미지 다운로드
        if all_products:
            all_products = await downloader.download_images(all_products)

        # session.json 저장
        session_file = save_session(session_dir, session_id, keywords, all_products)

        logger.info("=" * 50)
        logger.info(f"STEP 2 완료 — 세션: {session_id} | 총 {len(all_products)}개 상품")
        for p in all_products:
            logger.info(f"  ✅ {p.get('name')} | {p.get('price')}원 | {p.get('image_size')}")
        logger.info(f"  💾 세션 파일: {session_file}")
        logger.info("=" * 50)

        return all_products, session_id


# ──────────────────────────────────────────────
# 단독 테스트
# ──────────────────────────────────────────────
if __name__ == "__main__":
    # 세션 목록 확인
    existing = list_sessions()
    if existing:
        print(f"\n📋 기존 세션 목록 ({len(existing)}개):")
        for s in existing:
            print(f"  - {s['session_id']} | {s['created_at'][:16]} | 상품 {s['total_products']}개 | {s['status']}")

    # 새 세션 실행
    test_keywords = [
        {"keyword": "헬스", "reason": "건강 관심 증가", "product_category": "헬스용품", "estimated_purchase_rate": "높음"},
        {"keyword": "캠핑", "reason": "캠핑 인기",      "product_category": "캠핑용품", "estimated_purchase_rate": "중간"},
    ]

    pipeline           = ProductSearchPipeline()
    results, session_id = asyncio.run(pipeline.run(test_keywords, products_per_keyword=3))

    print(f"\n✅ 세션 ID: {session_id}")
    print(f"✅ 수집 완료: {len(results)}개 상품")
    for r in results:
        print(f"  - [{r.get('mall_name')}] {r.get('name')} | {r.get('price')}원")
        print(f"    이미지: {r.get('local_image_path', 'N/A')}")

    # 세션 재로드 테스트
    print(f"\n🔄 세션 재로드 테스트: {session_id}")
    loaded = load_session(session_id)
    print(f"  → 상품 {loaded['total_products']}개, 상태: {loaded['status']}")