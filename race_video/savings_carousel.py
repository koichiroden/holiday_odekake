# -*- coding: utf-8 -*-
"""
おでかけ節約カルーセル(Instagram / X / Threads 向け 1080x1350 の画像5枚 + 投稿文)。

「A駅からおでかけ先(舞浜・川越・横浜・新宿・渋谷…)まで、どのルートで行くと
いくら・何分 節約できるか」を、身近なモノ・時間に置き換えて見せる。

    python3 -m race_video.cli configs/savings_tokorozawa_maihama.json
    → output/<slug>/01_cover.png 02_route.png 03_money.png 04_time.png 05_summary.png caption.txt

構成: Top(地図に発着の2点・目的地のイラスト) → ルート(どこで何線に乗るか) → お金 → 時間 → まとめ

config の要点(詳しくは README の「おでかけ節約カルーセル」と configs/savings_*.json):
    "video_type": "savings",
    "destination_theme": "maihama" | "yokohama" | "kawagoe" | "shinjuku" | "shibuya" | "haneda" | "narita" | "skytree"
                                            | 汎用: "sea"(海) | "mountain"(山) | "mall"(ショッピングモール) | "onsen"(温泉街) | "shrine"(神社) | "temple"(お寺) | "none"
    "basis": "round_trip" | "one_way"     … 節約額・節約時間を往復で見せるか片道か
    "trips_per_year": 6                   … まとめの「年◯回行くなら」
    "arrive_by": "09:00"                  … ◯時に着くなら各ルートは何時に出る?(ルート図・まとめ・投稿文に出る)
                                            ルートごとに "depart_time" / "arrive_time"(時刻表の値。24:10 のようにも書ける)、
                                            または "schedule_text"(例: 当日は無理)を書ける。無ければ所要時間からの目安(「ごろ」)
    "hourly_wage": 1280                   … 時間を時給換算する(0 / 省略で出さない)
    "routes": [ 最大3本 {
        "name": "有楽町線ルート", "short_name": "有楽町線経由", "color": [0,167,219],
        "legs": [ {"line": "西武池袋線", "from": "所沢", "to": "練馬", "train": "Fライナー"},
                  {"line": "西武有楽町線", "from": "練馬", "to": "小竹向原", "through": true},  # 直通=乗換に数えない
                  {"line": "有楽町線", "from": "小竹向原", "to": "新木場"}, ... ],
        "fare": {"ic": 854, "ticket": 870}, "time_min": 76,   # 価格が変わるなら "from": true で「¥854〜」、"max": 1200 で「¥854〜¥1,200」
        "transfers": 2,           # 省略時は legs から自動(through でない継ぎ目の数)
        "transfer_walk_min": 9,   # 乗換の徒歩(任意。表示用)
        "note": "…" } ],
    "money_items": [ {"label": "ペットボトル飲料", "price": 200, "unit": "本", "icon": "bottle"} ... ],
    "time_items":  [ {"label": "トイレの行列", "min": 5, "unit": "回", "icon": "toilet"} ... ],
    "money_tips" / "time_tips": ["知見の一文", ...]
"""
import math
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

from .common import font, compute_projection_box, PolyPath
from .geo import project
from .render_base import load_all_lines, load_land_polygons, draw_land_and_sea, draw_glow_polyline
from .route_builder import load_line_geoms, build_route
from .station_db import get_station_lonlat
from .route_builder import resolve_line_name
from .carousel import (
    W, H, PAD, BG_TOP, BG_BOTTOM, INK, INK2, MUTED, GOLD, CARD, CARD_LINE,
    new_canvas, rrect, text_w, wrap_jp, fit_font, yen, fmt_min, header, route_tag, badge, chrome, callout,
    COVER_SX, COVER_TOP, COVER_BOTTOM,
)

MAX_ROUTES = 3
GREEN = (80, 215, 140)
DARK = (15, 18, 30)

THEMES = {
    "maihama":  {"label": "舞浜",  "accent": (255, 140, 200)},
    "yokohama": {"label": "横浜",  "accent": (90, 200, 255)},
    "kawagoe":  {"label": "川越",  "accent": (255, 170, 80)},
    "shinjuku": {"label": "新宿",  "accent": (150, 170, 255)},
    "shibuya":  {"label": "渋谷",  "accent": (120, 230, 200)},
    "haneda":   {"label": "羽田空港", "accent": (110, 200, 255)},
    "narita":   {"label": "成田空港", "accent": (255, 185, 110)},
    "skytree":  {"label": "スカイツリー", "accent": (175, 150, 255)},
    # 汎用(どこでも使える行楽地のタイプ)
    "sea":      {"label": "海の行楽地", "accent": (80, 210, 235)},
    "mountain": {"label": "山の行楽地", "accent": (120, 220, 130)},
    "mall":     {"label": "ショッピングモール", "accent": (255, 200, 90)},
    "onsen":    {"label": "温泉街", "accent": (255, 150, 115)},
    "shrine":   {"label": "神社", "accent": (255, 95, 85)},
    "temple":   {"label": "お寺", "accent": (235, 195, 110)},
}


# ================================================================ データの組み立て

def leg_stations(legs):
    """legs → build_route 用の stations と、乗換駅の名前リスト"""
    st = []
    transfers = []
    for i, lg in enumerate(legs):
        a = {"line": lg["line"], "name": lg["from"]}
        b = {"line": lg["line"], "name": lg["to"]}
        if not st or st[-1]["name"] != a["name"]:
            st.append(a)
        st.append(b)
        if i > 0 and not lg.get("through"):
            prev = legs[i - 1]["to"]
            transfers.append(prev if prev == lg["from"] else f"{prev}→{lg['from']}")
    return st, transfers


def resolve_routes(config):
    routes = []
    for i, rc in enumerate(config["routes"][:MAX_ROUTES]):
        r = dict(rc)
        r["key"] = rc.get("key") or f"route_{i}"
        r["name"] = rc.get("name") or r["key"]
        r["short_name"] = rc.get("short_name") or r["name"]
        r["color"] = tuple(rc.get("color") or (220, 220, 220))
        legs = rc.get("legs") or []
        _st, tr = leg_stations(legs) if legs else ([], [])
        r["_transfer_names"] = tr
        if rc.get("transfers") is None:
            r["transfers"] = len(tr)
        fare = rc.get("fare")
        if isinstance(fare, (int, float)):
            fare = {"ic": fare}
        r["fare"] = fare or {}
        routes.append(r)
    if len(config["routes"]) > MAX_ROUTES:
        print(f"[warn] ルートは最大{MAX_ROUTES}本です。4本目以降は使いません")
    return routes


def fare_of(r):
    return (r.get("fare") or {}).get("ic")


def fare_varies(r):
    f = r.get("fare") or {}
    return bool(f.get("from") or f.get("max"))


def fare_txt(r):
    """運賃の表示。価格が変わるものは「¥2,800〜」、上限もあれば「¥2,800〜¥5,000」"""
    f = r.get("fare") or {}
    ic = f.get("ic")
    if ic is None:
        return "-"
    if f.get("max"):
        return f"{yen(ic)}〜{yen(f['max'])}"
    return yen(ic) + ("〜" if f.get("from") else "")


def saving_wording(best, worst):
    """価格が変わるルートを比べるときの言い方: (前につける語, 後ろにつける語, 補足)。
    安い方だけが「〜」→ 最大◯円、高い方だけが「〜」→ ◯円〜、両方 → 最安値どうしの比較と注記"""
    bf = any(fare_varies(r) for r in best)
    wf = any(fare_varies(r) for r in worst)
    if bf and wf:
        return "", "", "最安値どうしで"
    if bf:
        return "最大 ", "", None
    if wf:
        return "", "〜", None
    return "", "", None


def basis_mult(config):
    return 2 if config.get("basis", "round_trip") == "round_trip" else 1


def basis_label(config):
    return "往復" if basis_mult(config) == 2 else "片道"


def extremes(routes, getter):
    """(最良ルートたち, 最悪ルートたち, 差)。差が無ければ None"""
    vals = [(r, getter(r)) for r in routes if getter(r) is not None]
    if len(vals) < 2:
        return None
    lo = min(v for _r, v in vals)
    hi = max(v for _r, v in vals)
    if hi == lo:
        return None
    return [r for r, v in vals if v == lo], [r for r, v in vals if v == hi], hi - lo


def names(rs):
    return "・".join(r["short_name"] for r in rs)


def fmt_count(x):
    """3.49 → '約3.5', 9.2 → '9', 0.8 → '0.8'"""
    if x >= 10:
        return f"{math.floor(x):,}"
    v = math.floor(x * 2) / 2
    if v >= 1:
        s = f"{v:g}"
        return s if v == x else "約" + s
    return f"{x:.1f}"


def equivalents(diff, items, key, rep="回"):
    """差(円 or 分)を items に置き換えた [(item, 表示文字列(大), 小さい説明)]"""
    out = []
    for it in items:
        unit_v = it.get(key)
        if not unit_v:
            continue
        cnt = diff / unit_v
        unit = it.get("unit", "回")
        if cnt >= 1:
            big = f"{fmt_count(cnt)}{unit}分"
            small = None
        elif cnt >= 0.6:
            big = f"ほぼ1{unit}分"
            small = None
        else:
            need = math.ceil(unit_v / diff)
            big = f"{need}{rep}で1{unit}分"
            small = "積み重ねると"
        out.append((it, big, small))
    return out


# ================================================================ アイコン(すべて自作の簡易図形)

def _ic_bottle(d, cx, cy, s, col):
    w = s * 0.36
    d.rounded_rectangle((cx - w / 2, cy - s * 0.22, cx + w / 2, cy + s * 0.46), radius=w * 0.3, fill=col)
    d.polygon([(cx - w / 2, cy - s * 0.18), (cx - w * 0.2, cy - s * 0.34), (cx + w * 0.2, cy - s * 0.34), (cx + w / 2, cy - s * 0.18)], fill=col)
    d.rectangle((cx - w * 0.22, cy - s * 0.46, cx + w * 0.22, cy - s * 0.34), fill=col)
    d.rectangle((cx - w / 2, cy - s * 0.02, cx + w / 2, cy + s * 0.2), fill=DARK + (200,))


def _ic_snack(d, cx, cy, s, col):
    # 紙カップに入ったスナック(ポテト風)
    for k in range(5):
        x = cx - s * 0.2 + k * s * 0.1
        d.rounded_rectangle((x - s * 0.035, cy - s * 0.42 + (k % 2) * s * 0.06, x + s * 0.035, cy), radius=4, fill=(255, 220, 120))
    d.polygon([(cx - s * 0.3, cy - s * 0.12), (cx + s * 0.3, cy - s * 0.12), (cx + s * 0.22, cy + s * 0.45), (cx - s * 0.22, cy + s * 0.45)], fill=col)
    d.ellipse((cx - s * 0.08, cy + s * 0.08, cx + s * 0.08, cy + s * 0.24), fill=DARK + (200,))


def _ic_ticket(d, cx, cy, s, col):
    w, h = s * 0.9, s * 0.52
    box = (cx - w / 2, cy - h / 2, cx + w / 2, cy + h / 2)
    d.rounded_rectangle(box, radius=10, fill=col)
    r = h * 0.16
    for x in (box[0], box[2]):
        d.ellipse((x - r, cy - r, x + r, cy + r), fill=CARD)
    for k in range(5):
        y = box[1] + 8 + k * (h - 16) / 4
        d.ellipse((cx + w * 0.18 - 3, y - 3, cx + w * 0.18 + 3, y + 3), fill=CARD)
    d.text((cx - w * 0.12, cy), "★", font=font(int(s * 0.3), "black"), fill=DARK, anchor="mm")


def _ic_toilet(d, cx, cy, s, col):
    # 男女のピクトグラム
    for dx, skirt in ((-s * 0.18, False), (s * 0.18, True)):
        x = cx + dx
        d.ellipse((x - s * 0.08, cy - s * 0.42, x + s * 0.08, cy - s * 0.26), fill=col)
        if skirt:
            d.polygon([(x - s * 0.06, cy - s * 0.22), (x + s * 0.06, cy - s * 0.22), (x + s * 0.15, cy + s * 0.12), (x - s * 0.15, cy + s * 0.12)], fill=col)
        else:
            d.rounded_rectangle((x - s * 0.1, cy - s * 0.22, x + s * 0.1, cy + s * 0.12), radius=6, fill=col)
        d.rectangle((x - s * 0.07, cy + s * 0.1, x - s * 0.02, cy + s * 0.44), fill=col)
        d.rectangle((x + s * 0.02, cy + s * 0.1, x + s * 0.07, cy + s * 0.44), fill=col)
    d.line([(cx, cy - s * 0.44), (cx, cy + s * 0.44)], fill=col, width=3)


def _ic_cafe(d, cx, cy, s, col):
    d.rounded_rectangle((cx - s * 0.28, cy - s * 0.1, cx + s * 0.18, cy + s * 0.34), radius=12, fill=col)
    d.arc((cx + s * 0.06, cy - s * 0.04, cx + s * 0.4, cy + s * 0.24), 270, 90, fill=col, width=max(3, int(s * 0.07)))
    d.rectangle((cx - s * 0.38, cy + s * 0.36, cx + s * 0.34, cy + s * 0.42), fill=col)
    for k in (-1, 0, 1):
        x = cx - s * 0.05 + k * s * 0.12
        d.arc((x - s * 0.06, cy - s * 0.42, x + s * 0.06, cy - s * 0.26), 90, 270, fill=col, width=3)
        d.arc((x - s * 0.06, cy - s * 0.28, x + s * 0.06, cy - s * 0.14), 270, 90, fill=col, width=3)


def _ic_clock(d, cx, cy, s, col):
    r = s * 0.4
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=col, width=max(4, int(s * 0.08)))
    d.line([(cx, cy), (cx, cy - r * 0.6)], fill=col, width=max(4, int(s * 0.07)))
    d.line([(cx, cy), (cx + r * 0.45, cy + r * 0.2)], fill=col, width=max(4, int(s * 0.07)))


def _ic_yen(d, cx, cy, s, col):
    r = s * 0.4
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=col)
    d.text((cx, cy + 2), "¥", font=font(int(s * 0.5), "black"), fill=DARK, anchor="mm")


def _ic_walk(d, cx, cy, s, col):
    d.ellipse((cx - s * 0.07, cy - s * 0.44, cx + s * 0.09, cy - s * 0.28), fill=col)
    w = max(4, int(s * 0.08))
    d.line([(cx, cy - s * 0.24), (cx - s * 0.04, cy + s * 0.08)], fill=col, width=w)
    d.line([(cx - s * 0.04, cy + s * 0.08), (cx - s * 0.2, cy + s * 0.42)], fill=col, width=w)
    d.line([(cx - s * 0.04, cy + s * 0.08), (cx + s * 0.16, cy + s * 0.42)], fill=col, width=w)
    d.line([(cx - s * 0.02, cy - s * 0.16), (cx - s * 0.2, cy + s * 0.02)], fill=col, width=w)
    d.line([(cx - s * 0.02, cy - s * 0.16), (cx + s * 0.18, cy - s * 0.02)], fill=col, width=w)


def _ic_bulb(d, cx, cy, s, col):
    d.ellipse((cx - s * 0.28, cy - s * 0.42, cx + s * 0.28, cy + s * 0.14), fill=col)
    d.rectangle((cx - s * 0.13, cy + s * 0.1, cx + s * 0.13, cy + s * 0.3), fill=col)
    d.rectangle((cx - s * 0.1, cy + s * 0.32, cx + s * 0.1, cy + s * 0.4), fill=col)
    d.text((cx, cy - s * 0.14), "!", font=font(int(s * 0.36), "black"), fill=DARK, anchor="mm")


def _ic_train(d, cx, cy, s, col):
    d.rounded_rectangle((cx - s * 0.3, cy - s * 0.4, cx + s * 0.3, cy + s * 0.3), radius=int(s * 0.12), fill=col)
    d.rectangle((cx - s * 0.22, cy - s * 0.3, cx + s * 0.22, cy - s * 0.04), fill=DARK + (220,))
    for x in (cx - s * 0.16, cx + s * 0.16):
        d.ellipse((x - s * 0.05, cy + s * 0.1, x + s * 0.05, cy + s * 0.2), fill=DARK + (220,))
    d.line([(cx - s * 0.2, cy + s * 0.3), (cx - s * 0.32, cy + s * 0.46)], fill=col, width=4)
    d.line([(cx + s * 0.2, cy + s * 0.3), (cx + s * 0.32, cy + s * 0.46)], fill=col, width=4)


ICONS = {"bottle": _ic_bottle, "snack": _ic_snack, "ticket": _ic_ticket, "toilet": _ic_toilet,
         "cafe": _ic_cafe, "clock": _ic_clock, "yen": _ic_yen, "walk": _ic_walk, "bulb": _ic_bulb,
         "train": _ic_train}


def draw_icon(d, name, cx, cy, s, col):
    (ICONS.get(name) or _ic_yen)(d, cx, cy, s, col)


# ================================================================ 目的地のイラスト(すべて自作の汎用シルエット)

def _firework(d, cx, cy, r, col, alpha=200, rays=18):
    for k in range(rays):
        a = 2 * math.pi * k / rays
        x0, y0 = cx + math.cos(a) * r * 0.25, cy + math.sin(a) * r * 0.25
        x1, y1 = cx + math.cos(a) * r, cy + math.sin(a) * r
        d.line([(x0, y0), (x1, y1)], fill=col + (alpha,), width=3)
        d.ellipse((x1 - 4, y1 - 4, x1 + 4, y1 + 4), fill=col + (alpha,))
        x2, y2 = cx + math.cos(a + math.pi / rays) * r * 0.62, cy + math.sin(a + math.pi / rays) * r * 0.62
        d.ellipse((x2 - 3, y2 - 3, x2 + 3, y2 + 3), fill=(255, 255, 255, int(alpha * 0.8)))
    d.ellipse((cx - 5, cy - 5, cx + 5, cy + 5), fill=(255, 255, 255, alpha))


def _castle(d, x, base_y, s, fill, line):
    """汎用のおとぎ話風の城(特定の建物を模したものではない)"""
    def tower(cx, w, top, roof_h):
        d.rectangle((cx - w / 2, top, cx + w / 2, base_y), fill=fill, outline=line, width=2)
        d.polygon([(cx - w / 2 - w * 0.12, top), (cx + w / 2 + w * 0.12, top), (cx, top - roof_h)], fill=line)
        d.line([(cx, top - roof_h), (cx, top - roof_h - s * 0.06)], fill=line, width=2)
        d.polygon([(cx, top - roof_h - s * 0.06), (cx + s * 0.05, top - roof_h - s * 0.045), (cx, top - roof_h - s * 0.03)], fill=line)
        d.rounded_rectangle((cx - w * 0.14, top + w * 0.3, cx + w * 0.14, top + w * 0.65), radius=int(w * 0.14), fill=line)
    # 城壁
    wall_top = base_y - s * 0.28
    d.rectangle((x + s * 0.02, wall_top, x + s * 0.98, base_y), fill=fill, outline=line, width=2)
    n = 9
    for k in range(n):
        cx = x + s * 0.02 + (k + 0.5) * s * 0.96 / n
        d.rectangle((cx - s * 0.025, wall_top - s * 0.04, cx + s * 0.025, wall_top), fill=fill, outline=line, width=2)
    tower(x + s * 0.1, s * 0.14, base_y - s * 0.5, s * 0.16)
    tower(x + s * 0.9, s * 0.14, base_y - s * 0.5, s * 0.16)
    tower(x + s * 0.3, s * 0.12, base_y - s * 0.66, s * 0.18)
    tower(x + s * 0.7, s * 0.12, base_y - s * 0.66, s * 0.18)
    # 中央の本館と主塔
    d.rectangle((x + s * 0.36, base_y - s * 0.56, x + s * 0.64, base_y), fill=fill, outline=line, width=2)
    tower(x + s * 0.5, s * 0.16, base_y - s * 0.86, s * 0.26)
    # 門
    d.rounded_rectangle((x + s * 0.44, base_y - s * 0.2, x + s * 0.56, base_y + 2), radius=int(s * 0.06), fill=line)


def _ferris_wheel(d, cx, cy, r, line, alpha=230):
    col = line + (alpha,)
    d.line([(cx, cy), (cx - r * 0.55, cy + r * 1.25)], fill=col, width=5)
    d.line([(cx, cy), (cx + r * 0.55, cy + r * 1.25)], fill=col, width=5)
    d.ellipse((cx - r, cy - r, cx + r, cy + r), outline=col, width=5)
    d.ellipse((cx - r * 0.92, cy - r * 0.92, cx + r * 0.92, cy + r * 0.92), outline=line + (alpha // 2,), width=2)
    n = 24
    for k in range(n):
        a = 2 * math.pi * k / n
        d.line([(cx, cy), (cx + math.cos(a) * r, cy + math.sin(a) * r)], fill=line + (alpha // 2,), width=2)
        gx, gy = cx + math.cos(a) * r, cy + math.sin(a) * r
        d.rounded_rectangle((gx - 7, gy - 4, gx + 7, gy + 12), radius=4, fill=col)
    d.ellipse((cx - r * 0.16, cy - r * 0.16, cx + r * 0.16, cy + r * 0.16), fill=col)


def _tower_building(d, cx, base_y, w, h, fill, line):
    """先細りの超高層ビル(汎用)"""
    top_w = w * 0.62
    d.polygon([(cx - w / 2, base_y), (cx + w / 2, base_y), (cx + top_w / 2, base_y - h), (cx - top_w / 2, base_y - h)],
              fill=fill, outline=line)
    d.rectangle((cx - top_w * 0.36, base_y - h - h * 0.05, cx + top_w * 0.36, base_y - h), fill=line)
    for k in range(1, 7):
        x = cx - w / 2 + k * w / 7
        xt = cx - top_w / 2 + k * top_w / 7
        d.line([(x, base_y), (xt, base_y - h)], fill=line, width=1)
    for k in range(1, 12):
        y = base_y - k * h / 12
        f = (base_y - y) / h
        hw = (w / 2) * (1 - f) + (top_w / 2) * f
        d.line([(cx - hw, y), (cx + hw, y)], fill=line, width=1)


def _kura(d, x, base_y, w, h, fill, line, roof):
    """蔵造りの町家(汎用): 重い瓦屋根 + 白黒の壁"""
    d.rectangle((x, base_y - h, x + w, base_y), fill=fill, outline=line, width=2)
    rh = h * 0.42
    d.polygon([(x - w * 0.1, base_y - h), (x + w * 1.1, base_y - h), (x + w * 0.88, base_y - h - rh), (x + w * 0.12, base_y - h - rh)], fill=roof)
    d.rectangle((x + w * 0.05, base_y - h - rh - h * 0.08, x + w * 0.95, base_y - h - rh), fill=roof)
    for xx in (x + w * 0.02, x + w * 0.92):
        d.rectangle((xx, base_y - h - rh - h * 0.2, xx + w * 0.06, base_y - h - rh), fill=roof)
    d.rectangle((x + w * 0.15, base_y - h * 0.62, x + w * 0.4, base_y - h * 0.3), fill=roof)
    d.rectangle((x + w * 0.58, base_y - h * 0.5, x + w * 0.85, base_y), fill=roof)
    d.line([(x, base_y - h * 0.18), (x + w, base_y - h * 0.18)], fill=line, width=2)


def _bell_tower(d, cx, base_y, s, fill, line, roof):
    """木造の鐘つき櫓(汎用)"""
    for k, (w, h) in enumerate(((0.34, 0.34), (0.26, 0.26), (0.2, 0.22))):
        y1 = base_y - sum(hh for _ww, hh in ((0.34, 0.34), (0.26, 0.26), (0.2, 0.22))[:k]) * s
        y0 = y1 - h * s
        d.rectangle((cx - w * s / 2, y0, cx + w * s / 2, y1), fill=fill, outline=line, width=2)
        d.polygon([(cx - w * s * 0.8, y0 + s * 0.02), (cx + w * s * 0.8, y0 + s * 0.02), (cx + w * s * 0.4, y0 - s * 0.05), (cx - w * s * 0.4, y0 - s * 0.05)], fill=roof)
        if k == 2:
            d.ellipse((cx - s * 0.05, y0 + s * 0.05, cx + s * 0.05, y0 + s * 0.16), fill=roof)
            d.polygon([(cx - w * s * 0.6, y0 - s * 0.05), (cx + w * s * 0.6, y0 - s * 0.05), (cx, y0 - s * 0.2)], fill=roof)
    for xx in (-0.12, 0.12):
        d.line([(cx + xx * s, base_y - s * 0.34), (cx + xx * s, base_y)], fill=line, width=3)


def _skyline(d, x, base_y, w, h, fill, line, seed=1):
    import random
    rnd = random.Random(seed)
    xx = x
    while xx < x + w:
        bw = rnd.uniform(0.07, 0.14) * w
        bh = rnd.uniform(0.35, 1.0) * h
        d.rectangle((xx, base_y - bh, xx + bw, base_y), fill=fill, outline=line, width=2)
        for wy in range(int(base_y - bh + 12), int(base_y - 6), 16):
            for wx in range(int(xx + 8), int(xx + bw - 8), 14):
                if rnd.random() < 0.55:
                    d.rectangle((wx, wy, wx + 5, wy + 7), fill=line)
        xx += bw + rnd.uniform(4, 12)


def _mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def _rot(pts, cx, cy, deg):
    a = math.radians(deg)
    ca, sa = math.cos(a), math.sin(a)
    return [(cx + x * ca - y * sa, cy + x * sa + y * ca) for x, y in pts]


def _airplane(d, cx, cy, s, fill, line, deg=-18):
    """横から見た旅客機(汎用)。s=全長"""
    L = s
    body = [(-0.5 * L, -0.03 * L), (0.36 * L, -0.045 * L), (0.46 * L, -0.03 * L), (0.5 * L, 0), (0.46 * L, 0.03 * L),
            (0.36 * L, 0.045 * L), (-0.42 * L, 0.04 * L), (-0.5 * L, 0.01 * L)]
    wing = [(0.06 * L, 0.02 * L), (-0.16 * L, 0.26 * L), (-0.24 * L, 0.26 * L), (-0.1 * L, 0.02 * L)]
    wing2 = [(0.02 * L, -0.02 * L), (-0.12 * L, -0.12 * L), (-0.18 * L, -0.12 * L), (-0.1 * L, -0.02 * L)]
    tail = [(-0.36 * L, -0.03 * L), (-0.46 * L, -0.2 * L), (-0.52 * L, -0.2 * L), (-0.5 * L, -0.03 * L)]
    stab = [(-0.4 * L, 0.02 * L), (-0.5 * L, 0.09 * L), (-0.54 * L, 0.09 * L), (-0.5 * L, 0.01 * L)]
    eng = [(-0.02 * L, 0.1 * L), (-0.12 * L, 0.1 * L), (-0.12 * L, 0.15 * L), (-0.02 * L, 0.15 * L)]
    for poly in (wing2, tail, stab, body, wing, eng):
        d.polygon(_rot(poly, cx, cy, deg), fill=fill, outline=line)
        d.line(_rot(poly + [poly[0]], cx, cy, deg), fill=line, width=2)
    for k in range(9):
        x = 0.3 * L - k * 0.075 * L
        wx, wy = _rot([(x, -0.012 * L)], cx, cy, deg)[0]
        d.ellipse((wx - 3, wy - 3, wx + 3, wy + 3), fill=line)


def _control_tower(d, cx, base_y, s, fill, line):
    """空港の管制塔(汎用)"""
    w = s * 0.12
    d.polygon([(cx - w * 0.7, base_y), (cx + w * 0.7, base_y), (cx + w * 0.42, base_y - s * 0.62), (cx - w * 0.42, base_y - s * 0.62)], fill=fill, outline=line)
    cab_y = base_y - s * 0.62
    d.polygon([(cx - w * 0.6, cab_y), (cx + w * 0.6, cab_y), (cx + w * 1.05, cab_y - s * 0.12), (cx - w * 1.05, cab_y - s * 0.12)], fill=fill, outline=line)
    for k in range(5):
        x = cx - w * 0.85 + k * w * 0.42
        d.line([(x, cab_y - s * 0.02), (x + w * 0.05, cab_y - s * 0.1)], fill=line, width=3)
    d.rectangle((cx - w * 1.1, cab_y - s * 0.15, cx + w * 1.1, cab_y - s * 0.12), fill=line)
    d.line([(cx, cab_y - s * 0.15), (cx, cab_y - s * 0.26)], fill=line, width=3)
    d.ellipse((cx - 5, cab_y - s * 0.27 - 5, cx + 5, cab_y - s * 0.27 + 5), fill=line)
    for k in range(1, 6):
        y = base_y - k * s * 0.1
        d.line([(cx - w * 0.3, y), (cx + w * 0.3, y)], fill=line, width=1)


def _runway(d, x0, x1, y, line, lights=True):
    d.line([(x0, y), (x1, y)], fill=line, width=3)
    for x in range(int(x0 + 10), int(x1 - 10), 34):
        d.line([(x, y + 12), (x + 16, y + 12)], fill=line, width=3)
    if lights:
        for x in range(int(x0), int(x1), 22):
            d.ellipse((x - 3, y + 22, x + 3, y + 28), fill=line)


def _lattice_tower(d, cx, base_y, s, fill, line):
    """細長い格子の電波塔(汎用)。足元は三角、上へ行くほど細く、展望台が2段"""
    top = base_y - s * 0.97
    def half(y):                       # 高さ y での半幅
        t = (base_y - y) / (base_y - top)
        return s * (0.13 * (1 - t) ** 1.6 + 0.012)
    ys = [base_y - k * (base_y - top) / 40 for k in range(41)]
    left = [(cx - half(y), y) for y in ys]
    right = [(cx + half(y), y) for y in ys][::-1]
    d.polygon(left + right, fill=fill, outline=line)
    d.line(left, fill=line, width=2)
    d.line(right, fill=line, width=2)
    for k in range(0, 34):              # 斜めの格子
        y0, y1 = ys[k], ys[k + 1]
        d.line([(cx - half(y0), y0), (cx + half(y1), y1)], fill=line, width=1)
        d.line([(cx + half(y0), y0), (cx - half(y1), y1)], fill=line, width=1)
    for frac, hw, hh in ((0.55, 0.075, 0.04), (0.73, 0.05, 0.03)):   # 展望台
        y = base_y - s * frac
        d.rounded_rectangle((cx - s * hw, y - s * hh, cx + s * hw, y + s * hh), radius=int(s * hh), fill=fill, outline=line, width=3)
        for k in range(7):
            x = cx - s * hw * 0.8 + k * s * hw * 1.6 / 6
            d.line([(x, y - s * hh * 0.4), (x, y + s * hh * 0.4)], fill=line, width=2)
    d.line([(cx, ys[34]), (cx, top - s * 0.02)], fill=line, width=3)


def _waves(d, x0, x1, y, amp, line, n=3, gap=18):
    for k in range(n):
        yy = y + k * gap
        pts = [(x, yy + math.sin((x - x0) / 26 + k) * amp) for x in range(int(x0), int(x1) + 1, 6)]
        d.line(pts, fill=line, width=3)


def _sea(d, s, base, fill, line):
    hz = base - s * 0.42                                        # 水平線
    sx, sr = s * 0.7, s * 0.17
    d.pieslice((sx - sr, hz - sr, sx + sr, hz + sr), 180, 360, fill=fill, outline=line, width=3)   # 夕日
    for k in range(9):
        a = math.pi + math.pi * (k + 0.5) / 9
        d.line([(sx + math.cos(a) * sr * 1.25, hz + math.sin(a) * sr * 1.25), (sx + math.cos(a) * sr * 1.55, hz + math.sin(a) * sr * 1.55)], fill=line, width=3)
    d.line([(s * 0.02, hz), (s * 0.98, hz)], fill=line, width=3)
    _waves(d, s * 0.04, s * 0.96, hz + s * 0.07, 4, line, n=3, gap=s * 0.065)
    # ヨット
    bx, by = s * 0.36, hz - 2
    d.polygon([(bx, by - s * 0.2), (bx, by - s * 0.03), (bx + s * 0.11, by - s * 0.03)], fill=fill, outline=line)
    d.polygon([(bx - s * 0.07, by - s * 0.02), (bx + s * 0.14, by - s * 0.02), (bx + s * 0.1, by + s * 0.02), (bx - s * 0.04, by + s * 0.02)], fill=line)
    # 砂浜とパラソル
    beach = base - s * 0.13
    d.polygon([(0, base), (s, base), (s, beach + s * 0.03), (s * 0.55, beach - s * 0.01), (0, beach + s * 0.02)], fill=fill, outline=line)
    d.line([(0, beach + s * 0.02), (s * 0.55, beach - s * 0.01), (s, beach + s * 0.03)], fill=line, width=3)
    px, py = s * 0.18, base - s * 0.04
    d.line([(px, py), (px + s * 0.03, py - s * 0.36)], fill=line, width=4)
    d.pieslice((px - s * 0.17, py - s * 0.46, px + s * 0.23, py - s * 0.22), 190, 350, fill=fill, outline=line, width=3)
    for k in range(4):
        a = math.radians(195 + k * 50)
        d.line([(px + s * 0.03, py - s * 0.36), (px + s * 0.03 + math.cos(a) * s * 0.19, py - s * 0.36 + math.sin(a) * s * 0.1)], fill=line, width=2)
    d.rounded_rectangle((s * 0.3, base - s * 0.07, s * 0.46, base - s * 0.045), radius=4, fill=line)   # ビーチタオル


def _ridge(x0, x1, base, peak_x, peak_y, n=48):
    """なだらかな山の稜線: 裾は広く、頂上は丸い(コサインの山)"""
    pts = []
    for i in range(n + 1):
        x = x0 + (x1 - x0) * i / n
        if x <= peak_x:
            t = (x - x0) / max(peak_x - x0, 1e-6)
        else:
            t = (x1 - x) / max(x1 - peak_x, 1e-6)
        h = (1 - math.cos(math.pi * t)) / 2          # 0→1 のなめらかな立ち上がり
        pts.append((x, base - (base - peak_y) * h ** 1.15))
    return pts


def _mountain(d, s, base, fill, line):
    # 奥の山なみ → 手前の大きな山 の順に重ねる(どれも頂上は丸め)
    for x0, x1, px, py in ((s * -0.15, s * 0.6, s * 0.2, base - s * 0.36), (s * 0.42, s * 1.15, s * 0.82, base - s * 0.4)):
        ridge = _ridge(x0, x1, base, px, py)
        d.polygon([(x0, base)] + ridge + [(x1, base)], fill=fill)
        d.line(ridge, fill=line, width=2)
    ridge = _ridge(s * 0.0, s * 1.0, base, s * 0.5, base - s * 0.56)
    d.polygon([(s * 0.0, base)] + ridge + [(s * 1.0, base)], fill=fill)
    d.line(ridge, fill=line, width=4)
    # 頂上の雪(稜線に沿った短い帯)
    top = [p for p in ridge if p[1] < base - s * 0.49]
    if len(top) > 2:
        d.line(top, fill=line, width=9)
        mid = top[len(top) // 2]
        d.line([(top[0][0] + 6, top[0][1] + 10), (mid[0] - s * 0.02, mid[1] + s * 0.05), (mid[0] + s * 0.02, mid[1] + s * 0.03), (top[-1][0] - 6, top[-1][1] + 10)], fill=line, width=3)
    # ロープウェイ(ゆるいカーブのケーブル)
    c0, c1 = (s * 0.06, base - s * 0.14), (s * 0.44, base - s * 0.52)
    cable = [(c0[0] + (c1[0] - c0[0]) * t, c0[1] + (c1[1] - c0[1]) * t + math.sin(math.pi * t) * s * 0.03) for t in [i / 30 for i in range(31)]]
    d.line(cable, fill=line, width=2)
    gx, gy = cable[13]
    d.line([(gx, gy), (gx, gy + s * 0.05)], fill=line, width=2)
    d.rounded_rectangle((gx - s * 0.05, gy + s * 0.05, gx + s * 0.05, gy + s * 0.13), radius=8, fill=fill, outline=line, width=3)
    d.line([(gx - s * 0.035, gy + s * 0.08), (gx + s * 0.035, gy + s * 0.08)], fill=line, width=2)
    # 手前の木(丸い広葉樹)
    for k, x in enumerate((s * 0.12, s * 0.22, s * 0.7, s * 0.82, s * 0.92)):
        r = s * (0.04 if k % 2 else 0.05)
        d.line([(x, base), (x, base - r * 1.4)], fill=line, width=3)
        d.ellipse((x - r, base - r * 3.2, x + r, base - r * 1.2), fill=fill, outline=line, width=3)


def _mall(d, s, base, fill, line):
    """郊外型のショッピングモール: 横長で平たい建物 + 広い駐車場 + 道路沿いの看板"""
    ground = base - s * 0.2                                     # 建物の足元(手前は駐車場)
    x0, x1, top = s * 0.02, s * 0.98, ground - s * 0.2
    d.rectangle((x0, top, x1, ground), fill=fill, outline=line, width=3)
    d.line([(x0 - s * 0.01, top), (x1 + s * 0.01, top)], fill=line, width=5)       # 平らな屋上のふち
    # 入口(中央の少し高いガラスのボックス)と左右の入口
    for cx, w, h in ((s * 0.5, 0.2, 0.08), (s * 0.16, 0.12, 0.03), (s * 0.84, 0.12, 0.03)):
        d.rectangle((cx - s * w / 2, top - s * h, cx + s * w / 2, ground), fill=fill, outline=line, width=3)
        for k in range(1, 4):
            x = cx - s * w / 2 + k * s * w / 4
            d.line([(x, top - s * h + 6), (x, ground)], fill=line, width=1)
        d.rectangle((cx - s * w / 2 - 6, ground - s * 0.075, cx + s * w / 2 + 6, ground - s * 0.065), fill=line)   # ひさし
    d.rounded_rectangle((s * 0.42, top - s * 0.06, s * 0.58, top - s * 0.02), radius=4, fill=line)                 # 壁の看板(文字なし)
    for x in (s * 0.3, s * 0.36, s * 0.64, s * 0.7):                                                              # 店の窓
        d.rectangle((x - s * 0.02, top + s * 0.06, x + s * 0.02, ground - s * 0.03), outline=line, width=2)
    # 駐車場(手前): 白線と車
    d.line([(0, ground), (s, ground)], fill=line, width=2)
    for row, y0 in enumerate((ground + s * 0.03, ground + s * 0.11)):
        for k in range(12):
            x = s * 0.04 + k * s * 0.08
            d.line([(x, y0), (x + s * 0.012, y0 + s * 0.06)], fill=line, width=2)
            if (k * 7 + row * 3) % 4 == 0:
                d.rounded_rectangle((x + s * 0.012, y0 + s * 0.012, x + s * 0.062, y0 + s * 0.05), radius=6, fill=line)
    # 道路沿いの看板(ポール)
    px = s * 0.9
    d.line([(px, base), (px, base - s * 0.62)], fill=line, width=5)
    d.rounded_rectangle((px - s * 0.075, base - s * 0.74, px + s * 0.075, base - s * 0.6), radius=8, fill=fill, outline=line, width=3)
    d.text((px, base - s * 0.67), "★", font=font(int(s * 0.06), "black"), fill=line, anchor="mm")
    for k in range(3):                                                                                           # 植え込み
        x = s * (0.1 + k * 0.08)
        d.ellipse((x - s * 0.025, ground - s * 0.05, x + s * 0.025, ground), fill=fill, outline=line, width=2)


def _onsen(d, s, base, fill, line):
    # 湯船と湯気(温泉マーク風)
    cx, cy = s * 0.5, base - s * 0.3
    d.ellipse((cx - s * 0.26, cy - s * 0.08, cx + s * 0.26, cy + s * 0.12), fill=fill, outline=line, width=4)
    for k, dx in enumerate((-0.12, 0, 0.12)):
        x = cx + s * dx
        pts = [(x + math.sin(t / 7 * math.pi * 2 + k) * s * 0.03, cy - s * 0.1 - t * s * 0.06) for t in range(7)]
        d.line(pts, fill=line, width=max(4, int(s * 0.025)), joint="curve")
    # 旅館の屋根
    for x, w in ((s * 0.0, 0.3), (s * 0.7, 0.3)):
        d.rectangle((x + s * 0.03, base - s * 0.2, x + s * (w - 0.03), base), fill=fill, outline=line, width=2)
        d.polygon([(x, base - s * 0.2), (x + s * w, base - s * 0.2), (x + s * w * 0.75, base - s * 0.3), (x + s * w * 0.25, base - s * 0.3)], fill=line)
        for k in range(3):
            xx = x + s * (0.06 + k * 0.08)
            d.rectangle((xx, base - s * 0.15, xx + s * 0.04, base - s * 0.07), fill=line)
    for k in range(5):                                          # ちょうちん
        x = s * (0.32 + k * 0.09)
        d.line([(x, base - s * 0.06), (x, base - s * 0.04)], fill=line, width=2)
        d.ellipse((x - s * 0.02, base - s * 0.04, x + s * 0.02, base), fill=line)


def _torii(d, cx, base, s, fill, line):
    w, h = s * 0.62, s * 0.62
    pw = s * 0.05
    for x in (cx - w * 0.36, cx + w * 0.36):
        d.polygon([(x - pw / 2, base - h + s * 0.08), (x + pw / 2, base - h + s * 0.08), (x + pw * 0.62, base), (x - pw * 0.62, base)], fill=line)
    d.rectangle((cx - w * 0.46, base - h + s * 0.12, cx + w * 0.46, base - h + s * 0.15), fill=line)           # 貫
    d.polygon([(cx - w * 0.6, base - h), (cx + w * 0.6, base - h), (cx + w * 0.54, base - h + s * 0.05), (cx - w * 0.54, base - h + s * 0.05)], fill=line)   # 笠木
    d.polygon([(cx - w * 0.6, base - h), (cx - w * 0.66, base - h - s * 0.03), (cx - w * 0.5, base - h)], fill=line)
    d.polygon([(cx + w * 0.6, base - h), (cx + w * 0.66, base - h - s * 0.03), (cx + w * 0.5, base - h)], fill=line)
    d.rectangle((cx - s * 0.03, base - h + s * 0.05, cx + s * 0.03, base - h + s * 0.12), fill=line)          # 額束


def _shrine(d, s, base, fill, line):
    # 奥の社殿
    x0, x1 = s * 0.46, s * 0.96
    d.rectangle((x0 + s * 0.04, base - s * 0.2, x1 - s * 0.04, base), fill=fill, outline=line, width=2)
    d.polygon([(x0 - s * 0.02, base - s * 0.2), (x1 + s * 0.02, base - s * 0.2), (x1 - s * 0.08, base - s * 0.34), (x0 + s * 0.08, base - s * 0.34)], fill=fill, outline=line)
    d.line([(x0 - s * 0.02, base - s * 0.2), (x0 + s * 0.08, base - s * 0.34), (x1 - s * 0.08, base - s * 0.34), (x1 + s * 0.02, base - s * 0.2)], fill=line, width=3)
    for xx in (x0 + s * 0.14, x1 - s * 0.14):                   # 千木
        d.line([(xx - s * 0.03, base - s * 0.38), (xx + s * 0.03, base - s * 0.32)], fill=line, width=3)
        d.line([(xx + s * 0.03, base - s * 0.38), (xx - s * 0.03, base - s * 0.32)], fill=line, width=3)
    d.line([(x0 + s * 0.08, base - s * 0.2), (x1 - s * 0.08, base - s * 0.2)], fill=line, width=3)
    for k in range(4):
        xx = x0 + s * (0.1 + k * 0.1)
        d.line([(xx, base - s * 0.18), (xx, base)], fill=line, width=2)
    _torii(d, s * 0.29, base, s * 0.72, fill, line)
    for x in (s * 0.08, s * 0.52):                              # 灯籠
        d.rectangle((x - s * 0.012, base - s * 0.12, x + s * 0.012, base), fill=line)
        d.rectangle((x - s * 0.03, base - s * 0.17, x + s * 0.03, base - s * 0.12), fill=fill, outline=line, width=2)
        d.polygon([(x - s * 0.045, base - s * 0.17), (x + s * 0.045, base - s * 0.17), (x, base - s * 0.21)], fill=line)


def _pagoda(d, cx, base, s, fill, line):
    """五重塔(汎用)"""
    y = base
    w = s * 0.22
    for k in range(5):
        h = s * 0.1
        bw = w * (1 - k * 0.1)
        d.rectangle((cx - bw * 0.36, y - h, cx + bw * 0.36, y), fill=fill, outline=line, width=2)
        ry = y - h
        d.polygon([(cx - bw * 0.62, ry + s * 0.012), (cx + bw * 0.62, ry + s * 0.012), (cx + bw * 0.4, ry - s * 0.03), (cx - bw * 0.4, ry - s * 0.03)], fill=line)
        d.line([(cx - bw * 0.62, ry + s * 0.012), (cx - bw * 0.68, ry - s * 0.004)], fill=line, width=3)
        d.line([(cx + bw * 0.62, ry + s * 0.012), (cx + bw * 0.68, ry - s * 0.004)], fill=line, width=3)
        y = ry - s * 0.03
    d.line([(cx, y), (cx, y - s * 0.16)], fill=line, width=4)    # 相輪
    for k in range(6):
        yy = y - s * 0.02 - k * s * 0.022
        d.line([(cx - s * 0.018, yy), (cx + s * 0.018, yy)], fill=line, width=3)


def _temple(d, s, base, fill, line):
    # 本堂(大きな屋根)
    x0, x1 = s * 0.02, s * 0.62
    d.rectangle((x0 + s * 0.06, base - s * 0.18, x1 - s * 0.06, base), fill=fill, outline=line, width=2)
    d.polygon([(x0 - s * 0.01, base - s * 0.17), (x1 + s * 0.01, base - s * 0.17), (x1 - s * 0.1, base - s * 0.3), (x0 + s * 0.1, base - s * 0.3)], fill=line)
    d.line([(x0 - s * 0.01, base - s * 0.17), (x0 - s * 0.03, base - s * 0.2)], fill=line, width=3)
    d.line([(x1 + s * 0.01, base - s * 0.17), (x1 + s * 0.03, base - s * 0.2)], fill=line, width=3)
    d.polygon([(x0 + s * 0.16, base - s * 0.3), (x1 - s * 0.16, base - s * 0.3), (x1 - s * 0.22, base - s * 0.36), (x0 + s * 0.22, base - s * 0.36)], fill=line)
    for k in range(5):
        xx = x0 + s * (0.1 + k * 0.1)
        d.line([(xx, base - s * 0.16), (xx, base)], fill=line, width=2)
    _pagoda(d, s * 0.8, base, s, fill, line)


def draw_theme_landmark(theme, size, neon=False):
    """目的地のランドマーク(1枚の透過レイヤー size x size)。
    neon=True は表紙用: 半透明の塗り + 白っぽく光る輪郭(あとで neon_landmark() で発光させる)"""
    s = size
    lay = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(lay, "RGBA")
    base = s * 0.96
    acc = THEMES.get(theme, {}).get("accent", GOLD)
    if neon:
        fill = acc + (46,)
        line = _mix(acc, (255, 255, 255), 0.55) + (255,)
    else:
        fill = (40, 46, 84, 235)
        line = acc + (255,)
    if theme == "maihama":
        _castle(d, s * 0.11, s * 0.98, s * 0.78, fill, line)
    elif theme == "yokohama":
        _ferris_wheel(d, s * 0.36, s * 0.5, s * 0.3, line[:3] if neon else acc)
        _tower_building(d, s * 0.78, base, s * 0.2, s * 0.86, fill, line)
        d.rectangle((s * 0.02, base - 4, s * 0.98, base), fill=line)
    elif theme == "kawagoe":
        roof = acc + (150,) if neon else (72, 66, 82, 255)
        wall = acc + (40,) if neon else (235, 228, 210, 235)
        wood = acc + (70,) if neon else (120, 84, 50, 240)
        _kura(d, s * 0.04, base, s * 0.3, s * 0.3, wall, line, roof)
        _bell_tower(d, s * 0.56, base, s * 0.9, wood, line, roof)
        _kura(d, s * 0.7, base, s * 0.26, s * 0.26, wall, line, roof)
    elif theme == "haneda":
        _control_tower(d, s * 0.78, base, s * 0.8, fill, line)
        _runway(d, s * 0.02, s * 0.98, base - 6, line, lights=False)
        _airplane(d, s * 0.4, s * 0.36, s * 0.7, fill, line, deg=-16)
    elif theme == "narita":
        _control_tower(d, s * 0.14, base, s * 0.72, fill, line)
        _runway(d, s * 0.02, s * 0.98, base - 30, line, lights=True)
        _airplane(d, s * 0.62, s * 0.44, s * 0.62, fill, line, deg=-22)
    elif theme == "skytree":
        _skyline(d, s * 0.02, base, s * 0.96, s * 0.16, fill, line, seed=11)
        _lattice_tower(d, s * 0.5, base, s, fill, line)
    elif theme == "sea":
        _sea(d, s, base, fill, line)
    elif theme == "mountain":
        _mountain(d, s, base, fill, line)
    elif theme == "mall":
        _mall(d, s, base, fill, line)
    elif theme == "onsen":
        _onsen(d, s, base, fill, line)
    elif theme == "shrine":
        _shrine(d, s, base, fill, line)
    elif theme == "temple":
        _temple(d, s, base, fill, line)
    elif theme == "shinjuku":
        _skyline(d, s * 0.02, base, s * 0.96, s * 0.9, fill, line, seed=7)
    elif theme == "shibuya":
        _skyline(d, s * 0.02, base - s * 0.22, s * 0.96, s * 0.6, fill, line, seed=3)
        # スクランブル交差点のしま模様
        for k in range(9):
            x = s * 0.1 + k * s * 0.09
            d.polygon([(x, base), (x + s * 0.05, base), (x + s * 0.1, base - s * 0.2), (x + s * 0.05, base - s * 0.2)], fill=(255, 255, 255, 200))
    return lay


def neon_landmark(theme, size):
    """表紙用: ランドマークを発光させ、足元を地図に溶け込ませる(下端フェード)"""
    acc = THEMES.get(theme, {}).get("accent", GOLD)
    core = draw_theme_landmark(theme, size, neon=True)
    a = core.split()[3]
    out = Image.new("RGBA", core.size, (0, 0, 0, 0))
    for rad, k in ((22, 0.9), (8, 0.8)):
        g = Image.new("RGBA", core.size, acc + (0,))
        g.putalpha(a.filter(ImageFilter.GaussianBlur(rad)).point(lambda v, k=k: min(255, int(v * k))))
        out.alpha_composite(g)
    out.alpha_composite(core)
    # 下12%を透明へフェード(地面から立ち上がって見えるように)
    fade = Image.new("L", core.size, 255)
    fd = ImageDraw.Draw(fade)
    h = core.size[1]
    for y in range(int(h * 0.86), h):
        fd.line([(0, y), (core.size[0], y)], fill=int(255 * (1 - (y - h * 0.86) / (h * 0.14)) ** 1.2))
    out.putalpha(ImageChops.multiply(out.split()[3], fade))
    return out


def draw_theme_sky(img, theme, region):
    """表紙の空(タイトルの裏)の飾り。舞浜は花火、それ以外は控えめな星"""
    x0, y0, x1, y1 = region
    lay = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(lay, "RGBA")
    acc = THEMES.get(theme, {}).get("accent", GOLD)
    if theme == "maihama":
        for cx, cy, r, col in ((LANDMARK_X + 45, 235, 54, (255, 120, 200)), (W - 60, 300, 52, (120, 220, 255)),
                               (LANDMARK_X + 95, 135, 36, (255, 215, 90)), (W - 40, 160, 34, (170, 140, 255))):
            _firework(d, cx, cy, r, col, alpha=170)
    else:
        import random
        rnd = random.Random(5)
        for _ in range(40):
            x, y = rnd.uniform(x0, x1), rnd.uniform(y0, y1)
            r = rnd.uniform(1, 2.6)
            d.ellipse((x - r, y - r, x + r, y + r), fill=acc + (rnd.randint(60, 150),))
    img.alpha_composite(lay)


# ================================================================ 各スライド

MAP_TOP, MAP_BOTTOM = 470, 1040
LANDMARK_SIZE = 270
LANDMARK_X = 725            # ランドマークの左端(タイトルはこれより左に収める)


def _route_paths(routes, line_geoms, station_db):
    paths = []
    for r in routes:
        st, _tr = leg_stations(r.get("legs") or [])
        if len(st) < 2:
            paths.append(None)
            continue
        cfg_st = [{"line": s["line"], "name": s["name"], "t_min": (0 if i == 0 else (1 if i == len(st) - 1 else None))}
                  for i, s in enumerate(st)]
        try:
            p, warns = build_route({"key": r["key"], "name": r["name"], "color": list(r["color"]), "stations": cfg_st},
                                   line_geoms, station_db)
        except KeyError as e:
            print(f"[warn] {r['name']}: 地図の線を作れません ({e})")
            paths.append(None)
            continue
        for w in warns:
            if "自動補完" not in w and "畳み込み" not in w and "間引き" not in w:
                print("[warn]", w)
        paths.append(p)
    return paths


# 駅データ(首都圏の鉄道)に無い場所の位置(地図にピンを置くための目安)。
# バス停・空港・首都圏の外の駅など。ここに無い場所は区間に "from_lonlat" / "to_lonlat" を書く。
PLACES = {
    "仙台": (140.8822, 38.2601), "仙台空港": (140.9170, 38.1397), "盛岡": (141.1365, 39.7016), "福島": (140.4594, 37.7544),
    "郡山": (140.3881, 37.3984), "宇都宮": (139.8985, 36.5590), "高崎": (139.0127, 36.3228), "長野": (138.1888, 36.6430),
    "新潟": (139.0618, 37.9122), "金沢": (136.6476, 36.5781), "静岡": (138.3890, 34.9718), "浜松": (137.7350, 34.7038),
    "名古屋": (136.8815, 35.1709), "京都": (135.7588, 34.9858), "新大阪": (135.5001, 34.7334), "大阪": (135.4959, 34.7025),
    "岡山": (133.9180, 34.6664), "広島": (132.4754, 34.3976), "博多": (130.4207, 33.5903), "大宮": (139.6237, 35.9064),
    "東京": (139.7671, 35.6812), "舞浜": (139.8843, 35.6363), "バスタ新宿": (139.7010, 35.6883), "横浜": (139.6223, 35.4658),
    "成田空港": (140.3929, 35.7720), "羽田空港": (139.7798, 35.5494), "新千歳空港": (141.6923, 42.7752), "伊丹空港": (135.4382, 34.7855),
    "関西空港": (135.2304, 34.4320), "中部国際空港": (136.8054, 34.8584), "福岡空港": (130.4508, 33.5859),
    "東京ディズニーランド": (139.8804, 35.6329), "東京ディズニーシー": (139.8851, 35.6267), "東京ディズニーリゾート": (139.8804, 35.6329),
}

RAIL_MODES = ("rail",)                      # 駅データ(SHP)で座標を引く区間
MODE_LABEL = {"rail": "鉄道", "train": "鉄道", "shinkansen": "新幹線", "bus": "バス", "plane": "飛行機"}


def place_lonlat(name, line, station_db, explicit=None):
    """場所の (lon, lat)。区間に書いた座標 → 駅データ(その路線→ほかの路線)→ PLACES の順で探す"""
    if explicit:
        return float(explicit[0]), float(explicit[1])
    if station_db is not None:
        try:
            return get_station_lonlat(station_db, resolve_line_name(line), name)
        except KeyError:
            pass
        for ln, rows in station_db.items():
            for r in rows:
                if r["name"] == name:
                    return r["lon"], r["lat"]
    key = name[:-1] if name.endswith("駅") else name
    if key in PLACES:
        return PLACES[key]
    raise KeyError(name)


def _endpoint(config, which, routes, station_db):
    """発着の (名前, lon, lat)。config の origin/destination に lat/lon があればそれを使う"""
    spec = config.get(which) or {}
    if isinstance(spec, str):
        spec = {"name": spec}
    legs = routes[0].get("legs") or []
    name = spec.get("name") or (legs[0]["from"] if which == "origin" else legs[-1]["to"])
    if spec.get("lat") is not None and spec.get("lon") is not None:
        return name, float(spec["lon"]), float(spec["lat"])
    for r in routes:
        lg = (r.get("legs") or [])
        if not lg:
            continue
        leg = lg[0] if which == "origin" else lg[-1]
        nm = leg["from"] if which == "origin" else leg["to"]
        try:
            lon, lat = place_lonlat(nm, leg["line"], station_db, leg.get("from_lonlat" if which == "origin" else "to_lonlat"))
            return name, lon, lat
        except KeyError:
            continue
    try:
        lon, lat = place_lonlat(name, "", station_db)
        return name, lon, lat
    except KeyError:
        raise KeyError(f"{which} の座標が分かりません。config の {which} に lat / lon を書いてください")


def fmt_latlon(lon, lat):
    return f"{abs(lat):.3f}°{'N' if lat >= 0 else 'S'}  {abs(lon):.3f}°{'E' if lon >= 0 else 'W'}"


COVER_MAP_LEN = 520          # 発着2点の画面上の距離(px)
COVER_MID = (540, 840)       # 発着2点の中点を置く位置
COVER_LANDMARK = 470         # 表紙ランドマークの大きさ(px)


def _cover_projection(o_lon, o_lat, d_lon, d_lat):
    """着(ランドマークが立つ点)を下寄りに固定し、発がその周りの安全な範囲に来るよう投影を決める。
    着が発より北のとき(発が下に来る)は、着を少し上げてランドマークを小さめにする。"""
    cos0 = math.cos(math.radians((o_lat + d_lat) / 2))
    vx, vy = (o_lon - d_lon) * cos0, (d_lat - o_lat)      # 着→発(画面座標の向き。y は下が正)
    dist = math.hypot(vx, vy) or 1e-4
    dest_y = 945 if vy <= 0.15 * dist else 800
    dest_x = 640 if vx < 0 else 440
    if abs(vx) < 0.35 * dist:
        # 発がほぼ真上/真下: 着を発と同じ側に寄せ、反対側にランドマークを置く余白をつくる
        dest_x = 700 if vx >= 0 else 380
    sc = COVER_MAP_LEN / dist
    lims = []
    if vx > 0: lims.append((930 - dest_x) / vx)
    if vx < 0: lims.append((150 - dest_x) / vx)
    if vy > 0: lims.append((1020 - dest_y) / vy)
    if vy < 0: lims.append((585 - dest_y) / vy)
    sc = min([sc] + lims)
    return {"lon_min": d_lon, "lat_max": d_lat, "cos0": cos0, "x_min": 0.0, "y_min": 0.0,
            "scale": sc, "off_x": dest_x, "off_y": dest_y}


# 首都圏の詳しい海岸線(data/coastline.geojson)の範囲。表紙がこれをはみ出すときは全国版を使う
DETAIL_COAST_BBOX = (136.07, 20.42, 153.99, 37.15)


def _coastline_for(proj, geojson_path):
    from .geo import unproject
    base = Path(geojson_path).parent
    lon0, lat1 = unproject(proj, 0, 0)
    lon1, lat0 = unproject(proj, W, H)
    x0, y0, x1, y1 = DETAIL_COAST_BBOX
    inside = x0 <= lon0 and lon1 <= x1 and y0 <= lat0 and lat1 <= y1
    japan = base / "coastline_japan.geojson"
    if not inside and japan.exists():
        return str(japan)
    return str(base / "coastline.geojson")


def _grid_step(proj):
    """経緯線の間隔: 画面の横幅に10本前後"""
    from .geo import unproject
    lon0, _ = unproject(proj, 0, 0)
    lon1, _ = unproject(proj, W, 0)
    span = abs(lon1 - lon0) / 10
    for st in (0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1.0, 2.0):
        if st >= span:
            return st
    return 5.0


def _graticule(lay, proj, step=0.05, alpha=13):
    from .geo import unproject
    d = ImageDraw.Draw(lay, "RGBA")
    lon0, lat1 = unproject(proj, -50, -50)
    lon1, lat0 = unproject(proj, W + 50, H + 50)
    k = math.floor(lon0 / step)
    while k * step <= lon1:
        x, _ = project(proj, k * step, lat0)
        d.line([(x, 0), (x, H)], fill=(140, 170, 255, alpha), width=1)
        k += 1
    k = math.floor(lat0 / step)
    while k * step <= lat1:
        _, y = project(proj, lon0, k * step)
        d.line([(0, y), (W, y)], fill=(140, 170, 255, alpha), width=1)
        k += 1


def slide_cover(config, routes, paths, geojson_path, station_db):
    """表紙: 全面の夜の地図 + 目的地に立ち上がる発光ランドマーク + 花火/星 + 発着ピン"""
    theme = config.get("destination_theme", "none")
    acc = THEMES.get(theme, {}).get("accent", GOLD)
    o_name, o_lon, o_lat = _endpoint(config, "origin", routes, station_db)
    d_name, d_lon, d_lat = _endpoint(config, "destination", routes, station_db)
    proj = _cover_projection(o_lon, o_lat, d_lon, d_lat)
    ox, oy = project(proj, o_lon, o_lat)
    dx, dy = project(proj, d_lon, d_lat)

    # --- 地図(全面): 海 → 陸 → 海岸線の発光 → 経緯線
    img = Image.new("RGBA", (W, H), (2, 6, 18, 255))
    land = load_land_polygons(_coastline_for(proj, geojson_path))
    draw_land_and_sea(img, proj, land, land_color=(17, 22, 42, 255), coast_color=acc + (0,))
    coast = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw_land_and_sea(coast, proj, land, land_color=(0, 0, 0, 0), coast_color=acc + (150,))
    for rad, k in ((10, 1.0), (3, 0.9)):
        g = coast.filter(ImageFilter.GaussianBlur(rad))
        img.alpha_composite(g)
    img.alpha_composite(coast)
    _graticule(img, proj, step=_grid_step(proj))

    # 着の周りをアクセント色でほんのり照らす
    halo = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(halo).ellipse((dx - 330, dy - 330, dx + 330, dy + 330), fill=acc + (70,))
    img.alpha_composite(halo.filter(ImageFilter.GaussianBlur(120)))

    # 上下のビネット(文字を読みやすく)
    vg = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    vd = ImageDraw.Draw(vg)
    for y in range(0, 560):
        t = 1 - y / 560
        vd.line([(0, y), (W, y)], fill=BG_TOP + (int(255 * min(1, t ** 0.8 * 1.15)),))
    for y in range(1060, H):
        t = (y - 1060) / (H - 1060)
        vd.line([(0, y), (W, y)], fill=BG_BOTTOM + (int(255 * min(1, t ** 0.9 * 1.1)),))
    img.alpha_composite(vg)

    # --- 発→着 の旅の弧(路線ではなく「どこからどこへ」を示す飾り)
    if config.get("cover_arc", True):
        arc = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        ad = ImageDraw.Draw(arc)
        mx, my = (ox + dx) / 2, (oy + dy) / 2
        nx, ny = -(dy - oy), (dx - ox)
        nl = math.hypot(nx, ny) or 1
        bend = 0.22 * math.hypot(dx - ox, dy - oy)
        if ny > 0:
            nx, ny = -nx, -ny                       # 弧は上向きにふくらませる
        cx, cy = mx + nx / nl * bend, my + ny / nl * bend
        pts = [((1 - t) ** 2 * ox + 2 * (1 - t) * t * cx + t ** 2 * dx,
                (1 - t) ** 2 * oy + 2 * (1 - t) * t * cy + t ** 2 * dy) for t in [i / 120 for i in range(121)]]
        for i in range(0, 120, 3):
            t = i / 120
            col = _mix(GOLD, acc, t)
            ad.line([pts[i], pts[i + 2]], fill=col + (235,), width=5)
        img.alpha_composite(arc.filter(ImageFilter.GaussianBlur(5)))
        img.alpha_composite(arc)

    # --- 空の飾り(ランドマークの上): 舞浜は花火、ほかは星
    size = int(min(COVER_LANDMARK, (dy - 470) / 0.95))
    top_y = dy - size * 0.95
    # 発がランドマークの真上あたりにあると重なるので、ランドマークを発と反対の横へずらす
    lcx = dx
    if dx - size / 2 - 20 < ox < dx + size / 2 + 20 and oy < dy:
        side = -1 if ox >= dx else 1
        lcx = dx + side * size * 0.5
        lcx = min(max(lcx, size / 2 + 10), W - size / 2 - 10)
    sky = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sky, "RGBA")
    if theme == "maihama":
        for fx, fy, r, col in ((lcx - 250, top_y + 40, 82, (255, 120, 200)), (lcx + 230, top_y + 10, 70, (120, 220, 255)),
                               (lcx + 120, top_y - 70, 46, (255, 215, 90)), (lcx - 50, top_y - 30, 36, (170, 140, 255))):
            fy = max(fy, 470 + r)                          # タイトルのチップにかぶらないように
            if math.hypot(fx - ox, fy - oy) < r + 60 or fx - r < 0 or fx + r > W:
                continue                                  # 発のピン・画面の端にかぶる花火は描かない
            _firework(sd, fx, fy, r, col, alpha=210, rays=22)
        img.alpha_composite(sky.filter(ImageFilter.GaussianBlur(6)))
        img.alpha_composite(sky)
    elif theme in THEMES:
        import random
        rnd = random.Random(5)
        for _ in range(70):
            x, y = rnd.uniform(lcx - 360, lcx + 360), rnd.uniform(top_y - 140, top_y + 200)
            r = rnd.uniform(1, 2.8)
            sd.ellipse((x - r, y - r, x + r, y + r), fill=acc + (rnd.randint(70, 200),))
        img.alpha_composite(sky)

    # --- ランドマーク(着の地点から立ち上がる)+ 足元の光の輪
    if theme in THEMES:
        rings = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        rd = ImageDraw.Draw(rings)
        for k, (rw, al) in enumerate(((60, 230), (130, 150), (210, 80))):
            rd.ellipse((dx - rw, dy - rw * 0.28, dx + rw, dy + rw * 0.28), outline=acc + (al,), width=4 - k)
        img.alpha_composite(rings.filter(ImageFilter.GaussianBlur(4)))
        img.alpha_composite(rings)
        lm = neon_landmark(theme, size)
        img.alpha_composite(lm, (int(lcx - size / 2), int(dy - size * 0.95)))

    img = img.convert("RGB")
    d = ImageDraw.Draw(img, "RGBA")

    # --- 発着ピン(発=金のリング、着=光る点)と名前
    def pin(x, y, col):
        for r, al in ((30, 60), (20, 120)):
            d.ellipse((x - r, y - r, x + r, y + r), outline=col + (al,), width=3)
        d.ellipse((x - 12, y - 12, x + 12, y + 12), fill=INK + (255,), outline=col + (255,), width=5)

    def label(x, y, tag, name, pos):
        """pos: below / above / left / right(ピンに対してラベルを置く向き)"""
        f = font(46, "black")
        ft = font(26, "black")
        tw = text_w(d, name, f) + 64
        if pos in ("left", "right"):
            bx = x - (tw / 2 + 56) if pos == "left" else x + (tw / 2 + 56)
            by = y
        else:
            bx = x
            by = y + 78 if pos == "below" else y - 78
        bx = min(max(bx, 90 + tw / 2), W - 90 - tw / 2)
        rrect(d, (bx - tw / 2 - 14, by - 36, bx + tw / 2 + 14, by + 36), 36, fill=(5, 8, 18, 215), outline=GOLD + (220,), width=2)
        d.ellipse((bx - tw / 2 - 4, by - 26, bx - tw / 2 + 48, by + 26), fill=GOLD + (255,))
        d.text((bx - tw / 2 + 22, by + 1), tag, font=ft, fill=DARK, anchor="mm")
        d.text((bx - tw / 2 + 64, by + 1), name, font=f, fill=INK, anchor="lm")

    pin(ox, oy, GOLD)
    pin(dx, dy, acc)
    if oy > dy + 20:
        # 発が着より下(着が北): 着は横、発は着と反対の横に出して重ならないようにする
        dpos = "left" if ox >= dx else "right"
        opos = "right" if dpos == "left" else "left"
        label(ox, oy, "発", o_name, opos)
        label(dx, dy, "着", d_name, dpos)
    else:
        label(ox, oy, "発", o_name, "above")
        label(dx, dy, "着", d_name, "below")

    # --- タイトル
    SX = COVER_SX
    tw_max = W - 2 * SX
    d.text((SX, COVER_TOP + 10), config.get("cover_kicker", "おでかけ節約くらべ"), font=font(28, "black"), fill=GOLD, anchor="lm")
    t1 = config.get("title_line1", f"{o_name} → {d_name}")
    d.text((SX, 268), t1, font=fit_font(d, t1, tw_max, 104), fill=INK, anchor="lm",
           stroke_width=4, stroke_fill=BG_TOP)
    t2 = config.get("title_line2", "どのルートがおトク?")
    d.text((SX, 356), t2, font=fit_font(d, t2, tw_max, 42), fill=INK2, anchor="lm", stroke_width=3, stroke_fill=BG_TOP)
    x = SX
    for lab in ("ルート", "お金", "時間", "まとめ"):
        f = font(26, "black")
        tw = text_w(d, lab, f)
        rrect(d, (x, 398, x + tw + 34, 444), 23, fill=(5, 8, 18, 160), outline=GOLD + (255,), width=3)
        d.text((x + 17 + tw / 2, 421), lab, font=f, fill=GOLD, anchor="mm")
        x += tw + 50

    # --- 凡例(比べるルート)
    n_col = len(routes)
    col_w = (W - 2 * SX + 60) / max(1, n_col)
    for i, r in enumerate(routes):
        route_tag(d, SX - 30 + col_w * i, 1098, {**r, "name": r["short_name"]}, size=26 if n_col == 3 else 30, max_w=col_w - 12)
    fy = COVER_BOTTOM - 14
    d.text((SX, fy), config.get("handle", "@geogram_jp"), font=font(22, "bold"), fill=MUTED, anchor="lm")
    d.text((W - SX, fy), "スワイプして比較 →", font=font(24, "black"), fill=GOLD, anchor="rm")
    return img


# ================================================================ 発時刻(◯時に着くなら何時に出る?)

def parse_hm(v):
    """'9:00' / '09:00' / '24:10' → 分。空なら None"""
    if v is None or v == "":
        return None
    hh, mm = str(v).replace("：", ":").split(":")
    return int(hh) * 60 + int(mm)


def fmt_hm(m):
    m = int(m) % 1440
    return f"{m // 60}:{m % 60:02d}"


def route_schedule(config, r):
    """ルートの発着時刻。
    - "schedule_text" があればそれだけを出す(例: 当日は無理)
    - "depart_time" / "arrive_time" があれば時刻表どおりに出す(24:10 のように24時以降も書ける)
    - 無ければ config の "arrive_by" から所要時間を引いた目安(5分単位で早めに丸めて「ごろ」)
    戻り値: {"text"} または {"dep", "arr", "dep_txt", "arr_txt", "est"}。何も分からなければ None"""
    if r.get("schedule_text"):
        return {"text": r["schedule_text"], "short": r.get("schedule_short", "×")}
    arr = parse_hm(r.get("arrive_time"))
    if arr is None:
        arr = parse_hm(config.get("arrive_by"))
    dep = parse_hm(r.get("depart_time"))
    est = False
    if dep is None and arr is not None and r.get("time_min") is not None:
        dep = (arr - int(r["time_min"])) // 5 * 5
        est = True
    if dep is None:
        return None
    if dep >= 1440:
        dep_txt = "深夜" + fmt_hm(dep)
    elif dep < 0:
        dep_txt = "前日" + fmt_hm(dep + 1440)
    else:
        dep_txt = fmt_hm(dep)
    if est:
        dep_txt += "ごろ"
    if dep >= 1440:
        dep -= 1440          # 「24:10」= 着く日の0:10。比べるときは着く日の時刻にそろえる
    return {"dep": dep, "arr": arr, "dep_txt": dep_txt, "arr_txt": fmt_hm(arr) if arr is not None else "", "est": est}


def schedule_line(sc):
    if not sc:
        return ""
    if "text" in sc:
        return sc["text"]
    return f"{sc['dep_txt']}発 → {sc['arr_txt']}着" if sc["arr_txt"] else f"{sc['dep_txt']}発"


def dest_display(config, routes):
    dn = config.get("destination")
    if isinstance(dn, dict):
        dn = dn.get("name")
    return dn or routes[0]["legs"][-1]["to"]


# ================================================================ ルート比較図(どこで何線に乗るか)

# 路線ごとの色(区間の "color" で上書きできる)。公式のラインカラーに近い色
LINE_COLORS = {
    "西武池袋線": (245, 170, 0), "西武有楽町線": (245, 170, 0), "西武新宿線": (245, 170, 0), "西武拝島線": (245, 170, 0),
    "有楽町線": (193, 164, 112), "副都心線": (156, 94, 49), "丸ノ内線": (246, 46, 54), "丸ノ内線分岐線": (246, 46, 54),
    "東西線": (0, 155, 191), "日比谷線": (181, 181, 172), "千代田線": (0, 187, 133), "半蔵門線": (143, 118, 214),
    "南北線": (0, 172, 155), "銀座線": (255, 149, 0),
    "大江戸線(光が丘~練馬)": (206, 4, 91), "大江戸線(都庁前~六本木~両国)": (206, 4, 91), "大江戸線(都庁前~飯田橋~両国)": (206, 4, 91),
    "浅草線": (232, 82, 152), "三田線": (0, 121, 194), "新宿線": (108, 187, 90),
    "京葉線": (220, 40, 50), "武蔵野線": (241, 90, 34), "山手線": (154, 205, 50), "中央線快速": (241, 90, 34),
    "中央総武線各停": (255, 212, 0), "京浜東北線": (0, 178, 229), "根岸線": (0, 178, 229), "埼京線": (0, 172, 154),
    "川越線": (0, 172, 154), "湘南新宿ライン": (226, 31, 38), "上野東京ライン": (246, 139, 30), "東海道本線": (246, 139, 30),
    "高崎線": (246, 139, 30), "宇都宮線": (246, 139, 30), "横須賀線総武快速線": (0, 103, 192), "横浜線": (127, 195, 66),
    "南武線": (255, 212, 0), "常磐線": (0, 178, 97), "青梅線": (241, 90, 34), "五日市線": (241, 90, 34),
    "東武東上線": (30, 100, 200), "東武伊勢崎線": (15, 108, 195), "東急東横線": (218, 4, 66), "東急田園都市線": (32, 162, 136),
    "東急目黒線": (0, 152, 214), "京王線": (221, 0, 119), "京王井の頭線": (147, 100, 200), "小田急小田原線": (0, 104, 183),
    "小田急江ノ島線": (0, 104, 183), "京急本線": (229, 23, 31), "りんかい線": (0, 65, 142), "つくばエクスプレス": (230, 0, 18),
    "相鉄本線": (13, 60, 140), "横浜市営地下鉄ブルーライン": (0, 114, 188), "東京モノレール": (0, 98, 172),
}


MODE_COLORS = {"shinkansen": (0, 160, 110), "bus": (120, 132, 160), "plane": (40, 140, 230), "train": (170, 178, 196)}


def line_color(leg):
    c = leg.get("color")
    if c:
        return tuple(c)
    if leg["line"] in LINE_COLORS:
        return LINE_COLORS[leg["line"]]
    return MODE_COLORS.get(leg.get("mode", "rail"), (170, 178, 196))


def _ic_bus(d, cx, cy, s, col):
    d.rounded_rectangle((cx - s * 0.42, cy - s * 0.3, cx + s * 0.42, cy + s * 0.22), radius=int(s * 0.1), fill=col)
    for k in range(3):
        x = cx - s * 0.34 + k * s * 0.24
        d.rectangle((x, cy - s * 0.22, x + s * 0.18, cy - s * 0.04), fill=DARK + (220,))
    for x in (cx - s * 0.24, cx + s * 0.24):
        d.ellipse((x - s * 0.09, cy + s * 0.14, x + s * 0.09, cy + s * 0.32), fill=col, outline=DARK + (255,), width=2)


def _ic_plane(d, cx, cy, s, col):
    # 右向きの飛行機
    d.polygon([(cx - s * 0.45, cy - s * 0.05), (cx + s * 0.38, cy - s * 0.06), (cx + s * 0.48, cy), (cx + s * 0.38, cy + s * 0.06), (cx - s * 0.45, cy + s * 0.05)], fill=col)
    d.polygon([(cx - s * 0.02, cy - s * 0.04), (cx - s * 0.2, cy - s * 0.42), (cx - s * 0.06, cy - s * 0.42), (cx + s * 0.16, cy - s * 0.04)], fill=col)
    d.polygon([(cx - s * 0.02, cy + s * 0.04), (cx - s * 0.2, cy + s * 0.42), (cx - s * 0.06, cy + s * 0.42), (cx + s * 0.16, cy + s * 0.04)], fill=col)
    d.polygon([(cx - s * 0.4, cy - s * 0.03), (cx - s * 0.48, cy - s * 0.2), (cx - s * 0.38, cy - s * 0.2), (cx - s * 0.3, cy - s * 0.03)], fill=col)


def _ic_shinkansen(d, cx, cy, s, col):
    d.polygon([(cx - s * 0.46, cy - s * 0.2), (cx + s * 0.1, cy - s * 0.2), (cx + s * 0.48, cy + s * 0.12), (cx + s * 0.48, cy + s * 0.2), (cx - s * 0.46, cy + s * 0.2)], fill=col)
    d.line([(cx - s * 0.46, cy + s * 0.04), (cx + s * 0.4, cy + s * 0.04)], fill=DARK + (220,), width=max(2, int(s * 0.06)))
    d.polygon([(cx + s * 0.06, cy - s * 0.14), (cx + s * 0.16, cy - s * 0.14), (cx + s * 0.3, cy - s * 0.02), (cx + s * 0.06, cy - s * 0.02)], fill=DARK + (220,))


ICONS.update({"bus": _ic_bus, "plane": _ic_plane, "shinkansen": _ic_shinkansen})


def route_nodes(route):
    """legs → 図に並べるノードと区間。
    ノード: {"name", "kind": start/end/transfer/through/walk_from/walk_to}
    区間:   {"kind": "ride"/"walk", "leg": leg}"""
    legs = route.get("legs") or []
    nodes, segs = [], []
    for i, lg in enumerate(legs):
        if i == 0:
            nodes.append({"name": lg["from"], "kind": "start"})
        else:
            prev = legs[i - 1]
            if prev["to"] != lg["from"]:              # 駅名が違う = 歩いて乗換
                nodes[-1]["kind"] = "walk_from"
                segs.append({"kind": "walk"})
                nodes.append({"name": lg["from"], "kind": "walk_to"})
            else:
                nodes[-1]["kind"] = "through" if lg.get("through") else "transfer"
        segs.append({"kind": "ride", "leg": lg})
        nodes.append({"name": lg["to"], "kind": "end"})
    return nodes, segs


def _mode_bar(d, mode, x0, x1, y, h, col):
    """区間の帯: 鉄道=シマシマ、新幹線=太い帯+白い中心線、バス=道路(黄色の破線)、飛行機=点線の弧"""
    if mode == "shinkansen":
        rrect(d, (x0, y - h * 0.65, x1, y + h * 0.65), h * 0.65, fill=col + (255,))
        d.line([(x0 + h * 0.6, y), (x1 - h * 0.6, y)], fill=(255, 255, 255, 240), width=max(3, int(h * 0.22)))
    elif mode == "bus":
        rrect(d, (x0, y - h * 0.6, x1, y + h * 0.6), h * 0.6, fill=(52, 58, 82, 255), outline=col + (255,), width=3)
        x = x0 + h * 0.7
        while x < x1 - h * 0.7:
            d.line([(x, y), (min(x + 14, x1 - h * 0.7), y)], fill=(255, 210, 40, 255), width=3)
            x += 26
    elif mode == "plane":
        pts = []
        for i in range(41):
            t = i / 40
            pts.append((x0 + (x1 - x0) * t, y - 4 * 34 * t * (1 - t)))
        for i in range(0, 40, 2):
            d.line([pts[i], pts[i + 1]], fill=col + (255,), width=5)
    else:
        _rail_bar(d, x0, x1, y, h, col)


def _rail_bar(d, x0, x1, y, h, col):
    """鉄道路線の「シマシマ」(路線色と白が交互の帯)"""
    rrect(d, (x0, y - h / 2, x1, y + h / 2), h / 2, fill=col + (255,))
    inner = h * 0.42
    step = 26
    x = x0 + h * 0.6
    k = 0
    while x < x1 - h * 0.6:
        xe = min(x + step, x1 - h * 0.6)
        if k % 2 == 1:
            d.rectangle((x, y - inner / 2, xe, y + inner / 2), fill=(255, 255, 255, 235))
        x = xe
        k += 1


def slide_route(config, routes):
    img = new_canvas()
    has_sched = any(route_schedule(config, r) for r in routes)
    default_sub = (f"{dest_display(config, routes)}に{fmt_hm(parse_hm(config['arrive_by']))}着なら 何時に出る? (◎=乗換)"
                   if config.get("arrive_by") else "どこで何に乗る? (◎=乗換 ・=直通)")
    header(img, "ルート", config.get("route_label", default_sub), num=1)
    d = ImageDraw.Draw(img, "RGBA")
    n = len(routes)
    top, bottom = 320, 1010
    gap = 22
    ch = (bottom - top - gap * (n - 1)) / max(n, 1)
    x0, x1 = PAD + 50, W - PAD - 50
    for ri, r in enumerate(routes):
        y = top + ri * (ch + gap)
        rrect(d, (PAD, y, W - PAD, y + ch), 24, fill=CARD + (255,), outline=CARD_LINE + (255,), width=2)
        d.rectangle((PAD, y + 18, PAD + 8, y + ch - 18), fill=tuple(r["color"]) + (255,))
        sc = route_schedule(config, r) if has_sched else None
        route_tag(d, PAD + 30, y + 40, r, size=32, max_w=(380 if sc else W - 2 * PAD - 260))
        if sc:
            # 発時刻(右上、乗換バッジの左)
            stext = schedule_line(sc)
            fbig = font(30, "black")
            xr = W - PAD - 20 - 150
            if "text" in sc:
                f = fit_font(d, stext, xr - (PAD + 440), 24, "black", min_size=16)
                d.text((xr, y + 40), stext, font=f, fill=(255, 150, 150), anchor="rm")
            else:
                dep, arr = f"{sc['dep_txt']}発", (f" → {sc['arr_txt']}着" if sc["arr_txt"] else "")
                fa = font(24, "bold")
                wa = text_w(d, arr, fa) if arr else 0
                d.text((xr, y + 42), arr, font=fa, fill=INK2, anchor="rm")
                d.text((xr - wa, y + 40), dep, font=fbig, fill=GOLD, anchor="rm")
        tr = r.get("transfers") or 0
        badge(d, W - PAD - 20, y + 40, f"乗換{tr}回" if tr else "乗換なし",
              fill=(255, 210, 40) if tr == min(x.get("transfers") or 0 for x in routes) else (60, 70, 105),
              ink=DARK if tr == min(x.get("transfers") or 0 for x in routes) else INK, size=24)

        nodes, segs = route_nodes(r)
        if len(nodes) < 2:
            continue
        # 区間の幅: 乗車区間は均等、徒歩区間は短め
        wts = [0.45 if sg["kind"] == "walk" else 1.0 for sg in segs]
        tot = sum(wts)
        xs = [x0]
        for wv in wts:
            xs.append(xs[-1] + (x1 - x0) * wv / tot)
        ly = y + ch * 0.58                        # 線の高さ
        bh = 20
        # 区間
        for k, sg in enumerate(segs):
            a, b = xs[k], xs[k + 1]
            if sg["kind"] == "walk":
                for xx in range(int(a + 14), int(b - 10), 14):
                    d.ellipse((xx - 3, ly - 3, xx + 3, ly + 3), fill=INK2 + (255,))
                draw_icon(d, "walk", (a + b) / 2, ly - 38, 34, INK2 + (255,))
                wm = r.get("transfer_walk_min")
                d.text(((a + b) / 2, ly + 34), f"徒歩{wm}分" if wm else "徒歩", font=font(20, "bold"), fill=INK2, anchor="mm")
                continue
            lg = sg["leg"]
            col = line_color(lg)
            mode = lg.get("mode", "rail")
            _mode_bar(d, mode, a, b, ly, bh, col)
            # 路線名(帯の上)。バス・飛行機・新幹線はアイコンつき
            lab = lg["line"] + (f" {lg['train']}" if lg.get("train") else "")
            icon = mode if mode in ("bus", "plane", "shinkansen") else None
            iw = 30 if icon else 0
            f = fit_font(d, lab, (b - a) - 16 - iw, 24, "black", min_size=14)
            tw = text_w(d, lab, f) + iw
            cx = (a + b) / 2
            cy = ly - 36 if mode != "plane" else ly - 38
            rrect(d, (cx - tw / 2 - 10, cy - 16, cx + tw / 2 + 10, cy + 16), 10, fill=_mix(col, CARD, 0.75) + (255,), outline=col + (255,), width=2)
            if icon:
                draw_icon(d, icon, cx - tw / 2 + 13, cy, 26, INK + (255,))
            d.text((cx + iw / 2, cy), lab, font=f, fill=INK, anchor="mm")
        # ノード
        prev_right = -1e9
        for k, nd in enumerate(nodes):
            x = xs[k]
            kind = nd["kind"]
            if kind == "through":
                d.ellipse((x - 9, ly - 9, x + 9, ly + 9), fill=INK + (255,), outline=(40, 46, 70, 255), width=3)
                nf, ncol = font(20, "bold"), MUTED
            elif kind in ("transfer", "walk_from", "walk_to"):
                d.ellipse((x - 19, ly - 19, x + 19, ly + 19), fill=INK + (255,), outline=DARK + (255,), width=4)
                d.ellipse((x - 9, ly - 9, x + 9, ly + 9), outline=DARK + (255,), width=4)
                nf, ncol = font(26, "black"), GOLD
            else:
                d.ellipse((x - 22, ly - 22, x + 22, ly + 22), fill=GOLD + (255,), outline=INK + (255,), width=5)
                nf, ncol = font(30, "black"), INK
            name = nd["name"]
            ny = ly + 50 if kind != "through" else ly + 36
            if kind in ("walk_from", "walk_to"):
                ny = ly + 64
            tw = text_w(d, name, nf)
            nx = min(max(x, PAD + 20 + tw / 2), W - PAD - 20 - tw / 2)
            # 隣の駅名と重なるときは、終点は字を小さく、途中の駅は1段下げる
            if k > 0 and nx - tw / 2 < prev_right + 12:
                if k == len(nodes) - 1:
                    avail = (W - PAD - 20) - (prev_right + 12)
                    while tw > avail and nf.size > 18:
                        nf = font(nf.size - 2, "black")
                        tw = text_w(d, name, nf)
                    nx = max(prev_right + 12 + tw / 2, min(nx, W - PAD - 20 - tw / 2))
                    if nx - tw / 2 < prev_right + 12:
                        ny += 32
                else:
                    ny += 32
            prev_right = nx + tw / 2 if ny < ly + 70 else prev_right
            d.text((nx, ny), name, font=nf, fill=ncol, anchor="mm")
            if kind == "through":
                d.text((nx, ny + 26), "直通", font=font(18, "bold"), fill=MUTED, anchor="mm")
    # 下の一言
    ex = extremes(routes, lambda r: r.get("transfers"))
    if ex:
        callout(img, 1030, 1200, f"乗換が少ないのは {names(ex[0])}", config.get("route_callout_sub") or "荷物が多い日や子連れなら乗換の少なさも大事")
    else:
        callout(img, 1030, 1200, "乗換の回数はどのルートも同じ", config.get("route_callout_sub"))
    return img


def _bars(img, routes, getter, fmt, sub_of, y0, row_h, best_label, diff_fmt, fmt_r=None):
    d = ImageDraw.Draw(img, "RGBA")
    vals = [getter(r) for r in routes]
    good = [v for v in vals if v is not None]
    vmax = max(good) if good else 1
    vmin = min(good) if good else 0
    for i, r in enumerate(routes):
        v = getter(r)
        y = y0 + i * row_h
        route_tag(d, PAD, y + 22, r, size=34, max_w=W - 2 * PAD - 240)
        if v is not None and v == vmin and vmax != vmin:
            badge(d, W - PAD, y + 22, best_label, size=26)
        elif v is not None and v != vmin:
            badge(d, W - PAD, y + 22, diff_fmt(v - vmin), fill=(70, 40, 50), ink=(255, 150, 150), size=26)
        by = y + 50
        bh = 54
        rrect(d, (PAD, by, W - PAD, by + bh), 12, fill=(255, 255, 255, 14))
        if v is None:
            d.text((PAD + 20, by + bh / 2), "データなし", font=font(28, "bold"), fill=MUTED, anchor="lm")
            continue
        frac = max(0.12, v / vmax)
        bx1 = PAD + (W - 2 * PAD) * frac
        rrect(d, (PAD, by, bx1, by + bh), 12, fill=tuple(r["color"]) + (255,))
        txt = fmt_r(r) if fmt_r else fmt(v)
        f = font(46, "black")
        if bx1 - PAD > text_w(d, txt, f) + 40:
            d.text((bx1 - 18, by + bh / 2), txt, font=f, fill=INK, anchor="rm", stroke_width=3, stroke_fill=(0, 0, 0, 130))
        else:
            d.text((bx1 + 14, by + bh / 2), txt, font=f, fill=INK, anchor="lm")
        sub = sub_of(r)
        if sub:
            d.text((PAD, by + bh + 26), sub, font=fit_font(d, sub, W - 2 * PAD, 24, "regular"), fill=INK2, anchor="lm")


def _equiv_panel(img, y0, y1, head, head_sub, eqs, value_key, tips, unit_fmt):
    d = ImageDraw.Draw(img, "RGBA")
    rrect(d, (PAD, y0, W - PAD, y1), 28, fill=(255, 210, 40, 22), outline=GOLD + (210,), width=3)
    d.text((W / 2, y0 + 46), head, font=fit_font(d, head, W - 2 * PAD - 60, 48), fill=INK, anchor="mm")
    if head_sub:
        d.text((W / 2, y0 + 90), head_sub, font=font(26, "bold"), fill=GOLD, anchor="mm")
    ty = y0 + 120
    eqs = eqs[:3]
    if eqs:
        n = len(eqs)
        gap = 16
        tw = (W - 2 * PAD - 40 - gap * (n - 1)) / n
        th = 176
        for k, (it, big, small) in enumerate(eqs):
            x = PAD + 20 + k * (tw + gap)
            rrect(d, (x, ty, x + tw, ty + th), 20, fill=CARD + (255,), outline=CARD_LINE + (255,), width=2)
            draw_icon(d, it.get("icon", "yen"), x + tw / 2, ty + 42, 56, GOLD + (255,))
            lab = it["label"]
            d.text((x + tw / 2, ty + 90), lab, font=fit_font(d, lab, tw - 24, 24, "bold"), fill=INK2, anchor="mm")
            d.text((x + tw / 2, ty + 130), big, font=fit_font(d, big, tw - 20, 40), fill=INK, anchor="mm")
            note = small or unit_fmt(it)
            d.text((x + tw / 2, ty + 160), note, font=fit_font(d, note, tw - 20, 19, "regular"), fill=MUTED, anchor="mm")
        ty += th + 14
    for tip in tips:
        if ty > y1 - 46:
            break
        f = font(24, "bold")
        lines = wrap_jp(d, tip, f, W - 2 * PAD - 110)[:2]
        draw_icon(d, "bulb", PAD + 48, ty + 20, 34, GOLD + (255,))
        for k, ln in enumerate(lines):
            d.text((PAD + 80, ty + 20 + k * 34), ln, font=f, fill=INK, anchor="lm")
        ty += 34 * len(lines) + 16


def _row_h(n):
    return {1: 170, 2: 170, 3: 160}.get(n, 150)


def slide_money(config, routes):
    img = new_canvas()
    header(img, "お金", config.get("fare_label", "IC運賃(大人・片道)"), num=2)
    n = len(routes)
    rh = _row_h(n)
    _bars(img, routes, fare_of, yen,
          lambda r: (r.get("fare_note") or (f"きっぷ {yen(r['fare']['ticket'])}" if r["fare"].get("ticket") not in (None, r["fare"].get("ic")) else "")),
          330, rh, "最安", lambda dv: f"+{yen(dv)}", fmt_r=fare_txt)
    ex = extremes(routes, fare_of)
    y0 = 330 + n * rh + 10
    if ex:
        best, worst, diff = ex
        m = basis_mult(config)
        dd = diff * m
        eqs = equivalents(dd, config.get("money_items") or DEFAULT_MONEY_ITEMS, "price", "往復" if m == 2 else "回")
        tips = list(config.get("money_tips") or [])
        pre, suf, note = saving_wording(best, worst)
        _equiv_panel(img, y0, H - 120, f"{names(best)}なら {basis_label(config)} {pre}{yen(dd)}{suf} 節約",
                     f"{names(worst)}と{note or ''}くらべて" + (f"(片道 {yen(diff)} × 2)" if m == 2 else ""),
                     eqs, "price", tips,
                     lambda it: f"1{it.get('unit', '回')} {yen(it['price'])}" + (f"({it['price_note']})" if it.get("price_note") else ""))
    else:
        _equiv_panel(img, y0, y0 + 160, "運賃はどのルートも同じ", None, [], "price", [], lambda it: "")
    return img


def time_of(r):
    return r.get("time_min")


def slide_time(config, routes):
    img = new_canvas()
    header(img, "時間", config.get("time_label", "所要時間(乗換・徒歩こみ)"), num=3)
    n = len(routes)
    rh = _row_h(n)

    def sub(r):
        bits = [f"乗換{r['transfers']}回" if r.get("transfers") else "乗換なし"]
        if r.get("_transfer_names"):
            bits[-1] += f"({'・'.join(r['_transfer_names'])})"
        if r.get("transfer_walk_min"):
            bits.append(f"徒歩{r['transfer_walk_min']}分")
        if r.get("time_note"):
            bits.append(r["time_note"])
        return " / ".join(bits)
    _bars(img, routes, time_of, fmt_min, sub, 330, rh, "最速", lambda dv: f"+{fmt_min(dv)}")
    ex = extremes(routes, time_of)
    y0 = 330 + n * rh + 10
    if ex:
        best, worst, diff = ex
        m = basis_mult(config)
        dd = diff * m
        eqs = equivalents(dd, config.get("time_items") or DEFAULT_TIME_ITEMS, "min", "往復" if m == 2 else "回")
        tips = []
        wage = config.get("hourly_wage")
        if wage:
            tips.append(f"時給{yen(wage)}{config.get('wage_label', '')}で換算すると、{fmt_min(dd)}は 約{yen(dd * wage / 60)} ぶん")
        tips += list(config.get("time_tips") or [])
        _equiv_panel(img, y0, H - 120, f"{names(best)}なら {basis_label(config)} {fmt_min(dd)} 節約",
                     f"{names(worst)}とくらべて" + (f"(片道 {fmt_min(diff)} × 2)" if m == 2 else ""),
                     eqs, "min", tips, lambda it: f"1{it.get('unit', '回')} 約{it['min']}分" + (f"({it['min_note']})" if it.get("min_note") else ""))
    else:
        _equiv_panel(img, y0, y0 + 160, "所要時間はどのルートも同じ", None, [], "min", [], lambda it: "")
    return img


def slide_summary(config, routes):
    img = new_canvas()
    header(img, "まとめ", config.get("summary_label", "黄色の枠 = その項目でいちばん"))
    d = ImageDraw.Draw(img, "RGBA")
    n = len(routes)
    label_w = 200
    x0 = PAD + label_w
    col_w = (W - PAD - x0) / n
    top, head_h = 300, 92
    scheds = [route_schedule(config, r) for r in routes]
    with_sched = any(scheds)
    row_h = 92 if with_sched else 104
    for j, r in enumerate(routes):
        cx = x0 + col_w * j
        rrect(d, (cx + 6, top, cx + col_w - 6, top + head_h), 18, fill=tuple(r["color"]) + (255,))
        f = fit_font(d, r["short_name"], col_w - 30, 32)
        d.text((cx + col_w / 2, top + head_h / 2), r["short_name"], font=f, fill=INK, anchor="mm", stroke_width=2, stroke_fill=(0, 0, 0, 100))
    rows = [("運賃", fare_of, yen), ("所要時間", time_of, fmt_min), ("乗換", lambda r: r.get("transfers"), lambda v: f"{v}回" if v else "なし")]
    if with_sched:
        # 出発はおそいほど(ゆっくり出られるほど)いちばん。-分 で比べる
        def dep_get(r):
            sc = route_schedule(config, r)
            return -sc["dep"] if sc and "dep" in sc else None

        def dep_fmt(v):
            m = -v
            return ("前日" if m < 0 else "") + fmt_hm(m) + "発"
        lab = f"{fmt_hm(parse_hm(config['arrive_by']))}着" if config.get("arrive_by") else "出発"
        rows.append((lab, dep_get, dep_fmt))
    for i, (lab, get, fmt) in enumerate(rows):
        y = top + head_h + 10 + i * row_h
        d.line([(PAD, y + row_h), (W - PAD, y + row_h)], fill=CARD_LINE + (255,), width=2)
        d.text((PAD, y + row_h / 2), lab, font=fit_font(d, lab, label_w - 16, 38), fill=INK, anchor="lm")
        vals = [get(r) for r in routes]
        good = [v for v in vals if v is not None]
        best = min(good) if good else None
        tie = good and all(v == best for v in good)
        for j, r in enumerate(routes):
            v = get(r)
            cx = x0 + col_w * j + col_w / 2
            won = v is not None and v == best and not tie
            if won:
                rrect(d, (cx - col_w / 2 + 10, y + 10, cx + col_w / 2 - 10, y + row_h - 10), 16, fill=(255, 210, 40, 34), outline=GOLD + (255,), width=3)
            if v is not None and lab == "運賃":
                txt = fare_txt(r)
            elif v is not None and (lab.endswith("着") or lab == "出発"):
                txt = route_schedule(config, r)["dep_txt"].replace("ごろ", "") + "発"
            elif v is not None:
                txt = fmt(v)
            elif lab.endswith("着") or lab == "出発":
                sc = route_schedule(config, r)
                txt = sc.get("short", "-") if sc else "-"
            else:
                txt = "-"
            d.text((cx, y + row_h / 2), txt, font=fit_font(d, txt, col_w - 36, 36), fill=INK if won else INK2, anchor="mm")

    # 年◯回行くなら
    y = top + head_h + 10 + len(rows) * row_h + 30
    trips = int(config.get("trips_per_year") or 0)
    fx = extremes(routes, fare_of)
    tx = extremes(routes, time_of)
    if trips and (fx or tx):
        rrect(d, (PAD, y, W - PAD, y + 230), 28, fill=CARD + (255,), outline=CARD_LINE + (255,), width=2)
        d.text((PAD + 36, y + 42), f"年{trips}回 往復するなら", font=font(32, "black"), fill=GOLD, anchor="lm")
        half = (W - 2 * PAD) / 2
        for k, (ex, icon, fmtv, who) in enumerate(((fx, "yen", yen, "お金"), (tx, "clock", fmt_min, "時間"))):
            cx = PAD + half * k + half / 2
            if not ex:
                d.text((cx, y + 140), f"{who}の差なし", font=font(30, "bold"), fill=MUTED, anchor="mm")
                continue
            best, _worst, diff = ex
            draw_icon(d, icon, cx - half / 2 + 70, y + 140, 70, GOLD + (255,))
            val = fmtv(diff * 2 * trips)
            sub = f"{names(best)}で節約"
            if who == "お金":
                pre, suf, _note = saving_wording(best, _worst)
                val = f"{pre}{val}{suf}"
                if _note:
                    sub += "(最安値どうし)"
            d.text((cx + 30, y + 118), val, font=fit_font(d, val, half - 150, 54), fill=INK, anchor="mm")
            d.text((cx + 30, y + 176), sub, font=fit_font(d, sub, half - 150, 24, "bold"), fill=INK2, anchor="mm")
        y += 260
    verdict = config.get("verdict") or auto_verdict(routes)
    if isinstance(verdict, str):
        verdict = [verdict]
    vy1 = H - 130
    if y < vy1 - 80:
        rrect(d, (PAD, y, W - PAD, vy1), 28, fill=(255, 210, 40, 26), outline=GOLD + (200,), width=3)
        lines = verdict[:3]
        lh = (vy1 - y) / (len(lines) + 1)
        for k, line in enumerate(lines):
            f = fit_font(d, line, W - 2 * PAD - 60, 42 if k == 0 else 32, "black" if k == 0 else "bold")
            d.text((W / 2, y + lh * (k + 1)), line, font=f, fill=INK if k == 0 else GOLD, anchor="mm")
    return img


def auto_verdict(routes):
    fx = extremes(routes, fare_of)
    tx = extremes(routes, time_of)
    out = []
    if fx and tx and set(r["key"] for r in fx[0]) & set(r["key"] for r in tx[0]):
        both = [r for r in fx[0] if r["key"] in {x["key"] for x in tx[0]}]
        out.append(f"安くて速い {names(both)} が最強")
    else:
        if fx:
            out.append(f"安さなら {names(fx[0])}")
        if tx:
            out.append(f"速さなら {names(tx[0])}")
    trs = extremes(routes, lambda r: r.get("transfers"))
    if trs:
        out.append(f"乗換の少なさなら {names(trs[0])}")
    return out or ["どのルートも大差なし"]


# ================================================================ キャプション

def build_caption(config, routes):
    o = (config.get("origin") or {}).get("name") if isinstance(config.get("origin"), dict) else config.get("origin")
    dn = (config.get("destination") or {}).get("name") if isinstance(config.get("destination"), dict) else config.get("destination")
    o = o or routes[0]["legs"][0]["from"]
    dn = dn or routes[0]["legs"][-1]["to"]
    lines = [f"{o}から{dn}へおでかけするなら、どのルートがおトク？",
             f"{len(routes)}つのルートを「お金」と「時間」でくらべて、", "節約できるぶんを身近なモノに置き換えてみました。", ""]
    lines += [f"・{r['name']}" for r in routes]
    if config.get("arrive_by") or any(r.get("depart_time") for r in routes):
        head = f"{dest_display(config, routes)}に{fmt_hm(parse_hm(config['arrive_by']))}着なら" if config.get("arrive_by") else "出発時刻"
        lines += ["", f"【{head}】"] + [f"・{r['short_name']}: {schedule_line(route_schedule(config, r)) or '-'}" for r in routes]
    lines += ["", "結果は画像をスワイプしてご覧ください。", "いつもどのルートで行くか、コメントで教えてください。", ""]
    note = config.get("as_of", "")
    lines += [f"※{note} {config.get('caption_note', '運賃はIC・大人。時間は検索結果の一例で、時間帯により変わります')}".strip()]
    tags = config.get("hashtags") or ["#おでかけ", "#節約", "#乗り換え", "#鉄道", "#地図", "#Geogram_JP"]
    lines += ["", " ".join(tags)]
    return "\n".join(lines) + "\n"


# ================================================================ 既定の置き換えアイテム(目安。config で上書き推奨)

DEFAULT_MONEY_ITEMS = [
    {"label": "ペットボトル飲料", "price": 200, "unit": "本", "icon": "bottle"},
    {"label": "軽食", "price": 500, "unit": "回", "icon": "snack"},
]
DEFAULT_TIME_ITEMS = [
    {"label": "トイレの行列", "min": 5, "unit": "回", "icon": "toilet"},
    {"label": "カフェで一息", "min": 30, "unit": "回", "icon": "cafe"},
]


# ================================================================ エントリーポイント

def render_savings(config, geojson_path, station_db, out_dir="output", fast_preview=False, out_name=None):
    slug = out_name or config.get("slug", "savings")
    out = Path(out_dir) / slug
    out.mkdir(parents=True, exist_ok=True)
    routes = resolve_routes(config)
    for r in routes:
        print(f"  {r['name']}: 運賃 {yen(fare_of(r)) if fare_of(r) is not None else '-'} / "
              f"所要時間 {fmt_min(r['time_min']) if r.get('time_min') is not None else '-'} / 乗換 {r.get('transfers')}回")
    paths = []      # 表紙に路線図は描かない(発着の2点だけ)。線を描きたくなったら _route_paths() を使う

    makers = {
        "cover": lambda: slide_cover(config, routes, paths, geojson_path, station_db),
        "route": lambda: slide_route(config, routes),
        "money": lambda: slide_money(config, routes),
        "time": lambda: slide_time(config, routes),
        "summary": lambda: slide_summary(config, routes),
    }
    order = config.get("slides") or ["cover", "route", "money", "time", "summary"]
    total = len(order)
    for page, name in enumerate(order, 1):
        img = makers[name]()
        if name != "cover":
            chrome(img, config, page, total, config.get("kicker", config.get("title_line1", "")))
        else:
            ImageDraw.Draw(img).text((W - COVER_SX, COVER_TOP + 10), f"{page}/{total}", font=font(22, "bold"), fill=MUTED, anchor="rm")
        path = out / f"{page:02d}_{name}.png"
        img.convert("RGB").save(path, optimize=True)
        print("  wrote", path)
    cap = out / "caption.txt"
    cap.write_text(build_caption(config, routes), encoding="utf-8")
    print("  wrote", cap)
    return str(out)
