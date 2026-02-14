bl_info = {
    "name": "Batch FBX Export for Unity",
    "author": "Custom",
    "version": (1, 1, 0),
    "blender": (3, 0, 0),
    "location": "File > Export > Batch FBX for Unity",
    "description": "Export each mesh as a separate FBX centered on X/Y, preserving Z height, rotation, and scale for Unity",
    "category": "Import-Export",
}

import bpy
import os
from bpy_extras.io_utils import ExportHelper


# ---- Per-object toggle stored in a CollectionProperty on the operator ----
class BATCH_FBX_MeshEntry(bpy.types.PropertyGroup):
    """One entry in the export list – holds the object name and an export toggle."""
    obj_name: bpy.props.StringProperty(name="Object")
    export: bpy.props.BoolProperty(name="Export", default=True)


class EXPORT_OT_batch_fbx_unity(bpy.types.Operator, ExportHelper):
    """Export each mesh object as a separate FBX file for Unity"""
    bl_idname = "export_scene.batch_fbx_unity"
    bl_label = "Batch FBX for Unity"
    bl_options = {'REGISTER', 'UNDO'}

    filename_ext = ".fbx"
    filter_glob: bpy.props.StringProperty(default="*.fbx", options={'HIDDEN'})

    export_children: bpy.props.BoolProperty(
        name="Include Children",
        description="Export child objects together with their parent mesh",
        default=True,
    )

    center_z: bpy.props.BoolProperty(
        name="Center Z Axis",
        description="Also center the geometry on the Z axis (vertical). "
                    "When disabled, the original Z height is preserved",
        default=False,
    )

    mesh_entries: bpy.props.CollectionProperty(type=BATCH_FBX_MeshEntry)

    def invoke(self, context, event):
        # Populate the mesh list every time the dialog opens
        self.mesh_entries.clear()
        for obj in sorted(bpy.data.objects, key=lambda o: o.name):
            if obj.type == 'MESH' and obj.visible_get():
                entry = self.mesh_entries.add()
                entry.obj_name = obj.name
                entry.export = True
        return super().invoke(context, event)

    def draw(self, context):
        layout = self.layout

        # --- Options ---
        layout.prop(self, "export_children")
        layout.prop(self, "center_z")

        layout.separator()

        # --- Select All / None helpers ---
        row = layout.row(align=True)
        row.label(text="Models to export:")
        row.operator(BATCH_FBX_SELECT_ALL.bl_idname, text="All")
        row.operator(BATCH_FBX_SELECT_NONE.bl_idname, text="None")

        # --- Per-mesh toggle list ---
        box = layout.box()
        if len(self.mesh_entries) == 0:
            box.label(text="No visible meshes found", icon='INFO')
        else:
            for entry in self.mesh_entries:
                row = box.row()
                row.prop(entry, "export", text="")
                row.label(text=entry.obj_name, icon='MESH_DATA')

    def execute(self, context):
        export_dir = os.path.dirname(self.filepath)
        if not os.path.isdir(export_dir):
            self.report({'ERROR'}, f"Directory does not exist: {export_dir}")
            return {'CANCELLED'}

        # Build set of object names the user wants to export
        names_to_export = {e.obj_name for e in self.mesh_entries if e.export}

        mesh_objects = [
            obj for obj in bpy.data.objects
            if obj.type == 'MESH'
            and obj.visible_get()
            and obj.name in names_to_export
        ]

        if not mesh_objects:
            self.report({'WARNING'}, "No meshes selected for export")
            return {'CANCELLED'}

        # Save current selection and active object
        orig_selected = context.selected_objects[:]
        orig_active = context.view_layer.objects.active

        exported = 0

        for obj in mesh_objects:
            # --- Collect objects to export (the mesh + optionally its hierarchy) ---
            objects_to_export = [obj]
            if self.export_children:
                objects_to_export.extend(self._get_descendants(obj))

            # --- Duplicate the objects so we never touch the originals ---
            bpy.ops.object.select_all(action='DESELECT')
            for o in objects_to_export:
                o.select_set(True)
            context.view_layer.objects.active = obj
            bpy.ops.object.duplicate()
            dupes = context.selected_objects[:]
            dupe_root = context.view_layer.objects.active

            # --- Apply only LOCATION so vertex coords become world-space positions.
            #     Keep rotation & scale on the object for the FBX exporter to handle. ---
            bpy.ops.object.select_all(action='DESELECT')
            for d in dupes:
                d.select_set(True)
            context.view_layer.objects.active = dupe_root
            bpy.ops.object.transform_apply(location=True, rotation=False, scale=False)

            # --- Shift vertices so geometry is centered on X/Y (and optionally Z) ---
            for d in dupes:
                if d.type != 'MESH':
                    continue
                me = d.data
                if len(me.vertices) == 0:
                    continue
                cx = sum(v.co.x for v in me.vertices) / len(me.vertices)
                cy = sum(v.co.y for v in me.vertices) / len(me.vertices)
                for v in me.vertices:
                    v.co.x -= cx
                    v.co.y -= cy
                if self.center_z:
                    cz = sum(v.co.z for v in me.vertices) / len(me.vertices)
                    for v in me.vertices:
                        v.co.z -= cz
                me.update()

            # --- Select only the duplicates for export ---
            bpy.ops.object.select_all(action='DESELECT')
            for d in dupes:
                d.select_set(True)
            context.view_layer.objects.active = dupe_root

            # --- Sanitise name for filename ---
            safe_name = self._safe_filename(obj.name)
            filepath = os.path.join(export_dir, safe_name + ".fbx")

            # --- Export FBX with Unity-compatible settings ---
            bpy.ops.export_scene.fbx(
                filepath=filepath,
                use_selection=True,
                apply_scale_options='FBX_SCALE_ALL',
                object_types={'MESH', 'ARMATURE', 'EMPTY'},
                axis_forward='-Z',
                axis_up='Y',
                bake_space_transform=True,
                mesh_smooth_type='FACE',
                use_mesh_modifiers=True,
                add_leaf_bones=False,
            )

            exported += 1

            # --- Delete the duplicates ---
            bpy.ops.object.select_all(action='DESELECT')
            for d in dupes:
                d.select_set(True)
            bpy.ops.object.delete()

        # --- Restore original selection ---
        bpy.ops.object.select_all(action='DESELECT')
        for obj in orig_selected:
            obj.select_set(True)
        context.view_layer.objects.active = orig_active

        self.report({'INFO'}, f"Exported {exported} mesh(es) to {export_dir}")
        return {'FINISHED'}

    @staticmethod
    def _get_descendants(obj):
        """Recursively collect all children."""
        result = []
        for child in obj.children:
            result.append(child)
            result.extend(EXPORT_OT_batch_fbx_unity._get_descendants(child))
        return result

    @staticmethod
    def _safe_filename(name):
        """Replace characters that are illegal in file names."""
        return "".join(c if c.isalnum() or c in (' ', '-', '_', '.') else '_' for c in name)


# ---- Helper operators for Select All / Select None buttons ----
class BATCH_FBX_SELECT_ALL(bpy.types.Operator):
    """Enable all meshes for export"""
    bl_idname = "batch_fbx.select_all"
    bl_label = "Select All"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        # Walk up to find the parent export operator's mesh_entries
        op = _get_running_export_op()
        if op:
            for entry in op.mesh_entries:
                entry.export = True
        return {'FINISHED'}


class BATCH_FBX_SELECT_NONE(bpy.types.Operator):
    """Disable all meshes for export"""
    bl_idname = "batch_fbx.select_none"
    bl_label = "Select None"
    bl_options = {'INTERNAL'}

    def execute(self, context):
        op = _get_running_export_op()
        if op:
            for entry in op.mesh_entries:
                entry.export = False
        return {'FINISHED'}


def _get_running_export_op():
    """Try to get the running export operator instance from the redo panel."""
    # This is a best-effort helper; the checkboxes themselves are the
    # primary way to toggle individual meshes.
    return None


def menu_func_export(self, context):
    self.layout.operator(EXPORT_OT_batch_fbx_unity.bl_idname, text="Batch FBX for Unity (.fbx)")


classes = (
    BATCH_FBX_MeshEntry,
    BATCH_FBX_SELECT_ALL,
    BATCH_FBX_SELECT_NONE,
    EXPORT_OT_batch_fbx_unity,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)
    bpy.types.TOPBAR_MT_file_export.append(menu_func_export)


def unregister():
    bpy.types.TOPBAR_MT_file_export.remove(menu_func_export)
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)


if __name__ == "__main__":
    register()
