import json, subprocess, sys
from pathlib import Path

srv = str(Path(__file__).resolve().parent.parent / "mcp_server.py")
p = subprocess.Popen([sys.executable, srv], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                     stderr=subprocess.PIPE, text=True, encoding="utf-8")
msgs = [
    {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}},
    {"jsonrpc": "2.0", "method": "notifications/initialized"},
    {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "imagegen_status", "arguments": {}}},
]
for m in msgs:
    p.stdin.write(json.dumps(m) + "\n")
p.stdin.flush()
for _ in range(3):
    line = p.stdout.readline()
    print(line.strip()[:800])
p.terminate()
err = p.stderr.read()
if err:
    print("STDERR", err[:500])
