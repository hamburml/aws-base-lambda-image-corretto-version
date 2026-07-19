# aws-base-lambda-image-corretto-version

**Welche Amazon-Corretto-Version steckt eigentlich in den
[AWS Lambda Java Base Images](https://gallery.ecr.aws/lambda/java)?**

AWS dokumentiert das nicht: Die Tags von `public.ecr.aws/lambda/java` (`:25`, `:21`, …)
sind mutable, und auch die datierten Snapshot-Tags (`25.2026.07.11.03-x86_64`) sind nur
Build-Zeitstempel ohne Versionsbedeutung. Dieses Projekt ermittelt die Antwort daher
automatisiert: Eine GitHub Action prüft **einmal täglich** die Base Images und schaut
per `java -version` hinein.

📊 **Ergebnis:** [Tabelle auf der Projekt-Website](https://hamburml.github.io/aws-base-lambda-image-corretto-version/)
(bzw. [`docs/index.md`](docs/index.md), Rohdaten in [`data/versions.json`](data/versions.json))

> ⚠️ Inoffizielles Community-Projekt, nicht mit AWS affiliert.
> Alle Angaben ohne Gewähr; für die Richtigkeit der veröffentlichten Daten wird keine
> Haftung übernommen (siehe [LICENSE](LICENSE)).

## Motivation

Für [Project Leyden](https://openjdk.org/projects/leyden/) (AOT-Cache) müssen die JVM,
die den Cache erzeugt, und die JVM, die ihn ausführt, exakt derselbe Build sein.
Wer seinen AOT-Cache z. B. in einer Maven/Corretto-Build-Stage erzeugt und im
Lambda-Java-Base-Image ausführt, muss die JVM-Versionen beider Images kennen und
abgleichen – genau dabei hilft diese Übersicht.

## Wie es funktioniert

1. Der Workflow [`.github/workflows/update-versions.yml`](.github/workflows/update-versions.yml)
   läuft täglich per Cron (und manuell per *Run workflow*).
2. [`scripts/update_versions.py`](scripts/update_versions.py) (nur Stdlib + Docker):
   - holt pro Tag (`8.al2`, `11`, `17`, `21`, `25`) über die Registry-API von
     `public.ecr.aws` den **amd64-Manifest-Digest** – ohne Pull;
   - nur wenn der Digest neu/geändert ist: `docker pull` + `java -version` im Container,
     Parsen der Corretto-Version;
   - schreibt `data/versions.json` und rendert `docs/index.md` neu.
3. Bei Änderungen committet und pusht der Workflow die Ergebnisse automatisch.
4. **GitHub Pages** liefert `docs/index.md` als Website aus.

## GitHub Pages aktivieren (einmalig)

*Settings → Pages → Build and deployment → Source: „Deploy from a branch" → Branch: `main`, Ordner: `/docs` → Save.*

Danach ist die Seite unter
`https://hamburml.github.io/aws-base-lambda-image-corretto-version/` erreichbar und wird
bei jedem Workflow-Lauf automatisch aktualisiert.

## Lokal ausführen

Voraussetzung: Python 3 und Docker.

```bash
# alle Tags prüfen (pullt bis zu 5 Images á mehrere hundert MB)
python3 scripts/update_versions.py

# nur bestimmte Tags, z. B. für Tests
TAGS="25" python3 scripts/update_versions.py

# gepullte Images anschließend wieder löschen (so läuft es in CI)
CLEANUP_IMAGES=1 python3 scripts/update_versions.py
```

## Projektstruktur

```
├── .github/workflows/update-versions.yml  # täglicher GitHub-Actions-Lauf
├── scripts/update_versions.py             # die eigentliche Anwendung
├── data/versions.json                     # Rohdaten (vom Skript gepflegt)
├── docs/                                  # GitHub-Pages-Website (generiert)
│   ├── index.md
│   └── _config.yml
├── LICENSE
└── README.md
```

## Lizenz

[MIT](LICENSE) – bewusst gewählt, weil sie einfach, weit verbreitet und mit explizitem
Gewährleistungs- und Haftungsausschluss versehen ist („as is", ohne jede Garantie;
keine Haftung der Autoren). Ergänzend steht auf der Website und hier im README ein
„Alle Angaben ohne Gewähr"-Hinweis für die veröffentlichten Daten.
