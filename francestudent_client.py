#!/usr/bin/env python3
"""Client MCP stdio pour le serveur francestudent-mcp (France Student IA).

Notre serveur MCP agit comme client : il lance `node src/server.mjs`, parle
JSON-RPC 2.0 (une ligne par message) et expose fs_generate_image / fs_usage.
"""
from __future__ import annotations

import atexit
import json
import os
import queue
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

NODE = Path(os.environ.get("FS_NODE") or shutil.which("node") or "node")
SERVER = Path(os.environ.get("FS_MCP_SERVER") or Path(__file__).resolve().parent.parent / "francestudent-mcp" / "src" / "server.mjs")
DEFAULT_OUT = Path.home() / "Pictures" / "francestudent"
PROTOCOL = "2024-11-05"
FILE_LINE = re.compile(r"✔\s+(.+?\.(?:png|jpg|jpeg|webp))\s+\((\d+)\s*Ko\)", re.IGNORECASE)
ERROR_LINE = re.compile(r"Erreur\s*(?:\[([A-Z_]+)\])?\s*:\s*(.*)", re.DOTALL)


class FrancestudentError(RuntimeError):
    def __init__(self, message, code=None):
        super().__init__(message)
        self.code = code


class FrancestudentMcp:
    def __init__(self, *, output_dir: Path | str | None = None, timeout: float = 60.0):
        self.output_dir = Path(output_dir or os.environ.get("FS_OUTPUT_DIR") or DEFAULT_OUT)
        self.timeout = timeout
        self.proc: subprocess.Popen | None = None
        self._queue: queue.Queue = queue.Queue()
        self._next_id = 0
        self._stderr_lines: list[str] = []
        atexit.register(self.close)

    # ------------------------------------------------------------------ process
    def start(self):
        if self.proc and self.proc.poll() is None:
            return
        if not (NODE.exists() or shutil.which(str(NODE))):
            raise FrancestudentError(f"Node introuvable : {NODE}", "NODE_MISSING")
        if not SERVER.exists():
            raise FrancestudentError(f"Serveur francestudent introuvable : {SERVER}", "SERVER_MISSING")
        env = {**os.environ, "FS_OUTPUT_DIR": str(self.output_dir)}
        self.proc = subprocess.Popen(
            [str(NODE), str(SERVER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            bufsize=1,
            env=env,
            cwd=str(SERVER.parent),
        )
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        self._request("initialize", {
            "protocolVersion": PROTOCOL,
            "capabilities": {},
            "clientInfo": {"name": "dsh-imagegen-bridge", "version": "1.0.0"},
        }, timeout=self.timeout)
        self._notify("notifications/initialized", {})

    def _read_stdout(self):
        assert self.proc and self.proc.stdout
        for line in self.proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                self._queue.put(json.loads(line))
            except json.JSONDecodeError:
                continue

    def _read_stderr(self):
        assert self.proc and self.proc.stderr
        for line in self.proc.stderr:
            self._stderr_lines.append(line.rstrip())
            if len(self._stderr_lines) > 50:
                self._stderr_lines.pop(0)

    def close(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                try:
                    self.proc.kill()
                except Exception:  # noqa: BLE001
                    pass
        self.proc = None

    # ------------------------------------------------------------------ JSON-RPC
    def _send(self, obj):
        assert self.proc and self.proc.stdin
        self.proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
        self.proc.stdin.flush()

    def _notify(self, method, params):
        self._send({"jsonrpc": "2.0", "method": method, "params": params})

    def _request(self, method, params, timeout=None):
        self.start() if method != "initialize" else None
        self._next_id += 1
        req_id = self._next_id
        self._send({"jsonrpc": "2.0", "id": req_id, "method": method, "params": params})
        deadline = time.time() + (timeout or self.timeout)
        while True:
            remaining = deadline - time.time()
            if remaining <= 0:
                raise FrancestudentError(f"Timeout MCP francestudent ({method})", "TIMEOUT")
            try:
                msg = self._queue.get(timeout=min(remaining, 2))
            except queue.Empty:
                if self.proc and self.proc.poll() is not None:
                    tail = " | ".join(self._stderr_lines[-5:])
                    raise FrancestudentError(
                        f"Serveur francestudent arrete (code {self.proc.returncode}) : {tail}",
                        "SERVER_EXITED")
                continue
            if msg.get("id") == req_id:
                if "error" in msg:
                    err = msg["error"]
                    raise FrancestudentError(err.get("message", "erreur MCP"), err.get("code"))
                return msg.get("result", {})

    def call_tool(self, name, arguments, timeout=None):
        res = self._request("tools/call", {"name": name, "arguments": arguments}, timeout=timeout)
        texts = [c.get("text", "") for c in res.get("content", []) if c.get("type") == "text"]
        text = "\n".join(t for t in texts if t)
        if res.get("isError") or text.lstrip().startswith("Erreur"):
            m = ERROR_LINE.search(text)
            code = m.group(1) if m else "UNKNOWN"
            message = m.group(2) if m else text
            raise FrancestudentError(message.strip(), code)
        return {"text": text, "content": res.get("content", [])}

    # ------------------------------------------------------------------ tools
    def usage(self, timeout=60):
        res = self.call_tool("fs_usage", {}, timeout=timeout)
        try:
            return json.loads(res["text"])
        except json.JSONDecodeError:
            return {"raw": res["text"]}

    def agents(self, timeout=60):
        return self.call_tool("fs_list_agents", {}, timeout=timeout)["text"]

    def generate(self, prompt: str, *, aspect="auto", quality="auto", count=1,
                 agent="gpt-6-sol", output_dir=None, return_image=False,
                 keep_chat=False, raw_prompt=False, timeout=300):
        out = str(Path(output_dir) if output_dir else self.output_dir)
        res = self.call_tool("fs_generate_image", {
            "prompt": prompt,
            "aspect": aspect,
            "quality": quality,
            "count": int(count),
            "agent": agent,
            "output_dir": out,
            "return_image": bool(return_image),
            "keep_chat": bool(keep_chat),
            "raw_prompt": bool(raw_prompt),
        }, timeout=timeout)
        files = []
        revised = []
        for line in res["text"].splitlines():
            m = FILE_LINE.search(line)
            if m:
                files.append({"path": m.group(1), "ko": int(m.group(2))})
            if "prompt révisé" in line:
                revised.append(line.split("prompt révisé", 1)[1].lstrip(" :"))
        return {"files": files, "revised_prompts": revised, "text": res["text"],
                "content": res["content"]}
