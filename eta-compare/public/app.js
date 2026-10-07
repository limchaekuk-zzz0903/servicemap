// ETA 비교 모바일 웹: 현재 위치 → 목적지 검색 → 3사 소요시간 → 선택 앱 실행 → 도착 기록

const PROVIDERS = {
  tmap: {
    label: "TMAP",
    scheme: "tmap",
    androidPackage: "com.skt.tmap.ku",
    iosStore: "https://apps.apple.com/kr/app/id431589174",
    // TMAP 은 현재 위치에서 바로 안내. X=경도, Y=위도
    path: (o, d) => `route?rGoName=${enc(d.name)}&rGoX=${d.lng}&rGoY=${d.lat}`,
  },
  naver: {
    label: "네이버지도",
    scheme: "nmap",
    androidPackage: "com.nhn.android.nmap",
    iosStore: "https://apps.apple.com/kr/app/id311867728",
    // 현재 위치에서 바로 내비게이션 시작. appname 필수(모바일 웹은 페이지 URL)
    path: (o, d) => `navigation?dlat=${d.lat}&dlng=${d.lng}&dname=${enc(d.name)}&appname=${enc(location.origin)}`,
  },
  kakao: {
    label: "카카오맵",
    scheme: "kakaomap",
    androidPackage: "net.daum.android.map",
    iosStore: "https://apps.apple.com/kr/app/id304608425",
    // 자동차 길찾기 화면이 열리고 앱에서 '안내 시작'을 눌러야 함. 좌표는 위도,경도 순
    path: (o, d) => `route?sp=${o.lat},${o.lng}&ep=${d.lat},${d.lng}&by=CAR`,
  },
};
const KEYS = Object.keys(PROVIDERS);
const STALE_MS = 3 * 60 * 1000; // 이보다 오래된 조회 결과로는 앱을 실행하지 않고 먼저 새로고침
const ua = navigator.userAgent;
const isAndroid = /Android/i.test(ua);
const isIOS = /iPhone|iPad|iPod/i.test(ua) || (/Macintosh/.test(ua) && navigator.maxTouchPoints > 1);
const isInApp = /KAKAOTALK|NAVER\(inapp|Instagram|FBAN|FBAV|Line\//i.test(ua);

const $ = (id) => document.getElementById(id);
const enc = encodeURIComponent;

const state = { origin: null, dest: null, eta: null, loading: false };

// ---------- 저장소 (개인용이라 브라우저 localStorage) ----------
const store = {
  get(key, fallback) {
    try {
      const v = localStorage.getItem(key);
      return v == null ? fallback : JSON.parse(v);
    } catch {
      return fallback;
    }
  },
  set(key, value) {
    try {
      localStorage.setItem(key, JSON.stringify(value));
    } catch {
      /* 사생활 보호 모드 등: 저장만 실패 */
    }
  },
};
const getToken = () => store.get("eta.token", "");
const getLog = () => store.get("eta.log", []);
const setLog = (log) => store.set("eta.log", log);
const getTrip = () => store.get("eta.trip", null);
const setTrip = (trip) => store.set("eta.trip", trip);

function askToken() {
  const t = prompt("접근 토큰(ACCESS_TOKEN)을 입력하세요", getToken());
  if (t != null) store.set("eta.token", t.trim());
  return t != null;
}

// ---------- 포맷 ----------
function fmtDuration(sec) {
  const m = Math.round(sec / 60);
  if (m < 60) return `${m}분`;
  return `${Math.floor(m / 60)}시간 ${m % 60 ? `${m % 60}분` : ""}`.trim();
}
function fmtClock(ms) {
  return new Date(ms).toLocaleTimeString("ko-KR", { hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Seoul" });
}
function fmtDate(ms) {
  return new Date(ms).toLocaleString("ko-KR", { month: "numeric", day: "numeric", weekday: "short", hour: "2-digit", minute: "2-digit", hour12: false, timeZone: "Asia/Seoul" });
}
function fmtKm(m) {
  return m == null ? "" : `${(m / 1000).toFixed(1)}km`;
}
function fmtDiffMin(sec) {
  const m = Math.round(sec / 60);
  return `${m > 0 ? "+" : ""}${m}분`;
}
function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c]);
}

let toastTimer;
function toast(html, ms = 4000) {
  const el = $("toast");
  el.innerHTML = html;
  el.hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => (el.hidden = true), ms);
}

// ---------- API ----------
async function api(path, params) {
  if (!getToken() && !askToken()) throw new Error("접근 토큰이 필요합니다");
  const res = await fetch(`${path}?${new URLSearchParams(params)}`, { headers: { "X-Access-Token": getToken() } });
  const body = await res.json().catch(() => ({}));
  if (res.status === 401) {
    askToken();
    throw new Error(body.error || "접근 토큰이 올바르지 않습니다");
  }
  if (!res.ok) throw new Error(body.error || `HTTP ${res.status}`);
  return body;
}

// ---------- 현재 위치 ----------
function locate() {
  $("origin-text").textContent = "현재 위치 확인 중…";
  if (!("geolocation" in navigator)) {
    $("origin-text").textContent = "이 브라우저는 위치를 지원하지 않습니다";
    return;
  }
  navigator.geolocation.getCurrentPosition(
    (pos) => {
      const { latitude: lat, longitude: lng, accuracy } = pos.coords;
      state.origin = { lat: +lat.toFixed(6), lng: +lng.toFixed(6) };
      $("origin-text").textContent = `현재 위치 (오차 약 ${Math.round(accuracy)}m)`;
      if (state.dest) loadEta();
    },
    (err) => {
      $("origin-text").textContent =
        err.code === err.PERMISSION_DENIED ? "위치 권한이 거부되었습니다. 브라우저 설정에서 허용하세요" : "현재 위치를 가져오지 못했습니다";
    },
    { enableHighAccuracy: true, timeout: 15000, maximumAge: 30000 },
  );
}

// ---------- 목적지 검색 ----------
async function search(q) {
  const list = $("search-results");
  list.hidden = false;
  list.innerHTML = `<li class="empty">검색 중…</li>`;
  try {
    const params = { q };
    if (state.origin) Object.assign(params, { lat: state.origin.lat, lng: state.origin.lng });
    const { places } = await api("/api/search", params);
    if (!places.length) {
      list.innerHTML = `<li class="empty">검색 결과가 없습니다</li>`;
      return;
    }
    list.innerHTML = "";
    places.forEach((p) => {
      const li = document.createElement("li");
      li.innerHTML = `<button type="button">${esc(p.name)}<span class="addr">${esc(p.address)}</span></button>`;
      li.firstChild.addEventListener("click", () => selectDest(p));
      list.appendChild(li);
    });
  } catch (e) {
    list.innerHTML = `<li class="empty">${esc(e.message)}</li>`;
  }
}

function selectDest(p) {
  state.dest = p;
  $("search-results").hidden = true;
  $("search-form").hidden = true;
  $("dest").hidden = false;
  $("dest-name").textContent = p.name;
  $("dest-addr").textContent = p.address;
  $("search-input").blur();
  loadEta();
}

function clearDest() {
  state.dest = null;
  state.eta = null;
  $("dest").hidden = true;
  $("search-form").hidden = false;
  $("eta-block").hidden = true;
  $("search-input").focus();
}

// ---------- 3사 조회 ----------
async function loadEta() {
  if (!state.dest) return;
  $("eta-block").hidden = false;
  if (!state.origin) {
    $("cards").innerHTML = `<div class="empty">현재 위치를 확인하는 중입니다</div>`;
    return;
  }
  state.loading = true;
  state.eta = null;
  renderCards();
  try {
    state.eta = await api("/api/eta", {
      olat: state.origin.lat,
      olng: state.origin.lng,
      dlat: state.dest.lat,
      dlng: state.dest.lng,
    });
  } catch (e) {
    $("cards").innerHTML = `<div class="empty">${esc(e.message)}</div>`;
    state.loading = false;
    return;
  }
  state.loading = false;
  renderCards();
}

function renderCards() {
  const cards = $("cards");
  cards.innerHTML = "";
  const results = state.eta?.results;
  const okDurations = results ? KEYS.filter((k) => results[k]?.ok).map((k) => results[k].durationSec) : [];
  const fastest = okDurations.length > 1 ? Math.min(...okDurations) : null;

  for (const key of KEYS) {
    const p = PROVIDERS[key];
    const r = results?.[key];
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "card" + (state.loading ? " loading" : "");
    btn.dataset.p = key;
    let right = `<div class="dur">…</div>`;
    let sub = "";
    if (r?.ok) {
      const badge = fastest != null && r.durationSec === fastest ? `<span class="badge">가장 빠름</span>` : "";
      right = `<div class="dur">${fmtDuration(r.durationSec)}</div><div class="eta">${fmtClock(state.eta.queriedAt + r.durationSec * 1000)} 도착</div>`;
      sub = `<div class="sub">${fmtKm(r.distanceM)}${badge}</div>`;
    } else if (r) {
      right = `<div class="dur">—</div>`;
      sub = `<div class="err">${esc(r.error)}</div>`;
    }
    btn.innerHTML = `<span class="bar"></span><div><div class="name">${p.label}</div>${sub}</div><div class="time">${right}</div>`;
    btn.addEventListener("click", () => onCardTap(key));
    cards.appendChild(btn);
  }
  renderAge();
}

function renderAge() {
  const el = $("eta-age");
  if (!state.eta) {
    el.textContent = state.loading ? "3사 조회 중…" : "";
    return;
  }
  const min = Math.floor((Date.now() - state.eta.queriedAt) / 60000);
  el.textContent = `${fmtClock(state.eta.queriedAt)} 조회${min >= 1 ? ` · ${min}분 전` : ""}`;
}

// ---------- 앱 실행 ----------
function onCardTap(key) {
  if (state.loading || !state.eta || !state.dest) return;
  if (Date.now() - state.eta.queriedAt > STALE_MS) {
    toast("조회한 지 오래되어 새로 조회합니다. 결과를 확인한 뒤 다시 눌러주세요.");
    loadEta();
    return;
  }
  startTrip(key);
  launchApp(key, state.origin, state.dest);
}

function launchApp(key, origin, dest) {
  const p = PROVIDERS[key];
  const path = p.path(origin, dest);
  if (isAndroid) {
    // 앱이 없으면 Chrome 이 Play 스토어로 보내준다
    const fallback = `https://play.google.com/store/apps/details?id=${p.androidPackage}`;
    location.href = `intent://${path}#Intent;scheme=${p.scheme};package=${p.androidPackage};S.browser_fallback_url=${enc(fallback)};end`;
    return;
  }
  location.href = `${p.scheme}://${path}`;
  // iOS 는 앱 설치 여부를 알 수 없으므로, 페이지가 그대로 보이면 스토어 링크를 안내
  setTimeout(() => {
    if (document.visibilityState === "visible") {
      const store = isIOS ? p.iosStore : `https://play.google.com/store/apps/details?id=${p.androidPackage}`;
      toast(`${p.label} 앱이 열리지 않았나요? <a href="${store}" target="_blank" rel="noopener">설치하기</a>`, 8000);
    }
  }, 2500);
}

// ---------- 운행 기록 ----------
function startTrip(key) {
  const results = {};
  for (const k of KEYS) {
    const r = state.eta.results[k];
    results[k] = r?.ok ? { durationSec: r.durationSec, distanceM: r.distanceM } : { error: r?.error ?? "없음" };
  }
  const trip = {
    id: `${Date.now()}`,
    queriedAt: state.eta.queriedAt,
    launchedAt: Date.now(),
    origin: state.origin,
    dest: { name: state.dest.name, address: state.dest.address, lat: state.dest.lat, lng: state.dest.lng },
    chosen: key,
    results,
    arrivedAt: null,
  };
  // 도착 처리 안 한 이전 운행이 있으면 미도착 상태로 기록에 남긴다
  const prev = getTrip();
  if (prev) setLog([prev, ...getLog()]);
  setTrip(trip);
  renderTrip();
}

function renderTrip() {
  const trip = getTrip();
  $("trip").hidden = !trip;
  if (!trip) return;
  $("trip-dest").textContent = trip.dest.name;
  $("trip-meta").textContent = `${PROVIDERS[trip.chosen].label} · ${fmtClock(trip.launchedAt)} 출발`;
}

function arrive() {
  const trip = getTrip();
  if (!trip) return;
  trip.arrivedAt = Date.now();
  setLog([trip, ...getLog()]);
  setTrip(null);
  renderTrip();
  const actual = (trip.arrivedAt - trip.launchedAt) / 1000;
  toast(`도착 기록 완료 · 실제 ${fmtDuration(actual)}`);
  renderLog();
}

function cancelTrip() {
  if (!confirm("이번 운행을 기록하지 않고 취소할까요?")) return;
  setTrip(null);
  renderTrip();
}

// 예측 오차(초): 실제 소요시간 - 예측 소요시간. +면 예측보다 늦게 도착
function tripErrors(t) {
  if (!t.arrivedAt) return null;
  const actual = (t.arrivedAt - t.launchedAt) / 1000;
  const out = {};
  for (const k of KEYS) out[k] = t.results[k]?.durationSec != null ? actual - t.results[k].durationSec : null;
  return { actual, errors: out };
}

function renderLog() {
  const log = getLog();
  $("log-count").textContent = log.length ? `(${log.length})` : "";

  // 3사 평균 절대오차
  const sums = Object.fromEntries(KEYS.map((k) => [k, { abs: 0, n: 0 }]));
  for (const t of log) {
    const e = tripErrors(t);
    if (!e) continue;
    for (const k of KEYS) if (e.errors[k] != null) (sums[k].abs += Math.abs(e.errors[k])), sums[k].n++;
  }
  $("log-summary").innerHTML = KEYS.map((k) => {
    const s = sums[k];
    const v = s.n ? `${Math.round(s.abs / s.n / 60)}분` : "—";
    return `<div class="cell"><div class="v">${v}</div><div class="k">${PROVIDERS[k].label}<br>평균오차 · ${s.n}건</div></div>`;
  }).join("");

  const list = $("log-list");
  if (!log.length) {
    list.innerHTML = `<li class="empty">아직 기록이 없습니다. 조회 화면에서 앱을 실행하고 도착 버튼을 누르면 쌓입니다.</li>`;
    return;
  }
  list.innerHTML = log
    .map((t) => {
      const e = tripErrors(t);
      const rows = KEYS.map((k) => {
        const r = t.results[k];
        const pred = r?.durationSec != null ? fmtDuration(r.durationSec) : "오류";
        const err = e?.errors[k] != null ? `<span class="${e.errors[k] > 0 ? "pos" : "neg"}">${fmtDiffMin(e.errors[k])}</span>` : "—";
        return `<tr class="${k === t.chosen ? "chosen" : ""}"><td>${PROVIDERS[k].label}${k === t.chosen ? " ✓" : ""}</td><td>${pred}</td><td>${err}</td></tr>`;
      }).join("");
      const status = e ? `실제 ${fmtDuration(e.actual)}` : `<span class="muted">미도착</span>`;
      return `<li>
        <div class="head"><strong>${esc(t.dest.name)}</strong><button class="btn ghost small" data-del="${t.id}">삭제</button></div>
        <div class="muted small-text">${fmtDate(t.launchedAt)} 출발 · ${status}</div>
        <table><tr><th>앱</th><th>예측</th><th>오차</th></tr>${rows}</table>
      </li>`;
    })
    .join("");
}

function exportCsv() {
  const log = getLog();
  if (!log.length) return toast("내보낼 기록이 없습니다");
  const header = ["출발시각", "도착시각", "목적지", "주소", "선택앱", "실제소요(분)"];
  for (const k of KEYS) header.push(`${PROVIDERS[k].label} 예측(분)`, `${PROVIDERS[k].label} 오차(분)`, `${PROVIDERS[k].label} 거리(km)`);
  const iso = (ms) => (ms ? new Date(ms + 9 * 3600e3).toISOString().slice(0, 16).replace("T", " ") : "");
  const rows = log.map((t) => {
    const e = tripErrors(t);
    const row = [iso(t.launchedAt), iso(t.arrivedAt), t.dest.name, t.dest.address, PROVIDERS[t.chosen].label, e ? (e.actual / 60).toFixed(1) : ""];
    for (const k of KEYS) {
      const r = t.results[k];
      row.push(r?.durationSec != null ? (r.durationSec / 60).toFixed(1) : "", e?.errors[k] != null ? (e.errors[k] / 60).toFixed(1) : "", r?.distanceM != null ? (r.distanceM / 1000).toFixed(1) : "");
    }
    return row;
  });
  const csv = [header, ...rows].map((r) => r.map((c) => `"${String(c ?? "").replace(/"/g, '""')}"`).join(",")).join("\r\n");
  const blob = new Blob(["﻿" + csv], { type: "text/csv;charset=utf-8" }); // BOM: 엑셀 한글 깨짐 방지
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `eta-log-${new Date().toISOString().slice(0, 10)}.csv`;
  a.click();
  setTimeout(() => URL.revokeObjectURL(a.href), 1000);
}

// ---------- 화면 전환 / 이벤트 ----------
function showView(view) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  $("view-main").hidden = view !== "main";
  $("view-log").hidden = view !== "log";
  if (view === "log") renderLog();
}

document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));
$("btn-token").addEventListener("click", askToken);
$("btn-locate").addEventListener("click", locate);
$("btn-refresh").addEventListener("click", loadEta);
$("btn-dest-clear").addEventListener("click", clearDest);
$("btn-arrive").addEventListener("click", arrive);
$("btn-trip-cancel").addEventListener("click", cancelTrip);
$("btn-export").addEventListener("click", exportCsv);
$("btn-clear-log").addEventListener("click", () => {
  if (confirm("모든 운행 기록을 삭제할까요?")) (setLog([]), renderLog());
});
$("log-list").addEventListener("click", (ev) => {
  const id = ev.target.closest("[data-del]")?.dataset.del;
  if (id && confirm("이 기록을 삭제할까요?")) (setLog(getLog().filter((t) => t.id !== id)), renderLog());
});
$("search-form").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const q = $("search-input").value.trim();
  if (q) search(q);
});
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState === "visible") renderAge();
});
setInterval(renderAge, 30000);

$("inapp-notice").hidden = !isInApp;
renderTrip();
locate();
