#!/usr/bin/env python3
"""
Ermittelt die JVM-Versionen (Amazon Corretto) in den AWS Lambda Java Base Images.

Ablauf:
  1. Base-Tags (z. B. "25"): amd64-Manifest-Digest via Registry-API bestimmen
     (ohne Pull). Nur wenn der Digest unbekannt/geändert ist: Image pullen und
     `java -version` ausführen.
  2. Snapshot-Tags (z. B. "25.2026.07.11.03-x86_64"): Die Tag-Liste von
     public.ecr.aws enthält datierte, arch-spezifische Tags. Es werden die
     x86_64-Tags aufgenommen, deren Datum max. SNAPSHOT_TAG_MAX_AGE_DAYS
     zurückliegt (Default: 1 = seit dem letzten täglichen Lauf, da die Tags
     keine Uhrzeit enthalten, werden gestern + heute erfasst).
     Ist der Digest bereits bekannt (Base-Tag oder anderer Snapshot), wird die
     Version ohne Pull übernommen - gleicher Digest = gleicher Inhalt.
  3. data/versions.json schreiben und docs/index.md neu rendern.
     Snapshot-Einträge werden nach SNAPSHOT_HISTORY_DAYS (Default: 14) entfernt.

Nur Standardbibliothek + Docker nötig.
Umgebungsvariablen: TAGS, SNAPSHOT_TAG_MAX_AGE_DAYS, SNAPSHOT_HISTORY_DAYS,
CLEANUP_IMAGES ("1" = gepullte Images wieder löschen, für CI).
"""

import hashlib
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

REGISTRY = "https://public.ecr.aws"
REPOSITORY = "lambda/java"
PLATFORM = "linux/amd64"
IMAGE = f"public.ecr.aws/{REPOSITORY}"

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = REPO_ROOT / "data" / "versions.json"
SITE_FILE = REPO_ROOT / "docs" / "index.md"

TAGS = os.environ.get("TAGS", "8.al2 11 17 21 25").split()
SNAPSHOT_TAG_MAX_AGE_DAYS = int(os.environ.get("SNAPSHOT_TAG_MAX_AGE_DAYS", "1"))
SNAPSHOT_HISTORY_DAYS = int(os.environ.get("SNAPSHOT_HISTORY_DAYS", "14"))
CLEANUP_IMAGES = os.environ.get("CLEANUP_IMAGES", "").lower() in ("1", "true", "yes")

JAVA_RE = re.compile(r'openjdk version "([^"]+)"')
CORRETTO_RE = re.compile(r"Corretto-([\d.]+)\s+\(build ([^)]+)\)")
# z. B. 25.2026.07.11.03-x86_64 oder 8.al2.2026.07.17.16-x86_64
SNAPSHOT_RE = re.compile(r"^.+\.(\d{4})\.(\d{2})\.(\d{2})\.\d{2}-x86_64$")

ACCEPT = ", ".join([
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
])


def today() -> date:
    return datetime.now(timezone.utc).date()


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


def ecr_token() -> str:
    with urllib.request.urlopen(f"{REGISTRY}/token/", timeout=30) as resp:
        return json.load(resp)["token"]


def registry_get(path: str, token: str):
    req = urllib.request.Request(
        f"{REGISTRY}/v2/{REPOSITORY}/{path}",
        headers={"Authorization": f"Bearer {token}", "Accept": ACCEPT})
    return urllib.request.urlopen(req, timeout=30)


def list_recent_tags(token: str) -> list:
    """Tag-Liste des Repositories.

    public.ecr.aws liefert max. 1000 Tags ohne funktionierende Paginierung;
    die Antwort enthält aber die neuesten Tags zuerst (verifiziert: die
    datierten Tags der letzten ~10 Tage sind vollständig enthalten).
    Für ein Discovery-Fenster von wenigen Tagen reicht die erste Seite.
    """
    with registry_get("tags/list?n=1000", token) as resp:
        return json.load(resp).get("tags", [])


def recent_snapshot_tags(token: str) -> list:
    """Datierte x86_64-Snapshot-Tags, deren Datum im Discovery-Fenster liegt."""
    found = []
    for tag in list_recent_tags(token):
        m = SNAPSHOT_RE.match(tag)
        if not m:
            continue
        tag_date = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if (today() - tag_date).days <= SNAPSHOT_TAG_MAX_AGE_DAYS:
            found.append(tag)
    return sorted(found)


def amd64_digest(tag: str, token: str) -> str:
    """Digest des linux/amd64-Manifests eines Tags (ohne Pull).

    Multi-Arch-Tags (z. B. ":25") liefern eine Manifest-Liste, aus der der
    amd64-Eintrag gewählt wird. Datierte Snapshot-Tags sind bereits
    arch-spezifisch (einzelnes Manifest) - dann zählt der Digest des
    Manifests selbst (Docker-Content-Digest-Header).
    """
    with registry_get(f"manifests/{tag}", token) as resp:
        body = resp.read()
    doc = json.loads(body)
    if "manifests" in doc:
        for manifest in doc["manifests"]:
            platform = manifest.get("platform", {})
            if platform.get("os") == "linux" and platform.get("architecture") == "amd64":
                return manifest["digest"]
        raise RuntimeError(f"Kein {PLATFORM}-Manifest für Tag '{tag}' gefunden")
    # Einzelnes Manifest (arch-spezifischer Snapshot-Tag): Der Digest ist per
    # Definition die SHA-256-Prüfsumme der Manifest-Bytes.
    return "sha256:" + hashlib.sha256(body).hexdigest()


def read_java_version(tag: str) -> dict:
    """Pullt das Image und liest `java -version` aus."""
    image = f"{IMAGE}:{tag}"
    try:
        subprocess.run(["docker", "pull", "--platform", PLATFORM, image],
                       check=True, capture_output=True, text=True)
        result = subprocess.run(
            ["docker", "run", "--rm", "--platform", PLATFORM,
             "--entrypoint", "java", image, "-version"],
            check=True, capture_output=True, text=True)
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


def process_tag(tag: str, entry: dict, known_by_digest: dict, token: str) -> dict:
    """Prüft einen Tag und aktualisiert seinen Eintrag (siehe Modul-Docstring)."""
    entry.pop("error", None)
    try:
        digest = amd64_digest(tag, token)
        if digest == entry.get("amd64Digest") and "correttoVersion" in entry:
            print(f":{tag}: unverändert ({short(digest)}), kein Pull nötig")
        elif digest in known_by_digest:
            entry.update(known_by_digest[digest])
            entry["amd64Digest"] = digest
            entry["firstSeen"] = today().isoformat()
            print(f":{tag}: Digest bekannt ({short(digest)}), "
                  f"Version ohne Pull übernommen")
        else:
            print(f":{tag}: neuer Digest {short(digest)}, pulle Image ...")
            entry.update(read_java_version(tag))
            entry["amd64Digest"] = digest
            entry["firstSeen"] = today().isoformat()
            print(f":{tag}: Corretto {entry['correttoVersion']} "
                  f"({entry['correttoBuild']})")
    except Exception as exc:  # alte Daten behalten, Fehler vermerken
        entry["error"] = str(exc)
        print(f":{tag}: FEHLER: {exc}", file=sys.stderr)
    entry["lastChecked"] = today().isoformat()
    return entry


COPY_JS = """<script>
function copyFromRef(el, ref) {
  navigator.clipboard.writeText(ref).then(function () {
    var old = el.textContent;
    el.textContent = "\\u2713 kopiert";
    setTimeout(function () { el.textContent = old; }, 1000);
  }).catch(function () {
    window.prompt("Manuell kopieren (Strg+C):", ref);
  });
}
</script>"""


def clickable(tag: str, digest: str, label: str) -> str:
    """Klickbares <code>-Element: kopiert den gepinnten FROM-Verweis."""
    ref = f"{IMAGE}:{tag}@{digest}"
    return (f'<code style="cursor:pointer" onclick="copyFromRef(this, \'{ref}\')" '
            f'title="Klicken kopiert: {ref}">{label}</code>')


def render_row(tag: str, e: dict) -> str:
    digest = e.get("amd64Digest", "-")
    if "error" in e or "correttoVersion" not in e:
        return (f"| {clickable(tag, digest, ':' + tag) if digest != '-' else ':' + tag} "
                f"| `{short(digest) if digest != '-' else '–'}` "
                f"| ⚠️ {e.get('error', 'keine Daten')} | – | – | – | – |")
    return (f"| {clickable(tag, digest, ':' + tag)} "
            f"| {clickable(tag, digest, short(digest))} "
            f"| {e['javaVersion']} | {e['correttoVersion']} | {e['correttoBuild']} "
            f"| {e.get('firstSeen', '–')} | {e.get('lastChecked', '–')} |")


TABLE_HEADER = ("| Base-Image-Tag | amd64-Digest | OpenJDK | Corretto | Corretto-Build "
                "| Erstmals gesehen | Zuletzt geprüft |\n"
                "|---|---|---|---|---|---|---|")


def render_site(data: dict) -> str:
    """Rendert docs/index.md (wird von GitHub Pages als Website ausgeliefert)."""
    base_rows = "\n".join(render_row(t, e) for t, e in data["tags"].items())

    snapshots = data.get("snapshots", {})
    if snapshots:
        def sort_key(item):
            m = SNAPSHOT_RE.match(item[0])
            d = "".join(m.groups()) if m else "00000000"
            return (d, item[0])
        snapshot_rows = "\n".join(
            render_row(t, e)
            for t, e in sorted(snapshots.items(), key=sort_key, reverse=True))
        snapshot_section = f"""## Neue Snapshot-Tags (x86_64)

Datierte Snapshot-Tags, die seit dem letzten Lauf neu im Registry auftauchten
(Discovery-Fenster: {SNAPSHOT_TAG_MAX_AGE_DAYS} Tag(e); Aufbewahrung:
{SNAPSHOT_HISTORY_DAYS} Tage). Die Tags enthalten nur ein Datum, keine Uhrzeit.

{TABLE_HEADER}
{snapshot_rows}"""
    else:
        snapshot_section = f"""## Neue Snapshot-Tags (x86_64)

Aktuell keine neuen datierten Snapshot-Tags im Discovery-Fenster
({SNAPSHOT_TAG_MAX_AGE_DAYS} Tag(e); Aufbewahrung: {SNAPSHOT_HISTORY_DAYS} Tage)."""

    return f"""---
title: JVM-Versionen in den AWS Lambda Java Base Images
---

{COPY_JS}

# JVM-Versionen in den AWS Lambda Java Base Images

**Welche Amazon-Corretto-Version steckt in welchem AWS Lambda Java Base Image?**
Diese Seite wird täglich automatisch per GitHub Action aktualisiert, indem die
Images von `public.ecr.aws/lambda/java` geprüft werden
([so funktioniert es](https://github.com/hamburml/aws-base-lambda-image-corretto-version#readme)).

> ⚠️ Inoffizielles Community-Projekt – AWS dokumentiert diese Zuordnung selbst nicht.
> Alle Angaben ohne Gewähr; für die Richtigkeit der Daten wird keine Haftung übernommen.

*Letzte Aktualisierung: {data.get('generatedAt', '–')}*

💡 **Klick auf einen Tag oder Digest** kopiert den gepinnten Verweis
(`public.ecr.aws/lambda/java:<tag>@sha256:<amd64-digest>`) – so wie er in einem
Dockerfile hinter `FROM` stehen muss.

## Base-Image-Tags

{TABLE_HEADER}
{base_rows}

{snapshot_section}

## Erläuterung

- **Base-Image-Tag**: Der Multi-Arch-Tag von `public.ecr.aws/lambda/java`.
- **amd64-Digest**: Digest des `linux/amd64`-Manifests hinter dem Tag (gekürzt).
  Tags sind mutable – der Digest identifiziert den Inhalt eindeutig.
- **OpenJDK / Corretto / Corretto-Build**: Ausgabe von `java -version` im Image.
- **Erstmals gesehen**: Datum, an dem dieser Digest hier zuerst auftauchte.

Rohdaten: [`data/versions.json`](https://github.com/hamburml/aws-base-lambda-image-corretto-version/blob/main/data/versions.json)
"""


def main() -> int:
    data = {"tags": {}, "snapshots": {}}
    if DATA_FILE.exists():
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    data.setdefault("tags", {})
    data.setdefault("snapshots", {})

    token = ecr_token()

    # Bekannte Digests: gleicher Digest = gleicher Inhalt, kein erneuter Pull nötig
    def known():
        return {e["amd64Digest"]: {k: e[k] for k in
                                   ("javaVersion", "correttoVersion",
                                    "correttoBuild", "rawOutput")}
                for e in list(data["tags"].values()) + list(data["snapshots"].values())
                if "amd64Digest" in e and "correttoVersion" in e}

    # 1. Base-Image-Tags
    for tag in TAGS:
        data["tags"][tag] = process_tag(
            tag, data["tags"].get(tag, {}), known(), token)

    # 2. Neue Snapshot-Tags (seit dem letzten Lauf)
    print(f"Suche Snapshot-Tags der letzten {SNAPSHOT_TAG_MAX_AGE_DAYS} Tag(e) ...")
    for tag in recent_snapshot_tags(token):
        existing = data["snapshots"].get(tag, {})
        if existing and "correttoVersion" in existing and "error" not in existing:
            existing["lastChecked"] = today().isoformat()
            continue
        if not existing:
            print(f"Neuer Snapshot-Tag entdeckt: {tag}")
        data["snapshots"][tag] = process_tag(tag, existing, known(), token)

    # 3. Alte Snapshot-Einträge aufräumen
    cutoff = today().toordinal() - SNAPSHOT_HISTORY_DAYS
    for tag in list(data["snapshots"]):
        first_seen = data["snapshots"][tag].get("firstSeen", "")
        if first_seen and date.fromisoformat(first_seen).toordinal() < cutoff:
            print(f"Snapshot-Tag entfernt (älter als {SNAPSHOT_HISTORY_DAYS} Tage): {tag}")
            del data["snapshots"][tag]

    data["generatedAt"] = now()
    data["source"] = IMAGE
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    SITE_FILE.parent.mkdir(parents=True, exist_ok=True)
    SITE_FILE.write_text(render_site(data), encoding="utf-8")
    print(f"Aktualisiert: {DATA_FILE.relative_to(REPO_ROOT)}, "
          f"{SITE_FILE.relative_to(REPO_ROOT)}")

    errors = sum(1 for e in list(data["tags"].values())
                 + list(data["snapshots"].values()) if "error" in e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
