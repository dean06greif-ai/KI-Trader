"""Isolierte Offline-Testkonfiguration (außerhalb des Quellrepos)."""

import os
import socket
import sys
import types
from pathlib import Path


_ORIG_SOCKET_CONNECT = socket.socket.connect
_ORIG_SOCKET_CONNECT_EX = socket.socket.connect_ex
_ORIG_CREATE_CONNECTION = socket.create_connection


def _deny_network(*_args, **_kwargs):
    raise RuntimeError("Netzwerkzugriff im Offline-Test blockiert")


def _deny_socket_connect(self, *args, **kwargs):
    raise RuntimeError(f"Socket connect blockiert: {args[0] if args else 'unknown'}")


def _install_fake_dotenv():
    mod = sys.modules.get("dotenv") or types.ModuleType("dotenv")
    mod.load_dotenv = lambda *args, **kwargs: False
    mod.dotenv_values = lambda *args, **kwargs: {}
    sys.modules["dotenv"] = mod


def pytest_sessionstart(session):
    # Quellpfad zentral steuerbar halten (keine hardcodierten Secrets/Hosts).
    source_dir = Path(os.environ.get("KI_TRADER_SOURCE_DIR", "/app/review_source/backend")).resolve()
    if str(source_dir) not in sys.path:
        sys.path.insert(0, str(source_dir))

    # Credentials im Testprozess entfernen (NIE Werte loggen).
    kill_tokens = (
        "KEY",
        "TOKEN",
        "SECRET",
        "PASSWORD",
        "PASS",
        "MONGO",
        "DATABASE",
        "OPENAI",
        "GEMINI",
        "ANTHROPIC",
        "BITUNIX",
        "TELEGRAM",
    )
    for k in list(os.environ.keys()):
        up = k.upper()
        if any(t in up for t in kill_tokens):
            os.environ.pop(k, None)

    _install_fake_dotenv()

    # Import-Zeit und Laufzeit: HTTP/Socket vollständig sperren.
    socket.create_connection = _deny_network
    socket.socket.connect = _deny_socket_connect
    socket.socket.connect_ex = _deny_network


def pytest_sessionfinish(session, exitstatus):
    socket.create_connection = _ORIG_CREATE_CONNECTION
    socket.socket.connect = _ORIG_SOCKET_CONNECT
    socket.socket.connect_ex = _ORIG_SOCKET_CONNECT_EX
