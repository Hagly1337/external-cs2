# -*- coding: utf-8 -*-
"""
CS2 External ESP (wallhack) на Python.

Экстернал: читает память cs2.exe через ReadProcessMemory (pymem),
рисует боксы / хп-бары / ники в прозрачном клик-сквозь окне поверх игры.
В игру ничего не пишется и не инжектится.

Оффсеты при каждом запуске качаются с a2x/cs2-dumper (последние дампы),
при отсутствии сети берётся кэш из offsets_cache/.
"""

import ctypes
import json
import math
import os
import struct
import sys
import time
import urllib.request

user32 = ctypes.windll.user32
try:
    user32.SetProcessDPIAware()
except Exception:
    pass

import win32con
import win32gui
import win32ui
import win32process
import win32event
import win32api

try:
    import pymem
    import pymem.exception
    import pymem.process
except ImportError:
    print("[!] Нет зависимостей. Установи: pip install pymem pywin32")
    sys.exit(1)

try:
    import radar
except Exception:
    radar = None

# ============================ НАСТРОЙКИ ============================
DRAW_BOX = True
DRAW_HEALTHBAR = True
DRAW_NAME = True
DRAW_ARMOR = True
DRAW_DISTANCE = True
DRAW_FLAGS = True
DRAW_SKELETON = True
DRAW_WEAPON = True       # оружие в руках + патроны
DRAW_DUCK_FLAG = True    # метка "DUCK" когда игрок присел
DRAW_LOOKLINE = True     # линия: куда игрок смотрит
DRAW_SNAPLINES = False
ENEMY_ONLY = True          # False = рисовать и тиммейтов (синим)
FPS_CAP = 60
PLAYER_HEIGHT = 72.0       # фолбэк-рост, если коллизия не читается
LOOK_LEN = 400.0           # длина линии взгляда в юнитах
ORIGIN_FIELD = "m_vecAbsOrigin"
ENTITY_STRIDE = 112  # Verified against local build; see Vesta entity_directory.

RADAR_ENABLED = False
RADAR_HOST = "127.0.0.1"   # "0.0.0.0" — чтобы отдавать сокомандникам по сети/LAN
RADAR_PORT = 8123
RADAR_STREAM_HOST = "rphelpers.store"  # бинарный TCP-стрим на VPS (порт 8445)
RADAR_STREAM_PORT = 8445
RADAR_PUSH_KEY = "5d1c6ad3270ff74d15ea6952"
# пустой RADAR_STREAM_HOST = не стримить.
# Ссылка для сокомандников:
#   https://rphelpers.store:8444/radar?k=f3daf20c39a18ed33713c743

COLOR_ENEMY = (255, 70, 70)
COLOR_TEAM = (80, 160, 255)
COLOR_NAME = (255, 255, 255)
COLOR_STATUS = (0, 255, 120)
COLOR_SNAPLINE = (255, 230, 0)
COLOR_SKELETON = (255, 255, 255)
COLOR_FLAGS = (255, 220, 120)
COLOR_LOOK = (130, 220, 255)

WEAPON_NAMES = {
    1: "Deagle", 2: "Dualies", 3: "Five-Seven", 4: "Glock", 7: "AK-47",
    8: "AUG", 9: "AWP", 10: "FAMAS", 11: "G3SG1", 13: "Galil", 14: "M249",
    16: "M4A4", 17: "MAC-10", 19: "P90", 23: "MP5-SD", 24: "UMP-45",
    25: "XM1014", 26: "Bizon", 27: "MAG-7", 28: "Negev", 29: "Sawed-Off",
    30: "Tec-9", 31: "Zeus", 32: "P2000", 33: "MP7", 34: "MP9", 35: "Nova",
    36: "P250", 38: "SCAR-20", 39: "SG553", 40: "SSG08", 41: "Knife",
    42: "Knife", 43: "Flash", 44: "HE", 45: "Smoke", 46: "Molotov",
    47: "Decoy", 48: "Incendiary", 49: "C4", 59: "Knife", 60: "M4A1-S",
    61: "USP-S", 63: "CZ75", 64: "R8", 500: "Bayonet", 505: "Flip",
    506: "Gut", 507: "Karambit", 508: "M9 Bayonet", 509: "Huntsman",
    512: "Falchion", 514: "Bowie", 515: "Butterfly", 516: "Shadow Daggers",
    519: "Ursus", 520: "Navaja", 522: "Stiletto", 525: "Skeleton",
}
NO_AMMO_IDS = {31, 41, 42, 43, 44, 45, 46, 47, 48, 49, 59}

# Суставы CS2 (индексы из vesta joint_id), кость = 32 байта, позиция в первых 12
J_PELVIS, J_SPINE0, J_SPINE1, J_SPINE2, J_SPINE3, J_NECK, J_HEAD = 1, 2, 3, 4, 5, 6, 7
J_CLAV_L, J_SHLD_L, J_ELBW_L, J_HAND_L = 8, 9, 10, 11
J_CLAV_R, J_SHLD_R, J_ELBW_R, J_HAND_R = 12, 13, 14, 15
J_HIP_L, J_KNEE_L, J_FOOT_L = 17, 18, 19
J_HIP_R, J_KNEE_R, J_FOOT_R = 20, 21, 22

SKELETON_LINKS = [
    (J_HEAD, J_NECK), (J_NECK, J_SPINE3), (J_SPINE3, J_SPINE2),
    (J_SPINE2, J_SPINE1), (J_SPINE1, J_SPINE0), (J_SPINE0, J_PELVIS),
    (J_NECK, J_CLAV_L), (J_CLAV_L, J_SHLD_L), (J_SHLD_L, J_ELBW_L), (J_ELBW_L, J_HAND_L),
    (J_NECK, J_CLAV_R), (J_CLAV_R, J_SHLD_R), (J_SHLD_R, J_ELBW_R), (J_ELBW_R, J_HAND_R),
    (J_PELVIS, J_HIP_L), (J_HIP_L, J_KNEE_L), (J_KNEE_L, J_FOOT_L),
    (J_PELVIS, J_HIP_R), (J_HIP_R, J_KNEE_R), (J_KNEE_R, J_FOOT_R),
]
BONE_FIRST, BONE_LAST = 1, 22  # кости читаем одним блоком, 1..22

OFFSETS_BASE = "https://raw.githubusercontent.com/a2x/cs2-dumper/main/output/"
CACHE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "offsets_cache")

# ============================ ОФФСЕТЫ ============================
def load_json_cached(name):
    cache = os.path.join(CACHE_DIR, name)
    try:
        req = urllib.request.Request(
            OFFSETS_BASE + name, headers={"User-Agent": "cs2-pyesp"})
        with urllib.request.urlopen(req, timeout=10) as r:
            data = json.loads(r.read().decode("utf-8"))
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(cache, "w", encoding="utf-8") as f:
            json.dump(data, f)
        print("[offsets] Свежие дампы скачаны с a2x/cs2-dumper: %s" % name)
        return data
    except Exception as e:
        print("[offsets] Скачать не удалось (%s), беру кэш %s" % (e, name))
        with open(cache, "r", encoding="utf-8") as f:
            return json.load(f)


class Offsets:
    def __init__(self):
        off = load_json_cached("offsets.json")
        cli = load_json_cached("client_dll.json")
        self.dw = off["client.dll"] if isinstance(off.get("client.dll"), dict) else off
        dll = cli.get("client.dll", cli)
        self.classes = dll.get("classes", dll)

        self.dwEntityList = self._dw("dwEntityList")
        self.dwViewMatrix = self._dw("dwViewMatrix")
        self.dwLocalPlayerPawn = self._dw("dwLocalPlayerPawn")
        self.dwLocalPlayerController = self._dw("dwLocalPlayerController")
        self.dwGlobalVars = self._dw("dwGlobalVars")

        self.m_iHealth = self.field("C_BaseEntity", "m_iHealth")
        self.m_iTeamNum = self.field("C_BaseEntity", "m_iTeamNum")
        self.m_lifeState = self.field("C_BaseEntity", "m_lifeState")
        self.m_pGameSceneNode = self.field("C_BaseEntity", "m_pGameSceneNode")
        self.m_vecOrigin = self.field("CGameSceneNode", ORIGIN_FIELD)
        self.m_modelState = self.field("CSkeletonInstance", "m_modelState")
        self.m_pCollision = self.field("C_BaseModelEntity", "m_Collision")
        self.m_vecMins = self.field("CCollisionProperty", "m_vecMins")
        self.m_vecMaxs = self.field("CCollisionProperty", "m_vecMaxs")
        self.m_pMovementServices = self.field("C_BasePlayerPawn", "m_pMovementServices")
        self.m_bDucked = self.field("CCSPlayer_MovementServices", "m_bDucked")
        self.m_angEyeAngles = self.field("C_CSPlayerPawn", "m_angEyeAngles")
        self.m_pWeaponServices = self.field("C_BasePlayerPawn", "m_pWeaponServices")
        self.m_hActiveWeapon = self.field("CPlayer_WeaponServices", "m_hActiveWeapon")
        self.m_AttributeManager = self.field("C_EconEntity", "m_AttributeManager")
        self.m_Item = self.field("C_AttributeContainer", "m_Item")
        self.m_iItemDefinitionIndex = self.field("C_EconItemView", "m_iItemDefinitionIndex")
        self.m_iClip1 = self.field("C_BasePlayerWeapon", "m_iClip1")
        self.m_hPlayerPawn = self.field("CCSPlayerController", "m_hPlayerPawn")
        self.m_iszPlayerName = self.field("CCSPlayerController", "m_iszPlayerName")
        self.optional = {}
        for key in ("m_ArmorValue", "m_bIsScoped", "m_bIsDefusing"):
            try:
                self.optional[key] = self.field("C_CSPlayerPawn", key)
            except RuntimeError:
                print("[offsets] Optional field unavailable: " + key)

    def _dw(self, name):
        if name not in self.dw:
            raise RuntimeError("в offsets.json нет %s — формат дампа изменился?" % name)
        return self.dw[name]

    def field(self, class_name, field_name):
        seen = set()
        c = class_name
        while c and c not in seen:
            seen.add(c)
            cls = self.classes.get(c)
            if not isinstance(cls, dict):
                break
            fields = cls.get("fields", {})
            if isinstance(fields, dict) and field_name in fields:
                return fields[field_name]
            parent = cls.get("parent")
            if isinstance(parent, str) and parent:
                c = parent
            else:
                break
        raise RuntimeError("поле %s.%s не найдено в дампе" % (class_name, field_name))

# ============================ ЧТЕНИЕ ПАМЯТИ ============================
class Reader:
    def __init__(self, pm, client_base):
        self.pm = pm
        self.client = client_base

    def u64(self, addr):
        return self.pm.read_ulonglong(addr)

    def u32(self, addr):
        return self.pm.read_uint(addr)

    def u16(self, addr):
        return self.pm.read_ushort(addr)

    def i32(self, addr):
        return self.pm.read_int(addr)

    def u8(self, addr):
        return self.pm.read_bytes(addr, 1)[0]

    def block(self, addr, n):
        return self.pm.read_bytes(addr, n)

    def vec3(self, addr):
        return struct.unpack("<3f", self.pm.read_bytes(addr, 12))

    def viewmatrix(self, addr):
        return struct.unpack("<16f", self.pm.read_bytes(addr, 64))

    def cstr(self, addr, n=32):
        return self.pm.read_bytes(addr, n).split(b"\x00")[0].decode("utf-8", "replace")

# entity list: 512 entries per chunk, 0x70 bytes per identity.
def entity_at(rd, entity_list, index):
    entry = rd.u64(entity_list + 8 * (index >> 9) + 16)
    if not entry:
        return 0
    return rd.u64(entry + (index & 0x1FF) * ENTITY_STRIDE)


def collect_entities(rd, off):
    ents = []
    entity_list = rd.u64(rd.client + off.dwEntityList)
    if not entity_list:
        return ents, None, None
    view = rd.viewmatrix(rd.client + off.dwViewMatrix)

    local_pawn = 0
    local_team = None
    local_origin = None
    local_yaw = None
    try:
        local_pawn = rd.u64(rd.client + off.dwLocalPlayerPawn)
        if local_pawn:
            local_team = rd.u8(local_pawn + off.m_iTeamNum)
            local_node = rd.u64(local_pawn + off.m_pGameSceneNode)
            if local_node:
                local_origin = rd.vec3(local_node + off.m_vecOrigin)
            local_yaw = rd.vec3(local_pawn + off.m_angEyeAngles)[1]
    except Exception:
        pass
    local = None
    if local_origin is not None:
        local = {"x": local_origin[0], "y": local_origin[1],
                 "team": local_team, "yaw": local_yaw}

    for i in range(1, 65):
        try:
            controller = entity_at(rd, entity_list, i)
            if not controller:
                continue
            handle = rd.u32(controller + off.m_hPlayerPawn)
            if handle in (0, 0xFFFFFFFF):
                continue
            pawn = entity_at(rd, entity_list, handle & 0x7FFF)
            if not pawn or pawn == local_pawn:
                continue
            hp = rd.i32(pawn + off.m_iHealth)
            if hp <= 0 or hp > 200:
                continue
            if rd.u8(pawn + off.m_lifeState) != 0:
                continue
            team = rd.u8(pawn + off.m_iTeamNum)
            # ENEMY_ONLY фильтруется при отрисовке, а не здесь —
            # радару нужны все игроки, включая тиммейтов
            node = rd.u64(pawn + off.m_pGameSceneNode)
            if not node:
                continue
            origin = rd.vec3(node + off.m_vecOrigin)
            if not all(math.isfinite(v) for v in origin) or sum(map(abs, origin)) > 200000:
                continue

            # присед: m_pMovementServices -> m_bDucked (формула из vesta)
            ducked = False
            try:
                ms = rd.u64(pawn + off.m_pMovementServices)
                if ms:
                    ducked = rd.u8(ms + off.m_bDucked) != 0
            except Exception:
                pass

            # коллизия (m_Collision встроен в пешку): бокс по факту,
            # при приседе maxs.z падает с ~72 до ~54 сам
            mins = maxs = None
            try:
                mins = rd.vec3(pawn + off.m_pCollision + off.m_vecMins)
                maxs = rd.vec3(pawn + off.m_pCollision + off.m_vecMaxs)
                if (not all(math.isfinite(v) for v in mins)
                        or not all(math.isfinite(v) for v in maxs)
                        or maxs[2] <= 1 or maxs[2] > 100):
                    mins = maxs = None
            except Exception:
                mins = maxs = None

            # кости: bone_cache = *(m_modelState + 0x80), запись кости 32 байта
            bones = None
            try:
                bone_cache = rd.u64(node + off.m_modelState + 0x80)
                if bone_cache:
                    data = rd.block(bone_cache + 32 * BONE_FIRST,
                                    32 * (BONE_LAST - BONE_FIRST + 1))
                    bones = {}
                    for j in range(BONE_FIRST, BONE_LAST + 1):
                        p = struct.unpack_from("<3f", data, (j - BONE_FIRST) * 32)
                        if all(math.isfinite(v) for v in p) and sum(map(abs, p)) < 200000:
                            bones[j] = p
                    if not bones:
                        bones = None
            except Exception:
                bones = None

            # взгляд: m_angEyeAngles (pitch, yaw, roll) -> forward
            eye_pos = fwd = None
            yaw = None
            try:
                pitch, yaw, _roll = rd.vec3(pawn + off.m_angEyeAngles)
                p_, y_ = math.radians(pitch), math.radians(yaw)
                fwd = (math.cos(p_) * math.cos(y_),
                       math.cos(p_) * math.sin(y_),
                       -math.sin(p_))
                eye_pos = (bones or {}).get(J_HEAD) or (origin[0], origin[1], origin[2] + 60.0)
            except Exception:
                pass

            # оружие в руках: m_pWeaponServices -> m_hActiveWeapon -> item index
            weapon = ""
            try:
                ws = rd.u64(pawn + off.m_pWeaponServices)
                if ws:
                    wh = rd.u32(ws + off.m_hActiveWeapon)
                    if wh and wh != 0xFFFFFFFF:
                        wptr = entity_at(rd, entity_list, wh & 0x7FFF)
                        if wptr:
                            widx = rd.u16(wptr + off.m_AttributeManager + off.m_Item
                                          + off.m_iItemDefinitionIndex)
                            weapon = WEAPON_NAMES.get(widx, "w%d" % widx)
                            if widx not in NO_AMMO_IDS and widx < 500:
                                clip = rd.i32(wptr + off.m_iClip1)
                                if 0 <= clip < 400:
                                    weapon = "%s %d" % (weapon, clip)
            except Exception:
                pass

            name = ""
            try:
                name = rd.cstr(controller + off.m_iszPlayerName)
            except Exception:
                pass
            extra = {}
            for key, label, enabled, read in (
                    ("m_ArmorValue", "armor", DRAW_ARMOR, rd.i32),
                    ("m_bIsScoped", "scoped", DRAW_FLAGS, rd.u8),
                    ("m_bIsDefusing", "defusing", DRAW_FLAGS, rd.u8)):
                if enabled and key in off.optional:
                    try:
                        extra[label] = read(pawn + off.optional[key])
                    except (pymem.exception.MemoryReadError, OSError):
                        pass
            distance = math.dist(local_origin, origin) * 0.0254 if local_origin else None
            ents.append({"hp": hp, "team": team, "origin": origin, "name": name,
                         "distance": distance,
                         "friendly": local_team is not None and team == local_team,
                         "mins": mins, "maxs": maxs, "bones": bones,
                         "ducked": ducked, "eye_pos": eye_pos, "fwd": fwd,
                         "yaw": yaw, "weapon": weapon, **extra})
        except Exception:
            continue
    return ents, view, local


def world_to_screen(pos, mat, w, h):
    if len(pos) != 3 or len(mat) != 16 or w <= 0 or h <= 0:
        return None
    if not all(math.isfinite(v) for v in (*pos, *mat)):
        return None
    x = mat[0] * pos[0] + mat[1] * pos[1] + mat[2] * pos[2] + mat[3]
    y = mat[4] * pos[0] + mat[5] * pos[1] + mat[6] * pos[2] + mat[7]
    c = mat[12] * pos[0] + mat[13] * pos[1] + mat[14] * pos[2] + mat[15]
    if c < 0.1 or not all(math.isfinite(v) for v in (x, y, c)):
        return None
    return (int(w * 0.5 + (x / c) * (w * 0.5)),
            int(h * 0.5 - (y / c) * (h * 0.5)))


def project_box(e, view, w, h):
    """Бокс из 8 углов коллизии — сам сжимается, когда игрок присел.
    Без коллизии — фолбэк на фиксированную высоту."""
    mins = e.get("mins")
    maxs = e.get("maxs")
    origin = e["origin"]
    if not mins or not maxs:
        top = 54.0 if e.get("ducked") else PLAYER_HEIGHT
        mins, maxs = (-16.0, -16.0, 0.0), (16.0, 16.0, top)
    pts = []
    for sx in (mins[0], maxs[0]):
        for sy in (mins[1], maxs[1]):
            for sz in (mins[2], maxs[2]):
                p = world_to_screen((origin[0] + sx, origin[1] + sy, origin[2] + sz),
                                    view, w, h)
                if p is None:
                    return None
                pts.append(p)
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    x1, y1, x2, y2 = min(xs), min(ys), max(xs), max(ys)
    if x2 - x1 < 3 or y2 - y1 < 6:
        return None
    return (x1, y1, x2, y2)

# ============================ ОВЕРЛЕЙ ============================
def rgb(c):
    return c[0] | (c[1] << 8) | (c[2] << 16)


def hp_color(v):
    return (int(255 * (100 - v) / 100), int(255 * v / 100), 0)


class Overlay:
    def __init__(self, w, h):
        self.w, self.h = w, h
        wc = win32gui.WNDCLASS()
        wc.hInstance = win32gui.GetModuleHandle(None)
        wc.lpszClassName = "Cs2PyEsp"
        wc.lpfnWndProc = win32gui.DefWindowProc
        atom = win32gui.RegisterClass(wc)
        ex = (win32con.WS_EX_LAYERED | win32con.WS_EX_TRANSPARENT |
              win32con.WS_EX_TOPMOST | win32con.WS_EX_NOACTIVATE |
              win32con.WS_EX_TOOLWINDOW)
        self.hwnd = win32gui.CreateWindowEx(
            ex, atom, "", win32con.WS_POPUP, 0, 0, w, h,
            None, None, wc.hInstance, None)
        # чёрный цвет = прозрачный (colorkey): заливка чёрным даёт окно-невидимку
        win32gui.SetLayeredWindowAttributes(self.hwnd, 0, 0, win32con.LWA_COLORKEY)
        win32gui.ShowWindow(self.hwnd, win32con.SW_SHOWNOACTIVATE)

        self.dc_win = win32gui.GetWindowDC(self.hwnd)
        self.dc = win32ui.CreateDCFromHandle(self.dc_win)
        self.mem = self.dc.CreateCompatibleDC()
        self.bmp = win32ui.CreateBitmap()
        self.bmp.CreateCompatibleBitmap(self.dc, w, h)
        self.old_bitmap = self.mem.SelectObject(self.bmp)

        self.pen_enemy = win32ui.CreatePen(win32con.PS_SOLID, 2, rgb(COLOR_ENEMY))
        self.pen_team = win32ui.CreatePen(win32con.PS_SOLID, 2, rgb(COLOR_TEAM))
        self.pen_snap = win32ui.CreatePen(win32con.PS_SOLID, 1, rgb(COLOR_SNAPLINE))
        self.pen_skel = win32ui.CreatePen(win32con.PS_SOLID, 1, rgb(COLOR_SKELETON))
        self.pen_look = win32ui.CreatePen(win32con.PS_SOLID, 1, rgb(COLOR_LOOK))
        self.pen_black = win32ui.CreatePen(win32con.PS_SOLID, 1, 0)
        self.brush_black = win32ui.CreateBrush(win32con.BS_SOLID, 0, 0)
        self.brushes_hp = [win32ui.CreateBrush(win32con.BS_SOLID, rgb(hp_color(v)), 0)
                           for v in range(0, 101, 10)]
        self.brush_armor = win32ui.CreateBrush(win32con.BS_SOLID, rgb((80, 160, 255)), 0)
        self.brush_armor_bg = win32ui.CreateBrush(win32con.BS_SOLID, rgb((20, 20, 20)), 0)
        self.font = win32ui.CreateFont({"name": "Arial", "height": 13,
                                        "weight": win32con.FW_BOLD,
                                        "quality": win32con.NONANTIALIASED_QUALITY})
        self.frame_i = 0
        self.window_rect = None
        self.previous_regions = []
        self.regions = []
        self.mem.FillSolidRect((0, 0, w, h), 0)

    def close(self):
        self.mem.SelectObject(self.old_bitmap)
        self.mem.DeleteDC()
        win32gui.DeleteObject(self.bmp.GetHandle())
        win32gui.ReleaseDC(self.hwnd, self.dc_win)
        win32gui.DestroyWindow(self.hwnd)
        win32gui.UnregisterClass("Cs2PyEsp", win32gui.GetModuleHandle(None))

    def follow_window(self, hwnd):
        # always-on: прячем только если окно игры исчезло/свёрнуто,
        # потеря фокуса оверлей НЕ скрывает
        if not hwnd or not win32gui.IsWindow(hwnd) or win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(self.hwnd, win32con.SW_HIDE)
            return False
        left, top, right, bottom = win32gui.GetClientRect(hwnd)
        w, h = right - left, bottom - top
        if w <= 0 or h <= 0:
            win32gui.ShowWindow(self.hwnd, win32con.SW_HIDE)
            return False
        x, y = win32gui.ClientToScreen(hwnd, (left, top))
        if (w, h) != (self.w, self.h):
            bitmap = win32ui.CreateBitmap()
            bitmap.CreateCompatibleBitmap(self.dc, w, h)
            self.mem.SelectObject(bitmap)
            win32gui.DeleteObject(self.bmp.GetHandle())
            self.bmp = bitmap
            self.w, self.h = w, h
            self.mem.FillSolidRect((0, 0, w, h), 0)
            self.previous_regions = [(0, 0, w, h)]
        rect = (x, y, w, h)
        if rect != self.window_rect or not win32gui.IsWindowVisible(self.hwnd):
            win32gui.SetWindowPos(self.hwnd, win32con.HWND_TOPMOST, x, y, w, h,
                                  win32con.SWP_NOACTIVATE | win32con.SWP_SHOWWINDOW)
            self.window_rect = rect
        return True

    def begin(self):
        for rect in self.previous_regions:
            self.mem.FillSolidRect(rect, 0)
        self.regions = []

    def dirty(self, rect):
        left, top, right, bottom = rect
        clipped = (max(0, int(left)), max(0, int(top)),
                   min(self.w, int(right)), min(self.h, int(bottom)))
        if clipped[2] > clipped[0] and clipped[3] > clipped[1]:
            self.regions.append(clipped)

    def end(self):
        for left, top, right, bottom in set(self.previous_regions + self.regions):
            self.dc.BitBlt((left, top), (right-left, bottom-top), self.mem,
                           (left, top), win32con.SRCCOPY)
        self.previous_regions = self.regions[:]

    def keep_topmost(self):
        win32gui.SetWindowPos(self.hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
                              win32con.SWP_NOMOVE | win32con.SWP_NOSIZE |
                              win32con.SWP_NOACTIVATE)

    def draw_esp(self, ents, view):
        self.mem.SelectObject(self.font)
        self.mem.SetBkMode(win32con.TRANSPARENT)
        for e in ents:
            if ENEMY_ONLY and e.get("friendly", False):
                continue
            origin = e["origin"]
            feet = world_to_screen(origin, view, self.w, self.h)
            pen = self.pen_team if e.get("friendly", False) else self.pen_enemy

            # бокс из коллизии — честная высота при приседе
            box = project_box(e, view, self.w, self.h)
            if box and (box[2] < 0 or box[0] >= self.w
                        or box[3] < 0 or box[1] >= self.h):
                box = None

            if box and DRAW_BOX:
                x1, y1, x2, y2 = box
                self.mem.SelectObject(pen)
                self.mem.SelectObject(self.brush_black)
                self.mem.Rectangle(box)
                self.dirty((x1 - 10, y1 - 2, x2 + 3, y2 + 3))

            # скелет
            if DRAW_SKELETON and e.get("bones"):
                self.mem.SelectObject(pen)
                pts = []
                for a, b in SKELETON_LINKS:
                    pa = e["bones"].get(a)
                    pb = e["bones"].get(b)
                    if not pa or not pb:
                        continue
                    sa = world_to_screen(pa, view, self.w, self.h)
                    sb = world_to_screen(pb, view, self.w, self.h)
                    if sa and sb:
                        self.mem.MoveTo(sa)
                        self.mem.LineTo(sb)
                        pts += [sa, sb]
                if pts:
                    xs = [p[0] for p in pts]
                    ys = [p[1] for p in pts]
                    self.dirty((min(xs) - 2, min(ys) - 2, max(xs) + 3, max(ys) + 3))

            # линия взгляда
            if DRAW_LOOKLINE and e.get("eye_pos") and e.get("fwd"):
                ep, fwd = e["eye_pos"], e["fwd"]
                a = world_to_screen(ep, view, self.w, self.h)
                b = world_to_screen((ep[0] + fwd[0] * LOOK_LEN,
                                     ep[1] + fwd[1] * LOOK_LEN,
                                     ep[2] + fwd[2] * LOOK_LEN), view, self.w, self.h)
                if a and b:
                    self.mem.SelectObject(self.pen_look)
                    self.mem.MoveTo(a)
                    self.mem.LineTo(b)
                    self.dirty((min(a[0], b[0]) - 2, min(a[1], b[1]) - 2,
                                max(a[0], b[0]) + 3, max(a[1], b[1]) + 3))

            if box:
                x1, y1, x2, y2 = box
                if DRAW_HEALTHBAR:
                    hp = max(0, min(100, e["hp"]))
                    bar_h = int((y2 - y1) * hp / 100)
                    self.mem.SelectObject(self.pen_black)
                    self.mem.SelectObject(self.brushes_hp[min(hp // 10, 10)])
                    self.mem.Rectangle((x1 - 8, y2 - bar_h, x1 - 4, y2))

                if DRAW_NAME and e["name"]:
                    self.mem.SetTextColor(rgb(COLOR_NAME))
                    self.mem.TextOut(x1, max(0, y1 - 18), e["name"])
                    tw, th = self.mem.GetTextExtent(e["name"])
                    self.dirty((x1, max(0, y1-18), x1+tw+2, max(0, y1-18)+th+2))

                armor = e.get("armor")
                if DRAW_ARMOR and armor is not None:
                    armor = max(0, min(100, armor))
                    self.mem.SelectObject(self.pen_black)
                    self.mem.SelectObject(self.brush_armor_bg)
                    self.mem.Rectangle((x1 - 14, y1, x1 - 10, y2))
                    self.mem.SelectObject(self.brush_armor)
                    a_h = int((y2 - y1) * armor / 100)
                    self.mem.Rectangle((x1 - 14, y2 - a_h, x1 - 10, y2))
                    self.dirty((x1 - 16, y1 - 2, x1 - 8, y2 + 3))

                labels = []
                if DRAW_WEAPON and e.get("weapon"):
                    labels.append(e["weapon"])
                if DRAW_DUCK_FLAG and e.get("ducked"):
                    labels.append("DUCK")
                if labels:
                    txt = " | ".join(labels)
                    ly = y2 + 2
                    self.mem.SetTextColor(rgb(COLOR_FLAGS))
                    self.mem.TextOut(x1, ly, txt)
                    tw, th = self.mem.GetTextExtent(txt)
                    self.dirty((x1, ly, x1 + tw + 2, ly + th + 2))

            if DRAW_SNAPLINES and feet:
                self.mem.SelectObject(self.pen_snap)
                self.mem.MoveTo((self.w // 2, self.h - 1))
                self.mem.LineTo(feet)
                self.dirty((min(self.w//2, feet[0])-2, min(self.h-1, feet[1])-2,
                            max(self.w//2, feet[0])+3, max(self.h-1, feet[1])+3))

    def draw_status(self, targets, fps):
        self.mem.SelectObject(self.font)
        self.mem.SetBkMode(win32con.TRANSPARENT)
        self.mem.SetTextColor(rgb(COLOR_STATUS))
        self.mem.TextOut(10, 10, "CS2 pyESP | targets: %d | overlay fps: %d" % (targets, fps))
        self.dirty((8, 8, 410, 30))

# ============================ ЛОГИКА ============================
def norm_map(s):
    s = s.replace("\\", "/").split("/")[-1]
    for suf in (".vpk", ".vpdb", ".bsp"):
        if s.endswith(suf):
            s = s[:-len(suf)]
    return s


class MapNameReader:
    """Имя карты из GlobalVars (эвристика из vesta workers.cpp:0x150..0x1F0)."""

    def __init__(self):
        self.cached_off = 0
        self.last_probe = 0.0
        self.last_value = ""

    @staticmethod
    def _valid(s):
        if len(s) < 3:
            return False
        if not all(c.isalnum() or c in "_-./\\" for c in s):
            return False
        return "_" in s or "/" in s

    def _try(self, rd, addr):
        try:
            ptr = rd.u64(addr)
            if ptr < 0x10000:
                return ""
            s = rd.cstr(ptr, 64)
            return s if self._valid(s) else ""
        except Exception:
            return ""

    def read(self, rd, gv):
        if self.cached_off:
            s = self._try(rd, gv + self.cached_off)
            if s:
                return s
            self.cached_off = 0
        now = time.time()
        if now - self.last_probe < 1.0:
            return ""
        self.last_probe = now
        for off in range(0x150, 0x1F1, 8):
            s = self._try(rd, gv + off)
            if s:
                self.cached_off = off
                print("[map] global_vars+0x%X -> %s" % (off, s))
                return s
        return ""


def attach_to_cs2():
    while True:
        try:
            pm = pymem.Pymem("cs2.exe")
            print("[+] cs2.exe найден, pid %d" % pm.process_id)
            return pm
        except Exception:
            print("[ ] Жду cs2.exe (запусти игру)...")
            time.sleep(2)


def wait_client_dll(pm):
    for _ in range(60):
        try:
            mod = pymem.process.module_from_name(pm.process_handle, "client.dll")
        except Exception:
            return None
        if mod:
            return mod
        time.sleep(1)
    return None


def session(off, ov, pm):
    mod = wait_client_dll(pm)
    if mod is None:
        return False
    rd = Reader(pm, mod.lpBaseOfDll)
    print("[+] client.dll @ 0x%X — работаю. Выход: Ctrl+C" % rd.client)
    fps, frames, fps_t = 0, 0, time.perf_counter()
    last_error = None
    game_window = None
    map_reader = MapNameReader()
    stats = []
    stats_time = time.perf_counter()
    while True:
        t0 = time.perf_counter()
        win32gui.PumpWaitingMessages()
        if not game_window or not win32gui.IsWindow(game_window):
            windows = []
            def find_window(hwnd, unused):
                if (win32gui.IsWindowVisible(hwnd)
                        and win32process.GetWindowThreadProcessId(hwnd)[1] == pm.process_id
                        and win32gui.GetWindow(hwnd, win32con.GW_OWNER) == 0):
                    windows.append(hwnd)
            win32gui.EnumWindows(find_window, None)
            game_window = windows[0] if windows else None
        visible = ov.follow_window(game_window)
        if not visible:
            if win32event.WaitForSingleObject(pm.process_handle, 0) == win32con.WAIT_OBJECT_0:
                return False
            time.sleep(0.2)
            continue

        read_start = time.perf_counter()
        targets = 0
        ents, view = [], None
        try:
            ents, view, local = collect_entities(rd, off)
            targets = len(ents)
            map_name = ""
            try:
                gv = rd.u64(rd.client + off.dwGlobalVars)
                if gv:
                    map_name = norm_map(map_reader.read(rd, gv))
            except Exception:
                pass
            if radar is not None:
                radar.update([{"x": e["origin"][0], "y": e["origin"][1],
                               "team": e["team"], "hp": e["hp"],
                               "name": e["name"], "weapon": e["weapon"],
                               "ducked": e["ducked"], "yaw": e["yaw"]}
                              for e in ents], local, map_name)
        except (pymem.exception.MemoryReadError, pymem.exception.ProcessError,
                pymem.exception.WinAPIError, OSError):
            print("[!] Процесс cs2.exe умер/закрылся — переподключаюсь")
            return False
        except Exception as exc:
            error = "%s: %s" % (type(exc).__name__, exc)
            if error != last_error:
                print("[!] Ошибка кадра: %s" % error)
                last_error = error
            targets = 0

        read_end = time.perf_counter()
        ov.begin()
        if view:
            ov.draw_esp(ents, view)

        frames += 1
        if time.perf_counter() - fps_t >= 1.0:
            fps, frames, fps_t = frames, 0, time.perf_counter()
        if visible:
            ov.draw_status(targets, fps)
            ov.end()
        stats.append(((read_end-read_start)*1000, (time.perf_counter()-read_end)*1000))
        if time.perf_counter()-stats_time >= 10:
            print("[perf] targets=%d read_avg_ms=%.3f render_avg_ms=%.3f" % (
                targets, sum(v[0] for v in stats)/len(stats), sum(v[1] for v in stats)/len(stats)))
            stats.clear()
            stats_time = time.perf_counter()

        dt = time.perf_counter() - t0
        frame_interval = 1.0 / (FPS_CAP if targets else min(FPS_CAP, 10))
        if dt < frame_interval:
            time.sleep(frame_interval - dt)


def run():
    if struct.calcsize("P") != 8:
        raise SystemExit("Нужен 64-битный Python.")
    if FPS_CAP <= 0:
        raise SystemExit("FPS_CAP должен быть положительным.")
    print("== CS2 pyESP (external) ==")
    try:
        off = Offsets()
    except Exception as e:
        print("[!] Не удалось загрузить оффсеты: %s" % e)
        sys.exit(1)
    print("[offsets] dwEntityList=0x%X  dwViewMatrix=0x%X  dwLocalPlayerPawn=0x%X"
          % (off.dwEntityList, off.dwViewMatrix, off.dwLocalPlayerPawn))

    w = user32.GetSystemMetrics(0)
    h = user32.GetSystemMetrics(1)
    # GDI по умолчанию: layered+transparent = клик-сквозь в чужие процессы.
    # GPU (--gpu) клики НЕ пропускает (HTTRANSPARENT работает только внутри
    # одного потока) — только для тестов рендера.
    if "--gpu" in sys.argv:
        from gpu_overlay import GPUOverlay
        ov = GPUOverlay(w, h, sys.modules[__name__])
    else:
        ov = Overlay(w, h)
    print("[+] Оверлей готов %dx%d (прозрачный, поверх всех окон)" % (w, h))
    if RADAR_ENABLED and radar is not None:
        try:
            radar.start(RADAR_HOST, RADAR_PORT)
            print("[radar] http://localhost:%d  |  LAN: http://%s:%d"
                  % (RADAR_PORT, radar.lan_ip(), RADAR_PORT))
            if RADAR_HOST != "0.0.0.0":
                print('[radar] для раздачи сокомандникам поставь RADAR_HOST="0.0.0.0"')
        except Exception as e:
            print("[!] радар не поднялся: %s" % e)
        if RADAR_STREAM_HOST:
            radar.start_stream(RADAR_STREAM_HOST, RADAR_STREAM_PORT, RADAR_PUSH_KEY)
            print("[radar] бинарный стрим на VPS включён (%s:%d)"
                  % (RADAR_STREAM_HOST, RADAR_STREAM_PORT))
    print("[!] Игра должна быть в оконном или безрамочном режиме (не эксклюзивный фуллскрин).")

    try:
        while True:
            pm = attach_to_cs2()
            try:
                session(off, ov, pm)
            finally:
                win32gui.ShowWindow(ov.hwnd, win32con.SW_HIDE)
                try:
                    pm.close_process()
                except Exception:
                    pass
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nВыход")
    finally:
        ov.close()


def main():
    # Shared by relative/absolute invocations: only one overlay per Windows session.
    guard = win32event.CreateMutex(None, False, "Local\\CS2PyEsp.SingleInstance")
    try:
        if win32api.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
            print("ESP is already running. Press End in the existing instance to close it.")
            return
        run()
    finally:
        win32api.CloseHandle(guard)


if __name__ == "__main__":
    main()
