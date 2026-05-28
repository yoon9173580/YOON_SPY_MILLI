# 1-min CSV 백테스트 데이터 인제스션 가이드

## 목표
- MES Futures v5 알고를 **실제 1-min bars로 검증** (현재는 v3 trade를 필터로 재처리한 projection)
- 2022 베어마켓 견고성 검증

## 데이터 사양

스크립트가 기대하는 CSV 포맷:
```csv
timestamp,open,high,low,close,volume
2023-03-25 09:30:00-04:00,4012.50,4014.25,4011.75,4013.25,1248
2023-03-25 09:31:00-04:00,4013.25,4015.00,4012.50,4014.75,892
...
```

- **컬럼명**: `timestamp,open,high,low,close,volume` (소문자, 정확히 이 순서)
- **timestamp 포맷**: ISO 8601 with timezone OR naive ET (스크립트가 NY tz로 자동 localize)
- **세션**: 09:30 ~ 16:00 ET (스크립트가 필터링)
- **상품**: ES (E-mini S&P 500) 또는 MES (Micro). 가격 스케일은 둘 다 동일

## 벤더 선택 (권장 순서)

### 1순위: FirstRate Data ($50 일회성, 최저가)
- URL: https://firstratedata.com/
- 상품: "ES Continuous Adjusted - 1 Min"
- 커버: 2008+ (3년 = 충분)
- 포맷: zip CSV, 컬럼 거의 그대로 사용 가능
- 결제 후 다운로드 → 압축 해제 → `MES_1min_data.csv`로 rename
- 라이브 거래에는 비활성

### 2순위: Polygon.io ($29/mo Stocks, $99/mo + Options)
- URL: https://polygon.io/
- 상품: Stocks Starter (1-min 5년) 또는 Stocks + Options Advanced
- 사용처: 백테스트 + Layer 3 Options Flow 실데이터
- 추천: 라이브 운영 중이라면 Polygon 한 번에 묶기

### 3순위: Databento ($99-200/mo, 선물 전문)
- URL: https://databento.com/
- 상품: GLBX.MDP3 (CME futures full depth)
- 추천: 본격 선물 트레이딩 + 실시간 tick 필요 시
- 과도함: 백테스트만이라면 FirstRate가 훨씬 저렴

### 비추: Yahoo (60일만), CME DataMine (월 $500), IB TWS (1년 한정)

## 인제스션 절차 (3년 백테스트 기준)

```bash
# 1. 데이터 파일을 repo 루트에 배치
mv ~/Downloads/ES_continuous_1min_2023-2026.csv MES_1min_data.csv

# 2. 포맷 정리 (필요 시)
python -c "
import pandas as pd
df = pd.read_csv('MES_1min_data.csv')
# 컬럼명 통일
df.columns = [c.lower() for c in df.columns]
# 필수 컬럼만 유지
df = df[['timestamp','open','high','low','close','volume']]
df.to_csv('MES_1min_data.csv', index=False)
print('Cleaned:', len(df), 'rows')
"

# 3. 백테스트 실행 (v5 새 필터 모두 적용된 알고)
python thorough_backtest_futures.py \
  --csv MES_1min_data.csv \
  --start 2023-03-25 \
  --end 2026-03-25 \
  --start_balance 10000

# 결과: backtest_futures_full_3yr.json 생성
```

## 2022 베어마켓 백테스트

```bash
# 동일 데이터셋, 다른 기간만 지정
python thorough_backtest_futures.py \
  --csv MES_1min_data.csv \
  --start 2022-01-03 \
  --end 2022-12-30 \
  --start_balance 10000
# 결과: backtest_2022_bear.json 생성
```

## 결과 통합

새 백테스트가 끝나면:
1. `backtest_futures_full_3yr.json` → `api/data.py:BACKTEST_SUMMARY["mes_futures"]` 갱신
2. `backtest_2022_bear.json` → `BACKTEST_SUMMARY["bear_market_2022"]`의 `status: "PROJECTION"` → `"BACKTEST"`로 변경
3. 대시보드는 자동으로 새 숫자 표시 (코드 변경 불필요)

## 비용 합산 (FirstRate 경로)

| 항목 | 비용 |
|---|---|
| FirstRate ES 1-min 2022-2026 | **$50 일회성** |
| (선택) Polygon Stocks Starter | $29/mo (라이브 옵션 데이터 시) |
| (선택) Tradier dev | $10/mo (옵션만 필요시 더 저렴) |
| **백테스트만 필요한 최소액** | **$50** |

## 데이터 받은 후 알림

CSV를 repo에 배치하고 알려주시면:
1. 포맷 검증
2. 3년 + 2022 백테스트 자동 실행
3. v5 projection 대비 실측 차이 리포트
4. `BACKTEST_SUMMARY` 갱신 PR
