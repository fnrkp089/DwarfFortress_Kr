"""설치 파일(msi)을 만드는 도구를 프로젝트 안 .toolchain/ 에만 받아 둔다 (시스템에는 아무것도 깔지 않는다).

    python tools/get_wix.py          # 없으면 받고, 있으면 그대로 둔다
    python tools/get_wix.py --check  # 받지 않고 있는지만 본다

받는 것 (전부 공식 배포처, SHA512 를 여기 적힌 값과 견준 뒤에 푼다):
  .toolchain/dotnet/   .NET 런타임 (Microsoft). WiX 를 돌리는 데만 쓴다.
  .toolchain/wix/      WiX Toolset 5.0.2 (nuget.org 의 wix, WixToolset.Util.wixext)

지우려면 두 폴더를 지우면 된다.
"""
import hashlib
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TOOLCHAIN = ROOT / ".toolchain"
DOTNET = TOOLCHAIN / "dotnet"
WIX = TOOLCHAIN / "wix"

DOTNET_VERSION = "10.0.15"      # 지원 중인 장기 지원판
WIX_VERSION = "5.0.2"
PARTS = [
    # (이름, 주소, SHA512, 풀 폴더)
    ("dotnet-runtime", f"https://builds.dotnet.microsoft.com/dotnet/Runtime/{DOTNET_VERSION}/dotnet-runtime-{DOTNET_VERSION}-win-x64.zip",
     "06f0bedd59ae4ac722ada805a6d6bbf605bcdc790c71f59b7003878072902b5cde5dec8c40c0baa81192660d4748e9d79afdbdcb0c0ea9d6b821d0a044c4c6dd",
     DOTNET),
    ("wix", f"https://api.nuget.org/v3-flatcontainer/wix/{WIX_VERSION}/wix.{WIX_VERSION}.nupkg",
     "cfe7eed5d79371a57e4d10a8e3ee87ac9e3c49d9e3fb27908e3312b53237196ecdc4510f6a49d809dc6b7f4a84be0757ae361d8cb9e464a0ac352a934cea3638",
     WIX / "wix"),
    ("wix-util", f"https://api.nuget.org/v3-flatcontainer/wixtoolset.util.wixext/{WIX_VERSION}/wixtoolset.util.wixext.{WIX_VERSION}.nupkg",
     "ca4887333df3f54972d4aba2a6f1d0866e1c36d6cce89800df9dc86b2aab7534cb9c71203ba51a8bdc364ffd48697760c63ad0e8f8df11a890e2460c79d378df",
     WIX / "util"),
]

DOTNET_EXE = DOTNET / "dotnet.exe"
WIX_DLL = WIX / "wix" / "tools" / "net6.0" / "any" / "wix.dll"
UTIL_EXT = WIX / "util" / "wixext5" / "WixToolset.Util.wixext.dll"


def ready() -> bool:
    return DOTNET_EXE.is_file() and WIX_DLL.is_file() and UTIL_EXT.is_file()


def sha512(path: Path) -> str:
    h = hashlib.sha512()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def fetch():
    cache = TOOLCHAIN / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    for name, url, digest, target in PARTS:
        archive = cache / url.rsplit("/", 1)[1]
        if not (archive.is_file() and sha512(archive) == digest):
            print(f"받는 중: {url}")
            part = archive.with_name(archive.name + ".part")
            for attempt in range(1, 4):     # 서버가 연결을 끊는 일이 있다: 세 번까지 다시 받는다
                try:
                    req = urllib.request.Request(url, headers={"User-Agent": "build-tool-fetch"})
                    with urllib.request.urlopen(req, timeout=300) as resp, open(part, "wb") as f:
                        shutil.copyfileobj(resp, f)
                    break
                except OSError as e:
                    part.unlink(missing_ok=True)
                    if attempt == 3:
                        sys.exit(f"받지 못함: {name} ({e}). 잠시 뒤에 다시 돌리면 된다.")
                    print(f"  끊김 ({e}). 다시 받는다 ({attempt}/3)")
                    time.sleep(3 * attempt)
            part.replace(archive)
            if sha512(archive) != digest:
                archive.unlink()
                sys.exit(f"해시가 다름: {name}. 받지 않았다.")
        if target.exists():
            shutil.rmtree(target)
        target.mkdir(parents=True)
        with zipfile.ZipFile(archive) as z:
            z.extractall(target)
        print(f"풀었음: {target.relative_to(ROOT)}")


def wix_command() -> list:
    """wix 를 부르는 명령의 앞부분. 뒤에 build … 를 붙여 쓴다."""
    return [str(DOTNET_EXE), str(WIX_DLL)]


def wix_env(env: dict) -> dict:
    env = dict(env)
    env["DOTNET_ROOT"] = str(DOTNET)
    env["DOTNET_ROLL_FORWARD"] = "Major"      # WiX 5 는 .NET 6 용으로 묶여 있다: 더 새 런타임에서 돌린다
    env["DOTNET_CLI_TELEMETRY_OPTOUT"] = "1"
    env["DOTNET_NOLOGO"] = "1"
    return env


if __name__ == "__main__":
    import os
    if "--check" in sys.argv:
        print("있음" if ready() else "없음")
        sys.exit(0 if ready() else 1)
    if not ready():
        fetch()
    out = subprocess.run(wix_command() + ["--version"], capture_output=True, text=True, env=wix_env(os.environ))
    print("wix", (out.stdout or out.stderr).strip())
    sys.exit(0 if out.returncode == 0 and ready() else 1)
