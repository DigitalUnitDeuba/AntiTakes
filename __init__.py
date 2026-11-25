# SPDX-License-Identifier: GPL-3.0-or-later

from array import array
from base64 import b64decode, b64encode
from hashlib import blake2b
import sys
import zlib

import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, PointerProperty, StringProperty
from bpy.types import Operator, Panel, PropertyGroup, UIList


def integers(size):
    return array("i", [0]) * size


def little_endian(values):
    if sys.byteorder != "little":
        values.byteswap()
    return values


def topology_signature(mesh):
    digest = blake2b(digest_size=16)
    digest.update(f"{len(mesh.vertices)}:{len(mesh.polygons)}:{len(mesh.loops)}:".encode())
    for elements, property_name in (
        (mesh.polygons, "loop_total"),
        (mesh.loops, "vertex_index"),
    ):
        values = integers(len(elements))
        elements.foreach_get(property_name, values)
        digest.update(little_endian(values))
    return digest.hexdigest()


def encode_assignment(mesh):
    values = integers(len(mesh.polygons))
    mesh.polygons.foreach_get("material_index", values)
    return b64encode(zlib.compress(little_endian(values), level=1)).decode("ascii")


def decode_assignment(encoded, face_count):
    expected_size = face_count * array("i").itemsize
    decoder = zlib.decompressobj()
    data = decoder.decompress(b64decode(encoded, validate=True), expected_size + 1)
    if len(data) != expected_size or not decoder.eof or decoder.unused_data:
        raise ValueError("Ungültige Flächenzuweisung")
    return little_endian(array("i", data))


class ANTITAKES_MaterialSlot(PropertyGroup):
    material: PointerProperty(type=bpy.types.Material)
    mesh_material: PointerProperty(type=bpy.types.Material)
    object_link: BoolProperty()
    had_material: BoolProperty()
    had_mesh_material: BoolProperty()


class ANTITAKES_Variant(PropertyGroup):
    name: StringProperty(name="Name", default="Variante")
    slots: CollectionProperty(type=ANTITAKES_MaterialSlot)
    signature: StringProperty()
    assignment: StringProperty()
    active_slot: IntProperty()


class ANTITAKES_ObjectState(PropertyGroup):
    object_ref: PointerProperty(type=bpy.types.Object)
    object_name: StringProperty()
    slots: CollectionProperty(type=ANTITAKES_MaterialSlot)
    signature: StringProperty()
    assignment: StringProperty()
    active_slot: IntProperty()


class ANTITAKES_SceneVariant(PropertyGroup):
    name: StringProperty(name="Name", default="Variante")
    objects: CollectionProperty(type=ANTITAKES_ObjectState)


def editable_mesh(obj):
    return obj.type == "MESH" and obj.is_editable and not obj.override_library


def scene_meshes(scene):
    return sorted((obj for obj in scene.objects if editable_mesh(obj)), key=lambda obj: obj.name)


def selected_variant(scene):
    index = scene.antitakes_scene_variant_index
    if 0 <= index < len(scene.antitakes_scene_variants):
        return scene.antitakes_scene_variants[index]
    return None


def capture(obj, variant):
    mesh = obj.data
    signature = topology_signature(mesh)
    assignment = encode_assignment(mesh)
    materials = [(slot.material, mesh.materials[i], slot.link == "OBJECT")
                 for i, slot in enumerate(obj.material_slots)]
    variant.slots.clear()
    for material, mesh_material, object_link in materials:
        slot = variant.slots.add()
        slot.material = material
        slot.mesh_material = mesh_material
        slot.object_link = object_link
        slot.had_material = material is not None
        slot.had_mesh_material = mesh_material is not None
    variant.signature = signature
    variant.assignment = assignment
    variant.active_slot = obj.active_material_index


def capture_scene(scene, variant, objects=None):
    variant.objects.clear()
    for obj in scene_meshes(scene) if objects is None else objects:
        state = variant.objects.add()
        state.object_ref = obj
        state.object_name = obj.name
        capture(obj, state)


def unique_name(scene, name):
    existing = {variant.name for variant in scene.antitakes_scene_variants}
    suffix = 1
    result = name
    while result in existing:
        suffix += 1
        result = f"{name} {suffix}"
    return result


def current_assignment(mesh):
    values = integers(len(mesh.polygons))
    mesh.polygons.foreach_get("material_index", values)
    return values


def variant_materials(obj, saved):
    mesh = obj.data
    if any((slot.had_material and slot.material is None)
           or (slot.had_mesh_material and slot.mesh_material is None) for slot in saved.slots):
        raise ValueError("Ein gespeichertes Material fehlt")

    data = tuple(slot.mesh_material for slot in saved.slots)
    links = tuple((slot.object_link, slot.material if slot.object_link else None) for slot in saved.slots)
    topology_changed = saved.signature != topology_signature(mesh)
    if topology_changed:
        # Existing faces now belong to a different topology. Keep their slot indices.
        values = current_assignment(mesh)
        count = max(len(mesh.materials), max(values, default=-1) + 1 if any(values) else 0)
        extra = max(0, count - len(data))
        data += (None,) * extra
        links += ((False, None),) * extra
    else:
        try:
            values = decode_assignment(saved.assignment, len(mesh.polygons))
        except (ValueError, zlib.error) as exc:
            raise ValueError("Gespeicherte Flächenzuweisung ist beschädigt") from exc
        if any(i < 0 or i >= max(1, len(data)) for i in values):
            raise ValueError("Ungültiger Material-Slot in der Flächenzuweisung")
    return data, values, links, topology_changed


def blank_materials(obj):
    links = tuple((slot.link == "OBJECT", None) for slot in obj.material_slots)
    return (None,) * len(links), current_assignment(obj.data), links


def current_links(obj):
    return tuple((slot.link == "OBJECT", slot.material if slot.link == "OBJECT" else None)
                 for slot in obj.material_slots)


def apply_material_plan(plan):
    # Preflight: never replace a mesh, break a link or alter an object in another scene.
    groups = {}
    for obj, data, values, links in plan:
        groups.setdefault(obj.data, []).append((obj, data, values, links, obj.active_material_index))
    prepared = []
    for mesh, rows in groups.items():
        obj, data, values, _, _ = rows[0]
        if any(data != row[1] or values != row[2] for row in rows[1:]):
            raise ValueError(f"{obj.name}: Ein geteiltes Mesh hat widersprüchliche Materialzuweisungen")
        previous_data = tuple(mesh.materials)
        previous_values = current_assignment(mesh)
        data_changed = data != previous_data or values != previous_values
        if data_changed and (not mesh.is_editable or mesh.users - int(mesh.use_fake_user) > len(rows)):
            raise ValueError(f"{obj.name}: Das Mesh wird außerhalb der Variante verwendet oder ist schreibgeschützt")
        for member, _, _, links, _ in rows:
            if len(member.users_scene) > 1 and (data_changed or current_links(member) != links):
                raise ValueError(f"{member.name}: Das Objekt ist mit mehreren Szenen verknüpft")
        prepared.append((mesh, rows, data, values, previous_data, previous_values))

    for mesh, rows, data, values, previous_data, previous_values in prepared:
        if len(previous_data) != len(data):
            mesh.materials.clear()
            for material in data:
                mesh.materials.append(material)
        elif previous_data != data:
            for i, material in enumerate(data):
                if previous_data[i] != material:
                    mesh.materials[i] = material
        for obj, _, _, links, active_slot in rows:
            for slot, (object_link, material) in zip(obj.material_slots, links):
                link = "OBJECT" if object_link else "DATA"
                if slot.link != link:
                    slot.link = link
                if object_link and slot.material != material:
                    slot.material = material
            if obj.active_material_index != active_slot:
                obj.active_material_index = min(active_slot, max(0, len(data) - 1))
        if previous_values != values or len(previous_data) != len(data):
            mesh.polygons.foreach_set("material_index", values)
        if previous_data != data or previous_values != values:
            mesh.update()


def object_mode(cls, context):
    if context.mode != "OBJECT":
        cls.poll_message_set("In den Objektmodus wechseln")
        return False
    return True


class ANTITAKES_OT_save(Operator):
    bl_idname = "antitakes_variants.save"
    bl_label = "Neue Szenenvariante speichern"
    bl_description = "Erste Variante übernimmt aktuelle Materialien; weitere Varianten starten ohne Materialien"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Variantenname", default="Variante")

    @classmethod
    def poll(cls, context):
        return object_mode(cls, context) and any(editable_mesh(obj) for obj in context.scene.objects)

    def invoke(self, context, event):
        self.name = unique_name(context.scene, "Variante")
        return context.window_manager.invoke_props_dialog(self, width=320)

    def draw(self, context):
        layout = self.layout
        layout.prop(self, "name")
        if context.scene.antitakes_scene_variants:
            layout.label(text="Die neue Variante startet ohne Materialien.", icon="INFO")
            layout.label(text="Nicht gespeicherte Materialänderungen gehen dabei verloren.")
        else:
            layout.label(text="Die aktuellen Materialien werden übernommen.", icon="INFO")

    def execute(self, context):
        scene = context.scene
        objects = scene_meshes(scene)
        name = unique_name(scene, self.name.strip() or "Variante")
        is_first = not scene.antitakes_scene_variants
        if not is_first:
            try:
                apply_material_plan([(obj, *blank_materials(obj)) for obj in objects])
            except ValueError as exc:
                self.report({"ERROR"}, f"{exc}. Es wurden keine Materialien geändert.")
                return {"CANCELLED"}
        variant = scene.antitakes_scene_variants.add()
        variant.name = name
        capture_scene(scene, variant, objects)
        scene.antitakes_scene_variant_index = len(scene.antitakes_scene_variants) - 1
        mode = "mit aktuellen Materialien" if is_first else "ohne Materialien"
        self.report({"INFO"}, f"„{name}“ {mode} gespeichert: {len(variant.objects)} Mesh-Objekte")
        return {"FINISHED"}


class ANTITAKES_OT_apply(Operator):
    bl_idname = "antitakes_variants.apply"
    bl_label = "Szenenvariante anwenden"
    bl_description = "Gespeicherte Materialien und Flächenzuweisungen der Szene gemeinsam wiederherstellen"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=-1, options={"SKIP_SAVE", "HIDDEN"})

    @classmethod
    def poll(cls, context):
        return object_mode(cls, context) and bool(context.scene.antitakes_scene_variants)

    def execute(self, context):
        scene = context.scene
        index = scene.antitakes_scene_variant_index if self.index == -1 else self.index
        if not 0 <= index < len(scene.antitakes_scene_variants):
            return {"CANCELLED"}
        variant = scene.antitakes_scene_variants[index]
        members = set(scene.objects)
        plan = []
        skipped = 0
        changed_topology = 0
        for state in variant.objects:
            obj = state.object_ref
            if obj is None or obj not in members:
                skipped += 1
                continue
            if not editable_mesh(obj) or obj.mode != "OBJECT":
                self.report({"ERROR"}, f"{obj.name}: Objekt ist nicht im Objektmodus bearbeitbar")
                return {"CANCELLED"}
            try:
                data, assignment, links, topology_changed = variant_materials(obj, state)
            except ValueError as exc:
                self.report({"ERROR"}, f"{obj.name}: {exc}. Variante wurde nicht angewendet.")
                return {"CANCELLED"}
            plan.append((obj, data, assignment, links))
            changed_topology += topology_changed
        if not plan:
            self.report({"WARNING"}, "Kein gespeichertes Mesh-Objekt ist noch in der Szene.")
            return {"CANCELLED"}
        try:
            apply_material_plan(plan)
        except ValueError as exc:
            self.report({"ERROR"}, f"{exc}. Es wurden keine Materialien geändert.")
            return {"CANCELLED"}
        scene.antitakes_scene_variant_index = index
        notices = []
        if changed_topology:
            notices.append(f"{changed_topology} Mesh(es) mit geänderter Topologie: aktuelle Flächenzuordnungen beibehalten")
        if skipped:
            notices.append(f"{skipped} fehlende Objekte übersprungen")
        if notices:
            self.report({"WARNING"}, f"„{variant.name}“ angewendet. " + "; ".join(notices))
        else:
            self.report({"INFO"}, f"„{variant.name}“ angewendet: {len(plan)} Mesh-Objekte")
        return {"FINISHED"}


class ANTITAKES_OT_update(Operator):
    bl_idname = "antitakes_variants.update"
    bl_label = "Aktuelle Zuweisungen speichern"
    bl_description = "Die gewählte Variante mit der aktuellen Szene überschreiben"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return (object_mode(cls, context) and selected_variant(context.scene) is not None
                and any(editable_mesh(obj) for obj in context.scene.objects))

    def invoke(self, context, event):
        variant = selected_variant(context.scene)
        return context.window_manager.invoke_confirm(
            self, event, message=f"„{variant.name}“ mit dem aktuellen Szenenstand überschreiben?"
        )

    def execute(self, context):
        variant = selected_variant(context.scene)
        capture_scene(context.scene, variant)
        self.report({"INFO"}, f"„{variant.name}“ aktualisiert")
        return {"FINISHED"}


class ANTITAKES_OT_remove(Operator):
    bl_idname = "antitakes_variants.remove"
    bl_label = "Szenenvariante löschen"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return selected_variant(context.scene) is not None

    def invoke(self, context, event):
        variant = selected_variant(context.scene)
        return context.window_manager.invoke_confirm(
            self, event, message=f"„{variant.name}“ aus der Liste löschen?"
        )

    def execute(self, context):
        scene = context.scene
        index = scene.antitakes_scene_variant_index
        scene.antitakes_scene_variants.remove(index)
        scene.antitakes_scene_variant_index = max(0, min(index, len(scene.antitakes_scene_variants) - 1))
        return {"FINISHED"}


class ANTITAKES_UL_variants(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index=0):
        row = layout.row(align=True)
        row.label(text=item.name, icon="MATERIAL")
        row.operator("antitakes_variants.apply", text="", icon="PLAY", emboss=False).index = index


class ANTITAKES_PT_variants(Panel):
    bl_label = "AntiTakes"
    bl_idname = "ANTITAKES_PT_variants"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "AntiTakes"

    def draw(self, context):
        layout = self.layout
        scene = context.scene
        layout.label(text=f"Szene: {scene.name}", icon="SCENE_DATA")
        if scene.antitakes_scene_variants:
            layout.template_list("ANTITAKES_UL_variants", "", scene, "antitakes_scene_variants",
                                 scene, "antitakes_scene_variant_index", rows=4)
        else:
            layout.label(text="Noch keine Szenenvarianten gespeichert.")
        row = layout.row()
        row.scale_y = 1.25
        row.operator("antitakes_variants.save", text="Neue Szenenvariante", icon="ADD")
        if context.mode != "OBJECT":
            layout.label(text="Zum Speichern oder Anwenden: Objektmodus", icon="INFO")
        variant = selected_variant(scene)
        if variant is None:
            return
        box = layout.box()
        box.prop(variant, "name")
        box.label(text=f"{len(variant.objects)} gespeicherte Mesh-Objekte", icon="MESH_DATA")
        row = box.row(align=True)
        row.operator("antitakes_variants.apply", text="Anwenden", icon="PLAY")
        row.operator("antitakes_variants.update", text="Zuweisungen speichern", icon="FILE_REFRESH")
        row.operator("antitakes_variants.remove", text="", icon="TRASH")


CLASSES = (
    ANTITAKES_MaterialSlot,
    ANTITAKES_Variant,
    ANTITAKES_ObjectState,
    ANTITAKES_SceneVariant,
    ANTITAKES_OT_save,
    ANTITAKES_OT_apply,
    ANTITAKES_OT_update,
    ANTITAKES_OT_remove,
    ANTITAKES_UL_variants,
    ANTITAKES_PT_variants,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    # Keep the v1.0 schema so old .blend files retain their per-object variants.
    bpy.types.Object.antitakes_variants = CollectionProperty(type=ANTITAKES_Variant)
    bpy.types.Object.antitakes_variant_index = IntProperty(min=0)
    bpy.types.Scene.antitakes_scene_variants = CollectionProperty(type=ANTITAKES_SceneVariant)
    bpy.types.Scene.antitakes_scene_variant_index = IntProperty(min=0)


def unregister():
    del bpy.types.Scene.antitakes_scene_variant_index
    del bpy.types.Scene.antitakes_scene_variants
    del bpy.types.Object.antitakes_variant_index
    del bpy.types.Object.antitakes_variants
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
