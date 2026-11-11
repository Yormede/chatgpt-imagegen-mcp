#!/usr/bin/env python3
"""Client local pour le service imagegen du Codex Router (abonnement ChatGPT).

Appelle POST http://127.0.0.1:4202/v1/images/edits avec la cle d'appel local du
routeur. Le routeur substitue ensuite la session ChatGPT capturee (auth.json) et
relaie vers https://chatgpt.com/backend-api/codex/images/edits.

Limites connues (voir README.md) :
  - 5 images de reference maximum par appel (limite dure, client + serveur) ;
  - modele gpt-image-2, quality=auto, size=auto par defaut ;
  - quota image_gen separe du quota du modele ; un 429 indique resets_at ;
  - moderation a l'entree et a la sortie (moderation_blocked).
"""
from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROUTER_BASE = os.environ.get("IMAGEGEN_ROUTER", "http://127.0.0.1:4202")
CODEX_HOME = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
AUTH_FILE = CODEX_HOME / "auth.json"
DEFAULT_OUT_DIR = Path(os.environ.get("IMAGEGEN_OUT_DIR", Path.home() / "Downloads"))
LOG_DIR = Path(__file__).resolve().parent / "state"
MAX_REFS = 5
DEFAULT_MODEL = "gpt-image-2"


class ImagegenError(RuntimeError):
    def __init__(self, message, *, status=None, body=None, request_id=None, code=None):
        super().__init__(message)
        self.status = status
        self.body = body
        self.request_id = request_id
        self.code = code


def read_chatgpt_session() -> dict:
    """Lit le jeton ChatGPT de Codex (auth.json) et le rafraichit si besoin.

    Le routeur accepte ce jeton comme preuve de session native
    (`nativeSessionTokenMatches`) et le relaie tel quel vers chatgpt.com.
    """
    if not AUTH_FILE.exists():
        raise ImagegenError(f"Session Codex introuvable : {AUTH_FILE}")
    doc = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
    tokens = doc.get("tokens") or {}
    token = tokens.get("access_token")
    if not token:
        raise ImagegenError("auth.json ne contient pas de jeton ChatGPT (mode API key ?).")
    exp = token_expiry(token)
    if exp and exp <= time.time():
        # Codex est le seul proprietaire du rafraichissement ; on le laisse faire.
        try:
            import subprocess
            subprocess.run(["codex", "login", "status"], capture_output=True, timeout=30)
        except Exception:
            pass
        doc = json.loads(AUTH_FILE.read_text(encoding="utf-8"))
        tokens = doc.get("tokens") or {}
        token = tokens.get("access_token") or token
        exp = token_expiry(token)
        if exp and exp <= time.time():
            raise ImagegenError("Jeton ChatGPT expire et non rafraichi ; relancer `codex login`.")
    return {
        "access_token": token,
        "account_id": tokens.get("account_id"),
        "expires_at": exp,
    }


def token_expiry(token: str):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("exp")
    except Exception:
        return None


def router_health(timeout=5):
    with urllib.request.urlopen(f"{ROUTER_BASE}/health", timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def data_url(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if not mime:
        mime = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
    payload = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{payload}"


def build_payload(prompt: str, refs, *, model=DEFAULT_MODEL, size="auto",
                  quality="auto", background="opaque", n=None):
    if len(refs) > MAX_REFS:
        raise ImagegenError(
            f"{len(refs)} references fournies ; le maximum est {MAX_REFS} par appel. "
            "Regrouper les references surnumeraires en planche avant l'appel."
        )
    payload = {
        "model": model,
        "prompt": prompt,
        "quality": quality,
        "size": size,
        "background": background,
    }
    if n:
        payload["n"] = int(n)
    if refs:
        payload["images"] = [{"image_url": data_url(Path(p))} for p in refs]
    return payload

def _request_id(headers):
    for key in ("x-codex-imagegen-request-id", "x-request-id", "x-oai-request-id"):
        value = headers.get(key) if headers else None
        if value:
            return value
    return None


def quota_from_headers(headers):
    """Lit les en-tetes de quota que le backend renvoie sur chaque reponse."""
    h = {str(k).lower(): v for k, v in (headers or {}).items()}

    def num(name):
        value = h.get(name)
        try:
            return float(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    quota = {
        "active_limit": h.get("x-codex-active-limit"),
        "plan_type": h.get("x-codex-plan-type"),
        "used_percent": num("x-codex-primary-used-percent"),
        "window_minutes": num("x-codex-primary-window-minutes"),
        "reset_at": num("x-codex-primary-reset-at"),
        "has_credits": h.get("x-codex-credits-has-credits"),
    }
    return {k: v for k, v in quota.items() if v is not None}


def generate(prompt: str, refs=(), *, out=None, model=DEFAULT_MODEL, size="auto",
             quality="auto", background="opaque", n=None, timeout=300,
             log=True, dry_run=False, meta=None):
    """Genere une image. Retourne un dict avec path, bytes, size, request_id, usage."""
    refs = [Path(p) for p in refs]
    for p in refs:
        if not p.is_file():
            raise ImagegenError(f"Reference introuvable : {p}")
    payload = build_payload(prompt, refs, model=model, size=size,
                            quality=quality, background=background, n=n)
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if dry_run:
        return {"dry_run": True, "body_bytes": len(body), "refs": len(refs),
                "prompt_chars": len(prompt), "model": model, "size": size}

    secret = read_chatgpt_session()
    headers = {
        "Authorization": f"Bearer {secret['access_token']}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "originator": "codex_cli_rs",
        "User-Agent": "dsh-imagegen-service/1.0",
    }
    if secret.get("account_id"):
        headers["chatgpt-account-id"] = secret["account_id"]
    endpoint = "/v1/images/edits" if refs else "/v1/images/generations"
    req = urllib.request.Request(
        f"{ROUTER_BASE}{endpoint}",
        data=body,
        method="POST",
        headers=headers,
    )
    started = time.time()
    status = None
    resp_body = b""
    resp_headers = {}
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            status = r.status
            resp_headers = dict(r.headers.items())
            resp_body = r.read()
    except urllib.error.HTTPError as e:
        status = e.code
        resp_headers = dict(e.headers.items()) if e.headers else {}
        resp_body = e.read()
    except urllib.error.URLError as e:
        raise ImagegenError(f"Routeur injoignable sur {ROUTER_BASE} : {e.reason}") from e
    duration = round(time.time() - started, 1)
    request_id = _request_id(resp_headers)

    if status != 200:
        detail = {}
        try:
            detail = json.loads(resp_body.decode("utf-8"))
        except Exception:
            pass
        err = detail.get("error") if isinstance(detail, dict) else None
        code = err.get("code") if isinstance(err, dict) else None
        message = err.get("message") if isinstance(err, dict) else resp_body[:400].decode("utf-8", "replace")
        resets_at = None
        if isinstance(err, dict):
            resets_at = err.get("resets_at") or (err.get("rate_limits") or {}).get("resets_at")
        suffix = ""
        if resets_at:
            local = datetime.fromtimestamp(resets_at).strftime("%d/%m/%Y %H:%M")
            suffix = f" (reprise possible : {local}, epoch {resets_at})"
        rid = f" [request id {request_id}]" if request_id else ""
        if status == 429:
            LOG_DIR.mkdir(parents=True, exist_ok=True)
            (LOG_DIR / "quota.json").write_text(json.dumps({
                "observed_at": datetime.now(timezone.utc).isoformat(),
                "status": status,
                "error": err if isinstance(err, dict) else detail,
                "headers": quota_from_headers(resp_headers),
            }, ensure_ascii=False, indent=2), encoding="utf-8")
        raise ImagegenError(f"HTTP {status} : {message}{suffix}{rid}", status=status,
                            body=detail or resp_body[:2000].decode("utf-8", "replace"),
                            request_id=request_id, code=code)

    try:
        data = json.loads(resp_body.decode("utf-8"))
        b64 = data["data"][0]["b64_json"]
    except Exception as e:
        raise ImagegenError(f"Reponse image illisible : {e} ; debut={resp_body[:300]!r}",
                            status=status, request_id=request_id)
    image_bytes = base64.b64decode(b64)
    if out is None:
        out = DEFAULT_OUT_DIR / f"imagegen_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:6]}.png"
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(image_bytes)
    quota = quota_from_headers(resp_headers)
    if quota:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        (LOG_DIR / "quota_headers.json").write_text(json.dumps({
            "observed_at": datetime.now(timezone.utc).isoformat(), **quota,
        }, ensure_ascii=False, indent=2), encoding="utf-8")
    result = {
        "path": str(out),
        "bytes": len(image_bytes),
        "size": data.get("size"),
        "quality": data.get("quality"),
        "background": data.get("background"),
        "created": data.get("created"),
        "generation_id": (data.get("data") or [{}])[0].get("generation_id"),
        "request_id": request_id,
        "usage": data.get("usage"),
        "duration_s": duration,
        "refs": len(refs),
        "prompt_chars": len(prompt),
        "quota": quota,
    }
    if log:
        write_log({**result, "status": status, "meta": meta or {},
                   "prompt_sha": __import__("hashlib").sha256(prompt.encode()).hexdigest()[:16]})
    return result


def write_log(entry):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / "appels.jsonl"
    entry = {"ts": datetime.now(timezone.utc).isoformat(), **entry}
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description="Genere une image via le service imagegen local (abonnement ChatGPT).")
    ap.add_argument("--prompt", help="Prompt texte (ou --prompt-file).")
    ap.add_argument("--prompt-file", help="Fichier contenant le prompt.")
    ap.add_argument("--ref", action="append", default=[], help="Image de reference (max 5, repetable).")
    ap.add_argument("--out", help="Chemin PNG de sortie.")
    ap.add_argument("--size", default="auto")
    ap.add_argument("--quality", default="auto", choices=["auto", "low", "medium", "high"])
    ap.add_argument("--background", default="opaque", choices=["opaque", "transparent", "auto"])
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--timeout", type=int, default=300)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--health", action="store_true")
    args = ap.parse_args(argv)

    try:
        if args.health:
            print(json.dumps(router_health(), ensure_ascii=False, indent=1))
            return 0
        prompt = args.prompt
        if args.prompt_file:
            prompt = Path(args.prompt_file).read_text(encoding="utf-8")
        if not prompt:
            ap.error("--prompt ou --prompt-file requis")
        result = generate(prompt, args.ref, out=args.out, model=args.model,
                          size=args.size, quality=args.quality,
                          background=args.background, timeout=args.timeout,
                          dry_run=args.dry_run)
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return 0
    except ImagegenError as e:
        print(json.dumps({"error": str(e), "status": e.status, "code": e.code,
                          "request_id": e.request_id}, ensure_ascii=False, indent=1),
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
