# medicine

한국 DUR 공개 데이터를 기반으로 여러 사람의 복약을 관리하는 모바일 우선 로컬 웹앱입니다.
현재는 텍스트로 의약품을 검색해 추가하는 MVP이며, 최종 목표는 같은 API/도메인 구조를
사용하는 Android 앱과 처방전 사진 입력입니다.

## 실행

Docker만 있으면 됩니다.

```bash
cd ~/dev/medicine
docker compose up -d --build web
```

브라우저에서 `http://127.0.0.1:18787`을 엽니다. 포트를 바꾸려면 다음처럼 실행합니다.

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
  - 이름
  - 생년월일
  - 성별
  - 임신 여부
- DUR 수록 제품 검색
- 약 추가 전 자동 안전 확인
  - 현재 복용약과의 병용금기
  - 특정연령대금기
  - 임부금기
  - 65세 이상 노인주의
  - 효능군중복주의
  - 용량주의/투여기간주의 대상 여부 안내
- 복용약별 복용량 메모와 시간 일정
- 복용 완료 기록
- 복용 종료 처리
- 최근 복용 기록 조회
- JSON API와 동일 코어를 사용하는 headless CLI

용량주의와 투여기간주의는 현재 입력 모델만으로 실제 기준 초과 여부까지 계산하지 않습니다.
해당 규칙이 존재한다는 정보만 보여줍니다.

## DB 구조

데이터베이스를 두 개로 분리합니다.

### `data/db/dur.sqlite`

공개 DUR 규칙 DB입니다. 앱에서는 읽기 전용으로 엽니다.

- `source_files`: 원본 파일 provenance, SHA-256, 행 수
- `product_dur`: 제품 단위 DUR 규칙
- `ingredient_dur`: 성분 단위 DUR 규칙
- `product_catalog`: 검색용 정규화 제품 카탈로그

현재 로컬 DB 기준:

- 제품 단위 DUR: 558,637행
- 성분 단위 DUR: 4,172행
- 총 DUR 규칙: 562,809행
- 검색용 제품: 23,131개
- 원본 데이터 파일: 15개

### `data/db/personal.sqlite`

개인 복약 데이터 DB입니다.

- `people`: 관리 대상 프로필
- `medications`: 사람별 복용약
- `medication_schedules`: 복용 시간
- `dose_logs`: 실제 복용/건너뜀 기록

개인 DB는 Git에 포함하지 않습니다. 현재 로컬 MVP에서는 암호화하지 않으므로 외부에 노출되는
서버로 그대로 배포하면 안 됩니다. 웹 서비스도 기본적으로 `127.0.0.1`에만 바인딩합니다.

## DUR 데이터 범위

공공데이터포털 제품 단위 CSV 7종:

- 병용금기
- 특정연령대금기
- 임부금기
- 용량주의
- 투여기간주의
- 노인주의
- 효능군중복주의

한국의약품안전관리원(KIDS) 성분 단위 XLSX 8종:

- 위 7종
- 수유부주의

원본 파일은 `data/raw/`, `data/kids/`에 그대로 보존하고 Git에는 넣지 않습니다.

효능군중복주의 CSV는 공개 원본의 헤더와 값 의미가 일부 어긋나고 코드 안 공백이 `0`이
빠진 형태로 나타나는 사례가 있어 import 시 명시적으로 정규화합니다. 원본 파일은 수정하지
않습니다.

## DUR DB 다시 만들기

```bash
docker compose run --rm dur build --json
```

빌드는 임시 DB에 전체 import와 제품 카탈로그 생성을 완료한 뒤 최종 DB를 원자적으로
교체합니다.

상태 확인:

```bash
docker compose run --rm dur stats --json
```

## 앱 제어 CLI

웹 UI와 같은 `MedicationApp` 코어를 사용합니다.

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

# 추가 전 DUR 위험 미리보기
docker compose run --rm app risk-preview \
  --person <PERSON_ID> \
  --product-code <PRODUCT_CODE> \
  --json

# 복용약 추가
docker compose run --rm app med-add \
  --person <PERSON_ID> \
  --product-code <PRODUCT_CODE> \
  --dose "1정" \
  --time 08:00 \
  --time 20:00 \
  --json
```

기존 DUR 저수준 검색 CLI도 유지합니다.

```bash
docker compose run --rm dur search acemetacin --limit 20 --json
```

## 테스트

```bash
docker compose run --rm --build test
```

현재 테스트는 개인 DB 분리, 여러 사람 관리, 복용 기록, 제품 검색, 병용금기, 연령/임부/노인
주의, 효능군 중복, DUR import 정규화, 모바일 HTML/API 흐름을 포함합니다.

개발 중 모바일 렌더링을 확인할 때만 별도 Chromium 이미지를 사용합니다. 운영용 웹 이미지에는
Chromium을 넣지 않습니다.

```bash
docker compose run --rm ui screenshot --output data/debug/mobile.png --json
```

검수용 스크린샷은 확인 후 삭제합니다.

## 아직 하지 않는 것

- 처방전 사진 OCR/약 자동 추출
- 전체 허가 의약품 카탈로그 검색
  - 현재 검색 대상은 DUR 데이터에 등장하는 23,131개 제품입니다.
- 복용량 숫자/단위 해석 후 용량주의 기준과 자동 비교
- 실제 처방일수와 투여기간주의 기준 자동 비교
- 수유 여부를 개인 프로필과 연결한 수유부주의 자동 판정
- 로그인/클라우드 동기화/다기기 공유
- 개인 DB 암호화
- 네이티브 Android UI

처방전 사진 입력은 이후 `사진 → 약 후보 추출 → 사용자 확인 → 기존 medication preview/add`
흐름으로 연결하면 현재 위험검사 코어를 그대로 재사용할 수 있습니다.

## 의료 정보 주의

DUR 결과는 금기·주의 여부를 확인하기 위한 안전 신호입니다. 앱 결과만을 근거로 처방약을
임의로 중단하거나 변경하지 말고 의사 또는 약사와 확인해야 합니다.

## 데이터 사용 주의

KIDS DUR 페이지는 비상업적 연구·교육 목적 사용을 안내하며, 상업적 활용에는 별도 승인이
필요하다고 고지합니다. 제품화 전에 각 데이터셋의 이용조건을 다시 확인해야 합니다.
