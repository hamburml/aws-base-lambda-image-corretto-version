#!/usr/bin/env python3
"""
Ermittelt die JVM-Versionen (Amazon Corretto) in den AWS Lambda Java Base Images
und den dazu passenden Maven/Corretto-Build-Images von Docker Hub - jeweils für
x86_64 (amd64) und arm64.

Ablauf:
  1. Base-Tags (z. B. "25"): Manifest-Liste via Registry-API lesen und die
     amd64-/arm64-Digests bestimmen (ohne Pull). Nur wenn ein Digest unbekannt/
     geändert ist: Image der Architektur pullen und `java -version` ausführen
     (arm64 via QEMU-Emulation).
  2. Snapshot-Tags (z. B. "25.2026.07.11.03-x86_64"): Datierte Tags, deren
     Datum max. SNAPSHOT_TAG_MAX_AGE_DAYS zurückliegt (Default: 1 = seit dem
     letzten täglichen Lauf; die Tags enthalten keine Uhrzeit, daher werden
     gestern + heute erfasst). x86_64- und arm64-Varianten werden zu einem
     Eintrag gruppiert. Ist ein Digest bereits bekannt, wird die Version ohne
     Pull übernommen - gleicher Digest = gleicher Inhalt.
  3. Maven-Gegenstück: Pro Corretto-Major-Version das neueste stabile
     maven:x.y.z-amazoncorretto-<major>-Image von Docker Hub bestimmen
     (Digests via Hub-API, ohne Pull) und dessen Corretto-Version je
     Architektur auslesen.
  4. data/versions.json schreiben und die Website neu rendern:
     docs/index.md (englisch) + docs/index.de.md (deutsch).
     Snapshot-Einträge werden nach SNAPSHOT_HISTORY_DAYS (Default: 14) entfernt.

Nur Standardbibliothek + Docker nötig. Für arm64-Images muss QEMU/binfmt
eingerichtet sein (GitHub Actions: docker/setup-qemu-action).
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
IMAGE = f"public.ecr.aws/{REPOSITORY}"

# Docker Hub: offizielles maven-Image (Build-Gegenstück zur Lambda-Runtime)
MAVEN_IMAGE = "maven"
HUB_TAGS_API = "https://hub.docker.com/v2/repositories/library/maven/tags"

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = REPO_ROOT / "data" / "versions.json"
SITE_EN = REPO_ROOT / "docs" / "index.md"
SITE_DE = REPO_ROOT / "docs" / "index.de.md"

TAGS = os.environ.get("TAGS", "8.al2 11 17 21 25").split()
SNAPSHOT_TAG_MAX_AGE_DAYS = int(os.environ.get("SNAPSHOT_TAG_MAX_AGE_DAYS", "1"))
SNAPSHOT_HISTORY_DAYS = int(os.environ.get("SNAPSHOT_HISTORY_DAYS", "14"))
CLEANUP_IMAGES = os.environ.get("CLEANUP_IMAGES", "").lower() in ("1", "true", "yes")

# Intern genutzte Architektur-Namen (docker --platform linux/<arch>) und ihre
# Entsprechung in den Lambda-Snapshot-Tag-Suffixen
ARCHES = ("amd64", "arm64")
LAMBDA_ARCH_SUFFIX = {"amd64": "x86_64", "arm64": "arm64"}

JAVA_RE = re.compile(r'openjdk version "([^"]+)"')
CORRETTO_RE = re.compile(r"Corretto-([\d.]+)\s+\(build ([^)]+)\)")
# z. B. 25.2026.07.11.03-x86_64 oder 8.al2.2026.07.17.16-arm64
SNAPSHOT_RE = re.compile(r"^(.+\.(\d{4})\.(\d{2})\.(\d{2})\.\d{2})-(x86_64|arm64)$")

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


# ---------------------------------------------------------------- ECR Public

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


def recent_snapshot_tags(token: str) -> dict:
    """Datierte Snapshot-Tags im Discovery-Fenster, gruppiert nach Präfix.

    Rückgabe: {"25.2026.07.11.03": {"x86_64": "25.2026.07.11.03-x86_64", ...}}
    """
    found: dict = {}
    for tag in list_recent_tags(token):
        m = SNAPSHOT_RE.match(tag)
        if not m:
            continue
        tag_date = date(int(m.group(2)), int(m.group(3)), int(m.group(4)))
        if (today() - tag_date).days <= SNAPSHOT_TAG_MAX_AGE_DAYS:
            found.setdefault(m.group(1), {})[m.group(5)] = tag
    return dict(sorted(found.items()))


def manifest_digests(tag: str, token: str) -> dict:
    """Digests eines Tags je Architektur (ohne Pull): {"amd64": ..., "arm64": ...}.

    Multi-Arch-Tags (z. B. ":25") liefern eine Manifest-Liste. Datierte
    Snapshot-Tags sind arch-spezifisch (einzelnes Manifest) - dann zählt die
    SHA-256-Prüfsumme der Manifest-Bytes (so ist der Digest per Definition
    festgelegt); die Architektur steht im Manifest selbst.
    """
    with registry_get(f"manifests/{tag}", token) as resp:
        body = resp.read()
    doc = json.loads(body)
    if "manifests" in doc:
        digests = {}
        for manifest in doc["manifests"]:
            platform = manifest.get("platform", {})
            arch = platform.get("architecture")
            if platform.get("os") == "linux" and arch in ARCHES:
                digests[arch] = manifest["digest"]
        if not digests:
            raise RuntimeError(f"Keine linux-Manifeste für Tag '{tag}' gefunden")
        return digests
    # Einzelnes Manifest: OCI-Manifeste haben kein Architektur-Feld - die
    # Architektur steht bei den Snapshot-Tags im Suffix (-x86_64/-arm64).
    suffix = tag.rsplit("-", 1)[-1]
    arch = {v: k for k, v in LAMBDA_ARCH_SUFFIX.items()}.get(suffix)
    if not arch:
        raise RuntimeError(f"Architektur für Tag '{tag}' nicht bestimmbar")
    return {arch: "sha256:" + hashlib.sha256(body).hexdigest()}


# ------------------------------------------------------------------ Docker Hub

def hub_get(suffix: str) -> dict:
    with urllib.request.urlopen(f"{HUB_TAGS_API}{suffix}", timeout=30) as resp:
        return json.load(resp)


def latest_maven_tag(major: str) -> str | None:
    """Neuestes stabiles maven:x.y.z-amazoncorretto-<major>-Tag (keine Aliase/RCs)."""
    pat = re.compile(rf"^(\d+)\.(\d+)\.(\d+)-amazoncorretto-{re.escape(major)}$")
    candidates = []
    for result in hub_get(f"?name=amazoncorretto-{major}&page_size=100").get("results", []):
        m = pat.match(result["name"])
        if m:
            candidates.append((tuple(int(g) for g in m.groups()), result["name"]))
    return max(candidates)[1] if candidates else None


def hub_digests(tag: str) -> dict:
    """Digests eines Docker-Hub-Tags je Architektur (ohne Pull)."""
    digests = {}
    for image in hub_get(f"/{tag}").get("images", []):
        if image.get("os") == "linux" and image.get("architecture") in ARCHES:
            digests[image["architecture"]] = image["digest"]
    if not digests:
        raise RuntimeError(f"Keine linux-Manifeste für '{MAVEN_IMAGE}:{tag}' gefunden")
    return digests


# ------------------------------------------------------------------ gemeinsam

def read_java_version(tag: str, arch: str, repo: str = IMAGE) -> dict:
    """Pullt das Image einer Architektur und liest `java -version` aus."""
    image = f"{repo}:{tag}"
    platform = f"linux/{arch}"
    try:
        subprocess.run(["docker", "pull", "--platform", platform, image],
                       check=True, capture_output=True, text=True)
        result = subprocess.run(
            ["docker", "run", "--rm", "--platform", platform,
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


def major_of(entry: dict) -> str | None:
    """Java-Major-Version (z. B. '25') aus der amd64-Corretto-Version eines Eintrags."""
    version = entry.get("arches", {}).get("amd64", {}).get("correttoVersion")
    if not version:  # Einträge, die es nur als arm64 gibt
        version = entry.get("arches", {}).get("arm64", {}).get("correttoVersion")
    return version.split(".")[0] if version else None


def update_arch(entry: dict, arch: str, digest: str, known: dict,
                tag: str, repo: str) -> None:
    """Aktualisiert einen Architektur-Zweig (entry["arches"][arch]) anhand des Digests."""
    arches = entry.setdefault("arches", {})
    a = arches.get(arch, {})
    label = f"{repo}:{tag} [{arch}]"
    if digest == a.get("digest") and "correttoVersion" in a:
        print(f"{label}: unverändert ({short(digest)}), kein Pull nötig")
    elif digest in known:
        info = known[digest]
        a.update(info)
        a["digest"] = digest
        a["firstSeen"] = today().isoformat()
        print(f"{label}: Digest bekannt ({short(digest)}), Version ohne Pull übernommen")
    else:
        print(f"{label}: neuer Digest {short(digest)}, pulle Image ...")
        a.update(read_java_version(tag, arch, repo))
        a["digest"] = digest
        a["firstSeen"] = today().isoformat()
    a["lastChecked"] = today().isoformat()
    arches[arch] = a
    if "correttoVersion" in a:
        print(f"{label}: Corretto {a['correttoVersion']} ({a['correttoBuild']})")


def process_base_tag(tag: str, entry: dict, known: dict, token: str) -> dict:
    entry.pop("error", None)
    try:
        for arch, digest in manifest_digests(tag, token).items():
            update_arch(entry, arch, digest, known, tag, IMAGE)
    except Exception as exc:  # alte Daten behalten, Fehler vermerken
        entry["error"] = str(exc)
        print(f":{tag}: FEHLER: {exc}", file=sys.stderr)
    entry["lastChecked"] = today().isoformat()
    return entry


def process_snapshot(prefix: str, arch_tags: dict, entry: dict,
                     known: dict, token: str) -> dict:
    entry.pop("error", None)
    try:
        for suffix, tag in sorted(arch_tags.items()):
            arch = {"x86_64": "amd64", "arm64": "arm64"}[suffix]
            digest = manifest_digests(tag, token)[arch]
            update_arch(entry, arch, digest, known, tag, IMAGE)
            entry["arches"][arch]["tag"] = tag
    except Exception as exc:
        entry["error"] = str(exc)
        print(f":{prefix}: FEHLER: {exc}", file=sys.stderr)
    entry.setdefault("firstSeen", today().isoformat())
    entry["lastChecked"] = today().isoformat()
    return entry


def process_maven(major: str, entry: dict) -> dict:
    """Aktualisiert den Maven-Image-Eintrag für eine Java-Major-Version."""
    entry.pop("error", None)
    try:
        tag = latest_maven_tag(major)
        if not tag:
            raise RuntimeError(
                f"Kein stabiles {MAVEN_IMAGE}:x.y.z-amazoncorretto-{major}-Tag gefunden")
        entry["mavenTag"] = tag
        for arch, digest in hub_digests(tag).items():
            update_arch(entry, arch, digest, {}, tag, MAVEN_IMAGE)
    except Exception as exc:
        entry["error"] = str(exc)
        print(f"{MAVEN_IMAGE} (Corretto {major}): FEHLER: {exc}", file=sys.stderr)
    entry["lastChecked"] = today().isoformat()
    return entry


def migrate(entry: dict) -> dict:
    """Hebt das alte flache Format (amd64Digest etc.) auf das arches-Format."""
    if "arches" not in entry and "amd64Digest" in entry:
        a = {k: entry.pop(k) for k in
             ("javaVersion", "correttoVersion", "correttoBuild", "rawOutput", "firstSeen")
             if k in entry}
        a["digest"] = entry.pop("amd64Digest")
        entry["arches"] = {"amd64": a}
    return entry


# ------------------------------------------------------------------ Website

COPY_JS = """<script>
function copyFromRef(el, ref) {
  navigator.clipboard.writeText(ref).then(function () {
    var old = el.textContent;
    el.textContent = "\\u2713";
    setTimeout(function () { el.textContent = old; }, 1000);
  }).catch(function () {
    window.prompt("Manuell kopieren (Strg+C):", ref);
  });
}
</script>"""


def clickable(image: str, tag: str, digest: str, label: str, title_prefix: str) -> str:
    """Klickbares <code>-Element: kopiert den gepinnten FROM-Verweis."""
    ref = f"{image}:{tag}@{digest}"
    return (f'<code style="cursor:pointer" onclick="copyFromRef(this, \'{ref}\')" '
            f'title="{title_prefix}{ref}">{label}</code>')


def digest_lines(entry: dict, image: str, default_tag: str, title_prefix: str) -> str:
    """Zeilen 'x86_64: <digest>' / 'arm64: <digest>' eines Eintrags (Click-to-copy)."""
    lines = []
    for arch in ARCHES:
        a = entry.get("arches", {}).get(arch, {})
        if not a.get("digest"):
            continue
        tag = a.get("tag", default_tag)  # Snapshots haben je Arch ein eigenes Tag
        label = LAMBDA_ARCH_SUFFIX[arch]
        lines.append(f"{label}: {clickable(image, tag, a['digest'], short(a['digest']), title_prefix)}")
    return "<br>".join(lines) if lines else "–"


def version_cells(entry: dict) -> tuple:
    """(OpenJDK, Corretto, Corretto-Build) der amd64-Seite, mit ⚠️ bei arm64-Abweichung."""
    a = entry.get("arches", {}).get("amd64", {})
    if "correttoVersion" not in a:  # z. B. reine arm64-Einträge
        a = entry.get("arches", {}).get("arm64", {})
    if "correttoVersion" not in a:
        return ("–", "–", "–")
    corretto = a["correttoVersion"]
    other_arch = "arm64" if a is entry["arches"].get("amd64") else "amd64"
    other = entry.get("arches", {}).get(other_arch, {})
    if other.get("correttoVersion") and other["correttoVersion"] != corretto:
        corretto += f"<br>⚠️ {other_arch}: {other['correttoVersion']}"
    return (a.get("javaVersion", "–"), corretto, a.get("correttoBuild", "–"))


def render_row(tag: str, e: dict, maven: dict, s: dict) -> str:
    tp = s["copy_title"]
    digests = digest_lines(e, IMAGE, tag, tp)
    java_version, corretto, build = version_cells(e)

    # Maven-Gegenstück (gleiche Java-Major-Version)
    m = maven.get(major_of(e) or "", {})
    m_arches = m.get("arches", {})
    if m.get("mavenTag") and "correttoVersion" in m_arches.get("amd64", {}):
        m_amd = m_arches["amd64"]
        maven_cell = "x86_64: " + clickable(MAVEN_IMAGE, m["mavenTag"], m_amd["digest"],
                                            f"{MAVEN_IMAGE}:{m['mavenTag']}", tp)
        if m_arches.get("arm64", {}).get("digest"):
            maven_cell += (f"<br>arm64: {clickable(MAVEN_IMAGE, m['mavenTag'], m_arches['arm64']['digest'], short(m_arches['arm64']['digest']), tp)}")
        match = " ✓" if m_amd["correttoVersion"] == corretto.split("<br>")[0] else " ⚠️"
        maven_version_cell = m_amd["correttoVersion"] + match
    else:
        maven_cell = maven_version_cell = "–"

    if "error" in e and corretto == "–":
        java_version = f"⚠️ {e['error']}"
    first_seen = e.get("arches", {}).get("amd64", {}).get(
        "firstSeen", e.get("firstSeen", "–"))
    return (f"| :{tag} | {digests} | {java_version} | {corretto} | {build} "
            f"| {maven_cell} | {maven_version_cell} "
            f"| {first_seen} | {e.get('lastChecked', '–')} |")


STRINGS = {
    "en": {
        "title": "JVM versions in the AWS Lambda Java base images",
        "heading": "JVM versions in the AWS Lambda Java base images",
        "switch": "**English** | [Deutsch](index.de.html)",
        "intro": ("**Which Amazon Corretto version ships with which AWS Lambda Java base image?** "
                  "This page is updated daily by a GitHub Action that inspects the images at "
                  "`public.ecr.aws/lambda/java` – for **x86_64 and arm64** "
                  "([how it works](https://github.com/hamburml/aws-base-lambda-image-corretto-version#readme))."),
        "disclaimer": ("⚠️ Unofficial community project – AWS does not document this mapping itself. "
                       "All information without warranty; no liability is accepted for the accuracy of the data."),
        "updated": "Last updated",
        "hint": ("💡 **Click a digest** to copy the pinned reference "
                 "(`<image>:<tag>@sha256:<digest>`) – exactly as it must appear "
                 "after `FROM` in a Dockerfile."),
        "base_section": "Base image tags",
        "snapshot_section": "New snapshot tags",
        "snapshot_text": ("Dated snapshot tags that appeared in the registry since the last run "
                          f"(discovery window: {SNAPSHOT_TAG_MAX_AGE_DAYS} day(s); retention: "
                          f"{SNAPSHOT_HISTORY_DAYS} days). The tags carry a date only, no time. "
                          "Each snapshot exists as an arch-specific tag (`-x86_64` / `-arm64`)."),
        "snapshot_empty": ("No new dated snapshot tags within the discovery window "
                           f"({SNAPSHOT_TAG_MAX_AGE_DAYS} day(s); retention: {SNAPSHOT_HISTORY_DAYS} days)."),
        "table_header": ("| Base image tag | Digests (x86_64 / arm64) | OpenJDK | Corretto | Corretto build "
                         "| Maven image | Maven Corretto | First seen | Last checked |"),
        "explanation": [
            "**Base image tag**: the multi-arch tag of `public.ecr.aws/lambda/java` (snapshot tags are arch-specific).",
            "**Digests**: digests of the `x86_64` (amd64) and `arm64` manifests behind the tag (shortened). Tags are mutable – a digest identifies the content uniquely. Click to copy the full pin.",
            "**OpenJDK / Corretto / Corretto build**: output of `java -version` inside the x86_64 image. The arm64 image is verified too; any deviation is flagged (⚠️ arm64: …).",
            "**Maven image**: the latest stable `maven:x.y.z-amazoncorretto-<major>` tag on Docker Hub for the same Java major version – i.e. the matching build image. Click `x86_64:` for the x86_64 pin, `arm64:` for the arm64 pin.",
            "**Maven Corretto**: Corretto version of that Maven image (x86_64). ✓ = identical build to the Lambda image (safe e.g. for Project Leyden AOT caches), ⚠️ = different build.",
            "**First seen**: the date this digest showed up here first.",
        ],
        "rawdata": "Raw data",
        "legend_heading": "Notes",
        "copy_title": "Click to copy: ",
    },
    "de": {
        "title": "JVM-Versionen in den AWS Lambda Java Base Images",
        "heading": "JVM-Versionen in den AWS Lambda Java Base Images",
        "switch": "[English](index.html) | **Deutsch**",
        "intro": ("**Welche Amazon-Corretto-Version steckt in welchem AWS Lambda Java Base Image?** "
                  "Diese Seite wird täglich automatisch per GitHub Action aktualisiert, indem die "
                  "Images von `public.ecr.aws/lambda/java` geprüft werden – für **x86_64 und arm64** "
                  "([so funktioniert es](https://github.com/hamburml/aws-base-lambda-image-corretto-version#readme))."),
        "disclaimer": ("⚠️ Inoffizielles Community-Projekt – AWS dokumentiert diese Zuordnung selbst nicht. "
                       "Alle Angaben ohne Gewähr; für die Richtigkeit der Daten wird keine Haftung übernommen."),
        "updated": "Letzte Aktualisierung",
        "hint": ("💡 **Klick auf einen Digest** kopiert den gepinnten Verweis "
                 "(`<image>:<tag>@sha256:<digest>`) – so wie er in einem "
                 "Dockerfile hinter `FROM` stehen muss."),
        "base_section": "Base-Image-Tags",
        "snapshot_section": "Neue Snapshot-Tags",
        "snapshot_text": ("Datierte Snapshot-Tags, die seit dem letzten Lauf neu im Registry auftauchten "
                          f"(Discovery-Fenster: {SNAPSHOT_TAG_MAX_AGE_DAYS} Tag(e); Aufbewahrung: "
                          f"{SNAPSHOT_HISTORY_DAYS} Tage). Die Tags enthalten nur ein Datum, keine Uhrzeit. "
                          "Jeder Snapshot existiert als arch-spezifisches Tag (`-x86_64` / `-arm64`)."),
        "snapshot_empty": ("Aktuell keine neuen datierten Snapshot-Tags im Discovery-Fenster "
                           f"({SNAPSHOT_TAG_MAX_AGE_DAYS} Tag(e); Aufbewahrung: {SNAPSHOT_HISTORY_DAYS} Tage)."),
        "table_header": ("| Base-Image-Tag | Digests (x86_64 / arm64) | OpenJDK | Corretto | Corretto-Build "
                         "| Maven-Image | Maven-Corretto | Erstmals gesehen | Zuletzt geprüft |"),
        "explanation": [
            "**Base-Image-Tag**: Der Multi-Arch-Tag von `public.ecr.aws/lambda/java` (Snapshot-Tags sind arch-spezifisch).",
            "**Digests**: Digests der `x86_64` (amd64)- und `arm64`-Manifeste hinter dem Tag (gekürzt). Tags sind mutable – ein Digest identifiziert den Inhalt eindeutig. Klick kopiert den vollständigen Pin.",
            "**OpenJDK / Corretto / Corretto-Build**: Ausgabe von `java -version` im x86_64-Image. Das arm64-Image wird ebenfalls geprüft; Abweichungen sind markiert (⚠️ arm64: …).",
            "**Maven-Image**: Das neueste stabile `maven:x.y.z-amazoncorretto-<major>`-Tag auf Docker Hub mit derselben Java-Major-Version – also das passende Build-Image. `x86_64:` kopiert den x86_64-Pin, `arm64:` den arm64-Pin.",
            "**Maven-Corretto**: Corretto-Version dieses Maven-Images (x86_64). ✓ = identischer Build wie das Lambda-Image (z. B. für Project-Leyden-AOT-Caches nutzbar), ⚠️ = abweichender Build.",
            "**Erstmals gesehen**: Datum, an dem dieser Digest hier zuerst auftauchte.",
        ],
        "rawdata": "Rohdaten",
        "legend_heading": "Erläuterung",
        "copy_title": "Klicken kopiert: ",
    },
}


def render_site(data: dict, lang: str) -> str:
    s = STRINGS[lang]
    maven = data.get("maven", {})
    header = s["table_header"] + "\n" + "|---|---|---|---|---|---|---|---|---|---|"

    # absteigend nach Java-Major-Version sortiert (25, 21, 17, 11, 8, ...)
    base_rows = "\n".join(
        render_row(t, e, maven, s)
        for t, e in sorted(data["tags"].items(),
                           key=lambda item: int(major_of(item[1]) or 0),
                           reverse=True))

    snapshots = data.get("snapshots", {})
    if snapshots:
        def sort_key(item):
            m = SNAPSHOT_RE.match(item[0] + "-x86_64")
            d = "".join(m.groups()[:3]) if m else "00000000"
            return (d, item[0])
        snapshot_rows = "\n".join(
            render_row(t, e, maven, s)
            for t, e in sorted(snapshots.items(), key=sort_key, reverse=True))
        snapshot_section = (f"## {s['snapshot_section']}\n\n{s['snapshot_text']}\n\n"
                            f"{header}\n{snapshot_rows}")
    else:
        snapshot_section = f"## {s['snapshot_section']}\n\n{s['snapshot_empty']}"

    explanation = "\n".join(f"- {line}" for line in s["explanation"])

    return f"""---
title: {s['title']}
---

{COPY_JS}

{s['switch']}

# {s['heading']}

{s['intro']}

> {s['disclaimer']}

*{s['updated']}: {data.get('generatedAt', '–')}*

{s['hint']}

## {s['base_section']}

{header}
{base_rows}

{snapshot_section}

## {s['legend_heading']}

{explanation}

{s['rawdata']}: [`data/versions.json`](https://github.com/hamburml/aws-base-lambda-image-corretto-version/blob/main/data/versions.json)
"""


# ------------------------------------------------------------------ main

def main() -> int:
    data = {"tags": {}, "snapshots": {}, "maven": {}}
    if DATA_FILE.exists():
        data = json.loads(DATA_FILE.read_text(encoding="utf-8"))
    data.setdefault("tags", {})
    data.setdefault("snapshots", {})
    data.setdefault("maven", {})

    for section in ("tags", "snapshots", "maven"):
        data[section] = {k: migrate(v) for k, v in data[section].items()}
    # alte flache Snapshot-Schlüssel (mit -x86_64-Suffix) fallen lassen,
    # sie werden im neuen Gruppenformat neu entdeckt
    data["snapshots"] = {k: v for k, v in data["snapshots"].items()
                         if not k.endswith("-x86_64")}

    token = ecr_token()

    # Bekannte Digests: gleicher Digest = gleicher Inhalt, kein erneuter Pull nötig
    def known():
        result = {}
        for section in ("tags", "snapshots", "maven"):
            for e in data[section].values():
                for a in e.get("arches", {}).values():
                    if a.get("digest") and "correttoVersion" in a:
                        result[a["digest"]] = {k: a[k] for k in
                                               ("javaVersion", "correttoVersion",
                                                "correttoBuild", "rawOutput")}
        return result

    # 1. Base-Image-Tags
    for tag in TAGS:
        data["tags"][tag] = process_base_tag(
            tag, data["tags"].get(tag, {}), known(), token)

    # 2. Neue Snapshot-Tags (seit dem letzten Lauf)
    print(f"Suche Snapshot-Tags der letzten {SNAPSHOT_TAG_MAX_AGE_DAYS} Tag(e) ...")
    for prefix, arch_tags in recent_snapshot_tags(token).items():
        existing = data["snapshots"].get(prefix, {})
        done = existing.get("arches", {}) and "error" not in existing and all(
            "correttoVersion" in existing["arches"].get(
                {"x86_64": "amd64", "arm64": "arm64"}[suffix], {})
            for suffix in arch_tags)
        if done:
            existing["lastChecked"] = today().isoformat()
            continue
        if not existing:
            print(f"Neuer Snapshot-Tag entdeckt: {prefix} ({', '.join(sorted(arch_tags))})")
        data["snapshots"][prefix] = process_snapshot(
            prefix, arch_tags, existing, known(), token)

    # 3. Alte Snapshot-Einträge aufräumen
    cutoff = today().toordinal() - SNAPSHOT_HISTORY_DAYS
    for prefix in list(data["snapshots"]):
        first_seen = data["snapshots"][prefix].get("firstSeen", "")
        if first_seen and date.fromisoformat(first_seen).toordinal() < cutoff:
            print(f"Snapshot entfernt (älter als {SNAPSHOT_HISTORY_DAYS} Tage): {prefix}")
            del data["snapshots"][prefix]

    # 4. Maven-Gegenstück für alle vorkommenden Java-Major-Versionen
    majors = sorted({m for m in (major_of(e) for e in
                                 list(data["tags"].values())
                                 + list(data["snapshots"].values()))
                     if m})
    for major in majors:
        data["maven"][major] = process_maven(major, data["maven"].get(major, {}))

    data["generatedAt"] = now()
    data["source"] = IMAGE
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    SITE_EN.parent.mkdir(parents=True, exist_ok=True)
    SITE_EN.write_text(render_site(data, "en"), encoding="utf-8")
    SITE_DE.write_text(render_site(data, "de"), encoding="utf-8")
    print(f"Aktualisiert: {DATA_FILE.relative_to(REPO_ROOT)}, "
          f"{SITE_EN.relative_to(REPO_ROOT)}, {SITE_DE.relative_to(REPO_ROOT)}")

    errors = sum(1 for section in ("tags", "snapshots", "maven")
                 for e in data[section].values() if "error" in e)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
