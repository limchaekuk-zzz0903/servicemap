// Cloudflare Worker: 정적 웹(public/)은 assets 가 서빙하고, 여기서는 /api/* 만 처리한다.
//   GET /api/search?q=키워드&lat=&lng=   → TMAP 장소 검색
//   GET /api/eta?olat=&olng=&dlat=&dlng= → 3사 소요시간 동시 조회
// 모든 /api 요청은 X-Access-Token 헤더가 ACCESS_TOKEN 과 같아야 한다(남이 내 키로 호출하는 것 방지).

import { PROVIDERS, tmapSearch } from "./providers.js";

function json(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json; charset=utf-8", "Cache-Control": "no-store" },
  });
}

function parsePoint(params, latKey, lngKey) {
  const lat = Number(params.get(latKey));
  const lng = Number(params.get(lngKey));
  // 대한민국 대략 범위
  if (!(lat >= 32 && lat <= 39.5 && lng >= 124 && lng <= 132.5)) return null;
  return { lat, lng };
}

async function handleEta(env, params) {
  const origin = parsePoint(params, "olat", "olng");
  const dest = parsePoint(params, "dlat", "dlng");
  if (!origin || !dest) return json({ error: "출발지/목적지 좌표가 올바르지 않습니다" }, 400);

  const queriedAt = Date.now();
  const entries = await Promise.all(
    Object.entries(PROVIDERS).map(async ([key, fn]) => {
      try {
        return [key, { ok: true, ...(await fn(env, origin, dest)) }];
      } catch (e) {
        const msg = e?.name === "TimeoutError" ? "응답 시간 초과" : e?.message || String(e);
        return [key, { ok: false, error: msg }];
      }
    }),
  );
  return json({ queriedAt, origin, dest, results: Object.fromEntries(entries) });
}

async function handleSearch(env, params) {
  const q = (params.get("q") || "").trim();
  if (!q || q.length > 60) return json({ error: "검색어를 입력하세요" }, 400);
  try {
    const places = await tmapSearch(env, q, parsePoint(params, "lat", "lng"));
    return json({ places });
  } catch (e) {
    return json({ error: e?.message || String(e) }, 502);
  }
}

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith("/api/")) return new Response("Not found", { status: 404 });
    if (request.method !== "GET") return json({ error: "Method not allowed" }, 405);

    if (!env.ACCESS_TOKEN) return json({ error: "서버에 ACCESS_TOKEN 이 설정되지 않았습니다" }, 500);
    if (request.headers.get("X-Access-Token") !== env.ACCESS_TOKEN) return json({ error: "접근 토큰이 올바르지 않습니다" }, 401);

    if (url.pathname === "/api/eta") return handleEta(env, url.searchParams);
    if (url.pathname === "/api/search") return handleSearch(env, url.searchParams);
    return json({ error: "Not found" }, 404);
  },
};
