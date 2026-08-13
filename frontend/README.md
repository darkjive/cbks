# CBKS Frontend

React-SPA für [CBKS](../README.md) — 3D-Ansicht des Wissensgraphen, Vault-Browser,
Analyse-Panels und Vorlesefunktion.

Stack: React 19 · TypeScript · Vite · [React Three Fiber](https://r3f.docs.pmnd.rs/)
(three.js) · d3-force-3d · oxlint.

## Entwicklung

```bash
npm install
npm run dev      # http://localhost:5173
```

Der Dev-Server proxyt alle API-Pfade auf das Backend unter `127.0.0.1:8000`
(siehe `vite.config.ts`) — **das Backend muss also parallel laufen**, sonst
liefern alle Requests 502.

```bash
npm run build    # tsc -b && vite build  ← zugleich der Typecheck-Gate
npm run lint     # oxlint
npm run preview  # Produktions-Build lokal ansehen
```

Es gibt derzeit **keine Frontend-Testsuite** — `npm run build` (Typecheck) und
`npm run lint` sind die Absicherung. Frontend-Tests stehen auf der
[Roadmap](../README.md#roadmap).

## Struktur

```
src/
  api/          Typed Client gegen die REST-API (client.ts, types.ts)
                ApiKeyContext.tsx hält den optionalen X-API-Key
  components/   GraphCanvas (3D-Szene), Panels, Suche, Upload, Toasts
  graph/        brainHull.ts / brainMeshData.ts — Gehirn-Hüllengeometrie,
                colors.ts — Farbzuordnung nach Knotentyp
  styles/       global.css (keine CSS-Framework-Abhängigkeit)
```

## API-Key

Ist im Backend `CBKS_API_KEY` gesetzt, fragt die App den Key beim ersten Start
ab und legt ihn im `localStorage` unter `cbks-api-key` ab. Ohne gesetzten Key im
Backend entfällt die Abfrage — siehe [SECURITY.md](../SECURITY.md).

## Bekannte Eigenheiten

- Der Produktions-Bundle ist mit ~1,4 MB (418 kB gzip) groß; three.js macht den
  Löwenanteil aus. Code-Splitting steht auf der Roadmap.
- Die 3D-Ansicht kennt drei LOD-Stufen (macro/meso/micro), die je nach
  Kamera-Distanz umschalten.
