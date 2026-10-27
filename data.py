"""Stored data: character library, animation library and each character's animation sequence."""
import bpy
from bpy.props import (
    StringProperty, IntProperty, FloatProperty, BoolProperty,
    FloatVectorProperty, CollectionProperty, PointerProperty,
)
from bpy.types import PropertyGroup


# --- changing any sequence value rebuilds that character's animation right away -------------------
def _clip_update(self, context):
    from . import sequence
    sequence.rebuild_for_clip(context, self)


def _char_update(self, context):
    from . import sequence
    sequence.rebuild_safe(context, self)


def _all_update(self, context):
    from . import sequence
    sequence.rebuild_all(context)


def _speed_update(self, context):
    """Faster / slower play: keep the SAME number of plays, so the clip gets shorter / longer."""
    from . import sequence
    prev = self.prev_speed if self.prev_speed > 0 else 1.0
    new = max(0.05, self.play_speed)
    sequence._BUSY = True
    try:
        self.frames = max(2, int(round(self.frames * prev / new)))
        self.prev_speed = new
    finally:
        sequence._BUSY = False
    sequence.rebuild_for_clip(context, self)


class CS_Anim(PropertyGroup):
    name: StringProperty()
    filepath: StringProperty(subtype="FILE_PATH")
    action_name: StringProperty()
    source: StringProperty(description="Armature in the scene this animation was taken from")


class CS_Clip(PropertyGroup):
    """One animation in a character's sequence. Clips play one after the other."""
    action: StringProperty(
        name="Animation", update=_clip_update,
        description="Animation from the Animation Library")
    frames: IntProperty(
        name="Frames", default=30, min=2, max=100000, update=_clip_update,
        description="How long this animation plays. Longer than the animation itself = it repeats (loops)")
    info: StringProperty(name="Detected")
    src_start: IntProperty(
        name="Copy from frame", default=0, min=0, update=_clip_update,
        description="First frame of the animation to copy. 0 together with 'to frame' 0 = copy the whole animation")
    src_end: IntProperty(
        name="to frame", default=0, min=0, update=_clip_update,
        description="Last frame of the animation to copy. Must be bigger than 'Copy from frame'")
    move: BoolProperty(
        name="Move forward", default=False, update=_clip_update,
        description="For IN-PLACE walk / run animations: the character travels forward while it plays. "
                    "Animations that already move by themselves (Mixamo without 'In Place') always carry "
                    "the character with them. Either way the next animation starts where this one stopped")
    play_speed: FloatProperty(
        name="Animation speed", default=1.0, min=0.05, max=10.0, step=10, precision=2,
        update=_speed_update,
        description="Play this animation faster (above 1) or slower (below 1). 2 = twice as fast, "
                    "0.5 = half speed. The Frames length changes with it, so the animation still "
                    "plays the same number of times")
    prev_speed: FloatProperty(default=1.0, options={"HIDDEN"})
    speed: FloatProperty(
        name="Speed (m/s)", default=0.0, min=0.0, max=30.0, update=_clip_update,
        description="Travel speed for an in-place animation. 0 = automatic (walk or run speed). "
                    "Animations that already move by themselves use their own distance")
    turn: FloatProperty(
        name="Turn (degrees)", default=0.0, min=-360.0, max=360.0, update=_clip_update,
        description="Turn the character before this animation starts. + = left, - = right. "
                    "Use it to change walking direction")
    location_offset: FloatVectorProperty(
        name="Location offset", size=3, subtype="TRANSLATION", unit="LENGTH",
        default=(0.0, 0.0, 0.0), update=_clip_update,
        description="Extra X/Y/Z position for this animation and the clips after it")
    rotation_offset: FloatVectorProperty(
        name="Rotation offset", size=3, subtype="EULER", unit="ROTATION",
        default=(0.0, 0.0, 0.0), update=_clip_update,
        description="Extra X/Y/Z rotation for this animation and the clips after it")


class CS_Character(PropertyGroup):
    name: StringProperty()
    filepath: StringProperty(subtype="FILE_PATH")
    armature_name: StringProperty()
    backup_path: StringProperty(subtype="FILE_PATH")
    clips: CollectionProperty(type=CS_Clip)
    clip_index: IntProperty()
    start_frame: IntProperty(
        name="Start frame", default=1, min=1, update=_char_update,
        description="Frame where the first animation of this character starts")
    end_frame: IntProperty(default=0)


class CS_Settings(PropertyGroup):
    project_dir: StringProperty(
        name="Project Folder", subtype="DIR_PATH",
        description="Optional. Used for character backups and the log file")
    char_height: FloatProperty(
        name="Character height", default=1.75, min=0.0, max=10.0, unit="LENGTH",
        description="Imported characters are scaled to this height in metres (0 = keep the original size)")
    rig_template: StringProperty(
        name="Rig Template", subtype="FILE_PATH",
        description="Any Mixamo T-pose character .fbx. Its skeleton is fitted to meshes that have no rig")
    smart_fit: BoolProperty(
        name="Fit animations to the skeleton (advanced)", default=False, update=_all_update,
        description="OFF (default): animations are copied onto the character exactly as they are. "
                    "ON: bone axes / hips size are re-fitted and built-in forward travel is removed. "
                    "Only try this if a copied animation looks wrong because the skeleton is different")
    drop_armature: PointerProperty(
        name="Armature", type=bpy.types.Object,
        poll=lambda self, obj: obj is not None and obj.type == "ARMATURE",
        description="Drag an armature that has an animation from the Outliner onto this field, then press Add")
    copy_keys: BoolProperty(
        name="Copy keyframes onto the character", default=True, update=_all_update,
        description="ON (default): the keyframes of every animation are copied one after the other into ONE normal "
                    "action on the skeleton - the same as copy / paste by hand - so you can open the Graph Editor "
                    "or Dope Sheet and adjust the curves. OFF: animations are placed as NLA strips instead")
    blend: IntProperty(
        name="Blend frames", default=6, min=0, max=30, update=_all_update,
        description="How many frames one animation takes to melt into the next one (0 = hard cut)")

    characters: CollectionProperty(type=CS_Character)
    char_index: IntProperty()
    animations: CollectionProperty(type=CS_Anim)
    anim_index: IntProperty()


classes = (
    CS_Anim,
    CS_Clip,
    CS_Character,
    CS_Settings,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)
    bpy.types.Scene.cartoon_studio = PointerProperty(type=CS_Settings)


def unregister():
    del bpy.types.Scene.cartoon_studio
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
