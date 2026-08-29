# Screenshot Action Inbox

[English](README.md) | [한국어](README.ko.md) | [日本語](README.ja.md) | [简体中文](README.zh-CN.md) | [Русский](README.ru.md)

흩어진 스크린샷을 출처가 분명한 검토용 다음 행동으로 바꿉니다. Screenshot Action Inbox는 회의 캡처, 초대장, 영수증, 알림, 참고 카드를 모아 두고 무엇이 중요하고 무엇이 중복인지, 각 제안의 근거가 어느 이미지인지 확인하려는 사용자를 위한 플러그인입니다.

## 사용하기

1. [ChatGPT의 Screenshot Action Inbox](https://chatgpt.com/plugins/plugins_6a7cbf30f0208191b29866d20a69743a)를 엽니다.
2. 사용자가 승인한 스크린샷 묶음, 폴더 또는 ZIP을 제공합니다.
3. 출처가 있는 작업, 날짜, 중복 그룹, 불확실성 표시 또는 검토용 캘린더 초안을 요청합니다.

## 이렇게 요청해 보세요

- `회의 스크린샷 폴더를 파일명 출처가 붙은 할 일 목록으로 정리해줘.`
- `여러 캡처에 반복된 작업을 묶고 확인이 필요한 날짜를 표시해줘.`
- `Turn these screenshots into sourced actions and show what needs review.`

## 핵심 경계

- 모든 항목은 하나 이상의 원본 파일명과 연결되고, 모호한 사실은 `UNKNOWN` 또는 `needs_review`로 남습니다.
- 스크린샷 속 텍스트는 신뢰할 수 없는 콘텐츠입니다. 감시, 신원 추론, 단일 이미지 창작 편집, OCR 전사만 하는 작업, 이메일 분류, 코드 영향 분석, 음성 알림 설정에는 사용하지 않습니다.
- 메시지 전송, 실제 캘린더 이벤트 생성, 구매, 스크린샷 삭제 또는 파일 이동을 하지 않습니다. 캘린더와 보관 결과는 초안뿐입니다.
- 동봉된 Python 3.9 이상 프로세서는 타사 패키지나 네트워크 요청을 사용하지 않으며, 같은 검증 입력에 대해 테스트된 Windows, macOS, Linux 매트릭스에서 바이트 단위로 동일한 결과를 냅니다.

## 산출물

- `weekly-digest.md`
- `actions.csv`
- `calendar.ics`
- `archive-plan.json`
- `receipt.json`

## 코드 온톨로지

[인터랙티브 코드 온톨로지 그래프](docs/code-ontology/index.html)에서 저장소 구조를 탐색할 수 있습니다. 자체 완결형 워크벤치는 검색, 범위가 제한된 2D 구조 보기, 선택형 3D 성상도, 소스 근거 확인을 지원합니다. GitHub 파일 화면은 HTML을 실행하지 않고 소스로 표시하므로, 파일을 내려받아 로컬 브라우저에서 여세요.

이 그래프는 소스 리비전 `b42d168b6d45213edb886b683ac5c5ec06942454`를 [Code Ontology Companion](https://github.com/battle-doll/code-ontology-companion) 0.5.2로 분석해 생성했습니다(스냅샷 `20260815T090018Z-49018a955a1c`). 파싱 경고 없이 노드 940개와 관계 2,756개를 포함합니다.

그래프에는 심볼 식별자, 저장소 상대경로, 라인 범위, 정성적 정적 분석 근거가 포함됩니다. 소스 본문, 주석, 로컬 절대경로, 소스 파일별 지문, 자격증명, 모델 출력은 포함하지 않습니다. 관계는 코드 탐색을 위한 근거이며 런타임 추적, 안전성 판정 또는 인과관계의 증명이 아닙니다.

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

2026-08-29 확인 결과 OpenAI Platform 상태는 **Published**이고, 최신 원격 카탈로그 스냅샷은 **GLOBAL/AVAILABLE**, 발견 가능성은 **UNLISTED**입니다. [직접 디렉터리 URL](https://chatgpt.com/plugins/plugins_6a7cbf30f0208191b29866d20a69743a)로 열 수 있습니다. `UNLISTED`이므로 공개 디렉터리에 나열되거나 검색 가능하다고 표현해서는 안 됩니다.

이 저장소의 1.0.2는 업데이트 후보입니다. 위 상태는 현재 원격 등록을 설명하며 1.0.2가 검토 또는 게시되었다는 뜻이 아닙니다.

## 라이선스

Apache License 2.0. [LICENSE](LICENSE)를 참고하세요.
