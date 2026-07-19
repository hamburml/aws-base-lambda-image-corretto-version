---
title: JVM-Versionen in den AWS Lambda Java Base Images
---

<script>
function copyFromRef(el, ref) {
  navigator.clipboard.writeText(ref).then(function () {
    var old = el.textContent;
    el.textContent = "\u2713 kopiert";
    setTimeout(function () { el.textContent = old; }, 1000);
  }).catch(function () {
    window.prompt("Manuell kopieren (Strg+C):", ref);
  });
}
</script>

# JVM-Versionen in den AWS Lambda Java Base Images

**Welche Amazon-Corretto-Version steckt in welchem AWS Lambda Java Base Image?**
Diese Seite wird täglich automatisch per GitHub Action aktualisiert, indem die
Images von `public.ecr.aws/lambda/java` geprüft werden
([so funktioniert es](https://github.com/hamburml/aws-base-lambda-image-corretto-version#readme)).

> ⚠️ Inoffizielles Community-Projekt – AWS dokumentiert diese Zuordnung selbst nicht.
> Alle Angaben ohne Gewähr; für die Richtigkeit der Daten wird keine Haftung übernommen.

*Letzte Aktualisierung: 2026-07-19 16:16 UTC*

💡 **Klick auf einen Tag oder Digest** kopiert den gepinnten Verweis
(`public.ecr.aws/lambda/java:<tag>@sha256:<amd64-digest>`) – so wie er in einem
Dockerfile hinter `FROM` stehen muss.

## Base-Image-Tags

| Base-Image-Tag | amd64-Digest | OpenJDK | Corretto | Corretto-Build | Erstmals gesehen | Zuletzt geprüft |
|---|---|---|---|---|---|---|
| <code style="cursor:pointer" onclick="copyFromRef(this, 'public.ecr.aws/lambda/java:25@sha256:eff08920757ac9bfcda47deea4b25de46149307ac485556f6f8da5fd0d38eaee')" title="Klicken kopiert: public.ecr.aws/lambda/java:25@sha256:eff08920757ac9bfcda47deea4b25de46149307ac485556f6f8da5fd0d38eaee">:25</code> | <code style="cursor:pointer" onclick="copyFromRef(this, 'public.ecr.aws/lambda/java:25@sha256:eff08920757ac9bfcda47deea4b25de46149307ac485556f6f8da5fd0d38eaee')" title="Klicken kopiert: public.ecr.aws/lambda/java:25@sha256:eff08920757ac9bfcda47deea4b25de46149307ac485556f6f8da5fd0d38eaee">eff08920757a</code> | 25.0.3 | 25.0.3.9.1 | 25.0.3+9-LTS | 2026-07-19 | 2026-07-19 |
| <code style="cursor:pointer" onclick="copyFromRef(this, 'public.ecr.aws/lambda/java:8.al2@sha256:563609a461605647466caae0a1ef963fb03fe716f0475d8668115c2086bd7434')" title="Klicken kopiert: public.ecr.aws/lambda/java:8.al2@sha256:563609a461605647466caae0a1ef963fb03fe716f0475d8668115c2086bd7434">:8.al2</code> | <code style="cursor:pointer" onclick="copyFromRef(this, 'public.ecr.aws/lambda/java:8.al2@sha256:563609a461605647466caae0a1ef963fb03fe716f0475d8668115c2086bd7434')" title="Klicken kopiert: public.ecr.aws/lambda/java:8.al2@sha256:563609a461605647466caae0a1ef963fb03fe716f0475d8668115c2086bd7434">563609a46160</code> | 1.8.0_492 | 8.492.09.2 | 1.8.0_492-b09 | 2026-07-19 | 2026-07-19 |
| <code style="cursor:pointer" onclick="copyFromRef(this, 'public.ecr.aws/lambda/java:11@sha256:1f5df280b5ac687124c67729d575d70134880be5af50c29d552e5f24d595e72b')" title="Klicken kopiert: public.ecr.aws/lambda/java:11@sha256:1f5df280b5ac687124c67729d575d70134880be5af50c29d552e5f24d595e72b">:11</code> | <code style="cursor:pointer" onclick="copyFromRef(this, 'public.ecr.aws/lambda/java:11@sha256:1f5df280b5ac687124c67729d575d70134880be5af50c29d552e5f24d595e72b')" title="Klicken kopiert: public.ecr.aws/lambda/java:11@sha256:1f5df280b5ac687124c67729d575d70134880be5af50c29d552e5f24d595e72b">1f5df280b5ac</code> | 11.0.31 | 11.0.31.11.1 | 11.0.31+11-LTS | 2026-07-19 | 2026-07-19 |
| <code style="cursor:pointer" onclick="copyFromRef(this, 'public.ecr.aws/lambda/java:17@sha256:74c99c983829c6c9e4dd44a9ee870a836f70865229f329fb02acb6e86935822f')" title="Klicken kopiert: public.ecr.aws/lambda/java:17@sha256:74c99c983829c6c9e4dd44a9ee870a836f70865229f329fb02acb6e86935822f">:17</code> | <code style="cursor:pointer" onclick="copyFromRef(this, 'public.ecr.aws/lambda/java:17@sha256:74c99c983829c6c9e4dd44a9ee870a836f70865229f329fb02acb6e86935822f')" title="Klicken kopiert: public.ecr.aws/lambda/java:17@sha256:74c99c983829c6c9e4dd44a9ee870a836f70865229f329fb02acb6e86935822f">74c99c983829</code> | 17.0.19 | 17.0.19.10.1 | 17.0.19+10-LTS | 2026-07-19 | 2026-07-19 |
| <code style="cursor:pointer" onclick="copyFromRef(this, 'public.ecr.aws/lambda/java:21@sha256:1dac9793c19eba748139fa1b9c722b36242425b61936419dea33e3d122e6e336')" title="Klicken kopiert: public.ecr.aws/lambda/java:21@sha256:1dac9793c19eba748139fa1b9c722b36242425b61936419dea33e3d122e6e336">:21</code> | <code style="cursor:pointer" onclick="copyFromRef(this, 'public.ecr.aws/lambda/java:21@sha256:1dac9793c19eba748139fa1b9c722b36242425b61936419dea33e3d122e6e336')" title="Klicken kopiert: public.ecr.aws/lambda/java:21@sha256:1dac9793c19eba748139fa1b9c722b36242425b61936419dea33e3d122e6e336">1dac9793c19e</code> | 21.0.11 | 21.0.11.10.1 | 21.0.11+10-LTS | 2026-07-19 | 2026-07-19 |

## Neue Snapshot-Tags (x86_64)

Datierte Snapshot-Tags, die seit dem letzten Lauf neu im Registry auftauchten
(Discovery-Fenster: 3 Tag(e); Aufbewahrung:
14 Tage). Die Tags enthalten nur ein Datum, keine Uhrzeit.

| Base-Image-Tag | amd64-Digest | OpenJDK | Corretto | Corretto-Build | Erstmals gesehen | Zuletzt geprüft |
|---|---|---|---|---|---|---|
| <code style="cursor:pointer" onclick="copyFromRef(this, 'public.ecr.aws/lambda/java:8.al2.2026.07.17.16-x86_64@sha256:8ecbbe3096ffe4046afdd47bbd9134c2d575d700415118d3ee8c66091259bea6')" title="Klicken kopiert: public.ecr.aws/lambda/java:8.al2.2026.07.17.16-x86_64@sha256:8ecbbe3096ffe4046afdd47bbd9134c2d575d700415118d3ee8c66091259bea6">:8.al2.2026.07.17.16-x86_64</code> | <code style="cursor:pointer" onclick="copyFromRef(this, 'public.ecr.aws/lambda/java:8.al2.2026.07.17.16-x86_64@sha256:8ecbbe3096ffe4046afdd47bbd9134c2d575d700415118d3ee8c66091259bea6')" title="Klicken kopiert: public.ecr.aws/lambda/java:8.al2.2026.07.17.16-x86_64@sha256:8ecbbe3096ffe4046afdd47bbd9134c2d575d700415118d3ee8c66091259bea6">8ecbbe3096ff</code> | 1.8.0_492 | 8.492.09.2 | 1.8.0_492-b09 | 2026-07-19 | 2026-07-19 |
| <code style="cursor:pointer" onclick="copyFromRef(this, 'public.ecr.aws/lambda/java:17.al2023.2026.07.17.17-x86_64@sha256:0fe2a26b0333272372c0f49e5fcd6b3e4d504391c7be4b51f2121c707594014a')" title="Klicken kopiert: public.ecr.aws/lambda/java:17.al2023.2026.07.17.17-x86_64@sha256:0fe2a26b0333272372c0f49e5fcd6b3e4d504391c7be4b51f2121c707594014a">:17.al2023.2026.07.17.17-x86_64</code> | <code style="cursor:pointer" onclick="copyFromRef(this, 'public.ecr.aws/lambda/java:17.al2023.2026.07.17.17-x86_64@sha256:0fe2a26b0333272372c0f49e5fcd6b3e4d504391c7be4b51f2121c707594014a')" title="Klicken kopiert: public.ecr.aws/lambda/java:17.al2023.2026.07.17.17-x86_64@sha256:0fe2a26b0333272372c0f49e5fcd6b3e4d504391c7be4b51f2121c707594014a">0fe2a26b0333</code> | 17.0.18 | 17.0.18.8.1 | 17.0.18+8-LTS | 2026-07-19 | 2026-07-19 |
| <code style="cursor:pointer" onclick="copyFromRef(this, 'public.ecr.aws/lambda/java:11.2026.07.16.13-x86_64@sha256:47238c7d2f9172095bde762e3b904e9fd75a29cf8ad4d794b511557cfc4acc98')" title="Klicken kopiert: public.ecr.aws/lambda/java:11.2026.07.16.13-x86_64@sha256:47238c7d2f9172095bde762e3b904e9fd75a29cf8ad4d794b511557cfc4acc98">:11.2026.07.16.13-x86_64</code> | <code style="cursor:pointer" onclick="copyFromRef(this, 'public.ecr.aws/lambda/java:11.2026.07.16.13-x86_64@sha256:47238c7d2f9172095bde762e3b904e9fd75a29cf8ad4d794b511557cfc4acc98')" title="Klicken kopiert: public.ecr.aws/lambda/java:11.2026.07.16.13-x86_64@sha256:47238c7d2f9172095bde762e3b904e9fd75a29cf8ad4d794b511557cfc4acc98">47238c7d2f91</code> | 11.0.31 | 11.0.31.11.1 | 11.0.31+11-LTS | 2026-07-19 | 2026-07-19 |

## Erläuterung

- **Base-Image-Tag**: Der Multi-Arch-Tag von `public.ecr.aws/lambda/java`.
- **amd64-Digest**: Digest des `linux/amd64`-Manifests hinter dem Tag (gekürzt).
  Tags sind mutable – der Digest identifiziert den Inhalt eindeutig.
- **OpenJDK / Corretto / Corretto-Build**: Ausgabe von `java -version` im Image.
- **Erstmals gesehen**: Datum, an dem dieser Digest hier zuerst auftauchte.

Rohdaten: [`data/versions.json`](https://github.com/hamburml/aws-base-lambda-image-corretto-version/blob/main/data/versions.json)
