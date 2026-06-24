"""
블로그 파이프라인 — 상품 데이터 → 블로그 글 생성 → 티스토리 발행
pipeline.py의 블로그 담당 모듈
"""

import os
import sys
import json
from pathlib import Path
from loguru import logger
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.expanduser("~/vids-auto-engine/.env"))
sys.path.insert(0, os.path.expanduser("~/vids-auto-engine/vids-app"))

from src.blog.blog_writer import BlogWriter, determine_strategy
from src.blog.tistory_publisher import TistoryPublisher
from src.db.database import (
    add_content, add_content_publish, mark_published,
    update_product_status, get_setting,
)

BASE_INPUT_DIR = Path(os.path.expanduser("~/vids-auto-engine/vids-backend/input"))


async def run_blog_pipeline(
    product_data: dict,
    session_id: str,
    auto_publish: bool = None,
) -> dict:
    """
    상품 데이터 → 블로그 글 생성 → 티스토리 발행

    반환:
      success     — 성공 여부
      title       — 발행된 글 제목
      post_url    — 티스토리 URL
      content_id  — DB content ID
    """
    writer    = BlogWriter()
    publisher = TistoryPublisher()

    name        = product_data.get("name", "상품")
    image_paths = product_data.get("all_image_paths", [])
    db_id       = product_data.get("db_id")

    logger.info(f"📝 블로그 파이프라인 시작: {name[:30]}")

    # 1. 전략 결정
    strategy = determine_strategy(
        category=product_data.get("product_category", "생활용품"),
        image_count=len(image_paths),
        description_length=len(product_data.get("description", "")),
    )

    # 2. 블로그 글 생성
    logger.info("✍️  블로그 글 생성 중...")
    blog_data = writer.generate(
        product_data=product_data,
        strategy=strategy,
        session_id=session_id,
    )

    if not blog_data.get("html"):
        logger.error("블로그 글 생성 실패")
        return {"success": False, "reason": "글 생성 실패"}

    # 3. DB에 콘텐츠 기록 (발행 전)
    content_id = None
    if db_id:
        content_id = add_content(
            product_id=db_id,
            content_type="blog",
            title=blog_data["title"],
            file_path=str(BASE_INPUT_DIR / session_id / "blog.json"),
            meta={
                "tags":     blog_data.get("tags", []),
                "summary":  blog_data.get("summary", ""),
                "strategy": strategy,
            }
        )
        # 티스토리 채널 발행 이력 초기화
        publish_id = add_content_publish(content_id, "tistory")

    # 4. 티스토리 인증 확인
    if not publisher.auth.is_authenticated():
        logger.warning("⚠️  티스토리 인증 필요 → 비공개 임시 저장")
        # 인증 없으면 글만 저장하고 나중에 발행
        blog_file = BASE_INPUT_DIR / session_id / "blog.json"
        with open(blog_file, "w", encoding="utf-8") as f:
            json.dump({**blog_data, "product_data": product_data}, f,
                     ensure_ascii=False, indent=2)
        return {
            "success":    True,
            "title":      blog_data["title"],
            "post_url":   "",
            "content_id": content_id,
            "status":     "saved_locally",
            "reason":     "티스토리 인증 필요. /auth_tistory 로 인증 후 /publish_pending 으로 발행"
        }

    # 5. 티스토리 발행
    logger.info("🚀 티스토리 발행 중...")
    result = publisher.publish_blog_post(
        blog_data=blog_data,
        image_paths=image_paths,
        auto_publish=auto_publish,
    )

    if result.get("post_url"):
        # DB 발행 완료 기록
        if content_id and publish_id:
            mark_published(publish_id, result["post_url"])
        if db_id:
            update_product_status(db_id, "published")

        logger.info(
            f"✅ 블로그 발행 완료\n"
            f"   제목: {result['title'][:40]}\n"
            f"   URL: {result['post_url']}\n"
            f"   공개: {'공개' if result.get('auto_published') else '비공개'}"
        )
        return {
            "success":    True,
            "title":      result["title"],
            "post_url":   result["post_url"],
            "content_id": content_id,
            "published":  result.get("auto_published", False),
            "status":     "published",
        }
    else:
        logger.error(f"티스토리 발행 실패: {result}")
        return {
            "success": False,
            "reason":  result.get("error", "발행 실패"),
        }


def publish_pending_blogs(session_id: str = None) -> list[dict]:
    """
    로컬에 저장된 미발행 블로그 글을 티스토리에 발행
    (인증 후 사용)
    """
    publisher = TistoryPublisher()
    if not publisher.auth.is_authenticated():
        logger.error("티스토리 인증 필요")
        return []

    # 미발행 blog.json 파일 찾기
    search_dir = BASE_INPUT_DIR / session_id if session_id else BASE_INPUT_DIR
    blog_files = list(search_dir.rglob("blog.json"))
    results    = []

    for blog_file in blog_files:
        try:
            with open(blog_file, encoding="utf-8") as f:
                data = json.load(f)

            # 이미 발행된 것 스킵 (post_url 있으면)
            if data.get("post_url"):
                continue

            product_data = data.get("product_data", {})
            image_paths  = product_data.get("all_image_paths", [])

            result = publisher.publish_blog_post(
                blog_data=data,
                image_paths=image_paths,
            )

            if result.get("post_url"):
                # 발행 완료 표시
                data["post_url"] = result["post_url"]
                with open(blog_file, "w", encoding="utf-8") as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)

            results.append(result)
            logger.info(f"발행 완료: {result.get('title','')[:30]}")

        except Exception as e:
            logger.error(f"발행 실패 ({blog_file}): {e}")

    return results


# ──────────────────────────────────────────────
# 단독 테스트
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import asyncio

    test_product = {
        "name":             "빨아쓰는 연예인 면마스크 코오롱원단",
        "keyword":          "면마스크",
        "product_category": "패션의류",
        "price":            "15900",
        "affiliate_url":    "https://link.coupang.com/a/eNfirPAAge",
        "partners_html":    '<a href="https://link.coupang.com/a/eNfirPAAge" target="_blank" referrerpolicy="unsafe-url">구매하기</a>',
        "description":      "항균 기능이 있는 빨아쓰는 면마스크. 코오롱 원단 사용.",
        "all_image_paths":  [],
        "db_id":            None,
    }

    async def test():
        result = await run_blog_pipeline(
            product_data=test_product,
            session_id="2026-06-23_blog_test",
            auto_publish=False,
        )
        print(f"\n{'='*50}")
        print(f"결과: {result}")

    asyncio.run(test())
