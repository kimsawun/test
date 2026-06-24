"""
STEP 8 — 인스타그램 릴스 자동 업로드
instagrapi 라이브러리 사용 (이미 설치됨)

인스타 링크 전략:
  - 캡션에 링크 텍스트로 표시 (클릭 안됨 — 인스타 정책)
  - 바이오 링크 업데이트 (클릭 가능)
  - "링크 인 바이오" 문구로 유도

주의:
  - 하루 업로드 권장: 3~5개
  - 너무 많으면 계정 제한 위험
"""

import os
import sys
import json
import time
from pathlib import Path
from loguru import logger
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.expanduser("~/vids-auto-engine/.env"))
sys.path.insert(0, os.path.expanduser("~/vids-auto-engine/vids-app"))

from src.db.database import get_setting, set_setting

SESSION_FILE = Path(os.path.expanduser("~/vids-auto-engine/vids-app/instagram_session.json"))

INSTAGRAM_USERNAME = os.getenv("INSTAGRAM_USERNAME", "")
INSTAGRAM_PASSWORD = os.getenv("INSTAGRAM_PASSWORD", "")


# ──────────────────────────────────────────────
# 인증
# ──────────────────────────────────────────────
class InstagramAuth:
    """instagrapi 세션 관리"""

    def __init__(self):
        self._client = None

    def get_client(self):
        """instagrapi 클라이언트 반환 (세션 자동 관리)"""
        if self._client:
            return self._client

        try:
            from instagrapi import Client
        except ImportError:
            logger.error("instagrapi 미설치: pip install instagrapi")
            return None

        client = Client()

        # 저장된 세션 로드
        if SESSION_FILE.exists():
            try:
                client.load_settings(str(SESSION_FILE))
                client.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
                logger.info("✅ 인스타그램 세션 복원")
                self._client = client
                return client
            except Exception as e:
                logger.warning(f"세션 복원 실패, 재로그인: {e}")

        # 새 로그인
        try:
            client.login(INSTAGRAM_USERNAME, INSTAGRAM_PASSWORD)
            client.dump_settings(str(SESSION_FILE))
            logger.info("✅ 인스타그램 로그인 완료")
            self._client = client
            return client
        except Exception as e:
            logger.error(f"인스타그램 로그인 실패: {e}")
            return None

    def is_authenticated(self) -> bool:
        return bool(INSTAGRAM_USERNAME and INSTAGRAM_PASSWORD)


# ──────────────────────────────────────────────
# 업로드
# ──────────────────────────────────────────────
class InstagramUploader:

    def __init__(self):
        self.auth = InstagramAuth()

    def upload_reels(
        self,
        video_path: str,
        title: str,
        tags: list[str] = None,
        affiliate_url: str = "",
        update_bio: bool = True,
    ) -> dict:
        """
        인스타그램 릴스 업로드

        링크 전략:
          - 캡션: 파트너스 링크 텍스트 표시 (클릭 불가)
          - 바이오: 파트너스 링크로 업데이트 (클릭 가능)
          - 캡션에 "링크 인 바이오 👆" 안내
        """
        if not Path(video_path).exists():
            logger.error(f"영상 파일 없음: {video_path}")
            return {}

        client = self.auth.get_client()
        if not client:
            return {"error": "인증 필요"}

        # 캡션 구성
        caption = self._build_caption(title, tags, affiliate_url)

        try:
            logger.info(f"📤 인스타 릴스 업로드 시작: {title[:30]}")

            # 커버 이미지 (썸네일) 자동 생성 — 영상 첫 프레임
            thumbnail = self._extract_thumbnail(video_path)

            media = client.clip_upload(
                path=Path(video_path),
                caption=caption,
                thumbnail=Path(thumbnail) if thumbnail else None,
            )

            media_id  = str(media.pk)
            media_url = f"https://www.instagram.com/reel/{media.code}/"

            logger.info(
                f"✅ 인스타 업로드 완료\n"
                f"   URL: {media_url}"
            )

            # 바이오 링크 업데이트 (파트너스 링크)
            if update_bio and affiliate_url:
                self._update_bio_link(client, affiliate_url, title)

            return {
                "media_id":  media_id,
                "media_url": media_url,
                "title":     title,
            }

        except Exception as e:
            logger.error(f"인스타 업로드 실패: {e}")
            return {"error": str(e)}

    def _build_caption(
        self,
        title: str,
        tags: list[str] = None,
        affiliate_url: str = "",
    ) -> str:
        """릴스 캡션 구성"""
        parts = [title]

        if affiliate_url:
            parts.append(
                "\n🛒 구매 링크는 프로필 바이오에서 확인하세요! 👆\n"
                f"(링크: {affiliate_url})\n"
                "※ 쿠팡 파트너스 링크입니다."
            )
        else:
            parts.append("\n🛒 구매 링크는 프로필에서 확인하세요!")

        # 해시태그
        if tags:
            hashtags = " ".join([f"#{t.replace(' ','')}" for t in tags[:15]])
            parts.append(f"\n{hashtags} #릴스 #쇼핑 #추천")

        return "\n".join(parts)[:2200]  # 인스타 캡션 최대 2200자

    def _extract_thumbnail(self, video_path: str) -> str:
        """영상 첫 프레임을 썸네일로 추출"""
        try:
            thumb_path = str(Path(video_path).with_suffix(".jpg"))
            os.system(
                f'~/vids-auto-engine/vids-backend/assets/bin/ffmpeg '
                f'-y -ss 0.5 -i "{video_path}" -vframes 1 '
                f'"{thumb_path}" -loglevel quiet'
            )
            return thumb_path if Path(thumb_path).exists() else ""
        except:
            return ""

    def _update_bio_link(self, client, url: str, title: str):
        """프로필 바이오 링크 업데이트"""
        try:
            client.account_edit(
                biography=(
                    f"🛒 최근 추천: {title[:20]}\n"
                    f"👇 구매 링크\n"
                    f"{url}"
                )
            )
            logger.info(f"✅ 바이오 링크 업데이트: {url[:50]}")
        except Exception as e:
            logger.warning(f"바이오 업데이트 실패: {e}")


# ──────────────────────────────────────────────
# 단독 테스트
# ──────────────────────────────────────────────
if __name__ == "__main__":
    uploader = InstagramUploader()

    if not uploader.auth.is_authenticated():
        print("❌ 인스타그램 계정 정보 없음")
        print(".env에 추가해주세요:")
        print("INSTAGRAM_USERNAME=your_username")
        print("INSTAGRAM_PASSWORD=your_password")
    else:
        print(f"✅ 계정: {INSTAGRAM_USERNAME}")
        client = uploader.auth.get_client()
        if client:
            print("✅ 로그인 성공!")
        else:
            print("❌ 로그인 실패")
