"""Cliente do language_server local do Antigravity.

Detecta porta e token de CSRF sozinho (via psutil), em qualquer maquina.
Nao imprime o token. Funciona em Windows/Mac/Linux (nome de processo varia).
"""
import json
import ssl
import struct
import urllib.request
import urllib.error

import psutil

SVC = "exa.language_server_pb.LanguageServerService"
_PROC_NAMES = ("language_server", "language_server.exe")


def _find_process():
    for p in psutil.process_iter(["name"]):
        try:
            if (p.info["name"] or "").lower() in _PROC_NAMES:
                return p
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return None


def _csrf_from(proc):
    try:
        cmd = proc.cmdline()
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return None
    for i, tok in enumerate(cmd):
        if tok == "--csrf_token" and i + 1 < len(cmd):
            return cmd[i + 1]
        if tok.startswith("--csrf_token="):
            return tok.split("=", 1)[1]
    return None


def _ports_from(proc):
    ports = []
    try:
        for c in proc.net_connections(kind="inet"):
            if c.status == psutil.CONN_LISTEN and c.laddr:
                ports.append(c.laddr.port)
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    return sorted(set(ports))


class Antigravity:
    """Conexao com o servidor local. Descobre porta/token ao instanciar."""

    def __init__(self):
        proc = _find_process()
        if proc is None:
            raise RuntimeError(
                "Antigravity nao esta rodando (processo language_server nao encontrado). "
                "Abra o Antigravity e tente de novo.")
        self.csrf = _csrf_from(proc)
        if not self.csrf:
            raise RuntimeError("nao consegui ler o csrf_token do processo.")
        self._ctx = ssl.create_default_context()
        self._ctx.check_hostname = False
        self._ctx.verify_mode = ssl.CERT_NONE
        self.port = self._pick_port(_ports_from(proc))

    def _pick_port(self, ports):
        # a porta certa e a que responde HTTPS; testa cada uma.
        for port in ports:
            try:
                self.port = port
                self.call("GetAllCascadeTrajectories", {})
                return port
            except Exception:
                continue
        raise RuntimeError(f"nenhuma porta respondeu ({ports}).")

    def call(self, method, body=None, as_proto=False):
        url = f"https://127.0.0.1:{self.port}/{SVC}/{method}"
        if as_proto:
            # request minimo: cascadeId no field 1 (string)
            cid = (body or {}).get("cascadeId", "").encode()
            data = bytes([0x0A, len(cid)]) + cid
            headers = {"Content-Type": "application/proto",
                       "Connect-Protocol-Version": "1",
                       "x-codeium-csrf-token": self.csrf}
        else:
            data = json.dumps(body or {}).encode()
            headers = {"Content-Type": "application/json",
                       "Connect-Protocol-Version": "1",
                       "x-codeium-csrf-token": self.csrf}
        req = urllib.request.Request(url, data=data, headers=headers, method="POST")
        try:
            r = urllib.request.urlopen(req, context=self._ctx, timeout=60)
            raw = r.read()
            return raw if as_proto else json.loads(raw or b"{}")
        except urllib.error.HTTPError as e:
            raise RuntimeError(f"{method} -> HTTP {e.code}: {e.read()[:200].decode('utf-8','replace')}")

    # --- conveniencias ---
    def list_conversations(self):
        d = self.call("GetAllCascadeTrajectories", {})
        return d.get("trajectorySummaries", {})

    def get_steps(self, cascade_id):
        return self.call("GetCascadeTrajectorySteps", {"cascadeId": cascade_id}).get("steps", [])

    def get_steps_proto(self, cascade_id):
        return self.call("GetCascadeTrajectorySteps", {"cascadeId": cascade_id}, as_proto=True)
