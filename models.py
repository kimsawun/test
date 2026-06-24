"""
DB 모델 정의 — SQLite + SQLAlchemy 2.0
테이블:
  AffiliatePlatform — 제휴 플랫폼 (쿠팡/무신사 등)
  Channel           — 광고 채널 (유튜브/인스타/티스토리 등)
  Session           — 실행 세션
  Product           — 추천/등록 상품 이력
  Content           — 생성한 콘텐츠 (쇼츠/릴스/블로그)
  ContentPublish    — 채널별 발행 이력
  Revenue           — 수익
  Setting           — 동적 설정
"""

from datetime import datetime
from enum import Enum as PyEnum
from sqlalchemy import (
    String, Integer, Float, DateTime, Text,
    ForeignKey, Boolean, Enum
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


# ──────────────────────────────────────────────
# 열거형
# ──────────────────────────────────────────────
class RunMode(str, PyEnum):
    SEMI_AUTO = "semi_auto"
    FULL_AUTO = "full_auto"


class ProductStatus(str, PyEnum):
    RECOMMENDED = "recommended"
    SELECTED    = "selected"
    LINKED      = "linked"
    PRODUCING   = "producing"
    PUBLISHED   = "published"
    ARCHIVED    = "archived"


class ContentType(str, PyEnum):
    SHORTS = "shorts"
    REELS  = "reels"
    BLOG   = "blog"


class PublishStatus(str, PyEnum):
    DRAFT     = "draft"
    PENDING   = "pending"
    PUBLISHED = "published"
    FAILED    = "failed"


class ApiStatus(str, PyEnum):
    PENDING  = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class ActiveStatus(str, PyEnum):
    ACTIVE   = "active"
    INACTIVE = "inactive"


# ──────────────────────────────────────────────
# 1. 제휴 플랫폼 (쿠팡/무신사 등)
# ──────────────────────────────────────────────
class AffiliatePlatform(Base):
    __tablename__ = "affiliate_platforms"

    id:               Mapped[int]   = mapped_column(primary_key=True)
    name:             Mapped[str]   = mapped_column(String(50), unique=True)   # coupang / musinsa
    display_name:     Mapped[str]   = mapped_column(String(100), default="")  # 쿠팡파트너스
    api_available:    Mapped[bool]  = mapped_column(Boolean, default=False)
    api_status:       Mapped[ApiStatus] = mapped_column(Enum(ApiStatus), default=ApiStatus.PENDING)
    commission_rate:  Mapped[float] = mapped_column(Float, default=0.0)
    link_prefix:      Mapped[str]   = mapped_column(Text, default="")
    status:           Mapped[ActiveStatus] = mapped_column(Enum(ActiveStatus), default=ActiveStatus.ACTIVE)
    created_at:       Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    products: Mapped[list["Product"]] = relationship(back_populates="platform")
    revenues: Mapped[list["Revenue"]] = relationship(back_populates="platform")

    # 기본 데이터 (init_db에서 자동 삽입)
    DEFAULTS = [
        {"name": "coupang",  "display_name": "쿠팡파트너스",  "api_status": "pending",
         "commission_rate": 3.0, "link_prefix": "https://link.coupang.com/a/", "status": "active"},
        {"name": "musinsa",  "display_name": "무신사파트너스", "api_status": "pending",
         "commission_rate": 5.0, "link_prefix": "https://musinsa.com/", "status": "inactive"},
    ]


# ──────────────────────────────────────────────
# 2. 광고 채널 (유튜브/인스타/티스토리 등)
# ──────────────────────────────────────────────
class Channel(Base):
    __tablename__ = "channels"

    id:           Mapped[int]   = mapped_column(primary_key=True)
    name:         Mapped[str]   = mapped_column(String(50), unique=True)  # youtube_shorts
    display_name: Mapped[str]   = mapped_column(String(100), default="")  # 유튜브 쇼츠
    content_type: Mapped[ContentType] = mapped_column(Enum(ContentType))
    account_id:   Mapped[str]   = mapped_column(String(200), default="")
    account_name: Mapped[str]   = mapped_column(String(200), default="")
    status:       Mapped[ActiveStatus] = mapped_column(Enum(ActiveStatus), default=ActiveStatus.ACTIVE)
    created_at:   Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    publishes: Mapped[list["ContentPublish"]] = relationship(back_populates="channel")
    revenues:  Mapped[list["Revenue"]]        = relationship(back_populates="channel")

    # 기본 데이터 (init_db에서 자동 삽입)
    DEFAULTS = [
        {"name": "youtube_shorts",   "display_name": "유튜브 쇼츠",    "content_type": "shorts", "status": "active"},
        {"name": "instagram_reels",  "display_name": "인스타그램 릴스", "content_type": "reels",  "status": "active"},
        {"name": "tistory",          "display_name": "티스토리 블로그", "content_type": "blog",   "status": "active"},
    ]


# ──────────────────────────────────────────────
# 3. 세션 — 실행 단위
# ──────────────────────────────────────────────
class Session(Base):
    __tablename__ = "sessions"

    id:              Mapped[int]      = mapped_column(primary_key=True)
    session_id:      Mapped[str]      = mapped_column(String(50), unique=True, index=True)
    mode:            Mapped[RunMode]  = mapped_column(Enum(RunMode), default=RunMode.SEMI_AUTO)
    status:          Mapped[str]      = mapped_column(String(30), default="created")
    keyword_summary: Mapped[str]      = mapped_column(Text, default="")
    platform_id:     Mapped[int | None] = mapped_column(ForeignKey("affiliate_platforms.id"), nullable=True)
    created_at:      Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at:      Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    products: Mapped[list["Product"]] = relationship(back_populates="session", cascade="all, delete-orphan")


# ──────────────────────────────────────────────
# 4. 상품 — 추천/등록 이력
# ──────────────────────────────────────────────
class Product(Base):
    __tablename__ = "products"

    id:           Mapped[int]   = mapped_column(primary_key=True)
    session_id:   Mapped[int]   = mapped_column(ForeignKey("sessions.id"))
    platform_id:  Mapped[int | None] = mapped_column(ForeignKey("affiliate_platforms.id"), nullable=True)

    name:         Mapped[str]   = mapped_column(String(300))
    keyword:      Mapped[str]   = mapped_column(String(100), index=True)
    category:     Mapped[str]   = mapped_column(String(100), default="")
    price:        Mapped[str]   = mapped_column(String(30),  default="")
    mall_name:    Mapped[str]   = mapped_column(String(100), default="")

    product_url:   Mapped[str]  = mapped_column(Text, default="")
    affiliate_url: Mapped[str]  = mapped_column(Text, default="")
    link_source:   Mapped[str]  = mapped_column(String(20), default="")

    image_path:   Mapped[str]   = mapped_column(Text, default="")
    image_url:    Mapped[str]   = mapped_column(Text, default="")

    reason:        Mapped[str]  = mapped_column(Text, default="")
    purchase_rate: Mapped[str]  = mapped_column(String(20), default="")

    status:       Mapped[ProductStatus] = mapped_column(Enum(ProductStatus), default=ProductStatus.RECOMMENDED)
    created_at:   Mapped[datetime]      = mapped_column(DateTime, default=datetime.now)

    session:  Mapped["Session"]           = relationship(back_populates="products")
    platform: Mapped["AffiliatePlatform"] = relationship(back_populates="products")
    contents: Mapped[list["Content"]]     = relationship(back_populates="product", cascade="all, delete-orphan")
    revenues: Mapped[list["Revenue"]]     = relationship(back_populates="product", cascade="all, delete-orphan")


# ──────────────────────────────────────────────
# 5. 콘텐츠 — 생성한 쇼츠/릴스/블로그
# ──────────────────────────────────────────────
class Content(Base):
    __tablename__ = "contents"

    id:           Mapped[int]         = mapped_column(primary_key=True)
    product_id:   Mapped[int]         = mapped_column(ForeignKey("products.id"))

    content_type: Mapped[ContentType] = mapped_column(Enum(ContentType))
    title:        Mapped[str]         = mapped_column(String(300), default="")
    file_path:    Mapped[str]         = mapped_column(Text, default="")
    meta_json:    Mapped[str]         = mapped_column(Text, default="")

    # 변형(variant) — both 모드에서 같은 상품의 비교 영상 묶음
    variant_group: Mapped[str]  = mapped_column(String(60), default="", index=True)  # 같은 상품 비교군 ID
    variant_mode:  Mapped[str]  = mapped_column(String(20), default="single")        # persona | single
    selected:      Mapped[bool] = mapped_column(Boolean, default=True)               # 선택 여부(단일은 자동 True)

    # 피드백 (STEP 5)
    feedback_score:    Mapped[int | None]  = mapped_column(Integer, nullable=True)
    feedback_passed:   Mapped[bool]        = mapped_column(Boolean, default=False)
    feedback_issues:   Mapped[str]         = mapped_column(Text, default="")
    feedback_suggestions: Mapped[str]      = mapped_column(Text, default="")
    retry_count:       Mapped[int]         = mapped_column(Integer, default=0)

    created_at:   Mapped[datetime]    = mapped_column(DateTime, default=datetime.now)

    product:  Mapped["Product"]              = relationship(back_populates="contents")
    publishes: Mapped[list["ContentPublish"]] = relationship(back_populates="content", cascade="all, delete-orphan")


# ──────────────────────────────────────────────
# 6. 채널별 발행 이력
# ──────────────────────────────────────────────
class ContentPublish(Base):
    __tablename__ = "content_publishes"

    id:           Mapped[int]  = mapped_column(primary_key=True)
    content_id:   Mapped[int]  = mapped_column(ForeignKey("contents.id"))
    channel_id:   Mapped[int]  = mapped_column(ForeignKey("channels.id"))

    publish_url:  Mapped[str]  = mapped_column(Text, default="")
    published_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    views:        Mapped[int]  = mapped_column(Integer, default=0)
    clicks:       Mapped[int]  = mapped_column(Integer, default=0)
    status:       Mapped[PublishStatus] = mapped_column(Enum(PublishStatus), default=PublishStatus.DRAFT)
    created_at:   Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    content: Mapped["Content"] = relationship(back_populates="publishes")
    channel: Mapped["Channel"] = relationship(back_populates="publishes")


# ──────────────────────────────────────────────
# 7. 수익
# ──────────────────────────────────────────────
class Revenue(Base):
    __tablename__ = "revenues"

    id:          Mapped[int]   = mapped_column(primary_key=True)
    product_id:  Mapped[int]   = mapped_column(ForeignKey("products.id"))
    platform_id: Mapped[int | None] = mapped_column(ForeignKey("affiliate_platforms.id"), nullable=True)
    channel_id:  Mapped[int | None] = mapped_column(ForeignKey("channels.id"), nullable=True)

    date:        Mapped[str]   = mapped_column(String(10), index=True)
    clicks:      Mapped[int]   = mapped_column(Integer, default=0)
    orders:      Mapped[int]   = mapped_column(Integer, default=0)
    revenue:     Mapped[float] = mapped_column(Float, default=0.0)
    created_at:  Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

    product:  Mapped["Product"]           = relationship(back_populates="revenues")
    platform: Mapped["AffiliatePlatform"] = relationship(back_populates="revenues")
    channel:  Mapped["Channel"]           = relationship(back_populates="revenues")


# ──────────────────────────────────────────────
# 8. 설정
# ──────────────────────────────────────────────
class Setting(Base):
    __tablename__ = "settings"

    key:         Mapped[str]      = mapped_column(String(100), primary_key=True)
    value:       Mapped[str]      = mapped_column(Text, default="")
    description: Mapped[str]      = mapped_column(Text, default="")
    updated_at:  Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)

    DEFAULTS = {
        "run_mode":               ("semi_auto",  "운영 모드: semi_auto | full_auto"),
        "full_auto_times":        ("09:00,12:00,15:00,18:00,21:00", "완전자동 실행 시각"),
        "products_per_keyword":   ("3",          "키워드당 수집 상품 수"),
        "max_keywords":           ("5",          "한 번에 처리할 최대 키워드 수"),
        "duplicate_days":         ("14",         "중복 체크 기간(일)"),
        "duplicate_threshold":    ("0.7",        "상품명 유사도 임계값 (0~1)"),
        "category_max_per_week":  ("3",          "주간 같은 카테고리 최대 허용 수"),
        "content_types":          ("shorts,blog","생성할 콘텐츠 종류"),
        "telegram_notify":        ("true",       "텔레그램 알림 여부"),
        "feedback_min_score":     ("70",         "피드백 최소 통과 점수"),
        "feedback_max_retry":     ("2",          "피드백 최대 재시도 횟수"),
        "tistory_access_token":   ("",           "티스토리 Access Token"),
        "tistory_blog_name":      ("myops1",     "티스토리 블로그명"),
        "tistory_category_id":    ("",           "티스토리 기본 카테고리 ID"),
        "tistory_visibility":     ("3",          "티스토리 공개 설정 (0:비공개/3:공개)"),
        "tistory_auto_publish":   ("false",      "티스토리 자동 발행 여부"),
        "youtube_auto_publish":   ("false",      "유튜브 자동 발행 여부"),
        "instagram_auto_publish": ("false",      "인스타그램 자동 발행 여부"),
        "default_platform":       ("coupang",    "기본 제휴 플랫폼"),
        # 트렌드 모드
        "trend_mode":             ("both",       "트렌드 모드: current | seasonal | both"),
        "seasonal_offset_days":   ("30",         "시즌 예측 선점 기간(일) — 30이면 한달 뒤 급상승 예측"),
        "seasonal_window_days":   ("7",          "시즌 예측 검색 윈도우(일) — 기준일 ±N일"),
        "current_keyword_count":  ("3",          "현재 트렌드 키워드 수"),
        "seasonal_keyword_count": ("2",          "시즌 예측 키워드 수"),
    }