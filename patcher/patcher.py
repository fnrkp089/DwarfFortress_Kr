"""Dwarf Fortress 한국어 패치 - 설치 프로그램.

    (바로가기 더블클릭)                창이 뜨고 [한글패치 적용] 단추 한 번
    patcher.py install                명령줄로 적용
    patcher.py uninstall              원래대로 되돌리기
    patcher.py status                 상태 보기
    patcher.py update                 새 버전이 올라왔는지 보고, 있으면 받아서 프로그램을 바꾼다
    patcher.py record on|off          화면 기록 켜기 / 끄기 (제보용)
    patcher.py report [폴더]          제보 묶음(zip) 만들기 (기본: 바탕 화면)
    patcher.py font [이름|파일]       한글 글꼴 보기 / 바꾸기 (galmuri9 / galmuri11 / galmuri11-bold / 글꼴 파일. --bold 0~3, --scale 0.9)
    patcher.py names [translit=no meaning=yes]      이름 표기 보기 / 바꾸기
    patcher.py original [always|search|no] [people=yes|no]   이름 옆 영어 원문 보기 / 바꾸기
    patcher.py dict                   번역 사전만 갈아 끼우기 (게임이 켜져 있어도 됨. 게임에서 Ctrl+F3)
    (--game "경로" 로 게임 폴더 지정)

하는 일은 단순하다. 게임 폴더에 DLL 하나(`dfhooks.dll`. DFHack 이 있으면 `dfhooks_kr.dll`)와 `dfkr-data` 폴더를 넣는다.
게임 원본 파일은 하나도 고치지 않으므로, 되돌리기는 그 둘을 지우는 것뿐이다.
"""
import datetime
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

# ---------------------------------------------------------------- 내려받는 곳
# 새 버전은 이 저장소의 릴리즈에서만 받는다. 저장소를 옮기면 이 줄만 바꾼다.
GITHUB_REPO = "fnrkp089/DwarfFortress_Kr"
API_BASE = "https://api.github.com"
DOWNLOAD_BASE = "https://github.com"

RELEASES_API = f"{API_BASE}/repos/{GITHUB_REPO}/releases"
DOWNLOAD_PREFIX = f"{DOWNLOAD_BASE}/{GITHUB_REPO}/releases/download/"
REPO_URL = f"https://github.com/{GITHUB_REPO}"
ISSUES_URL = f"{REPO_URL}/issues"

APP_TITLE = "Dwarf Fortress 한국어 패치"
PACKAGE_NAME = "DwarfFortress_KR"
PACKAGE_INFO = "package.json"
CA_BUNDLE = "cacert.pem"
MAX_DOWNLOAD = 200 * 1024 * 1024

HERE = Path(__file__).resolve().parent
EXE_NAME = "Dwarf Fortress.exe"
DLL_NAME = "dfhooks.dll"
DLL_NAME_CHAINED = "dfhooks_kr.dll"      # DFHack 의 dfhooks.dll(연쇄 로더)이 있을 때 쓰는 이름
DLL_MARK = b"dfkr-data"                  # 우리 DLL 안에만 있는 글자
DFHACK_MARKS = ("dfhooks_dfhack.ini", "dfhooks_dfhack.dll", "hack/dfhooks_dfhack.dll")
DATA_NAME = "dfkr-data"
STEAM_APP_ID = "975370"
KEEP_ON_UPDATE = ("untranslated.txt", "config.txt")  # 다시 적용해도 지우지 않는 사용자 파일
RECORD_FLAG = "record.flag"
# 제보 묶음에 넣는 것 (dfkr-data 안. 사전과 글꼴은 넣지 않는다)
REPORT_FILES = ("untranslated.txt", "dfkr-log.txt", "status.txt", "disabled.txt", "installed.json", "config.txt", "version.txt")
# 같이 들어 있는 한글 글꼴: 이름 → (설명, 파일, 글자 틀(FONT_GRID), 굵기(FONT_BOLD))
FONT_PRESETS = {
    "galmuri9": ("갈무리9 (도트, 기본)", "Galmuri9.ttf", 10, 1),
    "galmuri11": ("갈무리11 (도트, 조금 큼)", "Galmuri11.ttf", 12, 0),
    "galmuri11-bold": ("갈무리11 굵게 (도트)", "Galmuri11-Bold.ttf", 12, 0),
}
FONT_EXTS = (".ttf", ".otf", ".ttc")
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

if hasattr(sys.stdout, "reconfigure") and sys.stdout is not None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


class PatchError(Exception):
    pass


# ---------------------------------------------------------------- 찾기

def package_dir():
    """설치 파일로 깔려서 실행 중이면 app 폴더 (옆에 runtime 폴더가 있다), 소스로 실행 중이면 None."""
    return HERE if (HERE / PACKAGE_INFO).is_file() and (HERE.parent / "runtime").is_dir() else None


def payload_dir() -> Path:
    """dfhooks.dll 과 dfkr-data 가 들어 있는 폴더 (깔린 프로그램의 app 폴더, 또는 작업장의 dist/)."""
    for cand in (HERE / "dist", HERE):
        if (cand / DLL_NAME).exists() and (cand / DATA_NAME).is_dir():
            return cand
    raise PatchError("패치 파일(dfhooks.dll, dfkr-data)을 찾지 못했습니다. 프로그램을 다시 설치해 주세요.")


def patch_version(payload: Path) -> str:
    v = payload / DATA_NAME / "version.txt"
    return v.read_text(encoding="utf-8").strip() if v.exists() else "dev"


def program_version() -> str:
    try:
        return patch_version(payload_dir())
    except PatchError:
        return "dev"


def settings_file():
    pkg = package_dir()
    return pkg.parent / "settings.json" if pkg else None


def load_settings() -> dict:
    f = settings_file()
    if f and f.is_file():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            pass
    return {}


def save_settings(**values):
    f = settings_file()
    if not f:
        return
    data = load_settings()
    data.update(values)
    try:
        f.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass


def steam_libraries():
    libs = []
    try:
        import winreg
    except ImportError:
        return libs
    for hive, key in ((winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
                      (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam")):
        try:
            with winreg.OpenKey(hive, key) as k:
                for name in ("SteamPath", "InstallPath"):
                    try:
                        steam = Path(winreg.QueryValueEx(k, name)[0])
                    except OSError:
                        continue
                    libs.append(steam)
                    vdf = steam / "steamapps" / "libraryfolders.vdf"
                    if vdf.exists():
                        text = vdf.read_text(encoding="utf-8", errors="replace")
                        libs += [Path(m.group(1).replace("\\\\", "\\")) for m in re.finditer(r'"path"\s+"([^"]+)"', text)]
        except OSError:
            continue
    return libs


def steam_games():
    """스팀 라이브러리에서 찾은 Dwarf Fortress 폴더들."""
    found = []
    for lib in steam_libraries():
        apps = lib / "steamapps"
        names = ["Dwarf Fortress"]
        manifest = apps / f"appmanifest_{STEAM_APP_ID}.acf"
        if manifest.exists():
            m = re.search(r'"installdir"\s+"([^"]+)"', manifest.read_text(encoding="utf-8", errors="replace"))
            if m:
                names.insert(0, m.group(1))
        for name in names:
            game = apps / "common" / name
            if (game / EXE_NAME).exists() and game not in found:
                found.append(game)
    return found


def find_game(explicit=None) -> Path:
    """게임 폴더. 직접 지정한 폴더가 틀리면 다른 폴더로 넘어가지 않고 멈춘다 (엉뚱한 게임에 넣지 않게)."""
    if explicit:
        game = Path(explicit).resolve()
        if (game / EXE_NAME).exists():
            return game
        raise PatchError(f"그 폴더에 {EXE_NAME} 가 없습니다: {explicit}")
    remembered = load_settings().get("game")
    cands = ([Path(remembered)] if remembered else []) + steam_games()
    for c in cands:
        if (c / EXE_NAME).exists():
            return c
    raise PatchError("Dwarf Fortress 설치 폴더를 찾지 못했습니다. [폴더 변경]으로 직접 골라 주세요.")


# ---------------------------------------------------------------- 검사

def exe_info(game: Path):
    data = (game / EXE_NAME).read_bytes()
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    ts = struct.unpack_from("<I", data, pe + 8)[0]
    version = "?"
    notes = game / "release notes.txt"
    if notes.exists():
        m = re.search(r"Release notes for ([0-9.]+)", notes.read_text(encoding="cp437", errors="replace"))
        if m:
            version = m.group(1)
    return data, ts, version


def pattern_report(exe_data: bytes, offsets_text: str):
    """offsets.txt 의 함수 패턴이 실행 파일에서 정확히 한 번씩 맞는지 본다. {이름: 일치 횟수}"""
    res = {}
    for name, pat in re.findall(r"\[PATTERN:([A-Z_]+):([^\]]+)\]", offsets_text):
        rx = b"".join(b"." if t == "??" else re.escape(bytes([int(t, 16)])) for t in pat.split())
        res[name] = sum(1 for _ in re.finditer(rx, exe_data, re.S))
    return res


def verified_builds(offsets_text: str):
    """직접 확인한 게임 빌드: [(버전 이름, 빌드 시각)]. offsets.txt 의 [VERIFIED:53.17 steam win64, PE timestamp 0x6AEA05A1]"""
    out = []
    for body in re.findall(r"\[VERIFIED:([^\]]+)\]", offsets_text):
        m = re.search(r"0x([0-9A-Fa-f]{1,8})\s*$", body.strip())
        if m:
            out.append((body.split()[0], int(m.group(1), 16)))
    return out


def game_processes():
    """떠 있는 Dwarf Fortress 들의 실행 파일 경로. 경로를 읽지 못한 것은 None (권한이 더 높은 프로세스)."""
    try:
        out = subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {EXE_NAME}", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, errors="replace", creationflags=NO_WINDOW).stdout
    except OSError:
        return []
    pids = []
    for line in out.splitlines():
        cols = [c.strip('"') for c in line.split('","')]
        if len(cols) > 1 and cols[0].strip('"').lower() == EXE_NAME.lower() and cols[1].isdigit():
            pids.append(int(cols[1]))
    paths = []
    try:
        import ctypes
        import ctypes.wintypes as wt
        k32 = ctypes.windll.kernel32
        k32.OpenProcess.restype = wt.HANDLE
        k32.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        k32.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ctypes.POINTER(wt.DWORD)]
        k32.CloseHandle.argtypes = [wt.HANDLE]
        for pid in pids:
            path = None
            handle = k32.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
            if handle:
                buf, size = ctypes.create_unicode_buffer(1024), wt.DWORD(1024)
                if k32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                    path = buf.value
                k32.CloseHandle(handle)
            paths.append(path)
    except (AttributeError, OSError):
        paths = [None] * len(pids)
    return paths


def is_game_running(game=None) -> bool:
    """그 게임 폴더의 게임이 떠 있는가. 다른 폴더의 사본이 떠 있는 것은 치지 않는다. 폴더를 안 주면 아무 사본이나."""
    procs = game_processes()
    if game is None:
        return bool(procs)
    want = os.path.normcase(str(Path(game).resolve()))
    return any(p is None or os.path.normcase(os.path.dirname(p)) == want for p in procs)


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def is_ours(dll: Path) -> bool:
    """그 DLL 이 한국어 패치의 것인가 (파일 속의 글자로 가린다. 설치 기록이 없어도 알 수 있다)."""
    try:
        return dll.is_file() and DLL_MARK in dll.read_bytes()
    except OSError:
        return False


def dfhack_present(game: Path) -> bool:
    return any((game / name).exists() for name in DFHACK_MARKS)


def read_state(game: Path):
    f = game / DATA_NAME / "installed.json"
    if f.exists():
        try:
            return json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            return None
    return None


def read_tokens(path: Path) -> dict:
    """[이름:값] 꼴의 파일을 읽는다 (대괄호 밖은 주석)."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return {}
    return {m.group(1): m.group(2) for m in re.finditer(r"\[([A-Z_]+):([^\]\[]*)\]", text)}


REAPPLY_HINT = "[한글패치 다시 적용]을 누르세요. 한글이 다시 켜집니다."

# ---------------------------------------------------------------- 스팀 창작마당으로 깐 게임
# 창작마당으로 받은 사람은 구독 폴더의 설치 파일(DFKR_Install.bat)이 게임 폴더에 작은 로더와 길잡이 파일만 놓는다.
# 번역과 본체는 구독 폴더에 있고 스팀이 갱신한다. 그 게임에서는 이 프로그램으로 넣지도, 깃헙에서 새 버전을 받지도 않는다
# (넣으면 스팀이 갱신하는 길이 끊긴다). 글꼴·이름 표기 같은 설정은 게임 폴더의 config.txt 라서 그대로 바꿀 수 있다.
GUIDE_NAME = "workshop.txt"              # 길잡이 파일 (dfkr-data 안): [PATH:구독 폴더] 줄
WORKSHOP_BAT = "DFKR_Install.bat"
WORKSHOP_HINT = f"구독 폴더의 설치 파일({WORKSHOP_BAT})을 다시 실행하세요. 한글이 다시 켜집니다."
WORKSHOP_BLOCK = ("이 게임에는 한국어 패치가 스팀 창작마당으로 설치되어 있습니다.\n"
                  "번역과 본체는 스팀이 새 버전으로 바꿔 주므로 이 프로그램으로는 넣지 않습니다.\n"
                  "글꼴과 이름 표기는 이 창에서 그대로 바꿀 수 있습니다.\n\n"
                  "이 프로그램의 방식으로 바꾸려면 [원래대로]를 누른 뒤 [한글패치 적용]을 누르세요.")


def workshop_info(game: Path):
    """길잡이 파일이 있으면 창작마당으로 깐 게임이다: {"dir": 구독 폴더(못 찾으면 None), "version": 그 폴더의 번호, "paths": 적힌 폴더들}.
    없으면 None (이 프로그램으로 깐 게임이거나 안 깐 게임)."""
    guide = Path(game) / DATA_NAME / GUIDE_NAME
    try:
        raw = guide.read_bytes()
    except OSError:
        return None
    try:
        text = raw[2:].decode("utf-16-le") if raw[:2] == b"\xff\xfe" else raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = ""
    paths = []
    for m in re.finditer(r"\[PATH:([^\]\[]*)\]", text):
        p = m.group(1).strip().strip('"').rstrip("\\/")
        if p:
            paths.append(Path(p) if os.path.isabs(p) else Path(game) / p)
    found = next((p for p in paths if p.is_dir()), None)
    version = ""
    if found:
        try:
            version = (found / "version.txt").read_text(encoding="utf-8-sig").strip()
        except OSError:
            pass
    return {"dir": found, "version": version, "paths": paths}


def hook_status(game: Path, running=False):
    """지난 실행에서 한글이 켜졌는지. 훅이 남긴 dfkr-data/status.txt: {"on": bool, "reason": 글, "advice": 할 일} 또는 None.
    게임이 켜지다가 바로 꺼지면 훅은 다음 실행부터 한글을 꺼 둔다. 패치를 다시 넣으면 그 표시가 지워져 다시 켜진다."""
    data = game / DATA_NAME
    tok = read_tokens(data / "status.txt")
    patch = tok.get("PATCH", "").strip()
    hint = WORKSHOP_HINT if (data / GUIDE_NAME).exists() else REAPPLY_HINT     # 창작마당으로 깐 게임은 설치 파일이 다시 켠다
    if (data / "starting.flag").exists() and not running:
        # 훅은 켜질 때 이 파일을 만들고 처음 몇 초가 무사히 지나면 지운다: 게임이 꺼져 있는데 남아 있으면 켜지다가 꺼진 것이다
        return {"on": False, "crashed": True, "reason": "게임이 켜지다가 바로 꺼졌습니다. 이대로 켜면 영어로 실행됩니다.",
                "advice": hint, "patch": patch}
    if "STATE" not in tok:
        return None
    on = tok["STATE"].strip().upper() == "ON"
    reason = tok.get("REASON", "").strip()
    advice = hint if not on and "시작 도중" in reason else ""      # 훅이 스스로 꺼 둔 때의 사유 글
    return {"on": on, "crashed": False, "reason": reason, "advice": advice, "patch": patch}


def status(game: Path) -> dict:
    payload = payload_dir()
    data, ts, version = exe_info(game)
    offsets = (payload / DATA_NAME / "offsets.txt").read_text(encoding="utf-8")
    report = pattern_report(data, offsets)
    builds = verified_builds(offsets)
    state = read_state(game)
    main, chained = game / DLL_NAME, game / DLL_NAME_CHAINED
    ours_main = is_ours(main)
    foreign = main.exists() and not ours_main
    dfhack = dfhack_present(game)
    running = is_game_running(game)
    problem = None
    workshop = workshop_info(game)
    if workshop:
        # 창작마당으로 깐 게임: 설치 기록(installed.json)이 없다. 번호는 구독 폴더의 것 (스팀이 바꾸면 따라 바뀐다)
        loader = chained if chained.exists() else main if ours_main else None
        try:
            when = datetime.datetime.fromtimestamp((game / DATA_NAME / GUIDE_NAME).stat().st_mtime).isoformat(timespec="seconds")
        except OSError:
            when = ""
        state = {"patch_version": workshop["version"] or "?", "installed_at": when, "game_version": version,
                 "exe_timestamp": f"0x{ts:08X}", "dll_name": loader.name if loader else "", "channel": "workshop"}
        if not workshop["dir"]:
            problem = f"창작마당 구독 폴더를 찾지 못했습니다 (구독을 취소했거나 스팀 라이브러리를 옮겼습니다). {WORKSHOP_HINT}"
        elif not loader:
            problem = f"게임 폴더에 한국어 패치의 로더가 없습니다. {WORKSHOP_HINT}"
    elif state:
        if chained.exists() and not main.exists():
            problem = "DFHack 이 빠져서 한국어 패치가 불리지 않습니다. [한글패치 다시 적용]을 눌러 주세요."
        elif not ours_main and not chained.exists():
            problem = ("DFHack 이 새로 깔리면서 한국어 패치의 파일이 바뀌었습니다. [한글패치 다시 적용]을 눌러 주세요." if dfhack
                       else "한국어 패치의 파일이 없어졌습니다. [한글패치 다시 적용]을 눌러 주세요.")
    return {
        "game": str(game),
        "game_version": version,
        "exe_timestamp": f"0x{ts:08X}",
        "patterns": report,
        "patterns_ok": bool(report) and all(n == 1 for name, n in report.items() if name != "TAB_FONT"),
        "supported": ts in [b for _, b in builds],
        "supported_names": sorted({n for n, _ in builds}),
        "installed": state,
        "available_version": patch_version(payload),
        "foreign_dfhooks": foreign,
        "dfhack": dfhack,
        "problem": problem,
        "hook": hook_status(game, running) if state else None,
        "record": (game / DATA_NAME / RECORD_FLAG).exists(),
        "running": running,
        "channel": "workshop" if workshop else "program" if state else None,
        "workshop_dir": str(workshop["dir"]) if workshop and workshop["dir"] else None,
    }


def why_incompatible(st: dict):
    """이 게임에 넣을 수 없으면 그 까닭 (플레이어에게 보여 줄 글). 넣을 수 있으면 None."""
    want = ", ".join(st["supported_names"]) or "?"
    if not st["supported"]:
        same = st["game_version"] in st["supported_names"]
        return (f"이 한국어 패치는 Dwarf Fortress {want} (스팀판)용입니다.\n"
                f"설치된 게임은 {st['game_version']} (빌드 {st['exe_timestamp']})"
                + ("입니다. 버전 표기는 같지만 실행 파일이 다릅니다 (스팀판이 아니거나, 게임이 새로 고쳐졌습니다).\n" if same else "이라 맞지 않습니다.\n")
                + "버전이 다른 게임에 넣으면 게임이 꺼질 수 있어서 넣지 않았습니다.\n"
                  "새 게임 버전용 패치가 올라오면 이 프로그램이 알려 줍니다.")
    if not st["patterns_ok"]:
        bad = ", ".join(f"{k}({v}곳)" for k, v in st["patterns"].items() if v != 1 and k != "TAB_FONT")
        return (f"게임 실행 파일에서 필요한 자리를 찾지 못했습니다 ({bad}).\n"
                "게임 파일이 손상됐을 수 있습니다. 스팀에서 [파일 무결성 확인]을 한 뒤 다시 해 보세요.")
    return None


# ---------------------------------------------------------------- 적용 / 복구

def config_version(blob: bytes) -> int:
    """설정 파일의 [CONFIG_VERSION:n]. 없으면 1."""
    m = re.search(rb"\[CONFIG_VERSION:(\d+)\]", blob)
    return int(m.group(1)) if m else 1


def install(game: Path, log=print):
    payload = payload_dir()
    if workshop_info(game):
        raise PatchError(WORKSHOP_BLOCK)
    st = status(game)
    if st["running"]:
        raise PatchError("게임이 실행 중입니다. 게임을 끄고 다시 눌러 주세요.")
    why = why_incompatible(st)
    if why:
        raise PatchError(why)
    main, chained = game / DLL_NAME, game / DLL_NAME_CHAINED
    if st["foreign_dfhooks"] and not st["dfhack"]:
        raise PatchError(
            "게임 폴더에 다른 프로그램의 dfhooks.dll 이 있습니다 (DFHack 은 아닙니다).\n"
            "한국어 패치와 같은 자리를 쓰기 때문에 함께 쓸 수 없습니다. 그 프로그램을 빼고 다시 해 보세요.")
    # DFHack 의 dfhooks.dll 은 게임 폴더의 dfhooks_*.dll 을 전부 읽어 들인다: 그때는 dfhooks_kr.dll 로만 넣어 같이 쓴다
    target = chained if st["foreign_dfhooks"] else main

    dst = game / DATA_NAME
    keep = {}
    for name in KEEP_ON_UPDATE:
        f = dst / name
        if f.exists():
            keep[name] = f.read_bytes()
    own_fonts = {}
    if (dst / "fonts").is_dir():
        bundled = {f.name.lower() for f in (payload / DATA_NAME / "fonts").iterdir()}
        for f in (dst / "fonts").iterdir():
            if f.is_file() and f.name.lower() not in bundled:
                own_fonts[f.name] = f.read_bytes()  # 직접 고른 글꼴
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(payload / DATA_NAME, dst)
    for name, blob in own_fonts.items():
        (dst / "fonts" / name).write_bytes(blob)
    for name, blob in keep.items():
        if name == "config.txt" and config_version(blob) < config_version((dst / name).read_bytes()):
            # 설정 파일의 판이 올라갔다 (글꼴 등 기본값이 바뀜). 새 기본 설정을 쓰고 옛 설정은 옆에 남겨 둔다.
            (dst / "config.old.txt").write_bytes(blob)
            log("설정 파일이 새 판으로 바뀌었습니다. 예전 설정은 dfkr-data/config.old.txt 에 남겨 두었습니다.")
            continue
        (dst / name).write_bytes(blob)
    # dfhooks_kr.dll 은 늘 놓는다. DFHack 이 없으면 게임은 dfhooks.dll 만 읽으므로 한 번만 불린다. 나중에 DFHack 이 깔려
    # dfhooks.dll 을 덮어써도 DFHack 의 연쇄 로더가 dfhooks_kr.dll 을 읽어 한글이 그대로 나온다 (0.13.3)
    shutil.copy2(payload / DLL_NAME, chained)
    if target == main:
        shutil.copy2(payload / DLL_NAME, main)

    state = {
        "patch_version": patch_version(payload),
        "installed_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "game_version": st["game_version"],
        "exe_timestamp": st["exe_timestamp"],
        "dll_name": target.name,
        "dll_sha256": sha256(target),
    }
    (dst / "installed.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    log(f"한글패치 {state['patch_version']} 적용 완료 (게임 {st['game_version']}).")
    if target == chained:
        log("DFHack 이 있어서 dfhooks_kr.dll 로 넣었습니다 (DFHack 과 같이 뜹니다).")
    snapshot_if_possible(game, log)
    return state


def update_dict(game: Path, log=print):
    """사전(번역 데이터)만 갈아 끼운다. 게임이 켜져 있어도 되고, 게임 안에서 Ctrl+F3 을 누르면 바로 반영된다."""
    payload = payload_dir()
    dst = game / DATA_NAME / "dict"
    if workshop_info(game):
        raise PatchError(WORKSHOP_BLOCK)
    if not (game / DATA_NAME).is_dir():
        raise PatchError("한글패치가 설치되어 있지 않습니다. 먼저 [한글패치 적용]을 하세요.")
    tmp = game / DATA_NAME / "dict.new"
    if tmp.exists():
        shutil.rmtree(tmp)
    shutil.copytree(payload / DATA_NAME / "dict", tmp)
    if dst.exists():
        shutil.rmtree(dst)
    tmp.rename(dst)
    n = len(list(dst.glob("*.tsv")))
    log(f"사전 {n}개 파일을 갈아 끼웠습니다. 게임이 켜져 있으면 Ctrl+F3 을 누르세요.")


def uninstall(game: Path, log=print):
    if is_game_running(game):
        raise PatchError("게임이 실행 중입니다. 게임을 끄고 다시 눌러 주세요.")
    main, chained, dst = game / DLL_NAME, game / DLL_NAME_CHAINED, game / DATA_NAME
    if not dst.exists() and not chained.exists() and not is_ours(main):
        raise PatchError("한글패치가 설치되어 있지 않습니다.")
    chained.unlink(missing_ok=True)
    if is_ours(main):                      # 남의 dfhooks.dll (DFHack) 은 건드리지 않는다
        main.unlink()
    if dst.exists():
        collected = dst / "untranslated.txt"
        if (HERE / "tools").is_dir() and collected.exists() and collected.stat().st_size:
            out = HERE / "reports"       # 작업장에서만: 모아 둔 미번역 문장을 남긴다
            out.mkdir(exist_ok=True)
            stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
            shutil.copy2(collected, out / f"untranslated_{stamp}.txt")
            log(f"모아 둔 미번역 문장을 reports/untranslated_{stamp}.txt 로 옮겼습니다.")
        shutil.rmtree(dst)
    log("원래대로 되돌렸습니다 (게임은 영어로 실행됩니다).")


# ---------------------------------------------------------------- 글꼴

def read_config(game: Path) -> dict:
    """게임 폴더의 dfkr-data/config.txt 에서 [이름:값] 을 읽는다."""
    f = game / DATA_NAME / "config.txt"
    if not f.exists():
        raise PatchError("한글패치가 설치되어 있지 않습니다. 먼저 [한글패치 적용]을 하세요.")
    return read_tokens(f)


def write_config(game: Path, values: dict):
    """config.txt 의 [이름:값] 만 바꾼다 (주석과 다른 설정은 그대로). 없는 항목은 끝에 덧붙인다."""
    f = game / DATA_NAME / "config.txt"
    text = f.read_text(encoding="utf-8", errors="replace")
    for key, val in values.items():
        new = f"[{key}:{val}]"
        text, n = re.subn(rf"\[{key}:[^\]\[]*\]", lambda m: new, text, count=1)
        if not n:
            text = text.rstrip("\n") + f"\n\n{new}\n"
    f.write_text(text, encoding="utf-8", newline="\n")


def font_has_hangul(path: Path) -> bool:
    """글꼴 파일에 한글(가, 한, 힣)이 들어 있는지 cmap 표를 읽어 확인한다. 읽지 못하는 형식이면 True (막지 않는다)."""
    try:
        d = path.read_bytes()
        base = struct.unpack_from(">I", d, 12)[0] if d[:4] == b"ttcf" else 0  # 글꼴 묶음(TTC)이면 첫 글꼴
        ntab = struct.unpack_from(">H", d, base + 4)[0]
        cmap = None
        for i in range(ntab):
            tag, _, off, _ = struct.unpack_from(">4sIII", d, base + 12 + i * 16)
            if tag == b"cmap":
                cmap = off
        if cmap is None:
            return True
        want = (0xAC00, 0xD55C, 0xD7A3)
        nsub = struct.unpack_from(">H", d, cmap + 2)[0]
        for i in range(nsub):
            _, _, off = struct.unpack_from(">HHI", d, cmap + 4 + i * 8)
            sub = cmap + off
            fmt = struct.unpack_from(">H", d, sub)[0]
            if fmt == 4:
                segx2 = struct.unpack_from(">H", d, sub + 6)[0]
                ends = struct.unpack_from(f">{segx2 // 2}H", d, sub + 14)
                starts = struct.unpack_from(f">{segx2 // 2}H", d, sub + 16 + segx2)
                if all(any(a <= c <= b for a, b in zip(starts, ends)) for c in want):
                    return True
            elif fmt == 12:
                ngroups = struct.unpack_from(">I", d, sub + 12)[0]
                groups = [struct.unpack_from(">III", d, sub + 16 + g * 12)[:2] for g in range(ngroups)]
                if all(any(a <= c <= b for a, b in groups) for c in want):
                    return True
        return False
    except (OSError, struct.error, IndexError):
        return True


def current_font(game: Path) -> dict:
    cfg = read_config(game)
    name = cfg.get("FONT", "Galmuri9.ttf")
    preset = next((k for k, v in FONT_PRESETS.items() if v[1].lower() == name.lower()), None)
    return {"file": name, "preset": preset, "grid": cfg.get("FONT_GRID", "0"), "bold": cfg.get("FONT_BOLD", "0"),
            "scale": cfg.get("FONT_SCALE", "1.0"),
            "label": FONT_PRESETS[preset][0] if preset else f"직접 고른 글꼴 ({name})"}


def set_font(game: Path, choice: str, log=print, bold=None, scale=None):
    """한글 글꼴을 바꾼다. choice 는 FONT_PRESETS 의 이름이거나 글꼴 파일 경로. 게임을 다시 켜면 적용된다."""
    fonts = game / DATA_NAME / "fonts"
    # 창작마당으로 깐 게임: 같이 들어 있는 글꼴은 구독 폴더에 있고, 직접 고른 글꼴만 게임 폴더의 fonts 에 둔다 (훅이 거기를 먼저 본다)
    workshop = workshop_info(game)
    if workshop:
        fonts.mkdir(parents=True, exist_ok=True)
    if not fonts.is_dir():
        raise PatchError("한글패치가 설치되어 있지 않습니다. 먼저 [한글패치 적용]을 하세요.")
    key = choice.strip().lower()
    if key in FONT_PRESETS:
        label, name, grid, default_bold = FONT_PRESETS[key]
        bundled = workshop and workshop["dir"] and (workshop["dir"] / "fonts" / name).exists()
        if not (fonts / name).exists() and not bundled:
            raise PatchError(f"글꼴 파일이 없습니다: {name}. " + (WORKSHOP_HINT if workshop else "[한글패치 다시 적용]을 눌러 주세요."))
    else:
        src = Path(choice.strip().strip('"'))
        if not src.is_file() or src.suffix.lower() not in FONT_EXTS:
            raise PatchError(f"글꼴 파일(TTF/OTF/TTC)이 아닙니다: {choice}\n같이 들어 있는 글꼴: {', '.join(FONT_PRESETS)}")
        if not font_has_hangul(src):
            raise PatchError(f"이 글꼴에는 한글이 들어 있지 않습니다: {src.name}")
        name, grid, default_bold, label = src.name, 0, 0, f"직접 고른 글꼴 ({src.name})"
        if "]" in name or "[" in name or ":" in name:
            raise PatchError("글꼴 파일 이름에 [ ] : 는 쓸 수 없습니다. 이름을 바꿔서 다시 골라 주세요.")
        if src.resolve() != (fonts / name).resolve():
            shutil.copy2(src, fonts / name)
    values = {"FONT": name, "FONT_GRID": grid, "FONT_BOLD": default_bold if bold is None else max(0, min(3, int(bold)))}
    if scale is not None:
        values["FONT_SCALE"] = scale
    write_config(game, values)
    log(f"한글 글꼴: {label}, 굵기 {values['FONT_BOLD']}. 게임을 다시 켜면 적용됩니다.")


# ---------------------------------------------------------------- 이름 표기

NAME_OPTIONS = {
    "translit": ("NAME_TRANSLIT", "원어 이름을 한글로 적기 (Dumat Sheriksibrek → 두마트 셰리크시브레크)"),
    "meaning": ("NAME_MEANING", "뜻풀이 이름을 한국어로 옮기기 (The Tin Lances → 주석창)"),
}


def name_options(game: Path) -> dict:
    cfg = read_config(game)
    return {k: cfg.get(key, "YES").strip().upper() != "NO" for k, (key, _) in NAME_OPTIONS.items()}


def set_name_options(game: Path, log=print, **values):
    """이름 표기를 켜고 끈다 (translit=True/False, meaning=True/False). 게임을 다시 켜면 적용된다."""
    new = {}
    for k, v in values.items():
        if k not in NAME_OPTIONS:
            raise PatchError(f"모르는 이름 설정: {k} (쓸 수 있는 것: {', '.join(NAME_OPTIONS)})")
        new[NAME_OPTIONS[k][0]] = "YES" if v else "NO"
    write_config(game, new)
    now = name_options(game)
    log("이름 표기: " + ", ".join(f"{NAME_OPTIONS[k][1].split(' (')[0]} {'켬' if on else '끔'}" for k, on in now.items())
        + ". 게임을 다시 켜면 적용됩니다.")


# ---------------------------------------------------------------- 영어 원문 붙이기

ORIGINAL_MODES = {
    "always": ("ALWAYS", "항상"),
    "search": ("SEARCH", "검색할 때만"),
    "no": ("NO", "끔"),
}


def original_options(game: Path) -> dict:
    """이름 옆에 영어 원문을 붙이는 설정: {"mode": always|search|no, "people": 사람 이름에도 붙이는지}."""
    cfg = read_config(game)
    value = cfg.get("SHOW_ORIGINAL", "ALWAYS").strip().upper()
    mode = next((k for k, (v, _) in ORIGINAL_MODES.items() if v == value), "always")
    return {"mode": mode, "people": cfg.get("SHOW_ORIGINAL_PEOPLE", "NO").strip().upper() == "YES"}


def set_original_options(game: Path, log=print, mode=None, people=None):
    """이름 옆의 영어 원문 설정을 바꾼다 (mode: always/search/no, people: True/False). 게임을 다시 켜면 적용된다."""
    new = {}
    if mode is not None:
        if mode not in ORIGINAL_MODES:
            raise PatchError(f"모르는 값: {mode} (쓸 수 있는 것: {', '.join(ORIGINAL_MODES)})")
        new["SHOW_ORIGINAL"] = ORIGINAL_MODES[mode][0]
    if people is not None:
        new["SHOW_ORIGINAL_PEOPLE"] = "YES" if people else "NO"
    write_config(game, new)
    now = original_options(game)
    log(f"이름 옆 영어 원문: {ORIGINAL_MODES[now['mode']][1]}, 사람 이름에도 {'붙임' if now['people'] else '안 붙임'}. 게임을 다시 켜면 적용됩니다.")


def snapshot_if_possible(game: Path, log=print):
    """작업장 안에서 실행 중이면, 이 게임 버전의 영어 원문 스냅샷을 남긴다 (업데이트 비교용 백업)."""
    tool = HERE / "tools" / "snapshot.py"
    if not tool.exists():
        return
    try:
        res = subprocess.run([sys.executable, "-X", "utf8", str(tool), str(game)], capture_output=True, text=True,
                             encoding="utf-8", errors="replace", timeout=300, creationflags=NO_WINDOW)
        line = (res.stdout.strip().splitlines() or [""])[0]
        if line:
            log(f"원문 백업: {line}")
    except (OSError, subprocess.SubprocessError) as e:
        log(f"원문 백업은 건너뜀: {e}")


# ---------------------------------------------------------------- 화면 기록과 제보 묶음

def set_record(game: Path, on: bool, log=print):
    """화면 기록을 켜고 끈다. 게임이 켜져 있어도 바로 먹는다 (dfkr-data/record.flag 가 있는 동안 켜진다).
    켜 두면 게임 화면에 찍힌 글이 dfkr-data/record/ 에 쌓인다: 문제를 다시 보여 줄 때만 켠다."""
    data = game / DATA_NAME
    if not data.is_dir():
        raise PatchError("한글패치가 설치되어 있지 않습니다. 먼저 [한글패치 적용]을 하세요.")
    flag = data / RECORD_FLAG
    if on:
        flag.write_text("제보용 기록\n", encoding="utf-8")
        log("화면 기록을 켰습니다. 게임에서 문제가 나는 화면을 다시 열어 본 뒤, [제보 묶음 만들기]를 눌러 주세요.")
    else:
        flag.unlink(missing_ok=True)
        log("화면 기록을 껐습니다.")


def desktop_dir() -> Path:
    try:
        import ctypes
        buf = ctypes.create_unicode_buffer(520)
        if ctypes.windll.shell32.SHGetFolderPathW(None, 0x0010, None, 0, buf) == 0 and buf.value:  # CSIDL_DESKTOPDIRECTORY
            return Path(buf.value)
    except (AttributeError, OSError):
        pass
    return Path.home() / "Desktop"


def private_strings(game: Path):
    """제보 묶음에서 지울 것: [(찾을 글, 바꿀 글, 대소문자 가림)]. 게임 폴더와 사용자 폴더의 경로, 계정 이름, PC 이름."""
    pairs = []
    for path, label in ((str(game), "<게임 폴더>"), (str(Path.home()), "<사용자 폴더>")):
        for form in {path, path.replace("\\", "/"), path.replace("\\", "\\\\")}:
            pairs.append((re.escape(form), label, re.I))
    for name, label in ((os.environ.get("USERNAME"), "<사용자>"), (os.environ.get("COMPUTERNAME"), "<PC>")):
        if name and len(name) >= 3:
            pairs.append((rf"(?<![0-9A-Za-z_]){re.escape(name)}(?![0-9A-Za-z_])", label, 0))
    return sorted(pairs, key=lambda p: -len(p[0]))


def scrub(text: str, pairs) -> str:
    for pattern, label, flags in pairs:
        text = re.sub(pattern, lambda _m: label, text, flags=flags)
    return text


def make_report(game: Path, out_dir=None, log=print) -> Path:
    """문제를 알릴 때 보낼 것을 zip 하나로 묶는다: 화면 기록, 번역 안 된 글 목록, 기록 파일, 패치·게임 버전.
    묶기 전에 PC 경로와 계정 이름을 지우고, 묶은 뒤 다시 열어 남아 있지 않은지 확인한다."""
    data = game / DATA_NAME
    if not data.is_dir():
        raise PatchError("한글패치가 설치되어 있지 않습니다. 먼저 [한글패치 적용]을 하세요.")
    st = status(game)
    inst = st["installed"] or {}
    hook = st["hook"]
    try:
        font = current_font(game)
        font_line = f"{font['file']}, 굵기 {font['bold']}, 배율 {font['scale']}"
    except (PatchError, OSError):
        font_line = "?"
    files = [(name, data / name) for name in REPORT_FILES if (data / name).is_file()]
    if (data / "record").is_dir():
        files += [(f"record/{f.name}", f) for f in sorted((data / "record").iterdir()) if f.is_file()]
    info = "\n".join([
        f"{APP_TITLE} 제보 묶음",
        f"만든 때: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M')}",
        f"프로그램 버전: {st['available_version']}",
        f"게임에 넣은 패치: {inst.get('patch_version', '없음')} ({inst.get('dll_name', DLL_NAME)})",
        f"게임: {st['game_version']} (빌드 {st['exe_timestamp']}), 지원하는 빌드: {'예' if st['supported'] else '아니요'}",
        f"DFHack: {'있음' if st['dfhack'] else '없음'}",
        "지난 실행: " + ("기록 없음" if not hook else "한글 켜짐" if hook["on"] else f"한글 꺼짐 ({hook['reason']})"),        f"화면 기록: {'켜짐' if st['record'] else '꺼짐'}",
        f"한글 글꼴: {font_line}",
        f"윈도우: {sys.getwindowsversion().major}.{sys.getwindowsversion().minor}.{sys.getwindowsversion().build}" if hasattr(sys, "getwindowsversion") else "",
        "",
        "들어 있는 파일: " + ", ".join(name for name, _ in files),
        "",
        "어느 화면에서 무엇이 이상했는지 이슈에 같이 적어 주세요. 화면 캡처가 있으면 가장 좋습니다.",
        f"이슈: {ISSUES_URL}",
        "",
    ])
    pairs = private_strings(game)
    out_dir = Path(out_dir) if out_dir else desktop_dir()
    out_dir.mkdir(parents=True, exist_ok=True)
    target = out_dir / f"DFKR_report_{datetime.datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        z.writestr("report_info.txt", scrub(info, pairs))
        for name, path in files:
            z.writestr(name, scrub(path.read_text(encoding="utf-8", errors="replace"), pairs))
    # 다시 열어 확인: 지우려던 글이 하나라도 남았으면 묶음을 버린다
    left = []
    with zipfile.ZipFile(target) as z:
        for name in z.namelist():
            body = z.read(name).decode("utf-8", errors="replace")
            left += [name for pattern, _label, flags in pairs if re.search(pattern, body, flags)]
    if left:
        target.unlink()
        raise PatchError(f"제보 묶음에서 PC 경로를 다 지우지 못해 만들지 않았습니다 ({', '.join(sorted(set(left)))}).")
    log(f"제보 묶음을 만들었습니다: {target.name} ({target.stat().st_size // 1024} KB, 파일 {len(files) + 1}개)")
    log("PC 경로와 계정 이름이 들어 있지 않은 것을 확인했습니다. 이 파일을 이슈에 올려 주세요.")
    return target


# ---------------------------------------------------------------- 새 버전 받기

def parse_version(ver: str):
    parts = []
    for p in re.sub(r"^[a-zA-Z-]*v?", "", ver.strip()).split("."):
        try:
            parts.append(int(p))
        except ValueError:
            parts.append(0)
    return tuple(parts) if parts else (0,)


def ssl_context():
    """윈도우 인증서 저장소 + 같이 넣은 루트 인증서 묶음(cacert.pem). 저장소만 쓰면 GitHub 루트가 아직 없는 PC 에서
    인증서 오류가 나고, 묶음만 쓰면 백신이나 회사 프록시가 HTTPS 를 검사하는 PC 에서 오류가 난다."""
    import ssl
    ctx = ssl.create_default_context()
    bundle = HERE / CA_BUNDLE
    if bundle.is_file():
        try:
            ctx.load_verify_locations(cafile=str(bundle))
        except (OSError, ssl.SSLError):
            pass
    return ctx


def http_open(url, timeout=30, accept=None):
    headers = {"User-Agent": f"DFKr-Patcher/{program_version()}"}
    if accept:
        headers["Accept"] = accept
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout, context=ssl_context())


def host_allowed(url: str) -> bool:
    """내려받기가 끝난 주소가 우리가 받기로 한 곳인가 (GitHub 은 릴리즈 파일을 githubusercontent.com 으로 넘겨준다)."""
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    base = (urllib.parse.urlsplit(DOWNLOAD_BASE).hostname or "").lower()
    if base == "github.com":
        return host == "github.com" or host.endswith(".githubusercontent.com")
    return host == base


def download(url: str, dest: Path, progress=None, limit=MAX_DOWNLOAD):
    if not url or not url.startswith(DOWNLOAD_PREFIX):
        raise PatchError("이 저장소의 릴리즈가 아닌 주소라서 받지 않았습니다.")
    with http_open(url, timeout=120) as resp:
        if not host_allowed(resp.geturl()):
            raise PatchError("내려받는 주소가 GitHub 이 아닌 곳으로 넘어가서 받지 않았습니다.")
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with open(dest, "wb") as f:
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                done += len(chunk)
                if done > limit:
                    raise PatchError("받는 파일이 너무 큽니다.")
                f.write(chunk)
                if progress:
                    progress(done, total)


def plain(line: str) -> str:
    """릴리즈 노트 한 줄에서 굵게 표시와 백틱을 뗀다 (안내 창과 기록 칸에 보여 줄 때)."""
    return line.replace("**", "").replace("`", "")


def release_highlights(body: str, heading="## "):
    """릴리즈 노트에서 첫 꼭지(주요 작업)의 줄만 추린다. 꼭지가 없으면 본문 전체. 링크는 글만 남긴다."""
    lines = (body or "").replace("\r\n", "\n").split("\n")
    starts = [i for i, line in enumerate(lines) if line.startswith(heading)]
    if starts:
        end = starts[1] if len(starts) > 1 else len(lines)
        lines = lines[starts[0] + 1:end]
    else:
        lines = [line for line in lines if not line.startswith("#")]
    lines = [re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", line.rstrip()) for line in lines if line.strip() != "---"]
    while lines and not lines[0]:
        lines.pop(0)
    while lines and not lines[-1]:
        lines.pop()
    return lines


def markdown_spans(line: str):
    """릴리즈 노트 한 줄 → (머리 기호, [(글, 굵게 여부)]). 백틱은 뗀다."""
    text = line.strip().replace("`", "")
    lead = ""
    if text.startswith(("- ", "* ")):
        lead, text = "• ", text[2:]
    return lead, [(part, i % 2 == 1) for i, part in enumerate(text.split("**")) if part]


def fetch_releases():
    """저장소의 릴리즈들 (새 것부터). 릴리즈가 없으면 빈 목록.
    [{"version", "tag", "title", "date", "page", "name", "url", "digest", "sums", "installer", "notes"}]"""
    try:
        with http_open(f"{RELEASES_API}?per_page=100", accept="application/vnd.github+json") as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return []
        raise
    out = []
    for rel in data if isinstance(data, list) else []:
        tag = rel.get("tag_name") or ""
        m = re.fullmatch(r"v(\d+(?:\.\d+){1,3})", tag)
        if not m or rel.get("draft") or rel.get("prerelease"):
            continue
        version = m.group(1)
        assets = {a.get("name"): a for a in rel.get("assets", [])}
        name = f"{PACKAGE_NAME}-update-v{version}.zip"
        asset = assets.get(name) or {}
        out.append({
            "version": version, "tag": tag, "title": rel.get("name") or tag, "date": (rel.get("published_at") or "")[:10],
            "page": f"{REPO_URL}/releases/tag/{tag}",
            "name": name, "url": asset.get("browser_download_url"), "digest": asset.get("digest") or "",
            "sums": (assets.get("SHA256SUMS") or {}).get("browser_download_url"),
            "installer": f"{PACKAGE_NAME}-v{version}.msi",
            "notes": release_highlights(rel.get("body")),
        })
    out.sort(key=lambda r: parse_version(r["version"]), reverse=True)
    return out


def bundled_notes():
    """프로그램에 같이 들어 있는 릴리즈 노트(RELEASE_NOTES.md)의 꼭지들. 인터넷이 안 될 때도 보여 줄 수 있다."""
    for path in (HERE / "RELEASE_NOTES.md", HERE.parent / "RELEASE_NOTES.md"):
        if path.is_file():
            break
    else:
        return []
    out = []
    for part in re.split(r"^## v", path.read_text(encoding="utf-8", errors="replace"), flags=re.M)[1:]:
        head, _, body = part.partition("\n")
        m = re.match(r"(\d+(?:\.\d+){1,3})\s*(?:\((\d{4}-\d{2}-\d{2})\))?\s*(.*)", head)
        if m:
            out.append({"version": m.group(1), "tag": f"v{m.group(1)}", "title": f"v{m.group(1)} {m.group(3)}".strip(),
                        "date": m.group(2) or "", "notes": release_highlights(body, heading="### ")})
    return out


def note_entries(releases, applied=None):
    """오른쪽 칸에 보여 줄 릴리즈들 (새 것부터). 저장소의 릴리즈에, 같이 들어 있는 노트에만 있는 것을 더한다.
    applied 보다 새 것에는 new 표시 (아직 게임에 들어가지 않은 내용)."""
    seen, out = set(), []
    for rel in list(releases or []) + bundled_notes():
        if rel["version"] not in seen:
            seen.add(rel["version"])
            out.append({"version": rel["version"], "title": rel["title"], "date": rel.get("date", ""), "lines": rel["notes"],
                        "new": bool(applied) and parse_version(rel["version"]) > parse_version(applied)})
    out.sort(key=lambda r: parse_version(r["version"]), reverse=True)
    return out


def find_update(releases, current=None):
    """릴리즈들 가운데 지금 프로그램보다 새 것 (가장 새 것 하나). 없으면 None."""
    current = current or program_version()
    newer = [r for r in releases or [] if parse_version(r["version"]) > parse_version(current)]
    return newer[0] if newer else None


def check_update(current=None):
    """지금 프로그램보다 새 릴리즈가 있으면 그 정보, 없으면 None."""
    return find_update(fetch_releases(), current)


def extract_checked(archive: Path, destination: Path):
    """압축을 풀기 전에 경로를 전부 살핀다: 폴더 밖으로 나가는 경로, 윈도우 특수 이름, 링크, 겹치는 이름을 막는다."""
    destination = destination.resolve()
    reserved = {"CON", "PRN", "AUX", "NUL", *[f"COM{i}" for i in range(1, 10)], *[f"LPT{i}" for i in range(1, 10)]}
    with zipfile.ZipFile(archive) as z:
        infos = z.infolist()
        if len(infos) > 2000 or sum(i.file_size for i in infos) > 512 * 1024 * 1024:
            raise PatchError("업데이트 파일의 크기나 항목 수가 한도를 넘습니다.")
        seen = set()
        for info in infos:
            name = info.orig_filename
            parts = Path(name).parts
            if (name != info.filename or "\x00" in name or "\\" in name or ":" in name or name.startswith("/")
                    or ".." in parts or any(p.endswith((" ", ".")) for p in parts)
                    or any(p.split(".")[0].upper() in reserved for p in parts)
                    or (info.external_attr >> 16) & 0o170000 == 0o120000):
                raise PatchError(f"업데이트 파일에 허용되지 않는 경로가 있습니다: {name!r}")
            (destination / name).resolve().relative_to(destination)
            if name.casefold() in seen:
                raise PatchError(f"업데이트 파일에 겹치는 경로가 있습니다: {name}")
            seen.add(name.casefold())
        z.extractall(destination)


def apply_update(update: dict, progress=None) -> Path:
    """새 버전(app 폴더 묶음)을 받아, 올린 것과 같은지 확인하고, 뜨는지 확인한 뒤 app 폴더를 통째로 바꾼다.
    실패하면 지금 버전 그대로 둔다. 돌려주는 것은 다시 띄울 patcher.py."""
    app = package_dir()
    if not app:
        raise PatchError("설치 파일로 깐 프로그램이 아니어서 스스로 업데이트할 수 없습니다.")
    if not update.get("url") or not update.get("sums"):
        raise PatchError("릴리즈에 업데이트 파일이 없습니다. 릴리즈 쪽에서 새 설치 파일을 받아 주세요.")
    stage = app.parent / f".update-{os.getpid()}-{datetime.datetime.now().strftime('%H%M%S%f')}"
    old = app.with_name("app.old")
    try:
        stage.mkdir()
    except OSError as e:
        raise PatchError(f"프로그램 폴더에 쓸 수 없습니다: {e}")
    try:
        sums_file = stage / "SHA256SUMS"
        download(update["sums"], sums_file, limit=1 << 20)
        expected = ""
        for line in sums_file.read_text(encoding="utf-8", errors="replace").splitlines():
            parts = line.split()
            if len(parts) >= 2 and parts[-1].lstrip("*") == update["name"]:
                expected = parts[0].lower()
        if not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise PatchError("릴리즈의 SHA256SUMS 에 업데이트 파일의 해시가 없습니다.")
        digest = update.get("digest") or ""
        if digest.startswith("sha256:") and digest[7:].lower() != expected:
            raise PatchError("릴리즈에 적힌 해시 둘이 서로 다릅니다. 받지 않았습니다.")
        archive = stage / update["name"]
        download(update["url"], archive, progress)
        if sha256(archive).lower() != expected:
            raise PatchError("받은 파일이 올라온 것과 다릅니다 (해시 불일치). 넣지 않았습니다. 잠시 뒤 다시 해 보세요.")
        new = stage / "app"
        new.mkdir()
        extract_checked(archive, new)
        try:
            info = json.loads((new / PACKAGE_INFO).read_text(encoding="utf-8"))
            now = json.loads((app / PACKAGE_INFO).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            raise PatchError("업데이트 파일의 꼴이 맞지 않습니다.")
        if info.get("version") != update["version"] or not (new / DLL_NAME).is_file() or not (new / DATA_NAME).is_dir():
            raise PatchError("업데이트 파일의 버전이 릴리즈와 다릅니다.")
        if info.get("python") != now.get("python"):
            raise PatchError(f"이번 버전은 실행 환경도 바뀌어서 설치 파일을 새로 받아야 합니다.\n{update['page']}")
        # 새 프로그램이 지금 실행 환경에서 뜨는지 확인한 뒤에 바꾼다
        check = subprocess.run([str(Path(sys.executable).with_name("python.exe")), "-B", str(new / "patcher.py"), "--self-check"],
                               stdin=subprocess.DEVNULL, capture_output=True, text=True, encoding="utf-8", errors="replace",
                               timeout=60, creationflags=NO_WINDOW)
        if check.returncode != 0 or check.stdout.strip() != update["version"]:
            raise PatchError("새 프로그램이 실행 확인을 통과하지 못했습니다.")
        shutil.rmtree(old, ignore_errors=True)
        try:
            os.replace(app, old)
        except OSError as e:
            raise PatchError(f"프로그램 폴더를 바꿀 수 없습니다: {e}")
        try:
            os.replace(new, app)
        except OSError as e:
            os.replace(old, app)
            raise PatchError(f"프로그램 폴더를 바꿀 수 없습니다: {e}")
    finally:
        shutil.rmtree(stage, ignore_errors=True)
    shutil.rmtree(old, ignore_errors=True)
    return app / "patcher.py"


def restart(script: Path, *extra):
    subprocess.Popen([sys.executable, "-B", str(script), *extra], close_fds=True, cwd=str(script.parent.parent))


# ---------------------------------------------------------------- 글로 보기

def describe(st: dict) -> str:
    lines = [f"프로그램 버전: {st['available_version']}", f"게임 폴더: {st['game']}",
             f"게임 버전: {st['game_version']} (빌드 {st['exe_timestamp']})"]
    inst = st["installed"]
    workshop = st.get("channel") == "workshop"
    if inst:
        if workshop:
            lines.append(f"한글패치: 스팀 창작마당으로 설치됨 (v{inst.get('patch_version')}, 로더 {inst.get('dll_name') or '없음'})")
            lines.append(f"  구독 폴더: {st.get('workshop_dir') or '찾지 못함'}")
            lines.append("  번역과 본체는 스팀이 갱신합니다. 이 프로그램의 [한글패치 적용]과 업데이트는 꺼 두었습니다.")
        else:
            lines.append(f"한글패치: 적용됨 (v{inst.get('patch_version')}, {inst.get('installed_at', '')[:16].replace('T', ' ')}, {inst.get('dll_name', DLL_NAME)})")
        if not workshop and parse_version(inst.get("patch_version", "0")) < parse_version(st["available_version"]):
            lines.append(f"  새 버전 v{st['available_version']} 을 넣을 수 있습니다 (install).")
        if inst.get("exe_timestamp") != st["exe_timestamp"]:
            lines.append("  ※ 패치를 적용한 뒤에 게임이 업데이트되었습니다.")
        try:
            f = current_font(Path(st["game"]))
            lines.append(f"한글 글꼴: {f['label']}, 굵기 {f['bold']}")
        except (PatchError, OSError):
            pass
        if st["hook"]:
            lines.append("지난 실행: " + ("한글이 켜진 채로 실행됨" if st["hook"]["on"] else st["hook"]["reason"] if st["hook"]["crashed"]
                                      else f"한글이 꺼진 채로 실행됨 - {st['hook']['reason']}"))
            if st["hook"]["advice"]:
                lines.append("  " + st["hook"]["advice"])
        lines.append(f"화면 기록: {'켜짐' if st['record'] else '꺼짐'}")
    else:
        lines.append("한글패치: 적용 안 됨")
    why = why_incompatible(st)
    lines.append("호환성: 이 게임 버전에 넣을 수 있습니다." if not why else "호환성: 맞지 않음\n  " + why.replace("\n", "\n  "))
    if st["dfhack"]:
        lines.append("DFHack 이 있습니다 (같이 쓸 수 있습니다).")
    elif st["foreign_dfhooks"]:
        lines.append("주의: 다른 프로그램의 dfhooks.dll 이 있습니다.")
    if st["problem"]:
        lines.append("주의: " + st["problem"])
    if st["running"]:
        lines.append("게임이 실행 중입니다.")
    return "\n".join(lines)


# ---------------------------------------------------------------- 창

def run_gui(game_arg=None, updated=False):
    """패처 창. 게임 화면을 닮게 꾸민다: 어두운 판, 금색 겹테두리, 도트 글꼴(갈무리), 초록 확인 버튼."""
    import ctypes
    import threading
    import tkinter as tk
    import tkinter.font as tkfont
    import webbrowser
    from tkinter import filedialog, messagebox

    BG, PANEL, INSET = "#121212", "#1b1b1b", "#0c0c0c"
    GOLD, GOLD2 = "#e8a11c", "#7a4c0c"
    TEXT, DIM, OFF = "#f2f2f2", "#9a9a9a", "#555555"
    YELLOW, GREEN, RED = "#ffd84a", "#5fd35f", "#ff5a4a"
    BTN, BTN_HOVER, SEL = "#262626", "#3a321c", "#3b2f12"
    GO, GO_HOVER, GO_EDGE, GO_EDGE_HOVER = "#2e7d32", "#3c9a40", "#8fe08f", "#d6ffd6"

    # 작업 표시줄에 파이썬 아이콘 대신 패처 아이콘이 뜨게 하고, 도트 글꼴을 이 프로그램만 쓰도록 올린다
    try:
        try:
            # 화면 배율을 직접 다룬다. 안 그러면 윈도우가 창을 통째로 늘려 도트 글꼴이 흐려진다.
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except (AttributeError, OSError):
            pass
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("dfkr.patcher")
        loaded = set()
        for base in (HERE / "fonts", HERE / "dist" / DATA_NAME / "fonts", HERE / DATA_NAME / "fonts"):
            for name in ("Galmuri9.ttf", "Galmuri11.ttf", "Galmuri11-Bold.ttf"):
                src = base / name
                if name in loaded or not src.exists():
                    continue
                if package_dir():
                    # app 폴더의 파일을 바로 올리면 윈도우가 그 파일을 잡고 있어서 스스로 업데이트할 때 app 폴더를 못 바꾼다.
                    # 사본을 옆 폴더에 두고 그쪽을 올린다.
                    copy = HERE.parent / "ui" / name
                    if not (copy.is_file() and copy.stat().st_size == src.stat().st_size):
                        copy.parent.mkdir(parents=True, exist_ok=True)
                        shutil.copyfile(src, copy)
                    src = copy
                if ctypes.windll.gdi32.AddFontResourceExW(str(src), 0x10, 0):  # FR_PRIVATE
                    loaded.add(name)
    except (AttributeError, OSError):
        pass

    root = tk.Tk()
    root.title(APP_TITLE)
    root.configure(bg=BG)
    root.resizable(False, False)
    scale = root.winfo_fpixels("1i") / 96.0
    # 도트 글꼴과 테두리의 배율 (도트가 뭉개지지 않게 정수배만). 두 배로 그리면 화면에 안 들어가는 낮은 화면에서는 한 배로 둔다.
    k = 2 if scale >= 1.4 and root.winfo_screenheight() >= 1350 else 1
    families = sorted(set(tkfont.families(root)))

    def family(prefix):
        return next((f for f in families if f.startswith(prefix)), None)

    fam9, fam11 = family("Galmuri9"), family("Galmuri11")

    def make_font(grid, fam, mult=1, bold=False):
        if fam:  # 도트 글꼴: 글자 틀(grid)의 정수배
            return tkfont.Font(root=root, family=fam, size=-int(grid * k * mult), weight="bold" if bold else "normal")
        return tkfont.Font(root=root, family="맑은 고딕", size=-int(round((grid + 3) * scale * mult)), weight="bold" if bold else "normal")

    # 배율 100% 에서는 12픽셀 글꼴(갈무리11), 150% 넘으면 10픽셀 글꼴(갈무리9)을 두 배로: 둘 다 읽기 좋은 크기다
    F_BODY = make_font(10, fam9) if k == 2 else make_font(12, fam11 or fam9)
    F_HEAD = make_font(12, fam11, bold=True)
    F_TITLE = make_font(12, fam11, mult=2 if k == 1 else 1.5, bold=True)
    u = k  # 테두리 한 겹의 굵기

    icon_img = None
    try:
        ico = next((p for p in (HERE / "icon.ico", HERE / "assets" / "icon.ico") if p.exists()), None)
        if ico:
            root.iconbitmap(default=str(ico))
        png_name = "icon@2x.png" if k == 2 else "icon.png"
        png = next((p for p in (HERE / png_name, HERE / "assets" / png_name) if p.exists()), None)
        if png:
            icon_img = tk.PhotoImage(file=str(png))
    except tk.TclError:
        pass

    state = {"game": None, "update": None, "busy": False}
    version = program_version()

    # ------------------------------------------------------------ 부품

    def panel(parent, title=None):
        """금색 겹테두리를 두른 어두운 판 (게임의 창 테두리)."""
        outer = tk.Frame(parent, bg=GOLD)
        mid = tk.Frame(outer, bg=GOLD2)
        mid.pack(fill="both", expand=True, padx=u, pady=u)
        inner = tk.Frame(mid, bg=PANEL)
        inner.pack(fill="both", expand=True, padx=u, pady=u)
        if title:
            tk.Label(inner, text=title, font=F_HEAD, bg=PANEL, fg=YELLOW, anchor="w").pack(fill="x", padx=7 * k, pady=(5 * k, 1 * k))
        return outer, inner

    class Btn:
        def __init__(self, parent, text, command, go=False, font=None, padx=9, pady=4):
            self.command, self.go, self.enabled, self.selected = command, go, True, False
            self.frame = tk.Frame(parent, bg=GOLD2)
            self.label = tk.Label(self.frame, text=text, font=font or F_BODY, padx=padx * k, pady=pady * k)
            self.label.pack(fill="both", expand=True, padx=u, pady=u)
            self.label.bind("<Enter>", lambda _e: self.paint(True))
            self.label.bind("<Leave>", lambda _e: self.paint(False))
            self.label.bind("<ButtonRelease-1>", lambda _e: self.enabled and self.command())
            self.paint(False)

        def paint(self, hover=False):
            if not self.enabled:
                bg, fg, edge = PANEL, OFF, "#333333"
            elif self.go:
                bg, fg, edge = (GO_HOVER, "#ffffff", GO_EDGE_HOVER) if hover else (GO, "#ffffff", GO_EDGE)
            elif self.selected:
                bg, fg, edge = SEL, YELLOW, GOLD
            else:
                bg, fg, edge = (BTN_HOVER, TEXT, GOLD) if hover else (BTN, TEXT, GOLD2)
            self.frame.configure(bg=edge)
            self.label.configure(bg=bg, fg=fg, cursor="hand2" if self.enabled else "arrow")

        def set(self, enabled=None, selected=None, text=None):
            if enabled is not None:
                self.enabled = enabled
            if selected is not None:
                self.selected = selected
            if text is not None:
                self.label.configure(text=text)
            self.paint(False)

    class Check:
        """게임의 선택 칸처럼 생긴 켜고 끄는 줄: [X] 글  (보기)"""

        def __init__(self, parent, text, hint, command):
            self.command, self.value, self.enabled = command, True, True
            self.row = tk.Frame(parent, bg=PANEL)
            edge = tk.Frame(self.row, bg=GOLD2)
            edge.pack(side="left")
            self.box = tk.Label(edge, text="X", width=2, font=F_BODY, bg=INSET, fg=YELLOW)
            self.box.pack(padx=u, pady=u)
            self.text = tk.Label(self.row, text=text, font=F_BODY, bg=PANEL, fg=TEXT)
            self.text.pack(side="left", padx=(6 * k, 0))
            self.hint = tk.Label(self.row, text=hint, font=F_BODY, bg=PANEL, fg=DIM)
            self.hint.pack(side="left", padx=(8 * k, 0))
            for w in (self.box, self.text, self.hint):
                w.bind("<ButtonRelease-1>", self.toggle)
            self.paint()

        def paint(self):
            self.box.configure(text="X" if self.value else " ", fg=YELLOW if self.enabled else OFF)
            self.text.configure(fg=TEXT if self.enabled else OFF)
            for w in (self.box, self.text, self.hint):
                w.configure(cursor="hand2" if self.enabled else "arrow")

        def toggle(self, _e=None):
            if self.enabled:
                self.value = not self.value
                self.paint()
                self.command()

        def set(self, value=None, enabled=None):
            if value is not None:
                self.value = value
            if enabled is not None:
                self.enabled = enabled
            self.paint()

    def guarded_cmd(fn):
        def run():
            if state["busy"]:
                return
            try:
                fn()
            except PatchError as e:
                log(f"실패: {e}")
                messagebox.showwarning("한국어 패치", str(e))
            except Exception as e:  # 권한 문제 등
                log(f"오류: {e}")
                messagebox.showerror("한국어 패치", f"{type(e).__name__}: {e}")
            refresh()
        return run

    # ------------------------------------------------------------ 머리

    pad = 10 * k
    # 왼쪽: 패치 조작 / 오른쪽: 업데이트 내용 (릴리즈 노트)
    left = tk.Frame(root, bg=BG)
    left.pack(side="left", fill="y")
    head = tk.Frame(left, bg=BG)
    head.pack(fill="x", padx=pad, pady=(pad, 6 * k))
    if icon_img is not None:
        tk.Label(head, image=icon_img, bg=BG).pack(side="left", padx=(0, 8 * k))
    titles = tk.Frame(head, bg=BG)
    titles.pack(side="left")
    tk.Label(titles, text=APP_TITLE, font=F_TITLE, bg=BG, fg=TEXT, anchor="w").pack(anchor="w")
    tk.Label(titles, text="☼ 대지를 강타하라! ☼", font=F_BODY, bg=BG, fg=GOLD, anchor="w").pack(anchor="w", pady=(2 * k, 0))

    # ------------------------------------------------------------ 상태

    box_o, box = panel(left)
    box_o.pack(fill="x", padx=pad, pady=(0, 6 * k))
    grid = tk.Frame(box, bg=PANEL)
    grid.pack(fill="x", padx=7 * k, pady=5 * k)
    rows = {}
    for i, (key, label) in enumerate((("folder", "게임 폴더"), ("game", "게임 버전"), ("patch", "한글패치"), ("compat", "호환성"),
                                      ("hook", "지난 실행"), ("program", "프로그램"))):
        tk.Label(grid, text=label, font=F_BODY, bg=PANEL, fg=DIM, anchor="nw").grid(row=i, column=0, sticky="nw", padx=(0, 10 * k), pady=1)
        rows[key] = tk.Label(grid, text="", font=F_BODY, bg=PANEL, fg=TEXT, anchor="w", justify="left", wraplength=int(430 * max(scale, k)))
        rows[key].grid(row=i, column=1, sticky="w", pady=1)
    rows["program"].configure(text=f"v{version}  (새 버전을 확인하는 중…)", fg=DIM)

    # ------------------------------------------------------------ 한글 글꼴

    font_o, font_box = panel(left, "한글 글꼴")
    font_o.pack(fill="x", padx=pad, pady=(0, 6 * k))
    frow = tk.Frame(font_box, bg=PANEL)
    frow.pack(fill="x", padx=7 * k, pady=(2 * k, 3 * k))
    font_btns = {}

    def apply_font(choice):
        set_font(state["game"], choice, log, bold=1 if bold_chk.value else 0)

    for key, (label, _f, _g, _b) in FONT_PRESETS.items():
        b = Btn(frow, label.split(" (")[0], guarded_cmd(lambda key=key: apply_font(key)))
        b.frame.pack(side="left", padx=(0, 4 * k))
        font_btns[key] = b

    def pick_file():
        f = filedialog.askopenfilename(title="한글 글꼴 파일 고르기", filetypes=[("글꼴 파일", "*.ttf *.otf *.ttc")])
        if f:
            guarded_cmd(lambda: apply_font(f))()

    file_btn = Btn(frow, "글꼴 파일 고르기...", pick_file)
    file_btn.frame.pack(side="left")
    frow2 = tk.Frame(font_box, bg=PANEL)
    frow2.pack(fill="x", padx=7 * k, pady=(0, 6 * k))

    def toggle_bold():
        def run():
            write_config(state["game"], {"FONT_BOLD": 1 if bold_chk.value else 0})
            log(f"글꼴 굵기 {1 if bold_chk.value else 0}. 게임을 다시 켜면 적용됩니다.")
        guarded_cmd(run)()

    bold_chk = Check(frow2, "한 픽셀 굵게", "", toggle_bold)
    bold_chk.row.pack(side="left")
    font_now = tk.Label(frow2, text="", font=F_BODY, bg=PANEL, fg=DIM)
    font_now.pack(side="right")

    # ------------------------------------------------------------ 이름 표기

    name_o, name_box = panel(left, "이름 표기")
    name_o.pack(fill="x", padx=pad, pady=(0, 6 * k))
    name_chk = {}

    def toggle_names():
        guarded_cmd(lambda: set_name_options(state["game"], log, **{n: c.value for n, c in name_chk.items()}))()

    for n, (text, hint) in (("translit", ("원어 이름을 한글로", "Dumat Sheriksibrek → 두마트 셰리크시브레크")),
                            ("meaning", ("뜻풀이 이름을 한국어로", "The Tin Lances → 주석창"))):
        c = Check(name_box, text, hint, toggle_names)
        c.row.pack(anchor="w", padx=7 * k, pady=(2 * k, 0))
        name_chk[n] = c

    # 이름 옆에 영어 원문 붙이기: 항상 / 검색할 때만 / 끔, 그리고 사람 이름에도 붙일지
    orow = tk.Frame(name_box, bg=PANEL)
    orow.pack(fill="x", padx=7 * k, pady=(5 * k, 0))
    tk.Label(orow, text="이름 옆에 영어 원문", font=F_BODY, bg=PANEL, fg=TEXT).pack(side="left", padx=(0, 8 * k))
    orig_btns = {}
    for mode, (_v, label) in ORIGINAL_MODES.items():
        b = Btn(orow, label, guarded_cmd(lambda mode=mode: set_original_options(state["game"], log, mode=mode)))
        b.frame.pack(side="left", padx=(0, 4 * k))
        orig_btns[mode] = b
    tk.Label(orow, text="호랑이 (tiger)", font=F_BODY, bg=PANEL, fg=DIM).pack(side="left", padx=(4 * k, 0))

    def toggle_people():
        guarded_cmd(lambda: set_original_options(state["game"], log, people=people_chk.value))()

    people_chk = Check(name_box, "사람·무리 이름에도", "두마트 (Dumat)", toggle_people)
    people_chk.row.pack(anchor="w", padx=7 * k, pady=(3 * k, 0))
    tk.Label(name_box, text="글꼴과 이름 표기는 게임을 다시 켜면 적용됩니다.", font=F_BODY, bg=PANEL, fg=DIM, anchor="w").pack(
        fill="x", padx=7 * k, pady=(4 * k, 5 * k))

    # ------------------------------------------------------------ 문제 제보

    rep_o, rep_box = panel(left, "문제 제보")
    rep_o.pack(fill="x", padx=pad, pady=(0, 6 * k))
    rrow = tk.Frame(rep_box, bg=PANEL)
    rrow.pack(fill="x", padx=7 * k, pady=(2 * k, 3 * k))

    def toggle_record():
        guarded_cmd(lambda: set_record(state["game"], record_chk.value, log))()

    record_chk = Check(rrow, "화면 기록", "", toggle_record)
    record_chk.row.pack(side="left")

    def do_report():
        target = make_report(state["game"], log=log)
        try:
            subprocess.Popen(["explorer.exe", "/select,", str(target)])
        except OSError:
            pass

    Btn(rrow, "이슈 쓰러 가기", lambda: webbrowser.open(ISSUES_URL)).frame.pack(side="right")
    report_btn = Btn(rrow, "제보 묶음 만들기", guarded_cmd(do_report))
    report_btn.frame.pack(side="right", padx=(0, 4 * k))
    tk.Label(rep_box, text="화면 기록은 문제가 난 화면을 다시 보여 줄 때만 켜세요. 묶음은 바탕 화면에 만들어집니다.", font=F_BODY, bg=PANEL, fg=DIM,
             anchor="w").pack(fill="x", padx=7 * k, pady=(0, 5 * k))

    # ------------------------------------------------------------ 기록

    log_o, log_box = panel(left)
    log_o.pack(fill="x", padx=pad, pady=(0, 6 * k))
    text = tk.Text(log_box, height=4, width=10, font=F_BODY, bg=INSET, fg="#bdbdbd", relief="flat", bd=0, padx=6 * k, pady=4 * k,
                   state="disabled", wrap="word", cursor="arrow", highlightthickness=0)
    text.pack(fill="x", padx=u, pady=u)
    text.tag_configure("bad", foreground=RED)

    def log(msg):
        text.configure(state="normal")
        text.insert("end", msg + "\n", "bad" if msg.startswith(("실패", "오류")) else ())
        text.see("end")
        text.configure(state="disabled")
        root.update_idletasks()

    # ------------------------------------------------------------ 업데이트 내용 (오른쪽)

    notes_o = tk.Frame(root, bg=GOLD)
    notes_o.pack(side="left", fill="both", expand=True, padx=(0, pad), pady=pad)
    notes_mid = tk.Frame(notes_o, bg=GOLD2)
    notes_mid.pack(fill="both", expand=True, padx=u, pady=u)
    notes_box = tk.Frame(notes_mid, bg=PANEL, width=int(380 * k))
    notes_box.pack(fill="both", expand=True, padx=u, pady=u)
    notes_box.pack_propagate(False)
    notes_title = tk.Label(notes_box, text="업데이트 내용", font=F_HEAD, bg=PANEL, fg=YELLOW, anchor="w")
    notes_title.pack(fill="x", padx=7 * k, pady=(5 * k, 2 * k))
    # 위: 릴리즈 목록 / 아래: 고른 릴리즈의 주요 작업
    notes_list = tk.Listbox(notes_box, height=4, font=F_BODY, bg=INSET, fg=TEXT, relief="flat", bd=0, highlightthickness=u,
                            highlightbackground=GOLD2, highlightcolor=GOLD2, activestyle="none", exportselection=False,
                            selectbackground=SEL, selectforeground=YELLOW)
    notes_list.pack(fill="x", padx=7 * k, pady=(0, 4 * k))
    notes_text = tk.Text(notes_box, width=10, font=F_BODY, bg=INSET, fg="#d8d8d8", relief="flat", bd=0, padx=6 * k, pady=5 * k,
                         state="disabled", wrap="word", cursor="arrow", highlightthickness=u, highlightbackground=GOLD2,
                         highlightcolor=GOLD2, spacing1=1 * k, spacing3=1 * k)
    notes_text.pack(fill="both", expand=True, padx=7 * k, pady=(0, 7 * k))
    notes_text.tag_configure("version", foreground=YELLOW, font=F_HEAD)
    notes_text.tag_configure("date", foreground=DIM)
    notes_text.tag_configure("bold", foreground="#ffffff", font=make_font(10, fam9, bold=True) if k == 2 else make_font(12, fam11 or fam9, bold=True))
    notes_text.tag_configure("item", lmargin1=2 * k, lmargin2=12 * k)
    notes_text.tag_configure("subitem", lmargin1=12 * k, lmargin2=22 * k, foreground="#b0b0b0")
    notes_text.tag_configure("muted", foreground=DIM)
    notes = {"entries": [], "releases": None}      # releases: None = 아직 확인 중, [] = 받지 못했거나 없음

    def show_release(message=None):
        notes_text.configure(state="normal")
        notes_text.delete("1.0", "end")
        picked = notes_list.curselection()
        if message or not picked:
            notes_text.insert("end", message or "", "muted")
            notes_text.configure(state="disabled")
            return
        entry = notes["entries"][picked[0]]
        notes_text.insert("end", entry["title"] + "\n", "version")
        notes_text.insert("end", entry["date"] + ("  ·  " if entry["date"] and entry["new"] else "")
                          + ("아직 게임에 넣지 않은 버전입니다" if entry["new"] else "") + "\n", "date")
        for line in entry["lines"]:
            if not line.strip():
                continue
            lead, spans = markdown_spans(line)
            tag = ("subitem" if line.startswith(" ") else "item") if lead else None
            notes_text.insert("end", lead, tag)
            for part, bold in spans:
                notes_text.insert("end", part, tuple(t for t in (tag, "bold" if bold else None) if t))
            notes_text.insert("end", "\n", tag)
        notes_text.configure(state="disabled")

    def render_notes():
        """오른쪽 칸을 다시 그린다: 릴리즈 목록(새 것부터)과 고른 릴리즈의 내용. 게임에 넣은 것보다 새 버전에는 ● 표시."""
        applied = None
        try:
            if state["game"] is not None:
                applied = (read_state(state["game"]) or {}).get("patch_version")
        except OSError:
            pass
        selected = notes["entries"][notes_list.curselection()[0]]["version"] if notes["entries"] and notes_list.curselection() else None
        notes["entries"] = note_entries(notes["releases"], applied)
        notes_list.delete(0, "end")
        if not notes["entries"]:
            notes_title.configure(text="업데이트 내용")
            show_release("릴리즈 노트를 불러오는 중…" if notes["releases"] is None else "릴리즈 노트가 없습니다.")
            return
        top = notes["entries"][0]
        notes_title.configure(text=f"새로 들어올 내용 (v{applied} → v{top['version']})" if top["new"] else "업데이트 내용")
        for i, entry in enumerate(notes["entries"]):
            notes_list.insert("end", ("● " if entry["new"] else "   ") + entry["title"])
            if entry["new"]:
                notes_list.itemconfigure(i, foreground=YELLOW)
        index = next((i for i, entry in enumerate(notes["entries"]) if entry["version"] == selected), 0)
        notes_list.selection_set(index)
        notes_list.see(index)
        show_release()

    notes_list.bind("<<ListboxSelect>>", lambda _e: show_release())

    # ------------------------------------------------------------ 동작

    def refresh():
        installed = False
        try:
            if state["game"] is None:
                state["game"] = find_game(game_arg)
            st = status(state["game"])
        except PatchError as e:
            rows["folder"].configure(text=str(e), fg=RED)
            for key in ("game", "patch", "compat", "hook"):
                rows[key].configure(text="")
            apply_btn.set(enabled=False)
            undo_btn.set(enabled=False)
        else:
            inst = st["installed"]
            installed = bool(inst)
            why = why_incompatible(st)
            workshop = st.get("channel") == "workshop"
            was_workshop = state.get("channel") == "workshop"
            state["channel"] = st.get("channel")
            rows["folder"].configure(text=str(st["game"]), fg=TEXT)
            rows["game"].configure(text=f"{st['game_version']} (빌드 {st['exe_timestamp']})")
            newer = False
            if workshop:
                # 창작마당으로 깐 게임: 파일은 스팀이 댄다. 이 창은 설정(글꼴, 이름 표기)과 [원래대로]에만 쓴다
                rows["patch"].configure(text=f"스팀 창작마당으로 설치됨  v{inst.get('patch_version')}\n번역과 본체는 스팀이 새 버전으로 바꿔 줍니다.", fg=GREEN)
                rows["program"].configure(text=f"v{version}  (창작마당으로 설치한 게임이라 프로그램 업데이트는 꺼 두었습니다)", fg=DIM)
                update_btn.frame.grid_remove()
            elif inst:
                when = inst.get("installed_at", "")[:16].replace("T", " ")
                stale = inst.get("exe_timestamp") != st["exe_timestamp"]
                newer = parse_version(inst.get("patch_version", "0")) < parse_version(st["available_version"])
                note = ""
                if stale:
                    note = "\n패치를 적용한 뒤에 게임이 업데이트되었습니다."
                elif newer:
                    note = f"\n새 버전 v{st['available_version']} 을 넣을 수 있습니다."
                rows["patch"].configure(text=f"적용됨  v{inst.get('patch_version')}  ({when})" + note, fg=YELLOW if stale or newer else GREEN)
            else:
                rows["patch"].configure(text="적용 안 됨", fg=DIM)
            notes = [why or ("이 게임 버전에서 쓸 수 있습니다." if workshop else "이 게임 버전에 넣을 수 있습니다.")]
            if workshop:
                notes.append("창작마당으로 설치한 게임입니다. [한글패치 적용]과 깃헙 업데이트는 쓰지 않습니다\n(이 프로그램으로 넣으면 스팀의 자동 갱신이 끊깁니다). 글꼴과 이름 표기는 그대로 바꿀 수 있습니다.")
            if st["dfhack"] and workshop:
                notes.append("DFHack 이 있습니다 (같이 쓸 수 있습니다).")
            elif st["dfhack"]:
                notes.append("DFHack 이 있습니다. 같이 쓸 수 있게 넣습니다.")
            elif st["foreign_dfhooks"]:
                notes.append("다른 프로그램의 dfhooks.dll 이 있습니다.")
            if st["problem"]:
                notes.append(st["problem"])
            if st["running"]:
                notes.append("게임이 실행 중입니다.")
            bad = bool(why) or (st["foreign_dfhooks"] and not st["dfhack"])
            rows["compat"].configure(text="\n".join(notes), fg=RED if bad else YELLOW if st["problem"] or st["running"] else GREEN)
            hook = st["hook"]
            if not hook:
                rows["hook"].configure(text="아직 기록이 없습니다." if installed else "", fg=DIM)
            elif hook["on"]:
                rows["hook"].configure(text="한글이 켜진 채로 실행됐습니다.", fg=GREEN)
            else:
                head = hook["reason"] if hook["crashed"] else f"한글이 꺼진 채로 실행됐습니다.\n사유: {hook['reason']}"
                advice = hook["advice"].replace("[한글패치 다시 적용]을", "[새 버전 넣기]를") if newer else hook["advice"]    # 초록 단추의 글과 맞춘다
                rows["hook"].configure(text=head + (f"\n{advice}" if advice else ""), fg=RED)
            if workshop:
                apply_btn.set(enabled=False, text="창작마당으로 설치됨")
            else:
                apply_btn.set(enabled=not why, text=f"새 버전 넣기 (v{st['available_version']})" if newer else "한글패치 다시 적용" if inst else "한글패치 적용")
            undo_btn.set(enabled=installed)
            record_chk.set(value=st["record"])
            if was_workshop and not workshop:
                # [원래대로]로 창작마당 설치를 뺐다: 꺼 두었던 새 버전 확인을 다시 한다
                rows["program"].configure(text=f"v{version}", fg=TEXT)
                root.after(100, check_update_async)
        # 글꼴, 이름 표기, 제보 칸
        for b in list(font_btns.values()) + [file_btn, report_btn]:
            b.set(enabled=installed, selected=False)
        bold_chk.set(enabled=installed)
        record_chk.set(enabled=installed)
        if not installed:
            record_chk.set(value=False)
        for c in name_chk.values():
            c.set(enabled=installed)
        for b in orig_btns.values():
            b.set(enabled=installed, selected=False)
        people_chk.set(enabled=installed)
        font_now.configure(text="")
        if installed:
            try:
                f = current_font(state["game"])
                for key, b in font_btns.items():
                    b.set(selected=(key == f["preset"]))
                file_btn.set(selected=f["preset"] is None)
                bold_chk.set(value=f["bold"] not in ("0", ""))
                font_now.configure(text="" if f["preset"] else f["file"])
                for n, on in name_options(state["game"]).items():
                    name_chk[n].set(value=on)
                orig = original_options(state["game"])
                for mode, b in orig_btns.items():
                    b.set(selected=(mode == orig["mode"]))
                people_chk.set(value=orig["people"], enabled=orig["mode"] != "no")
            except (PatchError, OSError):
                pass
        render_notes()

    def do_install():
        install(state["game"], log)
        save_settings(game=str(state["game"]))
        messagebox.showinfo("한국어 패치", "적용했습니다. 게임을 실행하면 한국어로 나옵니다.\n게임 안에서 Ctrl+F2 로 한글/영어를 바꿀 수 있습니다.")

    def do_uninstall():
        uninstall(state["game"], log)

    def choose():
        d = filedialog.askdirectory(title="Dwarf Fortress 폴더 선택")
        if d:
            if not (Path(d) / EXE_NAME).exists():
                messagebox.showwarning("한국어 패치", "그 폴더에 Dwarf Fortress.exe 가 없습니다.")
                return
            state["game"] = Path(d)
            save_settings(game=str(state["game"]))
            refresh()

    def launch():
        game = state["game"]
        if game is None:
            return
        try:
            if any(game.resolve() == g.resolve() for g in steam_games()):
                os.startfile(f"steam://rungameid/{STEAM_APP_ID}")
            else:   # 스팀 라이브러리 밖의 폴더: 실행 파일을 바로 띄운다
                subprocess.Popen([str(game / EXE_NAME)], cwd=str(game))
        except OSError as e:
            log(f"오류: 게임을 띄우지 못했습니다 ({e})")

    # ---- 새 버전 확인 (뒤에서). 받는 것은 물어본 뒤에만.

    def show_update(releases, error=None):
        notes["releases"] = releases or []
        render_notes()
        if state.get("channel") == "workshop":
            # 창작마당으로 깐 게임: 업데이트 내용은 보여 주되 받으라고 권하지 않는다 (받아도 게임에는 못 넣는다)
            state["update"] = None
            update_btn.frame.grid_remove()
            return
        if error:
            rows["program"].configure(text=f"v{version}  (새 버전 확인을 못 했습니다: 인터넷 연결을 확인해 주세요)", fg=DIM)
            return
        update = find_update(releases, version)
        state["update"] = update
        if not update:
            rows["program"].configure(text=f"v{version}  최신 버전입니다.", fg=GREEN)
            update_btn.frame.grid_remove()
            return
        rows["program"].configure(text=f"v{version}  →  새 버전 v{update['version']} 이 나왔습니다.", fg=YELLOW)
        update_btn.frame.grid()
        log(f"새 버전 v{update['version']} 이 나왔습니다 (지금 v{version}). 바뀐 것은 오른쪽 칸에 있습니다.")
        root.after(300, offer_update)

    def check_update_async():
        def work():
            try:
                releases = fetch_releases()
            except Exception as e:
                root.after(0, show_update, None, str(e))
                return
            root.after(0, show_update, releases)
        threading.Thread(target=work, daemon=True).start()

    def offer_update():
        update = state["update"]
        if not update or state["busy"] or state.get("channel") == "workshop":
            return
        if not package_dir():
            log(f"  받는 곳: {update['page']}")
            return
        summary = "\n".join(plain(line) for line in update["notes"][:10])
        if not messagebox.askyesno("한국어 패치 업데이트",
                                   f"새 버전 v{update['version']} 이 나왔습니다. (지금 v{version})\n\n{summary}\n\n지금 받아서 프로그램을 바꾸고 다시 시작할까요?"):
            return
        state["busy"] = True
        rows["program"].configure(text=f"v{version}  →  v{update['version']} 받는 중…", fg=YELLOW)

        def progress(done, total):
            if total:
                root.after(0, lambda: rows["program"].configure(text=f"v{version}  →  v{update['version']} 받는 중… {done * 100 // total}%"))

        def work():
            try:
                script = apply_update(update, progress)
            except Exception as e:
                root.after(0, failed, str(e))
                return
            root.after(0, done, script)

        def done(script):
            restart(script, "--updated", *(["--game", str(state["game"])] if state["game"] else []))
            root.destroy()

        def failed(reason):
            state["busy"] = False
            rows["program"].configure(text=f"v{version}  업데이트를 못 했습니다. 지금 버전은 그대로 쓸 수 있습니다.", fg=RED)
            log(f"실패: {reason}")
            messagebox.showerror("한국어 패치 업데이트", f"{reason}\n\n직접 받기:\n{update['page']}")

        threading.Thread(target=work, daemon=True).start()

    update_btn = Btn(grid, "업데이트", offer_update, go=True, pady=1)
    update_btn.frame.grid(row=5, column=2, sticky="e", padx=(8 * k, 0))
    update_btn.frame.grid_remove()

    bar = tk.Frame(left, bg=BG)
    bar.pack(fill="x", padx=pad, pady=(0, pad))
    apply_btn = Btn(bar, "한글패치 적용", guarded_cmd(do_install), go=True, font=F_HEAD, padx=14, pady=7)
    apply_btn.frame.pack(side="left", fill="x", expand=True, padx=(0, 5 * k))
    undo_btn = Btn(bar, "원래대로", guarded_cmd(do_uninstall), pady=8)
    undo_btn.frame.pack(side="left", padx=(0, 4 * k))
    Btn(bar, "게임 실행", launch, pady=8).frame.pack(side="left", padx=(0, 4 * k))
    Btn(bar, "폴더 변경", choose, pady=8).frame.pack(side="left")

    refresh()
    log("☼ 준비되었습니다. 바꾼 내용은 여기에 적힙니다.")
    if updated:
        log(f"프로그램을 v{version} 으로 바꿨습니다.")

        def offer_install():
            try:
                st = status(state["game"])
            except (PatchError, TypeError, OSError):
                return
            inst = st["installed"]
            if st.get("channel") == "workshop":
                return      # 창작마당으로 깐 게임에는 넣지 않는다
            if inst and parse_version(inst.get("patch_version", "0")) < parse_version(version) and not st["running"] and not why_incompatible(st):
                if messagebox.askyesno("한국어 패치", f"프로그램을 v{version} 으로 바꿨습니다.\n게임에도 새 버전을 넣을까요? (지금 게임에는 v{inst.get('patch_version')})"):
                    guarded_cmd(do_install)()
        root.after(400, offer_install)
    check_update_async()
    root.mainloop()


def main():
    args = sys.argv[1:]
    if "--self-check" in args:      # 업데이트가 새 프로그램이 뜨는지 볼 때 쓴다
        print(program_version())
        return 0
    game_arg = None
    if "--game" in args:
        i = args.index("--game")
        game_arg = args[i + 1]
        del args[i:i + 2]
    updated = "--updated" in args
    opts = {}
    for name in ("--bold", "--scale"):
        if name in args:
            i = args.index(name)
            opts[name] = args[i + 1]
            del args[i:i + 2]
    args = [a for a in args if not a.startswith("--")]
    if not args:
        return run_gui(game_arg, updated)
    try:
        if args[0] == "update":
            try:
                if workshop_info(find_game(game_arg)):
                    print("실패: " + WORKSHOP_BLOCK.split("\n\n")[0])
                    return 1
            except PatchError:
                pass        # 게임을 못 찾았으면 프로그램만 바꾼다 (예전과 같다)
            update = check_update()
            if not update:
                print(f"최신 버전입니다 (v{program_version()}).")
                return 0
            print(f"새 버전 v{update['version']} 이 나왔습니다 (지금 v{program_version()}).")
            for line in update["notes"]:
                print("  " + plain(line))
            if not package_dir():
                print(f"받는 곳: {update['page']}")
                return 0
            apply_update(update)
            print(f"프로그램을 v{update['version']} 으로 바꿨습니다. 게임에 넣으려면: install")
            return 0
        game = find_game(game_arg)
        if args[0] == "install":
            install(game)
            save_settings(game=str(game))
        elif args[0] == "uninstall":
            uninstall(game)
        elif args[0] == "dict":
            update_dict(game)
        elif args[0] == "status":
            print(describe(status(game)))
        elif args[0] == "record":
            if len(args) > 1 and args[1].lower() in ("on", "off"):
                set_record(game, args[1].lower() == "on")
            else:
                print(f"화면 기록: {'켜짐' if (game / DATA_NAME / RECORD_FLAG).exists() else '꺼짐'}  (바꾸기: record on|off)")
        elif args[0] == "report":
            print(make_report(game, args[1] if len(args) > 1 else None))
        elif args[0] == "names":
            if len(args) > 1:
                vals = {}
                for a in args[1:]:
                    k, _, v = a.partition("=")
                    vals[k.strip().lower()] = v.strip().lower() in ("yes", "y", "on", "1", "true", "켬")
                set_name_options(game, **vals)
            else:
                for k, on in name_options(game).items():
                    print(f"{k}: {'켬' if on else '끔'}  - {NAME_OPTIONS[k][1]}")
        elif args[0] == "original":
            if len(args) > 1:
                mode, people = None, None
                for a in args[1:]:
                    k, eq, v = a.partition("=")
                    if eq and k.strip().lower() == "people":
                        people = v.strip().lower() in ("yes", "y", "on", "1", "true", "켬")
                    else:
                        mode = a.strip().lower()
                set_original_options(game, mode=mode, people=people)
            else:
                now = original_options(game)
                print(f"이름 옆 영어 원문: {now['mode']} ({ORIGINAL_MODES[now['mode']][1]}), 사람 이름에도: {'yes' if now['people'] else 'no'}")
                print("바꾸기: patcher.py original always|search|no [people=yes|no]")
        elif args[0] == "font":
            if len(args) > 1:
                set_font(game, args[1], bold=opts.get("--bold"), scale=opts.get("--scale"))
            else:
                f = current_font(game)
                print(f"한글 글꼴: {f['label']} (파일 {f['file']}, 글자 틀 {f['grid']}, 굵기 {f['bold']}, 배율 {f['scale']})")
                print("같이 들어 있는 글꼴: " + ", ".join(f"{k} = {v[0]}" for k, v in FONT_PRESETS.items()))
        else:
            print(__doc__)
            return 2
    except PatchError as e:
        print(f"실패: {e}")
        return 1
    except (urllib.error.URLError, OSError) as e:
        print(f"실패: {e}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
