#!/usr/bin/env python3
"""
Ermittelt die JVM-Versionen (Amazon Corretto) in den AWS Lambda Java Base Images.

Ablauf pro Base-Image-Tag (z. B. "25"):
  1. Manifest-Liste von public.ecr.aws abrufen (mit anonymem Token) und den
     linux/amd64-Manifest-Digest bestimmen - ohne Pull, nur Registry-API.
  2. Nur wenn dieser Digest unbekannt ist oder sich geändert hat:
     Image pullen und `java -version` darin ausführen.
  3. Ergebnisse in data/versions.json schreiben und docs/index.md neu rendern.

Nur Standardbibliothek + Docker nötig. Tags lassen sich per Umgebungsvariable
TAGS überschreiben (z. B. TAGS="25" für lokale Tests).
"""

import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

REGISTRY = "https://public.ecr.aws"
REPOSITORY = "lambda/java"
PLATFORM = "linux/amd64"

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = REPO_ROOT / "data" / "versions.json"
SITE_FILE = REPO_ROOT / "docs" / "index.md"

TAGS = os.environ.get("TAGS", "8.al2 11 17 21 25").split()

# Images nach dem Prüfen wieder löschen (in CI sinnvoll, lokal eher nicht)
CLEANUP_IMAGES = os.environ.get("CLEANUP_IMAGES", "").lower() in ("1", "true", "yes")

JAVA_RE = re.compile(r'openjdk version "([^"]+)"')
CORRETTO_RE = re.compile(r"Corretto-([\d.]+)\s+\(build ([^)]+)\)")


def today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def ecr_token() -> str:
    with urllib.request.urlopen(f"{REGISTRY}/token/", timeout=30) as resp:
        return json.load(resp)["token"]


def amd64_digest(tag: str, token: str) -> str:
    """Digest des linux/amd64-Manifests eines Tags (ohne das Image zu pullen)."""
    req = urllib.request.Request(
        f"{REGISTRY}/v2/{REPOSITORY}/manifests/{tag}",
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": ", ".join([
                "application/vnd.docker.distribution.manifest.list.v2+json",
                "application/vnd.oci.image.index.v1+json",
            ]),
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        manifest_list = json.load(resp)
    for manifest in manifest_list.get("manifests", []):
        platform = manifest.get("platform", {})
        if platform.get("os") == "linux" and platform.get("architecture") == "amd64":
            return manifest["digest"]
    raise RuntimeError(f"Kein {PLATFORM}-Manifest für Tag '{tag}' gefunden")


def read_java_version(tag: str) -> dict:
    """Pullt das Image und liest `java -version` aus."""
    image = f"public.ecr.aws/{REPOSITORY}:{tag}"
    try:
        subprocess.run(
            ["docker", "pull", "--platform", PLATFORM, image],
            check=True, capture_output=True, text=True,
        )
        result = subprocess.run(
            ["docker", "run", "--rm", "--platform", PLATFORM,
             "--entrypoint", "java", image, "-version"],
            check=True, capture_output=True, text=True,
        )
        output = result.stderr + result.stdout  # java -version schreibt nach stderr
    finally:
        if CLEANUP_IMAGES:
            subprocess.run(["docker", "rmi", image], capture_output=True)

    java_match = JAVA_RE.search(output)
    corretto_match = CORRETTO_RE.search(output)
    if not java_match or not corretto_match:
        raise RuntimeError(f"Konnte 'java -version' nicht parsen:\n{output}")
    return {
        "javaVersion": java_match.group(1),
        "correttoVersion": corretto_match.group(1),
        "correttoBuild": corretto_match.group(2),
        "rawOutput": output.strip(),
    }


def short(digest: str) -> str:
    return digest.removeprefix("sha256:")[:12]


def render_site(data: dict) -> str:
    """Rendert docs/index.md (wird von GitHub Pages als Website ausgeliefert)."""
    rows = []
    for tag, e in data["tags"].items():
        if "error" in e or "correttoVersion" not in e:
            rows.append(f"| `:{tag}` | `{short(e.get('amd64Digest', '-'))}` "
                        f"| ⚠️ {e.get('error', 'keine Daten')} | – | – | – | – |")
        else:
            rows.append(
                f"| `:{tag}` | `{short(e['amd64Digest'])}` "
                f"| {e['javaVersion']} | {e['correttoVersion']} | {e['correttoBuild']} "
                f"| {e.get('firstSeen', '–')} | {e.get('lastChecked', '–')} |"
            )
    table = "\n".join(rows)
    return f"""---
title: JVM-Versionen in den AWS Lambda Java Base Images
---

# JVM-Versionen in den AWS Lambda Java Base Images

**Welche Amazon-Corretto-Version steckt in welchem AWS Lambda Java Base Image?**
Diese Tabelle wird täglich automatisch per GitHub Action aktualisiert, indem die
Images von `public.ecr.aws/lambda/java` geprüft werden
([so funktioniert es](https://github.com/hamburml/aws-base-lambda-image-corretto-version#readme)).

> ⚠️ Inoffizielles Community-Projekt – AWS dokumentiert diese Zuordnung selbst nicht.
> Alle Angaben ohne Gewähr; für die Richtigkeit der Daten wird keine Haftung übernommen.

*Letzte Aktualisierung: {data.get('generatedAt', '–')}*

| Base-Image-Tag | amd64-Digest | OpenJDK | Corretto | Corretto-Build | Erstmals gesehen | Zuletzt geprüft |
|---|---|---|---|---|---|---|
{table}

## Erläuterung

- **Base-Image-Tag**: Der Multi-Arch-Tag von `public.ecr.aws/lambda/java`.
- **amd64-Digest**: Digest des `linux/amd64`-Manifests hinter dem Tag (gekürzt).
  Tags sind mutable – der Digest identifiziert den Inhalt eindeutig.
- **OpenJDK / Corretto / Corretto-Build**: Ausgabe von `java -version` im Image.
- **Erstmals gesehen**: Datum, an dem dieser Digest hier zuerst auftauchte.

Rohdaten: [`data/versions.json`](https://github.com/hamburml/aws-base-lambda-image-corretto-version/blob/main/data/versions.json)
"""


def main() -> int:
    data = {"tags": {}}
    if DATA_FILE.exists():
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    data.setdefault("tags", {})

    token = ecr_token()
    errors = 0

    for tag in TAGS:
        entry = data["tags"].get(tag, {})
        entry.pop("error", None)
        try:
            digest = amd64_digest(tag, token)
            if digest == entry.get("amd64Digest") and "correttoVersion" in entry:
                print(f":{tag}: unverändert ({short(digest)}), kein Pull nötig")
            else:
                print(f":{tag}: neuer Digest {short(digest)}, pull Image ...")
                info = read_java_version(tag)
                entry.update(info)
                entry["amd64Digest"] = digest
                entry["firstSeen"] = today()
                print(f":{tag}: Corretto {info['correttoVersion']} "
                      f"({info['correttoBuild']})")
        except Exception as exc:  # alte Daten behalten, Fehler vermerken
            errors += 1
            entry["error"] = str(exc)
            print(f":{tag}: FEHLER: {exc}", file=sys.stderr)
        entry["lastChecked"] = today()
        data["tags"][tag] = entry

    data["generatedAt"] = now()
    data["source"] = f"public.ecr.aws/{REPOSITORY}"
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    SITE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SITE_FILE.write_text(render_site(data), encoding="utf-8")
    print(f"Aktualisiert: {DATA_FILE.relative_to(REPO_ROOT)}, "
          f"{SITE_FILE.relative_to(REPO_ROOT)}")
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
