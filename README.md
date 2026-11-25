# AntiTakes

AntiTakes speichert Materialvarianten pro Mesh-Objekt in Blender. Jede Variante enthält die Material-Slots und die Materialzuweisung für jede Fläche. Die Varianten bleiben in der `.blend`-Datei gespeichert.

## Installation

AntiTakes benötigt Blender 4.2 oder neuer. Im Repository-Ordner lässt sich die Installations-ZIP erstellen:

```sh
blender --command extension build
```

In Blender die ZIP über **Bearbeiten → Einstellungen → Erweiterungen → Von Datenträger installieren** auswählen.

## Verwendung

1. Ein Mesh auswählen und die Seitenleiste im 3D-Viewport mit **N** öffnen.
2. Im Tab **AntiTakes** auf **Neue Variante** klicken, um die aktuelle Materialzuweisung zu speichern.
3. Im Objektmodus mit dem Pfeil neben einem Eintrag zur gewünschten Variante wechseln.

Im Detailbereich lassen sich Varianten umbenennen, überschreiben und löschen. Nach einer Änderung der Mesh-Topologie muss die Flächenzuweisung neu gespeichert werden. Verwenden mehrere Varianten dasselbe Blender-Material, wirken sich Änderungen am Shader auf alle diese Varianten aus.
