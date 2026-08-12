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
  - 식약처 전체 허가 의약품 `catalog.sqlite`를 필수 검색 소스로 사용
  - 카탈로그가 없거나 아직 동기화 중이면 사용 불가 상태를 명시하며 DUR 제품목록으로 fallback하지 않음
  - 기본 검색은 허가상태 `정상` 품목만 표시
  - `취소·취하·만료 품목도 검색` 옵션으로 과거 허가 이력 조회 가능
  - 허가상태를 `active / expired / withdrawn / business_closed / canceled`로 정규화
- 약 추가 전 자동 DUR 확인
  - 식약처 허가품목의 `ITEM_SEQ`를 canonical 제품 식별키로 사용
  - 상세 DUR 제품코드는 현재 식약처 EDI를 우선 사용하고, EDI가 상세 DUR과 연결되지 않으면 심평원 약가마스터의 `ITEM_SEQ → 제품코드` exact 매핑을 사용
  - 공식 코드 매핑이 없을 때만 제품명+성분명 정규화 fallback을 사용하며, 공식 매핑이 복수이면 추측하지 않고 판정 제한
  - 제품코드가 연결되면 제품 단위 상세 DUR 규칙을 우선 확인
  - 제품코드가 없어도 `ITEM_SEQ`로 확인된 첨가제주의·DUR 품목 유형·서방정분할주의를 놓치지 않음
  - 제품코드가 없더라도 식약처 성분명이 DUR 성분에 정확히 연결되면 성분 단위 DUR을 함께 확인
  - 다중 EDI는 실제 DUR 제품과 단일하게 연결되는 경우에만 제품 단위 상세 판정에 사용
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
  - 투여기간 기준과 처방 일수를 자동 비교
  - 제품 기준으로 확정할 수 없는 투여기간은 성분 기준이 제형까지 단일하게 맞을 때만 보완 판정
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
  - WebView UI, Python 앱 코어, 검증된 DUR/허가 카탈로그 snapshot, OCR ONNX/WASM 자산을 APK에 함께 포함
  - Android 앱은 `INTERNET` 권한 없이 앱 내부 HTTPS asset origin과 네이티브 Python 브리지만 사용
  - 개인 복약 DB는 Android Keystore AES-GCM 키로 요청 사이에 암호화해 보관하고, SQLite 처리 중에만 앱 전용 저장소의 임시 평문 DB를 사용
  - 비정상 종료로 임시 평문 DB가 남으면 다음 시작 시 이를 최신 상태로 복구·checkpoint한 뒤 즉시 다시 암호화
  - 배포 reference DB는 읽기 전용으로 분리

DUR 결과가 없다는 것은 안전하다는 뜻이 아닙니다. 앱은 제품·성분 매핑, 데이터셋 검증 상태,
지원하는 프로필 범위를 함께 기록하고, 지원 범위의 검사가 모두 명확하게 끝난 경우에만
`DUR 주의사항 없음`을 표시하며 `DUR 자동 확인 범위 제한`과 구분합니다. 현재 자동 판정에는 알레르기, 신장·간 기능, 체중·적응증, 등록하지 않은
일반약·건강기능식품 등의 임상정보가 포함되지 않습니다.

## DB 구조

### `data/db/dur.sqlite`

공개 DUR 규칙 DB입니다. 앱에서는 읽기 전용으로 엽니다.

- `source_files`: 원본 provenance, SHA-256, 행 수
- `product_dur`: 제품 단위 DUR 규칙
- `ingredient_dur`: 성분 단위 DUR 규칙
- `product_catalog`: 제품 단위 상세 DUR 규칙에서 파생한 정규화 제품코드 카탈로그
- `product_item_flags`: 식약처 `ITEM_SEQ` 기준 DUR 품목 유형·첨가제주의·서방정분할주의
- `product_code_bridge`: 심평원 약가마스터의 `ITEM_SEQ(품목기준코드) → 제품코드(개정후)` exact 매핑

현재 로컬 DB 기준:

- 제품 단위 상세 DUR: 558,637행
- 성분 단위 DUR: 4,172행
- `ITEM_SEQ` 제품 플래그: 43,295행
- 심평원 제품코드 bridge: 22,308개 관계
- 제품코드 카탈로그: 23,131개
- 검증 원본 데이터 파일: 18개

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

### `data/db/catalog.sqlite`

식약처 의약품 제품 허가정보 API에서 동기화한 전체 제품 카탈로그입니다. 식약처 품목기준코드
(`item_seq`)를 앱 내부 canonical 제품 참조키로 사용합니다. 상세 DUR 제품코드는 식약처 EDI를 먼저
확인하고, 그 코드가 상세 DUR에 없으면 심평원 약가마스터의 동일 `item_seq` exact 매핑으로 보완합니다.

EDI/DUR 연결이 되지 않은 제품도 검색·복약 등록은 가능하지만, 개인별 DUR 자동 판정 범위가
제한될 수 있다는 안내를 표시합니다. 이 경우 식약처 성분명이 DUR 성분 기준에 정확히 연결되면
성분 단위 규칙을 보완적으로 확인하지만 fuzzy matching으로 성분을 추측하지 않습니다. 전체
카탈로그가 없으면 약 검색은 실패하며 DUR 제품목록으로 대체하지 않습니다.

허가상태는 식약처 원본의 `CANCEL_NAME`을 보존하면서 다음 코드로 정규화합니다.

- `정상` → `active`
- `유효기간만료` → `expired`
- `취하` → `withdrawn`
- `폐업` → `business_closed`
- `행정(취소)` / `취소` → `canceled`

현재 로컬 카탈로그 기준:

- 전체 허가 이력: 42,962개
- 허가상태 정상: 35,228개
- 유효기간만료: 4,612개
- 취하: 2,817개
- 폐업: 257개
- 취소: 48개

### `data/db/canonical.sqlite` (병렬 실험 DB)

기존 `dur.sqlite`/`catalog.sqlite` 런타임을 변경하지 않고, 세 공식 원본 계열만으로 다시 만든
canonical DB입니다. 현재 앱 평가기는 아직 이 DB를 사용하지 않습니다.

- `mfds_permit_api`: 식약처 허가제품 API. `ITEM_SEQ`를 제품 중심키로 사용
- `mfds_dur_item_api`: 식약처 DUR 품목 API 9개 endpoint. 상세 규칙은 `ITEM_SEQ`, 병용금기는
  `MIXTURE_ITEM_SEQ`를 직접 보존
- `kids_mfds_xlsx`: 최신 성분/기준 XLSX 8종. 임부등급, 연령, 용량, 기간, 효능군, 수유부주의 등을 보존

상세 DUR API의 `INGR_CODE`/`INGR_ENG_NAME`과 병용 상대 성분 identity를 함께 보존하고,
동일 카테고리의 XLSX 성분 기준과 연결한 `product_rule_criteria` view를 제공합니다. 영문 성분명이
직접 일치하면 `english_exact`, 공식 MFDS 성분코드 identity로 이어지면 `mfds_ingredient_code` 근거로
연결합니다. 현재 소스에서 확인된 카테고리별 코드 차이는 Ketorolac/Naproxen/Piroxicam/Mizolastine
4개만 link-time equivalence로 명시하며, 원본 `INGR_CODE`는 변경하지 않습니다. 이후 다른 성분에서
복수 코드 때문에 fallback이 막히면 자동 추정하지 않고 `stats`에 성분명/후보 코드를 노출하고
`verify`를 실패시킵니다. 임의의 salt stripping이나 legacy alias는 사용하지 않습니다.

API 원본은 `data/canonical/raw/*.jsonl`에 페이지 순서대로 보존하고 SHA-256 metadata를 함께
저장합니다. DB의 각 행은 `source_dataset_key + source_row`로 원본 행을 추적하며, DB 재조립 시
원본 JSONL 해시가 metadata와 다르면 실패합니다. 기존 KIDS 제품코드 CSV와 HIRA 제품코드 bridge는
이 canonical DB의 안전성 원본으로 사용하지 않습니다.

현재 실데이터 빌드 기준(2026-08-12):

- 허가제품 42,956개 / 정상 35,239개
- `ITEM_SEQ` 상세 제품규칙 834,286행
- 품목 플래그 43,295행
- XLSX 성분/기준 규칙 4,172행
- 제품 DUR ↔ XLSX 기준 링크 782,146행 / 연결된 제품규칙 782,044행
- 링크 방식: 영문명 직접 일치 153,808행 / MFDS 성분코드 연결 628,338행
- source snapshot 18개 = 허가 API 1 + DUR API 9 + XLSX 8
- 상세/상대/플래그 ITEM_SEQ orphan 0건
- SQLite 약 633.7MB

전체 최신 API를 다시 받고 원자적으로 재구축:

```bash
docker compose run --rm canonical rebuild --json
```

이미 받은 API snapshot만 사용해 네트워크 없이 DB 재조립:

```bash
docker compose run --rm canonical build --json
```

검증/통계:

```bash
docker compose run --rm canonical verify --json
docker compose run --rm canonical stats --json
```

특정 제품의 결합된 XLSX 기준 조회:

```bash
docker compose run --rm canonical criteria --item-seq 198600630 --json
```

## 식약처 전체 의약품 카탈로그 동기화

공공데이터포털에서 발급한 서비스키가 필요합니다. 키는 저장소에 넣지 말고 환경변수로
전달합니다.

```bash
export DATA_GO_KR_SERVICE_KEY='발급받은_서비스키'
docker compose run --rm catalog sync --json
```

동기화는 `catalog.sqlite.tmp`에 페이지 단위로 저장하고 checkpoint를 남깁니다. 중간 실패 시
완성 전 DB를 운영 DB로 취급하지 않으며, 같은 조건으로 다시 실행하면 checkpoint에서 이어
받습니다. 전체 수집과 SQLite 무결성 검사를 통과한 뒤에만 `catalog.sqlite`로 원자 교체합니다.

상태 확인:

```bash
docker compose run --rm catalog stats --json
```

기존 카탈로그가 구 스키마라면 보존된 `raw_json`으로 재다운로드 없이 원자적으로 업그레이드할 수 있습니다.

```bash
docker compose run --rm catalog upgrade --json
```

급여·공급 상태는 허가상태와 별도 데이터 축으로 관리합니다. 현재 서비스키의 추가 API 활용권한을
확인하려면 다음 명령을 사용합니다. 키 값 자체는 출력하지 않습니다.

```bash
docker compose run --rm catalog status-sources --json
```

확인 대상은 건강보험심사평가원 약가기준정보, 식약처 생산·수입·공급중단, 식약처 공급부족
API입니다. 각 API는 공공데이터포털에서 별도 활용신청이 필요할 수 있습니다.

## DUR 데이터 다시 만들기

```bash
# 공공데이터포털 키로 DUR 품목정보 + 서방정분할주의 최신 snapshot 동기화
docker compose run --rm dur sync-product-items --json

# 심평원 공개 약가마스터에서 ITEM_SEQ → 제품코드 exact bridge 동기화
docker compose run --rm dur sync-product-code-bridge --json

# 기존 DUR CSV/XLSX와 위 snapshot을 하나의 검증 DB로 빌드
docker compose run --rm dur build --json
docker compose run --rm dur stats --json
docker compose run --rm dur verify --json
docker compose run --rm catalog ingredient-aliases --write --json
docker compose run --rm dur mobile-build --json
```

원본 파일은 `data/raw/`, `data/kids/`에 보존하고 Git에는 넣지 않습니다. DUR 빌드도 임시 DB에서
전체 import/검증을 끝낸 뒤 최종 DB를 원자 교체합니다. `dur verify`는 배포 전 release gate로
18개 필수 원본의 존재·헤더·실제 파일 SHA-256 일치, 실제 import 행 수, SQLite 무결성, 성분 고시 기준일과
제품 스냅샷 생성 시점을 확인하고 결정적인 `dataset_id`를 출력합니다. `sync-product-items`는 식약처
`DUR품목정보`와 `서방정분할주의` OpenAPI를 checkpoint 가능한 JSONL snapshot으로 저장하고,
`sync-product-code-bridge`는 심평원 공개 약가마스터 CSV를 정상 공개 다운로드 절차로 원자 갱신합니다. 이 식별자는 처방 안전성
평가와 변경 이력에 저장됩니다.

`catalog ingredient-aliases --write`는 현재 식약처 카탈로그와 DUR 데이터에서 증명되거나 개별
검토된 성분명 관계를 `catalog.sqlite`의 alias 테이블에 저장합니다. 정확한 EDI 제품 연결,
식약처에서 단일성분으로 확인되는 동일 DUR 제품·성분코드 표기, 안전하게 소거 가능한 복합성분
대응을 자동 근거로 사용합니다. 염·활성형·철자 차이는 일반 규칙으로 제거하지 않고 개별 검토 목록에
있는 항목도 현재 DUR 제품 규칙·DUR 제품 카탈로그 또는 활성 exact-EDI에서 그 표기가 실제 관찰되고
목표 DUR 성분이 모두 존재할 때만 materialize합니다. 하나의 제품 성분에 서로 다른 DUR 규칙
정체성이 함께 필요한 검토 항목은
`ingredient_multi_aliases`에 여러 target을 보존해 어느 한쪽 규칙을 버리지 않습니다. 검토된 원천
오류가 현재 데이터와 정확히 일치하면 제품 단위 DUR 연결은 유지하되 성분 단위 판정은 fail-closed
합니다. 모든 alias에는 현재 DUR `dataset_id`와 provenance를 함께 저장하므로 데이터셋이 바뀐 뒤
재생성하지 않은 매핑은 런타임에서 사용되지 않습니다.

`dur mobile-build`는 검증된 `dur.sqlite`와 `catalog.sqlite`에서 Android 런타임에 필요한 컬럼과
인덱스만 보존한 `data/db/mobile.sqlite`와 SHA-256 manifest를 만듭니다. DUR 규칙 행과 원본
provenance, `ITEM_SEQ` 제품 플래그, 심평원 제품코드 bridge 및 검증된 ingredient alias를 유지하며 `dataset_id`도 원본 DUR DB와 같아야 빌드가
성공합니다. Android 빌드는 alias 재생성과 mobile DB 생성을 다시 실행하므로 검증에 실패한
데이터나 오래된 alias는 APK에 패키징되지 않습니다.

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

기존 DUR 저수준 검색 CLI도 유지합니다.

```bash
docker compose run --rm dur search acemetacin --limit 20 --json
```

## 테스트 / 모바일 렌더링

```bash
docker compose run --rm --build test
```

테스트는 개인 DB migration, 여러 사람 관리, 구조화 처방, 정량 용량·기간 판정, 경고 확인 토큰,
중복 등록 방지, 처방 revision·이력, 날짜별 복용 인스턴스 멱등성, 수정 후 완료 이력 보존, PRN,
복용 기록, 전체 허가품목/허가상태 검색, 병용금기, 연령/임부/노인주의, 효능군 중복, DUR import 정규화,
MFDS 카탈로그 동기화·허가상태 migration, 모바일 HTML/API 흐름을 포함합니다.

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

## Android / OCR 빌드·실행

PC·모바일 브라우저와 Android WebView 모두 같은 브라우저 OCR 구현을 사용합니다.
전용 Worker가 PP-OCRv5 모바일 탐지·한국어 인식 ONNX 모델을 ONNX Runtime WebAssembly CPU
backend로 직접 실행하며 PaddleOCR.js, OpenCV, ML Kit은 사용하지 않습니다. 사진과 인식 원문은
Worker 메모리 안에서만 처리하고 서버·DB·웹 저장소로 보내지 않으며, 구조화된 복용 힌트만 기존
사용자 확인 흐름으로 넘깁니다. Android에서는 Worker와 ONNX/WASM 모델도 APK asset에 포함되어
외부 다운로드가 필요하지 않습니다. 브라우저 파서 테스트와 헤드리스 확인 CLI는 다음과 같이 실행합니다.

```bash
docker compose run --rm browser-test
printf '약명: 타이레놀정\n1정 1일 2회 7일\n오전 8시 오후 8시\n' \
  | docker compose run -T --rm browser-ocr --input - --json
```

Android 앱은 WebView와 시스템 사진 선택기를 UI 셸로 사용하지만 외부 웹 서버에는 연결하지 않습니다.
정적 UI와 OCR 자산은 AndroidX WebKit의 `https://appassets.androidplatform.net` 로컬 asset origin에서
제공하고, 앱의 `/api/...` 호출은 `MedicineNative` 브리지를 통해 APK에 포함된 Python `MedicationApp`
코어를 직접 호출합니다. WebView의 다른 HTTP/HTTPS 요청은 차단하며 Android manifest에는
`INTERNET` 권한이 없습니다.

배포용 reference DB는 원본 `dur.sqlite` + `catalog.sqlite`를 compact한 `mobile.sqlite`입니다.
APK에는 압축된 asset으로 들어가며 첫 실행 때 manifest의 크기와 SHA-256을 확인하면서 앱 전용
저장소에 원자적으로 설치합니다. reference DB는 이후 읽기 전용으로 사용하고 개인 기록은 별도의
`personal.sqlite`에 저장합니다. 데이터 snapshot이 바뀌면 새 해시 이름으로 설치한 뒤 이전 reference
파일을 정리합니다.

Docker에서 데이터 release gate, compact DB 생성, Android 단위 테스트와 debug APK 빌드를 한 번에 실행합니다.

```bash
docker compose run --rm android
```

APK는 `android/app/build/outputs/apk/debug/app-debug.apk`에 생성됩니다. 설치 후에는 PC, LAN,
loopback 서버나 인터넷 연결 없이 약 검색·DUR 판정·복약 기록·OCR을 실행할 수 있습니다.
현재 Gradle 설정은 개인 기기 우선으로 `arm64-v8a`만 패키징합니다.

release 변형도 동일한 온디바이스 구조로 빌드할 수 있지만 실제 배포 전에 Android 서명키와
release signing configuration을 별도로 설정해야 합니다. 데이터 이용조건 검토 역시 제품 배포 전
별도 release 절차로 남아 있습니다.

## 의료 정보 주의

DUR 결과는 금기·주의 여부를 확인하기 위한 안전 신호입니다. 앱 결과만을 근거로 처방약을
임의로 중단하거나 변경하지 말고 의사 또는 약사와 확인해야 합니다.

## 데이터 사용 주의

KIDS DUR 페이지는 비상업적 연구·교육 목적 사용을 안내하며, 상업적 활용에는 별도 승인이
필요하다고 고지합니다. 제품화 전에 각 데이터셋의 이용조건을 다시 확인해야 합니다.
