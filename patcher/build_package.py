r"""배포 패키지 만들기: 설치 파일(msi) + 업데이트용 묶음(zip) + SHA256SUMS.

    python build_package.py                      # release/v<번호>/ 에 만든다
    python build_package.py --out 폴더
    python build_package.py --no-msi             # 설치 파일 없이 폴더와 업데이트용 묶음만
    python build_package.py --payload 폴더        # 게임에 넣는 패치(dfhooks.dll, dfkr-data)가 든 폴더 (기본: dist)
    (시험용) --version 0.7.99 --api-base http://127.0.0.1:8765 --download-base http://127.0.0.1:8765
             번호와 내려받는 곳만 다른 패키지를 만든다 (업데이트를 이 PC 안에서 시험할 때)

PyInstaller 를 쓰지 않는다. python.org 가 배포하는 윈도우용 Python 을 받아 해시를 확인하고, 프로그램 소스(.py)와
패치 파일을 그대로 옆에 둔 폴더를 만든다. runtime\ 의 실행 파일(exe/dll/pyd)은 모두 Python Software Foundation 과
Microsoft 의 서명본이다. 직접 만든 실행 파일은 게임에 넣는 dfhooks.dll 하나뿐이다.

    <패키지>\
      DwarfFortress_KR.cmd      실행용 배치 파일
      README.txt
      runtime\                  Python 공식 배포본
      app\                      patcher.py, 아이콘, 루트 인증서 묶음, package.json,
                                dfhooks.dll, dfkr-data\ (게임에 넣는 패치), 라이선스

업데이트용 묶음은 app\ 폴더만 묶은 것이다. 깔린 프로그램이 이것을 받아 app\ 를 통째로 바꾼다.
"""
import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
# 작업장에서는 tools/ 아래, 공개 저장소에서는 patcher/ 아래에 있다
ROOT = HERE.parent
PATCHER = next(p for p in (ROOT / "patcher.py", HERE / "patcher.py") if p.exists())
WXS = next(p for p in (ROOT / "installer" / "installer.wxs", HERE / "installer.wxs") if p.exists())
ASSETS = next(p for p in (ROOT / "assets", HERE) if (p / "icon.ico").exists())

PYTHON_VERSION = "3.14.7"
PYTHON_URL = f"https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-amd64.zip"
PYTHON_SHA256 = "ac1a727a71738e11de80b76e975f9b8a258aea6412bfc31696b929d59c6aafd0"
PYTHON_TAG = "python" + "".join(PYTHON_VERSION.split(".")[:2])

# 루트 인증서 묶음 (Mozilla 목록, certifi 배포본에서 cacert.pem 만 꺼낸다)
CERTIFI_URL = ("https://files.pythonhosted.org/packages/0b/a7/71ac2cff56fec219ed242bb11b8efb69fcc4bec75db06fb7bfe35de520e6/"
               "certifi-2026.7.22-py3-none-any.whl")
CERTIFI_SHA256 = "62f22742b58a1a33014a2b6b706588a8d7e2a88ae7bd1a6ebe8c992928483775"
CA_BUNDLE = "cacert.pem"

PACKAGE_NAME = "DwarfFortress_KR"
APP_ASSETS = ["icon.ico", "icon.png", "icon@2x.png"]

# 프로그램이 쓰지 않는 런타임 구성요소
PRUNE_ROOT = ["Doc", "include", "libs", "Scripts", "__install__.json"]
PRUNE_LIB = ["test", "idlelib", "turtledemo", "ensurepip", "site-packages", "venv", "pydoc_data", "__phello__"]
ZIP_DATE = (2020, 1, 1, 0, 0, 0)

LAUNCHER = r"""@echo off
if not exist "%~dp0runtime\pythonw.exe" goto missing
if not "%~1"=="" goto cli
start "" "%~dp0runtime\pythonw.exe" -B "%~dp0app\patcher.py"
exit /b
:cli
"%~dp0runtime\python.exe" -B "%~dp0app\patcher.py" %*
exit /b %errorlevel%
:missing
chcp 65001 >nul
echo 설치 폴더가 온전하지 않습니다. 설치 파일을 다시 실행해 주세요.
pause
"""

README = """Dwarf Fortress 한국어 패치 {version}

실행: 바탕 화면이나 시작 메뉴의 "Dwarf Fortress 한국어 패치"

들어 있는 것
  DwarfFortress_KR.cmd   실행용 배치 파일 (메모장으로 열어 볼 수 있습니다)
  app\\                   프로그램 소스 patcher.py (메모장으로 열어 볼 수 있습니다), 루트 인증서 목록 cacert.pem (Mozilla),
                         게임에 넣는 패치 (dfhooks.dll, dfkr-data\\), 라이선스
  runtime\\               Python {python} 공식 배포본 (python.org, Python Software Foundation 서명)

게임 원본 파일은 고치지 않습니다. 게임 폴더에 dfhooks.dll (DFHack 이 있으면 dfhooks_kr.dll) 과 dfkr-data 폴더만 넣고,
[원래대로]를 누르면 그 둘을 지웁니다.

비공식 팬 번역입니다. Dwarf Fortress 의 저작권은 Bay 12 Games 에 있고, 이 패키지에 게임 파일은 들어 있지 않습니다.
안내와 제보: {repo}
"""


def sha256_file(path):
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def repo_url():
    m = re.search(r'^GITHUB_REPO = "([^"]+)"', PATCHER.read_text(encoding="utf-8"), re.M)
    if not m:
        raise RuntimeError("patcher.py 에서 GITHUB_REPO 를 찾을 수 없음")
    return f"https://github.com/{m.group(1)}"


def fetch(cache, url, sha256):
    cache.mkdir(parents=True, exist_ok=True)
    archive = cache / Path(url).name
    if not (archive.is_file() and sha256_file(archive) == sha256):
        print(f"받는 중: {url}")
        req = urllib.request.Request(url, headers={"User-Agent": "build-tool-fetch"})
        with urllib.request.urlopen(req, timeout=300) as resp, open(archive, "wb") as f:
            shutil.copyfileobj(resp, f)
        if sha256_file(archive) != sha256:
            archive.unlink()
            raise RuntimeError(f"해시 불일치: {archive.name}")
    return archive


def write_zip(target, root, files):
    """경로순·고정 시각으로 기록 (같은 입력이면 같은 zip)"""
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
            info = zipfile.ZipInfo(path.relative_to(root).as_posix(), ZIP_DATE)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            z.writestr(info, path.read_bytes())


def build_runtime(archive, runtime):
    with zipfile.ZipFile(archive) as z:
        z.extractall(runtime)
    for name in PRUNE_ROOT:
        target = runtime / name
        shutil.rmtree(target) if target.is_dir() else target.unlink(missing_ok=True)
    lib = runtime / "Lib"
    for name in PRUNE_LIB:
        shutil.rmtree(lib / name, ignore_errors=True)
    for cache in list(lib.rglob("__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)

    # 표준 라이브러리는 미리 컴파일해 zip 하나로 (python.org embeddable 배포본과 같은 구성).
    # -d Lib: 컴파일된 파일에는 소스 파일의 경로가 박힌다. 만드는 PC 의 경로 대신 "Lib\이름.py" 로 적는다.
    # -B: 컴파일하는 동안 __pycache__ 를 만들지 않는다 (만들어지면 그대로 묶여 들어간다).
    subprocess.run([str(runtime / "python.exe"), "-I", "-B", "-m", "compileall", "-b", "-q", "-d", "Lib",
                    "--invalidation-mode", "unchecked-hash", str(lib)], check=True)
    for cache in list(lib.rglob("__pycache__")):
        shutil.rmtree(cache, ignore_errors=True)
    write_zip(runtime / f"{PYTHON_TAG}.zip", lib,
              [p for p in lib.rglob("*") if p.is_file() and p.suffix != ".py"])
    shutil.rmtree(lib)
    # ._pth: 받는 사람 PC 의 PYTHONPATH·레지스트리·site-packages 와 분리
    (runtime / f"{PYTHON_TAG}._pth").write_text(f"{PYTHON_TAG}.zip\nDLLs\n..\\app\n", encoding="ascii", newline="\r\n")


def verify_signatures(runtime):
    """runtime 의 exe/dll/pyd 가 전부 유효한 서명본인지 확인. 하나라도 아니면 빌드 실패"""
    script = ("$bin = @(Get-ChildItem -LiteralPath $args[0] -Recurse -File | "
              "Where-Object { '.exe','.dll','.pyd' -contains $_.Extension.ToLower() }); "
              "if ($bin.Count -lt 10) { 'binaries: ' + $bin.Count; exit 1 }; "
              "$bad = @($bin | Where-Object { (Get-AuthenticodeSignature -LiteralPath $_.FullName).Status -ne 'Valid' }); "
              "$bad | ForEach-Object { $_.FullName }; if ($bad.Count) { exit 1 }")
    result = subprocess.run(["powershell", "-NoProfile", "-Command", f"& {{ {script} }}", str(runtime)],
                            capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(f"서명 없는 실행 파일: {result.stdout.strip() or result.stderr.strip()}")


def build_app(app, dist, version, repo, api_base=None, download_base=None):
    """app 폴더: 프로그램 소스 + 아이콘 + 인증서 묶음 + 패치 파일(dist 의 dfhooks.dll, dfkr-data)."""
    app.mkdir(parents=True)
    source = PATCHER.read_text(encoding="utf-8")
    if api_base or download_base:   # 시험용: 내려받는 곳만 바꾼 사본
        for name, value in (("API_BASE", api_base), ("DOWNLOAD_BASE", download_base)):
            if value:
                source, n = re.subn(rf'^{name} = "[^"]+"', f'{name} = "{value}"', source, flags=re.M)
                assert n == 1, name
    (app / "patcher.py").write_text(source, encoding="utf-8", newline="\n")
    for name in APP_ASSETS:
        shutil.copyfile(ASSETS / name, app / name)
    shutil.copyfile(dist / "dfhooks.dll", app / "dfhooks.dll")
    shutil.copytree(dist / "dfkr-data", app / "dfkr-data")
    (app / "dfkr-data" / "version.txt").write_text(version + "\n", encoding="utf-8", newline="\n")
    for name in ("LICENSE", "LICENSE-DATA.txt", "LICENSE-THIRD-PARTY.txt", "RELEASE_NOTES.md"):
        src = next((p for p in (ROOT / name, HERE / name, dist / "dfkr-data" / name) if p.exists()), None)
        if src:
            shutil.copyfile(src, app / name)
    # 프로그램의 자체 업데이트가 읽는다: python 이 같을 때만 app 폴더만 바꾼다
    (app / "package.json").write_text(json.dumps({"version": version, "python": PYTHON_VERSION}, indent=2) + "\n",
                                      encoding="utf-8", newline="\n")
    leftovers = [p.name for p in (app / "dfkr-data").iterdir()
                 if p.name not in ("config.txt", "offsets.txt", "version.txt", "LICENSE-THIRD-PARTY.txt", "LICENSE-DATA.txt", "dict", "fonts")]
    if leftovers:
        raise RuntimeError(f"dist/dfkr-data 에 배포하지 않는 파일이 있음: {leftovers}")


def build_msi(package, version, target, repo):
    sys.path[:0] = [str(HERE), str(ROOT / "tools")]
    try:
        import get_wix
    except ImportError:
        raise RuntimeError("get_wix.py 가 없음")
    if not get_wix.ready():
        raise RuntimeError("WiX 가 없음. 먼저 get_wix.py 를 실행")
    cmd = get_wix.wix_command() + ["build", "-ext", str(get_wix.UTIL_EXT), "-d", f"Version={version}",
                                   "-d", f"SourceDir={package}", "-d", f"RepoUrl={repo}", "-o", str(target), str(WXS)]
    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=get_wix.wix_env(os.environ))
    if result.returncode != 0 or not target.is_file():
        raise RuntimeError("wix build 실패:\n" + (result.stdout + result.stderr)[-3000:])
    target.with_suffix(".wixpdb").unlink(missing_ok=True)


def build(out, version=None, msi=True, api_base=None, download_base=None, cache=None, payload=None):
    if sys.platform != "win32":
        raise RuntimeError("Windows 에서만 빌드 가능")
    dist = Path(payload).resolve() if payload else ROOT / "dist"
    if not (dist / "dfhooks.dll").is_file() or not (dist / "dfkr-data").is_dir():
        raise RuntimeError("게임에 넣는 패치(dfhooks.dll, dfkr-data)가 없음. --payload 로 그 둘이 든 폴더를 알려 준다 "
                           "(릴리즈의 업데이트용 묶음을 푼 폴더를 쓰면 된다)")
    real = (dist / "dfkr-data" / "version.txt").read_text(encoding="utf-8").strip()
    version = version or real
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        raise RuntimeError(f"번호가 x.y.z 꼴이 아님: {version}")
    repo = repo_url()
    cache = cache or ROOT / ".toolchain" / "cache"
    out.mkdir(parents=True, exist_ok=True)
    package = out / PACKAGE_NAME
    if package.exists():
        shutil.rmtree(package)
    package.mkdir(parents=True)

    build_runtime(fetch(cache, PYTHON_URL, PYTHON_SHA256), package / "runtime")
    build_app(package / "app", dist, version, repo, api_base, download_base)
    with zipfile.ZipFile(fetch(cache, CERTIFI_URL, CERTIFI_SHA256)) as z:
        (package / "app" / CA_BUNDLE).write_bytes(z.read(f"certifi/{CA_BUNDLE}"))
    # cmd 는 UTF-8(BOM 없음)·CRLF. 한글은 chcp 65001 뒤에서만 출력
    (package / f"{PACKAGE_NAME}.cmd").write_text(LAUNCHER, encoding="utf-8", newline="\r\n")
    (package / "README.txt").write_text(README.format(version=version, python=PYTHON_VERSION, repo=repo),
                                        encoding="utf-8-sig", newline="\r\n")
    verify_signatures(package / "runtime")
    # 새 프로그램이 이 런타임에서 뜨는지
    check = subprocess.run([str(package / "runtime" / "python.exe"), "-B", str(package / "app" / "patcher.py"), "--self-check"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check.returncode != 0 or check.stdout.strip() != version:
        raise RuntimeError(f"패키지의 프로그램이 뜨지 않음: {check.stdout} {check.stderr}")

    made = []
    # 업데이트용: app 폴더만 (깔린 프로그램은 이것만 받아 바꾼다)
    update_zip = out / f"{PACKAGE_NAME}-update-v{version}.zip"
    write_zip(update_zip, package / "app", [p for p in (package / "app").rglob("*") if p.is_file()])
    made.append(update_zip)
    if msi:
        target = out / f"{PACKAGE_NAME}-v{version}.msi"
        target.unlink(missing_ok=True)
        build_msi(package, version, target, repo)
        made.insert(0, target)
    sums = "".join(f"{sha256_file(p)}  {p.name}\n" for p in made)
    (out / "SHA256SUMS").write_text(sums, encoding="ascii", newline="\n")
    for p in made:
        print(f"{p.name}  {p.stat().st_size / 1e6:.1f} MB")
    print(sums, end="")
    return made


def main():
    parser = argparse.ArgumentParser(description="배포 패키지 만들기")
    parser.add_argument("--out", help="출력 폴더 (기본: release/v<번호>)")
    parser.add_argument("--version", help="번호를 바꿔 찍기 (시험용)")
    parser.add_argument("--no-msi", action="store_true")
    parser.add_argument("--payload", help="게임에 넣는 패치(dfhooks.dll, dfkr-data)가 든 폴더 (기본: dist)")
    parser.add_argument("--api-base", help="시험용: 릴리즈 목록을 묻는 곳")
    parser.add_argument("--download-base", help="시험용: 파일을 받는 곳")
    a = parser.parse_args()
    payload = Path(a.payload).resolve() if a.payload else ROOT / "dist"
    version = a.version or (payload / "dfkr-data" / "version.txt").read_text(encoding="utf-8").strip()
    out = Path(a.out).resolve() if a.out else ROOT / "release" / f"v{version}"
    build(out, a.version, not a.no_msi, a.api_base, a.download_base, payload=a.payload)


if __name__ == "__main__":
    main()
