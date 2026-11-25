# AntiTakes

AntiTakes speichert globale Materialvarianten pro Blender-Szene. Eine Variante enthält die Material-Slots und die Materialzuweisung pro Fläche aller bearbeitbaren Mesh-Objekte der Szene, auch ohne Auswahl und bei ausgeblendeten Objekten. Die Liste bleibt in der `.blend`-Datei gespeichert.

## Installation

AntiTakes benötigt Blender 4.2 oder neuer. ZIP über **Bearbeiten → Einstellungen → Erweiterungen → Von Datenträger installieren** auswählen. Aus dem Quellordner lässt sich die ZIP mit `blender --command extension build` erstellen.

## Verwendung

1. Im Objektmodus die Seitenleiste im 3D-Viewport mit **N** öffnen.
2. Im Tab **AntiTakes** auf **Neue Szenenvariante** klicken, um die aktuellen Zuweisungen der gesamten Szene zu sichern.
3. Materialien ändern und eine zweite Variante speichern. Mit dem Pfeil neben einer Variante alle gespeicherten Zuweisungen gemeinsam wiederherstellen.

Neue Objekte bleiben beim Anwenden älterer Varianten unverändert, bis diese überschrieben werden. Fehlende Objekte werden mit Hinweis übersprungen. Ist die Topologie eines vorhandenen Objekts verändert, wird der Wechsel vor dem Anwenden abgebrochen. Verknüpfte, nicht bearbeitbare Mesh-Objekte und Library Overrides werden nicht erfasst. Geteilte Mesh-Daten können bei Bedarf für geänderte Zuweisungen getrennt werden.

Varianten speichern Materialreferenzen, keine Kopien der Shader. Änderungen an einem gemeinsam verwendeten Material wirken sich daher auf alle Varianten aus, die darauf verweisen. Verwende getrennte Materialien, wenn sich der Shader selbst je Variante unterscheiden soll.

Objektvarianten aus 1.0 bleiben in der Datei gespeichert, erscheinen aber nicht in der neuen Szenenliste und werden nicht automatisch zusammengeführt.
