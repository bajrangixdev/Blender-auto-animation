"""Animation sequence: put animations on a character one after the other.

How it works
  * Every character has a list of clips (run, walk, jump ...). They play back to back.
  * Each clip starts where the previous clip ended: same place, same direction.
  * The limb animation goes on the character's skeleton as NLA strips (one after the other).
  * Position and turning go on the black plus-sign "root" empty as DELTA keys, so you can still move,
    rotate or scale the root (or the character) yourself - that is simply where the sequence starts.
"""
import bpy
import re
import json
import math
import traceback
import numpy as np
from mathutils import Matrix, Quaternion, Vector
from bpy.props import EnumProperty
from bpy.types import Operator

from . import retarget as RT
from . import keycopy as KC
from .helpers import (_S, _safe_name, _log, _is_root, _char_top, _char_objects, _world_bbox,
                      _fcurves, _action_bones, _canon_bone)

WALK_SPEED = 1.35      # metres per second for a 1.75 m character
RUN_SPEED = 3.4
MOVE_RE = re.compile(r"(walk|run|jog|sprint|march|strut|stroll|sneak)", re.I)
RUN_RE = re.compile(r"(run|jog|sprint)", re.I)
ROOT_MOTION_MIN = 0.08      # metres (for a 1.75 m character): more than this = the animation moves by itself

_BUSY = False


# ======================================================================================
#  Pure planning (no Blender needed) - positions, turning and strip extents
# ======================================================================================
def _rot(x, y, a):
    c, s = math.cos(a), math.sin(a)
    return x * c - y * s, x * s + y * c


def plan_motion(clips, start_frame, base_yaw, fps, heightk, blend, face=0.0):
    """clips: [{"d": frames, "move": bool, "turn": degrees, "speed": m/s or 0, "run": bool,
                "vec": (x, y) or None  (distance the animation travels by itself, per full play),
                "loops": how many times the animation plays inside d}]
    A character faces -Y at yaw 0, so 'forward' is (0, -1) turned by the yaw.
    Returns {"loc": [(frame, x, y)], "yaw": [(frame, angle)], "strips": [(start, end)], "end": frame}."""
    n = len(clips)
    loc, rot_keys, spans = {}, {}, []
    t = int(start_frame)
    x = y = z = 0.0
    rx = ry = yaw = 0.0
    h = 0
    if n > 1:
        h = max(0, min(int(blend) // 2, min(c["d"] for c in clips) // 3))
    for i, c in enumerate(clips):
        S, d = t, int(c["d"])
        old_rx, old_ry, old_yaw = rx, ry, yaw
        old_x, old_y, old_z = x, y, z
        rot_offset = c.get("rotation", (0.0, 0.0, 0.0))
        pos_offset = c.get("location", (0.0, 0.0, 0.0))
        rx += float(rot_offset[0])
        ry += float(rot_offset[1])
        yaw += float(rot_offset[2]) + math.radians(c.get("turn", 0.0))
        x += float(pos_offset[0])
        y += float(pos_offset[1])
        z += float(pos_offset[2])
        tf = max(1, min(max(int(blend), 1), d // 2))
        if i == 0:
            loc[S] = (x, y, z)
            rot_keys[S] = (old_rx, old_ry, old_yaw)
            if (rx, ry, yaw) != (old_rx, old_ry, old_yaw):
                rot_keys[S + tf] = (rx, ry, yaw)
        else:
            # Keep the preceding clip's endpoint, then ease this clip's manual
            # transform offset over the blend interval.
            loc.setdefault(S, (old_x, old_y, old_z))
            rot_keys.setdefault(S, (old_rx, old_ry, old_yaw))
            loc[S + tf] = (x, y, z)
            rot_keys[S + tf] = (rx, ry, yaw)
        a = base_yaw + yaw
        if c.get("vec") is not None:
            # the animation travels by itself (its hips were pinned in place, see _prepare_action):
            # the root must carry that distance, otherwise the next animation starts back at the
            # old place. This does NOT depend on the "Move forward" tick.
            vx, vy = c["vec"][0] * c["loops"], c["vec"][1] * c["loops"]
            dx, dy = _rot(vx, vy, a)
            x, y = x + dx, y + dy
        elif c.get("move"):
            speed = c.get("speed") or (RUN_SPEED if c.get("run") else WALK_SPEED) * c.get("pspeed", 1.0)
            dist = speed * heightk * d / float(fps)
            dx, dy = _rot(0.0, -dist, a + face)
            x, y = x + dx, y + dy
        loc[S + d] = (x, y, z)
        rot_keys[S + d] = (rx, ry, yaw)
        spans.append((S - (h if i > 0 else 0), S + d + (h if i < n - 1 else 0)))
        t = S + d
    return {
        "loc": [(f, v[0], v[1], v[2]) for f, v in sorted(loc.items())],
        "rot": [(f, v[0], v[1], v[2]) for f, v in sorted(rot_keys.items())],
        "strips": spans,
        "end": t,
        "h": h,
    }


# ======================================================================================
#  Blender side
# ======================================================================================
def _vec_prop(obj, key, default):
    try:
        return tuple(json.loads(obj[key]))
    except Exception:
        return tuple(default)


def _tag_root(root, cname, origin):
    root["cs_root"] = True
    root["cs_char"] = cname
    root["cs_joined"] = True
    root.empty_display_type = "PLAIN_AXES"
    root.empty_display_size = 0.35
    root.show_in_front = True
    for key, vec in (("cs_anchor_loc", origin), ("cs_base_loc", origin), ("cs_base_rot", (0.0, 0.0, 0.0))):
        root[key] = json.dumps([float(v) for v in vec])


def _ensure_root(context, cname, arm):
    """One empty per character, sitting ON the character (at its origin). Move / rotate / scale it to
    place the character: that is where the animation sequence starts."""
    scene = context.scene
    top = _char_top(arm)
    root = top.parent if _is_root(top.parent) else None
    if root is not None and root.get("cs_char") == cname and all(ch is top for ch in root.children):
        return root
    mw = top.matrix_world.copy()
    if root is not None:                       # shared / foreign root: give this character its own
        top.parent = None
        top.matrix_world = mw
        if not root.children:
            try:
                bpy.data.objects.remove(root, do_unlink=True)
            except Exception:
                pass
    origin = mw.translation.copy()
    root = bpy.data.objects.new(f"CS_{_safe_name(cname)}_Root", None)
    coll = top.users_collection[0] if top.users_collection else scene.collection
    coll.objects.link(root)
    root.rotation_mode = "XYZ"
    root.location = origin
    top.parent = root
    top.matrix_parent_inverse = Matrix.Translation(-origin)
    _tag_root(root, cname, origin)
    return root


def _reset_pose(arm):
    if arm.type != "ARMATURE":
        return
    for pb in arm.pose.bones:
        pb.location = (0, 0, 0)
        pb.rotation_quaternion = (1, 0, 0, 0)
        pb.rotation_euler = (0, 0, 0)
        pb.scale = (1, 1, 1)


def _char_height(arm):
    meshes = [o for o in _char_objects(arm) if o.type == "MESH"]
    if not meshes:
        return 1.75
    mn, mx = _world_bbox(meshes)
    h = max(mx[2] - mn[2], max((float(o.dimensions.z) for o in meshes), default=0.0))
    return h if h > 0.2 else 1.75


def _clear_generated(arm, root):
    """Remove everything an earlier build made for this character."""
    if root.animation_data:
        act = root.animation_data.action
        if act is not None and any(fc.data_path in ("location", "rotation_euler") for fc in _fcurves(act)):
            # animation from an older version of the add-on: put the root back where it was placed
            root.location = _vec_prop(root, "cs_base_loc", root.location)
            root.rotation_euler = _vec_prop(root, "cs_base_rot", root.rotation_euler)
        root.animation_data_clear()
        if act is not None and act.users == 0 and not act.use_fake_user:
            try:
                bpy.data.actions.remove(act)
            except Exception:
                pass
    root.delta_location = (0.0, 0.0, 0.0)
    root.delta_rotation_euler = (0.0, 0.0, 0.0)
    ad = arm.animation_data
    if ad is not None:
        for tr in [t for t in ad.nla_tracks if t.name.startswith("CS_")]:
            ad.nla_tracks.remove(tr)
        ad.action = None
    try:
        made = json.loads(arm.get("cs_made_actions", "[]"))
    except Exception:
        made = []
    for name in made:
        a = bpy.data.actions.get(name)
        if a is not None:
            try:
                bpy.data.actions.remove(a)
            except Exception:
                pass
    arm["cs_made_actions"] = "[]"
    _reset_pose(arm)


def _apply_pose_correction(act, corr):
    """A-pose skeletons rest with the arms angled down, but Mixamo animations assume arms straight out."""
    groups = {}
    for fc in _fcurves(act):
        m = re.match(r'pose\.bones\["(.+?)"\]\.rotation_quaternion$', fc.data_path)
        if m and m.group(1) in corr:
            groups.setdefault(m.group(1), {})[fc.array_index] = fc
    for bone, fcs in groups.items():
        if len(fcs) != 4:
            continue
        n = len(fcs[0].keyframe_points)
        if any(len(fcs[i].keyframe_points) != n for i in range(4)):
            continue
        d = Quaternion(corr[bone])
        prev = None
        for k in range(n):
            q = Quaternion([fcs[i].keyframe_points[k].co[1] for i in range(4)])
            r = d @ q
            if prev is not None and (r.w * prev.w + r.x * prev.x + r.y * prev.y + r.z * prev.z) < 0:
                r = Quaternion((-r.w, -r.x, -r.y, -r.z))
            prev = r
            for i in range(4):
                kp = fcs[i].keyframe_points[k]
                dy = r[i] - kp.co[1]
                kp.co[1] = r[i]
                kp.handle_left[1] += dy
                kp.handle_right[1] += dy
        for fc in fcs.values():
            fc.update()


def _root_motion(act, arm, rot_inv, mapping):
    """Does this animation travel by itself (hips moving forward)? Returns
    {"bone", "axes", "vec", "dist"} or None. vec = net horizontal travel of one full play,
    measured with the character facing its default way (before the root is turned)."""
    if arm.type != "ARMATURE":
        return None
    loc = {}
    for fc in _fcurves(act):
        m = re.match(r'pose\.bones\["(.+?)"\]\.location$', fc.data_path)
        if m:
            loc.setdefault(m.group(1), {})[fc.array_index] = fc
    cands = []
    for n in loc:
        b = arm.data.bones.get(mapping.get(n, n))
        if b is not None:
            cands.append((n, b))
    if not cands:
        return None
    cands.sort(key=lambda nb: (nb[1].parent is not None,
                               not re.search(r"hips|pelvis|root", _canon_bone(nb[0]))))
    name, bone = cands[0]
    mat = rot_inv @ arm.matrix_world.to_3x3() @ bone.matrix_local.to_3x3()
    curves = loc[name]
    f0, f1 = act.frame_range

    def val(f):
        return Vector([curves[i].evaluate(f) if i in curves else 0.0 for i in range(3)])

    w = mat @ (val(f1) - val(f0))
    axes = []
    for i in curves:
        col = mat.col[i]
        if col.length > 1e-9 and math.hypot(col.x, col.y) / col.length > 0.5:
            axes.append(i)
    return {"bone": name, "axes": axes, "vec": (w.x, w.y), "dist": math.hypot(w.x, w.y)}


def _object_motion(act, arm, rot_inv):
    """Some animations (glb, Blender exports) move/turn the whole ARMATURE OBJECT instead of the hips bone.
    Returns (net horizontal travel (x, y) in the unrotated frame or None, [object-level curves])."""
    curves = [fc for fc in _fcurves(act)
              if fc.data_path in ("location", "rotation_euler", "rotation_quaternion", "rotation_axis_angle", "scale")]
    loc = {fc.array_index: fc for fc in curves if fc.data_path == "location"}
    vec = None
    if loc:
        f0, f1 = act.frame_range
        d = Vector([(loc[i].evaluate(f1) - loc[i].evaluate(f0)) if i in loc else 0.0 for i in range(3)])
        parent = arm.parent.matrix_world.to_3x3() if arm.parent is not None else Matrix.Identity(3)
        w = rot_inv @ parent @ arm.matrix_parent_inverse.to_3x3() @ d
        vec = (w.x, w.y)
    return vec, curves


def _np3(m):
    return np.array([[float(m[i][j]) for j in range(3)] for i in range(3)])


def _first(canon_names, table):
    for n in canon_names:
        if n in table:
            return table[n]
    return None


_LEFT = ("leftupleg", "leftupperleg", "leftthigh", "leftshoulder", "leftarm")
_RIGHT = ("rightupleg", "rightupperleg", "rightthigh", "rightshoulder", "rightarm")


def _retarget_setup(arm, rot_inv):
    """Compare this skeleton with the skeleton the animations were made on (remembered when the first
    animation was imported). Returns what is needed to fit animations, or None if that is not known."""
    if arm.type != "ARMATURE":
        return None
    txt = bpy.data.texts.get("CS_RigTemplate")
    if txt is None:
        return None
    try:
        tpl = json.loads(txt.as_string())
        sb = {b["n"]: b for b in tpl["bones"]}
    except Exception:
        return None
    tgt = {}
    for b in arm.data.bones:
        tgt.setdefault(_canon_bone(b.name), b.name)
    order, seen = [], set()

    def add(n):
        if n in seen:
            return
        seen.add(n)
        p = sb[n].get("p")
        if p and p in sb:
            add(p)
        order.append(n)

    for n in sb:
        add(n)
    common = [n for n in order if _canon_bone(n) in tgt]
    if len(common) < 8:
        return None
    idx = {n: i for i, n in enumerate(common)}
    parent = []
    for n in common:
        p = sb[n].get("p")
        while p and p not in idx:
            p = sb[p].get("p") if p in sb else ""
        parent.append(idx[p] if p else -1)
    mw = arm.matrix_world
    Rs, Rt, tnames = [], [], []
    src_head, tgt_head = {}, {}
    for n in common:
        c = _canon_bone(n)
        tb = arm.data.bones[tgt[c]]
        tnames.append(tb.name)
        Rs.append(np.array(sb[n]["r"], dtype=float).reshape(3, 3))
        Rt.append(_np3(rot_inv @ (mw @ tb.matrix_local).to_3x3().normalized()))
        src_head[c] = sb[n]["h"]
        wh = rot_inv @ (mw @ tb.head_local)
        tgt_head[c] = (wh.x, wh.y, wh.z)
    # which way is the character turned (about the vertical) compared with the animation's skeleton?
    lc = next((c for c in _LEFT if c in src_head), None)
    rc = next((c for c in _RIGHT if c in src_head), None)
    G, face, turn_deg = np.eye(3), 0.0, 0.0
    if lc and rc and lc.replace("left", "") == rc.replace("right", ""):
        ls, rs_ = src_head[lc], src_head[rc]
        lt, rt = tgt_head[lc], tgt_head[rc]
        th_s = math.atan2(rs_[1] - ls[1], rs_[0] - ls[0])
        th_t = math.atan2(rt[1] - lt[1], rt[0] - lt[0])
        G = RT.yaw_matrix(th_t - th_s)
        turn_deg = math.degrees(math.atan2(math.sin(th_t - th_s), math.cos(th_t - th_s)))
        face = math.atan2(math.sin(th_t - math.pi), math.cos(th_t - math.pi))   # 0 = faces -Y
    angles = RT.mismatch(Rs, Rt, G)
    need = RT.needs_retarget(angles)
    # size: animation hips offsets are in the source's bone units -> scale to this skeleton's units
    zs = []
    for b in arm.data.bones:
        for v in (b.head_local, b.tail_local):
            zs.append((rot_inv @ (mw @ v)).z)
    h_t = max(zs) - min(zs) if zs else 0.0
    s_t = sum(mw.to_scale()) / 3.0
    k = 1.0
    try:
        s_s, h_s = float(tpl.get("s", 0.0)), float(tpl.get("H", 0.0))
        if s_s > 0 and h_s > 0 and h_t > 1e-6 and s_t > 1e-9:
            k = (s_s * h_t / h_s) / s_t
    except Exception:
        k = 1.0
    return {"parent": parent, "Rs": Rs, "Rt": Rt, "G": G, "tgt": tnames, "face": face,
            "need": need, "k": k, "max_angle": max(angles) if angles else 0.0,
            "turn_deg": turn_deg, "bad": sum(1 for a in angles if a > 8.0), "count": len(angles)}


def _retarget_action(act, rs, rot_needed, k):
    """Rewrite the bone rotations (and hips translation) of an action copy so they fit this skeleton."""
    quats, locs = {}, {}
    for fc in _fcurves(act):
        m = re.match(r'pose\.bones\["(.+?)"\]\.(rotation_quaternion|location)$', fc.data_path)
        if m:
            (quats if m.group(2) == "rotation_quaternion" else locs).setdefault(m.group(1), {})[fc.array_index] = fc
    where = {t: i for i, t in enumerate(rs["tgt"])}
    done = 0

    def put(fc, kidx, val):
        kp = fc.keyframe_points[kidx]
        dy = val - kp.co[1]
        kp.co[1] = val
        kp.handle_left[1] += dy
        kp.handle_right[1] += dy

    if rot_needed:
        frames = set()
        for b, fcs in quats.items():
            if b in where and len(fcs) == 4:
                for kp in fcs[0].keyframe_points:
                    frames.add(round(kp.co[0], 4))
        table = {}
        nb = len(rs["tgt"])
        for f in sorted(frames):
            qs = [(1.0, 0.0, 0.0, 0.0)] * nb
            for b, fcs in quats.items():
                i = where.get(b)
                if i is not None and len(fcs) == 4:
                    qs[i] = tuple(fcs[j].evaluate(f) for j in range(4))
            table[f] = RT.retarget_frame(rs["parent"], rs["Rs"], rs["Rt"], rs["G"], qs)
        for b, fcs in quats.items():
            i = where.get(b)
            if i is None or len(fcs) != 4:
                continue
            n = len(fcs[0].keyframe_points)
            if any(len(fcs[j].keyframe_points) != n for j in range(4)):
                continue
            prev = None
            for kidx in range(n):
                q = table.get(round(fcs[0].keyframe_points[kidx].co[0], 4))
                if q is None:
                    continue
                r = q[i]
                if prev is not None and sum(a * c for a, c in zip(r, prev)) < 0:
                    r = tuple(-a for a in r)
                prev = r
                for j in range(4):
                    put(fcs[j], kidx, r[j])
            for fc in fcs.values():
                fc.update()
            done += 1
    if rot_needed or abs(k - 1.0) > 0.15:
        for b, fcs in locs.items():
            i = where.get(b)
            if i is None or len(fcs) != 3:
                continue
            n = len(fcs[0].keyframe_points)
            if any(len(fcs[j].keyframe_points) != n for j in range(3)):
                continue
            for kidx in range(n):
                v = [fcs[j].keyframe_points[kidx].co[1] for j in range(3)]
                if rot_needed:
                    vt = RT.retarget_vector(v, rs["Rs"][i], rs["Rt"][i], rs["G"], k)
                else:
                    vt = [k * x for x in v]
                for j in range(3):
                    put(fcs[j], kidx, float(vt[j]))
            for fc in fcs.values():
                fc.update()
            done += 1
    return done


def _prepare_action(act, arm, tag, rot_inv, heightk, rs=None, smart=False):
    """Make an animation fit this rig. Returns (action to play, fit 0..1, travel vec or None, note).
    Works on a COPY (your library animation is never changed):
      * bone names are matched (mixamorig: / Left / .L ...)
      * if the skeleton's bone axes, pose or turning differ from the animation's skeleton, the rotations
        are rewritten so the body moves the same way in the world (otherwise the character tips over)
      * hips translation is scaled to this skeleton's size
      * motion of the whole armature object (it would drag the character back to the world origin) is muted
      * forward travel built into the hips is taken out of the pose and given to the root instead, so the
        next animation starts from where this one ended."""
    if arm.type != "ARMATURE":
        return act, 1.0, None, "no skeleton"
    rig_bones = {b.name for b in arm.pose.bones}
    used = _action_bones(act)
    canon = {}
    for b in rig_bones:
        canon.setdefault(_canon_bone(b), b)
    mapping = {u: canon[_canon_bone(u)] for u in used if u not in rig_bones and _canon_bone(u) in canon}
    fit = ((len(used & rig_bones) + len(mapping)) / float(len(used))) if used else 1.0
    # Always inspect a private copy for root travel. In plain-copy mode we still
    # preserve the animation's pose, but movement embedded in the hips/object
    # must be transferred to the sequence root or the next clip can jump back
    # to its own source origin.
    corr = {}
    raw = arm.get("cs_apose") if smart else None
    if raw:
        try:
            corr = json.loads(raw)
        except Exception:
            corr = {}
    new = act.copy()
    new.name = tag
    changed, notes = False, []
    if mapping:
        changed = True
        for fc in _fcurves(new):
            m = re.match(r'pose\.bones\["(.+?)"\](.*)', fc.data_path)
            if m and m.group(1) in mapping:
                fc.data_path = f'pose.bones["{mapping[m.group(1)]}"]{m.group(2)}'
    if smart:
        if corr:
            _apply_pose_correction(new, corr)
            changed = True
        elif rs is not None and used and (rs["need"] or abs(rs["k"] - 1.0) > 0.15):
            try:
                n = _retarget_action(new, rs, rs["need"], rs["k"])
            except Exception:
                n = 0
                _log(bpy.context.scene.cartoon_studio, traceback.format_exc())
            if n:
                changed = True
                if rs["need"]:
                    notes.append("bone axes differ from the animation skeleton (%d of %d bones, up to %d deg) - "
                                 "rotations re-fitted" % (rs["bad"], rs["count"], rs["max_angle"]))
                if abs(rs["k"] - 1.0) > 0.15:
                    notes.append("hips motion scaled x%.3g" % rs["k"])
    else:
        notes.append("plain copy")
    obj_vec, obj_curves = _object_motion(new, arm, rot_inv)
    obj_moves = obj_vec is not None and math.hypot(*obj_vec) > ROOT_MOTION_MIN * heightk
    if obj_curves:
        changed = True
        for fc in obj_curves:
            fc.mute = True
        notes.append("whole-object animation switched off (it would pull the character to the origin)")
    rm = _root_motion(new, arm, rot_inv, {}) if used else None
    moves = rm is not None and bool(rm["axes"]) and rm["dist"] > ROOT_MOTION_MIN * heightk
    vec = None
    if rm is not None and rm["axes"]:
        path = f'pose.bones["{rm["bone"]}"].location'
        source_start = float(new.frame_range[0])
        normalized = False
        for fc in _fcurves(new):
            if fc.data_path == path and fc.array_index in rm["axes"] and len(fc.keyframe_points):
                # Clips can have different absolute hips offsets at their first
                # frame. Those offsets are source-scene placement, not part of
                # the pose; keeping them makes the character jump at clip joins.
                # For travelling clips, root motion carries the displacement, so
                # the in-place hips channel is held at zero. Otherwise preserve
                # small in-place motion relative to the clip's own starting value.
                start_value = float(fc.evaluate(source_start))
                for kp in fc.keyframe_points:
                    if moves:
                        kp.co[1] = 0.0
                        kp.handle_left[1] = 0.0
                        kp.handle_right[1] = 0.0
                    else:
                        kp.co[1] -= start_value
                        kp.handle_left[1] -= start_value
                        kp.handle_right[1] -= start_value
                fc.update()
                normalized = normalized or abs(start_value) > 1e-6 or moves
        if normalized:
            changed = True
    if moves:
        vec = rm["vec"]
        changed = True
        notes.append("moves by itself %.2f m (carried to the next animation)" % rm["dist"])
    elif obj_moves:
        vec = obj_vec
        notes.append("moves by itself %.2f m (carried to the next animation)" % math.hypot(*obj_vec))
    if not changed:
        try:
            bpy.data.actions.remove(new)
        except Exception:
            pass
        if not notes:
            notes.append("in place")
        return act, fit, None, "; ".join(notes)
    if not notes:
        notes.append("fitted to this skeleton")
    return new, fit, vec, "; ".join(notes)


# ======================================================================================
#  Copy the keyframes onto the character (what you do by hand: copy keys -> paste -> graph editor)
# ======================================================================================
class _KeyAction:
    """A new action whose F-curves can be created on every Blender version (4.2 and the 4.4+ layered actions)."""

    def __init__(self, name, owner):
        self.act = bpy.data.actions.new(name)
        self.owner = owner
        self.slot = None
        self.bag = None
        if hasattr(self.act, "slots") and hasattr(self.act, "layers"):
            try:
                self.slot = self.act.slots.new(id_type="OBJECT", name=owner.name)
                layer = self.act.layers.new("Layer")
                strip = layer.strips.new(type="KEYFRAME")
                self.bag = strip.channelbag(self.slot, ensure=True)
            except Exception:
                self.slot, self.bag = None, None

    def new_curve(self, path, index, group):
        if self.bag is not None:
            try:
                fc = self.bag.fcurves.new(path, index=index)
                if group:
                    try:
                        grp = None
                        for g in self.bag.groups:
                            if g.name == group:
                                grp = g
                                break
                        fc.group = grp or self.bag.groups.new(group)
                    except Exception:
                        pass
                return fc
            except Exception:
                pass
        try:
            return self.act.fcurves.new(path, index=index, action_group=group)
        except Exception:
            return self.act.fcurves.new(path, index=index)

    def assign(self):
        ad = self.owner.animation_data or self.owner.animation_data_create()
        ad.action = self.act
        try:
            if self.slot is not None and getattr(ad, "action_slot", None) is None:
                ad.action_slot = self.slot
            elif getattr(ad, "action_slot", None) is None and len(self.act.slots):
                ad.action_slot = self.act.slots[0]
        except Exception:
            pass


def _read_keys(fc):
    return [(kp.co[0], kp.co[1], kp.interpolation, kp.handle_left_type, kp.handle_right_type,
             kp.handle_left[0], kp.handle_left[1], kp.handle_right[0], kp.handle_right[1])
            for kp in fc.keyframe_points]


def _write_keys(fc, keys):
    pts = fc.keyframe_points
    pts.add(len(keys))
    pts.foreach_set("co", [c for k in keys for c in (k[KC.F], k[KC.V])])
    for i, k in enumerate(keys):
        kp = pts[i]
        if kp.interpolation != k[KC.INTERP]:
            kp.interpolation = k[KC.INTERP]
        if k[KC.LTYPE] not in KC.AUTO or k[KC.RTYPE] not in KC.AUTO:
            kp.handle_left_type = k[KC.LTYPE]
            kp.handle_right_type = k[KC.RTYPE]
            kp.handle_left = (k[KC.LX], k[KC.LY])
            kp.handle_right = (k[KC.RX], k[KC.RY])
    fc.update()


def _copy_keys(arm, name, plans, start_frame, h, warns):
    """Copy the keyframes of every animation, one after the other, into ONE normal action on the skeleton.
    Looping, speed and the melt (blend) between neighbours are written as ordinary keys, so the Graph Editor /
    Dope Sheet can edit everything. Returns (action name, number of curves)."""
    n = len(plans)
    spans, t = [], int(start_frame)
    for p in plans:
        spans.append((t, int(p["d"])))
        t += int(p["d"])
    wins = KC.windows(spans, h, n)

    sources = []           # per animation: {(path, index): (keys, evaluate)}
    channels = {}          # (path, index) in order of first appearance
    for p in plans:
        play = p["play"]
        table = {}
        for fc in _fcurves(play):
            if fc.mute or not fc.data_path.startswith("pose.bones["):
                continue
            if not len(fc.keyframe_points):
                continue
            key = (fc.data_path, fc.array_index)
            table[key] = (_read_keys(fc), fc.evaluate)
            channels.setdefault(key, None)
        sources.append(table)

    parts = {}
    for key in channels:
        per = []
        for i, p in enumerate(plans):
            lo, hi = wins[i]
            src = sources[i].get(key)
            if src is None:
                per.append(KC.flat_keys(lo, hi, KC.default_value(key[0], key[1])))
            else:
                a0 = p.get("a0", p["play"].frame_range[0])
                per.append(KC.clip_keys(src[0], src[1], a0, p["alen"], spans[i][0], spans[i][1],
                                        p.get("pspeed", 1.0), lo, hi))
        parts[key] = per

    for key in list(channels):                      # quaternions: keep neighbours on the same side
        if key[0].endswith(".rotation_quaternion") and key[1] == 0:
            comps = [(key[0], c) for c in range(4)]
            if all(c in parts for c in comps):
                KC.fix_quaternion_signs([parts[c] for c in comps])

    ka = _KeyAction(f"CS_Keys_{_safe_name(arm.name)}", arm)
    count = 0
    for key in channels:
        keys = KC.join(parts[key])
        if not keys:
            continue
        m = re.match(r'pose\.bones\["(.+?)"\]', key[0])
        try:
            fc = ka.new_curve(key[0], key[1], m.group(1) if m else "")
            _write_keys(fc, keys)
            count += 1
        except Exception as ex:
            warns.append(f"could not copy {key[0]}[{key[1]}] ({ex})")
            break
    ka.assign()
    return ka.act.name, count


def _anim_length(act):
    if act is None:
        return 30
    return max(2, int(round(act.frame_range[1] - act.frame_range[0])))


def build_character(context, item):
    """(Re)build the animation of one character from its clip list. Returns (ok, message)."""
    s = _S(context)
    scene = context.scene
    arm = bpy.data.objects.get(item.armature_name)
    if arm is None or arm.name not in scene.objects:
        return False, f"{item.name}: the character is not in the scene (use Restore)"
    if arm.get("cs_baked"):
        return False, (f"{item.name}: animation is in EDIT mode (baked) - press 'Back to Sequence' "
                       "to rebuild it from the list")
    root = _ensure_root(context, item.name, arm)
    _clear_generated(arm, root)
    item.end_frame = 0
    arm["cs_check"] = ""
    if not len(item.clips):
        context.view_layer.update()
        _update_range(context)
        return True, f"{item.name}: no animations"

    context.view_layer.update()
    heightk = max(0.2, _char_height(arm) / 1.75)
    fps = max(1.0, scene.render.fps / (scene.render.fps_base or 1.0))
    rot_inv = root.matrix_world.to_3x3().normalized().to_quaternion().inverted().to_matrix()
    base_yaw = root.rotation_euler.z
    try:
        rs = _retarget_setup(arm, rot_inv)
    except Exception:
        rs = None
    face = rs["face"] if rs else 0.0
    warns, made, plans = [], [], []

    for i, c in enumerate(item.clips):
        anim = s.animations.get(c.action)
        act = bpy.data.actions.get(anim.action_name) if anim else None
        if act is None:
            warns.append(f"'{c.action}' is not in the Animation Library - skipped")
            continue
        tag = f"CS_S_{_safe_name(item.name)}_{i + 1}"
        play, fit, vec, note = _prepare_action(act, arm, tag, rot_inv, heightk, rs, bool(s.smart_fit))
        c.info = note
        if play is not act:
            made.append(play.name)
        if fit < 0.75:
            warns.append(f"'{c.action}' fits only {int(fit * 100)}% of the bones of {item.name} "
                         "(different skeleton) - the motion may look wrong")
        a0 = float(play.frame_range[0])
        alen = max(1.0, play.frame_range[1] - play.frame_range[0])
        if s.copy_keys and c.src_end > c.src_start:       # only these frames are copied
            a0, alen = float(c.src_start), max(1.0, float(c.src_end - c.src_start))
        d = max(2, int(c.frames))
        ps = max(0.05, float(c.play_speed))
        plans.append({"d": d, "move": c.move, "turn": c.turn, "speed": c.speed * ps,
                      "location": tuple(c.location_offset), "rotation": tuple(c.rotation_offset),
                      "run": bool(RUN_RE.search(c.action)), "vec": vec, "loops": d * ps / alen,
                      "pspeed": ps,
                      "play": play, "alen": alen, "a0": a0, "name": _safe_name(c.action)})
    arm["cs_made_actions"] = json.dumps(made)
    if not plans:
        _update_range(context)
        return False, f"{item.name}: " + "; ".join(warns)

    plan = plan_motion(plans, item.start_frame, base_yaw, fps, heightk, s.blend, face)
    if rs is not None:
        arm["cs_check"] = ("%d of %d bones differ from the animation skeleton (max %.0f deg); character turned %.0f deg; "
                           "retarget %s" % (rs["bad"], rs["count"], rs["max_angle"], rs["turn_deg"],
                                            "ON" if rs["need"] else "not needed"))
        _log(s, f"{item.name}: skeleton check - {rs['bad']} of {rs['count']} bones differ from the animation "
                f"skeleton (max {rs['max_angle']:.0f} deg), character turned {rs['turn_deg']:.0f} deg, "
                f"hips scale x{rs['k']:.3g}, retarget {'ON' if rs['need'] else 'off'}")
    for i, p in enumerate(plans):
        if p["vec"] is not None:
            how = "moves by itself %.2f m per play x %.2f plays" % (math.hypot(*p["vec"]), p["loops"])
        elif p["move"]:
            how = "in-place, moved by speed"
        else:
            how = "stays on the spot"
        _log(s, f"{item.name}: animation {i + 1} '{p['name']}' ({p['d']} frames): {how}")
    _log(s, f"{item.name}: root path (frame, x, y, z) = " +
         "; ".join("%d: %.2f, %.2f, %.2f" % k for k in plan["loc"]))

    # ---- position and turning on the root (delta keys: your own placement stays untouched)
    root.rotation_mode = "XYZ"
    for f, x, y, z in plan["loc"]:
        root.delta_location = (x, y, z)
        root.keyframe_insert("delta_location", frame=f)
    for f, rx, ry, rz in plan["rot"]:
        root.delta_rotation_euler = (rx, ry, rz)
        root.keyframe_insert("delta_rotation_euler", frame=f)
    ad_root = root.animation_data
    if ad_root is not None and ad_root.action is not None:
        for fc in _fcurves(ad_root.action):
            for kp in fc.keyframe_points:
                kp.interpolation = "LINEAR"

    arm["cs_keys"] = False
    # ---- limb animation, copied as normal keyframes (editable in the Graph Editor)
    if arm.type == "ARMATURE" and s.copy_keys:
        try:
            ad = arm.animation_data or arm.animation_data_create()
            ad.action = None
            for tr in [x for x in ad.nla_tracks if x.name.startswith("CS_")]:
                ad.nla_tracks.remove(tr)
            kname, kcount = _copy_keys(arm, item.name, plans, item.start_frame, plan["h"], warns)
            made.append(kname)
            arm["cs_made_actions"] = json.dumps(made)
            arm["cs_keys"] = True
            _log(s, f"{item.name}: keyframes copied onto the character - {kcount} curves in action '{kname}'")
        except Exception:
            warns.append("copying keyframes failed - see the log")
            _log(s, traceback.format_exc())
    # ---- limb animation: strips one after the other (two tracks so neighbours can overlap and blend)
    elif arm.type == "ARMATURE":
        ad = arm.animation_data or arm.animation_data_create()
        ad.action = None
        tracks = []
        for nm in ("CS_Seq_A", "CS_Seq_B"):
            tr = ad.nla_tracks.new()
            tr.name = nm
            tracks.append(tr)
        h = plan["h"]
        n = len(plans)
        for i, (p, (a, b)) in enumerate(zip(plans, plan["strips"])):
            play = p["play"]
            name = f"{i + 1:02d}_{p['name']}"
            strip, placed_on = None, None
            for tr in (tracks[i % 2], tracks[(i + 1) % 2]):
                try:
                    strip = tr.strips.new(name, int(a), play)
                    placed_on = tr
                    break
                except Exception:
                    strip = None
            if strip is None:
                try:
                    tr = ad.nla_tracks.new()
                    tr.name = "CS_Seq_C"
                    strip = tr.strips.new(name, int(a), play)
                    placed_on = tr
                except Exception as ex:
                    warns.append(f"could not place '{p['name']}' ({ex})")
                    continue
            strip.blend_type = "REPLACE"
            strip.extrapolation = "HOLD_FORWARD" if i == n - 1 else "NOTHING"
            ps = p.get("pspeed", 1.0)
            strip.scale = 1.0 / ps
            strip.repeat = max(0.05, (b - a) * ps / p["alen"])
            try:
                if getattr(strip, "action_slot", None) is None and len(play.slots):
                    strip.action_slot = play.slots[0]
            except Exception:
                pass
            if h > 0 and placed_on is tracks[1]:
                try:
                    strip.blend_in = 2 * h
                    if i < n - 1:
                        strip.blend_out = 2 * h
                except Exception:
                    pass

    item.end_frame = int(plan["end"])
    _update_range(context)
    context.view_layer.update()
    msg = f"{item.name}: {len(plans)} animation(s), frames {item.start_frame}-{plan['end']}"
    if warns:
        msg += "  |  " + " | ".join(warns)
        _log(s, msg)
    return True, msg


def _update_range(context):
    """Timeline covers every character's sequence."""
    s = _S(context)
    starts, ends = [], []
    for ch in s.characters:
        if len(ch.clips) and ch.end_frame > 0:
            starts.append(int(ch.start_frame))
            ends.append(int(ch.end_frame))
    if ends:
        context.scene.frame_start = max(1, min(starts))
        context.scene.frame_end = max(ends)


def rebuild_safe(context, item):
    """Rebuild after an edit. Never raises (it runs from property callbacks)."""
    global _BUSY
    if _BUSY:
        return
    _BUSY = True
    try:
        build_character(context, item)
    except Exception:
        _log(_S(context), traceback.format_exc())
    finally:
        _BUSY = False


def rebuild_for_clip(context, clip):
    if _BUSY:
        return
    s = _S(context)
    ptr = clip.as_pointer()
    for ch in s.characters:
        for c in ch.clips:
            if c.as_pointer() == ptr:
                rebuild_safe(context, ch)
                return


def rebuild_all(context):
    s = _S(context)
    for ch in s.characters:
        if len(ch.clips):
            rebuild_safe(context, ch)


def _active_char(context, op):
    s = _S(context)
    if not (0 <= s.char_index < len(s.characters)):
        op.report({"ERROR"}, "Select a character in the Character Library first")
        return None
    return s.characters[s.char_index]


# ======================================================================================
#  Operators
# ======================================================================================
class CARTOON_OT_clip_add(Operator):
    bl_idname = "cartoon.clip_add"
    bl_label = "Add Animation"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = ("Add the animation selected in the Animation Library to the END of the selected "
                      "character's sequence. It starts where the previous animation stopped")

    def execute(self, context):
        global _BUSY
        s = _S(context)
        ch = _active_char(context, self)
        if ch is None:
            return {"CANCELLED"}
        if not (0 <= s.anim_index < len(s.animations)):
            self.report({"ERROR"}, "Select an animation in the Animation Library first")
            return {"CANCELLED"}
        an = s.animations[s.anim_index]
        _BUSY = True
        try:
            clip = ch.clips.add()
            clip.action = an.name
            clip.frames = _anim_length(bpy.data.actions.get(an.action_name))
            clip.move = bool(MOVE_RE.search(an.name))
            clip.speed = 0.0
            clip.play_speed = 1.0
            clip.prev_speed = 1.0
            clip.turn = 0.0
            ch.clip_index = len(ch.clips) - 1
        finally:
            _BUSY = False
        rebuild_safe(context, ch)
        self.report({"INFO"}, f"{an.name} added to {ch.name} (animation {len(ch.clips)})")
        return {"FINISHED"}


class CARTOON_OT_clip_remove(Operator):
    bl_idname = "cartoon.clip_remove"
    bl_label = "Remove Animation"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Remove the selected animation from the character's sequence"

    def execute(self, context):
        ch = _active_char(context, self)
        if ch is None:
            return {"CANCELLED"}
        if 0 <= ch.clip_index < len(ch.clips):
            ch.clips.remove(ch.clip_index)
            ch.clip_index = max(0, min(ch.clip_index, len(ch.clips) - 1))
        rebuild_safe(context, ch)
        return {"FINISHED"}


class CARTOON_OT_clip_move(Operator):
    bl_idname = "cartoon.clip_move"
    bl_label = "Move Animation"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Play the selected animation earlier or later in the sequence"

    direction: EnumProperty(items=[("UP", "Up", ""), ("DOWN", "Down", "")])

    def execute(self, context):
        ch = _active_char(context, self)
        if ch is None:
            return {"CANCELLED"}
        i = ch.clip_index
        j = i - 1 if self.direction == "UP" else i + 1
        if 0 <= i < len(ch.clips) and 0 <= j < len(ch.clips):
            ch.clips.move(i, j)
            ch.clip_index = j
            rebuild_safe(context, ch)
        return {"FINISHED"}


class CARTOON_OT_seq_build(Operator):
    bl_idname = "cartoon.seq_build"
    bl_label = "Update Animation"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = ("Rebuild the animation from the list (this also happens automatically when you change "
                      "something). Use it after you moved or rotated the character, or restored it")

    all_characters: bpy.props.BoolProperty(name="All characters", default=False)

    def execute(self, context):
        s = _S(context)
        if self.all_characters:
            items = [c for c in s.characters if len(c.clips)]
        else:
            ch = _active_char(context, self)
            if ch is None:
                return {"CANCELLED"}
            items = [ch]
        bad = []
        for ch in items:
            try:
                ok, msg = build_character(context, ch)
            except Exception as e:
                _log(s, traceback.format_exc())
                ok, msg = False, f"{ch.name}: {e}"
            if not ok:
                bad.append(msg)
        if bad:
            self.report({"WARNING"}, " | ".join(bad))
        else:
            self.report({"INFO"}, f"Animation updated for {len(items)} character(s)")
        return {"FINISHED"}


class CARTOON_OT_seq_clear(Operator):
    bl_idname = "cartoon.seq_clear"
    bl_label = "Clear All Animation"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = ("Remove every animation from the selected character and put it back in its rest pose. "
                      "The character stays where you placed it")

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        ch = _active_char(context, self)
        if ch is None:
            return {"CANCELLED"}
        ch.clips.clear()
        ch.clip_index = 0
        rebuild_safe(context, ch)
        self.report({"INFO"}, f"Animation cleared for {ch.name}")
        return {"FINISHED"}


def _enter_pose_mode(context, arm):
    """Select the skeleton, go to Pose Mode and switch Auto-Key on so every edit is saved."""
    try:
        if context.object is not None and context.object.mode != "OBJECT":
            bpy.ops.object.mode_set(mode="OBJECT")
    except Exception:
        pass
    for o in context.view_layer.objects:
        o.select_set(False)
    arm.hide_set(False)
    arm.select_set(True)
    context.view_layer.objects.active = arm
    try:
        bpy.ops.object.mode_set(mode="POSE")
    except Exception:
        pass
    context.scene.tool_settings.use_keyframe_insert_auto = True


class CARTOON_OT_bake_edit(Operator):
    bl_idname = "cartoon.bake_edit"
    bl_label = "Edit Pose"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = ("Turn the sequence into ONE normal action on the skeleton (keyframes you can edit). "
                      "After this your pose edits stay, and the sequence is no longer rebuilt automatically")

    def execute(self, context):
        ch = _active_char(context, self)
        if ch is None:
            return {"CANCELLED"}
        arm = bpy.data.objects.get(ch.armature_name)
        if arm is None or arm.type != "ARMATURE" or arm.animation_data is None:
            self.report({"ERROR"}, "This character has no skeleton animation to bake")
            return {"CANCELLED"}
        if arm.get("cs_baked"):
            _enter_pose_mode(context, arm)
            return {"FINISHED"}
        if ch.end_frame <= 0:
            self.report({"ERROR"}, "Build the sequence first (press Update)")
            return {"CANCELLED"}
        if arm.get("cs_keys"):
            # the keyframes are already ONE normal action: just stop rebuilding so your edits stay
            arm["cs_baked"] = True
            _enter_pose_mode(context, arm)
            self.report({"INFO"}, f"{ch.name}: your keyframes are kept now. Edit them in the Graph Editor / "
                                  "Dope Sheet. 'Back to Sequence' rebuilds from the list")
            return {"FINISHED"}
        for o in context.view_layer.objects:
            o.select_set(False)
        arm.select_set(True)
        context.view_layer.objects.active = arm
        context.scene.frame_set(int(ch.start_frame))
        try:
            bpy.ops.nla.bake(frame_start=int(ch.start_frame), frame_end=int(ch.end_frame),
                             only_selected=False, visual_keying=True, clear_constraints=False,
                             clear_parents=False, use_current_action=False, bake_types={"POSE"})
        except Exception as e:
            self.report({"ERROR"}, f"Bake failed: {e}")
            return {"CANCELLED"}
        ad = arm.animation_data
        baked = ad.action
        if baked is None:
            self.report({"ERROR"}, "Bake produced no action")
            return {"CANCELLED"}
        baked.name = f"CS_Baked_{_safe_name(ch.name)}"
        for tr in [t for t in ad.nla_tracks if t.name.startswith("CS_")]:
            ad.nla_tracks.remove(tr)
        ad.action = baked
        arm["cs_baked"] = True
        _enter_pose_mode(context, arm)
        self.report({"INFO"}, f"{ch.name}: pose edit ON. Pick a frame, move/rotate/scale bones - "
                              "Auto-Key saves it. Press 'Finish' when done")
        return {"FINISHED"}


class CARTOON_OT_unbake(Operator):
    bl_idname = "cartoon.unbake"
    bl_label = "Back to Sequence"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Throw away the baked/edited pose animation and rebuild the sequence from the list"

    def execute(self, context):
        ch = _active_char(context, self)
        if ch is None:
            return {"CANCELLED"}
        arm = bpy.data.objects.get(ch.armature_name)
        if arm is not None:
            act = arm.animation_data.action if arm.animation_data else None
            if arm.animation_data:
                arm.animation_data.action = None
            if act is not None and act.name.startswith(("CS_Baked_", "CS_Keys_")) and act.users == 0:
                bpy.data.actions.remove(act)
            arm["cs_baked"] = False
        rebuild_safe(context, ch)
        return {"FINISHED"}


class CARTOON_OT_pose_finish(Operator):
    bl_idname = "cartoon.pose_finish"
    bl_label = "Finish Pose Edit"
    bl_options = {"REGISTER", "UNDO"}
    bl_description = "Leave Pose Mode and turn Auto-Key off. Your edits stay on the animation"

    def execute(self, context):
        try:
            if context.object is not None and context.object.mode != "OBJECT":
                bpy.ops.object.mode_set(mode="OBJECT")
        except Exception:
            pass
        context.scene.tool_settings.use_keyframe_insert_auto = False
        return {"FINISHED"}


classes = (
    CARTOON_OT_pose_finish,
    CARTOON_OT_bake_edit,
    CARTOON_OT_unbake,
    CARTOON_OT_clip_add,
    CARTOON_OT_clip_remove,
    CARTOON_OT_clip_move,
    CARTOON_OT_seq_build,
    CARTOON_OT_seq_clear,
)


def register():
    for c in classes:
        bpy.utils.register_class(c)


def unregister():
    for c in reversed(classes):
        bpy.utils.unregister_class(c)
