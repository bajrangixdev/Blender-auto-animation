"""Pose retargeting maths (numpy only, no Blender needed, so it can be tested on its own).

Problem: a Mixamo animation stores every bone rotation in the BONE'S OWN axes. That is only right on a
skeleton whose bones have exactly the same axes (and rest pose) as the skeleton the animation was made on.
If the character's bones are turned differently (other roll, other import options, A-pose instead of
T-pose ...) the same numbers fold the body the wrong way - the character tips over or bends.

Fix: work with how far each bone turns in WORLD axes away from its rest pose ("delta"), then give the
target skeleton the bone-local rotations that produce the same world turning.
  delta(bone)   = delta(parent) * R * q * R^T          (R = bone rest orientation, q = bone-local rotation)
  q_target      = Rt^T * delta_t(parent)^T * delta_t(bone) * Rt
  delta_t(bone) = G * delta_s(bone) * G^T              (G = turn of the target character about the vertical)
"""
import math
import numpy as np

I3 = np.eye(3)


def yaw_matrix(a):
    c, s = math.cos(a), math.sin(a)
    return np.array([[c, -s, 0.0], [s, c, 0.0], [0.0, 0.0, 1.0]])


def quat_to_mat(q):
    w, x, y, z = q
    n = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
    w, x, y, z = w / n, x / n, y / n, z / n
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])


def mat_to_quat(m):
    t = m[0, 0] + m[1, 1] + m[2, 2]
    if t > 0.0:
        s = math.sqrt(t + 1.0) * 2.0
        w = 0.25 * s
        x = (m[2, 1] - m[1, 2]) / s
        y = (m[0, 2] - m[2, 0]) / s
        z = (m[1, 0] - m[0, 1]) / s
    elif m[0, 0] > m[1, 1] and m[0, 0] > m[2, 2]:
        s = math.sqrt(1.0 + m[0, 0] - m[1, 1] - m[2, 2]) * 2.0
        w = (m[2, 1] - m[1, 2]) / s
        x = 0.25 * s
        y = (m[0, 1] + m[1, 0]) / s
        z = (m[0, 2] + m[2, 0]) / s
    elif m[1, 1] > m[2, 2]:
        s = math.sqrt(1.0 + m[1, 1] - m[0, 0] - m[2, 2]) * 2.0
        w = (m[0, 2] - m[2, 0]) / s
        x = (m[0, 1] + m[1, 0]) / s
        y = 0.25 * s
        z = (m[1, 2] + m[2, 1]) / s
    else:
        s = math.sqrt(1.0 + m[2, 2] - m[0, 0] - m[1, 1]) * 2.0
        w = (m[1, 0] - m[0, 1]) / s
        x = (m[0, 2] + m[2, 0]) / s
        y = (m[1, 2] + m[2, 1]) / s
        z = 0.25 * s
    n = math.sqrt(w * w + x * x + y * y + z * z) or 1.0
    return (w / n, x / n, y / n, z / n)


def angle_between(a, b):
    """Angle in degrees between two rotation matrices."""
    c = (np.trace(a.T @ b) - 1.0) / 2.0
    return math.degrees(math.acos(max(-1.0, min(1.0, c))))


def retarget_frame(parent, Rs, Rt, G, q_src):
    """parent[i] = index of the parent (parents come first) or -1. Rs / Rt = rest orientation of every bone
    on the source / target skeleton (3x3, world axes). q_src[i] = source bone-local rotation (w, x, y, z).
    Returns the bone-local rotations for the target skeleton."""
    n = len(parent)
    ds, dt, out = [None] * n, [None] * n, [None] * n
    for i in range(n):
        local_s = Rs[i] @ quat_to_mat(q_src[i]) @ Rs[i].T
        pds = ds[parent[i]] if parent[i] >= 0 else I3
        ds[i] = pds @ local_s
        dt[i] = G @ ds[i] @ G.T
        pdt = dt[parent[i]] if parent[i] >= 0 else I3
        out[i] = mat_to_quat(Rt[i].T @ pdt.T @ dt[i] @ Rt[i])
    return out


def retarget_vector(v, Rs, Rt, G, k=1.0):
    """A bone-local translation (hips) from the source bone's axes to the target bone's axes."""
    return k * (Rt.T @ (G @ (Rs @ np.asarray(v, dtype=float))))


def mismatch(Rs, Rt, G):
    """Angle (degrees) per bone between source rest orientation (turned by G) and target rest orientation."""
    return [angle_between(G @ a, b) for a, b in zip(Rs, Rt)]


def needs_retarget(angles, limit=8.0, share=0.25):
    if not angles:
        return False
    bad = sum(1 for a in angles if a > limit)
    return bad >= max(3, int(share * len(angles)))
