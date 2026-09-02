# Smart Closet Final V2

이 버전은 기존 웹 `등록 / 찾기 / 코디` UI를 유지하면서 실제 Hall 8개, RFID, Arduino Solenoid 8개, GPIO24 문 센서를 한 런타임에 통합한다.

## 실제 동작

- 문 열림(GPIO24) -> 물리 동작 세션 시작
- 신규 등록: 사진 촬영/분석 -> 정보 확인 -> 서버가 새 Hall 입력 대기
- 빈 Hall N이 EMPTY->OCCUPIED가 되면 RFID를 20회(약 3초) 자동 스캔
- 등록되지 않은 RFID 중 인식률이 가장 높은 단일 태그를 옷과 매칭하고 `옷 N` 번호 및 Slot N을 한 번에 저장
- 이미 등록된 옷을 다시 거는 경우에도 Hall N 입력 후 20회 스캔하여 현재 다른 Slot에 없는 등록 RFID 중 최고 인식률 태그를 Slot N에 저장
- 자동 등록에서 후보가 없거나 최고 인식률이 동률이면 잘못 등록하지 않고 실패 안내 후 재시도
- 기다리는 동안 Hall N이 EMPTY가 되면 해당 매칭 취소
- Hall N OCCUPIED->EMPTY -> Slot만 NULL로 바꾸고 옷 번호/특징/RFID 매칭은 보존하며 웹 음성으로 색상·종류·계절을 안내
- 웹 찾기에서 `3` 또는 `옷 3` 입력 -> DB id=3 옷의 현재 Slot Solenoid ON
- 기존처럼 `검정색 반팔티` 같은 색상/종류 검색도 유지
- 찾기 Solenoid는 웹 `완료`, 새 찾기 요청, 또는 해당 Hall이 EMPTY가 될 때 OFF
- RFID 출력은 실제 확인된 최대값 26 dBm으로 설정하고 B7으로 설정값을 재확인
- 찾기 화면의 등록 목록은 옷 번호·색상·종류·계절·RFID 번호·현재 위치를 표시하며, 삭제하면 해당 RFID는 다시 미등록 후보가 됨

## 요구사항 기준 사용 순서

1. 웹에서 `등록`을 누르고 옷 사진을 촬영한다.
2. AI 분석 결과와 추가 정보를 확인하고 다음으로 이동한다.
3. 화면에 등록 대기 안내가 뜬 뒤 문을 열고 빈 Hall 위치에 옷걸이를 건다.
4. 서버가 새 Hall 입력을 감지한 시점부터 RFID를 20회 스캔한다. 완료될 때까지 옷걸이를 그대로 둔다.
5. 화면과 음성으로 `옷 N`, Slot, RFID 등록 완료를 확인한다.
6. 찾을 때는 `찾기`에서 `N` 또는 `옷 N`을 입력한다. 해당 Slot 솔레노이드가 켜진다.
7. 옷을 빼면 특징을 음성으로 안내하고 위치만 해제한다. RFID 매칭은 삭제되지 않는다.
8. RFID 매칭까지 없애려면 찾기 화면의 `등록된 옷 관리`에서 해당 옷의 `등록 삭제`를 누른다.

## 문 센서 배선 기본값

- BCM GPIO24 = physical pin 18
- 기본 가정: `GPIO24 -> JCAC0001-002 접점 -> GND`
- 내부 pull-up 사용
- 이 배선에서 문 열림=HIGH라면 `open_when_active: true`
- 실제 문 테스트에서 반대로 나오면 `config.smart_closet.yaml`의 `open_when_active`만 `false`로 바꾼다.

## 실행

프로젝트 루트가 `~/smart_closet_sw`이고 이 폴더가 그 안에 있다고 가정한다.

```bash
cd ~/smart_closet_sw
source .venv/bin/activate
set -a
source .env
set +a
python smart_closet_final_v2_7_hall_rfid_verified/smart_closet_final_v2/run_smart_closet.py \
  --config smart_closet_final_v2_7_hall_rfid_verified/smart_closet_final_v2/config.smart_closet.yaml \
  --host 0.0.0.0 \
  --port 8080
```

AI 키가 `config.real.yaml` 방식으로 이미 해결되어 있으면 export는 생략 가능하다.
AI 서버를 아직 사용하지 않으면 `fallback_to_manual: true`에 의해 사진 업로드 후 종류/색상/계절을 직접 입력할 수 있다. 실제 AI를 연결하려면 `config.real.yaml.example`을 `config.real.yaml`로 복사한 뒤 주소와 인증 환경변수를 설정한다.

### Raspberry Pi 최초 설치

```bash
sudo raspi-config nonint do_i2c 0
sudo apt update
sudo apt install -y python3-venv python3-dev i2c-tools
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r smart_closet_final_v2_7_hall_rfid_verified/smart_closet_final_v2/requirements-smart-closet.txt
```

RFID와 Arduino 장치명을 확인하고 현재 사용자를 직렬 포트 그룹에 추가한다.

```bash
ls -l /dev/ttyUSB* /dev/ttyACM*
sudo usermod -aG dialout,i2c,gpio "$USER"
```

그룹 변경은 로그아웃/로그인 또는 재부팅 후 적용된다. `/dev/ttyUSB0`, `/dev/ttyACM0`, Hall 임계값, 문센서 극성은 반드시 실제 배선에 맞춰 `config.smart_closet.yaml`에서 확인한다.

### 코디 날씨 API 설정

코디 추천은 첨부 시스템과 동일하게 한글 지역명을 좌표로 변환하고 OpenWeather 현재 날씨를 사용한다. OpenWeather API 키를 발급받은 뒤 루트 `.env`를 만든다.

```bash
cd ~/smart_closet_sw
cp smart_closet_final_v2_7_hall_rfid_verified/smart_closet_final_v2/.env.example .env
nano .env
chmod 600 .env
```

`.env`의 다음 값을 실제 키로 변경한다.

```text
OPENWEATHER_API_KEY=실제_OpenWeather_API_키
```

코디 화면은 현재 Hall + RFID로 안에 있음이 확인된 옷만 사용한다. 등록 시 `코디 구분`을 상의 또는 하의로 선택하고, 가능하면 색상 HEX와 패턴도 확인한다. 기존 옷은 종류 이름으로 상의/하의와 한글 색상을 자동 추론한다.

추천 점수는 제공된 시스템과 동일하게 색상 40%, 날씨 30%, 계절 20%, 선호도 10%로 계산한다. 코디를 선택하면 상의와 하의 슬롯의 솔레노이드가 동시에 켜지고 착용 횟수가 증가한다.

### 부팅 시 서버 자동 시작

서비스 예제에는 사용자 `closet`, 작업 폴더 `/home/closet/smart_closet_sw`, 현재 프로젝트의 중첩 경로가 반영되어 있다.

```bash
sudo cp smart_closet_final_v2_7_hall_rfid_verified/smart_closet_final_v2/smart-closet.service.example /etc/systemd/system/smart-closet.service
sudo systemctl daemon-reload
sudo systemctl enable --now smart-closet.service
sudo systemctl status smart-closet.service
```

## 웹 열기

같은 네트워크에서 Pi IP 확인:

```bash
hostname -I
```

예를 들어 Pi IP가 `192.168.0.50`이면:

```text
http://192.168.0.50:8080
```

mDNS가 활성화되어 있으면 다음 주소를 사용할 수 있다.

```text
http://smart-closet.local:8080
```

PC Chrome에서 음성 인식까지 안정적으로 쓰려면 Windows PowerShell에서 SSH 터널:

```powershell
ssh -L 8080:127.0.0.1:8080 closet@<PI_IP>
```

그 후 Chrome:

```text
http://localhost:8080
```

## HW 상태 확인

서버 실행 중 브라우저에서:

```text
http://<PI_IP>:8080/api/status
```

또는 Pi에서:

```bash
curl http://127.0.0.1:8080/api/status
```

여기서 `session.door_open`, `session.hall_voltages`, `session.hall_occupied`, `session.pending_slots`, `rfid_power_dbm`을 확인할 수 있다.

## 중요

Hall threshold `occupied_on_v / empty_off_v`는 실제 배선/분압 후 측정값에 맞춰야 한다. 현재 기본값 2.15 / 2.05 V는 최종 측정값이 아니라 기존 테스트 기준값이다.


## V2.2 Find-page clothing list
- The Find page now loads `/api/clothes/inside` and shows every item that currently has a slot.
- Each card shows clothing number, color, category, season, and extra information.
- Selecting a card reuses the existing `/api/find` path, activates the solenoid for that item's stored slot, and speaks/displays the location.
- The RFID UID is not exposed in the browser list.

## V2.3 찾기 음성 후보 선택
- "검정색 옷 찾아줘"처럼 특징으로 검색하면 최고 점수로 일치하는 옷을 모두 후보로 반환합니다.
- 웹과 TTS에서 `1번`, `2번`, ... 순서로 색상/종류/계절/추가정보를 안내합니다.
- 후보 안내 후 사용자가 "2번"처럼 말하면 두 번째 후보의 실제 옷 ID를 사용해 기존 찾기 로직을 실행하고 해당 슬롯의 솔레노이드를 ON 합니다.
- 후보 검색 단계에서는 어떤 솔레노이드도 켜지지 않습니다.
