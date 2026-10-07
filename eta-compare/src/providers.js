// 3사 길찾기 API 호출 및 응답 정규화.
// 모든 provider 는 { durationSec, distanceM } 을 돌려주거나 예외를 던진다.
// 좌표는 모두 WGS84, 경로 옵션은 각 사의 "추천" 기준.

const TIMEOUT_MS = 6000;

export class ProviderError extends Error {}

async function fetchJson(url, init) {
  const res = await fetch(url, { ...init, signal: AbortSignal.timeout(TIMEOUT_MS) });
  const text = await res.text();
  let body = null;
  if (text) {
    try {
      body = JSON.parse(text);
    } catch {
      throw new ProviderError(`HTTP ${res.status}: 응답이 JSON 이 아님`);
    }
  }
  return { status: res.status, ok: res.ok, body };
}

function errorMessage(body) {
  return body?.error?.message || body?.error?.msg || body?.errorMessage || body?.message || body?.msg;
}

// TMAP 자동차 경로안내. searchOption 0 = 추천(교통최적+추천)
export async function tmapRoute(env, origin, dest) {
  if (!env.TMAP_APP_KEY) throw new ProviderError("TMAP_APP_KEY 미설정");
  const { status, ok, body } = await fetchJson("https://apis.openapi.sk.com/tmap/routes?version=1", {
    method: "POST",
    headers: { appKey: env.TMAP_APP_KEY, "Content-Type": "application/json", Accept: "application/json" },
    body: JSON.stringify({
      startX: String(origin.lng),
      startY: String(origin.lat),
      endX: String(dest.lng),
      endY: String(dest.lat),
      reqCoordType: "WGS84GEO",
      resCoordType: "WGS84GEO",
      searchOption: "0",
      trafficInfo: "N",
    }),
  });
  if (!ok) throw new ProviderError(`HTTP ${status}: ${errorMessage(body) ?? "TMAP 오류"}`);
  return parseTmapRoute(body);
}

export function parseTmapRoute(body) {
  const props = body?.features?.[0]?.properties;
  if (typeof props?.totalTime !== "number") throw new ProviderError("TMAP 응답에 totalTime 없음");
  return { durationSec: props.totalTime, distanceM: props.totalDistance ?? null };
}

// 네이버 Directions 5. traoptimal = 실시간 최적(추천)
export async function naverRoute(env, origin, dest) {
  if (!env.NAVER_KEY_ID || !env.NAVER_KEY) throw new ProviderError("NAVER_KEY_ID/NAVER_KEY 미설정");
  const qs = new URLSearchParams({
    start: `${origin.lng},${origin.lat}`,
    goal: `${dest.lng},${dest.lat}`,
    option: "traoptimal",
  });
  const { status, ok, body } = await fetchJson(`https://maps.apigw.ntruss.com/map-direction/v1/driving?${qs}`, {
    headers: { "x-ncp-apigw-api-key-id": env.NAVER_KEY_ID, "x-ncp-apigw-api-key": env.NAVER_KEY },
  });
  if (!ok) throw new ProviderError(`HTTP ${status}: ${errorMessage(body) ?? "네이버 오류"}`);
  return parseNaverRoute(body);
}

export function parseNaverRoute(body) {
  if (body?.code !== 0) throw new ProviderError(`네이버 code ${body?.code}: ${body?.message ?? "경로 없음"}`);
  const summary = body?.route?.traoptimal?.[0]?.summary;
  if (typeof summary?.duration !== "number") throw new ProviderError("네이버 응답에 duration 없음");
  // 네이버만 밀리초 단위
  return { durationSec: Math.round(summary.duration / 1000), distanceM: summary.distance ?? null };
}

// 카카오모빌리티 자동차 길찾기. priority RECOMMEND = 추천
export async function kakaoRoute(env, origin, dest) {
  if (!env.KAKAO_REST_KEY) throw new ProviderError("KAKAO_REST_KEY 미설정");
  const qs = new URLSearchParams({
    origin: `${origin.lng},${origin.lat}`,
    destination: `${dest.lng},${dest.lat}`,
    priority: "RECOMMEND",
    summary: "true",
  });
  const { status, ok, body } = await fetchJson(`https://apis-navi.kakaomobility.com/v1/directions?${qs}`, {
    headers: { Authorization: `KakaoAK ${env.KAKAO_REST_KEY}` },
  });
  if (!ok) throw new ProviderError(`HTTP ${status}: ${errorMessage(body) ?? "카카오 오류"}`);
  return parseKakaoRoute(body);
}

export function parseKakaoRoute(body) {
  const route = body?.routes?.[0];
  if (!route) throw new ProviderError("카카오 응답에 routes 없음");
  if (route.result_code !== 0) throw new ProviderError(`카카오 ${route.result_code}: ${route.result_msg ?? "경로 없음"}`);
  const summary = route.summary;
  if (typeof summary?.duration !== "number") throw new ProviderError("카카오 응답에 duration 없음");
  return { durationSec: summary.duration, distanceM: summary.distance ?? null };
}

export const PROVIDERS = { tmap: tmapRoute, naver: naverRoute, kakao: kakaoRoute };

// TMAP 장소(POI) 통합검색. 결과가 없으면 TMAP 은 204(빈 본문)를 준다.
export async function tmapSearch(env, keyword, center) {
  if (!env.TMAP_APP_KEY) throw new ProviderError("TMAP_APP_KEY 미설정");
  const qs = new URLSearchParams({
    version: "1",
    searchKeyword: keyword,
    reqCoordType: "WGS84GEO",
    resCoordType: "WGS84GEO",
    count: "15",
  });
  if (center) {
    qs.set("centerLon", String(center.lng));
    qs.set("centerLat", String(center.lat));
  }
  const { status, ok, body } = await fetchJson(`https://apis.openapi.sk.com/tmap/pois?${qs}`, {
    headers: { appKey: env.TMAP_APP_KEY, Accept: "application/json" },
  });
  if (!ok) throw new ProviderError(`HTTP ${status}: ${errorMessage(body) ?? "TMAP 검색 오류"}`);
  return parseTmapSearch(body);
}

export function parseTmapSearch(body) {
  const pois = body?.searchPoiInfo?.pois?.poi ?? [];
  return pois
    .map((p) => {
      // 입구 좌표(front)를 우선 사용하고 없으면 중심 좌표(noor)
      const lat = Number(p.frontLat) || Number(p.noorLat);
      const lng = Number(p.frontLon) || Number(p.noorLon);
      const road = p.newAddressList?.newAddress?.[0]?.fullAddressRoad;
      const jibun = [p.upperAddrName, p.middleAddrName, p.lowerAddrName, p.detailAddrName].filter(Boolean).join(" ");
      return { name: p.name, address: road || jibun, lat, lng };
    })
    .filter((p) => p.name && Number.isFinite(p.lat) && Number.isFinite(p.lng) && p.lat !== 0);
}
