"""
텔레그램 관리자 봇 — 외부에서 파이프라인 제어
명령어:
  /start        — 봇 시작 + 메뉴
  /status       — 대시보드 통계
  /keywords     — 트렌드 키워드 조회
  /products     — 최근 상품 목록
  /pending      — 발행 대기 콘텐츠
  /settings     — 현재 설정 조회
  /set key val  — 설정 변경
  /run          — 완전자동 파이프라인 즉시 실행
  /addlink      — 수동 파트너스 링크 등록
  /help         — 도움말
"""

import os
import sys
import asyncio
import json
from pathlib import Path
from loguru import logger
from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.expanduser("~/vids-auto-engine/.env"))

sys.path.insert(0, os.path.expanduser("~/vids-auto-engine/vids-app"))

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

from src.db.database import (
    init_db, get_dashboard_stats, get_recent_products,
    get_pending_contents, get_all_settings, set_setting,
    update_product_link, get_products_by_status,
)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID   = int(os.getenv("TELEGRAM_CHAT_ID", "0"))

# ConversationHandler 상태
WAITING_LINK, WAITING_PRODUCT_ID = range(2)

# /register 단계
(
    REG_WAITING_PRODUCT_URL,
    REG_WAITING_PARTNERS,
    REG_WAITING_IMAGES,
) = range(10, 13)


def _only_admin(func):
    """관리자(본인)만 사용 가능"""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id != CHAT_ID:
            await update.message.reply_text("❌ 권한 없음")
            return
        return await func(update, context)
    return wrapper


# ──────────────────────────────────────────────
# /register — 쿠팡 상품 + 파트너스 링크 등록
# ──────────────────────────────────────────────
@_only_admin
async def cmd_register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """쿠팡 상품 URL + 파트너스 링크 입력 → 쇼츠+블로그 자동 제작"""
    context.user_data.clear()
    await update.message.reply_text(
        "🛒 *상품 등록 시작*\n\n"
        "쿠팡 상품 URL을 입력해주세요.\n\n"
        "예:\n`https://www.coupang.com/vp/products/9344037189?itemId=...`\n\n"
        "취소: /cancel",
        parse_mode="Markdown"
    )
    return REG_WAITING_PRODUCT_URL


async def reg_receive_product_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    url = update.message.text.strip()
    if not url.startswith("http") or "coupang.com" not in url:
        await update.message.reply_text("❌ 쿠팡 URL이 아니에요. 다시 입력해주세요.")
        return REG_WAITING_PRODUCT_URL

    context.user_data["product_url"] = url
    await update.message.reply_text(
        f"✅ 상품 URL 저장!\n`{url[:60]}...`\n\n"
        "이제 쿠팡 파트너스 배너 HTML 또는 링크를 입력해주세요.\n\n"
        "배너 HTML 예시:\n"
        "`<a href=\"https://link.coupang.com/a/xxx\" ...><img ...></a>`\n\n"
        "링크만 있으면:\n"
        "`https://link.coupang.com/a/xxx`",
        parse_mode="Markdown"
    )
    return REG_WAITING_PARTNERS


async def reg_receive_partners(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from src.product.coupang_scraper import parse_partners_input
    raw = update.message.text.strip()
    partners = parse_partners_input(raw)

    if not partners:
        await update.message.reply_text(
            "❌ 파트너스 링크를 인식하지 못했어요.\n"
            "HTML 배너 또는 https://link.coupang.com/a/... 형식으로 입력해주세요."
        )
        return REG_WAITING_PARTNERS

    context.user_data["partners_raw"]  = raw
    context.user_data["partners"]      = partners
    context.user_data["extra_images"]  = []

    # 파트너스 정보 확인
    alt_text = partners.get("alt", "")
    link     = partners.get("link", "")

    await update.message.reply_text(
        f"✅ 파트너스 링크 확인!\n\n"
        f"🔗 링크: `{link[:50]}...`\n"
        f"📦 상품명(배너): {alt_text[:50] if alt_text else '(배너 이미지 없음)'}\n\n"
        f"추가 상품 이미지가 있으면 사진을 보내주세요. (최대 5장)\n"
        f"없으면 `/skip` 을 입력하세요.\n\n"
        f"💡 쿠팡 상품 페이지에서 이미지를 캡처해서 보내주시면 더 좋은 영상이 만들어져요!",
        parse_mode="Markdown"
    )
    return REG_WAITING_IMAGES


async def reg_receive_image(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """이미지 파일 수신"""
    import tempfile, aiofiles

    photos = update.message.photo
    if not photos:
        await update.message.reply_text("사진 파일을 보내주세요. 또는 `/skip`으로 건너뛰세요.")
        return REG_WAITING_IMAGES

    # 가장 큰 해상도 선택
    photo   = photos[-1]
    img_dir = Path(os.path.expanduser("~/vids-auto-engine/vids-backend/input/temp_images"))
    img_dir.mkdir(parents=True, exist_ok=True)

    file     = await context.bot.get_file(photo.file_id)
    img_path = img_dir / f"user_img_{len(context.user_data['extra_images'])+1:02d}.jpg"
    await file.download_to_drive(str(img_path))

    context.user_data["extra_images"].append(str(img_path))
    count = len(context.user_data["extra_images"])

    if count >= 5:
        await update.message.reply_text(
            f"✅ 이미지 {count}장 수집 완료! (최대 도달)\n"
            "자동으로 다음 단계로 진행할게요..."
        )
        return await reg_start_production(update, context)

    await update.message.reply_text(
        f"📸 이미지 {count}장 받았어요!\n"
        "추가 이미지를 보내거나 `/skip`으로 제작을 시작하세요."
    )
    return REG_WAITING_IMAGES


async def reg_skip_images(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """이미지 없이 건너뛰기"""
    await update.message.reply_text("이미지 건너뛰고 제작을 시작할게요!")
    return await reg_start_production(update, context)


async def reg_start_production(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """상품 정보 수집 후 파이프라인 실행"""
    from datetime import datetime
    from src.product.coupang_scraper import parse_partners_input
    from src.product.coupang_crawler import ProductSearchPipeline
    from src.db.database import create_session, add_product

    product_url  = context.user_data.get("product_url", "")
    partners_raw = context.user_data.get("partners_raw", "")
    partners     = context.user_data.get("partners", {})
    extra_images = context.user_data.get("extra_images", [])

    await update.message.reply_text(
        "⚙️ *상품 정보 수집 중...*\n\n"
        "네이버 쇼핑에서 동일 상품 검색 중...",
        parse_mode="Markdown"
    )

    try:
        # 파트너스 alt 텍스트에서 상품명 추출
        alt_text = partners.get("alt", "")
        # 불필요한 앞부분 제거 (예: [TV홈쇼핑매진] 등)
        import re
        clean_name = re.sub(r"^\[[^\]]+\]\s*", "", alt_text).strip()
        keyword    = clean_name[:20] if clean_name else "상품"

        # 네이버 쇼핑에서 동일 상품 검색 → 고화질 이미지
        import requests
        naver_headers = {
            "X-Naver-Client-Id":     os.getenv("NAVER_CLIENT_ID"),
            "X-Naver-Client-Secret": os.getenv("NAVER_CLIENT_SECRET"),
        }
        naver_resp = requests.get(
            "https://openapi.naver.com/v1/search/shop.json",
            headers=naver_headers,
            params={"query": keyword, "display": 5, "sort": "sim"},
            timeout=10,
        )
        naver_items   = naver_resp.json().get("items", []) if naver_resp.ok else []
        naver_images  = [item.get("image","") for item in naver_items if item.get("image")]
        naver_product = naver_items[0] if naver_items else {}

        # 이미지 통합 (네이버 + 사용자 업로드)
        all_images = naver_images[:4] + extra_images

        # 세션 생성
        session_id = f"{datetime.now().strftime('%Y-%m-%d')}_reg"
        create_session(session_id, mode="semi_auto",
                       keyword_summary=keyword, platform_name="coupang")

        # 이미지 다운로드
        import urllib.request
        img_dir = Path(os.path.expanduser(
            f"~/vids-auto-engine/vids-backend/input/{session_id}"
        ))
        img_dir.mkdir(parents=True, exist_ok=True)
        local_images = []
        for i, img_url in enumerate(naver_images[:4]):
            try:
                img_path = img_dir / f"product_{i+1:02d}.jpg"
                urllib.request.urlretrieve(img_url, str(img_path))
                local_images.append(str(img_path))
            except:
                pass
        local_images += extra_images

        # 상품 데이터 구성
        import re as _re
        product_data = {
            "name":             clean_name or re.sub(r"<[^>]+>","",naver_product.get("title","상품")),
            "keyword":          keyword,
            "product_category": naver_product.get("category3", naver_product.get("category2","")),
            "price":            naver_product.get("lprice", ""),
            "product_url":      product_url,
            "affiliate_url":    partners.get("link", ""),
            "partners_html":    partners.get("html", ""),
            "local_image_path": local_images[0] if local_images else "",
            "all_image_paths":  local_images,
            "source":           "coupang_direct",
            "platform_name":    "coupang",
            "reason":           "직접 등록 상품",
        }

        pid = add_product(session_id, product_data, platform_name="coupang")
        product_data["db_id"] = pid

        await update.message.reply_text(
            f"✅ *상품 정보 수집 완료!*\n\n"
            f"📦 상품명: {product_data['name'][:40]}\n"
            f"💰 가격: {product_data['price']}원\n"
            f"🖼️ 이미지: {len(local_images)}장\n"
            f"🔗 파트너스: `{partners.get('link','')[:40]}...`\n\n"
            f"쇼츠 + 블로그 제작 시작! (5~10분 소요)\n"
            f"완료되면 알려드릴게요 📩",
            parse_mode="Markdown"
        )

        # 파이프라인 실행
        from src.core.pipeline import run_semi_auto
        result = await run_semi_auto([product_data], platform_name="coupang")

        if result["success"]:
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=(
                    f"🎉 *제작 완료!*\n\n"
                    f"세션: `{result['session_id']}`\n"
                    f"영상: {result['products']}개\n\n"
                    f"/pending 에서 발행 대기 목록을 확인하세요!"
                ),
                parse_mode="Markdown"
            )
        else:
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=f"⚠️ 제작 실패: {result.get('reason', '알 수 없는 오류')}"
            )

    except Exception as e:
        logger.error(f"/register 오류: {e}")
        await update.message.reply_text(f"❌ 오류 발생: {e}")

    context.user_data.clear()
    return ConversationHandler.END


async def reg_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data.clear()
    await update.message.reply_text("취소됐어요.")
    return ConversationHandler.END


# ──────────────────────────────────────────────
# /start — 메인 메뉴
# ──────────────────────────────────────────────
@_only_admin
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("📊 대시보드", callback_data="status"),
         InlineKeyboardButton("🔑 키워드 조회", callback_data="keywords")],
        [InlineKeyboardButton("📦 최근 상품", callback_data="products"),
         InlineKeyboardButton("⏳ 발행 대기", callback_data="pending")],
        [InlineKeyboardButton("⚙️ 설정", callback_data="settings"),
         InlineKeyboardButton("🚀 파이프라인 실행", callback_data="run_confirm")],
        [InlineKeyboardButton("🔗 파트너스 링크 등록", callback_data="addlink")],
    ]
    await update.message.reply_text(
        "🤖 *Vids Auto Engine 관리자 봇*\n\n"
        "집 밖에서도 모든 기능을 제어할 수 있어요.\n"
        "아래 메뉴를 선택하거나 명령어를 입력하세요.",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown"
    )


# ──────────────────────────────────────────────
# /status — 대시보드
# ──────────────────────────────────────────────
@_only_admin
async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    stats = get_dashboard_stats()
    rev   = stats["revenue"]
    msg = (
        "📊 *대시보드 현황*\n\n"
        f"📁 총 세션: {stats['total_sessions']}개\n"
        f"📦 총 상품: {stats['total_products']}개 (링크 연결: {stats['linked_products']}개)\n"
        f"🎬 총 콘텐츠: {stats['total_contents']}개\n"
        f"  ✅ 발행 완료: {stats['published']}개\n"
        f"  ⏳ 발행 대기: {stats['pending']}개\n\n"
        f"💰 *수익 현황*\n"
        f"  클릭: {rev['total_clicks']}회\n"
        f"  주문: {rev['total_orders']}건\n"
        f"  수익: {rev['total_revenue']:,.0f}원\n"
        f"  전환율: {rev['conversion']}%"
    )
    await _reply(update, msg)


# ──────────────────────────────────────────────
# /keywords — 트렌드 키워드 조회
# ──────────────────────────────────────────────
@_only_admin
async def cmd_keywords(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _reply(update, "🔍 트렌드 키워드 분석 중... (30~60초 소요)")
    try:
        from src.keyword.trend_extractor import TrendKeywordPipeline
        from src.db.database import add_product, create_session
        from datetime import datetime
        import urllib.parse

        pipeline = TrendKeywordPipeline()
        keywords = pipeline.run()

        if not keywords:
            await _reply(update, "❌ 키워드 조회 결과 없음. 잠시 후 다시 시도해주세요.")
            return

        # DB에 세션 + 추천 상품으로 저장 (링크 연결 대기 상태)
        session_id = f"{datetime.now().strftime('%Y-%m-%d')}_bot"
        create_session(session_id, mode="semi_auto",
                       keyword_summary=", ".join([k.get("keyword","") for k in keywords]))

        msg = "🔑 *트렌드 키워드 분석 완료*\n\n"
        msg += "아래 상품을 쿠팡 파트너스에서 검색해서 링크를 만들어주세요!\n\n"
        msg += "─" * 20 + "\n\n"

        for i, kw in enumerate(keywords):
            keyword      = kw.get("keyword", "")
            category     = kw.get("product_category", "-")
            reason       = kw.get("reason", "-")
            purchase     = kw.get("estimated_purchase_rate", "-")
            trend_mode   = kw.get("trend_mode", "current")
            seasonal_date = kw.get("seasonal_target_date", "")
            suggested    = kw.get("suggested_products", [])

            # DB에 추천 상품 저장
            pid = add_product(session_id, {
                "name":             suggested[0] if suggested else keyword,
                "keyword":          keyword,
                "product_category": category,
                "price":            "0",
                "reason":           reason,
                "trend_mode":       trend_mode,
                "seasonal_target_date": seasonal_date,
            }, platform_name="coupang")

            # 모드 아이콘
            mode_icon = "📈" if trend_mode == "current" else "🔮"
            mode_text = "현재 트렌드" if trend_mode == "current" else f"시즌 예측 (작년 {seasonal_date} 급상승)"

            # 쿠팡 검색 URL
            coupang_search = f"https://www.coupang.com/np/search?q={urllib.parse.quote(keyword)}"
            partners_url   = "https://partners.coupang.com"

            msg += (
                f"{mode_icon} *{i+1}. {keyword}*  `[DB ID: {pid}]`\n"
                f"   모드: {mode_text}\n"
                f"   카테고리: {category}\n"
                f"   이유: {reason}\n"
                f"   구매전환: {purchase}\n"
            )
            if suggested:
                msg += f"   추천 상품: {', '.join(suggested[:2])}\n"
            msg += (
                f"\n"
                f"   🛒 [쿠팡 검색]({coupang_search})\n"
                f"   🔗 [파트너스 링크 생성]({partners_url})\n"
                f"\n"
                f"   링크 생성 후:\n"
                f"   `/addlink {pid} https://link.coupang.com/a/...`\n\n"
                f"─────────────────────\n\n"
            )

        msg += (
            "💡 *링크 등록 방법*\n"
            "1. 위 쿠팡 검색 링크로 상품 찾기\n"
            "2. 쿠팡 파트너스에서 해당 상품 링크 생성\n"
            "3. `/addlink DB아이디 링크` 입력\n"
            "4. 쇼츠 + 블로그 자동 제작 시작!"
        )

        await _reply(update, msg)

    except Exception as e:
        await _reply(update, f"❌ 키워드 조회 실패: {e}")


# ──────────────────────────────────────────────
# /products — 최근 상품 목록
# ──────────────────────────────────────────────
@_only_admin
async def cmd_products(update: Update, context: ContextTypes.DEFAULT_TYPE):
    products = get_recent_products(10)
    if not products:
        await _reply(update, "📦 등록된 상품이 없어요.")
        return

    msg = "📦 *최근 상품 목록*\n\n"
    for p in products:
        status_icon = {
            "recommended": "⭐", "selected": "🎯", "linked": "🔗",
            "producing": "⚙️", "published": "✅", "archived": "📁"
        }.get(p["status"], "❓")
        link_status = "링크 있음 ✅" if p["affiliate_url"] else "링크 없음 ❌"
        msg += (
            f"{status_icon} `[ID:{p['id']}]` {p['name'][:30]}\n"
            f"   키워드: {p['keyword']} | {link_status}\n\n"
        )

    # 링크 없는 상품이 있으면 안내
    no_link = [p for p in products if not p["affiliate_url"]]
    if no_link:
        msg += f"⚠️ 파트너스 링크가 없는 상품 {len(no_link)}개\n"
        msg += "/addlink 명령어로 링크를 등록하세요."

    await _reply(update, msg)


# ──────────────────────────────────────────────
# /pending — 발행 대기 콘텐츠
# ──────────────────────────────────────────────
@_only_admin
async def cmd_pending(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contents = get_pending_contents()
    if not contents:
        await _reply(update, "⏳ 발행 대기 콘텐츠가 없어요.")
        return

    msg = f"⏳ *발행 대기 콘텐츠 {len(contents)}개*\n\n"
    for c in contents[:10]:
        type_icon = {"shorts": "🎬", "reels": "📱", "blog": "📝"}.get(c["type"], "📄")
        msg += f"{type_icon} `[ID:{c['id']}]` {c['title'][:30]}\n"
        msg += f"   상태: {c['status']} | 타입: {c['type']}\n\n"

    await _reply(update, msg)


# ──────────────────────────────────────────────
# /settings — 설정 조회
# ──────────────────────────────────────────────
@_only_admin
async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    settings = get_all_settings()
    msg = "⚙️ *현재 설정*\n\n"
    for key, info in settings.items():
        msg += f"`{key}` = `{info['value']}`\n"
        msg += f"   _{info['description']}_\n\n"
    msg += "변경: `/set 키 값` (예: `/set run_mode full_auto`)"
    await _reply(update, msg)


@_only_admin
async def cmd_set(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args
    if len(args) < 2:
        await _reply(update, "사용법: `/set 키 값`\n예: `/set duplicate_days 7`")
        return
    key, value = args[0], " ".join(args[1:])
    set_setting(key, value)
    await _reply(update, f"✅ 설정 변경 완료\n`{key}` = `{value}`")


# ──────────────────────────────────────────────
# /run — 완전자동 파이프라인 실행
# ──────────────────────────────────────────────
@_only_admin
async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[
        InlineKeyboardButton("✅ 실행", callback_data="run_yes"),
        InlineKeyboardButton("❌ 취소", callback_data="run_no"),
    ]]
    await _reply(
        update,
        "🚀 *완전자동 파이프라인 실행*\n\n"
        "키워드 추출 → 상품 검색 → 대본 생성 → 영상 제작\n"
        "약 5~10분 소요됩니다.\n\n"
        "실행하시겠어요?",
        keyboard=InlineKeyboardMarkup(keyboard)
    )


# ──────────────────────────────────────────────
# /addlink — 파트너스 링크 수동 등록
# ──────────────────────────────────────────────
@_only_admin
async def cmd_addlink(update: Update, context: ContextTypes.DEFAULT_TYPE):
    args = context.args if context.args else []

    # 한 줄 입력 지원: /addlink 3 https://link.coupang.com/a/...
    if len(args) >= 2:
        try:
            pid  = int(args[0])
            link = args[1]
            if not link.startswith("http"):
                await _reply(update, "❌ 올바른 URL이 아니에요.\n예: `/addlink 3 https://link.coupang.com/a/xxx`")
                return
            update_product_link(pid, link, "manual")
            await _reply(
                update,
                f"✅ *파트너스 링크 등록 완료!*\n\n"
                f"상품 ID: `{pid}`\n"
                f"링크: `{link[:60]}...`\n\n"
                f"이제 `/produce {pid}` 로 쇼츠 + 블로그를 제작할 수 있어요!"
            )
            return
        except ValueError:
            await _reply(update, "❌ 상품 ID는 숫자여야 해요.\n예: `/addlink 3 https://link.coupang.com/a/xxx`")
            return

    # 인자 없이 /addlink만 입력한 경우 → 링크 없는 상품 목록 안내
    products = get_products_by_status("recommended") + get_products_by_status("selected")
    if not products:
        await _reply(update, "링크 등록이 필요한 상품이 없어요.\n먼저 `/keywords` 로 키워드를 조회해주세요.")
        return ConversationHandler.END

    msg = "🔗 *파트너스 링크 등록*\n\n링크가 없는 상품 목록:\n\n"
    for p in products[:10]:
        msg += f"`[ID:{p['id']}]` {p['name'][:35]}\n"
    msg += "\n사용법: `/addlink 상품ID 링크`\n예: `/addlink 3 https://link.coupang.com/a/xxx`"

    await _reply(update, msg)
    return ConversationHandler.END


async def receive_product_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        pid = int(update.message.text.strip())
        context.user_data["pending_product_id"] = pid
        await update.message.reply_text(
            f"상품 ID `{pid}` 선택됨.\n\n"
            "쿠팡 파트너스에서 생성한 링크를 붙여넣어 주세요.\n"
            "예: `https://link.coupang.com/a/xxxxxxx`",
            parse_mode="Markdown"
        )
        return WAITING_LINK
    except ValueError:
        await update.message.reply_text("숫자로 입력해주세요.")
        return WAITING_PRODUCT_ID


async def receive_link(update: Update, context: ContextTypes.DEFAULT_TYPE):
    link = update.message.text.strip()
    pid  = context.user_data.get("pending_product_id")

    if not link.startswith("http"):
        await update.message.reply_text("❌ 올바른 URL이 아니에요. 다시 입력해주세요.")
        return WAITING_LINK

    update_product_link(pid, link, "manual")
    await update.message.reply_text(
        f"✅ 링크 등록 완료!\n"
        f"상품 ID: `{pid}`\n"
        f"링크: `{link[:60]}...`",
        parse_mode="Markdown"
    )
    return ConversationHandler.END


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("취소됐어요.")
    return ConversationHandler.END


# ──────────────────────────────────────────────
# 콜백 핸들러 (인라인 버튼)
# ──────────────────────────────────────────────
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data  = query.data

    if data == "status":
        await cmd_status(update, context)
    elif data == "keywords":
        await cmd_keywords(update, context)
    elif data == "products":
        await cmd_products(update, context)
    elif data == "pending":
        await cmd_pending(update, context)
    elif data == "settings":
        await cmd_settings(update, context)
    elif data == "run_confirm":
        await cmd_run(update, context)
    elif data == "run_yes":
        await query.edit_message_text("🚀 파이프라인 실행 중... 완료 후 알려드릴게요!")
        try:
            from src.core.pipeline import run_full_auto
            result = await run_full_auto()
            if result["success"]:
                msg = (
                    f"✅ *파이프라인 완료!*\n\n"
                    f"세션: `{result['session_id']}`\n"
                    f"생성 영상: {result['products']}개\n\n"
                    f"발행 대기 콘텐츠를 확인하세요. /pending"
                )
            else:
                msg = f"⚠️ 파이프라인 실패: {result.get('reason')}"
            await context.bot.send_message(chat_id=CHAT_ID, text=msg, parse_mode="Markdown")
        except Exception as e:
            await context.bot.send_message(chat_id=CHAT_ID, text=f"❌ 오류: {e}")
    elif data == "run_no":
        await query.edit_message_text("취소됐어요.")
    elif data == "addlink":
        await cmd_addlink(update, context)


# ──────────────────────────────────────────────
# 유틸
# ──────────────────────────────────────────────
async def _reply(update: Update, text: str, keyboard=None):
    """update가 message인지 callback_query인지 자동 판단해서 응답"""
    kwargs = {"text": text, "parse_mode": "Markdown"}
    if keyboard:
        kwargs["reply_markup"] = keyboard
    if update.message:
        await update.message.reply_text(**kwargs)
    elif update.callback_query:
        await update.callback_query.message.reply_text(**kwargs)


@_only_admin
async def cmd_produce(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """링크 등록된 상품으로 쇼츠 + 블로그 즉시 제작"""
    args = context.args if context.args else []
    if not args:
        await _reply(update, "사용법: `/produce 상품ID`\n예: `/produce 3`")
        return

    try:
        pid = int(args[0])
    except ValueError:
        await _reply(update, "❌ 상품 ID는 숫자여야 해요.")
        return

    # DB에서 상품 정보 조회
    with __import__('src.db.database', fromlist=['get_db']).get_db() as db:
        from src.db.models import Product as DBProduct
        p = db.query(DBProduct).filter_by(id=pid).first()
        if not p:
            await _reply(update, f"❌ 상품 ID {pid}를 찾을 수 없어요.")
            return
        if not p.affiliate_url:
            await _reply(update, f"❌ 상품 ID {pid}에 파트너스 링크가 없어요.\n먼저 `/addlink {pid} 링크`로 등록해주세요.")
            return
        product_data = {
            "name":             p.name,
            "keyword":          p.keyword,
            "product_category": p.category,
            "price":            p.price,
            "affiliate_url":    p.affiliate_url,
            "local_image_path": p.image_path,
            "reason":           p.reason,
            "db_id":            p.id,
        }

    await _reply(update,
        f"⚙️ *제작 시작!*\n\n"
        f"상품: {product_data['name'][:40]}\n"
        f"키워드: {product_data['keyword']}\n"
        f"링크: `{product_data['affiliate_url'][:50]}...`\n\n"
        f"쇼츠 + 블로그 제작 중... (5~10분 소요)"
    )

    try:
        from src.core.pipeline import run_semi_auto
        result = await run_semi_auto([product_data], platform_name="coupang")
        if result["success"]:
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=(
                    f"✅ *제작 완료!*\n\n"
                    f"세션: `{result['session_id']}`\n"
                    f"영상: {result['products']}개\n\n"
                    f"발행 대기 목록: /pending"
                ),
                parse_mode="Markdown"
            )
        else:
            await context.bot.send_message(
                chat_id=CHAT_ID,
                text=f"⚠️ 제작 실패: {result.get('reason', '알 수 없는 오류')}"
            )
    except Exception as e:
        await context.bot.send_message(chat_id=CHAT_ID, text=f"❌ 오류: {e}")


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = (
        "📋 *명령어 목록*\n\n"
        "*조회*\n"
        "/start — 메인 메뉴\n"
        "/status — 대시보드 통계\n"
        "/keywords — 트렌드 키워드 + 쿠팡 검색 링크\n"
        "/products — 최근 상품 목록\n"
        "/pending — 발행 대기 콘텐츠\n\n"
        "*수동 운영 (API 승인 전)*\n"
        "/addlink — 링크 없는 상품 목록\n"
        "/addlink ID 링크 — 링크 직접 등록\n"
        "/produce ID — 쇼츠+블로그 즉시 제작\n\n"
        "*자동화*\n"
        "/run — 완전자동 파이프라인 실행\n"
        "/settings — 설정 조회\n"
        "/set 키 값 — 설정 변경\n\n"
        "*수동 운영 흐름*\n"
        "① /keywords → 트렌드 + 쿠팡 검색링크\n"
        "② 쿠팡 파트너스에서 링크 생성\n"
        "③ /addlink ID 링크 → 등록\n"
        "④ /produce ID → 쇼츠+블로그 자동 제작"
    )
    await _reply(update, msg)


# ──────────────────────────────────────────────
# 봇 실행
# ──────────────────────────────────────────────
def main():
    init_db()
    logger.info("🤖 텔레그램 봇 시작")

    app = Application.builder().token(BOT_TOKEN).build()

    # 파트너스 링크 등록 대화 핸들러
    addlink_conv = ConversationHandler(
        entry_points=[CommandHandler("addlink", cmd_addlink)],
        states={
            WAITING_PRODUCT_ID: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_product_id)],
            WAITING_LINK:       [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_link)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    # 상품 등록 대화 핸들러
    register_conv = ConversationHandler(
        entry_points=[CommandHandler("register", cmd_register)],
        states={
            REG_WAITING_PRODUCT_URL: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_receive_product_url)
            ],
            REG_WAITING_PARTNERS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, reg_receive_partners)
            ],
            REG_WAITING_IMAGES: [
                MessageHandler(filters.PHOTO, reg_receive_image),
                CommandHandler("skip", reg_skip_images),
            ],
        },
        fallbacks=[CommandHandler("cancel", reg_cancel)],
    )

    app.add_handler(CommandHandler("start",    cmd_start))
    app.add_handler(CommandHandler("status",   cmd_status))
    app.add_handler(CommandHandler("keywords", cmd_keywords))
    app.add_handler(CommandHandler("products", cmd_products))
    app.add_handler(CommandHandler("pending",  cmd_pending))
    app.add_handler(CommandHandler("settings", cmd_settings))
    app.add_handler(CommandHandler("set",      cmd_set))
    app.add_handler(CommandHandler("run",      cmd_run))
    app.add_handler(CommandHandler("produce",  cmd_produce))
    app.add_handler(CommandHandler("help",     cmd_help))
    app.add_handler(register_conv)
    app.add_handler(addlink_conv)
    app.add_handler(CallbackQueryHandler(callback_handler))

    logger.info("✅ 봇 준비 완료. 텔레그램에서 /start 를 눌러주세요.")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()