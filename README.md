# aws-base-lambda-image-corretto-version

**Welche Amazon-Corretto-Version steckt eigentlich in den
[AWS Lambda Java Base Images](https://gallery.ecr.aws/lambda/java) –
und welches `maven:…-amazoncorretto`-Build-Image passt dazu?**

AWS dokumentiert das nicht: Die Tags von `public.ecr.aws/lambda/java` (`:25`, `:21`, …)
sind mutable, und auch die datierten Snapshot-Tags (`25.2026.07.11.03-x86_64`) sind nur
Build-Zeitstempel ohne Versionsbedeutung. Dieses Projekt ermittelt die Antwort daher
automatisiert: Eine GitHub Action prüft **einmal täglich** die Base Images und schaut
per `java -version` hinein – für **x86_64 und arm64** (arm64 ist bei Lambda meist
günstiger).

📊 **Ergebnis:** [Tabelle auf der Projekt-Website](https://hamburml.github.io/aws-base-lambda-image-corretto-version/)
(englisch; deutsche Version über den Sprachschalter oben rechts bzw. `index.de.html` –
Rohdaten in [`data/versions.json`](data/versions.json))

> ⚠️ Inoffizielles Community-Projekt, nicht mit AWS affiliert.
> Alle Angaben ohne Gewähr; für die Richtigkeit der veröffentlichten Daten wird keine
> Haftung übernommen (siehe [LICENSE](LICENSE)).

## Motivation

Für [Project Leyden](https://openjdk.org/projects/leyden/) (AOT-Cache) müssen die JVM,
die den Cache erzeugt, und die JVM, die ihn ausführt, exakt derselbe Build sein.
Wer seinen AOT-Cache z. B. in einer Maven/Corretto-Build-Stage erzeugt und im
Lambda-Java-Base-Image ausführt, muss die JVM-Versionen beider Images kennen und
abgleichen – genau dabei hilft diese Übersicht inklusive der ✓/⚠️-Spalte, die anzeigt,
ob das jeweils neueste Maven-Image denselben Corretto-Build enthält wie das Lambda-Image.

## Wie es funktioniert

1. Der Workflow [`.github/workflows/update-versions.yml`](.github/workflows/update-versions.yml)
   läuft täglich per Cron (und manuell per *Run workflow*).
2. [`scripts/update_versions.py`](scripts/update_versions.py) (nur Stdlib + Docker):
   - holt pro Base-Tag (`8.al2`, `11`, `17`, `21`, `25`) über die Registry-API von
     `public.ecr.aws` die **amd64- und arm64-Manifest-Digests** – ohne Pull;
   - entdeckt zusätzlich **neue datierte Snapshot-Tags** (z. B.
     `25.2026.07.11.03-x86_64`/`-arm64`), die seit dem letzten Lauf erstellt wurden
     (Discovery-Fenster standardmäßig 1 Tag; die Tags enthalten nur ein Datum,
     keine Uhrzeit – daher werden gestern + heute erfasst). Aufbewahrung in der
     Tabelle: 14 Tage. Hinweis: `public.ecr.aws` liefert max. 1000 Tags ohne
     funktionierende Paginierung, die neuesten stehen aber vorn – für das
     Zeitfenster ausreichend;
   - ermittelt das **Maven-Gegenstück**: pro Java-Major-Version das neueste stabile
     `maven:x.y.z-amazoncorretto-<major>`-Tag auf Docker Hub inkl. beider
     Architektur-Digests (Hub-API, ohne Pull);
   - nur wenn ein Digest neu/unbekannt ist: `docker pull` + `java -version` im
     Container der jeweiligen Architektur (arm64 via QEMU, in CI per
     `docker/setup-qemu-action`). Ist ein Digest bereits bekannt
     (gleicher Inhalt hinter mehreren Tags), wird die Version ohne Pull übernommen;
   - schreibt `data/versions.json` und rendert die Website (`docs/index.md`
     englisch, `docs/index.de.md` deutsch).
3. Bei Änderungen committet und pusht der Workflow die Ergebnisse automatisch.
4. **GitHub Pages** liefert `docs/` als Website aus (Sprachschalter oben auf der Seite).

💡 **Click-to-copy:** Auf der Website kopiert ein Klick auf einen Digest den gepinnten
Verweis `<image>:<tag>@sha256:<digest>` in die Zwischenablage – genau so, wie er in
einem Dockerfile hinter `FROM` stehen muss. Das gilt für die Lambda-Images (x86_64 und
arm64) genauso wie für die Maven-Images. (Funktioniert nur auf der Pages-Website, nicht
in der GitHub-Repo-Ansicht – github.com entfernt JavaScript aus gerendertem Markdown.)

## GitHub Pages aktivieren (einmalig)

*Settings → Pages → Build and deployment → Source: „Deploy from a branch" → Branch: `main`, Ordner: `/docs` → Save.*

Danach ist die Seite unter
`https://hamburml.github.io/aws-base-lambda-image-corretto-version/` erreichbar und wird
bei jedem Workflow-Lauf automatisch aktualisiert.

## Lokal ausführen

Voraussetzung: Python 3, Docker und für arm64-Images QEMU/binfmt
(auf Docker-Desktop-Systemen bereits enthalten, sonst z. B. `docker run --privileged --rm tonistiigi/binfmt --install all`).

```bash
# alle Tags prüfen (pullt neue/geänderte Images, je mehrere hundert MB)
python3 scripts/update_versions.py

# nur bestimmte Base-Tags, z. B. für Tests
TAGS="25" python3 scripts/update_versions.py

# Discovery-Fenster für Snapshot-Tags ändern (Default: 1 Tag)
SNAPSHOT_TAG_MAX_AGE_DAYS=3 python3 scripts/update_versions.py

# Aufbewahrung der Snapshot-Tabelle ändern (Default: 14 Tage)
SNAPSHOT_HISTORY_DAYS=30 python3 scripts/update_versions.py

# gepullte Images anschließend wieder löschen (so läuft es in CI)
CLEANUP_IMAGES=1 python3 scripts/update_versions.py
```

## Projektstruktur

```
├── .github/workflows/update-versions.yml  # täglicher GitHub-Actions-Lauf
├── scripts/update_versions.py             # die eigentliche Anwendung
├── data/versions.json                     # Rohdaten (vom Skript gepflegt)
├── docs/                                  # GitHub-Pages-Website (generiert)
│   ├── index.md                           #   englische Version
│   ├── index.de.md                        #   deutsche Version
│   └── _config.yml
├── LICENSE
└── README.md
```

## Lizenz

[MIT](LICENSE) – bewusst gewählt, weil sie einfach, weit verbreitet und mit explizitem
Gewährleistungs- und Haftungsausschluss versehen ist („as is", ohne jede Garantie;
keine Haftung der Autoren). Ergänzend steht auf der Website und hier im README ein
„Alle Angaben ohne Gewähr"-Hinweis für die veröffentlichten Daten.
