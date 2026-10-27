bl_info = {
    "name": "Cartoon Studio Pro (Characters + Animation Sequence)",
    "author": "Cartoon Studio",
    "version": (1, 9, 2),
    "blender": (4, 2, 0),
    "location": "View3D > Sidebar > Cartoon Studio",
    "description": "Character library, animation library, and one simple animation sequence per character",
    "category": "Animation",
}

import os as _os

_REQUIRED = ("helpers", "data", "characters", "animations", "sequence", "retarget", "keycopy", "rigfit", "ui")
_folder = _os.path.dirname(__file__)
_problems = []
for _n in _REQUIRED:
    _p = _os.path.join(_folder, _n + ".py")
    if not _os.path.isfile(_p):
        _problems.append(_n + ".py is MISSING")
    elif _os.path.getsize(_p) == 0:
        _problems.append(_n + ".py is EMPTY")
if _problems:
    raise ImportError("Cartoon Studio folder problem: " + "; ".join(_problems) + "  | folder: " + _folder)

from . import data, characters, animations, sequence, ui

# data FIRST (settings must exist), ui LAST
modules = (data, characters, animations, sequence, ui)


def register():
    for m in modules:
        m.register()


def unregister():
    for m in reversed(modules):
        m.unregister()
