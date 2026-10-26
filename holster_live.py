# /// script
# dependencies = ["pymhf>=0.2.4"]
#
# [tool.pymhf]
# exe = "NMS.exe"
# steam_gameid = 275850
# start_paused = false
# interactive_console = false
#
# [tool.pymhf.gui]
# shown = false
#
# [tool.pymhf.logging]
# log_dir = "logs"
# log_level = "info"
# shown = false
# ///
import ctypes
import hashlib
import json
import logging
import math
import os
import re
import threading
import time
from ctypes import _Pointer, c_bool, c_uint64

import nmspy.data.basic_types as basic
from nmspy.common import gameData
from nmspy.data import types as nms
from pymhf import Mod
from pymhf.core.hooking import on_key_pressed, static_function_hook

MOD_DIR = os.path.dirname(os.path.abspath(__file__))
STATE_FILE = os.path.join(MOD_DIR, "holster.json")
os.makedirs(os.path.join(MOD_DIR, "logs"), exist_ok=True)

logger = logging.getLogger(f"holster.{os.path.basename(MOD_DIR)}")
_fh = logging.FileHandler(os.path.join(MOD_DIR, "logs", "holster.log"), encoding="utf-8")
_fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(_fh)
logger.setLevel(logging.INFO)

SLOT_TYPES = {
    "belt_l": ("BELT RIGHT", "player01_ROOTSHJnt", ([0.0, 0.0, -0.17], [0.0, 0.0, 0.0], 0.55)),
    "belt_r": ("BELT LEFT", "player01_ROOTSHJnt", ([0.0, 0.0, 0.17], [0.0, 0.0, 0.0], 0.55)),
    "thigh_l": ("THIGH RIGHT", "player01_l_leg_HipSHJnt", ([0.0, 0.0, 0.0], [0.0, 0.0, 0.0], 0.55)),
    "thigh_r": ("THIGH LEFT", "player01_r_leg_HipSHJnt", ([0.0, 0.0, 0.0], [0.0, 0.0, 0.0], 0.55)),
    "back": ("BACK", "player01_spine_TopSHJnt", ([0.0, 0.0, 0.0], [0.0, 0.0, 0.0], 0.55)),
    "backpack": ("BACKPACK", "player01_backpack_root_AuxSHJnt", ([0.0, 0.0, 0.0], [0.0, 0.0, 0.0], 0.55)),
}
SLOTS = 3
SHIPPED_POSES = {
    "thigh_l": {0: {"t": [0.035, 0.12, 0.01], "r": [-180.0, -98.0, 87.0], "s": 0.55, "p": [0.0, 0.0, 0.0]}},
    "thigh_r": {0: {"t": [-0.035, -0.12, -0.01], "r": [0.0, -98.0, -87.0], "s": 0.55, "p": [0.0, 0.0, 0.0]}},
    "belt_l": {
        0: {"t": [0.05, -0.025, -0.205], "r": [-5.0, -98.0, 9.0], "s": 0.55, "p": [0.0, 0.0, 0.0]},
        1: {"t": [0.085, 0.015, -0.205], "r": [6.0, -102.0, 9.0], "s": 0.55, "p": [0.0, 0.0, 0.0]},
    },
    "belt_r": {
        0: {"t": [0.05, -0.025, 0.205], "r": [-5.0, -82.0, -9.0], "s": 0.55, "p": [0.0, 0.0, 0.0]},
        1: {"t": [0.085, 0.015, 0.205], "r": [6.0, -78.0, -9.0], "s": 0.55, "p": [0.0, 0.0, 0.0]},
    },
    "back": {0: {"t": [0.34, -0.195, -0.25], "r": [-2.0, -59.0, -92.0], "s": 0.55, "p": [0.0, 0.0, 0.0]}},
    "backpack": {
        0: {"t": [0.12, 0.325, 0.25], "r": [78.0, -98.0, -9.0], "s": 0.56, "p": [-0.055, 0.0, 0.0]},
        1: {"t": [0.22, 0.28, 0.055], "r": [93.0, -12.0, -9.0], "s": 0.56, "p": [-0.055, 0.0, 0.0]},
    },
}
SHIPPED_NAMES = {t: ["DEFAULT", "DEFAULT 2", "PRESET 3"] for t in ("backpack", "belt_l", "belt_r")}
GENERIC_NAMES = ["DEFAULT", "PRESET 2", "PRESET 3"]
CURRENT = "@current"
LIST_MAX = 16
PRESETS = 3
INVALID = 0x7FFFF
TOOL_RX = re.compile(r"MODELS/COMMON/WEAPONS/MULTITOOL/[A-Z]*MULTITOOL[A-Z]*\.SCENE\.MBIN$")
PHYS_DEFAULT = {"mode": 0.0, "k": 600.0, "c": 45.0, "len": 0.25, "rad": 0.04, "body": 1.0, "max": 6.0}
def new_profile():
    return {
        "slots": [{"type": t, "tool": CURRENT if t == "belt_l" else None, "show": True, "preset": 0}
                  for t in ("belt_l", "back", "backpack")],
        "tools": {},
        "names": {},
        "templates": {},
    }


DEFAULT_STATE = {"mode": "simple", "simple": new_profile(), "exp": new_profile(), "catalog": [], "phys": PHYS_DEFAULT}
CAPSULES = [
    ("player01_l_leg_HipSHJnt", "player01_l_leg_KneeSHJnt", 0.085),
    ("player01_r_leg_HipSHJnt", "player01_r_leg_KneeSHJnt", 0.085),
    ("player01_ROOTSHJnt", "player01_spine_01SHJnt", 0.14),
    ("player01_spine_01SHJnt", "player01_spine_TopSHJnt", 0.15),
]
BONE_CAPSULES = [
    ("player01_backpack_root_AuxSHJnt", (-0.08, -0.44, 0.09), (-0.08, 0.16, 0.09), 0.11),
    ("player01_backpack_root_AuxSHJnt", (0.08, -0.44, 0.09), (0.08, 0.16, 0.09), 0.11),
]
COLLIDE_SMOOTH = 25.0
BONE_SETTLE = 1.2
MARKER_SCALE = 0.06
UI_NAME = "holster"
PANEL_KEY = "insert"
VK_ESCAPE = 0x1B
FIELDS = {
    "tx": (("pose", ("t", 0)), (-0.6, 0.6), 0.005), "ty": (("pose", ("t", 1)), (-0.6, 0.6), 0.005),
    "tz": (("pose", ("t", 2)), (-0.6, 0.6), 0.005),
    "rx": (("pose", ("r", 0)), (-180.0, 180.0), 1.0), "ry": (("pose", ("r", 1)), (-180.0, 180.0), 1.0),
    "rz": (("pose", ("r", 2)), (-180.0, 180.0), 1.0),
    "sc": (("pose", ("s", None)), (0.2, 2.0), 0.01),
    "qx": (("pose", ("p", 0)), (-0.8, 0.8), 0.005), "qy": (("pose", ("p", 1)), (-0.8, 0.8), 0.005),
    "qz": (("pose", ("p", 2)), (-0.8, 0.8), 0.005),
    "pk": (("phys", ("k", None)), (20.0, 2000.0), 20.0), "pc": (("phys", ("c", None)), (0.0, 120.0), 2.0),
    "pa": (("phys", ("max", None)), (0.0, 45.0), 1.0),
    "pl": (("phys", ("len", None)), (-0.8, 0.8), 0.01), "pr": (("phys", ("rad", None)), (0.01, 0.15), 0.005),
    "pb": (("phys", ("body", None)), (0.3, 2.0), 0.05),
}


class Mat34(ctypes.Structure):
    _fields_ = [("m", ctypes.c_float * 16)]


def matrix(t, r, s):
    x, y, z = (math.radians(v) for v in r)
    cx, sx, cy, sy, cz, sz = math.cos(x), math.sin(x), math.cos(y), math.sin(y), math.cos(z), math.sin(z)
    R = [[cy * cz + sy * sx * sz, -cy * sz + sy * sx * cz, sy * cx],
         [cx * sz, cx * cz, -sx],
         [-sy * cz + cy * sx * sz, sy * sz + cy * sx * cz, cy * cx]]
    m = Mat34()
    m.m[:] = [R[0][0] * s, R[1][0] * s, R[2][0] * s, 0,
              R[0][1] * s, R[1][1] * s, R[2][1] * s, 0,
              R[0][2] * s, R[1][2] * s, R[2][2] * s, 0,
              t[0], t[1], t[2], 1]
    return m


def to4(m):
    v = list(m.m)
    return [[v[0], v[4], v[8], v[12]], [v[1], v[5], v[9], v[13]], [v[2], v[6], v[10], v[14]], [0, 0, 0, 1]]


def from4(M):
    m = Mat34()
    m.m[:] = [M[0][0], M[1][0], M[2][0], 0, M[0][1], M[1][1], M[2][1], 0,
              M[0][2], M[1][2], M[2][2], 0, M[0][3], M[1][3], M[2][3], 1]
    return m


def mul4(A, B):
    return [[sum(A[i][k] * B[k][j] for k in range(4)) for j in range(4)] for i in range(4)]


def inv3(A):
    a, b, c = A[0][:3]
    d, e, f = A[1][:3]
    g, h, i = A[2][:3]
    det = a * (e * i - f * h) - b * (d * i - f * g) + c * (d * h - e * g)
    return [[(e * i - f * h) / det, (c * h - b * i) / det, (b * f - c * e) / det],
            [(f * g - d * i) / det, (a * i - c * g) / det, (c * d - a * f) / det],
            [(d * h - e * g) / det, (b * g - a * h) / det, (a * e - b * d) / det]]


def inv4(M):
    R = inv3(M)
    t = [-sum(R[i][k] * M[k][3] for k in range(3)) for i in range(3)]
    return [R[0] + [t[0]], R[1] + [t[1]], R[2] + [t[2]], [0, 0, 0, 1]]


def rotation_part(M):
    R = [row[:3] for row in M[:3]]
    for _ in range(8):
        it = inv3(R)
        R = [[0.5 * (R[i][j] + it[j][i]) for j in range(3)] for i in range(3)]
    return [R[0] + [M[0][3]], R[1] + [M[1][3]], R[2] + [M[2][3]], [0, 0, 0, 1]]


class PrNodeApi:

    @static_function_hook(
        "4C 8B C1 8B CA C1 E9 ? 85 C9 0F 84 ? ? ? ? 8B C2 25 ? ? ? ? 3D ? ? ? ? 74 ? 4C 8B 0D ? ? ? ? 81 "
        "E2 ? ? ? ? 49 8B 81 ? ? ? ? 4C 63 14 90 41 83 FA ? 74 ? 4D 8B 99 ? ? ? ? 41 0F BF 04 53 3B C8 "
        "75"
    )
    @staticmethod
    def GetNodeFirstChild(result: _Pointer[basic.TkHandle], node: basic.TkHandle) -> c_uint64:
        ...

    @static_function_hook(
        "48 89 5C 24 ? 4C 8B C1 8B CA C1 E9 ? 85 C9 0F 84 ? ? ? ? 8B C2 25 ? ? ? ? 3D ? ? ? ? 0F 84 "
        "? ? ? ? 4C 8B 0D ? ? ? ? 81 E2 ? ? ? ? 49 8B 99"
    )
    @staticmethod
    def GetNodeNextSibling(result: _Pointer[basic.TkHandle], node: basic.TkHandle) -> c_uint64:
        ...

    @static_function_hook(
        "8B D1 C1 EA ? 85 D2 74 ? 8B C1 25 ? ? ? ? 3D ? ? ? ? 74 ? 4C 8B 05 ? ? ? ? 81 E1 ? ? ? ? 49 8B "
        "80 ? ? ? ? 4C 63 0C 88 41 83 F9 ? 74 ? 49 8B 80 ? ? ? ? 0F BF 0C 48 3B D1 75 ? 49 8B 80 ? ? ? ? "
        "41 0F B6 04 01 24 01"
    )
    @staticmethod
    def GetNodeActivation(node: basic.TkHandle) -> c_bool: ...

    @static_function_hook(
        "44 8B C1 41 C1 E8 ? 45 85 C0 74 ? 8B C1 25 ? ? ? ? 3D ? ? ? ? 74 ? 4C 8B 0D ? ? ? ? 81 E1 "
        "? ? ? ?"
    )
    @staticmethod
    def SetNodeActivation(node: basic.TkHandle, lbActive: c_bool): ...


def th(h):
    return basic.TkHandle(lookupInt=h)


class Engine:

    def __init__(self):
        self.scene = nms.cEgModules.mgpSceneManager.contents.mData
        logger.info(f"API: scene data {ctypes.addressof(self.scene):#x}")

    def children(self, h):
        out = basic.TkHandle()
        PrNodeApi.GetNodeFirstChild(ctypes.byref(out), th(h))
        c = out.lookupInt
        for _ in range(4096):
            if c in (0, INVALID):
                return
            yield c
            prev = c
            PrNodeApi.GetNodeNextSibling(ctypes.byref(out), th(c))
            c = out.lookupInt
            if c == prev:
                return

    def name(self, h):
        try:
            return bytes(nms.Engine.GetNodeName(th(h))).decode("utf-8", "replace")
        except Exception:
            return ""

    def res(self, h):
        out = ctypes.c_int32(0)
        nms.Engine.GetResourceHandleForNode(ctypes.byref(out), th(h))
        return out.value

    def parent(self, h):
        out = basic.TkHandle()
        nms.Engine.GetNodeParent(ctypes.byref(out), th(h))
        return out.lookupInt

    def top(self, h):
        for _ in range(256):
            p = self.parent(h)
            if p in (0, INVALID) or p == h:
                return h
            h = p
        return h

    def spawn(self, parent, res):
        out = basic.TkHandle()
        if nms.Engine.AddNodes(ctypes.byref(out), th(parent), res) is None:
            raise OSError("AddNodes failed")
        return out.lookupInt

    def absolute(self, h):
        m = basic.cTkMatrix34()
        nms.Engine.GetNodeAbsoluteTransMatrix(th(h), ctypes.byref(m))
        mm = Mat34()
        ctypes.memmove(ctypes.byref(mm), ctypes.byref(m), ctypes.sizeof(Mat34))
        return to4(mm)

    def get_activation(self, h):
        return bool(PrNodeApi.GetNodeActivation(th(h)))

    def set_activation(self, h, on):
        PrNodeApi.SetNodeActivation(th(h), bool(on))

    def set_transform(self, h, m):
        m34 = basic.cTkMatrix34()
        ctypes.memmove(ctypes.byref(m34), ctypes.byref(m), ctypes.sizeof(Mat34))
        self.scene.SetRelativeTransform(th(h), ctypes.byref(m34))


class BoneNotReady(Exception):
    pass


def default_pose(slot_type, preset=0):
    shipped = SHIPPED_POSES.get(slot_type, {})
    if preset in shipped:
        return json.loads(json.dumps(shipped[preset]))
    if preset and 0 in shipped:
        return json.loads(json.dumps(shipped[0]))
    t, r, s = SLOT_TYPES[slot_type][2]
    return {"t": list(t), "r": list(r), "s": s, "p": [0.0, 0.0, 0.0]}


def load_state():
    st = json.loads(json.dumps(DEFAULT_STATE))
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            saved = json.load(f)
    except (OSError, ValueError):
        return st
    st["phys"].update({k: v for k, v in saved.get("phys", {}).items() if k in PHYS_DEFAULT})
    st["catalog"] = saved.get("catalog", [])
    st["mode"] = saved.get("mode", st["mode"])
    for m in ("simple", "exp"):
        prof = saved.get(m)
        if isinstance(prof, dict) and len(prof.get("slots", [])) == SLOTS:
            st[m] = {k: prof.get(k, st[m][k]) for k in st[m]}
    return st


def v_sub(a, b): return [a[0] - b[0], a[1] - b[1], a[2] - b[2]]
def v_add(a, b): return [a[0] + b[0], a[1] + b[1], a[2] + b[2]]
def v_mul(a, k): return [a[0] * k, a[1] * k, a[2] * k]
def v_dot(a, b): return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]
def v_len(a): return math.sqrt(v_dot(a, a))


def v_cross(a, b):
    return [a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0]]


def closest_on_segment(p, a, b):
    ab = v_sub(b, a)
    t = max(0.0, min(1.0, v_dot(v_sub(p, a), ab) / max(v_dot(ab, ab), 1e-9)))
    return v_add(a, v_mul(ab, t))


def rotate_about(pivot, d0, d1):
    a, b = v_mul(d0, 1 / v_len(d0)), v_mul(d1, 1 / v_len(d1))
    axis = v_cross(a, b)
    s, c = v_len(axis), max(-1.0, min(1.0, v_dot(a, b)))
    if s < 1e-6:
        return [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
    x, y, z = v_mul(axis, 1 / s)
    t = 1 - c
    R = [[t * x * x + c, t * x * y - s * z, t * x * z + s * y],
         [t * x * y + s * z, t * y * y + c, t * y * z - s * x],
         [t * x * z - s * y, t * y * z + s * x, t * z * z + c]]
    off = v_sub(pivot, [sum(R[i][k] * pivot[k] for k in range(3)) for i in range(3)])
    return [R[0] + [off[0]], R[1] + [off[1]], R[2] + [off[2]], [0, 0, 0, 1]]


class HolsterLiveV41(Mod):
    __author__ = "Carbonster"
    __description__ = "Multitools in body slots"
    __version__ = "0.4.1"

    def __init__(self):
        super().__init__()
        self.engine = None
        self.ui = None
        self.ui_failed = False
        self.panel_open = False
        self.screen = "main"
        self.edit_slot = 0
        self.list_open = None
        self.types_open = None
        self.lock = threading.Lock()
        self.queue = []
        self.state = load_state()
        self.bones = {}
        self.tool = 0
        self.tool_res = 0
        self.tool_key = None
        self.known = {}
        self.copies = [{"copy": 0, "bone": 0, "res": 0} for _ in range(SLOTS)]
        self.marker = {"copy": 0, "bone": 0, "res": 0}
        self.phys = [self._fresh_phys() for _ in range(SLOTS)]
        self.next_scan = 0.0
        self.tool_drawn = False
        self.hand_scale = 0.0
        self.last_hand = 0.0
        self.next_measure = 0.0
        self.esc_down = False
        self.next_error_log = {}

        self.prev_bones = {}
        self.bone_changed = {}
        self.cache = {}
        self.next_spawn = 0.0

    @property
    def prof(self):
        return self.state[self.state["mode"]]

    @staticmethod
    def _fresh_phys():
        return {"tip": None, "v": [0.0, 0.0, 0.0], "dev": [0.0, 0.0, 0.0], "allowed": None}

    def _push(self, *action):
        with self.lock:
            self.queue.append(action)

    @on_key_pressed(PANEL_KEY)
    def k_panel(self):
        self._push("panel")

    @nms.cGcPlayer.Update.after
    def on_update(self, this, lfStep):
        try:
            if self.engine is None:
                self.engine = Engine()
            self._ensure_ui()
            self._poll_escape()
            with self.lock:
                actions, self.queue = self.queue, []
            for a in actions:
                getattr(self, f"_do_{a[0]}")(*a[1:])
            self._scan_player()
            self._measure()
            for i in range(SLOTS):
                try:
                    self._update_slot(i, lfStep)
                except Exception:
                    self._error(f"slot {i + 1}")
                    self._hide_copy(i)
            try:
                self._update_marker()
            except BoneNotReady:
                pass
            except Exception:
                self._error("marker")
                self._hide_marker()
        except Exception:
            self._error("update")
            self.next_scan = time.monotonic() + 1.0
        finally:
            if self.ui is not None:
                try:
                    self.ui.flush()
                except Exception:
                    self._error("panel")

    def _poll_escape(self):
        down = bool(self.panel_open and ctypes.windll.user32.GetAsyncKeyState(VK_ESCAPE) & 0x8000)
        if down and not self.esc_down:
            self._escape()
        self.esc_down = down

    def _error(self, where):
        now = time.monotonic()
        if now >= self.next_error_log.get(where, 0.0):
            self.next_error_log[where] = now + 5.0
            logger.exception(f"error: {where}")


    def _scan_player(self):
        if time.monotonic() < self.next_scan:
            return
        self.next_scan = time.monotonic() + 1.0
        e = self.engine
        player = gameData.player
        if player is None:
            return
        top = e.top(player.mRootNode.lookupInt)
        sim = next((c for c in e.children(top) if e.name(c) == "Simulation-Root"), 0)
        node = next((c for c in e.children(sim) if e.name(c) == "Player"), 0) if sim else 0
        tool = model = 0
        for c in e.children(node) if node else ():
            name = e.name(c).upper().replace("\\", "/")
            if TOOL_RX.match(name):
                tool = c
            elif name.endswith("PLAYERCHARACTER/PLAYERCHARACTER.SCENE.MBIN"):
                model = c
        wanted = {t[1] for t in SLOT_TYPES.values()} | {b for cap in CAPSULES for b in cap[:2]} | {c[0] for c in BONE_CAPSULES}
        bones, stack = {}, [model] if model else []
        while stack and len(bones) < len(wanted):
            h = stack.pop()
            if e.name(h) in wanted:
                bones[e.name(h)] = h
            stack.extend(e.children(h))
        now = time.monotonic()
        for name, h in bones.items():
            old = self.prev_bones.get(name)
            if old is not None and old != h:
                self.bone_changed[name] = now
        self.prev_bones = dict(bones)
        self.bones = bones
        self._purge_cache()
        res = e.res(tool) if tool else 0
        if tool and res and (tool != self.tool or res != self.tool_res):
            self.tool, self.tool_res = tool, res
            self.tool_key = self._tool_key(tool)
            self.known[self.tool_key] = res
            if self.tool_key not in self.state["catalog"]:
                self.state["catalog"].append(self.tool_key)
            logger.info(f"in hand {self.tool_key} (resource {res})")
            self._recall_current()
            self._sync_ui()

    def _tool_key(self, tool):
        e = self.engine
        names, stack = set(), list(e.children(tool))
        while stack:
            h = stack.pop()
            name = e.name(h)
            if name.startswith("_"):
                names.add(name)
            stack.extend(e.children(h))
        kind = e.name(tool).replace("\\", "/").split("/")[-1].split(".")[0]
        digest = hashlib.md5("|".join(sorted(names)).encode()).hexdigest()[:8].upper()
        return f"{kind}#{digest}"

    def _world_scale(self, h):
        M = self.engine.absolute(h)
        return sum(math.sqrt(sum(M[r][c] ** 2 for r in range(3))) for c in range(3)) / 3

    def _measure(self):
        if not self.tool:
            return
        hand = self._world_scale(self.tool)
        if hand < 0.05:
            return
        drawn = bool(self.engine.get_activation(self.tool))
        if drawn != self.tool_drawn:
            logger.info(f"multitool {'drawn' if drawn else 'holstered'} (scale {hand:.3f})")
            self.tool_drawn = drawn
        if drawn and abs(hand - self.last_hand) < 0.002 and abs(hand - self.hand_scale) > 0.002:
            self.hand_scale = hand
        self.last_hand = hand
        if self.ui is not None and self.panel_open and self.screen == "edit" and time.monotonic() > self.next_measure:
            self.next_measure = time.monotonic() + 0.25
            copy = self.copies[self.edit_slot]["copy"]
            if self.hand_scale <= 0 or not copy:
                self.ui.set("sizeRatio", "take the tool out once")
            else:
                self.ui.set("sizeRatio", f"{self._world_scale(copy) / self.hand_scale * 100:.0f}% of the one in hand")


    def _pose(self, i):
        slot = self.prof["slots"][i]
        tpl = self.prof["templates"].setdefault(slot["type"], [None] * PRESETS)
        if tpl[0] is None:
            tpl[0] = default_pose(slot["type"])
        p = slot["preset"]
        if tpl[p] is None:
            tpl[p] = (default_pose(slot["type"], p) if p in SHIPPED_POSES.get(slot["type"], {})
                      else json.loads(json.dumps(tpl[0])))
        tpl[p].setdefault("p", [0.0, 0.0, 0.0])
        return tpl[p]

    def _update_slot(self, i, dt):
        try:
            self._update_slot_inner(i, dt)
        except BoneNotReady:
            pass

    def _update_slot_inner(self, i, dt):
        e, slot, c = self.engine, self.prof["slots"][i], self.copies[i]
        bone_name = SLOT_TYPES[slot["type"]][1]
        bone = self.bones.get(bone_name, 0)
        if bone and e.name(bone) != bone_name:
            bone, self.bones = 0, {}
            self.next_scan = 0.0
        key = self._slot_tool(i)
        res = self.known.get(key) if key else None
        if self.state["mode"] == "simple" and i != 0:
            res = None
        fitting = self.panel_open and self.screen == "edit" and self.edit_slot == i
        if key and key.startswith("STAFF") and not fitting:
            res = None
        if not (slot["show"] and res and bone):
            self._hide_copy(i)
            return
        in_hand = key == self.tool_key and self.tool_drawn
        if not c["copy"] or c["res"] != res or c["bone"] != bone or e.parent(c["copy"]) != bone:
            self._hide_copy(i)
            if time.monotonic() - self.bone_changed.get(bone_name, -1e9) < BONE_SETTLE:
                return
            try:
                copy, fresh = self._cached_copy("slot", bone, res)
            except OSError:
                self.known.pop(key, None)
                logger.info(f"slot {i + 1}: resource {key} is already unloaded, take it in hand again")
                self._sync_ui()
                return
            if copy is None:
                return
            c.update(copy=copy, bone=bone, res=res)
            self.phys[i] = self._fresh_phys()
            logger.info(f"slot {i + 1}: {key} -> {slot['type']} ({'new' if fresh else 'cached'}, "
                        f"copies in cache {len(self.cache)})")
        self._step_physics(i, dt)
        self._apply(i)
        visible = fitting or not in_hand
        if bool(e.get_activation(c["copy"])) != visible:
            e.set_activation(c["copy"], visible)

    def _current_slot(self):
        return next((i for i, s in enumerate(self.prof["slots"]) if s["tool"] == CURRENT), None)

    def _remember_current(self):
        i = self._current_slot()
        if i is not None and self.tool_key:
            slot = self.prof["slots"][i]
            self.prof["tools"][self.tool_key] = {"type": slot["type"], "preset": slot["preset"]}

    def _recall_current(self):
        i = self._current_slot()
        if i is None or not self.tool_key:
            return
        mem = self.prof["tools"].get(self.tool_key)
        if mem is None:
            self._remember_current()
            return
        self._swap(i, "type", mem["type"])
        self.prof["slots"][i]["preset"] = mem["preset"]
        self._renorm(i)

    def _slot_tool(self, i):
        tool = self.prof["slots"][i]["tool"]
        if tool != CURRENT:
            return tool
        if self.tool_key and any(s["tool"] == self.tool_key and s["show"] for s in self.prof["slots"]):
            return None
        return self.tool_key

    def _update_marker(self):
        i = self.edit_slot
        c = self.copies[i]
        if not (self.state["mode"] == "exp" and self.panel_open and self.screen == "edit" and c["copy"]):
            self._hide_marker()
            return
        e, m = self.engine, self.marker
        if not m["copy"] or m["res"] != c["res"] or m["bone"] != c["bone"] or e.parent(m["copy"]) != c["bone"]:
            self._hide_marker()
            try:
                copy, _ = self._cached_copy("marker", c["bone"], c["res"])
            except OSError:
                return
            if copy is None:
                return
            m.update(copy=copy, bone=c["bone"], res=c["res"])
        M, _, pivot, _ = self._rest(i)
        world = mul4(rotation_part(M), [[MARKER_SCALE, 0, 0, 0], [0, MARKER_SCALE, 0, 0], [0, 0, MARKER_SCALE, 0], [0, 0, 0, 1]])
        for k in range(3):
            world[k][3] = pivot[k]
        try:
            e.set_transform(m["copy"], from4(mul4(inv4(M), world)))
        except ZeroDivisionError:
            return
        if not e.get_activation(m["copy"]):
            e.set_activation(m["copy"], True)

    def _hide_marker(self):
        if self.marker["copy"]:
            self.engine.set_activation(self.marker["copy"], False)
        self.marker.update(copy=0, bone=0, res=0)

    def _cached_copy(self, role, bone, res):
        e, key = self.engine, (role, res, bone)
        copy = self.cache.get(key)
        if copy and e.parent(copy) == bone:
            return copy, False
        self.cache.pop(key, None)
        if time.monotonic() < self.next_spawn:
            return None, False
        self.next_spawn = time.monotonic() + 1.0
        copy = e.spawn(bone, res)
        self.cache[key] = copy
        return copy, True

    def _purge_cache(self):
        e = self.engine
        for key in [k for k in self.cache if not e.name(k[2])]:
            del self.cache[key]

    def _hide_copy(self, i):
        c = self.copies[i]
        if c["copy"]:
            self.engine.set_activation(c["copy"], False)
        c.update(copy=0, bone=0, res=0)


    def _rest(self, i):
        pose = self._pose(i)
        M = self.engine.absolute(self.copies[i]["bone"])
        if abs(M[0][0] * (M[1][1] * M[2][2] - M[1][2] * M[2][1])
               - M[0][1] * (M[1][0] * M[2][2] - M[1][2] * M[2][0])
               + M[0][2] * (M[1][0] * M[2][1] - M[1][1] * M[2][0])) < 1e-9:
            raise BoneNotReady
        rest = mul4(rotation_part(M), to4(matrix(pose["t"], pose["r"], pose["s"])))
        q = pose.get("p", [0.0, 0.0, 0.0])
        pivot = [rest[k][3] + sum(rest[k][j] * q[j] for j in range(3)) for k in range(3)]
        tip = [pivot[k] + rest[k][2] * self.state["phys"]["len"] for k in range(3)]
        return M, rest, pivot, tip

    def _apply(self, i):
        c, ph = self.copies[i], self.phys[i]
        M, world, pivot, rest_tip = self._rest(i)
        if ph["tip"] is not None:
            world = mul4(rotate_about(pivot, v_sub(rest_tip, pivot), v_sub(ph["tip"], pivot)), world)
        try:
            local = mul4(inv4(M), world)
        except ZeroDivisionError:
            return
        self.engine.set_transform(c["copy"], from4(local))

    def _step_physics(self, i, dt):
        p, ph = self.state["phys"], self.phys[i]
        mode = int(p["mode"]) if self.state["mode"] == "exp" else 0
        if mode == 0 or abs(p["len"]) < 0.01:
            ph["tip"] = None
            return
        M, _, pivot, rest_tip = self._rest(i)
        length = v_len(v_sub(rest_tip, pivot))
        if mode == 1:
            pushed = self._collide(i, pivot, rest_tip, length, rest_tip)
            R = rotation_part(M)
            wd = v_sub(pushed, rest_tip)
            dev = [sum(R[k][j] * wd[k] for k in range(3)) for j in range(3)]
            a = 1 - math.exp(-max(0.0, float(dt)) * COLLIDE_SMOOTH)
            ph["dev"] = v_add(ph["dev"], v_mul(v_sub(dev, ph["dev"]), a))
            tip = v_add(rest_tip, [sum(R[j][k] * ph["dev"][k] for k in range(3)) for j in range(3)])
            ph["tip"] = v_add(pivot, v_mul(v_sub(tip, pivot), length / max(v_len(v_sub(tip, pivot)), 1e-6)))
            return
        dt = max(1 / 240, min(1 / 20, float(dt)))
        if ph["tip"] is None or v_len(v_sub(ph["tip"], rest_tip)) > 2 * length:
            ph["tip"], ph["v"] = rest_tip, [0.0, 0.0, 0.0]
        prev, tip, v = ph["tip"], ph["tip"], ph["v"]
        n = max(1, math.ceil(dt * 240))
        h = dt / n
        for _ in range(n):
            acc = v_sub(v_mul(v_sub(rest_tip, tip), p["k"]), v_mul(v, p["c"]))
            v = v_add(v, v_mul(acc, h))
            tip = v_add(tip, v_mul(v, h))
        tip = v_add(pivot, v_mul(v_sub(tip, pivot), length / max(v_len(v_sub(tip, pivot)), 1e-6)))
        tip = self._limit(pivot, rest_tip, tip, length)
        tip = self._collide(i, pivot, tip, length, rest_tip)
        ph["v"] = v_mul(v_sub(tip, prev), 1 / dt)
        ph["tip"] = tip

    def _limit(self, pivot, rest_tip, tip, length):
        a, b = v_sub(rest_tip, pivot), v_sub(tip, pivot)
        cos = v_dot(a, b) / max(v_len(a) * v_len(b), 1e-9)
        limit = math.radians(self.state["phys"]["max"])
        angle = math.acos(max(-1.0, min(1.0, cos)))
        if angle <= limit or angle < 1e-6:
            return tip
        mid = v_add(rest_tip, v_mul(v_sub(tip, rest_tip), limit / angle))
        return v_add(pivot, v_mul(v_sub(mid, pivot), length / max(v_len(v_sub(mid, pivot)), 1e-6)))

    def _collide(self, i, pivot, tip, length, rest_tip):
        p, ph = self.state["phys"], self.phys[i]
        caps = []
        for a, b, r in CAPSULES:
            if a in self.bones and b in self.bones:
                A, B = (self.engine.absolute(self.bones[n]) for n in (a, b))
                caps.append(([A[k][3] for k in range(3)], [B[k][3] for k in range(3)], r * p["body"]))
        for bone, oa, ob, r in BONE_CAPSULES:
            if bone in self.bones:
                M = rotation_part(self.engine.absolute(self.bones[bone]))
                at = lambda o: [M[k][3] + sum(M[k][j] * o[j] for j in range(3)) for k in range(3)]
                caps.append((at(oa), at(ob), r * p["body"]))

        def depth(pt, a, b, r):
            q = closest_on_segment(pt, a, b)
            return r + p["rad"] - v_len(v_sub(pt, q)), q

        fractions = (0.25, 0.5, 0.75, 1.0)
        if ph["allowed"] is None or len(ph["allowed"]) != len(caps) * len(fractions):
            ph["allowed"] = {(j, f): max(0.0, depth(v_add(pivot, v_mul(v_sub(rest_tip, pivot), f)), *cap)[0])
                             for j, cap in enumerate(caps) for f in fractions}
        for _ in range(2):
            for j, (a, b, r) in enumerate(caps):
                for f in fractions:
                    pt = v_add(pivot, v_mul(v_sub(tip, pivot), f))
                    dep, q = depth(pt, a, b, r)
                    extra, d = dep - ph["allowed"][(j, f)], v_len(v_sub(pt, q))
                    if extra > 1e-4 and d > 1e-5:
                        tip = v_add(tip, v_mul(v_sub(pt, q), extra / d / f))
            tip = v_add(pivot, v_mul(v_sub(tip, pivot), length / max(v_len(v_sub(tip, pivot)), 1e-6)))
        return tip

    def _renorm(self, i=None):
        for j in range(SLOTS) if i is None else (i,):
            self.phys[j] = self._fresh_phys()


    def _ensure_ui(self):
        if self.ui is not None or self.ui_failed:
            return
        try:
            import oreo_runtime
            session = oreo_runtime.start(overlay=True, mod_dir=MOD_DIR,
                                         log_dir=os.path.join(MOD_DIR, "logs"), ui=UI_NAME)
        except Exception:
            logger.exception("Oreo runtime failed to start, no panel")
            self.ui_failed = True
            return
        ui = session.ui
        ui.load("panel", "ui/holster.rml")
        on = ui.on
        for i in range(SLOTS):
            n = i + 1
            on("click", f"c{n}_pick", lambda ev, i=i: self._toggle_list(i))
            on("click", f"c{n}_cur", lambda ev, i=i: self._pick(i, CURRENT))
            for k in range(LIST_MAX):
                on("click", f"c{n}_k{k}", lambda ev, i=i, k=k: self._pick_index(i, k))
            on("click", f"c{n}_show", lambda ev, i=i: self._set_show(i, not self.prof["slots"][i]["show"]))
            on("click", f"c{n}_typebtn", lambda ev, i=i: self._toggle_types(i))
            on("click", f"c{n}_edit", lambda ev, i=i: self._open_edit(i))
            for t in SLOT_TYPES:
                on("click", f"c{n}_t_{t}", lambda ev, i=i, t=t: self._set_type(i, t))
        for p in range(PRESETS):
            on("click", f"e_p{p + 1}", lambda ev, p=p: self._set_preset(p))
        for name in FIELDS:
            on("change", name, lambda ev, n=name: self._set_field(n, ev.value))
            on("click", f"{name}_dec", lambda ev, n=name: self._step_field(n, -1))
            on("click", f"{name}_inc", lambda ev, n=name: self._step_field(n, 1))
        on("click", "save", lambda ev: self._save())
        on("click", "match", lambda ev: self._match_hand())
        on("click", "reset", lambda ev: self._reset_preset())
        on("click", "back", lambda ev: self._go("main"))
        on("click", "settings", lambda ev: self._go("settings"))
        on("click", "to_exp", lambda ev: self._set_mode("exp"))
        on("click", "to_simple", lambda ev: self._set_mode("simple"))
        on("change", "pname", lambda ev: self._rename(ev.value))
        on("click", "close", lambda ev: self._do_panel())
        for m in range(3):
            on("click", f"phys_{m}", lambda ev, m=m: self._set_phys_mode(m))
        ui.set("sizeRatio", "")
        ui.set("status", "")
        self.ui = ui
        self._sync_ui()
        logger.info(f"panel ready, press {PANEL_KEY.upper()} to open")

    def _short(self, key):
        if not key:
            return "Empty"
        if key == CURRENT:
            return "Current tool" + (f" ({self._short(self.tool_key)})" if self.tool_key else "")
        kind, digest = key.split("#")
        return f"{(kind.replace('MULTITOOL', '') or 'MULTITOOL').capitalize()} #{digest[:4]}"

    def _card_name(self, key):
        if key == CURRENT and self.state["mode"] == "simple" and self.tool_key:
            return self._short(self.tool_key)
        return self._short(key)

    def _row(self, key, i, mark):
        if key == self.tool_key:
            status, cls = "In hand", "hand"
        elif key in self.known:
            status, cls = "", ""
        else:
            status, cls = "Take in hand once", "take"
        return self._short(key), status, cls, "staff" if key.startswith("STAFF") else "multi", mark(key)

    def _sync_ui(self):
        if self.ui is None:
            return
        ui, st = self.ui, self.state
        prof = self.prof
        ui.set("screen", self.screen)
        ui.set("inHand", self._short(self.tool_key) if self.tool_key else "nothing")
        for i, slot in enumerate(prof["slots"]):
            n = i + 1
            ui.set(f"c{n}Tool", self._card_name(slot["tool"]))
            ui.set(f"c{n}Dim", not slot["tool"] or not slot["show"])
            ui.set(f"c{n}Warn", slot["tool"] not in (None, CURRENT) and slot["tool"] not in self.known)
            ui.set(f"c{n}ShowOn", bool(slot["show"]))
            ui.set(f"c{n}TypeOpen", self.types_open == i)
            ui.set(f"c{n}TypeLabel", SLOT_TYPES[slot["type"]][0].capitalize())
            ui.set(f"c{n}Open", self.list_open == i)
            used = {s["tool"]: j for j, s in enumerate(prof["slots"]) if j != i and s["tool"]}
            mark = lambda key: f"SLOT {used[key] + 1}" if key in used else ""
            ui.set(f"c{n}VC", True)
            ui.set(f"c{n}SelC", slot["tool"] == CURRENT)
            ui.set(f"c{n}TC", mark(CURRENT))
            for k in range(LIST_MAX):
                key = st["catalog"][k] if k < len(st["catalog"]) else None
                label, status, cls, kind, tag = self._row(key, i, mark) if key else ("", "", "", "multi", "")
                ui.set(f"c{n}V{k}", bool(key))
                ui.set(f"c{n}L{k}", label)
                ui.set(f"c{n}S{k}", status)
                ui.set(f"c{n}SC{k}", cls)
                ui.set(f"c{n}K{k}", kind)
                ui.set(f"c{n}T{k}", tag)
                ui.set(f"c{n}Sel{k}", bool(key) and slot["tool"] == key)
            owner = {s["type"]: j for j, s in enumerate(prof["slots"]) if j != i}
            for t, (label, _, _) in SLOT_TYPES.items():
                ui.set(f"c{n}TL_{t}", label.capitalize())
                ui.set(f"c{n}Tag_{t}", f"SLOT {owner[t] + 1}" if t in owner and st["mode"] == "exp" else "")
                ui.set(f"c{n}On_{t}", slot["type"] == t)
        slot = prof["slots"][self.edit_slot]
        ui.set("eTitle", SLOT_TYPES[slot["type"]][0])
        ui.set("eTool", self._short(slot["tool"]))
        ui.set("ePreset", slot["preset"] + 1)
        for k, name in enumerate(self._names(slot["type"])):
            ui.set(f"pn{k + 1}", name)
        ui.set("pname", self._names(slot["type"])[slot["preset"]])
        ui.set("mode", st["mode"])
        cs = self._current_slot()
        ui.set("sIdx", (cs or 0) + 1)
        for name in FIELDS:
            ui.set(name, self._get(name))
        ui.set("physMode", int(st["phys"]["mode"]))

    def _set_mode(self, mode):
        self.state["mode"] = mode
        self.edit_slot = 0
        self.types_open = self.list_open = None
        self._renorm()
        self._recall_current()
        self._sync_ui()

    def _names(self, t):
        return self.prof["names"].setdefault(t, list(SHIPPED_NAMES.get(t, GENERIC_NAMES)))

    def _rename(self, value):
        slot = self.prof["slots"][self.edit_slot]
        name = str(value or "").strip()[:20]
        if slot["preset"] and name:
            self._names(slot["type"])[slot["preset"]] = name
            self._sync_ui()

    def _go(self, screen):
        self.screen = screen
        self.ui.set("status", "")
        self._sync_ui()

    def _do_panel(self):
        if self.ui is None:
            return
        self.panel_open = not self.panel_open
        if self.panel_open:
            self.screen = "main"
            self._sync_ui()
            self.ui.show("panel", modal=True)
        else:
            self.ui.hide("panel")

    def _toggle_list(self, i):
        self.list_open = None if self.list_open == i else i
        self.types_open = None
        self._sync_ui()

    def _pick_index(self, i, k):
        if k < len(self.state["catalog"]):
            self._pick(i, self.state["catalog"][k])

    def _swap(self, i, field, value):
        slots = self.prof["slots"]
        for j, slot in enumerate(slots):
            if j != i and value is not None and slot[field] == value:
                slot[field] = slots[i][field]
                self._renorm(j)
        slots[i][field] = value

    def _pick(self, i, key):
        self._swap(i, "tool", key)
        self.list_open = None
        self._renorm(i)
        self._sync_ui()

    def _hang(self, i, key=None):
        key = key or self.tool_key
        if not key:
            self.ui.set("status", "take a multitool in hand first")
            return
        for slot in self.prof["slots"]:
            if slot["tool"] == key:
                slot["tool"] = None
        self.prof["slots"][i]["tool"] = key
        self.ui.set("status", f"slot {i + 1}: {self._short(key)}")
        self._sync_ui()

    def _set_show(self, i, value):
        self.prof["slots"][i]["show"] = bool(value)
        self._sync_ui()

    def _toggle_types(self, i):
        self.types_open = None if self.types_open == i else i
        self.list_open = None
        self._sync_ui()

    def _set_type(self, i, t):
        self._swap(i, "type", t)
        if self.prof["slots"][i]["tool"] == CURRENT:
            self._remember_current()
        self.types_open = None
        self._renorm(i)
        self._sync_ui()

    def _open_edit(self, i):
        self.edit_slot = i
        self._go("edit")

    def _set_preset(self, p):
        self.prof["slots"][self.edit_slot]["preset"] = p
        if self.prof["slots"][self.edit_slot]["tool"] == CURRENT:
            self._remember_current()
        self._renorm(self.edit_slot)
        self._sync_ui()

    def _target(self, name):
        where, (key, idx) = FIELDS[name][0]
        return (self._pose(self.edit_slot) if where == "pose" else self.state["phys"]), key, idx

    def _get(self, name):
        d, key, idx = self._target(name)
        return d[key][idx] if idx is not None else d[key]

    def _put(self, name, value):
        d, key, idx = self._target(name)
        lo, hi = FIELDS[name][1]
        value = round(min(hi, max(lo, float(value))), 4)
        if idx is None:
            d[key] = value
        else:
            d[key][idx] = value
        self._renorm(self.edit_slot if FIELDS[name][0][0] == "pose" else None)

    def _set_field(self, name, value):
        if isinstance(value, (int, float)):
            self._put(name, value)

    def _step_field(self, name, sign):
        self._put(name, self._get(name) + sign * FIELDS[name][2])
        self.ui.set(name, self._get(name))

    def _match_hand(self):
        copy = self.copies[self.edit_slot]["copy"]
        if self.hand_scale <= 0 or not copy:
            self.ui.set("status", "take the tool out once so its size can be read")
            return
        world = self._world_scale(copy)
        if world > 1e-6:
            self._put("sc", self._pose(self.edit_slot)["s"] * self.hand_scale / world)
            self.ui.set("sc", self._get("sc"))
            self.ui.set("status", "size matched to the one in hand")

    def _reset_preset(self):
        slot = self.prof["slots"][self.edit_slot]
        self.prof["templates"].setdefault(slot["type"], [None] * PRESETS)[slot["preset"]] = default_pose(slot["type"], slot["preset"])
        self._renorm(self.edit_slot)
        self._sync_ui()
        self.ui.set("status", "preset reset to default")

    def _set_phys_mode(self, mode):
        self.state["phys"]["mode"] = float(mode)
        self._renorm()
        self._sync_ui()

    def _escape(self):
        if self.screen == "main":
            self._do_panel()
        else:
            self._go("main")

    def _save(self):
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(self.state, f, indent=2)
        self.ui.set("status", "saved")
        logger.info("saved")
