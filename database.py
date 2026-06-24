"""
DB 연결 + 세션 관리 + 설정 관리 + 중복 체크 + 헬퍼 함수
SQLite 파일: ~/vids-auto-engine/vids.db
DB_URL 환경변수로 PostgreSQL 전환 가능.
"""

import os
import json
import difflib
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Optional
from loguru import logger

from sqlalchemy import create_engine, func
from sqlalchemy.orm import sessionmaker

import sys
sys.path.insert(0, os.path.expanduser("~/vids-auto-engine/vids-app"))
from src.db.models import (
    Base, Session, Product, Content, ContentPublish,
    Revenue, Setting, AffiliatePlatform, Channel,
    RunMode, ProductStatus, ContentType, PublishStatus,
    ApiStatus, ActiveStatus,
)

DB_URL = os.getenv("DB_URL", f"sqlite:///{os.path.expanduser('~/vids-auto-engine/vids.db')}")
engine = create_engine(DB_URL, echo=False, future=True)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False, future=True)


# ──────────────────────────────────────────────
# 초기화
# ──────────────────────────────────────────────
def init_db():
    """테이블 생성 + 기본 데이터 삽입"""
    Base.metadata.create_all(engine)
    with get_db() as db:
        # 기본 설정값
        for key, (value, desc) in Setting.DEFAULTS.items():
            if not db.query(Setting).filter_by(key=key).first():
                db.add(Setting(key=key, value=value, description=desc))

        # 기본 제휴 플랫폼
        for p in AffiliatePlatform.DEFAULTS:
            if not db.query(AffiliatePlatform).filter_by(name=p["name"]).first():
                db.add(AffiliatePlatform(
                    name=p["name"], display_name=p["display_name"],
                    api_status=ApiStatus(p["api_status"]),
                    commission_rate=p["commission_rate"],
                    link_prefix=p["link_prefix"],
                    status=ActiveStatus(p["status"]),
                ))

        # 기본 채널
        for c in Channel.DEFAULTS:
            if not db.query(Channel).filter_by(name=c["name"]).first():
                db.add(Channel(
                    name=c["name"], display_name=c["display_name"],
                    content_type=ContentType(c["content_type"]),
                    status=ActiveStatus(c["status"]),
                ))

    logger.info(f"✅ DB 초기화 완료: {DB_URL}")


@contextmanager
def get_db():
    db = SessionLocal()
    try:
        yield db
        db.commit()
    except Exception as e:
        db.rollback()
        logger.error(f"DB 오류, 롤백: {e}")
        raise
    finally:
        db.close()


# ──────────────────────────────────────────────
# 설정
# ──────────────────────────────────────────────
def get_setting(key: str, default: str = "") -> str:
    with get_db() as db:
        row = db.query(Setting).filter_by(key=key).first()
        return row.value if row else default


def set_setting(key: str, value: str):
    with get_db() as db:
        row = db.query(Setting).filter_by(key=key).first()
        if row:
            row.value = value
        else:
            db.add(Setting(key=key, value=value))
    logger.info(f"⚙️  설정 변경: {key} = {value}")


def get_all_settings() -> dict:
    with get_db() as db:
        rows = db.query(Setting).all()
        return {r.key: {"value": r.value, "description": r.description} for r in rows}


# ──────────────────────────────────────────────
# 제휴 플랫폼
# ──────────────────────────────────────────────
def get_platform_id(name: str) -> Optional[int]:
    with get_db() as db:
        row = db.query(AffiliatePlatform).filter_by(name=name).first()
        return row.id if row else None


def get_all_platforms() -> list[dict]:
    with get_db() as db:
        rows = db.query(AffiliatePlatform).all()
        return [{
            "id": p.id, "name": p.name, "display_name": p.display_name,
            "api_available": p.api_available,
            "api_status": p.api_status.value,
            "commission_rate": p.commission_rate,
            "status": p.status.value,
        } for p in rows]


def update_platform_api_status(name: str, api_status: str, api_available: bool = False):
    with get_db() as db:
        row = db.query(AffiliatePlatform).filter_by(name=name).first()
        if row:
            row.api_status    = ApiStatus(api_status)
            row.api_available = api_available
    logger.info(f"🛒 플랫폼 API 상태 변경: {name} → {api_status}")


# ──────────────────────────────────────────────
# 채널
# ──────────────────────────────────────────────
def get_channel_id(name: str) -> Optional[int]:
    with get_db() as db:
        row = db.query(Channel).filter_by(name=name).first()
        return row.id if row else None


def get_active_channels() -> list[dict]:
    with get_db() as db:
        rows = db.query(Channel).filter_by(status=ActiveStatus.ACTIVE).all()
        return [{
            "id": c.id, "name": c.name, "display_name": c.display_name,
            "content_type": c.content_type.value,
            "account_name": c.account_name,
        } for c in rows]


# ──────────────────────────────────────────────
# 중복 체크
# ──────────────────────────────────────────────
def check_duplicate_keyword(keyword: str) -> Optional[dict]:
    days  = int(get_setting("duplicate_days", "14"))
    since = datetime.now() - timedelta(days=days)
    with get_db() as db:
        row = (db.query(Product)
               .filter(Product.keyword == keyword)
               .filter(Product.created_at >= since)
               .filter(Product.status != ProductStatus.ARCHIVED)
               .first())
        if row:
            return {"id": row.id, "name": row.name,
                    "created_at": row.created_at.isoformat()}
        return None


def check_duplicate_product(product_name: str) -> Optional[dict]:
    threshold = float(get_setting("duplicate_threshold", "0.7"))
    days      = int(get_setting("duplicate_days", "14"))
    since     = datetime.now() - timedelta(days=days)
    with get_db() as db:
        existing = (db.query(Product)
                    .filter(Product.created_at >= since)
                    .filter(Product.status != ProductStatus.ARCHIVED)
                    .all())
    for p in existing:
        ratio = difflib.SequenceMatcher(None, product_name, p.name).ratio()
        if ratio >= threshold:
            logger.warning(f"⚠️  유사 상품: '{product_name[:20]}' ≈ '{p.name[:20]}' ({ratio:.0%})")
            return {"id": p.id, "name": p.name,
                    "similarity": round(ratio, 3),
                    "created_at": p.created_at.isoformat()}
    return None


def check_category_frequency(category: str) -> bool:
    max_per_week = int(get_setting("category_max_per_week", "3"))
    since        = datetime.now() - timedelta(days=7)
    with get_db() as db:
        count = (db.query(func.count(Product.id))
                 .filter(Product.category.contains(category))
                 .filter(Product.created_at >= since)
                 .filter(Product.status != ProductStatus.ARCHIVED)
                 .scalar() or 0)
    if count >= max_per_week:
        logger.warning(f"⚠️  카테고리 과다: '{category}' 최근 7일 {count}개")
        return True
    return False


def run_duplicate_check(keyword: str, product_name: str, category: str = "") -> dict:
    dup_kw = check_duplicate_keyword(keyword)
    if dup_kw:
        return {"passed": False,
                "reason": f"최근 동일 키워드 '{keyword}' 상품 존재",
                "detail": dup_kw}
    dup_name = check_duplicate_product(product_name)
    if dup_name:
        return {"passed": False,
                "reason": f"유사 상품 존재 (유사도 {dup_name['similarity']:.0%})",
                "detail": dup_name}
    if category and check_category_frequency(category):
        return {"passed": False,
                "reason": f"'{category}' 이번 주 허용 수 초과",
                "detail": {}}
    return {"passed": True, "reason": "중복 없음", "detail": {}}


# ──────────────────────────────────────────────
# 세션
# ──────────────────────────────────────────────
def create_session(session_id: str, mode: str = "semi_auto",
                   keyword_summary: str = "",
                   platform_name: str = "coupang") -> int:
    platform_id = get_platform_id(platform_name)
    with get_db() as db:
        existing = db.query(Session).filter_by(session_id=session_id).first()
        if existing:
            return existing.id
        s = Session(session_id=session_id, mode=RunMode(mode),
                    keyword_summary=keyword_summary,
                    platform_id=platform_id)
        db.add(s)
        db.flush()
        return s.id


def update_session_status(session_id: str, status: str):
    with get_db() as db:
        s = db.query(Session).filter_by(session_id=session_id).first()
        if s:
            s.status = status


# ──────────────────────────────────────────────
# 상품
# ──────────────────────────────────────────────
def add_product(session_id: str, product_data: dict,
                platform_name: str = "coupang") -> int:
    platform_id = get_platform_id(platform_name)
    with get_db() as db:
        s = db.query(Session).filter_by(session_id=session_id).first()
        if not s:
            logger.error(f"세션 없음: {session_id}")
            return -1
        p = Product(
            session_id=s.id,
            platform_id=platform_id,
            name=product_data.get("name", ""),
            keyword=product_data.get("keyword", ""),
            category=product_data.get("product_category",
                     product_data.get("category", "")),
            price=str(product_data.get("price", "")),
            mall_name=product_data.get("mall_name", ""),
            product_url=product_data.get("product_url", ""),
            affiliate_url=product_data.get("affiliate_url", ""),
            link_source=product_data.get("source", ""),
            image_path=product_data.get("local_image_path", ""),
            image_url=product_data.get("image_url", ""),
            reason=product_data.get("keyword_reason",
                   product_data.get("reason", "")),
            purchase_rate=product_data.get("purchase_rate", ""),
        )
        db.add(p)
        db.flush()
        return p.id


def update_product_status(product_id: int, status: str):
    with get_db() as db:
        p = db.query(Product).filter_by(id=product_id).first()
        if p:
            p.status = ProductStatus(status)


def update_product_link(product_id: int, affiliate_url: str,
                        link_source: str = "manual"):
    with get_db() as db:
        p = db.query(Product).filter_by(id=product_id).first()
        if p:
            p.affiliate_url = affiliate_url
            p.link_source   = link_source
            p.status        = ProductStatus.LINKED


def get_products_by_status(status: str, limit: int = 50) -> list[dict]:
    with get_db() as db:
        rows = (db.query(Product)
                .filter_by(status=ProductStatus(status))
                .order_by(Product.created_at.desc())
                .limit(limit).all())
        return [_product_to_dict(p) for p in rows]


def get_recent_products(limit: int = 20) -> list[dict]:
    with get_db() as db:
        rows = (db.query(Product)
                .order_by(Product.created_at.desc())
                .limit(limit).all())
        return [_product_to_dict(p) for p in rows]


def _product_to_dict(p: Product) -> dict:
    return {
        "id": p.id, "name": p.name, "keyword": p.keyword,
        "category": p.category, "price": p.price,
        "affiliate_url": p.affiliate_url, "link_source": p.link_source,
        "image_path": p.image_path,
        "status": p.status.value if p.status else "",
        "reason": p.reason,
        "platform_id": p.platform_id,
        "created_at": p.created_at.isoformat() if p.created_at else "",
    }


# ──────────────────────────────────────────────
# 콘텐츠
# ──────────────────────────────────────────────
def add_content(product_id: int, content_type: str, title: str = "",
                file_path: str = "", meta: dict = None) -> int:
    with get_db() as db:
        c = Content(
            product_id=product_id,
            content_type=ContentType(content_type),
            title=title, file_path=file_path,
            meta_json=json.dumps(meta, ensure_ascii=False) if meta else "",
        )
        db.add(c)
        db.flush()
        return c.id


def update_content_feedback(content_id: int, score: int, passed: bool,
                            issues: list, suggestions: list):
    with get_db() as db:
        c = db.query(Content).filter_by(id=content_id).first()
        if c:
            c.feedback_score       = score
            c.feedback_passed      = passed
            c.feedback_issues      = json.dumps(issues, ensure_ascii=False)
            c.feedback_suggestions = json.dumps(suggestions, ensure_ascii=False)
            c.retry_count         += 1


def get_pending_contents() -> list[dict]:
    with get_db() as db:
        rows = (db.query(Content)
                .join(ContentPublish, isouter=True)
                .filter(
                    (ContentPublish.id == None) |
                    (ContentPublish.status.in_([PublishStatus.DRAFT, PublishStatus.PENDING]))
                )
                .order_by(Content.created_at.desc()).all())
        return [{"id": c.id, "type": c.content_type.value,
                 "title": c.title, "file_path": c.file_path,
                 "feedback_score": c.feedback_score,
                 "feedback_passed": c.feedback_passed} for c in rows]


# ──────────────────────────────────────────────
# 발행 이력
# ──────────────────────────────────────────────
def add_content_publish(content_id: int, channel_name: str) -> int:
    channel_id = get_channel_id(channel_name)
    with get_db() as db:
        cp = ContentPublish(
            content_id=content_id,
            channel_id=channel_id,
            status=PublishStatus.PENDING,
        )
        db.add(cp)
        db.flush()
        return cp.id


def mark_published(publish_id: int, publish_url: str):
    with get_db() as db:
        cp = db.query(ContentPublish).filter_by(id=publish_id).first()
        if cp:
            cp.status       = PublishStatus.PUBLISHED
            cp.publish_url  = publish_url
            cp.published_at = datetime.now()


def mark_publish_failed(publish_id: int):
    with get_db() as db:
        cp = db.query(ContentPublish).filter_by(id=publish_id).first()
        if cp:
            cp.status = PublishStatus.FAILED


# ──────────────────────────────────────────────
# 수익
# ──────────────────────────────────────────────
def add_revenue(product_id: int, date: str, clicks: int = 0,
                orders: int = 0, revenue: float = 0.0,
                platform_name: str = "coupang", channel_name: str = ""):
    platform_id = get_platform_id(platform_name)
    channel_id  = get_channel_id(channel_name) if channel_name else None
    with get_db() as db:
        r = Revenue(product_id=product_id, platform_id=platform_id,
                    channel_id=channel_id, date=date,
                    clicks=clicks, orders=orders, revenue=revenue)
        db.add(r)


def get_revenue_summary(platform_name: str = None) -> dict:
    with get_db() as db:
        q = db.query(Revenue)
        if platform_name:
            pid = get_platform_id(platform_name)
            q   = q.filter_by(platform_id=pid)
        total_rev    = q.with_entities(func.sum(Revenue.revenue)).scalar() or 0.0
        total_clicks = q.with_entities(func.sum(Revenue.clicks)).scalar() or 0
        total_orders = q.with_entities(func.sum(Revenue.orders)).scalar() or 0
        return {
            "total_revenue": round(total_rev, 2),
            "total_clicks":  total_clicks,
            "total_orders":  total_orders,
            "conversion":    round(total_orders / total_clicks * 100, 2)
                             if total_clicks else 0,
        }


# ──────────────────────────────────────────────
# 대시보드
# ──────────────────────────────────────────────
def get_dashboard_stats() -> dict:
    with get_db() as db:
        published_count = (db.query(func.count(ContentPublish.id))
                           .filter_by(status=PublishStatus.PUBLISHED)
                           .scalar() or 0)
        pending_count   = (db.query(func.count(ContentPublish.id))
                           .filter(ContentPublish.status.in_(
                               [PublishStatus.DRAFT, PublishStatus.PENDING]))
                           .scalar() or 0)
        linked_count    = (db.query(func.count(Product.id))
                           .filter_by(status=ProductStatus.LINKED)
                           .scalar() or 0)

        # 플랫폼별 수익
        platforms = db.query(AffiliatePlatform).filter_by(status=ActiveStatus.ACTIVE).all()
        platform_revenue = {}
        for p in platforms:
            rev = (db.query(func.sum(Revenue.revenue))
                   .filter_by(platform_id=p.id).scalar() or 0.0)
            platform_revenue[p.display_name] = round(rev, 2)

        # 채널별 발행 수
        channels = db.query(Channel).filter_by(status=ActiveStatus.ACTIVE).all()
        channel_publishes = {}
        for c in channels:
            cnt = (db.query(func.count(ContentPublish.id))
                   .filter_by(channel_id=c.id,
                               status=PublishStatus.PUBLISHED)
                   .scalar() or 0)
            channel_publishes[c.display_name] = cnt

        return {
            "total_sessions":  db.query(func.count(Session.id)).scalar() or 0,
            "total_products":  db.query(func.count(Product.id)).scalar() or 0,
            "total_contents":  db.query(func.count(Content.id)).scalar() or 0,
            "linked_products": linked_count,
            "published":       published_count,
            "pending":         pending_count,
            "platform_revenue":  platform_revenue,
            "channel_publishes": channel_publishes,
            "revenue":         get_revenue_summary(),
        }


# ──────────────────────────────────────────────
# 테스트
# ──────────────────────────────────────────────
# ══════════════════════════════════════════════════
# variant(비교 영상) 선택 헬퍼 — database.py에 추가할 함수들
# ══════════════════════════════════════════════════

def add_content_variant(
    product_id: int,
    content_type: str,
    variant_group: str,
    variant_mode: str = "single",
    selected: bool = True,
    title: str = "",
    file_path: str = "",
    meta: dict = None,
) -> int:
    """
    variant 정보를 포함해 콘텐츠 기록
    - single/persona 모드: selected=True (바로 발행 가능)
    - both 모드: selected=False (선택 대기)
    """
    with get_db() as db:
        c = Content(
            product_id=product_id,
            content_type=ContentType(content_type),
            title=title, file_path=file_path,
            meta_json=json.dumps(meta, ensure_ascii=False) if meta else "",
            variant_group=variant_group,
            variant_mode=variant_mode,
            selected=selected,
        )
        db.add(c)
        db.flush()
        return c.id


def get_variants_by_group(variant_group: str) -> list[dict]:
    """같은 비교군의 콘텐츠 목록 조회"""
    with get_db() as db:
        rows = (db.query(Content)
                .filter_by(variant_group=variant_group)
                .order_by(Content.id).all())
        return [{
            "id": c.id,
            "title": c.title,
            "file_path": c.file_path,
            "variant_mode": c.variant_mode,
            "selected": c.selected,
            "feedback_score": c.feedback_score,
        } for c in rows]


def get_pending_variant_groups() -> list[dict]:
    """선택 대기 중인 비교군 목록 (both 모드로 생성됐으나 아직 선택 안 된 것)"""
    with get_db() as db:
        # selected=False인 콘텐츠가 있는 그룹 = 아직 선택 안 됨
        rows = (db.query(Content)
                .filter(Content.variant_group != "")
                .filter(Content.variant_mode.in_(["persona", "single"]))
                .all())
        # 그룹별로 묶기
        groups = {}
        for c in rows:
            g = c.variant_group
            if g not in groups:
                groups[g] = {"variant_group": g, "variants": [], "decided": False}
            groups[g]["variants"].append({
                "id": c.id, "title": c.title,
                "variant_mode": c.variant_mode,
                "file_path": c.file_path,
                "selected": c.selected,
            })
        # 2개 이상이면서 아직 아무것도 선택 안 됐거나, 선택 대기인 그룹만
        pending = []
        for g, data in groups.items():
            variants = data["variants"]
            if len(variants) >= 2:
                # 하나라도 selected=False면 선택 대기로 간주
                any_selected = any(v["selected"] for v in variants)
                # both 모드는 둘 다 처음 False → 선택 전
                if not any_selected:
                    pending.append(data)
        return pending


def select_variant(content_id: int) -> dict:
    """
    비교군에서 하나를 선택 → 나머지는 보관(archived)
    선택된 콘텐츠 정보 반환
    """
    with get_db() as db:
        chosen = db.query(Content).filter_by(id=content_id).first()
        if not chosen:
            return {}

        group = chosen.variant_group
        # 같은 그룹의 모든 콘텐츠
        siblings = db.query(Content).filter_by(variant_group=group).all()

        for c in siblings:
            if c.id == content_id:
                c.selected = True
            else:
                c.selected = False
                # 탈락한 콘텐츠의 발행 이력도 보관 처리
                for pub in c.publishes:
                    pub.status = PublishStatus.FAILED  # archived 대용

        return {
            "id": chosen.id,
            "title": chosen.title,
            "variant_mode": chosen.variant_mode,
            "file_path": chosen.file_path,
            "product_id": chosen.product_id,
        }


def get_selected_content(variant_group: str) -> dict:
    """비교군에서 선택된 콘텐츠 반환"""
    with get_db() as db:
        c = (db.query(Content)
             .filter_by(variant_group=variant_group, selected=True)
             .first())
        if not c:
            return {}
        return {
            "id": c.id, "title": c.title,
            "file_path": c.file_path,
            "variant_mode": c.variant_mode,
            "product_id": c.product_id,
        }


if __name__ == "__main__":
    init_db()
    print("\n=== DB 레이어 테스트 ===")

    # 플랫폼/채널 확인
    print("\n📦 제휴 플랫폼:")
    for p in get_all_platforms():
        print(f"  [{p['id']}] {p['display_name']} | API: {p['api_status']} | 수수료: {p['commission_rate']}%")

    print("\n📺 활성 채널:")
    for c in get_active_channels():
        print(f"  [{c['id']}] {c['display_name']} ({c['content_type']})")

    # 세션/상품 테스트
    sid = "2026-06-22_test"
    create_session(sid, mode="semi_auto",
                   keyword_summary="헬스, 캠핑", platform_name="coupang")
    print(f"\n✅ 세션 생성: {sid}")

    pid = add_product(sid, {
        "name": "천국의계단 스텝퍼 가정용 홈트",
        "keyword": "헬스", "price": "139000",
        "product_category": "헬스용품",
        "affiliate_url": "", "source": "manual",
        "reason": "건강 관심 증가",
    }, platform_name="coupang")
    print(f"✅ 상품 추가: id={pid}")

    update_product_link(pid, "https://link.coupang.com/a/sample", "manual")

    cid = add_content(pid, "shorts", title="10분 홈트",
                      file_path="/path/shorts_01.mp4")
    print(f"✅ 콘텐츠 추가: id={cid}")

    update_content_feedback(cid, score=85, passed=True,
                            issues=[], suggestions=["CTA 강화 추천"])

    pub_id = add_content_publish(cid, "youtube_shorts")
    mark_published(pub_id, "https://youtube.com/shorts/xxx")
    print(f"✅ 발행 기록: id={pub_id}")

    print("\n=== 중복 체크 ===")
    tests = [
        ("헬스",  "가정용 스텝밀 계단오르기", "헬스용품"),
        ("캠핑",  "원터치 텐트 4인용",        "캠핑용품"),
    ]
    for kw, name, cat in tests:
        r = run_duplicate_check(kw, name, cat)
        print(f"  {'✅' if r['passed'] else '❌'} [{kw}] {name[:25]} → {r['reason']}")

    print("\n=== 대시보드 ===")
    print(json.dumps(get_dashboard_stats(), ensure_ascii=False, indent=2))