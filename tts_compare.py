"""
STEP 4-1 — 한국어 TTS 비교: MeloTTS vs edge-tts
같은 문장을 두 엔진으로 생성해서 음성 품질 비교.
"""

import os
import asyncio
from pathlib import Path
from loguru import logger

OUTPUT_DIR = Path(os.path.expanduser("~/vids-auto-engine/vids-app/tts_compare_output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 실제 대본에서 발췌한 테스트 문장 (후킹 + CTA)
TEST_TEXT = "하루 10분이 헬스장 1시간보다 효과가 좋다? 믿어지나요? 지금 바로 확인해보세요!"


# ──────────────────────────────────────────────
# A. MeloTTS (완전 로컬)
# ──────────────────────────────────────────────
def generate_melotts():
    logger.info("=" * 50)
    logger.info("A. MeloTTS 한국어 생성 (로컬)")
    logger.info("=" * 50)
    try:
        from melo.api import TTS

        # 한국어 모델 로드 (M4는 mps 대신 cpu가 안정적)
        device = "cpu"
        model  = TTS(language="KR", device=device)
        speaker_ids = model.hps.data.spk2id

        out_file = OUTPUT_DIR / "A_melotts.wav"
        # 속도 1.0 기본, 0.9면 약간 느리게
        model.tts_to_file(TEST_TEXT, speaker_ids["KR"], str(out_file), speed=1.0)

        logger.success(f"✅ MeloTTS 완료: {out_file}")
        return str(out_file)
    except Exception as e:
        logger.error(f"❌ MeloTTS 실패: {e}")
        import traceback
        traceback.print_exc()
        return None


# ──────────────────────────────────────────────
# C. edge-tts (MS Edge 음성, 인터넷 필요)
# ──────────────────────────────────────────────
async def generate_edge_tts():
    logger.info("=" * 50)
    logger.info("C. edge-tts 한국어 생성 (온라인)")
    logger.info("=" * 50)
    try:
        import edge_tts

        # 한국어 음성 2종 비교
        voices = {
            "C1_SunHi_여성": "ko-KR-SunHiNeural",
            "C2_InJoon_남성": "ko-KR-InJoonNeural",
        }

        results = []
        for label, voice in voices.items():
            out_file = OUTPUT_DIR / f"{label}.mp3"
            communicate = edge_tts.Communicate(TEST_TEXT, voice)
            await communicate.save(str(out_file))
            logger.success(f"✅ edge-tts [{voice}] 완료: {out_file}")
            results.append(str(out_file))

        return results
    except Exception as e:
        logger.error(f"❌ edge-tts 실패: {e}")
        import traceback
        traceback.print_exc()
        return None


# ──────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────
def main():
    print(f"\n📝 테스트 문장:\n   \"{TEST_TEXT}\"\n")

    # A. MeloTTS
    melo_result = generate_melotts()

    # C. edge-tts
    edge_results = asyncio.run(generate_edge_tts())

    # 결과 정리
    print("\n" + "=" * 50)
    print("🔊 생성 완료 — 아래 명령어로 들어보세요")
    print("=" * 50)

    if melo_result:
        print(f"\n[A] MeloTTS (로컬):")
        print(f"  afplay '{melo_result}'")

    if edge_results:
        print(f"\n[C] edge-tts (온라인):")
        for r in edge_results:
            print(f"  afplay '{r}'")

    print("\n" + "=" * 50)
    print("💡 전부 한번에 순서대로 들으려면:")
    all_files = []
    if melo_result:
        all_files.append(melo_result)
    if edge_results:
        all_files.extend(edge_results)
    cmd = " && ".join([f"afplay '{f}'" for f in all_files])
    print(f"  {cmd}")
    print("=" * 50)


if __name__ == "__main__":
    main()