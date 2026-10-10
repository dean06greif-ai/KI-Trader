#!/bin/bash
# Startet den Token-Proxy (Render-Port) und danach IBeam (Gateway + Auto-Login).
set -e
cd /srv/ibeam
python3 /srv/proxy/proxy.py &
exec python ibeam_starter.py
