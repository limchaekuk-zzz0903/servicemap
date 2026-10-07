// 실제 API 키 없이 응답 파싱과 Worker 라우팅을 검증한다 (fetch 를 가짜로 교체).
import { test } from "node:test";
import assert from "node:assert/strict";
import worker from "../src/worker.js";
import { parseTmapSearch } from "../src/providers.js";

const env = { ACCESS_TOKEN: "t", TMAP_APP_KEY: "k", NAVER_KEY_ID: "i", NAVER_KEY: "s", KAKAO_REST_KEY: "r" };
const json = (body, status = 200) => new Response(JSON.stringify(body), { status });

function mockFetch(handler) {
  const calls = [];
  globalThis.fetch = async (url, init = {}) => {
    calls.push({ url: String(url), init });
    return handler(String(url), init);
  };
  return calls;
}

const req = (path, token = "t") => new Request(`https://x.dev${path}`, { headers: { "X-Access-Token": token } });
const ETA = "/api/eta?olat=37.5665&olng=126.978&dlat=37.4979&dlng=127.0276";

test("토큰이 틀리면 401", async () => {
  const res = await worker.fetch(req(ETA, "wrong"), env);
  assert.equal(res.status, 401);
});

test("좌표가 범위 밖이면 400", async () => {
  const res = await worker.fetch(req("/api/eta?olat=0&olng=0&dlat=37&dlng=127"), env);
  assert.equal(res.status, 400);
});

test("3사 응답을 초 단위로 정규화", async () => {
  const calls = mockFetch((url) => {
    if (url.includes("tmap/routes")) return json({ features: [{ properties: { totalTime: 1500, totalDistance: 12000 } }] });
    if (url.includes("map-direction")) return json({ code: 0, route: { traoptimal: [{ summary: { duration: 1620000, distance: 11800 } }] } });
    if (url.includes("kakaomobility")) return json({ routes: [{ result_code: 0, summary: { duration: 1560, distance: 12100 } }] });
    throw new Error("unexpected " + url);
  });
  const body = await (await worker.fetch(req(ETA), env)).json();
  assert.deepEqual(body.results.tmap, { ok: true, durationSec: 1500, distanceM: 12000 });
  assert.deepEqual(body.results.naver, { ok: true, durationSec: 1620, distanceM: 11800 });
  assert.deepEqual(body.results.kakao, { ok: true, durationSec: 1560, distanceM: 12100 });

  const tmap = calls.find((c) => c.url.includes("tmap"));
  assert.equal(JSON.parse(tmap.init.body).searchOption, "0");
  assert.equal(JSON.parse(tmap.init.body).startX, "126.978");
  const naver = new URL(calls.find((c) => c.url.includes("map-direction")).url);
  assert.equal(naver.searchParams.get("start"), "126.978,37.5665");
  assert.equal(naver.searchParams.get("option"), "traoptimal");
  const kakao = calls.find((c) => c.url.includes("kakaomobility"));
  assert.equal(new URL(kakao.url).searchParams.get("priority"), "RECOMMEND");
  assert.equal(kakao.init.headers.Authorization, "KakaoAK r");
});

test("한 곳이 실패해도 나머지는 표시", async () => {
  mockFetch((url) => {
    if (url.includes("tmap/routes")) return json({ error: { message: "Invalid appKey" } }, 403);
    if (url.includes("map-direction")) return json({ code: 1, message: "출발지와 도착지가 동일" });
    return json({ routes: [{ result_code: 104, result_msg: "출발지와 도착지가 5m 이내" }] });
  });
  const body = await (await worker.fetch(req(ETA), env)).json();
  assert.equal(body.results.tmap.ok, false);
  assert.match(body.results.tmap.error, /403.*Invalid appKey/);
  assert.match(body.results.naver.error, /code 1/);
  assert.match(body.results.kakao.error, /104/);
});

test("키 미설정은 해당 사만 오류", async () => {
  mockFetch(() => json({ routes: [{ result_code: 0, summary: { duration: 60, distance: 1 } }] }));
  const body = await (await worker.fetch(req(ETA), { ACCESS_TOKEN: "t", KAKAO_REST_KEY: "r" })).json();
  assert.match(body.results.tmap.error, /미설정/);
  assert.equal(body.results.kakao.ok, true);
});

test("TMAP 검색: 204 빈 응답은 빈 목록", async () => {
  mockFetch(() => new Response(null, { status: 204 }));
  const body = await (await worker.fetch(req("/api/search?q=없는장소"), env)).json();
  assert.deepEqual(body.places, []);
});

test("TMAP 검색 결과 파싱: 입구 좌표 우선, 도로명 주소", () => {
  const places = parseTmapSearch({
    searchPoiInfo: { pois: { poi: [
      { name: "강남역", frontLat: "37.4981", frontLon: "127.0280", noorLat: "37.4979", noorLon: "127.0276",
        upperAddrName: "서울", middleAddrName: "강남구", newAddressList: { newAddress: [{ fullAddressRoad: "서울 강남구 강남대로 396" }] } },
      { name: "중심좌표만", frontLat: "0", frontLon: "0", noorLat: "37.1", noorLon: "127.1", upperAddrName: "경기" },
    ] } },
  });
  assert.deepEqual(places[0], { name: "강남역", address: "서울 강남구 강남대로 396", lat: 37.4981, lng: 127.028 });
  assert.equal(places[1].lat, 37.1);
  assert.equal(places[1].address, "경기");
});
