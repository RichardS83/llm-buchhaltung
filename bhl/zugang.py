# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2025-2026 RichardS83
"""Zugangsdaten für die Schnittstellen von Qonto und Stripe.

Gelesen wird aus dem Schlüsselbund von macOS, ersatzweise aus der Umgebung.
Bewusst dieselben Schlüsselbund-Einträge, die schon das frühere UStVA-Werkzeug
verwendet hat — es muss nichts neu hinterlegt werden.

Ein Geheimnis steht nie in einer Datei dieses Verzeichnisses.
"""

from __future__ import annotations

import os
import shutil
import subprocess

SCHLUESSELBUND = {
    "STRIPE_API_KEY": "ustva-stripe",
    "QONTO_LOGIN": "ustva-qonto-login",
    "QONTO_SECRET_KEY": "ustva-qonto-secret",
    "QONTO_ACCESS_TOKEN": "ustva-qonto-token",
}


class ZugangFehlt(Exception):
    pass


def _aus_schluesselbund(name: str) -> str | None:
    dienst = SCHLUESSELBUND.get(name)
    if not dienst or not shutil.which("security"):
        return None
    try:
        p = subprocess.run(["security", "find-generic-password", "-s", dienst, "-w"],
                           capture_output=True, text=True, timeout=30, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    return (p.stdout.strip() or None) if p.returncode == 0 else None


def hole(name: str, pflicht: bool = True) -> str | None:
    """Umgebung zuerst, dann Schlüsselbund."""
    wert = os.environ.get(name) or _aus_schluesselbund(name)
    if not wert and pflicht:
        raise ZugangFehlt(
            f"{name} nicht gefunden. Entweder als Umgebungsvariable setzen oder im "
            f"Schlüsselbund unter dem Dienst '{SCHLUESSELBUND.get(name, name)}' hinterlegen:\n"
            f"  security add-generic-password -s {SCHLUESSELBUND.get(name, name)} "
            f"-a $USER -w"
        )
    return wert


def qonto_kopf() -> dict[str, str]:
    if token := hole("QONTO_ACCESS_TOKEN", pflicht=False):
        return {"Authorization": f"Bearer {token}"}
    # Qontos Schlüsselverfahren erwartet das rohe Paar login:secret, nicht Basic.
    return {"Authorization": f"{hole('QONTO_LOGIN')}:{hole('QONTO_SECRET_KEY')}"}


def stripe_kopf() -> dict[str, str]:
    return {"Authorization": f"Bearer {hole('STRIPE_API_KEY')}"}
