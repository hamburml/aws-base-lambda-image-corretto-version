# aws-base-lambda-image-corretto-version

**Which Amazon Corretto version actually ships inside the
[AWS Lambda Java base images](https://gallery.ecr.aws/lambda/java) –
and which `maven:…-amazoncorretto` build image matches it?**

AWS does not document this: the tags of `public.ecr.aws/lambda/java` (`:25`, `:21`, …)
are mutable, and even the dated snapshot tags (`25.2026.07.11.03-x86_64`) are just
build timestamps with no version meaning. This project therefore determines the answer
automatically: a GitHub Action checks the base images **once a day** and looks inside
via `java -version` – for **x86_64 and arm64** (arm64 is usually the cheaper option on
Lambda).

📊 **Result:** [table on the project website](https://hamburml.github.io/aws-base-lambda-image-corretto-version/)
(raw data in [`data/versions.json`](data/versions.json))

> ⚠️ Unofficial community project, not affiliated with AWS.
> All information without warranty; no liability is accepted for the accuracy of the
> published data (see [LICENSE](LICENSE)).

## Motivation

For [Project Leyden](https://openjdk.org/projects/leyden/) (AOT cache), the JVM that
creates the cache and the JVM that runs it must be the exact same build. If you create
your AOT cache e.g. in a Maven/Corretto build stage and run it in the Lambda Java base
image, you need to know and match the JVM versions of both images – that is exactly
what this overview helps with: each row shows the Maven image tag whose Corretto build
**exactly matches** the image of that row (or none if no tracked Maven tag matches),
and a Maven history table lists the Corretto version of the last 30 stable Maven tags
per Java LTS version.

## How it works

1. The workflow [`.github/workflows/update-versions.yml`](.github/workflows/update-versions.yml)
   runs daily via cron (and manually via *Run workflow*).
2. [`scripts/update_versions.py`](scripts/update_versions.py) (stdlib + Docker only):
   - fetches, for each base tag (`8.al2`, `11`, `17`, `21`, `25`), the **amd64 and
     arm64 manifest digests** via the registry API of `public.ecr.aws` – without a
     pull;
   - additionally discovers **new dated snapshot tags** (e.g. `25.2026.07.11.03` –
     published as arch-specific `-x86_64`/`-arm64` tags and/or as a multi-arch tag
     without suffix) created since the last run (discovery window defaults to 1 day;
     the tags contain a date only, no time – so yesterday + today are covered).
     Retention in the table: 90 days. Note: the tag list of `public.ecr.aws` is
     capped at 1000 entries without working pagination and is not reliably ordered –
     so the list only provides candidate prefixes; the tag variants of each tracked
     prefix are then probed directly via the manifests endpoint, which also
     backfills variants the list missed;
    - tracks the **Maven counterparts**: for each Java major version, the 30 newest
      stable `maven:x.y.z-amazoncorretto-<major>` tags on Docker Hub, including both
      architecture digests (Hub API, without a pull; the tag list is paginated).
      Pulls for `java -version` are limited to 10 Maven tags with unknown digests
      per run (`MAVEN_HISTORY_MAX_PULLS`) because Docker Hub rate-limits pulls –
      known digests are cached in `versions.json`, so later runs resume the
      backfill until the history is complete;
   - only if a digest is new/unknown: `docker pull` + `java -version` in the container
     of the respective architecture (arm64 via QEMU, in CI via
     `docker/setup-qemu-action`). If a digest is already known (same content behind
     multiple tags), the version is adopted without a pull;
    - writes `data/versions.json` and renders the website (`docs/index.md`): the
      base/snapshot tables show only Maven tags whose Corretto version **exactly
      matches** the image of the row (otherwise the Maven cells stay empty); the
      Maven history table lists all tracked Maven tags per Java LTS version;
3. On changes, the workflow commits and pushes the results automatically.
4. **GitHub Pages** serves `docs/` as the website.

💡 **Click-to-copy:** On the website, clicking a digest copies the pinned reference
`<image>:<tag>@sha256:<digest>` to the clipboard – exactly as it must appear after
`FROM` in a Dockerfile. This applies to the Lambda images (x86_64 and arm64) as well
as to the Maven images. (Works only on the Pages website, not in the GitHub repo view –
github.com strips JavaScript from rendered Markdown.)

🔎 **Filter:** Above the snapshot table and the Maven table there are buttons to
filter the rows by Java major version (e.g. *25* shows only the Java 25 rows, *All*
shows everything again). (Like click-to-copy, this works only on the Pages website.)

## Enable GitHub Pages (one-time)

*Settings → Pages → Build and deployment → Source: "Deploy from a branch" → Branch: `main`, folder: `/docs` → Save.*

The page is then available at
`https://hamburml.github.io/aws-base-lambda-image-corretto-version/` and is updated
automatically on every workflow run.

## Run locally

Requirements: Python 3, Docker, and for arm64 images QEMU/binfmt
(already included on Docker Desktop systems, otherwise e.g. `docker run --privileged --rm tonistiigi/binfmt --install all`).

```bash
# check all tags (pulls new/changed images, several hundred MB each)
python3 scripts/update_versions.py

# check only specific base tags, e.g. for testing
TAGS="25" python3 scripts/update_versions.py

# change the discovery window for snapshot tags (default: 1 day)
SNAPSHOT_TAG_MAX_AGE_DAYS=3 python3 scripts/update_versions.py

# change the retention of the snapshot table (default: 90 days)
SNAPSHOT_HISTORY_DAYS=30 python3 scripts/update_versions.py

# number of tracked Maven tags per Java LTS version (default: 30)
MAVEN_HISTORY_TAGS=30 python3 scripts/update_versions.py

# limit pulls of Maven tags with unknown digests per run (default: 10; Docker
# Hub rate-limits pulls - the backfill resumes automatically on the next run)
MAVEN_HISTORY_MAX_PULLS=5 python3 scripts/update_versions.py

# delete pulled images again afterwards (this is how it runs in CI)
CLEANUP_IMAGES=1 python3 scripts/update_versions.py
```

## Project structure

```
├── .github/workflows/update-versions.yml  # daily GitHub Actions run
├── scripts/update_versions.py             # the actual application
├── data/versions.json                     # raw data (maintained by the script)
├── docs/                                  # GitHub Pages website (generated)
│   ├── index.md
│   └── _config.yml
├── LICENSE
└── README.md
```

## License

[MIT](LICENSE) – deliberately chosen because it is simple, widely used, and comes with
an explicit disclaimer of warranty and liability ("as is", without any warranty; no
liability of the authors). Additionally, the website and this README carry an "all
information without warranty" notice for the published data.
