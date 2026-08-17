#!/bin/bash
set -u
cd /Users/limchaekuk_mini/Projects/tmap-crawler
source venv/bin/activate
export ANDROID_HOME=~/Library/Android/sdk
export PATH="$ANDROID_HOME/platform-tools:$PATH"

TOTAL=${1:-120}
for i in $(seq 1 "$TOTAL"); do
  echo "=================== RUN $i/$TOTAL ($(date '+%H:%M:%S')) ==================="
  # appium 서버가 죽어있으면 재기동
  if ! curl -s -o /dev/null http://127.0.0.1:4723/status; then
    echo "appium 서버 재시작"
    export JAVA_HOME=/opt/homebrew/opt/openjdk@17
    export PATH="$JAVA_HOME/bin:$PATH"
    nohup appium > /tmp/appium.log 2>&1 &
    sleep 4
  fi
  python3 crawler.py
done
echo "=================== 전체 ${TOTAL}회 반복 완료 ==================="
