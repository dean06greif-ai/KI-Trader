#!/bin/bash
# Manuelle E2E-Prüfung der Regime-Lab Job-Steuerung (Pause/Resume/Stop) gegen das laufende Backend.
API=$(grep REACT_APP_BACKEND_URL /app/frontend/.env | cut -d= -f2)
TOKEN=$(curl -s -X POST "$API/api/auth/login" -H "Content-Type: application/json" -d '{"username":"Admin","password":"Dean06Greif!/Admin"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
AID=${1:-ra_62d94ea6}
st() { curl -s "$API/api/regime-lab/status/$1" | python3 -c "import sys,json;d=json.load(sys.stdin);print('$2',{k:d.get(k) for k in ('status','progress','phase','pause','paused','paused_total_s')})"; }
J=$(curl -s -X POST "$API/api/regime-lab/$AID/optimize" -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d "{\"scope\":\"combined\",\"regime_id\":2,\"mode\":\"combo\",\"iterations\":500,\"max_rules\":6,\"deep_test\":true,\"min_trades\":3,\"optimize\":{\"tpsl\":true,\"leverage\":true}}")
echo "$J"
JID=$(echo "$J" | python3 -c "import sys,json;print(json.load(sys.stdin).get('job_id',''))")
sleep 3; st $JID RUNNING
curl -s -X POST "$API/api/regime-lab/pause/$JID" -H "Authorization: Bearer $TOKEN"; echo
sleep 4; st $JID PAUSED1
sleep 4; st $JID PAUSED2
curl -s -X POST "$API/api/regime-lab/resume/$JID" -H "Authorization: Bearer $TOKEN"; echo
sleep 5; st $JID RESUMED
curl -s -X POST "$API/api/regime-lab/stop/$JID" -H "Authorization: Bearer $TOKEN"; echo
for i in 1 2 3 4 5 6 7 8; do sleep 3; S=$(curl -s "$API/api/regime-lab/status/$JID"); echo "$S" | python3 -c "import sys,json;d=json.load(sys.stdin);print('AFTER_STOP',d['status'],d['progress'],d['phase'],'stopped_early=',(d.get('result') or {}).get('stopped_early'),'top5=',len((d.get('result') or {}).get('top5') or []))"; echo "$S" | grep -q '"status":"running"' || break; done
