#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mock da REST API oficial do Palworld (porta 8212) — só para TESTES do painel.

Simula os endpoints /v1/api/* com dados falsos, permitindo desenvolver e
validar o painel sem o servidor de jogo ligado.

Uso:  python3 tests/mock_palworld.py
      ADMIN_PASSWORD=123 python3 tests/mock_palworld.py
"""

import base64
import json
import os
import random
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PORT = int(os.environ.get("MOCK_PORT", "8212"))
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "123")

START = time.time()
LAST_ANNOUNCE = None

# Formato idêntico ao que um servidor real de Palworld responde (v1.0.x):
# chaves em minúsculo, players embrulhados em {"players": [...]}, userId camelCase.
PLAYERS = [
    {
        "name": "Laure",
        "accountName": "Laure",
        "playerId": "C9AA58AC000000000000000000000001",
        "userId": "steam_76561198315797272",
        "iP": "192.168.1.50",
        "ping": 23.5,
        "location_x": -211402.89,
        "location_y": 84855.53,
        "level": 47,
    },
    {
        "name": "Zé dos Pals",
        "accountName": "zedospals",
        "playerId": "C9AA58AC000000000000000000000002",
        "userId": "steam_10987654321098765",
        "iP": "192.168.1.51",
        "ping": 87.2,
        "location_x": -1000.0,
        "location_y": 2000.0,
        "level": 12,
    },
]

BANNED = []

INFO = {
    "version": "v1.0.2.100933-mock",
    "servername": "Laure Kanda",
    "description": "Putaria Sem Fim",
    "worldguid": "2FCE18ADCA6F44C19F2AD7737221443A",
}

SETTINGS = {
    "ActiveUids": "",
    "AutoSaveSpan": 30.0,
    "BanListURL": "None",
    "BaseCampMaxNum": 128,
    "BaseCampWorkerMaxNum": 15,
    "bCanKickaPlayer": False,
    "bIsMultiplay": True,
    "bIsUseBackupSaveData": True,
    "CrossplayPlatforms": "(Steam,Xbox)",
    "DeathPenalty": "Item",
    "DropItemAliveMaxHours": 24.0,
    "ExpPlayerRemainRatio": 1.0,
    "GuildPlayerMaxNum": 4,
    "PalCaptureRate": 1.0,
    "PalSpawnNumRate": 1.0,
    "PlayTimeBeforeAutoKick": 999999.0,
    "RESTAPIEnabled": True,
    "RESTAPIPort": 8212,
    "ServerDescription": "Putaria Sem Fim",
    "ServerName": "Laure Kanda",
}


class MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        print(f"[MOCK] {self.command} {self.path}")

    # ---------- helpers ----------

    def _json(self, status, obj):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self):
        expected = "Basic " + base64.b64encode(f"admin:{ADMIN_PASSWORD}".encode()).decode()
        return self.headers.get("Authorization") == expected

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return {}

    # ---------- rotas ----------

    def do_GET(self):
        if not self._auth_ok():
            return self._json(401, {"error": "unauthorized"})
        p = self.path.split("?")[0]

        if p == "/v1/api/info":
            return self._json(200, INFO)
        if p == "/v1/api/players":
            return self._json(200, {"players": PLAYERS})
        if p == "/v1/api/settings":
            return self._json(200, SETTINGS)
        if p == "/v1/api/metrics":
            return self._json(200, {
                "currentplayernum": len(PLAYERS),
                "serverfps": random.randint(108, 120),
                "serverfpsaverage": round(random.uniform(108, 120), 3),
                "serverframetime": round(random.uniform(7.5, 9.5), 5),
                "maxplayernum": int(os.environ.get("PLAYERS", "4")),
                "days": 400,
                "uptime": int(time.time() - START),
                "basecampnum": 3,
            })
        return self._json(404, {"error": "not found"})

    def do_POST(self):
        if not self._auth_ok():
            return self._json(401, {"error": "unauthorized"})
        p = self.path.split("?")[0]
        body = self._body()

        global LAST_ANNOUNCE, START

        if p == "/v1/api/announce":
            LAST_ANNOUNCE = body.get("message", "")
            print(f"[MOCK] ANUNCIO: {LAST_ANNOUNCE}")
            return self._json(200, {})
        if p == "/v1/api/save":
            print("[MOCK] SAVE executado")
            return self._json(200, {})
        if p in ("/v1/api/kick", "/v1/api/ban"):
            uid = body.get("userid")
            achou = [pl for pl in PLAYERS
                     if pl["userId"] == uid or pl["playerId"] == uid]
            if not achou:
                return self._json(404, {"error": f"player {uid} not found"})
            if p.endswith("ban"):
                BANNED.append(uid)
            PLAYERS.remove(achou[0])
            print(f"[MOCK] {p.split('/')[-1].upper()}: {uid} ({body.get('message', '')})")
            return self._json(200, {})
        if p == "/v1/api/unban":
            uid = body.get("userid")
            if uid in BANNED:
                BANNED.remove(uid)
            return self._json(200, {})
        if p == "/v1/api/shutdown":
            waittime = body.get("waittime", 30)
            print(f"[MOCK] SHUTDOWN em {waittime}s: {body.get('message', '')}")
            START = time.time() - 3600  # simula religar depois
            PLAYERS.clear()
            return self._json(200, {})
        if p == "/v1/api/stop":
            print("[MOCK] STOP imediato")
            return self._json(200, {})
        return self._json(404, {"error": "not found"})


if __name__ == "__main__":
    print(f"Mock da API do Palworld em http://127.0.0.1:{PORT}/v1/api "
          f"(senha admin: {ADMIN_PASSWORD})")
    ThreadingHTTPServer(("127.0.0.1", PORT), MockHandler).serve_forever()
