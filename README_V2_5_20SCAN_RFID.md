# Smart Closet Final V2.5 — 20-Scan RFID Integration

이 버전은 RFID 인식 자체를 새로 만들지 않습니다. `closet_system/hardware/rfid_r200.py`의 저수준 M100 인식은 기존 정상 동작 코드와 동일합니다.

- Inventory command: `AA0022000022DD`
- `single_poll()`의 AA...DD frame parsing / EPC extraction 유지
- RF Power 26 dBm 유지
- 통합 로직에서만 성공한 분석 조건을 적용:
  - 1 Round = 20 scans
  - poll timeout = 0.13 s
  - poll delay = 0.02 s
  - 약 3초 / Round
  - 최대 10개 RFID 분석
  - TOP 3 별도 표시
  - 이전 Round에 없던 태그 = `신규`
  - 이전 Round에 있던 태그가 현재 인식률 15% 미만 = `인식실패`

## Hall -> RFID 위치 매칭

문이 OPEN 상태에서 Hall N이 EMPTY -> OCCUPIED가 되면 20 Scan을 수행합니다.
현재 DB에 등록되어 있지만 Slot이 없는 RFID 중, 이번 Round TOP10에 있고 인식률이 15% 이상인 태그만 후보가 됩니다. 그중 가장 높은 인식률의 RFID 1개를 Slot N에 저장합니다.

후보가 없거나 최고 인식률 동률이면 추측하지 않고 다음 20 Scan Round를 반복합니다. Hall N이 EMPTY로 돌아가면 대기를 취소합니다.

## 웹 신규 RFID 등록

웹 등록 단계도 동일한 20 Scan을 사용합니다. 이미 등록된 RFID를 제외하고, 15% 이상인 미등록 RFID 중 최고 인식률 1개를 등록 후보로 선택합니다. 최고 인식률 동률이면 자동 등록하지 않습니다.

## 실행

```bash
cd ~/smart_closet_sw
source .venv/bin/activate
python smart_closet_final_v2/run_smart_closet.py \
  --config smart_closet_final_v2/config.smart_closet.yaml \
  --host 0.0.0.0 \
  --port 8080
```
