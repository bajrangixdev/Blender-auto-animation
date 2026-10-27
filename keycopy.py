"""Copy the keyframes of several animations, one after the other, into ONE editable curve per channel.

This is what you do by hand: copy the keyframes of animation 1, paste them on the character, copy the keyframes
of animation 2, paste them after it ... and then open the Graph Editor to fix the joins. Here it is done
automatically. Pure python (no Blender needed) so it can be tested on its own.

A key is a tuple:  (frame, value, interpolation, left_type, right_type, left_x, left_y, right_x, right_y)
"""
import math

F, V, INTERP, LTYPE, RTYPE, LX, LY, RX, RY = range(9)
AUTO = ("AUTO", "AUTO_CLAMPED")
EPS = 1e-4


def default_value(data_path, index):
    """Rest value of a pose-bone channel (used when one animation does not animate that channel)."""
    if data_path.endswith("rotation_quaternion"):
        return 1.0 if index == 0 else 0.0
    if data_path.endswith(".scale"):
        return 1.0
    return 0.0


def _auto_key(f, v):
    return (f, v, "BEZIER", "AUTO_CLAMPED", "AUTO_CLAMPED", f - 1.0, v, f + 1.0, v)


def clip_keys(src, ev, a0, alen, S, d, ps, lo, hi):
    """Keys of ONE animation channel placed on the timeline.

    src   keys of the source channel (frames a0 .. a0+alen)
    ev    function(source_frame) -> value of the source channel (used where no key exists)
    S, d  the animation starts at frame S and plays for d frames
    ps    animation speed (2 = twice as fast)
    lo,hi only frames inside [lo, hi] are kept (the joins to the neighbours are made outside)
    Longer than the animation = it repeats (loops), exactly like the old strips did.
    """
    alen = max(1e-6, float(alen))
    out = []
    reps = max(1, int(math.ceil(d * ps / alen - 1e-9)))

    def place(x, r):
        return S + (r * alen + (x - a0)) / ps

    for r in range(reps):
        for k in src:
            x = k[F]
            if x < a0 - EPS or x > a0 + alen + EPS:
                continue
            if r > 0 and abs(x - a0) < EPS:
                continue                      # same pose as the end of the previous repeat
            f = place(x, r)
            if f < lo - EPS or f > hi + EPS:
                continue
            lx, rx = place(k[LX], r), place(k[RX], r)
            out.append((f, k[V], k[INTERP], k[LTYPE], k[RTYPE], lx, k[LY], rx, k[RY]))

    def ensure(f, smooth=False):
        # A source key exactly on a clip boundary carries handles designed for the
        # source animation, not for the neighbouring clip. Reuse its value but
        # give the join a fresh, clamped handle so the transition eases cleanly.
        for i, k in enumerate(out):
            if abs(k[F] - f) < EPS:
                if smooth:
                    out[i] = _auto_key(f, k[V])
                return
        t = (f - S) * ps
        tm = t % alen
        if t > EPS and (tm < EPS or alen - tm < EPS):
            tm = alen
        out.append(_auto_key(f, ev(a0 + tm)))

    ensure(lo, smooth=True)
    if hi > lo + EPS:
        ensure(hi, smooth=True)
    out.sort(key=lambda k: k[F])
    return out


def flat_keys(lo, hi, value):
    """Two flat keys: the channel rests at 'value' while this animation plays."""
    if hi <= lo + EPS:
        return [_auto_key(lo, value)]
    return [_auto_key(lo, value), _auto_key(hi, value)]


def windows(plan_spans, h, n):
    """[(lo, hi)] per animation. Neighbours blend between hi(i) and lo(i+1)."""
    out = []
    for i, (S, d) in enumerate(plan_spans):
        lo = S + (h if i > 0 else 0)
        if i < n - 1:
            hi = S + d - (h if h > 0 else 1)
        else:
            hi = S + d
        out.append((float(lo), float(max(lo, hi))))
    return out


def join(parts):
    """Join per-animation key lists of one channel. Drops keys that would go backwards in time."""
    out = []
    for keys in parts:
        for k in keys:
            if out and k[F] <= out[-1][F] + EPS:
                continue
            out.append(k)
    return out


def flip_key(k):
    return (k[F], -k[V], k[INTERP], k[LTYPE], k[RTYPE], k[LX], -k[LY], k[RX], -k[RY])


def fix_quaternion_signs(parts4):
    """parts4[c][i] = key list of quaternion component c (w,x,y,z) for animation i.
    q and -q are the same rotation, but blending between them spins the long way round.
    Flip a whole animation's quaternions when they start on the opposite side to where the previous one ended."""
    n = len(parts4[0])
    flipped = [False] * n
    for i in range(1, n):
        prev, cur = [], []
        for c in range(4):
            p = parts4[c][i - 1]
            q = parts4[c][i]
            if not p or not q:
                break
            prev.append(p[-1][V])
            cur.append(q[0][V])
        if len(prev) < 4:
            continue
        if sum(a * b for a, b in zip(prev, cur)) < 0.0:
            flipped[i] = True
            for c in range(4):
                parts4[c][i] = [flip_key(k) for k in parts4[c][i]]
    return flipped
