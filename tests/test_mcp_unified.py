import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent.parent
SERVER = HERE / "mcp_server.py"
PY = sys.executable

proc = subprocess.Popen([PY, str(SERVER)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE, text=True, encoding="utf-8", bufsize=1)
_next = 0


def send(obj):
    proc.stdin.write(json.dumps(obj, ensure_ascii=False) + "\n")
    proc.stdin.flush()


def call(method, params=None, timeout=300):
    global _next
    _next += 1
    rid = _next
    send({"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})
    deadline = time.time() + timeout
    while time.time() < deadline:
        line = proc.stdout.readline()
        if not line:
            break
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        if msg.get("id") == rid:
            return msg
    raise TimeoutError(method)


init = call("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                           "clientInfo": {"name": "test", "version": "0"}})
print("INIT", init["result"]["serverInfo"])
send({"jsonrpc": "2.0", "method": "notifications/initialized"})
tools = call("tools/list")
print("TOOLS", [t["name"] for t in tools["result"]["tools"]])


def show(label, resp):
    res = resp.get("result", {})
    if res.get("isError"):
        print(label, "ERROR", res.get("content"))
        return
    texts = [c.get("text") for c in res.get("content", []) if c.get("type") == "text"]
    print(label, "OK", (texts[0] if texts else "")[:1200])
    print(label, "images:", sum(1 for c in res.get("content", []) if c.get("type") == "image"))


show("QUOTA", call("imagegen_quota", {"backend": "all"}, timeout=180))
show("AGENTS", call("francestudent_agents", {}, timeout=120))

fs_prompt = "Un triangle vert sur un fond blanc, style plat, aucune texte."
show("FS_GEN", call("generate_image", {
    "backend": "francestudent", "prompt": fs_prompt, "count": 1,
    "aspect": "square", "output_path": str(HERE / "state/fs_mcp_test"),
    "return_image": False,
}, timeout=300))

codex_prompt = "A small blue circle on a plain white background, flat vector style, no text."
show("CODEX_GEN", call("generate_image", {
    "backend": "codex", "prompt": codex_prompt,
    "output_path": str(HERE / "state/codex_mcp_test.png"),
    "return_image": False,
}, timeout=300))

show("QUOTA2", call("imagegen_quota", {"backend": "codex"}, timeout=120))
proc.terminate()
print("STDERR", proc.stderr.read()[:800])
