# medicine

한국 DUR 공개 데이터를 기반으로 여러 사람의 복약을 관리하는 모바일 우선 로컬 웹앱입니다.
현재는 텍스트로 의약품을 검색해 구조화된 처방 정보로 등록하고, 하루 단위 복용 계획과 실제
복용 기록을 관리합니다. 최종 목표는 같은 API/도메인 구조를 사용하는 Android 앱과 처방전
사진 입력입니다.

## 실행

Docker만 있으면 됩니다.

```bash
cd ~/dev/medicine
export LOCAL_UID="$(id -u)"
export LOCAL_GID="$(id -g)"
docker compose up -d --build web
```

`LOCAL_UID`/`LOCAL_GID`를 현재 사용자 ID로 설정하면 bind mount에 생성되는 DB, 빌드 산출물,
스크린샷 등이 root 소유로 남지 않습니다. 같은 셸에서 이후 `docker compose` 명령을 실행합니다.

브라우저에서 `http://127.0.0.1:18787`을 엽니다. 포트 변경:

```bash
MEDICINE_PORT=19000 docker compose up -d web
```

종료:

```bash
docker compose down
```

## 현재 기능

- 모바일 1열 UI + 하단 탭: 홈 / 복용 / 약 검색 / 가족 / 설정
- 여러 사람 프로필을 한 기기에서 분리 관리
  - 이름, 생년월일, 성별, 임신 여부
- 의약품 검색
  - Android와 로컬 개발 런타임 모두 `mobile.sqlite`의 식약처 허가제품을 `ITEM_SEQ` 기준으로 검색
  - 사용자 약 검색에는 허가상태 `정상` 품목만 표시하고, 비활성 품목은 신규 복용약으로 등록하지 않음
  - 취소·취하·만료 등 비활성 품목 행은 기존 복용약의 현재 허가상태 확인과 제품 식별을 위해 canonical/mobile DB에 유지
  - 정상 상태에서 등록된 기존 복용약의 허가상태가 나중에 바뀌면 복용 일정·기록은 유지하고 별도 `허가상태 변경` 안내를 표시
  - 허가상태를 `active / expired / withdrawn / business_closed / canceled`로 정규화
  - EDI는 검색·표시용 보조 식별자로만 사용하고 안전성 identity로 사용하지 않음
- 약 추가 전 자동 DUR 확인
  - 식약처 허가품목의 `ITEM_SEQ`를 canonical 제품 식별키로 사용
  - MFDS ITEM_SEQ 제품규칙과 MFDS 성분 DUR 기준을 공식 성분코드로 연결한 canonical product criterion을 직접 평가
  - 공식 제품규칙은 있으나 상세기준 연결이 확정되지 않은 경우 안전으로 추측하지 않고 `unknown`으로 표시
  - 수유부주의는 현재 자동 DUR 지원 범위에서 제외
  - EDI/HIRA/제품명 fallback이나 ingredient alias graph를 사용하지 않음
  - 병용금기·효능군 중복은 `active` 플래그만 보지 않고 두 처방의 시작·종료일이 실제로 겹치는지 확인
  - `24/48시간 이내 병용금기`, `특정 성분 투여 중 및 종료 후 N일/N주`처럼 기간과 방향을 확정할 수 있는 원문은 구조화해 washout까지 적용
  - 중단 후 제한이 있다는 사실만 있고 기간·방향을 확정할 수 없는 원문은 추측하지 않고 판정 불가 상태로 계속 경고
  - 성분명·제품코드 매핑이 불명확하면 추측하지 않고 자동 확인 범위 제한으로 표시
  - 현재 복용약과의 병용금기
  - 특정연령대금기
  - 임부금기
  - 65세 이상 노인주의
  - 효능군중복주의
  - 용량주의/투여기간주의 대상 여부 안내
  - 서방정분할주의
- 구조화된 처방 정보
  - 1회 복용량 / 단위
  - 1일 횟수
  - 복용 시간
  - 식전·식후 등 식사 관계
  - 경구·외용·흡입 등 투여 경로
  - 처방 일수와 시작·종료일, 또는 명시적 장기복용(종료일 없음)
  - 필요시 복용(PRN): 고정 일정과 분리하고 1일 최대 횟수 및 실제 복용 기록 지원
- 입력 처방의 정량 DUR 확인
  - canonical 제품 기준 투여기간과 처방 일수를 자동 비교
  - 제품규칙과 상세기준 연결을 확정할 수 없는 경우 보완 추측 없이 판정 불가로 표시
  - 단일 기준과 단위가 명확한 용량주의 항목은 1일 복용량을 자동 비교
  - 복수·조건부 기준이나 환산할 수 없는 단위는 안전으로 간주하지 않고 판정 불가 사유 표시
  - 금기·주의, 정량 기준 초과 또는 중요한 커버리지 제한은 경고를 먼저 표시하고 확인 후 등록 허용
  - 경고 확인 토큰은 처방 내용, 당시 DUR 데이터셋과 실제 평가 결과에 묶여 안전성 맥락이 바뀌면 재확인 필요
- 처방 수정과 변경 이력
  - revision 기반 동시 수정 충돌 방지
  - 수정 시 하루 복용 회차 identity를 보존해 시간 변경이 추가 복용을 만들지 않고, 과거 완료·건너뜀 기록 보존
  - 등록·수정·종료 당시 처방, DUR 판정, 평가기 버전과 데이터셋 식별자 스냅샷 보존
- 오늘 복용 계획 자동 생성
  - 같은 날짜를 여러 번 조회해도 동일 복용 인스턴스 유지
  - 복용 완료 / 건너뜀 상태 추적 및 잘못 누른 기록 되돌리기
  - PRN 약은 고정 일정과 분리하고 실제 복용 시점만 별도로 기록
- 복용 종료 처리 / 최근 복용 기록 조회
- Android, 로컬 개발 웹, Agent Control CLI가 공유하는 Rust `MedicineEngine` 코어
- 로컬 개발용 standalone web은 Rust HTTP 어댑터로 정적 UI와 JSON API를 제공하며 기본적으로 `127.0.0.1`에만 공개
- 서버 없는 Android 패키징
  - WebView UI는 APK에 포함하고 앱 도메인/API는 JNI를 통해 동일한 Rust `MedicineEngine`을 사용하며, reference DB는 signed hosted channel에서 first-launch bootstrap
  - `INTERNET` 권한은 native reference downloader에만 사용하며 WebView 외부 HTTP/HTTPS 요청은 차단
  - 개인 복약 DB는 Android Keystore AES-GCM 키로 요청 사이에 암호화해 보관하고, SQLite 처리 중에만 앱 전용 저장소의 임시 평문 DB를 사용
  - 비정상 종료로 임시 평문 DB가 남으면 다음 시작 시 이를 최신 상태로 복구·checkpoint한 뒤 즉시 다시 암호화
  - 배포 reference DB는 읽기 전용으로 분리

DUR 결과가 없다는 것은 안전하다는 뜻이 아닙니다. 앱은 제품·성분 매핑, 데이터셋 검증 상태,
지원하는 프로필 범위를 함께 기록하고, 지원 범위의 검사가 모두 명확하게 끝난 경우에만
`DUR 주의사항 없음`을 표시하며 `DUR 자동 확인 범위 제한`과 구분합니다. 현재 자동 판정에는 알레르기, 신장·간 기능, 체중·적응증, 등록하지 않은
일반약·건강기능식품 등의 임상정보가 포함되지 않습니다.

## DB 구조

### `data/db/personal.sqlite`

개인 복약 데이터 DB입니다.

- `people`: 관리 대상 프로필
- `medications`: 사람별 복용약 + 구조화 처방
- `medication_schedules`: 명시적 복용 시간
- `dose_instances`: 날짜별 실제 복용 예정 건과 상태
- `dose_logs`: 실제 복용/건너뜀 기록
- `medication_revisions`: 등록·수정·종료 처방 및 DUR 판정의 append-only 이력
- `medication_requests`: 중복 등록을 방지하는 요청 ID와 처방 fingerprint

기존 v1 `personal.sqlite`는 실행 시 누락 컬럼/테이블만 추가하는 방식으로 migration합니다.
개인 DB는 Git에 포함하지 않습니다. Android 앱은 Keystore 키로 암호화된 `personal.sqlite.enc`를
지속 저장본으로 사용합니다. 개발용 CLI/standalone web의 `data/db/personal.sqlite`는 로컬 개발
파일로서 암호화하지 않으며, 웹 서비스도 기본적으로 `127.0.0.1`에만 바인딩합니다.

### `data/db/canonical.sqlite` (reference 빌드 기준 DB)

Android와 로컬 개발 런타임이 직접 여는 DB가 아니라, 배포/개발용 `mobile.sqlite`를 만드는 authoritative
build input입니다. 세 공식 MFDS 원본 계열을 직접 보존하고 `ITEM_SEQ`를 제품 중심키로 사용합니다.

- `mfds_permit_api`: 식약처 허가제품 API
- `mfds_dur_item_api`: 식약처 DUR 품목 API 9개 endpoint
- `mfds_dur_ingredient_api`: 식약처 DUR 성분 API 7개 endpoint

지원하는 성분 기준은 병용금기, 특정연령대금기, 임부금기, 용량주의, 투여기간주의, 노인주의,
효능군중복주의입니다. 성분 API에서 `DEL_YN=정상`인 행만 canonical 기준으로 가져오며 삭제 행은
현재 근거로 사용하지 않습니다. 수유부주의는 지원하지 않습니다.

제품 DUR은 `product_rules -> product_criterion_links -> ingredient_rules`로 연결하며
`product_rule_criteria` view로 조회합니다. Ingredient criterion은 MFDS 공식 `INGR_CODE` payload가
반드시 있어야 하며, 코드가 없는 criterion을 성분명 유사도나 별칭으로 복구하는 fallback은 없습니다.
복합제는 MFDS의 성분코드 조성과 허가제품 조성을 비교하고, 제형 범위와 검토된 소수의 명시적
제품-scope override를 적용합니다. 염·수화물·용매화물 등 서로 다른 precise substance를 임의의
문자열 규칙으로 합치지 않습니다.

MFDS `REMARK`는 `qualifier_note`로 보존합니다. 앱에 적응증·농도 등 해당 조건을 판정할 authoritative
입력이 없으면 REMARK 자연어를 실행 규칙으로 해석하지 않고 `conditional`/전문가 확인 필요로
fail-closed합니다. 효능군중복의 `SERS_NAME` 같은 분류 설명은 일반 `note`로 분리해 REMARK와
혼동하지 않습니다.

앱 런타임은 EDI/HIRA/제품명 fallback이나 ingredient alias graph를 사용하지 않습니다. canonical에 존재하는
제품에 해당 DUR 제품규칙이 없으면 해당 항목은 clear/not-applicable이고, 공식 제품규칙은 있지만 대응하는
MFDS 성분기준 링크를 확정할 수 없는 경우에만 fail-closed `unknown`으로 남습니다. `product_code` API/개인 DB
호환 필드는 신규 등록에서 ITEM_SEQ를 저장하며, 기존 개인 DB 재평가는 `catalog_item_seq`를 authoritative
reference로 사용합니다.

Canonical source snapshot은 총 17개를 정확히 요구합니다: 허가 API 1개 + 품목 DUR API 9개 + 성분 DUR API
7개입니다. 예상하지 않은 source family/key가 섞이거나 필수 snapshot이 빠지면 release/runtime verification이
실패합니다.

API 원본은 `data/canonical/raw/*.jsonl`, 성분 DUR 원본은 `data/canonical/mfds_ingredient/*.jsonl`, substance
원본은 `data/canonical/substances/`에 보존합니다. 앱용 완전한 DB는 source → substance → DUR bridge →
product link 순서를 보장하는 integrated build로 재생성합니다.

```bash
# 최신 MFDS 허가/품목 DUR/성분 DUR API를 동기화
docker compose run --rm canonical sync --json

# 최신 MFDS API를 동기화한 뒤 canonical + substance DB 전체 재구축
docker compose run --rm canonical integrated-rebuild --json

# 이미 보존된 snapshot만 사용해 네트워크 없이 전체 재조립
docker compose run --rm canonical integrated-build --json

# release gate
docker compose run --rm canonical verify --json
docker compose run --rm canonical substance-verify --json
docker compose run --rm canonical stats --json

# 특정 ITEM_SEQ의 제품 DUR ↔ MFDS 성분기준 링크 확인
docker compose run --rm canonical criteria --item-seq 198600630 --json

# Android와 로컬 개발 런타임이 공통으로 사용할 compact reference snapshot 생성
docker compose run --rm canonical mobile-build --json
```

Reference DB 배포 workflow는 fresh source run에서 MFDS API snapshot과 substance identity snapshot을
동기화하고 검증한 뒤 integrated build와 mobile release를 생성합니다. GitHub Actions에는 R2 배포 secret과
`DATA_GO_KR_SERVICE_KEY`가 필요합니다. 수동 실행은 필요하면 검증된 source cache를 재사용할 수 있고,
정기 실행은 매일 03:17 `Asia/Seoul`에 항상 fresh source sync를 수행합니다. 정기 실행은 repository variable
`REFERENCE_PUBLISH_SCHEDULE_ENABLED=true`일 때만 실제 job을 실행하므로 rollout 검증 전에는 `false`로 둡니다.
R2 bucket은 public 개발 URL을 켜기 전에 `medicine-canonical r2-public-audit --json`으로
`reference/v1/` 외 객체가 없는지 확인합니다.

개발 단계의 Android 앱과 standalone development web은 Cloudflare R2의 동일한 non-production `r2.dev`
reference channel을 사용합니다. 현재 개발 endpoint는
`https://pub-539f06de795a469c85ab40570a8634a2.r2.dev/`입니다. Android는 이 값을 Gradle debug/release
기본값으로 사용하고, standalone web은 Compose의 `MEDICINE_REFERENCE_UPDATE_BASE_URL` 기본값으로 사용합니다.
Android의 `MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL`은 개발 endpoint를 교체하며,
`MEDICINE_REFERENCE_UPDATE_BASE_URL`은 Android와 standalone web 모두에서 명시적 테스트 override로 사용할 수
있습니다. 출시 준비 시 Android 배포 channel은 `r2.dev` 대신 custom domain으로 교체합니다.

`canonical verify`가 실패하면 앱은 데이터셋을 verified로 취급하지 않습니다. Reference publish workflow는
`canonical mobile-build`로 `mobile.sqlite`와 manifest를 생성해 signed hosted release로 배포하지만, Android
APK 자체에는 해당 DB나 manifest를 포함하지 않습니다. APK에는 reference 계약·서명·DB를 검증하고 앱 API를 실행하는 Rust 코어만 패키징하며 Python/Chaquopy runtime은 포함하지 않습니다.

## 앱 제어 CLI

`app` 서비스의 Python 코드는 CLI 인자와 출력 형식만 담당하며, 개인 DB 초기화와 모든 앱 도메인 요청은
동일 이미지의 Rust `medicine-core` Agent Control CLI로 전달합니다.

```bash
# 사람 목록
docker compose run --rm app people --json

# 프로필 추가
docker compose run --rm app person-add \
  --name "나" \
  --birth-date 1990-01-01 \
  --sex female \
  --pregnancy-status not_pregnant \
  --lactation-status not_breastfeeding \
  --json

# 의약품 검색
docker compose run --rm app drug-search 졸피뎀 --limit 10 --json

# 추가 전 DUR 범위/위험 미리보기
docker compose run --rm app risk-preview \
  --person <PERSON_ID> \
  --product-ref <PRODUCT_REF> \
  --dose-amount 1 --dose-unit 정 --frequency 2 --days 7 \
  --json

# 구조화 처방으로 복용약 추가
docker compose run --rm app med-add \
  --person <PERSON_ID> \
  --product-ref <PRODUCT_REF> \
  --dose-amount 1 \
  --dose-unit 정 \
  --frequency 2 \
  --meal-relation after_meal \
  --route oral \
  --days 7 \
  --start-date 2026-08-10 \
  --time 08:00 \
  --time 20:00 \
  --request-id <UNIQUE_REQUEST_ID> \
  --json

# 확인이 필요한 DUR 위험·기준 초과·중요 커버리지 제한은 warning_token을 반환합니다. 내용을 확인한 뒤 위 med-add 명령을
# 동일한 처방·요청 ID로 재실행하면서 다음 옵션을 추가합니다.
# --acknowledge-warnings --warning-token <WARNING_TOKEN>

# 처방 수정과 변경 이력
docker compose run --rm app med-update \
  --medication <MEDICATION_ID> --expected-revision <REVISION> \
  --time 09:00 --time 21:00 --json
docker compose run --rm app med-history --medication <MEDICATION_ID> --json

# 종료일 없는 장기복용은 --days 대신 명시적으로 --long-term 사용
# 필요시 복용은 고정 --frequency/--time 없이 --prn과 선택적 --prn-max 사용
docker compose run --rm app med-add \
  --person <PERSON_ID> --product-ref <PRODUCT_REF> \
  --prn --prn-max 3 --long-term --request-id <UNIQUE_REQUEST_ID> --json

# 필요시 약을 실제 복용했을 때 기록
docker compose run --rm app prn-intake \
  --medication <MEDICATION_ID> \
  --request-id <UNIQUE_REQUEST_ID> \
  --json

# 하루 복용 계획
docker compose run --rm app daily-plan \
  --person <PERSON_ID> \
  --date 2026-08-10 \
  --json

# 특정 복용 예정 건 완료 처리
docker compose run --rm app dose-instance \
  --instance <DOSE_INSTANCE_ID> \
  --status taken \
  --json
```


## 테스트 / 모바일 렌더링

```bash
docker compose run --rm --build test
```

테스트는 개인 DB migration, 여러 사람 관리, 구조화 처방, 정량 용량·기간 판정, 경고 확인 토큰,
중복 등록 방지, 처방 revision·이력, 날짜별 복용 인스턴스 멱등성, 수정 후 완료 이력 보존, PRN,
복용 기록, 전체 허가품목/허가상태 검색, 병용금기, 연령/임부/노인주의, 효능군 중복,
canonical build/link/applicability 검증, 모바일 HTML/API 흐름을 포함합니다.

개발 중 모바일 렌더링 확인용 Chromium은 별도 이미지로만 사용합니다.

```bash
docker compose run --rm ui screenshot --output data/debug/mobile.png --json
```

검수용 스크린샷은 확인 후 삭제합니다.

## 아직 하지 않는 것

- 건강보험 급여여부 실제 동기화
  - 심평원 약가기준정보 API의 별도 활용신청 필요
- 생산·수입·공급중단/공급부족 실제 동기화
  - 관련 식약처 API의 별도 활용신청 필요
- 복수 기준·적응증별 기준·환산 불가능 단위인 용량주의 항목의 자동 확정 판정
- 체중·적응증·신장/간 기능·알레르기 등 추가 임상정보를 이용한 구조화 판정
- 로그인/클라우드 동기화/다기기 공유
- 네이티브 Android UI

## Android 빌드·실행과 OCR 연구 경계

제품 웹 UI와 Android APK에는 로컬 사진 인식 진입점과 승인된 on-device OCR detector/recognizer 런타임이 포함됩니다.
이미지와 OCR 원문은 서버 API나 제품 도메인 경계를 통과하지 않습니다. 현재 rule-based document parser와 중간 OCR
review UI는 없으며, learned parser runtime도 아직 연결되지 않았으므로 detector/recognizer가 구조화 약 행을 가장하지 않고
parser unavailable 상태를 명시적으로 반환합니다.

learned parser는 `browser_ocr/document_parsing/contract.py`가 정의하는 구조의 medication row를 출력합니다. 각 행은 `product_query`, 정규화 가능한
medication draft, 명시적 uncertainty code만 가지며 canonical 제품 identity를 확정하지 않습니다. `product_query`는 별도 OCR
검색 모드 없이 일반 제품 검색으로 바로 전달되고, 선택된 제품과 draft/uncertainty는 최종 복용정보 편집 화면에서 한 번만
확인·수정한 뒤 저장합니다. 이미지 URI, 파일 경로, OCR 원문 같은 raw source artifact는 이 구조화 경계를 통과하지 않습니다.

OCR 모델 연구·평가 코드는 제품과 독립된 `browser_ocr` 디렉터리에 유지합니다. 모델 원본 URL과 SHA-256은
`model-manifest.json`에 고정되고, 합성 corpus는 실제 research Worker를 Chromium에서 실행해 문자 오류율,
중요/숫자 토큰 재현율과 layout metric을 평가합니다. 기본 corpus에는 환자 데이터가 없습니다.

```bash
docker compose run --rm ocr-eval
```

연구용 runtime export는 별도로 생성할 수 있고, Android/로컬 개발 웹 빌드는 승인된 on-device OCR runtime만 패키징합니다.

```bash
docker build -f browser_ocr/Dockerfile --target runtime \
  --output type=local,dest=/tmp/medicine-ocr-runtime .
```

Android 앱은 WebView를 UI 셸로 사용하지만 외부 웹 서버에는 연결하지 않습니다. 정적 UI는 AndroidX WebKit의
`https://appassets.androidplatform.net` 로컬 asset origin에서 제공하고, 앱의 `/api/...` 호출은
`MedicineNative` 브리지를 통해 APK의 Rust `MedicineEngine`을 직접 호출합니다. WebView의 다른
HTTP/HTTPS 요청은 차단합니다. Android manifest의 `INTERNET` 권한은 native reference downloader에만 사용됩니다.

배포용 reference DB는 검증된 `canonical.sqlite`에서 런타임 테이블과 view만 추린 `mobile.sqlite`입니다.
Android와 standalone development web은 같은 signed release protocol, Rust verifier/lifecycle policy, trust manifest를
사용합니다. standalone web은 기본적으로 `data/reference/state.v1`과 content-addressed
`data/reference/mobile-<sha256>.sqlite` LKG를 관리합니다. 검증된 LKG가 없으면 signed `latest.json`을 확인하고 full
gzip snapshot을 Range-resume 가능한 checkpoint로 내려받아 artifact SHA-256/크기와 SQLite/runtime identity를
검증한 뒤 원자적으로 설치합니다. 이후 signed update는 direct patch가 있으면 우선 사용하고 실패하면 signed full로
fallback하며, 검증된 candidate는 pending으로 stage되어 다음 시작에 활성화됩니다. 유효한 LKG가 있으면 update endpoint가
일시적으로 사용할 수 없어도 LKG로 정상 시작합니다. contract retirement/rollback/identity conflict는 fail-closed합니다.

Agent Control CLI는 로컬 개발 편의를 위해 여전히 명시적인 `data/db/mobile.sqlite` 파일을 직접 사용합니다. standalone
web에서 `MEDICINE_CANONICAL_DB` 또는 `--canonical-db`를 명시하면 signed store를 우회하는 테스트용 DB override가 됩니다.
어느 경로도 `canonical.sqlite`로 자동 fallback하지 않습니다. reference DB는 읽기 전용으로 유지하고 개인 기록은 별도의
`personal.sqlite`에 저장합니다.

Docker에서 Android 단위 테스트와 debug APK 빌드를 실행합니다. Android 빌드 자체는 canonical/mobile DB를 생성하지 않습니다.

```bash
docker compose run --rm android
```

APK는 `android/app/build/outputs/apk/debug/app-debug.apk`에 생성됩니다. 설치 후에는 PC, LAN, loopback 서버나
별도 웹 서버 없이 동작합니다. 최초 bootstrap 완료 후에는 reference LKG를 로컬에서 사용합니다. 현재 Gradle 설정은
개인 기기 우선으로 `arm64-v8a`만 패키징합니다.

release 변형은 배포 실수 방지를 위해 서명키와 버전을 명시하지 않으면 빌드하지 않습니다. 서명키 파일은 저장소에
두지 말고 컨테이너에 read-only로 마운트합니다. 아래 환경변수의 비밀번호 값은 shell history나 문서에 기록하지 않습니다.

```bash
export MEDICINE_ANDROID_VERSION_CODE="$(sed -n 's/^versionCode=//p' android/release.properties)"
export MEDICINE_ANDROID_VERSION_NAME="$(sed -n 's/^versionName=//p' android/release.properties)"
export MEDICINE_ANDROID_KEY_ALIAS='...'
export ANDROID_RELEASE_KEYSTORE=/absolute/path/to/release.jks

printf 'Android keystore password: '
read -r -s MEDICINE_ANDROID_KEYSTORE_PASSWORD
printf '\n'
export MEDICINE_ANDROID_KEYSTORE_PASSWORD

printf 'Android key password: '
read -r -s MEDICINE_ANDROID_KEY_PASSWORD
printf '\n'
export MEDICINE_ANDROID_KEY_PASSWORD

docker compose -p medicine_android_release build android
docker compose -p medicine_android_release run --rm \
  -v "$ANDROID_RELEASE_KEYSTORE:/run/secrets/medicine-release.jks:ro" \
  -e MEDICINE_ANDROID_VERSION_CODE \
  -e MEDICINE_ANDROID_VERSION_NAME \
  -e MEDICINE_ANDROID_KEYSTORE_PASSWORD \
  -e MEDICINE_ANDROID_KEY_ALIAS \
  -e MEDICINE_ANDROID_KEY_PASSWORD \
  -e MEDICINE_ANDROID_KEYSTORE_PATH=/run/secrets/medicine-release.jks \
  android sh /workspace/scripts/android_release_build.sh
```

이 release gate는 Android 단위 테스트와 release lint를 실행한 뒤 signed APK를 만들고, APK의 versionCode/versionName과
서명을 다시 읽어 요청한 값과 일치하는지 확인합니다. Gradle 의존성은 `android/app/gradle.lockfile`로 고정하고
`android/gradle/verification-metadata.xml`의 SHA-256으로 검증합니다. Android command-line tools 다운로드 역시
Docker 이미지 빌드 중 고정 SHA-256을 확인합니다. 의존성을 의도적으로 변경할 때만 고정된 Android Docker 환경에서
`--write-locks --write-verification-metadata sha256`로 두 파일을 함께 갱신하고 변경 내용을 검토합니다.

개발자용 GitHub 배포는 COWI와 같은 exact-SHA handoff를 사용합니다. 태그 전에 **Android Developer Release Check**
Actions workflow가 native GitHub-hosted Ubuntu runner에서 별도 signing secret이나 Docker 없이 debug-signed APK를 한 번 빌드·검증하고 해당 workflow run에 묶어 보관하며,
같은 commit에 `vX.Y.Z` 태그를 push하면 **Android Developer Release** workflow가 검증된 APK를 재빌드하지 않고
GitHub Release에 게시합니다. 이 경로는 정식 release signing/Play 배포와 별개이며, 버전 변경과 태그 순서는
`docs/android-releasing.md`를 따릅니다.

데이터 이용조건 검토는 제품 배포 전 별도 release 절차로 남아 있습니다.

## 의료 정보 주의

DUR 결과는 금기·주의 여부를 확인하기 위한 안전 신호입니다. 앱 결과만을 근거로 처방약을
임의로 중단하거나 변경하지 말고 의사 또는 약사와 확인해야 합니다.

## 데이터 사용 주의

제품화·배포 전에 MFDS 및 함께 사용하는 외부 substance identity 데이터셋의 최신 이용조건과
재배포 조건을 확인해야 합니다.
