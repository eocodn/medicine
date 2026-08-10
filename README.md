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
  - 현재 복용약과의 병용금기
  - 특정연령대금기
  - 임부금기
  - 65세 이상 노인주의
  - 효능군중복주의
  - 용량주의/투여기간주의 대상 여부 안내
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
  - 단일 기준과 단위가 명확한 용량주의 항목은 1일 복용량을 자동 비교
  - 복수·조건부 기준이나 환산할 수 없는 단위는 안전으로 간주하지 않고 판정 불가 사유 표시
  - 기준 초과 시 경고를 먼저 표시하고, 동일 처방 경고를 확인한 경우에만 등록 허용
- 처방 수정과 변경 이력
  - revision 기반 동시 수정 충돌 방지
  - 수정 시 미래의 미완료 일정만 교체하고 과거 완료·건너뜀 기록 보존
  - 등록·수정·종료 당시 처방과 DUR 판정 스냅샷 보존
- 오늘 복용 계획 자동 생성
  - 같은 날짜를 여러 번 조회해도 동일 복용 인스턴스 유지
  - 복용 완료 / 건너뜀 상태 추적
  - PRN 약은 고정 일정과 분리
- 복용 종료 처리 / 최근 복용 기록 조회
- JSON API와 동일 코어를 사용하는 headless CLI

DUR 결과가 없다는 것은 안전하다는 뜻이 아닙니다. 앱은 `DUR 위험 정보가 발견되지 않음` 또는
`DUR 자동 확인 범위 제한`으로 구분해 표시합니다.

## DB 구조

### `data/db/dur.sqlite`

공개 DUR 규칙 DB입니다. 앱에서는 읽기 전용으로 엽니다.

- `source_files`: 원본 provenance, SHA-256, 행 수
- `product_dur`: 제품 단위 DUR 규칙
- `ingredient_dur`: 성분 단위 DUR 규칙
- `product_catalog`: DUR 기반 검색용 정규화 제품 카탈로그

현재 로컬 DB 기준:

- 제품 단위 DUR: 558,637행
- 성분 단위 DUR: 4,172행
- 총 DUR 규칙: 562,809행
- 검색용 제품: 23,131개
- 원본 데이터 파일: 15개

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
개인 DB는 Git에 포함하지 않습니다. 현재 로컬 버전에서는 암호화하지 않으므로 외부 공개 서버로
그대로 배포하면 안 됩니다. 웹 서비스도 기본적으로 `127.0.0.1`에만 바인딩합니다.

### `data/db/catalog.sqlite`

식약처 의약품 제품 허가정보 API에서 동기화한 전체 제품 카탈로그입니다. 제품 허가번호
(`item_seq`)를 앱 내부 참조키로 사용하고, EDI 코드가 있는 제품은 DUR 제품코드와 연결합니다.

EDI/DUR 연결이 되지 않은 제품도 검색·복약 등록은 가능하지만, 개인별 DUR 자동 판정 범위가
제한된다는 안내를 표시합니다. 전체 카탈로그가 없으면 약 검색은 실패하며 DUR 제품목록으로
대체하지 않습니다.

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
docker compose run --rm dur build --json
docker compose run --rm dur stats --json
```

원본 파일은 `data/raw/`, `data/kids/`에 보존하고 Git에는 넣지 않습니다. DUR 빌드도 임시 DB에서
전체 import/검증을 끝낸 뒤 최종 DB를 원자 교체합니다.

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

# 기준 초과 응답은 warning_token을 반환합니다. 내용을 확인한 뒤 위 med-add 명령을
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
- 수유 여부를 개인 프로필과 연결한 수유부주의 자동 판정
- 로그인/클라우드 동기화/다기기 공유
- 개인 DB 암호화
- 네이티브 Android UI

## Android / OCR 빌드·실행

PC·모바일 브라우저와 Android WebView 모두 자체 호스팅한 단일 OCR 경로를 사용합니다.
전용 Worker가 PP-OCRv5 모바일 탐지·한국어 인식 ONNX 모델을 ONNX Runtime WebAssembly CPU
backend로 직접 실행하며 PaddleOCR.js, OpenCV, ML Kit은 사용하지 않습니다. 사진과 인식 원문은
Worker 메모리 안에서만 처리하고 서버·DB·웹 저장소로 보내지 않으며, 구조화된 복용 힌트만 기존
사용자 확인 흐름으로 넘깁니다. 모델 자산은 첫 실행 때 같은 로컬 웹 origin에서 받고 브라우저
캐시에 저장될 수 있습니다. 브라우저 파서 테스트와 헤드리스 확인 CLI는 다음과 같이 실행합니다.

```bash
docker compose run --rm browser-test
printf '약명: 타이레놀정\n1정 1일 2회 7일\n오전 8시 오후 8시\n' \
  | docker compose run -T --rm browser-ocr --input - --json
```

Android 셸은 최소 WebView와 시스템 사진 선택기만 제공합니다. OCR은 WebView 안의 동일한
브라우저 Worker가 담당하므로 별도 네이티브 모델이나 메시지 브리지가 없습니다. WebView 탐색은
설정된 동일 origin으로 제한되며 HTTP cleartext는 debug manifest에서만 허용되고 release
manifest에서는 꺼집니다.

Docker에서 단위 테스트와 debug APK를 함께 빌드합니다.

```bash
docker compose run --rm android
```

APK는 `android/app/build/outputs/apk/debug/app-debug.apk`에 생성됩니다. 기본 WebView URL은
Android 에뮬레이터에서 호스트 loopback으로 연결하는 `http://10.0.2.2:18787/`이며, 기존
웹 서비스는 계속 `127.0.0.1`에 바인딩됩니다. URL은 debug 빌드 시 바꿀 수 있습니다.

```bash
MEDICINE_WEB_URL=http://10.0.2.2:19000/ docker compose run --rm android
```

실기기 debug 빌드는 PC와 휴대폰을 같은 신뢰 가능한 LAN에 연결하고, 웹 서비스를 명시적으로
LAN에 공개한 뒤 해당 PC 주소로 APK를 빌드합니다. 방화벽에서도 선택한 포트만 허용합니다.

```bash
MEDICINE_BIND_IP=0.0.0.0 docker compose up -d web
MEDICINE_WEB_URL=http://192.168.0.10:18787/ docker compose run --rm android
```

`192.168.0.10`은 PC의 실제 LAN 주소로 바꿉니다. 사용 후 `docker compose down`으로 LAN 공개를
종료합니다. release 빌드는 cleartext가 차단되므로, 배포 시에는 접근 가능한 HTTPS URL과 서명
구성이 필요합니다.

Document Scanner와 bundled 모델의 첫 실행 조건 및 GMS 지원 여부에 따라 실기기 결과가
달라질 수 있습니다.

## 의료 정보 주의

DUR 결과는 금기·주의 여부를 확인하기 위한 안전 신호입니다. 앱 결과만을 근거로 처방약을
임의로 중단하거나 변경하지 말고 의사 또는 약사와 확인해야 합니다.

## 데이터 사용 주의

KIDS DUR 페이지는 비상업적 연구·교육 목적 사용을 안내하며, 상업적 활용에는 별도 승인이
필요하다고 고지합니다. 제품화 전에 각 데이터셋의 이용조건을 다시 확인해야 합니다.
