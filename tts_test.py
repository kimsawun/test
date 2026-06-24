"""
STEP 4-1 — Kokoro TTS 한국어 지원 검증 테스트
한국어 음성팩이 실제로 작동하는지, 품질이 쓸만한지 확인.
실패하면 Piper TTS로 전환.
"""

import os
import sys
from pathlib import Path
from loguru import logger

OUTPUT_DIR = Path(os.path.expanduser("~/vids-auto-engine/vids-app/tts_test_output"))
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

# 테스트할 한국어 문장 (실제 대본에서 발췌)
TEST_TEXT = "하루 10분이 헬스장 1시간보다 효과가 좋다? 믿어지나요?"


def test_kokoro_korean():
    """Kokoro로 한국어 음성 생성 시도"""
    logger.info("=" * 50)
    logger.info("Kokoro TTS 한국어 테스트 시작")
    logger.info("=" * 50)

    try:
        from kokoro import KPipeline
        import soundfile as sf
        import numpy as np
    except ImportError as e:
        logger.error(f"패키지 import 실패: {e}")
        logger.error("→ pip install kokoro soundfile 확인 필요")
        return False

    # 한국어 코드: 'k' (Kokoro 언어 코드)
    # 한국어 음성팩 후보들
    korean_voices = ["kf_dora", "km_jihun", "kf_a", "km_a"]

    for lang_code in ["k", "ko"]:
        try:
            logger.info(f"\n언어 코드 '{lang_code}' 시도 중...")
            pipeline = KPipeline(lang_code=lang_code)

            for voice in korean_voices:
                try:
                    logger.info(f"  음성팩 '{voice}' 테스트...")
                    generator = pipeline(TEST_TEXT, voice=voice)

                    audio_chunks = []
                    for i, (gs, ps, audio) in enumerate(generator):
                        audio_chunks.append(audio)

                    if audio_chunks:
                        full_audio = np.concatenate(audio_chunks)
                        out_file = OUTPUT_DIR / f"kokoro_{lang_code}_{voice}.wav"
                        sf.write(str(out_file), full_audio, 24000)
                        logger.success(f"  ✅ 성공! 저장: {out_file}")
                        logger.info(f"     길이: {len(full_audio)/24000:.1f}초")
                        return str(out_file)

                except Exception as e:
                    logger.warning(f"  음성팩 '{voice}' 실패: {str(e)[:80]}")
                    continue

        except Exception as e:
            logger.warning(f"언어 코드 '{lang_code}' 실패: {str(e)[:80]}")
            continue

    logger.error("❌ Kokoro 한국어 생성 실패 — Piper TTS로 전환 필요")
    return False


def test_piper_korean():
    """Piper TTS 한국어 대안 테스트 (Kokoro 실패 시)"""
    logger.info("\n" + "=" * 50)
    logger.info("Piper TTS 한국어 대안 안내")
    logger.info("=" * 50)
    logger.info("Kokoro가 안 되면 아래로 Piper를 설치합니다:")
    logger.info("  pip install piper-tts")
    logger.info("  한국어 모델 다운로드 후 사용")
    logger.info("우선 Kokoro 결과를 확인하세요.")


if __name__ == "__main__":
    result = test_kokoro_korean()

    if result:
        print("\n" + "=" * 50)
        print("✅ Kokoro 한국어 작동 확인!")
        print(f"🔊 생성된 음성 파일: {result}")
        print("=" * 50)
        print("\n다음 명령어로 음성을 들어보세요:")
        print(f"  afplay '{result}'")
        print("\n음성 품질이 괜찮으면 Kokoro로 진행합니다.")
        print("품질이 별로면 Piper로 전환하겠습니다.")
    else:
        test_piper_korean()
        print("\n❌ Kokoro 한국어가 작동하지 않습니다.")
        print("Piper TTS로 전환을 진행하겠습니다.")