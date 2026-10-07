# eta-compare

TMAP · 네이버지도 · 카카오맵의 **추천 경로 소요시간과 도착예정시간**을 한 화면에서 비교하고,
고른 앱을 목적지로 바로 실행하는 개인용 모바일 웹입니다. 실제 도착 시각을 기록해 3사 예측 오차도 비교할 수 있습니다.

- 개인 비교 테스트용(비상업), 하루 10건 안팎 사용 기준
- Cloudflare Workers 하나로 웹 화면과 API 프록시를 같이 서빙 (무료 플랜으로 충분)
- 운행 기록은 휴대폰 브라우저의 localStorage 에만 저장됨 (CSV 로 내보내기 가능)

## 구조

```
public/            모바일 웹 (index.html, app.js, style.css)
src/worker.js      /api/search, /api/eta 라우팅 + 접근 토큰 확인
src/providers.js   3사 길찾기 호출 및 응답 정규화, TMAP 장소 검색
test/              실제 키 없이 돌아가는 단위 테스트 (npm test)
```

| 서비스 | API | 추천 옵션 | 앱 실행 방식 |
|---|---|---|---|
| TMAP | 자동차 경로안내 `POST /tmap/routes` | `searchOption=0` | `tmap://route?rGoName&rGoX&rGoY` → 현재 위치에서 바로 안내 |
| 네이버 | Directions 5 `/map-direction/v1/driving` | `option=traoptimal` | `nmap://navigation?dlat&dlng&dname&appname` → 바로 안내 |
| 카카오 | 카카오모빌리티 `/v1/directions` | `priority=RECOMMEND` | `kakaomap://route?sp&ep&by=CAR` → 길찾기 화면, 앱에서 '안내 시작' 탭 |

Android 는 `intent://` 형식으로 실행해서 앱이 없으면 Play 스토어로 이동하고,
iOS 는 앱이 열리지 않으면 2.5초 뒤 설치 링크를 안내합니다.

## 1. API 키 발급

| 이름 | 발급처 | 비고 |
|---|---|---|
| `TMAP_APP_KEY` | [SK open API](https://openapi.sk.com) → 앱 생성 → TMAP API 사용 신청 | 경로안내 + 장소(POI) 검색 둘 다 사용 |
| `NAVER_KEY_ID`, `NAVER_KEY` | [네이버 클라우드 콘솔](https://console.ncloud.com) → Maps → Application 등록, **Directions 5** 체크 | Client ID / Client Secret. 결제수단 등록 필요(월 60,000건 무료) |
| `KAKAO_REST_KEY` | [카카오모빌리티 개발자센터](https://developers.kakaomobility.com) → 앱 등록 → REST API 키 | 자동차 길찾기 사용 권한 신청이 필요할 수 있음(일 10,000건 무료) |
| `ACCESS_TOKEN` | 직접 정한 아무 문자열 | 내 웹 주소를 남이 알아도 내 키로 호출하지 못하게 막는 비밀번호 |

## 2. 배포 (Cloudflare Workers)

```bash
cd eta-compare
npm install
npx wrangler login                 # 브라우저에서 Cloudflare 로그인(무료 계정)

npx wrangler secret put ACCESS_TOKEN
npx wrangler secret put TMAP_APP_KEY
npx wrangler secret put NAVER_KEY_ID
npx wrangler secret put NAVER_KEY
npx wrangler secret put KAKAO_REST_KEY

npm run deploy                     # https://eta-compare.<내 계정>.workers.dev 주소가 출력됨
```

휴대폰 브라우저(Safari / Chrome)로 출력된 주소를 열고, 처음 한 번 `ACCESS_TOKEN` 을 입력하면 됩니다.
홈 화면에 추가해두면 앱처럼 쓸 수 있습니다.

로컬에서 먼저 확인하려면 `.dev.vars.example` 을 `.dev.vars` 로 복사해 값을 채우고 `npm run dev`.
(위치 권한은 HTTPS 또는 localhost 에서만 동작하므로, 휴대폰 테스트는 배포 주소로 하세요.)

## 3. 사용법

1. 접속하면 현재 위치를 출발지로 잡습니다(위치 권한 허용).
2. 목적지를 검색해 고르면 3사 소요시간·도착예정시간이 카드로 나옵니다. 가장 빠른 곳에 표시가 붙습니다.
3. 카드를 누르면 해당 앱이 실행되고, 상단에 **운행 중** 배너가 생깁니다.
   조회한 지 3분이 넘었으면 먼저 다시 조회한 뒤 한 번 더 누르게 합니다.
4. 도착하면 웹으로 돌아와 **도착**을 누릅니다. **기록** 탭에서 3사별 예측 대비 오차를 볼 수 있습니다.
   - 오차 = 실제 소요시간(앱 실행 → 도착 버튼) − 예측 소요시간. `+` 면 예측보다 늦게 도착.

## 참고 / 한계

- API 가 주는 시간과 앱이 실행 후 다시 계산한 시간은 조금 다를 수 있습니다(같은 회사여도 엔진·시점 차이).
- 카카오톡 등 **인앱 브라우저**에서는 앱 실행이 막히는 경우가 많아, 감지되면 외부 브라우저로 열라고 안내합니다.
- TMAP URL 스킴(`rGoName/rGoX/rGoY`)은 공개 자료 기준입니다. 앱 업데이트로 동작이 바뀌면 `public/app.js` 의 `PROVIDERS` 만 고치면 됩니다.
- 기록은 해당 휴대폰 브라우저에만 있으므로, 브라우저 데이터를 지우기 전에 CSV 로 내보내세요.
