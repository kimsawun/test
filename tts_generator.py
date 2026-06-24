"""
STEP 4-1 — edge-tts 기반 한국어 나레이션 생성 모듈
대본(scripts.json)을 읽어 장면별 나레이션 음성(mp3)을 생성한다.

- 음성: SunHi(여성) / InJoon(남성) 영상마다 번갈아
- 속도: 빠르게 (+15%) — 쇼츠 템포
- 출력: 각 세션 폴더의 audio/ 하위에 저장
"""

import os
import json
import asyncio
import subprocess
import re
from pathlib import Path
from typing import Optional
from loguru import logger
import edge_tts
import sys as _sys
_sys.path.insert(0, __import__("os").path.expanduser("~/vids-auto-engine/vids-app"))
from src.video.media_config import FFPROBE

BASE_INPUT_DIR = Path(os.path.expanduser("~/vids-auto-engine/vids-backend/input"))

# ──────────────────────────────────────────────
# 음성 설정
# ──────────────────────────────────────────────
VOICES = {
    "female": "ko-KR-SunHiNeural",   # 밝고 경쾌
    "male":   "ko-KR-InJoonNeural",  # 차분하고 신뢰감
}
# 쇼츠 템포 — 빠르게
TTS_RATE   = "+15%"   # 속도 (+0% 보통, +15% 빠르게)
TTS_VOLUME = "+0%"
TTS_PITCH  = "+0Hz"


def _probe_duration(media_file: Path) -> float:
    """ffprobe로 실제 음성 길이(초) 측정"""
    try:
        cmd = [
            FFPROBE, "-v", "error", "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1", str(media_file)
        ]
        out = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return round(float(out.stdout.strip()), 2)
    except Exception as e:
        logger.warning(f"길이 측정 실패: {e}")
        return 0.0


def _estimate_word_boundaries(text: str, duration: float) -> list[dict]:
    """
    edge-tts가 word boundary를 안 줄 때(한국어 이슈),
    텍스트를 공백 단위로 나눠 글자 수 비례로 타이밍을 추정한다.
    각 단어의 길이(글자 수)에 비례해 시간을 배분.
    """
    words = text.split()
    if not words or duration <= 0:
        return []

    # 글자 수 기반 가중치 (긴 단어가 더 오래 발음됨)
    weights = [max(len(w), 1) for w in words]
    total_w = sum(weights)

    boundaries = []
    cursor = 0.0
    for word, w in zip(words, weights):
        seg = duration * (w / total_w)
        boundaries.append({
            "text":     word,
            "offset":   round(cursor, 3),
            "duration": round(seg, 3),
        })
        cursor += seg
    return boundaries


class TTSGenerator:
    def __init__(self, voice_gender: str = "female"):
        self.voice = VOICES.get(voice_gender, VOICES["female"])
        self.gender = voice_gender

    async def generate_line(self, text: str, out_file: Path) -> Optional[dict]:
        """단일 나레이션 라인 음성 생성 + 실제 길이 측정"""
        try:
            communicate = edge_tts.Communicate(
                text, self.voice,
                rate=TTS_RATE, volume=TTS_VOLUME, pitch=TTS_PITCH
            )

            # 음성 + 단어별 타이밍(자막 싱크용 word boundary) 수집
            word_boundaries = []
            with open(out_file, "wb") as f:
                async for chunk in communicate.stream():
                    if chunk["type"] == "audio":
                        f.write(chunk["data"])
                    elif chunk["type"] == "WordBoundary":
                        word_boundaries.append({
                            "text":   chunk["text"],
                            "offset": chunk["offset"] / 10_000_000,   # 100ns → 초
                            "duration": chunk["duration"] / 10_000_000,
                        })

            # 실제 음성 길이는 항상 ffprobe로 측정 (가장 정확)
            duration = _probe_duration(out_file)

            # word boundary가 비어있으면 (edge-tts 한국어 이슈) 단어 균등 분배로 추정
            if not word_boundaries:
                word_boundaries = _estimate_word_boundaries(text, duration)

            return {
                "audio_path": str(out_file),
                "duration":   duration,
                "voice":      self.voice,
                "word_boundaries": word_boundaries,
            }
        except Exception as e:
            logger.error(f"TTS 생성 실패 ('{text[:20]}...'): {e}")
            return None

    async def generate_script(self, script: dict, audio_dir: Path, idx: int) -> dict:
        """
        하나의 대본(script) 전체를 음성화.
        hook + 각 scene narration + cta 를 개별 mp3로 생성.
        """
        audio_dir.mkdir(parents=True, exist_ok=True)
        result = {
            "voice_gender": self.gender,
            "voice":        self.voice,
            "lines":        [],
            "total_duration": 0.0,
        }

        # 음성화할 텍스트 라인 구성: hook → scenes → cta
        lines = []
        if script.get("hook"):
            lines.append(("hook", script["hook"]))
        for scene in script.get("scenes", []):
            narr = (scene.get("narration") or "").strip()
            if narr:
                lines.append((f"scene{scene.get('scene_no')}", narr))
        if script.get("cta"):
            lines.append(("cta", script["cta"]))

        total = 0.0
        for part_name, text in lines:
            out_file = audio_dir / f"vid{idx}_{part_name}.mp3"
            info = await self.generate_line(text, out_file)
            if info:
                info["part"] = part_name
                info["text"] = text
                result["lines"].append(info)
                total += info["duration"]
                logger.info(f"  🔊 {part_name}: {info['duration']}초 | {text[:25]}...")

        result["total_duration"] = round(total, 2)
        return result


# ──────────────────────────────────────────────
# 세션 단위 음성 생성
# ──────────────────────────────────────────────
async def generate_audio_for_session(session_id: str) -> str:
    """
    세션의 scripts.json을 읽어 모든 대본 음성화 → audio.json 저장
    영상마다 음성(남/여) 번갈아 사용.
    """
    session_dir  = BASE_INPUT_DIR / session_id
    scripts_file = session_dir / "scripts.json"

    if not scripts_file.exists():
        logger.error(f"scripts.json 없음: {session_id}")
        return ""

    with open(scripts_file, encoding="utf-8") as f:
        scripts_data = json.load(f)

    scripts   = scripts_data.get("scripts", [])
    audio_dir = session_dir / "audio"

    logger.info("=" * 50)
    logger.info(f"STEP 4-1 — TTS 음성 생성 시작 (세션: {session_id})")
    logger.info(f"대본 {len(scripts)}개 | 음성 번갈아 (여→남→여...)")
    logger.info("=" * 50)

    audio_results = []
    for i, script_item in enumerate(scripts):
        script = script_item.get("script", {})
        # 영상마다 음성 번갈아
        gender = "female" if i % 2 == 0 else "male"
        gen    = TTSGenerator(voice_gender=gender)

        logger.info(f"\n[{i+1}/{len(scripts)}] {script.get('title', '')[:30]} ({gender})")
        audio_info = await gen.generate_script(script, audio_dir, i)
        audio_info["product_name"]  = script_item.get("product_name")
        audio_info["script_title"]  = script.get("title")
        audio_results.append(audio_info)
        logger.info(f"  ⏱️  총 길이: {audio_info['total_duration']}초")

    # audio.json 저장
    audio_json = session_dir / "audio.json"
    with open(audio_json, "w", encoding="utf-8") as f:
        json.dump({
            "session_id": session_id,
            "total":      len(audio_results),
            "audios":     audio_results,
        }, f, ensure_ascii=False, indent=2)

    # 세션 상태 업데이트
    session_file = session_dir / "session.json"
    if session_file.exists():
        with open(session_file, encoding="utf-8") as f:
            session = json.load(f)
        session["status"] = "audio_generated"
        with open(session_file, "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)

    logger.info("\n" + "=" * 50)
    logger.info(f"STEP 4-1 완료 — {len(audio_results)}개 음성 세트 생성")
    logger.info(f"💾 저장: {audio_json}")
    logger.info("=" * 50)
    return str(audio_json)


# ──────────────────────────────────────────────
# 단독 테스트
# ──────────────────────────────────────────────
if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.expanduser("~/vids-auto-engine/vids-app"))
    from src.product.coupang_crawler import list_sessions

    sessions = list_sessions()
    if not sessions:
        print("❌ 세션 없음. STEP 2~3 먼저 실행.")
        sys.exit(1)

    # scripts.json 있는 최신 세션 찾기
    target = None
    for s in sessions:
        if (BASE_INPUT_DIR / s["session_id"] / "scripts.json").exists():
            target = s["session_id"]
            break

    if not target:
        print("❌ scripts.json 있는 세션 없음. STEP 3 먼저 실행.")
        sys.exit(1)

    print(f"📋 대상 세션: {target}\n")
    audio_json = asyncio.run(generate_audio_for_session(target))

    # 결과 요약
    with open(audio_json, encoding="utf-8") as f:
        data = json.load(f)

    print(f"\n{'='*50}")
    print(f"🔊 생성된 음성 세트: {data['total']}개")
    for a in data["audios"]:
        print(f"\n📹 {a['script_title']} ({a['voice_gender']})")
        print(f"   총 {a['total_duration']}초, {len(a['lines'])}개 라인")
        # 첫 라인 재생 명령어 제공
        if a["lines"]:
            print(f"   미리듣기: afplay '{a['lines'][0]['audio_path']}'")
