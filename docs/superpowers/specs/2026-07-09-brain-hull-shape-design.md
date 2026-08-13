# Anatomisch erkennbare Gehirn-Huelle im 3D-Graph

Datum: 2026-07-09

## Kontext

`frontend/src/graph/brainHull.ts` erzeugt die leuchtende Punktwolken-Huelle,
in die der 3D-Concept-Graph (`GraphCanvas.tsx`) eingepasst wird. Aktuell ist
das eine einzelne Fibonacci-Kugel, zu einem Ellipsoid skaliert, mit Noise-
Falten und einer Mittelfurche. Das Ergebnis ist als "laenglicher Blob"
erkennbar, aber nicht als Gehirn.

Referenzbilder (BioDigital-Anatomiemodell, Front-/Rueck-/Seitenansicht)
zeigen die Zielform: zwei Grosshirn-Hemisphaeren mit seitlich haengenden
Temporallappen, ein deutlich abgesetztes, feiner gefaltetes Kleinhirn
unterhalb/hinten, und ein duenner Hirnstamm darunter.

Ziel ist weiterhin **keine anatomische Praezision**, sondern eine klar als
Gehirn erkennbare, stilisierte Punktwolke.

## Geometrie

`generateBrainHull(count)` erzeugt drei Teil-Punktwolken und fuegt sie zu
einem gemeinsamen Buffer zusammen:

1. **Cerebrum** (~75% der Punkte): wie bisher Ellipsoid-Fibonacci-Kugel mit
   Mehrfach-Sinus-Noise und zentraler Laengsfurche. Neu:
   - **Laterale Fissur**: eine Kerbe in mittlerer Hoehe, die den Radius an
     einer schmalen Hoehen-/Winkelbande nach innen zieht (Falte oberhalb der
     Temporallappen).
   - **Temporallappen-Bulge**: Punkte in einer unteren-seitlichen Zone
     werden nach aussen/unten gezogen, sodass eine haengende Lappen-
     Silhouette entsteht (analog zur bestehenden `topDip`/`groove`-Technik,
     nur unten statt oben).
2. **Cerebellum** (~20% der Punkte): eigene kleinere Ellipsoid-
   Fibonacci-Kugel, unterhalb/hinter dem Cerebrum positioniert (eigener
   Achsen-Satz + Offset). Feineres Noise (kuerzere Wellenlaenge) fuer die
   charakteristische dichte Kleinhirn-Textur, plus flache eigene
   Mittelrille.
3. **Brainstem** (~5% der Punkte): duenne Kapsel-Punktwolke (kleiner,
   konstanter Radius, kein Noise) zwischen Cerebrum-Unterseite und einem
   Punkt unterhalb des Cerebellums.

Der bestehende Farbverlauf (Kobalt unten -> Cyan -> Magenta oben, ueber die
y-Achse, Werte >1 fuer Bloom) bleibt unveraendert und wird ueber die
gesamte zusammengesetzte Form angewendet. Keine separate anatomische
Farbgebung.

`BRAIN_AXES` (Cerebrum-Halbachsen) bleibt als Export bestehen, da es fuer
das bisherige Node-Fitting in `GraphCanvas.tsx` weiter gebraucht wird
(siehe unten). Cerebellum/Brainstem bekommen eigene interne Achsen-
Konstanten, die nicht exportiert werden muessen.

## Node-Verteilung (GraphCanvas.tsx)

Die Kraefte-Simulation (`forceLink`/`forceManyBody`/`forceCenter`/
`areaForce`) bleibt unveraendert. Nur der finale Mapping-Schritt (aktuell:
Normalisierung auf `BRAIN_AXES` + skalarer Fit-Faktor `k`) wird erweitert:

1. Fitting ins Cerebrum-Ellipsoid laeuft wie bisher (unveraendert).
2. Von den so gefitteten Knoten wird das unterste ~20% nach y-Position
   identifiziert und in das Cerebellum-Ellipsoid umgemappt: gleiche
   normalisierte Winkelrichtung (nx, nz), aber skaliert auf die kleineren
   Cerebellum-Achsen plus dessen Positions-Offset.
3. Von dieser untersten Cerebellum-Teilmenge werden die untersten ~3%
   (der urspruenglichen Gesamtmenge) zusaetzlich in die Brainstem-Kapsel
   umgemappt (kleiner Radius um die Stem-Achse, Position entlang der
   Kapsel-Laenge proportional zur urspruenglichen Hoehe).

Damit bleibt die Physik-Simulation unangetastet; nur die Zielposition nach
dem Fitting aendert sich fuer die unterste Schicht der Knotenwolke.

## Umfang, was sich NICHT aendert

- Kamera (`position=[0,1.2,6.5]`, `fov=55`) und `OrbitControls`
  (`maxDistance=14`) bleiben unveraendert.
- `HULL_COLOR`, Punktgroesse/-opacity im `<pointsMaterial>` bleiben
  unveraendert.
- `FloatingParticles` und uebrige Szene bleiben unveraendert.
- Gesamt-Punktezahl bleibt bei 2400 (nur intern auf drei Regionen
  aufgeteilt).

## Testen

Kein automatisierter Test fuer eine stilisierte 3D-Form sinnvoll.
Verifikation erfolgt visuell:

1. `npm run dev` im `frontend/`-Verzeichnis starten.
2. Graph-Ansicht im Browser oeffnen, Modell per Maus aus mehreren Winkeln
   betrachten (Front, Ruecken, Seite) und mit den drei Referenzbildern
   vergleichen.
3. Pruefen: Temporallappen sichtbar als haengende seitliche Woelbung,
   Kleinhirn als eigene, dichter gefaltete Struktur unterhalb/hinten klar
   abgesetzt erkennbar, Hirnstamm als duenne Verbindung sichtbar, ein
   sichtbarer Teil der Graph-Knoten liegt erkennbar im Kleinhirn-Bereich.
