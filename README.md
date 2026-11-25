# AntiTakes

AntiTakes speichert globale Materialvarianten pro Blender-Szene. Eine Variante enthält die Material-Slots und die Materialzuweisung pro Fläche aller bearbeitbaren Mesh-Objekte der Szene, auch ohne Auswahl und bei ausgeblendeten Objekten. Die Liste bleibt in der `.blend`-Datei gespeichert.

## Installation

AntiTakes benötigt Blender 4.2 oder neuer. ZIP über **Bearbeiten → Einstellungen → Erweiterungen → Von Datenträger installieren** auswählen. Aus dem Quellordner lässt sich die ZIP mit `blender --command extension build` erstellen.

## Verwendung

1. Im Objektmodus die Seitenleiste im 3D-Viewport mit **N** öffnen.
2. Im Tab **AntiTakes** auf **Neue Szenenvariante** klicken, um die aktuellen Zuweisungen der gesamten Szene als erste Variante zu sichern.
3. Jede weitere **Neue Szenenvariante** leert die Materialien der Szene. Die Slots und ihre Zuordnung zu den Flächen bleiben als Vorlage bestehen. Bereits gespeicherte Varianten werden nicht verändert.
4. Neue Materialien zuweisen und **Zuweisungen speichern** klicken, bevor du wechselst oder eine neue Variante anlegst. Mit dem Pfeil neben einer Variante stellst du ihre gespeicherten Zuweisungen wieder her.

Neue Objekte bleiben beim Anwenden älterer Varianten unverändert, bis diese überschrieben werden. Fehlende Objekte werden mit Hinweis übersprungen. Bei geänderter Topologie (etwa nach Anwenden eines Modifiers) wechselt AntiTakes die Materialien in den Slots, behält aber die aktuellen Slot-Zuordnungen der Flächen bei. Für neue variantenspezifische Flächenzuordnungen die Variante auf dem neuen Mesh erneut speichern. Geometrie, Modifier, Transformationen und Objektverknüpfungen werden nicht wiederhergestellt oder verändert. Verknüpfte, nicht bearbeitbare Mesh-Objekte und Library Overrides werden nicht erfasst. Geteilte Mesh-Daten bleiben geteilt. Bei widersprüchlichen Zuordnungen, nötigen Materialänderungen an einem außerhalb der Variante verwendeten Mesh oder an einem Objekt in mehreren Szenen bricht AntiTakes vor Änderungen ab.

Varianten speichern Materialreferenzen, keine Kopien der Shader. Änderungen an einem gemeinsam verwendeten Material wirken sich daher auf alle Varianten aus, die darauf verweisen. Verwende getrennte Materialien, wenn sich der Shader selbst je Variante unterscheiden soll.

Objektvarianten aus 1.0 bleiben in der Datei gespeichert, erscheinen aber nicht in der neuen Szenenliste und werden nicht automatisch zusammengeführt.
