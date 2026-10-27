"""Small shared helpers."""
import bpy
import os
import re
import time
import unicodedata
from mathutils import Vector


def _S(context):
    return context.scene.cartoon_studio


def _project_dir(s):
    return bpy.path.abspath(s.project_dir) if s.project_dir else ""


def _ensure(path):
    os.makedirs(path, exist_ok=True)
    return path


def _safe_name(name):
    """Keep letters/digits of ANY script (Hindi included). Capped at 45 bytes (Blender names max 63)."""
    out = []
    for ch in (name or ""):
        if ch.isalnum() or ch == "_" or unicodedata.category(ch).startswith("M"):
            out.append(ch)
        else:
            out.append("_")
    s = re.sub(r"_+", "_", "".join(out)).strip("_")
    while len(s.encode("utf-8")) > 45:
        s = s[:-1]
    return s.strip("_") or "Item"


def _log(s, msg):
    print("[CartoonStudio]", msg)
    root = _project_dir(s)
    if root and os.path.isdir(root):
        try:
            logdir = _ensure(os.path.join(root, "logs"))
            with open(os.path.join(logdir, "studio.log"), "a", encoding="utf-8") as f:
                f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')}  {msg}\n")
        except OSError:
            pass


def _get_collection(scene, name):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
    if name not in scene.collection.children:
        scene.collection.children.link(coll)
    return coll


def _is_root(o):
    """True for the Studio root empty (black plus sign) that carries a character's position and turning."""
    return o is not None and o.type == "EMPTY" and bool(o.get("cs_root"))


def _char_top(arm):
    """Highest parent of the rig, NOT counting the Studio root empty above it."""
    top, guard = arm, 0
    while top.parent is not None and not _is_root(top.parent) and guard < 32:
        top, guard = top.parent, guard + 1
    return top


def _char_root(arm):
    top = _char_top(arm)
    return top.parent if _is_root(top.parent) else None


def _char_objects(arm):
    """Every object that belongs to the character (root, rig, meshes ...)."""
    top = _char_top(arm)
    objs = [top] + list(top.children_recursive)
    root = _char_root(arm)
    if root is not None and all(ch is top for ch in root.children):
        objs.insert(0, root)
    if arm not in objs:
        objs.append(arm)
    return objs


def _world_bbox(objs):
    mn, mx = [1e18] * 3, [-1e18] * 3
    for o in objs:
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            for i in range(3):
                mn[i] = min(mn[i], w[i])
                mx[i] = max(mx[i], w[i])
    return mn, mx


def _fcurves(act):
    """F-curves of an action on every Blender version (old API and the 4.4+ layered actions)."""
    try:
        return list(act.fcurves)
    except Exception:
        pass
    out = []
    try:
        for layer in act.layers:
            for strip in layer.strips:
                for slot in act.slots:
                    bag = strip.channelbag(slot)
                    if bag is not None:
                        out.extend(bag.fcurves)
    except Exception:
        pass
    return out


def _canon_bone(name):
    """'mixamorig:LeftArm', 'mixamorig1:LeftArm', 'LeftArm', 'Arm.L', 'arm_l' all meet in the middle."""
    n = (name or "").split(":")[-1]
    side = ""
    m = re.search(r"[._\- ]([lr])$", n, re.I)
    if m:
        side = "left" if m.group(1).lower() == "l" else "right"
        n = n[:m.start()]
    n = re.sub(r"[^a-z0-9]", "", n.lower())
    n = re.sub(r"^(mixamorig\d*|bip\d*|jbip[clr]?)", "", n)
    if n.startswith("left") or n.startswith("right"):
        return n
    return side + n


def _action_bones(act):
    names = set()
    for fc in _fcurves(act):
        m = re.match(r'pose\.bones\["(.+?)"\]', fc.data_path)
        if m:
            names.add(m.group(1))
    return names


def _remove_data(d):
    try:
        if isinstance(d, bpy.types.Mesh):
            bpy.data.meshes.remove(d)
        elif isinstance(d, bpy.types.Armature):
            bpy.data.armatures.remove(d)
    except Exception:
        pass


def _delete_objects(objs):
    datas = [o.data for o in objs if o.data is not None]
    for o in objs:
        bpy.data.objects.remove(o, do_unlink=True)
    for d in datas:
        if d.users == 0:
            _remove_data(d)


def _new_objects(before):
    return [o for o in bpy.data.objects if o.name not in before]


def _char_missing(item, scene):
    """True when the character's rig was deleted from the scene (it is still in the panel list)."""
    arm = bpy.data.objects.get(item.armature_name)
    return arm is None or arm.name not in scene.objects
