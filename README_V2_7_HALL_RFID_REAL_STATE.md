# V2.7 Hall + RFID 실상태 확인 / 등록 삭제 UI 보강

- RFID 저수준 인식 코드는 V2.5/V2.6과 동일함 (`AA0022000022DD`, 20 Scan 분석 유지).
- Find 화면은 DB의 `slot IS NOT NULL`만으로 '옷장 안'을 판단하지 않음.
- Hall이 EMPTY인 저장 Slot은 시작 시 및 Find 상태 확인 시 자동으로 stale 위치를 삭제함.
- Hall이 OCCUPIED인 Slot만 RFID 20회 Scan으로 해당 등록 RFID가 15% 이상 확인될 때 `CONFIRMED_INSIDE`로 표시함.
- Hall만 눌리고 RFID 확인이 약하면 `확인 필요`, RFID만 잡히고 Hall 위치가 없으면 `위치 미확정`으로 표시함.
- 등록된 옷 관리 카드에 빨간 `🗑 등록 삭제` 버튼을 명시적으로 표시함.
- 정적 파일에 `?v=2.7.0` cache bust를 추가하여 이전 브라우저 JS가 남는 문제를 줄임.
- 새 RFID 등록 완료 시 이미 눌려 있는 Hall 홈이 있으면 자동으로 20 Scan 매칭을 시작해 위치 확정/음성 안내가 가능함.
