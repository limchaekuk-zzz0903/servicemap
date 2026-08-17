#!/bin/bash
# 물리 단말(스테이지 빌드) 반복 크롤 + 라운드마다 서비스맵 HTML 자동 갱신.
# 사용: ./run_stage.sh [라운드수]   (기본 12라운드, 라운드당 TMAP_TIME_BUDGET_MIN 분)
set -u
cd "$(dirname "$0")"
source venv/bin/activate

export TMAP_TARGET=device
export TMAP_UDID=${TMAP_UDID:-R3CX405R5FM}
export ANDROID_HOME=${ANDROID_HOME:-~/Library/Android/sdk}
export JAVA_HOME=${JAVA_HOME:-/opt/homebrew/opt/openjdk@17}
export PATH="$JAVA_HOME/bin:$ANDROID_HOME/platform-tools:/opt/homebrew/bin:$PATH"
export TMAP_TIME_BUDGET_MIN=${TMAP_TIME_BUDGET_MIN:-20}

ROUNDS=${1:-12}
echo "스테이지 반복 크롤 시작: ${ROUNDS}라운드 x ${TMAP_TIME_BUDGET_MIN}분  ($(date '+%Y-%m-%d %H:%M:%S'))"

for i in $(seq 1 "$ROUNDS"); do
  echo "===== ROUND $i/$ROUNDS  $(date '+%H:%M:%S') ====="
  # appium 살아있는지 확인, 죽었으면 재기동
  if ! curl -s -o /dev/null http://127.0.0.1:4723/status; then
    echo "appium 재기동"
    nohup appium > /tmp/appium_stage.log 2>&1 &
    sleep 6
  fi

  python3 crawler.py

  # 라운드마다 서비스맵 갱신(성장 상황을 바로 확인 가능)
  if python3 scripts/build_service_map.py >/tmp/map_build.log 2>&1; then
    echo "  → service_map.html 갱신 완료 ($(grep -o '화면 [0-9]*개' /tmp/map_build.log | head -1))"
  else
    echo "  ! map 갱신 실패 (로그: /tmp/map_build.log)"
  fi
done

echo "===== 전체 ${ROUNDS}라운드 완료  $(date '+%H:%M:%S') ====="
