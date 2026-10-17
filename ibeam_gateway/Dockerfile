# IBeam (IBKR Client-Portal-Gateway mit Auto-Login) + Token-Proxy für Render.
#
# Warum ein Proxy? Render leitet pro Web-Service genau EINEN Port (PORT) weiter
# und erkennt automatisch den ERSTEN offenen Port des Containers. Bei IBeam ist
# das der Health-Server (5001, Python http.server) – NICHT das Gateway (5000,
# Java, nur HTTPS mit Self-Signed-Zertifikat). Ergebnis: "Error response 501/404".
#
# Der Proxy lauscht auf $PORT, verlangt einen Shared-Token (X-Gateway-Token)
# und reicht Anfragen an das Gateway (https://localhost:5000) weiter.
# /livez und /readyz gehen ohne Token an den IBeam-Health-Server (5001).
FROM voyz/ibeam:latest

USER root
COPY proxy.py /srv/proxy/proxy.py
COPY start.sh /srv/proxy/start.sh
RUN chmod 755 /srv/proxy/start.sh /srv/proxy/proxy.py && \
    chown -R basic_user:basic_group /srv/proxy
USER basic_user

WORKDIR /srv/ibeam
ENV PORT=10000
EXPOSE 10000
CMD ["/srv/proxy/start.sh"]
