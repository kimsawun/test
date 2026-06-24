"""
공통 미디어 설정 — ffmpeg/ffprobe 바이너리 경로 중앙 관리
libass 포함 정적 빌드를 우선 사용하고, 없으면 시스템 ffmpeg로 폴백.
"""

import os
import shutil
from pathlib import Path

# libass 포함 정적 빌드 경로
_STATIC_BIN = Path(os.path.expanduser("~/vids-auto-engine/vids-backend/assets/bin"))
_STATIC_FFMPEG  = _STATIC_BIN / "ffmpeg"
_STATIC_FFPROBE = _STATIC_BIN / "ffprobe"


def get_ffmpeg() -> str:
    """ffmpeg 경로 반환 (정적 빌드 우선)"""
    if _STATIC_FFMPEG.exists():
        return str(_STATIC_FFMPEG)
    sys_ffmpeg = shutil.which("ffmpeg")
    return sys_ffmpeg or "ffmpeg"


def get_ffprobe() -> str:
    """ffprobe 경로 반환 (정적 빌드 우선)"""
    if _STATIC_FFPROBE.exists():
        return str(_STATIC_FFPROBE)
    sys_ffprobe = shutil.which("ffprobe")
    return sys_ffprobe or "ffprobe"


FFMPEG  = get_ffmpeg()
FFPROBE = get_ffprobe()


if __name__ == "__main__":
    print(f"FFMPEG  = {FFMPEG}")
    print(f"FFPROBE = {FFPROBE}")
    # ass 필터 확인
    import subprocess
    out = subprocess.run([FFMPEG, "-filters"], capture_output=True, text=True)
    has_ass = any(line.strip().split()[1:2] == ["ass"] for line in out.stdout.splitlines() if len(line.split()) > 1)
    print(f"ass 필터 사용 가능: {'✅' if 'ass ' in out.stdout or ' ass ' in out.stdout else '확인필요'}")
