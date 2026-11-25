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


def capture_scene(scene, variant):
    variant.objects.clear()
    for obj in scene_meshes(scene):
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


def assignment_for(obj, saved):
    mesh = obj.data
    if saved.signature != topology_signature(mesh):
        raise ValueError("Mesh-Topologie geändert")
    if any((slot.had_material and slot.material is None)
           or (slot.had_mesh_material and slot.mesh_material is None) for slot in saved.slots):
        raise ValueError("Ein gespeichertes Material fehlt")
    try:
        values = decode_assignment(saved.assignment, len(mesh.polygons))
    except (ValueError, zlib.error) as exc:
        raise ValueError("Gespeicherte Flächenzuweisung ist beschädigt") from exc
    if any(i < 0 or i >= max(1, len(saved.slots)) for i in values):
        raise ValueError("Ungültiger Material-Slot in der Flächenzuweisung")
    return values


def apply_object(obj, saved, assignment):
    mesh = obj.data
    target_materials = [slot.mesh_material for slot in saved.slots]
    current_assignment = integers(len(mesh.polygons))
    mesh.polygons.foreach_get("material_index", current_assignment)
    materials_changed = list(mesh.materials) != target_materials
    assignment_changed = current_assignment != assignment
    # Only detach when a shared/read-only mesh needs changes to its underlying data.
    if (materials_changed or assignment_changed) and (
        not mesh.is_editable or mesh.users > 1 + int(mesh.use_fake_user)
    ):
        mesh = mesh.copy()
        obj.data = mesh
    if materials_changed:
        mesh.materials.clear()
        for material in target_materials:
            mesh.materials.append(material)
    for slot, saved_slot in zip(obj.material_slots, saved.slots):
        link = "OBJECT" if saved_slot.object_link else "DATA"
        if slot.link != link:
            slot.link = link
        if saved_slot.object_link and slot.material != saved_slot.material:
            slot.material = saved_slot.material
    if materials_changed or assignment_changed:
        mesh.polygons.foreach_set("material_index", assignment)
        mesh.update()
    obj.active_material_index = min(saved.active_slot, max(0, len(saved.slots) - 1))


def object_mode(cls, context):
    if context.mode != "OBJECT":
        cls.poll_message_set("In den Objektmodus wechseln")
        return False
    return True


class ANTITAKES_OT_save(Operator):
    bl_idname = "antitakes_variants.save"
    bl_label = "Neue Szenenvariante speichern"
    bl_description = "Material-Slots und Flächenzuweisungen aller bearbeitbaren Mesh-Objekte der Szene sichern"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Variantenname", default="Variante")

    @classmethod
    def poll(cls, context):
        return object_mode(cls, context) and any(editable_mesh(obj) for obj in context.scene.objects)

    def invoke(self, context, event):
        self.name = unique_name(context.scene, "Variante")
        return context.window_manager.invoke_props_dialog(self, width=320)

    def execute(self, context):
        scene = context.scene
        name = unique_name(scene, self.name.strip() or "Variante")
        variant = scene.antitakes_scene_variants.add()
        variant.name = name
        capture_scene(scene, variant)
        scene.antitakes_scene_variant_index = len(scene.antitakes_scene_variants) - 1
        self.report({"INFO"}, f"„{name}“ gespeichert: {len(variant.objects)} Mesh-Objekte")
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
        for state in variant.objects:
            obj = state.object_ref
            if obj is None or obj not in members:
                skipped += 1
                continue
            if not editable_mesh(obj) or obj.mode != "OBJECT":
                self.report({"ERROR"}, f"{obj.name}: Objekt ist nicht im Objektmodus bearbeitbar")
                return {"CANCELLED"}
            try:
                assignment = assignment_for(obj, state)
            except ValueError as exc:
                self.report({"ERROR"}, f"{obj.name}: {exc}. Variante wurde nicht angewendet.")
                return {"CANCELLED"}
            plan.append((obj, state, assignment))
        if not plan:
            self.report({"WARNING"}, "Kein gespeichertes Mesh-Objekt ist noch in der Szene.")
            return {"CANCELLED"}
        for obj, state, assignment in plan:
            apply_object(obj, state, assignment)
        scene.antitakes_scene_variant_index = index
        if skipped:
            self.report({"WARNING"}, f"„{variant.name}“ angewendet; {skipped} fehlende Objekte übersprungen.")
        else:
            self.report({"INFO"}, f"„{variant.name}“ angewendet: {len(plan)} Mesh-Objekte")
        return {"FINISHED"}


class ANTITAKES_OT_update(Operator):
    bl_idname = "antitakes_variants.update"
    bl_label = "Szenenvariante überschreiben"
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
        row.operator("antitakes_variants.update", text="", icon="FILE_REFRESH")
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
