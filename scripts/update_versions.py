#!/usr/bin/env python3
"""
Determines the JVM versions (Amazon Corretto) in the AWS Lambda Java base images
and the matching Maven/Corretto build images from Docker Hub - for both
x86_64 (amd64) and arm64.

Flow:
  1. Base tags (e.g. "25"): read the manifest list via the registry API and
     determine the amd64/arm64 digests (no pull). Only if a digest is unknown/
     changed: pull the image of that architecture and run `java -version`
     (arm64 via QEMU emulation).
  2. Snapshot tags (e.g. "25.2026.07.11.03", published as arch-specific
     "-x86_64"/"-arm64" tags and/or as a multi-arch tag without suffix): dated
     tags whose date is at most SNAPSHOT_TAG_MAX_AGE_DAYS in the past
     (default: 1 = since the last daily run; the tags carry no time, so
     yesterday + today are covered). The tag list is capped (1000 entries) and
     not reliably ordered, so it only provides candidate prefixes: the tag
     variants of each tracked prefix are then probed directly via the
     manifests endpoint, which also backfills variants the list missed.
     If a digest is already known, the version is adopted without a pull -
     same digest = same content.
  3. Maven counterpart: for each Corretto major version, track the
     MAVEN_HISTORY_TAGS (default: 30) newest stable
     maven:x.y.z-amazoncorretto-<major> tags from Docker Hub (digests via the
     Hub API, no pull) and read their Corretto versions per architecture.
     Pulls are limited to MAVEN_HISTORY_MAX_PULLS (default: 10) tags with
     unknown digests per run - Docker Hub rate-limits pulls, and the digest
     cache in versions.json lets later runs resume the backfill.
  4. Write data/versions.json and re-render the website (docs/index.md): the
     base/snapshot tables show only Maven tags whose Corretto version exactly
     matches the image of the row; a Maven history table lists all tracked
     tags per Java LTS. Snapshot entries are removed after
     SNAPSHOT_HISTORY_DAYS (default: 90).

Only the standard library + Docker are needed. For arm64 images, QEMU/binfmt
must be set up (GitHub Actions: docker/setup-qemu-action).
Environment variables: TAGS, SNAPSHOT_TAG_MAX_AGE_DAYS, SNAPSHOT_HISTORY_DAYS,
MAVEN_HISTORY_TAGS, MAVEN_HISTORY_MAX_PULLS,
CLEANUP_IMAGES ("1" = delete pulled images again, for CI).
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
GALLERY_URL = "https://gallery.ecr.aws/lambda/java"

# Docker Hub: official maven image (build counterpart to the Lambda runtime)
MAVEN_IMAGE = "maven"
HUB_TAGS_API = "https://hub.docker.com/v2/repositories/library/maven/tags"

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_FILE = REPO_ROOT / "data" / "versions.json"
SITE = REPO_ROOT / "docs" / "index.md"

TAGS = os.environ.get("TAGS", "8.al2 11 17 21 25").split()
SNAPSHOT_TAG_MAX_AGE_DAYS = int(os.environ.get("SNAPSHOT_TAG_MAX_AGE_DAYS", "1"))
SNAPSHOT_HISTORY_DAYS = int(os.environ.get("SNAPSHOT_HISTORY_DAYS", "90"))
# Maven history: how many stable tags per Java major version are tracked, and
# how many tags with unknown digests may be pulled per run (Hub rate limits)
MAVEN_HISTORY_TAGS = int(os.environ.get("MAVEN_HISTORY_TAGS", "30"))
MAVEN_HISTORY_MAX_PULLS = int(os.environ.get("MAVEN_HISTORY_MAX_PULLS", "10"))
CLEANUP_IMAGES = os.environ.get("CLEANUP_IMAGES", "").lower() in ("1", "true", "yes")

# Internally used architecture names (docker --platform linux/<arch>) and their
# counterpart in the Lambda snapshot tag suffixes
ARCHES = ("amd64", "arm64")
LAMBDA_ARCH_SUFFIX = {"amd64": "x86_64", "arm64": "arm64"}

JAVA_RE = re.compile(r'openjdk version "([^"]+)"')
CORRETTO_RE = re.compile(r"Corretto-([\d.]+)\s+\(build ([^)]+)\)")
# e.g. 25.2026.07.11.03 or 8.al2.2026.07.17.16-arm64 (arch suffix optional)
SNAPSHOT_RE = re.compile(r"^(.+\.(\d{4})\.(\d{2})\.(\d{2})\.\d{2})(?:-(x86_64|arm64))?$")
# Maven version at the start of a tag (e.g. "3.9.16-amazoncorretto-25")
MVN_VER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)")

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
    """Tag list of the repository.

    public.ecr.aws returns at most 1000 tags without working pagination;
    however, the response contains the newest tags first (verified: the
    dated tags of the last ~10 days are fully included).
    For a discovery window of a few days, the first page is sufficient.
    """
    with registry_get("tags/list?n=1000", token) as resp:
        return json.load(resp).get("tags", [])


def recent_snapshot_prefixes(token: str) -> list:
    """Dated snapshot tag prefixes within the discovery window.

    The tag list is capped (1000 entries) and not reliably ordered, so this
    only yields candidate prefixes - the tag variants of each prefix are
    probed directly in process_snapshot.
    """
    found = set()
    for tag in list_recent_tags(token):
        m = SNAPSHOT_RE.match(tag)
        if not m:
            continue
        tag_date = date(int(m.group(2)), int(m.group(3)), int(m.group(4)))
        if (today() - tag_date).days <= SNAPSHOT_TAG_MAX_AGE_DAYS:
            found.add(m.group(1))
    return sorted(found)


def manifest_digests(tag: str, token: str) -> dict:
    """Digests of a tag per architecture (no pull): {"amd64": ..., "arm64": ...}.

    Multi-arch tags (e.g. ":25" or dated tags without arch suffix) return a
    manifest list. Arch-specific tags (suffix -x86_64/-arm64) return a single
    manifest - in that case the SHA-256 checksum of the manifest bytes counts
    (that is how the digest is defined) and the architecture comes from the
    suffix (or, if there is none, from the image config blob).
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
            raise RuntimeError(f"No linux manifests found for tag '{tag}'")
        return digests
    # Single manifest: OCI manifests have no architecture field - it is taken
    # from the tag suffix (-x86_64/-arm64) or, if there is none, from the
    # image config blob.
    suffix = tag.rsplit("-", 1)[-1]
    arch = {v: k for k, v in LAMBDA_ARCH_SUFFIX.items()}.get(suffix)
    if not arch:
        arch = config_arch(doc, token)
    if not arch:
        raise RuntimeError(f"Cannot determine architecture for tag '{tag}'")
    return {arch: "sha256:" + hashlib.sha256(body).hexdigest()}


def config_arch(manifest: dict, token: str) -> str | None:
    """Architecture of a single-manifest image, read from its config blob."""
    try:
        digest = manifest.get("config", {}).get("digest", "")
        with registry_get(f"blobs/{digest}", token) as resp:
            arch = json.load(resp).get("architecture")
    except Exception:
        return None
    return arch if arch in ARCHES else None


def probe_snapshot_tag(prefix: str, token: str) -> dict:
    """All tag variants of a snapshot prefix and their per-arch digests.

    Probes the arch-specific tags and the multi-arch no-suffix tag directly
    (the tag list is capped, so variants missing from it are found here).
    Returns {arch: (tag, digest)}; arch-specific tags take precedence.
    """
    found: dict = {}
    for tag in (f"{prefix}-x86_64", f"{prefix}-arm64", prefix):
        try:
            digests = manifest_digests(tag, token)
        except Exception:  # variant does not exist (404) or is not readable
            continue
        for arch, digest in digests.items():
            found.setdefault(arch, (tag, digest))
    return found


# ------------------------------------------------------------------ Docker Hub

def hub_get(suffix: str) -> dict:
    """GET on the Hub tags API; accepts a query suffix or a full (next-)URL."""
    url = suffix if suffix.startswith("http") else f"{HUB_TAGS_API}{suffix}"
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.load(resp)


def stable_maven_tags(major: str) -> list:
    """All stable maven:x.y.z-amazoncorretto-<major> tags (no aliases/RCs),
    newest Maven version first. Paginates the Hub API (the tag list of one
    major version can span several pages)."""
    pat = re.compile(rf"^(\d+)\.(\d+)\.(\d+)-amazoncorretto-{re.escape(major)}$")
    found = {}
    url, pages = f"?name=amazoncorretto-{major}&page_size=100", 0
    while url and pages < 10:  # safety cap on pages
        doc = hub_get(url)
        for result in doc.get("results", []):
            m = pat.match(result["name"])
            if m:
                found[tuple(int(g) for g in m.groups())] = result["name"]
        url, pages = doc.get("next"), pages + 1
    return [found[v] for v in sorted(found, reverse=True)]


def hub_digests(tag: str) -> dict:
    """Digests of a Docker Hub tag per architecture (no pull)."""
    digests = {}
    for image in hub_get(f"/{tag}").get("images", []):
        if image.get("os") == "linux" and image.get("architecture") in ARCHES:
            digests[image["architecture"]] = image["digest"]
    if not digests:
        raise RuntimeError(f"No linux manifests found for '{MAVEN_IMAGE}:{tag}'")
    return digests


# ------------------------------------------------------------------ shared

def read_java_version(tag: str, arch: str, repo: str = IMAGE) -> dict:
    """Pulls the image of one architecture and reads `java -version`."""
    image = f"{repo}:{tag}"
    platform = f"linux/{arch}"
    try:
        subprocess.run(["docker", "pull", "--platform", platform, image],
                       check=True, capture_output=True, text=True)
        result = subprocess.run(
            ["docker", "run", "--rm", "--platform", platform,
             "--entrypoint", "java", image, "-version"],
            check=True, capture_output=True, text=True)
        output = result.stderr + result.stdout  # java -version writes to stderr
    finally:
        if CLEANUP_IMAGES:
            subprocess.run(["docker", "rmi", image], capture_output=True)

    java_match = JAVA_RE.search(output)
    corretto_match = CORRETTO_RE.search(output)
    if not java_match or not corretto_match:
        raise RuntimeError(f"Could not parse 'java -version':\n{output}")
    return {
        "javaVersion": java_match.group(1),
        "correttoVersion": corretto_match.group(1),
        "correttoBuild": corretto_match.group(2),
        "rawOutput": output.strip(),
    }


def short(digest: str) -> str:
    return digest.removeprefix("sha256:")[:12]


def major_of(entry: dict) -> str | None:
    """Java major version (e.g. '25') from the amd64 Corretto version of an entry."""
    version = entry.get("arches", {}).get("amd64", {}).get("correttoVersion")
    if not version:  # entries that exist only as arm64
        version = entry.get("arches", {}).get("arm64", {}).get("correttoVersion")
    return version.split(".")[0] if version else None


def update_arch(entry: dict, arch: str, digest: str, known: dict,
                tag: str, repo: str) -> None:
    """Updates one architecture branch (entry["arches"][arch]) based on the digest."""
    arches = entry.setdefault("arches", {})
    a = arches.get(arch, {})
    label = f"{repo}:{tag} [{arch}]"
    if digest == a.get("digest") and "correttoVersion" in a:
        print(f"{label}: unchanged ({short(digest)}), no pull needed")
    elif digest in known:
        info = known[digest]
        a.update(info)
        a["digest"] = digest
        a["firstSeen"] = today().isoformat()
        print(f"{label}: digest known ({short(digest)}), version adopted without pull")
    else:
        print(f"{label}: new digest {short(digest)}, pulling image ...")
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
    except Exception as exc:  # keep old data, record the error
        entry["error"] = str(exc)
        print(f":{tag}: ERROR: {exc}", file=sys.stderr)
    entry["lastChecked"] = today().isoformat()
    return entry


def process_snapshot(prefix: str, entry: dict, known: dict, token: str) -> dict:
    entry.pop("error", None)
    try:
        found = probe_snapshot_tag(prefix, token)
        if not found:
            raise RuntimeError(f"No tag variants found for snapshot '{prefix}'")
        for arch, (tag, digest) in sorted(found.items()):
            update_arch(entry, arch, digest, known, tag, IMAGE)
            entry["arches"][arch]["tag"] = tag
    except Exception as exc:
        entry["error"] = str(exc)
        print(f":{prefix}: ERROR: {exc}", file=sys.stderr)
    entry.setdefault("firstSeen", today().isoformat())
    entry["lastChecked"] = today().isoformat()
    return entry


def needs_pull(entry: dict, arch: str, digest: str, known: dict) -> bool:
    """Would update_arch() have to pull the image for this arch/digest?"""
    if digest in known:
        return False
    a = entry.get("arches", {}).get(arch, {})
    return digest != a.get("digest") or "correttoVersion" not in a


def process_maven(major: str, entry: dict, known: dict, budget: dict) -> dict:
    """Updates the Maven tag history for a Java major version.

    Tracks the MAVEN_HISTORY_TAGS newest stable tags. Tags whose digests are
    unknown require a pull (java -version); to respect the Docker Hub pull
    rate limit, at most budget["left"] such tags are pulled per run - the
    rest is skipped and resumed by a later run (digests are cached in
    versions.json, so nothing is pulled twice).
    """
    entry.pop("error", None)
    try:
        tags = stable_maven_tags(major)[:MAVEN_HISTORY_TAGS]
        if not tags:
            raise RuntimeError(
                f"No stable {MAVEN_IMAGE}:x.y.z-amazoncorretto-{major} tag found")
        hist = entry.setdefault("tags", {})
        for tag in tags:
            te = hist.get(tag, {})
            te.pop("error", None)
            try:
                digests = hub_digests(tag)
            except Exception as exc:  # keep old data, record the error
                te["error"] = str(exc)
                print(f"{MAVEN_IMAGE}:{tag}: ERROR: {exc}", file=sys.stderr)
                te["lastChecked"] = today().isoformat()
                if te.get("arches") or tag in hist:
                    hist[tag] = te
                continue
            if any(needs_pull(te, a, d, known) for a, d in digests.items()):
                if budget["left"] <= 0:
                    print(f"{MAVEN_IMAGE}:{tag}: new digest, but pull budget "
                          f"reached - skipped until a later run")
                    if tag in hist:
                        hist[tag] = te
                    continue
                budget["left"] -= 1
            for arch, digest in digests.items():
                update_arch(te, arch, digest, known, tag, MAVEN_IMAGE)
            te.setdefault("firstSeen", today().isoformat())
            te["lastChecked"] = today().isoformat()
            hist[tag] = te
    except Exception as exc:
        entry["error"] = str(exc)
        print(f"{MAVEN_IMAGE} (Corretto {major}): ERROR: {exc}", file=sys.stderr)
    entry["lastChecked"] = today().isoformat()
    return entry


def migrate(entry: dict) -> dict:
    """Upgrades the old flat format (amd64Digest etc.) to the arches format."""
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
    window.prompt("Copy manually (Ctrl+C):", ref);
  });
}
function filterTable(divId, major) {
  var div = document.getElementById(divId);
  if (!div) { return; }
  var table = div.nextElementSibling;
  while (table && table.tagName !== "TABLE") { table = table.nextElementSibling; }
  if (!table) { return; }
  var rows = table.getElementsByTagName("tr");
  for (var i = 0; i < rows.length; i++) {
    var cells = rows[i].getElementsByTagName("td");
    if (!cells.length) { continue; }  // header row
    // snapshot rows: ":25.2026..." in cell 0; maven rows: "25" in cell 0
    var m = cells[0].textContent.match(/:(\\d+)/) || cells[0].textContent.match(/^(\\d+)/);
    rows[i].style.display = (!major || (m && m[1] === major)) ? "" : "none";
  }
  var buttons = div.getElementsByTagName("button");
  for (var j = 0; j < buttons.length; j++) {
    buttons[j].style.fontWeight =
      buttons[j].getAttribute("data-major") === major ? "bold" : "normal";
  }
}
</script>"""


def clickable(image: str, tag: str, digest: str, label: str, title_prefix: str) -> str:
    """Clickable <code> element: copies the pinned FROM reference."""
    ref = f"{image}:{tag}@{digest}"
    return (f'<code style="cursor:pointer" onclick="copyFromRef(this, \'{ref}\')" '
            f'title="{title_prefix}{ref}">{label}</code>')


def digest_lines(entry: dict, image: str, default_tag: str, title_prefix: str) -> str:
    """Lines 'x86_64: <digest>' / 'arm64: <digest>' of an entry (click-to-copy)."""
    lines = []
    for arch in ARCHES:
        a = entry.get("arches", {}).get(arch, {})
        if not a.get("digest"):
            continue
        tag = a.get("tag", default_tag)  # snapshots have their own tag per arch
        label = LAMBDA_ARCH_SUFFIX[arch]
        lines.append(f"{label}: {clickable(image, tag, a['digest'], short(a['digest']), title_prefix)}")
    return "<br>".join(lines) if lines else "–"


def version_cells(entry: dict) -> tuple:
    """(OpenJDK, Corretto, Corretto build) of the amd64 side, with ⚠️ on arm64 deviation."""
    a = entry.get("arches", {}).get("amd64", {})
    if "correttoVersion" not in a:  # e.g. arm64-only entries
        a = entry.get("arches", {}).get("arm64", {})
    if "correttoVersion" not in a:
        return ("–", "–", "–")
    corretto = a["correttoVersion"]
    other_arch = "arm64" if a is entry["arches"].get("amd64") else "amd64"
    other = entry.get("arches", {}).get(other_arch, {})
    if other.get("correttoVersion") and other["correttoVersion"] != corretto:
        corretto += f"<br>⚠️ {other_arch}: {other['correttoVersion']}"
    return (a.get("javaVersion", "–"), corretto, a.get("correttoBuild", "–"))


def corretto_of(entry: dict) -> str | None:
    """Corretto version of an entry (amd64, arm64 fallback); None if unknown."""
    a = entry.get("arches", {}).get("amd64", {})
    if "correttoVersion" not in a:
        a = entry.get("arches", {}).get("arm64", {})
    return a.get("correttoVersion")


def maven_version_key(tag: str) -> tuple:
    """Sort key (x, y, z) of a maven:x.y.z-amazoncorretto-<major> tag."""
    m = MVN_VER_RE.match(tag)
    return tuple(int(g) for g in m.groups()) if m else (0, 0, 0)


def matching_maven(maven_entry: dict, corretto: str | None) -> tuple:
    """(tag, tag entry) of the newest tracked Maven tag whose Corretto version
    exactly matches `corretto` - (None, {}) if no tracked tag matches."""
    if not corretto:
        return None, {}
    for tag in sorted(maven_entry.get("tags", {}), key=maven_version_key, reverse=True):
        te = maven_entry["tags"][tag]
        if corretto_of(te) == corretto:
            return tag, te
    return None, {}


def render_row(tag: str, e: dict, maven: dict, s: dict) -> str:
    tp = s["copy_title"]
    digests = digest_lines(e, IMAGE, tag, tp)
    java_version, corretto, build = version_cells(e)

    # Maven counterpart: only a tag whose Corretto version exactly matches
    # this row's image - otherwise show no Maven image at all (the full list
    # of tracked Maven tags is in the Maven section below)
    m_tag, m_entry = matching_maven(maven.get(major_of(e) or "", {}),
                                    corretto.split("<br>")[0])
    if m_tag:
        maven_tag_cell = f"`{MAVEN_IMAGE}:{m_tag}`"
        maven_digest_cell = digest_lines(m_entry, MAVEN_IMAGE, m_tag, tp)
        maven_version_cell = corretto_of(m_entry) + " ✓"
    else:
        maven_tag_cell = maven_digest_cell = maven_version_cell = "–"

    if "error" in e and corretto == "–":
        java_version = f"⚠️ {e['error']}"
    first_seen = e.get("arches", {}).get("amd64", {}).get(
        "firstSeen", e.get("firstSeen", "–"))
    return (f"| [:{tag}]({GALLERY_URL}) | {digests} | {java_version} | {corretto} | {build} "
            f"| {maven_tag_cell} | {maven_digest_cell} | {maven_version_cell} "
            f"| {first_seen} | {e.get('lastChecked', '–')} |")


STRINGS = {
    "title": "JVM versions in the AWS Lambda Java base images",
    "heading": "JVM versions in the AWS Lambda Java base images",
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
    "base_text": ("The tags of `public.ecr.aws/lambda/java` (`:25`, `:21`, …) are mutable pointers: "
                  "each one always refers to the **latest build** of its Java version. When AWS "
                  "publishes a new build, the same tag moves to the new content – the tag stays, "
                  "the digest behind it changes. A tag alone therefore does not identify a specific "
                  "image; only its current digest does (pin it as `<image>:<tag>@sha256:<digest>` "
                  "for a reproducible build). Dated snapshot builds are listed in the table below."),
    "snapshot_section": "New snapshot tags",
    "snapshot_text": ("Dated snapshot tags that appeared in the registry since the last run "
                      f"(discovery window: {SNAPSHOT_TAG_MAX_AGE_DAYS} day(s); shown for "
                      f"{SNAPSHOT_HISTORY_DAYS} days). The tags carry a date only, no time. A snapshot "
                      "is published as arch-specific tags (`-x86_64` / `-arm64`) and/or as a multi-arch "
                      "tag without suffix – the table shows both architectures either way.\n\n"
                      "**Why this table is useful:** the base tags above are mutable and move to newer "
                      "Corretto builds over time. If the latest base image has no matching Maven build "
                      "image yet (no Maven tag in its row above) – breaking setups that need an exact JVM "
                      "build match, such as Project Leyden AOT caches – pin the runtime to a dated "
                      "snapshot whose Corretto build still matches your build image until the Maven image "
                      "catches up. The snapshot tags are immutable; AWS does not document an expiry for "
                      "them, and in practice they remain available for years."),
    "snapshot_empty": ("No new dated snapshot tags within the discovery window "
                       f"({SNAPSHOT_TAG_MAX_AGE_DAYS} day(s); retention: {SNAPSHOT_HISTORY_DAYS} days)."),
    "snapshot_filter_label": "Filter by Java version:",
    "snapshot_filter_all": "All",
    "maven_section": "Maven build image tags",
    "maven_text": (f"The {MAVEN_HISTORY_TAGS} most recent stable `maven:x.y.z-amazoncorretto-<major>` "
                   "tags per Java LTS version on Docker Hub and the Corretto version inside each. "
                   "This is the pool the base/snapshot tables above draw their Maven columns from: "
                   "only tags whose Corretto version **exactly matches** the image of the row are "
                   "shown there. ✓ marks the tag matching the current base image of its Java "
                   "version. (After a change, the history fills up over a few daily runs – pulls "
                   "are rate-limited, see README; until then fewer rows are shown.)"),
    "maven_table_header": ("| Java | Maven image tag | Maven image digests (x86_64 / arm64) | Corretto "
                           "| Corretto build | Matches base image | First seen | Last checked |"),
    "maven_empty": "No Maven tags tracked yet.",
    "table_header": ("| Base image tag | Base image digests (x86_64 / arm64) | OpenJDK | Corretto | Corretto build "
                     "| Maven image tag | Maven image digests (x86_64 / arm64) | Maven Corretto | First seen | Last checked |"),
    "explanation": [
        "**Base image tag**: the multi-arch tag of `public.ecr.aws/lambda/java`, linked to its page in the ECR Public Gallery (snapshot tags are dated: arch-specific `-x86_64`/`-arm64` tags and/or a multi-arch tag).",
        "**Base image digests**: digests of the `x86_64` (amd64) and `arm64` manifests behind the tag (shortened). Tags are mutable – a digest identifies the content uniquely. Click to copy the full pin.",
        "**OpenJDK / Corretto / Corretto build**: output of `java -version` inside the x86_64 image. The arm64 image is verified too; any deviation is flagged (⚠️ arm64: …).",
        "**Maven image tag**: the newest tracked `maven:x.y.z-amazoncorretto-<major>` tag whose Corretto version **exactly matches** the image in this row – i.e. a build image that is safe to combine with this runtime (required e.g. for Project Leyden AOT caches). `–` = none of the tracked Maven tags matches; the full list is in the Maven table below.",
        "**Maven image digests**: digests of that Maven image's `x86_64` (amd64) and `arm64` manifests (shortened). Click to copy the full pin.",
        "**Maven Corretto**: Corretto version of that Maven image (x86_64) – by selection always identical to the image in this row (✓).",
        "**First seen**: the date this digest showed up here first.",
    ],
    "rawdata": "Raw data",
    "legend_heading": "Notes",
    "copy_title": "Click to copy: ",
}


def filter_div(div_id: str, majors: list, s: dict) -> str:
    """Filter buttons ('All' + one per Java major version) for the table
    directly following the returned div (filtered client-side by filterTable())."""
    buttons = " ".join(
        f'<button type="button" data-major="{m}" '
        f'onclick="filterTable(\'{div_id}\', \'{m}\')">{m}</button>' for m in majors)
    return (f'<div id="{div_id}">{s["snapshot_filter_label"]} '
            f'<button type="button" data-major="" style="font-weight:bold" '
            f'onclick="filterTable(\'{div_id}\', \'\')">{s["snapshot_filter_all"]}</button> '
            f'{buttons}</div>')


def render_maven_section(maven: dict, data: dict, s: dict) -> str:
    """Table of the tracked Maven tags per Java LTS version."""
    tp = s["copy_title"]
    # Corretto version of the current base image per major (for the ✓ column)
    base_corretto = {m: corretto_of(e) for e in data.get("tags", {}).values()
                     if (m := major_of(e))}
    rows = []
    for major in sorted(maven, key=int, reverse=True):
        pick, _ = matching_maven(maven[major], base_corretto.get(major))
        for tag in sorted(maven[major].get("tags", {}),
                          key=maven_version_key, reverse=True):
            te = maven[major]["tags"][tag]
            _, corretto, build = version_cells(te)
            if "error" in te and corretto == "–":
                corretto = f"⚠️ {te['error']}"
            match = "✓" if tag == pick else "–"
            rows.append(f"| {major} | `{MAVEN_IMAGE}:{tag}` "
                        f"| {digest_lines(te, MAVEN_IMAGE, tag, tp)} "
                        f"| {corretto} | {build} | {match} "
                        f"| {te.get('firstSeen', '–')} | {te.get('lastChecked', '–')} |")
    if not rows:
        return f"## {s['maven_section']}\n\n{s['maven_empty']}"
    majors = sorted((m for m in maven if maven[m].get("tags")), key=int, reverse=True)
    header = s["maven_table_header"] + "\n" + "|---|---|---|---|---|---|---|---|"
    return (f"## {s['maven_section']}\n\n{s['maven_text']}\n\n"
            f"{filter_div('maven-filter', majors, s)}\n\n{header}\n" + "\n".join(rows))


def render_site(data: dict) -> str:
    s = STRINGS
    maven = data.get("maven", {})
    header = s["table_header"] + "\n" + "|---|---|---|---|---|---|---|---|---|---|"

    # sorted descending by Java major version (25, 21, 17, 11, 8, ...)
    base_rows = "\n".join(
        render_row(t, e, maven, s)
        for t, e in sorted(data["tags"].items(),
                           key=lambda item: int(major_of(item[1]) or 0),
                           reverse=True))

    snapshots = data.get("snapshots", {})
    if snapshots:
        def sort_key(item):
            # same primary order as the base table (Java major version
            # descending), newest snapshot first within one major version
            m = SNAPSHOT_RE.match(item[0] + "-x86_64")
            d = "".join(m.groups()[1:4]) if m else "00000000"
            return (int(major_of(item[1]) or 0), d, item[0])
        snapshot_rows = "\n".join(
            render_row(t, e, maven, s)
            for t, e in sorted(snapshots.items(), key=sort_key, reverse=True))
        # Filter buttons: "All" plus one button per Java major version present
        # in the snapshot table (filtered client-side by filterTable())
        majors = sorted({m for m in (major_of(e) for e in snapshots.values())
                         if m}, key=int, reverse=True)
        snapshot_section = (f"## {s['snapshot_section']}\n\n{s['snapshot_text']}\n\n"
                            f"{filter_div('snapshot-filter', majors, s)}\n\n"
                            f"{header}\n{snapshot_rows}")
    else:
        snapshot_section = f"## {s['snapshot_section']}\n\n{s['snapshot_empty']}"

    maven_section = render_maven_section(maven, data, s)

    explanation = "\n".join(f"- {line}" for line in s["explanation"])

    return f"""---
title: {s['title']}
---

{COPY_JS}

# {s['heading']}

{s['intro']}

> {s['disclaimer']}

*{s['updated']}: {data.get('generatedAt', '–')}*

{s['hint']}

## {s['base_section']}

{s['base_text']}

{header}
{base_rows}

{snapshot_section}

{maven_section}

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
    # drop old flat snapshot keys (with -x86_64 suffix);
    # they are rediscovered in the new grouped format
    data["snapshots"] = {k: v for k, v in data["snapshots"].items()
                         if not k.endswith("-x86_64")}
    # Maven: upgrade the old single-tag format (mavenTag + arches on the top
    # level) to the history format (tags: {<tag>: {...}})
    for e in data["maven"].values():
        if "tags" not in e and "mavenTag" in e:
            te = {k: e.pop(k) for k in ("arches", "firstSeen", "lastChecked") if k in e}
            e["tags"] = {e.pop("mavenTag"): te}

    token = ecr_token()

    # Known digests: same digest = same content, no re-pull needed
    def known():
        result = {}
        for section in ("tags", "snapshots", "maven"):
            for e in data[section].values():
                # Maven entries keep their per-tag data one level deeper
                entries = e.get("tags", {}).values() if section == "maven" else [e]
                for entry in entries:
                    for a in entry.get("arches", {}).values():
                        if a.get("digest") and "correttoVersion" in a:
                            result[a["digest"]] = {k: a[k] for k in
                                                   ("javaVersion", "correttoVersion",
                                                    "correttoBuild", "rawOutput")}
        return result

    # 1. Base image tags
    for tag in TAGS:
        data["tags"][tag] = process_base_tag(
            tag, data["tags"].get(tag, {}), known(), token)

    # 2. New snapshot tags (since the last run)
    print(f"Searching snapshot tags of the last {SNAPSHOT_TAG_MAX_AGE_DAYS} day(s) ...")
    for prefix in recent_snapshot_prefixes(token):
        if prefix not in data["snapshots"]:
            print(f"New snapshot tag discovered: {prefix}")
            data["snapshots"][prefix] = {}

    # 3. Incomplete snapshot entries: probe the tag variants directly and
    #    backfill missing architectures (the tag list is capped and misses
    #    variants; late-published variants are picked up here as well)
    for prefix, entry in list(data["snapshots"].items()):
        arches = entry.get("arches", {})
        complete = all("correttoVersion" in arches.get(a, {}) for a in ARCHES)
        if complete and "error" not in entry:
            entry["lastChecked"] = today().isoformat()
            continue
        data["snapshots"][prefix] = process_snapshot(prefix, entry, known(), token)

    # 4. Clean up old snapshot entries
    cutoff = today().toordinal() - SNAPSHOT_HISTORY_DAYS
    for prefix in list(data["snapshots"]):
        first_seen = data["snapshots"][prefix].get("firstSeen", "")
        if first_seen and date.fromisoformat(first_seen).toordinal() < cutoff:
            print(f"Snapshot removed (older than {SNAPSHOT_HISTORY_DAYS} days): {prefix}")
            del data["snapshots"][prefix]

    # 5. Maven tag history for all occurring Java major versions, newest Java
    #    version first (pull budget shared across majors: Docker Hub
    #    rate-limits pulls; the newest LTS profits first)
    majors = sorted({m for m in (major_of(e) for e in
                                 list(data["tags"].values())
                                 + list(data["snapshots"].values()))
                     if m}, key=int, reverse=True)
    budget = {"left": MAVEN_HISTORY_MAX_PULLS}
    for major in majors:
        data["maven"][major] = process_maven(
            major, data["maven"].get(major, {}), known(), budget)

    data["generatedAt"] = now()
    data["source"] = IMAGE
    DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
    DATA_FILE.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    SITE.parent.mkdir(parents=True, exist_ok=True)
    SITE.write_text(render_site(data), encoding="utf-8")
    print(f"Updated: {DATA_FILE.relative_to(REPO_ROOT)}, "
          f"{SITE.relative_to(REPO_ROOT)}")

    errors = sum(1 for section in ("tags", "snapshots", "maven")
                 for e in data[section].values() if "error" in e)
    errors += sum(1 for e in data["maven"].values()
                  for te in e.get("tags", {}).values() if "error" in te)
    return 1 if errors else 0


if __name__ == "__main__":
    sys.exit(main())
