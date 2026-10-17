"""AP00: Isolierte Offline-Testbasis für die AP01–AP03 Solltests.

* sys.path zeigt auf das ECHTE Backend (/app/backend), keine Kopie.
* Mit KI_OFFLINE_TESTS=1 werden Credentials aus dem Prozess entfernt und
  Netzwerk-Sockets deny-by-default gesperrt (Abnahme AP00: ein Netzversuch
  scheitert vor Clientnutzung). Ohne das Flag laufen die Tests trotzdem
  offline, aber ohne globale Socket-Sperre (damit die restliche Suite
  weiter E2E-Tests fahren kann).
* Kein Server-Lifespan, keine .env-Werte in Tests, Fake-DB statt Mongo.
"""
import os
import socket
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[2]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_ORIG_CONNECT = socket.socket.connect
_ORIG_CREATE = socket.create_connection

_KILL = ("KEY", "TOKEN", "SECRET", "PASSWORD", "MONGO", "BITUNIX",
         "TELEGRAM", "GEMINI", "OPENAI", "MISTRAL", "GROQ", "SUPABASE")


def _deny(*_a, **_k):
    raise RuntimeError("Netzwerkzugriff im Offline-Test blockiert")


def pytest_sessionstart(session):
    if os.environ.get("KI_OFFLINE_TESTS") != "1":
        return
    for k in list(os.environ):
        if any(t in k.upper() for t in _KILL):
            os.environ.pop(k, None)
    socket.create_connection = _deny
    socket.socket.connect = lambda self, *a, **k: _deny()


def pytest_sessionfinish(session, exitstatus):
    socket.create_connection = _ORIG_CREATE
    socket.socket.connect = _ORIG_CONNECT
