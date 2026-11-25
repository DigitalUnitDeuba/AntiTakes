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


def mesh_object(context):
    obj = context.active_object
    if obj and obj.type == "MESH" and obj.is_editable and not obj.override_library:
        return obj
    return None


def selected_variant(obj):
    if 0 <= obj.antitakes_variant_index < len(obj.antitakes_variants):
        return obj.antitakes_variants[obj.antitakes_variant_index]
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


def unique_name(obj, name):
    existing = {variant.name for variant in obj.antitakes_variants}
    if name not in existing:
        return name
    suffix = 2
    while f"{name} {suffix}" in existing:
        suffix += 1
    return f"{name} {suffix}"


class ANTITAKES_OT_save(Operator):
    bl_idname = "antitakes_variants.save"
    bl_label = "Neue Variante speichern"
    bl_description = "Material-Slots und die Zuordnung jeder Fläche als neue Variante sichern"
    bl_options = {"REGISTER", "UNDO"}

    name: StringProperty(name="Variantenname", default="Variante")

    @classmethod
    def poll(cls, context):
        obj = mesh_object(context)
        return obj is not None and obj.mode in {"OBJECT", "EDIT"}

    def invoke(self, context, event):
        self.name = unique_name(context.active_object, "Variante")
        return context.window_manager.invoke_props_dialog(self, width=320)

    def execute(self, context):
        obj = context.active_object
        name = unique_name(obj, self.name.strip() or "Variante")
        if obj.mode == "EDIT":
            obj.update_from_editmode()
        variant = obj.antitakes_variants.add()
        variant.name = name
        capture(obj, variant)
        obj.antitakes_variant_index = len(obj.antitakes_variants) - 1
        self.report({"INFO"}, f"„{name}“ gespeichert")
        return {"FINISHED"}


class ANTITAKES_OT_apply(Operator):
    bl_idname = "antitakes_variants.apply"
    bl_label = "Variante anwenden"
    bl_description = "Gespeicherte Materialien und Flächenzuweisungen am aktiven Objekt wiederherstellen"
    bl_options = {"REGISTER", "UNDO"}

    index: IntProperty(default=-1, options={"SKIP_SAVE", "HIDDEN"})

    @classmethod
    def poll(cls, context):
        obj = mesh_object(context)
        if obj is not None and obj.mode != "OBJECT":
            cls.poll_message_set("Zum Anwenden in den Objektmodus wechseln")
            return False
        return obj is not None and bool(obj.antitakes_variants)

    def execute(self, context):
        obj = context.active_object
        index = obj.antitakes_variant_index if self.index == -1 else self.index
        if not 0 <= index < len(obj.antitakes_variants):
            return {"CANCELLED"}
        variant = obj.antitakes_variants[index]
        mesh = obj.data
        if variant.signature != topology_signature(mesh):
            self.report({"ERROR"}, "Mesh-Topologie geändert. Speichere die Zuordnung für dieses Mesh neu.")
            return {"CANCELLED"}
        if any((slot.had_material and slot.material is None)
               or (slot.had_mesh_material and slot.mesh_material is None) for slot in variant.slots):
            self.report({"ERROR"}, "Ein Material dieser Variante wurde gelöscht.")
            return {"CANCELLED"}
        try:
            assignment = decode_assignment(variant.assignment, len(mesh.polygons))
        except (ValueError, zlib.error):
            self.report({"ERROR"}, "Die gespeicherte Flächenzuweisung ist beschädigt.")
            return {"CANCELLED"}

        # DATA slots and polygon indices are shared by every object using the mesh.
        if not mesh.is_editable or mesh.users > 1 + int(mesh.use_fake_user):
            mesh = mesh.copy()
            obj.data = mesh
        mesh.materials.clear()
        for saved_slot in variant.slots:
            mesh.materials.append(saved_slot.mesh_material)
            slot = obj.material_slots[-1]
            slot.link = "OBJECT" if saved_slot.object_link else "DATA"
            if saved_slot.object_link:
                slot.material = saved_slot.material
        mesh.polygons.foreach_set("material_index", assignment)
        mesh.update()
        obj.active_material_index = min(variant.active_slot, max(0, len(variant.slots) - 1))
        obj.antitakes_variant_index = index
        self.report({"INFO"}, f"„{variant.name}“ angewendet")
        return {"FINISHED"}


class ANTITAKES_OT_update(Operator):
    bl_idname = "antitakes_variants.update"
    bl_label = "Variante überschreiben"
    bl_description = "Die gewählte Variante mit der aktuellen Zuweisung überschreiben"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = mesh_object(context)
        return obj is not None and obj.mode in {"OBJECT", "EDIT"} and selected_variant(obj) is not None

    def invoke(self, context, event):
        variant = selected_variant(context.active_object)
        return context.window_manager.invoke_confirm(
            self, event, message=f"„{variant.name}“ mit dem aktuellen Stand überschreiben?"
        )

    def execute(self, context):
        obj = context.active_object
        variant = selected_variant(obj)
        if obj.mode == "EDIT":
            obj.update_from_editmode()
        capture(obj, variant)
        self.report({"INFO"}, f"„{variant.name}“ aktualisiert")
        return {"FINISHED"}


class ANTITAKES_OT_remove(Operator):
    bl_idname = "antitakes_variants.remove"
    bl_label = "Variante löschen"
    bl_description = "Die gewählte gespeicherte Variante löschen"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        obj = mesh_object(context)
        return obj is not None and selected_variant(obj) is not None

    def invoke(self, context, event):
        variant = selected_variant(context.active_object)
        return context.window_manager.invoke_confirm(
            self, event, message=f"„{variant.name}“ aus der Liste löschen?"
        )

    def execute(self, context):
        obj = context.active_object
        obj.antitakes_variants.remove(obj.antitakes_variant_index)
        obj.antitakes_variant_index = max(0, min(obj.antitakes_variant_index, len(obj.antitakes_variants) - 1))
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
        obj = mesh_object(context)
        if obj is None:
            layout.label(text="Bearbeitbares Mesh auswählen.", icon="INFO")
            return

        layout.label(text=obj.name, icon="OBJECT_DATA")
        if obj.antitakes_variants:
            layout.template_list("ANTITAKES_UL_variants", "", obj, "antitakes_variants",
                                 obj, "antitakes_variant_index", rows=4)
        else:
            layout.label(text="Noch keine Varianten gespeichert.")

        row = layout.row()
        row.scale_y = 1.25
        row.operator("antitakes_variants.save", text="Neue Variante", icon="ADD")

        variant = selected_variant(obj)
        if variant is None:
            return

        box = layout.box()
        box.prop(variant, "name")
        material_count = len({slot.material for slot in variant.slots if slot.material})
        count_label = "Material" if material_count == 1 else "Materialien"
        box.label(text=f"{material_count} {count_label}", icon="MATERIAL")
        if obj.mode != "OBJECT":
            box.label(text="Zum Anwenden: Objektmodus", icon="INFO")

        row = box.row(align=True)
        row.operator("antitakes_variants.apply", text="Anwenden", icon="PLAY").index = obj.antitakes_variant_index
        row.operator("antitakes_variants.update", text="", icon="FILE_REFRESH")
        row.operator("antitakes_variants.remove", text="", icon="TRASH")


CLASSES = (
    ANTITAKES_MaterialSlot,
    ANTITAKES_Variant,
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
    bpy.types.Object.antitakes_variants = CollectionProperty(type=ANTITAKES_Variant)
    bpy.types.Object.antitakes_variant_index = IntProperty(min=0)


def unregister():
    del bpy.types.Object.antitakes_variant_index
    del bpy.types.Object.antitakes_variants
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
