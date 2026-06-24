"""
STEP 4-2b — FFmpeg 영상 합성 모듈
상품 이미지 + 나레이션 음성 + 단어별 하이라이트 자막 + 배경음악
→ 9:16 쇼츠 mp4 생성

이미지에 켄번스(천천히 줌인) 효과를 줘서 정적 이미지도 생동감 있게.
"""

import os
import json
import random
import subprocess
from pathlib import Path
from typing import Optional
from loguru import logger

import sys
sys.path.insert(0, os.path.expanduser("~/vids-auto-engine/vids-app"))
from src.video.subtitle_builder import build_word_highlight_ass, register_fonts, VIDEO_W, VIDEO_H
from src.video.media_config import FFMPEG, FFPROBE

BASE_INPUT_DIR = Path(os.path.expanduser("~/vids-auto-engine/vids-backend/input"))
ASSETS_DIR     = Path(os.path.expanduser("~/vids-auto-engine/vids-backend/assets"))
BGM_DIR        = ASSETS_DIR / "bgm"

BGM_VOLUME = 0.15   # 배경음악 볼륨 (나레이션 대비 작게)


def _get_random_bgm() -> Optional[str]:
    """bgm 폴더에서 랜덤 음원 1개 선택"""
    if not BGM_DIR.exists():
        return None
    musics = list(BGM_DIR.glob("*.mp3")) + list(BGM_DIR.glob("*.m4a")) + list(BGM_DIR.glob("*.wav"))
    if not musics:
        return None
    return str(random.choice(musics))


def _concat_audio(line_files: list[str], out_file: Path) -> float:
    """여러 나레이션 mp3를 하나로 이어붙이고 총 길이 반환"""
    # concat용 리스트 파일 생성
    list_file = out_file.parent / "audio_concat_list.txt"
    with open(list_file, "w") as f:
        for lf in line_files:
            f.write(f"file '{lf}'\n")

    cmd = [
        FFMPEG, "-y", "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c:a", "libmp3lame", "-b:a", "192k", "-ar", "44100",
        str(out_file)
    ]
    subprocess.run(cmd, capture_output=True, check=True)
    list_file.unlink(missing_ok=True)

    # 길이 측정
    return _get_duration(out_file)


def _get_duration(media_file: Path) -> float:
    """ffprobe로 미디어 길이(초) 측정"""
    cmd = [
        FFPROBE, "-v", "error", "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1", str(media_file)
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, check=True)
    try:
        return float(result.stdout.strip())
    except ValueError:
        return 0.0


def compose_video(
    audio_info: dict,
    image_path: str,
    out_file: Path,
    add_bgm: bool = True,
) -> Optional[str]:
    """
    하나의 영상 합성.
    audio_info: tts_generator의 audio 결과 (lines 포함)
    image_path: 상품 이미지
    """
    work_dir = out_file.parent
    work_dir.mkdir(parents=True, exist_ok=True)

    lines = audio_info.get("lines", [])
    if not lines:
        logger.error("나레이션 라인 없음")
        return None

    # 1) 나레이션 음성 합치기
    line_files = [l["audio_path"] for l in lines]
    narration_audio = work_dir / "narration.mp3"
    total_duration  = _concat_audio(line_files, narration_audio)
    logger.info(f"  🎙️  나레이션 총 길이: {total_duration:.1f}초")

    # 2) 자막(ASS) 생성 — 각 라인의 누적 타이밍 반영
    ass_file = work_dir / "subtitle.ass"
    # 누적 offset 계산하며 build
    _build_session_subtitle(lines, ass_file)

    # 3) 이미지 검증
    if not image_path or not Path(image_path).exists():
        logger.error(f"이미지 없음: {image_path}")
        return None

    # 4) 배경음악
    bgm_path = _get_random_bgm() if add_bgm else None

    # 5) FFmpeg 합성
    fonts = register_fonts()
    # ass 필터에서 경로의 특수문자 이스케이프 (콜론, 작은따옴표, 백슬래시)
    def _escape_ass_path(p: str) -> str:
        return p.replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    ass_escaped   = _escape_ass_path(str(ass_file))
    fontsdir_esc  = _escape_ass_path(fonts["dir"])

    # 켄번스 효과: 이미지를 9:16에 맞춰 채우고 천천히 줌인
    # zoompan은 fps 기반, d = 총 프레임 수
    fps = 30
    total_frames = int(total_duration * fps) + fps

    vf_filter = (
        f"scale={VIDEO_W*2}:{VIDEO_H*2}:force_original_aspect_ratio=increase,"
        f"crop={VIDEO_W*2}:{VIDEO_H*2},"
        f"zoompan=z='min(zoom+0.0008,1.3)':d={total_frames}:"
        f"x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':s={VIDEO_W}x{VIDEO_H}:fps={fps},"
        f"ass=f='{ass_escaped}':fontsdir='{fontsdir_esc}'"
    )

    if bgm_path:
        # 나레이션 + 배경음악 믹스
        cmd = [
            FFMPEG, "-y",
            "-loop", "1", "-i", image_path,            # 0: 이미지
            "-i", str(narration_audio),                 # 1: 나레이션
            "-i", bgm_path,                             # 2: 배경음악
            "-filter_complex",
            f"[0:v]{vf_filter}[v];"
            f"[2:a]volume={BGM_VOLUME},aloop=loop=-1:size=2e9[bgm];"
            f"[1:a][bgm]amix=inputs=2:duration=first:dropout_transition=2[a]",
            "-map", "[v]", "-map", "[a]",
            "-t", str(total_duration),
            "-c:v", "h264_videotoolbox", "-b:v", "6M",   # M4 하드웨어 가속 인코딩
            "-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
            str(out_file)
        ]
    else:
        cmd = [
            FFMPEG, "-y",
            "-loop", "1", "-i", image_path,
            "-i", str(narration_audio),
            "-vf", vf_filter,
            "-map", "0:v", "-map", "1:a",
            "-t", str(total_duration),
            "-c:v", "h264_videotoolbox", "-b:v", "6M",
            "-c:a", "aac", "-b:a", "192k",
            "-pix_fmt", "yuv420p",
            "-r", str(fps),
            str(out_file)
        ]

    logger.info(f"  🎬 FFmpeg 합성 중... (BGM: {'있음' if bgm_path else '없음'})")
    result = subprocess.run(cmd, capture_output=True, text=True)

    if result.returncode != 0:
        logger.error(f"FFmpeg 오류:\n{result.stderr[-1500:]}")
        return None

    logger.success(f"  ✅ 영상 완성: {out_file}")
    return str(out_file)


def _build_session_subtitle(lines: list[dict], ass_file: Path):
    """라인들의 누적 타이밍을 반영해 ASS 생성"""
    # build_word_highlight_ass는 time_offset 기준 누적 처리하므로
    # 각 라인을 순서대로 넘기면 내부에서 cursor 누적됨
    build_word_highlight_ass(lines, ass_file, time_offset=0.0)


# ──────────────────────────────────────────────
# 세션 단위 영상 생성
# ──────────────────────────────────────────────
def compose_videos_for_session(session_id: str, add_bgm: bool = True) -> str:
    session_dir = BASE_INPUT_DIR / session_id
    audio_json  = session_dir / "audio.json"
    scripts_json = session_dir / "scripts.json"

    if not audio_json.exists():
        logger.error(f"audio.json 없음: {session_id}")
        return ""

    with open(audio_json, encoding="utf-8") as f:
        audio_data = json.load(f)
    with open(scripts_json, encoding="utf-8") as f:
        scripts_data = json.load(f)

    audios  = audio_data.get("audios", [])
    scripts = scripts_data.get("scripts", [])

    video_dir = session_dir / "videos"
    video_dir.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 50)
    logger.info(f"STEP 4-2 — 영상 합성 시작 (세션: {session_id})")
    logger.info(f"영상 {len(audios)}개 생성")
    logger.info("=" * 50)

    results = []
    for i, audio_info in enumerate(audios):
        # 대응되는 스크립트에서 이미지 경로 가져오기
        image_path = scripts[i].get("image_path") if i < len(scripts) else None

        out_file = video_dir / f"shorts_{i+1:02d}.mp4"
        logger.info(f"\n[{i+1}/{len(audios)}] {audio_info.get('script_title', '')[:30]}")

        video_path = compose_video(audio_info, image_path, out_file, add_bgm=add_bgm)
        if video_path:
            results.append({
                "video_path":   video_path,
                "script_title": audio_info.get("script_title"),
                "product_name": audio_info.get("product_name"),
                "duration":     _get_duration(Path(video_path)),
            })

    # videos.json 저장
    videos_json = session_dir / "videos.json"
    with open(videos_json, "w", encoding="utf-8") as f:
        json.dump({"session_id": session_id, "total": len(results), "videos": results},
                  f, ensure_ascii=False, indent=2)

    # 상태 업데이트
    session_file = session_dir / "session.json"
    if session_file.exists():
        with open(session_file, encoding="utf-8") as f:
            session = json.load(f)
        session["status"] = "video_generated"
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)

    logger.info("\n" + "=" * 50)
    logger.info(f"STEP 4-2 완료 — {len(results)}개 영상 생성")
    logger.info(f"💾 {videos_json}")
    logger.info("=" * 50)
    return str(videos_json)


if __name__ == "__main__":
    from src.product.coupang_crawler import list_sessions

    sessions = list_sessions()
    target = None
    for s in sessions:
        if (BASE_INPUT_DIR / s["session_id"] / "audio.json").exists():
            target = s["session_id"]
            break

    if not target:
        print("❌ audio.json 있는 세션 없음. STEP 4-1 먼저 실행.")
        sys.exit(1)

    print(f"📋 대상 세션: {target}\n")
    videos_json = compose_videos_for_session(target, add_bgm=True)

    with open(videos_json, encoding="utf-8") as f:
        data = json.load(f)

    print(f"\n{'='*50}")
    print(f"🎬 생성된 영상: {data['total']}개")
    for v in data["videos"]:
        print(f"\n📹 {v['script_title']}")
        print(f"   길이: {v['duration']:.1f}초")
        print(f"   재생: open '{v['video_path']}'")
