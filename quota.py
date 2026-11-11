#!/usr/bin/env python3
"""Affiche l'etat du quota image_gen.

Deux sources :
  - state/quota_headers.json : en-tetes renvoyes par le backend apres chaque
    image reussie (x-codex-primary-used-percent, reset-at, plan, fenetre) ;
  - state/quota.json : corps et en-tetes du dernier 429.

Le backend ne fournit pas de "restant" separe : le pourcentage utilise est la
mesure officielle, et le 429 confirme l'epuisement.
"""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

STATE = Path(__file__).resolve().parent / "state"
APPELS = STATE / "appels.jsonl"
QUOTA429 = STATE / "quota.json"
QUOTA_HDR = STATE / "quota_headers.json"
WINDOW_MIN = 1440
OBSERVED_CAP = 80


def load_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def fmt_reset(ts):
    if not ts:
        return "?"
    return datetime.fromtimestamp(ts).strftime("%d/%m/%Y %H:%M:%S")


def main():
    now = time.time()
    appels = load_jsonl(APPELS)
    recent = [a for a in appels if now - datetime.fromisoformat(a["ts"]).timestamp() <= WINDOW_MIN * 60]
    q429 = load_json(QUOTA429)
    qh = load_json(QUOTA_HDR)

    print("=== Quota image_gen ===")
    print(f"Images reussies dans les dernieres 24 h : {len(recent)}")
    used = None
    reset = None
    plan = None
    window = None
    if qh:
        used = qh.get("used_percent")
        reset = qh.get("reset_at")
        plan = qh.get("plan_type")
        window = qh.get("window_minutes")
        print(f"Derniere mesure (apres une image) : {qh.get('observed_at')}")
    if q429:
        err = q429.get("error") or {}
        hdr = q429.get("headers") or {}
        reset = err.get("resets_at") or hdr.get("reset_at") or reset
        used = hdr.get("used_percent", used)
        plan = err.get("plan_type") or hdr.get("plan_type") or plan
        window = err.get("limit_window_minutes") or hdr.get("window_minutes") or window
        print(f"Dernier 429 : {q429.get('observed_at')}")

    if plan or window:
        print(f"Plan : {plan or '?'}   Fenetre : {window or '?'} minutes")
    if used is not None:
        print(f"Utilisation : {used:.0f} %")
    if reset:
        restant = int((reset - now) // 60)
        etat = "quota epuise" if restant > 0 and (used or 0) >= 100 else "quota disponible"
        print(f"Reset : {fmt_reset(reset)} (dans {max(0, restant)} min) -> {etat}")

    if reset and reset <= now:
        # Fenetre precedente terminee : on repart de zero depuis le reset.
        recent = [a for a in appels if datetime.fromisoformat(a["ts"]).timestamp() >= reset]
        used = 0
        print(f"Fenetre precedente terminee ; {len(recent)} image(s) depuis le reset.")

    epuise = bool(reset and reset > now and (used or 0) >= 100)
    reste = 0 if epuise else max(0, OBSERVED_CAP - len(recent))
    print(f"\nCapacite observee : environ {OBSERVED_CAP} images / 24 h (plan Plus)")
    print(f"Reste estime : {reste} image(s)")
    print("\nPage officielle : https://chatgpt.com/settings/usage")


if __name__ == "__main__":
    main()
