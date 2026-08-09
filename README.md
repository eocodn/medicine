# medicine

한국 DUR 공개 데이터를 로컬 SQLite DB로 구축하고 조회하는 실험 프로젝트입니다.

## 현재 데이터 범위

- 공공데이터포털 제품 단위 CSV 7종
  - 병용금기
  - 특정연령대금기
  - 임부금기
  - 용량주의
  - 투여기간주의
  - 노인주의
  - 효능군중복주의
- 한국의약품안전관리원(KIDS) 성분 단위 XLSX 8종
  - 위 7종 + 수유부주의

원본 파일은 `data/raw/`, `data/kids/`에 보존하고 Git에는 넣지 않습니다. 생성 DB는
`data/db/dur.sqlite`이며 역시 Git에서 제외합니다.

## DB 구축

```bash
docker compose run --rm dur build --json
```

빌드는 임시 DB에 전체 import를 완료한 뒤 최종 DB를 원자적으로 교체합니다. 각 source의
SHA-256, 행 수, 원본 경로, 헤더 메타데이터를 `source_files`에 기록합니다.

## 상태 확인

```bash
docker compose run --rm dur stats --json
```

## 검색

```bash
docker compose run --rm dur search acemetacin --limit 20 --json
docker compose run --rm dur search 졸피뎀 --limit 20 --json
```

## 테스트

```bash
docker compose run --rm --build test
```

## 테이블

- `source_files`: 원본 파일 provenance, checksum, import row count
- `product_dur`: 제품 단위 DUR 레코드
- `ingredient_dur`: 성분 단위 DUR 레코드

각 DUR 레코드는 `source_row`를 가지고 있어 원본 CSV/XLSX 행으로 추적할 수 있습니다.

## 데이터 사용 주의

KIDS DUR 페이지는 비상업적 연구·교육 목적 사용을 안내하며, 상업적 활용에는 별도 승인이
필요하다고 고지합니다. 제품화 전에 데이터별 이용조건을 다시 확인해야 합니다.
