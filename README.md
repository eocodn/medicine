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

기존 v1 `personal.sqlite`는 실행 시 누락 컬럼/테이블만 추가하는 방식으로 migration합니다.
개인 DB는 Git에 포함하지 않습니다. 현재 로컬 버전에서는 암호화하지 않으므로 외부 공개 서버로
그대로 배포하면 안 됩니다. 웹 서비스도 기본적으로 `127.0.0.1`에만 바인딩합니다.

### `data/db/catalog.sqlite` (선택)

식약처 의약품 제품 허가정보 API에서 동기화한 전체 제품 카탈로그입니다. 제품 허가번호
(`item_seq`)를 앱 내부 참조키로 사용하고, EDI 코드가 있는 제품은 DUR 제품코드와 연결합니다.

EDI/DUR 연결이 되지 않은 제품도 검색·복약 등록은 가능하지만, 개인별 DUR 자동 판정 범위가
제한된다는 안내를 표시합니다. 전체 카탈로그가 없어도 앱은 `dur.sqlite`의 제품 카탈로그로
정상 동작합니다.

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

현재 개발환경에는 서비스키가 없어서 실제 MFDS 전체 카탈로그 파일은 아직 생성하지 않았습니다.

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

기존 DUR 저수준 검색 CLI도 유지합니다.

```bash
docker compose run --rm dur search acemetacin --limit 20 --json
```

## 테스트 / 모바일 렌더링

```bash
docker compose run --rm --build test
```

테스트는 개인 DB migration, 여러 사람 관리, 구조화 처방, 날짜별 복용 인스턴스 멱등성, PRN,
복용 기록, 전체/대체 제품 검색, 병용금기, 연령/임부/노인주의, 효능군 중복, DUR import 정규화,
MFDS 카탈로그 동기화, 모바일 HTML/API 흐름을 포함합니다.

개발 중 모바일 렌더링 확인용 Chromium은 별도 이미지로만 사용합니다.

```bash
docker compose run --rm ui screenshot --output data/debug/mobile.png --json
```

검수용 스크린샷은 확인 후 삭제합니다.

## 아직 하지 않는 것

- 처방전 사진 OCR/약 자동 추출
- 식약처 전체 허가 의약품 실데이터 동기화 완료본
  - 동기화 코드/DB/검색 경로는 구현되어 있으나 현재 개발환경에는 공공데이터포털 서비스키가 없음
- 입력한 복용량 숫자/단위와 DUR 용량주의 기준의 자동 비교
- 구조화 처방일수와 DUR 투여기간주의 기준의 자동 초과 판정
- 수유 여부를 개인 프로필과 연결한 수유부주의 자동 판정
- 로그인/클라우드 동기화/다기기 공유
- 개인 DB 암호화
- 네이티브 Android UI

처방전 사진 입력은 이후 `사진 → OCR/구조화 → 전체 의약품 후보 매칭 → 사용자 확인 → 기존
preview/add → 오늘 일정 생성` 흐름으로 연결합니다.

## 의료 정보 주의

DUR 결과는 금기·주의 여부를 확인하기 위한 안전 신호입니다. 앱 결과만을 근거로 처방약을
임의로 중단하거나 변경하지 말고 의사 또는 약사와 확인해야 합니다.

## 데이터 사용 주의

KIDS DUR 페이지는 비상업적 연구·교육 목적 사용을 안내하며, 상업적 활용에는 별도 승인이
필요하다고 고지합니다. 제품화 전에 각 데이터셋의 이용조건을 다시 확인해야 합니다.
