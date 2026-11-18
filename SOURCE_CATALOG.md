# 매체 연결 점검 기록

점검일: 2026-11-18. 공개 robots.txt·RSS·기사 목록의 실제 응답을 확인했습니다. HTTP 200은 모든 기사나 유료 본문에 접근할 수 있다는 뜻이 아닙니다. 실행별 상태와 출처는 `data/runs/`에 기록됩니다.

## 국내 경제 매체 5곳

| 매체 | 확인한 경로 | 방식·상태 |
| --- | --- | --- |
| 한국경제 | `https://www.hankyung.com/feed/economy`, `/feed/international`, `/feed/all-news` | RSS 정상 |
| 매일경제 | `https://www.mk.co.kr/rss/30200030/`, `/rss/30100041/`, `/rss/30300018/` | 산업·경제·국제 RSS 정상 |
| 서울경제 | `https://www.sedaily.com/rss/business`, `/rss/economy`, `/rss/international` | 공식 `/RSS` 안내에서 찾은 RSS 정상 |
| 헤럴드경제 | `https://biz.heraldcorp.com/rss/google/newsAll` | 홈페이지가 안내하는 RSS 정상. 기사 300건 반환 |
| 머니투데이 | `https://www.mt.co.kr/industry`, `/world`, `/policy` | 공개 HTML 목록 정상. 산업·세계·정책 기사 URL만 수집 |

아시아경제는 확인한 홈페이지·RSS·목록 경로의 자동 수집을 robots.txt에서 금지해 제외했습니다. 이데일리는 robots.txt 연결에 실패해 제외했습니다. 경제·경영 중심 국내 메이저 매체 5곳을 구성하기 위해 실제 접근을 확인한 헤럴드경제·머니투데이로 대체했습니다.

## 기존 해외 태양광·철강 매체

| 매체 | RSS | 상태 |
| --- | --- | --- |
| pv magazine | `https://www.pv-magazine.com/feed/` | 정상 |
| Solar Power World | `https://www.solarpowerworldonline.com/feed/` | 정상 |
| PV Tech | `https://www.pv-tech.org/feed/` | RSS 정상. 개별 기사 본문은 403으로 제한될 수 있음 |
| Steel News | `https://steelnews.biz/feed/` | 정상 |
| Steel Industry News | `https://steelindustry.news/feed/` | 정상 |

## 국내 건축·외장

| 매체 | 확인한 도메인·경로 | 연결 범위 |
| --- | --- | --- |
| 월간익스테리어 | `https://exteriormagazine.co.kr` | robots.txt 연결 실패. 공식 도메인·기사 경로 확인까지 비활성화 |
| 월간 창호기술 WINDOOR | `https://www.windoor.co.kr/rss/allArticle.xml` | RSS 정상. HTTP 기사 링크는 같은 도메인의 HTTPS로 조회 |
| 월간 창과문 | `https://www.windowndoor.co.kr` | robots.txt 연결 실패. 공식 도메인·기사 경로 확인까지 비활성화 |
| 건축세계 Archiworld | `https://www.archiworld1995.com/customer/notice.php` | 공개 공지·소식 목록만 확인. 잡지 도서 판매 페이지나 유료 본문은 수집하지 않음 |
| C3코리아 | `https://www.c3korea.net/feed/` | RSS 정상 |

## 해외 건축·외장

| 매체 | 경로 | 연결 범위 |
| --- | --- | --- |
| Facade Tectonics Institute | `https://www.facadetectonics.org/news/collection/news`, `/articles/collection/articles` | 홈페이지에 공개된 뉴스·아티클 목록. `/news?format=rss`는 실제 RSS가 아니어서 제외 |
| Facade Today | `https://facadetoday.com/feed/` | RSS 정상 |
| Facade World | `https://facadeworld.com/feed/` | RSS 정상 |
| ArchDaily | `https://feeds.feedburner.com/ArchDaily` | 공식 `/rss`의 리디렉션 목적지. RSS 응답은 확인했지만 FeedBurner의 robots.txt 확인이 실패해 현재 수집 제외. 연결 가능해지면 외장·소재·지붕 맥락으로 선별 |
| IGS Magazine | `https://igsmag.com/feed/`, 홈페이지 기사 링크 | 응답 정상이나 RSS 최근 게시일은 2026-06-30. 기간 밖 글은 제외. 공개 기사 목록도 조회 |
| Archello | `https://archello.com/news` | robots.txt HTTP 403. 접근 제한을 우회하지 않고 건너뜀 |
| Glass on Web | `https://www.glassonweb.com/news` | 최근 공개 뉴스 목록 정상. `/rss.xml`은 2022년 기사만 반환해 제외 |

매체의 접근이 막혔다고 다른 검색 서비스의 기사를 넣어 직접 수집한 것처럼 표시하지 않습니다. 비활성 매체를 활성화하려면 공식 도메인과 최근 기사 목록을 확인한 뒤 `sources.json`의 URL·기사 패턴·상태를 수정합니다.
