# Smart Closet V2.6

V2.5의 20-scan RFID 인식 코드는 그대로 유지하고 웹/DB/이벤트 기능만 확장했습니다.

## 추가 기능
- 찾기 화면의 **현재 옷장 안** 목록 유지
- 찾기 화면에 **등록된 옷 관리** 목록 추가
  - 모든 등록 옷 정보, RFID, 현재 내부/외부 상태, Slot 표시
  - 등록 삭제 버튼으로 RFID + 옷 정보 삭제
  - 현재 Slot이 있으면 위치 정보도 함께 삭제
  - legacy DB가 있어도 삭제한 RFID가 재시작 시 자동 복구되지 않도록 tombstone 저장
- Hall EMPTY→OCCUPIED 후 20-scan 매칭이 완료되면 전역 음성 안내
  - 예: `옷 3이 6번 위치에 걸렸습니다. 색상 검정색, 종류 반팔티, 계절 여름입니다.`
  - 최초 RFID 등록 직후 처음 Slot에 매칭되는 경우도 동일하게 안내
- 기존 제거 음성 안내 유지
- 기존 특징 검색 → 후보 음성 → 번호 선택 → Solenoid ON 유지

## RFID 인식
V2.5의 `AA0022000022DD`, 20회 Scan, 0.13s poll timeout, 0.02s delay, TOP10/TOP3, 15% threshold 로직을 변경하지 않았습니다.
