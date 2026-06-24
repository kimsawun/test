"""
STEP 4-2a — ASS 자막 생성 모듈 (단어별 하이라이트)
edge-tts의 word boundary 타이밍을 이용해 단어가 발음되는 순간
글자가 강조(색상/크기 변화)되는 ASS 자막을 만든다.
"""

import os
from pathlib import Path
from typing import Optional

FONT_DIR = Path(os.path.expanduser("~/vids-auto-engine/vids-backend/assets/fonts"))

# ── 쇼츠 영상 규격 (9:16) ──
VIDEO_W = 1080
VIDEO_H = 1920

# ── 자막 스타일 ──
FONT_NAME      = "Pretendard ExtraBold"   # 실제 등록명 (아래 register로 처리)
FONT_SIZE      = 56
PRIMARY_COLOR  = "&HFFFFFF"     # 기본 흰색 (BBGGRR)
HIGHLIGHT_COLOR = "&H00F0FF"    # 강조 노란색 (BBGGRR: 노랑=00F0FF)
OUTLINE_COLOR  = "&H000000"     # 검정 외곽선
OUTLINE_WIDTH  = 4
SHADOW         = 3
# 자막 세로 위치 (하단에서 위로, 화면 중앙~하단 1/3 지점)
MARGIN_V       = 600


def _format_time(seconds: float) -> str:
    """초 → ASS 시간 포맷 (H:MM:SS.cs)"""
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    cs = int((seconds - int(seconds)) * 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def _ass_header() -> str:
    """ASS 파일 헤더 + 스타일 정의"""
    return f"""[Script Info]
ScriptType: v4.00+
PlayResX: {VIDEO_W}
PlayResY: {VIDEO_H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{FONT_NAME},{FONT_SIZE},{PRIMARY_COLOR},{HIGHLIGHT_COLOR},{OUTLINE_COLOR},&H000000,1,0,0,0,100,100,0,0,1,{OUTLINE_WIDTH},{SHADOW},2,80,80,{MARGIN_V},1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def build_word_highlight_ass(
    lines: list[dict],
    out_file: Path,
    time_offset: float = 0.0,
) -> str:
    """
    lines: tts_generator가 만든 line 목록.
           각 line = {text, duration, word_boundaries:[{text,offset,duration}], part, ...}
    단어가 발음되는 순간 그 단어만 강조색으로 표시.

    구현 방식:
    - 한 line(문장)을 한 화면에 표시
    - 그 안에서 현재 발음 중인 단어를 \\c 색상 태그로 강조
    - word boundary offset은 각 line 내부 기준 → 누적 시간으로 변환
    """
    events = []
    cursor = time_offset

    for line in lines:
        text = line.get("text", "").strip()
        wbs  = line.get("word_boundaries", [])
        dur  = line.get("duration", 0.0)

        if not text:
            continue

        line_start = cursor
        line_end   = cursor + dur

        if not wbs:
            # 타이밍 정보 없으면 그냥 통자막
            events.append(
                f"Dialogue: 0,{_format_time(line_start)},{_format_time(line_end)},"
                f"Default,,0,0,0,,{text}"
            )
            cursor = line_end
            continue

        # 단어별 하이라이트: 각 단어 구간마다 별도 Dialogue 생성
        # 화면엔 문장 전체가 보이고, 현재 단어만 색이 바뀜
        for i, wb in enumerate(wbs):
            w_start = line_start + wb["offset"]
            # 다음 단어 시작 전까지 강조 유지
            if i + 1 < len(wbs):
                w_end = line_start + wbs[i + 1]["offset"]
            else:
                w_end = line_end

            # 문장을 단어 단위로 재구성하며 현재 단어만 강조
            rendered = _render_line_with_highlight(wbs, i)
            events.append(
                f"Dialogue: 0,{_format_time(w_start)},{_format_time(w_end)},"
                f"Default,,0,0,0,,{rendered}"
            )

        cursor = line_end

    # 파일 저장
    out_file.parent.mkdir(parents=True, exist_ok=True)
    with open(out_file, "w", encoding="utf-8") as f:
        f.write(_ass_header())
        f.write("\n".join(events))
        f.write("\n")

    return str(out_file)


def _render_line_with_highlight(word_boundaries: list[dict], active_idx: int) -> str:
    """
    문장 전체를 표시하되, active_idx 단어만 강조색(\\c) 적용.
    글자 수 기준으로 여러 줄 자동 분할 (\\N).
    """
    import re as _re
    # edge-tts가 끼워넣는 순수 문장부호 토큰(',', '.', '?' 등) 제거
    def _is_punct_only(s: str) -> bool:
        return bool(s) and _re.fullmatch(r"[\s,.\?!;:·…\-~]+", s) is not None

    # active_idx 보정을 위해 필터링하면서 인덱스 매핑
    filtered = [wb for wb in word_boundaries if not _is_punct_only(wb.get("text", ""))]
    if not filtered:
        return ""
    # active 단어가 필터로 사라졌으면 가장 가까운 유효 인덱스로
    active_word = word_boundaries[active_idx].get("text", "") if active_idx < len(word_boundaries) else ""
    if _is_punct_only(active_word):
        active_idx_f = 0
    else:
        # 원본 active 단어가 filtered에서 몇 번째인지
        active_idx_f = 0
        seen = 0
        for wb in word_boundaries[:active_idx + 1]:
            if not _is_punct_only(wb.get("text", "")):
                active_idx_f = seen
                seen += 1
        active_idx_f = max(0, seen - 1)

    word_boundaries = filtered
    active_idx = active_idx_f

    words = [wb["text"] for wb in word_boundaries]

    # 한 줄 최대 글자 수 (폰트 56, 9:16 화면 기준)
    MAX_CHARS_PER_LINE = 16

    # 각 단어가 몇 번째 줄에 속하는지 미리 계산
    line_of_word = []
    cur_line = 0
    cur_len  = 0
    for idx, w in enumerate(words):
        wlen = len(w)
        if cur_len > 0 and cur_len + wlen > MAX_CHARS_PER_LINE:
            cur_line += 1
            cur_len = 0
        line_of_word.append(cur_line)
        cur_len += wlen + 1   # +1은 공백

    parts = []
    prev_line = 0
    for i, wb in enumerate(word_boundaries):
        word = wb["text"]
        if line_of_word[i] != prev_line:
            parts.append("\\N")
            prev_line = line_of_word[i]
        elif i > 0:
            parts.append(" ")

        if i == active_idx:
            parts.append(f"{{\\c{HIGHLIGHT_COLOR}\\fscx115\\fscy115}}{word}{{\\c{PRIMARY_COLOR}\\fscx100\\fscy100}}")
        else:
            parts.append(f"{{\\c{PRIMARY_COLOR}}}{word}")

    return "".join(parts)


def register_fonts() -> dict:
    """폰트 경로 반환 (FFmpeg fontsdir용)"""
    return {
        "dir": str(FONT_DIR),
        "bold": str(FONT_DIR / "Pretendard-Bold.otf"),
        "extrabold": str(FONT_DIR / "Pretendard-ExtraBold.otf"),
    }


# ──────────────────────────────────────────────
# 단독 테스트
# ──────────────────────────────────────────────
if __name__ == "__main__":
    # 가짜 word boundary로 ASS 생성 테스트
    test_lines = [
        {
            "text": "하루 10분이 헬스장 1시간보다 효과가 좋다",
            "duration": 3.0,
            "word_boundaries": [
                {"text": "하루",     "offset": 0.0, "duration": 0.4},
                {"text": "10분이",   "offset": 0.5, "duration": 0.5},
                {"text": "헬스장",   "offset": 1.1, "duration": 0.5},
                {"text": "1시간보다","offset": 1.7, "duration": 0.6},
                {"text": "효과가",   "offset": 2.4, "duration": 0.3},
                {"text": "좋다",     "offset": 2.8, "duration": 0.2},
            ],
            "part": "hook",
        }
    ]

    out = Path("/tmp/test_subtitle.ass")
    result = build_word_highlight_ass(test_lines, out)
    print(f"✅ ASS 자막 생성: {result}")
    print("\n--- 내용 미리보기 ---")
    with open(result, encoding="utf-8") as f:
        print(f.read()[:1200])