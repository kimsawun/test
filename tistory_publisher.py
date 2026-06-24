"""
티스토리 블로그 자동 발행 모듈
카카오 OAuth 2.0 기반 티스토리 API 사용

주요 기능:
  - Access Token 발급/저장/갱신
  - 이미지 업로드 (티스토리 이미지 서버)
  - 블로그 글 발행 (공개/비공개)
  - 카테고리 조회
"""

import os
import sys
import json
import re
import requests
import webbrowser
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs
from loguru import logger
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.expanduser("~/vids-auto-engine/.env"))
sys.path.insert(0, os.path.expanduser("~/vids-auto-engine/vids-app"))

from src.db.database import get_setting, set_setting

KAKAO_REST_API_KEY = os.getenv("KAKAO_REST_API_KEY", "")
KAKAO_REDIRECT_URI = os.getenv("KAKAO_REDIRECT_URI", "http://localhost:8000/auth/tistory/callback")
TISTORY_BLOG_NAME  = os.getenv("TISTORY_BLOG_NAME", "myops1")

TISTORY_API_BASE   = "https://www.tistory.com/apis"
KAKAO_AUTH_URL     = "https://kauth.kakao.com/oauth/authorize"
KAKAO_TOKEN_URL    = "https://kauth.kakao.com/oauth/token"


# ──────────────────────────────────────────────
# OAuth 인증
# ──────────────────────────────────────────────
class TistoryAuth:
    """카카오 OAuth 2.0 인증 관리"""

    def get_access_token(self) -> str:
        """DB에서 Access Token 조회"""
        return get_setting("tistory_access_token", "")

    def save_access_token(self, token: str):
        """Access Token DB 저장"""
        set_setting("tistory_access_token", token)
        logger.info("✅ 티스토리 Access Token 저장 완료")

    def is_authenticated(self) -> bool:
        """인증 여부 확인"""
        token = self.get_access_token()
        if not token:
            return False
        # 토큰 유효성 간단 확인
        try:
            resp = requests.get(
                f"{TISTORY_API_BASE}/blog/info",
                params={"access_token": token, "output": "json"},
                timeout=10,
            )
            return resp.status_code == 200
        except:
            return False

    def get_auth_url(self) -> str:
        """카카오 로그인 URL 생성"""
        params = {
            "client_id":     KAKAO_REST_API_KEY,
            "redirect_uri":  KAKAO_REDIRECT_URI,
            "response_type": "code",
        }
        return f"{KAKAO_AUTH_URL}?{urlencode(params)}"

    def exchange_code_for_token(self, code: str) -> str:
        """인증 코드 → Access Token 교환"""
        try:
            resp = requests.post(
                KAKAO_TOKEN_URL,
                data={
                    "grant_type":   "authorization_code",
                    "client_id":    KAKAO_REST_API_KEY,
                    "redirect_uri": KAKAO_REDIRECT_URI,
                    "code":         code,
                },
                timeout=10,
            )
            resp.raise_for_status()
            token = resp.json().get("access_token", "")
            if token:
                self.save_access_token(token)
            return token
        except Exception as e:
            logger.error(f"토큰 교환 실패: {e}")
            return ""

    def authenticate_interactive(self) -> str:
        """
        로컬 환경에서 대화형 인증
        브라우저로 카카오 로그인 → 리다이렉트 URL에서 코드 추출
        """
        auth_url = self.get_auth_url()
        print(f"\n🔗 아래 URL을 브라우저에서 열어 카카오 로그인을 완료해주세요:")
        print(f"\n{auth_url}\n")
        print("로그인 후 리다이렉트된 URL을 아래에 붙여넣어 주세요:")
        print("(예: http://localhost:8000/auth/tistory/callback?code=XXXXX)\n")

        try:
            webbrowser.open(auth_url)
        except:
            pass

        redirect_url = input("리다이렉트 URL: ").strip()
        parsed = urlparse(redirect_url)
        params = parse_qs(parsed.query)
        code   = params.get("code", [""])[0]

        if not code:
            logger.error("인증 코드를 찾을 수 없어요.")
            return ""

        token = self.exchange_code_for_token(code)
        if token:
            print(f"✅ 인증 완료! Access Token 저장됨.")
        return token


# ──────────────────────────────────────────────
# 티스토리 API
# ──────────────────────────────────────────────
class TistoryPublisher:
    """티스토리 블로그 발행"""

    def __init__(self, blog_name: str = None):
        self.auth      = TistoryAuth()
        self.blog_name = blog_name or get_setting("tistory_blog_name", TISTORY_BLOG_NAME)

    def _token(self) -> str:
        return self.auth.get_access_token()

    def _api(self, path: str, method: str = "GET", data: dict = None) -> dict:
        """API 공통 호출"""
        url    = f"{TISTORY_API_BASE}{path}"
        params = {"access_token": self._token(), "output": "json"}

        try:
            if method == "POST":
                resp = requests.post(url, params=params, data=data, timeout=30)
            else:
                resp = requests.get(url, params={**params, **(data or {})}, timeout=30)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"API 호출 실패 ({path}): {e}")
            return {}

    # ── 카테고리 조회 ──
    def get_categories(self) -> list[dict]:
        """블로그 카테고리 목록 조회"""
        result = self._api("/category/list", data={"blogName": self.blog_name})
        categories = result.get("tistory", {}).get("item", {}).get("categories", {}).get("category", [])
        if isinstance(categories, dict):
            categories = [categories]
        logger.info(f"카테고리 {len(categories)}개 조회")
        return categories

    def find_category_id(self, category_name: str) -> str:
        """카테고리 이름으로 ID 조회"""
        categories = self.get_categories()
        for cat in categories:
            if category_name in cat.get("label", ""):
                return cat.get("id", "")
        return ""

    # ── 이미지 업로드 ──
    def upload_image(self, image_path: str) -> str:
        """이미지 파일 업로드 → 티스토리 이미지 URL 반환"""
        if not Path(image_path).exists():
            logger.warning(f"이미지 없음: {image_path}")
            return ""
        try:
            url = f"{TISTORY_API_BASE}/post/attach"
            with open(image_path, "rb") as f:
                resp = requests.post(
                    url,
                    params={"access_token": self._token(), "blogName": self.blog_name},
                    files={"uploadedfile": (Path(image_path).name, f)},
                    timeout=30,
                )
            resp.raise_for_status()
            result     = resp.json()
            img_url    = result.get("tistory", {}).get("url", "")
            if img_url:
                logger.info(f"  이미지 업로드: {Path(image_path).name} → {img_url[:50]}")
            return img_url
        except Exception as e:
            logger.error(f"이미지 업로드 실패: {e}")
            return ""

    def upload_all_images(self, image_paths: list) -> dict:
        """
        여러 이미지 업로드
        반환: {로컬경로: 티스토리URL}
        """
        url_map = {}
        for path in image_paths:
            url = self.upload_image(path)
            if url:
                url_map[path] = url
        logger.info(f"✅ 이미지 {len(url_map)}/{len(image_paths)}장 업로드 완료")
        return url_map

    # ── HTML 이미지 URL 교체 ──
    def replace_image_placeholders(self, html: str, url_map: dict) -> str:
        """
        HTML의 이미지 플레이스홀더를 실제 티스토리 URL로 교체
        <!-- IMAGE_PLACEHOLDER:/path/to/img --> 패턴 처리
        {{TISTORY_IMG_1}} 패턴 처리
        """
        # 플레이스홀더 주석 제거
        html = re.sub(r'<!-- IMAGE_PLACEHOLDER:[^>]+ -->\n?', '', html)

        # {{TISTORY_IMG_N}} → 실제 URL
        paths_list = list(url_map.keys())
        for i, (local_path, remote_url) in enumerate(url_map.items()):
            placeholder = f"{{{{TISTORY_IMG_{i+1}}}}}"
            if placeholder in html:
                html = html.replace(placeholder, remote_url)
            # src 속성의 로컬 경로도 교체
            html = html.replace(f'src="{local_path}"', f'src="{remote_url}"')

        return html

    # ── 글 발행 ──
    def publish(
        self,
        title: str,
        html: str,
        tags: list[str] = None,
        category_id: str = "",
        visibility: int = None,
    ) -> dict:
        """
        블로그 글 발행

        visibility:
          0 = 비공개 (기본, 확인 후 공개)
          3 = 공개
        """
        if visibility is None:
            vis_setting = get_setting("tistory_visibility", "0")
            visibility  = int(vis_setting)

        if not category_id:
            category_id = get_setting("tistory_category_id", "")

        tag_str = ",".join(tags or [])

        data = {
            "blogName":    self.blog_name,
            "title":       title,
            "content":     html,
            "visibility":  str(visibility),
            "categoryId":  category_id,
            "tag":         tag_str,
            "acceptComment": "1",
        }

        result = self._api("/post/write", method="POST", data=data)
        post_id  = result.get("tistory", {}).get("postId", "")
        post_url = result.get("tistory", {}).get("url", "")

        if post_id:
            vis_text = "공개" if visibility == 3 else "비공개"
            logger.info(
                f"✅ 발행 완료 ({vis_text})\n"
                f"   제목: {title[:40]}\n"
                f"   URL: {post_url}"
            )
        else:
            logger.error(f"발행 실패: {result}")

        return {
            "post_id":  post_id,
            "post_url": post_url,
            "title":    title,
            "visibility": visibility,
        }

    # ── 통합 발행 ──
    def publish_blog_post(
        self,
        blog_data: dict,
        image_paths: list = None,
        auto_publish: bool = None,
    ) -> dict:
        """
        블로그 데이터 → 이미지 업로드 → 발행 통합 처리

        blog_data: blog_writer.generate() 반환값
        image_paths: 로컬 이미지 경로 목록
        auto_publish: True=공개, False=비공개, None=설정 따름
        """
        if not self.auth.is_authenticated():
            logger.error("티스토리 인증 필요. /auth 명령어로 인증해주세요.")
            return {"error": "인증 필요"}

        title      = blog_data.get("title", "")
        html       = blog_data.get("html", "")
        tags       = blog_data.get("tags", [])
        image_paths = image_paths or []

        # 1. 이미지 업로드
        url_map = {}
        if image_paths:
            logger.info(f"📸 이미지 {len(image_paths)}장 업로드 중...")
            url_map = self.upload_all_images(image_paths)

        # 2. HTML 이미지 URL 교체
        if url_map:
            html = self.replace_image_placeholders(html, url_map)

        # 3. 공개 설정 결정
        if auto_publish is None:
            setting = get_setting("tistory_auto_publish", "false")
            auto_publish = setting.lower() == "true"
        visibility = 3 if auto_publish else 0

        # 4. 카테고리 자동 매핑
        category_id = get_setting("tistory_category_id", "")
        if not category_id and blog_data.get("strategy", {}).get("category"):
            category_id = self.find_category_id(
                blog_data["strategy"]["category"]
            )

        # 5. 발행
        result = self.publish(
            title=title,
            html=html,
            tags=tags,
            category_id=category_id,
            visibility=visibility,
        )

        result["uploaded_images"] = len(url_map)
        result["auto_published"]  = auto_publish
        return result


# ──────────────────────────────────────────────
# 단독 테스트
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import sys

    publisher = TistoryPublisher()
    auth      = TistoryAuth()

    print("=== 티스토리 인증 상태 확인 ===")
    if auth.is_authenticated():
        print("✅ 인증됨")

        # 카테고리 조회
        print("\n=== 카테고리 목록 ===")
        cats = publisher.get_categories()
        for cat in cats:
            print(f"  [{cat.get('id')}] {cat.get('label')}")

        # 테스트 발행
        print("\n=== 테스트 글 발행 ===")
        test_blog = {
            "title": "[테스트] 블로그 자동 발행 확인",
            "html":  "<h2>테스트</h2><p>자동 발행 테스트입니다.</p>",
            "tags":  ["테스트", "자동발행"],
            "strategy": {"category": ""},
        }
        result = publisher.publish_blog_post(
            blog_data=test_blog,
            auto_publish=False,  # 비공개로 테스트
        )
        print(f"결과: {result}")

    else:
        print("❌ 인증 필요")
        print("\n=== 카카오 로그인 시작 ===")
        token = auth.authenticate_interactive()
        if token:
            print("✅ 인증 완료!")
        else:
            print("❌ 인증 실패")
