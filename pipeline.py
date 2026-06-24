"""
파이프라인 오케스트레이터 — STEP 1~4를 DB와 연결해서 순서대로 실행
- trend_mode (current/seasonal/both) 지원
- platform_id, channel_id DB 연동
- 반자동/완전자동 모드 공통 코어
"""

import os
import sys
import json
import asyncio
from pathlib import Path
from datetime import datetime
from loguru import logger

sys.path.insert(0, os.path.expanduser("~/vids-auto-engine/vids-app"))

from src.db.database import (
    init_db, create_session, update_session_status,
    add_product, update_product_status, update_product_link,
    add_content, add_content_publish, mark_published,
    get_setting, run_duplicate_check, get_dashboard_stats,
    get_platform_id, get_channel_id,
)
from src.db.models import ProductStatus, ContentType

BASE_INPUT_DIR = Path(os.path.expanduser("~/vids-auto-engine/vids-backend/input"))


# ──────────────────────────────────────────────
# 세션 ID 생성
# ──────────────────────────────────────────────
def make_session_id() -> str:
    today    = datetime.now().strftime("%Y-%m-%d")
    existing = sorted([
        d for d in BASE_INPUT_DIR.iterdir()
        if d.is_dir() and d.name.startswith(today)
    ]) if BASE_INPUT_DIR.exists() else []
    count = int(existing[-1].name.split("_")[-1]) + 1 if existing else 1
    return f"{today}_{count:03d}"


# ──────────────────────────────────────────────
# STEP 1: 키워드 추출
# ──────────────────────────────────────────────
def step1_keywords() -> list[dict]:
    from src.keyword.trend_extractor import TrendKeywordPipeline
    max_kw     = int(get_setting("max_keywords", "5"))
    trend_mode = get_setting("trend_mode", "both")
    logger.info(f"STEP 1 — 키워드 추출 (모드: {trend_mode}, 최대 {max_kw}개)")
    pipeline = TrendKeywordPipeline()
    keywords = pipeline.run()
    return keywords[:max_kw]


# ──────────────────────────────────────────────
# STEP 2: 상품 검색 + 이미지 + DB 기록
# ──────────────────────────────────────────────
async def step2_products(
    session_id: str,
    keywords: list[dict],
    platform_name: str = "coupang",
) -> list[dict]:
    from src.product.coupang_crawler import ProductSearchPipeline
    ppp = int(get_setting("products_per_keyword", "3"))
    logger.info(f"STEP 2 — 상품 검색 (키워드당 {ppp}개, 플랫폼: {platform_name})")

    pipeline = ProductSearchPipeline()
    products, _ = await pipeline.run(keywords, products_per_keyword=ppp)

    db_products = []
    for p in products:
        # 중복 체크
        dup = run_duplicate_check(
            p.get("keyword", ""),
            p.get("name", ""),
            p.get("product_category", "")
        )
        if not dup["passed"]:
            logger.warning(f"⏭️  중복 스킵: {p.get('name', '')[:30]} — {dup['reason']}")
            continue

        # trend_mode 정보 전달
        p["trend_mode"]           = p.get("trend_mode", "current")
        p["seasonal_target_date"] = p.get("seasonal_target_date", "")

        pid = add_product(session_id, p, platform_name=platform_name)
        if pid > 0:
            p["db_id"] = pid
            db_products.append(p)
            logger.info(f"  DB 기록: {p.get('name', '')[:30]} (id={pid})")

    logger.info(f"STEP 2 완료 — {len(db_products)}개 상품 (중복 제외)")
    return db_products


# ──────────────────────────────────────────────
# STEP 3: 대본 생성
# ──────────────────────────────────────────────
def step3_scripts(session_id: str, products: list[dict]) -> list[dict]:
    from src.script.persona_writer import PersonaScriptWriter
    writer  = PersonaScriptWriter()
    results = []

    for p in products:
        logger.info(f"STEP 3 — 대본 생성: {p.get('name', '')[:30]}")
        try:
            script_data = writer.generate(p)
            p["script_data"] = script_data
            update_product_status(p["db_id"], "producing")
            results.append(p)
        except Exception as e:
            logger.error(f"대본 생성 실패: {e}")

    logger.info(f"STEP 3 완료 — {len(results)}개 대본")
    return results


# ──────────────────────────────────────────────
# STEP 4: TTS + 영상 합성 + DB 기록
# ──────────────────────────────────────────────
async def step4_videos(session_id: str, products: list[dict]) -> list[dict]:
    from src.video.tts_generator import TTSGenerator
    from src.video.video_composer import compose_video

    session_dir = BASE_INPUT_DIR / session_id
    video_dir   = session_dir / "videos"
    audio_dir   = session_dir / "audio"
    video_dir.mkdir(parents=True, exist_ok=True)
    audio_dir.mkdir(parents=True, exist_ok=True)

    content_types = get_setting("content_types", "shorts,blog").split(",")
    results       = []

    for i, p in enumerate(products):
        script_data = p.get("script_data", {})
        script      = script_data.get("script", {})
        if not script:
            continue

        logger.info(f"STEP 4 — 영상 생성: {script.get('title', '')[:30]}")
        try:
            # TTS
            gender     = "female" if i % 2 == 0 else "male"
            gen        = TTSGenerator(voice_gender=gender)
            audio_info = await gen.generate_script(script, audio_dir, i)

            # 영상 합성
            out_file   = video_dir / f"shorts_{i+1:02d}.mp4"
            video_path = compose_video(
                audio_info,
                p.get("local_image_path", ""),
                out_file,
                add_bgm=True,
            )

            if video_path:
                # 콘텐츠 타입별 DB 기록
                for ctype in content_types:
                    ctype = ctype.strip()
                    if ctype in ("shorts", "reels"):
                        cid = add_content(
                            p["db_id"], "shorts",
                            title=script.get("title", ""),
                            file_path=video_path,
                            meta={
                                "hashtags":           script.get("hashtags", []),
                                "cta":                script.get("cta", ""),
                                "affiliate_url":      p.get("affiliate_url", ""),
                                "trend_mode":         p.get("trend_mode", "current"),
                                "seasonal_target_date": p.get("seasonal_target_date", ""),
                            }
                        )
                        # 채널별 발행 이력 초기화
                        if ctype == "shorts":
                            add_content_publish(cid, "youtube_shorts")
                        elif ctype == "reels":
                            add_content_publish(cid, "instagram_reels")

                        p["content_id"] = cid
                        p["video_path"] = video_path

                update_product_status(p["db_id"], "published")
                logger.info(f"  ✅ 영상 완성: {video_path}")
                results.append(p)

        except Exception as e:
            logger.error(f"영상 생성 실패: {e}")

    logger.info(f"STEP 4 완료 — {len(results)}개 영상")


# ──────────────────────────────────────────────
# STEP 5: 피드백 검토
# ──────────────────────────────────────────────
def step5_feedback(products: list[dict]) -> list[dict]:
    """대본 피드백 검토 — 미통과 시 개선사항 주입"""
    from src.feedback.content_reviewer import ContentReviewer, review_with_retry
    reviewer = ContentReviewer()
    results  = []

    for p in products:
        script_data = p.get("script_data", {})
        script      = script_data.get("script", {})
        content_id  = p.get("content_id")

        if not script:
            results.append(p)
            continue

        logger.info(f"STEP 5 — 피드백: {script.get('title','')[:30]}")
        feedback = review_with_retry(
            reviewer=reviewer,
            content_type="script",
            content_data=script,
            product_data=p,
            content_id=content_id,
        )
        p["feedback"] = feedback
        results.append(p)

    logger.info(f"STEP 5 완료 — {len(results)}개 검토")
    return results


# ──────────────────────────────────────────────
# STEP 6: 블로그 생성 + 발행
# ──────────────────────────────────────────────
async def step6_blog(session_id: str, products: list[dict]) -> list[dict]:
    """블로그 글 생성 + 티스토리 발행"""
    from src.blog.blog_pipeline import run_blog_pipeline
    content_types = get_setting("content_types", "shorts,blog").split(",")

    if "blog" not in [c.strip() for c in content_types]:
        logger.info("블로그 미활성화 (content_types 설정 확인)")
        return products

    results = []
    for p in products:
        logger.info(f"STEP 6 — 블로그: {p.get('name','')[:30]}")
        try:
            blog_result = await run_blog_pipeline(
                product_data=p,
                session_id=session_id,
                auto_publish=None,
            )
            p["blog_result"] = blog_result
            if blog_result.get("post_url"):
                logger.info(f"  ✅ 블로그 발행: {blog_result['post_url']}")
            else:
                logger.info(f"  📄 블로그 저장 완료 (발행 대기)")
        except Exception as e:
            logger.error(f"블로그 생성 실패: {e}")
        results.append(p)

    logger.info(f"STEP 6 완료 — {len(results)}개 블로그")
    return results
    return results


# ──────────────────────────────────────────────
# 완전자동 파이프라인
# ──────────────────────────────────────────────
async def run_full_auto() -> dict:
    logger.info("=" * 60)
    logger.info("🚀 완전자동 파이프라인 시작")
    logger.info("=" * 60)

    init_db()
    session_id    = make_session_id()
    platform_name = get_setting("default_platform", "coupang")
    trend_mode    = get_setting("trend_mode", "both")

    # STEP 1
    keywords = step1_keywords()
    if not keywords:
        logger.error("키워드 추출 실패")
        return {"success": False, "reason": "키워드 없음"}

    kw_summary = ", ".join([k.get("keyword", "") for k in keywords])
    create_session(
        session_id, mode="full_auto",
        keyword_summary=kw_summary,
        platform_name=platform_name,
    )
    update_session_status(session_id, "running")

    # STEP 2
    products = await step2_products(session_id, keywords, platform_name)
    if not products:
        update_session_status(session_id, "no_products")
        return {"success": False, "reason": "유효 상품 없음 (중복 제외)"}

    # STEP 3
    products = step3_scripts(session_id, products)

    # STEP 4
    products = await step4_videos(session_id, products)

    # STEP 5: 피드백
    products = step5_feedback(products)

    # STEP 6: 블로그
    products = await step6_blog(session_id, products)

    # STEP 7~8: 유튜브 + 인스타 발행
    from src.publish.publish_pipeline import run_publish_pipeline
    products = await run_publish_pipeline(products, session_id)

    update_session_status(session_id, "completed")
    stats = get_dashboard_stats()

    logger.info("=" * 60)
    logger.info(f"✅ 완전자동 완료 — 세션: {session_id}")
    logger.info(f"   플랫폼: {platform_name} | 트렌드 모드: {trend_mode}")
    logger.info(f"   영상 {len(products)}개 | 블로그 {sum(1 for p in products if p.get('blog_result'))}개")
    logger.info("=" * 60)

    return {
        "success":    True,
        "session_id": session_id,
        "platform":   platform_name,
        "trend_mode": trend_mode,
        "products":   len(products),
        "videos":     [p.get("video_path", "") for p in products],
        "blogs":      [p.get("blog_result", {}).get("post_url", "") for p in products],
        "stats":      stats,
    }


# ──────────────────────────────────────────────
# 반자동 파이프라인 (관리자가 상품/링크 직접 입력)
# ──────────────────────────────────────────────
async def run_semi_auto(
    manual_products: list[dict],
    platform_name: str = "coupang",
) -> dict:
    logger.info("=" * 60)
    logger.info("🎯 반자동 파이프라인 시작")
    logger.info("=" * 60)

    init_db()
    session_id = make_session_id()
    create_session(
        session_id, mode="semi_auto",
        keyword_summary=", ".join([p.get("keyword", "") for p in manual_products]),
        platform_name=platform_name,
    )
    update_session_status(session_id, "running")

    # 상품 DB 기록 (중복 체크 없이 — 관리자가 직접 선택)
    for p in manual_products:
        pid = add_product(session_id, p, platform_name=platform_name)
        p["db_id"] = pid
        if p.get("affiliate_url"):
            update_product_link(pid, p["affiliate_url"], "manual")

    # STEP 3~4
    products = step3_scripts(session_id, manual_products)
    products = await step4_videos(session_id, products)

    # STEP 5: 피드백
    products = step5_feedback(products)

    # STEP 6: 블로그
    products = await step6_blog(session_id, products)

    # STEP 7~8: 유튜브 + 인스타 발행
    from src.publish.publish_pipeline import run_publish_pipeline
    products = await run_publish_pipeline(products, session_id)

    update_session_status(session_id, "completed")
    logger.info(
        f"✅ 반자동 완료 — 세션: {session_id} | "
        f"영상 {len(products)}개 | "
        f"블로그 {sum(1 for p in products if p.get('blog_result'))}개"
    )

    return {
        "success":    True,
        "session_id": session_id,
        "platform":   platform_name,
        "products":   len(products),
        "videos":     [p.get("video_path", "") for p in products],
        "blogs":      [p.get("blog_result", {}).get("post_url", "") for p in products],
    }


# ──────────────────────────────────────────────
# 단독 테스트
# ──────────────────────────────────────────────
if __name__ == "__main__":
    from src.product.coupang_crawler import list_sessions, load_session

    sessions = list_sessions()
    if not sessions:
        print("❌ 세션 없음. STEP 2 먼저 실행.")
        import sys; sys.exit(1)

    target = None
    for s in sessions:
        sf = BASE_INPUT_DIR / s["session_id"] / "session.json"
        if sf.exists():
            target = s["session_id"]
            break

    if not target:
        print("❌ session.json 없음.")
        import sys; sys.exit(1)

    session  = load_session(target)
    products = session.get("products", [])[:1]

    if not products:
        print("❌ 상품 없음.")
        import sys; sys.exit(1)

    print(f"📋 반자동 테스트: {products[0].get('name', '')[:40]}")
    print(f"🔗 파트너스 링크: {products[0].get('affiliate_url', '없음')}\n")

    result = asyncio.run(run_semi_auto(products, platform_name="coupang"))
    print(f"\n{'='*50}")
    print(f"결과: {result}")
    print(f"\n대시보드 통계:")
    print(json.dumps(get_dashboard_stats(), ensure_ascii=False, indent=2))
