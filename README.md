# medicine

한국 DUR 공개 데이터를 기반으로 여러 사람의 복약을 관리하는 모바일 우선 로컬 웹앱입니다.
현재는 텍스트로 의약품을 검색해 구조화된 처방 정보로 등록하고, 하루 단위 복용 계획과 실제
복용 기록을 관리합니다. 최종 목표는 같은 API/도메인 구조를 사용하는 Android 앱과 처방전
사진 입력입니다.

## 실행

Docker만 있으면 됩니다.

```bash
cd ~/dev/medicine
docker compose up -d --build web
```

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
  - `canonical.sqlite`의 식약처 허가제품을 `ITEM_SEQ` 기준으로 검색
  - 기본 검색은 허가상태 `정상` 품목만 표시
  - `취소·취하·만료 품목도 검색` 옵션으로 과거 허가 이력 조회 가능
  - 허가상태를 `active / expired / withdrawn / business_closed / canceled`로 정규화
  - EDI는 검색·표시용 보조 식별자로만 사용하고 안전성 identity로 사용하지 않음
- 약 추가 전 자동 DUR 확인
  - 식약처 허가품목의 `ITEM_SEQ`를 canonical 제품 식별키로 사용
  - MFDS ITEM_SEQ 제품규칙과 최신 XLSX 상세기준을 연결한 canonical product criterion을 직접 평가
  - 공식 제품규칙은 있으나 상세기준 연결이 확정되지 않은 경우 안전으로 추측하지 않고 `unknown`으로 표시
  - 수유부주의는 canonical product→ingredient criterion applicability를 사용하며 미확정 범위는 `unknown`으로 표시
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
  - 첨가제주의
  - 서방정분할주의
- 구조화된 처방 정보
  - 1회 복용량 / 단위
  - 1일 횟수
  - 복용 시간
  - 식전·식후 등 식사 관계
  - 경구·외용·흡입 등 투여 경로
  - 처방 일수와 시작·종료일
  - 필요시 복용(PRN)
- 입력 처방의 정량 DUR 확인
  - canonical 제품 기준 투여기간과 처방 일수를 자동 비교
  - 제품규칙과 상세기준 연결을 확정할 수 없는 경우 보완 추측 없이 판정 불가로 표시
  - 단일 기준과 단위가 명확한 용량주의 항목은 1일 복용량을 자동 비교
  - 복수·조건부 기준이나 환산할 수 없는 단위는 안전으로 간주하지 않고 판정 불가 사유 표시
  - 금기·주의, 정량 기준 초과 또는 중요한 커버리지 제한은 경고를 먼저 표시하고 확인 후 등록 허용
  - 경고 확인 토큰은 처방 내용, 당시 DUR 데이터셋과 실제 평가 결과에 묶여 안전성 맥락이 바뀌면 재확인 필요
- 처방 수정과 변경 이력
  - revision 기반 동시 수정 충돌 방지
  - 수정 시 미래의 미완료 일정만 교체하고 과거 완료·건너뜀 기록 보존
  - 등록·수정·종료 당시 처방, DUR 판정, 평가기 버전과 데이터셋 식별자 스냅샷 보존
- 오늘 복용 계획 자동 생성
  - 같은 날짜를 여러 번 조회해도 동일 복용 인스턴스 유지
  - 복용 완료 / 건너뜀 상태 추적
  - PRN 약은 고정 일정과 분리
- 복용 종료 처리 / 최근 복용 기록 조회
- JSON API와 동일 코어를 사용하는 headless CLI
- 서버 없는 Android 패키징
  - WebView UI, Python 앱 코어, 검증된 canonical reference snapshot을 APK에 함께 포함
  - Android 앱은 `INTERNET` 권한 없이 앱 내부 HTTPS asset origin과 네이티브 Python 브리지만 사용
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

### `data/db/canonical.sqlite` (앱 기준 DB)

앱과 Android가 사용하는 단일 read-only 의약품/DUR 기준 DB입니다. 세 공식 원본 계열을 직접
보존하고 `ITEM_SEQ`를 제품 중심키로 사용합니다.

- `mfds_permit_api`: 식약처 허가제품 API
- `mfds_dur_item_api`: 식약처 DUR 품목 API 9개 endpoint
- `kids_mfds_xlsx`: 최신 성분/기준 XLSX 8종

제품 DUR은 `product_rules -> product_criterion_links -> ingredient_rules`로 연결하며
`product_rule_criteria` view로 조회합니다. 수유부주의처럼 MFDS ITEM_SEQ 제품규칙 계열이 없는
성분 기준은 `product_ingredient_criterion_links`에 별도 materialize합니다. 확정할 수 없는 성분 적용범위는
`product_ingredient_criterion_unresolved`에 남기고 앱에서 `unknown`으로 표시합니다. 염·수화물·용매화물 등
서로 다른 precise substance를 일반 문자열 규칙으로 합치지 않습니다.

앱 런타임은 EDI/HIRA/제품명 fallback이나 ingredient alias graph를 사용하지 않습니다. canonical에 존재하는
제품에 해당 DUR 규칙이 없으면 해당 항목은 clear/not-applicable이고, 공식 제품규칙은 있지만 XLSX 상세기준
연결이 안 된 경우에만 fail-closed `unknown`으로 남습니다. `product_code` API/개인 DB 호환 필드는 신규 등록에서
ITEM_SEQ를 저장하며, 기존 개인 DB 재평가는 `catalog_item_seq`를 authoritative reference로 사용합니다.

현재 실데이터 빌드 기준(2026-08-13):

- schema version 7
- 허가제품 42,956개 / 정상 35,239개
- `ITEM_SEQ` 상세 제품규칙 834,286행
- 품목 플래그 43,295행
- XLSX 성분/기준 규칙 4,172행
- 제품 DUR ↔ XLSX 기준 링크 1,080,696행 / 연결된 제품규칙 829,200행 / 미연결 5,086행
- 수유부주의 등 제품↔성분기준 applicability 665행 / 활성 수유부주의 positive 제품 612개
- 활성 unresolved 성분 applicability 94행
- source snapshot 18개 = 허가 API 1 + DUR API 9 + XLSX 8
- blocking product-link identity ambiguity 0건

API 원본은 `data/canonical/raw/*.jsonl`, substance 원본은 `data/canonical/substances/`에 보존합니다.
앱용 완전한 DB는 source → substance → DUR bridge → product/applicability 순서를 보장하는 integrated build로
재생성합니다.

```bash
# 최신 MFDS API를 동기화한 뒤 canonical + substance DB 전체 재구축
docker compose run --rm canonical integrated-rebuild --json

# 이미 보존된 snapshot만 사용해 네트워크 없이 전체 재조립
docker compose run --rm canonical integrated-build --json

# release gate
docker compose run --rm canonical verify --json
docker compose run --rm canonical substance-verify --json
docker compose run --rm canonical stats --json

# 특정 ITEM_SEQ의 제품 DUR / 성분-only 기준 확인
docker compose run --rm canonical criteria --item-seq 198600630 --json
docker compose run --rm canonical ingredient-criteria --item-seq 198600630 --json

# Android용 compact canonical snapshot 생성
docker compose run --rm canonical mobile-build --json
```

`canonical verify`가 실패하거나 runtime manifest에 unresolved product-link ambiguity가 남아 있으면 앱은
데이터셋을 verified로 취급하지 않습니다. Android 빌드는 `canonical mobile-build`를 먼저 실행해
`data/db/mobile.sqlite`와 SHA-256 manifest를 만든 뒤 동일한 Python core를 APK에 패키징합니다.

## 앱 제어 CLI

```bash
# 사람 목록
docker compose run --rm app people --json

# 프로필 추가
docker compose run --rm app person-add \
  --name "나" \
  --birth-date 1990-01-01 \
  --sex female \
  --pregnancy-status not_pregnant \
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

현재 제품 입력은 **수기 전용**입니다. 사용자는 식약처 허가 의약품을 검색해 제품을 선택하고,
1회량·1일 횟수·복용 시간·식사 관계·투여 경로·처방 일수 등을 직접 확인해 입력합니다.
제품 웹 UI, 웹/Android API, Android APK에는 OCR 스캔 진입점, OCR review/batch API, ONNX/WASM OCR
런타임 또는 OCR 모델 자산을 포함하지 않습니다.

향후 파인튜닝된 OCR을 다시 연결할 때는 `medicine_app.intake`의 구조화 draft 계약을 경계로 사용합니다.
이 계약은 하나 이상의 `product_query`와 정규화 가능한 medication draft, 명시적 uncertainty code만 허용합니다.
canonical 제품 identity는 provider가 확정하지 않고 이후 제품 UI에서 사용자가 확인·선택합니다. 이미지 URI, 파일 경로,
OCR 원문 같은 raw source artifact는 제품 경계를 통과시키지 않습니다. 현재 이 계약에 등록된 provider나 사용자-facing route는 없습니다.

OCR 모델 연구·평가 코드는 제품과 독립된 `browser_ocr` 디렉터리에 유지합니다. 모델 원본 URL과 SHA-256은
`model-manifest.json`에 고정되고, 합성 corpus는 실제 research Worker를 Chromium에서 실행해 문자 오류율,
중요/숫자 토큰 재현율과 layout metric을 평가합니다. 기본 corpus에는 환자 데이터가 없습니다.

```bash
docker compose run --rm ocr-eval
```

연구용 runtime export도 제품 Docker/Android 빌드와 분리되어 있습니다. 필요할 때만 별도로 생성할 수 있습니다.

```bash
docker build -f browser_ocr/Dockerfile --target runtime \
  --output type=local,dest=/tmp/medicine-ocr-runtime .
```

Android 앱은 WebView를 UI 셸로 사용하지만 외부 웹 서버에는 연결하지 않습니다. 정적 UI는 AndroidX WebKit의
`https://appassets.androidplatform.net` 로컬 asset origin에서 제공하고, 앱의 `/api/...` 호출은
`MedicineNative` 브리지를 통해 APK에 포함된 Python `MedicationApp` 코어를 직접 호출합니다. WebView의 다른
HTTP/HTTPS 요청은 차단하며 Android manifest에는 `INTERNET` 권한이 없습니다.

배포용 reference DB는 검증된 `canonical.sqlite`에서 런타임 테이블과 view만 추린 `mobile.sqlite`입니다.
APK에는 압축된 asset으로 들어가며 첫 실행 때 manifest의 크기와 SHA-256을 확인하면서 앱 전용 저장소에
원자적으로 설치합니다. reference DB는 이후 읽기 전용으로 사용하고 개인 기록은 별도의 `personal.sqlite`에 저장합니다.

Docker에서 데이터 release gate, compact DB 생성, Android 단위 테스트와 debug APK 빌드를 한 번에 실행합니다.

```bash
docker compose run --rm android
```

APK는 `android/app/build/outputs/apk/debug/app-debug.apk`에 생성됩니다. 설치 후에는 PC, LAN, loopback 서버나
인터넷 연결 없이 약 검색·DUR 판정·복약 기록을 실행할 수 있습니다. 현재 Gradle 설정은 개인 기기 우선으로
`arm64-v8a`만 패키징합니다.

release 변형도 동일한 온디바이스 구조로 빌드할 수 있지만 실제 배포 전에 Android 서명키와 release signing
configuration을 별도로 설정해야 합니다. 데이터 이용조건 검토 역시 제품 배포 전 별도 release 절차로 남아 있습니다.

## 의료 정보 주의

DUR 결과는 금기·주의 여부를 확인하기 위한 안전 신호입니다. 앱 결과만을 근거로 처방약을
임의로 중단하거나 변경하지 말고 의사 또는 약사와 확인해야 합니다.

## 데이터 사용 주의

KIDS DUR 페이지는 비상업적 연구·교육 목적 사용을 안내하며, 상업적 활용에는 별도 승인이
필요하다고 고지합니다. 제품화 전에 각 데이터셋의 이용조건을 다시 확인해야 합니다.
