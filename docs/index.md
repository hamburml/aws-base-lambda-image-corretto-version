---
title: JVM-Versionen in den AWS Lambda Java Base Images
---

# JVM-Versionen in den AWS Lambda Java Base Images

**Welche Amazon-Corretto-Version steckt in welchem AWS Lambda Java Base Image?**
Diese Tabelle wird täglich automatisch per GitHub Action aktualisiert, indem die
Images von `public.ecr.aws/lambda/java` geprüft werden
([so funktioniert es](https://github.com/hamburml/aws-base-lambda-image-corretto-version#readme)).

> ⚠️ Inoffizielles Community-Projekt – AWS dokumentiert diese Zuordnung selbst nicht.
> Alle Angaben ohne Gewähr; für die Richtigkeit der Daten wird keine Haftung übernommen.

*Letzte Aktualisierung: 2026-07-19 15:45 UTC*

| Base-Image-Tag | amd64-Digest | OpenJDK | Corretto | Corretto-Build | Erstmals gesehen | Zuletzt geprüft |
|---|---|---|---|---|---|---|
| `:25` | `eff08920757a` | 25.0.3 | 25.0.3.9.1 | 25.0.3+9-LTS | 2026-07-19 | 2026-07-19 |

## Erläuterung

- **Base-Image-Tag**: Der Multi-Arch-Tag von `public.ecr.aws/lambda/java`.
- **amd64-Digest**: Digest des `linux/amd64`-Manifests hinter dem Tag (gekürzt).
  Tags sind mutable – der Digest identifiziert den Inhalt eindeutig.
- **OpenJDK / Corretto / Corretto-Build**: Ausgabe von `java -version` im Image.
- **Erstmals gesehen**: Datum, an dem dieser Digest hier zuerst auftauchte.

Rohdaten: [`data/versions.json`](https://github.com/hamburml/aws-base-lambda-image-corretto-version/blob/main/data/versions.json)
