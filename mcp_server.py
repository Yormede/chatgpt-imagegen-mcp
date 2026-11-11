#!/usr/bin/env python3
"""Serveur MCP (stdio) unifie pour la generation d'images.

Deux backends :
  - codex        : abonnement ChatGPT via le routeur Codex local (5 references max) ;
  - francestudent: compte France Student IA via le MCP francestudent-mcp (texte -> image).

Outils :
  - generate_image(prompt, backend?, references?, ...)
  - imagegen_quota(backend?)
  - imagegen_status()
  - francestudent_agents()

Claude Desktop (claude_desktop_config.json) :
{
  "mcpServers": {
    "imagegen": {
      "command": "python",
      "args": ["C:\\\\chemin\\\\vers\\\\chatgpt-imagegen-mcp\\\\mcp_server.py"]
    }
  }
}
"""
from __future__ import annotations

import base64
import json
import sys
import time
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import imagegen  # noqa: E402
import francestudent_client as fsclient  # noqa: E402

PROTOCOL = "2024-11-05"
SERVER_INFO = {"name": "imagegen-unified", "version": "1.1.0"}
STATE = Path(__file__).resolve().parent / "state"

TOOLS = [
    {
        "name": "generate_image",
        "description": (
            "Genere une image. backend=codex (defaut) : abonnement ChatGPT via le routeur Codex, "
            "5 references max. backend=francestudent : compte France Student IA, texte -> image, "
            "1 a 4 images, pas de reference. backend=auto : essaie codex puis bascule sur "
            "francestudent si le quota codex est epuise."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "prompt": {"type": "string", "description": "Prompt complet de l'image."},
                "backend": {"type": "string", "enum": ["codex", "francestudent", "auto"], "default": "codex"},
                "references": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 5,
                    "description": "Chemins locaux des images de reference (backend codex uniquement).",
                },
                "output_path": {"type": "string", "description": "Chemin PNG de sortie (codex) ou dossier (francestudent)."},
                "size": {"type": "string", "default": "auto"},
                "quality": {"type": "string", "enum": ["auto", "low", "medium", "high"], "default": "auto"},
                "background": {"type": "string", "enum": ["opaque", "transparent", "auto"], "default": "opaque"},
                "count": {"type": "integer", "minimum": 1, "maximum": 4, "default": 1,
                          "description": "Nombre d'images (francestudent uniquement)."},
                "aspect": {"type": "string", "enum": ["auto", "square", "landscape", "portrait"], "default": "auto"},
                "agent": {"type": "string", "default": "gpt-6-sol",
                          "description": "Agent France Student (defaut gpt-6-sol)."},
                "return_image": {"type": "boolean", "default": True,
                                 "description": "Renvoyer l'image dans la reponse MCP."},
            },
            "required": ["prompt"],
        },
    },
    {
        "name": "imagegen_quota",
        "description": "Quota des deux backends : codex (headers image_gen) et France Student (chatBudget, rate limits).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "backend": {"type": "string", "enum": ["all", "codex", "francestudent"], "default": "all"},
            },
        },
    },
    {
        "name": "imagegen_status",
        "description": "Etat du routeur Codex, de la session ChatGPT et de la presence du MCP France Student.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "francestudent_agents",
        "description": "Liste les agents France Student disponibles (seuls les agents OpenAI generent des images).",
        "inputSchema": {"type": "object", "properties": {}},
    },
]

_fs = None


def get_fs():
    global _fs
    if _fs is None:
        _fs = fsclient.FrancestudentMcp()
    return _fs


def send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def reply(req_id, result):
    send({"jsonrpc": "2.0", "id": req_id, "result": result})


def error(req_id, code, message):
    send({"jsonrpc": "2.0", "id": req_id, "error": {"code": code, "message": message}})


def _load(name):
    p = STATE / name
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def codex_quota():
    qh = _load("quota_headers.json") or {}
    q429 = _load("quota.json") or {}
    err = q429.get("error") or {}
    hdr = q429.get("headers") or {}
    reset = err.get("resets_at") or hdr.get("reset_at") or qh.get("reset_at")
    used = hdr.get("used_percent", qh.get("used_percent"))
    now = time.time()
    appels = []
    p = STATE / "appels.jsonl"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    appels.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    since = reset if reset and reset <= now else now - 24 * 3600
    recent = [a for a in appels if datetime.fromisoformat(a["ts"]).timestamp() >= since]
    exhausted = bool(reset and reset > now and (used or 0) >= 100)
    return {
        "backend": "codex",
        "plan": err.get("plan_type") or hdr.get("plan_type") or qh.get("plan_type"),
        "window_minutes": err.get("limit_window_minutes") or hdr.get("window_minutes") or qh.get("window_minutes"),
        "used_percent": used,
        "reset_at": reset,
        "reset_local": datetime.fromtimestamp(reset).strftime("%d/%m/%Y %H:%M:%S") if reset else None,
        "exhausted": exhausted,
        "images_in_window": len(recent),
        "observed_cap": 80,
        "remaining_estimate": 0 if exhausted else max(0, 80 - len(recent)),
    }


def generate_codex(args):
    prompt = args["prompt"]
    refs = args.get("references") or []
    result = imagegen.generate(
        prompt, refs,
        out=args.get("output_path"),
        size=args.get("size", "auto"),
        quality=args.get("quality", "auto"),
        background=args.get("background", "opaque"),
        timeout=300,
        meta={"source": "mcp", "backend": "codex"},
    )
    result["backend"] = "codex"
    return result, result.get("path")


def generate_francestudent(args):
    refs = args.get("references") or []
    if refs:
        raise ValueError("Le backend francestudent n'accepte pas d'images de reference (texte -> image uniquement).")
    quality = args.get("quality", "auto")
    quality = "high" if quality == "high" else "auto"
    res = get_fs().generate(
        args["prompt"],
        aspect=args.get("aspect", "auto"),
        quality=quality,
        count=int(args.get("count", 1)),
        agent=args.get("agent", "gpt-6-sol"),
        output_dir=args.get("output_path"),
        return_image=False,
        timeout=420,
    )
    first = res["files"][0]["path"] if res["files"] else None
    result = {
        "backend": "francestudent",
        "files": res["files"],
        "path": first,
        "revised_prompts": res["revised_prompts"],
        "text": res["text"],
    }
    return result, first


def call_tool(name, args):
    if name == "imagegen_status":
        health = imagegen.router_health()
        session = imagegen.read_chatgpt_session()
        return {
            "router": health,
            "token_expires_at": session.get("expires_at"),
            "francestudent_server": fsclient.SERVER.exists(),
            "francestudent_session": (fsclient.SERVER.parent.parent / ".secrets" / "session.json").exists(),
        }, None
    if name == "imagegen_quota":
        backend = args.get("backend", "all")
        out = {}
        if backend in ("all", "codex"):
            out["codex"] = codex_quota()
        if backend in ("all", "francestudent"):
            try:
                out["francestudent"] = get_fs().usage(timeout=90)
            except Exception as e:  # noqa: BLE001
                out["francestudent_error"] = {"code": getattr(e, "code", None), "message": str(e)}
        return out, None
    if name == "francestudent_agents":
        return {"agents": get_fs().agents(timeout=90)}, None
    if name == "generate_image":
        if not args.get("prompt"):
            raise ValueError("prompt requis")
        backend = args.get("backend", "codex")
        if backend == "francestudent":
            return generate_francestudent(args)
        if backend == "auto":
            try:
                return generate_codex(args)
            except imagegen.ImagegenError as e:
                if e.status in (401, 402, 429, 500, 502, 503, 504):
                    result, path = generate_francestudent(args)
                    result["fallback_from"] = {"backend": "codex", "status": e.status, "code": e.code}
                    return result, path
                raise
        return generate_codex(args)
    raise ValueError(f"outil inconnu : {name}")


def handle(msg):
    method = msg.get("method")
    req_id = msg.get("id")
    params = msg.get("params") or {}
    if method == "initialize":
        reply(req_id, {
            "protocolVersion": PROTOCOL,
            "capabilities": {"tools": {}},
            "serverInfo": SERVER_INFO,
        })
    elif method in ("notifications/initialized", "initialized"):
        return
    elif method == "ping":
        reply(req_id, {})
    elif method == "tools/list":
        reply(req_id, {"tools": TOOLS})
    elif method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        try:
            result, image_path = call_tool(name, args)
            content = [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}]
            if args.get("return_image", True) and image_path and Path(image_path).exists():
                data = base64.b64encode(Path(image_path).read_bytes()).decode("ascii")
                content.append({"type": "image", "data": data, "mimeType": "image/png"})
            reply(req_id, {"content": content, "isError": False})
        except imagegen.ImagegenError as e:
            reply(req_id, {"content": [{"type": "text", "text": json.dumps(
                {"error": str(e), "status": e.status, "code": e.code,
                 "request_id": e.request_id}, ensure_ascii=False)}], "isError": True})
        except fsclient.FrancestudentError as e:
            reply(req_id, {"content": [{"type": "text", "text": json.dumps(
                {"error": str(e), "code": e.code, "backend": "francestudent"},
                ensure_ascii=False)}], "isError": True})
        except Exception as e:  # noqa: BLE001
            reply(req_id, {"content": [{"type": "text", "text": f"Erreur : {e}"}], "isError": True})
    elif req_id is not None:
        error(req_id, -32601, f"Methode inconnue : {method}")


def main():
    # JSON-RPC MCP est en UTF-8 ; le stdout Windows par defaut ne l'est pas.
    try:
        sys.stdin.reconfigure(encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            handle(msg)
        except Exception as e:  # noqa: BLE001
            if msg.get("id") is not None:
                error(msg["id"], -32603, str(e))


if __name__ == "__main__":
    main()
