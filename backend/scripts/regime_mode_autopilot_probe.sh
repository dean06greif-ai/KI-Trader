#!/bin/bash
# Kurz-Autopilot je Regime-Anzahl gegen die LOKALE Preview (nie Prod): vergleicht
# Ø Richtungs-/Regime-Phase und Note für 3/5/9 Regime bei gleichen Einstellungen.
# Aufruf: API=http://localhost:8001 bash scripts/regime_mode_autopilot_probe.sh
API=${API:-http://localhost:8001}
TOK=$(curl -s -X POST "$API/api/auth/login" -H "Content-Type: application/json" \
  -d "{\"username\":\"$ADMIN_USER\",\"password\":\"$ADMIN_PASSWORD\"}" | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])")
for MODE in ${MODES:-3 5 9}; do
  JID=$(curl -s -X POST "$API/api/regime-lab/autopilot" -H "Authorization: Bearer $TOK" -H "Content-Type: application/json" \
    -d "{\"symbols\":[\"BTCUSDT\",\"ETHUSDT\"],\"timeframe\":\"1h\",\"days\":540,\"train_pct\":70,\"regime_mode\":$MODE,\"engine_config\":{\"regime_mode\":$MODE},\"max_rounds\":${ROUNDS:-25},\"plateau_rounds\":0,\"min_phase_days_target\":5,\"max_phase_days_target\":15,\"auto_chain\":false}" \
    | python3 -c "import sys,json;print(json.load(sys.stdin).get('job_id'))")
  echo "mode $MODE job $JID"
  while true; do
    S=$(curl -s "$API/api/regime-lab/status/$JID")
    ST=$(echo "$S" | python3 -c "import sys,json;print(json.load(sys.stdin).get('status'))")
    [ "$ST" != "running" ] && break
    sleep 5
  done
  echo "$S" | python3 -c "
import sys,json
j=json.load(sys.stdin); r=j.get('result') or {}; b=(r.get('best') or {}).get('metrics') or {}
print('  status', j.get('status'), j.get('error'))
print('  dir_phase', b.get('live_direction_phase_days'), 'regime_phase', b.get('avg_live_phase_days'),
      'score', (r.get('best') or {}).get('score'), 'grade', (r.get('quality') or {}).get('grade') or r.get('grade'))
print('  breakdown', ((r.get('score_breakdown') or {}).get('best') or {}).get('phase_targets'))"
done
