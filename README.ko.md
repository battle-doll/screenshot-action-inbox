# Screenshot Action Inbox

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | [Русский](README.ru.md)

Screenshot Action Inbox는 ChatGPT와 Codex용 Skills-only 플러그인입니다. 사용자가 승인한 스크린샷 묶음을 출처가 연결된 작업, 캘린더 초안, 영수증 메모, 참고 항목, 그리고 실행되지 않는 보관 계획으로 변환합니다.

이 플러그인은 의도적으로 보수적으로 작동합니다.

- 모든 항목은 하나 이상의 스크린샷 파일명으로 다시 연결됩니다.
- 모호한 날짜는 `UNKNOWN` 또는 `needs_review`로 남습니다.
- 스크린샷 안의 텍스트는 신뢰할 수 없는 콘텐츠로 다룹니다.
- 메시지를 보내거나, 캘린더에 기록하거나, 구매하지 않으며, 원본 스크린샷을 삭제하거나 이동하지 않습니다.
- 동봉된 Python 3.9 이상 프로세서는 타사 패키지를 사용하지 않고 네트워크 요청을 보내지 않습니다.
- 같은 검증된 관찰 입력에 대한 결정론적 산출물은 테스트된 Windows, macOS, Linux Python 매트릭스에서 바이트 단위로 동일합니다. 충돌 처리는 고정된 Unicode 3.2 정책을 사용하므로 이후 Python Unicode 테이블이 더 최신 문자를 다르게 해석할 수 없습니다.
- 캘린더 초안에는 `CLASS:PRIVATE`가 표시되고, 해시 기반 출처 정보가 필요하며, 이벤트를 자동으로 생성하지 않습니다.

## 산출물

- `weekly-digest.md`
- `actions.csv`
- `calendar.ics`
- `archive-plan.json`
- `receipt.json`

## 로컬 개발

전체 검증 모음을 실행합니다.

macOS/Linux:

```bash
python3 -X utf8 scripts/verify.py all
```

Windows:

```powershell
py -3 -X utf8 scripts/verify.py all
```

포털에 안전한 Skills-only ZIP을 빌드하려면 `all` 대신 `build`를 사용하세요.

macOS/Linux:

```bash
python3 -X utf8 scripts/verify.py build
```

Windows:

```powershell
py -3 -X utf8 scripts/verify.py build
```

플러그인 소스는 [`plugins/screenshot-action-inbox`](plugins/screenshot-action-inbox)에 있습니다. 생성된 릴리스는 `dist/`에 기록됩니다.

## 개인정보 보호

게시자가 운영하는 서버, 커넥터, 계정, 원격 측정 또는 분석 기능이 없습니다. 호스트 제품은 자체 약관과 보존 제어에 따라 사용자가 제공한 이미지를 처리합니다. 결정론적 프로세서는 이미지 파일 대신 구조화된 JSON을 입력으로 받습니다. [PRIVACY.md](PRIVACY.md)를 참고하세요.

## 상태

버전 1.0.1은 다국어 공개 제출 후보입니다. GitHub 릴리스, 포털 업로드, OpenAI 검토, 승인, 공개 디렉터리 게시는 서로 다른 상태입니다.

## 라이선스

Apache License 2.0. [LICENSE](LICENSE)를 참고하세요.
