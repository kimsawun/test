"""
STEP 7 — 유튜브 쇼츠 자동 업로드
Google YouTube Data API v3 사용
OAuth 2.0 인증 (최초 1회, 이후 토큰 자동 갱신)

쇼츠 조건:
  - 세로 영상 (9:16)
  - 60초 이하
  - 제목/설명에 #Shorts 포함
"""

import os
import sys
import json
import pickle
from pathlib import Path
from loguru import logger
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.expanduser("~/vids-auto-engine/.env"))
sys.path.insert(0, os.path.expanduser("~/vids-auto-engine/vids-app"))

from src.db.database import get_setting, set_setting

TOKEN_FILE  = Path(os.path.expanduser("~/vids-auto-engine/vids-app/youtube_token.pickle"))
SCOPES      = ["https://www.googleapis.com/auth/youtube.upload"]
CREDENTIALS_FILE = os.path.expanduser("~/vids-auto-engine/vids-app/youtube_credentials.json")


# ──────────────────────────────────────────────
# 인증
# ──────────────────────────────────────────────
def get_youtube_service():
    """YouTube API 서비스 객체 반환 (토큰 자동 관리)"""
    try:
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError:
        logger.error(
            "Google API 패키지 미설치\n"
            "pip install google-api-python-client google-auth-oauthlib"
        )
        return None

    creds = None

    # 저장된 토큰 로드
    if TOKEN_FILE.exists():
        with open(TOKEN_FILE, "rb") as f:
            creds = pickle.load(f)

    # 토큰 갱신 또는 새 인증
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
            logger.info("✅ YouTube 토큰 자동 갱신")
        else:
            if not Path(CREDENTIALS_FILE).exists():
                logger.error(
                    f"YouTube OAuth 자격증명 파일 없음: {CREDENTIALS_FILE}\n"
                    "Google Cloud Console에서 OAuth 2.0 클라이언트 ID를 생성하고\n"
                    "youtube_credentials.json 으로 저장해주세요."
                )
                return None
            flow  = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
            creds = flow.run_local_server(port=8080)
            logger.info("✅ YouTube 새 인증 완료")

        # 토큰 저장
        with open(TOKEN_FILE, "wb") as f:
            pickle.dump(creds, f)

    return build("youtube", "v3", credentials=creds)


# ──────────────────────────────────────────────
# 업로드
# ──────────────────────────────────────────────
class YouTubeUploader:

    def __init__(self):
        self.service = None

    def _get_service(self):
        if not self.service:
            self.service = get_youtube_service()
        return self.service

    def is_authenticated(self) -> bool:
        return TOKEN_FILE.exists()

    def upload_shorts(
        self,
        video_path: str,
        title: str,
        description: str,
        tags: list[str] = None,
        affiliate_url: str = "",
        privacy: str = None,
    ) -> dict:
        """
        유튜브 쇼츠 업로드

        privacy: public / unlisted / private
        """
        if not Path(video_path).exists():
            logger.error(f"영상 파일 없음: {video_path}")
            return {}

        service = self._get_service()
        if not service:
            return {"error": "인증 필요"}

        # 공개 설정
        if privacy is None:
            auto = get_setting("youtube_auto_publish", "false")
            privacy = "public" if auto.lower() == "true" else "private"

        # 쇼츠용 설명 구성
        # 파트너스 링크는 설명란에 삽입 (인스타와 달리 클릭 가능)
        shorts_desc = self._build_description(description, affiliate_url, tags)

        # 쇼츠 태그 (#Shorts 포함)
        shorts_tags = list(tags or []) + ["Shorts", "쇼츠"]

        body = {
            "snippet": {
                "title":       self._shorts_title(title),
                "description": shorts_desc,
                "tags":        shorts_tags[:30],  # 최대 30개
                "categoryId":  "22",  # People & Blogs
            },
            "status": {
                "privacyStatus":          privacy,
                "selfDeclaredMadeForKids": False,
            },
        }

        try:
            from googleapiclient.http import MediaFileUpload
            media = MediaFileUpload(
                video_path,
                mimetype="video/mp4",
                resumable=True,
                chunksize=1024*1024*5,  # 5MB 청크
            )

            logger.info(f"📤 유튜브 업로드 시작: {title[:30]}")
            request = service.videos().insert(
                part="snippet,status",
                body=body,
                media_body=media,
            )

            response = None
            while response is None:
                status, response = request.next_chunk()
                if status:
                    pct = int(status.progress() * 100)
                    logger.info(f"  업로드 진행: {pct}%")

            video_id  = response.get("id", "")
            video_url = f"https://www.youtube.com/shorts/{video_id}"

            logger.info(
                f"✅ 유튜브 업로드 완료\n"
                f"   제목: {title[:40]}\n"
                f"   URL: {video_url}\n"
                f"   공개: {privacy}"
            )
            return {
                "video_id":  video_id,
                "video_url": video_url,
                "title":     title,
                "privacy":   privacy,
            }

        except Exception as e:
            logger.error(f"유튜브 업로드 실패: {e}")
            return {"error": str(e)}

    def _shorts_title(self, title: str) -> str:
        """쇼츠 제목 최적화 (100자 이내, #Shorts 포함)"""
        if "#Shorts" not in title and "#shorts" not in title:
            title = f"{title} #Shorts"
        return title[:100]

    def _build_description(
        self,
        description: str,
        affiliate_url: str,
        tags: list[str] = None,
    ) -> str:
        """쇼츠 설명란 구성"""
        parts = []

        if description:
            parts.append(description[:500])

        if affiliate_url:
            parts.append(
                f"\n🛒 상품 구매 링크:\n{affiliate_url}\n"
                f"\n※ 이 링크는 쿠팡 파트너스 링크로, 구매 시 일정 수수료가 발생합니다."
            )

        if tags:
            hashtags = " ".join([f"#{t.replace(' ','')}" for t in tags[:10]])
            parts.append(f"\n{hashtags} #Shorts #쇼츠")

        return "\n".join(parts)[:5000]  # 유튜브 설명 최대 5000자


# ──────────────────────────────────────────────
# 인증 가이드 출력
# ──────────────────────────────────────────────
def print_auth_guide():
    """YouTube OAuth 설정 가이드"""
    print("""
=== 유튜브 업로드 인증 설정 가이드 ===

1. Google Cloud Console (console.cloud.google.com) 접속
2. 프로젝트 생성 또는 선택
3. YouTube Data API v3 활성화
4. OAuth 2.0 클라이언트 ID 생성
   - 애플리케이션 유형: 데스크톱 앱
5. 클라이언트 보안 비밀 JSON 다운로드
6. 파일명을 youtube_credentials.json으로 변경 후
   ~/vids-auto-engine/vids-app/ 에 저장

pip install google-api-python-client google-auth-oauthlib

설정 완료 후:
  python src/publish/youtube_uploader.py
  → 브라우저 열림 → 구글 로그인 → 완료
""")


# ──────────────────────────────────────────────
# 단독 테스트
# ──────────────────────────────────────────────
if __name__ == "__main__":
    uploader = YouTubeUploader()

    if not uploader.is_authenticated():
        print_auth_guide()
        print("\n인증을 시작합니다...")
        service = get_youtube_service()
        if service:
            print("✅ 인증 완료!")
        else:
            print("❌ 인증 실패. 위 가이드를 확인해주세요.")
    else:
        print("✅ 유튜브 인증 상태: OK")
        print("\n테스트 업로드를 하려면 영상 파일 경로를 입력하세요:")
        path = input("영상 경로 (엔터로 스킵): ").strip()
        if path and Path(path).exists():
            result = uploader.upload_shorts(
                video_path=path,
                title="테스트 쇼츠 #Shorts",
                description="자동 업로드 테스트",
                tags=["테스트", "쇼츠"],
                affiliate_url="",
                privacy="private",
            )
            print(f"결과: {result}")
