# News Digest Mailer

RSS 기사 목록을 읽고 각 기사 페이지에서 본문을 추출해 이메일로 배포하는 Python 스크립트입니다. 메일에는 제목, 원문 링크, 최대 300자의 발췌가 들어갑니다. 본문 추출이 안 되면 RSS 요약을 사용합니다. 원문 전체를 복제하지 않습니다.

## 설정

Python 3.12 이상을 사용합니다. 외부 패키지는 필요하지 않습니다.

1. `sources.json`에 확인된 태양광·철강 전문 매체 RSS와 요청한 관심어가 들어 있습니다. `keywords`가 비어 있으면 모든 기사를 포함하며, 값이 있으면 제목이나 RSS 요약에 키워드가 있는 기사만 포함합니다. `since_hours`는 게시 시각 기준 검색 범위, `max_items`는 메일 한 통의 최대 기사 수입니다.
2. 로컬 발송용으로 `.env.example`을 참고해 환경 변수를 설정합니다. Gmail에서는 일반 계정 비밀번호 대신 앱 비밀번호를 `SMTP_PASSWORD`에 사용하세요. 수신자가 여러 명이면 `MAIL_TO`에 쉼표로 구분해 입력합니다.
3. 먼저 `python digest.py --config sources.json --dry-run`으로 메일 내용을 확인합니다. 실제 발송은 `python digest.py --config sources.json`입니다. 일치하는 기사가 없으면 메일을 보내지 않습니다.

테스트: `python -m unittest discover -s tests`

## GitHub Actions 배포

`.github/workflows/digest.yml`은 매일 한국 시간 오전 4시에 실행되며 수동 실행도 가능합니다. Settings → Secrets and variables → Actions에서 `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`, `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_FROM`, `MAIL_TO`를 **Secrets**로 설정하세요. 같은 화면의 **Variables**에 `DIGEST_ENABLED=true`를 설정해야 메일 발송 단계가 실행됩니다. 설정 전에는 테스트만 실행됩니다.

예약 실행은 약간 지연될 수 있습니다. Actions 실행 환경은 매번 새로 생성되므로 이 버전은 기사 발송 이력을 보관하지 않습니다. 같은 날 수동 실행하면 같은 기사가 다시 발송될 수 있습니다. 각 RSS 제공자의 이용 조건과 발췌 사용 범위를 확인하세요.
