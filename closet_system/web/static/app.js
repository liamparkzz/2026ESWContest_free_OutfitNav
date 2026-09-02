const state = {
  photoFile: null,
  previewUrl: "",
  analysis: { category:"", color:"", season:"", position:"", colorHex:"", pattern:"" },
  extra: "",
  rfid: "",
  voiceEnabled: false,
  speechRate: Number(localStorage.getItem("smartClosetSpeechRate") || "1.25"),
  recognition: null,
  listening: false,
  voiceMode: null,
  findCandidates: [],
  lastHardwareEventId: 0,
  hardwareEventsReady: false,
  hardwareEventPollBusy: false,
  hardwareSpeechQueue: [],
  hardwareSpeaking: false,
  registrationPollTimer: null,
  registrationPollBusy: false,
  registrationCompleted: false,
  codiRecommendations: [],
};

const app = document.getElementById("app");

function esc(v){ return String(v ?? "").replaceAll("&","&amp;").replaceAll("<","&lt;").replaceAll(">","&gt;").replaceAll('"',"&quot;").replaceAll("'","&#039;"); }
function speak(text, force=false){
  if (!force && !state.voiceEnabled) return;
  if (!("speechSynthesis" in window)) return;
  speechSynthesis.cancel();
  const u = new SpeechSynthesisUtterance(text);
  u.lang = "ko-KR"; u.rate = state.speechRate; u.pitch = 1; u.volume = 1;
  speechSynthesis.speak(u);
}
function setRate(v){ state.speechRate = Number(v); localStorage.setItem("smartClosetSpeechRate", String(state.speechRate)); const el=document.getElementById("rateLabel"); if(el) el.textContent=state.speechRate.toFixed(2)+"배속"; }
function enableVoice(){ state.voiceEnabled=true; speak("음성 안내를 시작합니다.", true); }

let globalNoticeTimer = null;

function showGlobalNotice(text){
  const el=document.getElementById("globalNotice");
  if(!el) return;
  el.textContent=text;
  el.classList.add("show");
  if(globalNoticeTimer) clearTimeout(globalNoticeTimer);
  globalNoticeTimer=setTimeout(()=>el.classList.remove("show"),5000);
}

function speakNextHardwareEvent(){
  if(state.hardwareSpeaking) return;
  const text=state.hardwareSpeechQueue.shift();
  if(!text) return;
  if(!("speechSynthesis" in window)) return;

  state.hardwareSpeaking=true;
  const u=new SpeechSynthesisUtterance(text);
  u.lang="ko-KR";
  u.rate=state.speechRate;
  u.pitch=1;
  u.volume=1;
  u.onend=()=>{ state.hardwareSpeaking=false; speakNextHardwareEvent(); };
  u.onerror=()=>{ state.hardwareSpeaking=false; speakNextHardwareEvent(); };
  speechSynthesis.speak(u);
}

function speakHardwareEvent(text){
  state.hardwareSpeechQueue.push(text);
  if(!state.hardwareSpeaking){
    // Physical removal has priority over ordinary page guidance. Interrupt the
    // current page speech once, then speak multiple removal events in sequence.
    if("speechSynthesis" in window) speechSynthesis.cancel();
    speakNextHardwareEvent();
  }
}

function parseEventDetail(event){
  const raw=event?.detail_json;
  if(!raw) return {};
  if(typeof raw==="object") return raw;
  try{ return JSON.parse(raw); }catch(e){ return {}; }
}

function removalSpeechText(detail){
  const id=Number(detail?.clothing_id||0);
  const parts=[];
  if(detail?.color) parts.push(`색상 ${detail.color}`);
  if(detail?.category) parts.push(`종류 ${detail.category}`);
  if(detail?.season) parts.push(`계절 ${detail.season}`);
  if(detail?.extra) parts.push(`추가정보 ${detail.extra}`);

  const name=id ? `옷 ${id}` : "옷";
  if(parts.length){
    return `${name}을 꺼냈습니다. ${parts.join(", ")}입니다.`;
  }
  return `${name}을 꺼냈습니다.`;
}

function placementSpeechText(detail){
  const id=Number(detail?.clothing_id||0);
  const slot=Number(detail?.slot||0);
  const parts=[];
  if(detail?.color) parts.push(`색상 ${detail.color}`);
  if(detail?.category) parts.push(`종류 ${detail.category}`);
  if(detail?.season) parts.push(`계절 ${detail.season}`);
  const name=id ? `옷 ${id}` : "옷";
  const location=slot ? `${slot}번 위치에 걸렸습니다.` : "옷장에 걸렸습니다.";
  return parts.length ? `${name}이 ${location} ${parts.join(", ")}입니다.` : `${name}이 ${location}`;
}

function handleHardwareEvent(event){
  const detail=parseEventDetail(event);
  let message="";
  let icon="";

  if(event?.event_type==="REMOVE_CONFIRMED"){
    message=removalSpeechText(detail);
    icon="📤";
  }else if(event?.event_type==="PLACEMENT_CONFIRMED"){
    // Placement announcements are global too, including immediately after a
    // brand-new RFID registration is matched to its first Hall slot.
    message=placementSpeechText(detail);
    icon="📍";
  }else{
    return;
  }

  speakHardwareEvent(message);
  showGlobalNotice(`${icon} ${message}`);

  // Keep both Find lists synchronized when a physical placement/removal occurs.
  if(document.getElementById("insideClothes") || document.getElementById("registeredClothes")) loadClosetState();
}

async function initializeHardwareEventMonitor(){
  try{
    const r=await fetch("/api/events?limit=1",{cache:"no-store"});
    const d=await r.json();
    if(r.ok && Array.isArray(d.events) && d.events.length){
      state.lastHardwareEventId=Number(d.events[0].id||0);
    }
  }catch(e){
    // Server may still be starting; polling below will recover automatically.
  }
  state.hardwareEventsReady=true;
}

async function pollHardwareEvents(){
  if(!state.hardwareEventsReady || state.hardwareEventPollBusy) return;
  state.hardwareEventPollBusy=true;
  try{
    const r=await fetch("/api/events?limit=50",{cache:"no-store"});
    const d=await r.json();
    if(!r.ok || !Array.isArray(d.events)) return;

    // API returns newest first. Process unseen events oldest -> newest so multiple
    // clothes removed quickly are spoken in their actual physical order.
    const unseen=d.events
      .filter(e=>Number(e.id||0)>state.lastHardwareEventId)
      .sort((a,b)=>Number(a.id||0)-Number(b.id||0));

    for(const event of unseen){
      state.lastHardwareEventId=Math.max(state.lastHardwareEventId,Number(event.id||0));
      handleHardwareEvent(event);
    }
  }catch(e){
    // Hardware-event polling must never interrupt the current web workflow.
  }finally{
    state.hardwareEventPollBusy=false;
  }
}

async function jsonPost(url, body={}){
  const r = await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
  const data = await r.json().catch(()=>({detail:"응답 파싱 실패"}));
  if(!r.ok) throw new Error(data.detail || `HTTP ${r.status}`);
  return data;
}

function home(){
  app.innerHTML = `<section class="card">
    <h1>스마트 옷장</h1><p class="muted">원하는 기능을 선택해주세요.</p>
    <button class="big primary" onclick="registerPhoto()">등록</button>
    <button class="big" onclick="findPage()">찾기</button>
    <button class="big" onclick="codiPage()">코디</button>
    <div class="voice-box"><h3>음성 설정</h3>
      <button onclick="enableVoice()">🔊 음성 안내 시작</button>
      <label>말 속도: <span id="rateLabel">${state.speechRate.toFixed(2)}배속</span>
        <input type="range" min="0.6" max="2.0" step="0.05" value="${state.speechRate}" oninput="setRate(this.value)">
      </label>
      <p class="muted">음성 설정은 첫 화면에서만 표시됩니다.</p>
    </div>
  </section>`;
  speak("스마트 옷장입니다. 등록, 찾기, 코디 중 선택해주세요.");
}

function registerPhoto(){
  app.innerHTML = `<section class="card"><h2>1. 사진 촬영</h2>
    <input id="photo" type="file" accept="image/*" capture="environment" onchange="photoChanged(event)">
    <div id="preview"></div>
    <div class="row"><button onclick="home()">이전</button><button class="primary" onclick="analyzePhoto()">AI로 사진 분석하기</button></div>
    <div id="status" class="status"></div></section>`;
  speak("등록을 시작합니다. 옷 사진을 촬영해주세요.");
}
function photoChanged(e){ const f=e.target.files?.[0]; if(!f)return; state.photoFile=f; if(state.previewUrl)URL.revokeObjectURL(state.previewUrl); state.previewUrl=URL.createObjectURL(f); document.getElementById("preview").innerHTML=`<img class="preview" src="${state.previewUrl}">`; }
function pick(obj, keys){ for(const k of keys){ const v=obj?.[k]; if(v!==undefined && v!==null && String(v).trim()) return String(v).trim(); } return ""; }
function normalizeAi(raw){
  const root=raw?.result||raw?.data||raw?.analysis||raw?.item||raw?.clothing||raw;
  const rawCategory=pick(root,["category","position","대분류","구분"]);
  const rawColor=pick(root,["color","colour","색상"]);
  const colorIsHex=/^#?[0-9a-fA-F]{6}$/.test(rawColor);
  return {
    position:pick(root,["position","대분류","구분"])||(/[상하]의/.test(rawCategory)?rawCategory:""),
    category:pick(root,["detail_subcategory","detailSubcategory","세부종류","clothing_type","type"])||pick(root,["subcategory","sub_category","subtype","detail_type","서브카테고리"])||rawCategory,
    color:pick(root,["color_name","colorName","color_label","색상명","색상이름"])||(!colorIsHex?rawColor:""),
    colorHex:pick(root,["color_hex","colorHex","hex_color","색상코드"])||(colorIsHex?(rawColor.startsWith("#")?rawColor:`#${rawColor}`):""),
    pattern:pick(root,["pattern","패턴","무늬"]),
    season:pick(root,["season","계절"]),
    manual:Boolean(raw?.manual_entry_required||root?.manual_entry_required),
    message:pick(raw,["message"])||pick(root,["message"]),
  };
}
async function analyzePhoto(){
  if(!state.photoFile){ alert("사진을 먼저 선택해주세요."); return; }
  const s=document.getElementById("status"); s.textContent="AI 분석 중...";
  const fd=new FormData(); fd.append("file",state.photoFile);
  try{
    const r=await fetch("/api/analyze",{method:"POST",body:fd}); const d=await r.json(); if(!r.ok)throw new Error(d.detail||`HTTP ${r.status}`);
    state.analysis=normalizeAi(d); reviewPage();
    if(state.analysis.manual){
      speak("AI 서버가 설정되지 않아 직접 입력 단계로 이동합니다. 종류, 색상, 계절을 입력해주세요.",true);
    }else{
      speak(`분석이 완료되었습니다. 종류 ${state.analysis.category||"알 수 없음"}, 색상 ${state.analysis.color||"알 수 없음"}, 계절 ${state.analysis.season||"알 수 없음"}입니다.`, true);
    }
  }catch(e){ s.textContent="AI 분석 실패: "+e.message; speak("AI 분석에 실패했습니다.",true); }
}
function reviewPage(){
  app.innerHTML=`<section class="card"><h2>2. 분석 결과 확인 및 수정</h2>
    ${state.analysis.manual?`<div class="status">${esc(state.analysis.message||"AI 서버가 설정되지 않았습니다. 아래 정보를 직접 입력해주세요.")}</div>`:""}
    <label>코디 구분<select id="position">
      <option value="">선택해주세요</option>
      <option value="상의" ${state.analysis.position==="상의"?"selected":""}>상의</option>
      <option value="하의" ${state.analysis.position==="하의"?"selected":""}>하의</option>
    </select></label>
    <label>종류<input id="category" value="${esc(state.analysis.category)}"></label>
    <label>색상<input id="color" value="${esc(state.analysis.color)}"></label>
    <label>색상 HEX <span class="muted">(AI 결과가 없으면 색상명으로 자동 추정)</span><input id="colorHex" placeholder="#243B5A" value="${esc(state.analysis.colorHex||"")}"></label>
    <label>패턴<input id="pattern" placeholder="예: 무지, 스트라이프" value="${esc(state.analysis.pattern||"")}"></label>
    <label>계절<input id="season" value="${esc(state.analysis.season)}"></label>
    <div class="row"><button onclick="repeatAnalysis()">🔁 다시 듣기</button><button onclick="startVoice('review')">🎙 수정 말하기</button></div>
    <div id="voiceText" class="status">예: "구분 상의 종류 반팔티 색상 검정색 패턴 무지 계절 여름"</div>
    <div class="row"><button onclick="registerPhoto()">이전</button><button class="primary" onclick="extraPage()">다음</button></div>
  </section>`;
}
function saveReview(){ state.analysis.position=document.getElementById("position")?.value.trim()||""; state.analysis.category=document.getElementById("category")?.value.trim()||""; state.analysis.color=document.getElementById("color")?.value.trim()||""; state.analysis.colorHex=document.getElementById("colorHex")?.value.trim()||""; state.analysis.pattern=document.getElementById("pattern")?.value.trim()||""; state.analysis.season=document.getElementById("season")?.value.trim()||""; }
function repeatAnalysis(){ saveReview(); speak(`분석 결과입니다. 구분 ${state.analysis.position||"없음"}, 종류 ${state.analysis.category||"없음"}, 색상 ${state.analysis.color||"없음"}, 패턴 ${state.analysis.pattern||"없음"}, 계절 ${state.analysis.season||"없음"}입니다.`, true); }
function extraPage(){ saveReview(); app.innerHTML=`<section class="card"><h2>3. 추가 정보 입력</h2><label>추가사항<textarea id="extra">${esc(state.extra)}</textarea></label><button onclick="startVoice('extra')">🎙 추가사항 말하기</button><div id="voiceText" class="status"></div><div class="row"><button onclick="reviewPage()">이전</button><button class="primary" onclick="rfidPage()">다음</button></div></section>`; speak("추가 정보 입력 단계입니다."); }
function stopRegistrationPolling(){
  if(state.registrationPollTimer){
    clearInterval(state.registrationPollTimer);
    state.registrationPollTimer=null;
  }
  state.registrationPollBusy=false;
}

function resetRegistrationForm(){
  state.photoFile=null;
  state.analysis={category:"",color:"",season:"",position:"",colorHex:"",pattern:""};
  state.extra="";
  state.rfid="";
  state.registrationCompleted=false;
}

function rfidPage(){
  state.extra=document.getElementById("extra")?.value.trim()||state.extra;
  state.registrationCompleted=false;
  app.innerHTML=`<section class="card">
    <h2>4. 옷걸이 걸기 · 자동 RFID 등록</h2>
    <p>등록 준비가 끝났습니다. <strong>문을 열고 빈 위치에 옷걸이를 걸어주세요.</strong></p>
    <p class="muted">새로 눌린 Hall 센서를 서버가 감지하면 RFID를 약 3초 동안 20회 스캔합니다. 이미 등록된 태그를 제외하고 인식률이 가장 높은 태그 하나를 자동으로 옷과 매칭합니다.</p>
    <div id="status" class="status">서버에 등록 대기를 요청하고 있습니다...</div>
    <div id="registrationResult"></div>
    <button id="retryRegistration" class="primary" hidden onclick="startAutomaticRegistration()">다시 등록 대기</button>
    <div class="row"><button onclick="cancelRegistrationToExtra()">이전</button><button onclick="cancelRegistrationToHome()">등록 취소</button></div>
  </section>`;
  speak("등록 준비가 끝났습니다. 문을 열고 빈 위치에 옷걸이를 걸어주세요.",true);
  startAutomaticRegistration();
}

async function startAutomaticRegistration(){
  stopRegistrationPolling();
  state.registrationCompleted=false;
  const s=document.getElementById("status");
  const retry=document.getElementById("retryRegistration");
  if(s) s.textContent="새 Hall 센서 입력을 기다릴 준비 중입니다...";
  if(retry) retry.hidden=true;
  try{
    await jsonPost("/api/registration/start",{
      category:state.analysis.category,
      color:state.analysis.color,
      season:state.analysis.season,
      extra:state.extra,
      position:state.analysis.position,
      color_hex:state.analysis.colorHex,
      pattern:state.analysis.pattern,
    });
  }catch(e){
    // A browser refresh can reconnect while the server is already waiting.
    // Polling reveals that active state; a genuine failure is shown there too.
    if(s) s.textContent=e.message;
  }
  await pollRegistrationStatus();
  if(!state.registrationCompleted){
    state.registrationPollTimer=setInterval(pollRegistrationStatus,350);
  }
}

async function pollRegistrationStatus(){
  if(state.registrationPollBusy) return;
  state.registrationPollBusy=true;
  try{
    const r=await fetch(`/api/registration/status?t=${Date.now()}`,{cache:"no-store"});
    const d=await r.json().catch(()=>({detail:"응답 파싱 실패"}));
    if(!r.ok) throw new Error(d.detail||`HTTP ${r.status}`);
    const s=document.getElementById("status");
    const result=document.getElementById("registrationResult");
    const retry=document.getElementById("retryRegistration");
    if(s) s.textContent=d.message||d.status;

    if(d.status==="WAITING_HALL"){
      if(s && d.remaining_s!==null) s.textContent+=`\n대기 가능 시간: 약 ${Math.ceil(Number(d.remaining_s))}초`;
    }else if(d.status==="SCANNING"){
      if(s) s.textContent=`${Number(d.slot)}번 위치 감지 완료\nRFID 20회 스캔 중입니다. 옷걸이를 그대로 두세요.`;
    }else if(d.status==="COMPLETED" && !state.registrationCompleted){
      state.registrationCompleted=true;
      stopRegistrationPolling();
      state.rfid=d.item?.rfid||"";
      if(result) result.innerHTML=`<div class="registration-success"><strong>옷 ${Number(d.item?.id)}</strong><br>${Number(d.item?.slot)}번 위치<br>RFID: ${esc(d.item?.rfid||"")}</div>`;
      speak(`옷 ${Number(d.item?.id)}로 등록되었습니다. ${Number(d.item?.slot)}번 위치입니다.`,true);
      setTimeout(()=>{ resetRegistrationForm(); home(); },2200);
    }else if(d.status==="FAILED"){
      stopRegistrationPolling();
      if(s) s.textContent=`등록 실패: ${d.message}\n옷걸이를 홈에서 뺀 뒤 다시 등록 대기를 누르고 걸어주세요.`;
      if(retry) retry.hidden=false;
      speak("등록에 실패했습니다. 옷걸이를 뺀 뒤 다시 시도해주세요.",true);
    }else if(d.status==="CANCELLED"){
      stopRegistrationPolling();
    }
  }catch(e){
    const s=document.getElementById("status");
    if(s) s.textContent="등록 상태 확인 실패: "+e.message;
  }finally{
    state.registrationPollBusy=false;
  }
}

async function cancelRegistrationToExtra(){
  stopRegistrationPolling();
  try{ await jsonPost("/api/registration/cancel"); }catch(e){}
  extraPage();
}

async function cancelRegistrationToHome(){
  stopRegistrationPolling();
  try{ await jsonPost("/api/registration/cancel"); }catch(e){}
  resetRegistrationForm();
  home();
}

function clothingInfoText(item){
  const parts=[];
  if(item.position) parts.push(`구분 ${item.position}`);
  if(item.color) parts.push(`색상 ${item.color}`);
  if(item.category) parts.push(`종류 ${item.category}`);
  if(item.pattern) parts.push(`패턴 ${item.pattern}`);
  if(item.season) parts.push(`계절 ${item.season}`);
  if(item.extra) parts.push(`추가정보 ${item.extra}`);
  return parts.length ? parts.join(" · ") : "등록된 상세 정보 없음";
}

function renderInsideClothes(items){
  const box=document.getElementById("insideClothes");
  if(!box) return;
  if(!items?.length){
    box.innerHTML=`<div class="empty-state">Hall + RFID로 확인된 옷장 안의 옷이 없습니다.</div>`;
    return;
  }
  box.innerHTML=items.map(item=>`
    <button class="clothing-card" onclick="selectInsideClothing(${Number(item.id)})">
      <span class="clothing-number">옷 ${Number(item.id)}</span>
      <span class="clothing-info">${esc(clothingInfoText(item))}</span>
      <span class="location-badge inside">${Number(item.slot)}번 홈 · RFID ${Number(item.rfid_rate||0).toFixed(1)}%</span>
      <span class="clothing-action">이 옷 찾기 →</span>
    </button>`).join("");
}

function registeredStatusText(item){
  const rate=Number(item?.rfid_rate||0).toFixed(1);
  if(item?.presence==="CONFIRMED_INSIDE"){
    return `옷장 안 확인 · ${Number(item.slot)}번 홈 · RFID ${rate}%`;
  }
  if(item?.presence==="HALL_ONLY_RFID_WEAK"){
    return `${Number(item.slot)}번 Hall 눌림 · RFID 확인 실패 (${rate}%)`;
  }
  if(item?.presence==="RFID_ONLY_NO_SLOT"){
    return `RFID는 감지됨 (${rate}%) · Hall 위치 미확정`;
  }
  return "옷장 밖 · Hall 위치 없음";
}

function registeredBadgeClass(item){
  if(item?.presence==="CONFIRMED_INSIDE") return "inside";
  if(item?.presence==="HALL_ONLY_RFID_WEAK" || item?.presence==="RFID_ONLY_NO_SLOT") return "uncertain";
  return "outside";
}

function renderRegisteredClothes(items){
  const box=document.getElementById("registeredClothes");
  if(!box) return;
  if(!items?.length){
    box.innerHTML=`<div class="empty-state">등록된 옷이 없습니다.</div>`;
    return;
  }

  box.innerHTML=items.map(item=>{
    const inside=item?.presence==="CONFIRMED_INSIDE";
    const findButton=inside
      ? `<button class="small-action" onclick="selectInsideClothing(${Number(item.id)})">📍 위치 찾기</button>`
      : `<button class="small-action" disabled>위치 찾기 불가</button>`;
    return `<div class="registered-card">
      <div class="registered-card-main">
        <span class="clothing-number">옷 ${Number(item.id)}</span>
        <span class="clothing-info">${esc(clothingInfoText(item))}</span>
        <span class="location-badge ${registeredBadgeClass(item)}">${esc(registeredStatusText(item))}</span>
        <span class="rfid-text">RFID: ${esc(item.rfid||"")}</span>
      </div>
      <div class="registered-actions">
        ${findButton}
        <button class="danger prominent-delete" onclick="deleteRegisteredClothing(${Number(item.id)})">🗑 등록 삭제</button>
      </div>
    </div>`;
  }).join("");
}

async function loadClosetState(){
  const insideBox=document.getElementById("insideClothes");
  const registeredBox=document.getElementById("registeredClothes");
  const insideCount=document.getElementById("insideCount");
  const registeredCount=document.getElementById("registeredCount");
  const verifyStatus=document.getElementById("verifyStatus");

  if(insideBox) insideBox.innerHTML=`<div class="empty-state">Hall 상태 확인 중...</div>`;
  if(registeredBox) registeredBox.innerHTML=`<div class="empty-state">현재 물리 상태를 확인 중...</div>`;
  if(verifyStatus) verifyStatus.textContent="Hall을 확인하고, 눌린 홈이 있으면 기존 20회 RFID Scan으로 대조합니다.";

  try{
    const r=await fetch(`/api/clothes/state?t=${Date.now()}`,{cache:"no-store"});
    const d=await r.json();
    if(!r.ok) throw new Error(d.detail||`HTTP ${r.status}`);

    if(insideCount) insideCount.textContent=`현재 옷장 안 확인: ${d.inside_count??0}벌`;
    if(registeredCount){
      registeredCount.textContent=`전체 등록 ${d.registered_count??0}벌 · 안 확인 ${d.inside_count??0}벌 · 확인 필요 ${d.uncertain_count??0}벌`;
    }

    if(verifyStatus){
      if((d.occupied_hall_count??0)===0){
        verifyStatus.textContent="Hall 8개가 모두 EMPTY입니다. 따라서 현재 옷장 안으로 확정된 옷은 0벌입니다.";
      }else{
        verifyStatus.textContent=`Hall ${d.occupied_hall_count}개 OCCUPIED · RFID 20회 Scan 대조 완료 · 기준 ${Number(d.rfid_threshold_pct||15).toFixed(0)}%`;
      }
    }

    renderInsideClothes(d.inside_items||[]);
    renderRegisteredClothes(d.items||[]);
  }catch(e){
    if(insideBox) insideBox.innerHTML=`<div class="status">상태 확인 실패: ${esc(e.message)}</div>`;
    if(registeredBox) registeredBox.innerHTML=`<div class="status">등록 목록 확인 실패: ${esc(e.message)}</div>`;
    if(verifyStatus) verifyStatus.textContent=`상태 확인 실패: ${e.message}`;
  }
}

async function loadInsideClothes(){ return loadClosetState(); }
async function loadRegisteredClothes(){ return loadClosetState(); }

async function deleteRegisteredClothing(id){
  const itemId=Number(id);
  if(!Number.isFinite(itemId)) return;
  const ok=window.confirm(`옷 ${itemId}의 RFID 등록과 옷 정보를 완전히 삭제할까요?\n삭제 후 같은 RFID는 다시 신규 등록할 수 있습니다.`);
  if(!ok) return;

  try{
    const r=await fetch(`/api/clothes/${itemId}`,{method:"DELETE",cache:"no-store"});
    const d=await r.json().catch(()=>({detail:"응답 파싱 실패"}));
    if(!r.ok) throw new Error(d.detail||`HTTP ${r.status}`);
    showGlobalNotice(`🗑️ ${d.message}`);
    speak(d.message,true);
    state.findCandidates=[];
    await loadClosetState();
  }catch(e){
    showGlobalNotice(`⚠️ 삭제 실패: ${e.message}`);
    speak(`삭제에 실패했습니다. ${e.message}`,true);
  }
}

function findPage(){
  state.findCandidates=[];
  app.innerHTML=`<section class="card">
    <div class="version-chip">V2.9 Hall · RFID · 코디 통합</div>
    <h2>옷 찾기</h2>
    <p class="muted">DB에 저장된 위치만 믿지 않습니다. Hall 센서와 RFID 인식 결과를 대조해 현재 옷장 안의 옷을 확인합니다.</p>
    <div id="verifyStatus" class="status">현재 상태를 확인합니다.</div>
    <div class="find-list-header">
      <strong id="insideCount">현재 옷장 안: 확인 중...</strong>
      <button class="primary" onclick="loadClosetState()">↻ Hall + RFID 다시 확인</button>
    </div>
    <div id="insideClothes" class="clothing-list"></div>

    <hr class="section-divider">
    <h3>등록된 옷 관리 · 삭제 가능</h3>
    <p class="muted">등록된 모든 RFID/옷 정보입니다. 각 옷 카드 오른쪽의 빨간 <b>🗑 등록 삭제</b> 버튼으로 완전히 삭제할 수 있습니다.</p>
    <div class="find-list-header"><strong id="registeredCount">전체 등록: 확인 중...</strong></div>
    <div id="registeredClothes" class="registered-list"></div>

    <hr class="section-divider">
    <h3>직접 검색</h3>
    <p class="muted">옷 번호를 입력하거나 색상/종류를 말하면 Hall + RFID로 확인된 옷만 후보로 안내합니다.</p>
    <label>찾을 옷<input id="findQuery" placeholder="예: 3 또는 검정색 반팔티"></label>
    <div class="row"><button onclick="startVoice('find')">🎙 말하기</button><button class="primary" onclick="doFind()">찾기</button></div>
    <div id="status" class="status">옷을 선택하거나 검색해주세요.</div>
    <div class="row"><button onclick="completeFind()">찾기 완료</button><button onclick="home()">첫 화면</button></div>
  </section>`;
  loadClosetState();
  speak("Hall 센서와 RFID를 대조해 현재 옷장 상태를 확인합니다. 등록된 옷은 아래에서 삭제할 수도 있습니다.");
}

function candidateSpeechText(item, index){
  const parts=[];
  if(item.color) parts.push(item.color);
  if(item.category) parts.push(item.category);
  if(item.season) parts.push(item.season);
  if(item.extra) parts.push(item.extra);
  return `${index}번: ${parts.length ? parts.join(", ") : "상세 정보 없음"}`;
}

function renderFindCandidates(options){
  const s=document.getElementById("status");
  if(!s) return;
  if(!options?.length){
    s.innerHTML="해당 조건의 옷을 찾지 못했습니다.";
    return;
  }
  const rows=options.map((item,idx)=>`
    <button class="clothing-card" onclick="selectFindCandidate(${idx+1})">
      <span class="clothing-number">${idx+1}번</span>
      <span class="clothing-info">${esc(clothingInfoText(item))}</span>
      <span class="clothing-action">선택해서 위치 찾기 →</span>
    </button>`).join("");
  s.innerHTML=`<div><strong>조건에 맞는 옷 ${options.length}벌</strong></div><div class="clothing-list">${rows}</div><p class="muted">번호를 말하거나 눌러주세요.</p>`;
}

async function activateFind(query){
  const s=document.getElementById("status");
  if(s) s.textContent="위치를 찾는 중...";
  try{
    const d=await jsonPost("/api/find",{query:String(query)});

    if(d.status==="CANDIDATES"){
      state.findCandidates=d.options||[];
      renderFindCandidates(state.findCandidates);
      const spoken=state.findCandidates.map((item,idx)=>candidateSpeechText(item,idx+1)).join(". ");
      speak(`${d.message} ${spoken}. 찾을 옷의 번호를 말씀해주세요.`,true);
      return d;
    }

    // A concrete clothing number has been selected, so the backend has now
    // activated the matching slot's solenoid.  Clear the temporary candidate
    // numbering so a later number is treated as a clothing ID again.
    state.findCandidates=[];
    if(s){
      let details="";
      if(d.item){ details=`\n${clothingInfoText(d.item)}`; }
      s.textContent=(d.message || JSON.stringify(d))+details;
    }
    speak(d.message||"찾기 결과가 있습니다.",true);
    return d;
  }catch(e){
    if(s) s.textContent=e.message;
    speak(e.message,true);
    return null;
  }
}

async function selectFindCandidate(selectionNumber){
  const idx=Number(selectionNumber)-1;
  const item=state.findCandidates[idx];
  if(!item){
    const msg=`${selectionNumber}번 후보가 없습니다.`;
    const s=document.getElementById("status");
    if(s) s.textContent=msg;
    speak(msg,true);
    return;
  }
  const input=document.getElementById("findQuery");
  if(input) input.value=`옷 ${item.id}`;
  await activateFind(item.id);
}

async function selectInsideClothing(id){
  state.findCandidates=[];
  const input=document.getElementById("findQuery");
  if(input) input.value=String(id);
  await activateFind(id);
}

function parseCandidateSelection(text){
  const normalized=String(text||"").trim();
  const m=normalized.match(/(?:^|\s)(\d+)\s*번(?:\s|$|[을를이가은는])/);
  if(!m) return null;
  return Number(m[1]);
}

async function doFind(){
  const q=document.getElementById("findQuery")?.value.trim();
  if(!q)return;

  // After a feature search, phrases such as "2번" select the second candidate
  // rather than clothing ID 2.
  if(state.findCandidates.length){
    const n=parseCandidateSelection(q);
    if(n!==null){
      await selectFindCandidate(n);
      return;
    }
  }

  await activateFind(q);
}

async function completeFind(){
  try{ await jsonPost("/api/find/complete"); }catch(e){}
  const s=document.getElementById("status");
  if(s) s.textContent="찾기를 종료했습니다. 솔레노이드를 내렸습니다.";
  await loadClosetState();
}

function codiPage(){
  state.codiRecommendations=[];
  const savedLocation=localStorage.getItem("smartClosetCodiLocation")||"서울";
  app.innerHTML=`<section class="card">
    <div class="version-chip">날씨 · 색상 · 계절 · 선호도 코디</div>
    <h2>오늘의 코디 추천</h2>
    <p class="muted">현재 Hall + RFID로 옷장 안이 확인된 상의와 하의를 조합합니다. 추천 선택 시 두 위치의 솔레노이드가 함께 켜집니다.</p>
    <label>날씨 지역<input id="codiLocation" value="${esc(savedLocation)}" placeholder="예: 노원구, 강남구, 수원시"></label>
    <div class="row"><button onclick="startVoice('codi')">🎙 지역 말하기</button><button class="primary" onclick="requestCodiRecommendations()">코디 추천받기</button></div>
    <div id="codiStatus" class="status">지역을 입력하고 코디 추천받기를 눌러주세요.</div>
    <div id="codiWeather"></div>
    <div id="codiResults" class="codi-list"></div>
    <div class="row"><button onclick="completeCodi()">위치 안내 완료</button><button onclick="leaveCodi()">첫 화면</button></div>
  </section>`;
  speak("코디 추천입니다. 지역을 입력하고 코디 추천받기를 눌러주세요.");
}

function codiGarmentHtml(item,label){
  const hex=/^#[0-9a-fA-F]{6}$/.test(item?.color_hex||"")?item.color_hex:"#808080";
  return `<div class="codi-garment">
    <span class="color-swatch" style="background:${esc(hex)}"></span>
    <div><strong>${label} · 옷 ${Number(item.id)}</strong><div>${esc(clothingInfoText(item))}</div><div class="muted">${Number(item.slot)}번 홈 · 선호도 ${Number(item.preference||0)} · 착용 ${Number(item.wear_count||0)}회</div></div>
  </div>`;
}

function renderCodiRecommendations(){
  const box=document.getElementById("codiResults");
  if(!box) return;
  if(!state.codiRecommendations.length){
    box.innerHTML=`<div class="empty-state">추천 결과가 없습니다.</div>`;
    return;
  }
  box.innerHTML=state.codiRecommendations.map((result,index)=>`
    <article class="codi-card">
      <div class="codi-rank"><strong>${index+1}위</strong><span>${Number(result.score).toFixed(1)}점</span></div>
      ${codiGarmentHtml(result.top,"상의")}
      ${codiGarmentHtml(result.bottom,"하의")}
      <details><summary>추천 이유 보기</summary><ul>${(result.reasons||[]).map(reason=>`<li>${esc(reason)}</li>`).join("")}</ul></details>
      <div class="preference-row">
        <span>상의 선호</span><button onclick="adjustCodiPreference(${Number(result.top.id)},-1)">−</button><button onclick="adjustCodiPreference(${Number(result.top.id)},1)">+</button>
        <span>하의 선호</span><button onclick="adjustCodiPreference(${Number(result.bottom.id)},-1)">−</button><button onclick="adjustCodiPreference(${Number(result.bottom.id)},1)">+</button>
      </div>
      <button class="big primary" onclick="activateCodi(${index})">이 코디 위치 안내</button>
    </article>`).join("");
}

async function requestCodiRecommendations(){
  const location=document.getElementById("codiLocation")?.value.trim();
  const status=document.getElementById("codiStatus");
  const weatherBox=document.getElementById("codiWeather");
  if(!location){ if(status)status.textContent="지역명을 입력해주세요."; return; }
  localStorage.setItem("smartClosetCodiLocation",location);
  if(status) status.textContent="날씨를 확인하고 옷장 안의 상의·하의를 분석 중입니다...";
  if(weatherBox) weatherBox.innerHTML="";
  try{
    try{ await jsonPost("/api/find/complete"); }catch(e){}
    const data=await jsonPost("/api/codi/recommendations",{location,top_n:3});
    state.codiRecommendations=data.recommendations||[];
    const weather=data.weather||{};
    if(weatherBox) weatherBox.innerHTML=`<div class="weather-card"><strong>${esc(weather.input_location||location)} 날씨</strong><span>${esc(weather.description||"")}</span><span>기온 ${Number(weather.temperature).toFixed(1)}℃ · 체감 ${Number(weather.feels_like).toFixed(1)}℃ · 습도 ${Number(weather.humidity)}%</span><span>판정 계절: ${esc(data.current_season||"")}</span></div>`;
    renderCodiRecommendations();
    const ignored=(data.ignored_items||[]).length;
    if(status) status.textContent=`코디 ${state.codiRecommendations.length}개를 추천했습니다.${ignored?` 상의/하의 구분이 없는 옷 ${ignored}벌은 제외했습니다.`:""}`;
    const first=state.codiRecommendations[0];
    if(first) speak(`1위 코디는 상의 옷 ${Number(first.top.id)}, 하의 옷 ${Number(first.bottom.id)} 조합이며 ${Number(first.score).toFixed(1)}점입니다.`,true);
  }catch(e){
    state.codiRecommendations=[];
    renderCodiRecommendations();
    if(status) status.textContent=`코디 추천 실패: ${e.message}`;
    speak(`코디 추천에 실패했습니다. ${e.message}`,true);
  }
}

async function activateCodi(index){
  const result=state.codiRecommendations[Number(index)];
  const status=document.getElementById("codiStatus");
  if(!result) return;
  if(status) status.textContent="상의와 하의 위치를 확인하는 중입니다...";
  try{
    const data=await jsonPost("/api/codi/activate",{top_id:Number(result.top.id),bottom_id:Number(result.bottom.id)});
    if(status) status.textContent=data.message;
    showGlobalNotice(`📍 ${data.message}`);
    speak(data.message,true);
  }catch(e){
    if(status) status.textContent=`위치 안내 실패: ${e.message}`;
    speak(`위치 안내에 실패했습니다. ${e.message}`,true);
  }
}

async function adjustCodiPreference(itemId,delta){
  let current=0;
  for(const result of state.codiRecommendations){
    if(Number(result.top.id)===Number(itemId)) current=Number(result.top.preference||0);
    if(Number(result.bottom.id)===Number(itemId)) current=Number(result.bottom.preference||0);
  }
  const preference=Math.max(-5,Math.min(5,current+Number(delta)));
  try{
    const data=await jsonPost("/api/codi/preference",{item_id:Number(itemId),preference});
    for(const result of state.codiRecommendations){
      if(Number(result.top.id)===Number(itemId)) result.top.preference=data.item.preference;
      if(Number(result.bottom.id)===Number(itemId)) result.bottom.preference=data.item.preference;
    }
    renderCodiRecommendations();
    showGlobalNotice(`옷 ${Number(itemId)} 선호도: ${Number(data.item.preference)}`);
  }catch(e){ showGlobalNotice(`선호도 변경 실패: ${e.message}`); }
}

async function completeCodi(){
  try{ await jsonPost("/api/find/complete"); }catch(e){}
  const status=document.getElementById("codiStatus");
  if(status) status.textContent="코디 위치 안내를 종료하고 솔레노이드를 모두 내렸습니다.";
}

async function leaveCodi(){ await completeCodi(); home(); }

function startVoice(mode){
  const SR=window.SpeechRecognition||window.webkitSpeechRecognition; if(!SR){alert("Chrome/Edge의 localhost 또는 HTTPS에서 사용해주세요.");return;}
  if(state.listening)return; state.listening=true; state.voiceMode=mode; const rec=new SR(); state.recognition=rec; rec.lang="ko-KR"; rec.interimResults=false; rec.continuous=false; rec.maxAlternatives=1;
  if(mode==="review") speak("수정 내용을 한 문장으로 말씀해주세요.",true); else if(mode==="extra") speak("추가사항을 말씀해주세요.",true); else if(mode==="codi") speak("날씨를 확인할 지역을 말씀해주세요.",true); else speak("찾고 싶은 옷을 말씀해주세요.",true);
  rec.onresult=e=>{ const text=e.results[0][0].transcript.trim(); const box=document.getElementById("voiceText")||document.getElementById("status"); if(box)box.textContent=text; applyVoice(mode,text); };
  rec.onerror=e=>{ state.listening=false; const box=document.getElementById("voiceText")||document.getElementById("status"); if(box)box.textContent="음성 인식 오류: "+e.error; };
  rec.onend=()=>{state.listening=false;}; rec.start();
}
function cleanValue(v){ return String(v).replace(/^(은|는|이|가|을|를|으로|로|:)\s*/g,"").replace(/\s*(으로|로)?\s*(수정해줘|수정|바꿔줘|바꿔|변경해줘|변경|해줘|해주세요).*$/g,"").trim(); }
function extractReview(text){ const t=text.replace(/색깔/g,"색상").replace(/세부 ?종류|서브 ?카테고리/g,"종류"); const keys=[{k:"position",n:"구분"},{k:"category",n:"종류"},{k:"color",n:"색상"},{k:"pattern",n:"패턴"},{k:"season",n:"계절"}]; const found=[]; for(const x of keys){ const i=t.indexOf(x.n); if(i>=0)found.push({...x,i,start:i+x.n.length}); } found.sort((a,b)=>a.i-b.i); const out={}; found.forEach((x,j)=>{ const end=found[j+1]?.i ?? t.length; const v=cleanValue(t.slice(x.start,end)); if(v)out[x.k]=v; }); return out; }
function applyVoice(mode,text){ if(mode==="review"){ const p=extractReview(text); if(p.position && document.getElementById("position"))document.getElementById("position").value=p.position.includes("하의")?"하의":p.position.includes("상의")?"상의":""; if(p.category)document.getElementById("category").value=p.category; if(p.color)document.getElementById("color").value=p.color; if(p.pattern)document.getElementById("pattern").value=p.pattern; if(p.season)document.getElementById("season").value=p.season; saveReview(); speak("수정 내용을 반영했습니다.",true); } else if(mode==="extra"){ state.extra=text; const e=document.getElementById("extra"); if(e)e.value=text; speak("추가사항을 입력했습니다.",true); } else if(mode==="codi"){ const e=document.getElementById("codiLocation"); if(e)e.value=text; requestCodiRecommendations(); } else if(mode==="find"){ const e=document.getElementById("findQuery"); if(e)e.value=text; const n=state.findCandidates.length ? parseCandidateSelection(text) : null; if(n!==null) selectFindCandidate(n); else doFind(); } }

Object.assign(window,{home,registerPhoto,photoChanged,analyzePhoto,reviewPage,repeatAnalysis,extraPage,rfidPage,startAutomaticRegistration,cancelRegistrationToExtra,cancelRegistrationToHome,findPage,loadClosetState,loadInsideClothes,loadRegisteredClothes,deleteRegisteredClothing,selectInsideClothing,selectFindCandidate,doFind,completeFind,codiPage,requestCodiRecommendations,activateCodi,adjustCodiPreference,completeCodi,leaveCodi,startVoice,enableVoice,setRate});
home();
initializeHardwareEventMonitor().finally(()=>{
  setInterval(pollHardwareEvents,300);
});
