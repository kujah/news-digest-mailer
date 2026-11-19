# 산업 뉴스 브리핑

국내 경제 매체, 해외 태양광·철강 전문매체, 국내외 건축·외장 매체에 서로 다른 선별 기준을 적용하는 메일러입니다. 공개 RSS와 기사 목록을 읽고 가능한 본문·메타정보에서 관련 문장을 추출해 섹션별 HTML 메일을 만듭니다. Python 3.12 이상과 표준 라이브러리만 사용하며 별도 패키지는 필요하지 않습니다.

## 수집·선별 범위

| 섹션 | 매체 | 선별 기준 |
| --- | --- | --- |
| 국내 태양광 | 한국경제·매일경제·서울경제·헤럴드경제·머니투데이 | 의미 있는 규모의 신규 프로젝트, 국가·지역 전망, 정책 변화, 산업 동향, 주요 모듈·구조물 업체의 사업·경영 동향 |
| 국내 ESS/BESS | 같은 국내 경제 매체 5곳 | ESS/BESS가 명시된 프로젝트·시장 전망·정책·주요 업체 동향. 일반 전기차 배터리 뉴스 제외 |
| 해외 ESS/BESS | pv magazine·Solar Power World·PV Tech | 국가별 저장장치 시장·정책·주요 업체·프로젝트 동향을 BESS 카드에 함께 반영 |
| 국내 철강·통상 | 같은 국내 경제 매체 5곳 | 철강 수출입·관세·반덤핑·세이프가드·TRQ·CBAM, 주요 철강사의 투자·증설·실적·사업 변화 |
| 해외 태양광 구조물·산업 전망 | pv magazine·Solar Power World·PV Tech | 지정 구조물·트래커 업체, 국가·지역 전망·정책, 수상·영농형·분산형 등 산업 동향 |
| 해외 철강·통상 | Steel News·Steel Industry News | 기존 철강 매체를 유지하면서 통상·수출입·주요 철강사 동향으로 한정 |
| 국내 건축·외장 | 월간익스테리어·WINDOOR·월간 창과문·건축세계·C3코리아 | 건축·설계사무소, 착공·기공식·건설사 수주, 비정형 패널, 스틸커튼월·커튼월공사·지붕공사 |
| 해외 건축·외장 | FTI·Facade Today·Facade World·ArchDaily·IGS Magazine·Archello·Glass on Web | 같은 건축·외장 관심사의 영어 대응 표현. ArchDaily는 외장·소재·지붕 맥락도 함께 요구 |

요청한 매체는 모두 설정에 등록했으며 실제 접근 가능 여부는 [SOURCE_CATALOG.md](SOURCE_CATALOG.md)에 기록했습니다. 월간익스테리어·월간 창과문은 공식 기사 경로 확인까지 비활성화 상태이고, Archello는 확인 당시 robots.txt가 403을 반환해 건너뜁니다. 메일에 제한된 매체의 상태를 표시합니다.

메일은 **철강 / 태양광 / BESS / 외장·건축** 순서의 네 개 카드 섹션으로 표시합니다. 국내외 소싱 카테고리는 각 주제 안에 합치며, 기사마다 매체·게시일·선별 이유·관련 문장·원문 링크를 제공합니다. 각 주제에 같은 건수 제한을 두지 않으며, 선정 기사가 없는 주제도 상태를 표시합니다.

구조물·트래커 업체는 Nextpower/Nextracker, GameChange Energy/Solar, Ideematec, PV Hardware, Arctech, Array Technologies, Axial, FTC Solar, Gonvarri Solar Steel, Powerway, MetaloGalva, Vergo Enerji를 영문명·국문명·주요 별칭으로 찾습니다. 일반 단어 `array`를 Array Technologies로 판정하지 않습니다.

## 설정 파일

`sources.json`에서 조정합니다.

- `sources`: 매체별 RSS·공개 목록·허용 도메인·적용 카테고리. RSS가 없는 매체는 확인한 기사 목록과 기사 URL 패턴을 사용합니다.
- `categories`·`term_groups`: 매체 그룹별 주제와 선별 규칙. 전 매체에 하나의 키워드 목록을 적용하지 않습니다.
- `entities`·`markets`: 주요 업체 별칭과 국가·지역 표시용 표현.
- `project_thresholds`: 기본 태양광 **50MW**, BESS **100MWh**. 에너지 용량이 없는 BESS 프로젝트의 전력 용량 기준은 **50MW**입니다. GW/GWh/kW와 `5만kW` 등도 환산합니다.
- `allow_unknown_project_size`: 태양광·BESS 신규 프로젝트의 규모가 미공개이면 기본 제외. 필요한 카테고리에서 `true`로 바꿀 수 있습니다. 시장·정책·업체 동향에는 최소 프로젝트 규모를 적용하지 않습니다.
- `since_hours`: 기본 26시간. 일일 예약 실행을 위한 여유 2시간을 포함합니다.
- `max_items`: 전체 최대 **500건**. 주제별·매체별 메일 반영 건수 제한은 기본 설정에서 제거했습니다. 한 주제에 40건, 다른 주제에 1건이면 모두 반영할 수 있습니다. 전체 한도는 최대 1,000건까지 설정할 수 있습니다.
- `include_undated`: 기본 `false`. 게시일 미확인 기사로 과거 글이 섞이지 않도록 제외하며, 조회 시각을 게시일로 바꿔 쓰지 않습니다.
- `sources[].max_candidates`·`sources[].max_article_fetches`: 매체별 후보 1,000건, 본문 조회는 국내 경제 매체 150건·전문 매체 100건. 이전의 후보·본문 상한을 늘렸으며 상한 적용 시 메일에 표시합니다. 본문 상한을 넘으면 날짜가 있는 RSS 발췌로만 선별합니다. 공개 목록에 없는 기사와 접근 제한 본문까지 모두 수집한다고 보장할 수는 없습니다.

선별은 제목·RSS 요약·접근 가능한 본문에 대한 키워드와 규모 규칙입니다. 태양광·BESS는 제목·도입부 또는 여러 핵심 문장에서 주제가 확인되어야 하며, 시장·정책·프로젝트 신호도 해당 주제를 다루는 문장에서 찾습니다. 아파트 분양의 BIPV 설비 언급, 인근 ESS 공장, 동박 업황 기사의 태양광 정책 언급만으로 선정하지 않습니다. 관련 문장을 골라 최대 500자로 발췌하며 AI 요약이나 번역은 포함하지 않습니다. 영어 기사는 영문 제목·발췌로 표시됩니다.

## 실행과 미리보기

프로젝트 폴더에서 실행합니다.

```powershell
python digest.py --check-sources
python digest.py --preview
python digest.py --preview --history-backend local
python digest.py --preview --history-backend gmail
python digest.py --preview --sources pv_magazine,windoor,glassonweb --since-hours 168
python digest.py --dry-run --max-items 10
python -m unittest discover -s tests -v
```

`--check-sources`는 robots.txt·RSS·기사 목록만 확인합니다. `--preview`는 `data/preview/latest.html`과 `latest.txt`를 만들고 발송 이력을 변경하지 않습니다. `--dry-run`은 내용을 터미널에 표시합니다. CLI 설정 경로는 프로젝트 안으로, 상태 디렉터리는 프로젝트의 `data/` 안으로 제한됩니다.

발송 명령은 `python digest.py --config sources.json`입니다. 프로세스 환경 변수로 메일 설정을 읽습니다. `.env.example`은 변수 목록 참고용이며 `.env`를 자동으로 읽거나 실행하지 않습니다.

| 환경 변수 | 용도 |
| --- | --- |
| `SMTP_USER` | 발신 SMTP 로그인 주소. Gmail의 경우 Gmail 주소 |
| `SMTP_PASSWORD` | 제공자가 허용하는 SMTP 비밀번호. Gmail의 경우 앱 비밀번호 |
| `MAIL_TO` | 수신 이메일 주소. 여러 주소는 한 줄에서 쉼표로 구분 |
| `MAIL_FROM` | 선택 사항. 미설정 시 `SMTP_USER` |
| `SMTP_HOST` | 선택 사항. 기본 `smtp.gmail.com` |
| `SMTP_PORT` | 선택 사항. SSL 기본 465, STARTTLS 기본 587 |
| `SMTP_SECURITY` | 선택 사항. `ssl` 또는 `starttls` |
| `HISTORY_BACKEND` | 선택 사항. 기본 `gmail`. `local`은 로컬 SQLite 이력만 사용 |
| `HISTORY_IMAP_USER` | 선택 사항. Gmail 이력 조회 계정. 미설정 시 `SMTP_USER` |
| `HISTORY_IMAP_PASSWORD` | 선택 사항. Gmail 이력 조회 앱 비밀번호. 미설정 시 `SMTP_PASSWORD` |

Secrets의 값에는 `SMTP_USER=`·`MAIL_TO=` 같은 이름표를 넣지 않습니다. 주소 중간의 줄바꿈이나 잘못된 주소는 서버에 연결하기 전에 거부합니다. 앱 비밀번호·토큰은 코드·문서·채팅·로그에 넣지 않습니다.

## 이력과 수집 보고서

같은 사건의 보도는 한 카드로 묶습니다. 공개 본문이 있고 내용이 충실한 기사를 대표로 선택하며, 다른 매체의 원문 링크도 카드 안에 표시합니다. 제목·문장의 유사도와 업체·프로젝트·정책·국가·수치 등 사건 근거를 비교합니다. 같은 회사나 같은 국가의 뉴스라는 이유만으로 합치지 않으며, 서로 다른 프로젝트와 실적·전망의 다른 보고 기간은 분리합니다.

기본 `gmail` 이력 모드는 Gmail 보낸편지함에서 최근 **14일**의 브리핑을 읽습니다. `imap.gmail.com:993`에 TLS로 연결하고 `EXAMINE`과 `BODY.PEEK`를 사용하므로 읽음 상태·내용·폴더를 바꾸지 않습니다. 다른 보낸 메일은 제목·날짜 헤더만 확인하고 본문은 가져오지 않습니다. 검색 결과가 기본 2,000통을 넘으면 일부만 확인했다고 간주하지 않고 오류로 종료합니다. `history.lookback_days`·`max_mailbox_messages`로 조정할 수 있습니다.

기존 메일은 제목·발췌문을 파싱하고, 새 메일은 화면에 보이지 않는 HTML 주석에 사건 비교 정보를 포함합니다. 별도 첨부파일을 만들지 않습니다. 새 실행마다 보낸편지함에서 이 정보를 복원하므로 GitHub-hosted 환경에서도 실행 사이 이력이 유지됩니다. 이메일 원본이나 인증정보를 저장소·GitHub cache·artifact에 업로드하지 않습니다.

전날 보낸 사건을 다른 매체가 새 제목으로 보도해도 내용이 같으면 제외합니다. 실제 착공·준공·계약·승인 등 단계가 바뀌거나 수치 변경 근거가 확인되면 다시 표시하고 `새 내용`을 붙입니다. 단순 발행일 변경과 배경 설명의 수치 추가는 새 소식으로 보지 않습니다. 사건 비교는 규칙 기반이며, 판단 근거가 약하면 서로 다른 기사로 유지합니다.

Gmail 조회 실패 또는 기존 브리핑 파싱 실패는 발송 전에 오류로 종료합니다. 중복 검사를 건너뛴 메일을 보내지 않습니다. SMTP에서 모든 수신자를 수락한 후에만 현재 사건을 `data/history.sqlite3`에 기록합니다. 전송 실패나 미리보기는 발송 완료로 기록하지 않습니다. SMTP 수락은 수신함 최종 도착을 보장하지 않습니다.

`data/runs/` 보고서에는 `source_url`·UTC `fetched_at`·HTTP 상태·발췌 출처·게시일·선별 이유와 매체별 결과가 있습니다. 본문 전체나 인증 정보는 보고서에 저장하지 않습니다. 로그에는 인증 헤더·토큰 쿼리·연결 문자열 비밀번호를 가리는 필터를 적용합니다. `data/`, `.env`, `*.local.*`는 Git에 포함하지 않습니다.

메일의 마지막 표에는 매체별 접근 상태와 실제 반영 기사 수가 표시됩니다. 모든 활성 매체가 실패하면 실행을 실패로 종료합니다. 수집에 성공했지만 관심사의 새 기사가 없으면 기본적으로 0건 브리핑과 매체 상태를 보내 예약 작업의 결과를 확인할 수 있습니다. 이 알림을 끄려면 `send_empty_digest`를 `false`로 바꿉니다.

## GitHub Actions

현재 워크플로는 한국 시간 오전 4시에 예약 실행되며 수동 실행도 가능합니다. 테스트가 통과하고 활성화 변수가 맞을 때만 실행합니다. 동시 실행은 직렬화합니다.

Repository **Secrets**에는 `SMTP_USER`, `SMTP_PASSWORD`, `MAIL_TO`를 등록합니다. 선택 사항인 `MAIL_FROM`도 Repository Secret입니다. Repository **Variables**에는 이름을 `DIGEST_ENABLED`, **값을 소문자 `true`만** 입력합니다. `DIGEST_ENABLED=true` 전체 문자열을 값에 넣으면 발송 단계가 건너뛰어집니다. 선택 사항인 `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY`는 Repository Variables입니다.

현재 Gmail 발송 계정의 `SMTP_USER`·`SMTP_PASSWORD`가 등록되어 있다면 이력 조회에도 기본적으로 같은 Secret을 사용합니다. 별도 조회 계정을 쓸 때만 `HISTORY_IMAP_USER`·`HISTORY_IMAP_PASSWORD`를 Repository Secrets에 추가합니다. `HISTORY_BACKEND`를 Repository Variable에 설정한다면 값은 `gmail`입니다. Gmail IMAP 로그인은 실제 워크플로 첫 실행에서 확인해야 하며, 계정 정책이 IMAP을 허용해야 합니다.

`local` 모드는 이 PC처럼 디렉터리가 유지되는 환경용입니다. GitHub-hosted runner에서 `local`로 설정하면 실행마다 이력이 초기화돼 전날 사건 비교가 유지되지 않습니다. 기본 `gmail` 모드는 보낸편지함으로 이 문제를 해결합니다. 로컬 미리보기는 별도 지정 없이 `local` 모드를 사용해 Gmail에 연결하지 않습니다.

## 새 매체 연결

공식 RSS·기사 목록과 robots.txt를 먼저 확인합니다. 접근 제한·로그인·유료 본문을 우회하지 않습니다. 호스트당 초당 1회 이하로 조회하고 robots.txt의 crawl-delay와 Retry-After를 따릅니다. 공개 HTTPS만 허용하며 private·loopback·link-local·metadata 주소와 인증 정보가 포함된 URL은 차단합니다. 공개 페이지에서 찾은 비ASCII 기사 경로는 UTF-8 퍼센트 인코딩으로 요청합니다.
