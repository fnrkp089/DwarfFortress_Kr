## 🚀 설치와 사용법
1. 게임을 끕니다. 하던 요새가 있으면 저장 폴더를 먼저 백업해 두세요 (`%APPDATA%\Bay 12 Games\Dwarf Fortress\save`)
2. `DwarfFortress_KR-v{version}.msi`를 실행합니다 (설치가 끝나면 프로그램이 열립니다)
3. 게임 폴더와 게임 버전을 확인하고 **한글패치 적용**을 누릅니다
4. 스팀에서 게임을 실행합니다

- 이미 설치해 쓰는 분은 프로그램을 열면 새 버전 안내가 뜹니다. **예**를 누르면 받아서 바꾼 뒤, 게임에도 넣을지 묻습니다
- 게임 안에서 **Ctrl+F2**: 한글 / 영어 바꾸기
- 패치 빼기: 프로그램의 **원래대로**
- 프로그램 제거: Windows 설정 → 앱 → 설치된 앱 → Dwarf Fortress 한국어 패치 → 제거 (패치를 먼저 빼고 제거하세요)
- 자세한 안내와 알려진 문제: [README]({repo}#readme)

## ⚠️ 실행할 때 경고 창이 뜨는 경우
설치 파일과 게임에 넣는 `dfhooks.dll`에 코드 서명이 없어서, 처음 실행할 때 **Windows의 PC 보호** 또는 **게시자를 확인할 수 없습니다** 창이 뜰 수 있습니다.
- 바이러스가 탐지됐다는 뜻이 아니라, 서명이 없고 내려받은 사람이 적은 새 파일이라는 뜻입니다
- **추가 정보** → **실행**을 누르면 설치가 진행됩니다
- `dfhooks.dll`은 게임 프로세스 안에서 화면에 글자를 쓰는 함수에 끼어드는 파일이라, 백신에 따라 의심 파일로 볼 수 있습니다. 게임 파일과 저장 파일은 고치지 않습니다

## 🔍 직접 확인하는 방법
- 해시: 이 릴리즈의 `SHA256SUMS`. 프로그램은 업데이트를 받을 때 이 해시와 견준 뒤에만 넣습니다
- 설치 프로그램의 소스: 이 저장소의 [patcher 폴더]({repo}/tree/main/patcher). 설치 폴더의 `app\patcher.py`와 같습니다
- 서명: 설치 폴더 `runtime\`의 `pythonw.exe` 등을 우클릭 → 속성 → 디지털 서명에서 Python Software Foundation 서명을 볼 수 있습니다
- 후킹 모듈(`dfhooks.dll`)과 번역 엔진의 소스는 정리한 뒤에 따로 공개할 예정입니다

## 🙏 도구와 출처
- 후킹 구조와 함수 패턴: [dfint/df-steam-hook-rs](https://github.com/dfint/df-steam-hook-rs) (shevernitskiy, MIT), [vizv/dfint-rust-cjk](https://gitee.com/vizv/dfint-rust-cjk) (Viz, MIT)
- 글꼴: [Galmuri](https://github.com/quiple/galmuri) (Lee Minseo, SIL OFL 1.1)
- 설치 패키지: [WiX Toolset](https://wixtoolset.org/), 실행 환경: [Python](https://www.python.org/) {python}, 루트 인증서 목록: [certifi](https://github.com/certifi/python-certifi) (MPL-2.0)
- 전체 목록과 라이선스 글: 설치 폴더 `app\LICENSE-THIRD-PARTY.txt`

Dwarf Fortress의 저작권은 Bay 12 Games에 있습니다. 비공식 팬 번역이며 게임 파일은 들어 있지 않습니다.
