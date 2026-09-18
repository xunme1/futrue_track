# -*- coding: utf-8 -*-
"""席位追踪第 2 步：绘制「席位持仓·每日方向图」PNG → data/seat/seat_direction_<date>.png。

    python -m backend.pipeline.seat_plot --date 20260917 [--prev 20260916]

字体按平台自动探测（macOS Hiragino / Linux Noto CJK / 文泉驿），均无则报错提示安装。
"""
from __future__ import annotations

import argparse
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.patches import FancyArrowPatch, FancyBboxPatch, Rectangle

from backend.pipeline.seat_core import (
    CN_NAME,
    GROUPS,
    MEMBER_FAMILIES,
    SEAT_DIR,
    build_rows,
    complete_symbols,
    load_group_daily,
    prev_trade_date,
    read_seat_csv,
)

TOP_N = 10  # 每个分区显示行数（按当日 |净持仓| 降序）

# 颜色
BG = "#0e1e33"          # 深蓝近黑背景
PANEL = "#142a46"       # 分区卡片底色
PANEL_EDGE = "#27425f"
GOLD = "#d9b98a"        # 金色标题
TEXT = "#e8e2d4"        # 米白正文
SUB = "#8fa3bd"         # 次要文字
GREEN = "#1f8f6e"       # 偏多
RED = "#b03a3a"         # 偏空
TRACK = "#31465f"       # 哑铃轨道
ZERO = "#d9b98a"        # 零轴

FONT_CANDIDATES = [
    "/System/Library/Fonts/Hiragino Sans GB.ttc",                      # macOS
    "/System/Library/Fonts/PingFang.ttc",                              # macOS 较新版本
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",          # Debian/Ubuntu fonts-noto-cjk
    "/usr/share/fonts/noto-cjk/NotoSansCJK-Regular.ttc",               # Arch noto-fonts-cjk
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",                  # 文泉驿
]


def setup_font():
    for path in FONT_CANDIDATES:
        if Path(path).exists():
            font_manager.fontManager.addfont(path)
            name = font_manager.FontProperties(fname=path).get_name()
            plt.rcParams["font.family"] = name
            plt.rcParams["axes.unicode_minus"] = False
            return name
    raise SystemExit("[错误] 未找到中文字体；Linux 请安装 fonts-noto-cjk（sudo apt install fonts-noto-cjk）")


DPI = 100
WIDTH_PX = 1080

# 布局（像素）
PX_HEADER = 250
PX_LEGEND = 84
PX_GROUP_TITLE = 58
PX_BAR = 64
PX_COLHEAD = 30
PX_ROW = 42
PX_GROUP_PAD = 26
PX_FOOTER = 168

# 哑铃图 x 区间（fig 坐标 0-1）
X0, X1 = 0.475, 0.955
XMID = (X0 + X1) / 2
XSPAN = (X1 - X0) / 2 * 0.94


def xmap(x):
    return XMID + x * XSPAN


def render(df, trade_date, prev_date, out_png):
    group_data = []
    for code, gname, kws in GROUPS:
        g = load_group_daily(df, kws)
        rows = build_rows(g, trade_date, prev_date)
        n_before = len(rows)
        # 完整口径过滤：仅保留组内全部会员公司当日均在榜的品种
        full_today = complete_symbols(df, MEMBER_FAMILIES[code], trade_date)
        full_prev = complete_symbols(df, MEMBER_FAMILIES[code], prev_date)
        rows = [r for r in rows if r["symbol"] in full_today]
        for r in rows:
            r["prev_complete"] = r["symbol"] in full_prev
        n_long = sum(1 for r in rows if r["net_t"] > 0)
        group_data.append({
            "code": code, "name": gname, "rows": rows,
            "shown": rows[:TOP_N], "n_long": n_long,
            "n_short": len(rows) - n_long,
        })
        n_prev_full = sum(1 for r in rows if r["prev_complete"])
        print(f"{gname}: 完整口径过滤 {n_before} → {len(rows)} 个品种"
              f"（偏多 {n_long} / 偏空 {len(rows) - n_long}，前日也全员在榜 {n_prev_full} 个），"
              f"显示前 {min(TOP_N, len(rows))} 行")

    # ---- 画布 ----
    height_px = PX_HEADER + PX_LEGEND + PX_FOOTER
    for gd in group_data:
        height_px += PX_GROUP_TITLE + PX_BAR + PX_COLHEAD + PX_ROW * len(gd["shown"]) + PX_GROUP_PAD
    fig = plt.figure(figsize=(WIDTH_PX / DPI, height_px / DPI), dpi=DPI)
    fig.patch.set_facecolor(BG)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis("off")
    ax.add_patch(Rectangle((0, 0), 1, 1, facecolor=BG, edgecolor="none", zorder=-10))

    def py(px_from_top):
        return 1 - px_from_top / height_px

    cursor = 0.0  # 距顶部像素

    # ---- 顶部标题区 ----
    y = py(cursor + 46)
    ax.text(0.045, y, "BROKER POSITION · DAILY DIRECTION MAP",
            color=GOLD, fontsize=9.5, fontweight="bold", va="center")
    ax.text(0.045, y - 42 / height_px, "席位持仓 · 每日方向图",
            color=TEXT, fontsize=27, fontweight="bold", va="center")
    ax.text(0.045, y - 84 / height_px,
            "机构席位净持仓当日迁移 · 先读已披露存量，再看当日方向",
            color=SUB, fontsize=10.5, va="center")
    d = trade_date
    ax.text(0.955, y, f"{d[:4]}.{d[4:6]}.{d[6:]}",
            color=TEXT, fontsize=16, fontweight="bold", ha="right", va="center")
    ax.text(0.955, y - 26 / height_px, f"数据日期 {d} · 对比 {prev_date}",
            color=SUB, fontsize=9, ha="right", va="center")
    ax.plot([0.045, 0.955], [py(PX_HEADER - 14)] * 2, color=GOLD, lw=1.2, alpha=0.6)
    cursor = PX_HEADER

    # ---- 快速读图 ----
    y = py(cursor + 19)
    ax.text(0.045, y, "快速读图", color=GOLD, fontsize=9.5, fontweight="bold", va="center")
    lx = 0.115
    ax.plot(lx, y, "o", mfc="none", mec=SUB, ms=6, mew=1.2)
    ax.text(lx + 0.012, y, "前日", color=SUB, fontsize=9, va="center")
    ax.plot(lx + 0.052, y, "o", mfc=SUB, mec=SUB, ms=6)
    ax.text(lx + 0.064, y, "今日", color=SUB, fontsize=9, va="center")
    ax.add_patch(FancyArrowPatch((lx + 0.106, y), (lx + 0.128, y), arrowstyle="-|>",
                                 mutation_scale=11, lw=1.6, color=SUB))
    ax.text(lx + 0.134, y, "迁移方向", color=SUB, fontsize=9, va="center")
    ax.add_patch(Rectangle((lx + 0.196, y - 6 / height_px), 0.014, 12 / height_px,
                           facecolor=GREEN, edgecolor="none"))
    ax.text(lx + 0.215, y, "偏多", color=SUB, fontsize=9, va="center")
    ax.add_patch(Rectangle((lx + 0.251, y - 6 / height_px), 0.014, 12 / height_px,
                           facecolor=RED, edgecolor="none"))
    ax.text(lx + 0.270, y, "偏空", color=SUB, fontsize=9, va="center")
    ax.text(0.955, y, "轴 = 近20日相对定位 · 左净空 / 右净多",
            color=SUB, fontsize=9, ha="right", va="center")
    ax.text(0.115, py(cursor + 46),
            "动作分类：偏多 / 偏空 = 当日净持仓 >0 / <0　｜　加多 / 加空 = 方向不变且 |净持仓| 扩大　｜　"
            "减多 / 减空 = 方向不变且 |净持仓| 缩小",
            color=SUB, fontsize=8.5, va="center", alpha=0.9)
    ax.text(0.115, py(cursor + 66),
            "翻多 / 翻空 = 净持仓由负转正 / 由正转负（跨零轴迁移，图中以加粗箭头标示）　｜　箭头方向 = 前日 → 今日",
            color=SUB, fontsize=8.5, va="center", alpha=0.9)
    cursor += PX_LEGEND

    # ---- 分区 ----
    for gd in group_data:
        top = cursor
        gh = PX_GROUP_TITLE + PX_BAR + PX_COLHEAD + PX_ROW * len(gd["shown"]) + PX_GROUP_PAD
        ax.add_patch(FancyBboxPatch(
            (0.028, py(top + gh - 6)), 0.944, (gh - 12) / height_px,
            boxstyle="round,pad=0,rounding_size=0.006",
            facecolor=PANEL, edgecolor=PANEL_EDGE, lw=1, zorder=-5))

        y = py(top + PX_GROUP_TITLE / 2 + 4)
        ax.text(0.05, y, f"{gd['code']}  {gd['name']}",
                color=TEXT, fontsize=15, fontweight="bold", va="center")
        ax.text(0.05 + 0.115, y, f"持仓品种 {len(gd['rows'])} · 多 {gd['n_long']} / 空 {gd['n_short']}",
                color=SUB, fontsize=9.5, va="center")
        dom_long = gd["n_long"] >= gd["n_short"]
        badge_txt = "偏多主导" if dom_long else "偏空主导"
        badge_color = GREEN if dom_long else RED
        bx = 0.872
        ax.add_patch(Rectangle((bx, y - 10 / height_px), 0.083, 20 / height_px,
                               facecolor="none", edgecolor=badge_color, lw=1.2))
        ax.text(bx + 0.0415, y, badge_txt, color=badge_color, fontsize=9.5,
                fontweight="bold", ha="center", va="center")
        cursor += PX_GROUP_TITLE

        # 汇总条：该组所有品种今日 net 分布（左空右多，每品种一段）
        y_mid = cursor + PX_BAR / 2 + 2
        n = len(gd["rows"])
        if n:
            seg_w = (X1 - X0) / n
            ordered = sorted(gd["rows"], key=lambda r: r["net_t"])
            for i, r in enumerate(ordered):
                c = RED if r["net_t"] < 0 else GREEN
                ax.add_patch(Rectangle((X0 + i * seg_w + 0.0008, py(y_mid + 7)),
                                       seg_w - 0.0016, 14 / height_px,
                                       facecolor=c, edgecolor="none", alpha=0.9))
            ax.plot([XMID, XMID], [py(y_mid + 10), py(y_mid - 10)], color=ZERO, lw=1, alpha=0.7)
            ax.text(X0, py(y_mid - 16), f"空 {gd['n_short']}", color=RED, fontsize=8.5, va="center")
            ax.text(X1, py(y_mid - 16), f"多 {gd['n_long']}", color=GREEN,
                    fontsize=8.5, va="center", ha="right")
            ax.text(XMID, py(y_mid - 16), "零轴", color=ZERO, fontsize=8,
                    va="center", ha="center", alpha=0.8)
        ax.text(0.05, py(y_mid), "品种分布", color=SUB, fontsize=9, va="center")
        cursor += PX_BAR

        # 表头
        y = py(cursor + PX_COLHEAD / 2)
        ax.text(0.05, y, "品种", color=SUB, fontsize=8.5, va="center")
        ax.text(0.245, y, "方向 / 当日动作", color=SUB, fontsize=8.5, va="center")
        ax.text(X0, y, "净空", color=SUB, fontsize=8.5, va="center")
        ax.text(XMID, y, "零轴", color=ZERO, fontsize=8.5, va="center", ha="center", alpha=0.8)
        ax.text(X1, y, "净多", color=SUB, fontsize=8.5, va="center", ha="right")
        cursor += PX_COLHEAD

        # 行
        for idx, r in enumerate(gd["shown"]):
            y_mid_r = cursor + PX_ROW / 2
            yc = py(y_mid_r)
            if idx % 2 == 0:
                ax.add_patch(Rectangle((0.038, py(y_mid_r + PX_ROW / 2 - 3)),
                                       0.924, (PX_ROW - 6) / height_px,
                                       facecolor="#ffffff", alpha=0.025, edgecolor="none"))
            cn = CN_NAME.get(r["symbol"], r["symbol"])
            ax.text(0.05, yc + 7 / height_px, cn, color=TEXT, fontsize=11.5,
                    fontweight="bold", va="center")
            ax.text(0.05, yc - 9 / height_px, r["symbol"], color=SUB, fontsize=8, va="center")
            net_yi = r["net_t"] / 10000
            ax.text(0.135, yc + 7 / height_px, f"{net_yi:+.1f}万手",
                    color=SUB, fontsize=8.5, va="center")
            chg = (r["net_t"] - r["net_p"]) / 10000
            ax.text(0.135, yc - 9 / height_px, f"日变动 {chg:+.1f}",
                    color=SUB, fontsize=7.5, va="center", alpha=0.8)
            color = GREEN if r["direction"] == "偏多" else RED
            ax.add_patch(Rectangle((0.212, yc - 10 / height_px), 0.052, 20 / height_px,
                                   facecolor=color, edgecolor="none"))
            ax.text(0.238, yc, r["direction"], color="#ffffff", fontsize=10,
                    fontweight="bold", ha="center", va="center")
            ax.text(0.278, yc, r["action"], color=color, fontsize=10.5,
                    fontweight="bold", va="center")
            ax.plot([X0, X1], [yc, yc], color=TRACK, lw=2.4, alpha=0.8, zorder=1,
                    solid_capstyle="round")
            ax.plot([XMID, XMID], [yc - 9 / height_px, yc + 9 / height_px],
                    color=ZERO, lw=1, alpha=0.65, zorder=2)
            xp, xt = xmap(r["x_p"]), xmap(r["x_t"])
            if r.get("prev_complete", True):
                flipped = r["action"] in ("翻多", "翻空")
                if abs(xt - xp) > 0.004:
                    lw = 3.6 if flipped else 2.4
                    ms = 19 if flipped else 13
                    ax.add_patch(FancyArrowPatch(
                        (xp, yc), (xt, yc), arrowstyle="-|>", mutation_scale=ms,
                        lw=lw, color=color, alpha=1.0 if flipped else 0.9,
                        shrinkA=4, shrinkB=7, capstyle="round", zorder=3))
                else:
                    ax.plot([xp, xt], [yc, yc], color=color, lw=2.4, alpha=0.9,
                            zorder=3, solid_capstyle="round")
                ax.plot(xp, yc, "o", mfc=PANEL, mec=color, ms=7.5, mew=1.6, zorder=4)
            # 前日非全员在榜：合计有截断偏差，不画箭头起点，仅画今日点
            ax.plot(xt, yc, "o", mfc=color, mec="#e8e2d4", ms=8, mew=0.8, zorder=5)
            cursor += PX_ROW
        cursor += PX_GROUP_PAD

    # ---- 底部 ----
    n_symbol = df["symbol"].nunique()
    ax.plot([0.045, 0.955], [py(cursor + 8)] * 2, color=GOLD, lw=1, alpha=0.5)
    ax.text(0.045, py(cursor + 28),
            "轴 = 近20日相对定位 · ○ = 前日 · ● = 今日 · 箭头 = 当日迁移方向（翻多/翻空加粗）",
            color=SUB, fontsize=8.5, va="center")
    ax.text(0.045, py(cursor + 50),
            f"品种范围：主流商品期货 40 个品种（接口无数据品种自动剔除，本期实际 {n_symbol} 个）",
            color=SUB, fontsize=8.5, va="center", alpha=0.9)
    ax.text(0.045, py(cursor + 72),
            "持仓口径：品种全合约合计（交易所会员持仓前 20 名已披露口径，非主力合约）",
            color=SUB, fontsize=8.5, va="center", alpha=0.9)
    ax.text(0.045, py(cursor + 94),
            "展示规则：仅列组内全部会员当日均在榜（前20名披露）的品种，合计为完整口径；同一公司的名称变体合并计为一家",
            color=SUB, fontsize=8.5, va="center", alpha=0.9)
    ax.text(0.045, py(cursor + 120),
            "高盛 = 高盛期货 · 主力机构 = 国泰君安 / 中信期货 / 永安期货 · 散户 = 东方财富 / 徽商期货",
            color=SUB, fontsize=8.5, va="center", alpha=0.85)
    ax.text(0.045, py(cursor + 146),
            "仅供商品期货市场研究与教育用途，不构成投资建议。历史变化与模型结果不代表未来表现。",
            color=SUB, fontsize=8, va="center", alpha=0.65)

    fig.savefig(out_png, dpi=DPI, facecolor=BG)
    plt.close(fig)
    print(f"\n已生成 {out_png} ({WIDTH_PX}x{height_px}px)")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--date", required=True, help="交易日 YYYYMMDD")
    ap.add_argument("--prev", help="对比日 YYYYMMDD（默认从 CSV 缓存推导前一交易日）")
    args = ap.parse_args(argv)
    setup_font()
    csv = SEAT_DIR / f"seat_data_{args.date}.csv"
    if not csv.exists():
        raise SystemExit(f"[错误] 缓存不存在: {csv}，先运行 python -m backend.pipeline.seat_fetch")
    df = read_seat_csv(csv)
    prev = args.prev or prev_trade_date(df, args.date)
    if not prev:
        raise SystemExit(f"[错误] {args.date} 是缓存首个交易日，无前一交易日可对比")
    render(df, args.date, prev, SEAT_DIR / f"seat_direction_{args.date}.png")


if __name__ == "__main__":
    main()
