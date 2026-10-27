"""Sidebar: Character Library, Animation Library, Animation Sequence."""
import bpy
from bpy.types import Panel, UIList

from .helpers import _S, _char_missing


class CARTOON_UL_chars(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        missing = _char_missing(item, context.scene)
        arm = bpy.data.objects.get(item.armature_name)
        static = arm is not None and arm.type != "ARMATURE"
        layout.label(text=item.name, icon="ERROR" if missing else ("OBJECT_DATA" if static else "ARMATURE_DATA"))
        if missing:
            layout.label(text="deleted from scene")
        elif len(item.clips):
            layout.label(text=f"{len(item.clips)} animation(s)")
        else:
            layout.label(text="no animation")


class CARTOON_UL_anims(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name, icon="ACTION")


class CARTOON_UL_clips(UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        s = _S(context)
        found = s.animations.get(item.action) is not None
        row = layout.row(align=True)
        row.label(text=f"{index + 1}.")
        row.label(text=item.action or "(none)", icon="ACTION" if found else "ERROR")
        row.label(text=f"{item.frames}f")
        if abs(item.play_speed - 1.0) > 0.005:
            row.label(text=f"x{item.play_speed:.2g}")
        if item.move:
            row.label(text="", icon="FORWARD")
        if abs(item.turn) > 0.01:
            row.label(text=f"{item.turn:+.0f}\u00b0")


class CARTOON_PT_main(Panel):
    bl_label = "Cartoon Studio"
    bl_idname = "CARTOON_PT_main"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Cartoon Studio"

    def draw(self, context):
        col = self.layout.column()
        col.label(text="1. Add characters   2. Add animations", icon="INFO")
        col.label(text="3. Pick a character, add animations in order")


class CARTOON_PT_chars(Panel):
    bl_label = "Character Library"
    bl_idname = "CARTOON_PT_chars"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Cartoon Studio"
    bl_parent_id = "CARTOON_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = _S(context)
        lay = self.layout
        row = lay.row()
        row.template_list("CARTOON_UL_chars", "", s, "characters", s, "char_index", rows=3)
        col = row.column(align=True)
        col.operator("cartoon.char_add", icon="ADD", text="")
        col.operator("cartoon.char_remove", icon="REMOVE", text="")
        if any(_char_missing(c, context.scene) for c in s.characters):
            lay.label(text="Some characters are not in the scene", icon="ERROR")
        row = lay.row(align=True)
        row.operator("cartoon.char_restore", icon="FILE_REFRESH")
        row.operator("cartoon.char_restore_all", icon="RECOVER_LAST")
        row.operator("cartoon.char_clear_selection", icon="X", text="Clear")
        lay.operator("cartoon.char_save_blend", icon="FILE_TICK")
        lay.prop(s, "project_dir")
        lay.prop(s, "char_height")
        lay.prop(s, "rig_template")
        lay.operator("cartoon.auto_rig", icon="OUTLINER_OB_ARMATURE")
        box = lay.box()
        box.label(text="Fit a skeleton with markers (like Mixamo)", icon="EMPTY_AXIS")
        box.label(text="Mesh in T-pose, facing the front. Select it, then:")
        box.operator("cartoon.marker_add", icon="EMPTY_AXIS")
        row = box.row(align=True)
        op = row.operator("cartoon.marker_mirror", text="Mirror Left to Right")
        op.source = "L"
        op = row.operator("cartoon.marker_mirror", text="Right to Left")
        op.source = "R"
        box.operator("cartoon.rig_from_markers", icon="OUTLINER_OB_ARMATURE")


class CARTOON_PT_anims(Panel):
    bl_label = "Animation Library"
    bl_idname = "CARTOON_PT_anims"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Cartoon Studio"
    bl_parent_id = "CARTOON_PT_main"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        s = _S(context)
        row = self.layout.row()
        row.template_list("CARTOON_UL_anims", "", s, "animations", s, "anim_index", rows=4)
        col = row.column(align=True)
        col.operator("cartoon.anim_add", icon="ADD", text="")
        col.operator("cartoon.anim_remove", icon="REMOVE", text="")
        row = self.layout.row(align=True)
        row.operator("cartoon.anim_register_existing", icon="ACTION")
        row.operator("cartoon.anim_clear_selection", icon="X", text="Clear")
        box = self.layout.box()
        box.label(text="From an armature in the scene", icon="ARMATURE_DATA")
        box.prop(s, "drop_armature", text="")
        row = box.row(align=True)
        row.operator("cartoon.anim_add_armature", text="Add", icon="ADD").selected = False
        row.operator("cartoon.anim_add_armature", text="Add Selected", icon="RESTRICT_SELECT_OFF").selected = True
        box.label(text="Drag the armature from the Outliner into the field.")
        self.layout.label(text="Mixamo: download walk / run with 'In Place' ticked", icon="INFO")


class CARTOON_PT_sequence(Panel):
    bl_label = "Animation Sequence"
    bl_idname = "CARTOON_PT_sequence"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Cartoon Studio"
    bl_parent_id = "CARTOON_PT_main"

    def draw(self, context):
        s = _S(context)
        lay = self.layout
        if not (0 <= s.char_index < len(s.characters)):
            lay.label(text="Select a character in the Character Library", icon="INFO")
            return
        ch = s.characters[s.char_index]
        if _char_missing(ch, context.scene):
            lay.label(text=f"{ch.name} is not in the scene - press Restore", icon="ERROR")
            return
        lay.label(text=ch.name, icon="ARMATURE_DATA")

        row = lay.row()
        row.template_list("CARTOON_UL_clips", "", ch, "clips", ch, "clip_index", rows=4)
        col = row.column(align=True)
        col.operator("cartoon.clip_add", icon="ADD", text="")
        col.operator("cartoon.clip_remove", icon="REMOVE", text="")
        op = col.operator("cartoon.clip_move", icon="TRIA_UP", text="")
        op.direction = "UP"
        op = col.operator("cartoon.clip_move", icon="TRIA_DOWN", text="")
        op.direction = "DOWN"

        if not len(ch.clips):
            lay.label(text="Select an animation in the Animation Library,")
            lay.label(text="then press + to add it. Add the next one after it.")
        else:
            if ch.end_frame > 0:
                lay.label(text=f"Plays frames {ch.start_frame} - {ch.end_frame}, one after the other", icon="TIME")
            if 0 <= ch.clip_index < len(ch.clips):
                clip = ch.clips[ch.clip_index]
                box = lay.box()
                box.prop_search(clip, "action", s, "animations", text="Animation")
                an = s.animations.get(clip.action)
                src = bpy.data.actions.get(an.action_name) if an else None
                if src is not None:
                    box.label(text="Animation has frames %d - %d" % (src.frame_range[0], src.frame_range[1]),
                              icon="TIME")
                row = box.row(align=True)
                row.prop(clip, "src_start")
                row.prop(clip, "src_end")
                box.prop(clip, "play_speed")
                box.prop(clip, "frames")
                box.prop(clip, "move")
                if clip.move:
                    box.prop(clip, "speed")
                box.prop(clip, "turn")
                box.prop(clip, "location_offset", text="Location offset")
                box.prop(clip, "rotation_offset", text="Rotation offset")
                if clip.info:
                    box.label(text="Detected: " + clip.info, icon="INFO")
            rig = bpy.data.objects.get(ch.armature_name)
            if rig is not None and rig.get("cs_check"):
                lay.label(text="Skeleton: " + str(rig.get("cs_check")), icon="ARMATURE_DATA")
            lay.label(text="Each animation starts where the one before it stopped.")

        lay.prop(ch, "start_frame")
        lay.prop(s, "blend")
        lay.prop(s, "copy_keys")
        lay.prop(s, "smart_fit")
        row = lay.row(align=True)
        row.operator("cartoon.seq_build", text="Update", icon="FILE_REFRESH").all_characters = False
        row.operator("cartoon.seq_build", text="Update All", icon="FILE_REFRESH").all_characters = True
        lay.operator("cartoon.seq_clear", icon="TRASH")
        arm = bpy.data.objects.get(ch.armature_name)
        box = lay.box()
        box.label(text="Pose edit (after animation)", icon="POSE_HLT")
        if arm is not None and arm.get("cs_baked"):
            box.label(text="Edits are kept - Auto-Key saves them", icon="CHECKMARK")
            row = box.row(align=True)
            row.operator("cartoon.bake_edit", text="Edit Pose", icon="POSE_HLT")
            row.operator("cartoon.pose_finish", icon="OBJECT_DATAMODE")
            box.operator("cartoon.unbake", icon="LOOP_BACK")
        else:
            if arm is not None and arm.get("cs_keys"):
                box.label(text="Keyframes are on the character. To adjust them in the", icon="GRAPH")
                box.label(text="Graph Editor, press the button first (keeps your edits).")
            box.operator("cartoon.bake_edit", text="Keep my edits (Graph Editor)" if (arm is not None and arm.get("cs_keys")) else "Edit Pose", icon="POSE_HLT")


classes = (
    CARTOON_UL_chars,
    CARTOON_UL_anims,
    CARTOON_UL_clips,
    CARTOON_PT_main,
    CARTOON_PT_chars,
    CARTOON_PT_anims,
    CARTOON_PT_sequence,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
