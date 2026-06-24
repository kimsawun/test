"""
발행 파이프라인 — 유튜브 쇼츠 + 인스타 릴스 통합 업로드
pipeline.py의 STEP 7~8 담당

발행 전략:
  1. 유튜브 쇼츠 (설명란에 파트너스 링크)
  2. 인스타 릴스 (바이오 링크로 유도)

자동화 여부:
  youtube_auto_publish = true/false
  instagram_auto_publish = true/false
"""

import os
import sys
import asyncio
from pathlib import Path
from loguru import logger
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.expanduser("~/vids-auto-engine/.env"))
sys.path.insert(0, os.path.expanduser("~/vids-auto-engine/vids-app"))

from src.publish.youtube_uploader   import YouTubeUploader
from src.publish.instagram_uploader import InstagramUploader
from src.db.database import (
    add_content_publish, mark_published, mark_publish_failed,
    get_setting, get_pending_contents,
)


async def publish_video(
    product: dict,
    session_id: str,
) -> dict:
    """
    영상 한 개를 유튜브 + 인스타에 발행
    설정에 따라 자동/수동 선택
    """
    video_path    = product.get("video_path", "")
    title         = product.get("script_data", {}).get("script", {}).get("title", product.get("name",""))
    affiliate_url = product.get("affiliate_url", "")
    hashtags      = product.get("script_data", {}).get("script", {}).get("hashtags", [])
    content_id    = product.get("content_id")

    if not video_path or not Path(video_path).exists():
        logger.warning(f"영상 파일 없음: {video_path}")
        return {"success": False, "reason": "영상 파일 없음"}

    results = {}

    # ── 유튜브 업로드 ──
    yt_auto = get_setting("youtube_auto_publish", "false").lower() == "true"
    if yt_auto:
        logger.info(f"📤 유튜브 업로드: {title[:30]}")
        yt_uploader = YouTubeUploader()
        if yt_uploader.is_authenticated():
            pub_id = add_content_publish(content_id, "youtube_shorts") if content_id else None
            yt_result = yt_uploader.upload_shorts(
                video_path=video_path,
                title=title,
                description=f"{product.get('name','')} 상품 리뷰 #Shorts",
                tags=hashtags,
                affiliate_url=affiliate_url,
                privacy="public",
            )
            if yt_result.get("video_url"):
                if pub_id:
                    mark_published(pub_id, yt_result["video_url"])
                results["youtube"] = yt_result
                logger.info(f"  ✅ 유튜브: {yt_result['video_url']}")
            else:
                if pub_id:
                    mark_publish_failed(pub_id)
                results["youtube"] = {"error": yt_result.get("error", "실패")}
        else:
            logger.warning("유튜브 미인증 — 스킵")
            results["youtube"] = {"skipped": "인증 필요"}
    else:
        logger.info("유튜브 자동 발행 비활성화 — 수동 발행 대기")
        results["youtube"] = {"pending": "수동 발행 대기"}

    # ── 인스타 업로드 ──
    ig_auto = get_setting("instagram_auto_publish", "false").lower() == "true"
    if ig_auto:
        logger.info(f"📤 인스타 업로드: {title[:30]}")
        ig_uploader = InstagramUploader()
        if ig_uploader.auth.is_authenticated():
            pub_id = add_content_publish(content_id, "instagram_reels") if content_id else None
            ig_result = ig_uploader.upload_reels(
                video_path=video_path,
                title=title,
                tags=hashtags,
                affiliate_url=affiliate_url,
            )
            if ig_result.get("media_url"):
                if pub_id:
                    mark_published(pub_id, ig_result["media_url"])
                results["instagram"] = ig_result
                logger.info(f"  ✅ 인스타: {ig_result['media_url']}")
            else:
                if pub_id:
                    mark_publish_failed(pub_id)
                results["instagram"] = {"error": ig_result.get("error", "실패")}
        else:
            logger.warning("인스타 미인증 — 스킵")
            results["instagram"] = {"skipped": "계정 정보 필요"}
    else:
        logger.info("인스타 자동 발행 비활성화 — 수동 발행 대기")
        results["instagram"] = {"pending": "수동 발행 대기"}

    success = any(
        r.get("video_url") or r.get("media_url")
        for r in results.values()
        if isinstance(r, dict)
    )

    return {
        "success": success,
        "results": results,
        "title":   title,
    }


async def run_publish_pipeline(
    products: list[dict],
    session_id: str,
) -> list[dict]:
    """
    여러 상품 영상을 순서대로 발행
    (동시 업로드는 계정 제한 위험 → 순차 처리)
    """
    results = []
    for i, p in enumerate(products):
        logger.info(f"[{i+1}/{len(products)}] 발행 시작: {p.get('name','')[:30]}")
        result = await publish_video(p, session_id)
        p["publish_result"] = result
        results.append(p)

        # 업로드 간 딜레이 (계정 제한 방지)
        if i < len(products) - 1:
            import time
            time.sleep(10)

    published = sum(1 for p in results if p.get("publish_result", {}).get("success"))
    logger.info(f"✅ 발행 완료: {published}/{len(results)}개")
    return results


# ──────────────────────────────────────────────
# 텔레그램 발행 상태 요약
# ──────────────────────────────────────────────
def get_publish_summary(products: list[dict]) -> str:
    """발행 결과 텔레그램 메시지용 요약"""
    lines = ["📊 *발행 결과 요약*\n"]

    for p in products:
        name   = p.get("name", "")[:20]
        pr     = p.get("publish_result", {})
        results = pr.get("results", {})

        yt_icon = "✅" if results.get("youtube", {}).get("video_url") else \
                  "⏳" if results.get("youtube", {}).get("pending") else "❌"
        ig_icon = "✅" if results.get("instagram", {}).get("media_url") else \
                  "⏳" if results.get("instagram", {}).get("pending") else "❌"

        lines.append(f"📦 {name}")
        lines.append(f"  유튜브: {yt_icon} | 인스타: {ig_icon}")

        if results.get("youtube", {}).get("video_url"):
            lines.append(f"  🔗 {results['youtube']['video_url']}")
        if results.get("instagram", {}).get("media_url"):
            lines.append(f"  🔗 {results['instagram']['media_url']}")
        lines.append("")

    return "\n".join(lines)


# ──────────────────────────────────────────────
# 단독 테스트
# ──────────────────────────────────────────────
if __name__ == "__main__":
    print("=== 발행 설정 확인 ===")
    print(f"유튜브 자동 발행: {get_setting('youtube_auto_publish', 'false')}")
    print(f"인스타 자동 발행: {get_setting('instagram_auto_publish', 'false')}")

    yt = YouTubeUploader()
    ig = InstagramUploader()
    print(f"\n유튜브 인증: {'✅' if yt.is_authenticated() else '❌ 미인증'}")
    print(f"인스타 인증: {'✅' if ig.auth.is_authenticated() else '❌ 미인증'}")

    print("\n발행을 시작하려면:")
    print("  유튜브: python src/publish/youtube_uploader.py")
    print("  인스타: .env에 INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD 추가")
    print("\n자동 발행 활성화:")
    print("  /set youtube_auto_publish true")
    print("  /set instagram_auto_publish true")
