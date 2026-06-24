"""
쿠팡 상품 페이지 스크래핑
URL에서 상품명, 가격, 이미지, 카테고리 추출
Playwright 사용 (봇 차단 우회)
"""

import os
import sys
import json
import re
import asyncio
from pathlib import Path
from loguru import logger
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.expanduser("~/vids-auto-engine/.env"))
sys.path.insert(0, os.path.expanduser("~/vids-auto-engine/vids-app"))

BASE_INPUT_DIR = Path(os.path.expanduser("~/vids-auto-engine/vids-backend/input"))


# ──────────────────────────────────────────────
# 파트너스 링크 파싱
# ──────────────────────────────────────────────
def parse_partners_input(raw: str) -> dict:
    """
    파트너스 입력값 파싱 — 두 가지 형식 지원
    1. HTML 배너: <a href="https://link.coupang.com/a/xxx" ...>...</a>
    2. 링크만:    https://link.coupang.com/a/xxx

    반환:
      link  — 파트너스 단축 URL (수익 링크)
      html  — 블로그 삽입용 HTML (배너 형식이면 원본, 링크만이면 생성)
      image — 배너 이미지 URL (있으면)
      alt   — 배너 이미지 alt 텍스트 (상품명)
    """
    raw = raw.strip()

    # HTML 배너 형식
    href_match  = re.search(r'href=["\']([^"\']+)["\']', raw)
    src_match   = re.search(r'src=["\']([^"\']+)["\']', raw)
    alt_match   = re.search(r'alt=["\']([^"\']+)["\']', raw)

    if href_match:
        link  = href_match.group(1)
        image = src_match.group(1) if src_match else ""
        alt   = alt_match.group(1) if alt_match else ""
        html  = raw  # 원본 HTML 그대로
        logger.info(f"파트너스 HTML 배너 파싱 완료: {link[:50]}")
        return {"link": link, "html": html, "image": image, "alt": alt, "type": "banner"}

    # 링크만 있는 경우
    if raw.startswith("http"):
        link = raw
        html = f'<a href="{link}" target="_blank" referrerpolicy="unsafe-url">쿠팡에서 구매하기</a>'
        logger.info(f"파트너스 링크 파싱 완료: {link[:50]}")
        return {"link": link, "html": html, "image": "", "alt": "", "type": "link"}

    logger.error(f"파트너스 링크 파싱 실패: {raw[:50]}")
    return {}


# ──────────────────────────────────────────────
# 쿠팡 상품 ID 추출
# ──────────────────────────────────────────────
def extract_product_id(url: str) -> str:
    """
    쿠팡 URL에서 상품 ID 추출
    https://www.coupang.com/vp/products/9344037189?itemId=...
    → 9344037189
    """
    match = re.search(r"/products/(\d+)", url)
    return match.group(1) if match else ""


# ──────────────────────────────────────────────
# 쿠팡 상품 스크래핑 (Playwright)
# ──────────────────────────────────────────────
async def scrape_coupang_product(url: str) -> dict:
    """
    쿠팡 상품 URL에서 상품 정보 추출
    반환: name, price, images, category, description, rating, review_count
    """
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        logger.error("playwright 미설치. pip install playwright && playwright install chromium")
        return {}

    product_data = {}

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 800},
            locale="ko-KR",
        )
        page = await context.new_page()

        try:
            logger.info(f"🔍 쿠팡 상품 스크래핑: {url[:60]}")
            await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            await page.wait_for_timeout(2000)

            # 상품명
            name = ""
            for selector in [
                "h1.prod-buy-header__title",
                ".prod-title",
                "h1[class*='title']",
                "#contents h1",
            ]:
                try:
                    el = await page.query_selector(selector)
                    if el:
                        name = (await el.inner_text()).strip()
                        if name:
                            break
                except:
                    continue

            # 가격
            price = ""
            for selector in [
                ".prod-price .total-price strong",
                ".prod-buy-price .price-value",
                "[class*='price'] strong",
                ".total-price strong",
            ]:
                try:
                    el = await page.query_selector(selector)
                    if el:
                        price_text = (await el.inner_text()).strip()
                        price = re.sub(r"[^\d]", "", price_text)
                        if price:
                            break
                except:
                    continue

            # 이미지 목록
            images = []
            for selector in [
                ".prod-image__detail img",
                ".thumb-nail-list img",
                "[class*='prod-image'] img",
                ".detail-item-imgs img",
            ]:
                try:
                    els = await page.query_selector_all(selector)
                    for el in els[:5]:
                        src = await el.get_attribute("src")
                        if src and src.startswith("http") and src not in images:
                            images.append(src)
                    if images:
                        break
                except:
                    continue

            # 카테고리 (breadcrumb)
            category = ""
            try:
                breadcrumbs = await page.query_selector_all(".breadcrumb li, [class*='breadcrumb'] a")
                cats = []
                for bc in breadcrumbs[:4]:
                    text = (await bc.inner_text()).strip()
                    if text and text not in ("홈", ">"):
                        cats.append(text)
                category = " > ".join(cats)
            except:
                pass

            # 평점
            rating = ""
            try:
                el = await page.query_selector("[class*='rating'] .score, .prod-rating .rating")
                if el:
                    rating = (await el.inner_text()).strip()
            except:
                pass

            # 리뷰 수
            review_count = ""
            try:
                el = await page.query_selector("[class*='review-count'], .count")
                if el:
                    review_count = re.sub(r"[^\d]", "", (await el.inner_text()).strip())
            except:
                pass

            product_data = {
                "name":         name,
                "price":        price,
                "images":       images,
                "category":     category,
                "rating":       rating,
                "review_count": review_count,
                "product_url":  url,
                "product_id":   extract_product_id(url),
            }

            logger.info(
                f"✅ 스크래핑 완료: {name[:30]} | "
                f"가격: {price}원 | 이미지: {len(images)}장"
            )

        except Exception as e:
            logger.error(f"스크래핑 실패: {e}")
        finally:
            await browser.close()

    return product_data


# ──────────────────────────────────────────────
# 이미지 다운로드
# ──────────────────────────────────────────────
async def download_product_images(
    images: list[str],
    session_dir: Path,
    max_images: int = 5,
) -> list[str]:
    """상품 이미지 다운로드 → 로컬 경로 반환"""
    import aiohttp
    img_dir = session_dir / "images"
    img_dir.mkdir(parents=True, exist_ok=True)

    local_paths = []
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 Safari/537.36"
        )
    }

    async with aiohttp.ClientSession(headers=headers) as session:
        for i, url in enumerate(images[:max_images]):
            try:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status == 200:
                        ext  = url.split(".")[-1].split("?")[0][:4] or "jpg"
                        path = img_dir / f"product_{i+1:02d}.{ext}"
                        with open(path, "wb") as f:
                            f.write(await resp.read())
                        local_paths.append(str(path))
                        logger.info(f"  이미지 다운로드: {path.name}")
            except Exception as e:
                logger.warning(f"  이미지 다운로드 실패 ({url[:40]}): {e}")

    logger.info(f"✅ 이미지 {len(local_paths)}장 다운로드 완료")
    return local_paths


# ──────────────────────────────────────────────
# 통합 — URL + 파트너스 링크 → 상품 데이터 완성
# ──────────────────────────────────────────────
async def prepare_product_from_coupang(
    product_url: str,
    partners_raw: str,
    session_id: str,
    keyword: str = "",
) -> dict:
    """
    쿠팡 URL + 파트너스 링크/배너 → 파이프라인용 상품 데이터 생성

    반환: pipeline.run_semi_auto()에 넘길 수 있는 product_data dict
    """
    # 1. 파트너스 링크 파싱
    partners = parse_partners_input(partners_raw)
    if not partners:
        logger.error("파트너스 링크 파싱 실패")
        return {}

    # 2. 쿠팡 상품 스크래핑
    product = await scrape_coupang_product(product_url)
    if not product:
        logger.error("쿠팡 상품 스크래핑 실패")
        return {}

    # 3. 이미지 다운로드
    session_dir = BASE_INPUT_DIR / session_id
    local_images = []
    if product.get("images"):
        local_images = await download_product_images(
            product["images"], session_dir
        )

    # 4. 파이프라인용 데이터 합성
    product_data = {
        "name":             product.get("name", ""),
        "keyword":          keyword or product.get("name", "")[:10],
        "product_category": product.get("category", ""),
        "price":            product.get("price", ""),
        "product_url":      product_url,
        "affiliate_url":    partners.get("link", ""),
        "partners_html":    partners.get("html", ""),   # 블로그 배너 HTML
        "partners_type":    partners.get("type", ""),
        "local_image_path": local_images[0] if local_images else "",
        "all_image_paths":  local_images,
        "source":           "coupang_direct",
        "platform_name":    "coupang",
        "rating":           product.get("rating", ""),
        "review_count":     product.get("review_count", ""),
        "reason":           f"쿠팡 직접 등록 상품",
    }

    logger.info(
        f"✅ 상품 데이터 준비 완료: {product_data['name'][:30]}\n"
        f"   파트너스 링크: {partners['link'][:50]}"
    )
    return product_data


# ──────────────────────────────────────────────
# 단독 테스트
# ──────────────────────────────────────────────
if __name__ == "__main__":
    # 테스트용
    TEST_URL = "https://www.coupang.com/vp/products/9344037189?itemId=27713845650"
    TEST_PARTNERS = '<a href="https://link.coupang.com/a/eNfirPAAge" target="_blank" referrerpolicy="unsafe-url"><img src="https://image2.coupangcdn.com/image/affiliate/banner/97bcb4a4525b90e1ce7e044e3b1ef0b6@2x.jpg" alt="마스크" width="120" height="240"></a>'

    async def test():
        print("=== 파트너스 링크 파싱 ===")
        parsed = parse_partners_input(TEST_PARTNERS)
        print(f"링크: {parsed.get('link')}")
        print(f"이미지: {parsed.get('image', '')[:60]}")
        print(f"타입: {parsed.get('type')}")

        print("\n=== 쿠팡 상품 스크래핑 ===")
        product = await prepare_product_from_coupang(
            TEST_URL, TEST_PARTNERS,
            session_id="2026-06-22_test",
            keyword="마스크"
        )
        if product:
            print(f"상품명: {product['name']}")
            print(f"가격: {product['price']}원")
            print(f"카테고리: {product['product_category']}")
            print(f"이미지: {len(product['all_image_paths'])}장")
            print(f"파트너스: {product['affiliate_url'][:50]}")
        else:
            print("스크래핑 실패")

    asyncio.run(test())