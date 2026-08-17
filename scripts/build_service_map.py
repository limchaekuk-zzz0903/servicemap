#!/usr/bin/env python3
"""sitemap.json → 자기완결형 서비스 구조도 HTML 생성기.

크롤 결과(화면/전이/차단 액션)를 하나의 HTML 파일로 시각화한다. 스크린샷은
축소 썸네일(sips)로 base64 인라인 임베드하므로, 생성된 HTML 한 파일만 공유하면
누구나(크롤러/DB 없이) 전체 서비스 구조를 열어볼 수 있다.

사용법:
    python3 scripts/build_service_map.py [sitemap.json] [--out output/service_map.html]
"""
import argparse
import base64
import html
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from collections import Counter, defaultdict

import networkx as nx

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
DEFAULT_SITEMAP = os.path.join(ROOT, "output", "sitemap.json")
DEFAULT_OUT = os.path.join(ROOT, "output", "service_map.html")

THUMB_MAX = 320          # 썸네일 최대 변 길이(px)
THUMB_QUALITY = 55       # JPEG 품질

# 액티비티 → 기능 카테고리(색상/그룹핑용). 부분일치로 매칭.
CATEGORY_RULES = [
    ("홈·메인",   ["NewMain", "TransparentMain", "IntroActivity"], "#4f6ef2"),
    ("검색",      ["Search", "DestinationHistory"],                "#0ea5a4"),
    ("경로·주행", ["Route", "Navi", "Drive"],                      "#f5793b"),
    ("주변·서비스", ["LocalService", "Vertical", "Weather"],        "#10b981"),
    ("웹·광고",   ["IAB", "WebView", "InAppBrowser"],              "#a855f7"),
    ("내정보·계정", ["Member", "CarProfile", "Login", "Setting"],   "#ec4899"),
    ("시스템",    ["Chooser", "Permission"],                       "#64748b"),
]
CATEGORY_ORDER = [c[0] for c in CATEGORY_RULES] + ["기타"]
DEFAULT_COLOR = "#94a3b8"


def short_activity(activity: str) -> str:
    return (activity or "unknown").split(".")[-1]


def categorize(activity: str):
    name = short_activity(activity)
    for label, hints, color in CATEGORY_RULES:
        if any(h.lower() in name.lower() for h in hints):
            return label, color
    return "기타", DEFAULT_COLOR


_HAS_SIPS = shutil.which("sips") is not None


def make_thumbnail(png_path: str, tmpdir: str) -> str:
    """png을 축소 JPEG로 변환 후 data URI 반환. 실패 시 빈 문자열.

    크로스플랫폼: macOS면 내장 sips를 쓰고, 그 외(윈도우/리눅스)에서는 Pillow로
    폴백한다. Pillow가 없으면 `pip install pillow` 안내 후 썸네일 없이 진행한다."""
    if not png_path or not os.path.exists(png_path):
        return ""

    # 1) macOS 내장 sips
    if _HAS_SIPS:
        out = os.path.join(tmpdir, os.path.basename(png_path) + ".jpg")
        try:
            subprocess.run(
                ["sips", "-Z", str(THUMB_MAX), "-s", "format", "jpeg",
                 "-s", "formatOptions", str(THUMB_QUALITY), png_path, "--out", out],
                check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            with open(out, "rb") as f:
                return "data:image/jpeg;base64," + base64.b64encode(f.read()).decode("ascii")
        except Exception as exc:  # noqa: BLE001
            print(f"  ! sips 썸네일 실패 {png_path}: {exc}", file=sys.stderr)

    # 2) 크로스플랫폼 폴백: Pillow
    try:
        from PIL import Image
    except ImportError:
        if not getattr(make_thumbnail, "_warned", False):
            print("  ! 썸네일 생성기(sips/Pillow) 없음 → 썸네일 없이 진행. "
                  "이미지를 넣으려면 `pip install pillow`.", file=sys.stderr)
            make_thumbnail._warned = True
        return ""
    try:
        im = Image.open(png_path).convert("RGB")
        im.thumbnail((THUMB_MAX, THUMB_MAX))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=THUMB_QUALITY)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as exc:  # noqa: BLE001
        print(f"  ! Pillow 썸네일 실패 {png_path}: {exc}", file=sys.stderr)
        return ""


def build_activity_layout(screens, real_transitions, byfp):
    """액티비티 단위 그래프의 노드 좌표를 networkx로 계산해 개요 그래프에 사용."""
    g = nx.DiGraph()
    counts = Counter(short_activity(s["activity"]) for s in screens)
    for act, cnt in counts.items():
        g.add_node(act, count=cnt)
    weights = Counter()
    for t in real_transitions:
        f, to = byfp.get(t["from"]), byfp.get(t["to"])
        if not f or not to:
            continue
        fa, ta = short_activity(f["activity"]), short_activity(to["activity"])
        if fa != ta:
            weights[(fa, ta)] += 1
    for (fa, ta), w in weights.items():
        g.add_edge(fa, ta, weight=w)

    pos = nx.spring_layout(g, k=1.6, seed=7, iterations=200)
    return g, pos, weights


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("sitemap", nargs="?", default=DEFAULT_SITEMAP)
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--no-thumbs", action="store_true", help="썸네일 임베드 생략(디버그용)")
    args = ap.parse_args()

    data = json.load(open(args.sitemap, encoding="utf-8"))
    screens = data.get("screens", [])
    transitions = data.get("transitions", [])
    left_app = data.get("left_app_events", [])
    byfp = {s["fingerprint"]: s for s in screens}
    real_transitions = [t for t in transitions if not t.get("blocked")]

    print(f"화면 {len(screens)}개 / 전이 {len(real_transitions)}개 / 차단 액션 "
          f"{sum(len(s.get('blocked_actions', [])) for s in screens)}개")

    # ── 썸네일 생성 ────────────────────────────────────────────────────────────
    thumbs = {}
    if not args.no_thumbs:
        print("썸네일 생성 중(sips)...")
        with tempfile.TemporaryDirectory() as tmp:
            for i, s in enumerate(screens, 1):
                thumbs[s["fingerprint"]] = make_thumbnail(s.get("screenshot_path"), tmp)
                if i % 50 == 0:
                    print(f"  {i}/{len(screens)}")
        embedded = sum(1 for v in thumbs.values() if v)
        print(f"썸네일 임베드: {embedded}/{len(screens)}")

    # ── 인접(전이) 인덱스 ──────────────────────────────────────────────────────
    outgoing = defaultdict(list)
    incoming = defaultdict(list)
    for t in real_transitions:
        outgoing[t["from"]].append({"to": t["to"], "label": t.get("action_label", "")})
        incoming[t["to"]].append({"from": t["from"], "label": t.get("action_label", "")})

    # ── 화면 데이터(임베드용) ──────────────────────────────────────────────────
    screen_records = []
    for s in screens:
        fp = s["fingerprint"]
        cat, color = categorize(s["activity"])
        screen_records.append({
            "fp": fp,
            "activity": short_activity(s["activity"]),
            "label": s.get("label") or short_activity(s["activity"]),
            "depth": s.get("depth", 0),
            "cat": cat,
            "color": color,
            "discovered": (s.get("discovered_at") or "")[:19].replace("T", " "),
            "blocked": s.get("blocked_actions", []),
            "out": outgoing.get(fp, []),
            "inc": incoming.get(fp, []),
            "thumb": thumbs.get(fp, ""),
        })

    # ── 액티비티 개요 그래프 좌표 ──────────────────────────────────────────────
    g, pos, weights = build_activity_layout(screens, real_transitions, byfp)
    act_meta = {}
    for act in g.nodes():
        sample = next(s for s in screens if short_activity(s["activity"]) == act)
        cat, color = categorize(sample["activity"])
        x, y = pos[act]
        act_meta[act] = {
            "name": act, "count": g.nodes[act]["count"],
            "cat": cat, "color": color, "x": float(x), "y": float(y),
        }
    act_edges = [{"from": fa, "to": ta, "w": w} for (fa, ta), w in weights.items()]

    # ── 요약 통계 ──────────────────────────────────────────────────────────────
    blocked_flat = []
    for s in screens:
        for b in s.get("blocked_actions", []):
            blocked_flat.append({"screen": s["fingerprint"],
                                 "label": b.get("action_label", ""),
                                 "reason": b.get("block_reason", "")})
    depths = [s.get("depth", 0) for s in screens] or [0]
    cat_counts = Counter(categorize(s["activity"])[0] for s in screens)
    crawl_date = max((s.get("discovered_at", "") for s in screens), default="")[:10]

    summary = {
        "screens": len(screens),
        "transitions": len(real_transitions),
        "activities": len(g.nodes()),
        "blocked": len(blocked_flat),
        "leftApp": len(left_app),
        "maxDepth": max(depths),
        "crawlDate": crawl_date,
        "catCounts": [{"cat": c, "n": cat_counts.get(c, 0)} for c in CATEGORY_ORDER if cat_counts.get(c)],
    }

    payload = {
        "summary": summary,
        "screens": screen_records,
        "activities": list(act_meta.values()),
        "actEdges": act_edges,
        "blocked": blocked_flat,
        "categoryOrder": CATEGORY_ORDER,
    }

    html_out = render_html(payload)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        f.write(html_out)
    size_mb = os.path.getsize(args.out) / 1e6
    print(f"\n생성 완료: {args.out}  ({size_mb:.1f} MB)")


def render_html(payload: dict) -> str:
    data_json = json.dumps(payload, ensure_ascii=False)
    # data_json 안의 </script> 방지
    data_json = data_json.replace("</", "<\\/")
    return HTML_TEMPLATE.replace("__DATA__", data_json)


# HTML/CSS/JS 템플릿은 별도 파일에서 로드(가독성). 없으면 인라인 사용.
_TEMPLATE_PATH = os.path.join(HERE, "service_map_template.html")
if os.path.exists(_TEMPLATE_PATH):
    HTML_TEMPLATE = open(_TEMPLATE_PATH, encoding="utf-8").read()
else:
    HTML_TEMPLATE = "<!doctype html><meta charset=utf-8><body><script>const DATA=__DATA__;document.body.textContent=JSON.stringify(DATA.summary)</script>"


if __name__ == "__main__":
    main()
